"""One facade for call-before quote and post-response observation."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .calculator import DecimalCalculator
from .contracts import AccountingEnvelope, PriceQuery, UsageObservation
from .pricing.catalog import PriceCatalog


class AccountingKernel:
    def __init__(self, catalog: PriceCatalog, *, calculator: DecimalCalculator | None = None):
        self.catalog = catalog
        self.calculator = calculator or DecimalCalculator()

    @classmethod
    def from_catalog_path(cls, path: Path | str) -> "AccountingKernel":
        return cls(PriceCatalog.load(path))

    def quote(
        self,
        query: PriceQuery,
        usage_upper_bound: UsageObservation | None,
        *,
        evidence_refs: Iterable[str] = (),
    ) -> AccountingEnvelope:
        return self._calculate("QUOTE", query, usage_upper_bound, tuple(evidence_refs))

    def observe(
        self,
        query: PriceQuery,
        usage: UsageObservation | None,
        *,
        evidence_refs: Iterable[str] = (),
    ) -> AccountingEnvelope:
        return self._calculate("OBSERVED", query, usage, tuple(evidence_refs))

    def _calculate(
        self,
        stage: str,
        query: PriceQuery,
        usage: UsageObservation | None,
        evidence_refs: tuple[str, ...],
    ) -> AccountingEnvelope:
        resolution = self.catalog.resolve(query)
        return self.calculator.calculate(
            stage=stage,
            query=query,
            usage=usage,
            price=resolution,
            evidence_refs=evidence_refs,
        )
