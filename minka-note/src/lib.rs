//! One log line, one write(2), for every MinkaDE app.
//!
//! `note!` takes what `eprintln!` takes and writes the same text to stderr,
//! but it formats the whole line first and hands it to the kernel in one
//! write. stderr has no buffer, so `eprintln!` makes a write(2) for each
//! piece of its format string, and more for a value that formats itself in
//! pieces (an `io::Error` does). That costs about four times as much (about
//! 1.5-2 µs a line against 0.45 µs, measured 20/11/2026), and when another
//! writer shares the file (Mesa, say) its output can land in the middle of
//! the line. `eprintln!` also panics when the write fails, on a full disk for
//! one; a failed write here is dropped.
//!
//! Unlike `eprintln!`, it is not captured by the test harness: a `note!` that
//! a test reaches prints in `cargo test`'s output.

/// Writes one line to stderr with a single write(2). Takes what `eprintln!`
/// takes.
#[macro_export]
macro_rules! note {
    ($($arg:tt)*) => {
        $crate::line(::core::format_args!($($arg)*))
    };
}

/// What `note!` calls: `text` and a newline, as one write(2).
pub fn line(text: std::fmt::Arguments) {
    use std::fmt::Write as _;
    use std::io::Write as _;
    let mut buf = String::with_capacity(128);
    let _ = buf.write_fmt(text);
    buf.push('\n');
    let _ = std::io::stderr().write_all(buf.as_bytes());
}
