"""Frozen-paper closure and retry/repair policy checks."""

from __future__ import annotations

from .errors import PaperOutcomeClosureGap, PolicyViolation, SchemaValidationError
from .schema import validate_record


def frozen_paper_ids(sample_manifest: dict) -> tuple[str, ...]:
    """Extract the unique frozen paper set without guessing from filenames."""
    tasks = sample_manifest.get("tasks")
    if not isinstance(tasks, list):
        raise SchemaValidationError("sample_manifest.tasks must be a list")
    paper_ids: set[str] = set()
    for task in tasks:
        paper_id = task.get("paper_id") if isinstance(task, dict) else None
        if not isinstance(paper_id, str) or not paper_id.strip():
            raise SchemaValidationError(
                "every sample_manifest task must carry a non-empty paper_id"
            )
        paper_ids.add(paper_id)
    return tuple(sorted(paper_ids))


def validate_paper_outcome_closure(
    sample_manifest: dict, outcomes: list[dict]
) -> tuple[str, ...]:
    """Require exactly one terminal outcome for every frozen paper."""
    expected = set(frozen_paper_ids(sample_manifest))
    observed: dict[str, dict] = {}
    for outcome in outcomes:
        validate_record(outcome)
        if outcome["ledger_type"] != "paper_outcome":
            raise SchemaValidationError("closure input must contain paper outcomes only")
        if outcome["record_kind"] != "terminal":
            continue
        paper_id = outcome["paper_id"]
        if paper_id in observed:
            raise SchemaValidationError(
                f"duplicate terminal paper outcome for {paper_id!r}"
            )
        observed[paper_id] = outcome
    missing = sorted(expected - set(observed))
    if missing:
        raise PaperOutcomeClosureGap(missing)
    unexpected = sorted(set(observed) - expected)
    if unexpected:
        raise SchemaValidationError(
            "paper outcomes outside frozen manifest: " + ", ".join(unexpected)
        )
    return tuple(sorted(observed))


def enforce_follow_up_policy(
    *,
    prior_business_outcome: str,
    relation: str,
    authorized_new_run: bool = False,
) -> None:
    """Prevent semantic failure from being washed by an ordinary retry."""
    if relation not in {"technical_retry", "business_repair", "amendment"}:
        raise PolicyViolation(f"unknown follow-up relation: {relation}")
    if prior_business_outcome in {"failure", "partial"}:
        if relation == "technical_retry":
            raise PolicyViolation(
                "SEMANTIC_FAILURE_CANNOT_BE_WASHED_BY_TECHNICAL_RETRY"
            )
        if relation == "business_repair" and not authorized_new_run:
            raise PolicyViolation(
                "BUSINESS_REPAIR_REQUIRES_AUTHORIZED_NEW_RUN"
            )
