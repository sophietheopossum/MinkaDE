#!/usr/bin/env python3
"""Exact function starts for stripped libxul from .eh_frame_hdr's sorted table (survives stripping)."""
import re, struct, subprocess, bisect
LIB = "/opt/hellfire/libxul.so"
blob = open(LIB, "rb").read()
sec = {}
for ln in subprocess.run(["readelf", "-SW", LIB], capture_output=True, text=True).stdout.splitlines():
    m = re.match(r'\s*\[\s*\d+\]\s+(\S+)\s+\S+\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)', ln)
    if m: sec[m.group(1)] = (int(m.group(2), 16), int(m.group(3), 16), int(m.group(4), 16))
hv, ho, _ = sec[".eh_frame_hdr"]
cnt = struct.unpack_from("<I", blob, ho + 8)[0]          # fde_count, udata4 (enc 0x03)
starts = sorted(struct.unpack_from("<i", blob, ho + 12 + i * 8)[0] + hv for i in range(cnt))
def func_start(a):
    i = bisect.bisect_right(starts, a) - 1
    return starts[i] if i >= 0 else None
