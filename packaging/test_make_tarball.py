"""Tests for make-tarball.sh: what the tester tarball holds, and what stops it.

    python3 packaging/test_make_tarball.py

Nothing here reads or writes this checkout. Each test copies make-tarball.sh
into a stand-in checkout under a temp dir, so the script's own
`cd "$(dirname "$0")/.."` lands there and the tarball it writes goes there too.
It runs from a third directory, to show that it finds its checkout by itself.
npm is a recorder on PATH: for `ci` it writes the marker real npm leaves in
node_modules and downloads nothing. TMPDIR points into the temp dir, so a
staging dir the script failed to remove is caught.

The stand-in holds one small file for each input the script names, plus what it
must leave out: VCS and IDE state, cargo's target/, node_modules, each app's
dist/ and an app that does not ship. Each file's contents name its own path, so
a file shipped under the wrong name fails as well. install.sh and uninstall.sh
are the real ones, because one test checks install.sh against the tarball.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = "packaging/make-tarball.sh"
REAL = (SCRIPT, "packaging/install.sh", "packaging/uninstall.sh")

VERSION = "9.9-test"
ROOT = f"minkade-{VERSION}"

# The stand-in checkout, besides REAL. Every file is mode 644 except these.
CHECKOUT = (
    "README.md",
    "packaging/minka-session",
    "packaging/minka.desktop",
    # A submodule's .git is a file naming its gitdir, not a directory.
    "ShojiWM/.git",
    "ShojiWM/.idea/workspace.xml",
    "ShojiWM/Cargo.toml",
    "ShojiWM/src/main.rs",
    "ShojiWM/target/release/shoji_wm",
    "ShojiWM/target/release/xdg-desktop-portal-shojiwm",
    "ShojiWM/package.json",
    "ShojiWM/package-lock.json",
    "ShojiWM/tsconfig.json",
    "ShojiWM/node_modules/esbuild/package.json",
    "ShojiWM/packages/shoji_wm/index.ts",
    "ShojiWM/packages/config/src/index.tsx",
    "ShojiWM/tools/decoration-runtime.ts",
    "ShojiWM/tools/release.sh",
    "ShojiWM/dist/shojiwm.portal",
    "ShojiWM/dist/org.freedesktop.impl.portal.desktop.shojiwm.service",
    "ShojiWM/dist/xdg-desktop-portal-shojiwm.service",
    "xwayland-satellite/.git",
    "xwayland-satellite/Cargo.toml",
    "xwayland-satellite/src/main.rs",
    "xwayland-satellite/target/release/xwayland-satellite",
    "MinkaFX/.git",
    "MinkaFX/Cargo.toml",
    "MinkaFX/src/main.rs",
    "MinkaFX/target/release/MinkaFX",
    "MinkaIPC/.git",
    "MinkaIPC/Cargo.toml",
    "MinkaIPC/src/lib.rs",
    "MinkaIPC/target/debug/libminka_ipc.rlib",
    "smithay/.git",
    "smithay/Cargo.toml",
    "smithay/src/lib.rs",
    "MinkaShell/.git",
    "MinkaShell/.idea/workspace.xml",
    "MinkaShell/shell.qml",
    "MinkaShell/modules/bar/Bar.qml",
    "MinkaConf/.git",
    "MinkaConf/shell.qml",
    "MinkaConf/dist/MinkaConf.desktop",
    "MinkaMon/.git",
    "MinkaMon/shell.qml",
    "MinkaMon/scripts/sampler.py",
    "MinkaMon/dist/minkamon",
    "MinkaMon/dist/MinkaMon.desktop",
    "Proustite/.git",
    "Proustite/qmldir",
    "Proustite/Proustite.qml",
    "MinkaLink/.git",
    "MinkaLink/qmldir",
    "MinkaLink/ShojiClient.qml",
    "MinkaShot/shell.qml",  # not a shipped app
)
EXECUTABLE = {
    "packaging/minka-session",
    "ShojiWM/target/release/shoji_wm",
    "ShojiWM/target/release/xdg-desktop-portal-shojiwm",
    "xwayland-satellite/target/release/xwayland-satellite",
    "MinkaFX/target/release/MinkaFX",
    "MinkaMon/dist/minkamon",
}
# Each app reaches the shared singletons through these, as in the real tree.
CHECKOUT_LINKS = {
    "MinkaShell/Proustite": "../Proustite",
    "MinkaShell/MinkaLink": "../MinkaLink",
    "MinkaConf/Proustite": "../Proustite",
    "MinkaMon/Proustite": "../Proustite",
}

# What the npm stand-in writes into node_modules.
NPM_MARKER = b"fixture: npm ci\n"

# The tarball under ROOT: every file in it, and the checkout file it must be a
# copy of (None: written by npm ci). Nothing else may be in it.
SHIPPED_SOURCES = (
    "ShojiWM/Cargo.toml",
    "ShojiWM/src/main.rs",
    "ShojiWM/package.json",
    "ShojiWM/package-lock.json",
    "ShojiWM/tsconfig.json",
    "ShojiWM/packages/shoji_wm/index.ts",
    "ShojiWM/packages/config/src/index.tsx",
    "ShojiWM/tools/decoration-runtime.ts",
    "ShojiWM/tools/release.sh",
    "ShojiWM/dist/shojiwm.portal",
    "ShojiWM/dist/org.freedesktop.impl.portal.desktop.shojiwm.service",
    "ShojiWM/dist/xdg-desktop-portal-shojiwm.service",
    "xwayland-satellite/Cargo.toml",
    "xwayland-satellite/src/main.rs",
    "MinkaFX/Cargo.toml",
    "MinkaFX/src/main.rs",
    "MinkaIPC/Cargo.toml",
    "MinkaIPC/src/lib.rs",
    "smithay/Cargo.toml",
    "smithay/src/lib.rs",
)
TARBALL = {
    "bin/shoji_wm": "ShojiWM/target/release/shoji_wm",
    "bin/xdg-desktop-portal-shojiwm": "ShojiWM/target/release/xdg-desktop-portal-shojiwm",
    "bin/xwayland-satellite": "xwayland-satellite/target/release/xwayland-satellite",
    "bin/MinkaFX": "MinkaFX/target/release/MinkaFX",

    "runtime/package.json": "ShojiWM/package.json",
    "runtime/package-lock.json": "ShojiWM/package-lock.json",
    "runtime/tsconfig.json": "ShojiWM/tsconfig.json",
    "runtime/packages/shoji_wm/index.ts": "ShojiWM/packages/shoji_wm/index.ts",
    "runtime/packages/config/src/index.tsx": "ShojiWM/packages/config/src/index.tsx",
    "runtime/tools/decoration-runtime.ts": "ShojiWM/tools/decoration-runtime.ts",
    "runtime/node_modules/.package-lock.json": None,

    "minka/MinkaShell/shell.qml": "MinkaShell/shell.qml",
    "minka/MinkaShell/modules/bar/Bar.qml": "MinkaShell/modules/bar/Bar.qml",
    "minka/MinkaConf/shell.qml": "MinkaConf/shell.qml",
    "minka/MinkaMon/shell.qml": "MinkaMon/shell.qml",
    "minka/MinkaMon/scripts/sampler.py": "MinkaMon/scripts/sampler.py",
    "minka/Proustite/qmldir": "Proustite/qmldir",
    "minka/Proustite/Proustite.qml": "Proustite/Proustite.qml",
    "minka/MinkaLink/qmldir": "MinkaLink/qmldir",
    "minka/MinkaLink/ShojiClient.qml": "MinkaLink/ShojiClient.qml",

    **{f"src/{path}": path for path in SHIPPED_SOURCES},

    "dist/minka-session": "packaging/minka-session",
    "dist/minkamon": "MinkaMon/dist/minkamon",
    "dist/minka.desktop": "packaging/minka.desktop",
    "dist/MinkaConf.desktop": "MinkaConf/dist/MinkaConf.desktop",
    "dist/MinkaMon.desktop": "MinkaMon/dist/MinkaMon.desktop",
    "dist/shojiwm.portal": "ShojiWM/dist/shojiwm.portal",
    "dist/org.freedesktop.impl.portal.desktop.shojiwm.service":
        "ShojiWM/dist/org.freedesktop.impl.portal.desktop.shojiwm.service",
    "dist/xdg-desktop-portal-shojiwm.service": "ShojiWM/dist/xdg-desktop-portal-shojiwm.service",

    "install.sh": "packaging/install.sh",
    "uninstall.sh": "packaging/uninstall.sh",
    "README.md": "README.md",
}
TARBALL_LINKS = {f"minka/{link}": target for link, target in CHECKOUT_LINKS.items()}
# The files the script installs with an explicit mode. Copied trees keep theirs.
MODES = {
    **dict.fromkeys(("bin/shoji_wm", "bin/xdg-desktop-portal-shojiwm",
                     "bin/xwayland-satellite", "bin/MinkaFX",
                     "dist/minka-session", "dist/minkamon",
                     "install.sh", "uninstall.sh"), 0o755),
    **dict.fromkeys(("dist/minka.desktop", "dist/MinkaConf.desktop", "dist/MinkaMon.desktop",
                     "dist/shojiwm.portal",
                     "dist/org.freedesktop.impl.portal.desktop.shojiwm.service",
                     "dist/xdg-desktop-portal-shojiwm.service",
                     "README.md"), 0o644),
}

# npm, as make-tarball.sh calls it. Logs each call as a JSON line; for `ci`
# refuses a prefix with no lockfile, as real npm does, and otherwise writes
# NPM_MARKER where real npm writes its hidden lockfile. FAKE_NPM_FAIL makes it
# fail after logging.
FAKE_NPM = f"""#!{sys.executable}
import json, os, sys
with open(os.environ["FAKE_NPM_LOG"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
if os.environ.get("FAKE_NPM_FAIL"):
    sys.exit("npm error fake failure")
prefix = sys.argv[sys.argv.index("--prefix") + 1]
if not os.path.isfile(os.path.join(prefix, "package-lock.json")):
    sys.exit("npm error `npm ci` can only install with an existing package-lock.json")
os.makedirs(os.path.join(prefix, "node_modules"))
with open(os.path.join(prefix, "node_modules", ".package-lock.json"), "wb") as out:
    out.write({NPM_MARKER!r})
"""


def member_bytes(tar, member):
    """The contents of a regular file in tar, a TarInfo or a name."""
    handle = tar.extractfile(member)
    assert handle is not None, f"{member} is not a regular file"
    return handle.read()


@unittest.skipUnless(shutil.which("rsync"), "make-tarball.sh copies trees with rsync")
class MakeTarball(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory(prefix="test_make_tarball."))
        self.repo = os.path.join(self.tmp, "checkout")
        self.tmpdir = os.path.join(self.tmp, "tmpdir")      # the script's TMPDIR
        self.cwd = os.path.join(self.tmp, "elsewhere")      # where it is run from
        fake_bin = os.path.join(self.tmp, "bin")
        self.npm_log = os.path.join(self.tmp, "npm.log")
        for path in (self.tmpdir, self.cwd, fake_bin):
            os.makedirs(path)

        for path in CHECKOUT:
            self.write(path, f"fixture: {path}\n".encode(),
                       0o755 if path in EXECUTABLE else 0o644)
        for path in REAL:
            with open(os.path.join(HERE, os.path.basename(path)), "rb") as real:
                self.write(path, real.read(), 0o755)
        for link, target in CHECKOUT_LINKS.items():
            os.symlink(target, os.path.join(self.repo, link))

        npm = os.path.join(fake_bin, "npm")
        with open(npm, "w") as out:
            out.write(FAKE_NPM)
        os.chmod(npm, 0o755)

        self.env = dict(os.environ, PATH=fake_bin + os.pathsep + os.environ.get("PATH", ""),
                        TMPDIR=self.tmpdir, FAKE_NPM_LOG=self.npm_log)
        self.env.pop("FAKE_NPM_FAIL", None)

    def write(self, path, data, mode):
        full = os.path.join(self.repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as out:
            out.write(data)
        os.chmod(full, mode)

    def checkout_bytes(self, path):
        with open(os.path.join(self.repo, path), "rb") as handle:
            return handle.read()

    def run_script(self, *args):
        return subprocess.run([os.path.join(self.repo, SCRIPT), *args], cwd=self.cwd,
                              env=self.env, capture_output=True, text=True, timeout=120)

    def tarball(self, version=VERSION):
        return os.path.join(self.repo, f"minkade-{version}-linux-x86_64.tar.gz")

    def build(self, version):
        """Run the script, which must succeed, and return the tarball's path."""
        done = self.run_script(version)
        self.assertEqual(done.returncode, 0, done.stderr)
        return self.tarball(version)

    def npm_calls(self):
        try:
            with open(self.npm_log) as log:
                return [json.loads(line) for line in log]
        except FileNotFoundError:
            return []

    def assertNothingLeftBehind(self, before):
        """The checkout has only what was there before, and the staging dir is gone."""
        self.assertEqual(sorted(os.listdir(self.repo)), sorted(before))
        self.assertEqual(os.listdir(self.tmpdir), [])

    def test_tarball_holds_exactly_the_listed_files(self):
        before = os.listdir(self.repo)
        tarball = self.build(VERSION)
        # The tarball is the only thing the script adds to its checkout.
        self.assertEqual(sorted(set(os.listdir(self.repo)) - set(before)),
                         [os.path.basename(tarball)])

        with tarfile.open(tarball) as tar:
            members = tar.getmembers()
            self.assertEqual({m.name.split("/")[0] for m in members}, {ROOT})
            inside = {m.name.removeprefix(f"{ROOT}/"): m for m in members}
            files = {name: m for name, m in inside.items() if m.isfile()}
            links = {name: m.linkname for name, m in inside.items() if m.issym()}
            others = [name for name, m in inside.items()
                      if not (m.isfile() or m.issym() or m.isdir())]

            self.assertEqual(sorted(files), sorted(TARBALL))
            self.assertEqual(links, TARBALL_LINKS)
            self.assertEqual(others, [])
            for name, source in TARBALL.items():
                with self.subTest(file=name):
                    want = NPM_MARKER if source is None else self.checkout_bytes(source)
                    self.assertEqual(member_bytes(tar, files[name]), want)
            for name, mode in MODES.items():
                with self.subTest(mode=name):
                    self.assertEqual(oct(stat.S_IMODE(files[name].mode)), oct(mode))

    def test_shared_singletons_resolve_from_every_app(self):
        out = os.path.join(self.tmp, "unpacked")
        with tarfile.open(self.build(VERSION)) as tar:
            tar.extractall(out, filter="data")
        for link in TARBALL_LINKS:
            with self.subTest(link=link):
                self.assertTrue(os.path.isfile(os.path.join(out, ROOT, link, "qmldir")))

    def test_npm_ci_runs_once_in_the_staged_runtime(self):
        self.build(VERSION)
        calls = self.npm_calls()
        self.assertEqual(len(calls), 1, calls)
        flag, prefix, *rest = calls[0]
        self.assertEqual((flag, rest), ("--prefix", ["ci", "--silent"]))
        # Staged under TMPDIR, not in the checkout, and removed once packed.
        self.assertEqual(os.path.relpath(prefix, self.tmpdir).split(os.sep)[1:],
                         [ROOT, "runtime"])
        self.assertEqual(os.listdir(self.tmpdir), [])

    def test_version_defaults_to_0_1(self):
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        with tarfile.open(self.tarball("0.1")) as tar:
            self.assertEqual({name.split("/")[0] for name in tar.getnames()}, {"minkade-0.1"})

    def test_preflight_lists_every_missing_input_and_stages_nothing(self):
        os.remove(os.path.join(self.repo, "ShojiWM/target/release/shoji_wm"))
        os.chmod(os.path.join(self.repo, "MinkaFX/target/release/MinkaFX"), 0o644)
        os.remove(os.path.join(self.repo, "ShojiWM/tools/decoration-runtime.ts"))
        shutil.rmtree(os.path.join(self.repo, "MinkaLink"))
        os.remove(os.path.join(self.repo, "MinkaMon/dist/MinkaMon.desktop"))
        os.remove(os.path.join(self.repo, "README.md"))
        before = os.listdir(self.repo)

        done = self.run_script(VERSION)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "")
        self.assertEqual(done.stderr,
                         "missing from this checkout:\n"
                         "   - ShojiWM/target/release/shoji_wm (release binary)\n"
                         "   - MinkaFX/target/release/MinkaFX (release binary)\n"
                         "   - ShojiWM/tools/decoration-runtime.ts\n"
                         "   - MinkaLink\n"
                         "   - MinkaMon/dist/MinkaMon.desktop\n"
                         "   - README.md\n")
        self.assertEqual(self.npm_calls(), [])
        self.assertNothingLeftBehind(before)

    def test_failed_npm_ci_leaves_no_tarball_or_staging_dir(self):
        self.env["FAKE_NPM_FAIL"] = "1"
        before = os.listdir(self.repo)
        done = self.run_script(VERSION)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("npm error fake failure", done.stderr)
        self.assertEqual(len(self.npm_calls()), 1)
        self.assertNothingLeftBehind(before)

    def test_every_tarball_path_install_sh_reads_ships(self):
        """install.sh names what it installs one by one (make-tarball.sh says so
        above its lists), so a file dropped from a list, or renamed, leaves
        install.sh reading a path that is not there."""
        with tarfile.open(self.build(VERSION)) as tar:
            shipped = {name.removeprefix(f"{ROOT}/") for name in tar.getnames()}
            install_sh = member_bytes(tar, f"{ROOT}/install.sh").decode()
        # Relative paths only: /usr/share/minka/MinkaShell is where it installs to.
        read = set(re.findall(r"(?<![\w/.-])((?:bin|dist|minka|runtime|src)/[\w.+-]+(?:/[\w.+-]+)*)",
                              install_sh))
        # --from-source builds these itself.
        read = {path for path in read if not re.match(r"src/[^/]+/target/", path)}
        self.assertTrue(read, "found no tarball paths in install.sh: the pattern is stale")
        self.assertEqual(sorted(read - shipped), [])


if __name__ == "__main__":
    unittest.main()
