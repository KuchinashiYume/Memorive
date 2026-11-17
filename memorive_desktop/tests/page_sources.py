"""Where tests read the main window page's sources.

tools/build_pages.py builds product/desktop/bundle_integrated.html from the files under memorive_desktop/ui/.
Tests that look at the page's code read those files through this module, so they follow the page that is
actually built and shipped.
"""
from pathlib import Path

INTEGRATED = Path(__file__).resolve().parents[1] / "ui" / "integrated"


def settings_scripts() -> str:
    """Every script of the settings page, joined in file-name order."""
    folder = INTEGRATED / "routes" / "settings"
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(folder.glob("*.js")))


def page_source(relative: str) -> str:
    """One source file of the main window page, for example "routes/leaderboard/view.js"."""
    return (INTEGRATED / relative).read_text(encoding="utf-8")
