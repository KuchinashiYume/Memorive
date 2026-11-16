"""The application core the desktop service is built on: jobs, credentials, diagnostics, IPC.

facade.py is the application facade the service calls; durable_store.py keeps jobs, events and
idempotency keys in SQLite; credentials.py stores secrets in Windows Credential Manager by reference;
ipc_auth.py authenticates the loopback connection; diagnostics.py and support_bundle.py build the
diagnostic view and the support bundle. adapters.py, a read-only adapter for card review status, is
not reached from the app's own code.
"""

from .capabilities import build_capability_snapshot, evaluate_preflight
from .contracts import JobControlState
from .credentials import WindowsCredentialStore
from .diagnostics import DiagnosticCore
from .durable_store import DurableJobStore
from .facade import ApplicationFacade
from .import_binding import FileImportBinder
from .ipc_auth import LoopbackPeerAuthenticator
from .support_bundle import SupportBundleBuilder


__all__ = [
    "ApplicationFacade",
    "DiagnosticCore",
    "DurableJobStore",
    "FileImportBinder",
    "JobControlState",
    "LoopbackPeerAuthenticator",
    "SupportBundleBuilder",
    "WindowsCredentialStore",
    "build_capability_snapshot",
    "evaluate_preflight",
]

# Names the package offered before; restored with the code behind them.
from .workflow import WorkflowDefinitionProvider
__all__ = [*__all__, 'WorkflowDefinitionProvider']
