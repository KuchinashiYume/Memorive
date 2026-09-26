"""Explicit model-to-route bindings for leaderboard prices."""
from copy import deepcopy
from functools import lru_cache
import json
import math
from pathlib import Path
import re


_IDENTITY = re.compile(r"[a-z][a-z0-9-]{0,63}")
_SHA256 = re.compile(r"[a-f0-9]{64}")


def _valid_text(value, *, limit=512):
    return (
        isinstance(value, str)
        and 0 < len(value) <= limit
        and all(ord(char) >= 32 for char in value)
    )


def _validate_registry(payload):
    if payload.get("schema") != "MemoriveLeaderboardPriceRegistry-v1":
        raise ValueError("LEADERBOARD_PRICE_REGISTRY_SCHEMA_INVALID")
    if payload.get("policy") != "EXPLICIT_SAME_CREATOR_MODEL_BINDING_NO_RUNTIME_FUZZY_MATCH":
        raise ValueError("LEADERBOARD_PRICE_REGISTRY_POLICY_INVALID")
    for name in ("litellm_catalog", "modelsdev_catalog"):
        source = payload.get(name)
        if (
            not isinstance(source, dict)
            or set(source) != {"source", "sha256"}
            or not _valid_text(source.get("source"), limit=2048)
            or not source["source"].startswith("https://")
            or not isinstance(source.get("sha256"), str)
            or not _SHA256.fullmatch(source["sha256"])
        ):
            raise ValueError("LEADERBOARD_PRICE_CATALOG_PROVENANCE_INVALID")
    for source in payload.get("catalog_additions", []):
        if (
            not isinstance(source, dict)
            or set(source) != {"source", "sha256"}
            or not _valid_text(source.get("source"), limit=2048)
            or not source["source"].startswith("https://")
            or not isinstance(source.get("sha256"), str)
            or not _SHA256.fullmatch(source["sha256"])
        ):
            raise ValueError("LEADERBOARD_PRICE_CATALOG_PROVENANCE_INVALID")
    aliases = {}
    for row in payload.get("openrouter_aliases", []):
        if (
            not isinstance(row, dict)
            or set(row) != {"vendor", "base_model", "model_id"}
            or not isinstance(row.get("vendor"), str)
            or not _IDENTITY.fullmatch(row["vendor"])
            or not _valid_text(row.get("base_model"))
            or not _valid_text(row.get("model_id"))
        ):
            raise ValueError("LEADERBOARD_PRICE_ALIAS_INVALID")
        key = (row["vendor"], row["base_model"])
        if key in aliases and aliases[key] != row["model_id"]:
            raise ValueError("LEADERBOARD_PRICE_ALIAS_AMBIGUOUS")
        aliases[key] = row["model_id"]
    fallbacks = {}
    fallbacks_by_model_id = {}
    fallback_fields = {
        "vendor",
        "base_model",
        "input",
        "output",
        "model_id",
        "provider",
        "source_id",
        "source_name",
        "source_url",
        "selection",
    }
    for row in payload.get("public_route_fallbacks", []):
        if (
            not isinstance(row, dict)
            or set(row) != fallback_fields
            or not isinstance(row.get("vendor"), str)
            or not _IDENTITY.fullmatch(row["vendor"])
            or any(
                not _valid_text(row.get(field), limit=2048 if field == "source_url" else 512)
                for field in fallback_fields - {"vendor", "input", "output"}
            )
            or not row["source_url"].startswith("https://")
            or any(
                type(row.get(field)) not in (int, float)
                or not math.isfinite(row[field])
                or row[field] <= 0
                for field in ("input", "output")
            )
        ):
            raise ValueError("LEADERBOARD_PRICE_FALLBACK_INVALID")
        key = (row["vendor"], row["base_model"])
        if key in fallbacks:
            raise ValueError("LEADERBOARD_PRICE_FALLBACK_AMBIGUOUS")
        fallbacks[key] = row
        model_id = row["model_id"]
        if model_id in fallbacks_by_model_id:
            raise ValueError("LEADERBOARD_PRICE_MODEL_ID_AMBIGUOUS")
        fallbacks_by_model_id[model_id] = row
    official = []
    official_keys = set()
    fields = {"vendor", "base_model", "model_id", "display_name", "released_on",
              "source_as_of", "source_name", "source_url", "description",
              "context_length", "max_output_tokens"}
    for row in payload.get("official_models", []):
        if (not isinstance(row, dict) or set(row) != fields
                or not all(isinstance(row.get(key), str) and row[key]
                           for key in fields - {"context_length", "max_output_tokens"})
                or not row["source_url"].startswith("https://")
                or any(value is not None and (type(value) is not int or value <= 0)
                       for value in (row["context_length"], row["max_output_tokens"]))):
            raise ValueError("LEADERBOARD_OFFICIAL_MODEL_INVALID")
        key = (row["vendor"], row["base_model"])
        if key in official_keys:
            raise ValueError("LEADERBOARD_OFFICIAL_MODEL_AMBIGUOUS")
        official_keys.add(key)
        official.append(row)
    return payload, aliases, fallbacks, fallbacks_by_model_id, tuple(official)


@lru_cache(maxsize=1)
def _registry():
    path = Path(__file__).with_name("leaderboard_price_registry.json")
    payload = json.loads(path.read_text("utf-8"))
    return _validate_registry(payload)


def alias_model_id(vendor, base_model):
    return _registry()[1].get((vendor, base_model))


def public_route_price(vendor, base_model):
    payload, _, fallbacks, _, _ = _registry()
    row = fallbacks.get((vendor, base_model))
    return _price(payload, row)


def resolved_public_route_price(vendor, base_model):
    """Resolve one explicit leaderboard identity; never fuzzy-match model names."""
    payload, aliases, fallbacks, by_model_id, _ = _registry()
    row = fallbacks.get((vendor, base_model))
    if row is None:
        model_id = aliases.get((vendor, base_model))
        row = by_model_id.get(model_id) if model_id else None
    return _price(payload, row)


def official_catalog_models():
    return deepcopy(list(_registry()[4]))


def _price(payload, row):
    if row is None:
        return None, {}
    price = {"input":row["input"], "output":row["output"], "currency":"USD",
        "unit":"PER_MILLION_TOKENS", "source_id":row["source_id"],
        "source_name":row["source_name"], "source_url":row["source_url"],
        "retrieved_at":payload["generated_at"], "model_id":row["model_id"],
        "channel":"PUBLIC_PROVIDER_ROUTE_REFERENCE", "scope":"API_SKU_UNIT_PRICE_NOT_TASK_COST",
        "basis":"CURRENT_PUBLIC_ROUTE_REFERENCE_NO_PROMOTION_EVIDENCE"}
    evidence = {"channel_quote":price, "regular_price_status":"PUBLIC_ROUTE_REFERENCE",
        "regular_price_reason":row["selection"], "promotion_removed":False}
    return price, evidence
