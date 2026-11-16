"""Cost accounting of model calls: a price catalog (PriceCatalog), the usage a provider reports, and
the estimate made from them (AccountingKernel). cost.py records each ModelGateway call with it.

The model is named by the caller's alias binding (ModelAliasBinding); this package never
makes one up.
"""

from memorive.models.gateway.accounting.calculator import DecimalCalculator
from memorive.models.gateway.accounting.contracts import (
    AccountingContractError,
    AccountingEnvelope,
    PriceQuery,
    SettlementObservation,
    UsageObservation,
)
from memorive.models.gateway.accounting.kernel import AccountingKernel
from memorive.models.gateway.accounting.pricing.catalog import PriceCatalog, PriceResolution

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
from memorive.models.gateway.accounting.reconciliation import DiscrepancyQueue
from memorive.models.gateway.accounting.reconciliation import reconcile_observations
__all__ = [*__all__, 'DiscrepancyQueue', 'reconcile_observations']
