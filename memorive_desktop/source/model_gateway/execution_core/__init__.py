"""EXECUTION-CORE local execution core.

The package is deliberately separate from the existing ``ModelGateway`` path.
Importing it performs no discovery, authentication, process, or network action.
"""

from .contracts import ContractViolation, canonical_sha256, validate_initialization_object
from .core import ExecutionCore, ExecutionOutcome
from .handoff import HANDOFF_SCHEMA_VERSION, HandoffContract, HandoffViolation
from .normalization import OutputNormalizer
from .registry import AdapterRegistry, RegistryViolation
from .routing import ChannelSelector, SelectionOutcome
from .runner import IsolatedJobRunner, ProcessRun, ProcessSpec
from .workspace import JobWorkspace, WorkspaceViolation

__all__ = [
    "AdapterRegistry",
    "ChannelSelector",
    "ContractViolation",
    "ExecutionCore",
    "ExecutionOutcome",
    "HANDOFF_SCHEMA_VERSION",
    "HandoffContract",
    "HandoffViolation",
    "IsolatedJobRunner",
    "JobWorkspace",
    "OutputNormalizer",
    "ProcessRun",
    "ProcessSpec",
    "RegistryViolation",
    "SelectionOutcome",
    "WorkspaceViolation",
    "canonical_sha256",
    "validate_initialization_object",
]

