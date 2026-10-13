"""Tests for gpuwatchdog.py: attribution, the staged escalation, its safety rules.

    python3 tools/test_gpuwatchdog.py

Nothing here touches the machine it runs on. The watchdog sees a /proc built
under a temp dir and a clock that only counts; os.kill and subprocess.run are
recorders; every PID is above the kernel's PID_MAX_LIMIT. So no test can signal
a process, raise a desktop notification or write to /var/log/gpuwatchdog.
"""

import contextlib
import errno
import io
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
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

_listdir = os.listdir


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
        patch(os, "kill", self._kill)
        patch(os, "geteuid", return_value=0)
        patch(subprocess, "run",
              return_value=subprocess.CompletedProcess([], 0, "", ""))
        self.notify = patch(gpuwatchdog, "notify")

    def _path(self, path):
        if isinstance(path, str) and (path + "/").startswith("/proc/"):
            return self.root + path
        return path

    def _open(self, path, *args, **kwargs):
        return open(self._path(path), *args, **kwargs)

    def _listdir(self, path="."):
        if path in self.denied:
            raise PermissionError(errno.EACCES, "Permission denied", path)
        return _listdir(self._path(path))

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
        """A process with one fd per fdinfo text. Spawning a live PID again is
        that PID recycled."""
        self.exit(pid)
        proc = f"{self.root}/proc/{pid}"
        os.makedirs(f"{proc}/fdinfo")
        os.makedirs(f"{proc}/task/{pid}")
        # Field 22, the start time, is the 20th after the comm's closing paren.
        stat = f"{pid} ({comm}) S {'0 ' * 18}{start} 0 0\n"
        write(f"{proc}/comm", comm + "\n")
        write(f"{proc}/stat", stat)
        write(f"{proc}/task/{pid}/stat", stat)
        write(f"{proc}/task/{pid}/wchan", "futex_do_wait")
        for fd, text in enumerate(fdinfo):
            write(f"{proc}/fdinfo/{fd}", text)

    def exit(self, pid):
        shutil.rmtree(f"{self.root}/proc/{pid}", ignore_errors=True)

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

    def test_only_an_allowlisted_comm_is_a_target(self):
        self.spawn(COMPOSITOR, "shoji_wm", drm(7, "9437188 KiB"))
        self.spawn(BROWSER, "hellfire", drm(28, "307660 KiB"))
        self.assertEqual(gpuwatchdog.pick_target({"hellfire"}),
                         (BROWSER, 307660, "hellfire"))
        self.assertIsNone(gpuwatchdog.pick_target({"floorp"}))

    def test_biggest_allowlisted_holder_wins(self):
        self.spawn(BROWSER, "hellfire", drm(28, "307660 KiB"))
        self.spawn(OTHER, "floorp", drm(29, "7340036 KiB"))
        self.assertEqual(gpuwatchdog.pick_target({"hellfire", "floorp"}),
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
                                   (BROWSER, 7340036, "hellfire"), True)

    def test_capture_records_what_dies_with_the_process(self):
        text = read(self.capture(self.capture_dir))
        self.assertIn("reason: critical", text)
        # Per-thread state and wchan: what was missing on 11/9.
        self.assertIn(f"  {BROWSER} S futex_do_wait", text)
        self.assertRegex(text, rf"{BROWSER} hellfire +7340036")

    def test_unwritable_capture_dir_is_not_fatal(self):
        blocker = os.path.join(self.root, "not-a-directory")
        write(blocker, "")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(self.capture(os.path.join(blocker, "captures")))


class Notify(unittest.TestCase):
    def notify(self, bus, **run):
        with mock.patch.object(os.path, "exists", return_value=bus), \
                mock.patch.object(os, "geteuid", return_value=0), \
                mock.patch.object(subprocess, "run", **run) as spawned:
            gpuwatchdog.notify(1000, "GPU memory climbing", "GPUActive=5.00 GiB")
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


if __name__ == "__main__":
    unittest.main()
