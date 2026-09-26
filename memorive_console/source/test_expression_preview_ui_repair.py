from __future__ import annotations

from pathlib import Path
import threading
import urllib.error
import urllib.request

from server import make_server


class AssetManager:
    def snapshot(self):
        return {"schema_version": "Memorive-IndependentConsoleSnapshot-v1"}

    def expression_asset(self, name):
        if name != "neutral":
            raise ValueError("EXPRESSION_ASSET_NOT_ALLOWED")
        return b'<svg xmlns="http://www.w3.org/2000/svg"/>', "image/svg+xml"


def test_browser_image_request_can_read_allowlisted_expression_asset_without_api_header():
    server = make_server(AssetManager(), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/api/expression-assets/neutral.svg", timeout=5) as response:
            assert response.status == 200
            assert response.headers.get_content_type() == "image/svg+xml"
            assert response.read().startswith(b"<svg")

        with urllib.request.urlopen(
            urllib.request.Request(base + "/api/snapshot", headers={"X-Memorive-Console": "1"}),
            timeout=5,
        ) as response:
            assert response.status == 200

        try:
            urllib.request.urlopen(base + "/api/snapshot", timeout=5)
        except urllib.error.HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError("generic API route accepted a request without the console header")

        try:
            urllib.request.urlopen(base + "/api/expression-assets/missing.svg", timeout=5)
        except urllib.error.HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("unknown expression asset was served")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_author_workspace_uses_separate_views_and_image_failure_fallback():
    root = Path(__file__).parent
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")
    expected = {"overview", "expressions", "suite", "manual", "records", "contract"}
    assert all(f'data-view="{name}"' in html for name in expected)
    assert all(f'data-view-panel="{name}"' in html for name in expected)
    assert "document.querySelectorAll('.view')" in script
    assert "expression-image" in html
    assert "addEventListener('load'" in script
    assert "addEventListener('error'" in script
    assert "素材加载失败" in script
