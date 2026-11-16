"""The application core the desktop service is built on: the facade, diagnostics, support bundle and IPC.

facade.py is the application facade the service calls; ipc_auth.py authenticates the loopback
connection; diagnostics.py and support_bundle.py build the diagnostic view and the support bundle;
import_binding.py binds imported files into the inbox. adapters.py, a read-only adapter for card
review status, is not reached from the app's own code. The job stores, contracts, errors and
credentials are in memorive/platform (jobs/, errors.py, credentials.py); the modules here move to the
desktop and documents areas next.
"""

from memorive.platform.jobs.capabilities import build_capability_snapshot, evaluate_preflight
from memorive.platform.jobs.contracts import JobControlState
from memorive.platform.credentials import WindowsCredentialStore
from .diagnostics import DiagnosticCore
from memorive.platform.jobs.durable_store import DurableJobStore
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
from memorive.platform.jobs.workflow_definitions import WorkflowDefinitionProvider
__all__ = [*__all__, 'WorkflowDefinitionProvider']
