"""LiveBench results fetched through the GitHub API.

The first page names the commit of `main`; every later page is one file from that commit. A
file is used only when GitHub's answer is that exact file at that exact commit and its decoded
bytes match the size and git blob hash GitHub reports.
"""
from __future__ import annotations

import base64
import hashlib
import json

import pytest

from memorive.desktop.settings.external_sources import _github_commit, _github_content
from memorive.desktop.settings.metadata_transport import LIVEBENCH_GITHUB_API

COMMIT = "a" * 40
PATH = "results/2026-11-01/table.csv"
RAW = b"model,score\nexample,71.5\n"
ENCODED = base64.b64encode(RAW).decode()


def _blob(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False).hexdigest()


def _file(**changes) -> dict:
    value = {"type": "file", "path": PATH, "name": "table.csv", "size": len(RAW), "encoding": "base64",
             "content": ENCODED[:20] + "\n" + ENCODED[20:], "sha": _blob(RAW),
             "git_url": LIVEBENCH_GITHUB_API + "/git/blobs/" + _blob(RAW),
             "html_url": "https://github.com/LiveBench/livebench.github.io/blob/" + COMMIT + "/" + PATH}
    value.update(changes)
    return value


def _content(value) -> str:
    with pytest.raises(ValueError) as caught:
        _github_content(json.dumps(value).encode(), PATH, COMMIT)
    return str(caught.value)


def test_the_named_file_at_the_named_commit_is_decoded():
    raw, identity = _github_content(json.dumps(_file()).encode(), PATH, COMMIT)
    assert raw == RAW
    assert identity == {"path": PATH, "commit": COMMIT, "git_blob_sha1": _blob(RAW), "bytes": len(RAW),
                        "decoded_sha256": hashlib.sha256(RAW).hexdigest()}


@pytest.mark.parametrize("changes, code", [
    ({"type": "dir"}, "LIVEBENCH_GITHUB_IDENTITY_INVALID"),
    ({"path": "results/other.csv"}, "LIVEBENCH_GITHUB_IDENTITY_INVALID"),
    ({"name": "other.csv"}, "LIVEBENCH_GITHUB_IDENTITY_INVALID"),
    ({"size": 0}, "LIVEBENCH_GITHUB_SIZE_UNSUPPORTED"),
    ({"size": 1024 * 1024 + 1}, "LIVEBENCH_GITHUB_SIZE_UNSUPPORTED"),
    ({"size": float(len(RAW))}, "LIVEBENCH_GITHUB_SIZE_UNSUPPORTED"),
    ({"size": True}, "LIVEBENCH_GITHUB_SIZE_UNSUPPORTED"),
    ({"encoding": "none", "content": ""}, "LIVEBENCH_GITHUB_CONTENT_UNAVAILABLE"),
    ({"content": None}, "LIVEBENCH_GITHUB_CONTENT_UNAVAILABLE"),
    ({"content": "not base64 !"}, "LIVEBENCH_GITHUB_CONTENT_INVALID"),
    ({"content": ENCODED[:8] + "*" + ENCODED[8:]}, "LIVEBENCH_GITHUB_CONTENT_INVALID"),
    ({"size": len(RAW) + 1}, "LIVEBENCH_GITHUB_SIZE_MISMATCH"),
    ({"sha": "0" * 40}, "LIVEBENCH_GITHUB_HASH_MISMATCH"),
    ({"git_url": "https://api.github.com/repos/someone/else/git/blobs/" + _blob(RAW)},
     "LIVEBENCH_GITHUB_IDENTITY_INVALID"),
    ({"html_url": "https://github.com/LiveBench/livebench.github.io/blob/main/" + PATH}, "LIVEBENCH_GITHUB_IDENTITY_INVALID"),
])
def test_anything_but_that_exact_file_is_refused(changes, code):
    assert _content(_file(**changes)) == code


@pytest.mark.parametrize("body", [b"not json", b"[]"])
def test_a_body_that_is_not_a_file_object_is_refused(body):
    with pytest.raises(ValueError, match="LIVEBENCH_GITHUB_CONTENT_INVALID|LIVEBENCH_GITHUB_IDENTITY_INVALID"):
        _github_content(body, PATH, COMMIT)


def test_changed_bytes_with_the_old_hash_are_refused():
    other = base64.b64encode(b"model,score\nexample,99.9\n").decode()
    assert _content(_file(content=other)) == "LIVEBENCH_GITHUB_HASH_MISMATCH"


def _commit(**changes) -> bytes:
    value = {"sha": COMMIT, "url": LIVEBENCH_GITHUB_API + "/commits/" + COMMIT,
             "html_url": "https://github.com/LiveBench/livebench.github.io/commit/" + COMMIT}
    value.update(changes)
    return json.dumps(value).encode()


def test_the_commit_page_gives_the_commit():
    assert _github_commit(_commit()) == COMMIT


@pytest.mark.parametrize("body", [
    _commit(sha="A" * 40, url=LIVEBENCH_GITHUB_API + "/commits/" + "A" * 40,
            html_url="https://github.com/LiveBench/livebench.github.io/commit/" + "A" * 40),
    _commit(sha="a" * 39, url=LIVEBENCH_GITHUB_API + "/commits/" + "a" * 39,
            html_url="https://github.com/LiveBench/livebench.github.io/commit/" + "a" * 39),
    _commit(url="https://api.github.com/repos/someone/else/commits/" + COMMIT),
    _commit(html_url="https://github.com/someone/else/commit/" + COMMIT),
    b"not json",
    b"[]",
    json.dumps({"sha": None}).encode(),
])
def test_a_commit_page_from_elsewhere_is_refused(body):
    with pytest.raises(ValueError, match="LIVEBENCH_GITHUB_COMMIT_INVALID"):
        _github_commit(body)
