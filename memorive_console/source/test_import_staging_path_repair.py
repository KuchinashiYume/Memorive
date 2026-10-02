from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import threading

import pytest

import manager as manager_module
from manager import Manager


FIXTURE_NAME = "console-suite-import.txt"
CLASSIC_MAX_PATH = 260


def _fit_file_path(base: Path, name: str, minimum: int, maximum: int) -> Path:
    """Create a real path in the requested character-length window."""
    candidate = base / name
    if len(str(candidate)) < minimum:
        missing = minimum - len(str(candidate))
        # Adding a directory contributes its characters plus one separator.
        if missing == 1:
            missing += 1
        base = base / ("u" * (missing - 1))
        candidate = base / name
    assert minimum <= len(str(candidate)) <= maximum, str(candidate)
    base.mkdir(parents=True)
    return candidate


def _vm_shaped_data_root(tmp_path: Path) -> Path:
    # pytest's real temporary root already contains the current, relatively
    # long Windows profile name. Add only the console-owned data-root segment
    # so the final staged path remains valid while the 0.2.1 temporary name
    # crosses MAX_PATH.
    base = tmp_path / "profile-data"
    prototype = (
        base
        / "state"
        / "profile"
        / "inbox_source"
        / "console-staged"
        / "console-import-0000000000000000-00000000"
        / "00"
        / FIXTURE_NAME
    )
    if len(str(prototype)) < 225:
        missing = 225 - len(str(prototype))
        if missing == 1:
            missing += 1
        base = base / ("p" * (missing - 1))
        prototype = (
            base
            / "state"
            / "profile"
            / "inbox_source"
            / "console-staged"
            / "console-import-0000000000000000-00000000"
            / "00"
            / FIXTURE_NAME
        )
    assert 225 <= len(str(prototype)) < CLASSIC_MAX_PATH, str(prototype)
    base.mkdir(parents=True)
    return base


def _classic_win32_create_guard(monkeypatch):
    real_open = Path.open
    created = []
    lock = threading.Lock()

    def guarded(path, mode="r", *args, **kwargs):
        if "x" in mode:
            with lock:
                created.append(path)
            if len(str(path)) >= CLASSIC_MAX_PATH:
                raise FileNotFoundError(2, "classic Win32 path limit", str(path))
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    return created


def _old_temporary_path(target: Path) -> Path:
    return target.with_name("." + target.name + "." + ("0" * 32) + ".tmp")


def test_vm_long_profile_fixture_stages_without_crossing_classic_max_path(monkeypatch, tmp_path):
    source = _fit_file_path(tmp_path / "fixture-source", FIXTURE_NAME, 189, 210)
    source.write_text("Memorive console one-click test fixture\n", encoding="utf-8")
    data_root = _vm_shaped_data_root(tmp_path)
    manager = Manager.__new__(Manager)
    manager.session = {"data_root": str(data_root)}
    created = _classic_win32_create_guard(monkeypatch)

    execution, receipt = manager._stage_import(
        {
            "request_id": "suite-vm-long-profile-import",
            "operation": "inbox.import",
            "params": {"paths": [str(source)]},
            "allow_model_calls": False,
        }
    )

    staged = Path(execution["params"]["paths"][0])
    assert staged.is_file()
    assert staged.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert 225 <= len(str(staged)) < CLASSIC_MAX_PATH
    assert len(str(_old_temporary_path(staged))) >= CLASSIC_MAX_PATH
    assert created and all(len(str(path)) < CLASSIC_MAX_PATH for path in created)
    assert receipt["source_paths_recorded"] is False
    assert receipt["cleanup"] == "SESSION_CLOSE"


def test_real_long_filename_does_not_expand_temporary_basename(monkeypatch, tmp_path):
    filename = ("研究资料" * 28) + ".txt"
    assert 100 < len(filename) < 255
    source = tmp_path / "author-source" / filename
    source.parent.mkdir()
    source.write_bytes(b"long filename boundary")
    target = _fit_file_path(tmp_path / "owned-slot", filename, 245, 259)
    created = _classic_win32_create_guard(monkeypatch)

    receipt = Manager._copy_import_source(source, target)

    assert target.read_bytes() == source.read_bytes()
    assert receipt["bytes"] == len(source.read_bytes())
    assert len(str(_old_temporary_path(target))) >= CLASSIC_MAX_PATH
    assert len(created) == 1
    assert created[0].parent == target.parent
    assert created[0] != target
    assert len(str(created[0])) <= len(str(target)) < CLASSIC_MAX_PATH


def test_concurrent_owned_slots_keep_temporary_full_paths_unique(monkeypatch, tmp_path):
    created = _classic_win32_create_guard(monkeypatch)
    jobs = []
    for index in range(12):
        source = tmp_path / "sources" / f"{index:02d}" / "same-name.txt"
        source.parent.mkdir(parents=True)
        source.write_text(f"payload-{index}", encoding="utf-8")
        slot = tmp_path / "owned-batches" / f"batch-{index:02d}" / "00"
        slot.mkdir(parents=True)
        jobs.append((source, slot / source.name, index))

    with ThreadPoolExecutor(max_workers=6) as pool:
        receipts = list(pool.map(lambda row: Manager._copy_import_source(row[0], row[1]), jobs))

    assert len(receipts) == len(jobs)
    assert len(created) == len(jobs)
    assert len({str(path) for path in created}) == len(jobs)
    assert all(path.parent.name == "00" for path in created)
    for source, target, index in jobs:
        assert target.read_text(encoding="utf-8") == f"payload-{index}"
        assert source.read_text(encoding="utf-8") == f"payload-{index}"


def test_interrupted_copy_removes_temporary_and_does_not_publish_target(monkeypatch, tmp_path):
    source = tmp_path / "source.txt"
    source.write_bytes(b"interrupted payload")
    slot = tmp_path / "owned-slot" / "00"
    slot.mkdir(parents=True)
    target = slot / "target.txt"
    created = _classic_win32_create_guard(monkeypatch)

    def interrupted(_fileno):
        raise InterruptedError("simulated interruption after write")

    monkeypatch.setattr(manager_module.os, "fsync", interrupted)
    with pytest.raises(InterruptedError, match="simulated interruption"):
        Manager._copy_import_source(source, target)

    assert not target.exists()
    assert len(created) == 1
    assert not created[0].exists()
    assert list(slot.iterdir()) == []
    assert source.read_bytes() == b"interrupted payload"


def test_one_character_target_never_aliases_temporary_name(monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"one-character target")
    slot = tmp_path / "owned-slot" / "00"
    slot.mkdir(parents=True)
    target = slot / "~"
    created = _classic_win32_create_guard(monkeypatch)

    Manager._copy_import_source(source, target)

    assert target.read_bytes() == b"one-character target"
    assert len(created) == 1
    assert created[0] != target
    assert created[0].parent == target.parent
