"""The sessions page: chat sessions imported from files or captured from a browser, and their refinement.

The window reaches it through sessions.* methods (SessionsProductController in product_controller.py).
edge_visible_ui.py reads the visible page of a chat site in Edge; refinement runs through the
channel in memorive_workflow_bridge.
"""

from .product_controller import REAL_CHANNELS, SESSIONS_METHODS, SessionProductError, SessionsProductController

__all__ = ['REAL_CHANNELS', 'SESSIONS_METHODS', 'SessionProductError', 'SessionsProductController']
