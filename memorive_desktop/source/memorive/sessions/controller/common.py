"""What the parts of the sessions controller share: the method set and channels, the public row fields,
the visible-UI and universal site names, text, title and timestamp helpers, the projection state lock
and SessionProductError.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
from typing import Any, Mapping
from urllib.parse import urlsplit


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
        "source_site",
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
    "yiyan.baidu.com": "文心一言网页",
    "chatglm.cn": "智谱清言网页",
}


def _universal_source_name(source_locator: Any) -> str:
    try:
        host = (urlsplit(str(source_locator or "")).hostname or "").lower()
    except ValueError:
        host = ""
    if host in _UNIVERSAL_SITE_NAMES:
        return f"{_UNIVERSAL_SITE_NAMES[host]} · 当前页录入"
    # Other official AI websites the extension records (Gemini, DeepSeek, Kimi, Grok) by their brand name.
    from memorive.sessions.companion.site_profiles import official_web_identity
    try:
        identity = official_web_identity(str(source_locator or ""))
    except ValueError:
        identity = None
    if identity:
        return f"{identity['name']} 网页 · 当前页录入"
    safe_host = re.sub(r"[^a-z0-9.-]+", "", host)[:96]
    return f"{safe_host or '通用 AI 网页'} · 当前页录入"


def _universal_source_site(source_locator: Any) -> str:
    """The recorded website's key in the official site list (chatgpt, claude, gemini, ...), or "" for another
    address; the sessions page shows a letter mark by it."""
    from memorive.sessions.companion.site_profiles import official_web_identity
    identity = official_web_identity(str(source_locator or ""))
    return identity["provider"] if identity else ""


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
