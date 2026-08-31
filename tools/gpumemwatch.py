#!/usr/bin/env python3
"""gpumemwatch - per-DRM-client GPU memory sampler for MinkaDE.

Records, to an append-only NDJSON log:
  * every DRM client's drm-total-* / drm-resident-* / drm-shared-* (from
    /proc/<pid>/fdinfo/<fd>, deduplicated by drm-client-id),
  * GPUActive / GPUReclaim and the rest of the RAM picture from /proc/meminfo,
  * the zram compressed store's real RAM footprint (/sys/block/zram*/mm_stat),
    because on this machine zram is priority-100 swap: swapping harder makes
    the RAM shortage worse, and "free swap" does not mean free RAM,
  * optionally the deduplicated dma-buf footprint held by each process.

Why it exists: the kernel prints gpu_active ONLY inside an OOM dump, and the
OOM task table has no GPU column at all, so after an OOM-kill storm the GPU
memory is unattributable - the fdinfo that would have named the culprit dies
with the process. This samples that fdinfo continuously so the next occurrence
is attributable.

Read-only. No root. No signals to other processes. Bounded on disk.

  gpumemwatch.py --once                 one sample, pretty, to stdout
  gpumemwatch.py                        daemon; default log ~/.cache/gpumemwatch/
  gpumemwatch.py --report LOG           summarise a log: peak sample + top clients
"""

import argparse
import json
import os
import signal
import sys
import time

DEFAULT_LOG = os.path.expanduser("~/.cache/gpumemwatch/gpumem.ndjson")

MEMINFO_KEYS = (
    "MemTotal", "MemFree", "MemAvailable", "Cached", "Shmem",
    "SwapTotal", "SwapFree", "SReclaimable", "SUnreclaim",
    "GPUActive", "GPUReclaim",
)
# fdinfo keys worth keeping, mapped to short names for a compact log line.
DRM_FIELDS = {
    "drm-total-system": "tsys", "drm-shared-system": "ssys",
    "drm-resident-system": "rsys", "drm-purgeable-system": "psys",
    "drm-active-system": "asys",
    "drm-total-gtt": "tgtt", "drm-shared-gtt": "sgtt",
    "drm-resident-gtt": "rgtt", "drm-purgeable-gtt": "pgtt",
    "drm-active-gtt": "agtt",
    "drm-total-vram0": "tvram", "drm-shared-vram0": "svram",
    "drm-resident-vram0": "rvram", "drm-active-vram0": "avram",
    "drm-total-stolen": "tstol", "drm-resident-stolen": "rstol",
}
_UNIT = {"KiB": 1, "MiB": 1024, "GiB": 1024 * 1024, "B": 0}


def _kib(value):
    """'963320 KiB' / '0' -> KiB as int, or None."""
    parts = value.split()
    if not parts:
        return None
    try:
        n = int(parts[0])
    except ValueError:
        return None
    if len(parts) == 1:
        return n if n == 0 else n // 1024      # bare number is bytes
    return n * _UNIT.get(parts[1], 1)


def read_meminfo():
    out = {}
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                k, _, rest = line.partition(":")
                if k in MEMINFO_KEYS:
                    out[k] = int(rest.split()[0])
    except OSError:
        pass
    return out


def read_vmstat():
    out = {}
    try:
        with open("/proc/vmstat") as fh:
            for line in fh:
                k, _, v = line.partition(" ")
                if k in ("nr_zspages", "nr_gpu_active", "nr_gpu_reclaim",
                         "nr_slab_unreclaimable", "nr_free_pages"):
                    out[k] = int(v)
    except OSError:
        pass
    return out


def read_zram():
    """Real RAM cost of the compressed swap store, per zram device."""
    devs = {}
    try:
        entries = sorted(d for d in os.listdir("/sys/block") if d.startswith("zram"))
    except OSError:
        return devs
    for d in entries:
        try:
            with open(f"/sys/block/{d}/mm_stat") as fh:
                f = fh.read().split()
            with open(f"/sys/block/{d}/disksize") as fh:
                disksize = int(fh.read().strip())
        except (OSError, ValueError):
            continue
        if len(f) < 3:
            continue
        devs[d] = {
            "disksize_kib": disksize // 1024,
            "orig_kib": int(f[0]) // 1024,       # uncompressed bytes stored
            "compr_kib": int(f[1]) // 1024,      # compressed bytes
            "used_kib": int(f[2]) // 1024,       # RAM actually consumed  <-- the one that matters
        }
    return devs


def _comm(pid):
    try:
        with open(f"/proc/{pid}/comm") as fh:
            return fh.read().strip()
    except OSError:
        return "?"


def scan_fds(want_dmabuf):
    """One pass over /proc/*/fd. Returns (drm_clients, dmabuf_summary).

    DRM clients are deduplicated by (pdev, drm-client-id) so a process holding
    several fds to the same device - shoji_wm holds 5 - is counted once.
    dma-bufs are deduplicated by inode, so a buffer shared between the browser
    and the compositor is counted once system-wide.
    """
    clients = {}
    dmabuf_ino = {}          # ino -> size bytes
    dmabuf_by_pid = {}       # pid -> [bytes, n]
    try:
        pids = [e for e in os.listdir("/proc") if e[0].isdigit()]
    except OSError:
        return [], {}
    for pid in pids:
        fddir = f"/proc/{pid}/fd"
        try:
            fds = os.listdir(fddir)
        except OSError:
            continue                     # kernel thread, gone, or not ours
        comm = None
        for fd in fds:
            try:
                target = os.readlink(f"{fddir}/{fd}")
            except OSError:
                continue
            is_drm = target.startswith("/dev/dri/")
            is_dma = want_dmabuf and target.startswith("/dmabuf")
            if not (is_drm or is_dma):
                continue
            try:
                with open(f"/proc/{pid}/fdinfo/{fd}") as fh:
                    body = fh.read()
            except OSError:
                continue
            kv = {}
            for line in body.splitlines():
                k, _, v = line.partition(":")
                kv[k.strip()] = v.strip()
            if comm is None:
                comm = _comm(pid)
            if is_dma:
                try:
                    ino = int(kv.get("ino", "-1"))
                    size = int(kv.get("size", "0"))
                except ValueError:
                    continue
                dmabuf_ino[ino] = size
                slot = dmabuf_by_pid.setdefault(comm, [0, 0])
                slot[0] += size
                slot[1] += 1
                continue
            drv = kv.get("drm-driver")
            if drv is None:
                # e.g. the NVIDIA proprietary render node: no drm-* accounting.
                key = ("nofdinfo", target, pid)
                clients.setdefault(key, {
                    "pid": int(pid), "comm": comm, "drv": None,
                    "node": os.path.basename(target), "cid": None,
                    "no_accounting": True,
                })
                continue
            key = (kv.get("drm-pdev", "?"), kv.get("drm-client-id", f"p{pid}f{fd}"))
            if key in clients:
                continue                 # dup fd of the same DRM client
            rec = {
                "pid": int(pid), "comm": comm, "drv": drv,
                "pdev": kv.get("drm-pdev"), "cid": kv.get("drm-client-id"),
            }
            for long, short in DRM_FIELDS.items():
                if long in kv:
                    n = _kib(kv[long])
                    if n:                # omit zeros to keep lines small
                        rec[short] = n
            clients[key] = rec
    dsum = {
        "n_unique": len(dmabuf_ino),
        "kib": sum(dmabuf_ino.values()) // 1024,
        "by_comm": {c: [b // 1024, n] for c, (b, n) in
                    sorted(dmabuf_by_pid.items(), key=lambda x: -x[1][0])[:12]},
    } if want_dmabuf else {}
    return list(clients.values()), dsum


def sample(want_dmabuf):
    mem = read_meminfo()
    vm = read_vmstat()
    clients, dbuf = scan_fds(want_dmabuf)
    clients.sort(key=lambda c: -(c.get("tgtt", 0) + c.get("tsys", 0) + c.get("tvram", 0)))
    rec = {
        "t": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mono": round(time.monotonic(), 2),
        "mem": mem,
        "zspages_kib": vm.get("nr_zspages", 0) * 4,
        "zram": read_zram(),
        "drm": clients,
    }
    if want_dmabuf:
        rec["dmabuf"] = dbuf
    return rec


def fmt(rec):
    m = rec["mem"]
    g = m.get("GPUActive", 0)
    zr = sum(d["used_kib"] for d in rec["zram"].values())
    out = [
        f"{rec['t']}  GPUActive {g/1048576:6.3f} GiB   GPUReclaim "
        f"{m.get('GPUReclaim',0)/1048576:.3f} GiB   MemAvail "
        f"{m.get('MemAvailable',0)/1048576:6.3f} GiB   "
        f"zram-store {zr/1048576:.3f} GiB   SwapFree {m.get('SwapFree',0)/1048576:.2f} GiB"
    ]
    acc = 0
    for c in rec["drm"]:
        if c.get("no_accounting"):
            out.append(f"    {c['comm']:<16} [{c['pid']:>7}] {c['node']:<12} "
                       f"(driver exports no drm-* accounting)")
            continue
        tot = c.get("tgtt", 0) + c.get("tsys", 0) + c.get("tvram", 0)
        acc += tot
        out.append(
            f"    {c['comm']:<16} [{c['pid']:>7}] cid={str(c.get('cid')):<5} "
            f"{c.get('drv'):<6} total {tot/1024:9.1f} MiB   "
            f"shared {(c.get('sgtt',0)+c.get('ssys',0))/1024:8.1f} MiB   "
            f"resident {(c.get('rgtt',0)+c.get('rsys',0))/1024:9.1f} MiB")
    priv = sum((c.get("tgtt", 0) + c.get("tsys", 0) + c.get("tvram", 0))
               - (c.get("sgtt", 0) + c.get("ssys", 0) + c.get("svram", 0))
               for c in rec["drm"] if not c.get("no_accounting"))
    out.append(f"    {'TOTAL of clients':<20} {acc/1048576:7.3f} GiB  "
               f"(shared buffers counted once PER CLIENT - an over-count)")
    out.append(f"    {'PRIVATE only':<20} {priv/1048576:7.3f} GiB  "
               f"(total minus shared - an under-count; the truth is between)")
    out.append(f"    {'kernel GPUActive':<20} {g/1048576:7.3f} GiB  "
               f"(ground truth for the whole system)")
    if rec.get("dmabuf"):
        d = rec["dmabuf"]
        out.append(f"    dma-buf: {d['n_unique']} unique buffers, "
                   f"{d['kib']/1024:.1f} MiB deduplicated by inode")
    return "\n".join(out)


class Log:
    """Append-only NDJSON with a hard size cap: <cap> live + <cap> in .1."""

    def __init__(self, path, cap_bytes, min_free_bytes):
        self.path = path
        self.cap = cap_bytes
        self.min_free = min_free_bytes
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.fh = open(path, "a", buffering=1)
        self.n = 0

    def free_bytes(self):
        st = os.statvfs(os.path.dirname(self.path) or ".")
        return st.f_bavail * st.f_frsize

    def write(self, rec):
        self.fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        self.fh.flush()
        self.n += 1
        if self.n % 12 == 0:
            os.fsync(self.fh.fileno())
        if self.fh.tell() >= self.cap:
            self.fh.close()
            try:
                os.replace(self.path, self.path + ".1")
            except OSError:
                pass
            self.fh = open(self.path, "a", buffering=1)

    def close(self):
        try:
            os.fsync(self.fh.fileno())
        except OSError:
            pass
        self.fh.close()


def report(path):
    peak = None
    n = 0
    first = last = None
    seen_cids = set()
    for p in (path + ".1", path):          # oldest generation first
        try:
            fh = open(p)
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if "mem" not in r:
                    continue
                n += 1
                first = first or r["t"]
                last = r["t"]
                g = r["mem"].get("GPUActive", 0)
                if peak is None or g > peak["mem"].get("GPUActive", 0):
                    peak = r
                for c in r.get("drm", ()):
                    if c.get("cid") is not None:
                        try:
                            seen_cids.add(int(c["cid"]))
                        except (TypeError, ValueError):
                            pass
    if peak is None:
        print(f"no samples in {path}", file=sys.stderr)
        return 1
    print(f"{n} samples, {first} .. {last}\npeak GPUActive sample:\n")
    print(fmt(peak))
    if seen_cids:
        lo, hi = min(seen_cids), max(seen_cids)
        missed = sorted(set(range(lo, hi + 1)) - seen_cids)
        print(f"\ndrm-client-id coverage: saw {len(seen_cids)} of ids {lo}..{hi}")
        if missed:
            print(f"  NEVER SAMPLED: {len(missed)} client id(s) {missed[:40]}"
                  f"{' ...' if len(missed) > 40 else ''}")
            print("  Each gap is a DRM client that opened and closed between two")
            print("  samples, or one on a driver with no fdinfo accounting (the")
            print("  NVIDIA node). Its memory was never attributed - shorten")
            print("  --interval if the suspect is short-lived. drm-client-id is a")
            print("  global monotonic counter, so a gap is a real client.")
        else:
            print("  no gaps: every DRM client alive in this window was sampled")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--interval", type=float, default=15.0,
                    help="seconds between samples when memory is comfortable")
    ap.add_argument("--fast-interval", type=float, default=2.0,
                    help="seconds between samples once pressure is detected")
    ap.add_argument("--pressure-gib", type=float, default=2.5,
                    help="MemAvailable below this GiB switches to fast sampling")
    ap.add_argument("--cap-mib", type=float, default=32.0,
                    help="size cap per log generation; 2 generations are kept")
    ap.add_argument("--min-free-mib", type=float, default=512.0,
                    help="stop logging if the filesystem drops below this")
    ap.add_argument("--dmabuf", action="store_true",
                    help="also sweep dma-buf fds (heavier: touches every fd)")
    ap.add_argument("--count", type=int, default=0, help="stop after N samples")
    ap.add_argument("--once", action="store_true", help="one sample to stdout")
    ap.add_argument("--report", metavar="LOG", help="summarise an existing log")
    a = ap.parse_args()

    if a.report:
        return report(a.report)
    if a.once:
        print(fmt(sample(True)))
        return 0

    log = Log(a.log, int(a.cap_mib * 1048576), int(a.min_free_mib * 1048576))
    stop = []
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, lambda *_: stop.append(1))
    log.write({"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "ev": "start",
               "pid": os.getpid(), "argv": sys.argv[1:]})
    n = 0
    thresh = a.pressure_gib * 1048576
    try:
        while not stop:
            rec = sample(a.dmabuf)
            log.write(rec)
            n += 1
            if a.count and n >= a.count:
                break
            if log.free_bytes() < log.min_free:
                log.write({"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                           "ev": "stop", "why": "filesystem below --min-free-mib"})
                break
            avail = rec["mem"].get("MemAvailable", 1 << 30)
            delay = a.fast_interval if avail < thresh else a.interval
            end = time.monotonic() + delay
            while not stop and time.monotonic() < end:
                time.sleep(min(0.5, end - time.monotonic()))
    finally:
        log.write({"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "ev": "exit",
                   "samples": n})
        log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
