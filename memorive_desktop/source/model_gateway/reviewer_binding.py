"""Minimal CORE binding for the single current Card reviewer slot."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml


class ReviewerBindingError(RuntimeError):
    card_status = "pending"
    allow_fallback = False


class ModelIdMismatch(ReviewerBindingError):
    pass


class ReviewerUnavailable(ReviewerBindingError):
    pass


@dataclass(frozen=True)
class ReviewerBinding:
    slot: str
    provider: str
    exact_model_id: str
    enabled: bool
    review_contract_id: str
    full_prompt_version: str
    delta_prompt_version: str
    full_schema_version: str
    delta_schema_version: str
    full: dict[str, Any]
    high_risk_delta: dict[str, Any]
    low_risk_delta: dict[str, Any]
    prompt_cache: dict[str, Any]
    message_batch: dict[str, Any]
    failure_policy: dict[str, Any]

    @classmethod
    def from_files(cls, binding_path: str | Path, models_path: str | Path) -> "ReviewerBinding":
        binding_doc = yaml.safe_load(Path(binding_path).read_text(encoding="utf-8"))
        models_doc = yaml.safe_load(Path(models_path).read_text(encoding="utf-8"))
        raw = binding_doc.get("card_reviewer") if isinstance(binding_doc, dict) else None
        if not isinstance(raw, dict):
            raise ReviewerBindingError("card_reviewer static binding is missing")
        slot_name = raw.get("slot")
        slots = models_doc.get("slots") if isinstance(models_doc, dict) else None
        slot = slots.get(slot_name) if isinstance(slots, dict) else None
        if not isinstance(slot, dict):
            raise ReviewerBindingError(f"MODEL_GATEWAY reviewer slot not found: {slot_name!r}")
        provider = slot.get("provider")
        model_id = slot.get("model_id")
        if provider != raw.get("expected_provider"):
            raise ModelIdMismatch(f"reviewer provider mismatch: expected={raw.get('expected_provider')!r}, configured={provider!r}")
        cls.validate_exact_model(str(raw.get("expected_exact_model_id") or ""), str(model_id or ""))
        full = dict(raw.get("full") or {})
        if (full.get("thinking") or {}).get("type") != "adaptive":
            raise ReviewerBindingError("CORE Full must remain low + adaptive")
        if full.get("effort") != "low" or full.get("max_tokens") != 16384:
            raise ReviewerBindingError("CORE Full must remain low + adaptive + 16384")
        output_format = full.get("output_format")
        if not isinstance(output_format, dict) or output_format.get("type") != "json_schema":
            raise ReviewerBindingError("CORE Full must require native json_schema output")
        full_schema_version = str(raw.get("full_schema_version") or "")
        if output_format.get("schema_version") != full_schema_version:
            raise ReviewerBindingError("CORE Full output schema version is not bound to the reviewer contract")
        return cls(
            slot=str(slot_name),
            provider=str(provider),
            exact_model_id=str(model_id),
            enabled=raw.get("enabled") is True,
            review_contract_id=str(raw.get("review_contract_id") or ""),
            full_prompt_version=str(raw.get("full_prompt_version") or ""),
            delta_prompt_version=str(raw.get("delta_prompt_version") or ""),
            full_schema_version=full_schema_version,
            delta_schema_version=str(raw.get("delta_schema_version") or ""),
            full=full,
            high_risk_delta=dict(raw.get("high_risk_delta") or {}),
            low_risk_delta=dict(raw.get("low_risk_delta") or {}),
            prompt_cache=dict(raw.get("prompt_cache") or {}),
            message_batch=dict(raw.get("message_batch") or {}),
            failure_policy=dict(raw.get("failure_policy") or {}),
        )

    @staticmethod
    def validate_exact_model(expected: str, actual: str) -> None:
        if not expected or not actual or expected != actual:
            raise ModelIdMismatch(f"MODEL_ID_MISMATCH expected={expected!r} actual={actual!r}")

    def assert_actual_model(
        self,
        actual: str | None,
        *,
        event_logger: Callable[..., Any] | None = None,
        target: str | None = None,
    ) -> None:
        try:
            self.validate_exact_model(self.exact_model_id, str(actual or ""))
        except ModelIdMismatch:
            if event_logger is not None:
                event_logger(
                    "model_gateway",
                    "Card reviewer model ID mismatch",
                    "API actual model ID differs from the configured exact reviewer model; current Card remains pending and no fallback is allowed.",
                    event_category="verify",
                    target=target,
                    context={"provider": self.provider, "expected_model": self.exact_model_id, "actual_model": actual, "card_status": "pending"},
                )
            raise

    def build_full_request_payload(
        self,
        *,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        timeout: int | float | None = None,
    ) -> dict[str, Any]:
        """Build the frozen Full-review call with provider-native JSON Schema output."""
        if not isinstance(messages, list) or not messages:
            raise ReviewerBindingError("Full-review messages must be a non-empty list")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ReviewerBindingError("Full-review JSON schema must be an object schema")

        effort = self.full.get("effort")
        thinking = self.full.get("thinking")
        max_tokens = self.full.get("max_tokens")
        payload: dict[str, Any] = {
            "messages": deepcopy(messages),
            "thinking": deepcopy(thinking),
            "effort": effort,
            "max_tokens": max_tokens,
            "stream": False,
            "output_config": {
                "effort": effort,
                "format": {
                    "type": "json_schema",
                    "schema": deepcopy(schema),
                },
            },
        }
        if timeout is not None:
            payload["timeout"] = timeout
        return payload

    def call_metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "exact_model_id": self.exact_model_id,
            "full_prompt_version": self.full_prompt_version,
            "delta_prompt_version": self.delta_prompt_version,
            "full_schema_version": self.full_schema_version,
            "delta_schema_version": self.delta_schema_version,
            "review_contract_id": self.review_contract_id,
        }
