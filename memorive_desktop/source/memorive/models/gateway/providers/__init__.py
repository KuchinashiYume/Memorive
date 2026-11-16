"""The provider registry: provider name to adapter class.

The router looks up slot.provider here; there is no if provider == 'xxx' in code. A new provider is
an adapter module that registers itself, plus one import line at the end of this file; the router
does not change.
"""
from __future__ import annotations

from memorive.models.gateway.errors import ProviderNotRegistered

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


# ── the adapters register themselves when imported; a new provider adds one line here ──
from memorive.models.gateway.providers import deepseek  # noqa: E402,F401
from memorive.models.gateway.providers import siliconflow, ollama  # noqa: E402,F401  cloud and local embeddings
from memorive.models.gateway.providers import openai  # noqa: E402,F401  a second model for card review judgments (verify_judgment)
from memorive.models.gateway.providers import anthropic  # noqa: E402,F401  research analysis (Messages API)
