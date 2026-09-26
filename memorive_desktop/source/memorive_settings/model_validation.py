from __future__ import annotations
from memorive_settings.task_scheduling import scheduled
from .call_ledger import CallLedger, RecordingTransport, recorded_execution
from model_gateway.prompt_cache import cache_usage
from .cache_workspace import structured_chat_workspace, write_response_schema
from .cache_session import Continuation
import uuid

import base64
from dataclasses import dataclass
from http.client import IncompleteRead
import hashlib
from inspect import signature
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Any, Callable, Mapping, Protocol, Sequence
import unicodedata
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .contracts import API_PROTOCOLS, normalize_external_api_base_url
from .exam_successor import (
    WORKFLOW_EXAM_REPLY_TOKEN_CEILING,
    WORKFLOW_EXAM_TRANSPORT_CAPTURE_BYTES,
    workflow_exam_output_limit_contract,
)
from .model_capabilities import infer_api_model_capability
from .exam_score_history import ExamScoreHistory
from model_gateway.resource_limits import resolve_call_limits, _registry_entry
from .quality_new import (
    Quality_NEW_CATALOG_SCHEMA,
    validate_quality_new_catalog,
)

try:
    from model_gateway.execution_core.adapters.codex_cli import (
        CodexAdapterViolation as _ExecutionCodexAdapterViolation,
        CodexCliAdapter as _ExecutionCodexCliAdapter,
    )
except ImportError:  # optional in source-only service tests; packaged EXE includes it
    _ExecutionCodexAdapterViolation = None  # type: ignore[assignment]
    _ExecutionCodexCliAdapter = None  # type: ignore[assignment]


MAX_CATALOG_PAGES = 10
MAX_CATALOG_CURSOR_CHARS = 2048
MAX_CATALOG_CHOICES = 50
# Metadata limits are separate from generated model output. None delegates
# generated response capacity to the provider/runtime (capacity policy).
MAX_HTTP_BODY_BYTES = 1024 * 1024
MAX_EMBEDDING_HTTP_BODY_BYTES = None
MAX_WORKFLOW_EXAM_HTTP_BODY_BYTES = None
MAX_PROCESS_CAPTURE_BYTES = None  # compatibility name; no implicit output quota
SAFE_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
SAFE_THINKING = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SAFE_SHA256 = re.compile(r"^[A-F0-9]{64}$")
SAFE_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_:.-]{0,159}$")
WORKFLOW_EXAM_SCORE_METADATA_FIELDS_V4 = (
    "score_exact",
    "score_semantics",
    "qualification_verdict",
    "exam_category_id",
    "role_id",
    "reference_pack_id",
    "reference_pack_revision",
    "reference_pack_sha256",
    "scoring_protocol_revision",
    "scoring_protocol_sha256",
    "scoring_projection_revision",
    "comparison_binding_sha256",
    "comparison_cohort_id",
    "horizontal_comparison_eligible",
    "horizontal_comparison_allowed_only_with_same_cohort",
    "cross_category_comparison_forbidden",
    "global_ranking_forbidden",
)
WORKFLOW_EXAM_OPERATIONAL_METADATA_FIELDS_V5 = (
    "operational_eligibility_verdict",
    "operational_policy_revision",
    "operational_safety_gate",
    "safety_blocking_residual_count",
    "calibration_residual_count",
)
WORKFLOW_EXAM_REPAIR_METADATA_FIELDS_V6 = (
    "exact_case_repair_rate",
    "case_score_deficit_recovery_rate",
    "repair_metadata_collision_suspect_count",
    "collision_adjusted_repair_diagnostic_rate",
)
WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V7 = (
    "cache_reuse_status",
    "cache_provenance_kind",
    "cache_binding_sha256",
    "executor_ref",
    "sample_revision",
    "repair_prompt_projection_revision",
    "pointer_normalization_revision",
    "subject_transport_projection_revision",
    "core_closure_verdict",
    "core_closure_score",
    "semantic_repair_rounds",
    "failed_exam_item_count",
    "series_aggregate_method",
    "series_slots",
)
WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V8 = (
    "execution_outcome",
    "scorability",
    "quality_verdict",
    "terminal_disposition",
    "executor_binding_sha256",
    "executor_binding_kind",
    "sample_manifest_sha256",
    "cache_binding",
    "category_score_is_descriptive",
    "physical_model_calls",
    "token_usage",
    "provider_received_gold",
    "local_reference_pack_embedded",
)
WORKFLOW_EXAM_Quality_NEW_METADATA_FIELDS = (
    "quality_new",
    "language_distribution_status",
    "production_workload_score_established",
    "cache_reuse_requires_executor_ref_match",
    "cache_reuse_scope",
    "model_fail_verdict",
    "model_fail_established",
    "quality_threshold_defined",
    "primary_score_track",
    "calibration_series",
    "calibration_series_aggregate_method",
    "role_adequacy_policy_revision",
)
WORKFLOW_EXAM_SCORE_METADATA_FIELDS = (
    WORKFLOW_EXAM_SCORE_METADATA_FIELDS_V4
    + WORKFLOW_EXAM_OPERATIONAL_METADATA_FIELDS_V5
    + WORKFLOW_EXAM_REPAIR_METADATA_FIELDS_V6
    + WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V7
    + WORKFLOW_EXAM_CACHE_METADATA_FIELDS_V8
    + WORKFLOW_EXAM_Quality_NEW_METADATA_FIELDS
)
WORKFLOW_EXAM_ROLE_NODE_BINDINGS = {
    "CARD_DISTILLER": frozenset({"card_distill", "ingest"}),
    "CARD_REVIEWER": frozenset({"transport_review"}),
    "ANALYSIS_PRIMARY": frozenset({"analysis"}),
    "ANALYSIS_REVIEWER": frozenset({"judgment_review"}),
    "OCR_PAGE": frozenset({"ingest"}),
    "EMBEDDING_TEXT": frozenset({"chunk_embedding"}),
    "RERANKER_TEXT": frozenset({"context_pack"}),
}


def _workflow_exam_v8_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest().upper()


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
    expected_catalog_sha256 = _workflow_exam_v8_sha256(
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
            != _workflow_exam_v8_sha256(
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
                != _workflow_exam_v8_sha256(sample_hashes)
                or row["evidence_sha256"]
                != _workflow_exam_v8_sha256(evidence_hashes)
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
        expected_binding_hash = _workflow_exam_v8_sha256(
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
MINIMAL_PROMPT = b"Reply with OK only. Do not use tools."
DEEPSEEK_ESTIMATE_CNY_PER_MILLION = {
    "deepseek-v4-flash": {
        "input_cache_hit": 0.0224,
        "input_cache_miss": 1.12,
        "output": 2.24,
    },
    "deepseek-v4-pro": {
        "input_cache_hit": 0.029,
        "input_cache_miss": 3.48,
        "output": 6.96,
    },
}
_DEEPSEEK_OCR_GROUNDING_LINE_RE = re.compile(
    r"^[ \t]*<\|ref\|>[^\r\n]{1,120}<\|/ref\|>[ \t]*"
    r"<\|det\|>[ \t]*\[\[[0-9.,\- \t\[\]]+\]\]"
    r"[ \t]*<\|/det\|>[ \t]*(?:\r?\n)?",
    re.MULTILINE,
)
DEEPSEEK_OCR_NORMALIZATION_REVISION = (
    "Desktop_DEEPSEEK_OCR_GROUNDING_LINE_NORMALIZATION_V1"
)


def _normalize_deepseek_ocr_markdown(value: object) -> object:
    """Reuse the production SiliconFlow OCR grounding-line normalization."""

    if not isinstance(value, str):
        return value
    return _DEEPSEEK_OCR_GROUNDING_LINE_RE.sub("", value).strip()


class _LocalEmbeddingHttpFailure(RuntimeError):
    def __init__(self, reason: str, diagnostic: Mapping[str, Any]):
        self.reason = reason
        self.diagnostic = dict(diagnostic)
        super().__init__(reason)


def _local_embedding_http_diagnostic(status: int, body: bytes) -> dict[str, Any]:
    text = body.decode("utf-8", errors="replace").casefold()
    physical_batch = (
        "physical batch size" in text
        and ("too large to process" in text or "increase" in text)
    )
    context_exceeded = status == 400 and any(marker in text for marker in (
        'input length exceeds the context length', 'input length exceeds maximum context length',
    ))
    classification = (
        "PHYSICAL_BATCH_TOO_SMALL"
        if physical_batch
        else ('INPUT_CONTEXT_EXCEEDED' if context_exceeded else
              ("UNCLASSIFIED_RETRYABLE" if status == 400 else "HTTP_FAILURE"))
    )
    return {
        "http_status": status,
        "body_bytes": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest().upper(),
        "classification": classification,
        "physical_batch_marker": physical_batch,
        "context_length_marker": context_exceeded,
        "raw_body_recorded": False,
    }


def _split_embedding_text(value: str) -> tuple[str, str]:
    if len(value) < 2:
        raise ValueError("LOCAL_EMBEDDING_TEXT_CANNOT_SPLIT")
    midpoint = len(value) // 2
    radius = max(32, len(value) // 4)
    lower = max(1, midpoint - radius)
    upper = min(len(value) - 1, midpoint + radius)
    boundaries = [
        index + 1
        for index in range(lower, upper)
        if value[index] in "\n。！？.!?;； "
    ]
    split_at = min(boundaries, key=lambda index: abs(index - midpoint)) if boundaries else midpoint
    left, right = value[:split_at].strip(), value[split_at:].strip()
    if not left or not right:
        left, right = value[:midpoint], value[midpoint:]
    if not left or not right:
        raise ValueError("LOCAL_EMBEDDING_TEXT_CANNOT_SPLIT")
    return left, right


def _weighted_normalized_vector(
    vectors: Sequence[Sequence[float]], weights: Sequence[int]
) -> list[float]:
    if not vectors or len(vectors) != len(weights):
        raise ValueError("LOCAL_EMBEDDING_SEGMENT_VECTOR_SET_INVALID")
    dimension = len(vectors[0])
    if dimension <= 0 or any(len(vector) != dimension for vector in vectors):
        raise ValueError("LOCAL_EMBEDDING_SEGMENT_DIMENSION_MISMATCH")
    total_weight = sum(weights)
    if total_weight <= 0:
        raise ValueError("LOCAL_EMBEDDING_SEGMENT_WEIGHT_INVALID")
    combined = [
        sum(float(vector[index]) * weight for vector, weight in zip(vectors, weights, strict=True))
        / total_weight
        for index in range(dimension)
    ]
    norm = math.sqrt(sum(value * value for value in combined))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("LOCAL_EMBEDDING_SEGMENT_VECTOR_NORM_INVALID")
    return [value / norm for value in combined]


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes
    transport_diagnostic: Mapping[str, Any] | None = None


class _ResponseInterrupted(OSError):
    def __init__(self, partial: bytes, diagnostic: Mapping[str, Any]):
        super().__init__('API_RESPONSE_INCOMPLETE_REMOTE_STATE_UNKNOWN')
        self.partial = partial
        self.diagnostic = dict(diagnostic)


def _read_chat_stream(response, request_body: bytes | None) -> HttpResponse:
    """Reuse MODEL_GATEWAY SSE parsers. Heartbeats show connectivity, not model progress."""
    from model_gateway.providers.deepseek import _read_sse_response as read_openai
    from model_gateway.providers.anthropic import _read_sse_response as read_anthropic

    diagnostic = {'stream': True, 'received_bytes': 0, 'heartbeat_count': 0,
                  'progress_event_count': 0, 'last_byte_monotonic': None,
                  'last_progress_monotonic': None, 'complete': False,
                  'silence_is_disconnect': False, 'remote_cancellation_confirmed': False}
    request = json.loads(request_body or b'{}')
    # Anthropic uses messages without response_format; Gemini remains non-SSE.
    anthropic = 'response_format' not in request
    text_parts = []

    def observed_lines():
        for raw in response:
            diagnostic['received_bytes'] += len(raw)
            diagnostic['last_byte_monotonic'] = time.monotonic()
            line = raw.decode('utf-8').strip()
            if line.startswith(':'):
                diagnostic['heartbeat_count'] += 1
            elif line.startswith('data:') and line[5:].strip() not in ('', '[DONE]'):
                event = json.loads(line[5:].strip())
                if isinstance(event, Mapping):
                    if event.get('type') == 'ping':
                        diagnostic['heartbeat_count'] += 1
                    delta = event.get('delta') or {}
                    choices = event.get('choices') or []
                    if choices:
                        delta = choices[0].get('delta') or {}
                    if delta.get('content') or delta.get('text') or delta.get('reasoning_content') or delta.get('thinking'):
                        diagnostic['progress_event_count'] += 1
                        diagnostic['last_progress_monotonic'] = time.monotonic()
                    text = delta.get('content') or delta.get('text')
                    if isinstance(text, str):
                        text_parts.append(text)
            yield raw
    try:
        value, parsed = (read_anthropic if anthropic else read_openai)(observed_lines())
    except (IncompleteRead, OSError, ValueError, RuntimeError) as error:
        diagnostic['exception_type'] = type(error).__name__
        # Keep only visible answer text, not provider reasoning or credentials.
        partial = ''.join(text_parts).encode('utf-8')
        raise _ResponseInterrupted(partial, diagnostic) from error
    return HttpResponse(int(response.status), json.dumps(value, ensure_ascii=False).encode('utf-8'),
                        {**diagnostic, **parsed, 'complete': True})


@dataclass(frozen=True)
class ProcessResult:
    started: bool
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    output_truncated: bool = False
    duration_ms: int = 0
    stdout_total_bytes: int = 0
    stderr_total_bytes: int = 0
    capture_quota_bytes: int | None = None


class HttpTransport(Protocol):
    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
        proxy_mode: str,
        proxy_address: str,
        max_response_bytes: int | None = None,
    ) -> HttpResponse: ...


class ProcessTransport(Protocol):
    def run(
        self,
        *,
        argv: Sequence[str],
        cwd: str,
        environment: Mapping[str, str],
        stdin_bytes: bytes | None,
        timeout_seconds: int,
        shell: bool,
    ) -> ProcessResult: ...


class ModelValidationRunner(Protocol):
    """Explicit validation boundary; saving settings never invokes it."""

    def verify_api_model(self, service: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def verify_cli_model(
        self,
        cli_service: Mapping[str, Any],
        model: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def execute_structured_chat(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        prompt: str,
        response_schema: Mapping[str, Any],
        purpose: str,
        max_output_tokens: int | None = None,
        timeout_seconds: int | None = None,
    ) -> Mapping[str, Any]: ...

    def execute_embeddings(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        inputs: Sequence[str],
        purpose: str,
        timeout_seconds: int = 120,
    ) -> Mapping[str, Any]: ...

    def execute_ocr_image(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
        purpose: str,
        max_output_tokens: int | None = None,
        timeout_seconds: int = 120,
    ) -> Mapping[str, Any]: ...

    def execute_rerank(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        query: str,
        documents: Sequence[str],
        purpose: str,
        timeout_seconds: int = 120,
    ) -> Mapping[str, Any]: ...

    def plan_workflow_node_exam(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...

    def test_workflow_node(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
        authorization: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...


class _EmbeddingExecutionMixin:
    """Live API/local embedding transport shared only by the desktop runner."""

    @staticmethod
    def _not_run(kind: str) -> dict[str, Any]:
        return {
            "schema_version": "SettingsModelValidationResult-v1",
            "kind": kind,
            "status": "NOT_RUN",
            "score": None,
            "reason": "MODEL_VALIDATION_RUNNER_NOT_CONFIGURED",
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
            "external_process_launches": 0,
        }

    @recorded_execution
    def execute_embeddings(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        inputs: Sequence[str],
        purpose: str,
        timeout_seconds: int = 120,
    ) -> Mapping[str, Any]:
        if profile_kind not in {"API", "LOCAL"}:
            raise ValueError("EMBEDDING_PROFILE_KIND_INVALID")
        requested_model = model.get("model_name")
        accepted_inputs = list(inputs)
        if (
            not isinstance(requested_model, str)
            or not SAFE_MODEL.fullmatch(requested_model)
            or not accepted_inputs
            or len(accepted_inputs) > 256
            or any(
                not isinstance(value, str)
                or not value
                or len(value.encode("utf-8")) > 65536
                for value in accepted_inputs
            )
            or purpose not in {"workflow_model_exam", "core_document_processing", "research_retrieval"}
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 5 <= timeout_seconds <= 600
        ):
            raise ValueError("EMBEDDING_REQUEST_INVALID")
        behavior_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "model": requested_model,
                    "inputs": accepted_inputs,
                    "purpose": purpose,
                },
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest().upper()
        started = time.monotonic()
        vectors: list[list[float]] = []
        physical_calls = 0
        metadata_calls = 0
        usage_totals = {"prompt_tokens": 0, "completion_tokens": 0}
        response_hashes: list[str] = []
        adaptive_split_events: list[dict[str, Any]] = []
        batch_size = 256 if requested_model in {"BAAI/bge-m3", "Pro/BAAI/bge-m3"} else 32
        returned_model: str | None = requested_model if profile_kind == "LOCAL" else None
        secret = ""
        secret_bytes = b""
        headers: dict[str, str] = {}
        try:
            if profile_kind == "API":
                profile = _api_profile(service)
                if profile is None or profile.provider_family != "OPENAI_COMPATIBLE":
                    raise ValueError("EMBEDDING_API_PROFILE_UNSUPPORTED")
                parsed = urlsplit(profile.url)
                path = parsed.path.rstrip("/")
                if not path.casefold().endswith("/models"):
                    raise ValueError("EMBEDDING_API_ENDPOINT_INVALID")
                endpoint = urlunsplit(
                    (
                        parsed.scheme,
                        parsed.netloc,
                        path[: -len("/models")] + "/embeddings",
                        "",
                        "",
                    )
                )
                credential_ref = service.get("credential_ref")
                if not isinstance(credential_ref, str) or not credential_ref:
                    raise ValueError("API_CREDENTIAL_UNAVAILABLE")
                secret_bytes = self._credential_resolver(credential_ref)
                if not secret_bytes or len(secret_bytes) > 512:
                    raise ValueError("API_CREDENTIAL_UNAVAILABLE")
                secret = secret_bytes.decode("utf-8", errors="strict")
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "Memorive-Embedding-Exam/1",
                    **dict(profile.extra_headers),
                }
                headers[profile.auth_header] = (
                    f"Bearer {secret}"
                    if profile.auth_header == "Authorization"
                    else secret
                )
                proxy_mode, proxy_address, _ = self._preferences()
            else:
                if (
                    service.get("endpoint_kind") != "ollama"
                    or service.get("execution_eligible") is not True
                    or service.get("exact_identity_available") is not True
                    or not isinstance(service.get("chat_endpoint"), str)
                    or not isinstance(service.get("model_digest"), str)
                    or not SAFE_SHA256.fullmatch(
                        str(service["model_digest"]).upper()
                    )
                ):
                    raise ValueError("LOCAL_EMBEDDING_PROFILE_INVALID")
                parsed = urlsplit(str(service["chat_endpoint"]))
                if (
                    parsed.scheme != "http"
                    or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or parsed.path != "/api/chat"
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError("LOCAL_EMBEDDING_ENDPOINT_INVALID")
                endpoint = urlunsplit(
                    (parsed.scheme, parsed.netloc, "/api/embed", "", "")
                )
                tags_endpoint = urlunsplit(
                    (parsed.scheme, parsed.netloc, "/api/tags", "", "")
                )
                opener = build_opener(ProxyHandler({}), _NoRedirect())
                metadata_calls += 1
                from .call_ledger import recording_urlopen
                opener.open = recording_urlopen(opener.open)
                with opener.open(
                    Request(tags_endpoint, method="GET"), timeout=min(10, timeout_seconds)
                ) as response:
                    tags_raw = response.read(2 * 1024 * 1024 + 1)
                if len(tags_raw) > 2 * 1024 * 1024:
                    raise ValueError("LOCAL_EMBEDDING_TAGS_RESPONSE_TOO_LARGE")
                tags = json.loads(tags_raw.decode("utf-8"))
                rows = tags.get("models") if isinstance(tags, Mapping) else None
                matched = next(
                    (
                        row
                        for row in (rows if isinstance(rows, list) else [])
                        if isinstance(row, Mapping)
                        and (row.get("model") or row.get("name"))
                        == requested_model
                    ),
                    None,
                )
                if (
                    not isinstance(matched, Mapping)
                    or str(matched.get("digest") or "").upper()
                    != str(service["model_digest"]).upper()
                ):
                    raise ValueError("LOCAL_EMBEDDING_MODEL_IDENTITY_MISMATCH")

            if profile_kind == "LOCAL":
                def local_vector(value: str, *, depth: int = 0) -> list[float]:
                    nonlocal physical_calls
                    payload = {
                        "model": requested_model,
                        "input": [value],
                        "truncate": False,
                    }
                    body = json.dumps(
                        payload,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    request = Request(
                        endpoint,
                        data=body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    physical_calls += 1
                    try:
                        with opener.open(request, timeout=timeout_seconds) as response:
                            status_code = int(response.status)
                            raw = response.read(8 * 1024 * 1024 + 1)
                    except HTTPError as error:
                        error_body = error.read(64 * 1024 + 1)[: 64 * 1024]
                        diagnostic = _local_embedding_http_diagnostic(
                            int(error.code), error_body
                        )
                        if (
                            diagnostic["classification"] in {"PHYSICAL_BATCH_TOO_SMALL", "INPUT_CONTEXT_EXCEEDED"}
                            and len(value) > 1
                        ):
                            left, right = _split_embedding_text(value)
                            child_vectors = [
                                local_vector(left, depth=depth + 1),
                                local_vector(right, depth=depth + 1),
                            ]
                            adaptive_split_events.append(
                                {
                                    "depth": depth,
                                    "original_chars": len(value),
                                    "child_chars": [len(left), len(right)],
                                    "original_sha256": hashlib.sha256(
                                        value.encode("utf-8")
                                    ).hexdigest().upper(),
                                    "child_sha256s": [
                                        hashlib.sha256(part.encode("utf-8"))
                                        .hexdigest()
                                        .upper()
                                        for part in (left, right)
                                    ],
                                    "trigger": diagnostic,
                                    "source_chunk_identity_preserved": True,
                                }
                            )
                            return _weighted_normalized_vector(
                                child_vectors, [len(left), len(right)]
                            )
                        reason = (
                            "LOCAL_EMBEDDING_HTTP_400_UNCLASSIFIED_RETRYABLE"
                            if diagnostic["classification"]
                            == "UNCLASSIFIED_RETRYABLE"
                            else f"LOCAL_EMBEDDING_HTTP_{error.code}"
                        )
                        raise _LocalEmbeddingHttpFailure(reason, diagnostic) from error
                    if len(raw) > 8 * 1024 * 1024:
                        raise ValueError("LOCAL_EMBEDDING_RESPONSE_TOO_LARGE")
                    if status_code != 200:
                        raise _LocalEmbeddingHttpFailure(
                            f"LOCAL_EMBEDDING_HTTP_{status_code}",
                            _local_embedding_http_diagnostic(status_code, raw),
                        )
                    response_hashes.append(hashlib.sha256(raw).hexdigest().upper())
                    document = json.loads(raw.decode("utf-8"))
                    batch_vectors = (
                        document.get("embeddings")
                        if isinstance(document, Mapping)
                        else None
                    )
                    if (
                        not isinstance(batch_vectors, list)
                        or len(batch_vectors) != 1
                        or not isinstance(batch_vectors[0], list)
                    ):
                        raise ValueError("EMBEDDING_VECTOR_COUNT_INVALID")
                    raw_usage = (
                        document.get("usage")
                        if isinstance(document.get("usage"), Mapping)
                        else document
                    )
                    normalised = _normalise_token_usage(raw_usage)
                    if normalised:
                        usage_totals["prompt_tokens"] += normalised["prompt_tokens"]
                        usage_totals["completion_tokens"] += normalised[
                            "completion_tokens"
                        ]
                    return batch_vectors[0]

                for value in accepted_inputs:
                    vectors.append(local_vector(value))
            else:
                for start in range(0, len(accepted_inputs), batch_size):
                    batch = accepted_inputs[start : start + batch_size]
                    body = json.dumps(
                        {
                            "model": requested_model,
                            "input": batch,
                            "encoding_format": "float",
                        },
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    physical_calls += 1
                    response = self._http.request(
                        method="POST",
                        url=endpoint,
                        headers=headers,
                        body=body,
                        timeout_seconds=timeout_seconds,
                        proxy_mode=proxy_mode,
                        proxy_address=proxy_address,
                    )
                    status_code = response.status_code
                    raw = response.body
                    if status_code != 200:
                        raise ValueError(f"EMBEDDING_HTTP_{status_code}")
                    response_hashes.append(hashlib.sha256(raw).hexdigest().upper())
                    document = json.loads(raw.decode("utf-8"))
                    if not isinstance(document, Mapping):
                        raise ValueError("EMBEDDING_RESPONSE_INVALID")
                    observed_model = document.get("model")
                    if observed_model != requested_model:
                        raise ValueError("EMBEDDING_RETURNED_MODEL_MISMATCH")
                    returned_model = str(observed_model)
                    data = document.get("data")
                    if not isinstance(data, list):
                        raise ValueError("EMBEDDING_RESPONSE_INVALID")
                    ordered = sorted(
                        data,
                        key=lambda row: (
                            row.get("index")
                            if isinstance(row, Mapping)
                            and isinstance(row.get("index"), int)
                            else -1
                        ),
                    )
                    batch_vectors = [
                        row.get("embedding")
                        for row in ordered
                        if isinstance(row, Mapping)
                    ]
                    if (
                        len(batch_vectors) != len(batch)
                        or any(not isinstance(row, list) for row in batch_vectors)
                    ):
                        raise ValueError("EMBEDDING_VECTOR_COUNT_INVALID")
                    vectors.extend(batch_vectors)
                    raw_usage = (
                        document.get("usage")
                        if isinstance(document.get("usage"), Mapping)
                        else document
                    )
                    normalised = _normalise_token_usage(raw_usage)
                    if normalised:
                        usage_totals["prompt_tokens"] += normalised["prompt_tokens"]
                        usage_totals["completion_tokens"] += normalised[
                            "completion_tokens"
                        ]
            if len(vectors) != len(accepted_inputs):
                raise ValueError("EMBEDDING_VECTOR_COUNT_INVALID")
            input_price_cny_per_million = {"BAAI/bge-m3": 0.07, "Pro/BAAI/bge-m3": 0.07, "Qwen/Qwen3-VL-Embedding-8B": 0.70}.get(requested_model)
            estimated_cost_cny = (
                round(
                    (
                        usage_totals["prompt_tokens"]
                        if usage_totals["prompt_tokens"]
                        else sum(len(value.encode("utf-8")) for value in accepted_inputs)
                    )
                    * input_price_cny_per_million
                    / 1_000_000,
                    12,
                )
                if profile_kind == "API"
                and input_price_cny_per_million is not None
                else (0.0 if profile_kind == "LOCAL" else None)
            )
            duration_ms = max(0, round((time.monotonic() - started) * 1000))
            return {
                "schema_version": "SettingsEmbeddingRunnerResult-v1",
                "status": "PASS",
                "reason": "EMBEDDING_COMPLETED",
                "requested_model": requested_model,
                "returned_model": returned_model,
                "vectors": vectors,
                "duration_ms": duration_ms,
                "external_network_calls": physical_calls if profile_kind == "API" else 0,
                "provider_calls": physical_calls if profile_kind == "API" else 0,
                "external_model_calls": physical_calls,
                "external_process_launches": 0,
                "local_metadata_calls": metadata_calls,
                "execution_receipt": {
                    "schema_version": "SettingsEmbeddingExecutionReceipt-v1",
                    "status": "PASS",
                    "profile_kind": profile_kind,
                    "purpose": purpose,
                    "route": (
                        "EXISTING_SETTINGS_API_TRANSPORT"
                        if profile_kind == "API"
                        else "OLLAMA_LOOPBACK"
                    ),
                    "region": (
                        "PROVIDER_MANAGED_UNDISCLOSED"
                        if profile_kind == "API"
                        else "LOCAL_MACHINE"
                    ),
                    "egress": (
                        "PROVIDER_API"
                        if profile_kind == "API"
                        else "LOOPBACK_ONLY"
                    ),
                    "requested_model": requested_model,
                    "returned_model": returned_model,
                    "model_digest": service.get("model_digest"),
                    "model_identity_evidence": (
                        "PROVIDER_RESPONSE_MODEL_FIELD"
                        if profile_kind == "API"
                        else "OLLAMA_EXACT_NAME_AND_DIGEST_PRECALL_BINDING"
                    ),
                    "behavior_sha256": behavior_sha256,
                    "response_sha256": hashlib.sha256(
                        "".join(response_hashes).encode("ascii")
                    ).hexdigest().upper(),
                    "token_usage": usage_totals,
                    "actual_cost": None,
                    "estimated_cost_cny": estimated_cost_cny,
                    "cost_evidence": (
                        "MODEL_SPECIFIC_EMBEDDING_INPUT_PRICE_ESTIMATE"
                        if profile_kind == "API"
                        else "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
                    ),
                    "adaptive_embedding_split": {
                        "activated": bool(adaptive_split_events),
                        "split_event_count": len(adaptive_split_events),
                        "events": adaptive_split_events,
                        "aggregation": (
                            "CHARACTER_WEIGHTED_MEAN_THEN_L2_NORMALIZE"
                            if adaptive_split_events
                            else None
                        ),
                    },
                    "request_input_limit": 256,
                    "physical_batch_size": batch_size if profile_kind == "API" else 1,
                    "physical_batch_count": physical_calls,
                    "max_response_bytes": None,
                },
            }
        except Exception as error:
            reason = (
                error.reason
                if isinstance(error, _LocalEmbeddingHttpFailure)
                else (str(error) or type(error).__name__)
            )
            if len(reason) > 160:
                reason = type(error).__name__
            failed = {
                "schema_version": "SettingsEmbeddingRunnerResult-v1",
                "status": "FAILED",
                "reason": reason,
                "requested_model": requested_model,
                "returned_model": returned_model,
                "duration_ms": max(0, round((time.monotonic() - started) * 1000)),
                "external_network_calls": physical_calls if profile_kind == "API" else 0,
                "provider_calls": physical_calls if profile_kind == "API" else 0,
                "external_model_calls": physical_calls,
                "external_process_launches": 0,
                "local_metadata_calls": metadata_calls,
            }
            if isinstance(error, _LocalEmbeddingHttpFailure):
                failed["execution_receipt"] = {
                    "schema_version": "SettingsEmbeddingExecutionReceipt-v1",
                    "status": "FAILED",
                    "profile_kind": profile_kind,
                    "purpose": purpose,
                    "route": "OLLAMA_LOOPBACK",
                    "region": "LOCAL_MACHINE",
                    "egress": "LOOPBACK_ONLY",
                    "requested_model": requested_model,
                    "returned_model": returned_model,
                    "model_digest": service.get("model_digest"),
                    "behavior_sha256": behavior_sha256,
                    "actual_cost": None,
                    "estimated_cost_cny": 0.0,
                    "cost_evidence": "LOCAL_COMPUTE_NO_PROVIDER_API_COST",
                    "transport_diagnostic": error.diagnostic,
                }
            return failed
        finally:
            secret = ""
            secret_bytes = b""
            headers.clear()


class _SpecializedModelExecutionMixin:
    """Capability-specific OCR and reranker transports for model exams.

    These methods deliberately do not route either capability through the
    structured-chat or embedding adapters.  A completed request therefore
    proves the capability that the workflow node actually consumes.
    """

    @staticmethod
    def _specialized_failure(
        *,
        schema_version: str,
        reason: str,
        requested_model: str | None,
        returned_model: str | None,
        profile_kind: str,
        started: float,
        model_calls: int,
        metadata_calls: int = 0,
        process_launches: int = 0,
    ) -> dict[str, Any]:
        accepted_reason = reason if reason and len(reason) <= 160 else "SPECIALIZED_MODEL_CALL_FAILED"
        external_budget_calls = model_calls if profile_kind in {"API", "CLI"} else 0
        local_model_calls = model_calls if profile_kind == "LOCAL" else 0
        return {
            "schema_version": schema_version,
            "status": "FAILED",
            "reason": accepted_reason,
            "requested_model": requested_model,
            "returned_model": returned_model,
            "duration_ms": max(0, round((time.monotonic() - started) * 1000)),
            "external_network_calls": model_calls if profile_kind == "API" else 0,
            "provider_calls": model_calls if profile_kind == "API" else 0,
            # ``external_model_calls`` is retained as the legacy physical-call
            # counter consumed by older Quality executors.  Successor budget
            # enforcement must use ``external_budget_calls`` so an unlimited
            # loopback/local run cannot consume an external-call allowance.
            "external_model_calls": model_calls,
            "physical_model_calls": model_calls,
            "external_budget_calls": external_budget_calls,
            "local_model_calls": local_model_calls,
            "external_process_launches": process_launches,
            "local_metadata_calls": metadata_calls,
        }

    @recorded_execution
    def execute_ocr_image(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
        purpose: str,
        max_output_tokens: int | None = None,
        timeout_seconds: int = 120,
    ) -> Mapping[str, Any]:
        started = time.monotonic()
        requested_model = model.get("model_name")
        output_allocation_ceiling = (
            WORKFLOW_EXAM_REPLY_TOKEN_CEILING
        )
        if (
            profile_kind not in {"API", "CLI", "LOCAL"}
            or not isinstance(requested_model, str)
            or not SAFE_MODEL.fullmatch(requested_model)
            or not isinstance(image_bytes, bytes)
            or not image_bytes
            or len(image_bytes) > 8 * 1024 * 1024
            or mime_type not in {"image/png", "image/jpeg", "image/webp"}
            or not isinstance(prompt, str)
            or not prompt.strip()
            or len(prompt.encode("utf-8")) > 65_536
            or purpose not in {"workflow_model_exam", "core_document_processing", "research_attachment"}
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 5 <= timeout_seconds <= 600
            or (
                max_output_tokens is not None
                and (
                    isinstance(max_output_tokens, bool)
                    or not isinstance(max_output_tokens, int)
                    or not 1 <= max_output_tokens <= output_allocation_ceiling
                )
            )
        ):
            raise ValueError("OCR_IMAGE_REQUEST_INVALID")
        behavior_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "model": requested_model,
                    "image_sha256": hashlib.sha256(image_bytes).hexdigest().upper(),
                    "mime_type": mime_type,
                    "prompt": prompt,
                    "purpose": purpose,
                    "max_output_tokens": max_output_tokens,
                },
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest().upper()
        transport_diagnostic = {}
        secret = ""
        secret_bytes = b""
        headers: dict[str, str] = {}
        model_calls = 0
        metadata_calls = 0
        process_launches = 0
        returned_model: str | None = requested_model if profile_kind == "LOCAL" else None
        provider_identity_grade: str | None = None
        effective_identity_grade: str | None = None
        request_model_binding_status: str | None = None
        request_model_binding_sha256: str | None = None
        model_allowlist_sha256: str | None = None
        identity_authority_ref: str | None = None
        try:
            image_base64 = base64.b64encode(image_bytes).decode("ascii")
            if profile_kind == "API":
                profile = _api_profile(service)
                if profile is None or profile.provider_family != "OPENAI_COMPATIBLE":
                    raise ValueError("OCR_API_PROFILE_UNSUPPORTED")
                endpoint = _api_chat_url(profile, requested_model)
                credential_ref = service.get("credential_ref")
                if not isinstance(credential_ref, str) or not credential_ref:
                    raise ValueError("API_CREDENTIAL_UNAVAILABLE")
                secret_bytes = self._credential_resolver(credential_ref)
                if not secret_bytes or len(secret_bytes) > 512:
                    raise ValueError("API_CREDENTIAL_UNAVAILABLE")
                secret = secret_bytes.decode("utf-8", errors="strict")
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "Memorive-OCR-Exam/1",
                    **dict(profile.extra_headers),
                }
                headers[profile.auth_header] = (
                    f"Bearer {secret}" if profile.auth_header == "Authorization" else secret
                )
                detail = model.get("image_detail", "high")
                if detail not in {"auto", "low", "high"}:
                    raise ValueError("OCR_IMAGE_DETAIL_INVALID")
                payload: dict[str, Any] = {
                    "model": requested_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{mime_type};base64,{image_base64}",
                                        "detail": detail,
                                    },
                                },
                                {"type": "text", "text": prompt},
                            ],
                        }
                    ],
                    "stream": False,
                }
                from .api_request_options import reasoning_profile, resolve_reasoning_options, apply_reasoning_options
                control = reasoning_profile(requested_model, profile.url, profile.provider_family)
                options = resolve_reasoning_options(control, model)
                if options is not None:
                    apply_reasoning_options(payload, options)
                if max_output_tokens is not None:
                    field = control['output_field'] if control else model.get('max_output_tokens_wire_field', 'max_tokens')
                    if field not in {'max_tokens','max_completion_tokens'}:
                        raise ValueError('API_MAX_OUTPUT_TOKENS_FIELD_INVALID')
                    payload[field] = max_output_tokens
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                proxy_mode, proxy_address, _ = self._preferences()
                model_calls = 1
                http_response = self._http.request(
                    method="POST",
                    url=endpoint,
                    headers=headers,
                    body=body,
                    timeout_seconds=timeout_seconds,
                    proxy_mode=proxy_mode,
                    proxy_address=proxy_address,
                    max_response_bytes=(
                        MAX_WORKFLOW_EXAM_HTTP_BODY_BYTES
                        if purpose == "workflow_model_exam"
                        else MAX_HTTP_BODY_BYTES
                    ),
                )
                status_code = http_response.status_code
                raw = http_response.body
                if status_code != 200:
                    from .api_request_options import safe_error_details
                    transport_diagnostic = {'http_status': status_code,
                        'provider_body_sha256': hashlib.sha256(raw).hexdigest()}
                    try:
                        failed_document = json.loads(raw.decode('utf8'))
                        if isinstance(failed_document, Mapping):
                            transport_diagnostic.update(safe_error_details(failed_document))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        pass
                if status_code in {401, 403}:
                    raise ValueError("API_AUTHENTICATION_REJECTED")
                if status_code == 429:
                    raise ValueError("API_RATE_LIMITED")
                if status_code != 200:
                    raise ValueError(f"OCR_HTTP_{status_code}")
                document = json.loads(raw.decode("utf-8", errors="strict"))
                choices = document.get("choices") if isinstance(document, Mapping) else None
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
                    raise ValueError("OCR_RESPONSE_INVALID")
                message = choices[0].get("message")
                content: Any = message.get("content") if isinstance(message, Mapping) else None
                if isinstance(content, list):
                    content = "\n".join(
                        str(item.get("text", ""))
                        for item in content
                        if isinstance(item, Mapping) and item.get("type") == "text"
                    )
                finish_reason = choices[0].get("finish_reason")
                if finish_reason == "length":
                    raise ValueError("OCR_OUTPUT_TRUNCATED")
                observed_model = document.get("model") if isinstance(document, Mapping) else None
                if observed_model != requested_model:
                    raise ValueError("OCR_RETURNED_MODEL_MISMATCH")
                returned_model = str(observed_model)
                raw_usage = document.get("usage") if isinstance(document.get("usage"), Mapping) else {}
                token_usage = _normalise_token_usage(raw_usage)
                route = "EXISTING_SETTINGS_API_MULTIMODAL_TRANSPORT"
                region = "PROVIDER_MANAGED_UNDISCLOSED"
                egress = "PROVIDER_API"
                identity_evidence = "PROVIDER_RESPONSE_MODEL_FIELD"
                provider_identity_grade = "VERIFIED"
                effective_identity_grade = "VERIFIED"
                estimated_cost_cny = (
                    0.0 if requested_model == "deepseek-ai/DeepSeek-OCR" else None
                )
                cost_evidence = (
                    "FROZEN_Quality_OCR_ZERO_PRICE_PROFILE_2026-07-31"
                    if estimated_cost_cny == 0.0
                    else "ATTEMPT_COST_EVIDENCE_UNAVAILABLE"
                )
            elif profile_kind == "LOCAL":
                if (
                    service.get("endpoint_kind") != "ollama"
                    or service.get("execution_eligible") is not True
                    or service.get("exact_identity_available") is not True
                    or not isinstance(service.get("chat_endpoint"), str)
                    or not isinstance(service.get("model_digest"), str)
                    or not SAFE_SHA256.fullmatch(str(service["model_digest"]).upper())
                ):
                    raise ValueError("LOCAL_OCR_PROFILE_INVALID")
                parsed = urlsplit(str(service["chat_endpoint"]))
                if (
                    parsed.scheme != "http"
                    or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or parsed.path != "/api/chat"
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError("LOCAL_OCR_ENDPOINT_INVALID")
                # Research attachments use the same declared capacity policy as
                # ordinary local calls. An old exam allocation is not a user
                # output limit; absent limits remain managed by the runtime.
                endpoint = str(service["chat_endpoint"])
                tags_endpoint = urlunsplit((parsed.scheme, parsed.netloc, "/api/tags", "", ""))
                opener = build_opener(ProxyHandler({}), _NoRedirect())
                metadata_calls = 1
                from .call_ledger import recording_urlopen
                opener.open = recording_urlopen(opener.open)
                with opener.open(Request(tags_endpoint, method="GET"), timeout=min(10, timeout_seconds)) as response:
                    tags_raw = response.read(2 * 1024 * 1024 + 1)
                if len(tags_raw) > 2 * 1024 * 1024:
                    raise ValueError("LOCAL_OCR_TAGS_RESPONSE_TOO_LARGE")
                tags = json.loads(tags_raw.decode("utf-8"))
                rows = tags.get("models") if isinstance(tags, Mapping) else None
                matched = next(
                    (
                        row
                        for row in (rows if isinstance(rows, list) else [])
                        if isinstance(row, Mapping)
                        and (row.get("model") or row.get("name")) == requested_model
                    ),
                    None,
                )
                if (
                    not isinstance(matched, Mapping)
                    or str(matched.get("digest") or "").upper()
                    != str(service["model_digest"]).upper()
                ):
                    raise ValueError("LOCAL_OCR_MODEL_IDENTITY_MISMATCH")
                limits = resolve_call_limits(profile_kind=profile_kind, service=service, model=model,
                                             requested_output_tokens=max_output_tokens)
                local_context = (limits['capacity']['effective_capacity']['shared_context_tokens']
                                 if limits['capacity']['machine_safe_input_tokens'] is not None else None)
                max_output_tokens = limits['max_output_tokens']
                timeout_seconds = limits['timeout_seconds']
                payload = {
                    "model": requested_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt,
                            "images": [image_base64],
                        }
                    ],
                    "stream": False,
                    "think": False,
                    "options": {
                        "temperature": 0,
                        "seed": 0,
                        **({"num_ctx": local_context} if local_context is not None else {}),
                        **({"num_predict": max_output_tokens} if max_output_tokens is not None else {}),
                    },
                }
                body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
                request = Request(endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST")
                model_calls = 1
                try:
                    with opener.open(request, timeout=timeout_seconds) as response:
                        status_code = int(response.status)
                        raw = response.read()
                except HTTPError as error:
                    status_code = int(error.code)
                    raw = error.read()
                    message = ""
                    try:
                        failed_document = json.loads(raw.decode("utf-8", errors="strict"))
                        if isinstance(failed_document, Mapping) and isinstance(
                            failed_document.get("error"), str
                        ):
                            message = re.sub(
                                r"[^A-Za-z0-9 _.,:;()/-]+",
                                "",
                                str(failed_document["error"]),
                            )[:96].strip()
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        message = ""
                    suffix = f":{message}" if message else ""
                    raise ValueError(f"LOCAL_OCR_HTTP_{status_code}{suffix}") from error
                if status_code != 200:
                    raise ValueError(f"LOCAL_OCR_HTTP_{status_code}")
                document = json.loads(raw.decode("utf-8", errors="strict"))
                message = document.get("message") if isinstance(document, Mapping) else None
                content = message.get("content") if isinstance(message, Mapping) else None
                finish_reason = document.get("done_reason") if isinstance(document, Mapping) else None
                if finish_reason == "length":
                    raise ValueError("OCR_OUTPUT_TRUNCATED")
                observed_model = document.get("model") if isinstance(document, Mapping) else None
                if observed_model != requested_model:
                    raise ValueError("LOCAL_OCR_RETURNED_MODEL_MISMATCH")
                returned_model = requested_model
                token_usage = _normalise_token_usage(document)
                route = "OLLAMA_MULTIMODAL_LOOPBACK"
                region = "LOCAL_MACHINE"
                egress = "LOOPBACK_ONLY"
                identity_evidence = "OLLAMA_EXACT_NAME_AND_DIGEST_PRECALL_BINDING"
                provider_identity_grade = "VERIFIED"
                effective_identity_grade = "VERIFIED"
                estimated_cost_cny = 0.0
                cost_evidence = "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
            else:
                if max_output_tokens is not None:
                    raise ValueError("CLI_OCR_EXPLICIT_OUTPUT_LIMIT_UNSUPPORTED")
                adapter_id = service.get("adapter_id")
                executable_value = service.get("executable")
                thinking_mode = model.get("thinking_mode")
                if (
                    adapter_id != "codex_cli"
                    or not isinstance(executable_value, str)
                    or not isinstance(thinking_mode, str)
                    or not thinking_mode
                ):
                    raise ValueError("CLI_OCR_PROFILE_INVALID")
                executable = self._executable_resolver(executable_value)
                if executable is None:
                    raise ValueError("CLI_EXECUTABLE_NOT_FOUND")
                from .cli_image_identity import bind_saved_cli_identity
                proxy_mode, proxy_address, _ = self._preferences()
                self._scratch_root.mkdir(parents=True, exist_ok=True)
                service, identity_probes = bind_saved_cli_identity(
                    service, executable, process=self._process,
                    environment=self._safe_environment(
                        adapter_id, self._scratch_root, proxy_mode, proxy_address),
                    root=self._scratch_root)
                metadata_calls += identity_probes
                expected_binary_sha256 = service.get("cli_binary_sha256")
                cli_version = service.get("cli_version")
                identity_authority_ref = service.get("cli_identity_authority_ref")
                if (
                    not isinstance(expected_binary_sha256, str)
                    or not SAFE_SHA256.fullmatch(expected_binary_sha256.upper())
                    or _resolved_file_sha256(executable)
                    != expected_binary_sha256.upper()
                    or not isinstance(cli_version, str)
                    or not cli_version
                    or not isinstance(identity_authority_ref, str)
                    or not identity_authority_ref
                ):
                    raise ValueError("CLI_OCR_BINARY_IDENTITY_BINDING_INVALID")
                proxy_mode, proxy_address, _ = self._preferences()
                self._scratch_root.mkdir(parents=True, exist_ok=True)
                suffix = {
                    "image/png": ".png",
                    "image/jpeg": ".jpg",
                    "image/webp": ".webp",
                }[mime_type]
                with tempfile.TemporaryDirectory(
                    prefix="memorive-ocr-cli-", dir=self._scratch_root
                ) as temporary:
                    workspace = Path(temporary).resolve()
                    image_path = workspace / f"input{suffix}"
                    with image_path.open("xb") as stream:
                        stream.write(image_bytes)
                    argv, stdin_bytes = _cli_argv(
                        adapter_id,
                        executable,
                        requested_model,
                        thinking_mode,
                        str(workspace),
                        prompt.encode("utf-8"),
                        None,
                        str(image_path),
                    )
                    model_indices = [
                        index for index, item in enumerate(argv) if item == "--model"
                    ]
                    if (
                        len(model_indices) != 1
                        or model_indices[0] + 1 >= len(argv)
                        or argv[model_indices[0] + 1] != requested_model
                    ):
                        raise ValueError("CODEX_REQUEST_MODEL_ARGV_INVALID")
                    model_allowlist_sha256 = _workflow_exam_v8_sha256(
                        {"allowed_models": [requested_model]}
                    )
                    request_model_binding_sha256 = _workflow_exam_v8_sha256(
                        {
                            "adapter_id": "codex_cli",
                            "binary_sha256": expected_binary_sha256.upper(),
                            "cli_version": cli_version,
                            "argv_sha256": _workflow_exam_v8_sha256(list(argv)),
                            "model_flag_index": model_indices[0],
                            "requested_model": requested_model,
                            "model_allowlist_sha256": model_allowlist_sha256,
                            "behavior_sha256": behavior_sha256,
                        }
                    )
                    process = self._process.run(
                        argv=argv,
                        cwd=str(workspace),
                        environment=self._safe_environment(
                            adapter_id, workspace, proxy_mode, proxy_address
                        ),
                        stdin_bytes=stdin_bytes,
                        timeout_seconds=timeout_seconds,
                        shell=False,
                    )
                if not process.started:
                    raise ValueError("CLI_PROCESS_START_FAILED")
                process_launches = 1
                model_calls = 1
                if process.timed_out:
                    raise ValueError("CLI_OCR_TIMEOUT")
                if process.output_truncated:
                    raise ValueError("CLI_OUTPUT_LIMIT_EXCEEDED")
                if process.returncode != 0:
                    diagnostic = _cli_failure_diagnostic(process)
                    raise ValueError(str(diagnostic.get("category") or "CLI_OCR_FAILED"))
                execution_diagnostic, execution_error = _execution_codex_event_diagnostic(
                    process.stdout, requested_model
                )
                if execution_error is not None:
                    raise ValueError(execution_error)
                if not isinstance(execution_diagnostic, Mapping):
                    raise ValueError("CODEX_OCR_EVENT_EVIDENCE_MISSING")
                execution_diagnostic = {
                    **dict(execution_diagnostic),
                    "request_model_binding_status": (
                        "EXACT_ARGV_AND_FROZEN_ALLOWLIST"
                    ),
                    "request_model_binding_sha256": (
                        request_model_binding_sha256
                    ),
                    "model_allowlist_sha256": model_allowlist_sha256,
                }
                request_model_binding_status = str(
                    execution_diagnostic["request_model_binding_status"]
                )
                if execution_diagnostic.get("finish_status") != "SUCCESS":
                    raise ValueError("CODEX_JSONL_FINISH_NOT_SUCCESS")
                provider_identity_grade = execution_diagnostic.get(
                    "model_identity_grade"
                )
                if provider_identity_grade == "VERIFIED":
                    if execution_diagnostic.get("returned_model") != requested_model:
                        raise ValueError("CLI_RETURNED_MODEL_MISMATCH")
                    effective_identity_grade = "VERIFIED"
                    returned_model = requested_model
                elif provider_identity_grade == "PROVIDER_NOT_EXPOSED":
                    effective_identity_grade = "RECORDED_UNVERIFIED"
                    returned_model = None
                else:
                    raise ValueError("CLI_RETURNED_MODEL_CONFLICT")
                execution_usage = execution_diagnostic.get("token_usage")
                if (
                    execution_diagnostic.get("usage_status") != "ACTUAL"
                    or not isinstance(execution_usage, Mapping)
                ):
                    raise ValueError("OCR_TOKEN_EVIDENCE_MISSING")
                prompt_tokens = execution_usage.get("input_tokens")
                completion_tokens = execution_usage.get("output_tokens")
                if (
                    isinstance(prompt_tokens, bool)
                    or not isinstance(prompt_tokens, int)
                    or prompt_tokens < 0
                    or isinstance(completion_tokens, bool)
                    or not isinstance(completion_tokens, int)
                    or completion_tokens < 0
                ):
                    raise ValueError("OCR_TOKEN_EVIDENCE_MISSING")
                token_usage = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                }
                content = _codex_agent_message_from_events(process.stdout)
                finish_reason = "stop"
                raw = process.stdout
                route = "CODEX_CLI_SUBSCRIPTION_MULTIMODAL"
                region = "OPENAI_MANAGED_UNDISCLOSED"
                egress = "CODEX_CLI"
                identity_evidence = "DIRECT_CLI_REQUEST_BINDING_V1"
                estimated_cost_cny = None
                cost_evidence = "CHATGPT_SUBSCRIPTION_CLI_NO_PER_CALL_PRICE_RECEIPT"
            if not isinstance(content, str) or not content.strip():
                raise ValueError("OCR_RESPONSE_EMPTY")
            if not token_usage:
                raise ValueError("OCR_TOKEN_EVIDENCE_MISSING")
            raw_text = content
            if requested_model == "deepseek-ai/DeepSeek-OCR":
                content = _normalize_deepseek_ocr_markdown(content)
            if not isinstance(content, str) or not content.strip():
                raise ValueError("OCR_RESPONSE_EMPTY_AFTER_NORMALIZATION")
            response_sha256 = hashlib.sha256(raw).hexdigest().upper()
            duration_ms = max(0, round((time.monotonic() - started) * 1000))
            return {
                "schema_version": "SettingsOcrImageRunnerResult-v1",
                "status": "PASS",
                "reason": "OCR_IMAGE_COMPLETED",
                "requested_model": requested_model,
                "returned_model": returned_model,
                "raw_text": raw_text,
                "text": content,
                "normalization_revision": (
                    DEEPSEEK_OCR_NORMALIZATION_REVISION
                    if requested_model == "deepseek-ai/DeepSeek-OCR"
                    else "IDENTITY"
                ),
                "normalization_applied": raw_text != content,
                "finish_reason": str(finish_reason or "unknown"),
                "duration_ms": duration_ms,
                "external_network_calls": model_calls if profile_kind == "API" else 0,
                "provider_calls": model_calls if profile_kind == "API" else 0,
                "external_model_calls": model_calls,
                "physical_model_calls": model_calls,
                "external_budget_calls": (
                    model_calls if profile_kind in {"API", "CLI"} else 0
                ),
                "local_model_calls": model_calls if profile_kind == "LOCAL" else 0,
                "external_process_launches": process_launches,
                "local_metadata_calls": metadata_calls,
                "execution_receipt": {
                    "schema_version": "SettingsOcrImageExecutionReceipt-v1",
                    "status": "PASS",
                    "profile_kind": profile_kind,
                    "purpose": purpose,
                    "route": route,
                    "region": region,
                    "egress": egress,
                    "requested_model": requested_model,
                    "returned_model": returned_model,
                    "model_digest": service.get("model_digest"),
                    "model_identity_evidence": identity_evidence,
                    "model_identity_grade": effective_identity_grade,
                    "provider_event_model_identity_grade": provider_identity_grade,
                    "request_model_binding_status": request_model_binding_status,
                    "request_model_binding_sha256": request_model_binding_sha256,
                    "model_allowlist_sha256": model_allowlist_sha256,
                    "identity_authority_ref": identity_authority_ref,
                    "cli_binary_sha256": (
                        service.get("cli_binary_sha256")
                        if profile_kind == "CLI"
                        else None
                    ),
                    "cli_version": (
                        service.get("cli_version")
                        if profile_kind == "CLI"
                        else None
                    ),
                    "behavior_sha256": behavior_sha256,
                    "image_sha256": hashlib.sha256(image_bytes).hexdigest().upper(),
                    "response_sha256": response_sha256,
                    "raw_text_sha256": hashlib.sha256(
                        raw_text.encode("utf-8")
                    ).hexdigest().upper(),
                    "normalized_text_sha256": hashlib.sha256(
                        content.encode("utf-8")
                    ).hexdigest().upper(),
                    "normalization_revision": (
                        DEEPSEEK_OCR_NORMALIZATION_REVISION
                        if requested_model == "deepseek-ai/DeepSeek-OCR"
                        else "IDENTITY"
                    ),
                    "normalization_applied": raw_text != content,
                    "finish_reason": str(finish_reason or "unknown"),
                    "latency_ms": duration_ms,
                    "token_usage": token_usage,
                    "actual_cost": None,
                    "estimated_cost_cny": estimated_cost_cny,
                    "cost_evidence": cost_evidence,
                    "max_output_tokens_mode": (
                        "PROVIDER_DEFAULT"
                        if max_output_tokens is None
                        else "PROFILE_SPECIFIC_EXPLICIT"
                    ),
                    "request_max_output_tokens": max_output_tokens,
                    "output_limit_contract": (
                        workflow_exam_output_limit_contract()
                        if purpose == "workflow_model_exam"
                        else None
                    ),
                    "observed_completion_tokens": int(
                        token_usage.get("completion_tokens") or 0
                    ),
                    "reply_length_quality_semantics": (
                        "EVIDENCE_RETAINED_NEVER_MODEL_FAIL_OR_SEMANTIC_REPAIR"
                        if purpose == "workflow_model_exam"
                        else None
                    ),
                },
            }
        except Exception as error:
            failure = self._specialized_failure(
                schema_version="SettingsOcrImageRunnerResult-v1",
                reason=str(error) or type(error).__name__,
                requested_model=requested_model if isinstance(requested_model, str) else None,
                returned_model=returned_model,
                profile_kind=profile_kind,
                started=started,
                model_calls=model_calls,
                metadata_calls=metadata_calls,
                process_launches=process_launches,
            )
            if transport_diagnostic:
                failure["transport_diagnostic"] = transport_diagnostic
            return failure
        finally:
            secret = ""
            secret_bytes = b""
            headers.clear()

    @recorded_execution
    def execute_rerank(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        query: str,
        documents: Sequence[str],
        purpose: str,
        timeout_seconds: int = 120,
    ) -> Mapping[str, Any]:
        started = time.monotonic()
        requested_model = model.get("model_name")
        accepted_documents = list(documents)
        if (
            profile_kind not in {"API", "LOCAL"}
            or not isinstance(requested_model, str)
            or not SAFE_MODEL.fullmatch(requested_model)
            or not isinstance(query, str)
            or not query.strip()
            or len(query.encode("utf-8")) > 65_536
            or not accepted_documents
            or len(accepted_documents) > 64
            or any(
                not isinstance(document, str)
                or not document
                or len(document.encode("utf-8")) > 65_536
                for document in accepted_documents
            )
            or purpose not in {"workflow_model_exam", "core_document_processing"}
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 5 <= timeout_seconds <= 600
        ):
            raise ValueError("RERANK_REQUEST_INVALID")
        request_payload = {
            "model": requested_model,
            "query": query,
            "documents": accepted_documents,
            "return_documents": False,
            "top_n": len(accepted_documents),
            "max_chunks_per_doc": 1,
            "overlap_tokens": 0,
        }
        behavior_sha256 = hashlib.sha256(
            json.dumps(
                request_payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest().upper()
        secret = ""
        secret_bytes = b""
        headers: dict[str, str] = {}
        model_calls = 0
        returned_model: str | None = requested_model if profile_kind == "LOCAL" else None
        try:
            body = json.dumps(
                request_payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if profile_kind == "API":
                profile = _api_profile(service)
                if profile is None or profile.provider_family != "OPENAI_COMPATIBLE":
                    raise ValueError("RERANK_API_PROFILE_UNSUPPORTED")
                parsed = urlsplit(profile.url)
                path = parsed.path.rstrip("/")
                if not path.casefold().endswith("/models"):
                    raise ValueError("RERANK_API_ENDPOINT_INVALID")
                endpoint = urlunsplit((parsed.scheme, parsed.netloc, path[: -len("/models")] + "/rerank", "", ""))
                credential_ref = service.get("credential_ref")
                if not isinstance(credential_ref, str) or not credential_ref:
                    raise ValueError("API_CREDENTIAL_UNAVAILABLE")
                secret_bytes = self._credential_resolver(credential_ref)
                if not secret_bytes or len(secret_bytes) > 512:
                    raise ValueError("API_CREDENTIAL_UNAVAILABLE")
                secret = secret_bytes.decode("utf-8", errors="strict")
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "Memorive-Reranker-Exam/1",
                    **dict(profile.extra_headers),
                }
                headers[profile.auth_header] = (
                    f"Bearer {secret}" if profile.auth_header == "Authorization" else secret
                )
                proxy_mode, proxy_address, _ = self._preferences()
                model_calls = 1
                response = self._http.request(
                    method="POST",
                    url=endpoint,
                    headers=headers,
                    body=body,
                    timeout_seconds=timeout_seconds,
                    proxy_mode=proxy_mode,
                    proxy_address=proxy_address,
                )
                if response.status_code in {401, 403}:
                    raise ValueError("API_AUTHENTICATION_REJECTED")
                if response.status_code == 429:
                    raise ValueError("API_RATE_LIMITED")
                if response.status_code != 200:
                    raise ValueError(f"RERANK_HTTP_{response.status_code}")
                raw = response.body
                document = json.loads(raw.decode("utf-8", errors="strict"))
                observed_model = document.get("model") if isinstance(document, Mapping) else None
                if observed_model is not None and observed_model != requested_model:
                    raise ValueError("RERANK_RETURNED_MODEL_MISMATCH")
                if observed_model == requested_model:
                    returned_model = requested_model
                    identity_evidence = "PROVIDER_RESPONSE_MODEL_FIELD"
                    identity_grade = "DIRECT_RETURNED_MODEL_MATCH"
                    request_model_binding_status = "EXACT_REQUEST_AND_RESPONSE_MODEL"
                elif requested_model == "Qwen/Qwen3-VL-Reranker-8B":
                    catalog_receipt_sha256 = service.get(
                        "catalog_model_identity_receipt_sha256"
                    )
                    if (
                        service.get("catalog_model_identity_status")
                        != "AVAILABLE"
                        or not isinstance(catalog_receipt_sha256, str)
                        or not SAFE_SHA256.fullmatch(
                            catalog_receipt_sha256.upper()
                        )
                    ):
                        raise ValueError("RERANK_CATALOG_IDENTITY_EVIDENCE_MISSING")
                    returned_model = None
                    identity_evidence = (
                        "PREVALIDATED_CATALOG_AND_EXACT_REQUEST_MODEL_"
                        "PROVIDER_RESPONSE_OMITS_MODEL"
                    )
                    identity_grade = (
                        "REQUEST_BOUND_CATALOG_CONFIRMED_"
                        "PROVIDER_RESPONSE_OMITS_MODEL"
                    )
                    request_model_binding_status = (
                        "EXACT_REQUEST_MODEL_AND_PREVALIDATED_CATALOG"
                    )
                else:
                    returned_model = requested_model
                    identity_evidence = (
                        "LEGACY_EXACT_REQUEST_MODEL_PROVIDER_RESPONSE_OMITS_MODEL"
                    )
                    identity_grade = "LEGACY_REQUEST_BOUND"
                    request_model_binding_status = "EXACT_REQUEST_MODEL_ONLY"
                route = "EXISTING_SETTINGS_API_RERANK_TRANSPORT"
                region = "PROVIDER_MANAGED_UNDISCLOSED"
                egress = "PROVIDER_API"
                price = (
                    0.07
                    if requested_model == "Pro/BAAI/bge-reranker-v2-m3"
                    else 0.7
                    if requested_model == "Qwen/Qwen3-VL-Reranker-8B"
                    else 0.0
                    if requested_model == "BAAI/bge-reranker-v2-m3"
                    else None
                )
            else:
                endpoint_value = service.get("rerank_endpoint")
                if (
                    service.get("endpoint_kind") != "local_reranker"
                    or service.get("capability") != "RERANKER"
                    or service.get("execution_eligible") is not True
                    or service.get("exact_identity_available") is not True
                    or not isinstance(service.get("model_digest"), str)
                    or not SAFE_SHA256.fullmatch(str(service["model_digest"]).upper())
                    or not isinstance(endpoint_value, str)
                ):
                    raise ValueError("LOCAL_RERANK_PROFILE_INVALID")
                parsed = urlsplit(endpoint_value)
                if (
                    parsed.scheme != "http"
                    or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError("LOCAL_RERANK_ENDPOINT_INVALID")
                opener = build_opener(ProxyHandler({}), _NoRedirect())
                request = Request(endpoint_value, data=body, headers={"Content-Type": "application/json"}, method="POST")
                from .call_ledger import recording_urlopen
                opener.open = recording_urlopen(opener.open)
                model_calls = 1
                with opener.open(request, timeout=timeout_seconds) as response:
                    status_code = int(response.status)
                    raw = response.read(MAX_HTTP_BODY_BYTES + 1)
                if len(raw) > MAX_HTTP_BODY_BYTES:
                    raise ValueError("LOCAL_RERANK_RESPONSE_TOO_LARGE")
                if status_code != 200:
                    raise ValueError(f"LOCAL_RERANK_HTTP_{status_code}")
                document = json.loads(raw.decode("utf-8", errors="strict"))
                observed_model = document.get("model") if isinstance(document, Mapping) else None
                if observed_model not in {None, requested_model}:
                    raise ValueError("LOCAL_RERANK_RETURNED_MODEL_MISMATCH")
                returned_model = requested_model
                identity_evidence = "LOCAL_EXACT_PROFILE_DIGEST_AND_EXACT_REQUEST_MODEL"
                identity_grade = "LOCAL_EXACT_NAME_DIGEST_AND_REQUEST_MODEL"
                request_model_binding_status = "LOCAL_EXACT_PROFILE_BINDING"
                route = "LOCAL_RERANKER_LOOPBACK"
                region = "LOCAL_MACHINE"
                egress = "LOOPBACK_ONLY"
                price = 0.0
            results = document.get("results") if isinstance(document, Mapping) else None
            if not isinstance(results, list):
                raise ValueError("RERANK_RESULTS_MISSING")
            indices = [
                row.get("index") if isinstance(row, Mapping) else None
                for row in results
            ]
            if (
                len(results) != len(accepted_documents)
                or set(indices) != set(range(len(accepted_documents)))
            ):
                raise ValueError("RERANK_RESULT_INDEX_SET_MISMATCH")
            normalized: list[dict[str, Any]] = []
            for response_order, row in enumerate(results):
                if not isinstance(row, Mapping):
                    raise ValueError("RERANK_RESULT_INVALID")
                score = row.get("relevance_score")
                if (
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(float(score))
                ):
                    raise ValueError("RERANK_SCORE_INVALID")
                normalized.append(
                    {
                        "index": int(row["index"]),
                        "relevance_score": float(score),
                        "response_order": response_order,
                    }
                )
            normalized.sort(
                key=lambda row: (
                    -row["relevance_score"],
                    row["response_order"],
                    row["index"],
                )
            )
            meta = document.get("meta") if isinstance(document.get("meta"), Mapping) else {}
            meta_tokens = meta.get("tokens") if isinstance(meta.get("tokens"), Mapping) else {}
            usage = document.get("usage") if isinstance(document.get("usage"), Mapping) else {}
            token_usage = _normalise_token_usage(usage)
            if not token_usage:
                input_tokens = _nonnegative_int(meta_tokens.get("input_tokens"))
                if input_tokens is not None:
                    token_usage = {"prompt_tokens": input_tokens, "completion_tokens": 0}
            if profile_kind == "API" and not token_usage:
                raise ValueError("RERANK_TOKEN_EVIDENCE_MISSING")
            if profile_kind == "LOCAL" and not token_usage:
                token_usage = {"prompt_tokens": 0, "completion_tokens": 0}
            estimated_cost_cny = (
                round(token_usage["prompt_tokens"] * float(price) / 1_000_000, 12)
                if price is not None
                else None
            )
            cost_evidence = (
                "FROZEN_Quality_RERANKER_INPUT_PRICE_ESTIMATE"
                if profile_kind == "API" and price is not None
                else "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
                if profile_kind == "LOCAL"
                else "ATTEMPT_COST_EVIDENCE_UNAVAILABLE"
            )
            duration_ms = max(0, round((time.monotonic() - started) * 1000))
            return {
                "schema_version": "SettingsRerankRunnerResult-v1",
                "status": "PASS",
                "reason": "RERANK_COMPLETED",
                "requested_model": requested_model,
                "returned_model": returned_model,
                "results": normalized,
                "duration_ms": duration_ms,
                "external_network_calls": model_calls if profile_kind == "API" else 0,
                "provider_calls": model_calls if profile_kind == "API" else 0,
                "external_model_calls": model_calls,
                "physical_model_calls": model_calls,
                "external_budget_calls": model_calls if profile_kind == "API" else 0,
                "local_model_calls": model_calls if profile_kind == "LOCAL" else 0,
                "external_process_launches": 0,
                "execution_receipt": {
                    "schema_version": "SettingsRerankExecutionReceipt-v1",
                    "status": "PASS",
                    "profile_kind": profile_kind,
                    "purpose": purpose,
                    "route": route,
                    "region": region,
                    "egress": egress,
                    "requested_model": requested_model,
                    "returned_model": returned_model,
                    "model_digest": service.get("model_digest"),
                    "model_identity_evidence": identity_evidence,
                    "model_identity_grade": identity_grade,
                    "request_model_binding_status": request_model_binding_status,
                    "catalog_model_identity_receipt_sha256": service.get(
                        "catalog_model_identity_receipt_sha256"
                    ),
                    "behavior_sha256": behavior_sha256,
                    "response_sha256": hashlib.sha256(raw).hexdigest().upper(),
                    "duration_ms": duration_ms,
                    "token_usage": token_usage,
            "prompt_cache": cache_usage(dict(usage)),
                    "actual_cost": None,
                    "estimated_cost_cny": estimated_cost_cny,
                    "cost_evidence": cost_evidence,
                },
            }
        except Exception as error:
            return self._specialized_failure(
                schema_version="SettingsRerankRunnerResult-v1",
                reason=str(error) or type(error).__name__,
                requested_model=requested_model if isinstance(requested_model, str) else None,
                returned_model=returned_model,
                profile_kind=profile_kind,
                started=started,
                model_calls=model_calls,
            )
        finally:
            secret = ""
            secret_bytes = b""
            headers.clear()

class FailClosedModelValidationRunner:
    """Fallback boundary used outside the real desktop service."""

    @staticmethod
    def _not_run(kind: str) -> dict[str, Any]:
        return {
            "schema_version": "SettingsModelValidationResult-v1",
            "kind": kind,
            "status": "NOT_RUN",
            "score": None,
            "reason": "MODEL_VALIDATION_RUNNER_NOT_CONFIGURED",
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
            "external_process_launches": 0,
        }

    def execute_embeddings(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        inputs: Sequence[str],
        purpose: str,
        timeout_seconds: int = 120,
    ) -> Mapping[str, Any]:
        del service, model, inputs, purpose, timeout_seconds
        return {
            **self._not_run(profile_kind),
            "schema_version": "SettingsEmbeddingRunnerResult-v1",
            "status": "FAILED",
            "reason": "EMBEDDING_RUNNER_NOT_CONFIGURED",
        }

    def execute_ocr_image(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
        purpose: str,
        max_output_tokens: int | None = None,
        timeout_seconds: int = 120,
    ) -> Mapping[str, Any]:
        del service, model, image_bytes, mime_type, prompt, purpose, max_output_tokens, timeout_seconds
        return {
            **self._not_run(profile_kind),
            "schema_version": "SettingsOcrImageRunnerResult-v1",
            "status": "FAILED",
            "reason": "OCR_IMAGE_RUNNER_NOT_CONFIGURED",
        }

    def execute_rerank(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        query: str,
        documents: Sequence[str],
        purpose: str,
        timeout_seconds: int = 120,
    ) -> Mapping[str, Any]:
        del service, model, query, documents, purpose, timeout_seconds
        return {
            **self._not_run(profile_kind),
            "schema_version": "SettingsRerankRunnerResult-v1",
            "status": "FAILED",
            "reason": "RERANK_RUNNER_NOT_CONFIGURED",
        }

    def verify_api_model(self, service: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._not_run("API")

    def verify_cli_model(
        self,
        cli_service: Mapping[str, Any],
        model: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return self._not_run("CLI")

    def execute_structured_chat(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        prompt: str,
        response_schema: Mapping[str, Any],
        purpose: str,
        max_output_tokens: int | None = None,
        timeout_seconds: int | None = None,
    ) -> Mapping[str, Any]:
        del max_output_tokens, timeout_seconds
        return {
            **self._not_run(profile_kind),
            "schema_version": "SettingsStructuredChatRunnerResult-v1",
            "status": "FAILED",
            "reason": "STRUCTURED_CHAT_RUNNER_NOT_CONFIGURED",
        }

    def plan_workflow_node_exam(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        del node, target, context
        return {
            "schema_version": "WorkflowModelExamPlan-v2",
            "status": "NOT_AVAILABLE",
            "mode": "NONE",
            "reason": "MODEL_VALIDATION_RUNNER_NOT_CONFIGURED",
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
        }

    def test_workflow_node(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
        authorization: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        del node, target, context, authorization
        return self._not_run("WORKFLOW_NODE")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> None:
        return None


class UrllibHttpTransport:
    """HTTPS client that never follows redirects with an API key.

    Model/provider capacity is enforced at the execution-profile layer.  This
    transport does not add a smaller generic one-megabyte response ceiling.
    """

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
        proxy_mode: str,
        proxy_address: str,
        max_response_bytes: int | None = None,
    ) -> HttpResponse:
        if max_response_bytes is not None and (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or max_response_bytes <= 0
        ):
            raise ValueError("API_RESPONSE_LIMIT_INVALID")
        if not url.startswith("https://"):
            raise ValueError("API_VALIDATION_HTTPS_REQUIRED")
        from .network_compat import resolve_proxy, tls_context
        from urllib.request import HTTPSHandler
        proxy = resolve_proxy(url, proxy_mode, proxy_address)
        proxy_handler = ProxyHandler({'https': proxy} if proxy else {})
        opener = build_opener(proxy_handler, _NoRedirect(), HTTPSHandler(context=tls_context()))
        request = Request(url, data=body, headers=dict(headers), method=method)
        def read(response):
            response_headers = getattr(response, 'headers', {})
            if 'text/event-stream' in response_headers.get('Content-Type', ''):
                return _read_chat_stream(response, body)
            try:
                payload = response.read()
            except IncompleteRead as error:
                raise _ResponseInterrupted(error.partial, {
                    'stream': False, 'complete': False, 'exception_type': 'IncompleteRead',
                    'remote_cancellation_confirmed': False,
                }) from error
            return HttpResponse(status_code=int(response.status), body=payload)
        from .cancellable_http import read_response
        from .call_ledger import execution_interrupt_callback
        return read_response(opener.open, request, timeout=timeout_seconds,
            interrupt=execution_interrupt_callback(), reader=read, read_error_response=True)


class BoundedSubprocessTransport:
    """No-shell process runner with disk spooling and optional explicit quota."""

    def __init__(self, *, capture_quota_bytes: int | None = None):
        if capture_quota_bytes is None:
            raw = os.environ.get("MEMORIVE_CLI_CAPTURE_QUOTA_BYTES")
            capture_quota_bytes = int(raw) if raw and raw.strip() else None
        if capture_quota_bytes is not None and (
            isinstance(capture_quota_bytes, bool)
            or not isinstance(capture_quota_bytes, int)
            or capture_quota_bytes <= 0
        ):
            raise ValueError("CLI_CAPTURE_QUOTA_INVALID")
        self.capture_quota_bytes = capture_quota_bytes

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                process.kill()
            return
        process.kill()

    def run_verification(self, **kwargs):
        from .cli_verification_transport import run_verification
        return run_verification(self, **kwargs)

    def _read_bounded(self, path: Path) -> tuple[bytes, bool, int]:
        total = path.stat().st_size
        with path.open("rb") as stream:
            if self.capture_quota_bytes is None:
                value = stream.read()
                return value, False, total
            value = stream.read(self.capture_quota_bytes + 1)
        return (
            value[: self.capture_quota_bytes],
            total > self.capture_quota_bytes,
            total,
        )

    def run(
        self,
        *,
        argv: Sequence[str],
        cwd: str,
        environment: Mapping[str, str],
        stdin_bytes: bytes | None,
        timeout_seconds: int,
        shell: bool,
    ) -> ProcessResult:
        if shell is not False:
            raise ValueError("CLI_VALIDATION_SHELL_FORBIDDEN")
        workspace = Path(cwd)
        capture_workspace = workspace
        if (workspace / ".exclusive-call.lock").is_file():
            # Retain each attempt without colliding with a prior call in the
            # stable lane; only the child process cwd needs to stay constant.
            capture_workspace = workspace / "captures" / uuid.uuid4().hex
            capture_workspace.mkdir(parents=True, exist_ok=False)
        stdout_path = capture_workspace / "stdout.bin"
        stderr_path = capture_workspace / "stderr.bin"
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        started_at = time.monotonic()
        process: subprocess.Popen[bytes] | None = None
        timed_out = False
        try:
            with stdout_path.open("xb") as stdout_stream, stderr_path.open("xb") as stderr_stream:
                from .windows_cli import native_process_path
                process = subprocess.Popen(
                    list(argv),
                    cwd=native_process_path(cwd),
                    env=dict(environment),
                    stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                    shell=False,
                    creationflags=creationflags,
                    start_new_session=os.name != "nt",
                )
                try:
                    process.communicate(input=stdin_bytes, timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._terminate(process)
                    process.communicate(timeout=5)
            stdout, stdout_truncated, stdout_total = self._read_bounded(stdout_path)
            stderr, stderr_truncated, stderr_total = self._read_bounded(stderr_path)
            return ProcessResult(
                started=True,
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
                timed_out=timed_out,
                output_truncated=stdout_truncated or stderr_truncated,
                duration_ms=max(0, round((time.monotonic() - started_at) * 1000)),
                stdout_total_bytes=stdout_total,
                stderr_total_bytes=stderr_total,
                capture_quota_bytes=self.capture_quota_bytes,
            )
        except (FileNotFoundError, PermissionError, OSError):
            return ProcessResult(
                started=False,
                returncode=None,
                stdout=b"",
                stderr=b"",
                duration_ms=max(0, round((time.monotonic() - started_at) * 1000)),
            )


@dataclass(frozen=True)
class _ApiProfile:
    provider_family: str
    url: str
    auth_header: str
    extra_headers: Mapping[str, str]


def _compact(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _structured_chat_timeout_limit(purpose: str) -> int:
    if purpose in {"workflow_model_exam", "core_document_processing"}:
        return 3_600
    return 600


def _catalog_url(base_url: str, protocol: str) -> str:
    parsed = urlsplit(base_url)
    path = parsed.path.rstrip("/")
    lowered = path.casefold()
    if lowered.endswith("/models"):
        catalog_path = path
    elif protocol == "OPENAI_COMPATIBLE" and lowered.endswith("/chat/completions"):
        catalog_path = f"{path[:-len('/chat/completions')]}/models"
    elif protocol == "OPENAI_COMPATIBLE" and lowered.endswith("/responses"):
        catalog_path = f"{path[:-len('/responses')]}/models"
    elif protocol == "ANTHROPIC" and lowered.endswith("/messages"):
        catalog_path = f"{path[:-len('/messages')]}/models"
    elif path:
        catalog_path = f"{path}/models"
    else:
        version = "v1beta" if protocol == "GEMINI" else "v1"
        catalog_path = f"/{version}/models"
    if protocol == "GEMINI":
        query = "pageSize=1000"
    elif protocol == "ANTHROPIC":
        query = "limit=1000"
    else:
        query = ""
    return urlunsplit((parsed.scheme, parsed.netloc, catalog_path, query, ""))


def _api_profile(service: Mapping[str, Any]) -> _ApiProfile | None:
    provider = service.get("provider")
    if not isinstance(provider, str):
        return None
    protocol = service.get("api_protocol", "AUTO")
    base_url = service.get("api_base_url", "")
    if protocol != "AUTO" or base_url:
        if (
            not isinstance(protocol, str)
            or protocol not in API_PROTOCOLS
            or protocol == "AUTO"
        ):
            raise ValueError("API_ENDPOINT_INVALID")
        base_url = normalize_external_api_base_url(base_url)
        if protocol == "OPENAI_COMPATIBLE":
            return _ApiProfile(
                protocol,
                _catalog_url(base_url, protocol),
                "Authorization",
                {},
            )
        if protocol == "ANTHROPIC":
            return _ApiProfile(
                protocol,
                _catalog_url(base_url, protocol),
                "x-api-key",
                {"anthropic-version": "2023-06-01"},
            )
        if protocol == "GEMINI":
            return _ApiProfile(
                protocol,
                _catalog_url(base_url, protocol),
                "x-goog-api-key",
                {},
            )
        raise ValueError("API_ENDPOINT_INVALID")
    identity = _compact(provider)
    if re.fullmatch(r"deepseek(?:apikey[a-z0-9]*)?", identity):
        return _ApiProfile(
            "OPENAI_COMPATIBLE", "https://api.deepseek.com/models", "Authorization", {}
        )
    if re.fullmatch(
        r"(?:anthropic|claude)(?:apikey[a-z0-9]*)?", identity
    ):
        return _ApiProfile(
            "ANTHROPIC",
            "https://api.anthropic.com/v1/models?limit=1000",
            "x-api-key",
            {"anthropic-version": "2023-06-01"},
        )
    if re.fullmatch(r"openai(?:apikey[a-z0-9]*)?", identity):
        return _ApiProfile(
            "OPENAI", "https://api.openai.com/v1/models", "Authorization", {}
        )
    if re.fullmatch(r"(?:gemini|googleai)(?:apikey[a-z0-9]*)?", identity):
        return _ApiProfile(
            "GEMINI",
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
            "x-goog-api-key",
            {},
        )
    if re.fullmatch(r"(?:siliconflow|硅基流动|硅基)(?:apikey[a-z0-9]*)?", identity):
        return _ApiProfile(
            "OPENAI_COMPATIBLE",
            "https://api.siliconflow.cn/v1/models",
            "Authorization",
            {},
        )
    return None


def _catalog_document(body: bytes) -> Mapping[str, Any]:
    try:
        value = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("API_CATALOG_RESPONSE_INVALID") from error
    if not isinstance(value, Mapping):
        raise ValueError("API_CATALOG_RESPONSE_INVALID")
    return value


def _catalog_model_ids(
    profile: _ApiProfile, value: Mapping[str, Any]
) -> list[str]:
    rows = value.get("models" if profile.provider_family == "GEMINI" else "data")
    if not isinstance(rows, list):
        raise ValueError("API_CATALOG_RESPONSE_INVALID")
    found: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        candidate = row.get("name" if profile.provider_family == "GEMINI" else "id")
        if not isinstance(candidate, str) or not candidate:
            continue
        if candidate.startswith("models/"):
            candidate = candidate[len("models/") :]
        if len(candidate) <= 256:
            found.append(candidate)
    return found


def _valid_catalog_cursor(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_CATALOG_CURSOR_CHARS
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("API_CATALOG_RESPONSE_INVALID")
    return value


def _next_catalog_url(
    profile: _ApiProfile,
    value: Mapping[str, Any],
) -> str | None:
    parsed = urlsplit(profile.url)
    if profile.provider_family == "ANTHROPIC":
        has_more = value.get("has_more", False)
        if not isinstance(has_more, bool):
            raise ValueError("API_CATALOG_RESPONSE_INVALID")
        if not has_more:
            return None
        cursor = _valid_catalog_cursor(value.get("last_id"))
        query = urlencode((("limit", "1000"), ("after_id", cursor)))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))
    if profile.provider_family == "GEMINI":
        cursor_value = value.get("nextPageToken")
        if cursor_value is None or cursor_value == "":
            return None
        cursor = _valid_catalog_cursor(cursor_value)
        query = urlencode((("pageSize", "1000"), ("pageToken", cursor)))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))
    return None


def _catalog_match(
    profile: _ApiProfile, configured: str, catalog: Sequence[str]
) -> str | None:
    configured_identity = unicodedata.normalize("NFKC", configured).strip()
    for candidate in catalog:
        if unicodedata.normalize("NFKC", candidate).strip() == configured_identity:
            return candidate
    return None


def _catalog_choices(catalog: Sequence[str]) -> list[str]:
    choices: list[str] = []
    seen: set[str] = set()
    for candidate in catalog:
        if candidate in seen or not SAFE_MODEL.fullmatch(candidate):
            continue
        seen.add(candidate)
        choices.append(candidate)
        if len(choices) >= MAX_CATALOG_CHOICES:
            break
    return choices


def _catalog_suggestion(configured: str, catalog: Sequence[str]) -> str | None:
    """Suggest only a live catalog ID that is an unambiguous identity prefix.

    This recovers display-version/tier suffixes such as
    ``DeepSeek-V4-Flash-0731-maximum`` without treating the alias as a valid
    API model ID.  The caller must still save the returned exact catalog ID.
    """

    configured_identity = unicodedata.normalize("NFKC", configured).strip().casefold()
    matches: list[tuple[int, str]] = []
    for candidate in catalog:
        candidate_identity = (
            unicodedata.normalize("NFKC", candidate).strip().casefold()
        )
        if configured_identity == candidate_identity:
            matches.append((len(candidate_identity), candidate))
            continue
        if not configured_identity.startswith(candidate_identity):
            continue
        remainder = configured_identity[len(candidate_identity) :]
        if remainder and remainder[0] in "-_:/":
            matches.append((len(candidate_identity), candidate))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        return None
    return matches[0][1]


_CODEX_TOOL_DISABLE_CONFIG = (
    "features.shell_tool=false",
    "features.code_mode=false",
    "features.browser_use=false",
    "features.computer_use=false",
    "features.image_generation=false",
    "features.workspace_dependencies=false",
    "features.multi_agent=false",
)
_CODEX_TRANSPORT_SCHEMA_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "anyOf",
        "$ref",
        "$defs",
        "definitions",
        "description",
        "title",
    }
)
_CLAUDE_DISALLOWED_TOOLS = (
    "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task,"
    "Computer,NotebookEdit,AskUserQuestion"
)


def _json_schema_scalar_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    raise ValueError("CODEX_TRANSPORT_SCHEMA_VALUE_INVALID")


def _codex_refinement_transport_schema(source: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Use a homogeneous transport union; preserve ordered local validation."""
    candidate = json.loads(json.dumps(source))
    items = candidate.get("properties", {}).get("items")
    if not isinstance(items, dict) or not isinstance(items.get("prefixItems"), list) or not items["prefixItems"]:
        raise ValueError("CODEX_REFINEMENT_TUPLE_SCHEMA_INVALID")
    # prefixItems is unsupported by Codex. Each alternative retains its exact
    # message identity; the original schema and refinement validator still
    # enforce count, order, string lengths and provenance after the response.
    items["items"] = {"anyOf": items.pop("prefixItems")}
    projected, diagnostic = _codex_transport_schema(candidate)
    diagnostic["authoritative_source_schema_sha256"] = hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest().upper()
    diagnostic["refinement_tuple_projection"] = "ITEM_UNION_PLUS_ORIGINAL_ORDER_VALIDATOR"
    return projected, diagnostic


def _codex_transport_schema(
    source: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Project a Quality schema onto Codex structured-output's strict subset.

    The source schema remains the authoritative post-response validator.  This
    projection only removes constraints that the Codex/OpenAI transport rejects
    (for example ``uniqueItems`` and ``minLength``), while retaining the same
    object/array/scalar shape and closing every object.
    """

    removed: dict[str, int] = {}
    inferred_types = 0
    completed_required = 0

    def walk(schema: Mapping[str, Any]) -> dict[str, Any]:
        nonlocal inferred_types, completed_required
        projected: dict[str, Any] = {}
        for key, value in schema.items():
            if key not in _CODEX_TRANSPORT_SCHEMA_KEYS:
                removed[key] = removed.get(key, 0) + 1
                continue
            if key in {"properties", "$defs", "definitions"}:
                if not isinstance(value, Mapping):
                    raise ValueError("CODEX_TRANSPORT_SCHEMA_MAPPING_INVALID")
                projected[key] = {
                    str(name): walk(child)
                    for name, child in value.items()
                    if isinstance(name, str) and isinstance(child, Mapping)
                }
                if len(projected[key]) != len(value):
                    raise ValueError("CODEX_TRANSPORT_SCHEMA_MAPPING_INVALID")
            elif key == "items":
                if not isinstance(value, Mapping):
                    raise ValueError("CODEX_TRANSPORT_SCHEMA_ITEMS_INVALID")
                projected[key] = walk(value)
            elif key == "anyOf":
                if (
                    not isinstance(value, list)
                    or not value
                    or any(not isinstance(item, Mapping) for item in value)
                ):
                    raise ValueError("CODEX_TRANSPORT_SCHEMA_ANYOF_INVALID")
                projected[key] = [walk(item) for item in value]
            elif key in {"required", "enum", "type"}:
                projected[key] = json.loads(json.dumps(value))
            elif key == "additionalProperties":
                if value is not False:
                    raise ValueError(
                        "CODEX_TRANSPORT_SCHEMA_OPEN_OBJECT_FORBIDDEN"
                    )
                projected[key] = False
            else:
                projected[key] = json.loads(json.dumps(value))

        properties = projected.get("properties")
        if isinstance(properties, Mapping):
            observed_type = projected.get("type")
            if observed_type is not None and observed_type != "object":
                raise ValueError("CODEX_TRANSPORT_SCHEMA_OBJECT_TYPE_CONFLICT")
            if "type" not in projected:
                projected["type"] = "object"
                inferred_types += 1
            required = list(properties)
            if projected.get("required") != required:
                completed_required += 1
            projected["required"] = required
            projected["additionalProperties"] = False
        if "items" in projected and "type" not in projected:
            projected["type"] = "array"
            inferred_types += 1
        if projected.get("type") == "array" and "items" not in projected:
            raise ValueError("CODEX_TRANSPORT_SCHEMA_ARRAY_ITEMS_REQUIRED")
        if "const" in projected and "type" not in projected:
            projected["type"] = _json_schema_scalar_type(projected["const"])
            inferred_types += 1
        if "enum" in projected and "type" not in projected:
            enum = projected["enum"]
            if not isinstance(enum, list) or not enum:
                raise ValueError("CODEX_TRANSPORT_SCHEMA_ENUM_INVALID")
            observed = {_json_schema_scalar_type(item) for item in enum}
            if len(observed) != 1:
                raise ValueError("CODEX_TRANSPORT_SCHEMA_ENUM_TYPE_AMBIGUOUS")
            projected["type"] = next(iter(observed))
            inferred_types += 1
        return projected

    projected = walk(source)
    source_hash = hashlib.sha256(
        json.dumps(
            source,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest().upper()
    projected_hash = hashlib.sha256(
        json.dumps(
            projected,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest().upper()
    return projected, {
        "codex_transport_schema_projection": "OPENAI_STRICT_SUBSET_V1",
        "source_schema_sha256": source_hash,
        "transport_schema_sha256": projected_hash,
        "removed_keyword_counts": dict(sorted(removed.items())),
        "inferred_type_count": inferred_types,
        "completed_required_count": completed_required,
        "source_schema_remains_post_response_authority": True,
    }


def _cli_argv(
    adapter_id: str,
    executable: str,
    model: str,
    thinking_mode: str,
    cwd: str,
    prompt_bytes: bytes = MINIMAL_PROMPT,
    output_schema_path: str | None = None,
    image_path: str | None = None,
) -> tuple[tuple[str, ...], bytes | None]:
    from .windows_cli import native_process_path
    cwd=native_process_path(cwd)
    if output_schema_path:output_schema_path=native_process_path(output_schema_path)
    if image_path:image_path=native_process_path(image_path)
    if not SAFE_MODEL.fullmatch(model):
        raise ValueError("CLI_MODEL_NAME_INVALID")
    if thinking_mode and not SAFE_THINKING.fullmatch(thinking_mode):
        raise ValueError("CLI_THINKING_MODE_INVALID")
    if adapter_id == "codex_cli":
        # ChatGPT subscription auth belongs to Codex's built-in provider.  A
        # synthetic provider without a base URL makes an otherwise valid CLI
        # login fail before the requested model can run.
        config = _CODEX_TOOL_DISABLE_CONFIG
        config_argv = tuple(part for value in config for part in ("--config", value))
        effort = (
            ("--config", f'model_reasoning_effort="{thinking_mode}"')
            if thinking_mode
            else ()
        )
        output_schema = (
            ("--output-schema", output_schema_path)
            if output_schema_path is not None
            else ()
        )
        image = ("--image", image_path) if image_path is not None else ()
        return (
            (
                executable,
                "exec",
                "--ignore-user-config",
                "--strict-config",
                "--ignore-rules",
                "--ephemeral",
                "--skip-git-repo-check",
                "--color",
                "never",
                "--sandbox",
                "read-only",
                "--model",
                model,
                *effort,
                "--config",
                'shell_environment_policy.inherit="none"',
                *config_argv,
                "--cd",
                cwd,
                "--json",
                *output_schema,
                *image,
                "-",
            ),
            prompt_bytes,
        )
    if adapter_id == "claude_code":
        effort = ("--effort", thinking_mode) if thinking_mode else ()
        return (
            (
                executable,
                "--print",
                "--input-format",
                "text",
                "--output-format",
                "stream-json",
                "--verbose",
                "--model",
                model,
                *effort,
                "--permission-mode",
                "plan",
                "--tools",
                "",
                "--disallowedTools",
                _CLAUDE_DISALLOWED_TOOLS,
                "--safe-mode",
                "--disable-slash-commands",
                "--strict-mcp-config",
                "--mcp-config",
                "{}",
                "--no-chrome",
                "--no-session-persistence",
                "--prompt-suggestions",
                "false",
            ),
            prompt_bytes,
        )
    if adapter_id == "gemini_cli":
        return (
            (
                executable,
                "--prompt",
                "",
                "--output-format",
                "stream-json",
                "--model",
                model,
                "--approval-mode",
                "plan",
                "--extensions",
                "",
            ),
            prompt_bytes,
        )
    if adapter_id == "qwen_code":
        return (
            (
                executable,
                "--prompt",
                "",
                "--output-format",
                "stream-json",
                "--model",
                model,
                "--safe-mode",
                "--sandbox",
            ),
            prompt_bytes,
        )
    if adapter_id == "kimi_code":
        return (
            (
                executable,
                "--prompt",
                "",
                "--output-format",
                "stream-json",
                "--model",
                model,
            ),
            prompt_bytes,
        )
    if adapter_id == "codebuddy_code":
        return (
            (
                executable,
                "-p",
                "--output-format",
                "stream-json",
                "--model",
                model,
                "--tools",
                "",
                "--permission-mode",
                "plan",
                "--strict-mcp-config",
                "--no-session-persistence",
            ),
            prompt_bytes,
        )
    if adapter_id == "github_copilot_cli":
        return (
            (
                executable,
                "-p",
                prompt_bytes.decode("utf-8", errors="strict"),
                "--output-format=json",
                f"--model={model}",
                "--plan",
                "--deny-tool=shell,write,read,url,memory,github",
                "--disable-builtin-mcps",
                "--no-custom-instructions",
                "--no-experimental",
                "--no-remote",
                "--no-remote-export",
                "--no-auto-update",
                "--no-ask-user",
                "--no-color",
                "--stream=off",
                "--secret-env-vars=COPILOT_GITHUB_TOKEN,GH_TOKEN,GITHUB_TOKEN",
            ),
            None,
        )
    raise ValueError("CLI_ADAPTER_UNSUPPORTED")


def _walk_model_identity(value: Any, found: set[str]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if (
                str(key).casefold()
                in {"model", "model_name", "requested_model", "returned_model"}
                and isinstance(child, str)
                and SAFE_MODEL.fullmatch(child)
            ):
                found.add(child)
            _walk_model_identity(child, found)
    elif isinstance(value, list):
        for child in value:
            _walk_model_identity(child, found)


def _reported_models(output: bytes) -> set[str]:
    found: set[str] = set()
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        _walk_model_identity(value, found)
    if found:
        return found
    try:
        value = json.loads(output.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return set()
    _walk_model_identity(value, found)
    return found


def _walk_usage_candidates(value: Any, found: list[Mapping[str, Any]]) -> None:
    if isinstance(value, Mapping):
        keys = {str(key) for key in value}
        has_prompt = bool(
            keys
            & {
                "prompt_tokens",
                "input_tokens",
                "promptTokenCount",
                "inputTokens",
            }
        )
        has_completion = bool(
            keys
            & {
                "completion_tokens",
                "output_tokens",
                "candidatesTokenCount",
                "outputTokens",
            }
        )
        if has_prompt and has_completion:
            found.append(value)
        for child in value.values():
            _walk_usage_candidates(child, found)
    elif isinstance(value, list):
        for child in value:
            _walk_usage_candidates(child, found)


def _usage_from_cli_output(output: bytes) -> Mapping[str, Any]:
    found: list[Mapping[str, Any]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        _walk_usage_candidates(value, found)
    return found[-1] if found else {}


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _normalise_token_usage(value: Mapping[str, Any]) -> dict[str, int]:
    prompt = next(
        (
            parsed
            for key in (
                "prompt_tokens",
                "input_tokens",
                "promptTokenCount",
                "inputTokens",
                "prompt_eval_count",
            )
            if (parsed := _nonnegative_int(value.get(key))) is not None
        ),
        None,
    )
    completion = next(
        (
            parsed
            for key in (
                "completion_tokens",
                "output_tokens",
                "candidatesTokenCount",
                "outputTokens",
                "eval_count",
            )
            if (parsed := _nonnegative_int(value.get(key))) is not None
        ),
        None,
    )
    if prompt is None or completion is None:
        return {}
    normalised = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
    }
    cached = next(
        (
            parsed
            for key in (
                "cached_input_tokens",
                "prompt_cache_hit_tokens",
                "cache_read_input_tokens",
                "cachedContentTokenCount",
            )
            if (parsed := _nonnegative_int(value.get(key))) is not None
        ),
        None,
    )
    observed_cache = cache_usage(dict(value))
    cached = observed_cache["read_tokens"]
    if cached is not None:
        normalised["cached_input_tokens"] = cached
    if observed_cache["write_tokens"] is not None:
        normalised["cache_write_input_tokens"] = observed_cache["write_tokens"]
    cache_miss = _nonnegative_int(value.get("prompt_cache_miss_tokens"))
    if cache_miss is not None:
        normalised["prompt_cache_miss_tokens"] = cache_miss
    return normalised


def _deepseek_estimated_cost_cny(
    model: str,
    usage: Mapping[str, int],
) -> float | None:
    pricing = DEEPSEEK_ESTIMATE_CNY_PER_MILLION.get(model)
    prompt = _nonnegative_int(usage.get("prompt_tokens"))
    completion = _nonnegative_int(usage.get("completion_tokens"))
    if pricing is None or prompt is None or completion is None:
        return None
    cache_hit = min(
        prompt,
        _nonnegative_int(usage.get("cached_input_tokens")) or 0,
    )
    explicit_miss = _nonnegative_int(usage.get("prompt_cache_miss_tokens"))
    cache_miss = (
        min(prompt, explicit_miss)
        if explicit_miss is not None
        else prompt - cache_hit
    )
    estimate = (
        cache_hit * pricing["input_cache_hit"]
        + cache_miss * pricing["input_cache_miss"]
        + completion * pricing["output"]
    ) / 1_000_000
    return round(estimate, 12)


def _target_estimated_cost_cny(
    model_config: Mapping[str, Any],
    usage: Mapping[str, int],
) -> float | None:
    """Estimate a non-DeepSeek API call from a frozen target-side CNY profile.

    The transport deliberately does not carry a mutable global price table for
    every vendor.  A campaign that uses another paid API must bind its dated
    price evidence before sending and pass the conservative CNY rates with the
    model binding.  Cached input is charged at the ordinary input rate unless
    an explicit cache-hit rate is present, which keeps the estimate fail-safe.
    """

    pricing = model_config.get("pricing_estimate_cny_per_million")
    evidence_ref = model_config.get("pricing_evidence_ref")
    if not isinstance(pricing, Mapping) or not isinstance(evidence_ref, str):
        return None
    if not evidence_ref or len(evidence_ref) > 512:
        return None
    input_rate = pricing.get("input")
    output_rate = pricing.get("output")
    cache_hit_rate = pricing.get("input_cache_hit", input_rate)
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= float(value) <= 100_000
        for value in (input_rate, output_rate, cache_hit_rate)
    ):
        return None
    prompt = _nonnegative_int(usage.get("prompt_tokens"))
    completion = _nonnegative_int(usage.get("completion_tokens"))
    if prompt is None or completion is None:
        return None
    cache_hit = min(
        prompt,
        _nonnegative_int(usage.get("cached_input_tokens")) or 0,
    )
    cache_miss = prompt - cache_hit
    estimate = (
        cache_hit * float(cache_hit_rate)
        + cache_miss * float(input_rate)
        + completion * float(output_rate)
    ) / 1_000_000
    return round(estimate, 12)


def _api_estimated_cost_cny(
    model: str,
    model_config: Mapping[str, Any],
    usage: Mapping[str, int],
) -> float | None:
    deepseek = _deepseek_estimated_cost_cny(model, usage)
    if deepseek is not None:
        return deepseek
    return _target_estimated_cost_cny(model_config, usage)


def _structured_prompt(prompt: str, response_schema: Mapping[str, Any]) -> bytes:
    schema_text = json.dumps(
        response_schema,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    value = (
        f"{prompt.rstrip()}\n\n"
        "Return exactly one JSON object. Do not wrap it in Markdown. "
        "The object must conform to this JSON Schema:\n"
        f"{schema_text}"
    ).encode("utf-8")
    if not value:
        raise ValueError("STRUCTURED_CHAT_PROMPT_EMPTY")
    return value


def _json_object_from_text(value: str) -> Mapping[str, Any] | None:
    accepted = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", accepted, re.DOTALL | re.IGNORECASE)
    if fenced:
        accepted = fenced.group(1).strip()
    try:
        document = json.loads(accepted)
    except json.JSONDecodeError:
        start = accepted.find("{")
        if start < 0:
            return None
        try:
            document, _ = json.JSONDecoder().raw_decode(accepted[start:])
        except json.JSONDecodeError:
            return None
    return document if isinstance(document, Mapping) else None


def _walk_structured_candidates(value: Any, found: list[Mapping[str, Any]]) -> None:
    if isinstance(value, Mapping):
        if isinstance(value.get("schema_version"), str) and isinstance(value.get("items"), list):
            found.append(value)
        for key, child in value.items():
            if str(key).casefold() in {
                "text", "content", "result", "response", "output", "output_text", "message"
            } and isinstance(child, str):
                parsed = _json_object_from_text(child)
                if parsed is not None:
                    found.append(parsed)
            _walk_structured_candidates(child, found)
    elif isinstance(value, list):
        for child in value:
            _walk_structured_candidates(child, found)


def _structured_document_from_output(output: bytes) -> Mapping[str, Any] | None:
    found: list[Mapping[str, Any]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        _walk_structured_candidates(value, found)
    if not found:
        try:
            text_value = output.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
        direct = _json_object_from_text(text_value)
        if direct is not None:
            found.append(direct)
        else:
            try:
                value = json.loads(text_value)
            except json.JSONDecodeError:
                value = None
            _walk_structured_candidates(value, found)
    return found[-1] if found else None


def _api_chat_url(profile: _ApiProfile, model: str) -> str:
    parsed = urlsplit(profile.url)
    path = parsed.path.rstrip("/")
    if not path.casefold().endswith("/models"):
        raise ValueError("API_CHAT_ENDPOINT_INVALID")
    root = path[: -len("/models")]
    if profile.provider_family == "ANTHROPIC":
        chat_path = f"{root}/messages"
    elif profile.provider_family == "GEMINI":
        chat_path = f"{root}/models/{quote(model, safe='._-')}:generateContent"
    else:
        chat_path = f"{root}/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, chat_path, "", ""))


def _api_chat_request(
    profile: _ApiProfile,
    *,
    model: str,
    prompt: str,
    response_schema: Mapping[str, Any],
    max_output_tokens: int | None,
    model_config: Mapping[str, Any],
) -> bytes:
    prompt_text = _structured_prompt(prompt, response_schema).decode("utf-8")
    from .api_request_options import reasoning_profile, resolve_reasoning_options, apply_reasoning_options
    reasoning_control = reasoning_profile(model, profile.url, profile.provider_family)
    reasoning_options = resolve_reasoning_options(reasoning_control, model_config)
    if profile.provider_family == "ANTHROPIC":
        if max_output_tokens is None:
            raise ValueError("API_OUTPUT_CAPACITY_REQUIRED")
        value = {
            "model": model,
            "max_tokens": max_output_tokens,
            "stream": True,
            "messages": [{"role": "user", "content": prompt_text}],
        }
        from .api_request_options import anthropic_options
        if reasoning_options is None:
            value.update(anthropic_options(model, model_config, max_output_tokens))
        if model_config.get("streaming") is True:
            value["stream"] = True
    elif profile.provider_family == "GEMINI":
        generation_config: dict[str, Any] = {
            "responseMimeType": "application/json",
            "responseJsonSchema": response_schema,
        }
        if max_output_tokens is not None:
            generation_config["maxOutputTokens"] = max_output_tokens
        value = {
            "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
            "generationConfig": generation_config,
        }
    else:
        value = {
            "model": model,
            "stream": bool(model_config.get("streaming", _registry_entry(model)[0].get("capabilities", {}).get("streaming", False))),
            "messages": [{"role": "user", "content": prompt_text}],
            "response_format": {"type": "json_object"},
        }
        api_host = (urlsplit(profile.url).hostname or "").casefold()
        if (
            api_host == "dashscope.aliyuncs.com"
            and model.startswith("qwen3.8-")
            and isinstance(model_config.get("thinking"), bool)
        ):
            value["enable_thinking"] = model_config["thinking"]
        if model_config.get("streaming") is True:
            value["stream"] = True
            value["stream_options"] = {"include_usage": True}
        if max_output_tokens is not None:
            output_field = model_config.get(
                "max_output_tokens_wire_field", "max_tokens"
            )
            if reasoning_options is not None:
                output_field = reasoning_control['output_field']
            if output_field not in {"max_tokens", "max_completion_tokens"}:
                raise ValueError("API_MAX_OUTPUT_TOKENS_FIELD_INVALID")
            value[output_field] = max_output_tokens
        if (
            reasoning_options is None
            and model_config.get("thinking") is True
            and model_config.get("reasoning_effort") is None
        ):
            value["thinking"] = {"type": "enabled"}
        reasoning_effort = model_config.get("reasoning_effort")
        if reasoning_options is None and reasoning_effort is not None:
            if reasoning_effort not in {"low", "medium", "high", "xhigh", "max"}:
                raise ValueError("API_REASONING_EFFORT_INVALID")
            value["reasoning_effort"] = reasoning_effort
    if reasoning_options is not None:
        apply_reasoning_options(value, reasoning_options)
        if reasoning_control.get('streaming') and profile.provider_family != 'GEMINI':
            value['stream'] = True
            if profile.provider_family == 'OPENAI_COMPATIBLE':
                value['stream_options'] = {'include_usage': True}
    if model_config.get('prompt_cache_layout')=='MEMO_RESEARCH_PREFIX_V1' and profile.provider_family=='ANTHROPIC' and urlsplit(profile.url).hostname=='api.anthropic.com':
        from model_gateway.research_prompt_cache import prefix_parts
        parts=prefix_parts(prompt_text)
        if parts and len(parts[0])>=2048:
            value['messages'][0]['content']=[{'type':'text','text':parts[0],'cache_control':{'type':'ephemeral','ttl':'5m'}},{'type':'text','text':parts[1]}]
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _api_chat_response(
    profile: _ApiProfile,
    body: bytes,
    model_config: Mapping[str, Any] | None = None,
) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    if (
        profile.provider_family == "ANTHROPIC"
        and body.lstrip().startswith((b"event:", b"data:"))
    ):
        return _anthropic_stream_response(body)
    if (
        profile.provider_family == "OPENAI_COMPATIBLE"
        and body.lstrip().startswith((b"event:", b"data:"))
    ):
        return _openai_compatible_stream_response(body)
    try:
        value = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("STRUCTURED_CHAT_RESPONSE_INVALID") from error
    if not isinstance(value, Mapping):
        raise ValueError("STRUCTURED_CHAT_RESPONSE_INVALID")
    usage = value.get("usage") if isinstance(value.get("usage"), Mapping) else {}
    returned_model = value.get("model") or value.get("modelVersion")
    text_value: Any = None
    finish_reason = None
    if profile.provider_family == "ANTHROPIC":
        finish_reason = value.get('stop_reason')
        content = value.get("content")
        if isinstance(content, list):
            text_value = "".join(
                item.get("text", "")
                for item in content
                if isinstance(item, Mapping) and isinstance(item.get("text"), str)
            )
    elif profile.provider_family == "GEMINI":
        candidates = value.get("candidates")
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], Mapping):
            finish_reason = candidates[0].get('finishReason')
            content = candidates[0].get("content")
            parts = content.get("parts") if isinstance(content, Mapping) else None
            if isinstance(parts, list):
                text_value = "".join(
                    item.get("text", "")
                    for item in parts
                    if isinstance(item, Mapping) and isinstance(item.get("text"), str)
                )
        usage = (
            value.get("usageMetadata")
            if isinstance(value.get("usageMetadata"), Mapping)
            else usage
        )
    else:
        choices = value.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
            finish_reason = choices[0].get("finish_reason")
            message = choices[0].get("message")
            if isinstance(message, Mapping):
                text_value = message.get("content")
    if not isinstance(returned_model, str) or not returned_model:
        raise ValueError("STRUCTURED_CHAT_RETURNED_MODEL_MISSING")
    if finish_reason in {"length", "max_tokens", "MAX_TOKENS"}:
        raise ValueError("STRUCTURED_CHAT_JSON_TRUNCATED")
    if not isinstance(text_value, str) or not text_value.strip():
        raise ValueError("STRUCTURED_CHAT_RESPONSE_EMPTY")
    document = _json_object_from_text(text_value)
    if document is None:
        if profile.provider_family == "OPENAI_COMPATIBLE" and finish_reason == "length":
            raise ValueError("STRUCTURED_CHAT_JSON_TRUNCATED")
        raise ValueError("STRUCTURED_CHAT_RESPONSE_INVALID")
    return returned_model, document, usage


def _anthropic_stream_projection(
    body: bytes,
) -> tuple[str | None, str, Mapping[str, Any], str | None, bool]:
    returned_model: str | None = None
    text_parts: list[str] = []
    usage: dict[str, Any] = {}
    stop_reason: str | None = None
    message_stop_seen = False
    event_count = 0
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line.startswith(b"data:"):
            continue
        payload = line[len(b"data:") :].strip()
        if not payload or payload == b"[DONE]":
            continue
        try:
            event = json.loads(payload.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("STRUCTURED_CHAT_RESPONSE_INVALID") from error
        if not isinstance(event, Mapping):
            raise ValueError("STRUCTURED_CHAT_RESPONSE_INVALID")
        event_count += 1
        event_type = event.get("type")
        if event_type == "error":
            raise ValueError("API_STRUCTURED_CHAT_REQUEST_FAILED")
        if event_type == "message_start":
            message = event.get("message")
            if isinstance(message, Mapping):
                candidate_model = message.get("model")
                if isinstance(candidate_model, str) and candidate_model:
                    returned_model = candidate_model
                initial_usage = message.get("usage")
                if isinstance(initial_usage, Mapping):
                    usage.update(initial_usage)
            continue
        if event_type == "content_block_start":
            content_block = event.get("content_block")
            if (
                isinstance(content_block, Mapping)
                and content_block.get("type") == "text"
                and isinstance(content_block.get("text"), str)
            ):
                text_parts.append(str(content_block["text"]))
            continue
        if event_type == "content_block_delta":
            delta = event.get("delta")
            if (
                isinstance(delta, Mapping)
                and delta.get("type") == "text_delta"
                and isinstance(delta.get("text"), str)
            ):
                text_parts.append(str(delta["text"]))
            continue
        if event_type == "message_delta":
            delta = event.get("delta")
            if isinstance(delta, Mapping) and isinstance(
                delta.get("stop_reason"), str
            ):
                stop_reason = str(delta["stop_reason"])
            final_usage = event.get("usage")
            if isinstance(final_usage, Mapping):
                usage.update(final_usage)
            continue
        if event_type == "message_stop":
            message_stop_seen = True
    if event_count == 0:
        raise ValueError("STRUCTURED_CHAT_RESPONSE_INVALID")
    return returned_model, "".join(text_parts), usage, stop_reason, message_stop_seen


def _anthropic_stream_response(
    body: bytes,
) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    returned_model, text_value, usage, stop_reason, message_stop_seen = (
        _anthropic_stream_projection(body)
    )
    if not isinstance(returned_model, str) or not returned_model:
        raise ValueError("STRUCTURED_CHAT_RETURNED_MODEL_MISSING")
    if not message_stop_seen:
        raise ValueError("STRUCTURED_CHAT_RESPONSE_INVALID")
    if stop_reason in {"length", "max_tokens"}:
        raise ValueError("STRUCTURED_CHAT_JSON_TRUNCATED")
    if not text_value.strip():
        raise ValueError("STRUCTURED_CHAT_RESPONSE_EMPTY")
    document = _json_object_from_text(text_value)
    if document is None:
        if stop_reason == "max_tokens":
            raise ValueError("STRUCTURED_CHAT_JSON_TRUNCATED")
        raise ValueError("STRUCTURED_CHAT_RESPONSE_INVALID")
    return returned_model, document, usage


def _openai_compatible_stream_response(
    body: bytes,
) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    returned_model: str | None = None
    text_parts: list[str] = []
    usage: Mapping[str, Any] = {}
    finish_reason: str | None = None
    event_count = 0
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(b":"):
            continue
        if not line.startswith(b"data:"):
            continue
        payload = line[len(b"data:") :].strip()
        if payload == b"[DONE]":
            continue
        try:
            value = json.loads(payload.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("STRUCTURED_CHAT_STREAM_RESPONSE_INVALID") from error
        if not isinstance(value, Mapping):
            raise ValueError("STRUCTURED_CHAT_STREAM_RESPONSE_INVALID")
        event_count += 1
        model_value = value.get("model")
        if isinstance(model_value, str) and model_value:
            if returned_model is not None and returned_model != model_value:
                raise ValueError("STRUCTURED_CHAT_RETURNED_MODEL_MISMATCH")
            returned_model = model_value
        if isinstance(value.get("usage"), Mapping):
            usage = value["usage"]
        choices = value.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        first = choices[0]
        if not isinstance(first, Mapping):
            continue
        if isinstance(first.get("finish_reason"), str):
            finish_reason = str(first["finish_reason"])
        delta = first.get("delta")
        if isinstance(delta, Mapping) and isinstance(delta.get("content"), str):
            text_parts.append(str(delta["content"]))
        message = first.get("message")
        if isinstance(message, Mapping) and isinstance(message.get("content"), str):
            text_parts.append(str(message["content"]))
    if event_count == 0:
        raise ValueError("STRUCTURED_CHAT_STREAM_RESPONSE_INVALID")
    if returned_model is None:
        raise ValueError("STRUCTURED_CHAT_RETURNED_MODEL_MISSING")
    text_value = "".join(text_parts)
    if not text_value.strip():
        raise ValueError("STRUCTURED_CHAT_RESPONSE_EMPTY")
    document = _json_object_from_text(text_value)
    if document is None:
        if finish_reason == "length":
            raise ValueError("STRUCTURED_CHAT_JSON_TRUNCATED")
        raise ValueError("STRUCTURED_CHAT_RESPONSE_INVALID")
    return returned_model, document, usage


def _api_failure_evidence(
    body: bytes,
) -> tuple[str | None, Mapping[str, Any], Mapping[str, Any]]:
    body_sha256 = hashlib.sha256(body).hexdigest().upper()
    if b"data:" in body:
        try:
            returned, text, usage, stop_reason, message_stop_seen = (
                _anthropic_stream_projection(body)
            )
        except ValueError:
            returned, text, usage, stop_reason, message_stop_seen = (
                None,
                "",
                {},
                None,
                False,
            )
        diagnostic: dict[str, Any] = {
            "provider_body_sha256": body_sha256,
            "provider_body_json": False,
            "provider_body_sse": True,
            "message_stop_seen": message_stop_seen,
        }
        if isinstance(stop_reason, str) and len(stop_reason) <= 80:
            diagnostic["finish_reason"] = stop_reason
        if text:
            diagnostic["content_chars"] = len(text)
            diagnostic["content_sha256"] = hashlib.sha256(
                text.encode("utf-8")
            ).hexdigest().upper()
        return returned, usage, diagnostic
    try:
        value = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, {}, {
            "provider_body_sha256": body_sha256,
            "provider_body_json": False,
        }
    if not isinstance(value, Mapping):
        return None, {}, {
            "provider_body_sha256": body_sha256,
            "provider_body_json": True,
        }
    returned = value.get("model") or value.get("modelVersion")
    if not isinstance(returned, str) or not SAFE_MODEL.fullmatch(returned):
        returned = None
    usage = value.get("usage")
    if not isinstance(usage, Mapping):
        usage = value.get("usageMetadata")
    diagnostic: dict[str, Any] = {
        "provider_body_sha256": body_sha256,
        "provider_body_json": True,
    }
    from .api_request_options import safe_error_details
    diagnostic.update(safe_error_details(value))
    stop_reason = value.get('stop_reason')
    candidates = value.get('candidates')
    if isinstance(candidates, list) and candidates and isinstance(candidates[0], Mapping):
        stop_reason = candidates[0].get('finishReason')
    if stop_reason in {'max_tokens', 'MAX_TOKENS'}:
        diagnostic['finish_reason'] = 'length'
        diagnostic['provider_stop_reason'] = stop_reason
    choices = value.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        finish_reason = choices[0].get("finish_reason")
        if isinstance(finish_reason, str) and len(finish_reason) <= 80:
            diagnostic["finish_reason"] = finish_reason
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, Mapping) else None
        if isinstance(content, str):
            diagnostic["content_chars"] = len(content)
            diagnostic["content_sha256"] = hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest().upper()
    return returned, usage if isinstance(usage, Mapping) else {}, diagnostic


def _cli_failure_diagnostic(process: ProcessResult) -> Mapping[str, Any]:
    text = process.stderr.decode("utf-8", errors="replace").casefold()
    stdout_text = process.stdout.decode("utf-8", errors="replace").casefold()
    combined = text + "\n" + stdout_text
    capacity = {}
    if "input_too_large" in combined:
        for field in ("max_chars", "actual_chars"):
            match = re.search(r'"' + field + r'"\s*:\s*(\d{1,12})\b', combined)
            if match:
                capacity[field] = int(match.group(1))
        capacity["source"] = "CLI_ERROR_RESPONSE"
    if capacity:
        category = "CODEX_INPUT_CAPACITY_EXCEEDED"
    elif "invalid_json_schema" in combined or "invalid schema for response_format" in combined:
        category = "CODEX_OUTPUT_SCHEMA_REJECTED"
    elif "request timed out" in combined:
        category = "CODEX_TRANSPORT_TIMEOUT"
    elif "model provider" in text and ("base url" in text or "base_url" in text):
        category = "CODEX_CUSTOM_PROVIDER_BASE_URL_MISSING"
    elif "config" in text and any(
        marker in text for marker in ("unknown", "unrecognized", "invalid")
    ):
        category = "CODEX_CONFIG_INVALID"
    elif any(marker in text for marker in ("not logged in", "login required", "unauthorized")):
        category = "CODEX_AUTH_UNAVAILABLE"
    elif "model" in text and any(
        marker in text for marker in ("not found", "not available", "unsupported")
    ):
        category = "CODEX_MODEL_UNAVAILABLE"
    else:
        category = "CODEX_PROCESS_NONZERO"
    return {
        "category": category,
        "observed_input_capacity": capacity or None,
        "returncode": process.returncode,
        "stdout_sha256": hashlib.sha256(process.stdout).hexdigest().upper(),
        "stderr_sha256": hashlib.sha256(process.stderr).hexdigest().upper(),
        "stdout_total_bytes": process.stdout_total_bytes,
        "stderr_total_bytes": process.stderr_total_bytes,
        "capture_quota_bytes": process.capture_quota_bytes,
        "quota_source": (
            "EXPLICIT_RESOURCE_PROFILE"
            if process.capture_quota_bytes is not None
            else "DISK_SPOOL_NO_GENERIC_PRODUCT_LIMIT"
        ),
    }


def _execution_codex_event_diagnostic(
    output: bytes, requested_model: str
) -> tuple[Mapping[str, Any] | None, str | None]:
    """Reuse the frozen Execution JSONL parser without adopting its disabled route."""

    if _ExecutionCodexCliAdapter is None or _ExecutionCodexAdapterViolation is None:
        return None, "Execution_CODEX_PARSER_UNAVAILABLE"
    try:
        adapter = object.__new__(_ExecutionCodexCliAdapter)
        return (
            adapter.parse_event_stream(
                output,
                requested_model=requested_model,
                invocation=None,
            ),
            None,
        )
    except Exception as error:
        if isinstance(error, _ExecutionCodexAdapterViolation):
            return None, str(error).split(":", 1)[0]
        raise


def _codex_agent_message_from_events(output: bytes) -> str:
    """Extract the sole final agent message after the Execution parser validates JSONL."""

    messages: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("CODEX_JSONL_INVALID") from error
        if not isinstance(event, Mapping) or event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, Mapping) or item.get("type") != "agent_message":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            messages.append(text)
    if len(messages) != 1:
        raise ValueError("CODEX_OCR_AGENT_MESSAGE_NOT_UNIQUE")
    return messages[0]


def _resolved_file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _default_executable_resolver(value: str) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 260:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    candidate = Path(value)
    if candidate.is_absolute():
        if os.name == "nt" and candidate.name.lower() in {"codex.cmd", "codex.bat"}:
            from .windows_cli import native_codex_from_shim
            return native_codex_from_shim(candidate)
        return str(candidate.resolve()) if candidate.is_file() else None
    if any(separator in value for separator in ("/", "\\")):
        return None
    if os.name == "nt":
        from .windows_cli import resolve_supported_cli_executable
        supported_cli = resolve_supported_cli_executable(value)
        if supported_cli:
            return supported_cli
    located = shutil.which(value)
    if os.name == "nt" and located and Path(located).name.lower() in {"codex.cmd", "codex.bat"}:
        from .windows_cli import native_codex_from_shim
        return native_codex_from_shim(located)
    return str(Path(located).resolve()) if located else None


class LiveModelValidationRunner(_EmbeddingExecutionMixin, _SpecializedModelExecutionMixin):
    def _preserve_capacity_response(self, body: bytes, *, partial: bool = False) -> dict[str, Any]:
        """Retain visible output on failure without copying credentials/thinking."""
        visible: Any = body.decode('utf-8', errors='replace')
        if not partial:
            try:
                value = json.loads(visible)
                choices = value.get('choices') or []
                if choices:
                    visible = choices[0].get('message', {}).get('content')
                elif isinstance(value.get('message'), Mapping):
                    visible = value['message'].get('content')
                elif isinstance(value.get('content'), list):
                    visible = ''.join(row.get('text', '') for row in value['content']
                                      if isinstance(row, Mapping) and row.get('type') == 'text')
                else:
                    candidates = value.get('candidates') or []
                    visible = ''.join(part.get('text', '') for row in candidates
                                      for part in row.get('content', {}).get('parts', [])
                                      if isinstance(part, Mapping) and not part.get('thought'))
            except (ValueError, AttributeError, TypeError):
                visible = None
        data = (visible if isinstance(visible, str) else '').encode('utf-8')
        self._scratch_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix='response-evidence-', suffix='.txt',
                dir=self._scratch_root, delete=False) as stream:
            stream.write(data)
            path = Path(stream.name)
        return {'visible_response_path': str(path), 'visible_response_bytes': len(data),
                'visible_response_sha256': hashlib.sha256(data).hexdigest().upper(),
                'received_body_complete': not partial, 'visible_response_complete': False,
                'reasoning_content_recorded': False}
    """Real click-only API catalog and CLI minimal-request validator."""

    def __init__(
        self,
        *,
        credential_resolver: Callable[[str], bytes],
        preferences_loader: Callable[[], Mapping[str, Any]],
        scratch_root: Path,
        http_transport: HttpTransport | None = None,
        process_transport: ProcessTransport | None = None,
        executable_resolver: Callable[[str], str | None] = _default_executable_resolver,
        workflow_exam_catalog: Mapping[str, Any] | None = None,
        workflow_exam_executors: Sequence[Any] = (),
        local_profile_resolver: Callable[[str], Mapping[str, Any] | None]
        | None = None,
    ):
        self._credential_resolver = credential_resolver
        self._preferences_loader = preferences_loader
        self._scratch_root = Path(scratch_root)
        self._exam_score_history = ExamScoreHistory(self._scratch_root / "workflow_exam_scores")
        self._call_ledger = CallLedger(self._scratch_root / "model_call_ledger")
        self._http = RecordingTransport(http_transport or UrllibHttpTransport())
        self._process = RecordingTransport(process_transport or BoundedSubprocessTransport())
        self._executable_resolver = executable_resolver
        (
            self._workflow_exam_entries,
            self._workflow_exam_historical_entries,
        ) = self._validate_workflow_exam_catalog(workflow_exam_catalog)
        self._workflow_exam_executors = tuple(workflow_exam_executors)
        self._local_profile_resolver = local_profile_resolver
        if any(
            not callable(getattr(executor, "plan", None))
            or not callable(getattr(executor, "execute", None))
            for executor in self._workflow_exam_executors
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_EXECUTOR_INVALID")

    def _resolve_exam_target(self, target: Mapping[str, Any]) -> dict[str, Any]:
        accepted = dict(target)
        if accepted.get("kind") != "BUILTIN_PROFILE":
            return accepted
        profile_ref = accepted.get("profile_ref")
        if (
            not isinstance(profile_ref, str)
            or not profile_ref.startswith("local:")
            or self._local_profile_resolver is None
        ):
            return accepted
        try:
            projection = self._local_profile_resolver(profile_ref)
        except Exception:
            return accepted
        if not isinstance(projection, Mapping):
            return accepted
        row = dict(projection)
        if (
            row.get("profile_ref") != profile_ref
            or row.get("endpoint_kind") != "ollama"
            or row.get("structured_chat_adapter")
            != "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1"
            or row.get("execution_eligible") is not True
            or row.get("exact_identity_available") is not True
            or not isinstance(row.get("chat_endpoint"), str)
            or not isinstance(row.get("model_name"), str)
            or not SAFE_MODEL.fullmatch(str(row["model_name"]))
            or not isinstance(row.get("model_digest"), str)
            or not SAFE_SHA256.fullmatch(str(row["model_digest"]).upper())
        ):
            return accepted
        return {
            **row,
            "kind": "LOCAL",
            "config_id": profile_ref,
            "profile_ref": profile_ref,
            "model_digest": str(row["model_digest"]).upper(),
            "connection_status": "AVAILABLE",
        }

    def _resolve_ocr_exam_target(self, node, target):
        accepted = self._resolve_exam_target(target)
        if (node.get('node_id') != 'ingest' or accepted.get('kind') != 'CLI'
                or accepted.get('adapter_id') != 'codex_cli'
                or accepted.get('connection_status') != 'AVAILABLE'):
            return accepted
        from .cli_image_identity import bind_saved_cli_identity, FIELDS
        if any(key in accepted for key in FIELDS):
            return accepted
        executable = self._executable_resolver(accepted.get('executable'))
        if executable is None:
            return accepted
        proxy_mode, proxy_address, _ = self._preferences()
        self._scratch_root.mkdir(parents=True, exist_ok=True)
        accepted, _ = bind_saved_cli_identity(accepted, executable,
            process=self._process,
            environment=self._safe_environment('codex_cli', self._scratch_root,
                                              proxy_mode, proxy_address),
            root=self._scratch_root)
        return accepted

    @staticmethod
    def _validate_workflow_exam_catalog(
        value: Mapping[str, Any] | None,
    ) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
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
            expected_catalog_sha256 = hashlib.sha256(
                json.dumps(
                    {
                        key: item
                        for key, item in value.items()
                        if key != "catalog_sha256"
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest().upper()
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
                expected_historical_hash = hashlib.sha256(
                    json.dumps(
                        {
                            key: item
                            for key, item in historical_row.items()
                            if key != "historical_record_sha256"
                        },
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest().upper()
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
            if schema_version in {
                "WorkflowModelExamScoreCatalog-v4",
                "WorkflowModelExamScoreCatalog-v5",
                "WorkflowModelExamScoreCatalog-v6",
                "WorkflowModelExamScoreCatalog-v7",
            }:
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
                if schema_version in {
                    "WorkflowModelExamScoreCatalog-v5",
                    "WorkflowModelExamScoreCatalog-v6",
                    "WorkflowModelExamScoreCatalog-v7",
                }:
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
                if schema_version in {
                    "WorkflowModelExamScoreCatalog-v6",
                    "WorkflowModelExamScoreCatalog-v7",
                }:
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
                successor_series = False
                if schema_version == "WorkflowModelExamScoreCatalog-v7":
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
                    else:
                        successor_series = True
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
                        slot_numbers: list[int] = []
                        sample_hashes: list[str] = []
                        slot_scores: list[float] = []
                        slot_safety = 0
                        slot_calibration = 0
                        slot_failed = 0
                        slot_semantic_rounds = 0
                        slot_core_scores: list[float] = []
                        slot_exact_rates: list[float] = []
                        slot_deficit_rates: list[float] = []
                        slot_collision_counts = 0
                        slot_adjusted_rates: list[float] = []
                        all_slot_pass = True
                        all_slot_qualification_pass = True
                        expected_operational = "SUITABLE"
                        expected_closure = "DELTA_PASS"
                        for slot in series_slots:
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
                            slot_numbers.append(slot_number)
                            sample_hashes.append(
                                str(slot["sample_manifest_sha256"])
                            )
                            slot_scores.append(float(slot_score))
                            slot_safety += int(
                                slot["safety_blocking_residual_count"]
                            )
                            slot_calibration += int(
                                slot["calibration_residual_count"]
                            )
                            slot_failed += int(slot["failed_exam_item_count"])
                            slot_semantic_rounds += int(
                                slot["semantic_repair_rounds"]
                            )
                            slot_core_scores.append(
                                float(slot_core_score)
                            )
                            slot_exact_rates.append(
                                float(slot["exact_case_repair_rate"])
                            )
                            slot_deficit_rates.append(
                                float(
                                    slot[
                                        "case_score_deficit_recovery_rate"
                                    ]
                                )
                            )
                            slot_collision_counts += int(
                                slot[
                                    "repair_metadata_collision_suspect_count"
                                ]
                            )
                            slot_adjusted_rates.append(
                                float(
                                    slot[
                                        "collision_adjusted_repair_diagnostic_rate"
                                    ]
                                )
                            )
                            all_slot_pass &= slot["verdict"] == "PASS"
                            all_slot_qualification_pass &= (
                                slot["qualification_verdict"] == "PASS"
                            )
                            if slot["operational_eligibility_verdict"] in {
                                "NOT_RECOMMENDED",
                                "REJECT",
                            }:
                                expected_operational = "NOT_RECOMMENDED"
                            elif (
                                expected_operational != "NOT_RECOMMENDED"
                                and slot[
                                    "operational_eligibility_verdict"
                                ] == "SUITABLE_WITH_GOVERNED_DISPOSITION"
                            ):
                                expected_operational = (
                                    "SUITABLE_WITH_GOVERNED_DISPOSITION"
                                )
                            elif (
                                expected_operational == "SUITABLE"
                                and slot["operational_eligibility_verdict"]
                                == "SUITABLE_WITH_CALIBRATION"
                            ):
                                expected_operational = "SUITABLE_WITH_CALIBRATION"
                            if slot["core_closure_verdict"] == "HUMAN_REQUIRED":
                                expected_closure = "HUMAN_REQUIRED"
                            elif (
                                expected_closure != "HUMAN_REQUIRED"
                                and slot["core_closure_verdict"]
                                == "PASS_WITH_GOVERNED_DISPOSITION"
                            ):
                                expected_closure = (
                                    "PASS_WITH_GOVERNED_DISPOSITION"
                                )
                            elif (
                                expected_closure == "DELTA_PASS"
                                and slot["core_closure_verdict"]
                                == "PASS_WITH_CALIBRATION"
                            ):
                                expected_closure = "PASS_WITH_CALIBRATION"
                        mean = lambda values: round(sum(values) / len(values), 9)
                        if (
                            sorted(slot_numbers) != [1, 2, 3]
                            or len(set(sample_hashes)) != 3
                            or abs(float(row["score_exact"]) - mean(slot_scores))
                            > 0.000001
                            or row["score"]
                            != int(round(float(row["score_exact"])))
                            or row["verdict"]
                            != ("PASS" if all_slot_pass else "FAIL")
                            or row["qualification_verdict"]
                            != (
                                "PASS"
                                if all_slot_qualification_pass
                                else "DISQUALIFIED"
                            )
                            or row["operational_eligibility_verdict"]
                            != expected_operational
                            or row["operational_safety_gate"]
                            != ("PASS" if slot_safety == 0 else "FAIL")
                            or row["safety_blocking_residual_count"]
                            != slot_safety
                            or row["calibration_residual_count"]
                            != slot_calibration
                            or row["failed_exam_item_count"] != slot_failed
                            or row["semantic_repair_rounds"]
                            != slot_semantic_rounds
                            or row["core_closure_verdict"]
                            != expected_closure
                            or abs(
                                float(row["core_closure_score"])
                                - mean(slot_core_scores)
                            )
                            > 0.000001
                            or abs(
                                float(row["exact_case_repair_rate"])
                                - mean(slot_exact_rates)
                            )
                            > 0.000001
                            or abs(
                                float(
                                    row[
                                        "case_score_deficit_recovery_rate"
                                    ]
                                )
                                - mean(slot_deficit_rates)
                            )
                            > 0.000001
                            or row[
                                "repair_metadata_collision_suspect_count"
                            ]
                            != slot_collision_counts
                            or abs(
                                float(
                                    row[
                                        "collision_adjusted_repair_diagnostic_rate"
                                    ]
                                )
                                - mean(slot_adjusted_rates)
                            )
                            > 0.000001
                        ):
                            raise ValueError(
                                "WORKFLOW_MODEL_EXAM_CATALOG_SERIES_AGGREGATE_INVALID"
                            )
                    cache_binding = row.get("cache_binding_sha256")
                    expected_cache_binding = hashlib.sha256(
                        json.dumps(
                            {
                                key: item
                                for key, item in row.items()
                                if key != "cache_binding_sha256"
                            },
                            ensure_ascii=False,
                            allow_nan=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest().upper()
                    if (
                        not isinstance(cache_binding, str)
                        or cache_binding != expected_cache_binding
                    ):
                        raise ValueError(
                            "WORKFLOW_MODEL_EXAM_CATALOG_CACHE_BINDING_INVALID"
                        )
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
                    expected_binding = hashlib.sha256(
                        json.dumps(
                            binding,
                            ensure_ascii=False,
                            allow_nan=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest().upper()
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
            if identity in identities:
                raise ValueError("WORKFLOW_MODEL_EXAM_CATALOG_DUPLICATE")
            identities.add(identity)
            accepted.append(row)
        return tuple(accepted), historical_entries

    @staticmethod
    def _base(kind: str, *, status: str, reason: str) -> dict[str, Any]:
        return {
            "schema_version": "SettingsModelValidationResult-v1",
            "kind": kind,
            "status": status,
            "score": None,
            "reason": reason,
            "requested_model": None,
            "returned_model": None,
            "duration_ms": 0,
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
            "external_process_launches": 0,
        }

    def _preferences(self) -> tuple[str, str, int]:
        value = self._preferences_loader()
        proxy_mode = value.get("proxy_mode", "SYSTEM")
        proxy_address = value.get("proxy_address", "")
        timeout = value.get("request_timeout_seconds", 60)
        if proxy_mode not in {"SYSTEM", "NONE", "CUSTOM"}:
            proxy_mode = "SYSTEM"
        if not isinstance(proxy_address, str):
            proxy_address = ""
        if isinstance(timeout, bool) or not isinstance(timeout, int):
            timeout = 60
        timeout = min(300, max(5, timeout))
        return str(proxy_mode), proxy_address, timeout

    @scheduled('MODEL_VERIFICATION')
    @recorded_execution
    def verify_api_model(self, service: Mapping[str, Any]) -> Mapping[str, Any]:
        provider = service.get("provider")
        if not isinstance(provider, str):
            return self._base("API", status="NOT_RUN", reason="API_PROVIDER_UNSUPPORTED")
        try:
            profile = _api_profile(service)
        except ValueError:
            return self._base("API", status="NOT_RUN", reason="API_ENDPOINT_INVALID")
        if profile is None:
            return self._base("API", status="NOT_RUN", reason="API_PROVIDER_UNSUPPORTED")
        requested_model = service.get("model_name")
        if not isinstance(requested_model, str) or not requested_model:
            return self._base("API", status="NOT_RUN", reason="API_MODEL_NOT_CONFIGURED")
        result = self._base("API", status="INVALID", reason="API_VALIDATION_FAILED")
        result["requested_model"] = requested_model
        result["model_capability"] = infer_api_model_capability(
            provider, requested_model
        )
        result["validation_endpoint_kind"] = "MODEL_CATALOG"
        credential_ref = service.get("credential_ref")
        if not isinstance(credential_ref, str):
            result["reason"] = "API_CREDENTIAL_UNAVAILABLE"
            return result
        try:
            secret_bytes = self._credential_resolver(credential_ref)
            if not isinstance(secret_bytes, bytes) or not secret_bytes or len(secret_bytes) > 512:
                raise ValueError("API_CREDENTIAL_INVALID")
            secret = secret_bytes.decode("utf-8", errors="strict")
        except (OSError, PermissionError, UnicodeDecodeError, ValueError):
            result["reason"] = "API_CREDENTIAL_UNAVAILABLE"
            return result
        headers = {
            "Accept": "application/json",
            "User-Agent": "Memorive-Model-Validation/1",
            **dict(profile.extra_headers),
        }
        headers[profile.auth_header] = (
            f"Bearer {secret}" if profile.auth_header == "Authorization" else secret
        )
        proxy_mode, proxy_address, timeout = self._preferences()
        started_at = time.monotonic()
        next_url = profile.url
        visited_urls: set[str] = set()
        available_models: list[str] = []
        try:
            for _page_number in range(MAX_CATALOG_PAGES):
                if next_url in visited_urls:
                    result["reason"] = "API_CATALOG_RESPONSE_INVALID"
                    return result
                visited_urls.add(next_url)
                result["external_network_calls"] += 1
                result["provider_calls"] += 1
                try:
                    response = self._http.request(
                        method="GET",
                        url=next_url,
                        headers=headers,
                        body=None,
                        timeout_seconds=timeout,
                        proxy_mode=proxy_mode,
                        proxy_address=proxy_address,
                    )
                except (OSError, TimeoutError, ValueError) as error:
                    from .network_compat import error_code
                    result["reason"] = error_code(error)
                    return result
                if response.status_code in {401, 403}:
                    result["reason"] = "API_AUTHENTICATION_REJECTED"
                    return result
                if response.status_code == 429:
                    result["reason"] = "API_RATE_LIMITED"
                    return result
                if response.status_code != 200:
                    result["reason"] = "API_CATALOG_REQUEST_FAILED"
                    return result
                try:
                    document = _catalog_document(response.body)
                    catalog = _catalog_model_ids(profile, document)
                except ValueError:
                    result["reason"] = "API_CATALOG_RESPONSE_INVALID"
                    return result
                available_models = _catalog_choices([*available_models, *catalog])
                matched = _catalog_match(profile, requested_model, catalog)
                from .provider_catalog_aliases import catalog_alias
                alias = catalog_alias(service, requested_model, catalog) if matched is None else None
                if alias is not None:
                    result.update(status="AVAILABLE", reason="API_MODEL_CATALOG_MATCH",
                                  returned_model=alias['canonical_model'], alias_evidence=alias)
                    return result
                if matched is not None:
                    result.update(
                        status="AVAILABLE",
                        reason="API_MODEL_CATALOG_MATCH",
                        returned_model=matched,
                    )
                    return result
                try:
                    following_url = _next_catalog_url(profile, document)
                except ValueError:
                    result["reason"] = "API_CATALOG_RESPONSE_INVALID"
                    return result
                if following_url is None:
                    result["reason"] = "API_MODEL_NOT_LISTED"
                    result["available_models"] = available_models
                    result["suggested_model"] = _catalog_suggestion(
                        requested_model, available_models
                    )
                    return result
                next_url = following_url
            result["reason"] = "API_CATALOG_PAGINATION_LIMIT"
            return result
        finally:
            result["duration_ms"] = max(
                0, round((time.monotonic() - started_at) * 1000)
            )
            secret = ""
            secret_bytes = b""
            headers.clear()

    @staticmethod
    def _safe_environment(
        adapter_id: str,
        cwd: Path,
        proxy_mode: str,
        proxy_address: str,
    ) -> dict[str, str]:
        names = {
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "USERPROFILE",
            "HOME",
            "APPDATA",
            "LOCALAPPDATA",
            "PROGRAMDATA",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
        }
        adapter_names = {
            "codex_cli": {"CODEX_HOME"},
            "claude_code": {
                "CLAUDE_CONFIG_DIR",
                "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
                "CLAUDE_CODE_ENTRYPOINT",
            },
            "gemini_cli": {"GEMINI_CLI_HOME"},
            "qwen_code": {"QWEN_HOME"},
            "kimi_code": {"KIMI_CODE_HOME"},
            "codebuddy_code": set(),
            "github_copilot_cli": set(),
        }
        names.update(adapter_names.get(adapter_id, set()))
        if proxy_mode == "SYSTEM":
            names.update({"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"})
        environment = {
            name: value
            for name in names
            if isinstance((value := os.environ.get(name)), str)
            and value
            and "\x00" not in value
        }
        environment["TEMP"] = str(cwd)
        environment["TMP"] = str(cwd)
        if proxy_mode == "CUSTOM" and proxy_address:
            environment["HTTP_PROXY"] = proxy_address
            environment["HTTPS_PROXY"] = proxy_address
        if adapter_id == "qwen_code":
            environment["QWEN_RUNTIME_DIR"] = str(cwd / "qwen-runtime")
        elif adapter_id == "codebuddy_code":
            environment["CODEBUDDY_RUNTIME_DIR"] = str(cwd / "codebuddy-runtime")
        elif adapter_id == "github_copilot_cli":
            environment["COPILOT_HOME"] = str(cwd / "copilot-home")
            environment["COPILOT_CACHE_HOME"] = str(cwd / "copilot-cache")
        return environment

    @scheduled('MODEL_VERIFICATION')
    @recorded_execution
    def verify_cli_model(
        self,
        cli_service: Mapping[str, Any],
        model: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        requested_model = model.get("model_name")
        result = self._base("CLI", status="INVALID", reason="CLI_VALIDATION_FAILED")
        if isinstance(requested_model, str):
            result["requested_model"] = requested_model
        adapter_id = cli_service.get("adapter_id")
        if not isinstance(adapter_id, str):
            result.update(status="NOT_RUN", reason="CLI_ADAPTER_UNSUPPORTED")
            return result
        executable_value = cli_service.get("executable")
        if not isinstance(executable_value, str):
            result["reason"] = "CLI_EXECUTABLE_NOT_FOUND"
            return result
        executable = self._executable_resolver(executable_value)
        if executable is None:
            result["reason"] = "CLI_EXECUTABLE_NOT_FOUND"
            return result
        # Bind the successful verification to the exact executable selected by
        # the same resolver used by workflow execution.  The controller writes
        # this absolute path back only after the frozen target check passes.
        result["resolved_executable"] = executable
        if not isinstance(requested_model, str) or not SAFE_MODEL.fullmatch(requested_model):
            result.update(status="NOT_RUN", reason="CLI_MODEL_NOT_CONFIGURED")
            return result
        thinking_mode = model.get("thinking_mode", "")
        if not isinstance(thinking_mode, str):
            thinking_mode = ""
        proxy_mode, proxy_address, timeout = self._preferences()
        self._scratch_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="memorive-model-validation-", dir=self._scratch_root
        ) as temporary:
            workspace = Path(temporary).resolve()
            try:
                argv, stdin_bytes = _cli_argv(
                    adapter_id,
                    executable,
                    requested_model,
                    thinking_mode,
                    str(workspace),
                )
            except ValueError as error:
                if str(error) == "CLI_ADAPTER_UNSUPPORTED":
                    result.update(status="NOT_RUN", reason="CLI_ADAPTER_UNSUPPORTED")
                else:
                    result.update(status="NOT_RUN", reason="CLI_MODEL_CONFIGURATION_INVALID")
                return result
            environment = self._safe_environment(
                adapter_id, workspace, proxy_mode, proxy_address
            )
            options = dict(argv=argv, cwd=str(workspace), environment=environment,
                           stdin_bytes=stdin_bytes, timeout_seconds=timeout, shell=False)
            # Recording and cancellation must use the same transport path.
            process, diagnostic = self._process.run_verification(**options)
            result['cli_diagnostic'] = diagnostic
        result["duration_ms"] = process.duration_ms
        if not process.started:
            result["reason"] = "CLI_VERIFICATION_CANCELLED" if diagnostic.get('cancelled') else "CLI_PROCESS_START_FAILED"
            if diagnostic.get('cancelled'):
                result["status"] = "NOT_RUN"
            return result
        result["external_process_launches"] = 1
        result["external_network_calls"] = 1
        result["provider_calls"] = 1
        result["external_model_calls"] = 1
        if diagnostic.get('cancelled'):
            result.update(status='NOT_RUN', reason='CLI_VERIFICATION_CANCELLED')
            return result
        if process.timed_out:
            result["reason"] = "CLI_VALIDATION_TIMEOUT"
            return result
        if process.output_truncated:
            result["reason"] = "CLI_OUTPUT_LIMIT_EXCEEDED"
            return result
        from .cli_verification_transport import result_reason
        failure = result_reason(process)
        if failure is not None:
            result["reason"] = failure
            return result
        reported = _reported_models(process.stdout)
        if reported and requested_model not in reported:
            result["reason"] = "CLI_RETURNED_MODEL_MISMATCH"
            return result
        result.update(
            status="AVAILABLE",
            reason=(
                "CLI_MINIMAL_REQUEST_COMPLETED"
                if reported
                else "CLI_MINIMAL_REQUEST_COMPLETED_MODEL_NOT_EXPOSED"
            ),
            returned_model=requested_model if reported else None,
        )
        result['token_usage'] = _normalise_token_usage(_usage_from_cli_output(process.stdout))
        return result

    @recorded_execution
    def execute_structured_chat(
        self,
        *,
        profile_kind: str,
        service: Mapping[str, Any],
        model: Mapping[str, Any],
        prompt: str,
        response_schema: Mapping[str, Any],
        purpose: str,
        max_output_tokens: int | None = None,
        timeout_seconds: int | None = None,
    ) -> Mapping[str, Any]:
        if profile_kind not in {"API", "CLI", "LOCAL"}:
            raise ValueError("STRUCTURED_CHAT_PROFILE_KIND_INVALID")
        requested_model = model.get("model_name")
        if (
            not isinstance(requested_model, str)
            or not SAFE_MODEL.fullmatch(requested_model)
            or not isinstance(prompt, str)
            or not prompt.strip()
            or not isinstance(response_schema, Mapping)
            or purpose
            not in {
                "conversation_refinement",
                "workflow_model_exam",
                "core_document_processing",
                "report_daily", "report_weekly", "report_monthly", "research_chat",
            }
        ):
            raise ValueError("STRUCTURED_CHAT_REQUEST_INVALID")
        output_limit_mode = model.get("max_output_tokens_mode")
        if output_limit_mode not in {None, "EXPLICIT", "PROVIDER_DEFAULT"}:
            raise ValueError("STRUCTURED_CHAT_MAX_OUTPUT_TOKENS_MODE_INVALID")
        if max_output_tokens is not None and (
            isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or max_output_tokens < 1
        ):
            raise ValueError("STRUCTURED_CHAT_MAX_OUTPUT_TOKENS_INVALID")
        limits = resolve_call_limits(profile_kind=profile_kind, service=service, model=model,
                                     requested_output_tokens=max_output_tokens)
        effective_max_output_tokens = limits['max_output_tokens']
        if timeout_seconds is None:
            timeout_seconds = limits['timeout_seconds']
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or timeout_seconds <= 0
        ):
            raise ValueError("STRUCTURED_CHAT_TIMEOUT_INVALID")
        prompt_bytes = _structured_prompt(prompt, response_schema)
        behavior_hash = hashlib.sha256(prompt_bytes).hexdigest().upper()
        started_at = time.monotonic()

        def failed_attempt(
            reason: str,
            *,
            returned_model: str | None,
            raw_usage: Mapping[str, Any] | None,
            external_network_calls: int,
            provider_calls: int,
            external_model_calls: int,
            external_process_launches: int,
            route: str,
            region: str,
            egress: str,
            transport_diagnostic: Mapping[str, Any] | None = None,
        ) -> Mapping[str, Any]:
            token_usage = _normalise_token_usage(raw_usage or {})
            estimated_cost_cny = (
                _api_estimated_cost_cny(
                    requested_model, model, token_usage
                )
                if profile_kind == "API"
                else (0.0 if profile_kind == "LOCAL" else None)
            )
            if profile_kind == "CLI":
                cost_evidence = "SUBSCRIPTION_CLI_NO_PER_CALL_PRICE"
            elif profile_kind == "LOCAL":
                cost_evidence = "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
            elif estimated_cost_cny is not None:
                cost_evidence = (
                    "FROZEN_TARGET_PRICING_PROFILE_ESTIMATE"
                    if model.get("pricing_evidence_ref")
                    else "FROZEN_Quality_PUBLIC_PRICING_PROFILE_2026-07-30_ESTIMATE"
                )
            else:
                cost_evidence = "ATTEMPT_COST_EVIDENCE_UNAVAILABLE"
            duration_ms = max(0, round((time.monotonic() - started_at) * 1000))
            execution_receipt = {
                "schema_version": "SettingsStructuredChatExecutionReceipt-v2",
                "status": "FAILED",
                "profile_kind": profile_kind,
                "purpose": purpose,
                "route": route,
                "requested_model": requested_model,
                "returned_model": returned_model,
                "behavior_sha256": behavior_hash,
                "response_sha256": None,
                "duration_ms": duration_ms,
                "token_usage": token_usage or None,
                "prompt_cache": cache_usage(dict(raw_usage or {})),
                "actual_cost": None,
                "estimated_cost_cny": estimated_cost_cny,
                "cost_evidence": cost_evidence,
                "region": region,
                "egress": egress,
                "failure_reason": reason,
                "quality_assessment": 'NOT_ASSESSED',
                "resource_limits": limits,
                "capacity_assessment": (
                    'NOT_ASSESSED' if reason in {'STRUCTURED_CHAT_JSON_TRUNCATED',
                        'MODEL_OUTPUT_TRUNCATED', 'API_RESPONSE_INCOMPLETE_REMOTE_STATE_UNKNOWN',
                        'CLI_OUTPUT_LIMIT_EXCEEDED', 'CLI_INPUT_TOO_LARGE'} else None
                ),
                "output_budget": {
                    "effective_sent_tokens": effective_max_output_tokens,
                    "source": (
                        "CALLER_VERIFIED_PROFILE"
                        if effective_max_output_tokens is not None
                        else (
                            "CLI_PROVIDER_MANAGED"
                            if profile_kind == "CLI"
                            else "PROVIDER_OR_LOCAL_RUNTIME_MANAGED"
                        )
                    ),
                    "product_hard_limit_added": False,
                },
                "max_output_tokens_mode": (
                    "PROVIDER_DEFAULT"
                    if effective_max_output_tokens is None
                    else "EXPLICIT"
                ),
                "request_max_output_tokens": effective_max_output_tokens,
            }
            if purpose == "workflow_model_exam":
                execution_receipt["output_limit_contract"] = (
                    workflow_exam_output_limit_contract()
                )
                execution_receipt["length_only_failure_quality_semantics"] = (
                    "NOT_ASSESSED_NEVER_MODEL_FAIL_OR_SEMANTIC_REPAIR"
                )
            if isinstance(transport_diagnostic, Mapping):
                execution_receipt["transport_diagnostic"] = dict(
                    transport_diagnostic
                )
            return {
                "schema_version": "SettingsStructuredChatRunnerResult-v1",
                "status": "FAILED",
                "reason": reason,
                "requested_model": requested_model,
                "returned_model": returned_model,
                "duration_ms": duration_ms,
                "external_network_calls": external_network_calls,
                "provider_calls": provider_calls,
                "external_model_calls": external_model_calls,
                "external_process_launches": external_process_launches,
                "execution_receipt": execution_receipt,
            }
        response: Mapping[str, Any]
        returned_model: str
        usage: Mapping[str, Any] = {}
        external_network_calls = 0
        provider_calls = 0
        external_process_launches = 0
        route = ""
        api_provider_family: str | None = None
        transport_diagnostic: Mapping[str, Any] | None = None
        strict_cli_identity_binding = (
            profile_kind == "CLI"
            and model.get("require_exact_cli_identity_binding") is True
        )
        cli_binary_sha256: str | None = None
        cli_version: str | None = None
        cli_identity_authority_ref: str | None = None
        request_model_binding_status: str | None = None
        request_model_binding_sha256: str | None = None
        model_allowlist_sha256: str | None = None

        if profile_kind == "API":
            try:
                profile = _api_profile(service)
            except ValueError as error:
                raise ValueError("API_ENDPOINT_INVALID") from error
            if profile is None:
                raise ValueError("API_PROVIDER_UNSUPPORTED")
            api_provider_family = profile.provider_family
            if (output_limit_mode == "PROVIDER_DEFAULT"
                    and api_provider_family != "ANTHROPIC"
                    and limits["capacity"]["user_output_limit_tokens"] is None):
                # Omit the optional wire field when the user-selected execution
                # policy delegates it. The known provider ceiling remains evidence,
                # not an application-imposed generation cutoff.
                effective_max_output_tokens = None
                limits["wire_output_policy"] = "PROVIDER_DEFAULT"
            if (
                api_provider_family == "ANTHROPIC"
                and effective_max_output_tokens is None
            ):
                raise ValueError("API_OUTPUT_CAPACITY_REQUIRED")
            credential_ref = service.get("credential_ref")
            if not isinstance(credential_ref, str) or not credential_ref:
                raise ValueError("API_CREDENTIAL_UNAVAILABLE")
            try:
                secret_bytes = self._credential_resolver(credential_ref)
                if not isinstance(secret_bytes, bytes) or not secret_bytes or len(secret_bytes) > 512:
                    raise ValueError("API_CREDENTIAL_INVALID")
                secret = secret_bytes.decode("utf-8", errors="strict")
            except (OSError, PermissionError, UnicodeDecodeError, ValueError) as error:
                raise ValueError("API_CREDENTIAL_UNAVAILABLE") from error
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "Memorive-Structured-Chat/1",
                **dict(profile.extra_headers),
            }
            headers[profile.auth_header] = (
                f"Bearer {secret}" if profile.auth_header == "Authorization" else secret
            )
            proxy_mode, proxy_address, configured_timeout = self._preferences()
            timeout = timeout_seconds
            request_body = _api_chat_request(
                profile,
                model=requested_model,
                prompt=prompt,
                response_schema=response_schema,
                max_output_tokens=effective_max_output_tokens,
                model_config=model,
            )
            transport_diagnostic = {
                "api_request_body_sha256": hashlib.sha256(
                    request_body
                ).hexdigest().upper(),
                "max_output_tokens_mode": (
                    "PROVIDER_DEFAULT"
                    if effective_max_output_tokens is None
                    else "EXPLICIT"
                ),
                "request_max_output_tokens": effective_max_output_tokens,
                "wire_max_tokens_field_present": (
                    b'"max_tokens":' in request_body
                    or b'"max_completion_tokens":' in request_body
                    or b'"maxOutputTokens":' in request_body
                ),
                "streaming": json.loads(request_body).get("stream") is True,
                "reasoning_effort": json.loads(request_body).get("reasoning_effort"),
                "thinking_option": json.loads(request_body).get("thinking"),
                "enable_thinking": json.loads(request_body).get("enable_thinking"),
            }
            api_max_response_bytes = model.get("api_max_response_bytes")
            if api_max_response_bytes is not None and (
                isinstance(api_max_response_bytes, bool)
                or not isinstance(api_max_response_bytes, int)
                or api_max_response_bytes <= 0
            ):
                raise ValueError("API_RESPONSE_LIMIT_INVALID")
            transport_diagnostic["max_response_bytes"] = api_max_response_bytes
            try:
                http_response = self._http.request(
                    method="POST",
                    url=_api_chat_url(profile, requested_model),
                    headers=headers,
                    body=request_body,
                    timeout_seconds=timeout,
                    proxy_mode=proxy_mode,
                    proxy_address=proxy_address,
                    max_response_bytes=api_max_response_bytes,
                )
                external_network_calls = 1
                provider_calls = 1
                transport_diagnostic = {
                    **dict(transport_diagnostic or {}),
                    **dict(http_response.transport_diagnostic or {}),
                    "http_status": http_response.status_code,
                }
                if http_response.status_code != 200:
                    _, _, rejection = _api_failure_evidence(http_response.body)
                    transport_diagnostic.update(rejection)
                if http_response.status_code in {401, 403}:
                    return failed_attempt(
                        "API_AUTHENTICATION_REJECTED",
                        returned_model=None,
                        raw_usage=None,
                        external_network_calls=1,
                        provider_calls=1,
                        external_model_calls=1,
                        external_process_launches=0,
                        route="EXISTING_SETTINGS_API_TRANSPORT",
                        region="PROVIDER_MANAGED_UNDISCLOSED",
                        egress="PROVIDER_API",
                        transport_diagnostic=transport_diagnostic,
                    )
                if http_response.status_code == 429:
                    return failed_attempt(
                        "API_RATE_LIMITED",
                        returned_model=None,
                        raw_usage=None,
                        external_network_calls=1,
                        provider_calls=1,
                        external_model_calls=1,
                        external_process_launches=0,
                        route="EXISTING_SETTINGS_API_TRANSPORT",
                        region="PROVIDER_MANAGED_UNDISCLOSED",
                        egress="PROVIDER_API",
                        transport_diagnostic=transport_diagnostic,
                    )
                if http_response.status_code != 200:
                    return failed_attempt(
                        "API_STRUCTURED_CHAT_REQUEST_FAILED",
                        returned_model=None,
                        raw_usage=None,
                        external_network_calls=1,
                        provider_calls=1,
                        external_model_calls=1,
                        external_process_launches=0,
                        route="EXISTING_SETTINGS_API_TRANSPORT",
                        region="PROVIDER_MANAGED_UNDISCLOSED",
                        egress="PROVIDER_API",
                        transport_diagnostic=transport_diagnostic,
                    )
                try:
                    returned_model, response, usage = _api_chat_response(
                        profile, http_response.body, model
                    )
                except ValueError as error:
                    (
                        recovered_model,
                        recovered_usage,
                        api_diagnostic,
                    ) = _api_failure_evidence(
                        http_response.body
                    )
                    return failed_attempt(
                        str(error),
                        returned_model=recovered_model,
                        raw_usage=recovered_usage,
                        external_network_calls=1,
                        provider_calls=1,
                        external_model_calls=1,
                        external_process_launches=0,
                        route="EXISTING_SETTINGS_API_TRANSPORT",
                        region="PROVIDER_MANAGED_UNDISCLOSED",
                        egress="PROVIDER_API",
                        transport_diagnostic={**dict(transport_diagnostic or {}), **dict(http_response.transport_diagnostic or {}),
                                              **dict(api_diagnostic),
                                              **self._preserve_capacity_response(http_response.body)},
                    )
                transport_diagnostic = {
                    **dict(transport_diagnostic or {}),
                    **dict(http_response.transport_diagnostic or {}),
                }
            except _ResponseInterrupted as error:
                return failed_attempt(
                    'API_RESPONSE_INCOMPLETE_REMOTE_STATE_UNKNOWN',
                    returned_model=None, raw_usage=None, external_network_calls=1,
                    provider_calls=1, external_model_calls=1, external_process_launches=0,
                    route='EXISTING_SETTINGS_API_TRANSPORT', region='PROVIDER_MANAGED_UNDISCLOSED',
                    egress='PROVIDER_API', transport_diagnostic={**error.diagnostic,
                        **self._preserve_capacity_response(error.partial, partial=True),
                        'automatic_duplicate_request_safe': False},
                )
            except (OSError, TimeoutError) as error:
                return failed_attempt(
                    "API_RESPONSE_STATUS_UNKNOWN",
                    returned_model=None,
                    raw_usage=None,
                    external_network_calls=1,
                    provider_calls=1,
                    external_model_calls=1,
                    external_process_launches=0,
                    route="EXISTING_SETTINGS_API_TRANSPORT",
                    region="PROVIDER_MANAGED_UNDISCLOSED",
                    egress="PROVIDER_API",
                    transport_diagnostic={
                        **dict(transport_diagnostic or {}),
                        'exception_type': type(error).__name__,
                        'dispatch_state': 'MAY_HAVE_BEEN_ACCEPTED',
                        'remote_cancellation_confirmed': False,
                        'automatic_duplicate_request_safe': False,
                    },
                )
            finally:
                secret = ""
                secret_bytes = b""
                headers.clear()
            route = "EXISTING_SETTINGS_API_TRANSPORT"
        elif profile_kind == "CLI":
            adapter_id = service.get("adapter_id")
            executable_value = service.get("executable")
            if not isinstance(adapter_id, str):
                raise ValueError("CLI_ADAPTER_UNSUPPORTED")
            if not isinstance(executable_value, str):
                raise ValueError("CLI_EXECUTABLE_NOT_FOUND")
            executable = self._executable_resolver(executable_value)
            if executable is None:
                raise ValueError("CLI_EXECUTABLE_NOT_FOUND")
            if strict_cli_identity_binding:
                expected_binary_sha256 = service.get("cli_binary_sha256")
                cli_version_value = service.get("cli_version")
                identity_authority_value = service.get(
                    "cli_identity_authority_ref"
                )
                if (
                    not isinstance(expected_binary_sha256, str)
                    or not SAFE_SHA256.fullmatch(expected_binary_sha256.upper())
                    or _resolved_file_sha256(executable)
                    != expected_binary_sha256.upper()
                    or not isinstance(cli_version_value, str)
                    or not cli_version_value
                    or not isinstance(identity_authority_value, str)
                    or not identity_authority_value
                ):
                    raise ValueError(
                        "CLI_STRUCTURED_CHAT_BINARY_IDENTITY_BINDING_INVALID"
                    )
                cli_binary_sha256 = expected_binary_sha256.upper()
                cli_version = cli_version_value
                cli_identity_authority_ref = identity_authority_value
            thinking_mode = model.get("thinking_mode", "")
            if not isinstance(thinking_mode, str):
                thinking_mode = ""
            proxy_mode, proxy_address, configured_timeout = self._preferences()
            timeout = timeout_seconds
            self._scratch_root.mkdir(parents=True, exist_ok=True)
            with structured_chat_workspace(self._scratch_root, profile_kind=profile_kind,
                    service=service, model=model, purpose=purpose) as workspace:
                output_schema_path: str | None = None
                if adapter_id == "codex_cli":
                    transport_schema: Mapping[str, Any] = model.get("prompt_cache_transport_schema", response_schema)
                    if purpose == "conversation_refinement":
                        transport_schema, transport_diagnostic = _codex_refinement_transport_schema(transport_schema)
                    elif purpose in {
                        "workflow_model_exam",
                        "core_document_processing",
                        "report_daily", "report_weekly", "report_monthly", "research_chat",
                    }:
                        (
                            transport_schema,
                            transport_diagnostic,
                        ) = _codex_transport_schema(transport_schema)
                    schema_path = write_response_schema(workspace, json.dumps(
                        transport_schema, ensure_ascii=False, allow_nan=False,
                        sort_keys=True, separators=(",", ":"),
                    ).encode("utf-8"))
                    output_schema_path = str(schema_path)
                try:
                    argv, stdin_bytes = _cli_argv(
                        adapter_id,
                        executable,
                        requested_model,
                        thinking_mode,
                        str(workspace),
                        prompt_bytes,
                        output_schema_path,
                    )
                except ValueError as error:
                    raise ValueError(str(error)) from error
                continuation = Continuation(workspace, model, prompt_bytes,
                    transport_schema if adapter_id == "codex_cli" else response_schema)
                if continuation.enabled:
                    argv = continuation.prepare_argv(argv)
                    stdin_bytes = continuation.prompt_bytes
                    behavior_hash = hashlib.sha256(stdin_bytes).hexdigest().upper()
                if strict_cli_identity_binding:
                    model_indices = [
                        index for index, item in enumerate(argv) if item == "--model"
                    ]
                    if (
                        len(model_indices) != 1
                        or model_indices[0] + 1 >= len(argv)
                        or argv[model_indices[0] + 1] != requested_model
                    ):
                        raise ValueError("CODEX_REQUEST_MODEL_ARGV_INVALID")
                    model_allowlist_sha256 = _workflow_exam_v8_sha256(
                        {"allowed_models": [requested_model]}
                    )
                    request_model_binding_sha256 = _workflow_exam_v8_sha256(
                        {
                            "adapter_id": adapter_id,
                            "binary_sha256": cli_binary_sha256,
                            "cli_version": cli_version,
                            "argv_sha256": _workflow_exam_v8_sha256(list(argv)),
                            "model_flag_index": model_indices[0],
                            "requested_model": requested_model,
                            "model_allowlist_sha256": model_allowlist_sha256,
                            "behavior_sha256": behavior_hash,
                        }
                    )
                    request_model_binding_status = (
                        "EXACT_ARGV_BINARY_AND_FROZEN_ALLOWLIST"
                    )
                    transport_diagnostic = {
                        **dict(transport_diagnostic or {}),
                        "request_model_binding_status": (
                            request_model_binding_status
                        ),
                        "request_model_binding_sha256": (
                            request_model_binding_sha256
                        ),
                        "model_allowlist_sha256": model_allowlist_sha256,
                        "cli_binary_sha256": cli_binary_sha256,
                        "cli_version": cli_version,
                        "identity_authority_ref": cli_identity_authority_ref,
                    }
                process = self._process.run(
                    argv=argv,
                    cwd=str(workspace),
                    environment=self._safe_environment(
                        adapter_id, workspace, proxy_mode, proxy_address
                    ),
                    stdin_bytes=stdin_bytes,
                    timeout_seconds=timeout,
                    shell=False,
                )
                events_accepted = True
                if continuation.enabled:
                    event_summary, event_error = _execution_codex_event_diagnostic(process.stdout, requested_model)
                    events_accepted = (event_error is None and isinstance(event_summary, Mapping)
                        and event_summary.get("finish_status") == "SUCCESS"
                        and event_summary.get("model_identity_grade") != "CONFLICT")
                continuation.complete(process, _structured_document_from_output(process.stdout), response_schema,
                    events_accepted=events_accepted)
                transport_diagnostic = {**dict(transport_diagnostic or {}), **continuation.diagnostic}
                if (continuation.enabled and process.started and process.returncode == 0
                        and continuation.diagnostic.get("session_record_status") != "SUCCESS"):
                    return failed_attempt(
                        "CODEX_CONTINUATION_VALIDATION_FAILED", returned_model=None,
                        raw_usage=continuation.attempt_usage or {}, external_network_calls=0,
                        provider_calls=0, external_model_calls=1, external_process_launches=1,
                        route="CODEX_CLI_SUBSCRIPTION", region="OPENAI_MANAGED_UNDISCLOSED",
                        egress="CODEX_CLI", transport_diagnostic=transport_diagnostic,
                    )
            if not process.started:
                return failed_attempt("CLI_PROCESS_START_FAILED", returned_model=None,
                    raw_usage=None, external_network_calls=0, provider_calls=0,
                    external_model_calls=0, external_process_launches=0,
                    route="CODEX_CLI_SUBSCRIPTION", region="OPENAI_MANAGED_UNDISCLOSED",
                    egress="CODEX_CLI", transport_diagnostic={
                        **dict(transport_diagnostic or {}), "provider_request_sent": False,
                    })
            external_process_launches = 1
            transport_diagnostic = {
                **dict(transport_diagnostic or {}),
                "stdout_total_bytes": process.stdout_total_bytes,
                "stderr_total_bytes": process.stderr_total_bytes,
                "capture_quota_bytes": process.capture_quota_bytes,
                "quota_source": (
                    "EXPLICIT_RESOURCE_PROFILE"
                    if process.capture_quota_bytes is not None
                    else "DISK_SPOOL_NO_GENERIC_PRODUCT_LIMIT"
                ),
            }
            usage = (continuation.attempt_usage or {}) if continuation.enabled else _usage_from_cli_output(process.stdout)
            if process.timed_out:
                return failed_attempt(
                    "CLI_STRUCTURED_CHAT_TIMEOUT",
                    returned_model=None,
                    raw_usage=usage,
                    external_network_calls=0,
                    provider_calls=0,
                    external_model_calls=1,
                    external_process_launches=1,
                    route="CODEX_CLI_SUBSCRIPTION",
                    region="OPENAI_MANAGED_UNDISCLOSED",
                    egress="CODEX_CLI",
                    transport_diagnostic=transport_diagnostic,
                )
            if process.output_truncated:
                return failed_attempt(
                    "CLI_OUTPUT_LIMIT_EXCEEDED",
                    returned_model=None,
                    raw_usage=usage,
                    external_network_calls=0,
                    provider_calls=0,
                    external_model_calls=1,
                    external_process_launches=1,
                    route="CODEX_CLI_SUBSCRIPTION",
                    region="OPENAI_MANAGED_UNDISCLOSED",
                    egress="CODEX_CLI",
                    transport_diagnostic=transport_diagnostic,
                )
            if process.returncode != 0:
                from .cli_verification_transport import failure_reason
                failure_diagnostic = {
                    **dict(transport_diagnostic or {}),
                    **dict(_cli_failure_diagnostic(process)),
                }
                return failed_attempt(
                    failure_reason(process.stderr + b"\n" + process.stdout),
                    returned_model=None,
                    raw_usage=usage,
                    external_network_calls=0,
                    provider_calls=0,
                    external_model_calls=1,
                    external_process_launches=1,
                    route="CODEX_CLI_SUBSCRIPTION",
                    region="OPENAI_MANAGED_UNDISCLOSED",
                    egress="CODEX_CLI",
                    transport_diagnostic=failure_diagnostic,
                )
            if adapter_id == "codex_cli":
                execution_diagnostic, execution_error = _execution_codex_event_diagnostic(
                    process.stdout, requested_model
                )
                if execution_error is not None and purpose in {
                    "workflow_model_exam", "conversation_refinement",
                    "core_document_processing",
                    "report_daily", "report_weekly", "report_monthly", "research_chat",
                }:
                    return failed_attempt(
                        execution_error,
                        returned_model=None,
                        raw_usage=usage,
                        external_network_calls=0,
                        provider_calls=0,
                        external_model_calls=1,
                        external_process_launches=1,
                        route="CODEX_CLI_SUBSCRIPTION",
                        region="OPENAI_MANAGED_UNDISCLOSED",
                        egress="CODEX_CLI",
                        transport_diagnostic={
                            **dict(transport_diagnostic or {}),
                            "execution_parser_error": execution_error,
                            "stdout_sha256": hashlib.sha256(
                                process.stdout
                            ).hexdigest().upper(),
                        },
                    )
                if execution_error is not None:
                    transport_diagnostic = {
                        **dict(transport_diagnostic or {}),
                        "execution_parser_error": execution_error,
                        "execution_parser_compatibility_fallback": True,
                        "stdout_sha256": hashlib.sha256(
                            process.stdout
                        ).hexdigest().upper(),
                    }
                if isinstance(execution_diagnostic, Mapping):
                    transport_diagnostic = {
                        **dict(transport_diagnostic or {}),
                        "execution_parser_version": execution_diagnostic.get(
                            "parser_version"
                        ),
                        "execution_events_sha256": execution_diagnostic.get(
                            "events_sha256"
                        ),
                        "execution_model_identity_grade": execution_diagnostic.get(
                            "model_identity_grade"
                        ),
                        "execution_returned_model": execution_diagnostic.get(
                            "returned_model"
                        ),
                        "execution_usage_status": execution_diagnostic.get(
                            "usage_status"
                        ),
                    }
                    if execution_diagnostic.get("finish_status") != "SUCCESS":
                        return failed_attempt(
                            "CODEX_JSONL_FINISH_NOT_SUCCESS",
                            returned_model=(
                                execution_diagnostic.get("returned_model")
                                if isinstance(
                                    execution_diagnostic.get("returned_model"), str
                                )
                                else None
                            ),
                            raw_usage=usage,
                            external_network_calls=0,
                            provider_calls=0,
                            external_model_calls=1,
                            external_process_launches=1,
                            route="CODEX_CLI_SUBSCRIPTION",
                            region="OPENAI_MANAGED_UNDISCLOSED",
                            egress="CODEX_CLI",
                            transport_diagnostic=transport_diagnostic,
                        )
                    if execution_diagnostic.get("model_identity_grade") == "CONFLICT":
                        return failed_attempt(
                            "CLI_RETURNED_MODEL_MISMATCH",
                            returned_model=(
                                execution_diagnostic.get("returned_model")
                                if isinstance(
                                    execution_diagnostic.get("returned_model"), str
                                )
                                else None
                            ),
                            raw_usage=usage,
                            external_network_calls=0,
                            provider_calls=0,
                            external_model_calls=1,
                            external_process_launches=1,
                            route="CODEX_CLI_SUBSCRIPTION",
                            region="OPENAI_MANAGED_UNDISCLOSED",
                            egress="CODEX_CLI",
                            transport_diagnostic=transport_diagnostic,
                        )
                    execution_usage = execution_diagnostic.get("token_usage")
                    if isinstance(execution_usage, Mapping) and not continuation.enabled:
                        usage = {
                            **dict(usage),
                            **dict(execution_usage),
                        }
            reported = _reported_models(process.stdout)
            if reported and requested_model not in reported:
                return failed_attempt(
                    "CLI_RETURNED_MODEL_MISMATCH",
                    returned_model=(
                        next(iter(reported)) if len(reported) == 1 else None
                    ),
                    raw_usage=usage,
                    external_network_calls=0,
                    provider_calls=0,
                    external_model_calls=1,
                    external_process_launches=1,
                    route="CODEX_CLI_SUBSCRIPTION",
                    region="OPENAI_MANAGED_UNDISCLOSED",
                    egress="CODEX_CLI",
                )
            if (
                purpose in {"workflow_model_exam", "core_document_processing", "report_daily", "report_weekly", "report_monthly", "research_chat"}
                and not reported
                and adapter_id != "codex_cli"
            ):
                return failed_attempt(
                    "CLI_RETURNED_MODEL_EVIDENCE_MISSING",
                    returned_model=None,
                    raw_usage=usage,
                    external_network_calls=0,
                    provider_calls=0,
                    external_model_calls=1,
                    external_process_launches=1,
                    route="CODEX_CLI_SUBSCRIPTION",
                    region="OPENAI_MANAGED_UNDISCLOSED",
                    egress="CODEX_CLI",
                )
            response = _structured_document_from_output(process.stdout) or {}
            if continuation.enabled:
                response = response.get("payload") if isinstance(response, dict) and set(response) == {"payload"} else {}
            if not response:
                return failed_attempt(
                    "STRUCTURED_CHAT_RESPONSE_INVALID",
                    returned_model=requested_model if reported else None,
                    raw_usage=usage,
                    external_network_calls=0,
                    provider_calls=0,
                    external_model_calls=1,
                    external_process_launches=1,
                    route="CODEX_CLI_SUBSCRIPTION",
                    region="OPENAI_MANAGED_UNDISCLOSED",
                    egress="CODEX_CLI",
                )
            # Codex CLI binds the selected model through the exact --model
            # argument.  Current JSONL may omit a redundant model field; a
            # successful process with no contradictory identity still binds
            # the returned identity to that exact invocation.
            returned_model = requested_model
            route = "CODEX_CLI_SUBSCRIPTION"
        else:
            if (
                service.get("endpoint_kind") != "ollama"
                or service.get("structured_chat_adapter")
                != "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1"
                or service.get("execution_eligible") is not True
                or service.get("exact_identity_available") is not True
                or not isinstance(service.get("chat_endpoint"), str)
                or not isinstance(service.get("model_digest"), str)
                or not SAFE_SHA256.fullmatch(str(service["model_digest"]).upper())
            ):
                raise ValueError("LOCAL_STRUCTURED_CHAT_PROFILE_INVALID")
            from model_gateway.local_structured_chat import LocalStructuredChatAdapter, _default_open
            from .call_ledger import recording_urlopen

            timeout = timeout_seconds
            local_num_ctx = (
                limits['capacity']['effective_capacity']['shared_context_tokens']
                if limits['capacity']['machine_safe_input_tokens'] is not None else None
            )
            local_binding = {
                "schema_version": "EvaluationAssetsLocalStructuredChatBinding-v1",
                "endpoint": service["chat_endpoint"],
                "requested_model": requested_model,
                "expected_model_digest": str(service["model_digest"]).upper(),
                "think": False,
                "temperature": 0,
                "seed": 0,
                "cloud_fallback": False,
            }
            if effective_max_output_tokens is not None:
                local_binding["num_predict"] = effective_max_output_tokens
            if local_num_ctx is not None:
                local_binding["num_ctx"] = local_num_ctx
            transport_diagnostic = {"local_num_ctx": local_num_ctx, "local_num_predict": effective_max_output_tokens, "capacity_profile_specific": True}
            outcome = LocalStructuredChatAdapter(urlopen=recording_urlopen(_default_open)).execute(
                prompt=prompt_bytes.decode("utf-8"),
                schema=response_schema,
                binding=local_binding,
                purpose=purpose,
                timeout_seconds=timeout,
            )
            local_receipt = outcome.receipt
            local_model_calls = int(
                local_receipt.get("local_model_request_count") or 0
            )
            usage = (
                local_receipt.get("usage")
                if isinstance(local_receipt.get("usage"), Mapping)
                else {}
            )
            if not outcome.success or not isinstance(outcome.response, Mapping):
                return failed_attempt(
                    ('STRUCTURED_CHAT_JSON_TRUNCATED' if outcome.error_code == 'MODEL_OUTPUT_TRUNCATED'
                     else outcome.error_code or "LOCAL_STRUCTURED_CHAT_FAILED"),
                    returned_model=(
                        local_receipt.get("returned_model")
                        if isinstance(local_receipt.get("returned_model"), str)
                        else None
                    ),
                    raw_usage=usage,
                    external_network_calls=0,
                    provider_calls=0,
                    external_model_calls=local_model_calls,
                    external_process_launches=0,
                    route="OLLAMA_LOOPBACK",
                    region="LOCAL_MACHINE",
                    egress="LOOPBACK_ONLY",
                    transport_diagnostic={
                        **({'finish_reason': 'length'} if outcome.error_code == 'MODEL_OUTPUT_TRUNCATED' else {}),
                        **(self._preserve_capacity_response(json.dumps(outcome.raw_response).encode('utf-8'))
                           if isinstance(getattr(outcome, 'raw_response', None), Mapping) else {}),
                        **transport_diagnostic,
                        "local_receipt_sha256": local_receipt.get(
                            "receipt_sha256"
                        ),
                        "model_digest": local_receipt.get("model_digest"),
                        "ollama_version": local_receipt.get("ollama_version"),
                        "local_metadata_request_count": local_receipt.get(
                            "local_metadata_request_count"
                        ),
                        "local_model_request_count": local_model_calls,
                    },
                )
            response = dict(outcome.response)
            returned_model = str(local_receipt.get("returned_model") or "")
            route = "OLLAMA_LOOPBACK"
            transport_diagnostic.update(
                {
                    "local_receipt_sha256": local_receipt.get(
                        "receipt_sha256"
                    ),
                    "model_digest": local_receipt.get("model_digest"),
                    "ollama_version": local_receipt.get("ollama_version"),
                    "local_metadata_request_count": local_receipt.get(
                        "local_metadata_request_count"
                    ),
                    "local_model_request_count": local_model_calls,
                }
            )

        from .provider_catalog_aliases import catalog_alias
        provider_alias = catalog_alias(service, requested_model, [returned_model]) if profile_kind == 'API' else None
        if (unicodedata.normalize("NFKC", returned_model).strip()
            != unicodedata.normalize("NFKC", requested_model).strip() and provider_alias is None):
            return failed_attempt(
                "STRUCTURED_CHAT_RETURNED_MODEL_MISMATCH",
                returned_model=returned_model,
                raw_usage=usage,
                external_network_calls=external_network_calls,
                provider_calls=provider_calls,
                external_model_calls=1,
                external_process_launches=external_process_launches,
                route=route,
                region=(
                    "OPENAI_MANAGED_UNDISCLOSED"
                    if profile_kind == "CLI"
                    else (
                        "LOCAL_MACHINE"
                        if profile_kind == "LOCAL"
                        else "PROVIDER_MANAGED_UNDISCLOSED"
                    )
                ),
                egress=(
                    "CODEX_CLI"
                    if profile_kind == "CLI"
                    else ("LOOPBACK_ONLY" if profile_kind == "LOCAL" else "PROVIDER_API")
                ),
            )
        token_usage = _normalise_token_usage(usage)
        if (
            purpose in {"workflow_model_exam", "core_document_processing", "report_daily", "report_weekly", "report_monthly", "research_chat"}
            and not token_usage
        ):
            return failed_attempt(
                "STRUCTURED_CHAT_TOKEN_EVIDENCE_MISSING",
                returned_model=returned_model,
                raw_usage=usage,
                external_network_calls=external_network_calls,
                provider_calls=provider_calls,
                external_model_calls=1,
                external_process_launches=external_process_launches,
                route=route,
                region=(
                    "OPENAI_MANAGED_UNDISCLOSED"
                    if profile_kind == "CLI"
                    else (
                        "LOCAL_MACHINE"
                        if profile_kind == "LOCAL"
                        else "PROVIDER_MANAGED_UNDISCLOSED"
                    )
                ),
                egress=(
                    "CODEX_CLI"
                    if profile_kind == "CLI"
                    else ("LOOPBACK_ONLY" if profile_kind == "LOCAL" else "PROVIDER_API")
                ),
            )
        if purpose == "workflow_model_exam":
            observed_completion_tokens = int(
                token_usage.get("completion_tokens") or 0
            )
            transport_diagnostic = {
                **dict(transport_diagnostic or {}),
                "output_limit_contract": workflow_exam_output_limit_contract(),
                "observed_completion_tokens": observed_completion_tokens,
                "calibrated_reply_token_ceiling": (
                    WORKFLOW_EXAM_REPLY_TOKEN_CEILING
                ),
                "calibrated_reply_token_ceiling_exceeded": (
                    observed_completion_tokens
                    > WORKFLOW_EXAM_REPLY_TOKEN_CEILING
                ),
                "reply_length_quality_semantics": (
                    "EVIDENCE_RETAINED_NEVER_MODEL_FAIL_OR_SEMANTIC_REPAIR"
                ),
            }
        estimated_cost_cny = (
            _api_estimated_cost_cny(
                requested_model, model, token_usage
            )
            if profile_kind == "API"
            else None
        )
        if profile_kind == "CLI":
            cost_evidence = "SUBSCRIPTION_CLI_NO_PER_CALL_PRICE"
            region = "OPENAI_MANAGED_UNDISCLOSED"
            egress = "CODEX_CLI"
        elif profile_kind == "LOCAL":
            estimated_cost_cny = 0.0
            cost_evidence = "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
            region = "LOCAL_MACHINE"
            egress = "LOOPBACK_ONLY"
        elif estimated_cost_cny is not None:
            cost_evidence = (
                "FROZEN_TARGET_PRICING_PROFILE_ESTIMATE"
                if model.get("pricing_evidence_ref")
                else "FROZEN_Quality_PUBLIC_PRICING_PROFILE_2026-07-30_ESTIMATE"
            )
            region = "PROVIDER_MANAGED_UNDISCLOSED"
            egress = "PROVIDER_API"
        else:
            cost_evidence = "NOT_REPORTED_BY_TRANSPORT"
            region = "PROVIDER_MANAGED_UNDISCLOSED"
            egress = "PROVIDER_API"
        duration_ms = max(0, round((time.monotonic() - started_at) * 1000))
        from runtime_log.cache_metrics import cache_metrics
        transport_diagnostic = {**dict(transport_diagnostic or {}),
            'prompt_cache_metrics': cache_metrics(dict(usage or {}), duration_ms=duration_ms,
                estimated_cost=estimated_cost_cny)}
        response_hash = hashlib.sha256(
            json.dumps(
                response,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest().upper()
        return {
            "schema_version": "SettingsStructuredChatRunnerResult-v1",
            "status": "PASS",
            "reason": "STRUCTURED_CHAT_COMPLETED",
            "requested_model": requested_model,
            "returned_model": returned_model,
            "response": response,
            "duration_ms": duration_ms,
            "external_network_calls": external_network_calls,
            "provider_calls": provider_calls,
            "external_model_calls": 1,
            "external_process_launches": external_process_launches,
            "execution_receipt": {
                "schema_version": "SettingsStructuredChatExecutionReceipt-v2",
                "status": "PASS",
                "profile_kind": profile_kind,
                "purpose": purpose,
                **({'provider_model_alias': {**provider_alias, 'endpoint_origin': 'https://api.deepseek.com'}} if provider_alias is not None else {}),
                "route": route,
                "requested_model": requested_model,
                "returned_model": returned_model,
                "behavior_sha256": behavior_hash,
                "response_sha256": response_hash,
                "duration_ms": duration_ms,
                "token_usage": token_usage,
                "actual_cost": None,
                "estimated_cost_cny": estimated_cost_cny,
                "cost_evidence": cost_evidence,
                "region": region,
                "egress": egress,
                "model_identity_evidence": (
                    "DIRECT_CLI_REQUEST_BINDING_V1"
                    if strict_cli_identity_binding
                    else "CLI_EXACT_MODEL_ARGUMENT_AND_SUCCESSFUL_PROCESS"
                    if profile_kind == "CLI"
                    else (
                        "OLLAMA_EXACT_NAME_DIGEST_AND_RESPONSE_MODEL"
                        if profile_kind == "LOCAL"
                        else "PROVIDER_RESPONSE_MODEL_FIELD"
                    )
                ),
                "request_model_binding_status": request_model_binding_status,
                "request_model_binding_sha256": request_model_binding_sha256,
                "model_allowlist_sha256": model_allowlist_sha256,
                "identity_authority_ref": cli_identity_authority_ref,
                "cli_binary_sha256": cli_binary_sha256,
                "cli_version": cli_version,
                "transport_diagnostic": transport_diagnostic,
                "resource_limits": limits,
                "output_budget": {
                    "effective_sent_tokens": effective_max_output_tokens,
                    "source": (
                        "CALLER_VERIFIED_PROFILE"
                        if effective_max_output_tokens is not None
                        else (
                            "CLI_PROVIDER_MANAGED"
                            if profile_kind == "CLI"
                            else "PROVIDER_OR_LOCAL_RUNTIME_MANAGED"
                        )
                    ),
                    "product_hard_limit_added": False,
                },
                "max_output_tokens_mode": (
                    "PROVIDER_DEFAULT"
                    if effective_max_output_tokens is None
                    else "EXPLICIT"
                ),
                "request_max_output_tokens": effective_max_output_tokens,
            },
        }

    def _frozen_workflow_exam_row(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        active_executor_refs = {
            str(getattr(executor, "EXECUTOR_REF"))
            for executor in self._workflow_exam_executors
            if isinstance(getattr(executor, "EXECUTOR_REF", None), str)
        }
        recorded = self._exam_score_history.lookup(node, target, active_executor_refs)
        if recorded is None:
            recorded = self._exam_score_history.recover_completed_embedding(node, target, active_executor_refs)
        if recorded is not None:
            return recorded

        def reusable(candidate: Mapping[str, Any]) -> bool:
            if candidate.get(
                "cache_reuse_status", "STABLE_REUSE"
            ) != "STABLE_REUSE":
                return False
            if candidate.get("cache_reuse_requires_executor_ref_match") is True:
                return candidate.get("executor_ref") in active_executor_refs
            return True

        if target.get("kind") == "LOCAL":
            endpoint_kind = target.get("endpoint_kind")
            model_name = target.get("model_name")
            model_digest = target.get("model_digest")
            if (
                endpoint_kind != "ollama"
                or not isinstance(model_name, str)
                or not isinstance(model_digest, str)
                or not SAFE_SHA256.fullmatch(model_digest.upper())
            ):
                return None
            identity = (
                node.get("node_id"),
                "LOCAL",
                endpoint_kind,
                model_name,
                model_digest.upper(),
            )
            return next(
                (
                    candidate
                    for candidate in self._workflow_exam_entries
                    if reusable(candidate)
                    and candidate.get("profile_kind") == "LOCAL"
                    and (
                        candidate["node_id"],
                        "LOCAL",
                        candidate["endpoint_kind"],
                        candidate["model_name"],
                        candidate["model_digest"],
                    )
                    == identity
                ),
                None,
            )
        if target.get("kind") == "CLI":
            adapter_id = target.get("adapter_id")
            model_name = target.get("model_name")
            thinking_mode = target.get("thinking_mode")
            if not all(
                isinstance(value, str) and bool(value)
                for value in (adapter_id, model_name, thinking_mode)
            ):
                return None
            identity = (
                node.get("node_id"),
                "CLI",
                adapter_id,
                model_name,
                thinking_mode,
            )
            return next(
                (
                    candidate
                    for candidate in self._workflow_exam_entries
                    if reusable(candidate)
                    and candidate.get("profile_kind") == "CLI"
                    and (
                        candidate["node_id"],
                        "CLI",
                        candidate["adapter_id"],
                        candidate["model_name"],
                        candidate["thinking_mode"],
                    )
                    == identity
                ),
                None,
            )
        if target.get("kind") != "API":
            return None
        try:
            api_profile = _api_profile(target)
        except ValueError:
            return None
        if api_profile is None:
            return None
        model_name = target.get("model_name")
        thinking = target.get("thinking")
        tier = target.get("tier")
        if (
            not isinstance(model_name, str)
            or not isinstance(thinking, bool)
            or not isinstance(tier, str)
        ):
            return None
        identity = (
            node.get("node_id"),
            "API",
            api_profile.provider_family,
            model_name,
            thinking,
            tier,
        )
        row = next(
            (
                candidate
                for candidate in self._workflow_exam_entries
                if reusable(candidate)
                and candidate.get("profile_kind") == "API"
                and (
                    candidate["node_id"],
                    candidate["profile_kind"],
                    candidate["provider_family"],
                    candidate["model_name"],
                    candidate["thinking"],
                    candidate["tier"],
                )
                == identity
            ),
            None,
        )
        return row

    def workflow_exam_score_catalog(self) -> Mapping[str, Any]:
        return {
            "schema_version": "WorkflowModelExamScoreCatalogProjection-v2",
            "horizontal_comparison_allowed_only_with_same_cohort": True,
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
            "entries": [dict(row) for row in self._workflow_exam_entries],
            "historical_entries": [
                dict(row) for row in self._workflow_exam_historical_entries
            ],
        }

    def cached_workflow_node_score(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        resolved_target = self._resolve_exam_target(target)
        row = self._frozen_workflow_exam_row(node, resolved_target)
        return dict(row) if row is not None else None

    @staticmethod
    def _plan_hash(plan: Mapping[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                plan,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest().upper()

    def plan_workflow_node_exam(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        resolved_target = self._resolve_ocr_exam_target(node, target)
        row = None if (context or {}).get("workflow_exam_force_live") is True else self._frozen_workflow_exam_row(node, resolved_target)
        if row is not None:
            plan: dict[str, Any] = {
                "schema_version": "WorkflowModelExamPlan-v2",
                "status": "READY",
                "mode": "FROZEN_MODEL_EXAM_SCORE_REUSE",
                "reason": "FROZEN_MODEL_EXAM_SCORE_AVAILABLE",
                "node_id": node.get("node_id"),
                "model_name": resolved_target.get("model_name"),
                "maximum_model_calls": 0,
                "budget_cap_cny": 0.0,
                "reference_regression_only": True,
                "qualification_eligible": False,
                "exam_evidence_sha256": row["evidence_sha256"],
                "authorization_required": False,
                "external_network_calls": 0,
                "provider_calls": 0,
                "external_model_calls": 0,
            }
            plan.update(
                {
                    field: row[field]
                    for field in WORKFLOW_EXAM_SCORE_METADATA_FIELDS
                    if field in row
                }
            )
            plan["plan_sha256"] = self._plan_hash(plan)
            return plan
        accepted_context = dict(context or {})
        executor_plan_diagnostics: list[dict[str, str]] = []
        from .tiered_exam import tier_executor
        for original_executor in self._workflow_exam_executors:
            executor = tier_executor(original_executor, accepted_context)
            executor_ref = getattr(
                executor, "EXECUTOR_REF", type(executor).__name__
            )
            if not isinstance(executor_ref, str) or not SAFE_MODEL.fullmatch(
                executor_ref
            ):
                executor_ref = type(executor).__name__
            try:
                plan = executor.plan(node, resolved_target, accepted_context)
            except Exception as exc:
                reason = type(exc).__name__
                missing_module = getattr(exc, "name", None)
                if isinstance(missing_module, str) and SAFE_MODEL.fullmatch(
                    missing_module
                ):
                    reason = f"{reason}:{missing_module}"
                elif (
                    exc.args
                    and isinstance(exc.args[0], str)
                    and SAFE_REASON_CODE.fullmatch(exc.args[0])
                ):
                    reason = exc.args[0]
                executor_plan_diagnostics.append(
                    {
                        "executor_ref": executor_ref,
                        "status": "ERROR",
                        "reason": reason,
                    }
                )
                continue
            if isinstance(plan, Mapping) and plan.get("status") == "READY":
                return dict(plan)
            plan_reason = (
                plan.get("reason") if isinstance(plan, Mapping) else None
            )
            executor_plan_diagnostics.append(
                {
                    "executor_ref": executor_ref,
                    "status": "NOT_READY",
                    "reason": (
                        plan_reason
                        if isinstance(plan_reason, str)
                        and SAFE_REASON_CODE.fullmatch(plan_reason)
                        else "WORKFLOW_NODE_EXAM_EXECUTOR_PLAN_NOT_READY"
                    ),
                }
            )
        return {
            "schema_version": "WorkflowModelExamPlan-v2",
            "status": "NOT_AVAILABLE",
            "mode": "NONE",
            "reason": "WORKFLOW_NODE_EXAM_SCORE_NOT_AVAILABLE",
            "executor_plan_diagnostics": executor_plan_diagnostics,
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
        }

    def test_workflow_node(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
        authorization: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        result = self._base(
            "WORKFLOW_NODE",
            status="NOT_RUN",
            reason="WORKFLOW_NODE_EXAM_SCORE_NOT_AVAILABLE",
        )
        resolved_target = self._resolve_ocr_exam_target(node, target)
        row = None if (context or {}).get("workflow_exam_force_live") is True else self._frozen_workflow_exam_row(node, resolved_target)
        if row is None:
            accepted_context = dict(context or {})
            from .tiered_exam import tier_executor, project_tier_result
            for original_executor in self._workflow_exam_executors:
                executor = tier_executor(original_executor, accepted_context)
                try:
                    plan = executor.plan(node, resolved_target, accepted_context)
                except Exception:
                    continue
                if not isinstance(plan, Mapping) or plan.get("status") != "READY":
                    continue
                try:
                    parameters = signature(executor.execute).parameters
                    from .exam_jobs import observed_call
                    kwargs: dict[str, Any] = {
                        "authorization": authorization,
                        "structured_chat": observed_call(self.execute_structured_chat, "structured_chat"),
                    }
                    if "embedding_call" in parameters:
                        kwargs["embedding_call"] = observed_call(self.execute_embeddings, "embedding")
                    if "ocr_call" in parameters:
                        kwargs["ocr_call"] = observed_call(self.execute_ocr_image, "ocr")
                    if "rerank_call" in parameters:
                        kwargs["rerank_call"] = observed_call(self.execute_rerank, "rerank")
                    dynamic = executor.execute(
                        node,
                        resolved_target,
                        accepted_context,
                        **kwargs,
                    )
                except Exception:
                    result["reason"] = "WORKFLOW_NODE_EXAM_EXECUTOR_FAILED"
                    return result
                if not isinstance(dynamic, Mapping):
                    result["reason"] = "WORKFLOW_NODE_EXAM_EXECUTOR_FAILED"
                    return result
                projected = project_tier_result(dynamic, plan)
                try:
                    self._exam_score_history.record(
                        node, resolved_target, projected,
                        {getattr(e, "EXECUTOR_REF", None) for e in self._workflow_exam_executors},
                    )
                except (OSError, ValueError, TypeError):
                    # The answer and immutable run survive an auxiliary cache failure.
                    projected["score_cache_write_failed"] = True
                return projected
            return result
        model_name = resolved_target.get("model_name")
        result.update(
            status=row["verdict"],
            score=row["score"],
            reason="FROZEN_MODEL_EXAM_SCORE_REUSED",
            requested_model=model_name,
            returned_model=model_name,
            exam_evidence_sha256=row["evidence_sha256"],
            **{
                field: row[field]
                for field in WORKFLOW_EXAM_SCORE_METADATA_FIELDS
                if field in row
            },
        )
        return result


__all__ = [
    "BoundedSubprocessTransport",
    "FailClosedModelValidationRunner",
    "HttpResponse",
    "HttpTransport",
    "LiveModelValidationRunner",
    "ModelValidationRunner",
    "ProcessResult",
    "ProcessTransport",
    "UrllibHttpTransport",
]
