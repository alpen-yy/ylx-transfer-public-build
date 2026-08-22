# YLX Transfer

YLX Transfer is the Tauri desktop client for moving published recordings from
YLX 2UQ2 capture devices to a PC library and, optionally, to an S3-compatible
object store. The default composition uses real mDNS/HTTPS devices. The
simulator is available only with the explicit `demo` Cargo feature.

The implementation map, ownership rules, boot sequence, recovery protocol and
CI evidence boundaries are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
The Pi wire contract and its protocol rationale remain in
[docs/LAN_TRANSFER_PROTOCOL.md](docs/LAN_TRANSFER_PROTOCOL.md). The persistence
decision is recorded in [ADR-PC-001](docs/adr/ADR-PC-001-persistence.md).

## What Is Production

The normal build routes commands through the real composition root:

- mDNS discovery, manual-IP TLS fingerprint probing, physical-confirmation
  pairing, authenticated sessions and per-device heartbeat fencing;
- full-fingerprint device identity: canonical `ylx-<64 lowercase hex>` keys,
  `sha256:<64 lowercase hex>` TLS pins and display-only
  `YLX-<first 8 uppercase hex>` labels;
- signed publication verification (key identity, Ed25519 signature, schema,
  session identity, inventory, safe paths and SHA-256 claims);
- whole-session and per-file Range downloads with bounded workers, pause,
  resume, cancel, retry and crash recovery;
- safe local publication, a durable local library and guarded remote deletion;
- S3-compatible multipart upload with completion-bound verification and an OS
  credential vault; and
- a revisioned frontend backend adapter, single reducer, operation registry and
  independently disposed DOM views.

`src-tauri/src/demo.rs` and `src-tauri/src/sim.rs` are development-only paths.
They compile only when the explicit `demo` feature is requested, for example
with
`cargo build --manifest-path src-tauri/Cargo.toml --features demo`, and are not
selected by the default production commands.

The repository has one Rust workspace at `src-tauri/Cargo.toml`, with the Tauri
application, `ylx-transfer-core` and `ylx-transfer-adapters` as members, and one
lockfile at `src-tauri/Cargo.lock`. Runtime transfer persistence is fully
consolidated in `TransferStore`; its current schema version is **v19**. Retired
JSON files are accepted only as one-time startup migration inputs.

## Source Of Truth

| Concern                                                                                     | Authoritative owner                                                       | On-disk representation / boundary                                                              |
| ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Local library and non-secret storage profile                                                | `AppStore` (`state.rs`)                                                   | `app-state.sqlite3`; legacy `store.json` is imported once, scrubbed and archived               |
| Download/upload identity, immutable specs, state/version, retry lineage and terminal outbox | `TransferStore` (`persistence/transfer_store.rs`)                         | `transfer_store.sqlite3`; rows are tagged by `operation_kind`                                  |
| Download file plan, ledger and checkpoints                                                  | `TransferStore` + `ArtifactInspector`                                     | durable rows are reconciled with `.part`, journal and final-file evidence                      |
| Upload activity, visibility, verified receipts, multipart handles and acknowledged parts    | `TransferStore` upload records                                            | durable activity/receipt rows plus multipart cleanup state; credentials are never persisted    |
| Transfer transition decisions                                                               | `JobAggregate`; download execution is serialized by `TransferCoordinator` | expected-version CAS prevents stale commands or workers from overwriting a newer state         |
| Credentials                                                                                 | `CredentialVaultPort`/OS keyring adapter                                  | existence is exposed as `secretConfigured`; raw secrets never cross a read DTO or SQLite store |
| Verified files and atomic local publication                                                 | `ArtifactInspector` and revision staging                                  | `.part.journal`, `.ylx-staging`, `.ylx-revision` and `.ylx-selected` encode distinct evidence  |
| Browser-visible state                                                                       | TypeScript `AppStore` reducer                                             | `Transfer.state` is the sole activity-state field; revisioned stale observations are rejected  |

The retired JSON/SQLite engine comparison modules and generic credential port
are no longer runtime or test authorities. See the production, one-time
migration and test-only table in the architecture document before changing a
remaining compatibility artifact.

Legacy `pending-downloads.json` and `pending-uploads.json` support is deliberately
retained only as marker-backed, one-shot import. Importers preserve byte-for-byte
backup/diagnostic material until the replacement transaction and migration
marker are durable, and report the migration outcome. The download
`.part.journal` is not a retired task sidecar: it remains current per-file
recovery evidence. Historical migration DDL also remains intact so databases
created by older releases can still be opened.

## Boot And Shutdown

`src-tauri/src/lib.rs` wires startup and `src-tauri/src/application.rs` owns the
application-facing lifecycle. Together they enforce this order:

1. **Load and migrate.** `BootConfig::load` opens `app-state.sqlite3`, reads
   the persisted library and storage profile once, imports a legacy
   `store.json` when needed, applies shipped storage defaults and captures a
   legacy plaintext credential for vault migration.
2. **Build an inert composition.** `Composition::new` validates the selected
   library root, opens `TransferStore`, performs any one-time legacy transfer
   import, and constructs the fleet, coordinator, verifier and object-store
   adapter. It starts no loop and performs no network I/O.
3. **Construct and manage application state.** `AppState::from_boot_config`
   migrates credentials into the vault, reconciles interrupted uploads and
   archives the old application store. `AppState` and `TransferApplication` are
   then registered with `app.manage`.
4. **Bind and recover.** `TransferApplication::start` binds the Tauri event sink
   and calls `Composition::recover_on_startup`, which rehydrates durable
   download jobs from their complete `TransferStore` records.
5. **Start loops.** Only then are mDNS, heartbeat and transfer-poll loops
   started. The poll loop drains tagged download and upload completion outbox
   rows. `TransferApplication::stop` aborts the retained handles on
   `RunEvent::Exit`.

A configured download root is selected during stage 1 and prepared before the
runtime is built. An unusable path falls back to the app-data library root so
a bad setting cannot make the settings screen unreachable. Changing the root
takes effect immediately when the local library is empty and no durable job
remains; otherwise it is refused so files cannot be split between roots.

## Transfer State And Commands

`TransferJobState` is the domain enum and `JobAggregate` is the only legal
transition graph. The current state set is:

```text
queued -> waiting_for_device | waiting_for_pairing | paused_capture_active
       -> preparing -> transferring -> verifying -> committing -> succeeded
                                             \-> retry_wait -> queued
any non-terminal state -> cancelling -> cancelled
preparing/transferring/verifying/committing/retry_wait -> failed(code, retryable)
```

The reducer is pure: it returns a decision and effects, never opens a database,
performs network I/O or reads a clock. `TransferCoordinator` executes those
effects through a per-job serialized cell and an expected-version CAS. Worker
reports describe the stage they observed; they do not choose the next state.
Terminal states are immutable. Retry creates a new job with a new lineage.

User pause is a desired-run-state control. For a parked job it is represented by
the control flag; an actively transferring job parks through `retry_wait`.
Capture-priority interruption uses `paused_capture_active`. Pause/cancel waits
for the worker to release its file handle before acknowledging success.

The frontend has two deliberate views of transfer state. Activity rows use the
single snake-case string `Transfer.state`; the runtime decoder accepts only
`TRANSFER_STATES` and explicitly rejects the retired `done`, `failed`, `queued`
and `resumed` booleans. Durable job events use the richer discriminated
`TransferJobState` union. Their `userPaused` value reports desired-run control,
not a second lifecycle state.

The frontend does not infer state from button labels or parallel arrays.
`TransferBackend` owns transport decoding, `startBackend` subscribes before
reading the snapshot and replays only newer events, `AppStore.commit` is the
single state-write entry point, and `TransferApp`/the DOM view modules own
operation lifetimes and rendering.

### RPC and batch outcomes

Tauri commands validate wire input and call `TransferApplication`; they do not
expose persistence DTOs or orchestrate stores directly. Batch results keep the
input identity and verdict in one tagged object:

```text
dispatch: { results: [
  { status: "success", item, jobId } |
  { status: "failure", item, error }
] }

session mutation: { revision, value: {
  results: [
    { status: "success", item } |
    { status: "failure", item, error }
  ],
  sessions,
  operationError
} }

library mutation: { revision, value: { results, library } }
```

The session mutation's `operationError` is an independent batch-level refresh
failure; it is not attached to an arbitrary item. The frontend requires exactly
one result for each unique requested item and rejects missing, duplicate or
unexpected items. It also fails closed on an unknown status, unknown error
code, malformed payload or retired parallel-array shape.

Every error is `{ code, message, retryable, details? }`, where `code` is from
the shared machine-readable allowlist and `details`, when present, is a JSON
object. `message` is display text, never branch authority. Upload start accepts
a `LibraryKey`; `upload_entry`, batch dispatch and upload retry return a durable
`UploadJobId`. Upload `Transfer.key`, retry, cancel and dismiss use that same
job identity, with cancel/dismiss RPC payload `{ jobId }`.

Resource ordering is backend-issued rather than synthesized by the frontend.
`read_snapshot` returns one atomic outer revision and inner revisioned values
for devices, library, transfers and storage; those four individual reads use
the same published cache. `list_sessions` is the deliberate exception: it
refreshes one canonical device over the network outside the cache lock, then
publishes the exact session value and uses that device-scoped revision for both
the response and event. A per-device async gate serializes that refresh with
the same device's delete/cleanup/background refresh operations; different
devices remain independent. `add_manual_device` likewise returns a revisioned
`Device` using the revision of its complete `devices:update` publication. All
resource reads are `{ revision, value }`. Session/library mutations and storage
saves return revisioned projections, and a returned projection and its event
share the same allocated revision.
Event-delivery failure is logged but cannot roll back the durable mutation or
published cache, or change the revision in its command response. Cache locks
are released before delivery, so concurrent publishers do not imply event FIFO;
clients discard late events by server revision, and the revisioned command
response remains a convergence path. Startup replay compares only the resource
revisions actually included by the snapshot or degraded fallback.

The production typed-event bridge fails with `application_unavailable` when
`TransferApplication` has not been registered; it never fabricates a fixed
revision. A fixed-revision fallback exists only inside the `cfg(test)` mock-app
path.

## Crash Recovery

- A publication and its file plan are verified before a durable transfer spec
  is accepted. `TransferStore` creates the identity, spec, file ledger and
  initial state in one transaction; a failed transaction leaves no job to
  recover.
- On startup, `TransferStore` reconciles jobs interrupted in
  `transferring`, `verifying` or `committing` to a retryable state without
  deleting durable checkpoints. The complete spec is already in the same
  repository, so rehydration does not depend on a JSON request index.
- `ArtifactInspector` gives each file exactly one verdict: `Missing`,
  `Partial(durable_offset)`, `Verified` or `Invalid`. Progress and resume use
  that same evidence. A verified final file is reused; a bad same-size file is
  re-downloaded; a partial resumes only from bytes backed by both the file and
  its durable journal.
- Whole-session downloads assemble in revision-scoped hidden staging. A
  sealed, fully verified revision is published with one directory rename; a
  crash before or after that rename is distinguishable and rerunnable.
- Selected-file downloads publish only the requested verified files and write
  a scope-bound `.ylx-selected` marker. They merge with existing visible files
  and never create or overwrite the `.ylx-revision` whole-session-completeness
  claim.
- A terminal transfer transition and its outbox record commit together.
  Delivery applies the library projection, emits the refreshed snapshot and
  acknowledges last. A crash at any point leaves the outbox deliverable on the
  next poll or launch.
- `create_upload_job` atomically creates an `operation_kind = upload` job, its
  immutable `(entry_key, revision, input_digest, object_prefix)` spec and its
  durable activity row. Start and terminal operations use expected-version
  CAS; finish and cancel race for one durable terminal state plus a tagged
  outbox row, so the late writer cannot overwrite the winner. Retry, repeat and
  supersede operations create durable lineage children without inheriting
  progress or receipts.
- Upload activity persists its label, target, trusted total and confirmed data
  bytes. `transfer_jobs.dismissed_at` hides a terminal activity row without
  deleting its job, spec, lineage, completion, multipart or receipt evidence.
- Completion-bound receipts are staged before multipart rows are retired. Each
  immutable receipt records its data/evidence role, exact object key, ETag,
  optional version ID, byte count, source SHA-256 and digest-proof method.
  Evidence objects do not inflate user-visible data progress.
- Transfer-store multipart rows retain remote handles, acknowledged parts and
  desired abort state. Their endpoint, bucket and URL style are durable; the
  immutable upload spec also persists the normalized object namespace. Only a
  legacy row explicitly marked `legacy_configured` may consult current URL
  configuration, and a legacy `NULL` namespace cannot authorize an exact-key
  success projection. Startup durably cancels surviving non-terminal upload
  jobs, claims orphan multipart rows for abort and leaves any unaborted row for
  another launch. Legacy library rows stuck at `Uploading` without a durable
  upload job become an explicit failure. Malformed legacy import input is
  retained and surfaced, never treated as an empty success.

## Device Identity And Compatibility

The core identity module accepts a bare fingerprint or a case-insensitive
`sha256:` prefix followed by exactly 64 ASCII hexadecimal characters. It stores
bare lowercase hex and derives three deliberately different projections:

```text
identity / paths / RPC keys: ylx-<64 lowercase hex>
TLS pin:                      sha256:<64 lowercase hex>
display and legacy alias:     YLX-<first 8 uppercase hex>
```

`Device.id`, fleet handles, endpoints, sessions, commands, navigation keys and
new durable records use the canonical full identity. `Device.displayId` and
`LibraryEntry.deviceDisplayId` are display-only. Two devices may legitimately
have the same eight-character label and still retain independent rows and
operations.

Compatibility is dual-read/canonical-write. Existing jobs, library entries,
directories, natural keys, delete intents, leases and S3 keys are not blindly
rewritten because doing so could break idempotency or lose the location of
existing evidence. A legacy short alias resolves only against the currently
registered full identities: zero matches is unknown, one is compatible and
more than one is an explicit ambiguous error. New writes always use the
canonical identity.

Production has no `AppData.transfers` activity authority. Upload rows are
projected from durable jobs/activity; simulator transfer state exists only as
`DemoTransferState` behind the explicit `demo` feature.

## Object-Store Recovery Boundary

`ObjectStorePort` can abort an active multipart upload and verify a specific
completed version, but it has no version-safe completed-object delete or
provider orphan-list operation. Consequently an `UnknownUpload` response is
ambiguous: the multipart may already have been aborted or may have completed.
The durable multipart row is retired only when an exact, structurally valid,
version-bound receipt accounts for the same job and object. Otherwise it stays
`aborting`, blocks upload dismissal and library-root switching, and requires a
later retry or provider-side retention cleanup.

This is a deliberate saga boundary, not database/object-store atomicity. A
crash after a provider accepts `CreateMultipartUpload` but before the returned
handle is persisted, and a completed object that needs rollback, cannot be
fully repaired through the current port. Verified receipts provide durable
accounting; they do not claim that every external effect can be rolled back.

## Development

```bash
npm ci
npm run tauri dev
```

Frontend checks:

```bash
npm test
npm run format:check
npm run lint
npm run typecheck
npm run build
```

Rust checks (the repository's target workspace and lockfile):

```bash
cargo fmt --manifest-path src-tauri/Cargo.toml --all -- --check
cargo clippy --manifest-path src-tauri/Cargo.toml --workspace --all-targets --all-features -- -D warnings
cargo test --manifest-path src-tauri/Cargo.toml --workspace --all-targets --all-features
npm run tauri build
```

The CI workflow also runs a pinned MinIO object-store contract lane, a
filesystem-recovery contract on Ubuntu/macOS/Windows, and workspace
clippy/tests plus the Tauri build on all three platforms. The real Pi lane is
manual and pinned to RP-YLX revision
`2db57ae68e04197397b8ac84f4d71548aa2fcb36`; missing peer code or fixtures is a
hard failure rather than a skipped success. These commands and lanes are
configured gates, not a claim that they passed for this checkout without an
observed run.

The 2026-08-04 local PC audit at C72 commit `0f98097` observed all 281
frontend tests, typecheck, ESLint, the Vite build and the
Prettier check passing. `ylx-transfer-core` passed 305 unit tests plus every enabled
integration suite and strict Clippy; the adapters passed 110 tests plus check,
strict Clippy and rustfmt, with eight real-service/manual tests explicitly
ignored. The full Tauri application source passed check and strict Clippy with
a temporary `pkg-config` shim, and workspace rustfmt passed. That shim proves
source compilation only: the host could not link or execute the application
tests without the native GTK/WebKitGTK/DBus development libraries. A real
MinIO service lane, Windows cross-check and hosted CI were not run locally and
are not counted as passes.

## Object Store Credentials

The fresh-install endpoint, bucket and URL style are defined by
`StorageConfig::default()`. Access and secret keys are write-only settings and
are stored in the OS credential vault. Existing keys take precedence over the
bootstrap source. For the current deployment the bootstrap supports, in order,
the `YLX_OSS_ACCESS_KEY`/`YLX_OSS_SECRET_KEY` environment pair, an app-data
`credentials.json`, a build-time value, and the source-level built-in value.

The built-in key is an explicitly accepted deployment compromise for a RAM user
scoped to the recordings bucket; it is extractable from source and binaries and
must never be replaced with an account-wide key. A backend-issued short-lived
credential remains the intended future design. No raw credential is returned by
`get_storage_config`, persisted in either SQLite store, sent in events or
written to a multipart upload record.

## Project Map

```text
src/
  runtime/       TransferBackend adapter, startup buffering and reducer
  app/           TransferApp controller, actions and view orchestration
  ui/            DOM screens, selectors, escaping and event delegation
  types.ts/ids.ts  wire-facing data and branded frontend identities
src-tauri/src/
  lib.rs         ordered Tauri setup, managed-state registration and shutdown
  application.rs application protocol, revisions, subscriptions and start/stop
  state.rs       AppStore boot/migration and application state
  composition.rs real production composition, fleet, coordinator and loops
  commands.rs    validated Tauri RPC DTOs and command handlers
  models.rs      backend/UI DTOs; secrets are absent from read shapes
src-tauri/crates/ylx-transfer-core/src/
  domain/        opaque IDs, verified publication material and JobSpec
  device/        per-device actor/fleet state and fencing
  transfer/      aggregate, coordinator, bounded queue and recovery evidence
  library/       download, ArtifactInspector, staging and object-store ports
  persistence/   AppStore, TransferStore, completion consumer and transition data
src-tauri/crates/ylx-transfer-adapters/src/
  pi_http.rs, discovery_mdns.rs, pi_download_source.rs
  publication_verifier.rs, object_store_s3.rs, credential_keyring.rs
```

The Pi protocol document is intentionally separate from this implementation
map: its API schemas and fixtures are the wire-contract authority, while this
README and `docs/ARCHITECTURE.md` describe the PC runtime that consumes them.
