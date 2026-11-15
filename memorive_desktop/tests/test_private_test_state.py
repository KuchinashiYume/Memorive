"""Private-test state selection (`select_private_test_state`).

Each private-test package gets its own state folder. Opening it again keeps the state; taking
over an older package's state or clearing the current one first checks that state's identity
file and makes a full backup.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

import product_identity
from product_identity import select_private_test_state


def _identity(state):
    return json.loads((state / "profile" / "profile_identity.json").read_text(encoding="utf-8"))


def _edit_identity(state, **changes):
    path = state / "profile" / "profile_identity.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    for key, item in changes.items():
        if item is None:
            value.pop(key)
        else:
            value[key] = item
    path.write_text(json.dumps(value), encoding="utf-8")


def test_a_new_package_gets_a_clean_state_and_keeps_it(tmp_path):
    state, receipt = select_private_test_state(tmp_path, "build-1")
    assert receipt["selection_mode"] == "NEW" and receipt["backup_root"] is None
    first = _identity(state)
    assert (first["channel"], first["created_by_package"], first["migration_revision"]) == ("PRIVATE_TEST", "build-1", 0)
    (state / "notes.txt").write_text("kept", encoding="utf-8")
    again, receipt = select_private_test_state(tmp_path, "build-1")
    assert again == state and receipt["selection_mode"] == "EXISTING"
    assert _identity(state)["profile_instance_id"] == first["profile_instance_id"]
    assert (state / "notes.txt").read_text(encoding="utf-8") == "kept"


def test_taking_over_an_older_state_copies_it_and_backs_it_up(tmp_path):
    old, _ = select_private_test_state(tmp_path, "build-1")
    (old / "notes.txt").write_text("from build 1", encoding="utf-8")
    state, receipt = select_private_test_state(tmp_path, "build-2", migrate_from="build-1")
    assert receipt["selection_mode"] == "EXPLICIT_MIGRATION"
    assert (state / "notes.txt").read_text(encoding="utf-8") == "from build 1"
    assert (old / "notes.txt").exists()
    history = _identity(state)["migration_history"]
    assert [h["source_package"] for h in history] == ["build-1"]
    assert (tmp_path / "state" / "private-profile-backups" / "build-1").is_dir()


def test_clearing_the_current_state_backs_it_up_first(tmp_path):
    state, _ = select_private_test_state(tmp_path, "build-1")
    (state / "notes.txt").write_text("old", encoding="utf-8")
    state, receipt = select_private_test_state(tmp_path, "build-1", reset_current=True)
    assert receipt["selection_mode"] == "EXPLICIT_RESET"
    assert not (state / "notes.txt").exists()
    backup = receipt["backup_root"]
    assert backup and (tmp_path / "state" / "private-profile-backups" / "build-1").is_dir()
    assert open(os.path.join(backup, "notes.txt"), encoding="utf-8").read() == "old"
    assert _identity(state)["migration_revision"] == 1


@pytest.mark.parametrize("changes, code", [
    ({"channel": "RELEASE"}, "PRIVATE_TEST_PROFILE_IDENTITY_INVALID"),
    ({"migration_history": None}, "PRIVATE_TEST_PROFILE_IDENTITY_INVALID"),
    ({"profile_instance_id": "not a uuid"}, "PRIVATE_TEST_PROFILE_INSTANCE_ID_INVALID"),
    ({"data_schema_version": 0}, "PRIVATE_TEST_PROFILE_SCHEMA_INVALID"),
    ({"data_schema_version": True}, "PRIVATE_TEST_PROFILE_SCHEMA_INVALID"),
    ({"data_schema_version": "1"}, "PRIVATE_TEST_PROFILE_SCHEMA_INVALID"),
    ({"data_schema_version": product_identity.PRIVATE_PROFILE_SCHEMA + 1},
     "PRIVATE_TEST_PROFILE_SCHEMA_NEWER_THAN_RUNTIME"),
    ({"created_by_package": "build-0"}, "PRIVATE_TEST_PROFILE_BUILD_OWNERSHIP_MISMATCH"),
    ({"migration_revision": "0"}, "PRIVATE_TEST_PROFILE_MIGRATION_HISTORY_INVALID"),
    ({"migration_history": {}}, "PRIVATE_TEST_PROFILE_MIGRATION_HISTORY_INVALID"),
])
@pytest.mark.parametrize("action", ["reopen", "migrate", "reset"])
def test_a_state_whose_identity_does_not_hold_is_left_alone(tmp_path, changes, code, action):
    state, _ = select_private_test_state(tmp_path, "build-1")
    _edit_identity(state, **changes)
    before = (state / "profile" / "profile_identity.json").read_bytes()
    call = {"reopen": lambda: select_private_test_state(tmp_path, "build-1"),
            "migrate": lambda: select_private_test_state(tmp_path, "build-2", migrate_from="build-1"),
            "reset": lambda: select_private_test_state(tmp_path, "build-1", reset_current=True)}[action]
    with pytest.raises(ValueError, match=code):
        call()
    assert (state / "profile" / "profile_identity.json").read_bytes() == before
    assert not (tmp_path / "state" / "private-profile-backups").exists()
    assert not (tmp_path / "state" / "private-profiles" / "build-2").exists()


def test_an_older_schema_asks_for_a_migration(tmp_path, monkeypatch):
    state, _ = select_private_test_state(tmp_path, "build-1")
    monkeypatch.setattr(product_identity, "PRIVATE_PROFILE_SCHEMA", 2)
    with pytest.raises(ValueError, match="PRIVATE_TEST_PROFILE_SCHEMA_MIGRATION_REQUIRED"):
        select_private_test_state(tmp_path, "build-1")


def test_a_state_without_an_identity_file_is_not_taken_over(tmp_path):
    (tmp_path / "state" / "private-profiles" / "build-1").mkdir(parents=True)
    with pytest.raises(ValueError, match="PRIVATE_TEST_PROFILE_IDENTITY_MISSING"):
        select_private_test_state(tmp_path, "build-2", migrate_from="build-1")


def test_a_migration_needs_another_package_and_a_free_target(tmp_path):
    select_private_test_state(tmp_path, "build-1")
    select_private_test_state(tmp_path, "build-2")
    with pytest.raises(ValueError, match="PRIVATE_TEST_PROFILE_MIGRATION_SELF_FORBIDDEN"):
        select_private_test_state(tmp_path, "build-1", migrate_from="build-1")
    with pytest.raises(FileExistsError, match="PRIVATE_TEST_PROFILE_MIGRATION_TARGET_EXISTS"):
        select_private_test_state(tmp_path, "build-2", migrate_from="build-1")


@pytest.mark.parametrize("package", ["", "../build", ".build", "build/1", "b" * 129])
def test_a_package_name_that_could_leave_the_folder_is_refused(tmp_path, package):
    with pytest.raises(ValueError, match="PRIVATE_TEST_PROFILE_PACKAGE_ID_INVALID"):
        select_private_test_state(tmp_path, package)


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows feature")
def test_a_state_holding_a_junction_is_not_copied(tmp_path):
    import _winapi
    state, _ = select_private_test_state(tmp_path, "build-1")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "other.txt").write_text("not part of the state", encoding="utf-8")
    _winapi.CreateJunction(str(outside), str(state / "linked"))
    with pytest.raises(ValueError, match="PRIVATE_TEST_PROFILE_REPARSE_POINT_BLOCKED"):
        select_private_test_state(tmp_path, "build-2", migrate_from="build-1")
    with pytest.raises(ValueError, match="PRIVATE_TEST_PROFILE_REPARSE_POINT_BLOCKED"):
        select_private_test_state(tmp_path, "build-1", reset_current=True)
    assert (state / "linked" / "other.txt").exists()
    assert not (tmp_path / "state" / "private-profile-backups").exists()


def test_the_identity_file_records_a_real_instance_id(tmp_path):
    state, receipt = select_private_test_state(tmp_path, "build-1")
    assert str(uuid.UUID(_identity(state)["profile_instance_id"])) == receipt["profile_instance_id"]
