"""Where the runtime log is written, and where components keep their data when nothing has told them.

The desktop service binds every data folder to its profile when it starts, through the MEMORIVE_*
variables (memorive_workflow/executor.py, CorePipelineExecutor._bind_environment). A process or
component that runs without that binding (the window process, a command-line tool, a component
called on its own) uses default_runtime_root() with the same folder names. The log is operational
data: it is never embedded or searched.
"""
from __future__ import annotations

import os
from pathlib import Path

LOG_FILENAME = "log.txt"
ENV_LOG_DIR = "MEMORIVE_RUNTIME_LOG_LOG_DIR"
ENV_OPS_ROOT = "MEMORIVE_OPS_ROOT"


def default_runtime_root() -> Path:
    """<LOCALAPPDATA>/Memorive/runtime, or ~/.memorive/runtime where LOCALAPPDATA is not set.

    The installed and the portable app point LOCALAPPDATA into their own data folder at start-up
    (product/desktop/product_identity.py), so for them this root lies inside that folder."""
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) / "Memorive" if local else Path.home() / ".memorive") / "runtime"


def ops_root() -> Path:
    """The ops area that holds the log, the model cache, review reports and ledgers, each in its own
    folder: MEMORIVE_OPS_ROOT, or ops under default_runtime_root()."""
    root = os.environ.get(ENV_OPS_ROOT)
    return Path(root) if root else default_runtime_root() / "ops"


def get_log_path() -> Path:
    """The log.txt in use: in MEMORIVE_RUNTIME_LOG_LOG_DIR, or in the ops area's runtime_log folder."""
    configured = os.environ.get(ENV_LOG_DIR)
    return (Path(configured) if configured else ops_root() / "runtime_log") / LOG_FILENAME
