"""Where components keep their data when nothing has bound their folders (runtime_log/config.py).

The desktop service binds every folder through the MEMORIVE_* variables. Without them the defaults
lie under <LOCALAPPDATA>/Memorive/runtime, with the ops area in its ops folder, and never on a fixed
drive. A component's own variable still wins over the ops area.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from evidence_review import judgment_debt, report
from knowledge_admission import knowledge_admission_log
from model_gateway.cache import CacheLayer
from runtime_log import config

OPS_FOLDERS = {
    "MEMORIVE_RUNTIME_LOG_LOG_DIR": lambda: config.get_log_path().parent,
    "MEMORIVE_MODEL_GATEWAY_CACHE_DIR": lambda: CacheLayer().cache_dir,
    "MEMORIVE_EVIDENCE_REVIEW_REPORT_DIR": report._report_dir,
    "MEMORIVE_EVIDENCE_REVIEW_JUDGMENT_DEBT_DIR": lambda: judgment_debt._path().parent,
    "MEMORIVE_KNOWLEDGE_ADMISSION_LOG_DIR": knowledge_admission_log.log_dir,
}
# The same folder names the service binds (memorive_workflow/executor.py, _bind_environment).
DEFAULT_NAMES = {
    "MEMORIVE_RUNTIME_LOG_LOG_DIR": "runtime_log",
    "MEMORIVE_MODEL_GATEWAY_CACHE_DIR": "model_gateway_cache",
    "MEMORIVE_EVIDENCE_REVIEW_REPORT_DIR": "evidence_review_reports",
    "MEMORIVE_EVIDENCE_REVIEW_JUDGMENT_DEBT_DIR": "evidence_review_judgment_debt",
    "MEMORIVE_KNOWLEDGE_ADMISSION_LOG_DIR": "knowledge_admission",
}


@pytest.fixture
def unbound(monkeypatch, tmp_path):
    """No MEMORIVE_* folder variable set; LOCALAPPDATA is a folder of the test."""
    for name in (*OPS_FOLDERS, "MEMORIVE_OPS_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    return tmp_path


def test_the_runtime_root_lies_under_local_app_data(unbound):
    assert config.default_runtime_root() == unbound / "local" / "Memorive" / "runtime"
    assert config.ops_root() == unbound / "local" / "Memorive" / "runtime" / "ops"


def test_without_local_app_data_the_runtime_root_is_in_the_home_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert config.default_runtime_root() == tmp_path / ".memorive" / "runtime"


def test_unbound_components_use_the_ops_area_of_the_runtime_root(unbound):
    ops = unbound / "local" / "Memorive" / "runtime" / "ops"
    assert {name: folder() for name, folder in OPS_FOLDERS.items()} == {
        name: ops / DEFAULT_NAMES[name] for name in OPS_FOLDERS
    }
    assert config.get_log_path() == ops / "runtime_log" / "log.txt"


def test_the_ops_root_variable_moves_every_ops_folder(unbound, monkeypatch):
    monkeypatch.setenv("MEMORIVE_OPS_ROOT", str(unbound / "ops"))
    assert {name: folder() for name, folder in OPS_FOLDERS.items()} == {
        name: unbound / "ops" / DEFAULT_NAMES[name] for name in OPS_FOLDERS
    }


def test_a_components_own_variable_wins_over_the_ops_root(unbound, monkeypatch):
    monkeypatch.setenv("MEMORIVE_OPS_ROOT", str(unbound / "ops"))
    for name in OPS_FOLDERS:
        monkeypatch.setenv(name, str(unbound / "own" / name))
    assert {name: folder() for name, folder in OPS_FOLDERS.items()} == {
        name: unbound / "own" / name for name in OPS_FOLDERS
    }
