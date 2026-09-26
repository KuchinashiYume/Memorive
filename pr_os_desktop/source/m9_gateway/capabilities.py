"""Provider-neutral frozen capability registry.

Some providers expose token ceilings in their live model metadata API; others
only expose model visibility.  Adapters may combine a successful live probe
with this versioned registry, but callers never branch on provider names.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path


DEFAULT_REGISTRY_PATH = Path(__file__).with_name("model_capabilities.json")


def _positive_int(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def registry_capabilities(
    provider: str,
    model_id: str,
    *,
    path: Path = DEFAULT_REGISTRY_PATH,
) -> dict:
    """Return one validated registry entry without sharing mutable state."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        entry = raw["providers"][provider][model_id]
    except (KeyError, TypeError):
        raise ValueError(
            f"No frozen capability registry entry for {provider}/{model_id}"
        ) from None
    if not isinstance(entry, dict):
        raise ValueError(f"Invalid capability registry entry for {provider}/{model_id}")
    result = deepcopy(entry)
    result["max_output_tokens"] = _positive_int(
        result.get("max_output_tokens"),
        f"{provider}/{model_id} max_output_tokens",
    )
    result["max_input_tokens"] = _positive_int(
        result.get("max_input_tokens"),
        f"{provider}/{model_id} max_input_tokens",
    )
    if not isinstance(result.get("capabilities"), dict):
        raise ValueError(f"Invalid capabilities object for {provider}/{model_id}")
    return result
