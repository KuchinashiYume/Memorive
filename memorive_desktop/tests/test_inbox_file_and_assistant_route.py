"""ProductApi: opening an inbox item's stored file, and switching the main window from the assistant.

Before an inbox item is previewed or opened, its stored path must stay inside the inbox folder,
be an ordinary file and still have the hash recorded at import. The assistant may only switch the
main window to the home, settings or messages page.
"""
from __future__ import annotations

import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from memorive.desktop.api import ProductApi
from memorive.desktop.assistant.desktop import AssistantDesktop


class _Service:
    def __init__(self, detail):
        self.detail = detail
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        return dict(self.detail)


@pytest.fixture
def stored(tmp_path):
    root = tmp_path / "inbox"
    (root / "bound" / "batch").mkdir(parents=True)
    path = root / "bound" / "batch" / "paper.md"
    path.write_bytes(b"paper text")
    detail = {"item_id": "item-1", "bound_locator": "bound/batch/paper.md",
              "content_sha256": hashlib.sha256(b"paper text").hexdigest().upper()}
    return root, detail


def _open(root, detail, item_id="item-1"):
    api = SimpleNamespace(_service=_Service(detail), _inbox_root=root, _is_reparse_point=ProductApi._is_reparse_point)
    return ProductApi._inbox_item_path(api, item_id)


def test_an_item_whose_file_is_unchanged_opens(stored):
    root, detail = stored
    got, path = _open(root, detail)
    assert got == detail and path == (root / "bound" / "batch" / "paper.md").resolve()


@pytest.mark.parametrize("item_id", ["", None, 7])
def test_an_item_id_is_required(stored, item_id):
    with pytest.raises(ValueError, match="INBOX_ITEM_ID_INVALID"):
        _open(*stored, item_id=item_id)


@pytest.mark.parametrize("locator, code", [
    (None, "INBOX_ITEM_NOT_BOUND"),
    ("", "INBOX_ITEM_NOT_BOUND"),
    ("bound/../../secret.md", "INBOX_ITEM_NOT_BOUND"),
    ("bound/batch/missing.md", "INBOX_ITEM_FILE_INVALID"),
    ("bound/batch", "INBOX_ITEM_FILE_INVALID"),
])
def test_an_item_without_a_stored_file_inside_the_inbox_is_refused(stored, locator, code):
    root, detail = stored
    with pytest.raises(ValueError, match=code):
        _open(root, dict(detail, bound_locator=locator))


def test_an_absolute_locator_outside_the_inbox_is_refused(stored, tmp_path):
    root, detail = stored
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"paper text")
    with pytest.raises(ValueError, match="INBOX_ITEM_PATH_ESCAPE"):
        _open(root, dict(detail, bound_locator=str(outside)))


def test_a_changed_file_is_refused(stored):
    root, detail = stored
    (root / "bound" / "batch" / "paper.md").write_bytes(b"paper text, edited")
    with pytest.raises(ValueError, match="INBOX_ITEM_HASH_MISMATCH"):
        _open(root, detail)
    with pytest.raises(ValueError, match="INBOX_ITEM_HASH_MISMATCH"):
        _open(root, dict(detail, content_sha256=None))


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows feature")
def test_a_junction_is_followed_only_to_a_file_inside_the_inbox(stored, tmp_path):
    import _winapi
    root, detail = stored
    _winapi.CreateJunction(str(root / "bound" / "batch"), str(root / "bound" / "inside"))
    _, path = _open(root, dict(detail, bound_locator="bound/inside/paper.md"))
    assert path == (root / "bound" / "batch" / "paper.md").resolve()
    with pytest.raises(ValueError, match="INBOX_ITEM_FILE_INVALID"):
        _open(root, dict(detail, bound_locator="bound/inside"))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "paper.md").write_bytes(b"paper text")
    _winapi.CreateJunction(str(outside), str(root / "bound" / "away"))
    with pytest.raises(ValueError, match="INBOX_ITEM_PATH_ESCAPE"):
        _open(root, dict(detail, bound_locator="bound/away/paper.md"))


def test_a_hard_linked_copy_is_an_ordinary_file(stored):
    root, detail = stored
    os.link(root / "bound" / "batch" / "paper.md", root / "bound" / "batch" / "copy.md")
    _open(root, dict(detail, bound_locator="bound/batch/copy.md"))


# Assistant switching the main window

class _Window:
    def __init__(self):
        self.scripts = []

    def evaluate_js(self, script):
        self.scripts.append(script)


@pytest.mark.parametrize("route", ["home", "settings", "messages"])
def test_the_assistant_switches_to_a_known_page_and_raises_the_window(route):
    raised = []
    api = SimpleNamespace(main_window=_Window(), bring_main_window_to_foreground=lambda: raised.append(True))
    AssistantDesktop._activate_main_route(api, route)
    assert api.main_window.scripts == ["window.__Desktop_INTEGRATED_APP__.activate(" + json.dumps(route) + ")"]
    assert raised == [True]


@pytest.mark.parametrize("route", ["library", "home');alert(1);('", "", None])
def test_the_assistant_cannot_open_any_other_page_or_inject_script(route):
    api = SimpleNamespace(main_window=_Window(), bring_main_window_to_foreground=lambda: None)
    with pytest.raises(ValueError, match="ASSISTANT_MAIN_ROUTE_INVALID"):
        AssistantDesktop._activate_main_route(api, route)
    assert api.main_window.scripts == []


def test_switching_needs_the_main_window():
    api = SimpleNamespace(main_window=None, bring_main_window_to_foreground=lambda: None)
    with pytest.raises(RuntimeError, match="PRODUCT_WINDOW_NOT_ATTACHED"):
        AssistantDesktop._activate_main_route(api, "home")
