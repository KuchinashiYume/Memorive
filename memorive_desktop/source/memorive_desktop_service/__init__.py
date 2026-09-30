"""DESKTOP-SERVICE desktop Application Service, IPC, locator and Control Store layer."""

from .adapters import FakeFacadeAdapter, RealFacadeAdapter, REQUIRED_D38_METHODS
from .client import ApplicationServiceClient
from .command_outbox import DesktopCommandOutbox
from .control_store import ControlStore
from .locator import StableLocator, StableLocatorResolver
from .service import ApplicationServiceHost
from .single_instance import SingleInstanceCoordinator
from .watch import EventCursorReconciler


__all__ = [
    "ApplicationServiceClient",
    "ApplicationServiceHost",
    "ControlStore",
    "DesktopCommandOutbox",
    "EventCursorReconciler",
    "FakeFacadeAdapter",
    "REQUIRED_D38_METHODS",
    "RealFacadeAdapter",
    "SingleInstanceCoordinator",
    "StableLocator",
    "StableLocatorResolver",
]
