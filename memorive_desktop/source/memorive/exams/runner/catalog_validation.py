"""The check of the workflow exam catalog (revision 8) that WorkflowExams loads.
"""
from __future__ import annotations

from typing import Any, Mapping
from memorive.models.endpoints import normalize_external_api_base_url
from memorive.models.endpoints import (
    SAFE_MODEL,
    SAFE_REASON_CODE,
    SAFE_SHA256,
    SAFE_THINKING,
    json_sha256,
)
from memorive.exams.runner.metadata import WORKFLOW_EXAM_ROLE_NODE_BINDINGS


def _validate_workflow_exam_catalog_v8(
    value: Mapping[str, Any],
    *,
    allow_frozen_successor_cohort: bool = False,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    if set(value) != {
        "schema_version",
        "entries",
        "historical_entries",
        "catalog_sha256",
    }:
        raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SHAPE_INVALID")
    expected_catalog_sha256 = json_sha256(
        {key: item for key, item in value.items() if key != "catalog_sha256"}
    )
    if value.get("catalog_sha256") != expected_catalog_sha256:
        raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_HASH_INVALID")

    raw_historical = value.get("historical_entries")
    if not isinstance(raw_historical, list) or len(raw_historical) > 256:
        raise ValueError("WORKFLOW_MODEL_EXAM_HISTORICAL_ENTRIES_INVALID")
    historical: list[dict[str, Any]] = []
    historical_fields = {
        "cache_reuse_status",
        "historical_reason",
        "source_catalog_schema_version",
        "source_entry",
        "historical_record_sha256",
    }
    for raw in raw_historical:
        if not isinstance(raw, Mapping) or set(raw) != historical_fields:
            raise ValueError("WORKFLOW_MODEL_EXAM_HISTORICAL_ENTRY_INVALID")
        row = dict(raw)
        reason = row.get("historical_reason")
        source_schema = row.get("source_catalog_schema_version")
        if (
            row.get("cache_reuse_status") != "HISTORICAL_ONLY"
            or not isinstance(reason, str)
            or not SAFE_REASON_CODE.fullmatch(reason)
            or not isinstance(source_schema, str)
            or not SAFE_MODEL.fullmatch(source_schema)
            or not isinstance(row.get("source_entry"), Mapping)
            or row.get("historical_record_sha256")
            != json_sha256(
                {
                    key: item
                    for key, item in row.items()
                    if key != "historical_record_sha256"
                }
            )
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_HISTORICAL_ENTRY_INVALID")
        historical.append(row)

    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list) or len(raw_entries) > 64:
        raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRIES_INVALID")
    accepted: list[dict[str, Any]] = []
    identities: set[tuple[Any, ...]] = set()
    string_fields = (
        "node_id",
        "model_name",
        "score_semantics",
        "exam_category_id",
        "role_id",
        "reference_pack_id",
        "reference_pack_revision",
        "scoring_protocol_revision",
        "scoring_projection_revision",
        "comparison_cohort_id",
        "executor_ref",
        "sample_revision",
        "terminal_disposition",
        "executor_binding_kind",
        "series_aggregate_method",
    )
    sha_fields = (
        "evidence_sha256",
        "reference_pack_sha256",
        "scoring_protocol_sha256",
        "comparison_binding_sha256",
        "executor_binding_sha256",
        "sample_manifest_sha256",
        "cache_binding_sha256",
    )
    slot_fields = {
        "sample_slot",
        "sample_manifest_sha256",
        "score_exact",
        "verdict",
        "quality_verdict",
        "operational_eligibility_verdict",
        "operational_safety_gate",
        "terminal_disposition",
        "failed_exam_item_count",
        "safety_blocking_residual_count",
        "calibration_residual_count",
        "semantic_repair_rounds",
        "evidence_sha256",
        "comparison_binding_sha256",
        "physical_model_calls",
        "token_usage",
        "estimated_cost_cny",
    }
    compact_panel_fields = {
        "exam_evidence_sha256",
        "exam_run_id",
        "first_pass_score_exact",
        "physical_model_calls",
        "sample_slot",
        "score_exact",
        "semantic_repair_rounds",
    }
    for raw in raw_entries:
        if not isinstance(raw, Mapping):
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRY_INVALID")
        row = dict(raw)
        if (
            any(
                not isinstance(row.get(field), str)
                or not SAFE_MODEL.fullmatch(str(row[field]))
                for field in string_fields
            )
            or any(
                not isinstance(row.get(field), str)
                or not SAFE_SHA256.fullmatch(str(row[field]))
                for field in sha_fields
            )
            or row.get("cache_reuse_status") != "STABLE_REUSE"
            or row.get("cache_provenance_kind")
            not in (
                {
                    "THREE_SLOT_SUCCESSOR_SERIES",
                    "COMPLETE_REFERENCE_COHORT",
                    "FROZEN_SUCCESSOR_COHORT",
                }
                if allow_frozen_successor_cohort
                else {
                    "THREE_SLOT_SUCCESSOR_SERIES",
                    "COMPLETE_REFERENCE_COHORT",
                }
            )
            or row.get("execution_outcome") != "COMPLETED"
            or row.get("scorability") != "SCOREABLE"
            or row.get("verdict") not in {"PASS", "FAIL"}
            or row.get("quality_verdict")
            not in {"PASS", "FAIL", "NOT_ASSESSED"}
            or isinstance(row.get("score"), bool)
            or not isinstance(row.get("score"), int)
            or not 0 <= int(row["score"]) <= 100
            or isinstance(row.get("score_exact"), bool)
            or not isinstance(row.get("score_exact"), (int, float))
            or not 0.0 <= float(row["score_exact"]) <= 100.0
            or int(row["score"]) != int(round(float(row["score_exact"])))
            or row.get("qualification_verdict")
            not in {"PASS", "FAIL", "DISQUALIFIED", "NOT_ASSESSED"}
            or row.get("operational_eligibility_verdict")
            not in {
                "PASS",
                "SUITABLE",
                "SUITABLE_WITH_CALIBRATION",
                "SUITABLE_WITH_GOVERNED_DISPOSITION",
                "SUITABLE_WITH_DOMAIN_GUARD",
                "SUITABLE_WITH_STABILITY_MONITORING",
                "SUITABLE_WITH_RERANKER_AND_SOURCE_GUARD",
                "SUITABLE_WITH_RERANKER_STABILITY_MONITORING_AND_SOURCE_GUARD",
                "NOT_RECOMMENDED",
                "NOT_RECOMMENDED_CURRENT_CONFIGURATION",
                "REJECT",
                "NOT_ASSESSED",
            }
            or row.get("operational_safety_gate")
            not in {"PASS", "FAIL", "NOT_ASSESSED"}
            or not isinstance(row.get("category_score_is_descriptive"), bool)
            or row.get("horizontal_comparison_allowed_only_with_same_cohort")
            is not True
            or row.get("cross_category_comparison_forbidden") is not True
            or row.get("global_ranking_forbidden") is not True
            or row.get("provider_received_gold") is not False
            or not isinstance(row.get("local_reference_pack_embedded"), bool)
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_ENTRY_INVALID")
        if (
            row["quality_verdict"] == "NOT_ASSESSED"
            and row["category_score_is_descriptive"] is not True
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_DESCRIPTIVE_INVALID")
        allowed_nodes = WORKFLOW_EXAM_ROLE_NODE_BINDINGS.get(str(row["role_id"]))
        if allowed_nodes is None or row["node_id"] not in allowed_nodes:
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ROLE_NODE_MISMATCH")
        for field in (
            "failed_exam_item_count",
            "safety_blocking_residual_count",
            "calibration_residual_count",
            "semantic_repair_rounds",
            "physical_model_calls",
        ):
            item = row.get(field)
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_COUNT_INVALID")
        usage = row.get("token_usage")
        if not isinstance(usage, Mapping) or set(usage) != {
            "prompt_tokens",
            "completion_tokens",
        }:
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_USAGE_INVALID")
        if any(
            isinstance(usage.get(field), bool)
            or not isinstance(usage.get(field), int)
            or int(usage[field]) < 0
            for field in usage
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_USAGE_INVALID")

        profile_kind = row.get("profile_kind")
        if profile_kind == "API":
            if (
                not isinstance(row.get("provider_family"), str)
                or not SAFE_MODEL.fullmatch(str(row["provider_family"]))
                or not isinstance(row.get("thinking"), bool)
                or not isinstance(row.get("tier"), str)
                or not SAFE_MODEL.fullmatch(str(row["tier"]))
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_IDENTITY_INVALID")
            model_identity = {
                "node_id": row["node_id"],
                "profile_kind": profile_kind,
                "provider_family": row["provider_family"],
                "model_name": row["model_name"],
                "thinking": row["thinking"],
                "tier": row["tier"],
            }
            for field, pattern in (
                ("api_platform", SAFE_MODEL),
                ("credential_ref", SAFE_MODEL),
                ("thinking_mode", SAFE_THINKING),
            ):
                if field in row:
                    item = row.get(field)
                    if not isinstance(item, str) or not pattern.fullmatch(item):
                        raise ValueError(
                            "WORKFLOW_MODEL_EXAM_CATALOG_V9_API_ROUTE_IDENTITY_INVALID"
                        )
                    model_identity[field] = item
            if "base_url" in row:
                base_url = row.get("base_url")
                try:
                    normalized_base_url = normalize_external_api_base_url(base_url)
                except (TypeError, ValueError):
                    normalized_base_url = None
                if normalized_base_url != base_url:
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_CATALOG_V9_API_ROUTE_IDENTITY_INVALID"
                    )
                model_identity["base_url"] = base_url
            identity = (
                row["node_id"],
                profile_kind,
                row["provider_family"],
                row["model_name"],
                row["thinking"],
                row["tier"],
                model_identity.get("api_platform"),
                model_identity.get("base_url"),
                model_identity.get("credential_ref"),
                model_identity.get("thinking_mode"),
            )
        elif profile_kind == "CLI":
            if any(
                not isinstance(row.get(field), str)
                or not SAFE_MODEL.fullmatch(str(row[field]))
                for field in ("adapter_id", "thinking_mode")
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_IDENTITY_INVALID")
            identity = (
                row["node_id"],
                profile_kind,
                row["adapter_id"],
                row["model_name"],
                row["thinking_mode"],
            )
            model_identity = {
                "node_id": row["node_id"],
                "profile_kind": profile_kind,
                "adapter_id": row["adapter_id"],
                "model_name": row["model_name"],
                "thinking_mode": row["thinking_mode"],
            }
        elif profile_kind == "LOCAL":
            if (
                row.get("endpoint_kind") != "ollama"
                or not isinstance(row.get("model_digest"), str)
                or not SAFE_SHA256.fullmatch(str(row["model_digest"]))
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_IDENTITY_INVALID")
            identity = (
                row["node_id"],
                profile_kind,
                row["endpoint_kind"],
                row["model_name"],
                row["model_digest"],
            )
            model_identity = {
                "node_id": row["node_id"],
                "profile_kind": profile_kind,
                "endpoint_kind": row["endpoint_kind"],
                "model_name": row["model_name"],
                "model_digest": row["model_digest"],
            }
        else:
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_IDENTITY_INVALID")
        if identity in identities:
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_DUPLICATE_IDENTITY")
        identities.add(identity)

        series_slots = row.get("series_slots")
        if not isinstance(series_slots, list):
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SERIES_INVALID")
        if row["cache_provenance_kind"] == "THREE_SLOT_SUCCESSOR_SERIES":
            if len(series_slots) != 3:
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SERIES_INVALID")
            slots = []
            slot_scores = []
            evidence_hashes = []
            sample_hashes = []
            pass_all = True
            summed = {
                "failed_exam_item_count": 0,
                "safety_blocking_residual_count": 0,
                "calibration_residual_count": 0,
                "semantic_repair_rounds": 0,
                "physical_model_calls": 0,
            }
            for slot in series_slots:
                if not isinstance(slot, Mapping) or set(slot) != slot_fields:
                    raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SLOT_INVALID")
                number = slot.get("sample_slot")
                if isinstance(number, bool) or number not in {1, 2, 3}:
                    raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SLOT_INVALID")
                if any(
                    not isinstance(slot.get(field), str)
                    or not SAFE_SHA256.fullmatch(str(slot[field]))
                    for field in (
                        "sample_manifest_sha256",
                        "evidence_sha256",
                        "comparison_binding_sha256",
                    )
                ):
                    raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SLOT_INVALID")
                score = slot.get("score_exact")
                if (
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not 0.0 <= float(score) <= 100.0
                    or slot.get("verdict") not in {"PASS", "FAIL"}
                    or slot.get("quality_verdict") not in {"PASS", "FAIL"}
                ):
                    raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SLOT_INVALID")
                slot_usage = slot.get("token_usage")
                if not isinstance(slot_usage, Mapping) or set(slot_usage) != {
                    "prompt_tokens",
                    "completion_tokens",
                }:
                    raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SLOT_INVALID")
                for field in summed:
                    item = slot.get(field)
                    if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                        raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SLOT_INVALID")
                    summed[field] += item
                slots.append(number)
                slot_scores.append(float(score))
                evidence_hashes.append(str(slot["evidence_sha256"]))
                sample_hashes.append(str(slot["sample_manifest_sha256"]))
                pass_all &= slot["verdict"] == "PASS"
            if (
                sorted(slots) != [1, 2, 3]
                or len(set(sample_hashes)) != 3
                or abs(
                    float(row["score_exact"])
                    - round(sum(slot_scores) / 3.0, 6)
                )
                > 0.000001
                or row["verdict"] != ("PASS" if pass_all else "FAIL")
                or any(row[field] != value for field, value in summed.items())
                or row["sample_manifest_sha256"]
                != json_sha256(sample_hashes)
                or row["evidence_sha256"]
                != json_sha256(evidence_hashes)
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SERIES_INVALID")
        elif (
            row["cache_provenance_kind"] == "FROZEN_SUCCESSOR_COHORT"
            and series_slots
        ):
            if (
                row["role_id"] != "ANALYSIS_PRIMARY"
                or row["series_aggregate_method"] != "FROZEN_SUCCESSOR_COHORT"
                or len(series_slots) != 3
                or row.get("cohort_panel_count") != 3
            ):
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_CATALOG_V9_COMPACT_PANEL_SERIES_INVALID"
                )
            compact_slots: list[int] = []
            compact_scores: list[float] = []
            compact_evidence: list[str] = []
            compact_runs: list[str] = []
            compact_calls = 0
            compact_repairs = 0
            for slot in series_slots:
                if not isinstance(slot, Mapping) or set(slot) != compact_panel_fields:
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_CATALOG_V9_COMPACT_PANEL_SLOT_INVALID"
                    )
                number = slot.get("sample_slot")
                score = slot.get("score_exact")
                first_pass = slot.get("first_pass_score_exact")
                calls = slot.get("physical_model_calls")
                repairs = slot.get("semantic_repair_rounds")
                evidence = slot.get("exam_evidence_sha256")
                run_id = slot.get("exam_run_id")
                if (
                    isinstance(number, bool)
                    or number not in {1, 2, 3}
                    or isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not 0.0 <= float(score) <= 100.0
                    or isinstance(first_pass, bool)
                    or not isinstance(first_pass, (int, float))
                    or not 0.0 <= float(first_pass) <= 100.0
                    or isinstance(calls, bool)
                    or not isinstance(calls, int)
                    or calls < 0
                    or isinstance(repairs, bool)
                    or not isinstance(repairs, int)
                    or repairs < 0
                    or not isinstance(evidence, str)
                    or not SAFE_SHA256.fullmatch(evidence)
                    or not isinstance(run_id, str)
                    or not SAFE_MODEL.fullmatch(run_id)
                ):
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_CATALOG_V9_COMPACT_PANEL_SLOT_INVALID"
                    )
                compact_slots.append(int(number))
                compact_scores.append(float(score))
                compact_evidence.append(evidence)
                compact_runs.append(run_id)
                compact_calls += calls
                compact_repairs += repairs
            panel_range = row.get("cohort_panel_score_range")
            if (
                sorted(compact_slots) != [1, 2, 3]
                or len(set(compact_evidence)) != 3
                or len(set(compact_runs)) != 3
                or isinstance(panel_range, bool)
                or not isinstance(panel_range, (int, float))
                or abs(
                    float(panel_range)
                    - round(max(compact_scores) - min(compact_scores), 6)
                )
                > 0.000001
                or row["physical_model_calls"] < compact_calls
                or row["semantic_repair_rounds"] != compact_repairs
            ):
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_CATALOG_V9_COMPACT_PANEL_SERIES_INVALID"
                )
        else:
            expected_aggregate_method = (
                "FROZEN_SUCCESSOR_COHORT"
                if row["cache_provenance_kind"] == "FROZEN_SUCCESSOR_COHORT"
                else "COMPLETE_REFERENCE_COHORT"
            )
            if series_slots or row["series_aggregate_method"] != expected_aggregate_method:
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_COHORT_INVALID")

        cache_binding = row.get("cache_binding")
        if not isinstance(cache_binding, Mapping):
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_BINDING_INVALID")
        category_binding = cache_binding.get("category_binding")
        expected_category_binding = {
            "category_id": row["exam_category_id"],
            "role_id": row["role_id"],
            "reference_pack_id": row["reference_pack_id"],
            "reference_pack_revision": row["reference_pack_revision"],
            "reference_pack_sha256": row["reference_pack_sha256"],
            "scoring_protocol_revision": row["scoring_protocol_revision"],
            "scoring_protocol_sha256": row["scoring_protocol_sha256"],
            "scoring_projection_revision": row["scoring_projection_revision"],
            "executor_ref": row["executor_ref"],
            "sample_revision": row["sample_revision"],
            "sample_manifest_sha256": row["sample_manifest_sha256"],
        }
        expected_binding_hash = json_sha256(
            {
                key: item
                for key, item in cache_binding.items()
                if key != "cache_binding_sha256"
            }
        )
        if (
            cache_binding.get("schema_version")
            != "WorkflowModelExamCacheBinding-v2"
            or category_binding != expected_category_binding
            or cache_binding.get("model_identity") != model_identity
            or cache_binding.get("executor_binding_sha256")
            != row["executor_binding_sha256"]
            or cache_binding.get("executor_binding_kind")
            != row["executor_binding_kind"]
            or cache_binding.get("evidence_set_sha256")
            != row["evidence_sha256"]
            or cache_binding.get("cache_provenance_kind")
            != row["cache_provenance_kind"]
            or cache_binding.get("outcome_contract_revision")
            != "Desktop_EXAM_OUTCOME_CONTRACT_V1"
            or cache_binding.get("cross_category_comparison_forbidden") is not True
            or cache_binding.get("global_ranking_forbidden") is not True
            or cache_binding.get("cache_binding_sha256") != expected_binding_hash
            or row["cache_binding_sha256"] != expected_binding_hash
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_BINDING_INVALID")
        accepted.append(row)
    return tuple(accepted), tuple(historical)
