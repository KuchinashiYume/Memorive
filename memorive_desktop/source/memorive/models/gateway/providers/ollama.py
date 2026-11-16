"""The Ollama adapter for local embeddings (the embed_local slot, used for data entrusted to the user).

- Embeddings use Ollama's current endpoint POST /api/embed (body {model, input}, reply {model,
  embeddings: [[...]]}, L2-normalized; it replaced /api/embeddings with {model, prompt}). Support for
  an older local server would go in this adapter only; a local failure never falls back to the
  cloud (see fallback.FORBIDDEN_FALLBACK).
- healthcheck only asks GET /api/tags which models are installed and never sends text to embed,
  so the check itself cannot send entrusted data anywhere.
- A local service without a key.
"""
from __future__ import annotations

import json
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from memorive.models.gateway.providers import register
from memorive.models.gateway.providers.base import ProviderAdapter
from memorive.models.gateway.errors import NonLocalEmbedEndpoint

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}   # entrusted data goes only to this computer


def _same_host(endpoint: str, path: str) -> str:
    """Another path on the embed endpoint's host, so only one endpoint is configured."""
    s = urlsplit(endpoint)
    return urlunsplit((s.scheme, s.netloc, path, "", ""))


def _assert_loopback(endpoint: str) -> None:
    """Refuse an endpoint whose host is not a loopback address. Entrusted data goes only to this
    computer, so a remote endpoint, configured by mistake or changed, stops the call in code."""
    host = urlsplit(endpoint).hostname
    if host not in _LOOPBACK:
        raise NonLocalEmbedEndpoint(
            f"本地嵌入 endpoint 主机 {host!r} 非本地回环 {sorted(_LOOPBACK)};"
            f"他人托付数据只允许发本机,挡下(防 endpoint 被误配/篡改成远程致保密数据外发)。")


@register("ollama")
class OllamaAdapter(ProviderAdapter):
    def embed(self, slot, payload: dict) -> dict:
        _assert_loopback(slot.endpoint)              # before any text is sent
        body = json.dumps({"model": slot.model_id, "input": payload["input"]}).encode("utf-8")
        req = urllib.request.Request(slot.endpoint, data=body, method="POST")   # /api/embed, no key
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {
            "task_type": slot.task_type,
            "provider": "ollama",
            "model": data.get("model") or slot.model_id,       # as returned (bge-m3:latest); the gateway normalizes it
            "embeddings": data["embeddings"],                  # /api/embed returns [[...]]
            "usage": None,                                     # no tokens are counted locally
            "raw": data,                                       # for the caller and tests only; never logged
        }

    def healthcheck(self, slot) -> dict:
        """GET /api/tags: whether the model is installed. No text to embed is sent. Returns the state
        to the gateway; document embedding decides from ready whether to stop."""
        _assert_loopback(slot.endpoint)              # a remote endpoint is refused, never reported "ready"
        probe = _same_host(slot.endpoint, "/api/tags")
        req = urllib.request.Request(probe, method="GET")      # no body, no text
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        have = {(m.get("model") or m.get("name") or "").split(":")[0]
                for m in (data.get("models") or [])}           # compared without the :tag
        return {"ready": slot.model_id in have, "want": slot.model_id,
                "have": sorted(have), "probe": probe}
