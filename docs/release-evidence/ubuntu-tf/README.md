# Ubuntu TF Release Evidence

This directory is the evidence boundary for the Ubuntu TF-card media pipeline. The directory and its
template are scaffolding only. They do not constitute Import-ready, Code-complete, or Release-complete
evidence.

## Current Truth

- The removable-media files under `fixtures/removable-media/` are synthetic contract fixtures. They
  exercise schema, provenance, path, and projection behavior only. They are not codec, quality,
  throughput, playback, hardware, or production object-store evidence.
- `src-tauri/resources/media-profiles/approved_profiles.json` is intentionally empty. No normalization
  profile is approved, and no derived result may be described as release-qualified on the basis of the
  repository fixtures.
- Real Ubuntu hardware, removable-media insertion/removal, UDisks2 authorization, object-store
  qualification, legal/package review, codec/quality corpus results, and playback compatibility evidence
  are absent from this repository unless a dated evidence record explicitly proves them.
- The current implementation and this evidence package must not be described as Release-complete.

## Evidence Rules

Every collected record must identify the test, date, code revision, environment class, tool/build identity,
result, and digest of any controlled artifact. Use repository-relative references or opaque evidence IDs.

Evidence files must not contain:

- credentials, access tokens, passwords, private keys, or SAS strings;
- signed publication bytes, detached signature bytes, or public-key bytes;
- absolute filesystem paths, mount paths, home-directory paths, or machine-specific usernames;
- unbounded raw command output or unredacted diagnostic text.

Record verification results, fingerprints, and cryptographic digests instead of copying sensitive material.
Replace local locations with a bounded logical reference such as `<library-root-relative-artifact>` and
store any controlled raw artifact outside this repository under an independently governed evidence ID.

## Evidence Set

Use [TEMPLATE.md](TEMPLATE.md) for one dated qualification or release record. The planned record set is:

| Record                           | Required proof                                                    | Current status                      |
| -------------------------------- | ----------------------------------------------------------------- | ----------------------------------- |
| `local-automation-2026-08-06.md` | Local format/check/test gate results                              | Local automation; not release proof |
| `fixture-manifest.md`            | Synthetic contract fixture inventory and expected verdicts        | Contract-only; not release proof    |
| `discovery-import-hitl.md`       | Ubuntu 24.04 removable-media and import behavior on real hardware | Not collected                       |
| `fault-injection-report.md`      | Crash, restart, cancellation, and recovery convergence            | Not collected as release evidence   |
| `codec-quality-report.md`        | Real corpus codec and quality measurements                        | Not collected                       |
| `throughput-resource-report.md`  | Encode/upload throughput and resource bounds                      | Not collected                       |
| `stereo-cv-report.md`            | Domain-specific stereo/CV quality acceptance                      | Not collected                       |
| `encoder-legal-review.md`        | Shipped FFmpeg/libx265/legal/package review                       | Not collected                       |
| `playback-compatibility.md`      | Target decoder/player compatibility                               | Not collected                       |
| `minio-contract-report.md`       | S3-compatible integration and restart evidence                    | Not collected as release evidence   |
| `production-storage-smoke.md`    | Approved production-compatible object-store smoke                 | Not collected                       |
| `package-install-report.md`      | Cold install and release-package verification on target Ubuntu    | Not collected                       |
| `checksums.txt`                  | Digests for the evidence artifacts, without sensitive payloads    | Not collected                       |

The repository fixture contract can be recorded in `fixture-manifest.md`, but it must retain the
`contract-only` qualification. A passing fixture test does not approve a profile or establish that a real
camera, encoder, decoder, filesystem, or object store behaves correctly.

## Release Gate

R6 is open until every required real-world record is present, independently reviewable, and tied to the
shipped build. In particular:

1. An empty approved-profile manifest means normalization remains blocked by policy.
2. Synthetic removable-media fixtures cannot satisfy hardware, codec, quality, playback, legal, or
   production-storage gates.
3. A MinIO or in-memory contract run cannot satisfy the production object-store gate.
4. A successful local upload cannot satisfy completion-bound remote verification without its durable
   receipt evidence.
5. Missing or redacted evidence must be reported as missing or redacted, never inferred as a pass.

Until those gates are closed, the accurate completion statement is that the repository contains a
fail-closed implementation and an evidence-collection scaffold, not a release-qualified Ubuntu pipeline.
