"""The browser extension speaks 简体中文, English and 日本語: every word is in _locales/<language>/messages.json,
used by manifest.json (__MSG_key__) and by the popup and runner pages through i18n.js, where the Aa button
picks the language. The package Memorive prepares for the browser, default or for changed sites, carries them."""
from __future__ import annotations

import json
from pathlib import Path
import re

from browser_companion_support import companion  # noqa: E402
from memorive.sessions.companion.site_profiles import configured_extension, default_sites  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "source" / "memorive" / "sessions" / "companion" / "extension"
LANGUAGES = ("en", "ja", "zh_CN")
LANGUAGE_FILES = {f"_locales/{language}/messages.json" for language in LANGUAGES}


def _words(language: str) -> dict[str, str]:
    table = json.loads((EXTENSION / "_locales" / language / "messages.json").read_text(encoding="utf-8"))
    return {key: value["message"] for key, value in table.items()}


def test_every_language_has_the_same_words_with_the_same_blanks() -> None:
    tables = {language: _words(language) for language in LANGUAGES}
    assert tables["en"].keys() == tables["ja"].keys() == tables["zh_CN"].keys()
    for key, english in tables["en"].items():
        blanks = sorted(set(re.findall(r"\$\d", english)))
        for language in LANGUAGES:
            assert sorted(set(re.findall(r"\$\d", tables[language][key]))) == blanks, (language, key)


def test_no_message_reads_as_a_named_placeholder() -> None:
    # Chrome and Edge read "$3$" (two blanks side by side) as a named placeholder and refuse to load the
    # extension when it is not defined.
    for language in LANGUAGES:
        for key, message in _words(language).items():
            assert not re.search(r"\$\w+\$", message), (language, key, message)


def test_every_word_the_extension_asks_for_exists() -> None:
    used = set()
    for name in ("popup.js", "runner.js"):
        used |= set(re.findall(r"""\bt\(\s*["']([A-Za-z]+)["']""", (EXTENSION / name).read_text(encoding="utf-8")))
        used |= set(re.findall(r"""say\(\s*\w+\s*,\s*["']([A-Za-z]+)["']""", (EXTENSION / name).read_text(encoding="utf-8")))
    for name in ("popup.html", "runner.html"):
        used |= set(re.findall(r'data-i18n(?:-title|-aria-label)?="([A-Za-z]+)"', (EXTENSION / name).read_text(encoding="utf-8")))
    used |= set(re.findall(r"__MSG_([A-Za-z]+)__", (EXTENSION / "manifest.json").read_text(encoding="utf-8")))
    stage_keys = re.search(r"const STAGE_KEYS = \{(.*?)\};", (EXTENSION / "runner.js").read_text(encoding="utf-8"), re.S)
    used |= set(re.findall(r'"(stage[A-Za-z]+)"', stage_keys.group(1)))
    assert len(used) > 40
    assert used <= set(_words("en")), sorted(used - set(_words("en")))


def test_the_manifest_takes_its_name_from_the_languages() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["default_locale"] == "en"
    assert manifest["name"] == "__MSG_extName__"
    assert manifest["action"]["default_title"] == "__MSG_actionTitle__"


def test_the_prepared_extension_folder_carries_the_three_languages(tmp_path: Path) -> None:
    api = companion()
    api.source_root = ROOT / "source"
    api.profile_root = tmp_path / "profile"
    api.system_effects_enabled = False
    _source, required, _digest = api._browser_companion_package()
    assert LANGUAGE_FILES <= required and "i18n.js" in required

    receipt = api.install_browser_companion("EDGE")

    folder = Path(receipt["extension_path"])
    for name in LANGUAGE_FILES | {"i18n.js"}:
        assert (folder / name).read_bytes() == (EXTENSION / name).read_bytes(), name


def test_a_package_for_changed_sites_carries_them_too(tmp_path: Path) -> None:
    sites = default_sites()
    sites[0]["enabled"] = False
    folder = configured_extension(EXTENSION, tmp_path / "packages", sites)
    for name in LANGUAGE_FILES:
        assert (folder / name).read_bytes() == (EXTENSION / name).read_bytes(), name
    assert json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["name"] == "__MSG_extName__"
