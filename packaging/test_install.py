"""Tests for install.sh and uninstall.sh: what lands where, and what goes again.

    python3 packaging/test_install.py

Nothing here needs root or touches the machine. Each test builds a tarball
from test_make_tarball's stand-in checkout, unpacks it with tar as a tester
would, and runs its install.sh with stand-ins first on PATH:
- sudo runs the command as this user, with /usr and every path in it moved
  under a temp root, which has a usr/ as a system does. A /usr path inside a longer
  argument stops it, and the tests refuse to run as root, so a path it
  missed reaches the real /usr only as an ordinary user, and fails.
- systemctl only logs, so the session's real portal is never restarted.
- qs, Xwayland, kitty, pipewire and python3 (with Pillow) exist, so no
  dependency prompt asks anything. stdin is closed, so a prompt would fail
  the test.
- cargo, for --from-source, writes a stand-in for each package it is asked
  for where cargo puts a release build's (src/target/release/).
The minka installed is test_make_tarball's stand-in, and MINKA_BIN points the
launchers at it, so a real /usr/bin/minka never runs. MINKA_KEEP points the
shell's and MinkaMon's launchers at the minka-keep installed with it, so a
real /usr/lib/minka/minka-keep never runs either. The one test that leaves
it unset puts a stand-in keeper beside the launchers. HOME and the XDG dirs
are temp dirs too.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_make_tarball import (  # noqa: E402
    NAMES, REPO, ROOT, RUST_CONFIG, VERSION, Checkout, minka_script)

SUDO = """\
n=$#
for arg do
    case $arg in
        /usr|/usr/*) arg=$FAKE_ROOT$arg ;;
        *=/usr|*=/usr/*|*' /usr/'*|*'"/usr/'*|*"'/usr/"*)
            echo "fake sudo: a /usr path inside an argument: $arg" >&2
            exit 99 ;;
    esac
    set -- "$@" "$arg"
done
shift "$n"
exec "$@"
"""

# Where cargo puts a release build of each -p: target/release/<package>,
# which is the binary's name for minka.
FAKE_CARGO = f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
here = os.path.relpath(os.getcwd(), os.environ["FAKE_CARGO_BASE"])
with open(os.environ["FAKE_CARGO_LOG"], "a") as log:
    log.write(json.dumps([here, args]) + "\\n")
for i, arg in enumerate(args):
    if arg == "-p":
        out = "target/release/" + args[i + 1]
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as binary:
            binary.write("built: " + " ".join(args) + " in " + here + "\\n")
        os.chmod(out, 0o755)
"""

# The binary's source in minka/src/main.rs, whose apps! list holds its names.
MINKA_MAIN = os.path.join(REPO, "minka/src/main.rs")

# What --no-portal leaves out, under the fake root.
PORTAL = (
    "usr/bin/xdg-desktop-portal-shojiwm",
    "usr/share/xdg-desktop-portal/portals/shojiwm.portal",
    "usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.shojiwm.service",
    "usr/lib/systemd/user/xdg-desktop-portal-shojiwm.service",
)

# What tarballs from before minka installed, as regular files (with one in
# each tree): binaries one by one, the per-app builds inside the trees, and
# before that the TypeScript runtime and config.
OLDER_INSTALL = (
    "usr/bin/shoji_wm",
    "usr/bin/MinkaFX",
    "usr/bin/MinkaCap",
    "usr/bin/minka-session",
    "usr/bin/minkamon",
    "usr/bin/xdg-desktop-portal-shojiwm",
    "usr/lib/minka/xwayland-satellite",
    "usr/share/wayland-sessions/minka.desktop",
    "usr/share/applications/MinkaConf.desktop",
    "usr/share/applications/MinkaMon.desktop",
    "usr/share/xdg-desktop-portal/portals/shojiwm.portal",
    "usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.shojiwm.service",
    "usr/lib/systemd/user/xdg-desktop-portal-shojiwm.service",
    "usr/share/minka/shojiwm/shaders/removed.frag",
    "usr/share/minka/MinkaShell/minkashell",
    "usr/share/minka/MinkaMon/minkamon",
    "usr/share/minka/MinkaMon/minkamon-sampler",
    # MinkaSyu's QML window, installed up to 0.92.0.
    "usr/share/minka-syu/ui/shell.qml",
    # MinkaShot's QML daemon, installed up to 0.96.0. It ran usr/bin/MinkaCap,
    # above, which is no name of minka's any more.
    "usr/share/minka/MinkaShot/shell.qml",
)
TYPESCRIPT_INSTALL = (
    "usr/lib/shojiwm/runtime/index.js",
    "usr/share/shojiwm/config/index.tsx",
)
# Files of the system's own, beside MinkaDE's: never removed.
UNRELATED = (
    "usr/bin/kitty",
    "usr/share/applications/kitty.desktop",
    "usr/lib/systemd/user/pipewire.service",
    "usr/share/doc/kitty/README",
)


def tree(top):
    """Every file and symlink under top, as paths relative to it."""
    found = set()
    for parent, dirs, names in os.walk(top):
        found.update(os.path.relpath(os.path.join(parent, n), top) for n in names)
        found.update(os.path.relpath(os.path.join(parent, d), top)
                     for d in dirs if os.path.islink(os.path.join(parent, d)))
    return found


def umask():
    mask = os.umask(0)
    os.umask(mask)
    return mask


def real_names():
    """The names the real minka answers to: its apps! list."""
    with open(MINKA_MAIN) as main_rs:
        names = re.findall(r'(?m)^\s*"([^"]+)"\s*=>', main_rs.read())
    assert "shoji_wm" in names, "minka/src/main.rs: the apps! pattern is stale"
    return names


class Install(Checkout):
    def setUp(self):
        if os.geteuid() == 0:
            self.skipTest("as root, the stand-in sudo could not keep a missed path out of /usr")
        super().setUp()
        self.root = os.path.join(self.tmp, "root")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.root, "usr"))
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
                        MINKA_BIN=self.at("/usr/bin/minka"),
                        MINKA_KEEP=self.at("/usr/lib/minka/minka-keep"),
                        FAKE_SYSTEMCTL_LOG=self.systemctl_log, FAKE_CARGO_LOG=self.cargo_log)

    def unpack(self):
        """Build the tarball and unpack it; self.unpacked is its root. The
        stand-ins on PATH are install.sh's: make-tarball.sh gets the real
        tools (it reads Cargo.toml with python3)."""
        stubbed = self.env["PATH"]
        self.env["PATH"] = stubbed.removeprefix(self.bin + os.pathsep)
        try:
            tarball = self.build(VERSION)
        finally:
            self.env["PATH"] = stubbed
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

    def put(self, paths):
        """Files already in the fake root, each naming its own path."""
        for path in paths:
            os.makedirs(os.path.dirname(self.at(path)), exist_ok=True)
            with open(self.at(path), "w") as out:
                out.write(f"already here: {path}\n")

    def shipped(self):
        """Every file and link the tarball's usr/ holds, as fake-root paths."""
        return {f"usr/{path}" for path in tree(os.path.join(self.unpacked, "usr"))}

    def test_prebuilt_install_copies_the_tarballs_usr(self):
        self.install()
        usr = os.path.join(self.unpacked, "usr")
        self.assertEqual(sorted(tree(self.root)), sorted(self.shipped()))
        mask = umask()
        for path in tree(usr):
            source, installed = os.path.join(usr, path), self.at(f"/usr/{path}")
            with self.subTest(path=path):
                if os.path.islink(source):
                    self.assertEqual(os.readlink(installed), os.readlink(source))
                    continue
                self.assertFalse(os.path.islink(installed))
                self.assertEqual(self.read(installed), self.read(source))
                mode = 0o755 if os.stat(source).st_mode & 0o111 else 0o644
                self.assertEqual(oct(stat.S_IMODE(os.stat(installed).st_mode)), oct(mode & ~mask))
        # Every name reaches the binary.
        minka = os.path.realpath(self.at("/usr/bin/minka"))
        for name in NAMES:
            if name != "minkamon":
                where = ("/usr/lib/minka/" if name == "xwayland-satellite" else "/usr/bin/") + name
                with self.subTest(name=name):
                    self.assertEqual(os.path.realpath(self.at(where)), minka)
        for app in ("MinkaConf", "MinkaLedger"):
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
        shipped = os.path.join(self.unpacked, match.group(1).lstrip("/"))
        installed = self.at(match.group(1))
        self.assertEqual(sorted(tree(installed)), sorted(tree(shipped)))
        self.assertTrue(os.path.isdir(os.path.join(installed, "shaders")))

    def test_launchers_start_the_installed_apps(self):
        """The real dist/minkamon, dist/minkashell and minka-keep, run as the
        session runs them, start the installed minka (which says how it was
        started and exits) under their app's name. MINKA_KEEP stands in for
        the launchers' /usr/lib/minka/minka-keep, a path the fake root cannot
        move, and names the minka-keep installed here. The next test runs
        their own lookup."""
        for path in ("MinkaMon/dist/minkamon", "MinkaShell/dist/minkashell",
                     "packaging/minka-keep"):
            with open(os.path.join(REPO, path), "rb") as real:
                self.write(path, real.read(), 0o755)
        self.install()
        minka = self.at("/usr/bin/minka")
        runs = ((["/usr/bin/minkamon", "/usr/share/minka/MinkaMon"], "minkamon", "minkamon"),
                (["/usr/share/minka/MinkaShell/dist/minkashell", "/usr/share/minka/MinkaShell"],
                 "minkashell", "minkashell"),
                # As the session config starts MinkaShot's daemon.
                (["/usr/lib/minka/minka-keep", "minkashot", "daemon"],
                 "minkashot", "minkashot daemon"))
        for (command, *args), name, ran in runs:
            with self.subTest(name=name):
                args = [self.at(a) if a.startswith("/usr/") else a for a in args]
                done = subprocess.run([self.at(command), *args],
                                      env=self.env, stdin=subprocess.DEVNULL,
                                      capture_output=True, text=True, timeout=30)
                self.assertEqual(done.returncode, 0, done.stderr)
                with open(os.path.join(self.home, f".local/state/minka/{name}.log")) as log:
                    self.assertIn(f"minka ran as {minka}: {ran}\n", log.read())

    def test_checkout_launchers_exec_the_keeper_beside_them(self):
        """The session runs a checkout's dist/minkashell and dist/minkamon
        with whatever package is installed, and packages up to 0.93.0 have
        no /usr/lib/minka/minka-keep. So with no MINKA_KEEP each must find
        the minka-keep beside it, packaging/ in the checkout, and exec it:
        the keeper takes its parent for the compositor. A stand-in keeper
        here says its PID and how it was started. MINKA_BIN names the
        checkout's stand-in minka, in case a launcher reaches a real
        installed keeper instead."""
        launchers = (("MinkaShell", "minkashell", "minkashell"),
                     ("MinkaMon", "minkamon", "--once minkamon"))
        for app, launcher, _ in launchers:
            path = f"{app}/dist/{launcher}"
            with open(os.path.join(REPO, path), "rb") as real:
                self.write(path, real.read(), 0o755)
        self.write("packaging/minka-keep",
                   b'#!/bin/sh\nprintf "%s %s %s\\n" "$$" "$0" "$*" >> "$FAKE_KEEPER_LOG"\n',
                   0o755)
        env = dict(self.env, MINKA_BIN=os.path.join(self.repo, "target/release/minka"))
        del env["MINKA_KEEP"]
        for app, launcher, args in launchers:
            with self.subTest(launcher=launcher):
                directory = os.path.join(self.repo, app)
                env["FAKE_KEEPER_LOG"] = os.path.join(self.tmp, f"{launcher}.keeper")
                # As the session config starts it.
                proc = subprocess.Popen([os.path.join(directory, "dist", launcher), directory],
                                        cwd=self.cwd, env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                out, err = proc.communicate(timeout=30)
                # With no keeper, the launcher says why in its app's log.
                log = os.path.join(self.home, f".local/state/minka/{launcher}.log")
                self.assertEqual(proc.returncode, 0,
                                 out + err + (self.read(log).decode() if os.path.exists(log) else ""))
                self.assertTrue(os.path.exists(env["FAKE_KEEPER_LOG"]),
                                "the keeper beside it never ran")
                # The launcher's own PID: it exec'd the keeper.
                keeper = f"{directory}/dist/../../packaging/minka-keep"
                self.assertEqual(self.read(env["FAKE_KEEPER_LOG"]).decode(),
                                 f"{proc.pid} {keeper} {args}\n")
                # The installed keeper is tried after it. A fake root cannot
                # move that path, so read it from the script.
                with open(os.path.join(directory, "dist", launcher)) as script:
                    loop = re.search(r"(?m)^for KEEP in (.+); do$", script.read())
                self.assertIsNotNone(loop, "the keeper loop is stale")
                self.assertEqual(loop.group(1).split()[-1], "/usr/lib/minka/minka-keep")

    @unittest.skipUnless(os.path.isdir(RUST_CONFIG) and os.path.isfile(MINKA_MAIN),
                         "no Rust config or minka checkout beside this script")
    def test_the_session_finds_what_was_installed(self):
        """minka-session, the Rust config's defaults, the launchers and the apps
        name /usr paths, which a tester's environment does not override: each
        must be one install.sh fills. minka answers to its real names here."""
        launchers = ("MinkaMon/dist/minkamon", "MinkaShell/dist/minkashell",
                     "packaging/minka-keep")
        for path in ("packaging/minka-session", *launchers):
            with open(os.path.join(REPO, path), "rb") as real:
                self.write(path, real.read(), 0o755)
        self.write("target/release/minka", minka_script(real_names()).encode(), 0o755)
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
            config = session_rs.read()
        named |= set(re.findall(r'\$\{MINKA_\w+:-(/usr/[^}"\\]+)\}', config))
        # What it execs by its full path, such as minka-keep.
        named |= set(re.findall(r'"exec (/usr/[\w./-]+)', config))
        # The keeper the launchers exec, and the minka it runs.
        for launcher in launchers:
            with open(os.path.join(REPO, launcher)) as script:
                named |= {path.rstrip(".") for path in re.findall(r"/usr/[\w./-]+", script.read())}
        # The AntiWebUI apps' own fallbacks, such as the shell's assets.
        for app in ("MinkaShell", "MinkaMon"):
            for name in os.listdir(os.path.join(REPO, app, "src")):
                if not name.endswith(".rs"):
                    continue
                with open(os.path.join(REPO, app, "src", name)) as source:
                    named |= set(re.findall(r'"(/usr/share/minka/[^"]+)"', source.read()))
        for path in ("/usr/share/minka/MinkaShell", "/usr/lib/minka/minka-keep",
                     "/usr/bin/minka", "/usr/share/minka/MinkaShell/assets"):
            self.assertIn(path, named, "the patterns are stale")
        self.assertEqual(sorted(p for p in named if not os.path.exists(self.at(p))), [])
        # MinkaCap went with MinkaShot's QML daemon: the real minka has no
        # such name, so nothing installs its link again.
        self.assertFalse(os.path.lexists(self.at("/usr/bin/MinkaCap")),
                         "minka answers to MinkaCap again, or stage-usr.sh links it")
        # The session runs the shell's launcher only if it is executable.
        self.assertTrue(os.access(self.at("/usr/share/minka/MinkaShell/dist/minkashell"), os.X_OK))

    def test_from_source_installs_what_cargo_built(self):
        self.install("--from-source")
        with open(self.cargo_log) as log:
            self.assertEqual([json.loads(line) for line in log],
                             [["src", ["build", "--release", "-p", "minka"]]])
        installed = self.at("/usr/bin/minka")
        self.assertEqual(self.read(installed).decode(), "built: build --release -p minka in src\n")
        self.assertEqual(oct(stat.S_IMODE(os.stat(installed).st_mode)), oct(0o755 & ~umask()))
        # The rest is the tarball's, the links to the binary among it.
        self.assertEqual(sorted(tree(self.root)), sorted(self.shipped()))
        self.assertEqual(os.readlink(self.at("/usr/bin/shoji_wm")), "minka")

    def test_no_portal_leaves_out_the_portal_backend(self):
        self.install("--no-portal")
        installed = tree(self.root)
        self.assertEqual(sorted(self.shipped() - installed), sorted(PORTAL))
        self.assertEqual(sorted(installed - self.shipped()), [])
        self.assertFalse(os.path.exists(
            os.path.join(self.home, ".config/xdg-desktop-portal/shojiwm-portals.conf")))

    def test_reinstalling_replaces_every_tree(self):
        """A file an older install left in a tree must not outlive the next
        install, or a removed QML file keeps loading."""
        self.install()
        stale = []
        for parent, dirs, _ in os.walk(self.at("/usr")):
            for name in dirs:
                path = os.path.join(parent, name)
                below = os.path.relpath(path, self.at("/usr")).split(os.sep)
                if not os.path.islink(path) and any(p.startswith("minka") for p in below):
                    stale.append(os.path.join(path, "stale.qml"))
        self.assertIn(self.at("/usr/share/minka/MinkaShell/stale.qml"), stale)
        for path in stale:
            with open(path, "w") as out:
                out.write("// from an older install\n")
        done = self.run_unpacked("install.sh")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual([p for p in stale if os.path.exists(p)], [])

    def test_install_replaces_an_older_tarballs_files(self):
        """Binaries are links now where they were files, and the trees hold
        no binaries. The TypeScript runtime is named, not removed."""
        self.put(OLDER_INSTALL + TYPESCRIPT_INSTALL + UNRELATED)
        self.install()
        self.assertEqual(sorted(tree(self.root)),
                         sorted(self.shipped() | set(TYPESCRIPT_INSTALL) | set(UNRELATED)))
        self.assertEqual(os.readlink(self.at("/usr/bin/shoji_wm")), "minka")
        self.assertEqual(os.readlink(self.at("/usr/lib/minka/xwayland-satellite")),
                         "../../bin/minka")

    def test_uninstall_removes_everything_install_put_there(self):
        self.put(UNRELATED)
        self.install()
        done = self.run_unpacked("uninstall.sh")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(sorted(tree(self.root)), sorted(UNRELATED))
        self.assertFalse(os.path.exists(
            os.path.join(self.home, ".config/xdg-desktop-portal/shojiwm-portals.conf")))
        self.assertIn("minka-settings.json was kept", done.stdout)

    def test_uninstall_removes_an_older_tarballs_install(self):
        self.put(OLDER_INSTALL + TYPESCRIPT_INSTALL + UNRELATED)
        self.unpack()
        done = self.run_unpacked("uninstall.sh")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(sorted(tree(self.root)), sorted(UNRELATED))

    def test_uninstall_needs_the_usr_tree_beside_it(self):
        self.put(UNRELATED)
        self.unpack()
        shutil.rmtree(os.path.join(self.unpacked, "usr"))
        done = self.run_unpacked("uninstall.sh")
        self.assertEqual(done.returncode, 1)
        self.assertIn("no usr/ beside uninstall.sh", done.stderr)
        self.assertEqual(sorted(tree(self.root)), sorted(UNRELATED))

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
