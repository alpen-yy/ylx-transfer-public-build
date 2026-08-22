# Ubuntu TF Removable-Media Fixture Manifest

Status: `CONTRACT_ONLY`

This record covers the repository-authored synthetic removable-media fixture set. It does not establish
real camera output, codec quality, playback compatibility, removable-device behavior, throughput, or
production object-store suitability.

## Collection Metadata

| Field             | Value                                                                  |
| ----------------- | ---------------------------------------------------------------------- |
| Evidence ID       | `ubuntu-tf-fixture-contract-2026-08-11`                                |
| Collection date   | `2026-08-11`                                                           |
| Code revision     | `9e0a181740118f7b3a92be9d6030ac0b2e3e5cb3` plus reviewed local changes |
| Environment class | Local synthetic filesystem contract                                    |
| Tool identity     | `rustc 1.93.1`; `cargo 1.93.1`                                         |
| Reviewer/sign-off | `UNREVIEWED`                                                           |
| Overall result    | `PASS_CONTRACT_ONLY`                                                   |

## Controlled Inputs

| Artifact                                            | SHA-256                                                            |
| --------------------------------------------------- | ------------------------------------------------------------------ |
| `fixtures/removable-media/fixture-contract-v1.json` | `681dbf6bf7dbfc1676ffa5d66d123863c98b283ee6723136f89515b810e6638f` |
| `fixtures/removable-media/index.json`               | `e023fdcbc67f2d0e2d421bf15fdedc71dde6bfc8814f307516311f3b11224601` |
| `fixtures/removable-media/provenance.json`          | `d7bb9847eaf468d2ff54cf93e68d965f719c09df96600ae17884613d6ad89377` |
| `fixtures/removable-media/payloads.json`            | `12898fc8f1b0271b1bd2a2af5d76d16bc9356e09a2445881bef0bfa173002f70` |
| Complete fixture set                                | `49b4d6e017be3919f5ef875a3adf46fed3b6895e0185bed1c7a0b2325781a219` |

The complete-set digest is SHA-256 over the sorted list of each repository-relative fixture path and its
SHA-256. The set contains no private signing key, credentials, raw production recording, device serial,
username, or absolute source location.

## Inventory

The index contains 30 cases across seven grouped case files:

| Input contract               | Covered outcomes                                                                        |
| ---------------------------- | --------------------------------------------------------------------------------------- |
| Signed publication v1        | trusted fixture anchor, unpaired key, same-size hash mismatch, unsafe/non-regular paths |
| Unsigned publication v1      | H.264 separate eyes, MJPEG side-by-side, half-present signature pair, unknown codec     |
| Complete unpublished v6      | locally validated unsigned admission                                                    |
| Committed appliance spool v6 | valid short tail, missing/duplicate/unclosed segment, conflicting/interrupted state     |
| Raw capture v2               | 30/60 fps, failed capture, invalid JPEG range, sequence and IMU timestamp errors        |
| Legacy MJPEG session v5      | valid PTS-reset short tail, sequence gap, unsupported v1-v4 major                       |
| Recovery/rejection inputs    | interrupted capture, malformed JSON, unknown schema major                               |

Every indexed case has exactly one provenance record and exactly one expected verdict. Payload byte
lengths and digests are recomputed by the test. All virtual paths pass the production portable-path parser;
non-regular entries are simulated without following an external target.

## Automated Contract

Command:

```text
cargo test --manifest-path src-tauri/Cargo.toml -p ylx-transfer-adapters --test removable_media_fixture_contract -- --nocapture
```

Result: `PASS`; two integration tests validated metadata closure and materialized all 30 cases.

The signed success case uses the production Ed25519 verifier, the externally injected test-only anchor,
`PublicationTrust`, and `SourceRecording::admit_device_signed`. The unpaired case remains
`waiting_for_pairing_key`. Half-present detached pairs and mixed inline/detached profiles fail closed.
Same-size content substitution is detected by digest rather than size.

## Limitations

- Fixture video bytes are boundary stubs and are not decodable quality samples.
- The test-only public key is not a production trust root; its private key is not retained.
- Temporary directories do not model UDisks2, physical removal, filesystem latency, or real card failure.
- Injected probe metadata does not prove FFmpeg decode, VMAF, SSIM, stereo/CV, or player compatibility.
- This record cannot approve a normalization profile or change the release decision.
