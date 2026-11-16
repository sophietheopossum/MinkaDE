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


def parse(line: str) -> tuple[str, str] | None:
    """An objdump line's mnemonic and operands, or None for a label or any other line."""
    match = INSTRUCTION.match(line)
    if not match:
        return None
    mnemonic, operands = match.group(1), match.group(2).strip()
    while mnemonic in PREFIXES and operands:
        mnemonic, _, operands = operands.partition(" ")
        operands = operands.strip()
    return mnemonic, operands


def written(operands: str) -> str:
    return operands.rsplit(",", 1)[-1]  # AT&T puts the destination last


def leaves_block(mnemonic: str) -> bool:
    return mnemonic.startswith(("j", "call", "ret", "ud2", "hlt", "int3"))


def is_call(mnemonic: str, operands: str, bounds: tuple[int, int]) -> bool:
    """A call, or a jmp out of the function: a tail call made in a call's place."""
    if mnemonic == "call":
        return True
    if mnemonic != "jmp":
        return False
    if operands.startswith("*"):
        return operands.endswith("(%rip)")  # through the GOT; "*%rax" is a switch's table
    target = DIRECT_TARGET.match(operands)
    return bool(target) and not bounds[0] <= int(target.group(1), 16) < bounds[1]


def counts_vector_registers(mnemonic: str, operands: str) -> bool:
    """`xor %eax,%eax` or `mov $n,%al`: a variadic C call (printf and kin) saying how many
    vector registers it passes. Rust has no variadic functions, so a &str call never does."""
    if mnemonic == "xor":
        return operands == "%eax,%eax"
    constant = CONSTANT_INTO.fullmatch(operands)
    return mnemonic == "mov" and constant is not None and constant.group(2) in ("%al", "%eax")


def constant_moved(mnemonic: str, operands: str, register: tuple[str, ...]) -> int | None:
    """The constant a `mov $c,%reg` puts in REGISTER at 64 or 32 bits."""
    match = CONSTANT_INTO.fullmatch(operands)
    if mnemonic == "mov" and match and match.group(2) in register[:2]:
        return int(match.group(1), 16)
    return None


def str_length(lines: list[str], index: int, bounds: tuple[int, int]) -> int | None:
    """The length of the &str whose address the lea at LINES[index] loads: a constant in the
    next argument register, set before the lea or after it, and both reaching one call.
    BOUNDS are the function's, to tell a tail call from a jump inside it."""
    lea = parse(lines[index])
    into = RIP_RELATIVE_INTO.search(lea[1]) if lea and lea[0] == "lea" else None
    registers = NEXT_ARGUMENT.get(into.group(1)) if into else None
    if registers is None:
        return None
    address, length_register = registers
    length = None
    for line in lines[index + 1:index + 1 + PAIR_REACH]:
        instruction = parse(line)
        if instruction is None:
            return None
        mnemonic, operands = instruction
        if is_call(mnemonic, operands, bounds):
            break
        if (leaves_block(mnemonic) or written(operands) in address
                or counts_vector_registers(mnemonic, operands)):
            return None
        if written(operands) in length_register:
            length = constant_moved(mnemonic, operands, length_register)
            if length is None:
                return None
    else:
        return None  # no call within reach
    if length is not None:
        return length
    for line in reversed(lines[max(0, index - PAIR_REACH):index]):
        instruction = parse(line)
        if (instruction is None or leaves_block(instruction[0])
                or counts_vector_registers(*instruction)):
            return None
        if written(instruction[1]) in length_register:
            return constant_moved(*instruction, length_register)
    return None


def read_function(library: Library, lines: list[str], bounds: tuple[int, int]) -> Harvest:
    """The strings and call targets in a function's disassembly, LINES."""
    lengths: dict[int, int] = {}  # by address: the shortest any &str load there passes
    for index, line in enumerate(lines):
        length = str_length(lines, index, bounds)
        target = OPERAND_TARGET.search(line)
        if length is not None and target:
            address = int(target.group(1), 16)
            lengths[address] = min(length, lengths.get(address, length))
    strings: list[str] = []
    calls: list[int] = []
    for line in lines:
        for match in OPERAND_TARGET.finditer(line):
            address = int(match.group(1), 16)
            text = string_at(library.blob, library.segments, address, lengths.get(address))
            if text is not None and text not in strings:
                strings.append(text)
        call = CALL.search(line)
        if call:
            calls.append(int(call.group(1), 16))
    return Harvest(tuple(strings), tuple(calls))


def harvest(library: Library, start: int) -> Harvest:
    """The strings and call targets of the function at START. It goes through objdump once
    per run, as a callee is often reached from many frames."""
    if start in library.harvested:
        return library.harvested[start]
    end = function_end(library.starts, start)
    objdump = run("objdump", "-d", "--no-show-raw-insn", f"--start-address=0x{start:x}",
                  f"--stop-address=0x{end:x}", library.path)
    found = read_function(library, objdump.stdout.splitlines(), (start, end))
    # A failed objdump (an OOM kill, say) is not kept, so the next reach runs it again.
    if objdump.returncode == 0:
        library.harvested[start] = found
    return found


def is_source_path(text: str) -> bool:
    return "/" in text and (".cpp" in text or ".h" in text or text.endswith(".rs"))


def callee_strings(strings: tuple[str, ...]) -> tuple[str, ...]:
    """A callee's first source paths, which name it better than shared assert texts do;
    without any, its first strings."""
    paths = tuple(text for text in strings if is_source_path(text))
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
    followed = {start}  # a recursive call names this frame again
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
