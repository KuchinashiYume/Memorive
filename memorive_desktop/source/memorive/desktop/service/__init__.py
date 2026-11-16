"""The background service the desktop window talks to.

service_main.py starts it (python -m memorive.desktop.service) and builds the settings and inbox
controllers, the review service and the application facade, and serves them over IPC; protocol.py and client.py are the IPC between window and
service; control_store.py keeps window state; single_instance.py keeps one window per user;
locator.py names jobs and artifacts in links. facade.py is the application facade the service serves;
diagnostics.py and support_bundle.py build the diagnostic view and the support bundle; ipc_auth.py
authenticates the loopback connection; capability_adapters.py feeds the capability snapshot.
schemas/ holds the facade's contract schemas.
"""

from memorive.desktop.service.adapters import RealFacadeAdapter, REQUIRED_FACADE_METHODS
from memorive.desktop.service.client import ApplicationServiceClient
from memorive.desktop.service.control_store import ControlStore
from memorive.desktop.service.locator import StableLocator, StableLocatorResolver
from memorive.desktop.service.service import ApplicationServiceHost
from memorive.desktop.service.single_instance import SingleInstanceCoordinator


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
from memorive.desktop.service.adapters import FakeFacadeAdapter
from memorive.desktop.service.adapters import REQUIRED_FACADE_METHODS
from memorive.desktop.service.watch import EventCursorReconciler
__all__ = [*__all__, 'EventCursorReconciler', 'FakeFacadeAdapter', 'REQUIRED_FACADE_METHODS']
