"""Tests for gpuwatchdog.py: attribution, the staged escalation, its safety rules.

    python3 tools/test_gpuwatchdog.py

Nothing here touches the machine it runs on. The watchdog sees a /proc built
under a temp dir, fds whose device numbers are made up and a clock that only
counts; os.kill and subprocess.run are recorders; every PID is above the
kernel's PID_MAX_LIMIT. So no test can signal a process, raise a desktop
notification or write to /var/log/gpuwatchdog.
"""

import contextlib
import errno
import io
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gpuwatchdog  # noqa: E402

SIGTERM, SIGKILL = signal.SIGTERM, signal.SIGKILL
GIB = 1024 * 1024  # in KiB, the unit of /proc/meminfo

# Above PID_MAX_LIMIT (4194304): a signal that escaped the fakes would find no
# such process.
BROWSER, COMPOSITOR, OTHER = 5000001, 5000002, 5000003

# What os.stat() answers for an fd, by what the fd is open on. An inotify fd
# is an anonymous inode, which has no file type at all.
RENDER_NODE = types.SimpleNamespace(st_mode=stat.S_IFCHR | 0o666,
                                    st_rdev=os.makedev(226, 128))
DEV_NULL = types.SimpleNamespace(st_mode=stat.S_IFCHR | 0o666,
                                 st_rdev=os.makedev(1, 3))
ANON_INODE = types.SimpleNamespace(st_mode=0o600, st_rdev=0)

_listdir = os.listdir
_stat = os.stat


def read(path):
    with open(path) as handle:
        return handle.read()


def write(path, text):
    with open(path, "w") as handle:
        handle.write(text)


def drm(client, total, shared="0"):
    """fdinfo of one xe DRM fd, as the kernel prints it."""
    return ("pos:\t0\nflags:\t02100002\nmnt_id:\t28\nino:\t186\n"
            f"drm-driver:\txe\ndrm-client-id:\t{client}\ndrm-pdev:\t0000:00:02.0\n"
            f"drm-total-gtt:\t{total}\ndrm-shared-gtt:\t{shared}\n"
            f"drm-resident-gtt:\t{total}\n")


def unit_argv(*drop_ins):
    """gpuwatchdog.service's arguments as systemd expands them: a bare $VAR
    becomes one argument per word of its value, and none when it is empty."""
    env = {}
    exec_start = ""
    for name in ("gpuwatchdog.service", *drop_ins):
        for line in read(os.path.join(HERE, name)).splitlines():
            key, _, value = line.partition("=")
            if key == "Environment":
                var, _, setting = value.partition("=")
                env[var] = setting
            elif key == "ExecStart":
                exec_start = value
    argv = []
    for word in exec_start.split()[1:]:
        argv += env[word[1:]].split() if word.startswith("$") else [word]
    return argv


class Stop(BaseException):
    """Ends a run of the watchdog's endless loop."""


class Clock:
    """Stands in for the watchdog's `time`: a sleep only advances a counter."""

    def __init__(self):
        # Long after boot. CLOCK_MONOTONIC counts from boot, and that matters
        # to the loop: see test_runaway_just_after_boot.
        self.boot(100000)
        self.stop_at = 0.0
        self.due = []

    def boot(self, uptime):
        self.start = self.now = float(uptime)

    @property
    def elapsed(self):
        return self.now - self.start

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        while self.due and self.due[0][0] <= self.now:
            self.due.pop(0)[1]()
        if self.now > self.stop_at:
            raise Stop

    def strftime(self, fmt):
        return time.strftime(fmt, time.gmtime(self.now))


class WatchdogCase(unittest.TestCase):
    """A watchdog wired to a fake machine: /proc, clock, signals, subprocesses."""

    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        self.capture_dir = os.path.join(self.root, "captures")
        os.mkdir(os.path.join(self.root, "proc"))
        self.clock = Clock()
        self.denied = set()     # fdinfo directories that answer EACCES
        self.nodes = {}         # /proc/<pid>/fd/<n> -> what os.stat() answers
        self.opened = []        # every path the watchdog opened, in order
        self.signals = []       # (pid, signal) in the order sent
        self.sent_at = []       # ...and when, in seconds since the run began
        self.on_term = None     # what the target does on SIGTERM; None ignores it
        self.kill_error = None  # raised by os.kill once the signal is recorded
        self.journal = []       # (seconds since the run began, message)
        self.meminfo(gpu=1.9, avail=6)

        def patch(*args, **kwargs):
            return self.enterContext(mock.patch.object(*args, **kwargs))

        patch(gpuwatchdog, "open", self._open, create=True)
        patch(gpuwatchdog, "time", self.clock)
        patch(gpuwatchdog, "log",
              lambda message: self.journal.append((self.clock.elapsed, message)))
        patch(os, "listdir", self._listdir)
        patch(os, "stat", self._stat)
        patch(os, "kill", self._kill)
        patch(os, "geteuid", return_value=0)
        self.spawned = patch(subprocess, "run",
                             return_value=subprocess.CompletedProcess([], 0, "", ""))
        self.notify = patch(gpuwatchdog, "notify")

    def _path(self, path):
        if isinstance(path, str) and (path + "/").startswith("/proc/"):
            return self.root + path
        return path

    def _open(self, path, *args, **kwargs):
        self.opened.append(path)
        return open(self._path(path), *args, **kwargs)

    def _listdir(self, path="."):
        if path in self.denied:
            raise PermissionError(errno.EACCES, "Permission denied", path)
        return _listdir(self._path(path))

    def _stat(self, path, *args, **kwargs):
        if isinstance(path, str) and path in self.nodes:
            return self.nodes[path]
        return _stat(self._path(path), *args, **kwargs)

    def _kill(self, pid, sig):
        self.signals.append((pid, sig))
        self.sent_at.append(self.clock.elapsed)
        if self.kill_error:
            raise self.kill_error
        if sig == SIGKILL:
            self.exit(pid)
        elif self.on_term:
            self.on_term()

    def meminfo(self, gpu, avail):
        """Set GPUActive and MemAvailable, in GiB. gpu=None is a kernel
        without the accounting patch."""
        text = (f"MemTotal:       16053412 kB\n"
                f"MemAvailable:   {int(avail * GIB)} kB\n"
                f"SwapFree:       12789040 kB\n")
        if gpu is not None:
            text += f"GPUActive:      {int(gpu * GIB)} kB\nGPUReclaim:     0 kB\n"
        write(f"{self.root}/proc/meminfo", text)

    def spawn(self, pid, comm, *fdinfo, start=1):
        """A process with one fd per fdinfo text: open on a render node when
        the text is a DRM client's, on an anonymous inode otherwise. Spawning
        a live PID again is that PID recycled."""
        self.exit(pid)
        proc = f"{self.root}/proc/{pid}"
        os.makedirs(f"{proc}/fdinfo")
        os.makedirs(f"{proc}/task/{pid}")
        # Field 22, the start time, is the 20th after the comm's closing paren.
        stat_line = f"{pid} ({comm}) S {'0 ' * 18}{start} 0 0\n"
        write(f"{proc}/comm", comm + "\n")
        write(f"{proc}/stat", stat_line)
        write(f"{proc}/task/{pid}/stat", stat_line)
        write(f"{proc}/task/{pid}/wchan", "futex_do_wait")
        for fd, text in enumerate(fdinfo):
            write(f"{proc}/fdinfo/{fd}", text)
            self.nodes[f"/proc/{pid}/fd/{fd}"] = (
                RENDER_NODE if "drm-client-id" in text else ANON_INODE)

    def exit(self, pid):
        shutil.rmtree(f"{self.root}/proc/{pid}", ignore_errors=True)
        for path in [path for path in self.nodes if path.startswith(f"/proc/{pid}/")]:
            del self.nodes[path]

    def at(self, seconds, event):
        """Call `event` once the run is `seconds` old, before that reading."""
        self.clock.due.append((self.clock.now + seconds, event))
        self.clock.due.sort(key=lambda due: due[0])

    def watch(self, *args, seconds):
        """Run the loop for `seconds` of fake time. The script's own defaults
        apply unless `args` override them."""
        self.clock.stop_at = self.clock.now + seconds
        argv = ["gpuwatchdog", "--capture-dir", self.capture_dir, *args]
        with mock.patch.object(sys, "argv", argv), self.assertRaises(Stop):
            gpuwatchdog.main()

    def logged(self, prefix):
        return [message for _, message in self.journal if message.startswith(prefix)]

    def logged_at(self, prefix):
        return [when for when, message in self.journal if message.startswith(prefix)]

    def captured(self):
        """The first line of every capture written, oldest first."""
        try:
            names = sorted(_listdir(self.capture_dir))
        except FileNotFoundError:
            return []
        return [read(os.path.join(self.capture_dir, name)).split("\n", 1)[0]
                for name in names]


class Attribution(WatchdogCase):
    def test_meminfo(self):
        self.meminfo(gpu=0.5, avail=6.5)
        mem = gpuwatchdog.read_meminfo()
        self.assertEqual((mem["GPUActive"], mem["MemAvailable"]), (524288, 6815744))

        self.meminfo(gpu=None, avail=6.5)
        self.assertNotIn("GPUActive", gpuwatchdog.read_meminfo())

        os.remove(f"{self.root}/proc/meminfo")
        self.assertEqual(gpuwatchdog.read_meminfo(), {})

    def test_start_time_survives_an_awkward_comm(self):
        for comm in ("Isolated Web Co", "(sd-pam)", "a) S (b"):
            self.spawn(OTHER, comm, start=42514)
            self.assertEqual(gpuwatchdog._start_time(OTHER), "42514", comm)
        self.exit(OTHER)
        self.assertIsNone(gpuwatchdog._start_time(OTHER))

    def test_private_is_total_minus_shared(self):
        self.spawn(BROWSER, "hellfire", drm(28, "165828 KiB", "37648 KiB"))
        self.assertEqual(gpuwatchdog.gpu_clients(), {BROWSER: 128180})

    def test_client_open_on_several_fds_counts_once(self):
        fd = drm(28, "165828 KiB")
        self.spawn(BROWSER, "hellfire", fd, fd, fd)
        self.assertEqual(gpuwatchdog.gpu_clients(), {BROWSER: 165828})

    def test_clients_of_one_process_add_up(self):
        self.spawn(BROWSER, "hellfire", drm(28, "1000 KiB"), drm(29, "24 KiB"),
                   "pos:\t0\nflags:\t02100002\nmnt_id:\t28\nino:\t9\n")
        self.assertEqual(gpuwatchdog.gpu_clients(), {BROWSER: 1024})

    # The kernel prints a whole number of MiB as "672 MiB". Reading every value
    # as KiB misread 8 of this desktop's 21 clients on 12/10/2026, these two
    # among them: shoji_wm's 329 MiB came out as 0.
    def test_sizes_carry_their_own_unit(self):
        self.spawn(COMPOSITOR, "shoji_wm", drm(7, "672 MiB", "351200 KiB"))
        self.spawn(BROWSER, "floorp", drm(28, "307660 KiB", "30 MiB"))
        self.assertEqual(gpuwatchdog.gpu_clients(),
                         {COMPOSITOR: 336928, BROWSER: 276940})

    def test_unreadable_fdinfo_is_skipped_and_reported(self):
        self.spawn(BROWSER, "hellfire", drm(28, "165828 KiB"))
        self.spawn(OTHER, "systemd-resolve", drm(29, "4096 KiB"))
        self.denied.add(f"/proc/{OTHER}/fdinfo")
        # A process that exited mid-scan is not a permission problem.
        os.mkdir(f"{self.root}/proc/{COMPOSITOR}")
        self.assertEqual(gpuwatchdog.gpu_clients(), {BROWSER: 165828})
        self.assertEqual(gpuwatchdog.fdinfo_denied(), [OTHER])

    # fdinfo is not cheap for every fd: an inotify fd prints each watch it
    # holds, and on 13/10/2026 two of them were 30 MB of text and 94% of the
    # walk. Only a DRM node's fdinfo is opened at all.
    def test_only_a_drm_fd_has_its_fdinfo_read(self):
        watches = ("pos:\t0\nflags:\t02004000\nmnt_id:\t16\nino:\t2073\n"
                   "inotify wd:1 ino:c0a sdev:27 mask:fce ignored_mask:0\n")
        self.spawn(BROWSER, "gitkraken", watches, drm(28, "1000 KiB"), "pos:\t0\n")
        self.nodes[f"/proc/{BROWSER}/fd/2"] = DEV_NULL
        self.assertEqual(gpuwatchdog.gpu_clients(), {BROWSER: 1000})
        self.assertEqual(self.opened, [f"/proc/{BROWSER}/fdinfo/1"])

    def test_fd_closed_mid_walk_is_skipped(self):
        self.spawn(BROWSER, "hellfire", drm(28, "1000 KiB"), drm(29, "24 KiB"))
        del self.nodes[f"/proc/{BROWSER}/fd/1"]
        self.assertEqual(gpuwatchdog.gpu_clients(), {BROWSER: 1000})

    def test_only_an_allowlisted_comm_is_a_target(self):
        self.spawn(COMPOSITOR, "shoji_wm", drm(7, "9437188 KiB"))
        self.spawn(BROWSER, "hellfire", drm(28, "307660 KiB"))
        clients = gpuwatchdog.gpu_clients()
        self.assertEqual(gpuwatchdog.pick_target({"hellfire"}, clients),
                         (BROWSER, 307660, "hellfire"))
        self.assertIsNone(gpuwatchdog.pick_target({"floorp"}, clients))

    def test_biggest_allowlisted_holder_wins(self):
        self.spawn(BROWSER, "hellfire", drm(28, "307660 KiB"))
        self.spawn(OTHER, "floorp", drm(29, "7340036 KiB"))
        self.assertEqual(gpuwatchdog.pick_target({"hellfire", "floorp"},
                                                 gpuwatchdog.gpu_clients()),
                         (OTHER, 7340036, "floorp"))


class Escalation(WatchdogCase):
    """The loop, at the script's defaults. The compositor holds more than the
    browser throughout, so every signal asserted here is also a check that the
    biggest holder was passed over for the allowlisted one."""

    def setUp(self):
        super().setUp()
        self.spawn(BROWSER, "hellfire", drm(28, "7340036 KiB"))
        self.spawn(COMPOSITOR, "shoji_wm", drm(7, "9437188 KiB"))

    def quits(self):
        self.exit(BROWSER)
        self.meminfo(gpu=1.9, avail=6)

    def test_healthy_desktop_is_left_alone(self):
        self.watch(seconds=3600)
        self.assertEqual(self.signals, [])
        self.assertEqual(self.captured(), [])
        self.notify.assert_not_called()
        # Silent when healthy must still be distinguishable from dead.
        self.assertTrue(self.logged("alive:"))

    def test_kernel_without_gpuactive_idles(self):
        self.meminfo(gpu=None, avail=1)
        self.watch(seconds=600)
        self.assertEqual(self.signals, [])

    def test_plateau_warns_once_after_the_hold(self):
        self.meminfo(gpu=5, avail=6)
        self.watch(seconds=600)
        warned = self.logged_at("WARN ")
        self.assertEqual(len(warned), 1)
        self.assertAlmostEqual(warned[0], 60, delta=1)
        self.assertEqual(self.captured(), ["reason: warn"])
        self.assertEqual(self.notify.call_count, 1)
        self.assertEqual(self.signals, [])

    def test_dip_below_the_line_restarts_the_hold(self):
        self.meminfo(gpu=5, avail=6)
        self.at(50, lambda: self.meminfo(gpu=3.9, avail=6))
        self.at(51, lambda: self.meminfo(gpu=5, avail=6))
        self.watch(seconds=100)
        self.assertEqual(self.logged("WARN "), [])

    def test_critical_needs_low_memavailable_too(self):
        self.meminfo(gpu=8, avail=6)
        self.watch(seconds=600)
        self.assertEqual(self.logged("CRITICAL"), [])
        self.assertEqual(self.signals, [])

    def test_runaway_gets_sigterm_and_time_to_save(self):
        self.meminfo(gpu=8, avail=1)
        self.on_term = self.quits
        self.watch(seconds=120)
        self.assertEqual(self.signals, [(BROWSER, SIGTERM)])
        self.assertAlmostEqual(self.sent_at[0], 15, delta=1)
        self.assertEqual(self.captured(), ["reason: critical"])

    def test_ignored_sigterm_gets_sigkill_after_the_grace(self):
        self.meminfo(gpu=8, avail=1)
        self.watch(seconds=120)
        self.assertEqual(self.signals, [(BROWSER, SIGTERM), (BROWSER, SIGKILL)])
        self.assertAlmostEqual(self.sent_at[1] - self.sent_at[0], 10, delta=1)

    def test_dry_run_reports_and_never_signals(self):
        self.meminfo(gpu=8, avail=1)
        self.watch("--dry-run", seconds=600)
        self.assertEqual(self.signals, [])
        self.assertTrue(self.logged("dry-run:"))
        self.assertIn("reason: critical", self.captured())

    # A stage walks every fd on the machine while memory is short and, at the
    # critical one, with the signal still to send: once, not once to pick the
    # target and again for the capture's table.
    def test_each_stage_walks_the_fds_once(self):
        self.meminfo(gpu=8, avail=1)
        with mock.patch.object(gpuwatchdog, "gpu_clients",
                               wraps=gpuwatchdog.gpu_clients) as walk:
            self.watch("--dry-run", "--heartbeat", "0", seconds=70)
        self.assertEqual(self.captured(), ["reason: critical", "reason: warn"])
        # ...and the third is the self-check at startup.
        self.assertEqual(walk.call_count, 3)

    def test_no_allowlisted_holder_means_no_signal(self):
        self.exit(BROWSER)
        self.meminfo(gpu=8, avail=1)
        self.watch(seconds=600)
        self.assertTrue(self.logged("CRITICAL"))
        self.assertEqual(self.signals, [])

    def test_one_intervention_per_cooldown(self):
        def relaunched_and_runs_away_again():
            self.exit(BROWSER)
            self.spawn(OTHER, "hellfire", drm(29, "7340036 KiB"))
            self.on_term = None

        self.meminfo(gpu=8, avail=1)
        self.on_term = relaunched_and_runs_away_again
        self.watch(seconds=400)
        self.assertEqual(self.signals[:2], [(BROWSER, SIGTERM), (OTHER, SIGTERM)])
        self.assertGreaterEqual(self.sent_at[1] - self.sent_at[0], 300)

    def test_pid_recycled_before_sigterm_is_not_signalled(self):
        # The notification is raised between choosing the target and
        # signalling it, and at 20 s only the critical one has been raised.
        self.notify.side_effect = lambda *_: self.spawn(
            BROWSER, "hellfire", drm(29, "204800 KiB"), start=2)
        self.meminfo(gpu=8, avail=1)
        self.watch(seconds=20)
        self.assertTrue(self.logged("CRITICAL"))
        self.assertEqual(self.signals, [])

    def test_target_whose_start_time_is_unreadable_is_not_signalled(self):
        os.remove(f"{self.root}/proc/{BROWSER}/stat")
        self.meminfo(gpu=8, avail=1)
        self.watch(seconds=120)
        self.assertTrue(self.logged("CRITICAL"))
        self.assertEqual(self.signals, [])

    # Same comm, new start time: a comm check alone would kill the newcomer.
    def test_pid_recycled_during_the_grace_is_not_killed(self):
        def relaunched():
            self.meminfo(gpu=1.9, avail=6)
            self.spawn(BROWSER, "hellfire", drm(29, "204800 KiB"), start=2)

        self.meminfo(gpu=8, avail=1)
        self.on_term = relaunched
        self.watch(seconds=120)
        self.assertEqual(self.signals, [(BROWSER, SIGTERM)])

    # time.monotonic() counts from boot, so "never acted" and "never beat"
    # must not be 0.0: that made the first --cooldown seconds of uptime a
    # cooldown, and held the first heartbeat back to 30 minutes.
    def test_runaway_just_after_boot(self):
        self.clock.boot(20)
        self.meminfo(gpu=8, avail=1)
        self.on_term = self.quits
        self.watch(seconds=120)
        self.assertEqual(self.signals, [(BROWSER, SIGTERM)])
        self.assertTrue(self.logged("alive:"))

    def test_refused_signal_does_not_stop_the_watchdog(self):
        self.kill_error = PermissionError(errno.EPERM, "Operation not permitted")
        self.meminfo(gpu=8, avail=1)
        self.watch(seconds=120)
        self.assertEqual(self.signals, [(BROWSER, SIGTERM)])

    def test_missing_ptrace_capability_is_announced(self):
        self.spawn(OTHER, "systemd-resolve")
        self.denied.add(f"/proc/{OTHER}/fdinfo")
        self.watch(seconds=1)
        (warning,) = self.logged("WARNING")
        self.assertIn("CAP_SYS_PTRACE", warning)

    def test_boot_before_any_drm_client_is_not_a_warning(self):
        self.exit(BROWSER)
        self.exit(COMPOSITOR)
        self.spawn(OTHER, "systemd-resolve")
        self.watch(seconds=1)
        self.assertEqual(self.logged("WARNING"), [])

    def test_once_prints_a_reading_and_exits(self):
        with mock.patch.object(sys, "argv", ["gpuwatchdog", "--once"]), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(gpuwatchdog.main(), 0)
        self.assertIn("GPUActive: 1.90 GiB", out.getvalue())
        self.assertIn("hellfire", out.getvalue())


class ShippedUnit(WatchdogCase):
    """gpuwatchdog.service and its trial-mode drop-in, as they are installed."""

    def runaway(self, *drop_ins):
        self.spawn(BROWSER, "floorp", drm(28, "7340036 KiB"))
        self.spawn(COMPOSITOR, "shoji_wm", drm(7, "9437188 KiB"))
        self.meminfo(gpu=8, avail=1)
        self.watch(*unit_argv(*drop_ins), seconds=120)

    def test_armed_unit_stops_floorp(self):
        self.runaway()
        self.assertEqual(self.signals, [(BROWSER, SIGTERM), (BROWSER, SIGKILL)])

    def test_trial_drop_in_only_reports(self):
        self.runaway("gpuwatchdog-dryrun.conf")
        self.assertTrue(self.logged("dry-run:"))
        self.assertEqual(self.signals, [])


class Capture(WatchdogCase):
    def capture(self, directory):
        self.spawn(BROWSER, "hellfire", drm(28, "7340036 KiB"))
        return gpuwatchdog.capture(directory, "critical", "GPUActive=8.00 GiB",
                                   (BROWSER, 7340036, "hellfire"), True,
                                   gpuwatchdog.gpu_clients())

    def test_capture_records_what_dies_with_the_process(self):
        text = read(self.capture(self.capture_dir))
        self.assertIn("reason: critical", text)
        # Per-thread state and wchan: what was missing on 11/9.
        self.assertIn(f"  {BROWSER} S futex_do_wait", text)
        self.assertRegex(text, rf"{BROWSER} hellfire +7340036")
        self.assertIn("--- meminfo ---\nMemTotal:", text)

    # Each `cat` a capture used to run was a fork and an exec, with memory
    # short and, at the critical stage, the signal still to send.
    def test_capture_starts_no_process(self):
        self.capture(self.capture_dir)
        self.spawned.assert_not_called()

    def test_unwritable_capture_dir_is_not_fatal(self):
        blocker = os.path.join(self.root, "not-a-directory")
        write(blocker, "")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(self.capture(os.path.join(blocker, "captures")))


class Notify(unittest.TestCase):
    def notify(self, bus, **run):
        with mock.patch.object(os.path, "exists", return_value=bus), \
                mock.patch.object(os, "geteuid", return_value=0), \
                mock.patch.object(threading, "excepthook") as crashed, \
                mock.patch.object(subprocess, "run", **run) as spawned:
            sender = gpuwatchdog.notify(1000, "GPU memory climbing", "GPUActive=5.00 GiB")
            # Inside the patches: the send is on a thread, and one that outlived
            # them would run the real notify-send.
            if sender:
                sender.join()
        crashed.assert_not_called()
        return spawned

    def test_no_session_bus_no_notification(self):
        self.notify(bus=False).assert_not_called()

    def test_root_notifies_as_the_user_on_their_bus(self):
        (argv,), kwargs = self.notify(bus=True).call_args
        self.assertEqual(argv[0], "notify-send")
        self.assertEqual(argv[-2:], ["GPU memory climbing", "GPUActive=5.00 GiB"])
        self.assertEqual(kwargs["user"], 1000)
        self.assertEqual(kwargs["env"]["DBUS_SESSION_BUS_ADDRESS"],
                         "unix:path=/run/user/1000/bus")

    def test_hung_notification_daemon_is_survived(self):
        self.notify(bus=True,
                    side_effect=subprocess.TimeoutExpired("notify-send", 5))

    # The critical notification is raised with the SIGTERM still to send, and
    # notify-send can take its whole timeout to come back.
    def test_notification_is_not_waited_for(self):
        answered = threading.Event()
        with mock.patch.object(os.path, "exists", return_value=True), \
                mock.patch.object(os, "geteuid", return_value=0), \
                mock.patch.object(subprocess, "run",
                                  side_effect=lambda *_, **_env: answered.wait(10)):
            sender = gpuwatchdog.notify(1000, "GPU runaway", "GPUActive=8.00 GiB")
            if sender is None:
                self.fail("nothing was sent")
            still_sending = sender.is_alive()
            answered.set()
            sender.join()
        self.assertTrue(still_sending)


if __name__ == "__main__":
    unittest.main()
