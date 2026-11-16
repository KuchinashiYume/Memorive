from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

import pytest

from memorive_settings.quality_new import (
    DEFAULT_WORKFLOW_MAPPING,
    Quality_NEW_CATALOG_SCHEMA,
    build_quality_new_catalog,
    evaluate_defect_lifecycle,
    plan_exam_volume,
    project_cached_entry,
    validate_quality_new_catalog,
)
from memorive.models.validation import (
    HttpResponse,
    LiveModelValidationRunner,
    ProcessResult,
)
from memorive_settings.workflow_exam import (
    QualityEmbeddingExamExecutor,
    QualityNewEmbeddingExamExecutor,
)
from memorive_settings.specialized_exam import (
    QualityNewRerankerTextExamExecutor,
    QualityNewRerankerTextLanguageBankExamExecutor,
    QualityNewRerankerTextProductionExamExecutor,
    QualityNewRerankerTextRecoveryExamExecutor,
)


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_CATALOG_PATH = Path(os.environ.get('Quality_NEW_SOURCE_CATALOG', str(
    ATTEMPT_ROOT.parents[1]
    / "candidate_cache"
    / "workflow_model_exam_scores_successor_v5.json"
)))
Quality_ROOT = ATTEMPT_ROOT / "source" / "memorive_model_evaluation"
RERANKER_PACK = (
    Quality_ROOT / "assets" / "reranker_text_exam" / "v1" / "reference_pack"
)


def defect(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "defect_id": "D-01",
        "case_id": "CASE-01",
        "panel_id": "PANEL-01",
        "family_id": "FAMILY-01",
        "severity": "MATERIAL",
        "introduced": True,
        "downstream_visible": True,
        "detected": False,
        "blocked": False,
        "repair_round": "NONE",
        "repaired": False,
        "reverified": False,
        "escaped": False,
        "guard_kind": "NONE",
        "guard_evidence_sha256": None,
        "source_recovery_verified": False,
    }
    value.update(overrides)
    return value


def test_phase2_new_volume_is_tiered_category_local_and_cache_first() -> None:
    cached = plan_exam_volume(
        "CARD_REVIEWER", "STANDARD", cache_reusable=True, profile_kind="API"
    )
    connectivity = plan_exam_volume(
        "CARD_REVIEWER", "CONNECTIVITY", profile_kind="CLI"
    )
    assert cached["physical_model_calls"] == 0
    assert cached["execution_mode"] == "STABLE_CACHE_REUSE"
    assert connectivity["physical_model_calls"] == 2
    assert connectivity["scoreable"] is False
    assert connectivity["model_fail_may_be_established"] is False
    assert connectivity["output_limit_policy"] == (
        "CALIBRATED_CEILING_WITH_MODEL_PROVIDER_PHYSICAL_MINIMUM"
    )
    assert connectivity["global_output_token_cap"] == 173_030
    assert connectivity["output_limit_contract"][
        "calibration_max_actual_completion_tokens"
    ] == 17_303
    assert connectivity["length_only_model_fail_forbidden"] is True
    assert connectivity["cross_category_comparison_forbidden"] is True

    embedding = plan_exam_volume("EMBEDDING_TEXT", "STANDARD")
    assert embedding["global_output_token_cap"] is None
    assert embedding["output_limit_policy"] == (
        "NOT_APPLICABLE_NON_GENERATIVE_EMBEDDING"
    )


def test_phase2_new_standard_volume_keeps_discrimination_without_full_matrix() -> None:
    expected = {
        "CARD_REVIEWER": 10,
        "CARD_DISTILLER": 11,
        "ANALYSIS_REVIEWER": 12,
        "OCR_PAGE": 11,
        "ANALYSIS_PRIMARY": 24,
        "EMBEDDING_TEXT": 24,
        "RERANKER_TEXT": 24,
    }
    observed = {
        role: plan_exam_volume(role, "STANDARD")["target_physical_model_calls"]
        for role in expected
    }
    assert observed == expected

    primary = plan_exam_volume("ANALYSIS_PRIMARY", "STANDARD")
    assert primary["panels_required_for_stable_score"] == 3
    assert primary["early_stop_allowed_only_for"] == [
        "NON_SCORABLE_EXECUTION",
        "MODEL_FAIL_ALREADY_ESTABLISHED",
    ]
    assert primary["calibration_only_residual_triggers_repair"] is False
    assert plan_exam_volume("ANALYSIS_PRIMARY", "STANDARD", profile_kind="API")[
        "maximum_physical_model_calls"
    ] == 75
    assert plan_exam_volume("ANALYSIS_PRIMARY", "STANDARD", profile_kind="LOCAL")[
        "maximum_physical_model_calls"
    ] == 120

    embedding = plan_exam_volume("EMBEDDING_TEXT", "STANDARD")
    assert embedding["logical_case_count"] == 240
    assert embedding["corpora_per_physical_request"] == 10
    assert embedding["small_sample_substitution_forbidden"] is True

    reranker = plan_exam_volume("RERANKER_TEXT", "STANDARD")
    assert reranker["qualification_ready"] is False
    assert reranker["qualification_blocker"] == (
        "TWO_DISTINCT_SLOTS_AND_FORMAL_QUALIFICATION_GATE_REQUIRED"
    )
    assert reranker["role_adequacy_threshold_defined"] is True
    assert reranker["role_adequacy_required_distinct_slots"] == 2
    assert reranker["production_primary_main_calls"] == 18
    assert reranker["multilingual_adversarial_main_calls"] == 4
    assert reranker["repeat_calls"] == 2
    assert reranker["primary_score_track"] == "production_primary"
    assert reranker["multilingual_track_affects_primary_score"] is False
    assert plan_exam_volume("RERANKER_TEXT", "CALIBRATION")[
        "target_physical_model_calls"
    ] == 48


def test_earlier_node_position_does_not_discount_uncontained_material_defect() -> None:
    result = evaluate_defect_lifecycle(
        [defect(workflow_stage_index=1, downstream_visible=True)]
    )
    row = result["defects"][0]
    assert row["node_defect_retained"] is True
    assert row["workflow_credit"] == "NO_CREDIT"
    assert row["workflow_safety_residual"] is True
    assert result["summary"]["current_mapping_verdict"] == "BLOCKED"


def test_actual_repair_gets_flow_credit_but_keeps_node_fault_and_r2_is_heavier() -> None:
    r1 = defect(
        detected=True,
        blocked=True,
        repaired=True,
        reverified=True,
        repair_round="R1",
        guard_kind="INDEPENDENT",
        guard_evidence_sha256="A" * 64,
    )
    r2 = defect(
        defect_id="D-02",
        case_id="CASE-02",
        panel_id="PANEL-02",
        family_id="FAMILY-02",
        detected=True,
        blocked=True,
        repaired=True,
        reverified=True,
        repair_round="R2",
        guard_kind="INDEPENDENT",
        guard_evidence_sha256="B" * 64,
    )
    result = evaluate_defect_lifecycle([r1, r2])
    first, second = result["defects"]
    assert first["containment_status"] == "CONTAINED_AFTER_R1"
    assert second["containment_status"] == "CONTAINED_AFTER_R2"
    assert first["node_defect_retained"] is True
    assert second["repair_burden"]["ordinal"] > first["repair_burden"]["ordinal"]
    assert result["summary"]["workflow_safety_residual_count"] == 0
    assert result["summary"]["current_mapping_verdict"] == "READY"


def test_theoretical_or_unverified_guard_never_earns_recovery_credit() -> None:
    result = evaluate_defect_lifecycle(
        [
            defect(
                detected=True,
                blocked=True,
                repaired=True,
                reverified=True,
                repair_round="R1",
                guard_kind="THEORETICAL",
                guard_evidence_sha256=None,
            )
        ]
    )
    row = result["defects"][0]
    assert row["containment_status"] == "UNVERIFIED_GUARD_NO_CREDIT"
    assert row["workflow_credit"] == "NO_CREDIT"
    assert row["workflow_safety_residual"] is True


def test_same_model_guard_records_actual_containment_but_requires_canary() -> None:
    result = evaluate_defect_lifecycle(
        [
            defect(
                detected=True,
                blocked=True,
                repaired=True,
                reverified=True,
                repair_round="R1",
                guard_kind="SAME_MODEL",
                guard_evidence_sha256="C" * 64,
            )
        ]
    )
    row = result["defects"][0]
    assert row["containment_status"] == "CONTAINED_CORRELATED_GUARD"
    assert row["workflow_safety_residual"] is False
    assert row["stable_guard_generalization_eligible"] is False
    assert row["mapping_canary_required"] is True
    assert result["summary"]["current_mapping_verdict"] == (
        "CONDITIONALLY_READY_CANARY_REQUIRED"
    )


def test_information_loss_needs_verified_source_recovery() -> None:
    result = evaluate_defect_lifecycle(
        [
            defect(
                severity="INFORMATION_LOSS",
                detected=True,
                blocked=True,
                repaired=True,
                reverified=True,
                repair_round="R1",
                guard_kind="INDEPENDENT",
                guard_evidence_sha256="D" * 64,
                source_recovery_verified=False,
            )
        ]
    )
    row = result["defects"][0]
    assert row["containment_status"] == "SOURCE_RECOVERY_NOT_VERIFIED"
    assert row["workflow_safety_residual"] is True


def test_controlled_omission_is_completeness_debt_not_safety_escape() -> None:
    result = evaluate_defect_lifecycle(
        [
            defect(
                severity="COMPLETENESS",
                detected=True,
                blocked=True,
                repaired=False,
                reverified=True,
                repair_round="CONTROLLED_OMISSION",
                guard_kind="INDEPENDENT",
                guard_evidence_sha256="E" * 64,
            )
        ]
    )
    row = result["defects"][0]
    assert row["containment_status"] == "CONTROLLED_OMISSION"
    assert row["workflow_safety_residual"] is False
    assert row["completeness_debt"] is True
    assert result["summary"]["current_mapping_verdict"] == (
        "READY_WITH_COMPLETENESS_DEBT"
    )


def test_uncontrolled_completeness_is_not_mislabeled_ready() -> None:
    result = evaluate_defect_lifecycle(
        [defect(severity="COMPLETENESS", downstream_visible=True)]
    )
    assert result["summary"]["workflow_safety_residual_count"] == 0
    assert result["summary"]["uncontrolled_completeness_count"] == 1
    assert result["summary"]["current_mapping_verdict"] == (
        "BLOCKED_COMPLETENESS"
    )


def test_single_escape_blocks_mapping_but_two_independent_escapes_establish_fail() -> None:
    single = evaluate_defect_lifecycle([defect(escaped=True)])
    assert single["summary"]["current_mapping_verdict"] == "BLOCKED"
    assert single["summary"]["model_failure_verdict"] == "NOT_ESTABLISHED"

    repeated = evaluate_defect_lifecycle(
        [
            defect(escaped=True),
            defect(
                defect_id="D-02",
                case_id="CASE-02",
                panel_id="PANEL-02",
                family_id="FAMILY-02",
                escaped=True,
            ),
        ]
    )
    assert repeated["summary"]["model_failure_verdict"] == "ESTABLISHED"
    assert repeated["summary"]["independent_escape_case_count"] == 2


class _QwenEmbeddingHttp:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def request(self, **request: Any) -> HttpResponse:
        self.calls.append(deepcopy(request))
        payload = json.loads(request["body"])
        return HttpResponse(
            200,
            json.dumps(
                {
                    "model": "Qwen/Qwen3-VL-Embedding-8B",
                    "data": [
                        {"index": index, "embedding": [float(index + 1), 1.0]}
                        for index, _value in enumerate(payload["input"])
                    ],
                    "usage": {
                        "prompt_tokens": len(payload["input"]),
                        "completion_tokens": 0,
                    },
                }
            ).encode("utf-8"),
        )


def test_qwen_embedding_transport_binds_32_input_limit_and_price(
    tmp_path: Path,
) -> None:
    http = _QwenEmbeddingHttp()
    runner = LiveModelValidationRunner(
        credential_resolver=lambda _reference: b"fixture-secret",
        preferences_loader=lambda: {
            "proxy_mode": "NONE",
            "proxy_address": "",
            "request_timeout_seconds": 30,
        },
        scratch_root=tmp_path / "qwen-embedding-runner",
        http_transport=http,
    )
    service = {
        "provider": "SILICONFLOW_API_KEY",
        "credential_ref": "REF:fixture",
        "model_name": "Qwen/Qwen3-VL-Embedding-8B",
    }
    result = runner.execute_embeddings(
        profile_kind="API",
        service=service,
        model=service,
        inputs=[f"input-{index}" for index in range(32)],
        purpose="workflow_model_exam",
        timeout_seconds=30,
    )
    assert result["status"] == "PASS"
    assert result["returned_model"] == "Qwen/Qwen3-VL-Embedding-8B"
    assert result["external_model_calls"] == 1
    assert result["execution_receipt"]["physical_batch_size"] == 32
    assert result["execution_receipt"]["estimated_cost_cny"] == 0.0000224
    assert result["execution_receipt"]["cost_evidence"] == (
        "MODEL_SPECIFIC_EMBEDDING_INPUT_PRICE_ESTIMATE"
    )
    assert len(http.calls) == 1


class _StrictLunaCodexProcess:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(self, **request: Any) -> ProcessResult:
        self.calls.append(dict(request))
        events = [
            {"type": "thread.started", "model": "gpt-5.6-luna"},
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": json.dumps(
                        {
                            "schema_version": "QualityNewLlmRerankResponse-v1",
                            "items": [
                                {"candidate_index": 0},
                                {"candidate_index": 1},
                            ],
                        }
                    ),
                },
            },
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        ]
        return ProcessResult(
            True,
            0,
            b"\n".join(json.dumps(event).encode("utf-8") for event in events),
            b"",
            duration_ms=7,
        )


def test_luna_structured_chat_strictly_binds_cli_binary_and_model(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"phase2-new-codex-cli-fixture")
    binary_sha256 = hashlib.sha256(executable.read_bytes()).hexdigest().upper()
    process = _StrictLunaCodexProcess()
    runner = LiveModelValidationRunner(
        credential_resolver=lambda _reference: b"unused",
        preferences_loader=lambda: {
            "proxy_mode": "NONE",
            "proxy_address": "",
            "request_timeout_seconds": 30,
        },
        scratch_root=tmp_path / "structured-chat",
        process_transport=process,
        executable_resolver=lambda _value: str(executable.resolve()),
    )
    service = {
        "adapter_id": "codex_cli",
        "executable": "codex",
        "cli_binary_sha256": binary_sha256,
        "cli_version": "codex-cli fixture",
        "cli_identity_authority_ref": "Execution_DIRECT_CLI_REQUEST_BINDING_V1_TEST",
    }
    model = {
        "model_name": "gpt-5.6-luna",
        "thinking_mode": "high",
        "max_output_tokens_mode": "PROVIDER_DEFAULT",
        "require_exact_cli_identity_binding": True,
    }
    response_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "items"],
        "properties": {
            "schema_version": {
                "type": "string",
                "const": "QualityNewLlmRerankResponse-v1",
            },
            "items": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["candidate_index"],
                    "properties": {
                        "candidate_index": {"type": "integer"},
                    },
                },
            },
        },
    }
    result = runner.execute_structured_chat(
        profile_kind="CLI",
        service=service,
        model=model,
        prompt="Rank both untrusted candidates.",
        response_schema=response_schema,
        purpose="workflow_model_exam",
        max_output_tokens=None,
        timeout_seconds=600,
    )
    assert result["status"] == "PASS"
    receipt = result["execution_receipt"]
    assert receipt["model_identity_evidence"] == "DIRECT_CLI_REQUEST_BINDING_V1"
    assert receipt["request_model_binding_status"] == (
        "EXACT_ARGV_BINARY_AND_FROZEN_ALLOWLIST"
    )
    assert receipt["cli_binary_sha256"] == binary_sha256
    assert receipt["cli_version"] == "codex-cli fixture"
    assert receipt["identity_authority_ref"] == (
        "Execution_DIRECT_CLI_REQUEST_BINDING_V1_TEST"
    )
    assert receipt["max_output_tokens_mode"] == "PROVIDER_DEFAULT"
    assert receipt["request_max_output_tokens"] is None
    assert len(process.calls) == 1

    drifted_service = {**service, "cli_binary_sha256": "0" * 64}
    with pytest.raises(
        ValueError,
        match="CLI_STRUCTURED_CHAT_BINARY_IDENTITY_BINDING_INVALID",
    ):
        runner.execute_structured_chat(
            profile_kind="CLI",
            service=drifted_service,
            model=model,
            prompt="Rank both untrusted candidates.",
            response_schema=response_schema,
            purpose="workflow_model_exam",
            max_output_tokens=None,
            timeout_seconds=600,
        )
    assert len(process.calls) == 1


