from __future__ import annotations

from typing import Any


class ApplicationError(RuntimeError):
    """Stable public application error; raw exceptions stay behind cause refs."""

    code = "APPLICATION_ERROR"
    category = "internal"
    retryable = False

    def __init__(self, message: str | None = None, **context: Any):
        self.context = dict(context)
        super().__init__(message or self.code)


class ContractViolation(ApplicationError, ValueError):
    code = "CONTRACT_VIOLATION"
    category = "validation"


class InvalidTransition(ContractViolation):
    code = "JOB_STATE_TRANSITION_INVALID"


class JobNotFound(ApplicationError, LookupError):
    code = "JOB_NOT_FOUND"
    category = "execution"


class IdempotencyConflict(ApplicationError):
    code = "IDEMPOTENCY_KEY_CONFLICT"
    category = "concurrency"


class VersionConflict(ApplicationError):
    code = "EXPECTED_VERSION_CONFLICT"
    category = "concurrency"
    retryable = True


class ConcurrentWriter(ApplicationError):
    code = "ACTIVE_WRITER_PRESENT"
    category = "concurrency"
    retryable = True


class CorruptEventLog(ApplicationError):
    code = "CORRUPT_EVENT_LOG"
    category = "recovery"


class PauseUnsupported(ApplicationError):
    code = "PAUSE_UNSUPPORTED"
    category = "execution"


class ResumeInputDrift(ApplicationError):
    code = "RESUME_INPUT_DRIFT"
    category = "recovery"


class CapabilityBlocked(ApplicationError):
    code = "CAPABILITY_BLOCKED"
    category = "capability"

    def __init__(self, job_id: str, blockers: list[str]):
        self.job_id = job_id
        self.blockers = tuple(blockers)
        super().__init__(f"required capabilities blocked: {', '.join(blockers)}")


class SupportBundleForbidden(ApplicationError):
    code = "SUPPORT_BUNDLE_FORBIDDEN_CONTENT"
    category = "evidence"


ERROR_CODE_CATALOG = {
    "PARAMETER_INVALID": {"category": "validation", "retryable": False},
    "CAPABILITY_NOT_CONFIGURED": {"category": "config", "retryable": False},
    "CAPABILITY_DISABLED": {"category": "capability", "retryable": False},
    "QUALIFICATION_NOT_ASSESSED": {"category": "capability", "retryable": False},
    "DEPENDENCY_BLOCKED": {"category": "capability", "retryable": False},
    "IDEMPOTENCY_KEY_CONFLICT": {"category": "concurrency", "retryable": False},
    "EXPECTED_VERSION_CONFLICT": {"category": "concurrency", "retryable": True},
    "ACTIVE_WRITER_PRESENT": {"category": "concurrency", "retryable": True},
    "PAUSE_UNSUPPORTED": {"category": "execution", "retryable": False},
    "CANCEL_PENDING_SAFE_POINT": {"category": "execution", "retryable": True},
    "RECOVERY_EVIDENCE_INSUFFICIENT": {"category": "recovery", "retryable": False},
    "RESUME_INPUT_DRIFT": {"category": "recovery", "retryable": False},
    "SCHEMA_INCOMPATIBLE": {"category": "validation", "retryable": False},
    "DISK_SPACE_INSUFFICIENT": {"category": "execution", "retryable": True},
    "PERMISSION_DENIED": {"category": "execution", "retryable": False},
    "INTERNAL_UNCLASSIFIED": {"category": "internal", "retryable": False},
}


__all__ = [
    "ApplicationError",
    "CapabilityBlocked",
    "ConcurrentWriter",
    "ContractViolation",
    "CorruptEventLog",
    "ERROR_CODE_CATALOG",
    "IdempotencyConflict",
    "InvalidTransition",
    "JobNotFound",
    "PauseUnsupported",
    "ResumeInputDrift",
    "SupportBundleForbidden",
    "VersionConflict",
]
