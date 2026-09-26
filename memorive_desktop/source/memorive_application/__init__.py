"""APPLICATION-RUNTIME isolated real-facade integration surface."""

from .fixture import IsolatedFixture, build_isolated_fixture
from .transport import LoopbackApplicationService

__all__ = [
    "IsolatedFixture",
    "LoopbackApplicationService",
    "build_isolated_fixture",
]
