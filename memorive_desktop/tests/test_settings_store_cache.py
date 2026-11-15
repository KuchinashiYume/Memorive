"""SettingsStore remembers the last file content it checked and returns a copy of that result
while the file bytes stay the same. Any change on disk must be seen at once: a hand edit, a
save through another store object, a damaged file. Each result must be a private copy.
"""
import json

from memorive_settings.contracts import canonical_sha256, default_settings
from memorive_settings.store import SettingsStore


def _with_language(store, language):
    current = store.load()
    settings = json.loads(json.dumps(current["settings"]))
    settings["preferences"]["language"] = language
    return store.save(settings, expected_revision=current["revision"])


def test_same_bytes_give_equal_private_copies(tmp_path):
    store = SettingsStore(tmp_path)
    _with_language(store, "en-US")
    first = store.load()
    first["settings"]["preferences"]["language"] = "changed by the caller"
    second = store.load()
    assert second["settings"]["preferences"]["language"] == "en-US"
    assert second == store.load()


def test_hand_edited_file_is_read_again(tmp_path):
    store = SettingsStore(tmp_path)
    _with_language(store, "en-US")
    assert store.load()["settings"]["preferences"]["language"] == "en-US"
    value = json.loads(store.path.read_text(encoding="utf8"))
    value["settings"]["preferences"]["language"] = "zh-CN"
    store.path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf8")
    assert store.load()["settings"]["preferences"]["language"] == "zh-CN"


def test_save_by_another_store_object_is_seen(tmp_path):
    reader, writer = SettingsStore(tmp_path), SettingsStore(tmp_path)
    saved = _with_language(writer, "en-US")
    assert reader.load()["revision"] == saved["revision"]
    saved = _with_language(writer, "zh-CN")
    loaded = reader.load()
    assert loaded["revision"] == saved["revision"]
    assert loaded["settings"]["preferences"]["language"] == "zh-CN"
    # and the reader can save on top of it without a revision conflict
    assert _with_language(reader, "en-US")["revision"] == saved["revision"] + 1


def test_damaged_file_after_a_cached_read_is_recovered(tmp_path):
    store = SettingsStore(tmp_path)
    _with_language(store, "en-US")
    store.load()                                     # the good content is now remembered
    store.path.write_bytes(b"{ not json")
    recovered = store.load()
    assert recovered["revision"] == 0
    assert canonical_sha256(recovered["settings"]) == canonical_sha256(default_settings())
    assert store.recovery_receipt_path.exists()
    assert any(p.name.startswith("settings.corrupt.") for p in tmp_path.iterdir())
    assert store.load() == recovered                 # the recovered file reads back the same


def test_file_restored_to_earlier_bytes_is_read_again(tmp_path):
    store = SettingsStore(tmp_path)
    _with_language(store, "en-US")
    earlier = store.path.read_bytes()
    _with_language(store, "zh-CN")
    assert store.load()["settings"]["preferences"]["language"] == "zh-CN"
    store.path.write_bytes(earlier)
    assert store.load()["settings"]["preferences"]["language"] == "en-US"
