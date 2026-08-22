# Ubuntu TF Qualification Evidence: `<release-id>`

Status: `NOT_COLLECTED`

This record is a template. Replace placeholders only with bounded, reviewable values. Do not paste raw
secrets, signed material, or machine-specific paths into this document.

## Required Declarations

- Repository removable-media fixtures are synthetic contract fixtures only. They are not codec, quality,
  playback, hardware, or production object-store evidence.
- `src-tauri/resources/media-profiles/approved_profiles.json` is empty unless a separate reviewed profile
  approval record proves otherwise. No normalization profile is approved by this template.
- Real Ubuntu hardware, object-store, legal/package, codec/quality, and playback evidence is absent until
  the corresponding records below are completed with dated observations.
- This record contains no credentials, private keys, SAS strings, signed publication bytes, or absolute
  filesystem paths.

## Collection Metadata

| Field                  | Value                     |
| ---------------------- | ------------------------- |
| Evidence ID            | `<opaque-evidence-id>`    |
| Release ID             | `<release-id>`            |
| Collection date (UTC)  | `<YYYY-MM-DDTHH:MM:SSZ>`  |
| Code revision          | `<commit-id>`             |
| Target                 | `Ubuntu 24.04 LTS x86_64` |
| Build/package identity | `<bounded-build-id>`      |
| Reviewer               | `<reviewer-id>`           |
| Overall result         | `NOT_COLLECTED`           |

Use `PASS`, `FAIL`, `BLOCKED`, or `NOT_COLLECTED` for section results. Do not use `PASS` when the
observation was inferred from a synthetic fixture or an unavailable external system.

## Artifact Inventory

List only repository-relative artifacts or opaque references. Do not include absolute paths or raw media.

| Artifact          | Reference                                 | SHA-256        | Sensitivity review |
| ----------------- | ----------------------------------------- | -------------- | ------------------ |
| `<artifact-name>` | `<repo-relative-artifact-or-evidence-id>` | `<sha256:...>` | `reviewed`         |

## Fixture Contract Record

Result: `CONTRACT_ONLY`

- Fixture manifest: `<repo-relative-fixture-manifest>`
- Expected candidate/provenance verdicts: `<summary>`
- Automated contract command: `<command-and-result>`
- Explicit limitation: these fixtures do not establish real camera output, decoder behavior, quality
  thresholds, filesystem timing, object-store behavior, or release suitability.

## Discovery and Import HITL

Result: `NOT_COLLECTED`

| Scenario                                  | Observation     | Expected result | Evidence reference |
| ----------------------------------------- | --------------- | --------------- | ------------------ |
| Removable device qualification            | `<observation>` | `<expected>`    | `<evidence-id>`    |
| UDisks2 attach and re-enumeration         | `<observation>` | `<expected>`    | `<evidence-id>`    |
| Ubuntu Core nested recordings             | `<observation>` | `<expected>`    | `<evidence-id>`    |
| Access-denied diagnosis                   | `<observation>` | `<expected>`    | `<evidence-id>`    |
| Remove/reinsert or same-mount replacement | `<observation>` | `<expected>`    | `<evidence-id>`    |
| Library-root destination guard            | `<observation>` | `<expected>`    | `<evidence-id>`    |

Do not record a mount path, username, device serial, or unredacted system diagnostic. Record only the
bounded category, result, and evidence ID.

## Fault Injection and Recovery

Result: `NOT_COLLECTED`

| Boundary                              | Restart/recovery result | No-false-success check | Evidence reference |
| ------------------------------------- | ----------------------- | ---------------------- | ------------------ |
| Import checkpoint                     | `<result>`              | `<result>`             | `<evidence-id>`    |
| Import completion outbox              | `<result>`              | `<result>`             | `<evidence-id>`    |
| Derivation staging/commit             | `<result>`              | `<result>`             | `<evidence-id>`    |
| Upload job/checkpoint CAS             | `<result>`              | `<result>`             | `<evidence-id>`    |
| Upload completion verification/outbox | `<result>`              | `<result>`             | `<evidence-id>`    |
| Pause/cancel/shutdown                 | `<result>`              | `<result>`             | `<evidence-id>`    |

## Codec and Quality Corpus

Result: `NOT_COLLECTED`

- Corpus evidence ID: `<evidence-id>`
- Corpus digest: `<sha256:...>`
- Input schema/codec coverage: `<bounded-summary>`
- VMAF/SSIM/stereo/CV results: `<bounded-summary>`
- Full-decode and frame/timestamp alignment results: `<bounded-summary>`
- Output inventory and derived-receipt binding: `<bounded-summary>`
- Approved profile decision: `NOT_APPROVED`

The corpus itself must remain under its controlled handling policy. Do not embed raw video, signed
publication material, or absolute source locations here.

## Throughput and Resource Bounds

Result: `NOT_COLLECTED`

| Workload        | Encode/upload result | CPU/memory/temp-disk result | Evidence reference |
| --------------- | -------------------- | --------------------------- | ------------------ |
| `<workload-id>` | `<measurement>`      | `<measurement>`             | `<evidence-id>`    |

## Stereo/CV Domain Review

Result: `NOT_COLLECTED`

- Evaluator/build identity: `<bounded-id>`
- Domain acceptance method: `<bounded-summary>`
- Report digest: `<sha256:...>`
- Reviewer decision: `NOT_APPROVED`

## Encoder, Legal, and Package Review

Result: `NOT_COLLECTED`

- FFmpeg/libx265 build identity: `<bounded-id>`
- Distribution/license review reference: `<evidence-id>`
- Profile/build compatibility result: `<bounded-summary>`
- Package contents digest: `<sha256:...>`
- Approval: `NOT_APPROVED`

## Playback Compatibility

Result: `NOT_COLLECTED`

| Target decoder/player class | Result                | Evidence reference |
| --------------------------- | --------------------- | ------------------ |
| `<target-class>`            | `<PASS/FAIL/BLOCKED>` | `<evidence-id>`    |

## Object-Store Qualification

### MinIO Contract

Result: `NOT_COLLECTED`

- S3-compatible endpoint class: `<bounded-class>`
- Path-style/virtual-host behavior: `<bounded-summary>`
- Multipart restart and completion-bound verification: `<bounded-summary>`
- Report digest: `<sha256:...>`

### Production-Compatible Smoke

Result: `NOT_COLLECTED`

- Approved target class: `<bounded-class>`
- Storage profile identity: `<credential-free-identity>`
- Checksum/readback receipt result: `<bounded-summary>`
- Report digest: `<sha256:...>`

Never record endpoint credentials, SAS strings, bucket secrets, signed bytes, or private keys. A
credential-free storage profile identity is not a credential and does not prove that a smoke test ran.

## Cold Install and Release Package

Result: `NOT_COLLECTED`

- Package artifact reference: `<repo-relative-artifact-or-evidence-id>`
- Package digest: `<sha256:...>`
- Cold-install result: `<bounded-summary>`
- Startup/capability result: `<bounded-summary>`
- Real-card end-to-end result: `<evidence-id>`

## Checksums

Use `checksums.txt` for digests of the evidence files only. Do not include raw media, signed publication
bytes, credentials, private keys, SAS strings, or absolute paths.

```text
<sha256>  <repo-relative-evidence-file>
```

## Sign-Off

| Gate                                     | Result          | Reviewer        | Evidence reference |
| ---------------------------------------- | --------------- | --------------- | ------------------ |
| Contract fixtures remain contract-only   | `PASS`          | `<reviewer-id>` | `<evidence-id>`    |
| Approved profile evidence complete       | `NOT_APPROVED`  | `<reviewer-id>` | `<evidence-id>`    |
| Ubuntu hardware/import evidence complete | `NOT_COLLECTED` | `<reviewer-id>` | `<evidence-id>`    |
| Codec/quality/stereo evidence complete   | `NOT_COLLECTED` | `<reviewer-id>` | `<evidence-id>`    |
| Object-store evidence complete           | `NOT_COLLECTED` | `<reviewer-id>` | `<evidence-id>`    |
| Legal/package/playback evidence complete | `NOT_COLLECTED` | `<reviewer-id>` | `<evidence-id>`    |
| Release-complete decision                | `NOT_APPROVED`  | `<reviewer-id>` | `<evidence-id>`    |
