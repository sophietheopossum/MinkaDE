#!/usr/bin/env python3
"""Name stripped libxul frames by the string literals their function (and its callees) reference."""
import re, subprocess, sys, bisect
sys.path.insert(0, __import__("os").path.dirname(__file__))
from fde import func_start, starts, blob
LIB = "/opt/hellfire/libxul.so"
loads = []
for ln in subprocess.run(["readelf", "-lW", LIB], capture_output=True, text=True).stdout.splitlines():
    m = re.match(r'\s+LOAD\s+(0x[0-9a-f]+)\s+(0x[0-9a-f]+)\s+\S+\s+(0x[0-9a-f]+)', ln)
    if m: loads.append((int(m.group(2), 16), int(m.group(1), 16), int(m.group(3), 16)))
def v2f(v):
    for va, off, sz in loads:
        if va <= v < va + sz: return off + (v - va)
def s_at(v):
    f = v2f(v)
    if f is None: return None
    e = blob.find(b"\0", f, f + 200)
    if e < 0 or e - f < 5: return None
    try: t = blob[f:e].decode("ascii")
    except UnicodeDecodeError: return None
    return t if all(32 <= ord(c) < 127 for c in t) else None
def fend(a):
    i = bisect.bisect_right(starts, a); return starts[i] if i < len(starts) else a + 0x200
def harvest(fn):
    out = subprocess.run(["objdump", "-d", "--no-show-raw-insn", f"--start-address=0x{fn:x}",
                          f"--stop-address=0x{fend(fn):x}", LIB], capture_output=True, text=True).stdout
    strs, calls = [], []
    for ln in out.splitlines():
        for m in re.finditer(r'#\s*([0-9a-f]+)', ln):
            s = s_at(int(m.group(1), 16))
            if s and s not in strs: strs.append(s)
        m = re.search(r'\bcall\s+([0-9a-f]+)', ln)
        if m: calls.append(int(m.group(1), 16))
    return strs, calls
for a in sys.argv[1:]:
    addr = int(a, 16); fn = func_start(addr); strs, calls = harvest(fn)
    print(f"\n=== 0x{addr:x} -> fn 0x{fn:x} (+0x{addr-fn:x}, size 0x{fend(fn)-fn:x}) ===")
    for s in strs[:8]: print(f"    str: {s!r}")
    seen = set()
    for c in calls[:12]:
        cf = func_start(c)
        if cf is None or cf in seen: continue
        seen.add(cf); cs, _ = harvest(cf)
        pick = [x for x in cs if "/" in x and (".cpp" in x or ".h" in x)][:2] or cs[:2]
        for s in pick: print(f"    via 0x{cf:x}: {s!r}")
