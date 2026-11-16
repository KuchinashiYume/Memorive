"""Settings of card distillation, all in this one place; environment variables can override them.
The default model is deepseek-v4-pro for every step of a card."""
from __future__ import annotations

import os
from datetime import datetime

# The gateway slot; its model, endpoint and default (deepseek-v4-pro) are set in the gateway's models.yaml, not here.
DISTILL_TASK = "distill"
CORE_JSON_RECOVERY_PROMPT_MODULE = "distill_core_json_recovery"
CORE_ANCHOR_REPAIR_PROMPT_MODULE = "distill_anchor_repair"

# The schema version of the cards written.
# v3 (2026-07-08): each key_data entry carries a verbatim quote from the source, checked as a substring
#   during distillation.
# v4 (2026-07-08): by_field becomes a list of anchors [{chunk_id, quote, section_page}, ...] (v2 and v3
#   had {section_page, chunk_ids: [...]}). The high-risk fields (key_results, author_conclusion,
#   boundary_conditions) carry a verbatim quote per anchor, checked the same way; the combined fields
#   (research_question, research_object, method) may have a null quote. Readers go by shape: a dict is
#   the old v2/v3 shape (chunk_ids), a list is v4 (each item's chunk_id). Retrieval and the card review
#   accept both, so old v2/v3 cards stay readable unchanged. The number is for migration and telling
#   shapes apart.
# v5 (2026-07-09): a credibility for each field and each key_data entry (verified, anchor_uncertain,
#   flagged, plus a note); a failed review no longer quarantines the whole card, which becomes active
#   with these marks. Top-level field_credibility (the six core fields), and credibility,
#   credibility_note and danger on each key_data entry. New cards start as unknown (not reviewed yet);
#   grade_and_admit sets them. Older v1 to v4 cards without them read as unknown.
# v6 (2026-07-13): completion_status and omissions. A new card starts as pending_review with []. After
#   Sonnet's first review, one DeepSeek repair batch and Sonnet rechecking only what changed, the final
#   card may drop items that cannot be trusted, leaving a tombstone with the old location, value and
#   report link. v1 to v5 cards are read in their own shapes and not migrated.
CARD_SCHEMA_VERSION = 6

# Output token limit. Distillation needs plenty; never the DeepSeek adapter's default of 16 (the Pro
# model reasons before answering). Set by environment variable.
# 8192 by default: a whole card (7 fields in the source's language, not translated) plus the reasoning;
# 4096 was seen to cut the JSON short. Raise it for long papers if needed.
CARD_CREATE_MAX_TOKENS = int(os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_CARD_CREATE_MAX_TOKENS") or 8192)
DISTILL_MAX_TOKENS = CARD_CREATE_MAX_TOKENS  # compatibility alias

# Retry limit after a cut: when the first reply is cut (finish_reason=length) it is retried once with
# this limit, and never more (cost, loops). Still cut: CardParseError. Set by environment variable.
CARD_CREATE_MAX_TOKENS_RETRY = int(
    os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_CARD_CREATE_MAX_TOKENS_RETRY") or 16384
)
DISTILL_MAX_TOKENS_RETRY = CARD_CREATE_MAX_TOKENS_RETRY  # compatibility alias

# Whole-Card regeneration is a distinct request and accounting category.  The
# current atomic closure forbids this route, but the budget remains explicit so
# a future authorized regenerate never borrows the create or repair allowance.
CARD_REGENERATE_MAX_TOKENS = int(
    os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_CARD_REGENERATE_MAX_TOKENS") or 16384
)

# The one targeted repair of made-up chunk_ids in core fields. Only the affected fields, the made-up
# anchors and the fixed candidate chunks are sent, not the whole text and not a new card; the gateway's
# own provider retries still apply.
CORE_ANCHOR_REPAIR_MAX_TOKENS = int(
    os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_ANCHOR_REPAIR_MAX_TOKENS") or 4096
)

# Input character limit: joined chunks over it raise ChunksTooLargeError and are never cut silently.
# Set by environment variable.
# 250_000 by default, against deepseek-v4-pro's 1M-token context: 250,000 characters is about 60,000
#   tokens, about 6% of the context. That covers long reviews with references (a 15-page review is about
#   120,000 characters) about twice over; a full input costs about 0.19 yuan per paper.
# The old 120_000 was two rounded times the 63,000-character body of one test paper and was never tried
#   on reviews with long reference lists; an ordinary 15-page review (120,957 characters) went over it.
#   It was a calibration mistake, not a model limit, and was raised to 250k on 2026-07-08.
# Over 250,000 characters still raises ChunksTooLargeError; raise the limit for a long paper if needed.
INPUT_MAX_CHARS = int(os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_INPUT_MAX_CHARS") or 250_000)

# -- key_data extractor ---------------------------------------------------------------
# Its own prompt module (versioned on its own, apart from the core distill prompt), through the same
# DISTILL_TASK gateway slot (deepseek-v4-pro, no new model slot). Its own token budget: the key_data list
# can be long, so it does not share the budget of the 7 core fields. Set by environment variable.
# Each entry also carries a verbatim quote, which costs output tokens; if replies get cut, raise the
# limit by environment variable rather than changing the default here.
KEY_DATA_PROMPT_MODULE = "distill_key_data"
KEY_DATA_RECOVERY_PROMPT_MODULE = "distill_key_data_recovery"
KEY_DATA_MAX_TOKENS = int(os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_KEY_DATA_MAX_TOKENS") or 8192)
KEY_DATA_MAX_TOKENS_RETRY = int(os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_KEY_DATA_MAX_TOKENS_RETRY") or 16384)

# -- key_data quotes -------------------------------------------------------------------
# Each key_data entry carries a verbatim quote, checked during distillation (normalized, it must be a
# substring of its chunk). The character limit is for the re-anchor prompt and the quote_long note; a
# real substring is accepted even when longer, without a re-copy or refusal. A quote that is not a
# substring goes through _resolve_quote (a targeted re-copy, then the usual handling). Set by
# environment variable.
KEY_DATA_QUOTE_MAX_CHARS = int(os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_KEY_DATA_QUOTE_MAX_CHARS") or 300)
# Output token limit of a quote re-copy: one source sentence for one entry, so a small budget. Set by environment variable.
KEY_DATA_REANCHOR_MAX_TOKENS = int(os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_KEY_DATA_REANCHOR_MAX_TOKENS") or 1024)

# -- extra blocks: comparison_context / author_limitations_outlook / terms / citations ------------
# One call through the gateway's distill slot (deepseek-v4-pro, no new model slot), with its own prompt
# module distill_aux and token budget. They are stored but not used yet, so a failure or cut only logs a
# warning (detail level) and the card is written without them; the core fields and key_data are not
# affected. Shapes: a 7-field comparison, {quote, chunk_id} limitations and outlook, {term, definition}
# terms, {ref, relation} citations.
AUX_PROMPT_MODULE = "distill_aux"
AUX_MAX_TOKENS = int(os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_AUX_MAX_TOKENS") or 8192)
AUX_MAX_TOKENS_RETRY = int(os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_AUX_MAX_TOKENS_RETRY") or 16384)

# HTTP timeout per call, in seconds: distillation calls reason for a long time; passed to the gateway's
# DeepSeek adapter as payload["timeout"] (the adapter's own default stays 30). Set by environment variable.
HTTP_TIMEOUT = int(os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_HTTP_TIMEOUT") or 90)


def now_minute() -> str:
    """Local time with the offset, to the minute (like embedded_at; used for distilled_at).
    Keep it to the minute: PyYAML safe_load reads a full ISO time with seconds as a datetime object,
    which breaks every reader that takes distilled_at as a string; without seconds it reads back as one."""
    return datetime.now().astimezone().isoformat(timespec="minutes")
