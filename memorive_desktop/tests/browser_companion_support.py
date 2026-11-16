"""A BrowserCompanionDesktop for tests, without starting the rest of the app.

The tests set the fields they need afterwards (profile_root, source_root, system_effects_enabled,
sessions, window) and replace internal methods on the object. profile_path and
effective_directory call ProductApi's own helpers, so folders resolve as in the app.
"""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
for candidate in (ATTEMPT_ROOT / "source", ATTEMPT_ROOT / "product" / "desktop"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from memorive_browser_companion.desktop import BrowserCompanionDesktop  # noqa: E402
from runtime import ProductApi  # noqa: E402


def companion(service=None) -> BrowserCompanionDesktop:
    shim = SimpleNamespace(_service=service)

    def profile_path(name: str) -> Path:
        shim._profile_root, shim._source_root = result.profile_root, result.source_root
        return ProductApi._profile_path(shim, name)

    def effective_directory(role: str) -> Path:
        shim._profile_root, shim._source_root = result.profile_root, result.source_root
        shim._profile_path = profile_path
        shim._is_reparse_point = ProductApi._is_reparse_point
        return ProductApi._effective_directory(shim, role)

    result = BrowserCompanionDesktop(
        profile_root=None, source_root=None, sessions=None, profile_path=profile_path,
        write_json_file=ProductApi._write_json_file, effective_directory=effective_directory,
        system_effects_enabled=False,
    )
    return result
