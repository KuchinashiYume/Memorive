from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import Any, Iterable, Mapping

from .errors import BackpressureExceeded, ProtocolViolation


class EventCursorReconciler:
    """Deduplicate and order event pages while keeping a bounded UI buffer."""

    def __init__(self, *, after_sequence: int = 0, max_buffer: int = 256):
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
            raise ValueError("EVENT_CURSOR_INVALID")
        if isinstance(max_buffer, bool) or not isinstance(max_buffer, int) or max_buffer <= 0:
            raise ValueError("EVENT_BUFFER_INVALID")
        self.cursor = after_sequence
        self.max_buffer = max_buffer
        self._buffer: deque[dict[str, Any]] = deque()
        self._seen: set[int] = set(range(1, after_sequence + 1))

    def ingest(self, events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        candidates: dict[int, dict[str, Any]] = {}
        duplicate_count = 0
        for raw in events:
            if not isinstance(raw, Mapping):
                raise ProtocolViolation("EVENT_SHAPE_INVALID")
            sequence = raw.get("sequence")
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
                raise ProtocolViolation("EVENT_SEQUENCE_INVALID")
            if sequence in self._seen or sequence in candidates:
                duplicate_count += 1
                continue
            candidates[sequence] = deepcopy(dict(raw))
        ordered = [candidates[key] for key in sorted(candidates)]
        if len(self._buffer) + len(ordered) > self.max_buffer:
            raise BackpressureExceeded(
                "watch buffer would overflow",
                buffer_size=len(self._buffer),
                incoming=len(ordered),
                max_buffer=self.max_buffer,
            )
        for event in ordered:
            self._buffer.append(event)
            self._seen.add(event["sequence"])
        contiguous = self.cursor
        while contiguous + 1 in self._seen:
            contiguous += 1
        gap_detected = bool(ordered and ordered[0]["sequence"] > self.cursor + 1)
        self.cursor = contiguous
        return {
            "schema_version": "EventCursorReconciliationReceipt-v1",
            "accepted_count": len(ordered),
            "duplicate_count": duplicate_count,
            "out_of_order_input": [row["sequence"] for row in ordered] != [row.get("sequence") for row in events]
            if isinstance(events, list)
            else False,
            "gap_detected": gap_detected,
            "cursor": self.cursor,
            "buffer_size": len(self._buffer),
            "max_buffer": self.max_buffer,
        }

    def drain(self, limit: int | None = None) -> list[dict[str, Any]]:
        accepted_limit = len(self._buffer) if limit is None else limit
        if isinstance(accepted_limit, bool) or not isinstance(accepted_limit, int) or accepted_limit < 0:
            raise ValueError("EVENT_DRAIN_LIMIT_INVALID")
        rows = []
        while self._buffer and len(rows) < accepted_limit:
            rows.append(self._buffer.popleft())
        return deepcopy(rows)


__all__ = ["EventCursorReconciler"]
