"""Test-only harness for PC-03's real cross-process integration test
(``tests/pi_http_integration.rs`` in this crate).

This file lives entirely in the **ylx-transfer** repo -- it does not modify
anything in the sibling RP-YLX repo. It imports RP-YLX's production
composition entry point,
``ylx_capture.transfer.composition.build_transfer_daemon``, the *exact
same* function ``ylx_capture.transfer_daemon_cli.py`` itself calls to
assemble a real ``TransferDaemon`` (real TLS identity, real HTTP server,
real pairing/connections, real session repository, real capture-activity
bridge -- see that module's own docstring). This harness does not
reimplement or fake any of that; it is a thin wrapper around it.

## Why this exists instead of spawning ``transfer_daemon_cli`` directly

The Rust test needs a small synchronization protocol to learn the daemon's
ephemeral HTTPS port and stop it cleanly. Pairing itself uses production
trusted-LAN auto-approval; the harness never reaches into the composition's
``PairingBroker`` object.

## Wire protocol

Stdout (one line per event):
    ``READY <port>``                        -- HTTPS server is up on this port
    ``ERROR <message>``                      -- a command failed; harness keeps running

Stdin (one command per line):
    ``STOP``                                 -- graceful shutdown

## PC-03b addition: `--publish-mono-session <session_id>`

Optional, off by default (preserves every existing test's "fresh daemon has
no sessions" assumption). When given, this harness builds one minimal
"mono" session directory on disk (the same fixture shape RP-YLX's own
``capture/tests/transfer/conftest.py``'s ``make_mono_session``/
``write_capture_commit`` use -- deliberately a standalone copy, not an
import of that test-only file, for the same reason that file itself gives
for not importing CAP-09's: this package's owned tests/harness should not
reach into another package's test file) and really publishes it via the
**real**, unmodified, production ``ylx_capture.publication.publish_session``
(real dirfd-based hashing, real canonical-manifest Ed25519 signing, a real
atomic ``publication_manifest.json`` write) before starting the daemon,
with ``recording_roots`` pointed at its parent directory so
``build_transfer_daemon``'s own real `recover_publication_state` call
re-discovers it from a cold start exactly like a real Pi reboot would --
this is not a mocked/injected repository. This is what lets
`pi_http_integration.rs` prove a real `GET /sessions/{id}` response
genuinely carries a real `files[]` array end to end, cross-language,
cross-process.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

from ylx_capture.publication import PublicationCatalog, RestrictedPersistentEd25519Signer, publish_session
from ylx_capture.transfer.composition import build_transfer_daemon
from ylx_capture.transfer.models import DeviceId, DeviceIdentity


def _write_capture_commit(session: Path) -> None:
    """Standalone copy of `capture/tests/transfer/conftest.py`'s
    `write_capture_commit` -- see this module's doc comment for why this is
    a copy, not an import, of that test-only file."""
    spool = session / "spool"
    spool.mkdir(parents=True, exist_ok=True)
    source = spool / "source_00000.mp4"
    source.write_bytes(b"durable-source-bytes")
    (spool / "segments.csv").write_text(f"{source},0.000000,121.400000\n", encoding="utf-8")
    (session / "capture.commit.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "committed_at": "2026-08-01T04:00:00+00:00",
                "stop_reason": "user",
                "native_result": 0,
                "mux_return_code": 0,
                "capture_summary": {"result": 0},
                "final_progress": {"out_time_seconds": 121.4},
                "source_segments": ["spool/source_00000.mp4"],
            }
        ),
        encoding="utf-8",
    )


def _make_mono_session(root: Path, name: str) -> Path:
    """Standalone copy of `capture/tests/transfer/conftest.py`'s
    `make_mono_session` -- a minimal durably-complete mono session (one
    video file, one IMU file, session.json, capture.commit.json) -- enough
    for the real `publish_session` to accept it."""
    session = root / name
    video = session / "video"
    raw = session / "raw"
    video.mkdir(parents=True)
    raw.mkdir()
    (video / "segment_00000.mp4").write_bytes(b"real-integration-test-video-bytes-0123456789")
    (raw / "imu.jsonl").write_text('{"t":0,"ax":0.1}\n', encoding="utf-8")
    (session / "session.json").write_text(
        json.dumps(
            {
                "schema_version": 5,
                "state": "complete",
                "has_video": True,
                "camera": {"imu_status": "recorded", "video_codec": "mjpeg"},
                "capture_summary": {
                    "result": 0,
                    "frame_sequence_gaps": 0,
                    "imu_timestamp_errors": 0,
                    "imu_samples": 100,
                },
                "final_progress": {"out_time_seconds": 121.4},
            }
        ),
        encoding="utf-8",
    )
    _write_capture_commit(session)
    return session


def _publish_mono_session(recordings_root: Path, keys_dir: Path, session_id: str) -> dict[int, bytes]:
    """Builds one real mono session under `recordings_root` and really
    publishes it (real Ed25519 signature, real atomic manifest write) via
    the real, production `publish_session`. Returns the
    `trusted_signing_keys` mapping `build_transfer_daemon` needs to verify
    it back on its own real cold-start `recover_publication_state` scan."""
    session_dir = _make_mono_session(recordings_root, session_id)
    # Use the exact persistent key path `build_transfer_daemon` owns. This
    # models a real daemon restart: the pre-restart publisher and the
    # recovered daemon share one device publication identity. Supplying a
    # different key at the same key version is correctly rejected by the
    # production composition as an identity conflict.
    signer = RestrictedPersistentEd25519Signer(keys_dir / "publication_key.json")
    catalog = PublicationCatalog()
    publish_session(session_dir, session_id=session_id, signer=signer, catalog=catalog)
    return {signer.key_version(): signer.public_key()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pi-daemon-harness")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--device-id", default="pc03-it-01")
    parser.add_argument("--display-name", default="PC-03 Integration Test Pi")
    parser.add_argument(
        "--publish-mono-session",
        default=None,
        metavar="SESSION_ID",
        help="PC-03b: if given, real-publish one mono session with this id before starting the daemon "
        "(see this module's doc comment) so GET /sessions/{id} has a real files[] array to return.",
    )
    args = parser.parse_args(argv)

    device_identity = DeviceIdentity(device_id=DeviceId(args.device_id), display_name=args.display_name)

    recording_roots: tuple[Path, ...] = ()
    trusted_signing_keys: dict[int, bytes] | None = None
    if args.publish_mono_session:
        recordings_root = args.state_dir / "recordings"
        recordings_root.mkdir(parents=True, exist_ok=True)
        trusted_signing_keys = _publish_mono_session(recordings_root, args.state_dir, args.publish_mono_session)
        recording_roots = (recordings_root,)

    # Deliberately never create this path. Both scenarios prove the transfer
    # daemon remains usable when capture-daemon is absent.
    media_admission_socket = args.state_dir / "no-capture-daemon-here.sock"

    comp = None
    daemon_started = False
    try:
        comp = build_transfer_daemon(
            host=args.host,
            port=args.port,
            state_dir=args.state_dir,
            device_identity=device_identity,
            recording_roots=recording_roots,
            trusted_signing_keys=trusted_signing_keys,
            media_admission_socket=media_admission_socket,
        )
        comp.daemon.start()
        daemon_started = True
        port = comp.http_port()
        print(f"READY {port}", flush=True)

        stop_event = threading.Event()

        def stdin_loop() -> None:
            for raw_line in sys.stdin:
                line = raw_line.strip()
                if not line:
                    continue
                if line == "STOP":
                    stop_event.set()
                    return
                print(f"ERROR unrecognized command: {line!r}", flush=True)
            # stdin closed without an explicit STOP (e.g. parent died) -- still
            # shut down cleanly rather than hanging forever.
            stop_event.set()

        thread = threading.Thread(target=stdin_loop, daemon=True)
        thread.start()
        stop_event.wait()
    finally:
        if daemon_started and comp is not None:
            comp.daemon.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
