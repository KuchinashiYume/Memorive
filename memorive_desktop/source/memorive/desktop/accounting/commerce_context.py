"""Reference builder for SharedModelCommerceContext-v1.

The product adapter supplies normalized profiles from ``settings.get_state`` and
``local_models.bootstrap``.  Leaderboard identity is accepted only as an exact,
explicit key; this module deliberately performs no fuzzy model-name matching.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


COLORS = ("#C8704F", "#557C9E", "#B58732", "#8B6B91", "#4E7E65")
DISPLAY_LABEL = {"USD": "USD", "CNY": "RMB", "JPY": "JPY"}


def _utc_text(value: datetime | None) -> str:
    current = (value or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return current.isoformat().replace("+00:00", "Z")


def _clean_text(value: Any, code: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise ValueError(code)
    return text


def build_shared_context(
    normalized_profiles: Iterable[Mapping[str, Any]],
    *,
    preferred_currency: str,
    fx_snapshot: Mapping[str, Any],
    settings_revision: str | int,
    local_models_revision: str | int | None,
    leaderboard_catalog_revision: str | int | None,
    revision: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    currency = str(preferred_currency or "").upper()
    if currency not in DISPLAY_LABEL:
        raise ValueError("MODEL_COMMERCE_CURRENCY_UNSUPPORTED")
    status = str(fx_snapshot.get("status") or "")
    if status not in {"AVAILABLE", "CACHED", "EXPIRED", "MISSING", "INVALID"}:
        raise ValueError("MODEL_COMMERCE_FX_STATUS_INVALID")
    rates = dict(fx_snapshot.get("units_per_usd") or {})
    if set(rates) != {"USD", "CNY", "JPY"} or rates.get("USD") != 1:
        raise ValueError("MODEL_COMMERCE_FX_RATE_SET_INVALID")

    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(normalized_profiles):
        if not isinstance(raw, Mapping):
            raise ValueError("MODEL_COMMERCE_PROFILE_INVALID")
        profile_ref = _clean_text(raw.get("profile_ref"), "MODEL_COMMERCE_PROFILE_REF_INVALID", 192)
        if profile_ref in seen:
            raise ValueError("MODEL_COMMERCE_PROFILE_REF_DUPLICATE")
        seen.add(profile_ref)
        kind = str(raw.get("profile_kind") or "")
        if kind not in {"API", "CLI", "LOCAL"}:
            raise ValueError("MODEL_COMMERCE_PROFILE_KIND_INVALID")
        leaderboard_key = raw.get("leaderboard_model_key")
        if leaderboard_key is not None:
            leaderboard_key = _clean_text(leaderboard_key, "MODEL_COMMERCE_LEADERBOARD_KEY_INVALID", 192)
        settings_key = raw.get("settings_profile_key")
        local_key = raw.get("local_model_key")
        if kind in {"API", "CLI"} and not settings_key:
            raise ValueError("MODEL_COMMERCE_SETTINGS_BINDING_MISSING")
        if kind == "LOCAL" and not local_key:
            raise ValueError("MODEL_COMMERCE_LOCAL_BINDING_MISSING")
        models.append({
            "profile_ref": profile_ref,
            "profile_kind": kind,
            "provider_label": _clean_text(raw.get("provider_label"), "MODEL_COMMERCE_PROVIDER_LABEL_INVALID", 80),
            "model_label": _clean_text(raw.get("model_label"), "MODEL_COMMERCE_MODEL_LABEL_INVALID", 96),
            "model_id": _clean_text(raw.get("model_id"), "MODEL_COMMERCE_MODEL_ID_INVALID", 160),
            "availability": str(raw.get("availability") or "AVAILABLE"),
            "leaderboard_model_key": leaderboard_key,
            "source_bindings": {
                "settings_profile_key": str(settings_key) if settings_key is not None else None,
                "local_model_key": str(local_key) if local_key is not None else None,
            },
            "color": str(raw.get("color") or COLORS[index % len(COLORS)]),
        })
        if models[-1]["availability"] not in {"AVAILABLE", "DISABLED", "UNAVAILABLE"}:
            raise ValueError("MODEL_COMMERCE_AVAILABILITY_INVALID")

    return {
        "schema_version": "SharedModelCommerceContext-v1",
        "generated_at": _utc_text(now),
        "revision": _clean_text(revision, "MODEL_COMMERCE_REVISION_INVALID", 160),
        "settings_revision": settings_revision,
        "local_models_revision": local_models_revision,
        "leaderboard_catalog_revision": leaderboard_catalog_revision,
        "display_currency": currency,
        "display_currency_label": DISPLAY_LABEL[currency],
        "fx_snapshot": {
            "id": fx_snapshot.get("id"),
            "source": fx_snapshot.get("source"),
            "rate_date": fx_snapshot.get("rate_date"),
            "fetched_at": fx_snapshot.get("fetched_at"),
            "status": status,
            "units_per_usd": {key: rates[key] for key in ("USD", "CNY", "JPY")},
        },
        "models": models,
    }


__all__ = ["build_shared_context"]
