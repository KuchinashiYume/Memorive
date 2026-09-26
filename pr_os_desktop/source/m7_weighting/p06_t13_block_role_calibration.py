"""P06/T13 rank-domain block-role calibration.

The policy is intentionally separate from M7 ``DerivedPenalty``.  It never
drops candidates and treats unknown or inconsistent metadata as neutral.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence


QUERY_INTENTS = {
    "quantitative_result",
    "method_condition",
    "conclusion_mechanism",
    "citation_bibliographic",
}
PROTECT = "PROTECT"
NEUTRAL = "NEUTRAL"
SOFT_PENALIZE = "SOFT_PENALIZE"
_NOISE_ROLES = {
    "toc",
    "table_of_contents",
    "fixture_header",
    "header_footer",
    "page_metadata",
    "disclaimer",
    "references",
    "reference_list",
    "toc_like_summary",
}
_REFERENCE_ROLES = {"references", "reference_list"}


def _canonical_sha256(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest().upper()


@dataclass(frozen=True)
class CalibrationDecision:
    action: str
    reason_code: str
    rank_delta: int
    hard_drop: bool = False


class BlockRoleCalibrationPolicy:
    """Frozen T13 v1 soft-calibration policy."""

    def __init__(
        self,
        *,
        revision: str = "r1",
        t07_terminal_sha256: str,
        soft_penalty_rank_delta: int = 4,
    ) -> None:
        if not revision or len(t07_terminal_sha256) != 64:
            raise ValueError("POLICY_IDENTITY_INVALID")
        if soft_penalty_rank_delta < 1:
            raise ValueError("SOFT_PENALTY_DELTA_INVALID")
        self.revision = revision
        self.t07_terminal_sha256 = t07_terminal_sha256.upper()
        self.soft_penalty_rank_delta = soft_penalty_rank_delta

    @property
    def policy_sha256(self) -> str:
        return _canonical_sha256(
            {
                "policy": "P06T13BlockRoleCalibrationPolicy",
                "revision": self.revision,
                "t07_terminal_sha256": self.t07_terminal_sha256,
                "soft_penalty_rank_delta": self.soft_penalty_rank_delta,
                "hard_drop_allowed": False,
                "unknown_action": NEUTRAL,
                "inconsistent_action": NEUTRAL,
            }
        )

    def decide(
        self,
        query_intent: str,
        source_role: str | None,
        *,
        metadata_status: str = "vector",
        role_confidence: str = "high",
    ) -> CalibrationDecision:
        if query_intent not in QUERY_INTENTS:
            raise ValueError("QUERY_INTENT_INVALID")
        status = (metadata_status or "unknown").casefold()
        role = (source_role or "unknown").strip().casefold()
        confidence = (role_confidence or "unknown").casefold()
        if status in {"unknown", "inconsistent"} or role in {"", "unknown", "inconsistent"}:
            return CalibrationDecision(NEUTRAL, "ROLE_METADATA_UNKNOWN_OR_INCONSISTENT", 0)
        if role == "citation_discussion":
            return CalibrationDecision(PROTECT, "CITATION_DISCUSSION_PROTECTED", 0)
        if query_intent == "citation_bibliographic" and role in _REFERENCE_ROLES:
            return CalibrationDecision(PROTECT, "CITATION_QUERY_REFERENCE_PROTECTED", 0)
        if role in _NOISE_ROLES:
            if confidence not in {"high", "asserted"}:
                return CalibrationDecision(NEUTRAL, "ROLE_CONFIDENCE_INSUFFICIENT", 0)
            return CalibrationDecision(
                SOFT_PENALIZE,
                "ORDINARY_QUERY_HIGH_CONFIDENCE_STRUCTURE_NOISE",
                self.soft_penalty_rank_delta,
            )
        return CalibrationDecision(NEUTRAL, "ROLE_RELEVANCE_NEUTRAL", 0)

    def calibrate(
        self,
        query_intent: str,
        candidates: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        seen: set[str] = set()
        enriched: list[dict[str, Any]] = []
        for ordinal, candidate in enumerate(candidates, 1):
            candidate_id = candidate.get("candidate_id")
            if not isinstance(candidate_id, str) or not candidate_id or candidate_id in seen:
                raise ValueError("CANDIDATE_ID_INVALID_OR_DUPLICATE")
            seen.add(candidate_id)
            base_rank = candidate.get("base_rank", ordinal)
            if not isinstance(base_rank, int) or isinstance(base_rank, bool) or base_rank < 1:
                raise ValueError("BASE_RANK_INVALID")
            decision = self.decide(
                query_intent,
                candidate.get("source_role"),
                metadata_status=str(candidate.get("metadata_status") or "unknown"),
                role_confidence=str(candidate.get("role_confidence") or "high"),
            )
            row = dict(candidate)
            row.update(
                {
                    "base_rank": base_rank,
                    "calibration_action": decision.action,
                    "calibration_reason": decision.reason_code,
                    "calibration_rank_delta": decision.rank_delta,
                    "adjusted_rank": base_rank + decision.rank_delta,
                    "hard_drop": False,
                    "policy_sha256": self.policy_sha256,
                }
            )
            enriched.append(row)
        enriched.sort(
            key=lambda row: (row["adjusted_rank"], row["base_rank"], row["candidate_id"])
        )
        for final_rank, row in enumerate(enriched, 1):
            row["structure_rank"] = final_rank
        if len(enriched) != len(candidates):
            raise AssertionError("NO_HARD_DROP_INVARIANT_BROKEN")
        return enriched


def decision_dict(decision: CalibrationDecision) -> dict[str, Any]:
    return asdict(decision)
