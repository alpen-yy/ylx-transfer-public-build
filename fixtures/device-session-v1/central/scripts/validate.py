#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "cryptography>=43,<47",
#   "jsonschema[format]>=4.23,<5",
#   "PyYAML>=6,<7",
#   "referencing>=0.35,<1",
# ]
# ///
"""Validate YLX contract schemas, fixtures, and selected cross-field invariants."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from collections import Counter
from collections.abc import Iterable
from copy import deepcopy
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from imu_physical_acceptance import (
    ImuPhysicalAcceptanceError,
    validate_imu_physical_acceptance,
)

CONTRACTS = Path(__file__).resolve().parents[1]
SCHEMAS = CONTRACTS / "schemas"
FIXTURES = CONTRACTS / "fixtures"
OPENAPI_V2 = CONTRACTS / "openapi" / "ylx-device-v2.openapi.yaml"
OPENAPI_V3 = CONTRACTS / "openapi" / "ylx-device-v3.openapi.yaml"
OPENAPI_V4 = CONTRACTS / "openapi" / "ylx-device-v4.openapi.yaml"
OPENAPI = OPENAPI_V3
CONTRACT_IDENTITIES = CONTRACTS / "contract-identities.yaml"
V4_API_FIXTURES = FIXTURES / "api" / "v4"
RECORD_CORPUS = FIXTURES / "corpora" / "record-corpus-v1.json"
TAKE_AGGREGATION_CORPUS = FIXTURES / "corpora" / "take-aggregation-v1.json"
ARTIFACT_RESPONSE_CORPUS = (
    FIXTURES / "corpora" / "artifact-response-conformance-v1.json"
)
PUBLICATION_SIGNATURE_FIXTURES = FIXTURES / "publication-signature-v1"
FORMAT_CHECKER = FormatChecker()
RESERVED_ARTIFACT_NAMES = {"manifest.json", "recording.json"}
TEMPORARY_ARTIFACT_SEGMENT = re.compile(r"[^/]*[.]tmp(?:[._-][^/]*)?")
SESSION_LIST_LIMIT_MAXIMUM = 200
AUDIO_DURATION_EPSILON_SECONDS = 1e-9
MAX_WAV_HEADER_BYTES = 65_536
MAX_AUDIO_BYTE_COUNT = (1 << 63) - 1
RECORD_CORPUS_ROOT_FIELDS = {"schema_version", "cases"}
RECORD_CORPUS_CASE_FIELDS = {
    "name",
    "session",
    "frames",
    "imu",
    "negative_mutations",
}
RECORD_CORPUS_MUTATION_FIELDS = {
    "id",
    "operation",
    "target",
    "index",
    "expected_error",
}
EXTERNAL_AUTHORITY_DISCRIMINATOR = "ylx.external-authority-boundaries.v1"
EXTERNAL_AUTHORITY_FIXTURE = (
    FIXTURES / "valid" / "ylx-external-authority-boundaries-v1.json"
)
SAFE_SWAP_PARTICIPANT_AUTHORITY_DISCRIMINATOR = (
    "ylx.safe-swap-participant-authority.v1"
)
SAFE_SWAP_PARTICIPANT_AUTHORITY_FIXTURE = (
    FIXTURES / "valid" / "ylx-safe-swap-participant-authority-v1.synthetic.json"
)
SAFE_SWAP_REQUIRED_ACCESS_PATHS = {
    "capture-seal",
    "gateway-validation",
    "artifact-get-head-range",
    "preview",
    "lan-transfer",
    "media-adapter",
    "worker",
}
ACTIVE_RECORDING_STATES = {"recording", "finalizing", "encoding", "verifying"}
UNSUCCESSFUL_RECORDING_STATES = {"recoverable", "failed", "abandoned"}
OPENAPI_OPERATION_METHODS = {
    "delete",
    "get",
    "head",
    "options",
    "patch",
    "post",
    "put",
    "trace",
}
V4_SCHEMA_DELTA_ALLOWLIST = {
    "CameraFocusSetRequest",
    "CameraFocusStatus",
    "CameraFocusUnsupportedError",
    "CaptureEvent",
    "CaptureSnapshotEventData",
    "CaptureStatusSnapshot",
    "DeviceDescriptor",
    "DeviceRuntimeStatus",
    "InvalidCameraFocusError",
    "LiveImuObservation",
    "RawInt16Vector3",
}
V4_FORBIDDEN_LIVE_IMU_FIELDS = {
    "acceleration_m_s2",
    "angular_velocity_rad_s",
    "epoch_id",
    "orientation_quaternion",
}


class ContractError(Exception):
    pass


def validate_imu_physical_acceptance_invariants(
    evidence: dict[str, Any],
    fixture: Path,
) -> None:
    try:
        validate_imu_physical_acceptance(evidence, location=str(fixture))
    except ImuPhysicalAcceptanceError as error:
        raise ContractError(str(error)) from error


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def format_errors(errors: Iterable[Any]) -> str:
    parts = []
    for error in errors:
        instance_path = ".".join(str(part) for part in error.absolute_path)
        schema_path = ".".join(str(part) for part in error.absolute_schema_path)
        parts.append(
            f"path={instance_path or '$'}; keyword={error.validator}; "
            f"schema_path={schema_path}; message={error.message}"
        )
        parts.extend(format_errors(error.context).splitlines())
    return "\n".join(part for part in parts if part)


def require_keywords(text: str, keywords: list[str], fixture: Path) -> None:
    folded = text.casefold()
    missing = [keyword for keyword in keywords if keyword.casefold() not in folded]
    if missing:
        raise ContractError(f"{fixture}: expected error keywords missing: {missing}\n{text}")


def require_mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{location}: expected an object")
    return value


def canonical_line_set_sha256(values: Iterable[str]) -> str:
    payload = ("\n".join(sorted(values)) + "\n").encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(
                f"STRICT_JSON_DUPLICATE_KEY: duplicate JSON object key {key!r}"
            )
        result[key] = value
    return result


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(
        f"STRICT_JSON_NONFINITE_NUMBER: non-finite JSON value {value!r}"
    )


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        _reject_nonfinite_json(value)
    return parsed


def _reject_json_surrogates(candidate: Any) -> None:
    if isinstance(candidate, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in candidate):
            raise ValueError(
                "STRICT_JSON_ISOLATED_SURROGATE: isolated Unicode surrogate"
            )
    elif isinstance(candidate, list):
        for item in candidate:
            _reject_json_surrogates(item)
    elif isinstance(candidate, dict):
        for key, item in candidate.items():
            _reject_json_surrogates(key)
            _reject_json_surrogates(item)


def load_json_bytes(raw: bytes, path: Path | str) -> Any:
    """Parse one exact UTF-8 JSON byte sequence without Python extensions."""

    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ContractError(
            f"{path}: invalid strict JSON [STRICT_JSON_UTF8_INVALID]: {error}"
        ) from error

    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=_reject_nonfinite_json,
            parse_float=_parse_finite_json_float,
        )
        _reject_json_surrogates(value)
    except json.JSONDecodeError as error:
        raise ContractError(
            f"{path}: invalid strict JSON [STRICT_JSON_SYNTAX_INVALID]: {error}"
        ) from error
    except ValueError as error:
        raise ContractError(f"{path}: invalid strict JSON: {error}") from error
    return value


def load_json(path: Path) -> Any:
    return load_json_bytes(path.read_bytes(), path)


def parse_strict_json(raw_json: bytes) -> Any:
    """Parse wire bytes without JSON's permissive duplicate/non-finite shortcuts."""

    return load_json_bytes(raw_json, "wire bytes")


def canonical_publication_signature_payload(manifest: dict[str, Any]) -> bytes:
    """RP-YLX canonicalize_manifest: omit only the top-level signature field."""

    if "publication_signature" not in manifest:
        raise ContractError("missing top-level publication_signature")
    try:
        return json.dumps(
            {key: value for key, value in manifest.items() if key != "publication_signature"},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ContractError(f"manifest is not RP-canonicalizable: {error}") from error


def verify_publication_signature_raw(
    raw_json: bytes,
    *,
    external_device_identity: str,
    registry: Any,
    manifest_validator: Draft202012Validator,
    envelope_validator: Draft202012Validator,
) -> None:
    """Fail-closed DRAFT verifier; device identity is an authenticated external input.

    RP's publication manifest has no device identity field.  The caller must bind
    the connection/pairing identity to ``external_device_identity`` before calling
    this function; no media field is used as a substitute for that binding.
    """

    manifest = require_mapping(parse_strict_json(raw_json), "publication raw JSON")
    schema_errors = list(manifest_validator.iter_errors(manifest))
    if schema_errors:
        raise ContractError(f"actual RP manifest schema rejection\n{format_errors(schema_errors)}")
    envelope = require_mapping(manifest.get("publication_signature"), "publication_signature")
    envelope_errors = list(envelope_validator.iter_errors(envelope))
    if envelope_errors:
        raise ContractError(f"RP publication-signature wire rejection\n{format_errors(envelope_errors)}")
    key_version = envelope.get("key_version")
    if not isinstance(key_version, int) or isinstance(key_version, bool):
        raise ContractError("publication_signature.key_version must be a non-boolean integer")
    if not isinstance(registry, dict):
        raise ContractError("trusted-key registry unavailable; refusing downgrade")
    bindings = registry.get("bindings")
    if not isinstance(bindings, dict):
        raise ContractError("trusted-key registry has no bindings")
    binding = bindings.get(external_device_identity)
    if not isinstance(binding, dict):
        raise ContractError("unknown external device identity or binding mismatch")
    key = binding.get(str(key_version))
    if not isinstance(key, dict):
        raise ContractError("unknown trusted key version")
    if key.get("status") != "active":
        raise ContractError("trusted key is unavailable or revoked")
    fingerprint = envelope.get("public_key_fingerprint")
    if fingerprint != key.get("fingerprint"):
        raise ContractError("registry fingerprint mismatch")
    public_key_hex = key.get("public_key_hex")
    signature_hex = envelope.get("signature")
    if not isinstance(public_key_hex, str) or not isinstance(signature_hex, str):
        raise ContractError("malformed trusted public key or signature")
    try:
        public_key = bytes.fromhex(public_key_hex)
        signature = bytes.fromhex(signature_hex)
    except ValueError as error:
        raise ContractError("malformed trusted public key or signature") from error
    if len(public_key) != 32 or len(signature) != 64:
        raise ContractError("malformed trusted public key or signature")
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, canonical_publication_signature_payload(manifest)
        )
    except (InvalidSignature, ValueError) as error:
        raise ContractError("Ed25519 signature verification failed") from error


def verify_publication_admission_raw(
    raw_json: bytes,
    *,
    external_device_identity: str,
    registry: Any,
    manifest_validator: Draft202012Validator,
    envelope_validator: Draft202012Validator,
) -> None:
    """Apply RP's post-signature admission invariants to a full manifest."""

    verify_publication_signature_raw(
        raw_json,
        external_device_identity=external_device_identity,
        registry=registry,
        manifest_validator=manifest_validator,
        envelope_validator=envelope_validator,
    )
    manifest = require_mapping(parse_strict_json(raw_json), "publication admission raw JSON")
    content_field_names = (
        "schema_version",
        "session_id",
        "captured_at",
        "duration_seconds",
        "total_bytes",
        "video_bytes",
        "integrity_ok",
        "files",
    )
    content_fields = {name: manifest.get(name) for name in content_field_names}
    try:
        expected_revision = "sha256:" + hashlib.sha256(
            json.dumps(
                content_fields,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError, UnicodeError) as error:
        raise ContractError("manifest content fields are not RP-canonicalizable") from error
    if manifest.get("revision") != expected_revision:
        raise ContractError("manifest revision does not match RP content_fields canonical SHA-256")
    captured_at = manifest.get("captured_at")
    published_at = manifest.get("published_at")
    if not isinstance(captured_at, str) or not isinstance(published_at, str):
        raise ContractError("manifest timestamps are not strings")
    try:
        captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError("manifest timestamps are not RFC 3339 datetimes") from error
    if published < captured:
        raise ContractError("published_at precedes captured_at")


def validate_publication_signature_candidate(
    schemas: dict[str, tuple[str, dict[str, Any]]]
) -> int:
    """Exercise one DRAFT raw-byte verifier against RP's actual wire and KAT."""

    envelope_schema_path = CONTRACTS / "publication-signature-v1.schema.json"
    manifest_schema_path = CONTRACTS / "publication-manifest-v1.schema.json"
    schema = require_mapping(load_json(envelope_schema_path), str(envelope_schema_path))
    manifest_schema = require_mapping(load_json(manifest_schema_path), str(manifest_schema_path))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator.check_schema(manifest_schema)
    manifest_path = PUBLICATION_SIGNATURE_FIXTURES / "publication_manifest.json"
    vector_path = PUBLICATION_SIGNATURE_FIXTURES / "golden-vector.json"
    registry_path = PUBLICATION_SIGNATURE_FIXTURES / "synthetic-trusted-key-registry.json"
    vector = require_mapping(load_json(vector_path), str(vector_path))
    registry = require_mapping(load_json(registry_path), str(registry_path))
    schema_digest = hashlib.sha256(manifest_schema_path.read_bytes()).hexdigest()
    if (
        vector.get("candidate_schema_sha256") != schema_digest
        or vector.get("rp_source_schema_sha256") != schema_digest
    ):
        raise ContractError(f"{vector_path}: RP schema mechanical-mirror digest mismatch")
    raw_manifest = manifest_path.read_bytes()
    manifest = require_mapping(parse_strict_json(raw_manifest), str(manifest_path))
    payload = canonical_publication_signature_payload(manifest)
    if payload.hex() != vector.get("canonical_utf8_hex") or hashlib.sha256(payload).hexdigest() != vector.get("canonical_sha256"):
        raise ContractError(f"{vector_path}: canonical payload digest mismatch")
    manifest_validator = Draft202012Validator(manifest_schema)
    envelope_validator = Draft202012Validator(schema)
    device_identity = registry.get("fixture_external_device_identity")
    if not isinstance(device_identity, str):
        raise ContractError(f"{registry_path}: missing fixture external device identity")
    cases: dict[str, tuple[bytes, str, Any, bool]] = {
        "valid": (raw_manifest, device_identity, registry, True),
        "unknown_device": (raw_manifest, "unknown-device", registry, False),
        "external_binding_mismatch": (raw_manifest, "different-known-device", registry, False),
        "unknown_key_version": (raw_manifest.replace(b'"key_version":1', b'"key_version":2'), device_identity, registry, False),
        "revoked": (raw_manifest, device_identity, {**registry, "bindings": {**registry["bindings"], device_identity: {"1": {**registry["bindings"][device_identity]["1"], "status": "revoked"}}}}, False),
        "malformed_key": (raw_manifest, device_identity, {**registry, "bindings": {**registry["bindings"], device_identity: {"1": {**registry["bindings"][device_identity]["1"], "public_key_hex": "not-hex"}}}}, False),
        "fingerprint_mismatch": (raw_manifest, device_identity, {**registry, "bindings": {**registry["bindings"], device_identity: {"1": {**registry["bindings"][device_identity]["1"], "fingerprint": "sha256:" + "0" * 64}}}}, False),
        "signature_mutation": (raw_manifest.replace(b'"signature":"65', b'"signature":"75'), device_identity, registry, False),
        "algorithm_mutation": (raw_manifest.replace(b'"algorithm":"ed25519"', b'"algorithm":"rsa"'), device_identity, registry, False),
        "boolean_key_version": (raw_manifest.replace(b'"key_version":1', b'"key_version":true'), device_identity, registry, False),
        "registry_unavailable": (raw_manifest, device_identity, None, False),
        "body_mutation": (raw_manifest.replace(b'"total_bytes":483921234', b'"total_bytes":483921235'), device_identity, registry, False),
        "duplicate_key": (raw_manifest.replace(b'"session_id":"sess-0001"', b'"session_id":"sess-0001","session_id":"other"'), device_identity, registry, False),
        "nonfinite": (raw_manifest.replace(b'"duration_seconds":121.4', b'"duration_seconds":NaN'), device_identity, registry, False),
        "invalid_surrogate": (raw_manifest.replace(b'"session_id":"sess-0001"', b'"session_id":"\\ud800"'), device_identity, registry, False),
        "unicode_canonical_kat": (raw_manifest.replace(b'"session_id":"sess-0001"', b'"session_id":"s\\u00e9ss-0001"'), device_identity, registry, False),
        # RP signs parsed values; lexical 121.40 canonicalizes to the signed 121.4.
        "numeric_canonical_kat": (raw_manifest.replace(b'"duration_seconds":121.4', b'"duration_seconds":121.40'), device_identity, registry, True),
    }
    unicode_payload = canonical_publication_signature_payload(
        require_mapping(parse_strict_json(cases["unicode_canonical_kat"][0]), "unicode KAT")
    )
    numeric_payload = canonical_publication_signature_payload(
        require_mapping(parse_strict_json(cases["numeric_canonical_kat"][0]), "numeric KAT")
    )
    if b'"session_id":"s\\u00e9ss-0001"' not in unicode_payload:
        raise ContractError("unicode canonical KAT did not use RP ensure_ascii escaping")
    if numeric_payload != payload:
        raise ContractError("numeric canonical KAT did not normalize 121.40 to RP's signed value")
    for label, (candidate_raw, candidate_device_identity, candidate_registry, should_accept) in cases.items():
        try:
            verify_publication_signature_raw(
                candidate_raw,
                external_device_identity=candidate_device_identity,
                registry=candidate_registry,
                manifest_validator=manifest_validator,
                envelope_validator=envelope_validator,
            )
        except ContractError:
            if not should_accept:
                continue
            raise ContractError(f"{label}: valid RP fixture was rejected")
        if not should_accept:
            raise ContractError(f"{label}: malformed/untrusted input unexpectedly accepted")
    return len(cases)


def validate_publication_admission_invariant() -> int:
    """Keep the frozen crypto KAT separate from a revision-valid admission vector."""

    envelope_schema = require_mapping(
        load_json(CONTRACTS / "publication-signature-v1.schema.json"),
        "publication signature schema",
    )
    manifest_schema = require_mapping(
        load_json(CONTRACTS / "publication-manifest-v1.schema.json"),
        "publication manifest schema",
    )
    manifest_validator = Draft202012Validator(manifest_schema)
    envelope_validator = Draft202012Validator(envelope_schema)
    registry = require_mapping(
        load_json(PUBLICATION_SIGNATURE_FIXTURES / "synthetic-trusted-key-registry.json"),
        "synthetic trusted-key registry",
    )
    device_identity = registry.get("fixture_external_device_identity")
    if not isinstance(device_identity, str):
        raise ContractError("synthetic trusted-key registry lacks external device identity")
    fixture_path = PUBLICATION_SIGNATURE_FIXTURES / "admission_manifest.json"
    vector_path = PUBLICATION_SIGNATURE_FIXTURES / "admission-vector.json"
    raw_manifest = fixture_path.read_bytes()
    vector = require_mapping(load_json(vector_path), str(vector_path))
    manifest = require_mapping(parse_strict_json(raw_manifest), str(fixture_path))
    content_fields = {
        name: manifest[name]
        for name in (
            "schema_version", "session_id", "captured_at", "duration_seconds",
            "total_bytes", "video_bytes", "integrity_ok", "files",
        )
    }
    revision_bytes = json.dumps(
        content_fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")
    signature_bytes = canonical_publication_signature_payload(manifest)
    if (
        revision_bytes.decode("utf-8") != vector.get("revision_content_fields_canonical_utf8")
        or hashlib.sha256(revision_bytes).hexdigest() != vector.get("revision_content_fields_sha256")
        or hashlib.sha256(signature_bytes).hexdigest() != vector.get("signature_canonical_sha256")
        or manifest.get("publication_signature", {}).get("signature") != vector.get("signature")
    ):
        raise ContractError(f"{vector_path}: admission vector KAT mismatch")

    def resigned(candidate: dict[str, Any]) -> bytes:
        seed = bytes.fromhex(vector["test_only_private_seed_hex"])
        # Test-only seed is explicit in the vector; production never signs here.
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        payload = canonical_publication_signature_payload(candidate)
        candidate["publication_signature"] = {
            **require_mapping(candidate["publication_signature"], "admission signature"),
            "signature": Ed25519PrivateKey.from_private_bytes(seed).sign(payload).hex(),
        }
        return json.dumps(candidate, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    revision_mismatch = deepcopy(manifest)
    revision_mismatch["revision"] = "sha256:" + "0" * 64
    timestamp_order = deepcopy(manifest)
    timestamp_order["published_at"] = "2026-08-01T03:59:59Z"
    cases: dict[str, tuple[bytes, bool]] = {
        "admission_valid": (raw_manifest, True),
        "revision_mismatch": (resigned(revision_mismatch), False),
        "published_before_captured": (resigned(timestamp_order), False),
    }
    for label, (candidate_raw, should_accept) in cases.items():
        try:
            verify_publication_admission_raw(
                candidate_raw,
                external_device_identity=device_identity,
                registry=registry,
                manifest_validator=manifest_validator,
                envelope_validator=envelope_validator,
            )
        except ContractError:
            if not should_accept:
                continue
            raise ContractError(f"{label}: revision-valid admission fixture was rejected")
        if not should_accept:
            raise ContractError(f"{label}: invalid admission manifest unexpectedly accepted")
    return len(cases)


def contract_identity_index() -> tuple[
    dict[str, tuple[str, dict[str, Any]]],
    dict[str, tuple[str, dict[str, Any]]],
    dict[str, Any],
]:
    identity = require_mapping(load_yaml(CONTRACT_IDENTITIES), str(CONTRACT_IDENTITIES))
    expected_root_fields = {
        "schema_version",
        "json_schema_dialect",
        "current_schemas",
        "legacy_v2_schemas",
        "openapi",
    }
    if set(identity) != expected_root_fields:
        raise ContractError(
            f"{CONTRACT_IDENTITIES}: expected exactly root fields "
            f"{sorted(expected_root_fields)}"
        )
    if identity.get("schema_version") != "ylx.contract-identities.v3":
        raise ContractError(f"{CONTRACT_IDENTITIES}: unexpected schema_version")
    dialect = identity.get("json_schema_dialect")
    if dialect != "https://json-schema.org/draft/2020-12/schema":
        raise ContractError(f"{CONTRACT_IDENTITIES}: unexpected JSON Schema dialect")

    required_identity_fields = {"basename", "discriminator", "schema_id"}
    entries_by_scope: dict[str, list[dict[str, Any]]] = {}
    basenames: list[str] = []
    discriminators: list[str] = []
    schema_ids: list[str] = []
    for scope in ("current_schemas", "legacy_v2_schemas"):
        raw_entries = identity.get(scope)
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ContractError(
                f"{CONTRACT_IDENTITIES}: {scope} must be a nonempty array"
            )
        entries: list[dict[str, Any]] = []
        for index, raw_entry in enumerate(raw_entries):
            location = f"{CONTRACT_IDENTITIES}: {scope}[{index}]"
            entry = require_mapping(raw_entry, location)
            if set(entry) != required_identity_fields:
                raise ContractError(
                    f"{location} must contain exactly {sorted(required_identity_fields)}"
                )
            basename = entry["basename"]
            discriminator = entry["discriminator"]
            schema_id = entry["schema_id"]
            if not all(
                isinstance(item, str) and item
                for item in (basename, discriminator, schema_id)
            ):
                raise ContractError(
                    f"{location} identity values must be nonempty strings"
                )
            if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", basename) is None:
                raise ContractError(f"{location} has an unsafe basename")
            entries.append(entry)
            basenames.append(basename)
            discriminators.append(discriminator)
            schema_ids.append(schema_id)
        entries_by_scope[scope] = entries
    for label, values in (
        ("basename", basenames),
        ("discriminator", discriminators),
        ("schema_id", schema_ids),
    ):
        duplicates = sorted(item for item, count in Counter(values).items() if count > 1)
        if duplicates:
            raise ContractError(f"{CONTRACT_IDENTITIES}: duplicate {label} values {duplicates}")

    expected_files = {f"{basename}.schema.json" for basename in basenames}
    actual_files = {path.name for path in SCHEMAS.glob("*.schema.json")}
    nested_files = {
        path.relative_to(SCHEMAS).as_posix() for path in SCHEMAS.rglob("*.schema.json")
    }
    if actual_files != expected_files or nested_files != expected_files:
        raise ContractError(
            f"{SCHEMAS}: schema filenames differ from contract-identities.yaml; "
            f"missing={sorted(expected_files - nested_files)}; "
            f"unknown={sorted(nested_files - expected_files)}"
        )

    schemas_by_scope: dict[str, dict[str, tuple[str, dict[str, Any]]]] = {}
    for scope, entries in entries_by_scope.items():
        schemas: dict[str, tuple[str, dict[str, Any]]] = {}
        for entry in entries:
            basename = entry["basename"]
            path = SCHEMAS / f"{basename}.schema.json"
            schema = require_mapping(load_json(path), str(path))
            Draft202012Validator.check_schema(schema)
            if schema.get("$schema") != dialect:
                raise ContractError(
                    f"{path}: $schema does not match the canonical dialect"
                )
            if schema.get("$id") != entry["schema_id"]:
                raise ContractError(
                    f"{path}: $id does not match contract-identities.yaml"
                )
            discriminator = schema.get("properties", {}).get("schema", {}).get("const")
            if discriminator != entry["discriminator"]:
                raise ContractError(
                    f"{path}: schema discriminator does not match contract-identities.yaml"
                )
            if "schema" not in schema.get("required", []):
                raise ContractError(f"{path}: discriminator field schema must be required")
            if schema.get("additionalProperties") is not False:
                raise ContractError(f"{path}: persisted schema root must be closed")
            schemas[entry["discriminator"]] = (basename, schema)
        schemas_by_scope[scope] = schemas

    current_schemas = schemas_by_scope["current_schemas"]
    legacy_v2_schemas = schemas_by_scope["legacy_v2_schemas"]
    if SAFE_SWAP_PARTICIPANT_AUTHORITY_DISCRIMINATOR in current_schemas or (
        SAFE_SWAP_PARTICIPANT_AUTHORITY_DISCRIMINATOR not in legacy_v2_schemas
    ):
        raise ContractError(
            f"{CONTRACT_IDENTITIES}: safe-swap participant authority must be indexed "
            "only as legacy_v2"
        )

    openapi_identity = require_mapping(identity.get("openapi"), f"{CONTRACT_IDENTITIES}: openapi")
    required_openapi_fields = {
        "current_major",
        "supported_major_versions",
        "unknown_major_policy",
        "versions",
    }
    if set(openapi_identity) != required_openapi_fields:
        raise ContractError(
            f"{CONTRACT_IDENTITIES}: openapi must contain exactly {sorted(required_openapi_fields)}"
        )
    if openapi_identity.get("current_major") != 4:
        raise ContractError(f"{CONTRACT_IDENTITIES}: current_major must be 4")
    if openapi_identity.get("supported_major_versions") != [2, 3, 4]:
        raise ContractError(
            f"{CONTRACT_IDENTITIES}: supported_major_versions must be exactly [2, 3, 4]"
        )
    if openapi_identity.get("unknown_major_policy") != "fail_closed":
        raise ContractError(f"{CONTRACT_IDENTITIES}: unknown_major_policy must be fail_closed")

    versions = require_mapping(
        openapi_identity.get("versions"), f"{CONTRACT_IDENTITIES}: openapi.versions"
    )
    if set(versions) != {"v2", "v3", "v4"}:
        raise ContractError(
            f"{CONTRACT_IDENTITIES}: openapi.versions must contain exactly v2, v3, and v4"
        )
    expected_version_paths = {
        "v2": OPENAPI_V2,
        "v3": OPENAPI_V3,
        "v4": OPENAPI_V4,
    }
    expected_lifecycle = {
        "v2": "frozen_compat",
        "v3": "frozen_compat",
        "v4": "current",
    }
    required_version_fields = {
        "path",
        "lifecycle",
        "openapi_version",
        "info_version",
        "server_base_path",
        "sha256",
        "bytes",
    }
    declared_openapi_files: set[str] = set()
    for key, expected_path in expected_version_paths.items():
        entry = require_mapping(
            versions.get(key), f"{CONTRACT_IDENTITIES}: openapi.versions.{key}"
        )
        if set(entry) != required_version_fields:
            raise ContractError(
                f"{CONTRACT_IDENTITIES}: openapi.versions.{key} must contain exactly "
                f"{sorted(required_version_fields)}"
            )
        if entry.get("lifecycle") != expected_lifecycle[key]:
            raise ContractError(f"{CONTRACT_IDENTITIES}: {key} lifecycle drifted")
        for field in ("path", "lifecycle", "openapi_version", "info_version", "server_base_path", "sha256"):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise ContractError(
                    f"{CONTRACT_IDENTITIES}: openapi.versions.{key}.{field} must be a nonempty string"
                )
        if not isinstance(entry.get("bytes"), int) or entry["bytes"] <= 0:
            raise ContractError(f"{CONTRACT_IDENTITIES}: openapi.versions.{key}.bytes must be positive")
        declared_path = entry["path"]
        if Path(declared_path).is_absolute() or (CONTRACTS / declared_path).resolve() != expected_path.resolve():
            raise ContractError(
                f"{CONTRACT_IDENTITIES}: openapi.versions.{key}.path must identify "
                f"{expected_path.relative_to(CONTRACTS)}"
            )
        if re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None:
            raise ContractError(f"{CONTRACT_IDENTITIES}: openapi.versions.{key}.sha256 must be lowercase SHA-256")
        raw = expected_path.read_bytes()
        actual_sha = hashlib.sha256(raw).hexdigest()
        if entry["sha256"] != actual_sha or entry["bytes"] != len(raw):
            raise ContractError(
                f"{expected_path}: exact OpenAPI identity mismatch; "
                f"actual sha256={actual_sha} bytes={len(raw)}"
            )
        declared_openapi_files.add(declared_path)
    actual_openapi_files = {
        path.relative_to(CONTRACTS).as_posix()
        for pattern in ("*.yaml", "*.yml")
        for path in (CONTRACTS / "openapi").rglob(pattern)
    }
    if actual_openapi_files != declared_openapi_files:
        raise ContractError(
            f"{CONTRACTS / 'openapi'}: files differ from contract-identities.yaml; "
            f"found={sorted(actual_openapi_files)}"
        )
    return current_schemas, legacy_v2_schemas, openapi_identity


def openapi_versions(identity: dict[str, Any]) -> dict[str, dict[str, Any]]:
    versions = require_mapping(
        identity.get("versions"), f"{CONTRACT_IDENTITIES}: openapi.versions"
    )
    return {
        key: require_mapping(value, f"{CONTRACT_IDENTITIES}: openapi.versions.{key}")
        for key, value in versions.items()
    }


def validate_versioned_openapi_contracts(identity: dict[str, Any]) -> None:
    versions = openapi_versions(identity)
    for key, path in (("v2", OPENAPI_V2), ("v3", OPENAPI_V3), ("v4", OPENAPI_V4)):
        spec = require_mapping(load_yaml(path), str(path))
        validate_openapi_identity(spec, versions[key], path)

    v2 = require_mapping(load_yaml(OPENAPI_V2), str(OPENAPI_V2))
    v3 = require_mapping(load_yaml(OPENAPI_V3), str(OPENAPI_V3))
    v4 = require_mapping(load_yaml(OPENAPI_V4), str(OPENAPI_V4))
    for path, spec in ((OPENAPI_V2, v2), (OPENAPI_V3, v3), (OPENAPI_V4, v4)):
        validate_openapi_references_resolve(spec, path)
    for key, spec in (("v2", v2), ("v3", v3)):
        text = (OPENAPI_V2 if key == "v2" else OPENAPI_V3).read_text(encoding="utf-8")
        if "raw_int16" in text:
            raise ContractError(f"Device API {key} is frozen and must not contain raw_int16")
        live_imu = require_mapping(
            spec["components"]["schemas"].get("LiveImuObservation"),
            f"Device API {key} LiveImuObservation",
        )
        required = set(live_imu.get("required", []))
        if not {"acceleration_m_s2", "angular_velocity_rad_s", "orientation_quaternion"} <= required:
            raise ContractError(f"Device API {key} must remain canonical SI-or-null live IMU")
    validate_v4_live_imu_contract(v4)
    validate_v4_openapi_delta_against_v3(v3, v4)


def validate_openapi_identity(
    spec: dict[str, Any], identity: dict[str, Any], path: Path = OPENAPI
) -> None:
    if spec.get("openapi") != identity["openapi_version"]:
        raise ContractError(f"{path}: OpenAPI version identity mismatch")
    info_version = spec.get("info", {}).get("version")
    if info_version != identity["info_version"]:
        raise ContractError(f"{path}: info.version identity mismatch")
    servers = spec.get("servers")
    if not isinstance(servers, list) or not servers:
        raise ContractError(f"{path}: servers must be a nonempty array")
    wrong_server_paths = [
        server.get("url")
        for server in servers
        if not isinstance(server, dict)
        or urlsplit(str(server.get("url", ""))).path != identity["server_base_path"]
        or urlsplit(str(server.get("url", ""))).query
        or urlsplit(str(server.get("url", ""))).fragment
    ]
    if wrong_server_paths:
        raise ContractError(
            f"{path}: every server URL must use base path {identity['server_base_path']}; "
            f"invalid={wrong_server_paths}"
        )


def validate_v4_live_imu_contract(spec: dict[str, Any]) -> None:
    live_imu = require_mapping(
        spec["components"]["schemas"].get("LiveImuObservation"),
        f"{OPENAPI_V4}: LiveImuObservation",
    )
    if live_imu.get("required") != ["session_id", "clock", "raw", "sync"]:
        raise ContractError(f"{OPENAPI_V4}: v4 live IMU must require session_id, clock, raw, sync")
    properties = require_mapping(live_imu.get("properties"), f"{OPENAPI_V4}: LiveImuObservation.properties")
    clock = require_mapping(properties.get("clock"), f"{OPENAPI_V4}: LiveImuObservation.clock")
    clock_properties = require_mapping(clock.get("properties"), f"{OPENAPI_V4}: live_imu.clock.properties")
    if clock_properties.get("time_base", {}).get("const") != "host_monotonic":
        raise ContractError(f"{OPENAPI_V4}: live IMU clock.time_base must be host_monotonic")
    if "epoch_id" in clock_properties or "epoch_id" in clock.get("required", []):
        raise ContractError(f"{OPENAPI_V4}: v4 live IMU clock must not carry v3 epoch_id")
    raw = require_mapping(properties.get("raw"), f"{OPENAPI_V4}: LiveImuObservation.raw")
    raw_properties = require_mapping(raw.get("properties"), f"{OPENAPI_V4}: live_imu.raw.properties")
    if raw_properties.get("units", {}).get("const") != "raw_int16":
        raise ContractError(f"{OPENAPI_V4}: live IMU raw.units must be raw_int16")
    if set(raw.get("required", [])) != {"units", "accelerometer", "gyroscope"}:
        raise ContractError(f"{OPENAPI_V4}: live IMU raw must require units, accelerometer, gyroscope")
    sync = require_mapping(properties.get("sync"), f"{OPENAPI_V4}: LiveImuObservation.sync")
    sync_properties = require_mapping(sync.get("properties"), f"{OPENAPI_V4}: live_imu.sync.properties")
    if set(sync_properties.get("quality", {}).get("enum", [])) != {"insufficient", "degraded", "good"}:
        raise ContractError(f"{OPENAPI_V4}: live IMU sync.quality enum drifted")


def artifact_descriptors(session: dict[str, Any]) -> list[dict[str, Any]]:
    video = session["video"]
    if video["layout"] == "split-eyes":
        video_artifacts = [
            artifact
            for segment in video["segments"]
            for artifact in segment["artifacts"].values()
        ]
    else:
        video_artifacts = [video["artifact"]]
    audio = session.get("audio")
    audio_artifacts = (
        [segment["artifact"] for segment in audio["segments"]]
        if isinstance(audio, dict) and audio.get("state") == "recorded"
        else []
    )
    return [
        *video_artifacts,
        session["imu"]["artifact"],
        session["frames"]["artifact"],
        *audio_artifacts,
        *session["logs"],
    ]


def validate_audio_invariants(session: dict[str, Any], fixture: Path) -> None:
    if session.get("schema") != "ylx.device-session.v2":
        return
    imu = session["imu"]
    if imu["units"] != "raw_int16" or imu["coordinate_frame"] != "raw_device_axes":
        raise ContractError(
            f"{fixture}: v2 raw IMU must use raw_int16 in raw_device_axes"
        )

    audio = session["audio"]
    state = audio["state"]
    if state == "not_recorded":
        if any(
            field in audio
            for field in (
                "codec",
                "container",
                "sample_format",
                "sample_rate",
                "channels",
                "sample_count",
                "sync",
                "segments",
            )
        ):
            raise ContractError(
                f"{fixture}: not_recorded audio must not carry recorded audio fields"
            )
        return
    if state != "recorded":
        raise ContractError(f"{fixture}: unsupported audio state {state!r}")

    if audio["sample_count"] <= 0:
        raise ContractError(f"{fixture}: recorded audio sample_count must be nonzero")
    sample_rate = audio["sample_rate"]
    channels = audio["channels"]
    bytes_per_pcm_frame = channels * 2
    duration_tolerance = (1.0 / sample_rate) + AUDIO_DURATION_EPSILON_SECONDS
    segments = audio["segments"]
    previous_sample_end: int | None = None
    previous_time_end: float | None = None
    sample_total = 0
    for expected_index, segment in enumerate(segments):
        if segment["index"] != expected_index:
            raise ContractError(f"{fixture}: audio segment indices are not contiguous")
        if segment["start_sample"] >= segment["end_sample"]:
            raise ContractError(f"{fixture}: audio segment sample interval is empty or reversed")
        if segment["start_time_seconds"] >= segment["end_time_seconds"]:
            raise ContractError(f"{fixture}: audio segment time interval is empty or reversed")
        if previous_sample_end is not None and segment["start_sample"] != previous_sample_end:
            raise ContractError(f"{fixture}: audio segment sample intervals are not contiguous")
        if previous_time_end is not None and abs(
            segment["start_time_seconds"] - previous_time_end
        ) > 1e-9:
            raise ContractError(f"{fixture}: audio segment time intervals are not contiguous")
        segment_frames = segment["end_sample"] - segment["start_sample"]
        segment_duration = float(segment["end_time_seconds"]) - float(segment["start_time_seconds"])
        expected_segment_duration = segment_frames / sample_rate
        if abs(segment_duration - expected_segment_duration) > duration_tolerance:
            raise ContractError(
                f"{fixture}: audio segment duration must match sample frame domain "
                "and sample_rate"
            )
        if segment_frames > MAX_AUDIO_BYTE_COUNT // bytes_per_pcm_frame:
            raise ContractError(
                f"{fixture}: audio pcm_payload_bytes calculation exceeds checked bound"
            )
        expected_payload_bytes = segment_frames * bytes_per_pcm_frame
        if segment["pcm_payload_bytes"] != expected_payload_bytes:
            raise ContractError(
                f"{fixture}: audio pcm_payload_bytes must equal frames * channels * 2"
            )
        if not 44 <= segment["wav_header_bytes"] <= MAX_WAV_HEADER_BYTES:
            raise ContractError(f"{fixture}: audio wav_header_bytes is outside the allowed range")
        artifact = segment["artifact"]
        if artifact["role"] != "audio.wav":
            raise ContractError(f"{fixture}: audio artifact role mismatch")
        if artifact["media_type"] != "audio/wav":
            raise ContractError(f"{fixture}: audio artifact media_type mismatch")
        expected_file_bytes = segment["pcm_payload_bytes"] + segment["wav_header_bytes"]
        if expected_file_bytes > MAX_AUDIO_BYTE_COUNT:
            raise ContractError(f"{fixture}: audio artifact byte count exceeds checked bound")
        if artifact["bytes"] != expected_file_bytes:
            raise ContractError(
                f"{fixture}: audio artifact bytes must equal pcm_payload_bytes + wav_header_bytes"
            )
        sample_total += segment_frames
        previous_sample_end = segment["end_sample"]
        previous_time_end = float(segment["end_time_seconds"])

    if segments[0]["start_sample"] != 0:
        raise ContractError(f"{fixture}: audio segment sample domain must start at zero")
    if sample_total != audio["sample_count"]:
        raise ContractError(
            f"{fixture}: audio.sample_count does not equal audio segment sample sum"
        )
    sync = audio["sync"]
    if abs(sync["start_time_seconds"] - segments[0]["start_time_seconds"]) > 1e-9:
        raise ContractError(
            f"{fixture}: audio sync start_time_seconds must equal first segment start"
        )
    if abs(sync["end_time_seconds"] - segments[-1]["end_time_seconds"]) > 1e-9:
        raise ContractError(
            f"{fixture}: audio sync end_time_seconds must equal last segment end"
        )
    sync_duration = float(sync["end_time_seconds"]) - float(sync["start_time_seconds"])
    expected_sync_duration = audio["sample_count"] / sample_rate
    if abs(sync_duration - expected_sync_duration) > duration_tolerance:
        raise ContractError(
            f"{fixture}: audio sync duration must match sample_count and sample_rate"
        )
    duration = float(session["time"]["duration_seconds"])
    if not (0 <= sync["start_time_seconds"] < sync["end_time_seconds"] <= duration):
        raise ContractError(f"{fixture}: audio sync interval must be inside session duration")


def unsafe_relative_path_reason(value: str) -> str | None:
    if value.startswith("/") or "\\" in value:
        return "unsafe relative artifact path contains an absolute or backslash"
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        return "unsafe relative artifact path contains a control character"
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        return "unsafe relative artifact path contains a dot, parent, or empty segment"
    if value in RESERVED_ARTIFACT_NAMES:
        return f"artifact path conflicts with reserved root control file {value}"
    temporary_segment = next(
        (segment for segment in segments if TEMPORARY_ARTIFACT_SEGMENT.fullmatch(segment)),
        None,
    )
    if temporary_segment is not None:
        return f"artifact path uses reserved temporary segment {temporary_segment}"
    return None


def validate_session_invariants(session: dict[str, Any], fixture: Path) -> None:
    take = session["take"]
    sequence = take["sequence"]
    continuation = take["continuation_of"]
    if (sequence == 1) != (continuation is None):
        raise ContractError(
            f"{fixture}: take sequence 1 must have continuation_of null and successors must name a predecessor"
        )
    if continuation == session["session_id"]:
        raise ContractError(f"{fixture}: a session cannot continue itself")

    artifacts = artifact_descriptors(session)
    ids = [artifact["artifact_id"] for artifact in artifacts]
    paths = [artifact["path"] for artifact in artifacts]
    if len(ids) != len(set(ids)):
        raise ContractError(f"{fixture}: duplicate artifact_id")
    if len(paths) != len(set(paths)):
        raise ContractError(f"{fixture}: duplicate artifact paths")
    for artifact in artifacts:
        if artifact["artifact_id"] != artifact["sha256"]:
            raise ContractError(f"{fixture}: artifact_id != sha256")
        path_error = unsafe_relative_path_reason(artifact["path"])
        if path_error is not None:
            raise ContractError(f"{fixture}: {path_error}")
    validate_audio_invariants(session, fixture)

    camera = session["camera"]
    if camera["width"] != camera["eye_width"] * 2:
        raise ContractError(f"{fixture}: camera.width must equal two eye widths")
    nominal_fps = camera["sensor_fps"] / camera["frame_decimation"]
    quality_policy = session["integrity"].get("quality_policy")
    measured_semantics = "nominal_fps" in camera and quality_policy is not None
    if ("nominal_fps" in camera) != (quality_policy is not None):
        raise ContractError(f"{fixture}: nominal_fps and quality_policy must appear together")
    if measured_semantics and abs(camera["nominal_fps"] - nominal_fps) > 1e-9:
        raise ContractError(f"{fixture}: nominal_fps must equal sensor_fps/frame_decimation")
    if not measured_semantics and abs(camera["effective_fps"] - nominal_fps) > 1e-9:
        raise ContractError(
            f"{fixture}: legacy effective_fps must equal sensor_fps/frame_decimation"
        )

    video = session["video"]
    split_eye_frame_start: int | None = None
    split_eye_frame_end: int | None = None
    if video["layout"] == "split-eyes":
        previous_frame_end: int | None = None
        previous_time_end: float | None = None
        for expected_index, segment in enumerate(video["segments"]):
            if segment["index"] != expected_index:
                raise ContractError(f"{fixture}: segment indices are not contiguous")
            if segment["start_frame"] >= segment["end_frame"]:
                raise ContractError(f"{fixture}: empty or reversed frame interval")
            if segment["start_time_seconds"] >= segment["end_time_seconds"]:
                raise ContractError(f"{fixture}: empty or reversed time interval")
            if previous_frame_end is not None and segment["start_frame"] != previous_frame_end:
                raise ContractError(f"{fixture}: segment frame intervals are not contiguous")
            if previous_time_end is not None and abs(
                segment["start_time_seconds"] - previous_time_end
            ) > 1e-9:
                raise ContractError(f"{fixture}: segment time intervals are not contiguous")
            if segment["artifacts"]["left"]["role"] != "video.left":
                raise ContractError(f"{fixture}: left artifact role mismatch")
            if segment["artifacts"]["right"]["role"] != "video.right":
                raise ContractError(f"{fixture}: right artifact role mismatch")
            if split_eye_frame_start is None:
                split_eye_frame_start = segment["start_frame"]
            split_eye_frame_end = segment["end_frame"]
            previous_frame_end = segment["end_frame"]
            previous_time_end = float(segment["end_time_seconds"])

    drops = session["integrity"]["drop_events"]
    total_dropped = 0
    previous_end: int | None = None
    for drop in drops:
        if previous_end is not None and drop["start_frame"] <= previous_end:
            raise ContractError(f"{fixture}: drop events overlap or are adjacent")
        if drop["dropped"] != drop["end_frame"] - drop["start_frame"]:
            raise ContractError(f"{fixture}: dropped count does not match half-open interval")
        if drop["end_frame"] <= drop["start_frame"]:
            raise ContractError(f"{fixture}: drop event has an empty or reversed interval")
        total_dropped += drop["dropped"]
        previous_end = drop["end_frame"]
    if total_dropped != session["integrity"]["dropped_frames"]:
        raise ContractError(f"{fixture}: dropped_frames does not equal drop event sum")
    if video["layout"] == "split-eyes":
        assert split_eye_frame_start is not None and split_eye_frame_end is not None
        for drop in drops:
            if (
                drop["start_frame"] < split_eye_frame_start
                or drop["end_frame"] > split_eye_frame_end
            ):
                raise ContractError(f"{fixture}: drop event lies outside the segment sequence span")
        expected_frame_count = (
            split_eye_frame_end
            - split_eye_frame_start
            - session["integrity"]["dropped_frames"]
        )
        if session["frames"]["count"] != expected_frame_count:
            raise ContractError(f"{fixture}: frames.count does not equal frame domain minus dropped_frames")

    started_at = parse_api_datetime(session["time"]["started_at"])
    ended_at = parse_api_datetime(session["time"]["ended_at"])
    verified_at = parse_api_datetime(session["integrity"]["verified_at"])
    sealed_at = parse_api_datetime(session["sealed_at"])
    if not started_at <= ended_at <= verified_at <= sealed_at:
        raise ContractError(
            f"{fixture}: timestamp order must include ended_at <= verified_at <= sealed_at"
        )
    actual_duration = (ended_at - started_at).total_seconds()
    if "duration_clock" not in session["time"] and abs(
        float(session["time"]["duration_seconds"]) - actual_duration
    ) > 0.001:
        raise ContractError(f"{fixture}: duration_seconds does not match timestamps")
    if measured_semantics:
        expected_effective = (
            0.0 if session["time"]["duration_seconds"] == 0
            else session["frames"]["count"] / session["time"]["duration_seconds"]
        )
        if abs(camera["effective_fps"] - expected_effective) > 1e-9:
            raise ContractError(f"{fixture}: effective_fps must equal frames.count/duration_seconds")
        if session["integrity"]["dropped_frames"] != 0:
            raise ContractError(f"{fixture}: rdk-x5-lossless-v1 forbids dropped frames")


def validate_take_graph(
    sessions: dict[str, tuple[Path, dict[str, Any], bytes]],
    *,
    mode: str,
) -> str:
    if mode not in {"partial-view", "closed-corpus"}:
        raise ContractError(f"unsupported take aggregation mode {mode!r}")

    by_take: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    manifest_ids: dict[str, Path] = {}
    successor_by_predecessor: dict[str, Path] = {}
    is_partial = False
    for path, session, _ in sessions.values():
        manifest_id = session["manifest_id"]
        if manifest_id in manifest_ids:
            raise ContractError(
                f"{path}: duplicate manifest_id also used by {manifest_ids[manifest_id]}"
            )
        manifest_ids[manifest_id] = path
        by_take.setdefault(session["take"]["take_id"], []).append((path, session))

    for start_path, start, _ in sessions.values():
        visited: set[str] = set()
        current = start
        while current["take"]["continuation_of"] is not None:
            predecessor_id = current["take"]["continuation_of"]
            if predecessor_id in visited:
                raise ContractError(
                    f"{start_path}: take continuation graph contains a cycle at {predecessor_id}"
                )
            visited.add(predecessor_id)
            predecessor_entry = sessions.get(predecessor_id)
            if predecessor_entry is None:
                is_partial = True
                break
            current = predecessor_entry[1]

    for take_id, members in by_take.items():
        sequences = [session["take"]["sequence"] for _, session in members]
        duplicate_sequences = sorted(
            sequence for sequence, count in Counter(sequences).items() if count > 1
        )
        if duplicate_sequences:
            raise ContractError(
                f"take {take_id}: duplicate take sequence values {duplicate_sequences}"
            )
        for path, session in members:
            sequence = session["take"]["sequence"]
            predecessor_id = session["take"]["continuation_of"]
            if sequence == 1:
                continue
            predecessor_entry = sessions.get(predecessor_id)
            if predecessor_entry is None:
                if mode == "closed-corpus":
                    raise ContractError(
                        f"{path}: take predecessor {predecessor_id} is absent from the closed corpus"
                    )
                is_partial = True
                continue
            predecessor_path, predecessor, _ = predecessor_entry
            if predecessor["take"]["take_id"] != take_id:
                raise ContractError(f"{path}: predecessor belongs to a different take_id")
            if predecessor["take"]["sequence"] + 1 != sequence:
                raise ContractError(
                    f"{path}: take sequence is not predecessor.sequence + 1"
                )
            if (
                predecessor["device"]["device_id"]
                != session["device"]["device_id"]
            ):
                raise ContractError(f"{path}: take crosses canonical device_id")
            if predecessor_id in successor_by_predecessor:
                raise ContractError(
                    f"{path}: take graph branches from {predecessor_id}; first successor is "
                    f"{successor_by_predecessor[predecessor_id]}"
                )
            successor_by_predecessor[predecessor_id] = path
            predecessor_sealed = parse_api_datetime(predecessor["sealed_at"])
            successor_started = parse_api_datetime(session["time"]["started_at"])
            if predecessor_sealed > successor_started:
                raise ContractError(
                    f"{path}: predecessor {predecessor_path} was not sealed before continuation started"
                )

        if mode == "closed-corpus" and sorted(sequences) != list(
            range(1, len(members) + 1)
        ):
            raise ContractError(
                f"take {take_id}: sequence values are not contiguous from one in the closed corpus"
            )
        roots = [session for _, session in members if session["take"]["sequence"] == 1]
        if len(roots) > 1 or (mode == "closed-corpus" and len(roots) != 1):
            raise ContractError(
                f"take {take_id}: graph must have exactly one root in the closed corpus"
            )

    return "partial" if is_partial else "complete"


def validate_take_aggregation_corpus(
    schemas: dict[str, tuple[str, dict[str, Any]]]
) -> int:
    corpus = require_mapping(
        load_json(TAKE_AGGREGATION_CORPUS), str(TAKE_AGGREGATION_CORPUS)
    )
    expected_root_fields = {"schema", "fixture_scope", "cases"}
    if set(corpus) != expected_root_fields:
        raise ContractError(
            f"{TAKE_AGGREGATION_CORPUS}: expected exactly root fields "
            f"{sorted(expected_root_fields)}"
        )
    if corpus.get("schema") != "ylx.take-aggregation-corpus.v1":
        raise ContractError(f"{TAKE_AGGREGATION_CORPUS}: unexpected schema discriminator")
    if corpus.get("fixture_scope") != "synthetic-non-production":
        raise ContractError(
            f"{TAKE_AGGREGATION_CORPUS}: fixture_scope must remain synthetic-non-production"
        )
    cases = corpus.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ContractError(f"{TAKE_AGGREGATION_CORPUS}: cases must be a nonempty array")

    session_schema = schemas["ylx.device-session.v1"][1]
    case_ids: list[str] = []
    for index, raw_case in enumerate(cases):
        location = f"{TAKE_AGGREGATION_CORPUS}: cases[{index}]"
        case = require_mapping(raw_case, location)
        required_fields = {"id", "mode", "session_fixtures", "expected_status"}
        if not required_fields <= set(case) or not set(case) <= {
            *required_fields,
            "expected_error_keywords",
        }:
            raise ContractError(f"{location}: invalid metadata fields")
        case_id = case.get("id")
        mode = case.get("mode")
        expected_status = case.get("expected_status")
        if not isinstance(case_id, str) or not case_id:
            raise ContractError(f"{location}: id must be a nonempty string")
        case_ids.append(case_id)
        if mode not in {"partial-view", "closed-corpus"}:
            raise ContractError(f"{location}: unsupported aggregation mode")
        if expected_status not in {"complete", "partial", "invalid"}:
            raise ContractError(f"{location}: unsupported expected_status")
        if mode == "partial-view" and expected_status not in {"complete", "partial"}:
            raise ContractError(f"{location}: partial-view must have a valid view status")
        if mode == "closed-corpus" and expected_status not in {"complete", "invalid"}:
            raise ContractError(f"{location}: closed-corpus must be complete or invalid")

        fixture_names = case.get("session_fixtures")
        if not isinstance(fixture_names, list) or not fixture_names:
            raise ContractError(f"{location}: session_fixtures must be a nonempty array")
        sessions: dict[str, tuple[Path, dict[str, Any], bytes]] = {}
        for relative in fixture_names:
            if not isinstance(relative, str):
                raise ContractError(f"{location}: session fixture names must be strings")
            relative_path = Path(relative)
            if (
                relative_path.is_absolute()
                or relative_path.as_posix() != relative
                or ".." in relative_path.parts
                or relative_path.suffix != ".json"
            ):
                raise ContractError(f"{location}: unsafe session fixture path {relative!r}")
            fixture = FIXTURES / relative_path
            value = require_mapping(load_json(fixture), str(fixture))
            errors = list(
                Draft202012Validator(
                    session_schema, format_checker=FORMAT_CHECKER
                ).iter_errors(value)
            )
            if errors:
                raise ContractError(
                    f"{fixture}: take aggregation fixture is not manifest-local valid\n"
                    f"{format_errors(errors)}"
                )
            validate_session_invariants(value, fixture)
            session_id = value["session_id"]
            if session_id in sessions:
                raise ContractError(f"{location}: duplicate session_id {session_id}")
            sessions[session_id] = (fixture, value, fixture.read_bytes())

        keywords = case.get("expected_error_keywords")
        if expected_status == "invalid":
            if not isinstance(keywords, list) or not keywords or not all(
                isinstance(keyword, str) and keyword for keyword in keywords
            ):
                raise ContractError(
                    f"{location}: invalid case requires nonempty expected_error_keywords"
                )
            try:
                validate_take_graph(sessions, mode=mode)
            except ContractError as error:
                require_keywords(str(error), keywords, TAKE_AGGREGATION_CORPUS)
            else:
                raise ContractError(f"{location}: expected invalid take aggregation")
        else:
            if keywords is not None:
                raise ContractError(
                    f"{location}: valid case must not define expected_error_keywords"
                )
            actual_status = validate_take_graph(sessions, mode=mode)
            if actual_status != expected_status:
                raise ContractError(
                    f"{location}: expected {expected_status}, got {actual_status}"
                )

    duplicates = sorted(
        case_id for case_id, count in Counter(case_ids).items() if count > 1
    )
    if duplicates:
        raise ContractError(
            f"{TAKE_AGGREGATION_CORPUS}: duplicate case ids {duplicates}"
        )
    return len(cases)


def validate_artifact_response_case(
    case: dict[str, Any],
    descriptor: dict[str, Any],
    location: str,
) -> None:
    expected_fields = {"id", "method", "status", "headers", "body"}
    if set(case) != expected_fields:
        raise ContractError(
            f"{location}: expected exactly fields {sorted(expected_fields)}"
        )
    expected_operations = {
        "get-200": ("GET", 200),
        "get-206": ("GET", 206),
        "head-200": ("HEAD", 200),
    }
    case_id = case.get("id")
    if case_id not in expected_operations:
        raise ContractError(f"{location}: unknown response case id {case_id!r}")
    if (case.get("method"), case.get("status")) != expected_operations[case_id]:
        raise ContractError(f"{location}: method/status does not match case id")

    headers = require_mapping(case.get("headers"), f"{location}.headers")
    complete_headers = {"Accept-Ranges", "Content-Length", "Content-Type", "ETag"}
    expected_headers = (
        complete_headers | {"Content-Range"}
        if case_id == "get-206"
        else complete_headers
    )
    if set(headers) != expected_headers or not all(
        isinstance(value, str) for value in headers.values()
    ):
        raise ContractError(
            f"{location}: response headers must be exact wire-string metadata"
        )
    if headers["Accept-Ranges"] != "bytes":
        raise ContractError(f"{location}: Accept-Ranges must equal bytes")
    if headers["Content-Type"] != descriptor["media_type"]:
        raise ContractError(
            f"{location}: Content-Type must equal manifest descriptor media_type"
        )
    if headers["ETag"] != f'"{descriptor["sha256"]}"':
        raise ContractError(
            f"{location}: ETag must equal quoted manifest descriptor sha256"
        )
    content_length = headers["Content-Length"]
    if re.fullmatch(r"0|[1-9][0-9]*", content_length) is None:
        raise ContractError(f"{location}: Content-Length must be canonical decimal")
    response_bytes = int(content_length)

    body = require_mapping(case.get("body"), f"{location}.body")
    if set(body) != {"present", "bytes"}:
        raise ContractError(f"{location}: body metadata must be closed")
    if not isinstance(body.get("present"), bool) or type(body.get("bytes")) is not int:
        raise ContractError(f"{location}: body metadata types are invalid")
    if body["bytes"] < 0:
        raise ContractError(f"{location}: body bytes must be nonnegative")

    if case_id == "get-206":
        match = re.fullmatch(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)", headers["Content-Range"])
        if match is None:
            raise ContractError(f"{location}: Content-Range is not a satisfied byte range")
        first, last, complete_length = (int(value) for value in match.groups())
        if not (0 <= first <= last < complete_length):
            raise ContractError(f"{location}: Content-Range interval is outside the artifact")
        if complete_length != descriptor["bytes"]:
            raise ContractError(
                f"{location}: Content-Range complete length must equal descriptor bytes"
            )
        if response_bytes != last - first + 1:
            raise ContractError(
                f"{location}: partial Content-Length must equal selected inclusive range bytes"
            )
        if not body["present"] or body["bytes"] != response_bytes:
            raise ContractError(f"{location}: GET 206 body metadata differs from selected range")
    else:
        if response_bytes != descriptor["bytes"]:
            raise ContractError(
                f"{location}: complete Content-Length must equal descriptor bytes"
            )
        if case_id == "head-200":
            if body != {"present": False, "bytes": 0}:
                raise ContractError(f"{location}: HEAD 200 must remain bodyless")
        elif body != {"present": True, "bytes": descriptor["bytes"]}:
            raise ContractError(f"{location}: GET 200 body metadata must cover the artifact")


def validate_artifact_response_corpus(
    schemas: dict[str, tuple[str, dict[str, Any]]]
) -> tuple[int, int]:
    corpus = require_mapping(
        load_json(ARTIFACT_RESPONSE_CORPUS), str(ARTIFACT_RESPONSE_CORPUS)
    )
    expected_root_fields = {
        "schema",
        "fixture_scope",
        "manifest_fixture",
        "artifact_id",
        "cases",
        "negative_mutations",
    }
    if set(corpus) != expected_root_fields:
        raise ContractError(
            f"{ARTIFACT_RESPONSE_CORPUS}: expected exactly root fields "
            f"{sorted(expected_root_fields)}"
        )
    if corpus.get("schema") != "ylx.artifact-response-conformance-corpus.v1":
        raise ContractError(
            f"{ARTIFACT_RESPONSE_CORPUS}: unexpected schema discriminator"
        )
    if corpus.get("fixture_scope") != "synthetic-non-production":
        raise ContractError(
            f"{ARTIFACT_RESPONSE_CORPUS}: fixture_scope must remain synthetic-non-production"
        )

    relative = corpus.get("manifest_fixture")
    if not isinstance(relative, str):
        raise ContractError(f"{ARTIFACT_RESPONSE_CORPUS}: manifest_fixture must be a string")
    relative_path = Path(relative)
    if (
        relative_path.is_absolute()
        or relative_path.as_posix() != relative
        or ".." in relative_path.parts
        or len(relative_path.parts) != 2
        or relative_path.parts[0] != "valid"
        or relative_path.suffix != ".json"
    ):
        raise ContractError(
            f"{ARTIFACT_RESPONSE_CORPUS}: manifest_fixture must name one valid fixture"
        )
    manifest_path = FIXTURES / relative_path
    manifest = require_mapping(load_json(manifest_path), str(manifest_path))
    session_schema = schemas["ylx.device-session.v1"][1]
    errors = list(
        Draft202012Validator(
            session_schema, format_checker=FORMAT_CHECKER
        ).iter_errors(manifest)
    )
    if errors:
        raise ContractError(
            f"{manifest_path}: response corpus source is not a valid Device Session\n"
            f"{format_errors(errors)}"
        )
    validate_session_invariants(manifest, manifest_path)

    artifact_id = corpus.get("artifact_id")
    descriptors = [
        descriptor
        for descriptor in artifact_descriptors(manifest)
        if descriptor["artifact_id"] == artifact_id
    ]
    if len(descriptors) != 1:
        raise ContractError(
            f"{ARTIFACT_RESPONSE_CORPUS}: artifact_id must resolve one manifest descriptor"
        )
    descriptor = descriptors[0]

    raw_cases = corpus.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ContractError(f"{ARTIFACT_RESPONSE_CORPUS}: cases must be a nonempty array")
    cases_by_id: dict[str, dict[str, Any]] = {}
    for index, raw_case in enumerate(raw_cases):
        location = f"{ARTIFACT_RESPONSE_CORPUS}: cases[{index}]"
        case = require_mapping(raw_case, location)
        validate_artifact_response_case(case, descriptor, location)
        case_id = case["id"]
        if case_id in cases_by_id:
            raise ContractError(f"{ARTIFACT_RESPONSE_CORPUS}: duplicate case id {case_id}")
        cases_by_id[case_id] = case
    if set(cases_by_id) != {"get-200", "get-206", "head-200"}:
        raise ContractError(
            f"{ARTIFACT_RESPONSE_CORPUS}: cases must cover GET 200, GET 206, and HEAD 200"
        )

    raw_mutations = corpus.get("negative_mutations")
    if not isinstance(raw_mutations, list) or not raw_mutations:
        raise ContractError(
            f"{ARTIFACT_RESPONSE_CORPUS}: negative_mutations must be a nonempty array"
        )
    mutation_ids: list[str] = []
    mutation_fields = {
        "id",
        "case_id",
        "operation",
        "header",
        "value",
        "expected_error_keywords",
    }
    for index, raw_mutation in enumerate(raw_mutations):
        location = f"{ARTIFACT_RESPONSE_CORPUS}: negative_mutations[{index}]"
        mutation = require_mapping(raw_mutation, location)
        if set(mutation) != mutation_fields:
            raise ContractError(f"{location}: invalid mutation fields")
        mutation_id = mutation.get("id")
        if not isinstance(mutation_id, str) or not mutation_id:
            raise ContractError(f"{location}: mutation id must be a nonempty string")
        mutation_ids.append(mutation_id)
        case_id = mutation.get("case_id")
        source_case = cases_by_id.get(case_id)
        if source_case is None:
            raise ContractError(f"{location}: mutation names an unknown case")
        if mutation.get("operation") != "replace-header":
            raise ContractError(f"{location}: unsupported mutation operation")
        header = mutation.get("header")
        value = mutation.get("value")
        if not isinstance(header, str) or header not in source_case["headers"]:
            raise ContractError(f"{location}: mutation names an unknown response header")
        if not isinstance(value, str):
            raise ContractError(f"{location}: replacement header value must be a string")
        keywords = mutation.get("expected_error_keywords")
        if not isinstance(keywords, list) or not keywords or not all(
            isinstance(keyword, str) and keyword for keyword in keywords
        ):
            raise ContractError(f"{location}: expected_error_keywords must be nonempty strings")
        candidate = deepcopy(source_case)
        candidate["headers"][header] = value
        try:
            validate_artifact_response_case(candidate, descriptor, location)
        except ContractError as error:
            require_keywords(str(error), keywords, ARTIFACT_RESPONSE_CORPUS)
        else:
            raise ContractError(f"{location}: expected response mutation to fail")

    duplicates = sorted(
        mutation_id
        for mutation_id, count in Counter(mutation_ids).items()
        if count > 1
    )
    if duplicates:
        raise ContractError(
            f"{ARTIFACT_RESPONSE_CORPUS}: duplicate mutation ids {duplicates}"
        )
    if set(mutation_ids) != {"get-206-content-type-drift"}:
        raise ContractError(
            f"{ARTIFACT_RESPONSE_CORPUS}: negative mutation coverage drifted"
        )
    return len(raw_cases), len(raw_mutations)


def validate_record_invariants(record: dict[str, Any], fixture: Path) -> None:
    if record["clock"]["epoch_id"] != record["session_id"]:
        raise ContractError(f"{fixture}: clock epoch_id != session_id")
    if record["schema"] == "ylx.frame-record.v1":
        association = record["imu_association"]
        if association["status"] == "associated":
            first = association["first_sequence"]
            last = association["last_sequence"]
            count = association["sample_count"]
            if last < first or count != last - first + 1:
                raise ContractError(
                    f"{fixture}: IMU association bounds do not match sample_count"
                )


def dropped_frames_before(
    drop_events: Iterable[dict[str, Any]], sequence: int, lower_bound: int = 0
) -> int:
    total = 0
    for event in drop_events:
        total += max(
            0,
            min(sequence, event["end_frame"]) - max(lower_bound, event["start_frame"]),
        )
    return total


def validate_record_joins(
    records: list[tuple[Path, dict[str, Any]]],
    sessions: dict[str, tuple[Path, dict[str, Any], bytes]],
) -> None:
    frames_by_session_and_sequence: dict[tuple[str, int], tuple[Path, dict[str, Any]]] = {}
    imu_records: list[tuple[Path, dict[str, Any]]] = []
    record_keys: dict[tuple[str, str, int], Path] = {}
    records_by_stream_and_session: dict[
        tuple[str, str], list[tuple[Path, dict[str, Any]]]
    ] = {}
    for path, record in records:
        session_entry = sessions.get(record["session_id"])
        if session_entry is None:
            raise ContractError(f"{path}: JSONL record has no matching golden Device Session")
        _, session, _ = session_entry
        stream = record["schema"]
        record_key = (stream, record["session_id"], record["sequence"])
        if record_key in record_keys:
            raise ContractError(
                f"{path}: duplicate stream/session/sequence key also used by {record_keys[record_key]}"
            )
        record_keys[record_key] = path
        records_by_stream_and_session.setdefault(
            (stream, record["session_id"]), []
        ).append((path, record))
        if record["timestamp"] > session["time"]["duration_seconds"] * 1_000_000_000:
            raise ContractError(f"{path}: record timestamp lies outside session duration")
        if record["schema"] == "ylx.imu-sample.v1":
            if record["raw_axes"]["units"] != session["imu"]["units"]:
                raise ContractError(f"{path}: IMU units differ from matching Device Session")
            imu_records.append((path, record))
            continue

        key = (record["session_id"], record["sequence"])
        frames_by_session_and_sequence[key] = (path, record)
        if record["output_frame_index"] >= session["frames"]["count"]:
            raise ContractError(f"{path}: output_frame_index exceeds manifest frames.count")
        drop_events = session["integrity"]["drop_events"]
        if any(
            event["start_frame"] <= record["sequence"] < event["end_frame"]
            for event in drop_events
        ):
            raise ContractError(f"{path}: retained frame sequence lies in a drop interval")
        associations = record["video_associations"]
        video = session["video"]
        if video["layout"] == "raw-side-by-side":
            if len(associations) != 1 or associations[0]["role"] != "video.raw-side-by-side":
                raise ContractError(
                    f"{path}: raw-side-by-side frame must have exactly one matching video association"
                )
            association = associations[0]
            if association["artifact_id"] != video["artifact"]["artifact_id"]:
                raise ContractError(f"{path}: raw video association artifact_id mismatch")
            if "segment_index" in association:
                raise ContractError(f"{path}: raw video association must omit segment_index")
            if association["artifact_frame_index"] != record["output_frame_index"]:
                raise ContractError(
                    f"{path}: raw artifact_frame_index must equal dense output_frame_index"
                )
            continue

        sequence_origin = video["segments"][0]["start_frame"]
        expected_output_index = (
            record["sequence"]
            - sequence_origin
            - dropped_frames_before(
                drop_events,
                record["sequence"],
                lower_bound=sequence_origin,
            )
        )
        if record["output_frame_index"] != expected_output_index:
            raise ContractError(
                f"{path}: output_frame_index is not dense after accounted drops"
            )

        by_role = {association["role"]: association for association in associations}
        if len(associations) != 2 or set(by_role) != {"video.left", "video.right"}:
            raise ContractError(
                f"{path}: split-eye frame must have exactly one left and one right association"
            )
        segment_indices = {association.get("segment_index") for association in associations}
        if len(segment_indices) != 1:
            raise ContractError(f"{path}: split-eye associations use different segment_index values")
        segment_index = next(iter(segment_indices))
        if not isinstance(segment_index, int) or not 0 <= segment_index < len(video["segments"]):
            raise ContractError(f"{path}: video association segment_index is out of range")
        segment = video["segments"][segment_index]
        if not segment["start_frame"] <= record["sequence"] < segment["end_frame"]:
            raise ContractError(f"{path}: frame sequence lies outside its associated segment")
        artifact_frame_indices = {
            association["artifact_frame_index"] for association in associations
        }
        if len(artifact_frame_indices) != 1:
            raise ContractError(
                f"{path}: left/right artifact_frame_index values do not identify the same retained frame"
            )
        expected_artifact_index = (
            record["sequence"]
            - segment["start_frame"]
            - dropped_frames_before(
                drop_events,
                record["sequence"],
                lower_bound=segment["start_frame"],
            )
        )
        if artifact_frame_indices != {expected_artifact_index}:
            raise ContractError(
                f"{path}: artifact_frame_index is not dense within its segment"
            )
        for role, association in by_role.items():
            eye = role.removeprefix("video.")
            if association["artifact_id"] != segment["artifacts"][eye]["artifact_id"]:
                raise ContractError(
                    f"{path}: {role} association artifact_id does not match segment descriptor"
                )

    for (stream, session_id), stream_records in records_by_stream_and_session.items():
        ordered = sorted(stream_records, key=lambda item: item[1]["sequence"])
        if any(
            left[1]["timestamp"] >= right[1]["timestamp"]
            for left, right in pairwise(ordered)
        ):
            raise ContractError(
                f"{stream} records for session {session_id}: timestamps must increase with sequence"
            )

    for path, imu in imu_records:
        association = imu["frame_association"]
        if association["status"] != "associated":
            continue
        frame_key = (imu["session_id"], association["frame_sequence"])
        frame_entry = frames_by_session_and_sequence.get(frame_key)
        if frame_entry is None:
            raise ContractError(
                f"{path}: associated IMU sample has no matching golden frame record"
            )
        frame_path, frame = frame_entry
        expected_offset = imu["timestamp"] - frame["timestamp"]
        if association["offset_nanoseconds"] != expected_offset:
            raise ContractError(
                f"{path}: IMU offset_nanoseconds does not match {frame_path} timestamps"
            )
        frame_imu = frame["imu_association"]
        if frame_imu["status"] != "associated" or not (
            frame_imu["first_sequence"]
            <= imu["sequence"]
            <= frame_imu["last_sequence"]
        ):
            raise ContractError(
                f"{path}: IMU sequence is outside the matching frame association bounds"
            )


def validate_record_corpus_document(
    value: Any,
    discriminator: str,
    schemas: dict[str, tuple[str, dict[str, Any]]],
    location: Path,
) -> dict[str, Any]:
    document = require_mapping(value, str(location))
    schema_entry = schemas.get(discriminator)
    if schema_entry is None:
        raise ContractError(
            f"{location}: contract identities omit required schema {discriminator}"
        )
    _, schema = schema_entry
    errors = list(
        Draft202012Validator(
            schema, format_checker=FORMAT_CHECKER
        ).iter_errors(document)
    )
    if errors:
        raise ContractError(
            f"{location}: record corpus document is not valid {discriminator}\n"
            f"{format_errors(errors)}"
        )
    if discriminator == "ylx.device-session.v1":
        validate_session_invariants(document, location)
    else:
        validate_record_invariants(document, location)
    return document


def validate_complete_record_case(
    case: dict[str, Any],
    schemas: dict[str, tuple[str, dict[str, Any]]],
    location: Path,
) -> None:
    session = validate_record_corpus_document(
        case["session"], "ylx.device-session.v1", schemas, location / "session"
    )
    raw_frames = case["frames"]
    raw_imu = case["imu"]
    if not isinstance(raw_frames, list):
        raise ContractError(f"{location}: frames must be an array")
    if not isinstance(raw_imu, list):
        raise ContractError(f"{location}: imu must be an array")

    frames = [
        validate_record_corpus_document(
            value,
            "ylx.frame-record.v1",
            schemas,
            location / f"frames[{index}]",
        )
        for index, value in enumerate(raw_frames)
    ]
    imu = [
        validate_record_corpus_document(
            value,
            "ylx.imu-sample.v1",
            schemas,
            location / f"imu[{index}]",
        )
        for index, value in enumerate(raw_imu)
    ]

    video = session["video"]
    if video["layout"] != "split-eyes":
        raise ContractError(
            f"{location}: a complete record corpus case requires split-eye segment "
            "bounds to define the manifest frame domain"
        )
    frame_start = video["segments"][0]["start_frame"]
    frame_end = video["segments"][-1]["end_frame"]
    drop_events = session["integrity"]["drop_events"]
    dropped_sequences = {
        sequence
        for event in drop_events
        for sequence in range(event["start_frame"], event["end_frame"])
    }
    expected_frame_sequences = set(range(frame_start, frame_end)) - dropped_sequences
    frame_sequences = [frame["sequence"] for frame in frames]
    if frame_sequences != sorted(expected_frame_sequences):
        raise ContractError(
            f"{location}: frame sequence set must equal the complete manifest domain "
            "minus accounted drops"
        )
    frame_count = session["frames"]["count"]
    if len(frames) != frame_count:
        raise ContractError(
            f"{location}: frame record count does not equal manifest frames.count"
        )
    output_indices = [frame["output_frame_index"] for frame in frames]
    if output_indices != list(range(frame_count)):
        raise ContractError(
            f"{location}: output_frame_index set must be exactly 0..frames.count-1"
        )
    frame_timestamps = [frame["timestamp"] for frame in frames]
    if any(left >= right for left, right in pairwise(frame_timestamps)):
        raise ContractError(f"{location}: frame timestamps must be strictly increasing")
    source_frame_ids = [frame["source_frame_id"] for frame in frames]
    if any(left >= right for left, right in pairwise(source_frame_ids)):
        raise ContractError(
            f"{location}: source_frame_id values must be strictly increasing"
        )

    imu_sequences = [sample["sequence"] for sample in imu]
    if any(left >= right for left, right in pairwise(imu_sequences)):
        raise ContractError(
            f"{location}: IMU sequence values must be strictly increasing"
        )
    imu_timestamps = [sample["timestamp"] for sample in imu]
    if any(left >= right for left, right in pairwise(imu_timestamps)):
        raise ContractError(f"{location}: IMU timestamps must be strictly increasing")
    actual_imu_sequences = set(imu_sequences)
    frame_ranges = [
        (
            frame,
            frame["imu_association"]["first_sequence"],
            frame["imu_association"]["last_sequence"],
        )
        for frame in frames
        if frame["imu_association"]["status"] == "associated"
    ]
    declared_imu_count = sum(last - first + 1 for _, first, last in frame_ranges)
    covering_frames = {
        sequence: [
            frame
            for frame, first, last in frame_ranges
            if first <= sequence <= last
        ]
        for sequence in actual_imu_sequences
    }
    if (
        len(actual_imu_sequences) != len(imu_sequences)
        or declared_imu_count != len(actual_imu_sequences)
        or any(len(covering_frames[sequence]) != 1 for sequence in actual_imu_sequences)
    ):
        raise ContractError(
            f"{location}: IMU sequence set from frame ranges must cover every "
            "actual IMU sequence exactly once"
        )
    if len(imu) != session["imu"]["sample_count"]:
        raise ContractError(
            f"{location}: IMU record count does not equal manifest imu.sample_count"
        )
    for sample in imu:
        covering_frame = covering_frames[sample["sequence"]][0]
        association = sample["frame_association"]
        if (
            association["status"] != "associated"
            or association["frame_sequence"] != covering_frame["sequence"]
        ):
            raise ContractError(
                f"{location}: reciprocal frame/IMU association does not identify "
                f"the unique covering frame for IMU sequence {sample['sequence']}"
            )

    session_id = session["session_id"]
    session_index = {session_id: (location / "session", session, b"")}
    records = [
        *((location / f"frames[{index}]", frame) for index, frame in enumerate(frames)),
        *((location / f"imu[{index}]", sample) for index, sample in enumerate(imu)),
    ]
    validate_record_joins(records, session_index)


def apply_record_corpus_mutation(
    case: dict[str, Any], mutation: dict[str, Any], location: Path
) -> None:
    target = case[mutation["target"]]
    index = mutation["index"]
    if mutation["operation"] == "remove":
        del target[index]
        return

    node: Any = target[index]
    path = mutation["path"]
    try:
        for part in path[:-1]:
            if (
                isinstance(node, dict) and isinstance(part, str) and part in node
            ) or (
                isinstance(node, list)
                and type(part) is int
                and 0 <= part < len(node)
            ):
                node = node[part]
            else:
                raise TypeError(f"cannot descend through {part!r}")
        leaf = path[-1]
        if (
            isinstance(node, dict) and isinstance(leaf, str) and leaf in node
        ) or (
            isinstance(node, list)
            and type(leaf) is int
            and 0 <= leaf < len(node)
        ):
            node[leaf] = deepcopy(mutation["value"])
        else:
            raise TypeError(f"cannot assign through {leaf!r}")
    except (IndexError, KeyError, TypeError) as error:
        raise ContractError(f"{location}: mutation path cannot be applied: {error}") from error


def validate_record_corpus(
    schemas: dict[str, tuple[str, dict[str, Any]]]
) -> tuple[int, int]:
    corpus_dir = RECORD_CORPUS.parent
    expected_entries = {
        RECORD_CORPUS.name,
        TAKE_AGGREGATION_CORPUS.name,
        ARTIFACT_RESPONSE_CORPUS.name,
    }
    actual_entries = {
        path.relative_to(corpus_dir).as_posix() for path in corpus_dir.rglob("*")
    }
    if actual_entries != expected_entries or not RECORD_CORPUS.is_file():
        raise ContractError(
            f"{corpus_dir}: corpus entries must exactly match the packaged index; "
            f"missing={sorted(expected_entries - actual_entries)}; "
            f"unknown={sorted(actual_entries - expected_entries)}"
        )
    try:
        root = require_mapping(load_json(RECORD_CORPUS), str(RECORD_CORPUS))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"{RECORD_CORPUS}: invalid JSON corpus: {error}") from error
    if set(root) != RECORD_CORPUS_ROOT_FIELDS:
        raise ContractError(
            f"{RECORD_CORPUS}: expected exactly root fields "
            f"{sorted(RECORD_CORPUS_ROOT_FIELDS)}"
        )
    if root.get("schema_version") != "ylx.record-corpus-fixture.v1":
        raise ContractError(f"{RECORD_CORPUS}: unexpected schema_version")
    cases = root.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ContractError(f"{RECORD_CORPUS}: cases must be a nonempty array")

    case_names: set[str] = set()
    mutation_count = 0
    for case_index, raw_case in enumerate(cases):
        location = Path(f"{RECORD_CORPUS}#cases[{case_index}]")
        case = require_mapping(raw_case, str(location))
        if set(case) != RECORD_CORPUS_CASE_FIELDS:
            raise ContractError(
                f"{location}: expected exactly case fields "
                f"{sorted(RECORD_CORPUS_CASE_FIELDS)}"
            )
        name = case.get("name")
        if not isinstance(name, str) or re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", name
        ) is None:
            raise ContractError(f"{location}: name must be a nonempty kebab-case ID")
        if name in case_names:
            raise ContractError(f"{location}: duplicate record corpus case name {name!r}")
        case_names.add(name)
        if not isinstance(case.get("session"), dict):
            raise ContractError(f"{location}: session must be an object")
        if not isinstance(case.get("frames"), list) or not case["frames"]:
            raise ContractError(f"{location}: frames must be a nonempty array")
        if not isinstance(case.get("imu"), list) or not case["imu"]:
            raise ContractError(f"{location}: imu must be a nonempty array")
        mutations = case.get("negative_mutations")
        if not isinstance(mutations, list) or not mutations:
            raise ContractError(
                f"{location}: negative_mutations must be a nonempty array"
            )

        validate_complete_record_case(case, schemas, location)
        mutation_ids: set[str] = set()
        for mutation_index, raw_mutation in enumerate(mutations):
            mutation_location = location / f"negative_mutations[{mutation_index}]"
            mutation = require_mapping(raw_mutation, str(mutation_location))
            operation = mutation.get("operation")
            expected_fields = RECORD_CORPUS_MUTATION_FIELDS | (
                {"path", "value"} if operation == "set" else set()
            )
            if set(mutation) != expected_fields:
                raise ContractError(
                    f"{mutation_location}: expected exactly mutation fields "
                    f"{sorted(expected_fields)}"
                )
            mutation_id = mutation.get("id")
            if not isinstance(mutation_id, str) or re.fullmatch(
                r"[a-z0-9]+(?:-[a-z0-9]+)*", mutation_id
            ) is None:
                raise ContractError(
                    f"{mutation_location}: id must be a nonempty kebab-case ID"
                )
            if mutation_id in mutation_ids:
                raise ContractError(
                    f"{mutation_location}: duplicate mutation id {mutation_id!r}"
                )
            mutation_ids.add(mutation_id)
            if not isinstance(operation, str) or operation not in {"remove", "set"}:
                raise ContractError(
                    f"{mutation_location}: operation must be remove or set"
                )
            target_name = mutation.get("target")
            if not isinstance(target_name, str) or target_name not in {"frames", "imu"}:
                raise ContractError(
                    f"{mutation_location}: target must be frames or imu"
                )
            index = mutation.get("index")
            target = case[target_name]
            if type(index) is not int or not 0 <= index < len(target):
                raise ContractError(
                    f"{mutation_location}: index is outside the original target array"
                )
            expected_error = mutation.get("expected_error")
            if not isinstance(expected_error, str) or not expected_error.strip():
                raise ContractError(
                    f"{mutation_location}: expected_error must be a nonempty string"
                )
            if operation == "set":
                path = mutation.get("path")
                if not isinstance(path, list) or not path or not all(
                    (isinstance(part, str) and bool(part))
                    or (type(part) is int and part >= 0)
                    for part in path
                ):
                    raise ContractError(
                        f"{mutation_location}: path must contain nonempty keys or "
                        "nonnegative array indices"
                    )

            mutated = deepcopy(case)
            apply_record_corpus_mutation(mutated, mutation, mutation_location)
            try:
                validate_complete_record_case(mutated, schemas, mutation_location)
            except ContractError as error:
                require_keywords(str(error), [expected_error], mutation_location)
            else:
                raise ContractError(
                    f"{mutation_location}: mutation {mutation_id!r} was not rejected"
                )
            mutation_count += 1
    return len(cases), mutation_count


def synthetic_publication_prefix_authority() -> tuple[Path, set[str]]:
    authority_path = FIXTURES / "publication-prefix-authority.synthetic.json"
    authority = require_mapping(load_json(authority_path), str(authority_path))
    expected_fields = {
        "schema",
        "scope",
        "deployment_ref",
        "rendered_prefixes",
    }
    if set(authority) != expected_fields:
        raise ContractError(
            f"{authority_path}: synthetic prefix authority must contain exactly "
            f"{sorted(expected_fields)}"
        )
    if authority.get("schema") != "ylx.synthetic-publication-prefix-authority.v1":
        raise ContractError(f"{authority_path}: unexpected schema discriminator")
    if authority.get("scope") != "fixture-only-non-production":
        raise ContractError(
            f"{authority_path}: prefix authority must remain explicitly synthetic"
        )
    if authority.get("deployment_ref") != (
        "synthetic-fixture:publication-prefixes-v1"
    ):
        raise ContractError(
            f"{authority_path}: unexpected synthetic deployment_ref"
        )

    rendered = require_mapping(
        authority.get("rendered_prefixes"),
        f"{authority_path}: rendered_prefixes",
    )
    if set(rendered) != {
        "first_publication_prefixes",
        "production_canary_raw_prefix",
    }:
        raise ContractError(
            f"{authority_path}: rendered_prefixes field set drifted"
        )
    first_publication = require_mapping(
        rendered.get("first_publication_prefixes"),
        f"{authority_path}: rendered_prefixes.first_publication_prefixes",
    )
    if set(first_publication) != {"ylx_transfer", "ylx_card_pipeline"}:
        raise ContractError(
            f"{authority_path}: first-publication adapter prefix set drifted"
        )
    prefixes = [
        first_publication["ylx_transfer"],
        first_publication["ylx_card_pipeline"],
        rendered["production_canary_raw_prefix"],
    ]
    for prefix in prefixes:
        if not isinstance(prefix, str) or not prefix or not prefix.endswith("/"):
            raise ContractError(
                f"{authority_path}: every synthetic raw_prefix must be a nonempty "
                "relative directory prefix ending in slash"
            )
        if (
            len(prefix) > 512
            or prefix.startswith("/")
            or "\\" in prefix
            or "__ylx_evidence__" in prefix.split("/")
            or any(
                segment in {"", ".", ".."}
                for segment in prefix.removesuffix("/").split("/")
            )
            or any(
                ord(character) < 0x20 or ord(character) == 0x7F
                for character in prefix
            )
        ):
            raise ContractError(
                f"{authority_path}: unsafe synthetic raw_prefix {prefix!r}"
            )
    if len(prefixes) != len(set(prefixes)):
        raise ContractError(f"{authority_path}: synthetic raw_prefix values must be unique")
    if any(
        left.startswith(right) or right.startswith(left)
        for index, left in enumerate(prefixes)
        for right in prefixes[index + 1 :]
    ):
        raise ContractError(
            f"{authority_path}: synthetic raw_prefix values must be path-disjoint"
        )
    return authority_path, set(prefixes)


def validate_publication_invariants(
    publication: dict[str, Any], fixture: Path, sessions: dict[str, tuple[Path, dict[str, Any], bytes]]
) -> None:
    source = publication["source_manifest"]
    if source["session_id"] not in sessions:
        raise ContractError(f"{fixture}: no golden source manifest for session")
    source_path, session, raw = sessions[source["session_id"]]
    digest = hashlib.sha256(raw).hexdigest()
    if source["bytes"] != len(raw) or source["sha256"] != digest:
        raise ContractError(f"{fixture}: source manifest bytes/sha256 do not match {source_path}")
    identity_suffix = f'{publication["device"]["device_id"]}/{source["session_id"]}/'
    source_leaf = f"f-{digest}"
    source_key = source["object_key"]
    if not source_key.endswith(source_leaf):
        raise ContractError(f"{fixture}: source manifest object_key leaf does not match sha256")
    authority = source_key[: -len(source_leaf)]
    if not authority.endswith(identity_suffix):
        raise ContractError(
            f"{fixture}: source manifest object_key has wrong raw_prefix/device/session authority"
        )
    raw_prefix = authority[: -len(identity_suffix)]
    authority_path, allowed_raw_prefixes = synthetic_publication_prefix_authority()
    if raw_prefix not in allowed_raw_prefixes:
        raise ContractError(
            f"{fixture}: publication raw_prefix {raw_prefix!r} is not allowed by "
            f"the rendered deployment authority in {authority_path}"
        )
    expected_publication_key = f"{authority}__ylx_evidence__/publication.json"
    if publication["publication_object_key"] != expected_publication_key:
        raise ContractError(
            f"{fixture}: publication object_key has wrong raw_prefix/device/session authority"
        )
    for key in ("manifest_id", "session_id", "volume_id"):
        if source[key] != session[key]:
            raise ContractError(f"{fixture}: source manifest {key} mismatch")
    if source["schema"] != session["schema"]:
        raise ContractError(f"{fixture}: source manifest schema mismatch")
    if publication["take"] != session["take"]:
        raise ContractError(f"{fixture}: publication take is not an exact manifest copy")
    if publication["device"] != {
        "device_id": session["device"]["device_id"],
        "device_label": session["device"]["device_label"],
    }:
        raise ContractError(f"{fixture}: publication device projection mismatch")

    source_artifacts = {
        artifact["artifact_id"]: artifact for artifact in artifact_descriptors(session)
    }
    source_ids = set(source_artifacts)
    source_ids_by_role: dict[str, set[str]] = {}
    for source_id, source_artifact in source_artifacts.items():
        source_ids_by_role.setdefault(source_artifact["role"], set()).add(source_id)
    referenced_source_ids: set[str] = set()
    roles: set[str] = set()
    content_by_id: dict[str, tuple[Any, ...]] = {}
    content_by_key: dict[str, tuple[Any, ...]] = {}
    normalized = False
    identity_errors: list[str] = []
    artifacts_by_role: dict[str, dict[str, Any]] = {}
    for artifact in publication["artifacts"]:
        artifact_id = artifact["artifact_id"]
        role = artifact["role"]
        if role in roles:
            identity_errors.append(f"duplicate role {role}")
        roles.add(role)
        artifacts_by_role.setdefault(role, artifact)
        if artifact_id != artifact["sha256"]:
            identity_errors.append("artifact_id != sha256")
        expected_object_key = f"{authority}f-{artifact_id}"
        if artifact["object_key"] != expected_object_key:
            identity_errors.append("object_key has wrong raw_prefix/device/session authority or leaf")
        provenance = artifact["provenance"]
        provenance_ids = provenance["source_artifact_ids"]
        referenced_source_ids.update(provenance_ids)
        unknown_sources = set(provenance_ids) - source_ids
        if unknown_sources:
            raise ContractError(f"{fixture}: provenance references unknown source artifacts")
        if provenance["kind"] == "device-artifact":
            if provenance_ids != [artifact_id]:
                identity_errors.append(
                    "direct provenance must name exactly its own source artifact"
                )
            source_artifact = source_artifacts.get(artifact_id)
            if source_artifact is None:
                identity_errors.append("direct artifact is absent from source manifest")
            else:
                for key in ("artifact_id", "role", "media_type", "bytes", "sha256"):
                    if artifact[key] != source_artifact[key]:
                        identity_errors.append(
                            f"direct artifact descriptor changes source {key}"
                        )
        content_identity = (
            artifact_id,
            artifact["sha256"],
            artifact["bytes"],
            artifact["media_type"],
            artifact["object_key"],
            json.dumps(provenance, sort_keys=True, separators=(",", ":")),
        )
        previous_for_id = content_by_id.setdefault(artifact_id, content_identity)
        if previous_for_id != content_identity:
            identity_errors.append("shared artifact_id has inconsistent byte descriptor or provenance")
        previous_for_key = content_by_key.setdefault(artifact["object_key"], content_identity)
        if previous_for_key != content_identity:
            identity_errors.append("shared object_key has inconsistent byte descriptor or provenance")
        normalized = normalized or provenance["kind"] == "normalized-output"
    if referenced_source_ids != source_ids:
        missing = sorted(source_ids - referenced_source_ids)
        unknown = sorted(referenced_source_ids - source_ids)
        identity_errors.append(
            "publication provenance does not cover complete source inventory; "
            f"missing={missing}; unknown={unknown}"
        )
    if normalized and sum(
        item["role"] == "publication.transform-log"
        for item in publication["artifacts"]
    ) != 1:
        identity_errors.append(
            "normalized publication requires exactly one publication.transform-log evidence"
        )
    for required_role in ("video.left", "video.right", "imu.samples", "frames.index"):
        if required_role not in artifacts_by_role:
            identity_errors.append(f"publication requires exactly one {required_role}")
    for video_role in ("video.left", "video.right"):
        published_video = artifacts_by_role.get(video_role)
        if published_video is not None and published_video["media_type"] != "video/mp4":
            identity_errors.append(f"publication {video_role} must use video/mp4")

    if publication.get("schema") == "ylx.bucket-publication.v3":
        source_audio = publication["source_audio"]
        session_audio = session.get("audio")
        if session.get("schema") != "ylx.device-session.v2":
            identity_errors.append("publication v3 source_manifest must bind a Device Session v2")
        elif not isinstance(session_audio, dict):
            identity_errors.append("publication v3 source manifest lacks explicit audio state")
        elif source_audio["state"] != session_audio["state"]:
            identity_errors.append("publication source_audio state differs from source manifest")
        elif source_audio["state"] == "recorded":
            expected_audio_ids = source_ids_by_role.get("audio.wav", set())
            declared_audio_ids = set(source_audio["source_artifact_ids"])
            if declared_audio_ids != expected_audio_ids:
                identity_errors.append(
                    "publication source_audio must bind the exact audio.wav source inventory; "
                    f"expected={sorted(expected_audio_ids)}; actual={sorted(declared_audio_ids)}"
                )
            published_audio = artifacts_by_role.get("audio.wav")
            if published_audio is None:
                identity_errors.append("recorded audio publication requires exactly one audio.wav artifact")
            elif set(published_audio["provenance"]["source_artifact_ids"]) != expected_audio_ids:
                identity_errors.append(
                    "published audio.wav provenance must bind the exact audio.wav source inventory"
                )
        else:
            if source_audio["reason"] != session_audio["reason"]:
                identity_errors.append("publication source_audio reason differs from source manifest")
            if "audio.wav" in artifacts_by_role:
                identity_errors.append("not_recorded audio publication must not carry audio.wav artifact")

    delivered_source_ids: set[str] = set()
    normalized_delivery_source_ids: set[str] = set()
    transform_log_source_sets: list[set[str]] = []
    for published in publication["artifacts"]:
        role = published["role"]
        provenance = published["provenance"]
        provenance_ids = set(provenance["source_artifact_ids"])
        if role == "publication.transform-log":
            transform_log_source_sets.append(provenance_ids)
            continue
        allowed_source_roles = (
            {role, "video.raw-side-by-side"}
            if role in {"video.left", "video.right"}
            else {role}
        )
        expected_source_ids = set().union(
            *(source_ids_by_role.get(source_role, set()) for source_role in allowed_source_roles)
        )
        incompatible = {
            source_id
            for source_id in provenance_ids
            if source_artifacts[source_id]["role"] not in allowed_source_roles
        }
        if incompatible:
            identity_errors.append(
                f"publication role/source join for {role} uses incompatible source artifacts "
                f"{sorted(incompatible)}"
            )
        if provenance_ids != expected_source_ids:
            identity_errors.append(
                f"publication role/source join for {role} must name the complete source role inventory; "
                f"expected={sorted(expected_source_ids)}; actual={sorted(provenance_ids)}"
            )
        delivered_source_ids.update(provenance_ids)
        if provenance["kind"] == "normalized-output":
            normalized_delivery_source_ids.update(provenance_ids)
    if delivered_source_ids != source_ids:
        identity_errors.append(
            "publication role/source joins do not deliver the complete source inventory; "
            f"missing={sorted(source_ids - delivered_source_ids)}; "
            f"unknown={sorted(delivered_source_ids - source_ids)}"
        )
    for transform_sources in transform_log_source_sets:
        if transform_sources != normalized_delivery_source_ids:
            identity_errors.append(
                "publication.transform-log provenance must bind the exact normalized source inventory; "
                f"expected={sorted(normalized_delivery_source_ids)}; "
                f"actual={sorted(transform_sources)}"
            )

    video = session["video"]
    if video["layout"] == "raw-side-by-side":
        raw_id = video["artifact"]["artifact_id"]
        for role in ("video.left", "video.right"):
            published = artifacts_by_role.get(role)
            if published is None:
                continue
            if published["provenance"]["kind"] != "normalized-output":
                identity_errors.append(
                    f"raw-side-by-side source requires normalized-output for {role}"
                )
            if published["provenance"]["source_artifact_ids"] != [raw_id]:
                identity_errors.append(
                    f"raw-side-by-side {role} must name exactly the raw video source artifact"
                )
    else:
        segments = video["segments"]
        for eye in ("left", "right"):
            role = f"video.{eye}"
            published = artifacts_by_role.get(role)
            if published is None:
                continue
            expected_sources = [
                segment["artifacts"][eye]["artifact_id"] for segment in segments
            ]
            provenance = published["provenance"]
            if len(segments) > 1:
                if provenance["kind"] != "normalized-output":
                    identity_errors.append(
                        f"multi-segment split-eyes source requires normalized-output for {role}"
                    )
                if provenance["source_artifact_ids"] != expected_sources:
                    identity_errors.append(
                        f"multi-segment {role} provenance must list every matching segment source in order"
                    )
            elif not set(expected_sources) <= set(provenance["source_artifact_ids"]):
                identity_errors.append(
                    f"{role} publication is not bound to its matching source artifact"
                )

    if parse_api_datetime(publication["published_at"]) < parse_api_datetime(session["sealed_at"]):
        identity_errors.append("published_at precedes source manifest sealed_at")
    if identity_errors:
        raise ContractError(f"{fixture}: {'; '.join(identity_errors)}")


def validate_persisted_schemas(
    current_schemas: dict[str, tuple[str, dict[str, Any]]],
    legacy_v2_schemas: dict[str, tuple[str, dict[str, Any]]],
) -> tuple[
    int,
    int,
    int,
    int,
    dict[str, tuple[Path, dict[str, Any], bytes]],
    list[tuple[Path, dict[str, Any]]],
]:
    schemas = current_schemas | legacy_v2_schemas
    valid_dir = FIXTURES / "valid"
    valid_paths = sorted(valid_dir.glob("*.json"))
    all_valid_paths = sorted(valid_dir.rglob("*.json"))
    if valid_paths != all_valid_paths or not valid_paths:
        raise ContractError(
            f"{valid_dir}: persisted valid fixtures must be nonempty, flat, and exactly enumerable"
        )
    corpus: list[tuple[Path, dict[str, Any], bytes]] = []
    for fixture in valid_paths:
        raw = fixture.read_bytes()
        value = load_json_bytes(raw, fixture)
        corpus.append((fixture, require_mapping(value, str(fixture)), raw))

    sessions: dict[str, tuple[Path, dict[str, Any], bytes]] = {}
    for fixture, value, raw in corpus:
        if value.get("schema") not in {"ylx.device-session.v1", "ylx.device-session.v2"}:
            continue
        session_id = value.get("session_id")
        if session_id in sessions:
            raise ContractError(
                f"{fixture}: duplicate golden session_id also used by {sessions[session_id][0]}"
            )
        sessions[session_id] = (fixture, value, raw)

    current_covered_discriminators: set[str] = set()
    legacy_v2_covered_discriminators: set[str] = set()
    records: list[tuple[Path, dict[str, Any]]] = []
    publications: list[tuple[Path, dict[str, Any]]] = []
    recording_states: list[tuple[Path, dict[str, Any]]] = []
    current_valid_count = 0
    legacy_v2_valid_count = 0
    for fixture, value, _ in corpus:
        discriminator = value.get("schema")
        if discriminator not in schemas:
            raise ContractError(f"{fixture}: unknown fixture discriminator {discriminator}")
        if discriminator in current_schemas:
            current_covered_discriminators.add(discriminator)
            current_valid_count += 1
        else:
            legacy_v2_covered_discriminators.add(discriminator)
            legacy_v2_valid_count += 1
        _, schema = schemas[discriminator]
        errors = list(Draft202012Validator(schema, format_checker=FORMAT_CHECKER).iter_errors(value))
        if errors:
            raise ContractError(f"{fixture}: expected valid\n{format_errors(errors)}")
        if discriminator in {"ylx.device-session.v1", "ylx.device-session.v2"}:
            validate_session_invariants(value, fixture)
        elif discriminator in {"ylx.bucket-publication.v2", "ylx.bucket-publication.v3"}:
            publications.append((fixture, value))
        elif discriminator in {"ylx.imu-sample.v1", "ylx.frame-record.v1"}:
            validate_record_invariants(value, fixture)
            records.append((fixture, value))
        elif discriminator == "ylx.imu-physical-acceptance.v1":
            validate_imu_physical_acceptance_invariants(value, fixture)
        elif discriminator == "ylx.recording-state.v1":
            recording_states.append((fixture, value))
    if current_covered_discriminators != set(current_schemas):
        raise ContractError(
            f"{valid_dir}: current schema fixture coverage mismatch; "
            f"missing={sorted(set(current_schemas) - current_covered_discriminators)}; "
            f"unknown={sorted(current_covered_discriminators - set(current_schemas))}"
        )
    if legacy_v2_covered_discriminators != set(legacy_v2_schemas):
        raise ContractError(
            f"{valid_dir}: legacy_v2 schema fixture coverage mismatch; "
            f"missing={sorted(set(legacy_v2_schemas) - legacy_v2_covered_discriminators)}; "
            f"unknown={sorted(legacy_v2_covered_discriminators - set(legacy_v2_schemas))}"
        )
    validate_take_graph(sessions, mode="closed-corpus")
    validate_record_joins(records, sessions)
    for fixture, publication in publications:
        validate_publication_invariants(publication, fixture, sessions)
    unsuccessful_session_ids = {
        state["session_id"]
        for _, state in recording_states
        if state["state"] in {"recoverable", "failed", "abandoned"}
    }
    overlap = set(sessions) & unsuccessful_session_ids
    if overlap:
        raise ContractError(
            "valid persisted corpus assigns sealed and unsuccessful terminal outcomes "
            f"to the same sessions {sorted(overlap)}"
        )

    invalid_dir = FIXTURES / "invalid"
    expected_path = invalid_dir / "expected-errors.json"
    expected = require_mapping(load_json(expected_path), str(expected_path))
    if set(expected) != {"schema", "description", "cases"}:
        raise ContractError(
            f"{expected_path}: expected exactly schema, description, and cases fields"
        )
    if expected.get("schema") != "ylx.fixture-expected-errors.v1":
        raise ContractError(f"{expected_path}: unexpected schema discriminator")
    cases = require_mapping(expected.get("cases"), f"{expected_path}: cases")
    expected_names = set(cases)
    if any(Path(name).name != name or not name.endswith(".json") for name in expected_names):
        raise ContractError(f"{expected_path}: case names must be flat JSON filenames")
    actual_names = {
        path.relative_to(invalid_dir).as_posix()
        for path in invalid_dir.rglob("*.json")
        if path != expected_path
    }
    if actual_names != expected_names:
        raise ContractError(
            f"{invalid_dir}: invalid fixtures do not exactly match expected-errors.json; "
            f"missing={sorted(expected_names - actual_names)}; "
            f"unknown={sorted(actual_names - expected_names)}"
        )
    schemas_by_basename = {basename: schema for basename, schema in schemas.values()}
    current_invalid_count = 0
    legacy_v2_invalid_count = 0
    for name, raw_case in sorted(cases.items()):
        case = require_mapping(raw_case, f"{expected_path}: cases.{name}")
        allowed_case_fields = {
            "schema_basename",
            "validation_stage",
            "expected_error_keywords",
        }
        if not {"schema_basename", "expected_error_keywords"} <= set(case) or not set(
            case
        ) <= allowed_case_fields:
            raise ContractError(f"{expected_path}: cases.{name} has invalid metadata fields")
        stage = case.get("validation_stage", "json-schema")
        if stage not in {"json-schema", "cross-field", "closed-corpus"}:
            raise ContractError(f"{expected_path}: cases.{name} has invalid validation_stage")
        keywords = case.get("expected_error_keywords")
        if not isinstance(keywords, list) or not keywords or not all(
            isinstance(keyword, str) and keyword for keyword in keywords
        ):
            raise ContractError(
                f"{expected_path}: cases.{name} expected_error_keywords must be nonempty strings"
            )
        fixture = invalid_dir / name
        value = load_json(fixture)
        basename = case.get("schema_basename")
        schema = schemas_by_basename.get(basename)
        if schema is None:
            raise ContractError(f"{fixture}: unknown schema_basename {basename!r}")
        if basename in {item[0] for item in current_schemas.values()}:
            current_invalid_count += 1
        else:
            legacy_v2_invalid_count += 1
        errors = list(Draft202012Validator(schema, format_checker=FORMAT_CHECKER).iter_errors(value))
        if stage in {"cross-field", "closed-corpus"}:
            if errors:
                raise ContractError(f"{fixture}: procedural fixture unexpectedly fails schema\n{format_errors(errors)}")
            try:
                if case["schema_basename"] in {"ylx-device-session-v1", "ylx-device-session-v2"}:
                    validate_session_invariants(value, fixture)
                    candidate_sessions = dict(sessions)
                    candidate_sessions[value["session_id"]] = (
                        fixture,
                        value,
                        fixture.read_bytes(),
                    )
                    validate_take_graph(candidate_sessions, mode="closed-corpus")
                elif case["schema_basename"] in {"ylx-bucket-publication-v2", "ylx-bucket-publication-v3"}:
                    validate_publication_invariants(value, fixture, sessions)
                elif case["schema_basename"] in {
                    "ylx-imu-sample-v1",
                    "ylx-frame-record-v1",
                }:
                    validate_record_invariants(value, fixture)
                    candidate_records = [
                        (path, record)
                        for path, record in records
                        if not (
                            record["schema"] == value["schema"]
                            and record["session_id"] == value["session_id"]
                            and record["sequence"] == value["sequence"]
                        )
                    ]
                    candidate_records.append((fixture, value))
                    validate_record_joins(candidate_records, sessions)
                elif case["schema_basename"] == "ylx-imu-physical-acceptance-v1":
                    validate_imu_physical_acceptance_invariants(value, fixture)
                else:
                    raise ContractError(
                        f"{fixture}: no procedural validator for {case['schema_basename']}"
                    )
            except ContractError as error:
                require_keywords(str(error), case["expected_error_keywords"], fixture)
            else:
                raise ContractError(f"{fixture}: expected procedural validation failure")
        else:
            if not errors:
                raise ContractError(f"{fixture}: expected schema validation failure")
            require_keywords(format_errors(errors), case["expected_error_keywords"], fixture)
    return (
        current_valid_count,
        current_invalid_count,
        legacy_v2_valid_count,
        legacy_v2_invalid_count,
        sessions,
        recording_states,
    )


def component_validator(
    spec: dict[str, Any], component: str, openapi_path: Path = OPENAPI
) -> Draft202012Validator:
    wrapper = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": openapi_path.resolve().as_uri(),
        "$ref": f"#/components/schemas/{component}",
        "components": spec["components"],
    }
    Draft202012Validator.check_schema(wrapper)
    registry = Registry()
    for path in SCHEMAS.glob("*.schema.json"):
        resource = Resource.from_contents(load_json(path))
        registry = registry.with_resource(path.resolve().as_uri(), resource)
    return Draft202012Validator(
        wrapper,
        format_checker=FORMAT_CHECKER,
        registry=registry,
    )


def api_operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (path, method, operation)
        for path, path_item in spec["paths"].items()
        for method, operation in path_item.items()
        if method in OPENAPI_OPERATION_METHODS
    ]


def json_pointer_get(document: Any, pointer: str, *, location: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ContractError(f"{location}: unsupported JSON pointer {pointer!r}")
    current = document
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if part not in current:
                raise ContractError(f"{location}: unresolved JSON pointer segment {part!r}")
            current = current[part]
        elif isinstance(current, list):
            if not part.isdecimal():
                raise ContractError(f"{location}: array pointer segment must be numeric")
            index = int(part)
            if index >= len(current):
                raise ContractError(f"{location}: array pointer index {index} out of range")
            current = current[index]
        else:
            raise ContractError(f"{location}: JSON pointer descends into non-container")
    return current


def validate_openapi_references_resolve(spec: dict[str, Any], openapi_path: Path) -> None:
    """Resolve every local or relative-file ``$ref`` reachable from an OpenAPI document."""

    documents: dict[Path, Any] = {openapi_path.resolve(): spec}
    resolved_refs: set[tuple[Path, str]] = set()
    visiting_refs: set[tuple[Path, str]] = set()

    def load_document(path: Path) -> Any:
        resolved = path.resolve()
        if resolved not in documents:
            if not resolved.exists():
                raise ContractError(f"{openapi_path}: unresolved external OpenAPI ref file {path}")
            documents[resolved] = load_yaml(resolved)
        return documents[resolved]

    def resolve_ref(base_path: Path, raw_ref: str, location: str) -> tuple[Path, str, Any]:
        target_name, separator, fragment = raw_ref.partition("#")
        if target_name:
            target_path = (base_path.parent / target_name).resolve()
        else:
            target_path = base_path.resolve()
        if not separator:
            fragment = ""
        target_document = load_document(target_path)
        target = json_pointer_get(target_document, fragment, location=location)
        return target_path, fragment, target

    def walk(value: Any, base_path: Path, location: str) -> None:
        if isinstance(value, dict):
            raw_ref = value.get("$ref")
            if raw_ref is not None:
                if not isinstance(raw_ref, str) or not raw_ref:
                    raise ContractError(f"{location}: $ref must be a nonempty string")
                target_path, fragment, target = resolve_ref(base_path, raw_ref, location)
                key = (target_path, fragment)
                if key in visiting_refs:
                    return
                if key not in resolved_refs:
                    visiting_refs.add(key)
                    walk(target, target_path, f"{target_path}#{fragment}")
                    visiting_refs.remove(key)
                    resolved_refs.add(key)
            for child_key, child in value.items():
                if child_key == "$ref":
                    continue
                walk(child, base_path, f"{location}/{child_key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, base_path, f"{location}/{index}")

    walk(spec, openapi_path.resolve(), str(openapi_path))


def _semantic_operation_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _semantic_operation_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_semantic_operation_value(item) for item in value]
    return value


def _semantic_openapi_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _semantic_openapi_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_semantic_openapi_value(item) for item in value]
    return value


def _operation_surface(operation: dict[str, Any]) -> dict[str, Any]:
    response_surface: dict[str, Any] = {}
    for status, response in sorted(operation.get("responses", {}).items()):
        if "$ref" in response:
            response_surface[status] = {"$ref": response["$ref"]}
            continue
        headers = response.get("headers", {})
        content = response.get("content", {})
        response_surface[status] = {
            "headers": {
                name: _semantic_operation_value(value)
                for name, value in sorted(headers.items())
            },
            "content": {
                media_type: _semantic_operation_value(
                    {"schema": media.get("schema")}
                )
                for media_type, media in sorted(content.items())
            },
        }
    return {
        "operationId": operation.get("operationId"),
        "parameters": _semantic_operation_value(operation.get("parameters", [])),
        "requestBody": _semantic_operation_value(operation.get("requestBody")),
        "responses": response_surface,
        "security": operation.get("security"),
        "extensions": _semantic_operation_value(
            {
                key: value
                for key, value in sorted(operation.items())
                if key.startswith("x-")
            }
        ),
    }


def _get_path_value(value: Any, path: tuple[str, ...], *, location: str) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict) or key not in current:
            dotted_path = ".".join(path)
            raise ContractError(f"{location}: expected field {dotted_path}")
        current = current[key]
    return current


def _replace_path_value(
    value: Any, path: tuple[str, ...], replacement: Any, *, location: str
) -> Any:
    clone = deepcopy(value)
    current = clone
    for key in path[:-1]:
        if not isinstance(current, dict) or key not in current:
            dotted_path = ".".join(path)
            raise ContractError(f"{location}: expected field {dotted_path}")
        current = current[key]
    if not isinstance(current, dict) or path[-1] not in current:
        dotted_path = ".".join(path)
        raise ContractError(f"{location}: expected field {dotted_path}")
    current[path[-1]] = replacement
    return clone


def _normalize_server_paths(
    servers: Any, *, expected_path: str, replacement_path: str, location: str
) -> list[Any]:
    normalized = deepcopy(_semantic_openapi_value(servers))
    if not isinstance(normalized, list):
        raise ContractError(f"{location}: expected a server array")
    for index, server in enumerate(normalized):
        server_object = require_mapping(server, f"{location}[{index}]")
        url = server_object.get("url")
        if not isinstance(url, str):
            raise ContractError(f"{location}[{index}]: expected URL string")
        parsed = urlsplit(url)
        if parsed.path != expected_path or parsed.query or parsed.fragment:
            raise ContractError(
                f"{location}[{index}]: expected URL path {expected_path} "
                "without query or fragment"
            )
        path_index = url.find(expected_path)
        if path_index == -1:
            raise ContractError(f"{location}[{index}]: expected URL path {expected_path}")
        server_object["url"] = (
            f"{url[:path_index]}{replacement_path}"
            f"{url[path_index + len(expected_path):]}"
        )
    return normalized


def _validate_v4_global_delta(v3: dict[str, Any], v4: dict[str, Any]) -> None:
    ignored_top_level = {"components", "info", "paths", "servers"}
    if set(v3) != set(v4):
        raise ContractError(
            "Device API v4 top-level fields must match v3 except explicit "
            f"version/path deltas; missing={sorted(set(v3) - set(v4))}; "
            f"unknown={sorted(set(v4) - set(v3))}"
        )

    v3_info = require_mapping(
        _semantic_openapi_value(v3.get("info")), "Device API v3 info"
    )
    v4_info = require_mapping(
        _semantic_openapi_value(v4.get("info")), "Device API v4 info"
    )
    if v3_info.get("version") != "3.0.0" or v4_info.get("version") != "4.0.0":
        raise ContractError(
            "Device API v4 info.version delta must be exactly 3.0.0 -> 4.0.0"
        )
    v4_info_as_v3 = deepcopy(v4_info)
    v4_info_as_v3["version"] = "3.0.0"
    if v3_info != v4_info_as_v3:
        raise ContractError(
            "Device API v4 info fields drifted from v3 outside info.version"
        )

    v3_servers = _normalize_server_paths(
        v3.get("servers"),
        expected_path="/api/v3",
        replacement_path="/api/v3",
        location="Device API v3 servers",
    )
    v4_servers_as_v3 = _normalize_server_paths(
        v4.get("servers"),
        expected_path="/api/v4",
        replacement_path="/api/v3",
        location="Device API v4 servers",
    )
    if v3_servers != v4_servers_as_v3:
        raise ContractError(
            "Device API v4 servers drifted from v3 outside /api/v3 -> /api/v4"
        )

    v3_profiles = _semantic_openapi_value(v3.get("x-ylx-security-profiles"))
    v4_profiles_as_v3 = _semantic_openapi_value(v4.get("x-ylx-security-profiles"))
    if not isinstance(v3_profiles, dict) or not isinstance(v4_profiles_as_v3, dict):
        raise ContractError("Device API v4 security profiles must be objects")
    v4_lab = require_mapping(
        v4_profiles_as_v3.get("lab"), "Device API v4 security profiles.lab"
    )
    v4_allowed = v4_lab.get("allowed_operation_ids")
    if not isinstance(v4_allowed, list):
        raise ContractError("Device API v4 lab.allowed_operation_ids must be an array")
    allowed_extra = ["getCameraFocus", "setCameraFocus"]
    if sorted(item for item in v4_allowed if item in allowed_extra) != allowed_extra:
        raise ContractError(
            "Device API v4 lab.allowed_operation_ids must include camera focus operations"
        )
    v4_lab["allowed_operation_ids"] = [
        item for item in v4_allowed if item not in allowed_extra
    ]
    if v3_profiles != v4_profiles_as_v3:
        raise ContractError(
            "Device API v4 security profiles drifted from v3 outside camera focus "
            "lab allowed_operation_ids"
        )

    for key in sorted(set(v3) - ignored_top_level - {"x-ylx-security-profiles"}):
        if _semantic_openapi_value(v3.get(key)) != _semantic_openapi_value(v4.get(key)):
            raise ContractError(
                f"Device API v4 top-level field {key}: non-allowlisted global drift"
            )


def _v4_paths_as_v3_for_delta(paths: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(paths)
    example_path = (
        "/capture/events",
        "get",
        "responses",
        "200",
        "content",
        "text/event-stream",
        "example",
    )
    example = _get_path_value(
        normalized,
        example_path,
        location="Device API v4 SSE example",
    )
    if not isinstance(example, str):
        raise ContractError("Device API v4 SSE example: expected string")
    _replace_target = normalized
    for segment in example_path[:-1]:
        _replace_target = _replace_target[segment]
    _replace_target[example_path[-1]] = example.replace(
        "ylx.capture-event.v4",
        "ylx.capture-event.v3",
    )
    return normalized


def _validate_v4_path_item_delta(v3: dict[str, Any], v4: dict[str, Any]) -> None:
    v3_paths = require_mapping(v3.get("paths"), "Device API v3 paths")
    v4_paths = require_mapping(v4.get("paths"), "Device API v4 paths")
    expected_v4_paths = set(v3_paths) | {"/camera/focus"}
    if set(v4_paths) != expected_v4_paths:
        raise ContractError(
            "Device API v4 paths must match v3 plus /camera/focus; "
            f"missing={sorted(expected_v4_paths - set(v4_paths))}; "
            f"unknown={sorted(set(v4_paths) - expected_v4_paths)}"
        )
    for path in sorted(v3_paths):
        v3_item = require_mapping(v3_paths[path], f"Device API v3 path {path}")
        v4_item = require_mapping(v4_paths[path], f"Device API v4 path {path}")
        v3_methods = {key for key in v3_item if key in OPENAPI_OPERATION_METHODS}
        v4_methods = {key for key in v4_item if key in OPENAPI_OPERATION_METHODS}
        if v3_methods != v4_methods:
            raise ContractError(
                f"Device API v4 path {path}: HTTP methods drifted from v3"
            )
        v3_non_operations = {
            key: value
            for key, value in v3_item.items()
            if key not in OPENAPI_OPERATION_METHODS
        }
        v4_non_operations = {
            key: value
            for key, value in v4_item.items()
            if key not in OPENAPI_OPERATION_METHODS
        }
        if _semantic_openapi_value(v3_non_operations) != _semantic_openapi_value(
            v4_non_operations
        ):
            raise ContractError(
                f"Device API v4 path {path}: path item metadata drifted from v3"
            )
    v4_common_paths = {
        path: value for path, value in v4_paths.items() if path in v3_paths
    }
    v4_paths_as_v3 = _v4_paths_as_v3_for_delta(v4_common_paths)
    if _semantic_openapi_value(v3_paths) != _semantic_openapi_value(v4_paths_as_v3):
        raise ContractError(
            "Device API v4 paths drifted from v3 outside the allowed SSE "
            "capture-event schema example identity delta"
        )


def _expected_raw_int16_vector3_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["x", "y", "z"],
        "properties": {
            "x": {"type": "integer", "minimum": -32768, "maximum": 32767},
            "y": {"type": "integer", "minimum": -32768, "maximum": 32767},
            "z": {"type": "integer", "minimum": -32768, "maximum": 32767},
        },
    }


def _expected_v3_live_imu_observation_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "session_id",
            "clock",
            "acceleration_m_s2",
            "angular_velocity_rad_s",
            "orientation_quaternion",
        ],
        "properties": {
            "session_id": {"$ref": "#/components/schemas/UuidV7"},
            "clock": {
                "type": "object",
                "additionalProperties": False,
                "required": ["time_base", "epoch_id", "timestamp_ns"],
                "properties": {
                    "time_base": {"const": "session_monotonic"},
                    "epoch_id": {
                        "$ref": "#/components/schemas/UuidV7",
                        "description": "Must equal the enclosing live IMU session_id",
                    },
                    "timestamp_ns": {"type": "integer", "minimum": 0},
                },
            },
            "acceleration_m_s2": {"$ref": "#/components/schemas/Vector3"},
            "angular_velocity_rad_s": {"$ref": "#/components/schemas/Vector3"},
            "orientation_quaternion": {
                "type": "object",
                "additionalProperties": False,
                "required": ["w", "x", "y", "z"],
                "properties": {
                    "w": {"type": "number"},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "z": {"type": "number"},
                },
            },
        },
    }


def _expected_v4_live_imu_observation_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["session_id", "clock", "raw", "sync"],
        "properties": {
            "session_id": {"$ref": "#/components/schemas/UuidV7"},
            "clock": {
                "type": "object",
                "additionalProperties": False,
                "required": ["time_base", "timestamp_ns"],
                "properties": {
                    "time_base": {"const": "host_monotonic"},
                    "timestamp_ns": {"type": "integer", "minimum": 0},
                },
            },
            "raw": {
                "type": "object",
                "additionalProperties": False,
                "required": ["units", "accelerometer", "gyroscope"],
                "properties": {
                    "units": {"const": "raw_int16"},
                    "accelerometer": {"$ref": "#/components/schemas/RawInt16Vector3"},
                    "gyroscope": {"$ref": "#/components/schemas/RawInt16Vector3"},
                },
            },
            "sync": {
                "type": "object",
                "additionalProperties": False,
                "required": ["quality"],
                "properties": {
                    "quality": {"enum": ["insufficient", "degraded", "good"]}
                },
            },
        },
    }


def _expected_v4_camera_focus_status_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema",
            "value",
            "minimum",
            "maximum",
            "step",
            "default",
            "auto_supported",
            "auto_enabled",
        ],
        "properties": {
            "schema": {"const": "ylx.camera-focus.v1"},
            "value": {
                "type": "integer",
                "minimum": 0,
                "description": "Current V4L2 focus_absolute value",
            },
            "minimum": {
                "type": "integer",
                "minimum": 0,
                "description": "Minimum accepted focus_absolute value reported by the device",
            },
            "maximum": {
                "type": "integer",
                "minimum": 0,
                "description": "Maximum accepted focus_absolute value reported by the device",
            },
            "step": {
                "type": "integer",
                "minimum": 1,
                "description": "V4L2 focus_absolute increment",
            },
            "default": {
                "type": "integer",
                "minimum": 0,
                "description": "Device-reported default focus_absolute value",
            },
            "auto_supported": {
                "type": "boolean",
                "description": "Whether V4L2 focus_auto is exposed",
            },
            "auto_enabled": {
                "oneOf": [{"type": "boolean"}, {"type": "null"}],
                "description": "Current focus_auto state, or null when focus_auto is not exposed",
            },
        },
    }


def _expected_v4_camera_focus_set_request_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema"],
        "anyOf": [{"required": ["value"]}, {"required": ["auto_enabled"]}],
        "properties": {
            "schema": {"const": "ylx.camera-focus-set.v1"},
            "value": {
                "type": "integer",
                "minimum": 0,
                "description": "Requested focus_absolute value",
            },
            "auto_enabled": {
                "type": "boolean",
                "description": "Requested focus_auto state when the device exposes that control",
            },
        },
    }


def _expected_v4_focus_error_schema(code: str) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema", "error"],
        "properties": {
            "schema": {"const": "ylx.api-error.v2"},
            "error": {
                "type": "object",
                "additionalProperties": False,
                "required": ["code", "message", "request_id", "retryable"],
                "properties": {
                    "code": {"const": code},
                    "message": {"type": "string", "minLength": 1, "maxLength": 1024},
                    "request_id": {"type": "string", "format": "uuid"},
                    "retryable": {"const": False},
                    "details": {"type": "object", "additionalProperties": True},
                },
            },
        },
    }


def _expected_v4_device_runtime_status_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "observed_at",
            "connection_method",
            "temperature_celsius",
            "network",
            "live_imu",
            "camera_focus",
        ],
        "properties": {
            "observed_at": {
                "type": "string",
                "format": "date-time",
                "description": "Timestamp at which all fields in this runtime observation were reconciled",
            },
            "connection_method": {
                "enum": [
                    "wifi_ap",
                    "wifi_client",
                    "ethernet_direct",
                    "ethernet_lan",
                    "offline",
                ],
                "description": "Current browser-to-device reachability mode, not an inferred preferred mode",
            },
            "temperature_celsius": {
                "type": "number",
                "minimum": -40,
                "maximum": 125,
                "description": "Current SoC thermal reading in degrees Celsius",
            },
            "network": {"$ref": "#/components/schemas/NetworkRuntimeStatus"},
            "live_imu": {
                "oneOf": [
                    {"$ref": "#/components/schemas/LiveImuObservation"},
                    {"type": "null"},
                ],
                "description": "Latest non-stale live sample, or null when no active session sample is available",
            },
            "camera_focus": {
                "oneOf": [
                    {"$ref": "#/components/schemas/CameraFocusStatus"},
                    {"type": "null"},
                ],
                "description": "Latest focus control status, or null when focus_absolute is not exposed",
            },
        },
    }


def _validate_component_const_only_delta(
    v3_schemas: dict[str, Any],
    v4_schemas: dict[str, Any],
    component: str,
    deltas: tuple[tuple[tuple[str, ...], Any, Any], ...],
) -> None:
    v3_schema = _semantic_openapi_value(v3_schemas[component])
    v4_schema_as_v3 = _semantic_openapi_value(v4_schemas[component])
    for path, expected_v3, expected_v4 in deltas:
        v3_value = _get_path_value(
            v3_schema, path, location=f"Device API v3 component {component}"
        )
        v4_value = _get_path_value(
            v4_schema_as_v3, path, location=f"Device API v4 component {component}"
        )
        if v3_value != expected_v3 or v4_value != expected_v4:
            dotted_path = ".".join(path)
            raise ContractError(
                f"Device API v4 component {component}: expected {dotted_path} "
                f"delta {expected_v3!r} -> {expected_v4!r}"
            )
        v4_schema_as_v3 = _replace_path_value(
            v4_schema_as_v3,
            path,
            expected_v3,
            location=f"Device API v4 component {component}",
        )
    if v3_schema != v4_schema_as_v3:
        raise ContractError(
            f"Device API v4 component {component}: drifted outside allowed "
            "schema/API version const delta"
        )


def _validate_v4_live_imu_schema_delta(
    v3_schemas: dict[str, Any], v4_schemas: dict[str, Any]
) -> None:
    v3_live_imu = _semantic_openapi_value(v3_schemas["LiveImuObservation"])
    v4_live_imu = _semantic_openapi_value(v4_schemas["LiveImuObservation"])
    if v3_live_imu != _expected_v3_live_imu_observation_schema():
        raise ContractError(
            "Device API v3 component LiveImuObservation: expected canonical "
            "session_monotonic SI/quaternion schema"
        )
    if v4_live_imu != _expected_v4_live_imu_observation_schema():
        raise ContractError(
            "Device API v4 component LiveImuObservation: expected canonical "
            "host_monotonic raw_int16/sync schema"
        )


def _validate_v4_component_delta(v3: dict[str, Any], v4: dict[str, Any]) -> None:
    v3_components = require_mapping(v3.get("components"), "Device API v3 components")
    v4_components = require_mapping(v4.get("components"), "Device API v4 components")
    if set(v3_components) != set(v4_components):
        raise ContractError(
            "Device API v4 components buckets must match v3; "
            f"missing={sorted(set(v3_components) - set(v4_components))}; "
            f"unknown={sorted(set(v4_components) - set(v3_components))}"
        )
    for bucket in sorted(set(v3_components) - {"schemas"}):
        if bucket == "responses":
            v3_responses = require_mapping(
                v3_components[bucket], "Device API v3 components.responses"
            )
            v4_responses = require_mapping(
                v4_components[bucket], "Device API v4 components.responses"
            )
            expected_names = set(v3_responses) | {
                "CameraFocusUnsupported",
                "InvalidCameraFocus",
            }
            if set(v4_responses) != expected_names:
                raise ContractError(
                    "Device API v4 components.responses must match v3 plus "
                    "CameraFocusUnsupported and InvalidCameraFocus"
                )
            common_v4_responses = {
                key: value for key, value in v4_responses.items() if key in v3_responses
            }
            if _semantic_openapi_value(v3_responses) != _semantic_openapi_value(
                common_v4_responses
            ):
                raise ContractError(
                    "Device API v4 components.responses: existing response bucket drifted"
                )
            expected_focus_responses = {
                "CameraFocusUnsupported": {
                    "description": "Device does not expose V4L2 focus_absolute and cannot report or set camera focus",
                    "headers": {
                        "YLX-Error-Code": {
                            "required": True,
                            "schema": {
                                "type": "string",
                                "const": "camera_focus_unsupported",
                            },
                        },
                    },
                    "content": {
                        "application/problem+json": {
                            "schema": {
                                "$ref": "#/components/schemas/CameraFocusUnsupportedError"
                            },
                        },
                    },
                },
                "InvalidCameraFocus": {
                    "description": "Requested focus value or auto toggle is outside the exposed V4L2 control range/capability",
                    "headers": {
                        "YLX-Error-Code": {
                            "required": True,
                            "schema": {
                                "type": "string",
                                "const": "invalid_camera_focus",
                            },
                        },
                    },
                    "content": {
                        "application/problem+json": {
                            "schema": {
                                "$ref": "#/components/schemas/InvalidCameraFocusError"
                            },
                        },
                    },
                },
            }
            for name, expected in expected_focus_responses.items():
                if _semantic_openapi_value(v4_responses[name]) != expected:
                    raise ContractError(
                        f"Device API v4 components.responses.{name}: focus response drifted"
                    )
            continue
        if _semantic_openapi_value(v3_components[bucket]) != _semantic_openapi_value(
            v4_components[bucket]
        ):
            raise ContractError(
                f"Device API v4 components.{bucket}: non-schema component bucket drifted"
            )

    v3_schemas = require_mapping(
        v3_components.get("schemas"), "Device API v3 components.schemas"
    )
    v4_schemas = require_mapping(
        v4_components.get("schemas"), "Device API v4 components.schemas"
    )
    expected_v4_names = set(v3_schemas) | {
        "CameraFocusSetRequest",
        "CameraFocusStatus",
        "CameraFocusUnsupportedError",
        "InvalidCameraFocusError",
        "RawInt16Vector3",
    }
    if set(v4_schemas) != expected_v4_names:
        raise ContractError(
            "Device API v4 component schema inventory must equal v3 plus "
            "raw Live IMU and camera focus schemas; "
            f"missing={sorted(expected_v4_names - set(v4_schemas))}; "
            f"unknown={sorted(set(v4_schemas) - expected_v4_names)}"
        )

    for component in sorted(set(v3_schemas) - V4_SCHEMA_DELTA_ALLOWLIST):
        if _semantic_openapi_value(v3_schemas[component]) != _semantic_openapi_value(
            v4_schemas[component]
        ):
            raise ContractError(
                f"Device API v4 component {component}: non-allowlisted "
                "component drifted from v3"
            )

    raw_int16 = _semantic_openapi_value(v4_schemas["RawInt16Vector3"])
    if raw_int16 != _expected_raw_int16_vector3_schema():
        raise ContractError(
            "Device API v4 component RawInt16Vector3: expected canonical "
            "int16 x/y/z schema"
        )
    focus_status = _semantic_openapi_value(v4_schemas["CameraFocusStatus"])
    if focus_status != _expected_v4_camera_focus_status_schema():
        raise ContractError(
            "Device API v4 component CameraFocusStatus: expected canonical "
            "ylx.camera-focus.v1 schema"
        )
    focus_set = _semantic_openapi_value(v4_schemas["CameraFocusSetRequest"])
    if focus_set != _expected_v4_camera_focus_set_request_schema():
        raise ContractError(
            "Device API v4 component CameraFocusSetRequest: expected canonical "
            "ylx.camera-focus-set.v1 schema"
        )
    if _semantic_openapi_value(v4_schemas["CameraFocusUnsupportedError"]) != (
        _expected_v4_focus_error_schema("camera_focus_unsupported")
    ):
        raise ContractError(
            "Device API v4 component CameraFocusUnsupportedError: expected typed "
            "camera_focus_unsupported problem"
        )
    if _semantic_openapi_value(v4_schemas["InvalidCameraFocusError"]) != (
        _expected_v4_focus_error_schema("invalid_camera_focus")
    ):
        raise ContractError(
            "Device API v4 component InvalidCameraFocusError: expected typed "
            "invalid_camera_focus problem"
        )
    if _semantic_openapi_value(v4_schemas["DeviceRuntimeStatus"]) != (
        _expected_v4_device_runtime_status_schema()
    ):
        raise ContractError(
            "Device API v4 component DeviceRuntimeStatus: expected canonical "
            "runtime with live_imu and camera_focus nullable observations"
        )

    _validate_component_const_only_delta(
        v3_schemas,
        v4_schemas,
        "DeviceDescriptor",
        (
            (("properties", "schema", "const"), "ylx.device.v3", "ylx.device.v4"),
            (("properties", "api_version", "const"), "3.0", "4.0"),
        ),
    )
    _validate_component_const_only_delta(
        v3_schemas,
        v4_schemas,
        "CaptureStatusSnapshot",
        (
            (
                ("properties", "schema", "const"),
                "ylx.capture-status.v2",
                "ylx.capture-status.v4",
            ),
        ),
    )
    _validate_component_const_only_delta(
        v3_schemas,
        v4_schemas,
        "CaptureEvent",
        (
            (
                ("properties", "schema", "const"),
                "ylx.capture-event.v3",
                "ylx.capture-event.v4",
            ),
        ),
    )
    _validate_component_const_only_delta(
        v3_schemas,
        v4_schemas,
        "CaptureSnapshotEventData",
        (
            (
                ("properties", "schema", "const"),
                "ylx.capture-snapshot-event.v2",
                "ylx.capture-snapshot-event.v4",
            ),
        ),
    )
    _validate_v4_live_imu_schema_delta(v3_schemas, v4_schemas)


def _operation_by_id(spec: dict[str, Any], operation_id: str) -> dict[str, Any]:
    matches = [
        operation
        for _, _, operation in api_operations(spec)
        if operation.get("operationId") == operation_id
    ]
    if len(matches) != 1:
        raise ContractError(
            f"Device API operationId {operation_id}: expected exactly one operation"
        )
    return matches[0]


def _validate_v4_sse_example_delta(v3: dict[str, Any], v4: dict[str, Any]) -> None:
    def sse_example(spec: dict[str, Any], version: str) -> str:
        operation = _operation_by_id(spec, "streamCaptureEvents")
        example = (
            operation.get("responses", {})
            .get("200", {})
            .get("content", {})
            .get("text/event-stream", {})
            .get("example")
        )
        if not isinstance(example, str):
            raise ContractError(f"Device API {version} SSE example: expected string")
        return example

    v3_example = sse_example(v3, "v3")
    v4_example = sse_example(v4, "v4")
    if "ylx.capture-event.v3" not in v3_example:
        raise ContractError(
            "Device API v3 SSE example: expected ylx.capture-event.v3 discriminator"
        )
    if "ylx.capture-event.v4" not in v4_example:
        raise ContractError(
            "Device API v4 SSE example: expected ylx.capture-event.v4 discriminator"
        )
    if v4_example.replace("ylx.capture-event.v4", "ylx.capture-event.v3") != v3_example:
        raise ContractError(
            "Device API v4 SSE example drifted outside capture-event v3 -> v4 "
            "schema text delta"
        )


def _expected_v4_focus_path_item() -> dict[str, Any]:
    return {
        "get": {
            "tags": ["Device"],
            "operationId": "getCameraFocus",
            "summary": "Read current V4L2 camera focus controls",
            "description": (
                "Returns the latest reconciled focus_absolute/focus_auto observation. Devices\n"
                "that do not expose focus_absolute return the typed camera_focus_unsupported\n"
                "problem; status snapshots instead carry runtime.camera_focus as null.\n"
            ),
            "x-ylx-lab-access": "allowed",
            "security": [{"bearerAuth": []}],
            "responses": {
                "200": {
                    "description": "Current focus control state",
                    "headers": {
                        "Cache-Control": {
                            "schema": {
                                "type": "string",
                                "const": "no-store",
                            },
                        },
                    },
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/CameraFocusStatus",
                            },
                        },
                    },
                },
                "401": {"$ref": "#/components/responses/Unauthorized"},
                "403": {"$ref": "#/components/responses/Forbidden"},
                "404": {"$ref": "#/components/responses/CameraFocusUnsupported"},
                "500": {"$ref": "#/components/responses/InternalError"},
            },
        },
        "post": {
            "tags": ["Device"],
            "operationId": "setCameraFocus",
            "summary": "Idempotently set manual or automatic camera focus",
            "description": (
                "Applies a focus_absolute value, toggles focus_auto when exposed, or both.\n"
                "The response is the reconciled camera state after the accepted command.\n"
                "Omitting both value and auto_enabled is invalid. Setting auto_enabled is\n"
                "rejected with 422 when the device exposes focus_absolute but not focus_auto.\n"
            ),
            "x-ylx-lab-access": "allowed",
            "security": [{"bearerAuth": []}],
            "parameters": [
                {"$ref": "#/components/parameters/IdempotencyKey"},
            ],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "$ref": "#/components/schemas/CameraFocusSetRequest",
                        },
                    },
                },
            },
            "responses": {
                "200": {
                    "description": (
                        "Focus command accepted; response is the reconciled focus control state"
                    ),
                    "headers": {
                        "Idempotency-Replayed": {
                            "$ref": "#/components/headers/IdempotencyReplayed",
                        },
                    },
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/CameraFocusStatus",
                            },
                        },
                    },
                },
                "400": {"$ref": "#/components/responses/BadRequest"},
                "401": {"$ref": "#/components/responses/Unauthorized"},
                "403": {"$ref": "#/components/responses/Forbidden"},
                "404": {"$ref": "#/components/responses/CameraFocusUnsupported"},
                "409": {"$ref": "#/components/responses/Conflict"},
                "422": {"$ref": "#/components/responses/InvalidCameraFocus"},
                "500": {"$ref": "#/components/responses/InternalError"},
            },
        },
    }


def _validate_v4_focus_operations(v4: dict[str, Any]) -> None:
    paths = require_mapping(v4.get("paths"), "Device API v4 paths")
    focus = require_mapping(paths.get("/camera/focus"), "Device API v4 /camera/focus")
    expected_focus = _expected_v4_focus_path_item()
    if set(focus) != set(expected_focus):
        raise ContractError("Device API v4 /camera/focus must expose exactly GET and POST")
    for method in ("get", "post"):
        operation = require_mapping(
            focus.get(method),
            f"Device API v4 {method.upper()} /camera/focus",
        )
        if operation != expected_focus[method]:
            raise ContractError(
                f"Device API v4 {method.upper()} /camera/focus exact operation drifted"
            )


def validate_v4_openapi_delta_against_v3(
    v3: dict[str, Any],
    v4: dict[str, Any],
) -> None:
    """Ensure Device API v4 keeps the full v3 contract surface.

    v4 changes the version identity and Live IMU schema semantics. It must not
    shrink HTTP/SSE/Range/HEAD/safe-swap/session operation structure or mutate
    non-allowlisted components while doing so.
    """

    _validate_v4_global_delta(v3, v4)
    _validate_v4_path_item_delta(v3, v4)
    _validate_v4_component_delta(v3, v4)
    _validate_v4_sse_example_delta(v3, v4)

    v3_operations = {
        operation["operationId"]: (path, method, operation)
        for path, method, operation in api_operations(v3)
    }
    v4_operations = {
        operation["operationId"]: (path, method, operation)
        for path, method, operation in api_operations(v4)
    }
    allowed_extra_operations = {"getCameraFocus", "setCameraFocus"}
    if set(v4_operations) != set(v3_operations) | allowed_extra_operations:
        raise ContractError(
            "Device API v4 operationId inventory must match v3 plus camera focus; "
            f"missing={sorted((set(v3_operations) | allowed_extra_operations) - set(v4_operations))}; "
            f"unknown={sorted(set(v4_operations) - (set(v3_operations) | allowed_extra_operations))}"
        )
    for operation_id in sorted(v3_operations):
        v3_path, v3_method, v3_operation = v3_operations[operation_id]
        v4_path, v4_method, v4_operation = v4_operations[operation_id]
        if (v3_path, v3_method) != (v4_path, v4_method):
            raise ContractError(
                f"Device API v4 {operation_id}: path/method drifted from "
                f"{v3_method.upper()} {v3_path} to {v4_method.upper()} {v4_path}"
            )
        v3_surface = _operation_surface(v3_operation)
        v4_surface = _operation_surface(v4_operation)
        if v3_surface != v4_surface:
            raise ContractError(
                f"Device API v4 {operation_id}: operation surface drifted from v3 "
                "(parameters, request body, response codes, headers, content schemas, "
                "security, or x-* extensions)"
            )
    _validate_v4_focus_operations(v4)


def validate_openapi_authority_boundaries(
    spec: dict[str, Any],
    persisted_schemas: dict[str, tuple[str, dict[str, Any]]],
) -> None:
    boundary = require_mapping(
        spec.get("x-ylx-external-authority-boundaries"),
        f"{OPENAPI}: x-ylx-external-authority-boundaries",
    )
    schema_entry = persisted_schemas.get(EXTERNAL_AUTHORITY_DISCRIMINATOR)
    if schema_entry is None:
        raise ContractError(
            f"{OPENAPI}: external authority boundary schema is not indexed"
        )
    _, schema = schema_entry
    errors = list(
        Draft202012Validator(schema, format_checker=FORMAT_CHECKER).iter_errors(boundary)
    )
    if errors:
        raise ContractError(
            f"{OPENAPI}: external authority boundary is invalid\n{format_errors(errors)}"
        )
    golden_boundary = require_mapping(
        load_json(EXTERNAL_AUTHORITY_FIXTURE), str(EXTERNAL_AUTHORITY_FIXTURE)
    )
    if boundary != golden_boundary:
        raise ContractError(
            f"{OPENAPI}: external authority boundary must exactly match "
            f"{EXTERNAL_AUTHORITY_FIXTURE}"
        )


def validate_openapi_operation_invariants(spec: dict[str, Any]) -> None:
    profiles = spec["x-ylx-security-profiles"]
    if profiles["customer"]["authentication"] != "bearer":
        raise ContractError("customer security profile must require bearer authentication")
    if profiles["customer"].get("standard_openapi_security") != "bearer-only-authority":
        raise ContractError("standard OpenAPI security must be Customer bearer-only authority")
    if profiles["customer"].get("network_mutation") != "not-enabled":
        raise ContractError("customer network mutation must remain disabled until its product API is implemented")
    if profiles["customer"].get("destructive_mutation") != "not-enabled":
        raise ContractError("customer destructive mutation must remain target-disabled")
    if profiles["lab"]["authentication"] != "none":
        raise ContractError("lab security profile must be unauthenticated")
    if (
        profiles["lab"].get("standard_openapi_resolution")
        != "explicit-lab-profile-artifact-required"
    ):
        raise ContractError("Lab anonymous access must require an explicit resolved profile artifact")
    for capability in ("destructive_mutation", "network_mutation"):
        if profiles["lab"].get(capability) != "unreachable-or-403":
            raise ContractError(f"lab {capability} must be unreachable or rejected with 403")
    components = spec["components"]
    schemes = components["securitySchemes"]
    if "bearerAuth" not in schemes or "labAccess" in schemes:
        raise ContractError("security schemes must retain bearerAuth and omit labAccess")

    operations = api_operations(spec)
    expected_operations = {
        ("/device", "get"): "getDevice",
        ("/capture/status", "get"): "getCaptureStatus",
        ("/capture/start", "post"): "startCapture",
        ("/capture/stop", "post"): "stopCapture",
        ("/capture/events", "get"): "streamCaptureEvents",
        ("/capture/safe-swap", "get"): "getCurrentSafeSwapReceipt",
        ("/preview", "get"): "getPreview",
        ("/sessions", "get"): "listSessions",
        ("/sessions/{session_id}", "get"): "getSession",
        (
            "/sessions/{session_id}/unsuccessful-outcome",
            "get",
        ): "getRetainedUnsuccessfulSessionOutcome",
        (
            "/sessions/{session_id}/artifacts/{artifact_id}",
            "get",
        ): "getSessionArtifact",
        (
            "/sessions/{session_id}/artifacts/{artifact_id}",
            "head",
        ): "headSessionArtifact",
    }
    actual_operations = {
        (path, method): operation.get("operationId")
        for path, method, operation in operations
    }
    if actual_operations != expected_operations:
        raise ContractError(
            "OpenAPI operation surface or operationId values differ from current Device API v3"
        )
    operation_ids = list(actual_operations.values())
    if len(operation_ids) != len(set(operation_ids)):
        raise ContractError("OpenAPI operationId values must be unique")
    if any(method == "delete" for _, method, _ in operations):
        raise ContractError("DELETE operations are target-disabled pending STORE-DELETE-01")
    mutation_methods = {"put", "post", "delete", "patch"}
    if any(
        method in mutation_methods
        and ("network" in path.casefold() or "network" in operation["operationId"].casefold())
        for path, method, operation in operations
    ):
        raise ContractError("network mutation is not enabled by the current Device API policy")
    capabilities = components["schemas"]["DeviceDescriptor"]["properties"]["capabilities"]
    if "delete_session" in capabilities["properties"] or "delete_session" in capabilities["required"]:
        raise ContractError("DeviceDescriptor must not advertise unresolved session deletion")
    if capabilities["properties"]["network_mutation"].get("const") is not False:
        raise ContractError("network_mutation must remain false while the capability is target-disabled")

    declared_lab = set(profiles["lab"]["allowed_operation_ids"])
    actual_lab = {
        operation["operationId"]
        for _, _, operation in operations
        if operation.get("x-ylx-lab-access") == "allowed"
    }
    if actual_lab != declared_lab:
        raise ContractError("lab allowed_operation_ids do not match operation extensions")
    expected_security = [{"bearerAuth": []}]
    for path, method, operation in operations:
        if operation.get("security") != expected_security:
            raise ContractError(
                f"{method.upper()} {path}: standard OpenAPI security must be exactly bearer-only"
            )
        forbidden_response = (
            "#/components/responses/ForbiddenHead"
            if method == "head"
            else "#/components/responses/Forbidden"
        )
        if operation.get("responses", {}).get("403", {}).get("$ref") != forbidden_response:
            raise ContractError(
                f"{method.upper()} {path}: 403 must use the canonical authorization response"
            )

    paths = spec["paths"]
    for command_path in ("/capture/start", "/capture/stop"):
        command = paths[command_path]["post"]
        parameter_refs = [
            parameter.get("$ref")
            for parameter in command.get("parameters", [])
            if isinstance(parameter, dict)
        ]
        if parameter_refs != ["#/components/parameters/IdempotencyKey"]:
            raise ContractError(
                f"POST {command_path} must require exactly the shared Idempotency-Key parameter"
            )
        if command["responses"].get("409", {}).get("$ref") != "#/components/responses/Conflict":
            raise ContractError(f"POST {command_path} must expose the shared 409 Conflict")

    expected_status_ref = "#/components/schemas/CaptureStatusSnapshot"
    capture_status_responses = paths["/capture/status"]["get"]["responses"]
    if "404" in capture_status_responses:
        raise ContractError(
            "GET /capture/status must represent ordinary idle as a 200 snapshot, not 404"
        )
    authority_snapshot_responses = (
        ("GET /capture/status", capture_status_responses["200"]),
        ("POST /capture/start", paths["/capture/start"]["post"]["responses"]["202"]),
        ("POST /capture/stop", paths["/capture/stop"]["post"]["responses"]["202"]),
    )
    for name, response in authority_snapshot_responses:
        actual_ref = (
            response.get("content", {})
            .get("application/json", {})
            .get("schema", {})
            .get("$ref")
        )
        if actual_ref != expected_status_ref:
            raise ContractError(
                f"{name} must return the shared authority-bearing CaptureStatusSnapshot"
            )

    events = paths["/capture/events"]["get"]
    if events.get("x-sse-data-schema", {}).get("$ref") != "#/components/schemas/CaptureEvent":
        raise ContractError("capture SSE must bind CaptureEvent")
    event_parameter_refs = [
        parameter.get("$ref")
        for parameter in events.get("parameters", [])
        if isinstance(parameter, dict)
    ]
    if event_parameter_refs != ["#/components/parameters/LastEventId"]:
        raise ContractError("capture SSE must accept exactly the shared Last-Event-ID parameter")
    last_event_id = components["parameters"].get("LastEventId", {})
    if not (
        last_event_id.get("name") == "Last-Event-ID"
        and last_event_id.get("in") == "header"
        and last_event_id.get("required") is False
        and last_event_id.get("schema", {}).get("type") == "string"
        and last_event_id.get("schema", {}).get("pattern") == "^[0-9]+$"
    ):
        raise ContractError("Last-Event-ID must be an optional decimal SSE delivery header")
    event_responses = events["responses"]
    event_200 = event_responses.get("200", {})
    if set(event_200.get("content", {})) != {"text/event-stream"}:
        raise ContractError("capture SSE 200 must expose only text/event-stream")
    event_headers = event_200.get("headers", {})
    if not (
        event_headers.get("Cache-Control", {}).get("schema", {}).get("const") == "no-cache"
        and event_headers.get("X-Accel-Buffering", {}).get("schema", {}).get("const") == "no"
    ):
        raise ContractError("capture SSE must disable caches and proxy buffering")
    if event_responses.get("401", {}).get("$ref") != "#/components/responses/Unauthorized":
        raise ContractError("capture SSE 401 must use the Unauthorized response")
    if event_responses.get("500", {}).get("$ref") != "#/components/responses/InternalError":
        raise ContractError("capture SSE 500 must use the InternalError response")

    schemas = components["schemas"]
    event_schema = schemas["CaptureEvent"]
    expected_event_required = {
        "schema",
        "sse_delivery_id",
        "authority_epoch",
        "source_revision",
        "type",
        "occurred_at",
        "session_id",
        "data",
    }
    if event_schema.get("additionalProperties") is not False or set(
        event_schema.get("required", [])
    ) != expected_event_required:
        raise ContractError("CaptureEvent must be closed with its exact required field set")
    event_properties = event_schema["properties"]
    if not (
        event_properties["schema"].get("const") == "ylx.capture-event.v3"
        and event_properties["sse_delivery_id"].get("type") == "string"
        and event_properties["sse_delivery_id"].get("pattern") == "^[0-9]+$"
        and event_properties["authority_epoch"].get("$ref")
        == "#/components/schemas/UuidV4"
        and event_properties["source_revision"].get("type") == "integer"
        and event_properties["source_revision"].get("minimum") == 0
        and event_properties["data"].get("type") == "object"
    ):
        raise ContractError(
            "CaptureEvent delivery identity, source authority, revision, or data field structure drifted"
        )
    expected_event_payloads = {
        "snapshot": "#/components/schemas/CaptureSnapshotEventData",
        "state": "#/components/schemas/CaptureStateEventData",
        "progress": "#/components/schemas/CaptureProgressEventData",
        "diagnostic": "#/components/schemas/CaptureDiagnosticEventData",
        "safe_swap": "#/components/schemas/SafeSwapReceiptV3",
    }
    if set(event_properties["type"].get("enum", [])) != set(
        expected_event_payloads
    ):
        raise ContractError("CaptureEvent type set is incomplete or contains unknown events")
    actual_event_payloads: dict[Any, Any] = {}
    event_session_refs: dict[Any, Any] = {}
    for condition in event_schema.get("allOf", []):
        event_type = condition.get("if", {}).get("properties", {}).get("type", {}).get("const")
        if event_type in actual_event_payloads:
            raise ContractError(f"CaptureEvent has duplicate payload branch for {event_type!r}")
        then_properties = condition.get("then", {}).get("properties", {})
        payload_schema = then_properties.get("data", {})
        actual_event_payloads[event_type] = (
            {ref.get("$ref") for ref in payload_schema.get("oneOf", [])}
            if "oneOf" in payload_schema
            else payload_schema.get("$ref")
        )
        event_session_refs[event_type] = then_properties.get("session_id", {}).get("$ref")
    if actual_event_payloads != expected_event_payloads:
        raise ContractError("every CaptureEvent type must select its exact typed payload schema")
    for event_type in ("state", "progress", "safe_swap"):
        if event_session_refs.get(event_type) != "#/components/schemas/UuidV7":
            raise ContractError(f"CaptureEvent {event_type} must require a non-null session_id")
    for component in (
        "CaptureSnapshotEventData",
        "CaptureStateEventData",
        "CaptureProgressEventData",
        "CaptureDiagnosticEventData",
        "SafeSwapReceiptV3",
    ):
        if schemas[component].get("additionalProperties") is not False:
            raise ContractError(f"{component} event payload must be closed")
    for component in (
        "CaptureStatusSnapshot",
        "CaptureSnapshotRecording",
        "RetainedUnsuccessfulSessionResource",
    ):
        if schemas[component].get("additionalProperties") is not False:
            raise ContractError(f"nested event payload {component} must be closed")
    status_schema = schemas["CaptureStatusSnapshot"]
    if set(status_schema.get("required", [])) != {
        "schema",
        "authority_epoch",
        "source_revision",
        "snapshot",
    } or status_schema.get("properties", {}).get("snapshot", {}).get("$ref") != (
        "#/components/schemas/CaptureSnapshotEventData"
    ):
        raise ContractError(
            "CaptureStatusSnapshot must expose the exact authority identity and shared snapshot payload"
        )
    snapshot_schema = schemas["CaptureSnapshotEventData"]
    if set(snapshot_schema.get("required", [])) != {
        "schema",
        "device_state",
        "active_recording",
        "retained_unsuccessful",
        "runtime",
    }:
        raise ContractError(
            "CaptureSnapshotEventData must separate active and retained unsuccessful state"
        )
    recording_state_ref = "../schemas/ylx-recording-state-v1.schema.json"
    if (
        schemas["CaptureSnapshotRecording"]
        .get("properties", {})
        .get("recording_state", {})
        .get("$ref")
        != recording_state_ref
    ):
        raise ContractError(
            "CaptureSnapshotRecording must reuse the complete persisted Recording State"
        )
    if schemas["CaptureDiagnostic"].get("$ref") != (
        f"{recording_state_ref}#/$defs/diagnostic"
    ):
        raise ContractError(
            "CaptureDiagnostic must reuse the exact persisted diagnostic interface"
        )
    expected_payload_discriminators = {
        "CaptureSnapshotEventData": "ylx.capture-snapshot-event.v2",
        "CaptureStateEventData": "ylx.capture-state-event.v2",
        "CaptureProgressEventData": "ylx.capture-progress-event.v2",
        "CaptureDiagnosticEventData": "ylx.capture-diagnostic-event.v2",
        "SafeSwapReceiptV3": "ylx.safe-swap-receipt.v3",
    }
    for component, discriminator in expected_payload_discriminators.items():
        if schemas[component].get("properties", {}).get("schema", {}).get("const") != discriminator:
            raise ContractError(f"{component} event payload discriminator drifted")

    retained_path = paths["/sessions/{session_id}/unsuccessful-outcome"]
    retained_parameter_refs = [
        parameter.get("$ref")
        for parameter in retained_path.get("parameters", [])
        if isinstance(parameter, dict)
    ]
    if retained_parameter_refs != ["#/components/parameters/SessionId"]:
        raise ContractError(
            "retained unsuccessful outcome path must bind exactly SessionId"
        )
    retained_get = retained_path["get"]
    retained_200 = retained_get["responses"].get("200", {})
    retained_ref = (
        retained_200.get("content", {})
        .get("application/json", {})
        .get("schema", {})
        .get("$ref")
    )
    if retained_ref != "#/components/schemas/RetainedUnsuccessfulSessionResource":
        raise ContractError(
            "retained unsuccessful outcome query must return its typed resource"
        )
    for status in ("200", "404"):
        cache_control = (
            retained_get["responses"]
            .get(status, {})
            .get("headers", {})
            .get("Cache-Control", {})
            .get("schema", {})
            .get("const")
        )
        if cache_control != "no-store":
            raise ContractError(
                f"retained unsuccessful outcome {status} must be Cache-Control: no-store"
            )

    safe_swap = paths["/capture/safe-swap"]["get"]
    if paths["/capture/safe-swap"].get("parameters") or safe_swap.get("parameters"):
        raise ContractError("GET /capture/safe-swap must be a parameterless current resource")
    for status in ("200", "404"):
        response = safe_swap["responses"].get(status)
        if response is None:
            raise ContractError(f"GET /capture/safe-swap must define {status}")
        cache_control = response.get("headers", {}).get("Cache-Control", {}).get("schema", {})
        if cache_control.get("const") != "no-store":
            raise ContractError(f"GET /capture/safe-swap {status} must be Cache-Control: no-store")
    safe_swap_200 = safe_swap["responses"]["200"]["content"]["application/json"]["schema"]
    if set(safe_swap_200) != {"$ref"} or safe_swap_200.get("$ref") != (
        "#/components/schemas/SafeSwapReceiptResourceV3"
    ):
        raise ContractError(
            "GET /capture/safe-swap 200 must return only the current v3 receipt resource"
        )

    for component in (
        "SafeSwapReceiptV3",
        "SafeSwapReceiptResourceV3",
    ):
        if schemas[component].get("additionalProperties") is not False:
            raise ContractError(f"{component} must be closed")
    v3_receipt_schema = schemas["SafeSwapReceiptV3"]
    v3_receipt_fields = {
        "schema",
        "session_id",
        "volume_id",
        "generation_id",
        "manifest_id",
        "manifest_sha256",
        "sealed_at",
        "released_at",
        "release_state",
        "open_handle_count",
    }
    if (
        v3_receipt_schema.get("additionalProperties") is not False
        or set(v3_receipt_schema.get("required", [])) != v3_receipt_fields
        or set(v3_receipt_schema.get("properties", [])) != v3_receipt_fields
        or v3_receipt_schema["properties"]["schema"].get("const")
        != "ylx.safe-swap-receipt.v3"
        or v3_receipt_schema["properties"]["open_handle_count"].get("const") != 0
    ):
        raise ContractError(
            "SafeSwapReceiptV3 must expose only its minimal closed receipt field set"
        )
    v3_resource_schema = schemas["SafeSwapReceiptResourceV3"]
    if (
        v3_resource_schema.get("additionalProperties") is not False
        or set(v3_resource_schema.get("required", [])) != {"schema", "receipt"}
        or v3_resource_schema["properties"]["schema"].get("const")
        != "ylx.safe-swap-receipt-resource.v3"
        or v3_resource_schema["properties"]["receipt"].get("$ref")
        != "#/components/schemas/SafeSwapReceiptV3"
    ):
        raise ContractError("SafeSwapReceiptResourceV3 must wrap SafeSwapReceiptV3 exactly")
    expected_diagnostic_codes = {
        "manifest_unreadable",
        "unsupported_schema",
        "manifest_invalid",
        "manifest_not_sealed",
    }
    diagnostic_schema = schemas["SessionDiscoveryDiagnostic"]
    if diagnostic_schema.get("additionalProperties") is not False:
        raise ContractError("SessionDiscoveryDiagnostic must be closed")
    actual_diagnostic_codes = set(diagnostic_schema["properties"]["code"].get("enum", []))
    if actual_diagnostic_codes != expected_diagnostic_codes:
        raise ContractError("SessionDiscoveryDiagnostic code set is incomplete or unknown")
    session_list_schema = schemas["SessionList"]
    if session_list_schema.get("additionalProperties") is not False or not {
        "schema",
        "items",
        "diagnostics",
        "next_cursor",
    } <= set(session_list_schema["required"]):
        raise ContractError("SessionList must require items, diagnostics, and next_cursor")
    limit_parameter = next(
        (
            parameter
            for parameter in paths["/sessions"]["get"].get("parameters", [])
            if isinstance(parameter, dict) and parameter.get("name") == "limit"
        ),
        {},
    )
    limit_schema = limit_parameter.get("schema", {})
    if not (
        limit_parameter.get("in") == "query"
        and limit_schema.get("type") == "integer"
        and limit_schema.get("minimum") == 1
        and limit_schema.get("maximum") == SESSION_LIST_LIMIT_MAXIMUM
        and limit_schema.get("default") == 50
        and "combined" in limit_parameter.get("description", "").casefold()
    ):
        raise ContractError(
            "SessionList limit must count combined items and diagnostics with integer "
            f"bounds 1..{SESSION_LIST_LIMIT_MAXIMUM} and default 50"
        )

    artifact = paths["/sessions/{session_id}/artifacts/{artifact_id}"]
    expected_representation_contract = {
        "enforcement": "procedural-custom-validator",
        "descriptor_source": "verified-device-session-manifest.descriptor-by-artifact-id",
        "content_type_equals": "descriptor.media_type",
        "etag_equals": "quoted-descriptor.sha256",
        "complete_content_length_equals": "descriptor.bytes",
        "partial_content_length_equals": "selected-inclusive-range.bytes",
        "applies_to": ["GET-200", "GET-206", "HEAD-200"],
    }
    if artifact.get("x-ylx-representation-contract") != expected_representation_contract:
        raise ContractError(
            "artifact path must declare the exact procedural descriptor-bound "
            "representation contract"
        )
    artifact_get = artifact["get"]
    artifact_head = artifact["head"]
    path_parameter_refs = [
        parameter.get("$ref")
        for parameter in artifact.get("parameters", [])
        if isinstance(parameter, dict)
    ]
    if path_parameter_refs != [
        "#/components/parameters/SessionId",
        "#/components/parameters/ArtifactId",
    ]:
        raise ContractError("artifact path must declare SessionId then ArtifactId exactly")
    get_parameter_refs = [
        parameter.get("$ref")
        for parameter in artifact_get.get("parameters", [])
        if isinstance(parameter, dict)
    ]
    if get_parameter_refs != [
        "#/components/parameters/Range",
        "#/components/parameters/IfRange",
    ]:
        raise ContractError("artifact GET must declare Range then If-Range exactly")
    if artifact_head.get("parameters"):
        raise ContractError("artifact HEAD must not accept Range or If-Range")
    range_parameter = components["parameters"].get("Range", {})
    if not (
        range_parameter.get("name") == "Range"
        and range_parameter.get("in") == "header"
        and range_parameter.get("required") is False
        and range_parameter.get("schema", {}).get("type") == "string"
        and range_parameter.get("schema", {}).get("pattern")
        == "^bytes=(?:[0-9]+-[0-9]*|-[0-9]+)$"
    ):
        raise ContractError("Range must retain the exact optional single-range header grammar")
    strong_etag_pattern = '^"[0-9a-f]{64}"$'
    if_range = components["parameters"].get("IfRange", {})
    if not (
        if_range.get("name") == "If-Range"
        and if_range.get("in") == "header"
        and if_range.get("required") is False
        and if_range.get("schema", {}).get("type") == "string"
        and if_range.get("schema", {}).get("pattern") == strong_etag_pattern
    ):
        raise ContractError(
            "If-Range must accept only a strong quoted lowercase SHA-256 ETag"
        )
    if components["headers"]["ETag"]["schema"].get("pattern") != strong_etag_pattern:
        raise ContractError("ETag must use the same strong quoted SHA-256 pattern")
    if components["headers"]["AcceptRanges"]["schema"].get("const") != "bytes":
        raise ContractError("Accept-Ranges must remain bytes")
    if (
        components["headers"]["ContentRange"]["schema"].get("pattern")
        != "^bytes [0-9]+-[0-9]+/[0-9]+$"
    ):
        raise ContractError("Content-Range must retain the satisfied single-range grammar")

    get_responses = artifact_get["responses"]
    if set(get_responses) != {"200", "206", "401", "403", "404", "409", "416", "423", "500"}:
        raise ContractError("artifact GET response status set differs from the exact contract")
    unsatisfied_range = get_responses["416"].get("headers", {}).get("Content-Range", {})
    if unsatisfied_range.get("schema", {}).get("pattern") != "^bytes \\*/[0-9]+$":
        raise ContractError("artifact GET 416 must report the complete length with Content-Range")
    get_200_headers = get_responses["200"].get("headers", {})
    head_responses = artifact_head["responses"]
    head_200 = head_responses.get("200", {})
    expected_complete_headers = {
        "Accept-Ranges",
        "Content-Length",
        "Content-Type",
        "ETag",
    }
    if set(get_200_headers) != expected_complete_headers or head_200.get(
        "headers"
    ) != get_200_headers:
        raise ContractError("artifact HEAD headers must exactly match complete GET headers")
    if "content" in head_200:
        raise ContractError("artifact HEAD 200 must not define a response body")
    expected_head_refs = {
        "401": "#/components/responses/UnauthorizedHead",
        "403": "#/components/responses/ForbiddenHead",
        "409": "#/components/responses/SessionNotVerifiedHead",
        "423": "#/components/responses/CaptureBusyHead",
        "404": "#/components/responses/NotFoundHead",
        "500": "#/components/responses/InternalErrorHead",
    }
    if set(head_responses) != {"200", *expected_head_refs}:
        raise ContractError("artifact HEAD response status set differs from the exact contract")
    for status, expected_ref in expected_head_refs.items():
        if head_responses[status].get("$ref") != expected_ref:
            raise ContractError(f"artifact HEAD {status} must use {expected_ref}")
    for status, response in head_responses.items():
        resolved = response
        reference = response.get("$ref") if isinstance(response, dict) else None
        if isinstance(reference, str) and reference.startswith("#/components/responses/"):
            resolved = components["responses"].get(reference.rsplit("/", 1)[-1], {})
        if not isinstance(resolved, dict) or "content" in resolved:
            raise ContractError(f"artifact HEAD {status} must resolve to a bodyless response")

    if artifact_get["responses"].get("409", {}).get("$ref") != "#/components/responses/SessionNotVerified":
        raise ContractError("artifact GET must define stable SessionNotVerified 409")
    if head_responses.get("409", {}).get("$ref") != "#/components/responses/SessionNotVerifiedHead":
        raise ContractError("artifact HEAD must define bodyless SessionNotVerifiedHead 409")
    if not (
        artifact_get["responses"].get("423", {}).get("$ref")
        == "#/components/responses/CaptureBusy"
        and head_responses.get("423", {}).get("$ref")
        == "#/components/responses/CaptureBusyHead"
    ):
        raise ContractError("artifact GET/HEAD must expose typed 423 capture_busy responses")
    get_error_code = schemas["SessionNotVerifiedError"]["properties"]["error"]["properties"]["code"]
    if get_error_code.get("const") != "session_not_verified":
        raise ContractError("artifact GET 409 must expose the stable session_not_verified code")
    head_conflict = components["responses"]["SessionNotVerifiedHead"]
    if "content" in head_conflict:
        raise ContractError("SessionNotVerifiedHead must not define a response body")
    error_code = head_conflict["headers"]["YLX-Error-Code"]["schema"].get("const")
    if error_code != "session_not_verified":
        raise ContractError("SessionNotVerifiedHead must expose the stable error code header")

    busy_response = components["responses"].get("CaptureBusy", {})
    busy_head_response = components["responses"].get("CaptureBusyHead", {})
    expected_busy_headers = {"YLX-Error-Code", "YLX-Wait-State", "Retry-After"}
    for name, response, body_required in (
        ("CaptureBusy", busy_response, True),
        ("CaptureBusyHead", busy_head_response, False),
    ):
        headers = response.get("headers", {})
        if set(headers) != expected_busy_headers:
            raise ContractError(f"{name} must define the exact busy header set")
        if not all(headers[header].get("required") is True for header in expected_busy_headers):
            raise ContractError(f"{name} busy headers must all be required")
        if not (
            headers["YLX-Error-Code"].get("schema", {}).get("type") == "string"
            and headers["YLX-Error-Code"].get("schema", {}).get("const") == "capture_busy"
        ):
            raise ContractError(f"{name} must expose capture_busy")
        if not (
            headers["YLX-Wait-State"].get("schema", {}).get("type") == "string"
            and headers["YLX-Wait-State"].get("schema", {}).get("const") == "idle"
        ):
            raise ContractError(f"{name} must expose idle as the wait state")
        retry_schema = headers["Retry-After"].get("schema", {})
        if retry_schema.get("type") != "integer" or retry_schema.get("minimum") != 1:
            raise ContractError(f"{name} Retry-After must be an integer of at least one")
        has_content = "content" in response
        if has_content != body_required:
            raise ContractError(f"{name} body suppression differs from GET/HEAD semantics")
    busy_body_ref = (
        busy_response.get("content", {})
        .get("application/problem+json", {})
        .get("schema", {})
        .get("$ref")
    )
    if set(busy_response.get("content", {})) != {"application/problem+json"} or (
        busy_body_ref != "#/components/schemas/CaptureBusyError"
    ):
        raise ContractError("CaptureBusy GET response must return CaptureBusyError")

    busy_schema = schemas["CaptureBusyError"]
    busy_error = busy_schema.get("properties", {}).get("error", {})
    busy_details = busy_error.get("properties", {}).get("details", {})
    if not (
        busy_schema.get("additionalProperties") is False
        and set(busy_schema.get("required", [])) == {"schema", "error"}
        and busy_error.get("additionalProperties") is False
        and set(busy_error.get("required", []))
        == {"code", "message", "request_id", "retryable", "details"}
        and busy_details.get("additionalProperties") is False
        and set(busy_details.get("required", []))
        == {"wait_for", "retry_after_seconds", "current_state"}
        and busy_error.get("properties", {}).get("code", {}).get("const")
        == "capture_busy"
        and busy_error.get("properties", {}).get("retryable", {}).get("const") is True
        and busy_details.get("properties", {}).get("wait_for", {}).get("const") == "idle"
        and busy_details.get("properties", {})
        .get("retry_after_seconds", {})
        .get("type")
        == "integer"
        and busy_details.get("properties", {})
        .get("retry_after_seconds", {})
        .get("minimum")
        == 1
        and set(
            busy_details.get("properties", {}).get("current_state", {}).get("enum", [])
        )
        == {"recording", "finalizing", "encoding", "verifying"}
    ):
        raise ContractError(
            "CaptureBusyError must be closed, retryable, wait for idle, and enumerate active states"
        )


def validate_v4_openapi_operation_surface(spec: dict[str, Any]) -> None:
    profiles = spec["x-ylx-security-profiles"]
    operations = api_operations(spec)
    expected_operations = {
        ("/device", "get"): "getDevice",
        ("/capture/status", "get"): "getCaptureStatus",
        ("/capture/start", "post"): "startCapture",
        ("/capture/stop", "post"): "stopCapture",
        ("/capture/events", "get"): "streamCaptureEvents",
        ("/capture/safe-swap", "get"): "getCurrentSafeSwapReceipt",
        ("/preview", "get"): "getPreview",
        ("/camera/focus", "get"): "getCameraFocus",
        ("/camera/focus", "post"): "setCameraFocus",
        ("/sessions", "get"): "listSessions",
        ("/sessions/{session_id}", "get"): "getSession",
        (
            "/sessions/{session_id}/unsuccessful-outcome",
            "get",
        ): "getRetainedUnsuccessfulSessionOutcome",
        (
            "/sessions/{session_id}/artifacts/{artifact_id}",
            "get",
        ): "getSessionArtifact",
        (
            "/sessions/{session_id}/artifacts/{artifact_id}",
            "head",
        ): "headSessionArtifact",
    }
    actual_operations = {
        (path, method): operation.get("operationId")
        for path, method, operation in operations
    }
    if actual_operations != expected_operations:
        raise ContractError("Device API v4 operation surface or operationId values drifted")
    _validate_v4_focus_operations(spec)
    declared_lab = set(profiles["lab"]["allowed_operation_ids"])
    actual_lab = {
        operation["operationId"]
        for _, _, operation in operations
        if operation.get("x-ylx-lab-access") == "allowed"
    }
    if actual_lab != declared_lab:
        raise ContractError("Device API v4 lab allowed_operation_ids do not match operation extensions")
    expected_security = [{"bearerAuth": []}]
    for path, method, operation in operations:
        if operation.get("security") != expected_security:
            raise ContractError(
                f"Device API v4 {method.upper()} {path}: security must remain bearer-only"
            )
    capabilities = spec["components"]["schemas"]["DeviceDescriptor"]["properties"]["capabilities"]
    if capabilities["properties"]["network_mutation"].get("const") is not False:
        raise ContractError("Device API v4 network_mutation must remain target-disabled")
    if "delete_session" in capabilities["properties"]:
        raise ContractError("Device API v4 must not advertise unresolved session deletion")
    focus_command = spec["paths"]["/camera/focus"]["post"]
    parameter_refs = [
        parameter.get("$ref")
        for parameter in focus_command.get("parameters", [])
        if isinstance(parameter, dict)
    ]
    if parameter_refs != ["#/components/parameters/IdempotencyKey"]:
        raise ContractError("POST /camera/focus must require exactly the shared Idempotency-Key parameter")
    if focus_command["responses"].get("409", {}).get("$ref") != "#/components/responses/Conflict":
        raise ContractError("POST /camera/focus must expose the shared 409 Conflict")


def validate_legacy_v2_openapi_compatibility(spec: dict[str, Any]) -> None:
    schemas = spec["components"]["schemas"]
    for component in (
        "SafeSwapReceipt",
        "SafeSwapHandleAudit",
        "SafeSwapAdmissionFence",
        "SafeSwapParticipantAcknowledgement",
        "SafeSwapReceiptResource",
    ):
        if schemas[component].get("additionalProperties") is not False:
            raise ContractError(f"legacy_v2 {component} must remain closed")

    receipt_schema = schemas["SafeSwapReceipt"]
    if not {"generation_id", "handle_audit"} <= set(receipt_schema["required"]):
        raise ContractError(
            "legacy_v2 SafeSwapReceipt must require generation_id and handle_audit"
        )
    if receipt_schema["properties"]["handle_audit"].get("$ref") != (
        "#/components/schemas/SafeSwapHandleAudit"
    ):
        raise ContractError(
            "legacy_v2 SafeSwapReceipt handle_audit must use SafeSwapHandleAudit"
        )
    if receipt_schema["properties"]["open_handle_count"].get("const") != 0:
        raise ContractError(
            "legacy_v2 SafeSwapReceipt open_handle_count must remain const 0"
        )

    audit_schema = schemas["SafeSwapHandleAudit"]
    if audit_schema["properties"]["scope"].get("const") != "integrated-m4":
        raise ContractError("legacy_v2 SafeSwapHandleAudit scope drifted")
    expected_audit_fields = {
        "schema",
        "scope",
        "participant_set_authority",
        "binding_context_sha256",
        "deployment_record_sha256",
        "participant_authority_sha256",
        "expected_participant_set_sha256",
        "expected_participant_ids",
        "admission_fence",
        "acknowledgements",
    }
    if set(audit_schema.get("required", [])) != expected_audit_fields:
        raise ContractError("legacy_v2 SafeSwapHandleAudit required fields drifted")
    if audit_schema["properties"]["participant_set_authority"].get("const") != (
        "m4-qualified-deployment-record"
    ):
        raise ContractError(
            "legacy_v2 SafeSwapHandleAudit participant authority constant drifted"
        )

    acknowledgement_schema = schemas["SafeSwapParticipantAcknowledgement"]
    if acknowledgement_schema["properties"]["open_handle_count"].get("const") != 0:
        raise ContractError(
            "legacy_v2 SafeSwapParticipantAcknowledgement open_handle_count must remain const 0"
        )
    actual_access_paths = set(
        acknowledgement_schema["properties"]["access_paths"]["items"].get(
            "enum", []
        )
    )
    if actual_access_paths != SAFE_SWAP_REQUIRED_ACCESS_PATHS:
        raise ContractError("legacy_v2 safe-swap access-path inventory drifted")
    fence_schema = schemas["SafeSwapAdmissionFence"]
    if not (
        fence_schema["properties"]["state"].get("const") == "held"
        and fence_schema["properties"]["held_until"].get("const")
        == "receipt-durable-publish"
    ):
        raise ContractError("legacy_v2 safe-swap admission fence drifted")


def parse_api_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def safe_swap_receipt(
    value: dict[str, Any], component: str
) -> tuple[dict[str, Any] | None, str | None]:
    if component == "CaptureEvent" and value.get("type") == "safe_swap":
        return value["data"], value["occurred_at"]
    if component in {"SafeSwapReceiptResource", "SafeSwapReceiptResourceV3"}:
        return value["receipt"], None
    return None, None


def load_safe_swap_participant_authority(
    persisted_schemas: dict[str, tuple[str, dict[str, Any]]],
) -> tuple[dict[str, Any], str]:
    fixture = SAFE_SWAP_PARTICIPANT_AUTHORITY_FIXTURE
    raw = fixture.read_bytes()
    authority = require_mapping(load_json_bytes(raw, fixture), str(fixture))
    schema_entry = persisted_schemas.get(
        SAFE_SWAP_PARTICIPANT_AUTHORITY_DISCRIMINATOR
    )
    if schema_entry is None:
        raise ContractError(f"{fixture}: participant authority schema is not indexed")
    _, schema = schema_entry
    errors = list(
        Draft202012Validator(schema, format_checker=FORMAT_CHECKER).iter_errors(
            authority
        )
    )
    if errors:
        raise ContractError(
            f"{fixture}: invalid synthetic participant authority\n{format_errors(errors)}"
        )
    participants = authority["participants"]
    participant_ids = [item["participant_id"] for item in participants]
    if participant_ids != sorted(participant_ids) or len(participant_ids) != len(
        set(participant_ids)
    ):
        raise ContractError(
            f"{fixture}: participant authority IDs must be unique and bytewise sorted"
        )
    for participant in participants:
        access_paths = participant["access_paths"]
        if access_paths != sorted(access_paths):
            raise ContractError(
                f"{fixture}: participant authority access paths must be bytewise sorted"
            )
    covered_access_paths = {
        access_path
        for participant in participants
        for access_path in participant["access_paths"]
    }
    if covered_access_paths != SAFE_SWAP_REQUIRED_ACCESS_PATHS:
        raise ContractError(
            f"{fixture}: participant authority access-path inventory is incomplete"
        )
    return authority, hashlib.sha256(raw).hexdigest()


def validate_safe_swap_receipt(
    receipt: dict[str, Any],
    fixture: Path,
    sessions: dict[str, tuple[Path, dict[str, Any], bytes]],
    occurred_at: str | None,
    participant_authority: dict[str, Any] | None,
    participant_authority_sha256: str | None,
) -> None:
    receipt_schema = receipt.get("schema")
    if receipt_schema not in {"ylx.safe-swap-receipt.v2", "ylx.safe-swap-receipt.v3"}:
        raise ContractError(f"{fixture}: unknown safe-swap receipt schema {receipt_schema!r}")
    session_id = receipt["session_id"]
    if session_id not in sessions:
        raise ContractError(f"{fixture}: no golden Device Session for safe-swap receipt")
    source_path, session, raw = sessions[session_id]
    for key in ("session_id", "volume_id", "manifest_id", "sealed_at"):
        if receipt[key] != session[key]:
            raise ContractError(f"{fixture}: safe-swap {key} does not match {source_path}")
    digest = hashlib.sha256(raw).hexdigest()
    if receipt["manifest_sha256"] != digest:
        raise ContractError(
            f"{fixture}: safe-swap manifest_sha256 does not match exact manifest bytes in {source_path}"
        )
    sealed_at = parse_api_datetime(receipt["sealed_at"])
    ended_at = parse_api_datetime(session["time"]["ended_at"])
    producer_verified_at = parse_api_datetime(session["integrity"]["verified_at"])
    released_at = parse_api_datetime(receipt["released_at"])
    if not ended_at <= producer_verified_at <= sealed_at <= released_at:
        raise ContractError(
            f"{fixture}: safe-swap time order must be ended_at <= integrity.verified_at "
            "<= sealed_at <= released_at"
        )
    if occurred_at is not None and parse_api_datetime(occurred_at) < released_at:
        raise ContractError(f"{fixture}: safe-swap event occurred_at precedes released_at")

    if receipt["open_handle_count"] != 0:
        raise ContractError(f"{fixture}: safe-swap open_handle_count must be zero")
    if receipt_schema == "ylx.safe-swap-receipt.v3":
        if "handle_audit" in receipt:
            raise ContractError(f"{fixture}: v3 safe-swap receipt must not contain handle_audit")
        return

    if participant_authority is None or participant_authority_sha256 is None:
        raise ContractError(
            f"{fixture}: legacy_v2 safe-swap receipt requires its compatibility authority"
        )

    audit = receipt["handle_audit"]
    expected_ids = audit["expected_participant_ids"]
    if expected_ids != sorted(expected_ids):
        raise ContractError(
            f"{fixture}: safe-swap expected participant IDs must be bytewise sorted"
        )
    if len(expected_ids) != len(set(expected_ids)):
        raise ContractError(f"{fixture}: safe-swap expected participant IDs are duplicate")
    expected_digest = canonical_line_set_sha256(expected_ids)
    if audit["expected_participant_set_sha256"] != expected_digest:
        raise ContractError(
            f"{fixture}: safe-swap expected participant-set digest does not match "
            "the canonical participant ID set"
        )

    fence = audit["admission_fence"]
    if fence["generation_id"] != receipt["generation_id"]:
        raise ContractError(
            f"{fixture}: safe-swap admission fence generation_id does not match receipt"
        )
    acknowledgements = audit["acknowledgements"]
    acknowledgement_ids = [item["participant_id"] for item in acknowledgements]
    duplicates = sorted(
        participant_id
        for participant_id, count in Counter(acknowledgement_ids).items()
        if count > 1
    )
    if duplicates:
        raise ContractError(
            f"{fixture}: safe-swap duplicate participant acknowledgements {duplicates}"
        )
    if set(acknowledgement_ids) != set(expected_ids):
        raise ContractError(
            f"{fixture}: safe-swap acknowledgement participant IDs differ from the "
            f"expected participant set; expected={sorted(expected_ids)}; "
            f"actual={sorted(acknowledgement_ids)}"
        )
    covered_access_paths = {
        access_path
        for acknowledgement in acknowledgements
        for access_path in acknowledgement["access_paths"]
    }
    if covered_access_paths != SAFE_SWAP_REQUIRED_ACCESS_PATHS:
        raise ContractError(
            f"{fixture}: safe-swap access-path coverage is incomplete or unknown; "
            f"missing={sorted(SAFE_SWAP_REQUIRED_ACCESS_PATHS - covered_access_paths)}; "
            f"unknown={sorted(covered_access_paths - SAFE_SWAP_REQUIRED_ACCESS_PATHS)}"
        )
    for acknowledgement in acknowledgements:
        participant_id = acknowledgement["participant_id"]
        if acknowledgement["generation_id"] != receipt["generation_id"]:
            raise ContractError(
                f"{fixture}: safe-swap participant {participant_id} generation_id "
                "does not match receipt"
            )
        if acknowledgement["fence_id"] != fence["fence_id"]:
            raise ContractError(
                f"{fixture}: safe-swap participant {participant_id} fence_id "
                "does not match admission fence"
            )
        acknowledged_at = parse_api_datetime(acknowledgement["acknowledged_at"])
        if not sealed_at <= acknowledged_at <= released_at:
            raise ContractError(
                f"{fixture}: safe-swap participant {participant_id} acknowledged_at "
                "must be between sealed_at and released_at"
            )
    participant_total = sum(
        acknowledgement["open_handle_count"]
        for acknowledgement in acknowledgements
    )
    if receipt["open_handle_count"] != participant_total:
        raise ContractError(
            f"{fixture}: safe-swap aggregate open_handle_count does not equal the "
            "participant audit total"
        )

    if audit["participant_authority_sha256"] != participant_authority_sha256:
        raise ContractError(
            f"{fixture}: safe-swap participant authority content digest does not match "
            f"{SAFE_SWAP_PARTICIPANT_AUTHORITY_FIXTURE}"
        )
    for field in ("binding_context_sha256", "deployment_record_sha256"):
        if audit[field] != participant_authority[field]:
            raise ContractError(
                f"{fixture}: safe-swap participant authority {field} binding drifted"
            )
    authority_access_paths = {
        item["participant_id"]: set(item["access_paths"])
        for item in participant_authority["participants"]
    }
    authority_ids = sorted(authority_access_paths)
    if expected_ids != authority_ids:
        raise ContractError(
            f"{fixture}: safe-swap participant authority shrink or owner drift; "
            f"authority={authority_ids}; receipt={expected_ids}"
        )
    for acknowledgement in acknowledgements:
        participant_id = acknowledgement["participant_id"]
        expected_access_paths = authority_access_paths[participant_id]
        actual_access_paths = set(acknowledgement["access_paths"])
        if actual_access_paths != expected_access_paths:
            raise ContractError(
                f"{fixture}: safe-swap participant authority access-path owner drift "
                f"for {participant_id}; expected={sorted(expected_access_paths)}; "
                f"actual={sorted(actual_access_paths)}"
            )


def validate_safe_swap_receipt_pair(
    event_receipt: dict[str, Any],
    query_receipt: dict[str, Any],
    event_fixture: Path,
    query_fixture: Path,
) -> None:
    for key in (
        "session_id",
        "volume_id",
        "generation_id",
        "manifest_id",
        "manifest_sha256",
        "sealed_at",
    ):
        if query_receipt[key] != event_receipt[key]:
            raise ContractError(
                f"{query_fixture}: safe-swap query {key} does not match "
                f"SSE receipt in {event_fixture}"
            )
    if query_receipt != event_receipt:
        raise ContractError(
            f"{query_fixture}: safe-swap SSE and query fixtures must carry "
            "the exact same typed receipt"
        )


def capture_recording_state(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    state = value.get("recording_state")
    return state if isinstance(state, dict) else None


def validate_capture_snapshot(
    snapshot: dict[str, Any],
    authority_epoch: Any,
    source_revision: Any,
    fixture: Path,
    *,
    event_session_id: Any = ...,
) -> None:
    device_state = snapshot.get("device_state")
    active = snapshot.get("active_recording")
    retained = snapshot.get("retained_unsuccessful")
    if device_state in ACTIVE_RECORDING_STATES:
        selected = active
        if retained is not None:
            raise ContractError(
                f"{fixture}: active snapshot cannot also expose a retained unsuccessful outcome"
            )
    elif device_state == "idle":
        selected = retained
        if active is not None:
            raise ContractError(
                f"{fixture}: idle snapshot cannot expose an active recording"
            )
    elif device_state == "blocked":
        selected = None
        if active is not None or retained is not None:
            raise ContractError(
                f"{fixture}: blocked snapshot cannot claim an active or retained session"
            )
    else:
        raise ContractError(f"{fixture}: snapshot has unknown device_state {device_state!r}")

    state = capture_recording_state(selected)
    if selected is None:
        if event_session_id is not ... and event_session_id is not None:
            raise ContractError(
                f"{fixture}: sessionless snapshot must carry null event session_id"
            )
        return
    if state is None:
        raise ContractError(f"{fixture}: snapshot recording lacks complete Recording State")
    if device_state in ACTIVE_RECORDING_STATES and state.get("state") != device_state:
        raise ContractError(
            f"{fixture}: snapshot device_state and active Recording State differ"
        )
    if device_state == "idle" and state.get("state") not in UNSUCCESSFUL_RECORDING_STATES:
        raise ContractError(
            f"{fixture}: idle retained outcome is not recoverable, failed, or abandoned"
        )
    if (
        state.get("authority_epoch") != authority_epoch
        or state.get("state_revision") != source_revision
    ):
        raise ContractError(
            f"{fixture}: snapshot Recording State authority_epoch/state_revision must "
            "equal wrapper or event authority_epoch/source_revision"
        )
    if event_session_id is not ... and event_session_id != state.get("session_id"):
        raise ContractError(
            f"{fixture}: snapshot event session_id differs from its Recording State"
        )


def validate_api_fixture_invariants(
    value: dict[str, Any],
    component: str,
    fixture: Path,
    sessions: dict[str, tuple[Path, dict[str, Any], bytes]],
    recording_states: list[tuple[Path, dict[str, Any]]],
    participant_authority: dict[str, Any] | None = None,
    participant_authority_sha256: str | None = None,
) -> dict[str, Any] | None:
    runtime: dict[str, Any] | None = None
    if component == "DeviceDescriptor":
        runtime = value.get("runtime")
    elif component == "CaptureEvent" and value.get("type") == "snapshot":
        runtime = value.get("data", {}).get("runtime")
        validate_capture_snapshot(
            value["data"],
            value.get("authority_epoch"),
            value.get("source_revision"),
            fixture,
            event_session_id=value.get("session_id"),
        )
    elif component == "CaptureStatusSnapshot":
        runtime = value.get("snapshot", {}).get("runtime")
        validate_capture_snapshot(
            value["snapshot"],
            value.get("authority_epoch"),
            value.get("source_revision"),
            fixture,
        )
    elif component == "RetainedUnsuccessfulSessionResource":
        state = capture_recording_state(value.get("outcome"))
        if state is None or state.get("state") not in UNSUCCESSFUL_RECORDING_STATES:
            raise ContractError(
                f"{fixture}: retained outcome must contain one unsuccessful terminal Recording State"
            )
        if (
            value.get("authority_epoch") != state.get("authority_epoch")
            or value.get("source_revision") != state.get("state_revision")
        ):
            raise ContractError(
                f"{fixture}: retained outcome authority_epoch/source_revision must "
                "equal Recording State authority_epoch/state_revision"
            )
    if isinstance(runtime, dict) and isinstance(runtime.get("live_imu"), dict):
        live_imu = runtime["live_imu"]
        if live_imu["clock"]["epoch_id"] != live_imu["session_id"]:
            raise ContractError(
                f"{fixture}: live_imu clock epoch_id must equal live_imu session_id"
            )

    if component == "CaptureEvent":
        if value["type"] == "safe_swap" and value["data"]["session_id"] != value["session_id"]:
            raise ContractError(f"{fixture}: safe-swap receipt session_id mismatch")
        diagnostic = value.get("data", {}).get("diagnostic")
        if (
            value.get("type") == "diagnostic"
            and isinstance(diagnostic, dict)
            and value.get("session_id") is not None
        ):
            matches = [
                (path, state)
                for path, state in recording_states
                if state.get("session_id") == value.get("session_id")
                and diagnostic in state.get("diagnostics", [])
            ]
            if len(matches) != 1:
                raise ContractError(
                    f"{fixture}: diagnostic is not an exact lossless member of one authoritative Recording State"
                )
            state_path, state = matches[0]
            if (
                value.get("authority_epoch") != state.get("authority_epoch")
                or value.get("source_revision") != state.get("state_revision")
                or value.get("occurred_at") != diagnostic.get("at")
            ):
                raise ContractError(
                    f"{fixture}: {diagnostic.get('code')} diagnostic "
                    "authority_epoch/source_revision/occurred_at differs from authoritative "
                    f"Recording State authority_epoch/state_revision/diagnostic.at in {state_path}"
                )
    if component == "SessionList":
        items = value["items"]
        diagnostics = value["diagnostics"]
        session_ids = [item["session_id"] for item in items]
        if len(session_ids) != len(set(session_ids)):
            raise ContractError(f"{fixture}: SessionList contains duplicate session_id values")
        expected_order = sorted(
            items,
            key=lambda item: (parse_api_datetime(item["started_at"]), item["session_id"]),
        )
        if items != expected_order:
            raise ContractError(
                f"{fixture}: SessionList items must be ordered by started_at then session_id"
            )
        quarantine_ids = [item["quarantine_id"] for item in diagnostics]
        if len(quarantine_ids) != len(set(quarantine_ids)):
            raise ContractError(
                f"{fixture}: SessionList diagnostics contain duplicate quarantine_id values"
            )
        page_cardinality = len(items) + len(diagnostics)
        if page_cardinality > SESSION_LIST_LIMIT_MAXIMUM:
            raise ContractError(
                f"{fixture}: SessionList combined page cardinality exceeds "
                f"{SESSION_LIST_LIMIT_MAXIMUM}"
            )
        if page_cardinality == 0 and value["next_cursor"] is not None:
            raise ContractError(
                f"{fixture}: an empty SessionList page cannot advance a non-null cursor"
            )

        if fixture.name == "session-list-with-quarantine.json":
            expected_codes = Counter(
                {
                    "manifest_unreadable": 2,
                    "unsupported_schema": 1,
                    "manifest_invalid": 1,
                    "manifest_not_sealed": 1,
                }
            )
            actual_codes = Counter(item["code"] for item in diagnostics)
            if actual_codes != expected_codes:
                raise ContractError(
                    f"{fixture}: golden quarantine fixture must separately cover malformed bytes, "
                    "read failure, unknown schema, invalid manifest, and unsealed manifest"
                )
            unreadable_messages = [
                item["message"].casefold()
                for item in diagnostics
                if item["code"] == "manifest_unreadable"
            ]
            if not any("malformed" in message for message in unreadable_messages) or not any(
                "read" in message and ("i/o" in message or "failure" in message)
                for message in unreadable_messages
            ):
                raise ContractError(
                    f"{fixture}: manifest_unreadable fixtures must distinguish malformed bytes from read failure"
                )
            if items or value["next_cursor"] is None:
                raise ContractError(
                    f"{fixture}: quarantine-only page must advance a non-null cursor without summaries"
                )
            forbidden_summary_fields = {
                "session_id",
                "manifest_id",
                "take_id",
                "device",
                "started_at",
                "ended_at",
                "total_bytes",
            }
            if any(forbidden_summary_fields & set(item) for item in diagnostics):
                raise ContractError(
                    f"{fixture}: diagnostics must not manufacture SessionSummary identity or time fields"
                )
    receipt, occurred_at = safe_swap_receipt(value, component)
    if receipt is not None:
        validate_safe_swap_receipt(
            receipt,
            fixture,
            sessions,
            occurred_at,
            participant_authority,
            participant_authority_sha256,
        )
    return receipt


def _contains_any_key(value: Any, keys: set[str]) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys:
                return key
            nested = _contains_any_key(child, keys)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = _contains_any_key(child, keys)
            if nested is not None:
                return nested
    return None


def _v4_snapshot_active_session_id(snapshot: dict[str, Any]) -> str | None:
    if snapshot.get("device_state") not in ACTIVE_RECORDING_STATES:
        return None
    state = capture_recording_state(snapshot.get("active_recording"))
    if not isinstance(state, dict):
        return None
    session_id = state.get("session_id")
    return session_id if isinstance(session_id, str) else None


def _validate_v4_runtime_live_imu(
    runtime: Any,
    *,
    active_session_id: str | None,
    fixture: Path,
    require_active_match: bool,
) -> None:
    if not isinstance(runtime, dict):
        return
    live_imu = runtime.get("live_imu")
    if live_imu is None:
        return
    if not isinstance(live_imu, dict):
        return
    forbidden = _contains_any_key(live_imu, V4_FORBIDDEN_LIVE_IMU_FIELDS)
    if forbidden is not None:
        raise ContractError(
            f"{fixture}: v4 live_imu must not carry legacy v3 field {forbidden}"
        )
    if not require_active_match:
        return
    if active_session_id is None:
        raise ContractError(
            f"{fixture}: v4 live_imu must be null when snapshot has no active session"
        )
    if live_imu.get("session_id") != active_session_id:
        raise ContractError(
            f"{fixture}: v4 live_imu.session_id must equal active session_id "
            f"{active_session_id}"
        )


def validate_v4_api_fixture_invariants(
    value: dict[str, Any],
    component: str,
    fixture: Path,
) -> None:
    if component == "DeviceDescriptor":
        _validate_v4_runtime_live_imu(
            value.get("runtime"),
            active_session_id=None,
            fixture=fixture,
            require_active_match=False,
        )
    elif component == "CaptureStatusSnapshot":
        snapshot = require_mapping(value.get("snapshot"), f"{fixture}: snapshot")
        validate_capture_snapshot(
            snapshot,
            value.get("authority_epoch"),
            value.get("source_revision"),
            fixture,
        )
        _validate_v4_runtime_live_imu(
            snapshot.get("runtime"),
            active_session_id=_v4_snapshot_active_session_id(snapshot),
            fixture=fixture,
            require_active_match=True,
        )
    elif component == "CaptureEvent" and value.get("type") == "snapshot":
        snapshot = require_mapping(value.get("data"), f"{fixture}: data")
        validate_capture_snapshot(
            snapshot,
            value.get("authority_epoch"),
            value.get("source_revision"),
            fixture,
            event_session_id=value.get("session_id"),
        )
        _validate_v4_runtime_live_imu(
            snapshot.get("runtime"),
            active_session_id=_v4_snapshot_active_session_id(snapshot),
            fixture=fixture,
            require_active_match=True,
        )
    elif component == "RetainedUnsuccessfulSessionResource":
        state = capture_recording_state(value.get("outcome"))
        if state is None or state.get("state") not in UNSUCCESSFUL_RECORDING_STATES:
            raise ContractError(
                f"{fixture}: retained outcome must contain one unsuccessful terminal Recording State"
            )
        if (
            value.get("authority_epoch") != state.get("authority_epoch")
            or value.get("source_revision") != state.get("state_revision")
        ):
            raise ContractError(
                f"{fixture}: retained outcome authority_epoch/source_revision must "
                "equal Recording State authority_epoch/state_revision"
            )


def validate_api_fixtures(
    legacy_v2_schemas: dict[str, tuple[str, dict[str, Any]]],
    openapi_identity: dict[str, Any],
    sessions: dict[str, tuple[Path, dict[str, Any], bytes]],
    recording_states: list[tuple[Path, dict[str, Any]]],
) -> tuple[int, int, int, int]:
    spec = require_mapping(load_yaml(OPENAPI), str(OPENAPI))
    v3_identity = openapi_versions(openapi_identity)["v3"]
    validate_openapi_identity(spec, v3_identity, OPENAPI)
    mapping_path = FIXTURES / "api" / "expected-results.json"
    mapping = require_mapping(load_json(mapping_path), str(mapping_path))
    if set(mapping) != {"schema", "valid", "invalid", "legacy_v2"}:
        raise ContractError(
            f"{mapping_path}: expected exactly schema, valid, invalid, and legacy_v2 fields"
        )
    if mapping.get("schema") != "ylx.api-fixture-results.v2":
        raise ContractError(f"{mapping_path}: unexpected schema discriminator")
    valid_mapping = require_mapping(mapping.get("valid"), f"{mapping_path}: valid")
    invalid_mapping = require_mapping(mapping.get("invalid"), f"{mapping_path}: invalid")
    legacy_v2 = require_mapping(
        mapping.get("legacy_v2"), f"{mapping_path}: legacy_v2"
    )
    if set(legacy_v2) != {"valid", "invalid"}:
        raise ContractError(
            f"{mapping_path}: legacy_v2 must contain exactly valid and invalid"
        )

    legacy_v2_paths: dict[str, set[str]] = {}
    for kind, complete_mapping in (
        ("valid", valid_mapping),
        ("invalid", invalid_mapping),
    ):
        paths = legacy_v2.get(kind)
        if not isinstance(paths, list) or not paths or not all(
            isinstance(path, str) and path for path in paths
        ):
            raise ContractError(
                f"{mapping_path}: legacy_v2.{kind} must be a nonempty string array"
            )
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ContractError(
                f"{mapping_path}: legacy_v2.{kind} must be unique and bytewise sorted"
            )
        unknown = set(paths) - set(complete_mapping)
        if unknown:
            raise ContractError(
                f"{mapping_path}: legacy_v2.{kind} contains unknown fixtures "
                f"{sorted(unknown)}"
            )
        legacy_v2_paths[kind] = set(paths)

    if {
        valid_mapping[path] for path in legacy_v2_paths["valid"]
    } != {"SafeSwapReceiptResource"} or {
        invalid_mapping[path].get("component")
        for path in legacy_v2_paths["invalid"]
        if isinstance(invalid_mapping[path], dict)
    } != {"SafeSwapReceiptResource"}:
        raise ContractError(
            f"{mapping_path}: legacy_v2 may contain only the readable v2 safe-swap resource"
        )

    current_valid_mapping = {
        path: component
        for path, component in valid_mapping.items()
        if path not in legacy_v2_paths["valid"]
    }
    current_invalid_mapping = {
        path: case
        for path, case in invalid_mapping.items()
        if path not in legacy_v2_paths["invalid"]
    }
    validate_openapi_operation_invariants(spec)
    api_fixture_root = FIXTURES / "api"
    actual_valid = {
        path.relative_to(api_fixture_root).as_posix()
        for path in (api_fixture_root / "valid").rglob("*.json")
    }
    actual_invalid = {
        path.relative_to(api_fixture_root).as_posix()
        for path in (api_fixture_root / "invalid").rglob("*.json")
    }
    if actual_valid != set(valid_mapping):
        raise ContractError(
            "valid API fixtures do not exactly match expected-results.json; "
            f"missing={sorted(set(valid_mapping) - actual_valid)}; "
            f"unknown={sorted(actual_valid - set(valid_mapping))}"
        )
    if actual_invalid != set(invalid_mapping):
        raise ContractError(
            "invalid API fixtures do not exactly match expected-results.json; "
            f"missing={sorted(set(invalid_mapping) - actual_invalid)}; "
            f"unknown={sorted(actual_invalid - set(invalid_mapping))}"
        )
    if any(
        Path(relative).as_posix() != relative
        or Path(relative).parent.as_posix() != expected_parent
        or Path(relative).name in {"", ".", ".."}
        for expected_parent, relatives in (
            ("valid", valid_mapping),
            ("invalid", invalid_mapping),
        )
        for relative in relatives
    ):
        raise ContractError(
            f"{mapping_path}: API fixture keys must be canonical files directly below valid/ or invalid/"
        )
    current_golden_receipts: dict[str, tuple[Path, dict[str, Any]]] = {}
    media_lost_event: dict[str, Any] | None = None
    valid_documents: dict[str, tuple[str, dict[str, Any]]] = {}
    valid_count = 0
    for relative, component in sorted(current_valid_mapping.items()):
        if not isinstance(component, str) or component not in spec["components"]["schemas"]:
            raise ContractError(f"{mapping_path}: unknown component {component!r} for {relative}")
        fixture = FIXTURES / "api" / relative
        value = load_json(fixture)
        valid_documents[relative] = (component, value)
        errors = list(component_validator(spec, component).iter_errors(value))
        if errors:
            raise ContractError(f"{fixture}: expected valid {component}\n{format_errors(errors)}")
        receipt = validate_api_fixture_invariants(
            value,
            component,
            fixture,
            sessions,
            recording_states,
        )
        if receipt is not None:
            receipt_key = f"{component}:{receipt['schema']}"
            if receipt_key in current_golden_receipts:
                raise ContractError(
                    f"{fixture}: API valid corpus contains more than one safe-swap {component}"
                )
            current_golden_receipts[receipt_key] = (fixture, receipt)
        if (
            component == "CaptureEvent"
            and value.get("data", {}).get("diagnostic", {}).get("code") == "media_lost"
        ):
            media_lost_event = value
        valid_count += 1

    status_documents = [
        (relative, value)
        for relative, (component, value) in valid_documents.items()
        if component == "CaptureStatusSnapshot"
    ]
    retained_status_documents = [
        (relative, value)
        for relative, value in status_documents
        if value.get("snapshot", {}).get("retained_unsuccessful") is not None
    ]
    empty_idle_status_documents = [
        (relative, value)
        for relative, value in status_documents
        if value.get("snapshot", {}).get("device_state") == "idle"
        and value.get("snapshot", {}).get("active_recording") is None
        and value.get("snapshot", {}).get("retained_unsuccessful") is None
    ]
    retained_snapshot_events = [
        (relative, value)
        for relative, (component, value) in valid_documents.items()
        if component == "CaptureEvent"
        and value.get("type") == "snapshot"
        and value.get("data", {}).get("retained_unsuccessful") is not None
    ]
    empty_idle_snapshot_events = [
        (relative, value)
        for relative, (component, value) in valid_documents.items()
        if component == "CaptureEvent"
        and value.get("type") == "snapshot"
        and value.get("data", {}).get("device_state") == "idle"
        and value.get("data", {}).get("active_recording") is None
        and value.get("data", {}).get("retained_unsuccessful") is None
    ]
    retained_resources = [
        (relative, value)
        for relative, (component, value) in valid_documents.items()
        if component == "RetainedUnsuccessfulSessionResource"
    ]
    if not (
        len(status_documents) == 2
        and len(retained_status_documents) == 1
        and len(empty_idle_status_documents) == 1
        and len(retained_snapshot_events) == 1
        and len(empty_idle_snapshot_events) == 1
        and len(retained_resources) == 1
    ):
        raise ContractError(
            "API valid corpus must contain matching retained and empty-idle HTTP/SSE "
            "snapshots plus one per-session retained unsuccessful resource"
        )
    status_relative, status = retained_status_documents[0]
    event_relative, snapshot_event = retained_snapshot_events[0]
    idle_status_relative, idle_status = empty_idle_status_documents[0]
    idle_event_relative, idle_snapshot_event = empty_idle_snapshot_events[0]
    resource_relative, retained_resource = retained_resources[0]
    if not (
        status["authority_epoch"] == snapshot_event["authority_epoch"]
        and status["source_revision"] == snapshot_event["source_revision"]
        and status["snapshot"] == snapshot_event["data"]
    ):
        raise ContractError(
            f"{status_relative} and {event_relative}: HTTP status and SSE snapshot "
            "must preserve the exact authority identity and snapshot payload"
        )
    if not (
        idle_status["authority_epoch"] == idle_snapshot_event["authority_epoch"]
        and idle_status["source_revision"] == idle_snapshot_event["source_revision"]
        and idle_status["snapshot"] == idle_snapshot_event["data"]
        and idle_status["snapshot"].get("runtime") is not None
    ):
        raise ContractError(
            f"{idle_status_relative} and {idle_event_relative}: ordinary idle must be "
            "a 200-capable status with runtime and the exact shared SSE snapshot payload"
        )
    retained = status["snapshot"]["retained_unsuccessful"]
    if not (
        retained_resource["authority_epoch"] == status["authority_epoch"]
        and retained_resource["source_revision"] == status["source_revision"]
        and retained_resource["outcome"] == retained
    ):
        raise ContractError(
            f"{resource_relative}: per-session retained outcome must equal the "
            f"current retained state in {status_relative}"
        )

    expected_current_receipt_keys = {
        "CaptureEvent:ylx.safe-swap-receipt.v3",
        "SafeSwapReceiptResourceV3:ylx.safe-swap-receipt.v3",
    }
    if set(current_golden_receipts) != expected_current_receipt_keys:
        raise ContractError(
            "current API valid corpus must contain the v3 production event and v3 query resource"
        )
    event_fixture, event_receipt = current_golden_receipts[
        "CaptureEvent:ylx.safe-swap-receipt.v3"
    ]
    query_fixture, query_receipt = current_golden_receipts[
        "SafeSwapReceiptResourceV3:ylx.safe-swap-receipt.v3"
    ]
    validate_safe_swap_receipt_pair(
        event_receipt,
        query_receipt,
        event_fixture,
        query_fixture,
    )
    receipt_session_id = event_receipt["session_id"]
    matching_recoverable_states = [
        (path, state)
        for path, state in recording_states
        if state["state"] == "recoverable"
        and state["storage"]["status"] == "media_lost"
        and media_lost_event is not None
        and state["session_id"] == media_lost_event["session_id"]
    ]
    if len(matching_recoverable_states) != 1 or media_lost_event is None:
        raise ContractError("media_lost API event must bind the golden recoverable session")
    state_path, recoverable = matching_recoverable_states[0]
    if not (
        media_lost_event["authority_epoch"] == recoverable["authority_epoch"]
        and media_lost_event["source_revision"] == recoverable["state_revision"]
        and media_lost_event["occurred_at"] == recoverable["updated_at"]
    ):
        raise ContractError(
            f"media_lost API event source revision/time do not match {state_path}"
        )
    if media_lost_event["session_id"] == receipt_session_id:
        raise ContractError("one session cannot be both recoverable media_lost and safe-swap success")

    current_invalid_count = 0
    for relative, raw_case in sorted(current_invalid_mapping.items()):
        case = require_mapping(raw_case, f"{mapping_path}: invalid.{relative}")
        allowed_case_fields = {
            "component",
            "validation_stage",
            "expected_error_keywords",
        }
        if not {"component", "expected_error_keywords"} <= set(case) or not set(
            case
        ) <= allowed_case_fields:
            raise ContractError(f"{mapping_path}: invalid.{relative} has invalid metadata fields")
        stage = case.get("validation_stage", "json-schema")
        if stage not in {"json-schema", "cross-field"}:
            raise ContractError(
                f"{mapping_path}: invalid.{relative} has invalid validation_stage"
            )
        keywords = case.get("expected_error_keywords")
        if not isinstance(keywords, list) or not keywords or not all(
            isinstance(keyword, str) and keyword for keyword in keywords
        ):
            raise ContractError(
                f"{mapping_path}: invalid.{relative} expected_error_keywords must be nonempty strings"
            )
        fixture = FIXTURES / "api" / relative
        value = load_json(fixture)
        component = case.get("component")
        if component not in spec["components"]["schemas"]:
            raise ContractError(f"{mapping_path}: unknown component {component!r} for {relative}")
        errors = list(component_validator(spec, component).iter_errors(value))
        if stage == "cross-field":
            if errors:
                raise ContractError(
                    f"{fixture}: procedural API fixture unexpectedly fails schema\n{format_errors(errors)}"
                )
            try:
                validate_api_fixture_invariants(
                    value,
                    component,
                    fixture,
                    sessions,
                    recording_states,
                )
            except ContractError as error:
                require_keywords(str(error), case["expected_error_keywords"], fixture)
            else:
                raise ContractError(f"{fixture}: expected procedural API validation failure")
        else:
            if not errors:
                raise ContractError(f"{fixture}: expected invalid {component}")
            require_keywords(format_errors(errors), case["expected_error_keywords"], fixture)
        current_invalid_count += 1

    for relative, (component, session_list) in sorted(valid_documents.items()):
        if component != "SessionList":
            continue
        for item in session_list["items"]:
            matched_session = sessions.get(item["session_id"])
            if matched_session is None:
                raise ContractError(
                    f"{relative}: SessionSummary has no matching golden Device Session"
                )
            source_path, session, raw = matched_session
            projected_device = {
                "device_id": session["device"]["device_id"],
                "device_label": session["device"]["device_label"],
            }
            expected_total = sum(
                artifact["bytes"]
                for artifact in {item["artifact_id"]: item for item in artifact_descriptors(session)}.values()
            )
            expected_projection = {
                "session_id": session["session_id"],
                "producer_outcome": "sealed",
                "take_id": session["take"]["take_id"],
                "take_sequence": session["take"]["sequence"],
                "continuation_of": session["take"]["continuation_of"],
                "display_name": session["display_name"],
                "device": projected_device,
                "started_at": session["time"]["started_at"],
                "ended_at": session["time"]["ended_at"],
                "duration_seconds": session["time"]["duration_seconds"],
                "total_bytes": expected_total,
            }
            actual_projection = {key: item[key] for key in expected_projection}
            if actual_projection != expected_projection:
                raise ContractError(f"{relative}: SessionSummary exact projection mismatch")
            started_at = parse_api_datetime(session["time"]["started_at"])
            ended_at = parse_api_datetime(session["time"]["ended_at"])
            producer_verified_at = parse_api_datetime(session["integrity"]["verified_at"])
            sealed_at = parse_api_datetime(session["sealed_at"])
            if not started_at <= ended_at <= producer_verified_at <= sealed_at:
                raise ContractError(f"{relative}: Device Session timestamps are out of order")
            verification = item["verification"]
            if verification is not None:
                if verification["manifest_sha256"] != hashlib.sha256(raw).hexdigest():
                    raise ContractError(f"{relative}: verification digest mismatch for {source_path}")
                if parse_api_datetime(verification["verified_at"]) < sealed_at:
                    raise ContractError(f"{relative}: gateway verified_at precedes manifest sealed_at")
    validate_legacy_v2_openapi_compatibility(spec)
    participant_authority, participant_authority_sha256 = (
        load_safe_swap_participant_authority(legacy_v2_schemas)
    )
    legacy_v2_valid_count = 0
    for relative in sorted(legacy_v2_paths["valid"]):
        component = valid_mapping[relative]
        fixture = FIXTURES / "api" / relative
        value = load_json(fixture)
        errors = list(component_validator(spec, component).iter_errors(value))
        if errors:
            raise ContractError(
                f"{fixture}: expected valid legacy_v2 {component}\n{format_errors(errors)}"
            )
        receipt = validate_api_fixture_invariants(
            value,
            component,
            fixture,
            sessions,
            recording_states,
            participant_authority,
            participant_authority_sha256,
        )
        if receipt is None or receipt.get("schema") != "ylx.safe-swap-receipt.v2":
            raise ContractError(
                f"{fixture}: legacy_v2 valid fixture must carry the readable v2 receipt"
            )
        legacy_v2_valid_count += 1

    legacy_v2_invalid_count = 0
    for relative in sorted(legacy_v2_paths["invalid"]):
        case = require_mapping(
            invalid_mapping[relative], f"{mapping_path}: invalid.{relative}"
        )
        fixture = FIXTURES / "api" / relative
        value = load_json(fixture)
        component = case["component"]
        errors = list(component_validator(spec, component).iter_errors(value))
        stage = case.get("validation_stage", "json-schema")
        if stage == "cross-field":
            if errors:
                raise ContractError(
                    f"{fixture}: legacy_v2 procedural fixture unexpectedly fails schema\n"
                    f"{format_errors(errors)}"
                )
            try:
                validate_api_fixture_invariants(
                    value,
                    component,
                    fixture,
                    sessions,
                    recording_states,
                    participant_authority,
                    participant_authority_sha256,
                )
            except ContractError as error:
                require_keywords(str(error), case["expected_error_keywords"], fixture)
            else:
                raise ContractError(
                    f"{fixture}: expected legacy_v2 procedural validation failure"
                )
        else:
            if not errors:
                raise ContractError(
                    f"{fixture}: expected invalid legacy_v2 {component}"
                )
            require_keywords(format_errors(errors), case["expected_error_keywords"], fixture)
        legacy_v2_invalid_count += 1

    return (
        valid_count,
        current_invalid_count,
        legacy_v2_valid_count,
        legacy_v2_invalid_count,
    )


def validate_v4_api_fixtures(openapi_identity: dict[str, Any]) -> tuple[int, int]:
    spec = require_mapping(load_yaml(OPENAPI_V4), str(OPENAPI_V4))
    v4_identity = openapi_versions(openapi_identity)["v4"]
    validate_openapi_identity(spec, v4_identity, OPENAPI_V4)
    validate_openapi_references_resolve(spec, OPENAPI_V4)
    validate_v4_openapi_operation_surface(spec)
    validate_v4_openapi_delta_against_v3(
        require_mapping(load_yaml(OPENAPI_V3), str(OPENAPI_V3)),
        spec,
    )
    validate_v4_live_imu_contract(spec)

    mapping_path = V4_API_FIXTURES / "expected-results.json"
    mapping = require_mapping(load_json(mapping_path), str(mapping_path))
    if set(mapping) != {"schema", "valid", "invalid"}:
        raise ContractError(f"{mapping_path}: expected exactly schema, valid, invalid")
    if mapping.get("schema") != "ylx.api-v4-fixture-results.v1":
        raise ContractError(f"{mapping_path}: unexpected schema discriminator")
    valid_mapping = require_mapping(mapping.get("valid"), f"{mapping_path}: valid")
    invalid_mapping = require_mapping(mapping.get("invalid"), f"{mapping_path}: invalid")
    actual_valid = {
        path.relative_to(V4_API_FIXTURES).as_posix()
        for path in (V4_API_FIXTURES / "valid").rglob("*.json")
    }
    actual_invalid = {
        path.relative_to(V4_API_FIXTURES).as_posix()
        for path in (V4_API_FIXTURES / "invalid").rglob("*.json")
    }
    if actual_valid != set(valid_mapping):
        raise ContractError(
            "v4 valid API fixtures do not exactly match expected-results.json; "
            f"missing={sorted(set(valid_mapping) - actual_valid)}; "
            f"unknown={sorted(actual_valid - set(valid_mapping))}"
        )
    if actual_invalid != set(invalid_mapping):
        raise ContractError(
            "v4 invalid API fixtures do not exactly match expected-results.json; "
            f"missing={sorted(set(invalid_mapping) - actual_invalid)}; "
            f"unknown={sorted(actual_invalid - set(invalid_mapping))}"
        )

    valid_count = 0
    for relative, component in sorted(valid_mapping.items()):
        if component not in spec["components"]["schemas"]:
            raise ContractError(f"{mapping_path}: unknown v4 component {component!r} for {relative}")
        fixture = V4_API_FIXTURES / relative
        value = load_json(fixture)
        errors = list(component_validator(spec, component, OPENAPI_V4).iter_errors(value))
        if errors:
            raise ContractError(f"{fixture}: expected valid v4 {component}\n{format_errors(errors)}")
        validate_v4_api_fixture_invariants(value, component, fixture)
        valid_count += 1

    invalid_count = 0
    for relative, raw_case in sorted(invalid_mapping.items()):
        case = require_mapping(raw_case, f"{mapping_path}: invalid.{relative}")
        if set(case) != {"component", "expected_error_keywords"}:
            raise ContractError(
                f"{mapping_path}: invalid.{relative} must contain exactly component and expected_error_keywords"
            )
        fixture = V4_API_FIXTURES / relative
        value = load_json(fixture)
        component = case["component"]
        if component not in spec["components"]["schemas"]:
            raise ContractError(f"{mapping_path}: unknown v4 component {component!r} for {relative}")
        errors = list(component_validator(spec, component, OPENAPI_V4).iter_errors(value))
        if errors:
            error_text = format_errors(errors)
        else:
            try:
                validate_v4_api_fixture_invariants(value, component, fixture)
            except ContractError as error:
                error_text = str(error)
            else:
                raise ContractError(f"{fixture}: expected invalid v4 {component}")
        keywords = case.get("expected_error_keywords")
        if not isinstance(keywords, list) or not keywords or not all(
            isinstance(keyword, str) and keyword for keyword in keywords
        ):
            raise ContractError(
                f"{mapping_path}: invalid.{relative} expected_error_keywords must be nonempty strings"
            )
        require_keywords(error_text, keywords, fixture)
        invalid_count += 1
    return valid_count, invalid_count


def main() -> None:
    current_schemas, legacy_v2_schemas, openapi_identity = contract_identity_index()
    validate_versioned_openapi_contracts(openapi_identity)
    (
        persisted_valid,
        persisted_invalid,
        legacy_v2_persisted_valid,
        legacy_v2_persisted_invalid,
        sessions,
        recording_states,
    ) = validate_persisted_schemas(current_schemas, legacy_v2_schemas)
    publication_signature_cases = validate_publication_signature_candidate(
        current_schemas
    )
    publication_admission_cases = validate_publication_admission_invariant()
    take_cases = validate_take_aggregation_corpus(current_schemas)
    artifact_response_cases, artifact_response_mutations = (
        validate_artifact_response_corpus(current_schemas)
    )
    record_cases, record_mutations = validate_record_corpus(current_schemas)
    api_valid, api_invalid, legacy_v2_api_valid, legacy_v2_api_invalid = (
        validate_api_fixtures(
            legacy_v2_schemas,
            openapi_identity,
            sessions,
            recording_states,
        )
    )
    v4_api_valid, v4_api_invalid = validate_v4_api_fixtures(openapi_identity)
    print(
        "contract validation passed: "
        f"{persisted_valid} persisted valid, {persisted_invalid} persisted invalid, "
        f"{take_cases} take aggregation cases, "
        f"{artifact_response_cases} artifact response cases, "
        f"{artifact_response_mutations} artifact response mutations, "
        f"{record_cases} record corpus cases, {record_mutations} record corpus mutations, "
        f"{api_valid} API valid, {api_invalid} API invalid fixtures, "
        f"{legacy_v2_persisted_valid} legacy_v2 persisted valid, "
        f"{legacy_v2_persisted_invalid} legacy_v2 persisted invalid, "
        f"{legacy_v2_api_valid} legacy_v2 API valid, "
        f"{legacy_v2_api_invalid} legacy_v2 API invalid fixtures, "
        f"{v4_api_valid} v4 API valid, {v4_api_invalid} v4 API invalid fixtures, "
        f"{publication_signature_cases} publication-signature candidate checks, "
        f"{publication_admission_cases} publication admission checks"
    )


if __name__ == "__main__":
    try:
        main()
    except ContractError as error:
        raise SystemExit(f"contract validation failed: {error}") from error
