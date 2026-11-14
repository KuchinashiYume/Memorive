"""Provider-neutral, injected role adapters.

No adapter in this package imports an HTTP client, provider SDK or Ollama
provider. A caller must inject the bounded worker callable explicitly.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..errors import ContractViolation
from ..types import AdapterResult, LocalExecutionEnvelope, LogicalRole


Runner = Callable[[LocalExecutionEnvelope, Mapping[str, Any]], AdapterResult | Mapping[str, Any]]
Validator = Callable[[Mapping[str, Any]], None]


class InjectedRoleAdapter:
    logical_role: LogicalRole

    def __init__(self, runner: Runner, validator: Validator) -> None:
        if not callable(runner):
            raise TypeError("runner must be callable")
        self._runner = runner
        self._validator = validator
        self.call_count = 0

    def execute(self, envelope: LocalExecutionEnvelope, payload: Mapping[str, Any]) -> AdapterResult:
        if envelope.logical_role is not self.logical_role:
            raise ContractViolation(
                "ADAPTER_ROLE_MISMATCH",
                details={"adapter": self.logical_role.value, "envelope": envelope.logical_role.value},
            )
        self.call_count += 1
        returned = self._runner(envelope, payload)
        result = returned if isinstance(returned, AdapterResult) else AdapterResult(output=dict(returned))
        self._validator(result.output)
        return result


from .analysis import AnalysisAdapter
from .distill import DistillAdapter
from .embedding import EmbeddingAdapter
from .ocr import OCRAdapter
from .retrieval import RetrievalAdapter
from .verify import VerifyAdapter

__all__ = [
    "AnalysisAdapter",
    "DistillAdapter",
    "EmbeddingAdapter",
    "InjectedRoleAdapter",
    "OCRAdapter",
    "RetrievalAdapter",
    "VerifyAdapter",
]
