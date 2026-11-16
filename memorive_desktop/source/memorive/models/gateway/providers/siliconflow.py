"""The SiliconFlow adapter: cloud embeddings, reranking and DeepSeek-OCR.

OpenAI-compatible /v1/embeddings (body {model, input}, reply {data: [{embedding}], model, usage});
/v1/rerank (body {model, query, documents}, reply {results: [{index, relevance_score}], tokens,
model}). Plain urllib, no SDK; the key comes from the environment, and the Authorization header is
never logged. The user's own data uses it by default (embed_cloud, rerank; a domestic provider, so
the region check skips it); entrusted data is embedded locally (ollama.py).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.request

from memorive.models.gateway.providers import register
from memorive.models.gateway.providers.base import ProviderAdapter
from memorive.models.gateway.errors import ApiKeyMissing


_DEEPSEEK_OCR_MODEL = "deepseek-ai/DeepSeek-OCR"
_GROUNDING_METADATA_LINE_RE = re.compile(
    r"^[ \t]*<\|ref\|>[^\r\n]{1,120}<\|/ref\|>[ \t]*"
    r"<\|det\|>[ \t]*\[\[[0-9.,\- \t\[\]]+\]\]"
    r"[ \t]*<\|/det\|>[ \t]*(?:\r?\n)?",
    re.MULTILINE,
)


def _normalize_deepseek_ocr_markdown(content: object) -> object:
    """Drop DeepSeek's grounding coordinate lines and keep the Markdown after them."""
    if not isinstance(content, str):
        return content
    return _GROUNDING_METADATA_LINE_RE.sub("", content).strip()


def _sanitized_http_error(exc: urllib.error.HTTPError) -> urllib.error.HTTPError:
    """SiliconFlow's error code and message; the reply stream, request body, image and credentials are not kept."""
    provider_code = None
    provider_message = None
    try:
        raw = exc.read(8192)
        data = json.loads(raw.decode("utf-8", errors="replace"))
        if isinstance(data, dict):
            nested = data.get("error") if isinstance(data.get("error"), dict) else {}
            provider_code = data.get("code", nested.get("code"))
            provider_message = data.get("message", nested.get("message"))
    except Exception:
        pass
    if provider_message is not None:
        provider_message = " ".join(str(provider_message).split())
        provider_message = re.sub(
            r"data:[^;,\s]+;base64,[A-Za-z0-9+/=]+",
            "data:[REDACTED]",
            provider_message,
        )
        provider_message = re.sub(
            r"(?i)Bearer\s+\S+",
            "Bearer [REDACTED]",
            provider_message,
        )[:240]
    details = [str(exc.reason)]
    if provider_code is not None:
        details.append(f"provider_code={provider_code}")
    if provider_message:
        details.append(f"provider_message={provider_message}")
    return urllib.error.HTTPError(
        exc.url,
        exc.code,
        "; ".join(details),
        exc.headers,
        None,
    )


@register("siliconflow")
class SiliconFlowAdapter(ProviderAdapter):
    def ocr(self, slot, payload: dict) -> dict:
        """DeepSeek-OCR for one page. The image reaches this method, and is encoded, only after the
        data ownership check."""
        key = os.environ.get(slot.api_key_env or "")
        if not key:
            raise ApiKeyMissing(slot.task_type, slot.api_key_env or "(未配置)")

        detail = slot.image_detail or "high"
        if detail not in {"auto", "low", "high"}:
            raise ValueError(f"非法 image_detail: {detail!r}")
        image_url = (
            f"data:{payload['mime_type']};base64,"
            + base64.b64encode(payload["image_bytes"]).decode("ascii")
        )
        body_obj = {
            "model": slot.model_id,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {
                        "url": image_url,
                        "detail": detail,
                    }},
                    {"type": "text", "text": payload["prompt"]},
                ],
            }],
            "stream": False,
            "temperature": 0.0 if slot.temperature is None else slot.temperature,
        }
        if slot.max_tokens is not None:
            body_obj["max_tokens"] = slot.max_tokens
        body = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(slot.endpoint, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {key}")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(
                req,
                timeout=slot.timeout_seconds or 60,
            ) as resp:
                response_bytes = resp.read()
        except urllib.error.HTTPError as exc:
            raise _sanitized_http_error(exc) from exc
        data = json.loads(response_bytes.decode("utf-8"))
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "\n".join(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        if slot.model_id == _DEEPSEEK_OCR_MODEL:
            content = _normalize_deepseek_ocr_markdown(content)

        return {
            "task_type": slot.task_type,
            "provider": "siliconflow",
            "model": data.get("model") or slot.model_id,
            "text": content,
            "usage": data.get("usage"),
            "page_number": payload["page_number"],
            "source_sha256": payload["source_sha256"],
            "response_sha256": hashlib.sha256(response_bytes).hexdigest(),
            "request_id": data.get("id"),
            "finish_reason": choice.get("finish_reason"),
        }

    def embed(self, slot, payload: dict) -> dict:
        key = os.environ.get(slot.api_key_env or "")          # the variable named in the configuration
        if not key:
            raise ApiKeyMissing(slot.task_type, slot.api_key_env or "(未配置)")

        body = json.dumps({"model": slot.model_id, "input": payload["input"]}).encode("utf-8")
        req = urllib.request.Request(slot.endpoint, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {key}")      # never in a log, an error or metadata
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        # The OpenAI-compatible API does not promise data in input order, so it is sorted by index;
        # with several inputs a vector would otherwise land on the wrong chunk.
        items = sorted(data["data"], key=lambda d: d.get("index", 0))
        return {
            "task_type": slot.task_type,
            "provider": "siliconflow",
            "model": data.get("model"),                        # as returned (BAAI/bge-m3); the gateway normalizes it
            "embeddings": [d["embedding"] for d in items],     # data[i].embedding in index order
            "usage": data.get("usage"),
            "raw": data,                                        # for the caller and tests only; never logged
        }

    def rerank(self, slot, payload: dict) -> dict:
        """Cross-encoder reranking: POST /v1/rerank returns each document's relevance_score."""
        key = os.environ.get(slot.api_key_env or "")          # the variable named in the configuration
        if not key:
            raise ApiKeyMissing(slot.task_type, slot.api_key_env or "(未配置)")

        body = json.dumps({
            "model": slot.model_id,
            "query": payload["query"],
            "documents": payload["documents"],
            "return_documents": False,                        # index and score only, not the texts
        }).encode("utf-8")
        req = urllib.request.Request(slot.endpoint, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {key}")      # never in a log, an error or metadata
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        # /v1/rerank returns {results: [{index, relevance_score, document?}], tokens, model}; sorted by
        # index so each score stays with its document.
        items = sorted(data.get("results") or [], key=lambda r: r.get("index", 0))
        return {
            "task_type": slot.task_type,
            "provider": "siliconflow",
            "model": data.get("model") or slot.model_id,   # /v1/rerank has no model field: the slot's model_id
            "results": [{"index": r.get("index"), "relevance_score": r.get("relevance_score")} for r in items],
            "usage": None,          # billed per document or call, not per token, so no token counts
            "raw": data,                                        # for the caller and tests only; never logged
        }
