"""LOCAL_VERIFY adapter output validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..errors import ContractViolation
from ..types import LogicalRole
from . import InjectedRoleAdapter, Runner


def validate_verify_output(value: Mapping[str, Any]) -> None:
    if not isinstance(value.get("findings"), list):
        raise ContractViolation("VERIFY_FINDINGS_MISSING")
    if value.get("verdict") not in {"PASS", "FAIL", "NOT_ASSESSED"}:
        raise ContractViolation("VERIFY_VERDICT_INVALID")
    if not isinstance(value.get("structural_only"), bool):
        raise ContractViolation("VERIFY_INDEPENDENCE_CLASSIFICATION_MISSING")
    for finding in value["findings"]:
        if not isinstance(finding, Mapping) or finding.get("severity") not in {"INFO", "LOW", "MEDIUM", "HIGH"}:
            raise ContractViolation("VERIFY_FINDING_INVALID")


class VerifyAdapter(InjectedRoleAdapter):
    logical_role = LogicalRole.LOCAL_VERIFY

    def __init__(self, runner: Runner) -> None:
        super().__init__(runner, validate_verify_output)
