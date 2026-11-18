"""A notice shown instead of the main window when the app cannot start.

It is the dialog the main page shows when the window is closed: the same
markup and classes, in a small window with the app's background. Everything
that decides how it looks is read from bundle_integrated.html when the notice
is built: the style sheets, the close glyph, and the two functions that give
every element its typeface and size (typeRole and applyFontScale). The notice
therefore looks like the app's own dialogs, follows the user's language and
font size, and nothing is kept in two places.

At present there is one reason to show it: the settings file was written by a
newer version of the app (see memorive_settings.store.SettingsFromNewerVersion).
"""
from __future__ import annotations

import html
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Mapping

LANGUAGES = ("zh-CN", "en-US", "ja-JP")
SETTINGS_FROM_NEWER_VERSION = {
    "zh-CN": {
        "title": "无法读取设置",
        "notice": "设置文件由更新版本的 Memorive 保存，当前版本无法读取。文件没有被改动。请安装更新的版本后再打开。",
        "folder": "打开所在文件夹",
        "exit": "退出",
    },
    "en-US": {
        "title": "Settings cannot be read",
        "notice": "The settings file was saved by a newer version of Memorive, and this version cannot read it. "
                  "The file has not been changed. Install the newer version, then open Memorive again.",
        "folder": "Open folder",
        "exit": "Exit",
    },
    "ja-JP": {
        "title": "設定を読み込めません",
        "notice": "設定ファイルは新しいバージョンの Memorive で保存されており、このバージョンでは読み込めません。"
                  "ファイルは変更していません。新しいバージョンをインストールしてから、もう一度開いてください。",
        "folder": "フォルダーを開く",
        "exit": "終了",
    },
}
_STYLE_BLOCK = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.S | re.I)
_CLOSE_GLYPH = re.compile(r"glyphs=\{[^}]*\bclose:'([^']+)'")
_BODY_TAG = re.compile(r"<body\b[^>]*>", re.I)
# The page's typography: which role an element has, and the size and typeface that follow from it.
# applyFontScale calls setFontFactor, so all three come along; the notice runs applyFontScale before
# it opens the dialog.
_TYPOGRAPHY = ("const typeRole = (element) => {", "const setFontFactor = (percent) => {",
               "const applyFontScale = (percent, rootNode = document.body) => {")
_SCRIPT = """
const dialog = document.getElementById('desktop-close-dialog');
function call(name, ...values) {
  const run = () => window.pywebview.api[name](...values);
  if (window.pywebview && window.pywebview.api) run();
  else window.addEventListener('pywebviewready', run, {once: true});
}
for (const button of dialog.querySelectorAll('button')) {
  button.addEventListener('click', () => call(button.dataset.windowAction === 'folder' ? 'open_folder' : 'exit'));
}
dialog.addEventListener('cancel', (event) => { event.preventDefault(); call('exit'); });
// When the window appears, the host hands the page focus as if Tab had been pressed, which would draw the
// focus outline on the first button. Buttons keep focus only once the user has used the keyboard, so the
// dialog looks as it does in the main window after a click. Escape leaves either way.
let keyboard = false;
window.addEventListener('keydown', () => { keyboard = true; }, true);
dialog.addEventListener('focusin', (event) => { if (!keyboard && event.target !== dialog) event.target.blur(); });
dialog.showModal();
// The window starts hidden; it is shown once it has the size of the dialog.
const box = dialog.getBoundingClientRect();
call('fit', Math.ceil(box.width), Math.ceil(box.height));
"""


def saved_preferences(settings_file: Path) -> tuple[str, int]:
    """Interface language and font size (percent) recorded in the settings file; zh-CN and 100 when unreadable."""
    try:
        preferences = json.loads(Path(settings_file).read_bytes().decode("utf-8"))["settings"]["preferences"]
        language, percent = preferences.get("language"), preferences.get("font_scale_percent")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return LANGUAGES[0], 100
    if language not in LANGUAGES:
        language = LANGUAGES[0]
    if isinstance(percent, bool) or not isinstance(percent, int) or not 80 <= percent <= 200:
        percent = 100
    return language, percent


def _function_source(page: str, head: str) -> str:
    """The text of the function in page that starts with head, through its closing brace."""
    start = page.find(head)
    if start == -1 or page.find(head, start + 1) != -1:
        raise ValueError("STARTUP_NOTICE_PAGE_PARTS_MISSING")
    depth, quote, position = 0, "", start + len(head) - 1
    while position < len(page):
        char = page[position]
        if quote:
            if char == "\\":
                position += 1
            elif char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return page[start:position + 1] + ";"
        position += 1
    raise ValueError("STARTUP_NOTICE_PAGE_PARTS_MISSING")


def notice_page(bundle: Path, language: str, text: Mapping[str, str], font_scale_percent: int = 100) -> str:
    """The notice as a page: the app's style sheets and typography, and the close dialog's markup."""
    page = Path(bundle).read_text(encoding="utf-8")
    styles = _STYLE_BLOCK.findall(page)
    glyph = _CLOSE_GLYPH.search(page)
    body = _BODY_TAG.search(page)
    if not styles or glyph is None or body is None or ".desktop-close-dialog" not in "".join(styles):
        raise ValueError("STARTUP_NOTICE_PAGE_PARTS_MISSING")
    typography = "\n".join(_function_source(page, head) for head in _TYPOGRAPHY)
    e = lambda key: html.escape(text[key], quote=True)
    setup = (
        "const typedElements = new WeakSet();\n" + typography + "\n"
        f"document.body.dataset.desktopLanguage = {json.dumps(language)};\n"
        f"applyFontScale({int(font_scale_percent)});\n"
    )
    return (
        f'<!doctype html>\n<html lang="{html.escape(language, quote=True)}">\n<head>\n<meta charset="utf-8">\n'
        f"<title>Memorive</title>\n" + "\n".join(styles) + f"\n</head>\n{body.group(0)}\n"
        '<dialog class="desktop-close-dialog" id="desktop-close-dialog" data-desktop-no-translate="true"'
        ' aria-labelledby="desktop-close-title" aria-describedby="desktop-close-notice">'
        f'<header class="desktop-close-header"><h2 id="desktop-close-title">{e("title")}</h2>'
        f'<button type="button" class="desktop-close-x" data-window-action="x" aria-label="{e("exit")}" title="{e("exit")}">'
        f'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="{html.escape(glyph.group(1), quote=True)}"></path></svg>'
        "</button></header>"
        f'<p class="desktop-close-notice" id="desktop-close-notice" role="status">{e("notice")}</p>'
        '<footer class="desktop-close-actions">'
        f'<button type="button" class="desktop-close-button" data-window-action="folder">{e("folder")}</button>'
        '<div class="desktop-close-choices">'
        f'<button type="button" class="desktop-close-button desktop-close-primary" data-window-action="exit">{e("exit")}</button>'
        "</div></footer></dialog>\n"
        f"<script>(() => {{\n{setup}{_SCRIPT}}})();</script>\n</body>\n</html>\n"
    )


# Space around the dialog inside the window, so that the dialog's shadow is not cut off.
_MARGIN = 56


def _center(form: Any) -> None:
    """Put the window in the middle of the screen it is on."""
    from System import Action
    from System.Drawing import Point
    from System.Windows.Forms import Screen

    def place() -> None:
        area = Screen.FromControl(form).WorkingArea
        form.Location = Point(area.Left + (area.Width - form.Width) // 2, area.Top + (area.Height - form.Height) // 2)

    form.Invoke(Action(place))


class _NoticeApi:
    """What the page asks for: the window's size, and what the two buttons do."""

    def __init__(self, folder: Path):
        self._folder = Path(folder)
        self._window: Any = None
        self._shown = False

    def fit(self, width: int, height: int) -> None:
        """Give the window the size of the dialog plus the margin, centre it and show it."""
        try:
            self._window.resize(int(width) + 2 * _MARGIN, int(height) + 2 * _MARGIN)
            _center(self._window.native)
        finally:
            self._shown = True
            self._window.show()

    def show_anyway(self) -> None:
        """Show the window if the page has not reported its size in time."""
        deadline = time.monotonic() + 5
        while not self._shown and time.monotonic() < deadline:
            time.sleep(0.1)
        if not self._shown:
            self._shown = True
            self._window.show()

    def open_folder(self) -> None:
        os.startfile(str(self._folder))

    def exit(self) -> None:
        if self._window is not None:
            self._window.destroy()


def _native_message(title: str, message: str) -> None:
    """Last resort when the styled notice cannot be shown: the reason must still reach the user."""
    from System.Windows.Forms import MessageBox, MessageBoxButtons, MessageBoxIcon

    MessageBox.Show(message, title, MessageBoxButtons.OK, MessageBoxIcon.Warning)


def show_settings_from_newer_version(webview: Any, *, bundle: Path, settings_file: Path, page_file: Path,
                                     storage_path: Path | None, brand: str = "Memorive") -> int:
    """Tell the user that the settings file belongs to a newer version; returns the process exit code."""
    language, percent = saved_preferences(settings_file)
    text = SETTINGS_FROM_NEWER_VERSION[language]
    try:
        page_file.parent.mkdir(parents=True, exist_ok=True)
        page_file.write_text(notice_page(bundle, language, text, percent), encoding="utf-8")
        api = _NoticeApi(Path(settings_file).parent)
        window = webview.create_window(
            # Hidden and roomy at first, so that the dialog takes its natural size; fit() then shrinks the window.
            brand, url=str(page_file.resolve()), js_api=api, width=1280, height=960, resizable=False,
            frameless=True, easy_drag=True, shadow=True, focus=True, text_select=False, zoomable=False,
            background_color="#F7F5F1", hidden=True,
        )
        if window is None:
            raise RuntimeError("pywebview window creation cancelled")
        api._window = window
        webview.start(api.show_anyway, gui="edgechromium", debug=False, http_server=False, private_mode=True,
                      storage_path=str(storage_path) if storage_path else None)
    except Exception:
        _native_message(text["title"], text["notice"])
    return 3
