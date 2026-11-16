"""The background service the desktop window talks to.

service_main.py starts it (python -m memorive_desktop_service) and builds the settings and inbox
controllers, the review service and the application facade, and serves them over IPC; protocol.py and client.py are the IPC between window and
service; control_store.py keeps window state; single_instance.py keeps one window per user;
locator.py names jobs and artifacts in links.
"""

from .adapters import RealFacadeAdapter, REQUIRED_FACADE_METHODS
from .client import ApplicationServiceClient
from .control_store import ControlStore
from .locator import StableLocator, StableLocatorResolver
from .service import ApplicationServiceHost
from .single_instance import SingleInstanceCoordinator


__all__ = [
    "ApplicationServiceClient",
    "ApplicationServiceHost",
    "ControlStore",
    "REQUIRED_FACADE_METHODS",
    "RealFacadeAdapter",
    "SingleInstanceCoordinator",
    "StableLocator",
    "StableLocatorResolver",
]

# Names the package offered before; restored with the code behind them.
from .adapters import FakeFacadeAdapter
from .adapters import REQUIRED_FACADE_METHODS
from .watch import EventCursorReconciler
__all__ = [*__all__, 'EventCursorReconciler', 'FakeFacadeAdapter', 'REQUIRED_FACADE_METHODS']
