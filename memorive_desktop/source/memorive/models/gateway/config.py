"""The gateway's slot configuration: models.yaml read into SlotConfig records.

Only the name of a key's environment variable (api_key_env) is kept here, never the key; an adapter
reads the key from the environment when it makes the call.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from memorive.models.gateway.errors import UnknownTaskType

# The slot configuration lies next to this module; every reader takes its path from here.
MODELS_YAML = Path(__file__).with_name("models.yaml")

# The fields of a slot in models.yaml (api_key_env holds a variable name, never a key)
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
    thinking_type: str | None = None      # Anthropic thinking mode: adaptive, manual or disabled
    effort: str | None = None             # Anthropic output_config.effort, recorded as set
    max_tokens: int | None = None         # the slot's output limit, thinking and final text together
    temperature: float | None = None      # sampling for OCR and chat; None uses the adapter's default
    timeout_seconds: int | None = None    # per request; retries are FallbackPolicy's
    image_detail: str | None = None       # image_url.detail for pictures: auto, low or high
    prompt_version: int | None = None     # pins the prompt version, so a newer trial version is not picked up
    price_profile_id: str | None = None   # ACCOUNTING-RECOVERY versioned PriceCatalog ref
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
