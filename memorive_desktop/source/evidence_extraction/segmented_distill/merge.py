"""Deterministic, paper-scoped fact merge and three-axis assessment."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .contracts import SegmentedDistillContractError, canonical_hash


SLOT_STATES = {
    "present",
    "absent_in_source",
    "not_applicable",
    "extraction_gap",
    "not_assessed",
}


__all__ = ["SLOT_STATES"]
