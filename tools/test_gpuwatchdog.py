"""Tests for gpuwatchdog.py: attribution, the staged escalation, its safety rules.

    python3 tools/test_gpuwatchdog.py

Nothing here touches the machine it runs on. The watchdog sees a /proc built
under a temp dir, fds whose device numbers are made up and a clock that only
counts; os.kill and subprocess.run are recorders; every PID is above the
kernel's PID_MAX_LIMIT. So no test can signal a process, raise a desktop
notification or write to /var/log/gpuwatchdog: a file or directory the
watchdog makes outside the temp dir fails the test instead.

The fd walk runs where it was asked for, not on a thread, so that a run is the
same every time; self.stalled makes it stand still instead, as it does when a
GPU driver holds it. Only the Threaded tests start a real one.

A capture handed to a thread is likewise written by the clock: self.disk_stall
seconds after it was handed over, with the loop not held meanwhile, and the
loop is handed a stand-in for the thread that fails the test if it is waited
for. Only the Behind tests start the real one.
"""

import codecs
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

# A comm as the kernel keeps it: the first 15 bytes of the name, here 14
# letters and the first byte of a two-byte one. It is not valid UTF-8, and
# CUT_READ is what the watchdog reads it as.
CUT = "Benutzeroberflä".encode()[:15]
CUT_READ = "Benutzeroberfl\\xc3"

# What os.stat() answers for an fd, by what the fd is open on. An inotify fd
# is an anonymous inode, which has no file type at all.
RENDER_NODE = types.SimpleNamespace(st_mode=stat.S_IFCHR | 0o666,
                                    st_rdev=os.makedev(226, 128))
DEV_NULL = types.SimpleNamespace(st_mode=stat.S_IFCHR | 0o666,
                                 st_rdev=os.makedev(1, 3))
ANON_INODE = types.SimpleNamespace(st_mode=0o600, st_rdev=0)

_listdir = os.listdir
_stat = os.stat
_makedirs = os.makedirs
_write_behind = gpuwatchdog.write_behind


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def write(path, text):
    """Bytes are written as they are: /proc is not always text."""
    with open(path, "wb") as handle:
        handle.write(text if isinstance(text, bytes) else text.encode())


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


class Fence(unittest.TestCase):
    """Fails the test for a file or directory the watchdog would make outside
    self.root: a bug that writes elsewhere would otherwise write to this
    machine, in the directory the suite was started from."""

    root: str   # each test's temp dir, which its setUp() makes

    def fenced(self, path, mode="w"):
        if set(mode) & set("wax+") and not os.path.abspath(path).startswith(self.root + "/"):
            self.fail(f"the watchdog made {path!r} outside its temp dir")

    def _makedirs(self, path, *args, **kwargs):
        self.fenced(path)
        return _makedirs(path, *args, **kwargs)


class WatchdogCase(Fence, unittest.TestCase):
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
        self.on_disk = []       # ...and the captures written by then: None
                                # while there is no capture directory at all
        self.disk_stall = 0.0   # seconds the disk holds a capture's file open
        self.on_term = None     # what the target does on SIGTERM; None ignores it
        self.kill_error = None  # raised by os.kill once the signal is recorded
        self.journal = []       # (seconds since the run began, message)
        self.stalled = False    # whether a walk that begins now stands still
        self.out = []           # the walkers whose walk has not come back
        self.encodings = set()  # every encoding the watchdog opened a file in
        self.writers = []       # the stand-ins write_behind() handed back
        self.pending = []       # captures handed over and not yet on the disk
        self.meminfo(gpu=1.9, avail=6)
        self.addCleanup(self.each_capture_written_once)
        self.addCleanup(self.every_file_opened_as_utf8)
        self.addCleanup(self.no_writer_waited_for)

        def patch(*args, **kwargs):
            return self.enterContext(mock.patch.object(*args, **kwargs))

        patch(gpuwatchdog, "open", self._open, create=True)
        patch(gpuwatchdog, "time", self.clock)
        patch(gpuwatchdog, "log",
              lambda message: self.journal.append((self.clock.elapsed, message)))
        patch(os, "listdir", self._listdir)
        patch(os, "stat", self._stat)
        patch(os, "makedirs", self._makedirs)
        patch(os, "kill", self._kill)
        patch(os, "geteuid", return_value=0)
        self.spawned = patch(subprocess, "run",
                             return_value=subprocess.CompletedProcess([], 0, "", ""))
        self.notify = patch(gpuwatchdog, "notify")
        # A real mlockall() here would lock the test run's own memory.
        self.locked = patch(gpuwatchdog, "lock_memory", return_value=None)
        patch(gpuwatchdog, "write_behind", self._write_behind)
        if self.inline_walks:
            patch(gpuwatchdog.Walker, "_spawn", lambda walker: self._spawn(walker))

    inline_walks = True

    def each_capture_written_once(self):
        """Checked after every test: a second write of a capture is a second
        wait on the disk, and the listing in captured() cannot show it. Nor
        may a test end before a capture it handed over has landed: what it
        asserts would be about a disk still being written."""
        written = [path for path in self.opened if path.startswith(self.capture_dir)]
        self.assertEqual(sorted(written), sorted(set(written)))
        self.assertEqual(self.pending, [])

    def every_file_opened_as_utf8(self):
        """Checked after every test: a file opened as text in the locale's
        encoding reads and writes a name differently under another locale,
        and under an ASCII one a name outside ASCII raises."""
        self.assertEqual(self.encodings - {"utf-8"}, set())

    def no_writer_waited_for(self):
        """Checked after every test: a loop that waits for the thread writing
        its capture waits on the disk again."""
        for writer in self.writers:
            writer.join.assert_not_called()
            writer.is_alive.assert_not_called()

    def _write_behind(self, path, text):
        """Stands in for the thread a capture is written on: the file is there
        once the disk lets go of it, and nothing may wait for that."""
        def lands():
            self.pending.remove(path)
            with mock.patch.object(self, "disk_stall", 0.0):
                gpuwatchdog.write_capture(path, text)

        self.pending.append(path)
        self.at(self.disk_stall, lands)
        self.writers.append(mock.Mock(spec=threading.Thread))
        return self.writers[-1]

    def _spawn(self, walker):
        if self.stalled:
            self.out.append(walker)
        else:
            walker._walk()

    def stall(self):
        self.stalled = True

    def release(self):
        """The driver lets go: every walk that stood still comes back."""
        self.stalled = False
        while self.out:
            self.out.pop()._walk()

    def _path(self, path):
        if isinstance(path, str) and (path + "/").startswith("/proc/"):
            return self.root + path
        return path

    def _open(self, path, *args, **kwargs):
        self.opened.append(path)
        mode = args[0] if args else kwargs.get("mode", "r")
        if "b" not in mode:
            encoding = args[2] if len(args) > 2 else kwargs.get("encoding")
            self.encodings.add(encoding and codecs.lookup(encoding).name)
        self.fenced(path, mode)
        if path.startswith(self.capture_dir):
            self.clock.now += self.disk_stall
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
        self.on_disk.append(self.captured() if os.path.isdir(self.capture_dir)
                            else None)
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
        a live PID again is that PID recycled. `comm` may be bytes."""
        self.exit(pid)
        proc = f"{self.root}/proc/{pid}"
        os.makedirs(f"{proc}/fdinfo")
        comm = comm if isinstance(comm, bytes) else comm.encode()
        write(f"{proc}/comm", comm + b"\n")
        # Field 22, the start time, is the 20th after the comm's closing paren.
        write(f"{proc}/stat", b"%d (%s) S %s%d 0 0\n" % (pid, comm, b"0 " * 18, start))
        write(f"{proc}/status", b"Name:\t%s\nState:\tS (sleeping)\nPid:\t%d\n" % (comm, pid))
        write(f"{proc}/wchan", "futex_do_wait")
        write(f"{proc}/stack", "[<0>] futex_do_wait+0x5d/0xa0\n")
        self.thread(pid, pid, comm)
        for fd, text in enumerate(fdinfo):
            write(f"{proc}/fdinfo/{fd}", text)
            self.nodes[f"/proc/{pid}/fd/{fd}"] = (
                RENDER_NODE if "drm-client-id" in str(text) else ANON_INODE)

    def thread(self, pid, tid, name):
        """A thread of `pid`, asleep in a futex. `name` may be bytes."""
        task = f"{self.root}/proc/{pid}/task/{tid}"
        os.makedirs(task)
        name = name if isinstance(name, bytes) else name.encode()
        write(f"{task}/stat", b"%d (%s) S 0 0\n" % (tid, name))
        write(f"{task}/wchan", "futex_do_wait")

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
        """The first line of every capture written, by name: oldest first,
        and within one second critical before warn."""
        try:
            names = sorted(_listdir(self.capture_dir))
        except (FileNotFoundError, NotADirectoryError):
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

    # A byte that is not UTF-8 is read as its escape, the same each time. Read
    # strictly, it raised an error that nothing caught.
    def test_name_cut_mid_character_is_read(self):
        self.spawn(OTHER, CUT, drm(29, "4096 KiB"), start=42514)
        self.assertEqual(gpuwatchdog._comm(OTHER), CUT_READ)
        self.assertEqual(gpuwatchdog._start_time(OTHER), "42514")
        self.assertTrue(gpuwatchdog._same_process(OTHER, CUT_READ, "42514"))
        # Same PID, same start time, another name: not the process pinned.
        self.assertFalse(gpuwatchdog._same_process(OTHER, "Benutzeroberfl", "42514"))
        self.assertEqual(gpuwatchdog.gpu_clients(), {OTHER: 4096})
        self.assertIsNone(gpuwatchdog.pick_target({"hellfire"}, {OTHER: 4096}))

    # An allowlisted name with a byte in it that is not UTF-8 is another name.
    # A reader that dropped the byte would have made that process a target.
    def test_allowlisted_name_with_a_byte_that_is_not_utf8_is_no_target(self):
        for raw in (b"hellfire\xc3", b"\xffhellfire", b"hell\xfffire"):
            self.spawn(OTHER, raw, drm(29, "4096 KiB"))
            self.assertIsNone(gpuwatchdog.pick_target({"hellfire"}, {OTHER: 4096}), raw)

    # The kernel ends a comm with one newline, and that is all that may go:
    # stripped, each of these read as hellfire and was a target.
    def test_allowlisted_name_with_spaces_or_a_newline_is_no_target(self):
        for raw in (b" hellfire", b"hellfire ", b"\thellfire", b"hellfire\n",
                    b"hellfire\r", b"\nhellfire"):
            self.spawn(OTHER, raw, drm(29, "4096 KiB"))
            self.assertIsNone(gpuwatchdog.pick_target({"hellfire"}, {OTHER: 4096}), raw)
        self.spawn(OTHER, "hellfire", drm(29, "4096 KiB"), start=42514)
        self.assertEqual(gpuwatchdog.pick_target({"hellfire"}, {OTHER: 4096}),
                         (OTHER, 4096, "hellfire"))
        # Same PID, same start time, renamed since: not the process pinned.
        write(f"{self.root}/proc/{OTHER}/comm", b"hellfire \n")
        self.assertFalse(gpuwatchdog._same_process(OTHER, "hellfire", "42514"))

    # What does not print is read as its escape, so a name keeps to its line.
    def test_name_that_does_not_print_is_read_as_its_escapes(self):
        for raw, read_as in ((b"a\nb", "a\\nb"), (b"\x1b[2Jtab\there", "\\x1b[2Jtab\\there"),
                             ("x\u2028y".encode(), "x\\u2028y"), (b" floorp ", " floorp "),
                             # A byte that is not UTF-8 reads as it does alone.
                             (b"\xff\r", "\\xff\\r")):
            self.spawn(OTHER, raw)
            self.assertEqual(gpuwatchdog._comm(OTHER), read_as, raw)

    # A name that is UTF-8 and outside ASCII is read as itself.
    def test_name_outside_ascii_is_read_as_itself(self):
        self.spawn(OTHER, "fenêtre", drm(29, "4096 KiB"))
        self.assertEqual(gpuwatchdog._comm(OTHER), "fenêtre")

    # Nothing in a DRM fdinfo is known to be anything but ASCII: the kernel
    # takes a drm-client-name only in printable ASCII (drm_validate_value_string,
    # which refused this one on 27/11/2026). The read goes through the same
    # reader as the rest all the same, and must not be the one that raises.
    def test_fdinfo_that_is_not_utf8_is_read(self):
        named = drm(28, "165828 KiB").encode() + b"drm-client-name:\tfen\xeatre\n"
        self.spawn(BROWSER, "hellfire", named)
        self.assertEqual(gpuwatchdog.gpu_clients(), {BROWSER: 165828})

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
        # What the warn capture is for: who holds the memory and what its
        # threads are doing, beside everything else that holds some.
        (capture,) = _listdir(self.capture_dir)
        text = read(os.path.join(self.capture_dir, capture))
        self.assertIn("\ndry-run: False\n", text)
        self.assertIn(f"\ntarget: ({BROWSER}, 7340036, 'hellfire')\n", text)
        self.assertIn(f"  {BROWSER} S futex_do_wait\n", text)
        self.assertRegex(text, rf"{COMPOSITOR} shoji_wm +9437188\n")

    # The capture that says who holds the memory when no allowlisted process
    # does is the one most worth having.
    def test_warn_with_no_allowlisted_holder_still_leaves_its_capture(self):
        self.exit(BROWSER)
        self.meminfo(gpu=5, avail=6)
        self.watch(seconds=70)
        self.assertEqual(self.captured(), ["reason: warn"])
        (capture,) = _listdir(self.capture_dir)
        text = read(os.path.join(self.capture_dir, capture))
        self.assertIn("target: None\n", text)
        self.assertRegex(text, rf"{COMPOSITOR} shoji_wm +9437188\n")

    def test_warn_with_no_attribution_yet_says_so_and_goes_on(self):
        self.stall()
        self.meminfo(gpu=5, avail=6)
        self.watch("--walk-wait", "0", seconds=70)
        (warned,) = self.logged("WARN ")
        self.assertIn("target=None", warned)
        (capture,) = _listdir(self.capture_dir)
        self.assertIn("target: None (no attribution: the fd walk has been out ",
                      read(os.path.join(self.capture_dir, capture)))

    def test_warn_capture_says_how_old_its_attribution_is(self):
        self.meminfo(gpu=5, avail=6)
        self.at(12, self.stall)
        self.watch("--walk-wait", "0", seconds=70)
        (capture,) = _listdir(self.capture_dir)
        self.assertRegex(read(os.path.join(self.capture_dir, capture)),
                         r"target: \(5000001, 7340036, 'hellfire'\) \(attribution \d+s old: "
                         r"the fd walk has been out \d+s\)\n")

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

    # What a capture records dies with the target, so it is read ahead of the
    # SIGTERM: the browser is gone by the time the file is written.
    def test_capture_is_read_before_the_sigterm_and_written_after(self):
        self.meminfo(gpu=8, avail=1)
        self.on_term = self.quits
        self.watch(seconds=120)
        self.assertEqual(self.signals, [(BROWSER, SIGTERM)])
        self.assertEqual(self.on_disk, [None])
        (capture,) = _listdir(self.capture_dir)
        text = read(os.path.join(self.capture_dir, capture))
        self.assertIn("reason: critical", text)
        self.assertIn(f"  {BROWSER} S futex_do_wait", text)

    # A target that ignores the SIGTERM is still there to be read at the
    # SIGKILL, but not as the stage found it.
    def test_capture_of_a_target_that_ignores_sigterm_is_from_before_it(self):
        self.meminfo(gpu=8, avail=1)
        self.on_term = lambda: write(
            f"{self.root}/proc/{BROWSER}/task/{BROWSER}/wchan", "do_exit")
        self.watch(seconds=40)
        self.assertEqual(self.signals, [(BROWSER, SIGTERM), (BROWSER, SIGKILL)])
        (capture,) = _listdir(self.capture_dir)
        self.assertIn(f"  {BROWSER} S futex_do_wait",
                      read(os.path.join(self.capture_dir, capture)))

    # A run that ends inside the grace leaves the stage by an exception, with
    # the SIGTERM sent and the SIGKILL not: the capture is written all the same.
    def test_stage_left_by_an_exception_still_leaves_its_capture(self):
        self.meminfo(gpu=8, avail=1)
        self.watch(seconds=20)      # Stop comes out of the grace loop's sleep
        self.assertEqual(self.signals, [(BROWSER, SIGTERM)])
        self.assertEqual(self.captured(), ["reason: critical"])

    # The capture's file is the one step of a stage that waits on the disk,
    # and it used to come first: a disk that held it for 30 s sent the SIGTERM
    # 30 s late.
    def test_stalled_disk_does_not_hold_the_signals_back(self):
        self.meminfo(gpu=8, avail=1)
        self.disk_stall = 30.0
        self.watch(seconds=40)
        self.assertEqual(self.signals, [(BROWSER, SIGTERM), (BROWSER, SIGKILL)])
        self.assertAlmostEqual(self.sent_at[0], 15, delta=1)
        self.assertAlmostEqual(self.sent_at[1], 25, delta=1)
        self.assertEqual(self.on_disk, [None, None])
        self.assertEqual(self.captured(), ["reason: critical"])
        # The CRITICAL line named the file 10 s before it was written.
        (capture,) = _listdir(self.capture_dir)
        self.assertIn(f"capture={os.path.join(self.capture_dir, capture)}",
                      self.logged("CRITICAL")[0])

    # The log has named the file by the time its write fails, so the failure
    # is a line of its own that names it too, and it costs neither signal.
    def test_capture_that_cannot_be_written_costs_no_signal(self):
        blocker = os.path.join(self.root, "not-a-directory")
        write(blocker, "")
        self.meminfo(gpu=8, avail=1)
        with contextlib.redirect_stderr(io.StringIO()) as said:
            self.watch("--capture-dir", os.path.join(blocker, "captures"), seconds=40)
        self.assertEqual(self.signals, [(BROWSER, SIGTERM), (BROWSER, SIGKILL)])
        (critical,) = self.logged("CRITICAL")
        named = critical.partition("capture=")[2].split()[0]
        self.assertIn(f"capture {named} failed: ", said.getvalue())

    # The warn stage's capture was written from the loop, which took no
    # reading until the disk let go of the file. With the critical line
    # crossed 50 s in and that write held for 30 s, the SIGTERM went out at
    # 91 s where 65 s was due.
    def test_stalled_disk_at_the_warn_stage_does_not_stop_the_readings(self):
        self.meminfo(gpu=5, avail=6)
        self.at(50, lambda: self.meminfo(gpu=8, avail=1))
        self.disk_stall = 30.0
        self.on_term = self.quits
        self.watch(seconds=140)
        (warned,) = self.logged_at("WARN ")
        self.assertAlmostEqual(warned, 60, delta=1)
        self.assertEqual(self.signals, [(BROWSER, SIGTERM)])
        self.assertAlmostEqual(self.sent_at[0], 65, delta=1)
        # Neither capture was on the disk yet, and both are in the end.
        self.assertEqual(self.on_disk, [None])
        self.assertEqual(self.captured(), ["reason: warn", "reason: critical"])
        # The WARN line named its file before it was written.
        named = self.logged("WARN ")[0].partition("capture=")[2].split()[0]
        self.assertEqual(named, os.path.join(self.capture_dir, min(_listdir(self.capture_dir))))

    # With no thread to be had the warn capture is written from the loop, as
    # it used to be: the one corner where the disk can still hold a SIGTERM
    # back. The WARN line and the notification go out ahead of the write.
    def test_no_thread_for_the_warn_capture_writes_it_last_from_the_loop(self):
        self.meminfo(gpu=5, avail=6)
        self.at(50, lambda: self.meminfo(gpu=8, avail=1))
        self.disk_stall = 30.0
        self.on_term = self.quits
        notified_at = []
        self.notify.side_effect = lambda *_: notified_at.append(self.clock.elapsed)
        with mock.patch.object(gpuwatchdog, "write_behind", _write_behind), \
                mock.patch.object(threading.Thread, "start",
                                  side_effect=RuntimeError("can't start new thread")):
            self.watch(seconds=140)
        (warned,) = self.logged_at("WARN ")
        self.assertAlmostEqual(warned, 60, delta=1)
        self.assertAlmostEqual(notified_at[0], 60, delta=1)
        self.assertAlmostEqual(self.sent_at[0], 91, delta=1)
        self.assertEqual(self.captured(), ["reason: warn", "reason: critical"])

    # A warn and a critical capture taken by one reading had one name between
    # them, and the critical one replaced the other.
    def test_warn_and_critical_captures_of_one_reading_are_both_kept(self):
        self.meminfo(gpu=5, avail=6)
        self.at(45.5, lambda: self.meminfo(gpu=8, avail=1))
        self.on_term = self.quits
        self.watch(seconds=120)
        self.assertEqual(self.logged_at("WARN "), self.logged_at("CRITICAL"))
        self.assertEqual(self.signals, [(BROWSER, SIGTERM)])
        self.assertEqual(self.captured(), ["reason: critical", "reason: warn"])

    # One DRM client whose name was cut in the middle of a character ended the
    # watchdog at its start-up walk, and at every restart after that.
    def test_client_with_a_name_cut_mid_character_does_not_end_the_watchdog(self):
        self.spawn(OTHER, CUT, drm(29, "4096 KiB"))
        self.meminfo(gpu=8, avail=1)
        self.on_term = self.quits
        self.watch(seconds=120)
        self.assertEqual(self.logged("attribution ok"),
                         ["attribution ok: 3 DRM clients visible"])
        self.assertEqual(self.signals, [(BROWSER, SIGTERM)])
        self.assertAlmostEqual(self.sent_at[0], 15, delta=1)
        (capture,) = _listdir(self.capture_dir)
        self.assertIn(f"{OTHER} {CUT_READ} ",
                      read(os.path.join(self.capture_dir, capture)))

    # ...and a thread of the target with such a name ended it at the critical
    # stage's snapshot: ahead of the CRITICAL line, the notification and the
    # SIGTERM, so the journal showed nothing of the stage but the traceback.
    def test_target_thread_with_a_name_cut_mid_character_costs_no_signal(self):
        self.thread(BROWSER, BROWSER + 1, CUT)
        self.meminfo(gpu=8, avail=1)
        self.on_term = self.quits
        self.watch(seconds=120)
        self.assertEqual(self.signals, [(BROWSER, SIGTERM)])
        self.assertAlmostEqual(self.sent_at[0], 15, delta=1)
        (capture,) = _listdir(self.capture_dir)
        self.assertIn(f"  {BROWSER + 1} S futex_do_wait",
                      read(os.path.join(self.capture_dir, capture)))

    # A name outside ASCII goes into the capture as itself, in UTF-8.
    def test_name_outside_ascii_reaches_the_capture_as_utf8(self):
        self.spawn(OTHER, "fenêtre", drm(29, "4096 KiB"))
        self.meminfo(gpu=8, avail=1)
        self.on_term = self.quits
        self.watch(seconds=120)
        self.assertEqual(self.signals, [(BROWSER, SIGTERM)])
        (capture,) = _listdir(self.capture_dir)
        with open(os.path.join(self.capture_dir, capture), "rb") as handle:
            self.assertIn(f"{OTHER} fenêtre ".encode("utf-8"), handle.read())

    # A name is whatever its process chose, newlines included. Now that only
    # the kernel's newline goes, this one (15 bytes, all a comm holds) would,
    # printed as it is, begin a line of the table with the header of a stage
    # that signalled. A count of those by the line's start would take it for
    # one; one by whole lines (`grep -x`) would not.
    def test_client_name_cannot_add_a_line_to_the_capture(self):
        self.spawn(OTHER, b"\ndry-run: False", drm(29, "4096 KiB"))
        self.meminfo(gpu=8, avail=1)
        self.watch("--dry-run", seconds=120)
        self.assertEqual(self.captured(), ["reason: critical", "reason: warn"])
        for name in _listdir(self.capture_dir):
            text = read(os.path.join(self.capture_dir, name))
            self.assertNotIn("\ndry-run: False", text, name)
            self.assertIn(f"{OTHER} \\ndry-run: False ", text, name)

    def test_dry_run_reports_and_never_signals(self):
        self.meminfo(gpu=8, avail=1)
        self.watch("--dry-run", seconds=600)
        self.assertEqual(self.signals, [])
        self.assertTrue(self.logged("dry-run:"))
        self.assertIn("reason: critical", self.captured())
        # Each capture says it was a rehearsal, the warn one among them.
        self.assertIn("reason: warn", self.captured())
        for name in _listdir(self.capture_dir):
            self.assertIn("\ndry-run: True\n", read(os.path.join(self.capture_dir, name)), name)

    # A stage walks every fd on the machine while memory is short and, at the
    # critical one, with the signal still to send: once, not once to pick the
    # target and again for the capture's table.
    def test_each_stage_walks_the_fds_once(self):
        self.meminfo(gpu=8, avail=1)
        with mock.patch.object(gpuwatchdog, "gpu_clients",
                               wraps=gpuwatchdog.gpu_clients) as walk:
            self.watch("--dry-run", "--heartbeat", "0", "--walk-every", "0",
                       seconds=70)
        self.assertEqual(self.captured(), ["reason: critical", "reason: warn"])
        # ...and the third is the self-check at startup.
        self.assertEqual(walk.call_count, 3)

    # Above the warn line a walk every 10 s keeps the attribution recent, for
    # the stage whose own walk does not come back. Below it there is none.
    def test_attribution_is_kept_recent_only_above_the_warn_line(self):
        with mock.patch.object(gpuwatchdog, "gpu_clients",
                               wraps=gpuwatchdog.gpu_clients) as walk:
            self.watch("--heartbeat", "0", seconds=300)
            self.assertEqual(walk.call_count, 1)
            self.meminfo(gpu=5, avail=6)
            self.watch("--heartbeat", "0", seconds=55)
        self.assertEqual(walk.call_count, 1 + 1 + 5)

    # What 13/10/2026 would have cost with the watchdog armed: the driver
    # holds every fdinfo read for a minute or two, and the stage that waited
    # for its walk sent the SIGTERM that much late.
    def test_stalled_walk_does_not_hold_the_sigterm_back(self):
        self.meminfo(gpu=8, avail=1)
        self.at(12, self.stall)
        self.on_term = self.quits
        self.watch("--walk-wait", "0", seconds=120)
        self.assertEqual(self.signals, [(BROWSER, SIGTERM)])
        self.assertAlmostEqual(self.sent_at[0], 15, delta=1)
        (critical,) = self.logged("CRITICAL")
        # The walk of 10 s is the newest that came back; the one of 15 s is out.
        self.assertIn("hellfire(5000001) holding 7.00 GiB", critical)
        self.assertIn("(attribution 5s old: the fd walk has been out 0s)", critical)
        (capture,) = _listdir(self.capture_dir)
        self.assertIn("(attribution 5s old", read(os.path.join(self.capture_dir, capture)))

    def test_stalled_walk_does_not_stop_the_readings(self):
        self.meminfo(gpu=5, avail=6)
        self.at(12, self.stall)
        self.at(70, lambda: self.meminfo(gpu=8, avail=1))
        self.on_term = self.quits
        self.watch("--walk-wait", "0", seconds=120)
        (warning,) = self.logged_at("WARN ")
        self.assertAlmostEqual(warning, 60, delta=1)
        self.assertAlmostEqual(self.sent_at[0], 85, delta=1)

    # The start time is pinned by the walk, with the PID. Read at the stage,
    # it vouched for whatever held the PID by then.
    def test_pid_recycled_since_the_walk_is_not_signalled(self):
        self.meminfo(gpu=8, avail=1)
        self.at(12, self.stall)
        self.at(13, lambda: self.spawn(BROWSER, "hellfire", drm(29, "204800 KiB"),
                                       start=2))
        self.watch("--walk-wait", "0", seconds=20)
        self.assertTrue(self.logged("CRITICAL"))
        self.assertTrue(self.logged(f"target {BROWSER} changed identity"))
        self.assertEqual(self.signals, [])

    # Nothing was walked while GPUActive was low, and no walk has come back
    # since it rose: the start-up attribution is too old to pick a victim by.
    def test_attribution_too_old_waits_for_a_walk_and_then_acts(self):
        self.at(1, self.stall)
        self.at(400, lambda: self.meminfo(gpu=8, avail=1))
        self.at(450, self.release)
        self.on_term = self.quits
        self.watch("--heartbeat", "0", "--walk-wait", "0", seconds=500)
        self.assertEqual(self.signals, [(BROWSER, SIGTERM)])
        self.assertAlmostEqual(self.sent_at[0], 450, delta=1)
        blind, acted = self.logged("CRITICAL")
        self.assertIn("but no attribution newer than 300s (attribution 415s old: "
                      "the fd walk has been out 15s); signalling once a walk is back",
                      blind)
        self.assertIn("hellfire(5000001) holding 7.00 GiB", acted)

    def test_walk_out_since_startup_is_announced(self):
        self.stall()
        self.watch("--walk-wait", "0", seconds=1)
        (warning,) = self.logged("WARNING")
        self.assertIn("the first fd walk is not back", warning)
        self.assertEqual(self.logged("attribution ok"), [])

    def test_walk_that_fails_ends_the_watchdog_as_it_did(self):
        self.meminfo(gpu=5, avail=6)
        self.at(30, lambda: write(f"{self.root}/proc/{BROWSER}/fdinfo/0",
                                  drm(28, "many KiB")))
        self.clock.stop_at = self.clock.now + 120
        with mock.patch.object(sys, "argv", ["gpuwatchdog", "--capture-dir",
                                             self.capture_dir]), \
                self.assertRaises(ValueError):
            gpuwatchdog.main()
        self.assertLess(self.clock.elapsed, 60)

    def test_no_allowlisted_holder_means_no_signal(self):
        self.exit(BROWSER)
        self.meminfo(gpu=8, avail=1)
        self.watch(seconds=600)
        self.assertTrue(self.logged("CRITICAL"))
        self.assertEqual(self.signals, [])
        self.assertIn("reason: critical", self.captured())

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
        # A stage that stands down still leaves its capture.
        self.assertEqual(self.captured(), ["reason: critical"])

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
        self.assertIn("reason: critical", self.captured())

    # Only the SIGKILL is refused: the stage ends as it does when one is sent.
    def test_refused_sigkill_does_not_stop_the_watchdog(self):
        def refuse_the_next():
            self.kill_error = PermissionError(errno.EPERM, "Operation not permitted")

        self.meminfo(gpu=8, avail=1)
        self.on_term = refuse_the_next
        self.watch(seconds=40)
        self.assertEqual(self.signals, [(BROWSER, SIGTERM), (BROWSER, SIGKILL)])
        self.assertTrue(self.logged(f"SIGKILL {BROWSER} failed"))
        self.assertEqual(self.captured(), ["reason: critical"])

    def test_missing_ptrace_capability_is_announced(self):
        self.spawn(OTHER, "systemd-resolve")
        self.denied.add(f"/proc/{OTHER}/fdinfo")
        self.watch(seconds=1)
        (warning,) = self.logged("WARNING")
        self.assertIn("CAP_SYS_PTRACE", warning)

    # The journal makes an entry of each line: printed as it is, this name
    # (15 bytes) wrote one that reads as the watchdog's heartbeat.
    def test_name_in_a_log_line_keeps_to_it(self):
        self.spawn(OTHER, b"x\nalive: forged")
        self.denied.add(f"/proc/{OTHER}/fdinfo")
        self.watch(seconds=1)
        (warning,) = self.logged("WARNING")
        self.assertIn("(e.g. x\\nalive: forged(5000003))", warning)
        self.assertEqual([m for _, m in self.journal if "\n" in m], [])

    # A target is compared with the name as read, escapes and all. One that
    # does not print never matched its own process, one with a backslash in
    # it could match another (this one, a tab read as \t), one past 15 bytes
    # never matches at all (Benutzeroberflä is 15 characters and 16 bytes),
    # and an empty one matches each process whose name cannot be read: each
    # is left out, and said so. 15 bytes, the most a comm holds, are a target.
    def test_target_that_cannot_match_as_written_is_left_out_loudly(self):
        self.spawn(OTHER, b"fire\tfox", drm(29, "8388608 KiB"))
        self.meminfo(gpu=8, avail=1)
        self.on_term = self.quits
        self.watch("--target", "hellfire", "--target", "fire\\tfox", "--target", "fire\tfox",
                   "--target", "Isolated Web Con", "--target", "Isolated Web Co",
                   "--target", "Benutzeroberflä", "--target", "€€€€€",
                   "--target", "", seconds=120)
        (warning,) = self.logged("WARNING")
        self.assertIn("ignoring --target ['', 'Benutzeroberflä', 'Isolated Web Con', "
                      "'fire\\tfox', 'fire\\\\tfox']", warning)
        self.assertIn("targets=['Isolated Web Co', 'hellfire', '€€€€€']",
                      self.logged("armed")[0])
        self.assertEqual(self.signals, [(BROWSER, SIGTERM)])

    # A name outside ASCII is a target like any other.
    def test_target_outside_ascii_is_armed_and_signalled(self):
        self.spawn(OTHER, "fenêtre", drm(29, "8388608 KiB"))
        self.meminfo(gpu=8, avail=1)

        def quits():
            self.exit(OTHER)
            self.meminfo(gpu=1.9, avail=6)

        self.on_term = quits
        self.watch("--target", "hellfire", "--target", "fenêtre", seconds=120)
        self.assertEqual(self.logged("WARNING"), [])
        self.assertEqual(self.signals, [(OTHER, SIGTERM)])

    # What the output cannot carry is printed as its escape: a print that
    # raised, on a terminal or a journal stream that is not UTF-8, ended it.
    def test_once_on_an_ascii_terminal(self):
        self.spawn(OTHER, "fenêtre", drm(29, "8388608 KiB"))
        terminal = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
        with mock.patch.object(sys, "argv", ["gpuwatchdog", "--once", "--target", "fenêtre"]), \
                contextlib.redirect_stdout(terminal):
            self.assertEqual(gpuwatchdog.main(), 0)
        terminal.flush()
        self.assertIn(b"largest target: (5000003, 8388608, 'fen\\xeatre')",
                      terminal.buffer.getvalue())

    # Before the first thread: once one has run, glibc's arenas put the
    # address space past any small limit, and a walk is a thread.
    def test_memory_is_locked_before_the_first_walk(self):
        order = []
        self.locked.side_effect = lambda: order.append("lock")
        with mock.patch.object(gpuwatchdog, "gpu_clients",
                               side_effect=lambda: order.append("walk") or {}):
            self.watch(seconds=1)
        self.assertEqual(order[:2], ["lock", "walk"])
        self.assertEqual(self.logged("memory locked"), ["memory locked"])

    def test_memory_that_cannot_be_locked_costs_only_a_line(self):
        self.locked.return_value = "Cannot allocate memory"
        self.meminfo(gpu=8, avail=1)
        self.on_term = self.quits
        self.watch(seconds=120)
        (note,) = self.logged("note: memory not locked (Cannot allocate memory)")
        self.assertIn("LimitMEMLOCK=infinity", note)
        self.assertEqual(self.signals, [(BROWSER, SIGTERM)])

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
        self.locked.assert_not_called()


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

    # The script locks its whole address space, 26 MB at start-up, which
    # systemd's default limit of 8 MB refuses.
    def test_unit_lets_the_watchdog_lock_its_memory(self):
        self.assertIn("LimitMEMLOCK=infinity",
                      read(os.path.join(HERE, "gpuwatchdog.service")).splitlines())


class Capture(WatchdogCase):
    def snapshot(self, directory, reason="critical"):
        """The snapshot of a runaway browser, for `directory`."""
        self.spawn(BROWSER, "hellfire", drm(28, "7340036 KiB"))
        return gpuwatchdog.snapshot(directory, reason, "GPUActive=8.00 GiB",
                                    (BROWSER, 7340036, "hellfire"), True,
                                    gpuwatchdog.gpu_clients())

    def capture(self, directory):
        return gpuwatchdog.write_capture(*self.snapshot(directory))

    # A snapshot runs ahead of the SIGTERM, so it must not wait on a disk:
    # everything in it is read from /proc, and the file is a separate step.
    def test_snapshot_reads_only_proc_and_writes_nothing(self):
        path, text = self.snapshot(self.capture_dir)
        self.assertIn(gpuwatchdog.MEMINFO, self.opened)
        self.assertEqual([p for p in self.opened if not p.startswith("/proc/")], [])
        self.assertFalse(os.path.exists(self.capture_dir))
        self.assertEqual(gpuwatchdog.write_capture(path, text), path)
        self.assertEqual(read(path), text)

    # The stage is in the name: two stages can take a capture in one second.
    def test_capture_is_named_after_its_stage(self):
        names = [os.path.relpath(self.snapshot(self.capture_dir, reason)[0],
                                 self.capture_dir)
                 for reason in ("warn", "critical")]
        self.assertRegex(names[0], r"^gpuwatchdog-\d{8}-\d{6}-warn\.txt$")
        self.assertEqual(names[1], names[0].replace("warn", "critical"))

    def test_capture_records_what_dies_with_the_process(self):
        text = read(self.capture(self.capture_dir))
        self.assertIn("reason: critical", text)
        # Per-thread state and wchan: what was missing on 11/9.
        self.assertIn(f"  {BROWSER} S futex_do_wait", text)
        self.assertRegex(text, rf"{BROWSER} hellfire +7340036")
        self.assertIn("--- meminfo ---\nMemTotal:", text)
        self.assertIn(f"--- process {BROWSER} ---\n[status]\nName:\thellfire\n", text)
        self.assertIn("[wchan]\nfutex_do_wait\n[stack]\n[<0>] futex_do_wait+", text)

    # status escapes only a newline and a backslash in Name, and the target
    # can rename itself between the walk and the snapshot: this one's \r
    # began a line, "dry-run: False" in a rehearsal's capture.
    def test_target_renamed_since_the_walk_keeps_to_its_line(self):
        self.spawn(BROWSER, "hellfire", drm(28, "7340036 KiB"))
        write(f"{self.root}/proc/{BROWSER}/status",
              b"Name:\t\rdry-run: False\nState:\tS (sleeping)\n")
        _, text = gpuwatchdog.snapshot(self.capture_dir, "critical", "GPUActive=8.00 GiB",
                                       (BROWSER, 7340036, "hellfire"), True, {})
        self.assertIn("[status]\nName:\t\\rdry-run: False\nState:\tS (sleeping)\n", text)
        self.assertNotIn("\r", text)

    # Each `cat` a capture used to run was a fork and an exec, with memory
    # short and, at the critical stage, the signal still to send.
    def test_capture_starts_no_process(self):
        self.capture(self.capture_dir)
        self.spawned.assert_not_called()

    def test_unwritable_capture_dir_is_not_fatal(self):
        blocker = os.path.join(self.root, "not-a-directory")
        write(blocker, "")
        with contextlib.redirect_stderr(io.StringIO()) as said:
            self.assertIsNone(self.capture(os.path.join(blocker, "captures")))
        self.assertRegex(said.getvalue(),
                         r"capture \S+/captures/gpuwatchdog-\S+-critical\.txt failed: ")


class Threaded(WatchdogCase):
    """The walk on the thread it really runs on, held where a driver holds
    it: inside the read of one fdinfo."""

    inline_walks = False

    def test_walk_held_in_a_read_is_not_waited_out(self):
        self.spawn(BROWSER, "hellfire", drm(28, "1000 KiB"))
        walker = gpuwatchdog.Walker({"hellfire"})
        first = walker.fresh(30)
        if first is None:
            self.fail("the first walk never came back")
        self.assertEqual((first.target, first.started),
                         ((BROWSER, 1000, "hellfire"), "1"))
        self.assertEqual(walker.caveat(), "")

        self.spawn(BROWSER, "hellfire", drm(28, "2000 KiB"))
        reached, let_go = threading.Event(), threading.Event()
        opener = self._open

        def holding(path, *args, **kwargs):
            if path == f"/proc/{BROWSER}/fdinfo/0":
                reached.set()
                let_go.wait(30)
            return opener(path, *args, **kwargs)

        with mock.patch.object(gpuwatchdog, "open", holding):
            began = time.monotonic()
            self.assertIs(walker.fresh(0.05), first)
            self.assertLess(time.monotonic() - began, 10)
            self.assertTrue(reached.wait(30))
            self.assertIn("attribution 0s old", walker.caveat())
            # A second request does not start a second walk behind the first.
            self.assertIs(walker.fresh(0), first)
            let_go.set()
            # Inside the patches: a walk that outlived them would read the
            # real /proc.
            latest = walker.collect(30)
        if latest is None:
            self.fail("the held walk never came back")
        self.assertEqual(latest.target, (BROWSER, 2000, "hellfire"))
        self.assertEqual(walker.caveat(), "")
        self.assertEqual(self.opened.count(f"/proc/{BROWSER}/fdinfo/0"), 2)


class LockMemory(unittest.TestCase):
    """lock_memory() against a stand-in for libc: the real call would lock
    this test run's own memory."""

    def lock(self, result, error=0):
        libc = mock.Mock()
        libc.mlockall.return_value = result
        with mock.patch("ctypes.CDLL", return_value=libc) as cdll, \
                mock.patch("ctypes.get_errno", return_value=error):
            answer = gpuwatchdog.lock_memory()
        cdll.assert_called_once_with(None, use_errno=True)
        return libc, answer

    # MCL_CURRENT (1) alone. MCL_FUTURE (2) would lock each later mapping,
    # a new thread's whole stack among them, and MCL_ONFAULT (4) would leave
    # out the code that has not run yet, which is the code a stage runs.
    def test_locks_what_is_mapped_now_and_nothing_later(self):
        libc, answer = self.lock(0)
        libc.mlockall.assert_called_once_with(1)
        self.assertIsNone(answer)

    def test_refusal_says_why(self):
        for error in (errno.ENOMEM, errno.EPERM):
            with self.subTest(error=errno.errorcode[error]):
                _, answer = self.lock(-1, error)
                self.assertEqual(answer, os.strerror(error))

    # Nor does escaping a name import anything, as encoding it did: the codec
    # was read from the disk at its first use, at a stage. In an interpreter
    # of its own, since this one may hold that codec already.
    def test_escaping_a_name_imports_nothing(self):
        probe = ("import sys; sys.path.insert(0, sys.argv[1]); import gpuwatchdog; "
                 "before = set(sys.modules); "
                 "gpuwatchdog._printable(''.join(map(chr, (97, 27, 13, 0x200b, 0xe0001))), "
                 "keep=chr(9) + chr(10)); "
                 "sys.exit(sorted(set(sys.modules) - before) or None)")
        done = subprocess.run([sys.executable, "-I", "-B", "-c", probe, HERE],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual((done.returncode, done.stderr), (0, ""))


class Behind(Fence, unittest.TestCase):
    """write_behind() on the thread it really starts, with the disk holding
    the capture's file where it holds it: at the open."""

    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        self.path = os.path.join(self.root, "captures", "gpuwatchdog-warn.txt")
        self.enterContext(mock.patch.object(gpuwatchdog, "open", self._open, create=True))
        self.enterContext(mock.patch.object(os, "makedirs", self._makedirs))

    def _open(self, path, mode="r", *args, **kwargs):
        self.fenced(path, mode)
        return open(path, mode, *args, **kwargs)

    def test_write_is_not_waited_for(self):
        reached, let_go = threading.Event(), threading.Event()

        def holding(*args, **kwargs):
            reached.set()
            let_go.wait(30)
            return self._open(*args, **kwargs)

        with mock.patch.object(gpuwatchdog, "open", holding, create=True), \
                mock.patch.object(threading, "excepthook") as crashed:
            with mock.patch.object(threading.Thread, "join") as joined:
                writer = gpuwatchdog.write_behind(self.path, "reason: warn\n")
            joined.assert_not_called()
            if writer is None:
                self.fail("the capture was written here, not on a thread")
            self.assertTrue(reached.wait(30))
            held = (writer.is_alive(), os.path.exists(self.path))
            let_go.set()
            writer.join(30)
        crashed.assert_not_called()
        self.assertEqual(held, (True, False))
        # A watchdog on its way out does not wait for the disk either.
        self.assertTrue(writer.daemon)
        self.assertEqual(read(self.path), "reason: warn\n")

    def test_no_thread_to_be_had_writes_here(self):
        with mock.patch.object(threading.Thread, "start",
                               side_effect=RuntimeError("can't start new thread")):
            self.assertIsNone(gpuwatchdog.write_behind(self.path, "reason: warn\n"))
        self.assertEqual(read(self.path), "reason: warn\n")

    def test_write_that_fails_says_so_and_ends_its_thread_quietly(self):
        write(os.path.join(self.root, "captures"), "")      # not a directory
        with contextlib.redirect_stderr(io.StringIO()) as said, \
                mock.patch.object(threading, "excepthook") as crashed:
            writer = gpuwatchdog.write_behind(self.path, "reason: warn\n")
            if writer:
                writer.join(30)
        crashed.assert_not_called()
        self.assertIn(f"capture {self.path} failed: ", said.getvalue())


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
