"""Card reviewer exam policy: category and role, revisions and scoring semantics, the workload
profile and request reserve of the core card reviewer, and comparison metadata.
"""
from __future__ import annotations

import re
from typing import Any, Mapping
from memorive.exams.common import _sha256


CARD_REVIEWER_EXAM_CATEGORY_ID = "Quality_CARD_REVIEWER_REFERENCE_REGRESSION"


CARD_REVIEWER_ROLE_ID = "CARD_REVIEWER"


CARD_REVIEWER_SCORING_PROJECTION_REVISION = (
    "Desktop_CARD_REVIEWER_ROLE_PROJECTION_V5"
)


CARD_REVIEWER_SCORE_SEMANTICS = "FIRST_PASS_NONLINEAR_ROLE_ABILITY"


CARD_REVIEWER_OPERATIONAL_POLICY_REVISION = (
    "Desktop_CARD_REVIEWER_OPERATIONAL_ELIGIBILITY_V2_Core_CLOSURE"
)


CARD_REVIEWER_OPERATIONAL_PASSING_SCORE = 80.0


CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_REVISION = (
    "Desktop_CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_V6"
)


CORE_CARD_REVIEWER_LIFECYCLE_REVISION = (
    "Core_FULL_ONE_REPAIR_DELTA_GOVERNED_DISPOSITION_V1"
)


CORE_CARD_REVIEWER_WORKLOAD_PROFILE_REVISION = (
    "Desktop_Core_CORE19_ACTUAL_CLEANMD_MEAN_X2_V2"
)


Core_CORE_FINAL_SUPPORTED_PAPER_COUNT = 19


Core_CORE_FINAL_CLEANMD_TOTAL_CHARS = 1_306_218


Core_CORE_FINAL_CLEANMD_MEAN_CHARS = 68_748.315789


CORE_CARD_REVIEWER_WORKLOAD_MULTIPLIER = 2.0


CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT = 137_497


CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_MULTIPLIER = 2.0


CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_CHAR_LIMIT = 137_497


CORE_CARD_REVIEWER_SOURCE_MANIFEST_SHA256 = (
    "65606B32990005793E563D9C2B0B7AA5CF96CA3C7CD56FA7A2440CB4B22418A8"
)


def core_card_reviewer_workload_profile() -> dict[str, Any]:
    """Return the aggregate-only Core workload reference bundled in the EXE.

    The source texts and private PDFs are deliberately not embedded.  The
    aggregate is bound to the formal Core publication manifest and uses the
    19 text-supported Core Final papers; the correctly blocked Guo2011 OCR case
    is excluded from the mean.
    """

    profile = {
        "schema_version": CORE_CARD_REVIEWER_WORKLOAD_PROFILE_REVISION,
        "source_manifest_sha256": CORE_CARD_REVIEWER_SOURCE_MANIFEST_SHA256,
        "paper_count": Core_CORE_FINAL_SUPPORTED_PAPER_COUNT,
        "length_unit": "UNICODE_CODE_POINTS_IN_CLEANMD",
        "total_cleanmd_chars": Core_CORE_FINAL_CLEANMD_TOTAL_CHARS,
        "mean_cleanmd_chars": Core_CORE_FINAL_CLEANMD_MEAN_CHARS,
        "exam_multiplier": CORE_CARD_REVIEWER_WORKLOAD_MULTIPLIER,
        "soft_effective_prompt_char_limit": (
            CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
        ),
        "effective_prompt_char_limit": (
            CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
        ),
        "absolute_prompt_multiplier": (
            CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_MULTIPLIER
        ),
        "absolute_effective_prompt_char_limit": (
            CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_CHAR_LIMIT
        ),
        "soft_limit_semantics": (
            "TARGET_FOR_TOTAL_SUBJECT_VISIBLE_EXAM_INPUT_AND_EACH_TRANSPORT_SHARD"
        ),
        "absolute_limit_semantics": (
            "IRREDUCIBLE_ATOMIC_EXCEPTION_ONLY_OTHERWISE_NOT_ASSESSED"
        ),
        "gold_or_source_text_embedded": False,
    }
    profile["profile_sha256"] = _sha256(profile)
    return profile


def _api_worst_case_request_reserve_cny(
    target: Mapping[str, Any],
    prompt_metrics: Mapping[str, Any],
    request_max_output_tokens: int | None,
) -> float | None:
    """Return a conservative pre-send reserve for explicitly opted-in APIs.

    The UTF-8 byte count is a safe upper bound for the tokenizer input count
    used by the supported byte-backed model APIs.  The output side uses the
    exact provider ceiling bound into the request, not an observed average.
    """

    if target.get("enforce_per_call_worst_case_reserve") is not True:
        return None
    pricing = target.get("pricing_estimate_cny_per_million")
    input_bytes = prompt_metrics.get("effective_prompt_utf8_bytes")
    if (
        not isinstance(pricing, Mapping)
        or isinstance(input_bytes, bool)
        or not isinstance(input_bytes, int)
        or input_bytes < 0
        or isinstance(request_max_output_tokens, bool)
        or not isinstance(request_max_output_tokens, int)
        or request_max_output_tokens <= 0
    ):
        raise ValueError("WORKFLOW_MODEL_EXAM_WORST_CASE_RESERVE_PROFILE_INVALID")
    input_rate = pricing.get("input")
    output_rate = pricing.get("output")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= float(value) <= 100_000
        for value in (input_rate, output_rate)
    ):
        raise ValueError("WORKFLOW_MODEL_EXAM_WORST_CASE_RESERVE_PROFILE_INVALID")
    return round(
        (
            input_bytes * float(input_rate)
            + request_max_output_tokens * float(output_rate)
        )
        / 1_000_000,
        9,
    )


def card_reviewer_comparison_metadata(
    *,
    reference_pack_id: str,
    reference_pack_revision: str,
    reference_pack_sha256: str,
    scoring_protocol_revision: str,
    scoring_protocol_sha256: str,
    executor_ref: str,
) -> dict[str, Any]:
    """Build the exact same-category cohort binding for Reviewer scores."""

    values = {
        "exam_category_id": CARD_REVIEWER_EXAM_CATEGORY_ID,
        "role_id": CARD_REVIEWER_ROLE_ID,
        "reference_pack_id": reference_pack_id,
        "reference_pack_revision": reference_pack_revision,
        "reference_pack_sha256": reference_pack_sha256,
        "scoring_protocol_revision": scoring_protocol_revision,
        "scoring_protocol_sha256": scoring_protocol_sha256,
        "scoring_projection_revision": (
            CARD_REVIEWER_SCORING_PROJECTION_REVISION
        ),
        "score_semantics": CARD_REVIEWER_SCORE_SEMANTICS,
    }
    for key, value in values.items():
        if not isinstance(value, str) or not value:
            raise ValueError(f"WORKFLOW_MODEL_EXAM_COMPARISON_BINDING_INVALID:{key}")
    if not isinstance(executor_ref, str) or not executor_ref:
        raise ValueError(
            "WORKFLOW_MODEL_EXAM_COMPARISON_BINDING_INVALID:executor_ref"
        )
    for key in ("reference_pack_sha256", "scoring_protocol_sha256"):
        if not re.fullmatch(r"[A-F0-9]{64}", values[key]):
            raise ValueError(f"WORKFLOW_MODEL_EXAM_COMPARISON_BINDING_INVALID:{key}")
    binding_sha256 = _sha256(values)
    return {
        **{
            key: value
            for key, value in values.items()
            if key != "score_semantics"
        },
        "comparison_score_semantics": values["score_semantics"],
        "comparison_binding_sha256": binding_sha256,
        "comparison_cohort_id": f"CARD_REVIEWER:{binding_sha256}",
        "execution_adapter_ref": executor_ref,
        "horizontal_comparison_eligible": True,
        "horizontal_comparison_allowed_only_with_same_cohort": True,
        "cross_category_comparison_forbidden": True,
        "global_ranking_forbidden": True,
    }
