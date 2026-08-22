# ADR-PC-001: Application And Transfer Persistence

- Status: Accepted
- Scope: PC application state, download/upload jobs, file evidence and
  completion handoff
- Owners: `src-tauri/src/state.rs`, `src-tauri/src/application.rs`,
  `src-tauri/src/composition.rs`, `ylx-transfer-core::persistence`
- Related implementation map: [docs/ARCHITECTURE.md](../ARCHITECTURE.md)

## Decision

Use transactional SQLite repositories with explicit ownership:

1. `AppStore` is the authority for the local library and the non-secret
   storage profile. It is persisted in `app-state.sqlite3`.
2. `TransferStore` is the authority for tagged download/upload job identity,
   immutable specs, state/version, desired run state, retry lineage, download
   file ledgers, durable upload activity/visibility, multipart upload records,
   verified upload receipts and the tagged terminal completion outbox. It is
   persisted in `transfer_store.sqlite3`; the current schema is v19.
3. The `JobAggregate` owns the legal state graph and command decisions. The
   persistence layer stores tags and versions and executes expected-version
   CAS; it does not define a second domain state machine.
4. Completion projection is the only bridge from a terminal transfer to the
   application library. Download and upload consumers select rows by
   `operation_kind`, read the matching immutable spec, apply the application
   projection and acknowledge only after the application state commits.
5. Secrets are owned by `CredentialVaultPort` and its platform adapter. The
   application store, transfer store, event payloads and read DTOs contain only
   credential-existence metadata.

The repository has one Cargo workspace at `src-tauri/Cargo.toml`, with the Tauri
application, core and adapters as members, and one lockfile at
`src-tauri/Cargo.lock`. These are the repository's only workspace manifest and
lockfile.

## Context

The original application wrote library data and storage settings to one
`store.json` document. Read and write errors were swallowed, and independent
download state, checkpoints, pending contexts and in-memory queues could be
updated in different orders. A process crash could therefore produce any of
the following:

- a task row without a complete file plan;
- a partial file that was counted as complete;
- a terminal job whose library result was never applied;
- a duplicate job after restart; or
- a plaintext object-store credential left in an application snapshot.

The repository also accumulated an isolated persistence comparison and
separate request/state JSON files. Those experiments answered useful engine questions,
but retaining each as a production authority would preserve the same split-fact
failure mode this decision removes.

## Repository Ownership

### `AppStore`

`AppStore` owns application-level records:

- local-library entries and their upload status;
- endpoint, bucket, prefix, URL style and download-root settings; and
- a monotonically increasing application revision used by snapshots/CAS.

The store uses versioned migrations, integrity checks, transactions and
structured persistence errors. The persisted storage payload has no access-key
or secret-key fields. `get_storage_config` returns `secretConfigured`; the
write-only save input sends a secret directly to the vault and never to this
store.

Application reads and projections expose backend-issued revisions only.
`read_snapshot` atomically returns one outer application revision plus inner
revisioned device, library, transfer and storage values. Those four individual
reads consume the same immutable published cache. `list_sessions` alone is an
effectful device-scoped refresh: network I/O occurs outside the publication
lock, after which the exact value is published and returned with the same
revision used by its event. A per-canonical-device async gate serializes that
refresh with the same device's delete, cleanup and background completion
refresh through publication/response; another device uses an independent gate.
The gate-map lock only obtains the gate and is never held across network I/O or
with application/publication locks. `add_manual_device` returns a revisioned
`Device` with the same revision as its complete `devices:update` publication.
Session/library mutations and storage saves also return `{ revision, value }`
envelopes. A mutation's returned projection and corresponding event use the
same allocated revision.
Failure to deliver the event is observable but does not roll back the durable
write or published cache, nor fabricate another response revision. Publication
unlocks the cache before delivering events, so concurrent publishers do not
promise FIFO. The frontend discards late events by server revision and uses the
revisioned command response as a convergence path; it needs no synthetic
revision fallback and replays startup events against only the resource
revisions its snapshot actually includes.

Production typed event emission requires the managed application facade and
returns `application_unavailable` when it is absent. A fixed revision is
permitted only in the `cfg(test)` mock-app fallback, never in production.

### `TransferStore`

`TransferStore` owns every durable download or upload request from creation
through completion:

```text
job identity + operation_kind + natural key + request/input digest
        + immutable download JobSpec and ordered file rows
          OR immutable upload entry/revision/input spec
        + state/version/desired run state
        + parent/child retry lineage
        + download file ledger/checkpoints
        + upload activity/visibility tombstone
        + multipart handles/parts/URL style/desired state
        + version-bound verified upload receipts
        + operation-tagged terminal completion outbox
```

Download `create_job` is one transaction. A natural-key match with the same
request digest returns `Existing`; a different digest is a conflict; a
primary-key collision is a separate error. No caller may turn either conflict
into a different job.

The current schema is v19. The forward-only upload migrations intentionally
remain visible because each version can still be an upgrade source:

- v15 adds tagged operation kinds, immutable upload job specs, nullable legacy
  multipart-to-job linkage and tagged completion outbox rows;
- v16 adds the general `transfer_jobs.dismissed_at` tombstone and durable
  `transfer_upload_activity` metadata/progress;
- v17 adds immutable `transfer_upload_receipts`, including data/evidence role,
  exact object key, ETag, optional version ID, size, source SHA-256 and the
  digest-proof method;
- v18 persists each multipart handle's URL style, using
  `legacy_configured` only when an older row cannot prove the original style;
  and
- v19 adds the normalized immutable `object_prefix`; `NULL` is an explicit
  unknown legacy namespace, while `""` is a known root namespace.

`create_upload_job` transactionally writes the tagged job, immutable
`(entry_key, revision, input_digest, object_prefix)` spec and activity seed.
Its natural key is the library entry plus publication revision;
`input_digest` distinguishes an idempotent replay from changed local material or
storage input. A live upload for another revision of the same entry is an
explicit conflict.

Download `complete_job` and upload `complete_upload_job` commit the terminal
state and matching tagged outbox record together. A consumer can therefore
replay a terminal outcome after a crash without guessing whether the library
update happened.

### `JobAggregate` and `TransferCoordinator`

`JobAggregate::decide` is pure and exhaustive over the tagged job state and
commands. It returns effects for the coordinator to execute. Every durable
transition carries the version it was decided against. A stale CAS is an
observable conflict, never an overwrite.

`TransferCoordinator` provides the serialized runtime entry point, bounded
ready-set scheduling, per-job target leases, cancellation acknowledgement and
worker supervision for downloads. It may keep caches for labels or progress,
but those caches are projections of the repositories and are rebuilt at
startup.

Uploads do not use download workers. `start_upload_job` nevertheless validates
the `queued -> preparing` edge through `JobAggregate` and commits with
expected-version CAS. Complete and cancel use the same terminal CAS/outbox path:
the first writer wins, while a late finish or cancel receives stale or
already-terminal and cannot replace the result.

Both operation kinds use durable lineage. Upload retry, successful repeat and
supersede create a new child only after the relevant terminal completion is
acknowledged. Each child receives a transactional immutable spec and activity
row but starts with zero progress and no inherited multipart or receipt
evidence. Exact replay returns the same child; changed immutable input conflicts
instead of silently reusing it.

Upload progress is not inferred from UI state. Confirmed bytes advance in the
same transaction as acknowledged **data** parts; publication/evidence parts do
not count toward the data total. A terminal activity can be hidden by setting
`dismissed_at`, but dismissal never deletes the job, spec, retry lineage,
completion outbox, multipart state or verified receipts.

Each verified receipt is staged durably before its completed multipart row is
retired. Terminal success projection requires the complete receipt batch and
checks it against the immutable job/revision, persisted object namespace and
signed local inventory. A v19 job must match exact full keys for every data file
and publication evidence object. A legacy `object_prefix = NULL` success can be
acknowledged for durable history but cannot promote a library row to `Done` by
guessing the current namespace.

### `ArtifactInspector` and staging

`ArtifactInspector` is the sole judge of file evidence. For every expected file
it returns exactly one of `Missing`, `Partial`, `Verified` or `Invalid` based on
the final file, `.part` file and durable journal/checkpoint. Progress and resume
consume those same verdicts.

Whole-session transfers use revision-scoped staging. A complete, sealed and
verified revision is published by one directory rename. A single-file transfer
publishes only that file and cannot claim the complete session.

Selected-file publication merges only the requested verified files and writes
`.ylx-selected`, whose scope, revision, count, bytes and ordered manifest digest
bind it to that subset. It never writes the `.ylx-revision` whole-session seal
and does not overwrite unrelated files or an existing complete-session claim.

## Startup And Recovery Contract

Startup is ordered and has one owner at each stage:

1. `BootConfig::load` opens `AppStore`, applies migrations and reads the
   persisted configuration exactly once.
2. `Composition::new` constructs an inert runtime and opens the repositories;
   no loop, timer or worker may observe application state yet.
3. `AppState::from_boot_config` migrates legacy application data/credentials
   and reconciles interrupted uploads before `AppState` and
   `TransferApplication` are registered.
4. `TransferApplication::start` binds the Tauri event sink and runs
   `Composition::recover_on_startup` after managed state exists.
5. Only after registration and recovery do mDNS, heartbeat and transfer-poll
   loops start. Their handles are retained for `TransferApplication::stop` on
   `RunEvent::Exit`.

Recovery rules are evidence-based:

- A job interrupted in a non-terminal transfer stage returns to a retryable
  state while preserving durable per-file evidence.
- A final file is reused only after size and expected digest verification.
  Same-size, wrong-content files are invalid and are fetched again.
- A partial resumes from the lower of the actual `.part` length and the last
  durable checkpoint. Untrusted trailing bytes are truncated before resume.
- A terminal outbox row is applied to the library before acknowledgement. A
  crash before acknowledgement leaves it pending for the next poll/start.
- `pending-downloads.json` and `pending-uploads.json` are accepted only as
  one-time import inputs. Each import is transactional and marker-backed, and
  a byte-for-byte backup/diagnostic source is retained through successful
  cleanup; the migration outcome is logged. Corrupt input is retained and
  surfaced with identifying context, never treated as an empty queue.
  Historical migration DDL and importer tests remain even after the
  corresponding runtime writers are removed.
- Download `.part.journal` files remain current per-file durability evidence;
  they are not the retired whole-job sidecar and are reconciled with SQLite
  checkpoints and actual file length.
- Every non-terminal durable upload job is cancelled into the tagged outbox
  before the application state becomes visible.
- `claim_orphan_uploads` marks surviving multipart rows for abort. A row is
  deleted only after remote abort succeeds, reports not found, or returns
  `UnknownUpload` while an exact structurally valid receipt proves that same
  job/object completed. An ambiguous `UnknownUpload`, unavailable vault or
  unavailable object store leaves the row durable and cleanup-blocked for
  another launch.
- An application-library row left at `Uploading` without a durable upload job
  is a legacy orphan and becomes an explicit failure.
- Upload outbox projection reads the immutable upload spec, refuses to apply an
  old result over a changed publication revision, persists the application
  state and then acknowledges the completion.
- Credentials are never copied into a job, outbox or multipart recovery row.

`ObjectStorePort` has no version-safe completed-object delete and no provider
orphan-list operation. Therefore an already completed remote object cannot be
honestly claimed as rolled back, and a crash after the provider creates a
multipart handle but before SQLite records the response is outside the local
transaction. This is an explicit saga/provider-retention limitation. Durable
receipts account for verified effects and retained ambiguous rows prevent false
cleanup claims; neither mechanism creates cross-system atomicity.

## Device Identity Compatibility

The full normalized TLS fingerprint is the identity. The core parser accepts an
optional case-insensitive `sha256:` prefix and exactly 64 ASCII hex characters,
stores bare lowercase hex, and derives:

```text
canonical durable/RPC/path identity: ylx-<64 lowercase hex>
TLS pin:                             sha256:<64 lowercase hex>
display-only legacy alias:           YLX-<first 8 uppercase hex>
```

New devices, jobs and library entries are canonical-write. Existing jobs,
natural keys/request digests, library directories, entry keys, delete intents,
operation leases and object keys are not bulk-rewritten: their spelling is
part of durable identity and idempotency history. Reads may resolve a legacy
short alias against registered full identities only. Zero matches is unknown,
one match is compatible, and multiple matches fail closed as ambiguous. This
dual-read/canonical-write policy preserves old evidence without allowing an
eight-character collision to select the wrong device.

## RPC Identity And Outcome Projection

Persistence rows never cross the Tauri boundary. The thin command adapter
returns application DTOs and tagged per-item batch outcomes. Dispatch success
is `{ status: "success", item, jobId }`; mutation success omits `jobId`; failure
is `{ status: "failure", item, error }`. `error` has stable
`code/message/retryable/details?` fields, and `details` is absent or a JSON
object. Session batch refresh failures are separate `operationError` values so
they cannot be attributed to an arbitrary item.

The frontend accepts one verdict for each unique requested item and rejects
missing, duplicate, unexpected, malformed or legacy parallel-array responses.
For uploads, a library key identifies only the requested source entry. Once
started, `upload_entry`, batch dispatch and retry produce the durable job ID;
the upload activity `Transfer.key` and retry/cancel/dismiss controls use that
same `UploadJobId` (`{ jobId }` on cancel/dismiss), never a process-local key.

## Why SQLite

SQLite is chosen over extending a single JSON document because it provides the
transaction and migration primitives required by both repositories:

- multi-row job creation and terminal handoff commit as one unit;
- WAL transactions preserve the last committed state after a process crash;
- integrity checks and typed errors distinguish corrupt data from first launch;
- schema versions and migration markers make upgrades explicit and repeatable;
- independent rows limit the blast radius of a damaged library or job record;
- the bundled SQLite build avoids a runtime system-SQLite dependency.

The old JSON/SQLite comparison implementations and generic credential port have
been removed; neither survives as a second test authority. The decision record
retains the comparison rationale, while current tests exercise `AppStore`,
`TransferStore` and their production-compatible adapters directly.

## Migration Rules

Migrations are forward-only, idempotent and observable:

- validate the current schema version before applying a step;
- reject an unknown future version rather than opening it as an empty store;
- run each migration in a transaction and record its marker in that same
  transaction;
- preserve legacy files until the replacement data and migration marker are
  durable; and
- retain a concrete backup/diagnostic path when an import is malformed.

Credential migration is ordered separately from data migration: write the
credential to the OS vault first, then persist the secret-free application
snapshot. If the vault write fails, the old source remains intact for retry.

## Rejected Alternatives

### One JSON document

Even with temporary-file/`fsync`/rename, a single corrupt document can erase the
library and settings together, has no relational migration contract and cannot
atomically coordinate a job, file ledger and completion receipt.

### Split job state and request persistence

A separate job-state log plus request index stores the same lifecycle in two
representations. A crash can leave a state without the immutable input required
to rehydrate it, or input without the state/version that owns it. The accepted
architecture keeps both in `TransferStore` and lets `JobAggregate` own the
graph.

### Secrets in application persistence

Plaintext credentials in SQLite/JSON or returned to WebView are rejected. The
vault boundary exposes only get/set/delete and existence status; errors are
redacted and bounded.

## Residual Risk And Evidence Boundary

This ADR records ownership and invariants, not a claim that every gate has run
on every host. The authoritative checks are the repository's frontend tests,
the unified Cargo workspace targets, pinned MinIO contract, three-platform
filesystem-recovery/Tauri matrices and the manually triggered Pi integration
pinned to RP-YLX `2db57ae68e04197397b8ac84f4d71548aa2fcb36`. A release or
architecture claim must cite the corresponding observed run; this document does
not turn a configured or unexecuted lane into a pass.

Evidence for this checkout must come from the final audit/run, not from an old
commit's test count. The current Linux host lacks the GTK/GDK 3, WebKitGTK 4.1
and DBus development libraries required to link and execute native Tauri tests.
Windows cross-checks require a usable MinGW target toolchain and compatible
`aws-lc-sys` support; a real MinIO run requires the pinned image to be available
from its registry. These are environment blockers, not passes, and source
compilation through a temporary `pkg-config` shim is not a linked desktop test.

The local PC audit recorded on 2026-08-04 at C72 commit `0f98097` did execute
the hermetic lanes: all 281 frontend tests and the frontend
typecheck/lint/build/format gates passed; `ylx-transfer-core` passed 305 unit
tests plus all enabled integration suites and strict Clippy; and the adapters
passed 110 tests plus check, strict Clippy and rustfmt. Eight adapter tests that
require a real service or manual setup remained explicitly ignored. Full
application source check and strict Clippy passed through the temporary
`pkg-config` shim, and workspace rustfmt passed. No hosted CI, Windows
cross-check or real MinIO service run is implied by those local results.
