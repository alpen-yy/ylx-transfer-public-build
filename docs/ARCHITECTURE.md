# PC Runtime Architecture

This document is the implementation map for the current YLX Transfer PC
client. It complements `LAN_TRANSFER_PROTOCOL.md`: that document defines the
Pi HTTP/wire contract and its rationale; this document defines ownership and
ordering in the Tauri application. Paths below are relative to this repository.

## Status And Boundaries

The default Tauri composition is production-oriented. It uses the real
`DeviceFleet`, Pi HTTPS adapter, publication verifier, download coordinator,
local library and object-store adapter. The simulator (`demo.rs`/`sim.rs`) is
behind the explicit `demo` feature and is not a fallback for a production
network error.

The implemented architecture is intentionally explicit. The following table keeps
the production authorities separate from migration inputs and test fixtures.

| Area                                | Current production path                                                                               | One-time migration or test-only path                                             |
| ----------------------------------- | ----------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| Application data                    | `AppStore` in `persistence/app_store.rs`, opened by `BootConfig`                                      | Legacy `store.json` is imported once, scrubbed, archived and never written again |
| Download/upload jobs and completion | `TransferStore` in `persistence/transfer_store.rs`; rows are tagged by `operation_kind`               | `pending-downloads.json` is a one-time transactional import                      |
| Upload activity and visibility      | `transfer_upload_activity` plus `transfer_jobs.dismissed_at`                                          | No process-local upload tray is authoritative                                    |
| Multipart handles and parts         | `TransferStore` upload records, including durable endpoint/bucket/URL style                           | `pending-uploads.json` is a one-time transactional import                        |
| Verified upload receipts            | `transfer_upload_receipts`, fenced by immutable upload job context                                    | Browser receipt DTOs are projections, not persistence input                      |
| Durable state owner                 | `JobAggregate` plus expected-version operations in `TransferStore`                                    | `persistence::schema::JobStateTag` is only a checked storage encoding            |
| Download coordinator rehydration    | Complete `TransferStore` rows plus durable file evidence                                              | No secondary runtime state or request index                                      |
| Download evidence                   | `ArtifactInspector`, download journal and `staging`                                                   | Temporary-directory recovery contracts exercise the same production evidence     |
| Object storage                      | `ObjectStorePort` with `S3ObjectStore`; completion verification is bound to this multipart completion | In-memory object store and tiny HTTP fakes are test adapters                     |
| Credentials                         | `CredentialVaultPort` and OS keyring adapter                                                          | `InMemoryCredentialVault` and failure fakes are tests only                       |
| Frontend                            | Decoded tagged state -> `AppStore` reducer -> `TransferApp`/DOM views                                 | `MemoryBackend` drives deterministic runtime tests                               |

The repository has one Cargo workspace at `src-tauri/Cargo.toml` and one
lockfile at `src-tauri/Cargo.lock`. Its members are the Tauri application,
`crates/ylx-transfer-core` and `crates/ylx-transfer-adapters`, so they share
dependency resolution and all-target/all-feature gates. There is no repository
root manifest; every Rust command must pass
`--manifest-path src-tauri/Cargo.toml`.

## Persistent State

### Application store

`BootConfig::load` opens `app-state.sqlite3` once. `AppStore` owns the local
library rows and the non-secret storage profile. `PersistedStore` is a
serialization boundary used during migration, not a second runtime database.
When a legacy `store.json` is present, the loader validates it, extracts any
legacy plaintext credential before deserialization drops unknown fields, writes
the credential to the vault first, persists a secret-free SQLite snapshot and
archives the original file. A failed vault write leaves the original file for
retry.

The storage profile contains endpoint, bucket, prefix, URL style, download root
and a credential-existence bit. Access and secret keys are deliberately absent
from read DTOs and from SQLite payloads.

### Transfer store

`TransferStore` is the durable owner for the logical transfer context and
runtime recovery:

- opaque `job_id`, `operation_kind` (`download` or `upload`) and natural key;
- immutable download `JobSpec`/ordered file rows or immutable upload
  `(entry_key, revision, input_digest, object_prefix)` spec;
- state/version and desired-run-state rows used by expected-version operations;
- durable parent/child retry lineage;
- download file ledger, checkpoints and immutable publication evidence;
- durable upload activity and the general `dismissed_at` visibility tombstone;
- multipart upload handles, endpoint/bucket/URL style, desired abort state and
  acknowledged parts;
- version-bound verified upload receipts, separated by data/evidence role; and
- operation-tagged terminal completion outbox records.

`create_job` is transactional and distinguishes an identical natural-key
request (`Existing`) from a digest mismatch or a job-ID collision. Legacy
`pending-downloads.json` is backed up, imported transactionally and deleted
only after the import marker commits; startup reports the migration outcome.

### Upload jobs and recovery

The current `TransferStore` schema is **v19**. Upload persistence was added in
forward-only steps whose history remains part of the compatibility contract:

| Version | Durable addition                                                                                                               |
| ------- | ------------------------------------------------------------------------------------------------------------------------------ |
| v15     | tagged download/upload jobs and outbox rows, immutable upload specs, and nullable `transfer_uploads.job_id` for legacy imports |
| v16     | `transfer_jobs.dismissed_at` plus `transfer_upload_activity` labels, target, total and confirmed bytes                         |
| v17     | immutable `transfer_upload_receipts` with object role, exact key, ETag/version, size, source digest and digest proof           |
| v18     | per-multipart URL style; old/imported rows receive the explicit `legacy_configured` sentinel                                   |
| v19     | normalized immutable upload `object_prefix`; `NULL` means an older row cannot prove its namespace                              |

Historical DDL is not deleted when an old runtime API is retired: opening and
migrating databases created by earlier releases depends on the complete chain.

`create_upload_job` writes the tagged job, immutable
`(entry_key, revision, input_digest, object_prefix)` spec and activity seed in
one transaction. The natural key is library entry plus publication revision,
while `input_digest` distinguishes an idempotent replay from changed input. A
live upload for a different revision of the same entry is a conflict.

Uploads execute outside the download worker path but use the shared state
invariants. `start_upload_job` validates the `queued -> preparing` edge through
`JobAggregate` and commits it with expected-version CAS. Complete and cancel use
the same terminal CAS/outbox transaction; the first terminal writer wins and a
late finish/cancel receives a stale or already-terminal result. The upload
projection reads the immutable spec, applies the matching library revision and
acknowledges only after the application state commits.

Retry uses the shared lineage. The parent must have an acknowledged compatible
terminal outcome; retry, repeat and supersede each create a child with its own
zeroed activity and no inherited multipart or receipt evidence. Exact child
replay is idempotent, while a changed immutable input is a conflict. The
original job, spec, lineage and terminal evidence remain addressable even when
the user dismisses the activity: dismissal writes only the general
`dismissed_at` tombstone.

Confirmed progress advances transactionally from acknowledged **data** parts;
publication/evidence parts do not inflate it. Before a multipart row is
retired, the worker durably stages that object's immutable verified receipt.
Terminal success projection requires the complete receipt batch and validates
every receipt against the job/revision, persisted object namespace and signed
local inventory. A legacy spec with `object_prefix = NULL` is acknowledged
without falsely changing the library row to `Done` because it cannot prove the
exact full object keys.

At startup, every non-terminal upload job is durably cancelled into the tagged
outbox before application state is exposed. `claim_orphan_uploads` then marks
surviving multipart rows for abort. A row is deleted after a confirmed abort or
not-found response. `UnknownUpload` is different: it may mean the handle was
aborted or that it was consumed by completion, so the row is retired only when
an exact structurally valid durable receipt accounts for that job/object;
otherwise it remains `aborting` and blocks dismissal and download-root changes.
A legacy `AppStore` row stuck at `Uploading` without a durable upload job becomes
an explicit failure. `pending-uploads.json` is backed up and imported once with
a transaction marker; corrupt input is retained and surfaced, never treated as
an empty queue. Credentials are absent from all of these records.

The object-store port deliberately has no version-safe completed-object delete
or provider orphan-list operation. It therefore cannot promise rollback of an
already completed object or repair the provider-create/DB-persist crash window.
Durable receipts and retained ambiguous rows are accounting and cleanup fences,
not a claim of atomicity across SQLite and the remote provider.

The S3 adapter also treats remote error text as untrusted. Buffered bodies are
bounded to 64 KiB (with oversized success responses rejected), user-visible
text is capped at 1 KiB, control/formatting characters are neutralized and
known credentials/session tokens are redacted. HTTP status still maps to typed
errors such as `UnknownUpload`, `NotFound`, `RateLimited` and `ServerError`;
sanitization never converts a failure into success.

## Startup And Shutdown

`src-tauri/src/lib.rs` wires setup and shutdown;
`src-tauri/src/application.rs` owns the application protocol and start/stop
boundary. Their shared order is part of the correctness contract:

```text
app_data_dir / app-state.sqlite3 + transfer_store.sqlite3
        |
        v
1. BootConfig::load (read/migrate configuration once)
        |
        v
2. Composition::new (inert stores, fleet, adapters, coordinator)
        |
        v
3. AppState::from_boot_config(...)
   -> vault migration, interrupted-upload reconcile, legacy archive
   -> app.manage(AppState + TransferApplication)
        |
        v
4. TransferApplication::start
   -> bind Tauri event sink
   -> Composition::recover_on_startup
        |
        v
5. start_background_loops()
   -> mDNS, heartbeat, transfer poll/tagged completion delivery
        |
        v
6. RunEvent::Exit -> TransferApplication::stop
```

Stage 2 cannot take an `AppHandle` and starts no thread or timer. Stage 5 is the
first point where loops may resolve managed state or emit events. Loop handles
are retained so `RunEvent::Exit` can abort them; detached loops are not part of
the lifecycle contract.

The configured download root is loaded before `Composition::new`. It is
validated and created, with a fallback to the app-data library root when it is
relative, cannot be created or is unusable. A runtime change is applied only
when the local library is empty and no durable job remains; otherwise it fails
closed so committed and pending files cannot be split between roots.

## Transfer State Machine

`TransferJobState` in `transfer/mod.rs` is the durable job-state enum. The legal
edge set is `transfer/aggregate.rs::is_legal_transition`; persistence stores
only a tag/version encoding and cannot invent a second domain graph.

```text
queued
  |\
  | +--> waiting_for_device --+
  | +--> waiting_for_pairing -+--> preparing
  | +--> paused_capture_active -+
  +------------------------------+

preparing -> transferring -> verifying -> committing -> succeeded
     |             |             |             |
     +-------------+-------------+-------------+--> failed(code, retryable)
                   |
                   +--> retry_wait -> queued

any non-terminal state -> cancelling -> cancelled
```

The graph is deliberately sparse; the reducer may route through `queued` but
cannot bypass an illegal edge. Terminal states have no outgoing transition.
Retry creates a fresh child job and preserves parent/attempt lineage instead of
resurrecting a terminal row.

`JobAggregate::decide` is pure. It reads state, desired-run state and version,
then returns an outcome and ordered effects. The coordinator executes effects
inside a per-job serial cell. A durable effect carries the expected version;
the CAS returns a stale result rather than overwriting another command. The
only lock release during a command is the explicit worker-release wait used by
pause/cancel, preventing a worker and its command from waiting on each other's
lock.

Worker reports identify the stage they completed (`Prepared`, `TransferComplete`,
`Verified`, `CommitComplete` or a typed failure/interruption). They never select
the next state themselves. `WorkQueue` is bounded, FIFO, wakeable and
de-duplicates scheduled IDs; target leases prevent two jobs from writing the
same session directory concurrently.

User pause is a desired-run-state bit. A parked job may remain in its waiting
state while the bit is set; an active transfer transitions to `retry_wait` after
the current chunk unwinds. Capture activity is separate and uses
`paused_capture_active`. Cancel acknowledges only after the worker has released
the file handle. Device readiness is one versioned snapshot of connection and
capture activity, so an older observation cannot re-park a job after a newer
one made it ready.

## Download And Publication Recovery

`PublicationTrust` is the only constructor of a verified publication. It binds
the SAS-confirmed key identity to the raw signed payload, checks schema and
inventory consistency, rejects unsafe/duplicate paths and validates digest
format. `VerifiedTransferPlan` is then the only input accepted by the durable
job-spec builder; callers cannot attach a different file list to a valid
signature.

For each expected file, `ArtifactInspector` combines the final file, `.part`
file and durable journal/checkpoint into one verdict:

| Verdict           | Meaning                                              | Restart action          |
| ----------------- | ---------------------------------------------------- | ----------------------- |
| `Missing`         | no trustworthy bytes                                 | request from offset 0   |
| `Partial(offset)` | only `min(part length, confirmed offset)` is durable | resume at `offset`      |
| `Verified`        | exact size and expected SHA-256                      | reuse without a request |
| `Invalid`         | present but wrong/unreadable/unsafe                  | discard and fetch again |

Progress sums these same verdicts. It never counts a same-size file with a
wrong digest, and a recovery run does not reset a verified baseline to zero.

Whole-session downloads use `library/staging.rs`: a revision derived from the
signed publication is assembled under hidden `.ylx-staging`, sealed only after
every manifest file is verified, and published with one directory rename. A
crash leaves one of `Absent`, `Staged`, `Sealed` or `Published`; rerunning the
same revision resumes or completes the pending step. Per-file downloads retain
their own atomic file publication and do not claim a complete session.

Selected-file publication uses the same revision-scoped staging evidence, then
merges only the requested files into the visible session tree. It writes a
scope-bound `.ylx-selected` marker containing the revision, requested count,
bytes and ordered manifest digest. It never writes `.ylx-revision`, and it does
not erase a pre-existing whole-session marker or unrelated verified siblings.

When the coordinator first observes a terminal state, `TransferStore` commits
the terminal transition and completion outbox row together. The completion
consumer then:

```text
durable transition + outbox
        -> apply local-library projection
        -> emit resource snapshot/event
        -> acknowledge outbox row
```

The projection is idempotent. A process crash after any arrow leaves the row
unacknowledged and therefore replayable; acknowledging before the library
commit is forbidden.

## Device And Frontend Ownership

One core identity module parses an optional case-insensitive `sha256:` prefix
followed by exactly 64 ASCII hexadecimal characters and stores bare lowercase
hex. Its projections have distinct contracts:

```text
canonical identity: ylx-<64 lowercase hex>
TLS pin:            sha256:<64 lowercase hex>
display label:      YLX-<first 8 uppercase hex>
```

`DeviceFleet` keys endpoints, clients and handles by the canonical full
identity; `Device.id`, session routing and frontend operation/navigation keys
use it too. `Device.displayId` and `LibraryEntry.deviceDisplayId` are labels
only. Two fingerprints with the same first eight hex characters intentionally
remain separate devices.

Durable compatibility is dual-read/canonical-write. New device-derived jobs
and library rows use canonical IDs, but old jobs, natural keys/request digests,
library directories, entry keys, delete intents, operation leases and S3 keys
are not blindly rewritten. The centralized resolver accepts a legacy
`YLX-<8 hex>` alias only when exactly one registered full identity has that
display projection: zero matches is unknown and multiple matches is an explicit
ambiguous error. Registry locking covers lookup and insertion only; each device
handle copies inputs, performs network I/O outside its lock and applies replies
with epoch/attempt fencing. A slow device cannot hold the registry lock for
another device's heartbeat, pairing, catalog or delete.

The frontend has one write path and one transport seam:

1. `runtime/tauriTransport.ts` is the only module that imports Tauri APIs.
2. `runtime/tauriBackend.ts` decodes commands/events into `TransferBackend` and
   stamps observations with revisions.
3. `runtime/start.ts` registers listeners before reading the start snapshot,
   buffers events, commits the snapshot and replays only newer events.
4. `runtime/reducer.ts` is the only state mutation entry point. Per-resource
   values retain their last good snapshot on refresh failure.
5. `app/transferApp.ts` owns command runners, confirmations, view generations
   and disposal. `ui/views/*` render typed state and delegate DOM events; they
   do not call the backend or own transfer state.

The activity DTO has one lifecycle authority: `Transfer.state`, a snake-case
string constrained by `TRANSFER_STATES`. Its runtime decoder fails closed when
it sees the retired `done`, `failed`, `queued` or `resumed` booleans. The richer
discriminated `TransferJobState` is reserved for durable job events;
`userPaused` reports desired-run control for parked jobs and is not a parallel
lifecycle field.

### Application and RPC boundary

`commands.rs` validates scalar and batch inputs, obtains
`TransferApplication`, invokes one facade method and maps failure to `RpcError`.
It does not expose raw persistence DTOs or coordinate repositories/network
adapters. Scalar strings are bounded at 4096 UTF-8 bytes and batches at 256
caller-supplied items before deduplication.

Batch dispatch commands have one tagged result per unique input:

```text
download_sessions({ deviceId, sessionIds })
upload_entries({ keys })
  -> { results: [
       { status: "success", item, jobId } |
       { status: "failure", item, error }
     ] }
```

Mutation batch **values** use the same tagged shape without `jobId`, inside the
resource revision envelope:

```text
delete_sessions / cleanup_backed_up
  -> { revision, value: { results, sessions, operationError } }

remove_library_entries
  -> { revision, value: { results, library } }
```

`operationError` represents a refresh failure that does not belong to any
item. The frontend verifies complete set coverage and rejects a missing,
duplicate or unexpected item instead of repairing positional arrays.

`RpcError` is `{ code, message, retryable, details? }`; `details` is absent or a
JSON object, and the shared code allowlist is the machine-readable branch
contract. Unknown codes/statuses, malformed envelopes and legacy array results
fail closed at the decoder. For uploads, the library key is only the start
input. `upload_entry`, batch dispatch and retry yield durable job IDs; upload
`Transfer.key`, retry/cancel/dismiss identity and `{ jobId }` control payloads
all refer to the same `UploadJobId`.

Rust owns the revision clock. The aggregate read is one atomic application
snapshot with both an outer boundary and resource-specific boundaries:

```text
read_snapshot -> { revision, value: {
  devices:   { revision, value },
  library:   { revision, value },
  transfers: { revision, value },
  storage:   { revision, value }
} }
```

`list_devices`, `list_sessions`, `list_library`, `list_transfers` and
`get_storage_config` always return `{ revision, value }`; there is no
value-only production response or synthetic frontend revision fallback.
The aggregate and the individual devices/library/transfers/storage reads use
only the immutable published cache. `list_sessions` is intentionally
effectful: it refreshes one canonical device over the network while holding no
publication lock, then publishes that exact value. Its response and that
device's `sessions:update` event share the newly allocated device-scoped
revision. A per-canonical-device async operation gate serializes list/catalog
refresh, delete, backed-up cleanup, downloaded cleanup and background
completion refresh from their network read/delete through cache publication
and response. Different devices use independent gates and remain concurrent;
the gate-map mutex is held only while cloning the device's gate, never across
network I/O or together with publication, application-state or global locks.

`add_manual_device` returns `{ revision, value: Device }`; its revision is the
same one used to publish the complete `devices:update` value. Session
mutations, downloaded cleanup, library removal and storage saves also return
the revisioned resource projection. When a mutation returns a value and emits
an event, both carry the same allocated revision. A failed event delivery is
logged and testable but does not undo the durable write or published cache,
nor alter the command response.
The cache lock is released before event delivery, so concurrent publishers do
not promise FIFO delivery. Consumers discard a late event by its server-issued
revision; the revisioned command response is also a convergence path when an
event cannot be delivered. Startup replay uses only the inner resource
revisions that its successful snapshot/fallback reads actually cover, so a
high revision for one resource cannot hide another resource's event.

Production typed event emission requires the managed `TransferApplication`.
If it is absent, the bridge returns `application_unavailable` rather than
inventing revision 1; the fixed-revision fallback is compiled only for the
`cfg(test)` mock-app path.

## Production, Migration And Demo Boundaries

The default Cargo feature set contains no simulator. `demo.rs`, `sim.rs`, the
seed fleet and `DemoTransferState` are all immediately guarded by
`#[cfg(feature = "demo")]`; production upload activity is always projected from
durable jobs, and production `AppData.transfers` no longer exists.
Source-architecture tests fail when retired runtime identifiers,
raw persistence orchestration or an ungated demo declaration returns to the
production command/application surface.

Not every historical name is forbidden globally. The one-shot pending-download
and pending-upload import DTOs, byte-for-byte backups, migration markers,
legacy-import tests, `.part.journal` download evidence and historical schema DDL
remain required compatibility artifacts. They are excluded from the runtime
authority and from the production/demo gate rather than deleted.

## CI And Evidence

The workflow at `.github/workflows/ci.yml` is the authoritative command list.
It uses Node 22, bounded jobs and same-ref cancellation. The configured evidence
lanes are:

- frontend tests, formatting, ESLint, TypeScript and Vite build;
- workspace Rust fmt, clippy and all-target/all-feature tests;
- a pinned MinIO contract covering multipart resume/abort, completion-bound
  verification, metadata/content mismatch, 429/5xx/network loss and cleanup;
- the filesystem-recovery contract on Ubuntu, macOS and Windows;
- workspace clippy/tests and the Tauri build on all three platforms;
- the manually triggered, pinned cross-repository Pi integration; and
- release packaging/publishing for an existing numeric SemVer tag.

The cross-repository Pi lane is manual (`workflow_dispatch` with
`run_cross_repo=true`) and pins RP-YLX to
`2db57ae68e04197397b8ac84f4d71548aa2fcb36`. It verifies every fixture and
records both revisions. Missing credentials, repositories or fixtures fail the
lane; they are not counted as an ignored pass. The lane is evidence about a
specific pair of revisions, not a substitute for a local unit test.

Use these local commands when reproducing the workflow:

```bash
npm ci
npm test
npm run format:check
npm run lint
npm run typecheck
npm run build

cargo fmt --manifest-path src-tauri/Cargo.toml --all -- --check
cargo clippy --manifest-path src-tauri/Cargo.toml --workspace --all-targets --all-features -- -D warnings
cargo test --manifest-path src-tauri/Cargo.toml --workspace --all-targets --all-features

bash src-tauri/crates/ylx-transfer-adapters/tests/support/run_minio_object_store_contract.sh
cargo test --manifest-path src-tauri/Cargo.toml -p ylx-transfer-core --test fs_recovery_contract --all-targets --all-features -- --nocapture
```

These are gates, not claims about the current machine. A desktop build also
needs the platform's Tauri/WebKit prerequisites; cross-platform build evidence
comes from the CI matrix or a host with those dependencies installed.

The local PC audit recorded on 2026-08-04 at C72 commit `0f98097` observed the
following results: all 281 frontend tests, typecheck, lint, build and format
check passed; `ylx-transfer-core` passed 305 unit tests plus every enabled
integration suite and strict Clippy; and the adapters passed 110 tests plus
check, strict Clippy and rustfmt. Eight adapter tests that require a real
service or manual setup remained explicitly ignored. The full Tauri
application source passed check and strict Clippy with a temporary
`pkg-config` shim, and workspace rustfmt passed. These were local commands, not
a hosted-CI run.

Evidence for this checkout must come from the final audit/run, not from an old
commit's test count. On the current Linux host, native Tauri test execution and
linking require GTK/GDK 3, WebKitGTK 4.1 and DBus development libraries that are
not installed. Windows cross-checks additionally require a usable MinGW target
toolchain and compatible `aws-lc-sys` support. A real MinIO run requires its
pinned image to be available from the configured registry. A check blocked by
one of these prerequisites is recorded as blocked, never as a pass; source
compilation with a temporary `pkg-config` shim is not equivalent to a linked
desktop test run.
