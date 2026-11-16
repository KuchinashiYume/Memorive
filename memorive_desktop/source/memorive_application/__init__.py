"""An integration harness: runs the real application facade (memorive_app) behind a loopback
transport, with an isolated fixture and a fault matrix (orchestrator.py, faults.py).

Nothing in the app or the public tests calls it; it is run by hand to exercise the facade end to
end. It is not a second application layer.
"""

from .fixture import IsolatedFixture, build_isolated_fixture
from .transport import LoopbackApplicationService

__all__ = [
    "IsolatedFixture",
    "LoopbackApplicationService",
    "build_isolated_fixture",
]
