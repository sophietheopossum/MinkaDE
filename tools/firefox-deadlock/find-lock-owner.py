#!/usr/bin/env python3
"""Name the owner of the mutex wedging Firefox's storage (QuotaManager) threads.

READ-ONLY: reads /proc only, writes nothing, signals nothing, does not stop the
browser. Needs root solely because the parent is non-dumpable, so
/proc/<tid>/syscall and /proc/<pid>/mem are gated by ptrace_may_access().

Method: a thread blocked in pthread_mutex_lock sits in futex(uaddr, ...) where
uaddr == &mutex.__data.__lock; glibc keeps __data.__owner 8 bytes further in
(set on lock, zeroed on unlock), so 16 bytes at uaddr name the holding TID.
Threads sharing a CONDVAR also group by address but show __owner=0 -- those
are not contended locks.
"""
import os, struct, subprocess, sys

NR_FUTEX = 202  # x86_64
pid = int(sys.argv[1]) if len(sys.argv) > 1 else int(
    subprocess.run(["pgrep", "-xo", "hellfire"], capture_output=True, text=True).stdout.split()[0])

def comm(t):
    try: return open(f"/proc/{pid}/task/{t}/comm").read().strip()
    except Exception: return "(gone)"

def futex(t):
    try: parts = open(f"/proc/{pid}/task/{t}/syscall").read().split()
    except Exception as e: return None, f"unreadable ({getattr(e, 'strerror', e)})"
    if not parts: return None, "empty (exited mid-scan)"
    try: return (int(parts[1], 16), None) if int(parts[0]) == NR_FUTEX else (None, f"syscall {parts[0]}")
    except (ValueError, IndexError): return None, parts[0]

def mutex(addr):
    try:
        with open(f"/proc/{pid}/mem", "rb", 0) as m:
            m.seek(addr); d = m.read(16)
        return struct.unpack("<iiiI", d) if len(d) == 16 else None
    except Exception: return None

tids = sorted(int(t) for t in os.listdir(f"/proc/{pid}/task"))
print(f"hellfire pid {pid}: {len(tids)} threads\n", flush=True)
print("--- storage threads ---", flush=True)
for t in tids:
    c = comm(t)
    if not (c in ("IPDL Background", "QuotaManager IO", "LS Thread") or c.startswith("Indexed")): continue
    a, why = futex(t)
    if a is None: print(f"  {t} {c}: {why}", flush=True); continue
    mx = mutex(a); line = f"  {t} {c}: futex 0x{a:x}"
    if mx:
        lock, count, owner, nusers = mx
        line += f" __lock={lock} __owner={owner}" + (f" -> HELD BY {owner} ({comm(owner)})" if owner else "")
    print(line, flush=True)
print("\n--- contended addresses (>1 waiter) ---", flush=True)
groups = {}
for t in tids:
    a, _ = futex(t)
    if a: groups.setdefault(a, []).append(t)
for a, ts in sorted(groups.items()):
    if len(ts) < 2: continue
    mx = mutex(a); o = mx[2] if mx else "?"
    print(f"  0x{a:x}: {', '.join(f'{t} ({comm(t)})' for t in ts)}  owner={o}" + (f" ({comm(o)})" if isinstance(o, int) and o else ""), flush=True)
