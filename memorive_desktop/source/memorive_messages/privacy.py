from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping


_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(?:^|[^A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)(?:authorization|cookie|password|secret|token)\s*[:=]\s*\S+"),
)
_SENSITIVE_KEY = re.compile(
    r"(^|_)(api[_-]?key|authorization|cookie|credential|password|secret|token|private[_-]?payload|gold|holdout)($|_)",
    re.IGNORECASE,
)


class PrivacyBoundaryError(ValueError):
    pass


@dataclass(frozen=True)
class RedactionPolicy:
    forbidden_fragments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value for value in self.forbidden_fragments):
            raise PrivacyBoundaryError("REDACTION_FORBIDDEN_FRAGMENT_INVALID")

    def contains_sensitive(self, value: str) -> bool:
        if any(fragment in value for fragment in self.forbidden_fragments):
            return True
        return any(pattern.search(value) for pattern in _SENSITIVE_PATTERNS)

    def sanitize(self, value: Any, *, fallback: str, maximum: int) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            return fallback
        if self.contains_sensitive(value):
            return fallback
        return value

    def assert_public_safe(self, value: Any, path: str = "value") -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                rendered = str(key)
                if _SENSITIVE_KEY.search(rendered):
                    raise PrivacyBoundaryError(f"SENSITIVE_KEY:{path}.{rendered}")
                self.assert_public_safe(child, f"{path}.{rendered}")
        elif isinstance(value, list) or isinstance(value, tuple):
            for index, child in enumerate(value):
                self.assert_public_safe(child, f"{path}[{index}]")
        elif isinstance(value, str) and self.contains_sensitive(value):
            raise PrivacyBoundaryError(f"SENSITIVE_VALUE:{path}")

    def scan_surfaces(self, surfaces: Mapping[str, str]) -> dict[str, Any]:
        failures = sorted(
            name for name, value in surfaces.items() if not isinstance(value, str) or self.contains_sensitive(value)
        )
        return {
            "schema_version": "MessagesCanaryRedactionReceipt-v1",
            "surface_count": len(surfaces),
            "surfaces": sorted(surfaces),
            "failure_count": len(failures),
            "failures": failures,
            "raw_values_recorded": False,
            "status": "PASS" if not failures else "FAIL",
        }


__all__ = ["PrivacyBoundaryError", "RedactionPolicy"]
