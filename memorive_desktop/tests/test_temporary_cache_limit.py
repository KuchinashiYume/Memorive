"""Settings "maximum cache": staged import batches beyond the limit are removed, oldest first
(memorive.desktop.service.temporary_cache.limit).

Only the staging folder's own batch folders are measured and removed. A batch younger than an
hour may still be on its way into the inbox and stays; links and junctions are neither measured
through nor removed, and removing a batch never reaches what a link inside it points to. A batch
that cannot be removed (a file in use) is skipped and the rest are still handled.
"""
from __future__ import annotations

import os
import time

import pytest

from memorive.desktop.service.temporary_cache import SPARE_SECONDS, limit

MB = 1024 * 1024
NOW = time.time()


def _batch(root, name, megabytes, age_seconds):
    folder = root / name
    folder.mkdir(parents=True)
    with open(folder / "paper.pdf", "wb") as stream:   # sized without writing every byte
        stream.truncate(int(megabytes * MB))
    stamp = NOW - age_seconds
    os.utime(folder, (stamp, stamp))
    return folder


@pytest.fixture
def staging(tmp_path):
    root = tmp_path / "staging"
    root.mkdir()
    return root


def test_the_oldest_batches_go_until_the_cache_fits(staging):
    for name, age in (("old", 30 * 86400), ("middle", 5 * 86400), ("recent", 2 * 3600)):
        _batch(staging, name, 60, age)
    result = limit(staging, 128, now=NOW)
    assert sorted(p.name for p in staging.iterdir()) == ["middle", "recent"]
    assert (result["removed"], result["removed_bytes"], result["bytes"]) == (1, 60 * MB, 120 * MB)


def test_a_cache_within_the_limit_is_left_alone(staging):
    _batch(staging, "old", 100, 30 * 86400)
    assert limit(staging, 128, now=NOW) == {"bytes": 100 * MB, "removed": 0, "removed_bytes": 0}
    assert (staging / "old").is_dir()


def test_batches_staged_within_the_last_hour_stay_even_over_the_limit(staging):
    _batch(staging, "new", 200, SPARE_SECONDS - 60)
    _batch(staging, "old", 10, SPARE_SECONDS + 60)
    result = limit(staging, 128, now=NOW)
    assert sorted(p.name for p in staging.iterdir()) == ["new"]
    assert (result["removed"], result["bytes"]) == (1, 200 * MB)


@pytest.mark.parametrize("value", [127, 102401, 0, -1, 128.0, "512", True, None])
def test_a_limit_outside_what_settings_accepts_changes_nothing(staging, value):
    _batch(staging, "old", 200, 30 * 86400)
    assert limit(staging, value, now=NOW) == {"bytes": 0, "removed": 0, "removed_bytes": 0}
    assert (staging / "old").is_dir()


def test_a_missing_staging_folder_is_not_an_error(tmp_path):
    assert limit(tmp_path / "missing", 128, now=NOW) == {"bytes": 0, "removed": 0, "removed_bytes": 0}


def test_loose_files_in_the_staging_folder_are_not_batches(staging):
    with open(staging / "notes.txt", "wb") as stream:
        stream.truncate(200 * MB)
    stamp = NOW - 30 * 86400
    os.utime(staging / "notes.txt", (stamp, stamp))
    assert limit(staging, 128, now=NOW)["removed"] == 0
    assert (staging / "notes.txt").is_file()


def test_nothing_outside_the_staging_folder_is_touched(staging, tmp_path):
    inbox = tmp_path / "inbox"
    _batch(inbox, "imported", 200, 30 * 86400)
    _batch(staging, "old", 200, 30 * 86400)
    limit(staging, 128, now=NOW)
    assert (inbox / "imported" / "paper.pdf").is_file()


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows feature")
def test_a_junction_in_the_staging_folder_is_neither_measured_nor_removed(staging, tmp_path):
    import _winapi
    outside = _batch(tmp_path / "library", "folder", 300, 30 * 86400)
    _winapi.CreateJunction(str(outside), str(staging / "linked"))
    result = limit(staging, 128, now=NOW)
    assert result == {"bytes": 0, "removed": 0, "removed_bytes": 0}
    assert (staging / "linked").exists() and (outside / "paper.pdf").is_file()


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows feature")
def test_removing_a_batch_never_reaches_what_a_junction_inside_it_points_to(staging, tmp_path):
    import _winapi
    outside = _batch(tmp_path / "library", "folder", 1, 30 * 86400)
    old = _batch(staging, "old", 200, 30 * 86400)
    _winapi.CreateJunction(str(outside), str(old / "linked"))
    stamp = NOW - 30 * 86400
    os.utime(old, (stamp, stamp))
    assert limit(staging, 128, now=NOW)["removed"] == 1
    assert not old.exists()
    assert (outside / "paper.pdf").is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows refuses to delete a file another handle has open")
def test_a_batch_in_use_is_skipped_and_the_others_are_still_handled(staging):
    busy = _batch(staging, "busy", 100, 40 * 86400)
    _batch(staging, "old", 100, 30 * 86400)
    _batch(staging, "kept", 60, 30 * 60)
    with open(busy / "paper.pdf", "rb"):
        result = limit(staging, 128, now=NOW)
    assert (busy / "paper.pdf").is_file()
    assert not (staging / "old").exists() and (staging / "kept").is_dir()
    assert result["removed"] == 1 and result["removed_bytes"] == 100 * MB
