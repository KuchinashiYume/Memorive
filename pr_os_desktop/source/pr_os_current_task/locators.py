from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import quote, unquote, urlparse


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
KINDS = frozenset({"job", "attempt", "step", "event", "action", "artifact"})
PUBLIC_SAFE_PREFIXES = ("syn-", "job_fixture_", "attempt_fixture_", "action_fixture_", "artifact_fixture_", "event_fixture_")


class LocatorError(ValueError):
    pass


def _id(value: str, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise LocatorError(f"CURRENT_TASK_LOCATOR_{field.upper()}_INVALID")
    return value


@dataclass(frozen=True)
class CurrentTaskLocator:
    kind: str
    object_id: str
    job_id: str | None = None
    attempt_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise LocatorError("CURRENT_TASK_LOCATOR_KIND_INVALID")
        _id(self.object_id, "object_id")
        if self.kind in {"step"}:
            if self.job_id is None or self.attempt_id is None:
                raise LocatorError("CURRENT_TASK_LOCATOR_CONTEXT_REQUIRED")
            _id(self.job_id, "job_id")
            _id(self.attempt_id, "attempt_id")
        elif self.kind in {"event", "action", "artifact"}:
            if self.job_id is None:
                raise LocatorError("CURRENT_TASK_LOCATOR_JOB_REQUIRED")
            _id(self.job_id, "job_id")
            if self.attempt_id is not None:
                raise LocatorError("CURRENT_TASK_LOCATOR_ATTEMPT_FORBIDDEN")
        elif self.job_id is not None or self.attempt_id is not None:
            raise LocatorError("CURRENT_TASK_LOCATOR_EXTRA_CONTEXT")

    def render(self) -> str:
        if self.kind == "job":
            return f"pr-os://job/{quote(self.object_id, safe='._:-')}"
        if self.kind == "attempt":
            return f"pr-os://attempt/{quote(self.object_id, safe='._:-')}"
        if self.kind in {"event", "action", "artifact"}:
            return f"pr-os://{self.kind}/{quote(str(self.job_id), safe='._:-')}/{quote(self.object_id, safe='._:-')}"
        return (
            "pr-os-ui://step/"
            + "/".join(quote(value, safe="._:-") for value in (str(self.job_id), str(self.attempt_id), self.object_id))
        )

    @classmethod
    def parse(cls, value: str) -> "CurrentTaskLocator":
        if not isinstance(value, str) or len(value) > 768:
            raise LocatorError("CURRENT_TASK_LOCATOR_INVALID")
        parsed = urlparse(value)
        if parsed.params or parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port:
            raise LocatorError("CURRENT_TASK_LOCATOR_EXTRA_COMPONENT")
        parts = [unquote(row) for row in parsed.path.split("/") if row]
        if parsed.scheme == "pr-os" and parsed.netloc in {"job", "attempt"} and len(parts) == 1:
            result = cls(parsed.netloc, parts[0])
        elif parsed.scheme == "pr-os" and parsed.netloc in {"event", "action", "artifact"} and len(parts) == 2:
            result = cls(parsed.netloc, parts[1], job_id=parts[0])
        elif parsed.scheme == "pr-os-ui" and parsed.netloc == "step" and len(parts) == 3:
            result = cls("step", parts[2], job_id=parts[0], attempt_id=parts[1])
        else:
            raise LocatorError("CURRENT_TASK_LOCATOR_SHAPE_INVALID")
        if result.render() != value:
            raise LocatorError("CURRENT_TASK_LOCATOR_NOT_CANONICAL")
        return result

    def assert_public_safe(self) -> None:
        values = [self.object_id]
        if self.job_id is not None:
            values.append(self.job_id)
        if self.attempt_id is not None:
            values.append(self.attempt_id)
        if not all(value.startswith(PUBLIC_SAFE_PREFIXES) or value[:2].isdigit() for value in values):
            raise LocatorError("PRODUCTION_LOCATOR_FORBIDDEN")

    def assert_context(self, *, job_id: str, attempt_id: str | None = None) -> None:
        expected_job = _id(job_id, "expected_job_id")
        if self.kind in {"event", "action", "artifact", "step"} and self.job_id != expected_job:
            raise LocatorError("LOCATOR_WRONG_JOB")
        if self.kind == "job" and self.object_id != expected_job:
            raise LocatorError("LOCATOR_WRONG_JOB")
        if attempt_id is not None:
            expected_attempt = _id(attempt_id, "expected_attempt_id")
            if self.kind == "step" and self.attempt_id != expected_attempt:
                raise LocatorError("LOCATOR_WRONG_ATTEMPT")
            if self.kind == "attempt" and self.object_id != expected_attempt:
                raise LocatorError("LOCATOR_WRONG_ATTEMPT")
