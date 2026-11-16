from __future__ import annotations

import json
from pathlib import Path
import sys

ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
for candidate in (ATTEMPT_ROOT / "source", ATTEMPT_ROOT / "product" / "desktop"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from memorive.desktop.api import ProductApi  # noqa: E402
from browser_companion_support import companion  # noqa: E402


def _api(tmp_path: Path) -> ProductApi:
    api = companion()
    api.source_root = ATTEMPT_ROOT / "source"
    api.profile_root = tmp_path / "profile"
    api.system_effects_enabled = True
    return api


def test_prepared_extension_refresh_never_claims_loaded_code_is_current(
    tmp_path: Path,
) -> None:
    api = _api(tmp_path)
    state_path = api._browser_companion_state_path()
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": "DesktopBrowserCompanionSetupState-v1",
                "prepared": True,
                "package_sha256": "PREVIOUS-PACKAGE",
            }
        ),
        encoding="utf-8",
    )

    receipt = api.refresh_prepared_package()
    updated_state = json.loads(state_path.read_text(encoding="utf-8"))

    assert receipt["status"] == "UPDATED"
    assert receipt["browser_reload_required"] is True
    assert updated_state["browser_reload_required"] is True


