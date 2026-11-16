"""The errors of the card review, in two kinds:
- errors that stop the check (an unreadable card, YAML that does not parse, a chunk store that cannot
  be read or belongs to another paper) are raised; callers catch them instead of showing a traceback;
- problems with single anchors (a broken link, an unclear form) are not raised but recorded in
  MechanicalResult.problems.
"""
from __future__ import annotations

from memorive.documents.card_states import DecisionError


class EvidenceReviewError(Exception):
    """The base of the card review's errors."""


class CardUnreadable(EvidenceReviewError):
    """The card file cannot be read (missing, an I/O error)."""


class CardParseError(EvidenceReviewError):
    """The card's front matter is missing, does not parse as YAML, or is not a mapping."""


class ChunkStoreError(EvidenceReviewError):
    """The chunk store cannot be found, read or parsed, or its paper_id differs from the card's."""


class BatchContractExhausted(DecisionError):
    """A stage of the whole card used up its one retry for technical output; the caller must not run
    the whole card again."""


class OwnershipFailClosed(EvidenceReviewError):
    """Stopped for data ownership: the card or its data is not clearly the user's own (entrusted,
    missing, unknown) and no local review model is ready. Such data is never sent to an overseas cloud
    model, neither for transcription checks nor for judgments, and the manual path cannot bypass this.
    Raised; not a DecisionError, so no retry and no admission.
    Note: memorive.research.analysis.OwnershipFailClosed (an AnalysisError) has the same name but is a
    different class in another hierarchy. Code that runs both the analysis and the card review must
    catch both. Merging them into one shared ownership error and check is open work."""
