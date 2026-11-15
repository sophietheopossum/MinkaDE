"""Tests for resolve.py (and fde.py under it): naming frames of a stripped library.

    python3 tools/firefox-deadlock/test_resolve.py

Nothing here reads the real libxul. resolve.py and fde.py each name their
library in a LIB = "..." line, so the tests run copies of the two in which that
line names a small library compiled here from FIXTURE and then stripped, as
libxul is. Every address a test passes comes from the unstripped build's
symbols; resolve.py only ever sees the stripped copy.

The scripts are run as she runs them, as a command with addresses on the line,
and each test checks the exact lines printed.

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

LONGEST = "L" * 199   # s_at looks for the NUL in 200 bytes: this one's is the last
TOO_LONG = "M" * 200  # and this one's is one past them
CALLEES = 13          # one more than resolve.py follows

# A function's string literals are what resolve.py prints, so each function
# here stores its own in `sink`: that is one RIP-relative lea per literal, whose
# target objdump prints after a "#", as it does all through libxul.
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

@CALLEES@

void fan(void) {
@CALLS@
}

void last(void) { sink = "the last function"; }
"""

FIXTURE = (FIXTURE
           .replace("@TOO_LONG@", TOO_LONG)
           .replace("@LONGEST@", LONGEST)
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
]

# The functions defined in FIXTURE, by name.
FUNCTIONS = (["anchor", "paths", "plain", "silent", "caller", "filtered", "crowded"]
             + [f"callee{i:02}" for i in range(CALLEES)] + ["fan", "last"])

LIB_LINE = re.compile(r'^LIB = "[^"\n]*"$', re.M)


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
        source = os.path.join(tmp.name, "fixture.c")
        built = os.path.join(tmp.name, "libfixture.so")
        lib = os.path.join(tmp.name, "libfixture-stripped.so")
        with open(source, "w") as handle:
            handle.write(FIXTURE)
        run("gcc", *CFLAGS, "-o", built, source)
        run("strip", "-o", lib, built)

        # The tests mean something only if resolve.py sees what it sees in
        # libxul: no symbol table, a few exported names, an .eh_frame_hdr.
        sections = run("readelf", "-SW", lib)
        if ".symtab" in sections or ".eh_frame_hdr" not in sections:
            raise AssertionError(f"the fixture is not stripped like libxul:\n{sections}")
        exported = run("nm", "-D", "--defined-only", lib).split()
        if exported[-1:] != ["anchor"] or len(exported) != 3:
            raise AssertionError(f"the fixture should export only anchor: {exported}")

        symbols = {}
        for line in run("nm", "--defined-only", built).splitlines():
            address, _, name = line.split()
            symbols[name] = int(address, 16)
        cls.at = {name: symbols[name] for name in FUNCTIONS}
        order = sorted(cls.at.values())
        cls.next = dict(zip(order, order[1:]))  # function start -> the next one's

        # Copies of the two scripts that read the fixture instead of libxul.
        cls.script = os.path.join(tmp.name, "resolve.py")
        for name in ("fde.py", "resolve.py"):
            with open(os.path.join(HERE, name)) as handle:
                text = handle.read()
            original = LIB_LINE.search(text)
            text, count = LIB_LINE.subn(lambda _: f"LIB = {lib!r}", text)
            if count != 1 or original is None:
                raise AssertionError(f'{name}: expected one LIB = "..." line to point at '
                                     f"the fixture, found {count}")
            if original.group(0).split(" = ", 1)[1] in text:
                raise AssertionError(f"{name} still names its library outside the LIB line")
            with open(os.path.join(tmp.name, name), "w") as handle:
                handle.write(text)

    def resolve(self, *addresses):
        """resolve.py's output for these addresses, without its blank lines."""
        done = subprocess.run(
            [sys.executable, "-I", "-B", self.script, *(f"0x{a:x}" for a in addresses)],
            capture_output=True, text=True, timeout=120,
            env=dict(os.environ, LC_ALL="C"))
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        return [line for line in done.stdout.splitlines() if line]

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


if __name__ == "__main__":
    unittest.main()
