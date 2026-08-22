# Ubuntu TF Local Automation Evidence: 2026-08-06

Status: LOCAL_AUTOMATION_ONLY

This record captures local repository checks run while stabilizing the Ubuntu TF-card media pipeline.
It is not Import-ready, Code-complete, or Release-complete evidence. It contains no credentials,
private keys, signed publication material, raw media, absolute filesystem paths, or machine-specific
mount information.

## Collection Metadata

| Field                 | Value                                     |
| --------------------- | ----------------------------------------- |
| Evidence ID           | ubuntu-tf-local-automation-2026-08-06     |
| Collection date (UTC) | 2026-08-06T07:28:56Z                      |
| Code revision         | 9e0a181740118f7b3a92be9d6030ac0b2e3e5cb3  |
| Working tree          | dirty; includes uncommitted pipeline work |
| Target                | local developer automation                |
| Overall result        | PASS_LOCAL_GATES                          |

## Local Gate Results

| Gate                     | Command                                                                                                                            | Result           |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- | ---------------- |
| Rust format              | cargo fmt --check                                                                                                                  | PASS             |
| Prettier format          | npm run format:check                                                                                                               | PASS             |
| Whitespace/conflict scan | git diff --check                                                                                                                   | PASS             |
| TypeScript tests         | npm test                                                                                                                           | PASS; 295 passed |
| TypeScript typecheck     | npm run typecheck                                                                                                                  | PASS             |
| Rust workspace check     | cargo check --workspace                                                                                                            | PASS             |
| Rust startup regression  | cargo test -p ylx-transfer application::tests::startup_seed_propagates_transfer_projection_failure -- --nocapture --test-threads=1 | PASS             |
| Rust lib serial test     | cargo test -p ylx-transfer --lib -- --test-threads=1                                                                               | PASS; 215 passed |
| Rust workspace test      | cargo test --workspace                                                                                                             | PASS             |

## Stabilization Note

- The previous ylx-transfer lib test hang was isolated to startup bootstrapping that reached the real
  OS credential backend during default tests.
- Test builds now select the deterministic in-memory credential vault while production builds continue to
  use the OS keyring backend.
- The bootstrap log text now refers to the generic credential vault so test output does not imply OS
  keyring access.

## Explicit Limitations

- Repository removable-media fixtures remain synthetic contract fixtures only.
- The approved normalization profile manifest remains empty, so no profile is release-approved by this
  evidence.
- Real Ubuntu TF-card HITL, UDisks2 authorization behavior, card removal/reinsert behavior, codec/quality
  corpus measurements, stereo/CV review, MinIO evidence, production-compatible object-store smoke,
  legal/package review, playback compatibility, and cold-install evidence are not collected here.
- This record cannot close any checklist item that requires real hardware, real media, real object-store,
  codec corpus, legal review, package identity, or playback evidence.
