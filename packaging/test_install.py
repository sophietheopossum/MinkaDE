"""Tests for install.sh and uninstall.sh: what lands where, and what goes again.

    python3 packaging/test_install.py

Nothing here needs root or touches the machine. Each test builds a tarball
from test_make_tarball's stand-in checkout, unpacks it with tar as a tester
would, and runs its install.sh with stand-ins first on PATH:
- sudo runs the command as this user, with every /usr path moved under a temp
  root. A /usr path inside a longer argument stops it, and the tests refuse
  to run as root, so a path it missed reaches the real /usr only as an
  ordinary user, and fails.
- systemctl only logs, so the session's real portal is never restarted.
- qs, Xwayland, kitty, pipewire and python3 (with Pillow) exist, so no
  dependency prompt asks anything. stdin is closed, so a prompt would fail
  the test.
- cargo, for --from-source, writes a stand-in for each binary where cargo puts
  a release build's.
HOME and the XDG dirs are temp dirs too.
"""

import json
import os
import re
import stat
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_make_tarball import REPO, ROOT, RUST_CONFIG, VERSION, Checkout  # noqa: E402

SUDO = """\
n=$#
for arg do
    case $arg in
        /usr/*) arg=$FAKE_ROOT$arg ;;
        *=/usr/*|*' /usr/'*|*'"/usr/'*|*"'/usr/"*)
            echo "fake sudo: a /usr path inside an argument: $arg" >&2
            exit 99 ;;
    esac
    set -- "$@" "$arg"
done
shift "$n"
exec "$@"
"""

# Where cargo puts a release build: target/release/examples/<name> for an
# example, else target/release/<binary>. Binaries by package (-p) or, for a
# plain build, by the crate's directory, as the real crates name them.
FAKE_CARGO = f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
here = os.path.relpath(os.getcwd(), os.environ["FAKE_CARGO_BASE"])
with open(os.environ["FAKE_CARGO_LOG"], "a") as log:
    log.write(json.dumps([here, args]) + "\\n")
if "--example" in args:
    out = "target/release/examples/" + args[args.index("--example") + 1]
elif "-p" in args:
    out = "target/release/" + args[args.index("-p") + 1]
else:
    out = "target/release/" + {{"xwayland-satellite": "xwayland-satellite", "MinkaFX": "MinkaFX",
                               "MinkaCap": "MinkaCap", "MinkaMon": "minkamon",
                               "sampler-rs": "minkamon-sampler",
                               "MinkaShell": "minkashell"}}[os.path.basename(here)]
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w") as binary:
    binary.write("built: " + " ".join(args) + " in " + here + "\\n")
os.chmod(out, 0o755)
"""

# Each file install.sh installs on its own, from the unpacked tarball, with
# its mode. The app trees and assets under minka/ are copied whole.
INSTALLED = {
    "/usr/bin/shoji_wm": ("bin/shoji_wm", 0o755),
    "/usr/bin/MinkaFX": ("bin/MinkaFX", 0o755),
    "/usr/bin/MinkaCap": ("bin/MinkaCap", 0o755),
    "/usr/lib/minka/xwayland-satellite": ("bin/xwayland-satellite", 0o755),
    "/usr/bin/xdg-desktop-portal-shojiwm": ("bin/xdg-desktop-portal-shojiwm", 0o755),
    "/usr/bin/minka-session": ("dist/minka-session", 0o755),
    "/usr/bin/minkamon": ("dist/minkamon", 0o755),
    "/usr/share/wayland-sessions/minka.desktop": ("dist/minka.desktop", 0o644),
    "/usr/share/applications/MinkaConf.desktop": ("dist/MinkaConf.desktop", 0o644),
    "/usr/share/applications/MinkaMon.desktop": ("dist/MinkaMon.desktop", 0o644),
    "/usr/share/xdg-desktop-portal/portals/shojiwm.portal": ("dist/shojiwm.portal", 0o644),
    "/usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.shojiwm.service":
        ("dist/org.freedesktop.impl.portal.desktop.shojiwm.service", 0o644),
    "/usr/lib/systemd/user/xdg-desktop-portal-shojiwm.service":
        ("dist/xdg-desktop-portal-shojiwm.service", 0o644),
    "/usr/share/minka/MinkaMon/minkamon": ("minka/MinkaMon/minkamon", 0o755),
    "/usr/share/minka/MinkaMon/minkamon-sampler": ("minka/MinkaMon/minkamon-sampler", 0o755),
    "/usr/share/minka/MinkaShell/minkashell": ("minka/MinkaShell/minkashell", 0o755),
    "/usr/share/minka/MinkaShell/dist/minkashell": ("minka/MinkaShell/dist/minkashell", 0o755),
}

# What --from-source asks cargo for, in order, and from where.
CARGO_BUILDS = [
    ["src/ShojiWM", ["build", "--release", "-p", "shojiwm_rs", "--example", "default_config"]],
    ["src/ShojiWM", ["build", "--release", "-p", "xdg-desktop-portal-shojiwm"]],
    ["src/xwayland-satellite", ["build", "--release"]],
    ["src/MinkaFX", ["build", "--release"]],
    ["src/MinkaCap", ["build", "--release"]],
    ["src/MinkaMon", ["build", "--release"]],
    ["src/MinkaMon/sampler-rs", ["build", "--release"]],
    ["src/MinkaShell", ["build", "--release"]],
]


def tree(top):
    """Every file and symlink under top, as paths relative to it."""
    found = set()
    for parent, dirs, names in os.walk(top):
        found.update(os.path.relpath(os.path.join(parent, n), top) for n in names)
        found.update(os.path.relpath(os.path.join(parent, d), top)
                     for d in dirs if os.path.islink(os.path.join(parent, d)))
    return found


class Install(Checkout):
    def setUp(self):
        if os.geteuid() == 0:
            self.skipTest("as root, the stand-in sudo could not keep a missed path out of /usr")
        super().setUp()
        self.root = os.path.join(self.tmp, "root")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.root)
        os.makedirs(self.home)
        self.systemctl_log = os.path.join(self.tmp, "systemctl.log")
        self.cargo_log = os.path.join(self.tmp, "cargo.log")
        self.stub("sudo", SUDO)
        for name in ("qs", "Xwayland", "kitty", "pipewire", "python3"):
            self.stub(name, "exit 0\n")
        self.stub("systemctl", 'printf "%s\\n" "$*" >> "$FAKE_SYSTEMCTL_LOG"\n')
        cargo = os.path.join(self.bin, "cargo")
        with open(cargo, "w") as out:
            out.write(FAKE_CARGO)
        os.chmod(cargo, 0o755)
        self.env.update(FAKE_ROOT=self.root, HOME=self.home,
                        XDG_CONFIG_HOME=os.path.join(self.home, ".config"),
                        XDG_STATE_HOME=os.path.join(self.home, ".local/state"),
                        FAKE_SYSTEMCTL_LOG=self.systemctl_log, FAKE_CARGO_LOG=self.cargo_log)

    def unpack(self):
        """Build the tarball and unpack it; self.unpacked is its root."""
        tarball = self.build(VERSION)
        out = os.path.join(self.tmp, "unpacked")
        os.makedirs(out)
        subprocess.run(["tar", "-xzf", tarball, "-C", out], check=True)
        self.unpacked = os.path.join(out, ROOT)
        self.env["FAKE_CARGO_BASE"] = self.unpacked

    def run_unpacked(self, script, *args):
        return subprocess.run([os.path.join(self.unpacked, script), *args], cwd=self.cwd,
                              env=self.env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=120)

    def install(self, *args):
        self.unpack()
        done = self.run_unpacked("install.sh", *args)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done

    def at(self, path):
        """Where install.sh's /usr path landed."""
        return os.path.join(self.root, path.lstrip("/"))

    def read(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def test_prebuilt_install_puts_each_file_in_place(self):
        self.install()
        minka = os.path.join(self.unpacked, "minka")
        expected = {path.lstrip("/") for path in INSTALLED}
        expected |= {f"usr/share/minka/{path}" for path in tree(minka)}
        self.assertEqual(sorted(tree(self.root)), sorted(expected))

        for path, (source, mode) in INSTALLED.items():
            with self.subTest(path=path):
                self.assertEqual(self.read(self.at(path)),
                                 self.read(os.path.join(self.unpacked, source)))
                self.assertEqual(oct(stat.S_IMODE(os.stat(self.at(path)).st_mode)), oct(mode))
        for path in tree(minka):
            if not os.path.islink(os.path.join(minka, path)):
                with self.subTest(path=path):
                    self.assertEqual(self.read(self.at(f"/usr/share/minka/{path}")),
                                     self.read(os.path.join(minka, path)))
        for app in ("MinkaShell", "MinkaConf", "MinkaMon"):
            with self.subTest(app=app):
                self.assertTrue(os.path.isfile(self.at(f"/usr/share/minka/{app}/Proustite/qmldir")))

    def test_prebuilt_install_writes_no_typescript_config(self):
        self.install()
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config/shojiwm")))
        self.assertTrue(os.path.isfile(
            os.path.join(self.home, ".config/xdg-desktop-portal/shojiwm-portals.conf")))
        # Only the user's own systemd, never the system's.
        with open(self.systemctl_log) as log:
            calls = log.read().splitlines()
        self.assertTrue(calls)
        self.assertEqual([c for c in calls if not c.startswith("--user ")], [])

    def test_install_points_at_an_older_tarballs_config(self):
        old = os.path.join(self.home, ".config/shojiwm/src")
        os.makedirs(old)
        done = self.install()
        self.assertIn(f"   {self.home}/.config/shojiwm\n", done.stdout)
        self.assertTrue(os.path.isdir(old), "it is the tester's to delete")

    @unittest.skipUnless(os.path.isdir(RUST_CONFIG), "no Rust config checkout beside this script")
    def test_the_rust_config_finds_its_installed_assets(self):
        """The compositor falls back to a fixed path when its build checkout is
        gone; this is the path install.sh must fill."""
        with open(os.path.join(RUST_CONFIG, "minka/session.rs")) as session_rs:
            match = re.search(r'const INSTALLED_ASSETS: &str = "([^"]+)";', session_rs.read())
        assert match is not None, "minka/session.rs no longer names INSTALLED_ASSETS"
        self.install()
        shipped = os.path.join(self.unpacked, "minka/shojiwm")
        installed = self.at(match.group(1))
        self.assertEqual(sorted(tree(installed)), sorted(tree(shipped)))
        self.assertTrue(os.path.isdir(os.path.join(installed, "shaders")))

    def test_launchers_start_the_installed_apps(self):
        """The real dist/minkamon and dist/minkashell, run as the session runs
        them, against installed trees whose binaries report how they were
        started and exit."""
        for app, launcher in (("MinkaMon", "dist/minkamon"), ("MinkaShell", "dist/minkashell")):
            with open(os.path.join(REPO, app, launcher), "rb") as real:
                self.write(f"{app}/{launcher}", real.read(), 0o755)
        for binary in ("MinkaMon/target/release/minkamon", "MinkaShell/target/release/minkashell"):
            self.write(binary, f'#!/bin/sh\necho "{os.path.basename(binary)} ran as $0"\n'.encode(),
                       0o755)
        self.install()
        runs = (("/usr/bin/minkamon", "MinkaMon", "minkamon"),
                ("/usr/share/minka/MinkaShell/dist/minkashell", "MinkaShell", "minkashell"))
        for command, app, binary in runs:
            with self.subTest(app=app):
                tree_dir = self.at(f"/usr/share/minka/{app}")
                done = subprocess.run([self.at(command), tree_dir], env=self.env,
                                      stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                      timeout=30)
                self.assertEqual(done.returncode, 0, done.stderr)
                with open(os.path.join(self.home, f".local/state/minka/{binary}.log")) as log:
                    self.assertIn(f"{binary} ran as {tree_dir}/{binary}\n", log.read())
        # MinkaMon looks for its sampler beside itself (MinkaMon/src/state.rs).
        self.assertTrue(os.access(self.at("/usr/share/minka/MinkaMon/minkamon-sampler"), os.X_OK))

    @unittest.skipUnless(os.path.isdir(RUST_CONFIG), "no Rust config checkout beside this script")
    def test_the_session_finds_what_was_installed(self):
        """minka-session, the Rust config's defaults, the launchers and the apps
        name /usr paths, which a tester's environment does not override: each
        must be one install.sh fills."""
        launchers = ("MinkaMon/dist/minkamon", "MinkaShell/dist/minkashell")
        for path in ("packaging/minka-session", *launchers, "MinkaShot/services/ShotState.qml"):
            with open(os.path.join(REPO, path), "rb") as real:
                self.write(path, real.read(), 0o755)
        self.install()
        with open(self.at("/usr/bin/minka-session")) as session:
            wrapper = session.read()
        # Without --tty the Rust config takes the session for a nested one and
        # starts none of the desktop's apps.
        self.assertRegex(wrapper, r"(?m)^exec /usr/bin/shoji_wm\b.*\s--tty\b")
        # Without it, apps started from the AntiWebUI shell's menu see only
        # the compositor GPU's Vulkan drivers.
        self.assertRegex(wrapper, r"(?m)^export ANTIWEBUI_VULKAN_DRIVERS=all$")
        named = set(re.findall(r"/usr/[\w./-]+", wrapper))
        with open(os.path.join(RUST_CONFIG, "minka/session.rs")) as session_rs:
            named |= set(re.findall(r'\$\{MINKA_\w+:-(/usr/[^}"\\]+)\}', session_rs.read()))
        for launcher in launchers:
            with open(os.path.join(REPO, launcher)) as script:
                named |= set(re.findall(r'\$\{1:-(/usr/[^}"]+)\}', script.read()))
        # MinkaShot's default for MinkaCap: env("MINKA_CAP_BIN") || "/usr/bin/MinkaCap".
        with open(os.path.join(REPO, "MinkaShot/services/ShotState.qml")) as qml:
            named |= set(re.findall(r'\|\|\s*"(/usr/[^"]+)"', qml.read()))
        # The AntiWebUI apps' own fallbacks, such as the shell's assets.
        for app in ("MinkaShell", "MinkaMon"):
            for name in os.listdir(os.path.join(REPO, app, "src")):
                if not name.endswith(".rs"):
                    continue
                with open(os.path.join(REPO, app, "src", name)) as source:
                    named |= set(re.findall(r'"(/usr/share/minka/[^"]+)"', source.read()))
        for path in ("/usr/share/minka/MinkaShell", "/usr/share/minka/MinkaShot",
                     "/usr/bin/MinkaCap", "/usr/share/minka/MinkaShell/assets"):
            self.assertIn(path, named, "the patterns are stale")
        self.assertEqual(sorted(p for p in named if not os.path.exists(self.at(p))), [])
        # The session runs the shell's launcher only if it is executable.
        self.assertTrue(os.access(self.at("/usr/share/minka/MinkaShell/dist/minkashell"), os.X_OK))

    def test_from_source_installs_what_cargo_built(self):
        self.install("--from-source")
        with open(self.cargo_log) as log:
            self.assertEqual([json.loads(line) for line in log], CARGO_BUILDS)
        built = {
            "/usr/bin/shoji_wm": "-p shojiwm_rs --example default_config in src/ShojiWM",
            "/usr/bin/xdg-desktop-portal-shojiwm": "-p xdg-desktop-portal-shojiwm in src/ShojiWM",
            "/usr/lib/minka/xwayland-satellite": "in src/xwayland-satellite",
            "/usr/bin/MinkaFX": "in src/MinkaFX",
            "/usr/bin/MinkaCap": "in src/MinkaCap",
            "/usr/share/minka/MinkaShell/minkashell": "in src/MinkaShell",
            "/usr/share/minka/MinkaMon/minkamon": "in src/MinkaMon",
            "/usr/share/minka/MinkaMon/minkamon-sampler": "in src/MinkaMon/sampler-rs",
        }
        for path, build in built.items():
            with self.subTest(path=path):
                self.assertEqual(self.read(self.at(path)).decode(), f"built: build --release {build}\n")

    def test_reinstalling_replaces_every_tree(self):
        """A file an older install left in a tree must not outlive the next
        install, or a removed QML file keeps loading."""
        self.install()
        trees = sorted(os.listdir(self.at("/usr/share/minka")))
        for name in trees:
            with open(self.at(f"/usr/share/minka/{name}/stale.qml"), "w") as stale:
                stale.write("// from an older install\n")
        done = self.run_unpacked("install.sh")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual([n for n in trees if os.path.exists(self.at(f"/usr/share/minka/{n}/stale.qml"))],
                         [])

    def test_uninstall_removes_everything_install_put_there(self):
        self.install()
        done = self.run_unpacked("uninstall.sh")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(sorted(tree(self.root)), [])
        self.assertFalse(os.path.exists(
            os.path.join(self.home, ".config/xdg-desktop-portal/shojiwm-portals.conf")))
        self.assertIn("minka-settings.json was kept", done.stdout)

    def test_uninstall_points_at_an_older_tarballs_config(self):
        self.unpack()
        old = os.path.join(self.home, ".config/shojiwm/src")
        os.makedirs(old)
        done = self.run_unpacked("uninstall.sh")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(f"{self.home}/.config/shojiwm is from an older tarball", done.stdout)
        self.assertIn("MinkaDE does not use it", done.stdout)
        self.assertTrue(os.path.isdir(old), "it is the tester's to delete")


if __name__ == "__main__":
    unittest.main()
