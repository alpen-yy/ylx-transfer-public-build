# Ubuntu TF MinIO Contract Report

Status: `PASS_MINIO_CONTRACT_ONLY`

This record covers a hermetic S3-compatible contract run against a short-lived local MinIO container. It
does not qualify a production object-store target and contains no endpoint credential, bucket secret,
signed URL, object payload, or machine-specific path.

## Collection Metadata

| Field             | Value                                                                  |
| ----------------- | ---------------------------------------------------------------------- |
| Evidence ID       | `ubuntu-tf-minio-contract-2026-08-11`                                  |
| Collection date   | `2026-08-11`                                                           |
| Code revision     | `9e0a181740118f7b3a92be9d6030ac0b2e3e5cb3` plus reviewed local changes |
| Environment class | Local Docker; ephemeral MinIO; path-style S3                           |
| Tool identity     | Docker `29.2.1`; Rust `1.93.1`                                         |
| Service identity  | `minio/minio:RELEASE.2025-09-07T16-13-09Z` pinned by image digest      |
| Reviewer/sign-off | `UNREVIEWED`                                                           |
| Overall result    | `PASS_MINIO_CONTRACT_ONLY`                                             |

## Invocation

```text
bash src-tauri/crates/ylx-transfer-adapters/tests/support/run_minio_object_store_contract.sh
```

The script generated an isolated bucket, prefix, and temporary credentials for the run. MinIO data used a
container-owned 2 GiB tmpfs and was destroyed with the container. The Rust harness removed its bucket and
confirmed that no tracked key or pending multipart upload remained.

## Executed Scenarios

| Scenario                                     | Result |
| -------------------------------------------- | ------ |
| Multipart upload and completion-bound verify | PASS   |
| Resume after interrupted part checkpoints    | PASS   |
| Abort and no-object cleanup                  | PASS   |
| Completion binding after same-key overwrite  | PASS   |
| Metadata mismatch fails closed               | PASS   |
| Content digest mismatch fails closed         | PASS   |
| 429/5xx classification and retry             | PASS   |
| Network loss classification and retry        | PASS   |
| Final bucket, object, and multipart cleanup  | PASS   |

## Result Boundary

- The run proves the pinned adapter contract against this MinIO service class using path-style requests.
- It exercises completion-bound readback, fault-proxy behavior, durable multipart semantics, and cleanup.
- It does not prove virtual-host behavior for a production DNS/TLS deployment.
- It does not prove an approved production-compatible object store, production credentials, retention,
  archival, deletion policy, regional behavior, or service-level guarantees.
- `production-storage-smoke.md` remains not collected, so the production object-store release gate remains
  open.
