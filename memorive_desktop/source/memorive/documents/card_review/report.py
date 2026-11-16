"""Evidence review reports (verification step 2): run output in the ops area, never committed or searched.

The admission log keeps only a `report=<report_id>` link (not the Problem[] list); the full details
(Problem[], same-source confidence, escalation to a cross-source review) are in this report, and
resolve_report finds a report from that link.
Location: `<MEMORIVE_EVIDENCE_REVIEW_REPORT_DIR>/{report_id}.json`, by default the
evidence_review_reports folder of the ops area, next to the admission log and the runtime log
(memorive/platform/data_folders.py, ops_root).
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from memorive.platform.data_folders import ops_root

ENV_EVIDENCE_REVIEW_REPORT_DIR = "MEMORIVE_EVIDENCE_REVIEW_REPORT_DIR"

# Confidence: a model checking a card from its own vendor is weaker evidence
CONFIDENCE_SAME_SOURCE = "same_source_low"           # the checking model and the card's distill_model share a vendor or family
CONFIDENCE_CROSS_SOURCE = "cross_source"             # different vendors (cards distilled by DeepSeek, checked by Anthropic)


def _report_dir() -> Path:
    return Path(os.environ.get(ENV_EVIDENCE_REVIEW_REPORT_DIR) or ops_root() / "evidence_review_reports")


def _now_iso() -> str:
    """Local time with its offset, to the second, as the card state log and the runtime log write it."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe(s) -> str:
    """Safe for a file name: letters, digits, underscore and hyphen stay, anything else becomes _, so a
    separator, space or colon in paper_id cannot get into report_id and from there into a path."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(s)) or "unknown"


def gen_report_id(paper_id: str) -> str:
    """{safe(paper_id)}-EVIDENCE_REVIEW-{YYYYMMDDTHHMMSS}-{shorthex}; shorthex keeps two reruns in the
    same second apart."""
    ts = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    return f"{_safe(paper_id)}-EVIDENCE_REVIEW-{ts}-{uuid.uuid4().hex[:8]}"


@dataclass
class EvidenceReviewReport:
    """The full report of one transcription check. problems is a list of Problem (serialized with asdict)."""
    report_id: str
    paper_id: str
    card: str                          # the card's path
    verdict: str                       # "PASS" / "FAIL"
    problems: list                     # list[Problem]
    confidence: str                    # same_source_low / cross_source
    same_source_reason: str | None     # why it counts as the same source, when it does ("distill=deepseek / verify=deepseek", say)
    upgrade_pending: list              # core fields still to check with another vendor (["key_data values", "author_conclusion"], say)
    distill_model: str | None          # the card's distill_model (a top-level front matter key)
    verify_model: str | None           # the model of this check (the verify slot)
    checked_at: str                    # local ISO time with the offset
    checked_count: int = 0             # subjects actually checked against the source; 0 means none, and the card is not admitted
    checked_locations: list = field(default_factory=list)  # the locations actually checked; a field or key_data entry not listed is unknown, not verified


def report_path(report_id: str) -> Path:
    return _report_dir() / f"{report_id}.json"


def write_report(report: EvidenceReviewReport) -> str:
    """Write the report JSON and return report_id; a temporary file and os.replace, so there is never
    half a JSON file."""
    d = _report_dir()
    d.mkdir(parents=True, exist_ok=True)
    final = report_path(report.report_id)
    tmp = d / f".{report.report_id}.json.tmp"
    tmp.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, final)                      # an atomic replace on the same disk: no half-written file
    return report.report_id


def resolve_report(report_id: str) -> Path:
    """The report file of report=<id>. Refuses /, \\ and .., and the resolved path must stay inside the
    report folder."""
    if not isinstance(report_id, str) or not report_id.strip():
        raise ValueError(f"非法 report_id(空 / 非字符串): {report_id!r}")
    if "/" in report_id or "\\" in report_id or ".." in report_id:
        raise ValueError(f"非法 report_id(含 / \\ .. — 防路径穿越): {report_id!r}")
    base = _report_dir().resolve()
    p = (base / f"{report_id}.json").resolve()
    if not p.is_relative_to(base):              # outside the report folder after resolving: refused
        raise ValueError(f"report_id 解析越出报告目录(路径穿越): {report_id!r}")
    if not p.exists():
        raise FileNotFoundError(f"EVIDENCE_REVIEW 校核报告未找到: {report_id}({p})")
    return p


def read_report(report_id: str) -> dict:
    """The report as a dict, for the review view."""
    return json.loads(resolve_report(report_id).read_text(encoding="utf-8"))
