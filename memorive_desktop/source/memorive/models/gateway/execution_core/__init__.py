"""Running model work in isolated local processes: job workspaces (JobWorkspace), the isolated runner
(IsolatedJobRunner), channel selection, handoff and output contracts, and the command-line model
adapters (adapters/). Document processing (the isolated PDF engine, Marker, vector generation), the
local embedding and reranker components and the model channels use it.

The package is deliberately separate from the existing ``ModelGateway`` path.
Importing it performs no discovery, authentication, process, or network action.
"""

from memorive.models.gateway.execution_core.contracts import ContractViolation, canonical_sha256, validate_initialization_object
from memorive.models.gateway.execution_core.core import ExecutionCore, ExecutionOutcome
from memorive.models.gateway.execution_core.handoff import HANDOFF_SCHEMA_VERSION, HandoffContract, HandoffViolation
from memorive.models.gateway.execution_core.normalization import OutputNormalizer
from memorive.models.gateway.execution_core.registry import AdapterRegistry, RegistryViolation
from memorive.models.gateway.execution_core.routing import ChannelSelector, SelectionOutcome
from memorive.models.gateway.execution_core.runner import IsolatedJobRunner, ProcessRun, ProcessSpec
from memorive.models.gateway.execution_core.workspace import JobWorkspace, WorkspaceViolation

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

