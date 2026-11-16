"""Paths that work past the Windows MAX_PATH limit.

windows_io_path gives the extended-length (long path) form of a resolved path on Windows and the
resolved path elsewhere. Document titles end up in folder and file names, so a path under a normal
profile can exceed MAX_PATH; file I/O on such paths goes through this. Used across the app: the
folder policy, the inbox, research, sessions, settings and the desktop service.
"""
import os
from pathlib import Path


def windows_io_path(path: Path | str) -> Path:
    """Use Windows' extended namespace without changing the physical location.

    Literature titles occur in both folder and artifact names. Their paths can
    exceed MAX_PATH even under a normal user profile. Do not require a machine
    registry change or shorten a user's document identity to make I/O work.
    Resolve first so dot components and relative paths retain normal semantics.
    """
    resolved = Path(path).resolve(strict=False)
    if os.name != "nt":
        return resolved
    prefix = os.sep * 2 + "?" + os.sep
    rendered = str(resolved)
    if rendered.startswith(prefix):
        return resolved
    if rendered.startswith(os.sep * 2):
        return Path(prefix + "UNC" + os.sep + rendered[2:])
    return Path(prefix + rendered)
