"""The ErrorEnvelope-v1 payload: how an application error is reported to its caller.

Used by the model configuration errors (memorive/models/gateway/model_configuration/errors.py). It is
a module of its own, not part of errors.py, because it checks its fields with jobs/contracts.py,
which itself raises the errors of errors.py.
"""
from __future__ import annotations

from typing import Any

from memorive.platform.errors import ContractViolation
from memorive.platform.jobs.contracts import CONTRACT_REVISION, require_text


def error_envelope(
    *,
    code: str,
    category: str,
    severity: str,
    retryable: bool,
    user_action: str,
    technical_summary: str,
    correlation_id: str,
    job_id: str | None = None,
    attempt_id: str | None = None,
    retry_after: str | None = None,
    cause_ref: str | None = None,
    support_bundle_recommended: bool = False,
) -> dict[str, Any]:
    if not isinstance(retryable, bool) or not isinstance(support_bundle_recommended, bool):
        raise ContractViolation("ERROR_BOOLEAN_INVALID")
    return {
        "schema_version": "ErrorEnvelope-v1",
        "contract_revision": CONTRACT_REVISION,
        "error_code": require_text(code, "error_code"),
        "category": require_text(category, "category"),
        "severity": require_text(severity, "severity"),
        "retryable": retryable,
        "retry_after": retry_after,
        "user_action": require_text(user_action, "user_action"),
        "technical_summary": require_text(technical_summary, "technical_summary"),
        "correlation_id": require_text(correlation_id, "correlation_id"),
        "job_id": job_id,
        "attempt_id": attempt_id,
        "cause_ref": cause_ref,
        "support_bundle_recommended": support_bundle_recommended,
    }


__all__ = ["error_envelope"]
