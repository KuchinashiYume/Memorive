"""Token counts and cost estimates read from a reply: the models a command-line tool reports, its
usage numbers, the token usage of an API reply, and the estimated cost in CNY.
"""
from __future__ import annotations

from memorive.models.gateway.prompt_cache import cache_usage
import json
from typing import Any, Mapping
from memorive.models.endpoints import SAFE_MODEL


DEEPSEEK_ESTIMATE_CNY_PER_MILLION = {
    "deepseek-v4-flash": {
        "input_cache_hit": 0.0224,
        "input_cache_miss": 1.12,
        "output": 2.24,
    },
    "deepseek-v4-pro": {
        "input_cache_hit": 0.029,
        "input_cache_miss": 3.48,
        "output": 6.96,
    },
}


def _walk_model_identity(value: Any, found: set[str]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if (
                str(key).casefold()
                in {"model", "model_name", "requested_model", "returned_model"}
                and isinstance(child, str)
                and SAFE_MODEL.fullmatch(child)
            ):
                found.add(child)
            _walk_model_identity(child, found)
    elif isinstance(value, list):
        for child in value:
            _walk_model_identity(child, found)


def _reported_models(output: bytes) -> set[str]:
    found: set[str] = set()
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        _walk_model_identity(value, found)
    if found:
        return found
    try:
        value = json.loads(output.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return set()
    _walk_model_identity(value, found)
    return found


def _walk_usage_candidates(value: Any, found: list[Mapping[str, Any]]) -> None:
    if isinstance(value, Mapping):
        keys = {str(key) for key in value}
        has_prompt = bool(
            keys
            & {
                "prompt_tokens",
                "input_tokens",
                "promptTokenCount",
                "inputTokens",
            }
        )
        has_completion = bool(
            keys
            & {
                "completion_tokens",
                "output_tokens",
                "candidatesTokenCount",
                "outputTokens",
            }
        )
        if has_prompt and has_completion:
            found.append(value)
        for child in value.values():
            _walk_usage_candidates(child, found)
    elif isinstance(value, list):
        for child in value:
            _walk_usage_candidates(child, found)


def _usage_from_cli_output(output: bytes) -> Mapping[str, Any]:
    found: list[Mapping[str, Any]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        _walk_usage_candidates(value, found)
    return found[-1] if found else {}


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _normalise_token_usage(value: Mapping[str, Any]) -> dict[str, int]:
    prompt = next(
        (
            parsed
            for key in (
                "prompt_tokens",
                "input_tokens",
                "promptTokenCount",
                "inputTokens",
                "prompt_eval_count",
            )
            if (parsed := _nonnegative_int(value.get(key))) is not None
        ),
        None,
    )
    completion = next(
        (
            parsed
            for key in (
                "completion_tokens",
                "output_tokens",
                "candidatesTokenCount",
                "outputTokens",
                "eval_count",
            )
            if (parsed := _nonnegative_int(value.get(key))) is not None
        ),
        None,
    )
    if prompt is None or completion is None:
        return {}
    normalised = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
    }
    cached = next(
        (
            parsed
            for key in (
                "cached_input_tokens",
                "prompt_cache_hit_tokens",
                "cache_read_input_tokens",
                "cachedContentTokenCount",
            )
            if (parsed := _nonnegative_int(value.get(key))) is not None
        ),
        None,
    )
    observed_cache = cache_usage(dict(value))
    cached = observed_cache["read_tokens"]
    if cached is not None:
        normalised["cached_input_tokens"] = cached
    if observed_cache["write_tokens"] is not None:
        normalised["cache_write_input_tokens"] = observed_cache["write_tokens"]
    cache_miss = _nonnegative_int(value.get("prompt_cache_miss_tokens"))
    if cache_miss is not None:
        normalised["prompt_cache_miss_tokens"] = cache_miss
    return normalised


def _deepseek_estimated_cost_cny(
    model: str,
    usage: Mapping[str, int],
) -> float | None:
    pricing = DEEPSEEK_ESTIMATE_CNY_PER_MILLION.get(model)
    prompt = _nonnegative_int(usage.get("prompt_tokens"))
    completion = _nonnegative_int(usage.get("completion_tokens"))
    if pricing is None or prompt is None or completion is None:
        return None
    cache_hit = min(
        prompt,
        _nonnegative_int(usage.get("cached_input_tokens")) or 0,
    )
    explicit_miss = _nonnegative_int(usage.get("prompt_cache_miss_tokens"))
    cache_miss = (
        min(prompt, explicit_miss)
        if explicit_miss is not None
        else prompt - cache_hit
    )
    estimate = (
        cache_hit * pricing["input_cache_hit"]
        + cache_miss * pricing["input_cache_miss"]
        + completion * pricing["output"]
    ) / 1_000_000
    return round(estimate, 12)


def _target_estimated_cost_cny(
    model_config: Mapping[str, Any],
    usage: Mapping[str, int],
) -> float | None:
    """Estimate a non-DeepSeek API call from a frozen target-side CNY profile.

    The transport deliberately does not carry a mutable global price table for
    every vendor.  A campaign that uses another paid API must bind its dated
    price evidence before sending and pass the conservative CNY rates with the
    model binding.  Cached input is charged at the ordinary input rate unless
    an explicit cache-hit rate is present, which keeps the estimate fail-safe.
    """

    pricing = model_config.get("pricing_estimate_cny_per_million")
    evidence_ref = model_config.get("pricing_evidence_ref")
    if not isinstance(pricing, Mapping) or not isinstance(evidence_ref, str):
        return None
    if not evidence_ref or len(evidence_ref) > 512:
        return None
    input_rate = pricing.get("input")
    output_rate = pricing.get("output")
    cache_hit_rate = pricing.get("input_cache_hit", input_rate)
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= float(value) <= 100_000
        for value in (input_rate, output_rate, cache_hit_rate)
    ):
        return None
    prompt = _nonnegative_int(usage.get("prompt_tokens"))
    completion = _nonnegative_int(usage.get("completion_tokens"))
    if prompt is None or completion is None:
        return None
    cache_hit = min(
        prompt,
        _nonnegative_int(usage.get("cached_input_tokens")) or 0,
    )
    cache_miss = prompt - cache_hit
    estimate = (
        cache_hit * float(cache_hit_rate)
        + cache_miss * float(input_rate)
        + completion * float(output_rate)
    ) / 1_000_000
    return round(estimate, 12)


def _api_estimated_cost_cny(
    model: str,
    model_config: Mapping[str, Any],
    usage: Mapping[str, int],
) -> float | None:
    deepseek = _deepseek_estimated_cost_cny(model, usage)
    if deepseek is not None:
        return deepseek
    return _target_estimated_cost_cny(model_config, usage)
