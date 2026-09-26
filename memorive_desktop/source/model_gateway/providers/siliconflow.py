"""SiliconFlow 云端嵌入 + 重排 adapter(ServiceContracts 第 3 步嵌入 / A·修跨篇混入重排)。

OpenAI 兼容 `/v1/embeddings`(body `{model,input}` → resp `{data:[{embedding}],model,usage}`);
`/v1/rerank`(body `{model,query,documents}` → resp `{results:[{index,relevance_score}],tokens,model}`);
urllib、**不引任何 SDK**;key 走环境变量。**Authorization header 绝不进日志**(安全红线)。
自有数据默认走此(embed_cloud / rerank·国内 domestic_skip);他人托付走本地(见 ollama.py)。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.request

from . import register
from .base import ProviderAdapter
from ..errors import ApiKeyMissing


_DEEPSEEK_OCR_MODEL = "deepseek-ai/DeepSeek-OCR"
_GROUNDING_METADATA_LINE_RE = re.compile(
    r"^[ \t]*<\|ref\|>[^\r\n]{1,120}<\|/ref\|>[ \t]*"
    r"<\|det\|>[ \t]*\[\[[0-9.,\- \t\[\]]+\]\]"
    r"[ \t]*<\|/det\|>[ \t]*(?:\r?\n)?",
    re.MULTILINE,
)


def _normalize_deepseek_ocr_markdown(content: object) -> object:
    """删除 DeepSeek grounding 坐标行，保留其后的 Markdown 正文。"""
    if not isinstance(content, str):
        return content
    return _GROUNDING_METADATA_LINE_RE.sub("", content).strip()


def _sanitized_http_error(exc: urllib.error.HTTPError) -> urllib.error.HTTPError:
    """提取 SiliconFlow 错误 code/message；不保留响应流、请求体、图片或凭据。"""
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
        """DeepSeek-OCR 单页调用。图片只在所有权闸门之后进入本方法并编码。"""
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
        key = os.environ.get(slot.api_key_env or "")          # 按配置里的"环境变量名"取 key
        if not key:
            raise ApiKeyMissing(slot.task_type, slot.api_key_env or "(未配置)")

        body = json.dumps({"model": slot.model_id, "input": payload["input"]}).encode("utf-8")
        req = urllib.request.Request(slot.endpoint, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {key}")      # ⚠ 凭据:绝不进日志/错误/metadata
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        # OpenAI 兼容规范不保证 data 按输入顺序返回,须按 index 重排(承 ServiceContracts 第 3 步对抗审查 #7:
        # 防将来 >1 输入的批量调用 chunk↔向量错位;单条调用下亦无害)。
        items = sorted(data["data"], key=lambda d: d.get("index", 0))
        return {
            "task_type": slot.task_type,
            "provider": "siliconflow",
            "model": data.get("model"),                        # raw 回显(如 BAAI/bge-m3),归一化在 gateway 出口
            "embeddings": [d["embedding"] for d in items],     # OpenAI 兼容 data[i].embedding,按 index 重排
            "usage": data.get("usage"),
            "raw": data,                                        # ⚠ 仅函数内/测试用,绝不进日志
        }

    def rerank(self, slot, payload: dict) -> dict:
        """cross-encoder 重排(A·修跨篇混入)。POST /v1/rerank,回每个 document 的 relevance_score。"""
        key = os.environ.get(slot.api_key_env or "")          # 按配置里的"环境变量名"取 key
        if not key:
            raise ApiKeyMissing(slot.task_type, slot.api_key_env or "(未配置)")

        body = json.dumps({
            "model": slot.model_id,
            "query": payload["query"],
            "documents": payload["documents"],
            "return_documents": False,                        # 只要 index+score 对齐,不回显原文(省流量)
        }).encode("utf-8")
        req = urllib.request.Request(slot.endpoint, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {key}")      # ⚠ 凭据:绝不进日志/错误/metadata
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        # /v1/rerank 回 {results:[{index, relevance_score, document?}], tokens, model};
        # 按 index 升序对齐 documents(承 embed 同款防 block↔score 错位口径,承 #7)。
        items = sorted(data.get("results") or [], key=lambda r: r.get("index", 0))
        return {
            "task_type": slot.task_type,
            "provider": "siliconflow",
            "model": data.get("model") or slot.model_id,   # /v1/rerank 不回 model 字段 → 退回槽 model_id(记账/观测)
            "results": [{"index": r.get("index"), "relevance_score": r.get("relevance_score")} for r in items],
            "usage": None,          # rerank 按文档/调用计费、非 token 形状 → 记 None,不误配 token 账(bge-reranker 无定价→est_cost null)
            "raw": data,                                        # ⚠ 仅函数内/测试用,绝不进日志
        }
