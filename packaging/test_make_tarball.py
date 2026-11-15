"""Tests for make-tarball.sh: what the tester tarball holds, and what stops it.

    python3 packaging/test_make_tarball.py

Nothing here reads or writes this checkout, except that one test reads the
Rust config's source and asset files. Each test copies make-tarball.sh into a
stand-in checkout under a temp dir, so the script's own
`cd "$(dirname "$0")/.."` lands there and the tarball it writes goes there too.
It runs from a third directory, to show that it finds its checkout by itself.
TMPDIR points into the temp dir, so a staging dir the script failed to remove
is caught.

The stand-in holds one small file for each input the script names, plus what it
must leave out: VCS and IDE state, cargo's target/ (but for the builds it
names), the old TypeScript shoji_wm, node_modules, each app's dist/ (but for
MinkaShell's launcher), a TypeScript file beside the shaders, and an app that
does not ship. The session's MINKA_*/SHOJI_* overrides are left out of the
environment, so nothing points back into a real checkout. Each file's
contents name its own path, so a file shipped under the wrong name fails as
well. install.sh and uninstall.sh are the real ones, because one test checks
install.sh against the tarball. test_install.py builds on Checkout.
"""

import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPT = "packaging/make-tarball.sh"
REAL = (SCRIPT, "packaging/install.sh", "packaging/uninstall.sh")
# The Rust config, whose source names the assets it reads.
RUST_CONFIG = os.path.join(REPO, "ShojiWM/src/shojiwm_rs/examples/default_config")
ASSET_ROOT = "ShojiWM/packages/config"

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
    "ShojiWM/target/release/shoji_wm",  # the TypeScript build: must not ship
    "ShojiWM/target/release/examples/default_config",
    "ShojiWM/target/release/xdg-desktop-portal-shojiwm",
    "ShojiWM/package.json",
    "ShojiWM/node_modules/esbuild/package.json",
    "ShojiWM/packages/config/src/index.tsx",
    "ShojiWM/packages/config/src/effect/island-glass.ts",
    "ShojiWM/packages/config/src/effect/liquid-glass.frag",
    "ShojiWM/packages/config/src/effect/backdrop-fade.frag",
    "ShojiWM/packages/config/assets/drag-tab-top.svg",
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
    "MinkaCap/.git",
    "MinkaCap/Cargo.toml",
    "MinkaCap/src/main.rs",
    "MinkaCap/target/release/MinkaCap",
    "MinkaCap/target/debug/MinkaCap",
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
    "MinkaShell/Cargo.toml",
    "MinkaShell/src/main.rs",
    "MinkaShell/assets/mac-glass-minimize.svg",
    "MinkaShell/scripts/app-mute.py",
    "MinkaShell/target/release/minkashell",
    "MinkaShell/target/debug/minkashell",
    "MinkaShell/dist/minkashell",
    "MinkaConf/.git",
    "MinkaConf/shell.qml",
    "MinkaConf/dist/MinkaConf.desktop",
    "MinkaMon/.git",
    "MinkaMon/shell.qml",
    "MinkaMon/Cargo.toml",
    "MinkaMon/src/main.rs",
    "MinkaMon/assets/coastlines.json",
    "MinkaMon/sampler-rs/Cargo.toml",
    "MinkaMon/sampler-rs/src/main.rs",
    "MinkaMon/target/release/minkamon",
    "MinkaMon/sampler-rs/target/release/minkamon-sampler",
    "MinkaMon/dist/minkamon",
    "MinkaMon/dist/MinkaMon.desktop",
    "Proustite/.git",
    "Proustite/qmldir",
    "Proustite/Proustite.qml",
    "MinkaLink/.git",
    "MinkaLink/qmldir",
    "MinkaLink/ShojiClient.qml",
    "MinkaShot/.git",
    "MinkaShot/shell.qml",
    "MinkaShot/services/ShotState.qml",
    "MinkaLedger/ui/shell.qml",  # not a shipped app
)
EXECUTABLE = {
    "packaging/minka-session",
    "ShojiWM/target/release/shoji_wm",
    "ShojiWM/target/release/examples/default_config",
    "ShojiWM/target/release/xdg-desktop-portal-shojiwm",
    "xwayland-satellite/target/release/xwayland-satellite",
    "MinkaFX/target/release/MinkaFX",
    "MinkaCap/target/release/MinkaCap",
    "MinkaCap/target/debug/MinkaCap",
    "MinkaShell/target/release/minkashell",
    "MinkaShell/target/debug/minkashell",
    "MinkaShell/dist/minkashell",
    "MinkaMon/target/release/minkamon",
    "MinkaMon/sampler-rs/target/release/minkamon-sampler",
    "MinkaMon/dist/minkamon",
}
# Contents other than "fixture: <path>". The compositor's name its asset
# fallback, as a real build's do: make-tarball.sh looks for it.
CONTENTS = {
    "ShojiWM/target/release/examples/default_config":
        "fixture: the Rust config, falling back to /usr/share/minka/shojiwm\n",
}
# Each app reaches the shared singletons through these, as in the real tree.
CHECKOUT_LINKS = {
    "MinkaShell/Proustite": "../Proustite",
    "MinkaShell/MinkaLink": "../MinkaLink",
    "MinkaConf/Proustite": "../Proustite",
    "MinkaMon/Proustite": "../Proustite",
    "MinkaMon/MinkaLink": "../MinkaLink",
    "MinkaShot/Proustite": "../Proustite",
    "MinkaShot/MinkaLink": "../MinkaLink",
}

# The tarball under ROOT: every file in it, and the checkout file it must be a
# copy of. Nothing else may be in it.
SHIPPED_SOURCES = (
    "ShojiWM/Cargo.toml",
    "ShojiWM/src/main.rs",
    "ShojiWM/package.json",
    "ShojiWM/packages/config/src/index.tsx",
    "ShojiWM/packages/config/src/effect/island-glass.ts",
    "ShojiWM/packages/config/src/effect/liquid-glass.frag",
    "ShojiWM/packages/config/src/effect/backdrop-fade.frag",
    "ShojiWM/packages/config/assets/drag-tab-top.svg",
    "ShojiWM/dist/shojiwm.portal",
    "ShojiWM/dist/org.freedesktop.impl.portal.desktop.shojiwm.service",
    "ShojiWM/dist/xdg-desktop-portal-shojiwm.service",
    "xwayland-satellite/Cargo.toml",
    "xwayland-satellite/src/main.rs",
    "MinkaFX/Cargo.toml",
    "MinkaFX/src/main.rs",
    "MinkaCap/Cargo.toml",
    "MinkaCap/src/main.rs",
    "MinkaShell/shell.qml",
    "MinkaShell/modules/bar/Bar.qml",
    "MinkaShell/Cargo.toml",
    "MinkaShell/src/main.rs",
    "MinkaShell/assets/mac-glass-minimize.svg",
    "MinkaShell/scripts/app-mute.py",
    "MinkaShell/dist/minkashell",
    "MinkaMon/shell.qml",
    "MinkaMon/Cargo.toml",
    "MinkaMon/src/main.rs",
    "MinkaMon/assets/coastlines.json",
    "MinkaMon/sampler-rs/Cargo.toml",
    "MinkaMon/sampler-rs/src/main.rs",
    "MinkaMon/dist/minkamon",
    "MinkaMon/dist/MinkaMon.desktop",
    "MinkaIPC/Cargo.toml",
    "MinkaIPC/src/lib.rs",
    "smithay/Cargo.toml",
    "smithay/src/lib.rs",
)
TARBALL = {
    "bin/shoji_wm": "ShojiWM/target/release/examples/default_config",
    "bin/xdg-desktop-portal-shojiwm": "ShojiWM/target/release/xdg-desktop-portal-shojiwm",
    "bin/xwayland-satellite": "xwayland-satellite/target/release/xwayland-satellite",
    "bin/MinkaFX": "MinkaFX/target/release/MinkaFX",
    "bin/MinkaCap": "MinkaCap/target/release/MinkaCap",

    "minka/shojiwm/src/effect/liquid-glass.frag": f"{ASSET_ROOT}/src/effect/liquid-glass.frag",
    "minka/shojiwm/src/effect/backdrop-fade.frag": f"{ASSET_ROOT}/src/effect/backdrop-fade.frag",
    "minka/shojiwm/assets/drag-tab-top.svg": f"{ASSET_ROOT}/assets/drag-tab-top.svg",

    "minka/MinkaShell/shell.qml": "MinkaShell/shell.qml",
    "minka/MinkaShell/modules/bar/Bar.qml": "MinkaShell/modules/bar/Bar.qml",
    "minka/MinkaShell/Cargo.toml": "MinkaShell/Cargo.toml",
    "minka/MinkaShell/src/main.rs": "MinkaShell/src/main.rs",
    "minka/MinkaShell/assets/mac-glass-minimize.svg": "MinkaShell/assets/mac-glass-minimize.svg",
    "minka/MinkaShell/scripts/app-mute.py": "MinkaShell/scripts/app-mute.py",
    "minka/MinkaShell/minkashell": "MinkaShell/target/release/minkashell",
    "minka/MinkaShell/dist/minkashell": "MinkaShell/dist/minkashell",
    "minka/MinkaShot/shell.qml": "MinkaShot/shell.qml",
    "minka/MinkaShot/services/ShotState.qml": "MinkaShot/services/ShotState.qml",
    "minka/MinkaConf/shell.qml": "MinkaConf/shell.qml",
    "minka/MinkaMon/shell.qml": "MinkaMon/shell.qml",
    "minka/MinkaMon/Cargo.toml": "MinkaMon/Cargo.toml",
    "minka/MinkaMon/src/main.rs": "MinkaMon/src/main.rs",
    "minka/MinkaMon/assets/coastlines.json": "MinkaMon/assets/coastlines.json",
    "minka/MinkaMon/sampler-rs/Cargo.toml": "MinkaMon/sampler-rs/Cargo.toml",
    "minka/MinkaMon/sampler-rs/src/main.rs": "MinkaMon/sampler-rs/src/main.rs",
    "minka/MinkaMon/minkamon": "MinkaMon/target/release/minkamon",
    "minka/MinkaMon/minkamon-sampler": "MinkaMon/sampler-rs/target/release/minkamon-sampler",
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
APP_LINKS = {f"minka/{link}": target for link, target in CHECKOUT_LINKS.items()}
# The source trees of MinkaMon and MinkaShell keep their links as well. They
# dangle in src/, where nothing reads them: the cargo builds compile the
# theme in.
TARBALL_LINKS = {**APP_LINKS,
                 **{f"src/{link}": target for link, target in CHECKOUT_LINKS.items()
                    if link.startswith(("MinkaMon/", "MinkaShell/"))}}
# The files the script installs with an explicit mode. Copied trees keep theirs.
MODES = {
    **dict.fromkeys(("bin/shoji_wm", "bin/xdg-desktop-portal-shojiwm",
                     "bin/xwayland-satellite", "bin/MinkaFX", "bin/MinkaCap",
                     "minka/MinkaShell/minkashell", "minka/MinkaShell/dist/minkashell",
                     "minka/MinkaMon/minkamon", "minka/MinkaMon/minkamon-sampler",
                     "dist/minka-session", "dist/minkamon",
                     "install.sh", "uninstall.sh"), 0o755),
    **dict.fromkeys(("dist/minka.desktop", "dist/MinkaConf.desktop", "dist/MinkaMon.desktop",
                     "dist/shojiwm.portal",
                     "dist/org.freedesktop.impl.portal.desktop.shojiwm.service",
                     "dist/xdg-desktop-portal-shojiwm.service",
                     "README.md"), 0o644),
}

# A string literal naming an asset file in the Rust config's source. All of
# them are relative paths written out whole ("./src/effect/x.frag"); one
# that is not would be a path this test cannot follow.
ASSET_STRING = re.compile(r'"([^"\\]*\.(?:frag|vert|glsl|wgsl|svg|png|jpg|webp|ttf|otf))"')


def member_bytes(tar, member):
    """The contents of a regular file in tar, a TarInfo or a name."""
    handle = tar.extractfile(member)
    assert handle is not None, f"{member} is not a regular file"
    return handle.read()


def config_asset_paths():
    """Every asset path the Rust config's source names, relative to its root,
    and any asset literal that is not such a path."""
    paths, odd = set(), []
    for parent, _, names in os.walk(RUST_CONFIG):
        for name in names:
            if name.endswith(".rs"):
                with open(os.path.join(parent, name)) as source:
                    code = re.sub(r"//.*", "", source.read())
                for literal in ASSET_STRING.findall(code):
                    if literal.startswith("./"):
                        paths.add(literal.removeprefix("./"))
                    else:
                        odd.append(f"{os.path.join(parent, name)}: {literal!r}")
    return paths, odd


@unittest.skipUnless(shutil.which("rsync"), "make-tarball.sh copies trees with rsync")
class Checkout(unittest.TestCase):
    """A stand-in checkout with make-tarball.sh in it, run from elsewhere."""

    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory(prefix="test_packaging."))
        self.repo = os.path.join(self.tmp, "checkout")
        self.tmpdir = os.path.join(self.tmp, "tmpdir")      # the script's TMPDIR
        self.cwd = os.path.join(self.tmp, "elsewhere")      # where it is run from
        self.bin = os.path.join(self.tmp, "bin")            # stand-ins, first on PATH
        for path in (self.tmpdir, self.cwd, self.bin):
            os.makedirs(path)

        for path in CHECKOUT:
            self.write(path, CONTENTS.get(path, f"fixture: {path}\n").encode(),
                       0o755 if path in EXECUTABLE else 0o644)
        for path in REAL:
            with open(os.path.join(HERE, os.path.basename(path)), "rb") as real:
                self.write(path, real.read(), 0o755)
        for link, target in CHECKOUT_LINKS.items():
            os.symlink(target, os.path.join(self.repo, link))

        self.env = {name: value for name, value in os.environ.items()
                    if not name.startswith(("MINKA_", "SHOJI_", "ANTIWEBUI_"))}
        self.env.update(PATH=self.bin + os.pathsep + os.environ.get("PATH", ""),
                        TMPDIR=self.tmpdir)

    def write(self, path, data, mode):
        full = os.path.join(self.repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as out:
            out.write(data)
        os.chmod(full, mode)

    def stub(self, name, body):
        """Put a command named `name` first on PATH: a shell script."""
        path = os.path.join(self.bin, name)
        with open(path, "w") as out:
            out.write("#!/bin/sh\n" + body)
        os.chmod(path, 0o755)

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

    def assertNothingLeftBehind(self, before):
        """The checkout has only what was there before, and the staging dir is gone."""
        self.assertEqual(sorted(os.listdir(self.repo)), sorted(before))
        self.assertEqual(os.listdir(self.tmpdir), [])


class MakeTarball(Checkout):
    def test_tarball_holds_exactly_the_listed_files(self):
        before = os.listdir(self.repo)
        tarball = self.build(VERSION)
        # The tarball is the only thing the script adds to its checkout, and
        # its staging dir is gone.
        self.assertEqual(sorted(set(os.listdir(self.repo)) - set(before)),
                         [os.path.basename(tarball)])
        self.assertEqual(os.listdir(self.tmpdir), [])

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
                    self.assertEqual(member_bytes(tar, files[name]), self.checkout_bytes(source))
            for name, mode in MODES.items():
                with self.subTest(mode=name):
                    self.assertEqual(oct(stat.S_IMODE(files[name].mode)), oct(mode))

    def test_shared_singletons_resolve_from_every_app(self):
        out = os.path.join(self.tmp, "unpacked")
        with tarfile.open(self.build(VERSION)) as tar:
            tar.extractall(out, filter="data")
        for link in APP_LINKS:
            with self.subTest(link=link):
                self.assertTrue(os.path.isfile(os.path.join(out, ROOT, link, "qmldir")))

    def test_version_defaults_to_0_1(self):
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        with tarfile.open(self.tarball("0.1")) as tar:
            self.assertEqual({name.split("/")[0] for name in tar.getnames()}, {"minkade-0.1"})

    def test_preflight_lists_every_missing_input_and_stages_nothing(self):
        for path in ("ShojiWM/target/release/examples/default_config",
                     "MinkaShell/target/release/minkashell",
                     "MinkaShell/dist/minkashell",
                     "MinkaMon/sampler-rs/target/release/minkamon-sampler",
                     f"{ASSET_ROOT}/src/effect/liquid-glass.frag",
                     f"{ASSET_ROOT}/src/effect/backdrop-fade.frag",
                     "MinkaMon/dist/MinkaMon.desktop",
                     "README.md"):
            os.remove(os.path.join(self.repo, path))
        os.chmod(os.path.join(self.repo, "MinkaFX/target/release/MinkaFX"), 0o644)
        shutil.rmtree(os.path.join(self.repo, "MinkaLink"))
        before = os.listdir(self.repo)

        done = self.run_script(VERSION)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "")
        # An asset pattern that matches nothing is reported as written.
        self.assertEqual(done.stderr,
                         "missing from this checkout:\n"
                         "   - ShojiWM/target/release/examples/default_config (release binary)\n"
                         "   - MinkaFX/target/release/MinkaFX (release binary)\n"
                         "   - MinkaShell/target/release/minkashell (release binary)\n"
                         "   - MinkaMon/sampler-rs/target/release/minkamon-sampler (release binary)\n"
                         f"   - {ASSET_ROOT}/src/effect/*.frag\n"
                         "   - MinkaLink\n"
                         "   - MinkaShell/dist/minkashell\n"
                         "   - MinkaMon/dist/MinkaMon.desktop\n"
                         "   - README.md\n")
        self.assertNothingLeftBehind(before)

    def test_preflight_refuses_a_compositor_without_the_asset_fallback(self):
        """As one built from a ShojiWM commit older than the fallback would be."""
        self.write("ShojiWM/target/release/examples/default_config",
                   b"fixture: an older Rust config\n", 0o755)
        before = os.listdir(self.repo)
        done = self.run_script(VERSION)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stderr,
                         "missing from this checkout:\n"
                         "   - ShojiWM/target/release/examples/default_config (has no "
                         "/usr/share/minka/shojiwm asset fallback: is the ShojiWM gitlink current?)\n")
        self.assertNothingLeftBehind(before)

    def test_failure_after_staging_leaves_no_tarball_or_staging_dir(self):
        self.stub("tar", 'echo "tar: fake failure" >&2\nexit 2\n')
        before = os.listdir(self.repo)
        done = self.run_script(VERSION)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("tar: fake failure", done.stderr)
        self.assertNothingLeftBehind(before)

    @unittest.skipUnless(os.path.isdir(RUST_CONFIG), "no Rust config checkout beside this script")
    def test_every_asset_the_rust_config_names_ships(self):
        """Uses the real asset files, so a shader added, renamed or moved by the
        config is checked against the script's ASSETS patterns."""
        named, odd = config_asset_paths()
        self.assertTrue(named, "found no asset paths in the Rust config: the pattern is stale")
        self.assertEqual(odd, [], "asset paths not written as whole \"./...\" literals")
        shutil.rmtree(os.path.join(self.repo, ASSET_ROOT))
        shutil.copytree(os.path.join(REPO, ASSET_ROOT), os.path.join(self.repo, ASSET_ROOT))
        with tarfile.open(self.build(VERSION)) as tar:
            shipped = set(tar.getnames())
        self.assertEqual(sorted(p for p in named if f"{ROOT}/minka/shojiwm/{p}" not in shipped), [])

    def test_every_tarball_path_install_sh_reads_ships(self):
        """install.sh names what it installs one by one (make-tarball.sh says so
        above its lists), so a file dropped from a list, or renamed, leaves
        install.sh reading a path that is not there."""
        with tarfile.open(self.build(VERSION)) as tar:
            shipped = {name.removeprefix(f"{ROOT}/") for name in tar.getnames()}
            install_sh = member_bytes(tar, f"{ROOT}/install.sh").decode()
        # Relative paths only: /usr/share/minka/MinkaShell is where it installs to.
        read = set(re.findall(r"(?<![\w/.-])((?:bin|dist|minka|src)/[\w.+-]+(?:/[\w.+-]+)*)",
                              install_sh))
        # --from-source builds these itself.
        read = {path for path in read if "/target/" not in path}
        self.assertTrue(read, "found no tarball paths in install.sh: the pattern is stale")
        self.assertEqual(sorted(read - shipped), [])


if __name__ == "__main__":
    unittest.main()
