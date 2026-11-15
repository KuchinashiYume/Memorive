"""Local evaluator for the frozen RETRIEVAL-CALIBRATION query/metric pack."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from typing import Any, Mapping, Sequence


PROFILES = (
    "BASELINE_DISTANCE_OFF",
    "STRUCTURE_ONLY_SHADOW",
    "RERANK_ONLY_SHADOW",
    "STRUCTURE_PLUS_RERANK_SHADOW",
)


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


def validate_pack_bindings(
    pack: Mapping[str, Any],
    subject_projection: Mapping[str, Any],
    evaluator_gold: Mapping[str, Any],
    isolated_index: Mapping[str, Any],
) -> dict[str, Any]:
    pack_copy = dict(pack)
    expected_content = pack_copy.pop("content_sha256", None)
    if expected_content != canonical_sha256(pack_copy):
        raise ValueError("PACK_CONTENT_HASH_MISMATCH")
    if pack.get("source_snapshot_sha256") != canonical_sha256(isolated_index):
        raise ValueError("INDEX_HASH_MISMATCH")
    if pack.get("index_generation_sha256") != canonical_sha256(isolated_index):
        raise ValueError("INDEX_GENERATION_HASH_MISMATCH")
    if pack.get("subject_projection_sha256") != canonical_sha256(subject_projection):
        raise ValueError("SUBJECT_HASH_MISMATCH")
    if pack.get("evaluator_projection_sha256") != canonical_sha256(evaluator_gold):
        raise ValueError("GOLD_HASH_MISMATCH")
    if subject_projection.get("gold_or_labels_present") is not False:
        raise ValueError("SUBJECT_GOLD_ISOLATION_FAILED")
    if evaluator_gold.get("subject_visible") is not False:
        raise ValueError("GOLD_VISIBILITY_INVALID")
    queries = list(pack.get("queries") or [])
    subject_queries = list(subject_projection.get("queries") or [])
    gold_queries = list(evaluator_gold.get("queries") or [])
    query_ids = [query.get("query_id") for query in queries]
    if len(query_ids) != len(set(query_ids)) or set(query_ids) != {
        query.get("query_id") for query in subject_queries
    } or set(query_ids) != {query.get("query_id") for query in gold_queries}:
        raise ValueError("QUERY_EXACT_SET_MISMATCH")
    candidate_ids = [row.get("candidate_id") for row in isolated_index.get("candidates", [])]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("INDEX_CANDIDATE_DUPLICATE")
    candidate_set = set(candidate_ids)
    for gold in gold_queries:
        labels = gold.get("labels") or {}
        flattened: list[str] = []
        for values in labels.values():
            flattened.extend(values)
        if len(flattened) != len(set(flattened)) or not set(flattened).issubset(candidate_set):
            raise ValueError("GOLD_LABEL_EXACT_SET_INVALID")
    return {
        "query_count": len(queries),
        "candidate_count": len(candidate_ids),
        "gold_isolation": "PASS",
        "binding_verdict": "PASS",
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def score_query(
    query: Mapping[str, Any],
    gold: Mapping[str, Any],
    candidate_ids: Sequence[str],
    *,
    candidate_catalog: Mapping[str, Mapping[str, Any]],
    baseline_ids: Sequence[str],
) -> dict[str, Any]:
    k_values = query.get("k_values") or []
    if len(k_values) != 1 or not isinstance(k_values[0], int) or k_values[0] < 1:
        raise ValueError("QUERY_K_INVALID")
    k = k_values[0]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("OUTPUT_CANDIDATE_DUPLICATE")
    if any(candidate_id not in candidate_catalog for candidate_id in candidate_ids):
        raise ValueError("OUTPUT_CANDIDATE_FOREIGN")
    labels = gold.get("labels") or {}
    critical = set(labels.get("critical_positive") or [])
    protected = set(labels.get("protected_positive") or [])
    hard_negative = set(labels.get("hard_negative") or [])
    top_k = list(candidate_ids[:k])
    baseline_top_k = list(baseline_ids[:k])
    critical_hits = len(critical.intersection(top_k))
    protected_hits = len(protected.intersection(top_k))
    hard_negative_hits = len(hard_negative.intersection(top_k))
    first_critical_rank = next(
        (rank for rank, candidate_id in enumerate(candidate_ids, 1) if candidate_id in critical),
        None,
    )
    allowed_papers = set(query.get("allowed_paper_ids") or [])
    scope_leaks = sum(
        1
        for candidate_id in candidate_ids
        if candidate_catalog[candidate_id].get("paper_id") not in allowed_papers
    )
    harmful = len(critical.intersection(baseline_top_k).difference(top_k))
    return {
        "query_id": query.get("query_id"),
        "query_intent": query.get("query_intent"),
        "scope": query.get("scope"),
        "k": k,
        "critical_recall_at_k": _ratio(critical_hits, len(critical)),
        "first_critical_rank": first_critical_rank,
        "hard_negative_occupancy_at_k": hard_negative_hits / k,
        "protected_positive_retention_at_k": _ratio(protected_hits, len(protected)),
        "harmful_displacement_count": harmful,
        "scope_leak_rate": _ratio(scope_leaks, len(candidate_ids)) or 0.0,
        "underfill_or_abstain": len(candidate_ids) < k,
        "returned_count": len(candidate_ids),
        "critical_denominator": len(critical),
        "protected_denominator": len(protected),
    }


def _query_verdict(
    metrics: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    profile: str,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    recall = metrics.get("critical_recall_at_k")
    baseline_recall = baseline.get("critical_recall_at_k")
    if recall is None:
        return "NOT_ASSESSED", ["CRITICAL_DENOMINATOR_ZERO"]
    if baseline_recall is not None and recall < baseline_recall:
        reasons.append("CRITICAL_RECALL_BELOW_BASELINE")
    if metrics.get("query_intent") == "quantitative_result" and recall < 1.0:
        reasons.append("QUANTITATIVE_CRITICAL_RECALL_BELOW_ONE")
    if metrics.get("harmful_displacement_count") != 0:
        reasons.append("HARMFUL_DISPLACEMENT")
    if metrics.get("scope") == "single_paper" and metrics.get("scope_leak_rate") != 0.0:
        reasons.append("SINGLE_PAPER_SCOPE_LEAK")
    protected = metrics.get("protected_positive_retention_at_k")
    baseline_protected = baseline.get("protected_positive_retention_at_k")
    if protected is not None and baseline_protected is not None and protected < baseline_protected:
        reasons.append("PROTECTED_POSITIVE_REGRESSION")
    if (
        profile != "BASELINE_DISTANCE_OFF"
        and metrics.get("query_intent") != "citation_bibliographic"
        and metrics.get("hard_negative_occupancy_at_k")
        >= baseline.get("hard_negative_occupancy_at_k", 0.0)
    ):
        reasons.append("ORDINARY_HARD_NEGATIVE_OCCUPANCY_NOT_REDUCED")
    return ("FAIL", reasons) if reasons else ("PASS", [])


def score_profiles(
    pack: Mapping[str, Any],
    evaluator_gold: Mapping[str, Any],
    isolated_index: Mapping[str, Any],
    outputs: Mapping[str, Mapping[str, Sequence[str]]],
) -> dict[str, Any]:
    if set(outputs) != set(PROFILES):
        raise ValueError("PROFILE_EXACT_SET_MISMATCH")
    queries = {query["query_id"]: query for query in pack.get("queries", [])}
    gold = {query["query_id"]: query for query in evaluator_gold.get("queries", [])}
    catalog = {row["candidate_id"]: row for row in isolated_index.get("candidates", [])}
    baseline_ids = {
        query_id: [
            row["candidate_id"]
            for row in sorted(
                (row for row in catalog.values() if row.get("query_id") == query_id),
                key=lambda row: (row.get("distance", math.inf), row["candidate_id"]),
            )
        ]
        for query_id in queries
    }
    if set(outputs["BASELINE_DISTANCE_OFF"]) != set(queries):
        raise ValueError("OUTPUT_QUERY_EXACT_SET_MISMATCH")
    baseline_metrics = {
        query_id: score_query(
            query,
            gold[query_id],
            outputs["BASELINE_DISTANCE_OFF"][query_id],
            candidate_catalog=catalog,
            baseline_ids=baseline_ids[query_id],
        )
        for query_id, query in queries.items()
    }
    if any(
        list(outputs["BASELINE_DISTANCE_OFF"][query_id]) != baseline_ids[query_id]
        for query_id in queries
    ):
        raise ValueError("OFF_EXACT_PARITY_FAILED")
    profile_results: dict[str, Any] = {}
    for profile in PROFILES:
        if set(outputs[profile]) != set(queries):
            raise ValueError("OUTPUT_QUERY_EXACT_SET_MISMATCH")
        query_results = []
        family_results: dict[tuple[str, str], list[str]] = defaultdict(list)
        for query_id, query in queries.items():
            metrics = score_query(
                query,
                gold[query_id],
                outputs[profile][query_id],
                candidate_catalog=catalog,
                baseline_ids=baseline_ids[query_id],
            )
            verdict, reasons = _query_verdict(
                metrics, baseline_metrics[query_id], profile=profile
            )
            metrics["verdict"] = verdict
            metrics["reason_codes"] = reasons
            query_results.append(metrics)
            family_results[(metrics["query_intent"], metrics["scope"])].append(verdict)
        families = []
        for (intent, scope), verdicts in sorted(family_results.items()):
            verdict = (
                "FAIL"
                if "FAIL" in verdicts
                else "PASS"
                if "PASS" in verdicts
                else "NOT_ASSESSED"
            )
            families.append({"query_intent": intent, "scope": scope, "verdict": verdict})
        profile_verdict = (
            "FAIL"
            if any(row["verdict"] == "FAIL" for row in families)
            else "PASS"
            if any(row["verdict"] == "PASS" for row in families)
            else "NOT_ASSESSED"
        )
        profile_results[profile] = {
            "verdict": profile_verdict,
            "queries": query_results,
            "families": families,
            "output_sha256": canonical_sha256(outputs[profile]),
        }
    return {
        "schema_version": "RetrievalCalibrationRetrievalScoreReport-v1",
        "profiles": profile_results,
        "off_exact_parity": "PASS",
        "family_gate_not_macro_average": True,
        "report_sha256": canonical_sha256(profile_results),
    }


def build_qualification_manifest(
    score_report: Mapping[str, Any],
    *,
    candidate_id: str,
    source_hashes: Mapping[str, str],
    wp_b_model_eligible: bool,
    artifact_class: str = "FORMAL_LOCAL_QUALIFICATION_RESULT",
) -> dict[str, Any]:
    profiles = score_report.get("profiles") or {}
    wp_a_pass = profiles.get("STRUCTURE_ONLY_SHADOW", {}).get("verdict") == "PASS"
    wp_b_quality_pass = profiles.get("RERANK_ONLY_SHADOW", {}).get("verdict") == "PASS"
    combined_pass = profiles.get("STRUCTURE_PLUS_RERANK_SHADOW", {}).get("verdict") == "PASS"
    wp_b_pass = wp_b_model_eligible and wp_b_quality_pass
    if wp_a_pass and wp_b_pass and combined_pass:
        capability = "QUALIFIED"
    elif wp_a_pass or wp_b_pass:
        capability = "PARTIALLY_QUALIFIED"
    else:
        capability = "KEEP_BASELINE"
    all_families = profiles.get("STRUCTURE_PLUS_RERANK_SHADOW", {}).get("families", [])
    manifest = {
        "schema_version": "RetrievalCalibrationRetrievalQualificationManifest-v1",
        "artifact_class": artifact_class,
        "candidate_id": candidate_id,
        "machine_terminal": {
            "lifecycle_status": "completed",
            "verification_result": "PASS",
            "acceptance_verdict": "NOT_ASSESSED",
        },
        "wp_a": {
            "verdict": "QUALIFIED" if wp_a_pass else "KEEP_BASELINE",
            "reason_codes": [] if wp_a_pass else ["STRUCTURE_PROFILE_HARD_GATE_FAILED"],
        },
        "wp_b": {
            "verdict": "QUALIFIED" if wp_b_pass else "KEEP_DISABLED",
            "reason_codes": [] if wp_b_pass else ["LOCAL_RERANKER_NOT_ELIGIBLE"],
        },
        "profiles": {
            profile: {
                "verdict": profiles[profile]["verdict"],
                "output_sha256": profiles[profile]["output_sha256"],
            }
            for profile in PROFILES
        },
        "family_verdicts": all_families,
        "source_hashes": dict(source_hashes),
        "side_effects": {
            "external_calls": 0,
            "cloud_calls": 0,
            "production_index_writes": 0,
            "production_route_mutations": 0,
            "model_downloads_during_qualification": 0,
            "formal_repo_writes": 0,
        },
        "human_gate_b": "READY",
        "activation_authorized": False,
        "capability_verdict": capability,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    return manifest
