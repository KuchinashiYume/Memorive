"""Browser companion for capturing chat sessions: finds installed browsers (browsers.py), knows the
page layout of supported chat sites (site_profiles.py), and keeps the position up to which a
conversation was archived (incremental_cursor.py).

The sessions page and memorive_session_archive use it.
"""

from pathlib import Path


def extension_root() -> Path:
    return (Path(__file__).resolve().parent / "extension").resolve()


__all__ = []

# Names the package offered before; restored with the code behind them.
__all__ = [*__all__, 'extension_root']
