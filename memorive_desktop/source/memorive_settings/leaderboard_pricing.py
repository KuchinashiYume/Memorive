"""Price selection for the leaderboard.

The channel catalog quote is the adaptive-capacity baseline.  Endpoint evidence may
replace it with a verified non-promotional quote, but missing or inconclusive
verification must never erase a numeric catalog price.
"""
from copy import deepcopy
import math

from .reference_pricing import COMPATIBLE_POLICIES, quote_binding


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def select_leaderboard_price(channel_price, pricing, operational, model_id):
    """Return ``(selected_price, evidence)`` without inventing a price.

    A verified endpoint quote is allowed to remove an observed promotion.
    Every other outcome retains the exact numeric channel quote from adaptive-capacity.
    ``None`` is returned only when the channel itself has no usable quote.
    """
    channel = deepcopy(channel_price)
    regular = operational.get("regular_pricing") or {}
    verified = False
    try:
        verified = (
            channel is not None
            and regular.get("policy") in COMPATIBLE_POLICIES
            and regular.get("status") == "VERIFIED"
            and regular.get("model_id") == model_id
            and regular.get("catalog_quote_sha256") == quote_binding(pricing)
        )
    except (TypeError, ValueError):
        verified = False

    regular_input = _number(regular.get("prompt"))
    regular_output = _number(regular.get("completion"))
    verified = verified and all(
        value is not None
        and value >= 0
        and _number(value * 1_000_000) is not None
        for value in (regular_input, regular_output)
    )

    if verified:
        selected = deepcopy(channel)
        selected.update(
            input=regular_input * 1_000_000,
            output=regular_output * 1_000_000,
            basis="NON_PROMOTIONAL_ENDPOINT_VERIFIED",
            verification=deepcopy(regular),
        )
        status = "VERIFIED"
        reason = "PROMOTION_REMOVED" if regular.get("promotion_removed") else "CHANNEL_QUOTE_CONFIRMED_REGULAR"
    elif channel is not None:
        selected = deepcopy(channel)
        selected["basis"] = "CHANNEL_CATALOG_QUOTE_NO_PROMOTION_EVIDENCE"
        status = "CHANNEL_QUOTE_RETAINED"
        reason = regular.get("reason") or "REFERENCE_PRICE_NOT_CHECKED"
    else:
        selected = None
        status = "SOURCE_PRICE_MISSING"
        reason = regular.get("reason") or "CHANNEL_PRICE_MISSING"

    return selected, {
        "channel_quote": channel,
        "regular_price_status": status,
        "regular_price_reason": str(reason)[:160],
        "promotion_removed": bool(verified and regular.get("promotion_removed")),
    }
