# Device Session v1 vendored contract

This tree vendors the Device Session v1 transfer gate inputs from
`mirrorbloom/pi-dev` commit `9ba7ab95fac9fe64b54a602e4ec1068a60852dd3`.

The upstream commit is provenance only. It may not be resolvable from this
repository's Git object graph, so the local authority is
`contract-identity.json`: every vendored file used by the transfer gate is
pinned by SHA-256 and checked by Rust tests.

Runtime behavior:

- `ylx-transfer-core` embeds
  `central/schemas/ylx-device-session-v1.schema.json` and validates manifests
  with the Rust `jsonschema` crate in Draft 2020-12 mode.
- `ylx-transfer-core` ports the central `validate_session_invariants`
  semantics into Rust for admission.
- `central/scripts/validate.py` is retained as hash-pinned reference material
  only. It is not executed by transfer runtime code or test gates.
- The valid and invalid central fixture corpus is scanned by Rust integration
  tests so missing or drifted fixtures fail the gate.
