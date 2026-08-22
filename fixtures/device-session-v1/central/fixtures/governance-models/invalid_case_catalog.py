"""Mutation catalog for synthetic governance-model negative fixtures.

A case may apply one RFC 6901 mutation or a coordinated multi-model overlay.
Overlays may clone immutable artifacts to express competing successors and
forks.  Signed-payload mutations must be re-signed by the fixture generator
unless the expected oracle is SIGNATURE_INVALID.  CANONICAL_JSON_INVALID cases
must retain deliberately noncanonical raw bytes after semantic mutation.
"""

from __future__ import annotations

from typing import Any

_BAD_SHA = "0" * 64
_ALT_SHA = "f" * 64
_BAD_COMMIT = "2" * 40
_INVALID_ED25519_SIGNATURE = "A" * 86 + "=="
_NONCANONICAL_ED25519_SIGNATURE = "A" * 85 + "B=="


STABLE_CODES = frozenset(
    {
        "SCHEMA_VALIDATION_FAILED",
        "PLANNING_REVISION_CHAIN_INVALID",
        "PLANNING_ARTIFACT_MAP_INVALID",
        "PLANNING_OWNER_COVERAGE_INVALID",
        "PLANNING_CALENDAR_COVERAGE_INVALID",
        "PLANNING_WBS_COVERAGE_INVALID",
        "PLANNING_AUTHORIZATION_INVALID",
        "PLANNING_DAG_CYCLE",
        "PLANNING_CPM_INVALID",
        "PLANNING_RESOURCE_OVERALLOCATION",
        "PLANNING_BLOCKED_ABSOLUTE_FORECAST",
        "PLANNING_FINAL_RECONCILIATION_INVALID",
        "PLANNING_FINAL_DURABILITY_INVALID",
        "MEASUREMENT_QUEUE_INVALID",
        "CONTEXT_STAGE_BODY_INVALID",
        "CONTEXT_LINEAGE_INVALID",
        "CONTEXT_REF_CYCLE",
        "EVIDENCE_EXECUTION_COVERAGE_INVALID",
        "EVIDENCE_ACTOR_DEPLOYMENT_MISMATCH",
        "DATA_PARTITION_OVERLAP",
        "DATA_PARTITION_DIGEST_INVALID",
        "QUALIFICATION_PREDECESSOR_INVALID",
        "QUALIFICATION_ORACLE_PARTITION_INVALID",
        "CONTRACT_DIGEST_MISMATCH",
        "M4_COMPONENT_SET_INVALID",
        "M4_CANDIDATE_LINEAGE_INVALID",
        "M4_GRAPH_COVERAGE_INVALID",
        "M4_GRAPH_CYCLE",
        "M4_GRAPH_UNKNOWN_NODE",
        "M4_GRAPH_STALE",
        "M4_UNEXPLAINED_DIFF",
        "M4_AFFECTED_SET_INVALID",
        "M4_ALL_UP_REBASE_INVALID",
        "M4_TARGET_BINDING_INVALID",
        "M4_NA_REBASE_FORBIDDEN",
        "M4_CONTROL_PLANE_PROVENANCE_INVALID",
        "ISSUE_ARCHIVE_BYTES_INVALID",
        "ISSUE_SLICE_INVALID",
        "ISSUE_HEAD_CHAIN_INVALID",
        "ISSUE_HEAD_FORK",
        "ISSUE_REVISION_REUSE",
        "ISSUE_HEAD_STALE",
        "ISSUE_GATE_VERDICT_INVALID",
        "ISSUE_RECONCILIATION_INVALID",
        "G0_RATIFICATION_INVALID",
        "M0_BOOTSTRAP_GRAPH_INVALID",
        "M0_OPERATION_AUTHORITY_INVALID",
        "EXECUTION_AUTHORIZATION_INVALID",
        "RELEASE_RESULT_MAP_INVALID",
        "RELEASE_PROJECTION_INVALID",
        "RELEASE_PROJECTION_DURABILITY_INVALID",
        "RELEASE_OPERATION_ASSIGNMENT_INVALID",
        "RELEASE_NA_MAP_INVALID",
        "RELEASE_EVIDENCE_COVERAGE_INVALID",
        "RELEASE_ATTESTATION_SET_INVALID",
        "RELEASE_CONSUMER_SET_INVALID",
        "M5_COHERENT_FAMILY_INVALID",
        "M5_FRESHNESS_INVALID",
        "CONTENT_ADDRESS_INVALID",
        "CANONICAL_JSON_INVALID",
        "SIGNATURE_INVALID",
        "KEY_FINGERPRINT_INVALID",
        "QUORUM_ROLE_SET_INVALID",
        "QUORUM_PERSON_DISTINCTNESS",
        "SIGNATURE_DOMAIN_INVALID",
        "SIGNATURE_ASSIGNMENT_INVALID",
        "SIGNING_HEAD_STALE",
        "SIGNING_KEY_TIME_INVALID",
        "SIGNING_KEY_REVOKED_AT_SIGNING",
        "QA_INDEPENDENCE_INVALID",
        "GA_OPERATOR_SUBSTITUTION",
        "GA_TARGET_MISMATCH",
        "GA_REBUILD_FORBIDDEN",
        "GA_OVERWRITE_FORBIDDEN",
        "GA_READBACK_INVALID",
        "FENCE_ATTEMPT_MISMATCH",
        "FENCE_PARALLEL_ATTEMPT",
        "FENCE_TERMINATED_INPUT_REUSE",
        "GA_VISIBILITY_INVALID",
        "FINAL_AUTHORITY_STALE",
        "FINAL_RESULT_MAP_MISMATCH",
        "FINAL_SELF_HASH_FORBIDDEN",
        "FINAL_LOCATOR_READBACK_MISSING",
        "TERMINAL_FRESHNESS_INVALID",
        "FINALIZED_CAS_DRIFT_NOT_ABORTED",
        "FINALIZED_READBACK_DRIFT_RELEASE_FORBIDDEN",
    }
)


def _case(
    case_id: str,
    target: str,
    op: str,
    pointer: str,
    stage: str,
    code: str,
    notice: str,
    value: Any = None,
    *,
    raw_encoding: str | None = None,
) -> dict[str, Any]:
    mutation: dict[str, Any] = {"op": op, "pointer": pointer}
    if op != "remove":
        mutation["value"] = value
    if raw_encoding is not None:
        mutation["raw_encoding"] = raw_encoding
    return {
        "case_id": case_id,
        "target_path": f"valid/{target}",
        "mutation": mutation,
        "expected_stage": stage,
        "expected_code": code,
        "notice": notice,
    }


def _scenario_case(
    case_id: str,
    overlays: list[dict[str, Any]],
    stage: str,
    code: str,
    notice: str,
    *,
    expected_error_substring: str | None = None,
) -> dict[str, Any]:
    result = {
        "case_id": case_id,
        "overlays": overlays,
        "expected_stage": stage,
        "expected_code": code,
        "notice": notice,
    }
    if expected_error_substring is not None:
        result["expected_error_substring"] = expected_error_substring
    return result


def _support_case(
    case_id: str,
    support_overlays: list[dict[str, Any]],
    stage: str,
    code: str,
    notice: str,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "support_overlays": support_overlays,
        "expected_stage": stage,
        "expected_code": code,
        "notice": notice,
    }


def build_invalid_cases(
    notice: str, exact_refs: dict[str, Any]
) -> list[dict]:
    """Return the complete deterministic negative-fixture mutation corpus."""

    fixture_projection_prefix = (
        "contracts/fixtures/governance-models/valid/"
    )
    release_projection_v2_current_ref = exact_refs[
        "release_projection_v2_current_ref"
    ]
    release_projection_v2_predecessor_ref = exact_refs[
        "release_projection_v2_predecessor_ref"
    ]
    wrong_action_authority_ref = exact_refs["wrong_action_authority_ref"]
    wrong_type_authority_ref = exact_refs["wrong_type_authority_ref"]
    wrong_release_operation_assignment_ref = exact_refs[
        "wrong_release_operation_assignment_ref"
    ]
    final_bundle_ref = exact_refs["final_bundle_ref"]
    final_publication_ref = exact_refs["final_publication_ref"]
    final_ordinary_task_node_id = exact_refs["final_ordinary_task_node_id"]
    final_terminal_evidence_ref = exact_refs["final_terminal_evidence_ref"]
    final_bundle_byte_length = exact_refs["final_bundle_byte_length"]
    unrelated_release_authorizing_e_ref = exact_refs[
        "unrelated_release_authorizing_e_ref"
    ]
    unrelated_release_authorizing_e_source_ref = exact_refs[
        "unrelated_release_authorizing_e_source_ref"
    ]
    measurement_training_selection_ref = exact_refs[
        "measurement_training_selection_ref"
    ]
    measurement_holdout_selection_ref = exact_refs[
        "measurement_holdout_selection_ref"
    ]
    measurement_training_evidence_ref = exact_refs[
        "measurement_training_evidence_ref"
    ]
    measurement_holdout_evidence_ref = exact_refs[
        "measurement_holdout_evidence_ref"
    ]
    measurement_training_evaluation_ref = exact_refs[
        "measurement_training_evaluation_ref"
    ]
    measurement_partition_ref = exact_refs["measurement_partition_ref"]
    measurement_freeze_method_ref = exact_refs[
        "measurement_freeze_method_ref"
    ]
    measurement_statistical_method_ref = exact_refs[
        "measurement_statistical_method_ref"
    ]
    measurement_threshold_term = exact_refs["measurement_threshold_term"]
    measurement_duplicate_metric_term = {
        **measurement_threshold_term,
        "value": measurement_threshold_term["value"] + 1,
    }
    measurement_training_group_ids = exact_refs[
        "measurement_training_group_ids"
    ]
    measurement_holdout_group_ids = exact_refs[
        "measurement_holdout_group_ids"
    ]
    measurement_training_source_set_sha256 = exact_refs[
        "measurement_training_source_set_sha256"
    ]
    measurement_training_sample_set_sha256 = exact_refs[
        "measurement_training_sample_set_sha256"
    ]
    measurement_queue_previous_registry_binding = exact_refs[
        "measurement_queue_previous_registry_binding"
    ]
    measurement_queue_r1_registry_binding = exact_refs[
        "measurement_queue_r1_registry_binding"
    ]
    measurement_queue_self_predecessor_ref = exact_refs[
        "measurement_queue_self_predecessor_ref"
    ]
    measurement_queue_r3_self_predecessor_content_sha256 = exact_refs[
        "measurement_queue_r3_self_predecessor_content_sha256"
    ]
    measurement_queue_r3_bogus_predecessor_content_sha256 = exact_refs[
        "measurement_queue_r3_bogus_predecessor_content_sha256"
    ]
    measurement_queue_r3_bogus_selected_head_content_sha256 = exact_refs[
        "measurement_queue_r3_bogus_selected_head_content_sha256"
    ]
    measurement_queue_r3_stale_r1_binding_content_sha256 = exact_refs[
        "measurement_queue_r3_stale_r1_binding_content_sha256"
    ]
    measurement_queue_r3_stale_previous_binding_content_sha256 = exact_refs[
        "measurement_queue_r3_stale_previous_binding_content_sha256"
    ]
    measurement_wrong_verdict_ref = exact_refs[
        "measurement_wrong_verdict_ref"
    ]
    planning_semantic = exact_refs["planning_semantic"]
    g0 = exact_refs["g0_support"]
    g0_paths = g0["path_by_kind"]
    g0_approval_paths = g0["approval_path_by_role"]
    g0_approval_import_receipt_paths = g0[
        "approval_import_receipt_path_by_role"
    ]
    g0_grant_paths = g0["grant_path_by_kind"]
    g0_roles = g0["role_ids"]
    assert set(g0_roles) == {"release-owner", "security-owner"}
    g0_approval_sha_by_role = g0["approval_sha256_by_required_role"]
    g0_person_by_role = g0["approver_person_by_role"]
    g0_five_path_sha256_by_path = g0[
        "exact_five_path_sha256_by_path"
    ]
    g0_first_five_path = min(g0_five_path_sha256_by_path)
    m0 = exact_refs["m0_support"]
    m0_paths = m0["path_by_kind"]
    m0_approval_paths = m0["approval_path_by_role"]
    m0_approval_publication_paths = m0[
        "approval_publication_path_by_role"
    ]
    m0_approval_readback_paths = m0["approval_readback_path_by_role"]
    m0_child_publication_paths = m0["child_publication_path_by_kind"]
    m0_child_readback_paths = m0["child_readback_path_by_kind"]
    m0_grant_model_paths = m0[
        "operation_authority_model_path_by_step_and_kind"
    ]
    m0_grant_refs = m0["operation_authority_ref_by_step_and_kind"]
    m0_grant_sha256 = m0[
        "operation_authority_sha256_by_step_and_kind"
    ]
    m0_planning_roles = m0["planning_role_ids"]
    m0_child_kinds = m0["remaining_child_kinds"]
    m0_issue_approval_roles = m0["issue_approval_role_ids"]
    m0_issue_approval_index = m0["issue_approval_index_by_role"]
    m0_times = m0["timestamp_by_event"]
    assert set(m0_planning_roles) == {
        "release-owner",
        "build-platform-owner",
        "qa-evidence-owner",
    }
    assert set(m0_child_kinds) == {
        "resource_calendar",
        "delivery_wbs",
        "forecast_snapshot",
    }
    assert len(m0_issue_approval_roles) >= 2
    assert m0["q_absent_role_id"] not in m0_issue_approval_roles
    m0_first_issue_role = m0_issue_approval_roles[0]
    m0_second_issue_role = m0_issue_approval_roles[1]
    m0_first_planning_role = min(
        m0_planning_roles,
        key=lambda role: m0_times["approval_approved_by_role"][role],
    )
    m0_first_child_kind = min(
        m0_child_kinds,
        key=lambda kind: m0_times["child_published_by_kind"][kind],
    )
    m0_last_child_kind = max(
        m0_child_kinds,
        key=lambda kind: m0_times["child_readback_by_kind"][kind],
    )
    current_projection_path = release_projection_v2_current_ref.get(
        "artifact_path", ""
    )
    assert current_projection_path.startswith(fixture_projection_prefix)
    current_projection_target = current_projection_path.removeprefix(
        fixture_projection_prefix
    )
    final_bundle_path = final_bundle_ref.get("artifact_path", "")
    assert final_bundle_path.startswith(fixture_projection_prefix)
    final_bundle_target = final_bundle_path.removeprefix(
        fixture_projection_prefix
    )
    final_publication_path = final_publication_ref.get("artifact_path", "")
    assert final_publication_path.startswith(fixture_projection_prefix)
    final_publication_target = final_publication_path.removeprefix(
        fixture_projection_prefix
    )
    final_readback_target = "final-actual-variance-readback-receipt.json"
    final_publication_evidence_ref = {
        **final_publication_ref,
        "revision": 1,
    }

    def pointer_token(value: str) -> str:
        return value.replace("~", "~0").replace("/", "~1")

    def fixture_locator(relative_path: str) -> str:
        fixture_prefix = "contracts/fixtures/governance-models/"
        return (
            relative_path
            if relative_path.startswith(fixture_prefix)
            else f"{fixture_prefix}{relative_path}"
        )

    def suffixed_path(path: str, suffix: str) -> str:
        assert path.endswith(".json")
        return f"{path.removesuffix('.json')}-{suffix}.json"

    def mutation(op: str, pointer: str, value: Any = None) -> dict[str, Any]:
        result: dict[str, Any] = {"op": op, "pointer": pointer}
        if op != "remove":
            result["value"] = value
        return result

    def overlay(
        target_path: str,
        *mutations: dict[str, Any],
        copy_to_path: str | None = None,
        drop_model: bool = False,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {"target_path": target_path}
        if drop_model:
            result["drop_model"] = True
            return result
        result["mutations"] = list(mutations)
        if copy_to_path is not None:
            result["copy_to_path"] = copy_to_path
        return result

    def planning_forecast_case(case_id: str) -> dict[str, Any]:
        return _scenario_case(
            case_id,
            [
                overlay(
                    "valid/forecast-snapshot-v2-planning.json",
                    *planning_semantic["forecast_mutations_by_case_id"][case_id],
                ),
                overlay(
                    "valid/delivery-planning-bundle-v2.json",
                    mutation(
                        "replace",
                        "/artifacts/forecast_snapshot/artifact_sha256",
                        planning_semantic["forecast_sha256_by_case_id"][case_id],
                    ),
                ),
            ],
            "procedural",
            "PLANNING_CPM_INVALID",
            notice,
        )

    def planning_input_case(
        case_id: str, *, code: str = "PLANNING_CPM_INVALID"
    ) -> dict[str, Any]:
        definition = planning_semantic["input_mutations_by_case_id"][case_id]
        refs = planning_semantic["input_refs_by_case_id"][case_id]
        kind = definition["kind"]
        input_path = (
            "valid/delivery-wbs-v2.json"
            if kind == "delivery_wbs"
            else "valid/resource-calendar.json"
        )
        forecast_input_pointer = (
            "/delivery_wbs_sha256"
            if kind == "delivery_wbs"
            else "/resource_calendar_sha256"
        )
        bundle_input_pointer = f"/artifacts/{kind}/artifact_sha256"
        return _scenario_case(
            case_id,
            [
                overlay(input_path, *definition["mutations"]),
                overlay(
                    "valid/forecast-snapshot-v2-planning.json",
                    mutation(
                        "replace", forecast_input_pointer, refs["input_sha256"]
                    ),
                ),
                overlay(
                    "valid/delivery-planning-bundle-v2.json",
                    mutation(
                        "replace", bundle_input_pointer, refs["input_sha256"]
                    ),
                    mutation(
                        "replace",
                        "/artifacts/forecast_snapshot/artifact_sha256",
                        refs["forecast_sha256"],
                    ),
                ),
            ],
            "procedural",
            code,
            notice,
        )

    def g0_case(
        case_id: str, overlays: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return _scenario_case(
            case_id,
            overlays,
            "procedural",
            "G0_RATIFICATION_INVALID",
            notice,
        )

    def m0_case(
        case_id: str,
        overlays: list[dict[str, Any]],
        code: str,
        expected_error_substring: str,
        *,
        stage: str = "procedural",
    ) -> dict[str, Any]:
        return _scenario_case(
            case_id,
            overlays,
            stage,
            code,
            notice,
            expected_error_substring=expected_error_substring,
        )

    publisher_actual_pointer = "/publisher_task_actual"
    ordinary_actual_pointer = (
        "/final_actual_variance_reconciliation/task_actual_by_node_id/"
        f"{pointer_token(final_ordinary_task_node_id)}"
    )
    ordinary_variance_pointer = (
        "/final_actual_variance_reconciliation/task_variance_by_node_id/"
        f"{pointer_token(final_ordinary_task_node_id)}"
    )

    cases: list[dict[str, Any]] = [
        # Closed-object and required-field schema failures.
        _case(
            "INVALID-M4-CANDIDATE-ID-REUSE-01",
            "m4-candidate-assembly.json",
            "replace",
            "/predecessor_candidate_id",
            "procedural",
            "M4_CANDIDATE_LINEAGE_INVALID",
            notice,
            "fixture-target-candidate-001",
        ),
        _case(
            "INVALID-M4-CANDIDATE-PREDECESSOR-01",
            "m4-candidate-assembly.json",
            "replace",
            "/m3_binding_context_ref/artifact_sha256",
            "procedural",
            "M4_CANDIDATE_LINEAGE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-M4-CANDIDATE-CORE-DRIFT-01",
            "m4-candidate-assembly.json",
            "replace",
            "/base_core_input_projection/target_core_input_sha256_by_id/rendered-core-config",
            "procedural",
            "M4_CANDIDATE_LINEAGE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-M4-ASSEMBLY-CONTEXT-DRIFT-01",
            "binding-context-m4-target.json",
            "replace",
            "/body/product_assembly_sha256",
            "procedural",
            "CONTEXT_STAGE_BODY_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-M5-CANDIDATE-DRIFT-01",
            "binding-context-m5.json",
            "replace",
            "/body/candidate_id",
            "procedural",
            "CONTEXT_STAGE_BODY_INVALID",
            notice,
            "fixture-base-candidate-001",
        ),
        _case(
            "INVALID-SCHEMA-UNKNOWN-01",
            "owner-assignment.json",
            "add",
            "/unexpected_field",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            "must-be-rejected",
        ),
        _case(
            "INVALID-ACCEPTANCE-HISTORY-CHECKPOINT-UNKNOWN-01",
            "acceptance-registry-history-checkpoint.json",
            "add",
            "/unexpected_field",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            "must-be-rejected",
        ),
        _case(
            "INVALID-SCHEMA-MISSING-01",
            "resource-calendar.json",
            "remove",
            "/schema",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
        ),

        # Planning chain, exact artifact map, coverage, DAG, CPM, and capacity.
        _case(
            "INVALID-PLANNING-REVISION-01",
            "delivery-planning-bundle.json",
            "replace",
            "/artifacts/owner_assignment/revision",
            "procedural",
            "PLANNING_REVISION_CHAIN_INVALID",
            notice,
            2,
        ),
        _case(
            "INVALID-PLANNING-MAP-01",
            "delivery-planning-bundle.json",
            "replace",
            "/artifacts/owner_assignment/artifact_sha256",
            "procedural",
            "PLANNING_ARTIFACT_MAP_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-PLANNING-MAP-02",
            "delivery-planning-bundle.json",
            "replace",
            "/artifacts/forecast_snapshot/artifact_path",
            "procedural",
            "PLANNING_ARTIFACT_MAP_INVALID",
            notice,
            "contracts/fixtures/governance-models/valid/forecast-snapshot-input-blocked.json",
        ),
        _case(
            "INVALID-PLANNING-OWNER-01",
            "owner-assignment.json",
            "remove",
            "/assignments/0",
            "procedural",
            "PLANNING_OWNER_COVERAGE_INVALID",
            notice,
        ),
        _case(
            "INVALID-BOOTSTRAP-AUTHORIZED-REVISION-01",
            "owner-assignment-bootstrap-authority.json",
            "replace",
            "/authorized_revision",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            2,
        ),
        _case(
            "INVALID-BOOTSTRAP-AUTHORIZED-ARTIFACT-ID-01",
            "owner-assignment-bootstrap-authority.json",
            "replace",
            "/authorized_artifact_id",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            "fixture-unrelated-owner-assignment",
        ),
        _case(
            "INVALID-BOOTSTRAP-MISSING-ROLE-01",
            "owner-assignment-bootstrap-authority.json",
            "remove",
            "/authorized_role_slot_set/0",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
        ),
        _case(
            "INVALID-BOOTSTRAP-SELF-APPROVAL-01",
            "owner-assignment-bootstrap-authority.json",
            "replace",
            "/approver_identity",
            "procedural",
            "PLANNING_OWNER_COVERAGE_INVALID",
            notice,
            "fixture-organizational-authority-issuer",
        ),
        _case(
            "INVALID-BOOTSTRAP-EXPIRED-01",
            "owner-assignment-bootstrap-authority.json",
            "replace",
            "/expires_at",
            "procedural",
            "PLANNING_OWNER_COVERAGE_INVALID",
            notice,
            "2026-05-31T00:00:00Z",
        ),
        _case(
            "INVALID-BOOTSTRAP-STALE-EVIDENCE-01",
            "owner-assignment-bootstrap-authority.json",
            "replace",
            "/authority_evidence_ref/sha256",
            "procedural",
            "PLANNING_OWNER_COVERAGE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-BOOTSTRAP-R2-REUSE-01",
            "owner-assignment-r2.json",
            "replace",
            "/source_refs/0/authority_kind",
            "procedural",
            "PLANNING_REVISION_CHAIN_INVALID",
            notice,
            "external-organizational-authority",
        ),
        _case(
            "INVALID-PLANNING-CALENDAR-01",
            "resource-calendar.json",
            "remove",
            "/resources/7",
            "procedural",
            "PLANNING_CALENDAR_COVERAGE_INVALID",
            notice,
        ),
        _case(
            "INVALID-PLANNING-WBS-01",
            "delivery-wbs.json",
            "remove",
            "/requirement_ids/0",
            "procedural",
            "PLANNING_WBS_COVERAGE_INVALID",
            notice,
        ),
        _case(
            "INVALID-PLANNING-DAG-01",
            "delivery-wbs.json",
            "replace",
            "/tasks/0/predecessors",
            "procedural",
            "PLANNING_DAG_CYCLE",
            notice,
            [
                {
                    "predecessor_status": "RESOLVED",
                    "predecessor_task_id": "fixture-all-requirements-task",
                    "relation_type": "FINISH_TO_START",
                    "lag": {"value": 0.0, "unit": "hours"},
                    "blocker": None,
                }
            ],
        ),
        _case(
            "INVALID-PLANNING-CPM-TIME-01",
            "forecast-snapshot.json",
            "replace",
            "/task_forecasts/0/dependency_only_finish",
            "procedural",
            "PLANNING_CPM_INVALID",
            notice,
            "2026-06-01T23:00:00Z",
        ),
        _case(
            "INVALID-PLANNING-CPM-FLOAT-01",
            "forecast-snapshot.json",
            "replace",
            "/task_forecasts/0/dependency_critical",
            "procedural",
            "PLANNING_CPM_INVALID",
            notice,
            False,
        ),
        _case(
            "INVALID-PLANNING-CPM-PATH-01",
            "forecast-snapshot.json",
            "replace",
            "/resource_levelled_driving_path",
            "procedural",
            "PLANNING_CPM_INVALID",
            notice,
            [],
        ),
        _case(
            "INVALID-PLANNING-RESOURCE-01",
            "resource-calendar.json",
            "replace",
            "/resources/0/capacity_intervals/0/committed_capacity",
            "procedural",
            "PLANNING_RESOURCE_OVERALLOCATION",
            notice,
            2.0,
        ),
        _case(
            "INVALID-PLANNING-WINDOW-01",
            "resource-calendar.json",
            "replace",
            "/resources/1/capacity_intervals/0/committed_capacity",
            "procedural",
            "PLANNING_RESOURCE_OVERALLOCATION",
            notice,
            2.0,
        ),
        _case(
            "INVALID-PLANNING-BLOCKED-TASK-DATE-01",
            "delivery-planning-bundle.json",
            "replace",
            "/overall_status",
            "procedural",
            "PLANNING_BLOCKED_ABSOLUTE_FORECAST",
            notice,
            "INPUT_BLOCKED",
        ),
        planning_forecast_case("INVALID-PLANNING-V2-TOTAL-FLOAT-01"),
        planning_forecast_case("INVALID-PLANNING-V2-FREE-FLOAT-01"),
        planning_forecast_case("INVALID-PLANNING-V2-DEPENDENCY-TIME-01"),
        planning_forecast_case("INVALID-PLANNING-V2-EMPTY-CRITICAL-PATH-01"),
        planning_forecast_case("INVALID-PLANNING-V2-RESOURCE-TIME-01"),
        planning_forecast_case("INVALID-PLANNING-V2-EMPTY-DRIVING-PATH-01"),
        planning_forecast_case("INVALID-PLANNING-V2-WINDOW-DELAY-01"),
        planning_forecast_case("INVALID-PLANNING-V2-ORPHAN-DECISION-01"),
        planning_forecast_case("INVALID-PLANNING-V2-MILESTONE-01"),
        planning_forecast_case(
            "INVALID-PLANNING-V2-OVERLAPPING-QUANTITY-ONE-01"
        ),
        planning_input_case("INVALID-PLANNING-V2-OUT-OF-HORIZON-01"),
        planning_input_case(
            "INVALID-PLANNING-V2-QUANTITY-EXCEEDS-CAPACITY-01"
        ),
        planning_input_case(
            "INVALID-PLANNING-V2-DUPLICATE-AGGREGATE-CAPACITY-01"
        ),
        planning_input_case(
            "INVALID-PLANNING-V2-DUPLICATE-PREDECESSOR-ID-01",
            code="PLANNING_WBS_COVERAGE_INVALID",
        ),
        planning_input_case(
            "INVALID-PLANNING-V2-REVERSED-CALENDAR-INTERVAL-01",
            code="PLANNING_WBS_COVERAGE_INVALID",
        ),
        _case(
            "INVALID-PLANNING-BLOCKED-DECISION-DATE-01",
            "forecast-snapshot-input-blocked.json",
            "replace",
            "/decision_need_bys/0/need_by",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            "2026-06-02T00:00:00Z",
        ),

        # Binding and execution context equality, lineage, and acyclicity.
        _case(
            "INVALID-CONTEXT-STAGE-BODY-01",
            "binding-context-m4.json",
            "replace",
            "/body/product_contract_sha256",
            "procedural",
            "CONTEXT_STAGE_BODY_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-CONTEXT-LINEAGE-01",
            "binding-context-m4.json",
            "replace",
            "/lineage/decision_refs/0/artifact_id",
            "procedural",
            "CONTEXT_LINEAGE_INVALID",
            notice,
            "fixture-binding-context-m4-target",
        ),
        _case(
            "INVALID-CONTEXT-WRONG-BRANCH-01",
            "binding-context-m4-target.json",
            "replace",
            "/lineage/baseline_refs/0/artifact_id",
            "procedural",
            "CONTEXT_LINEAGE_INVALID",
            notice,
            "fixture-binding-context-m4-source",
        ),
        _case(
            "INVALID-CONTEXT-SELF-REF-01",
            "binding-context-m4.json",
            "replace",
            "/lineage/baseline_refs/0/artifact_id",
            "procedural",
            "CONTEXT_REF_CYCLE",
            notice,
            "fixture-binding-context-m4-source",
        ),

        # Evidence reverse coverage and actual actor deployment binding.
        _case(
            "INVALID-EVIDENCE-COVERAGE-MISSING-01",
            "evidence-binding.json",
            "replace",
            "/reverse_coverage",
            "procedural",
            "EVIDENCE_EXECUTION_COVERAGE_INVALID",
            notice,
            [],
        ),
        _case(
            "INVALID-EVIDENCE-COVERAGE-UNKNOWN-01",
            "evidence-binding.json",
            "replace",
            "/reverse_coverage/0/evidence_ids/0",
            "procedural",
            "EVIDENCE_EXECUTION_COVERAGE_INVALID",
            notice,
            "fixture-unknown-evidence",
        ),
        _case(
            "INVALID-EVIDENCE-DEPLOYMENT-01",
            "evidence-binding.json",
            "replace",
            "/evidence_records/0/actor_deployment_record_sha256",
            "procedural",
            "EVIDENCE_ACTOR_DEPLOYMENT_MISMATCH",
            notice,
            _BAD_SHA,
        ),

        # Partition disjointness and recomputed digest proofs.
        _case(
            "INVALID-PARTITION-GROUP-OVERLAP-01",
            "data-partition.json",
            "replace",
            "/holdout_group_ids/0",
            "procedural",
            "DATA_PARTITION_OVERLAP",
            notice,
            "training-group-source",
        ),
        _case(
            "INVALID-PARTITION-SAMPLE-OVERLAP-01",
            "data-partition.json",
            "replace",
            "/source_groups/1/expanded_samples/0/sample_id",
            "procedural",
            "DATA_PARTITION_OVERLAP",
            notice,
            "sample-1",
        ),
        _case(
            "INVALID-PARTITION-SOURCE-DIGEST-OVERLAP-01",
            "data-partition.json",
            "replace",
            "/source_groups/1/source_digests/0",
            "procedural",
            "DATA_PARTITION_OVERLAP",
            notice,
            "55d8b461d9daabc823c8371e80f6e11b02dd381ee2e7eee82d24bac4483e8a78",
        ),
        _case(
            "INVALID-PARTITION-DIGEST-01",
            "data-partition.json",
            "replace",
            "/disjointness_proof/training_sample_set_sha256",
            "procedural",
            "DATA_PARTITION_DIGEST_INVALID",
            notice,
            _BAD_SHA,
        ),

        # Qualification predecessor, fitted partition, and digest layers.
        _case(
            "INVALID-QUALIFICATION-PREDECESSOR-01",
            "qualification-plan-target.json",
            "replace",
            "/predecessor_plan_sha256",
            "procedural",
            "QUALIFICATION_PREDECESSOR_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-QUALIFICATION-ORACLE-PARTITION-01",
            "qualification-plan.json",
            "replace",
            "/data_partition_ref/artifact_sha256",
            "procedural",
            "QUALIFICATION_ORACLE_PARTITION_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-CONTRACT-DIGEST-RELEASE-01",
            "qualification-plan.json",
            "replace",
            "/contract_release_sha256",
            "procedural",
            "CONTRACT_DIGEST_MISMATCH",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-CONTRACT-DIGEST-PRODUCT-01",
            "qualification-plan.json",
            "replace",
            "/product_contract_sha256",
            "procedural",
            "CONTRACT_DIGEST_MISMATCH",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-CONTRACT-DIGEST-GOVERNANCE-01",
            "qualification-plan.json",
            "replace",
            "/qualification_governance_contract_sha256",
            "procedural",
            "CONTRACT_DIGEST_MISMATCH",
            notice,
            _BAD_SHA,
        ),

        # M4 graph completeness, drift propagation, and target-only rebasing.
        _case(
            "INVALID-M4-COMPONENT-SET-01",
            "m4-component-impact-graph.json",
            "replace",
            "/component_nodes/web/component_id",
            "procedural",
            "M4_COMPONENT_SET_INVALID",
            notice,
            "network",
        ),
        _case(
            "INVALID-M4-GRAPH-COVERAGE-01",
            "m4-component-impact-graph.json",
            "replace",
            "/registry_requirement_ids/0",
            "procedural",
            "M4_GRAPH_COVERAGE_INVALID",
            notice,
            "UNKNOWN-M4-ROW-01",
        ),
        _case(
            "INVALID-M4-GRAPH-CYCLE-01",
            "m4-component-impact-graph.json",
            "replace",
            "/dependency_edges/0/target_component_id",
            "procedural",
            "M4_GRAPH_CYCLE",
            notice,
            "network",
        ),
        _case(
            "INVALID-M4-GRAPH-UNKNOWN-01",
            "m4-component-impact-graph.json",
            "replace",
            "/dependency_edges/0/target_component_id",
            "procedural",
            "M4_GRAPH_UNKNOWN_NODE",
            notice,
            "unknown-component",
        ),
        _case(
            "INVALID-M4-GRAPH-STALE-01",
            "m4-component-impact-graph.json",
            "replace",
            "/registry_sha256",
            "procedural",
            "M4_GRAPH_STALE",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-M4-UNEXPLAINED-PRODUCT-01",
            "m4-verdict-rebase.json",
            "replace",
            "/component_diff/component_sub_bundles/web/target_sub_bundle_sha256",
            "procedural",
            "M4_UNEXPLAINED_DIFF",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-M4-UNEXPLAINED-QUALIFICATION-01",
            "m4-verdict-rebase.json",
            "replace",
            "/qualification_input_diff/qualification-plan/target_sha256",
            "procedural",
            "M4_UNEXPLAINED_DIFF",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-M4-AFFECTED-SET-01",
            "m4-verdict-rebase.json",
            "replace",
            "/recomputed_affected_requirement_ids",
            "procedural",
            "M4_AFFECTED_SET_INVALID",
            notice,
            ["M4-CANDIDATE-ASSEMBLY-01"],
        ),
        _case(
            "INVALID-M4-ALL-UP-REBASE-01",
            "m4-component-impact-graph.json",
            "replace",
            "/validation_outcome/outcome",
            "procedural",
            "M4_ALL_UP_REBASE_INVALID",
            notice,
            "BLOCKED_ALL_UP",
        ),
        _case(
            "INVALID-M4-TARGET-BINDING-01",
            "m4-verdict-rebase.json",
            "replace",
            "/target_binding_context_ref/artifact_sha256",
            "procedural",
            "M4_TARGET_BINDING_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-M4-TARGET-REF-SCHEMA-01",
            "m4-verdict-rebase.json",
            "replace",
            "/target_binding_context_ref/schema",
            "procedural",
            "M4_TARGET_BINDING_INVALID",
            notice,
            "ylx.stage-source-scope.v1",
        ),
        _case(
            "INVALID-M4-EQUALITY-PROOF-DIGEST-01",
            "m4-verdict-rebase.json",
            "replace",
            "/transitive_input_equality_proof/proof_sha256",
            "procedural",
            "M4_TARGET_BINDING_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-M4-NA-CARRY-01",
            "m4-verdict-rebase.json",
            "replace",
            "/source_applicability_outcome",
            "procedural",
            "M4_NA_REBASE_FORBIDDEN",
            notice,
            "NOT_APPLICABLE",
        ),
        _case(
            "INVALID-M4-CONTROL-PLANE-PROVENANCE-MISSING-01",
            "m4-release-closure-dry-run.json",
            "remove",
            "/control_plane_provenance",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
        ),
        _case(
            "INVALID-M2-UNKNOWN-COMPONENT-INJECTION-01",
            "m2-implementation-action-receipt-build-target-disabled.json",
            "add",
            "/output_ref_by_id/unknown-component",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            {
                "artifact_id": "fixture-m2-unknown-component-build",
                "schema": "ylx.m2-dual-read-consumer-action-output.v1",
                "revision": 1,
                "artifact_path": (
                    "contracts/fixtures/governance-models/support/"
                    "m2-unknown-component-build.json"
                ),
                "artifact_sha256": _ALT_SHA,
            },
        ),
        _case(
            "INVALID-M2-CONTROL-PLANE-COMPONENT-INJECTION-01",
            "m2-implementation-action-receipt-build-target-disabled.json",
            "replace",
            "/output_ref_by_id/rp-ylx-target-session-reader",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            {
                "artifact_id": "fixture-forbidden-m2-release-controller-build",
                "schema": "ylx.release-controller-build.v1",
                "revision": 1,
                "artifact_path": (
                    "contracts/fixtures/governance-models/support/"
                    "forbidden-m2-release-controller-build.json"
                ),
                "artifact_sha256": _ALT_SHA,
            },
        ),
        _case(
            "INVALID-M4-CONTROL-PLANE-SOURCE-STALE-01",
            "m4-release-closure-dry-run.json",
            "replace",
            "/control_plane_provenance/canonical_component/source_sha256",
            "procedural",
            "M4_CONTROL_PLANE_PROVENANCE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-M4-CONTROL-PLANE-MIXED-M2-CONTROLLER-01",
            "m4-release-closure-dry-run.json",
            "add",
            "/control_plane_provenance/m2_controller_deployment_ref",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            {
                "artifact_id": "fixture-forbidden-m2-controller-deployment",
                "schema": "ylx.target-disabled-deployment-receipt.v1",
                "revision": 1,
                "artifact_path": (
                    "contracts/fixtures/governance-models/support/"
                    "forbidden-m2-controller-deployment.json"
                ),
                "artifact_sha256": _ALT_SHA,
            },
        ),
        _case(
            "INVALID-M4-CONTROL-PLANE-WRONG-M4-BUILD-01",
            "m4-release-closure-dry-run.json",
            "replace",
            "/control_plane_provenance/dry_run_exercised_build_sha256",
            "procedural",
            "M4_CONTROL_PLANE_PROVENANCE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-M4-CONTROL-PLANE-DEPLOYMENT-PROVENANCE-01",
            "m4-release-closure-dry-run.json",
            "replace",
            "/control_plane_provenance/target_disabled_deployment/build_sha256",
            "procedural",
            "M4_CONTROL_PLANE_PROVENANCE_INVALID",
            notice,
            _BAD_SHA,
        ),

        # Exact issue archive bytes, slices, and monotonic single-head history.
        _case(
            "INVALID-ISSUE-ARCHIVE-BYTES-01",
            "issue-register-head.json",
            "replace",
            "/archived_source_sha256",
            "procedural",
            "ISSUE_ARCHIVE_BYTES_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-ISSUE-SLICE-OFFSET-01",
            "issue-register-head.json",
            "replace",
            "/issue_slices_by_id/O-1/body_start_byte",
            "procedural",
            "ISSUE_SLICE_INVALID",
            notice,
            0,
        ),
        _case(
            "INVALID-ISSUE-SLICE-DIGEST-01",
            "issue-register-head.json",
            "replace",
            "/issue_slices_by_id/O-1/body_sha256",
            "procedural",
            "ISSUE_SLICE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-ISSUE-CHAIN-01",
            "issue-register-head.json",
            "replace",
            "/predecessor_head_artifact_sha256",
            "procedural",
            "ISSUE_HEAD_CHAIN_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-ISSUE-FORK-01",
            "issue-register-head.json",
            "replace",
            "/issue_register_revision",
            "procedural",
            "ISSUE_HEAD_FORK",
            notice,
            1,
        ),
        _case(
            "INVALID-ISSUE-REVISION-REUSE-01",
            "issue-register-head.json",
            "replace",
            "/archived_source_path",
            "procedural",
            "ISSUE_REVISION_REUSE",
            notice,
            "contracts/fixtures/governance-models/support/issue-register-archive-r1.md",
        ),
        _case(
            "INVALID-ISSUE-STALE-01",
            "issue-register-head.json",
            "replace",
            "/issue_register_sha256",
            "procedural",
            "ISSUE_HEAD_STALE",
            notice,
            _BAD_SHA,
        ),

        # Exact 173-row closure maps, N/A approvals, evidence, and role sets.
        _case(
            "INVALID-RELEASE-RESULT-MARKER-01",
            "pre-release-closure.json",
            "replace",
            "/current_result_map/M5-SIGNOFF-01",
            "procedural",
            "RELEASE_RESULT_MAP_INVALID",
            notice,
            "PASS",
        ),
        _case(
            "INVALID-RELEASE-RESULT-PROPOSAL-01",
            "pre-release-closure.json",
            "replace",
            "/proposed_final_result_map/M5-MATRIX-COMPLETE-01",
            "procedural",
            "RELEASE_RESULT_MAP_INVALID",
            notice,
            "PASS",
        ),
        _case(
            "INVALID-RELEASE-RESULT-MISSING-01",
            "pre-release-closure.json",
            "remove",
            "/current_result_map/M0-GOV-01",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
        ),
        _case(
            "INVALID-RELEASE-RESULT-EXTRA-01",
            "pre-release-closure.json",
            "add",
            "/current_result_map/EXTRA-REQUIREMENT-01",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            "PASS",
        ),
        _case(
            "INVALID-RELEASE-NA-MAP-01",
            "release-result-projection.json",
            "replace",
            "/row_projection_by_requirement_id/M4-ISSUES-01/approved_na_record_ref/artifact_sha256",
            "procedural",
            "RELEASE_NA_MAP_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-RELEASE-EVIDENCE-01",
            "release-result-projection.json",
            "replace",
            "/row_projection_by_requirement_id/M0-GOV-01/evidence_ids/0",
            "procedural",
            "RELEASE_EVIDENCE_COVERAGE_INVALID",
            notice,
            "fixture-unknown-evidence",
        ),
        _case(
            "INVALID-RELEASE-PROJECTION-MISSING-ROW-01",
            "release-result-projection.json",
            "remove",
            "/row_projection_by_requirement_id/M0-GOV-01",
            "procedural",
            "RELEASE_PROJECTION_INVALID",
            notice,
        ),
        _case(
            "INVALID-RELEASE-PROJECTION-DERIVED-MATRIX-01",
            "release-result-projection.json",
            "add",
            "/row_projection_by_requirement_id/M5-MATRIX-COMPLETE-01",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            {},
        ),
        _case(
            "INVALID-RELEASE-PROJECTION-DERIVED-SIGNOFF-01",
            "release-result-projection.json",
            "add",
            "/row_projection_by_requirement_id/M5-SIGNOFF-01",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            {},
        ),
        _case(
            "INVALID-RELEASE-PROJECTION-STALE-M4-TIP-01",
            "release-result-projection.json",
            "replace",
            "/effective_m4_binding_context_ref",
            "procedural",
            "RELEASE_PROJECTION_INVALID",
            notice,
            exact_refs["m4_source_binding_context_ref"],
        ),
        _case(
            "INVALID-RELEASE-PROJECTION-RESULT-ID-01",
            "release-result-projection.json",
            "replace",
            "/row_projection_by_requirement_id/M0-GOV-01/effective_result_ref/artifact_sha256",
            "procedural",
            "RELEASE_PROJECTION_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-RELEASE-PROJECTION-WRONG-STAGE-01",
            "release-result-projection.json",
            "replace",
            "/row_projection_by_requirement_id/M3-INV-COMPLETE-01/source_scope_ref/artifact_sha256",
            "procedural",
            "RELEASE_PROJECTION_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-RELEASE-PROJECTION-RESOLVABLE-WRONG-STAGE-01",
            "release-result-projection.json",
            "replace",
            "/row_projection_by_requirement_id/M3-INV-COMPLETE-01/source_scope_ref",
            "procedural",
            "RELEASE_PROJECTION_INVALID",
            notice,
            exact_refs["m4_target_binding_context_ref"],
        ),
        _case(
            "INVALID-RELEASE-PROJECTION-RESOLVABLE-WRONG-ROOT-01",
            "release-result-projection.json",
            "replace",
            "/selected_gate_root_ref_by_closing_gate/M4a",
            "procedural",
            "RELEASE_PROJECTION_INVALID",
            notice,
            exact_refs["m3_gate_root_ref"],
        ),
        _case(
            "INVALID-RELEASE-PROJECTION-RESOLVABLE-WRONG-EVIDENCE-01",
            "release-result-projection.json",
            "replace",
            "/row_projection_by_requirement_id/M0-GOV-01/evidence_binding_refs/0",
            "procedural",
            "RELEASE_EVIDENCE_COVERAGE_INVALID",
            notice,
            exact_refs["m1_evidence_binding_ref"],
        ),
        _case(
            "INVALID-RELEASE-PROJECTION-WRONG-M5-01",
            "release-result-projection.json",
            "replace",
            "/m5_binding_context_ref/artifact_sha256",
            "procedural",
            "RELEASE_PROJECTION_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-RELEASE-PROJECTION-EVIDENCE-REF-01",
            "release-result-projection.json",
            "replace",
            "/row_projection_by_requirement_id/M0-GOV-01/evidence_binding_refs/0/artifact_sha256",
            "procedural",
            "RELEASE_EVIDENCE_COVERAGE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-RELEASE-PROJECTION-NO-EVIDENCE-01",
            "release-result-projection.json",
            "remove",
            "/evidence_record_sha256_by_id/fixture-stage-evidence-m0",
            "procedural",
            "RELEASE_EVIDENCE_COVERAGE_INVALID",
            notice,
        ),
        _case(
            "INVALID-RELEASE-PROJECTION-SELF-REF-01",
            "release-result-projection.json",
            "replace",
            "/row_projection_by_requirement_id/M0-GOV-01/effective_result_ref/schema",
            "procedural",
            "RELEASE_PROJECTION_INVALID",
            notice,
            "ylx.release-result-projection.v1",
        ),
        _case(
            "INVALID-PRE-RELEASE-PROJECTION-DIGEST-01",
            "pre-release-closure.json",
            "replace",
            "/release_result_projection_sha256",
            "procedural",
            "RELEASE_PROJECTION_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-RELEASE-ATTESTATION-DIGEST-01",
            "pre-release-closure.json",
            "replace",
            "/domain_attestation_sha256_by_role_slot/capture-owner",
            "procedural",
            "RELEASE_ATTESTATION_SET_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-RELEASE-ATTESTATION-MISSING-01",
            "pre-release-closure.json",
            "remove",
            "/domain_attestation_sha256_by_role_slot/capture-owner",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
        ),
        _case(
            "INVALID-RELEASE-ATTESTATION-DUPLICATE-ROLE-01",
            "domain-attestation-capture-owner.json",
            "replace",
            "/role_id",
            "procedural",
            "RELEASE_ATTESTATION_SET_INVALID",
            notice,
            "runtime-orchestrator-owner",
        ),
        _case(
            "INVALID-RELEASE-ATTESTATION-BLOCKED-01",
            "domain-attestation-capture-owner.json",
            "replace",
            "/attestation_outcome",
            "procedural",
            "RELEASE_ATTESTATION_SET_INVALID",
            notice,
            "BLOCKED",
        ),
        _case(
            "INVALID-RELEASE-CONSUMER-DIGEST-01",
            "pre-release-closure.json",
            "replace",
            "/component_acceptance_record_sha256_by_boundary/ylx-transfer",
            "procedural",
            "RELEASE_CONSUMER_SET_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-RELEASE-CONSUMER-MISSING-01",
            "pre-release-closure.json",
            "remove",
            "/component_acceptance_record_sha256_by_boundary/ylx-transfer",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
        ),
        _case(
            "INVALID-RELEASE-CONSUMER-RECEIPT-DIGEST-01",
            "consumer-attestation-set.json",
            "replace",
            "/receipt_sha256_by_boundary/ylx-transfer",
            "procedural",
            "RELEASE_CONSUMER_SET_INVALID",
            notice,
            _BAD_SHA,
        ),

        # RFC 8785 bytes and external content-addressed readback.
        _case(
            "INVALID-CONTENT-ADDRESS-PATH-01",
            "content-addressed-locator-readback.json",
            "replace",
            "/canonical_path",
            "procedural",
            "CONTENT_ADDRESS_INVALID",
            notice,
            "contracts/fixtures/governance-models/support/0000000000000000000000000000000000000000000000000000000000000000--release-closure-manifest.json",
        ),
        _case(
            "INVALID-CONTENT-ADDRESS-READBACK-01",
            "content-addressed-locator-readback.json",
            "replace",
            "/readback_sha256",
            "procedural",
            "CONTENT_ADDRESS_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-TERMINAL-FRESHNESS-STALE-01",
            "content-addressed-locator-readback.json",
            "replace",
            "/freshness_validation/checkpoints/0/fence_bound_input_set_sha256",
            "procedural",
            "TERMINAL_FRESHNESS_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-TERMINAL-FRESHNESS-REORDERED-01",
            "content-addressed-locator-readback.json",
            "replace",
            "/freshness_validation/checkpoints/1/checkpoint",
            "procedural",
            "TERMINAL_FRESHNESS_INVALID",
            notice,
            "pre_promotion",
        ),
        _case(
            "INVALID-TERMINAL-FRESHNESS-MISSING-01",
            "content-addressed-locator-readback.json",
            "remove",
            "/freshness_validation/checkpoints/2",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
        ),
        _case(
            "INVALID-TERMINAL-FRESHNESS-CHECKED-AT-01",
            "content-addressed-locator-readback.json",
            "replace",
            "/freshness_validation/checkpoints/2/checked_at",
            "procedural",
            "TERMINAL_FRESHNESS_INVALID",
            notice,
            "2026-06-01T12:10:00Z",
        ),
        _case(
            "INVALID-FINALIZED-CAS-PRECONDITION-DRIFT-01",
            "content-addressed-locator-readback.json",
            "replace",
            "/freshness_validation/checkpoints/6/fence_bound_input_set_sha256",
            "procedural",
            "FINALIZED_CAS_DRIFT_NOT_ABORTED",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-FINALIZED-READBACK-DRIFT-RELEASE-01",
            "content-addressed-locator-readback.json",
            "replace",
            "/freshness_validation/checkpoints/7/fence_bound_input_set_sha256",
            "procedural",
            "FINALIZED_READBACK_DRIFT_RELEASE_FORBIDDEN",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-CANONICAL-JSON-01",
            "release-quorum-signature-qa-evidence-owner.json",
            "replace",
            "/signed_payload/signed_at",
            "procedural",
            "CANONICAL_JSON_INVALID",
            notice,
            "2026-06-01T12:00:00.000Z",
            raw_encoding="PRETTY_JSON",
        ),

        # Real Ed25519 verification and signed metadata/key authority semantics.
        _case(
            "INVALID-SIGNATURE-CRYPTO-01",
            "release-quorum-signature-qa-evidence-owner.json",
            "replace",
            "/signature_b64",
            "procedural",
            "SIGNATURE_INVALID",
            notice,
            "A" * 86 + "==",
        ),
        _case(
            "INVALID-SIGNATURE-FINGERPRINT-01",
            "release-quorum-signature-qa-evidence-owner.json",
            "replace",
            "/signed_payload/signing_key_fingerprint",
            "procedural",
            "KEY_FINGERPRINT_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-SIGNATURE-ROLE-SET-01",
            "release-quorum-signature-qa-evidence-owner.json",
            "replace",
            "/signed_payload/role_slot",
            "procedural",
            "QUORUM_ROLE_SET_INVALID",
            notice,
            "security-owner",
        ),
        _case(
            "INVALID-SIGNATURE-PERSON-DUPLICATE-01",
            "release-quorum-signature-qa-evidence-owner.json",
            "replace",
            "/signed_payload/person_id",
            "procedural",
            "QUORUM_PERSON_DISTINCTNESS",
            notice,
            "fixture-release-owner-person",
        ),
        _case(
            "INVALID-SIGNATURE-DOMAIN-01",
            "release-quorum-signature-qa-evidence-owner.json",
            "replace",
            "/signed_payload/signature_domain",
            "procedural",
            "SIGNATURE_DOMAIN_INVALID",
            notice,
            "ylx.release-closure.quorum.v1/security-owner",
        ),
        _case(
            "INVALID-SIGNATURE-ASSIGNMENT-PERSON-01",
            "role-signing-key-assignment-qa-evidence-owner.json",
            "replace",
            "/person_id",
            "procedural",
            "SIGNATURE_ASSIGNMENT_INVALID",
            notice,
            "fixture-unassigned-person",
        ),
        _case(
            "INVALID-SIGNATURE-ASSIGNMENT-REVISION-01",
            "release-quorum-signature-qa-evidence-owner.json",
            "replace",
            "/signed_payload/role_assignment_revision",
            "procedural",
            "SIGNATURE_ASSIGNMENT_INVALID",
            notice,
            2,
        ),
        _case(
            "INVALID-SIGNING-HEAD-STALE-01",
            "release-quorum-signature-qa-evidence-owner.json",
            "replace",
            "/signed_payload/key_validity_revocation_head_sha256",
            "procedural",
            "SIGNING_HEAD_STALE",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-SIGNING-KEY-BEFORE-VALIDITY-01",
            "release-quorum-signature-qa-evidence-owner.json",
            "replace",
            "/signed_payload/signed_at",
            "procedural",
            "SIGNING_KEY_TIME_INVALID",
            notice,
            "2025-01-01T00:00:00Z",
        ),
        _case(
            "INVALID-SIGNING-KEY-AFTER-EXPIRY-01",
            "release-quorum-signature-qa-evidence-owner.json",
            "replace",
            "/signed_payload/signed_at",
            "procedural",
            "SIGNING_KEY_TIME_INVALID",
            notice,
            "2028-01-01T00:00:00Z",
        ),
        _case(
            "INVALID-SIGNING-KEY-HORIZON-01",
            "release-quorum-signature-qa-evidence-owner.json",
            "replace",
            "/signed_payload/key_validity_at_signature/required_remaining_validity_seconds",
            "procedural",
            "SIGNING_KEY_TIME_INVALID",
            notice,
            63072000,
        ),
        _case(
            "INVALID-SIGNING-KEY-REVOKED-01",
            "signing-key-validity-revocation-head.json",
            "replace",
            "/keys_by_fingerprint/73a22cbfbd7799ed9f8e967f0caa9b85064bd2ad52d1d34bc32f4712bed4e80a",
            "procedural",
            "SIGNING_KEY_REVOKED_AT_SIGNING",
            notice,
            {
                "key_id": "fixture-key-qa-evidence-owner",
                "person_id": "fixture-person-dana",
                "public_key_base64": "PQp3ug3MKRSLTWyefOMdUF/6ppt2/ERK744PJR+aSto=",
                "valid_from": "2026-01-01T00:00:00Z",
                "not_after": "2027-01-01T00:00:00Z",
                "status": "REVOKED",
                "revocation_or_compromise_effective_at": "2026-06-01T11:00:00Z",
                "reason": "Synthetic revocation effective before signing.",
            },
        ),
        _case(
            "INVALID-QA-SAME-PRODUCER-01",
            "release-quorum-signature-qa-evidence-owner.json",
            "replace",
            "/signed_payload/person_id",
            "procedural",
            "QA_INDEPENDENCE_INVALID",
            notice,
            "fixture-result-map-producer-person",
        ),
        _case(
            "INVALID-QA-SAME-OPERATOR-01",
            "release-quorum-signature-qa-evidence-owner.json",
            "replace",
            "/signed_payload/person_id",
            "procedural",
            "QA_INDEPENDENCE_INVALID",
            notice,
            "fixture-ga-operator-person",
        ),

        # GA exact-target promotion, operator identity, and durable observation.
        _case(
            "INVALID-GA-OPERATOR-01",
            "ga-promotion-receipt.json",
            "replace",
            "/promotion_operator_person_id",
            "procedural",
            "GA_OPERATOR_SUBSTITUTION",
            notice,
            "fixture-substitute-operator-person",
        ),
        _case(
            "INVALID-GA-TARGET-COMMIT-01",
            "ga-promotion-receipt.json",
            "replace",
            "/observed_ref_target_commit",
            "procedural",
            "GA_TARGET_MISMATCH",
            notice,
            _BAD_COMMIT,
        ),
        _case(
            "INVALID-GA-TARGET-REMOTE-01",
            "ga-promotion-receipt.json",
            "replace",
            "/canonical_remote_id",
            "procedural",
            "GA_TARGET_MISMATCH",
            notice,
            "fixture-wrong-remote",
        ),
        _case(
            "INVALID-GA-REBUILD-01",
            "ga-promotion-plan.json",
            "replace",
            "/rebuild_allowed",
            "procedural",
            "GA_REBUILD_FORBIDDEN",
            notice,
            True,
        ),
        _case(
            "INVALID-GA-OVERWRITE-01",
            "ga-promotion-plan.json",
            "replace",
            "/overwrite_allowed",
            "procedural",
            "GA_OVERWRITE_FORBIDDEN",
            notice,
            True,
        ),
        _case(
            "INVALID-GA-READBACK-01",
            "ga-promotion-receipt.json",
            "replace",
            "/remote_observation_sha256",
            "procedural",
            "GA_READBACK_INVALID",
            notice,
            _BAD_SHA,
        ),

        # Single-attempt fence, termination, quarantine, and recovery rules.
        _case(
            "INVALID-FENCE-ATTEMPT-MISMATCH-01",
            "ga-promotion-receipt.json",
            "replace",
            "/attempt_id",
            "procedural",
            "FENCE_ATTEMPT_MISMATCH",
            notice,
            "fixture-wrong-release-attempt",
        ),
        _case(
            "INVALID-FENCE-PARALLEL-01",
            "release-publication-fence-terminated.json",
            "replace",
            "/canonical_ga_target",
            "procedural",
            "FENCE_PARALLEL_ATTEMPT",
            notice,
            "fixture-origin/refs/tags/v0.5.0",
        ),
        _case(
            "INVALID-FENCE-TERMINATED-REUSE-01",
            "release-publication-fence-termination.json",
            "replace",
            "/attempt_id",
            "procedural",
            "FENCE_TERMINATED_INPUT_REUSE",
            notice,
            "fixture-release-attempt-001",
        ),
        _case(
            "INVALID-GA-VISIBILITY-01",
            "ga-promotion-receipt.json",
            "replace",
            "/ga_visibility",
            "procedural",
            "GA_VISIBILITY_INVALID",
            notice,
            "CUSTOMER_VISIBLE",
        ),

        # Final authority freshness, exact proposal, no self hash, and locator.
        _case(
            "INVALID-FINAL-AUTHORITY-STALE-01",
            "release-closure-manifest.json",
            "replace",
            "/key_validity_revocation_head_sha256",
            "procedural",
            "FINAL_AUTHORITY_STALE",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-FINAL-RESULT-MAP-01",
            "release-closure-manifest.json",
            "replace",
            "/final_result_map/M5-SIGNOFF-01",
            "procedural",
            "FINAL_RESULT_MAP_MISMATCH",
            notice,
            "PASS",
        ),
        _case(
            "INVALID-FINAL-SELF-HASH-01",
            "release-closure-manifest.json",
            "add",
            "/self_sha256",
            "procedural",
            "FINAL_SELF_HASH_FORBIDDEN",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-FINAL-LOCATOR-MISSING-01",
            "content-addressed-locator-readback.json",
            "replace",
            "/artifact_sha256",
            "procedural",
            "FINAL_LOCATOR_READBACK_MISSING",
            notice,
            _BAD_SHA,
        ),

        # Complete final actual/variance reconciliation and the external,
        # non-recursive F/P/R durability closure.
        _case(
            "INVALID-PLANNING-FINAL-MISSING-TASK-ACTUAL-01",
            final_bundle_target,
            "remove",
            ordinary_actual_pointer,
            "procedural",
            "PLANNING_FINAL_RECONCILIATION_INVALID",
            notice,
        ),
        _case(
            "INVALID-PLANNING-FINAL-MISSING-TASK-VARIANCE-01",
            final_bundle_target,
            "remove",
            ordinary_variance_pointer,
            "procedural",
            "PLANNING_FINAL_RECONCILIATION_INVALID",
            notice,
        ),
        _case(
            "INVALID-PLANNING-FINAL-REBASELINE-01",
            final_bundle_target,
            "replace",
            (
                "/final_actual_variance_reconciliation/"
                "variance_baseline_bundle_sha256"
            ),
            "procedural",
            "PLANNING_FINAL_RECONCILIATION_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-PLANNING-FINAL-FORECAST-HISTORY-INCOMPLETE-01",
            final_bundle_target,
            "remove",
            "/final_actual_variance_reconciliation/accepted_forecast_history/0",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
        ),
        _scenario_case(
            "INVALID-PLANNING-FINAL-PUBLICATION-MISSING-01",
            [
                {
                    "target_path": f"valid/{final_publication_target}",
                    "drop_model": True,
                }
            ],
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
        ),
        _scenario_case(
            "INVALID-PLANNING-FINAL-PUBLICATION-DUPLICATE-01",
            [
                {
                    "target_path": f"valid/{final_publication_target}",
                    "copy_to_path": (
                        "valid/final-actual-variance-publication-"
                        "receipt-duplicate.json"
                    ),
                    "mutations": [],
                }
            ],
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
        ),
        _scenario_case(
            "INVALID-PLANNING-FINAL-READBACK-MISSING-01",
            [
                {
                    "target_path": f"valid/{final_readback_target}",
                    "drop_model": True,
                }
            ],
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
        ),
        _scenario_case(
            "INVALID-PLANNING-FINAL-READBACK-DUPLICATE-01",
            [
                {
                    "target_path": f"valid/{final_readback_target}",
                    "copy_to_path": (
                        "valid/final-actual-variance-readback-"
                        "receipt-duplicate.json"
                    ),
                    "mutations": [],
                }
            ],
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
        ),
        _case(
            "INVALID-PLANNING-FINAL-TARGET-REF-DIGEST-01",
            final_publication_target,
            "replace",
            "/final_bundle_ref/artifact_sha256",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-PLANNING-FINAL-BUNDLE-DIGEST-01",
            final_publication_target,
            "replace",
            "/final_bundle_sha256",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-PLANNING-FINAL-LOCATOR-01",
            final_publication_target,
            "replace",
            "/final_bundle_locator",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
            (
                "contracts/fixtures/governance-models/valid/"
                f"final-actual-variance/{_BAD_SHA}--"
                "delivery-planning-bundle.json"
            ),
        ),
        _case(
            "INVALID-PLANNING-FINAL-BYTE-LENGTH-01",
            final_publication_target,
            "replace",
            "/final_bundle_byte_length",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
            final_bundle_byte_length + 1,
        ),
        _case(
            "INVALID-PLANNING-FINAL-EVALUATION-TUPLE-01",
            final_publication_target,
            "replace",
            "/execution_authorization_evaluation_ref/artifact_sha256",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-PLANNING-FINAL-ACTION-TUPLE-01",
            final_publication_target,
            "replace",
            "/action_instance_id",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
            "fixture-final-plan-publication-action-mismatch",
        ),
        _case(
            "INVALID-PLANNING-FINAL-ACTOR-TUPLE-01",
            final_publication_target,
            "replace",
            "/actor_person_id",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
            "fixture-unrelated-publisher-person",
        ),
        _case(
            "INVALID-PLANNING-FINAL-PLANNED-INPUT-TUPLE-01",
            final_publication_target,
            "replace",
            "/planned_action_input_sha256",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-PLANNING-FINAL-PUBLISHER-FINISH-TUPLE-01",
            final_publication_target,
            "replace",
            f"{publisher_actual_pointer}/actual_finished_at",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
            "2026-06-02T07:59:29Z",
        ),
        _case(
            "INVALID-PLANNING-FINAL-PUBLICATION-ID-01",
            final_publication_target,
            "replace",
            "/receipt_id",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
            f"final-actual-variance-publication-{_BAD_SHA}",
        ),
        _case(
            "INVALID-PLANNING-FINAL-READBACK-ID-01",
            final_readback_target,
            "replace",
            "/receipt_id",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
            f"final-actual-variance-readback-{_BAD_SHA}",
        ),
        _case(
            "INVALID-PLANNING-FINAL-CHRONOLOGY-01",
            final_readback_target,
            "replace",
            "/read_back_at",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
            "2026-06-02T07:59:59Z",
        ),
        _case(
            "INVALID-PLANNING-FINAL-NO-EVIDENCE-EXCEPTION-01",
            final_publication_target,
            "replace",
            f"{publisher_actual_pointer}/terminal_evidence_refs",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
            [final_terminal_evidence_ref],
        ),
        _case(
            "INVALID-PLANNING-FINAL-TWO-EVIDENCE-EXCEPTIONS-01",
            final_bundle_target,
            "replace",
            f"{ordinary_actual_pointer}/terminal_evidence_refs",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
            [],
        ),
        _scenario_case(
            "INVALID-PLANNING-FINAL-ORDINARY-EVIDENCE-EXCEPTION-01",
            [
                {
                    "target_path": f"valid/{final_publication_target}",
                    "mutations": [
                        {
                            "op": "replace",
                            "pointer": (
                                f"{publisher_actual_pointer}/"
                                "terminal_evidence_refs"
                            ),
                            "value": [final_terminal_evidence_ref],
                        }
                    ],
                },
                {
                    "target_path": f"valid/{final_bundle_target}",
                    "mutations": [
                        {
                            "op": "replace",
                            "pointer": (
                                f"{ordinary_actual_pointer}/"
                                "terminal_evidence_refs"
                            ),
                            "value": [],
                        },
                    ],
                }
            ],
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
        ),
        _case(
            "INVALID-PLANNING-FINAL-EMBEDS-PUBLICATION-01",
            final_bundle_target,
            "replace",
            "/final_actual_variance_reconciliation/evidence_refs/0",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
            final_publication_evidence_ref,
        ),
        _case(
            "INVALID-PLANNING-FINAL-RELEASE-AUTHORITY-01",
            final_bundle_target,
            "replace",
            "/final_actual_variance_reconciliation/evidence_refs/0",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
            release_projection_v2_current_ref,
        ),
        _case(
            "INVALID-PLANNING-FINAL-UNRELATED-RELEASE-E-IN-F-01",
            final_bundle_target,
            "replace",
            "/source_refs/0",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
            unrelated_release_authorizing_e_source_ref,
        ),
        _case(
            "INVALID-PLANNING-FINAL-UNRELATED-RELEASE-E-IN-P-01",
            final_publication_target,
            "add",
            f"{publisher_actual_pointer}/terminal_evidence_refs/-",
            "procedural",
            "PLANNING_FINAL_DURABILITY_INVALID",
            notice,
            unrelated_release_authorizing_e_ref,
        ),

        # G0 is one closed four-key subject plus a separately authorized F/P/R
        # graph.  Every mutation below has the same stage-native umbrella code;
        # its case ID names the exact graph invariant that was broken.
        g0_case(
            "INVALID-G0-SUBJECT-MISSING-CANONICAL-01",
            [
                overlay(
                    g0_paths["subject"],
                    mutation("remove", "/canonical_governance_subject"),
                ),
                overlay(
                    g0_paths["event"],
                    mutation(
                        "replace",
                        "/subject_sha256",
                        g0["missing_canonical_subject_sha256"],
                    ),
                ),
            ],
        ),
        g0_case(
            "INVALID-G0-SUBJECT-EXTRA-KEY-01",
            [
                overlay(
                    g0_paths["subject"],
                    mutation(
                        "add",
                        "/unrecognized_policy_context",
                        "FORBIDDEN",
                    ),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-FIVE-PATH-DIGEST-DRIFT-01",
            [
                overlay(
                    g0_paths["subject"],
                    mutation(
                        "replace",
                        (
                            "/canonical_governance_subject/"
                            "exact_five_path_sha256_by_path/"
                            f"{pointer_token(g0_first_five_path)}"
                        ),
                        _BAD_SHA,
                    ),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-LOCATOR-OBSERVED-FIVE-PATH-DRIFT-01",
            [
                overlay(
                    g0_paths["canonical_locator_readback"],
                    mutation(
                        "replace",
                        (
                            "/observed_five_path_sha256_by_path/"
                            f"{pointer_token(g0_first_five_path)}"
                        ),
                        _BAD_SHA,
                    ),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-LOCATOR-OBSERVED-TREE-DRIFT-01",
            [
                overlay(
                    g0_paths["canonical_locator_readback"],
                    mutation(
                        "replace",
                        "/observed_commit_tree_sha256",
                        _BAD_SHA,
                    ),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-SUBJECT-DIGEST-DRIFT-01",
            [
                overlay(
                    g0_paths["event"],
                    mutation("replace", "/subject_sha256", _BAD_SHA),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-AUTHORITY-QUORUM-SPLICE-01",
            [
                overlay(
                    g0_paths["quorum_policy"],
                    mutation("replace", "/minimum_approval_count", 1),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-APPROVAL-ROLE-MISMATCH-01",
            [
                overlay(
                    g0_paths["event"],
                    mutation(
                        "replace",
                        "/approval_sha256_by_required_role/release-owner",
                        g0_approval_sha_by_role["security-owner"],
                    ),
                    mutation(
                        "replace",
                        "/approval_sha256_by_required_role/security-owner",
                        g0_approval_sha_by_role["release-owner"],
                    ),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-APPROVAL-PERSON-MISMATCH-01",
            [
                overlay(
                    g0_approval_paths["security-owner"],
                    mutation(
                        "replace",
                        "/approver_person_id",
                        g0_person_by_role["release-owner"],
                    ),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-APPROVAL-PERSON-CONFLICT-01",
            [
                overlay(
                    g0_paths["event"],
                    mutation(
                        "replace",
                        "/approval_sha256_by_required_role/security-owner",
                        g0_approval_sha_by_role["release-owner"],
                    ),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-APPROVAL-SIGNATURE-01",
            [
                overlay(
                    g0_approval_paths["release-owner"],
                    mutation("replace", "/signature", "AA=="),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-APPROVAL-DECISION-INVALID-01",
            [
                overlay(
                    g0_approval_paths["release-owner"],
                    mutation("replace", "/decision", "REJECT"),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-APPROVAL-EFFECT-INVALID-01",
            [
                overlay(
                    g0_approval_paths["release-owner"],
                    mutation(
                        "replace",
                        "/authority_effect",
                        "PROSPECTIVE_ONLY",
                    ),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-P-PUBLISHER-ACTOR-SPLICE-01",
            [
                overlay(
                    g0_paths["publication_receipt"],
                    mutation(
                        "replace",
                        "/publisher_id",
                        "fixture-g0-foreign-publisher",
                    ),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-P-REPOSITORY-SPLICE-01",
            [
                overlay(
                    g0_paths["publication_receipt"],
                    mutation(
                        "replace",
                        "/repository_locator",
                        "fixture://foreign-governance-repository/r1",
                    ),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-P-OPERATION-SPLICE-01",
            [
                overlay(
                    g0_grant_paths["event_repository_permission"],
                    mutation(
                        "replace", "/grant/operation", "IMPORT_EXACT"
                    ),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-P-PAYLOAD-DIGEST-SPLICE-01",
            [
                overlay(
                    g0_grant_paths["event_repository_permission"],
                    mutation(
                        "replace", "/grant/payload_sha256", _BAD_SHA
                    ),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-P-SINK-SPLICE-01",
            [
                overlay(
                    g0_paths["publication_receipt"],
                    mutation(
                        "replace",
                        "/terminal_sink_id",
                        "fixture-g0-foreign-terminal-sink",
                    ),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-P-ISSUER-SPLICE-01",
            [
                overlay(
                    g0_paths["publication_receipt"],
                    mutation(
                        "replace",
                        "/receipt_issuer_id",
                        "fixture-g0-foreign-receipt-issuer",
                    ),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-P-SIGNING-KEY-SPLICE-01",
            [
                overlay(
                    g0_paths["publication_receipt"],
                    mutation(
                        "replace",
                        "/signing_key_id",
                        "fixture-g0-foreign-receipt-key",
                    ),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-F-MISSING-01",
            [overlay(g0_paths["event"], drop_model=True)],
        ),
        g0_case(
            "INVALID-G0-F-DUPLICATE-01",
            [
                overlay(
                    g0_paths["event"],
                    copy_to_path=g0["duplicate_event_path"],
                )
            ],
        ),
        g0_case(
            "INVALID-G0-P-MISSING-01",
            [overlay(g0_paths["publication_receipt"], drop_model=True)],
        ),
        g0_case(
            "INVALID-G0-P-DUPLICATE-01",
            [
                overlay(
                    g0_paths["publication_receipt"],
                    copy_to_path=g0["duplicate_publication_path"],
                )
            ],
        ),
        g0_case(
            "INVALID-G0-R-MISSING-01",
            [overlay(g0_paths["readback_receipt"], drop_model=True)],
        ),
        g0_case(
            "INVALID-G0-R-DUPLICATE-01",
            [
                overlay(
                    g0_paths["readback_receipt"],
                    copy_to_path=g0["duplicate_readback_path"],
                )
            ],
        ),
        g0_case(
            "INVALID-G0-F-PREDECESSOR-MISMATCH-01",
            [
                overlay(
                    g0_paths["event"],
                    mutation("replace", "/revision", 2),
                    mutation(
                        "replace", "/predecessor_event_sha256", _BAD_SHA
                    ),
                    mutation(
                        "replace",
                        "/effective_at",
                        "2026-06-01T12:03:00Z",
                    ),
                    copy_to_path=g0["duplicate_event_path"],
                )
            ],
        ),
        g0_case(
            "INVALID-G0-F-SAME-REVISION-FORK-01",
            [
                overlay(
                    g0_paths["event"],
                    mutation(
                        "replace",
                        "/effective_at",
                        "2026-06-01T12:03:00Z",
                    ),
                    copy_to_path=g0["duplicate_event_path"],
                )
            ],
        ),
        g0_case(
            "INVALID-G0-P-ATTACHED-TO-FOREIGN-F-01",
            [
                overlay(
                    g0_paths["event"],
                    copy_to_path=g0["duplicate_event_path"],
                ),
                overlay(
                    g0_paths["publication_receipt"],
                    mutation(
                        "replace",
                        "/event_ref",
                        fixture_locator(g0["duplicate_event_path"]),
                    ),
                ),
            ],
        ),
        g0_case(
            "INVALID-G0-R-ATTACHED-TO-FOREIGN-P-01",
            [
                overlay(
                    g0_paths["publication_receipt"],
                    copy_to_path=g0["duplicate_publication_path"],
                ),
                overlay(
                    g0_paths["readback_receipt"],
                    mutation(
                        "replace",
                        "/publication_receipt_ref",
                        fixture_locator(g0["duplicate_publication_path"]),
                    ),
                ),
            ],
        ),
        g0_case(
            "INVALID-G0-R-NON-LATER-01",
            [
                overlay(
                    g0_paths["readback_receipt"],
                    mutation(
                        "replace", "/read_back_at", g0["published_at"]
                    ),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-R-OBSERVED-EVENT-DIGEST-MISMATCH-01",
            [
                overlay(
                    g0_paths["readback_receipt"],
                    mutation(
                        "replace", "/observed_event_sha256", _BAD_SHA
                    ),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-EFFECTIVE-CHRONOLOGY-01",
            [
                overlay(
                    g0_paths["event"],
                    mutation(
                        "replace",
                        "/effective_at",
                        "2026-06-01T12:00:03Z",
                    ),
                )
            ],
        ),
        g0_case(
            "INVALID-G0-GRANT-REPLAY-01",
            [
                overlay(
                    g0_approval_import_receipt_paths["release-owner"],
                    copy_to_path=suffixed_path(
                        g0_approval_import_receipt_paths["release-owner"],
                        "grant-replay",
                    ),
                )
            ],
        ),
        _support_case(
            "INVALID-G0-CLEAN-COMMIT-SIXTH-PATH-01",
            [
                overlay(
                    g0_paths["canonical_clean_commit"],
                    mutation(
                        "add",
                        (
                            "/exact_five_path_sha256_by_path/"
                            "docs~1evidence~1governance~1"
                            "invented-sixth-input.json"
                        ),
                        _BAD_SHA,
                    ),
                )
            ],
            "procedural",
            "G0_RATIFICATION_INVALID",
            notice,
        ),

        # M0 exact input closure C is a closed fact-only object with one exact
        # 26-step operation map.  The issue reconciliation step is a terminal
        # sink, not an ordinary repository write or readback.
        m0_case(
            "INVALID-M0-C-MISSING-REQUIRED-KEY-01",
            [
                overlay(
                    m0_paths["input_closure"],
                    mutation("remove", "/authority_effect"),
                )
            ],
            "SCHEMA_VALIDATION_FAILED",
            (
                f"{m0_paths['input_closure']}: "
                "'authority_effect' is a required property"
            ),
            stage="schema",
        ),
        m0_case(
            "INVALID-M0-C-FORBIDDEN-TOP-LEVEL-KEY-01",
            [
                overlay(
                    m0_paths["input_closure"],
                    mutation(
                        "add",
                        "/forbidden_inline_authority",
                        "fixture-forbidden-authority",
                    ),
                )
            ],
            "SCHEMA_VALIDATION_FAILED",
            (
                f"{m0_paths['input_closure']}: Additional properties are "
                "not allowed ('forbidden_inline_authority' was unexpected)"
            ),
            stage="schema",
        ),
        m0_case(
            "INVALID-M0-C-26-STEPS-MISSING-RECONCILIATION-01",
            [
                overlay(
                    m0_paths["input_closure"],
                    mutation(
                        "remove",
                        "/operation_constraint_by_step/issue-reconciliation",
                    ),
                )
            ],
            "SCHEMA_VALIDATION_FAILED",
            (
                f"{m0_paths['input_closure']}: "
                "'issue-reconciliation' is a required property"
            ),
            stage="schema",
        ),
        m0_case(
            "INVALID-M0-C-26-STEPS-EXTRA-STEP-01",
            [
                overlay(
                    m0_paths["input_closure"],
                    mutation(
                        "add",
                        "/operation_constraint_by_step/invented-step",
                        {},
                    ),
                )
            ],
            "SCHEMA_VALIDATION_FAILED",
            (
                f"{m0_paths['input_closure']}: Additional properties are "
                "not allowed ('invented-step' was unexpected)"
            ),
            stage="schema",
        ),
        m0_case(
            "INVALID-M0-C-TERMINAL-SINK-KIND-01",
            [
                overlay(
                    m0_paths["input_closure"],
                    mutation(
                        "replace",
                        (
                            "/operation_constraint_by_step/"
                            "issue-reconciliation/constraint_kind"
                        ),
                        "readback",
                    ),
                )
            ],
            "SCHEMA_VALIDATION_FAILED",
            f"{m0_paths['input_closure']}: 'terminal-sink' was expected",
            stage="schema",
        ),
        m0_case(
            "INVALID-M0-C-TERMINAL-SINK-OPERATION-01",
            [
                overlay(
                    m0_paths["input_closure"],
                    mutation(
                        "replace",
                        (
                            "/operation_constraint_by_step/"
                            "issue-reconciliation/operation"
                        ),
                        "READ_EXACT",
                    ),
                )
            ],
            "SCHEMA_VALIDATION_FAILED",
            f"{m0_paths['input_closure']}: 'EMIT_RECONCILIATION' was expected",
            stage="schema",
        ),
        m0_case(
            "INVALID-M0-C-TERMINAL-SINK-SCHEMA-01",
            [
                overlay(
                    m0_paths["input_closure"],
                    mutation(
                        "replace",
                        (
                            "/operation_constraint_by_step/"
                            "issue-reconciliation/artifact_schema"
                        ),
                        "ylx.repository-operation-receipt.v1",
                    ),
                )
            ],
            "SCHEMA_VALIDATION_FAILED",
            (
                f"{m0_paths['input_closure']}: "
                "'ylx.g0-issue-register-reconciliation-receipt.v1' "
                "was expected"
            ),
            stage="schema",
        ),

        # The two deterministic derivation records must reproduce the exact
        # owner, remaining-child, and wrapperless subject projections.
        m0_case(
            "INVALID-M0-PROJECTION-PHASE-A-OWNER-DIGEST-01",
            [
                overlay(
                    m0_paths["phase_a_derivation"],
                    mutation("replace", "/owner_payload_sha256", _BAD_SHA),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-PROJECTION-BINDING: Phase-A "
                "owner_payload_sha256 mismatch"
            ),
        ),
        m0_case(
            "INVALID-M0-PROJECTION-PHASE-B-CALENDAR-DIGEST-01",
            [
                overlay(
                    m0_paths["phase_b_derivation"],
                    mutation(
                        "replace",
                        (
                            "/candidate_sha256_by_artifact_kind/"
                            "resource_calendar"
                        ),
                        _BAD_SHA,
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-PROJECTION-BINDING: Phase-B resource_calendar "
                "candidate digest mismatch"
            ),
        ),
        m0_case(
            "INVALID-M0-PROJECTION-PHASE-B-SUBJECT-DIGEST-01",
            [
                overlay(
                    m0_paths["phase_b_derivation"],
                    mutation(
                        "replace",
                        "/bundle_subject_projection_sha256",
                        _BAD_SHA,
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-PROJECTION-BINDING: Phase-B bundle subject "
                "projection mismatch"
            ),
        ),

        # Operation authority stays outside candidate semantics.  Every
        # envelope is signed, one-use, and exact-binds attempt, step, payload,
        # target, operation, and terminal sink.
        m0_case(
            "INVALID-M0-GRANT-ISSUER-PIN-ISSUER-01",
            [
                overlay(
                    m0_grant_model_paths["owner-root-write"]["PUBLISHER"],
                    mutation(
                        "replace",
                        "/issuer_id",
                        "fixture-m0-foreign-operation-issuer",
                    ),
                )
            ],
            "M0_OPERATION_AUTHORITY_INVALID",
            "M0-GRANT-ISSUER-PIN: issuer_id mismatch",
        ),
        m0_case(
            "INVALID-M0-GRANT-ISSUER-PIN-KEY-01",
            [
                overlay(
                    m0_grant_model_paths["owner-root-write"]["PUBLISHER"],
                    mutation(
                        "replace",
                        "/signing_key_id",
                        "fixture-m0-foreign-operation-key",
                    ),
                )
            ],
            "M0_OPERATION_AUTHORITY_INVALID",
            "M0-GRANT-ISSUER-PIN: signing_key_id mismatch",
        ),
        m0_case(
            "INVALID-M0-GRANT-SIGNATURE-CRYPTOGRAPHIC-01",
            [
                overlay(
                    m0_grant_model_paths["owner-root-write"]["PUBLISHER"],
                    mutation(
                        "replace",
                        "/signature",
                        _INVALID_ED25519_SIGNATURE,
                    ),
                )
            ],
            "M0_OPERATION_AUTHORITY_INVALID",
            "M0-GRANT-SIGNATURE: Ed25519 verification failed",
        ),
        m0_case(
            "INVALID-M0-GRANT-SIGNATURE-NONCANONICAL-TAIL-01",
            [
                overlay(
                    m0_grant_model_paths["owner-root-write"]["PUBLISHER"],
                    mutation(
                        "replace",
                        "/signature",
                        _NONCANONICAL_ED25519_SIGNATURE,
                    ),
                )
            ],
            "SCHEMA_VALIDATION_FAILED",
            "does not match '^[A-Za-z0-9+/]{85}[AQgw]==$'",
            stage="schema",
        ),
        m0_case(
            "INVALID-M0-GRANT-KIND-SPLICE-01",
            [
                overlay(
                    m0_paths["owner_publication"],
                    mutation(
                        "replace",
                        "/write_operation_authority_ref",
                        m0_grant_refs["owner-root-write"][
                            "REPOSITORY_WRITE"
                        ],
                    ),
                    mutation(
                        "replace",
                        "/write_operation_authority_sha256",
                        m0_grant_sha256["owner-root-write"][
                            "REPOSITORY_WRITE"
                        ],
                    ),
                )
            ],
            "M0_OPERATION_AUTHORITY_INVALID",
            "M0-GRANT-SIGNATURE: terminal receipt or sink binding is invalid",
        ),
        m0_case(
            "INVALID-M0-GRANT-ATTEMPT-SPLICE-01",
            [
                overlay(
                    m0_grant_model_paths["owner-root-write"]["PUBLISHER"],
                    mutation(
                        "replace",
                        "/bootstrap_attempt_id",
                        "fixture-m0-bootstrap-attempt-foreign",
                    ),
                )
            ],
            "M0_OPERATION_AUTHORITY_INVALID",
            "M0-GRANT-ATTEMPT: bootstrap_attempt_id mismatch",
        ),
        m0_case(
            "INVALID-M0-GRANT-STEP-SPLICE-01",
            [
                overlay(
                    m0_grant_model_paths["owner-root-write"]["PUBLISHER"],
                    mutation(
                        "replace",
                        "/grant/step_id",
                        "resource-calendar-write",
                    ),
                )
            ],
            "M0_OPERATION_AUTHORITY_INVALID",
            "M0-GRANT-STEP: grant step_id mismatch",
        ),
        m0_case(
            "INVALID-M0-GRANT-PAYLOAD-SPLICE-01",
            [
                overlay(
                    m0_grant_model_paths["owner-root-write"]["PUBLISHER"],
                    mutation("replace", "/grant/payload_sha256", _BAD_SHA),
                )
            ],
            "M0_OPERATION_AUTHORITY_INVALID",
            "M0-GRANT-PAYLOAD: payload_sha256 mismatch",
        ),
        m0_case(
            "INVALID-M0-GRANT-TARGET-SPLICE-01",
            [
                overlay(
                    m0_grant_model_paths["owner-root-write"]["PUBLISHER"],
                    mutation(
                        "replace",
                        "/grant/target_scope",
                        "fixture://m0-bootstrap/foreign-target",
                    ),
                )
            ],
            "M0_OPERATION_AUTHORITY_INVALID",
            "M0-GRANT-TARGET: target_scope mismatch",
        ),
        m0_case(
            "INVALID-M0-GRANT-REPLAY-01",
            [
                overlay(
                    m0_paths["approval_subject_publication"],
                    mutation(
                        "replace",
                        "/write_operation_authority_ref",
                        m0_grant_refs["owner-root-write"]["PUBLISHER"],
                    ),
                    mutation(
                        "replace",
                        "/write_operation_authority_sha256",
                        m0_grant_sha256["owner-root-write"]["PUBLISHER"],
                    ),
                )
            ],
            "M0_OPERATION_AUTHORITY_INVALID",
            "M0-GRANT-REPLAY: one-use grant reused",
        ),

        # F/P/R/Q cardinality is exact.  A byte-identical clone still creates
        # a competing identity instance and must be rejected.
        m0_case(
            "INVALID-M0-F-MISSING-OWNER-01",
            [overlay(m0_paths["owner_payload"], drop_model=True)],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            "M0-FPR-CARDINALITY: missing owner F",
        ),
        m0_case(
            "INVALID-M0-F-DUPLICATE-OWNER-01",
            [
                overlay(
                    m0_paths["owner_payload"],
                    copy_to_path=suffixed_path(
                        m0_paths["owner_payload"], "duplicate"
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            "M0-FPR-CARDINALITY: duplicate owner F",
        ),
        m0_case(
            "INVALID-M0-P-MISSING-OWNER-01",
            [overlay(m0_paths["owner_publication"], drop_model=True)],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            "M0-FPR-CARDINALITY: missing owner P",
        ),
        m0_case(
            "INVALID-M0-P-DUPLICATE-OWNER-01",
            [
                overlay(
                    m0_paths["owner_publication"],
                    copy_to_path=suffixed_path(
                        m0_paths["owner_publication"], "duplicate"
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            "M0-FPR-CARDINALITY: duplicate owner P",
        ),
        m0_case(
            "INVALID-M0-R-MISSING-OWNER-01",
            [overlay(m0_paths["owner_readback"], drop_model=True)],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            "M0-FPR-CARDINALITY: missing owner R",
        ),
        m0_case(
            "INVALID-M0-R-DUPLICATE-OWNER-01",
            [
                overlay(
                    m0_paths["owner_readback"],
                    copy_to_path=suffixed_path(
                        m0_paths["owner_readback"], "duplicate"
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            "M0-FPR-CARDINALITY: duplicate owner R",
        ),
        m0_case(
            "INVALID-M0-Q-MISSING-01",
            [overlay(m0_paths["issue_reconciliation"], drop_model=True)],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            "M0-FPR-CARDINALITY: missing Q",
        ),
        m0_case(
            "INVALID-M0-Q-DUPLICATE-01",
            [
                overlay(
                    m0_paths["issue_reconciliation"],
                    copy_to_path=suffixed_path(
                        m0_paths["issue_reconciliation"], "duplicate"
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            "M0-FPR-CARDINALITY: duplicate Q",
        ),

        # Q recomputes an exact role-indexed digest map from one successor
        # head whose selector-v2 approvals have unique role slots.
        m0_case(
            "INVALID-M0-Q-APPROVAL-DIGEST-01",
            [
                overlay(
                    m0_paths["issue_reconciliation"],
                    mutation(
                        "replace",
                        (
                            "/issue_approval_sha256_by_role/"
                            f"{pointer_token(m0_first_issue_role)}"
                        ),
                        _BAD_SHA,
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-Q-APPROVAL-MAP: "
                f"{m0_first_issue_role} digest mismatch"
            ),
        ),
        m0_case(
            "INVALID-M0-Q-APPROVAL-ROLE-SET-01",
            [
                overlay(
                    m0_paths["issue_reconciliation"],
                    mutation(
                        "add",
                        (
                            "/issue_approval_sha256_by_role/"
                            f"{pointer_token(m0['q_absent_role_id'])}"
                        ),
                        _BAD_SHA,
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            "M0-Q-APPROVAL-MAP: role set mismatch",
        ),
        m0_case(
            "INVALID-M0-Q-DUPLICATE-SUCCESSOR-ROLE-01",
            [
                overlay(
                    m0_paths["issue_successor_head"],
                    mutation(
                        "replace",
                        (
                            "/approvals/"
                            f"{m0_issue_approval_index[m0_second_issue_role]}/"
                            "role_id"
                        ),
                        m0_first_issue_role,
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            "M0-Q-ROLE-UNIQUE: duplicate successor-head approval role",
        ),

        # The chronology checker names every edge with its policy formula.
        # Equality is invalid on every '<' edge; the two '<=' edges are
        # exercised by moving the left-hand timestamp strictly after the right.
        m0_case(
            "INVALID-M0-CHRONOLOGY-C-READBACK-EQUAL-01",
            [
                overlay(
                    m0_paths["input_closure_readback"],
                    mutation("replace", "/read_back_at", m0_times["c_created"]),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            "M0-CHRONOLOGY-STRICT: C.created_at < C_R.read_back_at",
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-C-READBACK-AFTER-PHASE-A-01",
            [
                overlay(
                    m0_paths["input_closure_readback"],
                    mutation(
                        "replace",
                        "/read_back_at",
                        m0_times["owner_published"],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            "M0-CHRONOLOGY-STRICT: C_R.read_back_at <= phase_A.derived_at",
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-PHASE-A-OWNER-P-EQUAL-01",
            [
                overlay(
                    m0_paths["owner_publication"],
                    mutation(
                        "replace",
                        "/published_at",
                        m0_times["phase_a_derived"],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            "M0-CHRONOLOGY-STRICT: phase_A.derived_at < owner_P.published_at",
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-OWNER-P-R-EQUAL-01",
            [
                overlay(
                    m0_paths["owner_readback"],
                    mutation(
                        "replace",
                        "/read_back_at",
                        m0_times["owner_published"],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            "M0-CHRONOLOGY-STRICT: owner_P.published_at < owner_R.read_back_at",
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-OWNER-R-ISSUE-APPROVAL-EQUAL-01",
            [
                overlay(
                    m0_paths["issue_successor_head"],
                    mutation(
                        "replace",
                        (
                            f"/approvals/{m0['issue_approval_min_index']}/"
                            "approved_at"
                        ),
                        m0_times["owner_readback"],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-CHRONOLOGY-STRICT: owner_R.read_back_at < "
                "min(issue_approvals.approved_at)"
            ),
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-ISSUE-APPROVAL-SOURCE-EQUAL-01",
            [
                overlay(
                    m0_paths["issue_source_operation"],
                    mutation(
                        "replace",
                        "/completed_at",
                        m0_times["issue_approval_max"],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-CHRONOLOGY-STRICT: max(issue_approvals.approved_at) < "
                "issue_source.completed_at"
            ),
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-SOURCE-ARCHIVE-EQUAL-01",
            [
                overlay(
                    m0_paths["issue_archive_operation"],
                    mutation(
                        "replace",
                        "/completed_at",
                        m0_times["issue_source_completed"],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-CHRONOLOGY-STRICT: issue_source.completed_at < "
                "issue_archive.completed_at"
            ),
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-ARCHIVE-HEAD-EQUAL-01",
            [
                overlay(
                    m0_paths["issue_head_operation"],
                    mutation(
                        "replace",
                        "/completed_at",
                        m0_times["issue_archive_completed"],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-CHRONOLOGY-STRICT: issue_archive.completed_at < "
                "issue_head.completed_at"
            ),
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-HEAD-TRANSITION-EQUAL-01",
            [
                overlay(
                    m0_paths["issue_transition_readback"],
                    mutation(
                        "replace",
                        "/read_back_at",
                        m0_times["issue_head_completed"],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-CHRONOLOGY-STRICT: issue_head.completed_at < "
                "transition_R.read_back_at"
            ),
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-TRANSITION-AFTER-Q-01",
            [
                overlay(
                    m0_paths["issue_transition_readback"],
                    mutation(
                        "replace",
                        "/read_back_at",
                        m0_times["phase_b_derived"],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-CHRONOLOGY-STRICT: transition_R.read_back_at <= "
                "Q.reconciled_at"
            ),
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-Q-PHASE-B-EQUAL-01",
            [
                overlay(
                    m0_paths["phase_b_derivation"],
                    mutation(
                        "replace",
                        "/derived_at",
                        m0_times["q_reconciled"],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            "M0-CHRONOLOGY-STRICT: Q.reconciled_at < phase_B.derived_at",
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-PHASE-B-S-P-EQUAL-01",
            [
                overlay(
                    m0_paths["approval_subject_publication"],
                    mutation(
                        "replace",
                        "/published_at",
                        m0_times["phase_b_derived"],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            "M0-CHRONOLOGY-STRICT: phase_B.derived_at < S_P.published_at",
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-S-P-R-EQUAL-01",
            [
                overlay(
                    m0_paths["approval_subject_readback"],
                    mutation(
                        "replace",
                        "/read_back_at",
                        m0_times["subject_published"],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            "M0-CHRONOLOGY-STRICT: S_P.published_at < S_R.read_back_at",
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-S-R-APPROVAL-EQUAL-01",
            [
                overlay(
                    m0_approval_paths[m0_first_planning_role],
                    mutation(
                        "replace",
                        "/approved_at",
                        m0_times["subject_readback"],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-CHRONOLOGY-STRICT: S_R.read_back_at < "
                "min(planning_approvals.approved_at)"
            ),
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-APPROVAL-P-EQUAL-01",
            [
                overlay(
                    m0_approval_publication_paths[m0_first_planning_role],
                    mutation(
                        "replace",
                        "/published_at",
                        m0_times["approval_approved_by_role"][
                            m0_first_planning_role
                        ],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-CHRONOLOGY-STRICT: approval.approved_at < "
                "approval_P.published_at"
            ),
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-APPROVAL-P-R-EQUAL-01",
            [
                overlay(
                    m0_approval_readback_paths[m0_first_planning_role],
                    mutation(
                        "replace",
                        "/read_back_at",
                        m0_times["approval_published_by_role"][
                            m0_first_planning_role
                        ],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-CHRONOLOGY-STRICT: approval_P.published_at < "
                "approval_R.read_back_at"
            ),
        ),
        m0_case(
            "INVALID-M0-CHILD-P-BEFORE-APPROVALS-01",
            [
                overlay(
                    m0_child_publication_paths[m0_first_child_kind],
                    mutation(
                        "replace",
                        "/published_at",
                        m0_times["subject_readback"],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-CHRONOLOGY-STRICT: max(approval_R.read_back_at) < "
                "min(child_P.published_at)"
            ),
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-CHILD-P-R-EQUAL-01",
            [
                overlay(
                    m0_child_readback_paths[m0_first_child_kind],
                    mutation(
                        "replace",
                        "/read_back_at",
                        m0_times["child_published_by_kind"][
                            m0_first_child_kind
                        ],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-CHRONOLOGY-STRICT: child_P.published_at < "
                "child_R.read_back_at"
            ),
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-CHILD-R-BUNDLE-EQUAL-01",
            [
                overlay(
                    m0_paths["bundle_payload"],
                    mutation(
                        "replace",
                        "/generated_at",
                        m0_times["child_readback_by_kind"][
                            m0_last_child_kind
                        ],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-CHRONOLOGY-STRICT: max(child_R.read_back_at) < "
                "bundle.generated_at"
            ),
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-BUNDLE-F-P-EQUAL-01",
            [
                overlay(
                    m0_paths["bundle_publication"],
                    mutation(
                        "replace",
                        "/published_at",
                        m0_times["bundle_generated"],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-CHRONOLOGY-STRICT: bundle.generated_at < "
                "bundle_P.published_at"
            ),
        ),
        m0_case(
            "INVALID-M0-CHRONOLOGY-BUNDLE-P-R-EQUAL-01",
            [
                overlay(
                    m0_paths["bundle_readback"],
                    mutation(
                        "replace",
                        "/read_back_at",
                        m0_times["bundle_published"],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-CHRONOLOGY-STRICT: bundle_P.published_at < "
                "bundle_R.read_back_at"
            ),
        ),

        # S is wrapperless and keeps the containing-bundle wire
        # discriminator.  Its gate, horizon, and kind must remain coherent.
        m0_case(
            "INVALID-M0-S-WRAPPER-01",
            [
                overlay(
                    m0_paths["approval_subject"],
                    mutation("add", "/subject", {}),
                )
            ],
            "SCHEMA_VALIDATION_FAILED",
            (
                f"{m0_paths['approval_subject']}: Additional properties are "
                "not allowed ('subject' was unexpected)"
            ),
            stage="schema",
        ),
        m0_case(
            "INVALID-M0-S-DISCRIMINATOR-01",
            [
                overlay(
                    m0_paths["approval_subject"],
                    mutation(
                        "replace",
                        "/schema",
                        "ylx.planning-approval-subject.v1",
                    ),
                )
            ],
            "SCHEMA_VALIDATION_FAILED",
            (
                f"{m0_paths['approval_subject']}: "
                "'ylx.delivery-planning-bundle.v2' was expected"
            ),
            stage="schema",
        ),
        m0_case(
            "INVALID-M0-S-GATE-INCONSISTENT-01",
            [
                overlay(
                    m0_paths["approval_subject"],
                    mutation("replace", "/planning_gate", "M1"),
                )
            ],
            "SCHEMA_VALIDATION_FAILED",
            f"{m0_paths['approval_subject']}: 'M2' was expected",
            stage="schema",
        ),
        m0_case(
            "INVALID-M0-S-HORIZON-INCONSISTENT-01",
            [
                overlay(
                    m0_paths["approval_subject"],
                    mutation(
                        "replace",
                        "/detail_horizon/planning_gate",
                        "M1",
                    ),
                )
            ],
            "SCHEMA_VALIDATION_FAILED",
            f"{m0_paths['approval_subject']}: 'M0' was expected",
            stage="schema",
        ),
        m0_case(
            "INVALID-M0-S-BUNDLE-KIND-INCONSISTENT-01",
            [
                overlay(
                    m0_paths["approval_subject"],
                    mutation(
                        "replace",
                        "/bundle_kind",
                        "FINAL_ACTUAL_VARIANCE",
                    ),
                )
            ],
            "SCHEMA_VALIDATION_FAILED",
            f"{m0_paths['approval_subject']}: 'ROLLING_WAVE' was expected",
            stage="schema",
        ),
        m0_case(
            "INVALID-M0-BUNDLE-SELF-REFERENCE-01",
            [
                overlay(
                    m0_paths["bundle_payload"],
                    mutation(
                        "replace",
                        "/planning_approval_subject_ref",
                        m0["bundle_payload_ref"],
                    ),
                )
            ],
            "M0_BOOTSTRAP_GRAPH_INVALID",
            (
                "M0-BUNDLE-SELF-REFERENCE: complete bundle does not bind "
                "external S exactly"
            ),
        ),

        # Stage-native issue verdicts, execution authorization, and the
        # content-addressed release projection/durability protocol.
        _case(
            "INVALID-RELEASE-PROJECTION-V2-PREDECESSOR-01",
            current_projection_target,
            "replace",
            "/predecessor_projection_ref/artifact_sha256",
            "procedural",
            "RELEASE_PROJECTION_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-RELEASE-PROJECTION-PUBLICATION-STALE-01",
            "release-result-projection-publication-receipt.json",
            "replace",
            "/projection_ref",
            "procedural",
            "RELEASE_PROJECTION_DURABILITY_INVALID",
            notice,
            release_projection_v2_predecessor_ref,
        ),
        _case(
            "INVALID-RELEASE-PROJECTION-READBACK-DIGEST-01",
            "release-result-projection-readback-receipt.json",
            "replace",
            "/observed_projection_sha256",
            "procedural",
            "RELEASE_PROJECTION_DURABILITY_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-RELEASE-PROJECTION-READBACK-ACTION-01",
            "release-result-projection-readback-receipt.json",
            "replace",
            "/action_instance_id",
            "procedural",
            "RELEASE_PROJECTION_DURABILITY_INVALID",
            notice,
            "fixture-action-instance-mismatch",
        ),
        _case(
            "INVALID-RELEASE-OPERATION-ASSIGNMENT-CONTEXT-01",
            "release-operation-assignment-v2-projection.json",
            "replace",
            "/operation_scope/m5_binding_context_ref/artifact_sha256",
            "procedural",
            "RELEASE_OPERATION_ASSIGNMENT_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-ISSUE-GATE-VERDICT-PREDECESSOR-01",
            "issue-register-gate-verdict-m5.json",
            "replace",
            "/predecessor_verdict_ref/revision",
            "procedural",
            "ISSUE_GATE_VERDICT_INVALID",
            notice,
            2,
        ),
        _case(
            "INVALID-ISSUE-GATE-VERDICT-SLICE-01",
            "issue-register-gate-verdict-m5-r1.json",
            "replace",
            "/selected_issue_slices_by_id/O-1/body_sha256",
            "procedural",
            "ISSUE_GATE_VERDICT_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-ISSUE-GATE-VERDICT-HEAD-01",
            "issue-register-gate-verdict-m0.json",
            "replace",
            "/current_issue_register_head_artifact_sha256",
            "procedural",
            "ISSUE_GATE_VERDICT_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-ISSUE-RECONCILIATION-DIGEST-01",
            current_projection_target,
            "replace",
            "/issue_reconciliation_set_sha256",
            "procedural",
            "ISSUE_RECONCILIATION_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-EXECUTION-AUTHORIZATION-EVALUATION-REF-01",
            "issue-verdict-evidence-binding-v2-m5.json",
            "replace",
            (
                "/evidence_records/0/"
                "execution_authorization_evaluation_ref/artifact_sha256"
            ),
            "procedural",
            "EXECUTION_AUTHORIZATION_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-EXECUTION-AUTHORIZATION-ACTION-INSTANCE-01",
            "issue-verdict-evidence-binding-v2-m5.json",
            "replace",
            "/evidence_records/0/action_instance_id",
            "procedural",
            "EXECUTION_AUTHORIZATION_INVALID",
            notice,
            "fixture-action-instance-mismatch",
        ),
        _case(
            "INVALID-EXECUTION-AUTHORIZATION-PLANNED-INPUT-01",
            "issue-verdict-evidence-binding-v2-m5.json",
            "replace",
            "/evidence_records/0/planned_action_input_sha256",
            "procedural",
            "EXECUTION_AUTHORIZATION_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-EXECUTION-EVALUATION-ACTION-EQUALITY-P1-01",
            "execution-authorization-evaluation-stage-evidence-m0-pass.json",
            "replace",
            "/authorizes_action",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            "measure",
        ),
        _scenario_case(
            "INVALID-EXECUTION-EVALUATION-NONALLOWLISTED-NULL-CONTEXT-P1-01",
            [
                {
                    "target_path": "valid/execution-authorization-evaluation-stage-evidence-m2-pass.json",
                    "mutations": [
                        {
                            "op": "replace",
                            "pointer": "/authorization_binding_context_ref",
                            "value": None,
                        },
                        {
                            "op": "replace",
                            "pointer": "/authorization_binding_context_sha256",
                            "value": None,
                        },
                    ],
                }
            ],
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
        ),
        _case(
            "INVALID-EXECUTION-EVALUATION-FAILURE-CODE-MISSING-P1-01",
            "execution-authorization-evaluation-stage-evidence-m2-missing-inputs-fail.json",
            "replace",
            "/failure_codes",
            "procedural",
            "EXECUTION_AUTHORIZATION_INVALID",
            notice,
            [
                "PREREQUISITE_MISSING",
                "ENVIRONMENT_MISMATCH",
                "BINDING_CONTEXT_MISMATCH",
                "AUTHORITY_MISMATCH",
                "ACTOR_ASSIGNMENT_MISMATCH",
                "PHASE_BARRIER_UNSATISFIED",
                "STOP_RULE_EVIDENCE_MISMATCH",
                "VALIDATOR_MISMATCH",
            ],
        ),
        _case(
            "INVALID-EXECUTION-EVALUATION-FAILURE-CODE-UNRELATED-P1-01",
            "execution-authorization-evaluation-stage-evidence-m2-missing-inputs-fail.json",
            "replace",
            "/failure_codes",
            "procedural",
            "EXECUTION_AUTHORIZATION_INVALID",
            notice,
            [
                "STALE_PLANNING_BUNDLE",
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
        ),
        _case(
            "INVALID-EXECUTION-EVALUATION-FAILURE-CODE-ORDER-P1-01",
            "execution-authorization-evaluation-stage-evidence-m2-missing-inputs-fail.json",
            "replace",
            "/failure_codes",
            "procedural",
            "EXECUTION_AUTHORIZATION_INVALID",
            notice,
            [
                "CHECKER_ASSIGNMENT_MISMATCH",
                "VALIDATOR_MISMATCH",
                "STOP_RULE_EVIDENCE_MISMATCH",
                "PHASE_BARRIER_UNSATISFIED",
                "ACTOR_ASSIGNMENT_MISMATCH",
                "AUTHORITY_MISMATCH",
                "BINDING_CONTEXT_MISMATCH",
                "ENVIRONMENT_MISMATCH",
                "PREREQUISITE_MISSING",
            ],
        ),
        _case(
            "INVALID-EXECUTION-EVALUATION-REPLAY-CODE-MISSING-P1-01",
            "execution-authorization-evaluation-stage-evidence-m0-replay-fail.json",
            "replace",
            "/failure_codes",
            "procedural",
            "EXECUTION_AUTHORIZATION_INVALID",
            notice,
            ["PLANNED_ACTION_INPUT_MISMATCH"],
        ),
        _scenario_case(
            "INVALID-EXECUTION-EVALUATION-REPLAY-PASS-P1-01",
            [
                {
                    "target_path": "valid/execution-authorization-evaluation-stage-evidence-m0-replay-fail.json",
                    "mutations": [
                        {"op": "replace", "pointer": "/result", "value": "PASS"},
                        {"op": "replace", "pointer": "/failure_codes", "value": []},
                        {
                            "op": "replace",
                            "pointer": "/authorizes_action",
                            "value": "observe",
                        },
                    ],
                }
            ],
            "procedural",
            "EXECUTION_AUTHORIZATION_INVALID",
            notice,
        ),
        _case(
            "INVALID-EXECUTION-EVALUATION-MULTI-AUTHORITY-DROPPED-P1-01",
            "execution-authorization-evaluation-stage-evidence-m0-pass.json",
            "remove",
            (
                "/authorization_authority_ref_by_artifact_id/"
                "fixture-planning-v2-stage-evidence-authority-m0-independent"
            ),
            "procedural",
            "EXECUTION_AUTHORIZATION_INVALID",
            notice,
        ),
        _case(
            "INVALID-EXECUTION-WBS-DUPLICATE-AUTHORITY-ID-P1-01",
            "delivery-wbs-v2.json",
            "replace",
            "/nodes/1/execution_authorization/authority_refs/1/artifact_id",
            "procedural",
            "PLANNING_AUTHORIZATION_INVALID",
            notice,
            "fixture-planning-v2-stage-evidence-authority-m0",
        ),
        _scenario_case(
            "INVALID-EXECUTION-WBS-AUTHORITY-PAYLOAD-TYPE-P1-01",
            [
                {
                    "target_path": "valid/delivery-wbs-v2.json",
                    "copy_to_path": "valid/delivery-wbs-v2-wrong-authority-type.json",
                    "mutations": [
                        {
                            "op": "replace",
                            "pointer": (
                                "/nodes/1/execution_authorization/authority_refs/0"
                            ),
                            "value": wrong_type_authority_ref,
                        }
                    ],
                }
            ],
            "procedural",
            "PLANNING_AUTHORIZATION_INVALID",
            notice,
        ),
        _scenario_case(
            "INVALID-EXECUTION-EVALUATION-RELEASE-OPERATION-MAPPING-P1-01",
            [
                {
                    "target_path": (
                        "valid/execution-authorization-evaluation-"
                        "assemble-release-projection-pass.json"
                    ),
                    "mutations": [
                        {
                            "op": "replace",
                            "pointer": "/actor_assignment_ref",
                            "value": wrong_release_operation_assignment_ref,
                        },
                        {
                            "op": "replace",
                            "pointer": "/actor_person_id",
                            "value": "fixture-build-platform-owner-person",
                        },
                        {
                            "op": "replace",
                            "pointer": (
                                "/authorization_prerequisite_ref_by_kind/"
                                "projection_operation_assignment"
                            ),
                            "value": wrong_release_operation_assignment_ref,
                        },
                        {
                            "op": "replace",
                            "pointer": (
                                "/authorization_prerequisite_sha256_by_kind/"
                                "projection_operation_assignment"
                            ),
                            "value": wrong_release_operation_assignment_ref[
                                "artifact_sha256"
                            ],
                        },
                    ],
                }
            ],
            "procedural",
            "EXECUTION_AUTHORIZATION_INVALID",
            notice,
        ),
        _case(
            "INVALID-EXECUTION-EVALUATION-ACTOR-FAMILY-P1-01",
            "execution-authorization-evaluation-stage-evidence-m2-pass.json",
            "replace",
            "/actor_assignment_ref/schema",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            "ylx.role-signing-key-assignment.v1",
        ),
        _case(
            "INVALID-EXECUTION-EVALUATION-CHECKER-FAMILY-P1-01",
            "execution-authorization-evaluation-stage-evidence-m2-pass.json",
            "replace",
            "/checker_assignment_ref/schema",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            "ylx.release-operation-assignment.v2",
        ),
        _case(
            "INVALID-EXECUTION-EVALUATION-CHECKER-LEAF-P1-01",
            "execution-authorization-evaluation-stage-evidence-m2-pass.json",
            "replace",
            "/checker_assignment_ref/artifact_id",
            "procedural",
            "EXECUTION_AUTHORIZATION_INVALID",
            notice,
            "fixture-unrelated-owner-assignment",
        ),
        _case(
            "INVALID-EXECUTION-EVALUATION-PLANNED-DIGEST-P1-01",
            "execution-authorization-evaluation-stage-evidence-m2-pass.json",
            "replace",
            "/planned_action_input_sha256",
            "procedural",
            "EXECUTION_AUTHORIZATION_INVALID",
            notice,
            _BAD_SHA,
        ),
        _scenario_case(
            "INVALID-EXECUTION-EVALUATION-AUTHORITY-PAYLOAD-ACTION-P1-01",
            [
                {
                    "target_path": "valid/execution-authorization-evaluation-stage-evidence-m0-pass.json",
                    "mutations": [
                        {
                            "op": "replace",
                            "pointer": (
                                "/authorization_authority_ref_by_artifact_id/"
                                "fixture-planning-v2-stage-evidence-authority-m0/"
                                "artifact_path"
                            ),
                            "value": wrong_action_authority_ref["artifact_path"],
                        },
                        {
                            "op": "replace",
                            "pointer": (
                                "/authorization_authority_ref_by_artifact_id/"
                                "fixture-planning-v2-stage-evidence-authority-m0/"
                                "artifact_sha256"
                            ),
                            "value": wrong_action_authority_ref["artifact_sha256"],
                        },
                        {
                            "op": "replace",
                            "pointer": (
                                "/authorization_authority_sha256_by_artifact_id/"
                                "fixture-planning-v2-stage-evidence-authority-m0"
                            ),
                            "value": wrong_action_authority_ref["artifact_sha256"],
                        },
                    ],
                }
            ],
            "procedural",
            "EXECUTION_AUTHORIZATION_INVALID",
            notice,
        ),
        _case(
            "INVALID-EXECUTION-STOP-OBSERVATION-RULE-P1-01",
            "authorization-stop-rule-observation-stage-evidence-m0-pass-1.json",
            "replace",
            "/rule_id",
            "procedural",
            "EXECUTION_AUTHORIZATION_INVALID",
            notice,
            "Stop on an unrelated synthetic condition.",
        ),
        _case(
            "INVALID-EXECUTION-STOP-OBSERVATION-ACTION-INSTANCE-P1-01",
            "authorization-stop-rule-observation-stage-evidence-m0-pass-1.json",
            "replace",
            "/action_instance_id",
            "procedural",
            "EXECUTION_AUTHORIZATION_INVALID",
            notice,
            "fixture-unrelated-action-instance",
        ),
        _case(
            "INVALID-EXECUTION-STOP-OBSERVATION-INPUT-DIGEST-P1-01",
            "authorization-stop-rule-observation-stage-evidence-m0-pass-1.json",
            "replace",
            "/observed_input_sha256_by_kind/planning_bundle",
            "procedural",
            "EXECUTION_AUTHORIZATION_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-EXECUTION-STOP-OBSERVATION-CHECKER-P1-01",
            "authorization-stop-rule-observation-stage-evidence-m0-pass-1.json",
            "replace",
            "/checker_person_id",
            "procedural",
            "EXECUTION_AUTHORIZATION_INVALID",
            notice,
            "fixture-unrelated-checker",
        ),
        _case(
            "INVALID-EXECUTION-STOP-OBSERVATION-OUTCOME-P1-01",
            "authorization-stop-rule-observation-stage-evidence-m0-pass-1.json",
            "replace",
            "/outcome",
            "procedural",
            "EXECUTION_AUTHORIZATION_INVALID",
            notice,
            "TRIGGERED",
        ),
        _case(
            "INVALID-M5-COHERENT-PRE-RELEASE-01",
            "release-attempt-input-set.json",
            "replace",
            "/pre_release_ref/artifact_sha256",
            "procedural",
            "M5_COHERENT_FAMILY_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-M5-FRESHNESS-PREDECESSOR-01",
            "release-freshness-checkpoint-2.json",
            "replace",
            "/predecessor_checkpoint_ref/artifact_sha256",
            "procedural",
            "M5_FRESHNESS_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-M5-FRESHNESS-CONTENT-READBACK-01",
            "content-addressed-readback-receipt.json",
            "replace",
            "/publication_receipt_ref/artifact_sha256",
            "procedural",
            "M5_FRESHNESS_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-M5-FRESHNESS-TERMINAL-READBACK-01",
            "terminal-slot-readback-receipt.json",
            "replace",
            "/terminal_cas_receipt_ref/artifact_sha256",
            "procedural",
            "M5_FRESHNESS_INVALID",
            notice,
            _BAD_SHA,
        ),

        _case(
            "INVALID-DISTRIBUTION-PREDECESSOR-01",
            "release-distribution-control-withdrawn.json",
            "replace",
            "/predecessor_control_sha256",
            "procedural",
            "FINAL_AUTHORITY_STALE",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-DISTRIBUTION-SIGNATURE-01",
            "release-distribution-control-active.json",
            "replace",
            "/signatures_by_role_slot/release-owner/signature_b64",
            "procedural",
            "SIGNATURE_INVALID",
            notice,
            "A" * 86 + "==",
        ),
        _case(
            "INVALID-DISTRIBUTION-DUPLICATE-PERSON-01",
            "release-distribution-control-active.json",
            "replace",
            "/signatures_by_role_slot/security-owner/person_id",
            "procedural",
            "QUORUM_PERSON_DISTINCTNESS",
            notice,
            "fixture-release-owner-person",
        ),

        # Measurement method profiles and external durable readback.
        _case(
            "INVALID-MEASUREMENT-METHOD-ROLE-PROFILE-01",
            "measurement-method-statistical-fit-m0-meas-01.json",
            "replace",
            "/method_role",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "FREEZE_EVALUATION",
        ),
        _case(
            "INVALID-MEASUREMENT-METHOD-PARTITION-PROFILE-01",
            "measurement-method-freeze-evaluation-m0-meas-01.json",
            "replace",
            "/partition_policy",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "REQUIRED_SOURCE_GROUP_DISJOINT",
        ),
        _case(
            "INVALID-MEASUREMENT-METHOD-HOLDOUT-PROFILE-01",
            "measurement-method-freeze-evaluation-m0-meas-01.json",
            "replace",
            "/holdout_policy",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "VALIDATION_ONLY_NO_RETUNING",
        ),
        _case(
            "INVALID-MEASUREMENT-METHOD-MEASUREMENT-COVERAGE-01",
            "measurement-method-freeze-evaluation-m0-meas-01.json",
            "replace",
            "/applicable_measurement_ids/0",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "M0-MEAS-02",
        ),
        _case(
            "INVALID-MEASUREMENT-METHOD-METRIC-COVERAGE-01",
            "measurement-method-freeze-evaluation-m0-meas-01.json",
            "replace",
            "/metric_ids/0",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "mjpeg-bitrate-p99-mbps",
        ),
        _case(
            "INVALID-MEASUREMENT-METHOD-READBACK-DIGEST-01",
            (
                "content-addressed-locator-readback-measurement-method-"
                "freeze-evaluation-m0-meas-01.json"
            ),
            "replace",
            "/readback_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-METHOD-READBACK-CHRONOLOGY-01",
            (
                "content-addressed-locator-readback-measurement-method-"
                "statistical-fit-m0-meas-01.json"
            ),
            "replace",
            "/readback_at",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "2026-06-01T12:05:31Z",
        ),

        # Measurement selection recomputation and causal E prerequisite.
        _case(
            "INVALID-MEASUREMENT-SELECTION-MEASUREMENT-01",
            "measurement-data-selection-training-m0-meas-01.json",
            "replace",
            "/measurement_id",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "M0-MEAS-02",
        ),
        _case(
            "INVALID-MEASUREMENT-SELECTION-PARTITION-REF-01",
            "measurement-data-selection-training-m0-meas-01.json",
            "replace",
            "/data_partition_ref/artifact_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-SELECTION-SIDE-01",
            "measurement-data-selection-training-m0-meas-01.json",
            "replace",
            "/partition_side",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "HOLDOUT",
        ),
        _case(
            "INVALID-MEASUREMENT-SELECTION-GROUP-01",
            "measurement-data-selection-training-m0-meas-01.json",
            "replace",
            "/source_group_ids",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            measurement_holdout_group_ids,
        ),
        _case(
            "INVALID-MEASUREMENT-SELECTION-GROUP-DIGEST-01",
            "measurement-data-selection-training-m0-meas-01.json",
            "replace",
            "/selected_group_set_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-SELECTION-SOURCE-DIGEST-01",
            "measurement-data-selection-training-m0-meas-01.json",
            "replace",
            "/selected_source_digest_set_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-SELECTION-SAMPLE-DIGEST-01",
            "measurement-data-selection-training-m0-meas-01.json",
            "replace",
            "/selected_sample_set_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-SELECTION-READBACK-01",
            (
                "content-addressed-locator-readback-measurement-data-selection-"
                "training-m0-meas-01.json"
            ),
            "replace",
            "/readback_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-SELECTION-CHRONOLOGY-01",
            "measurement-data-selection-training-m0-meas-01.json",
            "replace",
            "/created_at",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "2026-06-01T12:04:44Z",
        ),
        _scenario_case(
            "INVALID-MEASUREMENT-SELECTION-E-PREREQUISITE-MISSING-01",
            [
                {
                    "target_path": (
                        "valid/execution-authorization-evaluation-measurement-"
                        "training-evidence-m0-meas-01-pass.json"
                    ),
                    "mutations": [
                        {
                            "op": "remove",
                            "pointer": (
                                "/authorization_prerequisite_ref_by_kind/"
                                "measurement_data_selection"
                            ),
                        },
                        {
                            "op": "remove",
                            "pointer": (
                                "/authorization_prerequisite_sha256_by_kind/"
                                "measurement_data_selection"
                            ),
                        },
                    ],
                }
            ],
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
        ),
        _scenario_case(
            "INVALID-MEASUREMENT-SELECTION-E-PREREQUISITE-WRONG-01",
            [
                {
                    "target_path": (
                        "valid/execution-authorization-evaluation-measurement-"
                        "training-evidence-m0-meas-01-pass.json"
                    ),
                    "mutations": [
                        {
                            "op": "replace",
                            "pointer": (
                                "/authorization_prerequisite_ref_by_kind/"
                                "measurement_data_selection"
                            ),
                            "value": measurement_holdout_selection_ref,
                        },
                        {
                            "op": "replace",
                            "pointer": (
                                "/authorization_prerequisite_sha256_by_kind/"
                                "measurement_data_selection"
                            ),
                            "value": measurement_holdout_selection_ref[
                                "artifact_sha256"
                            ],
                        },
                    ],
                }
            ],
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
        ),

        # DATA_FITTED threshold joins across policy, E/WBS, methods, and evidence.
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-DUPLICATE-METRIC-01",
            "measurement-threshold-record-m0-meas-01.json",
            "add",
            "/threshold_terms/-",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            measurement_duplicate_metric_term,
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-MEASUREMENT-01",
            "measurement-threshold-record-m0-meas-01.json",
            "replace",
            "/measurement_id",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "M0-MEAS-02",
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-FREEZE-GATE-01",
            "measurement-threshold-record-m0-meas-01.json",
            "replace",
            "/freeze_gate",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "M2",
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-OWNER-ROLE-01",
            "measurement-threshold-record-m0-meas-01.json",
            "replace",
            "/owner_role_projection",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "release-owner",
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-CHECKER-ROLE-01",
            "measurement-threshold-record-m0-meas-01.json",
            "replace",
            "/checker_role_projection",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "consumer-owner",
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-ACTOR-01",
            "measurement-threshold-record-m0-meas-01.json",
            "replace",
            "/actor_person_id",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "fixture-unrelated-actor-person",
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-CHECKER-01",
            "measurement-threshold-record-m0-meas-01.json",
            "replace",
            "/checker_person_id",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "fixture-unrelated-checker-person",
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-E-REF-01",
            "measurement-threshold-record-m0-meas-01.json",
            "replace",
            "/execution_authorization_evaluation_ref/artifact_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-ACTION-TUPLE-01",
            "measurement-threshold-record-m0-meas-01.json",
            "replace",
            "/action_instance_id",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "fixture-wrong-threshold-action",
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-PLANNED-INPUT-01",
            "measurement-threshold-record-m0-meas-01.json",
            "replace",
            "/planned_action_input_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-ACTOR-ASSIGNMENT-01",
            "measurement-threshold-record-m0-meas-01.json",
            "replace",
            "/actor_assignment_ref/artifact_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-CHECKER-ASSIGNMENT-01",
            "measurement-threshold-record-m0-meas-01.json",
            "replace",
            "/checker_assignment_ref/artifact_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-CHRONOLOGY-01",
            "measurement-threshold-record-m0-meas-01.json",
            "replace",
            "/frozen_at",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "2026-06-01T12:05:29Z",
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-FREEZE-METHOD-01",
            "measurement-threshold-record-m0-meas-01.json",
            "replace",
            "/freeze_method_ref",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            measurement_statistical_method_ref,
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-STATISTICAL-METHOD-01",
            "measurement-threshold-record-m0-meas-01.json",
            "replace",
            "/statistical_method_ref",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            measurement_freeze_method_ref,
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-PARTITION-01",
            "measurement-threshold-record-m0-meas-01.json",
            "replace",
            "/data_partition_ref/artifact_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-TRAINING-EVIDENCE-01",
            "measurement-threshold-record-m0-meas-01.json",
            "replace",
            "/freeze_evidence_refs/0/evidence_record_ref",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            measurement_holdout_evidence_ref,
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-TRAINING-GROUP-01",
            "measurement-threshold-record-m0-meas-01.json",
            "replace",
            "/freeze_evidence_refs/0/source_group_ids",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            measurement_holdout_group_ids,
        ),
        _scenario_case(
            "INVALID-MEASUREMENT-THRESHOLD-E-METHOD-PREREQUISITE-01",
            [
                {
                    "target_path": (
                        "valid/execution-authorization-evaluation-m1-"
                        "threshold-freeze-m0-meas-01-pass.json"
                    ),
                    "mutations": [
                        {
                            "op": "remove",
                            "pointer": (
                                "/authorization_prerequisite_ref_by_kind/"
                                "statistical_method"
                            ),
                        },
                        {
                            "op": "remove",
                            "pointer": (
                                "/authorization_prerequisite_sha256_by_kind/"
                                "statistical_method"
                            ),
                        },
                    ],
                }
            ],
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-READBACK-01",
            (
                "content-addressed-locator-readback-measurement-threshold-"
                "m0-meas-01.json"
            ),
            "replace",
            "/readback_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-READBACK-CHRONOLOGY-01",
            (
                "content-addressed-locator-readback-measurement-threshold-"
                "m0-meas-01.json"
            ),
            "replace",
            "/published_at",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "2026-06-01T12:05:34Z",
        ),
        _case(
            "INVALID-MEASUREMENT-THRESHOLD-INLINE-AUTHORITY-01",
            "measurement-threshold-record-m0-meas-01.json",
            "add",
            "/release_authority_ref",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            wrong_action_authority_ref,
        ),

        # TRAINING/HOLDOUT bytes, E, selection, and set membership are disjoint.
        _case(
            "INVALID-MEASUREMENT-CROSS-SIDE-EVIDENCE-REUSE-01",
            "measurement-queue-v2-r2.json",
            "replace",
            "/measurements/0/evidence_record_refs/0/evidence_record_ref",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            measurement_training_evidence_ref,
        ),
        _support_case(
            "INVALID-MEASUREMENT-CROSS-SIDE-E-REUSE-01",
            [
                {
                    "target_path": (
                        "support/measurement-holdout-evidence-m0-meas-01.json"
                    ),
                    "mutations": [
                        {
                            "op": "replace",
                            "pointer": (
                                "/execution_authorization_evaluation_ref"
                            ),
                            "value": measurement_training_evaluation_ref,
                        }
                    ],
                }
            ],
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
        ),
        _scenario_case(
            "INVALID-MEASUREMENT-CROSS-SIDE-SELECTION-REUSE-01",
            [
                {
                    "target_path": (
                        "valid/execution-authorization-evaluation-measurement-"
                        "holdout-evidence-m0-meas-01-pass.json"
                    ),
                    "mutations": [
                        {
                            "op": "replace",
                            "pointer": (
                                "/authorization_prerequisite_ref_by_kind/"
                                "measurement_data_selection"
                            ),
                            "value": measurement_training_selection_ref,
                        },
                        {
                            "op": "replace",
                            "pointer": (
                                "/authorization_prerequisite_sha256_by_kind/"
                                "measurement_data_selection"
                            ),
                            "value": measurement_training_selection_ref[
                                "artifact_sha256"
                            ],
                        },
                    ],
                }
            ],
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
        ),
        _case(
            "INVALID-MEASUREMENT-CROSS-SIDE-GROUP-OVERLAP-01",
            "measurement-queue-v2-r2.json",
            "replace",
            "/measurements/0/evidence_record_refs/0/source_group_ids",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            measurement_training_group_ids,
        ),
        _case(
            "INVALID-MEASUREMENT-CROSS-SIDE-SOURCE-OVERLAP-01",
            "measurement-data-selection-holdout-m0-meas-01.json",
            "replace",
            "/selected_source_digest_set_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            measurement_training_source_set_sha256,
        ),
        _case(
            "INVALID-MEASUREMENT-CROSS-SIDE-SAMPLE-OVERLAP-01",
            "measurement-data-selection-holdout-m0-meas-01.json",
            "replace",
            "/selected_sample_set_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            measurement_training_sample_set_sha256,
        ),

        # Queue chain, history binding, projections, and external truth refs.
        _case(
            "INVALID-MEASUREMENT-QUEUE-CONTENT-DIGEST-01",
            "measurement-queue-v2.json",
            "replace",
            "/content_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-STABLE-ID-01",
            "measurement-queue-v2.json",
            "replace",
            "/queue_id",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "fixture-unrelated-m0-measurement-queue",
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-REVISION-SKIP-01",
            "measurement-queue-v2.json",
            "replace",
            "/revision",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            4,
        ),
        _scenario_case(
            "INVALID-MEASUREMENT-QUEUE-PREDECESSOR-01",
            [
                {
                    "target_path": "valid/measurement-queue-v2.json",
                    "mutations": [
                        {
                            "op": "replace",
                            "pointer": (
                                "/predecessor_queue_ref/artifact_sha256"
                            ),
                            "value": _BAD_SHA,
                        },
                        {
                            "op": "replace",
                            "pointer": "/content_sha256",
                            "value": (
                                measurement_queue_r3_bogus_predecessor_content_sha256
                            ),
                        },
                    ],
                }
            ],
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
        ),
        _scenario_case(
            "INVALID-MEASUREMENT-QUEUE-SELF-PREDECESSOR-01",
            [
                {
                    "target_path": "valid/measurement-queue-v2.json",
                    "mutations": [
                        {
                            "op": "replace",
                            "pointer": "/predecessor_queue_ref",
                            "value": measurement_queue_self_predecessor_ref,
                        },
                        {
                            "op": "replace",
                            "pointer": "/content_sha256",
                            "value": (
                                measurement_queue_r3_self_predecessor_content_sha256
                            ),
                        },
                    ],
                }
            ],
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-CHRONOLOGY-01",
            "measurement-queue-v2.json",
            "replace",
            "/created_at",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "2026-06-01T12:19:59Z",
        ),
        _scenario_case(
            "INVALID-MEASUREMENT-QUEUE-DUPLICATE-TIP-01",
            [
                {
                    "target_path": "valid/measurement-queue-v2.json",
                    "copy_to_path": (
                        "valid/measurement-queue-v2-duplicate.json"
                    ),
                    "mutations": [],
                }
            ],
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-CARDINALITY-01",
            "measurement-queue-v2.json",
            "remove",
            "/measurements/10",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-OWNER-01",
            "measurement-queue-v2.json",
            "replace",
            "/measurements/0/owner_projection",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "release-owner",
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-GATE-01",
            "measurement-queue-v2.json",
            "replace",
            "/measurements/0/closing_gate_projection",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            "M5",
        ),
        _scenario_case(
            "INVALID-MEASUREMENT-QUEUE-STALE-TIP-HISTORY-01",
            [
                {
                    "target_path": "valid/measurement-queue-v2.json",
                    "mutations": [
                        {
                            "op": "replace",
                            "pointer": "/registry_binding",
                            "value": measurement_queue_previous_registry_binding,
                        },
                        {
                            "op": "replace",
                            "pointer": "/content_sha256",
                            "value": (
                                measurement_queue_r3_stale_previous_binding_content_sha256
                            ),
                        },
                    ],
                }
            ],
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
        ),
        _scenario_case(
            "INVALID-MEASUREMENT-QUEUE-NONMONOTONIC-HISTORY-01",
            [
                {
                    "target_path": "valid/measurement-queue-v2.json",
                    "mutations": [
                        {
                            "op": "replace",
                            "pointer": "/registry_binding",
                            "value": measurement_queue_r1_registry_binding,
                        },
                        {
                            "op": "replace",
                            "pointer": "/content_sha256",
                            "value": (
                                measurement_queue_r3_stale_r1_binding_content_sha256
                            ),
                        },
                    ],
                }
            ],
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
        ),
        _scenario_case(
            "INVALID-MEASUREMENT-QUEUE-BOGUS-SELECTED-HEAD-01",
            [
                {
                    "target_path": "valid/measurement-queue-v2.json",
                    "mutations": [
                        {
                            "op": "replace",
                            "pointer": (
                                "/registry_binding/selected_head_ref/"
                                "artifact_sha256"
                            ),
                            "value": _BAD_SHA,
                        },
                        {
                            "op": "replace",
                            "pointer": "/content_sha256",
                            "value": (
                                measurement_queue_r3_bogus_selected_head_content_sha256
                            ),
                        },
                    ],
                }
            ],
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-FORGED-HISTORY-01",
            "measurement-queue-v2-r1.json",
            "replace",
            "/registry_binding/selected_head_ref",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            measurement_queue_previous_registry_binding["selected_head_ref"],
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-REGISTRY-ARCHIVE-01",
            "measurement-queue-v2-r1.json",
            "replace",
            "/registry_binding/registry_archived_artifact_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-MEASUREMENT-ID-SET-01",
            "measurement-queue-v2.json",
            "replace",
            "/registry_binding/measurement_id_set_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-READY-MISSING-THRESHOLD-01",
            "measurement-queue-v2-r2.json",
            "replace",
            "/measurements/0/threshold_record_ref",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            None,
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-READY-MISSING-EVIDENCE-01",
            "measurement-queue-v2-r2.json",
            "remove",
            "/measurements/0/evidence_record_refs/0",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-VERDICT-MEASUREMENT-01",
            "measurement-queue-v2.json",
            "replace",
            "/measurements/0/requirement_verdict_ref",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            measurement_wrong_verdict_ref,
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-VERDICT-BYTES-01",
            "measurement-queue-v2.json",
            "replace",
            "/measurements/0/requirement_verdict_ref/artifact_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-THRESHOLD-BYTES-01",
            "measurement-queue-v2.json",
            "replace",
            "/measurements/0/threshold_record_ref/artifact_sha256",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-EVIDENCE-BYTES-01",
            "measurement-queue-v2.json",
            "replace",
            (
                "/measurements/0/evidence_record_refs/0/"
                "evidence_record_ref/artifact_sha256"
            ),
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-EVIDENCE-PARTITION-01",
            "measurement-queue-v2.json",
            "replace",
            (
                "/measurements/0/evidence_record_refs/0/"
                "data_partition_ref/artifact_sha256"
            ),
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-VERDICT-REGRESSION-01",
            "measurement-queue-v2.json",
            "replace",
            "/measurements/0/requirement_verdict_ref",
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
            None,
        ),
        _scenario_case(
            "INVALID-MEASUREMENT-QUEUE-UNBLOCKED-WITHOUT-TRUTH-01",
            [
                {
                    "target_path": "valid/measurement-queue-v2-r1.json",
                    "mutations": [
                        {
                            "op": "replace",
                            "pointer": "/measurements/0/planning_state",
                            "value": "READY_FOR_VERDICT",
                        },
                        {
                            "op": "replace",
                            "pointer": "/measurements/0/blockers",
                            "value": [],
                        },
                    ],
                }
            ],
            "procedural",
            "MEASUREMENT_QUEUE_INVALID",
            notice,
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-PLANNING-STATE-PASS-01",
            "measurement-queue-v2.json",
            "replace",
            "/measurements/0/planning_state",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            "PASS",
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-INLINE-AUTHORITY-01",
            "measurement-queue-v2.json",
            "add",
            "/measurements/0/authority_ref",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            wrong_action_authority_ref,
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-INLINE-THRESHOLD-01",
            "measurement-queue-v2.json",
            "add",
            "/measurements/0/threshold_value",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            24.0,
        ),
        _case(
            "INVALID-MEASUREMENT-QUEUE-THRESHOLD-MASQUERADE-01",
            "measurement-queue-v2.json",
            "replace",
            "/measurements/0/threshold_record_ref",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            measurement_partition_ref,
        ),
        _case(
            "INVALID-DISTRIBUTION-REDIRECT-01",
            "release-distribution-control-withdrawn.json",
            "replace",
            "/action",
            "schema",
            "SCHEMA_VALIDATION_FAILED",
            notice,
            "REDIRECTED",
        ),
        _case(
            "INVALID-DISTRIBUTION-TERMINAL-REF-01",
            "release-distribution-control-active.json",
            "replace",
            "/finalized_terminal_reference/payload_sha256",
            "procedural",
            "FINAL_AUTHORITY_STALE",
            notice,
            _BAD_SHA,
        ),
        _case(
            "INVALID-DISTRIBUTION-REACTIVATION-EVIDENCE-01",
            "release-distribution-control-reactivated.json",
            "replace",
            "/recovery_condition/not_before",
            "procedural",
            "GA_VISIBILITY_INVALID",
            notice,
            None,
        ),
        _case(
            "INVALID-DISTRIBUTION-REACTIVATION-EVIDENCE-02",
            "release-distribution-control-reactivated.json",
            "replace",
            "/recovery_condition/recovery_evidence_refs/0/artifact_sha256",
            "procedural",
            "GA_VISIBILITY_INVALID",
            notice,
            _BAD_SHA,
        ),
    ]

    case_ids = [case["case_id"] for case in cases]
    assert len(case_ids) == len(set(case_ids)), "invalid fixture case IDs must be unique"
    assert {case["expected_code"] for case in cases} == STABLE_CODES
    assert all(case["expected_stage"] in {"schema", "procedural"} for case in cases)
    m0_cases = [
        case for case in cases if case["case_id"].startswith("INVALID-M0-")
    ]
    m0_error_substrings = [
        case.get("expected_error_substring") for case in m0_cases
    ]
    assert all(
        isinstance(value, str) and bool(value.strip())
        for value in m0_error_substrings
    ), "every M0 case must name one exact error substring"
    assert len(m0_error_substrings) == len(set(m0_error_substrings)), (
        "M0 error substrings must be unique and case-specific"
    )

    def valid_mutation(mutation: Any) -> bool:
        return isinstance(mutation, dict) and mutation.get("op") in {
            "add",
            "remove",
            "replace",
        }

    def valid_overlay(overlay: Any) -> bool:
        if not isinstance(overlay, dict) or not isinstance(
            overlay.get("target_path"), str
        ):
            return False
        drop_model = overlay.get("drop_model", False)
        mutations = overlay.get("mutations", [])
        copy_to_path = overlay.get("copy_to_path")
        if not isinstance(drop_model, bool) or not isinstance(mutations, list):
            return False
        if drop_model:
            return copy_to_path is None and mutations == []
        if copy_to_path is not None and (
            not isinstance(copy_to_path, str) or not copy_to_path
        ):
            return False
        return (bool(mutations) or copy_to_path is not None) and all(
            valid_mutation(mutation) for mutation in mutations
        )

    for case in cases:
        legacy = isinstance(case.get("mutation"), dict)
        scenario = isinstance(case.get("overlays"), list) and bool(
            case["overlays"]
        )
        support = isinstance(case.get("support_overlays"), list) and bool(
            case["support_overlays"]
        )
        assert sum((legacy, scenario, support)) == 1, (
            f"invalid mutation shape for {case['case_id']}"
        )
        if legacy:
            assert isinstance(case.get("target_path"), str)
            assert valid_mutation(case["mutation"])
            continue
        if support:
            assert all(valid_overlay(overlay) for overlay in case["support_overlays"])
            continue
        assert all(valid_overlay(overlay) for overlay in case["overlays"])
    return cases
