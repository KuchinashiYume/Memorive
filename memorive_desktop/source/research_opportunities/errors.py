"""Fail-closed exceptions for the RESEARCH-OPPORTUNITIES successor003 candidate."""


class OpportunityAnalysisError(ValueError):
    """Base class for deterministic contract rejections."""


class ContractRejected(OpportunityAnalysisError):
    """An object does not satisfy the frozen successor contract."""


class SourceRejected(OpportunityAnalysisError):
    """A source, trigger, policy, or bounded-pool input is ineligible."""


class EvidenceRejected(OpportunityAnalysisError):
    """Evidence cannot be replayed from frozen inputs."""


class CandidateRejected(OpportunityAnalysisError):
    """An OpportunityCandidate violates identity, linkage, or state rules."""
