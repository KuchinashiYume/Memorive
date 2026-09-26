"""Immutable local price catalog.  No network or live supplier lookup exists."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..contracts import AccountingContractError, PriceQuery, parse_datetime


ALLOWED_COMPONENTS = {"input", "output", "cache_read", "cache_write"}


@dataclass(frozen=True)
class PriceResolution:
    status: str
    tariff: dict[str, Any] | None
    unavailable_reasons: tuple[str, ...]
    catalog_sha256: str


class PriceCatalog:
    def __init__(self, raw: dict[str, Any], *, content_sha256: str):
        self.raw = raw
        self.content_sha256 = content_sha256
        self._validate()

    @classmethod
    def load(cls, path: Path | str) -> "PriceCatalog":
        source = Path(path)
        data = source.read_bytes()
        try:
            raw = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AccountingContractError("price catalog must be canonical UTF-8 JSON") from exc
        return cls(raw, content_sha256=hashlib.sha256(data).hexdigest())

    def _validate(self) -> None:
        if self.raw.get("schema_version") != "PriceCatalog-v1":
            raise AccountingContractError("unsupported price catalog schema")
        for name in ("snapshot_id", "revision", "source", "profiles"):
            if name not in self.raw:
                raise AccountingContractError(f"price catalog missing {name}")
        seen: set[tuple[str, str, str, str, str, str]] = set()
        for tariff in self.raw["profiles"]:
            required = {
                "profile_id", "revision", "returned_model", "currency", "region",
                "service_tier", "effective_from", "effective_until", "context_min_tokens",
                "context_max_tokens", "rates_per_million", "source_ref",
            }
            missing = sorted(required - set(tariff))
            if missing:
                raise AccountingContractError(f"tariff missing fields: {missing}")
            parse_datetime(tariff["effective_from"])
            if tariff["effective_until"] is not None:
                if parse_datetime(tariff["effective_until"]) <= parse_datetime(tariff["effective_from"]):
                    raise AccountingContractError("tariff effective interval is empty")
            rates = tariff["rates_per_million"]
            if set(rates) != ALLOWED_COMPONENTS:
                raise AccountingContractError("tariff must declare all four components, using null when unavailable")
            for component, rate in rates.items():
                if rate is not None and (not isinstance(rate, str) or not rate):
                    raise AccountingContractError(f"{component} rate must be decimal string or null")
            key = (
                tariff["profile_id"], tariff["revision"], tariff["returned_model"],
                tariff["currency"], tariff["region"], tariff["service_tier"],
                tariff["effective_from"], tariff["effective_until"],
            )
            if key in seen:
                raise AccountingContractError(f"ambiguous duplicate tariff key: {key}")
            seen.add(key)
        profile_keys = {
            (item["profile_id"], item["revision"], item["returned_model"])
            for item in self.raw["profiles"]
        }
        for model_id, reference in (self.raw.get("legacy_model_to_price_profile") or {}).items():
            if not isinstance(reference, dict) or (
                reference.get("profile_id"), reference.get("revision"), model_id
            ) not in profile_keys:
                raise AccountingContractError(f"legacy price-profile reference is unresolved: {model_id}")

    def resolve(self, query: PriceQuery) -> PriceResolution:
        observed = parse_datetime(query.observed_at)
        candidates: list[dict[str, Any]] = []
        rejection_codes: set[str] = set()
        for tariff in self.raw["profiles"]:
            if tariff["profile_id"] != query.price_profile_id:
                rejection_codes.add("PRICE_PROFILE_ID_NOT_FOUND")
                continue
            if tariff["revision"] != query.price_profile_revision:
                rejection_codes.add("PRICE_PROFILE_REVISION_NOT_FOUND")
                continue
            if tariff["returned_model"] != query.returned_model:
                rejection_codes.add("RETURNED_MODEL_PRICE_KEY_MISMATCH")
                continue
            if tariff["currency"] != query.currency:
                rejection_codes.add("CURRENCY_MISMATCH")
                continue
            if tariff["region"] not in {query.region, "GLOBAL"}:
                rejection_codes.add("REGION_NOT_PRICED")
                continue
            if tariff["service_tier"] != query.service_tier:
                rejection_codes.add("SERVICE_TIER_NOT_PRICED")
                continue
            start = parse_datetime(tariff["effective_from"])
            end = parse_datetime(tariff["effective_until"]) if tariff["effective_until"] else None
            if observed < start or (end is not None and observed >= end):
                rejection_codes.add("PRICE_NOT_EFFECTIVE_AT_OBSERVED_TIME")
                continue
            if query.context_tokens < tariff["context_min_tokens"]:
                rejection_codes.add("CONTEXT_TIER_NOT_PRICED")
                continue
            maximum = tariff["context_max_tokens"]
            if maximum is not None and query.context_tokens >= maximum:
                rejection_codes.add("CONTEXT_TIER_NOT_PRICED")
                continue
            candidates.append(tariff)
        if not candidates:
            return PriceResolution(
                "NOT_ASSESSED",
                None,
                tuple(sorted(rejection_codes or {"PRICE_PROFILE_NOT_FOUND"})),
                self.content_sha256,
            )
        if len(candidates) != 1:
            raise AccountingContractError("PRICE_AMBIGUOUS: overlapping effective tariff windows")
        chosen = dict(candidates[0])
        chosen["snapshot_id"] = self.raw["snapshot_id"]
        chosen["catalog_revision"] = self.raw["revision"]
        return PriceResolution("RESOLVED", chosen, (), self.content_sha256)

    def profile_ref_for_model(self, returned_model: str) -> tuple[str, str] | None:
        mapping = self.raw.get("legacy_model_to_price_profile") or {}
        value = mapping.get(returned_model)
        if not isinstance(value, dict):
            return None
        profile_id = value.get("profile_id")
        revision = value.get("revision")
        if not profile_id or not revision:
            return None
        return profile_id, revision
