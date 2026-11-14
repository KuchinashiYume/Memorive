"""LOCAL_EMBED adapter output validation."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ..errors import ContractViolation
from ..types import LogicalRole
from . import InjectedRoleAdapter, Runner


def validate_embedding_output(value: Mapping[str, Any]) -> None:
    vectors = value.get("vectors")
    dimension = value.get("dimension")
    if not isinstance(dimension, int) or dimension <= 0:
        raise ContractViolation("EMBEDDING_DIMENSION_INVALID")
    if not isinstance(vectors, list) or not vectors:
        raise ContractViolation("EMBEDDING_VECTORS_MISSING")
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != dimension:
            raise ContractViolation("EMBEDDING_VECTOR_DIMENSION_MISMATCH")
        if any(not isinstance(item, int | float) or isinstance(item, bool) or not math.isfinite(item) for item in vector):
            raise ContractViolation("EMBEDDING_VECTOR_NONFINITE")
        norm = math.sqrt(sum(float(item) ** 2 for item in vector))
        if not 0.999 <= norm <= 1.001:
            raise ContractViolation("EMBEDDING_VECTOR_NOT_L2_UNIT")
    if value.get("normalization") != "L2_UNIT":
        raise ContractViolation("EMBEDDING_NORMALIZATION_INVALID")


class EmbeddingAdapter(InjectedRoleAdapter):
    logical_role = LogicalRole.LOCAL_EMBED

    def __init__(self, runner: Runner) -> None:
        super().__init__(runner, validate_embedding_output)
