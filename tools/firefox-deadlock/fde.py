#!/usr/bin/env python3
"""Exact function starts for stripped libxul from .eh_frame_hdr's sorted table (survives stripping)."""
import re, struct, subprocess, bisect
# version 1, eh_frame_ptr pcrel sdata4, fde_count udata4, table datarel sdata4: what LLD
# wrote into both hellfire's and Floorp's libxul, and the only layout load() reads.
HDR = b"\x01\x1b\x03\x3b"
def load(lib):
    """The library's bytes, and the start of every function in it, sorted."""
    blob = open(lib, "rb").read()
    sec = {}
    for ln in subprocess.run(["readelf", "-SW", lib], capture_output=True, text=True).stdout.splitlines():
        m = re.match(r'\s*\[\s*\d+\]\s+(\S+)\s+\S+\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)', ln)
        if m: sec[m.group(1)] = (int(m.group(2), 16), int(m.group(3), 16), int(m.group(4), 16))
    if ".eh_frame_hdr" not in sec: raise SystemExit(f"{lib}: no .eh_frame_hdr")
    hv, ho, _ = sec[".eh_frame_hdr"]
    if blob[ho:ho + 4] != HDR:
        raise SystemExit(f"{lib}: .eh_frame_hdr starts {blob[ho:ho + 4].hex()}, not {HDR.hex()}")
    cnt = struct.unpack_from("<I", blob, ho + 8)[0]
    return blob, sorted(struct.unpack_from("<i", blob, ho + 12 + i * 8)[0] + hv for i in range(cnt))
def func_start(starts, a):
    i = bisect.bisect_right(starts, a) - 1
    return starts[i] if i >= 0 else None
