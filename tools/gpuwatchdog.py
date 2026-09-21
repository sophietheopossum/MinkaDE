#!/usr/bin/env python3
"""gpuwatchdog - stop a GPU memory runaway before the kernel picks the victim.

Why this exists: GPU pages are not charged to any memcg and `oom_badness()`
ignores them, so when a client pins GPU memory the kernel kills everything
*except* the culprit. On 11/9/2026 that cost ~40 processes -- including
user@1000's systemd manager, which never came back -- over 6.7 minutes, before
the kernel finally reached the process actually holding 10.4 GiB.

That is also why this runs as a SYSTEM unit: a user unit dies in the same
storm it is supposed to watch.

Design:
  * The trigger is cheap. /proc/meminfo's GPUActive is read every 500 ms; it
    costs one small read and needs no fd sweep.
  * Attribution is expensive (a walk over every process's fdinfo), so it only
    runs once GPUActive is already above the warn threshold.
  * Escalation is staged and each stage must persist -- a burst of GPU memory
    while a video decodes is normal, a plateau above the line is not.

Stages, with the defaults tuned to this machine (16 GB, Intel xe, where a
healthy desktop sits near 2 GB GPUActive and the 17-21/9 stuck-pool period had
a median of 6.3 GB):

  warn      GPUActive >= 4 GiB for 60 s        capture + notify
  critical  GPUActive >= 7 GiB and             capture + notify + SIGTERM
            MemAvailable <= 2 GiB for 15 s     (SIGKILL 10 s later if ignored)

SIGTERM first is deliberate: Firefox writes its session on SIGTERM and restores
the tabs afterwards, so the recoverable signal is tried before the one that
loses work.

Safety rules, in order of importance:
  * Only a process whose comm is in --target (default: hellfire) is ever
    signalled. Anything else -- the compositor, systemd, this watchdog -- is
    never a candidate, whatever it is holding.
  * The target is re-read immediately before signalling and its start time is
    compared with the one observed at escalation, so a recycled PID cannot be
    signalled by mistake.
  * --dry-run does everything except signal. Run it that way first.

Usage:
  gpuwatchdog.py --dry-run            watch and report, never signal
  gpuwatchdog.py --once               print one reading and exit
  gpuwatchdog.py                      arm it (needs root to signal)
"""

import argparse
import os
import signal
import subprocess
import sys
import time

MEMINFO = "/proc/meminfo"
DEFAULT_CAPTURE_DIR = "/var/log/gpuwatchdog"

# fdinfo keys that describe private (unshared) GTT residency for a DRM client.
DRM_TOTAL = "drm-total-gtt:"
DRM_SHARED = "drm-shared-gtt:"
DRM_CLIENT_ID = "drm-client-id:"
DRM_PDEV = "drm-pdev:"


def read_meminfo():
    """GPUActive and MemAvailable in KiB. GPUActive is absent on kernels
    without the xe/i915 accounting patch; treat that as 'no signal'."""
    out = {}
    try:
        with open(MEMINFO) as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                if key in ("GPUActive", "MemAvailable", "MemTotal", "SwapFree"):
                    out[key] = int(rest.split()[0])
    except OSError:
        return {}
    return out


def _comm(pid):
    try:
        with open(f"/proc/{pid}/comm") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _start_time(pid):
    """Field 22 of /proc/<pid>/stat. Pinned alongside the PID so a recycled
    PID is never mistaken for the process we decided to signal."""
    try:
        with open(f"/proc/{pid}/stat") as handle:
            data = handle.read()
    except OSError:
        return None
    # comm can contain spaces and parentheses; everything after the last ')'
    # is positional.
    tail = data.rpartition(")")[2].split()
    return tail[19] if len(tail) > 19 else None


def gpu_clients():
    """Private GTT per process, deduplicated by (pdev, drm-client-id).

    A process holding several fds for one DRM client would otherwise be
    counted once per fd. Shared pages are subtracted so a buffer imported by
    the compositor is not charged twice across the two processes.
    """
    per_pid = {}
    seen = set()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        fdinfo_dir = f"/proc/{pid}/fdinfo"
        try:
            fds = os.listdir(fdinfo_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                with open(f"{fdinfo_dir}/{fd}") as handle:
                    text = handle.read()
            except OSError:
                continue
            if DRM_CLIENT_ID not in text:
                continue
            total = shared = 0
            client = pdev = ""
            for line in text.splitlines():
                if line.startswith(DRM_TOTAL):
                    total = int(line.split()[1])
                elif line.startswith(DRM_SHARED):
                    shared = int(line.split()[1])
                elif line.startswith(DRM_CLIENT_ID):
                    client = line.split()[1]
                elif line.startswith(DRM_PDEV):
                    pdev = line.split()[1]
            key = (pdev, client)
            if key in seen:
                continue
            seen.add(key)
            per_pid[pid] = per_pid.get(pid, 0) + max(0, total - shared)
    return per_pid


def pick_target(targets):
    """The biggest private-GTT holder whose comm is in the allowlist."""
    best = None
    for pid, kib in gpu_clients().items():
        comm = _comm(pid)
        if comm not in targets:
            continue
        if best is None or kib > best[1]:
            best = (pid, kib, comm)
    return best


def capture(capture_dir, reason, reading, target, dry_run):
    """Snapshot everything that dies with the process: thread states, wchan,
    its DRM fdinfo, and the system's memory and pressure picture."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(capture_dir, f"gpuwatchdog-{stamp}.txt")
    lines = [
        f"reason: {reason}",
        f"when: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
        f"dry-run: {dry_run}",
        f"reading: {reading}",
        f"target: {target}",
        "",
    ]
    for name, cmd in (
        ("meminfo", ["cat", MEMINFO]),
        ("pressure-memory", ["cat", "/proc/pressure/memory"]),
        ("pressure-io", ["cat", "/proc/pressure/io"]),
    ):
        try:
            lines.append(f"--- {name} ---")
            lines.append(subprocess.run(cmd, capture_output=True, text=True,
                                        timeout=5).stdout)
        except Exception as error:  # a capture must never break the watchdog
            lines.append(f"({name} failed: {error})")

    if target:
        pid = target[0]
        lines.append(f"--- process {pid} ---")
        for name in ("status", "wchan", "stack"):
            try:
                with open(f"/proc/{pid}/{name}") as handle:
                    lines.append(f"[{name}]\n{handle.read()}")
            except OSError as error:
                lines.append(f"[{name}] unavailable: {error}")
        lines.append("[threads] tid state wchan")
        try:
            for tid in sorted(os.listdir(f"/proc/{pid}/task")):
                try:
                    with open(f"/proc/{pid}/task/{tid}/stat") as handle:
                        state = handle.read().rpartition(")")[2].split()[0]
                    with open(f"/proc/{pid}/task/{tid}/wchan") as handle:
                        wchan = handle.read().strip() or "-"
                    lines.append(f"  {tid} {state} {wchan}")
                except OSError:
                    continue
        except OSError as error:
            lines.append(f"  (unavailable: {error})")

    lines.append("--- gpu clients (private GTT, KiB) ---")
    for pid, kib in sorted(gpu_clients().items(), key=lambda kv: -kv[1])[:12]:
        lines.append(f"  {pid:>8} {_comm(pid):<20} {kib:>10}")

    try:
        os.makedirs(capture_dir, exist_ok=True)
        with open(path, "w") as handle:
            handle.write("\n".join(lines) + "\n")
        return path
    except OSError as error:
        print(f"capture failed: {error}", file=sys.stderr, flush=True)
        return None


def notify(uid, summary, body):
    """Raise a desktop notification in the user's session. Best effort: the
    watchdog's job does not depend on the user being logged in."""
    bus = f"/run/user/{uid}/bus"
    if not os.path.exists(bus):
        return
    try:
        subprocess.run(
            ["notify-send", "-u", "critical", "-a", "gpuwatchdog", summary, body],
            env={
                "DBUS_SESSION_BUS_ADDRESS": f"unix:path={bus}",
                "PATH": "/usr/bin:/bin",
                "HOME": f"/home/{uid}",
            },
            user=uid if os.geteuid() == 0 else None,
            timeout=5,
            capture_output=True,
        )
    except Exception:
        pass


def log(message):
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=0.5,
                    help="seconds between GPUActive reads (default 0.5)")
    ap.add_argument("--warn-gib", type=float, default=4.0)
    ap.add_argument("--warn-hold", type=float, default=60.0,
                    help="seconds above --warn-gib before warning")
    ap.add_argument("--crit-gib", type=float, default=7.0)
    ap.add_argument("--crit-avail-gib", type=float, default=2.0,
                    help="MemAvailable must also be at or below this")
    ap.add_argument("--crit-hold", type=float, default=15.0,
                    help="seconds above the critical line before acting")
    ap.add_argument("--term-grace", type=float, default=10.0,
                    help="seconds to wait for SIGTERM to work before SIGKILL")
    ap.add_argument("--cooldown", type=float, default=300.0,
                    help="seconds after acting before acting again")
    ap.add_argument("--target", action="append", default=None,
                    help="comm allowed to be signalled (repeatable)")
    ap.add_argument("--heartbeat", type=float, default=1800.0,
                    help="seconds between liveness lines; 0 disables. A watchdog "
                         "that is silent when healthy cannot be told from a dead "
                         "one, and the line doubles as a GPUActive trace")
    ap.add_argument("--uid", type=int, default=1000, help="uid to notify")
    ap.add_argument("--capture-dir", default=DEFAULT_CAPTURE_DIR)
    ap.add_argument("--dry-run", action="store_true",
                    help="never signal; report what would have happened")
    ap.add_argument("--once", action="store_true",
                    help="print one reading and exit")
    args = ap.parse_args()

    targets = set(args.target or ["hellfire"])
    warn_kib = args.warn_gib * 1024 * 1024
    crit_kib = args.crit_gib * 1024 * 1024
    crit_avail_kib = args.crit_avail_gib * 1024 * 1024

    if args.once:
        mem = read_meminfo()
        gpu = mem.get("GPUActive")
        print(f"GPUActive: {'absent' if gpu is None else f'{gpu / 1048576:.2f} GiB'}")
        print(f"MemAvailable: {mem.get('MemAvailable', 0) / 1048576:.2f} GiB")
        best = pick_target(targets)
        print(f"largest target: {best if best else 'none'}")
        return 0

    if not args.dry_run and os.geteuid() != 0:
        log("note: not root -- it can only signal processes this user owns, "
            "which is enough for a rehearsal but not for the service")

    # A watchdog that cannot see any DRM client cannot attribute a runaway to
    # anyone, and would reach the critical stage only to find no target. That
    # is a silent failure worth one loud line at startup: the usual cause is a
    # missing CAP_SYS_PTRACE, because fdinfo is gated by ptrace_may_access().
    visible = gpu_clients()
    if not visible:
        log("WARNING: no DRM clients visible -- cannot attribute GPU memory to "
            "any process. If running as a service, CAP_SYS_PTRACE is missing.")
    else:
        log(f"attribution ok: {len(visible)} DRM clients visible")

    log(f"armed: warn {args.warn_gib} GiB/{args.warn_hold}s, "
        f"critical {args.crit_gib} GiB + avail<={args.crit_avail_gib} GiB/{args.crit_hold}s, "
        f"targets={sorted(targets)}, dry_run={args.dry_run}")

    above_warn_since = None
    above_crit_since = None
    warned = False
    acted_at = 0.0
    last_beat = 0.0

    while True:
        time.sleep(args.interval)
        now = time.monotonic()
        mem = read_meminfo()
        gpu = mem.get("GPUActive")
        if gpu is None:
            continue
        avail = mem.get("MemAvailable", 0)

        if args.heartbeat and now - last_beat >= args.heartbeat:
            last_beat = now
            best = pick_target(targets)
            held = f"{best[1] / 1048576:.2f} GiB by {best[2]}" if best else "none"
            log(f"alive: GPUActive={gpu / 1048576:.2f} GiB "
                f"MemAvailable={avail / 1048576:.2f} GiB held={held}")

        if gpu < warn_kib:
            # One reading below the line clears the state: the runaway we care
            # about is a plateau, not a spike.
            above_warn_since = above_crit_since = None
            warned = False
            continue

        above_warn_since = above_warn_since or now
        reading = (f"GPUActive={gpu / 1048576:.2f} GiB "
                   f"MemAvailable={avail / 1048576:.2f} GiB")

        if not warned and now - above_warn_since >= args.warn_hold:
            warned = True
            target = pick_target(targets)
            path = capture(args.capture_dir, "warn", reading, target, args.dry_run)
            log(f"WARN {reading} target={target} capture={path}")
            notify(args.uid, "GPU memory climbing",
                   f"{reading}\nHolding: {target[2] if target else 'unknown'}")

        if gpu >= crit_kib and avail <= crit_avail_kib:
            above_crit_since = above_crit_since or now
        else:
            above_crit_since = None
            continue

        if now - above_crit_since < args.crit_hold:
            continue
        if now - acted_at < args.cooldown:
            continue

        target = pick_target(targets)
        path = capture(args.capture_dir, "critical", reading, target, args.dry_run)
        if not target:
            log(f"CRITICAL {reading} but no target in {sorted(targets)} holds GPU "
                f"memory; not signalling. capture={path}")
            acted_at = now
            continue

        pid, kib, comm = target
        started = _start_time(pid)
        log(f"CRITICAL {reading} target={comm}({pid}) holding "
            f"{kib / 1048576:.2f} GiB capture={path}")
        notify(args.uid, "GPU runaway: restarting " + comm,
               f"{reading}\n{comm} holds {kib / 1048576:.1f} GiB. "
               f"Sending SIGTERM so the session is saved.")

        if args.dry_run:
            log(f"dry-run: would SIGTERM {pid}")
            acted_at = now
            continue

        # Re-read identity immediately before signalling: between the scan and
        # here the process may have exited and the PID been reused.
        if _comm(pid) != comm or _start_time(pid) != started:
            log(f"target {pid} changed identity before signalling; standing down")
            acted_at = now
            continue

        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as error:
            log(f"SIGTERM {pid} failed: {error}")
            acted_at = now
            continue

        deadline = time.monotonic() + args.term_grace
        while time.monotonic() < deadline:
            time.sleep(0.5)
            if _start_time(pid) is None:
                break
        if _start_time(pid) is not None and _comm(pid) == comm:
            log(f"{comm}({pid}) ignored SIGTERM for {args.term_grace}s; SIGKILL")
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError as error:
                log(f"SIGKILL {pid} failed: {error}")
        else:
            log(f"{comm}({pid}) exited on SIGTERM")

        acted_at = time.monotonic()
        above_warn_since = above_crit_since = None
        warned = False


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
