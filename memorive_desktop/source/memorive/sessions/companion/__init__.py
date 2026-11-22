"""The browser extension that records web AI conversations (extension/), and its desktop side: the installed
browsers (browsers.py) and the official chat websites it records on (site_profiles.py).

desktop.py (BrowserCompanionDesktop) is what the desktop runs: preparing the extension, telling whether the
browser loaded it, and importing its recordings. ProductApi (memorive/desktop/api/) builds it; api/browser_companion.py
forwards the window's calls to it; the session controller and archive (memorive/sessions/) use site_profiles.py.
"""

from pathlib import Path


def extension_root() -> Path:
    return (Path(__file__).resolve().parent / "extension").resolve()


__all__ = []

# Names the package offered before; restored with the code behind them.
__all__ = [*__all__, 'extension_root']
