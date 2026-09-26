"""Call-before-cost reservation and exact usage costing for the isolated reviewer run."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

from .accounting.calculator import DecimalCalculator


class BudgetBlocked(RuntimeError):
    code = "BUDGET_PRE_RESERVATION_BLOCKED"


def _count(value: int | float) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("token count must be a non-negative integer")
    return value


def calculate_anthropic_cost(
    *,
    input_tokens: int | float,
    output_tokens: int | float,
    reasoning_tokens: int | float,
    cache_write_tokens: int | float,
    cache_read_tokens: int | float,
    input_per_mtok: float,
    output_per_mtok: float,
    cache_write_per_mtok: float,
    cache_read_per_mtok: float,
    cny_per_usd: float,
    batch_discount: float = 1.0,
) -> dict[str, float]:
    plain_input = _count(input_tokens)
    output = _count(output_tokens)
    reasoning = _count(reasoning_tokens)
    cache_write = _count(cache_write_tokens)
    cache_read = _count(cache_read_tokens)
    if reasoning > output:
        raise ValueError("reasoning tokens are an output-token subset")
    if not 0 < batch_discount <= 1:
        raise ValueError("batch_discount must be in (0, 1]")
    usd_text = DecimalCalculator().direct_components(
        counts={
            "input": plain_input,
            "output": output,
            "cache_write": cache_write,
            "cache_read": cache_read,
        },
        rates_per_million={
            "input": str(input_per_mtok),
            "output": str(output_per_mtok),
            "cache_write": str(cache_write_per_mtok),
            "cache_read": str(cache_read_per_mtok),
        },
        multiplier=str(batch_discount),
    )
    usd = Decimal(usd_text)
    cny = usd * Decimal(str(cny_per_usd))
    return {"usd": float(usd), "cny": float(cny)}


def conservative_call_upper_cny(
    *,
    input_token_upper: int,
    max_output_tokens: int,
    input_per_mtok_cny: float,
    output_per_mtok_cny: float,
    cache_write_token_upper: int = 0,
    cache_write_per_mtok_cny: float = 0.0,
    service_multiplier: float = 1.0,
) -> float:
    if service_multiplier < 1:
        raise ValueError("service multiplier must not reduce a conservative bound")
    value = DecimalCalculator().direct_components(
        counts={
            "input": _count(input_token_upper),
            "output": _count(max_output_tokens),
            "cache_write": _count(cache_write_token_upper),
        },
        rates_per_million={
            "input": str(input_per_mtok_cny),
            "output": str(output_per_mtok_cny),
            "cache_write": str(cache_write_per_mtok_cny),
        },
        multiplier=str(service_multiplier),
    )
    return float(Decimal(value))


@dataclass
class BudgetLedger:
    limit_cny: float
    spent_cny: float = 0.0
    reservations: dict[str, float] = field(default_factory=dict)

    @property
    def reserved_cny(self) -> float:
        return sum(self.reservations.values())

    @property
    def available_cny(self) -> float:
        return self.limit_cny - self.spent_cny - self.reserved_cny

    def reserve(self, *, task_id: str, conservative_upper_cny: float) -> dict[str, float | str]:
        if not task_id or task_id in self.reservations:
            raise ValueError("task_id must be new and non-empty")
        if conservative_upper_cny < 0:
            raise ValueError("reservation must be non-negative")
        if self.spent_cny + self.reserved_cny + conservative_upper_cny > self.limit_cny + 1e-12:
            raise BudgetBlocked(
                f"{BudgetBlocked.code}: spent={self.spent_cny:.6f}, reserved={self.reserved_cny:.6f}, "
                f"next_upper={conservative_upper_cny:.6f}, limit={self.limit_cny:.6f} CNY"
            )
        self.reservations[task_id] = conservative_upper_cny
        return {"task_id": task_id, "reserved_cny": conservative_upper_cny, "available_after_cny": self.available_cny}

    def settle(self, *, task_id: str, actual_cny: float) -> dict[str, float | str]:
        reserved = self.reservations.pop(task_id)
        if actual_cny < 0:
            raise ValueError("actual cost must be non-negative")
        self.spent_cny += actual_cny
        if self.spent_cny > self.limit_cny + 1e-12:
            raise BudgetBlocked(f"actual cost exceeded hard limit: {self.spent_cny:.6f}>{self.limit_cny:.6f} CNY")
        return {"task_id": task_id, "reserved_cny": reserved, "actual_cny": actual_cny, "spent_cny": self.spent_cny}

    def release(self, *, task_id: str) -> float:
        return self.reservations.pop(task_id)
