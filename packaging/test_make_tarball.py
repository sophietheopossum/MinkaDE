"""Tests for make-tarball.sh: what the tester tarball holds, and what stops it.

    python3 packaging/test_make_tarball.py

Nothing here reads or writes this checkout, except that one test reads the
Rust config's source and asset files, and one reads the workspace's Cargo.toml.
Each test copies make-tarball.sh and stage-usr.sh into a stand-in checkout
under a temp dir, so the scripts' own `cd "$(dirname "$0")/.."` lands there
and the tarball goes there too. It runs from a third directory, to show that
it finds its checkout by itself. TMPDIR points into the temp dir, so a
staging dir the script failed to remove is caught.

The stand-in holds one small file for each input the scripts read, plus what
they must leave out: VCS and IDE state, cargo's target/ dirs (with the
per-app builds from before minka, and the crates' own from before the
workspace), the old TypeScript shoji_wm, node_modules, a TypeScript file
beside the shaders, an app that does not ship, and a tool that is not a
workspace member. Its minka is a shell script that answers --names as the
real one does, so no real build runs. The session's MINKA_*/SHOJI_*
overrides are left out of the environment, so nothing points back into a
real checkout. Each file's contents name its own path, so a file shipped
under the wrong name fails as well. install.sh and uninstall.sh are the real
ones, because one test checks them against the tarball. test_install.py
builds on Checkout.

usr/ is stage-usr.sh's, the one description of the installed layout: the
tests check that the tarball's usr/ is exactly what it lays out, and the
paths the tarball's own scripts and the session rely on, rather than listing
the layout again.
"""

import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPT = "packaging/make-tarball.sh"
STAGE_USR = "packaging/stage-usr.sh"
REAL = (SCRIPT, STAGE_USR, "packaging/install.sh", "packaging/uninstall.sh")
# The Rust config, whose source names the assets it reads.
RUST_CONFIG = os.path.join(REPO, "ShojiWM/src/shojiwm_rs/examples/default_config")
ASSET_ROOT = "ShojiWM/src/shojiwm_rs/examples/default_config/assets"
# Where the compositor looks for its assets once its checkout is gone.
ASSET_FALLBACK = "/usr/share/minka/shojiwm"

VERSION = "9.9-test"
ROOT = f"minkade-{VERSION}"

# The names the stand-in minka answers to, as the real one's apps! list has them.
NAMES = ("shoji_wm", "xdg-desktop-portal-shojiwm", "xwayland-satellite", "MinkaFX", "MinkaCap",
         "minkashell", "minkashot", "minkamon", "minkamon-sampler", "minka-syu", "minka-syu-gui",
         "minka-thermal", "minka-ledger", "bartizan-lsp")


def minka_script(names=NAMES, fallback=True):
    """A stand-in for minka: it lists its names for --names, and otherwise
    says how it was started. A real build names the asset fallback, which
    make-tarball.sh looks for."""
    return ("#!/bin/sh\n"
            "# fixture: target/release/minka"
            + (f", falling back to {ASSET_FALLBACK}" if fallback else "") + "\n"
            'if [ "$1" = --names ]; then\n'
            f"    printf '%s\\n' {' '.join(names)}\n"
            "    exit 0\n"
            "fi\n"
            'echo "minka ran as $0: $*"\n')


# The stand-in's Cargo workspace: each member, with the files it holds.
MEMBERS = {
    "bartizan-lsp": ("Cargo.toml", "src/lib.rs"),
    "MinkaFX": ("Cargo.toml", "src/lib.rs"),
    "MinkaIPC": ("Cargo.toml", "src/lib.rs"),
    "MinkaLedger": ("Cargo.toml", "src/lib.rs", "src/main.rs", "ui/shell.qml",
                    "dist/MinkaLedger.desktop"),
    "MinkaMon": ("Cargo.toml", "src/lib.rs", "assets/coastlines.json",
                 "dist/minkamon", "dist/MinkaMon.desktop"),
    "MinkaMon/sampler-rs": ("Cargo.toml", "src/lib.rs"),
    "MinkaShell": ("Cargo.toml", "src/lib.rs", "assets/mac-glass-minimize.svg",
                   "scripts/app-mute.py", "dist/minkashell"),
    # The crate, beside the QML tree, installed until it is deleted.
    "MinkaShot": ("Cargo.toml", "src/lib.rs", "shell.qml", "services/ShotState.qml"),
    "MinkaSyu": ("Cargo.toml", "src/lib.rs", "README.md",
                 "dist/minka-syu.desktop", "dist/minka-syu-check.service",
                 "dist/minka-syu-check.timer"),
    "MinkaSyu/gui": ("Cargo.toml", "src/lib.rs"),
    "MinkaThermal": ("Cargo.toml", "src/lib.rs", "dist/minka-thermal.desktop",
                     "dist/minka-thermal.service"),
    "Proustite": ("Cargo.toml", "build.rs", "src/lib.rs", "qmldir", "Proustite.qml"),
    "minka": ("Cargo.toml", "src/main.rs"),
    "tools/keysend": ("Cargo.toml", "src/main.rs"),
    "tools/md3e-trial": ("Cargo.toml", "src/main.rs"),
}
MEMBER_FILES = tuple(f"{member}/{name}" for member, names in MEMBERS.items() for name in names)
# Laid out as the real one is: one quoted member to a line.
CARGO_TOML = ("# fixture: Cargo.toml\n[workspace]\nresolver = \"3\"\nmembers = [\n"
              + "".join(f'    "{member}",\n' for member in MEMBERS)
              + ']\nexclude = [\n    "AntiWebUI",\n    "ShojiWM",\n]\n')

# The stand-in checkout, besides REAL. Every file is mode 644 except these.
CHECKOUT = (
    "README.md",
    "packaging/minka-session",
    "packaging/minka-keep",
    "packaging/minka.desktop",
    # A submodule's .git is a file naming its gitdir, not a directory.
    "ShojiWM/.git",
    "ShojiWM/.idea/workspace.xml",
    "ShojiWM/Cargo.toml",
    "ShojiWM/src/main.rs",
    "ShojiWM/target/release/shoji_wm",  # the TypeScript build: must not ship
    "ShojiWM/target/release/examples/default_config",  # the compositor before minka
    "ShojiWM/target/release/xdg-desktop-portal-shojiwm",
    "ShojiWM/package.json",
    "ShojiWM/node_modules/esbuild/package.json",
    "ShojiWM/packages/config/src/index.tsx",
    # The TypeScript config's copy of a shader the Rust config also reads:
    # only the one under ASSET_ROOT ships as the compositor's.
    "ShojiWM/packages/config/src/effect/backdrop-fade.frag",
    f"{ASSET_ROOT}/shaders/liquid-glass.frag",
    f"{ASSET_ROOT}/shaders/backdrop-fade.frag",
    f"{ASSET_ROOT}/icons/drag-tab-top.svg",
    "ShojiWM/dist/shojiwm.portal",
    "ShojiWM/dist/org.freedesktop.impl.portal.desktop.shojiwm.service",
    "ShojiWM/dist/xdg-desktop-portal-shojiwm.service",
    "xwayland-satellite/.git",
    "xwayland-satellite/Cargo.toml",
    "xwayland-satellite/src/main.rs",
    "xwayland-satellite/target/release/xwayland-satellite",
    # The Cargo workspace and its builds: minka, and the per-app binaries
    # from before it.
    "Cargo.toml",
    "Cargo.lock",
    "target/release/minka",
    "target/debug/minka",
    "target/release/MinkaFX",
    "target/release/minkashell",
    *MEMBER_FILES,
    *(f"{member}/.git" for member in MEMBERS if member not in (
        "MinkaMon/sampler-rs", "MinkaSyu/gui", "minka", "tools/md3e-trial")),
    "MinkaShell/.idea/workspace.xml",
    # The crates' own builds from before the workspace: never shipped.
    "MinkaIPC/target/debug/libminka_ipc.rlib",
    "MinkaShell/target/release/minkashell",
    "MinkaMon/sampler-rs/target/release/minkamon-sampler",
    # Not a workspace member.
    "tools/other/README.md",
    "AntiWebUI/.git",
    "AntiWebUI/Cargo.toml",
    "AntiWebUI/src/anti-webui/Cargo.toml",
    "AntiWebUI/src/anti-webui/src/lib.rs",
    "AntiWebUI/target/debug/libanti_webui.rlib",
    "smithay/.git",
    "smithay/Cargo.toml",
    "smithay/src/lib.rs",
    "MinkaConf/.git",
    "MinkaConf/shell.qml",
    "MinkaConf/dist/MinkaConf.desktop",
    "MinkaLink/.git",
    "MinkaLink/qmldir",
    "MinkaLink/ShojiClient.qml",
)
EXECUTABLE = {
    "packaging/minka-session",
    "packaging/minka-keep",
    "ShojiWM/target/release/shoji_wm",
    "ShojiWM/target/release/examples/default_config",
    "ShojiWM/target/release/xdg-desktop-portal-shojiwm",
    "xwayland-satellite/target/release/xwayland-satellite",
    "target/release/minka",
    "target/debug/minka",
    "target/release/MinkaFX",
    "target/release/minkashell",
    "MinkaShell/target/release/minkashell",
    "MinkaShell/dist/minkashell",
    "MinkaMon/sampler-rs/target/release/minkamon-sampler",
    "MinkaMon/dist/minkamon",
}
# Contents other than "fixture: <path>".
CONTENTS = {
    "target/release/minka": minka_script(),
    "Cargo.toml": CARGO_TOML,
}
# Each QML app reaches the shared singletons through these, as in the real tree.
CHECKOUT_LINKS = {
    "MinkaConf/Proustite": "../Proustite",
    "MinkaShot/Proustite": "../Proustite",
    "MinkaShot/MinkaLink": "../MinkaLink",
    "MinkaLedger/ui/Proustite": "../../Proustite",
    "MinkaLedger/ui/MinkaLink": "../../MinkaLink",
}
# Where each QML tree is installed, from where in the checkout.
QML_APPS = {
    "usr/share/minka/MinkaConf": "MinkaConf",
    "usr/share/minka/MinkaShot": "MinkaShot",
    "usr/share/minka/MinkaLedger": "MinkaLedger/ui",
}
# Each installed singleton link, as installed/<link>.
APP_LINKS = tuple(f"{installed}/{link.removeprefix(source + '/')}"
                  for installed, source in QML_APPS.items()
                  for link in CHECKOUT_LINKS if os.path.dirname(link) == source)

# The tarball under ROOT, but for usr/: every file in it, and the checkout
# file it must be a copy of. Nothing else may be in it.
SHIPPED_SOURCES = (
    "ShojiWM/Cargo.toml",
    "ShojiWM/src/main.rs",
    "ShojiWM/package.json",
    "ShojiWM/packages/config/src/index.tsx",
    "ShojiWM/packages/config/src/effect/backdrop-fade.frag",
    f"{ASSET_ROOT}/shaders/liquid-glass.frag",
    f"{ASSET_ROOT}/shaders/backdrop-fade.frag",
    f"{ASSET_ROOT}/icons/drag-tab-top.svg",
    "ShojiWM/dist/shojiwm.portal",
    "ShojiWM/dist/org.freedesktop.impl.portal.desktop.shojiwm.service",
    "ShojiWM/dist/xdg-desktop-portal-shojiwm.service",
    "xwayland-satellite/Cargo.toml",
    "xwayland-satellite/src/main.rs",
    "smithay/Cargo.toml",
    "smithay/src/lib.rs",
    "AntiWebUI/Cargo.toml",
    "AntiWebUI/src/anti-webui/Cargo.toml",
    "AntiWebUI/src/anti-webui/src/lib.rs",
    "Cargo.toml",
    "Cargo.lock",
    *MEMBER_FILES,
)
TARBALL = {
    **{f"src/{path}": path for path in SHIPPED_SOURCES},
    "install.sh": "packaging/install.sh",
    "uninstall.sh": "packaging/uninstall.sh",
    "README.md": "README.md",
}
# The members' trees keep their links in src/, where nothing reads them:
# the cargo builds compile the theme in.
TARBALL_LINKS = {f"src/{link}": target for link, target in CHECKOUT_LINKS.items()
                 if any(link.startswith(f"{member}/") for member in MEMBERS)}
MODES = {"install.sh": 0o755, "uninstall.sh": 0o755, "README.md": 0o644}

# A string literal naming an asset file in the Rust config's source. All of
# them are relative paths written out whole ("./shaders/x.frag"); one
# that is not would be a path this test cannot follow.
ASSET_STRING = re.compile(r'"([^"\\]*\.(?:frag|vert|glsl|wgsl|svg|png|jpg|webp|ttf|otf))"')


def member_bytes(tar, member):
    """The contents of a regular file in tar, a TarInfo or a name."""
    handle = tar.extractfile(member)
    assert handle is not None, f"{member} is not a regular file"
    return handle.read()


def describe(top):
    """Every entry under top, by its path relative to top: a file's contents
    and mode, a link's target, or that it is a directory."""
    found = {}
    for parent, dirs, names in os.walk(top):
        for name in dirs + names:
            path = os.path.join(parent, name)
            if os.path.islink(path):
                entry = ("link", os.readlink(path))
            elif os.path.isdir(path):
                entry = ("dir",)
            else:
                with open(path, "rb") as handle:
                    entry = ("file", handle.read(), stat.S_IMODE(os.lstat(path).st_mode))
            found[os.path.relpath(path, top)] = entry
    return found


def describe_tar(tar, top):
    """describe() for the members of tar under top."""
    found = {}
    for member in tar.getmembers():
        if not member.name.startswith(f"{top}/"):
            continue
        if member.issym():
            entry = ("link", member.linkname)
        elif member.isdir():
            entry = ("dir",)
        else:
            entry = ("file", member_bytes(tar, member), stat.S_IMODE(member.mode))
        found[member.name.removeprefix(f"{top}/")] = entry
    return found


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


@unittest.skipUnless(shutil.which("rsync"), "the scripts copy trees with rsync")
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

    def run_script(self, *args, script=SCRIPT):
        return subprocess.run([os.path.join(self.repo, script), *args], cwd=self.cwd,
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
            inside = {m.name.removeprefix(f"{ROOT}/"): m for m in members
                      if m.name != ROOT}
            self.assertEqual(sorted({name.split("/")[0] for name in inside}),
                             sorted(["usr", "src", "install.sh", "uninstall.sh", "README.md"]))
            # No build of cargo's ships, under usr/ or src/: minka's own is
            # usr/bin/minka.
            self.assertEqual([name for name in inside if "target" in name.split("/")], [])
            rest = {name: m for name, m in inside.items() if not name.startswith("usr/")}
            files = {name: m for name, m in rest.items() if m.isfile()}
            links = {name: m.linkname for name, m in rest.items() if m.issym()}
            others = [name for name, m in rest.items()
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

    def test_usr_is_what_stage_usr_lays_out(self):
        """Run on its own, as the minkade package runs it: the same files,
        modes and links."""
        staged = os.path.join(self.tmp, "staged")
        done = self.run_script(staged, script=STAGE_USR)
        self.assertEqual(done.returncode, 0, done.stderr)
        with tarfile.open(self.build(VERSION)) as tar:
            shipped = describe_tar(tar, f"{ROOT}/usr")
        expected = describe(os.path.join(staged, "usr"))
        self.assertTrue(expected)
        self.assertEqual(sorted(shipped), sorted(expected))
        self.assertEqual(shipped, expected)

    def test_usr_holds_the_binary_under_every_name(self):
        with tarfile.open(self.build(VERSION)) as tar:
            usr = describe_tar(tar, f"{ROOT}/usr")
        self.assertEqual(usr["bin/minka"],
                         ("file", self.checkout_bytes("target/release/minka"), 0o755))
        for name in NAMES:
            with self.subTest(name=name):
                if name == "minkamon":
                    # Its launcher, which logs its output.
                    self.assertEqual(usr["bin/minkamon"],
                                     ("file", self.checkout_bytes("MinkaMon/dist/minkamon"), 0o755))
                elif name == "xwayland-satellite":
                    # Out of PATH, where minka-session points the compositor.
                    self.assertEqual(usr["lib/minka/xwayland-satellite"], ("link", "../../bin/minka"))
                    self.assertNotIn("bin/xwayland-satellite", usr)
                else:
                    self.assertEqual(usr[f"bin/{name}"], ("link", "minka"))

    def test_shared_singletons_resolve_from_every_app(self):
        out = os.path.join(self.tmp, "unpacked")
        with tarfile.open(self.build(VERSION)) as tar:
            tar.extractall(out, filter="data")
        self.assertEqual(len(APP_LINKS), len(CHECKOUT_LINKS))
        for link in APP_LINKS:
            with self.subTest(link=link):
                self.assertTrue(os.path.isfile(os.path.join(out, ROOT, link, "qmldir")))

    def test_version_defaults_to_0_1(self):
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        with tarfile.open(self.tarball("0.1")) as tar:
            self.assertEqual({name.split("/")[0] for name in tar.getnames()}, {"minkade-0.1"})

    def test_preflight_lists_every_missing_input_and_stages_nothing(self):
        for path in (STAGE_USR, "packaging/uninstall.sh", "README.md"):
            os.remove(os.path.join(self.repo, path))
        os.chmod(os.path.join(self.repo, "target/release/minka"), 0o644)
        shutil.rmtree(os.path.join(self.repo, "smithay"))
        shutil.rmtree(os.path.join(self.repo, "tools/keysend"))  # a workspace member
        before = os.listdir(self.repo)

        done = self.run_script(VERSION)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "")
        self.assertEqual(done.stderr,
                         "missing from this checkout:\n"
                         "   - target/release/minka (release binary)\n"
                         f"   - {STAGE_USR}\n"
                         "   - smithay\n"
                         "   - tools/keysend\n"
                         "   - packaging/uninstall.sh\n"
                         "   - README.md\n")
        self.assertNothingLeftBehind(before)

    def test_a_file_stage_usr_needs_stops_the_build(self):
        """stage-usr.sh checks its own inputs as it goes: what it had staged
        goes with the staging dir."""
        missing = "MinkaThermal/dist/minka-thermal.service"
        os.remove(os.path.join(self.repo, missing))
        before = os.listdir(self.repo)
        done = self.run_script(VERSION)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(missing, done.stderr)
        self.assertNothingLeftBehind(before)

    def test_preflight_refuses_a_binary_without_the_asset_fallback(self):
        """As one built from a ShojiWM commit older than the fallback would be."""
        self.write("target/release/minka", minka_script(fallback=False).encode(), 0o755)
        before = os.listdir(self.repo)
        done = self.run_script(VERSION)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stderr,
                         "missing from this checkout:\n"
                         f"   - target/release/minka (has no {ASSET_FALLBACK} asset fallback: "
                         "is the ShojiWM gitlink current?)\n")
        self.assertNothingLeftBehind(before)

    def test_preflight_refuses_a_cargo_toml_with_no_members(self):
        """A workspace with no members would ship a src/ that builds nothing."""
        self.write("Cargo.toml", b'[workspace]\nmembers = []\n', 0o644)
        before = os.listdir(self.repo)
        done = self.run_script(VERSION)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stderr,
                         "missing from this checkout:\n"
                         "   - Cargo.toml (lists no workspace members)\n")
        self.assertNothingLeftBehind(before)

    def test_members_ship_however_the_list_is_laid_out(self):
        """As `cargo new` leaves it (two members on the last line) and on one
        line before an exclude list: every member ships, and nothing that is
        only excluded does."""
        self.write("MinkaPKG/PKGBUILD", b"fixture: MinkaPKG/PKGBUILD\n", 0o644)
        quoted = [f'"{member}"' for member in MEMBERS]
        layouts = {
            "cargo new": "members = [\n" + "".join(f"    {q},\n" for q in quoted[:-2])
                         + f"    {quoted[-2]}, {quoted[-1]},\n]\n",
            "one line": f"members = [{', '.join(quoted)}]\n",
        }
        for name, members in layouts.items():
            with self.subTest(name):
                self.write("Cargo.toml", ("[workspace]\n" + members
                                          + 'exclude = ["MinkaPKG", "ShojiWM"]\n').encode(),
                           0o644)
                with tarfile.open(self.build(VERSION)) as tar:
                    shipped = set(tar.getnames())
                os.remove(self.tarball())
                self.assertEqual(sorted(m for m in MEMBERS
                                        if f"{ROOT}/src/{m}/Cargo.toml" not in shipped), [])
                self.assertNotIn(f"{ROOT}/src/MinkaPKG", shipped)

    @unittest.skipUnless(os.path.isfile(os.path.join(REPO, "Cargo.toml")),
                         "no workspace Cargo.toml beside this script")
    def test_every_member_of_the_real_workspace_ships(self):
        """Uses the real Cargo.toml, so a member added, or written in a way the
        script does not read, is checked against what lands in src/."""
        with open(os.path.join(REPO, "Cargo.toml"), "rb") as real:
            manifest = real.read()
        members = tomllib.loads(manifest.decode())["workspace"]["members"]
        self.assertTrue(members)
        self.write("Cargo.toml", manifest, 0o644)
        for member in members:
            if not os.path.exists(os.path.join(self.repo, member, "Cargo.toml")):
                self.write(f"{member}/Cargo.toml", f"fixture: {member}/Cargo.toml\n".encode(),
                           0o644)
        with tarfile.open(self.build(VERSION)) as tar:
            shipped = set(tar.getnames())
        self.assertEqual(sorted(m for m in members
                                if f"{ROOT}/src/{m}/Cargo.toml" not in shipped), [])

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
        config is checked against what stage-usr.sh copies."""
        named, odd = config_asset_paths()
        self.assertTrue(named, "found no asset paths in the Rust config: the pattern is stale")
        self.assertEqual(odd, [], "asset paths not written as whole \"./...\" literals")
        shutil.rmtree(os.path.join(self.repo, ASSET_ROOT))
        shutil.copytree(os.path.join(REPO, ASSET_ROOT), os.path.join(self.repo, ASSET_ROOT))
        with tarfile.open(self.build(VERSION)) as tar:
            shipped = set(tar.getnames())
        fallback = f"{ROOT}{ASSET_FALLBACK}"
        self.assertEqual(sorted(p for p in named if f"{fallback}/{p}" not in shipped), [])

    def test_every_tarball_path_the_scripts_read_ships(self):
        """install.sh and uninstall.sh name a few tarball paths of their own,
        such as usr/bin/minka, which a change to the layout could leave
        reading a path that is not there."""
        with tarfile.open(self.build(VERSION)) as tar:
            shipped = {name.removeprefix(f"{ROOT}/") for name in tar.getnames()}
            scripts = "".join(member_bytes(tar, f"{ROOT}/{name}").decode()
                              for name in ("install.sh", "uninstall.sh"))
        # Relative paths only: /usr/bin/minka is where it installs to.
        read = set(re.findall(r"(?<![\w/.-])((?:usr|src)/[\w+-][\w.+-]*(?:/[\w.+-]+)*)",
                              scripts))
        # --from-source builds these itself.
        read = {path for path in read if "/target/" not in path}
        self.assertTrue(read, "found no tarball paths in the scripts: the pattern is stale")
        self.assertEqual(sorted(read - shipped), [])


if __name__ == "__main__":
    unittest.main()
