"""The gateway's errors. fallback.py sorts failures into four classes for the user (key or quota,
service unavailable, analysis or review model unavailable, transient errors used up) and raises
DependencyError with a message saying which module, which dependency, what went wrong and what to do.
"""
from __future__ import annotations


class GatewayError(Exception):
    """The base class of the gateway's errors."""


class UnknownTaskType(GatewayError):
    """The task_type or slot is not in the configuration."""


class SlotDisabled(GatewayError):
    """The slot is disabled (enabled=false), such as a placeholder slot."""


class HumanSlot(GatewayError):
    """The slot is a person's judgment, not a model; no model call may be made for it."""


class ProviderNotRegistered(GatewayError):
    """No adapter is registered for the configured provider (class 2, service unavailable)."""


class ApiKeyMissing(GatewayError):
    """The environment variable named by api_key_env holds no key (class 1, key or quota)."""

    def __init__(self, task_type: str, env_name: str):
        super().__init__(
            f"[{task_type}] 依赖的环境变量 {env_name} 未设置或为空;"
            f"请设置该 key 后重试(第 4 步将给出分类友好提示)。"
        )
        self.task_type = task_type
        self.env_name = env_name


class EmbedResultInvalid(GatewayError):
    """An embedding reply is invalid (not one vector per input, say). Nothing is written: a vector
    stored against the wrong chunk would silently point every citation at the wrong text."""


class RerankResultInvalid(GatewayError):
    """A rerank reply is invalid (not one score per document, say). It is not used and no success
    is recorded: a score attached to the wrong chunk would silently skew ranking and selection."""


class OcrOwnershipBlocked(GatewayError):
    """A cloud OCR call was stopped because the data is entrusted: it may not be encoded, uploaded or sent."""


class OcrResultInvalid(GatewayError):
    """An OCR reply is empty, names another page or source hash, or lacks its reply hash; it is not
    used and no success is recorded."""


class InvalidHealthcheckTarget(GatewayError):
    """healthcheck was called for a slot other than embed_local, the only one it serves. A
    GatewayError, so callers handle it with the gateway's other errors."""


class NonLocalEmbedEndpoint(GatewayError):
    """The local embedding endpoint (Ollama) is not a loopback address (localhost, 127.0.0.1, ::1).
    Entrusted data may go only to this computer, so a remote endpoint, configured by mistake or
    changed, is refused in code rather than trusted from models.yaml."""


class DependencyError(GatewayError):
    """A dependency of the gateway is unavailable. Carries the user class (error_class), the
    underlying root_class and a four-part message; callers show the message instead of a traceback."""

    def __init__(self, error_class: str, root_class: str, task_type: str, message: str):
        super().__init__(message)
        self.error_class = error_class    # key_or_quota / service_unavailable / analysis_verify_unavailable / transient_exhausted
        self.root_class = root_class      # auth_or_quota / service_unavailable / transient_exhausted / slot_disabled
        self.task_type = task_type
        self.message = message
