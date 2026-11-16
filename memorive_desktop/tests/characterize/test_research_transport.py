"""Public literature metadata requests: what MetadataClient.fetch sends, returns and records.

Pins today's behaviour of memorive.research.discovery.transport before the planned removal of
the source hashes it writes into every request record (the hash of transport.py and of
runtime.py). A fake transport stands in for the network and answers with the synthetic
Crossref and arXiv pages the module tests already use. Recorded per case: the request the
client sends, the result, and which fields the request records on disk carry. Times,
latencies and 64-character hex values are masked.

BoundedMetadataTransport.request is only exercised on requests it refuses before any
network access.
"""
import json
import re
import threading
from pathlib import Path

from memorive.research.discovery import transport
from memorive.research.common import read
from memorive.desktop.settings.metadata_transport import MetadataResponse
from tests.characterize.golden import check, outcome

HERE = Path(__file__).parent
FIXTURES = HERE.parent / "modules" / "literature_discovery" / "fixtures" / "source_connectors"
_HEX = re.compile(r"\b[0-9a-fA-F]{64}\b")
_VOLATILE = {"recorded_at", "started_at", "finished_at", "captured_at", "latency_ms", "stored_epoch"}


def _mask(value):
    if isinstance(value, dict):
        return {k: ("<volatile>" if k in _VOLATILE else _mask(v)) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_mask(v) for v in value]
    if isinstance(value, str):
        return _HEX.sub("<sha256>", value)
    return value


class _Fake:
    def __init__(self, status=200, body=b"", error=None):
        self.status, self.body, self.error, self.requests = status, body, error, []

    def request(self, **kwargs):
        self.requests.append({k: v for k, v in kwargs.items() if k != "headers"} | {"header_names": sorted(kwargs["headers"])})
        if self.error:
            raise self.error
        return MetadataResponse(self.status, self.body, {})


def _fetch(tmp_path, name, fake, provider, topic, count, **kwargs):
    root = tmp_path / name
    transport._last.clear()  # no 3-second pause between providers' calls in a test
    client = transport.MetadataClient(root, lambda: {"proxy_mode": "NONE"}, transport=fake)
    result = outcome(client.fetch, provider, topic, count, "run-1", "call-1", threading.Event(), **kwargs)
    records = {}
    requests = root / "runs" / "run-1" / "requests"
    if requests.is_dir():
        for path in sorted(requests.iterdir()):
            if path.suffix == ".json":
                value = read(path)
                records[path.name] = {"fields": sorted(value), "stage": value.get("stage"), "status": value.get("status"),
                                      "error_code": value.get("error_code"), "http_status": value.get("http_status")}
            else:
                records[path.name] = f"{path.stat().st_size} bytes"
    return _mask({"result": result, "sent": fake.requests, "records": records})


def test_fetch(tmp_path, monkeypatch):
    crossref = (FIXTURES / "crossref_page_1.json").read_bytes()
    arxiv = (FIXTURES / "arxiv_page_1.xml").read_bytes()
    wos = json.dumps({"metadata": {"total": 1, "page": 1, "limit": 3}, "hits": [{
        "uid": "WOS:SYNTHETIC", "title": "Original English title", "names": {"authors": [{"displayName": "Test Author"}]},
        "identifiers": {"doi": "10.9999/synthetic"}, "source": {"publishYear": 2026},
        "links": {"record": "https://www.webofscience.com/wos/woscc/full-record/WOS:SYNTHETIC"}}]}).encode()
    window = {"from": "2026-01-01", "until": "2026-06-30"}
    monkeypatch.delenv("WOS_API_KEY", raising=False)
    cases = {
        "crossref_relevance": _fetch(tmp_path, "c1", _Fake(body=crossref), "CROSSREF", "auditable discovery", 2),
        "crossref_latest": _fetch(tmp_path, "c2", _Fake(body=crossref), "CROSSREF",
                                  {"text": "discovery", "objective": "latest", "publication_window": window}, 2, cursor=4),
        "crossref_exact_doi": _fetch(tmp_path, "c3", _Fake(body=crossref), "CROSSREF", "x", 1, exact_doi="10.99999/memorive.synthetic.001"),
        "crossref_bad_doi": _fetch(tmp_path, "c4", _Fake(body=crossref), "CROSSREF", "x", 1, exact_doi="not-a-doi"),
        "crossref_http_429": _fetch(tmp_path, "c5", _Fake(status=429, body=b"{}"), "CROSSREF", "x", 2),
        "crossref_not_json": _fetch(tmp_path, "c6", _Fake(body=b"<html>"), "CROSSREF", "x", 2),
        "crossref_transport_error": _fetch(tmp_path, "c7", _Fake(error=ValueError("EXTERNAL_METADATA_TIMEOUT")), "CROSSREF", "x", 2),
        "crossref_too_many_records": _fetch(tmp_path, "c8", _Fake(body=crossref), "CROSSREF", "x", 1),
        "arxiv_relevance": _fetch(tmp_path, "a1", _Fake(body=arxiv), "ARXIV", "auditable discovery", 3),
        "arxiv_latest_window": _fetch(tmp_path, "a2", _Fake(body=arxiv), "ARXIV",
                                      {"text": "discovery", "objective": "latest", "publication_window": window}, 3),
        "arxiv_exact_id": _fetch(tmp_path, "a3", _Fake(body=arxiv), "ARXIV", "x", 3, exact_arxiv="2608.00001v2"),
        "arxiv_bad_id": _fetch(tmp_path, "a4", _Fake(body=arxiv), "ARXIV", "x", 3, exact_arxiv="../etc"),
        "wos_without_key": _fetch(tmp_path, "w1", _Fake(body=wos), "WEB-OF-SCIENCE", "x", 3),
        "unknown_provider": _fetch(tmp_path, "u1", _Fake(), "PUBMED", "x", 3),
        "page_size_zero": _fetch(tmp_path, "p0", _Fake(), "CROSSREF", "x", 0),
        "page_size_over_limit": _fetch(tmp_path, "p1", _Fake(), "ARXIV", "x", 2001),
    }
    monkeypatch.setenv("WOS_API_KEY", "synthetic-key-123")
    cases["wos_with_key"] = _fetch(tmp_path, "w2", _Fake(body=wos), "WEB-OF-SCIENCE", "x", 3)
    monkeypatch.setenv("WOS_API_KEY", "bad\nkey")
    cases["wos_key_with_newline"] = _fetch(tmp_path, "w3", _Fake(body=wos), "WEB-OF-SCIENCE", "x", 3)
    # The same request again within a day is answered from the cache.
    root_fake = _Fake(body=crossref)
    _fetch(tmp_path, "cache", root_fake, "CROSSREF", "cached topic", 2)
    cases["crossref_cached_second_call"] = _fetch(tmp_path, "cache", _Fake(body=b"never read"), "CROSSREF", "cached topic", 2)
    check("research_transport_fetch", cases, HERE)


def test_bounded_transport_refusals():
    base = dict(method="GET", body=None, timeout_seconds=30, proxy_mode="NONE", proxy_address="", max_response_bytes=1000)
    headers = {"Accept": "application/json", "User-Agent": "Memorive"}
    variants = {
        "http_scheme": dict(url="http://api.crossref.org/works?q=x", headers=headers),
        "other_host": dict(url="https://example.org/works", headers=headers),
        "other_path": dict(url="https://api.crossref.org/members", headers=headers),
        "openalex_key_in_query": dict(url="https://api.openalex.org/works?api_key=abc", headers=headers),
        "openalex_fragment": dict(url="https://api.openalex.org/works#x", headers=headers),
        "post": dict(url="https://api.crossref.org/works", headers=headers, method="POST"),
        "body": dict(url="https://api.crossref.org/works", headers=headers, body=b"x"),
        "extra_header": dict(url="https://api.crossref.org/works", headers=dict(headers, Cookie="a")),
        "missing_header": dict(url="https://api.crossref.org/works", headers={"Accept": "application/json"}),
        "clarivate_without_key": dict(url="https://api.clarivate.com/apis/wos-starter/v1/documents", headers=headers),
        "header_newline": dict(url="https://api.crossref.org/works", headers=dict(headers, Accept="a\nb")),
    }
    cases = {name: outcome(transport.BoundedMetadataTransport().request, **(base | changes)) for name, changes in variants.items()}
    check("research_transport_refusals", cases, HERE)
