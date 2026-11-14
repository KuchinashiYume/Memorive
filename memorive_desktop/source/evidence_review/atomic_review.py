"""Provider-neutral Atomic Review View and deterministic pre-review gates."""
from __future__ import annotations

import re
from typing import Any


CORE_FIELDS = (
    "research_question",
    "research_object",
    "method",
    "key_results",
    "author_conclusion",
    "boundary_conditions",
)

ATOMIZATION_REVIEW_REQUIRED = "ATOMIZATION_REVIEW_REQUIRED"
ATOMIZATION_COMPLETE = "ATOMIZATION_COMPLETE"

_FIELD_ATOM = {
    "research_question": "research_relation",
    "research_object": "research_object",
    "method": "method_step",
    "key_results": "conclusion_scope",
    "author_conclusion": "conclusion_scope",
    "boundary_conditions": "applicability",
}
_FIELD_PREFIX = {
    "research_question": "RQ",
    "research_object": "RO",
    "method": "MT",
    "key_results": "KR",
    "author_conclusion": "AC",
    "boundary_conditions": "BC",
}
_PLACEHOLDERS = {
    "", "n/a", "na", "none", "null", "not given", "not available", "unknown",
    "待补充", "暂无", "未知", "未提供",
}
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?:\s*(?:±|→|–|—|\bto\b)\s*[-+]?\d+(?:\.\d+)?)?\s*%?", re.I)
_UNIT = re.compile(
    r"(?i)(?<![A-Za-z])(?:mg|kg|µg|ug|mL|ml|µL|ul|mmol|mol|mbar|bar|kPa|MPa|Pa|atm|°C|℃|hr|hours?|days?|min)(?:\s*/\s*[A-Za-z0-9^−-]+)?(?![A-Za-z])"
)
_PHASE = re.compile(
    r"(?i)(?:Period\s*\d+(?:\s*\([^)]*\))?|Phase\s*\d+(?:\s*\([^)]*\))?|Day\s*\d+(?:\s*(?:–|—|-|to)\s*\d+)?|\b(?:inhibition|recovery|stabili[sz]ation|pre[- ]shock|post[- ]shock)\s+(?:phase|stage)\b)"
)
_SAMPLE_SIZE = re.compile(r"(?i)(?:\bn\s*[=:]\s*|sample size\s*(?:of|[=:])\s*)(\d+)")
_DIRECTION = re.compile(r"(?i)\b(increas(?:e|ed|ing)|decreas(?:e|ed|ing)|higher|lower|rise|rose|fall|fell|positive|negative)\b")
_CLAIM_STRENGTH = re.compile(r"(?i)\b(may|might|could|suggest(?:s|ed)?|associate(?:d|s)?|prove(?:s|d)?|cause(?:s|d)?|eliminate(?:s|d)?|definitive)\b")
_INSTRUMENT = re.compile(r"\b(?:PANDAseq|PERMANOVA|QIIME|UCHIME|UCLUST|MiSeq|HPLC|GC|AER-?200|Acorn\s+pH\s*5)\b", re.I)
_TRUNCATION = re.compile(r"(?i)(?:\[truncated\]|<truncated>|\.\.\.$|…$)")


def split_free_text(text: str) -> tuple[list[str], str]:
    """Split only on visible sentence/list boundaries; preserve uncertain text verbatim."""
    stripped = text.strip()
    if not stripped:
        return [], ATOMIZATION_COMPLETE
    pairs = (("(", ")"), ("[", "]"), ("{", "}"))
    if any(stripped.count(left) != stripped.count(right) for left, right in pairs):
        return [stripped], ATOMIZATION_REVIEW_REQUIRED
    pieces = [piece.strip() for piece in re.split(r"(?<=[.!?])\s+|\s*;\s*|\n\s*(?:[-*]\s*)?", stripped) if piece.strip()]
    if not pieces:
        return [stripped], ATOMIZATION_REVIEW_REQUIRED
    return pieces, ATOMIZATION_COMPLETE


def _paper_id(card: dict[str, Any]) -> str:
    value = card.get("paper_id")
    if not value and isinstance(card.get("source_anchor"), dict):
        value = card["source_anchor"].get("paper_id")
    return value if isinstance(value, str) else ""
