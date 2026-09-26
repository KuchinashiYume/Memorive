"""Ollama 本地嵌入 adapter(ServiceContracts 第 3 步)。

- 嵌入走**官方当前** endpoint `POST /api/embed`(body `{model,input}` → resp `{model,embeddings:[[...]]}`,
  L2 归一化;旧 `/api/embeddings` 的 `{model,prompt}` 已被取代)。如遇旧版本地服务需兼容,
  **只在本 adapter 内部处理**——本地失败**绝不触发云端 fallback**(安全阀红线,见 fallback.FORBIDDEN_FALLBACK)。
- `healthcheck` = **纯能力探测**:GET `/api/tags` 列本地模型,**绝不发任何待嵌文本**
  (否则探测请求自身就把他人托付数据发出去了,安全阀白设)。
- 本地服务、**无 key**。他人托付数据按归属分流至此(embed_local)。
"""
from __future__ import annotations

import json
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from . import register
from .base import ProviderAdapter
from ..errors import NonLocalEmbedEndpoint

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}   # 本地回环:他人托付数据只允许发本机


def _same_host(endpoint: str, path: str) -> str:
    """由 embed endpoint 派生同主机的另一路径(单一 endpoint 源,不另存探测 URL)。"""
    s = urlsplit(endpoint)
    return urlunsplit((s.scheme, s.netloc, path, "", ""))


def _assert_loopback(endpoint: str) -> None:
    """硬断言 endpoint 主机为本地回环(承 ServiceContracts 第 3 步对抗审查 #6):**使「本地」成为代码可验证的不变量**。
    他人托付数据只发本机;endpoint 被误配/篡改成远程即挡下,绝不让保密数据外发。"""
    host = urlsplit(endpoint).hostname
    if host not in _LOOPBACK:
        raise NonLocalEmbedEndpoint(
            f"本地嵌入 endpoint 主机 {host!r} 非本地回环 {sorted(_LOOPBACK)};"
            f"他人托付数据只允许发本机,挡下(防 endpoint 被误配/篡改成远程致保密数据外发)。")


@register("ollama")
class OllamaAdapter(ProviderAdapter):
    def embed(self, slot, payload: dict) -> dict:
        _assert_loopback(slot.endpoint)              # 发文本前先验:非回环即挡下(他人数据不外发)
        body = json.dumps({"model": slot.model_id, "input": payload["input"]}).encode("utf-8")
        req = urllib.request.Request(slot.endpoint, data=body, method="POST")   # /api/embed,本地无 key
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {
            "task_type": slot.task_type,
            "provider": "ollama",
            "model": data.get("model") or slot.model_id,       # raw 回显(如 bge-m3:latest),归一化在 gateway 出口
            "embeddings": data["embeddings"],                  # /api/embed 直接给 [[...]]
            "usage": None,                                     # 本地不计 token
            "raw": data,                                       # ⚠ 仅函数内/测试用,绝不进日志
        }

    def healthcheck(self, slot) -> dict:
        """纯能力探测:GET /api/tags,查目标模型是否已 pull。**不发任何待嵌文本**。
        返回结构化状态给 MODEL_GATEWAY(再由 DocumentEmbed 据 ready 决定挡下动作);本函数不做挡下判断。"""
        _assert_loopback(slot.endpoint)              # 探测前先验回环:非本机即受控挡下(不误判远程为"就绪")
        probe = _same_host(slot.endpoint, "/api/tags")
        req = urllib.request.Request(probe, method="GET")      # GET、无 body(payload 无任何文本)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        have = {(m.get("model") or m.get("name") or "").split(":")[0]
                for m in (data.get("models") or [])}           # 去 :tag 归一化后比对
        return {"ready": slot.model_id in have, "want": slot.model_id,
                "have": sorted(have), "probe": probe}
