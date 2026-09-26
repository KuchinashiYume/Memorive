"""Create immutable comparison and qualification report projections."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .contracts import (
    OCRQualificationContractError,
    canonical_sha256,
    validate_normalized_page_transcription,
    validate_page_family_qualification,
)


def compare_page(
    outputs: Sequence[Mapping[str, Any]],
    scorecards: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare independent outputs by hashes and metrics without merging text."""

    accepted_outputs = [validate_normalized_page_transcription(value) for value in outputs]
    if not accepted_outputs:
        raise OCRQualificationContractError("COMPARE_PAGE_OUTPUTS_EMPTY")
    pages = {value["page_id"] for value in accepted_outputs}
    images = {value["source_image_sha256"] for value in accepted_outputs}
    candidates = [value["candidate_key"] for value in accepted_outputs]
    if len(pages) != 1 or len(images) != 1:
        raise OCRQualificationContractError("COMPARE_PAGE_ANCHOR_MISMATCH")
    if len(candidates) != len(set(candidates)):
        raise OCRQualificationContractError("COMPARE_PAGE_CANDIDATE_DUPLICATE")
    card_by_candidate = {str(card.get("candidate_key")): deepcopy(dict(card)) for card in scorecards}
    if len(scorecards) != len(candidates) or len(card_by_candidate) != len(scorecards) or set(card_by_candidate) != set(candidates):
        raise OCRQualificationContractError("COMPARE_PAGE_SCORECARD_DENOMINATOR_MISMATCH")
    input_hashes_before = {value["candidate_key"]: canonical_sha256(value) for value in accepted_outputs}
    literal_hashes = {
        value["candidate_key"]: canonical_sha256(value["literal_text"])
        for value in accepted_outputs
    }
    block_orders = {
        value["candidate_key"]: [
            [block["block_id"], block["role"], block["reading_order"]]
            for block in value["ordered_blocks"]
        ]
        for value in accepted_outputs
    }
    critical_hashes = {
        value["candidate_key"]: canonical_sha256(value["critical_items"])
        for value in accepted_outputs
    }
    projection = {
        "page_id": next(iter(pages)),
        "source_image_sha256": next(iter(images)),
        "candidate_count": len(candidates),
        "candidate_outputs": {
            candidate: {
                "output_sha256": input_hashes_before[candidate],
                "literal_text_sha256": literal_hashes[candidate],
                "critical_items_sha256": critical_hashes[candidate],
                "block_order": block_orders[candidate],
                "metrics": deepcopy(card_by_candidate[candidate].get("metrics", {})),
                "hard_gate_pass": bool(card_by_candidate[candidate].get("hard_gate_pass")),
                "quality_pass": bool(card_by_candidate[candidate].get("quality_pass")),
                "hard_failure_codes": sorted(
                    set(card_by_candidate[candidate].get("hard_failure_codes", []))
                ),
            }
            for candidate in sorted(candidates)
        },
        "disagreement": {
            "literal_text": len(set(literal_hashes.values())) > 1,
            "critical_items": len(set(critical_hashes.values())) > 1,
            "block_order": len({canonical_sha256(value) for value in block_orders.values()}) > 1,
            "automatic_vote_or_merge": False,
        },
        "candidate_outputs_mutated": False,
    }
    input_hashes_after = {value["candidate_key"]: canonical_sha256(value) for value in accepted_outputs}
    if input_hashes_after != input_hashes_before:
        raise OCRQualificationContractError("COMPARE_PAGE_INPUT_MUTATED")
    projection["report_sha256"] = canonical_sha256(projection)
    return projection


def summarize_qualification_matrix(
    matrix: Sequence[Mapping[str, Any]],
    *,
    expected_exact_keys: Sequence[Sequence[Any]] | None = None,
) -> dict[str, Any]:
    cells = [validate_page_family_qualification(value) for value in matrix]
    keys = [
        (
            cell["candidate_key"],
            cell["page_family"],
            tuple(cell["modifiers"]),
            cell["output_lane"],
        )
        for cell in cells
    ]
    duplicate_count = len(keys) - len(set(keys))
    if duplicate_count:
        raise OCRQualificationContractError("QUALIFICATION_MATRIX_KEY_DUPLICATE")
    expected = {tuple(item) for item in (expected_exact_keys or [])}
    observed = set(keys)
    missing = sorted(expected - observed, key=repr)
    counts = Counter(cell["role_eligibility"] for cell in cells)
    return {
        "cell_count": len(cells),
        "role_counts": dict(sorted(counts.items())),
        "all_default_disabled": all(
            cell["default_enabled"] is False and cell["activation_authorized"] is False
            for cell in cells
        ),
        "missing_expected_cells": [
            [item[0], item[1], list(item[2]), item[3]] for item in missing
        ],
        "method_result": "PASS" if not missing else "FAIL_DENOMINATOR",
        "task_pass_requires_any_candidate_enabled": False,
        "matrix_sha256": canonical_sha256(cells),
    }
