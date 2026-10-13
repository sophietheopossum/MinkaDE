"""Tests for gpumemwatch.py: the fd sweep, the bounded log, the report, the daemon.

    python3 tools/test_gpumemwatch.py

Nothing here touches the machine it runs on. The sampler sees a /proc and /sys
built under a temp dir and a clock that only counts, its signal handlers are
recorded instead of installed, and its default log path points into the temp
dir too. So no test can append to ~/.cache/gpumemwatch/gpumem.ndjson, which the
live gpumemwatch.service is writing while these run.

The fd walk runs where it was asked for, not on a thread, so that a run is the
same every time; self.stalled makes it stand still instead, as it does when a
GPU driver holds it. Only the Threaded tests start a real one.

The expected failure is a defect this found in the script, not a flaky test:
its comment says what is wrong. Fixing the script turns it into an "unexpected
success", which is the cue to delete the decorator.
"""

import contextlib
import errno
import io
import json
import os
import signal
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gpumemwatch  # noqa: E402

GIB = 1024 * 1024  # in KiB, the unit of /proc/meminfo and of the log
MIB = 1024 * 1024  # in bytes, the unit of a dma-buf's size

BROWSER, COMPOSITOR, OTHER = 5000001, 5000002, 5000003

_listdir = os.listdir
_readlink = os.readlink


def read(path):
    with open(path) as handle:
        return handle.read()


def write(path, text):
    with open(path, "w") as handle:
        handle.write(text)


# Each of these is one open fd: (what /proc/<pid>/fd/<n> links to, its fdinfo).

def card(client, gtt="0", shared="0", system="0"):
    """An xe DRM fd, as the kernel prints it."""
    return ("/dev/dri/renderD128",
            "pos:\t0\nflags:\t02100002\nmnt_id:\t28\nino:\t186\n"
            f"drm-driver:\txe\ndrm-client-id:\t{client}\ndrm-pdev:\t0000:00:02.0\n"
            f"drm-total-system:\t{system}\ndrm-shared-system:\t0\n"
            f"drm-total-gtt:\t{gtt}\ndrm-shared-gtt:\t{shared}\n"
            f"drm-active-gtt:\t0\ndrm-resident-gtt:\t{gtt}\n")


def nvidia():
    """The proprietary render node: a DRM fd with no drm-* accounting."""
    return ("/dev/dri/renderD129",
            "pos:\t0\nflags:\t02100002\nmnt_id:\t28\nino:\t221\n")


def dmabuf(ino, mib):
    return ("/dmabuf:",
            f"pos:\t0\nflags:\t02000002\nmnt_id:\t14\nino:\t{ino}\n"
            f"size:\t{mib * MIB}\ncount:\t8\nexp_name:\tdrm\n")


def plain():
    return ("/home/seirra/notes.txt", "pos:\t0\nflags:\t0100000\nmnt_id:\t31\nino:\t77\n")


class Clock:
    """Stands in for the sampler's `time`: a sleep only advances a counter."""

    def __init__(self):
        self.start = self.now = 100000.0
        self.due = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        if seconds < 0:
            raise ValueError("sleep length must be non-negative")
        self.now += seconds
        while self.due and self.due[0][0] <= self.now:
            self.due.pop(0)[1]()
        if self.now - self.start > 86400:
            raise AssertionError("the sampler ran for a day without stopping")

    def strftime(self, fmt):
        return time.strftime(fmt, time.gmtime(self.now))


class SamplerCase(unittest.TestCase):
    """A sampler wired to a fake machine: /proc, /sys, clock and signals."""

    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        self.log_path = os.path.join(self.root, "log", "gpumem.ndjson")
        os.makedirs(f"{self.root}/proc")
        os.makedirs(f"{self.root}/sys/block")
        self.clock = Clock()
        self.denied = set()     # directories that answer EACCES
        self.handlers = {}      # signal number -> handler the sampler asked for
        self.stalled = False    # whether a walk that begins now stands still
        self.out = []           # the walkers whose walk has not come back
        self.meminfo(gpu=1.2, avail=6.5)
        write(f"{self.root}/proc/vmstat", "nr_free_pages 825317\nnr_zspages 510515\n")
        self.zram("zram0", "7348269056 2044176021 2091069440 0 2396168192 24569")

        def patch(*args, **kwargs):
            return self.enterContext(mock.patch.object(*args, **kwargs))

        patch(gpumemwatch, "open", self._open, create=True)
        patch(gpumemwatch, "time", self.clock)
        patch(gpumemwatch, "DEFAULT_LOG",
              os.path.join(self.root, "default", "gpumem.ndjson"))
        patch(os, "listdir", self._listdir)
        patch(os, "readlink", lambda path: _readlink(self._path(path)))
        patch(signal, "signal", self.handlers.__setitem__)
        if self.inline_walks:
            patch(gpumemwatch.Walker, "_spawn", lambda walker: self._spawn(walker))

    inline_walks = True

    def _spawn(self, walker):
        if self.stalled:
            self.out.append(walker)
        else:
            walker._walk()

    def release(self):
        """The driver lets go: every walk that stood still comes back."""
        self.stalled = False
        while self.out:
            self.out.pop()._walk()

    def _path(self, path):
        if isinstance(path, str) and path.startswith(("/proc/", "/sys/")):
            return self.root + path
        return self.root + path if path in ("/proc", "/sys") else path

    def _open(self, path, *args, **kwargs):
        return open(self._path(path), *args, **kwargs)

    def _listdir(self, path="."):
        if path in self.denied:
            raise PermissionError(errno.EACCES, "Permission denied", path)
        return _listdir(self._path(path))

    def meminfo(self, gpu, avail):
        """Set GPUActive and MemAvailable, in GiB, among lines the sampler
        should ignore."""
        write(f"{self.root}/proc/meminfo",
              f"MemTotal:       16053412 kB\nMemFree:         3304688 kB\n"
              f"MemAvailable:   {int(avail * GIB)} kB\nBuffers:             144 kB\n"
              f"Cached:          3012468 kB\nSwapCached:       612340 kB\n"
              f"SwapTotal:      24803832 kB\nSwapFree:       12056152 kB\n"
              f"Shmem:            271936 kB\nSReclaimable:     776804 kB\n"
              f"SUnreclaim:       433932 kB\nGPUActive:      {int(gpu * GIB)} kB\n"
              f"GPUReclaim:         2136 kB\n")

    def zram(self, name, mm_stat, disksize="8219262976"):
        os.makedirs(f"{self.root}/sys/block/{name}")
        write(f"{self.root}/sys/block/{name}/mm_stat", mm_stat + "\n")
        write(f"{self.root}/sys/block/{name}/disksize", disksize + "\n")

    def spawn(self, pid, comm, *fds):
        """A process holding the given fds, numbered from 3."""
        proc = f"{self.root}/proc/{pid}"
        os.makedirs(f"{proc}/fd")
        os.makedirs(f"{proc}/fdinfo")
        write(f"{proc}/comm", comm + "\n")
        for number, (target, fdinfo) in enumerate(fds, start=3):
            os.symlink(target, f"{proc}/fd/{number}")
            write(f"{proc}/fdinfo/{number}", fdinfo)

    def at(self, seconds, event):
        """Call `event` once the run is `seconds` old."""
        self.clock.due.append((self.clock.now + seconds, event))
        self.clock.due.sort(key=lambda due: due[0])

    def main(self, *args):
        """Run the sampler, logging into the temp dir, and return what it
        printed. Its exit status is left in self.status."""
        argv = ["gpumemwatch", "--log", self.log_path, *args]
        with mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.status = gpumemwatch.main()
        return out.getvalue()

    def records(self, path=None):
        return [json.loads(line) for line in read(path or self.log_path).splitlines()]

    def samples(self):
        return [record for record in self.records() if "mem" in record]

    def desktop(self):
        """The browser, the compositor and an NVIDIA client, sharing one buffer."""
        self.spawn(BROWSER, "floorp", plain(),
                   card(28, gtt="307660 KiB", shared="30 MiB"),
                   dmabuf(900, 10), dmabuf(901, 4))
        self.spawn(COMPOSITOR, "shoji_wm", card(7, gtt="672 MiB", shared="351200 KiB"),
                   dmabuf(900, 10))
        self.spawn(OTHER, "MinkaFX", nvidia())


class Parsing(SamplerCase):
    def test_kib(self):
        for text, kib in (("963320 KiB", 963320), ("672 MiB", 688128),
                          ("2 GiB", 2097152), ("0", 0), ("8192", 8),
                          ("", None), ("unknown", None)):
            self.assertEqual(gpumemwatch._kib(text), kib, text)

    def test_meminfo_keeps_its_own_keys(self):
        mem = gpumemwatch.read_meminfo()
        self.assertEqual(set(mem), set(gpumemwatch.MEMINFO_KEYS))
        self.assertEqual((mem["GPUActive"], mem["Cached"]), (1258291, 3012468))

        os.remove(f"{self.root}/proc/meminfo")
        self.assertEqual(gpumemwatch.read_meminfo(), {})

    def test_zram_reports_the_ram_the_store_really_uses(self):
        self.zram("zram1", "garbage")
        self.assertEqual(gpumemwatch.read_zram(), {"zram0": {
            "disksize_kib": 8026624, "orig_kib": 7176044,
            "compr_kib": 1996265, "used_kib": 2042060}})


class Sweep(SamplerCase):
    def test_client_is_logged_in_kib_without_its_zeros(self):
        self.spawn(BROWSER, "floorp", plain(),
                   card(28, gtt="307660 KiB", shared="30 MiB", system="6128 KiB"))
        clients, dbuf = gpumemwatch.scan_fds(False)
        self.assertEqual(clients, [{
            "pid": BROWSER, "comm": "floorp", "drv": "xe", "pdev": "0000:00:02.0",
            "cid": "28", "tsys": 6128, "tgtt": 307660, "sgtt": 30720, "rgtt": 307660}])
        self.assertEqual(dbuf, {})

    def test_client_open_on_several_fds_counts_once(self):
        fd = card(7, gtt="799704 KiB")
        self.spawn(COMPOSITOR, "shoji_wm", fd, fd, fd, fd, fd)
        clients, _ = gpumemwatch.scan_fds(False)
        self.assertEqual([client["tgtt"] for client in clients], [799704])

    def test_node_without_accounting_is_still_listed(self):
        self.spawn(OTHER, "MinkaFX", nvidia(), nvidia())
        clients, _ = gpumemwatch.scan_fds(False)
        self.assertEqual(clients, [{
            "pid": OTHER, "comm": "MinkaFX", "drv": None, "node": "renderD129",
            "cid": None, "no_accounting": True}])

    def test_process_of_another_user_is_skipped(self):
        self.spawn(BROWSER, "floorp", card(28, gtt="307660 KiB"))
        self.spawn(OTHER, "sddm-greeter", card(3, gtt="4096 KiB"))
        self.denied.add(f"/proc/{OTHER}/fd")
        clients, _ = gpumemwatch.scan_fds(True)
        self.assertEqual([client["comm"] for client in clients], ["floorp"])

    def test_buffer_shared_between_processes_counts_once_system_wide(self):
        self.desktop()
        _, dbuf = gpumemwatch.scan_fds(True)
        self.assertEqual((dbuf["n_unique"], dbuf["kib"]), (2, 14 * 1024))
        self.assertEqual(dbuf["by_comm"],
                         {"floorp": [14 * 1024, 2], "shoji_wm": [10 * 1024, 1]})

    # Defect: by_comm adds a buffer's size once per fd, and a process keeps
    # several fds to one dma-buf. On 13/10/2026 shoji_wm held 144 fds on 38
    # buffers, so the log gave it 1403.8 MiB while the same record put every
    # dma-buf in the system at 433.0 MiB.
    @unittest.expectedFailure
    def test_buffer_on_several_fds_is_charged_to_its_process_once(self):
        buffer = dmabuf(900, 10)
        self.spawn(COMPOSITOR, "shoji_wm", buffer, buffer, buffer, buffer)
        _, dbuf = gpumemwatch.scan_fds(True)
        self.assertEqual(dbuf["kib"], 10 * 1024)
        self.assertEqual(dbuf["by_comm"]["shoji_wm"][0], 10 * 1024)

    def test_sample_lists_the_biggest_client_first(self):
        self.desktop()
        record = gpumemwatch.sample(True)
        self.assertEqual([client["comm"] for client in record["drm"]],
                         ["shoji_wm", "floorp", "MinkaFX"])
        self.assertEqual(record["zspages_kib"], 2042060)
        self.assertEqual(record["zram"]["zram0"]["used_kib"], 2042060)
        self.assertEqual(record["mem"]["MemAvailable"], 6815744)
        self.assertEqual(json.loads(json.dumps(record)), record)

    # The figures are read before the walk and used to be stamped after it.
    # On 13/10/2026 a walk stood still for 110 s, and the row marked 05:43:07
    # held the memory of 05:41:17.
    def test_sample_is_stamped_when_its_memory_was_read(self):
        def slow_walk(*_):
            self.clock.sleep(90)
            self.meminfo(gpu=6.9, avail=2.0)
            return [], {}

        with mock.patch.object(gpumemwatch, "scan_fds", slow_walk):
            record = gpumemwatch.sample(True)
        self.assertEqual(record["mono"], self.clock.start)
        self.assertEqual(record["mem"]["GPUActive"], int(1.2 * GIB))
        self.assertEqual(record["walk_ms"], 90000)

    def test_once_names_every_client_and_brackets_the_truth(self):
        self.desktop()
        text = self.main("--once")
        self.assertEqual(self.status, 0)
        self.assertRegex(text, r"shoji_wm +\[5000002\] cid=7 +xe +total +672\.0 MiB")
        self.assertRegex(text, r"floorp +\[5000001\] cid=28 +xe +total +300\.4 MiB")
        self.assertRegex(text, r"MinkaFX +\[5000003\] renderD129 +\(driver exports no")
        # 672 MiB + 307660 KiB, and the same less 351200 KiB + 30 MiB shared.
        self.assertRegex(text, r"TOTAL of clients +0\.950 GiB")
        self.assertRegex(text, r"PRIVATE only +0\.585 GiB")
        self.assertRegex(text, r"kernel GPUActive +1\.200 GiB")
        self.assertIn("dma-buf: 2 unique buffers, 14.0 MiB", text)
        self.assertFalse(os.path.exists(self.log_path))


class LogFile(SamplerCase):
    def log(self, cap):
        log = gpumemwatch.Log(self.log_path, cap, 0)
        self.addCleanup(log.close)
        return log

    def test_appends_one_compact_line_per_record(self):
        os.makedirs(os.path.dirname(self.log_path))
        write(self.log_path, '{"n":0}\n')
        self.log(cap=1 << 20).write({"n": 1, "mem": {"GPUActive": 5}})
        self.assertEqual(read(self.log_path),
                         '{"n":0}\n{"n":1,"mem":{"GPUActive":5}}\n')

    def test_cap_keeps_two_generations_and_drops_nothing_between_them(self):
        log = self.log(cap=1000)
        for n in range(500):
            log.write({"n": n, "pad": "x" * 40})
        directory = os.path.dirname(self.log_path)
        self.assertEqual(sorted(_listdir(directory)),
                         ["gpumem.ndjson", "gpumem.ndjson.1"])
        longest = len(json.dumps({"n": 499, "pad": "x" * 40})) + 1
        for name in _listdir(directory):
            self.assertLessEqual(os.path.getsize(os.path.join(directory, name)),
                                 1000 + longest)
        kept = [record["n"] for record in
                self.records(self.log_path + ".1") + self.records()]
        self.assertEqual(kept, list(range(kept[0], 500)))


class Report(SamplerCase):
    def sample(self, t, gpu, *cids):
        return json.dumps({
            "t": t, "mono": 1.0, "mem": {"GPUActive": int(gpu * GIB)}, "zram": {},
            "drm": [{"pid": 1, "comm": "floorp", "drv": "xe", "cid": str(cid),
                     "tgtt": 4096} for cid in cids]}) + "\n"

    def report(self, older, newer):
        os.makedirs(os.path.dirname(self.log_path))
        if older is not None:
            write(self.log_path + ".1", older)
        write(self.log_path, newer)
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err, \
                mock.patch.object(sys, "argv", ["gpumemwatch", "--report", self.log_path]):
            self.status = gpumemwatch.main()
        return out.getvalue() + err.getvalue()

    def test_report_spans_both_generations_and_shows_the_peak(self):
        text = self.report(
            self.sample("2026-10-09T16:28:10+0100", 1.5, 7)
            + self.sample("2026-10-11T03:06:45+0100", 6.5, 7, 8),
            self.sample("2026-10-13T01:50:54+0100", 2.0, 7, 8, 9))
        self.assertEqual(self.status, 0)
        self.assertIn("3 samples, 2026-10-09T16:28:10+0100 .. "
                      "2026-10-13T01:50:54+0100", text)
        self.assertRegex(text, r"2026-10-11T03:06:45\+0100 +GPUActive +6\.500 GiB")
        self.assertIn("no gaps", text)

    def test_report_skips_events_and_a_torn_line(self):
        text = self.report(None, (
            '{"t":"2026-10-10T07:09:25+0100","ev":"start","pid":1476,"argv":[]}\n'
            + self.sample("2026-10-10T07:09:25+0100", 1.5, 7)
            + '{"t":"2026-10-10T07:09:40+0100","mono":12.5,"mem":{"GPUAc\n'
            + self.sample("2026-10-10T07:09:55+0100", 1.6, 7)
            + '{"t":"2026-10-10T07:10:10+0100","ev":"exit","samples":2}\n'))
        self.assertIn("2 samples, ", text)

    def test_report_names_client_ids_it_never_sampled(self):
        text = self.report(None, self.sample("2026-10-10T07:09:25+0100", 1.5, 7, 9, 12))
        self.assertIn("saw 3 of ids 7..12", text)
        self.assertIn("NEVER SAMPLED: 3 client id(s) [8, 10, 11]", text)

    # In a storm the highest reading is likely to be one whose walk was out.
    def test_report_of_a_peak_without_attribution_shows_the_next_that_has_it(self):
        blind = json.dumps({
            "t": "2026-10-13T05:42:09+0100", "mono": 2.0,
            "mem": {"GPUActive": int(8.9 * GIB)}, "zram": {},
            "walk": {"late_s": 52.0, "pid": 1529, "comm": "shoji_wm", "fd": 41,
                     "state": "D", "wchan": "ww_mutex_lock"}}) + "\n"
        text = self.report(None, self.sample("2026-10-13T05:41:02+0100", 6.8, 7)
                           + blind)
        self.assertEqual(self.status, 0)
        self.assertRegex(text, r"05:42:09\+0100 +GPUActive +8\.900 GiB")
        self.assertIn("no attribution: the fd walk had been out for 52.0 s, "
                      "reading shoji_wm[1529] fd 41, thread D in ww_mutex_lock", text)
        self.assertIn("highest sample that has attribution:", text)
        self.assertRegex(text, r"05:41:02\+0100 +GPUActive +6\.800 GiB")

    def test_report_of_a_log_without_samples_fails(self):
        text = self.report(None, '{"t":"2026-10-10T07:09:25+0100","ev":"start"}\n')
        self.assertEqual(self.status, 1)
        self.assertIn("no samples", text)


class Daemon(SamplerCase):
    def setUp(self):
        super().setUp()
        self.desktop()

    def kinds(self):
        return [record.get("ev", "sample") for record in self.records()]

    def gaps(self):
        monos = [record["mono"] for record in self.samples()]
        return [round(later - earlier, 2) for earlier, later in zip(monos, monos[1:])]

    def test_count_bounds_the_run(self):
        self.main("--count", "3")
        self.assertEqual(self.status, 0)
        self.assertEqual(self.kinds(), ["start", "sample", "sample", "sample", "exit"])
        self.assertEqual(self.records()[-1]["samples"], 3)
        self.assertFalse(os.path.exists(gpumemwatch.DEFAULT_LOG))

    def test_dma_buf_sweep_is_opt_in(self):
        self.main("--count", "1")
        self.assertNotIn("dmabuf", self.samples()[0])
        self.main("--count", "1", "--dmabuf")
        self.assertEqual(self.samples()[-1]["dmabuf"]["n_unique"], 2)

    def test_samples_every_15_s_while_memory_is_comfortable(self):
        self.main("--count", "4")
        self.assertEqual(self.gaps(), [15, 15, 15])

    def test_samples_every_2_s_under_pressure(self):
        self.at(20, lambda: self.meminfo(gpu=6.5, avail=2.4))
        self.main("--count", "5")
        self.assertEqual(self.gaps(), [15, 15, 2, 2])

    def test_sigterm_ends_the_run_cleanly_within_half_a_second(self):
        signalled = []

        def sigterm():
            signalled.append(self.clock.now)
            self.handlers[signal.SIGTERM](signal.SIGTERM, None)

        self.at(20, sigterm)
        self.main()
        self.assertEqual(self.status, 0)
        self.assertEqual(self.kinds(), ["start", "sample", "sample", "exit"])
        self.assertLessEqual(self.clock.now - signalled[0], 0.5)

    # What 13/10/2026 looked like: the driver held the walk for 80 s. The
    # memory figures are the ones that must not stop.
    def test_stalled_walk_does_not_stop_the_sampling(self):
        self.at(20, lambda: setattr(self, "stalled", True))
        self.at(110, self.release)
        self.main("--count", "10", "--walk-wait", "0")
        rows = self.samples()
        ticks = [row for row in rows if "walk_mono" not in row]
        self.assertEqual([round(row["mono"] - self.clock.start) for row in ticks],
                         [0, 15, 30, 45, 60, 75, 90, 105, 120])
        blind = [row for row in ticks if "drm" not in row]
        self.assertEqual([row["walk"]["late_s"] for row in blind],
                         [0, 15, 30, 45, 60, 75])
        # ...and the walk is logged when it comes back, not a sample later,
        # with when it began and what it cost.
        (back,) = [row for row in rows if "walk_mono" in row]
        self.assertEqual(round(back["mono"] - self.clock.start), 110)
        self.assertEqual(back["walk_mono"], self.clock.start + 30)
        self.assertEqual(back["walk_ms"], 80000)
        self.assertEqual([client["comm"] for client in back["drm"]],
                         ["shoji_wm", "floorp", "MinkaFX"])
        self.assertEqual(self.records()[-1]["samples"], len(rows))

    def test_walk_that_is_back_in_time_is_logged_with_its_own_sample(self):
        self.main("--count", "2")
        for row in self.samples():
            self.assertEqual((row["walk_ms"], "walk" in row, "walk_mono" in row),
                             (0, False, False))

    def test_walk_that_fails_ends_the_run_as_it_did(self):
        with mock.patch.object(gpumemwatch, "scan_fds", side_effect=ValueError("torn")), \
                self.assertRaisesRegex(ValueError, "torn"):
            self.main("--count", "2")
        self.assertEqual(self.kinds(), ["start", "exit"])

    def test_full_disk_stops_the_logging_and_says_why(self):
        nearly_full = types.SimpleNamespace(f_bavail=1000, f_frsize=4096)
        with mock.patch.object(os, "statvfs", return_value=nearly_full):
            self.main()
        self.assertEqual(self.status, 0)
        self.assertEqual(self.kinds(), ["start", "sample", "stop", "exit"])
        self.assertIn("--min-free-mib", self.records()[2]["why"])


class Threaded(SamplerCase):
    """The walk on the thread it really runs on, held where a driver holds
    it: inside the read of one fdinfo."""

    inline_walks = False

    def test_walk_held_in_a_read_is_not_waited_out(self):
        self.desktop()
        held = f"/proc/{COMPOSITOR}/fdinfo/3"
        reached, let_go = threading.Event(), threading.Event()
        opener = self._open

        def holding(path, *args, **kwargs):
            if path == held:
                reached.set()
                let_go.wait(30)
            return opener(path, *args, **kwargs)

        walker = gpumemwatch.Walker(True)
        with mock.patch.object(gpumemwatch, "open", holding):
            walker.start()
            self.assertTrue(reached.wait(30))
            began = time.monotonic()
            self.assertIsNone(walker.take(0.05))
            self.assertLess(time.monotonic() - began, 10)

            # What a row logged meanwhile says: the fd, and the thread's state.
            task = f"{self.root}/proc/self/task/{walker.tid}"
            os.makedirs(task)
            write(f"{task}/stat", f"{walker.tid} (walk) D 1 1 1\n")
            write(f"{task}/wchan", "ww_mutex_lock")
            self.assertEqual(walker.where(), {
                "late_s": 0.0, "pid": COMPOSITOR, "comm": "shoji_wm", "fd": 3,
                "state": "D", "wchan": "ww_mutex_lock"})
            self.assertNotEqual(walker.tid, threading.get_native_id())

            let_go.set()
            # Inside the patches: a walk that outlived them would read the
            # real /proc.
            walk = walker.take(30)
        if walk is None:
            self.fail("the walk never came back")
        self.assertEqual(sorted(client["comm"] for client in walk[2]),
                         ["MinkaFX", "floorp", "shoji_wm"])
        self.assertFalse(walker.out)


if __name__ == "__main__":
    unittest.main()
