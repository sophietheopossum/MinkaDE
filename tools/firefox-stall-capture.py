#!/usr/bin/env python3
"""firefox-stall-capture - snapshot a live Firefox painting stall.

Run this the moment the page stops drawing, BEFORE restarting anything. On
21/9/2026 the diagnosis took an hour of live back-and-forth and still could not
be reproduced afterwards; everything that mattered was only observable while
the stall was up. This collects all of it in one pass.

The discriminator worth knowing before you even run it: if the browser CHROME
still paints and only the page and popups are frozen, the fault is in the
wl_subsurface layer (GDK believing the parent toplevel is unmapped), not in the
compositor and not in GPU memory.

Read-only. No root. No signals. Writes one directory under ~/.cache/firefox-stall/.
"""

import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time

OUT_ROOT = pathlib.Path.home() / ".cache" / "firefox-stall"
FF_LOG = pathlib.Path.home() / ".local/state/hellfire-stderr.log"
WM_LOG = pathlib.Path.home() / "shoji_wm/logs/latest.log"


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                              shell=isinstance(cmd, str)).stdout
    except Exception as error:
        return f"({cmd!r} failed: {error})\n"


def pids_of(name):
    return [int(p) for p in run(["pgrep", "-x", name]).split()]


def thread_states(pid):
    """Thread name, state and wchan. A stalled client shows every renderer
    thread parked in futex_wait with nothing queued to wake it."""
    out = []
    try:
        for tid in sorted(os.listdir(f"/proc/{pid}/task")):
            try:
                comm = open(f"/proc/{pid}/task/{tid}/comm").read().strip()
                state = open(f"/proc/{pid}/task/{tid}/stat").read().rpartition(")")[2].split()[0]
                wchan = open(f"/proc/{pid}/task/{tid}/wchan").read().strip() or "-"
                out.append(f"  {tid:>8} {comm:<20} {state} {wchan}")
            except OSError:
                continue
    except OSError as error:
        out.append(f"  (unavailable: {error})")
    return "\n".join(out)


def gpu_totals(pid):
    """Private GTT for one process, deduplicated by DRM client id."""
    total, seen = 0, set()
    try:
        fds = os.listdir(f"/proc/{pid}/fdinfo")
    except OSError:
        return None
    for fd in fds:
        try:
            text = open(f"/proc/{pid}/fdinfo/{fd}").read()
        except OSError:
            continue
        if "drm-client-id:" not in text:
            continue
        fields = {}
        for line in text.splitlines():
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
        cid = fields.get("drm-client-id")
        if cid in seen:
            continue
        seen.add(cid)

        def kib(key):
            raw = fields.get(key, "0").split()
            return int(raw[0]) if raw and raw[0].isdigit() else 0

        total += max(0, kib("drm-total-gtt") - kib("drm-shared-gtt"))
    return total


def compositor_window_state():
    """Ask ShojiWM what it thinks of the window: if it reports the toplevel
    mapped and not minimised while the page is frozen, the compositor is not
    the one that stopped."""
    path = f"{os.environ.get('XDG_RUNTIME_DIR', '/run/user/1000')}/shojiwm-{os.environ.get('WAYLAND_DISPLAY', 'wayland-1')}.sock"
    try:
        sock = socket.socket(socket.AF_UNIX)
        sock.settimeout(3)
        sock.connect(path)
        sock.sendall(b'{"id":1,"method":"workspaces.get"}\n')
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        return buf.split(b"\n")[0].decode("utf-8", "replace")
    except Exception as error:
        return f"(ipc unavailable: {error})"


def main():
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = OUT_ROOT / stamp
    out.mkdir(parents=True, exist_ok=True)

    parents = pids_of("hellfire")
    report = [
        f"captured: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
        f"hellfire pids: {parents}",
        "",
        "REMINDER: note whether the CHROME is still painting. Chrome alive +",
        "page/popups frozen = subsurface mapping fault, not the compositor.",
        "",
    ]

    for pid in parents:
        report.append(f"--- parent {pid} threads ---")
        report.append(thread_states(pid))
        report.append(f"--- parent {pid} private GTT: {gpu_totals(pid)} KiB ---")
        kids = [int(p) for p in run(["pgrep", "-P", str(pid)]).split()]
        # Content processes hang off the forkserver, not the parent.
        grandkids = []
        for kid in kids:
            grandkids += [int(p) for p in run(["pgrep", "-P", str(kid)]).split()]
        for child in kids + grandkids:
            try:
                comm = open(f"/proc/{child}/comm").read().strip()
            except OSError:
                continue
            report.append(f"--- child {child} ({comm}) gtt={gpu_totals(child)} KiB ---")
            report.append(thread_states(child))

    # Queues both ways: data sent but unread means the client stopped
    # dispatching; nothing queued anywhere means it stopped asking.
    report.append("--- unix sockets (hellfire) ---")
    report.append(run("ss -x -e -p 2>/dev/null | grep -E 'hellfire|wayland-1'"))
    report.append("--- compositor view of the windows ---")
    report.append(compositor_window_state())
    report.append("--- meminfo ---")
    report.append(run(["cat", "/proc/meminfo"]))
    for name in ("cpu", "memory", "io"):
        report.append(f"--- pressure {name} ---")
        report.append(run(["cat", f"/proc/pressure/{name}"]))
    report.append("--- recent drm/kernel events ---")
    report.append(run("journalctl -b -k -o short-iso --no-pager --since '-2 hours' 2>/dev/null "
                      "| grep -iE 'drm|xe |reset|coredump' | tail -30"))

    (out / "report.txt").write_text("\n".join(report) + "\n")

    # The two logs, trimmed: the mapping warnings live in Firefox's, the output
    # and window lifecycle in the compositor's.
    for src, dst, lines in ((FF_LOG, "hellfire-stderr.tail", 4000),
                            (WM_LOG, "shoji_wm.tail", 4000)):
        try:
            text = src.read_text(errors="replace").splitlines()[-lines:]
            (out / dst).write_text("\n".join(text) + "\n")
        except OSError as error:
            (out / dst).write_text(f"(unavailable: {error})\n")

    try:
        shutil.copy(pathlib.Path.home() / ".cache/gpumemwatch/gpumem.ndjson",
                    out / "gpumem.ndjson")
    except Exception as error:
        (out / "gpumem.missing").write_text(f"{error}\n")

    # Count only this session: the log persists across restarts and only
    # rotates at launch, so a lifetime count says nothing about now.
    subsurface = run(f"awk '/^===== launched/{{n=0}} "
                     f"/as subsurface because its parent is not mapped/{{n++}} "
                     f"END{{print n+0}}' {FF_LOG} 2>/dev/null").strip()
    print(f"captured to {out}")
    print(f"  subsurface-mapping warnings since this Firefox launched: {subsurface}")
    print("  note whether the chrome is still painting -- that is the discriminator")
    return 0


if __name__ == "__main__":
    sys.exit(main())
