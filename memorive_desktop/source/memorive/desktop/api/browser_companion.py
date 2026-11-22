"""The browser companion's window calls: BrowserCompanionCallsMixin.
"""
from __future__ import annotations

from typing import Any


class BrowserCompanionCallsMixin:
    """The browser companion's window calls, forwarded to BrowserCompanionDesktop."""

    # The browser extension and its recordings: the window calls these; memorive/sessions/companion/desktop.py
    # (BrowserCompanionDesktop) does the work.
    def open_browser_companion_folder(self):
        return self._browser_companion.open_browser_companion_folder()

    def browser_companion_status(self) -> dict[str, Any]:
        return self._browser_companion.browser_companion_status()

    def install_browser_companion(self, preferred_browser: str | None = None) -> dict[str, Any]:
        return self._browser_companion.install_browser_companion(preferred_browser)

    def confirm_browser_companion_setup(self) -> dict[str, Any]:
        return self._browser_companion.confirm_browser_companion_setup()

    def sync_browser_sessions_from_downloads(self) -> dict[str, Any]:
        return self._browser_companion.sync_browser_sessions_from_downloads()

    def import_browser_sessions_file(self) -> dict[str, Any]:
        return self._browser_companion.import_browser_sessions_file()
