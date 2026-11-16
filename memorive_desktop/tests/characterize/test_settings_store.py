"""The settings file on disk: what loading and saving do with edited, newer or broken files.

Pins today's behaviour of memorive.desktop.settings.store.SettingsStore before the planned change
(the file carries its own checksum and an exact field set; any mismatch makes load()
move the file aside and start again from defaults). Each case uses its own profile folder.
Timestamps and the quarantine file name are left out of the golden file.
"""
import json
from pathlib import Path

from memorive.desktop.settings.contracts import default_settings
from memorive.platform.json_files import canonical_sha256
from memorive.desktop.settings.store import SettingsCorrupt, SettingsStore
from tests.characterize.golden import check

HERE = Path(__file__).parent


def _summary(store, result=None, error=None):
    folder = store.profile_root
    try:
        on_disk = json.loads(store.path.read_text(encoding="utf8")) if store.path.exists() else None
    except ValueError:
        on_disk = "<not json>"
    receipt = json.loads(store.recovery_receipt_path.read_text(encoding="utf8")) if store.recovery_receipt_path.exists() else None
    defaults = canonical_sha256(default_settings())
    return {
        "error": error,
        "loaded_revision": result and result["revision"],
        "loaded_is_defaults": result is not None and canonical_sha256(result["settings"]) == defaults,
        "loaded_language": result and result["settings"]["preferences"].get("language"),
        "file_fields": sorted(on_disk) if isinstance(on_disk, dict) else None,
        "file_revision": on_disk.get("revision") if isinstance(on_disk, dict) else None,
        "file_is_defaults": isinstance(on_disk, dict) and canonical_sha256(on_disk.get("settings")) == defaults,
        "recovery": receipt and {k: receipt[k] for k in ("reason_code", "status", "raw_content_in_receipt")},
        "other_files": sorted("<quarantine>" if "corrupt" in p.name or p.name.startswith("settings.json.") else p.name
                              for p in folder.iterdir() if p.name not in ("settings.json", "settings_recovery.json")),
    }


def _store_with(tmp_path, name, edit=None, raw=None):
    store = SettingsStore(tmp_path / name)
    saved = store.load()
    settings = json.loads(json.dumps(saved["settings"]))
    settings["preferences"]["language"] = "en-US"
    saved = store.save(settings, expected_revision=saved["revision"])
    if raw is not None:
        store.path.write_bytes(raw)
    elif edit is not None:
        value = json.loads(store.path.read_text(encoding="utf8"))
        edit(value)
        store.path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf8")
    return store


def _load(store, **kwargs):
    try:
        return _summary(store, result=store.load(**kwargs))
    except (SettingsCorrupt, ValueError) as error:
        return _summary(store, error=f"{type(error).__name__}: {error}")


def _hand_edit(value):
    value["settings"]["preferences"]["language"] = "ja-JP"


def test_loading_edited_and_foreign_files(tmp_path):
    cases = {
        "first_start": _load(SettingsStore(tmp_path / "fresh")),
        "saved_then_loaded": _load(_store_with(tmp_path, "saved")),
        "hand_edited_value": _load(_store_with(tmp_path, "edited", _hand_edit)),
        "hand_edited_strict": _load(_store_with(tmp_path, "edited_strict", _hand_edit), recover_corruption=False),
        "field_added_by_newer_version": _load(_store_with(tmp_path, "extra", lambda v: v.update(new_field=1))),
        "settings_field_added": _load(_store_with(tmp_path, "inner_extra", lambda v: v["settings"].update(new_section={}))),
        "newer_store_version": _load(_store_with(tmp_path, "newer", lambda v: v.update(schema_version="SettingsStoreEnvelope-v8"))),
        "revision_negative": _load(_store_with(tmp_path, "revision", lambda v: v.update(revision=-1))),
        "checksum_lowercase": _load(_store_with(tmp_path, "lower", lambda v: v.update(settings_sha256=v["settings_sha256"].lower()))),
        "not_json": _load(_store_with(tmp_path, "garbage", raw=b"{not json")),
        "not_json_strict": _load(_store_with(tmp_path, "garbage_strict", raw=b"{not json"), recover_corruption=False),
        "empty_file": _load(_store_with(tmp_path, "empty", raw=b"")),
        "a_list": _load(_store_with(tmp_path, "list", raw=b"[]")),
    }
    check("settings_store_loading", cases, HERE)


def test_saving(tmp_path):
    store = _store_with(tmp_path, "save")
    current = store.load()
    cases = {}
    try:
        store.save(current["settings"], expected_revision=current["revision"] - 1)
    except ValueError as error:
        cases["stale_revision"] = str(error)
    edited = _store_with(tmp_path, "save_over_edit", _hand_edit)
    try:
        edited.save(current["settings"], expected_revision=current["revision"])
        cases["save_over_hand_edit"] = _summary(edited)
    except (SettingsCorrupt, ValueError) as error:
        cases["save_over_hand_edit"] = _summary(edited, error=f"{type(error).__name__}: {error}")
    saved = store.save(current["settings"], expected_revision=current["revision"])
    cases["saved_revision"] = saved["revision"]
    cases["saved_fields"] = sorted(saved)
    check("settings_store_saving", cases, HERE)
