"""Where the runtime log is written. The log is operational data: it is never embedded or searched."""
from __future__ import annotations

import os
from pathlib import Path

from memorive.platform.data_folders import ops_root

LOG_FILENAME = "log.txt"
ENV_LOG_DIR = "MEMORIVE_RUNTIME_LOG_LOG_DIR"


def get_log_path() -> Path:
    """The log.txt in use: in MEMORIVE_RUNTIME_LOG_LOG_DIR, or in the ops area's runtime_log folder."""
    configured = os.environ.get(ENV_LOG_DIR)
    return (Path(configured) if configured else ops_root() / "runtime_log") / LOG_FILENAME
