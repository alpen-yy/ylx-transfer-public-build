# Ubuntu TF Card Video Pipeline Remaining Tasks

Status: RELEASE_NOT_COMPLETE

Date: 2026-08-06

This document records the remaining work after the local implementation and
automation gates were stabilized. It is derived from
docs/UBUNTU_TF_CARD_VIDEO_PIPELINE_REBASELINE_EXECUTION_PLAN.md, especially
section 18, and from the current release-evidence rules.

The key distinction is:

- Local code and contract tests are passing.
- Release qualification still requires dated, reviewable evidence from real
  Ubuntu hardware, real removable media, real video corpus, and real
  object-store/package environments.
- Synthetic fixtures and in-memory contract runs must not be promoted to
  hardware, codec, quality, legal, playback, or production-storage evidence.

## Current Baseline

The following local gates have already passed and are recorded in
docs/release-evidence/ubuntu-tf/local-automation-2026-08-06.md:

- cargo fmt --check
- npm run format:check
- git diff --check
- npm test: 295 tests passed
- npm run typecheck
- cargo check --workspace
- cargo test -p ylx-transfer --lib -- --test-threads=1: 215 tests passed
- cargo test --workspace

The previous ylx-transfer lib-test hang was caused by default tests reaching
the real OS credential backend during startup. Test builds now use the
deterministic in-memory credential vault; production builds continue to use
the OS keyring. This fixes the local automation blocker but does not qualify
the real keyring integration.

## Priority Model

| Priority | Meaning                                                  |
| -------- | -------------------------------------------------------- |
| P0       | Blocks any release-complete claim                        |
| P1       | Required to prove Import-ready or Code-complete behavior |
| P2       | Required before package/release sign-off                 |

## P0: Real Qualification Evidence

### P0.1 Ubuntu hardware and removable-media HITL

Run the complete matrix on a clean Ubuntu 24.04 LTS x86_64 target:

- Insert an unmounted removable TF card and verify the UDisks2 attach request,
  refusal, authorization error, and re-enumeration paths.
- Verify that an ordinary empty card is distinguishable from a card whose
  recordings directory is inaccessible.
- Verify access-denied behavior caused by a different UID or filesystem
  permissions. The UI must show an access issue rather than an empty library.
- Verify Ubuntu Core nested recordings under the fixed container allowlist.
- Verify direct-child scanning and bounded traversal. The scanner must not
  recursively search arbitrary card content.
- Verify that an internal ext4 SSD, an unknown removable device, and a virtual
  device do not become media candidates.
- Verify library-root protection when the destination is on the current TF
  card or another removable device.
- Pull the card during scan and during copy. The process must remain alive,
  release handles, and converge to waiting_for_media or another truthful
  typed state.
- Replace a card at the same mount path and verify that the old transfer is
  not resumed against the replacement card.
- Reinsert the exact signed card and verify exact-revision re-admission.
- Reinsert an unsigned card and verify that approval is required again.
- Exercise files larger than 4 GiB and long sessions without integer
  truncation.
- Exercise read-only, full-disk, and inode-exhaustion failures.
- Verify pause, cancel, shutdown, and release/eject behavior within the
  configured deadline.

Required evidence:

- Dated discovery-import-hitl.md
- Bounded environment/build identity
- Scenario-by-scenario expected verdict
- Artifact digest or opaque evidence ID
- No credentials, mount paths, usernames, serial numbers, or raw diagnostics

### P0.2 Real codec and quality corpus

Collect and run a controlled corpus covering:

- Low texture, strong motion, exposure changes, and repeated texture
- Near/far scenes and left/right eye brightness differences
- High-frequency IMU motion and long recordings
- MJPEG and H.264 generations
- Source corruption, VFR, extra tracks, audio, truncated MP4, and decode
  failures
- Target playback and downstream CV workloads

Record at least:

- VMAF mean, frame p01, and SSIM
- Stereo/CV domain metrics
- Frame/timestamp/keyframe alignment
- Output size ratio
- Encode FPS, CPU, peak memory, and temporary disk usage
- Target decoder/player compatibility
- Input corpus digest and tool/build identity

Required evidence:

- Dated codec-quality-report.md
- Dated throughput-resource-report.md
- Dated stereo-cv-report.md
- Controlled corpus digest
- Explicit pass/fail/blocked result per profile

The corpus must remain under its controlled handling policy. Raw videos,
signed publication material, and absolute source locations must not be copied
into this repository.

### P0.3 Object-store qualification

Complete both S3-compatible contract and production-compatible smoke testing:

- MinIO path-style and virtual-host behavior
- Multipart initiate, part upload, completion, abort, and readback
- Restart after each durable checkpoint
- Ambiguous completion and versioned-object behavior
- Same-key overwrite and receipt binding
- Completion-bound checksum/readback verification
- Final manifest uploaded after all data and evidence objects
- Remote receipt projection before showing object-store verification
- One approved production-compatible object-store target

Required evidence:

- Dated minio-contract-report.md
- Dated production-storage-smoke.md
- Credential-free storage profile identity
- Checksum/readback result
- Restart/replay result
- No access keys, secrets, SAS strings, or signed bytes in the evidence

An in-memory or MinIO-only pass cannot close the production object-store gate.

### P0.4 Legal, package, and playback qualification

Before enabling an approved profile or shipping a release package:

- Record the shipped FFmpeg/libx265 build identity.
- Review encoder packaging and distribution/license obligations.
- Prove that the approved profile manifest matches the shipped encoder/build.
- Cold-install the Ubuntu package on the target class.
- Verify startup, capability detection, and migrations from a clean install.
- Verify target decoder/player compatibility for every release output class.
- Record package artifact digest and compatibility evidence.

Required evidence:

- Dated encoder-legal-review.md
- Dated playback-compatibility.md
- Dated package-install-report.md
- checksums.txt

## P1: Contract and Implementation Closure

### P1.1 Contract and discovery audit

The implementation and fixture contract must be reviewed against the final
plan, with evidence for every item:

- Main implementation specification synchronized with Ubuntu Core nested
  paths, UDisks2 attach, and unsigned publication.
- Supported platform explicitly limited to Ubuntu 24.04 LTS x86_64.
- removable=Yes, filesystem allowlist, and non-system gates enforced.
- Attach refusal, permission failure, and access issue projected truthfully.
- Three fixed containers, direct-child bounds, and no-follow behavior enforced.
- Internal SSD, unknown removable, and virtual devices rejected before
  candidate creation.

The synthetic fixture set can prove schema and contract behavior only. It
cannot replace the real hardware matrix.

### P1.2 Trusted admission and asynchronous import

Close the signed/unsigned admission and import lifecycle:

- Paired signed media must admit while the Pi is offline.
- Rotated, bad, and unpaired producer keys must fail closed.
- An unsigned publication must require explicit approval.
- A half-present signature pair must never silently downgrade to unsigned.
- Commands must enqueue bounded work rather than synchronously copying large
  files.
- Root authority and destination guard must cover every I/O phase.
- Remove, replacement, and reinsert semantics must be revision-aware.
- After LocalVerified, no TF-card reader may remain open.

Add or complete fault-injection evidence at these boundaries:

- After file write and before checkpoint
- After target hash and before source rename
- After source rename and before terminal transaction
- After import outbox write and before/after AppStore commit
- Before and after outbox acknowledgement

Every restart must converge without manufacturing a terminal success.

### P1.3 Media library projection

Verify the library boundary independently from the legacy LAN
LibraryEntry model:

- Import outbox consumer is idempotent.
- Ack is durable and replay-safe.
- Media projection is loaded during boot.
- Wire DTOs and UI render the projection without leaking internal evidence.
- Stale projection and AppStore revision conflicts fail closed.
- Repeated completion delivery does not duplicate entries.
- Projection recovery after process restart converges to one durable result.

### P1.4 Normalization and derived projection

Before a profile can be approved:

- Normalizer must read only sealed PC source artifacts.
- Six real input classes must map to the correct typed normalization inputs.
- Production path must use the real FFmpeg quality analyzer and stereo
  evaluator.
- VMAF, SSIM, stereo/CV, and full-decode reports must be bounded and durable.
- Report digests must bind to the exact source, profile, and build.
- The approved profile must contain all five required receipt classes.
- Profile approval must match the shipped encoder/build artifact.
- Derived output must use atomic commit and a recoverable completion outbox.

The current empty approved-profile manifest intentionally keeps this path
blocked until the corpus and review evidence exist.

### P1.5 Derived upload

Verify the derived upload implementation against the durable upload contract:

- v20 typed derived subject and natural key
- Frozen bundle sourced only from DerivedReceipt and sealed artifacts
- Independent approval receipt for unsigned upload
- Durable multipart state at initiate, part, complete, abort, and readback
- Sidecar/checkpoint CAS and restart recovery
- Completion-bound checksum and remote verification
- Final manifest uploaded last
- Remote receipt projection before object-store verified
- Derived uploads never displayed as source backups
- Archival, retention, and delete remain disabled

Add fault-injection evidence for:

- Bundle freeze before and after upload job creation
- Upload job creation before pipeline attach
- Multipart initiate before handle checkpoint
- Part success before part checkpoint
- Completion response before completion checkpoint
- Verification before receipt, terminal, and outbox boundaries
- Remote projection before upload outbox acknowledgement

## P1: Lifecycle and UI Closure

### P1.6 Startup and shutdown ownership

Prove the complete lifecycle ordering:

- Startup drains outboxes and recovery before workers and watchers begin.
- Every owned worker, executor, watcher, and queue stops on shutdown.
- Stop deadlines produce a typed resource_stuck result instead of hanging.
- All owned resources are reaped and joined.
- Restart never duplicates worker lanes or subscriptions.

### P1.7 Capability and mode semantics

Verify that the following modes activate only when the real capability exists:

- ImportOnly
- AutoNormalize
- AutoUpload

Source, derived, and remote status/progress must remain separate. A source
import success must not imply derived verification or remote verification.

### P1.8 UI state and command visibility

The UI must visibly expose:

- Access issue
- Waiting for approval key
- Waiting for media
- Two-step approval for unsigned publication
- Credential configuration/action
- Source progress
- Derived progress
- Remote upload progress
- Typed failures, retryability, and cancellation state

Strict decoder, batch validation, stale projection, root conflict, and storage
conflict contracts must remain covered by automated tests.

## P2: Evidence and Release Administration

Create and review the complete release evidence set:

| Evidence record               | Current state                                 |
| ----------------------------- | --------------------------------------------- |
| fixture-manifest.md           | Contract-only record can be completed locally |
| discovery-import-hitl.md      | Not collected                                 |
| fault-injection-report.md     | Not collected as release evidence             |
| codec-quality-report.md       | Not collected                                 |
| throughput-resource-report.md | Not collected                                 |
| stereo-cv-report.md           | Not collected                                 |
| encoder-legal-review.md       | Not collected                                 |
| playback-compatibility.md     | Not collected                                 |
| minio-contract-report.md      | Not collected as release evidence             |
| production-storage-smoke.md   | Not collected                                 |
| package-install-report.md     | Not collected                                 |
| checksums.txt                 | Not collected                                 |

Every record must include:

- Test/scenario name
- Collection date
- Code revision
- Environment class
- Tool/build identity
- Expected and observed result
- Controlled artifact digest or opaque evidence ID
- Reviewer/sign-off state

Evidence must not contain credentials, private keys, signed bytes, absolute
paths, usernames, mount paths, or unbounded raw command output.

## Recommended Execution Order

1. Finish the local R0/R1 contract audit and complete the contract-only
   fixture manifest.
2. Prepare a clean Ubuntu 24.04 LTS x86_64 qualification machine and run the
   removable-media/UDisks2/import HITL matrix.
3. Run import fault injection and restart convergence tests, then create the
   fault-injection report.
4. Run the controlled codec/quality/throughput/stereo corpus.
5. Review and approve profiles only after the corpus, reports, and shipped
   build identity match.
6. Run MinIO contract qualification and then the approved production-compatible
   object-store smoke.
7. Complete package, legal, playback, and cold-install review.
8. Run the final release CI matrix and produce checksums/sign-offs.
9. Only after all required records are independently reviewable, change the
   release decision from RELEASE_NOT_COMPLETE.

## Explicit Non-Goals Until Release Gates Close

Do not:

- Fill the approved profile manifest merely to make normalization run.
- Treat synthetic fixtures as real camera, codec, quality, or hardware proof.
- Treat an in-memory or MinIO upload as production object-store proof.
- Treat a successful local upload as remote verification without a durable
  receipt and checksum/readback evidence.
- Enable archival, retention, or delete behavior before the corresponding
  policy and recovery evidence exists.
- Claim Release-complete because a card appears in the UI, a video transcodes,
  or an object upload succeeds once.
