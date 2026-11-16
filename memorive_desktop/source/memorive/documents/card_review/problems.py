"""The shape of a problem the card review finds, shared by every check: source, severity, kind,
location, field, chunk_id, detail. The mechanical check writes source='mechanical' with
severity='error'; the transcription checks (R1 to R9) use 'transcription' and the second-model
judgments 'gpt'.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Problem:
    source: str                    # 'mechanical', 'transcription' (R1 to R9) or 'gpt'
    severity: str                  # 'error' (others may follow)
    kind: str                      # 'broken_link' or 'malformed_anchor' for the mechanical check
    location: str                  # the exact place: by_field.key_results.chunk_ids[2], key_data[5].chunk_id
    detail: str                    # for people
    field: str | None = None       # key_results / research_object / 'key_data' / None
    chunk_id: str | None = None    # the chunk_id concerned (None when empty or not a string)
    quote: str | None = None       # a short quote of a transcription problem (stored, not checked); None for the mechanical check


@dataclass
class MechanicalResult:
    ok: bool                       # no problems
    problems: list                 # list[Problem]
    checked: int                   # chunk ids looked up (not counting malformed ones)
    resolved: int                  # found
    broken: int                    # broken links
    malformed: int                 # malformed anchors
