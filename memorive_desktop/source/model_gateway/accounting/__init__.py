"""Cost accounting of model calls: a price catalog (PriceCatalog), the usage a provider reports, and
the estimate made from them (AccountingKernel). cost.py records each ModelGateway call with it.

The model is named by the caller's alias binding (ModelAliasBinding); this package never
makes one up.
"""

from .calculator import DecimalCalculator
from .contracts import (
    AccountingContractError,
    AccountingEnvelope,
    PriceQuery,
    SettlementObservation,
    UsageObservation,
)
from .kernel import AccountingKernel
from .pricing.catalog import PriceCatalog, PriceResolution

__all__ = [
    "AccountingContractError",
    "AccountingEnvelope",
    "AccountingKernel",
    "DecimalCalculator",
    "PriceCatalog",
    "PriceQuery",
    "PriceResolution",
    "SettlementObservation",
    "UsageObservation",
]

# Names the package offered before; restored with the code behind them.
from .reconciliation import DiscrepancyQueue
from .reconciliation import reconcile_observations
__all__ = [*__all__, 'DiscrepancyQueue', 'reconcile_observations']
