"""Fail-closed verifier and perfect replay for an OCR page reference pack."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .ocr_page_exam import (
    FAMILIES,
    ExamMethodError,
    perfect_response,
    score_form,
    sha256_json,
    sha256_text,
)
from .ocr_page_exam_assets import (
    CANONICAL_REPO_RELATIVE_PATH,
    DECLASSIFICATION_AUTHORIZATION_ID,
    GOLD_SCHEMA_VERSION,
    NORMALIZATION_PROFILE_ID,
    PACK_SCHEMA_VERSION,
    PROMPT_SHA256,
    REFERENCE_PACK_FAMILY_ID,
    REFERENCE_PACK_ID,
    REFERENCE_PACK_REVISION,
    SCORING_PROTOCOL_ID,
)


_CHECKSUM_LINE = re.compile(r"^([0-9A-F]{64})  ([^\r\n]+)$")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExamMethodError(f"DUPLICATE_JSON_KEY:{key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExamMethodError(f"JSON_LOAD_FAILED:{path}:{type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise ExamMethodError(f"JSON_ROOT_NOT_OBJECT:{path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def parse_checksums(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ExamMethodError(f"CHECKSUM_READ_FAILED:{type(exc).__name__}") from exc
    for line in lines:
        match = _CHECKSUM_LINE.fullmatch(line)
        if match is None:
            raise ExamMethodError(f"CHECKSUM_LINE_INVALID:{line}")
        digest, relative = match.groups()
        if relative in result:
            raise ExamMethodError(f"CHECKSUM_DUPLICATE_PATH:{relative}")
        if "\\" in relative or relative.startswith("/") or ".." in Path(relative).parts:
            raise ExamMethodError(f"CHECKSUM_UNSAFE_PATH:{relative}")
        result[relative] = digest
    return result


def _verify_self_hash(value: dict[str, Any], field: str, label: str) -> None:
    observed = value.get(field)
    if not isinstance(observed, str):
        raise ExamMethodError(f"{label}_SELF_HASH_MISSING:{field}")
    payload = dict(value)
    payload.pop(field, None)
    expected = sha256_json(payload)
    if observed != expected:
        raise ExamMethodError(f"{label}_SELF_HASH_MISMATCH:{observed}:{expected}")


def _case_map(value: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    rows = value.get("cases")
    if not isinstance(rows, list):
        raise ExamMethodError(f"{label}_CASES_NOT_LIST")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ExamMethodError(f"{label}_CASE_NOT_OBJECT")
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ExamMethodError(f"{label}_CASE_ID_INVALID")
        if case_id in result:
            raise ExamMethodError(f"{label}_DUPLICATE_CASE:{case_id}")
        result[case_id] = row
    return result


def _validate_form_gold(
    *,
    pack: Path,
    expected_pack_id: str,
    form_name: str,
    form: dict[str, Any],
    gold: dict[str, Any],
) -> dict[str, Any]:
    if form.get("reference_pack_id") != expected_pack_id:
        raise ExamMethodError(f"FORM_PACK_ID_MISMATCH:{form_name}")
    if form.get("form_name") != form_name:
        raise ExamMethodError(f"FORM_NAME_MISMATCH:{form_name}")
    if form.get("role_id") != "ocr_page":
        raise ExamMethodError(f"FORM_ROLE_MISMATCH:{form_name}")
    if form.get("case_count") != 48:
        raise ExamMethodError(f"FORM_CASE_COUNT_MISMATCH:{form_name}")
    if form.get("provider_output_contract") != "PLAIN_MARKDOWN_TEXT":
        raise ExamMethodError(f"FORM_OUTPUT_CONTRACT_MISMATCH:{form_name}")
    if form.get("content_directed_repair_allowed") is not False:
        raise ExamMethodError(f"FORM_CONTENT_REPAIR_NOT_FALSE:{form_name}")
    if form.get("prompt_sha256") != PROMPT_SHA256:
        raise ExamMethodError(f"FORM_PROMPT_HASH_MISMATCH:{form_name}")
    _verify_self_hash(form, "form_sha256", f"FORM_{form_name}")

    if gold.get("schema_version") != GOLD_SCHEMA_VERSION:
        raise ExamMethodError(f"GOLD_SCHEMA_VERSION_MISMATCH:{form_name}")
    if gold.get("form_id") != form.get("form_id"):
        raise ExamMethodError(f"FORM_GOLD_ID_MISMATCH:{form_name}")
    if gold.get("provider_visible") is not False:
        raise ExamMethodError(f"GOLD_PROVIDER_VISIBLE:{form_name}")
    if gold.get("gold_sent_to_provider") is not False:
        raise ExamMethodError(f"GOLD_SENT_TO_PROVIDER:{form_name}")
    if gold.get("case_count") != 48:
        raise ExamMethodError(f"GOLD_CASE_COUNT_MISMATCH:{form_name}")
    _verify_self_hash(gold, "gold_sha256", f"GOLD_{form_name}")

    form_cases = _case_map(form, f"FORM_{form_name}")
    gold_cases = _case_map(gold, f"GOLD_{form_name}")
    if set(form_cases) != set(gold_cases):
        raise ExamMethodError(f"FORM_GOLD_CASE_PARTITION_MISMATCH:{form_name}")

    family_counts = Counter(str(case.get("family")) for case in form_cases.values())
    if family_counts != Counter({family: 6 for family in FAMILIES}):
        raise ExamMethodError(
            f"FORM_FAMILY_QUOTA_MISMATCH:{form_name}:{dict(family_counts)}"
        )
    difficulty_by_family: dict[str, Counter[str]] = {}
    for family in FAMILIES:
        difficulty_by_family[family] = Counter(
            str(case.get("difficulty"))
            for case in form_cases.values()
            if case.get("family") == family
        )
        if difficulty_by_family[family] != Counter(
            {"easy": 2, "medium": 2, "hard": 2}
        ):
            raise ExamMethodError(
                "FORM_DIFFICULTY_QUOTA_MISMATCH:"
                f"{form_name}:{family}:{dict(difficulty_by_family[family])}"
            )
    stability = [
        case_id
        for case_id, case in form_cases.items()
        if case.get("stability_subset") is True
    ]
    if len(stability) != 6:
        raise ExamMethodError(
            f"FORM_STABILITY_SUBSET_COUNT_MISMATCH:{form_name}:{len(stability)}"
        )

    for case_id, case in form_cases.items():
        image_ref = case.get("image_ref")
        if (
            not isinstance(image_ref, str)
            or not image_ref.startswith(f"images/{form_name}/")
            or ".." in Path(image_ref).parts
        ):
            raise ExamMethodError(f"FORM_IMAGE_REF_INVALID:{case_id}")
        image_path = pack / image_ref
        if not image_path.is_file():
            raise ExamMethodError(f"FORM_IMAGE_MISSING:{case_id}:{image_ref}")
        if case.get("image_sha256") != sha256_file(image_path):
            raise ExamMethodError(f"FORM_IMAGE_HASH_MISMATCH:{case_id}")
        if case.get("source_origin") != "self_synthetic":
            raise ExamMethodError(f"FORM_SOURCE_ORIGIN_NOT_SYNTHETIC:{case_id}")
        if case.get("restricted_source") is not False:
            raise ExamMethodError(f"FORM_RESTRICTED_SOURCE_TRUE:{case_id}")
        gold_case = gold_cases[case_id]
        observed_identity = gold_case.get("gold_identity_sha256")
        identity_payload = dict(gold_case)
        identity_payload.pop("gold_identity_sha256", None)
        if observed_identity != sha256_json(identity_payload):
            raise ExamMethodError(f"GOLD_CASE_IDENTITY_MISMATCH:{case_id}")

    return {
        "form": form,
        "gold": gold,
        "form_cases": form_cases,
        "gold_cases": gold_cases,
        "family_counts": dict(family_counts),
        "stability_subset": stability,
    }


def _validate_ab_zero_overlap(
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, int]:
    form_a = left["form_cases"]
    form_b = right["form_cases"]
    gold_a = left["gold_cases"]
    gold_b = right["gold_cases"]
    dimensions = {
        "source_id": (
            {case["source_id"] for case in form_a.values()},
            {case["source_id"] for case in form_b.values()},
        ),
        "page_id": (
            {case["page_id"] for case in form_a.values()},
            {case["page_id"] for case in form_b.values()},
        ),
        "region_id": (
            {case["region_id"] for case in form_a.values()},
            {case["region_id"] for case in form_b.values()},
        ),
        "image_sha256": (
            {case["image_sha256"] for case in form_a.values()},
            {case["image_sha256"] for case in form_b.values()},
        ),
        "expected_text_sha256": (
            {sha256_text(case["expected_text"]) for case in gold_a.values()},
            {sha256_text(case["expected_text"]) for case in gold_b.values()},
        ),
        "gold_identity_sha256": (
            {case["gold_identity_sha256"] for case in gold_a.values()},
            {case["gold_identity_sha256"] for case in gold_b.values()},
        ),
    }
    counts: dict[str, int] = {}
    for dimension, (a_values, b_values) in dimensions.items():
        overlap = a_values & b_values
        counts[dimension] = len(overlap)
        if overlap:
            raise ExamMethodError(
                f"FORM_AB_OVERLAP:{dimension}:{sorted(overlap)[:3]}"
            )
    return counts


def verify_reference_pack(pack: Path) -> dict[str, Any]:
    pack = pack.resolve()
    if not pack.is_dir():
        raise ExamMethodError(f"REFERENCE_PACK_NOT_DIRECTORY:{pack}")
    manifest_path = pack / "manifest.reference.json"
    checksum_path = pack / "SHA256SUMS.txt"
    if not manifest_path.is_file() or not checksum_path.is_file():
        raise ExamMethodError("REFERENCE_PACK_CONTROL_FILES_MISSING")

    manifest = load_json(manifest_path)
    if manifest.get("schema_version") != PACK_SCHEMA_VERSION:
        raise ExamMethodError("MANIFEST_SCHEMA_VERSION_MISMATCH")
    manifest_pack_id = manifest.get("reference_pack_id")
    if manifest_pack_id not in {REFERENCE_PACK_FAMILY_ID, REFERENCE_PACK_ID}:
        raise ExamMethodError("MANIFEST_PACK_ID_MISMATCH")
    if manifest.get("classification") != "REFERENCE_EXAM_PACK":
        raise ExamMethodError("MANIFEST_CLASSIFICATION_MISMATCH")
    if manifest.get("revision") != REFERENCE_PACK_REVISION:
        raise ExamMethodError("MANIFEST_REVISION_MISMATCH")
    if manifest.get("reference_regression_only") is not True:
        raise ExamMethodError("MANIFEST_REFERENCE_REGRESSION_ONLY_NOT_TRUE")
    if (
        manifest.get("declassification_authorization_id")
        != DECLASSIFICATION_AUTHORIZATION_ID
    ):
        raise ExamMethodError("MANIFEST_DECLASSIFICATION_AUTHORITY_MISMATCH")
    if manifest.get("canonical_repo_relative_path") != CANONICAL_REPO_RELATIVE_PATH:
        raise ExamMethodError("MANIFEST_CANONICAL_PATH_MISMATCH")
    if manifest.get("role_id") != "ocr_page":
        raise ExamMethodError("MANIFEST_ROLE_MISMATCH")
    if manifest.get("source_origin") != "self_synthetic":
        raise ExamMethodError("MANIFEST_SOURCE_ORIGIN_MISMATCH")
    if manifest.get("external_source_used") is not False:
        raise ExamMethodError("MANIFEST_EXTERNAL_SOURCE_TRUE")
    if manifest.get("restricted_source_used") is not False:
        raise ExamMethodError("MANIFEST_RESTRICTED_SOURCE_TRUE")
    if manifest.get("blind_holdout_eligible") is not False:
        raise ExamMethodError("MANIFEST_BLIND_ELIGIBILITY_NOT_FALSE")
    if manifest.get("qualification_eligible") is not False:
        raise ExamMethodError("MANIFEST_QUALIFICATION_ELIGIBILITY_NOT_FALSE")
    if manifest.get("provider_requests_during_build") != 0:
        raise ExamMethodError("MANIFEST_PROVIDER_REQUESTS_NONZERO")
    if manifest.get("gold_sent_to_provider") is not False:
        raise ExamMethodError("MANIFEST_GOLD_SENT_TO_PROVIDER")
    if manifest.get("production_prompt_sha256") != PROMPT_SHA256:
        raise ExamMethodError("MANIFEST_PROMPT_HASH_MISMATCH")
    if manifest.get("content_directed_repair_allowed") is not False:
        raise ExamMethodError("MANIFEST_CONTENT_REPAIR_NOT_FALSE")
    _verify_self_hash(
        manifest,
        "manifest_payload_sha256",
        "MANIFEST",
    )

    members = manifest.get("members")
    if not isinstance(members, list):
        raise ExamMethodError("MANIFEST_MEMBERS_NOT_LIST")
    if manifest.get("member_set_sha256") != sha256_json(members):
        raise ExamMethodError("MANIFEST_MEMBER_SET_HASH_MISMATCH")
    member_map: dict[str, dict[str, Any]] = {}
    for member in members:
        if not isinstance(member, dict):
            raise ExamMethodError("MANIFEST_MEMBER_NOT_OBJECT")
        relative = member.get("path")
        if not isinstance(relative, str) or not relative:
            raise ExamMethodError("MANIFEST_MEMBER_PATH_INVALID")
        if relative in member_map:
            raise ExamMethodError(f"MANIFEST_MEMBER_DUPLICATE:{relative}")
        member_map[relative] = member
        path = pack / relative
        if not path.is_file():
            raise ExamMethodError(f"MANIFEST_MEMBER_MISSING:{relative}")
        if path.stat().st_size != member.get("bytes"):
            raise ExamMethodError(f"MANIFEST_MEMBER_SIZE_MISMATCH:{relative}")
        if sha256_file(path) != member.get("sha256"):
            raise ExamMethodError(f"MANIFEST_MEMBER_HASH_MISMATCH:{relative}")
        if member.get("subject_visible") not in {True, False}:
            raise ExamMethodError(f"MEMBER_SUBJECT_VISIBILITY_INVALID:{relative}")
        if member.get("provider_visible") not in {True, False}:
            raise ExamMethodError(f"MEMBER_PROVIDER_VISIBILITY_INVALID:{relative}")
        if member.get("rights_class") != "SELF_OWNED_SYNTHETIC_OUTPUT":
            raise ExamMethodError(f"MEMBER_RIGHTS_CLASS_MISMATCH:{relative}")
        if not isinstance(member.get("provenance"), str) or not member["provenance"]:
            raise ExamMethodError(f"MEMBER_PROVENANCE_MISSING:{relative}")
        if relative.startswith("gold/") and member.get("provider_visible") is not False:
            raise ExamMethodError(f"GOLD_MEMBER_PROVIDER_VISIBLE:{relative}")
        if relative.startswith("images/") and member.get("provider_visible") is not True:
            raise ExamMethodError(f"IMAGE_MEMBER_PROVIDER_NOT_VISIBLE:{relative}")

    checksums = parse_checksums(checksum_path)
    expected_checksum_paths = set(member_map) | {"manifest.reference.json"}
    if set(checksums) != expected_checksum_paths:
        missing = sorted(expected_checksum_paths - set(checksums))
        extra = sorted(set(checksums) - expected_checksum_paths)
        raise ExamMethodError(f"CHECKSUM_EXACT_SET_MISMATCH:{missing}:{extra}")
    for relative, digest in checksums.items():
        if sha256_file(pack / relative) != digest:
            raise ExamMethodError(f"CHECKSUM_HASH_MISMATCH:{relative}")

    filesystem_paths = {
        path.relative_to(pack).as_posix()
        for path in pack.rglob("*")
        if path.is_file()
    }
    expected_filesystem = expected_checksum_paths | {"SHA256SUMS.txt"}
    if filesystem_paths != expected_filesystem:
        missing = sorted(expected_filesystem - filesystem_paths)
        extra = sorted(filesystem_paths - expected_filesystem)
        raise ExamMethodError(f"FILESYSTEM_EXACT_SET_MISMATCH:{missing}:{extra}")

    prompt_path = pack / "prompts" / "ocr_prompt_v3.md"
    if sha256_file(prompt_path) != PROMPT_SHA256:
        raise ExamMethodError("PRODUCTION_PROMPT_EXACT_BYTES_MISMATCH")
    scoring = load_json(pack / "scoring" / "scoring_protocol.json")
    layer_max_points = scoring.get("layer_max_points")
    if (
        not isinstance(layer_max_points, dict)
        or sum(float(value) for value in layer_max_points.values()) != 100
    ):
        raise ExamMethodError("SCORING_LAYER_MAX_TOTAL_NOT_100")
    expected_curve_layers = {
        "overall_case_quality",
        "hard_case_quality",
        "worst_family_quality",
        "bottom_decile_quality",
    }
    curves = scoring.get("curves")
    if not isinstance(curves, dict) or set(curves) != expected_curve_layers:
        raise ExamMethodError("SCORING_CURVE_LAYER_SET_MISMATCH")
    for layer in expected_curve_layers:
        knots = curves.get(layer)
        if (
            not isinstance(knots, list)
            or not knots
            or float(knots[-1][0]) != 100.0
            or float(knots[-1][1]) != float(layer_max_points[layer])
        ):
            raise ExamMethodError(f"SCORING_CURVE_MAX_MISMATCH:{layer}")
    if scoring.get("schema_version") != "m14-ocr-page-scoring-protocol-v4":
        raise ExamMethodError("SCORING_SCHEMA_VERSION_MISMATCH")
    if scoring.get("protocol_id") != SCORING_PROTOCOL_ID:
        raise ExamMethodError("SCORING_PROTOCOL_ID_MISMATCH")
    if scoring.get("critical_hard_failure_score_cap") is not None:
        raise ExamMethodError("SCORING_CRITICAL_CAP_NOT_NULL")
    expected_robustness_curves = {
        "worst_family_quality": [
            [0, 0],
            [35, 0],
            [50, 3],
            [65, 4],
            [75, 6],
            [85, 8],
            [92, 10],
            [97, 11.5],
            [100, 12],
        ],
        "bottom_decile_quality": [
            [0, 0],
            [35, 0],
            [50, 4],
            [65, 4.5],
            [75, 5.5],
            [85, 6.5],
            [92, 7],
            [97, 7.5],
            [100, 8],
        ],
    }
    for layer, expected_knots in expected_robustness_curves.items():
        if scoring["curves"].get(layer) != expected_knots:
            raise ExamMethodError(f"SCORING_ROBUSTNESS_CURVE_MISMATCH:{layer}")
    expected_score_bands = [
        {
            "minimum_inclusive": 0,
            "maximum_exclusive": 60,
            "label": "NOT_RECOMMENDED",
        },
        {
            "minimum_inclusive": 60,
            "maximum_exclusive": 80,
            "label": "USE_WITH_CAUTION",
        },
        {
            "minimum_inclusive": 80,
            "maximum_inclusive": 100,
            "label": "RECOMMENDED",
        },
    ]
    if scoring.get("score_bands") != expected_score_bands:
        raise ExamMethodError("SCORING_SCORE_BANDS_MISMATCH")
    expected_thresholds = {
        "not_recommended_below": 60,
        "use_with_caution_below": 80,
        "recommended_at_or_above": 80,
        "quality_hard_gate_reported_separately": True,
    }
    if scoring.get("recommendation_thresholds") != expected_thresholds:
        raise ExamMethodError("SCORING_RECOMMENDATION_THRESHOLDS_MISMATCH")
    if scoring.get("provider_blind") is not True:
        raise ExamMethodError("SCORING_NOT_PROVIDER_BLIND")
    if scoring.get("content_directed_repair_allowed") is not False:
        raise ExamMethodError("SCORING_CONTENT_REPAIR_NOT_FALSE")
    normalization = load_json(
        pack / "normalization" / "normalization_profile.json"
    )
    if normalization.get("profile_id") != NORMALIZATION_PROFILE_ID:
        raise ExamMethodError("NORMALIZATION_PROFILE_ID_MISMATCH")
    if normalization.get("raw_output_preserved") is not True:
        raise ExamMethodError("NORMALIZATION_RAW_OUTPUT_NOT_PRESERVED")
    markdown_syntax = normalization.get("markdown_presentation_syntax")
    if not isinstance(markdown_syntax, dict):
        raise ExamMethodError("NORMALIZATION_MARKDOWN_POLICY_MISSING")
    if markdown_syntax.get("source_punctuation_removed") is not False:
        raise ExamMethodError("NORMALIZATION_SOURCE_PUNCTUATION_NOT_PROTECTED")
    latex_policy = normalization.get("latex_math_presentation")
    if not isinstance(latex_policy, dict):
        raise ExamMethodError("NORMALIZATION_LATEX_POLICY_MISSING")
    if latex_policy.get("missing_sign_or_value_inference") is not False:
        raise ExamMethodError("NORMALIZATION_LATEX_VALUE_INFERENCE_NOT_FALSE")
    if latex_policy.get("scientific_correction") is not False:
        raise ExamMethodError("NORMALIZATION_LATEX_SCIENTIFIC_CORRECTION_NOT_FALSE")
    if latex_policy.get("raw_output_mutated") is not False:
        raise ExamMethodError("NORMALIZATION_LATEX_RAW_OUTPUT_MUTATED")
    layout_policy = normalization.get("layout_order_match_view")
    if not isinstance(layout_policy, dict):
        raise ExamMethodError("NORMALIZATION_LAYOUT_MATCH_POLICY_MISSING")
    if layout_policy.get("applied_to_literal_or_critical_token_scoring") is not False:
        raise ExamMethodError("NORMALIZATION_LAYOUT_GAP_APPLIED_TO_FIDELITY")
    if normalization.get("automatic_dehyphenation") is not False:
        raise ExamMethodError("NORMALIZATION_DEHYPHENATION_NOT_FALSE")
    if normalization.get("punctuation_folding") is not False:
        raise ExamMethodError("NORMALIZATION_PUNCTUATION_FOLDING_NOT_FALSE")

    forms: dict[str, dict[str, Any]] = {}
    for form_name in ("A", "B"):
        form = load_json(pack / "forms" / f"form_{form_name}.json")
        gold = load_json(pack / "gold" / f"form_{form_name}_gold.json")
        forms[form_name] = _validate_form_gold(
            pack=pack,
            expected_pack_id=str(manifest_pack_id),
            form_name=form_name,
            form=form,
            gold=gold,
        )
    overlap = _validate_ab_zero_overlap(forms["A"], forms["B"])
    if manifest.get("form_ab_overlap") != overlap:
        raise ExamMethodError("MANIFEST_AB_OVERLAP_RECEIPT_MISMATCH")

    replay: dict[str, Any] = {}
    for form_name in ("A", "B"):
        form = forms[form_name]["form"]
        gold = forms[form_name]["gold"]
        response = perfect_response(form=form, gold=gold)
        score = score_form(
            form=form,
            gold=gold,
            response=response,
            scoring_protocol=scoring,
        )
        if score.get("score") != 100.0:
            raise ExamMethodError(
                f"PERFECT_REPLAY_SCORE_NOT_100:{form_name}:{score.get('score')}"
            )
        if score.get("hard_gate_verdict") != "PASS":
            raise ExamMethodError(
                f"PERFECT_REPLAY_HARD_GATE_FAIL:{form_name}"
            )
        replay[form_name] = {
            "perfect_score": score["score"],
            "hard_gate_verdict": score["hard_gate_verdict"],
            "response_sha256": sha256_json(response),
            "score_sha256": score["score_sha256"],
            "provider_requests": 0,
        }

    receipt = {
        "schema_version": "m14-ocr-page-reference-pack-verification-v1",
        "reference_pack_id": manifest_pack_id,
        "revision": manifest["revision"],
        "classification": manifest["classification"],
        "reference_regression_only": manifest["reference_regression_only"],
        "path": str(pack),
        "verification_result": "PASS",
        "filesystem_file_count": len(filesystem_paths),
        "manifest_member_count": len(member_map),
        "image_count": sum(
            1 for relative in member_map if relative.startswith("images/")
        ),
        "form_case_count": {"A": 48, "B": 48},
        "family_quota_per_form": {family: 6 for family in FAMILIES},
        "form_ab_overlap": overlap,
        "forms": replay,
        "gold_sent_to_provider": False,
        "provider_requests": 0,
        "manifest_sha256": sha256_file(manifest_path),
        "checksums_sha256": sha256_file(checksum_path),
        "member_set_sha256": manifest["member_set_sha256"],
    }
    receipt["verification_sha256"] = sha256_json(receipt)
    return receipt
