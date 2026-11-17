"""The session list and a session's messages: SessionListingMixin.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any, Mapping
from memorive.sessions.controller.common import (
    SESSIONS_METHODS,
    SessionProductError,
    _UNIVERSAL_PROVIDER_ID,
    _VISIBLE_UI_PROFILES,
    _VISIBLE_UI_SOURCE_NAMES,
    _cli_user_presentation,
    _full_message_text,
    _iso_timestamp,
)


class SessionListingMixin:
    """The session list and one session's messages as the page shows them; the page contract."""

    def _refresh(self) -> dict[str, Any]:
        state = self._load()
        saved_ids = set(state["sessions"])
        codex_rows, codex_status = self._scan_codex()
        claude_rows, claude_status = self._scan_claude()
        visible_rows: dict[str, list[dict[str, Any]]] = {}
        visible_statuses: dict[str, dict[str, Any]] = {}
        for provider_id in _VISIBLE_UI_PROFILES:
            if self._legacy_deepseek_visible_ui_projection:
                rows, status = self._scan_visible_ui(provider_id)
            else:
                rows, status = [], {
                    "provider_id": provider_id,
                    "source_name": _VISIBLE_UI_SOURCE_NAMES[provider_id],
                    "available": False,
                    "record_count": 0,
                    "status": "BROWSER_COMPANION_REQUIRED",
                }
            if not rows and self._legacy_deepseek_visible_ui_projection:
                rows = self._restored_visible_ui_rows(provider_id)
                if rows:
                    status["available"] = True
                    status["record_count"] = len(rows)
                    status["stale_snapshot_retained"] = True
                    status["status"] = "PASS_VISIBLE_UI_SNAPSHOT_RETAINED"
            visible_rows[provider_id] = rows
            visible_statuses[provider_id] = status
        browser_rows, browser_statuses = self._scan_browser_sessions()
        browser_by_provider = {
            provider: [row for row in browser_rows if row["provider_id"] == provider]
            for provider in (*_VISIBLE_UI_PROFILES, _UNIVERSAL_PROVIDER_ID)
        }
        # The discovery window limits new imports. A previously saved local
        # conversation remains available while the fresh source scan still
        # validates its identity and path, even after that window expires.
        codex_rows = [row for row in codex_rows if row["local_projection_id"] in saved_ids or self._is_recent_row(row)]
        claude_rows = [row for row in claude_rows if row["local_projection_id"] in saved_ids or self._is_recent_row(row)]
        codex_status["record_count"] = len(codex_rows)
        claude_status["record_count"] = len(claude_rows)
        codex_status["discovery_window_days"] = self.discovery_window_days
        claude_status["discovery_window_days"] = self.discovery_window_days
        provider_rows: dict[str, list[dict[str, Any]]] = {}
        statuses: dict[str, dict[str, Any]] = {}
        for provider_id in _VISIBLE_UI_PROFILES:
            rows = self._recent_rows(
                [*visible_rows[provider_id], *browser_by_provider[provider_id]]
            )
            provider_rows[provider_id] = rows
            status = deepcopy(browser_statuses[provider_id])
            status["visible_ui_status"] = visible_statuses[provider_id].get("status")
            if visible_rows[provider_id]:
                status["available"] = True
                status["status"] = (
                    "PASS_VISIBLE_UI_AND_IMPORTED_COMPLETE"
                    if browser_by_provider[provider_id]
                    else visible_statuses[provider_id]["status"]
                )
                if visible_statuses[provider_id].get("stale_snapshot_retained"):
                    status["stale_snapshot_retained"] = True
            elif browser_by_provider[provider_id]:
                status["status"] = browser_statuses[provider_id]["status"]
            elif visible_statuses[provider_id].get("error_code"):
                status["error_code"] = visible_statuses[provider_id]["error_code"]
                status["status"] = visible_statuses[provider_id]["status"]
            status["record_count"] = len(rows)
            status["discovery_window_days"] = self.discovery_window_days
            statuses[provider_id] = status
        universal_rows = self._recent_rows(browser_by_provider[_UNIVERSAL_PROVIDER_ID])
        provider_rows[_UNIVERSAL_PROVIDER_ID] = universal_rows
        universal_status = deepcopy(browser_statuses[_UNIVERSAL_PROVIDER_ID])
        universal_status["record_count"] = len(universal_rows)
        universal_status["discovery_window_days"] = self.discovery_window_days
        statuses[_UNIVERSAL_PROVIDER_ID] = universal_status
        custom_rows = [row for row in browser_rows if re.fullmatch(r'web-[a-f0-9]{16}',row['provider_id'])]
        statuses.update({k:v for k,v in browser_statuses.items() if re.fullmatch(r'web-[a-f0-9]{16}',k)})
        merged: dict[str, dict[str, Any]] = {}
        for row in [
            *codex_rows,
            *claude_rows,
            *provider_rows["deepseek"],
            *provider_rows["gemini"],
            *provider_rows["kimi"],
            *provider_rows[_UNIVERSAL_PROVIDER_ID],
            *self._recent_rows(custom_rows),
        ]:
            merged[row["local_projection_id"]] = row
        state["sessions"] = merged
        state["source_status"] = {
            "codex": codex_status,
            "claude": claude_status,
            **statuses,
        }
        self._save(state)
        self._metrics["metadata_refreshes"] += 1
        return {
            "record_count": len(merged),
            "discovery_window_days": self.discovery_window_days,
            "source_status": deepcopy(state["source_status"]),
            "available_source_count": sum(bool(row.get("available")) for row in state["source_status"].values()),
        }

    def _filtered_rows(self, source: str = "all", search: str = "") -> list[dict[str, Any]]:
        if source not in {
            "all", "codex", "claude", "deepseek", "gemini", "kimi",
            _UNIVERSAL_PROVIDER_ID,
        }:
            raise SessionProductError("SESSION_SOURCE_FILTER_INVALID")
        term = search.strip().casefold()
        rows: list[dict[str, Any]] = []
        state = self._load()
        hidden = set(state["hidden_projection_ids"])
        for row in state["sessions"].values():
            if row.get("deleted") or row.get("local_projection_id") in hidden:
                continue
            if source != "all" and row["provider_id"] != source and not (source==_UNIVERSAL_PROVIDER_ID and row['provider_id'].startswith('web-')):
                continue
            domain = " ".join(
                str(row.get(key, ""))
                for key in ("provider_id", "source_name", "title", "summary", "local_dir", "model", "provider_session_id")
            ).casefold()
            if term and term not in domain:
                continue
            rows.append(self._public(row))
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        def conversation_time_key(
            row: Mapping[str, Any],
        ) -> tuple[int, float, int, int, str]:
            captured_at = str(row.get("captured_at") or "UNKNOWN")
            if captured_at == "UNKNOWN":
                ingested_at = str(row.get("ingested_at") or "UNKNOWN")
                if ingested_at != "UNKNOWN":
                    try:
                        ingested = datetime.fromisoformat(
                            ingested_at.replace("Z", "+00:00")
                        )
                        return (
                            1,
                            ingested.timestamp(),
                            4,
                            0,
                            str(row["local_projection_id"]),
                        )
                    except ValueError:
                        pass
                bucket = str(row.get("recency_bucket") or "UNKNOWN")
                bucket_score = {
                    "TODAY": 4,
                    "LAST_7_DAYS": 3,
                    "LAST_30_DAYS": 2,
                    "UNKNOWN": 1,
                }.get(bucket, 0)
                approximate_time = {
                    "TODAY": today_start.timestamp(),
                    "LAST_7_DAYS": (today_start - timedelta(days=7)).timestamp(),
                    "LAST_30_DAYS": (today_start - timedelta(days=30)).timestamp(),
                    "UNKNOWN": float("-inf"),
                }.get(bucket, float("-inf"))
                raw_rank = row.get("source_recency_rank", 239)
                rank = raw_rank if isinstance(raw_rank, int) else 239
                return (
                    1 if approximate_time != float("-inf") else 0,
                    approximate_time,
                    bucket_score,
                    -rank,
                    str(row["local_projection_id"]),
                )
            try:
                occurred = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
                timestamp = occurred.timestamp()
            except ValueError:
                timestamp = float("-inf")
            return (1, timestamp, 5, 0, str(row["local_projection_id"]))

        return sorted(rows, key=conversation_time_key, reverse=True)

    def _rows(
        self,
        source: str = "all",
        search: str = "",
        limit_per_source: int = 30,
    ) -> list[dict[str, Any]]:
        if limit_per_source not in {10, 20, 30, 50}:
            raise SessionProductError("SESSION_LIMIT_PER_SOURCE_INVALID")
        rows = self._filtered_rows(source, search)
        if source != "all":
            return rows[:limit_per_source]
        limited: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for row in rows:
            provider_id = str(row["provider_id"])
            count = counts.get(provider_id, 0)
            if count >= limit_per_source:
                continue
            counts[provider_id] = count + 1
            limited.append(row)
        return limited

    @staticmethod
    def _page_rows(
        rows: list[dict[str, Any]],
        page_offset: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if page_offset < 0:
            raise SessionProductError("SESSION_PAGE_OFFSET_INVALID")
        if page_size != 30:
            raise SessionProductError("SESSION_PAGE_SIZE_INVALID")
        page = rows[page_offset : page_offset + page_size]
        next_offset = page_offset + len(page)
        has_more = next_offset < len(rows)
        return page, {
            "page_offset": page_offset,
            "page_size": page_size,
            "page_available_count": len(rows),
            "has_more": has_more,
            "next_offset": next_offset if has_more else None,
        }

    def _get_internal(self, projection_id: str) -> dict[str, Any]:
        state = self._load()
        row = state["sessions"].get(projection_id)
        if (
            not row
            or row.get("deleted")
            or projection_id in set(state["hidden_projection_ids"])
        ):
            raise SessionProductError("SESSION_NOT_FOUND")
        return row

    @staticmethod
    def _local_message_snapshot(
        provider_id: str,
        path: Path,
        *, presentation: bool = False,
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        """Read an append-only JSONL prefix completely without retaining tool records."""
        try:
            before = path.stat()
            snapshot_size = int(before.st_size)
            snapshot_mtime_ns = int(before.st_mtime_ns)
            messages: list[dict[str, str]] = []
            presentation_filtered = False
            with path.open("rb") as handle:
                remaining = snapshot_size
                line_number = 0
                while remaining > 0:
                    payload = handle.readline(remaining)
                    if not payload:
                        raise SessionProductError("SESSION_SOURCE_RECORD_CHANGED_DURING_READ")
                    remaining -= len(payload)
                    line_number += 1
                    stripped = payload.strip()
                    if not stripped:
                        continue
                    try:
                        decoded = stripped.decode("utf-8")
                        if line_number == 1:
                            decoded = decoded.lstrip("\ufeff")
                        record = json.loads(decoded)
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise SessionProductError("SESSION_SOURCE_RECORD_MALFORMED_JSONL") from error
                    if not isinstance(record, Mapping):
                        continue
                    role = ""
                    content: Any = None
                    timestamp = record.get("timestamp")
                    if provider_id == "codex" and record.get("type") == "response_item":
                        value = record.get("payload")
                        if isinstance(value, Mapping) and value.get("type") == "message":
                            role = str(value.get("role") or "")
                            content = value.get("content")
                    elif provider_id == "claude" and record.get("type") in {"user", "assistant"}:
                        value = record.get("message")
                        if isinstance(value, Mapping):
                            role = str(value.get("role") or record.get("type") or "")
                            content = value.get("content")
                    if role not in {"user", "assistant"}:
                        continue
                    if presentation and record.get('isMeta') is True:
                        presentation_filtered = True
                        continue
                    rendered = _full_message_text(content)
                    if presentation and role == 'user':
                        cleaned = _cli_user_presentation(rendered)
                        presentation_filtered = presentation_filtered or cleaned != rendered
                        rendered = cleaned
                    if rendered:
                        messages.append(
                            {
                                "role": role,
                                "content": rendered,
                                "timestamp": _iso_timestamp(timestamp),
                            }
                        )
            after = path.stat()
        except SessionProductError:
            raise
        except OSError as error:
            raise SessionProductError("SESSION_SOURCE_RECORD_UNAVAILABLE") from error
        if int(after.st_size) < snapshot_size:
            raise SessionProductError("SESSION_SOURCE_RECORD_CHANGED_DURING_READ")
        if int(after.st_size) == snapshot_size and int(after.st_mtime_ns) != snapshot_mtime_ns:
            raise SessionProductError("SESSION_SOURCE_RECORD_CHANGED_DURING_READ")
        return messages, {
            "status": "COMPLETE",
            "scope": "USER_ASSISTANT_TEXT_WITH_RUNTIME_ENVELOPES_HIDDEN" if presentation_filtered else "ALL_USER_ASSISTANT_TEXT_MESSAGES",
            "snapshot_mode": "APPEND_ONLY_PREFIX_AT_OPEN",
            "source_snapshot_bytes": snapshot_size,
            "message_count": len(messages),
        }

    def _messages_for(
        self,
        row: Mapping[str, Any],
        *, presentation: bool = False,
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        provider_id = str(row["provider_id"])
        if row.get("source_format") in {
            "browser_session_library_v4",
            "browser_current_page_v1",
            "deepseek_official_export_v1",
            "gemini_takeout_activity_v1",
        }:
            conversation_id = str(row.get("provider_session_id") or "")
            try:
                archived = self._session_archive.get_session(provider_id, conversation_id)
                archived_messages = self._session_archive.get_messages(
                    provider_id, conversation_id
                )
            except KeyError as error:
                raise SessionProductError("SESSION_SOURCE_RECORD_UNAVAILABLE") from error
            messages = [
                {
                    "role": str(message["role"]),
                    "content_kind": str(message["content_kind"]),
                    "content": str(message["content"]),
                    "timestamp": str(message["occurred_at"]),
                }
                for message in archived_messages
            ]
            completeness = str(archived.get("completeness") or "PARTIAL")
            self._metrics["ui_session_reads"] += 1
            self._metrics["real_session_content_captured"] += int(bool(messages))
            return messages, {
                "status": completeness,
                "scope": "ALL_ARCHIVED_USER_ASSISTANT_CONTENT",
                "snapshot_mode": "NON_DESTRUCTIVE_LOCAL_SESSION_ARCHIVE",
                "message_count": len(messages),
            }
        if row.get("source_format") in {
            "browser_visible_dom_complete_bundle_v1",
            "browser_visible_dom_complete_bundle_v2",
            "browser_session_library_v3",
        }:
            path = self._validated_browser_snapshot_path(row.get("source_record_path"))
            if path is None:
                raise SessionProductError("SESSION_SOURCE_RECORD_UNAVAILABLE")
            try:
                conversation = self._validate_browser_conversation(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (OSError, json.JSONDecodeError) as error:
                raise SessionProductError("SESSION_SOURCE_RECORD_UNAVAILABLE") from error
            if (
                conversation["provider_id"] != provider_id
                or conversation["provider_session_id"] != row.get("provider_session_id")
            ):
                raise SessionProductError("SESSION_SOURCE_RECORD_UNAVAILABLE")
            if conversation.get("capture_status") == "ERROR":
                raise SessionProductError("BROWSER_SESSION_CAPTURE_FAILED")
            messages = [
                {
                    "role": message["role"],
                    "content": message["text"],
                    "timestamp": conversation.get("conversation_at", "UNKNOWN"),
                }
                for message in conversation["messages"]
            ]
            self._metrics["ui_session_reads"] += 1
            self._metrics["real_session_content_captured"] += int(bool(messages))
            return messages, {
                "status": "COMPLETE",
                "scope": "ALL_USER_ASSISTANT_TEXT_MESSAGES",
                "snapshot_mode": (
                    "BROWSER_COMPANION_BACKGROUND_TAB_SESSION_LIBRARY"
                    if row.get("source_format") == "browser_session_library_v3"
                    else "BROWSER_VISIBLE_DOM_FULL_SCROLL_AT_EXPORT"
                ),
                "message_count": len(messages),
            }
        if (
            provider_id in _VISIBLE_UI_PROFILES
            and row.get("source_format")
            == f"{provider_id}_visible_ui_normalized_bundle_v2"
        ):
            path = self._validated_visible_ui_path(row.get("source_record_path"))
            if path is None:
                raise SessionProductError("SESSION_SOURCE_RECORD_UNAVAILABLE")
            try:
                bundle = self._validate_visible_ui_bundle(
                    json.loads(path.read_text(encoding="utf-8")),
                    expected_provider=provider_id,
                )
            except (OSError, json.JSONDecodeError) as error:
                raise SessionProductError("SESSION_SOURCE_RECORD_UNAVAILABLE") from error
            if bundle["conversation_id"] != row.get("provider_session_id"):
                raise SessionProductError("SESSION_SOURCE_RECORD_UNAVAILABLE")
            if bundle["source_truncated"]:
                raise SessionProductError("VISIBLE_UI_CONVERSATION_INCOMPLETE")
            if bundle["boundary_method"] != "HOST_OBSERVED_VISIBLE_UI_FULL_SCROLL_V2":
                raise SessionProductError("VISIBLE_UI_FULL_SCROLL_PROOF_MISSING")
            messages = [
                {
                    "role": value["role"],
                    "content": value["text"],
                    "timestamp": "UNKNOWN",
                }
                for value in bundle["messages"]
            ]
            self._metrics["ui_session_reads"] += 1
            self._metrics["real_session_content_captured"] += int(bool(messages))
            return messages, {
                "status": "COMPLETE",
                "scope": "ALL_USER_ASSISTANT_TEXT_MESSAGES",
                "snapshot_mode": "VISIBLE_UI_FULL_SCROLL_AT_CAPTURE",
                "message_count": len(messages),
            }
        allow_indexed_external = (
            provider_id == "codex"
            and row.get("source_record_binding") == "CODEX_STATE5_EXACT_ID_PATH_V1"
        )
        path = self._validated_record_path(
            provider_id,
            row.get("source_record_path"),
            expected_session_id=str(row.get("provider_session_id") or ""),
            allow_codex_indexed_external=allow_indexed_external,
        )
        if path is None:
            raise SessionProductError("SESSION_SOURCE_RECORD_UNAVAILABLE")
        messages, completeness = self._local_message_snapshot(provider_id, path, presentation=presentation)
        self._metrics["cli_session_reads"] += 1
        self._metrics["real_session_content_captured"] += int(bool(messages))
        return messages, completeness

    def _source_capabilities(self) -> list[dict[str, Any]]:
        status = self._load()["source_status"]
        local_sources = [
            {
                "provider_id": provider_id,
                "source_name": source_name,
                "channel": "exact_local_cli_session_root",
                "metadata_list": True,
                "selected_detail_read": True,
                "refine": False,
                "delete": False,
                "remote_mutation": False,
                "available": bool(status.get(provider_id, {}).get("available")),
            }
            for provider_id, source_name in (("codex", "Codex"), ("claude", "Claude Code"))
        ]
        return [
            *local_sources,
            {
                "provider_id": "deepseek",
                "source_name": "DeepSeek 网页",
                "channel": "existing_login_visible_ui",
                "metadata_list": True,
                "selected_detail_read": True,
                "refine": True,
                "delete": False,
                "remote_mutation": False,
                "available": bool(status.get("deepseek", {}).get("available")),
            },
            *[
                {
                    "provider_id": provider_id,
                    "source_name": source_name,
                    "channel": "existing_login_visible_ui",
                    "metadata_list": True,
                    "selected_detail_read": True,
                    "refine": True,
                    "delete": False,
                    "remote_mutation": False,
                    "available": True,
                }
                for provider_id, source_name in (("gemini", "Gemini 网页"), ("kimi", "Kimi 网页"))
                if int(status.get(provider_id, {}).get("record_count", 0)) > 0
            ],
            *(
                [
                    {
                        "provider_id": _UNIVERSAL_PROVIDER_ID,
                        "source_name": "通用网页录入",
                        "channel": "existing_login_visible_ui",
                        "metadata_list": True,
                        "selected_detail_read": True,
                        "refine": True,
                        "delete": False,
                        "remote_mutation": False,
                        "available": bool(
                            status.get(_UNIVERSAL_PROVIDER_ID, {}).get("record_count")
                        ),
                    }
                ]
                if int(status.get(_UNIVERSAL_PROVIDER_ID, {}).get("record_count", 0)) > 0
                else []
            ),
        ]

    def _contract(self) -> dict[str, Any]:
        names = (
            "SessionSourceCapabilityMatrix.json",
            "FrozenSourceAccessEnvelope.json",
            "SessionIdentityDedupeLineageContract.json",
            "SessionPrivacyProjectionContract.json",
            "SessionCaptureWorkflowContract.json",
            "SessionsSessionActionStateContract.json",
            "SessionFaultDenominator.json",
            "SessionAcceptanceDenominator.json",
        )
        contracts = {name: json.loads((self.contract_root / name).read_text(encoding="utf-8")) for name in names}
        source_status = self._load()["source_status"]
        available = sum(bool(value.get("available")) for value in source_status.values())
        return {
            "schema_version": "SessionsSessionsProductContract-v2",
            "method_count": len(SESSIONS_METHODS),
            "methods": sorted(SESSIONS_METHODS),
            "contracts": contracts,
            "queries": ["list_sessions", "get_session", "get_provider_capabilities"],
            "actions": ["delete_session", "refine_session"],
            "provider_implementation": "LOCAL_CLI_EDGE_UI_AND_BROWSER_VISIBLE_DOM_READ_ONLY",
            "real_source_channels_assessed": min(3, available),
            "raw_private_default_local_only": True,
            "remote_session_mutation_authorized": False,
            "status": "PASS",
        }

    def _service_ready(self) -> bool:
        health = self.service.call("service.health", {})
        required = {
            "schema_version",
            "protocol_version",
            "adapter_kind",
            "request_count",
            "auth_rejection_count",
            "connection_count",
            "uptime_seconds",
            "external_network_calls",
            "external_model_calls",
            "credential_value_reads",
        }
        return (
            set(health) == required
            and health.get("schema_version") == "ApplicationServiceHealth-v1"
            and health.get("protocol_version") == "1.0"
            and isinstance(health.get("adapter_kind"), str)
            and bool(health["adapter_kind"])
            and all(health.get(key) == 0 for key in ("external_network_calls", "external_model_calls", "credential_value_reads"))
        )
