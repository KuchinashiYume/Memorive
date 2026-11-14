"""LOCAL_DISTILL adapter output validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..errors import ContractViolation
from ..types import LogicalRole
from . import InjectedRoleAdapter, Runner


def validate_distill_output(value: Mapping[str, Any]) -> None:
    if not isinstance(value.get("facts"), list):
        raise ContractViolation("DISTILL_FACTS_MISSING")
    if not isinstance(value.get("source_identity"), str) or not value["source_identity"]:
        raise ContractViolation("DISTILL_SOURCE_IDENTITY_MISSING")
    uncertainty = value.get("uncertainty")
    if not isinstance(uncertainty, int | float) or isinstance(uncertainty, bool) or not 0 <= uncertainty <= 1:
        raise ContractViolation("DISTILL_UNCERTAINTY_INVALID")


class DistillAdapter(InjectedRoleAdapter):
    logical_role = LogicalRole.LOCAL_DISTILL

    def __init__(self, runner: Runner) -> None:
        super().__init__(runner, validate_distill_output)
