"""P06/T03 offline accounting kernel candidate.

Model identity remains owned by the P05 ModelAliasBinding contract.  This
package consumes an immutable binding reference and never invents aliases.
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
from .reconciliation import DiscrepancyQueue, reconcile_observations

__all__ = [
    "AccountingContractError",
    "AccountingEnvelope",
    "AccountingKernel",
    "DecimalCalculator",
    "DiscrepancyQueue",
    "PriceCatalog",
    "PriceQuery",
    "PriceResolution",
    "SettlementObservation",
    "UsageObservation",
    "reconcile_observations",
]
