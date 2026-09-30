"""Memorive MODEL-GATEWAY/MODEL_GATEWAY · MODEL_GATEWAY Provider 注册表。

provider 名 → adapter 类。

Router 靠"读 slot.provider → 查本表"分派,绝不用 if provider == 'xxx' 硬编码。
加一个 provider = 写个 adapter + 在文末 import 一行登记(登记非分派),Router 不改。
"""
from __future__ import annotations

from ..errors import ProviderNotRegistered

_REGISTRY: dict[str, type] = {}


def register(provider_name: str):
    def deco(cls):
        _REGISTRY[provider_name] = cls
        return cls
    return deco


def get_adapter(provider_name: str):
    cls = _REGISTRY.get(provider_name)
    if cls is None:
        raise ProviderNotRegistered(
            f"provider {provider_name!r} 未注册 adapter;已注册: {sorted(_REGISTRY)}"
        )
    return cls()


def registered() -> list[str]:
    return sorted(_REGISTRY)


# ── adapter 登记(自注册;加 provider 在此加一行,Router 不动)──
from . import deepseek  # noqa: E402,F401
from . import siliconflow, ollama  # noqa: E402,F401  嵌入云/本地两 adapter(ServiceContracts 第 3 步)
from . import openai  # noqa: E402,F401  判断类 GPT 异源(Verification 第 3 步·verify_judgment 槽)
from . import anthropic  # noqa: E402,F401  RESEARCH_ANALYSIS 分析 · Opus(升 Anthropic;Messages API)
# 后续各链路上线再加: from . import chroma ...
