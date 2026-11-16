"""Tests for resolve.py (and fde.py under it): naming frames of a stripped library.

    python3 tools/firefox-deadlock/test_resolve.py

Nothing here reads a real libxul. Every run passes --lib, naming a small library
compiled here from FIXTURE and then stripped, as libxul is; the one that names no
library asks only for --help, which exits before a library is opened. Every
address a test passes comes from the unstripped build's symbols; resolve.py only
ever sees the stripped copy.

resolve.py is run as she runs it, as a command with offsets on the line, and each
test checks the exact lines printed.

FIXTURE is C, built with gcc and the system linker, so these tests are x86-64
only and skip where gcc or binutils is missing.
"""

import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "resolve.py")

BUILD_ID = "0123456789abcdef0123456789abcdef01234567"
LONGEST = "L" * 199   # string_at looks for the NUL in 200 bytes: this one's is the last
TOO_LONG = "M" * 200  # and this one's is one past them
CALLEES = 13          # one more than resolve.py follows

# A function's string literals are what resolve.py prints, so each function
# here stores its own in `sink`, or passes it on as Rust does: that is one
# RIP-relative lea per literal, whose target objdump prints after a "#", as it
# does all through libxul.
FIXTURE = r"""
const char *volatile sink;

/* A stripped libxul still exports symbols, and objdump prints every address
   relative to the nearest one ("call 1461 <anchor+0x1>"). In a library with no
   symbol at all it prints "0x1461" instead, which resolve.py reads as 0. */
__attribute__((visibility("default"))) void anchor(void) {}

void paths(void) {
    sink = "MOZ_RELEASE_ASSERT(isSome())";                 /* not a path */
    sink = "./../../../dom/quota/ActorsParent.cpp";
    sink = "ActorsParent.cpp";                             /* no directory */
    sink = "/home/plz/firebuild/dom/quota/DirectoryLockImpl.h";
    sink = "./../../../dom/quota/QuotaManagerService.cpp"; /* a third path */
}

void plain(void) {
    sink = "MOZ_CRASH(Bad state!)";
    sink = "UnknownErr in %s";
    sink = "moz-extension:";
}

void silent(void) { sink = "abc"; }

void caller(void) {
    sink = "OpenClientDirectoryImpl";
    paths();
    plain();
    silent();
    paths();
}

/* One text at two addresses. A literal stored twice is a single lea, whose
   result gcc keeps in a register for the second store. */
static const char twice[] = "twice", again[] = "twice";

void filtered(void) {
    sink = "abcd";                /* too short */
    sink = "abcde";
    sink = twice;
    sink = "caf\xc3\xa9 au lait";  /* not ASCII */
    sink = "tab\there";           /* a control character */
    sink = "@TOO_LONG@";
    sink = "@LONGEST@";
    sink = again;                 /* the same text again */
}

void crowded(void) {
    sink = "string 1"; sink = "string 2"; sink = "string 3";
    sink = "string 4"; sink = "string 5"; sink = "string 6";
    sink = "string 7"; sink = "string 8"; sink = "string 9";
}

void recursive(void) {
    sink = "recursion";
    if (sink[0] == 'x')
        recursive();
    plain();
}

/* Rust packs its string literals end to end, with no NUL between them, and passes
   a &str as its address in one argument register and its length in the next:
   here `take`'s two. Each run's leading NUL stands for whatever came before it. */
#define FIRST "first literal"
#define SECOND "second literal"
#define RUST_PATH "servo/components/style/gecko/wrapper.rs"
#define NUL_FREE "a literal far from any NUL"
#define LEN(text) (sizeof(text) - 1)
static const char run[] = "\0" FIRST SECOND RUST_PATH;
static const char long_run[] = "\0" NUL_FREE "@FILLER@";
/* A C string, which `report` gets with a line number after it, not a length. */
static const char c_path[] = "\0./../../../dom/quota/Short.cpp";

__attribute__((noipa)) void take(const char *text, unsigned long length) { sink = text + length; }
__attribute__((noipa)) void report(const char *file, int line) { sink = file + line; }

/* One address into `run` per function: gcc would compute it once and copy it into
   the argument register, a move resolve.py does not follow. */
void rusty(void) {
    take(run + 1 + LEN(FIRST), LEN(SECOND));  /* inside the run */
    take(long_run + 1, LEN(NUL_FREE));        /* no NUL in reach */
}

void rust_path(void) {
    sink = "wrapper.rs";     /* no directory */
    sink = "servo/x.rsync";  /* .rs not at the end */
    take(run + 1 + LEN(FIRST) + LEN(SECOND), LEN(RUST_PATH));  /* the run's last literal */
}

void cpp_report(void) { report(c_path + 1, 9); }

/* A C++ name the linker stored inside a longer one, with a count of 5 arguments. */
static const char shared_tail[] = "\0OffscreenCanvasRenderingContext2D.arc";
void canvas_method(void) { report(shared_tail + 1 + LEN("Offscreen"), 5); }

/* Instruction orders gcc does not produce on request, so in asm. None of it runs.
   %0 is "second literal"'s offset in `run`, %1 its length. */
#define SHAPE(name, code) void name(void) { __asm__ volatile(code \
    :: "i"(1 + LEN(FIRST)), "i"(LEN(SECOND)) : "rax", "rdi", "rsi", "memory"); }
#define SECOND_AT "lea run+%c0(%%rip),%%rdi\n\t"
#define SECOND_LENGTH "mov %1,%%esi\n\t"
SHAPE(length_after, SECOND_AT SECOND_LENGTH "call take")
/* "addr32 call", as LLD writes a GOT call it relaxes; as drops a written prefix. */
SHAPE(prefixed_call, SECOND_AT SECOND_LENGTH ".byte 0x67\n\tcall take")
SHAPE(tail_call, SECOND_LENGTH SECOND_AT "jmp take")
SHAPE(loaded_twice, "lea run+%c0(%%rip),%%rax\n\tmov %%rax,sink(%%rip)\n\t"
                    SECOND_LENGTH SECOND_AT "call take")
SHAPE(address_replaced, SECOND_AT SECOND_LENGTH "mov %%rax,%%rdi\n\tcall take")
SHAPE(branch_between, SECOND_LENGTH SECOND_AT "jmp 1f\n1:\n\tcall take")
SHAPE(call_too_far, SECOND_AT SECOND_LENGTH "nop\n\tnop\n\tnop\n\tnop\n\tnop\n\tcall take")

/* A printf-style call: %al holds a count of vector registers, %esi an argument. */
__attribute__((used)) static const char c_format[] = "\0    0x%08x (Unknown1)\n";
SHAPE(variadic, "lea c_format+1(%%rip),%%rdi\n\tmov $8,%%esi\n\txor %%eax,%%eax\n\tcall report")

void uses_rust_path(void) {
    sink = "calls rust_path";
    rust_path();
}

@CALLEES@

void fan(void) {
@CALLS@
}

void last(void) { sink = "the last function"; }
"""

FIXTURE = (FIXTURE
           .replace("@TOO_LONG@", TOO_LONG)
           .replace("@LONGEST@", LONGEST)
           .replace("@FILLER@", "x" * 300)
           .replace("@CALLEES@", "\n".join(
               f'void callee{i:02}(void) {{ sink = "callee {i:02}"; }}' for i in range(CALLEES)))
           .replace("@CALLS@", "\n".join(f"    callee{i:02}();" for i in range(CALLEES))))

CFLAGS = [
    "-O1",
    "-fno-inline",                  # every function keeps its own body and FDE
    "-fno-optimize-sibling-calls",  # a call stays a call: resolve.py ignores jmp
    "-fno-toplevel-reorder",        # functions in source order, so `last` is last
    "-fvisibility=hidden",          # direct calls, not through the PLT, as inside libxul
    "-fasynchronous-unwind-tables", # an FDE per function: what fde.py reads
    "-fPIC", "-shared",
    "-nostdlib",                    # no crt objects: these functions and nothing else
    f"-Wl,--build-id=0x{BUILD_ID}", # a build-id to expect on resolve.py's first line
]

# The functions defined in FIXTURE, by name.
FUNCTIONS = (["anchor", "paths", "plain", "silent", "caller", "filtered", "crowded",
              "recursive", "take", "report", "rusty", "rust_path", "cpp_report",
              "canvas_method", "length_after", "prefixed_call", "tail_call", "loaded_twice",
              "address_replaced", "branch_between", "call_too_far", "variadic", "uses_rust_path"]
             + [f"callee{i:02}" for i in range(CALLEES)] + ["fan", "last"])


def run(*cmd):
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} exited {done.returncode}:\n{done.stderr}")
    return done.stdout


@unittest.skipUnless(platform.machine() == "x86_64", "the fixture is x86-64 code")
@unittest.skipUnless(all(shutil.which(t) for t in ("gcc", "readelf", "objdump", "strip", "nm")),
                     "needs gcc and binutils")
class Resolve(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory(prefix="test_resolve.")
        cls.addClassCleanup(tmp.cleanup)
        cls.tmp = tmp.name
        source = os.path.join(tmp.name, "fixture.c")
        built = os.path.join(tmp.name, "libfixture.so")
        cls.lib = os.path.join(tmp.name, "libfixture-stripped.so")
        with open(source, "w") as handle:
            handle.write(FIXTURE)
        run("gcc", *CFLAGS, "-o", built, source)
        run("strip", "-o", cls.lib, built)

        # The tests mean something only if resolve.py sees what it sees in
        # libxul: no symbol table, a few exported names, an .eh_frame_hdr.
        cls.sections = run("readelf", "-SW", cls.lib)
        if ".symtab" in cls.sections or ".eh_frame_hdr" not in cls.sections:
            raise AssertionError(f"the fixture is not stripped like libxul:\n{cls.sections}")
        exported = run("nm", "-D", "--defined-only", cls.lib).split()
        if exported[-1:] != ["anchor"] or len(exported) != 3:
            raise AssertionError(f"the fixture should export only anchor: {exported}")

        symbols = {}
        for line in run("nm", "--defined-only", built).splitlines():
            address, _, name = line.split()
            symbols[name] = int(address, 16)
        cls.at = {name: symbols[name] for name in FUNCTIONS}
        order = sorted(cls.at.values())
        cls.next = dict(zip(order, order[1:]))  # function start -> the next one's

    @staticmethod
    def run_resolve(*args):
        return subprocess.run(
            [sys.executable, "-I", "-B", SCRIPT, *args],
            capture_output=True, text=True, timeout=120,
            env=dict(os.environ, LC_ALL="C", NO_COLOR="1"))

    def resolve(self, *addresses):
        """resolve.py's output for these addresses in the fixture, without its
        blank lines or the first, which names the library."""
        return self.resolve_args(*(f"0x{a:x}" for a in addresses))

    def resolve_args(self, *offsets):
        """The same, for OFFSET arguments as she would type them."""
        done = self.run_resolve("--lib", self.lib, *offsets)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        lines = [line for line in done.stdout.splitlines() if line]
        self.assertEqual(lines[0], f"{self.lib}: build-id {BUILD_ID}")
        return lines[1:]

    def header(self, address, function):
        start = self.at[function]
        return (f"=== 0x{address:x} -> fn 0x{start:x} (+0x{address - start:x}, "
                f"size 0x{self.next[start] - start:x}) ===")

    def test_frame_is_named_by_its_strings_and_its_callees_paths(self):
        address = self.at["caller"] + 5
        self.assertEqual(self.resolve(address), [
            self.header(address, "caller"),
            "    str: 'OpenClientDirectoryImpl'",
            # A callee gives its first two source paths, passing over the
            # assert before them, a bare file name and a third path...
            f"    via 0x{self.at['paths']:x}: './../../../dom/quota/ActorsParent.cpp'",
            f"    via 0x{self.at['paths']:x}: '/home/plz/firebuild/dom/quota/DirectoryLockImpl.h'",
            # ...or, with none, its first two strings. `silent` has nothing to
            # give, and the second call to `paths` is not followed again.
            f"    via 0x{self.at['plain']:x}: 'MOZ_CRASH(Bad state!)'",
            f"    via 0x{self.at['plain']:x}: 'UnknownErr in %s'",
        ])

    def test_every_function_start_is_found_in_the_stripped_library(self):
        # Each function's first byte and last byte, so an address at either end
        # of one is not put in its neighbour.
        ends = []
        for name in FUNCTIONS[:-1]:
            start = self.at[name]
            ends += [(start, name), (self.next[start] - 1, name)]
        headers = [line for line in self.resolve(*(a for a, _ in ends))
                   if line.startswith("===")]
        self.assertEqual(headers, [self.header(a, name) for a, name in ends])

    def test_short_foreign_control_unterminated_and_repeated_strings_are_dropped(self):
        address = self.at["filtered"]
        self.assertEqual(self.resolve(address), [
            self.header(address, "filtered"),
            "    str: 'abcde'",
            "    str: 'twice'",
            f"    str: '{LONGEST}'",
        ])

    def test_a_function_gives_its_first_eight_strings(self):
        address = self.at["crowded"]
        self.assertEqual(self.resolve(address),
                         [self.header(address, "crowded")]
                         + [f"    str: 'string {i}'" for i in range(1, 9)])

    def test_only_the_first_twelve_calls_are_followed(self):
        address = self.at["fan"]
        self.assertEqual(self.resolve(address),
                         [self.header(address, "fan")]
                         + [f"    via 0x{self.at[f'callee{i:02}']:x}: 'callee {i:02}'"
                            for i in range(12)])

    def test_the_last_function_is_given_a_guessed_size(self):
        # No FDE starts after it, so its end is unknown: resolve.py says 0x200.
        address = self.at["last"] + 3
        self.assertEqual(self.resolve(address), [
            f"=== 0x{address:x} -> fn 0x{self.at['last']:x} (+0x3, size 0x200) ===",
            "    str: 'the last function'",
        ])

    def test_an_address_below_every_function_is_reported_and_passed_over(self):
        below, after = self.at["anchor"] - 1, self.at["plain"]
        self.assertEqual(self.resolve(below, after), [
            f"=== 0x{below:x} -> below the first function start ===",
            self.header(after, "plain"),
            "    str: 'MOZ_CRASH(Bad state!)'",
            "    str: 'UnknownErr in %s'",
            "    str: 'moz-extension:'",
        ])

    def test_a_recursive_call_is_not_followed_back_into_the_frame(self):
        address = self.at["recursive"]
        self.assertEqual(self.resolve(address), [
            self.header(address, "recursive"),
            "    str: 'recursion'",
            f"    via 0x{self.at['plain']:x}: 'MOZ_CRASH(Bad state!)'",
            f"    via 0x{self.at['plain']:x}: 'UnknownErr in %s'",
        ])

    def test_a_rust_literal_ends_at_the_length_passed_with_it(self):
        # Read to its NUL, the first is "second literalservo/...", and the
        # second has no NUL within 200 bytes, so it was dropped.
        address = self.at["rusty"]
        self.assertEqual(self.resolve(address), [
            self.header(address, "rusty"),
            "    str: 'second literal'",
            "    str: 'a literal far from any NUL'",
        ])

    def test_a_c_string_keeps_its_nul_when_a_line_number_follows_it(self):
        # The 9 beside it is not its length: a string right after a NUL is a C string.
        address = self.at["cpp_report"]
        self.assertEqual(self.resolve(address), [
            self.header(address, "cpp_report"),
            "    str: './../../../dom/quota/Short.cpp'",
        ])

    def test_a_cpp_name_inside_a_longer_one_is_not_cut_at_an_argument_count(self):
        # The byte before it is "n" of "Offscreen", not a NUL, so only the cut
        # between two letters of a name with no space gives it away.
        address = self.at["canvas_method"]
        self.assertEqual(self.resolve(address), [
            self.header(address, "canvas_method"),
            "    str: 'CanvasRenderingContext2D.arc'",
        ])

    def test_a_length_counts_only_when_it_and_the_address_reach_one_call(self):
        cut = "    str: 'second literal'"
        glued = "    str: 'second literalservo/components/style/gecko/wrapper.rs'"
        shapes = [("length_after", cut), ("prefixed_call", cut), ("tail_call", cut),
                  ("loaded_twice", cut),  # one reading for both loads of the address
                  ("address_replaced", glued), ("branch_between", glued),
                  ("call_too_far", glued)]
        expected = []
        for name, line in shapes:
            expected += [self.header(self.at[name], name), line]
        self.assertEqual(self.resolve(*(self.at[name] for name, _ in shapes)), expected)

    def test_a_printf_style_call_gives_no_length(self):
        # Its %esi is an argument: read to its NUL, the format has a "\n", so
        # nothing is printed, rather than "    0x%0".
        address = self.at["variadic"]
        self.assertEqual(self.resolve(address), [self.header(address, "variadic")])

    def test_a_callee_is_named_by_its_rust_source_path(self):
        address = self.at["uses_rust_path"]
        self.assertEqual(self.resolve(address), [
            self.header(address, "uses_rust_path"),
            "    str: 'calls rust_path'",
            f"    via 0x{self.at['rust_path']:x}: 'servo/components/style/gecko/wrapper.rs'",
        ])

    def test_offsets_may_omit_0x_and_keep_eu_stacks_plus(self):
        start = self.at["plain"]
        done = self.run_resolve("--lib", self.lib, f"{start:x}", f"+0x{start:x}")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([line for line in done.stdout.splitlines() if line.startswith("===")],
                         [self.header(start, "plain")] * 2)

    def test_floorps_libxul_is_the_default(self):
        done = self.run_resolve("--help")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("default /usr/lib/floorp/libxul.so", " ".join(done.stdout.split()))

    def test_a_missing_library_is_named_before_anything_is_read(self):
        missing = os.path.join(self.tmp, "no-such-libxul.so")
        done = self.run_resolve("--lib", missing, "0x1000")
        self.assertEqual((done.returncode, done.stdout), (2, ""))
        self.assertTrue(done.stderr.endswith(f"error: {missing}: no such file\n"), done.stderr)

    def test_an_offset_that_is_not_hex_is_refused(self):
        done = self.run_resolve("--lib", self.lib, "0x1000", "0x34d8a00-1")
        self.assertEqual((done.returncode, done.stdout), (2, ""))
        self.assertTrue(done.stderr.endswith(
            "error: argument OFFSET: invalid offset value: '0x34d8a00-1'\n"), done.stderr)

    def test_an_eh_frame_hdr_laid_out_otherwise_is_refused(self):
        # fde.py reads one layout, the one LLD writes. Another must stop it, not
        # turn into function starts read from the wrong bytes.
        section = re.search(r"\.eh_frame_hdr\s+\S+\s+[0-9a-f]+\s+([0-9a-f]+)", self.sections)
        if section is None:
            self.fail(f"no .eh_frame_hdr in\n{self.sections}")
        offset = int(section.group(1), 16)
        with open(self.lib, "rb") as handle:
            blob = bytearray(handle.read())
        self.assertEqual(blob[offset:offset + 4].hex(), "011b033b")
        blob[offset] = 2  # version 2
        other = os.path.join(self.tmp, "libfixture-version2.so")
        with open(other, "wb") as handle:
            handle.write(blob)
        done = self.run_resolve("--lib", other, f"0x{self.at['plain']:x}")
        self.assertEqual((done.returncode, done.stdout), (1, ""))
        self.assertEqual(done.stderr, f"{other}: .eh_frame_hdr starts 021b033b, not 011b033b\n")


if __name__ == "__main__":
    unittest.main()
