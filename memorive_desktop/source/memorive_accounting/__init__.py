"""The accounting panel: what the model calls cost, by model profile, over the last 7 or 30 days or
this month, in the currency the user chose.

The window reaches it through accounting.* methods and models.get_commerce_context_v1
(AccountingProductController in product_controller.py). projection.py builds the panel from the
recorded call attempts; commerce_context.py builds the model price context. It only reads.
"""

from .product_controller import ACCOUNTING_METHODS, AccountingProductController

__all__ = ["ACCOUNTING_METHODS", "AccountingProductController"]
