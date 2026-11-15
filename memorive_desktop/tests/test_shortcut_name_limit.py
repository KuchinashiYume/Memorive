"""Shortcut names: 20 characters for new or edited names, existing longer names kept."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# MEMORIVE_TEST_ROOT points the test at a packaged build's _internal folder.
ROOT = Path(os.environ.get("MEMORIVE_TEST_ROOT") or Path(__file__).resolve().parents[1])
for candidate in (ROOT / "product" / "desktop", ROOT / "source"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from runtime import ProductApi  # noqa: E402


class AssistantStub:
    def set_control(self, **_kwargs: object) -> None:
        return None


def make_api(path: Path) -> ProductApi:
    api = object.__new__(ProductApi)
    api._assistant_preferences_path = path
    api._assistant = AssistantStub()
    api._set_assistant_window_visibility = lambda visible: {"visible": visible, "status": "PASS"}
    return api


def request(names: list[str]) -> dict[str, object]:
    return {
        "enabled": True,
        "shortcut_entries": [{"name": n, "target": "https://example.com/x" if n else ""} for n in names],
        "reminder_scope": "IMPORTANT_AND_COMPLETE",
        "hide_sensitive_names": False,
    }


def stored_names(path: Path) -> list[str]:
    return [e["name"] for e in json.loads(path.read_text(encoding="utf-8"))["shortcut_entries"]]


def test_first_save_works_without_an_existing_preferences_file(tmp_path: Path) -> None:
    path = tmp_path / "assistant_preferences_v3.json"
    make_api(path)._assistant_preference_save(request(["a" * 20, "", ""]))
    assert stored_names(path) == ["a" * 20, "", ""]


def test_new_name_longer_than_20_characters_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "assistant_preferences_v3.json"
    api = make_api(path)
    api._assistant_preference_save(request(["short", "", ""]))
    with pytest.raises(ValueError, match="ASSISTANT_SHORTCUT_NAME_TOO_LONG"):
        api._assistant_preference_save(request(["b" * 21, "", ""]))
    assert stored_names(path) == ["short", "", ""]


def test_existing_long_name_is_kept_until_it_is_edited(tmp_path: Path) -> None:
    path = tmp_path / "assistant_preferences_v3.json"
    api = make_api(path)
    api._assistant_preference_save(request(["short", "", ""]))
    value = json.loads(path.read_text(encoding="utf-8"))
    value["shortcut_entries"][0]["name"] = "c" * 30  # written by a version that allowed 40
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    api._assistant_preference_save(request(["c" * 30, "second", ""]))
    assert stored_names(path) == ["c" * 30, "second", ""]
    with pytest.raises(ValueError, match="ASSISTANT_SHORTCUT_NAME_TOO_LONG"):
        api._assistant_preference_save(request(["d" * 30, "second", ""]))


PROFILES = Path(os.environ["MEMORIVE_OLD_PROFILES"]) if os.environ.get("MEMORIVE_OLD_PROFILES") else None


@pytest.mark.skipif(PROFILES is None or not PROFILES.is_dir(), reason="MEMORIVE_OLD_PROFILES not set")
def test_preferences_written_by_released_versions_load_and_resave(tmp_path: Path) -> None:
    checked = 0
    for source in sorted(PROFILES.glob("*/assistant_preferences_v3.json")):
        path = tmp_path / source.parent.name / source.name
        path.parent.mkdir()
        path.write_bytes(source.read_bytes())
        api = make_api(path)
        loaded = api._load_assistant_preferences()
        assert len(loaded["shortcut_entries"]) == 3
        api._assistant_preference_save({k: loaded[k] for k in ("enabled", "shortcut_entries", "reminder_scope", "hide_sensitive_names")})
        assert api._load_assistant_preferences()["shortcut_entries"] == loaded["shortcut_entries"]
        checked += 1
    assert checked >= 1
