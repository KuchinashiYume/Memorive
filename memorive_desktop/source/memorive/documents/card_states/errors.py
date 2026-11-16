"""The errors of the card state machine. Callers catch them and show the message; any other exception
is a likely bug and is raised, never swallowed.
"""
from __future__ import annotations


class KnowledgeAdmissionError(Exception):
    """An error of the card state machine."""


class AdmissionBlocked(KnowledgeAdmissionError):
    """An analysis admission request does not meet its conditions."""


class RealStateTransitionForbidden(KnowledgeAdmissionError):
    """The build-only analysis admission adapter was used for a real card state change."""


class IllegalTransitionError(KnowledgeAdmissionError):
    """A transition that is never allowed; the state stays as it was. Active without pending first,
    back from active to pending without grounds, a target outside the three review states (archive
    belongs to the registry's usage, another axis), a state to itself, and the like."""


class TransitionNotEnabledError(KnowledgeAdmissionError):
    """A transition the state machine allows (is_legal=True) but the app does not make, typically active
    to quarantined after the fact: the mechanism is here, the trigger would come from the registry.
    Unlike IllegalTransitionError it is allowed in principle; it is still never made silently."""


class TriggerNotAcceptedError(KnowledgeAdmissionError):
    """Not raised any more: transition() accepts every Trigger member, and anything else is a
    MissingBasisError. Kept so that code importing it keeps working."""


class MissingBasisError(KnowledgeAdmissionError):
    """A transition without grounds: reason is empty, or trigger is not a Trigger member (a string such
    as "RETRIEVAL_WEIGHTING_UNPENALIZE", say). A state never changes without its recorded grounds."""


class CardStateError(KnowledgeAdmissionError):
    """The card file cannot carry its state; nothing is guessed and the file is not touched. The front
    matter is not --- ... --- or not a mapping; review_status is missing or not one of the three states;
    the front matter has no single plain, unquoted review_status line; the temporary file reads back
    another state; or source_anchor.paper_id is missing, empty or of the wrong type (read_data_id never
    falls back to the file name)."""


class KnowledgeAdmissionLogAppendError(KnowledgeAdmissionError):
    """The admission log could not be written: preflight() found the folder missing or read-only
    (the card is not moved), or appending the line failed after the card was moved. The second
    case is raised, not hidden, so the missing log line shows."""


class DecisionError(KnowledgeAdmissionError):
    """The decision step itself failed (a transient error, a failed model call), which can be retried;
    not a verdict of "failed". auto_review retries the step on it and hands over to a person after too
    many in a row. A FAIL verdict is a normal result (quarantined), not a retry. Only this class, raised
    by the decision source, means a retry; any other exception is raised, so a real bug never passes as
    a transient error."""


class DecisionContractError(KnowledgeAdmissionError):
    """decision_source returned something that is not a Verdict. A bug, raised at once: no retry, no
    quarantine, no transition, and never treated as PASS or FAIL (a string "pass" must not pass)."""
