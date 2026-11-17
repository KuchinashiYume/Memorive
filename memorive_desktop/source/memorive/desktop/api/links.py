"""The project page, the developer SDK folder, local help and locators: LinksMixin.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import webbrowser


class LinksMixin:
    """Opening the project page, the developer SDK folder and local help; resolving locators."""

    def open_project_page(self) -> dict[str, Any]:
        from memorive.desktop.release_links import PROJECT_URL
        opened = webbrowser.open(PROJECT_URL, new=2)
        return {
            "schema_version": "DesktopAssistantProjectPageOpenReceipt-v1",
            "opened": bool(opened),
            "url": PROJECT_URL,
            "status": "OPENED" if opened else "BLOCKED",
        }

    def resolve_locator(self, locator: str) -> dict[str, Any]:
        if not isinstance(locator, str):
            raise ValueError("locator must be a string")
        return self._service.call("service.resolve_locator", {"locator": locator})

    def open_developer_sdk(self) -> dict[str, Any]:
        import sys,os
        root=(Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[4])/'integrations/developer'
        if not (root/'api-v1.schema.json').is_file():
            raise ValueError('DEVELOPER_SDK_MISSING')
        os.startfile(str(root))
        return {'status':'OPENED','api_version':'1.0.0'}

    def open_local_help(self, kind: str, language: str = 'zh-CN') -> dict[str, Any]:
        from offline_help import open_help_asset
        return open_help_asset(kind, language)
