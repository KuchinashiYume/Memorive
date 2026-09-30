from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import sqlite3
import threading
import time
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit
import uuid

from memorive_folder_management.policy import windows_io_path
from memorive_session_archive import (
    ArchiveInputError,
    BrowserLibraryImporter,
    DeepSeekOfficialImporter,
    GeminiTakeoutImporter,
    SessionArchive,
)
from memorive_session_archive.models import (
    BROWSER_CAPTURE_SOURCE_ACTIVE_TAB_V1,
    BROWSER_CAPTURE_SOURCE_KINDS,
    ImportBatch,
    ImportFailure,
    NormalizedConversation,
    NormalizedMessage,
)
from memorive_current_task.refinement_tasks import (
    REFINEMENT_NODE_IDS,
    RefinementTaskCancelled,
    RefinementTaskError,
    safe_error_code,
)
from knowledge_feedback.conversation_refinement import (
    validate_typed_refinement_item,
)
from memorive_settings.model_capabilities import infer_api_model_capability


_PROJECTION_STATE_LOCKS_GUARD = threading.Lock()
_PROJECTION_STATE_LOCKS: dict[str, threading.RLock] = {}


def _projection_state_lock(path: Path) -> threading.RLock:
    key = str(path.resolve()).casefold()
    with _PROJECTION_STATE_LOCKS_GUARD:
        lock = _PROJECTION_STATE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROJECTION_STATE_LOCKS[key] = lock
        return lock


SESSIONS_METHODS = frozenset(
    {
        "sessions.get_contract",
        "sessions.bootstrap",
        "sessions.list",
        "sessions.get",
        "sessions.get_provider_capabilities",
        "sessions.capture_intent",
        "sessions.capture_progress",
        "sessions.capture_cancel",
        "sessions.capture_retry",
        "sessions.refine",
        "sessions.refinement_cancel",
        "sessions.refinement_start",
        "sessions.refinement_settings_get",
        "sessions.refinement_settings_save",
        "sessions.refinement_get",
        "sessions.delete_local",
        "sessions.bulk_delete_local",
        "sessions.refresh",
        "sessions.resolve_locator",
        "sessions.inject_fault",
        "sessions.restore",
        "sessions.effect_metrics",
    }
)

REAL_CHANNELS = frozenset(
    {
        "official_export",
        "formal_provider_api",
        "existing_login_visible_ui",
        "exact_local_cli_session_root",
    }
)

_PUBLIC_FIELDS = frozenset(
    {
        "local_projection_id",
        "provider_id",
        "provider_session_id",
        "source_name",
        "title",
        "summary",
        "captured_at",
        "ingested_at",
        "time_basis",
        "local_dir",
        "model",
        "partial",
        "delete_capability",
        "refine_status",
        "content_hash",
        "privacy_class",
        "lineage",
        "stable_locator",
        "deleted",
        "refined_summary",
        "source_format",
        "recency_bucket",
        "source_recency_rank",
        "sync_status",
    }
)

_VISIBLE_UI_PROFILES = {
    "deepseek": "DEEPSEEK_EDGE_VISIBLE_UI_V1",
    "gemini": "GEMINI_EDGE_VISIBLE_UI_V1",
    "kimi": "KIMI_EDGE_VISIBLE_UI_V1",
}
_VISIBLE_UI_SOURCE_NAMES = {
    "deepseek": "DeepSeek 网页",
    "gemini": "Gemini 网页",
    "kimi": "Kimi 网页",
}
_VISIBLE_UI_MODELS = {
    "deepseek": "DeepSeek Web",
    "gemini": "Gemini Web",
    "kimi": "Kimi Web",
}
_UNIVERSAL_PROVIDER_ID = "universal"
_UNIVERSAL_SITE_NAMES = {
    "chat.qwen.ai": "千问网页",
    "qianwen.com": "千问网页",
    "www.qianwen.com": "千问网页",
    "tongyi.aliyun.com": "千问网页",
    "chatgpt.com": "ChatGPT 网页",
    "claude.ai": "Claude 网页",
    "www.doubao.com": "豆包网页",
    "doubao.com": "豆包网页",
    "yuanbao.tencent.com": "腾讯元宝网页",
    "poe.com": "Poe 网页",
}


def _universal_source_name(source_locator: Any) -> str:
    try:
        host = (urlsplit(str(source_locator or "")).hostname or "").lower()
    except ValueError:
        host = ""
    if host in _UNIVERSAL_SITE_NAMES:
        return f"{_UNIVERSAL_SITE_NAMES[host]} · 当前页录入"
    safe_host = re.sub(r"[^a-z0-9.-]+", "", host)[:96]
    return f"{safe_host or '通用 AI 网页'} · 当前页录入"


class SessionProductError(ValueError):
    pass


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


def _text(value: Any, limit: int = 320) -> str:
    if value is None:
        return ""
    rendered = " ".join(str(value).replace("\x00", " ").split())
    if len(rendered) <= limit:
        return rendered
    return rendered[: max(1, limit - 1)].rstrip() + "…"


def _message_text(content: Any, limit: int = 12_000) -> str:
    if isinstance(content, str):
        return _text(content, limit)
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for item in content:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") not in {"text", "input_text", "output_text"}:
            continue
        value = item.get("text")
        if isinstance(value, str) and value.strip():
            chunks.append(value.strip())
    return _text("\n\n".join(chunks), limit)


def _full_message_text(content: Any) -> str:
    """Render one user/assistant message without collapsing or clipping it."""
    if isinstance(content, str):
        return content.replace("\x00", " ").strip()
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for item in content:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") not in {"text", "input_text", "output_text"}:
            continue
        value = item.get("text")
        if isinstance(value, str) and value.strip():
            chunks.append(value.replace("\x00", " ").strip())
    return "\n\n".join(chunks).strip()


def _cli_user_presentation(text: str) -> str:
    """Remove recognized leading runtime envelopes, never arbitrary quoted text.

    Display only: raw capture, source hashes and refinement input stay unchanged.
    Match complete envelopes before clipping metadata into a one-line title.
    """
    value = text.strip()
    runtime_envelopes = (
        "recommended_plugins",
        "skills_instructions",
        "permissions instructions",
        "collaboration_mode",
        "apps_instructions",
        "plugins_instructions",
        "app-context",
        "image_resize_notice",
        "local-command-caveat",
        "command-name",
        "command-message",
        "command-args",
        "local-command-stdout",
    )
    while value:
        previous = value
        value = re.sub(
            r"\A# AGENTS\.md instructions for [^\r\n]+\s+<INSTRUCTIONS>.*?</INSTRUCTIONS>\s*",
            "", value, count=1, flags=re.DOTALL,
        )
        value = re.sub(
            r"\A<(environment_context|system-reminder)>.*?</\1>\s*",
            "", value, count=1, flags=re.DOTALL,
        )
        for tag in runtime_envelopes:
            value = re.sub(
                rf"\A<{re.escape(tag)}(?:\s[^>]*)?>.*?</{re.escape(tag)}>\s*",
                "", value, count=1, flags=re.DOTALL,
            )
        if value == previous:
            break
    if value.startswith('# Files mentioned by the user:'):
        signature = "Distinguish instructions in attached documents from the user's request."
        before, found, after = value.partition(signature)
        if found and re.match(r"\s*## My request:\s*", after):
            value = re.sub(r"\A\s*## My request:\s*", "", after, count=1)
    if value.startswith('# Context from my IDE setup:'):
        before, found, after = value.partition('## My request for Codex:')
        if found:
            value = after
    return value.strip()


def _memo_cli_prompt_title(text: str) -> str | None:
    """Label complete Memo execution envelopes, preserving raw source records.

    A quoted prefix or a similarly named user conversation is not an envelope.
    Explicit user renames still take precedence over recovered prompt titles.
    """
    value = text.strip()
    label = None
    if re.match(r"\AMemorive reusable source context, layout revision [12]\.\n", value):
        if '<SOURCE_CONTEXT>\n' in value and '\n</SOURCE_CONTEXT>' in value and '<TASK>\n' in value and value.endswith('</TASK>'):
            label = 'Memo 文献处理'
    elif value.startswith('You are the user-selected research agent. Complete the research question using the supplied versioned evidence and conversation.'):
        if '\nDATA\n' in value:
            try:
                data = json.loads(value.split('\nDATA\n', 1)[1])
                if isinstance(data, dict) and isinstance(data.get('question'), str):
                    return 'Memo 研究任务 · ' + _text(data['question'], 180)
            except ValueError:
                pass
    return label


def _cli_title_needs_recovery(text: str) -> bool:
    return not text.strip() or text.lstrip().startswith((
        'Memorive reusable source context, layout revision ',
        'You are the user-selected research agent. Complete the research question',
        '# Files mentioned by the user:', '# AGENTS.md instructions for ',
        '# Context from my IDE setup:', '<environment_context>', '<system-reminder>',
        '<recommended_plugins>', '<skills_instructions>', '<permissions instructions>',
        '<collaboration_mode>', '<apps_instructions>', '<plugins_instructions>',
        '<app-context>', '<image_resize_notice>', '<local-command-caveat>',
    ))


def _iso_timestamp(value: Any, fallback_ns: int = 0) -> str:
    if isinstance(value, str) and value.strip():
        candidate = value.strip()
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        except ValueError:
            pass
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            pass
    if fallback_ns > 0:
        return datetime.fromtimestamp(fallback_ns / 1_000_000_000, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return "UNKNOWN"


def _projection_id(provider_id: str, provider_session_id: str) -> str:
    digest = hashlib.sha256(f"{provider_id}\x00{provider_session_id}".encode("utf-8")).hexdigest()[:24]
    return f"{provider_id}-{digest}"


_SHORT_CONVERSATIONAL_REPLIES = frozenset(
    {
        "ok",
        "okay",
        "yes",
        "no",
        "continue",
        "thanks",
        "好",
        "好的",
        "可以",
        "继续",
        "是",
        "不是",
        "对",
        "不对",
        "谢谢",
    }
)


def _legacy_inline_fragment(
    text: str,
    previous_assistant: str,
    next_assistant: str,
) -> bool:
    """Conservatively identify a rich-text child mislabelled as a user turn.

    Chromium UIA can expose headings, counters and bold spans as independent
    Text controls.  Geometry alone then makes a right-indented child look like
    a user bubble.  This repair is intentionally limited to a short row between
    two assistant rows; normal short conversational replies remain intact.
    """

    rendered = text.strip()
    folded = rendered.casefold()
    if (
        not rendered
        or len(rendered) > 64
        or len(rendered.splitlines()) > 3
        or folded in _SHORT_CONVERSATIONAL_REPLIES
        or rendered.endswith(("?", "？", "!", "！", "。"))
    ):
        return False
    structural = bool(
        rendered.isupper()
        or re.match(r"^(?:[#>*•]|[-–—]\s|\d+(?:[.)、:]|\s*(?:个|项|条|页|字|KB|MB|GB|毫升|克)))", rendered)
        or any(marker in rendered for marker in ("：", ":", "`", "→", "|"))
    )
    surrounded = (
        len(rendered) <= 32
        and len(previous_assistant.strip()) >= len(rendered) * 2
        and len(next_assistant.strip()) >= len(rendered) * 2
    )
    return structural or surrounded


def _normalize_web_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Fold nested rich-text fragments and coalesce adjacent equal-role rows."""

    repaired: list[dict[str, str]] = []
    index = 0
    while index < len(messages):
        current = messages[index]
        if (
            current["role"] == "user"
            and repaired
            and repaired[-1]["role"] == "assistant"
            and index + 1 < len(messages)
            and messages[index + 1]["role"] == "assistant"
            and _legacy_inline_fragment(
                current["text"],
                repaired[-1]["text"],
                messages[index + 1]["text"],
            )
        ):
            following = messages[index + 1]
            combined = "\n\n".join(
                value.strip()
                for value in (
                    repaired[-1]["text"],
                    current["text"],
                    following["text"],
                )
                if value.strip()
            )
            repaired[-1] = {
                "message_id": hashlib.sha256(
                    f"assistant\x00{combined}".encode("utf-8")
                ).hexdigest()[:32],
                "role": "assistant",
                "text": combined,
            }
            index += 2
            continue
        if repaired and repaired[-1]["role"] == current["role"]:
            combined = "\n\n".join(
                value.strip()
                for value in (repaired[-1]["text"], current["text"])
                if value.strip()
            )
            repaired[-1] = {
                "message_id": hashlib.sha256(
                    f"{current['role']}\x00{combined}".encode("utf-8")
                ).hexdigest()[:32],
                "role": current["role"],
                "text": combined,
            }
        else:
            repaired.append(dict(current))
        index += 1
    return repaired


class SessionsProductController:
    adapter_kind = "analysis_sessions_local_cli_read_only_metadata_first"
    discovery_window_days = 30

    def __init__(
        self,
        service: Any,
        control_store: Any,
        contract_root: Path,
        fixture_root: Path,
        state_root: Path,
        *,
        source_roots: Mapping[str, Path] | None = None,
        visible_ui_reader: Any | None = None,
        legacy_deepseek_visible_ui_projection: bool = True,
        local_model_registry: Any | None = None,
        conversation_refinement_channel: Any | None = None,
        refinement_task_manager: Any | None = None,
        refinement_lifecycle_observer: Any | None = None,
        refinement_queue_observer: Any | None = None,
        refinement_result_observer: Any | None = None,
    ):
        self.service = service
        self.control_store = control_store
        self.contract_root = contract_root.resolve()
        self.fixture_root = fixture_root.resolve()
        self.state_root = state_root.resolve()
        self.state_root.mkdir(parents=True, exist_ok=True)
        # Preserve the v1 synthetic projection for audit/replay.  The live UI
        # owns a separate metadata-only index and never migrates sample rows.
        self.state_path = self.state_root / "session_projection_v2.json"
        # pywebview may create more than one controller for the same profile
        # and dispatch bootstrap, refresh and filter changes from separate
        # threads.  Every controller targeting this projection therefore
        # shares one load/mutate/save critical section.  RLock is required
        # because bulk operations deliberately re-enter ``call``.
        self._state_lock = _projection_state_lock(self.state_path)
        self._source_roots = self._resolve_source_roots(source_roots)
        self._source_root_selection = self._source_root_selection_metadata(source_roots)
        self._last_codex_internal_exclusions = 0
        if isinstance(visible_ui_reader, Mapping):
            unknown = set(visible_ui_reader) - set(_VISIBLE_UI_PROFILES)
            if unknown:
                raise SessionProductError("VISIBLE_UI_READER_PROVIDER_INVALID")
            self._visible_ui_readers = dict(visible_ui_reader)
        elif visible_ui_reader is None:
            self._visible_ui_readers = {}
        else:
            # Backward-compatible visible-chat/chat-archive injection surface.
            self._visible_ui_readers = {"deepseek": visible_ui_reader}
        self._visible_ui_reader = self._visible_ui_readers.get("deepseek")
        self._legacy_deepseek_visible_ui_projection = bool(
            legacy_deepseek_visible_ui_projection
        )
        self._local_model_registry = local_model_registry
        self._conversation_refinement_channel = conversation_refinement_channel
        self._refinement_task_manager = refinement_task_manager
        if refinement_lifecycle_observer is not None and not callable(
            refinement_lifecycle_observer
        ):
            raise SessionProductError("SESSION_REFINEMENT_OBSERVER_INVALID")
        self._refinement_lifecycle_observer = refinement_lifecycle_observer
        if refinement_queue_observer is not None and not callable(
            refinement_queue_observer
        ):
            raise SessionProductError("SESSION_REFINEMENT_QUEUE_OBSERVER_INVALID")
        self._refinement_queue_observer = refinement_queue_observer
        if refinement_result_observer is not None and not callable(
            refinement_result_observer
        ):
            raise SessionProductError("SESSION_REFINEMENT_RESULT_OBSERVER_INVALID")
        self._refinement_result_observer = refinement_result_observer
        self._refinement_lock = threading.RLock()
        self._refinement_workers: dict[str, threading.Thread] = {}
        self._refinement_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._refinement_queue_items: set[str] = set()
        self._refinement_queue_worker: threading.Thread | None = None
        self._visible_ui_root = self.state_root / "visible_ui_private"
        self._visible_ui_root.mkdir(parents=True, exist_ok=True)
        self._browser_session_root = self.state_root / "browser_sessions_private"
        self._browser_session_root.mkdir(parents=True, exist_ok=True)
        self._session_archive = SessionArchive(
            self.state_root / "session_archive_v1.sqlite3",
            self.state_root / "session_source_snapshots_private",
        )
        self._refinement_root = self.state_root / "refinement_private"
        self._refinement_root.mkdir(parents=True, exist_ok=True)
        self._refinement_settings_path = self.state_root / "refinement_settings_v1.json"
        if not self._refinement_settings_path.exists():
            self._write_private_json(
                self._refinement_settings_path,
                {
                    "schema_version": "DesktopSessionRefinementSettings-v1",
                    "profile_ref": "",
                    "include_tasks": True,
                    "include_risks": True,
                },
            )
        self._metrics = {
            "synthetic_fixture_reads": 0,
            "local_projection_writes": 0,
            "external_network_calls": 0,
            "external_model_calls": 0,
            "provider_calls": 0,
            "credential_value_reads": 0,
            "cookie_value_reads": 0,
            "browser_storage_value_reads": 0,
            "cli_session_reads": 0,
            "ui_session_reads": 0,
            "real_session_content_captured": 0,
            "remote_session_mutations": 0,
            "account_mutations": 0,
            "permanent_deletes": 0,
            "raw_private_external_writes": 0,
            "metadata_refreshes": 0,
            "browser_bundle_imports": 0,
            "refinement_drafts_created": 0,
            "refinement_tasks_created": 0,
            "refinement_model_calls": 0,
            "refinement_tasks_failed": 0,
            "refinement_lifecycle_projection_failures": 0,
            "refinement_queue_projection_failures": 0,
        }
        if not self.state_path.exists():
            self._save(
                {
                    "schema_version": "SessionsSessionProjectionState-v3",
                    "revision": 0,
                    "sessions": {},
                    "capture_jobs": {},
                    "fault_history": [],
                    "last_filter": {
                        "source": "all",
                        "search": "",
                        "limit_per_source": 30,
                    },
                    "source_status": {},
                    "hidden_projection_ids": [],
                }
            )
        else:
            raw_state = json.loads(self.state_path.read_text(encoding="utf-8"))
            migrated_state = self._migrate_state(raw_state)
            if migrated_state != raw_state:
                self._save(migrated_state)
        self._validate_state(self._load())

    def _inbox_refinement_call(
        self, method: str, params: Mapping[str, Any]
    ) -> Any:
        return self.service.call(method, dict(params))

    def _find_inbox_refinement(
        self, projection_id: str
    ) -> dict[str, Any] | None:
        try:
            value = self._inbox_refinement_call(
                "inbox.find_session_refinement",
                {"local_projection_id": projection_id},
            )
        except (AssertionError, AttributeError):
            # Isolated legacy unit doubles predate the durable Inbox queue.
            return None
        return dict(value) if isinstance(value, Mapping) else None

    def _notify_refinement_lifecycle(self, job_id: str) -> None:
        observer = self._refinement_lifecycle_observer
        manager = self._refinement_task_manager
        if observer is None or manager is None:
            return
        try:
            task = manager.get_session_refinement_task(job_id)
            observer(
                {
                    "job_id": task["job_id"],
                    "attempt_id": task["attempt_id"],
                    "local_projection_id": task["local_projection_id"],
                    "display_name": task["display_name"],
                    "control_state": task["control_state"],
                    "error_code": task.get("error_code"),
                    "updated_at": task.get("updated_at"),
                }
            )
        except Exception:
            # Product notifications are projections.  They must never change
            # the execution result of the durable refinement task.
            self._metrics["refinement_lifecycle_projection_failures"] += 1
            return

    def _notify_refinement_queue_projection(self, item_id: str) -> None:
        observer = self._refinement_queue_observer
        if observer is None:
            return
        try:
            item = self._inbox_refinement_call(
                "inbox.item_detail", {"item_id": item_id}
            )
            if not isinstance(item, Mapping):
                raise SessionProductError("SESSION_REFINEMENT_QUEUE_ITEM_INVALID")
            observer(
                {
                    "item_id": item["item_id"],
                    "job_id": item.get("job_id"),
                    "local_projection_id": item.get("local_projection_id"),
                    "state": item.get("state"),
                    "updated_at": item.get("updated_at"),
                }
            )
        except Exception:
            # Queue state is already durable.  A failed UI projection must not
            # rewrite the queue or alter the model execution result.
            self._metrics["refinement_queue_projection_failures"] += 1
            return

    def _schedule_refinement_queue_item(self, payload: Mapping[str, Any]) -> None:
        if not self._refinement_auto_run_enabled():
            return
        item_id = str(payload["item_id"])
        with self._refinement_lock:
            if item_id in self._refinement_queue_items:
                return
            self._refinement_queue_items.add(item_id)
            self._refinement_queue.put(deepcopy(dict(payload)))
            worker = self._refinement_queue_worker
            if worker is not None and worker.is_alive():
                return
            worker = threading.Thread(
                target=self._drain_refinement_queue,
                name="memorive-session-refinement-inbox-queue",
                daemon=True,
            )
            self._refinement_queue_worker = worker
            worker.start()

    def _ensure_refinement_queue_task(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        # Enqueue and the background queue synchronizer can meet here. They
        # must reuse one durable task rather than race to create two workers.
        with self._refinement_lock:
            existing = self._refinement_task_for_projection(str(payload["local_projection_id"]))
            if existing is not None and existing.get("control_state") in {"QUEUED", "RUNNING"}:
                if str(existing["source_snapshot_sha256"]).upper() != str(payload["source_snapshot_sha256"]).upper():
                    raise SessionProductError("SESSION_REFINEMENT_SOURCE_SNAPSHOT_CHANGED")
                return existing
            task = self._refinement_task_manager.create_session_refinement_task(
                display_name=str(payload["display_name"]),
                local_projection_id=str(payload["local_projection_id"]),
                source_snapshot_sha256=str(payload["source_snapshot_sha256"]),
                selected_profile_ref=str(payload["selected_profile_ref"]),
                message_count=int(payload["message_count"]),
                language_context=payload.get("language_context"),
            )
            self._metrics["refinement_tasks_created"] += 1
            return task

    def _discard_pending_refinement_queue(self) -> int:
        """Forget queued work that has not been claimed by the worker yet."""

        discarded = 0
        while True:
            try:
                payload = self._refinement_queue.get_nowait()
            except queue.Empty:
                return discarded
            try:
                item_id = str(payload.get("item_id", ""))
                with self._refinement_lock:
                    self._refinement_queue_items.discard(item_id)
                discarded += 1
            finally:
                self._refinement_queue.task_done()

    def _drain_refinement_queue(self) -> None:
        while True:
            try:
                payload = self._refinement_queue.get_nowait()
            except queue.Empty:
                with self._refinement_lock:
                    self._refinement_queue_worker = None
                return
            item_id = str(payload["item_id"])
            try:
                if not self._refinement_auto_run_enabled():
                    self._discard_pending_refinement_queue()
                    with self._refinement_lock:
                        self._refinement_queue_worker = None
                    return
                item = self._inbox_refinement_call(
                    "inbox.item_detail", {"item_id": item_id}
                )
                if (
                    not isinstance(item, Mapping)
                    or item.get("item_kind") != "SESSION_REFINEMENT"
                    or item.get("state") != "QUEUED"
                ):
                    continue
                projection_id = str(payload["local_projection_id"])
                task = self._ensure_refinement_queue_task(payload)
                job_id = str(task["job_id"])
                self._notify_refinement_queue_projection(item_id)
                if task.get("control_state") == "QUEUED":
                    self._start_refinement_task(job_id)
                while True:
                    current = self._refinement_task_manager.get_session_refinement_task(
                        job_id
                    )
                    if current.get("control_state") in {
                        "SUCCEEDED",
                        "FAILED",
                        "CANCELLED",
                    }:
                        break
                    time.sleep(0.05)
                succeeded = current.get("control_state") == "SUCCEEDED"
                error_code = str(
                    current.get("error_code")
                    or (
                        "SESSION_REFINEMENT_CANCELLED"
                        if current.get("control_state") == "CANCELLED"
                        else ""
                    )
                )
                self._inbox_refinement_call(
                    "inbox.complete_session_refinement",
                    {
                        "item_id": item_id,
                        "succeeded": succeeded,
                        "error_code": error_code,
                    },
                )
                self._notify_refinement_queue_projection(item_id)
            except Exception as error:
                error_code = safe_error_code(
                    str(getattr(error, "code", None) or error)
                )
                try:
                    self._inbox_refinement_call(
                        "inbox.complete_session_refinement",
                        {
                            "item_id": item_id,
                            "succeeded": False,
                            "error_code": error_code,
                        },
                    )
                    self._notify_refinement_queue_projection(item_id)
                except Exception:
                    pass
            finally:
                with self._refinement_lock:
                    self._refinement_queue_items.discard(item_id)
                self._refinement_queue.task_done()

    def _refinement_auto_run_enabled(self) -> bool:
        setting = self._inbox_refinement_call("inbox.get_auto_run", {})
        return isinstance(setting, Mapping) and setting.get("enabled") is True

    def sync_refinement_queue(self) -> dict[str, Any]:
        """Schedule durable Inbox refinement intents only while auto-run is on."""

        if not self._refinement_auto_run_enabled():
            discarded = self._discard_pending_refinement_queue()
            return {
                "schema_version": "DesktopSessionRefinementQueueSync-v1",
                "auto_run": False,
                "scheduled_count": 0,
                "discarded_count": discarded,
                "status": "PAUSED",
            }
        projection = self._inbox_refinement_call("inbox.projection", {})
        cards = projection.get("cards", []) if isinstance(projection, Mapping) else []
        scheduled = 0
        for card in cards:
            if (
                not isinstance(card, Mapping)
                or card.get("item_kind") != "SESSION_REFINEMENT"
                or card.get("state") != "QUEUED"
            ):
                continue
            detail = self._inbox_refinement_call(
                "inbox.item_detail", {"item_id": card.get("item_id")}
            )
            if not isinstance(detail, Mapping):
                continue
            payload = {
                "item_id": detail["item_id"],
                "local_projection_id": detail["local_projection_id"],
                "display_name": detail["name"],
                "language_context": detail.get("language_context"),
                "source_snapshot_sha256": detail["source_snapshot_sha256"],
                "selected_profile_ref": detail["selected_profile_ref"],
                "message_count": detail["message_count"],
            }
            with self._refinement_lock:
                already_scheduled = str(detail["item_id"]) in self._refinement_queue_items
            self._schedule_refinement_queue_item(payload)
            if not already_scheduled:
                scheduled += 1
        return {
            "schema_version": "DesktopSessionRefinementQueueSync-v1",
            "auto_run": True,
            "scheduled_count": scheduled,
            "status": "PASS",
        }

    @staticmethod
    def _resolve_source_roots(source_roots: Mapping[str, Path] | None) -> dict[str, Path]:
        if source_roots is not None:
            if set(source_roots) != {"codex_home", "claude_home"}:
                raise SessionProductError("SESSION_SOURCE_ROOT_KEYS_INVALID")
            return {key: Path(value).expanduser().resolve() for key, value in source_roots.items()}
        home = Path.home()
        codex_value = os.environ.get("MEMORIVE_CODEX_HOME") or os.environ.get("CODEX_HOME")
        claude_value = os.environ.get("MEMORIVE_CLAUDE_HOME") or os.environ.get("CLAUDE_CONFIG_DIR")
        return {
            "codex_home": Path(codex_value).expanduser().resolve() if codex_value else (home / ".codex").resolve(),
            "claude_home": Path(claude_value).expanduser().resolve() if claude_value else (home / ".claude").resolve(),
        }

    @staticmethod
    def _source_root_selection_metadata(source_roots: Mapping[str, Path] | None) -> dict[str, str]:
        if source_roots is not None:
            return {"codex": "EXPLICIT_ISOLATED_SOURCE", "claude": "EXPLICIT_ISOLATED_SOURCE"}
        return {
            "codex": (
                "MEMORIVE_EXPLICIT_HOME" if os.environ.get("MEMORIVE_CODEX_HOME")
                else "CODEX_HOME" if os.environ.get("CODEX_HOME")
                else "USERPROFILE_DEFAULT"
            ),
            "claude": (
                "MEMORIVE_EXPLICIT_HOME" if os.environ.get("MEMORIVE_CLAUDE_HOME")
                else "CLAUDE_CONFIG_DIR" if os.environ.get("CLAUDE_CONFIG_DIR")
                else "USERPROFILE_DEFAULT"
            ),
        }

    @staticmethod
    def _public_source_root(path: Path) -> str:
        resolved = path.resolve(strict=False)
        user_home = Path.home().resolve(strict=False)
        try:
            relative = resolved.relative_to(user_home)
        except ValueError:
            return f"<LOCAL>/{resolved.name}"
        suffix = relative.as_posix()
        return "%USERPROFILE%" + ("/" + suffix if suffix else "")

    @staticmethod
    def _require_keys(params: Mapping[str, Any], required: set[str], optional: set[str] | None = None) -> None:
        optional = optional or set()
        keys = set(params)
        if not required <= keys or keys - required - optional:
            raise SessionProductError("SESSIONS_PRODUCT_PARAMS_INVALID")

    @staticmethod
    def _migrate_state(state: Mapping[str, Any]) -> dict[str, Any]:
        value = deepcopy(dict(state))
        if value.get("schema_version") == "SessionsSessionProjectionState-v2":
            value["schema_version"] = "SessionsSessionProjectionState-v3"
            value["hidden_projection_ids"] = []
            previous_filter = value.get("last_filter")
            if not isinstance(previous_filter, Mapping):
                previous_filter = {"source": "all", "search": ""}
            value["last_filter"] = {
                "source": str(previous_filter.get("source", "all")),
                "search": str(previous_filter.get("search", "")),
                "limit_per_source": 30,
            }
        if value.get("schema_version") == "SessionsSessionProjectionState-v3":
            current_filter = value.get("last_filter")
            if isinstance(current_filter, Mapping) and current_filter.get("limit_per_source") == 20:
                value["last_filter"] = {
                    "source": str(current_filter.get("source", "all")),
                    "search": str(current_filter.get("search", "")),
                    "limit_per_source": 30,
                }
        return value

    @staticmethod
    def _validate_state(state: Mapping[str, Any]) -> None:
        expected = {
            "schema_version",
            "revision",
            "sessions",
            "capture_jobs",
            "fault_history",
            "last_filter",
            "source_status",
            "hidden_projection_ids",
        }
        if set(state) != expected:
            raise SessionProductError("SESSION_STATE_FIELDS_INVALID")
        if state.get("schema_version") != "SessionsSessionProjectionState-v3":
            raise SessionProductError("SESSION_STATE_SCHEMA_INVALID")
        if not isinstance(state.get("revision"), int) or state["revision"] < 0:
            raise SessionProductError("SESSION_STATE_REVISION_INVALID")
        if not isinstance(state.get("sessions"), dict) or not isinstance(state.get("capture_jobs"), dict):
            raise SessionProductError("SESSION_STATE_COLLECTION_INVALID")
        if not isinstance(state.get("source_status"), dict):
            raise SessionProductError("SESSION_SOURCE_STATUS_INVALID")
        hidden = state.get("hidden_projection_ids")
        if (
            not isinstance(hidden, list)
            or not all(isinstance(value, str) and value for value in hidden)
            or len(hidden) != len(set(hidden))
        ):
            raise SessionProductError("SESSION_HIDDEN_PROJECTION_IDS_INVALID")
        last_filter = state.get("last_filter")
        if not isinstance(last_filter, Mapping) or set(last_filter) != {
            "source",
            "search",
            "limit_per_source",
        }:
            raise SessionProductError("SESSION_LAST_FILTER_INVALID")
        if last_filter.get("limit_per_source") not in {10, 20, 30, 50}:
            raise SessionProductError("SESSION_LIMIT_PER_SOURCE_INVALID")

    def _load(self) -> dict[str, Any]:
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self._validate_state(state)
        return state

    def _save(self, state: dict[str, Any]) -> None:
        state["revision"] = int(state.get("revision", -1)) + 1
        self._validate_state(state)
        temporary = self.state_path.with_name(
            f".{self.state_path.name}.{uuid.uuid4().hex}.next"
        )
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n"
                )
            temporary.replace(self.state_path)
        finally:
            # Only the unique temporary created by this call may be removed.
            # A legacy fixed-name .next file is left untouched and cannot
            # block the durable state projection anymore.
            temporary.unlink(missing_ok=True)
        self._metrics["local_projection_writes"] += 1

    @staticmethod
    def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.next")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n"
                )
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _load_refinement_settings(self) -> dict[str, Any]:
        try:
            value = json.loads(self._refinement_settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SessionProductError("SESSION_REFINEMENT_SETTINGS_UNAVAILABLE") from error
        expected = {"schema_version", "profile_ref", "include_tasks", "include_risks"}
        if not isinstance(value, Mapping) or set(value) != expected:
            raise SessionProductError("SESSION_REFINEMENT_SETTINGS_FIELDS_INVALID")
        if value.get("schema_version") != "DesktopSessionRefinementSettings-v1":
            raise SessionProductError("SESSION_REFINEMENT_SETTINGS_SCHEMA_INVALID")
        if not isinstance(value.get("profile_ref"), str):
            raise SessionProductError("SESSION_REFINEMENT_PROFILE_INVALID")
        if not isinstance(value.get("include_tasks"), bool) or not isinstance(value.get("include_risks"), bool):
            raise SessionProductError("SESSION_REFINEMENT_OPTIONS_INVALID")
        return dict(value)

    @staticmethod
    def _public(row: Mapping[str, Any]) -> dict[str, Any]:
        result = {key: deepcopy(row[key]) for key in _PUBLIC_FIELDS if key in row}
        result.setdefault("deleted", False)
        result["raw_content_included"] = False
        result["private_content_included"] = False
        result["public_safe_projection"] = True
        return result

    @staticmethod
    def _path_within(path: Path, roots: Iterable[Path]) -> bool:
        resolved = path.resolve()
        for root in roots:
            try:
                resolved.relative_to(root.resolve())
                return True
            except ValueError:
                continue
        return False

    def _allowed_record_roots(self, provider_id: str) -> tuple[Path, ...]:
        if provider_id == "codex":
            home = self._source_roots["codex_home"]
            return ((home / "sessions").resolve(), (home / "archived_sessions").resolve())
        if provider_id == "claude":
            return ((self._source_roots["claude_home"] / "projects").resolve(),)
        return ()

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
            "d36_queries": ["list_sessions", "get_session", "get_provider_capabilities"],
            "d36_actions": ["delete_session", "refine_session"],
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

    def _configured_refinement_models(self) -> list[dict[str, Any]]:
        from memorive_settings.refinement_models import configured_refinement_models
        try:
            projection = self.service.call("settings.get_state", {})
        except Exception:
            projection = {}
        return configured_refinement_models(projection, self._local_model_registry)

    @staticmethod
    def _legacy_local_profile_ref(option: Mapping[str, Any]) -> str:
        endpoint_id = _text(option.get("endpoint_id"), 120)
        model_name = _text(option.get("model_name"), 160)
        if not endpoint_id or not model_name:
            return ""
        model_suffix = hashlib.sha256(model_name.encode("utf-8")).hexdigest()[:20]
        return f"local-model:{endpoint_id}:{model_suffix}"

    @classmethod
    def _selected_refinement_option(
        cls,
        profile_ref: Any,
        model_options: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any] | None:
        accepted_ref = _text(profile_ref, 192)
        for row in model_options:
            if row.get("profile_ref") == accepted_ref:
                return dict(row)
        if not re.fullmatch(
            r"local-model:local-model-[0-9a-f]{20}:[0-9a-f]{20}",
            accepted_ref,
            re.IGNORECASE,
        ):
            return None
        matches = [
            dict(row)
            for row in model_options
            if row.get("profile_kind") == "LOCAL"
            and cls._legacy_local_profile_ref(row).casefold() == accepted_ref.casefold()
        ]
        return matches[0] if len(matches) == 1 else None

    def _prepare_refinement_settings_for_start(
        self,
        settings: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        accepted_settings = deepcopy(dict(settings))
        model_options = self._configured_refinement_models()
        selected = self._selected_refinement_option(
            accepted_settings.get("profile_ref"), model_options
        )
        if selected is None:
            raise SessionProductError("SESSION_REFINEMENT_PROFILE_NOT_CONFIGURED")
        if selected.get("execution_eligible") is not True:
            endpoint_id = _text(selected.get("endpoint_id"), 120)
            model_name = _text(selected.get("model_name"), 160)
            if (
                selected.get("profile_kind") != "LOCAL"
                or selected.get("identity_refresh_supported") is not True
                or self._local_model_registry is None
                or not endpoint_id
                or not model_name
            ):
                raise SessionProductError(
                    "SESSION_REFINEMENT_PROFILE_EXECUTION_UNAVAILABLE"
                )
            verification = self._local_model_registry.call(
                "local_models.verify", {"endpoint_id": endpoint_id}
            )
            if (
                not isinstance(verification, Mapping)
                or verification.get("service_reachable") is not True
                or model_name not in verification.get("models", [])
                or verification.get("inference_run") is not False
            ):
                raise SessionProductError("SESSION_REFINEMENT_LOCAL_IDENTITY_REFRESH_FAILED")
            model_options = self._configured_refinement_models()
            selected = next(
                (
                    dict(row)
                    for row in model_options
                    if row.get("profile_ref") == selected.get("profile_ref")
                ),
                None,
            )
            if selected is None or selected.get("execution_eligible") is not True:
                raise SessionProductError("SESSION_REFINEMENT_LOCAL_IDENTITY_REFRESH_FAILED")
        canonical_ref = _text(selected.get("profile_ref"), 192)
        if accepted_settings.get("profile_ref") != canonical_ref:
            accepted_settings["profile_ref"] = canonical_ref
            self._write_private_json(self._refinement_settings_path, accepted_settings)
            self._metrics["local_projection_writes"] += 1
        return accepted_settings, selected

    def _refinement_settings_projection(self) -> dict[str, Any]:
        settings = self._load_refinement_settings()
        configured = bool(settings.get("profile_ref"))
        model_options = self._configured_refinement_models()
        selected_option = self._selected_refinement_option(
            settings.get("profile_ref"), model_options
        )
        selected_execution_eligible = bool(
            selected_option is not None
            and selected_option.get("execution_eligible") is True
        )
        execute = getattr(
            self._conversation_refinement_channel,
            "execute",
            None,
        )
        selected_start_eligible = bool(
            selected_execution_eligible
            or selected_option is not None
            and selected_option.get("profile_kind") == "LOCAL"
            and selected_option.get("identity_refresh_supported") is True
        )
        execution_ready = bool(
            callable(execute)
            and self._refinement_task_manager is not None
            and selected_start_eligible
        )
        return {
            "schema_version": "DesktopSessionRefinementSettingsProjection-v1",
            "settings": settings,
            "model_options": model_options,
            "configured": configured,
            "execution_mode": (
                "EXISTING_PROFILE_STRUCTURED_CHAT_EXECUTION"
                if configured and execution_ready
                else "REFINEMENT_PROFILE_EXECUTION_UNAVAILABLE"
                if configured
                and selected_option is not None
                and not selected_execution_eligible
                else "CONVERSATION_REFINEMENT_CHANNEL_NOT_AVAILABLE"
                if configured and selected_option is not None
                else "REFINEMENT_PROFILE_NOT_CONFIGURED"
                if configured
                else "MODEL_NOT_CONFIGURED"
            ),
            "selected_profile_kind": (
                selected_option.get("profile_kind")
                if selected_option is not None
                else None
            ),
            "selected_profile_execution_eligible": selected_execution_eligible,
            "selected_profile_start_eligible": selected_start_eligible,
            "selected_profile_execution_status": (
                "LOCAL_IDENTITY_REFRESH_REQUIRED"
                if selected_option is not None
                and selected_option.get("profile_kind") == "LOCAL"
                and not selected_execution_eligible
                and selected_start_eligible
                else selected_option.get("execution_status")
                if selected_option is not None
                else "PROFILE_NOT_CONFIGURED"
                if configured
                else "MODEL_NOT_CONFIGURED"
            ),
            "execution_ready": execution_ready,
            "external_model_calls": 0,
            "review_required": True,
            "auto_ingest": False,
            "status": "PASS",
        }

    @staticmethod
    def _reject_sensitive_channel_result(value: Any, path: str = "result") -> None:
        forbidden = re.compile(
            r"^(api[_-]?key|authorization|cookie[_-]?value|credential[_-]?value|password|secret[_-]?value)$",
            re.IGNORECASE,
        )
        if isinstance(value, Mapping):
            for key, child in value.items():
                if forbidden.fullmatch(str(key)):
                    raise SessionProductError("SESSION_REFINEMENT_RESULT_SENSITIVE_FIELD_FORBIDDEN")
                SessionsProductController._reject_sensitive_channel_result(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                SessionsProductController._reject_sensitive_channel_result(child, f"{path}[{index}]")

    def _validate_refinement_channel_result(
        self,
        value: Any,
        *,
        task_id: str,
        profile_ref: str,
    ) -> dict[str, Any]:
        expected = {
            "profile_ref",
            "requested_model",
            "returned_model",
            "subject_output",
            "model_calls",
            "external_model_calls",
            "external_network_calls",
            "provider_calls",
            "execution_receipt",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise SessionProductError("SESSION_REFINEMENT_CHANNEL_RESULT_FIELDS_INVALID")
        accepted = deepcopy(dict(value))
        if (
            accepted.get("profile_ref") != profile_ref
        ):
            raise SessionProductError("SESSION_REFINEMENT_CHANNEL_RESULT_IDENTITY_INVALID")
        if not _text(accepted.get("requested_model"), 240) or not _text(accepted.get("returned_model"), 240):
            raise SessionProductError("SESSION_REFINEMENT_CHANNEL_MODEL_IDENTITY_MISSING")
        if accepted.get("model_calls") != 1:
            raise SessionProductError("SESSION_REFINEMENT_CHANNEL_CALL_COUNT_INVALID")
        for field in ("external_model_calls", "external_network_calls", "provider_calls"):
            count = accepted.get(field)
            if isinstance(count, bool) or count not in {0, 1}:
                raise SessionProductError("SESSION_REFINEMENT_CHANNEL_EFFECT_COUNT_INVALID")
        subject_output = accepted.get("subject_output")
        if (
            not isinstance(subject_output, Mapping)
            or set(subject_output) != {"schema_version", "pack_id", "graph_hash", "items"}
            or subject_output.get("schema_version") != "CONVERSATION_REFINEMENT_SUBJECT_OUTPUT_V1"
            or subject_output.get("pack_id") != task_id
            or not isinstance(subject_output.get("graph_hash"), Mapping)
            or not isinstance(subject_output.get("items"), list)
            or not subject_output["items"]
        ):
            raise SessionProductError("SESSION_REFINEMENT_Capabilities_SUBJECT_OUTPUT_INVALID")
        try:
            subject_output["items"] = [
                validate_typed_refinement_item(item)
                for item in subject_output["items"]
            ]
        except Exception as error:
            raise SessionProductError(
                "SESSION_REFINEMENT_Capabilities_TYPED_ITEM_INVALID"
            ) from error
        if not isinstance(accepted.get("execution_receipt"), Mapping) or not accepted["execution_receipt"]:
            raise SessionProductError("SESSION_REFINEMENT_CHANNEL_RECEIPT_REQUIRED")
        self._reject_sensitive_channel_result(accepted)
        try:
            _sha256_json(accepted)
        except (TypeError, ValueError) as error:
            raise SessionProductError("SESSION_REFINEMENT_CHANNEL_RESULT_JSON_INVALID") from error
        return accepted

    def _refinement_task_for_projection(self, projection_id: str) -> dict[str, Any] | None:
        manager = self._refinement_task_manager
        finder = getattr(manager, "find_session_refinement_task", None)
        if not callable(finder):
            return None
        result = finder(projection_id)
        return dict(result) if isinstance(result, Mapping) else None

    @staticmethod
    def _refinement_source_content_allowed(
        row: Mapping[str, Any],
        messages: list[dict[str, Any]],
        completeness: Mapping[str, Any],
    ) -> bool:
        if not messages:
            return False
        status = str(completeness.get("status") or "")
        if status == "COMPLETE":
            return True
        lineage = row.get("lineage")
        return (
            status == "PARTIAL"
            and row.get("provider_id") == _UNIVERSAL_PROVIDER_ID
            and row.get("source_format") == "browser_current_page_v1"
            and isinstance(lineage, Mapping)
            and lineage.get("source_kind") == BROWSER_CAPTURE_SOURCE_ACTIVE_TAB_V1
        )

    @staticmethod
    def _refinement_channel_messages(
        messages: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, str]]:
        """Project rich archive rows onto the exact Capabilities channel contract."""

        projected: list[dict[str, str]] = []
        for message in messages:
            if not isinstance(message, Mapping):
                raise SessionProductError("SESSION_REFINEMENT_MESSAGE_INVALID")
            role = str(message.get("role") or "").strip().lower()
            content = message.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str):
                raise SessionProductError("SESSION_REFINEMENT_MESSAGE_INVALID")
            if not content.strip():
                continue
            projected.append({"role": role, "content": content})
        if not projected:
            raise SessionProductError("SESSION_REFINEMENT_REQUIRES_COMPLETE_CONTENT")
        return projected

    def _run_refinement_task(
        self,
        *,
        job_id: str,
        projection_id: str,
        snapshot_sha256: str,
        messages: list[dict[str, str]],
        source_binding: Mapping[str, Any],
        settings: Mapping[str, Any],
    ) -> None:
        manager = self._refinement_task_manager
        model_calls = 0
        from memorive_settings.task_scheduling import TASK_CATEGORIES
        gate = TASK_CATEGORIES.enter('REFINEMENT', checkpoint=lambda: self._refinement_wait_checkpoint(job_id))
        admitted = False
        try:
            gate.__enter__(); admitted = True
            item = self._find_inbox_refinement(projection_id)
            if item:
                self._inbox_refinement_call('inbox.begin_session_refinement', {'item_id': item['item_id'], 'job_id': job_id})
            manager.begin_session_refinement_step(job_id, REFINEMENT_NODE_IDS[0])
            self._notify_refinement_lifecycle(job_id)
            if _sha256_json({"source": source_binding, "messages": messages}) != snapshot_sha256:
                raise SessionProductError("SESSION_REFINEMENT_FROZEN_SNAPSHOT_DRIFT")
            if manager.session_refinement_cancelled(job_id):
                return
            manager.complete_session_refinement_step(job_id, REFINEMENT_NODE_IDS[0])

            manager.begin_session_refinement_step(job_id, REFINEMENT_NODE_IDS[1])
            if any(
                not isinstance(message, Mapping)
                or not _text(message.get("role"), 80)
                or not _text(message.get("content"), 200000)
                for message in messages
            ):
                raise SessionProductError("SESSION_REFINEMENT_SOURCE_MESSAGE_INVALID")
            channel_messages = deepcopy(messages)
            if manager.session_refinement_cancelled(job_id):
                return
            manager.complete_session_refinement_step(job_id, REFINEMENT_NODE_IDS[1])

            current_task = manager.get_session_refinement_task(job_id)
            settings = {
                **deepcopy(dict(settings)),
                "profile_ref": current_task["selected_profile_ref"],
            }
            manager.begin_session_refinement_step(job_id, REFINEMENT_NODE_IDS[2])
            execute = getattr(
                self._conversation_refinement_channel,
                "execute",
                None,
            )
            if not callable(execute):
                raise SessionProductError("SESSION_REFINEMENT_CHANNEL_UNAVAILABLE")
            request = {
                "task_id": job_id,
                **({"language_context":current_task["language_context"]} if current_task.get("language_context") is not None else {}),
                "logical_slot": "conversation_refinement",
                "profile_ref": settings["profile_ref"],
                "explicit_user_action": True,
                "source_snapshot_sha256": snapshot_sha256,
                "source": deepcopy(dict(source_binding)),
                "messages": channel_messages,
                "review_required": True,
                "auto_ingest": False,
            }
            raw_result = execute(request)
            result = self._validate_refinement_channel_result(
                raw_result,
                task_id=job_id,
                profile_ref=str(settings["profile_ref"]),
            )
            model_calls = 1
            self._metrics["refinement_model_calls"] += 1
            self._metrics["external_model_calls"] += int(result["external_model_calls"])
            self._metrics["external_network_calls"] += int(result["external_network_calls"])
            self._metrics["provider_calls"] += int(result["provider_calls"])
            if manager.session_refinement_cancelled(job_id):
                return
            manager.complete_session_refinement_step(job_id, REFINEMENT_NODE_IDS[2])

            manager.begin_session_refinement_step(job_id, REFINEMENT_NODE_IDS[3])
            draft_id = f"refinement-{job_id.removeprefix('refinement-')[:32]}"
            draft = {
                "schema_version": "DesktopSessionRefinementDraft-v3",
                **({"language_context":current_task["language_context"]} if current_task.get("language_context") is not None else {}),
                "draft_id": draft_id,
                "task_id": job_id,
                "display_name": current_task["display_name"],
                "local_projection_id": projection_id,
                "source_snapshot_sha256": snapshot_sha256,
                "source": deepcopy(dict(source_binding)),
                "message_count": len(channel_messages),
                "selected_profile_ref": settings["profile_ref"],
                "requested_model": result["requested_model"],
                "returned_model": result["returned_model"],
                "execution_mode": "EXISTING_CONVERSATION_REFINEMENT_CHANNEL",
                "logical_slot": "conversation_refinement",
                "subject_output": result["subject_output"],
                "items": result["subject_output"]["items"],
                "execution_receipt": result["execution_receipt"],
                "review_required": True,
                "auto_ingest": False,
                "model_calls": 1,
                "external_model_calls": result["external_model_calls"],
                "external_network_calls": result["external_network_calls"],
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            }
            if manager.session_refinement_cancelled(job_id):
                return
            with self._refinement_lock:
                self._write_private_json(
                    self._refinement_root / f"{projection_id}.json", draft
                )
            self._metrics["local_projection_writes"] += 1
            self._metrics["refinement_drafts_created"] += 1
            if self._refinement_result_observer is not None:
                self._refinement_result_observer(deepcopy(draft))
            manager.complete_session_refinement_step(job_id, REFINEMENT_NODE_IDS[3])
            manager.succeed_session_refinement_task(
                job_id,
                draft_id=draft_id,
                model_calls=1,
                external_model_calls=int(result["external_model_calls"]),
            )
            self._notify_refinement_lifecycle(job_id)
        except RefinementTaskCancelled:
            return
        except Exception as error:
            error_code = (
                str(getattr(error, "code"))
                if isinstance(getattr(error, "code", None), str)
                else str(error)
                if isinstance(error, SessionProductError)
                else "SESSION_REFINEMENT_EXECUTION_FAILED"
            )
            self._metrics["refinement_tasks_failed"] += 1
            try:
                if not manager.session_refinement_cancelled(job_id):
                    manager.fail_session_refinement_task(
                        job_id,
                        error_code=safe_error_code(error_code),
                        model_calls=model_calls,
                    )
                    self._notify_refinement_lifecycle(job_id)
            except Exception:
                pass
        finally:
            if admitted: gate.__exit__(None, None, None)
            with self._refinement_lock:
                self._refinement_workers.pop(job_id, None)

    def _refinement_wait_checkpoint(self, job_id):
        if self._refinement_task_manager.session_refinement_cancelled(job_id):
            raise RefinementTaskCancelled('REFINEMENT_TASK_CANCELLED')

    def _create_refinement_task(self, projection_id: str) -> dict[str, Any]:
        row = self._get_internal(projection_id)
        source_messages, completeness = self._messages_for(row)
        if not self._refinement_source_content_allowed(
            row, source_messages, completeness
        ):
            raise SessionProductError("SESSION_REFINEMENT_REQUIRES_COMPLETE_CONTENT")
        messages = self._refinement_channel_messages(source_messages)
        settings = self._load_refinement_settings()
        if not settings.get("profile_ref"):
            raise SessionProductError("SESSION_REFINEMENT_MODEL_NOT_CONFIGURED")
        settings, selected_option = self._prepare_refinement_settings_for_start(settings)
        manager = self._refinement_task_manager
        if manager is None:
            raise SessionProductError("SESSION_REFINEMENT_TASK_MANAGER_NOT_CONFIGURED")
        if not callable(
            getattr(
                self._conversation_refinement_channel,
                "execute",
                None,
            )
        ):
            raise SessionProductError("SESSION_REFINEMENT_CHANNEL_UNAVAILABLE")
        source_binding = {
            "schema_version": "DesktopSessionRefinementSourceBinding-v1",
            "local_projection_id": projection_id,
            "provider_id": _text(row.get("provider_id"), 80),
            "provider_session_id": _text(row.get("provider_session_id"), 240),
            "source_format": _text(row.get("source_format"), 160),
            "captured_at": _text(
                row.get("captured_at")
                or row.get("conversation_at")
                or row.get("updated_at")
                or "UNKNOWN",
                120,
            ),
            "content_completeness": deepcopy(dict(completeness)),
        }
        snapshot_sha256 = _sha256_json(
            {"source": source_binding, "messages": messages}
        )
        active_task = self._refinement_task_for_projection(projection_id)
        control_state = str(active_task.get("control_state") or "") if active_task else ""
        if active_task is not None and control_state in {"QUEUED", "RUNNING", "SUCCEEDED"}:
            return {
                "schema_version": "DesktopSessionRefineReceipt-v6",
                "local_projection_id": projection_id,
                "job_id": active_task["job_id"],
                "inbox_item_id": None,
                "workflow_kind": "SESSION_REFINEMENT",
                "deduplicated": True,
                "model_calls": int(active_task.get("model_calls", 0)),
                "status": {
                    "QUEUED": "TASK_QUEUED",
                    "RUNNING": "TASK_RUNNING",
                    "SUCCEEDED": "TASK_SUCCEEDED",
                }.get(control_state, "TASK_STATE_UNKNOWN"),
            }
        inbox_item = self._find_inbox_refinement(projection_id)
        if inbox_item is not None and inbox_item.get("state") in {
            "QUEUED",
            "PROCESSING",
        }:
            return {
                "schema_version": "DesktopSessionRefineReceipt-v6",
                "local_projection_id": projection_id,
                "job_id": inbox_item.get("job_id") or None,
                "inbox_item_id": inbox_item["item_id"],
                "workflow_kind": "SESSION_REFINEMENT",
                "deduplicated": True,
                "model_calls": 0,
                "status": "INBOX_QUEUED",
            }
        if inbox_item is not None and inbox_item.get("state") == "ERROR":
            self._inbox_refinement_call(
                "inbox.cancel_session_refinement",
                {
                    "item_id": inbox_item["item_id"],
                    "operation_id": "refinement-retry-retire-" + uuid.uuid4().hex,
                },
            )
        from memorive_language import from_settings
        from memorive_language.text import choose
        language_context=from_settings(self.service)
        display_name = choose(language_context,'精炼对话','Conversation refinement','会話の精錬')+' · '+(_text(row.get('title'),120) or projection_id)
        # An active Inbox item is the duplicate guard.  A fresh key permits a
        # user-requested retry after a failed/cancelled card has been removed.
        queue_key = "refinement-" + uuid.uuid4().hex
        try:
            queued = self._inbox_refinement_call(
                "inbox.enqueue_session_refinement",
                {
                    "local_projection_id": projection_id,
                    "display_name": display_name,
                    "source_snapshot_sha256": snapshot_sha256,
                    "selected_profile_ref": str(settings["profile_ref"]),
                    "message_count": len(messages),
                    "idempotency_key": queue_key,
                    "language_context": language_context,
                },
            )
        except (AssertionError, AttributeError):
            queued = None
        if isinstance(queued, Mapping):
            item = queued.get("item")
            if not isinstance(item, Mapping) or not item.get("item_id"):
                raise SessionProductError("SESSION_REFINEMENT_INBOX_RECEIPT_INVALID")
            # Persist the editable task while it is queued, before the worker
            # freezes execution. Previously there was no task during this
            # window, making the existing model selector unreachable.
            task = self._ensure_refinement_queue_task({
                "language_context": language_context,
                "display_name": display_name,
                "local_projection_id": projection_id,
                "source_snapshot_sha256": snapshot_sha256,
                "selected_profile_ref": str(settings["profile_ref"]),
                "message_count": len(messages),
            })
            self._schedule_refinement_queue_item(
                {
                    "item_id": item["item_id"],
                    "local_projection_id": projection_id,
                    "display_name": display_name,
                    "source_snapshot_sha256": snapshot_sha256,
                    "selected_profile_ref": settings["profile_ref"],
                    "message_count": len(messages),
                }
            )
            return {
                "schema_version": "DesktopSessionRefineReceipt-v6",
                "local_projection_id": projection_id,
                "job_id": task["job_id"],
                "inbox_item_id": item["item_id"],
                "workflow_kind": "SESSION_REFINEMENT",
                "source_snapshot_sha256": snapshot_sha256,
                "selected_profile_ref": settings["profile_ref"],
                "deduplicated": queued.get("replayed") is True,
                "review_required": True,
                "auto_ingest": False,
                "model_calls": 0,
                "status": "INBOX_QUEUED",
            }
        task = manager.create_session_refinement_task(
            display_name=display_name,
            local_projection_id=projection_id,
            source_snapshot_sha256=snapshot_sha256,
            selected_profile_ref=str(settings["profile_ref"]),
            message_count=len(messages),
            language_context=language_context,
        )
        job_id = str(task["job_id"])
        self._metrics["refinement_tasks_created"] += 1
        return {
            "schema_version": "DesktopSessionRefineReceipt-v5",
            "local_projection_id": projection_id,
            "job_id": job_id,
            "attempt_id": task["attempt_id"],
            "workflow_kind": "SESSION_REFINEMENT",
            "workflow_node_ids": list(REFINEMENT_NODE_IDS),
            "source_snapshot_sha256": snapshot_sha256,
            "selected_profile_ref": settings["profile_ref"],
            "review_required": True,
            "auto_ingest": False,
            "model_calls": 0,
            "status": "TASK_CREATED",
        }

    def _start_refinement_task(self, job_id: str) -> dict[str, Any]:
        manager = self._refinement_task_manager
        if manager is None:
            raise SessionProductError("SESSION_REFINEMENT_TASK_MANAGER_NOT_CONFIGURED")
        task = manager.get_session_refinement_task(job_id)
        if task.get("control_state") != "QUEUED":
            raise SessionProductError("SESSION_REFINEMENT_TASK_NOT_QUEUED")
        projection_id = str(task["local_projection_id"])
        row = self._get_internal(projection_id)
        source_messages, completeness = self._messages_for(row)
        if not self._refinement_source_content_allowed(
            row, source_messages, completeness
        ):
            raise SessionProductError("SESSION_REFINEMENT_REQUIRES_COMPLETE_CONTENT")
        messages = self._refinement_channel_messages(source_messages)
        source_binding = {
            "schema_version": "DesktopSessionRefinementSourceBinding-v1",
            "local_projection_id": projection_id,
            "provider_id": _text(row.get("provider_id"), 80),
            "provider_session_id": _text(row.get("provider_session_id"), 240),
            "source_format": _text(row.get("source_format"), 160),
            "captured_at": _text(
                row.get("captured_at")
                or row.get("conversation_at")
                or row.get("updated_at")
                or "UNKNOWN",
                120,
            ),
            "content_completeness": deepcopy(dict(completeness)),
        }
        snapshot_sha256 = _sha256_json(
            {"source": source_binding, "messages": messages}
        )
        if snapshot_sha256.upper() != str(task["source_snapshot_sha256"]).upper():
            raise SessionProductError("SESSION_REFINEMENT_FROZEN_SNAPSHOT_DRIFT")
        saved_settings = self._load_refinement_settings()
        task_settings = {
            **saved_settings,
            "profile_ref": task["selected_profile_ref"],
        }
        task_settings, _ = self._prepare_refinement_settings_for_start(task_settings)
        worker = threading.Thread(
            target=self._run_refinement_task,
            kwargs={
                "job_id": job_id,
                "projection_id": projection_id,
                "snapshot_sha256": snapshot_sha256,
                "messages": messages,
                "source_binding": source_binding,
                "settings": deepcopy(task_settings),
            },
            name=f"memorive-session-refinement-{job_id[-12:]}",
            daemon=True,
        )
        with self._refinement_lock:
            if job_id in self._refinement_workers:
                raise SessionProductError("SESSION_REFINEMENT_TASK_ALREADY_STARTING")
            self._refinement_workers[job_id] = worker
        worker.start()
        return {
            "schema_version": "DesktopSessionRefinementStartReceipt-v1",
            "local_projection_id": projection_id,
            "job_id": job_id,
            "attempt_id": task["attempt_id"],
            "source_snapshot_sha256": snapshot_sha256,
            "selected_profile_ref": task_settings["profile_ref"],
            "model_calls": 0,
            "status": "TASK_STARTED",
        }

    def _cancel_refinement_task(self, inbox_item_id: str) -> dict[str, Any]:
        item_id = str(inbox_item_id or "").strip()
        if not item_id:
            raise SessionProductError("SESSION_REFINEMENT_INBOX_ITEM_REQUIRED")
        item = self._inbox_refinement_call("inbox.item_detail", {"item_id": item_id})
        if not isinstance(item, Mapping) or item.get("item_kind") != "SESSION_REFINEMENT":
            raise SessionProductError("SESSION_REFINEMENT_INBOX_ITEM_REQUIRED")
        job_id = str(item.get("job_id") or "")
        if not job_id and self._refinement_task_manager is not None:
            queued_task = self._refinement_task_for_projection(str(item.get("local_projection_id") or ""))
            if (queued_task is not None
                    and queued_task.get("control_state") == "QUEUED"
                    and queued_task.get("source_snapshot_sha256") == item.get("source_snapshot_sha256")):
                job_id = str(queued_task["job_id"])
        task_state = "NOT_CREATED"
        if job_id and self._refinement_task_manager is not None:
            try:
                task = self._refinement_task_manager.get_session_refinement_task(job_id)
                task_state = str(task.get("control_state") or "")
                if task_state in {"QUEUED", "RUNNING"}:
                    cancel = getattr(
                        self._refinement_task_manager,
                        "cancel_session_refinement_task",
                        None,
                    ) or getattr(self._refinement_task_manager, "cancel", None)
                    if not callable(cancel):
                        raise SessionProductError(
                            "SESSION_REFINEMENT_CANCEL_CHANNEL_UNAVAILABLE"
                        )
                    task = cancel(job_id)
                    task_state = str(task.get("control_state") or "CANCELLED")
                    self._notify_refinement_lifecycle(job_id)
            except KeyError:
                task_state = "NOT_CREATED"
            except RefinementTaskError as error:
                if str(error) != "REFINEMENT_TASK_NOT_FOUND":
                    raise
                task_state = "NOT_CREATED"
        removal = self._inbox_refinement_call(
            "inbox.cancel_session_refinement",
            {
                "item_id": item_id,
                "operation_id": "refinement-cancel-" + uuid.uuid4().hex,
            },
        )
        self._notify_refinement_queue_projection(item_id)
        return {
            "schema_version": "DesktopSessionRefinementCancelReceipt-v2",
            "inbox_item_id": item_id,
            "job_id": job_id or None,
            "task_state": task_state,
            "permanent_deletes": 0,
            "removal": deepcopy(dict(removal)) if isinstance(removal, Mapping) else None,
            "status": "CANCELLED",
        }

    def _capture_intent(self, channel: str) -> dict[str, Any]:
        if channel == "exact_local_cli_session_root":
            return {
                "schema_version": "SessionsCaptureIntentReceipt-v2",
                "channel": channel,
                "status": "ALREADY_CONNECTED_READ_ONLY",
                "capture_started": False,
                "metadata_first": True,
                "selected_detail_on_demand": True,
            }
        if channel == "existing_login_visible_ui":
            refresh = self._refresh()
            status = refresh["source_status"].get("deepseek", {})
            return {
                "schema_version": "SessionsCaptureIntentReceipt-v2",
                "channel": channel,
                "status": status.get("status", "VISIBLE_UI_READ_FAILED"),
                "capture_started": False,
                "metadata_first": True,
                "selected_detail_on_demand": True,
                "record_count": int(status.get("record_count", 0)),
                "remote_session_mutated": False,
            }
        if channel in REAL_CHANNELS:
            return {
                "schema_version": "SessionsCaptureIntentReceipt-v2",
                "channel": channel,
                "status": "DISABLED_NOT_ASSESSED",
                "capture_started": False,
                "reason": "CHANNEL_NOT_ENABLED_FOR_VISIBLE_CHAT",
            }
        if channel != "synthetic_offline_d38":
            raise SessionProductError("SESSION_CAPTURE_CHANNEL_UNKNOWN")
        state = self._load()
        job_id = f"synthetic-capture-{len(state['capture_jobs']) + 1:04d}"
        state["capture_jobs"][job_id] = {
            "job_id": job_id,
            "channel": channel,
            "status": "WAITING",
            "progress": 0,
            "retry_count": 0,
            "receipt_id": f"capture-receipt-{len(state['capture_jobs']) + 1:04d}",
        }
        self._save(state)
        return {
            "schema_version": "SessionsCaptureIntentReceipt-v2",
            **deepcopy(state["capture_jobs"][job_id]),
            "synthetic_only": True,
            "real_source_content_captured": False,
            "status": "AUTHORIZED_SYNTHETIC_TEST_TRACK",
        }

    def call(self, method: str, params: Mapping[str, Any]) -> Any:
        with self._state_lock:
            return self._call_unlocked(method, params)

    def _call_unlocked(self, method: str, params: Mapping[str, Any]) -> Any:
        if method not in SESSIONS_METHODS:
            raise SessionProductError("SESSIONS_PRODUCT_METHOD_NOT_ALLOWLISTED")
        accepted = dict(params)
        if any(str(key).startswith("_") for key in accepted):
            raise SessionProductError("SESSIONS_PRODUCT_PRIVATE_PARAM_FORBIDDEN")
        if method == "sessions.get_contract":
            self._require_keys(accepted, set())
            return self._contract()
        if method == "sessions.bootstrap":
            self._require_keys(accepted, set())
            if not self._service_ready():
                raise SessionProductError("SESSIONS_SERVICE_HEALTH_CONTRACT_INVALID")
            refresh = self._refresh()
            rows, pagination = self._page_rows(
                self._filtered_rows(),
                page_offset=0,
                page_size=30,
            )
            total_count = len(self._filtered_rows())
            return {
                "schema_version": "SessionsSessionsBootstrapProjection-v2",
                "records": rows,
                "record_count": len(rows),
                "total_count": total_count,
                "discovery_window_days": self.discovery_window_days,
                "source_capabilities": self._source_capabilities(),
                "source_count": len(self._source_capabilities()),
                "source_status": refresh["source_status"],
                "source_access_envelope_status": "ACTIVE_LOCAL_READ_ONLY",
                "real_source_channels_assessed": refresh["available_source_count"],
                "synthetic_only": False,
                "sample_fixture_retained_for_tests": (self.fixture_root / "synthetic_sessions.json").is_file(),
                "raw_private_content_included": False,
                "sort_order": "CONVERSATION_TIME_DESC_UNKNOWN_LAST",
                **pagination,
                "status": "PASS",
            }
        if method == "sessions.list":
            self._require_keys(
                accepted,
                set(),
                {"source", "search", "limit_per_source", "page_offset", "page_size"},
            )
            source = str(accepted.get("source", "all"))
            search = str(accepted.get("search", ""))
            limit_per_source = int(accepted.get("limit_per_source", 30))
            rows = self._rows(source, search, limit_per_source)
            pagination: dict[str, Any] = {}
            if "page_offset" in accepted or "page_size" in accepted:
                # Pagination must traverse the same collection as total_count.
                # The per-source preview cap applies only to unpaged previews.
                rows, pagination = self._page_rows(
                    self._filtered_rows(source, search),
                    page_offset=int(accepted.get("page_offset", 0)),
                    page_size=int(accepted.get("page_size", 30)),
                )
            total_count = len(self._filtered_rows(source, search))
            state = self._load()
            state["last_filter"] = {
                "source": source,
                "search": search,
                "limit_per_source": limit_per_source,
            }
            self._save(state)
            return {
                "schema_version": "SessionsSessionListProjection-v2",
                "source": source,
                "search_sha256": hashlib.sha256(search.encode("utf-8")).hexdigest().upper(),
                "rows": rows,
                "row_count": len(rows),
                "total_count": total_count,
                "limit_per_source": limit_per_source,
                "discovery_window_days": self.discovery_window_days,
                "raw_private_content_included": False,
                "sort_order": "CONVERSATION_TIME_DESC_UNKNOWN_LAST",
                **pagination,
                "status": "PASS",
            }
        if method == "sessions.get":
            self._require_keys(accepted, {"local_projection_id"})
            row = self._get_internal(str(accepted["local_projection_id"]))
            messages, completeness = self._messages_for(row, presentation=True)
            session = self._public(row)
            session["partial"] = completeness.get("status") != "COMPLETE"
            return {
                "schema_version": "SessionsSessionDetailProjection-v2",
                "session": session,
                "messages": messages,
                "message_count": len(messages),
                "completeness": completeness,
                "local_private_content_included": bool(messages),
                "raw_private_content_externalized": False,
                "external_network_calls": 0,
                "status": "PASS",
            }
        if method == "sessions.get_provider_capabilities":
            self._require_keys(accepted, set(), {"provider_id"})
            provider_id = accepted.get("provider_id")
            rows = [row for row in self._source_capabilities() if provider_id is None or row["provider_id"] == provider_id]
            return {
                "schema_version": "SessionsProviderCapabilityProjection-v2",
                "sources": deepcopy(rows),
                "source_count": len(rows),
                "status": "PASS",
            }
        if method == "sessions.capture_intent":
            self._require_keys(accepted, {"channel"})
            return self._capture_intent(str(accepted["channel"]))
        if method in {"sessions.capture_progress", "sessions.capture_cancel", "sessions.capture_retry"}:
            self._require_keys(accepted, {"job_id"}, {"progress"})
            state = self._load()
            job = state["capture_jobs"].get(str(accepted["job_id"]))
            if not job:
                raise SessionProductError("CAPTURE_JOB_NOT_FOUND")
            if method == "sessions.capture_progress":
                value = int(accepted.get("progress", 50))
                if not 0 <= value <= 100:
                    raise SessionProductError("CAPTURE_PROGRESS_INVALID")
                job.update({"progress": value, "status": "COMPLETED" if value == 100 else "WORKING"})
            elif method == "sessions.capture_cancel":
                job["status"] = "CANCELLED"
            else:
                job.update({"status": "WAITING", "progress": 0, "retry_count": int(job["retry_count"]) + 1})
            self._save(state)
            return {
                "schema_version": "SessionsCaptureControlReceipt-v2",
                **deepcopy(job),
                "synthetic_only": True,
                "status": "PASS",
            }
        if method == "sessions.refinement_settings_get":
            self._require_keys(accepted, set())
            return self._refinement_settings_projection()
        if method == "sessions.refinement_settings_save":
            self._require_keys(accepted, {"profile_ref", "include_tasks", "include_risks"})
            profile_ref = _text(accepted.get("profile_ref"), 160)
            if not isinstance(accepted.get("include_tasks"), bool) or not isinstance(accepted.get("include_risks"), bool):
                raise SessionProductError("SESSION_REFINEMENT_OPTIONS_INVALID")
            allowed = {row["profile_ref"] for row in self._configured_refinement_models()}
            if profile_ref and profile_ref not in allowed:
                raise SessionProductError("SESSION_REFINEMENT_PROFILE_NOT_CONFIGURED")
            settings = {
                "schema_version": "DesktopSessionRefinementSettings-v1",
                "profile_ref": profile_ref,
                "include_tasks": accepted["include_tasks"],
                "include_risks": accepted["include_risks"],
            }
            self._write_private_json(self._refinement_settings_path, settings)
            self._metrics["local_projection_writes"] += 1
            return {
                "schema_version": "DesktopSessionRefinementSettingsSaveReceipt-v1",
                "settings": settings,
                "external_model_calls": 0,
                "status": "PASS",
            }
        if method == "sessions.refinement_get":
            self._require_keys(accepted, {"local_projection_id"})
            projection_id = str(accepted["local_projection_id"])
            self._get_internal(projection_id)
            task = self._refinement_task_for_projection(projection_id)
            if task is None:
                inbox_item = self._find_inbox_refinement(projection_id)
                if inbox_item is not None:
                    return {
                        "schema_version": "DesktopSessionRefinementTaskProjection-v2",
                        "task": None,
                        "inbox_item": inbox_item,
                        "draft": None,
                        "status": (
                            "INBOX_QUEUED"
                            if inbox_item.get("state") == "QUEUED"
                            else "INBOX_PROCESSING"
                            if inbox_item.get("state") == "PROCESSING"
                            else "INBOX_FAILED"
                        ),
                    }
            if task is not None and task.get("control_state") != "SUCCEEDED":
                return {
                    "schema_version": "DesktopSessionRefinementTaskProjection-v1",
                    "task": task,
                    "draft": None,
                    "status": {
                        "QUEUED": "TASK_QUEUED",
                        "RUNNING": "TASK_RUNNING",
                        "FAILED": "TASK_FAILED",
                        "CANCELLED": "TASK_CANCELLED",
                    }.get(str(task.get("control_state")), "TASK_STATE_UNKNOWN"),
                }
            path = self._refinement_root / f"{projection_id}.json"
            if not path.is_file():
                raise SessionProductError("SESSION_REFINEMENT_DRAFT_NOT_FOUND")
            try:
                draft = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise SessionProductError("SESSION_REFINEMENT_DRAFT_UNAVAILABLE") from error
            return {
                "schema_version": "DesktopSessionRefinementDraftProjection-v1",
                "draft": draft,
                "task": task,
                "status": "PASS",
            }
        if method == "sessions.refine":
            self._require_keys(accepted, {"local_projection_id"})
            projection_id = str(accepted["local_projection_id"])
            return self._create_refinement_task(projection_id)
        if method == "sessions.refinement_cancel":
            self._require_keys(accepted, {"inbox_item_id"})
            return self._cancel_refinement_task(str(accepted["inbox_item_id"]))
        if method == "sessions.refinement_start":
            self._require_keys(accepted, {"job_id"})
            return self._start_refinement_task(str(accepted["job_id"]))
        if method == "sessions.delete_local":
            self._require_keys(accepted, {"local_projection_id"})
            projection_id = str(accepted["local_projection_id"])
            self._get_internal(projection_id)
            state = self._load()
            hidden = set(state["hidden_projection_ids"])
            hidden.add(projection_id)
            state["hidden_projection_ids"] = sorted(hidden)
            self._save(state)
            return {
                "schema_version": "SessionsDeleteReceipt-v2",
                "local_projection_id": projection_id,
                "local_projection_hidden": True,
                "local_projection_deleted": False,
                "source_record_deleted": False,
                "remote_session_mutated": False,
                "status": "PASS",
            }
        if method == "sessions.bulk_delete_local":
            self._require_keys(accepted, {"local_projection_ids"})
            values = accepted["local_projection_ids"]
            if (
                not isinstance(values, list)
                or not values
                or not all(isinstance(value, str) and value for value in values)
                or len(values) != len(set(values))
            ):
                raise SessionProductError("SESSION_IDS_INVALID")
            receipts = [self.call("sessions.delete_local", {"local_projection_id": value}) for value in values]
            return {
                "schema_version": "SessionsBulkDeleteReceipt-v2",
                "selected_count": len(values),
                "success_count": len(values),
                "blocked_count": 0,
                "receipts": receipts,
                "remote_session_mutations": 0,
                "status": "PASS",
            }
        if method == "sessions.refresh":
            self._require_keys(
                accepted,
                set(),
                {"source", "search", "limit_per_source", "page_offset", "page_size"},
            )
            prior_filter = self._load().get("last_filter", {})
            source = str(accepted.get("source", prior_filter.get("source", "all")))
            search = str(accepted.get("search", prior_filter.get("search", "")))
            limit_per_source = int(
                accepted.get(
                    "limit_per_source",
                    prior_filter.get("limit_per_source", 30),
                )
            )
            refresh = self._refresh()
            rows = self._rows(source, search, limit_per_source)
            pagination: dict[str, Any] = {}
            if "page_offset" in accepted or "page_size" in accepted:
                # Pagination must traverse the same collection as total_count.
                # The per-source preview cap applies only to unpaged previews.
                rows, pagination = self._page_rows(
                    self._filtered_rows(source, search),
                    page_offset=int(accepted.get("page_offset", 0)),
                    page_size=int(accepted.get("page_size", 30)),
                )
            state = self._load()
            state["last_filter"] = {
                "source": source,
                "search": search,
                "limit_per_source": limit_per_source,
            }
            self._save(state)
            return {
                "schema_version": "SessionsSessionRefreshReceipt-v2",
                **refresh,
                "records": rows,
                "total_count": len(self._filtered_rows(source, search)),
                "filter": deepcopy(state["last_filter"]),
                "sort_order": "CONVERSATION_TIME_DESC_UNKNOWN_LAST",
                "provider_calls": 0,
                "external_network_calls": 0,
                **pagination,
                "status": "PASS",
            }
        if method == "sessions.resolve_locator":
            self._require_keys(accepted, {"locator"})
            locator = str(accepted["locator"])
            prefix = "memorive://session/"
            if not locator.startswith(prefix):
                raise SessionProductError("SESSION_LOCATOR_INVALID")
            projection_id = locator[len(prefix) :]
            self._get_internal(projection_id)
            return {
                "schema_version": "SessionsSessionLocatorReceipt-v2",
                "locator": locator,
                "local_projection_id": projection_id,
                "route": "sessions",
                "fuzzy_match_attempted": False,
                "status": "PASS",
            }
        if method == "sessions.inject_fault":
            self._require_keys(accepted, {"fault"})
            fault = str(accepted["fault"])
            allowed = {"duplicate", "partial", "corrupt", "revoked", "expired", "source_drift", "interrupted_capture"}
            if fault not in allowed:
                raise SessionProductError("SESSION_FAULT_UNKNOWN")
            classification = {
                "duplicate": "DEDUPED_BY_PROVIDER_SESSION_AND_CONTENT_HASH",
                "partial": "PARTIAL_LOCAL_PRIVATE_PROJECTION",
                "corrupt": "REJECTED_FAIL_CLOSED",
                "revoked": "SOURCE_DISABLED_NOT_ASSESSED",
                "expired": "SOURCE_DISABLED_NOT_ASSESSED",
                "source_drift": "SOURCE_DRIFT_REQUIRES_REFRESH",
                "interrupted_capture": "RECOVERABLE_FROM_LOCAL_METADATA_INDEX",
            }[fault]
            return {
                "schema_version": "SessionsSessionFaultReceipt-v2",
                "fault": fault,
                "classification": classification,
                "raw_private_content_exposed": False,
                "remote_session_mutated": False,
                "status": "PASS",
            }
        if method == "sessions.restore":
            self._require_keys(accepted, set())
            state = self._load()
            return {
                "schema_version": "SessionsSessionRestartRecoveryReceipt-v2",
                "revision": state["revision"],
                "session_count": len(self._rows()),
                "capture_jobs": deepcopy(state["capture_jobs"]),
                "stable_locator_replay": True,
                "status": "PASS",
            }
        self._require_keys(accepted, set())
        return {
            "schema_version": "SessionsSessionEffectMetrics-v2",
            **deepcopy(self._metrics),
            "source_access_envelope_present": True,
            "source_access_envelope_status": "ACTIVE_LOCAL_READ_ONLY",
            "real_source_channels_assessed": sum(
                bool(value.get("available")) for value in self._load()["source_status"].values()
            ),
            "status": "PASS",
        }


__all__ = ["REAL_CHANNELS", "SESSIONS_METHODS", "SessionProductError", "SessionsProductController"]
