#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "PyYAML>=6,<7",
#   "tree-sitter>=0.25,<0.26",
#   "tree-sitter-typescript>=0.23,<0.24",
# ]
# ///
"""Check product consumers against the central Device API contract identity.

The gate is intentionally read-only. It compares exact central OpenAPI bytes,
the consumer's declared support manifest, and a small consumer matrix of version
routing probes. Unknown API majors fail closed by requiring every consumer to
declare only the supported majors listed in ``contract-identities.yaml``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from tree_sitter import Language, Parser
import tree_sitter_typescript


CONTRACTS = Path(__file__).resolve().parents[1]
ROOT = CONTRACTS.parent
IDENTITIES = CONTRACTS / "contract-identities.yaml"
MATRIX = CONTRACTS / "consumer-matrix.yaml"
SUPPORT_MANIFEST = Path("contracts/ylx-device-api-support.json")
TS_IDENTIFIER = re.compile(r"[A-Za-z_$][0-9A-Za-z_$]*")
TS_LANGUAGE = Language(tree_sitter_typescript.language_typescript())


@dataclass(frozen=True)
class ProbeFailure:
    consumer: str
    path: Path
    message: str


@dataclass
class ConsumerObservation:
    name: str
    repository: str
    path: Path
    live_imu_consumption: str | None = None
    reason: str | None = None
    head: str | None = None
    dirty: bool | None = None
    non_authoritative_reasons: list[str] = field(default_factory=list)
    failures: list[ProbeFailure] = field(default_factory=list)


@dataclass
class GateResult:
    observations: list[ConsumerObservation]

    @property
    def ok(self) -> bool:
        return all(not observation.failures for observation in self.observations)

    @property
    def authoritative(self) -> bool:
        return all(
            not observation.non_authoritative_reasons
            for observation in self.observations
        )

    def to_text(self) -> str:
        status = "PASSED" if self.ok else "FAILED"
        if self.authoritative:
            lines = [f"consumer contract gate {status}"]
        else:
            lines = [
                f"consumer contract gate {status} "
                "(NON-AUTHORITATIVE: --allow-dirty accepted dirty worktrees)"
            ]
        for observation in self.observations:
            state = []
            if observation.head:
                state.append(observation.head)
            if observation.dirty is True:
                state.append("dirty")
            elif observation.dirty is False:
                state.append("clean")
            suffix = f" ({', '.join(state)})" if state else ""
            lines.append(
                f"- {observation.name}: {observation.repository} at {observation.path}{suffix}"
            )
            if observation.live_imu_consumption == "unaffected":
                lines.append(f"  OK unaffected: {observation.reason}")
                continue
            for reason in observation.non_authoritative_reasons:
                lines.append(f"  NON-AUTHORITATIVE {reason}")
            if observation.failures:
                for failure in observation.failures:
                    lines.append(f"  FAIL {failure.path}: {failure.message}")
            else:
                lines.append("  OK")
        return "\n".join(lines)


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected YAML object")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def _git(path: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _repo_dirty(path: Path) -> bool | None:
    status = _git(path, "status", "--short")
    if status is None:
        return None
    return bool(status)


def _contract_identity(root: Path) -> dict[str, Any]:
    identity = _load_yaml(root / "contracts" / "contract-identities.yaml")
    openapi = identity.get("openapi")
    if not isinstance(openapi, dict):
        raise ValueError("contract-identities.yaml: openapi identity must be an object")
    return openapi


def required_contract_descriptors(
    root: Path = ROOT,
    *,
    majors: list[int] | tuple[int, ...] | None = None,
) -> list[dict[str, Any]]:
    """Return the exact OpenAPI descriptors each consumer must declare."""

    openapi = _contract_identity(root)
    versions = openapi.get("versions")
    if not isinstance(versions, dict):
        raise ValueError("contract-identities.yaml: openapi.versions must be an object")
    selected = set(majors) if majors is not None else None
    descriptors = []
    for key in sorted(versions, key=lambda item: int(item.removeprefix("v"))):
        entry = versions[key]
        major = int(key.removeprefix("v"))
        if selected is not None and major not in selected:
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"contract-identities.yaml: openapi.versions.{key} must be an object")
        descriptors.append(
            {
                "major": major,
                "path": entry["path"],
                "sha256": entry["sha256"],
                "bytes": entry["bytes"],
                "info_version": entry["info_version"],
                "server_base_path": entry["server_base_path"],
                "lifecycle": entry["lifecycle"],
            }
        )
    if selected is not None and {item["major"] for item in descriptors} != selected:
        missing = sorted(selected - {item["major"] for item in descriptors})
        raise ValueError(f"contract-identities.yaml: unknown requested API majors {missing}")
    return descriptors


def _expected_by_major(root: Path) -> dict[int, dict[str, Any]]:
    return {item["major"]: item for item in required_contract_descriptors(root)}


def _resolve_consumer_path(
    consumer: dict[str, Any],
    overrides: dict[str, Path],
) -> Path:
    name = str(consumer["name"])
    if name in overrides:
        return overrides[name]
    for raw_path in consumer.get("default_paths", []):
        path = Path(str(raw_path))
        if path.exists():
            return path
    paths = ", ".join(str(item) for item in consumer.get("default_paths", []))
    return Path(paths or f"<missing:{name}>")


def _validate_probe(probe: Any, location: str) -> None:
    if not isinstance(probe, dict):
        raise ValueError(f"{location}: probe must be an object")
    probe_type = probe.get("type")
    if probe_type == "support_manifest":
        if set(probe) != {"type"}:
            raise ValueError(f"{location}: support_manifest probe has unexpected fields")
    elif probe_type == "openapi_exact":
        if set(probe) != {"type", "major", "path"}:
            raise ValueError(f"{location}: openapi_exact probe must contain type, major, path")
        if not isinstance(probe.get("major"), int) or isinstance(probe.get("major"), bool):
            raise ValueError(f"{location}: openapi_exact.major must be an integer")
        if not isinstance(probe.get("path"), str) or not probe["path"]:
            raise ValueError(f"{location}: openapi_exact.path must be a nonempty string")
    elif probe_type == "file_exact":
        if set(probe) != {"type", "path", "sha256", "bytes", "label"}:
            raise ValueError(
                f"{location}: file_exact probe must contain type, path, sha256, bytes, label"
            )
        if not isinstance(probe.get("path"), str) or not probe["path"]:
            raise ValueError(f"{location}: file_exact.path must be a nonempty string")
        if not isinstance(probe.get("sha256"), str) or re.fullmatch(
            r"[0-9a-f]{64}",
            probe["sha256"],
        ) is None:
            raise ValueError(f"{location}: file_exact.sha256 must be lowercase 64-char hex")
        if (
            not isinstance(probe.get("bytes"), int)
            or isinstance(probe.get("bytes"), bool)
            or probe["bytes"] <= 0
        ):
            raise ValueError(f"{location}: file_exact.bytes must be a positive integer")
        if not isinstance(probe.get("label"), str) or not probe["label"]:
            raise ValueError(f"{location}: file_exact.label must be a nonempty string")
    elif probe_type == "forbid_text":
        if set(probe) != {"type", "path", "text", "label"}:
            raise ValueError(f"{location}: forbid_text probe must contain type, path, text, label")
        for field_name in ("path", "text", "label"):
            if not isinstance(probe.get(field_name), str) or not probe[field_name]:
                raise ValueError(f"{location}: forbid_text.{field_name} must be a nonempty string")
    elif probe_type == "text_contains":
        if set(probe) != {"type", "path", "text", "label"}:
            raise ValueError(f"{location}: text_contains probe must contain type, path, text, label")
        for field_name in ("path", "text", "label"):
            if not isinstance(probe.get(field_name), str) or not probe[field_name]:
                raise ValueError(f"{location}: text_contains.{field_name} must be a nonempty string")
    elif probe_type == "typescript_exported_constant":
        if set(probe) != {"type", "path", "export", "value", "label"}:
            raise ValueError(
                f"{location}: typescript_exported_constant probe must contain "
                "type, path, export, value, label"
            )
        for field_name in ("path", "export", "value", "label"):
            if not isinstance(probe.get(field_name), str) or not probe[field_name]:
                raise ValueError(
                    f"{location}: typescript_exported_constant.{field_name} "
                    "must be a nonempty string"
                )
        if TS_IDENTIFIER.fullmatch(probe["export"]) is None:
            raise ValueError(
                f"{location}: typescript_exported_constant.export must be a TS identifier"
            )
    elif probe_type == "typescript_focus_contract":
        expected_fields = {
            "type",
            "client_path",
            "types_path",
            "route",
            "response_type",
            "request_schema",
            "runtime_field",
            "label",
        }
        if set(probe) != expected_fields:
            raise ValueError(
                f"{location}: typescript_focus_contract probe must contain "
                f"exactly {sorted(expected_fields)}"
            )
        for field_name in expected_fields - {"type"}:
            if not isinstance(probe.get(field_name), str) or not probe[field_name]:
                raise ValueError(
                    f"{location}: typescript_focus_contract.{field_name} "
                    "must be a nonempty string"
                )
        for field_name in ("response_type", "runtime_field"):
            if TS_IDENTIFIER.fullmatch(probe[field_name]) is None:
                raise ValueError(
                    f"{location}: typescript_focus_contract.{field_name} "
                    "must be a TS identifier"
                )
    else:
        raise ValueError(f"{location}: unknown probe type {probe_type!r}")


def load_consumer_matrix(root: Path = ROOT) -> dict[str, Any]:
    matrix = _load_yaml(root / "contracts" / "consumer-matrix.yaml")
    expected_top_fields = {
        "schema_version",
        "contract_identity",
        "support_manifest",
        "data_contracts",
        "consumers",
    }
    if set(matrix) != expected_top_fields:
        raise ValueError(
            "consumer-matrix.yaml: top-level fields must be exactly "
            f"{sorted(expected_top_fields)}"
        )
    if matrix.get("schema_version") != "ylx.device-api-consumer-matrix.v3":
        raise ValueError("consumer-matrix.yaml: unexpected schema_version")
    if matrix.get("contract_identity") != "contracts/contract-identities.yaml":
        raise ValueError("consumer-matrix.yaml: contract_identity drifted")
    if matrix.get("support_manifest") != SUPPORT_MANIFEST.as_posix():
        raise ValueError("consumer-matrix.yaml: support_manifest drifted")
    consumers = matrix.get("consumers")
    if not isinstance(consumers, list) or not consumers:
        raise ValueError("consumer-matrix.yaml: consumers must be a nonempty array")

    names: list[str] = []
    supported_majors = set(_expected_by_major(root))
    for index, consumer in enumerate(consumers):
        location = f"consumer-matrix.yaml: consumers[{index}]"
        if not isinstance(consumer, dict):
            raise ValueError(f"{location}: consumer must be an object")
        name = consumer.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{location}: name must be a nonempty string")
        names.append(name)
        repository = consumer.get("repository")
        if not isinstance(repository, str) or not repository:
            raise ValueError(f"{location}: repository must be a nonempty string")
        if consumer.get("live_imu_consumption") == "unaffected":
            allowed = {"name", "repository", "live_imu_consumption", "reason"}
            if set(consumer) != allowed:
                raise ValueError(f"{location}: unaffected consumer fields drifted")
            reason = consumer.get("reason")
            if not isinstance(reason, str) or len(reason.strip()) < 20:
                raise ValueError(f"{location}: unaffected consumer reason is required")
            continue

        allowed = {
            "name",
            "repository",
            "device_api_role",
            "supported_device_api_majors",
            "unknown_major_policy",
            "default_paths",
            "probes",
        }
        if set(consumer) != allowed:
            raise ValueError(f"{location}: active consumer fields drifted")
        if consumer.get("device_api_role") not in {"producer", "raw_live_imu_consumer"}:
            raise ValueError(f"{location}: unsupported device_api_role")
        majors = consumer.get("supported_device_api_majors")
        if (
            not isinstance(majors, list)
            or not majors
            or any(not isinstance(major, int) or isinstance(major, bool) for major in majors)
            or majors != sorted(majors)
            or len(majors) != len(set(majors))
            or not set(majors) <= supported_majors
        ):
            raise ValueError(f"{location}: supported_device_api_majors drifted")
        if consumer.get("unknown_major_policy") != "fail_closed":
            raise ValueError(f"{location}: unknown_major_policy must be fail_closed")
        default_paths = consumer.get("default_paths")
        if not isinstance(default_paths, list) or not all(
            isinstance(path, str) and path for path in default_paths
        ):
            raise ValueError(f"{location}: default_paths must be nonempty strings")
        probes = consumer.get("probes")
        if not isinstance(probes, list) or not probes:
            raise ValueError(f"{location}: probes must be a nonempty array")
        support_probe_count = 0
        openapi_probe_majors: list[int] = []
        file_exact_paths: list[str] = []
        for probe_index, probe in enumerate(probes):
            _validate_probe(probe, f"{location}.probes[{probe_index}]")
            if probe["type"] == "support_manifest":
                support_probe_count += 1
            elif probe["type"] == "openapi_exact":
                openapi_probe_majors.append(probe["major"])
            elif probe["type"] == "file_exact":
                file_exact_paths.append(probe["path"])
        if support_probe_count != 1:
            raise ValueError(f"{location}: exactly one support_manifest probe is required")
        if len(file_exact_paths) != len(set(file_exact_paths)):
            raise ValueError(f"{location}: duplicate file_exact probe paths")
        if consumer["device_api_role"] == "producer" and sorted(openapi_probe_majors) != majors:
            raise ValueError(f"{location}: producers must pin exact OpenAPI bytes for every supported major")
        if consumer["device_api_role"] == "raw_live_imu_consumer" and openapi_probe_majors:
            raise ValueError(f"{location}: raw consumers use the support manifest for exact contract identity")
        if consumer["device_api_role"] == "raw_live_imu_consumer" and "src/api/client.ts" not in file_exact_paths:
            raise ValueError(f"{location}: raw consumers must pin exact src/api/client.ts source identity")
    if len(names) != len(set(names)):
        duplicates = sorted(name for name in set(names) if names.count(name) > 1)
        raise ValueError(f"consumer-matrix.yaml: duplicate consumer names {duplicates}")
    data_contracts = matrix.get("data_contracts")
    if not isinstance(data_contracts, list) or not data_contracts:
        raise ValueError("consumer-matrix.yaml: data_contracts must be a nonempty array")
    data_contract_names: list[str] = []
    allowed_statuses = {"producer_pending", "consumer_pending", "unsupported"}
    for index, contract in enumerate(data_contracts):
        location = f"consumer-matrix.yaml: data_contracts[{index}]"
        if not isinstance(contract, dict):
            raise ValueError(f"{location}: contract must be an object")
        if set(contract) != {"schema", "status", "unknown_major_policy", "consumers"}:
            raise ValueError(f"{location}: data contract fields drifted")
        schema = contract.get("schema")
        if schema not in {"ylx.device-session.v2", "ylx.bucket-publication.v3"}:
            raise ValueError(f"{location}: unknown data contract schema")
        data_contract_names.append(schema)
        if contract.get("status") != "pending":
            raise ValueError(f"{location}: new data contracts must remain pending")
        if contract.get("unknown_major_policy") != "fail_closed":
            raise ValueError(f"{location}: unknown_major_policy must be fail_closed")
        contract_consumers = contract.get("consumers")
        if not isinstance(contract_consumers, list) or not contract_consumers:
            raise ValueError(f"{location}: consumers must be a nonempty array")
        data_consumer_names: list[str] = []
        for consumer_index, consumer in enumerate(contract_consumers):
            consumer_location = f"{location}.consumers[{consumer_index}]"
            if not isinstance(consumer, dict):
                raise ValueError(f"{consumer_location}: consumer must be an object")
            if set(consumer) != {"name", "status", "reason"}:
                raise ValueError(f"{consumer_location}: consumer fields drifted")
            name = consumer.get("name")
            if name not in names:
                raise ValueError(f"{consumer_location}: unknown consumer name {name!r}")
            data_consumer_names.append(name)
            if consumer.get("status") not in allowed_statuses:
                raise ValueError(f"{consumer_location}: unsupported data contract status")
            reason = consumer.get("reason")
            if not isinstance(reason, str) or len(reason.strip()) < 20:
                raise ValueError(f"{consumer_location}: reason is required")
        if set(data_consumer_names) != set(names) or len(data_consumer_names) != len(set(data_consumer_names)):
            raise ValueError(f"{location}: data contract must list every consumer exactly once")
    if data_contract_names != ["ylx.device-session.v2", "ylx.bucket-publication.v3"]:
        raise ValueError("consumer-matrix.yaml: data contract coverage drifted")
    return matrix


def _fail(
    observation: ConsumerObservation,
    relative: Path | str,
    message: str,
) -> None:
    observation.failures.append(
        ProbeFailure(observation.name, Path(relative), message)
    )


def _check_support_manifest(
    root: Path,
    repo: Path,
    observation: ConsumerObservation,
    consumer: dict[str, Any],
) -> None:
    relative = SUPPORT_MANIFEST
    path = repo / relative
    if not path.exists():
        _fail(observation, relative, "contracts/ylx-device-api-support.json missing")
        return
    try:
        support = _load_json(path)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        _fail(observation, relative, f"support manifest is not valid JSON: {error}")
        return

    if support.get("schema") != "ylx.device-api-consumer-support.v1":
        _fail(observation, relative, "support manifest schema must be ylx.device-api-consumer-support.v1")
    if support.get("consumer") != observation.name:
        _fail(observation, relative, f"consumer must be {observation.name}")
    expected_majors = consumer.get("supported_device_api_majors")
    if support.get("supported_device_api_majors") != expected_majors:
        _fail(
            observation,
            relative,
            f"supported_device_api_majors must be {expected_majors}; unknown majors fail closed",
        )
    expected_policy = consumer.get("unknown_major_policy")
    if support.get("unknown_major_policy") != expected_policy:
        _fail(observation, relative, f"unknown_major_policy must be {expected_policy}")
    if support.get("required_contracts") != required_contract_descriptors(
        root,
        majors=expected_majors,
    ):
        _fail(
            observation,
            relative,
            f"required_contracts do not match central exact identities for majors {expected_majors}",
        )


def _check_openapi_exact(
    repo: Path,
    observation: ConsumerObservation,
    probe: dict[str, Any],
    expected: dict[int, dict[str, Any]],
) -> None:
    major = int(probe["major"])
    relative = Path(str(probe["path"]))
    path = repo / relative
    if major not in expected:
        _fail(observation, relative, f"unknown central API major {major}")
        return
    if not path.exists():
        _fail(observation, relative, f"missing exact Device API v{major} contract copy")
        return
    raw = path.read_bytes()
    actual_sha = hashlib.sha256(raw).hexdigest()
    expected_entry = expected[major]
    if actual_sha != expected_entry["sha256"] or len(raw) != expected_entry["bytes"]:
        _fail(
            observation,
            relative,
            "exact bytes drift: "
            f"sha256={actual_sha} bytes={len(raw)} expected "
            f"sha256={expected_entry['sha256']} bytes={expected_entry['bytes']}",
        )


def _check_file_exact(
    repo: Path,
    observation: ConsumerObservation,
    probe: dict[str, Any],
) -> None:
    relative = Path(str(probe["path"]))
    path = repo / relative
    if not path.exists():
        _fail(observation, relative, "missing file for exact source identity probe")
        return
    raw = path.read_bytes()
    actual_sha = hashlib.sha256(raw).hexdigest()
    actual_bytes = len(raw)
    expected_sha = str(probe["sha256"])
    expected_bytes = int(probe["bytes"])
    if actual_sha != expected_sha or actual_bytes != expected_bytes:
        _fail(
            observation,
            relative,
            "exact source identity drift: "
            f"sha256={actual_sha} bytes={actual_bytes} expected "
            f"sha256={expected_sha} bytes={expected_bytes}; {probe['label']}",
        )


def _check_forbid_text(
    repo: Path,
    observation: ConsumerObservation,
    probe: dict[str, Any],
) -> None:
    relative = Path(str(probe["path"]))
    path = repo / relative
    if not path.exists():
        _fail(observation, relative, "file missing for legacy drift probe")
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    needle = str(probe["text"])
    if needle in text:
        _fail(observation, relative, str(probe.get("label") or f"forbidden text {needle!r} present"))


def _check_text_contains(
    repo: Path,
    observation: ConsumerObservation,
    probe: dict[str, Any],
) -> None:
    relative = Path(str(probe["path"]))
    path = repo / relative
    if not path.exists():
        _fail(observation, relative, "file missing for routing probe")
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    needle = str(probe["text"])
    if needle not in text:
        _fail(observation, relative, str(probe.get("label") or f"expected {needle!r}"))


def _typescript_node_has_missing(node: Any) -> bool:
    if node.is_missing:
        return True
    return any(_typescript_node_has_missing(child) for child in node.children)


def _decode_typescript_string_literal(raw: str) -> str | None:
    if len(raw) < 2 or raw[0] not in {"'", '"'} or raw[-1] != raw[0]:
        return None
    quote = raw[0]
    index = 1
    value: list[str] = []
    end = len(raw) - 1
    escapes = {
        "0": "\0",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "v": "\v",
        quote: quote,
        "\\": "\\",
        "/": "/",
    }
    while index < end:
        char = raw[index]
        if char in "\r\n":
            return None
        if char != "\\":
            value.append(char)
            index += 1
            continue
        index += 1
        if index >= end:
            return None
        escaped = raw[index]
        if escaped in escapes:
            value.append(escapes[escaped])
            index += 1
            continue
        if escaped in "\r\n":
            if escaped == "\r" and index + 1 < end and raw[index + 1] == "\n":
                index += 2
            else:
                index += 1
            continue
        if escaped == "x":
            digits = raw[index + 1 : index + 3]
            if len(digits) != 2 or any(char not in "0123456789abcdefABCDEF" for char in digits):
                return None
            value.append(chr(int(digits, 16)))
            index += 3
            continue
        if escaped == "u":
            if index + 1 < end and raw[index + 1] == "{":
                close = raw.find("}", index + 2, end)
                if close == -1:
                    return None
                digits = raw[index + 2 : close]
                if not digits or any(char not in "0123456789abcdefABCDEF" for char in digits):
                    return None
                codepoint = int(digits, 16)
                if codepoint > 0x10FFFF:
                    return None
                value.append(chr(codepoint))
                index = close + 1
                continue
            digits = raw[index + 1 : index + 5]
            if len(digits) != 4 or any(char not in "0123456789abcdefABCDEF" for char in digits):
                return None
            value.append(chr(int(digits, 16)))
            index += 5
            continue
        return None
    return "".join(value)


def _node_text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _parse_typescript_program(text: str) -> tuple[bytes, Any] | None:
    source = text.encode("utf-8", errors="replace")
    parser = Parser(TS_LANGUAGE)
    tree = parser.parse(source)
    root = tree.root_node
    if root.has_error or _typescript_node_has_missing(root):
        return None
    return source, root


def _walk_typescript(node: Any) -> list[Any]:
    nodes = [node]
    for child in node.children:
        nodes.extend(_walk_typescript(child))
    return nodes


def _typescript_string_value(node: Any, source: bytes) -> str | None:
    if node.type != "string":
        return None
    return _decode_typescript_string_literal(_node_text(node, source))


def _typescript_top_level_exported_string_constant_values(
    text: str,
    export_name: str,
) -> list[str | None] | None:
    parsed = _parse_typescript_program(text)
    if parsed is None:
        return None
    source, root = parsed

    values: list[str | None] = []
    for statement in root.children:
        if statement.type != "export_statement" or not statement.is_named:
            continue
        declaration = statement.child_by_field_name("declaration")
        if declaration is None or declaration.type != "lexical_declaration":
            continue
        kind = declaration.child_by_field_name("kind")
        if kind is None or _node_text(kind, source) != "const":
            continue
        declarators = [
            child
            for child in declaration.named_children
            if child.type == "variable_declarator"
        ]
        for declarator in declarators:
            name = declarator.child_by_field_name("name")
            if name is None or name.type != "identifier":
                continue
            if _node_text(name, source) != export_name:
                continue
            value = declarator.child_by_field_name("value")
            if len(declarators) != 1 or value is None or value.type != "string":
                values.append(None)
                continue
            values.append(_decode_typescript_string_literal(_node_text(value, source)))
    return values


def _typescript_call_function_name(call: Any, source: bytes) -> str | None:
    function = call.child_by_field_name("function")
    if function is None or function.type != "identifier":
        return None
    return _node_text(function, source)


def _typescript_call_type_argument(call: Any, source: bytes) -> str | None:
    type_arguments = call.child_by_field_name("type_arguments")
    if type_arguments is None:
        return None
    named = [child for child in type_arguments.named_children]
    if len(named) != 1 or named[0].type != "type_identifier":
        return None
    return _node_text(named[0], source)


def _typescript_call_argument_nodes(call: Any) -> list[Any]:
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        return []
    return list(arguments.named_children)


def _typescript_object_string_property(
    object_node: Any,
    source: bytes,
    property_name: str,
) -> str | None:
    if object_node.type != "object":
        return None
    for child in object_node.named_children:
        if child.type != "pair":
            continue
        key = child.child_by_field_name("key")
        value = child.child_by_field_name("value")
        if key is None or value is None:
            continue
        if key.type not in {"property_identifier", "string"}:
            continue
        if key.type == "string":
            actual_key = _typescript_string_value(key, source)
        else:
            actual_key = _node_text(key, source)
        if actual_key == property_name:
            return _typescript_string_value(value, source)
    return None


def _typescript_command_init_schema(call: Any, source: bytes) -> str | None:
    if _typescript_call_function_name(call, source) != "commandInit":
        return None
    arguments = _typescript_call_argument_nodes(call)
    if len(arguments) != 1:
        return None
    return _typescript_object_string_property(arguments[0], source, "schema")


def _typescript_object_property_value(
    object_node: Any,
    source: bytes,
    property_name: str,
) -> Any | None:
    if object_node.type != "object":
        return None
    matches = []
    for child in object_node.named_children:
        if child.type != "pair":
            continue
        key = child.child_by_field_name("key")
        value = child.child_by_field_name("value")
        if key is None or value is None:
            continue
        if key.type == "string":
            actual_key = _typescript_string_value(key, source)
        elif key.type == "property_identifier":
            actual_key = _node_text(key, source)
        else:
            continue
        if actual_key == property_name:
            matches.append(value)
    if len(matches) != 1:
        return None
    return matches[0]


def _typescript_ordinary_object_members(
    object_node: Any,
    source: bytes,
) -> dict[str, Any] | None:
    if object_node.type != "object":
        return None
    members: dict[str, Any] = {}
    for child in object_node.named_children:
        if child.type == "comment":
            continue
        if child.type != "pair":
            return None
        key = child.child_by_field_name("key")
        value = child.child_by_field_name("value")
        if key is None or value is None:
            return None
        if key.type == "property_identifier":
            actual_key = _node_text(key, source)
        elif key.type == "string":
            actual_key = _typescript_string_value(key, source)
        else:
            return None
        if actual_key is None or actual_key in members:
            return None
        members[actual_key] = value
    return members


def _typescript_subtree_has_identifier(node: Any, source: bytes, identifier: str) -> bool:
    return any(
        child.type == "identifier" and _node_text(child, source) == identifier
        for child in _walk_typescript(node)
    )


def _typescript_declares_or_imports_binding(
    root: Any,
    source: bytes,
    identifier: str,
) -> bool:
    for node in _walk_typescript(root):
        if node.type == "import_statement":
            if _typescript_subtree_has_identifier(node, source, identifier):
                return True
            continue
        if node.type in {
            "variable_declarator",
            "function_declaration",
            "class_declaration",
            "interface_declaration",
            "type_alias_declaration",
            "enum_declaration",
            "required_parameter",
            "optional_parameter",
        }:
            name = node.child_by_field_name("name")
            if name is not None and _typescript_subtree_has_identifier(
                name,
                source,
                identifier,
            ):
                return True
    return False


def _typescript_direct_object_freeze_argument(
    call: Any,
    source: bytes,
) -> Any | None:
    if call.type != "call_expression":
        return None
    function = call.child_by_field_name("function")
    arguments = call.child_by_field_name("arguments")
    if function is None or arguments is None:
        return None
    if source[function.end_byte : arguments.start_byte] != b"":
        return None
    if function.type != "member_expression" or _node_text(function, source) != "Object.freeze":
        return None
    named_children = list(function.named_children)
    if len(named_children) != 2:
        return None
    object_node, property_node = named_children
    if object_node.type != "identifier" or _node_text(object_node, source) != "Object":
        return None
    if property_node.type != "property_identifier" or _node_text(property_node, source) != "freeze":
        return None
    argument_nodes = _typescript_call_argument_nodes(call)
    if len(argument_nodes) != 1 or argument_nodes[0].type != "object":
        return None
    return argument_nodes[0]


def _typescript_top_level_exported_object_constant(
    text: str,
    export_name: str,
) -> tuple[bytes, dict[str, Any]] | None:
    parsed = _parse_typescript_program(text)
    if parsed is None:
        return None
    source, root = parsed
    if _typescript_declares_or_imports_binding(root, source, "Object"):
        return None

    values: list[dict[str, Any] | None] = []
    for statement in root.children:
        if statement.type != "export_statement" or not statement.is_named:
            continue
        declaration = statement.child_by_field_name("declaration")
        if declaration is None or declaration.type != "lexical_declaration":
            continue
        kind = declaration.child_by_field_name("kind")
        if kind is None or _node_text(kind, source) != "const":
            continue
        declarators = [
            child
            for child in declaration.named_children
            if child.type == "variable_declarator"
        ]
        for declarator in declarators:
            name = declarator.child_by_field_name("name")
            if name is None or name.type != "identifier":
                continue
            if _node_text(name, source) != export_name:
                continue
            value = declarator.child_by_field_name("value")
            if len(declarators) != 1 or value is None:
                values.append(None)
                continue
            object_argument = _typescript_direct_object_freeze_argument(value, source)
            if object_argument is None:
                values.append(None)
                continue
            values.append(_typescript_ordinary_object_members(object_argument, source))
    if len(values) != 1 or values[0] is None:
        return None
    return source, values[0]


def _typescript_focus_call_matches(
    node: Any,
    source: bytes,
    *,
    function_name: str,
    route: str,
    response_type: str,
    request_schema: str | None = None,
) -> bool:
    if node.type != "call_expression":
        return False
    if _typescript_call_function_name(node, source) != function_name:
        return False
    if _typescript_call_type_argument(node, source) != response_type:
        return False
    arguments = _typescript_call_argument_nodes(node)
    if not arguments:
        return False
    if _typescript_string_value(arguments[0], source) != route:
        return False
    if function_name == "requestOptionalJson":
        return len(arguments) == 1
    if function_name == "requestJson":
        return (
            len(arguments) == 2
            and _typescript_command_init_schema(arguments[1], source) == request_schema
        )
    return False


def _typescript_function_direct_return_expression(function_node: Any) -> Any | None:
    if function_node.type not in {"arrow_function", "function_expression"}:
        return None
    body = function_node.child_by_field_name("body")
    if body is None:
        named_children = [
            child
            for child in function_node.named_children
            if child.type
            not in {
                "formal_parameters",
                "type_parameters",
            }
        ]
        if not named_children:
            return None
        body = named_children[-1]
    if body.type != "statement_block":
        return body
    direct_statements = list(body.named_children)
    if len(direct_statements) != 1 or direct_statements[0].type != "return_statement":
        return None
    expressions = [
        child
        for child in direct_statements[0].named_children
        if child.type != "return"
    ]
    if len(expressions) != 1:
        return None
    return expressions[0]


def _typescript_focus_member_returns_call(
    member_value: Any,
    source: bytes,
    *,
    function_name: str,
    route: str,
    response_type: str,
    request_schema: str | None = None,
) -> bool:
    returned = _typescript_function_direct_return_expression(member_value)
    if returned is None:
        return False
    return _typescript_focus_call_matches(
        returned,
        source,
        function_name=function_name,
        route=route,
        response_type=response_type,
        request_schema=request_schema,
    )


def _typescript_focus_route_calls(
    text: str,
    *,
    route: str,
    response_type: str,
    request_schema: str,
) -> tuple[bool, bool] | None:
    exported_device_api = _typescript_top_level_exported_object_constant(
        text,
        "deviceApi",
    )
    if exported_device_api is None:
        return None
    source, device_api = exported_device_api
    get_camera_focus = device_api.get("getCameraFocus")
    set_camera_focus = device_api.get("setCameraFocus")
    get_ok = get_camera_focus is not None and _typescript_focus_member_returns_call(
        get_camera_focus,
        source,
        function_name="requestOptionalJson",
        route=route,
        response_type=response_type,
    )
    post_ok = set_camera_focus is not None and _typescript_focus_member_returns_call(
        set_camera_focus,
        source,
        function_name="requestJson",
        route=route,
        response_type=response_type,
        request_schema=request_schema,
    )
    return get_ok, post_ok


def _typescript_top_level_exported_interface(
    text: str,
    interface_name: str,
) -> tuple[bytes, Any] | None:
    parsed = _parse_typescript_program(text)
    if parsed is None:
        return None
    source, root = parsed
    matches = []
    for statement in root.children:
        if statement.type != "export_statement" or not statement.is_named:
            continue
        declaration = statement.child_by_field_name("declaration")
        if declaration is None or declaration.type != "interface_declaration":
            continue
        name = declaration.child_by_field_name("name")
        if name is not None and _node_text(name, source) == interface_name:
            matches.append(declaration)
    if len(matches) != 1:
        return None
    return source, matches[0]


def _typescript_interface_properties(interface: Any, source: bytes) -> dict[str, Any]:
    body = interface.child_by_field_name("body")
    if body is None:
        return {}
    properties: dict[str, Any] = {}
    for child in body.named_children:
        if child.type != "property_signature":
            continue
        name = child.child_by_field_name("name")
        if name is None:
            continue
        properties[_node_text(name, source)] = child
    return properties


def _typescript_property_type_text(property_node: Any, source: bytes) -> str | None:
    annotation = property_node.child_by_field_name("type")
    if annotation is None:
        return None
    text = _node_text(annotation, source).strip()
    if text.startswith(":"):
        text = text[1:].strip()
    return "".join(text.split())


def _typescript_property_is_optional(property_node: Any, source: bytes) -> bool:
    return any(
        not child.is_named and _node_text(child, source) == "?"
        for child in property_node.children
    )


def _typescript_property_literal_string(property_node: Any, source: bytes) -> str | None:
    annotation = property_node.child_by_field_name("type")
    if annotation is None:
        return None
    for node in _walk_typescript(annotation):
        if node.type == "string":
            return _typescript_string_value(node, source)
    return None


def _typescript_focus_types_ok(
    text: str,
    *,
    response_type: str,
    runtime_field: str,
) -> bool | None:
    focus_interface = _typescript_top_level_exported_interface(text, response_type)
    runtime_interface = _typescript_top_level_exported_interface(text, "DeviceRuntime")
    if focus_interface is None or runtime_interface is None:
        return None

    focus_source, focus_node = focus_interface
    focus_properties = _typescript_interface_properties(focus_node, focus_source)
    expected_focus_properties = {
        "schema",
        "value",
        "minimum",
        "maximum",
        "step",
        "default",
        "auto_supported",
        "auto_enabled",
    }
    if set(focus_properties) != expected_focus_properties:
        return False
    if any(
        _typescript_property_is_optional(focus_properties[name], focus_source)
        for name in expected_focus_properties
    ):
        return False
    if _typescript_property_literal_string(focus_properties["schema"], focus_source) != (
        "ylx.camera-focus.v1"
    ):
        return False
    for name in ("value", "minimum", "maximum", "step", "default"):
        if _typescript_property_type_text(focus_properties[name], focus_source) != "number":
            return False
    if _typescript_property_type_text(focus_properties["auto_supported"], focus_source) != "boolean":
        return False
    auto_enabled = _typescript_property_type_text(focus_properties["auto_enabled"], focus_source)
    if auto_enabled not in {"boolean|null", "null|boolean"}:
        return False

    runtime_source, runtime_node = runtime_interface
    runtime_properties = _typescript_interface_properties(runtime_node, runtime_source)
    if runtime_field not in runtime_properties:
        return False
    if _typescript_property_is_optional(runtime_properties[runtime_field], runtime_source):
        return False
    runtime_type = _typescript_property_type_text(runtime_properties[runtime_field], runtime_source)
    return runtime_type in {f"{response_type}|null", f"null|{response_type}"}


def _check_typescript_exported_constant(
    repo: Path,
    observation: ConsumerObservation,
    probe: dict[str, Any],
) -> None:
    relative = Path(str(probe["path"]))
    path = repo / relative
    if not path.exists():
        _fail(observation, relative, "file missing for TypeScript constant probe")
        return
    export_name = str(probe["export"])
    expected_value = str(probe["value"])
    values = _typescript_top_level_exported_string_constant_values(
        path.read_text(encoding="utf-8", errors="replace"),
        export_name,
    )
    if values is None:
        _fail(
            observation,
            relative,
            f"expected exactly one module-level exported const {export_name} "
            f"assigned {expected_value!r}; TypeScript parse error or missing AST node",
        )
        return
    if len(values) != 1:
        _fail(
            observation,
            relative,
            f"expected exactly one module-level exported const {export_name} "
            f"assigned {expected_value!r}; found {len(values)}",
        )
        return
    if values[0] != expected_value:
        _fail(
            observation,
            relative,
            f"exported const {export_name} expected {expected_value}, found {values[0]}",
        )


def _check_typescript_focus_contract(
    repo: Path,
    observation: ConsumerObservation,
    probe: dict[str, Any],
) -> None:
    client_relative = Path(str(probe["client_path"]))
    types_relative = Path(str(probe["types_path"]))
    client_path = repo / client_relative
    types_path = repo / types_relative
    route = str(probe["route"])
    response_type = str(probe["response_type"])
    request_schema = str(probe["request_schema"])
    runtime_field = str(probe["runtime_field"])

    if not client_path.exists():
        _fail(observation, client_relative, "file missing for TypeScript focus route probe")
        return
    if not types_path.exists():
        _fail(observation, types_relative, "file missing for TypeScript focus type probe")
        return

    route_calls = _typescript_focus_route_calls(
        client_path.read_text(encoding="utf-8", errors="replace"),
        route=route,
        response_type=response_type,
        request_schema=request_schema,
    )
    if route_calls is None:
        _fail(
            observation,
            client_relative,
            f"expected exported const deviceApi = Object.freeze({{...}}) with real "
            f"{route} focus members; TypeScript parse error, missing AST node, "
            "duplicate export, shadowed Object, or non-frozen/non-object value",
        )
    else:
        get_ok, post_ok = route_calls
        if not get_ok:
            _fail(
                observation,
                client_relative,
                f"expected exported deviceApi.getCameraFocus member to call "
                f"requestOptionalJson<{response_type}>({route!r}) focus GET route",
            )
        if not post_ok:
            _fail(
                observation,
                client_relative,
                f"expected exported deviceApi.setCameraFocus member to call "
                f"requestJson<{response_type}>({route!r}, "
                f"commandInit({{schema: {request_schema!r}, ...}})) focus POST route",
            )

    type_ok = _typescript_focus_types_ok(
        types_path.read_text(encoding="utf-8", errors="replace"),
        response_type=response_type,
        runtime_field=runtime_field,
    )
    if type_ok is None:
        _fail(
            observation,
            types_relative,
            f"expected exported {response_type} and DeviceRuntime; "
            "TypeScript parse error, missing AST node, or duplicate/missing interface",
        )
    elif not type_ok:
        _fail(
            observation,
            types_relative,
            f"expected {response_type} schema ylx.camera-focus.v1 and "
            f"DeviceRuntime.{runtime_field}: {response_type} | null",
        )


def evaluate(
    root: Path = ROOT,
    consumer_paths: dict[str, Path] | None = None,
    selected_consumers: tuple[str, ...] | None = None,
    allow_dirty: bool = False,
) -> GateResult:
    root = root.resolve()
    matrix = load_consumer_matrix(root)
    consumers = matrix["consumers"]

    overrides = consumer_paths or {}
    selected = set(selected_consumers or ())
    expected = _expected_by_major(root)
    observations: list[ConsumerObservation] = []
    for consumer in consumers:
        name = str(consumer.get("name"))
        if selected and name not in selected:
            continue
        if consumer.get("live_imu_consumption") == "unaffected":
            observations.append(
                ConsumerObservation(
                    name=name,
                    repository=str(consumer.get("repository", "")),
                    path=Path("<not-applicable>"),
                    live_imu_consumption="unaffected",
                    reason=str(consumer.get("reason", "")),
                )
            )
            continue
        repo = _resolve_consumer_path(consumer, overrides)
        dirty = _repo_dirty(repo) if repo.exists() else None
        observation = ConsumerObservation(
            name=name,
            repository=str(consumer.get("repository", "")),
            path=repo,
            head=_git(repo, "rev-parse", "HEAD") if repo.exists() else None,
            dirty=dirty,
        )
        observations.append(observation)
        if not repo.exists():
            _fail(observation, ".", "consumer repository path does not exist")
            continue
        if dirty is None:
            _fail(observation, ".", "git status unavailable; refusing non-repository or unreadable state")
            continue
        if dirty:
            if allow_dirty:
                observation.non_authoritative_reasons.append(
                    "dirty worktree accepted because --allow-dirty was set"
                )
            else:
                _fail(
                    observation,
                    ".",
                    "dirty worktree rejected; use --allow-dirty only for non-authoritative local investigation",
                )
                continue
        probes = consumer["probes"]
        for probe in probes:
            probe_type = probe.get("type")
            if probe_type == "support_manifest":
                _check_support_manifest(root, repo, observation, consumer)
            elif probe_type == "openapi_exact":
                _check_openapi_exact(repo, observation, probe, expected)
            elif probe_type == "file_exact":
                _check_file_exact(repo, observation, probe)
            elif probe_type == "forbid_text":
                _check_forbid_text(repo, observation, probe)
            elif probe_type == "text_contains":
                _check_text_contains(repo, observation, probe)
            elif probe_type == "typescript_exported_constant":
                _check_typescript_exported_constant(repo, observation, probe)
            elif probe_type == "typescript_focus_contract":
                _check_typescript_focus_contract(repo, observation, probe)
            else:
                _fail(observation, ".", f"unknown probe type {probe_type!r}")
    if selected and {observation.name for observation in observations} != selected:
        missing = sorted(selected - {observation.name for observation in observations})
        for name in missing:
            observations.append(
                ConsumerObservation(
                    name=name,
                    repository="",
                    path=Path("."),
                    failures=[ProbeFailure(name, Path("."), "selected consumer not in matrix")],
                )
            )
    return GateResult(observations)


def _parse_consumer_overrides(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--consumer must be name=path, got {value!r}")
        name, raw_path = value.split("=", 1)
        if not name or not raw_path:
            raise SystemExit(f"--consumer must be name=path, got {value!r}")
        result[name] = Path(raw_path)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="pi-dev repository root containing contracts/",
    )
    parser.add_argument(
        "--consumer",
        action="append",
        default=[],
        help="Override a consumer path as name=/path/to/repo",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="Only evaluate the named consumer; may be repeated",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help=(
            "Allow dirty consumer worktrees for local investigation. "
            "Passing results are marked NON-AUTHORITATIVE."
        ),
    )
    args = parser.parse_args()
    result = evaluate(
        args.root,
        _parse_consumer_overrides(args.consumer),
        tuple(args.only) or None,
        allow_dirty=args.allow_dirty,
    )
    print(result.to_text())
    if not result.ok:
        raise SystemExit(1)
    if not result.authoritative:
        raise SystemExit(2)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
