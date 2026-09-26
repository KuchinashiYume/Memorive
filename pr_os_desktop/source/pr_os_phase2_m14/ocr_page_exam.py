"""Provider-blind scoring for the production-shaped OCR page exam.

The provider returns plain OCR text.  A local runner wraps that immutable text
with case and image hashes.  Gold, normalization and scoring stay local.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any


FORM_SCORE_SCHEMA_VERSION = "m14-ocr-page-form-score-v4"
DUAL_FORM_RESULT_SCHEMA_VERSION = "m14-ocr-page-dual-form-result-v3"
STABILITY_SCHEMA_VERSION = "m14-ocr-page-stability-result-v1"
OUTPUT_BUNDLE_SCHEMA_VERSION = "m14-ocr-page-local-output-bundle-v1"
NORMALIZATION_PROFILE_ID = "p02-t08-m14-ocr-page-normalization-v3"

FAMILIES = (
    "literal_text",
    "numeric_direction",
    "units_symbols_scripts",
    "reading_order_layout",
    "page_role_separation",
    "noise_degradation",
    "faithfulness_uncertainty",
    "clean_control",
)

UNCERTAINTY_MARKERS = (
    "[unclear]",
    "[illegible]",
    "[unreadable]",
    "<?>",
    "�",
)

_LIGATURES = str.maketrans(
    {
        "ﬀ": "ff",
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
        "ﬅ": "st",
        "ﬆ": "st",
    }
)
_MARKDOWN_HEADING_PREFIX = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+(?=\S)")
_LATEX_PRESENTATION_HINT = re.compile(
    r"\\(?:\(|\)|\[|\]|text\b|mathrm\b|mathit\b|mathbf\b|"
    r"operatorname\b|Delta\b|alpha\b|times\b|cdot\b|circ\b|"
    r"quad\b|qquad\b|[,;:!_%#&])|\^\s*(?:\{|\\circ)"
)
_LATEX_SIMPLE_WRAPPER = re.compile(
    r"\\(?:text|mathrm|mathit|mathbf|operatorname)\s*\{([^{}]*)\}"
)
_LATEX_DELIMITER = re.compile(r"\\(?:\(|\)|\[|\])")
_LATEX_SPACING_COMMAND = re.compile(r"\\(?:,|;|:|!|quad\b|qquad\b)")
_LATEX_DEGREE = re.compile(r"\^\s*(?:\{\s*)?\\circ(?:\s*\})?")
_LATEX_NUMERIC_SUPERSCRIPT = re.compile(
    r"\^\s*(?:\{\s*([+\-−]?\s*\d+)\s*\}|([+\-−]?\d+))"
)
_LATEX_SIMPLE_SUBSCRIPT = re.compile(r"_\{\s*([A-Za-z0-9]+)\s*\}")
_LAYOUT_IDENTIFIER_HYPHEN_GAP = re.compile(
    r"(?<=[A-Za-z0-9])-[ ]+(?=\d)"
)
_SUPERSCRIPT_TRANSLATION = str.maketrans(
    {
        "0": "⁰",
        "1": "¹",
        "2": "²",
        "3": "³",
        "4": "⁴",
        "5": "⁵",
        "6": "⁶",
        "7": "⁷",
        "8": "⁸",
        "9": "⁹",
        "+": "⁺",
        "-": "⁻",
        "−": "⁻",
    }
)


class ExamMethodError(ValueError):
    """The pack or local evidence is invalid, so model quality is not scored."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest().upper()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def strip_markdown_presentation_syntax(value: str) -> str:
    """Remove only line-leading Markdown heading control markers."""

    if not isinstance(value, str):
        raise TypeError("OCR text must be a string")
    return _MARKDOWN_HEADING_PREFIX.sub("", value)


def canonicalize_latex_math_presentation(value: str) -> str:
    """Render a narrow, semantics-preserving LaTeX subset as plain Unicode.

    DeepSeek-OCR legitimately emits Markdown with LaTeX math even when the
    printed source uses Unicode symbols.  This canonicalizer removes only the
    observed presentation layer.  It does not infer missing content, correct
    values, change a sign, or repair an exponent.
    """

    if not isinstance(value, str):
        raise TypeError("OCR text must be a string")
    if _LATEX_PRESENTATION_HINT.search(value) is None:
        return value

    text = _LATEX_DELIMITER.sub("", value)
    text = _LATEX_SPACING_COMMAND.sub(" ", text)
    for _ in range(8):
        unwrapped = _LATEX_SIMPLE_WRAPPER.sub(lambda match: match.group(1), text)
        if unwrapped == text:
            break
        text = unwrapped

    text = _LATEX_DEGREE.sub("°", text)
    text = text.replace(r"\Delta", "Δ")
    text = text.replace(r"\alpha", "α")
    text = text.replace(r"\times", " × ")
    text = text.replace(r"\cdot", "·")
    text = text.replace(r"\_", "_")
    text = text.replace(r"\%", "%")
    text = text.replace(r"\#", "#")
    text = text.replace(r"\&", "&")
    text = _LATEX_SIMPLE_SUBSCRIPT.sub(lambda match: f"_{match.group(1)}", text)

    def superscript(match: re.Match[str]) -> str:
        token = (match.group(1) or match.group(2) or "").replace(" ", "")
        return token.translate(_SUPERSCRIPT_TRANSLATION)

    text = _LATEX_NUMERIC_SUPERSCRIPT.sub(superscript, text)
    # In TeX math, an ASCII hyphen preceding a number is a minus operator.
    # The condition excludes lexical identifiers such as A-01.
    text = re.sub(r"(?<![A-Za-z0-9_])-\s*(?=\d)", "−", text)
    # These spaces are introduced by TeX presentation commands, not recovered
    # source characters.  Raw provider bytes remain frozen separately.
    text = re.sub(r"[ \t]*·[ \t]*", "·", text)
    text = re.sub(r"°[ \t]+(?=[A-Za-z])", "°", text)
    text = re.sub(r"[ \t]+([,.;:!?])", r"\1", text)
    return text


def normalize_text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("OCR text must be a string")
    text = unicodedata.normalize("NFC", value)
    text = text.translate(_LIGATURES)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = strip_markdown_presentation_syntax(text)
    text = canonicalize_latex_math_presentation(text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_layout_text(value: str) -> str:
    """Return a layout-only matching view without changing literal scoring."""

    text = normalize_text(value)
    return _LAYOUT_IDENTIFIER_HYPHEN_GAP.sub("-", text)


def normalized_lines(value: str) -> list[str]:
    text = unicodedata.normalize("NFC", value).translate(_LIGATURES)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = strip_markdown_presentation_syntax(text)
    text = canonicalize_latex_math_presentation(text)
    lines: list[str] = []
    for line in text.split("\n"):
        normalized = re.sub(r"[^\S\n]+", " ", line).strip()
        if normalized:
            lines.append(normalized)
    return lines


def edit_distance(left: str, right: str) -> int:
    """Return the Levenshtein distance using O(min(m, n)) memory."""

    if left == right:
        return 0
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def character_similarity(expected: str, observed: str) -> float:
    expected_norm = normalize_text(expected)
    observed_norm = normalize_text(observed)
    denominator = max(len(expected_norm), len(observed_norm), 1)
    return max(0.0, 1.0 - edit_distance(expected_norm, observed_norm) / denominator)


def interpolate(knots: Sequence[Sequence[float]], value: float) -> float:
    if not knots:
        raise ExamMethodError("SCORING_CURVE_EMPTY")
    numeric = float(value)
    points = [(float(item[0]), float(item[1])) for item in knots]
    if numeric <= points[0][0]:
        return points[0][1]
    for (left_x, left_y), (right_x, right_y) in zip(points, points[1:]):
        if numeric <= right_x:
            if right_x == left_x:
                return right_y
            ratio = (numeric - left_x) / (right_x - left_x)
            return left_y + ratio * (right_y - left_y)
    return points[-1][1]


def _case_map(value: Mapping[str, Any], field: str) -> dict[str, Mapping[str, Any]]:
    rows = value.get(field)
    if not isinstance(rows, list):
        raise ExamMethodError(f"{field.upper()}_MUST_BE_LIST")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ExamMethodError(f"{field.upper()}_ROW_NOT_OBJECT")
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ExamMethodError(f"{field.upper()}_CASE_ID_INVALID")
        if case_id in result:
            raise ExamMethodError(f"{field.upper()}_DUPLICATE_CASE:{case_id}")
        result[case_id] = row
    return result


def audit_response(
    *,
    form: Mapping[str, Any],
    response: Mapping[str, Any],
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    expected = _case_map(form, "cases")
    outputs = response.get("outputs")
    if not isinstance(outputs, list):
        outputs = []
        issues.append({"code": "OUTPUTS_NOT_LIST", "case_id": None})

    observed: dict[str, Mapping[str, Any]] = {}
    for row in outputs:
        if not isinstance(row, Mapping):
            issues.append({"code": "OUTPUT_ROW_NOT_OBJECT", "case_id": None})
            continue
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            issues.append({"code": "OUTPUT_CASE_ID_INVALID", "case_id": None})
            continue
        if case_id in observed:
            issues.append({"code": "DUPLICATE_CASE_ID", "case_id": case_id})
            continue
        observed[case_id] = row

    for case_id in sorted(expected):
        if case_id not in observed:
            issues.append({"code": "MISSING_CASE_ID", "case_id": case_id})
            continue
        form_case = expected[case_id]
        output = observed[case_id]
        if output.get("image_sha256") != form_case.get("image_sha256"):
            issues.append({"code": "IMAGE_HASH_MISMATCH", "case_id": case_id})
        text = output.get("output_text")
        if not isinstance(text, str) or not text.strip():
            issues.append({"code": "EMPTY_OCR_TEXT", "case_id": case_id})
        elif output.get("output_sha256") != sha256_text(text):
            issues.append({"code": "OUTPUT_HASH_MISMATCH", "case_id": case_id})

    for case_id in sorted(set(observed) - set(expected)):
        issues.append({"code": "UNKNOWN_CASE_ID", "case_id": case_id})

    issue_codes = sorted({str(item["code"]) for item in issues})
    audit = {
        "schema_version": "m14-ocr-page-response-audit-v1",
        "form_id": form.get("form_id"),
        "expected_case_count": len(expected),
        "observed_case_count": len(observed),
        "issues": issues,
        "issue_codes": issue_codes,
        "valid": not issues,
    }
    audit["audit_sha256"] = sha256_json(audit)
    return audit


def _critical_token_results(
    observed: str,
    critical_tokens: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int, int, int, int]:
    observed_norm = normalize_text(observed)
    results: list[dict[str, Any]] = []
    matched = 0
    numeric_total = 0
    numeric_matched = 0
    for entry in critical_tokens:
        token = str(entry.get("token", ""))
        token_type = str(entry.get("token_type", "other"))
        found = bool(token) and normalize_text(token) in observed_norm
        matched += int(found)
        if token_type in {"numeric", "direction", "unit", "symbol", "negation"}:
            numeric_total += 1
            numeric_matched += int(found)
        results.append(
            {
                "token": token,
                "token_type": token_type,
                "hard_gate": bool(entry.get("hard_gate", False)),
                "observed_exact": found,
            }
        )
    return results, matched, len(critical_tokens), numeric_matched, numeric_total


def _segment_results(
    observed: str,
    segments: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], float, dict[str, int]]:
    observed_norm = normalize_layout_text(observed)
    cursor = 0
    found_count = 0
    ordered_count = 0
    results: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for segment in segments:
        segment_id = str(segment.get("segment_id", ""))
        text = normalize_layout_text(str(segment.get("text", "")))
        absolute_position = observed_norm.find(text) if text else -1
        ordered_position = observed_norm.find(text, cursor) if text else -1
        found = absolute_position >= 0
        ordered = ordered_position >= 0
        found_count += int(found)
        ordered_count += int(ordered)
        if ordered:
            cursor = ordered_position + len(text)
        if found:
            positions[segment_id] = absolute_position
        results.append(
            {
                "segment_id": segment_id,
                "role": segment.get("role"),
                "expected": text,
                "observed_found": found,
                "observed_in_order": ordered,
            }
        )
    if not segments:
        return results, 1.0, positions
    coverage = found_count / len(segments)
    order = ordered_count / len(segments)
    return results, (coverage + order) / 2.0, positions


def _line_for_segment(lines: Sequence[str], segment_text: str) -> int | None:
    needle = normalize_text(segment_text)
    for index, line in enumerate(lines):
        if needle and needle in normalize_text(line):
            return index
    return None


def _role_boundary_results(
    observed: str,
    segment_by_id: Mapping[str, Mapping[str, Any]],
    boundaries: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], float]:
    lines = normalized_lines(observed)
    results: list[dict[str, Any]] = []
    passed = 0
    for boundary in boundaries:
        before_id = str(boundary.get("before_segment_id", ""))
        after_id = str(boundary.get("after_segment_id", ""))
        before = segment_by_id.get(before_id, {})
        after = segment_by_id.get(after_id, {})
        before_line = _line_for_segment(lines, str(before.get("text", "")))
        after_line = _line_for_segment(lines, str(after.get("text", "")))
        separated = (
            before_line is not None
            and after_line is not None
            and after_line > before_line
        )
        passed += int(separated)
        results.append(
            {
                "before_segment_id": before_id,
                "after_segment_id": after_id,
                "hard_gate": bool(boundary.get("hard_gate", False)),
                "before_line": before_line,
                "after_line": after_line,
                "separated": separated,
            }
        )
    score = passed / len(boundaries) if boundaries else 1.0
    return results, score


def _faithfulness_results(
    observed: str,
    gold: Mapping[str, Any],
) -> tuple[dict[str, Any], float, list[str]]:
    observed_norm = normalize_text(observed)
    required = [normalize_text(str(item)) for item in gold.get("required_source_tokens", [])]
    forbidden = [normalize_text(str(item)) for item in gold.get("forbidden_normalizations", [])]
    required_results = [
        {"token": token, "observed_exact": bool(token and token in observed_norm)}
        for token in required
    ]
    forbidden_results = [
        {"token": token, "observed_present": bool(token and token in observed_norm)}
        for token in forbidden
    ]
    required_rate = (
        statistics.fmean(float(item["observed_exact"]) for item in required_results)
        if required_results
        else 1.0
    )
    forbidden_rate = (
        statistics.fmean(float(not item["observed_present"]) for item in forbidden_results)
        if forbidden_results
        else 1.0
    )

    policy = gold.get("uncertainty_policy")
    if not isinstance(policy, Mapping):
        policy = {"mode": "none"}
    mode = str(policy.get("mode", "none"))
    lower = observed.casefold()
    marker_present = any(marker.casefold() in lower for marker in UNCERTAINTY_MARKERS)
    accepted_markers = [
        str(item).casefold() for item in policy.get("accepted_markers", [])
    ]
    accepted_marker_present = any(marker in lower for marker in accepted_markers)
    forbidden_confident = [
        normalize_text(str(item)) for item in policy.get("forbidden_confident_tokens", [])
    ]
    hallucinated = [token for token in forbidden_confident if token and token in observed_norm]
    if mode == "none":
        uncertainty_score = 0.0 if marker_present else 1.0
    elif mode == "mark_or_omit":
        ambiguous_anchor = normalize_text(str(policy.get("ambiguous_anchor", "")))
        anchor_present = bool(ambiguous_anchor and ambiguous_anchor in observed_norm)
        uncertainty_score = 1.0 if accepted_marker_present or not anchor_present else 0.0
    else:
        uncertainty_score = 0.0

    score = statistics.fmean((required_rate, forbidden_rate, uncertainty_score))
    failures: list[str] = []
    if any(item["observed_present"] for item in forbidden_results):
        failures.append("SOURCE_AS_PRINTED_SILENT_CORRECTION")
    if hallucinated:
        failures.append("UNCERTAINTY_CONFIDENT_HALLUCINATION")
    if mode == "none" and marker_present and bool(gold.get("clean_control", False)):
        failures.append("CLEAN_FALSE_UNCERTAINTY")
    return (
        {
            "required_source_tokens": required_results,
            "forbidden_normalizations": forbidden_results,
            "uncertainty_mode": mode,
            "uncertainty_marker_present": marker_present,
            "accepted_uncertainty_marker_present": accepted_marker_present,
            "forbidden_confident_tokens_observed": hallucinated,
        },
        score,
        failures,
    )


def score_case(
    *,
    form_case: Mapping[str, Any],
    gold_case: Mapping[str, Any],
    output: Mapping[str, Any],
) -> dict[str, Any]:
    observed = str(output["output_text"])
    expected = str(gold_case["expected_text"])
    literal = character_similarity(expected, observed)

    critical_tokens = gold_case.get("critical_tokens", [])
    if not isinstance(critical_tokens, list):
        raise ExamMethodError(f"CRITICAL_TOKENS_NOT_LIST:{form_case.get('case_id')}")
    critical_results, matched, total, numeric_matched, numeric_total = (
        _critical_token_results(observed, critical_tokens)
    )
    critical_rate = matched / total if total else 1.0
    numeric_rate = numeric_matched / numeric_total if numeric_total else 1.0

    segments = gold_case.get("ordered_segments", [])
    if not isinstance(segments, list):
        raise ExamMethodError(f"ORDERED_SEGMENTS_NOT_LIST:{form_case.get('case_id')}")
    segment_results, layout_score, _ = _segment_results(observed, segments)
    segment_by_id = {
        str(item.get("segment_id")): item for item in segments if isinstance(item, Mapping)
    }

    boundaries = gold_case.get("role_boundaries", [])
    if not isinstance(boundaries, list):
        raise ExamMethodError(f"ROLE_BOUNDARIES_NOT_LIST:{form_case.get('case_id')}")
    role_results, role_score = _role_boundary_results(
        observed,
        segment_by_id,
        boundaries,
    )

    faithfulness, faithfulness_score, faithfulness_failures = _faithfulness_results(
        observed,
        gold_case,
    )

    hard_failures: list[str] = []
    for result in critical_results:
        if result["hard_gate"] and not result["observed_exact"]:
            hard_failures.append(
                f"CRITICAL_TOKEN_MISMATCH:{result['token_type']}:{result['token']}"
            )
    for result in segment_results:
        segment = segment_by_id.get(str(result["segment_id"]), {})
        if bool(segment.get("hard_gate", False)) and not result["observed_in_order"]:
            hard_failures.append(f"CRITICAL_READING_ORDER:{result['segment_id']}")
    for result in role_results:
        if result["hard_gate"] and not result["separated"]:
            hard_failures.append(
                "CRITICAL_ROLE_BOUNDARY:"
                f"{result['before_segment_id']}->{result['after_segment_id']}"
            )
    hard_failures.extend(faithfulness_failures)

    clean_specificity = 1.0
    if bool(gold_case.get("clean_control", False)):
        clean_specificity = min(literal, faithfulness_score)
        if literal < 0.98:
            hard_failures.append("CLEAN_SUBSTANTIVE_OMISSION_OR_INSERTION")

    case_score = 100.0 * (
        0.30 * critical_rate
        + 0.30 * literal
        + 0.15 * layout_score
        + 0.10 * role_score
        + 0.15 * faithfulness_score
    )
    result = {
        "case_id": form_case["case_id"],
        "family": form_case["family"],
        "difficulty": form_case["difficulty"],
        "literal_similarity": round(literal, 8),
        "character_error_rate": round(1.0 - literal, 8),
        "critical_token_rate": round(critical_rate, 8),
        "numeric_token_rate": round(numeric_rate, 8),
        "layout_order_rate": round(layout_score, 8),
        "role_boundary_rate": round(role_score, 8),
        "faithfulness_rate": round(faithfulness_score, 8),
        "clean_specificity": round(clean_specificity, 8),
        "case_score": round(max(0.0, min(100.0, case_score)), 6),
        "critical_tokens": critical_results,
        "ordered_segments": segment_results,
        "role_boundaries": role_results,
        "faithfulness": faithfulness,
        "hard_failures": sorted(set(hard_failures)),
        "expected_observed": {
            "expected_text": expected,
            "observed_text": observed,
            "expected_sha256": sha256_text(expected),
            "observed_sha256": sha256_text(observed),
        },
    }
    result["detail_sha256"] = sha256_json(result)
    return result


def _layer_points(
    scoring_protocol: Mapping[str, Any],
    layer: str,
    raw_score: float,
) -> float:
    curves = scoring_protocol.get("curves")
    if not isinstance(curves, Mapping):
        raise ExamMethodError("SCORING_CURVES_MISSING")
    knots = curves.get(layer)
    if not isinstance(knots, list):
        raise ExamMethodError(f"SCORING_CURVE_MISSING:{layer}")
    maximums = scoring_protocol.get("layer_max_points")
    if not isinstance(maximums, Mapping):
        raise ExamMethodError("SCORING_LAYER_MAX_POINTS_MISSING")
    maximum = float(maximums.get(layer, -1.0))
    return max(0.0, min(maximum, interpolate(knots, raw_score)))


def score_form(
    *,
    form: Mapping[str, Any],
    gold: Mapping[str, Any],
    response: Mapping[str, Any],
    scoring_protocol: Mapping[str, Any],
) -> dict[str, Any]:
    audit = audit_response(form=form, response=response)
    if not audit["valid"]:
        codes = ",".join(audit["issue_codes"])
        raise ExamMethodError(f"INVALID_LOCAL_EVIDENCE:{codes}")

    form_cases = _case_map(form, "cases")
    gold_cases = _case_map(gold, "cases")
    outputs = _case_map(response, "outputs")
    if set(form_cases) != set(gold_cases):
        raise ExamMethodError("FORM_GOLD_CASE_PARTITION_MISMATCH")
    if set(form_cases) != set(outputs):
        raise ExamMethodError("FORM_OUTPUT_CASE_PARTITION_MISMATCH")

    details = [
        score_case(
            form_case=form_cases[case_id],
            gold_case=gold_cases[case_id],
            output=outputs[case_id],
        )
        for case_id in form_cases
    ]
    if len(details) != 48:
        raise ExamMethodError(f"FORM_CASE_COUNT_NOT_48:{len(details)}")

    family_scores: dict[str, float] = {}
    for family in FAMILIES:
        values = [item["case_score"] for item in details if item["family"] == family]
        if len(values) != 6:
            raise ExamMethodError(f"FAMILY_CASE_COUNT_NOT_6:{family}:{len(values)}")
        family_scores[family] = statistics.fmean(values)

    critical_total = sum(len(item["critical_tokens"]) for item in details)
    critical_matched = sum(
        sum(int(token["observed_exact"]) for token in item["critical_tokens"])
        for item in details
    )
    critical_rate = critical_matched / critical_total if critical_total else 1.0
    literal_rate = statistics.fmean(item["literal_similarity"] for item in details)

    layout_items = [
        item
        for item in details
        if item["family"] in {"reading_order_layout", "page_role_separation"}
    ]
    layout_rate = statistics.fmean(item["layout_order_rate"] for item in layout_items)
    role_items = [item for item in details if item["family"] == "page_role_separation"]
    role_rate = statistics.fmean(item["role_boundary_rate"] for item in role_items)
    faith_items = [
        item
        for item in details
        if item["family"] in {"faithfulness_uncertainty", "clean_control"}
    ]
    faithfulness_rate = statistics.fmean(item["faithfulness_rate"] for item in faith_items)
    hard_failures = sorted(
        {
            f"{item['case_id']}:{failure}"
            for item in details
            for failure in item["hard_failures"]
        }
    )
    clean_false_positive_count = sum(
        any(
            failure in {"CLEAN_FALSE_UNCERTAINTY", "CLEAN_SUBSTANTIVE_OMISSION_OR_INSERTION"}
            for failure in item["hard_failures"]
        )
        for item in details
        if item["family"] == "clean_control"
    )
    numeric_tokens = [
        token
        for item in details
        for token in item["critical_tokens"]
        if token["token_type"] in {"numeric", "direction", "unit", "symbol", "negation"}
    ]
    numeric_rate = (
        statistics.fmean(float(item["observed_exact"]) for item in numeric_tokens)
        if numeric_tokens
        else 1.0
    )
    overall_case_quality = statistics.fmean(
        float(item["case_score"]) for item in details
    )
    hard_cases = [
        item for item in details if item.get("difficulty") == "hard"
    ]
    if len(hard_cases) != 16:
        raise ExamMethodError(f"HARD_CASE_COUNT_NOT_16:{len(hard_cases)}")
    hard_case_quality = statistics.fmean(
        float(item["case_score"]) for item in hard_cases
    )
    bottom_count = max(1, math.ceil(len(details) * 0.10))
    bottom_decile_quality = statistics.fmean(
        sorted(float(item["case_score"]) for item in details)[:bottom_count]
    )
    worst_family_quality = min(family_scores.values())
    strict_output_contract = 100.0
    layer_raw_scores = {
        "overall_case_quality": overall_case_quality,
        "hard_case_quality": hard_case_quality,
        "worst_family_quality": worst_family_quality,
        "bottom_decile_quality": bottom_decile_quality,
        "strict_output_contract": strict_output_contract,
    }
    layer_max_points = scoring_protocol.get("layer_max_points")
    if not isinstance(layer_max_points, Mapping):
        raise ExamMethodError("SCORING_LAYER_MAX_POINTS_MISSING")
    actual_max_total = sum(float(value) for value in layer_max_points.values())
    if not math.isclose(actual_max_total, 100.0, abs_tol=1e-9):
        raise ExamMethodError(
            f"SCORING_LAYER_MAX_TOTAL_INVALID:{actual_max_total}"
        )
    layer_points = {
        layer: _layer_points(scoring_protocol, layer, raw_score)
        for layer, raw_score in layer_raw_scores.items()
        if layer != "strict_output_contract"
    }
    layer_points["strict_output_contract"] = float(
        layer_max_points["strict_output_contract"]
    )
    base_score = max(0.0, min(100.0, sum(layer_points.values())))
    critical_any = bool(hard_failures)
    critical_cap = scoring_protocol.get("critical_hard_failure_score_cap")
    if critical_cap is not None:
        raise ExamMethodError("SCORING_HARD_FAILURE_CAP_MUST_BE_NULL")
    score_caps_applied: list[str] = []
    score = base_score

    result = {
        "schema_version": FORM_SCORE_SCHEMA_VERSION,
        "normalization_profile_id": NORMALIZATION_PROFILE_ID,
        "form_id": form.get("form_id"),
        "status": "SCORED",
        "score": round(score, 6),
        "base_score_before_caps": round(base_score, 6),
        "hard_gate_verdict": "PASS" if not hard_failures else "FAIL",
        "hard_failures": hard_failures,
        "critical_hard_failure": critical_any,
        "critical_hard_failure_score_cap": critical_cap,
        "score_caps_applied": score_caps_applied,
        "score_is_uncapped": True,
        "score_equals_base_before_caps": math.isclose(
            score,
            base_score,
            abs_tol=1e-12,
        ),
        "layer_raw_scores": {
            key: round(value, 8) for key, value in layer_raw_scores.items()
        },
        "layer_points": {
            key: round(value, 6) for key, value in layer_points.items()
        },
        "diagnostic_raw_rates": {
            "critical_token_fidelity": round(critical_rate, 8),
            "general_literal_fidelity": round(literal_rate, 8),
            "layout_reading_order": round(layout_rate, 8),
            "page_role_boundary": round(role_rate, 8),
            "source_faithfulness": round(faithfulness_rate, 8),
        },
        "family_scores": {
            key: round(value, 6) for key, value in family_scores.items()
        },
        "character_error_rate": round(1.0 - literal_rate, 8),
        "critical_token_exact_rate": round(critical_rate, 8),
        "numeric_symbol_exact_rate": round(numeric_rate, 8),
        "layout_order_rate": round(layout_rate, 8),
        "role_boundary_rate": round(role_rate, 8),
        "source_faithfulness_rate": round(faithfulness_rate, 8),
        "worst_family_score": round(worst_family_quality, 8),
        "bottom_decile_score": round(bottom_decile_quality, 8),
        "bottom_decile_case_count": bottom_count,
        "clean_false_positive_count": clean_false_positive_count,
        "case_details": details,
        "response_audit": audit,
        "provider_identity_used_by_scorer": False,
        "gold_sent_to_provider": False,
    }
    result["score_sha256"] = sha256_json(result)
    return result


def finalize_form(
    *,
    form: Mapping[str, Any],
    gold: Mapping[str, Any],
    response: Mapping[str, Any] | None,
    scoring_protocol: Mapping[str, Any],
    terminal_reason: str | None = None,
) -> dict[str, Any]:
    if terminal_reason in {"capacity", "max_tokens", "length"}:
        return {
            "schema_version": FORM_SCORE_SCHEMA_VERSION,
            "form_id": form.get("form_id"),
            "status": "NOT_ASSESSED_CAPACITY",
            "score": None,
            "hard_gate_verdict": "NOT_ASSESSED",
            "terminal_reason": terminal_reason,
        }
    if response is None:
        return {
            "schema_version": FORM_SCORE_SCHEMA_VERSION,
            "form_id": form.get("form_id"),
            "status": "NOT_ASSESSED_NONDELIVERY",
            "score": None,
            "hard_gate_verdict": "NOT_ASSESSED",
            "terminal_reason": terminal_reason or "no_payload",
        }
    try:
        return score_form(
            form=form,
            gold=gold,
            response=response,
            scoring_protocol=scoring_protocol,
        )
    except ExamMethodError as exc:
        return {
            "schema_version": FORM_SCORE_SCHEMA_VERSION,
            "form_id": form.get("form_id"),
            "status": "INVALIDATED_METHOD",
            "score": None,
            "hard_gate_verdict": "NOT_ASSESSED",
            "method_error": str(exc),
        }


def publish_dual_form_result(
    *,
    forms: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    form_a = forms.get("A")
    form_b = forms.get("B")
    if not isinstance(form_a, Mapping) or not isinstance(form_b, Mapping):
        raise ExamMethodError("DUAL_FORM_RESULT_REQUIRES_A_AND_B")
    scored = all(
        item.get("status") == "SCORED" and isinstance(item.get("score"), (int, float))
        for item in (form_a, form_b)
    )
    if scored:
        score_a = float(form_a["score"])
        score_b = float(form_b["score"])
        total = statistics.fmean((score_a, score_b))
        hard_gate = (
            "PASS"
            if form_a.get("hard_gate_verdict") == "PASS"
            and form_b.get("hard_gate_verdict") == "PASS"
            else "FAIL"
        )
        if total < 60.0:
            score_band = "NOT_RECOMMENDED"
            recommendation = "NOT_RECOMMENDED_AS_OCR_PAGE_MODEL"
        elif total < 80.0:
            score_band = "USE_WITH_CAUTION"
            recommendation = "USE_WITH_CAUTION_AS_OCR_PAGE_MODEL"
        else:
            score_band = "RECOMMENDED"
            recommendation = "RECOMMENDED_AS_OCR_PAGE_MODEL"
        form_min = min(score_a, score_b)
        form_gap = abs(score_a - score_b)
    else:
        score_a = score_b = total = form_min = form_gap = None
        hard_gate = "NOT_ASSESSED"
        score_band = "NOT_ASSESSED"
        recommendation = "OCR_PAGE_QUALITY_NOT_ASSESSED"

    result = {
        "schema_version": DUAL_FORM_RESULT_SCHEMA_VERSION,
        "form_A_score": score_a,
        "form_B_score": score_b,
        "total_score_out_of_100": round(total, 6) if total is not None else None,
        "form_min": round(form_min, 6) if form_min is not None else None,
        "form_gap": round(form_gap, 6) if form_gap is not None else None,
        "quality_hard_gate_verdict": hard_gate,
        "score_band": score_band,
        "score_band_policy": {
            "NOT_RECOMMENDED": "[0,60)",
            "USE_WITH_CAUTION": "[60,80)",
            "RECOMMENDED": "[80,100]",
        },
        "recommendation": recommendation,
        "forms": {"A": dict(form_a), "B": dict(form_b)},
    }
    result["result_sha256"] = sha256_json(result)
    return result


def perfect_response(
    *,
    form: Mapping[str, Any],
    gold: Mapping[str, Any],
) -> dict[str, Any]:
    form_cases = _case_map(form, "cases")
    gold_cases = _case_map(gold, "cases")
    if set(form_cases) != set(gold_cases):
        raise ExamMethodError("FORM_GOLD_CASE_PARTITION_MISMATCH")
    outputs = []
    for case_id, form_case in form_cases.items():
        text = str(gold_cases[case_id]["expected_text"])
        outputs.append(
            {
                "case_id": case_id,
                "image_sha256": form_case["image_sha256"],
                "output_text": text,
                "output_sha256": sha256_text(text),
                "transport_status": "LOCAL_PERFECT_REPLAY",
                "finish_reason": "local",
            }
        )
    return {
        "schema_version": OUTPUT_BUNDLE_SCHEMA_VERSION,
        "form_id": form.get("form_id"),
        "generation": "gen1",
        "outputs": outputs,
        "provider_requests": 0,
        "gold_sent_to_provider": False,
    }


def score_stability_subset(
    *,
    form: Mapping[str, Any],
    generations: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    required = {"gen1", "gen2", "gen3"}
    if set(generations) != required:
        raise ExamMethodError("STABILITY_REQUIRES_GEN1_GEN2_GEN3")
    case_map = _case_map(form, "cases")
    subset = [
        case_id
        for case_id, case in case_map.items()
        if bool(case.get("stability_subset", False))
    ]
    if len(subset) != 6:
        raise ExamMethodError(f"STABILITY_SUBSET_COUNT_NOT_6:{len(subset)}")
    generation_maps = {
        generation: _case_map(bundle, "outputs")
        for generation, bundle in generations.items()
    }
    details: list[dict[str, Any]] = []
    drift_count = 0
    for case_id in subset:
        hashes = {
            generation: sha256_text(str(outputs[case_id]["output_text"]))
            for generation, outputs in generation_maps.items()
        }
        drift = len(set(hashes.values())) > 1
        drift_count += int(drift)
        details.append(
            {
                "case_id": case_id,
                "output_sha256": hashes,
                "drift": drift,
            }
        )
    result = {
        "schema_version": STABILITY_SCHEMA_VERSION,
        "form_id": form.get("form_id"),
        "subset_count": len(subset),
        "drift_case_count": drift_count,
        "stable_case_rate": round(1.0 - drift_count / len(subset), 8),
        "included_in_primary_score": False,
        "details": details,
    }
    result["stability_sha256"] = sha256_json(result)
    return result
