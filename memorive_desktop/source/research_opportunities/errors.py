"""Fail-closed exceptions for the RESEARCH-OPPORTUNITIES successor003 candidate."""


class OpportunityAnalysisError(ValueError):
    """Base class for deterministic contract rejections."""


class ContractRejected(OpportunityAnalysisError):
    """An object does not satisfy the frozen successor contract."""


class EvidenceRejected(OpportunityAnalysisError):
    """Evidence cannot be replayed from frozen inputs."""
