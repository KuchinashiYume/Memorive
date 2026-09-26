"""Immutable in-memory receipt ledger used by the broker and offline tests."""

from __future__ import annotations

from collections.abc import Iterable

from .contracts import canonical_sha256, validate_contract
from .errors import ReceiptConflict
from .types import LocalExecutionReceipt


class ReceiptLedger:
    def __init__(self) -> None:
        self._receipts: dict[str, LocalExecutionReceipt] = {}
        self._hashes: dict[str, str] = {}

    def record(self, receipt: LocalExecutionReceipt) -> str:
        validate_contract(receipt)
        digest = canonical_sha256(receipt)
        existing = self._hashes.get(receipt.receipt_id)
        if existing is not None and existing != digest:
            raise ReceiptConflict(receipt.receipt_id)
        self._receipts.setdefault(receipt.receipt_id, receipt)
        self._hashes.setdefault(receipt.receipt_id, digest)
        return digest

    def get(self, receipt_id: str) -> LocalExecutionReceipt | None:
        return self._receipts.get(receipt_id)

    def hash_for(self, receipt_id: str) -> str | None:
        return self._hashes.get(receipt_id)

    def all(self) -> Iterable[LocalExecutionReceipt]:
        return tuple(self._receipts[key] for key in sorted(self._receipts))

    def __len__(self) -> int:
        return len(self._receipts)
