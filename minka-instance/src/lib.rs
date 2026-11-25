//! One instance of a MinkaDE app per session, and one line of conversation
//! with it.
//!
//! The first instance binds `$XDG_RUNTIME_DIR/<name>.lock`, a Unix socket,
//! and writes `<pid>\n` on every connection before anything else. A second
//! launch connects, reads a live PID that is not its own, closes, and exits
//! as a duplicate. The holder hears that close as [`Event::TurnedAway`]
//! (MinkaMon and MinkaSyu raise their window on it). A client with
//! something to ask sends one line after the PID and reads one line back
//! ([`request`]); the holder sees it as [`Event::Request`] and answers from
//! whichever thread it likes.
//!
//! The PID line is the whole of the older protocol, so builds on either
//! side of this crate recognise each other: an old probe reads the PID and
//! closes, and an old holder writes the PID and closes.

use std::fmt;
use std::io::{self, ErrorKind, Read, Write};
use std::os::unix::fs::FileTypeExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::mpsc::{Receiver, SyncSender, sync_channel};
use std::time::{Duration, Instant};

/// How long a launch waits for the holder's PID line: the 1.2 s the older
/// guards gave a slow holder over two probes. The holder writes it as it
/// accepts, so only a holder swapped out or wedged takes this long.
const PROBE_WAIT: Duration = Duration::from_millis(1200);
/// How long the holder waits for a client's request after its PID line.
const REQUEST_WAIT: Duration = Duration::from_secs(2);
/// How long an answer may take to write. One short line goes into the
/// socket buffer at once; this is for a client that stopped reading.
const ANSWER_WAIT: Duration = Duration::from_secs(1);
/// The longest line either side reads.
const LINE_LIMIT: usize = 4096;
/// Events kept for an app that reads them late or never. Beyond these an
/// event is dropped, and a request's connection closes unanswered.
const QUEUE: usize = 16;

#[derive(Debug)]
pub enum Claim {
    /// This process is the instance. Also returned when there is no guard
    /// to hold (no `XDG_RUNTIME_DIR`, or a lock that cannot be bound):
    /// running unguarded beats not starting.
    Primary(Instance),
    /// Another instance is running, and this launch should exit.
    Duplicate,
}

/// The running instance's end. The lock is held, and every connection
/// answered with the PID, until the process exits, whether this is kept or
/// not: dropping it only drops the events.
#[derive(Debug)]
pub struct Instance {
    events: Receiver<Event>,
}

impl Instance {
    /// What clients did, as each finished. Disconnected from the start when
    /// the app runs unguarded.
    pub fn events(&self) -> &Receiver<Event> {
        &self.events
    }

    fn unguarded() -> Self {
        let (_, events) = sync_channel(0);
        Self { events }
    }
}

#[derive(Debug)]
pub enum Event {
    /// A connection closed without a request: a launch that found this one
    /// running and gave way.
    TurnedAway,
    /// A client's line, waiting for its answer.
    Request(Request),
}

/// A request line and the connection to answer it on. It can be sent to
/// another thread. Dropped unanswered, it closes the connection, and the
/// client gets [`RequestError::HungUp`].
#[derive(Debug)]
pub struct Request {
    line: String,
    stream: UnixStream,
}

impl Request {
    pub fn line(&self) -> &str {
        &self.line
    }

    /// Sends `answer`, one line without its newline, and closes the
    /// connection. A client that has gone is not an error.
    pub fn answer(mut self, answer: &str) {
        let _ = self.stream.set_write_timeout(Some(ANSWER_WAIT));
        let _ = self.stream.write_all(format!("{answer}\n").as_bytes());
    }
}

/// Claims `$XDG_RUNTIME_DIR/<name>.lock` for this process. The holder
/// answers from the moment this returns.
pub fn claim(name: &str) -> Claim {
    match lock_path(name) {
        Some(path) => claim_at(&path, name, std::process::id()),
        None => unguarded(name, "XDG_RUNTIME_DIR is not set"),
    }
}

fn lock_path(name: &str) -> Option<PathBuf> {
    std::env::var_os("XDG_RUNTIME_DIR")
        .filter(|dir| !dir.is_empty())
        .map(|dir| PathBuf::from(dir).join(format!("{name}.lock")))
}

fn unguarded(name: &str, why: impl fmt::Display) -> Claim {
    minka_note::note!("{name}: running without the single-instance guard: {why}");
    Claim::Primary(Instance::unguarded())
}

/// `pid` is the one the holder writes, and the one a probe takes for its
/// own.
fn claim_at(path: &Path, name: &str, pid: u32) -> Claim {
    // A second pass follows the removal of a stale socket.
    for pass in 0..2 {
        match UnixListener::bind(path) {
            Ok(listener) => return serve(listener, name, pid),
            Err(e) if e.kind() == ErrorKind::AddrInUse => {}
            Err(e) => return unguarded(name, format_args!("{}: {e}", path.display())),
        }
        match probe(path, pid) {
            Probe::Held(holder) => {
                minka_note::note!("{name}: already running as PID {holder}");
                return Claim::Duplicate;
            }
            // Another launch bound the file after this one removed it, and
            // is not listening yet. No bind follows here, so the file is
            // left to that launch.
            Probe::Nobody if pass == 1 => break,
            // Nothing listens. Only a socket is removed, never another
            // kind of file.
            Probe::Nobody => match std::fs::symlink_metadata(path) {
                Ok(meta) if meta.file_type().is_socket() => {
                    let _ = std::fs::remove_file(path);
                }
                Err(e) if e.kind() == ErrorKind::NotFound => {}
                _ => return unguarded(name, format_args!("{} is not a socket", path.display())),
            },
            // Something accepted the connection. It is never removed, even
            // when its PID line was late.
            Probe::Unclear(why) => {
                return unguarded(name, format_args!("{}: {why}", path.display()));
            }
        }
    }
    unguarded(
        name,
        format_args!("{} was taken again as it was freed", path.display()),
    )
}

enum Probe {
    /// A live process other than this one answered with its PID.
    Held(u32),
    /// Nothing listens: no file, or a file no process accepts on.
    Nobody,
    /// Anything else: no live PID in time, or a connect that failed
    /// otherwise.
    Unclear(String),
}

/// Connects, reads the PID line and closes, which the holder hears as a
/// launch turned away.
fn probe(path: &Path, own: u32) -> Probe {
    let mut stream = match UnixStream::connect(path) {
        Ok(stream) => stream,
        Err(e) if matches!(e.kind(), ErrorKind::ConnectionRefused | ErrorKind::NotFound) => {
            return Probe::Nobody;
        }
        Err(e) => return Probe::Unclear(e.to_string()),
    };
    match read_line(&mut stream, &mut Vec::new(), Instant::now() + PROBE_WAIT) {
        Ok(Some(line)) => match line.trim().parse::<u32>() {
            Ok(pid) if pid != own && Path::new(&format!("/proc/{pid}")).exists() => {
                Probe::Held(pid)
            }
            _ => Probe::Unclear(format!(
                "the holder answered {line:?}, not another live PID"
            )),
        },
        Ok(None) => Probe::Unclear("the holder closed without its PID".into()),
        Err(e) => Probe::Unclear(format!("no PID from the holder: {e}")),
    }
}

fn serve(listener: UnixListener, name: &str, pid: u32) -> Claim {
    let (events, receiver) = sync_channel(QUEUE);
    let thread_name = format!("{name}-instance");
    let spawned = std::thread::Builder::new()
        .name(thread_name.clone())
        .spawn(move || accept(listener, &thread_name, pid, events));
    match spawned {
        Ok(_) => Claim::Primary(Instance { events: receiver }),
        Err(e) => unguarded(name, e),
    }
}

fn accept(listener: UnixListener, thread_name: &str, pid: u32, events: SyncSender<Event>) {
    let pid_line = format!("{pid}\n");
    for stream in listener.incoming() {
        let Ok(mut stream) = stream else {
            // Out of descriptors, for one: wait rather than spin.
            std::thread::sleep(Duration::from_millis(100));
            continue;
        };
        // First, and here: a fresh connection's buffer takes a dozen bytes
        // without blocking, so a probe never waits on another client.
        let _ = stream.write_all(pid_line.as_bytes());
        let events = events.clone();
        // The rest is read on a thread of its own, so a silent client holds
        // up nothing. If the thread cannot start, the connection closes.
        let _ = std::thread::Builder::new()
            .name(format!("{thread_name}-client"))
            .spawn(move || listen(stream, events));
    }
}

/// Reads what a client says after the PID line: nothing (a launch turned
/// away) or one request.
fn listen(mut stream: UnixStream, events: SyncSender<Event>) {
    let event = match read_line(&mut stream, &mut Vec::new(), Instant::now() + REQUEST_WAIT) {
        Ok(None) => Event::TurnedAway,
        Ok(Some(line)) => Event::Request(Request { line, stream }),
        // Too slow, too long, or reset: dropped, which closes it.
        Err(_) => return,
    };
    // With the queue full, or the app's end dropped, the event is dropped,
    // and with it a request's connection.
    let _ = events.try_send(event);
}

/// What is left until `deadline`; never zero, which would mean no timeout.
fn left(deadline: Instant) -> io::Result<Duration> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|left| !left.is_zero())
        .ok_or_else(|| ErrorKind::TimedOut.into())
}

/// Reads one line, without its newline, by `deadline`. The deadline is
/// looked at before every read, so a peer sending a byte now and then
/// cannot hold it longer. `pending` keeps what came after the line. `None`
/// is the end of the stream before any byte; a last line without a newline
/// still counts.
fn read_line(
    stream: &mut UnixStream,
    pending: &mut Vec<u8>,
    deadline: Instant,
) -> io::Result<Option<String>> {
    let mut chunk = [0u8; 512];
    loop {
        if let Some(end) = pending.iter().position(|&b| b == b'\n') {
            let line: Vec<u8> = pending.drain(..=end).collect();
            return Ok(Some(String::from_utf8_lossy(&line[..end]).into_owned()));
        }
        if pending.len() > LINE_LIMIT {
            return Err(io::Error::new(ErrorKind::InvalidData, "line too long"));
        }
        stream.set_read_timeout(Some(left(deadline)?))?;
        match stream.read(&mut chunk) {
            Ok(0) if pending.is_empty() => return Ok(None),
            Ok(0) => {
                return Ok(Some(
                    String::from_utf8_lossy(&std::mem::take(pending)).into_owned(),
                ));
            }
            Ok(n) => pending.extend_from_slice(&chunk[..n]),
            Err(e) if e.kind() == ErrorKind::Interrupted => {}
            Err(e) if e.kind() == ErrorKind::WouldBlock => return Err(ErrorKind::TimedOut.into()),
            Err(e) => return Err(e),
        }
    }
}

#[derive(Debug)]
pub enum RequestError {
    /// No instance holds the lock, or there is no `XDG_RUNTIME_DIR`.
    NotRunning,
    /// An instance holds it but did not answer in time.
    TimedOut,
    /// An instance holds it but closed the connection without an answer: it
    /// dropped the request, or it is a build from before requests.
    HungUp,
    /// Any other failure, such as a lock that is not an instance's.
    Io(io::Error),
}

impl fmt::Display for RequestError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            RequestError::NotRunning => f.write_str("not running"),
            RequestError::TimedOut => f.write_str("running, but no answer in time"),
            RequestError::HungUp => f.write_str("running, but it hung up without an answer"),
            RequestError::Io(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for RequestError {}

/// Sends `line` to the running instance of `name` and returns its one-line
/// answer. Once connected, all of it takes at most `timeout`; connecting
/// itself has no bound, as the holder accepts on a thread of its own.
pub fn request(name: &str, line: &str, timeout: Duration) -> Result<String, RequestError> {
    let path = lock_path(name).ok_or(RequestError::NotRunning)?;
    request_at(&path, line, timeout)
}

fn request_at(path: &Path, line: &str, timeout: Duration) -> Result<String, RequestError> {
    if line.contains('\n') {
        let e = io::Error::new(ErrorKind::InvalidInput, "a request is one line");
        return Err(RequestError::Io(e));
    }
    let deadline = Instant::now() + timeout;
    let failed = |e: io::Error| match e.kind() {
        ErrorKind::TimedOut | ErrorKind::WouldBlock => RequestError::TimedOut,
        ErrorKind::BrokenPipe | ErrorKind::ConnectionReset => RequestError::HungUp,
        _ => RequestError::Io(e),
    };
    let mut stream = UnixStream::connect(path).map_err(|e| match e.kind() {
        ErrorKind::ConnectionRefused | ErrorKind::NotFound => RequestError::NotRunning,
        _ => RequestError::Io(e),
    })?;
    let mut pending = Vec::new();
    let pid = read_line(&mut stream, &mut pending, deadline)
        .map_err(failed)?
        .ok_or(RequestError::HungUp)?;
    if pid.trim().parse::<u32>().is_err() {
        let e = io::Error::new(ErrorKind::InvalidData, "the lock's first line is not a PID");
        return Err(RequestError::Io(e));
    }
    stream
        .set_write_timeout(Some(left(deadline).map_err(failed)?))
        .map_err(failed)?;
    stream
        .write_all(format!("{line}\n").as_bytes())
        .map_err(failed)?;
    read_line(&mut stream, &mut pending, deadline)
        .map_err(failed)?
        .ok_or(RequestError::HungUp)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    /// The PID a test holder writes: alive and not this process, as a
    /// holder in another process would be.
    const HOLDER: u32 = 1;

    /// A fresh directory under the temp dir, removed after. Short, because
    /// a socket path is at most 108 bytes.
    struct Dir(PathBuf);

    impl Dir {
        fn new() -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let n = NEXT.fetch_add(1, Ordering::Relaxed);
            let dir = std::env::temp_dir().join(format!("mi-{}-{n}", std::process::id()));
            std::fs::create_dir(&dir).unwrap();
            Dir(dir)
        }

        fn lock(&self) -> PathBuf {
            self.0.join("t.lock")
        }
    }

    impl Drop for Dir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn holder(path: &Path) -> Instance {
        match claim_at(path, "test", HOLDER) {
            Claim::Primary(instance) => instance,
            Claim::Duplicate => panic!("the first claim was taken for a duplicate"),
        }
    }

    fn is_duplicate(path: &Path) -> bool {
        matches!(claim_at(path, "test", std::process::id()), Claim::Duplicate)
    }

    fn next(instance: &Instance) -> Event {
        instance
            .events()
            .recv_timeout(Duration::from_secs(5))
            .unwrap()
    }

    /// Before this crate: write the PID on every connection, then close.
    fn old_holder(path: &Path) {
        let listener = UnixListener::bind(path).unwrap();
        let pid = std::process::id();
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { continue };
                let _ = writeln!(stream, "{pid}");
            }
        });
    }

    /// Before this crate: MinkaShell's and MinkaMon's probe.
    fn old_probe(path: &Path) -> bool {
        let Ok(mut stream) = UnixStream::connect(path) else {
            return false;
        };
        let _ = stream.set_read_timeout(Some(Duration::from_millis(600)));
        let mut buf = [0u8; 64];
        let mut text = String::new();
        loop {
            match stream.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    text.push_str(&String::from_utf8_lossy(&buf[..n]));
                    if text.contains('\n') || text.len() >= 64 {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
        let Ok(pid) = text.trim().parse::<u32>() else {
            return false;
        };
        pid != std::process::id() && Path::new(&format!("/proc/{pid}")).exists()
    }

    #[test]
    fn a_second_claim_is_a_duplicate_and_turned_away() {
        let dir = Dir::new();
        let instance = holder(&dir.lock());
        assert!(is_duplicate(&dir.lock()));
        assert!(matches!(next(&instance), Event::TurnedAway));
    }

    #[test]
    fn an_old_holder_is_recognised() {
        let dir = Dir::new();
        old_holder(&dir.lock());
        // Its PID is this process's, so the claim takes another for its own.
        assert!(matches!(
            claim_at(&dir.lock(), "test", HOLDER),
            Claim::Duplicate
        ));
    }

    #[test]
    fn an_old_probe_recognises_a_new_holder() {
        let dir = Dir::new();
        let instance = holder(&dir.lock());
        assert!(old_probe(&dir.lock()));
        assert!(matches!(next(&instance), Event::TurnedAway));
    }

    #[test]
    fn a_stale_socket_is_replaced() {
        let dir = Dir::new();
        drop(UnixListener::bind(dir.lock()).unwrap());
        assert!(matches!(probe(&dir.lock(), 0), Probe::Nobody));
        let _instance = holder(&dir.lock());
        assert!(is_duplicate(&dir.lock()));
    }

    #[test]
    fn another_kind_of_file_is_left_alone() {
        let dir = Dir::new();
        std::fs::write(dir.lock(), "hers").unwrap();
        let instance = holder(&dir.lock());
        // Unguarded: no events will ever come.
        assert!(instance.events().recv().is_err());
        assert_eq!(std::fs::read_to_string(dir.lock()).unwrap(), "hers");
    }

    #[test]
    fn a_request_is_answered() {
        let dir = Dir::new();
        let instance = holder(&dir.lock());
        let path = dir.lock();
        let client =
            std::thread::spawn(move || request_at(&path, "status", Duration::from_secs(5)));
        let Event::Request(request) = next(&instance) else {
            panic!("no request");
        };
        assert_eq!(request.line(), "status");
        request.answer("idle");
        assert_eq!(client.join().unwrap().unwrap(), "idle");
    }

    #[test]
    fn a_request_is_answered_from_another_thread() {
        let dir = Dir::new();
        let instance = holder(&dir.lock());
        let path = dir.lock();
        let client =
            std::thread::spawn(move || request_at(&path, "cancel", Duration::from_secs(5)));
        let Event::Request(request) = next(&instance) else {
            panic!("no request");
        };
        std::thread::spawn(move || request.answer("cancelled"))
            .join()
            .unwrap();
        assert_eq!(client.join().unwrap().unwrap(), "cancelled");
    }

    #[test]
    fn a_silent_client_does_not_delay_a_probe() {
        let dir = Dir::new();
        let _instance = holder(&dir.lock());
        // Connected, but neither reading nor sending nor closing.
        let silent: Vec<UnixStream> = (0..3)
            .map(|_| UnixStream::connect(dir.lock()).unwrap())
            .collect();
        let start = Instant::now();
        assert!(is_duplicate(&dir.lock()));
        assert!(
            start.elapsed() < Duration::from_millis(300),
            "{:?}",
            start.elapsed()
        );
        drop(silent);
    }

    #[test]
    fn a_dropped_request_is_an_error_not_a_hang() {
        let dir = Dir::new();
        let instance = holder(&dir.lock());
        let path = dir.lock();
        let start = Instant::now();
        let client =
            std::thread::spawn(move || request_at(&path, "status", Duration::from_secs(5)));
        drop(next(&instance));
        assert!(matches!(client.join().unwrap(), Err(RequestError::HungUp)));
        assert!(start.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn the_client_timeout_holds() {
        let dir = Dir::new();
        let instance = holder(&dir.lock());
        let start = Instant::now();
        let result = request_at(&dir.lock(), "status", Duration::from_millis(300));
        assert!(matches!(result, Err(RequestError::TimedOut)), "{result:?}");
        assert!(start.elapsed() < Duration::from_millis(1500));
        // The request was kept, unanswered, all along.
        assert!(matches!(next(&instance), Event::Request(_)));

        // A listener that never accepts: the PID line never comes.
        let wedged = dir.0.join("w.lock");
        let _listener = UnixListener::bind(&wedged).unwrap();
        let start = Instant::now();
        let result = request_at(&wedged, "status", Duration::from_millis(300));
        assert!(matches!(result, Err(RequestError::TimedOut)), "{result:?}");
        assert!(start.elapsed() < Duration::from_millis(1500));
    }

    #[test]
    fn nothing_running_is_told_apart() {
        let dir = Dir::new();
        let result = request_at(&dir.lock(), "status", Duration::from_secs(1));
        assert!(
            matches!(result, Err(RequestError::NotRunning)),
            "{result:?}"
        );
        drop(UnixListener::bind(dir.lock()).unwrap());
        let result = request_at(&dir.lock(), "status", Duration::from_secs(1));
        assert!(
            matches!(result, Err(RequestError::NotRunning)),
            "{result:?}"
        );
    }

    #[test]
    fn an_old_holder_hangs_up_on_a_request() {
        let dir = Dir::new();
        old_holder(&dir.lock());
        let result = request_at(&dir.lock(), "status", Duration::from_secs(5));
        assert!(matches!(result, Err(RequestError::HungUp)), "{result:?}");
    }

    #[test]
    fn the_holder_answers_events_unread_or_dropped() {
        let dir = Dir::new();
        let instance = holder(&dir.lock());
        // More launches than the queue keeps, none of them read.
        for _ in 0..QUEUE + 4 {
            assert!(is_duplicate(&dir.lock()));
        }
        drop(instance);
        assert!(is_duplicate(&dir.lock()));
        let result = request_at(&dir.lock(), "status", Duration::from_secs(5));
        assert!(matches!(result, Err(RequestError::HungUp)), "{result:?}");
    }

    /// Her running shell, read-only: connects to `minkashell.lock`, reads
    /// the PID and closes, which MinkaShell ignores. Never claims, binds or
    /// removes anything. `cargo test -p minka-instance -- --ignored`.
    #[test]
    #[ignore = "probes the live session's minkashell.lock"]
    fn the_running_shell_is_seen_as_a_duplicate() {
        let path = lock_path("minkashell").expect("XDG_RUNTIME_DIR");
        match probe(&path, std::process::id()) {
            Probe::Held(pid) => minka_note::note!("minkashell.lock is held by PID {pid}"),
            Probe::Nobody => panic!("nothing listens on {}", path.display()),
            Probe::Unclear(why) => panic!("{why}"),
        }
    }
}
