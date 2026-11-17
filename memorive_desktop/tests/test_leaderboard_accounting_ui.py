from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "product" / "web"
PAGES = [*(WEB / f"part{index}.html" for index in range(3, 10)), WEB / "leaderboard.html"]


def test_full_leaderboard_route_is_present_without_legacy_mini_card() -> None:
    html = (WEB / "leaderboard.html").read_text(encoding="utf-8")
    script = (WEB / "leaderboard.js").read_text(encoding="utf-8")
    runtime = "\n".join(path.read_text(encoding="utf-8") for path in sorted((ROOT / "source" / "memorive" / "desktop" / "api").glob("*.py")))
    assert 'class="lb-table"' in html
    assert 'id="lb-rows"' in html
    assert 'id="lb-model-detail"' in html
    assert 'leaderboard_currency.js' in html
    assert "settings.leaderboard_get" in script
    assert "settings.leaderboard_save" in script
    assert "settings.leaderboard_refresh" in script
    assert '"leaderboard"' in runtime


