from __future__ import annotations

from pathlib import Path
from page_sources import page_source


ROOT = Path(__file__).resolve().parents[1]


def test_full_leaderboard_route_is_present_without_legacy_mini_card() -> None:
    html = page_source("routes/leaderboard/markup.html")
    script = page_source("routes/leaderboard/view.js")
    runtime = "\n".join(path.read_text(encoding="utf-8") for path in sorted((ROOT / "source" / "memorive" / "desktop" / "api").glob("*.py")))
    assert 'class="lb-table"' in html
    assert 'id="lb-rows"' in html
    assert 'id="lb-model-detail"' in html
    assert "<!-- include: routes/leaderboard/currency.js -->" in page_source("page.html")
    assert "settings.leaderboard_get" in script
    assert "settings.leaderboard_save" in script
    assert "settings.leaderboard_refresh" in script
    assert '"leaderboard"' in runtime


