#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "cryptography>=44,<46",
#   "jsonschema[format]>=4.23,<5",
#   "PyYAML>=6,<7",
#   "rfc8785>=0.1.4,<1",
# ]
# ///
"""Generate the closed, deterministic governance-model fixture corpus.

All signing material in this file is deliberately derived from public labels.
It is test-only material and is not a production secret.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import rfc8785
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

sys.dont_write_bytecode = True


NOTICE = "SYNTHETIC EXAMPLE ONLY; NOT EVIDENCE; NOT A PASS VERDICT"
FIXTURE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = FIXTURE_ROOT.parents[2]
SCHEMA_ROOT = REPO_ROOT / "contracts" / "governance-schemas"
VALID_ROOT = FIXTURE_ROOT / "valid"
SUPPORT_ROOT = FIXTURE_ROOT / "support"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from delivery_planning_calculator import (  # noqa: E402
    CALCULATOR_ID as DELIVERY_PLANNING_CALCULATOR_ID,
    CALCULATOR_VERSION as DELIVERY_PLANNING_CALCULATOR_VERSION,
    SCHEDULING_RULES_VERSION as DELIVERY_PLANNING_RULES_VERSION,
    calculate_delivery_planning,
)

STAMP = "2026-06-01T12:00:00Z"
VALID_FROM = "2026-01-01T00:00:00Z"
NOT_AFTER = "2027-01-01T00:00:00Z"
ROLES = [
    "release-owner",
    "contract-owner",
    "capture-owner",
    "consumer-owner",
    "web-control-owner",
    "network-owner",
    "security-owner",
    "build-platform-owner",
    "qa-evidence-owner",
    "hardware-owner",
    "pipeline-owner",
]
QUORUM_ROLES = [
    "release-owner",
    "contract-owner",
    "security-owner",
    "qa-evidence-owner",
]
COMPONENTS = ["web", "network", "preview", "transfer-calibration"]
BOUNDARIES = [
    "rp-ylx-target-session-reader",
    "rp-ylx-v1-projection-adapter",
    "spectacular-calibration-reader",
    "ylx-card-pipeline",
    "ylx-transfer",
    "egoview-console",
    "g3-egoview-v5-wrapper",
]
FRESHNESS_CHECKPOINTS = [
    "quorum_signature_collection",
    "publication_fence_acquisition",
    "pre_promotion",
    "promotion_readback",
    "final_manifest_publish",
    "final_manifest_readback",
    "finalized_terminal_reference_cas",
    "finalized_terminal_reference_readback",
]
M5_EXECUTION_PHASES = [
    "pre_canary",
    "canary_entry",
    "canary_validation",
    "post_canary",
    "release_qualification",
    "matrix_closure",
    "signoff",
]
ISSUE_REQUIREMENT_BY_GATE = {
    "M0": "M0-ISSUES-01",
    "M1": "M1-ISSUES-01",
    "M2": "M2-ISSUES-01",
    "M3": "M3-ISSUES-01",
    "M4a": "M4A-ISSUES-01",
    "M4b": "M4B-ISSUES-01",
    "M4c": "M4C-ISSUES-01",
    "M4d": "M4D-ISSUES-01",
    "M4": "M4-ISSUES-01",
    "M5": "M5-ISSUES-01",
}
FRESHNESS_CHECKPOINT_RULE = (
    "at every checkpoint revalidate the exact issue-reconciliation set, binding context "
    "and all three contract roots, role assignments, signing/quorum/key heads, "
    "domain-attestation map, promotion plan/operator, RC/GA target, fence inputs, and "
    "every already-created receipt or payload; the CAS checkpoint runs immediately "
    "before the write and the readback checkpoint runs immediately after durable "
    "readback, with no omitted or mixed head"
)
TERMINAL_DRIFT_RULE = (
    "any mismatch before finalized_terminal_reference_cas must prevent FINALIZED and "
    "use the exact ABORTED/quarantine path; a mismatch after a FINALIZED CAS but before "
    "valid finalized_terminal_reference_readback cannot derive M5-SIGNOFF PASS or "
    "RELEASE_COMPLETE, cannot overwrite or release the slot, and enters immutable "
    "invalid-terminal incident handling"
)
FENCE_BOUND_INPUT_FIELDS = (
    "attempt_id",
    "fence_authority_id",
    "pre_release_closure_ref",
    "pre_release_closure_sha256",
    "domain_attestation_sha256_by_role_slot",
    "quorum_signature_sha256_by_role_slot",
    "role_assignment_by_role_slot",
    "binding_context_ref",
    "contract_release_sha256",
    "product_contract_sha256",
    "qualification_governance_contract_sha256",
    "fresh_issue_head",
    "signing_policy_sha256",
    "key_validity_revocation_head_sha256",
    "quorum_policy_sha256",
    "ga_promotion_plan_ref",
    "ga_promotion_plan_sha256",
    "planned_promotion_operator",
    "release_train",
    "system_milestone",
    "rc_version",
    "rc_commit",
    "rc_artifact_sha256",
    "canonical_remote_id",
    "ga_ref",
    "ga_channel",
    "canonical_ga_target",
    "attempt_terminal_slot",
    "initial_customer_visibility",
    "required_key_validity_horizon_seconds",
)


def sha(label_or_bytes: str | bytes) -> str:
    data = label_or_bytes.encode("utf-8") if isinstance(label_or_bytes, str) else label_or_bytes
    return hashlib.sha256(data).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return rfc8785.dumps(value)


def sign_closed_record(
    value: dict[str, Any],
    *,
    private_key: Ed25519PrivateKey,
    signature_domain: str,
) -> dict[str, Any]:
    """Sign one closed record without introducing a self-reference."""

    if "signed_payload_sha256" in value or "signature" in value:
        raise AssertionError("record must be unsigned before signing")
    payload = canonical_bytes(value)
    message = signature_domain.encode("ascii") + b"\x00" + payload
    signed = copy.deepcopy(value)
    signed["signed_payload_sha256"] = sha(payload)
    signed["signature"] = base64.b64encode(private_key.sign(message)).decode(
        "ascii"
    )
    return signed


def execution_authorization_projection_fields() -> tuple[str, ...]:
    """Read the generator-owned projection definition from acceptance policy."""

    registry = yaml.safe_load(
        (REPO_ROOT / "docs" / "acceptance-requirements.yaml").read_text()
    )
    fields = registry["policy"]["delivery_planning"][
        "execution_authorization_evaluation"
    ]["planned_action_input_projection_fields"]
    if (
        not isinstance(fields, list)
        or len(fields) != 22
        or len(fields) != len(set(fields))
        or not all(isinstance(field, str) and field for field in fields)
    ):
        raise AssertionError(
            "execution authorization projection policy must contain 22 unique fields"
        )
    return tuple(fields)


def ascii_set_sha256(values: list[str] | set[str]) -> str:
    raw = "".join(f"{value}\n" for value in sorted(set(values))).encode("ascii")
    return sha(raw)


def repo_json(relative_path: str) -> dict[str, Any]:
    value = json.loads((REPO_ROOT / relative_path).read_bytes())
    if not isinstance(value, dict):
        raise TypeError(f"expected object at {relative_path}")
    return value


def system_mapping_semantic_sha256(mapping: dict[str, Any]) -> str:
    source_value = mapping["source"]
    rows = {
        row["id"]: row["acceptance_ids"] for row in mapping["mappings"]
    }
    headers = (
        ("schema_version", mapping["schema_version"]),
        ("mapping_revision", mapping["mapping_revision"]),
        ("source_document", source_value["document"]),
        ("source_document_sha256", source_value["document_sha256"]),
        ("source_feature_set_sha256", source_value["feature_set_sha256"]),
        ("expected_count", mapping["expected_count"]),
    )
    lines = [f"{key}={value}\n" for key, value in headers]
    lines.extend(
        f"{feature_id}\t{','.join(sorted(set(rows[feature_id])))}\n"
        for feature_id in sorted(rows)
    )
    return sha("".join(lines).encode("ascii"))


def terminal_freshness_input_set_sha256(
    fence: dict[str, Any],
    fence_sha256: str,
    receipt_sha256: str,
    final_manifest_sha256: str,
    locator_without_freshness_validation: dict[str, Any],
) -> str:
    return sha(
        canonical_bytes(
            {
                "fence_bound_input_projection": {
                    field: fence[field] for field in FENCE_BOUND_INPUT_FIELDS
                },
                "publication_fence_sha256": fence_sha256,
                "ga_promotion_receipt_sha256": receipt_sha256,
                "release_closure_manifest_sha256": final_manifest_sha256,
                "final_locator_without_freshness_validation_sha256": sha(
                    canonical_bytes(locator_without_freshness_validation)
                ),
            }
        )
    )


def metadata() -> dict[str, Any]:
    return {
        "classification": "SYNTHETIC_FIXTURE",
        "evidence_claim": "NOT_EVIDENCE_OR_PASS",
        "fixture_notice": NOTICE,
    }


def governance_input_metadata() -> dict[str, Any]:
    return {
        "classification": "GOVERNANCE_ARTIFACT",
        "evidence_claim": "GOVERNANCE_INPUT",
        "fixture_notice": NOTICE,
    }


def governance_evidence_metadata() -> dict[str, Any]:
    return {
        "classification": "GOVERNANCE_ARTIFACT",
        "evidence_claim": "GOVERNANCE_EVIDENCE",
        "fixture_notice": NOTICE,
    }


def source(ref_id: str = "fixture-authority") -> dict[str, Any]:
    return {
        "ref_id": ref_id,
        "authority_kind": "fixture-oracle",
        "locator": f"contracts/fixtures/governance-models/support/{ref_id}.json",
        "sha256": sha(ref_id),
    }


def authority(authority_id: str, revision: int = 1) -> dict[str, Any]:
    return {
        "authority_id": authority_id,
        "revision": revision,
        "artifact_path": f"contracts/fixtures/governance-models/support/{authority_id}-r{revision}.json",
        "artifact_sha256": sha(f"authority:{authority_id}:{revision}"),
        "verified_at": STAMP,
    }


def approval(
    role_id: str,
    principal_id: str | None = None,
    decision: str = "SYNTHETIC_ONLY",
) -> dict[str, Any]:
    principal_id = principal_id or f"fixture-{role_id}-person"
    return {
        "role_id": role_id,
        "principal_id": principal_id,
        "decision": decision,
        "approved_at": STAMP,
        "assignment_ref": authority(f"{role_id}-assignment"),
    }


def blocker(blocker_id: str, reason_code: str = "UNKNOWN_ESTIMATE") -> dict[str, Any]:
    return {
        "blocker_id": blocker_id,
        "reason_code": reason_code,
        "next_action": "Resolve this synthetic fixture input before scheduling.",
        "authority_ref": source(f"{blocker_id}-authority"),
    }


def artifact_ref(
    artifact_id: str,
    schema: str = "ylx.synthetic-artifact.v1",
    digest: str | None = None,
    path: str | None = None,
    revision: int | None = 1,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "artifact_id": artifact_id,
        "schema": schema,
        "artifact_path": path or f"contracts/fixtures/governance-models/support/{artifact_id}.json",
        "artifact_sha256": digest or sha(artifact_id),
    }
    if revision is not None:
        result["revision"] = revision
    return result


def context_ref(context_id: str, digest: str, stage: str) -> dict[str, Any]:
    return artifact_ref(
        context_id,
        "ylx.binding-context.v1",
        digest,
        f"contracts/fixtures/governance-models/valid/binding-context-{stage.lower()}.json",
    )


def lineage() -> dict[str, Any]:
    return {
        "baseline_refs": [
            {"artifact_id": "fixture-baseline", "artifact_sha256": sha("fixture-baseline")}
        ],
        "decision_refs": [
            {"artifact_id": "fixture-decision", "artifact_sha256": sha("fixture-decision")}
        ],
    }


def duration(value: float) -> dict[str, Any]:
    return {"value": value, "unit": "hours"}


def estimate(value: float) -> dict[str, Any]:
    return {
        "estimate_status": "ESTIMATED",
        "value": value,
        "unit": "hours",
        "basis": "Synthetic deterministic fixture estimate.",
        "confidence_interval": {"lower": value, "upper": value, "confidence": 1.0},
        "blocker": None,
    }


class Corpus:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self.values: dict[str, Any] = {}
        self.digests: dict[str, str] = {}
        self.byte_lengths: dict[str, int] = {}
        self.relationships: dict[str, Any] = {}
        self.support_entries: list[dict[str, Any]] = []
        self.generator_context: dict[str, Any] = {}

    def add(
        self,
        case_id: str,
        filename: str,
        schema_file: str,
        value: Any,
        *,
        test_only: bool = True,
    ) -> str:
        path = VALID_ROOT / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = canonical_bytes(value)
        path.write_bytes(raw)
        rel = path.relative_to(FIXTURE_ROOT).as_posix()
        digest = sha(raw)
        self.entries.append(
            {
                "case_id": case_id,
                "path": rel,
                "schema_file": schema_file,
                "canonical_json": True,
                "test_only": test_only,
                "notice": NOTICE,
            }
        )
        self.values[rel] = copy.deepcopy(value)
        self.digests[rel] = digest
        self.byte_lengths[rel] = len(raw)
        return digest

    def replace(self, filename: str, value: Any) -> str:
        path = VALID_ROOT / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = canonical_bytes(value)
        path.write_bytes(raw)
        rel = path.relative_to(FIXTURE_ROOT).as_posix()
        if rel not in self.values:
            raise KeyError(f"cannot replace unknown fixture {rel}")
        digest = sha(raw)
        self.values[rel] = copy.deepcopy(value)
        self.digests[rel] = digest
        self.byte_lengths[rel] = len(raw)
        return digest

    def add_support(self, filename: str, value: Any, purpose: str) -> str:
        path = SUPPORT_ROOT / filename
        raw = canonical_bytes(value)
        rel = path.relative_to(FIXTURE_ROOT).as_posix()
        digest = sha(raw)
        existing = next(
            (entry for entry in self.support_entries if entry["path"] == rel),
            None,
        )
        if existing is not None:
            if existing["sha256"] != digest or path.read_bytes() != raw:
                raise AssertionError(f"support fixture reconstruction drift for {rel}")
            return digest
        path.write_bytes(raw)
        self.support_entries.append(
            {
                "path": rel,
                "sha256": digest,
                "exact_byte_length": len(raw),
                "purpose": purpose,
                "test_only": True,
                "notice": NOTICE,
            }
        )
        return digest


def build() -> None:
    pycache = FIXTURE_ROOT / "__pycache__"
    if pycache.exists():
        shutil.rmtree(pycache)
    VALID_ROOT.mkdir(parents=True, exist_ok=True)
    SUPPORT_ROOT.mkdir(parents=True, exist_ok=True)
    for old in VALID_ROOT.rglob("*.json"):
        old.unlink()
    for old in SUPPORT_ROOT.iterdir():
        if old.is_file():
            old.unlink()

    corpus = Corpus()
    registry = yaml.safe_load((REPO_ROOT / "docs" / "acceptance-requirements.yaml").read_text())
    requirement_ids = [row["id"] for row in registry["requirements"]]
    m4_rows = [
        row for row in registry["requirements"] if str(row["closing_gate"]).startswith("M4")
    ]
    m4_ids = [row["id"] for row in m4_rows]
    gate_by_id = {row["id"]: row["closing_gate"] for row in m4_rows}
    closing_gate_by_id = {
        row["id"]: row["closing_gate"] for row in registry["requirements"]
    }
    assert len(requirement_ids) == 173 and len(set(requirement_ids)) == 173
    assert len(m4_ids) == 72 and len(set(m4_ids)) == 72

    history_state = build_history_fixtures(corpus)
    g0_policy_state = build_g0_policy_ratification(corpus, history_state)

    # Accepted planning chain with complete owner, calendar, WBS, and forecast coverage.
    assignments = []
    for role in ROLES:
        assignments.append(
            {
                "role_id": role,
                "assignment_status": "RESOLVED",
                "accountable_party_id": f"fixture-{role}-person",
                "party_type": "person",
                "authorization_scope": ["synthetic-governance-fixture"],
                "effective_at": VALID_FROM,
                "expires_at": NOT_AFTER,
                "executor_id": f"fixture-{role}-person",
                "reviewer_id": "fixture-independent-reviewer",
                "conflict_predicates": [
                    {
                        "predicate_id": f"{role}-conflict-check",
                        "statement": "Synthetic fixture conflict check.",
                        "outcome": "NO_CONFLICT",
                        "evaluated_at": STAMP,
                        "authority_ref": source(f"{role}-conflict-authority"),
                    }
                ],
                "compensating_controls": [],
                "required_approval_roles": ["release-owner"],
                "last_verified_at": STAMP,
                "blocker": None,
            }
        )
    def bootstrap_support_ref(ref_id: str, filename: str, purpose: str) -> dict[str, Any]:
        digest = corpus.add_support(
            filename,
            {
                "support_id": ref_id,
                "purpose": purpose,
                "notice": NOTICE,
            },
            purpose,
        )
        return {
            "ref_id": ref_id,
            "authority_kind": "external-organizational-authority",
            "locator": f"contracts/fixtures/governance-models/support/{filename}",
            "sha256": digest,
        }

    bootstrap_authority = {
        "schema": "ylx.owner-assignment-bootstrap-authority.v1",
        "source_kind": "external-organizational-authority",
        "authority_id": "fixture-owner-assignment-bootstrap-authority",
        "issuer_identity": "fixture-organizational-authority-issuer",
        "issuer_authority_ref": bootstrap_support_ref(
            "fixture-bootstrap-issuer-authority",
            "bootstrap-issuer-authority.json",
            "Synthetic issuer-authority bytes for bootstrap validation.",
        ),
        "authority_evidence_ref": bootstrap_support_ref(
            "fixture-bootstrap-authority-evidence",
            "bootstrap-authority-evidence.json",
            "Synthetic revision-1 owner-assignment authorization evidence.",
        ),
        "authorized_role_slot_set": ROLES,
        "authorized_artifact_id": "M0-GOV-01-governed-owner-assignment",
        "authorized_artifact_schema": "ylx.owner-assignment.v1",
        "authorized_revision": 1,
        "effective_at": VALID_FROM,
        "expires_at": NOT_AFTER,
        "delegate_restrictions": {
            "delegation_allowed": False,
            "may_approve_bootstrap_authority": False,
            "may_approve_resource_calendar": False,
            "may_approve_delivery_wbs": False,
            "may_approve_forecast_snapshot": False,
            "may_approve_successor_owner_assignment": False,
            "may_authorize_release_signing": False,
        },
        "approver_identity": "fixture-bootstrap-approver",
        "independent_verification": {
            "verifier_identity": "fixture-bootstrap-independent-verifier",
            "verification_status": "VERIFIED",
            "verified_at": STAMP,
            "authority_ref": bootstrap_support_ref(
                "fixture-bootstrap-independent-verification",
                "bootstrap-independent-verification.json",
                "Synthetic independent-verification authority bytes.",
            ),
        },
        "authority_status": "RESOLVED",
        "blockers": [],
        "artifact_metadata": metadata(),
    }
    bootstrap_sha = corpus.add(
        "VALID-OWNER-ASSIGNMENT-BOOTSTRAP-AUTHORITY-01",
        "owner-assignment-bootstrap-authority.json",
        "owner-assignment-bootstrap-authority-v1.schema.json",
        bootstrap_authority,
    )
    bootstrap_ref = {
        "ref_id": bootstrap_authority["authority_id"],
        "authority_kind": "external-organizational-authority",
        "locator": (
            "contracts/fixtures/governance-models/valid/"
            "owner-assignment-bootstrap-authority.json"
        ),
        "sha256": bootstrap_sha,
    }

    owner = {
        "schema": "ylx.owner-assignment.v1",
        "artifact_id": "fixture-owner-assignment-r1",
        "revision": 1,
        "predecessor_sha256": None,
        "generated_at": STAMP,
        "source_refs": [bootstrap_ref],
        "approvals": [approval("release-owner", bootstrap_authority["approver_identity"])],
        "artifact_metadata": metadata(),
        "overall_status": "ACCEPTED",
        "assignments": assignments,
        "blockers": [],
    }
    owner_sha = corpus.add(
        "VALID-OWNER-ASSIGNMENT-01",
        "owner-assignment.json",
        "owner-assignment-v1.schema.json",
        owner,
    )
    owner_r2 = copy.deepcopy(owner)
    owner_r2_approval = approval("release-owner")
    owner_r2_approval["assignment_ref"] = {
        "authority_id": owner["artifact_id"],
        "revision": owner["revision"],
        "artifact_path": "contracts/fixtures/governance-models/valid/owner-assignment.json",
        "artifact_sha256": owner_sha,
        "verified_at": "2026-06-01T12:01:00Z",
    }
    owner_r2.update(
        {
            "revision": 2,
            "predecessor_sha256": owner_sha,
            "generated_at": "2026-06-01T12:01:00Z",
            "source_refs": [
                {
                    "ref_id": owner["artifact_id"],
                    "authority_kind": "identity-authority",
                    "locator": (
                        "contracts/fixtures/governance-models/valid/"
                        "owner-assignment.json"
                    ),
                    "sha256": owner_sha,
                }
            ],
            "approvals": [owner_r2_approval],
        }
    )
    corpus.add(
        "VALID-OWNER-ASSIGNMENT-SUCCESSOR-01",
        "owner-assignment-r2.json",
        "owner-assignment-v1.schema.json",
        owner_r2,
    )

    resources = []
    for role in ROLES:
        resources.append(
            {
                "resource_id": f"resource-{role}",
                "role_id": role,
                "capacity_unit": "fte",
                "capacity_intervals": [
                    {
                        "starts_at": "2026-06-01T00:00:00Z",
                        "ends_at": "2026-06-30T23:59:59Z",
                        "available_capacity": 1.0,
                        "committed_capacity": 0.5,
                        "source_ref": source(f"calendar-{role}"),
                        "last_verified_at": STAMP,
                    }
                ],
                "unavailable_intervals": [],
            }
        )
    calendar = {
        "schema": "ylx.resource-calendar.v1",
        "artifact_id": "fixture-resource-calendar-r1",
        "revision": 1,
        "predecessor_sha256": None,
        "generated_at": STAMP,
        "source_refs": [source("calendar-source")],
        "approvals": [approval("release-owner")],
        "artifact_metadata": metadata(),
        "overall_status": "ACCEPTED",
        "timezone": "UTC",
        "planning_horizon": {
            "starts_at": "2026-06-01T00:00:00Z",
            "ends_at": "2026-06-30T23:59:59Z",
        },
        "resources": resources,
        "windows": [
            {
                "window_id": "fixture-ci-window",
                "resource_kind": "CI_RUNNER",
                "resource_id": "resource-build-platform-owner",
                "quantity": 1.0,
                "permissions": ["fixture-execute"],
                "starts_at": "2026-06-01T00:00:00Z",
                "ends_at": "2026-06-30T23:59:59Z",
                "gate_critical": True,
                "confirmation_status": "CONFIRMED",
                "confirmation_source": source("ci-window-confirmation"),
                "last_verified_at": STAMP,
                "blocker": None,
            }
        ],
        "blockers": [],
    }
    calendar_sha = corpus.add(
        "VALID-RESOURCE-CALENDAR-01",
        "resource-calendar.json",
        "resource-calendar-v1.schema.json",
        calendar,
    )

    task = {
        "task_id": "fixture-all-requirements-task",
        "parent_task_id": None,
        "task_kind": "LEAF",
        "deliverable": "Synthetic all-requirement validation result.",
        "in_scope": ["synthetic-validation"],
        "out_of_scope": ["production-release"],
        "milestone_id": "fixture-milestone",
        "gate_id": "M0",
        "authorization_class": "may_prepare",
        "planning_detail": "DETAILED_EXECUTABLE",
        "conditional_scenario": None,
        "authorization_refs": [artifact_ref("fixture-authorization")],
        "authorization_stop_rules": ["Stop before any production mutation."],
        "affected_requirement_ids": requirement_ids,
        "affected_issue_ids": ["O-1"],
        "accountable_party_id": "fixture-release-owner-person",
        "executor_id": "fixture-qa-evidence-owner-person",
        "reviewer_id": "fixture-independent-reviewer",
        "effort_estimate": estimate(8.0),
        "fixed_duration_estimate": estimate(8.0),
        "predecessors": [],
        "resource_requirements": [
            {
                "requirement_id": "fixture-runner-requirement",
                "requirement_status": "RESOLVED",
                "resource_kind": "CI_RUNNER",
                "resource_id": "resource-build-platform-owner",
                "quantity": 1.0,
                "capacity_unit": "runner",
                "window_ids": ["fixture-ci-window"],
                "blocker": None,
            }
        ],
        "definition_of_done": ["All 173 synthetic registry rows are evaluated."],
        "evidence_locator": "contracts/fixtures/governance-models/support/task-evidence.json",
        "planning_status": "READY",
        "status": "NOT_STARTED",
        "risks": [
            {
                "risk_id": "fixture-risk",
                "severity": "LOW",
                "description": "Synthetic fixture only.",
                "mitigation": "Never treat fixture output as evidence.",
            }
        ],
        "blockers": [],
    }
    wbs = {
        "schema": "ylx.delivery-wbs.v1",
        "artifact_id": "fixture-delivery-wbs-r1",
        "revision": 1,
        "predecessor_sha256": None,
        "generated_at": STAMP,
        "source_refs": [source("wbs-source")],
        "approvals": [approval("release-owner")],
        "artifact_metadata": metadata(),
        "overall_status": "ACCEPTED",
        "planning_gate": "M0",
        "scope_revision": 1,
        "requirement_ids": requirement_ids,
        "active_blocker_ids": [],
        "tasks": [task],
        "blockers": [],
    }
    wbs_sha = corpus.add(
        "VALID-DELIVERY-WBS-01",
        "delivery-wbs.json",
        "delivery-wbs-v1.schema.json",
        wbs,
    )

    forecast = {
        "schema": "ylx.forecast-snapshot.v1",
        "artifact_id": "fixture-forecast-r1",
        "revision": 1,
        "predecessor_sha256": None,
        "generated_at": STAMP,
        "source_refs": [source("forecast-source")],
        "approvals": [approval("release-owner")],
        "artifact_metadata": metadata(),
        "overall_status": "ACCEPTED",
        "as_of": STAMP,
        "owner_assignment_sha256": owner_sha,
        "resource_calendar_sha256": calendar_sha,
        "delivery_wbs_sha256": wbs_sha,
        "scheduling_rules_version": "fixture-cpm-v1",
        "calculator": {
            "calculator_id": "fixture-cpm-calculator",
            "version": "1.0.0",
            "artifact_path": "contracts/fixtures/governance-models/support/cpm-calculator.json",
            "artifact_sha256": sha("fixture-cpm-calculator"),
        },
        "task_forecasts": [
            {
                "task_id": "fixture-all-requirements-task",
                "forecast_status": "SCHEDULED",
                "dependency_only_start": "2026-06-02T00:00:00Z",
                "dependency_only_finish": "2026-06-02T08:00:00Z",
                "dependency_critical": True,
                "total_float": duration(0.0),
                "free_float": duration(0.0),
                "resource_levelled_start": "2026-06-02T00:00:00Z",
                "resource_levelled_finish": "2026-06-02T08:00:00Z",
                "driving_path": True,
                "window_delay": duration(0.0),
                "forecast_start": "2026-06-02T00:00:00Z",
                "forecast_finish": "2026-06-02T08:00:00Z",
            }
        ],
        "dependency_critical_path": ["fixture-all-requirements-task"],
        "resource_levelled_driving_path": ["fixture-all-requirements-task"],
        "decision_need_bys": [
            {
                "decision_id": "fixture-release-decision",
                "need_by": "2026-06-02T00:00:00Z",
                "driving_task_ids": ["fixture-all-requirements-task"],
            }
        ],
        "milestone_forecasts": [
            {
                "milestone_id": "fixture-milestone",
                "forecast_status": "SCHEDULED",
                "forecast_start": "2026-06-02T00:00:00Z",
                "forecast_finish": "2026-06-02T08:00:00Z",
                "driving_task_ids": ["fixture-all-requirements-task"],
            }
        ],
        "capacity_overallocations": [],
        "external_constraints": [],
        "assumptions": [],
        "change_reasons": [],
        "blockers": [],
    }
    forecast_sha = corpus.add(
        "VALID-FORECAST-SNAPSHOT-01",
        "forecast-snapshot.json",
        "forecast-snapshot-v1.schema.json",
        forecast,
    )

    blocked_forecast = copy.deepcopy(forecast)
    blocked_forecast.update(
        {
            "artifact_id": "fixture-forecast-blocked-r1",
            "overall_status": "INPUT_BLOCKED",
            "approvals": [],
            "dependency_critical_path": [],
            "resource_levelled_driving_path": [],
            "blockers": [blocker("forecast-input-blocked", "UNKNOWN_ANCHOR")],
        }
    )
    blocked_row = blocked_forecast["task_forecasts"][0]
    blocked_row["forecast_status"] = "INPUT_BLOCKED"
    for key in [
        "dependency_only_start",
        "dependency_only_finish",
        "dependency_critical",
        "total_float",
        "free_float",
        "resource_levelled_start",
        "resource_levelled_finish",
        "driving_path",
        "window_delay",
        "forecast_start",
        "forecast_finish",
    ]:
        blocked_row[key] = None
    blocked_forecast["decision_need_bys"][0]["need_by"] = None
    blocked_forecast["milestone_forecasts"][0]["forecast_status"] = "INPUT_BLOCKED"
    blocked_forecast["milestone_forecasts"][0]["forecast_start"] = None
    blocked_forecast["milestone_forecasts"][0]["forecast_finish"] = None
    corpus.add(
        "VALID-FORECAST-INPUT-BLOCKED-01",
        "forecast-snapshot-input-blocked.json",
        "forecast-snapshot-v1.schema.json",
        blocked_forecast,
    )

    bundle = {
        "schema": "ylx.delivery-planning-bundle.v1",
        "artifact_id": "fixture-delivery-planning-bundle-r1",
        "revision": 1,
        "predecessor_sha256": None,
        "generated_at": STAMP,
        "source_refs": [source("planning-bundle-source")],
        "approvals": [approval("release-owner")],
        "artifact_metadata": metadata(),
        "artifacts": {
            "owner_assignment": artifact_ref(
                "fixture-owner-assignment-r1",
                "ylx.owner-assignment.v1",
                owner_sha,
                "valid/owner-assignment.json",
            ),
            "resource_calendar": artifact_ref(
                "fixture-resource-calendar-r1",
                "ylx.resource-calendar.v1",
                calendar_sha,
                "valid/resource-calendar.json",
            ),
            "delivery_wbs": artifact_ref(
                "fixture-delivery-wbs-r1",
                "ylx.delivery-wbs.v1",
                wbs_sha,
                "valid/delivery-wbs.json",
            ),
            "forecast_snapshot": artifact_ref(
                "fixture-forecast-r1",
                "ylx.forecast-snapshot.v1",
                forecast_sha,
                "valid/forecast-snapshot.json",
            ),
        },
        "overall_status": "ACCEPTED",
    }
    corpus.add(
        "VALID-DELIVERY-PLANNING-BUNDLE-01",
        "delivery-planning-bundle.json",
        "delivery-planning-bundle-v1.schema.json",
        bundle,
    )

    foundation_state = build_identity_and_mapping_fixtures(
        corpus,
        owner,
        owner_sha,
        history_state,
        registry,
    )
    planning_v2_state = build_planning_v2_fixtures(
        corpus,
        requirement_ids,
        registry,
        owner,
        owner_sha,
        calendar,
        calendar_sha,
        g0_policy_state,
    )
    planning_v2_state["m0_bootstrap"] = build_m0_bootstrap_graph(
        corpus,
        planning_v2_state,
        g0_policy_state,
    )

    build_context_and_release(
        corpus,
        requirement_ids,
        m4_ids,
        gate_by_id,
        closing_gate_by_id,
        registry,
        history_state,
        foundation_state,
        planning_v2_state,
    )
    finalize_corpus(corpus, requirement_ids, m4_ids)


def build_history_fixtures(corpus: Corpus) -> dict[str, Any]:
    """Mirror the immutable live history records without inventing authority."""

    records = {
        "acceptance_anchor": (
            "VALID-ACCEPTANCE-HISTORY-ANCHOR-01",
            "acceptance-registry-history-anchor.json",
            "acceptance-registry-history-anchor-v1.schema.json",
            (
                "docs/evidence/governance/acceptance-history/anchors/"
                "acceptance-registry-history-anchor--"
                "cf84f3cef6490e5e0374e4bbdd49bfd40b668b47c242236cda5bf7b1e0e821b6.json"
            ),
        ),
        "acceptance_proposal": (
            "VALID-ACCEPTANCE-HISTORY-PROPOSAL-01",
            "acceptance-registry-history-proposal.json",
            "acceptance-registry-history-proposal-v1.schema.json",
            (
                "docs/evidence/governance/acceptance-history/proposals/"
                "acceptance-registry-revision-1--"
                "de22a7ee4f0a15756aef7d527520192746f266418fb3f740d4c96ff062030717.json"
            ),
        ),
        "acceptance_head": (
            "VALID-ACCEPTANCE-HISTORY-HEAD-01",
            "acceptance-registry-history-head.json",
            "acceptance-registry-history-head-v1.schema.json",
            (
                "docs/evidence/governance/acceptance-history/heads/"
                "acceptance-registry-history-head-v1--"
                "b1495937070da626688eda76f60ed51460e4fb349aaa0529760afd5cd966d419.json"
            ),
        ),
        "decision_anchor": (
            "VALID-DECISION-HISTORY-ANCHOR-01",
            "decision-history-anchor.json",
            "decision-history-anchor-v1.schema.json",
            (
                "docs/evidence/governance/decision-history/anchors/"
                "decision-history-anchor--"
                "0b43894e993a7f0ef4ca6e73e791b7a52b05905c7e0fb5318fb7b45577fe2f79.json"
            ),
        ),
        "decision_proposal": (
            "VALID-DECISION-SUCCESSOR-PROPOSAL-01",
            "decision-successor-proposal.json",
            "decision-successor-proposal-v1.schema.json",
            (
                "docs/evidence/governance/decision-history/proposals/"
                "D-028--21afd9d064eb8b6f3a30edcf93dde9936a868aeb07bb300594feb1f3251cf41b.json"
            ),
        ),
        "decision_head": (
            "VALID-DECISION-HISTORY-HEAD-01",
            "decision-history-head.json",
            "decision-history-head-v1.schema.json",
            (
                "docs/evidence/governance/decision-history/heads/"
                "decision-history-head-v1--"
                "373870152bde11b258c57f39defc0abc823ce45c80c8ba482b043282052cd1a1.json"
            ),
        ),
    }
    state: dict[str, Any] = {}
    for key, (case_id, filename, schema_file, source_path) in records.items():
        value = repo_json(source_path)
        digest = corpus.add(case_id, filename, schema_file, value)
        state[key] = {
            "value": value,
            "sha256": digest,
            "fixture_path": f"valid/{filename}",
            "source_path": source_path,
        }

    checkpoint_dir = (
        REPO_ROOT
        / "docs"
        / "evidence"
        / "governance"
        / "acceptance-history"
        / "checkpoints"
    )
    checkpoint_candidates: list[tuple[int, Path, dict[str, Any]]] = []
    for checkpoint_path in checkpoint_dir.glob(
        "acceptance-registry-history-checkpoint--*.json"
    ):
        checkpoint_value = repo_json(
            checkpoint_path.relative_to(REPO_ROOT).as_posix()
        )
        history_revision = checkpoint_value.get("history_revision")
        if isinstance(history_revision, int) and not isinstance(
            history_revision, bool
        ):
            checkpoint_candidates.append(
                (history_revision, checkpoint_path, checkpoint_value)
            )
    if not checkpoint_candidates:
        raise AssertionError("acceptance history lacks a published checkpoint")
    checkpoint_revision = max(item[0] for item in checkpoint_candidates)
    checkpoint_tips = [
        item for item in checkpoint_candidates if item[0] == checkpoint_revision
    ]
    if len(checkpoint_tips) != 1:
        raise AssertionError("acceptance history has a highest-revision checkpoint fork")
    _, checkpoint_path, checkpoint_value = checkpoint_tips[0]
    checkpoint_raw = checkpoint_path.read_bytes()
    checkpoint_path_digest = checkpoint_path.stem.rsplit("--", 1)[-1]
    if sha(checkpoint_raw) != checkpoint_path_digest:
        raise AssertionError("acceptance history checkpoint path digest drift")
    checkpoint_digest = corpus.add(
        "VALID-ACCEPTANCE-HISTORY-CHECKPOINT-01",
        "acceptance-registry-history-checkpoint.json",
        "acceptance-registry-history-checkpoint-v1.schema.json",
        checkpoint_value,
    )
    state["acceptance_checkpoint"] = {
        "value": checkpoint_value,
        "sha256": checkpoint_digest,
        "fixture_path": "valid/acceptance-registry-history-checkpoint.json",
        "source_path": checkpoint_path.relative_to(REPO_ROOT).as_posix(),
        "history_revision": checkpoint_revision,
    }

    selector_path = "docs/acceptance-requirements-history.yaml"
    selector = yaml.safe_load((REPO_ROOT / selector_path).read_text())
    if not isinstance(selector, dict):
        raise TypeError(f"expected object at {selector_path}")
    selector_sha = corpus.add(
        "VALID-ACCEPTANCE-HISTORY-SELECTOR-01",
        "acceptance-registry-history-selector.json",
        "acceptance-registry-history-selector-v1.schema.json",
        selector,
    )
    state["acceptance_selector"] = {
        "value": selector,
        "sha256": selector_sha,
        "fixture_path": "valid/acceptance-registry-history-selector.json",
        "source_path": selector_path,
    }
    selected_head_path = selector.get("current_head", {}).get("artifact_path")
    if not isinstance(selected_head_path, str):
        raise TypeError("acceptance history selector lacks a current head path")
    selected_head = repo_json(selected_head_path)
    selected_head_raw = (REPO_ROOT / selected_head_path).read_bytes()
    selected_head_sha = sha(selected_head_raw)
    if selected_head_sha != selector.get("current_head", {}).get("artifact_sha256"):
        raise AssertionError("acceptance history selector current-head digest drift")
    current_proposal = selected_head.get("current_proposal", {})
    selected_proposal_path = current_proposal.get("artifact_path")
    if not isinstance(selected_proposal_path, str):
        raise TypeError("selected acceptance history head lacks a current proposal path")
    selected_proposal = repo_json(selected_proposal_path)
    selected_proposal_sha = sha((REPO_ROOT / selected_proposal_path).read_bytes())
    if selected_proposal_sha != current_proposal.get("artifact_sha256"):
        raise AssertionError("acceptance history selected-proposal digest drift")
    state["selected_acceptance_head"] = {
        "value": selected_head,
        "sha256": selected_head_sha,
        "source_path": selected_head_path,
    }
    state["selected_acceptance_proposal"] = {
        "value": selected_proposal,
        "sha256": selected_proposal_sha,
        "source_path": selected_proposal_path,
    }
    return state


def add_content_addressed_readback_fixture(
    corpus: Corpus,
    *,
    case_id: str,
    filename: str,
    locator_id: str,
    artifact_schema: str,
    artifact_id: str,
    artifact_path: str,
    artifact_sha256: str,
    canonical_directory: str,
    canonical_slug: str,
    published_at: str,
    readback_at: str,
) -> dict[str, Any]:
    fixture_prefix = "contracts/fixtures/governance-models/"
    normalized_path = artifact_path.removeprefix(fixture_prefix)
    if (
        normalized_path not in corpus.values
        or corpus.digests[normalized_path] != artifact_sha256
    ):
        raise AssertionError(
            f"content-addressed readback target does not resolve: {artifact_path}"
        )
    exact_byte_length = corpus.byte_lengths[normalized_path]
    value = {
        "schema": "ylx.content-addressed-locator-readback.v1",
        "locator_id": locator_id,
        "artifact_schema": artifact_schema,
        "artifact_id": artifact_id,
        "artifact_sha256": artifact_sha256,
        "canonical_path": (
            f"{canonical_directory}/{artifact_sha256}--{canonical_slug}.json"
        ),
        "attempt_terminal_slot": None,
        "terminal_slot_record": None,
        "terminal_slot_create_if_absent": None,
        "terminal_slot_recorded_at": None,
        "terminal_slot_readback_record": None,
        "terminal_slot_readback_at": None,
        "terminal_slot_readback_result": None,
        "freshness_validation": None,
        "exact_byte_length": exact_byte_length,
        "canonical_encoding": "RFC8785-JSON-UTF8",
        "create_if_absent": True,
        "existing_identical_is_idempotent": True,
        "different_digest_is_equivocation": True,
        "durability": {
            "temporary_exact_bytes_fsynced": True,
            "parent_fsynced_before_create": True,
            "atomic_unique_create": True,
            "parent_fsynced_after_create": True,
        },
        "published_at": published_at,
        "readback_sha256": artifact_sha256,
        "readback_byte_length": exact_byte_length,
        "readback_at": readback_at,
        "readback_result": "EXACT_PATH_DIGEST_AND_BYTES_MATCH",
    }
    digest = corpus.add(
        case_id,
        filename,
        "content-addressed-locator-readback-v1.schema.json",
        value,
    )
    return {
        "value": value,
        "sha": digest,
        "ref": artifact_ref(
            locator_id,
            value["schema"],
            digest,
            f"contracts/fixtures/governance-models/valid/{filename}",
            None,
        ),
    }


def build_measurement_data_selection_fixture(
    corpus: Corpus,
    *,
    measurement_id: str,
    partition: dict[str, Any],
    partition_ref: dict[str, Any],
    partition_side: str,
    created_at: str,
    published_at: str,
    readback_at: str,
) -> dict[str, Any]:
    side_slug = partition_side.lower()
    side_field = f"{side_slug}_group_ids"
    source_group_ids = sorted(set(partition[side_field]))
    group_by_id = {
        group["group_id"]: group for group in partition["source_groups"]
    }
    selected_groups = [group_by_id[group_id] for group_id in source_group_ids]
    if any(
        group["partition_side"] != side_slug for group in selected_groups
    ):
        raise AssertionError(
            f"measurement selection {partition_side} source-group drift"
        )
    source_digests = sorted(
        {
            digest
            for group in selected_groups
            for digest in group["source_digests"]
        }
    )
    samples = sorted(
        {
            (
                sample["sample_id"],
                sample["sample_kind"],
                sample["sample_sha256"],
            )
            for group in selected_groups
            for sample in group["expanded_samples"]
        }
    )
    selection_id = (
        f"fixture-measurement-data-selection-{side_slug}-m0-meas-01"
    )
    selection_filename = (
        f"measurement-data-selection-{side_slug}-m0-meas-01.json"
    )
    selection = {
        "schema": "ylx.measurement-data-selection.v1",
        "selection_id": selection_id,
        "measurement_id": measurement_id,
        "data_partition_ref": copy.deepcopy(partition_ref),
        "partition_side": partition_side,
        "source_group_ids": source_group_ids,
        "selected_group_set_sha256": sha(canonical_bytes(source_group_ids)),
        "selected_source_digest_set_sha256": sha(
            canonical_bytes(source_digests)
        ),
        "selected_sample_set_sha256": sha(
            "".join(
                f"{sample_id}\t{sample_kind}\t{sample_sha256}\n"
                for sample_id, sample_kind, sample_sha256 in samples
            ).encode("ascii")
        ),
        "selection_digest_rules": {
            "group_set": "SHA256_RFC8785_SORTED_UNIQUE_STRING_ARRAY",
            "source_digest_set": (
                "SHA256_RFC8785_SORTED_UNIQUE_STRING_ARRAY"
            ),
            "sample_set": (
                "SHA256_ASCII_SORTED_UNIQUE_SAMPLE_ID_TAB_KIND_TAB_SHA256_LF"
            ),
        },
        "created_at": created_at,
        "publication_protocol": (
            "EXTERNAL_CONTENT_ADDRESSED_LOCATOR_READBACK_V1"
        ),
        "publication_readback_schema": (
            "ylx.content-addressed-locator-readback.v1"
        ),
        "authority_effect": "NONE",
        "artifact_metadata": governance_input_metadata(),
    }
    selection_sha = corpus.add(
        f"VALID-MEASUREMENT-DATA-SELECTION-{partition_side}-M0-MEAS-01-01",
        selection_filename,
        "measurement-data-selection-v1.schema.json",
        selection,
    )
    selection_path = (
        "contracts/fixtures/governance-models/valid/"
        f"{selection_filename}"
    )
    selection_ref = artifact_ref(
        selection_id,
        selection["schema"],
        selection_sha,
        selection_path,
        None,
    )
    locator = add_content_addressed_readback_fixture(
        corpus,
        case_id=(
            "VALID-MEASUREMENT-DATA-SELECTION-"
            f"{partition_side}-READBACK-M0-MEAS-01-01"
        ),
        filename=(
            "content-addressed-locator-readback-measurement-data-selection-"
            f"{side_slug}-m0-meas-01.json"
        ),
        locator_id=(
            f"fixture-measurement-data-selection-{side_slug}-"
            "m0-meas-01-locator"
        ),
        artifact_schema=selection["schema"],
        artifact_id=selection_id,
        artifact_path=selection_path,
        artifact_sha256=selection_sha,
        canonical_directory="measurement-data-selection",
        canonical_slug=f"{side_slug}-m0-meas-01",
        published_at=published_at,
        readback_at=readback_at,
    )
    return {
        "value": selection,
        "sha": selection_sha,
        "ref": selection_ref,
        "locator": locator,
    }


def build_measurement_evidence_fixture(
    corpus: Corpus,
    planning_state: dict[str, Any],
    *,
    measurement_id: str,
    task_id: str,
    partition_ref: dict[str, Any],
    selection_state: dict[str, Any],
    partition_side: str,
    source_scope_ref: dict[str, Any],
    authorization_binding_context_ref: dict[str, Any] | None,
    phase_barrier_ids: list[str],
    prerequisite_ref_by_kind: dict[str, dict[str, Any]],
    actor_person_id: str,
    environment_class: str,
    closing_gate: str,
    evaluated_at: str,
    evidence_created_at: str,
    binding_created_at: str,
) -> dict[str, Any]:
    """Build one measurement-only PASS E, evidence record, and binding."""

    side_slug = partition_side.lower()
    evidence_id = f"fixture-measurement-{side_slug}-evidence-m0-meas-01"
    evidence_filename = (
        f"measurement-{side_slug}-evidence-m0-meas-01.json"
    )
    binding_id = f"fixture-measurement-{side_slug}-evidence-binding-m0-meas-01"
    binding_filename = (
        f"evidence-binding-measurement-{side_slug}-m0-meas-01.json"
    )
    evaluation = build_execution_authorization_evaluation(
        corpus,
        planning_state,
        task_id=task_id,
        action_instance_id=(
            f"fixture-action-produce-{side_slug}-evidence-m0-meas-01"
        ),
        filename_slug=f"measurement-{side_slug}-evidence-m0-meas-01-pass",
        authorization_binding_context_ref=copy.deepcopy(
            authorization_binding_context_ref
        ),
        environment_class=environment_class,
        phase_barrier_ids=phase_barrier_ids,
        actor_person_id=actor_person_id,
        additional_prerequisite_ref_by_kind={
            "stage_source_scope": copy.deepcopy(source_scope_ref),
            "measurement_data_selection": copy.deepcopy(selection_state["ref"]),
            **copy.deepcopy(prerequisite_ref_by_kind),
        },
        evaluated_at=evaluated_at,
    )
    evidence = {
        "schema": "ylx.stage-evidence-record.v1",
        "evidence_id": evidence_id,
        "closing_gate": closing_gate,
        "source_scope_ref": copy.deepcopy(source_scope_ref),
        "requirement_ids": [measurement_id],
        "evidence_outcome": (
            "THRESHOLD_TRAINING_INPUT_ONLY"
            if partition_side == "TRAINING"
            else "HOLDOUT_MEASUREMENT_OBSERVATION"
        ),
        "created_at": evidence_created_at,
        "authorization_binding_context_ref": copy.deepcopy(
            evaluation["value"]["authorization_binding_context_ref"]
        ),
        "execution_authorization_evaluation_ref": copy.deepcopy(
            evaluation["ref"]
        ),
        "action_instance_id": evaluation["value"]["action_instance_id"],
        "planned_action_input_sha256": evaluation["value"][
            "planned_action_input_sha256"
        ],
        "actor_person_id": evaluation["value"]["actor_person_id"],
        "authorization_action": evaluation["value"]["authorization_action"],
        "authorization_environment_class": evaluation["value"][
            "authorization_environment_class"
        ],
        "artifact_metadata": metadata(),
    }
    evidence_sha = corpus.add_support(
        evidence_filename,
        evidence,
        (
            f"Synthetic exact {side_slug}-side stage evidence for the "
            f"{measurement_id} measurement fixture."
        ),
    )
    evidence_path = (
        "contracts/fixtures/governance-models/support/"
        f"{evidence_filename}"
    )
    evidence_ref = artifact_ref(
        evidence_id,
        evidence["schema"],
        evidence_sha,
        evidence_path,
        1,
    )

    execution_context_path = "valid/execution-context.json"
    execution_context = corpus.values.get(execution_context_path)
    if not isinstance(execution_context, dict):
        raise TypeError("measurement evidence requires execution-context.json")
    execution_context_id = execution_context["context_id"]
    execution_context_ref = {
        "context_id": execution_context_id,
        "artifact_path": (
            "contracts/fixtures/governance-models/valid/execution-context.json"
        ),
        "artifact_sha256": corpus.digests[execution_context_path],
    }
    binding_context_ref = {
        "context_id": source_scope_ref["artifact_id"],
        "artifact_path": source_scope_ref["artifact_path"],
        "artifact_sha256": source_scope_ref["artifact_sha256"],
    }
    binding = {
        "schema": "ylx.evidence-binding.v1",
        "binding_id": binding_id,
        "created_at": binding_created_at,
        "binding_context_ref": binding_context_ref,
        "execution_context_refs": [execution_context_ref],
        "required_execution_context_ids": [execution_context_id],
        "evidence_records": [
            {
                "evidence_id": evidence_id,
                "evidence_record_kind": "run-evidence",
                "artifact_path": evidence_path,
                "artifact_sha256": evidence_sha,
                "execution_context_ids": [execution_context_id],
                "actor_deployment_record_sha256": None,
                "execution_authorization_evaluation_ref": copy.deepcopy(
                    evaluation["ref"]
                ),
                "action_instance_id": evaluation["value"][
                    "action_instance_id"
                ],
                "planned_action_input_sha256": evaluation["value"][
                    "planned_action_input_sha256"
                ],
            }
        ],
        "reverse_coverage": [
            {
                "requirement_id": measurement_id,
                "execution_context_id": execution_context_id,
                "evidence_ids": [evidence_id],
            }
        ],
        "artifact_metadata": metadata(),
    }
    binding_sha = corpus.add(
        (
            "VALID-MEASUREMENT-EVIDENCE-BINDING-"
            f"{partition_side}-{measurement_id}-01"
        ),
        binding_filename,
        "evidence-binding-v1.schema.json",
        binding,
    )
    binding_ref = artifact_ref(
        binding_id,
        binding["schema"],
        binding_sha,
        (
            "contracts/fixtures/governance-models/valid/"
            f"{binding_filename}"
        ),
        1,
    )
    return {
        "evaluation": evaluation,
        "evidence": evidence,
        "evidence_sha": evidence_sha,
        "evidence_ref": evidence_ref,
        "binding": binding,
        "binding_sha": binding_sha,
        "binding_ref": binding_ref,
    }


def build_measurement_threshold_fixtures(
    corpus: Corpus,
    planning_state: dict[str, Any],
    m1_stage_source_scope_ref: dict[str, Any],
) -> dict[str, Any]:
    """Build one fully joined M1 DATA_FITTED threshold-policy input."""

    measurement_id = "M0-MEAS-01"
    metric_id = "mjpeg-bitrate-p95-mbps"
    fixture_prefix = "contracts/fixtures/governance-models/valid/"
    partition_path = "valid/data-partition.json"
    partition = corpus.values.get(partition_path)
    if not isinstance(partition, dict):
        raise TypeError("measurement threshold requires data-partition.json")
    partition_ref = artifact_ref(
        partition["partition_id"],
        partition["schema"],
        corpus.digests[partition_path],
        f"contracts/fixtures/governance-models/{partition_path}",
        1,
    )
    if partition.get("training_group_ids") != ["training-group-source"]:
        raise AssertionError("measurement threshold training partition drift")

    training_selection = build_measurement_data_selection_fixture(
        corpus,
        measurement_id=measurement_id,
        partition=partition,
        partition_ref=partition_ref,
        partition_side="TRAINING",
        created_at="2026-06-01T12:04:41Z",
        published_at="2026-06-01T12:04:42Z",
        readback_at="2026-06-01T12:04:43Z",
    )
    training_evidence_state = build_measurement_evidence_fixture(
        corpus,
        planning_state,
        measurement_id=measurement_id,
        task_id=planning_state["m1_measurement_training_node_id"],
        partition_ref=partition_ref,
        selection_state=training_selection,
        partition_side="TRAINING",
        source_scope_ref=m1_stage_source_scope_ref,
        authorization_binding_context_ref=None,
        phase_barrier_ids=["milestone-entry/M1"],
        prerequisite_ref_by_kind={},
        actor_person_id="fixture-capture-owner-person",
        environment_class="governance-workspace",
        closing_gate="M1",
        evaluated_at="2026-06-01T12:04:45Z",
        evidence_created_at="2026-06-01T12:04:50Z",
        binding_created_at="2026-06-01T12:04:55Z",
    )

    def add_method(
        *,
        role: str,
        suffix: str,
        algorithm_id: str,
        partition_policy: str,
        holdout_policy: str,
        created_at: str,
        published_at: str,
        readback_at: str,
    ) -> dict[str, Any]:
        method_id = f"fixture-measurement-method-{suffix}-m0-meas-01"
        method_filename = f"measurement-method-{suffix}-m0-meas-01.json"
        method = {
            "schema": "ylx.measurement-method-record.v1",
            "method_record_id": method_id,
            "revision": 1,
            "predecessor_method_record_ref": None,
            "method_role": role,
            "applicable_measurement_ids": [measurement_id],
            "metric_ids": [metric_id],
            "algorithm_id": algorithm_id,
            "algorithm_version": "1.0.0",
            "procedure_steps": [
                "Select only the declared source-group side from the exact partition.",
                "Compute the per-session MJPEG bitrate distribution without holdout retuning.",
                "Return the declared p95 metric in Mbit/s for threshold-term construction.",
            ],
            "aggregation": "PERCENTILE",
            "percentile": 95.0,
            "repetition_rule": (
                "Use every expanded sample in each selected source group exactly once."
            ),
            "input_record_schema": "ylx.stage-evidence-record.v1",
            "output_contract": "MEASUREMENT_THRESHOLD_TERMS_V1",
            "partition_policy": partition_policy,
            "holdout_policy": holdout_policy,
            "created_at": created_at,
            "supersession_reason": None,
            "publication_protocol": (
                "EXTERNAL_CONTENT_ADDRESSED_LOCATOR_READBACK_V1"
            ),
            "publication_readback_schema": (
                "ylx.content-addressed-locator-readback.v1"
            ),
            "authority_effect": "NONE",
            "artifact_metadata": governance_input_metadata(),
        }
        method_sha = corpus.add(
            f"VALID-MEASUREMENT-METHOD-{role}-M0-MEAS-01-01",
            method_filename,
            "measurement-method-record-v1.schema.json",
            method,
        )
        method_path = f"{fixture_prefix}{method_filename}"
        method_ref = artifact_ref(
            method_id,
            method["schema"],
            method_sha,
            method_path,
            method["revision"],
        )
        locator = add_content_addressed_readback_fixture(
            corpus,
            case_id=(
                f"VALID-MEASUREMENT-METHOD-{role}-READBACK-M0-MEAS-01-01"
            ),
            filename=(
                "content-addressed-locator-readback-measurement-method-"
                f"{suffix}-m0-meas-01.json"
            ),
            locator_id=(
                f"fixture-measurement-method-{suffix}-m0-meas-01-locator"
            ),
            artifact_schema=method["schema"],
            artifact_id=method_id,
            artifact_path=method_path,
            artifact_sha256=method_sha,
            canonical_directory="measurement-method",
            canonical_slug=f"{suffix}-m0-meas-01",
            published_at=published_at,
            readback_at=readback_at,
        )
        return {
            "value": method,
            "sha": method_sha,
            "ref": method_ref,
            "locator": locator,
        }

    freeze_method = add_method(
        role="FREEZE_EVALUATION",
        suffix="freeze-evaluation",
        algorithm_id="mjpeg-bitrate-p95-freeze-evaluation",
        partition_policy="FORBIDDEN",
        holdout_policy="NOT_APPLICABLE",
        created_at="2026-06-01T12:05:00Z",
        published_at="2026-06-01T12:05:05Z",
        readback_at="2026-06-01T12:05:10Z",
    )
    statistical_method = add_method(
        role="STATISTICAL_FIT",
        suffix="statistical-fit",
        algorithm_id="source-group-disjoint-p95-fit",
        partition_policy="REQUIRED_SOURCE_GROUP_DISJOINT",
        holdout_policy="VALIDATION_ONLY_NO_RETUNING",
        created_at="2026-06-01T12:05:15Z",
        published_at="2026-06-01T12:05:20Z",
        readback_at="2026-06-01T12:05:25Z",
    )

    evaluation = build_execution_authorization_evaluation(
        corpus,
        planning_state,
        task_id=planning_state["m1_threshold_freeze_node_id"],
        action_instance_id="fixture-action-freeze-m0-meas-01-threshold",
        filename_slug="m1-threshold-freeze-m0-meas-01-pass",
        authorization_binding_context_ref=None,
        environment_class="governance-workspace",
        phase_barrier_ids=["milestone-entry/M1"],
        actor_person_id="fixture-capture-owner-person",
        additional_prerequisite_ref_by_kind={
            "stage_source_scope": copy.deepcopy(m1_stage_source_scope_ref),
            "measurement_data_selection": copy.deepcopy(
                training_selection["ref"]
            ),
            "training_evidence": copy.deepcopy(
                training_evidence_state["evidence_ref"]
            ),
            "training_evidence_binding": copy.deepcopy(
                training_evidence_state["binding_ref"]
            ),
            "freeze_method": copy.deepcopy(freeze_method["ref"]),
            "statistical_method": copy.deepcopy(statistical_method["ref"]),
            "data_partition": copy.deepcopy(partition_ref),
        },
        evaluated_at="2026-06-01T12:05:30Z",
    )

    threshold_node = next(
        node
        for node in planning_state["wbs"]["nodes"]
        if node["node_id"] == planning_state["m1_threshold_freeze_node_id"]
    )
    threshold_id = "fixture-measurement-threshold-m0-meas-01"
    threshold_filename = "measurement-threshold-record-m0-meas-01.json"
    threshold = {
        "schema": "ylx.measurement-threshold-record.v1",
        "threshold_record_id": threshold_id,
        "revision": 1,
        "predecessor_threshold_record_ref": None,
        "measurement_id": measurement_id,
        "threshold_kind": "DATA_FITTED",
        "threshold_terms": [
            {
                "metric_id": metric_id,
                "value": 24.0,
                "unit": "Mbit/s",
                "direction": "UPPER_BOUND",
            }
        ],
        "scope": {
            "scope_kind": "CANDIDATE_INDEPENDENT",
            "candidate_id": None,
            "qualification_revision": None,
            "binding_context_ref": None,
            "support_cell_ids": [
                "static-light",
                "high-motion",
                "bright-light",
                "low-light",
            ],
        },
        "freeze_gate": "M1",
        "authorization_class": "may_prepare",
        "authorization_action": "produce-governance-input",
        "task_id": planning_state["m1_threshold_freeze_node_id"],
        "execution_authorization_evaluation_ref": copy.deepcopy(
            evaluation["ref"]
        ),
        "action_instance_id": evaluation["value"]["action_instance_id"],
        "planned_action_input_sha256": evaluation["value"][
            "planned_action_input_sha256"
        ],
        "actor_assignment_ref": copy.deepcopy(
            evaluation["value"]["actor_assignment_ref"]
        ),
        "actor_person_id": evaluation["value"]["actor_person_id"],
        "checker_assignment_ref": copy.deepcopy(
            evaluation["value"]["checker_assignment_ref"]
        ),
        "checker_person_id": threshold_node["reviewer_ref"]["principal_id"],
        "owner_role_projection": threshold_node["executor_ref"]["role_id"],
        "checker_role_projection": threshold_node["reviewer_ref"]["role_id"],
        "freeze_evidence_refs": [
            {
                "evidence_record_ref": copy.deepcopy(
                    training_evidence_state["evidence_ref"]
                ),
                "data_partition_ref": copy.deepcopy(partition_ref),
                "partition_side": "TRAINING",
                "source_group_ids": copy.deepcopy(
                    training_selection["value"]["source_group_ids"]
                ),
            }
        ],
        "freeze_method_ref": copy.deepcopy(freeze_method["ref"]),
        "statistical_method_ref": copy.deepcopy(statistical_method["ref"]),
        "data_partition_ref": copy.deepcopy(partition_ref),
        "frozen_at": "2026-06-01T12:05:35Z",
        "supersession_reason": None,
        "publication_protocol": (
            "EXTERNAL_CONTENT_ADDRESSED_LOCATOR_READBACK_V1"
        ),
        "publication_readback_schema": (
            "ylx.content-addressed-locator-readback.v1"
        ),
        "authority_effect": "NONE",
        "artifact_metadata": governance_input_metadata(),
    }
    threshold_sha = corpus.add(
        "VALID-MEASUREMENT-THRESHOLD-M0-MEAS-01-01",
        threshold_filename,
        "measurement-threshold-record-v1.schema.json",
        threshold,
    )
    threshold_path = f"{fixture_prefix}{threshold_filename}"
    threshold_ref = artifact_ref(
        threshold_id,
        threshold["schema"],
        threshold_sha,
        threshold_path,
        threshold["revision"],
    )
    threshold_locator = add_content_addressed_readback_fixture(
        corpus,
        case_id="VALID-MEASUREMENT-THRESHOLD-READBACK-M0-MEAS-01-01",
        filename=(
            "content-addressed-locator-readback-measurement-threshold-"
            "m0-meas-01.json"
        ),
        locator_id="fixture-measurement-threshold-m0-meas-01-locator",
        artifact_schema=threshold["schema"],
        artifact_id=threshold_id,
        artifact_path=threshold_path,
        artifact_sha256=threshold_sha,
        canonical_directory="measurement-threshold",
        canonical_slug="m0-meas-01",
        published_at="2026-06-01T12:05:40Z",
        readback_at="2026-06-01T12:05:45Z",
    )
    holdout_selection = build_measurement_data_selection_fixture(
        corpus,
        measurement_id=measurement_id,
        partition=partition,
        partition_ref=partition_ref,
        partition_side="HOLDOUT",
        created_at="2026-06-01T12:05:50Z",
        published_at="2026-06-01T12:05:55Z",
        readback_at="2026-06-01T12:06:00Z",
    )
    state = {
        "evaluation": evaluation,
        "training_selection": training_selection,
        "training_evidence": training_evidence_state,
        "training_evidence_ref": training_evidence_state["evidence_ref"],
        "holdout_selection": holdout_selection,
        "partition_ref": partition_ref,
        "freeze_method": freeze_method,
        "statistical_method": statistical_method,
        "threshold": threshold,
        "threshold_sha": threshold_sha,
        "threshold_ref": threshold_ref,
        "threshold_locator": threshold_locator,
    }
    corpus.relationships["measurement_threshold_chain"] = {
        "execution_authorization_evaluation_ref": copy.deepcopy(
            evaluation["ref"]
        ),
        "training_selection_ref": copy.deepcopy(training_selection["ref"]),
        "training_selection_locator_ref": copy.deepcopy(
            training_selection["locator"]["ref"]
        ),
        "training_evidence_evaluation_ref": copy.deepcopy(
            training_evidence_state["evaluation"]["ref"]
        ),
        "training_evidence_ref": copy.deepcopy(
            training_evidence_state["evidence_ref"]
        ),
        "training_evidence_binding_ref": copy.deepcopy(
            training_evidence_state["binding_ref"]
        ),
        "data_partition_ref": copy.deepcopy(partition_ref),
        "freeze_method_ref": copy.deepcopy(freeze_method["ref"]),
        "freeze_method_locator_ref": copy.deepcopy(
            freeze_method["locator"]["ref"]
        ),
        "statistical_method_ref": copy.deepcopy(statistical_method["ref"]),
        "statistical_method_locator_ref": copy.deepcopy(
            statistical_method["locator"]["ref"]
        ),
        "threshold_ref": copy.deepcopy(threshold_ref),
        "threshold_locator_ref": copy.deepcopy(threshold_locator["ref"]),
        "holdout_selection_ref": copy.deepcopy(holdout_selection["ref"]),
        "holdout_selection_locator_ref": copy.deepcopy(
            holdout_selection["locator"]["ref"]
        ),
    }
    return state


def build_measurement_holdout_evidence_fixtures(
    corpus: Corpus,
    planning_state: dict[str, Any],
    threshold_state: dict[str, Any],
    context_state: dict[str, Any],
) -> dict[str, Any]:
    """Build the post-freeze M3 holdout evidence chain for M0-MEAS-01."""

    holdout_selection = threshold_state["holdout_selection"]
    evidence_state = build_measurement_evidence_fixture(
        corpus,
        planning_state,
        measurement_id="M0-MEAS-01",
        task_id=planning_state["m3_measurement_holdout_node_id"],
        partition_ref=threshold_state["partition_ref"],
        selection_state=holdout_selection,
        partition_side="HOLDOUT",
        source_scope_ref=context_state["m3_ref"],
        authorization_binding_context_ref=context_state["m3_ref"],
        phase_barrier_ids=["milestone-entry/M3"],
        prerequisite_ref_by_kind={
            "m2_binding_context": copy.deepcopy(context_state["m2_ref"]),
            "m3_binding_context": copy.deepcopy(context_state["m3_ref"]),
            "measurement_threshold": copy.deepcopy(
                threshold_state["threshold_ref"]
            ),
        },
        actor_person_id="fixture-capture-owner-person",
        environment_class="qualification-target",
        closing_gate="M3",
        evaluated_at="2026-06-01T12:15:00Z",
        evidence_created_at="2026-06-01T12:16:00Z",
        binding_created_at="2026-06-01T12:17:00Z",
    )
    state = {
        **evidence_state,
        "selection": holdout_selection,
        "evidence_wrapper": {
            "evidence_record_ref": copy.deepcopy(evidence_state["evidence_ref"]),
            "data_partition_ref": copy.deepcopy(threshold_state["partition_ref"]),
            "partition_side": "HOLDOUT",
            "source_group_ids": copy.deepcopy(
                holdout_selection["value"]["source_group_ids"]
            ),
        },
    }
    corpus.relationships["measurement_threshold_chain"].update(
        {
            "holdout_evidence_evaluation_ref": copy.deepcopy(
                evidence_state["evaluation"]["ref"]
            ),
            "holdout_evidence_ref": copy.deepcopy(
                evidence_state["evidence_ref"]
            ),
            "holdout_evidence_binding_ref": copy.deepcopy(
                evidence_state["binding_ref"]
            ),
        }
    )
    return state


def build_measurement_queue_fixture(
    corpus: Corpus,
    registry: dict[str, Any],
    history_state: dict[str, Any],
    threshold_ref: dict[str, Any],
    holdout_evidence_state: dict[str, Any],
    terminal_verdict_ref: dict[str, Any],
) -> dict[str, Any]:
    """Build the exact r1/r2/r3 non-authoritative M0 measurement projection."""

    expected_ids = [f"M0-MEAS-{index:02d}" for index in range(1, 12)]
    selected_head_state = history_state["selected_acceptance_head"]
    selected_head = selected_head_state["value"]

    def history_projection(
        head: dict[str, Any], head_sha: str, head_path: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        current_proposal = head.get("current_proposal")
        if not isinstance(current_proposal, dict):
            raise TypeError("acceptance history head lacks a current proposal")
        proposal_path = current_proposal.get("artifact_path")
        if not isinstance(proposal_path, str):
            raise TypeError("acceptance history proposal lacks an exact path")
        proposal_raw = (REPO_ROOT / proposal_path).read_bytes()
        proposal_sha = sha(proposal_raw)
        if proposal_sha != current_proposal.get("artifact_sha256"):
            raise AssertionError("acceptance history proposal digest drift")
        proposal = _load_json_without_duplicates(proposal_raw, proposal_path)
        if not (
            proposal.get("record_id") == current_proposal.get("record_id")
            and proposal.get("revision") == current_proposal.get("revision")
        ):
            raise AssertionError("acceptance history head/proposal tuple drift")

        proposal_registry = proposal["registry_artifact"]
        proposal_acceptance = proposal["acceptance_artifact"]
        proposal_id_set = proposal["canonical_id_set"]
        registry_archive_path = proposal_registry["archived_artifact_path"]
        registry_archive_raw = (REPO_ROOT / registry_archive_path).read_bytes()
        registry_archive_sha = sha(registry_archive_raw)
        if not (
            registry_archive_sha == proposal_registry["artifact_sha256"]
            == proposal_registry["archived_artifact_sha256"]
        ):
            raise AssertionError("acceptance history registry archive drift")
        historical_registry = yaml.safe_load(registry_archive_raw)
        if not isinstance(historical_registry, dict):
            raise TypeError("acceptance history registry archive is not an object")

        acceptance_archive_path = proposal_acceptance[
            "archived_artifact_path"
        ]
        acceptance_archive_sha = sha(
            (REPO_ROOT / acceptance_archive_path).read_bytes()
        )
        if not (
            acceptance_archive_sha == proposal_acceptance["artifact_sha256"]
            == proposal_acceptance["archived_artifact_sha256"]
        ):
            raise AssertionError("acceptance history ACCEPTANCE archive drift")

        historical_requirements = historical_registry.get("requirements")
        if not isinstance(historical_requirements, list):
            raise TypeError("historical registry requirements are not a list")
        historical_ids = [row["id"] for row in historical_requirements]
        canonical_id_set_path = proposal_id_set["artifact_path"]
        canonical_id_set_raw = (REPO_ROOT / canonical_id_set_path).read_bytes()
        expected_id_set_raw = "".join(
            f"{requirement_id}\n" for requirement_id in sorted(historical_ids)
        ).encode("ascii")
        if not (
            canonical_id_set_raw == expected_id_set_raw
            and sha(canonical_id_set_raw) == proposal_id_set["sha256"]
            and len(historical_ids)
            == len(set(historical_ids))
            == proposal_id_set["cardinality"]
            == 173
        ):
            raise AssertionError("acceptance history canonical ID-set drift")

        measurement_ids = sorted(
            requirement_id
            for requirement_id in historical_ids
            if requirement_id.startswith("M0-MEAS-")
        )
        if measurement_ids != expected_ids:
            raise AssertionError(
                "historical registry must select the exact ordered M0 measurements"
            )
        requirement_by_id = {
            row["id"]: row for row in historical_requirements
        }
        owner_patterns = historical_registry["policy"]["ownership"][
            "owner_patterns"
        ]

        def projected_owner(measurement_id: str) -> str:
            matches = [
                entry["owner_slot"]
                for entry in owner_patterns
                if any(
                    re.fullmatch(pattern, measurement_id)
                    for pattern in entry["patterns"]
                )
            ]
            if len(matches) != 1:
                raise AssertionError(
                    f"measurement {measurement_id} must match one historical owner"
                )
            return matches[0]

        binding = {
            "selected_proposal_ref": artifact_ref(
                proposal["record_id"],
                proposal["schema"],
                proposal_sha,
                proposal_path,
                proposal["revision"],
            ),
            "selected_head_ref": artifact_ref(
                head["record_id"],
                head["schema"],
                head_sha,
                head_path,
                head["history_revision"],
            ),
            "registry_revision": proposal_registry["scope_revision"],
            "registry_artifact_path": proposal_registry["artifact_path"],
            "registry_artifact_sha256": proposal_registry["artifact_sha256"],
            "registry_archived_artifact_path": registry_archive_path,
            "registry_archived_artifact_sha256": proposal_registry[
                "archived_artifact_sha256"
            ],
            "acceptance_artifact_path": proposal_acceptance["artifact_path"],
            "acceptance_artifact_sha256": proposal_acceptance[
                "artifact_sha256"
            ],
            "acceptance_archived_artifact_path": acceptance_archive_path,
            "acceptance_archived_artifact_sha256": proposal_acceptance[
                "archived_artifact_sha256"
            ],
            "canonical_id_set_artifact_path": canonical_id_set_path,
            "canonical_id_set_sha256": proposal_id_set["sha256"],
            "registry_cardinality": proposal_id_set["cardinality"],
            "measurement_id_set_sha256": ascii_set_sha256(measurement_ids),
            "measurement_cardinality": len(measurement_ids),
        }
        measurements = [
            {
                "measurement_id": measurement_id,
                "planning_state": "BLOCKED",
                "requirement_verdict_ref": None,
                "threshold_record_ref": None,
                "evidence_record_refs": [],
                "owner_projection": projected_owner(measurement_id),
                "closing_gate_projection": requirement_by_id[measurement_id][
                    "closing_gate"
                ],
                "blockers": [
                    blocker(
                        f"measurement-{measurement_id}-external-records-missing",
                        "UNKNOWN_DEPENDENCY",
                    )
                ],
            }
            for measurement_id in measurement_ids
        ]
        return binding, measurements

    r1_head_ref = selected_head.get("predecessor_head")
    if not isinstance(r1_head_ref, dict):
        raise TypeError("selected acceptance head lacks an exact predecessor")
    r1_head_path = r1_head_ref.get("artifact_path")
    if not isinstance(r1_head_path, str):
        raise TypeError("selected acceptance predecessor head lacks a path")
    r1_head_raw = (REPO_ROOT / r1_head_path).read_bytes()
    r1_head_sha = sha(r1_head_raw)
    if r1_head_sha != r1_head_ref.get("artifact_sha256"):
        raise AssertionError("selected acceptance predecessor-head digest drift")
    r1_head = _load_json_without_duplicates(r1_head_raw, r1_head_path)
    previous_head_ref = r1_head.get("predecessor_head")
    if not isinstance(previous_head_ref, dict):
        raise TypeError("queue r1 acceptance head lacks an exact predecessor")
    previous_head_path = previous_head_ref.get("artifact_path")
    if not isinstance(previous_head_path, str):
        raise TypeError("queue r1 acceptance predecessor head lacks a path")
    previous_head_raw = (REPO_ROOT / previous_head_path).read_bytes()
    previous_head_sha = sha(previous_head_raw)
    if previous_head_sha != previous_head_ref.get("artifact_sha256"):
        raise AssertionError("queue r1 predecessor-head digest drift")
    previous_head = _load_json_without_duplicates(
        previous_head_raw, previous_head_path
    )
    previous_registry_binding, _ = history_projection(
        previous_head,
        previous_head_sha,
        previous_head_path,
    )
    r1_registry_binding, initial_measurements = history_projection(
        r1_head,
        r1_head_sha,
        r1_head_path,
    )
    current_registry_binding, current_measurements = history_projection(
        selected_head,
        selected_head_state["sha256"],
        selected_head_state["source_path"],
    )
    selected_history_revision = selected_head.get("history_revision")
    if not isinstance(selected_history_revision, int) or selected_history_revision < 3:
        raise AssertionError(
            "measurement queue fixtures require a selected history head with two predecessors"
        )
    if not (
        previous_head.get("history_revision") == selected_history_revision - 2
        and r1_head.get("history_revision") == selected_history_revision - 1
        and r1_registry_binding["selected_proposal_ref"]["revision"]
        == selected_history_revision - 1
        and current_registry_binding["selected_proposal_ref"]["revision"]
        == selected_history_revision
    ):
        raise AssertionError(
            "measurement queue fixtures require three adjacent acceptance-history revisions"
        )
    live_measurement_ids = sorted(
        row["id"]
        for row in registry["requirements"]
        if row["id"].startswith("M0-MEAS-")
    )
    if live_measurement_ids != expected_ids:
        raise AssertionError("live registry measurement ID set drift")

    initial_queue = {
        "schema": "ylx.measurement-queue.v2",
        "queue_id": "fixture-m0-measurement-queue",
        "revision": 1,
        "predecessor_queue_ref": None,
        "content_sha256": "0" * 64,
        "content_digest_rule": (
            "SHA256_RFC8785_CANONICAL_JSON_EXCLUDING_CONTENT_SHA256"
        ),
        "registry_binding": r1_registry_binding,
        "measurements": initial_measurements,
        "created_at": "2026-06-01T12:04:40Z",
        "authority_effect": "NONE",
        "artifact_metadata": metadata(),
    }

    def update_content_sha256(queue: dict[str, Any]) -> None:
        queue["content_sha256"] = sha(
            canonical_bytes(
                {
                    key: value
                    for key, value in queue.items()
                    if key != "content_sha256"
                }
            )
        )

    update_content_sha256(initial_queue)
    initial_queue_sha = corpus.add(
        "VALID-MEASUREMENT-QUEUE-V2-INITIAL-01",
        "measurement-queue-v2-r1.json",
        "measurement-queue-v2.schema.json",
        initial_queue,
    )

    ready_queue = copy.deepcopy(initial_queue)
    ready_queue.update(
        {
            "revision": 2,
            "predecessor_queue_ref": artifact_ref(
                initial_queue["queue_id"],
                initial_queue["schema"],
                initial_queue_sha,
                (
                    "contracts/fixtures/governance-models/valid/"
                    "measurement-queue-v2-r1.json"
                ),
                1,
            ),
            "created_at": "2026-06-01T12:20:00Z",
            "registry_binding": copy.deepcopy(current_registry_binding),
            "measurements": copy.deepcopy(current_measurements),
        }
    )
    ready_row = next(
        row
        for row in ready_queue["measurements"]
        if row["measurement_id"] == "M0-MEAS-01"
    )
    ready_row.update(
        {
            "planning_state": "READY_FOR_VERDICT",
            "threshold_record_ref": copy.deepcopy(threshold_ref),
            "evidence_record_refs": [
                copy.deepcopy(holdout_evidence_state["evidence_wrapper"])
            ],
            "blockers": [],
        }
    )
    update_content_sha256(ready_queue)
    ready_queue_sha = corpus.add(
        "VALID-MEASUREMENT-QUEUE-V2-READY-FOR-VERDICT-01",
        "measurement-queue-v2-r2.json",
        "measurement-queue-v2.schema.json",
        ready_queue,
    )

    queue = copy.deepcopy(ready_queue)
    queue.update(
        {
            "revision": 3,
            "predecessor_queue_ref": artifact_ref(
                ready_queue["queue_id"],
                ready_queue["schema"],
                ready_queue_sha,
                (
                    "contracts/fixtures/governance-models/valid/"
                    "measurement-queue-v2-r2.json"
                ),
                2,
            ),
            "created_at": "2026-06-01T12:54:00Z",
        }
    )
    verdict_row = next(
        row
        for row in queue["measurements"]
        if row["measurement_id"] == "M0-MEAS-01"
    )
    verdict_row["requirement_verdict_ref"] = copy.deepcopy(
        terminal_verdict_ref
    )
    update_content_sha256(queue)
    queue_sha = corpus.add(
        "VALID-MEASUREMENT-QUEUE-V2-TERMINAL-VERDICT-01",
        "measurement-queue-v2.json",
        "measurement-queue-v2.schema.json",
        queue,
    )
    corpus.relationships["measurement_queue_chain"] = {
        "r1_sha256": initial_queue_sha,
        "r2_sha256": ready_queue_sha,
        "r3_sha256": queue_sha,
        "r1_registry_binding": copy.deepcopy(r1_registry_binding),
        "current_registry_binding": copy.deepcopy(current_registry_binding),
        "previous_registry_binding": copy.deepcopy(previous_registry_binding),
        "threshold_ref": copy.deepcopy(threshold_ref),
        "holdout_evidence_ref": copy.deepcopy(
            holdout_evidence_state["evidence_ref"]
        ),
        "holdout_evidence_binding_ref": copy.deepcopy(
            holdout_evidence_state["binding_ref"]
        ),
        "terminal_verdict_ref": copy.deepcopy(terminal_verdict_ref),
    }
    return {
        "initial_value": initial_queue,
        "initial_sha": initial_queue_sha,
        "ready_value": ready_queue,
        "ready_sha": ready_queue_sha,
        "value": queue,
        "sha": queue_sha,
    }


def g0_acceptance_history_subject(
    history_state: dict[str, Any],
) -> dict[str, Any]:
    """Project the exact selected draft proposal/head tuple into the G0 subject."""

    proposal_state = history_state["selected_acceptance_proposal"]
    proposal = proposal_state["value"]
    head_state = history_state["selected_acceptance_head"]
    head = head_state["value"]
    current_proposal = head.get("current_proposal")
    if not isinstance(current_proposal, dict):
        raise TypeError("selected G0 acceptance head lacks current_proposal")
    if (
        current_proposal.get("record_id") != proposal.get("record_id")
        or current_proposal.get("artifact_path") != proposal_state["source_path"]
        or current_proposal.get("artifact_sha256") != proposal_state["sha256"]
    ):
        raise AssertionError("selected G0 acceptance proposal/head tuple drift")

    registry_artifact = proposal["registry_artifact"]
    acceptance_artifact = proposal["acceptance_artifact"]
    canonical_id_set = proposal["canonical_id_set"]
    archive_bindings = (
        (
            registry_artifact["archived_artifact_path"],
            registry_artifact["archived_artifact_sha256"],
        ),
        (
            acceptance_artifact["archived_artifact_path"],
            acceptance_artifact["archived_artifact_sha256"],
        ),
        (
            canonical_id_set["artifact_path"],
            canonical_id_set["sha256"],
        ),
    )
    for artifact_path, expected_sha256 in archive_bindings:
        raw = (REPO_ROOT / artifact_path).read_bytes()
        if sha(raw) != expected_sha256:
            raise AssertionError(
                f"selected G0 acceptance-history input drift: {artifact_path}"
            )

    return {
        "proposal_record_id": proposal["record_id"],
        "proposal_artifact_path": proposal_state["source_path"],
        "proposal_artifact_sha256": proposal_state["sha256"],
        "history_head_record_id": head["record_id"],
        "history_head_artifact_path": head_state["source_path"],
        "history_head_artifact_sha256": head_state["sha256"],
        "registry_archive_artifact_path": registry_artifact[
            "archived_artifact_path"
        ],
        "registry_archive_artifact_sha256": registry_artifact[
            "archived_artifact_sha256"
        ],
        "acceptance_archive_artifact_path": acceptance_artifact[
            "archived_artifact_path"
        ],
        "acceptance_archive_artifact_sha256": acceptance_artifact[
            "archived_artifact_sha256"
        ],
        "canonical_id_set_artifact_path": canonical_id_set["artifact_path"],
        "canonical_id_set_sha256": canonical_id_set["sha256"],
    }


def build_g0_policy_ratification(
    corpus: Corpus, history_state: dict[str, Any]
) -> dict[str, Any]:
    """Build a closed synthetic four-key G0 F/P/R tuple."""

    fixture_prefix = "contracts/fixtures/governance-models/"
    valid_prefix = f"{fixture_prefix}valid/"
    support_prefix = f"{fixture_prefix}support/"
    repository_locator = "fixture://canonical-governance-subject/repository/r1"
    receipt_domain = "YLX-TERMINAL-AUDIT-RECEIPT-V1"
    authority_locator = f"{valid_prefix}g0-external-organizational-authority.json"
    quorum_locator = f"{valid_prefix}g0-quorum-policy.json"
    subject_locator = f"{valid_prefix}g0-policy-ratification-subject.json"
    event_locator = f"{valid_prefix}g0-policy-ratification.json"
    publication_locator = (
        f"{valid_prefix}g0-policy-ratification-publication-receipt.json"
    )
    readback_locator = (
        f"{valid_prefix}g0-policy-ratification-readback-receipt.json"
    )
    role_ids = ["release-owner", "security-owner"]

    def key_material(label: str, key_id: str) -> dict[str, Any]:
        seed = hashlib.sha256(
            f"YLX SYNTHETIC G0 TEST KEY ONLY:{label}".encode("ascii")
        ).digest()
        private_key = Ed25519PrivateKey.from_private_bytes(seed)
        public_raw = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return {
            "key_id": key_id,
            "private_key": private_key,
            "public_key_base64": base64.b64encode(public_raw).decode("ascii"),
            "fingerprint_sha256": sha(public_raw),
        }

    trust_anchor_key = key_material(
        "external-trust-anchor", "fixture-g0-external-trust-anchor-key"
    )
    grant_issuer_key = key_material(
        "operation-grant-issuer", "fixture-g0-operation-grant-issuer-key"
    )
    receipt_issuer_key = key_material(
        "terminal-receipt-issuer", "fixture-g0-terminal-receipt-issuer-key"
    )
    approval_key_by_role = {
        role_id: key_material(
            f"external-approval:{role_id}",
            f"fixture-g0-external-approval-key-{role_id}",
        )
        for role_id in role_ids
    }
    approver_person_by_role = {
        "release-owner": "fixture-external-policy-owner-person",
        "security-owner": "fixture-external-governance-reviewer-person",
    }

    corpus.relationships["g0_test_only_external_trust_anchor"] = {
        "trust_anchor_id": "fixture-g0-external-trust-anchor",
        "signing_key_id": trust_anchor_key["key_id"],
        "public_key_base64": trust_anchor_key["public_key_base64"],
        "fingerprint_sha256": trust_anchor_key["fingerprint_sha256"],
        "test_only": True,
    }

    authority_payload = {
        "schema": "ylx.external-organizational-authority.v1",
        "authority_id": "fixture-g0-external-organizational-authority",
        "revision": 1,
        "predecessor_authority_sha256": None,
        "authority_kind": "EXTERNAL_ORGANIZATIONAL_GOVERNANCE_AUTHORITY",
        "authority_origin": "PREEXISTING_OUTSIDE_CANDIDATE_POLICY",
        "authority_status": "ACTIVE",
        "scope": ["PROSPECTIVE_G0_POLICY_RATIFICATION"],
        "eligible_approver_by_role_id": {
            role_id: {
                "person_id": approver_person_by_role[role_id],
                "accountable_natural_person": True,
                "approval_signing_key_id": approval_key_by_role[role_id][
                    "key_id"
                ],
                "approval_public_key_base64": approval_key_by_role[role_id][
                    "public_key_base64"
                ],
                "eligible_from": "2026-01-01T00:00:00Z",
                "eligible_until": "2027-01-01T00:00:00Z",
                "conflict_group_id": f"fixture-independent-{role_id}",
            }
            for role_id in role_ids
        },
        "quorum": {
            "required_role_ids": role_ids,
            "minimum_approval_count": 2,
            "require_all_roles": True,
            "distinct_natural_persons": True,
            "conflict_rule": "DISTINCT_NATURAL_PERSONS_AND_CONFLICT_GROUPS",
            "approval_order_rule": "NO_REQUIRED_ORDER",
        },
        "grant_issuer": {
            "issuer_id": "fixture-g0-operation-grant-issuer",
            "signing_key_id": grant_issuer_key["key_id"],
            "public_key_base64": grant_issuer_key["public_key_base64"],
            "allowed_grant_kinds": [
                "G0_EVENT_PUBLISHER",
                "REPOSITORY_PERMISSION",
                "G0_EVENT_READBACK",
                "EXTERNAL_OUTREACH_SEND",
                "CANONICAL_LOCATOR_READBACK",
                "TERMINAL_AUDIT_SINK",
            ],
        },
        "authority_effect": "PROSPECTIVE_ONLY",
        "effective_semantics": {
            "application": "PROSPECTIVE_FROM_EFFECTIVE_AT",
            "retrospective_effect": "NONE",
            "source_history_mutation": "FORBIDDEN",
            "environment_scope": "REPOSITORY_GOVERNANCE",
        },
        "effective_from": "2026-01-01T00:00:00Z",
        "not_after": "2027-01-01T00:00:00Z",
        "revoked_at": None,
        "trust_anchor_id": "fixture-g0-external-trust-anchor",
        "signature_algorithm": "Ed25519",
        "signing_key_id": trust_anchor_key["key_id"],
    }
    external_authority = sign_closed_record(
        authority_payload,
        private_key=trust_anchor_key["private_key"],
        signature_domain="YLX-G0-EXTERNAL-ORGANIZATIONAL-AUTHORITY-V1",
    )
    external_authority_sha = corpus.add(
        "VALID-G0-EXTERNAL-ORGANIZATIONAL-AUTHORITY-01",
        "g0-external-organizational-authority.json",
        "external-organizational-authority-v1.schema.json",
        external_authority,
    )
    external_authority_ref = artifact_ref(
        external_authority["authority_id"],
        external_authority["schema"],
        external_authority_sha,
        authority_locator,
        external_authority["revision"],
    )

    quorum_payload = {
        "schema": "ylx.g0-quorum-policy.v1",
        "policy_id": "fixture-g0-quorum-policy",
        "revision": 1,
        "predecessor_policy_sha256": None,
        "external_authority_ref": authority_locator,
        "external_authority_sha256": external_authority_sha,
        "scope": "PROSPECTIVE_G0_POLICY_RATIFICATION",
        "required_role_ids": role_ids,
        "minimum_approval_count": 2,
        "require_all_roles": True,
        "distinct_natural_persons": True,
        "conflict_rule": "DISTINCT_NATURAL_PERSONS_AND_CONFLICT_GROUPS",
        "approval_order_rule": "NO_REQUIRED_ORDER",
        "approval_decision": "APPROVE_PROSPECTIVE_ONLY",
        "subject_schema": "ylx.g0-policy-ratification-subject.v1",
        "subject_digest_algorithm": "SHA-256",
        "approval_signature_domain_template": (
            "YLX-G0-EXTERNAL-APPROVAL-V1/<role_id>"
        ),
        "effective_from": "2026-01-01T00:00:00Z",
        "not_after": "2027-01-01T00:00:00Z",
        "issuer_id": external_authority["grant_issuer"]["issuer_id"],
        "signature_algorithm": "Ed25519",
        "signing_key_id": grant_issuer_key["key_id"],
    }
    quorum_policy = sign_closed_record(
        quorum_payload,
        private_key=grant_issuer_key["private_key"],
        signature_domain="YLX-G0-QUORUM-POLICY-V1",
    )
    quorum_policy_sha = corpus.add(
        "VALID-G0-QUORUM-POLICY-01",
        "g0-quorum-policy.json",
        "g0-quorum-policy-v1.schema.json",
        quorum_policy,
    )
    quorum_policy_ref = artifact_ref(
        quorum_policy["policy_id"],
        quorum_policy["schema"],
        quorum_policy_sha,
        quorum_locator,
        quorum_policy["revision"],
    )

    def add_operation_grant(
        *,
        filename: str,
        grant_id: str,
        grant_kind: str,
        grant: dict[str, Any],
        issued_at: str,
    ) -> tuple[dict[str, Any], str, str]:
        locator = f"{valid_prefix}{filename}"
        payload = {
            "schema": "ylx.g0-operation-authority.v1",
            "grant_id": grant_id,
            "grant_kind": grant_kind,
            "external_authority_ref": authority_locator,
            "external_authority_sha256": external_authority_sha,
            "grant": grant,
            "issued_at": issued_at,
            "valid_from": issued_at,
            "not_after": "2027-01-01T00:00:00Z",
            "authority_origin": "PREEXISTING_OUTSIDE_CANDIDATE_POLICY",
            "issuer_id": external_authority["grant_issuer"]["issuer_id"],
            "signature_algorithm": "Ed25519",
            "signing_key_id": grant_issuer_key["key_id"],
        }
        value = sign_closed_record(
            payload,
            private_key=grant_issuer_key["private_key"],
            signature_domain=f"YLX-G0-OPERATION-AUTHORITY-V1/{grant_kind}",
        )
        case_slug = filename.removesuffix(".json").upper().replace("_", "-")
        digest = corpus.add(
            f"VALID-{case_slug}-01",
            filename,
            "g0-operation-authority-v1.schema.json",
            value,
        )
        return value, digest, locator

    def add_sink_grant(
        *, filename: str, grant_id: str, receipt_schema: str, operation_class: str
    ) -> tuple[dict[str, Any], str, str]:
        return add_operation_grant(
            filename=filename,
            grant_id=grant_id,
            grant_kind="TERMINAL_AUDIT_SINK",
            grant={
                "sink_id": "fixture-g0-terminal-audit-sink",
                "sink_locator": "fixture://g0-terminal-audit-sink/r1",
                "receipt_schema": receipt_schema,
                "receipt_issuer_id": "fixture-g0-terminal-receipt-issuer",
                "receipt_signing_key_id": receipt_issuer_key["key_id"],
                "receipt_signing_public_key_base64": receipt_issuer_key[
                    "public_key_base64"
                ],
                "operation_class": operation_class,
            },
            issued_at="2026-01-01T00:01:00Z",
        )

    def add_repository_receipt(
        *,
        filename: str,
        receipt_id: str,
        permission_locator: str,
        permission_sha256: str,
        actor_id: str,
        operation: str,
        target_scope: str,
        output_ref: str,
        output_sha256: str,
        result: str,
        started_at: str,
        completed_at: str,
    ) -> tuple[dict[str, Any], str, str]:
        locator = f"{valid_prefix}{filename}"
        payload = {
            "schema": "ylx.repository-operation-receipt.v1",
            "receipt_id": receipt_id,
            "sink_id": "fixture-g0-terminal-audit-sink",
            "permission_ref": permission_locator,
            "permission_sha256": permission_sha256,
            "repository_locator": repository_locator,
            "actor_id": actor_id,
            "operation": operation,
            "target_scope": target_scope,
            "started_at": started_at,
            "completed_at": completed_at,
            "output_ref": output_ref,
            "output_sha256": output_sha256,
            "result": result,
            "fsync_result": "FILE_AND_PARENT_DIRECTORY_DURABLE",
            "issuer_id": "fixture-g0-terminal-receipt-issuer",
            "issued_at": completed_at,
            "signature_algorithm": "Ed25519",
            "signing_key_id": receipt_issuer_key["key_id"],
        }
        value = sign_closed_record(
            payload,
            private_key=receipt_issuer_key["private_key"],
            signature_domain=receipt_domain,
        )
        case_slug = filename.removesuffix(".json").upper().replace("_", "-")
        digest = corpus.add(
            f"VALID-{case_slug}-01",
            filename,
            "repository-operation-receipt-v1.schema.json",
            value,
        )
        return value, digest, locator

    _, repository_sink_sha, repository_sink_locator = add_sink_grant(
        filename="g0-operation-authority-repository-receipt-sink.json",
        grant_id="fixture-g0-repository-receipt-sink-authority",
        receipt_schema="ylx.repository-operation-receipt.v1",
        operation_class="REPOSITORY_WRITE",
    )
    _, outreach_sink_sha, outreach_sink_locator = add_sink_grant(
        filename="g0-operation-authority-outreach-receipt-sink.json",
        grant_id="fixture-g0-outreach-receipt-sink-authority",
        receipt_schema="ylx.external-outreach-send-receipt.v1",
        operation_class="OUTBOUND_SEND",
    )
    _, locator_sink_sha, locator_sink_locator = add_sink_grant(
        filename="g0-operation-authority-canonical-readback-sink.json",
        grant_id="fixture-g0-canonical-readback-sink-authority",
        receipt_schema="ylx.g0-canonical-locator-readback.v1",
        operation_class="CANONICAL_LOCATOR_READBACK",
    )
    _, publication_sink_sha, publication_sink_locator = add_sink_grant(
        filename="g0-operation-authority-event-publication-sink.json",
        grant_id="fixture-g0-event-publication-sink-authority",
        receipt_schema="ylx.g0-policy-ratification-publication-receipt.v1",
        operation_class="G0_EVENT_PUBLICATION",
    )
    _, readback_sink_sha, readback_sink_locator = add_sink_grant(
        filename="g0-operation-authority-event-readback-sink.json",
        grant_id="fixture-g0-event-readback-sink-authority",
        receipt_schema="ylx.g0-policy-ratification-readback-receipt.v1",
        operation_class="G0_EVENT_READBACK",
    )

    decision_proposal_refs: dict[str, dict[str, str]] = {}
    for decision_id in ("D-028", "D-029", "D-030"):
        matches = sorted(
            (
                REPO_ROOT
                / "docs"
                / "evidence"
                / "governance"
                / "decision-history"
                / "proposals"
            ).glob(f"{decision_id}--*.json")
        )
        if len(matches) != 1:
            raise AssertionError(
                f"expected one current repository proposal for {decision_id}"
            )
        path = matches[0]
        relative_path = path.relative_to(REPO_ROOT).as_posix()
        value = repo_json(relative_path)
        if value.get("decision_id") != decision_id:
            raise AssertionError(f"decision proposal identity drift for {decision_id}")
        decision_proposal_refs[decision_id] = {
            "artifact_path": relative_path,
            "artifact_sha256": sha(path.read_bytes()),
        }

    acceptance_history_subject = g0_acceptance_history_subject(history_state)
    exact_five_path_sha256_by_path = {
        ref["artifact_path"]: ref["artifact_sha256"]
        for ref in decision_proposal_refs.values()
    }
    exact_five_path_sha256_by_path.update(
        {
            acceptance_history_subject["proposal_artifact_path"]: (
                acceptance_history_subject["proposal_artifact_sha256"]
            ),
            acceptance_history_subject["history_head_artifact_path"]: (
                acceptance_history_subject["history_head_artifact_sha256"]
            ),
        }
    )
    if len(exact_five_path_sha256_by_path) != 5:
        raise AssertionError("G0 canonical clean commit must bind exactly five paths")
    subject_commit_tree_sha256 = sha(
        canonical_bytes(exact_five_path_sha256_by_path)
    )
    subject_commit_id = "fixture-g0-canonical-subject-commit-r1"
    clean_commit_filename = "g0-canonical-clean-commit.json"
    clean_commit_locator = f"{support_prefix}{clean_commit_filename}"
    clean_commit_target_scope = (
        f"{repository_locator}/commits/{subject_commit_id}"
    )
    clean_commit = {
        "schema": "ylx.g0-canonical-clean-commit.v1",
        "commit_id": subject_commit_id,
        "repository_locator": repository_locator,
        "subject_commit_tree_sha256": subject_commit_tree_sha256,
        "exact_five_path_sha256_by_path": exact_five_path_sha256_by_path,
        "worktree_state": "CLEAN_EXACT_FIVE_PATHS",
        "committed_at": "2026-06-01T12:00:00Z",
        "notice": NOTICE,
    }
    clean_commit_sha = corpus.add_support(
        clean_commit_filename,
        clean_commit,
        (
            "Synthetic earlier clean commit containing exactly the five external "
            "decision-proposal and acceptance-history files."
        ),
    )
    clean_commit_ref = artifact_ref(
        clean_commit["commit_id"],
        clean_commit["schema"],
        clean_commit_sha,
        clean_commit_locator,
        1,
    )

    _, clean_permission_sha, clean_permission_locator = add_operation_grant(
        filename="g0-operation-authority-clean-commit-publication.json",
        grant_id="fixture-g0-clean-commit-publication-permission",
        grant_kind="REPOSITORY_PERMISSION",
        grant={
            "repository_locator": repository_locator,
            "actor_id": "fixture-g0-canonical-commit-publisher",
            "operation": "CREATE_CLEAN_COMMIT",
            "target_scope": clean_commit_target_scope,
            "payload_sha256": clean_commit_sha,
            "terminal_sink_authority_ref": repository_sink_locator,
            "terminal_sink_authority_sha256": repository_sink_sha,
        },
        issued_at="2026-01-01T00:02:00Z",
    )
    (
        clean_commit_operation_receipt,
        clean_commit_operation_receipt_sha,
        clean_commit_operation_receipt_locator,
    ) = add_repository_receipt(
        filename="g0-canonical-clean-commit-operation-receipt.json",
        receipt_id="fixture-g0-canonical-clean-commit-operation-receipt",
        permission_locator=clean_permission_locator,
        permission_sha256=clean_permission_sha,
        actor_id="fixture-g0-canonical-commit-publisher",
        operation="CREATE_CLEAN_COMMIT",
        target_scope=clean_commit_target_scope,
        output_ref=clean_commit_locator,
        output_sha256=clean_commit_sha,
        result="CREATED_EXACT",
        started_at="2026-06-01T12:00:01Z",
        completed_at="2026-06-01T12:00:02Z",
    )

    _, locator_read_authority_sha, locator_read_authority_locator = (
        add_operation_grant(
            filename="g0-operation-authority-canonical-locator-readback.json",
            grant_id="fixture-g0-canonical-locator-readback-authority",
            grant_kind="CANONICAL_LOCATOR_READBACK",
            grant={
                "reader_id": "fixture-g0-canonical-locator-reader",
                "repository_locator": repository_locator,
                "subject_commit_id": subject_commit_id,
                "subject_commit_tree_sha256": subject_commit_tree_sha256,
                "exact_five_path_sha256_by_path": (
                    exact_five_path_sha256_by_path
                ),
                "operation": "EXACT_READBACK",
                "target_scope": clean_commit_target_scope,
                "terminal_sink_authority_ref": locator_sink_locator,
                "terminal_sink_authority_sha256": locator_sink_sha,
            },
            issued_at="2026-01-01T00:03:00Z",
        )
    )
    locator_readback_payload = {
        "schema": "ylx.g0-canonical-locator-readback.v1",
        "receipt_id": "fixture-g0-canonical-locator-readback",
        "commit_operation_receipt_ref": clean_commit_operation_receipt_locator,
        "commit_operation_receipt_sha256": clean_commit_operation_receipt_sha,
        "repository_locator": repository_locator,
        "target_scope": clean_commit_target_scope,
        "subject_commit_id": subject_commit_id,
        "subject_commit_tree_sha256": subject_commit_tree_sha256,
        "exact_five_path_sha256_by_path": exact_five_path_sha256_by_path,
        "reader_id": "fixture-g0-canonical-locator-reader",
        "readback_authority_ref": locator_read_authority_locator,
        "readback_authority_sha256": locator_read_authority_sha,
        "observed_commit_id": subject_commit_id,
        "observed_commit_tree_sha256": subject_commit_tree_sha256,
        "observed_five_path_sha256_by_path": exact_five_path_sha256_by_path,
        "read_back_at": "2026-06-01T12:00:03Z",
        "result": "EXACT_COMMIT_TREE_AND_FIVE_PATH_BYTES_MATCH",
        "terminal_sink_id": "fixture-g0-terminal-audit-sink",
        "receipt_issuer_id": "fixture-g0-terminal-receipt-issuer",
        "signature_algorithm": "Ed25519",
        "signing_key_id": receipt_issuer_key["key_id"],
    }
    locator_readback = sign_closed_record(
        locator_readback_payload,
        private_key=receipt_issuer_key["private_key"],
        signature_domain=receipt_domain,
    )
    locator_readback_filename = "g0-canonical-locator-readback.json"
    locator_readback_locator = f"{valid_prefix}{locator_readback_filename}"
    locator_readback_sha = corpus.add(
        "VALID-G0-CANONICAL-LOCATOR-READBACK-01",
        locator_readback_filename,
        "g0-canonical-locator-readback-v1.schema.json",
        locator_readback,
    )

    proposal_extension_reconciliation_decision = {
        "proposal_id": "D-028",
        "decision": "RATIFY_INTEGRATED_FOUR_FIELD_TERMINAL_REFERENCE",
        "exact_proposal_terminal_reference_fields": [
            "kind",
            "payload_locator",
            "payload_sha256",
        ],
        "candidate_extension_field": "payload_ref",
        "ratified_terminal_reference_fields": [
            "kind",
            "payload_ref",
            "payload_locator",
            "payload_sha256",
        ],
        "payload_ref_binding": {
            "artifact_path_equals": "payload_locator",
            "artifact_sha256_equals": "payload_sha256",
        },
        "d028_proposal_mutation": "FORBIDDEN",
        "implicit_union": "FORBIDDEN",
    }
    canonical_governance_subject = {
        "repository_locator": repository_locator,
        "subject_commit_id": subject_commit_id,
        "subject_commit_tree_sha256": subject_commit_tree_sha256,
        "exact_five_path_sha256_by_path": exact_five_path_sha256_by_path,
        "locator_readback_ref": locator_readback_locator,
        "locator_readback_sha256": locator_readback_sha,
    }
    ratification_subject = {
        "decision_proposal_ref_by_id": decision_proposal_refs,
        "acceptance_history_subject": acceptance_history_subject,
        "canonical_governance_subject": canonical_governance_subject,
        "proposal_extension_reconciliation_decision": (
            proposal_extension_reconciliation_decision
        ),
    }
    ratification_subject_sha = sha(canonical_bytes(ratification_subject))
    stored_ratification_subject_sha = corpus.add(
        "VALID-G0-POLICY-RATIFICATION-SUBJECT-01",
        "g0-policy-ratification-subject.json",
        "g0-policy-ratification-subject-v1.schema.json",
        ratification_subject,
    )
    if stored_ratification_subject_sha != ratification_subject_sha:
        raise AssertionError("stored G0 subject digest drift")

    _, subject_permission_sha, subject_permission_locator = add_operation_grant(
        filename="g0-operation-authority-subject-publication.json",
        grant_id="fixture-g0-subject-publication-permission",
        grant_kind="REPOSITORY_PERMISSION",
        grant={
            "repository_locator": repository_locator,
            "actor_id": "fixture-g0-request-preparer",
            "operation": "CREATE_IF_ABSENT",
            "target_scope": subject_locator,
            "payload_sha256": ratification_subject_sha,
            "terminal_sink_authority_ref": repository_sink_locator,
            "terminal_sink_authority_sha256": repository_sink_sha,
        },
        issued_at="2026-06-01T12:00:04Z",
    )
    add_repository_receipt(
        filename="g0-policy-ratification-subject-operation-receipt.json",
        receipt_id="fixture-g0-policy-ratification-subject-operation-receipt",
        permission_locator=subject_permission_locator,
        permission_sha256=subject_permission_sha,
        actor_id="fixture-g0-request-preparer",
        operation="CREATE_IF_ABSENT",
        target_scope=subject_locator,
        output_ref=subject_locator,
        output_sha256=ratification_subject_sha,
        result="CREATED_EXACT",
        started_at="2026-06-01T12:00:05Z",
        completed_at="2026-06-01T12:00:06Z",
    )

    send_receipt_by_role: dict[str, dict[str, Any]] = {}
    send_receipt_sha_by_role: dict[str, str] = {}
    send_receipt_locator_by_role: dict[str, str] = {}
    for sequence, role_id in enumerate(role_ids, start=1):
        person_id = approver_person_by_role[role_id]
        _, outreach_sha, outreach_locator = add_operation_grant(
            filename=f"g0-operation-authority-outreach-{role_id}.json",
            grant_id=f"fixture-g0-outreach-authority-{role_id}",
            grant_kind="EXTERNAL_OUTREACH_SEND",
            grant={
                "sender_id": "fixture-g0-request-sender",
                "recipient_id": person_id,
                "channel": "fixture-secure-governance-channel",
                "subject_sha256": ratification_subject_sha,
                "operation": "SEND",
                "request_ref": subject_locator,
                "request_sha256": ratification_subject_sha,
                "target_scope": f"fixture://g0-approver-inbox/{role_id}",
                "terminal_sink_authority_ref": outreach_sink_locator,
                "terminal_sink_authority_sha256": outreach_sink_sha,
            },
            issued_at=f"2026-06-01T12:00:{6 + sequence:02d}Z",
        )
        send_payload = {
            "schema": "ylx.external-outreach-send-receipt.v1",
            "receipt_id": f"fixture-g0-outreach-send-receipt-{role_id}",
            "sink_id": "fixture-g0-terminal-audit-sink",
            "outreach_authority_ref": outreach_locator,
            "outreach_authority_sha256": outreach_sha,
            "sender_id": "fixture-g0-request-sender",
            "recipient_id": person_id,
            "channel": "fixture-secure-governance-channel",
            "subject_sha256": ratification_subject_sha,
            "request_ref": subject_locator,
            "request_sha256": ratification_subject_sha,
            "channel_message_id": f"fixture-g0-request-message-{role_id}",
            "sent_at": f"2026-06-01T12:00:{8 + sequence:02d}Z",
            "result": "SENT",
            "issuer_id": "fixture-g0-terminal-receipt-issuer",
            "issued_at": f"2026-06-01T12:00:{8 + sequence:02d}Z",
            "signature_algorithm": "Ed25519",
            "signing_key_id": receipt_issuer_key["key_id"],
        }
        send_receipt = sign_closed_record(
            send_payload,
            private_key=receipt_issuer_key["private_key"],
            signature_domain=receipt_domain,
        )
        filename = f"g0-external-outreach-send-receipt-{role_id}.json"
        locator = f"{valid_prefix}{filename}"
        digest = corpus.add(
            f"VALID-G0-EXTERNAL-OUTREACH-SEND-RECEIPT-{role_id.upper()}-01",
            filename,
            "external-outreach-send-receipt-v1.schema.json",
            send_receipt,
        )
        send_receipt_by_role[role_id] = send_receipt
        send_receipt_sha_by_role[role_id] = digest
        send_receipt_locator_by_role[role_id] = locator

    approval_by_role: dict[str, dict[str, Any]] = {}
    approval_sha_by_role: dict[str, str] = {}
    approval_locator_by_role: dict[str, str] = {}
    for sequence, role_id in enumerate(role_ids, start=1):
        approved_at = f"2026-06-01T12:01:{sequence:02d}Z"
        approval_payload = {
            "schema": "ylx.g0-external-approval.v1",
            "approval_id": f"fixture-g0-external-approval-{role_id}",
            "subject_ref": subject_locator,
            "subject_sha256": ratification_subject_sha,
            "external_authority_ref": authority_locator,
            "external_authority_sha256": external_authority_sha,
            "quorum_policy_ref": quorum_locator,
            "quorum_policy_sha256": quorum_policy_sha,
            "role_id": role_id,
            "approver_person_id": approver_person_by_role[role_id],
            "decision": "APPROVE_PROSPECTIVE_ONLY",
            "approved_at": approved_at,
            "valid_until": "2027-01-01T00:00:00Z",
            "request_send_receipt_ref": send_receipt_locator_by_role[role_id],
            "request_send_receipt_sha256": send_receipt_sha_by_role[role_id],
            "response_channel": "fixture-secure-governance-channel",
            "response_channel_message_id": (
                f"fixture-g0-response-message-{role_id}"
            ),
            "received_at": f"2026-06-01T12:01:{sequence + 2:02d}Z",
            "authority_effect": "NONE",
            "signature_algorithm": "Ed25519",
            "signing_key_id": approval_key_by_role[role_id]["key_id"],
        }
        approval_value = sign_closed_record(
            approval_payload,
            private_key=approval_key_by_role[role_id]["private_key"],
            signature_domain=f"YLX-G0-EXTERNAL-APPROVAL-V1/{role_id}",
        )
        filename = f"g0-external-approval-{role_id}.json"
        locator = f"{valid_prefix}{filename}"
        digest = corpus.add(
            f"VALID-G0-EXTERNAL-APPROVAL-{role_id.upper()}-01",
            filename,
            "g0-external-approval-v1.schema.json",
            approval_value,
        )
        approval_by_role[role_id] = approval_value
        approval_sha_by_role[role_id] = digest
        approval_locator_by_role[role_id] = locator

        _, import_permission_sha, import_permission_locator = add_operation_grant(
            filename=f"g0-operation-authority-approval-import-{role_id}.json",
            grant_id=f"fixture-g0-approval-import-permission-{role_id}",
            grant_kind="REPOSITORY_PERMISSION",
            grant={
                "repository_locator": repository_locator,
                "actor_id": "fixture-g0-approval-importer",
                "operation": "IMPORT_EXACT",
                "target_scope": locator,
                "payload_sha256": digest,
                "terminal_sink_authority_ref": repository_sink_locator,
                "terminal_sink_authority_sha256": repository_sink_sha,
            },
            issued_at=f"2026-06-01T12:01:{sequence + 3:02d}Z",
        )
        add_repository_receipt(
            filename=f"g0-external-approval-import-receipt-{role_id}.json",
            receipt_id=f"fixture-g0-external-approval-import-receipt-{role_id}",
            permission_locator=import_permission_locator,
            permission_sha256=import_permission_sha,
            actor_id="fixture-g0-approval-importer",
            operation="IMPORT_EXACT",
            target_scope=locator,
            output_ref=locator,
            output_sha256=digest,
            result="IMPORTED_EXACT",
            started_at=f"2026-06-01T12:01:{sequence + 4:02d}Z",
            completed_at=f"2026-06-01T12:01:{sequence + 5:02d}Z",
        )

    effective_at = "2026-06-01T12:02:00Z"
    ratification = {
        "schema": "ylx.g0-policy-ratification.v1",
        "event_id": "fixture-g0-policy-ratification",
        "revision": 1,
        "predecessor_event_sha256": None,
        "subject_ref": subject_locator,
        "subject_sha256": ratification_subject_sha,
        "external_authority_ref": authority_locator,
        "external_authority_sha256": external_authority_sha,
        "approval_sha256_by_required_role": approval_sha_by_role,
        "quorum_policy_ref": quorum_locator,
        "quorum_policy_sha256": quorum_policy_sha,
        "canonical_governance_subject": canonical_governance_subject,
        "proposal_extension_reconciliation_decision": (
            proposal_extension_reconciliation_decision
        ),
        "effective_at": effective_at,
    }
    ratification_filename = "g0-policy-ratification.json"
    ratification_sha = corpus.add(
        "VALID-G0-POLICY-RATIFICATION-EVENT-01",
        ratification_filename,
        "g0-policy-ratification-v1.schema.json",
        ratification,
    )
    ratification_ref = artifact_ref(
        ratification["event_id"],
        ratification["schema"],
        ratification_sha,
        event_locator,
        ratification["revision"],
    )

    publisher_id = "fixture-g0-event-publisher"
    event_target_scope = event_locator
    _, publisher_authority_sha, publisher_authority_locator = (
        add_operation_grant(
            filename="g0-operation-authority-event-publisher.json",
            grant_id="fixture-g0-event-publisher-authority",
            grant_kind="G0_EVENT_PUBLISHER",
            grant={
                "publisher_id": publisher_id,
                "repository_locator": repository_locator,
                "event_schema": ratification["schema"],
                "event_id": ratification["event_id"],
                "event_revision": ratification["revision"],
                "event_sha256": ratification_sha,
                "subject_sha256": ratification_subject_sha,
                "operation": "CREATE_IF_ABSENT",
                "target_scope": event_target_scope,
                "terminal_sink_authority_ref": publication_sink_locator,
                "terminal_sink_authority_sha256": publication_sink_sha,
            },
            issued_at="2026-06-01T12:02:01Z",
        )
    )
    _, event_permission_sha, event_permission_locator = add_operation_grant(
        filename="g0-operation-authority-event-repository-permission.json",
        grant_id="fixture-g0-event-repository-permission",
        grant_kind="REPOSITORY_PERMISSION",
        grant={
            "repository_locator": repository_locator,
            "actor_id": publisher_id,
            "operation": "CREATE_IF_ABSENT",
            "target_scope": event_target_scope,
            "payload_sha256": ratification_sha,
            "terminal_sink_authority_ref": publication_sink_locator,
            "terminal_sink_authority_sha256": publication_sink_sha,
        },
        issued_at="2026-06-01T12:02:02Z",
    )
    publication_payload = {
        "schema": "ylx.g0-policy-ratification-publication-receipt.v1",
        "receipt_id": "fixture-g0-policy-ratification-publication-receipt",
        "event_id": ratification["event_id"],
        "event_revision": ratification["revision"],
        "subject_sha256": ratification_subject_sha,
        "publisher_id": publisher_id,
        "publisher_authority_ref": publisher_authority_locator,
        "publisher_authority_sha256": publisher_authority_sha,
        "repository_permission_ref": event_permission_locator,
        "repository_permission_sha256": event_permission_sha,
        "repository_locator": repository_locator,
        "operation": "CREATE_IF_ABSENT",
        "target_scope": event_target_scope,
        "event_ref": event_locator,
        "event_sha256": ratification_sha,
        "byte_length": len(canonical_bytes(ratification)),
        "published_at": "2026-06-01T12:02:03Z",
        "create_result": "CREATED_EXACT",
        "fsync_result": "FILE_AND_PARENT_DIRECTORY_DURABLE",
        "terminal_sink_id": "fixture-g0-terminal-audit-sink",
        "receipt_issuer_id": "fixture-g0-terminal-receipt-issuer",
        "signature_algorithm": "Ed25519",
        "signing_key_id": receipt_issuer_key["key_id"],
    }
    publication_receipt = sign_closed_record(
        publication_payload,
        private_key=receipt_issuer_key["private_key"],
        signature_domain=receipt_domain,
    )
    publication_receipt_sha = corpus.add(
        "VALID-G0-POLICY-RATIFICATION-PUBLICATION-RECEIPT-01",
        "g0-policy-ratification-publication-receipt.json",
        "g0-policy-ratification-publication-receipt-v1.schema.json",
        publication_receipt,
    )
    publication_receipt_ref = artifact_ref(
        publication_receipt["receipt_id"],
        publication_receipt["schema"],
        publication_receipt_sha,
        publication_locator,
        None,
    )

    reader_id = "fixture-g0-event-reader"
    _, readback_authority_sha, readback_authority_locator = add_operation_grant(
        filename="g0-operation-authority-event-readback.json",
        grant_id="fixture-g0-event-readback-authority",
        grant_kind="G0_EVENT_READBACK",
        grant={
            "reader_id": reader_id,
            "repository_locator": repository_locator,
            "event_schema": ratification["schema"],
            "event_id": ratification["event_id"],
            "event_revision": ratification["revision"],
            "event_sha256": ratification_sha,
            "operation": "EXACT_READBACK",
            "target_scope": event_target_scope,
            "terminal_sink_authority_ref": readback_sink_locator,
            "terminal_sink_authority_sha256": readback_sink_sha,
        },
        issued_at="2026-06-01T12:02:04Z",
    )
    readback_payload = {
        "schema": "ylx.g0-policy-ratification-readback-receipt.v1",
        "receipt_id": "fixture-g0-policy-ratification-readback-receipt",
        "publication_receipt_ref": publication_locator,
        "publication_receipt_sha256": publication_receipt_sha,
        "reader_id": reader_id,
        "readback_authority_ref": readback_authority_locator,
        "readback_authority_sha256": readback_authority_sha,
        "repository_locator": repository_locator,
        "target_scope": event_target_scope,
        "event_ref": event_locator,
        "event_sha256": ratification_sha,
        "observed_event_sha256": ratification_sha,
        "byte_length": len(canonical_bytes(ratification)),
        "read_back_at": "2026-06-01T12:02:05Z",
        "result": "MATCH",
        "terminal_sink_id": "fixture-g0-terminal-audit-sink",
        "receipt_issuer_id": "fixture-g0-terminal-receipt-issuer",
        "signature_algorithm": "Ed25519",
        "signing_key_id": receipt_issuer_key["key_id"],
    }
    readback_receipt = sign_closed_record(
        readback_payload,
        private_key=receipt_issuer_key["private_key"],
        signature_domain=receipt_domain,
    )
    readback_receipt_sha = corpus.add(
        "VALID-G0-POLICY-RATIFICATION-READBACK-RECEIPT-01",
        "g0-policy-ratification-readback-receipt.json",
        "g0-policy-ratification-readback-receipt-v1.schema.json",
        readback_receipt,
    )
    readback_receipt_ref = artifact_ref(
        readback_receipt["receipt_id"],
        readback_receipt["schema"],
        readback_receipt_sha,
        readback_locator,
        None,
    )

    corpus.relationships["g0_policy_ratification_chain"] = {
        "synthetic_test_only_contract_fixture": True,
        "subject_path": subject_locator,
        "subject_sha256": ratification_subject_sha,
        "external_authority_path": authority_locator,
        "external_authority_sha256": external_authority_sha,
        "quorum_policy_path": quorum_locator,
        "quorum_policy_sha256": quorum_policy_sha,
        "approval_path_by_required_role": approval_locator_by_role,
        "approval_sha256_by_required_role": approval_sha_by_role,
        "event_path": event_locator,
        "event_sha256": ratification_sha,
        "publication_receipt_path": publication_locator,
        "publication_receipt_sha256": publication_receipt_sha,
        "readback_receipt_path": readback_locator,
        "readback_receipt_sha256": readback_receipt_sha,
    }
    return {
        "subject": ratification_subject,
        "subject_sha": ratification_subject_sha,
        "ratification": ratification,
        "ratification_sha": ratification_sha,
        "ratification_ref": ratification_ref,
        "external_authority": external_authority,
        "external_authority_sha": external_authority_sha,
        "external_authority_ref": external_authority_ref,
        "quorum_policy": quorum_policy,
        "quorum_policy_sha": quorum_policy_sha,
        "quorum_policy_ref": quorum_policy_ref,
        "clean_commit": clean_commit,
        "clean_commit_ref": clean_commit_ref,
        "clean_commit_operation_receipt": clean_commit_operation_receipt,
        "locator_readback": locator_readback,
        "approval_by_role": approval_by_role,
        "approval_sha_by_role": approval_sha_by_role,
        "send_receipt_by_role": send_receipt_by_role,
        "publication_receipt": publication_receipt,
        "publication_receipt_ref": publication_receipt_ref,
        "readback_receipt": readback_receipt,
        "readback_receipt_ref": readback_receipt_ref,
    }


def build_identity_and_mapping_fixtures(
    corpus: Corpus,
    owner: dict[str, Any],
    owner_sha: str,
    history_state: dict[str, Any],
    registry: dict[str, Any],
) -> dict[str, Any]:
    """Build pseudonymous identity roots and the three-party mapping ratification."""

    identity_refs_by_person: dict[str, dict[str, Any]] = {}
    for role in [*ROLES, "independent-reviewer"]:
        person_id = (
            "fixture-independent-reviewer"
            if role == "independent-reviewer"
            else f"fixture-{role}-person"
        )
        source_filename = f"identity-source-{role}.json"
        source_sha = corpus.add_support(
            source_filename,
            {
                "assertion_id": f"fixture-{role}-identity-assertion",
                "person_id": person_id,
                "notice": NOTICE,
            },
            f"Synthetic external identity assertion for {role}.",
        )
        identity = {
            "schema": "ylx.natural-person-identity-authority.v1",
            "authority_record_id": f"fixture-natural-person-{role}-r1",
            "identity_authority_id": "fixture-organizational-identity-authority",
            "person_id": person_id,
            "revision": 1,
            "predecessor_identity_authority_ref": None,
            "identity_claim_sha256": source_sha,
            "source_authority_refs": [
                {
                    "ref_id": f"fixture-{role}-identity-assertion",
                    "authority_kind": "external-organizational-authority",
                    "locator": (
                        "contracts/fixtures/governance-models/support/"
                        f"{source_filename}"
                    ),
                    "sha256": source_sha,
                }
            ],
            "accountable_natural_person": True,
            "subject_status": "ACTIVE",
            "effective_from": VALID_FROM,
            "not_after": NOT_AFTER,
            "revoked_at": None,
            "revocation_reason": None,
            "verification_method": "AUTHORITATIVE_DIRECTORY_ASSERTION",
            "verified_at": STAMP,
            "privacy_profile": "PSEUDONYMOUS_STABLE_ID_NO_RAW_PII",
            "artifact_metadata": metadata(),
        }
        filename = f"natural-person-identity-{role}.json"
        digest = corpus.add(
            f"VALID-NATURAL-PERSON-IDENTITY-{role.upper()}-01",
            filename,
            "natural-person-identity-authority-v1.schema.json",
            identity,
        )
        identity_refs_by_person[person_id] = artifact_ref(
            identity["authority_record_id"],
            identity["schema"],
            digest,
            f"valid/{filename}",
            1,
        )

    owner_ref = artifact_ref(
        owner["artifact_id"],
        owner["schema"],
        owner_sha,
        "valid/owner-assignment.json",
        owner["revision"],
    )
    policy_filename = "system-feature-mapping-policy.json"
    policy_sha = corpus.add_support(
        policy_filename,
        {
            "policy_id": "policy.system_feature_mapping",
            "policy_kind": "contract-package",
            "notice": NOTICE,
        },
        "Synthetic mapping approval-policy bytes.",
    )
    registry_raw = (REPO_ROOT / "docs" / "acceptance-requirements.yaml").read_bytes()
    acceptance_raw = (REPO_ROOT / "docs" / "ACCEPTANCE.md").read_bytes()
    mapping_raw = (REPO_ROOT / "docs" / "system-requirement-mapping.yaml").read_bytes()
    mapping_document = yaml.safe_load(mapping_raw)
    if not isinstance(mapping_document, dict):
        raise TypeError("system requirement mapping must be an object")
    source_document_raw = (
        REPO_ROOT / "docs" / "RP-YLX-0.5-REQUIREMENTS.md"
    ).read_bytes()
    requirement_ids = [row["id"] for row in registry["requirements"]]
    mapping_authority = registry["policy"]["system_feature_mapping"]
    mapping_source = mapping_document.get("source")
    authority_source = mapping_authority.get("source")
    revision_control = mapping_authority.get("revision_control")
    mapping_revision = mapping_document.get("mapping_revision")
    mapping_artifact_sha = sha(mapping_raw)
    mapping_semantic_sha = system_mapping_semantic_sha256(mapping_document)
    source_document_sha = sha(source_document_raw)
    source_feature_ids = re.findall(
        r"^\| ([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d{2}) \|",
        source_document_raw.decode("utf-8"),
        re.MULTILINE,
    )
    source_feature_set_sha = ascii_set_sha256(source_feature_ids)

    selected_proposal = history_state["selected_acceptance_proposal"]["value"]
    selected_approval = selected_proposal.get("approval")
    if not (
        selected_proposal.get("proposal_status") == "DRAFT"
        and selected_proposal.get("approval_status") == "NOT_APPROVED"
        and isinstance(selected_approval, dict)
        and selected_approval.get("canonical_locator") is None
        and selected_approval.get("canonical_commit") is None
    ):
        raise AssertionError(
            "synthetic mapping root may only mirror an unpublished live draft"
        )
    if not (
        mapping_revision == mapping_authority.get("current_mapping_revision") == 1
        and isinstance(revision_control, dict)
        and revision_control.get("predecessor_mapping_revision") is None
        and revision_control.get("predecessor_artifact_sha256") is None
        and revision_control.get("predecessor_semantic_sha256") is None
    ):
        raise AssertionError(
            "synthetic mapping fixture models only the revision-1 null-predecessor root"
        )
    if not (
        isinstance(mapping_source, dict)
        and isinstance(authority_source, dict)
        and mapping_source.get("document")
        == authority_source.get("document")
        == "docs/RP-YLX-0.5-REQUIREMENTS.md"
        and mapping_source.get("document_sha256")
        == authority_source.get("document_sha256")
        == source_document_sha
        and mapping_source.get("feature_set_sha256")
        == authority_source.get("feature_set_sha256")
        == source_feature_set_sha
        and len(source_feature_ids)
        == len(set(source_feature_ids))
        == mapping_document.get("expected_count")
        == authority_source.get("expected_feature_count")
    ):
        raise AssertionError("system mapping source bindings are stale or inconsistent")
    if not (
        mapping_authority.get("reviewed_artifact_sha256") == mapping_artifact_sha
        and mapping_authority.get("reviewed_semantic_sha256")
        == mapping_semantic_sha
    ):
        raise AssertionError("system mapping reviewed digests are stale")

    predecessor_ratification_sha = None
    mapping_subject = {
        "schema": "ylx.system-feature-mapping-approval-subject.v1",
        "mapping_revision": mapping_revision,
        "predecessor_ratification_sha256": predecessor_ratification_sha,
        "candidate_remote_id": "fixture-origin",
        "candidate_commit": "1" * 40,
        "source_document_path": "docs/RP-YLX-0.5-REQUIREMENTS.md",
        "source_document_sha256": source_document_sha,
        "source_feature_set_sha256": source_feature_set_sha,
        "mapping_artifact_path": "docs/system-requirement-mapping.yaml",
        "mapping_artifact_sha256": mapping_artifact_sha,
        "mapping_semantic_sha256": mapping_semantic_sha,
        "registry_revision": registry["scope_revision"],
        "registry_artifact_path": "docs/acceptance-requirements.yaml",
        "registry_artifact_sha256": sha(registry_raw),
        "registry_id_set_sha256": ascii_set_sha256(requirement_ids),
        "acceptance_artifact_path": "docs/ACCEPTANCE.md",
        "acceptance_artifact_sha256": sha(acceptance_raw),
        "owner_assignment_ref": owner_ref,
        "approval_policy_ref": {
            "ref_id": "policy.system_feature_mapping",
            "authority_kind": "contract-package",
            "locator": (
                "contracts/fixtures/governance-models/support/"
                f"{policy_filename}"
            ),
            "sha256": policy_sha,
        },
        "prepared_at": STAMP,
    }
    subject_sha = corpus.add(
        "VALID-SYSTEM-FEATURE-MAPPING-APPROVAL-SUBJECT-01",
        "system-feature-mapping-approval-subject.json",
        "system-feature-mapping-approval-subject-v1.schema.json",
        mapping_subject,
    )

    approval_sha_by_role: dict[str, str] = {}
    for role in ("release-owner", "contract-owner", "qa-evidence-owner"):
        evidence_filename = f"mapping-approval-evidence-{role}.json"
        evidence_sha = corpus.add_support(
            evidence_filename,
            {
                "evidence_id": f"fixture-mapping-approval-evidence-{role}",
                "subject_sha256": subject_sha,
                "notice": NOTICE,
            },
            f"Synthetic mapping approval evidence for {role}.",
        )
        approval_record = {
            "schema": "ylx.system-feature-mapping-approval.v1",
            "approval_id": f"fixture-system-feature-mapping-approval-{role}",
            "candidate_subject_sha256": subject_sha,
            "role_slot": role,
            "actor_id": f"fixture-{role}-actor",
            "natural_person_id": f"fixture-{role}-person",
            "owner_assignment_ref": owner_ref,
            "decision": "APPROVED",
            "approved_at": STAMP,
            "evidence_refs": [
                artifact_ref(
                    f"fixture-mapping-approval-evidence-{role}",
                    "ylx.mapping-approval-evidence.v1",
                    evidence_sha,
                    f"support/{evidence_filename}",
                    1,
                )
            ],
            "artifact_metadata": metadata(),
        }
        approval_sha_by_role[role] = corpus.add(
            f"VALID-SYSTEM-FEATURE-MAPPING-APPROVAL-{role.upper()}-01",
            f"system-feature-mapping-approval-{role}.json",
            "system-feature-mapping-approval-v1.schema.json",
            approval_record,
        )

    ratification = {
        "schema": "ylx.system-feature-mapping-ratification.v1",
        "ratification_id": "fixture-system-feature-mapping-ratification",
        "revision": mapping_revision,
        "predecessor_ratification_sha256": predecessor_ratification_sha,
        "candidate_subject": mapping_subject,
        "candidate_subject_sha256": subject_sha,
        "approval_sha256_by_role_slot": approval_sha_by_role,
        "published_at": STAMP,
        "artifact_metadata": metadata(),
    }
    ratification_sha = corpus.add(
        "VALID-SYSTEM-FEATURE-MAPPING-RATIFICATION-01",
        "system-feature-mapping-ratification.json",
        "system-feature-mapping-ratification-v1.schema.json",
        ratification,
    )
    return {
        "identity_refs_by_person": identity_refs_by_person,
        "owner_ref": owner_ref,
        "mapping_subject": mapping_subject,
        "mapping_subject_sha": subject_sha,
        "mapping_approval_sha_by_role": approval_sha_by_role,
        "mapping_ratification": ratification,
        "mapping_ratification_sha": ratification_sha,
        "acceptance_history_head": history_state["acceptance_head"],
    }


def build_planning_v2_fixtures(
    corpus: Corpus,
    requirement_ids: list[str],
    registry: dict[str, Any],
    owner: dict[str, Any],
    owner_sha: str,
    calendar: dict[str, Any],
    calendar_sha: str,
    g0_policy_state: dict[str, Any],
) -> dict[str, Any]:
    """Build one accepted M0 rolling-wave root with exact 173-row coverage."""

    migration_path = (
        "docs/evidence/M0/governance/M0-GOV-01/migration/"
        "d832c38836b17ffcf4026775618f159d217748d11309a54a3df32954da6e500d--"
        "planning-legacy-migration-anchor.json"
    )
    migration_anchor = repo_json(migration_path)
    migration_fixture_sha = corpus.add(
        "VALID-PLANNING-LEGACY-MIGRATION-ANCHOR-01",
        "planning-legacy-migration-anchor.json",
        "planning-legacy-migration-anchor-v1.schema.json",
        migration_anchor,
    )
    migration_sha = sha((REPO_ROOT / migration_path).read_bytes())
    if migration_sha != "d832c38836b17ffcf4026775618f159d217748d11309a54a3df32954da6e500d":
        raise AssertionError("immutable planning migration observation drift")
    migration_observation_source_ref = {
        "ref_id": "M0-GOV-01-planning-legacy-migration-anchor",
        "authority_kind": "planning-migration-observation",
        "locator": migration_path,
        "sha256": migration_sha,
    }
    policy_ratification_source_ref = {
        "ref_id": g0_policy_state["ratification"]["event_id"],
        "authority_kind": "policy-ratification",
        "locator": g0_policy_state["ratification_ref"]["artifact_path"],
        "sha256": g0_policy_state["ratification_sha"],
    }
    governed_common_source_refs = [
        migration_observation_source_ref,
        policy_ratification_source_ref,
    ]

    planning_owner = copy.deepcopy(owner)
    planning_owner["artifact_id"] = "fixture-v2-planning-owner-assignment"
    planning_owner_sha = corpus.add(
        "VALID-OWNER-ASSIGNMENT-V2-PLANNING-01",
        "owner-assignment-v2-planning.json",
        "owner-assignment-v1.schema.json",
        planning_owner,
    )
    planning_owner_ref = artifact_ref(
        planning_owner["artifact_id"],
        planning_owner["schema"],
        planning_owner_sha,
        "valid/owner-assignment-v2-planning.json",
        1,
    )
    calendar_ref = artifact_ref(
        calendar["artifact_id"],
        calendar["schema"],
        calendar_sha,
        "valid/resource-calendar.json",
        1,
    )

    registry_raw = (REPO_ROOT / "docs" / "acceptance-requirements.yaml").read_bytes()
    registry_id_set_sha = ascii_set_sha256(requirement_ids)
    registry_binding = {
        "registry_revision": registry["policy"]["release_scope"]["registry_revision"],
        "registry_artifact_path": "docs/acceptance-requirements.yaml",
        "registry_artifact_sha256": sha(registry_raw),
        "registry_id_set_sha256": registry_id_set_sha,
        "registry_cardinality": 173,
    }
    detail_horizon = {
        "planning_gate": "M0",
        "executable_through_gate": "M1",
        "next_expansion_gate": "M1",
        "registry_id_set_sha256": registry_id_set_sha,
        "requirement_cardinality": 173,
    }

    authorization_sha = corpus.add_support(
        "planning-v2-execution-authority.json",
        {
            "schema": "ylx.execution-authority.v1",
            "authority_id": "fixture-planning-v2-execution-authority",
            "revision": 1,
            "authorized_action": "publish-planning-record",
            "notice": NOTICE,
        },
        "Synthetic exact execution-authority bytes for the v2 WBS leaf.",
    )
    corpus.add_support(
        "execution-authority-wrong-type-oracle.json",
        {
            "schema": "ylx.wrong-execution-authority.v1",
            "authority_id": "fixture-wrong-type-execution-authority",
            "revision": 1,
            "authorized_action": "observe",
            "notice": NOTICE,
        },
        (
            "Synthetic wrong-payload-type oracle referenced only by an invalid "
            "execution-authority scenario."
        ),
    )
    resource_authority_sha = corpus.add_support(
        "planning-v2-resource-authority.json",
        {
            "authority_id": "fixture-planning-v2-resource-authority",
            "resource_id": "resource-build-platform-owner",
            "notice": NOTICE,
        },
        "Synthetic exact resource-authority bytes for the v2 WBS leaf.",
    )
    node_id = "fixture-v2-all-requirements-node"

    def owner_role_ref(role: str, principal_id: str) -> dict[str, Any]:
        return {
            "role_id": role,
            "principal_id": principal_id,
            "owner_assignment_ref": planning_owner_ref,
        }

    node = {
        "node_id": node_id,
        "parent_node_id": None,
        "milestone_gate": "M0",
        "horizon_class": "WITHIN_DETAIL_HORIZON",
        "node_kind": "EXECUTABLE_LEAF",
        "affected_requirement_ids": requirement_ids,
        "affected_blocker_ids": [],
        "accountable_owner_ref": owner_role_ref(
            "release-owner", "fixture-release-owner-person"
        ),
        "scope": {
            "in_scope": ["Construct and validate the synthetic M0 planning root."],
            "out_of_scope": ["Production mutation."],
        },
        "planning_status": "READY",
        "absolute_start": "2026-06-02T00:00:00Z",
        "absolute_finish": "2026-06-02T08:00:00Z",
        "executor_ref": owner_role_ref(
            "qa-evidence-owner", "fixture-qa-evidence-owner-person"
        ),
        "reviewer_ref": owner_role_ref(
            "release-owner", "fixture-independent-reviewer"
        ),
        "execution_authorization": {
            "authorization_class": "may_prepare",
            "authorization_action": "publish-planning-record",
            "authority_refs": [
                artifact_ref(
                    "fixture-planning-v2-execution-authority",
                    "ylx.execution-authority.v1",
                    authorization_sha,
                    (
                        "contracts/fixtures/governance-models/support/"
                        "planning-v2-execution-authority.json"
                    ),
                    1,
                )
            ],
            "stop_rules": ["Stop before any production or customer-visible mutation."],
        },
        "effort_estimate": {
            "value": 8.0,
            "unit": "hours",
            "basis": "Deterministic synthetic fixture estimate.",
        },
        "fixed_elapsed_estimate": {
            "value": 8.0,
            "unit": "hours",
            "basis": "Deterministic synthetic fixture duration.",
        },
        "predecessor_refs": [],
        "resource_requirements": [
            {
                "resource_requirement_id": "fixture-v2-ci-runner",
                "resource_kind": "CI_RUNNER",
                "resource_id": "resource-build-platform-owner",
                "quantity": 1.0,
                "capacity_unit": "runner",
                "window_ids": ["fixture-ci-window"],
                "authority_ref": {
                    "ref_id": "fixture-planning-v2-resource-authority",
                    "authority_kind": "fixture-oracle",
                    "locator": (
                        "contracts/fixtures/governance-models/support/"
                        "planning-v2-resource-authority.json"
                    ),
                    "sha256": resource_authority_sha,
                },
            }
        ],
        "definition_of_done": [
            "Every one of the 173 registry rows is covered bidirectionally."
        ],
        "evidence_locator": (
            "contracts/fixtures/governance-models/support/"
            "planning-v2-validation-output.json"
        ),
        "blockers": [],
    }
    evidence_node_id_by_gate: dict[str, str] = {}
    evidence_node_id_by_m5_phase: dict[str, str] = {}
    evidence_authority_ref_by_gate: dict[str, dict[str, Any]] = {}
    evidence_nodes: list[dict[str, Any]] = []
    gate_order = ["M0", "M1", "M2", "M3", "M4a", "M4b", "M4c", "M4d", "M4", "M5"]
    requirement_ids_by_gate = {
        gate: [
            row["id"]
            for row in registry["requirements"]
            if row["id"] in requirement_ids and row["closing_gate"] == gate
        ]
        for gate in gate_order
    }
    assert all(requirement_ids_by_gate.values())
    for gate in gate_order:
        authorization_class = (
            "may_prepare" if gate in {"M0", "M1"} else "may_qualify"
        )
        authorization_action = (
            "observe"
            if gate == "M0"
            else "produce-governance-input"
            if gate == "M1"
            else "collect-evidence"
        )
        gate_batches = (
            [
                (
                    phase,
                    [
                        row["id"]
                        for row in registry["requirements"]
                        if row["id"] in requirement_ids_by_gate["M5"]
                        and row.get("execution_phase") == phase
                    ],
                )
                for phase in M5_EXECUTION_PHASES
            ]
            if gate == "M5"
            else [(None, requirement_ids_by_gate[gate])]
        )
        assert all(batch_requirement_ids for _, batch_requirement_ids in gate_batches)
        for execution_phase, batch_requirement_ids in gate_batches:
            gate_slug = gate.lower()
            batch_slug = (
                f"{gate_slug}-{execution_phase.replace('_', '-')}"
                if execution_phase is not None
                else gate_slug
            )
            evidence_node_id = f"fixture-v2-stage-evidence-{batch_slug}"
            authority_filename = f"planning-v2-stage-evidence-authority-{batch_slug}.json"
            authority_id = f"fixture-planning-v2-stage-evidence-authority-{batch_slug}"
            authority_digest = corpus.add_support(
                authority_filename,
                {
                    "schema": "ylx.execution-authority.v1",
                    "authority_id": authority_id,
                    "revision": 1,
                    "authorization_class": authorization_class,
                    "authorized_action": authorization_action,
                    "closing_gate": gate,
                    "execution_phase": execution_phase,
                    "notice": NOTICE,
                },
                f"Synthetic exact execution authority for the {batch_slug} evidence batch.",
            )
            authority_ref = artifact_ref(
                authority_id,
                "ylx.execution-authority.v1",
                authority_digest,
                f"contracts/fixtures/governance-models/support/{authority_filename}",
                1,
            )
            authority_refs = [authority_ref]
            if batch_slug == "m0":
                secondary_authority_filename = (
                    "planning-v2-stage-evidence-authority-m0-independent.json"
                )
                secondary_authority_id = (
                    "fixture-planning-v2-stage-evidence-authority-m0-independent"
                )
                secondary_authority_digest = corpus.add_support(
                    secondary_authority_filename,
                    {
                        "schema": "ylx.execution-authority.v1",
                        "authority_id": secondary_authority_id,
                        "revision": 1,
                        "authorization_class": authorization_class,
                        "authorized_action": authorization_action,
                        "closing_gate": gate,
                        "execution_phase": execution_phase,
                        "notice": NOTICE,
                    },
                    (
                        "Synthetic independent execution authority proving that one "
                        "leaf and evaluation preserve multiple declared authorities."
                    ),
                )
                authority_refs.append(
                    artifact_ref(
                        secondary_authority_id,
                        "ylx.execution-authority.v1",
                        secondary_authority_digest,
                        (
                            "contracts/fixtures/governance-models/support/"
                            f"{secondary_authority_filename}"
                        ),
                        1,
                    )
                )
            evidence_node = copy.deepcopy(node)
            evidence_node.update(
                {
                    "node_id": evidence_node_id,
                    "milestone_gate": gate if gate in {"M0", "M1", "M2", "M3", "M4", "M5"} else "M4",
                    "affected_requirement_ids": batch_requirement_ids,
                    "scope": {
                        "in_scope": [
                            f"Produce the exact synthetic {batch_slug} evidence batch."
                        ],
                        "out_of_scope": [
                            "Any undeclared action or customer-visible mutation."
                        ],
                    },
                    "execution_authorization": {
                        "authorization_class": authorization_class,
                        "authorization_action": authorization_action,
                        "authority_refs": authority_refs,
                        "stop_rules": [
                            f"Stop the {batch_slug} evidence action on authority, context, predecessor, or input drift."
                        ],
                    },
                    "definition_of_done": [
                        f"The exact {batch_slug} evidence batch is bound to its one-shot authorization evaluation."
                    ],
                    "evidence_locator": (
                        "contracts/fixtures/governance-models/support/"
                        f"stage-evidence-record-{batch_slug}.json"
                    ),
                }
            )
            if execution_phase is None:
                evidence_node_id_by_gate[gate] = evidence_node_id
                evidence_authority_ref_by_gate[gate] = authority_ref
            else:
                evidence_node_id_by_m5_phase[execution_phase] = evidence_node_id
            evidence_nodes.append(evidence_node)

    m2_bootstrap_node_id_by_action: dict[str, str] = {}
    m2_bootstrap_authority_ref_by_action: dict[str, dict[str, Any]] = {}
    m2_bootstrap_nodes: list[dict[str, Any]] = []
    g0_ratification_ref = copy.deepcopy(g0_policy_state["ratification_ref"])
    for action, authorization_class in (
        ("produce-governance-input", "may_prepare"),
        ("implement-contract", "may_implement"),
        ("implement-product", "may_implement"),
        ("build-target-disabled", "may_implement"),
        ("run-integration-smoke", "may_implement"),
        ("install-target", "may_deploy"),
        ("configure-target", "may_deploy"),
        ("deploy-target-disabled", "may_deploy"),
    ):
        action_slug = action.replace("-", "_")
        bootstrap_node_id = f"fixture-v2-m2-bootstrap-{action}"
        authority_filename = f"planning-v2-m2-bootstrap-authority-{action_slug}.json"
        authority_id = f"fixture-planning-v2-m2-bootstrap-authority-{action}"
        authority_digest = corpus.add_support(
            authority_filename,
            {
                "schema": "ylx.execution-authority.v1",
                "authority_id": authority_id,
                "revision": 1,
                "authorization_class": authorization_class,
                "authorized_action": action,
                "milestone_gate": "M2",
                "source_authority_ref": copy.deepcopy(g0_ratification_ref),
                "notice": NOTICE,
            },
            (
                f"Synthetic typed M2 execution authority for {action}, rooted in "
                "the synthetic G0 policy-ratification chain."
            ),
        )
        authority_ref = artifact_ref(
            authority_id,
            "ylx.execution-authority.v1",
            authority_digest,
            f"contracts/fixtures/governance-models/support/{authority_filename}",
            1,
        )
        bootstrap_node = copy.deepcopy(node)
        bootstrap_node.update(
            {
                "node_id": bootstrap_node_id,
                "milestone_gate": "M2",
                "affected_requirement_ids": ["M2-SCHEMA-01"],
                "executor_ref": owner_role_ref(
                    "contract-owner", "fixture-contract-owner-person"
                ),
                "scope": {
                    "in_scope": [
                        f"Execute the exact synthetic M2 {action} bootstrap action."
                    ],
                    "out_of_scope": [
                        "Qualification, production mutation, or customer-visible publication."
                    ],
                },
                "execution_authorization": {
                    "authorization_class": authorization_class,
                    "authorization_action": action,
                    "authority_refs": [authority_ref],
                    "stop_rules": [
                        f"Stop M2 {action} on M1 scope, policy authority, environment, or input drift."
                    ],
                },
                "definition_of_done": [
                    (
                        "The typed M2 implementation bootstrap context is created from a "
                        "null-context governance evaluation."
                        if action == "produce-governance-input"
                        else "The contract implementation consumes the exact typed M2 bootstrap context."
                    )
                ],
                "evidence_locator": (
                    "contracts/fixtures/governance-models/valid/"
                    f"execution-authorization-m2-{action_slug}-pass.json"
                ),
            }
        )
        m2_bootstrap_node_id_by_action[action] = bootstrap_node_id
        m2_bootstrap_authority_ref_by_action[action] = authority_ref
        m2_bootstrap_nodes.append(bootstrap_node)

    m2_qualification_creation_node_id = (
        "fixture-v2-m2-qualification-produce-governance-input"
    )
    m2_qualification_creation_node = copy.deepcopy(
        next(
            item
            for item in m2_bootstrap_nodes
            if item["node_id"]
            == m2_bootstrap_node_id_by_action["produce-governance-input"]
        )
    )
    m2_qualification_creation_node.update(
        {
            "node_id": m2_qualification_creation_node_id,
            "scope": {
                "in_scope": [
                    (
                        "Create the M2 qualification context from the current exact "
                        "implementation bootstrap and observed target-disabled receipts."
                    )
                ],
                "out_of_scope": [
                    (
                        "Any implementation, deployment mutation, qualification verdict, "
                        "or customer-visible publication."
                    )
                ],
            },
            "definition_of_done": [
                (
                    "The M2_QUALIFICATION context exact-binds the current bootstrap, both "
                    "creation evaluations, implementation receipt, seven deployment "
                    "receipts, and observed deployment set."
                )
            ],
            "evidence_locator": (
                "contracts/fixtures/governance-models/valid/"
                "binding-context-v2-m2.json"
            ),
        }
    )
    m2_bootstrap_nodes.append(m2_qualification_creation_node)

    m1_scope_creation_node_id = "fixture-v2-m1-create-stage-source-scope"
    m1_scope_authority_filename = "planning-v2-m1-scope-execution-authority.json"
    m1_scope_authority_id = "fixture-planning-v2-m1-scope-execution-authority"
    m1_scope_authority_sha = corpus.add_support(
        m1_scope_authority_filename,
        {
            "schema": "ylx.execution-authority.v1",
            "authority_id": m1_scope_authority_id,
            "revision": 1,
            "authorization_class": "may_prepare",
            "authorized_action": "produce-governance-input",
            "milestone_gate": "M1",
            "source_authority_ref": copy.deepcopy(g0_ratification_ref),
            "notice": NOTICE,
        },
        (
            "Synthetic typed M1 scope-creation execution authority rooted in "
            "the synthetic G0 policy-ratification chain."
        ),
    )
    m1_scope_authority_ref = artifact_ref(
        m1_scope_authority_id,
        "ylx.execution-authority.v1",
        m1_scope_authority_sha,
        (
            "contracts/fixtures/governance-models/support/"
            f"{m1_scope_authority_filename}"
        ),
        1,
    )
    m1_scope_creation_node = copy.deepcopy(node)
    m1_scope_creation_node.update(
        {
            "node_id": m1_scope_creation_node_id,
            "milestone_gate": "M1",
            "affected_requirement_ids": ["M1-DEC-01"],
            "executor_ref": owner_role_ref(
                "release-owner", "fixture-release-owner-person"
            ),
            "scope": {
                "in_scope": [
                    "Create the immutable M1 decision source scope from the current exact M0 custody scope."
                ],
                "out_of_scope": [
                    "Any candidate implementation, deployment, or retrospective authority."
                ],
            },
            "execution_authorization": {
                "authorization_class": "may_prepare",
                "authorization_action": "produce-governance-input",
                "authority_refs": [copy.deepcopy(m1_scope_authority_ref)],
                "stop_rules": [
                    "Stop M1 scope creation on M0 scope, decision-head, or G0 authority drift."
                ],
            },
            "definition_of_done": [
                "The M1 scope exact-binds this PASS evaluation and its closed source ref/digest inventory."
            ],
            "evidence_locator": (
                "contracts/fixtures/governance-models/valid/"
                "release-source-scope-m1.json"
            ),
        }
    )

    m1_measurement_training_node_id = (
        "fixture-v2-m1-produce-training-evidence-m0-meas-01"
    )
    m1_measurement_training_authority_filename = (
        "planning-v2-m1-measurement-training-execution-authority.json"
    )
    m1_measurement_training_authority_id = (
        "fixture-planning-v2-m1-measurement-training-execution-authority"
    )
    m1_measurement_training_authority_sha = corpus.add_support(
        m1_measurement_training_authority_filename,
        {
            "schema": "ylx.execution-authority.v1",
            "authority_id": m1_measurement_training_authority_id,
            "revision": 1,
            "authorization_class": "may_prepare",
            "authorized_action": "produce-governance-input",
            "milestone_gate": "M1",
            "measurement_id": "M0-MEAS-01",
            "data_selection_side": "TRAINING",
            "source_authority_ref": copy.deepcopy(g0_ratification_ref),
            "notice": NOTICE,
        },
        (
            "Synthetic typed M1 training-evidence authority for "
            "M0-MEAS-01."
        ),
    )
    m1_measurement_training_authority_ref = artifact_ref(
        m1_measurement_training_authority_id,
        "ylx.execution-authority.v1",
        m1_measurement_training_authority_sha,
        (
            "contracts/fixtures/governance-models/support/"
            f"{m1_measurement_training_authority_filename}"
        ),
        1,
    )
    m1_measurement_training_node = copy.deepcopy(node)
    m1_measurement_training_node.update(
        {
            "node_id": m1_measurement_training_node_id,
            "milestone_gate": "M1",
            "affected_requirement_ids": ["M0-MEAS-01"],
            "accountable_owner_ref": owner_role_ref(
                "capture-owner", "fixture-capture-owner-person"
            ),
            "executor_ref": owner_role_ref(
                "capture-owner", "fixture-capture-owner-person"
            ),
            "reviewer_ref": owner_role_ref(
                "qa-evidence-owner", "fixture-independent-reviewer"
            ),
            "scope": {
                "in_scope": [
                    "Produce the exact M0-MEAS-01 training evidence from the declared training selection."
                ],
                "out_of_scope": [
                    "Holdout consumption, threshold publication, or a terminal verdict."
                ],
            },
            "execution_authorization": {
                "authorization_class": "may_prepare",
                "authorization_action": "produce-governance-input",
                "authority_refs": [
                    copy.deepcopy(m1_measurement_training_authority_ref)
                ],
                "stop_rules": [
                    "Stop training evidence production on selection, partition, source scope, assignment, or input drift."
                ],
            },
            "definition_of_done": [
                "One exact training evidence record and its one-record evidence binding reconcile to the selection-bound PASS E."
            ],
            "evidence_locator": (
                "contracts/fixtures/governance-models/support/"
                "measurement-threshold-training-evidence-m0-meas-01.json"
            ),
        }
    )

    m1_threshold_freeze_node_id = (
        "fixture-v2-m1-freeze-threshold-m0-meas-01"
    )
    m1_threshold_freeze_authority_filename = (
        "planning-v2-m1-threshold-freeze-execution-authority.json"
    )
    m1_threshold_freeze_authority_id = (
        "fixture-planning-v2-m1-threshold-freeze-execution-authority"
    )
    m1_threshold_freeze_authority_sha = corpus.add_support(
        m1_threshold_freeze_authority_filename,
        {
            "schema": "ylx.execution-authority.v1",
            "authority_id": m1_threshold_freeze_authority_id,
            "revision": 1,
            "authorization_class": "may_prepare",
            "authorized_action": "produce-governance-input",
            "milestone_gate": "M1",
            "measurement_id": "M0-MEAS-01",
            "source_authority_ref": copy.deepcopy(g0_ratification_ref),
            "notice": NOTICE,
        },
        (
            "Synthetic typed M1 threshold-freeze execution authority for "
            "M0-MEAS-01, rooted in the synthetic G0 policy-ratification chain."
        ),
    )
    m1_threshold_freeze_authority_ref = artifact_ref(
        m1_threshold_freeze_authority_id,
        "ylx.execution-authority.v1",
        m1_threshold_freeze_authority_sha,
        (
            "contracts/fixtures/governance-models/support/"
            f"{m1_threshold_freeze_authority_filename}"
        ),
        1,
    )
    m1_threshold_freeze_node = copy.deepcopy(node)
    m1_threshold_freeze_node.update(
        {
            "node_id": m1_threshold_freeze_node_id,
            "milestone_gate": "M1",
            "affected_requirement_ids": ["M0-MEAS-01"],
            "accountable_owner_ref": owner_role_ref(
                "capture-owner", "fixture-capture-owner-person"
            ),
            "executor_ref": owner_role_ref(
                "capture-owner", "fixture-capture-owner-person"
            ),
            "reviewer_ref": owner_role_ref(
                "qa-evidence-owner", "fixture-independent-reviewer"
            ),
            "scope": {
                "in_scope": [
                    "Freeze the exact candidate-independent M0-MEAS-01 fitted threshold input."
                ],
                "out_of_scope": [
                    "Holdout evaluation, a requirement verdict, or any release authority."
                ],
            },
            "execution_authorization": {
                "authorization_class": "may_prepare",
                "authorization_action": "produce-governance-input",
                "authority_refs": [
                    copy.deepcopy(m1_threshold_freeze_authority_ref)
                ],
                "stop_rules": [
                    "Stop threshold freeze on M1 source scope, method, partition, training evidence, assignment, or planned-input drift."
                ],
            },
            "definition_of_done": [
                "The exact M0-MEAS-01 threshold input is externally published and read back without claiming a verdict."
            ],
            "evidence_locator": (
                "contracts/fixtures/governance-models/valid/"
                "measurement-threshold-record-m0-meas-01.json"
            ),
        }
    )

    m3_measurement_holdout_node_id = (
        "fixture-v2-m3-produce-holdout-evidence-m0-meas-01"
    )
    m3_measurement_holdout_authority_filename = (
        "planning-v2-m3-measurement-holdout-execution-authority.json"
    )
    m3_measurement_holdout_authority_id = (
        "fixture-planning-v2-m3-measurement-holdout-execution-authority"
    )
    m3_measurement_holdout_authority_sha = corpus.add_support(
        m3_measurement_holdout_authority_filename,
        {
            "schema": "ylx.execution-authority.v1",
            "authority_id": m3_measurement_holdout_authority_id,
            "revision": 1,
            "authorization_class": "may_qualify",
            "authorized_action": "collect-evidence",
            "milestone_gate": "M3",
            "measurement_id": "M0-MEAS-01",
            "data_selection_side": "HOLDOUT",
            "notice": NOTICE,
        },
        "Synthetic typed M3 holdout-evidence authority for M0-MEAS-01.",
    )
    m3_measurement_holdout_authority_ref = artifact_ref(
        m3_measurement_holdout_authority_id,
        "ylx.execution-authority.v1",
        m3_measurement_holdout_authority_sha,
        (
            "contracts/fixtures/governance-models/support/"
            f"{m3_measurement_holdout_authority_filename}"
        ),
        1,
    )
    m3_measurement_holdout_node = copy.deepcopy(node)
    m3_measurement_holdout_node.update(
        {
            "node_id": m3_measurement_holdout_node_id,
            "milestone_gate": "M3",
            "affected_requirement_ids": ["M0-MEAS-01"],
            "accountable_owner_ref": owner_role_ref(
                "capture-owner", "fixture-capture-owner-person"
            ),
            "executor_ref": owner_role_ref(
                "capture-owner", "fixture-capture-owner-person"
            ),
            "reviewer_ref": owner_role_ref(
                "qa-evidence-owner", "fixture-independent-reviewer"
            ),
            "scope": {
                "in_scope": [
                    "Produce the exact M0-MEAS-01 holdout evidence from the declared holdout selection."
                ],
                "out_of_scope": [
                    "Threshold retuning, queue-derived results, or customer-visible mutation."
                ],
            },
            "execution_authorization": {
                "authorization_class": "may_qualify",
                "authorization_action": "collect-evidence",
                "authority_refs": [
                    copy.deepcopy(m3_measurement_holdout_authority_ref)
                ],
                "stop_rules": [
                    "Stop holdout evidence production on selection, partition, threshold, context, assignment, or input drift."
                ],
            },
            "definition_of_done": [
                "One exact holdout evidence record and its one-record evidence binding reconcile to the selection-bound PASS E."
            ],
            "evidence_locator": (
                "contracts/fixtures/governance-models/support/"
                "measurement-holdout-evidence-m0-meas-01.json"
            ),
        }
    )

    transition_specs = (
        ("m3-implement-product", "M3", "may_implement", "implement-product", "M3-INV-COMPLETE-01", "qa-evidence-owner"),
        ("m3-build-target-disabled", "M3", "may_implement", "build-target-disabled", "M3-INV-COMPLETE-01", "build-platform-owner"),
        ("m3-run-integration-smoke", "M3", "may_implement", "run-integration-smoke", "M3-INV-COMPLETE-01", "qa-evidence-owner"),
        ("m3-create-context", "M3", "may_prepare", "produce-governance-input", "M3-INV-COMPLETE-01", "qa-evidence-owner"),
        ("m4-implement-product", "M4", "may_implement", "implement-product", "M4-CANDIDATE-ASSEMBLY-01", "build-platform-owner"),
        ("m4-build-target-disabled", "M4", "may_implement", "build-target-disabled", "M4-CANDIDATE-ASSEMBLY-01", "build-platform-owner"),
        ("m4-assemble-target", "M4", "may_implement", "assemble-m4-target", "M4-CANDIDATE-ASSEMBLY-01", "build-platform-owner"),
        ("m4-run-integration-smoke", "M4", "may_implement", "run-integration-smoke", "M4-CANDIDATE-ASSEMBLY-01", "qa-evidence-owner"),
        ("m4-create-context", "M4", "may_prepare", "produce-governance-input", "M4-CANDIDATE-ASSEMBLY-01", "build-platform-owner"),
        ("m4-install-target", "M4", "may_deploy", "install-target", "M4-CANDIDATE-ASSEMBLY-01", "build-platform-owner"),
        ("m4-configure-target", "M4", "may_deploy", "configure-target", "M4-CANDIDATE-ASSEMBLY-01", "build-platform-owner"),
        ("m4-deploy-target-disabled", "M4", "may_deploy", "deploy-target-disabled", "M4-CANDIDATE-ASSEMBLY-01", "build-platform-owner"),
        ("m5-build-prerelease-rc", "M5", "may_implement", "build-target-disabled", "BUILD-RELEASE-01", "build-platform-owner"),
        ("m5-create-context", "M5", "may_prepare", "produce-governance-input", "BUILD-RELEASE-01", "release-owner"),
        ("m5-publish-prerelease-rc", "M5", "may_deploy", "publish-prerelease-rc", "BUILD-RELEASE-01", "build-platform-owner"),
    )
    transition_node_id_by_key: dict[str, str] = {}
    transition_nodes: list[dict[str, Any]] = []
    for (
        transition_key,
        milestone_gate,
        authorization_class,
        action,
        requirement_id,
        role_id,
    ) in transition_specs:
        transition_node_id = f"fixture-v2-transition-{transition_key}"
        transition_authority_filename = (
            f"planning-v2-transition-authority-{transition_key}.json"
        )
        transition_authority_id = (
            f"fixture-planning-v2-transition-authority-{transition_key}"
        )
        transition_authority_sha = corpus.add_support(
            transition_authority_filename,
            {
                "schema": "ylx.execution-authority.v1",
                "authority_id": transition_authority_id,
                "revision": 1,
                "authorization_class": authorization_class,
                "authorized_action": action,
                "milestone_gate": milestone_gate,
                "notice": NOTICE,
            },
            f"Synthetic exact execution authority for transition {transition_key}.",
        )
        transition_authority_ref = artifact_ref(
            transition_authority_id,
            "ylx.execution-authority.v1",
            transition_authority_sha,
            (
                "contracts/fixtures/governance-models/support/"
                f"{transition_authority_filename}"
            ),
            1,
        )
        transition_node = copy.deepcopy(node)
        transition_node.update(
            {
                "node_id": transition_node_id,
                "milestone_gate": milestone_gate,
                "affected_requirement_ids": [requirement_id],
                "executor_ref": owner_role_ref(
                    role_id, f"fixture-{role_id}-person"
                ),
                "scope": {
                    "in_scope": [
                        f"Execute the exact {transition_key} transition once."
                    ],
                    "out_of_scope": [
                        "Any action, context, environment, or output outside the declared transition."
                    ],
                },
                "execution_authorization": {
                    "authorization_class": authorization_class,
                    "authorization_action": action,
                    "authority_refs": [transition_authority_ref],
                    "stop_rules": [
                        f"Stop {transition_key} on context, root, predecessor, or output drift."
                    ],
                },
                "definition_of_done": [
                    f"The {transition_key} output or context exact-binds its PASS evaluation."
                ],
                "evidence_locator": (
                    "contracts/fixtures/governance-models/support/"
                    f"transition-receipt-{transition_key}.json"
                ),
            }
        )
        transition_node_id_by_key[transition_key] = transition_node_id
        transition_nodes.append(transition_node)

    all_evidence_authority_filename = "planning-v2-all-evidence-authority.json"
    all_evidence_authority_id = "fixture-planning-v2-all-evidence-authority"
    all_evidence_authority_sha = corpus.add_support(
        all_evidence_authority_filename,
        {
            "schema": "ylx.execution-authority.v1",
            "authority_id": all_evidence_authority_id,
            "revision": 1,
            "authorization_class": "may_qualify",
            "authorized_action": "collect-evidence",
            "scope": "all-173-requirements",
            "notice": NOTICE,
        },
        "Synthetic exact execution authority for the 173-row qualification evidence batch.",
    )
    all_evidence_authority_ref = artifact_ref(
        all_evidence_authority_id,
        "ylx.execution-authority.v1",
        all_evidence_authority_sha,
        (
            "contracts/fixtures/governance-models/support/"
            f"{all_evidence_authority_filename}"
        ),
        1,
    )
    all_evidence_node_id = "fixture-v2-all-requirements-evidence"
    all_evidence_node = copy.deepcopy(node)
    all_evidence_node.update(
        {
            "node_id": all_evidence_node_id,
            "milestone_gate": "M4",
            "scope": {
                "in_scope": [
                    "Produce the exact synthetic qualification evidence covering all 173 requirements."
                ],
                "out_of_scope": ["Any customer-visible mutation."],
            },
            "execution_authorization": {
                "authorization_class": "may_qualify",
                "authorization_action": "collect-evidence",
                "authority_refs": [all_evidence_authority_ref],
                "stop_rules": [
                    "Stop the all-requirements evidence action on authority, context, predecessor, or input drift."
                ],
            },
            "definition_of_done": [
                "The exact 173-row evidence batch is bound to its one-shot authorization evaluation."
            ],
            "evidence_locator": (
                "contracts/fixtures/governance-models/support/evidence-all.json"
            ),
        }
    )
    issue_verdict_node_id_by_gate: dict[str, str] = {}
    issue_verdict_nodes: list[dict[str, Any]] = []
    issue_verdict_batches = [
        (
            gate.lower(),
            gate if gate in {"M0", "M1", "M2", "M3", "M4", "M5"} else "M4",
            [requirement_id],
        )
        for gate, requirement_id in ISSUE_REQUIREMENT_BY_GATE.items()
    ]
    for batch_slug, milestone_gate, batch_requirement_ids in issue_verdict_batches:
        issue_node_id = f"fixture-v2-action-issue-verdict-{batch_slug}"
        authority_id = f"fixture-planning-v2-issue-verdict-authority-{batch_slug}"
        authority_filename = f"planning-v2-issue-verdict-authority-{batch_slug}.json"
        authority_digest = corpus.add_support(
            authority_filename,
            {
                "schema": "ylx.execution-authority.v1",
                "authority_id": authority_id,
                "revision": 1,
                "authorization_class": "may_qualify",
                "authorized_action": "issue-verdict",
                "milestone_gate": milestone_gate,
                "requirement_ids": batch_requirement_ids,
                "notice": NOTICE,
            },
            f"Synthetic exact execution authority for the {batch_slug} issue verdict batch.",
        )
        authority_ref = artifact_ref(
            authority_id,
            "ylx.execution-authority.v1",
            authority_digest,
            f"contracts/fixtures/governance-models/support/{authority_filename}",
            1,
        )
        issue_node = copy.deepcopy(node)
        issue_node.update(
            {
                "node_id": issue_node_id,
                "milestone_gate": milestone_gate,
                "affected_requirement_ids": batch_requirement_ids,
                "executor_ref": owner_role_ref(
                    "release-owner", "fixture-release-owner-person"
                ),
                "scope": {
                    "in_scope": [
                        f"Evaluate the exact current issue selectors for the {batch_slug} batch."
                    ],
                    "out_of_scope": [
                        "Any issue-source mutation or inferred aggregate verdict."
                    ],
                },
                "execution_authorization": {
                    "authorization_class": "may_qualify",
                    "authorization_action": "issue-verdict",
                    "authority_refs": [authority_ref],
                    "stop_rules": [
                        f"Stop the {batch_slug} issue verdict batch on head, selector, context, authority, or input drift."
                    ],
                },
                "definition_of_done": [
                    "Every immutable batch verdict binds the same complete selected issue head."
                ],
                "evidence_locator": (
                    "contracts/fixtures/governance-models/valid/"
                    f"issue-register-gate-verdict-{batch_slug}.json"
                ),
            }
        )
        for gate, requirement_id in ISSUE_REQUIREMENT_BY_GATE.items():
            if requirement_id in batch_requirement_ids:
                issue_verdict_node_id_by_gate[gate] = issue_node_id
        issue_verdict_nodes.append(issue_node)

    release_action_specs = {
        "rehearse-distribution-control": "may_qualify",
        "assemble-release-projection": "may_finalize_release",
        "collect-domain-attestations": "may_finalize_release",
        "publish-pre-release-closure": "may_finalize_release",
        "collect-release-quorum": "may_finalize_release",
        "acquire-publication-fence": "may_finalize_release",
        "promote-exact-rc": "may_finalize_release",
        "publish-promotion-receipt": "may_finalize_release",
        "publish-final-manifest": "may_finalize_release",
        "cas-terminal-reference": "may_finalize_release",
        "publish-aborted-termination": "may_finalize_release",
        "publish-initial-active": "may_control_distribution",
        "publish-withdrawn": "may_control_distribution",
        "publish-redirected": "may_control_distribution",
        "publish-reactivated-active": "may_control_distribution",
    }
    action_node_id_by_action: dict[str, str] = {}
    action_authority_ref_by_action: dict[str, dict[str, Any]] = {}
    action_nodes: list[dict[str, Any]] = []
    for action, authorization_class in release_action_specs.items():
        action_slug = action.replace("-", "_")
        action_node_id = f"fixture-v2-action-{action}"
        authority_id = f"fixture-planning-v2-action-authority-{action}"
        authority_filename = f"planning-v2-action-authority-{action_slug}.json"
        authority_digest = corpus.add_support(
            authority_filename,
            {
                "schema": "ylx.execution-authority.v1",
                "authority_id": authority_id,
                "revision": 1,
                "authorization_class": authorization_class,
                "authorized_action": action,
                "notice": NOTICE,
            },
            f"Synthetic exact execution authority for {action}.",
        )
        authority_ref = artifact_ref(
            authority_id,
            "ylx.execution-authority.v1",
            authority_digest,
            f"contracts/fixtures/governance-models/support/{authority_filename}",
            1,
        )
        action_node = copy.deepcopy(node)
        action_node.update(
            {
                "node_id": action_node_id,
                "milestone_gate": "M5",
                "affected_requirement_ids": ["M5-MATRIX-COMPLETE-01"],
                "executor_ref": (
                    owner_role_ref(
                        "contract-owner", "fixture-contract-owner-person"
                    )
                    if action == "assemble-release-projection"
                    else owner_role_ref(
                        "build-platform-owner", "fixture-build-platform-owner-person"
                    )
                    if action == "promote-exact-rc"
                    else copy.deepcopy(node["executor_ref"])
                ),
                "scope": {
                    "in_scope": [f"Execute the exact synthetic {action} action once."],
                    "out_of_scope": [
                        "Any undeclared action, input substitution, or production mutation."
                    ],
                },
                "execution_authorization": {
                    "authorization_class": authorization_class,
                    "authorization_action": action,
                    "authority_refs": [authority_ref],
                    "stop_rules": [
                        f"Stop {action} on authority, context, predecessor, or planned-input drift."
                    ],
                },
                "definition_of_done": [
                    f"The {action} artifact or receipt is bound to its one-shot authorization evaluation."
                ],
                "evidence_locator": (
                    "contracts/fixtures/governance-models/support/"
                    f"action-result-{action_slug}.json"
                ),
            }
        )
        action_node_id_by_action[action] = action_node_id
        action_authority_ref_by_action[action] = authority_ref
        action_nodes.append(action_node)

    all_nodes = [
        node,
        *evidence_nodes,
        m1_scope_creation_node,
        m1_measurement_training_node,
        m1_threshold_freeze_node,
        m3_measurement_holdout_node,
        *m2_bootstrap_nodes,
        *transition_nodes,
        all_evidence_node,
        *issue_verdict_nodes,
        *action_nodes,
    ]
    def bound_nodes_beyond_m1(source_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        decision_node_id = issue_verdict_node_id_by_gate["M1"]
        decomposition_deadline_by_gate = {
            "M2": "2026-06-08T00:00:00Z",
            "M3": "2026-06-15T00:00:00Z",
            "M4": "2026-06-22T00:00:00Z",
            "M5": "2026-06-29T00:00:00Z",
        }
        result: list[dict[str, Any]] = []
        for candidate in source_nodes:
            gate = candidate["milestone_gate"]
            if gate in {"M0", "M1"}:
                result.append(copy.deepcopy(candidate))
                continue
            result.append(
                {
                    "node_id": candidate["node_id"],
                    "parent_node_id": candidate["parent_node_id"],
                    "milestone_gate": gate,
                    "horizon_class": "BEYOND_DETAIL_HORIZON",
                    "node_kind": "BOUNDED_SUMMARY",
                    "affected_requirement_ids": copy.deepcopy(
                        candidate["affected_requirement_ids"]
                    ),
                    "affected_blocker_ids": copy.deepcopy(
                        candidate["affected_blocker_ids"]
                    ),
                    "accountable_owner_ref": copy.deepcopy(
                        candidate["accountable_owner_ref"]
                    ),
                    "scope": copy.deepcopy(candidate["scope"]),
                    "planning_status": "CONDITIONAL",
                    "absolute_start": None,
                    "absolute_finish": None,
                    "blockers": [],
                    "estimate_range": {
                        "effort": {
                            "lower": 4.0,
                            "upper": 12.0,
                            "unit": "hours",
                            "basis": "Bounded synthetic rolling-wave effort range.",
                            "confidence": 0.8,
                        },
                        "fixed_elapsed": {
                            "lower": 4.0,
                            "upper": 12.0,
                            "unit": "hours",
                            "basis": "Bounded synthetic rolling-wave elapsed range.",
                            "confidence": 0.8,
                        },
                    },
                    "decision_predecessor_refs": [
                        {
                            "decision_node_id": decision_node_id,
                            "decision_status": "PENDING",
                            "relation_type": "DECISION_BEFORE_DECOMPOSITION",
                            "decision_artifact_ref": None,
                        }
                    ],
                    "decomposition_gate": gate,
                    "decomposition_deadline": decomposition_deadline_by_gate[gate],
                    "stop_rules": [
                        "Do not execute before expansion by an accepted successor plan."
                    ],
                }
            )
        return result

    fixture_detail_horizon = copy.deepcopy(detail_horizon)
    wbs_v2 = {
        "schema": "ylx.delivery-wbs.v2",
        "artifact_id": "fixture-delivery-wbs-v2",
        "revision": 1,
        "predecessor_sha256": None,
        "generated_at": STAMP,
        "source_refs": copy.deepcopy(governed_common_source_refs),
        "approvals": [approval("release-owner")],
        "artifact_metadata": metadata(),
        "overall_status": "ACCEPTED",
        "planning_gate": "M0",
        "detail_horizon": fixture_detail_horizon,
        "registry_binding": registry_binding,
        "requirement_ids": requirement_ids,
        "requirement_coverage": {
            requirement_id: [
                item["node_id"]
                for item in all_nodes
                if requirement_id in item["affected_requirement_ids"]
            ]
            for requirement_id in requirement_ids
        },
        "active_blocker_ids": [],
        "active_blocker_coverage": {},
        "nodes": bound_nodes_beyond_m1(all_nodes),
        "blockers": [],
    }
    wbs_v2_sha = corpus.add(
        "VALID-DELIVERY-WBS-V2-01",
        "delivery-wbs-v2.json",
        "delivery-wbs-v2.schema.json",
        wbs_v2,
    )
    wbs_v2_ref = artifact_ref(
        wbs_v2["artifact_id"],
        wbs_v2["schema"],
        wbs_v2_sha,
        "valid/delivery-wbs-v2.json",
        1,
    )

    calculator_path = REPO_ROOT / "scripts" / "delivery_planning_calculator.py"
    calculator_sha = sha(calculator_path.read_bytes())
    forecast_v2 = copy.deepcopy(corpus.values["valid/forecast-snapshot.json"])
    forecast_v2.update(
        {
            "artifact_id": "fixture-v2-planning-forecast",
            "owner_assignment_sha256": planning_owner_sha,
            "resource_calendar_sha256": calendar_sha,
            "delivery_wbs_sha256": wbs_v2_sha,
            "scheduling_rules_version": DELIVERY_PLANNING_RULES_VERSION,
            "calculator": {
                "calculator_id": DELIVERY_PLANNING_CALCULATOR_ID,
                "version": DELIVERY_PLANNING_CALCULATOR_VERSION,
                "artifact_path": "scripts/delivery_planning_calculator.py",
                "artifact_sha256": calculator_sha,
            },
        }
    )
    forecast_v2.update(calculate_delivery_planning(wbs_v2, calendar))
    forecast_v2_sha = corpus.add(
        "VALID-FORECAST-SNAPSHOT-V2-PLANNING-01",
        "forecast-snapshot-v2-planning.json",
        "forecast-snapshot-v1.schema.json",
        forecast_v2,
    )
    forecast_v2_ref = artifact_ref(
        forecast_v2["artifact_id"],
        forecast_v2["schema"],
        forecast_v2_sha,
        "valid/forecast-snapshot-v2-planning.json",
        1,
    )

    artifacts = {
        "owner_assignment": planning_owner_ref,
        "resource_calendar": calendar_ref,
        "delivery_wbs": wbs_v2_ref,
        "forecast_snapshot": forecast_v2_ref,
    }
    bundle_v2 = {
        "schema": "ylx.delivery-planning-bundle.v2",
        "artifact_id": "fixture-delivery-planning-bundle-v2",
        "revision": 1,
        "predecessor_sha256": None,
        "generated_at": STAMP,
        "source_refs": copy.deepcopy(governed_common_source_refs),
        "artifact_metadata": metadata(),
        "planning_approval_subject_sha256": "0" * 64,
        "planning_bundle_approval_by_role": {},
        "planning_gate": "M0",
        "detail_horizon": fixture_detail_horizon,
        "registry_binding": registry_binding,
        "artifacts": artifacts,
        "bundle_kind": "ROLLING_WAVE",
        "final_actual_variance_reconciliation": None,
        "overall_status": "ACCEPTED",
    }
    subject_fields = (
        "schema",
        "artifact_id",
        "revision",
        "predecessor_sha256",
        "source_refs",
        "artifact_metadata",
        "planning_gate",
        "detail_horizon",
        "registry_binding",
        "artifacts",
        "bundle_kind",
        "final_actual_variance_reconciliation",
    )
    subject_sha = sha(
        canonical_bytes({field: bundle_v2[field] for field in subject_fields})
    )
    bundle_v2["planning_approval_subject_sha256"] = subject_sha
    artifact_sha_by_kind = {
        "owner_assignment": planning_owner_sha,
        "resource_calendar": calendar_sha,
        "delivery_wbs": wbs_v2_sha,
        "forecast_snapshot": forecast_v2_sha,
    }
    approvals: dict[str, Any] = {}
    for role in ("release-owner", "build-platform-owner", "qa-evidence-owner"):
        evidence_filename = f"planning-v2-approval-evidence-{role}.json"
        evidence_sha = corpus.add_support(
            evidence_filename,
            {
                "evidence_id": f"fixture-planning-v2-approval-evidence-{role}",
                "subject_sha256": subject_sha,
                "notice": NOTICE,
            },
            f"Synthetic accepted v2 planning evidence for {role}.",
        )
        approvals[role] = {
            "role_id": role,
            "principal_id": f"fixture-{role}-person",
            "natural_person_id": f"fixture-{role}-person",
            "decision": "APPROVED",
            "approved_at": "2026-06-01T11:59:59Z",
            "assignment_ref": planning_owner_ref,
            "planning_approval_subject_sha256": subject_sha,
            "bundle_revision": 1,
            "predecessor_sha256": None,
            "artifact_sha256_by_kind": artifact_sha_by_kind,
            "owner_assignment_revision": 1,
            "approval_evidence_ref": artifact_ref(
                f"fixture-planning-v2-approval-evidence-{role}",
                "ylx.planning-approval-evidence.v1",
                evidence_sha,
                (
                    "contracts/fixtures/governance-models/support/"
                    f"{evidence_filename}"
                ),
                1,
            ),
        }
    bundle_v2["planning_bundle_approval_by_role"] = approvals
    bundle_v2_sha = corpus.add(
        "VALID-DELIVERY-PLANNING-BUNDLE-V2-01",
        "delivery-planning-bundle-v2.json",
        "delivery-planning-bundle-v2.schema.json",
        bundle_v2,
    )

    execution_wbs = copy.deepcopy(wbs_v2)
    execution_wbs.update(
        {
            "artifact_id": "fixture-execution-template-delivery-wbs-v2",
            "generated_at": "2026-06-01T12:00:30Z",
            "planning_gate": "M4",
            "detail_horizon": {
                **copy.deepcopy(detail_horizon),
                "planning_gate": "M4",
                "executable_through_gate": "M5",
                "next_expansion_gate": "M5",
            },
            "nodes": copy.deepcopy(all_nodes),
        }
    )
    execution_wbs_sha = corpus.add(
        "VALID-EXECUTION-TEMPLATE-DELIVERY-WBS-V2-01",
        "execution-template-delivery-wbs-v2.json",
        "delivery-wbs-v2.schema.json",
        execution_wbs,
    )
    execution_wbs_ref = artifact_ref(
        execution_wbs["artifact_id"],
        execution_wbs["schema"],
        execution_wbs_sha,
        "valid/execution-template-delivery-wbs-v2.json",
        1,
    )
    execution_forecast = copy.deepcopy(forecast_v2)
    execution_forecast.update(
        {
            "artifact_id": "fixture-execution-template-forecast-v2",
            "generated_at": "2026-06-01T12:00:40Z",
            "delivery_wbs_sha256": execution_wbs_sha,
        }
    )
    execution_forecast.update(
        calculate_delivery_planning(execution_wbs, calendar)
    )
    execution_forecast_sha = corpus.add(
        "VALID-EXECUTION-TEMPLATE-FORECAST-V2-01",
        "execution-template-forecast-v2.json",
        "forecast-snapshot-v1.schema.json",
        execution_forecast,
    )
    execution_forecast_ref = artifact_ref(
        execution_forecast["artifact_id"],
        execution_forecast["schema"],
        execution_forecast_sha,
        "valid/execution-template-forecast-v2.json",
        1,
    )
    execution_bundle = copy.deepcopy(bundle_v2)
    execution_bundle.update(
        {
            "artifact_id": "fixture-execution-template-planning-bundle-v2",
            "generated_at": "2026-06-01T12:00:50Z",
            "planning_gate": "M4",
            "detail_horizon": copy.deepcopy(execution_wbs["detail_horizon"]),
            "artifacts": {
                "owner_assignment": planning_owner_ref,
                "resource_calendar": calendar_ref,
                "delivery_wbs": execution_wbs_ref,
                "forecast_snapshot": execution_forecast_ref,
            },
        }
    )
    execution_subject_sha = sha(
        canonical_bytes(
            {field: execution_bundle[field] for field in subject_fields}
        )
    )
    execution_bundle["planning_approval_subject_sha256"] = execution_subject_sha
    execution_artifact_sha_by_kind = {
        "owner_assignment": planning_owner_sha,
        "resource_calendar": calendar_sha,
        "delivery_wbs": execution_wbs_sha,
        "forecast_snapshot": execution_forecast_sha,
    }
    execution_approvals: dict[str, Any] = {}
    for role in ("release-owner", "build-platform-owner", "qa-evidence-owner"):
        evidence_filename = f"execution-template-approval-evidence-{role}.json"
        evidence_sha = corpus.add_support(
            evidence_filename,
            {
                "evidence_id": f"fixture-execution-template-approval-evidence-{role}",
                "subject_sha256": execution_subject_sha,
                "notice": NOTICE,
            },
            f"Synthetic accepted execution-template planning evidence for {role}.",
        )
        execution_approvals[role] = {
            "role_id": role,
            "principal_id": f"fixture-{role}-person",
            "natural_person_id": f"fixture-{role}-person",
            "decision": "APPROVED",
            "approved_at": "2026-06-01T12:00:45Z",
            "assignment_ref": planning_owner_ref,
            "planning_approval_subject_sha256": execution_subject_sha,
            "bundle_revision": 1,
            "predecessor_sha256": None,
            "artifact_sha256_by_kind": execution_artifact_sha_by_kind,
            "owner_assignment_revision": 1,
            "approval_evidence_ref": artifact_ref(
                f"fixture-execution-template-approval-evidence-{role}",
                "ylx.planning-approval-evidence.v1",
                evidence_sha,
                (
                    "contracts/fixtures/governance-models/support/"
                    f"{evidence_filename}"
                ),
                1,
            ),
        }
    execution_bundle["planning_bundle_approval_by_role"] = execution_approvals
    execution_bundle_sha = corpus.add(
        "VALID-EXECUTION-TEMPLATE-PLANNING-BUNDLE-V2-01",
        "execution-template-planning-bundle-v2.json",
        "delivery-planning-bundle-v2.schema.json",
        execution_bundle,
    )
    execution_bundle_ref = artifact_ref(
        execution_bundle["artifact_id"],
        execution_bundle["schema"],
        execution_bundle_sha,
        "valid/execution-template-planning-bundle-v2.json",
        1,
    )

    governed_owner_created_at = "2026-06-01T12:04:10Z"
    governed_calendar_created_at = "2026-06-01T12:04:20Z"
    governed_wbs_created_at = "2026-06-01T12:04:25Z"
    governed_forecast_created_at = "2026-06-01T12:04:30Z"
    governed_bundle_created_at = "2026-06-01T12:04:40Z"
    bootstrap_source_refs = [
        ref
        for ref in planning_owner.get("source_refs", [])
        if ref.get("authority_kind") == "external-organizational-authority"
    ]
    if len(bootstrap_source_refs) != 1:
        raise AssertionError("governed owner root requires one bootstrap authority")
    governed_owner = copy.deepcopy(planning_owner)
    governed_owner.update(
        {
            "artifact_id": "M0-GOV-01-governed-owner-assignment",
            "revision": 1,
            "predecessor_sha256": None,
            "generated_at": governed_owner_created_at,
            "source_refs": [
                *copy.deepcopy(governed_common_source_refs),
                copy.deepcopy(bootstrap_source_refs[0]),
            ],
            "overall_status": "ACCEPTED",
            "blockers": [],
        }
    )
    governed_owner_sha = corpus.add(
        "VALID-GOVERNED-OWNER-ASSIGNMENT-ROOT-01",
        "governed-owner-assignment-root.json",
        "owner-assignment-v1.schema.json",
        governed_owner,
    )
    governed_owner_ref = artifact_ref(
        governed_owner["artifact_id"],
        governed_owner["schema"],
        governed_owner_sha,
        "valid/governed-owner-assignment-root.json",
        1,
    )

    governed_calendar = copy.deepcopy(calendar)
    governed_calendar.update(
        {
            "artifact_id": "M0-GOV-01-governed-resource-calendar",
            "revision": 1,
            "predecessor_sha256": None,
            "generated_at": governed_calendar_created_at,
            "source_refs": copy.deepcopy(governed_common_source_refs),
            "overall_status": "ACCEPTED",
            "blockers": [],
        }
    )
    governed_calendar_sha = corpus.add(
        "VALID-GOVERNED-RESOURCE-CALENDAR-ROOT-01",
        "governed-resource-calendar-root.json",
        "resource-calendar-v1.schema.json",
        governed_calendar,
    )
    governed_calendar_ref = artifact_ref(
        governed_calendar["artifact_id"],
        governed_calendar["schema"],
        governed_calendar_sha,
        "valid/governed-resource-calendar-root.json",
        1,
    )

    governed_wbs = copy.deepcopy(wbs_v2)
    governed_wbs.update(
        {
            "artifact_id": "M0-GOV-01-governed-delivery-wbs",
            "revision": 1,
            "predecessor_sha256": None,
            "generated_at": governed_wbs_created_at,
            "source_refs": copy.deepcopy(governed_common_source_refs),
            "overall_status": "ACCEPTED",
            "planning_gate": "M0",
            "detail_horizon": copy.deepcopy(detail_horizon),
            "nodes": bound_nodes_beyond_m1(wbs_v2["nodes"]),
            "active_blocker_ids": [],
            "active_blocker_coverage": {},
            "blockers": [],
        }
    )
    for governed_node in governed_wbs["nodes"]:
        for role_field in ("accountable_owner_ref", "executor_ref", "reviewer_ref"):
            if role_field in governed_node:
                governed_node[role_field]["owner_assignment_ref"] = copy.deepcopy(
                    governed_owner_ref
                )
    governed_wbs_sha = corpus.add(
        "VALID-GOVERNED-DELIVERY-WBS-ROOT-01",
        "governed-delivery-wbs-root.json",
        "delivery-wbs-v2.schema.json",
        governed_wbs,
    )
    governed_wbs_ref = artifact_ref(
        governed_wbs["artifact_id"],
        governed_wbs["schema"],
        governed_wbs_sha,
        "valid/governed-delivery-wbs-root.json",
        1,
    )

    governed_forecast = copy.deepcopy(forecast_v2)
    governed_forecast.update(
        {
            "artifact_id": "M0-GOV-01-governed-forecast-snapshot",
            "revision": 1,
            "predecessor_sha256": None,
            "generated_at": governed_forecast_created_at,
            "source_refs": copy.deepcopy(governed_common_source_refs),
            "overall_status": "ACCEPTED",
            "owner_assignment_sha256": governed_owner_sha,
            "resource_calendar_sha256": governed_calendar_sha,
            "delivery_wbs_sha256": governed_wbs_sha,
            "blockers": [],
        }
    )
    governed_forecast.update(
        calculate_delivery_planning(governed_wbs, governed_calendar)
    )
    governed_forecast_sha = corpus.add(
        "VALID-GOVERNED-FORECAST-SNAPSHOT-ROOT-01",
        "governed-forecast-snapshot-root.json",
        "forecast-snapshot-v1.schema.json",
        governed_forecast,
    )
    governed_forecast_ref = artifact_ref(
        governed_forecast["artifact_id"],
        governed_forecast["schema"],
        governed_forecast_sha,
        "valid/governed-forecast-snapshot-root.json",
        1,
    )

    governed_bundle = copy.deepcopy(bundle_v2)
    governed_bundle.update(
        {
            "artifact_id": "M0-GOV-01-governed-delivery-planning-bundle",
            "revision": 1,
            "predecessor_sha256": None,
            "generated_at": governed_bundle_created_at,
            "source_refs": copy.deepcopy(governed_common_source_refs),
            "planning_gate": "M0",
            "detail_horizon": copy.deepcopy(detail_horizon),
            "artifacts": {
                "owner_assignment": governed_owner_ref,
                "resource_calendar": governed_calendar_ref,
                "delivery_wbs": governed_wbs_ref,
                "forecast_snapshot": governed_forecast_ref,
            },
            "overall_status": "ACCEPTED",
        }
    )
    governed_subject_sha = sha(
        canonical_bytes(
            {field: governed_bundle[field] for field in subject_fields}
        )
    )
    governed_bundle["planning_approval_subject_sha256"] = governed_subject_sha
    governed_artifact_sha_by_kind = {
        "owner_assignment": governed_owner_sha,
        "resource_calendar": governed_calendar_sha,
        "delivery_wbs": governed_wbs_sha,
        "forecast_snapshot": governed_forecast_sha,
    }
    governed_approvals = copy.deepcopy(
        bundle_v2["planning_bundle_approval_by_role"]
    )
    for approval_record in governed_approvals.values():
        approval_record.update(
            {
                "assignment_ref": copy.deepcopy(governed_owner_ref),
                "planning_approval_subject_sha256": governed_subject_sha,
                "bundle_revision": 1,
                "predecessor_sha256": None,
                "artifact_sha256_by_kind": copy.deepcopy(
                    governed_artifact_sha_by_kind
                ),
                "owner_assignment_revision": 1,
            }
        )
    governed_bundle["planning_bundle_approval_by_role"] = governed_approvals
    governed_bundle_sha = corpus.add(
        "VALID-GOVERNED-DELIVERY-PLANNING-BUNDLE-ROOT-01",
        "governed-delivery-planning-bundle-root.json",
        "delivery-planning-bundle-v2.schema.json",
        governed_bundle,
    )
    return {
        "migration_anchor": migration_anchor,
        "migration_sha": migration_sha,
        "migration_fixture_sha": migration_fixture_sha,
        "owner": planning_owner,
        "owner_sha": planning_owner_sha,
        "owner_ref": planning_owner_ref,
        "calendar": calendar,
        "calendar_sha": calendar_sha,
        "calendar_ref": calendar_ref,
        "wbs": wbs_v2,
        "wbs_sha": wbs_v2_sha,
        "wbs_ref": wbs_v2_ref,
        "forecast": forecast_v2,
        "forecast_sha": forecast_v2_sha,
        "forecast_ref": forecast_v2_ref,
        "execution_nodes": copy.deepcopy(all_nodes),
        "execution_planning": {
            "wbs": execution_wbs,
            "wbs_sha": execution_wbs_sha,
            "wbs_ref": execution_wbs_ref,
            "forecast": execution_forecast,
            "forecast_sha": execution_forecast_sha,
            "forecast_ref": execution_forecast_ref,
            "bundle": execution_bundle,
            "bundle_sha": execution_bundle_sha,
            "bundle_ref": execution_bundle_ref,
        },
        "bundle": bundle_v2,
        "bundle_sha": bundle_v2_sha,
        "bundle_ref": artifact_ref(
            bundle_v2["artifact_id"],
            bundle_v2["schema"],
            bundle_v2_sha,
            "valid/delivery-planning-bundle-v2.json",
            1,
        ),
        "governed_roots": {
            "owner_assignment": governed_owner,
            "owner_assignment_sha": governed_owner_sha,
            "resource_calendar": governed_calendar,
            "resource_calendar_sha": governed_calendar_sha,
            "delivery_wbs": governed_wbs,
            "delivery_wbs_sha": governed_wbs_sha,
            "forecast_snapshot": governed_forecast,
            "forecast_snapshot_sha": governed_forecast_sha,
            "delivery_planning_bundle": governed_bundle,
            "delivery_planning_bundle_sha": governed_bundle_sha,
        },
        "evidence_node_id_by_gate": evidence_node_id_by_gate,
        "evidence_node_id_by_m5_phase": evidence_node_id_by_m5_phase,
        "evidence_authority_ref_by_gate": evidence_authority_ref_by_gate,
        "m2_bootstrap_node_id_by_action": m2_bootstrap_node_id_by_action,
        "m2_bootstrap_authority_ref_by_action": (
            m2_bootstrap_authority_ref_by_action
        ),
        "m2_qualification_creation_node_id": m2_qualification_creation_node_id,
        "m1_scope_creation_node_id": m1_scope_creation_node_id,
        "m1_measurement_training_node_id": m1_measurement_training_node_id,
        "m1_threshold_freeze_node_id": m1_threshold_freeze_node_id,
        "m1_threshold_freeze_authority_ref": (
            m1_threshold_freeze_authority_ref
        ),
        "m3_measurement_holdout_node_id": m3_measurement_holdout_node_id,
        "transition_node_id_by_key": transition_node_id_by_key,
        "g0_policy": g0_policy_state,
        "all_evidence_node_id": all_evidence_node_id,
        "all_evidence_authority_ref": all_evidence_authority_ref,
        "action_node_id_by_action": action_node_id_by_action,
        "action_authority_ref_by_action": action_authority_ref_by_action,
        "issue_verdict_node_id_by_gate": issue_verdict_node_id_by_gate,
    }


def build_m0_bootstrap_graph(
    corpus: Corpus,
    planning_state: dict[str, Any],
    g0_state: dict[str, Any],
) -> dict[str, Any]:
    """Build the complete synthetic M0 bootstrap F/P/R graph."""

    fixture_prefix = "contracts/fixtures/governance-models/"
    valid_prefix = f"{fixture_prefix}valid/"
    support_prefix = f"{fixture_prefix}support/"
    repository_locator = "fixture://m0-bootstrap/repository"
    bootstrap_attempt_id = "fixture-m0-bootstrap-attempt-001"
    operation_issuer_id = "fixture-m0-operation-authority-issuer"
    receipt_issuer_id = "fixture-m0-terminal-receipt-issuer"
    receipt_sink_id = "fixture-m0-terminal-audit-sink"
    planning_roles = (
        "release-owner",
        "build-platform-owner",
        "qa-evidence-owner",
    )
    grant_kinds = (
        "PUBLISHER",
        "IMPORTER",
        "REPOSITORY_WRITE",
        "READBACK",
        "ISSUE_WRITE",
        "ISSUE_READBACK",
        "TERMINAL_SINK",
    )
    terminal_domain = "YLX-PLANNING-BOOTSTRAP-TERMINAL-RECEIPT-V1"
    repository_receipt_domain = "YLX-TERMINAL-AUDIT-RECEIPT-V1"

    base_time = datetime(2026, 6, 1, 12, 3, tzinfo=timezone.utc)

    def moment(offset_seconds: int) -> str:
        return (
            (base_time + timedelta(seconds=offset_seconds))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )

    def valid_locator(filename: str) -> str:
        return f"{valid_prefix}{filename}"

    def support_locator(filename: str) -> str:
        return f"{support_prefix}{filename}"

    def add_support_raw(filename: str, raw: bytes, purpose: str) -> str:
        path = SUPPORT_ROOT / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        rel = path.relative_to(FIXTURE_ROOT).as_posix()
        digest = sha(raw)
        existing = next(
            (entry for entry in corpus.support_entries if entry["path"] == rel),
            None,
        )
        if existing is not None:
            if existing["sha256"] != digest or path.read_bytes() != raw:
                raise AssertionError(
                    f"support fixture reconstruction drift for {rel}"
                )
            return digest
        path.write_bytes(raw)
        corpus.support_entries.append(
            {
                "path": rel,
                "sha256": digest,
                "exact_byte_length": len(raw),
                "purpose": purpose,
                "test_only": True,
                "notice": NOTICE,
            }
        )
        return digest

    def key_material(label: str, key_id: str) -> dict[str, Any]:
        seed = hashlib.sha256(
            f"YLX SYNTHETIC M0 TEST KEY ONLY:{label}".encode("ascii")
        ).digest()
        private_key = Ed25519PrivateKey.from_private_bytes(seed)
        public_raw = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return {
            "key_id": key_id,
            "private_key": private_key,
            "public_key_base64": base64.b64encode(public_raw).decode("ascii"),
            "fingerprint_sha256": sha(public_raw),
        }

    operation_issuer_key = key_material(
        "operation-authority-issuer",
        "fixture-m0-operation-authority-issuer-key",
    )
    receipt_issuer_key = key_material(
        "terminal-receipt-issuer",
        "fixture-m0-terminal-receipt-issuer-key",
    )
    def shared_role_key_material(role_id: str) -> dict[str, Any]:
        seed = hashlib.sha256(
            f"YLX SYNTHETIC TEST KEY ONLY:{role_id}".encode("ascii")
        ).digest()
        private_key = Ed25519PrivateKey.from_private_bytes(seed)
        public_raw = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return {
            "key_id": f"fixture-key-{role_id}",
            "private_key": private_key,
            "public_key_base64": base64.b64encode(public_raw).decode("ascii"),
            "fingerprint_sha256": sha(public_raw),
        }

    approval_key_by_role = {
        role_id: shared_role_key_material(role_id)
        for role_id in planning_roles
    }

    corpus.relationships["m0_test_only_operation_authority_issuer_pin"] = {
        "pin_id": "fixture-m0-operation-authority-issuer-pin",
        "issuer_id": operation_issuer_id,
        "signing_key_id": operation_issuer_key["key_id"],
        "public_key_base64": operation_issuer_key["public_key_base64"],
        "allowed_grant_kinds": list(grant_kinds),
        "valid_from": "2026-01-01T00:00:00Z",
        "not_after": "2027-01-01T00:00:00Z",
        "authority_origin": "PREEXISTING_OUTSIDE_CANDIDATE_REPOSITORY",
        "test_only": True,
    }

    operation_authority_path_by_step_and_kind: dict[
        str, dict[str, str]
    ] = {}
    operation_authority_sha256_by_step_and_kind: dict[
        str, dict[str, str]
    ] = {}

    def add_operation_grant(
        *,
        step_id: str,
        grant_kind: str,
        grant: dict[str, Any],
        issued_at: str,
    ) -> tuple[str, str]:
        filename = (
            f"m0-operation-authority-{step_id}-"
            f"{grant_kind.lower().replace('_', '-')}.json"
        )
        payload = {
            "schema": "ylx.planning-bootstrap-operation-authority.v1",
            "grant_id": (
                f"fixture-m0-operation-authority-{step_id}-"
                f"{grant_kind.lower().replace('_', '-')}"
            ),
            "grant_kind": grant_kind,
            "bootstrap_attempt_id": bootstrap_attempt_id,
            "grant": grant,
            "issued_at": issued_at,
            "valid_from": "2026-01-01T00:00:00Z",
            "not_after": "2027-01-01T00:00:00Z",
            "one_use": True,
            "issuer_id": operation_issuer_id,
            "signature_algorithm": "Ed25519",
            "signing_key_id": operation_issuer_key["key_id"],
        }
        value = sign_closed_record(
            payload,
            private_key=operation_issuer_key["private_key"],
            signature_domain=(
                "YLX-PLANNING-BOOTSTRAP-OPERATION-AUTHORITY-V1/"
                f"{grant_kind}"
            ),
        )
        digest = corpus.add(
            (
                f"VALID-M0-OPERATION-AUTHORITY-{step_id.upper()}-"
                f"{grant_kind}-01"
            ),
            filename,
            "planning-bootstrap-operation-authority-v1.schema.json",
            value,
        )
        locator = valid_locator(filename)
        operation_authority_path_by_step_and_kind.setdefault(step_id, {})[
            grant_kind
        ] = locator
        operation_authority_sha256_by_step_and_kind.setdefault(step_id, {})[
            grant_kind
        ] = digest
        return locator, digest

    def add_terminal_sink_grant(
        *,
        step_id: str,
        operation_instance_id: str,
        payload_sha256: str,
        operation: str,
        target_scope: str,
        receipt_schema: str,
        receipt_id: str,
        receipt_locator: str,
        operation_class: str,
        issued_at: str,
    ) -> tuple[str, str]:
        return add_operation_grant(
            step_id=step_id,
            grant_kind="TERMINAL_SINK",
            grant={
                "step_id": step_id,
                "operation_instance_id": operation_instance_id,
                "payload_sha256": payload_sha256,
                "repository_locator": repository_locator,
                "operation": operation,
                "target_scope": target_scope,
                "sink_id": receipt_sink_id,
                "sink_locator": receipt_locator,
                "receipt_schema": receipt_schema,
                "receipt_id": receipt_id,
                "receipt_issuer_id": receipt_issuer_id,
                "receipt_signing_key_id": receipt_issuer_key["key_id"],
                "receipt_signing_public_key_base64": receipt_issuer_key[
                    "public_key_base64"
                ],
                "operation_class": operation_class,
            },
            issued_at=issued_at,
        )

    def add_write_authorities(
        *,
        step_id: str,
        semantic_grant_kind: str,
        artifact_schema: str | None,
        artifact_id: str | None,
        artifact_revision: int | None,
        role_id: str | None,
        payload_ref: str,
        payload_sha256: str,
        operation: str,
        target_scope: str,
        receipt_schema: str,
        receipt_id: str,
        receipt_locator: str,
        operation_class: str,
        actor_id: str,
        issued_at: str,
    ) -> tuple[str, str, str, str]:
        operation_instance_id = f"fixture-m0-operation-{step_id}"
        sink_ref, sink_sha = add_terminal_sink_grant(
            step_id=step_id,
            operation_instance_id=operation_instance_id,
            payload_sha256=payload_sha256,
            operation=operation,
            target_scope=target_scope,
            receipt_schema=receipt_schema,
            receipt_id=receipt_id,
            receipt_locator=receipt_locator,
            operation_class=operation_class,
            issued_at=issued_at,
        )
        semantic_grant: dict[str, Any] = {
            "step_id": step_id,
            "operation_instance_id": operation_instance_id,
            "actor_id": actor_id,
            "payload_ref": payload_ref,
            "payload_sha256": payload_sha256,
            "repository_locator": repository_locator,
            "operation": operation,
            "target_scope": target_scope,
            "terminal_sink_grant_ref": sink_ref,
            "terminal_sink_grant_sha256": sink_sha,
        }
        if semantic_grant_kind in {"PUBLISHER", "IMPORTER"}:
            if artifact_schema is None or artifact_id is None:
                raise AssertionError(
                    f"{semantic_grant_kind} requires an artifact schema and ID"
                )
            semantic_grant.update(
                {
                    "artifact_schema": artifact_schema,
                    "artifact_id": artifact_id,
                }
            )
        if semantic_grant_kind == "PUBLISHER":
            if artifact_revision is None:
                raise AssertionError("publisher grant requires artifact revision")
            semantic_grant["artifact_revision"] = artifact_revision
        elif semantic_grant_kind == "IMPORTER":
            if role_id is None:
                raise AssertionError("importer grant requires role ID")
            semantic_grant["role_id"] = role_id
        semantic_ref, semantic_sha = add_operation_grant(
            step_id=step_id,
            grant_kind=semantic_grant_kind,
            grant=semantic_grant,
            issued_at=issued_at,
        )
        permission_ref, permission_sha = add_operation_grant(
            step_id=step_id,
            grant_kind="REPOSITORY_WRITE",
            grant={
                "step_id": step_id,
                "operation_instance_id": operation_instance_id,
                "actor_id": actor_id,
                "payload_ref": payload_ref,
                "payload_sha256": payload_sha256,
                "repository_locator": repository_locator,
                "operation": operation,
                "target_scope": target_scope,
                "terminal_sink_grant_ref": sink_ref,
                "terminal_sink_grant_sha256": sink_sha,
            },
            issued_at=issued_at,
        )
        return semantic_ref, semantic_sha, permission_ref, permission_sha

    def add_readback_authority(
        *,
        step_id: str,
        artifact_schema: str,
        artifact_id: str,
        payload_ref: str,
        payload_sha256: str,
        target_scope: str,
        receipt_schema: str,
        receipt_id: str,
        receipt_locator: str,
        reader_id: str,
        issued_at: str,
    ) -> tuple[str, str]:
        operation_instance_id = f"fixture-m0-operation-{step_id}"
        sink_ref, sink_sha = add_terminal_sink_grant(
            step_id=step_id,
            operation_instance_id=operation_instance_id,
            payload_sha256=payload_sha256,
            operation="READ_EXACT",
            target_scope=target_scope,
            receipt_schema=receipt_schema,
            receipt_id=receipt_id,
            receipt_locator=receipt_locator,
            operation_class="PLANNING_READBACK",
            issued_at=issued_at,
        )
        return add_operation_grant(
            step_id=step_id,
            grant_kind="READBACK",
            grant={
                "step_id": step_id,
                "operation_instance_id": operation_instance_id,
                "reader_id": reader_id,
                "artifact_schema": artifact_schema,
                "artifact_id": artifact_id,
                "payload_ref": payload_ref,
                "payload_sha256": payload_sha256,
                "repository_locator": repository_locator,
                "operation": "READ_EXACT",
                "target_scope": target_scope,
                "terminal_sink_grant_ref": sink_ref,
                "terminal_sink_grant_sha256": sink_sha,
            },
            issued_at=issued_at,
        )

    def issue_register_bytes(
        rows: list[tuple[str, str, str, str]], marker: str
    ) -> tuple[bytes, dict[str, Any]]:
        prefix = (
            NOTICE
            + f"\n\n# Synthetic M0 issue register {marker}\n\n## Overview\n"
        ).encode("utf-8")
        overview = b"".join(
            (
                f"{issue_id} | {status} | fixture-release-owner-slot | "
                f"{target}\n"
            ).encode("utf-8")
            for issue_id, status, _, target in rows
        )
        raw = prefix + overview + b"\n"
        slices: dict[str, Any] = {}
        overview_cursor = len(prefix)
        for issue_id, status, severity, target in rows:
            overview_row = (
                f"{issue_id} | {status} | fixture-release-owner-slot | "
                f"{target}\n"
            ).encode("utf-8")
            body = (
                f"## {issue_id}\n\n"
                "#### Canonical machine fields\n\n"
                "| Status | Severity | Owner slot | Component subrole | Target | Blocks gate |\n"
                "|---|---|---|---|---|---|\n"
                f"| `{status}` | `{severity}` | `release-owner` | "
                f"synthetic-only | `{target}` | `{target}` |\n\n"
                f"Revision marker: {marker}\n"
                "This synthetic issue is not evidence and cannot close a gate.\n"
            ).encode("utf-8")
            body_start = len(raw)
            raw += body
            slices[issue_id] = {
                "overview_start_byte": overview_cursor,
                "overview_end_byte": overview_cursor + len(overview_row),
                "overview_sha256": sha(overview_row),
                "body_start_byte": body_start,
                "body_end_byte": len(raw),
                "body_sha256": sha(body),
            }
            overview_cursor += len(overview_row)
        return raw, slices

    predecessor_source_filename = "m0-g0-issue-register-predecessor-source.md"
    predecessor_archive_filename = "m0-g0-issue-register-predecessor-archive.md"
    predecessor_source_raw, predecessor_slices = issue_register_bytes(
        [
            ("O-1", "OPEN", "S1", "M5"),
            ("O-35", "BLOCKED", "S0", "G0"),
        ],
        "predecessor-r1",
    )
    predecessor_source_sha = add_support_raw(
        predecessor_source_filename,
        predecessor_source_raw,
        "Synthetic pre-reconciliation M0 issue-register live source bytes.",
    )
    predecessor_archive_sha = add_support_raw(
        predecessor_archive_filename,
        predecessor_source_raw,
        "Synthetic immutable archive of the pre-reconciliation issue source.",
    )
    predecessor_head_filename = "m0-g0-issue-register-predecessor-head.json"
    predecessor_head = {
        "schema": "ylx.issue-register-head.v1",
        "issue_register_revision": 1,
        "predecessor_revision": None,
        "predecessor_head_artifact_sha256": None,
        "source_artifact_path": support_locator(predecessor_source_filename),
        "issue_register_sha256": predecessor_source_sha,
        "archived_source_path": support_locator(predecessor_archive_filename),
        "archived_source_sha256": predecessor_archive_sha,
        "selector_version": "issue-register-gate-selector.v2",
        "overview_cardinality": len(predecessor_slices),
        "issue_slices_by_id": predecessor_slices,
        "published_at": moment(-10),
        "publisher_role_slot": "release-owner",
        "approvals": [],
        "publication_mode": "OBSERVED_PRE_AUTHORITY",
        "authority_effect": "DENIAL_ONLY",
        "policy_authority_ref": None,
        "publisher_assignment_ref": None,
    }
    predecessor_head_sha = corpus.add(
        "VALID-M0-G0-ISSUE-REGISTER-PREDECESSOR-HEAD-01",
        predecessor_head_filename,
        "issue-register-head-v1.schema.json",
        predecessor_head,
    )
    predecessor_head_locator = valid_locator(predecessor_head_filename)

    replacement_source_filename = "m0-g0-issue-register-replacement-source.md"
    replacement_archive_filename = "m0-g0-issue-register-replacement-archive.md"
    replacement_source_raw, replacement_slices = issue_register_bytes(
        [("O-1", "OPEN", "S1", "M5")],
        "authorized-r2-o35-closed",
    )
    replacement_source_sha = add_support_raw(
        replacement_source_filename,
        replacement_source_raw,
        "Synthetic governed issue source with the G0 selector empty.",
    )
    replacement_archive_sha = add_support_raw(
        replacement_archive_filename,
        replacement_source_raw,
        "Synthetic immutable archive of the governed issue source.",
    )
    if replacement_archive_sha != replacement_source_sha:
        raise AssertionError("M0 issue source/archive exact bytes diverged")

    roots = planning_state["governed_roots"]
    owner_output_fields = [
        "schema",
        "artifact_id",
        "revision",
        "predecessor_sha256",
        "generated_at",
        "source_refs",
        "artifact_metadata",
        "overall_status",
        "assignments",
        "blockers",
    ]
    calendar_output_fields = [
        "schema",
        "artifact_id",
        "revision",
        "predecessor_sha256",
        "generated_at",
        "source_refs",
        "artifact_metadata",
        "overall_status",
        "timezone",
        "planning_horizon",
        "resources",
        "windows",
        "blockers",
    ]
    wbs_output_fields = [
        "schema",
        "artifact_id",
        "revision",
        "predecessor_sha256",
        "generated_at",
        "source_refs",
        "artifact_metadata",
        "overall_status",
        "planning_gate",
        "detail_horizon",
        "registry_binding",
        "requirement_ids",
        "requirement_coverage",
        "active_blocker_ids",
        "active_blocker_coverage",
        "nodes",
        "blockers",
    ]
    forecast_output_fields = [
        "schema",
        "artifact_id",
        "revision",
        "predecessor_sha256",
        "generated_at",
        "source_refs",
        "artifact_metadata",
        "overall_status",
        "as_of",
        "owner_assignment_sha256",
        "resource_calendar_sha256",
        "delivery_wbs_sha256",
        "scheduling_rules_version",
        "calculator",
        "task_forecasts",
        "dependency_critical_path",
        "resource_levelled_driving_path",
        "decision_need_bys",
        "milestone_forecasts",
        "capacity_overallocations",
        "external_constraints",
        "assumptions",
        "change_reasons",
        "blockers",
    ]
    subject_fields = [
        "schema",
        "artifact_id",
        "revision",
        "predecessor_sha256",
        "source_refs",
        "artifact_metadata",
        "planning_gate",
        "detail_horizon",
        "registry_binding",
        "artifacts",
        "bundle_kind",
        "final_actual_variance_reconciliation",
    ]

    projection_inputs = [
        "input-closure",
        "owner-payload",
        "owner-publication-receipt",
        "owner-readback-receipt",
        "g0-issue-reconciliation",
    ]

    def add_projection_implementation(
        name: str, output_schema: str, output_fields: list[str]
    ) -> tuple[str, str]:
        filename = f"m0-bootstrap-{name}-projection-implementation.json"
        digest = corpus.add_support(
            filename,
            {
                "implementation_id": f"fixture-m0-{name}-projection",
                "implementation_revision": 1,
                "canonical_encoding": "RFC8785_JSON_UTF8",
                "output_schema": output_schema,
                "output_field_order": output_fields,
                "authority_effect": "NONE",
                "notice": NOTICE,
            },
            f"Synthetic immutable deterministic {name} projection definition.",
        )
        return support_locator(filename), digest

    owner_impl_ref, owner_impl_sha = add_projection_implementation(
        "owner", "ylx.owner-assignment.v1", owner_output_fields
    )
    calendar_impl_ref, calendar_impl_sha = add_projection_implementation(
        "resource-calendar", "ylx.resource-calendar.v1", calendar_output_fields
    )
    wbs_impl_ref, wbs_impl_sha = add_projection_implementation(
        "delivery-wbs", "ylx.delivery-wbs.v2", wbs_output_fields
    )
    forecast_impl_ref, forecast_impl_sha = add_projection_implementation(
        "forecast-snapshot", "ylx.forecast-snapshot.v1", forecast_output_fields
    )
    subject_impl_ref, subject_impl_sha = add_projection_implementation(
        "approval-subject", "ylx.delivery-planning-bundle.v2", subject_fields
    )

    def projection_descriptor(
        projection_id: str,
        implementation_ref: str,
        implementation_sha256: str,
        input_names: list[str],
        output_schema: str,
        output_field_order: list[str],
    ) -> dict[str, Any]:
        return {
            "projection_id": projection_id,
            "projection_revision": 1,
            "implementation_ref": implementation_ref,
            "implementation_sha256": implementation_sha256,
            "input_names": input_names,
            "output_schema": output_schema,
            "output_field_order": output_field_order,
        }

    derivation_contract_filename = (
        "m0-planning-bootstrap-candidate-derivation-contract.json"
    )
    calculator = planning_state["forecast"]["calculator"]
    derivation_contract = {
        "schema": "ylx.planning-bootstrap-candidate-derivation-contract.v1",
        "contract_id": "fixture-m0-bootstrap-candidate-derivation-contract",
        "contract_revision": 1,
        "canonical_encoding": "RFC8785_JSON_UTF8",
        "closure_schema": "ylx.planning-bootstrap-input-closure.v1",
        "artifact_schema_by_kind": {
            "owner_assignment": "ylx.owner-assignment.v1",
            "resource_calendar": "ylx.resource-calendar.v1",
            "delivery_wbs": "ylx.delivery-wbs.v2",
            "forecast_snapshot": "ylx.forecast-snapshot.v1",
        },
        "artifact_id_by_kind": {
            "owner_assignment": "M0-GOV-01-governed-owner-assignment",
            "resource_calendar": "M0-GOV-01-governed-resource-calendar",
            "delivery_wbs": "M0-GOV-01-governed-delivery-wbs",
            "forecast_snapshot": "M0-GOV-01-governed-forecast-snapshot",
        },
        "phase_a_owner_projection": projection_descriptor(
            "fixture-m0-phase-a-owner-projection",
            owner_impl_ref,
            owner_impl_sha,
            [
                "input-closure",
                "g0-policy-ratification",
                "planning-migration-observation",
                "owner-assignment-bootstrap-authority",
            ],
            "ylx.owner-assignment.v1",
            owner_output_fields,
        ),
        "phase_b_child_projection_by_kind": {
            "resource_calendar": projection_descriptor(
                "fixture-m0-phase-b-resource-calendar-projection",
                calendar_impl_ref,
                calendar_impl_sha,
                projection_inputs,
                "ylx.resource-calendar.v1",
                calendar_output_fields,
            ),
            "delivery_wbs": projection_descriptor(
                "fixture-m0-phase-b-delivery-wbs-projection",
                wbs_impl_ref,
                wbs_impl_sha,
                projection_inputs,
                "ylx.delivery-wbs.v2",
                wbs_output_fields,
            ),
            "forecast_snapshot": projection_descriptor(
                "fixture-m0-phase-b-forecast-snapshot-projection",
                forecast_impl_ref,
                forecast_impl_sha,
                projection_inputs,
                "ylx.forecast-snapshot.v1",
                forecast_output_fields,
            ),
        },
        "phase_b_bundle_subject_projection": projection_descriptor(
            "fixture-m0-phase-b-approval-subject-projection",
            subject_impl_ref,
            subject_impl_sha,
            [
                *projection_inputs,
                "resource-calendar-candidate",
                "delivery-wbs-candidate",
                "forecast-snapshot-candidate",
            ],
            "ylx.delivery-planning-bundle.v2",
            subject_fields,
        ),
        "approval_subject_schema": "ylx.planning-approval-subject.v1",
        "approval_subject_field_order": subject_fields,
        "calculator_contract_ref": calculator["artifact_path"],
        "calculator_contract_sha256": calculator["artifact_sha256"],
    }
    derivation_contract_sha = corpus.add(
        "VALID-M0-PLANNING-BOOTSTRAP-DERIVATION-CONTRACT-01",
        derivation_contract_filename,
        "planning-bootstrap-candidate-derivation-contract-v1.schema.json",
        derivation_contract,
    )
    derivation_contract_locator = valid_locator(derivation_contract_filename)

    approval_id_by_role = {
        role_id: f"fixture-m0-planning-bundle-approval-{role_id}"
        for role_id in planning_roles
    }
    path_by_step = {
        "input-closure-readback": (
            valid_locator("m0-planning-bootstrap-input-closure.json"),
            "ylx.planning-bootstrap-input-closure.v1",
            "fixture-m0-planning-bootstrap-input-closure",
        ),
        "phase-a-derivation-record-write": (
            valid_locator("m0-planning-bootstrap-owner-candidate-derivation.json"),
            "ylx.planning-bootstrap-owner-candidate-derivation.v1",
            "fixture-m0-owner-candidate-derivation",
        ),
        "owner-root-write": (
            valid_locator("governed-owner-assignment-root.json"),
            "ylx.owner-assignment.v1",
            "M0-GOV-01-governed-owner-assignment",
        ),
        "owner-root-readback": (
            valid_locator("governed-owner-assignment-root.json"),
            "ylx.owner-assignment.v1",
            "M0-GOV-01-governed-owner-assignment",
        ),
        "issue-source-replacement-write": (
            support_locator(replacement_source_filename),
            None,
            None,
        ),
        "issue-archive-write": (
            support_locator(replacement_archive_filename),
            None,
            None,
        ),
        "issue-head-write": (
            valid_locator("m0-g0-issue-register-successor-head.json"),
            None,
            None,
        ),
        "issue-transition-readback": (
            "fixture://m0-bootstrap/issue-transition-tuple",
            None,
            None,
        ),
        "issue-reconciliation": (
            valid_locator("m0-g0-issue-register-reconciliation-receipt.json"),
            "ylx.g0-issue-register-reconciliation-receipt.v1",
            None,
        ),
        "phase-b-derivation-record-write": (
            valid_locator(
                "m0-planning-bootstrap-post-reconciliation-candidate-derivation.json"
            ),
            "ylx.planning-bootstrap-post-reconciliation-candidate-derivation.v1",
            "fixture-m0-post-reconciliation-candidate-derivation",
        ),
        "approval-subject-write": (
            valid_locator("m0-planning-approval-subject.json"),
            "ylx.planning-approval-subject.v1",
            "M0-GOV-01-governed-delivery-planning-bundle",
        ),
        "approval-subject-readback": (
            valid_locator("m0-planning-approval-subject.json"),
            "ylx.planning-approval-subject.v1",
            "M0-GOV-01-governed-delivery-planning-bundle",
        ),
        "resource-calendar-write": (
            valid_locator("governed-resource-calendar-root.json"),
            "ylx.resource-calendar.v1",
            "M0-GOV-01-governed-resource-calendar",
        ),
        "resource-calendar-readback": (
            valid_locator("governed-resource-calendar-root.json"),
            "ylx.resource-calendar.v1",
            "M0-GOV-01-governed-resource-calendar",
        ),
        "delivery-wbs-write": (
            valid_locator("governed-delivery-wbs-root.json"),
            "ylx.delivery-wbs.v2",
            "M0-GOV-01-governed-delivery-wbs",
        ),
        "delivery-wbs-readback": (
            valid_locator("governed-delivery-wbs-root.json"),
            "ylx.delivery-wbs.v2",
            "M0-GOV-01-governed-delivery-wbs",
        ),
        "forecast-snapshot-write": (
            valid_locator("governed-forecast-snapshot-root.json"),
            "ylx.forecast-snapshot.v1",
            "M0-GOV-01-governed-forecast-snapshot",
        ),
        "forecast-snapshot-readback": (
            valid_locator("governed-forecast-snapshot-root.json"),
            "ylx.forecast-snapshot.v1",
            "M0-GOV-01-governed-forecast-snapshot",
        ),
        "containing-bundle-write": (
            valid_locator("governed-delivery-planning-bundle-root.json"),
            "ylx.delivery-planning-bundle.v2",
            "M0-GOV-01-governed-delivery-planning-bundle",
        ),
        "containing-bundle-readback": (
            valid_locator("governed-delivery-planning-bundle-root.json"),
            "ylx.delivery-planning-bundle.v2",
            "M0-GOV-01-governed-delivery-planning-bundle",
        ),
    }
    for role_id in planning_roles:
        approval_path = valid_locator(
            f"m0-planning-bundle-approval-{role_id}.json"
        )
        path_by_step[f"approval-{role_id}-import"] = (
            approval_path,
            "ylx.planning-bundle-approval.v1",
            approval_id_by_role[role_id],
        )
        path_by_step[f"approval-{role_id}-readback"] = (
            approval_path,
            "ylx.planning-bundle-approval.v1",
            approval_id_by_role[role_id],
        )

    def constraint(
        constraint_kind: str,
        operation: str,
        target_scope: str,
        artifact_schema: str | None,
        artifact_id: str | None,
    ) -> dict[str, Any]:
        return {
            "constraint_kind": constraint_kind,
            "repository_locator": repository_locator,
            "operation": operation,
            "target_scope": target_scope,
            "artifact_schema": artifact_schema,
            "artifact_id": artifact_id,
        }

    operation_constraint_by_step: dict[str, dict[str, Any]] = {}
    ordered_step_spec = [
        ("input-closure-readback", "readback", "READ_EXACT"),
        ("phase-a-derivation-record-write", "repository-write", "CREATE_IF_ABSENT"),
        ("owner-root-write", "repository-write", "CREATE_IF_ABSENT"),
        ("owner-root-readback", "readback", "READ_EXACT"),
        ("issue-source-replacement-write", "issue-write", "REPLACE_LIVE_SOURCE_EXACT"),
        ("issue-archive-write", "issue-write", "CREATE_IMMUTABLE_ARCHIVE"),
        ("issue-head-write", "issue-write", "PUBLISH_DIRECT_SUCCESSOR_HEAD"),
        ("issue-transition-readback", "issue-readback", "READ_EXACT_TRANSITION_TUPLE"),
        ("issue-reconciliation", "terminal-sink", "EMIT_RECONCILIATION"),
        ("phase-b-derivation-record-write", "repository-write", "CREATE_IF_ABSENT"),
        ("approval-subject-write", "repository-write", "CREATE_IF_ABSENT"),
        ("approval-subject-readback", "readback", "READ_EXACT"),
        ("approval-release-owner-import", "approval-import", "IMPORT_EXACT"),
        ("approval-release-owner-readback", "readback", "READ_EXACT"),
        ("approval-build-platform-owner-import", "approval-import", "IMPORT_EXACT"),
        ("approval-build-platform-owner-readback", "readback", "READ_EXACT"),
        ("approval-qa-evidence-owner-import", "approval-import", "IMPORT_EXACT"),
        ("approval-qa-evidence-owner-readback", "readback", "READ_EXACT"),
        ("resource-calendar-write", "repository-write", "CREATE_IF_ABSENT"),
        ("resource-calendar-readback", "readback", "READ_EXACT"),
        ("delivery-wbs-write", "repository-write", "CREATE_IF_ABSENT"),
        ("delivery-wbs-readback", "readback", "READ_EXACT"),
        ("forecast-snapshot-write", "repository-write", "CREATE_IF_ABSENT"),
        ("forecast-snapshot-readback", "readback", "READ_EXACT"),
        ("containing-bundle-write", "repository-write", "CREATE_IF_ABSENT"),
        ("containing-bundle-readback", "readback", "READ_EXACT"),
    ]
    for step_id, constraint_kind, operation in ordered_step_spec:
        target_scope, artifact_schema, artifact_id = path_by_step[step_id]
        operation_constraint_by_step[step_id] = constraint(
            constraint_kind,
            operation,
            target_scope,
            artifact_schema,
            artifact_id,
        )

    fact_entry_by_id: dict[str, Any] = {}

    def add_synthetic_fact(
        fact_id: str,
        fact_kind: str,
        canonical_value: Any,
        *,
        valid_interval: bool = True,
    ) -> None:
        filename = f"m0-bootstrap-fact-{fact_id}.json"
        fact_source = {
            "fact_source_id": f"fixture-m0-{fact_id}",
            "fact_kind": fact_kind,
            "canonical_value": canonical_value,
            "authority_effect": "NONE",
            "notice": NOTICE,
        }
        digest = corpus.add_support(
            filename,
            fact_source,
            f"Synthetic exact M0 input fact source for {fact_id}.",
        )
        fact_entry_by_id[fact_id] = {
            "fact_kind": fact_kind,
            "source_ref": support_locator(filename),
            "source_sha256": digest,
            "canonical_value": canonical_value,
            "observed_at_or_attested_at": moment(-5),
            "actor_or_attestor_id": "fixture-m0-input-attestor",
            "validity_not_before": (
                "2026-01-01T00:00:00Z" if valid_interval else None
            ),
            "validity_not_after": (
                "2027-01-01T00:00:00Z" if valid_interval else None
            ),
            "resolution_state": "RESOLVED",
        }

    add_synthetic_fact(
        "accountable-principals",
        "accountable-principal",
        {
            role_id: (
                "fixture-ga-operator-person"
                if role_id == "build-platform-owner"
                else f"fixture-{role_id}-person"
            )
            for role_id in ROLES
        },
    )
    add_synthetic_fact(
        "capacity-plan",
        "capacity",
        {"resource-build-platform-owner": {"available_fte": 1.0}},
    )
    add_synthetic_fact(
        "resource-window",
        "resource-window",
        {
            "window_id": "fixture-ci-window",
            "starts_at": "2026-06-01T00:00:00Z",
            "ends_at": "2026-06-30T23:59:59Z",
        },
    )
    add_synthetic_fact(
        "effort-estimate",
        "estimate",
        {"value": 8, "unit": "hours", "basis": "synthetic"},
    )
    add_synthetic_fact(
        "dependency-set",
        "dependency",
        {"predecessor_task_ids": []},
        valid_interval=False,
    )
    add_synthetic_fact(
        "schedule-anchor",
        "schedule-anchor",
        {"starts_at": "2026-06-02T00:00:00Z"},
    )
    add_synthetic_fact(
        "calculator-input",
        "calculator-input",
        copy.deepcopy(calculator),
        valid_interval=False,
    )
    for role_id in planning_roles:
        add_synthetic_fact(
            f"approval-{role_id}-identity",
            "approval-role-identity",
            {
                "role_id": role_id,
                "principal_id": f"fixture-{role_id}-person",
                "natural_person_id": f"fixture-{role_id}-person",
            },
        )
        add_synthetic_fact(
            f"approval-{role_id}-eligibility",
            "approval-role-eligibility",
            {"role_id": role_id, "eligible": True},
        )

    migration_source_ref = next(
        ref
        for ref in roots["owner_assignment"]["source_refs"]
        if ref["authority_kind"] == "planning-migration-observation"
    )
    g0_source_ref = {
        "ref_id": g0_state["ratification"]["event_id"],
        "authority_kind": "policy-ratification",
        "locator": g0_state["ratification_ref"]["artifact_path"],
        "sha256": g0_state["ratification_sha"],
    }
    bootstrap_authority_source_ref = next(
        ref
        for ref in roots["owner_assignment"]["source_refs"]
        if ref["authority_kind"] == "external-organizational-authority"
    )
    fact_entry_by_id["planning-migration-observation"] = {
        "fact_kind": "immutable-observation",
        "source_ref": migration_source_ref["locator"],
        "source_sha256": migration_source_ref["sha256"],
        "canonical_value": {
            "ref_id": migration_source_ref["ref_id"],
            "authority_kind": migration_source_ref["authority_kind"],
        },
        "observed_at_or_attested_at": moment(-5),
        "actor_or_attestor_id": "fixture-m0-input-attestor",
        "validity_not_before": None,
        "validity_not_after": None,
        "resolution_state": "RESOLVED",
    }
    fact_entry_by_id["g0-policy-ratification"] = {
        "fact_kind": "immutable-observation",
        "source_ref": g0_source_ref["locator"],
        "source_sha256": g0_source_ref["sha256"],
        "canonical_value": {
            "event_id": g0_state["ratification"]["event_id"],
            "revision": g0_state["ratification"]["revision"],
        },
        "observed_at_or_attested_at": moment(-5),
        "actor_or_attestor_id": "fixture-m0-input-attestor",
        "validity_not_before": None,
        "validity_not_after": None,
        "resolution_state": "RESOLVED",
    }
    fact_entry_by_id["owner-bootstrap-authority"] = {
        "fact_kind": "owner-assignment-bootstrap-authority",
        "source_ref": bootstrap_authority_source_ref["locator"],
        "source_sha256": bootstrap_authority_source_ref["sha256"],
        "canonical_value": {
            "authorized_artifact_id": "M0-GOV-01-governed-owner-assignment",
            "authorized_artifact_schema": "ylx.owner-assignment.v1",
            "authorized_revision": 1,
        },
        "observed_at_or_attested_at": moment(-5),
        "actor_or_attestor_id": "fixture-m0-input-attestor",
        "validity_not_before": "2026-01-01T00:00:00Z",
        "validity_not_after": "2027-01-01T00:00:00Z",
        "resolution_state": "RESOLVED",
    }
    fact_entry_by_id["issue-register-predecessor"] = {
        "fact_kind": "issue-register-predecessor",
        "source_ref": predecessor_head_locator,
        "source_sha256": predecessor_head_sha,
        "canonical_value": {
            "head_ref": predecessor_head_locator,
            "head_sha256": predecessor_head_sha,
            "source_ref": support_locator(predecessor_source_filename),
            "source_sha256": predecessor_source_sha,
        },
        "observed_at_or_attested_at": moment(-5),
        "actor_or_attestor_id": "fixture-m0-input-attestor",
        "validity_not_before": None,
        "validity_not_after": None,
        "resolution_state": "RESOLVED",
    }

    closure_filename = "m0-planning-bootstrap-input-closure.json"
    closure = {
        "schema": "ylx.planning-bootstrap-input-closure.v1",
        "closure_id": "fixture-m0-planning-bootstrap-input-closure",
        "bootstrap_attempt_id": bootstrap_attempt_id,
        "created_at": moment(0),
        "fact_entry_by_id": fact_entry_by_id,
        "intended_g0_issue_transition": {
            "predecessor_head_ref": predecessor_head_locator,
            "predecessor_head_sha256": predecessor_head_sha,
            "predecessor_source_ref": support_locator(
                predecessor_source_filename
            ),
            "predecessor_source_sha256": predecessor_source_sha,
            "replacement_source_ref": support_locator(
                replacement_source_filename
            ),
            "replacement_source_sha256": replacement_source_sha,
            "selector_version": "issue-register-gate-selector.v2",
            "publication_mode": "AUTHORIZED_GOVERNANCE_PUBLICATION",
            "authority_effect": "GOVERNED_ISSUE_SELECTION",
            "expected_g0_selector_cardinality": 0,
        },
        "candidate_derivation_contract_ref": derivation_contract_locator,
        "candidate_derivation_contract_sha256": derivation_contract_sha,
        "operation_constraint_by_step": operation_constraint_by_step,
        "authority_effect": "NONE",
    }
    closure_sha = corpus.add(
        "VALID-M0-PLANNING-BOOTSTRAP-INPUT-CLOSURE-01",
        closure_filename,
        "planning-bootstrap-input-closure-v1.schema.json",
        closure,
    )
    closure_locator = valid_locator(closure_filename)

    def add_repository_operation_receipt(
        *,
        step_id: str,
        semantic_grant_kind: str,
        payload_ref: str,
        payload_sha256: str,
        operation: str,
        target_scope: str,
        actor_id: str,
        receipt_filename: str,
        receipt_id: str,
        started_at: str,
        completed_at: str,
        result: str,
        artifact_schema: str | None = None,
        artifact_id: str | None = None,
        artifact_revision: int | None = None,
        role_id: str | None = None,
    ) -> tuple[dict[str, Any], str, str]:
        _, _, permission_ref, permission_sha = add_write_authorities(
            step_id=step_id,
            semantic_grant_kind=semantic_grant_kind,
            artifact_schema=artifact_schema,
            artifact_id=artifact_id,
            artifact_revision=artifact_revision,
            role_id=role_id,
            payload_ref=payload_ref,
            payload_sha256=payload_sha256,
            operation=operation,
            target_scope=target_scope,
            receipt_schema="ylx.repository-operation-receipt.v1",
            receipt_id=receipt_id,
            receipt_locator=valid_locator(receipt_filename),
            operation_class=(
                "ISSUE_WRITE"
                if semantic_grant_kind == "ISSUE_WRITE"
                else "REPOSITORY_WRITE"
            ),
            actor_id=actor_id,
            issued_at=started_at,
        )
        payload = {
            "schema": "ylx.repository-operation-receipt.v1",
            "receipt_id": receipt_id,
            "sink_id": receipt_sink_id,
            "permission_ref": permission_ref,
            "permission_sha256": permission_sha,
            "repository_locator": repository_locator,
            "actor_id": actor_id,
            "operation": operation,
            "target_scope": target_scope,
            "started_at": started_at,
            "completed_at": completed_at,
            "output_ref": payload_ref,
            "output_sha256": payload_sha256,
            "result": result,
            "fsync_result": "FILE_AND_PARENT_DIRECTORY_DURABLE",
            "issuer_id": receipt_issuer_id,
            "issued_at": completed_at,
            "signature_algorithm": "Ed25519",
            "signing_key_id": receipt_issuer_key["key_id"],
        }
        receipt = sign_closed_record(
            payload,
            private_key=receipt_issuer_key["private_key"],
            signature_domain=repository_receipt_domain,
        )
        digest = corpus.add(
            f"VALID-M0-REPOSITORY-OPERATION-RECEIPT-{step_id.upper()}-01",
            receipt_filename,
            "repository-operation-receipt-v1.schema.json",
            receipt,
        )
        return receipt, digest, valid_locator(receipt_filename)

    def add_planning_publication_receipt(
        *,
        step_id: str,
        artifact_schema: str,
        artifact_id: str,
        artifact_ref_value: str,
        artifact_sha256: str,
        byte_length: int,
        receipt_filename: str,
        receipt_id: str,
        actor_id: str,
        published_at: str,
    ) -> tuple[dict[str, Any], str, str]:
        publisher_ref, publisher_sha, permission_ref, permission_sha = (
            add_write_authorities(
                step_id=step_id,
                semantic_grant_kind="PUBLISHER",
                artifact_schema=artifact_schema,
                artifact_id=artifact_id,
                artifact_revision=1,
                role_id=None,
                payload_ref=artifact_ref_value,
                payload_sha256=artifact_sha256,
                operation="CREATE_IF_ABSENT",
                target_scope=artifact_ref_value,
                receipt_schema=(
                    "ylx.planning-bootstrap-publication-receipt.v1"
                ),
                receipt_id=receipt_id,
                receipt_locator=valid_locator(receipt_filename),
                operation_class="PLANNING_PUBLICATION",
                actor_id=actor_id,
                issued_at=moment(
                    int(
                        (
                            datetime.fromisoformat(
                                published_at.replace("Z", "+00:00")
                            )
                            - base_time
                        ).total_seconds()
                    )
                    - 1
                ),
            )
        )
        payload = {
            "schema": "ylx.planning-bootstrap-publication-receipt.v1",
            "receipt_id": receipt_id,
            "artifact_schema": artifact_schema,
            "artifact_id": artifact_id,
            "revision": 1,
            "actor_id": actor_id,
            "write_operation_authority_ref": publisher_ref,
            "write_operation_authority_sha256": publisher_sha,
            "repository_permission_ref": permission_ref,
            "repository_permission_sha256": permission_sha,
            "repository_locator": repository_locator,
            "operation": "CREATE_IF_ABSENT",
            "target_scope": artifact_ref_value,
            "artifact_ref": artifact_ref_value,
            "artifact_sha256": artifact_sha256,
            "byte_length": byte_length,
            "published_at": published_at,
            "create_result": "CREATED_EXACT",
            "fsync_result": "FILE_AND_PARENT_DIRECTORY_DURABLE",
            "terminal_sink_id": receipt_sink_id,
            "receipt_issuer_id": receipt_issuer_id,
            "signature_algorithm": "Ed25519",
            "signing_key_id": receipt_issuer_key["key_id"],
        }
        receipt = sign_closed_record(
            payload,
            private_key=receipt_issuer_key["private_key"],
            signature_domain=terminal_domain,
        )
        digest = corpus.add(
            f"VALID-M0-PLANNING-PUBLICATION-RECEIPT-{step_id.upper()}-01",
            receipt_filename,
            "planning-bootstrap-publication-receipt-v1.schema.json",
            receipt,
        )
        return receipt, digest, valid_locator(receipt_filename)

    def add_planning_readback_receipt(
        *,
        step_id: str,
        artifact_schema: str,
        artifact_id: str,
        artifact_ref_value: str,
        artifact_sha256: str,
        byte_length: int,
        publication_receipt_ref: str,
        publication_receipt_sha256: str,
        receipt_filename: str,
        receipt_id: str,
        reader_id: str,
        read_back_at: str,
    ) -> tuple[dict[str, Any], str, str]:
        readback_authority_ref, readback_authority_sha = add_readback_authority(
            step_id=step_id,
            artifact_schema=artifact_schema,
            artifact_id=artifact_id,
            payload_ref=artifact_ref_value,
            payload_sha256=artifact_sha256,
            target_scope=artifact_ref_value,
            receipt_schema="ylx.planning-bootstrap-readback-receipt.v1",
            receipt_id=receipt_id,
            receipt_locator=valid_locator(receipt_filename),
            reader_id=reader_id,
            issued_at=moment(
                int(
                    (
                        datetime.fromisoformat(
                            read_back_at.replace("Z", "+00:00")
                        )
                        - base_time
                    ).total_seconds()
                )
                - 1
            ),
        )
        payload = {
            "schema": "ylx.planning-bootstrap-readback-receipt.v1",
            "receipt_id": receipt_id,
            "publication_receipt_ref": publication_receipt_ref,
            "publication_receipt_sha256": publication_receipt_sha256,
            "reader_id": reader_id,
            "readback_authority_ref": readback_authority_ref,
            "readback_authority_sha256": readback_authority_sha,
            "artifact_ref": artifact_ref_value,
            "artifact_sha256": artifact_sha256,
            "byte_length": byte_length,
            "read_back_at": read_back_at,
            "result": "MATCH",
            "terminal_sink_id": receipt_sink_id,
            "receipt_issuer_id": receipt_issuer_id,
            "signature_algorithm": "Ed25519",
            "signing_key_id": receipt_issuer_key["key_id"],
        }
        receipt = sign_closed_record(
            payload,
            private_key=receipt_issuer_key["private_key"],
            signature_domain=terminal_domain,
        )
        digest = corpus.add(
            f"VALID-M0-PLANNING-READBACK-RECEIPT-{step_id.upper()}-01",
            receipt_filename,
            "planning-bootstrap-readback-receipt-v1.schema.json",
            receipt,
        )
        return receipt, digest, valid_locator(receipt_filename)

    def add_approval_publication_receipt(
        *,
        role_id: str,
        approval_id: str,
        approval_ref: str,
        approval_sha256: str,
        byte_length: int,
        actor_id: str,
        published_at: str,
    ) -> tuple[dict[str, Any], str, str]:
        step_id = f"approval-{role_id}-import"
        receipt_filename = (
            f"m0-planning-bundle-approval-{role_id}-publication-receipt.json"
        )
        receipt_id = (
            f"fixture-m0-planning-bundle-approval-{role_id}-"
            "publication-receipt"
        )
        importer_ref, importer_sha, permission_ref, permission_sha = (
            add_write_authorities(
                step_id=step_id,
                semantic_grant_kind="IMPORTER",
                artifact_schema="ylx.planning-bundle-approval.v1",
                artifact_id=approval_id,
                artifact_revision=None,
                role_id=role_id,
                payload_ref=approval_ref,
                payload_sha256=approval_sha256,
                operation="IMPORT_EXACT",
                target_scope=approval_ref,
                receipt_schema="ylx.planning-approval-publication-receipt.v1",
                receipt_id=receipt_id,
                receipt_locator=valid_locator(receipt_filename),
                operation_class="APPROVAL_IMPORT",
                actor_id=actor_id,
                issued_at=moment(
                    int(
                        (
                            datetime.fromisoformat(
                                published_at.replace("Z", "+00:00")
                            )
                            - base_time
                        ).total_seconds()
                    )
                    - 1
                ),
            )
        )
        payload = {
            "schema": "ylx.planning-approval-publication-receipt.v1",
            "receipt_id": receipt_id,
            "approval_id": approval_id,
            "role_id": role_id,
            "approval_ref": approval_ref,
            "approval_sha256": approval_sha256,
            "actor_id": actor_id,
            "write_operation_authority_ref": importer_ref,
            "write_operation_authority_sha256": importer_sha,
            "repository_permission_ref": permission_ref,
            "repository_permission_sha256": permission_sha,
            "repository_locator": repository_locator,
            "target_scope": approval_ref,
            "byte_length": byte_length,
            "published_at": published_at,
            "create_result": "CREATED_EXACT",
            "fsync_result": "FILE_AND_PARENT_DIRECTORY_DURABLE",
            "terminal_sink_id": receipt_sink_id,
            "receipt_issuer_id": receipt_issuer_id,
            "signature_algorithm": "Ed25519",
            "signing_key_id": receipt_issuer_key["key_id"],
        }
        receipt = sign_closed_record(
            payload,
            private_key=receipt_issuer_key["private_key"],
            signature_domain=terminal_domain,
        )
        digest = corpus.add(
            (
                "VALID-M0-PLANNING-APPROVAL-PUBLICATION-RECEIPT-"
                f"{role_id.upper()}-01"
            ),
            receipt_filename,
            "planning-approval-publication-receipt-v1.schema.json",
            receipt,
        )
        return receipt, digest, valid_locator(receipt_filename)

    def add_approval_readback_receipt(
        *,
        role_id: str,
        approval_id: str,
        approval_ref: str,
        approval_sha256: str,
        byte_length: int,
        publication_receipt_ref: str,
        publication_receipt_sha256: str,
        reader_id: str,
        read_back_at: str,
    ) -> tuple[dict[str, Any], str, str]:
        step_id = f"approval-{role_id}-readback"
        receipt_filename = (
            f"m0-planning-bundle-approval-{role_id}-readback-receipt.json"
        )
        receipt_id = (
            f"fixture-m0-planning-bundle-approval-{role_id}-readback-receipt"
        )
        readback_authority_ref, readback_authority_sha = add_readback_authority(
            step_id=step_id,
            artifact_schema="ylx.planning-bundle-approval.v1",
            artifact_id=approval_id,
            payload_ref=approval_ref,
            payload_sha256=approval_sha256,
            target_scope=approval_ref,
            receipt_schema="ylx.planning-approval-readback-receipt.v1",
            receipt_id=receipt_id,
            receipt_locator=valid_locator(receipt_filename),
            reader_id=reader_id,
            issued_at=moment(
                int(
                    (
                        datetime.fromisoformat(
                            read_back_at.replace("Z", "+00:00")
                        )
                        - base_time
                    ).total_seconds()
                )
                - 1
            ),
        )
        payload = {
            "schema": "ylx.planning-approval-readback-receipt.v1",
            "receipt_id": receipt_id,
            "publication_receipt_ref": publication_receipt_ref,
            "publication_receipt_sha256": publication_receipt_sha256,
            "approval_id": approval_id,
            "role_id": role_id,
            "approval_ref": approval_ref,
            "approval_sha256": approval_sha256,
            "reader_id": reader_id,
            "readback_authority_ref": readback_authority_ref,
            "readback_authority_sha256": readback_authority_sha,
            "byte_length": byte_length,
            "read_back_at": read_back_at,
            "result": "MATCH",
            "terminal_sink_id": receipt_sink_id,
            "receipt_issuer_id": receipt_issuer_id,
            "signature_algorithm": "Ed25519",
            "signing_key_id": receipt_issuer_key["key_id"],
        }
        receipt = sign_closed_record(
            payload,
            private_key=receipt_issuer_key["private_key"],
            signature_domain=terminal_domain,
        )
        digest = corpus.add(
            (
                "VALID-M0-PLANNING-APPROVAL-READBACK-RECEIPT-"
                f"{role_id.upper()}-01"
            ),
            receipt_filename,
            "planning-approval-readback-receipt-v1.schema.json",
            receipt,
        )
        return receipt, digest, valid_locator(receipt_filename)

    closure_readback_filename = (
        "m0-planning-bootstrap-input-closure-readback-receipt.json"
    )
    closure_readback_id = (
        "fixture-m0-planning-bootstrap-input-closure-readback-receipt"
    )
    closure_reader_id = "fixture-m0-input-closure-reader"
    closure_readback_authority_ref, closure_readback_authority_sha = (
        add_readback_authority(
            step_id="input-closure-readback",
            artifact_schema=closure["schema"],
            artifact_id=closure["closure_id"],
            payload_ref=closure_locator,
            payload_sha256=closure_sha,
            target_scope=closure_locator,
            receipt_schema=(
                "ylx.planning-bootstrap-input-closure-readback-receipt.v1"
            ),
            receipt_id=closure_readback_id,
            receipt_locator=valid_locator(closure_readback_filename),
            reader_id=closure_reader_id,
            issued_at=moment(1),
        )
    )
    closure_readback_payload = {
        "schema": (
            "ylx.planning-bootstrap-input-closure-readback-receipt.v1"
        ),
        "receipt_id": closure_readback_id,
        "bootstrap_attempt_id": bootstrap_attempt_id,
        "closure_ref": closure_locator,
        "closure_sha256": closure_sha,
        "byte_length": corpus.byte_lengths[f"valid/{closure_filename}"],
        "reader_id": closure_reader_id,
        "readback_authority_ref": closure_readback_authority_ref,
        "readback_authority_sha256": closure_readback_authority_sha,
        "read_back_at": moment(2),
        "result": "MATCH",
        "terminal_sink_id": receipt_sink_id,
        "receipt_issuer_id": receipt_issuer_id,
        "signature_algorithm": "Ed25519",
        "signing_key_id": receipt_issuer_key["key_id"],
    }
    closure_readback = sign_closed_record(
        closure_readback_payload,
        private_key=receipt_issuer_key["private_key"],
        signature_domain=terminal_domain,
    )
    closure_readback_sha = corpus.add(
        "VALID-M0-PLANNING-BOOTSTRAP-INPUT-CLOSURE-READBACK-01",
        closure_readback_filename,
        "planning-bootstrap-input-closure-readback-receipt-v1.schema.json",
        closure_readback,
    )
    closure_readback_locator = valid_locator(closure_readback_filename)

    closure_source_ref = {
        "ref_id": closure["closure_id"],
        "authority_kind": "planning-bootstrap-input-closure",
        "locator": closure_locator,
        "sha256": closure_sha,
    }
    owner = copy.deepcopy(roots["owner_assignment"])
    owner.pop("approvals", None)
    owner.update(
        {
            "artifact_id": "M0-GOV-01-governed-owner-assignment",
            "revision": 1,
            "predecessor_sha256": None,
            "generated_at": moment(3),
            "source_refs": [
                copy.deepcopy(migration_source_ref),
                copy.deepcopy(g0_source_ref),
                copy.deepcopy(closure_source_ref),
                copy.deepcopy(bootstrap_authority_source_ref),
            ],
            "overall_status": "ACCEPTED",
            "blockers": [],
        }
    )
    build_assignment = next(
        assignment
        for assignment in owner["assignments"]
        if assignment["role_id"] == "build-platform-owner"
    )
    build_assignment["accountable_party_id"] = "fixture-ga-operator-person"
    build_assignment["executor_id"] = "fixture-ga-operator-person"
    if set(owner) != set(owner_output_fields):
        raise AssertionError(
            "governed owner candidate fields differ from derivation contract"
        )
    owner_locator = valid_locator("governed-owner-assignment-root.json")
    owner_sha = sha(canonical_bytes(owner))

    phase_a_filename = (
        "m0-planning-bootstrap-owner-candidate-derivation.json"
    )
    phase_a = {
        "schema": "ylx.planning-bootstrap-owner-candidate-derivation.v1",
        "derivation_id": "fixture-m0-owner-candidate-derivation",
        "bootstrap_attempt_id": bootstrap_attempt_id,
        "input_closure_ref": closure_locator,
        "input_closure_sha256": closure_sha,
        "derivation_contract_ref": derivation_contract_locator,
        "derivation_contract_sha256": derivation_contract_sha,
        "owner_payload_sha256": owner_sha,
        "derived_at": moment(3),
        "authority_effect": "NONE",
    }
    phase_a_sha = corpus.add(
        "VALID-M0-PLANNING-BOOTSTRAP-PHASE-A-DERIVATION-01",
        phase_a_filename,
        "planning-bootstrap-owner-candidate-derivation-v1.schema.json",
        phase_a,
    )
    phase_a_locator = valid_locator(phase_a_filename)
    (
        phase_a_operation_receipt,
        phase_a_operation_receipt_sha,
        phase_a_operation_receipt_locator,
    ) = add_repository_operation_receipt(
        step_id="phase-a-derivation-record-write",
        semantic_grant_kind="PUBLISHER",
        payload_ref=phase_a_locator,
        payload_sha256=phase_a_sha,
        operation="CREATE_IF_ABSENT",
        target_scope=phase_a_locator,
        actor_id="fixture-m0-bootstrap-derivation-recorder",
        receipt_filename=(
            "m0-planning-bootstrap-owner-candidate-derivation-"
            "operation-receipt.json"
        ),
        receipt_id=(
            "fixture-m0-owner-candidate-derivation-operation-receipt"
        ),
        started_at=moment(4),
        completed_at=moment(5),
        result="CREATED_EXACT",
        artifact_schema=phase_a["schema"],
        artifact_id=phase_a["derivation_id"],
        artifact_revision=1,
    )

    stored_owner_sha = corpus.replace(
        "governed-owner-assignment-root.json", owner
    )
    if stored_owner_sha != owner_sha:
        raise AssertionError("governed owner candidate digest drift")
    owner_ref = artifact_ref(
        owner["artifact_id"],
        owner["schema"],
        owner_sha,
        owner_locator,
        owner["revision"],
    )
    (
        owner_publication,
        owner_publication_sha,
        owner_publication_locator,
    ) = add_planning_publication_receipt(
        step_id="owner-root-write",
        artifact_schema=owner["schema"],
        artifact_id=owner["artifact_id"],
        artifact_ref_value=owner_locator,
        artifact_sha256=owner_sha,
        byte_length=corpus.byte_lengths["valid/governed-owner-assignment-root.json"],
        receipt_filename="m0-planning-bootstrap-owner-publication-receipt.json",
        receipt_id="fixture-m0-owner-publication-receipt",
        actor_id="fixture-m0-owner-root-publisher",
        published_at=moment(8),
    )
    owner_readback, owner_readback_sha, owner_readback_locator = (
        add_planning_readback_receipt(
            step_id="owner-root-readback",
            artifact_schema=owner["schema"],
            artifact_id=owner["artifact_id"],
            artifact_ref_value=owner_locator,
            artifact_sha256=owner_sha,
            byte_length=corpus.byte_lengths[
                "valid/governed-owner-assignment-root.json"
            ],
            publication_receipt_ref=owner_publication_locator,
            publication_receipt_sha256=owner_publication_sha,
            receipt_filename="m0-planning-bootstrap-owner-readback-receipt.json",
            receipt_id="fixture-m0-owner-readback-receipt",
            reader_id="fixture-m0-owner-root-reader",
            read_back_at=moment(10),
        )
    )

    issue_approvals = [
        {
            "role_id": role_id,
            "principal_id": f"fixture-{role_id}-person",
            "decision": "APPROVED",
            "approved_at": moment(11 + index),
            "assignment_ref": {
                "authority_id": owner["artifact_id"],
                "revision": owner["revision"],
                "artifact_path": owner_locator,
                "artifact_sha256": owner_sha,
                "verified_at": moment(11 + index),
            },
        }
        for index, role_id in enumerate(
            ("release-owner", "security-owner")
        )
    ]
    successor_head_filename = "m0-g0-issue-register-successor-head.json"
    successor_head = {
        "schema": "ylx.issue-register-head.v1",
        "issue_register_revision": 2,
        "predecessor_revision": predecessor_head["issue_register_revision"],
        "predecessor_head_artifact_sha256": predecessor_head_sha,
        "source_artifact_path": support_locator(replacement_source_filename),
        "issue_register_sha256": replacement_source_sha,
        "archived_source_path": support_locator(replacement_archive_filename),
        "archived_source_sha256": replacement_archive_sha,
        "selector_version": "issue-register-gate-selector.v2",
        "overview_cardinality": len(replacement_slices),
        "issue_slices_by_id": replacement_slices,
        "published_at": moment(18),
        "publisher_role_slot": "release-owner",
        "approvals": issue_approvals,
        "publication_mode": "AUTHORIZED_GOVERNANCE_PUBLICATION",
        "authority_effect": "GOVERNED_ISSUE_SELECTION",
        "policy_authority_ref": copy.deepcopy(g0_state["ratification_ref"]),
        "publisher_assignment_ref": copy.deepcopy(owner_ref),
    }
    successor_head_sha = corpus.add(
        "VALID-M0-G0-ISSUE-REGISTER-SUCCESSOR-HEAD-01",
        successor_head_filename,
        "issue-register-head-v1.schema.json",
        successor_head,
    )
    successor_head_locator = valid_locator(successor_head_filename)

    (
        source_operation_receipt,
        source_operation_receipt_sha,
        source_operation_receipt_locator,
    ) = add_repository_operation_receipt(
        step_id="issue-source-replacement-write",
        semantic_grant_kind="ISSUE_WRITE",
        payload_ref=support_locator(replacement_source_filename),
        payload_sha256=replacement_source_sha,
        operation="REPLACE_LIVE_SOURCE_EXACT",
        target_scope=support_locator(replacement_source_filename),
        actor_id="fixture-m0-issue-register-publisher",
        receipt_filename="m0-g0-issue-register-source-operation-receipt.json",
        receipt_id="fixture-m0-g0-issue-source-operation-receipt",
        started_at=moment(13),
        completed_at=moment(14),
        result="UPDATED_BY_CAS_EXACT",
    )
    (
        archive_operation_receipt,
        archive_operation_receipt_sha,
        archive_operation_receipt_locator,
    ) = add_repository_operation_receipt(
        step_id="issue-archive-write",
        semantic_grant_kind="ISSUE_WRITE",
        payload_ref=support_locator(replacement_archive_filename),
        payload_sha256=replacement_archive_sha,
        operation="CREATE_IMMUTABLE_ARCHIVE",
        target_scope=support_locator(replacement_archive_filename),
        actor_id="fixture-m0-issue-register-publisher",
        receipt_filename="m0-g0-issue-register-archive-operation-receipt.json",
        receipt_id="fixture-m0-g0-issue-archive-operation-receipt",
        started_at=moment(15),
        completed_at=moment(16),
        result="CREATED_EXACT",
    )
    (
        head_operation_receipt,
        head_operation_receipt_sha,
        head_operation_receipt_locator,
    ) = add_repository_operation_receipt(
        step_id="issue-head-write",
        semantic_grant_kind="ISSUE_WRITE",
        payload_ref=successor_head_locator,
        payload_sha256=successor_head_sha,
        operation="PUBLISH_DIRECT_SUCCESSOR_HEAD",
        target_scope=successor_head_locator,
        actor_id="fixture-m0-issue-register-publisher",
        receipt_filename="m0-g0-issue-register-head-operation-receipt.json",
        receipt_id="fixture-m0-g0-issue-head-operation-receipt",
        started_at=moment(17),
        completed_at=moment(18),
        result="CREATED_EXACT",
    )

    transition_tuple = {
        "source_ref": support_locator(replacement_source_filename),
        "source_sha256": replacement_source_sha,
        "archive_ref": support_locator(replacement_archive_filename),
        "archive_sha256": replacement_archive_sha,
        "successor_head_ref": successor_head_locator,
        "successor_head_sha256": successor_head_sha,
    }
    transition_tuple_sha = sha(canonical_bytes(transition_tuple))
    transition_step_id = "issue-transition-readback"
    transition_operation_id = f"fixture-m0-operation-{transition_step_id}"
    transition_receipt_filename = (
        "m0-g0-issue-register-transition-readback-receipt.json"
    )
    transition_receipt_id = (
        "fixture-m0-g0-issue-register-transition-readback-receipt"
    )
    transition_sink_ref, transition_sink_sha = add_terminal_sink_grant(
        step_id=transition_step_id,
        operation_instance_id=transition_operation_id,
        payload_sha256=transition_tuple_sha,
        operation="READ_EXACT_TRANSITION_TUPLE",
        target_scope="fixture://m0-bootstrap/issue-transition-tuple",
        receipt_schema=(
            "ylx.g0-issue-register-transition-readback-receipt.v1"
        ),
        receipt_id=transition_receipt_id,
        receipt_locator=valid_locator(transition_receipt_filename),
        operation_class="ISSUE_TRANSITION_READBACK",
        issued_at=moment(19),
    )
    transition_reader_id = "fixture-m0-issue-transition-reader"
    transition_readback_authority_ref, transition_readback_authority_sha = (
        add_operation_grant(
            step_id=transition_step_id,
            grant_kind="ISSUE_READBACK",
            grant={
                "step_id": transition_step_id,
                "operation_instance_id": transition_operation_id,
                "reader_id": transition_reader_id,
                **transition_tuple,
                "payload_sha256": transition_tuple_sha,
                "repository_locator": repository_locator,
                "operation": "READ_EXACT_TRANSITION_TUPLE",
                "target_scope": "fixture://m0-bootstrap/issue-transition-tuple",
                "terminal_sink_grant_ref": transition_sink_ref,
                "terminal_sink_grant_sha256": transition_sink_sha,
            },
            issued_at=moment(19),
        )
    )
    transition_readback_payload = {
        "schema": "ylx.g0-issue-register-transition-readback-receipt.v1",
        "receipt_id": transition_receipt_id,
        "source_ref": transition_tuple["source_ref"],
        "source_sha256": replacement_source_sha,
        "source_byte_length": len(replacement_source_raw),
        "archive_ref": transition_tuple["archive_ref"],
        "archive_sha256": replacement_archive_sha,
        "archive_byte_length": len(replacement_source_raw),
        "successor_head_ref": successor_head_locator,
        "successor_head_sha256": successor_head_sha,
        "successor_head_byte_length": corpus.byte_lengths[
            f"valid/{successor_head_filename}"
        ],
        "reader_id": transition_reader_id,
        "readback_authority_ref": transition_readback_authority_ref,
        "readback_authority_sha256": transition_readback_authority_sha,
        "read_back_at": moment(20),
        "result": "MATCH",
        "terminal_sink_id": receipt_sink_id,
        "receipt_issuer_id": receipt_issuer_id,
        "signature_algorithm": "Ed25519",
        "signing_key_id": receipt_issuer_key["key_id"],
    }
    transition_readback = sign_closed_record(
        transition_readback_payload,
        private_key=receipt_issuer_key["private_key"],
        signature_domain=(
            "YLX-G0-ISSUE-REGISTER-TRANSITION-READBACK-RECEIPT-V1"
        ),
    )
    transition_readback_sha = corpus.add(
        "VALID-M0-G0-ISSUE-REGISTER-TRANSITION-READBACK-01",
        transition_receipt_filename,
        "g0-issue-register-transition-readback-receipt-v1.schema.json",
        transition_readback,
    )
    transition_readback_locator = valid_locator(transition_receipt_filename)

    issue_approval_sha_by_role = {
        value["role_id"]: sha(canonical_bytes(value))
        for value in issue_approvals
    }
    reconciliation_filename = (
        "m0-g0-issue-register-reconciliation-receipt.json"
    )
    reconciliation_locator = valid_locator(reconciliation_filename)
    reconciliation_payload = {
        "schema": "ylx.g0-issue-register-reconciliation-receipt.v1",
        "reconciliation_id": "fixture-m0-g0-issue-register-reconciliation",
        "bootstrap_attempt_id": bootstrap_attempt_id,
        "g0_event_ref": g0_state["ratification_ref"]["artifact_path"],
        "g0_event_sha256": g0_state["ratification_sha"],
        "owner_payload_ref": owner_locator,
        "owner_payload_sha256": owner_sha,
        "owner_publication_receipt_ref": owner_publication_locator,
        "owner_publication_receipt_sha256": owner_publication_sha,
        "owner_readback_receipt_ref": owner_readback_locator,
        "owner_readback_receipt_sha256": owner_readback_sha,
        "predecessor_head_ref": predecessor_head_locator,
        "predecessor_head_sha256": predecessor_head_sha,
        "predecessor_source_ref": support_locator(predecessor_source_filename),
        "predecessor_source_sha256": predecessor_source_sha,
        "replacement_source_ref": support_locator(replacement_source_filename),
        "replacement_source_sha256": replacement_source_sha,
        "source_operation_receipt_ref": source_operation_receipt_locator,
        "source_operation_receipt_sha256": source_operation_receipt_sha,
        "archive_ref": support_locator(replacement_archive_filename),
        "archive_sha256": replacement_archive_sha,
        "archive_operation_receipt_ref": archive_operation_receipt_locator,
        "archive_operation_receipt_sha256": archive_operation_receipt_sha,
        "successor_head_ref": successor_head_locator,
        "successor_head_sha256": successor_head_sha,
        "head_operation_receipt_ref": head_operation_receipt_locator,
        "head_operation_receipt_sha256": head_operation_receipt_sha,
        "transition_readback_receipt_ref": transition_readback_locator,
        "transition_readback_receipt_sha256": transition_readback_sha,
        "publisher_assignment_ref": owner_locator,
        "publisher_assignment_sha256": owner_sha,
        "issue_approval_sha256_by_role": issue_approval_sha_by_role,
        "g0_selector_cardinality": 0,
        "reconciled_at": moment(21),
        "result": "RECONCILED",
        "terminal_sink_id": receipt_sink_id,
        "receipt_issuer_id": receipt_issuer_id,
        "signature_algorithm": "Ed25519",
        "signing_key_id": receipt_issuer_key["key_id"],
    }
    reconciliation = sign_closed_record(
        reconciliation_payload,
        private_key=receipt_issuer_key["private_key"],
        signature_domain=(
            "YLX-G0-ISSUE-REGISTER-RECONCILIATION-RECEIPT-V1"
        ),
    )
    add_terminal_sink_grant(
        step_id="issue-reconciliation",
        operation_instance_id="fixture-m0-operation-issue-reconciliation",
        payload_sha256=reconciliation["signed_payload_sha256"],
        operation="EMIT_RECONCILIATION",
        target_scope=reconciliation_locator,
        receipt_schema=reconciliation["schema"],
        receipt_id=reconciliation["reconciliation_id"],
        receipt_locator=reconciliation_locator,
        operation_class="ISSUE_RECONCILIATION",
        issued_at=moment(20),
    )
    reconciliation_sha = corpus.add(
        "VALID-M0-G0-ISSUE-REGISTER-RECONCILIATION-01",
        reconciliation_filename,
        "g0-issue-register-reconciliation-receipt-v1.schema.json",
        reconciliation,
    )
    reconciliation_source_ref = {
        "ref_id": reconciliation["reconciliation_id"],
        "authority_kind": "g0-issue-register-reconciliation",
        "locator": reconciliation_locator,
        "sha256": reconciliation_sha,
    }

    phase_b_source_refs = [
        copy.deepcopy(migration_source_ref),
        copy.deepcopy(g0_source_ref),
        copy.deepcopy(closure_source_ref),
        copy.deepcopy(reconciliation_source_ref),
    ]

    calendar = copy.deepcopy(roots["resource_calendar"])
    calendar.pop("approvals", None)
    calendar.update(
        {
            "artifact_id": "M0-GOV-01-governed-resource-calendar",
            "revision": 1,
            "predecessor_sha256": None,
            "generated_at": moment(22),
            "source_refs": copy.deepcopy(phase_b_source_refs),
            "overall_status": "ACCEPTED",
            "blockers": [],
        }
    )
    if set(calendar) != set(calendar_output_fields):
        raise AssertionError(
            "governed calendar candidate fields differ from derivation contract"
        )
    calendar_locator = valid_locator("governed-resource-calendar-root.json")
    calendar_sha = corpus.replace(
        "governed-resource-calendar-root.json", calendar
    )
    calendar_ref = artifact_ref(
        calendar["artifact_id"],
        calendar["schema"],
        calendar_sha,
        calendar_locator,
        calendar["revision"],
    )

    wbs = copy.deepcopy(roots["delivery_wbs"])
    wbs.pop("approvals", None)
    wbs.update(
        {
            "artifact_id": "M0-GOV-01-governed-delivery-wbs",
            "revision": 1,
            "predecessor_sha256": None,
            "generated_at": moment(22),
            "source_refs": copy.deepcopy(phase_b_source_refs),
            "overall_status": "ACCEPTED",
            "active_blocker_ids": [],
            "active_blocker_coverage": {},
            "blockers": [],
        }
    )
    for node in wbs["nodes"]:
        for role_field in (
            "accountable_owner_ref",
            "executor_ref",
            "reviewer_ref",
        ):
            if role_field not in node:
                continue
            node[role_field]["owner_assignment_ref"] = copy.deepcopy(owner_ref)
            if node[role_field]["role_id"] == "build-platform-owner":
                node[role_field]["principal_id"] = "fixture-ga-operator-person"
    if set(wbs) != set(wbs_output_fields):
        raise AssertionError(
            "governed WBS candidate fields differ from derivation contract"
        )
    wbs_locator = valid_locator("governed-delivery-wbs-root.json")
    wbs_sha = corpus.replace("governed-delivery-wbs-root.json", wbs)
    wbs_ref = artifact_ref(
        wbs["artifact_id"],
        wbs["schema"],
        wbs_sha,
        wbs_locator,
        wbs["revision"],
    )

    forecast = copy.deepcopy(roots["forecast_snapshot"])
    forecast.pop("approvals", None)
    forecast.update(
        {
            "artifact_id": "M0-GOV-01-governed-forecast-snapshot",
            "revision": 1,
            "predecessor_sha256": None,
            "generated_at": moment(22),
            "source_refs": copy.deepcopy(phase_b_source_refs),
            "overall_status": "ACCEPTED",
            "owner_assignment_sha256": owner_sha,
            "resource_calendar_sha256": calendar_sha,
            "delivery_wbs_sha256": wbs_sha,
            "blockers": [],
        }
    )
    if set(forecast) != set(forecast_output_fields):
        raise AssertionError(
            "governed forecast candidate fields differ from derivation contract"
        )
    forecast_locator = valid_locator("governed-forecast-snapshot-root.json")
    forecast_sha = corpus.replace(
        "governed-forecast-snapshot-root.json", forecast
    )
    forecast_ref = artifact_ref(
        forecast["artifact_id"],
        forecast["schema"],
        forecast_sha,
        forecast_locator,
        forecast["revision"],
    )

    artifacts = {
        "owner_assignment": owner_ref,
        "resource_calendar": calendar_ref,
        "delivery_wbs": wbs_ref,
        "forecast_snapshot": forecast_ref,
    }
    old_bundle = roots["delivery_planning_bundle"]
    approval_subject = {
        "schema": "ylx.delivery-planning-bundle.v2",
        "artifact_id": "M0-GOV-01-governed-delivery-planning-bundle",
        "revision": 1,
        "predecessor_sha256": None,
        "source_refs": copy.deepcopy(phase_b_source_refs),
        "artifact_metadata": copy.deepcopy(old_bundle["artifact_metadata"]),
        "planning_gate": old_bundle["planning_gate"],
        "detail_horizon": copy.deepcopy(old_bundle["detail_horizon"]),
        "registry_binding": copy.deepcopy(old_bundle["registry_binding"]),
        "artifacts": artifacts,
        "bundle_kind": old_bundle["bundle_kind"],
        "final_actual_variance_reconciliation": copy.deepcopy(
            old_bundle["final_actual_variance_reconciliation"]
        ),
    }
    if list(approval_subject) != subject_fields:
        raise AssertionError("wrapperless S field order drift")
    approval_subject_filename = "m0-planning-approval-subject.json"
    approval_subject_sha = corpus.add(
        "VALID-M0-PLANNING-APPROVAL-SUBJECT-01",
        approval_subject_filename,
        "planning-approval-subject-v1.schema.json",
        approval_subject,
    )
    approval_subject_locator = valid_locator(approval_subject_filename)

    candidate_sha_by_kind = {
        "resource_calendar": calendar_sha,
        "delivery_wbs": wbs_sha,
        "forecast_snapshot": forecast_sha,
    }
    phase_b_filename = (
        "m0-planning-bootstrap-post-reconciliation-candidate-derivation.json"
    )
    phase_b = {
        "schema": (
            "ylx.planning-bootstrap-post-reconciliation-candidate-derivation.v1"
        ),
        "derivation_id": (
            "fixture-m0-post-reconciliation-candidate-derivation"
        ),
        "bootstrap_attempt_id": bootstrap_attempt_id,
        "input_closure_ref": closure_locator,
        "input_closure_sha256": closure_sha,
        "derivation_contract_ref": derivation_contract_locator,
        "derivation_contract_sha256": derivation_contract_sha,
        "owner_payload_ref": owner_locator,
        "owner_payload_sha256": owner_sha,
        "owner_publication_receipt_ref": owner_publication_locator,
        "owner_publication_receipt_sha256": owner_publication_sha,
        "owner_readback_receipt_ref": owner_readback_locator,
        "owner_readback_receipt_sha256": owner_readback_sha,
        "g0_issue_reconciliation_ref": reconciliation_locator,
        "g0_issue_reconciliation_sha256": reconciliation_sha,
        "candidate_sha256_by_artifact_kind": candidate_sha_by_kind,
        "bundle_subject_projection_sha256": approval_subject_sha,
        "planning_approval_subject_ref": approval_subject_locator,
        "planning_approval_subject_sha256": approval_subject_sha,
        "derived_at": moment(22),
        "authority_effect": "NONE",
    }
    phase_b_sha = corpus.add(
        "VALID-M0-PLANNING-BOOTSTRAP-PHASE-B-DERIVATION-01",
        phase_b_filename,
        "planning-bootstrap-post-reconciliation-candidate-derivation-v1.schema.json",
        phase_b,
    )
    phase_b_locator = valid_locator(phase_b_filename)
    (
        phase_b_operation_receipt,
        phase_b_operation_receipt_sha,
        phase_b_operation_receipt_locator,
    ) = add_repository_operation_receipt(
        step_id="phase-b-derivation-record-write",
        semantic_grant_kind="PUBLISHER",
        payload_ref=phase_b_locator,
        payload_sha256=phase_b_sha,
        operation="CREATE_IF_ABSENT",
        target_scope=phase_b_locator,
        actor_id="fixture-m0-bootstrap-derivation-recorder",
        receipt_filename=(
            "m0-planning-bootstrap-post-reconciliation-candidate-"
            "derivation-operation-receipt.json"
        ),
        receipt_id=(
            "fixture-m0-post-reconciliation-candidate-derivation-"
            "operation-receipt"
        ),
        started_at=moment(23),
        completed_at=moment(24),
        result="CREATED_EXACT",
        artifact_schema=phase_b["schema"],
        artifact_id=phase_b["derivation_id"],
        artifact_revision=1,
    )

    (
        subject_publication,
        subject_publication_sha,
        subject_publication_locator,
    ) = add_planning_publication_receipt(
        step_id="approval-subject-write",
        artifact_schema="ylx.planning-approval-subject.v1",
        artifact_id=approval_subject["artifact_id"],
        artifact_ref_value=approval_subject_locator,
        artifact_sha256=approval_subject_sha,
        byte_length=corpus.byte_lengths[f"valid/{approval_subject_filename}"],
        receipt_filename=(
            "m0-planning-approval-subject-publication-receipt.json"
        ),
        receipt_id="fixture-m0-planning-approval-subject-publication-receipt",
        actor_id="fixture-m0-planning-subject-publisher",
        published_at=moment(25),
    )
    subject_readback, subject_readback_sha, subject_readback_locator = (
        add_planning_readback_receipt(
            step_id="approval-subject-readback",
            artifact_schema="ylx.planning-approval-subject.v1",
            artifact_id=approval_subject["artifact_id"],
            artifact_ref_value=approval_subject_locator,
            artifact_sha256=approval_subject_sha,
            byte_length=corpus.byte_lengths[
                f"valid/{approval_subject_filename}"
            ],
            publication_receipt_ref=subject_publication_locator,
            publication_receipt_sha256=subject_publication_sha,
            receipt_filename=(
                "m0-planning-approval-subject-readback-receipt.json"
            ),
            receipt_id="fixture-m0-planning-approval-subject-readback-receipt",
            reader_id="fixture-m0-planning-subject-reader",
            read_back_at=moment(27),
        )
    )

    approval_person_by_role = {
        "release-owner": "fixture-release-owner-person",
        "build-platform-owner": "fixture-ga-operator-person",
        "qa-evidence-owner": "fixture-qa-evidence-owner-person",
    }
    shared_people_by_role = {
        "release-owner": "fixture-release-owner-person",
        "contract-owner": "fixture-contract-owner-person",
        "security-owner": "fixture-security-owner-person",
        "qa-evidence-owner": "fixture-qa-evidence-owner-person",
        "build-platform-owner": "fixture-ga-operator-person",
    }
    shared_key_by_role = {
        role_id: shared_role_key_material(role_id)
        for role_id in shared_people_by_role
    }
    shared_assignment_by_role = {
        role_id: {
            "schema": "ylx.role-signing-key-assignment.v1",
            "assignment_id": f"fixture-signing-assignment-{role_id}-r1",
            "revision": 1,
            "predecessor_assignment_sha256": None,
            "role_slot": role_id,
            "person_id": person_id,
            "natural_person_identity_sha256": sha(f"identity:{person_id}"),
            "signing_key_fingerprint": shared_key_by_role[role_id][
                "fingerprint_sha256"
            ],
            "effective_from": VALID_FROM,
            "not_after": NOT_AFTER,
            "assignment_status": "ACTIVE",
            "is_delegate": False,
            "delegation_approval_ref": None,
            "identity_authority_ref": authority("fixture-identity-authority"),
            "published_at": "2026-05-15T00:00:00Z",
            "approvals": [
                approval("contract-owner"),
                approval("security-owner"),
            ],
        }
        for role_id, person_id in shared_people_by_role.items()
    }
    shared_assignment_sha_by_role = {
        role_id: sha(canonical_bytes(value))
        for role_id, value in shared_assignment_by_role.items()
    }
    shared_key_head = {
        "schema": "ylx.signing-key-validity-revocation-head.v1",
        "head_id": "fixture-signing-key-head-r1",
        "revision": 1,
        "predecessor_head_sha256": None,
        "effective_at": "2026-05-31T00:00:00Z",
        "keys_by_fingerprint": {
            shared_key_by_role[role_id]["fingerprint_sha256"]: {
                "key_id": shared_key_by_role[role_id]["key_id"],
                "person_id": person_id,
                "public_key_base64": shared_key_by_role[role_id][
                    "public_key_base64"
                ],
                "valid_from": VALID_FROM,
                "not_after": NOT_AFTER,
                "status": "VALID",
                "revocation_or_compromise_effective_at": None,
                "reason": None,
            }
            for role_id, person_id in shared_people_by_role.items()
        },
        "published_at": "2026-05-31T00:01:00Z",
        "approvals": [
            approval("contract-owner"),
            approval("security-owner"),
        ],
    }
    for boundary in BOUNDARIES:
        maintainer_person_id = f"fixture-maintainer-person-{boundary}"
        maintainer_seed = hashlib.sha256(
            f"YLX SYNTHETIC TEST MAINTAINER KEY ONLY:{boundary}".encode(
                "ascii"
            )
        ).digest()
        maintainer_public_raw = (
            Ed25519PrivateKey.from_private_bytes(maintainer_seed)
            .public_key()
            .public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        )
        maintainer_fingerprint = sha(maintainer_public_raw)
        shared_key_head["keys_by_fingerprint"][maintainer_fingerprint] = {
            "key_id": f"fixture-maintainer-key-{boundary}",
            "person_id": maintainer_person_id,
            "public_key_base64": base64.b64encode(
                maintainer_public_raw
            ).decode("ascii"),
            "valid_from": VALID_FROM,
            "not_after": NOT_AFTER,
            "status": "VALID",
            "revocation_or_compromise_effective_at": None,
            "reason": None,
        }
    shared_key_head_sha = sha(canonical_bytes(shared_key_head))
    shared_key_head_locator = valid_locator(
        "signing-key-validity-revocation-head.json"
    )

    ga_identity_source_filename = "m0-identity-source-ga-operator.json"
    ga_identity_source_sha = corpus.add_support(
        ga_identity_source_filename,
        {
            "assertion_id": "fixture-ga-operator-identity-assertion",
            "person_id": "fixture-ga-operator-person",
            "notice": NOTICE,
        },
        "Synthetic external identity assertion for the M0 build approver.",
    )
    ga_identity_filename = "natural-person-identity-ga-operator.json"
    ga_identity = {
        "schema": "ylx.natural-person-identity-authority.v1",
        "authority_record_id": "fixture-natural-person-ga-operator-r1",
        "identity_authority_id": "fixture-organizational-identity-authority",
        "person_id": "fixture-ga-operator-person",
        "revision": 1,
        "predecessor_identity_authority_ref": None,
        "identity_claim_sha256": ga_identity_source_sha,
        "source_authority_refs": [
            {
                "ref_id": "fixture-ga-operator-identity-assertion",
                "authority_kind": "external-organizational-authority",
                "locator": support_locator(ga_identity_source_filename),
                "sha256": ga_identity_source_sha,
            }
        ],
        "accountable_natural_person": True,
        "subject_status": "ACTIVE",
        "effective_from": VALID_FROM,
        "not_after": NOT_AFTER,
        "revoked_at": None,
        "revocation_reason": None,
        "verification_method": "AUTHORITATIVE_DIRECTORY_ASSERTION",
        "verified_at": STAMP,
        "privacy_profile": "PSEUDONYMOUS_STABLE_ID_NO_RAW_PII",
        "artifact_metadata": metadata(),
    }
    ga_identity_sha = corpus.add(
        "VALID-M0-NATURAL-PERSON-IDENTITY-GA-OPERATOR-01",
        ga_identity_filename,
        "natural-person-identity-authority-v1.schema.json",
        ga_identity,
    )
    identity_path_by_role = {
        "release-owner": valid_locator(
            "natural-person-identity-release-owner.json"
        ),
        "build-platform-owner": valid_locator(ga_identity_filename),
        "qa-evidence-owner": valid_locator(
            "natural-person-identity-qa-evidence-owner.json"
        ),
    }
    identity_sha_by_role = {
        "release-owner": corpus.digests[
            "valid/natural-person-identity-release-owner.json"
        ],
        "build-platform-owner": ga_identity_sha,
        "qa-evidence-owner": corpus.digests[
            "valid/natural-person-identity-qa-evidence-owner.json"
        ],
    }
    for role_id, identity_sha in identity_sha_by_role.items():
        shared_assignment_by_role[role_id][
            "natural_person_identity_sha256"
        ] = identity_sha
    shared_assignment_sha_by_role = {
        role_id: sha(canonical_bytes(value))
        for role_id, value in shared_assignment_by_role.items()
    }
    approval_time_by_role = {
        "release-owner": moment(29),
        "build-platform-owner": moment(32),
        "qa-evidence-owner": moment(35),
    }
    approval_publication_time_by_role = {
        "release-owner": moment(30),
        "build-platform-owner": moment(33),
        "qa-evidence-owner": moment(36),
    }
    approval_readback_time_by_role = {
        "release-owner": moment(31),
        "build-platform-owner": moment(34),
        "qa-evidence-owner": moment(37),
    }
    artifact_sha_by_kind = {
        "owner_assignment": owner_sha,
        "resource_calendar": calendar_sha,
        "delivery_wbs": wbs_sha,
        "forecast_snapshot": forecast_sha,
    }
    approval_value_by_role: dict[str, dict[str, Any]] = {}
    approval_sha_by_role: dict[str, str] = {}
    approval_path_by_role: dict[str, str] = {}
    approval_publication_by_role: dict[str, dict[str, Any]] = {}
    approval_publication_sha_by_role: dict[str, str] = {}
    approval_publication_path_by_role: dict[str, str] = {}
    approval_readback_by_role: dict[str, dict[str, Any]] = {}
    approval_readback_sha_by_role: dict[str, str] = {}
    approval_readback_path_by_role: dict[str, str] = {}

    for role_id in planning_roles:
        evidence_filename = f"m0-planning-approval-evidence-{role_id}.json"
        evidence_id = f"fixture-m0-planning-approval-evidence-{role_id}"
        evidence_sha = corpus.add_support(
            evidence_filename,
            {
                "evidence_id": evidence_id,
                "role_id": role_id,
                "principal_id": approval_person_by_role[role_id],
                "planning_approval_subject_ref": approval_subject_locator,
                "planning_approval_subject_sha256": approval_subject_sha,
                "observed_at": moment(28),
                "authority_effect": "NONE",
                "notice": NOTICE,
            },
            f"Synthetic opaque M0 planning approval support for {role_id}.",
        )
        approval_filename = f"m0-planning-bundle-approval-{role_id}.json"
        approval_locator = valid_locator(approval_filename)
        key_assignment_locator = valid_locator(
            f"role-signing-key-assignment-{role_id}.json"
        )
        approval_payload = {
            "schema": "ylx.planning-bundle-approval.v1",
            "approval_id": approval_id_by_role[role_id],
            "role_id": role_id,
            "principal_id": approval_person_by_role[role_id],
            "natural_person_id": approval_person_by_role[role_id],
            "decision": "APPROVED",
            "approved_at": approval_time_by_role[role_id],
            "identity_authority_ref": identity_path_by_role[role_id],
            "identity_authority_sha256": identity_sha_by_role[role_id],
            "assignment_ref": owner_locator,
            "assignment_sha256": owner_sha,
            "owner_publication_receipt_ref": owner_publication_locator,
            "owner_publication_receipt_sha256": owner_publication_sha,
            "owner_readback_receipt_ref": owner_readback_locator,
            "owner_readback_receipt_sha256": owner_readback_sha,
            "g0_issue_reconciliation_ref": reconciliation_locator,
            "g0_issue_reconciliation_sha256": reconciliation_sha,
            "planning_approval_subject_ref": approval_subject_locator,
            "planning_approval_subject_sha256": approval_subject_sha,
            "bundle_revision": 1,
            "predecessor_sha256": None,
            "artifact_sha256_by_kind": copy.deepcopy(artifact_sha_by_kind),
            "owner_assignment_revision": owner["revision"],
            "approval_evidence_ref": artifact_ref(
                evidence_id,
                "ylx.synthetic-opaque-support-record.v1",
                evidence_sha,
                support_locator(evidence_filename),
                1,
            ),
            "approval_evidence_sha256": evidence_sha,
            "key_assignment_ref": key_assignment_locator,
            "key_assignment_sha256": shared_assignment_sha_by_role[role_id],
            "public_key_fingerprint": approval_key_by_role[role_id][
                "fingerprint_sha256"
            ],
            "key_validity_revocation_head_ref": shared_key_head_locator,
            "key_validity_revocation_head_sha256": shared_key_head_sha,
            "signature_algorithm": "Ed25519",
            "signature_encoding": "base64",
            "signing_key_id": approval_key_by_role[role_id]["key_id"],
        }
        approval_value = sign_closed_record(
            approval_payload,
            private_key=approval_key_by_role[role_id]["private_key"],
            signature_domain="YLX-PLANNING-BUNDLE-APPROVAL-V1",
        )
        approval_sha = corpus.add(
            f"VALID-M0-PLANNING-BUNDLE-APPROVAL-{role_id.upper()}-01",
            approval_filename,
            "planning-bundle-approval-v1.schema.json",
            approval_value,
        )
        publication, publication_sha, publication_locator = (
            add_approval_publication_receipt(
                role_id=role_id,
                approval_id=approval_value["approval_id"],
                approval_ref=approval_locator,
                approval_sha256=approval_sha,
                byte_length=corpus.byte_lengths[f"valid/{approval_filename}"],
                actor_id=f"fixture-m0-planning-approval-importer-{role_id}",
                published_at=approval_publication_time_by_role[role_id],
            )
        )
        readback, readback_sha, readback_locator = (
            add_approval_readback_receipt(
                role_id=role_id,
                approval_id=approval_value["approval_id"],
                approval_ref=approval_locator,
                approval_sha256=approval_sha,
                byte_length=corpus.byte_lengths[f"valid/{approval_filename}"],
                publication_receipt_ref=publication_locator,
                publication_receipt_sha256=publication_sha,
                reader_id=f"fixture-m0-planning-approval-reader-{role_id}",
                read_back_at=approval_readback_time_by_role[role_id],
            )
        )
        approval_value_by_role[role_id] = approval_value
        approval_sha_by_role[role_id] = approval_sha
        approval_path_by_role[role_id] = approval_locator
        approval_publication_by_role[role_id] = publication
        approval_publication_sha_by_role[role_id] = publication_sha
        approval_publication_path_by_role[role_id] = publication_locator
        approval_readback_by_role[role_id] = readback
        approval_readback_sha_by_role[role_id] = readback_sha
        approval_readback_path_by_role[role_id] = readback_locator

    child_by_kind = {
        "resource_calendar": calendar,
        "delivery_wbs": wbs,
        "forecast_snapshot": forecast,
    }
    child_locator_by_kind = {
        "resource_calendar": calendar_locator,
        "delivery_wbs": wbs_locator,
        "forecast_snapshot": forecast_locator,
    }
    child_sha_by_kind = {
        "resource_calendar": calendar_sha,
        "delivery_wbs": wbs_sha,
        "forecast_snapshot": forecast_sha,
    }
    child_filename_by_kind = {
        "resource_calendar": "governed-resource-calendar-root.json",
        "delivery_wbs": "governed-delivery-wbs-root.json",
        "forecast_snapshot": "governed-forecast-snapshot-root.json",
    }
    child_publication_time_by_kind = {
        "resource_calendar": moment(39),
        "delivery_wbs": moment(41),
        "forecast_snapshot": moment(43),
    }
    child_readback_time_by_kind = {
        "resource_calendar": moment(40),
        "delivery_wbs": moment(42),
        "forecast_snapshot": moment(44),
    }
    child_publication_by_kind: dict[str, dict[str, Any]] = {}
    child_publication_sha_by_kind: dict[str, str] = {}
    child_publication_path_by_kind: dict[str, str] = {}
    child_readback_by_kind: dict[str, dict[str, Any]] = {}
    child_readback_sha_by_kind: dict[str, str] = {}
    child_readback_path_by_kind: dict[str, str] = {}
    for kind in ("resource_calendar", "delivery_wbs", "forecast_snapshot"):
        child = child_by_kind[kind]
        slug = kind.replace("_", "-")
        publication, publication_sha, publication_locator = (
            add_planning_publication_receipt(
                step_id=f"{slug}-write",
                artifact_schema=child["schema"],
                artifact_id=child["artifact_id"],
                artifact_ref_value=child_locator_by_kind[kind],
                artifact_sha256=child_sha_by_kind[kind],
                byte_length=corpus.byte_lengths[
                    f"valid/{child_filename_by_kind[kind]}"
                ],
                receipt_filename=(
                    f"m0-planning-bootstrap-{slug}-publication-receipt.json"
                ),
                receipt_id=(
                    f"fixture-m0-planning-bootstrap-{slug}-publication-receipt"
                ),
                actor_id=f"fixture-m0-{slug}-publisher",
                published_at=child_publication_time_by_kind[kind],
            )
        )
        readback, readback_sha, readback_locator = (
            add_planning_readback_receipt(
                step_id=f"{slug}-readback",
                artifact_schema=child["schema"],
                artifact_id=child["artifact_id"],
                artifact_ref_value=child_locator_by_kind[kind],
                artifact_sha256=child_sha_by_kind[kind],
                byte_length=corpus.byte_lengths[
                    f"valid/{child_filename_by_kind[kind]}"
                ],
                publication_receipt_ref=publication_locator,
                publication_receipt_sha256=publication_sha,
                receipt_filename=(
                    f"m0-planning-bootstrap-{slug}-readback-receipt.json"
                ),
                receipt_id=(
                    f"fixture-m0-planning-bootstrap-{slug}-readback-receipt"
                ),
                reader_id=f"fixture-m0-{slug}-reader",
                read_back_at=child_readback_time_by_kind[kind],
            )
        )
        child_publication_by_kind[kind] = publication
        child_publication_sha_by_kind[kind] = publication_sha
        child_publication_path_by_kind[kind] = publication_locator
        child_readback_by_kind[kind] = readback
        child_readback_sha_by_kind[kind] = readback_sha
        child_readback_path_by_kind[kind] = readback_locator

    planning_approval_reference_by_role = {
        role_id: {
            "approval_ref": approval_path_by_role[role_id],
            "approval_sha256": approval_sha_by_role[role_id],
            "publication_receipt_ref": approval_publication_path_by_role[
                role_id
            ],
            "publication_receipt_sha256": (
                approval_publication_sha_by_role[role_id]
            ),
            "readback_receipt_ref": approval_readback_path_by_role[role_id],
            "readback_receipt_sha256": approval_readback_sha_by_role[role_id],
        }
        for role_id in planning_roles
    }
    bundle = {
        "schema": approval_subject["schema"],
        "artifact_id": approval_subject["artifact_id"],
        "revision": approval_subject["revision"],
        "predecessor_sha256": approval_subject["predecessor_sha256"],
        "generated_at": moment(46),
        "source_refs": copy.deepcopy(approval_subject["source_refs"]),
        "artifact_metadata": copy.deepcopy(
            approval_subject["artifact_metadata"]
        ),
        "planning_approval_subject_ref": approval_subject_locator,
        "planning_approval_subject_sha256": approval_subject_sha,
        "planning_bundle_approval_by_role": copy.deepcopy(
            planning_approval_reference_by_role
        ),
        "planning_gate": approval_subject["planning_gate"],
        "detail_horizon": copy.deepcopy(approval_subject["detail_horizon"]),
        "registry_binding": copy.deepcopy(
            approval_subject["registry_binding"]
        ),
        "artifacts": copy.deepcopy(approval_subject["artifacts"]),
        "bundle_kind": approval_subject["bundle_kind"],
        "final_actual_variance_reconciliation": copy.deepcopy(
            approval_subject["final_actual_variance_reconciliation"]
        ),
        "overall_status": "ACCEPTED",
    }
    bundle_filename = "governed-delivery-planning-bundle-root.json"
    bundle_locator = valid_locator(bundle_filename)
    bundle_sha = corpus.replace(bundle_filename, bundle)
    bundle_ref = artifact_ref(
        bundle["artifact_id"],
        bundle["schema"],
        bundle_sha,
        bundle_locator,
        bundle["revision"],
    )
    bundle_publication, bundle_publication_sha, bundle_publication_locator = (
        add_planning_publication_receipt(
            step_id="containing-bundle-write",
            artifact_schema=bundle["schema"],
            artifact_id=bundle["artifact_id"],
            artifact_ref_value=bundle_locator,
            artifact_sha256=bundle_sha,
            byte_length=corpus.byte_lengths[f"valid/{bundle_filename}"],
            receipt_filename=(
                "m0-planning-bootstrap-containing-bundle-publication-"
                "receipt.json"
            ),
            receipt_id=(
                "fixture-m0-planning-bootstrap-containing-bundle-"
                "publication-receipt"
            ),
            actor_id="fixture-m0-containing-bundle-publisher",
            published_at=moment(47),
        )
    )
    bundle_readback, bundle_readback_sha, bundle_readback_locator = (
        add_planning_readback_receipt(
            step_id="containing-bundle-readback",
            artifact_schema=bundle["schema"],
            artifact_id=bundle["artifact_id"],
            artifact_ref_value=bundle_locator,
            artifact_sha256=bundle_sha,
            byte_length=corpus.byte_lengths[f"valid/{bundle_filename}"],
            publication_receipt_ref=bundle_publication_locator,
            publication_receipt_sha256=bundle_publication_sha,
            receipt_filename=(
                "m0-planning-bootstrap-containing-bundle-readback-receipt.json"
            ),
            receipt_id=(
                "fixture-m0-planning-bootstrap-containing-bundle-readback-"
                "receipt"
            ),
            reader_id="fixture-m0-containing-bundle-reader",
            read_back_at=moment(48),
        )
    )

    expected_steps = set(operation_constraint_by_step)
    if (
        set(operation_authority_path_by_step_and_kind) != expected_steps
        or set(operation_authority_sha256_by_step_and_kind) != expected_steps
    ):
        raise AssertionError("M0 operation-authority step set is incomplete")
    for step_id in expected_steps:
        if set(operation_authority_path_by_step_and_kind[step_id]) != set(
            operation_authority_sha256_by_step_and_kind[step_id]
        ):
            raise AssertionError(
                f"M0 operation-authority kind map differs for {step_id}"
            )

    roots.update(
        {
            "owner_assignment": owner,
            "owner_assignment_sha": owner_sha,
            "resource_calendar": calendar,
            "resource_calendar_sha": calendar_sha,
            "delivery_wbs": wbs,
            "delivery_wbs_sha": wbs_sha,
            "forecast_snapshot": forecast,
            "forecast_snapshot_sha": forecast_sha,
            "delivery_planning_bundle": bundle,
            "delivery_planning_bundle_sha": bundle_sha,
        }
    )

    timestamp_by_event = {
        "c_created": closure["created_at"],
        "c_readback": closure_readback["read_back_at"],
        "phase_a_derived": phase_a["derived_at"],
        "owner_published": owner_publication["published_at"],
        "owner_readback": owner_readback["read_back_at"],
        "issue_approval_min": issue_approvals[0]["approved_at"],
        "issue_approval_max": issue_approvals[1]["approved_at"],
        "issue_source_completed": source_operation_receipt["completed_at"],
        "issue_archive_completed": archive_operation_receipt["completed_at"],
        "issue_head_completed": head_operation_receipt["completed_at"],
        "transition_readback": transition_readback["read_back_at"],
        "q_reconciled": reconciliation["reconciled_at"],
        "phase_b_derived": phase_b["derived_at"],
        "subject_published": subject_publication["published_at"],
        "subject_readback": subject_readback["read_back_at"],
        "approval_approved_by_role": copy.deepcopy(approval_time_by_role),
        "approval_published_by_role": copy.deepcopy(
            approval_publication_time_by_role
        ),
        "approval_readback_by_role": copy.deepcopy(
            approval_readback_time_by_role
        ),
        "child_published_by_kind": copy.deepcopy(
            child_publication_time_by_kind
        ),
        "child_readback_by_kind": copy.deepcopy(child_readback_time_by_kind),
        "bundle_generated": bundle["generated_at"],
        "bundle_published": bundle_publication["published_at"],
        "bundle_readback": bundle_readback["read_back_at"],
    }
    m0_graph = {
        "synthetic_test_only_contract_fixture": True,
        "bootstrap_attempt_id": bootstrap_attempt_id,
        "repository_locator": repository_locator,
        "input_closure": {
            "path": closure_locator,
            "sha256": closure_sha,
            "readback_receipt_path": closure_readback_locator,
            "readback_receipt_sha256": closure_readback_sha,
        },
        "derivation_contract": {
            "path": derivation_contract_locator,
            "sha256": derivation_contract_sha,
        },
        "phase_a_derivation": {
            "path": phase_a_locator,
            "sha256": phase_a_sha,
            "operation_receipt_path": phase_a_operation_receipt_locator,
            "operation_receipt_sha256": phase_a_operation_receipt_sha,
        },
        "owner": {
            "payload_path": owner_locator,
            "payload_sha256": owner_sha,
            "publication_receipt_path": owner_publication_locator,
            "publication_receipt_sha256": owner_publication_sha,
            "readback_receipt_path": owner_readback_locator,
            "readback_receipt_sha256": owner_readback_sha,
        },
        "issue_reconciliation": {
            "predecessor_source_path": support_locator(
                predecessor_source_filename
            ),
            "predecessor_source_sha256": predecessor_source_sha,
            "predecessor_head_path": predecessor_head_locator,
            "predecessor_head_sha256": predecessor_head_sha,
            "replacement_source_path": support_locator(
                replacement_source_filename
            ),
            "replacement_source_sha256": replacement_source_sha,
            "archive_path": support_locator(replacement_archive_filename),
            "archive_sha256": replacement_archive_sha,
            "successor_head_path": successor_head_locator,
            "successor_head_sha256": successor_head_sha,
            "source_operation_receipt_path": source_operation_receipt_locator,
            "source_operation_receipt_sha256": source_operation_receipt_sha,
            "archive_operation_receipt_path": archive_operation_receipt_locator,
            "archive_operation_receipt_sha256": archive_operation_receipt_sha,
            "head_operation_receipt_path": head_operation_receipt_locator,
            "head_operation_receipt_sha256": head_operation_receipt_sha,
            "transition_readback_receipt_path": transition_readback_locator,
            "transition_readback_receipt_sha256": transition_readback_sha,
            "reconciliation_receipt_path": reconciliation_locator,
            "reconciliation_receipt_sha256": reconciliation_sha,
        },
        "phase_b_derivation": {
            "path": phase_b_locator,
            "sha256": phase_b_sha,
            "operation_receipt_path": phase_b_operation_receipt_locator,
            "operation_receipt_sha256": phase_b_operation_receipt_sha,
        },
        "approval_subject": {
            "payload_path": approval_subject_locator,
            "payload_sha256": approval_subject_sha,
            "publication_receipt_path": subject_publication_locator,
            "publication_receipt_sha256": subject_publication_sha,
            "readback_receipt_path": subject_readback_locator,
            "readback_receipt_sha256": subject_readback_sha,
        },
        "approval_by_role": {
            role_id: {
                "approval_path": approval_path_by_role[role_id],
                "approval_sha256": approval_sha_by_role[role_id],
                "publication_receipt_path": approval_publication_path_by_role[
                    role_id
                ],
                "publication_receipt_sha256": (
                    approval_publication_sha_by_role[role_id]
                ),
                "readback_receipt_path": approval_readback_path_by_role[
                    role_id
                ],
                "readback_receipt_sha256": approval_readback_sha_by_role[
                    role_id
                ],
            }
            for role_id in planning_roles
        },
        "remaining_child_by_kind": {
            kind: {
                "payload_path": child_locator_by_kind[kind],
                "payload_sha256": child_sha_by_kind[kind],
                "publication_receipt_path": child_publication_path_by_kind[
                    kind
                ],
                "publication_receipt_sha256": child_publication_sha_by_kind[
                    kind
                ],
                "readback_receipt_path": child_readback_path_by_kind[kind],
                "readback_receipt_sha256": child_readback_sha_by_kind[kind],
            }
            for kind in ("resource_calendar", "delivery_wbs", "forecast_snapshot")
        },
        "bundle": {
            "payload_path": bundle_locator,
            "payload_sha256": bundle_sha,
            "publication_receipt_path": bundle_publication_locator,
            "publication_receipt_sha256": bundle_publication_sha,
            "readback_receipt_path": bundle_readback_locator,
            "readback_receipt_sha256": bundle_readback_sha,
        },
        "operation_authority_path_by_step_and_kind": copy.deepcopy(
            operation_authority_path_by_step_and_kind
        ),
        "operation_authority_sha256_by_step_and_kind": copy.deepcopy(
            operation_authority_sha256_by_step_and_kind
        ),
        "chronology": copy.deepcopy(timestamp_by_event),
    }
    corpus.relationships["m0_bootstrap_graph"] = m0_graph

    def relative_model_path(locator: str) -> str:
        if not locator.startswith(fixture_prefix):
            raise AssertionError(f"M0 model locator escapes fixture root: {locator}")
        return locator.removeprefix(fixture_prefix)

    corpus.generator_context["m0_support"] = {
        "path_by_kind": {
            "input_closure": relative_model_path(closure_locator),
            "input_closure_readback": relative_model_path(
                closure_readback_locator
            ),
            "phase_a_derivation": relative_model_path(phase_a_locator),
            "owner_payload": relative_model_path(owner_locator),
            "owner_publication": relative_model_path(
                owner_publication_locator
            ),
            "owner_readback": relative_model_path(owner_readback_locator),
            "issue_successor_head": relative_model_path(
                successor_head_locator
            ),
            "issue_source_operation": relative_model_path(
                source_operation_receipt_locator
            ),
            "issue_archive_operation": relative_model_path(
                archive_operation_receipt_locator
            ),
            "issue_head_operation": relative_model_path(
                head_operation_receipt_locator
            ),
            "issue_transition_readback": relative_model_path(
                transition_readback_locator
            ),
            "issue_reconciliation": relative_model_path(
                reconciliation_locator
            ),
            "phase_b_derivation": relative_model_path(phase_b_locator),
            "approval_subject": relative_model_path(approval_subject_locator),
            "approval_subject_publication": relative_model_path(
                subject_publication_locator
            ),
            "approval_subject_readback": relative_model_path(
                subject_readback_locator
            ),
            "bundle_payload": relative_model_path(bundle_locator),
            "bundle_publication": relative_model_path(
                bundle_publication_locator
            ),
            "bundle_readback": relative_model_path(bundle_readback_locator),
        },
        "approval_path_by_role": {
            role_id: relative_model_path(approval_path_by_role[role_id])
            for role_id in planning_roles
        },
        "approval_publication_path_by_role": {
            role_id: relative_model_path(
                approval_publication_path_by_role[role_id]
            )
            for role_id in planning_roles
        },
        "approval_readback_path_by_role": {
            role_id: relative_model_path(
                approval_readback_path_by_role[role_id]
            )
            for role_id in planning_roles
        },
        "child_payload_path_by_kind": {
            kind: relative_model_path(child_locator_by_kind[kind])
            for kind in ("resource_calendar", "delivery_wbs", "forecast_snapshot")
        },
        "child_publication_path_by_kind": {
            kind: relative_model_path(child_publication_path_by_kind[kind])
            for kind in ("resource_calendar", "delivery_wbs", "forecast_snapshot")
        },
        "child_readback_path_by_kind": {
            kind: relative_model_path(child_readback_path_by_kind[kind])
            for kind in ("resource_calendar", "delivery_wbs", "forecast_snapshot")
        },
        "operation_authority_model_path_by_step_and_kind": {
            step_id: {
                kind: relative_model_path(locator)
                for kind, locator in value.items()
            }
            for step_id, value in operation_authority_path_by_step_and_kind.items()
        },
        "operation_authority_ref_by_step_and_kind": copy.deepcopy(
            operation_authority_path_by_step_and_kind
        ),
        "operation_authority_sha256_by_step_and_kind": copy.deepcopy(
            operation_authority_sha256_by_step_and_kind
        ),
        "planning_role_ids": list(planning_roles),
        "remaining_child_kinds": [
            "resource_calendar",
            "delivery_wbs",
            "forecast_snapshot",
        ],
        "issue_approval_role_ids": [
            item["role_id"] for item in issue_approvals
        ],
        "issue_approval_index_by_role": {
            item["role_id"]: index
            for index, item in enumerate(issue_approvals)
        },
        "q_absent_role_id": "contract-owner",
        "bootstrap_attempt_id": bootstrap_attempt_id,
        "bundle_payload_ref": bundle_locator,
        "timestamp_by_event": copy.deepcopy(timestamp_by_event),
        "issue_approval_min_index": 0,
        "issue_approval_max_index": 1,
    }
    return {
        "graph": copy.deepcopy(m0_graph),
        "owner_ref": owner_ref,
        "calendar_ref": calendar_ref,
        "wbs_ref": wbs_ref,
        "forecast_ref": forecast_ref,
        "bundle_ref": bundle_ref,
        "approval_by_role": copy.deepcopy(approval_value_by_role),
        "approval_publication_by_role": copy.deepcopy(
            approval_publication_by_role
        ),
        "approval_readback_by_role": copy.deepcopy(approval_readback_by_role),
        "child_publication_by_kind": copy.deepcopy(
            child_publication_by_kind
        ),
        "child_readback_by_kind": copy.deepcopy(child_readback_by_kind),
    }


def build_execution_authorization_evaluation(
    corpus: Corpus,
    planning_state: dict[str, Any],
    *,
    task_id: str,
    action_instance_id: str,
    filename_slug: str,
    authorization_binding_context_ref: dict[str, Any] | None,
    environment_class: str,
    phase_barrier_ids: list[str],
    result: str = "PASS",
    actor_assignment_ref: dict[str, Any] | None = None,
    actor_person_id: str = "fixture-qa-evidence-owner-person",
    additional_prerequisite_ref_by_kind: dict[str, dict[str, Any]] | None = None,
    evaluated_at: str = "2026-06-01T12:15:00Z",
) -> dict[str, Any]:
    """Materialize one exact one-shot authorization evaluation and its ref."""

    execution_nodes = planning_state.get(
        "execution_nodes", planning_state["wbs"]["nodes"]
    )
    node = next(item for item in execution_nodes if item["node_id"] == task_id)
    selected_wbs_nodes = planning_state["wbs"]["nodes"]
    selected_matches = [
        item for item in selected_wbs_nodes if item["node_id"] == task_id
    ]
    selected_node = selected_matches[0] if len(selected_matches) == 1 else None
    if (
        selected_node is not None
        and selected_node.get("node_kind") == "EXECUTABLE_LEAF"
        and selected_node.get("planning_status") in {"READY", "COMPLETE"}
    ):
        node = selected_node
        planning_authority = planning_state
    elif node["milestone_gate"] in {"M2", "M3", "M4", "M5"}:
        planning_authority = planning_state["execution_planning"]
    else:
        planning_authority = planning_state
    declaration = copy.deepcopy(node["execution_authorization"])
    authorization_class = declaration["authorization_class"]
    authorization_action = declaration["authorization_action"]
    authority_refs = copy.deepcopy(declaration["authority_refs"])
    authority_ref_by_id = {
        ref["artifact_id"]: copy.deepcopy(ref) for ref in authority_refs
    }
    authority_sha_by_id = {
        artifact_id: ref["artifact_sha256"]
        for artifact_id, ref in authority_ref_by_id.items()
    }

    if not phase_barrier_ids or len(phase_barrier_ids) != len(set(phase_barrier_ids)):
        raise AssertionError("authorization evaluation requires unique phase barriers")
    environment_filename = f"execution-environment-{environment_class}.json"
    environment_id = f"fixture-{environment_class}-environment"
    environment_sha = corpus.add_support(
        environment_filename,
        {
            "environment_id": environment_id,
            "environment_class": environment_class,
            "customer_visible": False,
            "notice": NOTICE,
        },
        f"Synthetic exact {environment_class} authorization environment bytes.",
    )
    environment_ref = artifact_ref(
        environment_id,
        "ylx.execution-environment.v1",
        environment_sha,
        f"contracts/fixtures/governance-models/support/{environment_filename}",
        1,
    )

    validator_filename = "execution-authorization-validator.json"
    validator_id = "fixture-execution-authorization-validator"
    validator_sha = corpus.add_support(
        validator_filename,
        {
            "validator_id": validator_id,
            "validator_version": "1.0.0",
            "notice": NOTICE,
        },
        "Synthetic exact execution-authorization validator bytes.",
    )
    validator_ref = artifact_ref(
        validator_id,
        "ylx.execution-authorization-validator.v1",
        validator_sha,
        f"contracts/fixtures/governance-models/support/{validator_filename}",
        1,
    )

    binding_sha = (
        authorization_binding_context_ref["artifact_sha256"]
        if authorization_binding_context_ref is not None
        else None
    )
    prerequisite_refs = {
        "delivery_wbs": copy.deepcopy(planning_authority["wbs_ref"])
    }
    prerequisite_digests = {"delivery_wbs": planning_authority["wbs_sha"]}
    for kind, ref in (additional_prerequisite_ref_by_kind or {}).items():
        prerequisite_refs[kind] = copy.deepcopy(ref)
        prerequisite_digests[kind] = (
            ref["artifact_sha256"] if "artifact_sha256" in ref else ref["sha256"]
        )
    resolved_actor_assignment_ref = copy.deepcopy(
        actor_assignment_ref or planning_state["owner_ref"]
    )
    reviewer_ref = node["reviewer_ref"]
    checker_assignment_ref = copy.deepcopy(reviewer_ref["owner_assignment_ref"])
    checker_person_id = reviewer_ref["principal_id"]
    typed_predecessor_state_by_task_id: dict[str, dict[str, Any]] = {}
    phase_barrier_state_by_id: dict[str, dict[str, Any]] = {}
    for barrier_id in phase_barrier_ids:
        barrier_slug = barrier_id.replace("/", "-").replace("_", "-")
        barrier_filename = (
            f"execution-phase-barrier-{filename_slug}-{barrier_slug}.json"
        )
        barrier_artifact_id = (
            f"fixture-execution-phase-barrier-{filename_slug}-{barrier_slug}"
        )
        barrier_payload = {
            "barrier_id": barrier_id,
            "state": "SATISFIED",
            "evaluated_at": evaluated_at,
            "notice": NOTICE,
        }
        if barrier_id == "milestone-entry/M0":
            barrier_payload.update(
                {
                    "predicate": "G0_POLICY_RATIFIED",
                    "g0_policy_ratification_ref": copy.deepcopy(
                        planning_state["g0_policy"]["ratification_ref"]
                    ),
                    "external_organizational_authority_ref": copy.deepcopy(
                        planning_state["g0_policy"]["external_authority_ref"]
                    ),
                    "canonical_clean_commit_ref": copy.deepcopy(
                        planning_state["g0_policy"]["clean_commit_ref"]
                    ),
                    "event_publication_receipt_ref": copy.deepcopy(
                        planning_state["g0_policy"]["publication_receipt_ref"]
                    ),
                    "event_readback_receipt_ref": copy.deepcopy(
                        planning_state["g0_policy"]["readback_receipt_ref"]
                    ),
                    "ratification_effective_at": planning_state["g0_policy"][
                        "ratification"
                    ]["effective_at"],
                }
            )
        barrier_sha = corpus.add_support(
            barrier_filename,
            barrier_payload,
            f"Synthetic exact satisfied execution phase barrier {barrier_id}.",
        )
        phase_barrier_state_by_id[barrier_id] = {
            "state": "SATISFIED",
            "evidence_ref": artifact_ref(
                barrier_artifact_id,
                "ylx.execution-phase-barrier-evidence.v1",
                barrier_sha,
                (
                    "contracts/fixtures/governance-models/support/"
                    f"{barrier_filename}"
                ),
                1,
            ),
        }

    observed_input_ref_by_kind: dict[str, dict[str, Any]] = {
        "planning_bundle": copy.deepcopy(planning_authority["bundle_ref"]),
        "delivery_wbs": copy.deepcopy(planning_authority["wbs_ref"]),
        "environment": copy.deepcopy(environment_ref),
        "actor_assignment": copy.deepcopy(resolved_actor_assignment_ref),
    }
    if authorization_binding_context_ref is not None:
        observed_input_ref_by_kind["binding_context"] = copy.deepcopy(
            authorization_binding_context_ref
        )
    observed_input_ref_by_kind.update(
        {
            f"prerequisite/{kind}": copy.deepcopy(ref)
            for kind, ref in prerequisite_refs.items()
        }
    )
    observed_input_ref_by_kind.update(
        {
            f"authority/{artifact_id}": copy.deepcopy(ref)
            for artifact_id, ref in authority_ref_by_id.items()
        }
    )
    observed_input_ref_by_kind.update(
        {
            f"predecessor/{predecessor_id}": copy.deepcopy(state["evidence_ref"])
            for predecessor_id, state in typed_predecessor_state_by_task_id.items()
        }
    )
    observed_input_ref_by_kind.update(
        {
            f"barrier/{barrier_id}": copy.deepcopy(state["evidence_ref"])
            for barrier_id, state in phase_barrier_state_by_id.items()
        }
    )
    observed_input_sha256_by_kind = {
        kind: (
            ref["artifact_sha256"]
            if "artifact_sha256" in ref
            else ref["sha256"]
        )
        for kind, ref in observed_input_ref_by_kind.items()
    }
    triggered = result == "FAIL"
    authorization_stop_rules: list[dict[str, Any]] = []
    for index, rule in enumerate(declaration["stop_rules"], start=1):
        observation_id = (
            f"fixture-stop-rule-observation-{filename_slug}-{index}"
        )
        observation_filename = (
            f"authorization-stop-rule-observation-{filename_slug}-{index}.json"
        )
        observation = {
            "schema": "ylx.authorization-stop-rule-observation.v1",
            "observation_id": observation_id,
            "revision": 1,
            "observed_at": evaluated_at,
            "task_id": task_id,
            "authorization_class": authorization_class,
            "authorization_action": authorization_action,
            "action_instance_id": action_instance_id,
            "rule_id": rule,
            "rule_sha256": sha(canonical_bytes(rule)),
            "observed_input_ref_by_kind": copy.deepcopy(
                observed_input_ref_by_kind
            ),
            "observed_input_sha256_by_kind": copy.deepcopy(
                observed_input_sha256_by_kind
            ),
            "checker_assignment_ref": copy.deepcopy(checker_assignment_ref),
            "checker_person_id": checker_person_id,
            "outcome": "TRIGGERED" if triggered else "CLEAR",
            "artifact_metadata": metadata(),
        }
        observation_sha = corpus.add(
            (
                "VALID-AUTHORIZATION-STOP-RULE-OBSERVATION-"
                f"{filename_slug.upper()}-{index:02d}"
            ),
            observation_filename,
            "authorization-stop-rule-observation-v1.schema.json",
            observation,
        )
        authorization_stop_rules.append(
            {
                "rule_id": rule,
                "rule_sha256": sha(canonical_bytes(rule)),
                "triggered": triggered,
                "evidence_ref": artifact_ref(
                    observation_id,
                    observation["schema"],
                    observation_sha,
                    (
                        "contracts/fixtures/governance-models/valid/"
                        f"{observation_filename}"
                    ),
                    observation["revision"],
                ),
            }
        )
    evaluation = {
        "schema": "ylx.execution-authorization-evaluation.v1",
        "evaluation_id": f"fixture-execution-evaluation-{filename_slug}",
        "evaluated_at": evaluated_at,
        "task_id": task_id,
        "leaf_declaration_sha256": sha(canonical_bytes(declaration)),
        "authorization_class": authorization_class,
        "authorization_action": authorization_action,
        "action_instance_id": action_instance_id,
        "planned_action_input_sha256": "",
        "planning_bundle_ref": copy.deepcopy(planning_authority["bundle_ref"]),
        "planning_bundle_sha256": planning_authority["bundle_sha"],
        "delivery_wbs_ref": copy.deepcopy(planning_authority["wbs_ref"]),
        "delivery_wbs_sha256": planning_authority["wbs_sha"],
        "authorization_prerequisite_ref_by_kind": prerequisite_refs,
        "authorization_prerequisite_sha256_by_kind": prerequisite_digests,
        "authorization_environment_class": environment_class,
        "authorization_environment_ref": environment_ref,
        "authorization_binding_context_ref": copy.deepcopy(
            authorization_binding_context_ref
        ),
        "authorization_binding_context_sha256": binding_sha,
        "authorization_authority_ref_by_artifact_id": authority_ref_by_id,
        "authorization_authority_sha256_by_artifact_id": authority_sha_by_id,
        "actor_assignment_ref": resolved_actor_assignment_ref,
        "actor_person_id": actor_person_id,
        "authorization_stop_rules": authorization_stop_rules,
        "typed_predecessor_state_by_task_id": typed_predecessor_state_by_task_id,
        "phase_barrier_state_by_id": phase_barrier_state_by_id,
        "validator_artifact_ref": validator_ref,
        "checker_assignment_ref": checker_assignment_ref,
        "result": result,
        "failure_codes": [] if result == "PASS" else ["STOP_RULE_TRIGGERED"],
        "authorizes_action": authorization_action if result == "PASS" else None,
        "artifact_metadata": metadata(),
    }
    planned_input_fields = execution_authorization_projection_fields()
    evaluation["planned_action_input_sha256"] = sha(
        canonical_bytes({field: evaluation[field] for field in planned_input_fields})
    )
    filename = f"execution-authorization-evaluation-{filename_slug}.json"
    evaluation_sha = corpus.add(
        f"VALID-EXECUTION-AUTHORIZATION-EVALUATION-{filename_slug.upper()}-01",
        filename,
        "execution-authorization-evaluation-v1.schema.json",
        evaluation,
    )
    evaluation_ref = artifact_ref(
        evaluation["evaluation_id"],
        evaluation["schema"],
        evaluation_sha,
        f"contracts/fixtures/governance-models/valid/{filename}",
        None,
    )
    return {
        "value": evaluation,
        "sha": evaluation_sha,
        "ref": evaluation_ref,
    }


def build_final_actual_variance_durability_fixtures(
    corpus: Corpus,
    planning_state: dict[str, Any],
    m5_binding_context_ref: dict[str, Any],
) -> dict[str, Any]:
    """Build the non-recursive accepted-H/W -> F -> E -> P -> R chain."""

    fixture_prefix = "contracts/fixtures/governance-models/valid/"
    chain_source_filename = "final-planning-chain-source.json"
    chain_source_id = "fixture-final-planning-chain-source"
    chain_source_sha = corpus.add_support(
        chain_source_filename,
        {
            "source_id": chain_source_id,
            "purpose": "Synthetic final planning revision-chain provenance.",
            "notice": NOTICE,
        },
        "Synthetic exact source bytes for the final planning revision chain.",
    )
    chain_source_ref = {
        "ref_id": chain_source_id,
        "authority_kind": "fixture-oracle",
        "locator": (
            "contracts/fixtures/governance-models/support/"
            f"{chain_source_filename}"
        ),
        "sha256": chain_source_sha,
    }
    h_detail_horizon = copy.deepcopy(planning_state["bundle"]["detail_horizon"])
    h_detail_horizon.update(
        {
            "planning_gate": "M4",
            "executable_through_gate": "M5",
            "next_expansion_gate": "M5",
        }
    )
    f_detail_horizon = copy.deepcopy(h_detail_horizon)
    f_detail_horizon.update(
        {
            "planning_gate": "M5",
            "executable_through_gate": "M5",
            "next_expansion_gate": None,
        }
    )

    h_owner_filename = "final-plan-owner-assignment-r1.json"
    h_owner = copy.deepcopy(planning_state["owner"])
    h_owner.update(
        {
            "artifact_id": "fixture-final-plan-owner-assignment",
            "revision": 1,
            "predecessor_sha256": None,
            "generated_at": "2026-06-01T12:10:00Z",
            "source_refs": [copy.deepcopy(chain_source_ref)],
        }
    )
    h_owner_sha = corpus.add(
        "VALID-FINAL-PLAN-OWNER-ASSIGNMENT-R1-01",
        h_owner_filename,
        "owner-assignment-v1.schema.json",
        h_owner,
    )
    h_owner_ref = artifact_ref(
        h_owner["artifact_id"],
        h_owner["schema"],
        h_owner_sha,
        f"{fixture_prefix}{h_owner_filename}",
        1,
    )

    h_calendar_filename = "final-plan-resource-calendar-r1.json"
    h_calendar = copy.deepcopy(planning_state["calendar"])
    h_calendar.update(
        {
            "artifact_id": "fixture-final-plan-resource-calendar",
            "revision": 1,
            "predecessor_sha256": None,
            "generated_at": "2026-06-01T12:20:00Z",
            "source_refs": [copy.deepcopy(chain_source_ref)],
        }
    )
    h_calendar_sha = corpus.add(
        "VALID-FINAL-PLAN-RESOURCE-CALENDAR-R1-01",
        h_calendar_filename,
        "resource-calendar-v1.schema.json",
        h_calendar,
    )
    h_calendar_ref = artifact_ref(
        h_calendar["artifact_id"],
        h_calendar["schema"],
        h_calendar_sha,
        f"{fixture_prefix}{h_calendar_filename}",
        1,
    )

    def bind_wbs_owner_refs(
        wbs: dict[str, Any], owner_ref: dict[str, Any]
    ) -> None:
        for node in wbs["nodes"]:
            for field in ("accountable_owner_ref", "executor_ref", "reviewer_ref"):
                node[field]["owner_assignment_ref"] = copy.deepcopy(owner_ref)

    h_wbs_filename = "final-plan-delivery-wbs-r1.json"
    h_wbs = copy.deepcopy(planning_state["wbs"])
    h_wbs.update(
        {
            "artifact_id": "fixture-final-plan-delivery-wbs",
            "revision": 1,
            "predecessor_sha256": None,
            "generated_at": "2026-06-01T12:30:00Z",
            "source_refs": [copy.deepcopy(chain_source_ref)],
            "planning_gate": "M4",
            "detail_horizon": copy.deepcopy(h_detail_horizon),
            "nodes": copy.deepcopy(planning_state["execution_nodes"]),
        }
    )
    for node in h_wbs["nodes"]:
        if node["node_id"] == "fixture-v2-all-requirements-node":
            node["milestone_gate"] = "M5"
    bind_wbs_owner_refs(h_wbs, h_owner_ref)
    h_wbs_sha = corpus.add(
        "VALID-FINAL-PLAN-DELIVERY-WBS-R1-01",
        h_wbs_filename,
        "delivery-wbs-v2.schema.json",
        h_wbs,
    )
    h_wbs_ref = artifact_ref(
        h_wbs["artifact_id"],
        h_wbs["schema"],
        h_wbs_sha,
        f"{fixture_prefix}{h_wbs_filename}",
        1,
    )

    h_forecast_filename = "final-plan-forecast-snapshot-r1.json"
    h_forecast = copy.deepcopy(planning_state["forecast"])
    h_forecast.update(
        {
            "artifact_id": "fixture-final-plan-forecast-snapshot",
            "revision": 1,
            "predecessor_sha256": None,
            "generated_at": "2026-06-01T12:40:00Z",
            "source_refs": [copy.deepcopy(chain_source_ref)],
            "owner_assignment_sha256": h_owner_sha,
            "resource_calendar_sha256": h_calendar_sha,
            "delivery_wbs_sha256": h_wbs_sha,
        }
    )
    h_forecast.update(calculate_delivery_planning(h_wbs, h_calendar))
    h_forecast_sha = corpus.add(
        "VALID-FINAL-PLAN-FORECAST-SNAPSHOT-R1-01",
        h_forecast_filename,
        "forecast-snapshot-v1.schema.json",
        h_forecast,
    )
    h_forecast_ref = artifact_ref(
        h_forecast["artifact_id"],
        h_forecast["schema"],
        h_forecast_sha,
        f"{fixture_prefix}{h_forecast_filename}",
        1,
    )

    approval_subject_fields = (
        "schema",
        "artifact_id",
        "revision",
        "predecessor_sha256",
        "source_refs",
        "artifact_metadata",
        "planning_gate",
        "detail_horizon",
        "registry_binding",
        "artifacts",
        "bundle_kind",
        "final_actual_variance_reconciliation",
    )

    def bind_bundle_approvals(
        bundle: dict[str, Any],
        owner_ref: dict[str, Any],
        child_sha_by_kind: dict[str, str],
        *,
        approved_at: str,
        label: str,
    ) -> None:
        subject_sha = sha(
            canonical_bytes(
                {field: bundle[field] for field in approval_subject_fields}
            )
        )
        bundle["planning_approval_subject_sha256"] = subject_sha
        approvals: dict[str, Any] = {}
        for role in (
            "release-owner",
            "build-platform-owner",
            "qa-evidence-owner",
        ):
            evidence_filename = f"final-plan-approval-{label}-{role}.json"
            evidence_id = f"fixture-final-plan-approval-{label}-{role}"
            evidence_sha = corpus.add_support(
                evidence_filename,
                {
                    "evidence_id": evidence_id,
                    "planning_approval_subject_sha256": subject_sha,
                    "bundle_revision": bundle["revision"],
                    "notice": NOTICE,
                },
                f"Synthetic final-plan {label} approval evidence for {role}.",
            )
            approvals[role] = {
                "role_id": role,
                "principal_id": f"fixture-{role}-person",
                "natural_person_id": f"fixture-{role}-person",
                "decision": "APPROVED",
                "approved_at": approved_at,
                "assignment_ref": copy.deepcopy(owner_ref),
                "planning_approval_subject_sha256": subject_sha,
                "bundle_revision": bundle["revision"],
                "predecessor_sha256": bundle["predecessor_sha256"],
                "artifact_sha256_by_kind": copy.deepcopy(child_sha_by_kind),
                "owner_assignment_revision": owner_ref["revision"],
                "approval_evidence_ref": artifact_ref(
                    evidence_id,
                    "ylx.planning-approval-evidence.v1",
                    evidence_sha,
                    (
                        "contracts/fixtures/governance-models/support/"
                        f"{evidence_filename}"
                    ),
                    1,
                ),
            }
        bundle["planning_bundle_approval_by_role"] = approvals

    h_bundle_filename = "final-actual-variance/planned-delivery-planning-bundle.json"
    h_bundle = copy.deepcopy(planning_state["bundle"])
    h_bundle.update(
        {
            "artifact_id": "fixture-final-plan-delivery-planning-bundle",
            "revision": 1,
            "predecessor_sha256": None,
            "generated_at": "2026-06-01T13:00:00Z",
            "source_refs": [copy.deepcopy(chain_source_ref)],
            "planning_gate": "M4",
            "detail_horizon": copy.deepcopy(h_detail_horizon),
            "artifacts": {
                "owner_assignment": h_owner_ref,
                "resource_calendar": h_calendar_ref,
                "delivery_wbs": h_wbs_ref,
                "forecast_snapshot": h_forecast_ref,
            },
            "bundle_kind": "ROLLING_WAVE",
            "final_actual_variance_reconciliation": None,
        }
    )
    bind_bundle_approvals(
        h_bundle,
        h_owner_ref,
        {
            "owner_assignment": h_owner_sha,
            "resource_calendar": h_calendar_sha,
            "delivery_wbs": h_wbs_sha,
            "forecast_snapshot": h_forecast_sha,
        },
        approved_at="2026-06-01T12:59:00Z",
        label="h-r1",
    )
    h_bundle_sha = corpus.add(
        "VALID-FINAL-PLAN-ROLLING-WAVE-H-01",
        h_bundle_filename,
        "delivery-planning-bundle-v2.schema.json",
        h_bundle,
    )
    h_bundle_ref = artifact_ref(
        h_bundle["artifact_id"],
        h_bundle["schema"],
        h_bundle_sha,
        f"{fixture_prefix}{h_bundle_filename}",
        1,
    )
    publisher_task_node_id = "fixture-v2-all-requirements-node"
    action_instance_id = "fixture-final-plan-publication-action"

    f_owner_filename = "final-plan-owner-assignment-r2.json"
    f_owner = copy.deepcopy(h_owner)
    f_owner.update(
        {
            "revision": 2,
            "predecessor_sha256": h_owner_sha,
            "generated_at": "2026-06-02T07:54:00Z",
            "source_refs": [copy.deepcopy(chain_source_ref)],
        }
    )
    f_owner_sha = corpus.add(
        "VALID-FINAL-PLAN-OWNER-ASSIGNMENT-R2-01",
        f_owner_filename,
        "owner-assignment-v1.schema.json",
        f_owner,
    )
    f_owner_ref = artifact_ref(
        f_owner["artifact_id"],
        f_owner["schema"],
        f_owner_sha,
        f"{fixture_prefix}{f_owner_filename}",
        2,
    )

    f_calendar_filename = "final-plan-resource-calendar-r2.json"
    f_calendar = copy.deepcopy(h_calendar)
    f_calendar.update(
        {
            "revision": 2,
            "predecessor_sha256": h_calendar_sha,
            "generated_at": "2026-06-02T07:55:00Z",
            "source_refs": [copy.deepcopy(chain_source_ref)],
        }
    )
    f_calendar_sha = corpus.add(
        "VALID-FINAL-PLAN-RESOURCE-CALENDAR-R2-01",
        f_calendar_filename,
        "resource-calendar-v1.schema.json",
        f_calendar,
    )
    f_calendar_ref = artifact_ref(
        f_calendar["artifact_id"],
        f_calendar["schema"],
        f_calendar_sha,
        f"{fixture_prefix}{f_calendar_filename}",
        2,
    )

    f_wbs_filename = "final-plan-delivery-wbs-r2.json"
    f_wbs = copy.deepcopy(h_wbs)
    f_wbs.update(
        {
            "revision": 2,
            "predecessor_sha256": h_wbs_sha,
            "generated_at": "2026-06-02T07:56:00Z",
            "source_refs": [copy.deepcopy(chain_source_ref)],
            "planning_gate": "M5",
            "detail_horizon": copy.deepcopy(f_detail_horizon),
        }
    )
    for node in f_wbs["nodes"]:
        node["planning_status"] = "COMPLETE"
    bind_wbs_owner_refs(f_wbs, f_owner_ref)
    f_wbs_sha = corpus.add(
        "VALID-FINAL-PLAN-DELIVERY-WBS-R2-01",
        f_wbs_filename,
        "delivery-wbs-v2.schema.json",
        f_wbs,
    )
    f_wbs_ref = artifact_ref(
        f_wbs["artifact_id"],
        f_wbs["schema"],
        f_wbs_sha,
        f"{fixture_prefix}{f_wbs_filename}",
        2,
    )

    f_forecast_filename = "final-plan-forecast-snapshot-r2.json"
    f_forecast = copy.deepcopy(h_forecast)
    f_forecast.update(
        {
            "revision": 2,
            "predecessor_sha256": h_forecast_sha,
            "generated_at": "2026-06-02T07:57:00Z",
            "source_refs": [copy.deepcopy(chain_source_ref)],
            "owner_assignment_sha256": f_owner_sha,
            "resource_calendar_sha256": f_calendar_sha,
            "delivery_wbs_sha256": f_wbs_sha,
        }
    )
    f_forecast_sha = corpus.add(
        "VALID-FINAL-PLAN-FORECAST-SNAPSHOT-R2-01",
        f_forecast_filename,
        "forecast-snapshot-v1.schema.json",
        f_forecast,
    )
    f_forecast_ref = artifact_ref(
        f_forecast["artifact_id"],
        f_forecast["schema"],
        f_forecast_sha,
        f"{fixture_prefix}{f_forecast_filename}",
        2,
    )

    terminal_evidence_filename = "final-plan-terminal-evidence.json"
    terminal_evidence_id = "fixture-final-plan-terminal-evidence"
    terminal_evidence_sha = corpus.add_support(
        terminal_evidence_filename,
        {
            "evidence_id": terminal_evidence_id,
            "terminal_status": "COMPLETED",
            "notice": NOTICE,
        },
        "Synthetic shared terminal evidence for non-publisher final-plan leaves.",
    )
    terminal_evidence_ref = artifact_ref(
        terminal_evidence_id,
        "ylx.synthetic-terminal-evidence.v1",
        terminal_evidence_sha,
        (
            "contracts/fixtures/governance-models/support/"
            f"{terminal_evidence_filename}"
        ),
        1,
    )
    reconciliation_evidence_filename = "final-plan-reconciliation-evidence.json"
    reconciliation_evidence_id = "fixture-final-plan-reconciliation-evidence"
    reconciliation_evidence_sha = corpus.add_support(
        reconciliation_evidence_filename,
        {
            "evidence_id": reconciliation_evidence_id,
            "reconciliation_status": (
                "CONTENT_RECONCILED_PENDING_DURABILITY"
            ),
            "notice": NOTICE,
        },
        "Synthetic evidence for the final actual/variance reconciliation.",
    )
    reconciliation_evidence_ref = artifact_ref(
        reconciliation_evidence_id,
        "ylx.synthetic-reconciliation-evidence.v1",
        reconciliation_evidence_sha,
        (
            "contracts/fixtures/governance-models/support/"
            f"{reconciliation_evidence_filename}"
        ),
        1,
    )
    error_calculator_filename = "final-plan-forecast-error-calculator.json"
    error_calculator_id = "fixture-final-plan-forecast-error-calculator"
    error_calculator_sha = corpus.add_support(
        error_calculator_filename,
        {
            "calculator_id": error_calculator_id,
            "version": "1.0.0",
            "notice": NOTICE,
        },
        "Synthetic exact calculator bytes for final forecast-error reconciliation.",
    )
    error_calculator_ref = artifact_ref(
        error_calculator_id,
        "ylx.forecast-error-calculator.v1",
        error_calculator_sha,
        (
            "contracts/fixtures/governance-models/support/"
            f"{error_calculator_filename}"
        ),
        1,
    )

    leaf_nodes = [
        node
        for node in h_wbs["nodes"]
        if node["node_kind"] == "EXECUTABLE_LEAF"
    ]
    if len(leaf_nodes) != len(h_wbs["nodes"]):
        raise AssertionError("synthetic final plan expects only executable leaves")
    actual_started_at = "2026-06-02T00:00:00Z"
    finalized_at = "2026-06-02T07:58:00Z"
    final_generated_at = "2026-06-02T07:59:30Z"
    publisher_started_at = "2026-06-02T07:59:50Z"
    published_at = "2026-06-02T08:00:00Z"
    task_actual_by_node_id: dict[str, Any] = {}
    task_variance_by_node_id: dict[str, Any] = {}
    publisher_task_actual: dict[str, Any] | None = None
    publisher_task_variance: dict[str, Any] | None = None
    for node in leaf_nodes:
        node_id = node["node_id"]
        planned_effort = {
            "value": node["effort_estimate"]["value"],
            "unit": node["effort_estimate"]["unit"],
        }
        planned_fixed_elapsed = {
            "value": node["fixed_elapsed_estimate"]["value"],
            "unit": node["fixed_elapsed_estimate"]["unit"],
        }
        task_actual = {
            "terminal_status": "COMPLETED",
            "actual_started_at": (
                publisher_started_at
                if node_id == publisher_task_node_id
                else actual_started_at
            ),
            "actual_finished_at": (
                published_at
                if node_id == publisher_task_node_id
                else finalized_at
            ),
            "actual_effort": copy.deepcopy(planned_effort),
            "actual_fixed_elapsed": copy.deepcopy(planned_fixed_elapsed),
            "actual_capacity_usage": [],
            "actual_hardware_usage": [],
            "actual_credential_window_usage": [],
            "actual_blocked_elapsed": {"value": 0.0, "unit": "hours"},
            "actual_wait_elapsed": {"value": 0.0, "unit": "hours"},
            "terminal_evidence_refs": (
                [] if node_id == publisher_task_node_id else [copy.deepcopy(terminal_evidence_ref)]
            ),
        }
        task_variance = {
            "planned_effort": copy.deepcopy(planned_effort),
            "actual_effort": copy.deepcopy(planned_effort),
            "effort_variance": {"value": 0.0, "unit": "hours"},
            "planned_fixed_elapsed": copy.deepcopy(planned_fixed_elapsed),
            "actual_fixed_elapsed": copy.deepcopy(planned_fixed_elapsed),
            "fixed_elapsed_variance": {"value": 0.0, "unit": "hours"},
        }
        if node_id == publisher_task_node_id:
            publisher_task_actual = task_actual
            publisher_task_variance = task_variance
        else:
            task_actual_by_node_id[node_id] = task_actual
            task_variance_by_node_id[node_id] = task_variance
    if publisher_task_actual is None or publisher_task_variance is None:
        raise AssertionError("final plan lacks its sole publisher leaf")

    milestone_variances = []
    for gate in ("M0", "M1", "M2", "M3", "M4", "M5"):
        milestone_variances.append(
            {
                "milestone_gate": gate,
                "planned_start": actual_started_at,
                "actual_start": actual_started_at,
                "start_variance": {"value": 0.0, "unit": "hours"},
                "planned_finish": finalized_at,
                "actual_finish": finalized_at,
                "finish_variance": {"value": 0.0, "unit": "hours"},
                "planned_effort": {"value": 8.0, "unit": "hours"},
                "actual_effort": {"value": 8.0, "unit": "hours"},
                "effort_variance": {"value": 0.0, "unit": "hours"},
                "planned_fixed_elapsed": {"value": 8.0, "unit": "hours"},
                "actual_fixed_elapsed": {"value": 8.0, "unit": "hours"},
                "fixed_elapsed_variance": {"value": 0.0, "unit": "hours"},
            }
        )

    final_reconciliation = {
        "status": "CONTENT_RECONCILED_PENDING_DURABILITY",
        "reconciliation_revision": 1,
        "predecessor_reconciliation_sha256": None,
        "planned_bundle_ref": copy.deepcopy(h_bundle_ref),
        "variance_baseline_bundle_sha256": h_bundle_sha,
        "accepted_forecast_history": [
            copy.deepcopy(h_forecast_ref),
            copy.deepcopy(f_forecast_ref),
        ],
        "task_node_count": len(h_wbs["nodes"]),
        "terminal_task_actual_count": len(task_actual_by_node_id),
        "planned_task_node_id_set_sha256": ascii_set_sha256(
            [node["node_id"] for node in h_wbs["nodes"]]
        ),
        "actual_task_node_id_set_sha256": ascii_set_sha256(
            list(task_actual_by_node_id)
        ),
        "task_actual_by_node_id": task_actual_by_node_id,
        "task_variance_by_node_id": task_variance_by_node_id,
        "milestone_variances": milestone_variances,
        "resource_window_variances": [],
        "dependency_critical_path": copy.deepcopy(
            h_forecast["dependency_critical_path"]
        ),
        "resource_levelled_driving_path": copy.deepcopy(
            h_forecast["resource_levelled_driving_path"]
        ),
        "forecast_error": {
            "calculation_method": (
                "Synthetic exact zero-error comparison across the accepted forecast chain."
            ),
            "calculator_ref": error_calculator_ref,
            "accepted_snapshot_errors": [
                {
                    "forecast_snapshot_ref": copy.deepcopy(forecast_ref),
                    "metrics": [
                        {
                            "metric_id": "fixed-elapsed-hours",
                            "planned_value": 8.0,
                            "actual_value": 8.0,
                            "error_value": 0.0,
                            "unit": "hours",
                        }
                    ],
                }
                for forecast_ref in (h_forecast_ref, f_forecast_ref)
            ],
        },
        "unmaterialized_scenario_dispositions": [],
        "variance_reason_by_id": {},
        "publisher_closure": {
            "task_node_id": publisher_task_node_id,
            "protocol": "EXTERNAL_CONTENT_ADDRESSED_PUBLICATION_READBACK_V1",
            "publication_receipt_schema": (
                "ylx.final-actual-variance-publication-receipt.v1"
            ),
            "readback_receipt_schema": (
                "ylx.final-actual-variance-readback-receipt.v1"
            ),
        },
        "reconciled_at": finalized_at,
        "evidence_refs": [reconciliation_evidence_ref],
    }
    f_bundle = copy.deepcopy(h_bundle)
    f_bundle.update(
        {
            "revision": 2,
            "predecessor_sha256": h_bundle_sha,
            "generated_at": final_generated_at,
            "source_refs": [copy.deepcopy(chain_source_ref)],
            "planning_gate": "M5",
            "detail_horizon": copy.deepcopy(f_detail_horizon),
            "artifacts": {
                "owner_assignment": f_owner_ref,
                "resource_calendar": f_calendar_ref,
                "delivery_wbs": f_wbs_ref,
                "forecast_snapshot": f_forecast_ref,
            },
            "bundle_kind": "FINAL_ACTUAL_VARIANCE",
            "final_actual_variance_reconciliation": final_reconciliation,
        }
    )
    bind_bundle_approvals(
        f_bundle,
        f_owner_ref,
        {
            "owner_assignment": f_owner_sha,
            "resource_calendar": f_calendar_sha,
            "delivery_wbs": f_wbs_sha,
            "forecast_snapshot": f_forecast_sha,
        },
        approved_at="2026-06-02T07:59:00Z",
        label="f-r2",
    )
    f_bundle_raw = canonical_bytes(f_bundle)
    f_bundle_sha = sha(f_bundle_raw)
    f_bundle_filename = (
        "final-actual-variance/"
        f"{f_bundle_sha}--delivery-planning-bundle.json"
    )
    observed_f_bundle_sha = corpus.add(
        "VALID-FINAL-ACTUAL-VARIANCE-BUNDLE-F-01",
        f_bundle_filename,
        "delivery-planning-bundle-v2.schema.json",
        f_bundle,
    )
    if observed_f_bundle_sha != f_bundle_sha:
        raise AssertionError("final bundle content-address calculation drift")
    f_bundle_locator = f"{fixture_prefix}{f_bundle_filename}"
    f_bundle_ref = artifact_ref(
        f_bundle["artifact_id"],
        f_bundle["schema"],
        f_bundle_sha,
        f_bundle_locator,
        2,
    )
    f_bundle_byte_length = len(f_bundle_raw)

    f_planning_state = {
        **planning_state,
        "owner": f_owner,
        "owner_sha": f_owner_sha,
        "owner_ref": f_owner_ref,
        "calendar": f_calendar,
        "calendar_sha": f_calendar_sha,
        "calendar_ref": f_calendar_ref,
        "wbs": f_wbs,
        "wbs_sha": f_wbs_sha,
        "wbs_ref": f_wbs_ref,
        "forecast": f_forecast,
        "forecast_sha": f_forecast_sha,
        "forecast_ref": f_forecast_ref,
        "bundle": f_bundle,
        "bundle_sha": f_bundle_sha,
        "bundle_ref": f_bundle_ref,
    }
    evaluation_state = build_execution_authorization_evaluation(
        corpus,
        f_planning_state,
        task_id=publisher_task_node_id,
        action_instance_id=action_instance_id,
        filename_slug="final-plan-publication-pass",
        authorization_binding_context_ref=m5_binding_context_ref,
        environment_class="governance-publication",
        phase_barrier_ids=["milestone-entry/M5"],
        additional_prerequisite_ref_by_kind={},
        actor_person_id="fixture-qa-evidence-owner-person",
        evaluated_at="2026-06-02T07:59:40Z",
    )
    common_authorization_tuple = {
        "execution_authorization_evaluation_ref": copy.deepcopy(
            evaluation_state["ref"]
        ),
        "action_instance_id": action_instance_id,
        "planned_action_input_sha256": evaluation_state["value"][
            "planned_action_input_sha256"
        ],
        "actor_person_id": evaluation_state["value"]["actor_person_id"],
    }
    publication_id_preimage = {
        "final_bundle_ref": copy.deepcopy(f_bundle_ref),
        "final_bundle_sha256": f_bundle_sha,
        **copy.deepcopy(common_authorization_tuple),
        "publisher_task_node_id": publisher_task_node_id,
        "publisher_task_actual": copy.deepcopy(publisher_task_actual),
        "publisher_task_variance": copy.deepcopy(publisher_task_variance),
    }
    publication = {
        "schema": "ylx.final-actual-variance-publication-receipt.v1",
        "receipt_id": (
            "final-actual-variance-publication-"
            f"{sha(canonical_bytes(publication_id_preimage))}"
        ),
        "final_bundle_ref": copy.deepcopy(f_bundle_ref),
        "final_bundle_sha256": f_bundle_sha,
        "final_bundle_locator": f_bundle_locator,
        "final_bundle_byte_length": f_bundle_byte_length,
        "canonical_encoding": "RFC8785-JSON-UTF8",
        **copy.deepcopy(common_authorization_tuple),
        "publisher_task_node_id": publisher_task_node_id,
        "publisher_task_actual": copy.deepcopy(publisher_task_actual),
        "publisher_task_variance": copy.deepcopy(publisher_task_variance),
        "create_if_absent": True,
        "operation_result": "CREATED_EXACT",
        "file_fsynced": True,
        "parent_directory_fsynced": True,
        "published_at": published_at,
        "artifact_metadata": metadata(),
    }
    publication_filename = "final-actual-variance-publication-receipt.json"
    publication_sha = corpus.add(
        "VALID-FINAL-ACTUAL-VARIANCE-PUBLICATION-RECEIPT-P-01",
        publication_filename,
        "final-actual-variance-publication-receipt-v1.schema.json",
        publication,
    )
    publication_ref = artifact_ref(
        publication["receipt_id"],
        publication["schema"],
        publication_sha,
        f"{fixture_prefix}{publication_filename}",
        None,
    )
    readback_id_preimage = {
        "publication_receipt_ref": copy.deepcopy(publication_ref),
        "final_bundle_ref": copy.deepcopy(f_bundle_ref),
        "final_bundle_sha256": f_bundle_sha,
    }
    readback = {
        "schema": "ylx.final-actual-variance-readback-receipt.v1",
        "receipt_id": (
            "final-actual-variance-readback-"
            f"{sha(canonical_bytes(readback_id_preimage))}"
        ),
        "publication_receipt_ref": publication_ref,
        "final_bundle_ref": copy.deepcopy(f_bundle_ref),
        "final_bundle_sha256": f_bundle_sha,
        "final_bundle_locator": f_bundle_locator,
        "final_bundle_byte_length": f_bundle_byte_length,
        "canonical_encoding": "RFC8785-JSON-UTF8",
        "observed_final_bundle_sha256": f_bundle_sha,
        "observed_final_bundle_byte_length": f_bundle_byte_length,
        "digest_match": True,
        "exact_bytes_match": True,
        "read_back_at": "2026-06-02T08:05:00Z",
        "artifact_metadata": metadata(),
    }
    readback_filename = "final-actual-variance-readback-receipt.json"
    readback_sha = corpus.add(
        "VALID-FINAL-ACTUAL-VARIANCE-READBACK-RECEIPT-R-01",
        readback_filename,
        "final-actual-variance-readback-receipt-v1.schema.json",
        readback,
    )
    corpus.relationships["final_actual_variance_durability"] = {
        "planned_bundle_ref": h_bundle_ref,
        "execution_authorization_evaluation_ref": evaluation_state["ref"],
        "final_bundle_ref": f_bundle_ref,
        "publication_receipt_ref": publication_ref,
        "publication_receipt_sha256": publication_sha,
        "readback_receipt_sha256": readback_sha,
    }
    return {
        "planned_bundle": h_bundle,
        "planned_bundle_ref": h_bundle_ref,
        "evaluation": evaluation_state,
        "final_bundle": f_bundle,
        "final_bundle_ref": f_bundle_ref,
        "publication": publication,
        "publication_ref": publication_ref,
        "readback": readback,
    }


def build_nonrecursive_governance_fixture_corpus(corpus: Corpus) -> dict[str, Any]:
    """Hash only digest-independent inputs, never release-bound context outputs."""

    included_paths = [
        "contracts/governance-schemas/binding-context-v2.schema.json",
        "contracts/governance-schemas/governance-common.schema.json",
        "contracts/fixtures/governance-models/generate_fixtures.py",
        "scripts/validate_governance_models.py",
    ]
    included_sha256_by_path = {
        path: sha((REPO_ROOT / path).read_bytes()) for path in included_paths
    }
    self_path = (
        "contracts/fixtures/governance-models/valid/"
        "governance-fixture-input-corpus.json"
    )
    excluded_release_bound_paths = [
        (
            "contracts/fixtures/governance-models/valid/"
            "binding-context-v2-m2-bootstrap.json"
        ),
        "contracts/fixtures/governance-models/valid/binding-context-v2-m2.json",
    ]
    value = {
        "schema": "ylx.governance-fixture-corpus.v1",
        "corpus_id": "fixture-governance-input-corpus-r1",
        "revision": 1,
        "corpus_kind": "DIGEST_INDEPENDENT_GENERATOR_INPUTS",
        "created_at": "2026-06-01T12:07:45Z",
        "included_artifact_sha256_by_path": included_sha256_by_path,
        "included_path_set_sha256": ascii_set_sha256(included_paths),
        "exclusion_rule": "EXCLUDE_RELEASE_BOUND_M2_CONTEXTS_AND_SELF",
        "excluded_release_bound_artifact_paths": excluded_release_bound_paths,
        "self_artifact_path": self_path,
        "contract_root_relation": "INPUTS_ONLY_NO_RELEASE_BOUND_OUTPUTS",
        "artifact_metadata": metadata(),
    }
    digest = corpus.add(
        "VALID-GOVERNANCE-FIXTURE-CORPUS-01",
        "governance-fixture-input-corpus.json",
        "governance-fixture-corpus-v1.schema.json",
        value,
    )
    return {
        "value": value,
        "sha": digest,
        "ref": artifact_ref(
            value["corpus_id"],
            value["schema"],
            digest,
            self_path,
            value["revision"],
        ),
    }


def build_context_and_release(
    corpus: Corpus,
    requirement_ids: list[str],
    m4_ids: list[str],
    gate_by_id: dict[str, str],
    closing_gate_by_id: dict[str, str],
    registry: dict[str, Any],
    history_state: dict[str, Any],
    foundation_state: dict[str, Any],
    planning_v2_state: dict[str, Any],
) -> None:
    """Build context, qualification, M4, issue, and signing-chain fixtures."""
    registry_raw = (REPO_ROOT / "docs" / "acceptance-requirements.yaml").read_bytes()
    registry_sha = sha(registry_raw)
    product_contract_path = "contracts/openapi/ylx-device-v2.openapi.yaml"
    product_contract_file = REPO_ROOT / product_contract_path
    if not product_contract_file.is_file() or product_contract_file.is_symlink():
        raise FileNotFoundError(
            "contract-release product artifact must be a regular non-symlink file: "
            f"{product_contract_path}"
        )
    qualification_contract_path = (
        "contracts/governance-schemas/governance-common.schema.json"
    )
    qualification_contract_file = REPO_ROOT / qualification_contract_path
    if (
        not qualification_contract_file.is_file()
        or qualification_contract_file.is_symlink()
    ):
        raise FileNotFoundError(
            "contract-release qualification artifact must be a regular non-symlink file: "
            f"{qualification_contract_path}"
        )

    product_inventory = {
        "package_id": "fixture-product-contract-package",
        "package_version": "2.0.0",
        "artifacts": [
            {
                "artifact_id": "fixture-product-api",
                "artifact_kind": "api",
                "artifact_version": "2.0.0",
                "artifact_path": product_contract_path,
                "artifact_sha256": sha(product_contract_file.read_bytes()),
            }
        ],
    }
    qualification_inventory = {
        "package_id": "fixture-qualification-governance-package",
        "package_version": "1.0.0",
        "artifacts": [
            {
                "artifact_id": "fixture-governance-schema-inventory",
                "artifact_kind": "schema",
                "artifact_version": "1.0.0",
                "artifact_path": qualification_contract_path,
                "artifact_sha256": sha(qualification_contract_file.read_bytes()),
            }
        ],
    }
    product_contract_sha = sha(canonical_bytes(product_inventory))
    qualification_contract_sha = sha(canonical_bytes(qualification_inventory))
    contract_release = {
        "schema": "ylx.contract-release.v1",
        "contract_release_id": "fixture-contract-release-r1",
        "release_version": 1,
        "predecessor_contract_release_sha256": None,
        "created_at": "2026-06-01T12:07:30Z",
        "product_contract_sha256": product_contract_sha,
        "qualification_governance_contract_sha256": qualification_contract_sha,
        "inventory": {
            "product_contract": product_inventory,
            "qualification_governance_contract": qualification_inventory,
        },
        "artifact_metadata": metadata(),
    }
    contract_release_sha = corpus.add(
        "VALID-CONTRACT-RELEASE-01",
        "contract-release.json",
        "contract-release-v1.schema.json",
        contract_release,
    )
    fixture_corpus = build_nonrecursive_governance_fixture_corpus(corpus)
    consumer_bootstrap = build_consumer_bootstrap(corpus, product_contract_sha)
    build_preview_decision_fixtures(corpus)
    m3_base_candidate_id = "fixture-base-candidate-001"
    candidate_id = "fixture-target-candidate-001"
    rendered_config_sha = corpus.add_support(
        "rendered-core-config.json",
        {
            "schema": "ylx.rendered-core-config.v1",
            "config_id": "fixture-rendered-core-config",
            "candidate_id": m3_base_candidate_id,
            "product_contract_sha256": product_contract_sha,
            "values": {"fixture-core-mode": "base"},
            "created_at": STAMP,
            "artifact_metadata": metadata(),
        },
        "Exact rendered core configuration retained from M3 B into M4 T.",
    )
    m3_base_bundle_sha = corpus.add_support(
        "m3-base-bundle.json",
        {
            "schema": "ylx.m3-base-bundle.v1",
            "bundle_id": "fixture-m3-base-bundle",
            "candidate_id": m3_base_candidate_id,
            "rendered_core_config_sha256": rendered_config_sha,
            "product_contract_sha256": product_contract_sha,
            "core_artifact_sha256_by_id": {
                "capture-core": sha("fixture-capture-core-bytes"),
                "pipeline-core": sha("fixture-pipeline-core-bytes"),
            },
            "created_at": STAMP,
            "artifact_metadata": metadata(),
        },
        "Exact M3 base bundle bytes for candidate-lineage validation.",
    )

    m2_context = {
        "schema": "ylx.binding-context.v1",
        "context_id": "fixture-binding-context-m2",
        "stage": "M2",
        "created_at": STAMP,
        "owner_role": "contract-owner",
        "reviewer_role": "release-owner",
        "lineage": lineage(),
        "body": {
            "contract_release_id": "fixture-contract-release-r1",
            "contract_release_sha256": contract_release_sha,
            "product_contract_sha256": product_contract_sha,
            "qualification_governance_contract_sha256": qualification_contract_sha,
            "fixture_corpus_sha256": sha("fixture-corpus-bootstrap"),
            "consumer_deployment_set_sha256": consumer_bootstrap[
                "deployment_set_sha256"
            ],
            "deployment_state": "target-disabled",
        },
        "artifact_metadata": metadata(),
    }
    m2_context_sha = corpus.add(
        "VALID-BINDING-CONTEXT-M2-01",
        "binding-context-m2.json",
        "binding-context-v1.schema.json",
        m2_context,
    )

    def partition_value(suffix: str, sample_offset: int) -> dict[str, Any]:
        training_key = {
            "source_acquisition_id": f"acquisition-training-{suffix}",
            "source_session_id": f"session-training-{suffix}",
            "scenario_id": "scenario-fixture",
            "fault_seed_id": "seed-training",
        }
        holdout_key = {
            "source_acquisition_id": f"acquisition-holdout-{suffix}",
            "source_session_id": f"session-holdout-{suffix}",
            "scenario_id": "scenario-fixture",
            "fault_seed_id": "seed-holdout",
        }
        training_group_id = f"training-group-{suffix}"
        holdout_group_id = f"holdout-group-{suffix}"
        training_sample_id = f"sample-{sample_offset}"
        holdout_sample_id = f"sample-{sample_offset + 1}"
        training_sample_sha = sha(f"sample-bytes-{sample_offset}")
        holdout_sample_sha = sha(f"sample-bytes-{sample_offset + 1}")
        training_source_sha = sha(f"source-bytes-{sample_offset}")
        holdout_source_sha = sha(f"source-bytes-{sample_offset + 1}")
        return {
            "schema": "ylx.data-partition.v1",
            "partition_id": f"fixture-partition-{suffix}",
            "frozen_at": STAMP,
            "grouping_rule_version": "fixture-grouping-v1",
            "grouping_rule_ref": artifact_ref("fixture-grouping-rule"),
            "strata": [
                {
                    "stratum_id": "fixture-stratum",
                    "selector_ref": artifact_ref("fixture-stratum-selector"),
                }
            ],
            "training_group_ids": [training_group_id],
            "holdout_group_ids": [holdout_group_id],
            "source_groups": [
                {
                    "group_id": training_group_id,
                    "partition_side": "training",
                    "stratum_ids": ["fixture-stratum"],
                    "group_key": training_key,
                    "group_key_sha256": sha(canonical_bytes(training_key)),
                    "source_digests": [training_source_sha],
                    "expanded_samples": [
                        {
                            "sample_id": training_sample_id,
                            "sample_kind": "frame",
                            "sample_sha256": training_sample_sha,
                        }
                    ],
                },
                {
                    "group_id": holdout_group_id,
                    "partition_side": "holdout",
                    "stratum_ids": ["fixture-stratum"],
                    "group_key": holdout_key,
                    "group_key_sha256": sha(canonical_bytes(holdout_key)),
                    "source_digests": [holdout_source_sha],
                    "expanded_samples": [
                        {
                            "sample_id": holdout_sample_id,
                            "sample_kind": "frame",
                            "sample_sha256": holdout_sample_sha,
                        }
                    ],
                },
            ],
            "disjointness_proof": {
                "calculator_version": "fixture-set-digest-v1",
                "training_group_set_sha256": sha(canonical_bytes([training_group_id])),
                "holdout_group_set_sha256": sha(canonical_bytes([holdout_group_id])),
                "training_sample_set_sha256": sha(
                    f"{training_sample_id}\tframe\t{training_sample_sha}\n"
                ),
                "holdout_sample_set_sha256": sha(
                    f"{holdout_sample_id}\tframe\t{holdout_sample_sha}\n"
                ),
                "training_source_digest_set_sha256": sha(
                    canonical_bytes([training_source_sha])
                ),
                "holdout_source_digest_set_sha256": sha(
                    canonical_bytes([holdout_source_sha])
                ),
                "group_id_intersection": [],
                "sample_id_intersection": [],
                "sample_digest_intersection": [],
                "source_digest_intersection": [],
            },
            "owner_role": "qa-evidence-owner",
            "reviewer_role": "security-owner",
            "artifact_metadata": metadata(),
        }

    partition_source = partition_value("source", 1)
    partition_source_sha = corpus.add(
        "VALID-DATA-PARTITION-01",
        "data-partition.json",
        "data-partition-v1.schema.json",
        partition_source,
    )
    partition_target = partition_value("target", 101)
    partition_target_sha = corpus.add(
        "VALID-DATA-PARTITION-TARGET-01",
        "data-partition-target.json",
        "data-partition-v1.schema.json",
        partition_target,
    )

    deployment_record_sha = sha("fixture-deployment-record")

    def execution_context_value(
        context_id: str, partition_id: str, partition_digest: str, suffix: str
    ) -> dict[str, Any]:
        return {
            "schema": "ylx.execution-context.v1",
            "context_id": context_id,
            "created_at": STAMP,
            "run_id": f"fixture-run-{suffix}",
            "route_id": "fixture-route-e3",
            "environment_refs": [
                {
                    "environment_id": "fixture-no-production-environment",
                    "environment_kind": "synthetic-lab",
                    "hardware_id": "fixture-hardware",
                    "camera_id": "fixture-camera",
                    "media_id": "fixture-media",
                    "firmware_id": "fixture-firmware",
                    "os_id": "fixture-os",
                    "runtime_id": "fixture-runtime",
                    "artifact_path": "contracts/fixtures/governance-models/support/environment.json",
                    "artifact_sha256": sha("fixture-environment"),
                }
            ],
            "support_cell_refs": [
                {
                    "support_cell_id": "fixture-support-cell",
                    "artifact_path": "contracts/fixtures/governance-models/support/support-cell.json",
                    "artifact_sha256": sha("fixture-support-cell"),
                }
            ],
            "deployment_refs": [
                {
                    "deployment_id": "fixture-deployment",
                    "component_id": "web",
                    "deployment_state": "synthetic-observed",
                    "artifact_path": "contracts/fixtures/governance-models/support/deployment.json",
                    "deployment_record_sha256": deployment_record_sha,
                    "rendered_config_sha256": rendered_config_sha,
                }
            ],
            "data_partition_refs": [
                {
                    "partition_id": partition_id,
                    "artifact_path": f"contracts/fixtures/governance-models/valid/data-partition{suffix}.json",
                    "artifact_sha256": partition_digest,
                }
            ],
            "artifact_metadata": metadata(),
        }

    execution_source = execution_context_value(
        "fixture-execution-context-source",
        "fixture-partition-source",
        partition_source_sha,
        "",
    )
    execution_source_sha = corpus.add(
        "VALID-EXECUTION-CONTEXT-01",
        "execution-context.json",
        "execution-context-v1.schema.json",
        execution_source,
    )
    execution_target = execution_context_value(
        "fixture-execution-context-target",
        "fixture-partition-target",
        partition_target_sha,
        "-target",
    )
    execution_target_sha = corpus.add(
        "VALID-EXECUTION-CONTEXT-TARGET-01",
        "execution-context-target.json",
        "execution-context-v1.schema.json",
        execution_target,
    )

    def qualification_plan_value(
        plan_id: str,
        plan_candidate_id: str,
        revision: int,
        predecessor: str | None,
        partition_id: str,
        partition_digest: str,
        execution_id: str,
        execution_digest: str,
        suffix: str,
    ) -> dict[str, Any]:
        return {
            "schema": "ylx.qualification-plan.v1",
            "plan_id": plan_id,
            "candidate_id": plan_candidate_id,
            "binding_context_ref": context_ref(
                "fixture-binding-context-m2", m2_context_sha, "M2"
            ),
            "contract_release_sha256": contract_release_sha,
            "product_contract_sha256": product_contract_sha,
            "qualification_governance_contract_sha256": qualification_contract_sha,
            "qualification_revision": revision,
            "predecessor_plan_sha256": predecessor,
            "frozen_at": STAMP,
            "applicable_requirement_ids": m4_ids,
            "applicable_support_cell_ids": ["fixture-support-cell"],
            "stimulus": {
                "stimulus_id": f"fixture-stimulus-{suffix}",
                "protocol_ref": artifact_ref("fixture-procedure"),
                "input_refs": [artifact_ref(f"fixture-input-{suffix}")],
            },
            "oracle_type": "fitted",
            "oracle": {
                "oracle_domain": "latency",
                "metric_ids": ["fixture-latency-ms"],
                "estimator_ref": artifact_ref("fixture-estimator"),
                "evaluation_protocol_ref": artifact_ref("fixture-evaluation-protocol"),
                "holdout_policy": "validation-only-no-retuning",
            },
            "repetitions": 3,
            "exclusion_rules": [],
            "aggregation_method_ref": artifact_ref("fixture-aggregation-method"),
            "statistical_method_ref": artifact_ref("fixture-statistical-method"),
            "threshold_refs": [
                {
                    "threshold_id": "fixture-threshold",
                    "threshold_kind": "data-fitted",
                    "artifact_ref": artifact_ref("fixture-threshold"),
                }
            ],
            "data_partition_ref": {
                "partition_id": partition_id,
                "artifact_path": f"contracts/fixtures/governance-models/valid/data-partition{suffix}.json",
                "artifact_sha256": partition_digest,
            },
            "data_partition_execution_context_refs": [
                {
                    "context_id": execution_id,
                    "artifact_path": f"contracts/fixtures/governance-models/valid/execution-context{suffix}.json",
                    "artifact_sha256": execution_digest,
                }
            ],
            "owner_role": "qa-evidence-owner",
            "reviewer_role": "contract-owner",
            "artifact_metadata": metadata(),
        }

    qualification_m3 = qualification_plan_value(
        "fixture-m3-qualification-plan",
        m3_base_candidate_id,
        1,
        None,
        "fixture-partition-source",
        partition_source_sha,
        "fixture-execution-context-source",
        execution_source_sha,
        "-m3",
    )
    qualification_m3_sha = corpus.add(
        "VALID-QUALIFICATION-PLAN-M3-01",
        "qualification-plan-m3.json",
        "qualification-plan-v1.schema.json",
        qualification_m3,
    )
    qualification_source = qualification_plan_value(
        "fixture-qualification-plan",
        candidate_id,
        1,
        None,
        "fixture-partition-source",
        partition_source_sha,
        "fixture-execution-context-source",
        execution_source_sha,
        "",
    )
    qualification_source_sha = corpus.add(
        "VALID-QUALIFICATION-PLAN-01",
        "qualification-plan.json",
        "qualification-plan-v1.schema.json",
        qualification_source,
    )
    qualification_target = qualification_plan_value(
        "fixture-qualification-plan",
        candidate_id,
        2,
        qualification_source_sha,
        "fixture-partition-target",
        partition_target_sha,
        "fixture-execution-context-target",
        execution_target_sha,
        "-target",
    )
    qualification_target_sha = corpus.add(
        "VALID-QUALIFICATION-PLAN-TARGET-01",
        "qualification-plan-target.json",
        "qualification-plan-v1.schema.json",
        qualification_target,
    )

    m3_context = {
        "schema": "ylx.binding-context.v1",
        "context_id": "fixture-binding-context-m3",
        "stage": "M3",
        "created_at": STAMP,
        "owner_role": "build-platform-owner",
        "reviewer_role": "qa-evidence-owner",
        "lineage": lineage(),
        "body": {
            "candidate_id": m3_base_candidate_id,
            "base_bundle_sha256": m3_base_bundle_sha,
            "rendered_core_config_sha256": rendered_config_sha,
            "contract_release_sha256": contract_release_sha,
            "product_contract_sha256": product_contract_sha,
            "qualification_governance_contract_sha256": qualification_contract_sha,
            "qualification_revision": 1,
            "qualification_plan_sha256": qualification_m3_sha,
        },
        "artifact_metadata": metadata(),
    }
    m3_context_sha = corpus.add(
        "VALID-BINDING-CONTEXT-M3-01",
        "binding-context-m3.json",
        "binding-context-v1.schema.json",
        m3_context,
    )

    sub_bundle_digests: dict[str, str] = {}
    for component in COMPONENTS:
        sub_bundle = {
            "schema": "ylx.m4-component-sub-bundle.v1",
            "component_id": component,
            "artifact_refs": [artifact_ref(f"fixture-{component}-artifact")],
            "config_refs": [artifact_ref(f"fixture-{component}-config")],
            "import_contract_refs": [],
            "export_contract_refs": [artifact_ref(f"fixture-{component}-export")],
            "lineage_sha256": sha(f"fixture-{component}-lineage"),
        }
        sub_bundle_digests[component] = corpus.add(
            f"VALID-M4-SUB-BUNDLE-{component.upper().replace('-', '_')}-01",
            f"m4-component-sub-bundle-{component}.json",
            "m4-component-sub-bundle-v1.schema.json",
            sub_bundle,
        )

    base_core_inputs = {
        "base-bundle": m3_context["body"]["base_bundle_sha256"],
        "rendered-core-config": rendered_config_sha,
        "product-contract": product_contract_sha,
    }
    shared_product_inputs = {"common-lineage": sha("fixture-common-lineage")}
    base_core_input_projection = {
        "source_core_input_sha256_by_id": base_core_inputs,
        "target_core_input_sha256_by_id": dict(base_core_inputs),
        "added_product_input_sha256_by_id": {
            **sub_bundle_digests,
            **shared_product_inputs,
        },
        "removed_core_input_ids": [],
        "changed_core_input_ids": [],
        "unexplained_input_ids": [],
        "source_rendered_core_config_sha256": rendered_config_sha,
        "target_rendered_core_config_projection_sha256": rendered_config_sha,
        "source_product_contract_sha256": product_contract_sha,
        "target_product_contract_sha256": product_contract_sha,
        "projection_algorithm_sha256": sha("ylx.base-core-input-projection.v1"),
        "all_core_inputs_equal": True,
    }
    base_core_input_projection_sha = sha(canonical_bytes(base_core_input_projection))
    candidate_identity_inputs = {
        "predecessor_candidate_id": m3_base_candidate_id,
        "base_bundle_sha256": m3_context["body"]["base_bundle_sha256"],
        "base_core_input_projection_sha256": base_core_input_projection_sha,
        "component_sub_bundle_sha256_by_id": sub_bundle_digests,
        "rendered_config_sha256": rendered_config_sha,
        "product_contract_sha256": product_contract_sha,
        "shared_input_sha256_by_id": shared_product_inputs,
    }
    assembly = {
        "schema": "ylx.m4-candidate-assembly.v1",
        "candidate_id": candidate_id,
        "predecessor_candidate_id": m3_base_candidate_id,
        "component_sub_bundle_sha256_by_id": sub_bundle_digests,
        "rendered_config_sha256": rendered_config_sha,
        "product_contract_sha256": product_contract_sha,
        "m3_binding_context_ref": context_ref(
            "fixture-binding-context-m3", m3_context_sha, "M3"
        ),
        "base_bundle_sha256": m3_context["body"]["base_bundle_sha256"],
        "base_core_input_projection": base_core_input_projection,
        "shared_input_sha256_by_id": shared_product_inputs,
        "integration_smoke_sha256": sha("fixture-integration-smoke"),
        "candidate_identity_input_sha256": sha(
            canonical_bytes(candidate_identity_inputs)
        ),
    }
    assembly_sha = corpus.add(
        "VALID-M4-CANDIDATE-ASSEMBLY-01",
        "m4-candidate-assembly.json",
        "m4-candidate-assembly-v1.schema.json",
        assembly,
    )

    cross_ids = [
        "M0-MEAS-07",
        "M0-MEAS-08",
        "M4-CANDIDATE-ASSEMBLY-01",
        "SAFE-SWAP-INTEGRATED-01",
        "RUNTIME-SERVICE-01",
        "CONSUMER-ATTESTATION-01",
        "SEC-PROFILE-01",
        "SEC-LAB-01",
        "SEC-PATH-01",
        "SEC-SECRET-01",
        "WEB-SAFE-SWAP-01",
        "M4-ISSUES-01",
    ]
    assert set(cross_ids) == {rid for rid in m4_ids if gate_by_id[rid] == "M4"}
    component_for_gate = {
        "M4a": "web",
        "M4b": "network",
        "M4c": "preview",
        "M4d": "transfer-calibration",
    }
    qualification_input_ids = [
        "qualification-plan",
        "procedure",
        "threshold",
        "statistical-method",
        "repetition-policy",
        "data-partition",
        "support-cell",
    ]
    qualification_inputs: dict[str, Any] = {}
    qualification_kinds = {
        "qualification-plan": (
            "QUALIFICATION_PLAN",
            "DATA_FITTED",
            [rid for rid in m4_ids if gate_by_id[rid] == "M4d"],
        ),
        "procedure": ("PROCEDURE", "DATA_FITTED", m4_ids),
        "threshold": ("THRESHOLD", "DATA_FITTED", m4_ids),
        "statistical-method": ("STATISTICAL_METHOD", "DATA_FITTED", m4_ids),
        "repetition-policy": ("REPETITION_COUNT", "DATA_FITTED", m4_ids),
        "data-partition": (
            "DATA_PARTITION",
            "DATA_FITTED",
            [rid for rid in m4_ids if gate_by_id[rid] == "M4d"],
        ),
        "support-cell": ("SUPPORT_CELL", "EXECUTION_COVERAGE", m4_ids),
    }
    for input_id, (input_kind, oracle_class, covered_rows) in qualification_kinds.items():
        qualification_inputs[input_id] = {
            "input_kind": input_kind,
            "oracle_class": oracle_class,
            "component_ids": COMPONENTS,
            "requirement_ids": covered_rows,
            "change_policy": "REQUIREMENT_DEPENDENCY_CLOSURE",
        }

    edge_id = "edge-network-to-web"
    component_nodes: dict[str, Any] = {}
    for component in COMPONENTS:
        gate = {
            "web": "M4a",
            "network": "M4b",
            "preview": "M4c",
            "transfer-calibration": "M4d",
        }[component]
        component_nodes[component] = {
            "component_id": component,
            "owned_requirement_ids": [rid for rid in m4_ids if gate_by_id[rid] == gate],
            "shared_input_ids": ["product-contract", "contract-release"],
            "qualification_input_ids": qualification_input_ids,
            "inbound_dependency_edge_ids": [edge_id] if component == "web" else [],
            "outbound_dependency_edge_ids": [edge_id] if component == "network" else [],
        }

    row_bindings: dict[str, Any] = {}
    for requirement_id in m4_ids:
        gate = gate_by_id[requirement_id]
        component_ids = (
            COMPONENTS if gate == "M4" else [component_for_gate[gate]]
        )
        row_qualification_inputs = [
            qid
            for qid in qualification_input_ids
            if requirement_id in qualification_inputs[qid]["requirement_ids"]
        ]
        row_bindings[requirement_id] = {
            "closing_gate": gate,
            "component_ids": component_ids,
            "shared_input_ids": ["product-contract", "contract-release"],
            "qualification_input_ids": row_qualification_inputs,
            "dependency_edge_ids": [edge_id] if requirement_id in cross_ids else [],
            "requirement_dependency_ids": [],
            "binding_scope": "CROSS_COMPONENT" if gate == "M4" else "COMPONENT",
            "oracle_qualification_input_ids": row_qualification_inputs,
            "applicability_selector_qualification_input_id": None,
            "change_policy": "BLOCKED_ALL_UP" if gate == "M4" else "SELECTIVE_REQUALIFICATION",
        }

    graph = {
        "schema": "ylx.m4-component-impact-graph.v1",
        "graph_id": "fixture-m4-impact-graph-r1",
        "graph_revision": 1,
        "predecessor_graph_sha256": None,
        "created_at": STAMP,
        "artifact_path": "contracts/fixtures/governance-models/valid/m4-component-impact-graph.json",
        "artifact_metadata": metadata(),
        "qualification_governance_contract_sha256": qualification_contract_sha,
        "contract_release_sha256": contract_release_sha,
        "registry_sha256": registry_sha,
        "registry_requirement_ids": m4_ids,
        "component_nodes": component_nodes,
        "shared_inputs": {
            "product-contract": {
                "input_kind": "PRODUCT_CONTRACT",
                "component_ids": COMPONENTS,
                "requirement_ids": m4_ids,
                "change_policy": "BLOCKED_ALL_UP",
            },
            "contract-release": {
                "input_kind": "CONTRACT_RELEASE",
                "component_ids": COMPONENTS,
                "requirement_ids": m4_ids,
                "change_policy": "BLOCKED_ALL_UP",
            }
        },
        "qualification_inputs": qualification_inputs,
        "dependency_edges": [
            {
                "edge_id": edge_id,
                "source_component_id": "network",
                "target_component_id": "web",
                "dependency_type": "RUNTIME",
                "requirement_ids": cross_ids,
                "change_policy": "REVERSE_CLOSURE",
            }
        ],
        "row_bindings": row_bindings,
        "requirement_dependencies": [],
        "cross_component_requirement_ids": cross_ids,
        "validation_outcome": {
            "outcome": "VALID",
            "selected_requirement_count": 72,
            "invalidated_requirement_ids": [],
            "rebase_disposition": "MAY_EVALUATE",
            "accepted_rebase_wrapper_count": 0,
            "complete_all_up_required": False,
            "diagnostics": [],
        },
        "approvals": [approval("contract-owner")],
    }
    graph_sha = corpus.add(
        "VALID-M4-COMPONENT-IMPACT-GRAPH-01",
        "m4-component-impact-graph.json",
        "m4-component-impact-graph-v1.schema.json",
        graph,
    )

    source_qualification_inputs = {
        "qualification-plan": qualification_source_sha,
        "procedure": sha("fixture-procedure"),
        "threshold": sha("fixture-threshold"),
        "statistical-method": sha("fixture-statistical-method"),
        "repetition-policy": sha("fixture-repetition-policy"),
        "data-partition": partition_source_sha,
        "support-cell": sha("fixture-support-cell"),
    }
    target_qualification_inputs = dict(source_qualification_inputs)
    target_qualification_inputs["qualification-plan"] = qualification_target_sha
    target_qualification_inputs["data-partition"] = partition_target_sha

    def m4_context_value(
        context_id: str,
        revision: int,
        plan_sha: str,
        qualification_inputs_map: dict[str, str],
    ) -> dict[str, Any]:
        return {
            "schema": "ylx.binding-context.v1",
            "context_id": context_id,
            "stage": "M4",
            "created_at": STAMP,
            "owner_role": "build-platform-owner",
            "reviewer_role": "qa-evidence-owner",
            "lineage": lineage(),
            "body": {
                "candidate_id": candidate_id,
                "predecessor_candidate_id": m3_base_candidate_id,
                "product_assembly_sha256": assembly_sha,
                "m3_binding_context_sha256": m3_context_sha,
                "base_core_input_projection_sha256": base_core_input_projection_sha,
                "qualification_bundle_sha256": sha(f"fixture-qualification-bundle-r{revision}"),
                "rendered_config_sha256": rendered_config_sha,
                "contract_release_sha256": contract_release_sha,
                "product_contract_sha256": product_contract_sha,
                "qualification_governance_contract_sha256": qualification_contract_sha,
                "qualification_revision": revision,
                "qualification_plan_sha256": plan_sha,
                "qualification_input_sha256_by_id": qualification_inputs_map,
                "component_impact_graph_sha256": graph_sha,
            },
            "artifact_metadata": metadata(),
        }

    m4_source = m4_context_value(
        "fixture-binding-context-m4-source",
        1,
        qualification_source_sha,
        source_qualification_inputs,
    )
    m4_source_sha = corpus.add(
        "VALID-BINDING-CONTEXT-M4-01",
        "binding-context-m4.json",
        "binding-context-v1.schema.json",
        m4_source,
    )
    m4_target = m4_context_value(
        "fixture-binding-context-m4-target",
        2,
        qualification_target_sha,
        target_qualification_inputs,
    )
    m4_target_sha = corpus.add(
        "VALID-BINDING-CONTEXT-M4-TARGET-01",
        "binding-context-m4-target.json",
        "binding-context-v1.schema.json",
        m4_target,
    )

    stage_source_scopes = build_stage_source_scope_fixtures(
        corpus, planning_v2_state, history_state
    )
    measurement_threshold_state = build_measurement_threshold_fixtures(
        corpus,
        planning_v2_state,
        stage_source_scopes["refs"]["M1"],
    )
    decision_head_state = history_state["decision_head"]
    decision_head_value = decision_head_state["value"]
    mapping_ratification = foundation_state["mapping_ratification"]
    m2_implementation_environment_filename = (
        "environment-m2-target-disabled-non-production.json"
    )
    m2_implementation_environment_id = (
        "fixture-environment-m2-target-disabled-non-production"
    )
    m2_implementation_environment_sha = corpus.add_support(
        m2_implementation_environment_filename,
        {
            "environment_id": m2_implementation_environment_id,
            "environment_class": "target-disabled-non-production",
            "production_writer_enabled": False,
            "customer_visible": False,
            "notice": NOTICE,
        },
        "Synthetic target-disabled M2 implementation environment authority.",
    )
    m2_implementation_environment_ref = artifact_ref(
        m2_implementation_environment_id,
        "ylx.execution-environment.v1",
        m2_implementation_environment_sha,
        (
            "contracts/fixtures/governance-models/support/"
            f"{m2_implementation_environment_filename}"
        ),
        1,
    )
    m2_bootstrap_frozen_input_refs = {
        "acceptance_registry": {
            "ref_id": "fixture-acceptance-registry",
            "authority_kind": "contract-package",
            "locator": "docs/acceptance-requirements.yaml",
            "sha256": sha(
                (REPO_ROOT / "docs" / "acceptance-requirements.yaml").read_bytes()
            ),
        },
        "decision_history_head": artifact_ref(
            decision_head_value["record_id"],
            decision_head_value["schema"],
            decision_head_state["sha256"],
            decision_head_state["fixture_path"],
            decision_head_value["history_revision"],
        ),
        "policy_authority": copy.deepcopy(
            planning_v2_state["g0_policy"]["ratification_ref"]
        ),
        "system_feature_mapping_ratification": artifact_ref(
            mapping_ratification["ratification_id"],
            mapping_ratification["schema"],
            foundation_state["mapping_ratification_sha"],
            "valid/system-feature-mapping-ratification.json",
            mapping_ratification["revision"],
        ),
    }
    m2_bootstrap_creation_evaluation = build_execution_authorization_evaluation(
        corpus,
        planning_v2_state,
        task_id=planning_v2_state["m2_bootstrap_node_id_by_action"][
            "produce-governance-input"
        ],
        action_instance_id="fixture-action-create-m2-bootstrap-context",
        filename_slug="m2-produce-governance-input-pass",
        authorization_binding_context_ref=None,
        environment_class="governance-workspace",
        phase_barrier_ids=["milestone-entry/M2"],
        actor_person_id="fixture-contract-owner-person",
        additional_prerequisite_ref_by_kind={
            "stage_source_scope": copy.deepcopy(stage_source_scopes["refs"]["M1"]),
            **copy.deepcopy(m2_bootstrap_frozen_input_refs),
            "m2_implementation_environment": copy.deepcopy(
                m2_implementation_environment_ref
            ),
        },
        evaluated_at="2026-06-01T12:05:00Z",
    )
    context_v2_input_state = {
        "m2_context": m2_context,
        "m3_context": m3_context,
        "m4_context": m4_target,
        "assembly": assembly,
        "assembly_sha": assembly_sha,
        "m3_base_bundle_sha": m3_base_bundle_sha,
        "candidate_id": candidate_id,
        "qualification_source_sha": qualification_source_sha,
        "qualification_target_sha": qualification_target_sha,
        "planning_v2": planning_v2_state,
        "contract_release_sha": contract_release_sha,
        "product_contract_sha": product_contract_sha,
        "qualification_contract_sha": qualification_contract_sha,
        "stage_source_scopes": stage_source_scopes,
        "m2_bootstrap_creation_evaluation": m2_bootstrap_creation_evaluation,
        "m2_bootstrap_frozen_input_refs": m2_bootstrap_frozen_input_refs,
        "m2_implementation_environment_ref": m2_implementation_environment_ref,
        "fixture_corpus": fixture_corpus,
        "consumer_deployment_set": consumer_bootstrap["deployment_set"],
        "consumer_deployment_set_ref": consumer_bootstrap["deployment_set_ref"],
        "consumer_deployment_records": consumer_bootstrap["deployment_records"],
        "consumer_deployment_record_ref_by_boundary": consumer_bootstrap[
            "deployment_record_ref_by_boundary"
        ],
        "consumer_deployment_record_ref_by_action_and_boundary": (
            consumer_bootstrap[
                "deployment_record_ref_by_action_and_boundary"
            ]
        ),
    }
    context_v2_state = build_context_lineage_and_projection_v2(
        corpus,
        context_v2_input_state,
        {},
        "",
        context_only=True,
    )
    measurement_holdout_state = build_measurement_holdout_evidence_fixtures(
        corpus,
        planning_v2_state,
        measurement_threshold_state,
        context_v2_state,
    )
    final_actual_variance_state = (
        build_final_actual_variance_durability_fixtures(
            corpus,
            planning_v2_state,
            context_v2_state["m5_ref"],
        )
    )
    base_evidence_evaluation = build_execution_authorization_evaluation(
        corpus,
        planning_v2_state,
        task_id=planning_v2_state["all_evidence_node_id"],
        action_instance_id="fixture-action-qualification-evidence-all",
        filename_slug="qualification-evidence-all-pass",
        authorization_binding_context_ref=context_v2_state["m4_r2_ref"],
        environment_class="qualification-target",
        phase_barrier_ids=["m4-start/qualification"],
        additional_prerequisite_ref_by_kind={
            "m3_binding_context": copy.deepcopy(context_v2_state["m3_ref"]),
            "m4_binding_context": copy.deepcopy(context_v2_state["m4_r2_ref"]),
            "assembly": artifact_ref(
                assembly["candidate_id"],
                assembly["schema"],
                assembly_sha,
                (
                    "contracts/fixtures/governance-models/valid/"
                    "m4-candidate-assembly.json"
                ),
                1,
            ),
            "m4_target_deployment": copy.deepcopy(
                context_v2_state["m4_target_deployment_ref"]
            ),
            **{
                f"deployment-receipt-{key}": copy.deepcopy(ref)
                for key, ref in context_v2_state[
                    "m4_deployment_receipts"
                ].items()
            },
        },
    )

    evidence_record_sha = corpus.add_support(
        "evidence-all.json",
        {
            "evidence_id": "fixture-evidence-all",
            "created_at": "2026-06-01T12:18:00Z",
            "requirement_ids": requirement_ids,
            "authorization_binding_context_ref": copy.deepcopy(
                context_v2_state["m4_r2_ref"]
            ),
            "execution_authorization_evaluation_ref": copy.deepcopy(
                base_evidence_evaluation["ref"]
            ),
            "action_instance_id": base_evidence_evaluation["value"][
                "action_instance_id"
            ],
            "planned_action_input_sha256": base_evidence_evaluation["value"][
                "planned_action_input_sha256"
            ],
            "actor_person_id": base_evidence_evaluation["value"]["actor_person_id"],
            "authorization_action": base_evidence_evaluation["value"][
                "authorization_action"
            ],
            "authorization_environment_class": base_evidence_evaluation["value"][
                "authorization_environment_class"
            ],
            "notice": NOTICE,
        },
        "Exact synthetic all-requirements action evidence bytes.",
    )
    evidence = {
        "schema": "ylx.evidence-binding.v1",
        "binding_id": "fixture-evidence-binding",
        "created_at": "2026-06-01T12:20:00Z",
        "binding_context_ref": {
            "context_id": context_v2_state["m4_r2_ref"]["artifact_id"],
            "artifact_path": context_v2_state["m4_r2_ref"]["artifact_path"],
            "artifact_sha256": context_v2_state["m4_r2_ref"]["artifact_sha256"],
        },
        "execution_context_refs": [
            {
                "context_id": "fixture-execution-context-source",
                "artifact_path": "contracts/fixtures/governance-models/valid/execution-context.json",
                "artifact_sha256": execution_source_sha,
            }
        ],
        "required_execution_context_ids": ["fixture-execution-context-source"],
        "evidence_records": [
            {
                "evidence_id": "fixture-evidence-all",
                "evidence_record_kind": "component-actor-receipt",
                "artifact_path": "contracts/fixtures/governance-models/support/evidence-all.json",
                "artifact_sha256": evidence_record_sha,
                "execution_context_ids": ["fixture-execution-context-source"],
                "actor_deployment_record_sha256": deployment_record_sha,
                "execution_authorization_evaluation_ref": copy.deepcopy(
                    base_evidence_evaluation["ref"]
                ),
                "action_instance_id": base_evidence_evaluation["value"][
                    "action_instance_id"
                ],
                "planned_action_input_sha256": base_evidence_evaluation["value"][
                    "planned_action_input_sha256"
                ],
            }
        ],
        "reverse_coverage": [
            {
                "requirement_id": requirement_id,
                "execution_context_id": "fixture-execution-context-source",
                "evidence_ids": ["fixture-evidence-all"],
            }
            for requirement_id in requirement_ids
        ],
        "artifact_metadata": metadata(),
    }
    evidence_sha = corpus.add(
        "VALID-EVIDENCE-BINDING-01",
        "evidence-binding.json",
        "evidence-binding-v1.schema.json",
        evidence,
    )

    def equal_pair(digest: str) -> dict[str, Any]:
        return {"source_sha256": digest, "target_sha256": digest, "equal": True}

    component_diff = {
        component: {
            "source_sub_bundle_sha256": sub_bundle_digests[component],
            "target_sub_bundle_sha256": sub_bundle_digests[component],
            "changed": False,
        }
        for component in COMPONENTS
    }
    qualification_diff = {
        input_id: {
            "source_sha256": source_qualification_inputs[input_id],
            "target_sha256": target_qualification_inputs[input_id],
            "changed": source_qualification_inputs[input_id]
            != target_qualification_inputs[input_id],
        }
        for input_id in qualification_input_ids
    }
    affected = [rid for rid in m4_ids if gate_by_id[rid] == "M4d"]
    target_requirement_id = next(rid for rid in m4_ids if gate_by_id[rid] == "M4a")
    source_evidence_ref = artifact_ref(
        "fixture-evidence-binding",
        "ylx.evidence-binding.v1",
        evidence_sha,
        "contracts/fixtures/governance-models/valid/evidence-binding.json",
    )
    source_verdict_id = "fixture-source-web-verdict"
    source_verdict_filename = "m4-source-web-verdict.json"
    source_verdict_sha = corpus.add_support(
        source_verdict_filename,
        {
            "schema": "ylx.stage-terminal-result.v1",
            "result_id": source_verdict_id,
            "revision": 1,
            "predecessor_result_sha256": None,
            "requirement_id": target_requirement_id,
            "candidate_id": candidate_id,
            "binding_context_ref": context_ref(
                "fixture-binding-context-m4-source", m4_source_sha, "M4"
            ),
            "effective_result": "PASS",
            "applicability_outcome": "APPLICABLE",
            "evidence_binding_refs": [source_evidence_ref],
            "issued_at": STAMP,
            "artifact_metadata": metadata(),
        },
        "Exact source terminal verdict bytes consumed by the M4 rebase wrapper.",
    )
    equality_proof = {
        "product_contract": equal_pair(product_contract_sha),
        "qualification_governance_contract": equal_pair(qualification_contract_sha),
        "contract_release": equal_pair(contract_release_sha),
        "m3_binding_context": equal_pair(m3_context_sha),
        "common_lineage": equal_pair(sha("fixture-common-lineage")),
        "component_impact_graph": equal_pair(graph_sha),
        "target_requirement_inputs": {
            "product-contract": equal_pair(product_contract_sha),
            "contract-release": equal_pair(contract_release_sha),
        },
        "target_requirement_qualification_inputs": {
            input_id: equal_pair(source_qualification_inputs[input_id])
            for input_id in row_bindings[target_requirement_id][
                "qualification_input_ids"
            ]
        },
        "dependency_closure_requirement_ids": [target_requirement_id],
        "all_equal": True,
    }
    equality_proof["proof_sha256"] = sha(canonical_bytes(equality_proof))
    rebase = {
        "schema": "ylx.m4-verdict-rebase.v1",
        "rebase_id": "fixture-m4-rebase-web",
        "revision": 1,
        "predecessor_sha256": None,
        "created_at": STAMP,
        "artifact_path": "contracts/fixtures/governance-models/valid/m4-verdict-rebase.json",
        "artifact_metadata": metadata(),
        "target_requirement_id": target_requirement_id,
        "source_candidate_id": candidate_id,
        "target_candidate_id": candidate_id,
        "source_qualification_revision": 1,
        "target_qualification_revision": 2,
        "source_effective_verdict": "PASS",
        "source_applicability_outcome": "APPLICABLE",
        "target_applicability_outcome": "APPLICABLE",
        "source_verdict_ref": artifact_ref(
            source_verdict_id,
            "ylx.stage-terminal-result.v1",
            source_verdict_sha,
            (
                "contracts/fixtures/governance-models/support/"
                f"{source_verdict_filename}"
            ),
        ),
        "source_evidence_refs": [source_evidence_ref],
        "source_binding_context_ref": context_ref(
            "fixture-binding-context-m4-source", m4_source_sha, "M4"
        ),
        "target_binding_context_ref": context_ref(
            "fixture-binding-context-m4-target", m4_target_sha, "M4-target"
        ),
        "source_assembly_ref": artifact_ref(
            candidate_id,
            "ylx.m4-candidate-assembly.v1",
            assembly_sha,
            "contracts/fixtures/governance-models/valid/m4-candidate-assembly.json",
        ),
        "target_assembly_ref": artifact_ref(
            candidate_id,
            "ylx.m4-candidate-assembly.v1",
            assembly_sha,
            "contracts/fixtures/governance-models/valid/m4-candidate-assembly.json",
        ),
        "component_impact_graph_ref": artifact_ref(
            "fixture-m4-impact-graph-r1",
            "ylx.m4-component-impact-graph.v1",
            graph_sha,
            "contracts/fixtures/governance-models/valid/m4-component-impact-graph.json",
        ),
        "component_diff": {
            "component_sub_bundles": component_diff,
            "shared_inputs": {
                "product-contract": equal_pair(product_contract_sha),
                "contract-release": equal_pair(contract_release_sha),
                "common-lineage": equal_pair(sha("fixture-common-lineage")),
            },
            "source_assembly_projection_sha256": assembly_sha,
            "target_assembly_projection_sha256": assembly_sha,
            "unexplained_artifact_diff_ids": [],
            "unexplained_config_diff_ids": [],
        },
        "source_qualification_input_sha256_by_id": source_qualification_inputs,
        "target_qualification_input_sha256_by_id": target_qualification_inputs,
        "qualification_input_diff": qualification_diff,
        "recomputed_affected_requirement_ids": affected,
        "target_requirement_affected": False,
        "transitive_input_equality_proof": equality_proof,
        "target_integration_smoke_sha256": assembly["integration_smoke_sha256"],
        "evaluator_sha256": sha("fixture-rebase-evaluator"),
        "target_wrapper_signature_ref": artifact_ref("fixture-rebase-wrapper-signature"),
        "rebase_outcome": "REBASED_UNAFFECTED",
        "target_effective_verdict": "PASS",
        "issued_at": STAMP,
    }
    corpus.add(
        "VALID-M4-VERDICT-REBASE-01",
        "m4-verdict-rebase.json",
        "m4-verdict-rebase-v1.schema.json",
        rebase,
    )

    dry_run = build_dry_run(
        m4_source_sha=m4_target_sha,
        m4_source=m4_target,
        graph_sha=graph_sha,
        assembly_sha=assembly_sha,
        registry_sha=registry_sha,
    )
    corpus.add(
        "VALID-M4-RELEASE-CLOSURE-DRY-RUN-01",
        "m4-release-closure-dry-run.json",
        "m4-release-closure-dry-run-v1.schema.json",
        dry_run,
    )

    release_state = {
        "contract_release_sha": contract_release_sha,
        "product_contract_sha": product_contract_sha,
        "qualification_contract_sha": qualification_contract_sha,
        "candidate_id": candidate_id,
        "m4_context_sha": m4_target_sha,
        "m4_context": m4_target,
        "m2_context_sha": m2_context_sha,
        "m2_context": m2_context,
        "m3_context_sha": m3_context_sha,
        "m3_context": m3_context,
        "m3_base_bundle_sha": m3_base_bundle_sha,
        "assembly_sha": assembly_sha,
        "assembly": assembly,
        "graph_sha": graph_sha,
        "qualification_source_sha": qualification_source_sha,
        "qualification_target_sha": qualification_target_sha,
        "qualification_target": qualification_target,
        "qualification_input_sha256_by_id": target_qualification_inputs,
        "registry_sha": registry_sha,
        "registry": registry,
        "evidence_record_sha": evidence_record_sha,
        "requirement_ids": requirement_ids,
        "closing_gate_by_id": closing_gate_by_id,
        "execution_phase_by_id": {
            row["id"]: row.get("execution_phase")
            for row in registry["requirements"]
        },
        "consumer_boundary_registry_sha": consumer_bootstrap[
            "registry_sha256"
        ],
        "consumer_deployment_set_sha": consumer_bootstrap[
            "deployment_set_sha256"
        ],
        "consumer_deployment_set_ref": copy.deepcopy(
            consumer_bootstrap["deployment_set_ref"]
        ),
        "consumer_deployment_set": copy.deepcopy(
            consumer_bootstrap["deployment_set"]
        ),
        "consumer_deployment_records": consumer_bootstrap["deployment_records"],
        "consumer_deployment_record_ref_by_boundary": consumer_bootstrap[
            "deployment_record_ref_by_boundary"
        ],
        "consumer_deployment_record_ref_by_action_and_boundary": copy.deepcopy(
            consumer_bootstrap[
                "deployment_record_ref_by_action_and_boundary"
            ]
        ),
        "execution_source_sha": execution_source_sha,
        "history": history_state,
        "foundation": foundation_state,
        "planning_v2": planning_v2_state,
        "context_v2": context_v2_state,
        "measurement_threshold": measurement_threshold_state,
        "measurement_holdout": measurement_holdout_state,
        "final_actual_variance": final_actual_variance_state,
        "stage_source_scopes": stage_source_scopes,
        "m2_bootstrap_creation_evaluation": (
            m2_bootstrap_creation_evaluation
        ),
        "m2_bootstrap_frozen_input_refs": m2_bootstrap_frozen_input_refs,
        "m2_implementation_environment_ref": (
            m2_implementation_environment_ref
        ),
        "fixture_corpus": fixture_corpus,
    }
    build_issue_and_release(corpus, release_state)


def build_consumer_bootstrap(
    corpus: Corpus, product_contract_sha: str
) -> dict[str, Any]:
    boundaries = [
        {
            "boundary_id": boundary,
            "applicability": "applicable",
            "component_artifact_owner": f"owner-{boundary}",
            "component_maintainer_subrole": f"maintainer-{boundary}",
            "legacy_input_contracts": [f"legacy-contract-{boundary}"],
            "target_input_contracts": [f"target-contract-{boundary}"],
        }
        for boundary in BOUNDARIES
    ]
    registry = {
        "schema": "ylx.consumer-boundary-registry.v1",
        "revision": 1,
        "predecessor_registry_sha256": None,
        "decision_set_id": "fixture-consumer-boundary-decision-set",
        "product_contract_sha256": product_contract_sha,
        "boundaries": boundaries,
        "scope_change": {
            "change_kind": "INITIAL",
            "reason": "Initial exact seven-boundary synthetic fixture scope.",
            "added_boundary_ids": [],
            "retired_boundary_ids": [],
            "replacement_or_retirement_evidence_refs": [],
        },
        "approvals": [
            approval("release-owner"),
            approval("contract-owner"),
            approval("consumer-owner"),
        ],
    }
    registry_sha = corpus.add(
        "VALID-CONSUMER-BOUNDARY-REGISTRY-01",
        "consumer-boundary-registry.json",
        "consumer-boundary-registry-v1.schema.json",
        registry,
    )
    deployment_actions = (
        "install-target",
        "configure-target",
        "deploy-target-disabled",
    )
    deployed_at_by_action = {
        "install-target": "2026-06-01T12:08:12Z",
        "configure-target": "2026-06-01T12:08:17Z",
        "deploy-target-disabled": "2026-06-01T12:08:30Z",
    }
    deployment_records: dict[str, Any] = {}
    deployment_record_ref_by_boundary: dict[str, dict[str, Any]] = {}
    deployment_record_ref_by_action_and_boundary: dict[
        str, dict[str, dict[str, Any]]
    ] = {action: {} for action in deployment_actions}
    for boundary in BOUNDARIES:
        deployment_record_value_by_action: dict[str, dict[str, Any]] = {}
        deployment_record_sha_by_action: dict[str, str] = {}
        for action in deployment_actions:
            action_slug = action.removesuffix("-target-disabled")
            deployment_record_value = {
                "schema": "ylx.target-disabled-deployment-record.v1",
                "record_id": f"fixture-{action}-record-{boundary}",
                "revision": 1,
                "boundary_id": boundary,
                "authorization_action": action,
                "component_artifact_identity": f"artifact-{boundary}",
                "component_artifact_sha256": sha(
                    f"fixture-component-artifact:{boundary}"
                ),
                "deployed_environment_identity": f"environment-{boundary}",
                "deployment_state": "target-disabled",
                "producer_target_write_enabled": False,
                "deployed_at": deployed_at_by_action[action],
                "notice": NOTICE,
            }
            deployment_record_filename = (
                f"deployment-{boundary}.json"
                if action == "deploy-target-disabled"
                else f"deployment-{action_slug}-{boundary}.json"
            )
            deployment_record_sha = corpus.add_support(
                deployment_record_filename,
                deployment_record_value,
                (
                    "Synthetic exact target-disabled "
                    f"{action} observation bytes for {boundary}."
                ),
            )
            deployment_record_value_by_action[action] = deployment_record_value
            deployment_record_sha_by_action[action] = deployment_record_sha
            deployment_record_ref_by_action_and_boundary[action][boundary] = (
                artifact_ref(
                    deployment_record_value["record_id"],
                    deployment_record_value["schema"],
                    deployment_record_sha,
                    (
                        "contracts/fixtures/governance-models/support/"
                        f"{deployment_record_filename}"
                    ),
                    deployment_record_value["revision"],
                )
            )
        deployment_record_ref_by_boundary[boundary] = copy.deepcopy(
            deployment_record_ref_by_action_and_boundary[
                "deploy-target-disabled"
            ][boundary]
        )
        deployment_record_sha = deployment_record_sha_by_action[
            "deploy-target-disabled"
        ]
        deployment_records[boundary] = {
            "boundary_id": boundary,
            "component_artifact_identity": f"artifact-{boundary}",
            "component_artifact_version": "1.0.0",
            "component_artifact_sha256": sha(f"fixture-component-artifact:{boundary}"),
            "deployed_environment_identity": f"environment-{boundary}",
            "deployment_ref": {
                "deployment_id": f"deployment-{boundary}",
                "component_id": boundary,
                "deployment_state": "target-disabled",
                "artifact_path": f"contracts/fixtures/governance-models/support/deployment-{boundary}.json",
                "deployment_record_sha256": deployment_record_sha,
                "rendered_config_sha256": sha(f"fixture-rendered-config:{boundary}"),
            },
            "reader_strategy": "DUAL_READER",
            "legacy_reader_artifact_sha256": sha(f"fixture-legacy-reader:{boundary}"),
            "target_reader_artifact_sha256": sha(f"fixture-target-reader:{boundary}"),
            "version_router_artifact_sha256": None,
            "producer_target_write_enabled": False,
        }
    deployment_set = {
        "schema": "ylx.consumer-deployment-set.v1",
        "deployment_set_id": "fixture-consumer-deployment-set-r1",
        "revision": 1,
        "predecessor_deployment_set_sha256": None,
        "consumer_boundary_registry_sha256": registry_sha,
        "deployment_state": "target-disabled",
        "boundary_ids": BOUNDARIES,
        "deployment_record_by_boundary": deployment_records,
        "created_at": "2026-06-01T12:09:00Z",
        "artifact_metadata": metadata(),
    }
    deployment_set_sha = corpus.add(
        "VALID-CONSUMER-DEPLOYMENT-SET-01",
        "consumer-deployment-set.json",
        "consumer-deployment-set-v1.schema.json",
        deployment_set,
    )
    return {
        "registry_sha256": registry_sha,
        "deployment_set": deployment_set,
        "deployment_set_sha256": deployment_set_sha,
        "deployment_set_ref": artifact_ref(
            deployment_set["deployment_set_id"],
            deployment_set["schema"],
            deployment_set_sha,
            "contracts/fixtures/governance-models/valid/consumer-deployment-set.json",
            deployment_set["revision"],
        ),
        "deployment_records": deployment_records,
        "deployment_record_ref_by_boundary": deployment_record_ref_by_boundary,
        "deployment_record_ref_by_action_and_boundary": (
            deployment_record_ref_by_action_and_boundary
        ),
    }


def build_preview_decision_fixtures(corpus: Corpus) -> None:
    infeasibility = {
        "schema": "ylx.preview-recording-time-infeasibility.v1",
        "artifact_version": 1,
        "decision_scope_id": "fixture-preview-decision-scope",
        "target_hardware_cell_id": "fixture-target-hardware-cell",
        "prototype_candidate_sha256": sha("fixture-preview-prototype"),
        "recording_time_config_sha256": sha("fixture-recording-time-config"),
        "workload_sha256": sha("fixture-preview-workload"),
        "evaluated_predicate_ids": [
            "capture-safety",
            "resource-capacity",
        ],
        "failed_predicate_ids": ["resource-capacity"],
        "measurement_evidence_ids": ["fixture-preview-measurement"],
        "measurement_evidence_sha256_by_id": {
            "fixture-preview-measurement": sha("fixture-preview-measurement")
        },
        "attempted_at": "2026-05-10T00:00:00Z",
        "supersedes_infeasibility_sha256": None,
        "owner_approvals": [
            approval("capture-owner"),
            approval("web-control-owner"),
            approval("hardware-owner"),
            approval("release-owner"),
        ],
        "independent_review": approval("qa-evidence-owner"),
    }
    infeasibility_sha = corpus.add(
        "VALID-PREVIEW-RECORDING-TIME-INFEASIBILITY-01",
        "preview-recording-time-infeasibility.json",
        "preview-recording-time-infeasibility-v1.schema.json",
        infeasibility,
    )
    support_mode = {
        "schema": "ylx.preview-support-mode.v1",
        "artifact_version": 1,
        "selector": "preview_support_mode",
        "value": "pre_recording_only",
        "decision_requirement_id": "M1-SUPPORT-01",
        "decision_sha256": sha("fixture-preview-support-decision"),
        "issued_at": "2026-06-01T12:01:00Z",
        "supersedes_selector_artifact_sha256": None,
        "recording_time_infeasibility_sha256": infeasibility_sha,
    }
    corpus.add(
        "VALID-PREVIEW-SUPPORT-MODE-01",
        "preview-support-mode.json",
        "preview-support-mode-v1.schema.json",
        support_mode,
    )


def build_consumer_completion(
    *,
    corpus: Corpus,
    state: dict[str, Any],
    m5_context_ref: dict[str, Any],
    signing_policy_sha: str,
    key_head_sha: str,
) -> dict[str, Any]:
    m4_ref = {
        "context_id": "fixture-binding-context-m4-target",
        "artifact_path": "valid/binding-context-m4-target.json",
        "artifact_sha256": state["m4_context_sha"],
    }
    m5_ref = {
        "context_id": m5_context_ref["artifact_id"],
        "artifact_path": "valid/binding-context-m5.json",
        "artifact_sha256": m5_context_ref["artifact_sha256"],
    }
    consumer_execution_context = {
        "schema": "ylx.execution-context.v1",
        "context_id": "fixture-consumer-execution-context",
        "created_at": STAMP,
        "run_id": "fixture-consumer-qualification-run",
        "route_id": "fixture-consumer-seven-boundary-route",
        "environment_refs": [
            {
                "environment_id": "fixture-consumer-environment",
                "environment_kind": "synthetic-lab",
                "hardware_id": "fixture-hardware",
                "camera_id": "fixture-camera",
                "media_id": "fixture-media",
                "firmware_id": "fixture-firmware",
                "os_id": "fixture-os",
                "runtime_id": "fixture-runtime",
                "artifact_path": "contracts/fixtures/governance-models/support/consumer-environment.json",
                "artifact_sha256": sha("fixture-consumer-environment"),
            }
        ],
        "support_cell_refs": [
            {
                "support_cell_id": "fixture-support-cell",
                "artifact_path": "contracts/fixtures/governance-models/support/support-cell.json",
                "artifact_sha256": sha("fixture-support-cell"),
            }
        ],
        "deployment_refs": [
            copy.deepcopy(state["consumer_deployment_records"][boundary]["deployment_ref"])
            for boundary in BOUNDARIES
        ],
        "data_partition_refs": [],
        "artifact_metadata": metadata(),
    }
    consumer_execution_context_sha = corpus.add(
        "VALID-CONSUMER-EXECUTION-CONTEXT-01",
        "execution-context-consumers.json",
        "execution-context-v1.schema.json",
        consumer_execution_context,
    )
    execution_ref = {
        "context_id": "fixture-consumer-execution-context",
        "artifact_path": "valid/execution-context-consumers.json",
        "artifact_sha256": consumer_execution_context_sha,
    }
    common_lineage = {
        "source_session_id": "fixture-source-session",
        "source_manifest_sha256": sha("fixture-source-manifest"),
        "source_artifact_set_sha256": sha("fixture-source-artifact-set"),
        "source_volume_id": "fixture-source-volume",
        "source_generation_id": "fixture-source-generation",
        "source_lineage_sha256": sha("fixture-source-lineage"),
    }
    receipts: dict[str, Any] = {}
    receipt_digests: dict[str, str] = {}
    for boundary in BOUNDARIES:
        deployment = state["consumer_deployment_records"][boundary]
        receipt = {
            "boundary_id": boundary,
            "consumer_boundary_registry_sha256": state[
                "consumer_boundary_registry_sha"
            ],
            "binding_context_ref": m4_ref,
            "execution_context_refs": [execution_ref],
            "rendered_config_sha256": deployment["deployment_ref"][
                "rendered_config_sha256"
            ],
            "component_artifact_owner": f"owner-{boundary}",
            "component_maintainer_subrole": f"maintainer-{boundary}",
            "attesting_actor_identity": {
                "actor_id": f"fixture-actor-{boundary}",
                "actor_identity_sha256": sha(f"fixture-actor-identity:{boundary}"),
                "identity_authority_ref": authority("fixture-actor-identity-authority"),
            },
            "role_assignment_artifact_sha256": sha(
                f"fixture-actor-assignment:{boundary}"
            ),
            "role_assignment_revision": 1,
            "component_artifact_sha256": deployment["component_artifact_sha256"],
            "deployed_environment_identity": deployment[
                "deployed_environment_identity"
            ],
            "deployment_record_sha256": deployment["deployment_ref"][
                "deployment_record_sha256"
            ],
            **common_lineage,
            "consumed_input_sha256": sha(f"fixture-consumed-input:{boundary}"),
            "validator_artifact_sha256": sha(f"fixture-consumer-validator:{boundary}"),
            "validator_version": "1.0.0",
            "checked_at": STAMP,
            "verdict": "PASS",
        }
        receipts[boundary] = receipt
        receipt_digests[boundary] = sha(canonical_bytes(receipt))
    attestation_set = {
        "schema": "ylx.consumer-attestation-set.v1",
        "attestation_set_id": "fixture-consumer-attestation-set",
        "consumer_boundary_registry_sha256": state[
            "consumer_boundary_registry_sha"
        ],
        "binding_context_ref": m4_ref,
        "receipt_boundary_ids": BOUNDARIES,
        "common_source_lineage": common_lineage,
        "receipts": receipts,
        "receipt_sha256_by_boundary": receipt_digests,
        "created_at": STAMP,
        "overall_verdict": "PASS",
    }
    attestation_set_sha = corpus.add(
        "VALID-CONSUMER-ATTESTATION-SET-01",
        "consumer-attestation-set.json",
        "consumer-attestation-set-v1.schema.json",
        attestation_set,
    )

    maintainer_digests: dict[str, str] = {}
    for boundary in BOUNDARIES:
        deployment = state["consumer_deployment_records"][boundary]
        maintainer_seed = hashlib.sha256(
            f"YLX SYNTHETIC TEST MAINTAINER KEY ONLY:{boundary}".encode("ascii")
        ).digest()
        public_raw = (
            Ed25519PrivateKey.from_private_bytes(maintainer_seed)
            .public_key()
            .public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        )
        person_id = f"fixture-maintainer-person-{boundary}"
        maintainer = {
            "schema": "ylx.component-maintainer-attestation.v1",
            "boundary_id": boundary,
            "component_artifact_owner": f"owner-{boundary}",
            "component_maintainer_subrole": f"maintainer-{boundary}",
            "binding_context_ref": m5_ref,
            "execution_context_refs": [execution_ref],
            "component_artifact_identity": deployment["component_artifact_identity"],
            "component_artifact_sha256": deployment["component_artifact_sha256"],
            "deployed_environment_identity": deployment[
                "deployed_environment_identity"
            ],
            "deployment_record_sha256": deployment["deployment_ref"][
                "deployment_record_sha256"
            ],
            "signer_identity": {
                "person_id": person_id,
                "natural_person_identity_sha256": sha(f"identity:{person_id}"),
                "identity_authority_ref": authority("fixture-identity-authority"),
            },
            "signing_key_fingerprint": sha(public_raw),
            "role_assignment_artifact_sha256": sha(
                f"fixture-maintainer-assignment:{boundary}"
            ),
            "role_assignment_revision": 1,
            "signing_policy_sha256": signing_policy_sha,
            "key_validity_revocation_head_sha256": key_head_sha,
            "signed_at": STAMP,
        }
        maintainer_digests[boundary] = corpus.add(
            f"VALID-COMPONENT-MAINTAINER-ATTESTATION-{boundary.upper()}-01",
            f"component-maintainer-attestation-{boundary}.json",
            "component-maintainer-attestation-v1.schema.json",
            maintainer,
        )

    component_acceptance_map: dict[str, str] = {}
    for boundary in BOUNDARIES:
        deployment = state["consumer_deployment_records"][boundary]
        acceptance = {
            "schema": "ylx.consumer-boundary-acceptance.v1",
            "boundary_id": boundary,
            "consumer_boundary_registry_sha256": state[
                "consumer_boundary_registry_sha"
            ],
            "binding_context_ref": m5_ref,
            "execution_context_refs": [execution_ref],
            "component_artifact_owner": f"owner-{boundary}",
            "component_maintainer_subrole": f"maintainer-{boundary}",
            "component_maintainer_attestation_sha256": maintainer_digests[boundary],
            "component_artifact_identity": deployment["component_artifact_identity"],
            "component_artifact_sha256": deployment["component_artifact_sha256"],
            "deployed_environment_identity": deployment[
                "deployed_environment_identity"
            ],
            "deployment_record_sha256": deployment["deployment_ref"][
                "deployment_record_sha256"
            ],
            "legacy_accept_evidence_ids": [f"legacy-evidence-{boundary}"],
            "target_accept_evidence_ids": [f"target-evidence-{boundary}"],
            "unknown_major_reject_evidence_ids": [
                f"unknown-major-reject-evidence-{boundary}"
            ],
            "requirement_verdict_refs": [
                artifact_ref(f"fixture-consumer-verdict-{boundary}")
            ],
            "owner_slot": "consumer-owner",
            "reviewer_slot": "contract-owner",
        }
        component_acceptance_map[boundary] = corpus.add(
            f"VALID-CONSUMER-BOUNDARY-ACCEPTANCE-{boundary.upper()}-01",
            f"consumer-boundary-acceptance-{boundary}.json",
            "consumer-boundary-acceptance-v1.schema.json",
            acceptance,
        )
    acceptance_set = {
        "schema": "ylx.consumer-boundary-acceptance-set.v1",
        "revision": 1,
        "consumer_boundary_registry_sha256": state[
            "consumer_boundary_registry_sha"
        ],
        "binding_context_ref": m5_ref,
        "boundary_ids": BOUNDARIES,
        "component_acceptance_record_sha256_by_boundary": component_acceptance_map,
        "created_at": STAMP,
        "owner_slot": "consumer-owner",
        "reviewer_slot": "contract-owner",
        "approvals": [approval("consumer-owner"), approval("contract-owner")],
    }
    acceptance_set_sha = corpus.add(
        "VALID-CONSUMER-BOUNDARY-ACCEPTANCE-SET-01",
        "consumer-boundary-acceptance-set.json",
        "consumer-boundary-acceptance-set-v1.schema.json",
        acceptance_set,
    )
    corpus.relationships["consumer_chain"] = {
        "boundary_ids": BOUNDARIES,
        "boundary_registry_path": "valid/consumer-boundary-registry.json",
        "deployment_set_path": "valid/consumer-deployment-set.json",
        "execution_context_path": "valid/execution-context-consumers.json",
        "m4_attestation_set_path": "valid/consumer-attestation-set.json",
        "m4_attestation_set_sha256": attestation_set_sha,
        "maintainer_attestation_paths_by_boundary": {
            boundary: f"valid/component-maintainer-attestation-{boundary}.json"
            for boundary in BOUNDARIES
        },
        "acceptance_record_paths_by_boundary": {
            boundary: f"valid/consumer-boundary-acceptance-{boundary}.json"
            for boundary in BOUNDARIES
        },
        "acceptance_set_path": "valid/consumer-boundary-acceptance-set.json",
    }
    return {
        "component_acceptance_map": component_acceptance_map,
        "acceptance_set_sha256": acceptance_set_sha,
        "maintainer_attestation_map": maintainer_digests,
        "m4_attestation_set_sha256": attestation_set_sha,
    }


def build_dry_run(
    *,
    m4_source_sha: str,
    m4_source: dict[str, Any],
    graph_sha: str,
    assembly_sha: str,
    registry_sha: str,
) -> dict[str, Any]:
    m4_context_id = m4_source["context_id"]
    m4_context_stage_path = (
        "M4-target" if m4_context_id.endswith("-target") else "M4"
    )
    m4_binding_context_ref = context_ref(
        m4_context_id, m4_source_sha, m4_context_stage_path
    )

    def case_result(case_id: str, positive: bool) -> dict[str, Any]:
        outcome = "ACCEPTED" if positive else "REJECTED"
        return {
            "case_id": case_id,
            "case_kind": "POSITIVE_FIXTURE" if positive else "NEGATIVE_MUTATION",
            "fixture_or_mutation_ref": artifact_ref(f"fixture-{case_id}"),
            "expected_outcome": outcome,
            "observed_outcome": outcome,
            "matched_expectation": True,
            "evidence_ref": artifact_ref(f"fixture-{case_id}-evidence"),
        }

    def probe(label: str) -> dict[str, Any]:
        return {
            "validator_sha256": sha(f"fixture-{label}-validator"),
            "fixture_set_sha256": sha(f"fixture-{label}-set"),
            "case_results": [
                case_result(f"{label}-positive", True),
                case_result(f"{label}-negative", False),
            ],
            "positive_fixture_count": 1,
            "negative_mutation_count": 1,
            "all_expectations_met": True,
            "outcome": "PASS",
            "diagnostics": [],
            "evidence_refs": [artifact_ref(f"fixture-{label}-probe-evidence")],
        }

    boundary_map = {boundary: sha(f"fixture-boundary:{boundary}") for boundary in BOUNDARIES}
    role_map = {role: sha(f"fixture-attestation:{role}") for role in ROLES}
    control_plane_source_sha = sha("fixture-m4-release-control-plane-source")
    release_controller_artifact_ref = artifact_ref(
        "fixture-m4-release-controller-build",
        "ylx.release-controller-build.v1",
        sha("fixture-m4-release-controller-artifact"),
    )
    resolver_artifact_ref = artifact_ref(
        "fixture-m4-customer-visible-resolver-build",
        "ylx.customer-visible-resolver-build.v1",
        sha("fixture-m4-customer-visible-resolver-artifact"),
    )
    control_plane_artifact_set_sha = sha(
        canonical_bytes(
            {
                "release-controller": release_controller_artifact_ref[
                    "artifact_sha256"
                ],
                "customer-visible-resolver": resolver_artifact_ref[
                    "artifact_sha256"
                ],
            }
        )
    )
    control_plane_build_sha = sha(
        canonical_bytes(
            {
                "source_sha256": control_plane_source_sha,
                "artifact_set_sha256": control_plane_artifact_set_sha,
            }
        )
    )
    control_plane_rendered_config_sha = sha(
        canonical_bytes(
            {
                "build_sha256": control_plane_build_sha,
                "config_profile": "M4_TARGET_DISABLED_NO_PRODUCTION",
            }
        )
    )
    control_plane_deployment_sha = sha(
        canonical_bytes(
            {
                "deployment_name": "fixture-m4-release-control-plane-dry-run",
                "environment_name": "fixture-m4-no-production-control-plane",
                "build_sha256": control_plane_build_sha,
                "rendered_config_sha256": control_plane_rendered_config_sha,
                "deployment_state": "target-disabled",
            }
        )
    )

    def control_plane_action(step: str) -> dict[str, Any]:
        return {
            "execution_authorization_evaluation_ref": artifact_ref(
                f"fixture-m4-release-control-plane-{step}-evaluation",
                "ylx.execution-authorization-evaluation.v1",
            ),
            "action_receipt_ref": artifact_ref(
                f"fixture-m4-release-control-plane-{step}-receipt",
                "ylx.release-control-plane-action-receipt.v1",
            ),
            "bound_build_sha256": control_plane_build_sha,
            "outcome": "PASS",
        }

    def control_plane_proof(kind: str) -> dict[str, Any]:
        return {
            "evidence_ref": artifact_ref(
                f"fixture-m4-release-control-plane-{kind}-proof",
                "ylx.release-control-plane-operational-proof.v1",
            ),
            "exercised_build_sha256": control_plane_build_sha,
            "outcome": "PASS",
        }

    return {
        "schema": "ylx.m4-release-closure-dry-run.v1",
        "dry_run_id": "fixture-m4-release-closure-dry-run-r1",
        "revision": 1,
        "predecessor_sha256": None,
        "created_at": STAMP,
        "artifact_path": "contracts/fixtures/governance-models/valid/m4-release-closure-dry-run.json",
        "artifact_metadata": metadata(),
        "m4_binding_context_ref": copy.deepcopy(m4_binding_context_ref),
        "m4_binding_projection": copy.deepcopy(m4_source["body"]),
        "m2_contract_package_ref": artifact_ref("fixture-m2-contract-package"),
        "control_plane_provenance": {
            "lane": "M4_RELEASE_CONTROL_PLANE",
            "product_candidate_relation": (
                "OUTSIDE_FOUR_PRODUCT_COMPONENTS_AND_CANDIDATE_IDENTITY"
            ),
            "m5_production_relation": (
                "SEPARATE_M5_PRODUCTION_QUALIFICATION_REQUIRED"
            ),
            "qualification_binding_context_ref": copy.deepcopy(
                m4_binding_context_ref
            ),
            "canonical_component": {
                "component_id": "release-publication-controller-resolver",
                "component_class": "RELEASE_CONTROL_PLANE",
                "canonical_source_ref": artifact_ref(
                    "fixture-m4-release-control-plane-source",
                    "ylx.release-control-plane-source.v1",
                    control_plane_source_sha,
                ),
                "source_sha256": control_plane_source_sha,
                "ownership_authority_ref": authority(
                    "fixture-release-control-plane-ownership"
                ),
                "product_component": False,
                "m4_candidate_identity_member": False,
            },
            "exact_build": {
                "build_id": "fixture-m4-release-control-plane-build",
                "build_provenance_ref": artifact_ref(
                    "fixture-m4-release-control-plane-build-provenance",
                    "ylx.release-control-plane-build-provenance.v1",
                    control_plane_build_sha,
                ),
                "source_sha256": control_plane_source_sha,
                "build_sha256": control_plane_build_sha,
                "release_controller_artifact_ref": (
                    release_controller_artifact_ref
                ),
                "customer_visible_resolver_artifact_ref": (
                    resolver_artifact_ref
                ),
                "artifact_set_sha256": control_plane_artifact_set_sha,
            },
            "rendered_config": {
                "rendered_config_ref": artifact_ref(
                    "fixture-m4-release-control-plane-rendered-config",
                    "ylx.release-control-plane-rendered-config.v1",
                    control_plane_rendered_config_sha,
                ),
                "rendered_config_sha256": control_plane_rendered_config_sha,
                "build_sha256": control_plane_build_sha,
                "config_profile": "M4_TARGET_DISABLED_NO_PRODUCTION",
            },
            "target_disabled_deployment": {
                "deployment_name": "fixture-m4-release-control-plane-dry-run",
                "environment_name": "fixture-m4-no-production-control-plane",
                "deployment_record_ref": artifact_ref(
                    "fixture-m4-release-control-plane-target-disabled-deployment",
                    "ylx.release-control-plane-target-disabled-deployment.v1",
                    control_plane_deployment_sha,
                ),
                "deployment_record_sha256": control_plane_deployment_sha,
                "environment_class": "NO_PRODUCTION",
                "deployment_state": "target-disabled",
                "build_sha256": control_plane_build_sha,
                "rendered_config_sha256": control_plane_rendered_config_sha,
                "production_writer_enabled": False,
                "production_visibility_enabled": False,
            },
            "boundaries": {
                "service_identity_ref": artifact_ref(
                    "fixture-m4-release-control-plane-service-identity",
                    "ylx.release-control-plane-service-identity.v1",
                ),
                "storage_cas_authority_ref": authority(
                    "fixture-m4-release-control-plane-storage-cas"
                ),
                "credential_operator_boundary_ref": authority(
                    "fixture-m4-release-control-plane-credential-operator"
                ),
                "canonical_visibility_source_ref": artifact_ref(
                    "fixture-m4-release-control-plane-visibility-source",
                    "ylx.release-control-plane-visibility-source.v1",
                ),
                "production_storage_access": False,
                "production_credential_access": False,
                "production_writer_access": False,
                "production_visibility_access": False,
            },
            "action_execution_by_step": {
                step: control_plane_action(step)
                for step in (
                    "implement",
                    "build",
                    "install",
                    "configure",
                    "deploy",
                    "smoke",
                )
            },
            "operational_proof_by_kind": {
                kind: control_plane_proof(kind)
                for kind in (
                    "health",
                    "backup",
                    "restore",
                    "upgrade",
                    "rollback",
                    "quarantine",
                    "writer_refusal",
                    "production_visibility_refusal",
                )
            },
            "dry_run_exercised_build_sha256": control_plane_build_sha,
            "same_exact_build_verified": True,
            "outcome": "PASS",
            "diagnostics": [],
        },
        "no_production_execution": {
            "execution_context_ref": artifact_ref(
                "fixture-no-production-execution",
                "ylx.execution-context.v1",
            ),
            "environment_class": "NO_PRODUCTION",
            "fixture_authority_id": "fixture-authority",
            "production_writer_access": False,
            "production_storage_access": False,
            "production_signing_key_access": False,
            "production_promotion_access": False,
            "production_side_effects_detected": False,
            "production_mutation_count": 0,
            "external_state_before_sha256": sha("fixture-external-state"),
            "external_state_after_sha256": sha("fixture-external-state"),
            "external_state_unchanged": True,
            "side_effect_audit_ref": artifact_ref("fixture-side-effect-audit"),
        },
        "governance_schema_probe": {
            "schema_inventory_ref": artifact_ref("fixture-schema-inventory"),
            "schema_set_sha256": sha("fixture-schema-set"),
            "canonical_encoding_sha256": sha("fixture-canonical-encoding"),
            "execution": probe("schema"),
        },
        "governance_validator_probe": {
            "validator_inventory_ref": artifact_ref("fixture-validator-inventory"),
            "validator_set_sha256": sha("fixture-validator-set"),
            "fixture_corpus_ref": artifact_ref("fixture-corpus"),
            "duplicate_key_parser_sha256": sha("fixture-duplicate-key-parser"),
            "execution": probe("validator"),
        },
        "issue_probe": {
            "current_issue_head": {
                "artifact_path": "contracts/fixtures/governance-models/valid/issue-register-head.json",
                "revision": 1,
                "head_artifact_sha256": sha("fixture-issue-head-bootstrap"),
                "register_sha256": sha("fixture-issue-register"),
                "selector_version": "fixture-selector-v1",
                "overview_cardinality": 1,
            },
            "issue_archive_sha256": sha("fixture-issue-archive"),
            "issue_slice_set_sha256": sha("fixture-issue-slices"),
            "issue_reconciliation_set_sha256": sha("fixture-issue-reconciliation"),
            "selector_sha256": sha("fixture-issue-selector"),
            "execution": probe("issue"),
        },
        "mapping_probe": {
            "mapping_ref": artifact_ref("fixture-system-requirement-mapping"),
            "mapping_semantic_sha256": sha("fixture-mapping-semantic"),
            "source_feature_set_sha256": sha("fixture-source-feature-set"),
            "registry_sha256": registry_sha,
            "execution": probe("mapping"),
        },
        "component_probe": {
            "candidate_assembly_ref": artifact_ref(
                m4_source["body"]["candidate_id"],
                "ylx.m4-candidate-assembly.v1",
                assembly_sha,
                "contracts/fixtures/governance-models/valid/m4-candidate-assembly.json",
            ),
            "component_impact_graph_ref": artifact_ref(
                "fixture-m4-impact-graph-r1",
                "ylx.m4-component-impact-graph.v1",
                graph_sha,
            ),
            "consumer_boundary_registry_ref": artifact_ref("fixture-consumer-boundary-registry"),
            "consumer_acceptance_set_ref": artifact_ref("fixture-consumer-acceptance-set"),
            "component_acceptance_record_sha256_by_boundary": boundary_map,
            "maintainer_attestation_sha256_by_boundary": {
                key: sha(f"fixture-maintainer:{key}") for key in BOUNDARIES
            },
            "domain_attestation_sha256_by_role": role_map,
            "subrole_accountability_sha256": sha("fixture-subrole-accountability"),
            "full_tuple_sha256": sha("fixture-full-tuple"),
            "execution": probe("component"),
        },
        "closure_probe": {
            "promotion_simulator_ref": artifact_ref("fixture-promotion-simulator"),
            "signing_fixture_authority_ref": artifact_ref("fixture-signing-authority"),
            "four_distinct_natural_persons_required": True,
            "qa_independence_required": True,
            "create_if_absent_only": True,
            "overwrite_allowed": False,
            "final_manifest_self_reference_rejected": True,
            "final_manifest_durable_readback_required": True,
            "registry_result_count": 173,
            "registry_result_map_sha256": sha("fixture-result-map"),
            "canary_selector_and_first_eligible_journal": probe("canary"),
            "writer_enable_commit": probe("writer"),
            "pre_release_closure": probe("pre-release"),
            "distinct_person_quorum_and_trust": probe("quorum"),
            "exact_rc_promotion_recovery": probe("promotion"),
            "final_manifest_and_signoff_derivation": probe("final"),
            "outcome": "PASS",
            "diagnostics": [],
        },
        "component_qualification_relation": "MAY_RUN_IN_PARALLEL",
        "required_before": "M4_AGGREGATE",
        "overall_outcome": "PASS",
        "diagnostics": [],
        "approvals": {
            "contract-owner": approval(
                "contract-owner", decision="APPROVED"
            ),
            "release-owner": approval("release-owner", decision="APPROVED"),
            "qa-evidence-owner": approval(
                "qa-evidence-owner", decision="APPROVED"
            ),
        },
        "completed_at": STAMP,
    }


def build_stage_source_scope_fixtures(
    corpus: Corpus,
    planning_state: dict[str, Any],
    history_state: dict[str, Any],
) -> dict[str, Any]:
    """Build the shared revisioned native M0/M1 source scopes."""

    values: dict[str, dict[str, Any]] = {}
    refs: dict[str, dict[str, Any]] = {}
    m0_source_ref_by_id = {
        "acceptance-registry": {
            "ref_id": "acceptance-registry",
            "source_kind": "ALREADY_HELD_LOCAL_BYTES",
            "locator": "docs/acceptance-requirements.yaml",
            "sha256": sha(
                (REPO_ROOT / "docs/acceptance-requirements.yaml").read_bytes()
            ),
            "observed_at": "2026-05-01T00:00:00Z",
            "provenance": "M0-P custody observation of already-held repository bytes.",
            "authority_effect": "NONE",
        },
        "system-feature-mapping": {
            "ref_id": "system-feature-mapping",
            "source_kind": "ALREADY_HELD_LOCAL_BYTES",
            "locator": "docs/system-requirement-mapping.yaml",
            "sha256": sha(
                (REPO_ROOT / "docs/system-requirement-mapping.yaml").read_bytes()
            ),
            "observed_at": "2026-05-01T00:00:00Z",
            "provenance": "M0-P custody observation of already-held repository bytes.",
            "authority_effect": "NONE",
        },
    }
    m0_value = {
        "schema": "ylx.stage-source-scope.v1",
        "scope_id": "fixture-m0-baseline-scope",
        "revision": 1,
        "predecessor_scope_ref": None,
        "scope_kind": "M0_BASELINE",
        "closing_gate": "M0",
        "candidate_id": None,
        "source_ref_by_id": m0_source_ref_by_id,
        "source_sha256_by_id": {
            source_id: ref["sha256"]
            for source_id, ref in m0_source_ref_by_id.items()
        },
        "creation_provenance": {
            "mode": "M0_P_CUSTODY_INDEX",
            "execution_authorization_evaluation_ref": None,
        },
        "authority_effect": "NONE",
        "created_at": "2026-05-01T00:00:01Z",
        "artifact_metadata": metadata(),
    }
    m0_filename = "release-source-scope-m0.json"
    m0_digest = corpus.add(
        "VALID-STAGE-SOURCE-SCOPE-M0-01",
        m0_filename,
        "stage-source-scope-v1.schema.json",
        m0_value,
    )
    m0_ref = artifact_ref(
        m0_value["scope_id"],
        m0_value["schema"],
        m0_digest,
        f"contracts/fixtures/governance-models/valid/{m0_filename}",
        m0_value["revision"],
    )
    values["M0"] = m0_value
    refs["M0"] = m0_ref

    m1_creation_evaluation = build_execution_authorization_evaluation(
        corpus,
        planning_state,
        task_id=planning_state["m1_scope_creation_node_id"],
        action_instance_id="fixture-action-create-m1-stage-source-scope",
        filename_slug="m1-stage-source-scope-creation-pass",
        authorization_binding_context_ref=None,
        environment_class="governance-workspace",
        phase_barrier_ids=["milestone-entry/M1"],
        actor_person_id="fixture-release-owner-person",
        additional_prerequisite_ref_by_kind={
            "stage_source_scope": copy.deepcopy(m0_ref)
        },
        evaluated_at="2026-06-01T12:04:30Z",
    )
    decision_head = history_state["decision_head"]
    acceptance_head = history_state["acceptance_head"]
    m1_source_refs = [
        artifact_ref(
            decision_head["value"]["record_id"],
            decision_head["value"]["schema"],
            decision_head["sha256"],
            (
                "contracts/fixtures/governance-models/"
                f"{decision_head['fixture_path']}"
            ),
            decision_head["value"]["history_revision"],
        ),
        artifact_ref(
            acceptance_head["value"]["record_id"],
            acceptance_head["value"]["schema"],
            acceptance_head["sha256"],
            (
                "contracts/fixtures/governance-models/"
                f"{acceptance_head['fixture_path']}"
            ),
            acceptance_head["value"]["history_revision"],
        ),
    ]
    m1_source_ref_by_id = {
        ref["artifact_id"]: ref for ref in m1_source_refs
    }
    m1_value = {
        "schema": "ylx.stage-source-scope.v1",
        "scope_id": "fixture-m1-decision-scope",
        "revision": 1,
        "predecessor_scope_ref": None,
        "scope_kind": "M1_DECISION",
        "closing_gate": "M1",
        "candidate_id": None,
        "source_ref_by_id": m1_source_ref_by_id,
        "source_sha256_by_id": {
            source_id: ref["artifact_sha256"]
            for source_id, ref in m1_source_ref_by_id.items()
        },
        "creation_provenance": {
            "mode": "EXECUTION_AUTHORIZED_GOVERNANCE_OUTPUT",
            "execution_authorization_evaluation_ref": copy.deepcopy(
                m1_creation_evaluation["ref"]
            ),
        },
        "authority_effect": "NONE",
        "created_at": "2026-06-01T12:04:40Z",
        "artifact_metadata": metadata(),
    }
    m1_filename = "release-source-scope-m1.json"
    m1_digest = corpus.add(
        "VALID-STAGE-SOURCE-SCOPE-M1-01",
        m1_filename,
        "stage-source-scope-v1.schema.json",
        m1_value,
    )
    m1_ref = artifact_ref(
        m1_value["scope_id"],
        m1_value["schema"],
        m1_digest,
        f"contracts/fixtures/governance-models/valid/{m1_filename}",
        m1_value["revision"],
    )
    values["M1"] = m1_value
    refs["M1"] = m1_ref
    return {
        "values": values,
        "refs": refs,
        "m1_creation_evaluation": m1_creation_evaluation,
    }


def build_release_result_projection(
    corpus: Corpus,
    state: dict[str, Any],
    m5_context_ref: dict[str, Any],
    current_issue_head: dict[str, Any],
    issue_reconciliation_sha: str,
) -> dict[str, Any]:
    """Materialize the nonrecursive 171-row stage-native release input."""

    def support_artifact_ref(
        artifact_id: str, schema: str, digest: str, filename: str
    ) -> dict[str, Any]:
        return artifact_ref(
            artifact_id,
            schema,
            digest,
            f"contracts/fixtures/governance-models/support/{filename}",
        )

    source_scope_ref_by_gate = {
        "M0": copy.deepcopy(state["stage_source_scopes"]["refs"]["M0"]),
        "M1": copy.deepcopy(state["stage_source_scopes"]["refs"]["M1"]),
        "M2": context_ref(
            state["m2_context"]["context_id"], state["m2_context_sha"], "M2"
        ),
        "M3": context_ref(
            state["m3_context"]["context_id"], state["m3_context_sha"], "M3"
        ),
        "M4a": context_ref(
            state["m4_context"]["context_id"], state["m4_context_sha"], "M4-target"
        ),
        "M4b": context_ref(
            state["m4_context"]["context_id"], state["m4_context_sha"], "M4-target"
        ),
        "M4c": context_ref(
            state["m4_context"]["context_id"], state["m4_context_sha"], "M4-target"
        ),
        "M4d": context_ref(
            state["m4_context"]["context_id"], state["m4_context_sha"], "M4-target"
        ),
        "M4": context_ref(
            state["m4_context"]["context_id"], state["m4_context_sha"], "M4-target"
        ),
        "M5": copy.deepcopy(m5_context_ref),
    }
    gate_order = ["M0", "M1", "M2", "M3", "M4a", "M4b", "M4c", "M4d", "M4", "M5"]
    derived_ids = {"M5-MATRIX-COMPLETE-01", "M5-SIGNOFF-01"}
    core_requirement_ids = sorted(set(state["requirement_ids"]) - derived_ids)
    assert len(core_requirement_ids) == 171
    ids_by_gate = {
        gate: [
            requirement_id
            for requirement_id in core_requirement_ids
            if state["closing_gate_by_id"][requirement_id] == gate
        ]
        for gate in gate_order
    }
    assert all(ids_by_gate.values())

    authorization_context_ref_by_gate = {
        "M0": None,
        "M1": None,
        "M2": state["context_v2"]["m2_ref"],
        "M3": state["context_v2"]["m3_ref"],
        "M4a": state["context_v2"]["m4_r2_ref"],
        "M4b": state["context_v2"]["m4_r2_ref"],
        "M4c": state["context_v2"]["m4_r2_ref"],
        "M4d": state["context_v2"]["m4_r2_ref"],
        "M4": state["context_v2"]["m4_r2_ref"],
        "M5": state["context_v2"]["m5_ref"],
    }
    decision_head_state = state["history"]["decision_head"]
    decision_head_value = decision_head_state["value"]
    contract_release_value = corpus.values["valid/contract-release.json"]
    mapping_ratification = state["foundation"]["mapping_ratification"]
    bootstrap_prerequisites_by_gate = {
        gate: {
            "stage_source_scope": copy.deepcopy(
                state["stage_source_scopes"]["refs"][gate]
            ),
            "decision_history_head": artifact_ref(
                decision_head_value["record_id"],
                decision_head_value["schema"],
                decision_head_state["sha256"],
                decision_head_state["fixture_path"],
                decision_head_value["history_revision"],
            ),
            "acceptance_registry": {
                "ref_id": "fixture-acceptance-registry",
                "authority_kind": "contract-package",
                "locator": "docs/acceptance-requirements.yaml",
                "sha256": state["registry_sha"],
            },
            "contract_release": artifact_ref(
                contract_release_value["contract_release_id"],
                contract_release_value["schema"],
                state["contract_release_sha"],
                "valid/contract-release.json",
                contract_release_value["release_version"],
            ),
            "system_feature_mapping_ratification": artifact_ref(
                mapping_ratification["ratification_id"],
                mapping_ratification["schema"],
                state["foundation"]["mapping_ratification_sha"],
                "valid/system-feature-mapping-ratification.json",
                mapping_ratification["revision"],
            ),
        }
        for gate in ("M0", "M1")
    }
    contract_release_ref = artifact_ref(
        contract_release_value["contract_release_id"],
        contract_release_value["schema"],
        state["contract_release_sha"],
        "contracts/fixtures/governance-models/valid/contract-release.json",
        contract_release_value["release_version"],
    )
    full_m2_qualification_prerequisites = {
        "m2_qualification_context": copy.deepcopy(state["context_v2"]["m2_ref"]),
        "contract_release": copy.deepcopy(contract_release_ref),
        "fixture_corpus": copy.deepcopy(state["fixture_corpus"]["ref"]),
        "consumer_deployment_set": copy.deepcopy(
            state["consumer_deployment_set_ref"]
        ),
    }
    m4_qualification_prerequisites = {
        "m3_binding_context": copy.deepcopy(state["context_v2"]["m3_ref"]),
        "m4_binding_context": copy.deepcopy(state["context_v2"]["m4_r2_ref"]),
        "assembly": artifact_ref(
            state["assembly"]["candidate_id"],
            state["assembly"]["schema"],
            state["assembly_sha"],
            "contracts/fixtures/governance-models/valid/m4-candidate-assembly.json",
            1,
        ),
        "m4_target_deployment": copy.deepcopy(
            state["context_v2"]["m4_target_deployment_ref"]
        ),
        **{
            f"deployment-receipt-{key}": copy.deepcopy(ref)
            for key, ref in state["context_v2"]["m4_deployment_receipts"].items()
        },
    }
    stage_prerequisites_by_gate = {
        "M2": full_m2_qualification_prerequisites,
        "M3": {
            "m2_qualification_context": copy.deepcopy(
                state["context_v2"]["m2_ref"]
            )
        },
        "M4a": m4_qualification_prerequisites,
        "M4b": m4_qualification_prerequisites,
        "M4c": m4_qualification_prerequisites,
        "M4d": m4_qualification_prerequisites,
        "M4": m4_qualification_prerequisites,
    }
    environment_by_gate = {
        "M0": "read-only-observation",
        "M1": "governance-workspace",
        "M2": "qualification-target",
        "M3": "qualification-target",
        "M4a": "qualification-target",
        "M4b": "qualification-target",
        "M4c": "qualification-target",
        "M4d": "qualification-target",
        "M4": "qualification-target",
        "M5": "qualification-target",
    }
    evidence_evaluation_by_batch: dict[str, dict[str, Any]] = {}
    for gate in gate_order[:-1]:
        barriers = (
            ["m4-start/qualification"]
            if gate.startswith("M4")
            else [f"milestone-entry/{gate}"]
        )
        evidence_evaluation_by_batch[gate] = build_execution_authorization_evaluation(
            corpus,
            state["planning_v2"],
            task_id=state["planning_v2"]["evidence_node_id_by_gate"][gate],
            action_instance_id=f"fixture-action-stage-evidence-{gate.lower()}",
            filename_slug=f"stage-evidence-{gate.lower()}-pass",
            authorization_binding_context_ref=authorization_context_ref_by_gate[gate],
            environment_class=environment_by_gate[gate],
            phase_barrier_ids=barriers,
            additional_prerequisite_ref_by_kind=(
                bootstrap_prerequisites_by_gate.get(
                    gate, stage_prerequisites_by_gate.get(gate)
                )
            ),
        )
    for execution_phase in M5_EXECUTION_PHASES:
        batch_key = f"M5:{execution_phase}"
        phase_slug = execution_phase.replace("_", "-")
        barriers = ["milestone-entry/M5"]
        if execution_phase != "pre_canary":
            barriers.append(f"m5-earlier-phases/{execution_phase}")
        evidence_evaluation_by_batch[batch_key] = (
            build_execution_authorization_evaluation(
                corpus,
                state["planning_v2"],
                task_id=state["planning_v2"]["evidence_node_id_by_m5_phase"][
                    execution_phase
                ],
                action_instance_id=f"fixture-action-stage-evidence-m5-{phase_slug}",
                filename_slug=f"stage-evidence-m5-{phase_slug}-pass",
                authorization_binding_context_ref=authorization_context_ref_by_gate[
                    "M5"
                ],
                environment_class="qualification-target",
                phase_barrier_ids=barriers,
            )
        )
    build_execution_authorization_evaluation(
        corpus,
        state["planning_v2"],
        task_id=state["planning_v2"]["evidence_node_id_by_gate"]["M2"],
        action_instance_id="fixture-action-stage-evidence-m2-blocked",
        filename_slug="stage-evidence-m2-fail",
        authorization_binding_context_ref=state["context_v2"]["m2_ref"],
        environment_class="qualification-target",
        phase_barrier_ids=["milestone-entry/M2"],
        result="FAIL",
    )

    replay_primary = evidence_evaluation_by_batch["M0"]["value"]
    replay_denial = copy.deepcopy(replay_primary)
    replay_denial.update(
        {
            "evaluation_id": "fixture-execution-evaluation-stage-evidence-m0-replay-fail",
            "evaluated_at": "2026-06-01T12:16:00Z",
            "result": "FAIL",
            "failure_codes": ["ACTION_INSTANCE_REPLAY"],
            "authorizes_action": None,
            "artifact_metadata": metadata(),
        }
    )
    corpus.add(
        "VALID-EXECUTION-AUTHORIZATION-EVALUATION-IMMUTABLE-REPLAY-FAIL-01",
        "execution-authorization-evaluation-stage-evidence-m0-replay-fail.json",
        "execution-authorization-evaluation-v1.schema.json",
        replay_denial,
    )

    missing_input_denial = copy.deepcopy(evidence_evaluation_by_batch["M2"]["value"])
    missing_input_denial.update(
        {
            "evaluation_id": "fixture-execution-evaluation-stage-evidence-m2-missing-inputs-fail",
            "evaluated_at": "2026-06-01T12:17:00Z",
            "action_instance_id": "fixture-action-stage-evidence-m2-missing-inputs",
            "authorization_prerequisite_ref_by_kind": {},
            "authorization_prerequisite_sha256_by_kind": {},
            "authorization_environment_class": None,
            "authorization_environment_ref": None,
            "authorization_binding_context_ref": None,
            "authorization_binding_context_sha256": None,
            "authorization_authority_ref_by_artifact_id": {},
            "authorization_authority_sha256_by_artifact_id": {},
            "actor_assignment_ref": None,
            "actor_person_id": None,
            "authorization_stop_rules": [],
            "typed_predecessor_state_by_task_id": {},
            "phase_barrier_state_by_id": {},
            "validator_artifact_ref": None,
            "checker_assignment_ref": None,
            "result": "FAIL",
            "failure_codes": [
                "PREREQUISITE_MISSING",
                "ENVIRONMENT_MISMATCH",
                "BINDING_CONTEXT_MISMATCH",
                "AUTHORITY_MISMATCH",
                "ACTOR_ASSIGNMENT_MISMATCH",
                "PHASE_BARRIER_UNSATISFIED",
                "STOP_RULE_EVIDENCE_MISMATCH",
                "VALIDATOR_MISMATCH",
                "CHECKER_ASSIGNMENT_MISMATCH",
            ],
            "authorizes_action": None,
            "artifact_metadata": metadata(),
        }
    )
    missing_input_denial["planned_action_input_sha256"] = sha(
        canonical_bytes(
            {
                field: missing_input_denial[field]
                for field in execution_authorization_projection_fields()
            }
        )
    )
    corpus.add(
        "VALID-EXECUTION-AUTHORIZATION-EVALUATION-HONEST-MISSING-INPUTS-FAIL-01",
        "execution-authorization-evaluation-stage-evidence-m2-missing-inputs-fail.json",
        "execution-authorization-evaluation-v1.schema.json",
        missing_input_denial,
    )
    state["stage_evaluation_by_batch"] = evidence_evaluation_by_batch

    row_projection_by_requirement_id: dict[str, Any] = {}
    evidence_record_sha256_by_id: dict[str, str] = {}
    selected_gate_root_ref_by_closing_gate: dict[str, Any] = {}
    for gate in gate_order:
        gate_slug = gate.lower()
        gate_requirement_ids = ids_by_gate[gate]
        source_scope_ref = source_scope_ref_by_gate[gate]
        gate_result_sha256_by_requirement_id: dict[str, str] = {}
        gate_batches = (
            [
                (
                    phase,
                    [
                        requirement_id
                        for requirement_id in gate_requirement_ids
                        if state["execution_phase_by_id"][requirement_id] == phase
                    ],
                )
                for phase in M5_EXECUTION_PHASES
            ]
            if gate == "M5"
            else [(None, gate_requirement_ids)]
        )
        for execution_phase, batch_requirement_ids in gate_batches:
            if not batch_requirement_ids:
                continue
            batch_slug = (
                f"{gate_slug}-{execution_phase.replace('_', '-')}"
                if execution_phase is not None
                else gate_slug
            )
            evidence_id = f"fixture-stage-evidence-{batch_slug}"
            evidence_filename = f"stage-evidence-record-{batch_slug}.json"
            evidence_sha = corpus.add_support(
                evidence_filename,
                {
                    "schema": "ylx.stage-evidence-record.v1",
                    "evidence_id": evidence_id,
                    "closing_gate": gate,
                    "source_scope_ref": source_scope_ref,
                    "requirement_ids": batch_requirement_ids,
                    "evidence_outcome": "SUPPORTS_TERMINAL_RESULT",
                    "created_at": "2026-06-01T12:20:00Z",
                    "artifact_metadata": metadata(),
                },
                f"Exact stage-native evidence record selected for {batch_slug} rows.",
            )
            evidence_record_sha256_by_id[evidence_id] = evidence_sha
            evidence_binding_id = f"fixture-stage-evidence-binding-{batch_slug}"
            evidence_binding_filename = f"stage-evidence-binding-{batch_slug}.json"
            evidence_binding_sha = corpus.add_support(
                evidence_binding_filename,
                {
                    "schema": "ylx.evidence-binding.v1",
                    "binding_id": evidence_binding_id,
                    "created_at": "2026-06-01T12:21:00Z",
                    "binding_context_ref": {
                        "context_id": source_scope_ref["artifact_id"],
                        "artifact_path": source_scope_ref["artifact_path"],
                        "artifact_sha256": source_scope_ref["artifact_sha256"],
                    },
                    "execution_context_refs": [
                        {
                            "context_id": "fixture-execution-context-source",
                            "artifact_path": (
                                "contracts/fixtures/governance-models/valid/"
                                "execution-context.json"
                            ),
                            "artifact_sha256": state["execution_source_sha"],
                        }
                    ],
                    "required_execution_context_ids": [
                        "fixture-execution-context-source"
                    ],
                    "evidence_records": [
                        {
                            "evidence_id": evidence_id,
                            "evidence_record_kind": "run-evidence",
                            "artifact_path": (
                                "contracts/fixtures/governance-models/support/"
                                f"{evidence_filename}"
                            ),
                            "artifact_sha256": evidence_sha,
                            "execution_context_ids": [
                                "fixture-execution-context-source"
                            ],
                            "actor_deployment_record_sha256": None,
                        }
                    ],
                    "reverse_coverage": [
                        {
                            "requirement_id": requirement_id,
                            "execution_context_id": "fixture-execution-context-source",
                            "evidence_ids": [evidence_id],
                        }
                        for requirement_id in batch_requirement_ids
                    ],
                    "artifact_metadata": metadata(),
                },
                f"Exact stage-native evidence binding selected for {batch_slug} rows.",
            )
            evidence_binding_ref = support_artifact_ref(
                evidence_binding_id,
                "ylx.evidence-binding.v1",
                evidence_binding_sha,
                evidence_binding_filename,
            )
            for requirement_id in batch_requirement_ids:
                effective_result = (
                    "N/A" if requirement_id == "M4-ISSUES-01" else "PASS"
                )
                approved_na_record_ref: dict[str, Any] | None = None
                if effective_result == "N/A":
                    approval_id = f"fixture-approved-na-{requirement_id.lower()}"
                    approval_filename = f"approved-na-{requirement_id.lower()}.json"
                    approval_sha = corpus.add_support(
                        approval_filename,
                        {
                            "schema": "ylx.approved-na-record.v1",
                            "approval_id": approval_id,
                            "requirement_id": requirement_id,
                            "registry_sha256": state["registry_sha"],
                            "native_source_scope_ref": source_scope_ref,
                            "m5_binding_context_ref": m5_context_ref,
                            "evidence_binding_refs": [evidence_binding_ref],
                            "evidence_ids": [evidence_id],
                            "reason": (
                                "The synthetic M4 issue row is explicitly not applicable "
                                "for these frozen native and M5 target inputs."
                            ),
                            "approvals": [
                                approval("release-owner"),
                                approval("qa-evidence-owner"),
                            ],
                            "created_at": "2026-06-01T12:25:00Z",
                            "artifact_metadata": metadata(),
                        },
                        "Exact target-input-bound approval for the synthetic N/A row.",
                    )
                    approved_na_record_ref = support_artifact_ref(
                        approval_id,
                        "ylx.approved-na-record.v1",
                        approval_sha,
                        approval_filename,
                    )
                result_id = f"fixture-terminal-result-{requirement_id.lower()}"
                result_filename = f"stage-terminal-result-{requirement_id.lower()}.json"
                result_sha = corpus.add_support(
                    result_filename,
                    {
                        "schema": "ylx.stage-terminal-result.v1",
                        "result_id": result_id,
                        "revision": 1,
                        "predecessor_result_sha256": None,
                        "requirement_id": requirement_id,
                        "closing_gate": gate,
                        "effective_result": effective_result,
                        "source_scope_ref": source_scope_ref,
                        "evidence_binding_refs": [evidence_binding_ref],
                        "evidence_ids": [evidence_id],
                        "approved_na_record_ref": approved_na_record_ref,
                        "terminal_state": "CURRENT_EFFECTIVE_TERMINAL",
                        "created_at": "2026-06-01T12:30:00Z",
                        "artifact_metadata": metadata(),
                    },
                    f"Exact stage-native terminal result for {requirement_id}.",
                )
                result_ref = support_artifact_ref(
                    result_id,
                    "ylx.stage-terminal-result.v1",
                    result_sha,
                    result_filename,
                )
                gate_result_sha256_by_requirement_id[requirement_id] = result_sha
                row_projection_by_requirement_id[requirement_id] = {
                    "closing_gate": gate,
                    "effective_result": effective_result,
                    "effective_result_ref": result_ref,
                    "source_scope_ref": source_scope_ref,
                    "evidence_binding_refs": [evidence_binding_ref],
                    "evidence_ids": [evidence_id],
                    "approved_na_record_ref": approved_na_record_ref,
                }
        gate_root_id = f"fixture-selected-gate-root-{gate_slug}"
        gate_root_filename = f"selected-gate-root-{gate_slug}.json"
        gate_root_sha = corpus.add_support(
            gate_root_filename,
            {
                "schema": "ylx.stage-gate-result-root.v1",
                "root_id": gate_root_id,
                "revision": 1,
                "predecessor_root_sha256": None,
                "closing_gate": gate,
                "source_scope_ref": source_scope_ref,
                "selected_result_sha256_by_requirement_id": (
                    gate_result_sha256_by_requirement_id
                ),
                "selection_algorithm": (
                    "stage-native-current-effective-terminal-selection-rfc8785"
                ),
                "created_at": "2026-06-01T12:40:00Z",
                "artifact_metadata": metadata(),
            },
            f"Unique current selected terminal-result root for closing gate {gate}.",
        )
        selected_gate_root_ref_by_closing_gate[gate] = support_artifact_ref(
            gate_root_id,
            "ylx.stage-gate-result-root.v1",
            gate_root_sha,
            gate_root_filename,
        )

    projection = {
        "schema": "ylx.release-result-projection.v1",
        "projection_id": "fixture-release-result-projection-001",
        "revision": 1,
        "predecessor_projection_sha256": None,
        "m5_binding_context_ref": m5_context_ref,
        "effective_m4_binding_context_ref": context_ref(
            state["m4_context"]["context_id"], state["m4_context_sha"], "M4-target"
        ),
        "registry_sha256": state["registry_sha"],
        "registry_id_set_sha256": sha(
            "".join(
                f"{requirement_id}\n"
                for requirement_id in sorted(set(state["requirement_ids"]))
            )
        ),
        "core_requirement_id_set_sha256": sha(
            "".join(f"{requirement_id}\n" for requirement_id in core_requirement_ids)
        ),
        "core_requirement_cardinality": 171,
        "acceptance_sha256": sha((REPO_ROOT / "docs" / "ACCEPTANCE.md").read_bytes()),
        "system_requirement_mapping_artifact_sha256": sha(
            (REPO_ROOT / "docs" / "system-requirement-mapping.yaml").read_bytes()
        ),
        "system_requirement_mapping_semantic_sha256": sha(
            canonical_bytes(
                yaml.safe_load(
                    (REPO_ROOT / "docs" / "system-requirement-mapping.yaml").read_text()
                )
            )
        ),
        "selected_gate_root_ref_by_closing_gate": (
            selected_gate_root_ref_by_closing_gate
        ),
        "row_projection_by_requirement_id": row_projection_by_requirement_id,
        "evidence_record_sha256_by_id": evidence_record_sha256_by_id,
        "issue_head": current_issue_head,
        "issue_reconciliation_set_sha256": issue_reconciliation_sha,
        "projection_algorithm": (
            "stage-native-current-effective-terminal-selection-rfc8785"
        ),
        "projection_version": "1.0.0",
        "projector_person_id": "fixture-result-map-producer-person",
        "created_at": "2026-06-01T09:00:00Z",
        "artifact_metadata": metadata(),
    }
    projection_sha = corpus.add(
        "VALID-RELEASE-RESULT-PROJECTION-01",
        "release-result-projection.json",
        "release-result-projection-v1.schema.json",
        projection,
    )
    projection_ref = artifact_ref(
        projection["projection_id"],
        projection["schema"],
        projection_sha,
        "contracts/fixtures/governance-models/valid/release-result-projection.json",
    )
    projection_path = "valid/release-result-projection.json"
    projection_locator = {
        "schema": "ylx.content-addressed-locator-readback.v1",
        "locator_id": "fixture-release-result-projection-locator",
        "artifact_schema": projection["schema"],
        "artifact_id": projection["projection_id"],
        "artifact_sha256": projection_sha,
        "canonical_path": (
            "release-result-projection/"
            f"{projection_sha}--release-result-projection.json"
        ),
        "attempt_terminal_slot": None,
        "terminal_slot_record": None,
        "terminal_slot_create_if_absent": None,
        "terminal_slot_recorded_at": None,
        "terminal_slot_readback_record": None,
        "terminal_slot_readback_at": None,
        "terminal_slot_readback_result": None,
        "freshness_validation": None,
        "exact_byte_length": corpus.byte_lengths[projection_path],
        "canonical_encoding": "RFC8785-JSON-UTF8",
        "create_if_absent": True,
        "existing_identical_is_idempotent": True,
        "different_digest_is_equivocation": True,
        "durability": {
            "temporary_exact_bytes_fsynced": True,
            "parent_fsynced_before_create": True,
            "atomic_unique_create": True,
            "parent_fsynced_after_create": True,
        },
        "published_at": "2026-06-01T09:01:00Z",
        "readback_sha256": projection_sha,
        "readback_byte_length": corpus.byte_lengths[projection_path],
        "readback_at": "2026-06-01T09:02:00Z",
        "readback_result": "EXACT_PATH_DIGEST_AND_BYTES_MATCH",
    }
    projection_locator_sha = corpus.add(
        "VALID-CONTENT-ADDRESSED-RELEASE-RESULT-PROJECTION-READBACK-01",
        "content-addressed-locator-readback-release-result-projection.json",
        "content-addressed-locator-readback-v1.schema.json",
        projection_locator,
    )
    return {
        "projection": projection,
        "projection_sha256": projection_sha,
        "projection_ref": projection_ref,
        "projection_locator_sha256": projection_locator_sha,
        "core_result_map": {
            requirement_id: row["effective_result"]
            for requirement_id, row in row_projection_by_requirement_id.items()
        },
    }


def build_context_lineage_and_projection_v2(
    corpus: Corpus,
    state: dict[str, Any],
    current_issue_head: dict[str, Any],
    issue_reconciliation_sha: str,
    *,
    context_only: bool = False,
) -> dict[str, Any]:
    """Build the revisioned context lineage and its typed 171-row projection."""

    def valid_ref(
        artifact_id: str,
        schema: str,
        digest: str,
        filename: str,
        revision: int = 1,
    ) -> dict[str, Any]:
        return artifact_ref(
            artifact_id,
            schema,
            digest,
            f"contracts/fixtures/governance-models/valid/{filename}",
            revision,
        )

    def add_or_reuse(
        case_id: str,
        filename: str,
        schema_file: str,
        value: dict[str, Any],
    ) -> str:
        rel = f"valid/{filename}"
        if rel in corpus.values:
            if corpus.values[rel] != value:
                raise AssertionError(f"fixture reconstruction drift for {rel}")
            return corpus.digests[rel]
        return corpus.add(case_id, filename, schema_file, value)

    def add_context(
        case_id: str,
        filename: str,
        context_id: str,
        stage: str,
        body: dict[str, Any],
        owner_role: str,
        reviewer_role: str,
        *,
        revision: int = 1,
        predecessor_ref: dict[str, Any] | None = None,
        created_at: str = STAMP,
    ) -> tuple[dict[str, Any], str, dict[str, Any]]:
        value = {
            "schema": "ylx.binding-context.v2",
            "context_id": context_id,
            "revision": revision,
            "predecessor_context_ref": predecessor_ref,
            "stage": stage,
            "created_at": created_at,
            "owner_role": owner_role,
            "reviewer_role": reviewer_role,
            "lineage": lineage(),
            "body": body,
            "artifact_metadata": metadata(),
        }
        digest = add_or_reuse(
            case_id,
            filename,
            "binding-context-v2.schema.json",
            value,
        )
        return (
            value,
            digest,
            valid_ref(context_id, value["schema"], digest, filename, revision),
        )

    contract_release_value = corpus.values["valid/contract-release.json"]
    contract_release_ref = valid_ref(
        contract_release_value["contract_release_id"],
        contract_release_value["schema"],
        state["contract_release_sha"],
        "contract-release.json",
        contract_release_value["release_version"],
    )
    m2_scope = {
        "scope_kind": "M2_CONTRACT_PACKAGE_AND_DUAL_READ_CONSUMERS",
        "contract_package_included": True,
        "consumer_boundary_ids": copy.deepcopy(BOUNDARIES),
        "release_control_plane_included": False,
    }
    m2_bootstrap_body = {
        "context_kind": "M2_IMPLEMENTATION_BOOTSTRAP",
        "creation_evaluation_ref": copy.deepcopy(
            state["m2_bootstrap_creation_evaluation"]["ref"]
        ),
        "m1_stage_source_scope_ref": copy.deepcopy(
            state["stage_source_scopes"]["refs"]["M1"]
        ),
        "scope": copy.deepcopy(m2_scope),
        "frozen_input_ref_by_id": copy.deepcopy(
            state["m2_bootstrap_frozen_input_refs"]
        ),
        "implementation_environment_ref": copy.deepcopy(
            state["m2_implementation_environment_ref"]
        ),
        "deployment_state": "target-disabled",
    }
    (
        m2_bootstrap_context,
        m2_bootstrap_sha,
        m2_bootstrap_ref,
    ) = add_context(
        "VALID-BINDING-CONTEXT-V2-M2-BOOTSTRAP-01",
        "binding-context-v2-m2-bootstrap.json",
        "fixture-binding-context-v2-m2-bootstrap",
        "M2",
        m2_bootstrap_body,
        "contract-owner",
        "release-owner",
        created_at="2026-06-01T12:06:00Z",
    )
    m2_bootstrap_prerequisites = {
        "stage_source_scope": copy.deepcopy(
            state["stage_source_scopes"]["refs"]["M1"]
        ),
        **copy.deepcopy(state["m2_bootstrap_frozen_input_refs"]),
        "m2_implementation_environment": copy.deepcopy(
            state["m2_implementation_environment_ref"]
        ),
    }
    implementation_profiles = {
        "implement-contract": {
            "environment_class": "isolated-development",
            "evaluated_at": "2026-06-01T12:07:00Z",
            "started_at": "2026-06-01T12:07:01Z",
            "completed_at": "2026-06-01T12:07:46Z",
            "readback_at": "2026-06-01T12:07:47Z",
            "operation_result": "IMPLEMENTED_CONTRACT_TARGET_DISABLED",
        },
        "implement-product": {
            "environment_class": "isolated-development",
            "evaluated_at": "2026-06-01T12:07:48Z",
            "started_at": "2026-06-01T12:07:49Z",
            "completed_at": "2026-06-01T12:07:53Z",
            "readback_at": "2026-06-01T12:07:54Z",
            "operation_result": (
                "IMPLEMENTED_DUAL_READ_CONSUMERS_TARGET_DISABLED"
            ),
            "output_slug": "consumer-implementation",
        },
        "build-target-disabled": {
            "environment_class": "isolated-build",
            "evaluated_at": "2026-06-01T12:07:55Z",
            "started_at": "2026-06-01T12:07:56Z",
            "completed_at": "2026-06-01T12:08:00Z",
            "readback_at": "2026-06-01T12:08:01Z",
            "operation_result": "BUILT_DUAL_READ_CONSUMERS_TARGET_DISABLED",
            "output_slug": "consumer-target-disabled-build",
        },
        "run-integration-smoke": {
            "environment_class": "isolated-integration",
            "evaluated_at": "2026-06-01T12:08:02Z",
            "started_at": "2026-06-01T12:08:03Z",
            "completed_at": "2026-06-01T12:08:07Z",
            "readback_at": "2026-06-01T12:08:08Z",
            "operation_result": (
                "DUAL_READ_CONSUMER_SMOKE_PASSED_TARGET_DISABLED"
            ),
            "output_slug": "consumer-integration-smoke-report",
        },
    }
    implementation_evaluation_ref_by_action: dict[str, dict[str, Any]] = {}
    implementation_evaluation_by_action: dict[str, dict[str, Any]] = {}
    implementation_action_receipt_by_action: dict[str, dict[str, Any]] = {}
    implementation_action_receipt_ref_by_action: dict[str, dict[str, Any]] = {}
    predecessor_implementation_receipt_ref: dict[str, Any] | None = None
    for action, profile in implementation_profiles.items():
        evaluation_prerequisites = copy.deepcopy(m2_bootstrap_prerequisites)
        if predecessor_implementation_receipt_ref is not None:
            evaluation_prerequisites[
                "predecessor_implementation_action_receipt"
            ] = copy.deepcopy(predecessor_implementation_receipt_ref)
        evaluation = (
            build_execution_authorization_evaluation(
                corpus,
                state["planning_v2"],
                task_id=state["planning_v2"]["m2_bootstrap_node_id_by_action"][
                    action
                ],
                action_instance_id=f"fixture-action-m2-{action}",
                filename_slug=f"m2-{action}-pass",
                authorization_binding_context_ref=m2_bootstrap_ref,
                environment_class=profile["environment_class"],
                phase_barrier_ids=["milestone-entry/M2"],
                actor_person_id="fixture-contract-owner-person",
                additional_prerequisite_ref_by_kind=evaluation_prerequisites,
                evaluated_at=profile["evaluated_at"],
            )
            if context_only
            else state["context_v2"][
                "m2_implementation_evaluation_by_action"
            ][action]
        )
        implementation_evaluation_by_action[action] = evaluation
        implementation_evaluation_ref_by_action[action] = copy.deepcopy(
            evaluation["ref"]
        )
        if action == "implement-contract":
            output_ref_by_id = {
                "contract-release": copy.deepcopy(contract_release_ref),
                "governance-fixture-corpus": copy.deepcopy(
                    state["fixture_corpus"]["ref"]
                ),
            }
        else:
            output_ref_by_id = {}
            for boundary_id in BOUNDARIES:
                output_slug = profile["output_slug"]
                output_filename = f"m2-{output_slug}-{boundary_id}.json"
                output_value = {
                    "schema": "ylx.m2-dual-read-consumer-action-output.v1",
                    "output_id": f"fixture-m2-{output_slug}-{boundary_id}",
                    "revision": 1,
                    "bootstrap_context_ref": copy.deepcopy(m2_bootstrap_ref),
                    "contract_package_ref": copy.deepcopy(contract_release_ref),
                    "scope_kind": "M2_DUAL_READ_CONSUMER",
                    "boundary_id": boundary_id,
                    "authorization_action": action,
                    "deployment_state": "target-disabled",
                    "product_contract_sha256": state["product_contract_sha"],
                    "release_control_plane_included": False,
                    "created_at": profile["completed_at"],
                    "notice": NOTICE,
                }
                output_sha = corpus.add_support(
                    output_filename,
                    output_value,
                    (
                        "Synthetic exact M2 consumer-only "
                        f"{action} output bytes for {boundary_id}."
                    ),
                )
                output_ref_by_id[boundary_id] = artifact_ref(
                    output_value["output_id"],
                    output_value["schema"],
                    output_sha,
                    (
                        "contracts/fixtures/governance-models/support/"
                        f"{output_filename}"
                    ),
                    output_value["revision"],
                )
        output_sha256_by_id = {
            output_id: ref["artifact_sha256"]
            for output_id, ref in output_ref_by_id.items()
        }
        receipt_filename = f"m2-implementation-action-receipt-{action}.json"
        receipt = {
            "schema": "ylx.implementation-action-receipt.v1",
            "receipt_id": f"fixture-m2-implementation-action-receipt-{action}",
            "bootstrap_context_ref": copy.deepcopy(m2_bootstrap_ref),
            "action_scope": copy.deepcopy(m2_scope),
            "execution_authorization_evaluation_ref": copy.deepcopy(
                evaluation["ref"]
            ),
            "action_instance_id": evaluation["value"]["action_instance_id"],
            "planned_action_input_sha256": evaluation["value"][
                "planned_action_input_sha256"
            ],
            "authorization_action": action,
            "actor_person_id": evaluation["value"]["actor_person_id"],
            "execution_environment_ref": copy.deepcopy(
                evaluation["value"]["authorization_environment_ref"]
            ),
            "started_at": profile["started_at"],
            "output_ref_by_id": output_ref_by_id,
            "output_sha256_by_id": output_sha256_by_id,
            "operation_result": profile["operation_result"],
            "completed_at": profile["completed_at"],
            "readback_ref_by_id": copy.deepcopy(output_ref_by_id),
            "readback_sha256_by_id": copy.deepcopy(output_sha256_by_id),
            "readback_at": profile["readback_at"],
            "readback_matches_expected": True,
            "artifact_metadata": metadata(),
        }
        receipt_sha = add_or_reuse(
            f"VALID-IMPLEMENTATION-ACTION-RECEIPT-{action.upper().replace('-', '_')}-01",
            receipt_filename,
            "implementation-action-receipt-v1.schema.json",
            receipt,
        )
        receipt_ref = artifact_ref(
            receipt["receipt_id"],
            receipt["schema"],
            receipt_sha,
            f"contracts/fixtures/governance-models/valid/{receipt_filename}",
            None,
        )
        implementation_action_receipt_by_action[action] = receipt
        implementation_action_receipt_ref_by_action[action] = receipt_ref
        predecessor_implementation_receipt_ref = receipt_ref

    deployment_profiles = {
        "install-target": {
            "evaluated_at": "2026-06-01T12:08:09Z",
            "started_at": "2026-06-01T12:08:10Z",
            "completed_at": "2026-06-01T12:08:12Z",
            "readback_at": "2026-06-01T12:08:13Z",
            "operation_result": "INSTALLED_TARGET_DISABLED",
        },
        "configure-target": {
            "evaluated_at": "2026-06-01T12:08:14Z",
            "started_at": "2026-06-01T12:08:15Z",
            "completed_at": "2026-06-01T12:08:17Z",
            "readback_at": "2026-06-01T12:08:18Z",
            "operation_result": "CONFIGURED_TARGET_DISABLED",
        },
        "deploy-target-disabled": {
            "evaluated_at": "2026-06-01T12:08:20Z",
            "started_at": "2026-06-01T12:08:21Z",
            "completed_at": "2026-06-01T12:08:30Z",
            "readback_at": "2026-06-01T12:08:40Z",
            "operation_result": "DEPLOYED_TARGET_DISABLED",
        },
    }
    deployment_evaluation_ref_by_action_and_boundary_id: dict[
        str, dict[str, dict[str, Any]]
    ] = {action: {} for action in deployment_profiles}
    deployment_evaluation_by_action_and_boundary_id: dict[
        str, dict[str, dict[str, Any]]
    ] = {action: {} for action in deployment_profiles}
    deployment_receipt_ref_by_action_and_boundary_id: dict[
        str, dict[str, dict[str, Any]]
    ] = {action: {} for action in deployment_profiles}
    deployment_receipt_by_action_and_boundary_id: dict[
        str, dict[str, dict[str, Any]]
    ] = {action: {} for action in deployment_profiles}
    predecessor_action: str | None = None
    for action, profile in deployment_profiles.items():
        for boundary_id in sorted(state["consumer_deployment_records"]):
            deployment_record = state["consumer_deployment_records"][boundary_id]
            exact_deployment_record_ref = state[
                "consumer_deployment_record_ref_by_action_and_boundary"
            ][action][boundary_id]
            predecessor_receipt_ref = (
                None
                if predecessor_action is None
                else deployment_receipt_ref_by_action_and_boundary_id[
                    predecessor_action
                ][boundary_id]
            )
            evaluation_prerequisites = {
                **{
                    f"implementation-receipt-{implementation_action}": (
                        copy.deepcopy(ref)
                    )
                    for implementation_action, ref in (
                        implementation_action_receipt_ref_by_action.items()
                    )
                },
                "deployment_record": copy.deepcopy(exact_deployment_record_ref),
            }
            if predecessor_receipt_ref is not None:
                evaluation_prerequisites[
                    "predecessor_deployment_receipt"
                ] = copy.deepcopy(predecessor_receipt_ref)
            evaluation = (
                build_execution_authorization_evaluation(
                    corpus,
                    state["planning_v2"],
                    task_id=state["planning_v2"][
                        "m2_bootstrap_node_id_by_action"
                    ][action],
                    action_instance_id=(
                        f"fixture-action-m2-{action}-{boundary_id}"
                    ),
                    filename_slug=f"m2-{action}-{boundary_id}-pass",
                    authorization_binding_context_ref=m2_bootstrap_ref,
                    environment_class="target-disabled-non-production",
                    phase_barrier_ids=["milestone-entry/M2"],
                    actor_person_id="fixture-contract-owner-person",
                    additional_prerequisite_ref_by_kind=evaluation_prerequisites,
                    evaluated_at=profile["evaluated_at"],
                )
                if context_only
                else state["context_v2"][
                    "m2_deployment_evaluation_by_action_and_boundary_id"
                ][action][boundary_id]
            )
            deployment_evaluation_by_action_and_boundary_id[action][
                boundary_id
            ] = evaluation
            deployment_evaluation_ref_by_action_and_boundary_id[action][
                boundary_id
            ] = copy.deepcopy(evaluation["ref"])
            receipt_filename = (
                f"m2-target-disabled-{action}-{boundary_id}.json"
            )
            receipt = {
                "schema": "ylx.target-disabled-deployment-receipt.v1",
                "receipt_id": f"fixture-m2-target-disabled-{action}-{boundary_id}",
                "boundary_id": boundary_id,
                "bootstrap_context_ref": copy.deepcopy(m2_bootstrap_ref),
                "deployment_scope": copy.deepcopy(m2_scope),
                "execution_authorization_evaluation_ref": copy.deepcopy(
                    evaluation["ref"]
                ),
                "implementation_action_receipt_ref_by_action": copy.deepcopy(
                    implementation_action_receipt_ref_by_action
                ),
                "predecessor_deployment_receipt_ref": copy.deepcopy(
                    predecessor_receipt_ref
                ),
                "action_instance_id": evaluation["value"]["action_instance_id"],
                "planned_action_input_sha256": evaluation["value"][
                    "planned_action_input_sha256"
                ],
                "authorization_action": action,
                "actor_person_id": evaluation["value"]["actor_person_id"],
                "execution_environment_ref": copy.deepcopy(
                    evaluation["value"]["authorization_environment_ref"]
                ),
                "deployment_record_ref": copy.deepcopy(
                    exact_deployment_record_ref
                ),
                "deployment_record_sha256": exact_deployment_record_ref[
                    "artifact_sha256"
                ],
                "deployed_environment_identity": deployment_record[
                    "deployed_environment_identity"
                ],
                "deployment_state": "target-disabled",
                "producer_target_write_enabled": False,
                "deployment_started_at": profile["started_at"],
                "operation_result": profile["operation_result"],
                "completed_at": profile["completed_at"],
                "readback_deployment_record_ref": copy.deepcopy(
                    exact_deployment_record_ref
                ),
                "readback_deployment_record_sha256": exact_deployment_record_ref[
                    "artifact_sha256"
                ],
                "readback_at": profile["readback_at"],
                "readback_matches_expected": True,
                "artifact_metadata": metadata(),
            }
            receipt_sha = add_or_reuse(
                (
                    "VALID-TARGET-DISABLED-DEPLOYMENT-RECEIPT-"
                    f"{action.upper().replace('-', '_')}-"
                    f"{boundary_id.upper().replace('-', '_')}-01"
                ),
                receipt_filename,
                "target-disabled-deployment-receipt-v1.schema.json",
                receipt,
            )
            receipt_ref = artifact_ref(
                receipt["receipt_id"],
                receipt["schema"],
                receipt_sha,
                f"contracts/fixtures/governance-models/valid/{receipt_filename}",
                None,
            )
            deployment_receipt_by_action_and_boundary_id[action][
                boundary_id
            ] = receipt
            deployment_receipt_ref_by_action_and_boundary_id[action][
                boundary_id
            ] = receipt_ref
        predecessor_action = action

    qualification_creation_prerequisites = {
        "bootstrap_context": copy.deepcopy(m2_bootstrap_ref),
        "bootstrap_creation_evaluation": copy.deepcopy(
            state["m2_bootstrap_creation_evaluation"]["ref"]
        ),
        "consumer_deployment_set": copy.deepcopy(
            state["consumer_deployment_set_ref"]
        ),
        "contract_release": copy.deepcopy(contract_release_ref),
        "fixture_corpus": copy.deepcopy(state["fixture_corpus"]["ref"]),
        "policy_authority": copy.deepcopy(
            state["m2_bootstrap_frozen_input_refs"]["policy_authority"]
        ),
        **{
            f"implementation-evaluation-{action}": copy.deepcopy(ref)
            for action, ref in implementation_evaluation_ref_by_action.items()
        },
        **{
            f"implementation-receipt-{action}": copy.deepcopy(ref)
            for action, ref in (
                implementation_action_receipt_ref_by_action.items()
            )
        },
        **{
            f"deployment-evaluation-{action}-{boundary_id}": copy.deepcopy(ref)
            for action, refs_by_boundary in (
                deployment_evaluation_ref_by_action_and_boundary_id.items()
            )
            for boundary_id, ref in refs_by_boundary.items()
        },
        **{
            f"deployment-receipt-{action}-{boundary_id}": copy.deepcopy(ref)
            for action, refs_by_boundary in (
                deployment_receipt_ref_by_action_and_boundary_id.items()
            )
            for boundary_id, ref in refs_by_boundary.items()
        },
    }
    m2_qualification_creation_evaluation = (
        build_execution_authorization_evaluation(
            corpus,
            state["planning_v2"],
            task_id=state["planning_v2"]["m2_qualification_creation_node_id"],
            action_instance_id="fixture-action-create-m2-qualification-context",
            filename_slug="m2-qualification-context-creation-pass",
            authorization_binding_context_ref=m2_bootstrap_ref,
            environment_class="governance-workspace",
            phase_barrier_ids=["milestone-entry/M2"],
            actor_person_id="fixture-contract-owner-person",
            additional_prerequisite_ref_by_kind=(
                qualification_creation_prerequisites
            ),
            evaluated_at="2026-06-01T12:10:00Z",
        )
        if context_only
        else state["context_v2"]["m2_qualification_creation_evaluation"]
    )

    m2_body = {
        "context_kind": "M2_QUALIFICATION",
        "bootstrap_context_ref": copy.deepcopy(m2_bootstrap_ref),
        "bootstrap_creation_evaluation_ref": copy.deepcopy(
            state["m2_bootstrap_creation_evaluation"]["ref"]
        ),
        "creation_evaluation_ref": copy.deepcopy(
            m2_qualification_creation_evaluation["ref"]
        ),
        "scope": copy.deepcopy(m2_scope),
        "implementation_evaluation_ref_by_action": copy.deepcopy(
            implementation_evaluation_ref_by_action
        ),
        "implementation_action_receipt_ref_by_action": copy.deepcopy(
            implementation_action_receipt_ref_by_action
        ),
        "deployment_evaluation_ref_by_action_and_boundary_id": copy.deepcopy(
            deployment_evaluation_ref_by_action_and_boundary_id
        ),
        "deployment_receipt_ref_by_action_and_boundary_id": copy.deepcopy(
            deployment_receipt_ref_by_action_and_boundary_id
        ),
        "consumer_deployment_set_ref": copy.deepcopy(
            state["consumer_deployment_set_ref"]
        ),
        "consumer_deployment_set_sha256": state["consumer_deployment_set_ref"][
            "artifact_sha256"
        ],
        "contract_release_ref": copy.deepcopy(contract_release_ref),
        "contract_release_id": contract_release_value["contract_release_id"],
        "contract_release_sha256": state["contract_release_sha"],
        "product_contract_sha256": state["product_contract_sha"],
        "qualification_governance_contract_sha256": state[
            "qualification_contract_sha"
        ],
        "fixture_corpus_ref": copy.deepcopy(state["fixture_corpus"]["ref"]),
        "fixture_corpus_sha256": state["fixture_corpus"]["sha"],
        "deployment_state": "target-disabled",
    }
    m2_context, m2_sha, m2_ref = add_context(
        "VALID-BINDING-CONTEXT-V2-M2-01",
        "binding-context-v2-m2.json",
        "fixture-binding-context-v2-m2-qualification",
        "M2",
        m2_body,
        "contract-owner",
        "release-owner",
        created_at="2026-06-01T12:11:00Z",
    )

    transition_evaluation_by_key: dict[str, dict[str, Any]] = {}

    def transition_evaluation(
        transition_key: str,
        *,
        binding_context_ref: dict[str, Any],
        environment_class: str,
        phase_barrier_ids: list[str],
        prerequisite_ref_by_kind: dict[str, dict[str, Any]],
        actor_person_id: str,
        evaluated_at: str,
    ) -> dict[str, Any]:
        if not context_only:
            return state["context_v2"]["transition_evaluation_by_key"][
                transition_key
            ]
        evaluation_state = build_execution_authorization_evaluation(
            corpus,
            state["planning_v2"],
            task_id=state["planning_v2"]["transition_node_id_by_key"][
                transition_key
            ],
            action_instance_id=f"fixture-action-{transition_key}",
            filename_slug=f"{transition_key}-pass",
            authorization_binding_context_ref=binding_context_ref,
            environment_class=environment_class,
            phase_barrier_ids=phase_barrier_ids,
            actor_person_id=actor_person_id,
            additional_prerequisite_ref_by_kind=prerequisite_ref_by_kind,
            evaluated_at=evaluated_at,
        )
        transition_evaluation_by_key[transition_key] = evaluation_state
        return evaluation_state

    def transition_receipt(
        transition_key: str,
        evaluation_state: dict[str, Any],
        output_ref: dict[str, Any],
        *,
        started_at: str,
        completed_at: str,
        readback_at: str,
    ) -> dict[str, Any]:
        receipt_filename = f"transition-receipt-{transition_key}.json"
        receipt_value = {
            "schema": "ylx.stage-transition-action-receipt.v1",
            "receipt_id": f"fixture-transition-receipt-{transition_key}",
            "execution_authorization_evaluation_ref": copy.deepcopy(
                evaluation_state["ref"]
            ),
            "action_instance_id": evaluation_state["value"][
                "action_instance_id"
            ],
            "planned_action_input_sha256": evaluation_state["value"][
                "planned_action_input_sha256"
            ],
            "authorization_action": evaluation_state["value"][
                "authorization_action"
            ],
            "actor_person_id": evaluation_state["value"]["actor_person_id"],
            "execution_environment_ref": copy.deepcopy(
                evaluation_state["value"]["authorization_environment_ref"]
            ),
            "output_ref": copy.deepcopy(output_ref),
            "output_sha256": output_ref["artifact_sha256"],
            "started_at": started_at,
            "completed_at": completed_at,
            "readback_ref": copy.deepcopy(output_ref),
            "readback_sha256": output_ref["artifact_sha256"],
            "readback_at": readback_at,
            "readback_matches_expected": True,
            "notice": NOTICE,
        }
        receipt_sha = corpus.add_support(
            receipt_filename,
            receipt_value,
            f"Synthetic exact transition receipt for {transition_key}.",
        )
        return artifact_ref(
            receipt_value["receipt_id"],
            receipt_value["schema"],
            receipt_sha,
            (
                "contracts/fixtures/governance-models/support/"
                f"{receipt_filename}"
            ),
            1,
        )

    full_m2_prerequisites = {
        "m2_qualification_context": copy.deepcopy(m2_ref),
        "contract_release": copy.deepcopy(contract_release_ref),
        "fixture_corpus": copy.deepcopy(state["fixture_corpus"]["ref"]),
        "consumer_deployment_set": copy.deepcopy(
            state["consumer_deployment_set_ref"]
        ),
    }
    m3_base_bundle_ref = artifact_ref(
        "fixture-m3-base-bundle",
        "ylx.m3-base-bundle.v1",
        state["m3_base_bundle_sha"],
        "contracts/fixtures/governance-models/support/m3-base-bundle.json",
        1,
    )
    m3_transition_receipts: dict[str, dict[str, Any]] = {}
    for (
        transition_key,
        environment_class,
        actor_person_id,
        evaluated_at,
        started_at,
        completed_at,
        readback_at,
        output_ref,
    ) in (
        (
            "m3-implement-product",
            "isolated-development",
            "fixture-qa-evidence-owner-person",
            "2026-06-01T12:11:10Z",
            "2026-06-01T12:11:11Z",
            "2026-06-01T12:11:18Z",
            "2026-06-01T12:11:19Z",
            contract_release_ref,
        ),
        (
            "m3-build-target-disabled",
            "isolated-build",
            "fixture-build-platform-owner-person",
            "2026-06-01T12:11:20Z",
            "2026-06-01T12:11:21Z",
            "2026-06-01T12:11:28Z",
            "2026-06-01T12:11:29Z",
            m3_base_bundle_ref,
        ),
        (
            "m3-run-integration-smoke",
            "isolated-integration",
            "fixture-qa-evidence-owner-person",
            "2026-06-01T12:11:30Z",
            "2026-06-01T12:11:31Z",
            "2026-06-01T12:11:38Z",
            "2026-06-01T12:11:39Z",
            m3_base_bundle_ref,
        ),
    ):
        evaluation_state = transition_evaluation(
            transition_key,
            binding_context_ref=m2_bootstrap_ref,
            environment_class=environment_class,
            phase_barrier_ids=["milestone-entry/M3"],
            prerequisite_ref_by_kind=copy.deepcopy(full_m2_prerequisites),
            actor_person_id=actor_person_id,
            evaluated_at=evaluated_at,
        )
        m3_transition_receipts[transition_key] = transition_receipt(
            transition_key,
            evaluation_state,
            output_ref,
            started_at=started_at,
            completed_at=completed_at,
            readback_at=readback_at,
        )

    m3_creation_evaluation = transition_evaluation(
        "m3-create-context",
        binding_context_ref=m2_ref,
        environment_class="governance-workspace",
        phase_barrier_ids=["milestone-entry/M3"],
        prerequisite_ref_by_kind={
            **copy.deepcopy(full_m2_prerequisites),
            **{
                f"transition-receipt-{key}": copy.deepcopy(ref)
                for key, ref in m3_transition_receipts.items()
            },
        },
        actor_person_id="fixture-qa-evidence-owner-person",
        evaluated_at="2026-06-01T12:11:50Z",
    )

    m3_body = copy.deepcopy(state["m3_context"]["body"])
    m3_body.update(
        {
            "creation_evaluation_ref": copy.deepcopy(
                m3_creation_evaluation["ref"]
            ),
            "m2_binding_context_ref": copy.deepcopy(m2_ref),
        }
    )
    m3_context, m3_sha, m3_ref = add_context(
        "VALID-BINDING-CONTEXT-V2-M3-01",
        "binding-context-v2-m3.json",
        "fixture-binding-context-v2-m3",
        "M3",
        m3_body,
        "qa-evidence-owner",
        "security-owner",
        created_at="2026-06-01T12:12:00Z",
    )

    m4_component_receipts: dict[str, dict[str, Any]] = {}
    for (
        transition_key,
        environment_class,
        evaluated_at,
        started_at,
        completed_at,
        readback_at,
        output_ref,
    ) in (
        (
            "m4-implement-product",
            "isolated-development",
            "2026-06-01T12:12:10Z",
            "2026-06-01T12:12:11Z",
            "2026-06-01T12:12:16Z",
            "2026-06-01T12:12:17Z",
            contract_release_ref,
        ),
        (
            "m4-build-target-disabled",
            "isolated-build",
            "2026-06-01T12:12:20Z",
            "2026-06-01T12:12:21Z",
            "2026-06-01T12:12:26Z",
            "2026-06-01T12:12:27Z",
            m3_base_bundle_ref,
        ),
    ):
        evaluation_state = transition_evaluation(
            transition_key,
            binding_context_ref=m2_bootstrap_ref,
            environment_class=environment_class,
            phase_barrier_ids=["m4-start/component-contract"],
            prerequisite_ref_by_kind=copy.deepcopy(full_m2_prerequisites),
            actor_person_id="fixture-build-platform-owner-person",
            evaluated_at=evaluated_at,
        )
        m4_component_receipts[transition_key] = transition_receipt(
            transition_key,
            evaluation_state,
            output_ref,
            started_at=started_at,
            completed_at=completed_at,
            readback_at=readback_at,
        )

    assembly_ref = artifact_ref(
        state["candidate_id"],
        "ylx.m4-candidate-assembly.v1",
        state["assembly_sha"],
        "contracts/fixtures/governance-models/valid/m4-candidate-assembly.json",
        1,
    )
    m4_base_receipts: dict[str, dict[str, Any]] = {}
    for (
        transition_key,
        environment_class,
        actor_person_id,
        evaluated_at,
        started_at,
        completed_at,
        readback_at,
    ) in (
        (
            "m4-assemble-target",
            "isolated-assembly",
            "fixture-build-platform-owner-person",
            "2026-06-01T12:12:30Z",
            "2026-06-01T12:12:31Z",
            "2026-06-01T12:12:36Z",
            "2026-06-01T12:12:37Z",
        ),
        (
            "m4-run-integration-smoke",
            "isolated-integration",
            "fixture-qa-evidence-owner-person",
            "2026-06-01T12:12:40Z",
            "2026-06-01T12:12:41Z",
            "2026-06-01T12:12:46Z",
            "2026-06-01T12:12:47Z",
        ),
    ):
        evaluation_state = transition_evaluation(
            transition_key,
            binding_context_ref=m3_ref,
            environment_class=environment_class,
            phase_barrier_ids=["m4-start/base-tuple"],
            prerequisite_ref_by_kind={
                "m3_binding_context": copy.deepcopy(m3_ref),
                "m2_qualification_context": copy.deepcopy(m2_ref),
                "contract_release": copy.deepcopy(contract_release_ref),
                **{
                    f"component-receipt-{key}": copy.deepcopy(ref)
                    for key, ref in m4_component_receipts.items()
                },
            },
            actor_person_id=actor_person_id,
            evaluated_at=evaluated_at,
        )
        m4_base_receipts[transition_key] = transition_receipt(
            transition_key,
            evaluation_state,
            assembly_ref,
            started_at=started_at,
            completed_at=completed_at,
            readback_at=readback_at,
        )

    m4_creation_evaluation = transition_evaluation(
        "m4-create-context",
        binding_context_ref=m3_ref,
        environment_class="governance-workspace",
        phase_barrier_ids=["m4-start/base-tuple"],
        prerequisite_ref_by_kind={
            "m3_binding_context": copy.deepcopy(m3_ref),
            "m2_qualification_context": copy.deepcopy(m2_ref),
            "contract_release": copy.deepcopy(contract_release_ref),
            **{
                f"base-receipt-{key}": copy.deepcopy(ref)
                for key, ref in m4_base_receipts.items()
            },
        },
        actor_person_id="fixture-build-platform-owner-person",
        evaluated_at="2026-06-01T12:12:50Z",
    )
    m4_creation_evaluation_r2 = (
        build_execution_authorization_evaluation(
            corpus,
            state["planning_v2"],
            task_id=state["planning_v2"]["transition_node_id_by_key"][
                "m4-create-context"
            ],
            action_instance_id="fixture-action-m4-create-context-r2",
            filename_slug="m4-create-context-r2-pass",
            authorization_binding_context_ref=m3_ref,
            environment_class="governance-workspace",
            phase_barrier_ids=["m4-start/base-tuple"],
            actor_person_id="fixture-build-platform-owner-person",
            additional_prerequisite_ref_by_kind={
                "m3_binding_context": copy.deepcopy(m3_ref),
                "m2_qualification_context": copy.deepcopy(m2_ref),
                "contract_release": copy.deepcopy(contract_release_ref),
                **{
                    f"base-receipt-{key}": copy.deepcopy(ref)
                    for key, ref in m4_base_receipts.items()
                },
            },
            evaluated_at="2026-06-01T12:13:05Z",
        )
        if context_only
        else state["context_v2"]["m4_creation_evaluation_r2"]
    )

    lineage_id = "fixture-m4-context-lineage-v2"
    m4_context_id = "fixture-binding-context-v2-m4"
    source_m4 = corpus.values["valid/binding-context-m4.json"]
    target_m4 = state["m4_context"]

    def m4_body(
        legacy_context: dict[str, Any],
        revision: int,
        predecessor_ref: dict[str, Any] | None,
        plan_digest: str,
        plan_filename: str,
        creation_evaluation: dict[str, Any],
    ) -> dict[str, Any]:
        legacy_body = legacy_context["body"]
        return {
            "creation_evaluation_ref": copy.deepcopy(
                creation_evaluation["ref"]
            ),
            "candidate_id": legacy_body["candidate_id"],
            "predecessor_candidate_id": legacy_body["predecessor_candidate_id"],
            "product_assembly_sha256": legacy_body["product_assembly_sha256"],
            "qualification_lineage_id": lineage_id,
            "predecessor_m4_context_ref": predecessor_ref,
            "m3_binding_context_ref": copy.deepcopy(m3_ref),
            "base_core_input_projection_sha256": legacy_body[
                "base_core_input_projection_sha256"
            ],
            "qualification_bundle_sha256": legacy_body[
                "qualification_bundle_sha256"
            ],
            "rendered_config_sha256": legacy_body["rendered_config_sha256"],
            "contract_release_sha256": legacy_body["contract_release_sha256"],
            "product_contract_sha256": legacy_body["product_contract_sha256"],
            "qualification_governance_contract_sha256": legacy_body[
                "qualification_governance_contract_sha256"
            ],
            "qualification_revision": revision,
            "qualification_plan_ref": valid_ref(
                "fixture-qualification-plan",
                "ylx.qualification-plan.v1",
                plan_digest,
                plan_filename,
                revision,
            ),
            "qualification_input_sha256_by_id": copy.deepcopy(
                legacy_body["qualification_input_sha256_by_id"]
            ),
            "component_impact_graph_sha256": legacy_body[
                "component_impact_graph_sha256"
            ],
        }

    m4_r1, m4_r1_sha, m4_r1_ref = add_context(
        "VALID-BINDING-CONTEXT-V2-M4-R1-01",
        "binding-context-v2-m4-r1.json",
        m4_context_id,
        "M4",
        m4_body(
            source_m4,
            1,
            None,
            state["qualification_source_sha"],
            "qualification-plan.json",
            m4_creation_evaluation,
        ),
        "build-platform-owner",
        "qa-evidence-owner",
        created_at="2026-06-01T12:13:00Z",
    )
    m4_r2, m4_r2_sha, m4_r2_ref = add_context(
        "VALID-BINDING-CONTEXT-V2-M4-R2-01",
        "binding-context-v2-m4-r2.json",
        m4_context_id,
        "M4",
        m4_body(
            target_m4,
            2,
            m4_r1_ref,
            state["qualification_target_sha"],
            "qualification-plan-target.json",
            m4_creation_evaluation_r2,
        ),
        "build-platform-owner",
        "qa-evidence-owner",
        revision=2,
        predecessor_ref=m4_r1_ref,
        created_at="2026-06-01T12:13:10Z",
    )

    m4_target_deployment_filename = "m4-target-disabled-deployment.json"
    m4_target_deployment_value = {
        "schema": "ylx.m4-target-disabled-deployment.v1",
        "deployment_id": "fixture-m4-target-disabled-deployment",
        "revision": 1,
        "binding_context_ref": copy.deepcopy(m4_r2_ref),
        "assembly_ref": copy.deepcopy(assembly_ref),
        "deployment_state": "target-disabled",
        "producer_target_write_enabled": False,
        "created_at": "2026-06-01T12:13:47Z",
        "notice": NOTICE,
    }
    m4_target_deployment_sha = corpus.add_support(
        m4_target_deployment_filename,
        m4_target_deployment_value,
        "Synthetic exact target-disabled M4 deployment bytes.",
    )
    m4_target_deployment_ref = artifact_ref(
        m4_target_deployment_value["deployment_id"],
        m4_target_deployment_value["schema"],
        m4_target_deployment_sha,
        (
            "contracts/fixtures/governance-models/support/"
            f"{m4_target_deployment_filename}"
        ),
        m4_target_deployment_value["revision"],
    )
    m4_deployment_receipts: dict[str, dict[str, Any]] = {}
    for (
        transition_key,
        environment_class,
        evaluated_at,
        output_ref,
        started_at,
        completed_at,
        readback_at,
    ) in (
        (
            "m4-install-target",
            "qualification-target",
            "2026-06-01T12:13:20Z",
            assembly_ref,
            "2026-06-01T12:13:21Z",
            "2026-06-01T12:13:24Z",
            "2026-06-01T12:13:25Z",
        ),
        (
            "m4-configure-target",
            "qualification-target",
            "2026-06-01T12:13:30Z",
            assembly_ref,
            "2026-06-01T12:13:31Z",
            "2026-06-01T12:13:34Z",
            "2026-06-01T12:13:35Z",
        ),
        (
            "m4-deploy-target-disabled",
            "target-disabled-non-production",
            "2026-06-01T12:13:40Z",
            m4_target_deployment_ref,
            "2026-06-01T12:13:41Z",
            "2026-06-01T12:13:47Z",
            "2026-06-01T12:13:48Z",
        ),
    ):
        evaluation_state = transition_evaluation(
            transition_key,
            binding_context_ref=m4_r2_ref,
            environment_class=environment_class,
            phase_barrier_ids=["m4-start/deployment"],
            prerequisite_ref_by_kind={
                "m3_binding_context": copy.deepcopy(m3_ref),
                "m4_binding_context": copy.deepcopy(m4_r2_ref),
                "assembly": copy.deepcopy(assembly_ref),
                **{
                    f"base-receipt-{key}": copy.deepcopy(ref)
                    for key, ref in m4_base_receipts.items()
                },
            },
            actor_person_id="fixture-build-platform-owner-person",
            evaluated_at=evaluated_at,
        )
        m4_deployment_receipts[transition_key] = transition_receipt(
            transition_key,
            evaluation_state,
            output_ref,
            started_at=started_at,
            completed_at=completed_at,
            readback_at=readback_at,
        )

    lineage_r1_map = {"1": copy.deepcopy(m4_r1_ref)}
    lineage_r1 = {
        "schema": "ylx.m4-context-lineage.v1",
        "lineage_id": lineage_id,
        "revision": 1,
        "predecessor_lineage_ref": None,
        "candidate_id": state["candidate_id"],
        "predecessor_candidate_id": target_m4["body"]["predecessor_candidate_id"],
        "m3_binding_context_ref": copy.deepcopy(m3_ref),
        "context_ref_by_qualification_revision": lineage_r1_map,
        "tip_context_ref": copy.deepcopy(m4_r1_ref),
        "context_set_sha256": sha(canonical_bytes(lineage_r1_map)),
        "lineage_mode": "SINGLE_DIRECT_PREDECESSOR_CHAIN",
        "tip_selection_rule": "HIGHEST_CONTIGUOUS_REVISION",
        "fork_policy": "REJECT",
        "created_at": "2026-06-01T12:13:50Z",
        "artifact_metadata": metadata(),
    }
    lineage_r1_sha = add_or_reuse(
        "VALID-M4-CONTEXT-LINEAGE-R1-01",
        "m4-context-lineage-r1.json",
        "m4-context-lineage-v1.schema.json",
        lineage_r1,
    )
    lineage_r1_ref = valid_ref(
        lineage_id,
        lineage_r1["schema"],
        lineage_r1_sha,
        "m4-context-lineage-r1.json",
        1,
    )
    lineage_r2_map = {
        "1": copy.deepcopy(m4_r1_ref),
        "2": copy.deepcopy(m4_r2_ref),
    }
    lineage_r2 = copy.deepcopy(lineage_r1)
    lineage_r2.update(
        {
            "revision": 2,
            "predecessor_lineage_ref": lineage_r1_ref,
            "context_ref_by_qualification_revision": lineage_r2_map,
            "tip_context_ref": copy.deepcopy(m4_r2_ref),
            "context_set_sha256": sha(canonical_bytes(lineage_r2_map)),
            "created_at": "2026-06-01T12:13:55Z",
        }
    )
    lineage_r2_sha = add_or_reuse(
        "VALID-M4-CONTEXT-LINEAGE-R2-01",
        "m4-context-lineage-r2.json",
        "m4-context-lineage-v1.schema.json",
        lineage_r2,
    )
    lineage_r2_ref = valid_ref(
        lineage_id,
        lineage_r2["schema"],
        lineage_r2_sha,
        "m4-context-lineage-r2.json",
        2,
    )

    m5_rc_build_evaluation = transition_evaluation(
        "m5-build-prerelease-rc",
        binding_context_ref=m4_r2_ref,
        environment_class="isolated-build",
        phase_barrier_ids=["milestone-entry/M5"],
        prerequisite_ref_by_kind={
            "effective_m4_binding_context": copy.deepcopy(m4_r2_ref),
            "effective_m4_lineage": copy.deepcopy(lineage_r2_ref),
            "m4_target_deployment": copy.deepcopy(m4_target_deployment_ref),
            **{
                f"deployment-receipt-{key}": copy.deepcopy(ref)
                for key, ref in m4_deployment_receipts.items()
            },
        },
        actor_person_id="fixture-build-platform-owner-person",
        evaluated_at="2026-06-01T12:14:00Z",
    )
    m5_rc_build_receipt = transition_receipt(
        "m5-build-prerelease-rc",
        m5_rc_build_evaluation,
        state["planning_v2"]["bundle_ref"],
        started_at="2026-06-01T12:14:01Z",
        completed_at="2026-06-01T12:14:04Z",
        readback_at="2026-06-01T12:14:05Z",
    )
    production_binding_filename = "m5-production-binding.json"
    production_binding_value = {
        "schema": "ylx.m5-production-binding.v1",
        "binding_id": "fixture-production-binding-v2",
        "revision": 1,
        "effective_m4_binding_context_ref": copy.deepcopy(m4_r2_ref),
        "release_bundle_ref": copy.deepcopy(state["planning_v2"]["bundle_ref"]),
        "writer_enabled": False,
        "created_at": "2026-06-01T12:14:06Z",
        "notice": NOTICE,
    }
    production_binding_sha = corpus.add_support(
        production_binding_filename,
        production_binding_value,
        "Synthetic exact M5 writer-disabled production binding bytes.",
    )
    production_binding_ref = artifact_ref(
        production_binding_value["binding_id"],
        production_binding_value["schema"],
        production_binding_sha,
        (
            "contracts/fixtures/governance-models/support/"
            f"{production_binding_filename}"
        ),
        production_binding_value["revision"],
    )
    m5_creation_evaluation = transition_evaluation(
        "m5-create-context",
        binding_context_ref=m4_r2_ref,
        environment_class="governance-workspace",
        phase_barrier_ids=["milestone-entry/M5"],
        prerequisite_ref_by_kind={
            "effective_m4_binding_context": copy.deepcopy(m4_r2_ref),
            "effective_m4_lineage": copy.deepcopy(lineage_r2_ref),
            "release_bundle": copy.deepcopy(state["planning_v2"]["bundle_ref"]),
            "production_binding": copy.deepcopy(production_binding_ref),
            "rc_build_receipt": copy.deepcopy(m5_rc_build_receipt),
        },
        actor_person_id="fixture-release-owner-person",
        evaluated_at="2026-06-01T12:14:10Z",
    )

    m5_body = {
        "creation_evaluation_ref": copy.deepcopy(m5_creation_evaluation["ref"]),
        "candidate_id": state["candidate_id"],
        "release_bundle_sha256": state["planning_v2"]["bundle_sha"],
        "production_binding_sha256": production_binding_sha,
        "contract_release_sha256": state["contract_release_sha"],
        "product_contract_sha256": state["product_contract_sha"],
        "qualification_governance_contract_sha256": state[
            "qualification_contract_sha"
        ],
        "effective_m4_lineage_ref": copy.deepcopy(lineage_r2_ref),
        "effective_m4_binding_context_ref": copy.deepcopy(m4_r2_ref),
    }
    m5_context, m5_sha, m5_ref = add_context(
        "VALID-BINDING-CONTEXT-V2-M5-01",
        "binding-context-v2-m5.json",
        "fixture-binding-context-v2-m5",
        "M5",
        m5_body,
        "release-owner",
        "qa-evidence-owner",
        created_at="2026-06-01T12:14:20Z",
    )

    m5_prerelease_publication_evaluation = transition_evaluation(
        "m5-publish-prerelease-rc",
        binding_context_ref=m5_ref,
        environment_class="immutable-prerelease-artifact-channel",
        phase_barrier_ids=["milestone-entry/M5"],
        prerequisite_ref_by_kind={
            "m5_binding_context": copy.deepcopy(m5_ref),
            "release_bundle": copy.deepcopy(state["planning_v2"]["bundle_ref"]),
            "rc_build_receipt": copy.deepcopy(m5_rc_build_receipt),
        },
        actor_person_id="fixture-build-platform-owner-person",
        evaluated_at="2026-06-01T12:14:30Z",
    )
    m5_prerelease_publication_receipt = transition_receipt(
        "m5-publish-prerelease-rc",
        m5_prerelease_publication_evaluation,
        state["planning_v2"]["bundle_ref"],
        started_at="2026-06-01T12:14:31Z",
        completed_at="2026-06-01T12:14:34Z",
        readback_at="2026-06-01T12:14:35Z",
    )

    context_state = {
        "m2_bootstrap_context": m2_bootstrap_context,
        "m2_bootstrap_sha": m2_bootstrap_sha,
        "m2_bootstrap_ref": m2_bootstrap_ref,
        "m2_implementation_evaluation_by_action": (
            implementation_evaluation_by_action
        ),
        "m2_implementation_action_receipt_by_action": (
            implementation_action_receipt_by_action
        ),
        "m2_implementation_action_receipt_ref_by_action": (
            implementation_action_receipt_ref_by_action
        ),
        "m2_deployment_receipt_by_action_and_boundary_id": (
            deployment_receipt_by_action_and_boundary_id
        ),
        "m2_deployment_receipt_ref_by_action_and_boundary_id": (
            deployment_receipt_ref_by_action_and_boundary_id
        ),
        "m2_deployment_evaluation_by_action_and_boundary_id": (
            deployment_evaluation_by_action_and_boundary_id
        ),
        "m2_qualification_creation_evaluation": (
            m2_qualification_creation_evaluation
        ),
        "m2_context": m2_context,
        "m2_sha": m2_sha,
        "m2_ref": m2_ref,
        "m3_context": m3_context,
        "m3_sha": m3_sha,
        "m3_ref": m3_ref,
        "m4_r1": m4_r1,
        "m4_r1_sha": m4_r1_sha,
        "m4_r1_ref": m4_r1_ref,
        "m4_r2": m4_r2,
        "m4_r2_sha": m4_r2_sha,
        "m4_r2_ref": m4_r2_ref,
        "lineage": lineage_r2,
        "lineage_sha": lineage_r2_sha,
        "lineage_ref": lineage_r2_ref,
        "m5_context": m5_context,
        "m5_sha": m5_sha,
        "m5_ref": m5_ref,
        "m3_creation_evaluation": m3_creation_evaluation,
        "m4_creation_evaluation": m4_creation_evaluation,
        "m4_creation_evaluation_r2": m4_creation_evaluation_r2,
        "m4_deployment_receipts": m4_deployment_receipts,
        "m4_target_deployment_ref": m4_target_deployment_ref,
        "m5_creation_evaluation": m5_creation_evaluation,
        "m5_prerelease_publication_evaluation": (
            m5_prerelease_publication_evaluation
        ),
        "m5_prerelease_publication_receipt": (
            m5_prerelease_publication_receipt
        ),
        "transition_evaluation_by_key": transition_evaluation_by_key,
    }
    if context_only:
        return context_state

    legacy_projection = corpus.values["valid/release-result-projection.json"]
    derived_ids = {"M5-MATRIX-COMPLETE-01", "M5-SIGNOFF-01"}
    core_requirement_ids = sorted(set(state["requirement_ids"]) - derived_ids)
    source_ref_by_gate = {
        "M2": m2_ref,
        "M3": m3_ref,
        "M4a": m4_r2_ref,
        "M4b": m4_r2_ref,
        "M4c": m4_r2_ref,
        "M4d": m4_r2_ref,
        "M4": m4_r2_ref,
        "M5": m5_ref,
    }
    for gate in ("M0", "M1"):
        source_ref_by_gate[gate] = copy.deepcopy(
            next(
                row["source_scope_ref"]
                for row in legacy_projection["row_projection_by_requirement_id"].values()
                if row["closing_gate"] == gate
            )
        )

    evidence_binding_ref_by_requirement_id: dict[str, dict[str, Any]] = {}
    evidence_id_by_requirement_id: dict[str, str] = {}
    evidence_record_sha256_by_id: dict[str, str] = {}
    selected_evidence_binding_refs: dict[str, dict[str, Any]] = {}
    for gate, issue_requirement_id in ISSUE_REQUIREMENT_BY_GATE.items():
        gate_requirement_ids = [
            requirement_id
            for requirement_id in core_requirement_ids
            if state["closing_gate_by_id"][requirement_id] == gate
        ]
        gate_batches = (
            [
                (
                    phase,
                    [
                        requirement_id
                        for requirement_id in gate_requirement_ids
                        if state["execution_phase_by_id"][requirement_id] == phase
                    ],
                )
                for phase in M5_EXECUTION_PHASES
            ]
            if gate == "M5"
            else [(None, gate_requirement_ids)]
        )
        for execution_phase, batch_requirement_ids in gate_batches:
            non_issue_ids = [
                requirement_id
                for requirement_id in batch_requirement_ids
                if requirement_id != issue_requirement_id
            ]
            if not non_issue_ids:
                continue
            gate_slug = gate.lower()
            batch_slug = (
                f"{gate_slug}-{execution_phase.replace('_', '-')}"
                if execution_phase is not None
                else gate_slug
            )
            legacy_binding_path = (
                SUPPORT_ROOT / f"stage-evidence-binding-{batch_slug}.json"
            )
            binding = json.loads(legacy_binding_path.read_bytes())
            evaluation_key = (
                f"M5:{execution_phase}" if execution_phase is not None else gate
            )
            evaluation_state = state["stage_evaluation_by_batch"][evaluation_key]
            source_scope_ref = source_ref_by_gate[gate]
            evidence_id = f"fixture-stage-evidence-v2-{batch_slug}"
            legacy_evidence_record = json.loads(
                (SUPPORT_ROOT / f"stage-evidence-record-{batch_slug}.json").read_bytes()
            )
            evidence_payload = copy.deepcopy(legacy_evidence_record)
            evidence_payload.update(
                {
                    "evidence_id": evidence_id,
                    "source_scope_ref": copy.deepcopy(source_scope_ref),
                    "requirement_ids": non_issue_ids,
                    "authorization_binding_context_ref": copy.deepcopy(
                        evaluation_state["value"][
                            "authorization_binding_context_ref"
                        ]
                    ),
                    "execution_authorization_evaluation_ref": copy.deepcopy(
                        evaluation_state["ref"]
                    ),
                    "action_instance_id": evaluation_state["value"][
                        "action_instance_id"
                    ],
                    "planned_action_input_sha256": evaluation_state["value"][
                        "planned_action_input_sha256"
                    ],
                    "actor_person_id": evaluation_state["value"]["actor_person_id"],
                    "authorization_action": evaluation_state["value"][
                        "authorization_action"
                    ],
                    "authorization_environment_class": evaluation_state["value"][
                        "authorization_environment_class"
                    ],
                }
            )
            evidence_filename = f"stage-evidence-record-v2-{batch_slug}.json"
            evidence_sha = corpus.add_support(
                evidence_filename,
                evidence_payload,
                f"Exact v2 non-issue stage evidence for the {batch_slug} rows.",
            )
            binding.update(
                {
                    "binding_id": f"fixture-stage-evidence-binding-v2-{batch_slug}",
                    "binding_context_ref": {
                        "context_id": source_scope_ref["artifact_id"],
                        "artifact_path": source_scope_ref["artifact_path"],
                        "artifact_sha256": source_scope_ref["artifact_sha256"],
                    },
                    "reverse_coverage": [
                        row
                        for row in binding["reverse_coverage"]
                        if row["requirement_id"] in non_issue_ids
                    ],
                }
            )
            binding["evidence_records"][0].update(
                {
                    "evidence_id": evidence_id,
                    "artifact_path": (
                        "contracts/fixtures/governance-models/support/"
                        f"{evidence_filename}"
                    ),
                    "artifact_sha256": evidence_sha,
                    "execution_authorization_evaluation_ref": copy.deepcopy(
                        evaluation_state["ref"]
                    ),
                    "action_instance_id": evaluation_state["value"][
                        "action_instance_id"
                    ],
                    "planned_action_input_sha256": evaluation_state["value"][
                        "planned_action_input_sha256"
                    ],
                }
            )
            for row in binding["reverse_coverage"]:
                row["evidence_ids"] = [evidence_id]
            binding_filename = f"evidence-binding-v2-{batch_slug}.json"
            binding_sha = corpus.add(
                f"VALID-EVIDENCE-BINDING-V2-{batch_slug.upper()}-01",
                binding_filename,
                "evidence-binding-v1.schema.json",
                binding,
            )
            binding_ref = valid_ref(
                binding["binding_id"],
                binding["schema"],
                binding_sha,
                binding_filename,
                1,
            )
            selected_evidence_binding_refs[binding["binding_id"]] = binding_ref
            evidence_record_sha256_by_id[evidence_id] = evidence_sha
            for requirement_id in non_issue_ids:
                evidence_binding_ref_by_requirement_id[requirement_id] = binding_ref
                evidence_id_by_requirement_id[requirement_id] = evidence_id

    for gate, requirement_id in ISSUE_REQUIREMENT_BY_GATE.items():
        source_scope_ref = source_ref_by_gate[gate]
        verdict_ref = state["issue_verdicts"]["ref_by_gate"][gate]
        verdict_value = state["issue_verdicts"]["value_by_gate"][gate]
        verdict_evaluation = state["issue_verdicts"]["evaluation_by_gate"][gate]
        binding = {
            "schema": "ylx.evidence-binding.v1",
            "binding_id": f"fixture-issue-verdict-evidence-binding-v2-{gate.lower()}",
            "created_at": "2026-06-01T12:47:00Z",
            "binding_context_ref": {
                "context_id": source_scope_ref["artifact_id"],
                "artifact_path": source_scope_ref["artifact_path"],
                "artifact_sha256": source_scope_ref["artifact_sha256"],
            },
            "execution_context_refs": [
                {
                    "context_id": "fixture-execution-context-source",
                    "artifact_path": (
                        "contracts/fixtures/governance-models/valid/execution-context.json"
                    ),
                    "artifact_sha256": state["execution_source_sha"],
                }
            ],
            "required_execution_context_ids": ["fixture-execution-context-source"],
            "evidence_records": [
                {
                    "evidence_id": verdict_value["verdict_id"],
                    "evidence_record_kind": "run-evidence",
                    "artifact_path": verdict_ref["artifact_path"],
                    "artifact_sha256": verdict_ref["artifact_sha256"],
                    "execution_context_ids": ["fixture-execution-context-source"],
                    "actor_deployment_record_sha256": None,
                    "execution_authorization_evaluation_ref": copy.deepcopy(
                        verdict_evaluation["ref"]
                    ),
                    "action_instance_id": verdict_evaluation["value"][
                        "action_instance_id"
                    ],
                    "planned_action_input_sha256": verdict_evaluation["value"][
                        "planned_action_input_sha256"
                    ],
                }
            ],
            "reverse_coverage": [
                {
                    "requirement_id": requirement_id,
                    "execution_context_id": "fixture-execution-context-source",
                    "evidence_ids": [verdict_value["verdict_id"]],
                }
            ],
            "artifact_metadata": metadata(),
        }
        binding_filename = f"issue-verdict-evidence-binding-v2-{gate.lower()}.json"
        binding_sha = corpus.add(
            f"VALID-ISSUE-VERDICT-EVIDENCE-BINDING-V2-{gate.upper()}-01",
            binding_filename,
            "evidence-binding-v1.schema.json",
            binding,
        )
        binding_ref = valid_ref(
            binding["binding_id"],
            binding["schema"],
            binding_sha,
            binding_filename,
            1,
        )
        selected_evidence_binding_refs[binding["binding_id"]] = binding_ref
        evidence_binding_ref_by_requirement_id[requirement_id] = binding_ref
        evidence_id_by_requirement_id[requirement_id] = verdict_value["verdict_id"]
        evidence_record_sha256_by_id[verdict_value["verdict_id"]] = verdict_ref[
            "artifact_sha256"
        ]

    measurement_holdout_state = state["measurement_holdout"]
    measurement_binding = measurement_holdout_state["binding"]
    measurement_binding_ref = copy.deepcopy(
        measurement_holdout_state["binding_ref"]
    )
    measurement_evidence = measurement_holdout_state["evidence"]
    measurement_evidence_id = measurement_evidence["evidence_id"]
    selected_evidence_binding_refs[
        measurement_binding["binding_id"]
    ] = measurement_binding_ref
    evidence_binding_ref_by_requirement_id["M0-MEAS-01"] = (
        measurement_binding_ref
    )
    evidence_id_by_requirement_id["M0-MEAS-01"] = measurement_evidence_id
    evidence_record_sha256_by_id[measurement_evidence_id] = (
        measurement_holdout_state["evidence_sha"]
    )

    result_ref_by_requirement_id: dict[str, dict[str, Any]] = {}
    result_by_requirement_id: dict[str, dict[str, Any]] = {}
    rows: dict[str, Any] = {}
    for requirement_id in core_requirement_ids:
        gate = state["closing_gate_by_id"][requirement_id]
        source_scope_ref = copy.deepcopy(source_ref_by_gate[gate])
        evidence_binding_ref = copy.deepcopy(
            evidence_binding_ref_by_requirement_id[requirement_id]
        )
        evidence_id = evidence_id_by_requirement_id[requirement_id]
        result_id = f"fixture-terminal-result-v2-{requirement_id.lower()}"
        filename = f"stage-terminal-result-v2-{requirement_id.lower()}.json"
        result = {
            "schema": "ylx.stage-terminal-result.v2",
            "result_id": result_id,
            "revision": 1,
            "predecessor_result_ref": None,
            "requirement_id": requirement_id,
            "closing_gate": gate,
            "effective_result": "PASS",
            "source_scope_ref": source_scope_ref,
            "evidence_binding_refs": [evidence_binding_ref],
            "evidence_ids": [evidence_id],
            "approved_na_record_ref": None,
            "supersession_reason": None,
            "created_at": (
                "2026-06-01T12:48:00Z"
                if requirement_id in ISSUE_REQUIREMENT_BY_GATE.values()
                else "2026-06-01T12:30:00Z"
            ),
            "artifact_metadata": metadata(),
        }
        result_sha = corpus.add(
            f"VALID-STAGE-TERMINAL-RESULT-V2-{requirement_id}-01",
            filename,
            "stage-terminal-result-v2.schema.json",
            result,
        )
        result_ref = valid_ref(
            result_id,
            result["schema"],
            result_sha,
            filename,
            1,
        )
        result_ref_by_requirement_id[requirement_id] = result_ref
        result_by_requirement_id[requirement_id] = result
        rows[requirement_id] = {
            "closing_gate": gate,
            "effective_result": result["effective_result"],
            "effective_result_ref": copy.deepcopy(result_ref),
            "source_scope_ref": source_scope_ref,
            "evidence_binding_refs": copy.deepcopy(result["evidence_binding_refs"]),
            "evidence_ids": copy.deepcopy(result["evidence_ids"]),
            "approved_na_record_ref": copy.deepcopy(result["approved_na_record_ref"]),
        }

    algorithm = "stage-native-current-effective-terminal-selection-rfc8785"
    version = "2.0.0"
    gate_order = ["M0", "M1", "M2", "M3", "M4a", "M4b", "M4c", "M4d", "M4", "M5"]
    root_refs: dict[str, dict[str, Any]] = {}
    root_by_gate: dict[str, dict[str, Any]] = {}
    for gate in gate_order:
        selected = {
            requirement_id: copy.deepcopy(result_ref_by_requirement_id[requirement_id])
            for requirement_id in core_requirement_ids
            if state["closing_gate_by_id"][requirement_id] == gate
        }
        root_id = f"fixture-stage-gate-result-root-v2-{gate.lower()}"
        filename = f"stage-gate-result-root-v2-{gate.lower()}.json"
        root = {
            "schema": "ylx.stage-gate-result-root.v2",
            "root_id": root_id,
            "revision": 1,
            "predecessor_root_ref": None,
            "closing_gate": gate,
            "registry_sha256": state["registry_sha"],
            "requirement_id_set_sha256": ascii_set_sha256(set(selected)),
            "source_scope_ref": copy.deepcopy(source_ref_by_gate[gate]),
            "selected_result_ref_by_requirement_id": selected,
            "selection_algorithm": algorithm,
            "selection_version": version,
            "created_at": "2026-06-01T12:50:00Z",
            "artifact_metadata": metadata(),
        }
        root_sha = corpus.add(
            f"VALID-STAGE-GATE-RESULT-ROOT-V2-{gate.upper()}-01",
            filename,
            "stage-gate-result-root-v2.schema.json",
            root,
        )
        root_refs[gate] = valid_ref(
            root_id,
            root["schema"],
            root_sha,
            filename,
            1,
        )
        root_by_gate[gate] = root

    issue_reconciliation_records: list[dict[str, Any]] = []
    for gate, requirement_id in ISSUE_REQUIREMENT_BY_GATE.items():
        verdict = state["issue_verdicts"]["value_by_gate"][gate]
        issue_reconciliation_records.append(
            {
                "gate": gate,
                "requirement_id": requirement_id,
                "effective_terminal_result_ref": copy.deepcopy(
                    result_ref_by_requirement_id[requirement_id]
                ),
                "current_issue_register_head_artifact_path": verdict[
                    "current_issue_register_head_artifact_path"
                ],
                "current_issue_register_revision": verdict[
                    "current_issue_register_revision"
                ],
                "current_issue_register_head_artifact_sha256": verdict[
                    "current_issue_register_head_artifact_sha256"
                ],
                "current_issue_register_sha256": verdict[
                    "current_issue_register_sha256"
                ],
                "current_issue_register_selector_version": verdict[
                    "current_issue_register_selector_version"
                ],
                "current_issue_register_overview_cardinality": verdict[
                    "current_issue_register_overview_cardinality"
                ],
                "selected_issue_ids": copy.deepcopy(verdict["selected_issue_ids"]),
                "selected_issue_slices_by_id": copy.deepcopy(
                    verdict["selected_issue_slices_by_id"]
                ),
            }
        )
    issue_reconciliation_sha = sha(canonical_bytes(issue_reconciliation_records))
    corpus.relationships["issue_register_chain"].update(
        {
            "issue_reconciliation_set_sha256": issue_reconciliation_sha,
            "issue_reconciliation_records": copy.deepcopy(
                issue_reconciliation_records
            ),
        }
    )

    mapping = yaml.safe_load(
        (REPO_ROOT / "docs" / "system-requirement-mapping.yaml").read_text()
    )
    projection_r1 = {
        "schema": "ylx.release-result-projection.v2",
        "projection_id": "fixture-release-result-projection-v2",
        "revision": 1,
        "predecessor_projection_ref": None,
        "m5_binding_context_ref": copy.deepcopy(m5_ref),
        "effective_m4_lineage_ref": copy.deepcopy(lineage_r2_ref),
        "effective_m4_binding_context_ref": copy.deepcopy(m4_r2_ref),
        "registry_sha256": state["registry_sha"],
        "registry_id_set_sha256": ascii_set_sha256(state["requirement_ids"]),
        "core_requirement_id_set_sha256": ascii_set_sha256(core_requirement_ids),
        "core_requirement_cardinality": 171,
        "acceptance_sha256": sha((REPO_ROOT / "docs" / "ACCEPTANCE.md").read_bytes()),
        "system_requirement_mapping_artifact_sha256": sha(
            (REPO_ROOT / "docs" / "system-requirement-mapping.yaml").read_bytes()
        ),
        "system_requirement_mapping_semantic_sha256": system_mapping_semantic_sha256(
            mapping
        ),
        "selected_gate_root_ref_by_closing_gate": copy.deepcopy(root_refs),
        "row_projection_by_requirement_id": copy.deepcopy(rows),
        "evidence_record_sha256_by_id": copy.deepcopy(
            evidence_record_sha256_by_id
        ),
        "issue_head": copy.deepcopy(current_issue_head),
        "issue_reconciliation_set_sha256": issue_reconciliation_sha,
        "projection_algorithm": algorithm,
        "projection_version": version,
        "projector_person_id": "fixture-contract-owner-person",
        "created_at": "2026-06-01T12:51:00Z",
        "artifact_metadata": metadata(),
    }

    def add_content_addressed_projection(
        case_id: str, value: dict[str, Any]
    ) -> tuple[str, str, dict[str, Any]]:
        digest = sha(canonical_bytes(value))
        filename = (
            "release-result-projections/"
            f"{digest}--release-result-projection.json"
        )
        emitted_digest = corpus.add(
            case_id,
            filename,
            "release-result-projection-v2.schema.json",
            value,
        )
        if emitted_digest != digest:
            raise AssertionError("content-addressed projection digest drift")
        return (
            digest,
            filename,
            valid_ref(
                value["projection_id"],
                value["schema"],
                digest,
                filename,
                value["revision"],
            ),
        )

    _, _, projection_r1_ref = (
        add_content_addressed_projection(
            "VALID-RELEASE-RESULT-PROJECTION-V2-R1-01", projection_r1
        )
    )

    witness_requirement_id = next(
        requirement_id
        for requirement_id in core_requirement_ids
        if state["closing_gate_by_id"][requirement_id] == "M2"
        and not requirement_id.endswith("ISSUES-01")
    )
    witness_gate = state["closing_gate_by_id"][witness_requirement_id]
    result_r1 = result_by_requirement_id[witness_requirement_id]
    result_r1_ref = copy.deepcopy(result_ref_by_requirement_id[witness_requirement_id])
    result_r2 = copy.deepcopy(result_r1)
    result_r2.update(
        {
            "revision": 2,
            "predecessor_result_ref": result_r1_ref,
            "supersession_reason": (
                "Re-evaluated the same immutable M2 source tuple to witness current-tip "
                "selection without changing its PASS outcome."
            ),
            "created_at": "2026-06-01T12:52:00Z",
        }
    )
    result_r2_filename = (
        f"stage-terminal-result-v2-{witness_requirement_id.lower()}-r2.json"
    )
    result_r2_sha = corpus.add(
        f"VALID-STAGE-TERMINAL-RESULT-V2-{witness_requirement_id}-R2-01",
        result_r2_filename,
        "stage-terminal-result-v2.schema.json",
        result_r2,
    )
    result_r2_ref = valid_ref(
        result_r2["result_id"],
        result_r2["schema"],
        result_r2_sha,
        result_r2_filename,
        2,
    )
    result_ref_by_requirement_id[witness_requirement_id] = result_r2_ref
    rows[witness_requirement_id]["effective_result_ref"] = copy.deepcopy(result_r2_ref)

    root_r1 = root_by_gate[witness_gate]
    root_r1_ref = copy.deepcopy(root_refs[witness_gate])
    root_r2 = copy.deepcopy(root_r1)
    root_r2.update(
        {
            "revision": 2,
            "predecessor_root_ref": root_r1_ref,
            "selected_result_ref_by_requirement_id": {
                **copy.deepcopy(root_r1["selected_result_ref_by_requirement_id"]),
                witness_requirement_id: copy.deepcopy(result_r2_ref),
            },
            "created_at": "2026-06-01T12:53:00Z",
        }
    )
    root_r2_filename = f"stage-gate-result-root-v2-{witness_gate.lower()}-r2.json"
    root_r2_sha = corpus.add(
        f"VALID-STAGE-GATE-RESULT-ROOT-V2-{witness_gate.upper()}-R2-01",
        root_r2_filename,
        "stage-gate-result-root-v2.schema.json",
        root_r2,
    )
    root_r2_ref = valid_ref(
        root_r2["root_id"],
        root_r2["schema"],
        root_r2_sha,
        root_r2_filename,
        2,
    )
    root_refs[witness_gate] = root_r2_ref

    projection = copy.deepcopy(projection_r1)
    projection.update(
        {
            "revision": 2,
            "predecessor_projection_ref": projection_r1_ref,
            "selected_gate_root_ref_by_closing_gate": copy.deepcopy(root_refs),
            "row_projection_by_requirement_id": copy.deepcopy(rows),
            "created_at": "2026-06-01T13:00:00Z",
        }
    )
    projection_sha = sha(canonical_bytes(projection))
    projection_filename = (
        "release-result-projections/"
        f"{projection_sha}--release-result-projection.json"
    )
    projection_ref = valid_ref(
        projection["projection_id"],
        projection["schema"],
        projection_sha,
        projection_filename,
        projection["revision"],
    )

    owner = state["planning_v2"]["owner"]
    owner_authority_ref = {
        "authority_id": owner["artifact_id"],
        "revision": owner["revision"],
        "artifact_path": (
            "contracts/fixtures/governance-models/valid/"
            "owner-assignment-v2-planning.json"
        ),
        "artifact_sha256": state["planning_v2"]["owner_sha"],
        "verified_at": "2026-06-01T12:04:00Z",
    }
    projector_actor = {
        "actor_id": "fixture-release-result-projector",
        "person_id": projection["projector_person_id"],
        "natural_person_identity_ref": copy.deepcopy(
            state["foundation"]["identity_refs_by_person"][
                projection["projector_person_id"]
            ]
        ),
        "role_slot": "contract-owner",
        "role_assignment_ref": copy.deepcopy(owner_authority_ref),
    }
    selected_result_source_root_sha = sha(canonical_bytes(root_refs))
    selected_evidence_binding_root_sha = sha(
        canonical_bytes(selected_evidence_binding_refs)
    )
    selected_approved_na_root_sha = sha(canonical_bytes({}))
    projection_input = {
        "acceptance_registry_sha256": state["registry_sha"],
        "m5_binding_context_ref": copy.deepcopy(m5_ref),
        "selected_issue_head": copy.deepcopy(current_issue_head),
        "selected_result_source_root_sha256": selected_result_source_root_sha,
        "selected_evidence_binding_root_sha256": selected_evidence_binding_root_sha,
        "selected_approved_na_root_sha256": selected_approved_na_root_sha,
    }
    projection_assignment = {
        "schema": "ylx.release-operation-assignment.v2",
        "assignment_id": "fixture-release-result-projection-assignment",
        "revision": 1,
        "predecessor_assignment_ref": None,
        "authorized_operation": "PROJECT_RELEASE_RESULTS",
        "actor_identity": projector_actor,
        "operation_scope": {
            "scope_kind": "RELEASE_RESULT_PROJECTION",
            "acceptance_registry_ref": {
                "ref_id": "fixture-acceptance-registry",
                "authority_kind": "contract-package",
                "locator": "docs/acceptance-requirements.yaml",
                "sha256": state["registry_sha"],
            },
            "m5_binding_context_ref": copy.deepcopy(m5_ref),
            "selected_issue_head": copy.deepcopy(current_issue_head),
            "selected_result_source_root_sha256": selected_result_source_root_sha,
            "selected_evidence_binding_root_sha256": selected_evidence_binding_root_sha,
            "selected_approved_na_root_sha256": selected_approved_na_root_sha,
            "projection_input_set_sha256": sha(canonical_bytes(projection_input)),
            "projection_output_slot": (
                "contracts/fixtures/governance-models/valid/"
                "release-result-projections"
            ),
            "create_if_absent_only": True,
        },
        "effective_from": "2026-06-01T12:06:00Z",
        "expires_at": NOT_AFTER,
        "assignment_status": "ACTIVE",
        "issued_by": [
            {
                "role_id": role,
                "principal_id": f"fixture-{role}-person",
                "decision": "APPROVED",
                "approved_at": "2026-06-01T12:05:00Z",
                "assignment_ref": copy.deepcopy(owner_authority_ref),
            }
            for role in ("release-owner", "qa-evidence-owner")
        ],
        "artifact_metadata": metadata(),
    }
    projection_assignment_filename = "release-operation-assignment-v2-projection.json"
    projection_assignment_sha = corpus.add(
        "VALID-RELEASE-OPERATION-ASSIGNMENT-V2-PROJECTION-01",
        projection_assignment_filename,
        "release-operation-assignment-v2.schema.json",
        projection_assignment,
    )
    projection_assignment_ref = valid_ref(
        projection_assignment["assignment_id"],
        projection_assignment["schema"],
        projection_assignment_sha,
        projection_assignment_filename,
        1,
    )
    projection_evaluation = build_execution_authorization_evaluation(
        corpus,
        state["planning_v2"],
        task_id=state["planning_v2"]["action_node_id_by_action"][
            "assemble-release-projection"
        ],
        action_instance_id="fixture-action-assemble-release-projection",
        filename_slug="assemble-release-projection-pass",
        authorization_binding_context_ref=m5_ref,
        environment_class="release-governance",
        phase_barrier_ids=[
            "milestone-entry/M5",
            "m5-earlier-phases/matrix_closure",
            "elevated/release-finalization",
        ],
        actor_assignment_ref=projection_assignment_ref,
        actor_person_id=projection["projector_person_id"],
        additional_prerequisite_ref_by_kind={
            "projection_operation_assignment": projection_assignment_ref
        },
        evaluated_at="2026-06-01T12:55:00Z",
    )
    emitted_projection_sha = corpus.add(
        "VALID-RELEASE-RESULT-PROJECTION-V2-R2-01",
        projection_filename,
        "release-result-projection-v2.schema.json",
        projection,
    )
    if emitted_projection_sha != projection_sha:
        raise AssertionError("content-addressed projection digest drift")
    projection_locator = (
        "contracts/fixtures/governance-models/valid/"
        f"{projection_filename}"
    )
    publication_receipt = {
        "schema": "ylx.release-result-projection-publication-receipt.v1",
        "receipt_id": "fixture-release-result-projection-publication-receipt",
        "projection_ref": copy.deepcopy(projection_ref),
        "projection_sha256": projection_sha,
        "projection_locator": projection_locator,
        "execution_authorization_evaluation_ref": copy.deepcopy(
            projection_evaluation["ref"]
        ),
        "action_instance_id": projection_evaluation["value"]["action_instance_id"],
        "planned_action_input_sha256": projection_evaluation["value"][
            "planned_action_input_sha256"
        ],
        "actor_person_id": projection["projector_person_id"],
        "operation_result": "CREATED_EXACT",
        "file_fsynced": True,
        "parent_directory_fsynced": True,
        "published_at": "2026-06-01T13:01:00Z",
        "artifact_metadata": metadata(),
    }
    publication_filename = "release-result-projection-publication-receipt.json"
    publication_sha = corpus.add(
        "VALID-RELEASE-RESULT-PROJECTION-PUBLICATION-RECEIPT-01",
        publication_filename,
        "release-result-projection-publication-receipt-v1.schema.json",
        publication_receipt,
    )
    publication_ref = valid_ref(
        publication_receipt["receipt_id"],
        publication_receipt["schema"],
        publication_sha,
        publication_filename,
        None,
    )
    readback_receipt = {
        "schema": "ylx.release-result-projection-readback-receipt.v1",
        "receipt_id": "fixture-release-result-projection-readback-receipt",
        "projection_ref": copy.deepcopy(projection_ref),
        "projection_sha256": projection_sha,
        "projection_locator": projection_locator,
        "publication_receipt_ref": publication_ref,
        "observed_projection_sha256": projection_sha,
        "digest_match": True,
        "exact_bytes_match": True,
        "execution_authorization_evaluation_ref": copy.deepcopy(
            projection_evaluation["ref"]
        ),
        "action_instance_id": projection_evaluation["value"]["action_instance_id"],
        "planned_action_input_sha256": projection_evaluation["value"][
            "planned_action_input_sha256"
        ],
        "actor_person_id": projection["projector_person_id"],
        "read_back_at": "2026-06-01T13:02:00Z",
        "artifact_metadata": metadata(),
    }
    readback_filename = "release-result-projection-readback-receipt.json"
    readback_sha = corpus.add(
        "VALID-RELEASE-RESULT-PROJECTION-READBACK-RECEIPT-01",
        readback_filename,
        "release-result-projection-readback-receipt-v1.schema.json",
        readback_receipt,
    )
    return {
        **context_state,
        "result_refs": result_ref_by_requirement_id,
        "root_refs": root_refs,
        "projection": projection,
        "projection_sha": projection_sha,
        "projection_ref": projection_ref,
        "projection_filename": projection_filename,
        "projection_assignment": projection_assignment,
        "projection_assignment_sha": projection_assignment_sha,
        "projection_assignment_ref": projection_assignment_ref,
        "projection_evaluation": projection_evaluation,
        "projection_publication_receipt": publication_receipt,
        "projection_publication_receipt_sha": publication_sha,
        "projection_publication_receipt_ref": publication_ref,
        "projection_readback_receipt": readback_receipt,
        "projection_readback_receipt_sha": readback_sha,
        "issue_reconciliation_sha": issue_reconciliation_sha,
        "issue_reconciliation_records": issue_reconciliation_records,
    }


def build_issue_gate_verdict_fixtures(
    corpus: Corpus,
    state: dict[str, Any],
    *,
    issue_head_r1: dict[str, Any],
    issue_head_r1_sha: str,
    issue_r1_source_sha: str,
    issue_r1_slices: dict[str, Any],
    issue_head: dict[str, Any],
    issue_head_sha: str,
    issue_source_sha: str,
    current_issue_head: dict[str, Any],
) -> dict[str, Any]:
    """Build the exact current per-gate issue verdict chains and authorizations."""

    context_ref_by_gate = {
        "M0": None,
        "M1": None,
        "M2": state["context_v2"]["m2_ref"],
        "M3": state["context_v2"]["m3_ref"],
        "M4a": state["context_v2"]["m4_r2_ref"],
        "M4b": state["context_v2"]["m4_r2_ref"],
        "M4c": state["context_v2"]["m4_r2_ref"],
        "M4d": state["context_v2"]["m4_r2_ref"],
        "M4": state["context_v2"]["m4_r2_ref"],
        "M5": state["context_v2"]["m5_ref"],
    }
    decision_head_state = state["history"]["decision_head"]
    decision_head_value = decision_head_state["value"]
    contract_release_value = corpus.values["valid/contract-release.json"]
    mapping_ratification = state["foundation"]["mapping_ratification"]
    bootstrap_prerequisites_by_gate = {
        gate: {
            "stage_source_scope": copy.deepcopy(
                state["stage_source_scopes"]["refs"][gate]
            ),
            "decision_history_head": artifact_ref(
                decision_head_value["record_id"],
                decision_head_value["schema"],
                decision_head_state["sha256"],
                decision_head_state["fixture_path"],
                decision_head_value["history_revision"],
            ),
            "acceptance_registry": {
                "ref_id": "fixture-acceptance-registry",
                "authority_kind": "contract-package",
                "locator": "docs/acceptance-requirements.yaml",
                "sha256": state["registry_sha"],
            },
            "contract_release": artifact_ref(
                contract_release_value["contract_release_id"],
                contract_release_value["schema"],
                state["contract_release_sha"],
                "valid/contract-release.json",
                contract_release_value["release_version"],
            ),
            "system_feature_mapping_ratification": artifact_ref(
                mapping_ratification["ratification_id"],
                mapping_ratification["schema"],
                state["foundation"]["mapping_ratification_sha"],
                "valid/system-feature-mapping-ratification.json",
                mapping_ratification["revision"],
            ),
        }
        for gate in ("M0", "M1")
    }

    m4_qualification_prerequisites = {
        "m3_binding_context": copy.deepcopy(state["context_v2"]["m3_ref"]),
        "m4_binding_context": copy.deepcopy(state["context_v2"]["m4_r2_ref"]),
        "assembly": artifact_ref(
            state["assembly"]["candidate_id"],
            state["assembly"]["schema"],
            state["assembly_sha"],
            "contracts/fixtures/governance-models/valid/m4-candidate-assembly.json",
            1,
        ),
        "m4_target_deployment": copy.deepcopy(
            state["context_v2"]["m4_target_deployment_ref"]
        ),
        **{
            f"deployment-receipt-{key}": copy.deepcopy(ref)
            for key, ref in state["context_v2"]["m4_deployment_receipts"].items()
        },
    }
    qualification_prerequisites_by_gate = {
        "M2": {
            "m2_qualification_context": copy.deepcopy(
                state["context_v2"]["m2_ref"]
            ),
            "contract_release": artifact_ref(
                contract_release_value["contract_release_id"],
                contract_release_value["schema"],
                state["contract_release_sha"],
                "contracts/fixtures/governance-models/valid/contract-release.json",
                contract_release_value["release_version"],
            ),
            "fixture_corpus": copy.deepcopy(state["fixture_corpus"]["ref"]),
            "consumer_deployment_set": copy.deepcopy(
                state["consumer_deployment_set_ref"]
            ),
        },
        "M3": {
            "m2_qualification_context": copy.deepcopy(
                state["context_v2"]["m2_ref"]
            )
        },
        "M4a": m4_qualification_prerequisites,
        "M4b": m4_qualification_prerequisites,
        "M4c": m4_qualification_prerequisites,
        "M4d": m4_qualification_prerequisites,
        "M4": m4_qualification_prerequisites,
    }

    current_tuple = {
        "current_issue_register_head_artifact_path": current_issue_head[
            "artifact_path"
        ],
        "current_issue_register_revision": current_issue_head["revision"],
        "current_issue_register_head_artifact_sha256": current_issue_head[
            "head_artifact_sha256"
        ],
        "current_issue_register_sha256": current_issue_head["register_sha256"],
        "current_issue_register_selector_version": current_issue_head[
            "selector_version"
        ],
        "current_issue_register_overview_cardinality": current_issue_head[
            "overview_cardinality"
        ],
    }
    historical_m5_tuple = {
        "current_issue_register_head_artifact_path": "valid/issue-register-head-r1.json",
        "current_issue_register_revision": issue_head_r1[
            "issue_register_revision"
        ],
        "current_issue_register_head_artifact_sha256": issue_head_r1_sha,
        "current_issue_register_sha256": issue_r1_source_sha,
        "current_issue_register_selector_version": issue_head_r1[
            "selector_version"
        ],
        "current_issue_register_overview_cardinality": issue_head_r1[
            "overview_cardinality"
        ],
    }
    current_issue_head_prerequisite = {
        "ref_id": "fixture-current-issue-register-head-r2",
        "authority_kind": "issue-register",
        "locator": "valid/issue-register-head.json",
        "sha256": issue_head_sha,
    }
    historical_issue_head_prerequisite = {
        "ref_id": "fixture-historical-issue-register-head-r1",
        "authority_kind": "issue-register",
        "locator": "valid/issue-register-head-r1.json",
        "sha256": issue_head_r1_sha,
    }

    def authorization_for_gate(
        gate: str,
        *,
        suffix: str = "pass",
        evaluated_at: str = "2026-06-01T12:45:00Z",
        issue_head_prerequisite: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        milestone_gate = "M4" if gate.startswith("M4") else gate
        barriers = (
            ["m4-start/qualification"]
            if gate.startswith("M4")
            else [f"milestone-entry/{milestone_gate}"]
        )
        if gate == "M5":
            barriers.append("m5-earlier-phases/matrix_closure")
        gate_slug = gate.lower()
        additional_prerequisites = copy.deepcopy(
            bootstrap_prerequisites_by_gate.get(
                gate, qualification_prerequisites_by_gate.get(gate, {})
            )
        )
        additional_prerequisites["issue_register_head"] = copy.deepcopy(
            issue_head_prerequisite or current_issue_head_prerequisite
        )
        return build_execution_authorization_evaluation(
            corpus,
            state["planning_v2"],
            task_id=state["planning_v2"]["issue_verdict_node_id_by_gate"][gate],
            action_instance_id=(
                f"fixture-action-issue-verdict-{gate_slug}-{suffix}"
            ),
            filename_slug=f"issue-verdict-{gate_slug}-{suffix}",
            authorization_binding_context_ref=context_ref_by_gate[gate],
            environment_class=(
                "governance-publication"
                if gate in {"M0", "M1"}
                else "qualification-target"
            ),
            phase_barrier_ids=barriers,
            actor_assignment_ref=state["planning_v2"]["owner_ref"],
            actor_person_id="fixture-release-owner-person",
            additional_prerequisite_ref_by_kind=additional_prerequisites,
            evaluated_at=evaluated_at,
        )

    m5_r1_evaluation = authorization_for_gate(
        "M5",
        suffix="blocked-r1",
        evaluated_at="2026-06-01T12:42:00Z",
        issue_head_prerequisite=historical_issue_head_prerequisite,
    )
    m5_verdict_id = "fixture-issue-register-gate-verdict-m5"
    m5_r1 = {
        "schema": "ylx.issue-register-gate-verdict.v1",
        "verdict_id": m5_verdict_id,
        "revision": 1,
        "predecessor_verdict_ref": None,
        "requirement_id": ISSUE_REQUIREMENT_BY_GATE["M5"],
        "gate": "M5",
        **historical_m5_tuple,
        "selected_issue_ids": ["O-1"],
        "selected_issue_slices_by_id": {
            "O-1": copy.deepcopy(issue_r1_slices["O-1"])
        },
        "result": "BLOCKED",
        "evaluated_at": "2026-06-01T12:43:00Z",
        "evaluator_person_id": "fixture-release-owner-person",
        "evaluator_assignment_ref": copy.deepcopy(
            state["planning_v2"]["owner_ref"]
        ),
        "artifact_metadata": metadata(),
    }
    m5_r1_filename = "issue-register-gate-verdict-m5-r1.json"
    m5_r1_sha = corpus.add(
        "VALID-ISSUE-REGISTER-GATE-VERDICT-M5-R1-BLOCKED-01",
        m5_r1_filename,
        "issue-register-gate-verdict-v1.schema.json",
        m5_r1,
    )
    m5_r1_ref = artifact_ref(
        m5_verdict_id,
        m5_r1["schema"],
        m5_r1_sha,
        f"contracts/fixtures/governance-models/valid/{m5_r1_filename}",
        1,
    )

    value_by_gate: dict[str, dict[str, Any]] = {}
    ref_by_gate: dict[str, dict[str, Any]] = {}
    sha_by_gate: dict[str, str] = {}
    evaluation_by_gate: dict[str, dict[str, Any]] = {}
    for gate, requirement_id in ISSUE_REQUIREMENT_BY_GATE.items():
        gate_slug = gate.lower()
        evaluation = authorization_for_gate(gate)
        revision = 2 if gate == "M5" else 1
        value = {
            "schema": "ylx.issue-register-gate-verdict.v1",
            "verdict_id": f"fixture-issue-register-gate-verdict-{gate_slug}",
            "revision": revision,
            "predecessor_verdict_ref": (
                copy.deepcopy(m5_r1_ref) if gate == "M5" else None
            ),
            "requirement_id": requirement_id,
            "gate": gate,
            **current_tuple,
            "selected_issue_ids": [],
            "selected_issue_slices_by_id": {},
            "result": "PASS",
            "evaluated_at": "2026-06-01T12:46:00Z",
            "evaluator_person_id": "fixture-release-owner-person",
            "evaluator_assignment_ref": copy.deepcopy(
                state["planning_v2"]["owner_ref"]
            ),
            "artifact_metadata": metadata(),
        }
        filename = f"issue-register-gate-verdict-{gate_slug}.json"
        digest = corpus.add(
            f"VALID-ISSUE-REGISTER-GATE-VERDICT-{gate.upper()}-PASS-01",
            filename,
            "issue-register-gate-verdict-v1.schema.json",
            value,
        )
        ref = artifact_ref(
            value["verdict_id"],
            value["schema"],
            digest,
            f"contracts/fixtures/governance-models/valid/{filename}",
            revision,
        )
        value_by_gate[gate] = value
        ref_by_gate[gate] = ref
        sha_by_gate[gate] = digest
        evaluation_by_gate[gate] = evaluation

    state_value = {
        "value_by_gate": value_by_gate,
        "ref_by_gate": ref_by_gate,
        "sha_by_gate": sha_by_gate,
        "evaluation_by_gate": evaluation_by_gate,
        "historical_m5_value": m5_r1,
        "historical_m5_sha": m5_r1_sha,
        "historical_m5_ref": m5_r1_ref,
        "historical_m5_evaluation": m5_r1_evaluation,
        "current_head": copy.deepcopy(issue_head),
        "current_head_sha": issue_head_sha,
        "current_register_sha": issue_source_sha,
    }
    corpus.relationships["issue_register_chain"] = {
        "current_verdict_path_by_gate": {
            gate: ref["artifact_path"] for gate, ref in ref_by_gate.items()
        },
        "current_verdict_sha256_by_gate": copy.deepcopy(sha_by_gate),
        "historical_m5_blocked_verdict_path": m5_r1_ref["artifact_path"],
        "historical_m5_blocked_verdict_sha256": m5_r1_sha,
    }
    return state_value


def build_release_v2_chain(
    corpus: Corpus,
    state: dict[str, Any],
    *,
    current_issue_head: dict[str, Any],
    issue_reconciliation_sha: str,
    consumer_boundary_registry_sha: str,
    consumer_acceptance_set_sha: str,
    component_acceptance_map: dict[str, str],
    assignment_values: dict[str, Any],
    assignment_digests: dict[str, str],
    key_head: dict[str, Any],
    key_head_sha: str,
    private_keys: dict[str, Ed25519PrivateKey],
    fingerprint_by_role: dict[str, str],
) -> dict[str, Any]:
    """Build the typed v2 release-attempt, freshness, and distribution chain."""

    projection_v2 = state["projection_v2"]

    def vref(
        artifact_id: str,
        schema: str,
        digest: str,
        filename: str,
        revision: int | None = 1,
    ) -> dict[str, Any]:
        return artifact_ref(
            artifact_id,
            schema,
            digest,
            f"contracts/fixtures/governance-models/valid/{filename}",
            revision,
        )

    planning_owner = state["planning_v2"]["owner"]
    planning_owner_authority_ref = {
        "authority_id": planning_owner["artifact_id"],
        "revision": planning_owner["revision"],
        "artifact_path": (
            "contracts/fixtures/governance-models/valid/"
            "owner-assignment-v2-planning.json"
        ),
        "artifact_sha256": state["planning_v2"]["owner_sha"],
        "verified_at": "2026-06-01T13:00:00Z",
    }

    def owner_approval(role: str, approved_at: str) -> dict[str, Any]:
        return {
            "role_id": role,
            "principal_id": f"fixture-{role}-person",
            "decision": "APPROVED",
            "approved_at": approved_at,
            "assignment_ref": copy.deepcopy(planning_owner_authority_ref),
        }

    planned_operator_person = "fixture-build-platform-owner-person"
    planned_operator = {
        "actor_id": "fixture-ga-promotion-operator-v2",
        "person_id": planned_operator_person,
        "natural_person_identity_ref": copy.deepcopy(
            state["foundation"]["identity_refs_by_person"][planned_operator_person]
        ),
        "role_slot": "build-platform-owner",
        "role_assignment_ref": copy.deepcopy(planning_owner_authority_ref),
    }

    policy_approvals = [
        owner_approval("contract-owner", "2026-06-01T12:58:00Z"),
        owner_approval("security-owner", "2026-06-01T12:58:00Z"),
    ]
    signing_policy = {
        "schema": "ylx.m5-signing-policy.v2",
        "policy_id": "fixture-m5-signing-policy-v2",
        "revision": 1,
        "predecessor_policy_ref": None,
        "canonicalization": "RFC8785-JSON-UTF8",
        "digest_algorithm": "SHA-256",
        "signature_algorithm": "Ed25519",
        "public_key_encoding": "32-byte-raw-Ed25519",
        "signature_encoding": "base64",
        "signature_domain_template": "ylx.release-closure.quorum.v2/<role_slot>",
        "signed_payload_schema": "ylx.release-quorum-signature.signed-payload.v2",
        "signed_artifact_schema": "ylx.pre-release-closure.v2",
        "distribution_signature_domain_template": (
            "ylx.release-distribution-control.v2/<role_slot>"
        ),
        "distribution_signed_artifact_schema": "ylx.release-distribution-control.v2",
        "distribution_role_slots": ["release-owner", "security-owner"],
        "distribution_signed_payload_rule": (
            "OMIT_SIGNATURES_BY_ROLE_SLOT_THEN_RFC8785_JSON_UTF8"
        ),
        "signature_message_rule": (
            "ASCII_DOMAIN || 0x00 || RFC8785_SIGNED_PAYLOAD_JSON_BYTES"
        ),
        "minimum_key_validity_horizon_seconds": 86400,
        "valid_at_signature_rule": "SIGNED_AT_WITHIN_NOT_BEFORE_AND_NOT_AFTER",
        "normal_post_signature_expiry_rule": "DOES_NOT_INVALIDATE_VALID_SIGNATURE",
        "retroactive_revocation_rule": (
            "INVALID_ONLY_WHEN_EFFECTIVE_AT_OR_BEFORE_SIGNED_AT"
        ),
        "published_at": "2026-06-01T12:59:00Z",
        "approvals": copy.deepcopy(policy_approvals),
        "artifact_metadata": metadata(),
    }
    signing_policy_filename = "m5-signing-policy-v2.json"
    signing_policy_sha = corpus.add(
        "VALID-M5-SIGNING-POLICY-V2-01",
        signing_policy_filename,
        "m5-signing-policy-v2.schema.json",
        signing_policy,
    )
    signing_policy_ref = vref(
        signing_policy["policy_id"],
        signing_policy["schema"],
        signing_policy_sha,
        signing_policy_filename,
    )

    quorum_policy = {
        "schema": "ylx.release-quorum-policy.v2",
        "policy_id": "fixture-release-quorum-policy-v2",
        "revision": 1,
        "predecessor_policy_ref": None,
        "mandatory_role_slots": QUORUM_ROLES,
        "distinct_natural_person_count": 4,
        "signature_domain_template": "ylx.release-closure.quorum.v2/<role_slot>",
        "signed_payload_schema": "ylx.release-quorum-signature.signed-payload.v2",
        "signed_artifact_schema": "ylx.pre-release-closure.v2",
        "signing_policy_ref": copy.deepcopy(signing_policy_ref),
        "qa_independence": {
            "not_result_map_producer": True,
            "not_promotion_operator": True,
        },
        "delegation_rule": "PREAPPROVED_DIRECT_PREDECESSOR_ASSIGNMENT_ONLY",
        "freshness_checkpoint_schema": "ylx.release-freshness-checkpoint.v1",
        "freshness_checkpoints": FRESHNESS_CHECKPOINTS,
        "freshness_checkpoint_rule": (
            "each checkpoint is an action-precondition authority snapshot over the exact "
            "v2 attempt family and every already-created artifact; checkpoint 5 authorizes "
            "manifest publication/readback, checkpoint 7 is the immediate pre-CAS snapshot, "
            "and checkpoint 8 validates exact terminal readback before distribution"
        ),
        "terminal_drift_rule": (
            "any mismatch before checkpoint 7 prevents FINALIZED and uses the ABORTED "
            "quarantine path; mismatch after FINALIZED CAS but before valid checkpoint 8 "
            "cannot derive M5-SIGNOFF PASS or RELEASE_COMPLETE and cannot overwrite or reuse "
            "the slot"
        ),
        "published_at": "2026-06-01T12:59:30Z",
        "approvals": copy.deepcopy(policy_approvals),
        "artifact_metadata": metadata(),
    }
    quorum_policy_filename = "release-quorum-policy-v2.json"
    quorum_policy_sha = corpus.add(
        "VALID-RELEASE-QUORUM-POLICY-V2-01",
        quorum_policy_filename,
        "release-quorum-policy-v2.schema.json",
        quorum_policy,
    )
    quorum_policy_ref = vref(
        quorum_policy["policy_id"],
        quorum_policy["schema"],
        quorum_policy_sha,
        quorum_policy_filename,
    )

    rc_target = {
        "rc_version": "0.5.0-rc.1",
        "rc_commit": "1" * 40,
        "rc_artifact_sha256": sha("fixture-rc-artifact-v2"),
        "canonical_remote_id": "fixture-origin-v2",
        "ga_ref": "refs/tags/v0.5.0",
        "ga_channel": "channels/ga/0.5",
        "canonical_ga_target": "fixture-origin-v2/refs/tags/v0.5.0",
    }
    promotion_plan = {
        "schema": "ylx.ga-promotion-plan.v2",
        "plan_id": "fixture-ga-promotion-plan-v2",
        "revision": 1,
        "predecessor_plan_ref": None,
        "binding_context_ref": copy.deepcopy(projection_v2["m5_ref"]),
        "effective_m4_lineage_ref": copy.deepcopy(projection_v2["lineage_ref"]),
        **copy.deepcopy(rc_target),
        "planned_promotion_operator": copy.deepcopy(planned_operator),
        "create_if_absent": True,
        "existing_exact_target_is_idempotent": True,
        "rebuild_allowed": False,
        "overwrite_allowed": False,
        "force_push_allowed": False,
        "created_at": "2026-06-01T13:03:00Z",
        "approvals": [
            owner_approval("release-owner", "2026-06-01T13:02:30Z"),
            owner_approval("contract-owner", "2026-06-01T13:02:30Z"),
        ],
        "artifact_metadata": metadata(),
    }
    promotion_plan_filename = "ga-promotion-plan-v2.json"
    promotion_plan_sha = corpus.add(
        "VALID-GA-PROMOTION-PLAN-V2-01",
        promotion_plan_filename,
        "ga-promotion-plan-v2.schema.json",
        promotion_plan,
    )
    promotion_plan_ref = vref(
        promotion_plan["plan_id"],
        promotion_plan["schema"],
        promotion_plan_sha,
        promotion_plan_filename,
    )

    projection_readback = projection_v2["projection_readback_receipt"]
    projection_readback_ref = vref(
        projection_readback["receipt_id"],
        projection_readback["schema"],
        projection_v2["projection_readback_receipt_sha"],
        "release-result-projection-readback-receipt.json",
        None,
    )
    attestation_refs: dict[str, dict[str, Any]] = {}
    attestation_digests: dict[str, str] = {}
    for role in ROLES:
        person_id = f"fixture-{role}-person"
        identity_ref = state["foundation"]["identity_refs_by_person"][person_id]
        filename = f"domain-attestation-v2-{role}.json"
        attestation: dict[str, Any] = {
            "schema": "ylx.domain-attestation.v2",
            "attestation_id": f"fixture-domain-attestation-v2-{role}",
            "revision": 1,
            "predecessor_attestation_ref": None,
            "created_at": "2026-06-01T13:04:00Z",
            "artifact_path": (
                "contracts/fixtures/governance-models/valid/" + filename
            ),
            "artifact_metadata": metadata(),
            "role_id": role,
            "attesting_identity": {
                "person_id": person_id,
                "natural_person_identity_sha256": identity_ref["artifact_sha256"],
                "identity_authority_ref": copy.deepcopy(
                    planning_owner_authority_ref
                ),
            },
            "role_assignment_ref": copy.deepcopy(planning_owner_authority_ref),
            "subject_refs": [
                copy.deepcopy(projection_v2["m5_ref"]),
                copy.deepcopy(projection_v2["projection_ref"]),
            ],
            "evidence_refs": [copy.deepcopy(projection_readback_ref)],
            "decision_refs": [source(f"fixture-v2-{role}-release-decision")],
            "current_issue_head": copy.deepcopy(current_issue_head),
            "binding_context_ref": copy.deepcopy(projection_v2["m5_ref"]),
            "effective_m4_lineage_ref": copy.deepcopy(projection_v2["lineage_ref"]),
            "release_result_projection_ref": copy.deepcopy(
                projection_v2["projection_ref"]
            ),
            "release_result_projection_sha256": projection_v2["projection_sha"],
            "projection_operation_assignment_ref": copy.deepcopy(
                projection_v2["projection_assignment_ref"]
            ),
            "shared_context_refs": [copy.deepcopy(projection_v2["m4_r2_ref"])],
            "conflict_control_ref": None,
            "attestation_outcome": "PASS",
            "attested_at": "2026-06-01T13:05:00Z",
        }
        if role == "consumer-owner":
            attestation["consumer_bindings"] = {
                "consumer_boundary_registry_sha256": consumer_boundary_registry_sha,
                "consumer_boundary_acceptance_set_sha256": consumer_acceptance_set_sha,
                "component_acceptance_record_sha256_by_boundary": copy.deepcopy(
                    component_acceptance_map
                ),
            }
        digest = corpus.add(
            f"VALID-DOMAIN-ATTESTATION-V2-{role.upper()}-01",
            filename,
            "domain-attestation-v2.schema.json",
            attestation,
        )
        attestation_digests[role] = digest
        attestation_refs[role] = vref(
            attestation["attestation_id"],
            attestation["schema"],
            digest,
            filename,
        )

    execution_context_ref = vref(
        "fixture-execution-context-source",
        "ylx.execution-context.v1",
        state["execution_source_sha"],
        "execution-context.json",
        None,
    )
    transition_keys = (
        "active_to_withdrawn",
        "active_to_redirected",
        "withdrawn_to_active",
        "withdrawn_to_redirected",
        "redirected_to_active",
        "redirected_to_withdrawn",
        "redirected_to_redirected_changed_target",
    )
    negative_keys = (
        "stale_predecessor",
        "forked_head",
        "missing_signature",
        "bad_signature",
        "revoked_signature",
        "duplicate_signer_person",
        "invalid_transition",
        "missing_recovery_evidence",
        "invalid_finalized_target",
        "rto_breach",
        "cache_propagation_breach",
        "client_behavior_mismatch",
        "producer_behavior_mismatch",
        "consumer_behavior_mismatch",
        "compatibility_loss",
    )
    distribution_drill = {
        "schema": "ylx.release-distribution-drill.v1",
        "drill_id": "fixture-release-distribution-drill-v1",
        "revision": 1,
        "predecessor_drill_sha256": None,
        "m5_binding_context_ref": copy.deepcopy(projection_v2["m5_ref"]),
        "production_deployment_record_ref": artifact_ref(
            "fixture-production-deployment-record",
            "ylx.production-deployment-record.v1",
        ),
        "release_controller_artifact_ref": artifact_ref(
            "fixture-release-controller-build",
            "ylx.release-controller-build.v1",
        ),
        "customer_visible_resolver_artifact_ref": artifact_ref(
            "fixture-customer-visible-resolver-build",
            "ylx.customer-visible-resolver-build.v1",
        ),
        "execution_context_refs": [execution_context_ref],
        "isolated_channel": {
            "channel_id": "fixture-isolated-distribution-drill",
            "canonical_locator": "channels/fixture-isolated-drill",
            "customer_discoverable": False,
            "production_writer_enabled": False,
        },
        "production_authority_ref": copy.deepcopy(planning_owner_authority_ref),
        "transition_result_by_case": {
            key: {
                "expected_transition": key,
                "observed_transition": key,
                "result": "PASS",
                "evidence_ref": artifact_ref(f"fixture-drill-evidence-{key}"),
            }
            for key in transition_keys
        },
        "negative_result_by_case": {
            key: {
                "expected_result": "REJECT",
                "observed_result": "REJECTED",
                "result": "PASS",
                "evidence_ref": artifact_ref(
                    f"fixture-drill-negative-evidence-{key}"
                ),
            }
            for key in negative_keys
        },
        "required_rto_seconds": 900,
        "observed_max_rto_seconds": 120,
        "customer_visibility_side_effect": False,
        "overall_result": "PASS",
        "owner_role": "build-platform-owner",
        "reviewer_role": "qa-evidence-owner",
        "started_at": "2026-06-01T13:06:00Z",
        "completed_at": "2026-06-01T13:07:00Z",
        "artifact_metadata": metadata(),
    }
    distribution_drill_filename = "release-distribution-drill.json"
    distribution_drill_sha = corpus.add(
        "VALID-RELEASE-DISTRIBUTION-DRILL-01",
        distribution_drill_filename,
        "release-distribution-drill-v1.schema.json",
        distribution_drill,
    )
    distribution_drill_ref = vref(
        distribution_drill["drill_id"],
        distribution_drill["schema"],
        distribution_drill_sha,
        distribution_drill_filename,
    )

    readiness_check_names = (
        "effective_m4_lineage_exact",
        "release_bundle_exact",
        "same_qualified_controller_build",
        "same_qualified_resolver_build",
        "production_service_identity_bound",
        "rendered_config_and_deployment_bound",
        "production_storage_and_cas_bound",
        "production_credentials_bound",
        "visibility_authority_bound",
        "customer_profile_auth_rerun_passed",
        "production_storage_verdicts_passed",
        "distribution_drill_passed",
        "writer_disabled",
        "customer_visibility_quarantined",
        "rollback_and_recovery_passed",
        "active_distribution_not_used_as_evidence",
    )
    production_deployment_ref = artifact_ref(
        "fixture-production-deployment-record",
        "ylx.production-deployment-record.v1",
    )
    readiness = {
        "schema": "ylx.production-deployment-readiness.v1",
        "readiness_id": "fixture-production-deployment-readiness",
        "revision": 1,
        "predecessor_readiness_ref": None,
        "requirement_id": "BUILD-DEPLOY-01",
        "m5_binding_context_ref": copy.deepcopy(projection_v2["m5_ref"]),
        "effective_m4_lineage_ref": copy.deepcopy(projection_v2["lineage_ref"]),
        "release_bundle_sha256": state["planning_v2"]["bundle_sha"],
        "controller_deployment_ref": copy.deepcopy(production_deployment_ref),
        "resolver_deployment_ref": copy.deepcopy(production_deployment_ref),
        "service_authority_ref": copy.deepcopy(planning_owner_authority_ref),
        "storage_authority_ref": copy.deepcopy(planning_owner_authority_ref),
        "credential_authority_ref": copy.deepcopy(planning_owner_authority_ref),
        "visibility_authority_ref": copy.deepcopy(planning_owner_authority_ref),
        "customer_profile_verdict_ref": copy.deepcopy(
            projection_v2["result_refs"]["SEC-CUSTOMER-01"]
        ),
        "production_storage_verdict_refs": [
            copy.deepcopy(projection_v2["result_refs"][requirement_id])
            for requirement_id in (
                "STORE-IAM-01",
                "STORE-CORS-01",
                "STORE-LIFECYCLE-01",
                "STORE-FIRST-PUBLISH-01",
            )
        ],
        "distribution_drill_ref": copy.deepcopy(distribution_drill_ref),
        "customer_visibility": "QUARANTINED",
        "actual_distribution_activation": "PENDING_SIGNOFF",
        "readiness_checks": {name: True for name in readiness_check_names},
        "result": "PASS",
        "readiness_result": "READY_FOR_RELEASE_ATTEMPT",
        "assessed_by_role": "build-platform-owner",
        "reviewed_by_role": "qa-evidence-owner",
        "closed_at": "2026-06-01T13:08:00Z",
        "artifact_metadata": metadata(),
    }
    readiness_filename = "production-deployment-readiness.json"
    readiness_sha = corpus.add(
        "VALID-PRODUCTION-DEPLOYMENT-READINESS-01",
        readiness_filename,
        "production-deployment-readiness-v1.schema.json",
        readiness,
    )
    readiness_ref = vref(
        readiness["readiness_id"],
        readiness["schema"],
        readiness_sha,
        readiness_filename,
    )

    projection = projection_v2["projection"]
    current_result_map = {
        requirement_id: row["effective_result"]
        for requirement_id, row in projection[
            "row_projection_by_requirement_id"
        ].items()
    }
    current_result_map["M5-MATRIX-COMPLETE-01"] = (
        "PASS_DERIVED_FROM_PRE_RELEASE_VALIDITY"
    )
    current_result_map["M5-SIGNOFF-01"] = "PENDING_CLOSURE"
    proposed_final_result_map = copy.deepcopy(current_result_map)
    proposed_final_result_map["M5-SIGNOFF-01"] = (
        "PASS_DERIVED_FROM_FINAL_MANIFEST_VALIDITY"
    )
    key_head_ref = vref(
        key_head["head_id"],
        key_head["schema"],
        key_head_sha,
        "signing-key-validity-revocation-head.json",
    )
    pre_release = {
        "schema": "ylx.pre-release-closure.v2",
        "closure_id": "fixture-pre-release-closure-v2",
        "revision": 1,
        "predecessor_closure_ref": None,
        "binding_context_ref": copy.deepcopy(projection_v2["m5_ref"]),
        "effective_m4_lineage_ref": copy.deepcopy(projection_v2["lineage_ref"]),
        "effective_m4_binding_context_ref": copy.deepcopy(
            projection_v2["m4_r2_ref"]
        ),
        "contract_release_sha256": state["contract_release_sha"],
        "product_contract_sha256": state["product_contract_sha"],
        "qualification_governance_contract_sha256": state[
            "qualification_contract_sha"
        ],
        "registry_sha256": projection["registry_sha256"],
        "registry_id_set_sha256": projection["registry_id_set_sha256"],
        "registry_cardinality": 173,
        "acceptance_sha256": projection["acceptance_sha256"],
        "system_requirement_mapping_artifact_sha256": projection[
            "system_requirement_mapping_artifact_sha256"
        ],
        "system_requirement_mapping_semantic_sha256": projection[
            "system_requirement_mapping_semantic_sha256"
        ],
        "release_result_projection_ref": copy.deepcopy(
            projection_v2["projection_ref"]
        ),
        "release_result_projection_sha256": projection_v2["projection_sha"],
        "current_result_map": current_result_map,
        "proposed_final_result_map": proposed_final_result_map,
        "issue_head": copy.deepcopy(current_issue_head),
        "issue_reconciliation_set_sha256": issue_reconciliation_sha,
        "domain_attestation_ref_by_role_slot": copy.deepcopy(attestation_refs),
        "domain_attestation_sha256_by_role_slot": copy.deepcopy(
            attestation_digests
        ),
        "consumer_boundary_registry_sha256": consumer_boundary_registry_sha,
        "consumer_boundary_acceptance_set_sha256": consumer_acceptance_set_sha,
        "component_acceptance_record_sha256_by_boundary": copy.deepcopy(
            component_acceptance_map
        ),
        "signing_policy_ref": copy.deepcopy(signing_policy_ref),
        "signing_policy_sha256": signing_policy_sha,
        "key_validity_revocation_head_ref": copy.deepcopy(key_head_ref),
        "key_validity_revocation_head_sha256": key_head_sha,
        "quorum_policy_ref": copy.deepcopy(quorum_policy_ref),
        "quorum_policy_sha256": quorum_policy_sha,
        "ga_promotion_plan_ref": copy.deepcopy(promotion_plan_ref),
        "ga_promotion_plan_sha256": promotion_plan_sha,
        "planned_promotion_operator": copy.deepcopy(planned_operator),
        "created_at": "2026-06-01T13:09:00Z",
        "artifact_metadata": metadata(),
    }
    pre_release_filename = "pre-release-closure-v2.json"
    pre_release_sha = corpus.add(
        "VALID-PRE-RELEASE-CLOSURE-V2-01",
        pre_release_filename,
        "pre-release-closure-v2.schema.json",
        pre_release,
    )
    pre_release_ref = vref(
        pre_release["closure_id"],
        pre_release["schema"],
        pre_release_sha,
        pre_release_filename,
    )

    role_assignment_refs = {
        role: vref(
            assignment_values[role]["assignment_id"],
            assignment_values[role]["schema"],
            assignment_digests[role],
            f"role-signing-key-assignment-{role}.json",
        )
        for role in QUORUM_ROLES
    }
    signature_refs: dict[str, dict[str, Any]] = {}
    signature_digests: dict[str, str] = {}
    for role in QUORUM_ROLES:
        assignment = assignment_values[role]
        signed_at = "2026-06-01T13:10:00Z"
        signature_domain = f"ylx.release-closure.quorum.v2/{role}"
        signed_payload = {
            "payload_schema": "ylx.release-quorum-signature.signed-payload.v2",
            "signature_domain": signature_domain,
            "pre_release_closure_ref": copy.deepcopy(pre_release_ref),
            "pre_release_closure_sha256": pre_release_sha,
            "role_slot": role,
            "person_id": assignment["person_id"],
            "signer_identity": {
                "natural_person_identity_sha256": assignment[
                    "natural_person_identity_sha256"
                ],
                "identity_authority_ref": copy.deepcopy(
                    assignment["identity_authority_ref"]
                ),
            },
            "signing_key_fingerprint": fingerprint_by_role[role],
            "key_validity_at_signature": {
                "not_before": assignment["effective_from"],
                "not_after": assignment["not_after"],
                "evaluated_revocation_head_sha256": key_head_sha,
                "status": "VALID_AT_SIGNED_AT",
                "required_remaining_validity_seconds": 86400,
                "validity_horizon_satisfied": True,
                "post_signature_expiry_rule": (
                    "NORMAL_EXPIRY_DOES_NOT_INVALIDATE_A_SIGNATURE_VALID_AT_SIGNED_AT"
                ),
                "retroactive_compromise_rule": (
                    "ONLY_REVOCATION_OR_COMPROMISE_EFFECTIVE_AT_OR_BEFORE_SIGNED_AT_INVALIDATES"
                ),
            },
            "role_assignment_ref": copy.deepcopy(role_assignment_refs[role]),
            "role_assignment_revision": assignment["revision"],
            "signing_policy_ref": copy.deepcopy(signing_policy_ref),
            "signing_policy_sha256": signing_policy_sha,
            "key_validity_revocation_head_ref": copy.deepcopy(key_head_ref),
            "key_validity_revocation_head_sha256": key_head_sha,
            "quorum_policy_ref": copy.deepcopy(quorum_policy_ref),
            "quorum_policy_sha256": quorum_policy_sha,
            "binding_context_ref": copy.deepcopy(projection_v2["m5_ref"]),
            "effective_m4_lineage_ref": copy.deepcopy(
                projection_v2["lineage_ref"]
            ),
            "fresh_issue_head": copy.deepcopy(current_issue_head),
            "issue_reconciliation_set_sha256": issue_reconciliation_sha,
            "domain_attestation_ref_by_role_slot": copy.deepcopy(
                attestation_refs
            ),
            "domain_attestation_sha256_by_role_slot": copy.deepcopy(
                attestation_digests
            ),
            "signed_at": signed_at,
        }
        message = signature_domain.encode("ascii") + b"\x00" + canonical_bytes(
            signed_payload
        )
        signature = {
            "schema": "ylx.release-quorum-signature.v2",
            "signature_id": f"fixture-release-quorum-signature-v2-{role}",
            "revision": 1,
            "predecessor_signature_ref": None,
            "signed_payload": signed_payload,
            "signature_b64": base64.b64encode(
                private_keys[role].sign(message)
            ).decode("ascii"),
            "artifact_metadata": metadata(),
        }
        filename = f"release-quorum-signature-v2-{role}.json"
        digest = corpus.add(
            f"VALID-RELEASE-QUORUM-SIGNATURE-V2-{role.upper()}-01",
            filename,
            "release-quorum-signature-v2.schema.json",
            signature,
        )
        signature_digests[role] = digest
        signature_refs[role] = vref(
            signature["signature_id"],
            signature["schema"],
            digest,
            filename,
        )

    attempt_id = "fixture-release-attempt-v2-001"
    attempt_terminal_slot = "release-terminal-slots/fixture-release-attempt-v2-001"
    promotion_assignment = {
        "schema": "ylx.release-operation-assignment.v2",
        "assignment_id": "fixture-release-operation-assignment-v2-promotion",
        "revision": 1,
        "predecessor_assignment_ref": None,
        "authorized_operation": "PROMOTE_EXACT_RC_TO_GA",
        "actor_identity": copy.deepcopy(planned_operator),
        "operation_scope": {
            "scope_kind": "EXACT_RC_GA_PROMOTION",
            "release_attempt_id": attempt_id,
            "pre_release_closure_ref": copy.deepcopy(pre_release_ref),
            "promotion_plan_ref": copy.deepcopy(promotion_plan_ref),
            **copy.deepcopy(rc_target),
            "attempt_terminal_slot": attempt_terminal_slot,
            "create_if_absent_only": True,
            "overwrite_allowed": False,
            "rebuild_allowed": False,
            "force_push_allowed": False,
        },
        "effective_from": "2026-06-01T13:11:00Z",
        "expires_at": NOT_AFTER,
        "assignment_status": "ACTIVE",
        "issued_by": [
            owner_approval("release-owner", "2026-06-01T13:10:30Z"),
            owner_approval("contract-owner", "2026-06-01T13:10:30Z"),
        ],
        "artifact_metadata": metadata(),
    }
    promotion_assignment_filename = "release-operation-assignment-v2-promotion.json"
    promotion_assignment_sha = corpus.add(
        "VALID-RELEASE-OPERATION-ASSIGNMENT-V2-PROMOTION-01",
        promotion_assignment_filename,
        "release-operation-assignment-v2.schema.json",
        promotion_assignment,
    )
    promotion_assignment_ref = vref(
        promotion_assignment["assignment_id"],
        promotion_assignment["schema"],
        promotion_assignment_sha,
        promotion_assignment_filename,
    )

    attempt_input = {
        "schema": "ylx.release-attempt-input-set.v1",
        "input_set_id": "fixture-release-attempt-input-set-v1",
        "revision": 1,
        "predecessor_input_set_ref": None,
        "attempt_id": attempt_id,
        "system_milestone": "0.5",
        "pre_release_ref": copy.deepcopy(pre_release_ref),
        "readiness_ref": copy.deepcopy(readiness_ref),
        "distribution_drill_ref": copy.deepcopy(distribution_drill_ref),
        "domain_attestation_ref_by_role": copy.deepcopy(attestation_refs),
        "quorum_signature_ref_by_role": copy.deepcopy(signature_refs),
        "role_assignment_ref_by_role": copy.deepcopy(role_assignment_refs),
        "m5_binding_context_ref": copy.deepcopy(projection_v2["m5_ref"]),
        "effective_m4_lineage_ref": copy.deepcopy(projection_v2["lineage_ref"]),
        "release_bundle_sha256": state["planning_v2"]["bundle_sha"],
        "contract_release_sha256": state["contract_release_sha"],
        "product_contract_sha256": state["product_contract_sha"],
        "qualification_governance_contract_sha256": state[
            "qualification_contract_sha"
        ],
        "issue_head": copy.deepcopy(current_issue_head),
        "issue_reconciliation_set_sha256": issue_reconciliation_sha,
        "signing_policy_ref": copy.deepcopy(signing_policy_ref),
        "key_head_ref": copy.deepcopy(key_head_ref),
        "quorum_policy_ref": copy.deepcopy(quorum_policy_ref),
        "promotion_plan_ref": copy.deepcopy(promotion_plan_ref),
        "promotion_operation_assignment_ref": copy.deepcopy(
            promotion_assignment_ref
        ),
        "planned_operator": copy.deepcopy(planned_operator),
        "rc_target": copy.deepcopy(rc_target),
        "release_train": "fixture-release-train-0-5",
        "attempt_terminal_slot": attempt_terminal_slot,
        "customer_visibility": "QUARANTINED",
        "actual_distribution_activation": "PENDING_SIGNOFF",
        "input_set_semantics": "IMMUTABLE_EXACT_INPUT_ROOT_NO_LATER_REDEFINITION",
        "frozen_at": "2026-06-01T13:12:00Z",
        "artifact_metadata": metadata(),
    }
    attempt_input_filename = "release-attempt-input-set.json"
    attempt_input_sha = corpus.add(
        "VALID-RELEASE-ATTEMPT-INPUT-SET-01",
        attempt_input_filename,
        "release-attempt-input-set-v1.schema.json",
        attempt_input,
    )
    attempt_input_ref = vref(
        attempt_input["input_set_id"],
        attempt_input["schema"],
        attempt_input_sha,
        attempt_input_filename,
    )

    freshness_validator = {
        "schema": "ylx.release-freshness-validator.v1",
        "artifact_id": "fixture-release-freshness-validator",
        "revision": 1,
        "implementation": "fixture-release-freshness-validator-v1",
        "notice": NOTICE,
    }
    freshness_validator_sha = corpus.add_support(
        "release-freshness-validator.json",
        freshness_validator,
        "Exact synthetic validator artifact used by release freshness checkpoints.",
    )
    freshness_validator_ref = artifact_ref(
        freshness_validator["artifact_id"],
        freshness_validator["schema"],
        freshness_validator_sha,
        (
            "contracts/fixtures/governance-models/support/"
            "release-freshness-validator.json"
        ),
    )
    authority_snapshot_fields = (
        "m5_binding_context_ref",
        "effective_m4_lineage_ref",
        "pre_release_ref",
        "readiness_ref",
        "distribution_drill_ref",
        "domain_attestation_ref_by_role",
        "quorum_signature_ref_by_role",
        "role_assignment_ref_by_role",
        "signing_policy_ref",
        "key_head_ref",
        "quorum_policy_ref",
        "promotion_plan_ref",
        "promotion_operation_assignment_ref",
        "release_bundle_sha256",
        "contract_release_sha256",
        "product_contract_sha256",
        "qualification_governance_contract_sha256",
        "issue_head",
        "issue_reconciliation_set_sha256",
    )
    authority_snapshot = {
        field: copy.deepcopy(attempt_input[field])
        for field in authority_snapshot_fields
    }
    authority_snapshot_sha = sha(canonical_bytes(authority_snapshot))

    checkpoint_spec = {
        1: (
            "quorum_signature_collection",
            "PRE_PUBLICATION_FENCE",
            "ACQUIRE_PUBLICATION_FENCE",
            "2026-06-01T13:13:00Z",
        ),
        2: (
            "publication_fence_acquisition",
            "POST_PUBLICATION_FENCE",
            "ADVANCE_TO_PRE_PROMOTION_CHECK",
            "2026-06-01T13:15:00Z",
        ),
        3: (
            "pre_promotion",
            "PRE_GA_PROMOTION",
            "PROMOTE_EXACT_RC_AND_READBACK",
            "2026-06-01T13:16:00Z",
        ),
        4: (
            "promotion_readback",
            "POST_GA_READBACK_PRE_MANIFEST_CONSTRUCTION",
            "CONSTRUCT_FINAL_MANIFEST_PAYLOAD",
            "2026-06-01T13:18:00Z",
        ),
        5: (
            "final_manifest_publish",
            "PRE_FINAL_MANIFEST_PUBLICATION",
            "PUBLISH_AND_READBACK_FINAL_MANIFEST",
            "2026-06-01T13:20:00Z",
        ),
        6: (
            "final_manifest_readback",
            "POST_FINAL_MANIFEST_READBACK",
            "ADVANCE_TO_PRE_TERMINAL_CAS_CHECK",
            "2026-06-01T13:23:00Z",
        ),
        7: (
            "finalized_terminal_reference_cas",
            "PRE_FINALIZED_TERMINAL_REFERENCE_CAS",
            "CAS_AND_READBACK_FINALIZED_TERMINAL_REFERENCE",
            "2026-06-01T13:24:00Z",
        ),
        8: (
            "finalized_terminal_reference_readback",
            "POST_FINALIZED_TERMINAL_REFERENCE_READBACK",
            "PUBLISH_INITIAL_ACTIVE_DISTRIBUTION",
            "2026-06-01T13:27:00Z",
        ),
    }
    action_input_sha_by_sequence = {
        sequence: sha(f"fixture-release-v2-action-{sequence}")
        for sequence in range(1, 9)
    }
    checkpoint_refs: dict[int, dict[str, Any]] = {}
    checkpoint_digests: dict[int, str] = {}

    def known_artifacts(
        **updates: dict[str, Any] | None,
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "release_attempt_input_set": copy.deepcopy(attempt_input_ref),
            "release_publication_fence": None,
            "ga_promotion_receipt": None,
            "release_closure_manifest": None,
            "content_addressed_publication_receipt": None,
            "content_addressed_readback_receipt": None,
            "terminal_slot_cas_receipt": None,
            "terminal_slot_readback_receipt": None,
        }
        value.update(copy.deepcopy(updates))
        return value

    def event_receipts(
        **updates: dict[str, Any] | None,
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "publication_fence_ref": None,
            "ga_promotion_receipt_ref": None,
            "content_publication_receipt_ref": None,
            "content_readback_receipt_ref": None,
            "terminal_cas_receipt_ref": None,
            "terminal_readback_receipt_ref": None,
        }
        value.update(copy.deepcopy(updates))
        return value

    def add_checkpoint(
        sequence: int,
        known: dict[str, Any],
        events: dict[str, Any],
    ) -> dict[str, Any]:
        checkpoint_name, phase, action, checked_at = checkpoint_spec[sequence]
        checkpoint_id = f"fixture-release-freshness-checkpoint-{sequence}"
        checkpoint = {
            "schema": "ylx.release-freshness-checkpoint.v1",
            "checkpoint_id": checkpoint_id,
            "attempt_id": attempt_id,
            "sequence": sequence,
            "checkpoint": checkpoint_name,
            "phase": phase,
            "attempt_input_set_ref": copy.deepcopy(attempt_input_ref),
            "predecessor_checkpoint_ref": (
                None
                if sequence == 1
                else copy.deepcopy(checkpoint_refs[sequence - 1])
            ),
            "predecessor_checkpoint_sequence": (
                None if sequence == 1 else sequence - 1
            ),
            "authority_snapshot": copy.deepcopy(authority_snapshot),
            "authority_snapshot_sha256": authority_snapshot_sha,
            "known_artifact_ref_by_kind": copy.deepcopy(known),
            "event_receipt_refs": copy.deepcopy(events),
            "planned_action_input_sha256": action_input_sha_by_sequence[
                sequence
            ],
            "authorizes_action": action,
            "validator_artifact_ref": copy.deepcopy(freshness_validator_ref),
            "checker_assignment_ref": copy.deepcopy(
                planning_owner_authority_ref
            ),
            "result": "PASS",
            "failure_codes": [],
            "checked_at": checked_at,
            "artifact_metadata": metadata(),
        }
        filename = f"release-freshness-checkpoint-{sequence}.json"
        digest = corpus.add(
            f"VALID-RELEASE-FRESHNESS-CHECKPOINT-{sequence}-01",
            filename,
            "release-freshness-checkpoint-v1.schema.json",
            checkpoint,
        )
        checkpoint_digests[sequence] = digest
        checkpoint_ref = vref(
            checkpoint_id,
            checkpoint["schema"],
            digest,
            filename,
            None,
        )
        checkpoint_refs[sequence] = checkpoint_ref
        return checkpoint_ref

    checkpoint_1_ref = add_checkpoint(
        1,
        known_artifacts(),
        event_receipts(),
    )

    role_assignment_bindings = {
        role: {
            "person_id": assignment_values[role]["person_id"],
            "assignment_ref": copy.deepcopy(role_assignment_refs[role]),
        }
        for role in QUORUM_ROLES
    }
    fence = {
        "schema": "ylx.release-publication-fence.v2",
        "fence_id": "fixture-release-publication-fence-v2",
        "revision": 1,
        "attempt_id": attempt_id,
        "fence_authority_id": "fixture-release-fence-authority-v2",
        "attempt_input_set_ref": copy.deepcopy(attempt_input_ref),
        "authorizing_freshness_checkpoint_ref": copy.deepcopy(
            checkpoint_1_ref
        ),
        "authorizing_freshness_checkpoint_sequence": 1,
        "planned_action_input_sha256": action_input_sha_by_sequence[1],
        "pre_release_ref": copy.deepcopy(pre_release_ref),
        "pre_release_sha256": pre_release_sha,
        "domain_attestation_sha256_by_role": copy.deepcopy(
            attestation_digests
        ),
        "quorum_signature_sha256_by_role": copy.deepcopy(signature_digests),
        "role_assignment_by_role": role_assignment_bindings,
        "release_operation_assignment_ref": copy.deepcopy(
            promotion_assignment_ref
        ),
        "binding_context_ref": copy.deepcopy(projection_v2["m5_ref"]),
        "effective_m4_lineage_ref": copy.deepcopy(projection_v2["lineage_ref"]),
        "contract_release_sha256": state["contract_release_sha"],
        "product_contract_sha256": state["product_contract_sha"],
        "qualification_governance_contract_sha256": state[
            "qualification_contract_sha"
        ],
        "fresh_issue_head": copy.deepcopy(current_issue_head),
        "signing_policy_ref": copy.deepcopy(signing_policy_ref),
        "signing_policy_sha256": signing_policy_sha,
        "key_head_ref": copy.deepcopy(key_head_ref),
        "key_head_sha256": key_head_sha,
        "quorum_policy_ref": copy.deepcopy(quorum_policy_ref),
        "quorum_policy_sha256": quorum_policy_sha,
        "promotion_plan_ref": copy.deepcopy(promotion_plan_ref),
        "promotion_plan_sha256": promotion_plan_sha,
        "planned_promotion_operator": copy.deepcopy(planned_operator),
        "release_train": attempt_input["release_train"],
        "system_milestone": attempt_input["system_milestone"],
        **copy.deepcopy(rc_target),
        "attempt_terminal_slot": attempt_terminal_slot,
        "initial_customer_visibility": "QUARANTINED",
        "required_key_validity_horizon_seconds": signing_policy[
            "minimum_key_validity_horizon_seconds"
        ],
        "acquired_at": "2026-06-01T13:14:00Z",
        "fence_semantics": {
            "acquisition": "CREATE_IF_ABSENT_SINGLE_ATTEMPT",
            "active_derivation": "TERMINAL_SLOT_ABSENT",
            "parallel_attempts": "FORBIDDEN",
            "ordinary_authority_successors": "BLOCKED_WHILE_ACTIVE",
            "emergency_revocation": (
                "TERMINATE_ATTEMPT_AND_KEEP_GA_QUARANTINED"
            ),
            "ga_visibility": (
                "QUARANTINED_UNTIL_VALID_ACTIVE_DISTRIBUTION_HEAD"
            ),
            "recovery": "SAME_IMMUTABLE_INPUTS_ONLY",
            "release_condition": (
                "VALID_MANIFEST_AND_FINALIZED_SLOT_REFERENCE_DURABLE_EXACT_READBACK"
            ),
            "missing_manifest_state": "RELEASE_NOT_COMPLETE",
            "orphan_final_payload_state": "FINAL_PAYLOAD_DURABLE_NOT_ACTIVATED",
            "finalized_state": "FINAL_REFERENCE_DURABLE",
            "aborted_state": "ABORTED_REFERENCE_DURABLE",
            "pre_finalized_cas_freshness_mismatch": (
                "ABORTED_AND_QUARANTINED"
            ),
            "post_finalized_cas_pre_readback_freshness_mismatch": (
                "IMMUTABLE_INVALID_TERMINAL_NO_PASS_NO_RELEASE_COMPLETE_NO_OVERWRITE_OR_REUSE"
            ),
        },
        "artifact_metadata": metadata(),
    }
    fence_filename = "release-publication-fence-v2.json"
    fence_sha = corpus.add(
        "VALID-RELEASE-PUBLICATION-FENCE-V2-01",
        fence_filename,
        "release-publication-fence-v2.schema.json",
        fence,
    )
    fence_ref = vref(
        fence["fence_id"],
        fence["schema"],
        fence_sha,
        fence_filename,
    )

    checkpoint_2_known = known_artifacts(release_publication_fence=fence_ref)
    checkpoint_2_events = event_receipts(publication_fence_ref=fence_ref)
    add_checkpoint(2, checkpoint_2_known, checkpoint_2_events)
    checkpoint_3_ref = add_checkpoint(
        3,
        checkpoint_2_known,
        checkpoint_2_events,
    )

    remote_observation = {
        "canonical_remote_id": rc_target["canonical_remote_id"],
        "ga_ref": rc_target["ga_ref"],
        "ga_channel": rc_target["ga_channel"],
        "canonical_ga_target": rc_target["canonical_ga_target"],
        "observed_ref_target_commit": rc_target["rc_commit"],
        "observed_artifact_sha256": rc_target["rc_artifact_sha256"],
        "operation_result": "CREATED_EXACT",
        "observed_at": "2026-06-01T13:16:30Z",
    }
    promotion_receipt = {
        "schema": "ylx.ga-promotion-receipt.v2",
        "receipt_id": "fixture-ga-promotion-receipt-v2",
        "revision": 1,
        "attempt_id": attempt_id,
        "attempt_input_set_ref": copy.deepcopy(attempt_input_ref),
        "authorizing_freshness_checkpoint_ref": copy.deepcopy(
            checkpoint_3_ref
        ),
        "authorizing_freshness_checkpoint_sequence": 3,
        "planned_action_input_sha256": action_input_sha_by_sequence[3],
        "publication_fence_ref": copy.deepcopy(fence_ref),
        "publication_fence_sha256": fence_sha,
        "promotion_plan_ref": copy.deepcopy(promotion_plan_ref),
        "promotion_plan_sha256": promotion_plan_sha,
        "release_operation_assignment_ref": copy.deepcopy(
            promotion_assignment_ref
        ),
        "actor_identity": copy.deepcopy(planned_operator),
        "binding_context_ref": copy.deepcopy(projection_v2["m5_ref"]),
        **copy.deepcopy(rc_target),
        "observed_ref_target_commit": rc_target["rc_commit"],
        "observed_artifact_sha256": rc_target["rc_artifact_sha256"],
        "operation_result": "CREATED_EXACT",
        "verified_at": "2026-06-01T13:17:00Z",
        "promotion_operator_person_id": planned_operator["person_id"],
        "ga_visibility": (
            "QUARANTINED_UNTIL_VALID_ACTIVE_DISTRIBUTION_HEAD"
        ),
        "remote_observation": remote_observation,
        "remote_observation_sha256": sha(canonical_bytes(remote_observation)),
        "artifact_metadata": metadata(),
    }
    promotion_receipt_filename = "ga-promotion-receipt-v2.json"
    promotion_receipt_sha = corpus.add(
        "VALID-GA-PROMOTION-RECEIPT-V2-01",
        promotion_receipt_filename,
        "ga-promotion-receipt-v2.schema.json",
        promotion_receipt,
    )
    promotion_receipt_ref = vref(
        promotion_receipt["receipt_id"],
        promotion_receipt["schema"],
        promotion_receipt_sha,
        promotion_receipt_filename,
    )

    checkpoint_4_known = known_artifacts(
        release_publication_fence=fence_ref,
        ga_promotion_receipt=promotion_receipt_ref,
    )
    checkpoint_4_events = event_receipts(
        publication_fence_ref=fence_ref,
        ga_promotion_receipt_ref=promotion_receipt_ref,
    )
    checkpoint_4_ref = add_checkpoint(
        4,
        checkpoint_4_known,
        checkpoint_4_events,
    )

    final_manifest = {
        "schema": "ylx.release-closure-manifest.v2",
        "closure_id": "fixture-release-closure-manifest-v2",
        "revision": 1,
        "attempt_id": attempt_id,
        "attempt_terminal_slot": attempt_terminal_slot,
        "attempt_input_set_ref": copy.deepcopy(attempt_input_ref),
        "authorizing_freshness_checkpoint_ref": copy.deepcopy(
            checkpoint_4_ref
        ),
        "authorizing_freshness_checkpoint_sequence": 4,
        "planned_action_input_sha256": action_input_sha_by_sequence[4],
        "publication_fence_ref": copy.deepcopy(fence_ref),
        "publication_fence_sha256": fence_sha,
        "pre_release_closure_ref": copy.deepcopy(pre_release_ref),
        "pre_release_closure_sha256": pre_release_sha,
        "quorum_signature_ref_by_role_slot": copy.deepcopy(signature_refs),
        "quorum_signature_sha256_by_role_slot": copy.deepcopy(
            signature_digests
        ),
        "ga_promotion_receipt_ref": copy.deepcopy(promotion_receipt_ref),
        "ga_promotion_receipt_sha256": promotion_receipt_sha,
        "binding_context_ref": copy.deepcopy(projection_v2["m5_ref"]),
        "effective_m4_lineage_ref": copy.deepcopy(projection_v2["lineage_ref"]),
        "contract_release_sha256": state["contract_release_sha"],
        "product_contract_sha256": state["product_contract_sha"],
        "qualification_governance_contract_sha256": state[
            "qualification_contract_sha"
        ],
        "fresh_issue_head": copy.deepcopy(current_issue_head),
        "issue_reconciliation_set_sha256": issue_reconciliation_sha,
        "signing_policy_ref": copy.deepcopy(signing_policy_ref),
        "signing_policy_sha256": signing_policy_sha,
        "key_validity_revocation_head_ref": copy.deepcopy(key_head_ref),
        "key_validity_revocation_head_sha256": key_head_sha,
        "quorum_policy_ref": copy.deepcopy(quorum_policy_ref),
        "quorum_policy_sha256": quorum_policy_sha,
        "final_result_map": copy.deepcopy(
            pre_release["proposed_final_result_map"]
        ),
        "release_decision": "RELEASE_COMPLETE",
        "closed_at": "2026-06-01T13:19:00Z",
        "artifact_metadata": metadata(),
    }
    final_manifest_sha = sha(canonical_bytes(final_manifest))
    final_manifest_filename = (
        "release-closure-manifests/"
        f"{final_manifest_sha}--release-closure-manifest.json"
    )
    actual_final_manifest_sha = corpus.add(
        "VALID-RELEASE-CLOSURE-MANIFEST-V2-01",
        final_manifest_filename,
        "release-closure-manifest-v2.schema.json",
        final_manifest,
    )
    if actual_final_manifest_sha != final_manifest_sha:
        raise AssertionError("release-v2 manifest digest changed during publication")
    final_manifest_ref = vref(
        final_manifest["closure_id"],
        final_manifest["schema"],
        final_manifest_sha,
        final_manifest_filename,
    )
    final_manifest_locator = (
        "contracts/fixtures/governance-models/valid/"
        + final_manifest_filename
    )

    checkpoint_5_known = known_artifacts(
        release_publication_fence=fence_ref,
        ga_promotion_receipt=promotion_receipt_ref,
        release_closure_manifest=final_manifest_ref,
    )
    checkpoint_5_events = copy.deepcopy(checkpoint_4_events)
    checkpoint_5_ref = add_checkpoint(
        5,
        checkpoint_5_known,
        checkpoint_5_events,
    )

    content_operation_id = "fixture-release-manifest-publication-v2"
    content_storage_version = "fixture-release-storage-version-v2"
    content_etag = "fixture-release-manifest-etag-v2"
    content_publication = {
        "schema": "ylx.content-addressed-publication-receipt.v1",
        "receipt_id": "fixture-content-publication-receipt-v1",
        "attempt_id": attempt_id,
        "attempt_input_set_ref": copy.deepcopy(attempt_input_ref),
        "authorizing_freshness_checkpoint_ref": copy.deepcopy(
            checkpoint_5_ref
        ),
        "authorizing_freshness_checkpoint_sequence": 5,
        "planned_action_input_sha256": action_input_sha_by_sequence[5],
        "release_manifest_ref": copy.deepcopy(final_manifest_ref),
        "payload_locator": final_manifest_locator,
        "payload_sha256": final_manifest_sha,
        "publication_authority_ref": copy.deepcopy(
            planning_owner_authority_ref
        ),
        "operation_id": content_operation_id,
        "storage_version": content_storage_version,
        "etag": content_etag,
        "operation_result": "CREATED_EXACT",
        "published_at": "2026-06-01T13:21:00Z",
        "artifact_metadata": metadata(),
    }
    content_publication_filename = "content-addressed-publication-receipt.json"
    content_publication_sha = corpus.add(
        "VALID-CONTENT-ADDRESSED-PUBLICATION-RECEIPT-01",
        content_publication_filename,
        "content-addressed-publication-receipt-v1.schema.json",
        content_publication,
    )
    content_publication_ref = vref(
        content_publication["receipt_id"],
        content_publication["schema"],
        content_publication_sha,
        content_publication_filename,
        None,
    )
    content_readback = {
        "schema": "ylx.content-addressed-readback-receipt.v1",
        "receipt_id": "fixture-content-readback-receipt-v1",
        "attempt_id": attempt_id,
        "attempt_input_set_ref": copy.deepcopy(attempt_input_ref),
        "authorizing_freshness_checkpoint_ref": copy.deepcopy(
            checkpoint_5_ref
        ),
        "authorizing_freshness_checkpoint_sequence": 5,
        "planned_action_input_sha256": action_input_sha_by_sequence[5],
        "publication_receipt_ref": copy.deepcopy(content_publication_ref),
        "release_manifest_ref": copy.deepcopy(final_manifest_ref),
        "payload_locator": final_manifest_locator,
        "expected_payload_sha256": final_manifest_sha,
        "observed_payload_sha256": final_manifest_sha,
        "readback_authority_ref": copy.deepcopy(planning_owner_authority_ref),
        "operation_id": content_operation_id,
        "storage_version": content_storage_version,
        "etag": content_etag,
        "exact_bytes_readback": True,
        "result": "EXACT_MATCH",
        "read_back_at": "2026-06-01T13:22:00Z",
        "artifact_metadata": metadata(),
    }
    content_readback_filename = "content-addressed-readback-receipt.json"
    content_readback_sha = corpus.add(
        "VALID-CONTENT-ADDRESSED-READBACK-RECEIPT-01",
        content_readback_filename,
        "content-addressed-readback-receipt-v1.schema.json",
        content_readback,
    )
    content_readback_ref = vref(
        content_readback["receipt_id"],
        content_readback["schema"],
        content_readback_sha,
        content_readback_filename,
        None,
    )

    checkpoint_6_known = known_artifacts(
        release_publication_fence=fence_ref,
        ga_promotion_receipt=promotion_receipt_ref,
        release_closure_manifest=final_manifest_ref,
        content_addressed_publication_receipt=content_publication_ref,
        content_addressed_readback_receipt=content_readback_ref,
    )
    checkpoint_6_events = event_receipts(
        publication_fence_ref=fence_ref,
        ga_promotion_receipt_ref=promotion_receipt_ref,
        content_publication_receipt_ref=content_publication_ref,
        content_readback_receipt_ref=content_readback_ref,
    )
    add_checkpoint(6, checkpoint_6_known, checkpoint_6_events)
    checkpoint_7_ref = add_checkpoint(
        7,
        checkpoint_6_known,
        checkpoint_6_events,
    )

    finalized_terminal_reference = {
        "kind": "FINALIZED",
        "payload_ref": copy.deepcopy(final_manifest_ref),
        "payload_locator": final_manifest_locator,
        "payload_sha256": final_manifest_sha,
    }
    terminal_slot_version = "fixture-terminal-slot-version-v2"
    terminal_slot_etag = "fixture-terminal-slot-etag-v2"
    terminal_cas = {
        "schema": "ylx.terminal-slot-cas-receipt.v1",
        "receipt_id": "fixture-terminal-slot-cas-receipt-v1",
        "attempt_id": attempt_id,
        "attempt_input_set_ref": copy.deepcopy(attempt_input_ref),
        "authorizing_freshness_checkpoint_ref": copy.deepcopy(
            checkpoint_7_ref
        ),
        "authorizing_freshness_checkpoint_sequence": 7,
        "pre_cas_checkpoint_ref": copy.deepcopy(checkpoint_7_ref),
        "pre_cas_checkpoint_sequence": 7,
        "planned_action_input_sha256": action_input_sha_by_sequence[7],
        "manifest_readback_receipt_ref": copy.deepcopy(content_readback_ref),
        "terminal_slot": attempt_terminal_slot,
        "expected_slot_state": "ABSENT",
        "requested_terminal_reference": copy.deepcopy(
            finalized_terminal_reference
        ),
        "cas_authority_ref": copy.deepcopy(planning_owner_authority_ref),
        "cas_operation_id": "fixture-terminal-slot-cas-operation-v2",
        "observed_slot_version": terminal_slot_version,
        "observed_etag": terminal_slot_etag,
        "operation_result": "CREATED_EXACT",
        "cas_completed_at": "2026-06-01T13:25:00Z",
        "artifact_metadata": metadata(),
    }
    terminal_cas_filename = "terminal-slot-cas-receipt.json"
    terminal_cas_sha = corpus.add(
        "VALID-TERMINAL-SLOT-CAS-RECEIPT-01",
        terminal_cas_filename,
        "terminal-slot-cas-receipt-v1.schema.json",
        terminal_cas,
    )
    terminal_cas_ref = vref(
        terminal_cas["receipt_id"],
        terminal_cas["schema"],
        terminal_cas_sha,
        terminal_cas_filename,
        None,
    )
    terminal_readback = {
        "schema": "ylx.terminal-slot-readback-receipt.v1",
        "receipt_id": "fixture-terminal-slot-readback-receipt-v1",
        "attempt_id": attempt_id,
        "attempt_input_set_ref": copy.deepcopy(attempt_input_ref),
        "authorizing_freshness_checkpoint_ref": copy.deepcopy(
            checkpoint_7_ref
        ),
        "authorizing_freshness_checkpoint_sequence": 7,
        "pre_cas_checkpoint_ref": copy.deepcopy(checkpoint_7_ref),
        "pre_cas_checkpoint_sequence": 7,
        "planned_action_input_sha256": action_input_sha_by_sequence[7],
        "terminal_cas_receipt_ref": copy.deepcopy(terminal_cas_ref),
        "payload_readback_receipt_ref": copy.deepcopy(content_readback_ref),
        "terminal_slot": attempt_terminal_slot,
        "expected_terminal_reference": copy.deepcopy(
            finalized_terminal_reference
        ),
        "observed_terminal_reference": copy.deepcopy(
            finalized_terminal_reference
        ),
        "readback_authority_ref": copy.deepcopy(planning_owner_authority_ref),
        "readback_operation_id": "fixture-terminal-slot-readback-operation-v2",
        "observed_slot_version": terminal_slot_version,
        "observed_etag": terminal_slot_etag,
        "durable_readback": True,
        "result": "EXACT_FINALIZED_REFERENCE",
        "read_back_at": "2026-06-01T13:26:00Z",
        "artifact_metadata": metadata(),
    }
    terminal_readback_filename = "terminal-slot-readback-receipt.json"
    terminal_readback_sha = corpus.add(
        "VALID-TERMINAL-SLOT-READBACK-RECEIPT-01",
        terminal_readback_filename,
        "terminal-slot-readback-receipt-v1.schema.json",
        terminal_readback,
    )
    terminal_readback_ref = vref(
        terminal_readback["receipt_id"],
        terminal_readback["schema"],
        terminal_readback_sha,
        terminal_readback_filename,
        None,
    )

    checkpoint_8_known = known_artifacts(
        release_publication_fence=fence_ref,
        ga_promotion_receipt=promotion_receipt_ref,
        release_closure_manifest=final_manifest_ref,
        content_addressed_publication_receipt=content_publication_ref,
        content_addressed_readback_receipt=content_readback_ref,
        terminal_slot_cas_receipt=terminal_cas_ref,
        terminal_slot_readback_receipt=terminal_readback_ref,
    )
    checkpoint_8_events = event_receipts(
        publication_fence_ref=fence_ref,
        ga_promotion_receipt_ref=promotion_receipt_ref,
        content_publication_receipt_ref=content_publication_ref,
        content_readback_receipt_ref=content_readback_ref,
        terminal_cas_receipt_ref=terminal_cas_ref,
        terminal_readback_receipt_ref=terminal_readback_ref,
    )
    checkpoint_8_ref = add_checkpoint(
        8,
        checkpoint_8_known,
        checkpoint_8_events,
    )

    distribution_operator_person = "fixture-qa-evidence-owner-person"
    distribution_operator = {
        "actor_id": "fixture-release-distribution-operator-v2",
        "person_id": distribution_operator_person,
        "natural_person_identity_ref": copy.deepcopy(
            state["foundation"]["identity_refs_by_person"][
                distribution_operator_person
            ]
        ),
        "role_slot": "qa-evidence-owner",
        "role_assignment_ref": copy.deepcopy(planning_owner_authority_ref),
    }
    channel_scope = {
        "channel_ids": [rc_target["ga_channel"]],
        "visibility_surfaces": [
            "CUSTOMER_DISCOVERY",
            "DEFAULT_INSTALLATION",
            "AUTOMATIC_UPGRADE",
            "PUBLIC_INDEXES",
        ],
    }
    finalized_release_binding = {
        "manifest_ref": copy.deepcopy(final_manifest_ref),
        "terminal_slot": attempt_terminal_slot,
        "terminal_reference": copy.deepcopy(finalized_terminal_reference),
        "terminal_reference_readback_sha256": terminal_readback_sha,
    }
    channel_binding = {
        "distribution_head_slot": "release-distribution-heads/fixture-ga-0-5",
        "channel_scope": copy.deepcopy(channel_scope),
        "channel_scope_sha256": sha(canonical_bytes(channel_scope)),
        "channel_authority_ref": {
            "ref_id": "fixture-release-distribution-channel-authority",
            "authority_kind": "promotion-authority",
            "locator": (
                "contracts/fixtures/governance-models/valid/"
                "ga-promotion-plan-v2.json"
            ),
            "sha256": promotion_plan_sha,
        },
    }
    distribution_execution_constraints = {
        "create_if_absent_only": True,
        "overwrite_allowed": False,
        "finalized_release_mutation_allowed": False,
        "channel_scope_expansion_allowed": False,
        "successor_must_bind_expected_predecessor": True,
        "cas_conflict_requires_winner_reload": True,
    }

    def add_distribution_assignment(
        operation: str,
        suffix: str,
        effective_from: str,
        expected_predecessor_control_ref: dict[str, Any] | None,
        incident_id: str | None,
        incident_ref: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        incident_binding = (
            None
            if incident_id is None
            else {
                "incident_id": incident_id,
                "incident_ref": copy.deepcopy(incident_ref),
            }
        )
        assignment = {
            "schema": "ylx.release-operation-assignment.v2",
            "assignment_id": f"fixture-release-operation-assignment-v2-{suffix}",
            "revision": 1,
            "predecessor_assignment_ref": None,
            "authorized_operation": operation,
            "actor_identity": copy.deepcopy(distribution_operator),
            "operation_scope": {
                "scope_kind": "POST_FINALIZED_DISTRIBUTION",
                "finalized_release_binding": copy.deepcopy(
                    finalized_release_binding
                ),
                "channel_binding": copy.deepcopy(channel_binding),
                "expected_predecessor_control_ref": copy.deepcopy(
                    expected_predecessor_control_ref
                ),
                "incident_binding": incident_binding,
                "execution_constraints": copy.deepcopy(
                    distribution_execution_constraints
                ),
            },
            "effective_from": effective_from,
            "expires_at": NOT_AFTER,
            "assignment_status": "ACTIVE",
            "issued_by": [
                owner_approval("release-owner", "2026-06-01T13:27:00Z"),
                owner_approval("security-owner", "2026-06-01T13:27:00Z"),
            ],
            "artifact_metadata": metadata(),
        }
        filename = f"release-operation-assignment-v2-{suffix}.json"
        digest = corpus.add(
            f"VALID-RELEASE-OPERATION-ASSIGNMENT-V2-{suffix.upper()}-01",
            filename,
            "release-operation-assignment-v2.schema.json",
            assignment,
        )
        assignment_ref = vref(
            assignment["assignment_id"],
            assignment["schema"],
            digest,
            filename,
        )
        return assignment, assignment_ref

    def sign_distribution_control_v2(
        unsigned_control: dict[str, Any],
        signed_at: str,
    ) -> dict[str, Any]:
        payload = canonical_bytes(unsigned_control)
        payload_sha = sha(payload)
        control = copy.deepcopy(unsigned_control)
        control["signatures_by_role_slot"] = {}
        for role in ("release-owner", "security-owner"):
            signature_domain = f"ylx.release-distribution-control.v2/{role}"
            signature = private_keys[role].sign(
                signature_domain.encode("ascii") + b"\x00" + payload
            )
            control["signatures_by_role_slot"][role] = {
                "role_slot": role,
                "person_id": assignment_values[role]["person_id"],
                "signing_key_fingerprint": fingerprint_by_role[role],
                "role_assignment_ref": {
                    "authority_id": assignment_values[role]["assignment_id"],
                    "revision": assignment_values[role]["revision"],
                    "artifact_path": (
                        "contracts/fixtures/governance-models/valid/"
                        f"role-signing-key-assignment-{role}.json"
                    ),
                    "artifact_sha256": assignment_digests[role],
                    "verified_at": signed_at,
                },
                "signed_at": signed_at,
                "signature_domain": signature_domain,
                "signed_payload_sha256": payload_sha,
                "signature_b64": base64.b64encode(signature).decode("ascii"),
            }
        return control

    def add_distribution_control(
        revision: int,
        predecessor_control_ref: dict[str, Any] | None,
        assignment_ref: dict[str, Any],
        action: str,
        incident_ref: dict[str, Any] | None,
        reason: str | None,
        client_behavior: dict[str, Any],
        producer_behavior: dict[str, Any],
        consumer_behavior: dict[str, Any],
        compatibility_window: dict[str, Any],
        recovery_condition: dict[str, Any],
        created_at: str,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        unsigned_control = {
            "schema": "ylx.release-distribution-control.v2",
            "control_id": "fixture-release-distribution-control-v2",
            "revision": revision,
            "predecessor_control_ref": copy.deepcopy(
                predecessor_control_ref
            ),
            "attempt_id": attempt_id,
            "attempt_input_set_ref": copy.deepcopy(attempt_input_ref),
            "production_deployment_readiness_ref": copy.deepcopy(
                readiness_ref
            ),
            "distribution_drill_ref": copy.deepcopy(distribution_drill_ref),
            "prerequisite_evidence_semantics": (
                "PREREQUISITE_ONLY_NO_REVERSE_EVIDENCE"
            ),
            "authorizing_freshness_checkpoint_ref": copy.deepcopy(
                checkpoint_8_ref
            ),
            "authorizing_freshness_checkpoint_sequence": 8,
            "planned_action_input_sha256": action_input_sha_by_sequence[8],
            "terminal_slot_readback_receipt_ref": copy.deepcopy(
                terminal_readback_ref
            ),
            "release_operation_assignment_ref": copy.deepcopy(assignment_ref),
            "actor_identity": copy.deepcopy(distribution_operator),
            "finalized_release_manifest_ref": copy.deepcopy(final_manifest_ref),
            "finalized_terminal_reference": copy.deepcopy(
                finalized_terminal_reference
            ),
            "channel_scope": copy.deepcopy(channel_scope),
            "action": action,
            "incident_ref": copy.deepcopy(incident_ref),
            "reason": reason,
            "effective_at": created_at,
            "required_rto_seconds": 900,
            "client_behavior": copy.deepcopy(client_behavior),
            "producer_behavior": copy.deepcopy(producer_behavior),
            "consumer_behavior": copy.deepcopy(consumer_behavior),
            "compatibility_window": copy.deepcopy(compatibility_window),
            "recovery_condition": copy.deepcopy(recovery_condition),
            "redirect_target_finalized_manifest_ref": None,
            "distribution_activation_phase": "POST_FINALIZATION_ONLY",
            "immutable_release_relation": (
                "NO_REVERSE_EVIDENCE_NO_SIGNOFF_OR_HISTORY_REWRITE"
            ),
            "signing_policy_ref": copy.deepcopy(signing_policy_ref),
            "signing_policy_sha256": signing_policy_sha,
            "created_at": created_at,
            "artifact_metadata": metadata(),
        }
        control = sign_distribution_control_v2(unsigned_control, created_at)
        filename = f"release-distribution-control-v2-r{revision}.json"
        digest = corpus.add(
            f"VALID-RELEASE-DISTRIBUTION-CONTROL-V2-R{revision}-01",
            filename,
            "release-distribution-control-v2.schema.json",
            control,
        )
        control_ref = vref(
            control["control_id"],
            control["schema"],
            digest,
            filename,
            revision,
        )
        return control, control_ref, digest

    def add_transition_receipt(
        suffix: str,
        transition_kind: str,
        assignment_ref: dict[str, Any],
        incident_lineage: dict[str, Any],
        expected_control_ref: dict[str, Any] | None,
        successor_control_ref: dict[str, Any],
        predecessor_receipt_ref: dict[str, Any] | None,
        cas_at: str,
        readback_at: str,
        completed_at: str,
    ) -> dict[str, Any]:
        successor_ref_sha = sha(canonical_bytes(successor_control_ref))
        receipt = {
            "schema": "ylx.release-distribution-head-transition-receipt.v1",
            "receipt_id": f"fixture-release-distribution-transition-{suffix}",
            "transition_attempt_id": (
                f"fixture-release-distribution-transition-attempt-{suffix}"
            ),
            "transition_kind": transition_kind,
            "operation_assignment_ref": copy.deepcopy(assignment_ref),
            "finalized_release_binding": copy.deepcopy(
                finalized_release_binding
            ),
            "channel_binding": copy.deepcopy(channel_binding),
            "incident_lineage": copy.deepcopy(incident_lineage),
            "expected_predecessor_control_ref": copy.deepcopy(
                expected_control_ref
            ),
            "attempted_successor_control_ref": copy.deepcopy(
                successor_control_ref
            ),
            "predecessor_transition_receipt_ref": copy.deepcopy(
                predecessor_receipt_ref
            ),
            "operation_result": "CREATED_EXACT",
            "cas_observation": {
                "expected_head_reference_sha256": (
                    None
                    if expected_control_ref is None
                    else sha(canonical_bytes(expected_control_ref))
                ),
                "attempted_head_reference_sha256": successor_ref_sha,
                "observed_head_reference_sha256": successor_ref_sha,
                "durable_readback_sha256": successor_ref_sha,
                "head_created": True,
                "attempted_value_exact_match": True,
                "cas_at": cas_at,
                "readback_at": readback_at,
            },
            "observed_winner_control_ref": copy.deepcopy(
                successor_control_ref
            ),
            "conflict_recovery": None,
            "completed_at": completed_at,
            "artifact_metadata": metadata(),
        }
        filename = f"release-distribution-transition-{suffix}.json"
        digest = corpus.add(
            f"VALID-RELEASE-DISTRIBUTION-TRANSITION-{suffix.upper()}-01",
            filename,
            "release-distribution-head-transition-receipt-v1.schema.json",
            receipt,
        )
        return vref(
            receipt["receipt_id"],
            receipt["schema"],
            digest,
            filename,
            None,
        )

    active_client_behavior = {
        "customer_discovery": "VISIBLE",
        "default_installation": "ENABLED",
        "automatic_upgrade": "ENABLED",
        "public_indexes": "VISIBLE",
        "offline_client_notice": "The synthetic finalized release is available.",
    }
    active_producer_behavior = {
        "write_disposition": "CONTINUE",
        "operator_instruction": "Continue the synthetic finalized producer.",
    }
    active_consumer_behavior = {
        "read_disposition": "TARGET_ONLY",
        "existing_data_disposition": "PRESERVE_EXISTING_DATA",
    }
    active_compatibility_window = {
        "required_data_scope": "fixture-required-data-scope-v2",
        "target_read_from": "2026-06-01T13:29:00Z",
        "legacy_read_until": None,
        "dual_read_required": False,
    }
    _initial_assignment, initial_assignment_ref = add_distribution_assignment(
        "PUBLISH_INITIAL_ACTIVE_HEAD",
        "initial-active",
        "2026-06-01T13:28:00Z",
        None,
        None,
        None,
    )
    _distribution_active, distribution_active_ref, distribution_active_sha = (
        add_distribution_control(
            1,
            None,
            initial_assignment_ref,
            "ACTIVE",
            None,
            None,
            active_client_behavior,
            active_producer_behavior,
            active_consumer_behavior,
            active_compatibility_window,
            {
                "condition": (
                    "Publish a signed direct successor when distribution policy changes."
                ),
                "not_before": None,
                "required_evidence": (
                    "Current signed synthetic distribution-control head."
                ),
                "recovery_evidence_refs": [],
            },
            "2026-06-01T13:29:00Z",
        )
    )
    no_incident_lineage = {
        "lineage_kind": "NO_INCIDENT_INITIAL_ACTIVE",
        "incident_ref": None,
        "incident_resolution_ref": None,
        "reactivation_evidence_refs": [],
    }
    initial_transition_ref = add_transition_receipt(
        "initial-active",
        "INITIAL_ACTIVE",
        initial_assignment_ref,
        no_incident_lineage,
        None,
        distribution_active_ref,
        None,
        "2026-06-01T13:29:20Z",
        "2026-06-01T13:29:40Z",
        "2026-06-01T13:30:00Z",
    )

    incident_id = "fixture-release-incident-v1"
    _declaration_assignment, declaration_assignment_ref = (
        add_distribution_assignment(
            "DECLARE_INCIDENT",
            "declare-incident",
            "2026-06-01T13:31:00Z",
            distribution_active_ref,
            incident_id,
            None,
        )
    )
    incident = {
        "schema": "ylx.release-incident.v1",
        "incident_id": incident_id,
        "root_incident_id": incident_id,
        "revision": 1,
        "predecessor_incident_ref": None,
        "finalized_release_binding": copy.deepcopy(finalized_release_binding),
        "channel_binding": copy.deepcopy(channel_binding),
        "triggering_control_ref": copy.deepcopy(distribution_active_ref),
        "declaration_assignment_ref": copy.deepcopy(
            declaration_assignment_ref
        ),
        "incident_kind": "RELEASE_DEFECT",
        "severity": "SEV2",
        "status": "OPEN",
        "detected_at": "2026-06-01T13:31:30Z",
        "declared_at": "2026-06-01T13:32:00Z",
        "summary": "Synthetic release withdrawal exercise.",
        "impact": "Synthetic customer distribution is withdrawn for validation.",
        "required_distribution_action": "WITHDRAWN",
        "containment": {
            "customer_visibility": "WITHDRAW",
            "producer_writes": "STOP",
            "consumer_reads": "DUAL_READ_REQUIRED",
            "existing_data_disposition": "PRESERVE_EXISTING_DATA",
            "finalized_history_mutation": "FORBIDDEN",
        },
        "detection_evidence_refs": [copy.deepcopy(terminal_readback_ref)],
        "artifact_metadata": metadata(),
    }
    incident_filename = "release-incident-v1.json"
    incident_sha = corpus.add(
        "VALID-RELEASE-INCIDENT-V1-01",
        incident_filename,
        "release-incident-v1.schema.json",
        incident,
    )
    incident_ref = vref(
        incident_id,
        incident["schema"],
        incident_sha,
        incident_filename,
    )

    _withdrawn_assignment, withdrawn_assignment_ref = (
        add_distribution_assignment(
            "PUBLISH_WITHDRAWN_HEAD",
            "withdrawn",
            "2026-06-01T13:33:00Z",
            distribution_active_ref,
            incident_id,
            incident_ref,
        )
    )
    withdrawn_compatibility_window = {
        "required_data_scope": "fixture-required-data-scope-v2",
        "target_read_from": "2026-06-01T13:29:00Z",
        "legacy_read_until": NOT_AFTER,
        "dual_read_required": True,
    }
    _distribution_withdrawn, distribution_withdrawn_ref, distribution_withdrawn_sha = (
        add_distribution_control(
            2,
            distribution_active_ref,
            withdrawn_assignment_ref,
            "WITHDRAWN",
            incident_ref,
            "Synthetic incident containment requires withdrawal.",
            {
                "customer_discovery": "HIDDEN",
                "default_installation": "DISABLED",
                "automatic_upgrade": "DISABLED",
                "public_indexes": "HIDDEN",
                "offline_client_notice": "The synthetic release is withdrawn.",
            },
            {
                "write_disposition": "STOP",
                "operator_instruction": (
                    "Stop the synthetic producer and preserve existing data."
                ),
            },
            {
                "read_disposition": "DUAL_READ_REQUIRED",
                "existing_data_disposition": "PRESERVE_EXISTING_DATA",
            },
            withdrawn_compatibility_window,
            {
                "condition": (
                    "Resolve the incident and publish current compatibility evidence."
                ),
                "not_before": "2026-06-01T13:35:00Z",
                "required_evidence": (
                    "Incident resolution and retained-data compatibility evidence."
                ),
                "recovery_evidence_refs": [copy.deepcopy(incident_ref)],
            },
            "2026-06-01T13:34:00Z",
        )
    )
    bound_incident_lineage = {
        "lineage_kind": "BOUND_INCIDENT",
        "incident_ref": copy.deepcopy(incident_ref),
        "incident_resolution_ref": None,
        "reactivation_evidence_refs": [],
    }
    withdrawn_transition_ref = add_transition_receipt(
        "withdrawn",
        "WITHDRAW",
        withdrawn_assignment_ref,
        bound_incident_lineage,
        distribution_active_ref,
        distribution_withdrawn_ref,
        initial_transition_ref,
        "2026-06-01T13:34:20Z",
        "2026-06-01T13:34:40Z",
        "2026-06-01T13:35:00Z",
    )

    _resolution_assignment, resolution_assignment_ref = (
        add_distribution_assignment(
            "RESOLVE_INCIDENT",
            "resolve-incident",
            "2026-06-01T13:36:00Z",
            distribution_withdrawn_ref,
            incident_id,
            incident_ref,
        )
    )
    resolution = {
        "schema": "ylx.release-incident-resolution.v1",
        "resolution_id": "fixture-release-incident-resolution-v1",
        "incident_id": incident_id,
        "incident_root_id": incident_id,
        "revision": 1,
        "predecessor_resolution_ref": None,
        "incident_ref": copy.deepcopy(incident_ref),
        "finalized_release_binding": copy.deepcopy(finalized_release_binding),
        "channel_binding": copy.deepcopy(channel_binding),
        "predecessor_control_ref": copy.deepcopy(
            distribution_withdrawn_ref
        ),
        "resolution_assignment_ref": copy.deepcopy(
            resolution_assignment_ref
        ),
        "status": "RESOLVED",
        "resolved_at": "2026-06-01T13:37:00Z",
        "root_cause_sha256": sha("fixture-release-v2-root-cause"),
        "root_cause_summary": (
            "Synthetic compatibility observation was resolved without changing "
            "the finalized release history."
        ),
        "corrective_actions": [
            {
                "action_id": "fixture-release-v2-corrective-action",
                "description": (
                    "Re-run the retained-data compatibility validation."
                ),
                "status": "COMPLETED",
                "evidence_refs": [copy.deepcopy(content_readback_ref)],
            }
        ],
        "verification_evidence_refs": [copy.deepcopy(content_readback_ref)],
        "residual_risk": "NONE_IDENTIFIED",
        "distribution_reactivation_authority": (
            "REQUIRES_SEPARATE_CURRENT_REACTIVATION_EVIDENCE_AND_SIGNED_SUCCESSOR"
        ),
        "artifact_metadata": metadata(),
    }
    resolution_filename = "release-incident-resolution-v1.json"
    resolution_sha = corpus.add(
        "VALID-RELEASE-INCIDENT-RESOLUTION-V1-01",
        resolution_filename,
        "release-incident-resolution-v1.schema.json",
        resolution,
    )
    resolution_ref = vref(
        resolution["resolution_id"],
        resolution["schema"],
        resolution_sha,
        resolution_filename,
    )

    _evidence_assignment, evidence_assignment_ref = (
        add_distribution_assignment(
            "PUBLISH_REACTIVATION_EVIDENCE",
            "reactivation-evidence",
            "2026-06-01T13:38:00Z",
            distribution_withdrawn_ref,
            incident_id,
            incident_ref,
        )
    )
    reactivation_evidence = {
        "schema": "ylx.release-reactivation-evidence.v1",
        "evidence_id": "fixture-release-reactivation-evidence-v1",
        "revision": 1,
        "predecessor_evidence_ref": None,
        "incident_ref": copy.deepcopy(incident_ref),
        "incident_resolution_ref": copy.deepcopy(resolution_ref),
        "finalized_release_binding": copy.deepcopy(finalized_release_binding),
        "channel_binding": copy.deepcopy(channel_binding),
        "predecessor_control_ref": copy.deepcopy(
            distribution_withdrawn_ref
        ),
        "evaluation_assignment_ref": copy.deepcopy(evidence_assignment_ref),
        "required_data_scope": "fixture-required-data-scope-v2",
        "compatibility_window_sha256": sha(
            canonical_bytes(withdrawn_compatibility_window)
        ),
        "test_plan_ref": copy.deepcopy(distribution_drill_ref),
        "evidence_refs": [
            copy.deepcopy(readiness_ref),
            copy.deepcopy(content_readback_ref),
        ],
        "result": "PASS",
        "evaluated_at": "2026-06-01T13:39:00Z",
        "valid_until": "2026-06-02T13:39:00Z",
        "retained_data_disposition": "PRESERVE_EXISTING_DATA",
        "finalized_history_mutation": "FORBIDDEN",
        "reactivation_disposition": (
            "ELIGIBLE_FOR_SIGNED_DIRECT_SUCCESSOR_ONLY"
        ),
        "artifact_metadata": metadata(),
    }
    reactivation_evidence_filename = "release-reactivation-evidence-v1.json"
    reactivation_evidence_sha = corpus.add(
        "VALID-RELEASE-REACTIVATION-EVIDENCE-V1-01",
        reactivation_evidence_filename,
        "release-reactivation-evidence-v1.schema.json",
        reactivation_evidence,
    )
    reactivation_evidence_ref = vref(
        reactivation_evidence["evidence_id"],
        reactivation_evidence["schema"],
        reactivation_evidence_sha,
        reactivation_evidence_filename,
    )

    _reactivated_assignment, reactivated_assignment_ref = (
        add_distribution_assignment(
            "PUBLISH_REACTIVATED_HEAD",
            "reactivated",
            "2026-06-01T13:40:00Z",
            distribution_withdrawn_ref,
            incident_id,
            incident_ref,
        )
    )
    _distribution_reactivated, distribution_reactivated_ref, distribution_reactivated_sha = (
        add_distribution_control(
            3,
            distribution_withdrawn_ref,
            reactivated_assignment_ref,
            "ACTIVE",
            incident_ref,
            "Synthetic incident resolution and compatibility evidence permit reactivation.",
            active_client_behavior,
            active_producer_behavior,
            active_consumer_behavior,
            active_compatibility_window,
            {
                "condition": (
                    "Reactivate only from the withdrawn head using current evidence."
                ),
                "not_before": "2026-06-01T13:40:00Z",
                "required_evidence": (
                    "Exact incident resolution and PASS reactivation evidence."
                ),
                "recovery_evidence_refs": [
                    copy.deepcopy(resolution_ref),
                    copy.deepcopy(reactivation_evidence_ref),
                ],
            },
            "2026-06-01T13:41:00Z",
        )
    )
    reactivated_incident_lineage = {
        "lineage_kind": "BOUND_INCIDENT",
        "incident_ref": copy.deepcopy(incident_ref),
        "incident_resolution_ref": copy.deepcopy(resolution_ref),
        "reactivation_evidence_refs": [
            copy.deepcopy(reactivation_evidence_ref)
        ],
    }
    reactivated_transition_ref = add_transition_receipt(
        "reactivated",
        "REACTIVATE",
        reactivated_assignment_ref,
        reactivated_incident_lineage,
        distribution_withdrawn_ref,
        distribution_reactivated_ref,
        withdrawn_transition_ref,
        "2026-06-01T13:41:20Z",
        "2026-06-01T13:41:40Z",
        "2026-06-01T13:42:00Z",
    )

    legacy_m5_context_ref = artifact_ref(
        "fixture-binding-context-m5",
        "ylx.binding-context.v1",
        corpus.digests["valid/binding-context-m5.json"],
        "contracts/fixtures/governance-models/valid/binding-context-m5.json",
    )
    revoked_projection_assignment_v1 = {
        "schema": "ylx.release-operation-assignment.v1",
        "assignment_id": "fixture-release-operation-assignment-v1-projection",
        "revision": 1,
        "predecessor_assignment_ref": None,
        "authorized_operation": "PROJECT_RELEASE_RESULTS",
        "actor_identity": copy.deepcopy(distribution_operator),
        "operation_scope": {
            "scope_kind": "RELEASE_RESULT_PROJECTION",
            "acceptance_registry_ref": {
                "ref_id": "fixture-acceptance-registry-v1-assignment",
                "authority_kind": "contract-package",
                "locator": "docs/acceptance-requirements.yaml",
                "sha256": sha("fixture-acceptance-registry-v1-assignment"),
            },
            "m5_binding_context_ref": legacy_m5_context_ref,
            "selected_issue_head": copy.deepcopy(current_issue_head),
            "selected_result_source_root_sha256": sha(
                "fixture-revoked-v1-result-source-root"
            ),
            "selected_evidence_binding_root_sha256": sha(
                "fixture-revoked-v1-evidence-binding-root"
            ),
            "selected_approved_na_root_sha256": sha(
                "fixture-revoked-v1-approved-na-root"
            ),
            "projection_input_set_sha256": sha(
                "fixture-revoked-v1-projection-input"
            ),
            "projection_output_slot": (
                "release-result-projections/fixture-revoked-v1"
            ),
            "create_if_absent_only": True,
        },
        "effective_from": "2026-06-01T13:43:00Z",
        "expires_at": NOT_AFTER,
        "assignment_status": "REVOKED",
        "issued_by": [
            owner_approval("release-owner", "2026-06-01T13:42:30Z"),
            owner_approval("security-owner", "2026-06-01T13:42:30Z"),
        ],
        "artifact_metadata": metadata(),
    }
    corpus.add(
        "VALID-RELEASE-OPERATION-ASSIGNMENT-V1-REVOKED-01",
        "release-operation-assignment-v1-projection-revoked.json",
        "release-operation-assignment-v1.schema.json",
        revoked_projection_assignment_v1,
    )

    return {
        "attempt_id": attempt_id,
        "attempt_terminal_slot": attempt_terminal_slot,
        "attempt_input": attempt_input,
        "attempt_input_ref": attempt_input_ref,
        "pre_release": pre_release,
        "pre_release_ref": pre_release_ref,
        "pre_release_sha": pre_release_sha,
        "readiness_ref": readiness_ref,
        "distribution_drill_ref": distribution_drill_ref,
        "attestation_refs": attestation_refs,
        "attestation_digests": attestation_digests,
        "signature_refs": signature_refs,
        "signature_digests": signature_digests,
        "role_assignment_refs": role_assignment_refs,
        "signing_policy": signing_policy,
        "signing_policy_ref": signing_policy_ref,
        "signing_policy_sha": signing_policy_sha,
        "key_head_ref": key_head_ref,
        "quorum_policy_ref": quorum_policy_ref,
        "quorum_policy_sha": quorum_policy_sha,
        "promotion_plan": promotion_plan,
        "promotion_plan_ref": promotion_plan_ref,
        "promotion_plan_sha": promotion_plan_sha,
        "promotion_assignment_ref": promotion_assignment_ref,
        "checkpoint_refs": checkpoint_refs,
        "checkpoint_digests": checkpoint_digests,
        "checkpoint_8_ref": checkpoint_8_ref,
        "final_manifest": final_manifest,
        "final_manifest_ref": final_manifest_ref,
        "final_manifest_sha": final_manifest_sha,
        "finalized_terminal_reference": finalized_terminal_reference,
        "terminal_readback_ref": terminal_readback_ref,
        "terminal_readback_sha": terminal_readback_sha,
        "distribution_active_ref": distribution_active_ref,
        "distribution_active_sha": distribution_active_sha,
        "distribution_withdrawn_ref": distribution_withdrawn_ref,
        "distribution_withdrawn_sha": distribution_withdrawn_sha,
        "distribution_reactivated_ref": distribution_reactivated_ref,
        "distribution_reactivated_sha": distribution_reactivated_sha,
        "incident_ref": incident_ref,
        "resolution_ref": resolution_ref,
        "reactivation_evidence_ref": reactivation_evidence_ref,
        "reactivated_transition_ref": reactivated_transition_ref,
        "planned_operator": planned_operator,
        "rc_target": rc_target,
        "planning_owner_authority_ref": planning_owner_authority_ref,
        "owner_approval": owner_approval,
        "vref": vref,
    }


def build_issue_and_release(corpus: Corpus, state: dict[str, Any]) -> None:
    """Build the immutable issue head and the no-cycle D-025 release chain."""
    def write_support(path: str, raw: bytes, purpose: str) -> str:
        destination = FIXTURE_ROOT / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        digest = sha(raw)
        corpus.support_entries.append(
            {
                "path": path,
                "sha256": digest,
                "exact_byte_length": len(raw),
                "purpose": purpose,
                "test_only": True,
                "notice": NOTICE,
            }
        )
        return digest

    def issue_bytes(version: str) -> tuple[bytes, dict[str, Any]]:
        status = "OPEN" if version == "r1" else "CLOSED"
        prefix = (
            NOTICE
            + "\n\n# Synthetic issue register "
            + version
            + "\n\n## Overview\n"
        ).encode()
        overview = (
            f"O-1 | {status} | fixture-release-owner-slot | synthetic-only\n"
        ).encode()
        separator = b"\n"
        body = (
            b"## O-1\n"
            + b"\n#### Canonical machine fields\n\n"
            + b"| Status | Severity | Owner slot | Component subrole | Target | Blocks gate |\n"
            + b"|---|---|---|---|---|---|\n"
            + (
                f"| `{status}` | `S1` | `release-owner` | "
                "synthetic-only | `M5` | `M5` |\n"
            ).encode()
            + b"\n"
            + f"Revision marker: {version}\n".encode()
            + b"This synthetic issue is not evidence and cannot close a gate.\n"
        )
        raw = prefix + overview + separator + body
        overview_start = len(prefix)
        overview_end = overview_start + len(overview)
        body_start = overview_end + len(separator)
        body_end = len(raw)
        slices = {
            "O-1": {
                "overview_start_byte": overview_start,
                "overview_end_byte": overview_end,
                "overview_sha256": sha(raw[overview_start:overview_end]),
                "body_start_byte": body_start,
                "body_end_byte": body_end,
                "body_sha256": sha(raw[body_start:body_end]),
            }
        }
        return raw, slices

    issue_r1_raw, issue_r1_slices = issue_bytes("r1")
    issue_r1_source_sha = write_support(
        "support/issue-register-source-r1.md",
        issue_r1_raw,
        "Exact UTF-8/LF synthetic issue-register source for revision 1.",
    )
    issue_r1_archive_sha = write_support(
        "support/issue-register-archive-r1.md",
        issue_r1_raw,
        "Immutable exact-byte archive of synthetic issue-register revision 1.",
    )
    issue_head_r1 = {
        "schema": "ylx.issue-register-head.v1",
        "issue_register_revision": 1,
        "predecessor_revision": None,
        "predecessor_head_artifact_sha256": None,
        "source_artifact_path": "contracts/fixtures/governance-models/support/issue-register-source-r1.md",
        "issue_register_sha256": issue_r1_source_sha,
        "archived_source_path": "contracts/fixtures/governance-models/support/issue-register-archive-r1.md",
        "archived_source_sha256": issue_r1_archive_sha,
        "selector_version": "issue-register-gate-selector.v1",
        "overview_cardinality": 1,
        "issue_slices_by_id": issue_r1_slices,
        "published_at": "2026-05-30T10:00:00Z",
        "publisher_role_slot": "release-owner",
        "approvals": [approval("release-owner")],
    }
    issue_head_r1_sha = corpus.add(
        "VALID-ISSUE-REGISTER-HEAD-R1-01",
        "issue-register-head-r1.json",
        "issue-register-head-v1.schema.json",
        issue_head_r1,
    )

    issue_raw, issue_slices = issue_bytes("r2")
    issue_source_sha = write_support(
        "support/issue-register-source.md",
        issue_raw,
        "Exact UTF-8/LF synthetic issue-register source for the current revision.",
    )
    issue_archive_sha = write_support(
        "support/issue-register-archive.md",
        issue_raw,
        "Immutable exact-byte archive of the current synthetic issue register.",
    )
    issue_head_value = {
        "schema": "ylx.issue-register-head.v1",
        "issue_register_revision": 2,
        "predecessor_revision": 1,
        "predecessor_head_artifact_sha256": issue_head_r1_sha,
        "source_artifact_path": "contracts/fixtures/governance-models/support/issue-register-source.md",
        "issue_register_sha256": issue_source_sha,
        "archived_source_path": "contracts/fixtures/governance-models/support/issue-register-archive.md",
        "archived_source_sha256": issue_archive_sha,
        "selector_version": "issue-register-gate-selector.v1",
        "overview_cardinality": 1,
        "issue_slices_by_id": issue_slices,
        "published_at": "2026-06-01T12:44:00Z",
        "publisher_role_slot": "release-owner",
        "approvals": [approval("release-owner")],
    }
    issue_head_sha = corpus.add(
        "VALID-ISSUE-REGISTER-HEAD-01",
        "issue-register-head.json",
        "issue-register-head-v1.schema.json",
        issue_head_value,
    )
    current_issue_head = {
        "artifact_path": "valid/issue-register-head.json",
        "revision": 2,
        "head_artifact_sha256": issue_head_sha,
        "register_sha256": issue_source_sha,
        "selector_version": "issue-register-gate-selector.v1",
        "overview_cardinality": 1,
    }

    m5_context = {
        "schema": "ylx.binding-context.v1",
        "context_id": "fixture-binding-context-m5",
        "stage": "M5",
        "created_at": STAMP,
        "owner_role": "release-owner",
        "reviewer_role": "qa-evidence-owner",
        "lineage": lineage(),
        "body": {
            "candidate_id": state["candidate_id"],
            "release_bundle_sha256": sha("fixture-release-bundle"),
            "production_binding_sha256": sha("fixture-production-binding"),
            "contract_release_sha256": state["contract_release_sha"],
            "product_contract_sha256": state["product_contract_sha"],
            "qualification_governance_contract_sha256": state["qualification_contract_sha"],
            "effective_m4_binding_context_sha256": state["m4_context_sha"],
        },
        "artifact_metadata": metadata(),
    }
    m5_context_sha = corpus.add(
        "VALID-BINDING-CONTEXT-M5-01",
        "binding-context-m5.json",
        "binding-context-v1.schema.json",
        m5_context,
    )
    m5_context_ref = context_ref("fixture-binding-context-m5", m5_context_sha, "M5")
    requirement_ids = state["requirement_ids"]
    issue_verdict_state = build_issue_gate_verdict_fixtures(
        corpus,
        state,
        issue_head_r1=issue_head_r1,
        issue_head_r1_sha=issue_head_r1_sha,
        issue_r1_source_sha=issue_r1_source_sha,
        issue_r1_slices=issue_r1_slices,
        issue_head=issue_head_value,
        issue_head_sha=issue_head_sha,
        issue_source_sha=issue_source_sha,
        current_issue_head=current_issue_head,
    )
    state["issue_verdicts"] = issue_verdict_state
    issue_reconciliation_sha = sha("pending-exact-issue-reconciliation")
    projection_state = build_release_result_projection(
        corpus,
        state,
        m5_context_ref,
        current_issue_head,
        issue_reconciliation_sha,
    )
    projection_v2_state = build_context_lineage_and_projection_v2(
        corpus,
        state,
        current_issue_head,
        issue_reconciliation_sha,
    )
    issue_reconciliation_sha = projection_v2_state["issue_reconciliation_sha"]
    legacy_projection = projection_state["projection"]
    legacy_projection["issue_reconciliation_set_sha256"] = issue_reconciliation_sha
    legacy_projection["created_at"] = "2026-06-01T13:05:00Z"
    legacy_projection_sha = corpus.replace(
        "release-result-projection.json", legacy_projection
    )
    legacy_projection_ref = artifact_ref(
        legacy_projection["projection_id"],
        legacy_projection["schema"],
        legacy_projection_sha,
        "contracts/fixtures/governance-models/valid/release-result-projection.json",
        1,
    )
    legacy_projection_locator = corpus.values[
        "valid/content-addressed-locator-readback-release-result-projection.json"
    ]
    legacy_projection_locator.update(
        {
            "artifact_sha256": legacy_projection_sha,
            "canonical_path": (
                "release-result-projection/"
                f"{legacy_projection_sha}--release-result-projection.json"
            ),
            "exact_byte_length": corpus.byte_lengths[
                "valid/release-result-projection.json"
            ],
            "published_at": "2026-06-01T13:06:00Z",
            "readback_sha256": legacy_projection_sha,
            "readback_byte_length": corpus.byte_lengths[
                "valid/release-result-projection.json"
            ],
            "readback_at": "2026-06-01T13:07:00Z",
        }
    )
    legacy_projection_locator_sha = corpus.replace(
        "content-addressed-locator-readback-release-result-projection.json",
        legacy_projection_locator,
    )
    projection_state.update(
        {
            "projection_sha256": legacy_projection_sha,
            "projection_ref": legacy_projection_ref,
            "projection_locator_sha256": legacy_projection_locator_sha,
        }
    )
    state["projection_v2"] = projection_v2_state
    state["measurement_queue"] = build_measurement_queue_fixture(
        corpus,
        state["registry"],
        state["history"],
        state["measurement_threshold"]["threshold_ref"],
        state["measurement_holdout"],
        projection_v2_state["result_refs"]["M0-MEAS-01"],
    )
    release_result_projection_ref = projection_state["projection_ref"]
    release_result_projection_sha = projection_state["projection_sha256"]

    component_acceptance_map = {
        boundary: sha(f"fixture-component-acceptance:{boundary}") for boundary in BOUNDARIES
    }
    consumer_boundary_registry_sha = state["consumer_boundary_registry_sha"]
    consumer_acceptance_set_sha = sha("fixture-consumer-boundary-acceptance-set")

    attestation_digests: dict[str, str] = {}
    attestation_values: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        value: dict[str, Any] = {
            "schema": "ylx.domain-attestation.v1",
            "attestation_id": f"fixture-domain-attestation-{role}",
            "revision": 1,
            "predecessor_sha256": None,
            "created_at": STAMP,
            "artifact_path": f"contracts/fixtures/governance-models/valid/domain-attestation-{role}.json",
            "artifact_metadata": metadata(),
            "role_id": role,
            "attesting_identity": {
                "person_id": f"fixture-{role}-attestor",
                "natural_person_identity_sha256": sha(f"identity:{role}-attestor"),
                "identity_authority_ref": authority("fixture-identity-authority"),
            },
            "role_assignment_ref": authority(f"fixture-{role}-domain-assignment"),
            "subject_refs": [m5_context_ref, release_result_projection_ref],
            "evidence_refs": [artifact_ref(f"fixture-{role}-evidence")],
            "decision_refs": [source(f"fixture-{role}-decision")],
            "current_issue_head": current_issue_head,
            "binding_context_ref": m5_context_ref,
            "shared_context_refs": [
                context_ref(
                    "fixture-binding-context-m4-target", state["m4_context_sha"], "M4-target"
                )
            ],
            "conflict_control_ref": None,
            "attestation_outcome": "PASS",
            "attested_at": STAMP,
        }
        if role == "consumer-owner":
            value["consumer_bindings"] = {
                "consumer_boundary_registry_sha256": consumer_boundary_registry_sha,
                "consumer_boundary_acceptance_set_sha256": consumer_acceptance_set_sha,
                "component_acceptance_record_sha256_by_boundary": component_acceptance_map,
            }
        attestation_digests[role] = corpus.add(
            f"VALID-DOMAIN-ATTESTATION-{role.upper()}-01",
            f"domain-attestation-{role}.json",
            "domain-attestation-v1.schema.json",
            value,
        )
        attestation_values[role] = value

    policy_approvals = [approval("contract-owner"), approval("security-owner")]
    signing_policy = {
        "schema": "ylx.m5-signing-policy.v1",
        "policy_id": "fixture-m5-signing-policy-r1",
        "revision": 1,
        "predecessor_policy_sha256": None,
        "canonicalization": "RFC8785-JSON-UTF8",
        "digest_algorithm": "SHA-256",
        "signature_algorithm": "Ed25519",
        "public_key_encoding": "32-byte-raw-Ed25519",
        "signature_encoding": "base64",
        "signature_domain_template": "ylx.release-closure.quorum.v1/<role_slot>",
        "signature_message_rule": "ASCII_DOMAIN || 0x00 || RFC8785_SIGNED_PAYLOAD_JSON_BYTES",
        "minimum_key_validity_horizon_seconds": 86400,
        "valid_at_signature_rule": "SIGNED_AT_WITHIN_NOT_BEFORE_AND_NOT_AFTER",
        "normal_post_signature_expiry_rule": "DOES_NOT_INVALIDATE_VALID_SIGNATURE",
        "retroactive_revocation_rule": "INVALID_ONLY_WHEN_EFFECTIVE_AT_OR_BEFORE_SIGNED_AT",
        "published_at": "2026-05-01T00:00:00Z",
        "approvals": policy_approvals,
    }
    signing_policy_sha = corpus.add(
        "VALID-M5-SIGNING-POLICY-01",
        "m5-signing-policy.json",
        "m5-signing-policy-v1.schema.json",
        signing_policy,
    )
    quorum_policy = {
        "schema": "ylx.release-quorum-policy.v1",
        "policy_id": "fixture-release-quorum-policy-r1",
        "revision": 1,
        "predecessor_policy_sha256": None,
        "mandatory_role_slots": QUORUM_ROLES,
        "distinct_natural_person_count": 4,
        "signature_domain_template": "ylx.release-closure.quorum.v1/<role_slot>",
        "signed_artifact_schema": "ylx.pre-release-closure.v1",
        "qa_independence": {
            "not_result_map_producer": True,
            "not_promotion_operator": True,
        },
        "delegation_rule": "PREAPPROVED_DIRECT_PREDECESSOR_ASSIGNMENT_ONLY",
        "freshness_checkpoints": FRESHNESS_CHECKPOINTS,
        "freshness_checkpoint_rule": FRESHNESS_CHECKPOINT_RULE,
        "terminal_drift_rule": TERMINAL_DRIFT_RULE,
        "published_at": "2026-05-01T00:00:00Z",
        "approvals": policy_approvals,
    }
    quorum_policy_sha = corpus.add(
        "VALID-RELEASE-QUORUM-POLICY-01",
        "release-quorum-policy.json",
        "release-quorum-policy-v1.schema.json",
        quorum_policy,
    )

    people_by_role = {
        "release-owner": "fixture-release-owner-person",
        "contract-owner": "fixture-contract-owner-person",
        "security-owner": "fixture-security-owner-person",
        "qa-evidence-owner": "fixture-qa-evidence-owner-person",
        "build-platform-owner": "fixture-ga-operator-person",
    }
    private_keys: dict[str, Ed25519PrivateKey] = {}
    public_key_raw_by_role: dict[str, bytes] = {}
    fingerprint_by_role: dict[str, str] = {}
    for role in people_by_role:
        seed = hashlib.sha256(f"YLX SYNTHETIC TEST KEY ONLY:{role}".encode("ascii")).digest()
        private_key = Ed25519PrivateKey.from_private_bytes(seed)
        public_raw = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        private_keys[role] = private_key
        public_key_raw_by_role[role] = public_raw
        fingerprint_by_role[role] = sha(public_raw)

    assignment_digests: dict[str, str] = {}
    assignment_values: dict[str, Any] = {}
    identity_digest_by_person = {
        person_id: identity_ref["artifact_sha256"]
        for person_id, identity_ref in state["foundation"][
            "identity_refs_by_person"
        ].items()
    }
    identity_digest_by_person["fixture-ga-operator-person"] = corpus.digests[
        "valid/natural-person-identity-ga-operator.json"
    ]
    for role, person_id in people_by_role.items():
        assignment_value = {
            "schema": "ylx.role-signing-key-assignment.v1",
            "assignment_id": f"fixture-signing-assignment-{role}-r1",
            "revision": 1,
            "predecessor_assignment_sha256": None,
            "role_slot": role,
            "person_id": person_id,
            "natural_person_identity_sha256": identity_digest_by_person[
                person_id
            ],
            "signing_key_fingerprint": fingerprint_by_role[role],
            "effective_from": VALID_FROM,
            "not_after": NOT_AFTER,
            "assignment_status": "ACTIVE",
            "is_delegate": False,
            "delegation_approval_ref": None,
            "identity_authority_ref": authority("fixture-identity-authority"),
            "published_at": "2026-05-15T00:00:00Z",
            "approvals": [approval("contract-owner"), approval("security-owner")],
        }
        filename = f"role-signing-key-assignment-{role}.json"
        assignment_digests[role] = corpus.add(
            f"VALID-ROLE-SIGNING-KEY-ASSIGNMENT-{role.upper()}-01",
            filename,
            "role-signing-key-assignment-v1.schema.json",
            assignment_value,
        )
        assignment_values[role] = assignment_value

    key_head = {
        "schema": "ylx.signing-key-validity-revocation-head.v1",
        "head_id": "fixture-signing-key-head-r1",
        "revision": 1,
        "predecessor_head_sha256": None,
        "effective_at": "2026-05-31T00:00:00Z",
        "keys_by_fingerprint": {
            fingerprint_by_role[role]: {
                "key_id": f"fixture-key-{role}",
                "person_id": people_by_role[role],
                "public_key_base64": base64.b64encode(public_key_raw_by_role[role]).decode("ascii"),
                "valid_from": VALID_FROM,
                "not_after": NOT_AFTER,
                "status": "VALID",
                "revocation_or_compromise_effective_at": None,
                "reason": None,
            }
            for role in people_by_role
        },
        "published_at": "2026-05-31T00:01:00Z",
        "approvals": [approval("contract-owner"), approval("security-owner")],
    }
    for boundary in BOUNDARIES:
        maintainer_person_id = f"fixture-maintainer-person-{boundary}"
        maintainer_seed = hashlib.sha256(
            f"YLX SYNTHETIC TEST MAINTAINER KEY ONLY:{boundary}".encode("ascii")
        ).digest()
        maintainer_public_raw = (
            Ed25519PrivateKey.from_private_bytes(maintainer_seed)
            .public_key()
            .public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        )
        maintainer_fingerprint = sha(maintainer_public_raw)
        key_head["keys_by_fingerprint"][maintainer_fingerprint] = {
            "key_id": f"fixture-maintainer-key-{boundary}",
            "person_id": maintainer_person_id,
            "public_key_base64": base64.b64encode(maintainer_public_raw).decode("ascii"),
            "valid_from": VALID_FROM,
            "not_after": NOT_AFTER,
            "status": "VALID",
            "revocation_or_compromise_effective_at": None,
            "reason": None,
        }
    key_head_sha = corpus.add(
        "VALID-SIGNING-KEY-VALIDITY-REVOCATION-HEAD-01",
        "signing-key-validity-revocation-head.json",
        "signing-key-validity-revocation-head-v1.schema.json",
        key_head,
    )

    consumer_completion = build_consumer_completion(
        corpus=corpus,
        state=state,
        m5_context_ref=m5_context_ref,
        signing_policy_sha=signing_policy_sha,
        key_head_sha=key_head_sha,
    )
    component_acceptance_map = consumer_completion["component_acceptance_map"]
    consumer_acceptance_set_sha = consumer_completion["acceptance_set_sha256"]
    consumer_attestation = attestation_values["consumer-owner"]
    consumer_attestation["consumer_bindings"] = {
        "consumer_boundary_registry_sha256": consumer_boundary_registry_sha,
        "consumer_boundary_acceptance_set_sha256": consumer_acceptance_set_sha,
        "component_acceptance_record_sha256_by_boundary": component_acceptance_map,
    }
    attestation_digests["consumer-owner"] = corpus.replace(
        "domain-attestation-consumer-owner.json", consumer_attestation
    )

    operator_role = "build-platform-owner"
    planned_operator = {
        "person_id": people_by_role[operator_role],
        "role_assignment_artifact_sha256": assignment_digests[operator_role],
        "role_assignment_revision": 1,
    }
    rc_version = "0.5.0-rc.1"
    rc_commit = "1" * 40
    rc_artifact_sha = sha("fixture-rc-artifact")
    ga_plan = {
        "schema": "ylx.ga-promotion-plan.v1",
        "plan_id": "fixture-ga-promotion-plan-r1",
        "revision": 1,
        "predecessor_plan_sha256": None,
        "binding_context_ref": m5_context_ref,
        "rc_version": rc_version,
        "rc_commit": rc_commit,
        "rc_artifact_sha256": rc_artifact_sha,
        "canonical_remote_id": "fixture-origin",
        "ga_ref": "refs/tags/v0.5.0",
        "ga_channel": "channels/ga/0.5",
        "planned_promotion_operator": planned_operator,
        "create_if_absent": True,
        "existing_exact_target_is_idempotent": True,
        "rebuild_allowed": False,
        "overwrite_allowed": False,
        "force_push_allowed": False,
        "created_at": "2026-05-31T01:00:00Z",
        "approvals": [approval("release-owner"), approval("contract-owner")],
    }
    ga_plan_sha = corpus.add(
        "VALID-GA-PROMOTION-PLAN-01",
        "ga-promotion-plan.json",
        "ga-promotion-plan-v1.schema.json",
        ga_plan,
    )

    assert "M5-MATRIX-COMPLETE-01" in requirement_ids
    assert "M5-SIGNOFF-01" in requirement_ids
    assert "M4-ISSUES-01" in requirement_ids
    current_result_map = dict(projection_state["core_result_map"])
    current_result_map[
        "M5-MATRIX-COMPLETE-01"
    ] = "PASS_DERIVED_FROM_PRE_RELEASE_VALIDITY"
    current_result_map["M5-SIGNOFF-01"] = "PENDING_CLOSURE"
    proposed_final_result_map = dict(current_result_map)
    proposed_final_result_map[
        "M5-SIGNOFF-01"
    ] = "PASS_DERIVED_FROM_FINAL_MANIFEST_VALIDITY"
    pre_release = {
        "schema": "ylx.pre-release-closure.v1",
        "closure_id": "fixture-release-closure-001",
        "revision": 1,
        "predecessor_closure_sha256": None,
        "binding_context_ref": m5_context_ref,
        "contract_release_sha256": state["contract_release_sha"],
        "product_contract_sha256": state["product_contract_sha"],
        "qualification_governance_contract_sha256": state["qualification_contract_sha"],
        "registry_sha256": state["registry_sha"],
        "registry_id_set_sha256": sha(
            "".join(f"{requirement_id}\n" for requirement_id in sorted(set(requirement_ids)))
        ),
        "registry_cardinality": 173,
        "acceptance_sha256": sha((REPO_ROOT / "docs" / "ACCEPTANCE.md").read_bytes()),
        "system_requirement_mapping_artifact_sha256": sha(
            (REPO_ROOT / "docs" / "system-requirement-mapping.yaml").read_bytes()
        ),
        "system_requirement_mapping_semantic_sha256": sha(
            canonical_bytes(
                yaml.safe_load(
                    (REPO_ROOT / "docs" / "system-requirement-mapping.yaml").read_text()
                )
            )
        ),
        "release_result_projection_ref": release_result_projection_ref,
        "release_result_projection_sha256": release_result_projection_sha,
        "current_result_map": current_result_map,
        "proposed_final_result_map": proposed_final_result_map,
        "issue_head": current_issue_head,
        "issue_reconciliation_set_sha256": issue_reconciliation_sha,
        "domain_attestation_sha256_by_role_slot": attestation_digests,
        "consumer_boundary_registry_sha256": consumer_boundary_registry_sha,
        "consumer_boundary_acceptance_set_sha256": consumer_acceptance_set_sha,
        "component_acceptance_record_sha256_by_boundary": component_acceptance_map,
        "signing_policy_sha256": signing_policy_sha,
        "key_validity_revocation_head_sha256": key_head_sha,
        "quorum_policy_sha256": quorum_policy_sha,
        "ga_promotion_plan_sha256": ga_plan_sha,
        "planned_promotion_operator": planned_operator,
        "created_at": "2026-06-01T10:00:00Z",
    }
    pre_release_sha = corpus.add(
        "VALID-PRE-RELEASE-CLOSURE-01",
        "pre-release-closure.json",
        "pre-release-closure-v1.schema.json",
        pre_release,
    )
    pre_release_ref = artifact_ref(
        "fixture-release-closure-001",
        "ylx.pre-release-closure.v1",
        pre_release_sha,
        "contracts/fixtures/governance-models/valid/pre-release-closure.json",
    )
    pre_release_path = "valid/pre-release-closure.json"
    pre_release_locator = {
        "schema": "ylx.content-addressed-locator-readback.v1",
        "locator_id": "fixture-pre-release-closure-locator",
        "artifact_schema": "ylx.pre-release-closure.v1",
        "artifact_id": "fixture-release-closure-001",
        "artifact_sha256": pre_release_sha,
        "canonical_path": f"pre-release-closure/{pre_release_sha}--pre-release-closure.json",
        "attempt_terminal_slot": None,
        "terminal_slot_record": None,
        "terminal_slot_create_if_absent": None,
        "terminal_slot_recorded_at": None,
        "terminal_slot_readback_record": None,
        "terminal_slot_readback_at": None,
        "terminal_slot_readback_result": None,
        "freshness_validation": None,
        "exact_byte_length": corpus.byte_lengths[pre_release_path],
        "canonical_encoding": "RFC8785-JSON-UTF8",
        "create_if_absent": True,
        "existing_identical_is_idempotent": True,
        "different_digest_is_equivocation": True,
        "durability": {
            "temporary_exact_bytes_fsynced": True,
            "parent_fsynced_before_create": True,
            "atomic_unique_create": True,
            "parent_fsynced_after_create": True,
        },
        "published_at": "2026-06-01T10:01:00Z",
        "readback_sha256": pre_release_sha,
        "readback_byte_length": corpus.byte_lengths[pre_release_path],
        "readback_at": "2026-06-01T10:02:00Z",
        "readback_result": "EXACT_PATH_DIGEST_AND_BYTES_MATCH",
    }
    pre_release_locator_sha = corpus.add(
        "VALID-CONTENT-ADDRESSED-PRE-RELEASE-LOCATOR-READBACK-01",
        "content-addressed-locator-readback-pre-release.json",
        "content-addressed-locator-readback-v1.schema.json",
        pre_release_locator,
    )

    signature_digests: dict[str, str] = {}
    signature_public_keys: dict[str, Any] = {}
    for role in QUORUM_ROLES:
        person_id = people_by_role[role]
        assignment = assignment_values[role]
        signed_payload = {
            "payload_schema": "ylx.release-quorum-signature.signed-payload.v1",
            "signature_domain": f"ylx.release-closure.quorum.v1/{role}",
            "pre_release_closure_ref": pre_release_ref,
            "pre_release_closure_sha256": pre_release_sha,
            "role_slot": role,
            "person_id": person_id,
            "signer_identity": {
                "natural_person_identity_sha256": assignment[
                    "natural_person_identity_sha256"
                ],
                "identity_authority_ref": assignment["identity_authority_ref"],
            },
            "signing_key_fingerprint": fingerprint_by_role[role],
            "key_validity_at_signature": {
                "not_before": VALID_FROM,
                "not_after": NOT_AFTER,
                "evaluated_revocation_head_sha256": key_head_sha,
                "status": "VALID_AT_SIGNED_AT",
                "required_remaining_validity_seconds": 86400,
                "validity_horizon_satisfied": True,
                "post_signature_expiry_rule": "NORMAL_EXPIRY_DOES_NOT_INVALIDATE_A_SIGNATURE_VALID_AT_SIGNED_AT",
                "retroactive_compromise_rule": "ONLY_REVOCATION_OR_COMPROMISE_EFFECTIVE_AT_OR_BEFORE_SIGNED_AT_INVALIDATES",
            },
            "role_assignment_ref": artifact_ref(
                assignment["assignment_id"],
                "ylx.role-signing-key-assignment.v1",
                assignment_digests[role],
                f"contracts/fixtures/governance-models/valid/role-signing-key-assignment-{role}.json",
            ),
            "role_assignment_revision": 1,
            "signing_policy_sha256": signing_policy_sha,
            "key_validity_revocation_head_sha256": key_head_sha,
            "quorum_policy_sha256": quorum_policy_sha,
            "binding_context_ref": m5_context_ref,
            "fresh_issue_head": current_issue_head,
            "issue_reconciliation_set_sha256": issue_reconciliation_sha,
            "domain_attestation_sha256_by_role_slot": attestation_digests,
            "signed_at": STAMP,
        }
        domain = signed_payload["signature_domain"].encode("ascii")
        message = domain + b"\x00" + canonical_bytes(signed_payload)
        signature = private_keys[role].sign(message)
        signature_value = {
            "signed_payload": signed_payload,
            "signature_b64": base64.b64encode(signature).decode("ascii"),
        }
        signature_digests[role] = corpus.add(
            f"VALID-RELEASE-QUORUM-SIGNATURE-{role.upper()}-01",
            f"release-quorum-signature-{role}.json",
            "release-quorum-signature-v1.schema.json",
            signature_value,
        )
        signature_public_keys[role] = {
            "person_id": person_id,
            "public_key_base64": base64.b64encode(public_key_raw_by_role[role]).decode(
                "ascii"
            ),
            "fingerprint_sha256": fingerprint_by_role[role],
            "test_only": True,
            "notice": NOTICE,
        }

    attempt_id = "fixture-release-attempt-001"
    attempt_terminal_slot = f"release-attempt-terminals/{attempt_id}"
    role_assignment_map = {
        role: {
            "person_id": people_by_role[role],
            "assignment_artifact_sha256": assignment_digests[role],
            "assignment_revision": 1,
        }
        for role in QUORUM_ROLES
    }
    fence = {
        "schema": "ylx.release-publication-fence.v1",
        "attempt_id": attempt_id,
        "fence_authority_id": "fixture-fence-authority",
        "pre_release_closure_ref": pre_release_ref,
        "pre_release_closure_sha256": pre_release_sha,
        "domain_attestation_sha256_by_role_slot": attestation_digests,
        "quorum_signature_sha256_by_role_slot": signature_digests,
        "role_assignment_by_role_slot": role_assignment_map,
        "binding_context_ref": m5_context_ref,
        "contract_release_sha256": state["contract_release_sha"],
        "product_contract_sha256": state["product_contract_sha"],
        "qualification_governance_contract_sha256": state["qualification_contract_sha"],
        "fresh_issue_head": current_issue_head,
        "signing_policy_sha256": signing_policy_sha,
        "key_validity_revocation_head_sha256": key_head_sha,
        "quorum_policy_sha256": quorum_policy_sha,
        "ga_promotion_plan_ref": artifact_ref(
            "fixture-ga-promotion-plan-r1",
            "ylx.ga-promotion-plan.v1",
            ga_plan_sha,
            "contracts/fixtures/governance-models/valid/ga-promotion-plan.json",
        ),
        "ga_promotion_plan_sha256": ga_plan_sha,
        "planned_promotion_operator": planned_operator,
        "release_train": "fixture-release-train-0.5",
        "system_milestone": "0.5",
        "rc_version": rc_version,
        "rc_commit": rc_commit,
        "rc_artifact_sha256": rc_artifact_sha,
        "canonical_remote_id": "fixture-origin",
        "ga_ref": "refs/tags/v0.5.0",
        "ga_channel": "channels/ga/0.5",
        "canonical_ga_target": "fixture-origin/refs/tags/v0.5.0",
        "attempt_terminal_slot": attempt_terminal_slot,
        "initial_customer_visibility": "QUARANTINED",
        "required_key_validity_horizon_seconds": 86400,
        "acquired_at": "2026-06-01T12:10:00Z",
        "fence_semantics": {
            "acquisition": "CREATE_IF_ABSENT_SINGLE_ATTEMPT",
            "active_derivation": "TERMINAL_SLOT_ABSENT",
            "parallel_attempts": "FORBIDDEN",
            "ordinary_authority_successors": "BLOCKED_WHILE_ACTIVE",
            "emergency_revocation": "TERMINATE_ATTEMPT_AND_KEEP_GA_QUARANTINED",
            "ga_visibility": "QUARANTINED_UNTIL_VALID_ACTIVE_DISTRIBUTION_HEAD",
            "recovery": "SAME_IMMUTABLE_INPUTS_ONLY",
            "release_condition": "FINAL_MANIFEST_PAYLOAD_AND_FINALIZED_SLOT_REFERENCE_DURABLE_EXACT_READBACK",
            "missing_manifest_state": "RELEASE_NOT_COMPLETE",
            "orphan_final_payload_state": "FINAL_PAYLOAD_DURABLE_NOT_ACTIVATED",
            "finalized_state": "FINAL_REFERENCE_DURABLE",
            "aborted_state": "ABORTED_REFERENCE_DURABLE",
            "pre_finalized_cas_freshness_mismatch": "ABORTED_AND_QUARANTINED",
            "post_finalized_cas_pre_readback_freshness_mismatch": "IMMUTABLE_INVALID_TERMINAL_NO_PASS_NO_RELEASE_COMPLETE_NO_OVERWRITE_OR_REUSE",
        },
    }
    fence_sha = corpus.add(
        "VALID-RELEASE-PUBLICATION-FENCE-01",
        "release-publication-fence.json",
        "release-publication-fence-v1.schema.json",
        fence,
    )
    fence_ref = artifact_ref(
        attempt_id,
        "ylx.release-publication-fence.v1",
        fence_sha,
        "contracts/fixtures/governance-models/valid/release-publication-fence.json",
    )

    receipt_verified_at = "2026-06-01T12:20:00Z"
    remote_observation = {
        "canonical_remote_id": "fixture-origin",
        "ga_ref": "refs/tags/v0.5.0",
        "ga_channel": "channels/ga/0.5",
        "observed_ref_target_commit": rc_commit,
        "observed_artifact_sha256": rc_artifact_sha,
        "operation_result": "CREATED_EXACT",
        "observed_at": receipt_verified_at,
    }
    receipt = {
        "schema": "ylx.ga-promotion-receipt.v1",
        "attempt_id": attempt_id,
        "publication_fence_ref": fence_ref,
        "publication_fence_sha256": fence_sha,
        "ga_promotion_plan_ref": fence["ga_promotion_plan_ref"],
        "ga_promotion_plan_sha256": ga_plan_sha,
        "binding_context_ref": m5_context_ref,
        "rc_version": rc_version,
        "rc_commit": rc_commit,
        "rc_artifact_sha256": rc_artifact_sha,
        "canonical_remote_id": "fixture-origin",
        "ga_ref": "refs/tags/v0.5.0",
        "ga_channel": "channels/ga/0.5",
        "observed_ref_target_commit": rc_commit,
        "observed_artifact_sha256": rc_artifact_sha,
        "operation_result": "CREATED_EXACT",
        "verified_at": receipt_verified_at,
        "promotion_operator_person_id": planned_operator["person_id"],
        "promotion_operator_assignment_artifact_sha256": planned_operator[
            "role_assignment_artifact_sha256"
        ],
        "promotion_operator_assignment_revision": 1,
        "ga_visibility": "QUARANTINED_UNTIL_VALID_ACTIVE_DISTRIBUTION_HEAD",
        "remote_observation": remote_observation,
        "remote_observation_sha256": sha(canonical_bytes(remote_observation)),
    }
    receipt_sha = corpus.add(
        "VALID-GA-PROMOTION-RECEIPT-01",
        "ga-promotion-receipt.json",
        "ga-promotion-receipt-v1.schema.json",
        receipt,
    )
    receipt_ref = artifact_ref(
        attempt_id,
        "ylx.ga-promotion-receipt.v1",
        receipt_sha,
        "contracts/fixtures/governance-models/valid/ga-promotion-receipt.json",
    )

    final_manifest = {
        "schema": "ylx.release-closure-manifest.v1",
        "closure_id": "fixture-release-closure-001",
        "attempt_id": attempt_id,
        "attempt_terminal_slot": attempt_terminal_slot,
        "publication_fence_ref": fence_ref,
        "publication_fence_sha256": fence_sha,
        "pre_release_closure_ref": pre_release_ref,
        "pre_release_closure_sha256": pre_release_sha,
        "quorum_signature_sha256_by_role_slot": signature_digests,
        "ga_promotion_receipt_ref": receipt_ref,
        "ga_promotion_receipt_sha256": receipt_sha,
        "binding_context_ref": m5_context_ref,
        "contract_release_sha256": state["contract_release_sha"],
        "product_contract_sha256": state["product_contract_sha"],
        "qualification_governance_contract_sha256": state["qualification_contract_sha"],
        "fresh_issue_head": current_issue_head,
        "issue_reconciliation_set_sha256": issue_reconciliation_sha,
        "signing_policy_sha256": signing_policy_sha,
        "key_validity_revocation_head_sha256": key_head_sha,
        "quorum_policy_sha256": quorum_policy_sha,
        "final_result_map": proposed_final_result_map,
        "release_decision": "RELEASE_COMPLETE",
        "closed_at": "2026-06-01T12:30:00Z",
    }
    final_manifest_sha = corpus.add(
        "VALID-RELEASE-CLOSURE-MANIFEST-01",
        "release-closure-manifest.json",
        "release-closure-manifest-v1.schema.json",
        final_manifest,
    )
    final_path = "valid/release-closure-manifest.json"
    final_payload_locator = (
        f"release-closure/{final_manifest_sha}--release-closure-manifest.json"
    )
    final_terminal_slot_record = {
        "kind": "FINALIZED",
        "payload_locator": final_payload_locator,
        "payload_sha256": final_manifest_sha,
    }
    freshness_checkpoint_times = [
        "2026-06-01T12:05:00Z",
        fence["acquired_at"],
        "2026-06-01T12:15:00Z",
        receipt["verified_at"],
        "2026-06-01T12:31:00Z",
        "2026-06-01T12:32:00Z",
        "2026-06-01T12:33:00Z",
        "2026-06-01T12:34:00Z",
    ]
    locator = {
        "schema": "ylx.content-addressed-locator-readback.v1",
        "locator_id": "fixture-final-manifest-locator",
        "artifact_schema": "ylx.release-closure-manifest.v1",
        "artifact_id": "fixture-release-closure-001",
        "artifact_sha256": final_manifest_sha,
        "canonical_path": final_payload_locator,
        "attempt_terminal_slot": attempt_terminal_slot,
        "terminal_slot_record": final_terminal_slot_record,
        "terminal_slot_create_if_absent": True,
        "terminal_slot_recorded_at": "2026-06-01T12:33:00Z",
        "terminal_slot_readback_record": final_terminal_slot_record,
        "terminal_slot_readback_at": "2026-06-01T12:34:00Z",
        "terminal_slot_readback_result": "EXACT_TERMINAL_SLOT_RECORD_MATCH",
        "freshness_validation": None,
        "exact_byte_length": corpus.byte_lengths[final_path],
        "canonical_encoding": "RFC8785-JSON-UTF8",
        "create_if_absent": True,
        "existing_identical_is_idempotent": True,
        "different_digest_is_equivocation": True,
        "durability": {
            "temporary_exact_bytes_fsynced": True,
            "parent_fsynced_before_create": True,
            "atomic_unique_create": True,
            "parent_fsynced_after_create": True,
        },
        "published_at": "2026-06-01T12:31:00Z",
        "readback_sha256": final_manifest_sha,
        "readback_byte_length": corpus.byte_lengths[final_path],
        "readback_at": "2026-06-01T12:32:00Z",
        "readback_result": "EXACT_PATH_DIGEST_AND_BYTES_MATCH",
    }
    locator_without_freshness_validation = copy.deepcopy(locator)
    fence_bound_input_sha = terminal_freshness_input_set_sha256(
        fence,
        fence_sha,
        receipt_sha,
        final_manifest_sha,
        locator_without_freshness_validation,
    )
    locator["freshness_validation"] = {
        "fence_bound_input_set_sha256": fence_bound_input_sha,
        "checkpoints": [
            {
                "checkpoint": checkpoint,
                "fence_bound_input_set_sha256": fence_bound_input_sha,
                "result": "PASS",
                "checked_at": checked_at,
            }
            for checkpoint, checked_at in zip(
                FRESHNESS_CHECKPOINTS, freshness_checkpoint_times, strict=True
            )
        ],
    }
    locator_sha = corpus.add(
        "VALID-CONTENT-ADDRESSED-LOCATOR-READBACK-01",
        "content-addressed-locator-readback.json",
        "content-addressed-locator-readback-v1.schema.json",
        locator,
    )

    def sign_distribution_control(
        unsigned_control: dict[str, Any], signed_at: str
    ) -> dict[str, Any]:
        payload_raw = canonical_bytes(unsigned_control)
        payload_sha = sha(payload_raw)
        signed_control = copy.deepcopy(unsigned_control)
        signed_control["signatures_by_role_slot"] = {}
        for role in ("release-owner", "security-owner"):
            domain = f"ylx.release-distribution-control.v1/{role}"
            signature = private_keys[role].sign(
                domain.encode("ascii") + b"\x00" + payload_raw
            )
            signed_control["signatures_by_role_slot"][role] = {
                "role_slot": role,
                "person_id": people_by_role[role],
                "signing_key_fingerprint": fingerprint_by_role[role],
                "role_assignment_ref": {
                    "authority_id": assignment_values[role]["assignment_id"],
                    "revision": assignment_values[role]["revision"],
                    "artifact_path": (
                        "contracts/fixtures/governance-models/valid/"
                        f"role-signing-key-assignment-{role}.json"
                    ),
                    "artifact_sha256": assignment_digests[role],
                    "verified_at": signed_at,
                },
                "signed_at": signed_at,
                "signature_domain": domain,
                "signed_payload_sha256": payload_sha,
                "signature_b64": base64.b64encode(signature).decode("ascii"),
            }
        return signed_control

    finalized_manifest_ref = artifact_ref(
        final_manifest["closure_id"],
        "ylx.release-closure-manifest.v1",
        final_manifest_sha,
        "contracts/fixtures/governance-models/valid/release-closure-manifest.json",
        revision=None,
    )
    distribution_active_unsigned = {
        "schema": "ylx.release-distribution-control.v1",
        "revision": 1,
        "predecessor_control_sha256": None,
        "finalized_release_manifest_ref": finalized_manifest_ref,
        "finalized_terminal_reference": final_terminal_slot_record,
        "channel_scope": {
            "channel_ids": ["channels/ga/0.5"],
            "visibility_surfaces": [
                "CUSTOMER_DISCOVERY",
                "DEFAULT_INSTALLATION",
                "AUTOMATIC_UPGRADE",
                "PUBLIC_INDEXES",
            ],
        },
        "action": "ACTIVE",
        "incident_ref": None,
        "reason": None,
        "effective_at": "2026-06-01T12:35:00Z",
        "required_rto_seconds": 900,
        "client_behavior": {
            "customer_discovery": "VISIBLE",
            "default_installation": "ENABLED",
            "automatic_upgrade": "ENABLED",
            "public_indexes": "VISIBLE",
            "offline_client_notice": "The finalized synthetic release is available.",
        },
        "producer_behavior": {
            "write_disposition": "CONTINUE",
            "operator_instruction": "Continue the finalized synthetic producer.",
        },
        "consumer_behavior": {
            "read_disposition": "TARGET_ONLY",
            "existing_data_disposition": "PRESERVE_EXISTING_DATA",
        },
        "compatibility_window": {
            "required_data_scope": "fixture-required-data-scope-v1",
            "target_read_from": "2026-06-01T12:35:00Z",
            "legacy_read_until": None,
            "dual_read_required": False,
        },
        "recovery_condition": {
            "condition": "Publish a signed direct successor when distribution policy changes.",
            "not_before": None,
            "required_evidence": "Current signed distribution-control head.",
            "recovery_evidence_refs": [],
        },
        "redirect_target_finalized_manifest_ref": None,
        "signing_policy_sha256": signing_policy_sha,
        "created_at": "2026-06-01T12:35:00Z",
    }
    distribution_active = sign_distribution_control(
        distribution_active_unsigned, "2026-06-01T12:35:00Z"
    )
    distribution_active_sha = corpus.add(
        "VALID-RELEASE-DISTRIBUTION-CONTROL-ACTIVE-01",
        "release-distribution-control-active.json",
        "release-distribution-control-v1.schema.json",
        distribution_active,
    )

    incident_sha = corpus.add_support(
        "release-incident-001.json",
        {
            "incident_id": "fixture-release-incident-001",
            "summary": "Synthetic distribution withdrawal exercise.",
            "notice": NOTICE,
        },
        "Synthetic incident input for distribution-control withdrawal.",
    )
    distribution_withdrawn_unsigned = copy.deepcopy(distribution_active_unsigned)
    distribution_withdrawn_unsigned.update(
        {
            "revision": 2,
            "predecessor_control_sha256": distribution_active_sha,
            "action": "WITHDRAWN",
            "incident_ref": artifact_ref(
                "fixture-release-incident-001",
                "ylx.release-incident.v1",
                incident_sha,
                "contracts/fixtures/governance-models/support/release-incident-001.json",
            ),
            "reason": "Synthetic withdrawal exercises append-only distribution control.",
            "effective_at": "2026-06-01T12:40:00Z",
            "client_behavior": {
                "customer_discovery": "HIDDEN",
                "default_installation": "DISABLED",
                "automatic_upgrade": "DISABLED",
                "public_indexes": "HIDDEN",
                "offline_client_notice": "This synthetic release was withdrawn.",
            },
            "producer_behavior": {
                "write_disposition": "STOP",
                "operator_instruction": "Stop the synthetic producer; preserve existing data.",
            },
            "consumer_behavior": {
                "read_disposition": "DUAL_READ_REQUIRED",
                "existing_data_disposition": "PRESERVE_EXISTING_DATA",
            },
            "compatibility_window": {
                "required_data_scope": "fixture-required-data-scope-v1",
                "target_read_from": "2026-06-01T12:35:00Z",
                "legacy_read_until": "2027-01-01T00:00:00Z",
                "dual_read_required": True,
            },
            "recovery_condition": {
                "condition": "A signed successor may restore or redirect distribution.",
                "not_before": "2026-06-01T12:40:00Z",
                "required_evidence": "Incident closure and retained-data compatibility evidence.",
                "recovery_evidence_refs": [],
            },
            "created_at": "2026-06-01T12:39:00Z",
        }
    )
    distribution_withdrawn = sign_distribution_control(
        distribution_withdrawn_unsigned, "2026-06-01T12:39:00Z"
    )
    distribution_withdrawn_sha = corpus.add(
        "VALID-RELEASE-DISTRIBUTION-CONTROL-WITHDRAWN-01",
        "release-distribution-control-withdrawn.json",
        "release-distribution-control-v1.schema.json",
        distribution_withdrawn,
    )

    incident_resolution_sha = corpus.add_support(
        "release-incident-resolution-001.json",
        {
            "incident_id": "fixture-release-incident-001",
            "resolution_id": "fixture-release-incident-resolution-001",
            "status": "RESOLVED",
            "notice": NOTICE,
        },
        "Synthetic incident resolution for distribution reactivation.",
    )
    compatibility_evidence_sha = corpus.add_support(
        "release-reactivation-compatibility-evidence-001.json",
        {
            "evidence_id": "fixture-release-reactivation-compatibility-001",
            "required_data_scope": "fixture-required-data-scope-v1",
            "result": "PASS",
            "notice": NOTICE,
        },
        "Synthetic retained-data compatibility evidence for reactivation.",
    )
    distribution_reactivated_unsigned = copy.deepcopy(distribution_active_unsigned)
    distribution_reactivated_unsigned.update(
        {
            "revision": 3,
            "predecessor_control_sha256": distribution_withdrawn_sha,
            "incident_ref": artifact_ref(
                "fixture-release-incident-resolution-001",
                "ylx.release-incident-resolution.v1",
                incident_resolution_sha,
                (
                    "contracts/fixtures/governance-models/support/"
                    "release-incident-resolution-001.json"
                ),
            ),
            "reason": "Synthetic incident resolution authorizes controlled reactivation.",
            "effective_at": "2026-06-01T12:50:00Z",
            "consumer_behavior": {
                "read_disposition": "DUAL_READ_REQUIRED",
                "existing_data_disposition": "PRESERVE_EXISTING_DATA",
            },
            "compatibility_window": {
                "required_data_scope": "fixture-required-data-scope-v1",
                "target_read_from": "2026-06-01T12:35:00Z",
                "legacy_read_until": "2027-01-01T00:00:00Z",
                "dual_read_required": True,
            },
            "recovery_condition": {
                "condition": "The incident is resolved and reactivation evidence is current.",
                "not_before": "2026-06-01T12:45:00Z",
                "required_evidence": "Exact incident-resolution and compatibility bytes.",
                "recovery_evidence_refs": [
                    artifact_ref(
                        "fixture-release-incident-resolution-001",
                        "ylx.release-incident-resolution.v1",
                        incident_resolution_sha,
                        (
                            "contracts/fixtures/governance-models/support/"
                            "release-incident-resolution-001.json"
                        ),
                    ),
                    artifact_ref(
                        "fixture-release-reactivation-compatibility-001",
                        "ylx.release-reactivation-evidence.v1",
                        compatibility_evidence_sha,
                        (
                            "contracts/fixtures/governance-models/support/"
                            "release-reactivation-compatibility-evidence-001.json"
                        ),
                    ),
                ],
            },
            "created_at": "2026-06-01T12:49:00Z",
        }
    )
    distribution_reactivated = sign_distribution_control(
        distribution_reactivated_unsigned, "2026-06-01T12:49:00Z"
    )
    corpus.add(
        "VALID-RELEASE-DISTRIBUTION-CONTROL-REACTIVATED-01",
        "release-distribution-control-reactivated.json",
        "release-distribution-control-v1.schema.json",
        distribution_reactivated,
    )

    terminated_attempt_id = "fixture-release-attempt-terminated-001"
    terminated_attempt_terminal_slot = (
        f"release-attempt-terminals/{terminated_attempt_id}"
    )
    terminated_fence = copy.deepcopy(fence)
    terminated_fence.update(
        {
            "attempt_id": terminated_attempt_id,
            "fence_authority_id": "fixture-termination-fence-authority",
            "release_train": "fixture-release-train-0.5",
            "canonical_remote_id": "fixture-termination-origin",
            "ga_ref": "refs/tags/v0.5.0-terminated-fixture",
            "ga_channel": "channels/terminated-fixture/0.5",
            "canonical_ga_target": (
                "fixture-termination-origin/refs/tags/v0.5.0-terminated-fixture"
            ),
            "attempt_terminal_slot": terminated_attempt_terminal_slot,
            "acquired_at": "2026-06-01T11:10:00Z",
        }
    )
    terminated_fence_sha = corpus.add(
        "VALID-RELEASE-PUBLICATION-FENCE-TERMINATED-ALTERNATIVE-01",
        "release-publication-fence-terminated.json",
        "release-publication-fence-v1.schema.json",
        terminated_fence,
    )
    terminated_fence_ref = artifact_ref(
        terminated_attempt_id,
        "ylx.release-publication-fence.v1",
        terminated_fence_sha,
        "contracts/fixtures/governance-models/valid/release-publication-fence-terminated.json",
    )
    termination = {
        "schema": "ylx.release-publication-fence-termination.v1",
        "termination_id": "fixture-release-attempt-termination-alternative",
        "attempt_id": terminated_attempt_id,
        "attempt_terminal_slot": terminated_attempt_terminal_slot,
        "publication_fence_ref": terminated_fence_ref,
        "publication_fence_sha256": terminated_fence_sha,
        "pre_release_closure_ref": pre_release_ref,
        "pre_release_closure_sha256": pre_release_sha,
            "reason": "AUTHORITY_DRIFT",
        "authority_heads_at_termination": {
            "issue_head": current_issue_head,
            "binding_context_ref": m5_context_ref,
            "signing_policy_sha256": signing_policy_sha,
            "key_validity_revocation_head_sha256": key_head_sha,
            "quorum_policy_sha256": quorum_policy_sha,
        },
        "approved_by": [approval("release-owner"), approval("security-owner")],
        "terminated_at": "2026-06-01T12:15:00Z",
        "ga_quarantine_proof": {
            "promotion_state": "NOT_ATTEMPTED",
            "ga_visibility": "NOT_CUSTOMER_VISIBLE",
            "remote_observation_sha256": None,
        },
        "old_input_recovery": "FORBIDDEN",
        "successor_attempt_policy": "NEW_PRE_RELEASE_CLOSURE_AND_NEW_FOUR_PERSON_SIGNATURES_REQUIRED",
        "release_state": "RELEASE_NOT_COMPLETE",
    }
    termination_sha = corpus.add(
        "VALID-RELEASE-PUBLICATION-FENCE-TERMINATION-01",
        "release-publication-fence-termination.json",
        "release-publication-fence-termination-v1.schema.json",
        termination,
    )
    termination_path = "valid/release-publication-fence-termination.json"
    termination_payload_locator = (
        f"release-termination/{termination_sha}--release-publication-fence-termination.json"
    )
    termination_terminal_slot_record = {
        "kind": "ABORTED",
        "payload_locator": termination_payload_locator,
        "payload_sha256": termination_sha,
    }
    termination_locator = {
        "schema": "ylx.content-addressed-locator-readback.v1",
        "locator_id": "fixture-release-termination-locator",
        "artifact_schema": "ylx.release-publication-fence-termination.v1",
        "artifact_id": termination["termination_id"],
        "artifact_sha256": termination_sha,
        "canonical_path": termination_payload_locator,
        "attempt_terminal_slot": terminated_attempt_terminal_slot,
        "terminal_slot_record": termination_terminal_slot_record,
        "terminal_slot_create_if_absent": True,
        "terminal_slot_recorded_at": "2026-06-01T12:18:00Z",
        "terminal_slot_readback_record": termination_terminal_slot_record,
        "terminal_slot_readback_at": "2026-06-01T12:19:00Z",
        "terminal_slot_readback_result": "EXACT_TERMINAL_SLOT_RECORD_MATCH",
        "freshness_validation": None,
        "exact_byte_length": corpus.byte_lengths[termination_path],
        "canonical_encoding": "RFC8785-JSON-UTF8",
        "create_if_absent": True,
        "existing_identical_is_idempotent": True,
        "different_digest_is_equivocation": True,
        "durability": {
            "temporary_exact_bytes_fsynced": True,
            "parent_fsynced_before_create": True,
            "atomic_unique_create": True,
            "parent_fsynced_after_create": True,
        },
        "published_at": "2026-06-01T12:16:00Z",
        "readback_sha256": termination_sha,
        "readback_byte_length": corpus.byte_lengths[termination_path],
        "readback_at": "2026-06-01T12:17:00Z",
        "readback_result": "EXACT_PATH_DIGEST_AND_BYTES_MATCH",
    }
    termination_locator_sha = corpus.add(
        "VALID-CONTENT-ADDRESSED-TERMINATION-LOCATOR-READBACK-01",
        "content-addressed-locator-readback-termination.json",
        "content-addressed-locator-readback-v1.schema.json",
        termination_locator,
    )

    state["release_v2"] = build_release_v2_chain(
        corpus,
        state,
        current_issue_head=current_issue_head,
        issue_reconciliation_sha=issue_reconciliation_sha,
        consumer_boundary_registry_sha=consumer_boundary_registry_sha,
        consumer_acceptance_set_sha=consumer_acceptance_set_sha,
        component_acceptance_map=component_acceptance_map,
        assignment_values=assignment_values,
        assignment_digests=assignment_digests,
        key_head=key_head,
        key_head_sha=key_head_sha,
        private_keys=private_keys,
        fingerprint_by_role=fingerprint_by_role,
    )

    corpus.relationships.update(
        {
            "issue_register_chain": {
                "source_paths": [
                    "support/issue-register-source-r1.md",
                    "support/issue-register-source.md",
                ],
                "archive_paths": [
                    "support/issue-register-archive-r1.md",
                    "support/issue-register-archive.md",
                ],
                "head_paths": [
                    "valid/issue-register-head-r1.json",
                    "valid/issue-register-head.json",
                ],
                "current_head_sha256": issue_head_sha,
            },
            "release_chain": {
                "binding_context_path": "valid/binding-context-m5.json",
                "release_result_projection_path": "valid/release-result-projection.json",
                "release_result_projection_sha256": release_result_projection_sha,
                "release_result_projection_locator_readback_path": "valid/content-addressed-locator-readback-release-result-projection.json",
                "release_result_projection_locator_readback_sha256": projection_state[
                    "projection_locator_sha256"
                ],
                "release_result_projection_content_addressed_path": corpus.values[
                    "valid/content-addressed-locator-readback-release-result-projection.json"
                ]["canonical_path"],
                "domain_attestation_paths_by_role_slot": {
                    role: f"valid/domain-attestation-{role}.json" for role in ROLES
                },
                "role_assignment_paths_by_role_slot": {
                    role: f"valid/role-signing-key-assignment-{role}.json"
                    for role in QUORUM_ROLES
                },
                "signing_policy_path": "valid/m5-signing-policy.json",
                "key_head_path": "valid/signing-key-validity-revocation-head.json",
                "quorum_policy_path": "valid/release-quorum-policy.json",
                "ga_plan_path": "valid/ga-promotion-plan.json",
                "pre_release_closure_path": "valid/pre-release-closure.json",
                "quorum_signature_paths_by_role_slot": {
                    role: f"valid/release-quorum-signature-{role}.json"
                    for role in QUORUM_ROLES
                },
                "publication_fence_path": "valid/release-publication-fence.json",
                "ga_promotion_receipt_path": "valid/ga-promotion-receipt.json",
                "final_manifest_path": "valid/release-closure-manifest.json",
                "final_locator_readback_path": "valid/content-addressed-locator-readback.json",
                "alternative_termination_path": "valid/release-publication-fence-termination.json",
                "alternative_termination_locator_readback_path": "valid/content-addressed-locator-readback-termination.json",
                "alternative_terminated_fence_path": "valid/release-publication-fence-terminated.json",
                "pre_release_locator_readback_path": "valid/content-addressed-locator-readback-pre-release.json",
                "pre_release_locator_readback_sha256": pre_release_locator_sha,
                "final_manifest_sha256": final_manifest_sha,
                "final_locator_readback_sha256": locator_sha,
                "alternative_termination_sha256": termination_sha,
                "alternative_termination_locator_readback_sha256": termination_locator_sha,
                "terminal_slot_records_by_attempt_id": {
                    attempt_id: {
                        "attempt_terminal_slot": attempt_terminal_slot,
                        "record": locator["terminal_slot_record"],
                    },
                    terminated_attempt_id: {
                        "attempt_terminal_slot": terminated_attempt_terminal_slot,
                        "record": termination_locator["terminal_slot_record"],
                    },
                },
            },
            "test_only_public_keys_by_role_slot": signature_public_keys,
            "signature_message_rule": "ASCII(signature_domain) || 0x00 || RFC8785(signed_payload)",
            "fixture_notice_carriage": (
                "Closed schemas and signed bytes cannot accept an extra fixture_notice member. "
                "The exact notice is carried by every manifest entry and by artifact_metadata "
                "where the corresponding closed schema permits it."
            ),
        }
    )


def _decode_pointer_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _assert_mutation_pointer(target: Any, mutation: dict[str, Any]) -> None:
    pointer = mutation["pointer"]
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise AssertionError(f"mutation pointer is not RFC 6901-like: {pointer!r}")
    tokens = [_decode_pointer_token(token) for token in pointer[1:].split("/")]
    parent = target
    for token in tokens[:-1]:
        if isinstance(parent, list):
            parent = parent[int(token)]
        elif isinstance(parent, dict):
            parent = parent[token]
        else:
            raise TypeError(f"pointer traverses scalar at {pointer!r}")
    leaf = tokens[-1]
    if mutation["op"] in {"replace", "remove"}:
        if isinstance(parent, list):
            index = int(leaf)
            if index < 0 or index >= len(parent):
                raise AssertionError(f"array pointer does not exist: {pointer!r}")
        elif isinstance(parent, dict):
            if leaf not in parent:
                raise AssertionError(f"object pointer does not exist: {pointer!r}")
        else:
            raise TypeError(f"pointer parent is scalar: {pointer!r}")
    elif mutation["op"] == "add":
        if isinstance(parent, list) and leaf != "-":
            index = int(leaf)
            if index < 0 or index > len(parent):
                raise AssertionError(f"array add pointer is out of range: {pointer!r}")
        elif not isinstance(parent, (list, dict)):
            raise TypeError(f"add pointer parent is scalar: {pointer!r}")
    else:
        raise AssertionError(f"unsupported mutation op: {mutation['op']!r}")


def _apply_catalog_mutation(target: Any, mutation: dict[str, Any]) -> Any:
    """Apply one catalog mutation while validating a coordinated overlay."""

    _assert_mutation_pointer(target, mutation)
    result = copy.deepcopy(target)
    tokens = [_decode_pointer_token(token) for token in mutation["pointer"][1:].split("/")]
    parent = result
    for token in tokens[:-1]:
        parent = parent[int(token)] if isinstance(parent, list) else parent[token]
    leaf = tokens[-1]
    if isinstance(parent, list):
        if mutation["op"] == "add":
            if leaf == "-":
                parent.append(copy.deepcopy(mutation["value"]))
            else:
                parent.insert(int(leaf), copy.deepcopy(mutation["value"]))
        elif mutation["op"] == "remove":
            del parent[int(leaf)]
        else:
            parent[int(leaf)] = copy.deepcopy(mutation["value"])
    elif mutation["op"] == "add" or mutation["op"] == "replace":
        parent[leaf] = copy.deepcopy(mutation["value"])
    else:
        del parent[leaf]
    return result


def _load_json_without_duplicates(raw: bytes, path: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r} in {path}")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=reject_duplicates)


def finalize_corpus(
    corpus: Corpus, requirement_ids: list[str], m4_ids: list[str]
) -> None:
    from invalid_case_catalog import STABLE_CODES, build_invalid_cases

    projection = corpus.values["valid/release-result-projection.json"]
    rebase = corpus.values["valid/m4-verdict-rebase.json"]
    m1_row = next(
        row
        for row in projection["row_projection_by_requirement_id"].values()
        if row["closing_gate"] == "M1"
    )
    projection_publication = corpus.values[
        "valid/release-result-projection-publication-receipt.json"
    ]
    release_projection_v2_current_ref = copy.deepcopy(
        projection_publication["projection_ref"]
    )
    fixture_root_prefix = "contracts/fixtures/governance-models/"
    current_projection_artifact_path = release_projection_v2_current_ref[
        "artifact_path"
    ]
    if not current_projection_artifact_path.startswith(fixture_root_prefix):
        raise AssertionError("current release projection ref escapes fixture root")
    current_projection_path = current_projection_artifact_path.removeprefix(
        fixture_root_prefix
    )
    if current_projection_path not in corpus.values:
        raise AssertionError("current release projection ref does not resolve")
    current_projection = corpus.values[current_projection_path]
    release_projection_v2_predecessor_ref = copy.deepcopy(
        current_projection["predecessor_projection_ref"]
    )
    predecessor_artifact_path = release_projection_v2_predecessor_ref[
        "artifact_path"
    ]
    if not predecessor_artifact_path.startswith(fixture_root_prefix):
        raise AssertionError("predecessor release projection ref escapes fixture root")
    predecessor_projection_path = predecessor_artifact_path.removeprefix(
        fixture_root_prefix
    )
    if predecessor_projection_path not in corpus.values:
        raise AssertionError("predecessor release projection ref does not resolve")
    predecessor_projection = corpus.values[predecessor_projection_path]
    if not (
        release_projection_v2_current_ref["schema"]
        == current_projection["schema"]
        == release_projection_v2_predecessor_ref["schema"]
        == predecessor_projection["schema"]
        == "ylx.release-result-projection.v2"
        and release_projection_v2_current_ref["artifact_id"]
        == current_projection["projection_id"]
        == release_projection_v2_predecessor_ref["artifact_id"]
        == predecessor_projection["projection_id"]
        and release_projection_v2_current_ref["revision"]
        == current_projection["revision"]
        == 2
        and release_projection_v2_predecessor_ref["revision"]
        == predecessor_projection["revision"]
        == 1
        and release_projection_v2_current_ref["artifact_sha256"]
        == corpus.digests[current_projection_path]
        and release_projection_v2_predecessor_ref["artifact_sha256"]
        == corpus.digests[predecessor_projection_path]
        and current_projection["predecessor_projection_ref"]
        == release_projection_v2_predecessor_ref
    ):
        raise AssertionError("release projection exact-ref chain is inconsistent")
    wrong_action_evaluation = corpus.values[
        "valid/execution-authorization-evaluation-assemble-release-projection-pass.json"
    ]
    unrelated_release_authorizing_e_ref = artifact_ref(
        wrong_action_evaluation["evaluation_id"],
        wrong_action_evaluation["schema"],
        corpus.digests[
            "valid/execution-authorization-evaluation-assemble-release-projection-pass.json"
        ],
        (
            "contracts/fixtures/governance-models/valid/"
            "execution-authorization-evaluation-assemble-release-projection-pass.json"
        ),
        None,
    )
    unrelated_release_authorizing_e_source_ref = {
        "ref_id": unrelated_release_authorizing_e_ref["artifact_id"],
        "authority_kind": "execution-authorization-evaluation",
        "locator": unrelated_release_authorizing_e_ref["artifact_path"],
        "sha256": unrelated_release_authorizing_e_ref["artifact_sha256"],
    }
    wrong_action_authority_refs = wrong_action_evaluation[
        "authorization_authority_ref_by_artifact_id"
    ]
    if not isinstance(wrong_action_authority_refs, dict) or len(
        wrong_action_authority_refs
    ) != 1:
        raise AssertionError("wrong-action authority oracle must contain one exact ref")
    wrong_action_authority_ref = copy.deepcopy(
        next(iter(wrong_action_authority_refs.values()))
    )
    wrong_type_authority_support_path = (
        "support/execution-authority-wrong-type-oracle.json"
    )
    wrong_type_authority_entry = next(
        entry
        for entry in corpus.support_entries
        if entry["path"] == wrong_type_authority_support_path
    )
    wrong_type_authority_ref = artifact_ref(
        "fixture-wrong-type-execution-authority",
        "ylx.execution-authority.v1",
        wrong_type_authority_entry["sha256"],
        (
            "contracts/fixtures/governance-models/"
            "support/execution-authority-wrong-type-oracle.json"
        ),
        1,
    )
    wrong_release_assignment_path = (
        "valid/release-operation-assignment-v2-promotion.json"
    )
    wrong_release_assignment = corpus.values[wrong_release_assignment_path]
    wrong_release_operation_assignment_ref = artifact_ref(
        wrong_release_assignment["assignment_id"],
        wrong_release_assignment["schema"],
        corpus.digests[wrong_release_assignment_path],
        (
            "contracts/fixtures/governance-models/"
            f"{wrong_release_assignment_path}"
        ),
        wrong_release_assignment["revision"],
    )
    final_chain = corpus.relationships.get(
        "final_actual_variance_durability"
    )
    if not isinstance(final_chain, dict):
        raise TypeError("final actual/variance durability relationship is absent")
    final_bundle_ref = copy.deepcopy(final_chain.get("final_bundle_ref"))
    final_publication_ref = copy.deepcopy(
        final_chain.get("publication_receipt_ref")
    )
    if not isinstance(final_bundle_ref, dict) or not isinstance(
        final_publication_ref, dict
    ):
        raise TypeError("final durability relationship lacks exact F/P refs")
    final_bundle_artifact_path = final_bundle_ref.get("artifact_path")
    if not isinstance(final_bundle_artifact_path, str) or not (
        final_bundle_artifact_path.startswith(fixture_root_prefix)
    ):
        raise AssertionError("final bundle ref escapes fixture root")
    final_bundle_path = final_bundle_artifact_path.removeprefix(
        fixture_root_prefix
    )
    final_bundle = corpus.values.get(final_bundle_path)
    if not isinstance(final_bundle, dict) or (
        corpus.digests.get(final_bundle_path)
        != final_bundle_ref.get("artifact_sha256")
    ):
        raise AssertionError("final bundle relationship ref does not resolve")
    final_reconciliation = final_bundle.get(
        "final_actual_variance_reconciliation"
    )
    if not isinstance(final_reconciliation, dict):
        raise TypeError("final bundle lacks final reconciliation")
    publisher_closure = final_reconciliation.get("publisher_closure")
    task_actuals = final_reconciliation.get("task_actual_by_node_id")
    if not isinstance(publisher_closure, dict) or not isinstance(
        task_actuals, dict
    ):
        raise TypeError("final reconciliation lacks publisher task actuals")
    final_publisher_task_node_id = publisher_closure.get("task_node_id")
    if not isinstance(final_publisher_task_node_id, str):
        raise TypeError("final publisher task ID is absent")
    ordinary_task_node_ids = sorted(
        node_id
        for node_id in task_actuals
        if node_id != final_publisher_task_node_id
    )
    if not ordinary_task_node_ids:
        raise AssertionError("final reconciliation lacks an ordinary executed leaf")
    final_ordinary_task_node_id = ordinary_task_node_ids[0]
    ordinary_terminal_refs = task_actuals[final_ordinary_task_node_id].get(
        "terminal_evidence_refs"
    )
    if not isinstance(ordinary_terminal_refs, list) or not ordinary_terminal_refs:
        raise AssertionError("ordinary final leaf lacks terminal evidence")
    final_terminal_evidence_ref = copy.deepcopy(ordinary_terminal_refs[0])

    measurement_chain = corpus.relationships.get("measurement_threshold_chain")
    measurement_queue_chain = corpus.relationships.get("measurement_queue_chain")
    if not isinstance(measurement_chain, dict) or not isinstance(
        measurement_queue_chain, dict
    ):
        raise TypeError("measurement threshold or queue relationship is absent")
    measurement_threshold = corpus.values[
        "valid/measurement-threshold-record-m0-meas-01.json"
    ]
    measurement_training_selection = corpus.values[
        "valid/measurement-data-selection-training-m0-meas-01.json"
    ]
    measurement_holdout_selection = corpus.values[
        "valid/measurement-data-selection-holdout-m0-meas-01.json"
    ]
    measurement_queue_r1 = corpus.values["valid/measurement-queue-v2-r1.json"]
    measurement_queue_r2 = corpus.values["valid/measurement-queue-v2-r2.json"]
    measurement_queue_r3 = corpus.values["valid/measurement-queue-v2.json"]
    measurement_holdout_row = next(
        row
        for row in measurement_queue_r2["measurements"]
        if row["measurement_id"] == "M0-MEAS-01"
    )
    wrong_measurement_verdict_path = (
        "valid/stage-terminal-result-v2-m0-meas-02.json"
    )
    wrong_measurement_verdict = corpus.values[wrong_measurement_verdict_path]
    wrong_measurement_verdict_ref = artifact_ref(
        wrong_measurement_verdict["result_id"],
        wrong_measurement_verdict["schema"],
        corpus.digests[wrong_measurement_verdict_path],
        f"{fixture_root_prefix}{wrong_measurement_verdict_path}",
        wrong_measurement_verdict["revision"],
    )
    measurement_queue_r1_ref = artifact_ref(
        measurement_queue_r1["queue_id"],
        measurement_queue_r1["schema"],
        measurement_queue_chain["r1_sha256"],
        f"{fixture_root_prefix}valid/measurement-queue-v2-r1.json",
        1,
    )
    measurement_queue_r2_ref = artifact_ref(
        measurement_queue_r2["queue_id"],
        measurement_queue_r2["schema"],
        measurement_queue_chain["r2_sha256"],
        f"{fixture_root_prefix}valid/measurement-queue-v2-r2.json",
        2,
    )
    measurement_queue_r3_ref = artifact_ref(
        measurement_queue_r3["queue_id"],
        measurement_queue_r3["schema"],
        measurement_queue_chain["r3_sha256"],
        f"{fixture_root_prefix}valid/measurement-queue-v2.json",
        3,
    )

    def measurement_queue_content_sha256(queue: dict[str, Any]) -> str:
        return sha(
            canonical_bytes(
                {
                    key: value
                    for key, value in queue.items()
                    if key != "content_sha256"
                }
            )
        )

    measurement_queue_self_predecessor_ref = copy.deepcopy(
        measurement_queue_r3_ref
    )
    measurement_queue_r3_self_predecessor = copy.deepcopy(
        measurement_queue_r3
    )
    measurement_queue_r3_self_predecessor["predecessor_queue_ref"] = (
        measurement_queue_self_predecessor_ref
    )
    measurement_queue_r3_bogus_predecessor = copy.deepcopy(
        measurement_queue_r3
    )
    measurement_queue_r3_bogus_predecessor["predecessor_queue_ref"][
        "artifact_sha256"
    ] = "0" * 64
    measurement_queue_r3_bogus_selected_head = copy.deepcopy(
        measurement_queue_r3
    )
    measurement_queue_r3_bogus_selected_head["registry_binding"][
        "selected_head_ref"
    ]["artifact_sha256"] = "0" * 64
    measurement_queue_r3_stale_r1_binding = copy.deepcopy(
        measurement_queue_r3
    )
    measurement_queue_r3_stale_r1_binding["registry_binding"] = copy.deepcopy(
        measurement_queue_chain["r1_registry_binding"]
    )
    measurement_queue_r3_stale_previous_binding = copy.deepcopy(
        measurement_queue_r3
    )
    measurement_queue_r3_stale_previous_binding[
        "registry_binding"
    ] = copy.deepcopy(measurement_queue_chain["previous_registry_binding"])

    def load_g0_fixture(filename: str) -> tuple[dict[str, Any], bytes]:
        path = VALID_ROOT / filename
        raw = path.read_bytes()
        value = _load_json_without_duplicates(raw, path.as_posix())
        if not isinstance(value, dict):
            raise TypeError(f"G0 fixture file is not an object: {filename}")
        return value, raw

    g0_subject, g0_subject_raw = load_g0_fixture(
        "g0-policy-ratification-subject.json"
    )
    g0_ratification, g0_ratification_raw = load_g0_fixture(
        "g0-policy-ratification.json"
    )
    g0_publication, g0_publication_raw = load_g0_fixture(
        "g0-policy-ratification-publication-receipt.json"
    )
    g0_readback, g0_readback_raw = load_g0_fixture(
        "g0-policy-ratification-readback-receipt.json"
    )
    g0_authority, _ = load_g0_fixture(
        "g0-external-organizational-authority.json"
    )
    g0_quorum, _ = load_g0_fixture("g0-quorum-policy.json")
    g0_approval_by_role = {
        role_id: load_g0_fixture(
            f"g0-external-approval-{role_id}.json"
        )[0]
        for role_id in ("release-owner", "security-owner")
    }
    g0_missing_canonical_subject_sha256 = sha(
        canonical_bytes(
            {
                key: value
                for key, value in g0_subject.items()
                if key != "canonical_governance_subject"
            }
        )
    )
    g0_support_context = {
        "path_by_kind": {
            "subject": "valid/g0-policy-ratification-subject.json",
            "external_authority": (
                "valid/g0-external-organizational-authority.json"
            ),
            "quorum_policy": "valid/g0-quorum-policy.json",
            "canonical_clean_commit": "support/g0-canonical-clean-commit.json",
            "canonical_locator_readback": (
                "valid/g0-canonical-locator-readback.json"
            ),
            "event": "valid/g0-policy-ratification.json",
            "publication_receipt": (
                "valid/g0-policy-ratification-publication-receipt.json"
            ),
            "readback_receipt": (
                "valid/g0-policy-ratification-readback-receipt.json"
            ),
        },
        "approval_path_by_role": {
            role_id: f"valid/g0-external-approval-{role_id}.json"
            for role_id in g0_approval_by_role
        },
        "approval_import_receipt_path_by_role": {
            role_id: (
                f"valid/g0-external-approval-import-receipt-{role_id}.json"
            )
            for role_id in g0_approval_by_role
        },
        "outreach_receipt_path_by_role": {
            role_id: (
                f"valid/g0-external-outreach-send-receipt-{role_id}.json"
            )
            for role_id in g0_approval_by_role
        },
        "grant_path_by_kind": {
            "clean_commit_publication": (
                "valid/g0-operation-authority-clean-commit-publication.json"
            ),
            "repository_receipt_sink": (
                "valid/g0-operation-authority-repository-receipt-sink.json"
            ),
            "outreach_receipt_sink": (
                "valid/g0-operation-authority-outreach-receipt-sink.json"
            ),
            "canonical_readback_sink": (
                "valid/g0-operation-authority-canonical-readback-sink.json"
            ),
            "event_publication_sink": (
                "valid/g0-operation-authority-event-publication-sink.json"
            ),
            "event_readback_sink": (
                "valid/g0-operation-authority-event-readback-sink.json"
            ),
            "canonical_locator_readback": (
                "valid/g0-operation-authority-canonical-locator-readback.json"
            ),
            "subject_publication": (
                "valid/g0-operation-authority-subject-publication.json"
            ),
            "event_publisher": (
                "valid/g0-operation-authority-event-publisher.json"
            ),
            "event_repository_permission": (
                "valid/g0-operation-authority-event-repository-permission.json"
            ),
            "event_readback": (
                "valid/g0-operation-authority-event-readback.json"
            ),
        },
        "role_ids": list(g0_quorum["required_role_ids"]),
        "approver_person_by_role": {
            role_id: value["approver_person_id"]
            for role_id, value in g0_approval_by_role.items()
        },
        "conflict_group_by_role": {
            role_id: value["conflict_group_id"]
            for role_id, value in g0_authority[
                "eligible_approver_by_role_id"
            ].items()
        },
        "approval_sha256_by_required_role": copy.deepcopy(
            g0_ratification["approval_sha256_by_required_role"]
        ),
        "exact_five_path_sha256_by_path": copy.deepcopy(
            g0_subject["canonical_governance_subject"][
                "exact_five_path_sha256_by_path"
            ]
        ),
        "subject_sha256": sha(g0_subject_raw),
        "missing_canonical_subject_sha256": (
            g0_missing_canonical_subject_sha256
        ),
        "event_sha256": sha(g0_ratification_raw),
        "event_byte_length": len(g0_ratification_raw),
        "publication_receipt_sha256": sha(g0_publication_raw),
        "readback_receipt_sha256": sha(g0_readback_raw),
        "published_at": g0_publication["published_at"],
        "read_back_at": g0_readback["read_back_at"],
        "effective_at": g0_ratification["effective_at"],
        "event_id": g0_ratification["event_id"],
        "event_revision": g0_ratification["revision"],
        "repository_locator": g0_publication["repository_locator"],
        "target_scope": g0_publication["target_scope"],
        "duplicate_event_path": "valid/g0-policy-ratification-fork.json",
        "duplicate_publication_path": (
            "valid/g0-policy-ratification-publication-receipt-fork.json"
        ),
        "duplicate_readback_path": (
            "valid/g0-policy-ratification-readback-receipt-fork.json"
        ),
    }

    scheduled_forecast_indexes = [
        index
        for index, item in enumerate(
            corpus.values["valid/forecast-snapshot-v2-planning.json"][
                "task_forecasts"
            ]
        )
        if item["forecast_status"] == "SCHEDULED"
    ]
    primary_forecast_index, secondary_forecast_index = scheduled_forecast_indexes[:2]
    planning_forecast = corpus.values["valid/forecast-snapshot-v2-planning.json"]
    primary_forecast = planning_forecast["task_forecasts"][primary_forecast_index]
    m0_milestone_index = next(
        index
        for index, item in enumerate(
            corpus.values["valid/forecast-snapshot-v2-planning.json"][
                "milestone_forecasts"
            ]
        )
        if item["milestone_id"] == "M0"
    )
    planning_forecast_mutations_by_case_id = {
        "INVALID-PLANNING-V2-TOTAL-FLOAT-01": [
            {"op": "replace", "pointer": f"/task_forecasts/{primary_forecast_index}/total_float/value", "value": 999}
        ],
        "INVALID-PLANNING-V2-FREE-FLOAT-01": [
            {"op": "replace", "pointer": f"/task_forecasts/{primary_forecast_index}/free_float/value", "value": 999}
        ],
        "INVALID-PLANNING-V2-DEPENDENCY-TIME-01": [
            {"op": "replace", "pointer": f"/task_forecasts/{primary_forecast_index}/dependency_only_start", "value": "2026-06-10T00:00:00Z"},
            {"op": "replace", "pointer": f"/task_forecasts/{primary_forecast_index}/dependency_only_finish", "value": "2026-06-10T08:00:00Z"},
        ],
        "INVALID-PLANNING-V2-EMPTY-CRITICAL-PATH-01": [
            {"op": "replace", "pointer": "/dependency_critical_path", "value": []},
            *[
                {"op": "replace", "pointer": f"/task_forecasts/{index}/dependency_critical", "value": False}
                for index in scheduled_forecast_indexes
            ],
        ],
        "INVALID-PLANNING-V2-RESOURCE-TIME-01": [
            {"op": "replace", "pointer": f"/task_forecasts/{primary_forecast_index}/resource_levelled_start", "value": "2026-06-10T00:00:00Z"},
            {"op": "replace", "pointer": f"/task_forecasts/{primary_forecast_index}/resource_levelled_finish", "value": "2026-06-10T08:00:00Z"},
        ],
        "INVALID-PLANNING-V2-EMPTY-DRIVING-PATH-01": [
            {"op": "replace", "pointer": "/resource_levelled_driving_path", "value": []},
            *[
                {"op": "replace", "pointer": f"/task_forecasts/{index}/driving_path", "value": False}
                for index in scheduled_forecast_indexes
            ],
        ],
        "INVALID-PLANNING-V2-WINDOW-DELAY-01": [
            {"op": "replace", "pointer": f"/task_forecasts/{primary_forecast_index}/window_delay/value", "value": 999}
        ],
        "INVALID-PLANNING-V2-ORPHAN-DECISION-01": [
            {"op": "replace", "pointer": "/decision_need_bys/0/decision_id", "value": "fixture-orphan-decision"}
        ],
        "INVALID-PLANNING-V2-MILESTONE-01": [
            {"op": "replace", "pointer": f"/milestone_forecasts/{m0_milestone_index}/forecast_finish", "value": "2026-06-20T00:00:00Z"},
            {"op": "replace", "pointer": f"/milestone_forecasts/{m0_milestone_index}/driving_task_ids", "value": ["fixture-v2-stage-evidence-m1"]},
        ],
        "INVALID-PLANNING-V2-OVERLAPPING-QUANTITY-ONE-01": [
            {"op": "replace", "pointer": f"/task_forecasts/{secondary_forecast_index}/resource_levelled_start", "value": primary_forecast["resource_levelled_start"]},
            {"op": "replace", "pointer": f"/task_forecasts/{secondary_forecast_index}/resource_levelled_finish", "value": primary_forecast["resource_levelled_finish"]},
            {"op": "replace", "pointer": f"/task_forecasts/{secondary_forecast_index}/forecast_start", "value": primary_forecast["forecast_start"]},
            {"op": "replace", "pointer": f"/task_forecasts/{secondary_forecast_index}/forecast_finish", "value": primary_forecast["forecast_finish"]},
        ],
    }
    planning_forecast_sha256_by_case_id = {}
    for case_id, mutations in planning_forecast_mutations_by_case_id.items():
        mutated = copy.deepcopy(planning_forecast)
        for mutation in mutations:
            mutated = _apply_catalog_mutation(mutated, mutation)
        planning_forecast_sha256_by_case_id[case_id] = sha(canonical_bytes(mutated))

    planning_wbs = corpus.values["valid/delivery-wbs-v2.json"]
    duplicate_requirement = copy.deepcopy(
        planning_wbs["nodes"][0]["resource_requirements"][0]
    )
    duplicate_requirement["resource_requirement_id"] = (
        "fixture-v2-ci-runner-duplicate"
    )
    duplicate_predecessor_id = planning_wbs["nodes"][0]["node_id"]
    if planning_wbs["nodes"][1]["predecessor_refs"] != []:
        raise AssertionError(
            "duplicate predecessor mutation target must start without predecessors"
        )
    duplicate_predecessor_refs = [
        {
            "predecessor_node_id": duplicate_predecessor_id,
            "relation_type": "FINISH_TO_START",
            "lag": {"value": 0, "unit": "hours"},
        },
        {
            "predecessor_node_id": duplicate_predecessor_id,
            "relation_type": "START_TO_START",
            "lag": {"value": 1, "unit": "hours"},
        },
    ]
    planning_input_mutations_by_case_id = {
        "INVALID-PLANNING-V2-OUT-OF-HORIZON-01": {
            "kind": "delivery_wbs",
            "mutations": [
                {"op": "replace", "pointer": "/nodes/0/absolute_start", "value": "2026-07-01T00:00:00Z"},
                {"op": "replace", "pointer": "/nodes/0/absolute_finish", "value": "2026-07-01T08:00:00Z"},
            ],
        },
        "INVALID-PLANNING-V2-QUANTITY-EXCEEDS-CAPACITY-01": {
            "kind": "delivery_wbs",
            "mutations": [
                {"op": "replace", "pointer": "/nodes/0/resource_requirements/0/quantity", "value": 2}
            ],
        },
        "INVALID-PLANNING-V2-DUPLICATE-AGGREGATE-CAPACITY-01": {
            "kind": "delivery_wbs",
            "mutations": [
                {"op": "add", "pointer": "/nodes/0/resource_requirements/-", "value": duplicate_requirement}
            ],
        },
        "INVALID-PLANNING-V2-DUPLICATE-PREDECESSOR-ID-01": {
            "kind": "delivery_wbs",
            "mutations": [
                {
                    "op": "replace",
                    "pointer": "/nodes/1/predecessor_refs",
                    "value": duplicate_predecessor_refs,
                }
            ],
        },
        "INVALID-PLANNING-V2-REVERSED-CALENDAR-INTERVAL-01": {
            "kind": "resource_calendar",
            "mutations": [
                {"op": "replace", "pointer": "/windows/0/ends_at", "value": "2026-05-31T23:59:59Z"}
            ],
        },
    }
    planning_input_refs_by_case_id: dict[str, dict[str, str]] = {}
    planning_calendar = corpus.values["valid/resource-calendar.json"]
    for case_id, definition in planning_input_mutations_by_case_id.items():
        source = planning_wbs if definition["kind"] == "delivery_wbs" else planning_calendar
        mutated_input = copy.deepcopy(source)
        for mutation in definition["mutations"]:
            mutated_input = _apply_catalog_mutation(mutated_input, mutation)
        input_sha = sha(canonical_bytes(mutated_input))
        mutated_forecast = copy.deepcopy(planning_forecast)
        forecast_input_field = (
            "delivery_wbs_sha256"
            if definition["kind"] == "delivery_wbs"
            else "resource_calendar_sha256"
        )
        mutated_forecast[forecast_input_field] = input_sha
        planning_input_refs_by_case_id[case_id] = {
            "input_sha256": input_sha,
            "forecast_sha256": sha(canonical_bytes(mutated_forecast)),
        }
    invalid_cases = build_invalid_cases(
        NOTICE,
        {
            "m4_source_binding_context_ref": copy.deepcopy(
                rebase["source_binding_context_ref"]
            ),
            "m4_target_binding_context_ref": copy.deepcopy(
                rebase["target_binding_context_ref"]
            ),
            "m3_gate_root_ref": copy.deepcopy(
                projection["selected_gate_root_ref_by_closing_gate"]["M3"]
            ),
            "m1_evidence_binding_ref": copy.deepcopy(
                m1_row["evidence_binding_refs"][0]
            ),
            "release_projection_v2_current_ref": (
                release_projection_v2_current_ref
            ),
            "release_projection_v2_predecessor_ref": (
                release_projection_v2_predecessor_ref
            ),
            "wrong_action_authority_ref": wrong_action_authority_ref,
            "unrelated_release_authorizing_e_ref": (
                unrelated_release_authorizing_e_ref
            ),
            "unrelated_release_authorizing_e_source_ref": (
                unrelated_release_authorizing_e_source_ref
            ),
            "wrong_type_authority_ref": wrong_type_authority_ref,
            "wrong_release_operation_assignment_ref": (
                wrong_release_operation_assignment_ref
            ),
            "final_bundle_ref": final_bundle_ref,
            "final_publication_ref": final_publication_ref,
            "final_publisher_task_node_id": final_publisher_task_node_id,
            "final_ordinary_task_node_id": final_ordinary_task_node_id,
            "final_terminal_evidence_ref": final_terminal_evidence_ref,
            "final_bundle_byte_length": len(canonical_bytes(final_bundle)),
            "measurement_training_selection_ref": copy.deepcopy(
                measurement_chain["training_selection_ref"]
            ),
            "measurement_holdout_selection_ref": copy.deepcopy(
                measurement_chain["holdout_selection_ref"]
            ),
            "measurement_training_evidence_ref": copy.deepcopy(
                measurement_chain["training_evidence_ref"]
            ),
            "measurement_holdout_evidence_ref": copy.deepcopy(
                measurement_chain["holdout_evidence_ref"]
            ),
            "measurement_training_evaluation_ref": copy.deepcopy(
                measurement_chain["training_evidence_evaluation_ref"]
            ),
            "measurement_holdout_evaluation_ref": copy.deepcopy(
                measurement_chain["holdout_evidence_evaluation_ref"]
            ),
            "measurement_threshold_evaluation_ref": copy.deepcopy(
                measurement_chain["execution_authorization_evaluation_ref"]
            ),
            "measurement_training_evidence_binding_ref": copy.deepcopy(
                measurement_chain["training_evidence_binding_ref"]
            ),
            "measurement_holdout_evidence_binding_ref": copy.deepcopy(
                measurement_chain["holdout_evidence_binding_ref"]
            ),
            "measurement_partition_ref": copy.deepcopy(
                measurement_chain["data_partition_ref"]
            ),
            "measurement_freeze_method_ref": copy.deepcopy(
                measurement_chain["freeze_method_ref"]
            ),
            "measurement_statistical_method_ref": copy.deepcopy(
                measurement_chain["statistical_method_ref"]
            ),
            "measurement_threshold_ref": copy.deepcopy(
                measurement_chain["threshold_ref"]
            ),
            "measurement_threshold_term": copy.deepcopy(
                measurement_threshold["threshold_terms"][0]
            ),
            "measurement_training_evidence_wrapper": copy.deepcopy(
                measurement_threshold["freeze_evidence_refs"][0]
            ),
            "measurement_holdout_evidence_wrapper": copy.deepcopy(
                measurement_holdout_row["evidence_record_refs"][0]
            ),
            "measurement_training_group_ids": copy.deepcopy(
                measurement_training_selection["source_group_ids"]
            ),
            "measurement_holdout_group_ids": copy.deepcopy(
                measurement_holdout_selection["source_group_ids"]
            ),
            "measurement_training_source_set_sha256": (
                measurement_training_selection[
                    "selected_source_digest_set_sha256"
                ]
            ),
            "measurement_training_sample_set_sha256": (
                measurement_training_selection["selected_sample_set_sha256"]
            ),
            "measurement_queue_r1_ref": measurement_queue_r1_ref,
            "measurement_queue_r2_ref": measurement_queue_r2_ref,
            "measurement_queue_r3_ref": measurement_queue_r3_ref,
            "measurement_queue_self_predecessor_ref": (
                measurement_queue_self_predecessor_ref
            ),
            "measurement_queue_r3_self_predecessor_content_sha256": (
                measurement_queue_content_sha256(
                    measurement_queue_r3_self_predecessor
                )
            ),
            "measurement_queue_r3_bogus_predecessor_content_sha256": (
                measurement_queue_content_sha256(
                    measurement_queue_r3_bogus_predecessor
                )
            ),
            "measurement_queue_r3_bogus_selected_head_content_sha256": (
                measurement_queue_content_sha256(
                    measurement_queue_r3_bogus_selected_head
                )
            ),
            "measurement_queue_r3_stale_r1_binding_content_sha256": (
                measurement_queue_content_sha256(
                    measurement_queue_r3_stale_r1_binding
                )
            ),
            "measurement_queue_r3_stale_previous_binding_content_sha256": (
                measurement_queue_content_sha256(
                    measurement_queue_r3_stale_previous_binding
                )
            ),
            "measurement_queue_r1_registry_binding": copy.deepcopy(
                measurement_queue_chain["r1_registry_binding"]
            ),
            "measurement_queue_current_registry_binding": copy.deepcopy(
                measurement_queue_chain["current_registry_binding"]
            ),
            "measurement_queue_previous_registry_binding": copy.deepcopy(
                measurement_queue_chain["previous_registry_binding"]
            ),
            "measurement_wrong_verdict_ref": wrong_measurement_verdict_ref,
            "g0_missing_canonical_subject_sha256": (
                g0_missing_canonical_subject_sha256
            ),
            "g0_support": g0_support_context,
            "m0_support": copy.deepcopy(
                corpus.generator_context["m0_support"]
            ),
            "planning_semantic": {
                "forecast_mutations_by_case_id": planning_forecast_mutations_by_case_id,
                "forecast_sha256_by_case_id": planning_forecast_sha256_by_case_id,
                "input_mutations_by_case_id": planning_input_mutations_by_case_id,
                "input_refs_by_case_id": planning_input_refs_by_case_id,
            },
        },
    )
    case_ids = [case["case_id"] for case in invalid_cases]
    if len(case_ids) != len(set(case_ids)):
        raise AssertionError("invalid mutation case IDs are not unique")
    observed_codes = {case["expected_code"] for case in invalid_cases}
    if observed_codes != set(STABLE_CODES):
        raise AssertionError(
            f"invalid code coverage mismatch: missing={sorted(set(STABLE_CODES)-observed_codes)}, "
            f"extra={sorted(observed_codes-set(STABLE_CODES))}"
        )
    for case in invalid_cases:
        if case["notice"] != NOTICE:
            raise AssertionError(f"case notice mismatch: {case['case_id']}")
        if case["expected_stage"] not in {"schema", "procedural"}:
            raise AssertionError(f"invalid expected_stage: {case['case_id']}")
        uses_legacy_mutation = isinstance(case.get("target_path"), str) and (
            isinstance(case.get("mutation"), dict)
        )
        uses_scenario_overlays = isinstance(case.get("overlays"), list) and bool(
            case["overlays"]
        )
        uses_support_overlays = isinstance(
            case.get("support_overlays"), list
        ) and bool(case["support_overlays"])
        if sum(
            (
                uses_legacy_mutation,
                uses_scenario_overlays,
                uses_support_overlays,
            )
        ) != 1:
            raise AssertionError(
                f"case must use exactly one mutation shape: {case['case_id']}"
            )
        if uses_legacy_mutation:
            target_path = case["target_path"]
            if target_path not in corpus.values:
                raise AssertionError(
                    f"unknown mutation target {target_path}: {case['case_id']}"
                )
            _assert_mutation_pointer(corpus.values[target_path], case["mutation"])
            continue

        if uses_support_overlays:
            support_scenario_values: dict[str, Any] = {}
            support_scenario_paths = {
                entry["path"] for entry in corpus.support_entries
            }
            for overlay in case["support_overlays"]:
                if not isinstance(overlay, dict):
                    raise TypeError(
                        f"support overlay is not an object: {case['case_id']}"
                    )
                target_path = overlay.get("target_path")
                if not isinstance(target_path, str):
                    raise TypeError(
                        f"support overlay target is invalid: {case['case_id']}"
                    )
                normalized_target = target_path.removeprefix(
                    fixture_root_prefix
                )
                if not normalized_target.startswith("support/"):
                    raise AssertionError(
                        f"support overlay escapes support root: {case['case_id']}"
                    )
                if normalized_target not in support_scenario_paths:
                    raise AssertionError(
                        f"unknown support overlay target {normalized_target}: "
                        f"{case['case_id']}"
                    )
                drop_model = overlay.get("drop_model", False)
                if not isinstance(drop_model, bool):
                    raise TypeError(
                        f"support overlay drop_model is invalid: {case['case_id']}"
                    )
                mutations = overlay.get("mutations", [])
                copy_to_path = overlay.get("copy_to_path")
                if drop_model:
                    if copy_to_path is not None or mutations not in (None, []):
                        raise AssertionError(
                            f"invalid support drop overlay: {case['case_id']}"
                        )
                    support_scenario_paths.remove(normalized_target)
                    support_scenario_values.pop(normalized_target, None)
                    continue
                if not isinstance(mutations, list) or (
                    not mutations and copy_to_path is None
                ):
                    raise AssertionError(
                        f"support overlay requires mutations or copy: {case['case_id']}"
                    )
                destination_path = normalized_target
                if copy_to_path is not None:
                    if not isinstance(copy_to_path, str):
                        raise TypeError(
                            f"support copy target is invalid: {case['case_id']}"
                        )
                    destination_path = copy_to_path.removeprefix(
                        fixture_root_prefix
                    )
                    if not destination_path.startswith("support/"):
                        raise AssertionError(
                            f"support copy escapes support root: {case['case_id']}"
                        )
                    if destination_path in support_scenario_paths:
                        raise AssertionError(
                            f"support copy target exists {destination_path}: "
                            f"{case['case_id']}"
                        )
                if normalized_target not in support_scenario_values:
                    support_path = FIXTURE_ROOT / normalized_target
                    support_scenario_values[normalized_target] = (
                        _load_json_without_duplicates(
                            support_path.read_bytes(), support_path.as_posix()
                        )
                    )
                support_value = copy.deepcopy(
                    support_scenario_values[normalized_target]
                )
                for mutation in mutations:
                    support_value = _apply_catalog_mutation(
                        support_value, mutation
                    )
                support_scenario_values[destination_path] = support_value
                support_scenario_paths.add(destination_path)
            continue

        overlays = case["overlays"]
        scenario_values = copy.deepcopy(corpus.values)
        for overlay in overlays:
            if not isinstance(overlay, dict):
                raise TypeError(
                    f"scenario overlay is not an object: {case['case_id']}"
                )
            target_path = overlay.get("target_path")
            if target_path not in scenario_values:
                raise AssertionError(
                    f"unknown overlay target {target_path}: {case['case_id']}"
                )
            drop_model = overlay.get("drop_model", False)
            if not isinstance(drop_model, bool):
                raise TypeError(
                    f"overlay drop_model is not boolean: {case['case_id']}"
                )
            if drop_model:
                if overlay.get("copy_to_path") is not None or overlay.get(
                    "mutations"
                ) not in (None, []):
                    raise AssertionError(
                        f"invalid drop overlay shape: {case['case_id']}"
                    )
                del scenario_values[target_path]
                continue
            copy_to_path = overlay.get("copy_to_path")
            destination_path = copy_to_path or target_path
            if copy_to_path is not None and copy_to_path in scenario_values:
                raise AssertionError(
                    f"overlay copy target exists {copy_to_path}: {case['case_id']}"
                )
            mutations = overlay.get("mutations", [])
            if not isinstance(mutations, list) or (
                not mutations and copy_to_path is None
            ):
                raise AssertionError(
                    f"overlay requires mutations or an exact copy: {case['case_id']}"
                )
            value = copy.deepcopy(scenario_values[target_path])
            for mutation in mutations:
                value = _apply_catalog_mutation(value, mutation)
            scenario_values[destination_path] = value

    invalid_document = {"invalid_cases": invalid_cases}
    invalid_path = FIXTURE_ROOT / "invalid-mutations.json"
    invalid_path.write_bytes(canonical_bytes(invalid_document))

    schema_paths = sorted(SCHEMA_ROOT.glob("*.schema.json"))
    expected_schema_files = [path.name for path in schema_paths]
    schema_by_name = {
        path.name: _load_json_without_duplicates(path.read_bytes(), path.as_posix())
        for path in schema_paths
    }
    schema_registry = Registry().with_resources(
        [
            (
                schema["$id"],
                Resource.from_contents(schema, default_specification=DRAFT202012),
            )
            for schema in schema_by_name.values()
        ]
    )
    for schema in schema_by_name.values():
        Draft202012Validator.check_schema(schema)

    covered_schema_files = {entry["schema_file"] for entry in corpus.entries}
    expected_instance_schema_files = set(expected_schema_files) - {
        "governance-common.schema.json",
    }
    if covered_schema_files != expected_instance_schema_files:
        raise AssertionError(
            "positive schema coverage mismatch: "
            f"missing={sorted(expected_instance_schema_files-covered_schema_files)}, "
            f"extra={sorted(covered_schema_files-expected_instance_schema_files)}"
        )
    for entry in corpus.entries:
        path = FIXTURE_ROOT / entry["path"]
        raw = path.read_bytes()
        value = _load_json_without_duplicates(raw, entry["path"])
        if raw != canonical_bytes(value):
            raise AssertionError(f"not RFC 8785 canonical JSON: {entry['path']}")
        schema = schema_by_name[entry["schema_file"]]
        validator = Draft202012Validator(
            schema,
            registry=schema_registry,
            format_checker=FormatChecker(),
        )
        errors = sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
        if errors:
            details = "; ".join(
                f"/{'/'.join(map(str, error.absolute_path))}: {error.message}"
                for error in errors[:8]
            )
            raise AssertionError(f"schema-invalid fixture {entry['path']}: {details}")

    # Verify every quorum signature independently from the manifest's fixed test public keys.
    for role, public_entry in corpus.relationships[
        "test_only_public_keys_by_role_slot"
    ].items():
        signature_path = VALID_ROOT / f"release-quorum-signature-{role}.json"
        signature_value = _load_json_without_duplicates(
            signature_path.read_bytes(), signature_path.as_posix()
        )
        signed_payload = signature_value["signed_payload"]
        domain = signed_payload["signature_domain"].encode("ascii")
        message = domain + b"\x00" + canonical_bytes(signed_payload)
        public_raw = base64.b64decode(public_entry["public_key_base64"], validate=True)
        if sha(public_raw) != public_entry["fingerprint_sha256"]:
            raise AssertionError(f"test key fingerprint mismatch for {role}")
        Ed25519PrivateKey.from_private_bytes(
            hashlib.sha256(f"YLX SYNTHETIC TEST KEY ONLY:{role}".encode("ascii")).digest()
        ).public_key().verify(
            base64.b64decode(signature_value["signature_b64"], validate=True), message
        )

    corpus.relationships.update(
        {
            "registry_requirement_ids": requirement_ids,
            "m4_selected_requirement_ids": m4_ids,
            "m4_selection_rule": "closing_gate starts with literal M4",
            "partition_digest_rules": {
                "group_id_set": "SHA-256(RFC8785(sorted JSON string array))",
                "source_digest_set": "SHA-256(RFC8785(sorted JSON string array))",
                "sample_set": (
                    "SHA-256(concatenated sorted ASCII records "
                    "sample_id<TAB>sample_kind<TAB>sample_sha256<LF>)"
                ),
                "group_key": "SHA-256(RFC8785(group_key object))",
            },
            "canonical_json_paths": sorted(corpus.values),
            "sha256_by_valid_fixture_path": dict(sorted(corpus.digests.items())),
        }
    )

    invalid_rel = invalid_path.relative_to(FIXTURE_ROOT).as_posix()
    expected_files = sorted(
        set(corpus.values)
        | {entry["path"] for entry in corpus.support_entries}
        | {
            "fixture-manifest.json",
            invalid_rel,
            "generate_fixtures.py",
            "invalid_case_catalog.py",
        }
    )
    actual_paths = {
        path.relative_to(FIXTURE_ROOT).as_posix()
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    # The manifest is the last generated file. Include that imminent write when
    # checking the exact final inventory.
    actual_paths.add("fixture-manifest.json")
    undeclared_paths = actual_paths - set(expected_files)
    missing_paths = set(expected_files) - actual_paths
    if undeclared_paths or missing_paths:
        raise AssertionError(
            "fixture inventory mismatch: "
            f"undeclared={sorted(undeclared_paths)}, missing={sorted(missing_paths)}"
        )
    manifest = {
        "schema_version": "1.0",
        "notice": NOTICE,
        "test_only_cryptography_notice": (
            "All Ed25519 public keys, signatures, identities, and deterministic private-key "
            "seeds in this corpus are synthetic and test-only; no production secret is present."
        ),
        "expected_schema_files": expected_schema_files,
        "valid_fixtures": corpus.entries,
        "invalid_cases": invalid_cases,
        "support_files": corpus.support_entries,
        "relationships": corpus.relationships,
        "expected_files": expected_files,
        "non_fixture_files": [
            "generate_fixtures.py",
            "invalid_case_catalog.py",
        ],
        "counts": {
            "schema_files": len(expected_schema_files),
            "valid_fixtures": len(corpus.entries),
            "invalid_cases": len(invalid_cases),
            "support_files": len(corpus.support_entries),
            "registry_requirements": len(requirement_ids),
            "m4_selected_requirements": len(m4_ids),
        },
    }
    if manifest["notice"] != NOTICE:
        raise AssertionError("manifest notice mismatch")
    for entry in manifest["valid_fixtures"]:
        if entry["notice"] != NOTICE:
            raise AssertionError(f"valid entry notice mismatch: {entry['case_id']}")
    (FIXTURE_ROOT / "fixture-manifest.json").write_bytes(canonical_bytes(manifest))


_GENERATOR_SOURCE_FILES = (
    "generate_fixtures.py",
    "invalid_case_catalog.py",
)
_GENERATED_DIRECTORIES = ("valid", "support")
_GENERATED_FILES = ("invalid-mutations.json", "fixture-manifest.json")


@contextmanager
def fixture_output_root(output_root: Path):
    """Temporarily direct generator-owned writes to an isolated root."""

    global FIXTURE_ROOT, VALID_ROOT, SUPPORT_ROOT
    previous = (FIXTURE_ROOT, VALID_ROOT, SUPPORT_ROOT)
    FIXTURE_ROOT = output_root
    VALID_ROOT = output_root / "valid"
    SUPPORT_ROOT = output_root / "support"
    try:
        yield
    finally:
        FIXTURE_ROOT, VALID_ROOT, SUPPORT_ROOT = previous


@contextmanager
def staged_fixture_corpus():
    """Build and validate a corpus without touching the shared fixture root."""

    shared_root = FIXTURE_ROOT
    with tempfile.TemporaryDirectory(prefix="ylx-governance-fixtures-") as temp_dir:
        stage_root = Path(temp_dir)
        for filename in _GENERATOR_SOURCE_FILES:
            shutil.copy2(shared_root / filename, stage_root / filename)
        with fixture_output_root(stage_root):
            build()
        yield stage_root


def fixture_file_map(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def check_staged_corpus(stage_root: Path) -> tuple[list[str], list[str], list[str]]:
    """Return missing, undeclared, and byte-different shared fixture paths."""

    staged_files = fixture_file_map(stage_root)
    manifest = _load_json_without_duplicates(
        staged_files["fixture-manifest.json"],
        "fixture-manifest.json",
    )
    expected_files = set(manifest["expected_files"])
    if set(staged_files) != expected_files:
        raise AssertionError(
            "staged fixture inventory does not equal its manifest: "
            f"undeclared={sorted(set(staged_files) - expected_files)}, "
            f"missing={sorted(expected_files - set(staged_files))}"
        )
    shared_files = fixture_file_map(FIXTURE_ROOT)
    shared_paths = set(shared_files)
    missing = sorted(expected_files - shared_paths)
    undeclared = sorted(shared_paths - expected_files)
    changed = sorted(
        path
        for path in expected_files & shared_paths
        if shared_files[path] != staged_files[path]
    )
    return missing, undeclared, changed


def publish_staged_corpus(stage_root: Path) -> None:
    """Publish a fully generated corpus, leaving generator source files intact."""

    swaps: list[tuple[Path, Path, Path]] = []
    for dirname in _GENERATED_DIRECTORIES:
        target = FIXTURE_ROOT / dirname
        replacement = FIXTURE_ROOT / f".{dirname}.next"
        backup = FIXTURE_ROOT / f".{dirname}.previous"
        if replacement.exists() or backup.exists():
            raise FileExistsError(
                f"refusing to overwrite interrupted fixture swap for {dirname}"
            )
        shutil.copytree(stage_root / dirname, replacement)
        swaps.append((target, replacement, backup))

    staged_files: list[tuple[Path, Path]] = []
    for filename in _GENERATED_FILES:
        target = FIXTURE_ROOT / filename
        replacement = FIXTURE_ROOT / f".{filename}.next"
        if replacement.exists():
            raise FileExistsError(
                f"refusing to overwrite interrupted fixture write for {filename}"
            )
        shutil.copy2(stage_root / filename, replacement)
        staged_files.append((target, replacement))

    try:
        for target, replacement, backup in swaps:
            if target.exists():
                target.rename(backup)
            replacement.rename(target)
        for target, replacement in staged_files:
            replacement.replace(target)
    except Exception:
        for target, replacement, backup in reversed(swaps):
            if backup.exists():
                if target.exists():
                    shutil.rmtree(target)
                backup.rename(target)
            if replacement.exists():
                shutil.rmtree(replacement)
        for _, replacement in staged_files:
            if replacement.exists():
                replacement.unlink()
        raise
    else:
        for _, _, backup in swaps:
            if backup.exists():
                shutil.rmtree(backup)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the deterministic closed governance-model fixture corpus. "
            "Generation is staged and validated before shared fixture bytes change."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that shared fixture bytes equal a fresh staged generation",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    with staged_fixture_corpus() as stage_root:
        if args.check:
            missing, undeclared, changed = check_staged_corpus(stage_root)
            if missing or undeclared or changed:
                print(
                    "governance fixture corpus is stale: "
                    f"missing={missing}, undeclared={undeclared}, changed={changed}",
                    file=sys.stderr,
                )
                return 1
            print("Governance fixture corpus is current.")
            return 0
        publish_staged_corpus(stage_root)
        missing, undeclared, changed = check_staged_corpus(stage_root)
        if missing or undeclared or changed:
            raise AssertionError(
                "published fixture corpus differs from validated staging bytes: "
                f"missing={missing}, undeclared={undeclared}, changed={changed}"
            )
    print("Governance fixture corpus generated and validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
