"""Bounded Full -> one targeted repair -> Delta adapter for LONG-DOCUMENT.

This module owns only the Verification orchestration contract.  It keeps the existing
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
            "schema_version": "long_document-bounded-repair-ledger-v1",
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
]
