"""A ledger of reviews that could not be done yet because no suitable verifier model was available
(record_pending_judgment). judgment.py writes to it.

When no eligible verifier is available, the research analysis result remains a suggestion rather
than a blocked pipeline item.  This append-only operations ledger keeps the
uncompleted review visible outside the runtime log without persisting source chunks or keys.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from memorive.platform.data_folders import ops_root

ENV_DEBT_DIR = "MEMORIVE_EVIDENCE_REVIEW_JUDGMENT_DEBT_DIR"
DEBT_FILENAME = "evidence_review_judgment_cross_source_pending.jsonl"


def _path() -> Path:
    return Path(os.environ.get(ENV_DEBT_DIR) or ops_root() / "evidence_review_judgment_debt") / DEBT_FILENAME


def record_pending_judgment(*, target: dict, producer_model: str | None,
                            producer_provider: str | None, deferred: list,
                            failures: list) -> dict:
    """Append one unresolved RESEARCH_ANALYSIS judgment-review task and return its public reference."""
    material = json.dumps(
        {"target": target, "producer_model": producer_model, "deferred": deferred},
        ensure_ascii=False, sort_keys=True, default=str,
    )
    debt_id = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    entry = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "debt_id": debt_id,
        "kind": "evidence_review_judgment_cross_source_pending",
        "status": "未异源核·待核",
        "target": target,
        "producer_model": producer_model,
        "producer_provider": producer_provider,
        "deferred": deferred,
        "failures": failures,
    }
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return {"debt_id": debt_id, "path": str(path), "count": len(deferred)}
