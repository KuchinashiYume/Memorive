"""Offline update maintenance (`Memorive.exe --memo-update-maintenance`).

The installer starts the new program with a request file; the program backs up, migrates,
verifies or restores the user's data. Before touching anything it checks that the request
belongs to this installation, that the data folder is the bound one and does not overlap the
program, and that the running program is exactly the new version named in the transaction.
`main()` is not called here: it installs a process-wide audit hook that forbids subprocesses.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

# verify() and migrate() import these; importing them first, while the program does not look
# packaged, keeps their module-level paths pointing at the source tree for later tests.
import memorive_folder_management  # noqa: F401
import memorive_research_workspace.store  # noqa: F401
import memorive_settings.store  # noqa: F401
import product_identity  # noqa: F401
import update_maintenance as maintenance

RESOURCES = ("bundle_integrated.html", "bundle_assistant.html", "release_identity_binding.json",
             "source/facade_method_binding.json")


def _json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf8")


class Install:
    def __init__(self, base: Path, monkeypatch):
        self.monkeypatch = monkeypatch
        self.root = base / "Memorive"
        self.data = base / "data"
        self.profile = self.data / "state"
        self.version = self.root / "versions" / "1.04"
        self.exe = self.version / "app" / "Memorive.exe"
        self.operation = self.root / "updates" / "op-1"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(b"new program")
        self.profile.mkdir(parents=True)
        _json(self.root / "install-owner.json", {"owner": "Memorive-INSTALLER-TRIAL-V1", "root": str(self.root)})
        _json(self.root / "updates" / "binding.json", {"state_root": str(self.profile), "data_root": str(self.data)})
        self.transaction = {"schema": "MemoriveUpdateTransaction-v1", "operation_id": "op-1",
                            "new_version": str(self.version)}
        self.request = {"schema": "MemoriveUpdateMaintenance-v1", "operation_id": "op-1",
                        "program_root": str(self.root), "state_root": str(self.profile),
                        "data_root": str(self.data), "target_executable": str(self.exe),
                        "target_exe_sha256": hashlib.sha256(b"new program").hexdigest().upper()}
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(self.exe))

    @property
    def request_path(self) -> Path:
        return self.operation / "maintenance-request.json"

    def write(self) -> Path:
        _json(self.operation / "transaction.json", self.transaction)
        _json(self.request_path, self.request)
        return self.request_path


@pytest.fixture
def install(tmp_path, monkeypatch):
    return Install(tmp_path, monkeypatch)


def _refused(path: Path) -> str:
    with pytest.raises(ValueError) as caught:
        maintenance.validate_request(path)
    return str(caught.value)


def test_a_request_from_this_installation_is_accepted(install):
    request, root, profile, operation = maintenance.validate_request(install.write())
    assert (root, profile, operation) == (install.root.resolve(), install.profile.resolve(),
                                          install.operation.resolve())
    assert request["operation_id"] == "op-1"


@pytest.mark.parametrize("change, code", [
    (lambda i: i.request.update(schema="MemoriveUpdateMaintenance-v0"), "UPDATE_MAINTENANCE_REQUEST_INVALID"),
    (lambda i: i.request.update(operation_id=""), "UPDATE_OPERATION_INVALID"),
    (lambda i: i.request.update(operation_id="../op-1"), "UPDATE_OPERATION_INVALID"),
    (lambda i: i.request.update(operation_id=".op"), "UPDATE_OPERATION_INVALID"),
    (lambda i: i.request.update(operation_id="op-2"), "UPDATE_MAINTENANCE_SCOPE_INVALID"),
    (lambda i: i.request.update(program_root=str(i.root.parent)), "UPDATE_MAINTENANCE_SCOPE_INVALID"),
    (lambda i: _json(i.root / "install-owner.json", {"owner": "someone else", "root": str(i.root)}),
     "UPDATE_MAINTENANCE_OWNER_INVALID"),
    (lambda i: _json(i.root / "install-owner.json", {"owner": "Memorive-INSTALLER-TRIAL-V1",
                                                    "root": str(i.root.parent)}), "UPDATE_MAINTENANCE_OWNER_INVALID"),
    (lambda i: i.request.update(state_root=str(i.data)), "UPDATE_MAINTENANCE_DATA_SCOPE_INVALID"),
    (lambda i: i.request.update(state_root=str(i.root.parent / "elsewhere")), "UPDATE_MAINTENANCE_DATA_SCOPE_INVALID"),
    (lambda i: i.request.update(data_root=str(i.root), state_root=str(i.root / "state")),
     "UPDATE_PROGRAM_DATA_OVERLAP"),
    (lambda i: _json(i.root / "updates" / "binding.json", {"state_root": str(i.data / "other"),
                                                         "data_root": str(i.data)}),
     "UPDATE_MAINTENANCE_BINDING_MISMATCH"),
    (lambda i: _json(i.root / "updates" / "binding.json", {"state_root": str(i.profile),
                                                         "data_root": str(i.root.parent)}),
     "UPDATE_MAINTENANCE_BINDING_MISMATCH"),
    (lambda i: i.transaction.update(schema="other"), "UPDATE_MAINTENANCE_TRANSACTION_MISMATCH"),
    (lambda i: i.transaction.update(operation_id="op-2"), "UPDATE_MAINTENANCE_TRANSACTION_MISMATCH"),
    (lambda i: (i.transaction.update(new_version=str(i.root / "1.04")),
                i.request.update(target_executable=str(i.root / "1.04" / "app" / "Memorive.exe"))),
     "UPDATE_MAINTENANCE_TRANSACTION_MISMATCH"),
    (lambda i: i.request.update(target_executable=str(i.version / "Memorive.exe")),
     "UPDATE_MAINTENANCE_TRANSACTION_MISMATCH"),
    (lambda i: i.monkeypatch.setattr(sys, "frozen", False), "UPDATE_MAINTENANCE_EXE_MISMATCH"),
    (lambda i: i.monkeypatch.setattr(sys, "executable", str(i.root / "Memorive.exe")),
     "UPDATE_MAINTENANCE_EXE_MISMATCH"),
    (lambda i: i.request.update(target_exe_sha256="0" * 64), "UPDATE_MAINTENANCE_EXE_MISMATCH"),
])
def test_a_request_that_does_not_belong_to_this_installation_is_refused(install, change, code):
    change(install)
    assert _refused(install.write()) == code


def test_a_request_with_a_different_file_name_is_refused(install):
    install.write().rename(install.operation / "request.json")
    assert _refused(install.operation / "request.json") == "UPDATE_MAINTENANCE_SCOPE_INVALID"


def test_a_program_folder_inside_the_data_folder_is_refused(tmp_path, monkeypatch):
    install = Install(tmp_path / "data" / "state" / "inner", monkeypatch)
    install.request.update(data_root=str(tmp_path / "data"), state_root=str(tmp_path / "data" / "state"))
    _json(install.root / "updates" / "binding.json", {"state_root": str(tmp_path / "data" / "state"),
                                                      "data_root": str(tmp_path / "data")})
    assert _refused(install.write()) == "UPDATE_PROGRAM_DATA_OVERLAP"


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows feature")
def test_a_state_folder_reached_through_a_junction_is_refused(install):
    import _winapi
    real = install.data / "real"
    real.mkdir()
    link = install.data / "linked"
    _winapi.CreateJunction(str(real), str(link))
    install.request.update(state_root=str(link))
    _json(install.root / "updates" / "binding.json", {"state_root": str(link), "data_root": str(install.data)})
    assert _refused(install.write()) == "UPDATE_DATA_REPARSE_FORBIDDEN"


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows feature")
def test_backup_refuses_a_junction_inside_the_data(install):
    import _winapi
    outside = install.root.parent / "outside"
    outside.mkdir()
    (outside / "other.txt").write_text("not part of the profile", encoding="utf8")
    _winapi.CreateJunction(str(outside), str(install.profile / "linked"))
    install.operation.mkdir(parents=True)
    with pytest.raises(ValueError, match="UPDATE_DATA_REPARSE_FORBIDDEN"):
        maintenance.backup(install.profile, install.operation)


def _database(path: Path, rows: int) -> None:
    with closing(sqlite3.connect(path)) as db:
        db.execute("create table if not exists t (x)")
        db.executemany("insert into t values (?)", [(n,) for n in range(rows)])
        db.commit()


@pytest.fixture
def migrated(install, tmp_path, monkeypatch):
    """Backed up and migrated, with the packaged resources in place."""
    _database(install.profile / "store.sqlite3", 3)
    (install.profile / "notes.txt").write_text("user notes", encoding="utf8")
    install.operation.mkdir(parents=True)
    _json(install.operation / "transaction.json", install.transaction)
    maintenance.backup(install.profile, install.operation)
    maintenance.migrate(install.profile, install.operation)
    bundle = tmp_path / "bundle"
    for name in RESOURCES:
        (bundle / name).parent.mkdir(parents=True, exist_ok=True)
        (bundle / name).write_text("{}", encoding="utf8")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    return install, bundle


def test_backup_migrate_and_verify_leave_the_data_as_migrated(migrated):
    install, _ = migrated
    backup = json.loads((install.operation / "data-backup.json").read_text(encoding="utf8"))
    assert backup["consistent_sqlite"] == ["store.sqlite3"]
    assert set(backup["files"]) == {"store.sqlite3", "notes.txt"}
    result = maintenance.verify(install.profile, install.operation)
    assert (result["status"], result["writable_data_untouched"], result["network_calls"]) == ("PASS", True, 0)


def test_verify_refuses_data_written_after_the_migration(migrated):
    install, _ = migrated
    (install.profile / "notes.txt").write_text("written by the new version", encoding="utf8")
    with pytest.raises(ValueError, match="UPDATE_DATA_CHANGED_AFTER_MIGRATION"):
        maintenance.verify(install.profile, install.operation)


@pytest.mark.parametrize("missing", RESOURCES)
def test_verify_refuses_a_program_without_its_resources(migrated, missing):
    install, bundle = migrated
    (bundle / missing).unlink()
    with pytest.raises(ValueError, match="UPDATE_RUNTIME_RESOURCE_MISSING"):
        maintenance.verify(install.profile, install.operation)


def test_verify_notices_data_changed_while_it_checks(migrated, monkeypatch):
    install, _ = migrated
    real = maintenance.inventory
    calls = []

    def inventory(root):
        result = real(root)
        if root == install.profile and not calls:
            calls.append(root)
            (install.profile / "late.txt").write_text("x", encoding="utf8")
        return result

    monkeypatch.setattr(maintenance, "inventory", inventory)
    with pytest.raises(ValueError, match="UPDATE_VERIFY_MUTATED_DATA"):
        maintenance.verify(install.profile, install.operation)


def test_restore_brings_back_the_backup_and_keeps_the_migrated_copy(migrated):
    install, _ = migrated
    result = maintenance.restore(install.profile, install.operation)
    assert result["status"] == "RESTORED_BEFORE_WRITES"
    backup = json.loads((install.operation / "data-backup.json").read_text(encoding="utf8"))
    assert maintenance.inventory(install.profile) == backup["files"]
    assert Path(result["retained"]).is_dir()
    assert maintenance.restore(install.profile, install.operation)["status"] == "RESTORED_BEFORE_WRITES"


@pytest.mark.parametrize("opened", [
    lambda i: _json(i.operation / "writes-opened.json", {}),
    lambda i: _json(i.operation / "transaction.json", dict(i.transaction, writes_opened=True)),
    lambda i: (i.profile / "notes.txt").write_text("written by the new version", encoding="utf8"),
])
def test_restore_refuses_once_the_new_version_may_have_written(migrated, opened):
    install, _ = migrated
    opened(install)
    with pytest.raises(ValueError, match="UPDATE_NEW_WRITES_BLOCK_RECOVERY"):
        maintenance.restore(install.profile, install.operation)
    assert (install.profile / "notes.txt").exists()


def test_restore_refuses_a_changed_backup(migrated):
    install, _ = migrated
    (install.operation / "data-backup" / "notes.txt").write_text("edited", encoding="utf8")
    with pytest.raises(ValueError, match="UPDATE_BACKUP_HASH_MISMATCH"):
        maintenance.restore(install.profile, install.operation)


def test_migrate_needs_a_backup_and_backup_runs_once(install):
    install.operation.mkdir(parents=True)
    with pytest.raises(ValueError, match="UPDATE_BACKUP_REQUIRED"):
        maintenance.migrate(install.profile, install.operation)
    maintenance.backup(install.profile, install.operation)
    with pytest.raises(ValueError, match="UPDATE_BACKUP_ALREADY_EXISTS"):
        maintenance.backup(install.profile, install.operation)
