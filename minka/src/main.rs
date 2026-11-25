//! MinkaDE's one binary. The compositor (Minka's ShojiWM config), its portal
//! backend, xwayland-satellite, the shell, every Minka app and the dev tools
//! (bartizan-lsp, keysend) are libraries linked into it, so what they share
//! is on disk once and, since every process maps this same file, in memory
//! once.
//!
//! Which of them a process is depends on the name it was started as:
//! /usr/bin/shoji_wm, /usr/bin/minkashell and the rest are links to this
//! file. `minka <name> [args]` does the same for a caller that cannot set
//! argv[0], such as a terminal or a program starting /proc/self/exe; the app
//! is started again under its own name, so it reads its arguments as it
//! always has. `minka --names` prints the names, one per line, and `minka`
//! with anything else lists them on stderr: it never starts the compositor,
//! or anything else, unasked.

use std::ffi::{OsStr, OsString};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::process::CommandExt;
use std::process::{ExitCode, Termination};

/// The names, in one place: each runs its app's `main`.
macro_rules! apps {
    ($($name:literal => $main:expr,)*) => {
        /// Every name this binary answers to.
        pub const NAMES: &[&str] = &[$($name),*];

        fn run(name: &[u8]) -> Option<ExitCode> {
            $(if name == $name.as_bytes() {
                set_process_name($name);
                return Some($main.report());
            })*
            None
        }
    };
}

apps! {
    "shoji_wm" => shojiwm_minka::main(),
    "xdg-desktop-portal-shojiwm" => xdg_desktop_portal_shojiwm::main(),
    "xwayland-satellite" => xwayland_satellite::cli::main(),
    "MinkaFX" => minka_fx::main(),
    // MinkaCap was its own crate; the name stays while the QML MinkaShot runs it.
    "MinkaCap" => minkashot::cap_main(),
    "minkashell" => minkashell::main(),
    "minkashot" => minkashot::main(),
    "minkamon" => minkamon::main(),
    "minkamon-sampler" => minkamon_sampler::main(),
    "minka-syu" => minka_syu::main(),
    "minka-syu-gui" => minka_syu_gui::main(),
    "minka-thermal" => minka_thermal::main(),
    "minka-ledger" => minka_ledger::main(),
    "bartizan-lsp" => bartizan_lsp::main(),
    "keysend" => keysend::main(),
}

fn main() -> ExitCode {
    let mut args = std::env::args_os();
    let argv0 = args.next().unwrap_or_default();
    if let Some(code) = run(base_name(&argv0)) {
        return code;
    }
    match args.next() {
        // One per line, for packaging/stage-usr.sh to make the links.
        Some(flag) if flag == "--names" => {
            print!("{}", NAMES.iter().map(|n| format!("{n}\n")).collect::<String>());
            ExitCode::SUCCESS
        }
        Some(name) if NAMES.iter().any(|n| n.as_bytes() == name.as_bytes()) => {
            let err = std::process::Command::new("/proc/self/exe")
                .arg0(&name)
                .args(args)
                .exec();
            minka_note::note!("minka: cannot start {}: {err}", name.to_string_lossy());
            ExitCode::FAILURE
        }
        other => {
            usage(other);
            ExitCode::from(2)
        }
    }
}

/// The file name of a path, as the name a process was started as.
fn base_name(path: &OsStr) -> &[u8] {
    let bytes = path.as_bytes();
    match bytes.iter().rposition(|b| *b == b'/') {
        Some(i) => &bytes[i + 1..],
        None => bytes,
    }
}

fn usage(asked: Option<OsString>) {
    let mut text = String::new();
    if let Some(name) = asked {
        text.push_str(&format!("minka: no app named {}\n", name.to_string_lossy()));
    }
    text.push_str("minka: MinkaDE's one binary. Run it as one of these names (a link to it), or as `minka <name> [args]`:\n");
    for name in NAMES {
        text.push_str("  ");
        text.push_str(name);
        text.push('\n');
    }
    eprint!("{text}");
}

/// The name `ps`, `top` and `pgrep -x` show. A start through a link already
/// has it; a start through /proc/self/exe would show "exe". The kernel keeps
/// the first 15 bytes, as it does of a file name.
fn set_process_name(name: &str) {
    let mut buf = [0u8; 16];
    let len = name.len().min(15);
    buf[..len].copy_from_slice(&name.as_bytes()[..len]);
    // SAFETY: PR_SET_NAME reads at most 16 bytes from a NUL-terminated buffer.
    unsafe { libc::prctl(libc::PR_SET_NAME, buf.as_ptr() as libc::c_ulong, 0, 0, 0) };
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_name_is_the_file_name_of_argv0() {
        assert_eq!(base_name(OsStr::new("/usr/bin/minkashell")), b"minkashell");
        assert_eq!(base_name(OsStr::new("minkashell")), b"minkashell");
        assert_eq!(base_name(OsStr::new("/proc/self/exe")), b"exe");
        assert_eq!(base_name(OsStr::new("")), b"");
    }

    #[test]
    fn names_are_unique_file_names() {
        for (i, name) in NAMES.iter().enumerate() {
            assert!(!name.is_empty() && !name.contains('/'), "{name}");
            assert!(!NAMES[..i].contains(name), "{name} twice");
        }
    }

    #[test]
    fn the_binary_itself_is_not_an_app() {
        for name in ["minka", "exe", "", "--version", "--help", "--names"] {
            assert!(!NAMES.contains(&name), "{name}");
        }
    }
}
