from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


BROWSER_CAPTURE_SOURCE_LEGACY_V3 = (
    "BROWSER_COMPANION_VISIBLE_UI_LIBRARY_V3_LEGACY"
)
BROWSER_CAPTURE_SOURCE_DEPLOYED_DRAFT = (
    "BROWSER_COMPANION_VISIBLE_UI_LIBRARY_V4_DRAFT"
)
BROWSER_CAPTURE_SOURCE_STRUCTURED_DOM_V4 = (
    "BROWSER_COMPANION_VISIBLE_UI_LIBRARY_V4_STRUCTURED_DOM"
)
BROWSER_CAPTURE_SOURCE_ACTIVE_TAB_V1 = (
    "BROWSER_COMPANION_ACTIVE_TAB_VISIBLE_DOM_V1"
)
BROWSER_CAPTURE_STRUCTURED_DOM_PREDECESSORS = frozenset(
    {
        BROWSER_CAPTURE_SOURCE_LEGACY_V3,
        BROWSER_CAPTURE_SOURCE_DEPLOYED_DRAFT,
    }
)
BROWSER_CAPTURE_SOURCE_KINDS = frozenset(
    {
        *BROWSER_CAPTURE_STRUCTURED_DOM_PREDECESSORS,
        BROWSER_CAPTURE_SOURCE_STRUCTURED_DOM_V4,
        BROWSER_CAPTURE_SOURCE_ACTIVE_TAB_V1,
    }
)


class ArchiveInputError(ValueError):
    """Raised when an import source violates the bounded archive contract."""


@dataclass(frozen=True)
class NormalizedMessage:
    message_id: str
    role: str
    content_kind: str
    content: str
    occurred_at: str

    def as_dict(self) -> dict[str, str]:
        return {
            "message_id": self.message_id,
            "role": self.role,
            "content_kind": self.content_kind,
            "content": self.content,
            "occurred_at": self.occurred_at,
        }


@dataclass(frozen=True)
class NormalizedConversation:
    provider: str
    provider_conversation_id: str
    title: str
    created_at: str
    updated_at: str
    messages: tuple[NormalizedMessage, ...]
    completeness: str
    source_locator: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "provider_conversation_id": self.provider_conversation_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": [message.as_dict() for message in self.messages],
            "completeness": self.completeness,
            "source_locator": self.source_locator,
        }


@dataclass(frozen=True)
class ImportFailure:
    provider_conversation_id: str
    title: str
    error_code: str


@dataclass(frozen=True)
class ImportBatch:
    provider: str
    source_kind: str
    generated_at: str
    expected_count: int
    conversations: tuple[NormalizedConversation, ...]
    failures: tuple[ImportFailure, ...] = field(default_factory=tuple)
    source_path: Path = Path(".")
    raw_bytes: bytes = b""
