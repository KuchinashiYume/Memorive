"""Bounded Full -> one targeted repair -> Delta adapter for P06/T06.

This module owns only the T06 orchestration contract.  It keeps the existing
``atomic_repair`` implementation read-only and can carry an existing repair
bundle reference without mutating that implementation.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Mapping, Sequence


class SegmentedRepairContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def canonical_hash(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest().upper()


def _family(model: str) -> str:
    value = model.strip().casefold()
    for family in ("deepseek", "claude", "gpt", "gemini", "qwen", "kimi"):
        if family in value:
            return family
    return value.split("-", 1)[0]


def _pointer_parts(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise SegmentedRepairContractError(
            "REPAIR_TARGET_POINTER_INVALID", pointer
        )
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _get(value: Mapping[str, Any], pointer: str) -> Any:
    current: Any = value
    for part in _pointer_parts(pointer):
        if not isinstance(current, Mapping) or part not in current:
            raise SegmentedRepairContractError(
                "REPAIR_TARGET_NOT_FOUND", pointer
            )
        current = current[part]
    return current


def _set(value: dict[str, Any], pointer: str, replacement: Any) -> None:
    parts = _pointer_parts(pointer)
    current: Any = value
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            raise SegmentedRepairContractError("REPAIR_TARGET_NOT_FOUND", pointer)
        current = current[part]
    if not isinstance(current, dict) or parts[-1] not in current:
        raise SegmentedRepairContractError("REPAIR_TARGET_NOT_FOUND", pointer)
    current[parts[-1]] = deepcopy(replacement)


def freeze_targeted_repair_bundle(
    *,
    card_candidate: Mapping[str, Any],
    full_review_receipt: Mapping[str, Any],
    producer_model: str,
    reviewer_model: str,
    existing_atomic_bundle_ref: str,
) -> dict[str, Any]:
    if _family(producer_model) == _family(reviewer_model):
        raise SegmentedRepairContractError(
            "PRODUCER_REVIEWER_FAMILY_MUST_DIFFER",
            f"{producer_model} / {reviewer_model}",
        )
    if full_review_receipt.get("review_type") != "FULL" or full_review_receipt.get("finish_reason") != "stop":
        raise SegmentedRepairContractError(
            "FULL_REVIEW_RECEIPT_INVALID", "one complete Full receipt is required"
        )
    findings = full_review_receipt.get("repairable_findings")
    if not isinstance(findings, list) or not findings:
        raise SegmentedRepairContractError(
            "REPAIRABLE_FINDINGS_REQUIRED", "repair bundle cannot be speculative"
        )
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for finding in findings:
        finding_id = finding.get("finding_id")
        pointer = finding.get("target_pointer")
        if not isinstance(finding_id, str) or not finding_id or not isinstance(pointer, str):
            raise SegmentedRepairContractError(
                "REPAIR_FINDING_INVALID", repr(finding)
            )
        if pointer in seen:
            raise SegmentedRepairContractError("REPAIR_TARGET_DUPLICATE", pointer)
        if not pointer.startswith("/fields/"):
            raise SegmentedRepairContractError(
                "REPAIR_TARGET_OUTSIDE_WHITELIST", pointer
            )
        seen.add(pointer)
        before = _get(card_candidate, pointer)
        targets.append(
            {
                "finding_id": finding_id,
                "target_pointer": pointer,
                "before_hash": canonical_hash(before),
                "source_excerpts": deepcopy(finding.get("source_excerpts") or []),
                "allowed_actions": ["replace_exact_target", "abstain"],
            }
        )
    body = {
        "schema_version": "p06-t06-targeted-repair-bundle-v1",
        "card_candidate_hash": card_candidate["content_hash"],
        "producer_model": producer_model,
        "reviewer_model": reviewer_model,
        "existing_atomic_bundle_ref": existing_atomic_bundle_ref,
        "targets": targets,
        "max_repair_attempts": 1,
        "max_delta_reviews": 1,
        "retry_ceiling": 0,
        "gold_or_scorer_in_producer_payload": False,
    }
    return {**body, "content_hash": canonical_hash(body)}


def apply_targeted_repair(
    *,
    card_candidate: Mapping[str, Any],
    repair_bundle: Mapping[str, Any],
    repair_response: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if repair_response.get("finish_reason") != "stop" or repair_response.get("terminal_marker") != "P06_T06_REPAIR_COMPLETE":
        raise SegmentedRepairContractError(
            "REPAIR_RESPONSE_TRUNCATED_OR_UNTERMINATED", "partial repair cannot apply"
        )
    if repair_bundle["card_candidate_hash"] != card_candidate["content_hash"]:
        raise SegmentedRepairContractError(
            "REPAIR_BUNDLE_STALE", "card candidate changed"
        )
    expected = {item["target_pointer"]: item for item in repair_bundle["targets"]}
    replacements = repair_response.get("replacements")
    if not isinstance(replacements, list) or {item.get("target_pointer") for item in replacements} != set(expected):
        raise SegmentedRepairContractError(
            "REPAIR_TARGET_EXACT_SET_MISMATCH", "all and only frozen targets are required"
        )
    before_hashes = {
        pointer: canonical_hash(_get(card_candidate, pointer)) for pointer in expected
    }
    if any(before_hashes[pointer] != expected[pointer]["before_hash"] for pointer in expected):
        raise SegmentedRepairContractError("REPAIR_TARGET_STALE", "target bytes changed")
    before_field_hashes = {
        key: canonical_hash(value)
        for key, value in card_candidate.get("fields", {}).items()
    }
    successor = deepcopy(dict(card_candidate))
    successor.pop("content_hash", None)
    for replacement in replacements:
        _set(successor, replacement["target_pointer"], replacement["replacement"])
    successor["parent_card_candidate_hash"] = card_candidate["content_hash"]
    successor["content_hash"] = canonical_hash(
        {key: value for key, value in successor.items() if key != "content_hash"}
    )
    targeted_fields = {
        _pointer_parts(pointer)[1] for pointer in expected if len(_pointer_parts(pointer)) >= 2
    }
    unchanged = {
        key: before_field_hashes[key] == canonical_hash(successor["fields"][key])
        for key in before_field_hashes
        if key not in targeted_fields
    }
    if not all(unchanged.values()):
        raise SegmentedRepairContractError(
            "UNTARGETED_FIELD_CHANGED", repr([key for key, value in unchanged.items() if not value])
        )
    receipt_body = {
        "schema_version": "p06-t06-targeted-repair-application-v1",
        "predecessor_card_hash": card_candidate["content_hash"],
        "successor_card_hash": successor["content_hash"],
        "repair_bundle_hash": repair_bundle["content_hash"],
        "target_pointers": sorted(expected),
        "unchanged_field_hash_checks": unchanged,
        "repair_attempt_count": 1,
    }
    return successor, {**receipt_body, "content_hash": canonical_hash(receipt_body)}


@dataclass
class BoundedRepairLedger:
    producer_model: str
    reviewer_model: str
    events: list[dict[str, Any]] = field(default_factory=list)
    terminal_state: str | None = None

    def __post_init__(self) -> None:
        if _family(self.producer_model) == _family(self.reviewer_model):
            raise SegmentedRepairContractError(
                "PRODUCER_REVIEWER_FAMILY_MUST_DIFFER", "heterogeneous review is required"
            )

    def record_full(self, receipt: Mapping[str, Any]) -> str:
        if self.events:
            raise SegmentedRepairContractError("SECOND_OR_LATE_FULL_REVIEW_FORBIDDEN", "Full must be first and once")
        if receipt.get("review_type") != "FULL" or receipt.get("finish_reason") != "stop":
            raise SegmentedRepairContractError("FULL_REVIEW_RECEIPT_INVALID", repr(receipt))
        verdict = receipt.get("verdict")
        if verdict not in {"PASS", "REPAIRABLE", "FAIL", "NOT_ASSESSED"}:
            raise SegmentedRepairContractError("FULL_REVIEW_VERDICT_INVALID", repr(verdict))
        self.events.append({"stage": "FULL", "receipt_hash": canonical_hash(receipt), "verdict": verdict})
        if verdict == "PASS":
            self.terminal_state = "PASS"
        elif verdict in {"FAIL", "NOT_ASSESSED"}:
            self.terminal_state = verdict
        return verdict

    def record_repair(self, application_receipt: Mapping[str, Any]) -> None:
        if self.terminal_state is not None or [item["stage"] for item in self.events] != ["FULL"] or self.events[0]["verdict"] != "REPAIRABLE":
            raise SegmentedRepairContractError("SECOND_OR_OUT_OF_ORDER_REPAIR_FORBIDDEN", "one repair follows one repairable Full")
        self.events.append({"stage": "REPAIR", "receipt_hash": canonical_hash(application_receipt)})

    def record_delta(self, receipt: Mapping[str, Any]) -> str:
        if self.terminal_state is not None or [item["stage"] for item in self.events] != ["FULL", "REPAIR"]:
            raise SegmentedRepairContractError("SECOND_OR_OUT_OF_ORDER_DELTA_FORBIDDEN", "one Delta follows one repair")
        if receipt.get("review_type") != "DELTA" or receipt.get("finish_reason") != "stop":
            raise SegmentedRepairContractError("DELTA_REVIEW_RECEIPT_INVALID", repr(receipt))
        verdict = receipt.get("verdict")
        if verdict not in {"PASS", "FAIL", "NOT_ASSESSED"}:
            raise SegmentedRepairContractError("DELTA_REVIEW_VERDICT_INVALID", repr(verdict))
        self.events.append({"stage": "DELTA", "receipt_hash": canonical_hash(receipt), "verdict": verdict})
        self.terminal_state = verdict
        return verdict

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": "p06-t06-bounded-repair-ledger-v1",
            "producer_model": self.producer_model,
            "reviewer_model": self.reviewer_model,
            "events": deepcopy(self.events),
            "counts": {
                "full": sum(item["stage"] == "FULL" for item in self.events),
                "repair": sum(item["stage"] == "REPAIR" for item in self.events),
                "delta": sum(item["stage"] == "DELTA" for item in self.events),
            },
            "terminal_state": self.terminal_state,
            "retry_ceiling": 0,
        }


__all__ = [
    "BoundedRepairLedger",
    "SegmentedRepairContractError",
    "apply_targeted_repair",
    "freeze_targeted_repair_bundle",
]
