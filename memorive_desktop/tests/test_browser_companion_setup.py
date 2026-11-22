"""The browser extension's setup on the desktop side (memorive/sessions/companion/desktop.py): preparing the
extension folder, telling from the extension's identity receipt whether the browser has loaded it, and the files of
the earlier site sync, which still import."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

import pytest

from browser_companion_support import companion
from memorive.sessions.archive import ArchiveInputError, BrowserLibraryImporter
from memorive.sessions.companion.site_profiles import default_sites, normalize_sites, profile_hash, saved_site_settings

ROOT = Path(__file__).resolve().parents[1]
VERSION = json.loads(
    (ROOT / "source/memorive/sessions/companion/extension/manifest.json").read_text(encoding="utf-8")
)["version"]
EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop"


def _api(tmp_path: Path, *, prepared: bool = True, **state: object):
    api = companion()
    api.source_root = ROOT / "source"
    api.profile_root = tmp_path / "profile"
    api.system_effects_enabled = False
    downloads = tmp_path / "Downloads"
    (downloads / "Memorive会话桥接").mkdir(parents=True)
    api._browser_session_download_roots = lambda: [downloads]
    if prepared:
        _source, _required, package_sha256 = api._browser_companion_package()
        path = api._browser_companion_state_path()
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "schema_version": "DesktopBrowserCompanionSetupState-v1",
            "prepared": True,
            "prepared_at_unix": int(time.time()) - 5,
            "package_sha256": package_sha256,
            "preferred_browser": "EDGE",
            "extension_id": EXTENSION_ID,
            **state,
        }), encoding="utf-8")
    return api, downloads


def _receipt(downloads: Path, **fields: object) -> Path:
    """The file the extension saves when its service worker starts with a new version (background.js)."""
    payload = {
        "schema_version": "MEMORIVE_BROWSER_COMPANION_IDENTITY_V1",
        "extension_id": EXTENSION_ID,
        "extension_version": VERSION,
        "browser_family": "EDGE",
        "observed_at": "2026-11-22T08:00:00Z",
        "lifecycle_reason": "SERVICE_WORKER_VERSION_START",
        **fields,
    }
    path = downloads / "Memorive会话桥接" / f"memorive-browser-companion-identity-{VERSION}-EDGE-{EXTENSION_ID}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_the_receipt_of_the_prepared_version_connects_the_extension(tmp_path: Path) -> None:
    api, downloads = _api(tmp_path)
    assert api.browser_companion_status()["bridge_identity_verified"] is False

    _receipt(downloads)
    status = api.browser_companion_status()

    assert status["bridge_identity_verified"] is True and status["configured"] is True
    assert status["connected_browser"] == "EDGE" and status["extension_id"] == EXTENSION_ID
    state = json.loads(api._browser_companion_state_path().read_text(encoding="utf-8"))
    assert state["extension_connected"] is True and state["browser_reload_required"] is False


def test_a_receipt_with_the_fields_of_the_site_sync_still_counts(tmp_path: Path) -> None:
    # Builds from before the sync was removed also wrote its limits and sites; Memorive no longer reads them.
    api, downloads = _api(tmp_path)
    _receipt(downloads, trigger_protocol="HTTPS_APPROVED_FRAGMENT_V3", preflight_limit=2,
             providers=["deepseek", "gemini", "kimi"], site_profile_sha256="0" * 64)

    assert api.browser_companion_status()["bridge_identity_verified"] is True


@pytest.mark.parametrize(("field", "value"), [
    ("extension_version", "3.12.0"),
    ("extension_id", "ponmlkjihgfedcbaponmlkjihgfedcba"),
    ("extension_id", "not-an-extension-id"),
    ("browser_family", "CHROME"),
    ("browser_family", "SAFARI"),
    ("schema_version", "MEMORIVE_BROWSER_COMPANION_IDENTITY_V0"),
    ("observed_at", ""),
])
def test_a_receipt_of_another_extension_does_not_connect(tmp_path: Path, field: str, value: object) -> None:
    api, downloads = _api(tmp_path)
    _receipt(downloads, **{field: value})

    assert api.browser_companion_status()["bridge_identity_verified"] is False


def test_a_receipt_from_before_the_extension_was_prepared_does_not_connect(tmp_path: Path) -> None:
    api, downloads = _api(tmp_path)
    earlier = time.time() - 60
    os.utime(_receipt(downloads), (earlier, earlier))

    assert api.browser_companion_status()["bridge_identity_verified"] is False


def test_the_first_connect_on_a_new_profile_asks_to_load_the_folder(tmp_path: Path) -> None:
    # A new install has no browser companion folder in the profile yet; the first "Connect browser" makes it.
    api, _downloads = _api(tmp_path, prepared=False)
    state_path = api._browser_companion_state_path()
    assert not state_path.parent.exists()

    receipt = api.install_browser_companion("EDGE")

    assert receipt["status"] == "AWAITING_EXTENSION_LOAD" and receipt["manual_load_required"] is True
    assert (Path(receipt["extension_path"]) / "manifest.json").is_file()
    assert json.loads(state_path.read_text(encoding="utf-8"))["preferred_browser"] == "EDGE"


def test_an_update_of_a_loaded_extension_waits_for_its_reload(tmp_path: Path) -> None:
    # Loaded before (the browser gave it an id) and the package changed: the folder is updated in place and the
    # user reloads the extension on the browser's extensions page.
    api, downloads = _api(tmp_path, package_sha256="EARLIER-PACKAGE")

    receipt = api.install_browser_companion("EDGE")

    assert receipt["status"] == "AWAITING_EXTENSION_RELOAD" and receipt["manual_load_required"] is False
    state = json.loads(api._browser_companion_state_path().read_text(encoding="utf-8"))
    assert state["browser_reload_required"] is True and state["extension_id"] == EXTENSION_ID
    _receipt(downloads)
    assert api.browser_companion_status()["bridge_identity_verified"] is True


def test_an_unchanged_loaded_extension_is_already_configured(tmp_path: Path) -> None:
    api, downloads = _api(tmp_path)
    api.install_browser_companion("EDGE")
    _receipt(downloads)

    assert api.install_browser_companion("EDGE")["status"] == "ALREADY_CONFIGURED"


def _custom_site_bundle(sites: list[dict], *, site_profile_sha256: str) -> dict:
    """A file the earlier site sync wrote for a site the user added (provider web-...)."""
    provider = sites[0]["provider_id"]
    return {
        "schema_version": "MEMORIVE_BROWSER_SESSION_LIBRARY_V4_DRAFT",
        "source": "MEMORIVE_BROWSER_COMPANION_VISIBLE_UI_V4_DRAFT",
        "generated_at": "2026-09-20T08:30:00Z",
        "sync_scope": "RECENT_30_DAYS",
        "provider_id": provider,
        "site_profile_sha256": site_profile_sha256,
        "enumerated_count": 1,
        "captured_count": 1,
        "failed_count": 0,
        "conversations": [{
            "provider_id": provider,
            "provider_session_id": "qwen-custom-001",
            "title": "Qwen custom site",
            "created_at": "2026-09-20T07:30:00Z",
            "updated_at": "2026-09-20T08:00:00Z",
            "recency_bucket": "TODAY",
            "source_url": "https://chat.qwen.ai/c/qwen-custom-001",
            "capture_method": "BROWSER_COMPANION_BACKGROUND_TAB_V4",
            "capture_status": "COMPLETE",
            "source_truncated": False,
            "top_boundary_reached": True,
            "bottom_boundary_reached": True,
            "messages": [
                {"message_id": "m1", "role": "user", "content_kind": "text", "content": "问题", "occurred_at": "UNKNOWN"},
                {"message_id": "m2", "role": "assistant", "content_kind": "text", "content": "回答", "occurred_at": "UNKNOWN"},
            ],
        }],
        "failures": [],
    }


def test_a_custom_site_file_of_the_site_sync_imports_against_the_list_it_saved(tmp_path: Path) -> None:
    rows = default_sites()
    rows[0].update(name="Qwen", adapter="generic", url="https://chat.qwen.ai/")
    rows[0]["selectors"]["path_prefix"] = "/c/"
    sites = normalize_sites(rows)
    folder = tmp_path / "BROWSER_COMPANION"
    folder.mkdir()
    (folder / "sites_v1.json").write_text(json.dumps({
        "schema_version": "DesktopBrowserSites-v1", "revision": 1, "sites": sites, "profile_sha256": profile_hash(sites),
    }), encoding="utf-8")
    payload = _custom_site_bundle(sites, site_profile_sha256=profile_hash(sites))
    raw = json.dumps(payload).encode("utf-8")

    batch = BrowserLibraryImporter.parse_payload(payload, raw_bytes=raw, allowed_site_settings=saved_site_settings(folder))

    assert batch.provider == sites[0]["provider_id"] and len(batch.conversations) == 1
    # Without the saved list, or with a file of another list, nothing comes in.
    assert saved_site_settings(tmp_path / "elsewhere") is None
    with pytest.raises(ArchiveInputError, match="CUSTOM_SITE_NOT_CONFIGURED"):
        BrowserLibraryImporter.parse_payload(payload, raw_bytes=raw, allowed_site_settings=None)
    other = _custom_site_bundle(sites, site_profile_sha256="0" * 64)
    with pytest.raises(ArchiveInputError, match="SITE_PROFILE_MISMATCH"):
        BrowserLibraryImporter.parse_payload(other, raw_bytes=json.dumps(other).encode("utf-8"),
                                             allowed_site_settings=saved_site_settings(folder))
