"""The notice shown when the settings file comes from a newer version of Memorive.

- memorive.desktop.settings.store.file_from_newer_version only reads the version of the file;
- startup_notice.notice_page builds the notice from the shipped bundle_integrated.html, reusing
  its close dialog, style sheets, close icon and typography functions. If the page is changed so
  that one of these parts is missing or renamed, notice_page raises
  STARTUP_NOTICE_PAGE_PARTS_MISSING; test_notice_page_from_the_shipped_bundle catches that before
  a release;
- startup_notice.saved_preferences reads language and font size, with zh-CN / 100 as fallback.
No window is opened here.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import startup_notice
from memorive.desktop.settings.contracts import STORE_SCHEMA_VERSION
from memorive.desktop.settings.store import file_from_newer_version

BUNDLE = Path(__file__).resolve().parents[1] / "product" / "desktop" / "bundle_integrated.html"
CURRENT = int(re.fullmatch(r"SettingsStoreEnvelope-v(\d+)", STORE_SCHEMA_VERSION).group(1))


def _file(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return path


@pytest.mark.parametrize("name, content, expected", [
    ("one_newer", {"schema_version": f"SettingsStoreEnvelope-v{CURRENT + 1}"}, True),
    ("much_newer", {"schema_version": f"SettingsStoreEnvelope-v{CURRENT + 10}"}, True),
    ("current", {"schema_version": STORE_SCHEMA_VERSION}, False),
    ("older", {"schema_version": f"SettingsStoreEnvelope-v{CURRENT - 1}"}, False),
    ("first_format", {"schema_version": "SettingsTemplate-v1"}, False),
    ("unknown_name", {"schema_version": "SomethingElse-v99"}, False),
    ("version_not_text", {"schema_version": 99}, False),
    ("no_version", {"revision": 1}, False),
    ("a_list", [1, 2], False),
    ("not_json", "{not json", False),
    ("empty", "", False),
])
def test_file_from_newer_version(tmp_path, name, content, expected):
    assert file_from_newer_version(_file(tmp_path, f"{name}.json", content)) is expected


def test_missing_file_is_not_from_a_newer_version(tmp_path):
    assert file_from_newer_version(tmp_path / "absent.json") is False


@pytest.mark.parametrize("language", startup_notice.LANGUAGES)
def test_notice_page_from_the_shipped_bundle(language):
    text = startup_notice.SETTINGS_FROM_NEWER_VERSION[language]
    page = startup_notice.notice_page(BUNDLE, language, text, 150)
    assert 'class="desktop-close-dialog"' in page
    assert ".desktop-close-dialog" in page          # the close dialog's styles came along
    assert "const typeRole = (element) => {" in page
    assert "const setFontFactor = (percent) => {" in page
    assert "const applyFontScale = (percent, rootNode = document.body) => {" in page
    assert "applyFontScale(150);" in page
    assert f'lang="{language}"' in page
    for key in ("title", "folder", "exit"):
        assert text[key] in page
    assert 'data-window-action="folder"' in page and 'data-window-action="exit"' in page


# The notice's script with just enough of a page around it: a dialog that records showModal and the
# window bridge that records fit. It ran setFontFactor before the dialog opened, and that function was
# not copied from the main page (ReferenceError, the notice never showed).
NOTICE_RUN = """
class Element {}
class HTMLElement extends Element {}
const result = {showModal: false, fit: null};
const dialog = {querySelectorAll() { return []; }, addEventListener() {},
                showModal() { result.showModal = true; }, getBoundingClientRect() { return {width: 340, height: 210}; }};
const style = {getPropertyValue() { return ''; }, setProperty() {}};
const document = {body: Object.assign(new Element(), {dataset: {}, style, querySelectorAll() { return []; }}),
                  documentElement: {style}, getElementById() { return dialog; }, querySelectorAll() { return []; }};
const window = {innerWidth: 1280, addEventListener() {}, pywebview: {api: {fit(...size) { result.fit = size; }}}};
try { SCRIPT } catch (error) { result.error = error.name + ': ' + error.message; }
console.log(JSON.stringify(result));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_the_notice_script_opens_the_dialog_and_sizes_the_window(tmp_path):
    page = startup_notice.notice_page(BUNDLE, "zh-CN", startup_notice.SETTINGS_FROM_NEWER_VERSION["zh-CN"], 150)
    script = re.search(r"<script>(.*)</script>", page, re.S).group(1)
    runner = tmp_path / "notice.js"
    runner.write_text(NOTICE_RUN.replace("SCRIPT", script), encoding="utf-8")
    out = subprocess.run([shutil.which("node"), str(runner)], capture_output=True, text=True, timeout=60)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == {"showModal": True, "fit": [340, 210]}


@pytest.mark.parametrize("break_page", [
    lambda page: page.replace("const typeRole = (element) => {", "const roleOfType = (element) => {"),
    lambda page: page.replace("const setFontFactor = (percent) => {", "const setFontSize = (percent) => {"),
    lambda page: page.replace("const applyFontScale = (percent, rootNode = document.body) => {",
                              "const applyScale = (percent, rootNode = document.body) => {"),
    lambda page: page.replace(".desktop-close-dialog", ".app-close-dialog"),
    lambda page: re.sub(r"glyphs=\{[^}]*\}", "glyphs={}", page, count=1),
], ids=["typeRole_renamed", "setFontFactor_renamed", "applyFontScale_renamed", "close_dialog_style_renamed",
        "close_icon_missing"])
def test_notice_page_refuses_a_page_without_its_parts(tmp_path, break_page):
    broken = tmp_path / "bundle_integrated.html"
    broken.write_text(break_page(BUNDLE.read_text(encoding="utf-8")), encoding="utf-8")
    with pytest.raises(ValueError, match="STARTUP_NOTICE_PAGE_PARTS_MISSING"):
        startup_notice.notice_page(broken, "en-US", startup_notice.SETTINGS_FROM_NEWER_VERSION["en-US"])


@pytest.mark.parametrize("preferences, expected", [
    ({"language": "ja-JP", "font_scale_percent": 150}, ("ja-JP", 150)),
    ({"language": "en-US", "font_scale_percent": 80}, ("en-US", 80)),
    ({"language": "en-US", "font_scale_percent": 200}, ("en-US", 200)),
    ({"language": "fr-FR", "font_scale_percent": 120}, ("zh-CN", 120)),
    ({"language": "en-US", "font_scale_percent": 79}, ("en-US", 100)),
    ({"language": "en-US", "font_scale_percent": 201}, ("en-US", 100)),
    ({"language": "en-US", "font_scale_percent": True}, ("en-US", 100)),
    ({"language": "en-US", "font_scale_percent": "150"}, ("en-US", 100)),
    ({}, ("zh-CN", 100)),
])
def test_saved_preferences(tmp_path, preferences, expected):
    path = _file(tmp_path, "settings.json", {"schema_version": STORE_SCHEMA_VERSION, "settings": {"preferences": preferences}})
    assert startup_notice.saved_preferences(path) == expected


@pytest.mark.parametrize("content", ["{not json", json.dumps({"settings": {}}), json.dumps([1]), json.dumps({"settings": []})])
def test_saved_preferences_fall_back_when_unreadable(tmp_path, content):
    assert startup_notice.saved_preferences(_file(tmp_path, "settings.json", content)) == ("zh-CN", 100)
    assert startup_notice.saved_preferences(tmp_path / "absent.json") == ("zh-CN", 100)
