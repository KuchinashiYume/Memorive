"""End-to-end, side-effect-free RETRIEVAL-CALIBRATION shadow qualification helpers."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from memorive_model_evaluation.retrieval_metrics import (
    build_qualification_manifest,
    score_profiles,
    validate_pack_bindings,
)

from .candidate_features import build_reranker_provenance, canonical_sha256
from .profile_compare import compare_profiles


RERANK_PROFILES = ("RERANK_ONLY_SHADOW", "STRUCTURE_PLUS_RERANK_SHADOW")
MULTIMODAL_LISTWISE_SUBJECT = "LOCAL_MULTIMODAL_GENERATIVE_LISTWISE"


def _queries_by_id(subject_projection: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    queries = subject_projection.get("queries")
    if not isinstance(queries, list):
        raise ValueError("SUBJECT_QUERIES_INVALID")
    by_id = {query.get("query_id"): dict(query) for query in queries if isinstance(query, Mapping)}
    if len(by_id) != len(queries) or any(not isinstance(key, str) or not key for key in by_id):
        raise ValueError("SUBJECT_QUERY_ID_INVALID_OR_DUPLICATE")
    return by_id


def baseline_candidates(isolated_index: Mapping[str, Any], query_id: str) -> list[dict[str, Any]]:
    rows = [
        deepcopy(dict(row))
        for row in isolated_index.get("candidates", [])
        if row.get("query_id") == query_id
    ]
    rows.sort(key=lambda row: (row["distance"], row["candidate_id"]))
    if not rows or len({row.get("candidate_id") for row in rows}) != len(rows):
        raise ValueError("INDEX_QUERY_CANDIDATES_INVALID")
    for rank, row in enumerate(rows, 1):
        row["base_rank"] = rank
        row.setdefault("rerank_score", None)
    return rows


def prepare_reranker_batches(
    subject_projection: Mapping[str, Any],
    isolated_index: Mapping[str, Any],
    *,
    policy: Any,
) -> dict[str, Any]:
    """Create Gold-free logical-rerank batches for score or listwise subjects."""

    queries = _queries_by_id(subject_projection)
    retrieval_terminal_sha256 = getattr(policy, "retrieval_terminal_sha256", None)
    if (
        not isinstance(retrieval_terminal_sha256, str)
        or len(retrieval_terminal_sha256) != 64
        or any(character not in "0123456789ABCDEF" for character in retrieval_terminal_sha256)
    ):
        raise ValueError("Retrieval_TERMINAL_SHA256_INVALID")
    batches: list[dict[str, Any]] = []
    bindings: dict[str, dict[str, Any]] = {}
    for query_id, query in queries.items():
        baseline = baseline_candidates(isolated_index, query_id)
        preorders = {
            "RERANK_ONLY_SHADOW": baseline,
            "STRUCTURE_PLUS_RERANK_SHADOW": policy.calibrate(query["query_intent"], baseline),
        }
        for profile, candidates in preorders.items():
            batch_id = f"{query_id}::{profile}"
            documents = []
            for row in candidates:
                documents.append(
                    {
                        "candidate_id": row["candidate_id"],
                        "text": row["text"],
                        "provenance": build_reranker_provenance(
                            row,
                            retrieval_terminal_sha256=retrieval_terminal_sha256,
                        ),
                    }
                )
            batches.append(
                {
                    "query_id": batch_id,
                    "query": query["canonical_query"],
                    "query_intent": query["query_intent"],
                    "documents": documents,
                }
            )
            bindings[batch_id] = {
                "query_id": query_id,
                "profile": profile,
                "candidate_ids": [row["candidate_id"] for row in candidates],
                "provenance_sha256s": [
                    document["provenance"]["provenance_sha256"]
                    for document in documents
                ],
            }
    receipt = {
        "schema_version": "RetrievalCalibrationRerankerBatchBinding-v1",
        "batch_count": len(batches),
        "query_count": len(queries),
        "profiles": list(RERANK_PROFILES),
        "retrieval_terminal_sha256": retrieval_terminal_sha256,
        "bindings": bindings,
        "gold_or_scorer_present": False,
    }
    receipt["binding_sha256"] = canonical_sha256(receipt)
    return {"batches": batches, "binding": receipt}


def bind_listwise_batches(
    rank_batches: Sequence[Mapping[str, Any]],
    binding: Mapping[str, Any],
) -> dict[str, dict[str, list[str]]]:
    """Bind complete candidate-id orders without converting ranks to scores."""

    bindings = binding.get("bindings")
    if not isinstance(bindings, Mapping):
        raise ValueError("RERANK_BINDING_INVALID")
    by_batch = {batch.get("query_id"): batch for batch in rank_batches}
    if len(by_batch) != len(rank_batches) or set(by_batch) != set(bindings):
        raise ValueError("LISTWISE_BATCH_EXACT_SET_MISMATCH")
    output: dict[str, dict[str, list[str]]] = {profile: {} for profile in RERANK_PROFILES}
    for batch_id, expected in bindings.items():
        candidate_ids = by_batch[batch_id].get("candidate_ids")
        expected_ids = expected["candidate_ids"]
        if (
            not isinstance(candidate_ids, list)
            or len(candidate_ids) != len(expected_ids)
            or len(set(candidate_ids)) != len(candidate_ids)
            or set(candidate_ids) != set(expected_ids)
        ):
            raise ValueError("LISTWISE_CANDIDATE_EXACT_SET_MISMATCH")
        output[expected["profile"]][expected["query_id"]] = list(candidate_ids)
    return output


def bind_score_batches(
    score_batches: Sequence[Mapping[str, Any]],
    binding: Mapping[str, Any],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    bindings = binding.get("bindings")
    if not isinstance(bindings, Mapping):
        raise ValueError("RERANK_BINDING_INVALID")
    by_batch = {batch.get("query_id"): batch for batch in score_batches}
    if len(by_batch) != len(score_batches) or set(by_batch) != set(bindings):
        raise ValueError("RERANK_SCORE_BATCH_EXACT_SET_MISMATCH")
    output = {profile: {} for profile in RERANK_PROFILES}
    for batch_id, expected in bindings.items():
        batch = by_batch[batch_id]
        scores = batch.get("scores")
        candidate_ids = expected["candidate_ids"]
        if not isinstance(scores, list) or len(scores) != len(candidate_ids):
            raise ValueError("RERANK_SCORE_COUNT_MISMATCH")
        rows = []
        for index, (candidate_id, score) in enumerate(zip(candidate_ids, scores)):
            if score.get("index") != index or score.get("candidate_id") != candidate_id:
                raise ValueError("RERANK_SCORE_ALIGNMENT_MISMATCH")
            rows.append({"index": index, "score": score.get("score")})
        output[expected["profile"]][expected["query_id"]] = rows
    return output


def run_qualification(
    pack: Mapping[str, Any],
    subject_projection: Mapping[str, Any],
    evaluator_gold: Mapping[str, Any],
    isolated_index: Mapping[str, Any],
    *,
    policy: Any,
    score_rows: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]] | None = None,
    ranked_candidate_ids: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
    candidate_id: str,
    source_hashes: Mapping[str, str],
    wp_b_model_eligible: bool,
) -> dict[str, Any]:
    binding_receipt = validate_pack_bindings(pack, subject_projection, evaluator_gold, isolated_index)
    queries = _queries_by_id(subject_projection)
    if (score_rows is None) == (ranked_candidate_ids is None):
        raise ValueError("EXACTLY_ONE_RANKING_SUBJECT_OUTPUT_REQUIRED")
    subject_outputs = score_rows if score_rows is not None else ranked_candidate_ids
    if not isinstance(subject_outputs, Mapping) or set(subject_outputs) != set(RERANK_PROFILES):
        raise ValueError("RERANK_PROFILE_OUTPUT_EXACT_SET_MISMATCH")
    outputs: dict[str, dict[str, list[str]]] = {
        "BASELINE_DISTANCE_OFF": {},
        "STRUCTURE_ONLY_SHADOW": {},
        "RERANK_ONLY_SHADOW": {},
        "STRUCTURE_PLUS_RERANK_SHADOW": {},
    }
    profile_details: dict[str, Any] = {}
    all_traces: list[dict[str, Any]] = []
    for query_id, query in queries.items():
        candidates = baseline_candidates(isolated_index, query_id)
        compared = compare_profiles(
            query,
            candidates,
            policy=policy,
            rerank_only_scores=(
                score_rows["RERANK_ONLY_SHADOW"][query_id]
                if score_rows is not None
                else None
            ),
            combined_scores=(
                score_rows["STRUCTURE_PLUS_RERANK_SHADOW"][query_id]
                if score_rows is not None
                else None
            ),
            rerank_only_candidate_ids=(
                ranked_candidate_ids["RERANK_ONLY_SHADOW"][query_id]
                if ranked_candidate_ids is not None
                else None
            ),
            combined_candidate_ids=(
                ranked_candidate_ids["STRUCTURE_PLUS_RERANK_SHADOW"][query_id]
                if ranked_candidate_ids is not None
                else None
            ),
        )
        profile_details[query_id] = compared
        for profile, detail in compared.items():
            outputs[profile][query_id] = detail["candidate_ids"]
            all_traces.extend(detail["traces"])
    score_report = score_profiles(pack, evaluator_gold, isolated_index, outputs)
    manifest = build_qualification_manifest(
        score_report,
        candidate_id=candidate_id,
        source_hashes=source_hashes,
        wp_b_model_eligible=wp_b_model_eligible,
    )
    ranking_subject_mode = (
        "PER_CANDIDATE_RELEVANCE_SCORE"
        if score_rows is not None
        else MULTIMODAL_LISTWISE_SUBJECT
    )
    manifest["wp_b"]["ranking_subject_mode"] = ranking_subject_mode
    manifest["wp_b"]["dedicated_reranker_model_required"] = False
    manifest["wp_b"]["synthetic_relevance_scores"] = False
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    result = {
        "schema_version": "RetrievalCalibrationQualificationResult-v1",
        "candidate_id": candidate_id,
        "pack_binding_receipt": binding_receipt,
        "profile_outputs": outputs,
        "profile_details": profile_details,
        "candidate_traces": all_traces,
        "score_report": score_report,
        "qualification_manifest": manifest,
        "ranking_subject_mode": ranking_subject_mode,
        "side_effects": {
            "external_calls": 0,
            "cloud_calls": 0,
            "production_index_writes": 0,
            "production_route_mutations": 0,
        },
    }
    result["result_sha256"] = canonical_sha256(result)
    return result
