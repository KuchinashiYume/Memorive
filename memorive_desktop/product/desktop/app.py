from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
import queue
import random
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
from pathlib import Path
from typing import Any

from assistant_native_motion import (
    AUTO_BLINK_MAX_MS,
    AUTO_BLINK_MIN_MS,
    NATIVE_ANIMATION_FRAME_INTERVAL_MS,
    ASSISTANT_PETS_FINAL_SHA256,
    TERMINAL_ANIMATION_COALESCE_MS,
    auto_blink_completion_plan,
    base_frame_for_presentation,
    public_motion_contract,
    sample_motion,
    should_suppress_presentation_during_auto_blink,
)

from product_identity import BRAND, BINDING, sandbox_environment, select_private_test_state

SERVICE_MODE_ARG = "--desktop-service-mode"
WEBVIEW2_STORAGE_PATH_BUDGET = 96
EXPECTED_DESKTOP_ROUTES = (
    "settings", "inbox", "current-task", "messages", "sessions",
    "library", "work-log", "leaderboard", "chat",
)
_ASSISTANT_NATIVE_DEACTIVATE_HANDLERS: dict[int, Any] = {}
_ASSISTANT_NATIVE_OVERLAYS: dict[int, dict[str, Any]] = {}
_MAIN_WINDOW_BEHAVIORS: dict[int, dict[str, Any]] = {}
ASSISTANT_EXPRESSION_ASSET_FILENAMES = {
    "save-success": "assistant-save-success.png",
    "error": "assistant-error.png",
}


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)).resolve()


def assistant_pet_sheet_path(root: Path) -> Path:
    candidates = (
        root / "adopted_inline_001.png",
        root.parent / "web" / "assets" / "assistant" / "adopted_inline_001.png",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("assistant pet sprite sheet is missing")


def assistant_expression_asset_paths(root: Path) -> dict[str, Path]:
    """Resolve optional additive frames without weakening stable-pet startup."""

    resolved: dict[str, Path] = {}
    for frame_name, filename in ASSISTANT_EXPRESSION_ASSET_FILENAMES.items():
        candidates = (
            root / "assets" / "decorations" / filename,
            root.parent / "web" / "assets" / "decorations" / filename,
        )
        for candidate in candidates:
            if candidate.is_file():
                resolved[frame_name] = candidate.resolve()
                break
    return resolved


def configure_resource_imports(root: Path) -> None:
    candidates = (root / "source", root / "dependency_overlay")
    for candidate in reversed(candidates):
        if not candidate.is_dir():
            continue
        rendered = str(candidate)
        if rendered not in sys.path:
            sys.path.insert(0, rendered)


def run_embedded_service() -> int:
    root = resource_root()
    configure_resource_imports(root)
    forwarded = [argument for argument in sys.argv[1:] if argument != SERVICE_MODE_ARG]
    from memorive_desktop_service.service_main import main as service_main
    return int(service_main(forwarded))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _windows_short_existing_path(path: Path) -> Path:
    if os.name != "nt" or not path.is_dir():
        return path
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(
        str(path), buffer, len(buffer)
    )
    if 0 < length < len(buffer):
        return Path(buffer.value)
    return path


def webview_storage_selection(state_dir: Path) -> dict[str, Any]:
    """Select a stable isolated WebView2 profile with legacy-path headroom."""
    state_path = Path(state_dir).resolve()
    state_identity = hashlib.sha256(
        os.path.normcase(str(state_path)).encode("utf-8")
    ).hexdigest()[:20]
    configured = os.environ.get("MEMORIVE_WEBVIEW2_STORAGE_PATH")
    console_root_raw = os.environ.get("MEMORIVE_TEST_CONSOLE_DATA_ROOT")
    console_root: Path | None = None
    if console_root_raw:
        if configured:
            raise RuntimeError("CONSOLE_WEBVIEW_OVERRIDE_FORBIDDEN")
        console_root = Path(console_root_raw).resolve(strict=True)
        requested = (console_root / "webview2").resolve()
        strategy = "console_data_root"
    else:
        requested = (
            Path(configured).expanduser()
            if configured
            else state_path / "webview2-user-data"
        ).resolve()
        strategy = "configured_override" if configured else "state_local"
    selected = requested
    if (
        console_root is None
        and os.name == "nt"
        and len(str(requested)) > WEBVIEW2_STORAGE_PATH_BUDGET
    ):
        bases: set[Path] = set()
        for raw_base in (
            tempfile.gettempdir(),
            os.environ.get("TEMP"),
            os.environ.get("TMP"),
        ):
            if not raw_base:
                continue
            base = Path(raw_base).expanduser().resolve()
            bases.add(base)
            bases.add(_windows_short_existing_path(base))
        candidates = sorted(
            (base / "Memorive-WV2" / state_identity for base in bases),
            key=lambda path: (len(str(path)), os.path.normcase(str(path))),
        )
        if not candidates:
            raise RuntimeError("WEBVIEW2_SHORT_STORAGE_ROOT_UNAVAILABLE")
        selected = candidates[0]
        strategy = "short_temp_isolated"
    if strategy == "short_temp_isolated":
        # Preserve a valid 8.3 form returned by GetShortPathNameW. Calling
        # Path.resolve() here would expand it back to the unsafe long form.
        selected = Path(os.path.abspath(str(selected)))
    else:
        selected = selected.resolve()
    if (
        console_root is None
        and os.name == "nt"
        and len(str(selected)) > WEBVIEW2_STORAGE_PATH_BUDGET
    ):
        raise RuntimeError(
            "WEBVIEW2_STORAGE_PATH_EXCEEDS_SAFE_BUDGET: "
            f"selected_chars={len(str(selected))}, "
            f"budget={WEBVIEW2_STORAGE_PATH_BUDGET}"
        )
    selected.mkdir(parents=True, exist_ok=True)
    console_root_verified = False
    if console_root is not None:
        actual_storage = selected.resolve(strict=True)
        if actual_storage != console_root and console_root not in actual_storage.parents:
            raise RuntimeError("CONSOLE_WEBVIEW_STORAGE_OUTSIDE_DATA_ROOT")
        console_root_verified = True
    return {
        "schema_version": "DesktopWebView2StorageSelection-v1",
        "strategy": strategy,
        "storage_path": str(selected),
        "storage_path_chars": len(str(selected)),
        "requested_path_chars": len(str(requested)),
        "path_budget_chars": WEBVIEW2_STORAGE_PATH_BUDGET,
        "path_budget_enforced": console_root is None,
        "profile_identity": state_identity,
        "configured_override_present": bool(configured),
        "state_dir_unchanged": True,
        "console_data_root_verified": console_root_verified,
    }


def webview_storage_path(state_dir: Path) -> Path:
    return Path(webview_storage_selection(state_dir)["storage_path"])


def install_webview_diagnostics(
    evidence_dir: Path, selection: dict[str, Any]
) -> tuple[logging.Handler, Path]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    log_path = evidence_dir / f"webview_startup_{os.getpid()}.log"
    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    handler.setLevel(logging.INFO)
    logger_names = ("pywebview", "memorive.webview_startup")
    for logger_name in logger_names:
        logger = logging.getLogger(logger_name)
        logger.addHandler(handler)
        if logger.level == logging.NOTSET or logger.level > logging.INFO:
            logger.setLevel(logging.INFO)
    setattr(handler, "_memorive_logger_names", logger_names)
    logging.getLogger("memorive.webview_startup").info(
        "webview_storage_selected %s",
        json.dumps(selection, ensure_ascii=False, sort_keys=True),
    )
    return handler, log_path


def remove_webview_diagnostics(handler: logging.Handler) -> None:
    for logger_name in getattr(handler, "_memorive_logger_names", ()):
        logging.getLogger(logger_name).removeHandler(handler)
    handler.flush()
    handler.close()


def log_webview_startup_event(event: str, **details: Any) -> None:
    logging.getLogger("memorive.webview_startup").info(
        "%s %s",
        event,
        json.dumps(details, ensure_ascii=False, sort_keys=True),
    )


def ensure_child(root: Path, target: Path, label: str) -> None:
    try:
        target.relative_to(root)
    except ValueError as error:
        raise RuntimeError(f"{label} must be inside its authorized root: {target}") from error
    if target == root:
        raise RuntimeError(f"{label} may not equal its authorized root")


def write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def evaluate_promise(window: webview.Window, script: str, timeout: float = 30.0) -> Any:
    completed = threading.Event()
    outcome: dict[str, Any] = {}
    def callback(value: Any) -> None:
        outcome["value"] = value
        completed.set()
    window.evaluate_js(script, callback=callback)
    if not completed.wait(timeout):
        raise TimeoutError("pywebview promise callback timed out")
    return outcome.get("value")


def extract_inbox_drop_paths(event: Any) -> list[str]:
    if not isinstance(event, dict):
        raise RuntimeError("INBOX_DROP_LOCATOR_MISSING")
    transfer = event.get("dataTransfer")
    files = transfer.get("files") if isinstance(transfer, dict) else None
    if not isinstance(files, list) or not files:
        raise RuntimeError("INBOX_DROP_LOCATOR_MISSING")
    if len(files) > 32:
        raise RuntimeError("INBOX_DROP_TOO_MANY_FILES")
    paths: list[str] = []
    seen: set[str] = set()
    for file_projection in files:
        locator = file_projection.get("pywebviewFullPath") if isinstance(file_projection, dict) else None
        if not isinstance(locator, str) or not locator.strip() or "\x00" in locator:
            raise RuntimeError("INBOX_DROP_LOCATOR_MISSING")
        rendered = locator.strip()
        if not Path(rendered).is_absolute():
            raise RuntimeError("INBOX_DROP_LOCATOR_MISSING")
        identity = os.path.normcase(os.path.normpath(rendered))
        if identity not in seen:
            seen.add(identity)
            paths.append(rendered)
    if not paths:
        raise RuntimeError("INBOX_DROP_LOCATOR_MISSING")
    return paths


def process_inbox_drop_event(event: Any, api: ProductApi, window: webview.Window) -> dict[str, Any]:
    paths = extract_inbox_drop_paths(event)
    try:
        staged = api.stage_inbox_paths(paths)
    except Exception as error:
        raise RuntimeError("INBOX_DROP_STAGE_REJECTED") from error
    try:
        imported = evaluate_promise(
            window,
            "window.__Desktop_INBOX_BRIDGE__.acceptNativeDrop("
            + json.dumps(staged["paths"], ensure_ascii=False)
            + ")",
            timeout=60.0,
        )
    except Exception as error:
        raise RuntimeError("INBOX_DROP_IMPORT_FAILED") from error
    items = imported.get("items", []) if isinstance(imported, dict) else []
    item_ids = [
        row.get("item", {}).get("item_id")
        for row in items
        if isinstance(row, dict) and isinstance(row.get("item"), dict)
        and isinstance(row["item"].get("item_id"), str)
    ]
    return {
        "schema_version": "DesktopInboxNativeDropReceipt-v1",
        "status": "PASS",
        "source_file_count": len(paths),
        "staged_file_count": len(staged.get("paths", [])),
        "imported_item_count": len(item_ids),
        "item_ids": item_ids,
        "source_kind": "drop",
        "source_files_moved_or_deleted": int(imported.get("source_files_moved_or_deleted", -1)),
    }


def attach_inbox_drop_handler(window: webview.Window, api: ProductApi,
                              runtime: dict[str, Any]) -> None:
    try:
        # Bind the native full-path drop event to the whole product workspace.
        # A narrower body-only target lets WebView2 navigate to a dropped file
        # when it lands on the sidebar, header, inspector, or another gap.
        element = window.dom.get_element("#desktop-inbox-preview")
        if element is None:
            raise RuntimeError("INBOX_DROP_TARGET_NOT_FOUND")

        def on_drop(event: Any) -> dict[str, Any]:
            with runtime["lock"]:
                runtime["attempt_count"] += 1
                try:
                    receipt = process_inbox_drop_event(event, api, window)
                    runtime["success_count"] += 1
                    runtime["last_receipt"] = receipt
                    runtime["last_error"] = None
                    return receipt
                except Exception as error:
                    reason_code = str(error)
                    if reason_code not in {
                        "INBOX_DROP_LOCATOR_MISSING", "INBOX_DROP_TOO_MANY_FILES",
                        "INBOX_DROP_STAGE_REJECTED", "INBOX_DROP_IMPORT_FAILED",
                    }:
                        reason_code = "INBOX_DROP_IMPORT_FAILED"
                    runtime["failure_count"] += 1
                    runtime["last_error"] = reason_code
                    window.evaluate_js(
                        "window.__Desktop_INBOX_BRIDGE__.reportNativeDropFailure("
                        + json.dumps(reason_code)
                        + ")"
                    )
                    raise RuntimeError(reason_code) from error

        research_element = window.dom.get_element("#desktop-partchat-preview")
        if research_element is not None:
            def on_research_drop(event):
                try:
                    paths = extract_inbox_drop_paths(event)
                    staged = api.stage_inbox_paths(paths)
                    return evaluate_promise(window, "window.__MEMO_WORKSPACE__.importFiles(" + json.dumps(staged["paths"],ensure_ascii=False) + ")",timeout=30)
                except Exception as error:
                    window.evaluate_js("window.__MEMO_WORKSPACE__?.reportImportFailure(" + json.dumps(str(error)[:120]) + ")")
                    return {"status":"ERROR","reason":str(error)[:120]}
            research_element.on("drop",on_research_drop)
            runtime["research_element"]=research_element
            runtime["research_callback"]=on_research_drop
            runtime["research_attached"]=True

        element.on("drop", on_drop)
        runtime["element"] = element
        runtime["callback"] = on_drop
        runtime["attached"] = True
        runtime["attach_error"] = None
    except Exception as error:
        runtime["attached"] = False
        runtime["attach_error"] = type(error).__name__
    finally:
        runtime["ready"].set()


def browser_version(window: webview.Window) -> str:
    import clr
    clr.AddReference("System.Windows.Forms")
    from System import Action
    holder: dict[str, str] = {}
    form = window.native
    def read() -> None:
        holder["value"] = str(form.webview.CoreWebView2.Environment.BrowserVersionString)
    form.Invoke(Action(read))
    return holder.get("value", "")


def capture_window(window: webview.Window, destination: Path) -> dict[str, Any]:
    import clr
    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.IO")
    from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
    from System import Action
    from System.IO import MemoryStream

    form = window.native
    holder: dict[str, Any] = {}

    def start() -> None:
        stream = MemoryStream()
        holder["stream"] = stream
        holder["task"] = form.webview.CoreWebView2.CapturePreviewAsync(
            CoreWebView2CapturePreviewImageFormat.Png, stream
        )

    form.Invoke(Action(start))
    task = holder["task"]
    if not task.Wait(15000):
        raise TimeoutError("WebView2 CapturePreviewAsync timed out")
    data = bytes(holder["stream"].ToArray())
    with destination.open("xb") as handle:
        handle.write(data)
    return {"path": str(destination), "bytes": destination.stat().st_size, "sha256": sha256_file(destination)}


def packaged_provider_visual_checks(inspection: dict[str, Any]) -> dict[str, bool]:
    card_count = int(inspection["card_count"])
    turn_count = int(inspection["turn_count"])
    return {
        "route_exact": inspection["route"] == "sessions",
        "provider_conversation_count_bounded": 1 <= card_count <= 20,
        "provider_filter_exact": inspection["all_cards_match_provider"] is True,
        "provider_mark_exact": inspection["source_marks_exact"] is True,
        "stable_titles_visible": (
            len(inspection["card_title_lengths"]) == card_count
            and all(int(value) > 0 for value in inspection["card_title_lengths"])
        ),
        "provider_times_visible": (
            int(inspection["card_time_nonempty_count"]) == card_count
        ),
        "complete_user_and_assistant_body": (
            turn_count >= 2
            and int(inspection["user_turn_count"]) >= 1
            and int(inspection["assistant_turn_count"]) >= 1
            and len(inspection["turn_roles"]) == turn_count
            and len(inspection["turn_text_lengths"]) == turn_count
            and all(int(value) > 0 for value in inspection["turn_text_lengths"])
        ),
        "detail_title_visible": int(inspection["detail_title_chars"]) > 0,
        "detail_source_visible": inspection["detail_source_present"] is True,
        "detail_time_visible": inspection["detail_time_present"] is True,
        "list_detail_ratio_40_60": 0.38 <= inspection["list_ratio"] <= 0.42,
        "no_page_horizontal_overflow": inspection["horizontal_overflow"] is False,
    }


def capture_packaged_visual_qa(
    window: webview.Window,
    evidence_dir: Path,
    phase: str,
    evidence_prefix: str = "packaged_visual",
) -> dict[str, Any]:
    """Capture provider and settings layouts from the actual packaged WebView.

    This test-only path is enabled by an explicit isolated import directory. It
    reads only synthetic/offline bundle files already placed in that directory.
    """
    import_root = os.environ.get("MEMORIVE_BROWSER_SESSION_IMPORT_DIR")
    if not import_root:
        return {
            "enabled": False,
            "provider_results": {},
            "settings": None,
            "status": "NOT_RUN_NO_ISOLATED_IMPORT_DIR",
        }

    provider_results: dict[str, Any] = {}
    expected_marks = {"deepseek": "DS", "gemini": "GM", "kimi": "KM"}
    for provider in ("deepseek", "gemini", "kimi"):
        provider_json = json.dumps(provider)
        expected_mark_json = json.dumps(expected_marks[provider])
        inspection = evaluate_promise(
            window,
            f"""
            (async () => {{
              const provider = {provider_json};
              const expectedMark = {expected_mark_json};
              const app = window.__Desktop_INTEGRATED_APP__;
              const bridge = window.__Desktop_SESSIONS_BRIDGE__;
              app.activate('sessions');
              await bridge.query({{source:provider, search:'', limit_per_source:20}});
              const root = document.querySelector('[data-desktop-route="sessions"] #desktop-sessions-preview');
              const started = Date.now();
              const waitUntil = async (predicate, code) => {{
                while (!predicate()) {{
                  if (Date.now() - started > 12000) throw new Error(code);
                  await new Promise((resolve) => setTimeout(resolve, 80));
                }}
              }};
              await waitUntil(() => root && root.querySelectorAll('.desktop-sessions-card').length > 0, 'VISUAL_PROVIDER_LIST_TIMEOUT');
              const cards = [...root.querySelectorAll('.desktop-sessions-card')];
              const exactCards = cards.filter((card) => card.dataset.source === provider);
              if (!exactCards.length) throw new Error('VISUAL_PROVIDER_CARD_MISSING:' + provider);
              exactCards[0].click();
              await waitUntil(
                () => root.querySelectorAll('#sessions-turns .desktop-sessions-turn').length >= 2,
                'VISUAL_PROVIDER_DETAIL_TIMEOUT:' + provider
              );
              await new Promise((resolve) => setTimeout(resolve, 220));
              const turns = [...root.querySelectorAll('#sessions-turns .desktop-sessions-turn')];
              const stage = root.querySelector('#sessions-stage').getBoundingClientRect();
              const list = root.querySelector('#sessions-listpane').getBoundingClientRect();
              const detail = root.querySelector('#sessions-detail').getBoundingClientRect();
              return {{
                provider,
                route: document.body.dataset.desktopActiveRoute,
                card_count: exactCards.length,
                all_cards_match_provider: exactCards.length === cards.length,
                card_title_lengths: exactCards.map((card) => (card.dataset.title || '').trim().length),
                card_time_nonempty_count: exactCards.filter((card) => (card.querySelector('time')?.textContent || '').trim().length > 0).length,
                source_marks_exact: exactCards.every((card) => (card.querySelector('.desktop-sessions-source')?.textContent || '').trim() === expectedMark),
                detail_title_chars: (root.querySelector('#sessions-detail-title')?.textContent || '').trim().length,
                detail_source_present: (root.querySelector('#sessions-detail-source')?.textContent || '').trim().length > 0,
                detail_time_present: (root.querySelector('#sessions-detail-time')?.textContent || '').trim().length > 0,
                turn_count: turns.length,
                user_turn_count: turns.filter((turn) => turn.classList.contains('user')).length,
                assistant_turn_count: turns.filter((turn) => turn.classList.contains('assistant')).length,
                turn_roles: turns.map((turn) => turn.classList.contains('user') ? 'user' : (turn.classList.contains('assistant') ? 'assistant' : 'unknown')),
                turn_text_lengths: turns.map((turn) => (turn.querySelector('p')?.textContent || '').trim().length),
                list_ratio: list.width / (list.width + detail.width),
                left_gutter: list.left - stage.left,
                horizontal_overflow: root.scrollWidth > root.clientWidth + 1,
              }};
            }})()
            """,
            timeout=20.0,
        )
        screenshot_path = evidence_dir / f"{evidence_prefix}_{provider}_{phase}.png"
        screenshot = capture_window(window, screenshot_path)
        checks = packaged_provider_visual_checks(inspection)
        provider_results[provider] = {
            "inspection": inspection,
            "screenshot": screenshot,
            "checks": checks,
            "status": "PASS" if all(checks.values()) else "FAIL",
        }

    settings_inspection = evaluate_promise(
        window,
        r"""
        (async () => {
          const app = window.__Desktop_INTEGRATED_APP__;
          app.activate('settings');
          const root = document.querySelector('[data-desktop-route="settings"] #desktop-settings-preview');
          const category = root.querySelector('.desktop-settings-category[data-category="local-models"]');
          const title = category.querySelector('strong');
          const before = title.getBoundingClientRect();
          category.focus({preventScroll:true});
          await new Promise((resolve) => setTimeout(resolve, 180));
          const after = title.getBoundingClientRect();
          category.click();
          const started = Date.now();
          while (root.querySelector('[data-panel="local-models"]').hidden) {
            if (Date.now() - started > 8000) throw new Error('VISUAL_LOCAL_MODEL_PANEL_TIMEOUT');
            await new Promise((resolve) => setTimeout(resolve, 80));
          }
          const disclosure = root.querySelector('.desktop-settings-local-model-advanced');
          if (!disclosure.open) disclosure.querySelector('summary').click();
          await new Promise((resolve) => setTimeout(resolve, 220));
          const name = root.querySelector('#settings-local-model-name').getBoundingClientRect();
          const nativeSelect = root.querySelector('#settings-local-model-kind');
          const selectShell = nativeSelect.parentElement.querySelector('.desktop-settings-select-shell');
          const selectRect = (selectShell || nativeSelect).getBoundingClientRect();
          const endpoint = root.querySelector('#settings-local-model-endpoint').getBoundingClientRect();
          return {
            route: document.body.dataset.desktopActiveRoute,
            title_delta_x: after.left - before.left,
            title_delta_y: after.top - before.top,
            category_height: category.getBoundingClientRect().height,
            category_tooltip: category.dataset.desktopTip || '',
            category_tooltip_count: root.querySelectorAll('.desktop-settings-category[data-desktop-tip]').length,
            helper_copy: root.querySelector('[data-panel="local-models"] .desktop-settings-helper')?.textContent?.trim() || '',
            protocol_tip: root.querySelector('#settings-local-model-protocol-tip')?.textContent?.trim() || '',
            name_height: name.height,
            select_height: selectRect.height,
            endpoint_height: endpoint.height,
            panel_horizontal_overflow: root.querySelector('[data-panel="local-models"]').scrollWidth > root.querySelector('[data-panel="local-models"]').clientWidth + 1,
          };
        })()
        """,
        timeout=15.0,
    )
    settings_screenshot_path = evidence_dir / f"{evidence_prefix}_local_models_{phase}.png"
    settings_screenshot = capture_window(window, settings_screenshot_path)
    settings_checks = {
        "route_exact": settings_inspection["route"] == "settings",
        "hover_title_fixed": (
            abs(float(settings_inspection["title_delta_x"])) < 0.1
            and abs(float(settings_inspection["title_delta_y"])) < 0.1
        ),
        "category_height_readable": float(settings_inspection["category_height"]) >= 56.0,
        "category_black_tooltips_removed": (
            settings_inspection["category_tooltip_count"] == 0
            and not settings_inspection["category_tooltip"]
        ),
        "two_protocol_families_explained": "两类模型列表协议" in settings_inspection["protocol_tip"],
        "three_fields_aligned": (
            max(
                float(settings_inspection["name_height"]),
                float(settings_inspection["select_height"]),
                float(settings_inspection["endpoint_height"]),
            )
            - min(
                float(settings_inspection["name_height"]),
                float(settings_inspection["select_height"]),
                float(settings_inspection["endpoint_height"]),
            )
            <= 1.0
        ),
        "no_panel_horizontal_overflow": settings_inspection["panel_horizontal_overflow"] is False,
    }
    settings_result = {
        "inspection": settings_inspection,
        "screenshot": settings_screenshot,
        "checks": settings_checks,
        "status": "PASS" if all(settings_checks.values()) else "FAIL",
    }
    statuses = [row["status"] for row in provider_results.values()] + [settings_result["status"]]
    return {
        "enabled": True,
        "import_root_name": Path(import_root).name,
        "provider_results": provider_results,
        "settings": settings_result,
        "status": "PASS" if all(value == "PASS" for value in statuses) else "FAIL",
    }


def capture_visual_qa_compatibility(
    window: webview.Window, evidence_dir: Path, phase: str
) -> dict[str, Any]:
    """Compatibility wrapper for frozen visual-check test callers."""
    return capture_packaged_visual_qa(
        window,
        evidence_dir,
        phase,
        evidence_prefix="visual-check_visual",
    )


def configure_assistant_native(
    window: webview.Window,
    api: ProductApi,
    pet_sheet: Path,
    *,
    reveal: bool,
    expression_assets: dict[str, Path] | None = None,
) -> None:
    import clr
    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    from System import Action
    from System.Drawing import Bitmap, Color, Graphics, GraphicsUnit, Point, Rectangle, Region, Size
    from System.Drawing.Drawing2D import InterpolationMode, PixelOffsetMode, SmoothingMode
    from System.Drawing.Imaging import ColorMatrix, ImageAttributes, ImageFormat, PixelFormat
    from System.IO import MemoryStream
    from System.Windows.Forms import (
        ContextMenuStrip, Cursor, Cursors, MouseButtons, PictureBox,
        PictureBoxSizeMode, Timer as FormsTimer, ToolStripDropDownCloseReason,
        ToolStripMenuItem, ToolStripSeparator,
    )

    form = window.native

    def configure() -> None:
        # Keep antialiased edge coverage through desktop composition. A colour
        # key flattens partial alpha against its matte and creates light fringes.
        from assistant_alpha import LayeredPetSurface
        form.AllowTransparency = True
        form.BackColor = Color.FromArgb(240, 240, 240)
        form.TransparencyKey = Color.Empty
        form.webview.DefaultBackgroundColor = Color.Transparent
        # The WebView is only a pywebview bootstrap carrier. Leaving its child
        # HWND below a moving transparent WinForms surface forces two native
        # composition trees to repaint each other and can expose a white host
        # rectangle or stall the main WebView. The pet is rendered exclusively
        # by the bounded native PictureBox after setup.
        form.webview.Visible = False
        form.webview.Enabled = False
        form.Opacity = 1.0
        form.ShowInTaskbar = False
        form.TopMost = True
        form_handle = int(form.Handle.ToInt64())
        existing = _ASSISTANT_NATIVE_OVERLAYS.get(form_handle)

        def apply_pet_window_region(overlay: dict[str, Any]) -> None:
            """Shape the HWND to the union of the visible sprite frames.

            The top-level native window is already pet-sized. This additional
            region removes its transparent corners from Windows hit testing and
            window selection instead of exposing a rectangular invisible box.
            """
            client_width = max(1, int(form.ClientSize.Width))
            client_height = max(1, int(form.ClientSize.Height))
            from assistant_alpha import pet_canvas_bounds
            canvas_x, canvas_y_offset, canvas_size = pet_canvas_bounds(client_width, client_height)
            mask_runs = tuple(overlay.get("input_mask_runs") or ())
            region = Region()
            region.MakeEmpty()
            if mask_runs:
                for canvas_y, canvas_left, canvas_right in mask_runs:
                    left = canvas_x + int(canvas_left) * canvas_size // 224
                    right = canvas_x + (int(canvas_right) * canvas_size + 223) // 224
                    top = canvas_y_offset + int(canvas_y) * canvas_size // 224
                    bottom = canvas_y_offset + ((int(canvas_y) + 1) * canvas_size + 223) // 224
                    if right > left and bottom > top:
                        region.Union(Rectangle(left, top, right - left, bottom - top))
            else:
                region.Union(Rectangle(canvas_x, canvas_y_offset, canvas_size, canvas_size))
            previous = overlay.get("window_region")
            form.Region = region
            overlay["window_region"] = region
            overlay["window_region_client_size"] = (client_width, client_height)
            if previous is not None:
                previous.Dispose()

        def place_overlay(picture: Any, overlay: dict[str, Any] | None = None) -> None:
            from assistant_alpha import pet_canvas_bounds
            left, top, side = pet_canvas_bounds(form.ClientSize.Width, form.ClientSize.Height)
            picture.Location = Point(left, top)
            picture.Size = Size(side, side)
            if overlay is not None:
                overlay["client_scale"] = (
                    float(side) / 90.0,
                    float(side) / 90.0,
                )
                apply_pet_window_region(overlay)
            picture.BringToFront()
            if overlay is not None and overlay.get("rendered_image") is not None:
                overlay["alpha_surface"].present(overlay["rendered_image"])

        if existing is None:
            source = Bitmap(str(pet_sheet))
            try:
                frames = {
                    "idle": source.Clone(Rectangle(0, 0, 192, 192), PixelFormat.Format32bppArgb),
                    "blink-open": source.Clone(Rectangle(192, 0, 192, 192), PixelFormat.Format32bppArgb),
                    "blink-closed": source.Clone(Rectangle(384, 0, 192, 192), PixelFormat.Format32bppArgb),
                    "interaction": source.Clone(Rectangle(0, 192, 192, 192), PixelFormat.Format32bppArgb),
                    "working": source.Clone(Rectangle(192, 192, 192, 192), PixelFormat.Format32bppArgb),
                    "success": source.Clone(Rectangle(384, 192, 192, 192), PixelFormat.Format32bppArgb),
                }
            finally:
                source.Dispose()

            for frame_name, asset_path in (expression_assets or {}).items():
                if frame_name not in ASSISTANT_EXPRESSION_ASSET_FILENAMES:
                    continue
                expression_frame = None
                try:
                    expression_source = Bitmap(str(asset_path))
                    expression_frame = Bitmap(
                        192, 192, PixelFormat.Format32bppArgb
                    )
                    expression_graphics = Graphics.FromImage(expression_frame)
                    try:
                        expression_graphics.Clear(Color.Transparent)
                        expression_graphics.SmoothingMode = SmoothingMode.HighQuality
                        expression_graphics.InterpolationMode = (
                            InterpolationMode.HighQualityBicubic
                        )
                        expression_graphics.PixelOffsetMode = PixelOffsetMode.HighQuality
                        expression_graphics.DrawImage(
                            expression_source,
                            Rectangle(0, 0, 192, 192),
                            0,
                            0,
                            int(expression_source.Width),
                            int(expression_source.Height),
                            GraphicsUnit.Pixel,
                        )
                    finally:
                        expression_graphics.Dispose()
                        expression_source.Dispose()
                    frames[frame_name] = expression_frame
                except Exception:
                    # Decorations are additive. Missing/corrupt optional files
                    # must fall back to the six adopted frames, never block boot.
                    if expression_frame is not None:
                        try:
                            expression_frame.Dispose()
                        except Exception:
                            pass
                    continue

            def frame_sha256(frame: Any) -> str:
                stream = MemoryStream()
                try:
                    frame.Save(stream, ImageFormat.Png)
                    return hashlib.sha256(bytes(stream.ToArray())).hexdigest().upper()
                finally:
                    stream.Dispose()

            frame_sha256s = {
                name: frame_sha256(frame) for name, frame in frames.items()
            }

            def build_input_mask_runs() -> tuple[tuple[int, int, int], ...]:
                from assistant_alpha import bitmap_alpha_rows
                alpha_planes = tuple(bitmap_alpha_rows(frame) for frame in frames.values())
                # Rendered frames occupy 16..208 in the 224px transform canvas.
                # Union every authoritative Assistant frame and add a small motion
                # allowance so rotations, blink and status animations are not
                # clipped while the transparent corners stay outside the HWND.
                source_rows: list[tuple[int, int] | None] = [None] * 192
                for source_y in range(192):
                    minimum_x = 192
                    maximum_x = -1
                    for source_x in range(192):
                        if any(
                            plane[source_y][source_x] > 4
                            for plane in alpha_planes
                        ):
                            minimum_x = min(minimum_x, source_x)
                            maximum_x = max(maximum_x, source_x)
                    if maximum_x >= minimum_x:
                        source_rows[source_y] = (minimum_x, maximum_x + 1)
                padding = 12
                expanded: list[tuple[int, int] | None] = [None] * 224
                for source_y, bounds in enumerate(source_rows):
                    if bounds is None:
                        continue
                    canvas_y = source_y + 16
                    left = max(0, bounds[0] + 16 - padding)
                    right = min(224, bounds[1] + 16 + padding)
                    for target_y in range(
                        max(0, canvas_y - padding),
                        min(224, canvas_y + padding + 1),
                    ):
                        current = expanded[target_y]
                        expanded[target_y] = (
                            (left, right)
                            if current is None
                            else (min(current[0], left), max(current[1], right))
                        )
                return tuple(
                    (row, bounds[0], bounds[1])
                    for row, bounds in enumerate(expanded)
                    if bounds is not None
                )

            input_mask_runs = build_input_mask_runs()

            picture = PictureBox()
            picture.Name = "DesktopNativePetHitSurface"
            picture.BackColor = Color.Transparent
            picture.Image = frames["idle"]
            # Startup and monitor-DPI transitions can temporarily give the
            # host unequal dimensions. Never distort the square sprite canvas.
            picture.SizeMode = PictureBoxSizeMode.Zoom
            picture.Cursor = Cursors.Hand
            picture.TabStop = False

            menu = ContextMenuStrip()
            menu.Name = "DesktopNativePetContextMenu"
            menu.AutoClose = True
            menu.AutoSize = True
            menu.ShowCheckMargin = False
            menu.ShowImageMargin = False
            menu.MaximumSize = Size(320, 0)

            state: dict[str, Any] = {
                "api": api,
                "alpha_surface": LayeredPetSurface(form),
                "picture": picture,
                "frames": frames,
                "menu": menu,
                "handlers": [],
                "menu_open_count": 0,
                "menu_close_count": 0,
                "menu_close_reasons": [],
                "primary_activation_count": 0,
                "last_primary_status": None,
                "last_primary_receipt": None,
                "last_action": None,
                "last_action_status": None,
                "drag": None,
                "drag_move_count": 0,
                "last_drag_target": None,
                "base_frame": "idle",
                "animation_state": "STATIC",
                "animation_sequence": ["idle"],
                "animation_started_at": 0.0,
                "animation_locked": False,
                "animation_source": None,
                "reduced_motion": False,
                "current_frame": "idle",
                "current_frame_sha256": frame_sha256s["idle"],
                "frame_sha256s": frame_sha256s,
                "current_transform": None,
                "rendered_image": None,
                "render_cache": {},
                "render_cache_limit": 256,
                "input_mask_runs": input_mask_runs,
                "window_region": None,
                "window_region_client_size": None,
                "interaction_animation_count": 0,
                "animation_completed_count": 0,
                "auto_blink_count": 0,
                "auto_blink_due_ms": None,
                "auto_blink_preserved_count": 0,
                "auto_blink_restore_frame": None,
                "auto_blink_discarded_queue_count": 0,
                "last_presentation_signature": None,
                "presentation_apply_count": 0,
                "presentation_reason_code": "NO_ACTIVE_REAL_JOB",
                "active_presentation_receipt": None,
                "active_presentation_signature": None,
                "presentation_queue": [],
                "queued_presentation_count": 0,
                "animation_tick_index": 0,
                "last_success_animation_at": 0.0,
                "terminal_animation_started_count": 0,
                "coalesced_terminal_count": 0,
                "interrupted_presentation_count": 0,
                "receipt_record_error_count": 0,
            }

            def presentation_frame_name(
                motion_name: str, sampled_frame: str, *, done: bool = False
            ) -> str:
                if done:
                    return sampled_frame
                normalized = str(motion_name).upper()
                if normalized == "ERROR" and "error" in state["frames"]:
                    return "error"
                if (
                    normalized == "SUCCESS"
                    and state.get("presentation_reason_code")
                    == "LOCAL_UI_SAVE_SUCCESS"
                    and "save-success" in state["frames"]
                ):
                    return "save-success"
                return sampled_frame

            receipt_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()

            def record_presentation_receipts() -> None:
                while True:
                    payload = receipt_queue.get()
                    try:
                        if payload is None:
                            return
                        api.record_assistant_presentation_receipt(payload)
                    except Exception:
                        state["receipt_record_error_count"] += 1
                    finally:
                        receipt_queue.task_done()

            receipt_worker = threading.Thread(
                target=record_presentation_receipts,
                name="memorive-assistant-receipt-writer",
                daemon=True,
            )
            receipt_worker.start()
            state["receipt_queue"] = receipt_queue
            state["receipt_worker"] = receipt_worker
            state["enqueue_presentation_receipt"] = (
                lambda payload: receipt_queue.put(dict(payload))
            )

            animation_timer = FormsTimer()
            animation_timer.Interval = NATIVE_ANIMATION_FRAME_INTERVAL_MS
            auto_blink_timer = FormsTimer()
            drag_timer = FormsTimer()
            drag_timer.Interval = 16

            def render_frame(
                frame_name: str,
                offset_x: float = 0.0,
                offset_y: float = 0.0,
                rotation_degrees: float = 0.0,
                scale: float = 1.0,
                opacity: float = 1.0,
            ) -> Any:
                source_frame = state["frames"].get(frame_name, state["frames"]["idle"])
                canvas = Bitmap(224, 224, PixelFormat.Format32bppArgb)
                graphics = Graphics.FromImage(canvas)
                attributes = ImageAttributes()
                try:
                    graphics.Clear(Color.Transparent)
                    graphics.SmoothingMode = SmoothingMode.HighQuality
                    graphics.InterpolationMode = InterpolationMode.HighQualityBicubic
                    graphics.PixelOffsetMode = PixelOffsetMode.HighQuality
                    graphics.TranslateTransform(
                        112.0 + float(offset_x) * 224.0 / 90.0,
                        112.0 + float(offset_y) * 224.0 / 90.0,
                    )
                    graphics.RotateTransform(float(rotation_degrees))
                    graphics.ScaleTransform(float(scale), float(scale))
                    matrix = ColorMatrix()
                    matrix.Matrix00 = 1.0
                    matrix.Matrix11 = 1.0
                    matrix.Matrix22 = 1.0
                    matrix.Matrix33 = max(0.0, min(1.0, float(opacity)))
                    matrix.Matrix44 = 1.0
                    if frame_name in {"save-success", "error"}:
                        # A light white veil softens the additive expressions.
                        # Preserve alpha so the native pet keeps transparent edges.
                        matrix.Matrix00 = 0.82
                        matrix.Matrix11 = 0.82
                        matrix.Matrix22 = 0.82
                        matrix.Matrix40 = 0.18
                        matrix.Matrix41 = 0.18
                        matrix.Matrix42 = 0.18
                    attributes.SetColorMatrix(matrix)
                    graphics.DrawImage(
                        source_frame,
                        Rectangle(-96, -96, 192, 192),
                        0,
                        0,
                        192,
                        192,
                        GraphicsUnit.Pixel,
                        attributes,
                    )
                finally:
                    attributes.Dispose()
                    graphics.Dispose()
                return canvas

            def set_visual(
                frame_name: str,
                *,
                offset_x: float = 0.0,
                offset_y: float = 0.0,
                rotation_degrees: float = 0.0,
                scale: float = 1.0,
                opacity: float = 1.0,
            ) -> None:
                resolved = frame_name if frame_name in state["frames"] else "idle"
                signature = (
                    resolved,
                    round(float(offset_x), 2),
                    round(float(offset_y), 2),
                    round(float(rotation_degrees), 2),
                    round(float(scale), 3),
                    round(float(opacity), 2),
                )
                if state.get("current_transform") != signature:
                    render_cache = state["render_cache"]
                    rendered = render_cache.get(signature)
                    if rendered is None:
                        rendered = render_frame(
                            resolved,
                            offset_x,
                            offset_y,
                            rotation_degrees,
                            scale,
                            opacity,
                        )
                        if len(render_cache) >= int(state["render_cache_limit"]):
                            stale_key = next(
                                (
                                    key
                                    for key in render_cache
                                    if key != state.get("current_transform")
                                ),
                                None,
                            )
                            if stale_key is not None:
                                stale = render_cache.pop(stale_key)
                                try:
                                    stale.Dispose()
                                except Exception:
                                    pass
                        render_cache[signature] = rendered
                    picture.Image = rendered
                    state["rendered_image"] = rendered
                    state["current_transform"] = signature
                    state["alpha_surface"].present(rendered)
                state["current_frame"] = resolved
                state["current_frame_sha256"] = state["frame_sha256s"][resolved]

            def schedule_auto_blink(*, preserve_existing: bool = False) -> None:
                if (
                    preserve_existing
                    and bool(auto_blink_timer.Enabled)
                    and state.get("auto_blink_due_ms") is not None
                ):
                    state["auto_blink_preserved_count"] += 1
                    return
                auto_blink_timer.Stop()
                state["auto_blink_due_ms"] = None
                if (
                    not bool(form.Visible)
                    or state.get("base_frame") != "idle"
                    or bool(state.get("animation_locked"))
                    or bool(state.get("reduced_motion"))
                    or bool(menu.Visible)
                ):
                    return
                due = random.randint(AUTO_BLINK_MIN_MS, AUTO_BLINK_MAX_MS)
                auto_blink_timer.Interval = due
                state["auto_blink_due_ms"] = due
                auto_blink_timer.Start()

            def finish_motion() -> None:
                animation_timer.Stop()
                completed_source = state.get("animation_source")
                state["animation_locked"] = False
                state["animation_source"] = None
                state["animation_completed_count"] += 1
                active_receipt = state.pop("active_presentation_receipt", None)
                state["active_presentation_signature"] = None
                if isinstance(active_receipt, dict):
                    state["enqueue_presentation_receipt"](
                        {**active_receipt, "event": "COMPLETED"}
                    )
                queued = state.get("presentation_queue") or []
                if completed_source == "auto":
                    restore_frame = str(
                        state.pop("auto_blink_restore_frame", None)
                        or state.get("base_frame")
                        or "idle"
                    )
                    queued_names = [
                        str(entry.get("animation") or "")
                        for entry in queued
                        if isinstance(entry, dict)
                    ]
                    plan = auto_blink_completion_plan(
                        animation_source="auto",
                        restore_frame=restore_frame,
                        queued_animation_names=queued_names,
                    )
                    low_priority = {"STATIC", "BLINK", "MESSAGE", "WORKING"}
                    urgent = [
                        entry
                        for entry in queued
                        if isinstance(entry, dict)
                        and (str(entry.get("animation") or "").upper() not in low_priority
                             or str(entry.get("receipt_payload", {}).get("delivery_id") or "").startswith("message:"))
                    ]
                    queued[:] = urgent
                    state["auto_blink_discarded_queue_count"] += int(
                        plan["discarded_queue_count"]
                    )
                    state["base_frame"] = str(plan["restore_frame"])
                    while queued:
                        entry = queued.pop(0)
                        if state["start_presentation_entry"](entry):
                            return
                    set_visual(str(plan["restore_frame"]))
                    schedule_auto_blink()
                    return
                # STATIC projections have no timer/completion callback. Drain
                # those entries here so a durable message behind them cannot
                # remain claimed forever, then get dropped by an idle blink.
                while queued:
                    entry = queued.pop(0)
                    if state["start_presentation_entry"](entry):
                        return
                set_visual(str(state.get("base_frame") or "idle"))
                schedule_auto_blink()

            def on_animation_tick(_sender, _event) -> None:
                elapsed_ms = (
                    int(state.get("animation_tick_index", 0))
                    * NATIVE_ANIMATION_FRAME_INTERVAL_MS
                )
                state["animation_tick_index"] = int(
                    state.get("animation_tick_index", 0)
                ) + 1
                sample = sample_motion(
                    str(state.get("animation_state") or "STATIC"),
                    elapsed_ms,
                    base_frame=str(state.get("base_frame") or "idle"),
                )
                frame = presentation_frame_name(
                    str(state.get("animation_state") or "STATIC"),
                    sample.frame,
                    done=sample.done,
                )
                if bool(state.get("reduced_motion")):
                    # Keep the acknowledgement visible for its normal duration,
                    # without motion, then let finish_motion restore the base.
                    set_visual(frame)
                else:
                    set_visual(
                        frame,
                        offset_x=sample.offset_x,
                        offset_y=sample.offset_y,
                        rotation_degrees=sample.rotation_degrees,
                        scale=sample.scale,
                        opacity=sample.opacity,
                    )
                if sample.done:
                    finish_motion()

            def play_native_motion(
                motion_name: str,
                *,
                source_name: str = "presentation",
                base_frame: str | None = None,
            ) -> bool:
                normalized = str(motion_name).upper()
                if source_name == "user" and bool(state.get("animation_locked")):
                    return False
                animation_timer.Stop()
                auto_blink_timer.Stop()
                state["auto_blink_due_ms"] = None
                if base_frame is not None:
                    state["base_frame"] = base_frame
                state["animation_state"] = normalized
                state["animation_sequence"] = {
                    "BLINK": ["blink-open", "blink-closed", "blink-open", "blink-closed", "blink-open", str(state["base_frame"])],
                    "INTERACTION": ["interaction", str(state["base_frame"])],
                    "WAKE": ["interaction", str(state["base_frame"])],
                    "MESSAGE": ["interaction", str(state["base_frame"])],
                    "WORKING": ["working", "working"],
                    "ATTENTION": ["interaction", str(state["base_frame"])],
                    "SUCCESS": [
                        presentation_frame_name("SUCCESS", "success"),
                        str(state["base_frame"]),
                    ],
                    "ERROR": [
                        presentation_frame_name("ERROR", "idle"),
                        str(state["base_frame"]),
                    ],
                }.get(normalized, [str(state["base_frame"])])
                reduced_expression = (
                    bool(state.get("reduced_motion"))
                    and presentation_frame_name(normalized, str(state["base_frame"]))
                    in {"save-success", "error"}
                )
                if normalized in {"STATIC", "DRAG", "SLEEP"} or (
                    bool(state.get("reduced_motion")) and not reduced_expression
                ):
                    static_frame = (
                        "interaction" if normalized == "DRAG"
                        else "blink-closed" if normalized == "SLEEP"
                        else presentation_frame_name(
                            normalized, str(state["base_frame"])
                        )
                    )
                    state["animation_locked"] = False
                    state["animation_source"] = None
                    set_visual(static_frame)
                    schedule_auto_blink()
                    return False
                if normalized not in {"INTERACTION", "BLINK", "WAKE", "MESSAGE", "WORKING", "ATTENTION", "SUCCESS", "ERROR"}:
                    state["animation_locked"] = False
                    state["animation_source"] = None
                    set_visual(str(state["base_frame"]))
                    schedule_auto_blink()
                    return False
                state["animation_locked"] = True
                state["animation_source"] = source_name
                state["animation_started_at"] = time.perf_counter()
                state["animation_tick_index"] = 1
                if normalized == "INTERACTION":
                    state["interaction_animation_count"] += 1
                if normalized == "BLINK" and source_name == "auto":
                    state["auto_blink_restore_frame"] = str(
                        state.get("base_frame") or "idle"
                    )
                    state["auto_blink_count"] += 1
                first = sample_motion(normalized, 0.0, base_frame=str(state["base_frame"]))
                set_visual(
                    presentation_frame_name(
                        normalized, first.frame, done=first.done
                    ),
                    offset_x=0.0 if reduced_expression else first.offset_x,
                    offset_y=0.0 if reduced_expression else first.offset_y,
                    rotation_degrees=0.0 if reduced_expression else first.rotation_degrees,
                    scale=1.0 if reduced_expression else first.scale,
                    opacity=1.0 if reduced_expression else first.opacity,
                )
                animation_timer.Start()
                if normalized == "SUCCESS" and source_name == "presentation":
                    state["last_success_animation_at"] = time.perf_counter()
                    state["terminal_animation_started_count"] += 1
                return True

            def start_presentation_entry(entry: dict[str, Any]) -> bool:
                state["presentation_reason_code"] = entry["reason_code"]
                state["reduced_motion"] = entry["reduced_motion"]
                state["last_presentation_signature"] = entry["signature"]
                started = play_native_motion(
                    entry["animation"],
                    source_name="presentation",
                    base_frame=entry["base_frame"],
                )
                event_name = "STARTED" if started else "STATIC"
                state["enqueue_presentation_receipt"](
                    {**entry["receipt_payload"], "event": event_name}
                )
                if started:
                    state["active_presentation_receipt"] = entry["receipt_payload"]
                    state["active_presentation_signature"] = entry["signature"]
                return started

            def on_auto_blink(_sender, _event) -> None:
                auto_blink_timer.Stop()
                state["auto_blink_due_ms"] = None
                play_native_motion("BLINK", source_name="auto")

            animation_timer.Tick += on_animation_tick
            auto_blink_timer.Tick += on_auto_blink
            state["animation_timer"] = animation_timer
            state["auto_blink_timer"] = auto_blink_timer
            state["set_frame"] = lambda frame_name: set_visual(frame_name)
            state["set_visual"] = set_visual
            state["play_native_motion"] = play_native_motion
            state["start_presentation_entry"] = start_presentation_entry
            state["schedule_auto_blink"] = schedule_auto_blink
            state["animation_tick_handler"] = on_animation_tick
            state["auto_blink_tick_handler"] = on_auto_blink

            def run_action(action_id: str) -> None:
                try:
                    receipt = api.assistant_shortcut({"action_id": action_id})
                    state["last_action"] = action_id
                    state["last_action_status"] = receipt.get("status")
                    if action_id == "OPEN_Desktop_MAIN":
                        state["primary_activation_count"] += 1
                        focus_receipt = receipt.get("main_window_focus") or {}
                        state["last_primary_status"] = focus_receipt.get("status")
                        state["last_primary_receipt"] = dict(focus_receipt)
                except Exception as error:
                    state["last_action"] = action_id
                    state["last_action_status"] = "ERROR:" + type(error).__name__

            def run_external_target(target: str) -> None:
                try:
                    receipt = api.assistant_launch_target({"target": target})
                    state["last_action"] = "EXTERNAL_SHORTCUT"
                    state["last_action_status"] = receipt.get("status")
                except Exception as error:
                    state["last_action"] = "EXTERNAL_SHORTCUT"
                    state["last_action_status"] = "ERROR:" + type(error).__name__

            def dispatch_action(action_id: str) -> None:
                worker = threading.Thread(target=run_action, args=(action_id,), daemon=True)
                worker.start()

            def dispatch_external_target(target: str) -> None:
                worker = threading.Thread(target=run_external_target, args=(target,), daemon=True)
                worker.start()

            action_specs = (
                ("打开 Memorive", "OPEN_Desktop_MAIN"),
                ("桌面助手设置", "OPEN_DESKTOP_ASSISTANT_SETTINGS"),
                ("暂时隐藏", "HIDE_DESKTOP_ASSISTANT"),
            )
            native_items: list[Any] = []
            fixed_open_label, fixed_open_action = action_specs[0]
            open_item = ToolStripMenuItem(fixed_open_label)
            open_item.Tag = fixed_open_action

            def on_open_click(_sender, _event) -> None:
                dispatch_action(fixed_open_action)

            open_item.Click += on_open_click
            state["handlers"].append(on_open_click)
            menu.Items.Add(open_item)
            native_items.append(open_item)

            first_separator = ToolStripSeparator()
            menu.Items.Add(first_separator)
            native_items.append(first_separator)

            external_items: list[Any] = []
            for slot_index in range(3):
                item = ToolStripMenuItem("")
                item.Name = f"DesktopAssistantShortcutSlot{slot_index + 1}"
                item.Visible = False

                def on_external_click(sender, _event) -> None:
                    selected_target = str(sender.Tag or "")
                    if selected_target:
                        dispatch_external_target(selected_target)

                item.Click += on_external_click
                state["handlers"].append(on_external_click)
                menu.Items.Add(item)
                native_items.append(item)
                external_items.append(item)

            shortcut_separator = ToolStripSeparator()
            shortcut_separator.Visible = False
            menu.Items.Add(shortcut_separator)
            native_items.append(shortcut_separator)

            for label, action_id in action_specs[1:]:
                item = ToolStripMenuItem(label)
                item.Tag = action_id

                def on_item_click(_sender, _event, selected: str = action_id) -> None:
                    dispatch_action(selected)

                item.Click += on_item_click
                state["handlers"].append(on_item_click)
                menu.Items.Add(item)
                native_items.append(item)
            state["items"] = native_items
            state["external_items"] = external_items

            def refresh_shortcut_items() -> None:
                try:
                    projection = api.call("assistant.preference_get", {})
                    preferences = projection.get("preferences", {})
                    entries = preferences.get("shortcut_entries", [])
                except Exception:
                    entries = []
                visible_count = 0
                for index, item in enumerate(external_items):
                    entry = entries[index] if index < len(entries) and isinstance(entries[index], dict) else {}
                    name = str(entry.get("name") or "").strip()
                    target = str(entry.get("target") or "").strip()
                    if not name or not target:
                        item.Visible = False
                        item.Tag = None
                        continue
                    try:
                        target_status = api.assistant_target_status(
                            {"target": target}
                        )
                        available = bool(target_status.get("available"))
                    except Exception:
                        available = False
                    item.Text = name
                    item.Tag = target
                    item.Enabled = available
                    item.ToolTipText = "" if available else "打开位置不可用"
                    item.Visible = True
                    visible_count += 1
                first_separator.Visible = True
                shortcut_separator.Visible = visible_count > 0

            def on_menu_opening(_sender, event) -> None:
                if bool(state.get("animation_locked")):
                    event.Cancel = True
                    return
                refresh_shortcut_items()

            def on_menu_opened(_sender, _event) -> None:
                state["menu_open_count"] += 1
                auto_blink_timer.Stop()
                state["auto_blink_due_ms"] = None

            def on_menu_closed(_sender, event) -> None:
                state["menu_close_count"] += 1
                state["menu_close_reasons"].append(str(event.CloseReason))
                schedule_auto_blink()

            menu.Opening += on_menu_opening
            menu.Opened += on_menu_opened
            menu.Closed += on_menu_closed
            state["handlers"].extend((on_menu_opening, on_menu_opened, on_menu_closed))

            def update_drag_position() -> None:
                drag = state.get("drag")
                if drag is None:
                    return
                cursor = Cursor.Position
                delta_x = int(cursor.X) - int(drag["cursor_x"])
                delta_y = int(cursor.Y) - int(drag["cursor_y"])
                if delta_x * delta_x + delta_y * delta_y >= 36:
                    drag["moved"] = True
                if not drag["moved"]:
                    return
                auto_blink_timer.Stop()
                state["animation_state"] = "DRAG"
                set_visual("interaction")
                target_x = int(drag["form_x"]) + delta_x
                target_y = int(drag["form_y"]) + delta_y
                form.Left = target_x
                form.Top = target_y
                state["drag_move_count"] = int(state.get("drag_move_count", 0)) + 1
                state["last_drag_target"] = {"x": target_x, "y": target_y}

            def on_drag_tick(_sender, _event) -> None:
                update_drag_position()

            drag_timer.Tick += on_drag_tick
            state["drag_timer"] = drag_timer
            state["drag_tick_handler"] = on_drag_tick

            def interrupt_motion_for_drag() -> None:
                """User input owns the pet immediately; no stale motion may resume."""

                animation_timer.Stop()
                auto_blink_timer.Stop()
                state["auto_blink_due_ms"] = None
                interrupted: list[dict[str, Any]] = []
                active_receipt = state.pop("active_presentation_receipt", None)
                if isinstance(active_receipt, dict):
                    interrupted.append(active_receipt)
                for entry in list(state.get("presentation_queue") or []):
                    if isinstance(entry, dict) and isinstance(
                        entry.get("receipt_payload"), dict
                    ):
                        interrupted.append(entry["receipt_payload"])
                state["presentation_queue"].clear()
                state["active_presentation_signature"] = None
                state["animation_locked"] = False
                state["animation_source"] = None
                state["interrupted_presentation_count"] += len(interrupted)
                for receipt in interrupted:
                    state["enqueue_presentation_receipt"](
                        {**receipt, "event": "INTERRUPTED"}
                    )
                set_visual(str(state.get("base_frame") or "idle"))

            state["interrupt_motion_for_drag"] = interrupt_motion_for_drag

            def on_mouse_down(_sender, event) -> None:
                if event.Button != MouseButtons.Left:
                    return
                interrupt_motion_for_drag()
                cursor = Cursor.Position
                state["drag"] = {
                    "cursor_x": int(cursor.X), "cursor_y": int(cursor.Y),
                    "form_x": int(form.Left), "form_y": int(form.Top),
                    "moved": False,
                }
                picture.Cursor = Cursors.SizeAll
                picture.Capture = True
                drag_timer.Start()

            def on_mouse_move(_sender, event) -> None:
                update_drag_position()

            def finish_drag(*, activate_if_click: bool) -> None:
                drag = state.get("drag")
                if drag is None:
                    return
                drag_timer.Stop()
                update_drag_position()
                state["drag"] = None
                picture.Capture = False
                picture.Cursor = Cursors.Hand
                if not drag["moved"] and activate_if_click:
                    play_native_motion("INTERACTION", source_name="user")
                    dispatch_action("OPEN_Desktop_MAIN")
                    return
                state["animation_state"] = "STATIC"
                set_visual(str(state.get("base_frame") or "idle"))
                schedule_auto_blink()
                target = {
                    "x": int(form.Left),
                    "y": int(form.Top),
                    "persist": True,
                }

                def persist_move() -> None:
                    try:
                        api.assistant_move_window(target)
                        state["last_action"] = "PERSIST_PLACEMENT"
                        state["last_action_status"] = "PASS"
                    except Exception as error:
                        state["last_action"] = "PERSIST_PLACEMENT"
                        state["last_action_status"] = "ERROR:" + type(error).__name__

                threading.Thread(target=persist_move, daemon=True).start()

            def on_mouse_up(_sender, event) -> None:
                if event.Button != MouseButtons.Left:
                    return
                finish_drag(activate_if_click=True)

            def on_capture_changed(_sender, _event) -> None:
                if state.get("drag") is not None and not bool(picture.Capture):
                    finish_drag(activate_if_click=False)

            picture.MouseDown += on_mouse_down
            picture.MouseMove += on_mouse_move
            picture.MouseUp += on_mouse_up
            picture.MouseCaptureChanged += on_capture_changed
            picture.ContextMenuStrip = menu
            state["handlers"].extend(
                (on_mouse_down, on_mouse_move, on_mouse_up, on_capture_changed)
            )

            def on_resize(_sender, _event) -> None:
                place_overlay(picture, state)

            # Client metrics settle after the outer Resize event during
            # initial chrome/DPI adjustment.
            form.ClientSizeChanged += on_resize
            state["handlers"].append(on_resize)

            def on_visible_changed(_sender, _event) -> None:
                if bool(form.Visible):
                    picture.Visible = True
                    picture.Enabled = True
                    picture.BringToFront()
                    if state.get("rendered_image") is not None:
                        state["alpha_surface"].present(state["rendered_image"])
                    schedule_auto_blink()
                else:
                    animation_timer.Stop()
                    auto_blink_timer.Stop()
                    drag_timer.Stop()
                    state["drag"] = None
                    picture.Capture = False
                    state["animation_locked"] = False
                    state["animation_source"] = None
                    state["auto_blink_due_ms"] = None

            form.VisibleChanged += on_visible_changed
            state["handlers"].append(on_visible_changed)

            def on_disposed(_sender, _event) -> None:
                state["alpha_surface"].dispose()
                try:
                    animation_timer.Stop()
                    animation_timer.Tick -= on_animation_tick
                    animation_timer.Dispose()
                except Exception:
                    pass
                try:
                    auto_blink_timer.Stop()
                    auto_blink_timer.Tick -= on_auto_blink
                    auto_blink_timer.Dispose()
                except Exception:
                    pass
                try:
                    drag_timer.Stop()
                    drag_timer.Tick -= on_drag_tick
                    drag_timer.Dispose()
                except Exception:
                    pass
                try:
                    receipt_queue.put_nowait(None)
                except Exception:
                    pass
                disposed_images: set[int] = set()
                for rendered in state.get("render_cache", {}).values():
                    try:
                        identity = id(rendered)
                        if identity not in disposed_images:
                            rendered.Dispose()
                            disposed_images.add(identity)
                    except Exception:
                        pass
                state["render_cache"].clear()
                state["rendered_image"] = None
                window_region = state.get("window_region")
                if window_region is not None:
                    try:
                        window_region.Dispose()
                    except Exception:
                        pass
                for frame in state.get("frames", {}).values():
                    try:
                        frame.Dispose()
                    except Exception:
                        pass

            form.Disposed += on_disposed
            state["handlers"].append(on_disposed)
            form.Controls.Add(picture)
            place_overlay(picture, state)
            set_visual("idle")
            _ASSISTANT_NATIVE_OVERLAYS[form_handle] = state
        else:
            place_overlay(existing["picture"], existing)

    form.Invoke(Action(configure))
    if reveal:
        window.show()

    def schedule_assistant_auto_blink() -> None:
        overlay = _ASSISTANT_NATIVE_OVERLAYS.get(int(form.Handle.ToInt64()))
        if overlay is not None:
            overlay["schedule_auto_blink"]()

    form.Invoke(Action(schedule_assistant_auto_blink))


def apply_assistant_native_presentation(
    window: webview.Window, presentation: dict[str, Any]
) -> dict[str, Any]:
    import clr
    clr.AddReference("System.Windows.Forms")
    from System import Action

    if not isinstance(presentation, dict):
        raise ValueError("ASSISTANT_NATIVE_PRESENTATION_INVALID")
    animation = str(presentation.get("animation_state", "STATIC"))
    reason_code = str(presentation.get("reason_code", ""))
    locator = str(presentation.get("locator") or "") or None
    delivery_id = str(presentation.get("delivery_id") or "") or None
    reduced_motion = bool(presentation.get("reduced_motion"))
    base_frame = base_frame_for_presentation(animation, reason_code)
    signature = (
        delivery_id,
        locator,
        animation,
        reason_code,
        reduced_motion,
        base_frame,
    )
    form = window.native
    form_handle = int(form.Handle.ToInt64())
    overlay_snapshot = _ASSISTANT_NATIVE_OVERLAYS.get(form_handle)
    if overlay_snapshot is None:
        raise RuntimeError("ASSISTANT_NATIVE_OVERLAY_NOT_READY")
    receipt_api = overlay_snapshot["api"]
    holder: dict[str, Any] = {}
    durable_claim_status = "NOT_REQUIRED"
    durably_consumed = False
    if delivery_id is not None:
        try:
            claim = receipt_api.claim_assistant_presentation_delivery(
                {
                    "locator": locator,
                    "reason_code": reason_code,
                    "animation_state": animation.upper(),
                    "delivery_id": delivery_id,
                    "reduced_motion": reduced_motion,
                }
            )
            durable_claim_status = str(claim.get("status") or "INVALID")
            durably_consumed = claim.get("already_consumed") is True
        except Exception as error:
            # Do not animate a durable transition when exactly-once ownership
            # cannot be established. This I/O deliberately stays off the UI
            # thread shared by the pet and the main Memorive window.
            durable_claim_status = "BLOCKED:" + type(error).__name__
            durably_consumed = True

    def apply() -> None:
        overlay = _ASSISTANT_NATIVE_OVERLAYS.get(form_handle)
        if overlay is None:
            raise RuntimeError("ASSISTANT_NATIVE_OVERLAY_NOT_READY")
        timer = overlay["animation_timer"]
        overlay["presentation_apply_count"] += 1
        queued_signatures = {
            entry.get("signature")
            for entry in overlay.get("presentation_queue", [])
            if isinstance(entry, dict)
        }
        repeated = (
            durably_consumed
            or overlay.get("active_presentation_signature") == signature
            or signature in queued_signatures
            or overlay.get("last_presentation_signature") == signature
        )
        started = False
        queued = False
        receipt_payload = {
            "locator": locator,
            "reason_code": reason_code,
            "animation_state": animation.upper(),
            "delivery_id": delivery_id,
            "reduced_motion": reduced_motion,
            "deduplicated": repeated,
            "single_shot_started": False,
        }
        now_monotonic = time.perf_counter()
        reliable_message = bool(delivery_id and delivery_id.startswith("message:"))
        drag_preempted = bool(overlay.get("drag")) and not repeated and not reliable_message
        success_in_flight = (
            bool(overlay.get("animation_locked"))
            and str(overlay.get("animation_state") or "").upper() == "SUCCESS"
        ) or any(
            isinstance(entry, dict)
            and str(entry.get("animation") or "").upper() == "SUCCESS"
            for entry in overlay.get("presentation_queue", [])
        )
        terminal_coalesced = bool(
            not repeated
            and not drag_preempted
            and animation.upper() == "SUCCESS"
            and (
                success_in_flight
                or (
                    float(overlay.get("last_success_animation_at") or 0.0) > 0.0
                    and (
                        now_monotonic
                        - float(overlay.get("last_success_animation_at") or 0.0)
                    )
                    * 1000.0
                    <= TERMINAL_ANIMATION_COALESCE_MS
                )
            )
        )
        blink_suppressed = not reliable_message and not repeated and not drag_preempted and not terminal_coalesced and should_suppress_presentation_during_auto_blink(
            animation_locked=bool(overlay.get("animation_locked")),
            animation_source=overlay.get("animation_source"),
            animation=animation,
        )
        if drag_preempted:
            overlay["presentation_reason_code"] = reason_code
            overlay["last_presentation_signature"] = signature
            overlay["interrupted_presentation_count"] += 1
        elif terminal_coalesced:
            overlay["presentation_reason_code"] = reason_code
            overlay["last_presentation_signature"] = signature
            overlay["coalesced_terminal_count"] += 1
        elif blink_suppressed:
            overlay["presentation_reason_code"] = reason_code
            overlay["last_presentation_signature"] = signature
        elif not repeated and (bool(overlay.get("animation_locked")) or (reliable_message and bool(overlay.get("drag")))):
            queued = True
            queued_payload = {**receipt_payload, "single_shot_started": True}
            overlay["presentation_queue"].append({
                "signature": signature,
                "animation": animation,
                "reason_code": reason_code,
                "reduced_motion": reduced_motion,
                "base_frame": base_frame,
                "receipt_payload": queued_payload,
            })
            overlay["queued_presentation_count"] += 1
        elif not repeated:
            overlay["presentation_reason_code"] = reason_code
            overlay["reduced_motion"] = reduced_motion
            overlay["last_presentation_signature"] = signature
            started = overlay["play_native_motion"](
                animation,
                source_name="presentation",
                base_frame=base_frame,
            )
            receipt_payload["single_shot_started"] = bool(started)
            if started:
                overlay["active_presentation_signature"] = signature
        elif not bool(overlay.get("animation_locked")):
            overlay["base_frame"] = base_frame
            overlay["set_frame"](base_frame)
            # The HTML authority refreshes its projection every three seconds,
            # while its idle blink deadline is 8-18 seconds. Preserve an
            # already-running deadline so repeated identical projections
            # cannot postpone blinking forever.
            overlay["schedule_auto_blink"](preserve_existing=True)
        receipt_event = (
            "DEDUPLICATED" if repeated
            else "INTERRUPTED" if drag_preempted
            else "COALESCED" if terminal_coalesced
            else "BLINK_QUEUE_SUPPRESSED" if blink_suppressed
            else "QUEUED" if queued
            else "STARTED" if started
            else "STATIC"
        )
        if started:
            overlay["active_presentation_receipt"] = receipt_payload
        holder["_receipt_payload"] = {**receipt_payload, "event": receipt_event}
        holder.update({
            "animation_state": animation,
            "current_frame": overlay["current_frame"],
            "sequence": list(overlay.get("animation_sequence") or [base_frame]),
            "base_frame": overlay["base_frame"],
            "single_shot_started": bool(started) and bool(timer.Enabled),
            "queued": queued,
            "terminal_coalesced": terminal_coalesced,
            "drag_preempted": drag_preempted,
            "blink_queue_suppressed": blink_suppressed,
            "queue_depth": len(overlay.get("presentation_queue", [])),
            "deduplicated": repeated,
            "presentation_apply_count": int(overlay["presentation_apply_count"]),
            "reason_code": overlay["presentation_reason_code"],
            "locator": locator,
            "durable_claim_status": durable_claim_status,
            "timer_enabled": bool(timer.Enabled),
            "motion_contract": public_motion_contract(),
        })

    form.Invoke(Action(apply))
    receipt_payload = holder.pop("_receipt_payload", None)
    receipt_status = "NOT_RECORDED"
    if isinstance(receipt_payload, dict):
        try:
            receipt_api.record_assistant_presentation_receipt(receipt_payload)
            receipt_status = "PASS"
        except Exception as error:
            receipt_status = "WARNING:" + type(error).__name__
    holder["durable_receipt_status"] = receipt_status
    return {"schema_version": "DesktopAssistantNativePresentationReceipt-v1", **holder, "status": "PASS"}


def inspect_assistant_native(window: webview.Window, pet_probe: dict[str, Any]) -> dict[str, Any]:
    import clr
    clr.AddReference("System.Windows.Forms")
    from System import Action
    from ctypes import wintypes

    class Point(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class Rect(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    form = window.native
    holder: dict[str, Any] = {}

    def inspect() -> None:
        viewport = pet_probe.get("viewport_size_css") or {}
        viewport_width = float(viewport.get("width") or 0)
        viewport_height = float(viewport.get("height") or 0)
        if viewport_width <= 0 or viewport_height <= 0:
            raise RuntimeError("ASSISTANT_VIEWPORT_PROBE_INVALID")
        scale_x = float(form.ClientSize.Width) / viewport_width
        scale_y = float(form.ClientSize.Height) / viewport_height
        user32 = ctypes.windll.user32
        user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(Point)]
        user32.ClientToScreen.restype = wintypes.BOOL
        user32.WindowFromPoint.argtypes = [Point]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        form_handle = int(form.Handle.ToInt64())
        overlay = _ASSISTANT_NATIVE_OVERLAYS.get(form_handle)
        picture = overlay.get("picture") if overlay else None
        menu = overlay.get("menu") if overlay else None
        if picture is None:
            raise RuntimeError("ASSISTANT_NATIVE_OVERLAY_NOT_READY")
        # The final renderer is the native PictureBox, not the clipped legacy
        # HTML anchor. Probe its real centre so hit testing follows the same
        # surface the user clicks and drags.
        point = Point(
            int(picture.Left) + max(1, int(picture.Width) // 2),
            int(picture.Top) + max(1, int(picture.Height) // 2),
        )
        if not user32.ClientToScreen(form_handle, ctypes.byref(point)):
            raise RuntimeError("ASSISTANT_HIT_POINT_CONVERSION_FAILED")
        hit_window = user32.WindowFromPoint(point)
        hit_root = user32.GetAncestor(hit_window, 2) if hit_window else 0
        corner = Point(1, 1)
        if not user32.ClientToScreen(form_handle, ctypes.byref(corner)):
            raise RuntimeError("ASSISTANT_CORNER_POINT_CONVERSION_FAILED")
        corner_window = user32.WindowFromPoint(corner)
        corner_root = user32.GetAncestor(corner_window, 2) if corner_window else 0
        gdi32 = ctypes.windll.gdi32
        gdi32.CreateRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        gdi32.CreateRectRgn.restype = wintypes.HANDLE
        gdi32.GetRgnBox.argtypes = [wintypes.HANDLE, ctypes.POINTER(Rect)]
        gdi32.GetRgnBox.restype = ctypes.c_int
        gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        gdi32.DeleteObject.restype = wintypes.BOOL
        user32.GetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE]
        user32.GetWindowRgn.restype = ctypes.c_int
        native_region = gdi32.CreateRectRgn(0, 0, 0, 0)
        region_type = 0
        region_box = Rect()
        try:
            if native_region:
                region_type = int(user32.GetWindowRgn(form_handle, native_region))
                if region_type:
                    gdi32.GetRgnBox(native_region, ctypes.byref(region_box))
        finally:
            if native_region:
                gdi32.DeleteObject(native_region)
        extended_style = int(user32.GetWindowLongW(form_handle, -20))
        holder.update({
            "transparent_requested": bool(window.transparent),
            "form_border_style": str(form.FormBorderStyle),
            "show_in_taskbar": bool(form.ShowInTaskbar),
            "topmost": bool(form.TopMost),
            "allow_transparency": bool(form.AllowTransparency),
            "opacity": float(form.Opacity),
            "visible": bool(form.Visible),
            "back_color_argb": int(form.BackColor.ToArgb()),
            "transparency_key_argb": int(form.TransparencyKey.ToArgb()),
            "transparency_key_is_empty": bool(form.TransparencyKey.IsEmpty),
            "transparency_key_matches_back_color": (
                int(form.TransparencyKey.ToArgb()) == int(form.BackColor.ToArgb())
            ),
            "chroma_key_rgb": {
                "r": int(form.TransparencyKey.R),
                "g": int(form.TransparencyKey.G),
                "b": int(form.TransparencyKey.B),
            },
            "webview_background_argb": int(form.webview.DefaultBackgroundColor.ToArgb()),
            "webview_background_alpha": int(form.webview.DefaultBackgroundColor.A),
            "webview_child_visible": bool(form.webview.Visible),
            "webview_child_enabled": bool(form.webview.Enabled),
            "extended_style": extended_style,
            "layered_window": bool(extended_style & 0x00080000),
            "per_pixel_alpha": bool(overlay and overlay.get("alpha_surface") and overlay["alpha_surface"].updates > 0),
            "no_activate_window": bool(extended_style & 0x08000000),
            "pet_hit_point_screen": {"x": point.x, "y": point.y},
            "client_to_css_scale": {"x": scale_x, "y": scale_y},
            "hit_window_handle": int(hit_window or 0),
            "hit_root_handle": int(hit_root or 0),
            "form_handle": form_handle,
            "pet_hit_test_targets_assistant": int(hit_root or 0) == form_handle,
            "form_client_size": {
                "width": int(form.ClientSize.Width),
                "height": int(form.ClientSize.Height),
            },
            "pet_surface_bounds": {
                "left": int(picture.Left) if picture is not None else -1,
                "top": int(picture.Top) if picture is not None else -1,
                "width": int(picture.Width) if picture is not None else 0,
                "height": int(picture.Height) if picture is not None else 0,
            },
            "host_matches_pet_surface": bool(
                picture is not None
                and int(picture.Left) == 0
                and int(picture.Top) == 0
                and int(picture.Width) == int(form.ClientSize.Width)
                and int(picture.Height) == int(form.ClientSize.Height)
            ),
            "window_region_type": region_type,
            "window_region_complex": region_type == 3,
            "window_region_bounds": {
                "left": int(region_box.left),
                "top": int(region_box.top),
                "right": int(region_box.right),
                "bottom": int(region_box.bottom),
            },
            "transparent_corner_hit_root": int(corner_root or 0),
            "transparent_corner_not_assistant": int(corner_root or 0) != form_handle,
            "native_pet_overlay_ready": bool(
                picture is not None and picture.Visible and picture.Image is not None
            ),
            "native_pet_overlay_handle": int(picture.Handle.ToInt64()) if picture is not None else 0,
            "pet_hit_window_is_native_overlay": bool(
                picture is not None and int(hit_window or 0) == int(picture.Handle.ToInt64())
            ),
            "native_context_menu_attached": bool(
                picture is not None and menu is not None
                and picture.ContextMenuStrip is not None
                and str(picture.ContextMenuStrip.Name) == str(menu.Name)
            ),
            "native_context_menu_auto_close": bool(menu.AutoClose) if menu is not None else False,
            "native_animation_state": overlay.get("animation_state") if overlay else None,
            "native_current_frame": overlay.get("current_frame") if overlay else None,
            "current_frame_sha256": overlay.get("current_frame_sha256") if overlay else None,
            "frame_sha256s": dict(overlay.get("frame_sha256s") or {}) if overlay else {},
            "native_animation_timer_enabled": bool(overlay["animation_timer"].Enabled) if overlay else False,
            "native_animation_frame_interval_ms": int(overlay["animation_timer"].Interval) if overlay else 0,
            "native_animation_locked": bool(overlay.get("animation_locked")) if overlay else False,
            "native_base_frame": overlay.get("base_frame") if overlay else None,
            "native_interaction_animation_count": int(overlay.get("interaction_animation_count", 0)) if overlay else 0,
            "native_animation_completed_count": int(overlay.get("animation_completed_count", 0)) if overlay else 0,
            "native_auto_blink_count": int(overlay.get("auto_blink_count", 0)) if overlay else 0,
            "native_auto_blink_due_ms": overlay.get("auto_blink_due_ms") if overlay else None,
            "native_auto_blink_preserved_count": int(overlay.get("auto_blink_preserved_count", 0)) if overlay else 0,
            "native_drag_active": bool(overlay.get("drag")) if overlay else False,
            "native_drag_timer_enabled": bool(overlay["drag_timer"].Enabled) if overlay else False,
            "native_drag_move_count": int(overlay.get("drag_move_count", 0)) if overlay else 0,
            "native_last_drag_target": dict(overlay.get("last_drag_target") or {}) if overlay else {},
            "native_presentation_apply_count": int(overlay.get("presentation_apply_count", 0)) if overlay else 0,
            "native_presentation_reason_code": overlay.get("presentation_reason_code") if overlay else None,
            "native_render_cache_count": len(overlay.get("render_cache") or {}) if overlay else 0,
            "native_render_cache_limit": int(overlay.get("render_cache_limit", 0)) if overlay else 0,
            "native_terminal_animation_started_count": int(overlay.get("terminal_animation_started_count", 0)) if overlay else 0,
            "native_coalesced_terminal_count": int(overlay.get("coalesced_terminal_count", 0)) if overlay else 0,
            "native_interrupted_presentation_count": int(overlay.get("interrupted_presentation_count", 0)) if overlay else 0,
        })

    form.Invoke(Action(inspect))
    return holder


def inspect_assistant_visibility(window: webview.Window) -> dict[str, Any]:
    import clr
    clr.AddReference("System.Windows.Forms")
    from System import Action

    form = window.native
    holder: dict[str, Any] = {}

    def inspect() -> None:
        holder.update({
            "visible": bool(form.Visible),
            "left": int(form.Left),
            "top": int(form.Top),
            "width": int(form.Width),
            "height": int(form.Height),
            "form_handle": int(form.Handle.ToInt64()),
        })

    form.Invoke(Action(inspect))
    return holder


def open_assistant_native_menu(window: webview.Window) -> dict[str, Any]:
    import clr
    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    from System import Action
    from System.Drawing import Point, Rectangle, Size
    from System.Windows.Forms import Screen, ToolStripItemPlacement

    form = window.native
    holder: dict[str, Any] = {}

    def show_and_inspect() -> None:
        form_handle = int(form.Handle.ToInt64())
        overlay = _ASSISTANT_NATIVE_OVERLAYS.get(form_handle)
        if overlay is None:
            raise RuntimeError("ASSISTANT_NATIVE_OVERLAY_NOT_READY")
        picture = overlay["picture"]
        menu = overlay["menu"]
        menu.Show(picture, Point(max(1, picture.Width // 2), max(1, picture.Height // 2)))
        menu.Update()
        bounds = menu.Bounds
        working = Screen.FromRectangle(
            Rectangle(bounds.Left, bounds.Top, bounds.Width, bounds.Height)
        ).WorkingArea
        action_items = [
            item for item in menu.Items
            if item.Tag is not None and str(item.Tag)
        ]
        preferred = menu.GetPreferredSize(Size.Empty)
        holder.update({
            "opened": bool(menu.Visible),
            "auto_close": bool(menu.AutoClose),
            "context_menu_attached": bool(
                picture.ContextMenuStrip is not None
                and str(picture.ContextMenuStrip.Name) == str(menu.Name)
            ),
            "bounds": {
                "left": int(bounds.Left), "top": int(bounds.Top),
                "width": int(bounds.Width), "height": int(bounds.Height),
                "right": int(bounds.Right), "bottom": int(bounds.Bottom),
            },
            "working_area": {
                "left": int(working.Left), "top": int(working.Top),
                "right": int(working.Right), "bottom": int(working.Bottom),
            },
            "fully_within_working_area": (
                bounds.Left >= working.Left and bounds.Top >= working.Top
                and bounds.Right <= working.Right and bounds.Bottom <= working.Bottom
            ),
            "preferred_size": {
                "width": int(preferred.Width), "height": int(preferred.Height),
            },
            "content_fully_visible": bool(
                preferred.Width <= bounds.Width and preferred.Height <= bounds.Height
                and all(
                    item.Available and item.Placement == ToolStripItemPlacement.Main
                    for item in menu.Items
                    if item.Visible
                )
            ),
            "visible_action_count": sum(bool(item.Visible) for item in action_items),
            "visible_actions": [str(item.Tag) for item in action_items if item.Visible],
            "visible_labels": [str(item.Text) for item in action_items if item.Visible],
            "menu_open_count": int(overlay["menu_open_count"]),
            "menu_close_count": int(overlay["menu_close_count"]),
            "menu_close_reasons": list(overlay["menu_close_reasons"]),
        })

    form.Invoke(Action(show_and_inspect))
    return holder


def close_assistant_native_menu(window: webview.Window, reason: str) -> dict[str, Any]:
    import clr
    clr.AddReference("System.Windows.Forms")
    from System import Action
    from System.Windows.Forms import ToolStripDropDownCloseReason

    reasons = {
        "outside-pointer": ToolStripDropDownCloseReason.AppClicked,
        "escape": ToolStripDropDownCloseReason.Keyboard,
        "programmatic": ToolStripDropDownCloseReason.CloseCalled,
    }
    if reason not in reasons:
        raise ValueError("ASSISTANT_NATIVE_MENU_CLOSE_REASON_INVALID")
    form = window.native
    holder: dict[str, Any] = {}

    def close_and_inspect() -> None:
        form_handle = int(form.Handle.ToInt64())
        overlay = _ASSISTANT_NATIVE_OVERLAYS.get(form_handle)
        if overlay is None:
            raise RuntimeError("ASSISTANT_NATIVE_OVERLAY_NOT_READY")
        menu = overlay["menu"]
        menu.Close(reasons[reason])
        holder.update({
            "reason_requested": reason,
            "closed": not bool(menu.Visible),
            "menu_open_count": int(overlay["menu_open_count"]),
            "menu_close_count": int(overlay["menu_close_count"]),
            "menu_close_reasons": list(overlay["menu_close_reasons"]),
        })

    form.Invoke(Action(close_and_inspect))
    return holder


def inspect_assistant_native_interaction(window: webview.Window) -> dict[str, Any]:
    import clr
    clr.AddReference("System.Windows.Forms")
    from System import Action

    form = window.native
    holder: dict[str, Any] = {}

    def inspect() -> None:
        overlay = _ASSISTANT_NATIVE_OVERLAYS.get(int(form.Handle.ToInt64()))
        if overlay is None:
            raise RuntimeError("ASSISTANT_NATIVE_OVERLAY_NOT_READY")
        picture = overlay["picture"]
        holder.update({
            "primary_activation_count": int(overlay["primary_activation_count"]),
            "last_primary_status": overlay["last_primary_status"],
            "last_primary_receipt": overlay["last_primary_receipt"],
            "last_action": overlay["last_action"],
            "last_action_status": overlay["last_action_status"],
            "form_left": int(form.Left),
            "form_top": int(form.Top),
            "picture_left": int(picture.Left),
            "picture_top": int(picture.Top),
            "picture_width": int(picture.Width),
            "picture_height": int(picture.Height),
            "visible_screen_bounds": {
                "left": int(form.Left) + int(picture.Left),
                "top": int(form.Top) + int(picture.Top),
                "right": int(form.Left) + int(picture.Left) + int(picture.Width),
                "bottom": int(form.Top) + int(picture.Top) + int(picture.Height),
            },
            "drag_active": bool(overlay.get("drag")),
            "drag_timer_enabled": bool(overlay["drag_timer"].Enabled),
            "drag_move_count": int(overlay.get("drag_move_count", 0)),
            "last_drag_target": dict(overlay.get("last_drag_target") or {}),
            "picture_handle": int(picture.Handle.ToInt64()),
            "picture_visible": bool(picture.Visible),
            "picture_enabled": bool(picture.Enabled),
            "animation_state": overlay["animation_state"],
            "current_frame": overlay["current_frame"],
            "current_frame_sha256": overlay["current_frame_sha256"],
            "frame_sha256s": dict(overlay["frame_sha256s"]),
            "base_frame": overlay["base_frame"],
            "animation_locked": bool(overlay.get("animation_locked")),
            "interaction_animation_count": int(overlay.get("interaction_animation_count", 0)),
            "animation_completed_count": int(overlay.get("animation_completed_count", 0)),
            "auto_blink_count": int(overlay.get("auto_blink_count", 0)),
            "auto_blink_due_ms": overlay.get("auto_blink_due_ms"),
            "auto_blink_timer_enabled": bool(overlay["auto_blink_timer"].Enabled),
            "auto_blink_preserved_count": int(overlay.get("auto_blink_preserved_count", 0)),
            "presentation_apply_count": int(overlay["presentation_apply_count"]),
            "render_cache_count": len(overlay.get("render_cache") or {}),
            "render_cache_limit": int(overlay.get("render_cache_limit", 0)),
            "terminal_animation_started_count": int(overlay.get("terminal_animation_started_count", 0)),
            "coalesced_terminal_count": int(overlay.get("coalesced_terminal_count", 0)),
            "interrupted_presentation_count": int(overlay.get("interrupted_presentation_count", 0)),
            "receipt_record_error_count": int(overlay.get("receipt_record_error_count", 0)),
        })

    form.Invoke(Action(inspect))
    return holder


def invoke_assistant_native_auto_blink(window: webview.Window) -> dict[str, Any]:
    """Exercise the exact WinForms timer callback without waiting up to 18 seconds."""
    import clr
    clr.AddReference("System.Windows.Forms")
    from System import Action

    form = window.native
    holder: dict[str, Any] = {}

    def invoke() -> None:
        overlay = _ASSISTANT_NATIVE_OVERLAYS.get(int(form.Handle.ToInt64()))
        if overlay is None:
            raise RuntimeError("ASSISTANT_NATIVE_OVERLAY_NOT_READY")
        before_count = int(overlay.get("auto_blink_count", 0))
        due_before = overlay.get("auto_blink_due_ms")
        overlay["auto_blink_tick_handler"](None, None)
        holder.update({
            "due_before_ms": due_before,
            "auto_blink_count_before": before_count,
            "auto_blink_count_after": int(overlay.get("auto_blink_count", 0)),
            "animation_state": overlay.get("animation_state"),
            "animation_locked": bool(overlay.get("animation_locked")),
            "current_frame": overlay.get("current_frame"),
            "current_frame_sha256": overlay.get("current_frame_sha256"),
        })

    form.Invoke(Action(invoke))
    return {
        "schema_version": "DesktopAssistantNativeAutoBlinkTriggerReceipt-v1",
        **holder,
        "status": "PASS",
    }


def invoke_assistant_native_primary_click(window: webview.Window) -> dict[str, Any]:
    from ctypes import wintypes

    ready_deadline = time.monotonic() + 4.0
    before = inspect_assistant_native_interaction(window)
    while before["animation_locked"] and time.monotonic() < ready_deadline:
        time.sleep(0.05)
        before = inspect_assistant_native_interaction(window)
    if before["animation_locked"]:
        raise RuntimeError("ASSISTANT_NATIVE_PRIMARY_CLICK_LOCKED")
    handle = int(before["picture_handle"])
    user32 = ctypes.windll.user32
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = wintypes.LPARAM
    # Client coordinates are only used to construct a normal WinForms mouse
    # event; the native handler determines click-versus-drag from cursor delta.
    coordinate = (24 << 16) | 24
    user32.SendMessageW(handle, 0x0201, 0x0001, coordinate)  # WM_LBUTTONDOWN
    user32.SendMessageW(handle, 0x0202, 0x0000, coordinate)  # WM_LBUTTONUP
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        after = inspect_assistant_native_interaction(window)
        if (
            after["primary_activation_count"] == before["primary_activation_count"] + 1
            and after["interaction_animation_count"] == before["interaction_animation_count"] + 1
        ):
            return {"before": before, "after": after, "status": "PASS"}
        time.sleep(0.05)
    raise RuntimeError("ASSISTANT_NATIVE_PRIMARY_CLICK_TIMEOUT")


def invoke_assistant_physical_primary_click(window: webview.Window) -> dict[str, Any]:
    """Exercise the real pointer path; unlike SendMessage this hits Windows input routing."""
    from ctypes import wintypes

    class MouseInput(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG), ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class InputUnion(ctypes.Union):
        _fields_ = [("mi", MouseInput)]

    class Input(ctypes.Structure):
        _anonymous_ = ("union",)
        _fields_ = [("type", wintypes.DWORD), ("union", InputUnion)]

    class NativePoint(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    ready_deadline = time.monotonic() + 4.0
    before = inspect_assistant_native_interaction(window)
    while before["animation_locked"] and time.monotonic() < ready_deadline:
        time.sleep(0.05)
        before = inspect_assistant_native_interaction(window)
    if before["animation_locked"]:
        raise RuntimeError("ASSISTANT_PHYSICAL_PRIMARY_CLICK_LOCKED")
    form = window.native
    target: dict[str, int] = {}

    import clr
    clr.AddReference("System.Windows.Forms")
    from System import Action
    from System.Drawing import Point

    def locate() -> None:
        overlay = _ASSISTANT_NATIVE_OVERLAYS.get(int(form.Handle.ToInt64()))
        if overlay is None:
            raise RuntimeError("ASSISTANT_NATIVE_OVERLAY_NOT_READY")
        form.TopMost = True
        form.Enabled = True
        form.Show()
        form.BringToFront()
        form.Activate()
        picture = overlay["picture"]
        picture.Visible = True
        picture.Enabled = True
        picture.BringToFront()
        picture.Refresh()
        form.Refresh()
        origin = picture.PointToScreen(Point(0, 0))
        point = picture.PointToScreen(Point(max(1, picture.Width // 2), max(1, picture.Height // 2)))
        target.update({
            "x": int(point.X),
            "y": int(point.Y),
            "picture_left": int(origin.X),
            "picture_top": int(origin.Y),
            "picture_width": int(picture.Width),
            "picture_height": int(picture.Height),
            "form_handle": int(form.Handle.ToInt64()),
            "picture_handle": int(picture.Handle.ToInt64()),
            "picture_visible": bool(picture.Visible),
            "picture_enabled": bool(picture.Enabled),
            "form_visible": bool(form.Visible),
            "form_enabled": bool(form.Enabled),
            "form_opacity": float(form.Opacity),
        })

    form.Invoke(Action(locate))
    user32 = ctypes.windll.user32
    original = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(original)):
        raise RuntimeError("ASSISTANT_CURSOR_READ_FAILED")
    user32.WindowFromPoint.argtypes = [NativePoint]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL

    def inspect_pointer_target() -> tuple[int, int]:
        hit = int(user32.WindowFromPoint(NativePoint(target["x"], target["y"])) or 0)
        root = int(user32.GetAncestor(hit, 2) or 0) if hit else 0
        return hit, root

    hit_window, hit_root = inspect_pointer_target()
    if hit_root != target["form_handle"]:
        raised = bool(user32.SetWindowPos(
            target["form_handle"], -1, 0, 0, 0, 0,
            0x0001 | 0x0002 | 0x0040,
        ))
        target["set_window_pos"] = raised
        time.sleep(0.08)
        hit_window, hit_root = inspect_pointer_target()
    if hit_root != target["form_handle"]:
        centre_x = target["picture_width"] // 2
        centre_y = target["picture_height"] // 2
        candidates = sorted(
            (
                (local_x - centre_x) ** 2 + (local_y - centre_y) ** 2,
                local_x,
                local_y,
            )
            for local_y in range(2, target["picture_height"] - 1, 3)
            for local_x in range(2, target["picture_width"] - 1, 3)
        )
        for _distance, local_x, local_y in candidates:
            candidate_x = target["picture_left"] + local_x
            candidate_y = target["picture_top"] + local_y
            candidate_hit = int(user32.WindowFromPoint(NativePoint(candidate_x, candidate_y)) or 0)
            candidate_root = int(user32.GetAncestor(candidate_hit, 2) or 0) if candidate_hit else 0
            if candidate_root == target["form_handle"]:
                target.update({
                    "x": candidate_x,
                    "y": candidate_y,
                    "local_x": local_x,
                    "local_y": local_y,
                })
                hit_window, hit_root = candidate_hit, candidate_root
                break
    target.update({"hit_window": hit_window, "hit_root": hit_root})
    if hit_root != target["form_handle"]:
        user32.SetCursorPos(original.x, original.y)
        raise RuntimeError(
            "ASSISTANT_PHYSICAL_TARGET_NOT_TOPMOST:"
            + json.dumps(target, ensure_ascii=False, sort_keys=True)
        )
    if not user32.SetCursorPos(target["x"], target["y"]):
        user32.SetCursorPos(original.x, original.y)
        raise RuntimeError("ASSISTANT_CURSOR_POSITION_FAILED")
    time.sleep(0.08)
    inputs = (Input * 2)()
    inputs[0].type = 0
    inputs[0].mi = MouseInput(0, 0, 0, 0x0002, 0, None)
    inputs[1].type = 0
    inputs[1].mi = MouseInput(0, 0, 0, 0x0004, 0, None)
    try:
        sent = int(user32.SendInput(2, ctypes.byref(inputs), ctypes.sizeof(Input)))
        if sent != 2:
            raise RuntimeError("ASSISTANT_SEND_INPUT_INCOMPLETE")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            after = inspect_assistant_native_interaction(window)
            if (
                after["primary_activation_count"] == before["primary_activation_count"] + 1
                and after["interaction_animation_count"] == before["interaction_animation_count"] + 1
            ):
                settle_deadline = time.monotonic() + 3.0
                settled = inspect_assistant_native_interaction(window)
                while settled["animation_locked"] and time.monotonic() < settle_deadline:
                    time.sleep(0.05)
                    settled = inspect_assistant_native_interaction(window)
                if settled["animation_locked"]:
                    raise RuntimeError("ASSISTANT_PHYSICAL_INTERACTION_DID_NOT_SETTLE")
                return {
                    "before": before,
                    "after": after,
                    "settled": settled,
                    "target_screen": target,
                    "send_input_count": sent,
                    "status": "PASS",
                }
            time.sleep(0.05)
        raise RuntimeError("ASSISTANT_PHYSICAL_PRIMARY_CLICK_TIMEOUT")
    finally:
        user32.SetCursorPos(original.x, original.y)


def invoke_assistant_physical_drag(
    window: webview.Window, dx: int = -120, dy: int = -80
) -> dict[str, Any]:
    """Drag the native pet through the real Windows pointer route."""
    from ctypes import wintypes

    class MouseInput(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG), ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class InputUnion(ctypes.Union):
        _fields_ = [("mi", MouseInput)]

    class Input(ctypes.Structure):
        _anonymous_ = ("union",)
        _fields_ = [("type", wintypes.DWORD), ("union", InputUnion)]

    import clr
    clr.AddReference("System.Windows.Forms")
    from System import Action
    from System.Drawing import Point

    form = window.native
    target: dict[str, int] = {}

    def locate() -> None:
        overlay = _ASSISTANT_NATIVE_OVERLAYS.get(int(form.Handle.ToInt64()))
        if overlay is None:
            raise RuntimeError("ASSISTANT_NATIVE_OVERLAY_NOT_READY")
        form.TopMost = True
        form.Enabled = True
        form.Show()
        form.BringToFront()
        picture = overlay["picture"]
        picture.Visible = True
        picture.Enabled = True
        picture.BringToFront()
        picture.Refresh()
        point = picture.PointToScreen(
            Point(max(2, picture.Width // 2), max(2, picture.Height // 2))
        )
        target.update({
            "start_x": int(point.X),
            "start_y": int(point.Y),
            "form_handle": int(form.Handle.ToInt64()),
        })

    form.Invoke(Action(locate))
    before = inspect_assistant_native_interaction(window)
    user32 = ctypes.windll.user32
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    original = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(original)):
        raise RuntimeError("ASSISTANT_CURSOR_READ_FAILED")
    if not user32.SetCursorPos(target["start_x"], target["start_y"]):
        raise RuntimeError("ASSISTANT_CURSOR_POSITION_FAILED")
    time.sleep(0.08)

    down = Input()
    down.type = 0
    down.mi = MouseInput(0, 0, 0, 0x0002, 0, None)
    up = Input()
    up.type = 0
    up.mi = MouseInput(0, 0, 0, 0x0004, 0, None)
    released = False
    try:
        # A synthetic pointer probe must wait for the shaped top-level HWND to
        # become the real hit target. Under a presentation flood, BringToFront
        # and Windows hit-test publication can trail the UI callback briefly;
        # moving before this handshake turns the drag into an unrelated desktop
        # gesture and produces a false zero-distance result.
        hit_deadline = time.monotonic() + 2.5
        hit_window = 0
        hit_root = 0
        hit_point = wintypes.POINT(target["start_x"], target["start_y"])
        while time.monotonic() < hit_deadline:
            hit_window = int(user32.WindowFromPoint(hit_point) or 0)
            hit_root = int(user32.GetAncestor(hit_window, 2) or 0) if hit_window else 0
            if hit_root == target["form_handle"]:
                break
            time.sleep(0.025)
        if hit_root != target["form_handle"]:
            raise RuntimeError(
                f"ASSISTANT_DRAG_HIT_TARGET_NOT_READY:{hit_window},{hit_root}"
            )
        if int(user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(Input))) != 1:
            raise RuntimeError("ASSISTANT_DRAG_MOUSE_DOWN_FAILED")
        # Do not race queued WinForms invokes. The movement begins only after
        # the production MouseDown handler has acquired drag ownership.
        down_deadline = time.monotonic() + 2.5
        down_state = inspect_assistant_native_interaction(window)
        while not down_state["drag_active"] and time.monotonic() < down_deadline:
            time.sleep(0.025)
            down_state = inspect_assistant_native_interaction(window)
        if not down_state["drag_active"]:
            raise RuntimeError("ASSISTANT_DRAG_MOUSE_DOWN_NOT_CAPTURED")
        steps = 12
        for step in range(1, steps + 1):
            x = target["start_x"] + int(round(dx * step / steps))
            y = target["start_y"] + int(round(dy * step / steps))
            if not user32.SetCursorPos(x, y):
                raise RuntimeError("ASSISTANT_DRAG_CURSOR_MOVE_FAILED")
            time.sleep(0.025)
        if int(user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(Input))) != 1:
            raise RuntimeError("ASSISTANT_DRAG_MOUSE_UP_FAILED")
        released = True
        deadline = time.monotonic() + 5.0
        after = inspect_assistant_native_interaction(window)
        while (after["drag_active"] or after["drag_timer_enabled"]) and time.monotonic() < deadline:
            time.sleep(0.05)
            after = inspect_assistant_native_interaction(window)
        actual_dx = int(after["form_left"]) - int(before["form_left"])
        actual_dy = int(after["form_top"]) - int(before["form_top"])
        follows_pointer = abs(actual_dx - int(dx)) <= 5 and abs(actual_dy - int(dy)) <= 5
        if not follows_pointer:
            raise RuntimeError(
                f"ASSISTANT_DRAG_DID_NOT_FOLLOW_POINTER:{actual_dx},{actual_dy}"
            )
        if after["primary_activation_count"] != before["primary_activation_count"]:
            raise RuntimeError("ASSISTANT_DRAG_TRIGGERED_PRIMARY_ACTION")
        return {
            "before": before,
            "after": after,
            "requested_delta": {"x": int(dx), "y": int(dy)},
            "actual_delta": {"x": actual_dx, "y": actual_dy},
            "follows_pointer": follows_pointer,
            "status": "PASS",
        }
    finally:
        if not released:
            user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(Input))
        user32.SetCursorPos(original.x, original.y)


def capture_screen_rectangle(bounds: dict[str, int], destination: Path) -> dict[str, Any]:
    import clr
    clr.AddReference("System.Drawing")
    from System.Drawing import Bitmap, CopyPixelOperation, Graphics, Point, Size
    from System.Drawing.Imaging import ImageFormat

    if destination.exists():
        raise FileExistsError(destination)
    capture_root = _windows_short_existing_path(
        Path(tempfile.gettempdir()).resolve()
    ) / "Memorive-Capture"
    capture_root.mkdir(parents=True, exist_ok=True)
    temporary = capture_root / (
        f"capture-{os.getpid()}-{threading.get_ident()}-{time.time_ns()}.png"
    )
    width = int(bounds["width"])
    height = int(bounds["height"])
    bitmap = Bitmap(width, height)
    graphics = Graphics.FromImage(bitmap)
    try:
        graphics.CopyFromScreen(
            Point(int(bounds["left"]), int(bounds["top"])),
            Point(0, 0),
            Size(width, height),
            CopyPixelOperation.SourceCopy,
        )
        bitmap.Save(str(temporary), ImageFormat.Png)
    finally:
        graphics.Dispose()
        bitmap.Dispose()
    try:
        with temporary.open("rb") as source, destination.open("xb") as target:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                target.write(chunk)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
    }


def inspect_assistant_desktop_transparency(
    window: webview.Window, evidence_dir: Path, phase: str
) -> dict[str, Any]:
    import clr
    clr.AddReference("System.Drawing")
    clr.AddReference("System.Windows.Forms")
    from System import Action, Array, Byte
    from System.Drawing import Bitmap
    from System.IO import MemoryStream

    form = window.native
    bounds = inspect_assistant_visibility(window)

    def reveal() -> None:
        form.Show()
        form.TopMost = True
        form.BringToFront()
        form.Update()

    def conceal() -> None:
        form.Hide()

    def flush_desktop_compositor() -> None:
        try:
            ctypes.windll.dwmapi.DwmFlush()
        except Exception:
            time.sleep(0.02)

    visible_path = evidence_dir / f"pywebview_assistant_desktop_{phase}.png"
    underlay_before_path = evidence_dir / f"pywebview_assistant_underlay_before_{phase}.png"
    underlay_after_path = evidence_dir / f"pywebview_assistant_underlay_after_{phase}.png"
    try:
        form.Invoke(Action(conceal))
        flush_desktop_compositor()
        underlay_before_capture = capture_screen_rectangle(bounds, underlay_before_path)
        form.Invoke(Action(reveal))
        flush_desktop_compositor()
        visible_capture = capture_screen_rectangle(bounds, visible_path)
        form.Invoke(Action(conceal))
        flush_desktop_compositor()
        underlay_after_capture = capture_screen_rectangle(bounds, underlay_after_path)
    finally:
        form.Invoke(Action(reveal))
        flush_desktop_compositor()

    def bitmap_from_bytes(path: Path) -> tuple[Any, Any]:
        stream = MemoryStream(Array[Byte](path.read_bytes()))
        return Bitmap(stream), stream

    visible_bitmap, visible_stream = bitmap_from_bytes(visible_path)
    underlay_before_bitmap, underlay_before_stream = bitmap_from_bytes(
        underlay_before_path
    )
    underlay_after_bitmap, underlay_after_stream = bitmap_from_bytes(
        underlay_after_path
    )
    try:
        width = int(visible_bitmap.Width)
        height = int(visible_bitmap.Height)
        if any(
            width != int(bitmap.Width) or height != int(bitmap.Height)
            for bitmap in (underlay_before_bitmap, underlay_after_bitmap)
        ):
            raise RuntimeError("ASSISTANT_DESKTOP_CAPTURE_SIZE_MISMATCH")
        patch = max(6, min(12, width // 10, height // 10))
        corner_points: set[tuple[int, int]] = set()
        for start_x, start_y in (
            (0, 0), (width - patch, 0),
            (0, height - patch), (width - patch, height - patch),
        ):
            for x in range(start_x, start_x + patch):
                for y in range(start_y, start_y + patch):
                    corner_points.add((x, y))
        corner_exact_matches = 0
        corner_tolerant_matches = 0
        corner_max_rgb_delta = 0
        corner_dynamic_underlay_pixels = 0
        tolerance = 24
        for x, y in corner_points:
            visible_pixel = visible_bitmap.GetPixel(x, y)
            before_pixel = underlay_before_bitmap.GetPixel(x, y)
            after_pixel = underlay_after_bitmap.GetPixel(x, y)
            if visible_pixel.ToArgb() in {before_pixel.ToArgb(), after_pixel.ToArgb()}:
                corner_exact_matches += 1
            deltas = []
            bracket_delta = 0
            underlay_delta = 0
            for channel in ("R", "G", "B"):
                visible_value = int(getattr(visible_pixel, channel))
                before_value = int(getattr(before_pixel, channel))
                after_value = int(getattr(after_pixel, channel))
                deltas.append(min(
                    abs(visible_value - before_value),
                    abs(visible_value - after_value),
                ))
                lower, upper = sorted((before_value, after_value))
                bracket_delta += max(lower - visible_value, 0, visible_value - upper)
                underlay_delta += abs(before_value - after_value)
            rgb_delta = sum(deltas)
            corner_max_rgb_delta = max(corner_max_rgb_delta, rgb_delta)
            if underlay_delta > tolerance:
                corner_dynamic_underlay_pixels += 1
            # The visible frame is bracketed by two compositor-synchronised
            # underlay frames. A transparent pixel either matches one side or
            # falls between them while a live wallpaper advances.
            if rgb_delta <= tolerance or bracket_delta <= 12:
                corner_tolerant_matches += 1
        changed_pixels = 0
        for x in range(width):
            for y in range(height):
                visible_pixel = visible_bitmap.GetPixel(x, y)
                before_pixel = underlay_before_bitmap.GetPixel(x, y)
                after_pixel = underlay_after_bitmap.GetPixel(x, y)
                before_delta = sum(
                    abs(int(getattr(visible_pixel, channel)) - int(getattr(before_pixel, channel)))
                    for channel in ("R", "G", "B")
                )
                after_delta = sum(
                    abs(int(getattr(visible_pixel, channel)) - int(getattr(after_pixel, channel)))
                    for channel in ("R", "G", "B")
                )
                if min(before_delta, after_delta) > tolerance:
                    changed_pixels += 1
    finally:
        visible_bitmap.Dispose()
        underlay_before_bitmap.Dispose()
        underlay_after_bitmap.Dispose()
        visible_stream.Dispose()
        underlay_before_stream.Dispose()
        underlay_after_stream.Dispose()

    corner_exact_ratio = corner_exact_matches / len(corner_points)
    corner_ratio = corner_tolerant_matches / len(corner_points)
    # When the desktop below the transparent form changes between the two
    # bracket captures (live wallpaper, WebView repaint, notification fade),
    # tolerant matching is allowed a small dynamic margin. Exact corner
    # matching still has to stay high, so an opaque rectangular host cannot
    # pass by merely falling between the two underlay colours.
    minimum_corner_ratio = 0.82 if corner_dynamic_underlay_pixels else 0.90
    minimum_corner_exact_ratio = 0.75
    background_transparent = (
        corner_ratio >= minimum_corner_ratio
        and corner_exact_ratio >= minimum_corner_exact_ratio
        and changed_pixels >= 100
    )
    return {
        "schema_version": "DesktopAssistantAssistantDesktopTransparencyReceipt-v2",
        "bounds": bounds,
        "visible_capture": visible_capture,
        "underlay_before_capture": underlay_before_capture,
        "underlay_after_capture": underlay_after_capture,
        "corner_sample_count": len(corner_points),
        "corner_exact_match_count": corner_exact_matches,
        "corner_exact_match_ratio": corner_exact_ratio,
        "corner_tolerant_match_count": corner_tolerant_matches,
        "corner_rgb_delta_tolerance": tolerance,
        "corner_max_rgb_delta": corner_max_rgb_delta,
        "corner_dynamic_underlay_pixel_count": corner_dynamic_underlay_pixels,
        "dynamic_underlay_detected": corner_dynamic_underlay_pixels > 0,
        "transparent_corner_match_ratio": corner_ratio,
        "minimum_transparent_corner_match_ratio": minimum_corner_ratio,
        "minimum_corner_exact_match_ratio": minimum_corner_exact_ratio,
        "foreground_non_underlay_pixel_count": changed_pixels,
        "native_background_transparent": background_transparent,
        "status": "PASS" if background_transparent else "FAIL",
    }


def bring_product_window_to_front(window: webview.Window) -> None:
    import clr
    clr.AddReference("System.Windows.Forms")
    from System import Action
    from System.Windows.Forms import FormWindowState

    form = window.native

    def activate() -> None:
        form.Show()
        if form.WindowState == FormWindowState.Minimized:
            form.WindowState = FormWindowState.Normal
        form.BringToFront()
        form.Activate()

    # ``loaded`` handlers for the product and assistant WebViews can overlap.
    # A synchronous cross-form Invoke here creates a lock-order cycle with a
    # concurrent assistant presentation. Queue focus restoration on the UI
    # loop and return immediately so terminal-event delivery cannot deadlock.
    form.BeginInvoke(Action(activate))


def configure_main_window_behavior(
    window: webview.Window,
    api: ProductApi,
    *,
    attach_close_handler: bool = True,
) -> dict[str, Any]:
    import clr
    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    clr.AddReference("System")
    from System import Action
    from System.Drawing import SystemIcons
    from System.Media import SystemSounds
    from System.Windows.Forms import (
        ContextMenuStrip, FormWindowState, MessageBox, MessageBoxButtons,
        MessageBoxIcon, NotifyIcon, Timer as FormsTimer, ToolStripMenuItem,
        ToolTipIcon,
    )

    form = window.native
    holder: dict[str, Any] = {}

    def configure() -> None:
        form_handle = int(form.Handle.ToInt64())
        if form_handle in _MAIN_WINDOW_BEHAVIORS:
            holder.update({"already_configured": True, "status": "PASS"})
            return
        notify = NotifyIcon()
        notify.Text = BRAND
        notify.Icon = form.Icon if form.Icon is not None else SystemIcons.Application
        notify.Visible = False
        menu = ContextMenuStrip()
        open_item = ToolStripMenuItem("打开 "+BRAND)
        exit_item = ToolStripMenuItem("退出 "+BRAND)
        menu.Items.Add(open_item)
        menu.Items.Add(exit_item)
        notify.ContextMenuStrip = menu
        notification_timer = FormsTimer()
        notification_timer.Interval = 7000
        state: dict[str, Any] = {
            "notify": notify,
            "menu": menu,
            "handlers": [],
            "hide_count": 0,
            "show_count": 0,
            "ask_count": 0,
            "exit_request_count": 0,
            "last_policy": None,
            "notification_present_count": 0,
            "notification_click_count": 0,
            "notification_sound_count": 0,
            "last_notification": None,
            "blocked_file_navigation_count": 0,
        }

        core_webview = form.webview.CoreWebView2
        initial_uri = str(form.webview.Source or "")
        allowed_file_document = initial_uri.split("#", 1)[0].split("?", 1)[0]

        def on_navigation_starting(_sender, event) -> None:
            uri = str(event.Uri or "")
            candidate = uri.split("#", 1)[0].split("?", 1)[0]
            if (
                uri.casefold().startswith("file:")
                and candidate.casefold() != allowed_file_document.casefold()
            ):
                event.Cancel = True
                state["blocked_file_navigation_count"] += 1

        core_webview.NavigationStarting += on_navigation_starting
        state["handlers"].append(on_navigation_starting)

        def show_main(_sender=None, _event=None) -> None:
            form.Show()
            if form.WindowState == FormWindowState.Minimized:
                form.WindowState = FormWindowState.Normal
            form.BringToFront()
            form.Activate()
            notify.Visible = False
            state["show_count"] += 1

        def request_exit(_sender=None, _event=None) -> None:
            state["exit_request_count"] += 1
            notify.Visible = False
            api.exit_application({})

        def on_visible_changed(_sender, _event) -> None:
            if api._exit_requested:
                notify.Visible = False
            elif form.Visible:
                notify.Visible = False
            else:
                notify.Visible = True

        def on_disposed(_sender, _event) -> None:
            notification_timer.Stop()
            notification_timer.Dispose()
            notify.Visible = False
            notify.Dispose()
            menu.Dispose()
            _MAIN_WINDOW_BEHAVIORS.pop(form_handle, None)

        def on_notification_timer(_sender, _event) -> None:
            notification_timer.Stop()
            if bool(form.Visible):
                notify.Visible = False

        def present_notification(request: dict[str, Any]) -> dict[str, Any]:
            if not isinstance(request, dict) or request.get("status") != "DELIVER":
                raise ValueError("WINDOWS_NOTIFICATION_PLAN_INVALID")
            observed: dict[str, Any] = {}

            def show_balloon() -> None:
                notify.BalloonTipTitle = str(request["title"])[:63]
                notify.BalloonTipText = str(request["body"])[:255]
                notify.BalloonTipIcon = getattr(ToolTipIcon, "None")
                notify.Visible = True
                notify.ShowBalloonTip(6000)
                if bool(request.get("play_sound")):
                    SystemSounds.Asterisk.Play()
                    state["notification_sound_count"] += 1
                state["notification_present_count"] += 1
                state["last_notification"] = dict(request)
                notification_timer.Stop()
                notification_timer.Start()
                observed.update(
                    {
                        "notify_icon_visible": bool(notify.Visible),
                        "presentation_count": state["notification_present_count"],
                        "sound_played": bool(request.get("play_sound")),
                        "sound_count": state["notification_sound_count"],
                    }
                )

            if bool(form.InvokeRequired):
                form.Invoke(Action(show_balloon))
            else:
                show_balloon()
            return {
                "schema_version": "DesktopWindowsNotifyIconPresentationReceipt-v1",
                **observed,
                "native_notify_icon_api_called": True,
                "status": "PASS",
            }

        def on_balloon_clicked(_sender, _event) -> None:
            request = state.get("last_notification")
            if not isinstance(request, dict):
                return
            state["notification_click_count"] += 1
            threading.Thread(
                target=lambda: api.activate_notification(
                    {
                        "source_message_id": request.get("source_message_id"),
                        "activation_locator": request.get("activation_locator"),
                    }
                ),
                daemon=True,
            ).start()

        def on_closing() -> bool:
            if api._exit_requested:
                notify.Visible = False
                return True
            if getattr(api, "_main_chrome", None) is not None:
                state["ask_count"] += 1
                return api._main_chrome.request_close()
            policy = api.get_close_policy()
            state["last_policy"] = dict(policy)
            action = policy["action"]
            if action == "HIDE":
                form.Hide()
                notify.Visible = True
                state["hide_count"] += 1
                return False
            if action == "EXIT":
                request_exit()
                return False

            state["ask_count"] += 1
            active = int(policy["active_job_count"])
            allow_background = bool(policy["keep_tasks_in_background"])
            if active and not allow_background:
                answer = MessageBox.Show(
                    f"仍有 {active} 个任务处于活动状态，且后台继续运行已关闭。\n\n是否退出 {BRAND}？",
                    BRAND,
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Warning,
                )
                if str(answer) == "Yes":
                    request_exit()
                return False
            detail = (
                f"当前仍有 {active} 个活动任务。\n\n"
                if active else ""
            )
            answer = MessageBox.Show(
                detail + "选择“是”退出；选择“否”最小化到托盘；选择“取消”返回程序。",
                "关闭 "+BRAND,
                MessageBoxButtons.YesNoCancel,
                MessageBoxIcon.Question,
            )
            rendered = str(answer)
            if rendered == "Yes":
                request_exit()
            elif rendered == "No":
                decision = api.get_close_policy("HIDE")
                if decision["action"] == "HIDE":
                    form.Hide()
                    notify.Visible = True
                    state["hide_count"] += 1
            return False

        open_item.Click += show_main
        def request_exit_intent(_sender=None, _event=None) -> None:
            if getattr(api, "_main_chrome", None) is not None:
                show_main()
                api._main_chrome.request_close(force_ask=True)
            else:
                request_exit()

        def localize_menu(_sender=None, _event=None) -> None:
            language = api._current_preferences().get("language", "zh-CN")
            open_item.Text, exit_item.Text = {
                "en-US": ("Open "+BRAND, "Exit "+BRAND),
                "ja-JP": (BRAND+" を開く", BRAND+" を終了"),
            }.get(language, ("打开 "+BRAND, "退出 "+BRAND))

        exit_item.Click += request_exit_intent
        menu.Opening += localize_menu
        notify.DoubleClick += show_main
        notify.BalloonTipClicked += on_balloon_clicked
        notification_timer.Tick += on_notification_timer
        form.VisibleChanged += on_visible_changed
        form.Disposed += on_disposed
        if attach_close_handler:
            window.events.closing += on_closing
        state["handlers"].extend((
            show_main, request_exit, on_visible_changed, on_disposed,
            on_notification_timer, on_balloon_clicked, on_navigation_starting,
            request_exit_intent, localize_menu,
        ))
        if attach_close_handler:
            state["handlers"].append(on_closing)
        state["notification_timer"] = notification_timer
        state["present_notification"] = present_notification
        _MAIN_WINDOW_BEHAVIORS[form_handle] = state
        api.attach_notification_presenter(present_notification)
        holder.update({
            "already_configured": False,
            "notify_icon_ready": True,
            "close_handler_ready": attach_close_handler,
            "native_notification_presenter_ready": True,
            "status": "PASS",
        })

    form.Invoke(Action(configure))
    return {"schema_version": "DesktopMainWindowBehaviorReceipt-v1", **holder}


def _read_primary_instance_pid(descriptor: Path | None) -> int | None:
    if descriptor is None:
        return None
    try:
        payload = json.loads(descriptor.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != "DesktopSingleInstanceDescriptor-v1":
        return None
    if payload.get("application_id") != "memorive-desktop":
        return None
    if payload.get("secret_or_credential_present") is not False:
        return None
    primary_pid = payload.get("primary_pid")
    if isinstance(primary_pid, bool) or not isinstance(primary_pid, int) or primary_pid <= 0:
        return None
    return primary_pid


def activate_existing_product_window(
    executable: Path,
    *,
    timeout_seconds: float = 5.0,
    instance_descriptor: Path | None = None,
) -> dict[str, Any]:
    from ctypes import wintypes

    descriptor_primary_pid = _read_primary_instance_pid(instance_descriptor)
    receipt: dict[str, Any] = {
        "schema_version": "DesktopAssistantSecondaryLaunchActivationReceipt-v1",
        "requested_executable": str(executable.resolve()),
        "instance_descriptor": (
            str(instance_descriptor.resolve()) if instance_descriptor is not None else None
        ),
        "descriptor_primary_pid": descriptor_primary_pid,
        "window_title": BRAND,
        "navigation_invoked": False,
        "new_product_instance_created": False,
        "status": "TARGET_NOT_FOUND",
    }
    if os.name != "nt":
        return receipt

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    process_query_limited_information = 0x1000
    expected = os.path.normcase(os.path.realpath(str(executable)))

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    def process_image(process_id: int) -> str | None:
        handle = kernel32.OpenProcess(process_query_limited_information, False, process_id)
        if not handle:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return os.path.normcase(os.path.realpath(buffer.value))
        finally:
            kernel32.CloseHandle(handle)

    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindowAsync.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    deadline = time.monotonic() + timeout_seconds
    candidates: list[tuple[int, int, bool, bool]] = []
    while time.monotonic() < deadline and not candidates:
        candidates = []
        if descriptor_primary_pid is None:
            descriptor_primary_pid = _read_primary_instance_pid(instance_descriptor)
            receipt["descriptor_primary_pid"] = descriptor_primary_pid

        @callback_type
        def enumerate_window(hwnd: int, _lparam: int) -> bool:
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            if title.value != BRAND:
                return True
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            candidate_pid = int(process_id.value)
            candidate_image = process_image(candidate_pid)
            executable_path_exact = candidate_image == expected
            descriptor_primary_pid_exact = (
                descriptor_primary_pid is not None
                and candidate_pid == descriptor_primary_pid
            )
            executable_name_exact = bool(
                candidate_image
                and Path(candidate_image).name.casefold() == Path(expected).name.casefold()
            )
            if executable_path_exact or (
                descriptor_primary_pid_exact and executable_name_exact
            ):
                candidates.append(
                    (
                        int(hwnd),
                        candidate_pid,
                        executable_path_exact,
                        descriptor_primary_pid_exact,
                    )
                )
            return True

        user32.EnumWindows(enumerate_window, 0)
        if not candidates:
            time.sleep(0.1)

    if not candidates:
        return receipt

    hwnd, process_id, executable_path_exact, descriptor_primary_pid_exact = candidates[0]
    restored = bool(user32.ShowWindowAsync(hwnd, 9 if user32.IsIconic(hwnd) else 5))
    topmost_raise = bool(user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040))
    topmost_release = bool(user32.SetWindowPos(hwnd, wintypes.HWND(-2), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040))
    brought = bool(user32.BringWindowToTop(hwnd))
    foreground = bool(user32.SetForegroundWindow(hwnd))
    receipt.update({
        "target_process_id": process_id,
        "target_window_handle": hwnd,
        "executable_path_exact": executable_path_exact,
        "descriptor_primary_pid_exact": descriptor_primary_pid_exact,
        "target_selection": (
            "EXACT_EXECUTABLE_PATH"
            if executable_path_exact
            else "INSTANCE_DESCRIPTOR_PID"
        ),
        "show_or_restore_requested": True,
        "show_window_previous_visibility": restored,
        "temporary_topmost_raise": topmost_raise,
        "temporary_topmost_release": topmost_release,
        "bring_window_to_top": brought,
        "set_foreground_window": foreground,
        "status": "PASS" if topmost_raise and topmost_release and brought else "PARTIAL",
    })
    return receipt


def run_test(window: webview.Window, assistant_window: webview.Window, api: ProductApi,
             evidence_dir: Path, bundle: Path, assistant_bundle: Path,
             test_pdf: Path, phase: str, result: dict[str, Any],
             remote_requests: list[dict[str, str]],
             loopback_requests: list[dict[str, str]],
             drop_runtime: dict[str, Any],
             startup_diagnostics: dict[str, Any]) -> None:
    receipt_path = evidence_dir / f"pywebview_product_{phase}.json"
    try:
        if not window.events.loaded.wait(30):
            log_webview_startup_event(
                "product_window_loaded_timeout",
                timeout_seconds=30,
            )
            raise TimeoutError("product WebView2 loaded event timed out")
        if not drop_runtime["ready"].wait(15):
            raise TimeoutError("native inbox drop handler attach timed out")
        if not drop_runtime.get("attached"):
            raise RuntimeError(
                "native inbox drop handler did not attach: "
                + str(drop_runtime.get("attach_error") or "unknown")
            )
        if not assistant_window.events.loaded.wait(30):
            log_webview_startup_event(
                "assistant_window_loaded_timeout",
                timeout_seconds=30,
            )
            raise TimeoutError("assistant WebView2 loaded event timed out")
        configure_assistant_native(
            assistant_window,
            api,
            assistant_pet_sheet_path(resource_root()),
            reveal=True,
            expression_assets=assistant_expression_asset_paths(resource_root()),
        )
        time.sleep(0.25)
        assistant_projection = evaluate_promise(
            window,
            "window.pywebview.api.assistant_bootstrap({reduced_motion:false})",
        )
        assistant_restore = evaluate_promise(
            window,
            "window.pywebview.api.assistant_shortcut({action_id:'RESTORE_DEFAULT_PLACEMENT'})",
        )
        assistant_placement = api.assistant_initial_placement()
        assistant_inspect = assistant_window.evaluate_js(
            "window.__Desktop_ASSISTANT_RUNTIME__.inspect()"
        )
        assistant_native = inspect_assistant_native(assistant_window, assistant_inspect)
        try:
            assistant_initial_physical_click = invoke_assistant_physical_primary_click(
                assistant_window
            )
        except Exception as error:
            raise RuntimeError(
                "ASSISTANT_INITIAL_POINTER_PROBE_FAILED:"
                + json.dumps(assistant_native, ensure_ascii=False, default=str)
            ) from error
        assistant_window.evaluate_js(
            "window.__Desktop_ASSISTANT_RUNTIME__.setAutoRefreshEnabled(false)"
        )
        assistant_native_animation_matrix: dict[str, Any] = {}
        for animation, attention, reason in (
            ("WORKING", "INFO", "JOB_RUNNING"),
            ("ATTENTION", "WARNING", "MESSAGE_WAITING_ACTION"),
            ("SUCCESS", "SUCCESS", "JOB_TERMINAL_SUCCESS"),
            ("ERROR", "ERROR", "MESSAGE_NEEDS_ATTENTION"),
        ):
            motion_before = inspect_assistant_native_interaction(assistant_window)
            motion_receipt = apply_assistant_native_presentation(
                assistant_window,
                {
                    "animation_state": animation,
                    "attention_state": attention,
                    "reason_code": reason,
                    "reduced_motion": False,
                },
            )
            motion_started = inspect_assistant_native_interaction(assistant_window)
            motion_deadline = time.monotonic() + 3.5
            motion_settled = motion_started
            while motion_settled["animation_locked"] and time.monotonic() < motion_deadline:
                time.sleep(0.05)
                motion_settled = inspect_assistant_native_interaction(assistant_window)
            if motion_settled["animation_locked"]:
                raise RuntimeError(f"ASSISTANT_NATIVE_{animation}_DID_NOT_SETTLE")
            assistant_native_animation_matrix[animation] = {
                "receipt": motion_receipt,
                "before": motion_before,
                "started": motion_started,
                "settled": motion_settled,
            }
        assistant_window.evaluate_js(
            "window.__Desktop_ASSISTANT_RUNTIME__.setAutoRefreshEnabled(true)"
        )
        assistant_projection = evaluate_promise(
            window,
            "window.pywebview.api.assistant_bootstrap({reduced_motion:false})",
        )
        assistant_bootstrap_deadline = time.monotonic() + 4.0
        assistant_native_after_matrix = inspect_assistant_native_interaction(assistant_window)
        while (
            assistant_native_after_matrix["animation_locked"]
            and time.monotonic() < assistant_bootstrap_deadline
        ):
            time.sleep(0.05)
            assistant_native_after_matrix = inspect_assistant_native_interaction(assistant_window)
        if assistant_native_after_matrix["animation_locked"]:
            raise RuntimeError("ASSISTANT_NATIVE_BOOTSTRAP_DID_NOT_SETTLE")
        assistant_blink_refresh_before = inspect_assistant_native_interaction(assistant_window)
        assistant_blink_refresh_receipts = [
            apply_assistant_native_presentation(
                assistant_window, assistant_projection["presentation"]
            )
            for _ in range(2)
        ]
        assistant_blink_refresh_after = inspect_assistant_native_interaction(assistant_window)
        assistant_blink_refresh_preservation = {
            "before": assistant_blink_refresh_before,
            "receipts": assistant_blink_refresh_receipts,
            "after": assistant_blink_refresh_after,
        }
        assistant_blink_scheduled = inspect_assistant_native_interaction(assistant_window)
        assistant_blink_trigger = invoke_assistant_native_auto_blink(assistant_window)
        assistant_blink_working_receipt = apply_assistant_native_presentation(
            assistant_window,
            {
                "animation_state": "WORKING",
                "attention_state": "INFO",
                "reason_code": "JOB_RUNNING",
                "reduced_motion": False,
            },
        )
        assistant_blink_after_working_projection = inspect_assistant_native_interaction(
            assistant_window
        )
        # assistant_pets.html keeps the first closed-eye frame from 1200 to
        # 1650 ms. Sample the middle of that interval so the evidence proves
        # an actually rendered closed frame instead of only a state label.
        time.sleep(1.38)
        assistant_blink_closed = inspect_assistant_native_interaction(assistant_window)
        assistant_blink_closed_screenshot = capture_screen_rectangle(
            inspect_assistant_visibility(assistant_window),
            evidence_dir / f"pywebview_assistant_blink_closed_{phase}.png",
        )
        assistant_blink_deadline = time.monotonic() + 3.0
        assistant_blink_settled = assistant_blink_closed
        while (
            assistant_blink_settled["animation_locked"]
            and time.monotonic() < assistant_blink_deadline
        ):
            time.sleep(0.05)
            assistant_blink_settled = inspect_assistant_native_interaction(assistant_window)
        if assistant_blink_settled["animation_locked"]:
            raise RuntimeError("ASSISTANT_NATIVE_AUTO_BLINK_DID_NOT_SETTLE")
        time.sleep(1.0)
        assistant_blink_post_settle = inspect_assistant_native_interaction(
            assistant_window
        )
        assistant_native_blink = {
            "scheduled": assistant_blink_scheduled,
            "trigger": assistant_blink_trigger,
            "working_projection_during_blink": assistant_blink_working_receipt,
            "after_working_projection": assistant_blink_after_working_projection,
            "closed": assistant_blink_closed,
            "settled": assistant_blink_settled,
            "post_settle": assistant_blink_post_settle,
            "closed_screenshot": assistant_blink_closed_screenshot,
        }
        assistant_after_blink_physical_click = invoke_assistant_physical_primary_click(
            assistant_window
        )
        # The WebView is now a hidden bootstrap carrier and deliberately has no
        # visual or pointer authority. Exercising its requestAnimationFrame
        # menu path would wait on a throttled hidden renderer and would test a
        # surface the user cannot reach. The native ContextMenuStrip below is
        # the sole product menu and receives the full interaction test.
        assistant_html_context_menu = {
            "disabled": True,
            "reason": "HIDDEN_BOOTSTRAP_WEBVIEW_NO_VISUAL_AUTHORITY",
        }
        assistant_native_menu_open = open_assistant_native_menu(assistant_window)
        assistant_menu_screenshot = capture_screen_rectangle(
            assistant_native_menu_open["bounds"],
            evidence_dir / f"native_assistant_menu_{phase}.png",
        )
        assistant_native_menu_outside = close_assistant_native_menu(
            assistant_window, "outside-pointer"
        )
        assistant_native_menu_reopen = open_assistant_native_menu(assistant_window)
        assistant_native_menu_escape = close_assistant_native_menu(
            assistant_window, "escape"
        )
        assistant_context_menu = {
            "native": {
                **assistant_native_menu_open,
                "outside_dismiss": assistant_native_menu_outside,
                "reopen": assistant_native_menu_reopen,
                "escape_dismiss": assistant_native_menu_escape,
            },
            "html_fallback": assistant_html_context_menu,
        }
        assistant_after_menu_physical_click = invoke_assistant_physical_primary_click(
            assistant_window
        )
        native_pdf_source = evidence_dir / "native-ui-real-user.pdf"
        native_pdf_bytes = test_pdf.read_bytes()
        with native_pdf_source.open("xb") as stream:
            stream.write(native_pdf_bytes)
        native_pdf_source_sha256 = sha256_file(native_pdf_source)
        native_pdf_stage = api.stage_inbox_paths([str(native_pdf_source)])
        native_pdf_import = api.call(
            "inbox.import_paths",
            {
                "paths": native_pdf_stage["paths"],
                "request_id": "native-ui-real-pdf-" + hashlib.sha256(native_pdf_bytes).hexdigest()[:16],
                "source_kind": "picker",
            },
        )
        native_pdf_item = native_pdf_import["items"][0]["item"]
        if native_pdf_item.get("state") == "DEDUPLICATED":
            existing_item_id = str(
                (native_pdf_item.get("dedupe_receipt") or {}).get(
                    "existing_item_id", ""
                )
            )
            if not existing_item_id:
                raise RuntimeError("native UI PDF dedupe target missing")
            existing_detail = api.call(
                "inbox.item_detail", {"item_id": existing_item_id}
            )
            # The user-visible card belongs to the already-bound canonical
            # item.  Continue the UI proof against that card instead of the
            # hidden DEDUPLICATED audit row returned for the second import.
            native_pdf_item = {
                **native_pdf_item,
                "item_id": existing_detail["item_id"],
                "source_name": existing_detail["name"],
            }
        if sha256_file(native_pdf_source) != native_pdf_source_sha256:
            raise RuntimeError("native UI source PDF changed during import")
        integrated_activation = window.evaluate_js("window.__Desktop_INTEGRATED_APP__.activate('work-log')")
        bridge_targets = (
            ("settings", "__Desktop_SETTINGS_BRIDGE__"),
            ("inbox", "__Desktop_INBOX_BRIDGE__"),
            ("current_task", "__Desktop_CURRENT_TASK_BRIDGE__"),
            ("messages", "__Desktop_MESSAGES_BRIDGE__"),
            ("sessions", "__Desktop_SESSIONS_BRIDGE__"),
            ("library", "__Desktop_LIBRARY_BRIDGE__"),
            ("work_log", "__Desktop_WORK_LOG_BRIDGE__"),
        )
        bridge_bootstraps: dict[str, Any] = {}
        for key, global_name in bridge_targets:
            bridge_bootstraps[key] = evaluate_promise(
                window,
                f"Promise.resolve(window[{json.dumps(global_name)}].bootstrap())",
            )
        evaluate_promise(window, "window.__Desktop_INBOX_BRIDGE__.refresh()")
        native_drop_source = evidence_dir / "native-ui-drop-real-user.md"
        native_drop_bytes = (
            "# Native drop regression\n\n"
            "This file must travel through pywebviewFullPath and the controlled inbox staging boundary.\n"
        ).encode("utf-8")
        with native_drop_source.open("xb") as stream:
            stream.write(native_drop_bytes)
        native_drop_source_sha256 = sha256_file(native_drop_source)
        native_drop_receipt = drop_runtime["callback"]({
            "type": "drop",
            "dataTransfer": {
                "files": [{
                    "name": native_drop_source.name,
                    "size": native_drop_source.stat().st_size,
                    "type": "text/markdown",
                    "pywebviewFullPath": str(native_drop_source),
                }]
            },
        })
        if sha256_file(native_drop_source) != native_drop_source_sha256:
            raise RuntimeError("native drop source changed during import")
        native_drop_item_id = native_drop_receipt["item_ids"][0]
        native_drop_detail = api.call("inbox.item_detail", {"item_id": native_drop_item_id})
        pdf_runtime_inspect = window.evaluate_js(
            "({ready:Boolean(window.__Desktop_PDFJS_READY__),"
            "library:Boolean(window.pdfjsLib),worker:Boolean(window.pdfjsWorker),"
            "worker_handler:Boolean(window.pdfjsWorker?.WorkerMessageHandler),"
            "errors:window.__Desktop_PDFJS_ERRORS__||null})"
        )
        native_provider = "研究模型服务 · 本地验收 " + hashlib.sha256(os.urandom(16)).hexdigest()[:8]
        native_credential_input = os.urandom(24).hex()
        native_provider_2 = "异源校核服务 · 本地验收 " + hashlib.sha256(os.urandom(16)).hexdigest()[:8]
        native_credential_input_2 = os.urandom(24).hex()
        native_provider_3 = "开发者代码服务 · 本地验收 " + hashlib.sha256(os.urandom(16)).hexdigest()[:8]
        native_credential_input_3 = os.urandom(24).hex()
        native_settings_root = evidence_dir / "native-settings-directories"
        native_workspace_root = native_settings_root / "用户工作区"
        native_artifact_root = native_settings_root / "用户产物"
        native_obsidian_root = native_settings_root / "Research Vault"
        native_workspace_root.mkdir(parents=True)
        native_artifact_root.mkdir()
        (native_obsidian_root / ".obsidian").mkdir(parents=True)
        ui_script = r"""
        (async () => {
          window.__Desktop_NATIVE_STAGE__ = 'bootstrap';
          const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
          const waitUntil = async (predicate, timeout = 12000) => {
            const started = Date.now();
            while (!predicate()) {
              if (Date.now() - started > timeout) throw new Error('UI_WAIT_TIMEOUT');
              await wait(80);
            }
          };
          const configureCustomApiEndpoint = () => {
            const protocol = document.querySelector('[data-desktop-route="settings"] #settings-api-protocol');
            const endpoint = document.querySelector('[data-desktop-route="settings"] #settings-api-base-url');
            protocol.value = 'OPENAI_COMPATIBLE';
            protocol.dispatchEvent(new Event('change', { bubbles: true }));
            endpoint.value = 'https://native-smoke.invalid/v1';
            endpoint.dispatchEvent(new Event('input', { bubbles: true }));
          };
          const app = window.__Desktop_INTEGRATED_APP__;
          const rectRatio = (left, right) => {
            const a = left.getBoundingClientRect().width;
            const b = right.getBoundingClientRect().width;
            return a / (a + b);
          };
          const settingsPaneMetrics = (settingsRoot) => {
            const categoryWidth = settingsRoot.querySelector('.desktop-settings-categories').getBoundingClientRect().width;
            const primaryWidth = settingsRoot.querySelector('#settings-primary').getBoundingClientRect().width;
            return {
              category_width: categoryWidth,
              primary_width: primaryWidth,
              primary_ratio: primaryWidth / (categoryWidth + primaryWidth),
            };
          };
          const accessibleBackIcon = (button) => Boolean(
            button?.querySelector('svg.desktop-symbol path')?.getAttribute('d') === 'research_reports 6-6 6 6 6'
            && button.getAttribute('aria-label')
            && button.getBoundingClientRect().width >= 30
            && button.getBoundingClientRect().height >= 30
          );

          window.__Desktop_NATIVE_STAGE__ = 'home';
          app.activate('home');
          const homeRoot = document.querySelector('[data-desktop-route="inbox"] #desktop-inbox-preview');
          const home = {
            title: document.title,
            prompt: homeRoot.querySelector('#desktop-blank-state')?.textContent.trim(),
            selected_nav_count: homeRoot.querySelectorAll('.desktop-nav-item[aria-current="page"]').length,
            back_hidden: homeRoot.querySelector('#desktop-return-main').hidden,
          };

          const routeRows = app.routes.map((route) => {
            const row = app.activate(route);
            return {
              route,
              active_surface_count: row.active_surface_count,
              selected_nav_count: row.selected_nav_count,
              content_shell_present: row.content_shell_present,
              placeholder_visible: row.placeholder_visible,
            };
          });

          window.__Desktop_NATIVE_STAGE__ = 'settings';
          app.activate('settings');
          const settingsRoot = document.querySelector('[data-desktop-route="settings"] #desktop-settings-preview');
          const settingsPrimaryInitiallyHidden = settingsRoot.querySelector('#settings-primary').hidden;
          const modeHiddenInDirectory = settingsRoot.querySelector('#settings-mode').hidden;
          settingsRoot.querySelector('[data-category="api"]').click();
          await waitUntil(() => settingsRoot.querySelector('#settings-primary').getBoundingClientRect().width >= 700);
          await wait(40);
          const modeVisibleInApi = !settingsRoot.querySelector('#settings-mode').hidden;
          const apiTwoPane = settingsPaneMetrics(settingsRoot);
          settingsRoot.querySelector('#settings-mode button[data-mode="normal"]').click();
          const emptyCard = settingsRoot.querySelector('#settings-api-empty');
          const emptyStrongRect = emptyCard.querySelector('strong').getBoundingClientRect();
          const emptyCopyRect = emptyCard.querySelector('.desktop-settings-api-empty-copy span').getBoundingClientRect();
          const emptyLayout = {
            visible: !emptyCard.hidden,
            heading_width: emptyStrongRect.width,
            copy_width: emptyCopyRect.width,
            non_overlapping: emptyStrongRect.bottom <= emptyCopyRect.top + 1,
          };
          const providerInput = settingsRoot.querySelector('#settings-provider');
          const orphanCreateReceipt = await window.__Desktop_SETTINGS_BRIDGE__.call('settings.credential_create', {
            provider: __CUSTOM_PROVIDER__,
            logical_key: 'primary',
            secret_input: __CUSTOM_CREDENTIAL__,
            expected_revision: window.__Desktop_SETTINGS_BRIDGE__.inspect().revision,
          });
          await window.__Desktop_SETTINGS_BRIDGE__.refresh();
          const orphanStateBeforeBinding = await window.__Desktop_SETTINGS_BRIDGE__.call('settings.get_state', {});
          settingsRoot.querySelector('#settings-add-api').click();
          await wait(220);
          const normalModelForm = {
            model_name_visible: getComputedStyle(settingsRoot.querySelector('#settings-model-name')).display !== 'none',
            plan_values: [...settingsRoot.querySelectorAll('#settings-model-plan [data-model-plan]')].map((node) => node.dataset.modelPlan),
            plan_labels: [...settingsRoot.querySelectorAll('#settings-model-plan [data-model-plan]')].map((node) => node.textContent.trim()),
            selected_plan: settingsRoot.querySelector('#settings-model-plan [aria-checked="true"]')?.dataset.modelPlan || null,
            thinking_is_switch: settingsRoot.querySelector('#settings-thinking-enabled')?.getAttribute('role') === 'switch',
            tier_input_hidden: getComputedStyle(settingsRoot.querySelector('#settings-model-tier').closest('label')).display === 'none',
            thinking_hidden: getComputedStyle(settingsRoot.querySelector('#settings-thinking-enabled').closest('.desktop-settings-thinking-row')).display === 'none',
            text_thinking_absent: !settingsRoot.querySelector('#settings-thinking-mode'),
          };
          const tierInteraction = [];
          for (const tierButton of settingsRoot.querySelectorAll('#settings-model-plan [data-model-plan]')) {
            tierButton.click();
            await wait(24);
            const selectedTier = settingsRoot.querySelector('#settings-model-plan [aria-checked="true"]');
            const unselectedTier = [...settingsRoot.querySelectorAll('#settings-model-plan [data-model-plan]')]
              .find((node) => node !== selectedTier);
            const selectedStyle = getComputedStyle(selectedTier);
            const unselectedStyle = getComputedStyle(unselectedTier);
            tierInteraction.push({
              requested: tierButton.dataset.modelPlan,
              selected: selectedTier?.dataset.modelPlan || null,
              selected_aria_checked: selectedTier?.getAttribute('aria-checked') || null,
              selected_box_shadow: selectedStyle.boxShadow,
              selected_background: selectedStyle.backgroundColor,
              unselected_background: unselectedStyle.backgroundColor,
              visual_difference: selectedStyle.backgroundColor !== unselectedStyle.backgroundColor
                || selectedStyle.boxShadow !== unselectedStyle.boxShadow,
              page_save_enabled: !settingsRoot.querySelector('#settings-save').disabled,
            });
          }
          normalModelForm.tier_interaction = tierInteraction;
          settingsRoot.querySelector('#settings-mode button[data-mode="advanced"]').click();
          const advancedTierLabel = settingsRoot.querySelector('#settings-model-tier').closest('label');
          const advancedTierRect = advancedTierLabel.getBoundingClientRect();
          const advancedTierStyle = getComputedStyle(advancedTierLabel);
          const advancedModelForm = {
            tier_visible: advancedTierStyle.display !== 'none',
            plan_hidden: getComputedStyle(settingsRoot.querySelector('#settings-model-plan')).display === 'none',
            thinking_is_same_switch: settingsRoot.querySelectorAll('#settings-thinking-enabled').length === 1,
            tier_label_width: advancedTierRect.width,
            tier_label_height: advancedTierRect.height,
            tier_label_white_space: advancedTierStyle.whiteSpace,
            tier_label_writing_mode: advancedTierStyle.writingMode,
          };
          providerInput.value = __CUSTOM_PROVIDER__;
          providerInput.dispatchEvent(new Event('input', { bubbles: true }));
          configureCustomApiEndpoint();
          settingsRoot.querySelector('#settings-api-key').value = __CUSTOM_CREDENTIAL__;
          settingsRoot.querySelector('#settings-model-name').value = 'sonnet5';
          settingsRoot.querySelector('#settings-model-tier').value = 'high-quality';
          settingsRoot.querySelector('#settings-model-plan [data-model-plan="HIGH_QUALITY"]').click();
          const thinkingControl = settingsRoot.querySelector('#settings-thinking-enabled');
          if (thinkingControl.getAttribute('aria-checked') !== 'true') thinkingControl.click();
          let credentialService = null;
          let credentialService2 = null;
          let credentialState = null;
          let settingsSaveReceipt = null;
          let mappedState = null;
          let mappingEvidence = null;
          let duplicateProviderEvidence = null;
          let currentTaskModelEvidence = null;
          let localReferenceEvidence = null;
          let orphanReferenceEvidence = null;
          let draftAfterSave = null;
          let modeHiddenInViewer = null;
          let modeVisibleInWorkflow = null;
          let workflowTwoPane = null;
          let workflowVisibleModes = null;
          let developerApiEvidence = null;
          let developerWorkflowEvidence = null;
          try {
            credentialService = await window.__Desktop_SETTINGS_BRIDGE__.bindCredential();
            credentialState = await window.__Desktop_SETTINGS_BRIDGE__.call('settings.get_state', {});
            if (!credentialService?.config_id) throw new Error('CONFIGURED_MODEL_SERVICE_MISSING');
            orphanReferenceEvidence = {
              created_ref: orphanCreateReceipt.credential_ref,
              reused_ref: credentialService.credential_ref,
              reused: orphanCreateReceipt.credential_ref === credentialService.credential_ref,
              service_count_before: orphanStateBeforeBinding.settings.model_services.length,
              provider_reference_count_before: orphanStateBeforeBinding.settings.credential_references
                .filter((row) => row.provider === __CUSTOM_PROVIDER__).length,
              provider_reference_count_after: credentialState.settings.credential_references
                .filter((row) => row.provider === __CUSTOM_PROVIDER__).length,
              provider_service_count_after: credentialState.settings.model_services
                .filter((row) => row.provider === __CUSTOM_PROVIDER__).length,
            };
            const serviceCard = settingsRoot.querySelector(`[data-config-id="${CSS.escape(credentialService.config_id)}"]`);
            const statusCard = serviceCard?.querySelector('.desktop-settings-status-card');
            localReferenceEvidence = {
              card_present: Boolean(serviceCard),
              model_card_heading: serviceCard?.querySelector('.desktop-settings-api-summary strong')?.textContent.trim() || null,
              heading: statusCard?.querySelector('strong')?.textContent.trim() || null,
              button_text: serviceCard?.querySelector('[data-credential-status]')?.textContent.trim() || null,
              neutral_class: statusCard?.classList.contains('neutral') || false,
              yellow_class: statusCard?.classList.contains('yellow') || false,
              background_color: statusCard ? getComputedStyle(statusCard).backgroundColor : null,
            };
            draftAfterSave = {
              provider: providerInput.value,
              api_key: settingsRoot.querySelector('#settings-api-key').value,
              model_name: settingsRoot.querySelector('#settings-model-name').value,
              tier: settingsRoot.querySelector('#settings-model-tier').value,
              thinking_enabled: settingsRoot.querySelector('#settings-thinking-enabled').getAttribute('aria-checked') === 'true',
              closed: settingsRoot.querySelector('#settings-api-draft').hidden,
            };

            settingsRoot.querySelector('#settings-add-api').click();
            await wait(160);
            providerInput.value = `  ${__CUSTOM_PROVIDER__}  `;
            providerInput.dispatchEvent(new Event('input', { bubbles: true }));
            configureCustomApiEndpoint();
            settingsRoot.querySelector('#settings-api-key').value = __CUSTOM_CREDENTIAL_2__;
            settingsRoot.querySelector('#settings-model-name').value = 'sonnet5';
            settingsRoot.querySelector('#settings-model-tier').value = 'high-quality';
            settingsRoot.querySelector('#settings-model-plan [data-model-plan="HIGH_QUALITY"]').click();
            if (settingsRoot.querySelector('#settings-thinking-enabled').getAttribute('aria-checked') !== 'true') {
              settingsRoot.querySelector('#settings-thinking-enabled').click();
            }
            const duplicateRevisionBefore = window.__Desktop_SETTINGS_BRIDGE__.inspect().revision;
            const duplicateCountBefore = credentialState.settings.model_services.length;
            const duplicateResult = await window.__Desktop_SETTINGS_BRIDGE__.bindCredential();
            const duplicateState = await window.__Desktop_SETTINGS_BRIDGE__.call('settings.get_state', {});
            const duplicateProviderStyle = getComputedStyle(providerInput);
            duplicateProviderEvidence = {
              result_is_null: duplicateResult === null,
              revision_unchanged: duplicateState.revision === duplicateRevisionBefore,
              service_count_unchanged: duplicateState.settings.model_services.length === duplicateCountBefore,
              invalid_class: providerInput.classList.contains('is-invalid'),
              aria_invalid: providerInput.getAttribute('aria-invalid'),
              status_copy: settingsRoot.querySelector('#settings-draft-status').textContent.trim(),
              red_border: duplicateProviderStyle.borderColor,
              red_background: duplicateProviderStyle.backgroundColor,
            };
            settingsRoot.querySelector('#settings-cancel-api').click();
            await wait(120);

            settingsRoot.querySelector('#settings-add-api').click();
            await wait(160);
            settingsRoot.querySelector('#settings-mode button[data-mode="normal"]').click();
            providerInput.value = __CUSTOM_PROVIDER_2__;
            providerInput.dispatchEvent(new Event('input', { bubbles: true }));
            configureCustomApiEndpoint();
            settingsRoot.querySelector('#settings-api-key').value = __CUSTOM_CREDENTIAL_2__;
            settingsRoot.querySelector('#settings-model-name').value = 'GPT5.6Luna';
            settingsRoot.querySelector('#settings-model-plan [data-model-plan="STANDARD"]').click();
            if (settingsRoot.querySelector('#settings-thinking-enabled').getAttribute('aria-checked') === 'true') {
              settingsRoot.querySelector('#settings-thinking-enabled').click();
            }
            credentialService2 = await window.__Desktop_SETTINGS_BRIDGE__.bindCredential();
            credentialState = await window.__Desktop_SETTINGS_BRIDGE__.call('settings.get_state', {});
            if (!credentialService2?.config_id) throw new Error('SECOND_CONFIGURED_MODEL_SERVICE_MISSING');

            const apiDeveloperButton = settingsRoot.querySelector('#settings-mode button[data-mode="developer"]');
            apiDeveloperButton.click();
            await waitUntil(() => (
              apiDeveloperButton.getAttribute('aria-pressed') === 'true'
              && getComputedStyle(settingsRoot.querySelector('#settings-api-code-workbench')).display !== 'none'
              && getComputedStyle(settingsRoot.querySelector('#settings-api-list')).display === 'none'
            ));
            const apiWorkbenchVisibleBeforeSave =
              getComputedStyle(settingsRoot.querySelector('#settings-api-code-workbench')).display !== 'none';
            const apiNormalListHiddenBeforeSave =
              getComputedStyle(settingsRoot.querySelector('#settings-api-list')).display === 'none';
            const apiEditor = settingsRoot.querySelector('#settings-api-code');
            const apiDocument = window.__Desktop_SETTINGS_BRIDGE__.developerDocument('api');
            const validationCode = (scope, value) => {
              try {
                window.__Desktop_SETTINGS_BRIDGE__.validateDeveloperDocument(scope, value);
                return null;
              } catch (error) {
                return error.code || String(error.message || error).split(':')[0];
              }
            };
            const apiSecretDocument = { ...apiDocument, token: 'must-remain-forbidden' };
            const apiUnknownDocument = { ...apiDocument, unsupported_field: true };
            const apiWriteDocument = structuredClone(apiDocument);
            apiWriteDocument.model_services[0].api_key = __CUSTOM_CREDENTIAL_2__;
            apiWriteDocument.model_services.push({
              config_id: 'developer-code-model',
              provider: __CUSTOM_PROVIDER_3__,
              credential_ref: '',
              model_name: 'DeveloperModel',
              api_protocol: 'OPENAI_COMPATIBLE',
              api_base_url: 'https://native-smoke.invalid/v1',
              tier: 'standard',
              thinking_mode: '',
              plan: 'STANDARD',
              api_key: __CUSTOM_CREDENTIAL_3__,
            });
            apiEditor.value = JSON.stringify(apiWriteDocument, null, 2) + '\n';
            apiEditor.dispatchEvent(new Event('input', { bubbles: true }));
            settingsRoot.querySelector('#settings-api-code-diff-toggle').click();
            await wait(40);
            const apiDiffRows = settingsRoot.querySelector('#settings-api-code-diff-rows');
            const apiAddedRow = apiDiffRows.querySelector('.desktop-settings-diff-added');
            const apiRemovedRow = apiDiffRows.querySelector('.desktop-settings-diff-removed');
            const apiVisualBeforeSave = {
              syntax_token_count: Number(settingsRoot.querySelector('#settings-api-code-highlight').dataset.tokenCount || 0),
              syntax_colour_count: new Set(
                [...settingsRoot.querySelectorAll('#settings-api-code-highlight [class^="desktop-settings-json-"]')]
                  .map((node) => getComputedStyle(node).color)
              ).size,
              diff_added_count: Number(apiDiffRows.dataset.addedCount || 0),
              diff_removed_count: Number(apiDiffRows.dataset.removedCount || 0),
              added_background: apiAddedRow ? getComputedStyle(apiAddedRow).backgroundColor : null,
              removed_background: apiRemovedRow ? getComputedStyle(apiRemovedRow).backgroundColor : null,
              backgrounds_differ: Boolean(apiAddedRow && apiRemovedRow)
                && getComputedStyle(apiAddedRow).backgroundColor !== getComputedStyle(apiRemovedRow).backgroundColor,
            };
            const apiDeveloperRevisionBefore = window.__Desktop_SETTINGS_BRIDGE__.inspect().revision;
            const apiDeveloperReceipt = await window.__Desktop_SETTINGS_BRIDGE__.saveDeveloper('api');
            const apiDeveloperState = await window.__Desktop_SETTINGS_BRIDGE__.call('settings.get_state', {});
            const apiDeveloperInspect = window.__Desktop_SETTINGS_BRIDGE__.inspect().developer_editor;
            developerApiEvidence = {
              workbench_visible_before_save: apiWorkbenchVisibleBeforeSave,
              normal_list_hidden_before_save: apiNormalListHiddenBeforeSave,
              workbench_visible: getComputedStyle(settingsRoot.querySelector('#settings-api-code-workbench')).display !== 'none',
              normal_list_hidden: getComputedStyle(settingsRoot.querySelector('#settings-api-list')).display === 'none',
              schema_version: apiDocument.schema_version,
              secret_field_block: validationCode('api', apiSecretDocument),
              unknown_field_block: validationCode('api', apiUnknownDocument),
              credential_values_absent: !apiEditor.value.includes(__CUSTOM_CREDENTIAL__)
                && !apiEditor.value.includes(__CUSTOM_CREDENTIAL_2__),
              api_key_fields_present: window.__Desktop_SETTINGS_BRIDGE__.developerDocument('api').model_services
                .every((row) => Object.prototype.hasOwnProperty.call(row, 'api_key')),
              api_key_fields_empty_after_save: window.__Desktop_SETTINGS_BRIDGE__.developerDocument('api').model_services
                .every((row) => row.api_key === ''),
              credential_refs_only: apiDocument.model_services.every((row) => /^REF:windows:/.test(row.credential_ref)),
              line_number_count: settingsRoot.querySelector('#settings-api-code-lines').textContent.trim().split('\n').length,
              editor_line_count: apiEditor.value.split('\n').length,
              save_action: apiDeveloperReceipt.action,
              revision_increased: apiDeveloperState.revision > apiDeveloperRevisionBefore,
              persisted_service_count: apiDeveloperState.settings.model_services.length,
              developer_created_service_present: apiDeveloperState.settings.model_services
                .some((row) => row.provider === __CUSTOM_PROVIDER_3__),
              developer_created_credential_ref: apiDeveloperState.settings.model_services
                .find((row) => row.provider === __CUSTOM_PROVIDER_3__)?.credential_ref || null,
              status_copy: settingsRoot.querySelector('#settings-api-code-status').textContent.trim(),
              credential_write_count: apiDeveloperInspect.credential_write_count,
              api_key_scrub_count: apiDeveloperInspect.api_key_scrub_count,
              visual_before_save: apiVisualBeforeSave,
            };
            const developerCreatedReference = apiDeveloperState.settings.model_services
              .find((row) => row.provider === __CUSTOM_PROVIDER_3__)?.credential_ref;
            if (!developerCreatedReference) throw new Error('DEVELOPER_CREATED_REFERENCE_MISSING');
            await window.__Desktop_SETTINGS_BRIDGE__.call('settings.credential_delete', {
              credential_ref: developerCreatedReference,
              expected_revision: apiDeveloperState.revision,
            });
            await window.__Desktop_SETTINGS_BRIDGE__.refresh();
            credentialState = await window.__Desktop_SETTINGS_BRIDGE__.call('settings.get_state', {});
            developerApiEvidence.developer_created_service_removed_after_probe = credentialState.settings.model_services
              .every((row) => row.provider !== __CUSTOM_PROVIDER_3__);

            settingsRoot.querySelector('[data-category="viewer"]').click();
            await wait(260);
            modeHiddenInViewer = settingsRoot.querySelector('#settings-mode').hidden;
            settingsRoot.querySelector('[data-category="workflow"]').click();
            await wait(400);
            modeVisibleInWorkflow = !settingsRoot.querySelector('#settings-mode').hidden;
            workflowTwoPane = settingsPaneMetrics(settingsRoot);
            workflowVisibleModes = [...settingsRoot.querySelectorAll('#settings-mode button[data-mode]')]
              .filter((button) => !button.hidden)
              .map((button) => button.dataset.mode);
            settingsRoot.querySelector('#settings-font-size').textContent = '110%';
            settingsRoot.querySelector('#settings-full-tooltips').setAttribute('aria-checked', 'false');

            const configureWorkflowNode = async (step, profileRef) => {
              settingsRoot.querySelector(`button.desktop-settings-node[data-step="${step}"]`).click();
              await wait(180);
              settingsRoot.querySelector('#settings-adjust-model').click();
              await wait(80);
              const buttons = [...settingsRoot.querySelectorAll('#settings-model-menu button[data-profile-ref]')];
              const target = buttons.find((button) => button.dataset.profileRef === profileRef);
              if (!target || target.disabled) throw new Error(`CONFIGURED_MODEL_OPTION_UNAVAILABLE:${step}`);
              const options = buttons.map((button) => ({
                profile_ref: button.dataset.profileRef,
                model_key: button.dataset.modelKey || '',
                label: button.querySelector('span:nth-of-type(2)')?.textContent.trim() || '',
                note: button.querySelector('small')?.textContent.trim() || '',
                disabled: button.disabled,
              }));
              target.click();
              await wait(60);
              return { options, selected_profile_ref: profileRef };
            };

            const workflowNodes = {
              card: await configureWorkflowNode('card', credentialService.config_id),
              card_review: await configureWorkflowNode('card-review', credentialService2.config_id),
              analysis: await configureWorkflowNode('analysis', credentialService.config_id),
              judgment_review: await configureWorkflowNode('judgment-review', credentialService2.config_id),
            };
            const revisionBeforeWorkflowSave = window.__Desktop_SETTINGS_BRIDGE__.inspect().revision;
            const workflowPageSave = settingsRoot.querySelector('#settings-save');
            if (workflowPageSave.disabled) throw new Error('MAPPED_WORKFLOW_PAGE_SAVE_DISABLED');
            workflowPageSave.click();
            await waitUntil(() => {
              const inspect = window.__Desktop_SETTINGS_BRIDGE__.inspect();
              return inspect.revision > revisionBeforeWorkflowSave
                && inspect.last_mutation_receipt?.method === 'settings.save'
                && inspect.last_mutation_receipt?.action === 'SAVE';
            });
            settingsSaveReceipt = window.__Desktop_SETTINGS_BRIDGE__.inspect().last_mutation_receipt;
            mappedState = await window.__Desktop_SETTINGS_BRIDGE__.call('settings.get_state', {});
            const configuredIds = new Set(credentialState.settings.model_services.map((service) => service.config_id));
            mappingEvidence = {
              option_count: workflowNodes.analysis.options.length,
              enabled_count: workflowNodes.analysis.options.filter((option) => !option.disabled).length,
              configured_profile_present: workflowNodes.analysis.options.some((option) => option.profile_ref === credentialService.config_id),
              configured_option_label: workflowNodes.analysis.options.find((option) => option.profile_ref === credentialService.config_id)?.label || null,
              sample_option_count: workflowNodes.analysis.options.filter((option) => /sample|示例/i.test(option.label)).length,
              every_option_is_configured: Object.values(workflowNodes).every((node) => node.options.every((option) => configuredIds.has(option.profile_ref))),
              nodes: workflowNodes,
              mapped_profile_refs: Object.fromEntries(
                mappedState.settings.workflow.nodes
                  .filter((row) => ['card_distill', 'transport_review', 'analysis', 'judgment_review'].includes(row.node_id))
                  .map((row) => [row.node_id, row.profile_ref])
              ),
            };

            const workflowWorkarea = settingsRoot.querySelector('#settings-workarea');
            const workflowNodePanel = settingsRoot.querySelector('#settings-node-settings');
            const workflowDeveloperButton = settingsRoot.querySelector('#settings-mode button[data-mode="developer"]');
            const workflowWorkbench = settingsRoot.querySelector('#settings-workflow-code-workbench');
            const workflowFlow = settingsRoot.querySelector('.desktop-settings-flow');
            settingsRoot.querySelector('button.desktop-settings-node[data-step="analysis"]').click();
            await waitUntil(() => workflowWorkarea.classList.contains('has-node') && !workflowNodePanel.hidden);
            const workflowNodeOpenBeforeDeveloper = workflowWorkarea.classList.contains('has-node')
              && !workflowNodePanel.hidden;
            workflowDeveloperButton.click();
            await waitUntil(() => (
              workflowDeveloperButton.getAttribute('aria-pressed') === 'true'
              && !workflowWorkarea.classList.contains('has-node')
              && workflowNodePanel.hidden
              && workflowNodePanel.getAttribute('aria-hidden') === 'true'
              && getComputedStyle(workflowWorkbench).display !== 'none'
              && getComputedStyle(workflowFlow).display === 'none'
            ));
            const workflowNodeClosedBeforeDeveloper = !workflowWorkarea.classList.contains('has-node')
              && workflowNodePanel.hidden
              && workflowNodePanel.getAttribute('aria-hidden') === 'true';
            const workflowWorkbenchVisibleBeforeSave =
              getComputedStyle(settingsRoot.querySelector('#settings-workflow-code-workbench')).display !== 'none';
            const workflowFlowHiddenBeforeSave =
              getComputedStyle(settingsRoot.querySelector('.desktop-settings-flow')).display === 'none';
            const workflowEditor = settingsRoot.querySelector('#settings-workflow-code');
            const workflowDocument = window.__Desktop_SETTINGS_BRIDGE__.developerDocument('workflow');
            const workflowUnknownDocument = { ...workflowDocument, arbitrary_command: 'blocked' };
            const workflowWriteDocument = structuredClone(workflowDocument);
            const editableWorkflowNode = workflowWriteDocument.nodes.find((node) => node.node_id === 'transport_review');
            editableWorkflowNode.retry_count = editableWorkflowNode.retry_count === 3 ? 4 : 3;
            workflowEditor.value = JSON.stringify(workflowWriteDocument, null, 2) + '\n';
            workflowEditor.dispatchEvent(new Event('input', { bubbles: true }));
            settingsRoot.querySelector('#settings-workflow-code-diff-toggle').click();
            await wait(40);
            const workflowDiffRows = settingsRoot.querySelector('#settings-workflow-code-diff-rows');
            const workflowAddedRow = workflowDiffRows.querySelector('.desktop-settings-diff-added');
            const workflowRemovedRow = workflowDiffRows.querySelector('.desktop-settings-diff-removed');
            const workflowVisualBeforeSave = {
              syntax_token_count: Number(settingsRoot.querySelector('#settings-workflow-code-highlight').dataset.tokenCount || 0),
              syntax_colour_count: new Set(
                [...settingsRoot.querySelectorAll('#settings-workflow-code-highlight [class^="desktop-settings-json-"]')]
                  .map((node) => getComputedStyle(node).color)
              ).size,
              diff_added_count: Number(workflowDiffRows.dataset.addedCount || 0),
              diff_removed_count: Number(workflowDiffRows.dataset.removedCount || 0),
              added_background: workflowAddedRow ? getComputedStyle(workflowAddedRow).backgroundColor : null,
              removed_background: workflowRemovedRow ? getComputedStyle(workflowRemovedRow).backgroundColor : null,
              backgrounds_differ: Boolean(workflowAddedRow && workflowRemovedRow)
                && getComputedStyle(workflowAddedRow).backgroundColor !== getComputedStyle(workflowRemovedRow).backgroundColor,
            };
            const workflowDeveloperRevisionBefore = window.__Desktop_SETTINGS_BRIDGE__.inspect().revision;
            const workflowDeveloperReceipt = await window.__Desktop_SETTINGS_BRIDGE__.saveDeveloper('workflow');
            const workflowDeveloperState = await window.__Desktop_SETTINGS_BRIDGE__.call('settings.get_state', {});
            developerWorkflowEvidence = {
              tertiary_open_before_switch: workflowNodeOpenBeforeDeveloper,
              tertiary_closed_before_developer: workflowNodeClosedBeforeDeveloper,
              workbench_visible_before_save: workflowWorkbenchVisibleBeforeSave,
              flow_hidden_before_save: workflowFlowHiddenBeforeSave,
              workbench_visible: getComputedStyle(settingsRoot.querySelector('#settings-workflow-code-workbench')).display !== 'none',
              flow_hidden: getComputedStyle(settingsRoot.querySelector('.desktop-settings-flow')).display === 'none',
              schema_version: workflowDocument.schema_version,
              unknown_field_block: validationCode('workflow', workflowUnknownDocument),
              node_set_preserved: workflowDeveloperState.settings.workflow.nodes.length === workflowDocument.nodes.length,
              line_number_count: settingsRoot.querySelector('#settings-workflow-code-lines').textContent.trim().split('\n').length,
              editor_line_count: workflowEditor.value.split('\n').length,
              save_action: workflowDeveloperReceipt.action,
              revision_increased: workflowDeveloperState.revision > workflowDeveloperRevisionBefore,
              status_copy: settingsRoot.querySelector('#settings-workflow-code-status').textContent.trim(),
              visual_before_save: workflowVisualBeforeSave,
            };
            settingsRoot.querySelector('#settings-mode button[data-mode="normal"]').click();
            await wait(40);

            app.activate('current-task');
            await wait(220);
            const currentTaskRoot = document.querySelector('[data-desktop-route="current-task"] #desktop-tasks-preview');
            currentTaskRoot.querySelector('.desktop-tasks-task[data-run="run-042"]').click();
            await wait(180);
            const chooseTaskModel = async (step, profileRef) => {
              const node = currentTaskRoot.querySelector(`.desktop-tasks-node[data-step="${step}"]`);
              node.click();
              await wait(100);
              const replaceButton = currentTaskRoot.querySelector('#tasks-adjust-model');
              if (replaceButton.hidden || replaceButton.disabled) throw new Error(`TASK_MODEL_REPLACE_UNAVAILABLE:${step}`);
              replaceButton.click();
              await wait(60);
              const options = [...currentTaskRoot.querySelectorAll('#tasks-model-menu button[data-profile-ref]')].map((button) => ({
                profile_ref: button.dataset.profileRef,
                model_key: button.dataset.modelKey || '',
                model: button.dataset.model,
                disabled: button.disabled,
                note: button.querySelector('small')?.textContent.trim() || '',
              }));
              const target = currentTaskRoot.querySelector(`#tasks-model-menu button[data-profile-ref="${CSS.escape(profileRef)}"]`);
              if (!target || target.disabled) throw new Error(`TASK_CONFIGURED_MODEL_OPTION_UNAVAILABLE:${step}`);
              const selectedModel = target.dataset.model;
              target.click();
              await waitUntil(() => {
                const refreshedNode = currentTaskRoot.querySelector(`.desktop-tasks-node[data-step="${step}"]`);
                const saveStatus = currentTaskRoot.querySelector('#tasks-adjust-status');
                return refreshedNode?.dataset.profileRef === profileRef
                  && refreshedNode?.dataset.model === selectedModel
                  && saveStatus?.dataset.tone === 'success';
              });
              const refreshedNode = currentTaskRoot.querySelector(`.desktop-tasks-node[data-step="${step}"]`);
              return {
                options,
                selected_profile_ref: refreshedNode.dataset.profileRef,
                selected_model: refreshedNode.dataset.model,
                current_model_copy: currentTaskRoot.querySelector('#tasks-current-model').textContent.trim(),
              };
            };
            const taskCard = await chooseTaskModel('card', credentialService2.config_id);
            const taskAnalysis = await chooseTaskModel('analysis', credentialService.config_id);
            currentTaskModelEvidence = {
              tasks: window.__Desktop_TASKS_UI__.inspect(),
              card: taskCard,
              analysis: taskAnalysis,
            };
            app.activate('settings');
            await wait(100);
          } finally {
            for (const provider of [__CUSTOM_PROVIDER__, __CUSTOM_PROVIDER_2__, __CUSTOM_PROVIDER_3__]) {
              const cleanupState = await window.__Desktop_SETTINGS_BRIDGE__.call('settings.get_state', {});
              const cleanupReference = cleanupState.settings.credential_references.find((row) => row.provider === provider)?.credential_ref;
              if (!cleanupReference) continue;
              await window.__Desktop_SETTINGS_BRIDGE__.call('settings.credential_delete', {
                credential_ref: cleanupReference,
                expected_revision: cleanupState.revision,
              });
              await window.__Desktop_SETTINGS_BRIDGE__.refresh();
            }
          }
          let settingsSavedState = await window.__Desktop_SETTINGS_BRIDGE__.call('settings.get_state', {});
          if (mappingEvidence) {
            mappingEvidence.cleanup_profile_ref = settingsSavedState.settings.workflow.nodes.find((row) => row.node_id === 'analysis')?.profile_ref || null;
            mappingEvidence.cleanup_profile_refs = Object.fromEntries(
              settingsSavedState.settings.workflow.nodes
                .filter((row) => ['card_distill', 'transport_review', 'analysis', 'judgment_review'].includes(row.node_id))
                .map((row) => [row.node_id, row.profile_ref])
            );
          }
          window.__Desktop_NATIVE_STAGE__ = 'settings-viewer-directories';
          settingsRoot.querySelector('[data-category="viewer"]').click();
          await waitUntil(() => {
            const primaryWidth = settingsRoot.querySelector('#settings-primary').getBoundingClientRect().width;
            const nodeClosed = !settingsRoot.querySelector('.desktop-settings-workarea').classList.contains('has-node');
            const tertiaryClosed = settingsRoot.querySelector('#desktop-right-panel').getAttribute('aria-hidden') === 'true';
            return !settingsRoot.querySelector('[data-panel="viewer"]').hidden
              && nodeClosed && tertiaryClosed && primaryWidth >= 700;
          });
          const workspaceInput = settingsRoot.querySelector('#settings-workspace-root');
          const artifactInput = settingsRoot.querySelector('#settings-artifact-root');
          const libraryProvider = settingsRoot.querySelector('#settings-library-provider');
          await window.__Desktop_SETTINGS_BRIDGE__.call('settings.register_directory', {
            role: 'workspace_root', path: __WORKSPACE_ROOT__
          });
          await window.__Desktop_SETTINGS_BRIDGE__.call('settings.register_directory', {
            role: 'artifact_root', path: __ARTIFACT_ROOT__
          });
          workspaceInput.value = __WORKSPACE_ROOT__;
          workspaceInput.dispatchEvent(new Event('change', { bubbles: true }));
          artifactInput.value = __ARTIFACT_ROOT__;
          artifactInput.dispatchEvent(new Event('change', { bubbles: true }));
          libraryProvider.value = 'OBSIDIAN';
          libraryProvider.dispatchEvent(new Event('change', { bubbles: true }));
          settingsRoot.querySelector('#settings-library-name').value = '研究资料库';
          settingsRoot.querySelector('#settings-library-name').dispatchEvent(new Event('change', { bubbles: true }));
          settingsRoot.querySelector('#settings-library-root').value = __OBSIDIAN_ROOT__;
          settingsRoot.querySelector('#settings-library-root').dispatchEvent(new Event('change', { bubbles: true }));
          settingsRoot.querySelector('#settings-library-test').click();
          await waitUntil(() => (
            settingsRoot.querySelector('#settings-library-status').textContent.trim() === '本地可用'
            && !settingsRoot.querySelector('#settings-library-test').disabled
          ));
          const viewerRevisionBeforeSave = window.__Desktop_SETTINGS_BRIDGE__.inspect().revision;
          if (settingsRoot.querySelector('#settings-save').disabled) throw new Error('VIEWER_SETTINGS_SAVE_DISABLED');
          settingsRoot.querySelector('#settings-save').click();
          await waitUntil(() => (
            window.__Desktop_SETTINGS_BRIDGE__.inspect().revision > viewerRevisionBeforeSave
          ));
          settingsSavedState = await window.__Desktop_SETTINGS_BRIDGE__.call('settings.get_state', {});
          settingsRoot.querySelector('#settings-library-test').click();
          await waitUntil(() => (
            settingsRoot.querySelector('#settings-library-status').textContent.trim() === '本地可用'
            && !settingsRoot.querySelector('#settings-library-test').disabled
          ));
          const externalLibraryEvidence = {
            provider_kind: settingsSavedState.settings.directories.external_library.provider_kind,
            enabled: settingsSavedState.settings.directories.external_library.enabled,
            display_name: settingsSavedState.settings.directories.external_library.display_name,
            root: settingsSavedState.settings.directories.external_library.root,
            workspace_root: settingsSavedState.settings.directories.workspace_root,
            artifact_root: settingsSavedState.settings.directories.artifact_root,
            external_refresh: settingsSavedState.settings.preferences.external_refresh,
            status_copy: settingsRoot.querySelector('#settings-library-status').textContent.trim(),
            detail_copy: settingsRoot.querySelector('#settings-library-copy').textContent.trim(),
            display_name_help_absent: !settingsRoot.querySelector('#settings-library-name').closest('label').querySelector('small'),
            display_name_placeholder: settingsRoot.querySelector('#settings-library-name').placeholder,
            root_help: settingsRoot.querySelector('#settings-library-root-help').textContent.trim(),
            alignment: (() => {
              const display = settingsRoot.querySelector('#settings-library-name').getBoundingClientRect();
              const rootInput = settingsRoot.querySelector('#settings-library-root').getBoundingClientRect();
              const rootHelp = settingsRoot.querySelector('#settings-library-root-help').getBoundingClientRect();
              return {
                input_top_delta: Math.abs(display.top - rootInput.top),
                input_height_delta: Math.abs(display.height - rootInput.height),
                root_help_below_input: rootHelp.top >= rootInput.bottom - 1,
                display_input_height: display.height,
                root_input_height: rootInput.height,
              };
            })(),
          };

          window.__Desktop_NATIVE_STAGE__ = 'settings-notifications';
          settingsRoot.querySelector('[data-category="notice"]').click();
          await wait(260);
          const errorSwitch = settingsRoot.querySelector('#settings-notify-error');
          const errorBefore = errorSwitch.getAttribute('aria-checked');
          errorSwitch.click();
          const errorOff = errorSwitch.getAttribute('aria-checked');
          errorSwitch.click();
          const errorOnAgain = errorSwitch.getAttribute('aria-checked');
          const soundSwitch = settingsRoot.querySelector('#settings-notify-sound');
          if (soundSwitch.getAttribute('aria-checked') !== 'true') soundSwitch.click();
          settingsRoot.querySelector('#settings-dnd-start').value = '00:00';
          settingsRoot.querySelector('#settings-dnd-start').dispatchEvent(new Event('change', { bubbles: true }));
          settingsRoot.querySelector('#settings-dnd-end').value = '00:00';
          settingsRoot.querySelector('#settings-dnd-end').dispatchEvent(new Event('change', { bubbles: true }));
          const notificationRevisionBeforeSave = window.__Desktop_SETTINGS_BRIDGE__.inspect().revision;
          if (settingsRoot.querySelector('#settings-save').disabled) throw new Error('NOTIFICATION_SETTINGS_SAVE_DISABLED');
          settingsRoot.querySelector('#settings-save').click();
          await waitUntil(() => (
            window.__Desktop_SETTINGS_BRIDGE__.inspect().revision > notificationRevisionBeforeSave
          ));
          settingsSavedState = await window.__Desktop_SETTINGS_BRIDGE__.call('settings.get_state', {});
          const notificationReceipt = await window.pywebview.api.test_windows_notification({ kind: 'COMPLETE' });
          const notificationEvidence = {
            error_toggle_cycle: { before: errorBefore, off: errorOff, on_again: errorOnAgain },
            persisted: {
              notifications_enabled: settingsSavedState.settings.preferences.notifications_enabled,
              task_complete_notification: settingsSavedState.settings.preferences.task_complete_notification,
              task_error_notification: settingsSavedState.settings.preferences.task_error_notification,
              approval_notification: settingsSavedState.settings.preferences.approval_notification,
              weekly_report_notification: settingsSavedState.settings.preferences.weekly_report_notification,
              notification_sound: settingsSavedState.settings.preferences.notification_sound,
              do_not_disturb_start: settingsSavedState.settings.preferences.do_not_disturb_start,
              do_not_disturb_end: settingsSavedState.settings.preferences.do_not_disturb_end,
              notification_open_task: settingsSavedState.settings.preferences.notification_open_task,
            },
            receipt: notificationReceipt,
          };
          window.__Desktop_NATIVE_STAGE__ = 'settings-workflow-layout';
          settingsRoot.querySelector('[data-category="workflow"]').click();
          await waitUntil(() => (
            !settingsRoot.querySelector('[data-panel="workflow"]').hidden
            && !settingsRoot.querySelector('.desktop-settings-workarea').classList.contains('has-node')
            && settingsRoot.querySelector('#desktop-right-panel').getAttribute('aria-hidden') === 'true'
            && settingsRoot.querySelector('#settings-primary').getBoundingClientRect().width >= 700
          ));
          settingsRoot.querySelector('#settings-mode button[data-mode="normal"]').click();
          await wait(40);
          const settingsWorkspace = settingsRoot.querySelector('.desktop-settings-workarea');
          const initialCategoryContainer = settingsRoot.querySelector('.desktop-settings-categories');
          const initialSelectedCategory = settingsRoot.querySelector('.desktop-settings-category[aria-selected="true"]');
          const initialCategoryIcon = initialSelectedCategory.querySelector('.desktop-settings-cat-icon');
          const initialCategoryContainerStyle = getComputedStyle(initialCategoryContainer);
          const initialSelectedCategoryStyle = getComputedStyle(initialSelectedCategory);
          const initialCategoryGeometry = {
            row_height: initialSelectedCategory.getBoundingClientRect().height,
            container_padding_left: Number.parseFloat(initialCategoryContainerStyle.paddingLeft),
            container_padding_right: Number.parseFloat(initialCategoryContainerStyle.paddingRight),
            card_padding_left: Number.parseFloat(initialSelectedCategoryStyle.paddingLeft),
            card_padding_right: Number.parseFloat(initialSelectedCategoryStyle.paddingRight),
            icon_width: initialCategoryIcon.getBoundingClientRect().width,
            icon_height: initialCategoryIcon.getBoundingClientRect().height,
          };
          const categoryRowGeometry = () => Array.from(
            settingsRoot.querySelectorAll('.desktop-settings-category')
          ).map((row) => {
            const card = row.getBoundingClientRect();
            const icon = row.querySelector('.desktop-settings-cat-icon').getBoundingClientRect();
            return {
              height: card.height,
              icon_center_x_delta: Math.abs((icon.left + icon.right - card.left - card.right) / 2),
              icon_center_y_delta: Math.abs((icon.top + icon.bottom - card.top - card.bottom) / 2),
            };
          });
          window.__Desktop_NATIVE_STAGE__ = 'settings-node-layout';
          settingsRoot.querySelector('button.desktop-settings-node[data-step="analysis"]').click();
          await waitUntil(() => (
            settingsWorkspace.classList.contains('has-node')
            && settingsRoot.querySelector('#desktop-right-panel').getAttribute('aria-hidden') === 'false'
            && settingsRoot.querySelector('#desktop-right-panel').getBoundingClientRect().width >= 479
            && settingsRoot.querySelector('#settings-resize-primary').hidden
            && categoryRowGeometry().every((row) => (
              Math.abs(row.height - initialCategoryGeometry.row_height) <= 0.5
              && row.icon_center_x_delta <= 0.5 && row.icon_center_y_delta <= 0.5
            ))
          ));
          const settingsShell = settingsRoot.querySelector('.desktop-workspace');
          const flowStageRect = settingsRoot.querySelector('#settings-flow-stage').getBoundingClientRect();
          const flowListRect = settingsRoot.querySelector('#settings-flow-list').getBoundingClientRect();
          const nodePrimaryRect = settingsRoot.querySelector('#settings-primary').getBoundingClientRect();
          const nodeCategoryRect = settingsRoot.querySelector('.desktop-settings-categories').getBoundingClientRect();
          const nodeRightRect = settingsRoot.querySelector('#desktop-right-panel').getBoundingClientRect();
          const selectedCategory = settingsRoot.querySelector('.desktop-settings-category[aria-selected="true"]');
          const selectedCategoryRect = selectedCategory.getBoundingClientRect();
          const selectedCategoryStyle = getComputedStyle(selectedCategory);
          const nodeCategoryContainerStyle = getComputedStyle(settingsRoot.querySelector('.desktop-settings-categories'));
          const nodeCategoryIcon = selectedCategory.querySelector('.desktop-settings-cat-icon');
          const selectedNav = settingsRoot.querySelector('.desktop-nav-item[aria-current="page"]');
          const selectedNavStyle = getComputedStyle(selectedNav);
          const modelHeading = settingsRoot.querySelector('.desktop-settings-model-heading');
          const modelDescription = settingsRoot.querySelector('.desktop-settings-model-description');
          const modelChoice = settingsRoot.querySelector('.desktop-settings-model-choice');
          const currentModel = settingsRoot.querySelector('#settings-current-model');
          const adjustModel = settingsRoot.querySelector('#settings-adjust-model');
          const originalModelCopy = currentModel.textContent;
          currentModel.textContent = 'GPT5.6Luna-standard-with-complete-model-name';
          currentModel.title = currentModel.textContent;
          await wait(24);
          const modelHeadingRect = modelHeading.getBoundingClientRect();
          const modelDescriptionRect = modelDescription.getBoundingClientRect();
          const modelChoiceRect = modelChoice.getBoundingClientRect();
          const currentModelRect = currentModel.getBoundingClientRect();
          const adjustModelRect = adjustModel.getBoundingClientRect();
          const currentModelStyle = getComputedStyle(currentModel);
          const adjustModelStyle = getComputedStyle(adjustModel);
          const nodeLayout = {
            category_rows: categoryRowGeometry(),
            category_width: nodeCategoryRect.width,
            category_inner_width: selectedCategoryRect.width,
            category_left_gutter: selectedCategoryRect.left - nodeCategoryRect.left,
            category_right_gutter: nodeCategoryRect.right - selectedCategoryRect.right,
            geometry_matches_initial: {
              container_padding_left: Number.parseFloat(nodeCategoryContainerStyle.paddingLeft),
              container_padding_right: Number.parseFloat(nodeCategoryContainerStyle.paddingRight),
              card_padding_left: Number.parseFloat(selectedCategoryStyle.paddingLeft),
              card_padding_right: Number.parseFloat(selectedCategoryStyle.paddingRight),
              icon_width: nodeCategoryIcon.getBoundingClientRect().width,
              icon_height: nodeCategoryIcon.getBoundingClientRect().height,
              initial: initialCategoryGeometry,
            },
            primary_width: nodePrimaryRect.width,
            tertiary_width: nodeRightRect.width,
            primary_resizer_hidden: settingsRoot.querySelector('#settings-resize-primary').hidden,
            flow_center_delta: Math.abs(
              (flowStageRect.left + flowStageRect.width / 2)
              - (nodePrimaryRect.left + settingsRoot.querySelector('#settings-primary').clientWidth / 2)
            ),
            node_open: settingsWorkspace.classList.contains('has-node'),
            model_layout: {
              description_hidden: getComputedStyle(modelDescription).display === 'none',
              description_empty: modelDescription.textContent.trim() === '',
              heading_before_choice: modelHeadingRect.bottom <= modelChoiceRect.top + 2,
              name_inside_choice: currentModelRect.top >= modelChoiceRect.top - 1
                && currentModelRect.bottom <= modelChoiceRect.bottom + 1,
              full_name: currentModel.textContent,
              full_name_visible: currentModel.scrollWidth <= currentModel.clientWidth + 1,
              model_white_space: currentModelStyle.whiteSpace,
              model_overflow_wrap: currentModelStyle.overflowWrap,
              adjust_width: adjustModelRect.width,
              adjust_text: adjustModel.textContent.trim(),
              adjust_white_space: adjustModelStyle.whiteSpace,
            },
            selected_treatment: {
              background_matches_nav: selectedCategoryStyle.backgroundColor === selectedNavStyle.backgroundColor,
              text_matches_nav: selectedCategoryStyle.color === selectedNavStyle.color,
              category_font_weight: selectedCategoryStyle.fontWeight,
              nav_font_weight: selectedNavStyle.fontWeight,
            },
          };
          currentModel.textContent = originalModelCopy;
          currentModel.title = originalModelCopy;
          const apiCategoryButton = settingsRoot.querySelector('[data-category="api"]');
          apiCategoryButton.click();
          await wait(420);
          const apiCategorySettled = () => (
            !settingsWorkspace.classList.contains('has-node')
            && settingsRoot.querySelector('#desktop-right-panel').getAttribute('aria-hidden') === 'true'
            && Math.abs(settingsPaneMetrics(settingsRoot).category_width - apiTwoPane.category_width) <= 1
          );
          if (!apiCategorySettled()) {
            // A same-category click is explicitly idempotent and closes a
            // stale tertiary inspector without replaying the whole transition.
            apiCategoryButton.click();
          }
          await waitUntil(() => (
            apiCategorySettled()
          ));
          const afterNodeCategoryLayout = {
            ...settingsPaneMetrics(settingsRoot),
            node_open: settingsWorkspace.classList.contains('has-node'),
            tertiary_hidden: settingsRoot.querySelector('#desktop-right-panel').getAttribute('aria-hidden') === 'true',
            primary_resizer_visible: !settingsRoot.querySelector('#settings-resize-primary').hidden,
          };
          const toast = settingsRoot.querySelector('#settings-toast');
          const toastCopy = toast.querySelector('span');
          toastCopy.textContent = '凭据安全保存失败：SETTINGS_CALL_FAILED:settings.credential_create:PARAMETER_INVALID:'
            + '输入字段校验失败，需要完整显示。'.repeat(24);
          toast.hidden = false;
          await wait(24);
          const toastRect = toast.getBoundingClientRect();
          const toastHostRect = settingsRoot.querySelector('.desktop-window').getBoundingClientRect();
          const toastCopyStyle = getComputedStyle(toastCopy);
          const longToast = {
            within_window: toastRect.left >= toastHostRect.left - 1 && toastRect.right <= toastHostRect.right + 1,
            text_fits_width: toastCopy.scrollWidth <= toastCopy.clientWidth + 1,
            wraps_to_multiple_lines: toastCopy.getBoundingClientRect().height
              > Number.parseFloat(toastCopyStyle.lineHeight) * 1.5,
            overflow_wrap: toastCopyStyle.overflowWrap,
          };
          toast.hidden = true;
          const networkFlagEvidence = window.__Desktop_SETTINGS_BRIDGE__.renderNetworkLocation({
            country_code: 'JP', colo: 'NRT'
          });
          const networkFlagNode = settingsRoot.querySelector('#settings-egress-location .desktop-settings-country-flag');
          networkFlagEvidence.country_text = settingsRoot.querySelector('#settings-egress-location').textContent.trim();
          networkFlagEvidence.role = networkFlagNode?.getAttribute('role') || null;
          networkFlagEvidence.graphic_child_count = networkFlagNode?.querySelector('svg')?.childElementCount || 0;
          const settingsBack = settingsRoot.querySelector('#desktop-return-main');
          const settings = {
            category_count: settingsRoot.querySelectorAll('.desktop-settings-category').length,
            primary_initially_hidden: settingsPrimaryInitiallyHidden,
            primary_size_css: getComputedStyle(settingsWorkspace).getPropertyValue('--desktop-settings-primary-size').trim(),
            tertiary_size_css: getComputedStyle(settingsShell).getPropertyValue('--desktop-right-size').trim(),
            api_two_pane: apiTwoPane,
            workflow_two_pane: workflowTwoPane,
            node_layout: nodeLayout,
            after_node_category_layout: afterNodeCategoryLayout,
            long_toast: longToast,
            flow_stage_width: flowStageRect.width,
            flow_list_width: flowListRect.width,
            preference_count: Object.keys(window.__Desktop_SETTINGS_BRIDGE__.collectSettings().preferences).length,
            provider_control_tag: providerInput.tagName,
            provider_control_type: providerInput.type,
            custom_provider: credentialService?.provider || null,
            custom_credential_ref: credentialService?.credential_ref || null,
            second_provider: credentialService2?.provider || null,
            second_credential_ref: credentialService2?.credential_ref || null,
            custom_provider_persisted: credentialState?.settings?.model_services?.some((row) => row.provider === __CUSTOM_PROVIDER__) || false,
            second_provider_persisted: credentialState?.settings?.model_services?.some((row) => row.provider === __CUSTOM_PROVIDER_2__) || false,
            persisted_model_profile: credentialState?.settings?.model_services?.find((row) => row.config_id === credentialService?.config_id) || null,
            persisted_second_model_profile: credentialState?.settings?.model_services?.find((row) => row.config_id === credentialService2?.config_id) || null,
            duplicate_provider: duplicateProviderEvidence,
            credential_cleaned: settingsSavedState.settings.credential_references.every((row) => ![__CUSTOM_PROVIDER__, __CUSTOM_PROVIDER_2__].includes(row.provider)),
            model_service_cleaned: settingsSavedState.settings.model_services.every((row) => ![__CUSTOM_PROVIDER__, __CUSTOM_PROVIDER_2__].includes(row.provider)),
            empty_layout: emptyLayout,
            normal_model_form: normalModelForm,
            advanced_model_form: advancedModelForm,
            developer_modes: {
              api: developerApiEvidence,
              workflow: developerWorkflowEvidence,
            },
            network_flag: networkFlagEvidence,
            empty_visible_after_cleanup: !settingsRoot.querySelector('#settings-api-empty').hidden,
            draft_after_save: draftAfterSave,
            local_reference: localReferenceEvidence,
            orphan_reference: orphanReferenceEvidence,
            mapping: mappingEvidence,
            current_task_models: currentTaskModelEvidence,
            external_library: externalLibraryEvidence,
            notification: notificationEvidence,
            mode_scope: {
              hidden_in_directory: modeHiddenInDirectory,
              visible_in_api: modeVisibleInApi,
              hidden_in_viewer: modeHiddenInViewer,
              visible_in_workflow: modeVisibleInWorkflow,
              workflow_visible_modes: workflowVisibleModes,
            },
            save_action: settingsSaveReceipt?.action || null,
            saved_font_scale: settingsSavedState.settings.preferences.font_scale_percent,
            saved_full_tooltips: settingsSavedState.settings.preferences.show_full_tooltips,
            back_text: settingsBack.textContent.trim(),
            back_control: accessibleBackIcon(settingsBack),
            back_gap: getComputedStyle(settingsBack.parentElement).gap,
            back_transform: getComputedStyle(settingsBack).transform,
          };

          window.__Desktop_NATIVE_STAGE__ = 'messages';
          app.activate('messages');
          const messagesRoot = document.querySelector('[data-desktop-route="messages"] #desktop-messages-preview');
          const visibleMessageCopy = [...messagesRoot.querySelectorAll('.desktop-messages-card .desktop-messages-copy')]
            .find((copy) => !copy.closest('.desktop-messages-card').hidden);
          if (!visibleMessageCopy) throw new Error('VISIBLE_MESSAGE_CARD_MISSING');
          visibleMessageCopy.click();
          await waitUntil(() => messagesRoot.querySelector('#desktop-right-panel').getAttribute('aria-hidden') === 'false');
          const messages = {
            detail_open: messagesRoot.querySelector('#desktop-right-panel').getAttribute('aria-hidden') === 'false',
            empty_visible: !messagesRoot.querySelector('#messages-empty').hidden,
            card_count: messagesRoot.querySelectorAll('.desktop-messages-card').length,
            close_text: messagesRoot.querySelector('#messages-close').textContent.trim(),
            close_control: accessibleBackIcon(messagesRoot.querySelector('#messages-close')),
            close_is_left_of_title: messagesRoot.querySelector('.desktop-messages-inspector-heading').firstElementChild?.id === 'messages-close',
            duplicate_close_count: messagesRoot.querySelectorAll('#messages-close').length,
          };

          window.__Desktop_NATIVE_STAGE__ = 'sessions';
          app.activate('sessions');
          const sessionsRoot = document.querySelector('[data-desktop-route="sessions"] #desktop-sessions-preview');
          const searchToggle = sessionsRoot.querySelector('#sessions-search-toggle');
          const search = sessionsRoot.querySelector('#sessions-search');
          sessionsRoot.querySelector('#sessions-search-toggle').click();
          search.value = 'Codex';
          search.dispatchEvent(new Event('input', { bubbles: true }));
          sessionsRoot.querySelector('#sessions-search-clear').click();
          await wait(80);
          const searchClosed = {
            hidden: sessionsRoot.querySelector('#sessions-searchbox').hidden,
            value: search.value,
            aria_expanded: searchToggle.getAttribute('aria-expanded'),
            focus_returned: document.activeElement === searchToggle,
          };
          sessionsRoot.querySelector('.desktop-sessions-card')?.click();
          await waitUntil(() => sessionsRoot.querySelectorAll('#sessions-turns .desktop-sessions-turn').length > 0);
          await waitUntil(() => {
            const ratio = rectRatio(sessionsRoot.querySelector('#sessions-listpane'), sessionsRoot.querySelector('#sessions-detail'));
            return sessionsRoot.querySelector('#sessions-detail').getAttribute('aria-hidden') === 'false'
              && ratio >= 0.38 && ratio <= 0.42;
          });
          const sessionStageRect = sessionsRoot.querySelector('#sessions-stage').getBoundingClientRect();
          const sessionListRect = sessionsRoot.querySelector('#sessions-listpane').getBoundingClientRect();
          const sessions = {
            search_closed: searchClosed,
            close_text: sessionsRoot.querySelector('#sessions-close').textContent.trim(),
            close_control: accessibleBackIcon(sessionsRoot.querySelector('#sessions-close')),
            list_ratio: rectRatio(sessionsRoot.querySelector('#sessions-listpane'), sessionsRoot.querySelector('#sessions-detail')),
            duplicate_close_count: sessionsRoot.querySelectorAll('#sessions-close').length,
            real_card_count: sessionsRoot.querySelectorAll('.desktop-sessions-card').length,
            rendered_turn_count: sessionsRoot.querySelectorAll('#sessions-turns .desktop-sessions-turn').length,
            sample_badge_count: [...sessionsRoot.querySelectorAll('.desktop-sample-badge')]
              .filter((node) => node.textContent.includes('示例 / 测试数据')).length,
            left_gutter: sessionListRect.left - sessionStageRect.left,
            bottom_gutter: sessionStageRect.bottom - sessionListRect.bottom,
          };

          window.__Desktop_NATIVE_STAGE__ = 'library';
          app.activate('library');
          const libraryRoot = document.querySelector('[data-desktop-route="library"] #desktop-library-preview');
          libraryRoot.querySelector('.desktop-library-card')?.click();
          await wait(200);
          const library = {
            detail_open: libraryRoot.querySelector('#desktop-right-panel').getAttribute('aria-hidden') === 'false',
            empty_visible: !libraryRoot.querySelector('#library-empty').hidden,
            card_count: libraryRoot.querySelectorAll('.desktop-library-card').length,
            close_text: libraryRoot.querySelector('#library-close').textContent.trim(),
            close_control: accessibleBackIcon(libraryRoot.querySelector('#library-close')),
            duplicate_close_count: libraryRoot.querySelectorAll('#library-close').length,
          };

          window.__Desktop_NATIVE_STAGE__ = 'work-log';
          app.activate('work-log');
          const workLogRoot = document.querySelector('[data-desktop-route="work-log"] #desktop-worklog-preview');
          workLogRoot.querySelector('.desktop-worklog-card')?.click();
          await waitUntil(() => {
            const ratio = rectRatio(workLogRoot.querySelector('#desktop-central'), workLogRoot.querySelector('#desktop-right-panel'));
            return workLogRoot.querySelector('#desktop-right-panel').getAttribute('aria-hidden') === 'false'
              && ratio >= 0.58 && ratio <= 0.62;
          });
          const workLog = {
            close_text: workLogRoot.querySelector('#worklog-close').textContent.trim(),
            close_control: accessibleBackIcon(workLogRoot.querySelector('#worklog-close')),
            central_ratio: rectRatio(workLogRoot.querySelector('#desktop-central'), workLogRoot.querySelector('#desktop-right-panel')),
            duplicate_close_count: workLogRoot.querySelectorAll('#worklog-close').length,
          };

          window.__Desktop_NATIVE_STAGE__ = 'inbox-pdf';
          app.activate('inbox');
          const inboxRoot = document.querySelector('[data-desktop-route="inbox"] #desktop-inbox-preview');
          await window.__Desktop_INBOX_BRIDGE__.refresh();
          const realCard = inboxRoot.querySelector('[data-item-id=__REAL_ITEM_ID__]');
          if (!realCard) throw new Error('INBOX_REAL_PDF_CARD_MISSING_AFTER_REFRESH');
          realCard?.click();
          await waitUntil(() => (
            inboxRoot.querySelector('#inbox-preview canvas.desktop-inbox-pdf-canvas')?.dataset.rendered === 'true'
            || (!inboxRoot.querySelector('#inbox-toast').hidden
              && inboxRoot.querySelector('#inbox-toast').textContent.includes('只读预览失败'))
          ));
          await wait(260);
          const pdfCanvas = inboxRoot.querySelector('#inbox-preview canvas.desktop-inbox-pdf-canvas');
          if (pdfCanvas?.dataset.rendered !== 'true') {
            throw new Error('PDF_PREVIEW_FAILED:' + inboxRoot.querySelector('#inbox-toast').textContent.trim());
          }
          const inboxCentral = inboxRoot.querySelector('#desktop-central');
          const inboxDetail = inboxRoot.querySelector('#desktop-right-panel');
          const inboxWorkspace = inboxRoot.querySelector('.desktop-workspace');
          const inboxNavigation = inboxRoot.querySelector('.desktop-navigation');
          const pdf = {
            card_present: Boolean(realCard),
            selected: realCard?.getAttribute('aria-selected') === 'true',
            title: inboxRoot.querySelector('#inbox-detail-title').textContent.trim(),
            detail_open: inboxDetail.getAttribute('aria-hidden') === 'false',
            primary_ratio: rectRatio(inboxCentral, inboxDetail),
            workspace_width: inboxWorkspace.getBoundingClientRect().width,
            navigation_width: inboxNavigation.getBoundingClientRect().width,
            central_width: inboxCentral.getBoundingClientRect().width,
            detail_width: inboxDetail.getBoundingClientRect().width,
            right_size_css: getComputedStyle(inboxWorkspace).getPropertyValue('--desktop-right-size').trim(),
            pdf_canvas_present: Boolean(pdfCanvas),
            pdf_canvas_rendered: pdfCanvas?.dataset.rendered === 'true',
            pdf_canvas_width: pdfCanvas?.width || 0,
            pdf_canvas_height: pdfCanvas?.height || 0,
            pdf_page_status: inboxRoot.querySelector('.desktop-inbox-pdf-page-status')?.textContent.trim() || '',
            pdf_embed_absent: !inboxRoot.querySelector('#inbox-preview embed, #inbox-preview object, #inbox-preview iframe'),
            sample_label_absent: !realCard?.textContent.includes('示例 / 测试数据'),
          };

          window.__Desktop_NATIVE_STAGE__ = 'appearance-and-startup';
          const fileTitleBeforeLanguageChange = inboxRoot.querySelector('#inbox-detail-title').textContent.trim();
          const viewName = inboxRoot.querySelector('#desktop-view-name');
          const logo = inboxRoot.querySelector('.desktop-logo');
          const tooltipTarget = inboxRoot.querySelector('#desktop-toggle-left');
          const tooltip = inboxRoot.querySelector('#desktop-tooltip');
          const tooltipVisible = () => Boolean(
            tooltip
            && !tooltip.hidden
            && getComputedStyle(tooltip).display !== 'none'
            && tooltip.textContent.trim()
          );
          const languageLabels = () => {
            const select = settingsRoot.querySelector('#settings-language');
            const shell = select.closest('.desktop-settings-select-shell');
            return {
              native: [...select.options].map((option) => option.textContent.trim()),
              enhanced: [...shell.querySelectorAll('.desktop-settings-select-menu button')]
                .map((button) => button.textContent.trim()),
            };
          };
          const collectHeaderMetrics = () => app.routes.map((route) => {
            app.activate(route);
            const surface = document.querySelector(`[data-desktop-route="${route}"]`);
            const header = surface.querySelector('.page-header');
            const eyebrow = header.querySelector('.page-heading > .page-eyebrow');
            const title = header.querySelector('.page-heading > h2');
            const headerRect = header.getBoundingClientRect();
            const titleRect = title.getBoundingClientRect();
            const titleStyle = getComputedStyle(title);
            return {
              route,
              header_height: headerRect.height,
              title_top_in_header: titleRect.top - headerRect.top,
              title_font_size: Number.parseFloat(titleStyle.fontSize),
              title_line_height: Number.parseFloat(titleStyle.lineHeight),
              eyebrow_visibility: !eyebrow?.getClientRects().length ? 'hidden' : getComputedStyle(eyebrow).visibility,
              content_fits: [...header.querySelectorAll('h2,button,select,.count-badge')].filter(n=>n.getClientRects().length&&getComputedStyle(n).visibility!=='hidden').every(n=>{
                const r=n.getBoundingClientRect();return r.bottom<=headerRect.bottom+1&&r.right<=headerRect.right+1&&r.left>=headerRect.left-1;
              }),
            };
          });
          app.applyPreferences({
            ...settingsSavedState.settings.preferences,
            language: 'zh-CN',
            font_scale_percent: 100,
            show_full_tooltips: false,
          });
          const chineseHeaderMetrics = collectHeaderMetrics();
          app.applyPreferences({
            ...settingsSavedState.settings.preferences,
            language: 'en-US',
            font_scale_percent: 100,
            show_full_tooltips: false,
          });
          const englishHeaderMetrics = collectHeaderMetrics();
          const headers = {
            chinese: chineseHeaderMetrics,
            english: englishHeaderMetrics,
            english_eyebrows_hidden: englishHeaderMetrics.every((row) => row.eyebrow_visibility === 'hidden'),
            responsive_headers_fit: [...chineseHeaderMetrics,...englishHeaderMetrics].every(row=>row.content_fits),
          };
          app.activate('inbox');
          app.applyPreferences({
            ...settingsSavedState.settings.preferences,
            language: 'en-US',
            font_scale_percent: 130,
            show_full_tooltips: false,
          });
          tooltipTarget.dispatchEvent(new MouseEvent('mouseenter'));
          await wait(430);
          const firstDisabledTooltipHidden = !tooltipVisible();
          const firstBasicTooltipVisible = tooltipVisible() && inboxRoot.querySelector('#desktop-tooltip').textContent === tooltipTarget.getAttribute('aria-label');
          const englishFontSize = Number.parseFloat(getComputedStyle(viewName).fontSize);
          const english = {
            view_name: viewName.textContent.trim(),
            settings_nav: [...inboxRoot.querySelectorAll('.desktop-nav-item')]
              .some((node) => node.textContent.trim() === 'Settings'),
            file_title_unchanged: inboxRoot.querySelector('#inbox-detail-title').textContent.trim()
              === fileTitleBeforeLanguageChange,
            native_title_removed: !logo.hasAttribute('title'),
            no_tooltip_marker: logo.hasAttribute('data-desktop-no-tooltip'),
            tooltips_dataset: document.body.dataset.desktopTooltips,
            font_size: englishFontSize,
            language_labels: languageLabels(),
            disabled_tooltip_hidden: firstDisabledTooltipHidden,
            basic_tooltip_visible: firstBasicTooltipVisible,
          };
          tooltipTarget.dispatchEvent(new MouseEvent('mouseleave'));
          app.applyPreferences({
            ...settingsSavedState.settings.preferences,
            language: 'ja-JP',
            font_scale_percent: 90,
            show_full_tooltips: true,
          });
          tooltipTarget.dispatchEvent(new MouseEvent('mouseenter'));
          await wait(430);
          const firstEnabledTooltipVisible = tooltipVisible();
          const japanese = {
            view_name: viewName.textContent.trim(),
            settings_nav: [...inboxRoot.querySelectorAll('.desktop-nav-item')]
              .some((node) => node.textContent.trim() === '設定'),
            file_title_unchanged: inboxRoot.querySelector('#inbox-detail-title').textContent.trim()
              === fileTitleBeforeLanguageChange,
            native_title_removed: !logo.hasAttribute('title'),
            no_tooltip_marker: logo.hasAttribute('data-desktop-no-tooltip'),
            tooltips_dataset: document.body.dataset.desktopTooltips,
            font_size: Number.parseFloat(getComputedStyle(viewName).fontSize),
            language_labels: languageLabels(),
            enabled_tooltip_visible: firstEnabledTooltipVisible,
          };
          tooltipTarget.dispatchEvent(new MouseEvent('mouseleave'));
          app.applyPreferences({
            ...settingsSavedState.settings.preferences,
            language: 'ja-JP',
            font_scale_percent: 90,
            show_full_tooltips: false,
          });
          tooltipTarget.dispatchEvent(new MouseEvent('mouseenter'));
          await wait(430);
          const secondDisabledTooltipHidden = !tooltipVisible();
          const secondBasicTooltipVisible = tooltipVisible() && inboxRoot.querySelector('#desktop-tooltip').textContent === tooltipTarget.getAttribute('aria-label');
          tooltipTarget.dispatchEvent(new MouseEvent('mouseleave'));
          app.applyPreferences({
            ...settingsSavedState.settings.preferences,
            language: 'ja-JP',
            font_scale_percent: 90,
            show_full_tooltips: true,
          });
          tooltipTarget.dispatchEvent(new MouseEvent('mouseenter'));
          await wait(430);
          const secondEnabledTooltipVisible = tooltipVisible();
          tooltipTarget.dispatchEvent(new MouseEvent('mouseleave'));
          const startupRestored = app.initializeFromPersistedState(
            { state: { route: 'messages', filters: {} } },
            { settings: { preferences: {
              ...settingsSavedState.settings.preferences,
              language: 'en-US',
              restore_last_view: true,
            } } },
          );
          const startupHome = app.initializeFromPersistedState(
            { state: { route: 'work-log', filters: {} } },
            { settings: { preferences: {
              ...settingsSavedState.settings.preferences,
              language: 'zh-CN',
              restore_last_view: false,
            } } },
          );
          app.applyPreferences(settingsSavedState.settings.preferences);
          app.activate('inbox');
          const appearance = {
            english,
            japanese,
            headers,
            font_scale_changes_geometry: english.font_size > japanese.font_size * 1.35,
            restored_document_setting_absent: !settingsRoot.querySelector('#settings-restore-document'),
            tooltip_repeat_cycle: {
              first_disabled_hidden: firstDisabledTooltipHidden,
              first_basic_visible: firstBasicTooltipVisible,
              first_enabled_visible: firstEnabledTooltipVisible,
              second_disabled_hidden: secondDisabledTooltipHidden,
              second_basic_visible: secondBasicTooltipVisible,
              second_enabled_visible: secondEnabledTooltipVisible,
            },
          };
          const startup = {
            restored_route: startupRestored.route,
            restored_enabled: startupRestored.restore_last_view,
            disabled_route: startupHome.route,
            disabled_enabled: startupHome.restore_last_view,
          };
          const sampleBadges = [...document.querySelectorAll('.desktop-sample-badge')]
            .filter((node) => node.textContent.includes('示例 / 测试数据')).length;
          return { home, routeRows, settings, messages, sessions, library, workLog, pdf, appearance, startup, sampleBadges };
        })().catch((error) => ({
          __error__: String(error?.stack || error),
          stage: window.__Desktop_NATIVE_STAGE__ || 'unknown',
        }))
        """.replace("__REAL_ITEM_ID__", json.dumps(native_pdf_item["item_id"]))
        ui_script = ui_script.replace("__CUSTOM_PROVIDER__", json.dumps(native_provider))
        ui_script = ui_script.replace("__CUSTOM_CREDENTIAL__", json.dumps(native_credential_input))
        ui_script = ui_script.replace("__CUSTOM_PROVIDER_2__", json.dumps(native_provider_2))
        ui_script = ui_script.replace("__CUSTOM_CREDENTIAL_2__", json.dumps(native_credential_input_2))
        ui_script = ui_script.replace("__CUSTOM_PROVIDER_3__", json.dumps(native_provider_3))
        ui_script = ui_script.replace("__CUSTOM_CREDENTIAL_3__", json.dumps(native_credential_input_3))
        ui_script = ui_script.replace("__WORKSPACE_ROOT__", json.dumps(str(native_workspace_root)))
        ui_script = ui_script.replace("__ARTIFACT_ROOT__", json.dumps(str(native_artifact_root)))
        ui_script = ui_script.replace("__OBSIDIAN_ROOT__", json.dumps(str(native_obsidian_root)))
        native_credential_input = ""
        native_credential_input_2 = ""
        native_credential_input_3 = ""
        ui_regression = evaluate_promise(window, ui_script, timeout=60.0)
        if not isinstance(ui_regression, dict) or "home" not in ui_regression:
            raise RuntimeError(
                "native UI regression script returned an error: "
                + json.dumps(
                    {"ui": ui_regression, "pdf_runtime": pdf_runtime_inspect},
                    ensure_ascii=False,
                    default=str,
                )
            )
        directory_runtime_evidence = {
            "workspace_effective": str(api._effective_directory("workspace_root")),
            "artifact_effective": str(api._effective_directory("artifact_root")),
            "external_library_effective": str(api._effective_directory("external_library")),
            "external_library_test": api.test_external_library({
                "provider_kind": "OBSIDIAN",
                "display_name": "研究资料库",
                "root": str(native_obsidian_root),
            }),
        }
        # Exercise both opposite pet-window edges through the real pointer
        # path. Work-area coordinates are physical pixels while the 90x90
        # pywebview size is logical, so each monitor's own DPI participates.
        primary_assistant_area = next(
            (area for area in api._assistant_areas if area.primary),
            api._assistant_areas[0],
        )
        visible_left, visible_top, visible_width, visible_height = api._assistant_visible_bounds(
            api._assistant_placement_class(
                monitor_id=primary_assistant_area.monitor_id,
                x=0,
                y=0,
                width=90,
                height=90,
                dpi_percent=int(primary_assistant_area.dpi_percent),
            )
        )
        center_form_x = (
            primary_assistant_area.left
            + (primary_assistant_area.right - primary_assistant_area.left - visible_width) // 2
            - visible_left
        )
        center_form_y = (
            primary_assistant_area.top
            + (primary_assistant_area.bottom - primary_assistant_area.top - visible_height) // 2
            - visible_top
        )
        assistant_test_move = api.assistant_move_window({
            "x": center_form_x,
            "y": center_form_y,
            "persist": False,
        })
        time.sleep(0.2)
        top_left_form_x = primary_assistant_area.left - visible_left
        top_left_form_y = primary_assistant_area.top - visible_top
        native_pet_drag_top_left = invoke_assistant_physical_drag(
            assistant_window,
            dx=top_left_form_x - center_form_x,
            dy=top_left_form_y - center_form_y,
        )
        bottom_right_form_x = primary_assistant_area.right - visible_left - visible_width
        bottom_right_form_y = primary_assistant_area.bottom - visible_top - visible_height
        native_pet_drag = invoke_assistant_physical_drag(
            assistant_window,
            dx=bottom_right_form_x - top_left_form_x,
            dy=bottom_right_form_y - top_left_form_y,
        )
        native_pet_desktop_span = {
            "work_area": {
                "monitor_id": primary_assistant_area.monitor_id,
                "left": primary_assistant_area.left,
                "top": primary_assistant_area.top,
                "right": primary_assistant_area.right,
                "bottom": primary_assistant_area.bottom,
            },
            "top_left": native_pet_drag_top_left,
            "bottom_right": native_pet_drag,
            "enumerated_monitor_count": len(api._assistant_areas),
        }
        # Reproduce the reported failure surface: one terminal animation starts,
        # ninety-nine more completion deliveries arrive while the user drags
        # the pet from a desktop edge onto the live Memorive window. The drag must
        # own the pet immediately, duplicate completion motions must not start,
        # and both the main HWND and the transparent corners must stay healthy.
        completion_stress_before = inspect_assistant_native_interaction(
            assistant_window
        )
        completion_stress_receipts = [apply_assistant_native_presentation(
            assistant_window,
            {
                "animation_state": "SUCCESS",
                "attention_state": "SUCCESS",
                "reason_code": "JOB_TERMINAL_SUCCESS",
                "locator": "memorive://job/presentation-stress-native-stress-000",
                "delivery_id": "presentation-stress-native-stress-000",
                "reduced_motion": False,
            },
        )]
        completion_stress_errors: list[str] = []

        def flood_terminal_presentations() -> None:
            for index in range(1, 100):
                delivery_id = f"presentation-stress-native-stress-{index:03d}"
                try:
                    completion_stress_receipts.append(
                        apply_assistant_native_presentation(
                            assistant_window,
                            {
                                "animation_state": "SUCCESS",
                                "attention_state": "SUCCESS",
                                "reason_code": "JOB_TERMINAL_SUCCESS",
                                "locator": f"memorive://job/{delivery_id}",
                                "delivery_id": delivery_id,
                                "reduced_motion": False,
                            },
                        )
                    )
                except Exception as error:
                    completion_stress_errors.append(
                        f"{type(error).__name__}:{error}"
                    )

        terminal_flood = threading.Thread(
            target=flood_terminal_presentations,
            name="memorive-presentation-stress-terminal-presentation-stress",
            daemon=True,
        )
        terminal_flood.start()
        main_form = window.native
        pet_before_main_drag = inspect_assistant_native_interaction(
            assistant_window
        )
        target_pet_left = int(main_form.Left) + min(220, max(40, int(main_form.Width) // 5))
        target_pet_top = int(main_form.Top) + min(180, max(40, int(main_form.Height) // 5))
        completion_stress_drag = invoke_assistant_physical_drag(
            assistant_window,
            dx=target_pet_left - int(pet_before_main_drag["form_left"]),
            dy=target_pet_top - int(pet_before_main_drag["form_top"]),
        )
        terminal_flood.join(timeout=15.0)
        if terminal_flood.is_alive():
            raise RuntimeError("ASSISTANT_TERMINAL_STRESS_THREAD_DID_NOT_SETTLE")
        time.sleep(0.3)
        completion_stress_after = inspect_assistant_native_interaction(
            assistant_window
        )
        response_value = ctypes.c_size_t()
        main_hwnd = int(main_form.Handle.ToInt64())
        main_window_responding = bool(ctypes.windll.user32.SendMessageTimeoutW(
            main_hwnd,
            0x0000,
            0,
            0,
            0x0002,
            1000,
            ctypes.byref(response_value),
        ))
        main_webview_probe = window.evaluate_js(r"""
          (() => ({
            alive: true,
            active_route: window.__Desktop_INTEGRATED_APP__?.inspect?.().active_route || null,
            surface_count: document.querySelectorAll('.desktop-route-surface').length,
          }))()
        """)
        completion_stress_main_screenshot = capture_screen_rectangle(
            {
                "left": int(main_form.Left),
                "top": int(main_form.Top),
                "width": int(main_form.Width),
                "height": int(main_form.Height),
            },
            evidence_dir / f"presentation-stress_pet_over_main_stress_{phase}.png",
        )
        completion_stress_transparency = inspect_assistant_desktop_transparency(
            assistant_window,
            evidence_dir,
            f"{phase}-presentation-stress-stress",
        )
        native_completion_drag_stress = {
            "delivery_count": len(completion_stress_receipts),
            "errors": completion_stress_errors,
            "before": completion_stress_before,
            "receipts": completion_stress_receipts,
            "drag": completion_stress_drag,
            "after": completion_stress_after,
            "main_window_responding": main_window_responding,
            "main_webview_probe": main_webview_probe,
            "main_screenshot": completion_stress_main_screenshot,
            "transparency": completion_stress_transparency,
        }
        # Return to the centre before the click test so a lower-right Windows
        # notification cannot become the physical pointer target.
        api.assistant_move_window({"x": center_form_x, "y": center_form_y, "persist": False})
        time.sleep(0.2)
        pet_state_before = window.evaluate_js(r"""
          (() => {
            const root = document.querySelector('[data-desktop-route="inbox"] #desktop-inbox-preview');
            const selected = root.querySelector('[data-item-id][aria-selected="true"]');
            return {
              route: document.body.dataset.desktopActiveRoute,
              detail_aria_hidden: root.querySelector('#desktop-right-panel').getAttribute('aria-hidden'),
              selected_item_id: selected?.dataset.itemId || null,
            };
          })()
        """)
        native_pet_click = invoke_assistant_physical_primary_click(assistant_window)
        pet_state_after = window.evaluate_js(r"""
          (() => {
            const root = document.querySelector('[data-desktop-route="inbox"] #desktop-inbox-preview');
            const selected = root.querySelector('[data-item-id][aria-selected="true"]');
            return {
              route: document.body.dataset.desktopActiveRoute,
              detail_aria_hidden: root.querySelector('#desktop-right-panel').getAttribute('aria-hidden'),
              selected_item_id: selected?.dataset.itemId || null,
            };
          })()
        """)
        pet_focus = {
            "state_before": pet_state_before,
            "state_after": pet_state_after,
            "activation_count_before": native_pet_click["before"]["primary_activation_count"],
            "activation_count_after": native_pet_click["after"]["primary_activation_count"],
            "receipt": native_pet_click["after"]["last_primary_receipt"],
            "native_event": native_pet_click,
        }
        deleted_selected_detail = evaluate_promise(window, r"""
          (async () => {
            const root = document.querySelector('[data-desktop-route="inbox"] #desktop-inbox-preview');
            const selected = root.querySelector('.desktop-inbox-card[aria-selected="true"]');
            const itemId = selected?.dataset.itemId || null;
            const remove = selected?.querySelector('.desktop-inbox-delete');
            if (!itemId || !remove) throw new Error('SELECTED_INBOX_DELETE_TARGET_MISSING');
            remove.click();
            const started = Date.now();
            while (root.querySelector(`[data-item-id="${CSS.escape(itemId)}"]`)) {
              if (Date.now() - started > 12000) throw new Error('SELECTED_INBOX_DELETE_TIMEOUT');
              await new Promise((resolve) => setTimeout(resolve, 80));
            }
            await new Promise((resolve) => setTimeout(resolve, 160));
            const bridge = window.__Desktop_INBOX_BRIDGE__.inspect();
            return {
              deleted_item_id: itemId,
              card_absent: !root.querySelector(`[data-item-id="${CSS.escape(itemId)}"]`),
              detail_aria_hidden: root.querySelector('#desktop-right-panel').getAttribute('aria-hidden'),
              selected_card_count: root.querySelectorAll('.desktop-inbox-card[aria-selected="true"]').length,
              preview_child_count: root.querySelector('#inbox-preview').childElementCount,
              bridge_selected_item_id: bridge.selected_item_id,
              bridge_inspector_open: bridge.inspector_open,
            };
          })()
        """, timeout=20.0)
        window.evaluate_js("window.__Desktop_INTEGRATED_APP__.activate('work-log')")
        bootstrap = bridge_bootstraps["work_log"]
        typed = evaluate_promise(window, "window.__Desktop_PRODUCT_BRIDGE__.dispatch('navigation.select',{route:'work-log'},'native-smoke')")
        contract = evaluate_promise(window, "window.pywebview.api.call('work_log.get_contract',{})")
        all_rows = evaluate_promise(window, "window.pywebview.api.call('work_log.list_entries',{page_size:500})")
        branch = evaluate_promise(window, "window.pywebview.api.call('work_log.list_entries',{ledger_type:'branch',page_size:500})")
        central = evaluate_promise(window, "window.pywebview.api.call('work_log.list_entries',{ledger_type:'central',page_size:500})")
        searched = evaluate_promise(window, "window.pywebview.api.call('work_log.list_entries',{search:'历史会话采集',page_size:500})")
        detail = evaluate_promise(window, "window.pywebview.api.call('work_log.get_entry',{log_entry_id:'MODEL-CONFIGURATION/CROSS/run-001'})")
        milestones = evaluate_promise(window, "window.pywebview.api.call('work_log.get_milestones',{log_entry_id:'SHARED/CENTRAL/2026-08-23'})")
        selected = evaluate_promise(window, "window.pywebview.api.call('work_log.select',{log_entry_id:'MODEL-CONFIGURATION/CROSS/run-001'})")
        filtered = evaluate_promise(window, "window.pywebview.api.call('work_log.set_filter',{time_range:'today',ledger_type:'branch'})")
        job = evaluate_promise(window, "window.pywebview.api.call('work_log.resolve_job_locator',{job_locator:'memorive://job/MODEL-CONFIGURATION/CROSS'})")
        evidence = evaluate_promise(window, "window.pywebview.api.call('work_log.resolve_evidence_locator',{evidence_locator:'memorive://evidence/model_configuration-h0'})")
        sources = evaluate_promise(window, "window.pywebview.api.call('work_log.get_source_status',{})")
        refreshed = evaluate_promise(window, "window.pywebview.api.call('work_log.refresh',{})")
        preview = evaluate_promise(window, "window.pywebview.api.call('work_log.export_preview',{ledger_type:'branch'})")
        export_id = f"native-{phase.lower()}"
        exported = evaluate_promise(window, f"window.pywebview.api.call('work_log.export_redacted_view',{{export_id:{json.dumps(export_id)},ledger_type:'branch'}})")
        verified = evaluate_promise(window, f"window.pywebview.api.call('work_log.verify_export',{{export_id:{json.dumps(export_id)}}})")
        fault_names = ['duplicate','out_of_order','gap','clock_skew','large_stream','corrupt_event','missing_locator','export_write_failure']
        faults = [evaluate_promise(window, f"window.pywebview.api.call('work_log.inject_fault',{{fault:{json.dumps(name)}}})") for name in fault_names]
        restored = evaluate_promise(window, "window.pywebview.api.call('work_log.restore',{})")
        effects = evaluate_promise(window, "window.pywebview.api.call('work_log.effect_metrics',{})")
        control = evaluate_promise(window, "window.pywebview.api.get_control_state()")
        status = evaluate_promise(window, "window.pywebview.api.get_runtime_status()")
        route_sweep = window.evaluate_js("window.__Desktop_INTEGRATED_APP__.probeAll()")
        window.evaluate_js("window.__Desktop_INTEGRATED_APP__.activate('work-log')")
        bridge_inspect = window.evaluate_js("(() => { const names=['__Desktop_SETTINGS_BRIDGE__','__Desktop_INBOX_BRIDGE__','__Desktop_CURRENT_TASK_BRIDGE__','__Desktop_MESSAGES_BRIDGE__','__Desktop_SESSIONS_BRIDGE__','__Desktop_LIBRARY_BRIDGE__','__Desktop_WORK_LOG_BRIDGE__']; return names.map((name) => { const bridge=window[name]; const detail=bridge && typeof bridge.inspect==='function' ? bridge.inspect() : {}; return {name,present:Boolean(bridge),method_count:Number(detail.method_count||0),native_transport:detail.native_transport===true,binding_count:Number(detail.binding_count||0),bootstrapped:detail.bootstrapped===undefined ? null : Boolean(detail.bootstrapped)}; }); })()")
        assistant_desktop_transparency = inspect_assistant_desktop_transparency(
            assistant_window, evidence_dir, phase
        )
        assistant_restore_after_transparency = evaluate_promise(
            window,
            "window.pywebview.api.assistant_shortcut({action_id:'RESTORE_DEFAULT_PLACEMENT'})",
        )
        assistant_hide = evaluate_promise(
            window,
            "window.pywebview.api.assistant_shortcut({action_id:'HIDE_DESKTOP_ASSISTANT'})",
        )
        time.sleep(0.2)
        assistant_hidden_native = inspect_assistant_visibility(assistant_window)
        assistant_logo_toggle = evaluate_promise(window, r"""
          (async () => {
            const app = window.__Desktop_INTEGRATED_APP__;
            app.activate('work-log');
            const logo = document.querySelector('[data-desktop-route="work-log"]:not([hidden]) .desktop-logo');
            if (!logo) throw new Error('ACTIVE_NAVIGATION_LOGO_MISSING');
            const activate = async (eventName, expectedAction, expectedVisible, expectedRoute) => {
              const before = app.inspect();
              const event = eventName === 'contextmenu'
                ? new MouseEvent('contextmenu', { bubbles: true, cancelable: true, button: 2, clientX: 48, clientY: 90 })
                : new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, clientX: 48, clientY: 90 });
              const dispatchResult = logo.dispatchEvent(event);
              const started = Date.now();
              while (true) {
                const current = app.inspect();
                if (current.assistant_show_request_count > before.assistant_show_request_count
                    && current.assistant_show_last_action === expectedAction
                    && current.assistant_show_last_status === 'ACKNOWLEDGED'
                    && (expectedVisible === null || current.assistant_show_last_window_visible === expectedVisible)
                    && (expectedRoute === null || current.active_route === expectedRoute)) {
                  return {
                    event_name: eventName,
                    default_prevented: dispatchResult === false,
                    request_count_before: before.assistant_show_request_count,
                    request_count_after: current.assistant_show_request_count,
                    action_id: current.assistant_show_last_action,
                    shortcut_status: current.assistant_show_last_status,
                    receipt_window_visible: current.assistant_show_last_window_visible,
                    route_after: current.active_route,
                    error: current.assistant_show_last_error,
                  };
                }
                if (Date.now() - started > 5000) {
                  throw new Error(`ASSISTANT_LOGO_ACTION_TIMEOUT:${eventName}:${expectedAction}`);
                }
                await new Promise((resolve) => setTimeout(resolve, 50));
              }
            };
            const routeBefore = app.inspect().active_route;
            const hiddenByContextMenu = await activate(
              'contextmenu', 'HIDE_DESKTOP_ASSISTANT', false, 'work-log'
            );
            const openedByLeftClick = await activate(
              'click', 'SHOW_DESKTOP_ASSISTANT', true, 'work-log'
            );
            const shownAgainByLeftClick = await activate(
              'click', 'SHOW_DESKTOP_ASSISTANT', true, 'work-log'
            );
            return {
              route_before: routeBefore,
              route_after: app.inspect().active_route,
              active_logo_count: document.querySelectorAll('[data-desktop-route="work-log"]:not([hidden]) .desktop-logo').length,
              contextmenu: hiddenByContextMenu,
              opened_by_left_click: openedByLeftClick,
              shown_again_by_left_click: shownAgainByLeftClick,
            };
          })()
        """)
        time.sleep(0.2)
        assistant_reopened_native = inspect_assistant_visibility(assistant_window)
        assistant_reopened_inspect = assistant_window.evaluate_js(
            "window.__Desktop_ASSISTANT_RUNTIME__.inspect()"
        )
        inspect = window.evaluate_js("(() => { const root=document.querySelector('#desktop-worklog-preview'); return {root:Boolean(root),pageRootCount:document.querySelectorAll('.desktop-page-root').length,productWindow:Boolean(root&&root.querySelector('#desktop-window')),navCount:root?root.querySelectorAll('.desktop-nav-item').length:0,workLogCardCount:root?root.querySelectorAll('.desktop-worklog-card').length:0,iframeCount:document.querySelectorAll('iframe').length,previewControlCount:document.querySelectorAll('.viz-controls,.desktop-preview-controls').length,bridge:window.__Desktop_PRODUCT_BRIDGE__.inspect(),workLogBridge:window.__Desktop_WORK_LOG_BRIDGE__.inspect(),integrated:window.__Desktop_INTEGRATED_APP__.inspect()}; })()")
        assistant_screenshot_path = evidence_dir / f"pywebview_assistant_{phase}.png"
        # The WebView child is intentionally disabled after native bootstrap.
        # Capture the actual shaped WinForms host instead of waiting on the
        # hidden WebView2 CapturePreviewAsync pipeline.
        assistant_screenshot = capture_screen_rectangle(
            {
                "left": assistant_reopened_native["left"],
                "top": assistant_reopened_native["top"],
                "width": assistant_reopened_native["width"],
                "height": assistant_reopened_native["height"],
            },
            assistant_screenshot_path,
        )
        assistant_window.hide()
        packaged_visual_qa = capture_packaged_visual_qa(window, evidence_dir, phase)
        window.evaluate_js("window.__Desktop_INTEGRATED_APP__.activate('work-log')")
        screenshot_path = evidence_dir / f"pywebview_product_{phase}.png"
        screenshot = capture_window(window, screenshot_path)
        restricted = ('branch_log_writes','business_network_calls','central_log_writes','credential_value_reads','external_model_calls','external_process_launches','frozen_evidence_writes','runtime_log_writes','private_payload_reads','production_writes','raw_authority_content_reads','shell_invocations','source_ledger_writes')
        dashboard_native = evaluate_promise(window, """(async () => {
          const ranking = await window.pywebview.api.model_dashboard({view:'ranking'});
          const billing = await window.pywebview.api.model_dashboard({view:'billing'});
          return {ranking_status:ranking.status, billing_status:billing.status,
            ranked_rows:(ranking.data?.groups || []).reduce((n,g)=>n+g.rows.length,0),
            ranking_calls:ranking.data?.external_model_calls, billing_calls:billing.data?.external_model_calls,
            installed_tabs:document.querySelectorAll('.desktop-model-dashboard [role="tab"]').length,
            account_invoice:billing.data?.complete_account_invoice};
        })()""", timeout=60.0)
        checks = {
            "model_dashboard_native_ranking_bridge": dashboard_native.get("ranking_status") == "PASS" and dashboard_native.get("ranked_rows", 0) >= 19,
            "model_dashboard_native_billing_bridge": dashboard_native.get("billing_status") == "PASS" and dashboard_native.get("account_invoice") is False,
            "model_dashboard_all_routes_read_only": dashboard_native.get("installed_tabs") == 14 and dashboard_native.get("ranking_calls") == 0 and dashboard_native.get("billing_calls") == 0,
            "renderer_edgechromium": webview.renderer == "edgechromium",
            "packaged_visual_qa_pass_or_not_requested": packaged_visual_qa["status"] in {
                "PASS", "NOT_RUN_NO_ISOLATED_IMPORT_DIR"
            },
            "native_drop_handler_attached": (
                drop_runtime.get("attached") is True
                and drop_runtime.get("attempt_count") == 1
                and drop_runtime.get("success_count") == 1
                and drop_runtime.get("failure_count") == 0
            ),
            "native_drop_real_file_import_exact": (
                native_drop_receipt["status"] == "PASS"
                and native_drop_receipt["source_file_count"] == 1
                and native_drop_receipt["staged_file_count"] == 1
                and native_drop_receipt["imported_item_count"] == 1
                and native_drop_receipt["source_kind"] == "drop"
                and native_drop_receipt["source_files_moved_or_deleted"] == 0
                and native_drop_detail["item_id"] == native_drop_item_id
                and native_drop_detail["name"] == native_drop_source.name
                and native_drop_detail["source_kind"] == "drop"
                and native_drop_detail["sample"] is False
                and sha256_file(native_drop_source) == native_drop_source_sha256
            ),
            "integrated_route_count_exact": inspect["integrated"]["route_count"] == len(EXPECTED_DESKTOP_ROUTES) and inspect["integrated"]["surface_count"] == len(EXPECTED_DESKTOP_ROUTES),
            "integrated_one_surface_active": inspect["integrated"]["active_surface_count"] == 1,
            "integrated_all_routes_real": len(route_sweep) == len(EXPECTED_DESKTOP_ROUTES) and all(
                row["root_present"] is True
                and row["page_view_visible"] is True
                and row["content_shell_present"] is True
                and row["placeholder_visible"] is False
                and row["active_surface_count"] == 1
                and row["selected_nav_count"] == 1
                for row in route_sweep
            ),
            "integrated_all_bridges_live": len(bridge_inspect) == 7 and all(
                row["present"] is True and row["method_count"] > 0 and row["native_transport"] is True
                for row in bridge_inspect
            ),
            "assistant_window_attached": status["assistant_window_attached"] is True,
            "assistant_runtime_ready": assistant_inspect["ready"] is True,
            "assistant_real_track_idle": (
                assistant_projection["source_track"] == "REAL_LOCAL_SERVICE"
                and assistant_projection["presentation"]["reason_code"] == "NO_ACTIVE_REAL_JOB"
            ),
            "assistant_sample_not_consumed": (
                assistant_projection["sample_job_consumed"] is False
                and assistant_projection["sample_success_used_as_real_success"] is False
                and assistant_inspect["sample_job_consumed"] is False
            ),
            "assistant_native_job_and_message_animations": (
                set(assistant_native_animation_matrix) == {"WORKING", "ATTENTION", "SUCCESS", "ERROR"}
                and all(
                    row["receipt"]["status"] == "PASS"
                    and row["receipt"]["timer_enabled"] is True
                    and row["receipt"]["single_shot_started"] is True
                    and len(row["receipt"]["sequence"]) >= 2
                    and row["receipt"]["motion_contract"]["source_sha256"]
                    == ASSISTANT_PETS_FINAL_SHA256
                    and row["receipt"]["motion_contract"]["single_shot"] is True
                    and row["started"]["animation_state"] == animation
                    and row["settled"]["animation_locked"] is False
                    and row["settled"]["current_frame"] == row["settled"]["base_frame"]
                    and row["settled"]["animation_completed_count"]
                    == row["before"]["animation_completed_count"] + 1
                    for animation, row in assistant_native_animation_matrix.items()
                )
                # Recording a native receipt immediately refreshes the real
                # service projection.  This isolated run has no active job,
                # so the service is allowed to restore IDLE after the injected
                # WORKING motion completes.  Verify the motion's own target
                # base frame and the visible start instead of requiring the
                # synthetic probe to override the real idle authority.
                and assistant_native_animation_matrix["WORKING"]["receipt"]["base_frame"] == "working"
                and assistant_native_animation_matrix["WORKING"]["settled"]["base_frame"]
                in {"working", "idle"}
                and all(
                    assistant_native_animation_matrix[name]["settled"]["base_frame"] == "idle"
                    for name in ("ATTENTION", "SUCCESS", "ERROR")
                )
                and assistant_native_after_matrix["presentation_apply_count"] >= 5
            ),
            "assistant_native_auto_blink_visible_and_scheduled": (
                AUTO_BLINK_MIN_MS
                <= int(assistant_native_blink["scheduled"]["auto_blink_due_ms"] or 0)
                <= AUTO_BLINK_MAX_MS
                and assistant_native_blink["trigger"]["status"] == "PASS"
                and assistant_native_blink["trigger"]["animation_state"] == "BLINK"
                and assistant_native_blink["trigger"]["animation_locked"] is True
                and assistant_native_blink["trigger"]["auto_blink_count_after"]
                == assistant_native_blink["trigger"]["auto_blink_count_before"] + 1
                and assistant_native_blink["closed"]["current_frame"] == "blink-closed"
                and assistant_native_blink["closed"]["current_frame_sha256"]
                == assistant_native_blink["closed"]["frame_sha256s"]["blink-closed"]
                and len({
                    assistant_native_blink["closed"]["frame_sha256s"]["idle"],
                    assistant_native_blink["closed"]["frame_sha256s"]["blink-open"],
                    assistant_native_blink["closed"]["frame_sha256s"]["blink-closed"],
                }) == 3
                and assistant_native_blink["settled"]["animation_locked"] is False
                and assistant_native_blink["settled"]["current_frame"] == "idle"
                and assistant_native_blink["settled"]["current_frame_sha256"]
                == assistant_native_blink["settled"]["frame_sha256s"]["idle"]
                and assistant_native_blink["working_projection_during_blink"]["blink_queue_suppressed"] is True
                and assistant_native_blink["working_projection_during_blink"]["queued"] is False
                and assistant_native_blink["working_projection_during_blink"]["queue_depth"] == 0
                and assistant_native_blink["after_working_projection"]["animation_state"] == "BLINK"
                and assistant_native_blink["after_working_projection"]["animation_locked"] is True
                and assistant_native_blink["post_settle"]["animation_locked"] is False
                and assistant_native_blink["post_settle"]["current_frame"] == "idle"
                and assistant_native_blink["post_settle"]["current_frame_sha256"]
                == assistant_native_blink["post_settle"]["frame_sha256s"]["idle"]
                and AUTO_BLINK_MIN_MS
                <= int(assistant_native_blink["settled"]["auto_blink_due_ms"] or 0)
                <= AUTO_BLINK_MAX_MS
                and assistant_native_blink["closed_screenshot"]["bytes"] > 2000
            ),
            "assistant_native_blink_deadline_survives_refresh": (
                assistant_blink_refresh_preservation["before"]["auto_blink_timer_enabled"] is True
                and assistant_blink_refresh_preservation["after"]["auto_blink_timer_enabled"] is True
                and assistant_blink_refresh_preservation["before"]["auto_blink_due_ms"]
                == assistant_blink_refresh_preservation["after"]["auto_blink_due_ms"]
                and all(
                    row["status"] == "PASS" and row["deduplicated"] is True
                    for row in assistant_blink_refresh_preservation["receipts"]
                )
                and assistant_blink_refresh_preservation["after"]["auto_blink_preserved_count"]
                >= assistant_blink_refresh_preservation["before"]["auto_blink_preserved_count"] + 2
            ),
            "assistant_safe_shortcut_restore": (
                assistant_restore["status"] == "ACKNOWLEDGED"
                and assistant_restore["system_autostart_writes"] == 0
                and assistant_restore["shell_command_calls"] == 0
            ),
            "assistant_pet_only_transparent_surface": (
                assistant_placement["width"] == 90
                and assistant_placement["height"] == 90
                and assistant_inspect["visible_debug_chrome_count"] == 0
                and assistant_inspect["pet_only_default"] is True
                and assistant_inspect["transparent_surface_exact"] is True
                and 0.95 <= assistant_inspect["pet_sprite_opacity"] <= 0.97
                and assistant_native["transparent_requested"] is True
                and assistant_native["form_border_style"] == "None"
                and assistant_native["show_in_taskbar"] is False
                and assistant_native["topmost"] is True
                and assistant_native["allow_transparency"] is True
                and assistant_native["opacity"] == 1.0
                and assistant_native["visible"] is True
                and assistant_native["back_color_argb"] == -986896
                and assistant_native["transparency_key_is_empty"] is True
                and assistant_native["per_pixel_alpha"] is True
                and assistant_native["webview_background_alpha"] == 0
                and assistant_native["webview_child_visible"] is False
                and assistant_native["webview_child_enabled"] is False
                and assistant_native["layered_window"] is True
                and assistant_native["no_activate_window"] is False
                and assistant_native["native_pet_overlay_ready"] is True
                and assistant_native["pet_hit_window_is_native_overlay"] is True
                and assistant_native["pet_hit_test_targets_assistant"] is True
                and assistant_native["host_matches_pet_surface"] is True
                and assistant_native["window_region_complex"] is True
                and assistant_native["transparent_corner_not_assistant"] is True
                and assistant_test_move["status"] == "PASS"
                and assistant_desktop_transparency["native_background_transparent"] is True
                and assistant_desktop_transparency["status"] == "PASS"
                and assistant_screenshot["bytes"] > 2000
            ),
            "assistant_native_host_has_no_selectable_invisible_frame": (
                assistant_placement["width"] == 90
                and assistant_placement["height"] == 90
                and assistant_native["host_matches_pet_surface"] is True
                and assistant_native["window_region_complex"] is True
                and assistant_native["transparent_corner_not_assistant"] is True
                and assistant_native["window_region_bounds"]["right"]
                    <= assistant_native["form_client_size"]["width"]
                and assistant_native["window_region_bounds"]["bottom"]
                    <= assistant_native["form_client_size"]["height"]
            ),
            "assistant_context_menu_complete_and_dismissible": (
                assistant_context_menu["native"]["opened"] is True
                and assistant_context_menu["native"]["auto_close"] is True
                and assistant_context_menu["native"]["context_menu_attached"] is True
                and assistant_context_menu["native"]["fully_within_working_area"] is True
                and assistant_context_menu["native"]["content_fully_visible"] is True
                and 3 <= assistant_context_menu["native"]["visible_action_count"] <= 6
                and assistant_context_menu["native"]["visible_actions"][0] == "OPEN_Desktop_MAIN"
                and assistant_context_menu["native"]["visible_actions"][-2:] == [
                    "OPEN_DESKTOP_ASSISTANT_SETTINGS",
                    "HIDE_DESKTOP_ASSISTANT",
                ]
                and assistant_context_menu["native"]["visible_labels"][0] == "打开 Memorive"
                and assistant_context_menu["native"]["visible_labels"][-2:] == [
                    "桌面助手设置", "暂时隐藏",
                ]
                and assistant_context_menu["native"]["outside_dismiss"]["closed"] is True
                and assistant_context_menu["native"]["escape_dismiss"]["closed"] is True
                and "AppClicked" in assistant_context_menu["native"]["outside_dismiss"]["menu_close_reasons"]
                and "Keyboard" in assistant_context_menu["native"]["escape_dismiss"]["menu_close_reasons"]
                and assistant_context_menu["html_fallback"]["disabled"] is True
                and assistant_context_menu["html_fallback"]["reason"]
                    == "HIDDEN_BOOTSTRAP_WEBVIEW_NO_VISUAL_AUTHORITY"
                and assistant_menu_screenshot["bytes"] > 2000
            ),
            "assistant_navigation_logo_left_show_right_hide_contract": (
                assistant_restore_after_transparency["status"] == "ACKNOWLEDGED"
                and assistant_hide["assistant_window_visibility"]["visible"] is False
                and assistant_hide["presentation"]["presentation"]["display_state"] == "HIDDEN"
                and assistant_hidden_native["visible"] is False
                and assistant_logo_toggle["route_before"] == "work-log"
                and assistant_logo_toggle["route_after"] == "work-log"
                and assistant_logo_toggle["active_logo_count"] == 1
                and assistant_logo_toggle["contextmenu"]["default_prevented"] is True
                and assistant_logo_toggle["contextmenu"]["request_count_after"]
                    == assistant_logo_toggle["contextmenu"]["request_count_before"] + 1
                and assistant_logo_toggle["contextmenu"]["action_id"] == "HIDE_DESKTOP_ASSISTANT"
                and assistant_logo_toggle["contextmenu"]["shortcut_status"] == "ACKNOWLEDGED"
                and assistant_logo_toggle["contextmenu"]["receipt_window_visible"] is False
                and assistant_logo_toggle["contextmenu"]["error"] is None
                and assistant_logo_toggle["contextmenu"]["route_after"] == "work-log"
                and assistant_logo_toggle["opened_by_left_click"]["receipt_window_visible"] is True
                and assistant_logo_toggle["shown_again_by_left_click"]["receipt_window_visible"] is True
                and all(
                    row["request_count_after"] == row["request_count_before"] + 1
                    and row["action_id"] == "SHOW_DESKTOP_ASSISTANT"
                    and row["shortcut_status"] == "ACKNOWLEDGED"
                    and row["error"] is None
                    for row in (
                        assistant_logo_toggle["opened_by_left_click"],
                        assistant_logo_toggle["shown_again_by_left_click"],
                    )
                )
                and assistant_reopened_native["visible"] is True
                and assistant_reopened_inspect["visible"] is True
            ),
            "ui_default_home_exact": (
                ui_regression["home"]["title"] == "Memorive"
                and ui_regression["home"]["prompt"] == "想构建什么呢？"
                and ui_regression["home"]["selected_nav_count"] == 0
                and ui_regression["home"]["back_hidden"] is True
            ),
            "ui_all_routes_real": len(ui_regression["routeRows"]) == 7 and all(
                row["active_surface_count"] == 1
                and row["selected_nav_count"] == 1
                and row["content_shell_present"] is True
                and row["placeholder_visible"] is False
                for row in ui_regression["routeRows"]
            ),
            "ui_settings_widths_and_mapping_exact": (
                ui_regression["settings"]["category_count"] == 13
                and ui_regression["settings"]["primary_initially_hidden"] is True
                and ui_regression["settings"]["tertiary_size_css"] == "480px"
                and 259 <= ui_regression["settings"]["api_two_pane"]["category_width"] <= 261
                and 259 <= ui_regression["settings"]["workflow_two_pane"]["category_width"] <= 261
                and 63 <= ui_regression["settings"]["node_layout"]["category_width"] <= 65
                and 51 <= ui_regression["settings"]["node_layout"]["category_inner_width"] <= 53
                and abs(
                    ui_regression["settings"]["node_layout"]["category_left_gutter"]
                    - ui_regression["settings"]["node_layout"]["category_right_gutter"]
                ) <= 1
                and 5 <= ui_regression["settings"]["node_layout"]["category_left_gutter"] <= 7
                and all(
                    abs(ui_regression["settings"]["node_layout"]["geometry_matches_initial"][field] - 6) <= 0.5
                    for field in ("container_padding_left", "container_padding_right")
                )
                and abs(ui_regression["settings"]["node_layout"]["geometry_matches_initial"]["initial"]["row_height"] - 60) <= 0.5
                and len(ui_regression["settings"]["node_layout"]["category_rows"]) == 13
                and all(
                    abs(row["height"] - 60) <= 0.5
                    and row["icon_center_x_delta"] <= 0.5
                    and row["icon_center_y_delta"] <= 0.5
                    for row in ui_regression["settings"]["node_layout"]["category_rows"]
                )
                and abs(
                    ui_regression["settings"]["node_layout"]["geometry_matches_initial"]["icon_width"]
                    - ui_regression["settings"]["node_layout"]["geometry_matches_initial"]["initial"]["icon_width"]
                ) <= 0.5
                and abs(
                    ui_regression["settings"]["node_layout"]["geometry_matches_initial"]["icon_height"]
                    - ui_regression["settings"]["node_layout"]["geometry_matches_initial"]["initial"]["icon_height"]
                ) <= 0.5
                and ui_regression["settings"]["node_layout"]["primary_width"] >= 540
                and 479 <= ui_regression["settings"]["node_layout"]["tertiary_width"] <= 481
                and ui_regression["settings"]["node_layout"]["primary_resizer_hidden"] is True
                and ui_regression["settings"]["node_layout"]["flow_center_delta"] <= 1.5
                and ui_regression["settings"]["node_layout"]["node_open"] is True
                and abs(
                    ui_regression["settings"]["after_node_category_layout"]["category_width"]
                    - ui_regression["settings"]["api_two_pane"]["category_width"]
                ) <= 1
                and ui_regression["settings"]["after_node_category_layout"]["node_open"] is False
                and ui_regression["settings"]["after_node_category_layout"]["tertiary_hidden"] is True
                and ui_regression["settings"]["after_node_category_layout"]["primary_resizer_visible"] is True
                and 1 <= ui_regression["settings"]["flow_stage_width"] <= 660.5
                and 1 <= ui_regression["settings"]["flow_list_width"] <= 560.5
                and ui_regression["settings"]["preference_count"] == 29
            ),
            "ui_settings_node_model_layout_and_selection_exact": (
                ui_regression["settings"]["node_layout"]["model_layout"]["description_hidden"] is True
                and ui_regression["settings"]["node_layout"]["model_layout"]["description_empty"] is True
                and ui_regression["settings"]["node_layout"]["model_layout"]["heading_before_choice"] is True
                and ui_regression["settings"]["node_layout"]["model_layout"]["name_inside_choice"] is True
                and ui_regression["settings"]["node_layout"]["model_layout"]["full_name"]
                == "GPT5.6Luna-standard-with-complete-model-name"
                and ui_regression["settings"]["node_layout"]["model_layout"]["full_name_visible"] is True
                and ui_regression["settings"]["node_layout"]["model_layout"]["model_white_space"] == "normal"
                and ui_regression["settings"]["node_layout"]["model_layout"]["model_overflow_wrap"] == "anywhere"
                and ui_regression["settings"]["node_layout"]["model_layout"]["adjust_width"] >= 57
                and ui_regression["settings"]["node_layout"]["model_layout"]["adjust_text"] == "调整"
                and ui_regression["settings"]["node_layout"]["model_layout"]["adjust_white_space"] == "nowrap"
                and ui_regression["settings"]["node_layout"]["selected_treatment"]["background_matches_nav"] is True
                and ui_regression["settings"]["node_layout"]["selected_treatment"]["text_matches_nav"] is True
                and ui_regression["settings"]["node_layout"]["selected_treatment"]["category_font_weight"] == "500"
                and ui_regression["settings"]["node_layout"]["selected_treatment"]["nav_font_weight"] == "500"
            ),
            "ui_settings_custom_provider_and_save_exact": (
                ui_regression["settings"]["provider_control_tag"] == "INPUT"
                and ui_regression["settings"]["provider_control_type"] == "text"
                and ui_regression["settings"]["custom_provider"] == native_provider
                and ui_regression["settings"]["second_provider"] == native_provider_2
                and ui_regression["settings"]["custom_provider_persisted"] is True
                and ui_regression["settings"]["second_provider_persisted"] is True
                and ui_regression["settings"]["custom_credential_ref"].startswith("REF:windows:Memorive/custom-v1-")
                and ui_regression["settings"]["second_credential_ref"].startswith("REF:windows:Memorive/custom-v1-")
                and ui_regression["settings"]["credential_cleaned"] is True
                and ui_regression["settings"]["model_service_cleaned"] is True
                and ui_regression["settings"]["empty_layout"]["visible"] is True
                and ui_regression["settings"]["empty_layout"]["heading_width"] > 90
                and ui_regression["settings"]["empty_layout"]["copy_width"] > 180
                and ui_regression["settings"]["empty_layout"]["non_overlapping"] is True
                and ui_regression["settings"]["empty_visible_after_cleanup"] is True
                and ui_regression["settings"]["save_action"] == "SAVE"
                and ui_regression["settings"]["saved_font_scale"] == 110
                and ui_regression["settings"]["saved_full_tooltips"] is False
            ),
            "ui_settings_orphan_credential_reference_reused_exact": (
                ui_regression["settings"]["orphan_reference"]["reused"] is True
                and ui_regression["settings"]["orphan_reference"]["created_ref"]
                == ui_regression["settings"]["orphan_reference"]["reused_ref"]
                and ui_regression["settings"]["orphan_reference"]["service_count_before"] == 0
                and ui_regression["settings"]["orphan_reference"]["provider_reference_count_before"] == 1
                and ui_regression["settings"]["orphan_reference"]["provider_reference_count_after"] == 1
                and ui_regression["settings"]["orphan_reference"]["provider_service_count_after"] == 1
            ),
            "ui_settings_long_error_toast_wraps_exact": (
                ui_regression["settings"]["long_toast"]["within_window"] is True
                and ui_regression["settings"]["long_toast"]["text_fits_width"] is True
                and ui_regression["settings"]["long_toast"]["wraps_to_multiple_lines"] is True
                and ui_regression["settings"]["long_toast"]["overflow_wrap"] == "anywhere"
            ),
            "ui_settings_duplicate_provider_rejected_exact": (
                ui_regression["settings"]["duplicate_provider"]["result_is_null"] is True
                and ui_regression["settings"]["duplicate_provider"]["revision_unchanged"] is True
                and ui_regression["settings"]["duplicate_provider"]["service_count_unchanged"] is True
                and ui_regression["settings"]["duplicate_provider"]["invalid_class"] is True
                and ui_regression["settings"]["duplicate_provider"]["aria_invalid"] == "true"
                and ui_regression["settings"]["duplicate_provider"]["status_copy"] == "保存失败-该模型配置已存在"
                and ui_regression["settings"]["duplicate_provider"]["red_background"] == "rgb(251, 239, 236)"
            ),
            "ui_settings_mode_scope_and_advanced_profile_exact": (
                ui_regression["settings"]["mode_scope"] == {
                    "hidden_in_directory": True,
                    "visible_in_api": True,
                    "hidden_in_viewer": True,
                    "visible_in_workflow": True,
                    "workflow_visible_modes": ["normal", "developer"],
                }
                and ui_regression["settings"]["normal_model_form"]["model_name_visible"] is True
                and ui_regression["settings"]["normal_model_form"]["plan_values"]
                == ["ECONOMY", "STANDARD", "HIGH_QUALITY", "MAXIMUM"]
                and ui_regression["settings"]["normal_model_form"]["plan_labels"]
                == ["经济", "标准", "高质量", "最高"]
                and ui_regression["settings"]["normal_model_form"]["selected_plan"] == "STANDARD"
                and ui_regression["settings"]["normal_model_form"]["thinking_is_switch"] is True
                and ui_regression["settings"]["normal_model_form"]["tier_input_hidden"] is True
                and ui_regression["settings"]["normal_model_form"]["thinking_hidden"] is True
                and ui_regression["settings"]["normal_model_form"]["text_thinking_absent"] is True
                and [
                    row["requested"]
                    for row in ui_regression["settings"]["normal_model_form"]["tier_interaction"]
                ] == ["ECONOMY", "STANDARD", "HIGH_QUALITY", "MAXIMUM"]
                and all(
                    row["selected"] == row["requested"]
                    and row["selected_aria_checked"] == "true"
                    and row["visual_difference"] is True
                    and row["page_save_enabled"] is True
                    and row["selected_box_shadow"] != "none"
                    for row in ui_regression["settings"]["normal_model_form"]["tier_interaction"]
                )
                and ui_regression["settings"]["advanced_model_form"]["tier_visible"] is True
                and ui_regression["settings"]["advanced_model_form"]["plan_hidden"] is True
                and ui_regression["settings"]["advanced_model_form"]["thinking_is_same_switch"] is True
                and ui_regression["settings"]["advanced_model_form"]["tier_label_width"] > 180
                and ui_regression["settings"]["advanced_model_form"]["tier_label_height"] < 100
                and ui_regression["settings"]["advanced_model_form"]["tier_label_white_space"] == "nowrap"
                and ui_regression["settings"]["advanced_model_form"]["tier_label_writing_mode"] == "horizontal-tb"
                and ui_regression["settings"]["persisted_model_profile"]["model_name"] == "sonnet5"
                and ui_regression["settings"]["persisted_model_profile"]["tier"] == "high-quality"
                and ui_regression["settings"]["persisted_model_profile"]["plan"] == "HIGH_QUALITY"
                and ui_regression["settings"]["persisted_model_profile"]["thinking_mode"] == "thinking"
                and ui_regression["settings"]["persisted_second_model_profile"]["model_name"] == "GPT5.6Luna"
                and ui_regression["settings"]["persisted_second_model_profile"]["tier"] == "standard"
                and ui_regression["settings"]["persisted_second_model_profile"]["plan"] == "STANDARD"
                and ui_regression["settings"]["persisted_second_model_profile"]["thinking_mode"] == ""
                and ui_regression["settings"]["draft_after_save"] == {
                    "provider": "",
                    "api_key": "",
                    "model_name": "",
                    "tier": "",
                    "thinking_enabled": False,
                    "closed": True,
                }
                and ui_regression["settings"]["local_reference"]["card_present"] is True
                and ui_regression["settings"]["local_reference"]["model_card_heading"] == "sonnet5"
                and ui_regression["settings"]["local_reference"]["heading"] == "本地引用存在"
                and ui_regression["settings"]["local_reference"]["button_text"] == "再次检查"
                and ui_regression["settings"]["local_reference"]["neutral_class"] is True
                and ui_regression["settings"]["local_reference"]["yellow_class"] is False
                and ui_regression["settings"]["local_reference"]["background_color"] == "rgb(255, 255, 255)"
            ),
            "ui_settings_developer_code_editors_write_only_coloured_diff_and_persistent": (
                ui_regression["settings"]["developer_modes"]["api"]["workbench_visible_before_save"] is True
                and ui_regression["settings"]["developer_modes"]["api"]["normal_list_hidden_before_save"] is True
                and ui_regression["settings"]["developer_modes"]["api"]["workbench_visible"] is True
                and ui_regression["settings"]["developer_modes"]["api"]["normal_list_hidden"] is True
                and ui_regression["settings"]["developer_modes"]["api"]["schema_version"] == "DesktopDeveloperApi-v2"
                and ui_regression["settings"]["developer_modes"]["api"]["secret_field_block"]
                == "DEVELOPER_SECRET_FIELD_FORBIDDEN"
                and ui_regression["settings"]["developer_modes"]["api"]["unknown_field_block"]
                == "DEVELOPER_UNKNOWN_FIELD"
                and ui_regression["settings"]["developer_modes"]["api"]["credential_values_absent"] is True
                and ui_regression["settings"]["developer_modes"]["api"]["api_key_fields_present"] is True
                and ui_regression["settings"]["developer_modes"]["api"]["api_key_fields_empty_after_save"] is True
                and ui_regression["settings"]["developer_modes"]["api"]["credential_refs_only"] is True
                and ui_regression["settings"]["developer_modes"]["api"]["line_number_count"]
                == ui_regression["settings"]["developer_modes"]["api"]["editor_line_count"]
                and ui_regression["settings"]["developer_modes"]["api"]["save_action"] == "SAVE"
                and ui_regression["settings"]["developer_modes"]["api"]["revision_increased"] is True
                and ui_regression["settings"]["developer_modes"]["api"]["persisted_service_count"] == 3
                and ui_regression["settings"]["developer_modes"]["api"]["developer_created_service_present"] is True
                and ui_regression["settings"]["developer_modes"]["api"]["developer_created_credential_ref"].startswith("REF:windows:Memorive/custom-v1-")
                and ui_regression["settings"]["developer_modes"]["api"]["developer_created_service_removed_after_probe"] is True
                and ui_regression["settings"]["developer_modes"]["api"]["status_copy"] == "配置与只写凭据已持久化；API Key 已从编辑器清空。"
                and ui_regression["settings"]["developer_modes"]["api"]["credential_write_count"] >= 2
                and ui_regression["settings"]["developer_modes"]["api"]["api_key_scrub_count"] >= 2
                and ui_regression["settings"]["developer_modes"]["api"]["visual_before_save"]["syntax_token_count"] >= 20
                and ui_regression["settings"]["developer_modes"]["api"]["visual_before_save"]["syntax_colour_count"] >= 3
                and ui_regression["settings"]["developer_modes"]["api"]["visual_before_save"]["diff_added_count"] >= 1
                and ui_regression["settings"]["developer_modes"]["api"]["visual_before_save"]["diff_removed_count"] >= 1
                and ui_regression["settings"]["developer_modes"]["api"]["visual_before_save"]["backgrounds_differ"] is True
                and ui_regression["settings"]["developer_modes"]["workflow"]["workbench_visible_before_save"] is True
                and ui_regression["settings"]["developer_modes"]["workflow"]["flow_hidden_before_save"] is True
                and ui_regression["settings"]["developer_modes"]["workflow"]["tertiary_open_before_switch"] is True
                and ui_regression["settings"]["developer_modes"]["workflow"]["tertiary_closed_before_developer"] is True
                and ui_regression["settings"]["developer_modes"]["workflow"]["workbench_visible"] is True
                and ui_regression["settings"]["developer_modes"]["workflow"]["flow_hidden"] is True
                and ui_regression["settings"]["developer_modes"]["workflow"]["schema_version"] == "DesktopDeveloperWorkflow-v1"
                and ui_regression["settings"]["developer_modes"]["workflow"]["unknown_field_block"]
                == "DEVELOPER_UNKNOWN_FIELD"
                and ui_regression["settings"]["developer_modes"]["workflow"]["node_set_preserved"] is True
                and ui_regression["settings"]["developer_modes"]["workflow"]["line_number_count"]
                == ui_regression["settings"]["developer_modes"]["workflow"]["editor_line_count"]
                and ui_regression["settings"]["developer_modes"]["workflow"]["save_action"] == "SAVE"
                and ui_regression["settings"]["developer_modes"]["workflow"]["revision_increased"] is True
                and ui_regression["settings"]["developer_modes"]["workflow"]["status_copy"] == "本地校验与持久化均已完成。"
                and ui_regression["settings"]["developer_modes"]["workflow"]["visual_before_save"]["syntax_token_count"] >= 20
                and ui_regression["settings"]["developer_modes"]["workflow"]["visual_before_save"]["syntax_colour_count"] >= 4
                and ui_regression["settings"]["developer_modes"]["workflow"]["visual_before_save"]["diff_added_count"] >= 1
                and ui_regression["settings"]["developer_modes"]["workflow"]["visual_before_save"]["diff_removed_count"] >= 1
                and ui_regression["settings"]["developer_modes"]["workflow"]["visual_before_save"]["backgrounds_differ"] is True
            ),
            "ui_settings_network_location_uses_real_flag_graphic": (
                ui_regression["settings"]["network_flag"]["country_code"] == "JP"
                and ui_regression["settings"]["network_flag"]["colo"] == "NRT"
                and ui_regression["settings"]["network_flag"]["flag_present"] is True
                and ui_regression["settings"]["network_flag"]["svg_present"] is True
                and ui_regression["settings"]["network_flag"]["role"] == "img"
                and ui_regression["settings"]["network_flag"]["graphic_child_count"] >= 2
                and "JP" in ui_regression["settings"]["network_flag"]["country_text"]
                and "NRT" in ui_regression["settings"]["network_flag"]["country_text"]
            ),
            "ui_settings_configured_api_mapping_and_cleanup_exact": (
                ui_regression["settings"]["mapping"]["option_count"] == 2
                and ui_regression["settings"]["mapping"]["enabled_count"] == 2
                and ui_regression["settings"]["mapping"]["configured_profile_present"] is True
                and ui_regression["settings"]["mapping"]["configured_option_label"] == "sonnet5-high-quality-thinking"
                and ui_regression["settings"]["mapping"]["sample_option_count"] == 0
                and ui_regression["settings"]["mapping"]["every_option_is_configured"] is True
                and ui_regression["settings"]["mapping"]["mapped_profile_refs"] == {
                    "card_distill": ui_regression["settings"]["persisted_model_profile"]["config_id"],
                    "transport_review": ui_regression["settings"]["persisted_second_model_profile"]["config_id"],
                    "analysis": ui_regression["settings"]["persisted_model_profile"]["config_id"],
                    "judgment_review": ui_regression["settings"]["persisted_second_model_profile"]["config_id"],
                }
                and all(
                    next(
                        option for option in ui_regression["settings"]["mapping"]["nodes"][reviewer]["options"]
                        if option["profile_ref"] == ui_regression["settings"]["persisted_model_profile"]["config_id"]
                    )["disabled"] is True
                    and next(
                        option for option in ui_regression["settings"]["mapping"]["nodes"][reviewer]["options"]
                        if option["profile_ref"] == ui_regression["settings"]["persisted_second_model_profile"]["config_id"]
                    )["disabled"] is False
                    for reviewer in ("card_review", "judgment_review")
                )
                and ui_regression["settings"]["mapping"]["cleanup_profile_ref"] == "ANALYSIS_PROFILE"
                and ui_regression["settings"]["mapping"]["cleanup_profile_refs"] == {
                    "card_distill": "DISTILLATION_PROFILE",
                    "transport_review": "AUTO_HETEROGENEOUS",
                    "analysis": "ANALYSIS_PROFILE",
                    "judgment_review": "AUTO_HETEROGENEOUS",
                }
            ),
            "ui_current_task_uses_saved_model_catalog_for_unstarted_nodes": (
                ui_regression["settings"]["current_task_models"]["tasks"]["configured_model_count"] == 2
                and ui_regression["settings"]["current_task_models"]["card"]["selected_profile_ref"]
                == ui_regression["settings"]["persisted_second_model_profile"]["config_id"]
                and ui_regression["settings"]["current_task_models"]["card"]["selected_model"] == "GPT5.6Luna-standard"
                and len(ui_regression["settings"]["current_task_models"]["card"]["options"]) == 2
                and all(
                    option["disabled"] is False
                    for option in ui_regression["settings"]["current_task_models"]["card"]["options"]
                )
                and ui_regression["settings"]["current_task_models"]["analysis"]["selected_profile_ref"]
                == ui_regression["settings"]["persisted_model_profile"]["config_id"]
                and ui_regression["settings"]["current_task_models"]["analysis"]["selected_model"] == "sonnet5-high-quality-thinking"
                and len(ui_regression["settings"]["current_task_models"]["analysis"]["options"]) == 2
                and all(
                    option["disabled"] is False
                    for option in ui_regression["settings"]["current_task_models"]["analysis"]["options"]
                )
            ),
            "ui_settings_directories_and_obsidian_live": (
                ui_regression["settings"]["external_library"]["provider_kind"] == "OBSIDIAN"
                and ui_regression["settings"]["external_library"]["enabled"] is True
                and ui_regression["settings"]["external_library"]["display_name"] == "研究资料库"
                and ui_regression["settings"]["external_library"]["root"] == str(native_obsidian_root)
                and ui_regression["settings"]["external_library"]["workspace_root"] == str(native_workspace_root)
                and ui_regression["settings"]["external_library"]["artifact_root"] == str(native_artifact_root)
                and ui_regression["settings"]["external_library"]["external_refresh"] is True
                and ui_regression["settings"]["external_library"]["status_copy"] == "本地可用"
                and ui_regression["settings"]["external_library"]["display_name_help_absent"] is True
                and ui_regression["settings"]["external_library"]["display_name_placeholder"] == "研究资料库"
                and ".obsidian" in ui_regression["settings"]["external_library"]["root_help"]
                and ui_regression["settings"]["external_library"]["alignment"]["input_top_delta"] <= 1
                and ui_regression["settings"]["external_library"]["alignment"]["input_height_delta"] <= 1
                and ui_regression["settings"]["external_library"]["alignment"]["root_help_below_input"] is True
                and directory_runtime_evidence["workspace_effective"] == str(native_workspace_root.resolve())
                and directory_runtime_evidence["artifact_effective"] == str(native_artifact_root.resolve())
                and directory_runtime_evidence["external_library_effective"] == str(native_obsidian_root.resolve())
                and directory_runtime_evidence["external_library_test"]["status"] == "PASS"
                and directory_runtime_evidence["external_library_test"]["marker"] == ".obsidian"
                and directory_runtime_evidence["external_library_test"]["external_network_calls"] == 0
                and directory_runtime_evidence["external_library_test"]["external_process_launches"] == 0
            ),
            "ui_windows_notification_settings_and_native_presenter_live": (
                ui_regression["settings"]["notification"]["error_toggle_cycle"]
                == {"before": "true", "off": "false", "on_again": "true"}
                and all(
                    ui_regression["settings"]["notification"]["persisted"][key] is True
                    for key in (
                        "notifications_enabled",
                        "task_complete_notification",
                        "task_error_notification",
                        "approval_notification",
                        "weekly_report_notification",
                        "notification_sound",
                        "notification_open_task",
                    )
                )
                and ui_regression["settings"]["notification"]["persisted"]["do_not_disturb_start"] == "00:00"
                and ui_regression["settings"]["notification"]["persisted"]["do_not_disturb_end"] == "00:00"
                and ui_regression["settings"]["notification"]["receipt"]["status"] == "DELIVERED"
                and ui_regression["settings"]["notification"]["receipt"]["windows_presenter_called"] is True
                and ui_regression["settings"]["notification"]["receipt"]["private_body_included"] is False
                and ui_regression["settings"]["notification"]["receipt"]["presenter_result"]["status"] == "PASS"
                and ui_regression["settings"]["notification"]["receipt"]["presenter_result"]["native_notify_icon_api_called"] is True
                and ui_regression["settings"]["notification"]["receipt"]["presenter_result"]["sound_played"] is True
                and ui_regression["settings"]["notification"]["receipt"]["presenter_result"]["sound_count"] >= 1
            ),
            "ui_three_languages_font_and_tooltips_live": (
                ui_regression["appearance"]["english"]["view_name"] == "Inbox"
                and ui_regression["appearance"]["english"]["settings_nav"] is True
                and ui_regression["appearance"]["english"]["file_title_unchanged"] is True
                and ui_regression["appearance"]["english"]["native_title_removed"] is True
                and ui_regression["appearance"]["english"]["no_tooltip_marker"] is True
                and ui_regression["appearance"]["english"]["tooltips_dataset"] == "false"
                and ui_regression["appearance"]["english"]["language_labels"] == {
                    "native": ["简体中文", "English", "日本語"],
                    "enhanced": ["简体中文", "English", "日本語"],
                }
                and ui_regression["appearance"]["english"]["basic_tooltip_visible"] is True
                and ui_regression["appearance"]["japanese"]["view_name"] == "受信箱"
                and ui_regression["appearance"]["japanese"]["settings_nav"] is True
                and ui_regression["appearance"]["japanese"]["file_title_unchanged"] is True
                and ui_regression["appearance"]["japanese"]["native_title_removed"] is True
                and ui_regression["appearance"]["japanese"]["no_tooltip_marker"] is True
                and ui_regression["appearance"]["japanese"]["tooltips_dataset"] == "true"
                and ui_regression["appearance"]["japanese"]["language_labels"] == {
                    "native": ["简体中文", "English", "日本語"],
                    "enhanced": ["简体中文", "English", "日本語"],
                }
                and ui_regression["appearance"]["japanese"]["enabled_tooltip_visible"] is True
                and ui_regression["appearance"]["tooltip_repeat_cycle"] == {
                    "first_disabled_hidden": False,
                    "first_basic_visible": True,
                    "first_enabled_visible": True,
                    "second_disabled_hidden": False,
                    "second_basic_visible": True,
                    "second_enabled_visible": True,
                }
                and ui_regression["appearance"]["font_scale_changes_geometry"] is True
                and ui_regression["appearance"]["restored_document_setting_absent"] is True
            ),
            "ui_workspace_responsive_headers_and_shared_typography": (
                len(ui_regression["appearance"]["headers"]["chinese"]) == 7
                and len(ui_regression["appearance"]["headers"]["english"]) == 7
                and ui_regression["appearance"]["headers"]["responsive_headers_fit"] is True
                and all(
                    abs(row["title_font_size"] - 24) <= 0.1
                    for row in ui_regression["appearance"]["headers"]["chinese"]
                )
            ),
            "ui_startup_restore_last_page_live": (
                ui_regression["startup"] == {
                    "restored_route": "messages",
                    "restored_enabled": True,
                    "disabled_route": "home",
                    "disabled_enabled": False,
                }
            ),
            "ui_business_back_exact": (
                ui_regression["settings"]["back_control"] is True
                and ui_regression["settings"]["back_gap"] == "4px"
                and ui_regression["settings"]["back_transform"] in {
                    "matrix(1, 0, 0, 1, 0, 4)",
                    "matrix(1, 0, 0, 1, 0, 4.0000)",
                }
            ),
            "ui_messages_projection_and_detail_exact": (
                ui_regression["messages"]["detail_open"] is True
                and ui_regression["messages"]["empty_visible"] is False
                and ui_regression["messages"]["card_count"]
                == bridge_bootstraps["messages"]["record_count"]
                and ui_regression["messages"]["close_control"] is True
                and ui_regression["messages"]["close_is_left_of_title"] is True
                and ui_regression["messages"]["duplicate_close_count"] == 1
            ),
            "ui_sessions_search_and_ratio_exact": (
                bridge_bootstraps["sessions"]["status"] == "PASS"
                and bridge_bootstraps["sessions"]["synthetic_only"] is False
                and bridge_bootstraps["sessions"]["record_count"] >= 2
                and bridge_bootstraps["sessions"]["dom_binding_count"] >= 2
                and ui_regression["sessions"]["search_closed"]["hidden"] is True
                and ui_regression["sessions"]["search_closed"]["value"] == ""
                and ui_regression["sessions"]["search_closed"]["aria_expanded"] == "false"
                and ui_regression["sessions"]["search_closed"]["focus_returned"] is True
                and ui_regression["sessions"]["close_control"] is True
                and ui_regression["sessions"]["duplicate_close_count"] == 1
                # Visible-UI providers may contribute additional read-only rows.
                # Keep the deterministic local baseline as a lower bound instead
                # of rejecting a successful live Kimi/DeepSeek projection.
                and ui_regression["sessions"]["real_card_count"] >= 2
                and ui_regression["sessions"]["rendered_turn_count"] >= 2
                and ui_regression["sessions"]["sample_badge_count"] == 0
                and 0.38 <= ui_regression["sessions"]["list_ratio"] <= 0.42
                and 11.5 <= ui_regression["sessions"]["left_gutter"] <= 12.5
                and abs(
                    ui_regression["sessions"]["bottom_gutter"]
                    - ui_regression["sessions"]["left_gutter"]
                ) <= 0.5
            ),
            "ui_library_projection_and_log_ratio_exact": (
                ui_regression["library"]["detail_open"] is True
                and ui_regression["library"]["empty_visible"] is False
                and ui_regression["library"]["card_count"]
                == bridge_bootstraps["library"]["record_count"]
                and ui_regression["library"]["close_control"] is True
                and ui_regression["library"]["duplicate_close_count"] == 1
                and ui_regression["workLog"]["close_control"] is True
                and ui_regression["workLog"]["duplicate_close_count"] == 1
                and 0.58 <= ui_regression["workLog"]["central_ratio"] <= 0.62
            ),
            "ui_real_pdf_preview_exact": (
                ui_regression["pdf"]["card_present"] is True
                and ui_regression["pdf"]["selected"] is True
                and ui_regression["pdf"]["title"] == native_pdf_item["source_name"]
                and ui_regression["pdf"]["detail_open"] is True
                and 0.58 <= ui_regression["pdf"]["primary_ratio"] <= 0.62
                and ui_regression["pdf"]["pdf_canvas_present"] is True
                and ui_regression["pdf"]["pdf_canvas_rendered"] is True
                and ui_regression["pdf"]["pdf_canvas_width"] > 100
                and ui_regression["pdf"]["pdf_canvas_height"] > 100
                and ui_regression["pdf"]["pdf_page_status"].startswith("第 1 / ")
                and ui_regression["pdf"]["pdf_embed_absent"] is True
                and ui_regression["pdf"]["sample_label_absent"] is True
                and sha256_file(native_pdf_source) == native_pdf_source_sha256
            ),
            "assistant_left_click_focus_preserves_main_state": (
                pet_focus["native_event"]["status"] == "PASS"
                and pet_focus["native_event"]["before"]["picture_visible"] is True
                and pet_focus["native_event"]["before"]["picture_enabled"] is True
                and pet_focus["activation_count_after"] == pet_focus["activation_count_before"] + 1
                and pet_focus["native_event"]["after"]["interaction_animation_count"]
                == pet_focus["native_event"]["before"]["interaction_animation_count"] + 1
                and pet_focus["native_event"]["after"]["animation_state"] == "INTERACTION"
                and pet_focus["native_event"]["settled"]["animation_locked"] is False
                and pet_focus["native_event"]["settled"]["current_frame"]
                == pet_focus["native_event"]["settled"]["base_frame"]
                and pet_focus["native_event"]["settled"]["animation_completed_count"]
                == pet_focus["native_event"]["before"]["animation_completed_count"] + 1
                and pet_focus["state_before"] == pet_focus["state_after"]
                and pet_focus["state_after"]["route"] == "inbox"
                and pet_focus["state_after"]["detail_aria_hidden"] == "false"
                and pet_focus["state_after"]["selected_item_id"] == native_pdf_item["item_id"]
                and pet_focus["receipt"]["route_preserved"] is True
                and pet_focus["receipt"]["navigation_invoked"] is False
                and pet_focus["receipt"]["status"] == "PASS"
            ),
            "assistant_physical_drag_spans_full_desktop_visible_bounds": (
                native_pet_drag_top_left["status"] == "PASS"
                and native_pet_drag_top_left["follows_pointer"] is True
                and native_pet_drag["status"] == "PASS"
                and native_pet_drag["follows_pointer"] is True
                and native_pet_drag["after"]["drag_active"] is False
                and native_pet_drag["after"]["drag_timer_enabled"] is False
                and native_pet_drag["after"]["drag_move_count"]
                > native_pet_drag["before"]["drag_move_count"]
                and native_pet_drag["after"]["primary_activation_count"]
                == native_pet_drag["before"]["primary_activation_count"]
                and abs(
                    native_pet_drag_top_left["after"]["visible_screen_bounds"]["left"]
                    - primary_assistant_area.left
                ) <= 2
                and abs(
                    native_pet_drag_top_left["after"]["visible_screen_bounds"]["top"]
                    - primary_assistant_area.top
                ) <= 2
                and abs(
                    native_pet_drag["after"]["visible_screen_bounds"]["right"]
                    - primary_assistant_area.right
                ) <= 2
                and abs(
                    native_pet_drag["after"]["visible_screen_bounds"]["bottom"]
                    - primary_assistant_area.bottom
                ) <= 2
                and abs(native_pet_drag["actual_delta"]["x"])
                    >= (primary_assistant_area.right - primary_assistant_area.left) - visible_width - 4
                and abs(native_pet_drag["actual_delta"]["y"])
                    >= (primary_assistant_area.bottom - primary_assistant_area.top) - visible_height - 4
                and native_pet_desktop_span["enumerated_monitor_count"] >= 1
            ),
            "assistant_completion_flood_drag_over_main_stays_single_and_responsive": (
                native_completion_drag_stress["delivery_count"] == 100
                and native_completion_drag_stress["errors"] == []
                and native_completion_drag_stress["receipts"][0]["single_shot_started"] is True
                and native_completion_drag_stress["after"]["terminal_animation_started_count"]
                    == native_completion_drag_stress["before"]["terminal_animation_started_count"] + 1
                and (
                    native_completion_drag_stress["after"]["coalesced_terminal_count"]
                    - native_completion_drag_stress["before"]["coalesced_terminal_count"]
                    + native_completion_drag_stress["after"]["interrupted_presentation_count"]
                    - native_completion_drag_stress["before"]["interrupted_presentation_count"]
                ) >= 99
                and native_completion_drag_stress["drag"]["status"] == "PASS"
                and native_completion_drag_stress["drag"]["follows_pointer"] is True
                and native_completion_drag_stress["after"]["animation_locked"] is False
                and native_completion_drag_stress["after"]["drag_active"] is False
                and native_completion_drag_stress["after"]["drag_timer_enabled"] is False
                and native_completion_drag_stress["after"]["render_cache_count"]
                    <= native_completion_drag_stress["after"]["render_cache_limit"]
                and native_completion_drag_stress["after"]["receipt_record_error_count"] == 0
                and all(
                    receipt["durable_receipt_status"] == "PASS"
                    for receipt in native_completion_drag_stress["receipts"]
                )
                and native_completion_drag_stress["main_window_responding"] is True
                and native_completion_drag_stress["main_webview_probe"]["alive"] is True
                and native_completion_drag_stress["main_webview_probe"]["surface_count"] == len(EXPECTED_DESKTOP_ROUTES)
                and native_completion_drag_stress["main_screenshot"]["bytes"] > 10000
                and native_completion_drag_stress["transparency"]["native_background_transparent"] is True
                and native_completion_drag_stress["transparency"]["status"] == "PASS"
            ),
            "ui_delete_selected_card_closes_detail": (
                deleted_selected_detail["deleted_item_id"] == native_pdf_item["item_id"]
                and deleted_selected_detail["card_absent"] is True
                and deleted_selected_detail["detail_aria_hidden"] == "true"
                and deleted_selected_detail["selected_card_count"] == 0
                and deleted_selected_detail["preview_child_count"] == 0
                and deleted_selected_detail["bridge_selected_item_id"] is None
                and deleted_selected_detail["bridge_inspector_open"] is False
            ),
            "ui_remaining_test_tracks_visible": ui_regression["sampleBadges"] >= 1,
            "integrated_default_activation": integrated_activation["route"] == "work-log",
            "worklog_root_present": inspect["root"] is True,
            "all_page_roots_present": inspect["pageRootCount"] == len(EXPECTED_DESKTOP_ROUTES),
            "product_window_present": inspect["productWindow"] is True,
            "navigation_exact_count": inspect["navCount"] == len(EXPECTED_DESKTOP_ROUTES),
            "work_log_cards_exact_count": inspect["workLogCardCount"] == 7,
            "work_log_dom_bindings_exact": bootstrap["record_count"] == 7 and bootstrap["dom_binding_count"] == 7 and inspect["workLogBridge"]["binding_count"] == 7,
            "iframe_zero": inspect["iframeCount"] == 0,
            "visible_placeholder_zero": inspect["integrated"]["visible_placeholder_count"] == 0,
            "preview_controls_zero": inspect["previewControlCount"] == 0,
            "typed_navigation": typed["transport_status"] == "LOCAL_PYWEBVIEW_DISPATCHED",
            "contract_d36_exact": contract["d36_actions"] == ['filter','export_redacted_view'] and contract["d36_queries"] == ['list_log_entries','resolve_job_locator'],
            "synthetic_stream_exact": all_rows["row_count"] == 7 and branch["row_count"] == 4 and central["row_count"] == 3,
            "search_exact": searched["row_count"] == 1 and searched["rows"][0]["parse_status"] == "partial",
            "detail_public_safe": detail["entry"]["public_safe_projection"] is True and detail["entry"]["absolute_path_included"] is False,
            "milestones_public_safe": milestones["milestone_count"] == 2 and milestones["raw_private_content_included"] is False,
            "selection_identities": selected["job_id"] == "MODEL-CONFIGURATION/CROSS" and isinstance(selected["event_sequence"], int),
            "filter_state": filtered["row_count"] == 1,
            "job_locator_exact": job["status"] == "PASS" and job["fuzzy_match_attempted"] is False,
            "evidence_locator_exact": evidence["match_count"] == 1 and evidence["absolute_path_included"] is False,
            "source_hash_metadata_only": sources["source_count"] == 3 and sources["raw_authority_content_read"] is False and sources["source_mutations"] == 0,
            "refresh_selection_preserved": refreshed["event_type"] == "log_projection_refreshed" and refreshed["selection_preserved"] is True,
            "export_preview_redacted": preview["redacted_field_count"] >= 9 and preview["canary_value_hits"] == 0 and preview["files_written"] == 0,
            "atomic_export": exported["atomic_write"] is True and exported["partial_final_output"] is False and exported["manifest"]["source_authority_mutations"] == 0,
            "export_verified": verified["verification_result"] == "PASS" and all(verified["checks"].values()),
            "fault_matrix_eight": len(faults) == 8 and all(row["status"] == "PASS" and row["restricted_effects"] == 0 for row in faults),
            "restart_recovery": restored["status"] == "PASS" and restored["stable_locator_replay"] is True,
            "restricted_effects_zero": all(effects[key] == 0 for key in restricted) and effects["restricted_effect_total"] == 0,
            "work_log_method_count": status["work_log_method_count"] == 17 and inspect["workLogBridge"]["method_count"] == 17,
            "work_log_route_persisted": control["state"]["route"] == "work-log" and status["restored_route"] == "work-log",
            "service_process_live": status["service"]["status"] == "READY",
            "single_instance_primary": status["single_instance_primary"] is True,
            "missing_required_methods_zero": status["missing_required_methods"] == [],
            "runtime_remote_network_zero": len(remote_requests) == 0,
            "screenshot_nonempty": screenshot["bytes"] > 10000,
        }
        receipt = {
            "schema_version": "DesktopAssistant-IntegratedStandaloneProductReceipt-v1",
            "run_id": "DESKTOP_ASSISTANT_CROSS_integrated_standalone_successor_20260826_run001",
            "phase": phase,
            "webview_startup": startup_diagnostics,
            "versions": {"python": sys.version, "pywebview": "6.2.1", "webview2": browser_version(window)},
            "bundle": {"path": str(bundle), "bytes": bundle.stat().st_size, "sha256": sha256_file(bundle)},
            "assistant_bundle": {
                "path": str(assistant_bundle),
                "bytes": assistant_bundle.stat().st_size,
                "sha256": sha256_file(assistant_bundle),
            },
            "assistant": {
                "projection": assistant_projection,
                "restore": assistant_restore,
                "placement": assistant_placement,
                "inspect": assistant_inspect,
                "native_window": assistant_native,
                "initial_pointer_activation": assistant_initial_physical_click,
                "native_animation_matrix": assistant_native_animation_matrix,
                "native_after_animation_matrix": assistant_native_after_matrix,
                "native_blink_refresh_preservation": assistant_blink_refresh_preservation,
                "native_auto_blink": assistant_native_blink,
                "after_blink_pointer_activation": assistant_after_blink_physical_click,
                "desktop_transparency": assistant_desktop_transparency,
                "context_menu": assistant_context_menu,
                "after_menu_pointer_activation": assistant_after_menu_physical_click,
                "menu_screenshot": assistant_menu_screenshot,
                "test_move": assistant_test_move,
                "physical_drag": native_pet_drag,
                "physical_drag_desktop_span": native_pet_desktop_span,
                "completion_drag_over_main_stress": native_completion_drag_stress,
                "restore_after_transparency": assistant_restore_after_transparency,
                "hide_before_logo_reopen": assistant_hide,
                "hidden_native_window": assistant_hidden_native,
                "logo_toggle": assistant_logo_toggle,
                "reopened_native_window": assistant_reopened_native,
                "reopened_inspect": assistant_reopened_inspect,
                "pointer_activation": pet_focus,
                "screenshot": assistant_screenshot,
            },
            "ui_regression": ui_regression,
            "packaged_visual_qa": packaged_visual_qa,
            "settings_runtime": directory_runtime_evidence,
            "native_real_pdf": {
                "source": {
                    "path": str(native_pdf_source),
                    "bytes": native_pdf_source.stat().st_size,
                    "sha256": native_pdf_source_sha256,
                },
                "stage": native_pdf_stage,
                "import": native_pdf_import,
                "sample_used": False,
            },
            "native_real_drop": {
                "source": {
                    "path": str(native_drop_source),
                    "bytes": native_drop_source.stat().st_size,
                    "sha256": native_drop_source_sha256,
                },
                "handler": {
                    "attached": drop_runtime.get("attached"),
                    "attempt_count": drop_runtime.get("attempt_count"),
                    "success_count": drop_runtime.get("success_count"),
                    "failure_count": drop_runtime.get("failure_count"),
                    "attach_error": drop_runtime.get("attach_error"),
                    "last_error": drop_runtime.get("last_error"),
                },
                "receipt": native_drop_receipt,
                "detail": native_drop_detail,
                "sample_used": False,
            },
            "deleted_selected_inbox_detail": deleted_selected_detail,
            "inspect": inspect,
            "integrated_route_sweep": route_sweep,
            "integrated_bridge_inspect": bridge_inspect,
            "integrated_bridge_bootstrap_keys": sorted(bridge_bootstraps),
            "runtime_status": status,
            "bootstrap": bootstrap,
            "contract": contract,
            "projection": {"all": all_rows, "branch": branch, "central": central},
            "locators": {"job": job, "evidence": evidence},
            "export": {"preview": preview, "receipt": exported, "verification": verified},
            "faults": faults,
            "restore": restored,
            "effects": effects,
            "remote_requests": remote_requests,
            "loopback_resource_requests": loopback_requests,
            "screenshot": screenshot,
            "checks": checks,
            "acceptance_verdict": "NOT_ASSESSED",
            "status": "PASS" if all(checks.values()) else "FAIL",
        }
        write_json_exclusive(receipt_path, receipt)
        if receipt["status"] != "PASS":
            raise RuntimeError(f"product checks failed: {checks}")
        result.update({
            "status": "PASS",
            "phase": phase,
            "webview_startup": startup_diagnostics,
        })
    except Exception:
        failure_traceback = traceback.format_exc()
        failure = {
            "schema_version": "DesktopAssistant-IntegratedStandaloneProductFailure-v1",
            "status": "ERROR",
            "traceback": failure_traceback,
            "webview_startup": startup_diagnostics,
        }
        diagnostic_log = Path(str(startup_diagnostics.get("log_path", "")))
        if diagnostic_log.is_file():
            failure["webview_startup_log"] = {
                "path": str(diagnostic_log),
                "bytes": diagnostic_log.stat().st_size,
                "sha256": sha256_file(diagnostic_log),
            }
        result.update({
            "status": "ERROR",
            "traceback": failure_traceback,
            "webview_startup": startup_diagnostics,
        })
        if not receipt_path.exists():
            write_json_exclusive(receipt_path, failure)
    finally:
        try:
            assistant_window.destroy()
        except Exception:
            result.setdefault("assistant_destroy_error", traceback.format_exc())
        try:
            window.destroy()
        except Exception:
            result.setdefault("destroy_error", traceback.format_exc())

def main() -> int:
    if "--memo-install-health" in sys.argv:
        root = resource_root()
        configure_resource_imports(root)
        from install_health import main as health_main
        return health_main(root)
    parser = argparse.ArgumentParser()
    parser.add_argument("--desktop-run-root", type=Path)
    parser.add_argument("--desktop-state-dir", type=Path)
    parser.add_argument("--desktop-evidence-dir", type=Path)
    parser.add_argument("--desktop-source-root", type=Path)
    parser.add_argument("--desktop-method-binding", type=Path)
    parser.add_argument("--desktop-test-pdf", type=Path)
    parser.add_argument("--desktop-migrate-private-profile-from")
    parser.add_argument("--desktop-reset-current-private-profile", action="store_true")
    parser.add_argument("--desktop-phase", default="construction-a", choices=["construction-a", "pre-b", "b"])
    parser.add_argument("--desktop-test-mode", action="store_true")
    parser.add_argument("--desktop-secondary-receipt", type=Path)
    parser.add_argument("--memo-automated-acceptance", action="store_true")
    args = parser.parse_args()
    root = resource_root()
    frozen = bool(getattr(sys, "frozen", False))
    configure_resource_imports(root)
    from memorive_test_console_bridge import ConsoleBridge, ConsoleLaunch

    console_launch = ConsoleLaunch.from_environment(Path(sys.executable))
    if console_launch is not None:
        if not frozen:
            raise RuntimeError("CONSOLE_REQUIRES_PACKAGED_EXECUTABLE")
        if (
            args.desktop_run_root is not None
            or args.desktop_state_dir is not None
            or args.desktop_evidence_dir is not None
            or args.desktop_source_root is not None
            or args.desktop_method_binding is not None
            or args.desktop_test_pdf is not None
            or args.desktop_migrate_private_profile_from is not None
            or args.desktop_reset_current_private_profile
            or args.desktop_test_mode
            or args.desktop_secondary_receipt is not None
            or args.memo_automated_acceptance
        ):
            raise RuntimeError("CONSOLE_COMMAND_LINE_OVERRIDE_FORBIDDEN")
    product_profile_root = None
    profile_selection_receipt = None
    if frozen:
        if not args.desktop_test_mode and console_launch is None and BINDING['channel']=='SANDBOX_TEST_ONLY_UNSIGNED':
            private_test_hash=BINDING.get('private_test_capsule_sha256')
            profile_namespace=private_test_hash[:16] if private_test_hash else None
            product_profile_root = sandbox_environment(sys.executable,os.environ,profile_namespace)
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise RuntimeError("LOCALAPPDATA is required for the standalone Memorive profile")
        data_root = (
            console_launch.data_root
            if console_launch is not None
            else (Path(local_app_data) / "Memorive" / "desktop-review").resolve()
        )
        run_root = (args.desktop_run_root or data_root).resolve()
        state_dir = (args.desktop_state_dir or (data_root / "state")).resolve()
        if (
            args.desktop_state_dir is None
            and not args.desktop_test_mode
            and console_launch is None
            and BINDING['channel']=='SANDBOX_TEST_ONLY_UNSIGNED'
        ):
            state_dir,profile_selection_receipt=select_private_test_state(
                data_root,
                BINDING['package_id'],
                migrate_from=args.desktop_migrate_private_profile_from,
                reset_current=args.desktop_reset_current_private_profile,
            )
        evidence_dir = (args.desktop_evidence_dir or (data_root / "evidence")).resolve()
        source_root = (args.desktop_source_root or (root / "source")).resolve()
        method_binding = (args.desktop_method_binding or (source_root / "facade_method_binding.json")).resolve()
        ensure_child(data_root, state_dir, "state_dir")
        ensure_child(data_root, evidence_dir, "evidence_dir")
        ensure_child(root, source_root, "source_root")
        ensure_child(root, method_binding, "method_binding")
        secondary_receipt = args.desktop_secondary_receipt.resolve() if args.desktop_secondary_receipt else None
        if secondary_receipt is not None:
            ensure_child(data_root, secondary_receipt, "secondary_receipt")
    else:
        required = {
            "--desktop-run-root": args.desktop_run_root,
            "--desktop-state-dir": args.desktop_state_dir,
            "--desktop-evidence-dir": args.desktop_evidence_dir,
            "--desktop-source-root": args.desktop_source_root,
            "--desktop-method-binding": args.desktop_method_binding,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            parser.error("source mode requires " + ", ".join(missing))
        run_root = args.desktop_run_root.resolve()
        state_dir = args.desktop_state_dir.resolve()
        evidence_dir = args.desktop_evidence_dir.resolve()
        source_root = args.desktop_source_root.resolve()
        method_binding = args.desktop_method_binding.resolve()
        ensure_child(run_root, state_dir, "state_dir")
        ensure_child(run_root, evidence_dir, "evidence_dir")
        ensure_child(run_root, source_root, "source_root")
        ensure_child(run_root, method_binding, "method_binding")
        secondary_receipt = args.desktop_secondary_receipt.resolve() if args.desktop_secondary_receipt else None
        if secondary_receipt is not None:
            ensure_child(run_root, secondary_receipt, "secondary_receipt")
    if not source_root.is_dir():
        raise NotADirectoryError(source_root)
    if not method_binding.is_file():
        raise FileNotFoundError(method_binding)
    test_pdf = args.desktop_test_pdf.resolve() if args.desktop_test_pdf else None
    if args.desktop_test_mode:
        if test_pdf is None or not test_pdf.is_file():
            parser.error("--desktop-test-mode requires an existing --desktop-test-pdf")
        if not test_pdf.read_bytes()[:5] == b"%PDF-":
            parser.error("--desktop-test-pdf is not a PDF document")
    state_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if console_launch is not None:
        console_home = console_launch.data_root / "home"
        console_home.mkdir(parents=True, exist_ok=True)
        # The console intentionally removes the real USERPROFILE.  Product
        # defaults that use Path.home() must resolve inside this ephemeral
        # session instead of failing or reaching the daily profile.
        os.environ["USERPROFILE"] = str(console_home)
        os.environ["HOME"] = str(console_home)
    bundle = root / "bundle_integrated.html"
    assistant_bundle = root / "bundle_assistant.html"
    if not bundle.is_file() or not assistant_bundle.is_file():
        raise FileNotFoundError(bundle if not bundle.is_file() else assistant_bundle)
    # Serve the immutable local bundles through pywebview's loopback-only
    # resource server. WebView2 NavigateToString has a 2 MiB ceiling; the
    # bundled PDF renderer intentionally makes the integrated document larger.
    bundle_url = str(bundle.resolve())
    assistant_bundle_url = str(assistant_bundle.resolve())
    if args.memo_automated_acceptance:
        if not frozen or console_launch is not None or args.desktop_test_mode:
            raise RuntimeError("AUTOMATED_ACCEPTANCE_REQUIRES_INSTALLED_PRIVATE_BUILD")
        from private_test_bootstrap import apply as prepare_private_test
        private_test_receipt = prepare_private_test(
            state_dir, Path(sys.executable).parent / 'private-test',
            BINDING.get('private_test_capsule_sha256'))
        from automated_acceptance import run as run_automated_acceptance
        receipt = run_automated_acceptance(
            state_dir, bundle, evidence_dir / 'Memorive-automated-acceptance.json',
            private_receipt=private_test_receipt)
        return 0 if receipt.get('status') == 'PASS' else 1
    if os.name == "nt":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Memorive.Desktop")
    webview_diagnostic_handler: logging.Handler | None = None
    console_bridge: ConsoleBridge | None = None
    webview_storage: dict[str, Any] | None = None
    webview_user_data: Path | None = None
    console_verified_paths: dict[str, str] = {}
    startup_diagnostics: dict[str, Any] = {}
    if console_launch is not None:
        webview_storage = webview_storage_selection(state_dir)
        webview_user_data = Path(webview_storage["storage_path"])
        candidate_paths = {
            "run_root": run_root,
            "state_dir": state_dir,
            "evidence_dir": evidence_dir,
            "webview2": webview_user_data,
            "descriptor_parent": console_launch.descriptor_path.parent,
            "localappdata": Path(os.environ["LOCALAPPDATA"]),
            "appdata": Path(os.environ["APPDATA"]),
            "temp": Path(os.environ["TEMP"]),
            "tmp": Path(os.environ["TMP"]),
            "home": console_home,
        }
        if hasattr(sys, "_MEIPASS"):
            executable_parent = Path(sys.executable).resolve().parent
            # onefile extracts a writable _MEI tree and must prove it is
            # inside DATA_ROOT.  onedir uses the immutable sibling _internal
            # tree; it is installation input, not a session write location.
            if root != executable_parent and executable_parent not in root.parents:
                candidate_paths["pyinstaller_extraction"] = root
        console_verified_paths = console_launch.verify_write_paths(candidate_paths)
        webview_diagnostic_handler, webview_log_path = install_webview_diagnostics(
            evidence_dir, webview_storage
        )
        startup_diagnostics = {
            "storage": webview_storage,
            "log_path": str(webview_log_path),
        }
        console_bridge = ConsoleBridge(
            console_launch,
            system_effects_locked=True,
            package_id="desktop-console",
            verified_write_paths=console_verified_paths,
        )
        console_bridge.start()
        log_webview_startup_event("console_bridge_ready")
    # pywebview initializes the Windows GUI stack during import.  Keep that
    # work behind the console handshake so a safe build can report its
    # INITIALIZING state within the console's fixed launch deadline.
    global webview
    import webview
    # pywebview 4.x does not expose the module-level ``settings`` mapping
    # introduced by newer releases.  Support both packaged dependency APIs.
    webview_settings = getattr(webview, "settings", None)
    if isinstance(webview_settings, dict):
        webview_settings["ALLOW_DOWNLOADS"] = False
        webview_settings["ALLOW_FILE_URLS"] = False
        webview_settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
        webview_settings["REMOTE_DEBUGGING_PORT"] = None
        webview_settings["IGNORE_SSL_ERRORS"] = False
    # Runtime imports fan out across every product controller.  In console
    # mode the authenticated, fail-closed bridge must become reachable first
    # so the independent console can observe INITIALIZING instead of timing
    # out while Python imports the full product graph.
    private_test_receipt = None
    if frozen and not args.desktop_test_mode and console_launch is None:
        from private_test_bootstrap import apply as prepare_private_test
        private_test_receipt = prepare_private_test(state_dir, Path(sys.executable).parent / 'private-test',
            BINDING.get('private_test_capsule_sha256'))
    from runtime import ProductApi
    try:
        session_source_roots = None
        if console_launch is not None:
            local_session_sources = console_launch.data_root / "session-sources"
            session_source_roots = {
                "codex_home": local_session_sources / "codex",
                "claude_home": local_session_sources / "claude",
            }
            for session_source in session_source_roots.values():
                session_source.mkdir(parents=True, exist_ok=True)
        elif args.desktop_test_mode:
            local_session_sources = source_root.parent / "fixtures_analysis" / "local_sources"
            session_source_roots = {
                "codex_home": local_session_sources / "codex",
                "claude_home": local_session_sources / "claude",
            }
        elif frozen and BINDING['channel']=='SANDBOX_TEST_ONLY_UNSIGNED':
            session_mode=str(os.environ.get('MEMORIVE_SESSION_SOURCE_MODE') or 'REAL_LOCAL').strip().upper()
            if session_mode=='ISOLATED_SANDBOX':
                if product_profile_root is None:
                    raise RuntimeError('PRODUCT_PROFILE_BINDING_REQUIRED')
                local_session_sources=product_profile_root/'session_sources'
                session_source_roots={'codex_home':local_session_sources/'codex','claude_home':local_session_sources/'claude'}
                for session_source in session_source_roots.values():session_source.mkdir(parents=True,exist_ok=True)
            elif session_mode!='REAL_LOCAL':
                raise RuntimeError('SESSION_SOURCE_MODE_INVALID')
        api = ProductApi(
            state_dir,
            source_root,
            method_binding,
            system_effects_enabled=bool(
                frozen and not args.desktop_test_mode and console_launch is None
            ),
            session_source_roots=session_source_roots,
            session_visible_ui_reader={} if console_launch is not None else None,
            test_fixture_mode=args.desktop_test_mode,
            private_profile_selection=profile_selection_receipt,
        )
        if private_test_receipt:
            from private_test_bootstrap import verify_models
            verify_models(api, state_dir, private_test_receipt)
        if console_bridge is not None:
            console_bridge.bind_product_api(api)
            api._console_bridge = console_bridge
            log_webview_startup_event("console_product_runtime_ready")
    except RuntimeError as error:
        if console_launch is not None:
            if console_bridge is not None:
                console_bridge.close()
            if webview_diagnostic_handler is not None:
                remove_webview_diagnostics(webview_diagnostic_handler)
            raise
        if str(error) != "DESKTOP_INSTANCE_ALREADY_ACTIVE":
            raise
        receipt = activate_existing_product_window(
            Path(sys.executable),
            instance_descriptor=(
                state_dir / "profile" / "ui" / "desktop.instance.json"
            ),
        )
        if secondary_receipt is not None:
            write_json_exclusive(secondary_receipt, receipt)
        return 0 if receipt["status"] in {"PASS", "PARTIAL"} else 2
    except Exception:
        if console_bridge is not None:
            console_bridge.close()
        if webview_diagnostic_handler is not None:
            remove_webview_diagnostics(webview_diagnostic_handler)
        raise
    try:
        if webview_storage is None or webview_user_data is None:
            webview_storage = webview_storage_selection(state_dir)
            webview_user_data = Path(webview_storage["storage_path"])
        if webview_diagnostic_handler is None:
            webview_diagnostic_handler, webview_log_path = install_webview_diagnostics(
                evidence_dir, webview_storage
            )
            startup_diagnostics = {
                "storage": webview_storage,
                "log_path": str(webview_log_path),
            }
        log_webview_startup_event(
            "window_creation_begin",
            test_mode=args.desktop_test_mode,
            phase=args.desktop_phase,
        )
        window = webview.create_window(
            BRAND, url=bundle_url, js_api=api, width=1440, height=900, resizable=True,
            hidden=False, focus=True, on_top=bool(args.desktop_test_mode), text_select=True,
            zoomable=False, background_color="#F7F5F1",
        )
        if window is None:
            raise RuntimeError("pywebview window creation cancelled")
        api.attach_window(window)
        from window_chrome import MainWindowChrome
        api._main_chrome = MainWindowChrome(window, api)
        window.events.before_show += api._main_chrome.install
        assistant_window = webview.create_window(
            BRAND+" 桌面助手",
            url=assistant_bundle_url,
            js_api=api,
            width=90,
            height=90,
            # pywebview treats initial x/y as logical coordinates and scales
            # them using whichever monitor creates the HWND. Canonical pet
            # positions are physical virtual-desktop coordinates, so create
            # hidden at a neutral origin and apply the exact native bounds
            # after the handle is ready.
            x=0,
            y=0,
            resizable=False,
            min_size=(90, 90),
            hidden=True,
            frameless=True,
            easy_drag=False,
            shadow=False,
            focus=True,
            on_top=True,
            confirm_close=False,
            transparent=True,
            text_select=False,
            zoomable=False,
            background_color="#000000",
        )
        if assistant_window is None:
            raise RuntimeError("assistant window creation cancelled")
        api.attach_assistant_window(assistant_window)
        drop_runtime: dict[str, Any] = {
            "ready": threading.Event(),
            "lock": threading.Lock(),
            "attached": False,
            "attach_error": None,
            "attempt_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "last_receipt": None,
            "last_error": None,
            "element": None,
            "callback": None,
        }
        remote_requests: list[dict[str, str]] = []
        loopback_requests: list[dict[str, str]] = []
        def on_request(request) -> None:
            parsed = urllib.parse.urlparse(request.url)
            scheme = parsed.scheme.lower()
            if scheme in {"http", "https", "ws", "wss"}:
                record = {"url": request.url, "method": request.method}
                if (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}:
                    loopback_requests.append(record)
                else:
                    remote_requests.append(record)
        window.events.request_sent += on_request
        assistant_window.events.request_sent += on_request
        def on_product_loaded() -> None:
            log_webview_startup_event("product_window_loaded")
            attach_inbox_drop_handler(window, api, drop_runtime)
            log_webview_startup_event("product_inbox_drop_handler_attached")
            configure_main_window_behavior(
                window,
                api,
                attach_close_handler=not args.desktop_test_mode,
            )
            log_webview_startup_event("product_native_behavior_configured")
        def on_assistant_loaded() -> None:
            log_webview_startup_event("assistant_window_loaded")
            configure_assistant_native(
                assistant_window, api, assistant_pet_sheet_path(root),
                reveal=False,
                expression_assets=assistant_expression_asset_paths(root),
            )
            log_webview_startup_event("assistant_native_configured")
            if console_bridge is not None:
                from console_expressions import ConsoleExpressionRenderer
                console_bridge._expressions.bind(ConsoleExpressionRenderer(
                    window, assistant_window, api, _ASSISTANT_NATIVE_OVERLAYS))
            initial_native_placement = api.assistant_initial_placement()
            api.assistant_move_window({
                "x": int(initial_native_placement["x"]),
                "y": int(initial_native_placement["y"]),
                "persist": False,
            })
            log_webview_startup_event("assistant_native_placed")
            try:
                api.attach_assistant_presenter(
                    lambda presentation: apply_assistant_native_presentation(
                        assistant_window, presentation
                    )
                )
            except RuntimeError as error:
                if str(error) != "ASSISTANT_PRESENTER_ALREADY_ATTACHED":
                    raise
            log_webview_startup_event("assistant_native_presenter_attached")
            # The assistant document can issue its first bootstrap before the
            # pywebview ``loaded`` callback attaches the native presenter.  A
            # native-side refresh here closes that race and guarantees that a
            # durable terminal projection reaches the WinForms animation
            # layer at least once after the overlay is ready.
            def bootstrap_native_presenter() -> None:
                try:
                    log_webview_startup_event(
                        "assistant_native_presenter_bootstrap_begin"
                    )
                    presenter_projection = api.assistant_bootstrap({})
                    log_webview_startup_event(
                        "assistant_native_presenter_bootstrap",
                        status=presenter_projection.get("status"),
                        native_presentation_status=presenter_projection.get(
                            "native_presentation_status"
                        ),
                        reason_code=(
                            presenter_projection.get("presentation", {}).get(
                                "reason_code"
                            )
                        ),
                    )
                except Exception as error:
                    log_webview_startup_event(
                        "assistant_native_presenter_bootstrap_failed",
                        error_type=type(error).__name__,
                    )

            if not args.desktop_test_mode:
                assistant_preferences = api.call("assistant.preference_get", {})
                log_webview_startup_event("assistant_preferences_loaded")
                if assistant_preferences.get("preferences", {}).get("enabled", True):
                    assistant_window.show()
                else:
                    assistant_window.hide()
                log_webview_startup_event("assistant_visibility_applied")
            # Delay the worker until this loaded callback has released both
            # the UI thread and any ProductApi lock used by preference reads.
            # Starting it earlier creates a lock-order cycle between the
            # worker's WinForms Invoke and this callback's API call.
            presenter_timer = threading.Timer(0.25, bootstrap_native_presenter)
            presenter_timer.name = "memorive-assistant-presenter-bootstrap"
            presenter_timer.daemon = True
            presenter_timer.start()
            if not args.desktop_test_mode:
                # Schedule the presenter before restoring main-window focus.
                # Some WebView2/WinForms combinations do not return cleanly
                # from the synchronous activation call inside a loaded
                # callback.  That must not suppress terminal-event delivery.
                try:
                    bring_product_window_to_front(window)
                except Exception as error:
                    log_webview_startup_event(
                        "product_window_focus_restore_failed",
                        error_type=type(error).__name__,
                    )
        window.events.loaded += on_product_loaded
        assistant_window.events.loaded += on_assistant_loaded
        result: dict[str, Any] = {}
        if args.desktop_test_mode:
            log_webview_startup_event("webview_start_enter", test_mode=True)
            webview.start(
                func=run_test,
                args=(window, assistant_window, api, evidence_dir, bundle, assistant_bundle,
                      test_pdf, args.desktop_phase, result, remote_requests, loopback_requests,
                      drop_runtime, startup_diagnostics),
                gui="edgechromium", debug=False, http_server=False, private_mode=True,
                storage_path=str(webview_user_data),
            )
            log_webview_startup_event(
                "webview_start_return",
                test_mode=True,
                status=result.get("status"),
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result.get("status") == "PASS" else 1
        log_webview_startup_event("webview_start_enter", test_mode=False)
        webview.start(gui="edgechromium", debug=False, http_server=False, private_mode=True,
                      storage_path=str(webview_user_data))
        log_webview_startup_event("webview_start_return", test_mode=False)
        return 0
    except Exception as error:
        if webview_diagnostic_handler is not None:
            logging.getLogger("memorive.webview_startup").exception(
                "desktop_startup_exception type=%s message=%s",
                type(error).__name__,
                str(error),
            )
        raise
    finally:
        try:
            if console_bridge is not None:
                console_bridge.close()
        finally:
            try:
                api.close()
            finally:
                if webview_diagnostic_handler is not None:
                    remove_webview_diagnostics(webview_diagnostic_handler)


if __name__ == "__main__":
    if '--memo-native-pdf' in sys.argv:
        root = resource_root()
        configure_resource_imports(root)
        if not getattr(sys, 'frozen', False):
            configure_resource_imports(root.parents[1])
        from document_processing.engines.isolated_pdf import main as native_pdf_main
        raise SystemExit(native_pdf_main([v for v in sys.argv[1:] if v != '--memo-native-pdf']))
    if '--memo-agent' in sys.argv or ('--workspace' in sys.argv and any(v in sys.argv for v in ('--mcp','--call','--connector-call'))):
        root=resource_root();configure_resource_imports(root)
        if not getattr(sys,'frozen',False):configure_resource_imports(root.parents[1])
        from integrated_agent import main as agent_main
        raise SystemExit(agent_main())
    if SERVICE_MODE_ARG in sys.argv:
        raise SystemExit(run_embedded_service())
    raise SystemExit(main())
