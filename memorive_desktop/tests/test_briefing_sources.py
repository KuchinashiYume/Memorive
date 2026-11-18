"""Reading the public feeds of the AI briefing (memorive/research/briefing/sources.py).

PublicSources._fetch reads one feed or article over HTTP; normalize turns a feed into items. A local
HTTP server stands in for the public site. In a real run on 2026-11-18 DeepMind's feed came back
gzip-compressed although the request asked for no compression, and the source was reported as failed.
"""
from __future__ import annotations

import gzip
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from memorive.research.briefing import sources
from memorive.research.briefing.sources import PublicSources, normalize

FEED = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Google DeepMind News</title>
<item><title>A made-up model release</title><link>https://deepmind.google/blog/a-made-up-model-release/</link>
<pubDate>Tue, 17 Nov 2026 10:00:00 GMT</pubDate><description>Two sentences of a made-up summary.</description></item>
</channel></rss>"""


@pytest.fixture
def site():
    """A local server answering every path with the body and headers set on it."""
    class Handler(BaseHTTPRequestHandler):
        body, headers = b"", {}

        def do_GET(self):
            self.send_response(200)
            for name, value in Handler.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(Handler.body)))
            self.end_headers()
            self.wfile.write(Handler.body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Handler, f"http://127.0.0.1:{server.server_address[1]}/blog/rss.xml"
    server.shutdown()
    server.server_close()


def test_a_gzip_compressed_feed_is_read(site, tmp_path):
    handler, url = site
    handler.body, handler.headers = gzip.compress(FEED), {"Content-Type": "application/xml", "Content-Encoding": "gzip"}
    body = PublicSources(tmp_path)._fetch(url, tmp_path, lambda: None)
    assert body == FEED
    [item] = normalize("deepmind_news", body, "2026-11-18T00:00:00+00:00")
    assert (item["title"], item["published_at"]) == ("A made-up model release", "2026-11-17T10:00:00+00:00")


def test_a_plain_feed_is_read_as_it_comes(site, tmp_path):
    handler, url = site
    handler.body, handler.headers = FEED, {"Content-Type": "application/xml"}
    assert PublicSources(tmp_path)._fetch(url, tmp_path, lambda: None) == FEED


def test_the_size_limit_holds_for_the_inflated_feed(site, tmp_path, monkeypatch):
    handler, url = site
    monkeypatch.setattr(sources, "BODY_LIMIT", 1000)
    handler.body, handler.headers = gzip.compress(b" " * 5000), {"Content-Encoding": "gzip"}
    with pytest.raises(ValueError, match="AI_SOURCE_BODY_LIMIT"):
        PublicSources(tmp_path)._fetch(url, tmp_path, lambda: None)
