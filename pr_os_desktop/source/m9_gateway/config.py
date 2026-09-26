"""PR-OS P01/T01/M09 · 读取 M9 模型配置(models.yaml) → 结构化 SlotConfig。

纪律:本模块只记 api_key_env 的"名",绝不在此(或任何地方)取 key 的"值";
key 值由各 adapter 在真正调用时才从环境变量读。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .errors import UnknownTaskType

# 与 models.yaml 各槽字段对应(api_key_env 只存名、不存值)
_SLOT_FIELDS = {
    "enabled", "provider", "model_id", "endpoint",
    "api_key_env", "swap_granularity", "on_swap", "notes",
    "thinking_type", "effort", "max_tokens", "temperature",
    "timeout_seconds", "image_detail", "prompt_version",
    "price_profile_id", "price_profile_revision",
}


@dataclass(frozen=True)
class SlotConfig:
    task_type: str
    enabled: bool = False
    provider: str | None = None
    model_id: str | None = None
    endpoint: str | None = None
    api_key_env: str | None = None
    swap_granularity: str | None = None
    on_swap: str | None = None
    notes: str | None = None
    thinking_type: str | None = None      # Anthropic adaptive / manual / disabled thinking 模式(按 slot 配置)
    effort: str | None = None             # Anthropic output_config.effort；显式记录实验档位
    max_tokens: int | None = None         # 该槽的输出硬上限；thinking + 最终文本共用
    temperature: float | None = None      # OCR/对话确定性采样；None 时由 adapter 用安全默认
    timeout_seconds: int | None = None    # 单次请求超时；有限重试仍由 FallbackPolicy 统一控制
    image_detail: str | None = None       # 多模态 image_url.detail: auto | low | high
    prompt_version: int | None = None     # 生产槽显式固定版本；避免资格/实验 successor 自动接管 live prompt
    price_profile_id: str | None = None   # P06/T03 versioned PriceCatalog ref
    price_profile_revision: str | None = None


class GatewayConfig:
    def __init__(self, path: Path):
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        slots = {}
        for name, spec in (raw.get("slots") or {}).items():
            spec = spec or {}
            kept = {k: v for k, v in spec.items() if k in _SLOT_FIELDS}
            slots[name] = SlotConfig(task_type=name, **kept)
        self._slots = slots
        self.schema_version = raw.get("schema_version")
        self.price_catalog = raw.get("price_catalog") or {}
        relative_catalog = self.price_catalog.get("path")
        self.price_catalog_path = (
            (Path(path).parent / relative_catalog).resolve()
            if isinstance(relative_catalog, str) and relative_catalog
            else None
        )
        self.pricing = raw.get("pricing") or {}      # read-only legacy compatibility

    def slot(self, task_type: str) -> SlotConfig:
        try:
            return self._slots[task_type]
        except KeyError:
            raise UnknownTaskType(
                f"未知 task_type/槽位: {task_type!r};已知: {sorted(self._slots)}"
            ) from None

    def task_types(self) -> list[str]:
        return sorted(self._slots)

    def price_for(self, model_id: str | None):
        """Deprecated non-dated projection; exact callers use AccountingKernel.

        A catalog cannot be selected safely from model_id alone because date,
        region, service tier and alias-binding evidence are required.  Legacy
        callers therefore receive unknown rather than a guessed numeric cost.
        """
        if self.price_catalog_path is not None:
            return None, None
        m = (self.pricing.get("models") or {}).get(model_id or "")
        if not isinstance(m, dict):
            return None, None
        return m.get("input"), m.get("output")

    def pricing_status(self) -> str:
        if self.price_catalog_path is not None:
            return "VERSIONED_PRICE_CATALOG_EXACT_CONTEXT_REQUIRED"
        return self.pricing.get("pricing_status", "unverified")
