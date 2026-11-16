"""The receipt of an official record validator describes the code that actually runs
(memorive.research.reports.adapters._official_callable).

The validator is used only when every module its receipt lists is loaded from this source tree; a
module of the same name loaded first from another folder is refused, leaves no receipt and is not
called. A module loaded again gets a new receipt; one swapped in after a valid receipt is refused.
The synthetic modules live in a temporary folder; the module table and the receipt cache are
restored after each test.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

from memorive.research.reports import adapters
from memorive.research.reports.errors import SourceRecordError

SOURCE = Path(adapters.__file__).resolve().parents[3]
FOREIGN = 'def validate_record(record):\n    return "foreign"\n'


@pytest.fixture(autouse=True)
def isolated_modules():
    saved = {name: module for name, module in sys.modules.items() if name.startswith("run_ledger")}
    receipts = dict(adapters.OFFICIAL_VALIDATOR_RECEIPTS)
    cache = getattr(adapters, "_RECEIPT_MODULES", {})
    receipt_modules = dict(cache)
    adapters.OFFICIAL_VALIDATOR_RECEIPTS.clear()
    cache.clear()
    yield
    for name in [n for n in sys.modules if n.startswith("run_ledger")]:
        del sys.modules[name]
    sys.modules.update(saved)
    adapters.OFFICIAL_VALIDATOR_RECEIPTS.clear()
    adapters.OFFICIAL_VALIDATOR_RECEIPTS.update(receipts)
    cache.clear()
    cache.update(receipt_modules)


def _refusal(call):
    with pytest.raises(SourceRecordError) as caught:
        call()
    return caught.value.error_code


def _validator():
    return adapters._official_callable("run_ledger.schema", "validate_record")


def test_the_validator_of_this_tree_comes_with_a_receipt_of_its_file():
    validator = _validator()
    assert Path(sys.modules[validator.__module__].__file__).resolve() == (SOURCE / "run_ledger" / "schema.py").resolve()
    receipt = adapters.OFFICIAL_VALIDATOR_RECEIPTS["run_ledger.schema"]
    assert Path(receipt["module_file"]) == (SOURCE / "run_ledger" / "schema.py").resolve()
    assert receipt["files"][0]["sha256"].lower() == hashlib.sha256(
        (SOURCE / "run_ledger" / "schema.py").read_bytes()).hexdigest()


def test_a_same_named_module_from_another_folder_is_not_used(tmp_path):
    other = tmp_path / "other_copy" / "run_ledger"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("", encoding="utf-8")
    (other / "schema.py").write_text(FOREIGN, encoding="utf-8")
    for name in [n for n in sys.modules if n.startswith("run_ledger")]:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location("run_ledger", other / "__init__.py",
                                                  submodule_search_locations=[str(other)])
    package = importlib.util.module_from_spec(spec)
    sys.modules["run_ledger"] = package
    spec.loader.exec_module(package)
    importlib.import_module("run_ledger.schema")
    assert "OFFICIAL_VALIDATOR_SOURCE_MISMATCH" == _refusal(_validator)
    assert "run_ledger.schema" not in adapters.OFFICIAL_VALIDATOR_RECEIPTS


def test_a_module_loaded_again_gets_its_own_receipt():
    _validator()
    first = sys.modules["run_ledger.schema"]
    del sys.modules["run_ledger.schema"]
    second = importlib.import_module("run_ledger.schema")
    assert first is not second
    validator = _validator()
    assert validator.__globals__ is second.__dict__
    assert getattr(adapters, "_RECEIPT_MODULES", {}).get("run_ledger.schema", (None,))[0] is second


def test_a_module_swapped_in_after_a_valid_receipt_is_refused(tmp_path):
    _validator()
    stray = tmp_path / "schema.py"
    stray.write_text(FOREIGN, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("run_ledger.schema", stray)
    swapped = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(swapped)
    sys.modules["run_ledger.schema"] = swapped
    assert "OFFICIAL_VALIDATOR_SOURCE_MISMATCH" == _refusal(_validator)


def test_a_validator_imported_from_elsewhere_into_the_module_is_refused(monkeypatch):
    module = importlib.import_module("run_ledger.schema")
    monkeypatch.setattr(module, "validate_record", lambda record: "borrowed")
    assert "OFFICIAL_VALIDATOR_UNAVAILABLE" == _refusal(_validator)


def test_an_unregistered_module_is_refused():
    assert "OFFICIAL_VALIDATOR_UNAVAILABLE" == _refusal(lambda: adapters._official_callable("os.path", "join"))
