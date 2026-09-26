"""Regression coverage for owned-session cleanup races.

The disappearing leaf reproduces the WebView2 lockfile race observed on the
packaged console.  The marker tests preserve the existing fail-closed boundary.
"""

from pathlib import Path

import pytest

import common


def _owned_tree(tmp_path):
    root = tmp_path / "explicit-owned-session"
    root.mkdir()
    marker = root / ".console-session.json"
    common.write_json(
        marker,
        {
            "owner": "MEMORIVE_INDEPENDENT_CONSOLE",
            "session_id": "synthetic-owned-cleanup-race",
        },
    )
    leaf = root / "webview2" / "EBWebView" / "lockfile"
    leaf.parent.mkdir(parents=True)
    leaf.write_bytes(b"synthetic lock")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"must preserve")
    return root, marker, leaf, outside


def test_vanishing_owned_cache_leaf_replans_and_completes(tmp_path, monkeypatch):
    root, marker, leaf, outside = _owned_tree(tmp_path)
    original = common._cleanup_plan
    calls = []

    def race(root_arg, marker_name):
        plan = original(root_arg, marker_name)
        calls.append(1)
        if len(calls) == 1:
            leaf.unlink()
        return plan

    monkeypatch.setattr(common, "_cleanup_plan", race)
    monkeypatch.setattr(common.time, "sleep", lambda *_: None)

    result = common._clear_owned_tree(root, marker.name)

    assert len(calls) == 2
    assert result["removed_entry_count"] >= 3
    assert not root.exists()
    assert outside.read_bytes() == b"must preserve"


def test_vanishing_owner_marker_still_fails_closed(tmp_path, monkeypatch):
    root, marker, leaf, outside = _owned_tree(tmp_path)
    original = common._cleanup_plan

    def race(root_arg, marker_name):
        plan = original(root_arg, marker_name)
        marker.unlink()
        leaf.unlink()
        return plan

    monkeypatch.setattr(common, "_cleanup_plan", race)
    monkeypatch.setattr(common.time, "sleep", lambda *_: None)

    with pytest.raises(FileNotFoundError):
        common._clear_owned_tree(root, marker.name)

    assert root.exists()
    assert outside.read_bytes() == b"must preserve"


def test_changed_owner_marker_still_fails_closed(tmp_path, monkeypatch):
    root, marker, leaf, outside = _owned_tree(tmp_path)
    original = common._cleanup_plan

    def race(root_arg, marker_name):
        plan = original(root_arg, marker_name)
        common.write_json(marker, {"owner": "unrelated"})
        leaf.unlink()
        return plan

    monkeypatch.setattr(common, "_cleanup_plan", race)
    monkeypatch.setattr(common.time, "sleep", lambda *_: None)

    with pytest.raises(common.CleanupPathError, match="CLEANUP_MARKER_MISMATCH"):
        common._clear_owned_tree(root, marker.name)

    assert root.exists()
    assert outside.read_bytes() == b"must preserve"
