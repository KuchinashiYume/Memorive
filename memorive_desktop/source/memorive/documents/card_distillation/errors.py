"""Errors of card distillation: expected failures, not crashes, in the style of DocumentProcessingError.
A detail entry is in the runtime log before each one is raised.

Callers (the workflow, tests) catch them and show the message. Unexpected exceptions (likely bugs) are
not among them and are raised as they are.
"""
from __future__ import annotations


class EvidenceExtractionError(Exception):
    """General expected error of card distillation."""


class SourceNotFoundError(EvidenceExtractionError):
    """The [CleanMD] or [Chunks].jsonl of the same paper cannot be found. Distillation stops and never
    falls back to distilling CleanMD directly, which would lose the chunk_id each field is traced to."""


class SourceInvalidError(EvidenceExtractionError):
    """The CleanMD front matter is invalid, a chunk has no chunk_id, or the paper_id or ownership differs
    within one paper: distillation stops."""


class OwnershipBlockedError(EvidenceExtractionError):
    """Ownership: data_ownership=entrusted (needs a local model, not available yet), missing or unknown
    stops distillation before the gateway is called. Distillation uses a cloud model (DeepSeek), and data
    entrusted by others or of unknown ownership is never sent."""


class ChunksTooLargeError(EvidenceExtractionError):
    """The joined input is over the character limit. An error, never a silent cut: a cut breaks R8 (use
    the whole text) and lets the model mark what it never read as not given, breaking R9."""


class CardParseError(EvidenceExtractionError):
    """The model's reply is not valid JSON even after removing the fence and taking the first JSON object.
    The raw reply is in the runtime log detail; it never becomes an empty card."""


class CardLanguageDriftError(CardParseError):
    """The core text has a long passage in another language that the source does not support; translations
    or explanations in another language added by the model are refused."""


class CardFieldMissingError(EvidenceExtractionError):
    """The model's output lacks a core field, or a field has the wrong shape or type (no value or
    chunk_ids, a wrong type)."""


class CardAnchorInvalidError(EvidenceExtractionError):
    """A core field's made-up chunk_id could not be handled safely after the one targeted repair.

    Normally DeepSeek repairs only the affected anchors and the full local check runs again. If the
    repair reply breaks its contract, only the made-up anchors are removed, and each field must still keep
    at least one real anchor in the same paper. Only when that would leave a field with no anchor is the
    whole card refused with this error.
    """


class CardExistsError(EvidenceExtractionError):
    """The target [Card] already exists and overwrite=True was not given: stored data is never
    overwritten by default, and distilling again must not replace the old card. Overwriting has to be
    asked for explicitly and is logged, like re_embed's single explicit entry."""


class KeyDataInvalidError(EvidenceExtractionError):
    """The structure check of the key_data extraction failed; the whole card is refused, so no half card
    is written.
    Structure only: a bare number (stat empty, or stat_type without a value, against R3), an empty unit, a
    missing or empty quote, an incomplete four-slot sample template (no place for R4), a made-up chunk_id
    (not among the real chunks), a missing field.
    Meaning is not checked: a real anchor with a wrong value, statistics in the source marked "not given",
    a wrong scope (a part taken for the whole). These are known gaps left to the second-vendor review; the
    validator does not judge them and does not report them."""


class CardQuoteNotFoundError(EvidenceExtractionError):
    """A quote failed the check against the source, so the whole card is refused: no card is written, the
    card stays pending, and the error goes to the runtime log (paper_id, path, chunk_id and reason, for a
    person to check).
    It happens in one case only: a key_data quote (numbers) from a clean source (no U+FFFD or <br>) that,
    normalized, is not a substring of its chunk, and still is not after one targeted re-copy. That is the
    one clear sign of a made-up sentence.
    Nothing else raises it or removes the card: (1) a long real substring is accepted (long is not false;
    it is only noted); (2) when a garbled source makes a faithful quote fail, key_data keeps the quote, a
    document processing debt is recorded and the by_field quote becomes null; (3) a by_field quote that
    fails becomes null (no field is strictly checked: models paraphrase or combine every core content
    field, boundary_conditions included, so none has a reliable verbatim anchor), and whether a paraphrase
    is faithful is left to the second-vendor review and a person.

    The substring check proves only that the quote is present in the source. It catches quotes that are
    not there at all (merged from another source, a made-up sentence, made-up support). It does not prove
    that the value follows the sentence or that a paraphrase keeps the meaning: a real quote with a wrong
    value, or a paraphrase that drifts, is still for the second-vendor review (key_data is always
    needs_review=true and goes to that review). So this is a fixed check before the review, not a
    replacement for it.

    The location is on the error as fields, not only in the log context: path (for people), chunk_id,
    reason, and the structured locator."""

    def __init__(self, message: str, *, path: str | None = None, chunk_id: str | None = None,
                 reason: str | None = None, locator: dict | None = None):
        super().__init__(message)
        self.path = path                 # for people: key_data[3].quote / by_field.key_results[1].quote
        self.chunk_id = chunk_id
        self.reason = reason
        self.locator = locator or {}     # structured: block / entry_index / entry_no / field / anchor_index ...
