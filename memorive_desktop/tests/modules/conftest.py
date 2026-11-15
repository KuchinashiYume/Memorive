"""Settings shared by the per-package tests.

Some of them refuse to run unless an explicit scratch folder is named in the
environment; point those at the (already sandboxed) temp folder. Local development
material kept in _dev_docs, when present, is not collected.
"""
import os
import tempfile
from pathlib import Path

collect_ignore = ["_dev_docs"]

_SCRATCH = Path(tempfile.gettempdir()) / "memorive-module-tests"
for _name in ("MEMORIVE_REGISTRY_TEST_TEMP_ROOT", "MEMORIVE_MIGRATION_TEST_TEMP_ROOT"):
    _path = _SCRATCH / _name
    _path.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault(_name, str(_path))
