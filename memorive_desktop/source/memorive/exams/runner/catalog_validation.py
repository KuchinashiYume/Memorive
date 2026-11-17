"""Reading a stored workflow exam score catalog: validate_workflow_exam_catalog takes a catalog of any
revision (the current one, 8 and 9 through _validate_workflow_exam_catalog_v8, 1 to 7 itself) and returns
its entries and historical entries. WorkflowExams loads its catalog through it.
"""
from __future__ import annotations

from typing import Any, Mapping

from memorive.exams.catalog import Quality_NEW_CATALOG_SCHEMA, validate_quality_new_catalog
from memorive.models.endpoints import normalize_external_api_base_url
from memorive.models.endpoints import (
    SAFE_MODEL,
    SAFE_REASON_CODE,
    SAFE_SHA256,
    SAFE_THINKING,
    json_sha256,
)
from memorive.exams.runner.metadata import (
    WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V7,
    WORKFLOW_EXAM_OPERATIONAL_METADATA_FIELDS_V5,
    WORKFLOW_EXAM_REPAIR_METADATA_FIELDS_V6,
    WORKFLOW_EXAM_ROLE_NODE_BINDINGS,
    WORKFLOW_EXAM_SCORE_METADATA_FIELDS_V4,
)


def _historical_entries_v8(value):
    """The historical entries of a revision 8 or 9 catalog, each with its record hash."""
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
    return historical


def _entry_row_v8(raw, allow_frozen_successor_cohort):
    """One entry as a dict: its strings, hashes, verdicts, scores, counts and token usage well formed."""
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
    return row


def _entry_identity_v8(row):
    """The entry's identity by profile kind (API, CLI or local) and the model identity its cache
    binding must carry."""
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
    return model_identity, identity


def _check_successor_series_v8(row, series_slots):
    """Three successor samples: each slot well formed, and the entry's score, verdict, counts and
    hashes aggregate them."""
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


def _check_compact_panel_v8(row, series_slots):
    """A frozen analysis cohort's compact panel of three runs: each slot well formed, and the panel's
    range, calls and repairs agree with the entry."""
    compact_panel_fields = {
        "exam_evidence_sha256",
        "exam_run_id",
        "first_pass_score_exact",
        "physical_model_calls",
        "sample_slot",
        "score_exact",
        "semantic_repair_rounds",
    }
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


def _check_series_v8(row):
    """The entry's series slots: three successor samples, a compact panel of a frozen cohort, or none."""
    series_slots = row.get("series_slots")
    if not isinstance(series_slots, list):
        raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_SERIES_INVALID")
    if row["cache_provenance_kind"] == "THREE_SLOT_SUCCESSOR_SERIES":
        _check_successor_series_v8(row, series_slots)
    elif (
        row["cache_provenance_kind"] == "FROZEN_SUCCESSOR_COHORT"
        and series_slots
    ):
        _check_compact_panel_v8(row, series_slots)
    else:
        expected_aggregate_method = (
            "FROZEN_SUCCESSOR_COHORT"
            if row["cache_provenance_kind"] == "FROZEN_SUCCESSOR_COHORT"
            else "COMPLETE_REFERENCE_COHORT"
        )
        if series_slots or row["series_aggregate_method"] != expected_aggregate_method:
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V8_COHORT_INVALID")


def _check_cache_binding_v8(row, model_identity):
    """The entry's cache binding: its category binding, model identity and hash over its own fields."""
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


def _validate_workflow_exam_catalog_v8(
    value: Mapping[str, Any],
    *,
    allow_frozen_successor_cohort: bool = False,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    """The entries and historical entries of a revision 8 or 9 catalog, each checked, and the
    catalog's own hash; an entry from a frozen successor cohort only when allowed."""
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

    historical = _historical_entries_v8(value)

    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list) or len(raw_entries) > 64:
        raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRIES_INVALID")
    accepted: list[dict[str, Any]] = []
    identities: set[tuple[Any, ...]] = set()
    for raw in raw_entries:
        row = _entry_row_v8(raw, allow_frozen_successor_cohort)

        model_identity, identity = _entry_identity_v8(row)
        if identity in identities:
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_DUPLICATE_IDENTITY")
        identities.add(identity)

        _check_series_v8(row)

        _check_cache_binding_v8(row, model_identity)
        accepted.append(row)
    return tuple(accepted), tuple(historical)


def _historical_entries(schema_version, value):
    """A revision 7 catalog's own hash and its historical entries (each with its record hash); () for
    older revisions."""
    historical_entries: tuple[dict[str, Any], ...] = ()
    if schema_version == "WorkflowModelExamScoreCatalog-v7":
        if set(value) != {
            "schema_version",
            "entries",
            "historical_entries",
            "catalog_sha256",
        }:
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_V7_SHAPE_INVALID")
        catalog_sha256 = value.get("catalog_sha256")
        expected_catalog_sha256 = json_sha256(
            {key: item for key, item in value.items() if key != "catalog_sha256"}
        )
        if (
            not isinstance(catalog_sha256, str)
            or not SAFE_SHA256.fullmatch(catalog_sha256)
            or catalog_sha256 != expected_catalog_sha256
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_HASH_INVALID")
        raw_historical = value.get("historical_entries")
        if not isinstance(raw_historical, list) or len(raw_historical) > 64:
            raise ValueError(
                "WORKFLOW_MODEL_EXAM_HISTORICAL_ENTRIES_INVALID"
            )
        accepted_historical: list[dict[str, Any]] = []
        for raw_historical_row in raw_historical:
            if not isinstance(raw_historical_row, Mapping) or set(
                raw_historical_row
            ) != {
                "cache_reuse_status",
                "historical_reason",
                "source_catalog_schema_version",
                "source_entry",
                "historical_record_sha256",
            }:
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_HISTORICAL_ENTRY_INVALID"
                )
            historical_row = dict(raw_historical_row)
            source_entry = historical_row.get("source_entry")
            historical_hash = historical_row.get(
                "historical_record_sha256"
            )
            expected_historical_hash = json_sha256(
                {key: item for key, item in historical_row.items() if key != "historical_record_sha256"}
            )
            if (
                historical_row.get("cache_reuse_status")
                != "HISTORICAL_ONLY"
                or historical_row.get("historical_reason")
                != "INCOMPATIBLE_CARD_REVIEWER_PRE_SUCCESSOR_SINGLE_RUN"
                or historical_row.get("source_catalog_schema_version")
                != "WorkflowModelExamScoreCatalog-v6"
                or not isinstance(source_entry, Mapping)
                or source_entry.get("role_id") != "CARD_REVIEWER"
                or not isinstance(source_entry.get("score"), int)
                or isinstance(source_entry.get("score"), bool)
                or not 0 <= int(source_entry["score"]) <= 100
                or not isinstance(source_entry.get("evidence_sha256"), str)
                or not SAFE_SHA256.fullmatch(
                    str(source_entry["evidence_sha256"])
                )
                or not isinstance(historical_hash, str)
                or historical_hash != expected_historical_hash
            ):
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_HISTORICAL_ENTRY_INVALID"
                )
            accepted_historical.append(historical_row)
        historical_entries = tuple(accepted_historical)
    return historical_entries


def _entry_row(raw):
    """One entry as a dict, with the fields every revision has present and well formed."""
    if not isinstance(raw, Mapping):
        raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRY_INVALID")
    row = dict(raw)
    if not {
        "node_id",
        "profile_kind",
        "model_name",
        "verdict",
        "score",
        "evidence_sha256",
    }.issubset(row):
        raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRY_INVALID")
    common_invalid = (
        not isinstance(row["node_id"], str)
        or not row["node_id"]
        or not isinstance(row["model_name"], str)
        or not SAFE_MODEL.fullmatch(row["model_name"])
        or row["verdict"] not in {"PASS", "FAIL"}
        or isinstance(row["score"], bool)
        or not isinstance(row["score"], int)
        or not 0 <= row["score"] <= 100
        or not isinstance(row["evidence_sha256"], str)
        or not SAFE_SHA256.fullmatch(row["evidence_sha256"])
    )
    if common_invalid:
        raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRY_INVALID")
    return row


def _check_score_fields(row):
    """Revision 4 and later: the exact score, the scoring and comparison fields, and a role that may score
    this node."""
    score_exact = row.get("score_exact")
    string_fields = (
        "score_semantics",
        "exam_category_id",
        "role_id",
        "scoring_projection_revision",
        "comparison_cohort_id",
    )
    if (
        isinstance(score_exact, bool)
        or not isinstance(score_exact, (int, float))
        or not 0.0 <= float(score_exact) <= 100.0
        or row["score"] != int(round(float(score_exact)))
        or row.get("qualification_verdict")
        not in {"PASS", "FAIL", "DISQUALIFIED"}
        or any(
            not isinstance(row.get(field), str)
            or not SAFE_MODEL.fullmatch(str(row[field]))
            for field in string_fields
        )
        or not isinstance(
            row.get("horizontal_comparison_eligible"), bool
        )
        or row.get(
            "horizontal_comparison_allowed_only_with_same_cohort"
        )
        is not True
        or row.get("cross_category_comparison_forbidden") is not True
        or row.get("global_ranking_forbidden") is not True
    ):
        raise ValueError(
            "WORKFLOW_MODEL_EXAM_CATALOG_SCORE_METADATA_INVALID"
        )
    allowed_nodes = WORKFLOW_EXAM_ROLE_NODE_BINDINGS.get(
        str(row["role_id"])
    )
    if (
        allowed_nodes is None
        or row["node_id"] not in allowed_nodes
    ):
        raise ValueError(
            "WORKFLOW_MODEL_EXAM_CATALOG_ROLE_NODE_MISMATCH"
        )


def _check_operational_fields(row):
    """Revision 5 and later: the operational verdict, its policy and safety gate, and the residual counts."""
    operational_verdict = row.get(
        "operational_eligibility_verdict"
    )
    operational_policy = row.get(
        "operational_policy_revision"
    )
    operational_gate = row.get("operational_safety_gate")
    safety_count = row.get("safety_blocking_residual_count")
    calibration_count = row.get("calibration_residual_count")
    count_values_valid = all(
        value is None
        or (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        )
        for value in (safety_count, calibration_count)
    )
    if (
        operational_verdict
        not in {
            "SUITABLE",
            "SUITABLE_WITH_CALIBRATION",
            "SUITABLE_WITH_GOVERNED_DISPOSITION",
            "NOT_RECOMMENDED",
            "REJECT",
            "NOT_ASSESSED",
        }
        or not isinstance(operational_policy, str)
        or not SAFE_MODEL.fullmatch(operational_policy)
        or operational_gate not in {"PASS", "FAIL", "NOT_ASSESSED"}
        or not count_values_valid
        or (
            operational_verdict == "NOT_ASSESSED"
            and (
                operational_gate != "NOT_ASSESSED"
                or safety_count is not None
                or calibration_count is not None
            )
        )
        or (
            operational_verdict
            in {
                "SUITABLE",
                "SUITABLE_WITH_CALIBRATION",
                "SUITABLE_WITH_GOVERNED_DISPOSITION",
            }
            and (
                row["verdict"] != "PASS"
                or operational_gate != "PASS"
                or safety_count != 0
                or not isinstance(calibration_count, int)
                or row["role_id"] != "CARD_REVIEWER"
            )
        )
    ):
        raise ValueError(
            "WORKFLOW_MODEL_EXAM_CATALOG_OPERATIONAL_METADATA_INVALID"
        )
    return operational_verdict


def _check_repair_fields(row, operational_verdict):
    """Revision 6 and later: the repair rates and collision count of a card reviewer that was assessed."""
    exact_rate = row.get("exact_case_repair_rate")
    deficit_rate = row.get(
        "case_score_deficit_recovery_rate"
    )
    collision_count = row.get(
        "repair_metadata_collision_suspect_count"
    )
    adjusted_rate = row.get(
        "collision_adjusted_repair_diagnostic_rate"
    )
    rates = (exact_rate, deficit_rate, adjusted_rate)
    all_rates_none = all(value is None for value in rates)
    all_rates_valid = all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0.0 <= float(value) <= 1.0
        for value in rates
    )
    reviewer_repair_assessed = (
        row["role_id"] == "CARD_REVIEWER"
        and operational_verdict != "NOT_ASSESSED"
    )
    if (
        (
            reviewer_repair_assessed
            and (
                not all_rates_valid
                or not isinstance(collision_count, int)
                or isinstance(collision_count, bool)
                or collision_count < 0
                or float(adjusted_rate)
                < float(exact_rate)
            )
        )
        or (
            not reviewer_repair_assessed
            and (
                not all_rates_none
                or collision_count is not None
            )
        )
    ):
        raise ValueError(
            "WORKFLOW_MODEL_EXAM_CATALOG_REPAIR_METADATA_INVALID"
        )


def _check_cache_fields(row):
    """Revision 7: the cache strings, a stable reuse status, a known provenance and the repair counts."""
    cache_strings = (
        "executor_ref",
        "sample_revision",
        "repair_prompt_projection_revision",
        "pointer_normalization_revision",
        "subject_transport_projection_revision",
        "series_aggregate_method",
    )
    series_slots = row.get("series_slots")
    provenance = row.get("cache_provenance_kind")
    core_closure_score = row.get("core_closure_score")
    semantic_repair_rounds = row.get(
        "semantic_repair_rounds"
    )
    failed_exam_item_count = row.get(
        "failed_exam_item_count"
    )
    if (
        row.get("cache_reuse_status") != "STABLE_REUSE"
        or provenance
        not in {
            "SINGLE_FROZEN_EVIDENCE",
            "THREE_SLOT_SUCCESSOR_SERIES",
        }
        or any(
            not isinstance(row.get(field), str)
            or not SAFE_MODEL.fullmatch(str(row[field]))
            for field in cache_strings
        )
        or row.get("core_closure_verdict")
        not in {
            "DELTA_PASS",
            "PASS_WITH_CALIBRATION",
            "PASS_WITH_GOVERNED_DISPOSITION",
            "HUMAN_REQUIRED",
            "NOT_ASSESSED",
        }
        or (
            core_closure_score is not None
            and (
                isinstance(core_closure_score, bool)
                or not isinstance(
                    core_closure_score, (int, float)
                )
                or not 0.0
                <= float(core_closure_score)
                <= 100.0
            )
        )
        or (
            semantic_repair_rounds is not None
            and (
                isinstance(semantic_repair_rounds, bool)
                or not isinstance(
                    semantic_repair_rounds, int
                )
                or semantic_repair_rounds < 0
            )
        )
        or (
            failed_exam_item_count is not None
            and (
                isinstance(failed_exam_item_count, bool)
                or not isinstance(failed_exam_item_count, int)
                or failed_exam_item_count < 0
            )
        )
        or not isinstance(series_slots, list)
    ):
        raise ValueError(
            "WORKFLOW_MODEL_EXAM_CATALOG_CACHE_METADATA_INVALID"
        )
    return series_slots, provenance, core_closure_score, semantic_repair_rounds, failed_exam_item_count


def _series_slot_fields(row, series_slots, core_closure_score, semantic_repair_rounds, failed_exam_item_count):
    """The fields every slot of a three-sample series has; a series is for card reviewers only and
    aggregates by the mean of first passes."""
    slot_fields = {
        "sample_slot",
        "sample_manifest_sha256",
        "score_exact",
        "verdict",
        "qualification_verdict",
        "operational_eligibility_verdict",
        "operational_safety_gate",
        "safety_blocking_residual_count",
        "calibration_residual_count",
        "core_closure_verdict",
        "core_closure_score",
        "failed_exam_item_count",
        "semantic_repair_rounds",
        "exact_case_repair_rate",
        "case_score_deficit_recovery_rate",
        "repair_metadata_collision_suspect_count",
        "collision_adjusted_repair_diagnostic_rate",
        "evidence_sha256",
        "comparison_binding_sha256",
    }
    if (
        row["role_id"] != "CARD_REVIEWER"
        or row["series_aggregate_method"]
        != "MEAN_FIRST_PASS_NONLINEAR_ROLE_ABILITY_ACROSS_THREE_DISJOINT_SLOTS"
        or len(series_slots) != 3
        or core_closure_score is None
        or semantic_repair_rounds is None
        or failed_exam_item_count is None
    ):
        raise ValueError(
            "WORKFLOW_MODEL_EXAM_CATALOG_SERIES_INCOMPLETE"
        )
    return slot_fields


def _check_series_slot(slot, slot_fields):
    """One slot of the series: its fields, number, score, closure score, counts and rates well formed."""
    if not isinstance(slot, Mapping) or set(slot) != slot_fields:
        raise ValueError(
            "WORKFLOW_MODEL_EXAM_CATALOG_SERIES_SLOT_INVALID"
        )
    slot_number = slot.get("sample_slot")
    slot_score = slot.get("score_exact")
    slot_core_score = slot.get(
        "core_closure_score"
    )
    integer_fields = (
        "safety_blocking_residual_count",
        "calibration_residual_count",
        "failed_exam_item_count",
        "semantic_repair_rounds",
        "repair_metadata_collision_suspect_count",
    )
    rate_fields = (
        "exact_case_repair_rate",
        "case_score_deficit_recovery_rate",
        "collision_adjusted_repair_diagnostic_rate",
    )
    if (
        isinstance(slot_number, bool)
        or not isinstance(slot_number, int)
        or slot_number not in {1, 2, 3}
        or isinstance(slot_score, bool)
        or not isinstance(slot_score, (int, float))
        or not 0.0 <= float(slot_score) <= 100.0
        or isinstance(slot_core_score, bool)
        or not isinstance(
            slot_core_score, (int, float)
        )
        or not 0.0
        <= float(slot_core_score)
        <= 100.0
        or slot.get("verdict") not in {"PASS", "FAIL"}
        or slot.get("qualification_verdict")
        not in {"PASS", "DISQUALIFIED"}
        or slot.get("operational_eligibility_verdict")
        not in {
            "SUITABLE",
            "SUITABLE_WITH_CALIBRATION",
            "SUITABLE_WITH_GOVERNED_DISPOSITION",
            "NOT_RECOMMENDED",
            "REJECT",
        }
        or slot.get("operational_safety_gate")
        not in {"PASS", "FAIL"}
        or slot.get("core_closure_verdict")
        not in {
            "DELTA_PASS",
            "PASS_WITH_CALIBRATION",
            "PASS_WITH_GOVERNED_DISPOSITION",
            "HUMAN_REQUIRED",
        }
        or any(
            isinstance(slot.get(field), bool)
            or not isinstance(slot.get(field), int)
            or int(slot[field]) < 0
            for field in integer_fields
        )
        or any(
            isinstance(slot.get(field), bool)
            or not isinstance(
                slot.get(field), (int, float)
            )
            or not 0.0 <= float(slot[field]) <= 1.0
            for field in rate_fields
        )
        or float(
            slot[
                "collision_adjusted_repair_diagnostic_rate"
            ]
        )
        < float(slot["exact_case_repair_rate"])
        or any(
            not isinstance(slot.get(field), str)
            or not SAFE_SHA256.fullmatch(
                str(slot[field])
            )
            for field in (
                "sample_manifest_sha256",
                "evidence_sha256",
                "comparison_binding_sha256",
            )
        )
    ):
        raise ValueError(
            "WORKFLOW_MODEL_EXAM_CATALOG_SERIES_SLOT_INVALID"
        )


def _check_series(row, provenance, series_slots, core_closure_score, semantic_repair_rounds,
                  failed_exam_item_count):
    """Revision 7: an entry from single frozen evidence has no series; a three-sample successor series
    is well formed and the entry aggregates its slots. True for a successor series."""
    if provenance == "SINGLE_FROZEN_EVIDENCE":
        if (
            series_slots
            or row["series_aggregate_method"]
            != "SINGLE_FROZEN_EXAM"
            or row["sample_revision"] != "NOT_APPLICABLE"
            or row["repair_prompt_projection_revision"]
            != "NOT_APPLICABLE"
            or row["pointer_normalization_revision"]
            != "NOT_APPLICABLE"
            or row["subject_transport_projection_revision"]
            != "NOT_APPLICABLE"
            or row["core_closure_verdict"]
            != "NOT_ASSESSED"
            or core_closure_score is not None
            or semantic_repair_rounds is not None
            or failed_exam_item_count is not None
        ):
            raise ValueError(
                "WORKFLOW_MODEL_EXAM_CATALOG_SINGLE_CACHE_INVALID"
            )
        return False
    else:
        slot_fields = _series_slot_fields(
            row, series_slots, core_closure_score, semantic_repair_rounds, failed_exam_item_count
        )
        for slot in series_slots:
            _check_series_slot(slot, slot_fields)
        _check_series_aggregate(row, series_slots)
        return True
def _check_series_aggregate(row, slots):
    """The entry aggregates its three checked slots: one each of slots 1 to 3 with distinct samples,
    the means of the scores and rates, the sums of the counts, the weakest operational and closure
    verdicts, and PASS only when every slot passes."""
    def mean(field):
        return round(sum(float(slot[field]) for slot in slots) / len(slots), 9)

    def total(field):
        return sum(int(slot[field]) for slot in slots)

    def weakest(field, order):
        return max((slot[field] for slot in slots), key=order.index)

    operational = weakest(
        "operational_eligibility_verdict",
        ["SUITABLE", "SUITABLE_WITH_CALIBRATION", "SUITABLE_WITH_GOVERNED_DISPOSITION", "NOT_RECOMMENDED",
         "REJECT"],
    )
    slot_safety = total("safety_blocking_residual_count")
    if (
        sorted(slot["sample_slot"] for slot in slots) != [1, 2, 3]
        or len({str(slot["sample_manifest_sha256"]) for slot in slots}) != 3
        or abs(float(row["score_exact"]) - mean("score_exact")) > 0.000001
        or row["score"] != int(round(float(row["score_exact"])))
        or row["verdict"] != ("PASS" if all(slot["verdict"] == "PASS" for slot in slots) else "FAIL")
        or row["qualification_verdict"]
        != ("PASS" if all(slot["qualification_verdict"] == "PASS" for slot in slots) else "DISQUALIFIED")
        or row["operational_eligibility_verdict"]
        != ("NOT_RECOMMENDED" if operational == "REJECT" else operational)
        or row["operational_safety_gate"] != ("PASS" if slot_safety == 0 else "FAIL")
        or row["safety_blocking_residual_count"] != slot_safety
        or row["calibration_residual_count"] != total("calibration_residual_count")
        or row["failed_exam_item_count"] != total("failed_exam_item_count")
        or row["semantic_repair_rounds"] != total("semantic_repair_rounds")
        or row["core_closure_verdict"]
        != weakest(
            "core_closure_verdict",
            ["DELTA_PASS", "PASS_WITH_CALIBRATION", "PASS_WITH_GOVERNED_DISPOSITION", "HUMAN_REQUIRED"],
        )
        or abs(float(row["core_closure_score"]) - mean("core_closure_score")) > 0.000001
        or abs(float(row["exact_case_repair_rate"]) - mean("exact_case_repair_rate")) > 0.000001
        or abs(float(row["case_score_deficit_recovery_rate"]) - mean("case_score_deficit_recovery_rate"))
        > 0.000001
        or row["repair_metadata_collision_suspect_count"] != total("repair_metadata_collision_suspect_count")
        or abs(
            float(row["collision_adjusted_repair_diagnostic_rate"])
            - mean("collision_adjusted_repair_diagnostic_rate")
        )
        > 0.000001
    ):
        raise ValueError(
            "WORKFLOW_MODEL_EXAM_CATALOG_SERIES_AGGREGATE_INVALID"
        )


def _check_cache_binding(row):
    """Revision 7: the entry's cache binding hash over its own fields."""
    cache_binding = row.get("cache_binding_sha256")
    expected_cache_binding = json_sha256(
        {key: item for key, item in row.items() if key != "cache_binding_sha256"}
    )
    if (
        not isinstance(cache_binding, str)
        or cache_binding != expected_cache_binding
    ):
        raise ValueError(
            "WORKFLOW_MODEL_EXAM_CATALOG_CACHE_BINDING_INVALID"
        )


def _check_comparison(row, successor_series):
    """Revision 4 and later: an entry eligible for horizontal comparison names its reference pack and
    cohort, and its comparison binding hash matches."""
    comparable_fields = (
        "reference_pack_id",
        "reference_pack_revision",
        "reference_pack_sha256",
        "scoring_protocol_revision",
        "scoring_protocol_sha256",
        "comparison_binding_sha256",
    )
    if row["horizontal_comparison_eligible"]:
        if (
            any(
                not isinstance(row.get(field), str)
                or not row[field]
                for field in comparable_fields
            )
            or not SAFE_SHA256.fullmatch(
                str(row["reference_pack_sha256"])
            )
            or not SAFE_SHA256.fullmatch(
                str(row["scoring_protocol_sha256"])
            )
            or not SAFE_SHA256.fullmatch(
                str(row["comparison_binding_sha256"])
            )
        ):
            raise ValueError(
                "WORKFLOW_MODEL_EXAM_CATALOG_COMPARISON_BINDING_INVALID"
            )
        binding = {
            "exam_category_id": row["exam_category_id"],
            "reference_pack_id": row["reference_pack_id"],
            "reference_pack_revision": row[
                "reference_pack_revision"
            ],
            "reference_pack_sha256": row[
                "reference_pack_sha256"
            ],
            "role_id": row["role_id"],
            "score_semantics": row["score_semantics"],
            "scoring_projection_revision": row[
                "scoring_projection_revision"
            ],
            "scoring_protocol_revision": row[
                "scoring_protocol_revision"
            ],
            "scoring_protocol_sha256": row[
                "scoring_protocol_sha256"
            ],
        }
        if successor_series:
            binding.update(
                {
                    "executor_ref": row["executor_ref"],
                    "sample_revision": row["sample_revision"],
                    "sample_manifest_sha256s": [
                        slot["sample_manifest_sha256"]
                        for slot in sorted(
                            row["series_slots"],
                            key=lambda item: item["sample_slot"],
                        )
                    ],
                    "repair_prompt_projection_revision": row[
                        "repair_prompt_projection_revision"
                    ],
                    "pointer_normalization_revision": row[
                        "pointer_normalization_revision"
                    ],
                    "subject_transport_projection_revision": row[
                        "subject_transport_projection_revision"
                    ],
                }
            )
        expected_binding = json_sha256(binding)
        if (
            row["comparison_binding_sha256"]
            != expected_binding
            or row["comparison_cohort_id"]
            != (
                f"{row['role_id']}:SERIES:{expected_binding}"
                if successor_series
                else f"{row['role_id']}:{expected_binding}"
            )
        ):
            raise ValueError(
                "WORKFLOW_MODEL_EXAM_CATALOG_COMPARISON_BINDING_INVALID"
            )
    elif (
        any(row.get(field) is not None for field in comparable_fields)
        or row["comparison_cohort_id"]
        != f"LEGACY_SELF:{row['evidence_sha256']}"
    ):
        raise ValueError(
            "WORKFLOW_MODEL_EXAM_CATALOG_LEGACY_SCOPE_INVALID"
        )


def _entry_identity(row, schema_version, api_required, cli_required, local_required):
    """The entry's identity by profile kind (API, CLI or local), with the exact fields that kind has."""
    if row.get("profile_kind") == "API":
        if (
            set(row) != api_required
            or row["provider_family"]
            not in {
                "OPENAI_COMPATIBLE",
                "ANTHROPIC",
                "OPENAI",
                "GEMINI",
            }
            or not isinstance(row["thinking"], bool)
            or not isinstance(row["tier"], str)
            or not SAFE_THINKING.fullmatch(row["tier"])
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRY_INVALID")
        identity = (
            row["node_id"],
            "API",
            row["provider_family"],
            row["model_name"],
            row["thinking"],
            row["tier"],
        )
    elif row.get("profile_kind") == "CLI":
        if (
            schema_version
            not in {
                "WorkflowModelExamScoreCatalog-v2",
                "WorkflowModelExamScoreCatalog-v3",
                "WorkflowModelExamScoreCatalog-v4",
                "WorkflowModelExamScoreCatalog-v5",
                "WorkflowModelExamScoreCatalog-v6",
                "WorkflowModelExamScoreCatalog-v7",
            }
            or set(row) != cli_required
            or not isinstance(row["adapter_id"], str)
            or not SAFE_THINKING.fullmatch(row["adapter_id"])
            or not isinstance(row["thinking_mode"], str)
            or not SAFE_THINKING.fullmatch(row["thinking_mode"])
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRY_INVALID")
        identity = (
            row["node_id"],
            "CLI",
            row["adapter_id"],
            row["model_name"],
            row["thinking_mode"],
        )
    elif row.get("profile_kind") == "LOCAL":
        if (
            schema_version
            not in {
                "WorkflowModelExamScoreCatalog-v3",
                "WorkflowModelExamScoreCatalog-v4",
                "WorkflowModelExamScoreCatalog-v5",
                "WorkflowModelExamScoreCatalog-v6",
                "WorkflowModelExamScoreCatalog-v7",
            }
            or set(row) != local_required
            or row["endpoint_kind"] != "ollama"
            or not isinstance(row["model_digest"], str)
            or not SAFE_SHA256.fullmatch(row["model_digest"])
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRY_INVALID")
        identity = (
            row["node_id"],
            "LOCAL",
            row["endpoint_kind"],
            row["model_name"],
            row["model_digest"],
        )
    else:
        raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRY_INVALID")
    return identity


def validate_workflow_exam_catalog(
    value: Mapping[str, Any] | None,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    """The entries of a stored workflow exam score catalog, and the historical entries kept with it.

    The current catalog goes to validate_quality_new_catalog, revisions 8 and 9 to
    _validate_workflow_exam_catalog_v8; revisions 1 to 7 are read here, entry by entry. A malformed
    catalog raises ValueError. WorkflowExams loads its catalog through this function."""
    if value is None:
        return (), ()
    schema_version = value.get("schema_version")
    if schema_version == Quality_NEW_CATALOG_SCHEMA:
        return validate_quality_new_catalog(value)
    if schema_version == "WorkflowModelExamScoreCatalog-v8":
        return _validate_workflow_exam_catalog_v8(value)
    if schema_version == "WorkflowModelExamScoreCatalog-v9":
        return _validate_workflow_exam_catalog_v8(
            value,
            allow_frozen_successor_cohort=True,
        )
    if schema_version not in {
        "WorkflowModelExamScoreCatalog-v1",
        "WorkflowModelExamScoreCatalog-v2",
        "WorkflowModelExamScoreCatalog-v3",
        "WorkflowModelExamScoreCatalog-v4",
        "WorkflowModelExamScoreCatalog-v5",
        "WorkflowModelExamScoreCatalog-v6",
        "WorkflowModelExamScoreCatalog-v7",
    }:
        raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_SCHEMA_INVALID")
    historical_entries = _historical_entries(schema_version, value)
    entries = value.get("entries")
    if not isinstance(entries, list) or len(entries) > 64:
        raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_ENTRIES_INVALID")
    accepted: list[dict[str, Any]] = []
    identities: set[tuple[Any, ...]] = set()
    api_required = {
        "node_id",
        "profile_kind",
        "provider_family",
        "model_name",
        "thinking",
        "tier",
        "verdict",
        "score",
        "evidence_sha256",
    }
    cli_required = {
        "node_id",
        "profile_kind",
        "adapter_id",
        "model_name",
        "thinking_mode",
        "verdict",
        "score",
        "evidence_sha256",
    }
    local_required = {
        "node_id",
        "profile_kind",
        "endpoint_kind",
        "model_name",
        "model_digest",
        "verdict",
        "score",
        "evidence_sha256",
    }
    if schema_version in {
        "WorkflowModelExamScoreCatalog-v4",
        "WorkflowModelExamScoreCatalog-v5",
        "WorkflowModelExamScoreCatalog-v6",
        "WorkflowModelExamScoreCatalog-v7",
    }:
        metadata_required = set(WORKFLOW_EXAM_SCORE_METADATA_FIELDS_V4)
        if schema_version in {
            "WorkflowModelExamScoreCatalog-v5",
            "WorkflowModelExamScoreCatalog-v6",
            "WorkflowModelExamScoreCatalog-v7",
        }:
            metadata_required |= set(
                WORKFLOW_EXAM_OPERATIONAL_METADATA_FIELDS_V5
            )
        if schema_version in {
            "WorkflowModelExamScoreCatalog-v6",
            "WorkflowModelExamScoreCatalog-v7",
        }:
            metadata_required |= set(
                WORKFLOW_EXAM_REPAIR_METADATA_FIELDS_V6
            )
        if schema_version == "WorkflowModelExamScoreCatalog-v7":
            metadata_required |= set(
                WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V7
            )
        api_required |= metadata_required
        cli_required |= metadata_required
        local_required |= metadata_required
    for raw in entries:
        row = _entry_row(raw)
        if schema_version in {
            "WorkflowModelExamScoreCatalog-v4",
            "WorkflowModelExamScoreCatalog-v5",
            "WorkflowModelExamScoreCatalog-v6",
            "WorkflowModelExamScoreCatalog-v7",
        }:
            _check_score_fields(row)
            if schema_version in {
                "WorkflowModelExamScoreCatalog-v5",
                "WorkflowModelExamScoreCatalog-v6",
                "WorkflowModelExamScoreCatalog-v7",
            }:
                operational_verdict = _check_operational_fields(row)
            if schema_version in {
                "WorkflowModelExamScoreCatalog-v6",
                "WorkflowModelExamScoreCatalog-v7",
            }:
                _check_repair_fields(row, operational_verdict)
            successor_series = False
            if schema_version == "WorkflowModelExamScoreCatalog-v7":
                (series_slots, provenance, core_closure_score, semantic_repair_rounds,
                 failed_exam_item_count) = _check_cache_fields(
                    row
                )
                successor_series = _check_series(
                    row, provenance, series_slots, core_closure_score, semantic_repair_rounds,
                    failed_exam_item_count
                )
                _check_cache_binding(row)
            _check_comparison(row, successor_series)
        identity = _entry_identity(row, schema_version, api_required, cli_required, local_required)
        if identity in identities:
            raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_DUPLICATE")
        identities.add(identity)
        accepted.append(row)
    return tuple(accepted), historical_entries
