//! PC-03's real cross-process, cross-language integration proof.
//!
//! This spawns a **real** Python process from RP-YLX (a sibling repo on
//! this same machine, `/home/alpen/DEV/RP-YLX` by default, overridable via
//! `YLX_RP_REPO_PATH`) and speaks real HTTPS to it using this crate's own
//! `PiHttpClient` -- not a fake, not an in-process handler call. Two test
//! functions, in increasing order of ambition:
//!
//! - [`unauthenticated_wire_compat_against_real_pi_daemon`][]: spawns the
//!   **real, unmodified** `ylx_capture.transfer_daemon_cli` entry point
//!   (the actual production process a real Pi would run) and proves (a) a
//!   real `POST /pairing-requests` round trip (202, real body shape) and
//!   (b) a real unauthenticated call being rejected with a real `401`
//!   `problem+json` body -- all over a real TLS connection with this
//!   client's fingerprint pin validated against the daemon's real,
//!   freshly-generated self-signed certificate. This alone proves real
//!   cross-language wire compatibility (TLS handshake + HTTP/1.1 framing +
//!   JSON shapes + problem+json error shapes), independent of anything
//!   below.
//! - [`full_pairing_and_authenticated_round_trip_via_harness`][]: goes
//!   further and proves trusted-LAN auto-approval -> authenticated
//!   `GET /device` and `GET /sessions` -> unauthenticated rejection. It
//!   spawns `tests/support/pi_daemon_harness.py`, which composes the real
//!   daemon without an admin approval channel. No test code reaches into
//!   `PairingBroker` in process.
//!
//! Every test in this file is `#[ignore]`d by default: it needs the sibling
//! RP-YLX repo, which a checkout of `ylx-transfer` alone does not have. That
//! is deliberate honesty -- an unexecuted cross-repo proof is reported as
//! *ignored*, never as a pass. Run them explicitly with
//!
//! ```text
//! cargo test -p ylx-transfer-adapters --test pi_http_integration -- --ignored
//! ```
//!
//! and once explicitly requested, a missing repo/fixture is a hard failure
//! (see [`require_rp_ylx_repo`]) rather than a quiet `return`.

use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdout, Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use sha2::{Digest, Sha256};

use ylx_transfer_adapters::pi_client_port::session::{AuthenticatedPiClient, PiPairingClient};
use ylx_transfer_adapters::pi_http::{
    probe_tls_identity, tls_pin_from_pem_certificate, PiHttpClient, PiHttpClientConfig,
};
use ylx_transfer_core::device::actor::{AuthenticatedPiSession, PairingPort};
use ylx_transfer_core::device::{PairingPhase, PiClientErrorKind};
use ylx_transfer_core::domain::SessionId;

/// Default location of the sibling RP-YLX repo this task's brief names
/// explicitly. Overridable so this test isn't hard-broken on a checkout of
/// `ylx-transfer` alone.
fn rp_ylx_repo_path() -> PathBuf {
    std::env::var("YLX_RP_REPO_PATH")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("/home/alpen/DEV/RP-YLX"))
}

/// Hard-fails (rather than silently returning) when the sibling repo or the
/// specific fixture file this test needs is absent.
///
/// These tests are `#[ignore]`d by default, so reaching this function means
/// somebody explicitly asked for the cross-repo proof to run. At that point a
/// missing repo is a genuine failure of the environment, not a reason to
/// paint a green check on an unexecuted test.
fn require_rp_ylx_repo(marker: &str) -> PathBuf {
    let repo = rp_ylx_repo_path();
    let fixture = repo.join(marker);
    assert!(
        fixture.is_file(),
        "cross-repo proof requires the sibling RP-YLX repo, but {} is missing \
         (repo root resolved to {}; set YLX_RP_REPO_PATH to override). This test \
         is #[ignore]d by default precisely so it is never silently counted as \
         passed -- if you asked for it explicitly, the repo really must be there.",
        fixture.display(),
        repo.display()
    );
    repo
}

/// Upper bound on how long any spawned Python daemon may take to announce
/// readiness. Every wait in this file is deadline-bounded: a child that dies
/// (or never starts) must fail the test, not hang the run forever.
const READY_TIMEOUT: Duration = Duration::from_secs(30);

/// Continuously drains the child's stderr on a background thread.
///
/// Both of these things matter. Draining means a chatty daemon can never fill
/// its 64 KiB pipe buffer and block on `write()` while we are waiting for it to
/// become ready -- a classic subprocess deadlock. Capturing means the text is
/// available to put in the panic message when readiness never arrives, which is
/// exactly when you need it.
fn drain_stderr(child: &mut Child) -> Arc<Mutex<String>> {
    let sink = Arc::new(Mutex::new(String::new()));
    if let Some(stderr) = child.stderr.take() {
        let sink = Arc::clone(&sink);
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines() {
                let Ok(line) = line else { break };
                let mut buffer = sink.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
                buffer.push_str(&line);
                buffer.push('\n');
            }
        });
    }
    sink
}

fn captured_stderr(sink: &Arc<Mutex<String>>) -> String {
    sink.lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .clone()
}

/// Same idea as [`drain_stderr`], for stdout: reading on a background thread is
/// what makes a *bounded* wait for the READY handshake possible at all, since
/// `BufRead::lines().next()` on the pipe itself would block indefinitely.
fn stdout_line_reader(stdout: ChildStdout) -> Receiver<String> {
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            let Ok(line) = line else { break };
            if tx.send(line).is_err() {
                break;
            }
        }
    });
    rx
}

/// Waits (with a deadline) for the harness's `READY <port>` startup contract
/// and returns the port it bound.
fn wait_for_ready_port(lines: &Receiver<String>, stderr: &Arc<Mutex<String>>) -> u16 {
    let ready_line = lines.recv_timeout(READY_TIMEOUT).unwrap_or_else(|err| {
        panic!(
            "harness never announced readiness within {READY_TIMEOUT:?} ({err}); \
             harness stderr so far:\n{}",
            captured_stderr(stderr)
        )
    });
    ready_line
        .strip_prefix("READY ")
        .unwrap_or_else(|| panic!("expected \"READY <port>\", got {ready_line:?}"))
        .trim()
        .parse()
        .unwrap_or_else(|_| panic!("READY line did not carry a valid port: {ready_line:?}"))
}

/// Picks a free loopback TCP port by binding to port 0 and immediately
/// releasing it. Small TOCTOU race (another process could grab it before
/// the daemon binds) is an accepted, standard trade-off for local test
/// tooling -- the alternative (parsing the daemon's log output for the
/// bound port) is more fragile in this specific case since
/// `transfer_daemon_cli` logs structuredly to a file, not stdout.
fn pick_free_port() -> u16 {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind ephemeral port");
    listener.local_addr().expect("local addr").port()
}

fn wait_for_tcp_ready(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if std::net::TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    false
}

fn wait_for_file(path: &Path, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if path.exists() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    false
}

fn python_env_for_rp_ylx(cmd: &mut Command, repo: &Path) {
    let pythonpath = format!(
        "{}:{}",
        repo.join("src").display(),
        repo.join("capture/src").display()
    );
    cmd.env("PYTHONDONTWRITEBYTECODE", "1")
        .env("PYTHONPATH", pythonpath)
        .current_dir(repo);
}

/// Owns a spawned Python subprocess and guarantees it is killed and
/// reaped even if a test assertion panics partway through -- a hung
/// `ylx-transferd`/harness process left behind by a failed test would
/// otherwise silently occupy a port and a TLS listener for the rest of the
/// CI run.
struct ChildGuard(Option<Child>);

impl ChildGuard {
    fn new(child: Child) -> Self {
        Self(Some(child))
    }
}

impl Drop for ChildGuard {
    fn drop(&mut self) {
        if let Some(mut child) = self.0.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

/// Real, unmodified `ylx_capture.transfer_daemon_cli` -- the actual
/// production entry point a real Pi runs. `--port 0` lets the daemon
/// itself pick an ephemeral port; discovering that port requires either
/// parsing its (file-based, structured) logs or pre-allocating one
/// ourselves and hoping for no collision. This test uses the latter (see
/// `pick_free_port`) since it is simpler and the daemon accepts an
/// explicit `--port`.
#[test]
#[ignore = "cross-repo proof: needs the sibling RP-YLX repo and python3 (run with -- --ignored)"]
fn unauthenticated_wire_compat_against_real_pi_daemon() {
    let repo = require_rp_ylx_repo("capture/src/ylx_capture/transfer_daemon_cli.py");

    let tempdir = tempfile::tempdir().expect("create tempdir");
    let state_dir = tempdir.path().join("state");
    let runtime_dir = tempdir.path().join("runtime");
    let port = pick_free_port();

    let mut cmd = Command::new("python3");
    cmd.args(["-m", "ylx_capture.transfer_daemon_cli"])
        .arg("--state-dir")
        .arg(&state_dir)
        .arg("--runtime-dir")
        .arg(&runtime_dir)
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(port.to_string())
        .arg("--device-id")
        .arg("pc03-it-cli-01")
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    python_env_for_rp_ylx(&mut cmd, &repo);

    let mut child = cmd
        .spawn()
        .expect("spawn real transfer_daemon_cli subprocess");
    // Drain stderr from the moment the child exists: this daemon logs to a
    // piped stderr, and an undrained pipe would wedge it mid-startup.
    let stderr = drain_stderr(&mut child);
    let _guard = ChildGuard::new(child);

    let cert_path = state_dir.join("tls_cert.pem");
    assert!(
        wait_for_file(&cert_path, READY_TIMEOUT),
        "real daemon never wrote its TLS certificate at {} within {READY_TIMEOUT:?}; stderr:\n{}",
        cert_path.display(),
        captured_stderr(&stderr)
    );
    assert!(
        wait_for_tcp_ready(port, READY_TIMEOUT),
        "real daemon never became reachable on port {port} within {READY_TIMEOUT:?}; stderr:\n{}",
        captured_stderr(&stderr)
    );

    let pem =
        std::fs::read_to_string(&cert_path).expect("read real daemon's generated TLS certificate");
    let pin = tls_pin_from_pem_certificate(&pem)
        .expect("compute real fingerprint pin from real certificate");
    let observed_pin = probe_tls_identity("127.0.0.1", port, Duration::from_secs(10))
        .expect("first-contact TLS probe observes the real daemon certificate");
    assert_eq!(
        observed_pin, pin,
        "SAS bootstrap must bind the certificate actually observed on the network"
    );

    let client = std::sync::Arc::new(
        PiHttpClient::new(PiHttpClientConfig {
            host: "127.0.0.1".to_string(),
            port,
            tls_pin: pin.clone(),
            request_timeout: Duration::from_secs(10),
        })
        .expect("construct PiHttpClient with the real pin"),
    );
    let pairing = PiPairingClient::new(client.clone());

    // 1. Real TLS handshake + real POST /pairing-requests -> real 202.
    let created =
        PairingPort::create_pairing_request(&pairing, "pc-03-it-unauth", "nonce-unauth-1")
            .expect("real daemon accepts a real pairing-request creation over real pinned TLS");
    assert!(!created.attempt_id.is_empty());
    assert!(!created.poll_secret.is_empty());

    pairing
        .cancel_pairing(&created.attempt_id, &created.poll_secret)
        .expect("real daemon accepts authenticated pairing cancellation over pinned TLS");

    // 2. Real unauthenticated call -> real 401 problem+json, not a hang,
    //    not a panic, not a silently-accepted response.
    let unauthorized_session =
        AuthenticatedPiSession::new("token-that-was-never-issued", pin.0.clone(), None, 1)
            .expect("test session has a valid identity shape");
    let unauthorized = AuthenticatedPiClient::new(client, unauthorized_session)
        .expect("transport pin matches test session");
    let error = unauthorized
        .list_sessions(None, None)
        .expect_err("an unknown token must be rejected");
    assert_eq!(error.kind, PiClientErrorKind::Unauthorized);

    // Teardown: `_guard`'s `Drop` kills and reaps the subprocess when this
    // function returns (also the fallback for the harness test below if a
    // graceful `STOP` doesn't land in time) -- no orphaned daemon process
    // is left behind either way.
}

/// See the module doc comment: this proves the *full* pairing dance and an
/// authenticated round trip. The harness uses the same production
/// pairing-admin Unix socket that connects the Pi GUI to `ylx-transferd`.
#[test]
#[ignore = "cross-repo proof: needs the sibling RP-YLX repo and python3 (run with -- --ignored)"]
fn full_pairing_and_authenticated_round_trip_via_harness() {
    let repo = require_rp_ylx_repo("capture/src/ylx_capture/transfer/composition.py");

    let harness_path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/support/pi_daemon_harness.py");
    assert!(
        harness_path.is_file(),
        "harness script missing at {}",
        harness_path.display()
    );

    let tempdir = tempfile::tempdir().expect("create tempdir");
    let state_dir = tempdir.path().join("state");

    let mut cmd = Command::new("python3");
    cmd.arg(&harness_path)
        .arg("--state-dir")
        .arg(&state_dir)
        .arg("--port")
        .arg("0")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    python_env_for_rp_ylx(&mut cmd, &repo);

    let mut child = cmd.spawn().expect("spawn pi_daemon_harness.py subprocess");
    let mut stdin = child.stdin.take().expect("harness stdin");
    let stdout = child.stdout.take().expect("harness stdout");
    let stderr = drain_stderr(&mut child);
    let stdout_lines = stdout_line_reader(stdout);
    let _guard = ChildGuard::new(child);

    // First line must be "READY <port>" -- the harness's own startup
    // contract (see its module doc comment's wire protocol section).
    let port = wait_for_ready_port(&stdout_lines, &stderr);

    let cert_path = state_dir.join("tls_cert.pem");
    assert!(
        wait_for_file(&cert_path, READY_TIMEOUT),
        "harness never wrote its TLS certificate at {} within {READY_TIMEOUT:?}; stderr:\n{}",
        cert_path.display(),
        captured_stderr(&stderr)
    );
    assert!(
        wait_for_tcp_ready(port, READY_TIMEOUT),
        "harness daemon never became reachable on port {port} within {READY_TIMEOUT:?}; stderr:\n{}",
        captured_stderr(&stderr)
    );

    let pem = std::fs::read_to_string(&cert_path)
        .expect("read harness daemon's generated TLS certificate");
    let pin = tls_pin_from_pem_certificate(&pem)
        .expect("compute real fingerprint pin from real certificate");

    let client = std::sync::Arc::new(
        PiHttpClient::new(PiHttpClientConfig {
            host: "127.0.0.1".to_string(),
            port,
            tls_pin: pin.clone(),
            request_timeout: Duration::from_secs(10),
        })
        .expect("construct PiHttpClient with the real pin"),
    );
    let pairing = PiPairingClient::new(client.clone());

    // 1. Real POST /pairing-requests, exactly like a real PC client's
    //    first contact.
    let created = PairingPort::create_pairing_request(&pairing, "pc-03-it-full", "nonce-full-1")
        .expect("create_pairing_request succeeds over real pinned TLS");
    assert_eq!(created.phase, PairingPhase::Pending);

    // 2. Trusted-LAN mode auto-approves on the Pi. The first HTTPS poll
    //    observes "allowed" and delivers the one-shot connection token.
    let mut token: Option<String> = None;
    let mut sas_publication_key_fingerprint = created.sas_publication_key_fingerprint.clone();
    let poll_deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < poll_deadline {
        let status =
            PairingPort::get_pairing_status(&pairing, &created.attempt_id, &created.poll_secret)
                .expect("get_pairing_status succeeds over real pinned TLS");
        sas_publication_key_fingerprint = status
            .sas_publication_key_fingerprint
            .clone()
            .or(sas_publication_key_fingerprint);
        if let Some(t) = status.connection_token {
            token = Some(t);
            break;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    let token =
        token.expect("pairing attempt was approved but no connection_token was ever delivered");
    let session =
        AuthenticatedPiSession::new(token, pin.0.clone(), sas_publication_key_fingerprint, 1)
            .expect("allowed pairing yields a valid authenticated session");
    let authenticated = AuthenticatedPiClient::new(client.clone(), session)
        .expect("authenticated wrapper accepts the pinned transport");

    // 3. The real proof: authenticated GET /sessions over real HTTPS,
    //    through the real composed TransferApplication/HTTP server.
    let sessions = authenticated
        .list_sessions(None, None)
        .expect("authenticated list_sessions succeeds over real pinned TLS");
    assert!(
        sessions.sessions.is_empty(),
        "fresh daemon with no recording_roots has no sessions"
    );
    assert!(sessions.next_cursor.is_none());

    // 5. GET /device doubles as the CaptureBridge graceful-degradation
    //    proof: no real capture-daemon is running, so capture activity
    //    must read back as "unknown".
    let device = authenticated
        .get_device()
        .expect("authenticated get_device succeeds over real pinned TLS");
    assert_eq!(
        device.capture_activity,
        ylx_transfer_core::device::CaptureActivityState::Unknown
    );
    assert!(device.publication_key_fingerprint.starts_with("sha256:"));
    assert_eq!(device.publication_key_fingerprint.len(), 71);

    // 6. An unauthenticated call to an authenticated endpoint is still
    //    rejected -- the pairing dance above was not a formality.
    let unauthorized_session = AuthenticatedPiSession::new(
        "token-that-was-never-issued",
        pin.0.clone(),
        Some(device.publication_key_fingerprint.clone()),
        2,
    )
    .expect("unknown token session has valid shape");
    let unauthorized = AuthenticatedPiClient::new(client, unauthorized_session)
        .expect("transport pin matches unknown-token session");
    let error = unauthorized
        .list_sessions(None, None)
        .expect_err("unknown token must be rejected");
    assert_eq!(error.kind, PiClientErrorKind::Unauthorized);

    // Clean shutdown.
    let _ = writeln!(stdin, "STOP");
}

/// PC-03b's headline real cross-repo proof: this crate's own real
/// production `SessionCatalogPort::get_session` mapping -- the exact same code path
/// `ylx-transfer`'s `Composition::download_session` now calls -- genuinely
/// verifies the real `/device`-bound Ed25519 publication envelope and parses
/// a real `files[]` array out of a real `GET /sessions/{id}`
/// response served by the real, unmodified RP-YLX `TransferDaemon`, for a
/// session that was really published on disk (real dirfd hashing, real
/// canonical-manifest Ed25519 signing, real atomic `publication_manifest.json`
/// write via `ylx_capture.publication.publish_session`, then really
/// rediscovered by the daemon's own real `recover_publication_state` cold
/// scan -- not injected into a mocked repository; see
/// `pi_daemon_harness.py`'s `--publish-mono-session` addition). It then
/// takes one file's real opaque `id` straight out of that response and
/// round-trips it against the real `GET /sessions/{id}/files/{file_id}`
/// endpoint, verifying the downloaded bytes' real length and SHA-256
/// against exactly what the manifest itself declared -- proving the `id`
/// this client hands `composition::download_session` is not just
/// well-shaped JSON, but a genuinely working file handle.
#[test]
#[ignore = "cross-repo proof: needs the sibling RP-YLX repo and python3 (run with -- --ignored)"]
fn get_session_returns_a_real_file_inventory_that_round_trips_to_real_file_download() {
    let repo = require_rp_ylx_repo("capture/src/ylx_capture/transfer/composition.py");

    let harness_path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/support/pi_daemon_harness.py");
    assert!(
        harness_path.is_file(),
        "harness script missing at {}",
        harness_path.display()
    );

    let tempdir = tempfile::tempdir().expect("create tempdir");
    let state_dir = tempdir.path().join("state");
    let session_id = SessionId("sess-pc03b-real-1".to_string());

    let mut cmd = Command::new("python3");
    cmd.arg(&harness_path)
        .arg("--state-dir")
        .arg(&state_dir)
        .arg("--port")
        .arg("0")
        .arg("--publish-mono-session")
        .arg(session_id.as_str())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    python_env_for_rp_ylx(&mut cmd, &repo);

    let mut child = cmd.spawn().expect("spawn pi_daemon_harness.py subprocess");
    let mut stdin = child.stdin.take().expect("harness stdin");
    let stdout = child.stdout.take().expect("harness stdout");
    let stderr = drain_stderr(&mut child);
    let stdout_lines = stdout_line_reader(stdout);
    let _guard = ChildGuard::new(child);

    let port = wait_for_ready_port(&stdout_lines, &stderr);

    let cert_path = state_dir.join("tls_cert.pem");
    assert!(
        wait_for_file(&cert_path, READY_TIMEOUT),
        "harness never wrote its TLS certificate at {} within {READY_TIMEOUT:?}; stderr:\n{}",
        cert_path.display(),
        captured_stderr(&stderr)
    );
    assert!(
        wait_for_tcp_ready(port, READY_TIMEOUT),
        "harness daemon never became reachable on port {port} within {READY_TIMEOUT:?}; stderr:\n{}",
        captured_stderr(&stderr)
    );

    let pem = std::fs::read_to_string(&cert_path)
        .expect("read harness daemon's generated TLS certificate");
    let pin = tls_pin_from_pem_certificate(&pem)
        .expect("compute real fingerprint pin from real certificate");

    let client = std::sync::Arc::new(
        PiHttpClient::new(PiHttpClientConfig {
            host: "127.0.0.1".to_string(),
            port,
            tls_pin: pin.clone(),
            request_timeout: Duration::from_secs(10),
        })
        .expect("construct PiHttpClient with the real pin"),
    );
    let pairing = PiPairingClient::new(client.clone());

    // 1. Real trusted-LAN connection, exactly like the other harness-backed test.
    let created =
        PairingPort::create_pairing_request(&pairing, "pc-03b-it-real-files", "nonce-real-1")
            .expect("create_pairing_request succeeds over real pinned TLS");

    let mut token: Option<String> = None;
    let mut sas_publication_key_fingerprint = created.sas_publication_key_fingerprint.clone();
    let poll_deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < poll_deadline {
        let status =
            PairingPort::get_pairing_status(&pairing, &created.attempt_id, &created.poll_secret)
                .expect("get_pairing_status succeeds over real pinned TLS");
        sas_publication_key_fingerprint = status
            .sas_publication_key_fingerprint
            .clone()
            .or(sas_publication_key_fingerprint);
        if let Some(t) = status.connection_token {
            token = Some(t);
            break;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    let token =
        token.expect("pairing attempt was approved but no connection_token was ever delivered");
    let session =
        AuthenticatedPiSession::new(token, pin.0.clone(), sas_publication_key_fingerprint, 1)
            .expect("allowed pairing yields a valid authenticated session");
    let authenticated = AuthenticatedPiClient::new(client.clone(), session)
        .expect("authenticated wrapper accepts the pinned transport");

    // 2. Bind the detail envelope to the signing-key identity returned by
    //    authenticated GET /device, then verify the real Ed25519 signature
    //    and signed inventory through the production session-catalog mapping.
    let device = authenticated
        .get_device()
        .expect("authenticated get_device returns the real publication key identity");
    let detail = authenticated
        .get_session(session_id.as_str())
        .expect("authenticated get_session verifies a really-published session envelope");

    assert_eq!(detail.session_id, session_id.as_str());
    assert_eq!(
        detail.publication_key_fingerprint, device.publication_key_fingerprint,
        "session detail must be bound to authenticated GET /device identity"
    );
    assert_eq!(detail.publication_public_key.len(), 32);
    assert_eq!(detail.publication_signature.len(), 64);
    assert!(!detail.publication_payload.is_empty());
    assert_eq!(
        detail.file_count as usize,
        detail.files.len(),
        "file_count must match the real files[] array length"
    );
    assert_eq!(
        detail.files.len(),
        4,
        "the real mono fixture publish_session builds always yields exactly 4 real file entries \
         (video, session.json, capture.commit.json, raw/imu.jsonl) -- see publication.py's \
         _build_fixed_allowlist"
    );
    let video_entry = detail
        .files
        .iter()
        .find(|f| f.role == "video_mono")
        .expect("a real video_mono entry is present");
    assert_eq!(video_entry.media_type, "video/mp4");
    assert!(!video_entry.id.is_empty());
    assert_eq!(
        video_entry.sha256.len(),
        64,
        "sha256 must be real 64-hex, not a placeholder"
    );

    // 3. The deepest proof: this client's own real `get_file` call, fed
    //    nothing but the opaque `id` this same real `get_session` call
    //    just returned, really downloads real bytes matching exactly what
    //    the real manifest declared -- confirming the id is not just
    //    well-shaped JSON but a genuinely working file handle end to end.
    let file_response = authenticated
        .get_file(session_id.as_str(), &video_entry.id, None, None)
        .expect("get_file succeeds for the real id get_session just returned");
    assert_eq!(file_response.status, 200);
    assert_eq!(file_response.content_length, video_entry.size_bytes);
    assert_eq!(file_response.body.len() as u64, video_entry.size_bytes);
    let real_hash = hex_encode_lower(&Sha256::digest(&file_response.body));
    assert_eq!(
        real_hash, video_entry.sha256,
        "downloaded bytes' real SHA-256 must match exactly what the real manifest declared"
    );

    let _ = writeln!(stdin, "STOP");
}

fn hex_encode_lower(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{b:02x}"));
    }
    s
}
