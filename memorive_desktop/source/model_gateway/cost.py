"""Compatibility facade over the ACCOUNTING-RECOVERY Accounting Kernel.

The legacy RUNTIME_LOG projection remains available, but all numeric calculation comes
from the one Decimal kernel.  Missing date/region/alias/cache evidence produces
NOT_ASSESSED rather than a guessed zero or guessed actual cost.
"""
from __future__ import annotations

import warnings
from pathlib import Path

from runtime_log import log_accounting

from .accounting import AccountingEnvelope, AccountingKernel, PriceCatalog, PriceQuery, UsageObservation
from .accounting.contracts import legacy_float


class CostManager:
    def __init__(self, config):
        self.config = config
        catalog_path = getattr(config, "price_catalog_path", None)
        self.kernel = (
            AccountingKernel(PriceCatalog.load(catalog_path))
            if catalog_path is not None
            else None
        )

    def record(self, task_type: str, result: dict, slot, prompt_version, *, cache_hit: bool, retries: int = 0) -> dict:
        runtime_log_task, backend = _map_task_type(task_type)
        model_id = (result.get("model") if isinstance(result, dict) else None) or slot.model_id or "unknown"

        usage_raw = result.get("usage") if isinstance(result, dict) else None
        tokens = _map_tokens(usage_raw)
        envelope = self._observe(result if isinstance(result, dict) else {}, slot, model_id, usage_raw)
        envelope_payload = envelope.as_dict()
        est_cost = None
        if envelope.accounting_status in {"EXACT_FROM_REPORTED_USAGE", "CONSERVATIVE_QUOTE"}:
            est_cost = {
                "value": legacy_float(envelope.local_estimate),
                "currency": envelope.currency,
            }

        from runtime_log.cache_metrics import cache_metrics
        context = {"prompt_cache_metrics": cache_metrics(usage_raw, duration_ms=result.get("duration_ms"))}
        if backend:
            context["backend"] = backend
        context["pricing_status"] = self.config.pricing_status()
        context["accounting_status"] = envelope.accounting_status
        context["accounting_envelope"] = envelope_payload
        context["legacy_est_cost_projection"] = "BINARY_FLOAT_COMPATIBILITY_ONLY" if est_cost else "NOT_NUMERIC"
        if isinstance(result, dict):
            if result.get("thinking") is not None:
                context["thinking"] = result["thinking"]
            if result.get("effort") is not None:
                context["effort"] = result["effort"]
            if result.get("max_tokens") is not None:
                context["max_tokens"] = result["max_tokens"]

        try:
            log_accounting(
                runtime_log_task, model_id,
                prompt_version=prompt_version,
                tokens=tokens,
                est_cost=est_cost,
                quota_account=slot.api_key_env,
                retries=retries,
                cache_hit=cache_hit,
                context=context or None,
            )
        except Exception as exc:   # 记账失败不拖垮调用,但不静默 (warn + 标记结果)
            warnings.warn(f"RUNTIME_LOG 记账写入失败 (不影响本次调用结果): {exc!r}", stacklevel=2)
            if isinstance(result, dict):
                result["_accounting_error"] = repr(exc)
        return result

    def _observe(self, result, slot, returned_model, usage_raw):
        requested_model = getattr(slot, "model_id", None) or "unknown"
        profile_id = getattr(slot, "price_profile_id", None)
        profile_revision = getattr(slot, "price_profile_revision", None)
        if self.kernel is not None and (not profile_id or not profile_revision):
            inferred = self.kernel.catalog.profile_ref_for_model(returned_model)
            if inferred:
                profile_id, profile_revision = inferred
        observed_at = result.get("accounting_observed_at")
        region = result.get("accounting_region")
        alias_ref = result.get("model_alias_binding_ref")
        missing = [
            name
            for name, value in (
                ("ACCOUNTING_KERNEL_UNAVAILABLE", self.kernel),
                ("PRICE_PROFILE_ID_MISSING", profile_id),
                ("PRICE_PROFILE_REVISION_MISSING", profile_revision),
                ("OBSERVED_AT_MISSING", observed_at),
                ("REGION_MISSING", region),
                ("MODEL_ALIAS_BINDING_REF_MISSING", alias_ref),
            )
            if not value
        ]
        prompt_details = (usage_raw or {}).get("prompt_tokens_details") or {}
        cache_evidence_present = bool(
            prompt_details.get("cached_tokens")
            or prompt_details.get("cache_creation_tokens")
            or (usage_raw or {}).get("cache_read_input_tokens")
            or (usage_raw or {}).get("cache_write_input_tokens")
        )
        includes_cache = result.get("input_includes_cache")
        if cache_evidence_present and not isinstance(includes_cache, bool):
            missing.append("INPUT_CACHE_INCLUSION_SEMANTICS_MISSING")
        usage = None
        if not missing:
            usage = UsageObservation.from_provider_usage(
                usage_raw,
                input_includes_cache=includes_cache if isinstance(includes_cache, bool) else True,
                source_evidence_ref=result.get("usage_evidence_ref"),
            )
            query = PriceQuery(
                price_profile_id=profile_id,
                price_profile_revision=profile_revision,
                requested_model=requested_model,
                returned_model=returned_model,
                model_alias_binding_ref=alias_ref,
                observed_at=observed_at,
                region=region,
                currency="CNY",
                service_tier=result.get("service_tier", "standard"),
                context_tokens=int(result.get("context_tokens", 0)),
            )
            return self.kernel.observe(
                query,
                usage,
                evidence_refs=tuple(filter(None, (result.get("usage_evidence_ref"), alias_ref))),
            )
        return AccountingEnvelope(
            stage="OBSERVED",
            accounting_status="NOT_ASSESSED",
            currency=None,
            local_estimate=None,
            breakdown=(),
            identity={
                "requested_model": requested_model,
                "returned_model": returned_model,
                "model_alias_binding_ref": alias_ref,
            },
            usage=None,
            price_binding=None,
            unavailable_reasons=tuple(sorted(set(missing))),
        )


def _map_task_type(task_type: str):
    """MODEL_GATEWAY 槽位名 → RUNTIME_LOG 记账 task_type (硬枚举) + backend。
    embed_cloud / embed_local → embed + cloud/local (承第 3 步契约:云/本地不进 task_type,落 context.backend)。"""
    if task_type in ("embed_cloud", "embed_local"):
        return "embed", task_type.split("_", 1)[1]
    return task_type, None


def _map_tokens(usage):
    if not usage:
        return None
    details = usage.get("completion_tokens_details") or {}
    return {
        "prompt": usage.get("prompt_tokens", 0),
        "completion": usage.get("completion_tokens", 0),
        "reasoning": details.get("reasoning_tokens", 0),   # completion 的子集,不叠加
        "total": usage.get("total_tokens", 0),             # provider 原始口径
    }
