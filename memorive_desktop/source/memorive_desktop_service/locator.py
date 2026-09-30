from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import quote, unquote, urlparse

from .errors import ProtocolViolation


LOCATOR_KINDS = frozenset({"job", "attempt", "artifact", "event", "action"})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")


def _accepted_id(value: str, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ProtocolViolation(f"LOCATOR_{field.upper()}_INVALID")
    return value


@dataclass(frozen=True)
class StableLocator:
    kind: str
    object_id: str
    job_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in LOCATOR_KINDS:
            raise ProtocolViolation("LOCATOR_KIND_INVALID")
        _accepted_id(self.object_id, "object_id")
        if self.kind in {"artifact", "event", "action"}:
            if self.job_id is None:
                raise ProtocolViolation("LOCATOR_JOB_ID_REQUIRED")
            _accepted_id(self.job_id, "job_id")
        elif self.job_id is not None:
            raise ProtocolViolation("LOCATOR_JOB_ID_FORBIDDEN")

    def render(self) -> str:
        parts = [quote(self.object_id, safe="._:-")]
        if self.job_id is not None:
            parts.insert(0, quote(self.job_id, safe="._:-"))
        return f"memorive://{self.kind}/" + "/".join(parts)

    @classmethod
    def parse(cls, value: str) -> "StableLocator":
        if not isinstance(value, str) or len(value) > 512:
            raise ProtocolViolation("LOCATOR_INVALID")
        parsed = urlparse(value)
        if parsed.scheme != "memorive" or parsed.netloc not in LOCATOR_KINDS:
            raise ProtocolViolation("LOCATOR_SCHEME_OR_KIND_INVALID")
        if parsed.params or parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port:
            raise ProtocolViolation("LOCATOR_EXTRA_COMPONENT_FORBIDDEN")
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if parsed.netloc in {"job", "attempt"} and len(parts) == 1:
            result = cls(parsed.netloc, parts[0])
        elif parsed.netloc in {"artifact", "event", "action"} and len(parts) == 2:
            result = cls(parsed.netloc, parts[1], job_id=parts[0])
        else:
            raise ProtocolViolation("LOCATOR_PATH_SHAPE_INVALID")
        if result.render() != value:
            raise ProtocolViolation("LOCATOR_NOT_CANONICAL")
        return result


def route_for_locator(locator: StableLocator) -> str:
    return {
        "job": "current-task",
        "attempt": "current-task",
        "artifact": "library",
        "event": "work-log",
        "action": "messages",
    }[locator.kind]


class StableLocatorResolver:
    def __init__(self, facade: Any):
        self.facade = facade

    def resolve(self, rendered: str) -> dict[str, Any]:
        locator = StableLocator.parse(rendered)
        if locator.kind == "job":
            value = self.facade.get_job(locator.object_id)
        elif locator.kind == "attempt":
            value = self.facade.get_attempt(locator.object_id)
        elif locator.kind == "artifact":
            rows = self.facade.get_artifact_bindings(locator.job_id)["artifacts"]
            value = next((row for row in rows if row.get("artifact_id") == locator.object_id), None)
        elif locator.kind == "event":
            rows = self.facade.get_job_events(locator.job_id, after_sequence=0)["events"]
            value = next(
                (
                    row
                    for row in rows
                    if row.get("event_id") == locator.object_id
                    or f"event_{int(row.get('sequence', -1)):08d}" == locator.object_id
                ),
                None,
            )
        else:
            rows = self.facade.list_action_requests(locator.job_id)["action_requests"]
            value = next((row for row in rows if row.get("action_request_id") == locator.object_id), None)
        if value is None:
            raise ProtocolViolation("LOCATOR_TARGET_NOT_FOUND")
        return {
            "schema_version": "StableLocatorResolution-v1",
            "locator": rendered,
            "kind": locator.kind,
            "object_id": locator.object_id,
            "job_id": locator.job_id or value.get("job_id"),
            "route": route_for_locator(locator),
            "target": value,
        }


__all__ = ["LOCATOR_KINDS", "StableLocator", "StableLocatorResolver", "route_for_locator"]
