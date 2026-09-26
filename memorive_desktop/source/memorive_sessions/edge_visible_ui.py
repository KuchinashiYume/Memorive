from __future__ import annotations

from datetime import datetime, timezone
import ctypes
import hashlib
import math
import os
from pathlib import Path
import struct
import time
from typing import Any, Iterable
from urllib.parse import urlsplit


class VisibleUiCaptureError(RuntimeError):
    """A fail-closed visible-UI boundary error with no page content attached."""


_ADDRESS_NAMES = {
    "address and search bar",
    "address bar",
    "地址和搜索栏",
    "地址栏",
    "アドレスと検索バー",
    "アドレス バー",
}
_NON_MESSAGE_LABELS = {
    "deepseek",
    "kimi",
    "gemini",
    "new chat",
    "新建对话",
    "新しいチャット",
    "copy",
    "复制",
    "コピー",
    "edit",
    "编辑",
    "編集",
    "retry",
    "重新生成",
    "再生成",
    "send",
    "发送",
    "送信",
    "stop generating",
    "停止生成",
    "生成を停止",
}


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    rendered = "\n".join(line.strip() for line in value.replace("\x00", " ").splitlines() if line.strip())
    if not rendered:
        return ""
    return rendered


def _clean_message_text(value: Any) -> str:
    rendered = _clean_text(value)
    if not rendered:
        return ""
    lines = [
        line
        for line in rendered.splitlines()
        if line.strip().casefold() not in _NON_MESSAGE_LABELS
    ]
    return "\n".join(lines).strip()


def _collection_values(collection: Any) -> Iterable[Any]:
    for index in range(int(collection.Count)):
        yield collection[index]


class EdgeDeepSeekVisibleUIReader:
    """Read only the currently rendered DeepSeek page through Windows UI Automation.

    The adapter never activates, clicks, types into, or navigates Edge.  It also
    never reads browser storage, cookies, credentials, the clipboard, or a
    network endpoint.  Browser and Windows profile locations are discovered at
    runtime, so the implementation has no user name, drive, DPI, or screen-size
    dependency.
    """

    provider_id = "deepseek"
    provider_profile = "DEEPSEEK_EDGE_VISIBLE_UI_V1"
    provider_hosts = frozenset({"chat.deepseek.com"})
    provider_title_tokens = ("deepseek",)
    boundary_method = "HOST_OBSERVED_VISIBLE_UI_FULL_SCROLL_V2"

    @staticmethod
    def _windows_directory() -> Path:
        configured = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
        if configured:
            return Path(configured).resolve()
        buffer = ctypes.create_unicode_buffer(32768)
        length = int(ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer)))
        if length <= 0 or length >= len(buffer):
            raise VisibleUiCaptureError("WINDOWS_DIRECTORY_UNAVAILABLE")
        return Path(buffer.value).resolve()

    @classmethod
    def _automation_assembly(cls, name: str) -> Path:
        windows = cls._windows_directory()
        framework = "Framework64" if struct.calcsize("P") == 8 else "Framework"
        direct = windows / "Microsoft.NET" / framework / "v4.0.30319" / "WPF" / f"{name}.dll"
        if direct.is_file():
            return direct
        gac = windows / "Microsoft.NET" / "assembly" / "GAC_MSIL" / name
        matches = sorted(gac.glob(f"v4.0_*__31bf3856ad364e35/{name}.dll")) if gac.is_dir() else []
        if matches:
            return matches[-1]
        raise VisibleUiCaptureError("DEEPSEEK_UIA_ASSEMBLY_UNAVAILABLE")

    @classmethod
    def _automation(cls) -> dict[str, Any]:
        if os.name != "nt":
            raise VisibleUiCaptureError("DEEPSEEK_VISIBLE_UI_WINDOWS_REQUIRED")
        try:
            import clr

            clr.AddReference(str(cls._automation_assembly("UIAutomationTypes")))
            clr.AddReference(str(cls._automation_assembly("UIAutomationClient")))
            clr.AddReference("System")
            from System.Diagnostics import Process
            from System.Windows.Automation import (
                AutomationElement,
                Condition,
                ControlType,
                PropertyCondition,
                ScrollAmount,
                ScrollPattern,
                TreeScope,
                ValuePattern,
            )
        except Exception as error:
            raise VisibleUiCaptureError("DEEPSEEK_UIA_RUNTIME_UNAVAILABLE") from error
        return {
            "AutomationElement": AutomationElement,
            "Condition": Condition,
            "ControlType": ControlType,
            "Process": Process,
            "PropertyCondition": PropertyCondition,
            "ScrollAmount": ScrollAmount,
            "ScrollPattern": ScrollPattern,
            "TreeScope": TreeScope,
            "ValuePattern": ValuePattern,
        }

    @staticmethod
    def _is_edge_window(element: Any, process_type: Any) -> bool:
        try:
            current = element.Current
            if bool(current.IsOffscreen) or int(current.ProcessId) <= 0:
                return False
            process = process_type.GetProcessById(int(current.ProcessId))
            return str(process.ProcessName).casefold() == "msedge"
        except Exception:
            return False

    @classmethod
    def _verified_provider_url(cls, window: Any, api: dict[str, Any]) -> str:
        condition = api["PropertyCondition"](
            api["AutomationElement"].ControlTypeProperty,
            api["ControlType"].Edit,
        )
        try:
            edits = window.FindAll(api["TreeScope"].Descendants, condition)
        except Exception:
            return ""
        for element in _collection_values(edits):
            try:
                current = element.Current
                name = str(current.Name or "").strip().casefold()
                automation_id = str(current.AutomationId or "").strip().casefold()
                if name not in _ADDRESS_NAMES and "address" not in automation_id:
                    continue
                pattern = element.GetCurrentPattern(api["ValuePattern"].Pattern)
                raw = str(pattern.Current.Value or "").strip()
            except Exception:
                continue
            candidate = raw if "://" in raw else f"https://{raw}"
            try:
                parsed = urlsplit(candidate)
                port = parsed.port
            except ValueError:
                continue
            if (
                parsed.scheme.casefold() == "https"
                and (parsed.hostname or "").casefold() in cls.provider_hosts
                and parsed.username is None
                and parsed.password is None
                and port in {None, 443}
            ):
                return candidate
        return ""

    @classmethod
    def _verified_deepseek_url(cls, window: Any, api: dict[str, Any]) -> str:
        """Compatibility alias retained for the chat-archive DeepSeek contract."""
        return cls._verified_provider_url(window, api)

    @staticmethod
    def _window_title(window: Any) -> str:
        try:
            return str(window.Current.Name or "").strip()
        except Exception:
            return ""

    @classmethod
    def _candidate_seed(cls, url: str, window_title: str) -> tuple[str, str] | None:
        """Identify a visibly selected provider tab without reading browser storage."""
        if url:
            parsed = urlsplit(url)
            return (
                f"{parsed.hostname or ''}{parsed.path or '/'}",
                "VERIFIED_ADDRESS_BAR",
            )
        folded_title = window_title.casefold()
        if any(token in folded_title for token in cls.provider_title_tokens):
            return (f"{cls.provider_id}-visible-title", "VISIBLE_WINDOW_TITLE_FALLBACK")
        return None

    @staticmethod
    def _rect(element: Any) -> tuple[float, float, float, float] | None:
        try:
            current = element.Current
            if bool(current.IsOffscreen):
                return None
            rect = current.BoundingRectangle
            values = (float(rect.Left), float(rect.Top), float(rect.Width), float(rect.Height))
        except Exception:
            return None
        if not all(math.isfinite(value) for value in values) or values[2] < 2 or values[3] < 2:
            return None
        return values

    @staticmethod
    def _role_for_relative_geometry(relative_left: float, relative_right: float) -> str | None:
        width = relative_right - relative_left
        center = relative_left + (width / 2)
        if relative_left >= 0.50 and center >= 0.58 and width <= 0.48:
            return "user"
        if 0.18 <= relative_left <= 0.56 and center <= 0.72:
            return "assistant"
        return None

    @classmethod
    def _visible_text_nodes(cls, document: Any, api: dict[str, Any]) -> list[dict[str, Any]]:
        document_rect = cls._rect(document)
        if document_rect is None:
            return []
        left, top, width, height = document_rect
        condition = api["PropertyCondition"](
            api["AutomationElement"].ControlTypeProperty,
            api["ControlType"].Text,
        )
        try:
            elements = document.FindAll(api["TreeScope"].Descendants, condition)
        except Exception:
            return []
        nodes: list[dict[str, Any]] = []
        for element in _collection_values(elements):
            rect = cls._rect(element)
            if rect is None:
                continue
            node_left, node_top, node_width, node_height = rect
            relative_left = (node_left - left) / width
            relative_right = (node_left + node_width - left) / width
            relative_top = (node_top - top) / height
            relative_bottom = (node_top + node_height - top) / height
            if relative_right < 0.18 or relative_left > 0.98:
                continue
            if relative_bottom < 0.07 or relative_top > 0.88:
                continue
            try:
                text = _clean_message_text(str(element.Current.Name or ""))
            except Exception:
                continue
            if not text or text.casefold() in _NON_MESSAGE_LABELS:
                continue
            if len(text) < 2 and not text.isalnum():
                continue
            role = cls._role_for_relative_geometry(relative_left, relative_right)
            if role is None:
                continue
            nodes.append(
                {
                    "role": role,
                    "text": text,
                    "top": node_top,
                    "left": node_left,
                    "width": node_width,
                    "height": node_height,
                    "right": node_left + node_width,
                    "bottom": node_top + node_height,
                }
            )
        return cls._canonical_visual_nodes(nodes)

    @staticmethod
    def _canonical_visual_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep the largest message control when Chromium exposes rich children.

        UI Automation often emits both an entire message Text control and the
        heading/strong/list children inside it.  A nested child can be far to
        the right and therefore receive the opposite geometry role.  Retaining
        only the containing control prevents that child from becoming a fake
        turn while preserving repeated text in genuinely separate bubbles.
        """

        def area(node: dict[str, Any]) -> float:
            return max(0.0, float(node.get("width", 0.0))) * max(
                0.0, float(node.get("height", 0.0))
            )

        def contains(outer: dict[str, Any], inner: dict[str, Any]) -> bool:
            tolerance = 2.0
            return bool(
                float(outer.get("left", 0.0)) <= float(inner.get("left", 0.0)) + tolerance
                and float(outer.get("top", 0.0)) <= float(inner.get("top", 0.0)) + tolerance
                and float(outer.get("right", 0.0)) + tolerance >= float(inner.get("right", 0.0))
                and float(outer.get("bottom", 0.0)) + tolerance >= float(inner.get("bottom", 0.0))
            )

        ranked = sorted(
            nodes,
            key=lambda value: (
                -area(value),
                -len(str(value.get("text") or "")),
                float(value.get("top", 0.0)),
                float(value.get("left", 0.0)),
            ),
        )
        canonical: list[dict[str, Any]] = []
        for node in ranked:
            text = str(node.get("text") or "")
            if any(
                contains(existing, node)
                and text
                and text in str(existing.get("text") or "")
                for existing in canonical
            ):
                continue
            canonical.append(node)
        return sorted(canonical, key=lambda value: (value["top"], value["left"]))

    @staticmethod
    def _merge_turns(
        nodes: list[dict[str, Any]],
        *,
        require_both_roles: bool = True,
    ) -> list[dict[str, str]]:
        turns: list[dict[str, str]] = []
        for node in nodes:
            text = node["text"]
            role = node["role"]
            if turns and turns[-1]["role"] == role:
                existing = turns[-1]["text"]
                if text != existing and text not in existing.split("\n\n"):
                    turns[-1]["text"] = f"{existing}\n\n{text}"
                continue
            turns.append({"role": role, "text": text})
        if require_both_roles and (
            len(turns) < 2 or {turn["role"] for turn in turns} != {"user", "assistant"}
        ):
            raise VisibleUiCaptureError("DEEPSEEK_VISIBLE_TURN_BOUNDARY_UNRESOLVED")
        return turns

    @staticmethod
    def _turns_compatible(left: dict[str, str], right: dict[str, str]) -> bool:
        if left.get("role") != right.get("role"):
            return False
        left_text = str(left.get("text") or "")
        right_text = str(right.get("text") or "")
        return bool(
            left_text
            and right_text
            and (
                left_text == right_text
                or left_text in right_text
                or right_text in left_text
            )
        )

    @classmethod
    def _stitch_turn_pages(
        cls,
        pages: list[list[dict[str, str]]],
    ) -> list[dict[str, str]]:
        """Join top-to-bottom viewport captures while retaining every distinct turn."""
        merged: list[dict[str, str]] = []
        for raw_page in pages:
            page = [
                {"role": str(turn.get("role") or ""), "text": str(turn.get("text") or "")}
                for turn in raw_page
                if turn.get("role") in {"user", "assistant"} and str(turn.get("text") or "")
            ]
            if not page:
                continue
            overlap = 0
            limit = min(len(merged), len(page))
            for length in range(limit, 0, -1):
                if all(
                    cls._turns_compatible(left, right)
                    for left, right in zip(merged[-length:], page[:length], strict=True)
                ):
                    overlap = length
                    break
            if overlap:
                for index in range(overlap):
                    left_index = len(merged) - overlap + index
                    if len(page[index]["text"]) > len(merged[left_index]["text"]):
                        merged[left_index] = page[index]
            merged.extend(page[overlap:])
        return merged

    @classmethod
    def _scroll_target(cls, document: Any, api: dict[str, Any]) -> tuple[Any, Any] | None:
        candidates: list[Any] = [document]
        try:
            descendants = document.FindAll(
                api["TreeScope"].Descendants,
                api["Condition"].TrueCondition,
            )
            candidates.extend(_collection_values(descendants))
        except Exception:
            pass
        ranked: list[tuple[float, Any, Any]] = []
        for element in candidates:
            try:
                pattern = element.GetCurrentPattern(api["ScrollPattern"].Pattern)
                current = pattern.Current
                view_size = float(current.VerticalViewSize)
                vertically_scrollable = bool(current.VerticallyScrollable)
            except Exception:
                continue
            if not vertically_scrollable and view_size < 99.0:
                continue
            rect = cls._rect(element)
            area = rect[2] * rect[3] if rect else 0.0
            ranked.append((area, element, pattern))
        if not ranked:
            return None
        ranked.sort(key=lambda value: value[0], reverse=True)
        return ranked[0][1], ranked[0][2]

    @classmethod
    def _turn_pages_for_document(
        cls,
        document: Any,
        api: dict[str, Any],
    ) -> list[list[dict[str, str]]]:
        target = cls._scroll_target(document, api)
        if target is None:
            raise VisibleUiCaptureError("DEEPSEEK_SCROLL_BOUNDARY_UNAVAILABLE")
        scroll_element, scroll_pattern = target
        try:
            original = float(scroll_pattern.Current.VerticalScrollPercent)
            scrollable = bool(scroll_pattern.Current.VerticallyScrollable)
        except Exception as error:
            raise VisibleUiCaptureError("DEEPSEEK_SCROLL_STATE_UNAVAILABLE") from error
        pages: list[list[dict[str, str]]] = []
        restored = not scrollable
        try:
            if scrollable:
                scroll_pattern.SetScrollPercent(api["ScrollPattern"].NoScroll, 0.0)
                time.sleep(0.12)
            attempts = 0
            previous_percent = -1.0
            while True:
                nodes = cls._visible_text_nodes(document, api)
                turns = cls._merge_turns(nodes, require_both_roles=False)
                if turns:
                    pages.append(turns)
                try:
                    scroll_pattern = scroll_element.GetCurrentPattern(api["ScrollPattern"].Pattern)
                    current = scroll_pattern.Current
                    percent = float(current.VerticalScrollPercent)
                    view_size = float(current.VerticalViewSize)
                    scrollable = bool(current.VerticallyScrollable)
                except Exception as error:
                    raise VisibleUiCaptureError("DEEPSEEK_SCROLL_STATE_UNAVAILABLE") from error
                if not scrollable or percent < 0 or percent >= 99.5:
                    break
                attempts += 1
                if attempts > 2_048:
                    raise VisibleUiCaptureError("DEEPSEEK_FULL_SCROLL_STEP_LIMIT_EXCEEDED")
                step = max(1.0, min(80.0, view_size * 0.72))
                target_percent = min(100.0, percent + step)
                if target_percent <= percent + 0.01 or percent <= previous_percent + 0.01:
                    try:
                        scroll_pattern.Scroll(
                            api["ScrollAmount"].NoAmount,
                            api["ScrollAmount"].LargeIncrement,
                        )
                    except Exception as error:
                        raise VisibleUiCaptureError("DEEPSEEK_FULL_SCROLL_STALLED") from error
                else:
                    scroll_pattern.SetScrollPercent(
                        api["ScrollPattern"].NoScroll,
                        target_percent,
                    )
                previous_percent = percent
                time.sleep(0.12)
            if scrollable and percent < 99.5:
                raise VisibleUiCaptureError("DEEPSEEK_FULL_SCROLL_BOTTOM_NOT_REACHED")
        finally:
            if original >= 0:
                try:
                    scroll_pattern = scroll_element.GetCurrentPattern(api["ScrollPattern"].Pattern)
                    scroll_pattern.SetScrollPercent(api["ScrollPattern"].NoScroll, original)
                    time.sleep(0.08)
                    restored = True
                except Exception:
                    restored = False
        if not restored:
            raise VisibleUiCaptureError("DEEPSEEK_SCROLL_POSITION_RESTORE_FAILED")
        if not pages:
            raise VisibleUiCaptureError("DEEPSEEK_VISIBLE_TURN_BOUNDARY_UNRESOLVED")
        return pages

    @classmethod
    def _messages_for_document(
        cls,
        document: Any,
        api: dict[str, Any],
        conversation_seed: str,
    ) -> list[dict[str, str]]:
        turns = cls._stitch_turn_pages(cls._turn_pages_for_document(document, api))
        if len(turns) < 2 or {turn["role"] for turn in turns} != {"user", "assistant"}:
            raise VisibleUiCaptureError("DEEPSEEK_VISIBLE_TURN_BOUNDARY_UNRESOLVED")
        messages: list[dict[str, str]] = []
        for index, turn in enumerate(turns):
            identity = f"{conversation_seed}\x00{index}\x00{turn['role']}\x00{turn['text']}"
            message_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
            messages.append({"message_id": message_id, **turn})
        return messages

    def capture_visible_conversation(self) -> dict[str, Any]:
        api = self._automation()
        root = api["AutomationElement"].RootElement
        try:
            windows = root.FindAll(api["TreeScope"].Children, api["Condition"].TrueCondition)
            foreground = int(ctypes.windll.user32.GetForegroundWindow())
        except Exception as error:
            raise VisibleUiCaptureError("DEEPSEEK_UIA_DESKTOP_UNAVAILABLE") from error
        candidates: list[tuple[int, float, Any, str]] = []
        for window in _collection_values(windows):
            if not self._is_edge_window(window, api["Process"]):
                continue
            url = self._verified_provider_url(window, api)
            identity = self._candidate_seed(url, self._window_title(window))
            if identity is None:
                continue
            seed, _identity_method = identity
            rect = self._rect(window)
            if rect is None:
                continue
            try:
                handle = int(window.Current.NativeWindowHandle)
            except Exception:
                handle = 0
            candidates.append((int(handle == foreground), rect[2] * rect[3], window, seed))
        if not candidates:
            raise VisibleUiCaptureError("DEEPSEEK_VISIBLE_EDGE_WINDOW_NOT_FOUND")
        candidates.sort(key=lambda value: (value[0], value[1]), reverse=True)
        document_condition = api["PropertyCondition"](
            api["AutomationElement"].ControlTypeProperty,
            api["ControlType"].Document,
        )
        last_capture_error: VisibleUiCaptureError | None = None
        for _foreground, _area, window, seed in candidates:
            try:
                documents = window.FindAll(api["TreeScope"].Descendants, document_condition)
            except Exception:
                continue
            ranked = sorted(
                (document for document in _collection_values(documents) if self._rect(document) is not None),
                key=lambda document: (self._rect(document) or (0, 0, 0, 0))[2]
                * (self._rect(document) or (0, 0, 0, 0))[3],
                reverse=True,
            )
            for document in ranked:
                try:
                    messages = self._messages_for_document(document, api, seed)
                except VisibleUiCaptureError as error:
                    last_capture_error = error
                    continue
                first_user = next(value["text"] for value in messages if value["role"] == "user")
                conversation_id = hashlib.sha256(
                    f"{seed}\x00{first_user}".encode("utf-8")
                ).hexdigest()[:32]
                return {
                    "provider_profile": self.provider_profile,
                    "conversation_id": conversation_id,
                    "messages": messages,
                    "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                    "source_truncated": False,
                    "boundary_method": self.boundary_method,
                }
        if last_capture_error is not None:
            raise last_capture_error
        raise VisibleUiCaptureError("DEEPSEEK_VISIBLE_TURN_BOUNDARY_UNRESOLVED")


class EdgeKimiVisibleUIReader(EdgeDeepSeekVisibleUIReader):
    """Read the currently selected Kimi conversation through visible Edge UI only."""

    provider_id = "kimi"
    provider_profile = "KIMI_EDGE_VISIBLE_UI_V1"
    provider_hosts = frozenset({"www.kimi.com", "kimi.com", "kimi.moonshot.cn"})
    provider_title_tokens = ("kimi",)


class EdgeGeminiVisibleUIReader(EdgeDeepSeekVisibleUIReader):
    """Read the currently selected Gemini conversation through visible Edge UI only."""

    provider_id = "gemini"
    provider_profile = "GEMINI_EDGE_VISIBLE_UI_V1"
    provider_hosts = frozenset({"gemini.google.com"})
    provider_title_tokens = ("gemini",)


__all__ = [
    "EdgeDeepSeekVisibleUIReader",
    "EdgeGeminiVisibleUIReader",
    "EdgeKimiVisibleUIReader",
    "VisibleUiCaptureError",
]
