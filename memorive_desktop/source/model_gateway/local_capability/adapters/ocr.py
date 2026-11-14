"""LOCAL_OCR adapter output validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..errors import ContractViolation
from ..types import LogicalRole
from . import InjectedRoleAdapter, Runner


def validate_ocr_output(value: Mapping[str, Any]) -> None:
    if not isinstance(value.get("transcription"), str):
        raise ContractViolation("OCR_TRANSCRIPTION_MISSING")
    anchors = value.get("anchors")
    if not isinstance(anchors, list) or not anchors:
        raise ContractViolation("OCR_ANCHORS_MISSING")
    if any(not isinstance(row, Mapping) or not row.get("source_id") for row in anchors):
        raise ContractViolation("OCR_ANCHOR_INVALID")
    if not isinstance(value.get("diagnostics"), Mapping):
        raise ContractViolation("OCR_DIAGNOSTICS_MISSING")


class OCRAdapter(InjectedRoleAdapter):
    logical_role = LogicalRole.LOCAL_OCR

    def __init__(self, runner: Runner) -> None:
        super().__init__(runner, validate_ocr_output)
