# Dependency request: `rusqlite` (bundled) for `ylx-transfer-core`

Filed by: W0-06 (PC core/persistence spike)
Status: proposed, not yet adopted into the root app — this crate dependency currently lives only
in `src-tauri/crates/ylx-transfer-core/Cargo.toml`, a _separate_ Cargo workspace from
`src-tauri/Cargo.toml`/`Cargo.lock` (see `docs/adr/ADR-PC-001-persistence.md` for why). No change
was made to the root manifest or lockfile. This document exists so the integration owner has the
full picture before PC-00/PC-01 fold `ylx-transfer-core` into the real app's build.

## Package / version

- `rusqlite = { version = "0.32", features = ["bundled"] }`
- Resolved in this spike to `rusqlite 0.32.1` (crates.io reports a newer `0.40.1` line exists as of
  writing; `0.32` was chosen deliberately — see "Why not the newest version" below).
- Transitive dependencies pulled in by the `bundled` feature: `libsqlite3-sys 0.30.1` (vendors the
  SQLite C amalgamation source and compiles it via `cc`), plus `hashlink`, `smallvec`,
  `fallible-iterator`, `fallible-streaming-iterator`, `bitflags` (all small, widely-used, permissively
  licensed crates already common in the Rust ecosystem).

## License

- `rusqlite`: MIT.
- `libsqlite3-sys`: MIT.
- The vendored SQLite C source itself (bundled feature): public domain (SQLite's own
  long-standing license).
- All other transitive deps pulled in (`hashlink`, `smallvec`, `fallible-iterator`,
  `fallible-streaming-iterator`, `bitflags`, `cc`, `pkg-config`, `vcpkg`): MIT/Apache-2.0 dual or
  MIT.
- No copyleft (GPL/LGPL/AGPL) dependency is introduced.

## Offline / cross-platform build impact

- **Linux/macOS**: the `bundled` feature compiles SQLite's C source at build time via the `cc`
  crate. Requires a C compiler (`cc`/`gcc`/`clang`) present on the build machine — **build-time
  only**, no runtime dependency, no system `libsqlite3` needed. Verified in this environment:
  `cc`/`gcc` already present (`/usr/bin/cc`, `/usr/bin/gcc`).
- **Windows**: `libsqlite3-sys`'s `bundled` feature is designed to work with the MSVC toolchain
  (already required for any Tauri Windows build) via the `cc` crate's MSVC support
  (`find-msvc-tools`); this was not independently verified on a Windows host in this environment
  (none available) — flagged as a residual risk, not a fabricated pass.
- **Offline builds**: once `cargo vendor`/`cargo fetch` has pulled the crates once (standard
  practice for this repo's existing dependencies too), no network access is needed at build time —
  `bundled` compiles from vendored C source, it does not fetch/link against a system SQLite at
  build or run time. This is _more_ offline-friendly than the alternative of requiring a
  system-installed `libsqlite3` (which would need to be present on every build/CI machine and every
  end-user machine if dynamically linked).

## Why `rusqlite` (bundled) and not the alternatives considered

| Option                                          | Rejected because                                                                                                                                                                                                                                                                                                                                                                                                       |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sqlx` (SQLite feature)                         | Async-first API (built around `tokio`/`async-std` executors) adds concurrency-model complexity this spike/PC-01 doesn't need — Tauri commands can use a blocking connection behind `tauri::async_runtime::spawn_blocking` just as easily as a bespoke async pool. Also still needs `libsqlite3-sys` under the hood for the SQLite driver, so the C-toolchain dependency doesn't go away, only the API surface changes. |
| System-linked `rusqlite` (no `bundled` feature) | Requires `libsqlite3` to be installed on every build machine and, if dynamically linked, on every end-user machine — worse offline/cross-platform story than vendoring, for an app meant to ship to end users' Windows/macOS/Linux desktops.                                                                                                                                                                           |
| Continue with (even a fixed) JSON store         | Rejected on its own merits in `ADR-PC-001-persistence.md` — this isn't really a "dependency" alternative since it adds no new crate, but it's the actual competing option the ADR evaluates.                                                                                                                                                                                                                           |

## Why 0.32 and not the latest line

`cargo search rusqlite` in this environment reports `0.40.1` as available. `0.32` was chosen for
this spike because it's a well-established, widely-deployed release line at the time this task was
executed, and pinning to whatever "latest" resolves to at ADR-write time is less important than the
integration owner making a deliberate version choice when this actually gets adopted repo-wide —
this request flags that choice explicitly rather than silently locking in whatever `cargo add`
happened to pick. The integration owner should re-evaluate the exact version (0.32 vs. a newer 0.3x/
0.4x line) at PC-00/PC-01 time against whatever `rusqlite` release is current then.

## What happens if this is not approved

If the integration owner rejects `rusqlite`/SQLite for the production app, `ADR-PC-001-persistence.md`'s
recommendation would need to be revisited — the JSON-store alternative built in this spike
(`persistence::json_store::JsonAtomicStore`) is a strictly-better-than-current fallback (atomic
rename, typed errors, no silent swallowing) that could be adopted instead, with the trade-offs
listed in the ADR (single-blob blast radius, no migration story, unverified Windows directory-fsync
durability) accepted as residual risk.

---

# Dependency request: `rusty-s3`, `ureq`, `tiny_http` (dev-only) for `ylx-transfer-adapters`

Filed by: SPIKE-PC-S3 (early, explicitly-authorized PC S3/ObjectStore spike — see the module-level
doc comment at the top of `src-tauri/crates/ylx-transfer-adapters/src/object_store_s3.rs` for the
authorization/scope rationale; this is **not** the real PC-06 task and this request will need to be
re-reviewed, and possibly superseded, when PC-06 actually runs).

Status: proposed, not yet adopted into the root app. All three crates currently live only in
`src-tauri/crates/ylx-transfer-adapters/Cargo.toml`, inside the same separate Cargo workspace
`src-tauri/crates/Cargo.toml` that W0-06 established specifically so nothing under `crates/` can
reach — and therefore cannot modify — the root `src-tauri/Cargo.toml`/`Cargo.lock`. No change was
made to the root manifest or lockfile by this spike either; verified with
`git diff --stat -- src-tauri/Cargo.toml src-tauri/Cargo.lock` showing no output.

## Packages / versions

- `rusty-s3 = "0.10"` — resolved to `0.10.1` in this spike's `src-tauri/crates/Cargo.lock`.
- `ureq = "3.3"` — resolved to `3.3.0`.
- `url = "2"` — resolved to `2.5.8` (already a transitive dependency of `rusty-s3`; declared
  directly because `object_store_s3.rs`'s own public `S3ObjectStoreConfig::endpoint` field is typed
  `url::Url`).
- `tiny_http = "0.12"` — resolved to `0.12.0`, **`[dev-dependencies]` only**. Used exclusively by
  `object_store_s3.rs`'s `#[cfg(test)]` module as a self-hosted fake HTTP server (see "What the
  tests do and do not prove" below) — never compiled into a non-test build, never present in the
  crate's runtime dependency graph.

`cargo tree -p ylx-transfer-adapters` (run from `src-tauri/crates/`) full transitive list: the three
new direct deps above, plus (all standard, widely-used, permissively-licensed crates already common
in the Rust ecosystem, no new copyleft or unusual dependency) — cryptography (`sha2`, `hmac`,
`digest`, `md-5`, `ring`, `rustls`, `webpki-roots`, `rustls-webpki`, `rustls-pki-types`, `subtle`,
`zeroize`, `cmov`, `ctutils`, `const-oid`), XML (`instant-xml`, `xmlparser`), HTTP plumbing
(`http`, `httparse`, `ureq-proto`, `bytes`, `base64`, `flate2`/`miniz_oxide`/`crc32fast`,
`percent-encoding`, `utf8-zero`), URL/IDNA (`url`, `idna`, `idna_adapter`, the `icu_*`/`zerovec`/
`yoke`/`zerofrom` family pulled in for Unicode IDNA normalization), and the usual `serde`/
`serde_json`/`thiserror`/`jiff`(time) crates. Dev-only additions from `tiny_http`: `ascii`,
`chunked_transfer`, `httpdate`. No dependency in this list requires a system package, a specific
OS, or network access to _build_ (network is only needed once, the same as any other Cargo
dependency, to fetch source from crates.io — verified this spike ran with real network access in
its sandbox; see "Offline / cross-platform build impact" below for what changes with no network).

## License

- `rusty-s3`: **BSD-2-Clause**.
- `ureq`: **MIT OR Apache-2.0**.
- `tiny_http` (dev-only): **MIT OR Apache-2.0**.
- `url`: MIT OR Apache-2.0 (already accepted transitively via `rusty-s3`, no new license family).
- Every transitive dependency inspected in the tree above is MIT, Apache-2.0, MIT/Apache-2.0 dual,
  BSD-2/3-Clause, ISC, Unicode-3.0 (the `icu_*`/Unicode-data crates), or Zlib — no GPL/LGPL/AGPL
  anywhere in the graph. `ring` (rustls's default crypto backend, pulled in transitively via
  `ureq`'s default `rustls` feature) uses a mixed ISC/OpenSSL/MIT-style license typical of that
  crate's history; it carries no copyleft obligation.

## Why `rusty-s3` (Sans-IO signing) instead of `aws-sdk-s3`

| Option                          | Rejected because                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `aws-sdk-s3` (official AWS SDK) | Async-only (built around `tokio`), pulls in a much larger dependency tree (its own `hyper`/`tokio`/`aws-config`/credential-provider-chain machinery), and is tuned for AWS-specific auth flows (IAM roles, STS, EC2 metadata) that this "S3-compatible endpoint" requirement (plan 9.3: MinIO/generic S3-compatible, not AWS-only) doesn't need. It would also commit this early spike to an async runtime choice before PC-00 has decided how Tauri commands in this app want to structure async work. |
| `rusty-s3` (chosen)             | Sans-IO: it only builds and SigV4-signs URLs/headers/XML bodies, does zero network I/O itself — the adapter stays free to use any HTTP client (blocking here; could be swapped to async later without rewriting the signing logic). Explicitly supports path-style URLs against arbitrary endpoints (MinIO's primary mode), not just AWS's virtual-hosted-style. Small, focused, `forbid(unsafe_code)` outside its own test code.                                                                       |
| Hand-rolled SigV4 signing       | Rejected as needless risk for a spike — SigV4 has enough subtlety (canonical request construction, credential scope, signed-headers ordering) that reimplementing it from scratch invites a signature bug that would only surface against a real S3/MinIO endpoint, which this sandbox doesn't have available to catch it.                                                                                                                                                                              |

## Why `ureq` instead of `reqwest`

`ureq` is a small, blocking-only HTTP client (rustls-backed by default, no OpenSSL system
dependency, no async runtime). `reqwest` would also work and is arguably the more likely long-term
choice once PC-03's `pi_http.rs` (a separate, later task) picks an HTTP client for the Pi-facing
adapter — **this spike does not attempt to pre-decide that choice for PC-03/PC-06.** `ureq` was
picked specifically because:

1. It keeps this spike's footprint minimal and independent of any async-runtime decision — nothing
   here forces PC-00 into "the app must use tokio" before that's actually been decided.
2. Its `http_status_as_error(false)` config option makes "give me the response body/headers even on
   4xx/5xx so I can parse S3's structured error XML" a one-line opt-out, which is exactly this
   adapter's error-mapping strategy (`map_error_response` in `object_store_s3.rs`).

**Explicit flag for the real PC-06 task**: when PC-06 actually runs (after PC-00/Wave 2 gating), it
should re-evaluate `ureq` vs. `reqwest` against whatever HTTP client PC-03's `pi_http.rs` has
already committed to by then — using two different HTTP clients across `ylx-transfer-adapters`
would be a real (if minor) maintenance cost worth avoiding if avoidable. This spike does not resolve
that cross-task question; it only proves the S3 signing/upload/verify logic works with _a_ blocking
client.

## What the tests do and do not prove (honesty note, see also the module doc comment)

No MinIO or other real S3-compatible server was available in the sandbox this spike was built in.
`object_store_s3.rs`'s tests spin up a real `tiny_http` server on loopback and send real signed HTTP
requests to it over a real socket — this proves the adapter's request construction (method, path,
query-string signature parameters, the `x-amz-meta-source-sha256` header actually being present on
the wire, multipart XML body shape) and response parsing (success XML, `<Error>` XML, HEAD response
headers) are internally correct and self-consistent. **It does not prove a real S3-compatible
server's SigV4 verifier would accept these exact signed requests** — `tiny_http` performs no
signature verification, it only records what it received and returns a scripted response. Real
MinIO integration testing (the plan's actual PC-06 merge gate: "MinIO 绿色") is explicitly out of
this spike's scope and is flagged as the primary remaining gap for the real PC-06 task.

## Offline / cross-platform build impact

- All four crates and their transitive dependencies are pure Rust (`ring`'s crypto primitives use
  some architecture-specific assembly/intrinsics internally, same as it does for every other Rustls
  user in this ecosystem, but this requires no separate system package or C toolchain step beyond
  what a normal `cargo build` already does — unlike `rusqlite`'s `bundled` SQLite, nothing here
  invokes `cc`/a C compiler).
- **Offline builds**: once fetched once (`cargo fetch`/`cargo vendor`, same standard practice as
  this repo's existing dependencies), no network access is needed at build time.
- **Windows**: `ureq`'s default `rustls` backend (not the OS's native TLS stack, not OpenSSL) avoids
  the usual Windows cross-compilation pain of linking a system TLS library; not independently
  verified on a Windows host in this environment (none available) — flagged as a residual risk, not
  a fabricated pass, consistent with how ADR-PC-001 flagged the same gap for `rusqlite`.

## What happens if this is not approved

If the integration owner (or the real PC-06 task, when it runs) rejects this specific
`rusty-s3`/`ureq` pairing, the `ObjectStorePort` trait in
`ylx-transfer-core/src/library/object_store_port.rs` and its `MemoryObjectStore` mock are unaffected
— they have zero dependency on either crate (verified: `cargo tree -p ylx-transfer-core` contains
neither). Only `ylx-transfer-adapters/src/object_store_s3.rs`'s production implementation would need
to be rewritten against a different HTTP client / signing library (e.g. `aws-sdk-s3`, or a hand-rolled
signer); the port trait and its test coverage would not need to change.

# Dependency request: `keyring` (+ transitive Secret Service/zbus/native-store stack) for `ylx-transfer-adapters`

Filed by: SPIKE-PC-CRED (explicitly authorized, out-of-sequence pre-PC-00/PC-07 credential-vault
spike — see plan section 9.3's last paragraph and section 10.1's `ADR-CRED-001` row for the
authorization; this is **not** the real PC-07 task, and this dependency choice is provisional
pending PC-00/PC-07 review).

Status: proposed, not yet adopted into the root app — this crate dependency currently lives only
in `src-tauri/crates/ylx-transfer-adapters/Cargo.toml`, the same _separate_ Cargo workspace
described in the `rusqlite` request above. No change was made to the root manifest or lockfile
(verified: `git status --porcelain` in the worktree shows no root `src-tauri/Cargo.toml`/
`Cargo.lock` changes, only `src-tauri/crates/Cargo.lock` and files under `src-tauri/crates/`).

## Package / version / features

- `keyring = { version = "4.1.6", features = ["apple-native-keyring-store"] }`
- Resolved in this spike to `keyring 4.1.6` (the latest published line at time of writing;
  `cargo add keyring --dry-run` offered no newer alternative).
- Feature selection: the crate's **default** features already include `v1` (the simple sync
  `Entry::new/set_password/get_password/delete_credential` API this adapter uses),
  `windows-native-keyring-store` (Windows Credential Manager), and
  `zbus-secret-service-keyring-store` (a pure-Rust, `zbus`-based Linux/*nix Secret Service client —
  chosen by the crate's own defaults over the alternative `dbus-secret-service-keyring-store`
  feature, which links the system `libdbus` C library via FFI; the zbus-based default avoids that
  system-library dependency). This request adds **one** feature on top of the defaults:
  `apple-native-keyring-store` (macOS Keychain Services), so all three of Windows/macOS/Linux have
  a native backend enabled — without it, a macOS build of this crate would compile but have no
  working credential store (`Error::NoDefaultStore` on every call).
- Not enabled: `android-native-keyring-store`, `db-keystore`, `linux-keyutils-keyring-store`, `cli`
  — none of Android, an encrypted-file-fallback keystore, the Linux kernel keyutils backend, or the
  cross-platform CLI/demo glue are needed for this spike or for PC's target platforms
  (Windows/macOS/Linux desktop, per plan section 16).
- **Architectural note for PC-00/PC-07**: as of this `keyring` major version, the crate's own docs
  (`src/lib.rs`) now recommend that applications wanting fine-grained control over which credential
  store is used on which platform link directly against `keyring-core` plus specific store crates,
  rather than this `keyring` "v1 compat" facade — the facade is described upstream as convenient but
  bringing in more transitive dependency surface than a hand-picked selection would. This spike used
  the `v1` facade for speed or a working spike; PC-07 should weigh switching to a direct
  `keyring-core` + per-platform-store composition against the transitive-dependency cost documented
  below.

## Transitive dependency footprint (this is the honest part)

`cargo tree -p ylx-transfer-adapters` shows **122** total crates in the dependency graph after
adding `keyring` (up from 2 — just `ylx-transfer-core` and its own deps — before). The bulk of this
comes from the Linux Secret Service path pulling in `zbus` (a full async D-Bus client), which in
turn pulls `async-executor`/`async-io`/`futures-*`/`blocking` (its own bundled async runtime, not
`tokio` — it does not require the app to already run a `tokio` runtime, but it does spin up its own
thread-pool-backed executor internally) plus a small crypto stack (`aes`, `cbc`, `hkdf`, `hmac`,
`sha2`, `subtle`) that `secret-service` (a dependency of `zbus-secret-service-keyring-store`) uses
to implement the Secret Service's session-encryption transport option. This is a materially larger
dependency footprint than `rusqlite`'s (7 crates including transitives). It is inherent to "talk to
a real D-Bus Secret Service from Rust without linking `libdbus`," not to a poor feature choice in
this request — `keyring`'s own default features already make this trade (pure-Rust zbus over
FFI-linked `dbus-rs`), and this request does not change that default.

## License

Machine-checked via `cargo metadata` across the full `ylx-transfer-adapters` dependency graph
(122 packages): **92** `MIT OR Apache-2.0`, **22** `Apache-2.0 OR MIT`, **18** `MIT`, **4**
`MIT/Apache-2.0`, **3** `Unlicense OR MIT`, **3** `Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR
MIT`, **2** `BSD-2-Clause OR Apache-2.0 OR MIT`, **1** `BSD-3-Clause` (`subtle`), **1**
`(MIT OR Apache-2.0) AND Unicode-3.0`, and **1** `MIT OR Apache-2.0 OR LGPL-2.1-or-later` (`r-efi`,
an indirect dependency of the `wasm32` target's `getrandom` — the `LGPL` arm is one of three
license _options_, so `MIT` can be selected instead; not a copyleft obligation). **No package in
the graph is copyleft-only** (no bare `GPL`/`LGPL`/`AGPL` with no permissive alternative). All 122
third-party packages report a non-empty `license` field (verified programmatically — zero packages
with missing license metadata other than this workspace's own two crates, which correctly have
none since they're `publish = false`).

## Offline / cross-platform build impact

- **Linux**: builds and links against the D-Bus session bus at _runtime_ only (via `zbus`, pure
  Rust — no `libdbus` C library needed at build or run time). At build time, no network access is
  needed beyond the initial `cargo fetch`. **Runtime dependency**: a working D-Bus session bus and
  a Secret-Service-implementing daemon (`gnome-keyring-daemon`, KWallet's Secret Service shim, or
  equivalent) must be present and its default collection unlocked for calls to succeed — see
  "What was actually exercised in this sandbox" below for what happens when that's not true.
- **Windows**: `windows-native-keyring-store` (already a default feature) uses the Windows
  Credential Manager via the `windows`-crate FFI bindings — not independently verified on a Windows
  host in this environment (none available), consistent with this repo's existing residual-risk
  disclosure pattern for the `rusqlite` request above.
- **macOS**: `apple-native-keyring-store` (the one feature this request adds beyond defaults) uses
  Keychain Services via `security-framework`/`security-framework-sys` FFI bindings — likewise not
  independently verified on a macOS host in this environment (none available).
- **Offline builds**: once `cargo fetch` has pulled all 122 crates once, no network access is needed
  at build time for any platform — none of the backends fetch anything remotely at build time (the
  D-Bus/Keychain/Credential-Manager calls are all _runtime_ IPC/FFI, not build-time network calls).

## What was actually exercised in this sandbox (honest disclosure)

This sandbox **does** have a D-Bus session bus (`DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`)
and a running `gnome-keyring-daemon --start --foreground --components=secrets` for the user this
spike ran as — so a real Secret Service is genuinely present, unlike a minimal headless CI
container that might have neither. However:

- `secretstorage` (Python) reported the default collection as `is_locked() == True` before this
  spike touched anything.
- Running the `#[ignore]`d `real_backend_probe_reports_status_or_locked_honestly` test
  (`cargo test -p ylx-transfer-adapters real_backend_probe -- --ignored --nocapture`) against the
  real `OsKeyringCredentialVault` produced:
  ```
  real backend probe: backend present but locked: credential store not accessible (locked?): SS error: prompt dismissed
  ```
  i.e. the `zbus-secret-service-keyring-store` backend correctly attempted to unlock the collection
  via the Secret Service's `Prompt` interface, and — because there is no interactive human/agent
  present in this sandbox to approve that prompt — the prompt was dismissed and the call surfaced as
  exactly the kind of "locked" failure `CredentialVaultError::Locked` is designed for. **This is a
  real, live exercise of the locked-backend error path against the actual OS keyring stack**, not a
  fake standing in for it.
- A full real-backend set/get/delete round trip was **not** achieved in this sandbox, because the
  collection could not be unlocked non-interactively. The round-trip guarantee is instead proven
  against `InMemoryCredentialVault` (the fake) in the default `cargo test` run — see this task's
  final report for the full test list.
- **What this means for CI/other environments**: a headless CI runner with no D-Bus session at all
  would instead see `Error::NoDefaultStore` or a `PlatformFailure` at `Entry::new(...)` time (mapped
  to `CredentialVaultError::Unavailable`), not `Locked` — both are handled by the same "structured
  error, no plaintext fallback" contract, so this doesn't change the adapter's behavior, only which
  specific error variant is observed.

## What happens if this is not approved

If the integration owner rejects `keyring`/the Secret-Service-via-`zbus` dependency chain (e.g. over
the 122-crate transitive footprint), the two real alternatives to evaluate at PC-07 time are: (a)
link directly against `keyring-core` plus a hand-picked, narrower set of platform store crates
(likely the same practical footprint on Linux, since Secret Service access requires _some_ D-Bus
client either way, but avoids the `v1` facade's `NoStorageAccess`/error-shape choices this spike
inherited), or (b) implement platform stores directly (raw `zbus`/`windows`/`security-framework`
calls) for maximum control at the cost of maintaining three platform-specific code paths instead of
one. This spike's `CredentialVaultPort` trait (in `ylx-transfer-core`) is deliberately independent
of which of these is chosen — swapping the adapter's internals would not require changing the port
or any of its tests beyond the production-adapter-specific ones.

# Dependency request: `sha2` (runtime) and `tiny_http` (dev-only) for `ylx-transfer-core`

Filed by: SPIKE-PC-DOWNLOAD (explicitly authorized, out-of-sequence pre-PC-00/PC-04 download-engine
spike — see the module-level doc comment at the top of
`src-tauri/crates/ylx-transfer-core/src/library/download.rs` for the authorization/scope rationale;
this is **not** the real PC-04 task and this dependency choice is provisional pending PC-00/PC-04
review).

Status: proposed, not yet adopted into the root app. Both crates currently live only in
`src-tauri/crates/ylx-transfer-core/Cargo.toml`, inside the same separate Cargo workspace
(`src-tauri/crates/Cargo.toml`) described in the `rusqlite` request above. No change was made to the
root manifest or lockfile (verified: `git diff --stat -- src-tauri/Cargo.toml src-tauri/Cargo.lock`
shows no output).

## Packages / versions

- `sha2 = "0.10"` (runtime `[dependencies]`) — resolved to `0.10.9` in this spike's
  `src-tauri/crates/Cargo.lock`. Already present _transitively_ elsewhere in this same Cargo
  workspace (pulled in by `rusty-s3`/`secret-service` for `ylx-transfer-adapters`), so this request
  adds no genuinely new crate to the workspace's overall dependency set — only promotes an
  already-vetted crate to a direct dependency of `ylx-transfer-core`.
- `tiny_http = "0.12"` (`[dev-dependencies]` only) — resolved to `0.12.0`, identical to the
  dev-dependency SPIKE-PC-S3 already added to `ylx-transfer-adapters` (see that request above for
  the full write-up). Used only by `library/download.rs`'s `#[cfg(test)]` module and by
  `tests/download_http_spike.rs`, never compiled into a non-test build.

## Why `sha2` is a runtime dependency here, unlike `object_store_port.rs`'s fake ETag

`library/object_store_port.rs` (SPIKE-PC-S3) deliberately avoids pulling in a real hash crate for
its mock S3 ETag, because that value is explicitly documented as _not_ a content hash and never
compared against anything security-relevant in that module. `library/download.rs` is different:
plan section 9.2 step 5 and section 6.1 invariant 12 require PC to verify a file's real SHA-256
before ever renaming it into the committed `LocalLibrary` location — this is a genuine integrity
check a real attacker/corruption scenario could exploit if faked, so a `DefaultHasher`-style
stand-in would defeat the entire point of the module. `sha2` is the same well-known, widely-audited,
pure-Rust (`RustCrypto`) hash crate already present in this workspace's transitive graph via
`rusty-s3`, so promoting it to a direct dependency of `ylx-transfer-core` adds no new crate to
`cargo tree` at the workspace level — only a new _direct_ edge from `ylx-transfer-core` to a crate
that was already being built.

## License

- `sha2`: `MIT OR Apache-2.0`.
- Its runtime transitive additions to `ylx-transfer-core`'s own dependency graph (previously zero
  for anything hash-related): `cfg-if`, `cpufeatures`, `digest`, `block-buffer`, `crypto-common`,
  `generic-array` (`MIT` only), `typenum` — all `MIT OR Apache-2.0` except `generic-array`'s bare
  `MIT`, still fully permissive. Verified via `cargo metadata` (see this task's final report for the
  exact per-package listing). No copyleft.
- `tiny_http` (dev-only): `MIT OR Apache-2.0`, identical to the already-accepted SPIKE-PC-S3 entry.

## Offline / cross-platform build impact

- `sha2` is pure Rust with no C-toolchain build step (unlike `rusqlite`'s `bundled` feature) —
  `cpufeatures` only does runtime CPU-feature _detection_ on x86/ARM, it does not require a C
  compiler or any system library, on any of PC's target platforms (Windows/macOS/Linux desktop).
- Both crates were already resolved in this same Cargo workspace's `Cargo.lock` (via
  `ylx-transfer-adapters`'s existing SPIKE-PC-S3 dependencies) before this request, so this change
  fetches nothing new from crates.io — verified: `cargo build --workspace` after this change pulled
  no additional network-fetched packages beyond what SPIKE-PC-S3 had already resolved.
- **Offline builds**: same standard practice as every other dependency in this workspace — once
  fetched once, no network access needed at build time.

## What happens if this is not approved

If the integration owner rejects `sha2` specifically (unlikely, given it is already transitively
present and is one of the most widely-used pure-Rust hash crates in the ecosystem), the concrete
integrity check in `download_file`'s size/hash verification step would need a different SHA-256
implementation — the `DownloadSource`/`FilePlan`/`VerifiedFile` types and the rest of the state
machine (path safety, journal, Range handling, atomic commit, crash recovery, `PublicationVerifier`
seam) are independent of which hash crate computes `sha256_of_file`, so swapping it would be a
localized change. Rejecting `tiny_http` here would only remove
`tests/download_http_spike.rs`'s real-socket coverage; the pure in-memory `FakeDownloadSource`-based
tests in `library/download.rs` itself do not depend on it and would be unaffected.

# Dependency request: `rustls`, `rustls-webpki` (as `webpki`), `serde`/`serde_json`, `sha2`,

# `base64`, `mdns-sd` for `ylx-transfer-adapters`

Filed by: PC-03 (the real task — Pi HTTPS client + mDNS discovery adapters, plan section 16). All
crates below currently live only in `src-tauri/crates/ylx-transfer-adapters/Cargo.toml`, the same
separate Cargo workspace described in every request above. No change was made to the root
`src-tauri/Cargo.toml`/`Cargo.lock` (verified: `git diff --stat -- src-tauri/Cargo.toml
src-tauri/Cargo.lock` shows no output).

## Packages / versions

- `rustls = "0.23"` — resolved to `0.23.43`. Already a transitive dependency of `ureq`'s default
  `rustls` feature (SPIKE-PC-S3 added `ureq` already) at this exact version — promoted to a direct
  dependency, not a new crate in the graph.
- `webpki = { package = "rustls-webpki", version = "0.103" }` — resolved to `0.103.13`. Same
  situation: already transitively present (via `rustls`) at this exact version.
- `serde = { version = "1", features = ["derive"] }`, `serde_json = "1"` — resolved to `1.0.229` /
  `1.0.151`. Already direct dependencies of `ylx-transfer-core` and already transitively present in
  this crate's own graph (via `rusty-s3`, `keyring`) — promoted to direct dependencies here because
  `pi_http.rs` builds/parses its own JSON request and response bodies, and a crate can only use a
  dependency it declares directly, not one only present transitively through another crate.
- `sha2 = "0.10"` — resolved to `0.10.9`. Same promoted-not-new situation as the `sha2` request
  above (already a direct dependency of `ylx-transfer-core`, already transitively present here via
  `rusty-s3`/`keyring`).
- `base64 = "0.22"` — resolved to `0.22.1`. Already transitively present (via `rustls`/`ureq`'s own
  graph) at this exact version. Used only by `pi_http.rs::tls_pin_from_pem_certificate`, a small
  convenience for turning a PEM certificate file into a `PiTlsPin` (used by
  `tests/pi_http_integration.rs` to read the real Pi daemon's freshly-generated certificate off disk
  and pin it — see "What the tests do and do not prove" below).
- `mdns-sd = "0.13"` — resolved to `0.13.11`. **Genuinely new** to this workspace's dependency graph
  (unlike everything else in this request). Pulls in `flume` (an MPMC channel crate, its own event
  channel), `if-addrs` (network interface enumeration), and (via `if-addrs`/its own async socket use)
  `polling`/`async-io`-family crates already present in this graph via `keyring`'s `zbus` dependency
  — no new async runtime family introduced.

## Why a bespoke TLS connector instead of `ureq`'s built-in `TlsConfig`

`pi_http.rs`'s module doc comment has the full rationale; summarized here: the Pi daemon's
certificate is self-signed per device (ADR-SEC-002) with no CA to chain-validate against — trust is
meant to come from a human-verified SAS at pairing time, not a certificate authority. This calls for
"accept this exact certificate's pinned SPKI fingerprint, skip hostname/chain validation" — a policy
`ureq`'s own `tls::TlsConfig` cannot express (it only offers a fixed root-cert list or
`disable_verification` — the latter would accept **any** certificate with **no** cryptographic check
at all, which is a materially weaker and more dangerous option than what this request implements).
`pi_http.rs::PinnedTlsConnector`/`PinnedFingerprintVerifier` plug a custom `rustls::ClientConfig`
(with a custom `rustls::client::danger::ServerCertVerifier`) directly into `ureq` via
`Agent::with_parts` — a real, documented (if explicitly "not yet semver-stable")
`ureq::unversioned` extension point, not a fork or vendored copy of `ureq`'s own internal
`RustlsConnector`/`RustlsTransport` (the new code mirrors their shape closely, at maybe 150 lines,
specifically because that shape is already the right one — only the certificate verifier itself
differs). The handshake signature is still fully cryptographically verified
(`rustls::crypto::verify_tls12_signature`/`verify_tls13_signature`, the same routines `ureq`'s own
verifier delegates to) — only the certificate-identity check is replaced, not signature verification.

## Why `mdns-sd` instead of the alternatives considered

| Option                                                                                                                   | Rejected because                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `zeroconf` (the Rust crate, name collision with the unrelated Python-side `zeroconf`/`ZeroconfMdnsRegistrar` PI-06 uses) | Wraps the C `avahi`/`mDNSResponder` libraries via FFI on Linux/macOS — a system-library dependency this workspace's other crates (`rusqlite`'s bundled SQLite, `keyring`'s pure-Rust `zbus` Secret Service path) have consistently avoided.                                                                                                                                                                                                                                                                 |
| Hand-rolled mDNS/DNS-SD packet parsing                                                                                   | Rejected as needless risk for what the task card calls out as explicitly low-priority scope ("don't over-invest here, PI-06's Pi-side advertiser already proved the protocol works, this side just needs to compile and have basic fake-response tests") — a correct DNS-SD implementation (compression pointers, TTL/cache-flush semantics, multi-packet responses) has enough subtlety that reimplementing it for a browse-only, discovery-is-never-a-trust-anchor use case is not a good time trade-off. |
| `mdns-sd` (chosen)                                                                                                       | Pure Rust (no system mDNS daemon dependency), actively maintained, implements the browse/resolve side this module needs (`ServiceDaemon::browse` → `ServiceEvent::ServiceResolved`) directly, and is used by other real projects for exactly this "produce unauthenticated LAN service candidates" use case.                                                                                                                                                                                                |

## License

Machine-checked (`grep license` on each crate's own `Cargo.toml`, all sourced from the same
`~/.cargo/registry` cache this workspace already resolved against):

- `rustls`: `Apache-2.0 OR ISC OR MIT`.
- `rustls-webpki` (`webpki`): `ISC`.
- `serde`/`serde_json`: `MIT OR Apache-2.0` (already accepted transitively — see the `sha2` request
  above for the precedent of promoting an already-transitive crate).
- `sha2`: `MIT OR Apache-2.0` (already accepted, see above).
- `base64`: `MIT OR Apache-2.0`.
- `mdns-sd`: `Apache-2.0 OR MIT`. Its own new-to-this-graph transitive additions: `flume`
  (`Apache-2.0/MIT`), `if-addrs` (`MIT OR BSD-3-Clause`), plus reuse of the `polling`/`async-io`
  family already present via `keyring`'s `zbus` dependency (`Apache-2.0 OR MIT` throughout that
  family, per the `keyring` request above). No copyleft anywhere in this request's additions.

## Offline / cross-platform build impact

- All six crates (and `mdns-sd`'s own new transitive additions) are pure Rust — no C-toolchain build
  step, no system mDNS daemon (`avahi`/`mDNSResponder`) required to _build_; `mdns-sd` only touches
  real sockets at _runtime_ when `ServiceDaemon::new()`/`browse()` are actually called.
- **Offline builds**: once fetched once (`cargo fetch`), no network access needed at build time —
  standard for every dependency in this workspace.
- **Windows/macOS multicast**: not independently verified on those hosts in this environment (none
  available), consistent with this repo's existing residual-risk disclosure pattern. On Linux (this
  sandbox), `mdns-sd`'s `ServiceDaemon::new()` and `browse()` do start successfully (see
  `discovery_mdns.rs`'s `#[ignore]`d `real_daemon_starts_and_can_be_stopped` test) — what was _not_
  verified is a real advertiser being found and resolved, since no `_ylx-capture._tcp.local.`
  advertiser (PI-06's Pi-side registrar) was running alongside this task; see `discovery_mdns.rs`'s
  module doc comment for the full honest disclosure.

## What the tests do and do not prove (honesty note)

`pi_http.rs`'s own unit tests (`#[cfg(test)]`, plain-`http://` `tiny_http` fake server, same pattern
as `object_store_s3.rs`) exercise every status-code/header/error-mapping code path _except_ the real
TLS handshake itself (a non-`https://` URL never engages `PinnedTlsConnector`). The real TLS
fingerprint-pinning path — and a real cross-language, cross-process proof of the whole client — is
instead covered by `tests/pi_http_integration.rs`, which spawns the **real** Python
`ylx_capture.transfer_daemon_cli` (RP-YLX's actual production entry point) as a real OS subprocess
and speaks real pinned HTTPS to it: real `POST /pairing-requests` → real `202`, real unauthenticated
call → real `401` `problem+json`. A second test in that file goes further and proves the _full_
pairing-approval → authenticated `GET /device`/`GET /sessions` round trip, using a small test-only
harness script (`tests/support/pi_daemon_harness.py`, owned by this repo, RP-YLX untouched) built
around RP-YLX's own unmodified `composition.build_transfer_daemon` — see that test file's module doc
comment for exactly why a harness was needed instead of the CLI directly (the CLI's own admin-side
pairing approval has no externally-reachable surface, HTTP or otherwise) and exactly what is/isn't
different from the real CLI. Both integration tests passed for real in this sandbox — see this
task's final report for the verbatim `cargo test` output.

## What happens if this is not approved

`ylx_transfer_core`'s domain types (`DeviceId`/`SessionId`/`FileId`/`ConnectionState`/
`TransferJobState`/`PublicationManifest`, all PC-00's) have zero dependency on any crate in this
request — verified: `cargo tree -p ylx-transfer-core` contains none of them. Rejecting `mdns-sd`
would leave `discovery_mdns.rs` back at its W0-06 stub (candidate discovery would need a different
approach, e.g. manual IP entry only, until re-evaluated). Rejecting the bespoke `rustls`/`webpki`
TLS-pinning connector would force a materially weaker fallback
(`ureq::tls::TlsConfig::disable_verification(true)`, i.e. no certificate check at all) for
`pi_http.rs` until a different pinning mechanism is approved — the task card's own instructions
flagged `disable_verification` as the outcome to avoid, not the preferred fallback.
