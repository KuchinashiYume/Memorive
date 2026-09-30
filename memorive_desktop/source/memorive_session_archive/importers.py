from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable
from urllib.parse import urlparse, urlsplit

from memorive_folder_management.policy import windows_io_path

from .models import (
    ArchiveInputError,
    BROWSER_CAPTURE_SOURCE_ACTIVE_TAB_V1,
    BROWSER_CAPTURE_SOURCE_DEPLOYED_DRAFT,
    BROWSER_CAPTURE_SOURCE_LEGACY_V3,
    BROWSER_CAPTURE_SOURCE_STRUCTURED_DOM_V4,
    ImportBatch,
    ImportFailure,
    NormalizedConversation,
    NormalizedMessage,
)


_FORBIDDEN_CREDENTIAL_KEYS = {
    "accesstoken",
    "authorization",
    "apikey",
    "browserstorage",
    "cookie",
    "cookies",
    "localstorage",
    "password",
    "refreshtoken",
    "sessionstorage",
    "token",
}

_PROVIDER_HOSTS = {
    "deepseek": {"chat.deepseek.com", "www.deepseek.com", "deepseek.com"},
    "gemini": {"gemini.google.com"},
    "kimi": {"www.kimi.com", "kimi.com", "kimi.moonshot.cn"},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> tuple[Any, bytes]:
    try:
        raw = windows_io_path(path).read_bytes()
        return json.loads(raw.decode("utf-8-sig")), raw
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArchiveInputError(f"SOURCE_JSON_INVALID: {exc}") from exc


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArchiveInputError(f"FIELD_REQUIRED: {field_name}")
    return value.strip()


def _normalized_visible_message_content(value: Any) -> str:
    content = _required_text(value, "content").replace("\r\n", "\n").replace("\r", "\n")
    normalized: list[str] = []
    pending_bullet = False

    def last_text_index() -> int | None:
        for index in range(len(normalized) - 1, -1, -1):
            if normalized[index]:
                return index
        return None

    for raw_line in content.split("\n"):
        line = raw_line.rstrip()
        trimmed = line.strip()
        if trimmed in {"•", "·", "▪", "◦"}:
            pending_bullet = True
            continue
        if not trimmed:
            if not pending_bullet and normalized and normalized[-1] != "":
                normalized.append("")
            continue
        if re.fullmatch(r"[，。！？；：,.!?;:]", trimmed):
            index = last_text_index()
            if index is not None:
                normalized[index] = f"{normalized[index]}{trimmed}"
            continue
        normalized.append(f"• {trimmed}" if pending_bullet else line.lstrip())
        pending_bullet = False
    return "\n".join(normalized).strip()


def _iso_timestamp(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArchiveInputError(f"TIME_INVALID: {field_name}") from exc
    if parsed.tzinfo is None:
        raise ArchiveInputError(f"TIMEZONE_REQUIRED: {field_name}")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _browser_timestamp(value: Any, field_name: str) -> str:
    if isinstance(value, str) and value.strip().upper() == "UNKNOWN":
        return "UNKNOWN"
    return _iso_timestamp(value, field_name)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _find_forbidden_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in _FORBIDDEN_CREDENTIAL_KEYS:
                return str(key)
            found = _find_forbidden_key(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_forbidden_key(child)
            if found:
                return found
    return None


def _extract_text_values(value: Any) -> list[str]:
    """Best-effort extraction for exported Gemini userInteractions variants."""
    result: list[str] = []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return result
        if stripped[:1] in "[{":
            try:
                return _extract_text_values(json.loads(stripped))
            except json.JSONDecodeError:
                pass
        result.append(stripped)
    elif isinstance(value, list):
        for child in value:
            result.extend(_extract_text_values(child))
    elif isinstance(value, dict):
        preferred = ("text", "value", "prompt", "response", "content")
        matched = False
        for key in preferred:
            if key in value:
                result.extend(_extract_text_values(value[key]))
                matched = True
        if not matched:
            for child in value.values():
                result.extend(_extract_text_values(child))
    return result


class DeepSeekOfficialImporter:
    @staticmethod
    def parse(path: Path) -> ImportBatch:
        source_path = Path(path)
        payload, raw = _load_json(source_path)
        if not isinstance(payload, list):
            raise ArchiveInputError("DEEPSEEK_EXPORT_TOP_LEVEL_NOT_LIST")
        if not payload:
            raise ArchiveInputError("DEEPSEEK_EXPORT_EMPTY")

        conversations: list[NormalizedConversation] = []
        for item in payload:
            if not isinstance(item, dict):
                raise ArchiveInputError("DEEPSEEK_CONVERSATION_INVALID")
            conversation_id = _required_text(item.get("id"), "id")
            title = _required_text(item.get("title"), "title")
            created_at = _iso_timestamp(item.get("inserted_at"), "inserted_at")
            updated_at = _iso_timestamp(
                item.get("updated_at") or created_at, "updated_at"
            )
            mapping = item.get("mapping")
            if not isinstance(mapping, dict):
                raise ArchiveInputError("DEEPSEEK_MAPPING_INVALID")
            messages = DeepSeekOfficialImporter._walk_mapping(mapping, created_at)
            roles = {(message.role, message.content_kind) for message in messages}
            completeness = (
                "COMPLETE"
                if any(role == "user" for role, _ in roles)
                and ("assistant", "text") in roles
                else "PARTIAL"
            )
            conversations.append(
                NormalizedConversation(
                    provider="deepseek",
                    provider_conversation_id=conversation_id,
                    title=title,
                    created_at=created_at,
                    updated_at=updated_at,
                    messages=tuple(messages),
                    completeness=completeness,
                    source_locator=f"deepseek-export:{conversation_id}",
                )
            )

        generated_at = max(
            (conversation.updated_at for conversation in conversations),
            default=_utc_now(),
        )
        return ImportBatch(
            provider="deepseek",
            source_kind="DEEPSEEK_OFFICIAL_EXPORT",
            generated_at=generated_at,
            expected_count=len(payload),
            conversations=tuple(conversations),
            source_path=source_path,
            raw_bytes=raw,
        )

    @staticmethod
    def _walk_mapping(mapping: dict[str, Any], fallback_time: str) -> list[NormalizedMessage]:
        root_id = "root" if "root" in mapping else ""
        if not root_id:
            for key, node in mapping.items():
                if isinstance(node, dict) and node.get("parent") is None:
                    root_id = str(key)
                    break
        if not root_id:
            raise ArchiveInputError("DEEPSEEK_MAPPING_ROOT_MISSING")

        paths: list[list[str]] = []

        def visit(node_id: str, path: list[str], active: set[str]) -> None:
            if node_id in active:
                raise ArchiveInputError("DEEPSEEK_MAPPING_CYCLE")
            if len(path) >= 20000 or len(paths) >= 10000:
                raise ArchiveInputError("DEEPSEEK_MAPPING_LIMIT_EXCEEDED")
            node = mapping.get(node_id)
            if not isinstance(node, dict):
                raise ArchiveInputError("DEEPSEEK_MAPPING_NODE_INVALID")
            children_value = node.get("children")
            children = (
                [str(child) for child in children_value]
                if isinstance(children_value, list)
                else []
            )
            next_path = [*path, node_id]
            if not children:
                paths.append(next_path)
                return
            next_active = {*active, node_id}
            for child in children:
                visit(child, next_path, next_active)

        visit(root_id, [], set())
        if not paths:
            raise ArchiveInputError("DEEPSEEK_MAPPING_LEAF_MISSING")

        def path_score(path: list[str]) -> tuple[str, int, str]:
            latest = fallback_time
            for candidate_id in path:
                candidate = mapping[candidate_id]
                message = candidate.get("message")
                if isinstance(message, dict) and message.get("inserted_at"):
                    occurred_at = _iso_timestamp(
                        message.get("inserted_at"), "message.inserted_at"
                    )
                    latest = max(latest, occurred_at)
            return latest, len(path), path[-1]

        selected_path = max(paths, key=path_score)
        result: list[NormalizedMessage] = []
        for node_id in selected_path:
            node = mapping.get(node_id)
            if not isinstance(node, dict):
                raise ArchiveInputError("DEEPSEEK_MAPPING_NODE_INVALID")
            message = node.get("message")
            if isinstance(message, dict):
                occurred_at = _iso_timestamp(
                    message.get("inserted_at") or fallback_time,
                    "message.inserted_at",
                )
                fragments = message.get("fragments")
                if not isinstance(fragments, list):
                    fragments = []
                for index, fragment in enumerate(fragments):
                    if not isinstance(fragment, dict):
                        continue
                    fragment_type = str(fragment.get("type", "")).upper()
                    content = _text(fragment.get("content"))
                    if not content:
                        continue
                    if fragment_type == "REQUEST":
                        role, content_kind = "user", "text"
                    elif fragment_type == "RESPONSE":
                        role, content_kind = "assistant", "text"
                    elif fragment_type == "THINK":
                        role, content_kind = "assistant", "thinking"
                    elif fragment_type in {"SEARCH", "READ_LINK"}:
                        role, content_kind = "assistant", "tool"
                    else:
                        role, content_kind = "assistant", "tool"
                    result.append(
                        NormalizedMessage(
                            message_id=f"{node_id}:{index}",
                            role=role,
                            content_kind=content_kind,
                            content=content,
                            occurred_at=occurred_at,
                        )
                    )
        return result


class GeminiTakeoutImporter:
    _CONVERSATION_RE = re.compile(r"/app/(?:c/)?([^/?#]+)")

    @staticmethod
    def parse(path: Path) -> ImportBatch:
        source_path = Path(path)
        payload, raw = _load_json(source_path)
        if not isinstance(payload, list):
            raise ArchiveInputError("GEMINI_TAKEOUT_TOP_LEVEL_NOT_LIST")

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for activity in payload:
            if not isinstance(activity, dict):
                continue
            match = GeminiTakeoutImporter._CONVERSATION_RE.search(
                str(activity.get("titleUrl", ""))
            )
            if match:
                grouped[match.group(1)].append(activity)
        if not grouped:
            raise ArchiveInputError("GEMINI_TAKEOUT_CONVERSATIONS_NOT_FOUND")

        conversations: list[NormalizedConversation] = []
        for conversation_id, activities in grouped.items():
            activities.sort(key=lambda item: str(item.get("time", "")))
            messages: list[NormalizedMessage] = []
            incomplete = False
            first_prompt = ""
            for activity_index, activity in enumerate(activities):
                occurred_at = _iso_timestamp(activity.get("time"), "time")
                request_values: list[str] = []
                response_values: list[str] = []
                details = activity.get("details")
                if isinstance(details, list):
                    for detail in details:
                        if not isinstance(detail, dict):
                            continue
                        name = str(detail.get("name", "")).strip().lower()
                        content = _text(detail.get("value"))
                        if not content:
                            continue
                        if name == "request":
                            request_values.append(content)
                        elif name == "response":
                            response_values.append(content)
                if not request_values and "userInteractions" in activity:
                    extracted = _extract_text_values(activity["userInteractions"])
                    if extracted:
                        request_values.append(extracted[0])
                        response_values.extend(extracted[1:])

                for index, content in enumerate(request_values):
                    first_prompt = first_prompt or content
                    messages.append(
                        NormalizedMessage(
                            message_id=f"{conversation_id}:{activity_index}:u:{index}",
                            role="user",
                            content_kind="text",
                            content=content,
                            occurred_at=occurred_at,
                        )
                    )
                for index, content in enumerate(response_values):
                    messages.append(
                        NormalizedMessage(
                            message_id=f"{conversation_id}:{activity_index}:a:{index}",
                            role="assistant",
                            content_kind="text",
                            content=content,
                            occurred_at=occurred_at,
                        )
                    )
                if request_values and not response_values:
                    incomplete = True

            if not messages:
                continue
            created_at = _iso_timestamp(activities[0].get("time"), "time")
            updated_at = _iso_timestamp(activities[-1].get("time"), "time")
            conversations.append(
                NormalizedConversation(
                    provider="gemini",
                    provider_conversation_id=conversation_id,
                    title=(first_prompt[:120] or f"Gemini {conversation_id}"),
                    created_at=created_at,
                    updated_at=updated_at,
                    messages=tuple(messages),
                    completeness="PARTIAL" if incomplete else "COMPLETE",
                    source_locator=f"gemini-takeout:{conversation_id}",
                )
            )

        if not conversations:
            raise ArchiveInputError("GEMINI_TAKEOUT_MESSAGES_NOT_FOUND")

        return ImportBatch(
            provider="gemini",
            source_kind="GEMINI_TAKEOUT_ACTIVITY",
            generated_at=max(
                (conversation.updated_at for conversation in conversations),
                default=_utc_now(),
            ),
            expected_count=len(grouped),
            conversations=tuple(conversations),
            source_path=source_path,
            raw_bytes=raw,
        )


class BrowserLibraryImporter:
    SCHEMA = "MEMORIVE_BROWSER_SESSION_LIBRARY_V4_DRAFT"
    SOURCE = "MEMORIVE_BROWSER_COMPANION_VISIBLE_UI_V4_DRAFT"

    @staticmethod
    def parse(path: Path) -> ImportBatch:
        source_path = Path(path)
        payload, raw = _load_json(source_path)
        return BrowserLibraryImporter.parse_payload(
            payload,
            raw_bytes=raw,
            source_path=source_path,
        )

    @staticmethod
    def parse_payload(
        payload: Any,
        *,
        raw_bytes: bytes,
        source_path: Path = Path("browser-library.json"),
        allowed_site_settings=None,
    ) -> ImportBatch:
        source_path = Path(source_path)
        if not isinstance(payload, dict):
            raise ArchiveInputError("BROWSER_LIBRARY_TOP_LEVEL_NOT_OBJECT")
        forbidden = _find_forbidden_key(payload)
        if forbidden:
            raise ArchiveInputError(f"CREDENTIAL_FIELD_FORBIDDEN: {forbidden}")
        if payload.get("schema_version") != BrowserLibraryImporter.SCHEMA:
            raise ArchiveInputError("BROWSER_LIBRARY_SCHEMA_UNSUPPORTED")
        if payload.get("source") != BrowserLibraryImporter.SOURCE:
            raise ArchiveInputError("BROWSER_LIBRARY_SOURCE_UNSUPPORTED")
        sync_scope = payload.get("sync_scope")
        if sync_scope not in {
            "CURRENT_PAGE",
            "RECENT_14_DAYS_OR_30_CONVERSATIONS",
            "RECENT_30_DAYS",
        }:
            raise ArchiveInputError("BROWSER_LIBRARY_SCOPE_UNSUPPORTED")

        provider = _required_text(payload.get("provider_id"), "provider_id").lower()
        current_page_capture = sync_scope == "CURRENT_PAGE"
        if provider == "universal" and not current_page_capture:
            raise ArchiveInputError("BROWSER_CURRENT_PAGE_SCOPE_REQUIRED")
        allowed_hosts = _PROVIDER_HOSTS
        if provider.startswith('web-'):
            # Provider-controlled payloads never grant themselves a new origin.
            from memorive_browser_companion.site_profiles import normalize_sites, profile_hash, active_sites
            if not allowed_site_settings: raise ArchiveInputError('BROWSER_CUSTOM_SITE_NOT_CONFIGURED')
            sites=normalize_sites(allowed_site_settings['sites'])
            if payload.get('site_profile_sha256') != profile_hash(sites):
                raise ArchiveInputError('BROWSER_SITE_PROFILE_MISMATCH')
            allowed_hosts={row['provider_id']:set(row['hosts']) for row in active_sites(sites)}
        if provider != "universal" and provider not in allowed_hosts:
            raise ArchiveInputError("BROWSER_PROVIDER_UNSUPPORTED")
        if current_page_capture and provider != "universal":
            raise ArchiveInputError("BROWSER_CURRENT_PAGE_PROVIDER_INVALID")
        conversations_payload = payload.get("conversations")
        failures_payload = payload.get("failures")
        if not isinstance(conversations_payload, list) or not isinstance(
            failures_payload, list
        ):
            raise ArchiveInputError("BROWSER_LIBRARY_ITEMS_INVALID")
        enumerated_count = payload.get("enumerated_count")
        captured_count = payload.get("captured_count")
        failed_count = payload.get("failed_count")
        if not all(
            isinstance(value, int) and value >= 0
            for value in (enumerated_count, captured_count, failed_count)
        ):
            raise ArchiveInputError("BROWSER_LIBRARY_COUNT_INVALID")
        unchanged_count = payload.get("unchanged_count")
        body_read_count = payload.get("body_read_count")
        capture_target = payload.get("capture_target")
        candidate_count = payload.get("candidate_count")
        zero_change_incremental = bool(
            payload.get("sync_mode") == "INCREMENTAL"
            and enumerated_count == 0
            and captured_count == 0
            and failed_count == 0
            and not conversations_payload
            and not failures_payload
            and isinstance(unchanged_count, int)
            and unchanged_count > 0
            and isinstance(body_read_count, int)
            and body_read_count == unchanged_count
            and isinstance(capture_target, int)
            and capture_target >= unchanged_count
            and isinstance(candidate_count, int)
            and candidate_count >= capture_target
        )
        if enumerated_count == 0 and not zero_change_incremental:
            raise ArchiveInputError("BROWSER_LIBRARY_EMPTY")
        if (
            captured_count != len(conversations_payload)
            or failed_count != len(failures_payload)
            or enumerated_count != captured_count + failed_count
        ):
            raise ArchiveInputError("BROWSER_LIBRARY_COUNT_MISMATCH")
        if (
            sync_scope == "RECENT_14_DAYS_OR_30_CONVERSATIONS"
            and enumerated_count > 50
        ):
            raise ArchiveInputError("BROWSER_LIBRARY_RECENT_CONVERSATION_LIMIT_EXCEEDED")
        if current_page_capture and (
            payload.get("sync_mode") != "CURRENT_PAGE"
            or enumerated_count != 1
            or captured_count != 1
            or failed_count != 0
            or len(conversations_payload) != 1
            or failures_payload
        ):
            raise ArchiveInputError("BROWSER_CURRENT_PAGE_COUNT_INVALID")

        generated_at = _iso_timestamp(payload.get("generated_at"), "generated_at")
        if current_page_capture:
            from memorive_browser_companion.site_profiles import official_web_identity
            # Manual visible-page capture uses the official-origin registry.
            # Background-sync selections and their hashes do not grant or deny
            # this user-triggered operation; the custom-sync branch above keeps
            # its separate configuration and profile-hash checks.
            for item in conversations_payload:
                raw_url = item.get("source_url") if isinstance(item, dict) else None
                if not official_web_identity(raw_url):
                    raise ArchiveInputError("BROWSER_SITE_NOT_VERIFIED_OFFICIAL")
        conversations = tuple(
            BrowserLibraryImporter._conversation(
                provider, item, generated_at, sync_scope, allowed_hosts
            )
            for item in conversations_payload
        )
        capture_methods = {
            str(item["capture_method"])
            for item in conversations_payload
            if isinstance(item, dict)
        }
        if len(capture_methods) > 1:
            raise ArchiveInputError("BROWSER_CAPTURE_METHOD_MIXED")
        source_kind = (
            BROWSER_CAPTURE_SOURCE_ACTIVE_TAB_V1
            if capture_methods == {"BROWSER_COMPANION_ACTIVE_TAB_V1"}
            else
            BROWSER_CAPTURE_SOURCE_STRUCTURED_DOM_V4
            if capture_methods == {"BROWSER_COMPANION_BACKGROUND_TAB_V4"}
            else BROWSER_CAPTURE_SOURCE_LEGACY_V3
            if capture_methods
            else BROWSER_CAPTURE_SOURCE_DEPLOYED_DRAFT
        )
        failures = tuple(
            ImportFailure(
                provider_conversation_id=_required_text(
                    item.get("provider_session_id"), "provider_session_id"
                ),
                title=_required_text(item.get("title"), "title"),
                error_code=_required_text(item.get("error_code"), "error_code"),
            )
            for item in failures_payload
            if isinstance(item, dict)
        )
        if len(failures) != len(failures_payload):
            raise ArchiveInputError("BROWSER_LIBRARY_FAILURE_INVALID")

        return ImportBatch(
            provider=provider,
            source_kind=source_kind,
            generated_at=generated_at,
            expected_count=enumerated_count,
            conversations=conversations,
            failures=failures,
            source_path=source_path,
            raw_bytes=raw_bytes,
        )

    @staticmethod
    def _conversation(
        provider: str,
        item: Any,
        generated_at: str,
        sync_scope: str,
        allowed_hosts=None,
    ) -> NormalizedConversation:
        if not isinstance(item, dict):
            raise ArchiveInputError("BROWSER_CONVERSATION_INVALID")
        if str(item.get("provider_id", "")).lower() != provider:
            raise ArchiveInputError("BROWSER_PROVIDER_ID_MISMATCH")
        conversation_id = _required_text(
            item.get("provider_session_id"), "provider_session_id"
        )
        source_url = _required_text(item.get("source_url"), "source_url")
        parsed_source = urlparse(source_url)
        host = (parsed_source.hostname or "").lower()
        current_page_capture = sync_scope == "CURRENT_PAGE"
        if current_page_capture:
            if parsed_source.scheme.lower() != "https" or not host:
                raise ArchiveInputError("BROWSER_CURRENT_PAGE_URL_SCHEME_UNSUPPORTED")
            if (
                parsed_source.username
                or parsed_source.password
                or parsed_source.query
                or parsed_source.fragment
            ):
                raise ArchiveInputError("BROWSER_CURRENT_PAGE_URL_NOT_SANITIZED")
        elif (host not in (allowed_hosts or _PROVIDER_HOSTS)[provider]
              or parsed_source.scheme!='https' or parsed_source.username or parsed_source.password
              or parsed_source.port not in (None,443)):
            raise ArchiveInputError("BROWSER_SOURCE_HOST_NOT_ALLOWED")
        if provider.startswith('web-') and (parsed_source.query or parsed_source.fragment):
            raise ArchiveInputError('BROWSER_CUSTOM_URL_NOT_SANITIZED')
        capture_method = _required_text(item.get("capture_method"), "capture_method")
        if current_page_capture and capture_method != "BROWSER_COMPANION_ACTIVE_TAB_V1":
            raise ArchiveInputError("BROWSER_CURRENT_PAGE_CAPTURE_METHOD_INVALID")
        if not current_page_capture and capture_method not in {
            "BROWSER_COMPANION_BACKGROUND_TAB_V3",
            "BROWSER_COMPANION_BACKGROUND_TAB_V4",
        }:
            raise ArchiveInputError("BROWSER_CAPTURE_METHOD_UNSUPPORTED")
        capture_status = item.get("capture_status")
        if current_page_capture:
            if capture_status not in {"COMPLETE", "PARTIAL"}:
                raise ArchiveInputError("BROWSER_CURRENT_PAGE_STATUS_INVALID")
            if capture_status == "PARTIAL":
                if item.get("source_truncated") is not True:
                    raise ArchiveInputError("BROWSER_CURRENT_PAGE_PARTIAL_PROOF_INVALID")
            elif (
                item.get("source_truncated") is not False
                or item.get("top_boundary_reached") is not True
                or item.get("bottom_boundary_reached") is not True
            ):
                raise ArchiveInputError("BROWSER_CURRENT_PAGE_COMPLETE_PROOF_INVALID")
        else:
            if capture_status != "COMPLETE":
                raise ArchiveInputError("BROWSER_CAPTURE_NOT_COMPLETE")
            if item.get("source_truncated") is not False:
                raise ArchiveInputError("BROWSER_CAPTURE_TRUNCATED")
            if item.get("top_boundary_reached") is not True:
                raise ArchiveInputError("BROWSER_CAPTURE_TOP_BOUNDARY_UNPROVEN")
            if item.get("bottom_boundary_reached") is not True:
                raise ArchiveInputError("BROWSER_CAPTURE_BOTTOM_BOUNDARY_UNPROVEN")
        messages_payload = item.get("messages")
        if not isinstance(messages_payload, list) or not messages_payload:
            raise ArchiveInputError("BROWSER_MESSAGES_EMPTY")
        messages: list[NormalizedMessage] = []
        for message in messages_payload:
            if not isinstance(message, dict):
                raise ArchiveInputError("BROWSER_MESSAGE_INVALID")
            role = _required_text(message.get("role"), "role").lower()
            content_kind = _required_text(
                message.get("content_kind"), "content_kind"
            ).lower()
            if role not in {"user", "assistant"}:
                raise ArchiveInputError("BROWSER_MESSAGE_ROLE_INVALID")
            if content_kind not in {"text", "thinking", "tool"}:
                raise ArchiveInputError("BROWSER_MESSAGE_KIND_INVALID")
            messages.append(
                NormalizedMessage(
                    message_id=_required_text(message.get("message_id"), "message_id"),
                    role=role,
                    content_kind=content_kind,
                    content=_normalized_visible_message_content(message.get("content")),
                    occurred_at=_browser_timestamp(
                        message.get("occurred_at"), "occurred_at"
                    ),
                )
            )
        has_user = any(message.role == "user" for message in messages)
        has_assistant = any(
            message.role == "assistant" and message.content_kind == "text"
            for message in messages
        )
        if not has_user or not has_assistant:
            raise ArchiveInputError("BROWSER_MESSAGES_ROLE_COVERAGE_INCOMPLETE")
        created_at = _browser_timestamp(item.get("created_at"), "created_at")
        updated_at = _browser_timestamp(item.get("updated_at"), "updated_at")
        known_times = [value for value in (created_at, updated_at) if value != "UNKNOWN"]
        selection_basis = item.get("selection_basis")
        source_recency_rank = item.get("source_recency_rank")
        if sync_scope == "RECENT_14_DAYS_OR_30_CONVERSATIONS":
            if selection_basis not in {
                "RECENT_14_DAYS",
                "RECENT_30_CONVERSATIONS",
            }:
                raise ArchiveInputError("BROWSER_CONVERSATION_SELECTION_BASIS_INVALID")
            valid_rank_limit = (
                50 if selection_basis == "RECENT_14_DAYS" else 30
            )
            if (
                not isinstance(source_recency_rank, int)
                or isinstance(source_recency_rank, bool)
                or source_recency_rank < 0
                or source_recency_rank >= valid_rank_limit
            ):
                raise ArchiveInputError("BROWSER_CONVERSATION_RECENCY_RANK_INVALID")
        if known_times:
            newest = max(
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                for value in known_times
            )
            reference = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            age = reference - newest
            if age < -timedelta(days=1):
                raise ArchiveInputError("BROWSER_CONVERSATION_TIMESTAMP_IN_FUTURE")
            if (
                sync_scope == "RECENT_14_DAYS_OR_30_CONVERSATIONS"
                and selection_basis == "RECENT_14_DAYS"
                and age > timedelta(days=14)
            ):
                raise ArchiveInputError("BROWSER_CONVERSATION_OUTSIDE_RECENT_14_DAYS")
            if sync_scope == "RECENT_30_DAYS" and age > timedelta(days=30):
                raise ArchiveInputError("BROWSER_CONVERSATION_OUTSIDE_RECENT_30_DAYS")
        elif sync_scope == "RECENT_14_DAYS_OR_30_CONVERSATIONS":
            if (
                selection_basis == "RECENT_14_DAYS"
                and item.get("recency_bucket")
                not in {"TODAY", "LAST_7_DAYS", "LAST_14_DAYS"}
            ):
                raise ArchiveInputError("BROWSER_CONVERSATION_RECENCY_UNPROVEN")
        elif sync_scope == "CURRENT_PAGE":
            if (
                item.get("selection_basis") != "CURRENT_PAGE_USER_GESTURE"
                or source_recency_rank != 0
            ):
                raise ArchiveInputError("BROWSER_CURRENT_PAGE_SELECTION_PROOF_INVALID")
        elif item.get("recency_bucket") not in {
            "TODAY", "LAST_7_DAYS", "LAST_30_DAYS"
        }:
            raise ArchiveInputError("BROWSER_CONVERSATION_RECENCY_UNPROVEN")
        return NormalizedConversation(
            provider=provider,
            provider_conversation_id=conversation_id,
            title=_required_text(item.get("title"), "title"),
            created_at=created_at,
            updated_at=updated_at,
            messages=tuple(messages),
            completeness="PARTIAL" if capture_status == "PARTIAL" else "COMPLETE",
            source_locator=source_url,
        )
