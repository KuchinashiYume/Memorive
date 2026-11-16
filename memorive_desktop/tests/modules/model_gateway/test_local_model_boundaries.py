"""Boundaries of the local model routes: Ollama embedding only on this machine, and the local
multimodal ranker only reading listed image files whose bytes and type match the request.
"""
from __future__ import annotations

import io
import json
import urllib.request
from types import SimpleNamespace

import pytest

from memorive.models.gateway.errors import NonLocalEmbedEndpoint
from memorive.models.gateway.local_multimodal_ranker import LocalMultimodalRankerViolation, build_request
from memorive.models.gateway.local_multimodal_ranker.contracts import bytes_sha256
from memorive.models.gateway.providers.ollama import OllamaAdapter
from tests.modules.model_gateway.test_local_multimodal_ranker import _batch, _binding


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def urlopen(request, timeout=None):
        calls.append(request.full_url)
        if request.full_url.endswith("/api/tags"):
            return _Response(json.dumps({"models": [{"name": "bge-m3:latest"}]}).encode())
        return _Response(json.dumps({"model": "bge-m3:latest", "embeddings": [[0.1, 0.2]]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return calls


def _slot(endpoint):
    return SimpleNamespace(endpoint=endpoint, model_id="bge-m3", task_type="embed")


@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:11434/api/embed", "http://localhost:11434/api/embed",
                                      "http://[::1]:11434/api/embed"])
def test_ollama_embeds_and_probes_on_this_machine(sent, endpoint):
    adapter = OllamaAdapter()
    assert adapter.embed(_slot(endpoint), {"input": ["text"]})["embeddings"] == [[0.1, 0.2]]
    assert adapter.healthcheck(_slot(endpoint))["ready"] is True
    assert len(sent) == 2


@pytest.mark.parametrize("endpoint", ["http://192.168.1.20:11434/api/embed", "https://ollama.example.com/api/embed",
                                      "http://127.0.0.2:11434/api/embed", "http://user@example.com/api/embed"])
def test_ollama_refuses_any_other_host_before_sending_anything(sent, endpoint):
    adapter = OllamaAdapter()
    with pytest.raises(NonLocalEmbedEndpoint):
        adapter.embed(_slot(endpoint), {"input": ["private text"]})
    with pytest.raises(NonLocalEmbedEndpoint):
        adapter.healthcheck(_slot(endpoint))
    assert sent == []


@pytest.fixture
def image_root(tmp_path):
    root = tmp_path / "figures"
    root.mkdir()
    (root / "figure1.png").write_bytes(b"\x89PNG fake figure")
    (tmp_path / "outside.png").write_bytes(b"\x89PNG outside")
    return root


def _with_image(image):
    batch = _batch()
    batch["documents"][0]["images"] = [image]
    return [batch]


def _image(root, **changes):
    value = {"path": str(root / "figure1.png"), "sha256": bytes_sha256((root / "figure1.png").read_bytes()),
             "mime_type": "image/png"}
    value.update(changes)
    return value


def test_a_listed_image_with_matching_bytes_is_passed_on(image_root):
    request = build_request(_with_image(_image(image_root)), _binding(), allowed_image_roots=[image_root])
    images = request["batches"][0]["documents"][0]["images"]
    assert images == [{"path": str((image_root / "figure1.png").resolve()), "sha256": _image(image_root)["sha256"],
                       "mime_type": "image/png"}]


@pytest.mark.parametrize("change, code", [
    (lambda root: {"path": ""}, "IMAGE_PATH_INVALID"),
    (lambda root: {"path": str(root.parent / "outside.png")}, "IMAGE_PATH_OUTSIDE_ALLOWED_ROOTS"),
    (lambda root: {"path": str(root / ".." / "outside.png")}, "IMAGE_PATH_OUTSIDE_ALLOWED_ROOTS"),
    (lambda root: {"path": str(root / "missing.png")}, "IMAGE_MISSING"),
    (lambda root: {"sha256": "0" * 64}, "IMAGE_HASH_MISMATCH"),
    (lambda root: {"mime_type": "image/gif"}, "IMAGE_MIME_INVALID"),
])
def test_an_image_that_is_not_exactly_the_listed_file_is_refused(image_root, change, code):
    with pytest.raises(LocalMultimodalRankerViolation, match=code):
        build_request(_with_image(_image(image_root, **change(image_root))), _binding(),
                      allowed_image_roots=[image_root])


def test_images_need_allowed_roots_and_a_list(image_root):
    with pytest.raises(LocalMultimodalRankerViolation, match="IMAGE_ROOTS_REQUIRED"):
        build_request(_with_image(_image(image_root)), _binding())
    batch = _batch()
    batch["documents"][0]["images"] = "figure1.png"
    with pytest.raises(LocalMultimodalRankerViolation, match="IMAGES_INVALID"):
        build_request([batch], _binding(), allowed_image_roots=[image_root])
