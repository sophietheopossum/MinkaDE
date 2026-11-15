#!/usr/bin/env python3
"""Name stripped libxul frames by the string literals their function (and its callees) reference.

OFFSETs are libxul-relative, as eu-stack prints them after a frame's build-id
("[615805be...]@0x7f823c600000+0x7bb7801"), and name the right code only in that
build: the first line printed is the build-id of the library they were read against.
"""
import argparse
import bisect
import importlib.util
import os
import re
import subprocess
from dataclasses import dataclass, field
from types import ModuleType
from typing import NamedTuple

FLOORP_LIBXUL = "/usr/lib/floorp/libxul.so"

STRING_SCAN = 200     # bytes searched for a literal's NUL: a longer literal is dropped
MIN_STRING = 5        # a shorter printable run is as likely to be stray bytes as text
# A frame's block should read at a glance, and each callee followed costs an objdump run.
FRAME_STRINGS = 8
CALLS_FOLLOWED = 12   # call instructions, repeats included, not distinct callees
CALLEE_STRINGS = 2
GUESSED_SIZE = 0x200  # the last function has no next start to end it

BUILD_ID = re.compile(r'Build ID: ([0-9a-f]+)')
LOAD = re.compile(r'\s+LOAD\s+(?P<offset>0x[0-9a-f]+)\s+(?P<vaddr>0x[0-9a-f]+)'
                  r'\s+\S+\s+(?P<filesz>0x[0-9a-f]+)')
# objdump follows a RIP-relative operand with its target: "lea -0x13d5(%rip),%rax  # 6b3 <...>"
OPERAND_TARGET = re.compile(r'#\s*([0-9a-f]+)')
CALL = re.compile(r'\bcall\s+([0-9a-f]+)')


def import_sibling(name: str) -> ModuleType:
    """NAME.py from this script's directory, which `python3 -I` leaves off sys.path. Run through
    a symlink, the link's directory comes first and then the real file's."""
    script = os.path.abspath(__file__)
    path = os.path.join(os.path.dirname(script), f"{name}.py")
    if not os.path.isfile(path):
        path = os.path.join(os.path.dirname(os.path.realpath(script)), f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fde = import_sibling("fde")


class Segment(NamedTuple):
    """A PT_LOAD segment: where its bytes are in memory and in the file."""
    vaddr: int
    offset: int
    filesz: int  # not MemSiz: past FileSiz (.bss) there are no bytes to read a literal from


class Harvest(NamedTuple):
    """What a function's code references. Tuples, as every frame reaching it shares one."""
    strings: tuple[str, ...]  # each literal once, in order of first use
    calls: tuple[int, ...]    # every call instruction's target, in order


@dataclass
class Library:
    path: str
    blob: bytes
    starts: list[int]  # every function's first byte, sorted
    segments: list[Segment]
    harvested: dict[int, Harvest] = field(default_factory=dict)  # by function start


def offset(text: str) -> int:
    # argparse names its error after this function: "invalid offset value".
    return int(text, 16)


def run(*command: str) -> subprocess.CompletedProcess[str]:
    """COMMAND's result, its output captured as text. A failure does not raise."""
    return subprocess.run(command, capture_output=True, text=True)


def build_id(lib: str) -> str:
    match = BUILD_ID.search(run("readelf", "-n", lib).stdout)
    return match.group(1) if match else "(none)"


def load_segments(lib: str) -> list[Segment]:
    segments = []
    for line in run("readelf", "-lW", lib).stdout.splitlines():
        match = LOAD.match(line)
        if match:
            segments.append(Segment(vaddr=int(match.group("vaddr"), 16),
                                    offset=int(match.group("offset"), 16),
                                    filesz=int(match.group("filesz"), 16)))
    return segments


def file_offset(segments: list[Segment], vaddr: int) -> int | None:
    for segment in segments:
        if segment.vaddr <= vaddr < segment.vaddr + segment.filesz:
            return segment.offset + (vaddr - segment.vaddr)
    return None


def string_at(blob: bytes, segments: list[Segment], vaddr: int) -> str | None:
    """The NUL-terminated printable ASCII text at VADDR, if that is what is there."""
    start = file_offset(segments, vaddr)
    if start is None:
        return None
    end = blob.find(b"\0", start, start + STRING_SCAN)
    if end < 0 or end - start < MIN_STRING:
        return None
    raw = blob[start:end]
    if not all(0x20 <= byte < 0x7f for byte in raw):
        return None
    return raw.decode("ascii")


def function_end(starts: list[int], start: int) -> int:
    following = bisect.bisect_right(starts, start)
    return starts[following] if following < len(starts) else start + GUESSED_SIZE


def harvest(library: Library, start: int) -> Harvest:
    """The strings and call targets of the function at START. It goes through objdump once
    per run, as a callee is often reached from many frames."""
    if start in library.harvested:
        return library.harvested[start]
    objdump = run("objdump", "-d", "--no-show-raw-insn", f"--start-address=0x{start:x}",
                  f"--stop-address=0x{function_end(library.starts, start):x}", library.path)
    strings: list[str] = []
    calls: list[int] = []
    for line in objdump.stdout.splitlines():
        for match in OPERAND_TARGET.finditer(line):
            text = string_at(library.blob, library.segments, int(match.group(1), 16))
            if text is not None and text not in strings:
                strings.append(text)
        call = CALL.search(line)
        if call:
            calls.append(int(call.group(1), 16))
    found = Harvest(tuple(strings), tuple(calls))
    # A failed objdump (an OOM kill, say) is not kept, so the next reach runs it again.
    if objdump.returncode == 0:
        library.harvested[start] = found
    return found


def callee_strings(strings: tuple[str, ...]) -> tuple[str, ...]:
    """A callee's first source paths, which name it better than shared assert texts do;
    without any, its first strings."""
    paths = tuple(text for text in strings if "/" in text and (".cpp" in text or ".h" in text))
    return paths[:CALLEE_STRINGS] or strings[:CALLEE_STRINGS]


def print_frame(library: Library, address: int) -> None:
    start = fde.func_start(library.starts, address)
    if start is None:
        print(f"\n=== 0x{address:x} -> below the first function start ===")
        return
    strings, calls = harvest(library, start)
    size = function_end(library.starts, start) - start
    print(f"\n=== 0x{address:x} -> fn 0x{start:x} (+0x{address - start:x}, size 0x{size:x}) ===")
    for text in strings[:FRAME_STRINGS]:
        print(f"    str: {text!r}")
    followed: set[int] = set()
    for target in calls[:CALLS_FOLLOWED]:
        callee = fde.func_start(library.starts, target)
        if callee is None or callee in followed:
            continue
        followed.add(callee)
        for text in callee_strings(harvest(library, callee).strings):
            print(f"    via 0x{callee:x}: {text!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lib", default=FLOORP_LIBXUL,
                        help=f"the libxul the stacks came from (default {FLOORP_LIBXUL}; "
                             "the September hellfire captures need /opt/hellfire/libxul.so)")
    parser.add_argument("offsets", metavar="OFFSET", type=offset, nargs="+",
                        help="hex, 0x optional")
    args = parser.parse_args()
    lib = args.lib
    if not os.path.isfile(lib):
        parser.error(f"{lib}: no such file")
    blob, starts = fde.load(lib)
    print(f"{lib}: build-id {build_id(lib)}")
    library = Library(lib, blob, starts, load_segments(lib))
    for address in args.offsets:
        print_frame(library, address)


if __name__ == "__main__":
    main()
