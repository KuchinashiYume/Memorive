"""Browser conversations and the visible-UI reader: BrowserSessionsMixin.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit
from memorive.platform.windows_paths import windows_io_path
from memorive.sessions.archive import (
    ArchiveInputError,
    BrowserLibraryImporter,
    DeepSeekOfficialImporter,
    GeminiTakeoutImporter,
)
from memorive.sessions.archive.models import (
    BROWSER_CAPTURE_SOURCE_ACTIVE_TAB_V1,
    BROWSER_CAPTURE_SOURCE_KINDS,
    ImportBatch,
    ImportFailure,
    NormalizedConversation,
    NormalizedMessage,
)
from memorive.sessions.controller.common import (
    SessionProductError,
    _UNIVERSAL_PROVIDER_ID,
    _VISIBLE_UI_MODELS,
    _VISIBLE_UI_PROFILES,
    _VISIBLE_UI_SOURCE_NAMES,
    _iso_timestamp,
    _normalize_web_messages,
    _projection_id,
    _text,
    _universal_source_name,
)


class BrowserSessionsMixin:
    """Browser conversations and the visible-UI reader: their checks, import into the archive, and rows."""

    def _validated_visible_ui_path(self, raw_path: Any) -> Path | None:
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None
        candidate = Path(raw_path).expanduser().resolve()
        if not self._path_within(candidate, (self._visible_ui_root,)):
            return None
        if not windows_io_path(candidate).is_file() or candidate.suffix.casefold() != ".json":
            return None
        return candidate

    @staticmethod
    def _validate_visible_ui_bundle(
        bundle: Any,
        expected_provider: str | None = None,
    ) -> dict[str, Any]:
        root_keys = {
            "provider_profile",
            "conversation_id",
            "messages",
            "observed_at",
            "source_truncated",
            "boundary_method",
        }
        if not isinstance(bundle, Mapping) or set(bundle) != root_keys:
            raise SessionProductError("VISIBLE_UI_BUNDLE_FIELDS_INVALID")
        profile = str(bundle.get("provider_profile") or "")
        providers = {
            configured_profile: provider_id
            for provider_id, configured_profile in _VISIBLE_UI_PROFILES.items()
        }
        provider_id = providers.get(profile)
        if provider_id is None or (
            expected_provider is not None and provider_id != expected_provider
        ):
            raise SessionProductError("VISIBLE_UI_PROFILE_INVALID")
        boundary_method = str(bundle.get("boundary_method") or "")
        if boundary_method not in {
            "HOST_OBSERVED_VISIBLE_UI_TURN_ORDER_V1",
            "HOST_OBSERVED_VISIBLE_UI_FULL_SCROLL_V2",
        }:
            raise SessionProductError("VISIBLE_UI_BOUNDARY_INVALID")
        conversation_id = _text(bundle.get("conversation_id"), 256)
        observed_at = _iso_timestamp(bundle.get("observed_at"))
        if not conversation_id or observed_at == "UNKNOWN":
            raise SessionProductError("VISIBLE_UI_IDENTITY_INVALID")
        if not isinstance(bundle.get("source_truncated"), bool):
            raise SessionProductError("VISIBLE_UI_TRUNCATION_INVALID")
        raw_messages = bundle.get("messages")
        if not isinstance(raw_messages, list) or not raw_messages:
            raise SessionProductError("VISIBLE_UI_MESSAGES_INVALID")
        messages: list[dict[str, str]] = []
        seen: set[str] = set()
        for value in raw_messages:
            if not isinstance(value, Mapping) or set(value) != {"message_id", "role", "text"}:
                raise SessionProductError("VISIBLE_UI_MESSAGE_FIELDS_INVALID")
            message_id = _text(value.get("message_id"), 256)
            role = str(value.get("role") or "")
            text = value.get("text")
            if not message_id or message_id in seen or role not in {"user", "assistant"}:
                raise SessionProductError("VISIBLE_UI_MESSAGE_IDENTITY_INVALID")
            if not isinstance(text, str) or not text.strip():
                raise SessionProductError("VISIBLE_UI_MESSAGE_TEXT_INVALID")
            seen.add(message_id)
            messages.append({"message_id": message_id, "role": role, "text": text.strip()})
        return {
            "provider_profile": profile,
            "conversation_id": conversation_id,
            "messages": _normalize_web_messages(messages),
            "observed_at": observed_at,
            "source_truncated": bool(bundle["source_truncated"]),
            "boundary_method": boundary_method,
        }

    def _write_visible_ui_snapshot(
        self,
        provider_id: str,
        bundle: Mapping[str, Any],
    ) -> Path:
        if provider_id not in _VISIBLE_UI_PROFILES:
            raise SessionProductError("VISIBLE_UI_PROVIDER_INVALID")
        projection_id = _projection_id(provider_id, str(bundle["conversation_id"]))
        path = self._visible_ui_root / f"{projection_id}.json"
        temporary = path.with_suffix(".json.next")
        if temporary.exists():
            raise FileExistsError("VISIBLE_UI_TEMP_COLLISION")
        temporary.write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(path)
        self._metrics["local_projection_writes"] += 1
        return path.resolve()

    @classmethod
    def _validate_browser_conversation(cls, value: Any) -> dict[str, Any]:
        expected_v1 = {
            "provider_id",
            "provider_session_id",
            "title",
            "observed_at",
            "source_url",
            "capture_method",
            "source_truncated",
            "messages",
        }
        expected_v2 = {*expected_v1, "conversation_at"}
        expected_v3 = {
            *expected_v2,
            "recency_bucket",
            "source_recency_rank",
            "capture_status",
            "error_code",
        }
        value_keys = frozenset(value) if isinstance(value, Mapping) else frozenset()
        if not isinstance(value, Mapping) or value_keys not in {
            frozenset(expected_v1),
            frozenset(expected_v2),
            frozenset(expected_v3),
        }:
            raise SessionProductError("BROWSER_SESSION_CONVERSATION_FIELDS_INVALID")
        is_v2 = value_keys == frozenset(expected_v2)
        is_v3 = value_keys == frozenset(expected_v3)
        provider_id = str(value.get("provider_id") or "")
        approved_hosts = {
            "deepseek": {"chat.deepseek.com"},
            "gemini": {"gemini.google.com"},
            "kimi": {"kimi.com", "www.kimi.com", "kimi.moonshot.cn"},
        }
        if provider_id not in approved_hosts:
            raise SessionProductError("BROWSER_SESSION_PROVIDER_INVALID")
        provider_session_id = _text(value.get("provider_session_id"), 256)
        title = _text(value.get("title"), 240)
        observed_at = _iso_timestamp(value.get("observed_at"))
        if not provider_session_id or not title or observed_at == "UNKNOWN":
            raise SessionProductError("BROWSER_SESSION_IDENTITY_INVALID")
        try:
            observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise SessionProductError("BROWSER_SESSION_OBSERVED_AT_INVALID") from error
        if observed < datetime.now(timezone.utc) - timedelta(days=cls.discovery_window_days):
            raise SessionProductError("BROWSER_SESSION_OUTSIDE_DISCOVERY_WINDOW")
        raw_conversation_at = value.get("conversation_at", "UNKNOWN")
        conversation_at = (
            "UNKNOWN"
            if raw_conversation_at == "UNKNOWN"
            else _iso_timestamp(raw_conversation_at)
        )
        if (is_v2 or is_v3) and conversation_at == "UNKNOWN" and raw_conversation_at != "UNKNOWN":
            raise SessionProductError("BROWSER_SESSION_CONVERSATION_AT_INVALID")
        if conversation_at != "UNKNOWN":
            occurred = datetime.fromisoformat(conversation_at.replace("Z", "+00:00"))
            if occurred < datetime.now(timezone.utc) - timedelta(days=cls.discovery_window_days):
                raise SessionProductError("BROWSER_SESSION_OUTSIDE_DISCOVERY_WINDOW")
        source_url = str(value.get("source_url") or "")
        parsed = urlsplit(source_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in approved_hosts[provider_id]
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise SessionProductError("BROWSER_SESSION_SOURCE_URL_INVALID")
        expected_capture_method = (
            "BROWSER_COMPANION_BACKGROUND_TAB_V3"
            if is_v3
            else "VISIBLE_DOM_FULL_SCROLL_V2"
            if is_v2
            else "VISIBLE_DOM_FULL_SCROLL_V1"
        )
        if value.get("capture_method") != expected_capture_method:
            raise SessionProductError("BROWSER_SESSION_CAPTURE_METHOD_INVALID")
        capture_status = str(value.get("capture_status") or "COMPLETE")
        error_code = _text(value.get("error_code"), 160) if is_v3 else ""
        if capture_status not in {"COMPLETE", "ERROR"}:
            raise SessionProductError("BROWSER_SESSION_CAPTURE_STATUS_INVALID")
        if is_v3:
            recency_bucket = str(value.get("recency_bucket") or "")
            source_recency_rank = value.get("source_recency_rank")
            if recency_bucket not in {"TODAY", "LAST_7_DAYS", "LAST_30_DAYS", "UNKNOWN"}:
                raise SessionProductError("BROWSER_SESSION_RECENCY_BUCKET_INVALID")
            if (
                not isinstance(source_recency_rank, int)
                or isinstance(source_recency_rank, bool)
                or source_recency_rank < 0
                or source_recency_rank >= 5_000
            ):
                raise SessionProductError("BROWSER_SESSION_RECENCY_RANK_INVALID")
            if capture_status == "COMPLETE":
                if value.get("source_truncated") is not False or error_code:
                    raise SessionProductError("BROWSER_SESSION_COMPLETENESS_INVALID")
            elif value.get("source_truncated") is not True or not error_code:
                raise SessionProductError("BROWSER_SESSION_FAILURE_PROOF_INVALID")
        else:
            recency_bucket = "UNKNOWN"
            source_recency_rank = 239
            if value.get("source_truncated") is not False:
                raise SessionProductError("BROWSER_SESSION_INCOMPLETE")
        raw_messages = value.get("messages")
        if (
            not isinstance(raw_messages, list)
            or len(raw_messages) > 50_000
            or (capture_status == "COMPLETE" and not raw_messages)
            or (capture_status == "ERROR" and raw_messages)
        ):
            raise SessionProductError("BROWSER_SESSION_MESSAGES_INVALID")
        messages: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        for message in raw_messages:
            if not isinstance(message, Mapping) or set(message) != {"message_id", "role", "text"}:
                raise SessionProductError("BROWSER_SESSION_MESSAGE_FIELDS_INVALID")
            message_id = _text(message.get("message_id"), 256)
            role = str(message.get("role") or "")
            text = message.get("text")
            if not message_id or message_id in seen_ids or role not in {"user", "assistant"}:
                raise SessionProductError("BROWSER_SESSION_MESSAGE_IDENTITY_INVALID")
            if not isinstance(text, str) or not text.strip() or len(text) > 10_000_000:
                raise SessionProductError("BROWSER_SESSION_MESSAGE_TEXT_INVALID")
            seen_ids.add(message_id)
            messages.append({"message_id": message_id, "role": role, "text": text.strip()})
        normalized = {
            "provider_id": provider_id,
            "provider_session_id": provider_session_id,
            "title": title,
            "observed_at": observed_at,
            "source_url": source_url,
            "capture_method": expected_capture_method,
            "source_truncated": bool(value.get("source_truncated")),
            "messages": _normalize_web_messages(messages),
        }
        if is_v2 or is_v3:
            normalized["conversation_at"] = conversation_at
        if is_v3:
            normalized.update(
                {
                    "recency_bucket": recency_bucket,
                    "source_recency_rank": source_recency_rank,
                    "capture_status": capture_status,
                    "error_code": error_code,
                }
            )
        return normalized

    @classmethod
    def _validate_browser_bundle(cls, bundle: Any) -> dict[str, Any]:
        expected_legacy = {"schema_version", "source", "generated_at", "conversations"}
        expected_v3 = {
            *expected_legacy,
            "sync_scope",
            "provider_id",
            "expected_count",
            "captured_count",
            "failed_count",
        }
        bundle_keys = frozenset(bundle) if isinstance(bundle, Mapping) else frozenset()
        if not isinstance(bundle, Mapping) or bundle_keys not in {
            frozenset(expected_legacy),
            frozenset(expected_v3),
        }:
            raise SessionProductError("BROWSER_SESSION_BUNDLE_FIELDS_INVALID")
        bundle_profile = (
            bundle.get("schema_version"),
            bundle.get("source"),
        )
        if bundle_profile not in {
            ("MEMORIVE_BROWSER_SESSION_BUNDLE_V1", "MEMORIVE_BROWSER_COMPANION_V1"),
            ("MEMORIVE_BROWSER_SESSION_BUNDLE_V2", "MEMORIVE_BROWSER_COMPANION_V2"),
            ("MEMORIVE_BROWSER_SESSION_LIBRARY_V3", "MEMORIVE_BROWSER_COMPANION_LIBRARY_V3"),
        }:
            raise SessionProductError("BROWSER_SESSION_BUNDLE_SCHEMA_INVALID")
        generated_at = _iso_timestamp(bundle.get("generated_at"))
        if generated_at == "UNKNOWN":
            raise SessionProductError("BROWSER_SESSION_BUNDLE_TIME_INVALID")
        values = bundle.get("conversations")
        if not isinstance(values, list) or not values or len(values) > 5_000:
            raise SessionProductError("BROWSER_SESSION_BUNDLE_CONVERSATIONS_INVALID")
        conversations = [cls._validate_browser_conversation(value) for value in values]
        identities = [(row["provider_id"], row["provider_session_id"]) for row in conversations]
        if len(set(identities)) != len(identities):
            raise SessionProductError("BROWSER_SESSION_BUNDLE_DUPLICATE_IDENTITY")
        normalized = {
            "schema_version": bundle_profile[0],
            "source": bundle_profile[1],
            "generated_at": generated_at,
            "conversations": conversations,
        }
        if bundle_profile[0] == "MEMORIVE_BROWSER_SESSION_LIBRARY_V3":
            provider_id = str(bundle.get("provider_id") or "")
            failed_count = sum(row.get("capture_status") == "ERROR" for row in conversations)
            captured_count = len(conversations) - failed_count
            if (
                bundle.get("sync_scope") != "RECENT_30_DAYS"
                or provider_id not in _VISIBLE_UI_PROFILES
                or any(row["provider_id"] != provider_id for row in conversations)
                or bundle.get("expected_count") != len(conversations)
                or bundle.get("captured_count") != captured_count
                or bundle.get("failed_count") != failed_count
            ):
                raise SessionProductError("BROWSER_SESSION_LIBRARY_COUNTS_INVALID")
            normalized.update(
                {
                    "sync_scope": "RECENT_30_DAYS",
                    "provider_id": provider_id,
                    "expected_count": len(conversations),
                    "captured_count": captured_count,
                    "failed_count": failed_count,
                }
            )
        return normalized

    @staticmethod
    def _archive_source_kind(schema_version: str) -> str:
        return {
            "MEMORIVE_BROWSER_SESSION_BUNDLE_V1": "BROWSER_VISIBLE_DOM_COMPLETE_BUNDLE_V1",
            "MEMORIVE_BROWSER_SESSION_BUNDLE_V2": "BROWSER_VISIBLE_DOM_COMPLETE_BUNDLE_V2",
            "MEMORIVE_BROWSER_SESSION_LIBRARY_V3": "BROWSER_COMPANION_VISIBLE_UI_LIBRARY_V3_MIGRATED",
        }[schema_version]

    @classmethod
    def _archive_batches_from_legacy_bundle(
        cls,
        normalized: Mapping[str, Any],
        raw_bytes: bytes,
    ) -> list[ImportBatch]:
        grouped: dict[str, list[Mapping[str, Any]]] = {
            provider: [] for provider in _VISIBLE_UI_PROFILES
        }
        for conversation in normalized["conversations"]:
            grouped[str(conversation["provider_id"])].append(conversation)
        batches: list[ImportBatch] = []
        source_kind = cls._archive_source_kind(str(normalized["schema_version"]))
        for provider, values in grouped.items():
            if not values:
                continue
            conversations: list[NormalizedConversation] = []
            failures: list[ImportFailure] = []
            for value in values:
                conversation_id = str(value["provider_session_id"])
                if value.get("capture_status") == "ERROR":
                    failures.append(
                        ImportFailure(
                            provider_conversation_id=conversation_id,
                            title=str(value["title"]),
                            error_code=str(value.get("error_code") or "CAPTURE_FAILED"),
                        )
                    )
                    continue
                conversation_at = str(value.get("conversation_at") or "UNKNOWN")
                messages = tuple(
                    NormalizedMessage(
                        message_id=str(message["message_id"]),
                        role=str(message["role"]),
                        content_kind="text",
                        content=str(message["text"]),
                        occurred_at=conversation_at,
                    )
                    for message in value["messages"]
                )
                conversations.append(
                    NormalizedConversation(
                        provider=provider,
                        provider_conversation_id=conversation_id,
                        title=str(value["title"]),
                        created_at=conversation_at,
                        updated_at=conversation_at,
                        messages=messages,
                        completeness="COMPLETE",
                        source_locator=str(value["source_url"]),
                    )
                )
            batches.append(
                ImportBatch(
                    provider=provider,
                    source_kind=source_kind,
                    generated_at=str(normalized["generated_at"]),
                    expected_count=len(values),
                    conversations=tuple(conversations),
                    failures=tuple(failures),
                    source_path=Path(f"{provider}-{normalized['schema_version']}.json"),
                    raw_bytes=raw_bytes,
                )
            )
        return batches

    def _ingest_archive_batch(self, batch: ImportBatch) -> dict[str, Any]:
        receipt = self._session_archive.ingest(batch)
        if receipt.get("verification_status") == "REJECTED_CORRUPT":
            raise SessionProductError("SESSION_ARCHIVE_BATCH_REJECTED_CORRUPT")
        return receipt

    def _restore_hidden_projection_ids(self, identities: Iterable[tuple[str, str]]) -> None:
        projection_ids = {
            _projection_id(provider, conversation_id)
            for provider, conversation_id in identities
        }
        if not projection_ids:
            return
        state = self._load()
        hidden = set(state["hidden_projection_ids"])
        restored = hidden.intersection(projection_ids)
        if restored:
            state["hidden_projection_ids"] = sorted(hidden - restored)
            self._save(state)

    def import_session_file(self, source: Path) -> dict[str, Any]:
        path = Path(source).resolve(strict=False)
        path_io = windows_io_path(path)
        if not path_io.is_file() or path.suffix.casefold() != ".json":
            raise SessionProductError("SESSION_IMPORT_FILE_INVALID")
        try:
            raw_bytes = path_io.read_bytes()
            payload = json.loads(raw_bytes.decode("utf-8-sig"))
            if isinstance(payload, Mapping) and payload.get("schema_version") in {
                "MEMORIVE_BROWSER_SESSION_BUNDLE_V1",
                "MEMORIVE_BROWSER_SESSION_BUNDLE_V2",
                "MEMORIVE_BROWSER_SESSION_LIBRARY_V3",
                BrowserLibraryImporter.SCHEMA,
            }:
                return self.import_browser_bundle(payload)
            if isinstance(payload, list) and any(
                isinstance(item, Mapping) and isinstance(item.get("mapping"), Mapping)
                for item in payload
            ):
                batch = DeepSeekOfficialImporter.parse(path)
            elif isinstance(payload, list) and any(
                isinstance(item, Mapping) and item.get("titleUrl")
                for item in payload
            ):
                batch = GeminiTakeoutImporter.parse(path)
            else:
                raise SessionProductError("SESSION_IMPORT_FORMAT_UNSUPPORTED")
            receipt = self._ingest_archive_batch(batch)
        except ArchiveInputError as error:
            raise SessionProductError(str(error)) from error
        identities = [
            (batch.provider, conversation.provider_conversation_id)
            for conversation in batch.conversations
        ]
        self._restore_hidden_projection_ids(identities)
        refresh = self._refresh()
        self._metrics["browser_bundle_imports"] += 1
        return {
            "schema_version": "DesktopSessionArchiveFileImportReceipt-v1",
            "source_kind": batch.source_kind,
            "source_name": path.name,
            "source_sha256": receipt["source_sha256"].upper(),
            "imported_count": receipt["applied_count"],
            "failed_count": receipt["failed_count"],
            "verification_status": receipt["verification_status"],
            "retained_not_observed_count": receipt["retained_not_observed_count"],
            "record_count": refresh["record_count"],
            "remote_session_mutations": 0,
            "cookie_value_reads": 0,
            "browser_storage_value_reads": 0,
            "status": "PASS",
        }

    def import_browser_bundle(self, bundle: Any) -> dict[str, Any]:
        if isinstance(bundle, Mapping) and bundle.get("schema_version") == BrowserLibraryImporter.SCHEMA:
            try:
                raw_bytes = json.dumps(
                    bundle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                batch = BrowserLibraryImporter.parse_payload(
                    bundle,
                    raw_bytes=raw_bytes,
                    allowed_site_settings=(self.browser_sites_provider() if callable(getattr(self,'browser_sites_provider',None)) else None),
                )
                archive_receipt = self._ingest_archive_batch(batch)
            except ArchiveInputError as error:
                raise SessionProductError(str(error)) from error
            identities = archive_receipt["resolved_identities"]
            self._restore_hidden_projection_ids(identities)
            self._metrics["browser_bundle_imports"] += 1
            refresh = self._refresh()
            projection_ids = [
                _projection_id(provider, conversation_id)
                for provider, conversation_id in identities
            ]
            return {
                "schema_version": "DesktopBrowserSessionArchiveImportReceipt-v1",
                "provider_id": batch.provider,
                "imported_count": archive_receipt["applied_count"],
                "failed_count": archive_receipt["failed_count"],
                "local_projection_ids": projection_ids,
                "provider_counts": {
                    provider: len(projection_ids) if provider == batch.provider else 0
                    for provider in (*_VISIBLE_UI_PROFILES, _UNIVERSAL_PROVIDER_ID, batch.provider)
                },
                "verification_status": archive_receipt["verification_status"],
                "retained_not_observed_count": archive_receipt[
                    "retained_not_observed_count"
                ],
                "retained_previous_complete_count": archive_receipt[
                    "retained_previous_verified_count"
                ],
                "retained_previous_complete_ids": [],
                "retained_newer_over_stale_count": archive_receipt[
                    "retained_newer_over_stale_count"
                ],
                "source_sha256": archive_receipt["source_sha256"].upper(),
                "remote_session_mutations": 0,
                "cookie_value_reads": 0,
                "browser_storage_value_reads": 0,
                "record_count": refresh["record_count"],
                "status": "PASS",
            }
        normalized = self._validate_browser_bundle(bundle)
        imported: list[str] = []
        retained_previous_complete: list[str] = []
        provider_counts = {"deepseek": 0, "gemini": 0, "kimi": 0}
        for conversation in normalized["conversations"]:
            projection_id = _projection_id(
                conversation["provider_id"], conversation["provider_session_id"]
            )
            path = self._browser_session_root / f"{projection_id}.json"
            retain_existing = False
            if conversation.get("capture_status") == "ERROR" and path.is_file():
                try:
                    previous = self._validate_browser_conversation(
                        json.loads(path.read_text(encoding="utf-8"))
                    )
                    retain_existing = previous.get("capture_status", "COMPLETE") == "COMPLETE"
                except (OSError, json.JSONDecodeError, SessionProductError):
                    retain_existing = False
            if retain_existing:
                retained_previous_complete.append(projection_id)
            else:
                self._write_private_json(path, conversation)
                self._metrics["local_projection_writes"] += 1
            imported.append(projection_id)
            provider_counts[conversation["provider_id"]] += 1
        state = self._load()
        hidden = set(state["hidden_projection_ids"])
        restored = hidden.intersection(imported)
        if restored:
            state["hidden_projection_ids"] = sorted(hidden - restored)
            self._save(state)
        self._metrics["browser_bundle_imports"] += 1
        refresh = self._refresh()
        return {
            "schema_version": "DesktopBrowserSessionImportReceipt-v1",
            "imported_count": len(imported),
            "local_projection_ids": imported,
            "provider_counts": provider_counts,
            "retained_previous_complete_count": len(retained_previous_complete),
            "retained_previous_complete_ids": retained_previous_complete,
            "remote_session_mutations": 0,
            "cookie_value_reads": 0,
            "browser_storage_value_reads": 0,
            "record_count": refresh["record_count"],
            "status": "PASS",
        }

    def _validated_browser_snapshot_path(self, raw_path: Any) -> Path | None:
        if not isinstance(raw_path, str) or not raw_path:
            return None
        path = Path(raw_path).resolve()
        try:
            path.relative_to(self._browser_session_root)
        except ValueError:
            return None
        return path if windows_io_path(path).is_file() and path.suffix.casefold() == ".json" else None

    def _archive_browser_row(self, archived: Mapping[str, Any]) -> dict[str, Any] | None:
        provider_id = str(archived.get("provider") or "")
        is_custom = bool(re.fullmatch(r'web-[a-f0-9]{16}',provider_id))
        if provider_id not in {*_VISIBLE_UI_PROFILES, _UNIVERSAL_PROVIDER_ID} and not is_custom:
            return None
        source_kind = str(archived.get("source_kind") or "")
        if provider_id == _UNIVERSAL_PROVIDER_ID:
            if source_kind != BROWSER_CAPTURE_SOURCE_ACTIVE_TAB_V1:
                return None
            browser_profile = (
                "browser_current_page_v1",
                _universal_source_name(archived.get("source_locator")),
                "用户主动录入的当前可见网页会话",
                "existing_login_visible_ui",
            )
            model_name = "Web AI"
        else:
            browser_profile = (
                "browser_session_library_v4",
                _universal_source_name(archived.get('source_locator')) if is_custom else _VISIBLE_UI_SOURCE_NAMES[provider_id],
                "网页会话本地只读库中的完整会话",
                "existing_login_visible_ui",
            )
            model_name = 'Web AI' if is_custom else _VISIBLE_UI_MODELS[provider_id]
        profiles = {
            **{
                kind: browser_profile
                for kind in BROWSER_CAPTURE_SOURCE_KINDS
                if (
                    provider_id == _UNIVERSAL_PROVIDER_ID
                    and kind == BROWSER_CAPTURE_SOURCE_ACTIVE_TAB_V1
                )
                or (
                    provider_id != _UNIVERSAL_PROVIDER_ID
                    and kind != BROWSER_CAPTURE_SOURCE_ACTIVE_TAB_V1
                )
            },
            "DEEPSEEK_OFFICIAL_EXPORT": (
                "deepseek_official_export_v1",
                "DeepSeek 官方导出",
                "DeepSeek 官方导出的本机只读会话",
                "official_export",
            ),
            "GEMINI_TAKEOUT_ACTIVITY": (
                "gemini_takeout_activity_v1",
                "Gemini Takeout",
                "Gemini 官方活动导出的本机只读会话",
                "official_export",
            ),
        }
        profile = profiles.get(source_kind)
        if profile is None:
            return None
        source_format, source_name, summary, channel = profile
        completeness = str(archived.get("completeness") or "PARTIAL")
        row = self._make_row(
            provider_id=provider_id,
            provider_session_id=str(archived["provider_conversation_id"]),
            source_name=source_name,
            title=str(archived.get("title") or ""),
            summary=(
                summary
                if completeness == "COMPLETE"
                else f"{summary}（部分内容）"
            ),
            captured_at=str(archived.get("updated_at") or "UNKNOWN"),
            local_dir="",
            model=model_name,
            record_path=self._session_archive.database_path,
            partial=completeness != "COMPLETE",
            source_format=source_format,
            record_binding="SESSION_ARCHIVE_STABLE_ID_V1",
        )
        row["content_hash"] = str(archived.get("content_sha256") or row["content_hash"])
        row["ingested_at"] = _iso_timestamp(archived.get("first_seen_at"))
        row["time_basis"] = (
            "CONVERSATION_AT"
            if row["captured_at"] != "UNKNOWN"
            else "INGESTED_AT"
            if row["ingested_at"] != "UNKNOWN"
            else "UNKNOWN"
        )
        row["sync_status"] = completeness
        row["recency_bucket"] = "UNKNOWN"
        row["source_recency_rank"] = 0
        row["lineage"] = {
            "channel": channel,
            "mode": "non_destructive_local_session_archive",
            "source_format": source_format,
            "source_kind": source_kind,
        }
        row["delete_capability"] = "hide_local_projection"
        return row

    def _scan_browser_sessions(self) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        rows: list[dict[str, Any]] = []
        statuses = {
            provider: {
                "provider_id": provider,
                "source_name": source_name,
                "available": False,
                "record_count": 0,
                "status": "NO_IMPORTED_BROWSER_SESSION",
            }
            for provider, source_name in (
                ("deepseek", "DeepSeek 网页"),
                ("gemini", "Gemini 网页"),
                ("kimi", "Kimi 网页"),
                (_UNIVERSAL_PROVIDER_ID, "通用网页录入"),
            )
        }
        failures = {provider: 0 for provider in statuses}
        seen_projection_ids: set[str] = set()
        for archived in self._session_archive.all_sessions():
            row = self._archive_browser_row(archived)
            if row is None:
                continue
            provider_id = str(row["provider_id"])
            rows.append(row)
            seen_projection_ids.add(str(row["local_projection_id"]))
            statuses.setdefault(provider_id,dict(provider_id=provider_id,source_name=row['source_name'],available=False,record_count=0))
            statuses[provider_id]["available"] = (
                statuses[provider_id]["available"] or not row["partial"]
            )
            statuses[provider_id]["record_count"] += 1
            statuses[provider_id]["status"] = (
                "PASS_IMPORTED_SESSION_ARCHIVE"
                if not row["partial"]
                else "SESSION_ARCHIVE_PARTIAL_RECORDS"
            )
        for path in self._browser_session_root.glob("*.json"):
            try:
                conversation = self._validate_browser_conversation(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (OSError, json.JSONDecodeError, SessionProductError):
                continue
            provider_id = conversation["provider_id"]
            is_library_v3 = (
                conversation["capture_method"] == "BROWSER_COMPANION_BACKGROUND_TAB_V3"
            )
            if not is_library_v3 and not self._legacy_deepseek_visible_ui_projection:
                # Retain historical current-page files on disk for audit, but
                # never reintroduce them into the formal session-filtering session list.
                continue
            capture_status = str(conversation.get("capture_status") or "COMPLETE")
            row = self._make_row(
                provider_id=provider_id,
                provider_session_id=conversation["provider_session_id"],
                source_name=statuses[provider_id]["source_name"],
                title=conversation["title"],
                summary=(
                    "网页会话本地只读库同步失败，可保留身份后再次同步"
                    if capture_status == "ERROR"
                    else "网页会话本地只读库中的完整会话"
                    if is_library_v3
                    else "历史浏览器当前页只读快照（兼容）"
                ),
                captured_at=conversation.get("conversation_at", "UNKNOWN"),
                local_dir="",
                model={"deepseek": "DeepSeek Web", "gemini": "Gemini Web", "kimi": "Kimi Web"}[provider_id],
                record_path=path.resolve(),
                partial=capture_status != "COMPLETE",
                source_format=(
                    "browser_session_library_v3"
                    if is_library_v3
                    else "browser_visible_dom_complete_bundle_v2"
                ),
                record_binding=(
                    "BROWSER_SESSION_LIBRARY_STABLE_ID_V3"
                    if is_library_v3
                    else "BROWSER_VISIBLE_DOM_COMPLETE_SNAPSHOT_V2"
                ),
            )
            row["recency_bucket"] = conversation.get("recency_bucket", "UNKNOWN")
            if row["captured_at"] == "UNKNOWN":
                try:
                    row["ingested_at"] = _iso_timestamp(None, path.stat().st_mtime_ns)
                except OSError:
                    row["ingested_at"] = "UNKNOWN"
                row["time_basis"] = (
                    "INGESTED_AT"
                    if row["ingested_at"] != "UNKNOWN"
                    else "UNKNOWN"
                )
            row["source_recency_rank"] = int(conversation.get("source_recency_rank", 239))
            row["sync_status"] = capture_status
            row["lineage"] = {
                "channel": "existing_login_visible_ui",
                "mode": (
                    "browser_companion_background_tab_session_library"
                    if is_library_v3
                    else "retired_current_page_snapshot_compatibility"
                ),
                "source_format": row["source_format"],
                "capture_method": conversation["capture_method"],
            }
            row["delete_capability"] = (
                "hide_local_projection"
                if is_library_v3 or conversation["capture_method"] == "VISIBLE_DOM_FULL_SCROLL_V2"
                else "none"
            )
            if row["local_projection_id"] in seen_projection_ids:
                continue
            rows.append(row)
            statuses[provider_id]["available"] = (
                statuses[provider_id]["available"] or capture_status == "COMPLETE"
            )
            statuses[provider_id]["record_count"] += 1
            if capture_status == "ERROR":
                failures[provider_id] += 1
                statuses[provider_id]["status"] = "SESSION_LIBRARY_CAPTURE_ERRORS"
            elif is_library_v3:
                statuses[provider_id]["status"] = "PASS_IMPORTED_SESSION_LIBRARY"
            else:
                statuses[provider_id]["status"] = "PASS_RETIRED_SNAPSHOT_COMPATIBILITY"
        for provider_id, count in failures.items():
            if count:
                statuses[provider_id]["failed_record_count"] = count
        return rows, statuses

    def _visible_ui_row(
        self,
        provider_id: str,
        bundle: Mapping[str, Any],
        path: Path,
    ) -> dict[str, Any]:
        first_user = next(
            (value["text"] for value in bundle["messages"] if value["role"] == "user"),
            "",
        )
        source_name = _VISIBLE_UI_SOURCE_NAMES[provider_id]
        source_format = f"{provider_id}_visible_ui_normalized_bundle_v2"
        row = self._make_row(
            provider_id=provider_id,
            provider_session_id=str(bundle["conversation_id"]),
            source_name=source_name,
            title=_text(first_user, 96)
            or f"{source_name}只读会话 · {str(bundle['conversation_id'])[:8]}",
            summary="网页可见内容的本机只读记录",
            # A visible capture proves the message order, not when the
            # conversation happened.  Never substitute the capture/open time.
            captured_at="UNKNOWN",
            local_dir="",
            model=_VISIBLE_UI_MODELS[provider_id],
            record_path=path,
            partial=bool(bundle["source_truncated"]),
            source_format=source_format,
            record_binding=f"{provider_id.upper()}_LOCAL_VISIBLE_UI_COMPLETE_SNAPSHOT_V2",
        )
        row["lineage"] = {
            "channel": "existing_login_visible_ui",
            "mode": "visible_ui_read_only_full_scroll_snapshot",
            "source_format": source_format,
            "boundary_method": bundle["boundary_method"],
        }
        # The provider remains strictly read-only. Batch management only hides
        # Memorive's local projection id, so no remote conversation or retained
        # recovery snapshot is deleted.
        row["delete_capability"] = "hide_local_projection"
        try:
            row["ingested_at"] = _iso_timestamp(None, path.stat().st_mtime_ns)
        except OSError:
            row["ingested_at"] = "UNKNOWN"
        row["time_basis"] = (
            "INGESTED_AT"
            if row["ingested_at"] != "UNKNOWN"
            else "UNKNOWN"
        )
        return row

    def _restored_visible_ui_rows(self, provider_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self._visible_ui_root.glob(f"{provider_id}-*.json")):
            try:
                bundle = self._validate_visible_ui_bundle(
                    json.loads(path.read_text(encoding="utf-8")),
                    expected_provider=provider_id,
                )
                if bundle["source_truncated"]:
                    continue
                if bundle["boundary_method"] != "HOST_OBSERVED_VISIBLE_UI_FULL_SCROLL_V2":
                    continue
                rows.append(self._visible_ui_row(provider_id, bundle, path.resolve()))
            except (OSError, json.JSONDecodeError, SessionProductError):
                continue
        return rows

    def _scan_visible_ui(self, provider_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        source_name = _VISIBLE_UI_SOURCE_NAMES[provider_id]
        reader = self._visible_ui_readers.get(provider_id)
        if reader is None:
            return [], {
                "provider_id": provider_id,
                "source_name": source_name,
                "available": False,
                "record_count": 0,
                "status": "DISABLED_NOT_ASSESSED",
            }
        try:
            bundle = self._validate_visible_ui_bundle(
                reader.capture_visible_conversation(),
                expected_provider=provider_id,
            )
            if bundle["source_truncated"]:
                raise SessionProductError(
                    f"{provider_id.upper()}_VISIBLE_UI_CONVERSATION_INCOMPLETE"
                )
            if bundle["boundary_method"] != "HOST_OBSERVED_VISIBLE_UI_FULL_SCROLL_V2":
                raise SessionProductError(
                    f"{provider_id.upper()}_VISIBLE_UI_FULL_SCROLL_PROOF_MISSING"
                )
            path = self._write_visible_ui_snapshot(provider_id, bundle)
            row = self._visible_ui_row(provider_id, bundle, path)
            self._metrics["ui_session_reads"] += 1
            return [row], {
                "provider_id": provider_id,
                "source_name": source_name,
                "available": True,
                "record_count": 1,
                "status": "PASS_VISIBLE_UI_COMPLETE",
            }
        except Exception as error:
            error_text = _text(str(error), 160)
            return [], {
                "provider_id": provider_id,
                "source_name": source_name,
                "available": False,
                "record_count": 0,
                "status": "VISIBLE_UI_READ_FAILED",
                "error_code": error_text or type(error).__name__,
            }

    def _scan_deepseek(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Compatibility wrapper for visible-chat/chat-archive tests."""
        return self._scan_visible_ui("deepseek")
