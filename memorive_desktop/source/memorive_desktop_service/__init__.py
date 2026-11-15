"""DESKTOP-SERVICE desktop Application Service, IPC, locator and Control Store layer."""

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
