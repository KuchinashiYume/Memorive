"""Fail-closed errors for P03/T06 controlled knowledge feedback."""


class KnowledgeFeedbackError(ValueError):
    """Base class for deterministic T06 contract failures."""


class ContractRejected(KnowledgeFeedbackError):
    """A supplied object does not satisfy its exact frozen contract."""


class SourceIneligible(KnowledgeFeedbackError):
    """The frozen source snapshot cannot support a derived candidate."""


class DecisionRejected(KnowledgeFeedbackError):
    """A human-decision receipt is missing, stale, forged, or out of scope."""


class LineageRejected(KnowledgeFeedbackError):
    """Lineage is self-referential, cyclic, dangling, or otherwise unsafe."""


class ControlledIngestBlocked(KnowledgeFeedbackError):
    """A real ingest or activation was attempted outside its authority."""

