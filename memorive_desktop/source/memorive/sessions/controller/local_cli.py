"""Sessions of local command-line tools found on disk: LocalCliSessionsMixin.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping
from memorive.platform.windows_paths import windows_io_path
from memorive.sessions.archive.models import BROWSER_CAPTURE_SOURCE_KINDS
from memorive.sessions.controller.common import (
    SessionProductError,
    _cli_title_needs_recovery,
    _cli_user_presentation,
    _full_message_text,
    _iso_timestamp,
    _memo_cli_prompt_title,
    _projection_id,
    _sha256_json,
    _text,
)


class LocalCliSessionsMixin:
    """Sessions of local command-line tools (Codex, Claude Code) found on disk, as page rows."""

    def _is_recent_row(self, row: Mapping[str, Any]) -> bool:
        lineage = row.get("lineage")
        if (
            isinstance(lineage, Mapping)
            and lineage.get("source_kind") in BROWSER_CAPTURE_SOURCE_KINDS
        ):
            # V4 browser-library rows have already passed the bounded source
            # proof: recent 14 days first, otherwise the provider sidebar's
            # most recent 30 conversations (with a hard 50-row ceiling).
            # Reapplying the generic 30-day timestamp filter here would erase
            # the explicitly allowed fallback whenever a provider exposes an
            # old or unreliable conversation timestamp.
            return True
        captured_at = str(row.get("captured_at") or "")
        observed: datetime | None = None
        if captured_at and captured_at != "UNKNOWN":
            try:
                observed = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
                if observed.tzinfo is None:
                    observed = observed.replace(tzinfo=timezone.utc)
                observed = observed.astimezone(timezone.utc)
            except ValueError:
                observed = None
        if observed is None:
            raw_path = row.get("source_record_path")
            if isinstance(raw_path, str) and raw_path:
                try:
                    observed = datetime.fromtimestamp(windows_io_path(Path(raw_path)).stat().st_mtime, timezone.utc)
                except OSError:
                    observed = None
        if observed is None:
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.discovery_window_days)
        return observed >= cutoff

    def _recent_rows(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return [row for row in rows if self._is_recent_row(row)]

    @staticmethod
    def _codex_embedded_session_id(path: Path) -> str:
        try:
            with windows_io_path(path).open("rb") as handle:
                payload = handle.read(1_048_576)
        except OSError:
            return ""
        for line in payload.splitlines():
            try:
                record = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(record, Mapping) or record.get("type") != "session_meta":
                continue
            value = record.get("payload")
            if isinstance(value, Mapping):
                return _text(value.get("id"), 160)
        return ""

    def _codex_database_binds_path(self, session_id: str, path: Path) -> bool:
        database = self._source_roots["codex_home"] / "state_5.sqlite"
        if not database.is_file():
            return False
        try:
            connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=1.5)
            try:
                connection.execute("PRAGMA query_only=ON")
                record = connection.execute(
                    "SELECT rollout_path FROM threads WHERE id = ? LIMIT 1", (session_id,)
                ).fetchone()
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            return False
        if not record or not isinstance(record[0], str):
            return False
        indexed = Path(record[0]).expanduser()
        if not indexed.is_absolute():
            indexed = self._source_roots["codex_home"] / indexed
        return indexed.resolve() == path.resolve()

    def _validated_record_path(
        self,
        provider_id: str,
        raw_path: Any,
        *,
        expected_session_id: str = "",
        allow_codex_indexed_external: bool = False,
        index_binding_already_verified: bool = False,
    ) -> Path | None:
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = self._source_roots[f"{provider_id}_home"] / candidate
        candidate = candidate.resolve()
        if not windows_io_path(candidate).is_file() or candidate.suffix.casefold() != ".jsonl":
            return None
        if self._path_within(candidate, self._allowed_record_roots(provider_id)):
            return candidate
        if provider_id != "codex" or not allow_codex_indexed_external or not expected_session_id:
            return None
        if not index_binding_already_verified and not self._codex_database_binds_path(expected_session_id, candidate):
            return None
        if self._codex_embedded_session_id(candidate) != expected_session_id:
            return None
        return candidate

    @staticmethod
    def _jsonl_regions(path: Path, *, head_bytes: int, tail_bytes: int) -> tuple[list[dict[str, Any]], bool]:
        path_io = windows_io_path(path)
        size = path_io.stat().st_size
        pieces: list[tuple[int, bytes]] = []
        with path_io.open("rb") as handle:
            head = handle.read(head_bytes)
            pieces.append((0, head))
            if size > head_bytes:
                offset = max(head_bytes, size - tail_bytes)
                handle.seek(offset)
                pieces.append((offset, handle.read(tail_bytes)))
        rows: list[dict[str, Any]] = []
        seen: set[bytes] = set()
        for offset, payload in pieces:
            lines = payload.splitlines()
            if offset > 0 and payload and not payload.startswith((b"{", b"[")) and lines:
                lines = lines[1:]
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped in seen:
                    continue
                seen.add(stripped)
                try:
                    value = json.loads(stripped.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    rows.append(value)
        return rows, size > head_bytes + tail_bytes

    def _make_row(
        self,
        *,
        provider_id: str,
        provider_session_id: str,
        source_name: str,
        title: str,
        summary: str,
        captured_at: Any,
        local_dir: str,
        model: str,
        record_path: Path | None,
        partial: bool,
        source_format: str,
        record_binding: str = "",
    ) -> dict[str, Any]:
        projection_id = _projection_id(provider_id, provider_session_id)
        record_io = windows_io_path(record_path) if record_path else None
        stat = record_io.stat() if record_io and record_io.is_file() else None
        content_identity = {
            "provider_id": provider_id,
            "provider_session_id": provider_session_id,
            "path": str(record_path) if record_path else "",
            "size": stat.st_size if stat else 0,
            "mtime_ns": stat.st_mtime_ns if stat else 0,
        }
        normalized_captured_at = (
            "UNKNOWN"
            if captured_at == "UNKNOWN"
            else _iso_timestamp(captured_at, stat.st_mtime_ns if stat else 0)
        )
        return {
            "local_projection_id": projection_id,
            "provider_id": provider_id,
            "provider_session_id": provider_session_id,
            "source_name": source_name,
            "title": _text(title, 240) or f"{source_name} 会话 · {provider_session_id[:8]}",
            "summary": _text(summary, 320) or "本机只读会话",
            "captured_at": normalized_captured_at,
            "ingested_at": "UNKNOWN",
            "time_basis": (
                "CONVERSATION_AT"
                if normalized_captured_at != "UNKNOWN"
                else "UNKNOWN"
            ),
            "local_dir": _text(local_dir, 320),
            "model": _text(model, 120),
            "partial": bool(partial or record_path is None),
            "delete_capability": "",
            "refine_status": "disabled",
            "content_hash": _sha256_json(content_identity),
            "privacy_class": "LOCAL_PRIVATE_SESSION_METADATA",
            "lineage": {
                "channel": "exact_local_cli_session_root",
                "mode": "metadata_first_selected_detail_on_demand",
                "source_format": source_format,
            },
            "stable_locator": f"memorive://session/{projection_id}",
            "deleted": False,
            "source_record_path": str(record_path) if record_path else "",
            "source_format": source_format,
            "source_record_binding": record_binding,
        }

    def _codex_display_title(self, title: Any, path: Path | None) -> str:
        raw = str(title or '')
        if not _cli_title_needs_recovery(raw):
            return _text(raw, 240)
        memo_title = _memo_cli_prompt_title(raw)
        if memo_title:
            return memo_title
        cleaned = _cli_user_presentation(raw)
        if cleaned and not _cli_title_needs_recovery(cleaned):
            return _text(cleaned, 240)
        if path is not None:
            # Only malformed/missing titles need a bounded metadata sample, on
            # the already validated exact session path. No full-body list scan.
            try:
                records, _ = self._jsonl_regions(path, head_bytes=1_048_576, tail_bytes=0)
                for record in records:
                    value = record.get('payload')
                    if record.get('type') != 'response_item' or not isinstance(value, Mapping):
                        continue
                    if value.get('type') != 'message' or value.get('role') != 'user':
                        continue
                    message_text = _full_message_text(value.get('content'))
                    memo_title = _memo_cli_prompt_title(message_text)
                    if memo_title:
                        return memo_title
                    candidate = _cli_user_presentation(message_text)
                    if candidate:
                        return _text(candidate, 240)
            except OSError:
                pass
        return ''  # Existing source-name/session-id fallback; never invent a title.

    @staticmethod
    def _codex_session_index_titles(home: Path) -> dict[str, str]:
        """Return the latest explicit session-index title for each Codex thread."""
        index = home / "session_index.jsonl"
        titles: dict[str, str] = {}
        if not index.is_file():
            return titles
        try:
            with index.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(value, Mapping):
                        continue
                    session_id = _text(value.get("id"), 160)
                    title = str(value.get("thread_name") or "").strip()
                    if session_id and title:
                        titles[session_id] = title
        except OSError:
            return {}
        return titles

    def _memo_internal_codex_cwd(self, value: Any) -> bool:
        rendered = str(value or "").strip()
        if not rendered:
            return False
        path = Path(rendered).resolve(strict=False)
        try:
            if path == self.state_root.parent or path.is_relative_to(self.state_root.parent):
                return True
        except (OSError, ValueError):
            pass
        parts = {part.casefold() for part in path.parts}
        if {"prompt-cache-lanes", "model_validation"} & parts:
            return True
        try:
            return (path / ".memorive-internal-codex-session.json").is_file()
        except OSError:
            return False

    def _scan_codex_database(self, database: Path) -> list[dict[str, Any]]:
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=2.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA busy_timeout=2000")
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(threads)")}
            if not {"id", "rollout_path"} <= columns:
                raise SessionProductError("CODEX_THREAD_INDEX_SCHEMA_UNSUPPORTED")
            wanted = [
                name
                for name in (
                    "id",
                    "rollout_path",
                    "created_at",
                    "updated_at",
                    "updated_at_ms",
                    "recency_at",
                    "recency_at_ms",
                    "cwd",
                    "preview",
                    "model",
                    "model_provider",
                    "has_user_event",
                    "archived",
                )
                if name in columns
            ]
            if "name" in columns:
                wanted.append("name")
            if "title" in columns and "first_user_message" in columns:
                wanted.append(
                    "CASE WHEN title IS NOT first_user_message THEN title ELSE NULL END AS explicit_title"
                )
            elif "title" in columns:
                wanted.append("title AS explicit_title")
            records = connection.execute(
                f"SELECT {', '.join(wanted)} FROM threads WHERE length(rollout_path) > 0"
            ).fetchall()
        finally:
            connection.close()
        rows: list[dict[str, Any]] = []
        excluded_internal = 0
        index_titles = self._codex_session_index_titles(database.parent)
        for record in records:
            value = dict(record)
            if self._memo_internal_codex_cwd(value.get("cwd")):
                excluded_internal += 1
                continue
            provider_session_id = _text(value.get("id"), 160)
            if not provider_session_id:
                continue
            record_path = self._validated_record_path(
                "codex",
                value.get("rollout_path"),
                expected_session_id=provider_session_id,
                allow_codex_indexed_external=True,
                index_binding_already_verified=True,
            )
            updated = (
                value.get("updated_at_ms")
                or value.get("recency_at_ms")
                or value.get("updated_at")
                or value.get("recency_at")
                or value.get("created_at")
            )
            rows.append(
                self._make_row(
                    provider_id="codex",
                    provider_session_id=provider_session_id,
                    source_name="Codex",
                    title=self._codex_display_title(
                        value.get("name")
                        or index_titles.get(provider_session_id)
                        or value.get("explicit_title"),
                        record_path,
                    ),
                    summary=_text(_cli_user_presentation(str(value.get("preview") or '')), 320),
                    captured_at=updated,
                    local_dir=_text(value.get("cwd"), 320),
                    model=_text(value.get("model") or value.get("model_provider"), 120),
                    record_path=record_path,
                    partial=record_path is None,
                    source_format="codex_state_v5_jsonl",
                    record_binding="CODEX_STATE5_EXACT_ID_PATH_V1",
                )
            )
        self._last_codex_internal_exclusions = excluded_internal
        return rows

    def _scan_codex_fallback(self, home: Path) -> list[dict[str, Any]]:
        index = home / "session_index.jsonl"
        if not index.is_file():
            return []
        file_map: dict[str, Path] = {}
        for root in self._allowed_record_roots("codex"):
            if not root.is_dir():
                continue
            for path in root.rglob("*.jsonl"):
                file_map.setdefault(path.stem.rsplit("_", 1)[-1], path.resolve())
        rows: list[dict[str, Any]] = []
        excluded_internal = 0
        with index.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(value, dict):
                    continue
                provider_session_id = _text(value.get("id"), 160)
                if not provider_session_id:
                    continue
                record_path = file_map.get(provider_session_id)
                # Old session_index rows do not carry cwd.  Use the bound
                # record only to recover the first session metadata record.
                cwd = ""
                if record_path is not None:
                    try:
                        records, _ = self._jsonl_regions(record_path, head_bytes=131_072, tail_bytes=0)
                        cwd = next((_text(r.get("payload", {}).get("cwd"), 320) for r in records if isinstance(r.get("payload"), Mapping) and r.get("payload", {}).get("cwd")), "")
                    except OSError:
                        cwd = ""
                if self._memo_internal_codex_cwd(cwd):
                    excluded_internal += 1
                    continue
                rows.append(
                    self._make_row(
                        provider_id="codex",
                        provider_session_id=provider_session_id,
                        source_name="Codex",
                        title=self._codex_display_title(value.get("thread_name"), record_path),
                        summary="本机 Codex 会话",
                        captured_at=value.get("updated_at"),
                        local_dir="",
                        model="",
                        record_path=record_path,
                        partial=record_path is None,
                        source_format="codex_session_index_jsonl",
                    )
                )
        self._last_codex_internal_exclusions = excluded_internal
        return rows

    def _scan_codex(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        home = self._source_roots["codex_home"]
        database = home / "state_5.sqlite"
        index = home / "session_index.jsonl"
        common = {
            "provider_id": "codex",
            "source_name": "Codex",
            "resolved_source_root": self._public_source_root(home),
            "source_selection": self._source_root_selection["codex"],
            "isolated_source": self._source_root_selection["codex"] == "EXPLICIT_ISOLATED_SOURCE",
        }
        if not home.is_dir():
            return [], {**common, "available": False, "record_count": 0, "status": "SOURCE_DIRECTORY_MISSING"}
        if not database.is_file() and not index.is_file():
            return [], {**common, "available": False, "record_count": 0, "status": "SOURCE_DATABASE_MISSING"}
        try:
            rows = self._scan_codex_database(database) if database.is_file() else self._scan_codex_fallback(home)
            return rows, {
                **common,
                "available": True,
                "record_count": len(rows),
                "scope": "USER_TASKS_ONLY",
                "excluded_internal_count": self._last_codex_internal_exclusions,
                "status": "PASS" if rows else "SOURCE_EMPTY",
            }
        except PermissionError as error:
            return [], {
                **common,
                "available": True,
                "record_count": 0,
                "status": "SOURCE_PERMISSION_DENIED",
                "error_code": type(error).__name__,
            }
        except (OSError, sqlite3.Error, SessionProductError) as error:
            return [], {
                **common,
                "available": True,
                "record_count": 0,
                "status": "SOURCE_PARSE_FAILED",
                "error_code": type(error).__name__,
            }

    def _claude_metadata(self, path: Path) -> dict[str, Any] | None:
        records, truncated = self._jsonl_regions(path, head_bytes=1_048_576, tail_bytes=1_048_576)
        provider_session_id = path.stem
        custom_title = ""
        last_prompt = ""
        first_user = ""
        local_dir = ""
        model = ""
        timestamp: Any = None
        for record in records:
            provider_session_id = _text(record.get("sessionId"), 160) or provider_session_id
            timestamp = record.get("timestamp") or timestamp
            local_dir = _text(record.get("cwd"), 320) or local_dir
            if record.get("type") == "custom-title":
                custom_title = _text(record.get("customTitle"), 240) or custom_title
            elif record.get("type") == "last-prompt":
                last_prompt = _text(_cli_user_presentation(str(record.get("lastPrompt") or '')), 320) or last_prompt
            message = record.get("message")
            if not isinstance(message, Mapping):
                continue
            role = message.get("role")
            if role == "user" and not first_user and record.get('isMeta') is not True:
                first_user = _text(_cli_user_presentation(_full_message_text(message.get("content"))), 320)
            if role == "assistant":
                model = _text(message.get("model"), 120) or model
        if not provider_session_id:
            return None
        title = custom_title or last_prompt or first_user
        summary = last_prompt if custom_title and last_prompt != custom_title else (local_dir or "本机 Claude Code 会话")
        return self._make_row(
            provider_id="claude",
            provider_session_id=provider_session_id,
            source_name="Claude Code",
            title=title,
            summary=summary,
            captured_at=timestamp,
            local_dir=local_dir,
            model=model,
            record_path=path.resolve(),
            # Head/tail sampling is used only for inexpensive list metadata.
            # It says nothing about selected-detail completeness because the
            # detail reader reopens and validates the whole JSONL snapshot.
            partial=False,
            source_format="claude_code_project_jsonl",
        )

    def _scan_claude(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        projects = self._source_roots["claude_home"] / "projects"
        rows: list[dict[str, Any]] = []
        errors = 0
        if projects.is_dir():
            for project in projects.iterdir():
                if not project.is_dir():
                    continue
                for path in project.glob("*.jsonl"):
                    try:
                        row = self._claude_metadata(path)
                    except OSError:
                        errors += 1
                        continue
                    if row:
                        rows.append(row)
        return rows, {
            "provider_id": "claude",
            "source_name": "Claude Code",
            "available": projects.is_dir(),
            "record_count": len(rows),
            "failed_record_count": errors,
            "status": "PASS" if rows else ("EMPTY_OR_UNAVAILABLE" if errors == 0 else "SOURCE_READ_PARTIAL"),
        }
