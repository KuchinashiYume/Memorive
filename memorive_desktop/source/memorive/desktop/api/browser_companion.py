"""The browser companion's window calls: BrowserCompanionCallsMixin.
"""
from __future__ import annotations

from typing import Any


class BrowserCompanionCallsMixin:
    """The browser companion's window calls, forwarded to BrowserCompanionDesktop."""

    # The browser companion and browser sessions: the window calls these; memorive/sessions/companion/desktop.py
    # (BrowserCompanionDesktop) does the work.
    def browser_companion_sites(self) -> dict[str, Any]:
        return self._browser_companion.browser_companion_sites()

    def save_browser_companion_sites(self, sites, expected_revision):
        return self._browser_companion.save_browser_companion_sites(sites, expected_revision)

    def open_browser_companion_site(self, url, preferred_browser=None):
        return self._browser_companion.open_browser_companion_site(url, preferred_browser)

    def open_browser_companion_folder(self):
        return self._browser_companion.open_browser_companion_folder()

    def browser_companion_status(self) -> dict[str, Any]:
        return self._browser_companion.browser_companion_status()

    def install_browser_companion(self, preferred_browser: str | None = None) -> dict[str, Any]:
        return self._browser_companion.install_browser_companion(preferred_browser)

    def confirm_browser_companion_setup(self) -> dict[str, Any]:
        return self._browser_companion.confirm_browser_companion_setup()

    def reload_browser_companion(self) -> dict[str, Any]:
        return self._browser_companion.reload_browser_companion()

    def run_browser_companion_reload(self) -> dict[str, Any]:
        return self._browser_companion.run_browser_companion_reload()

    def run_browser_companion_test_read(self) -> dict[str, Any]:
        return self._browser_companion.run_browser_companion_test_read()

    def run_browser_companion_sync(self, requested_mode=None) -> dict[str, Any]:
        return self._browser_companion.run_browser_companion_sync(requested_mode)

    def run_browser_companion_full_sync(self) -> dict[str, Any]:
        return self._browser_companion.run_browser_companion_full_sync()

    def restart_browser_companion(self) -> dict[str, Any]:
        return self._browser_companion.restart_browser_companion()

    def sync_browser_sessions_from_downloads(self) -> dict[str, Any]:
        return self._browser_companion.sync_browser_sessions_from_downloads()

    def import_browser_sessions_file(self) -> dict[str, Any]:
        return self._browser_companion.import_browser_sessions_file()
