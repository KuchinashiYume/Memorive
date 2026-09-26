"""Deterministic self-synthetic assets for the OCR page reference exam."""

from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from .ocr_page_exam import FAMILIES, canonical_bytes, sha256_json, sha256_text


PACK_SCHEMA_VERSION = "m14-ocr-page-reference-exam-pack-v1"
FORM_SCHEMA_VERSION = "m14-ocr-page-form-v1"
GOLD_SCHEMA_VERSION = "m14-ocr-page-gold-v1"
PROMPT_SHA256 = "F9D21C498ADBC86FC5518A9EBB879F67352AAC3D40DAD3C3B544086F4631EE5C"
REFERENCE_PACK_FAMILY_ID = "P02-T08-M14-OCR-PAGE-REFERENCE-EXAM-V3"
REFERENCE_PACK_ID = "P02-T08-M14-OCR-PAGE-REFERENCE-EXAM-V3-S01"
REFERENCE_PACK_REVISION = "r2.0-formal"
PREDECESSOR_PACK_ID = "P02-T08-M14-OCR-PAGE-REFERENCE-EXAM-V2-S01"
PREDECESSOR_PACK_REVISION = "r1.0-formal"
DECLASSIFICATION_AUTHORIZATION_ID = (
    "PR-OS-P02-T08-M14-OCR-PAGE-REFERENCE-EXAM-PACK-"
    "NATURAL-SCORE-NO-CAP-FORMAL-WRITE-20260731"
)
FORMALIZATION_SOURCE_MANIFEST_SHA256 = (
    "01AF8E763CD208181B4C24DF4902CA0A9E1CB8EE3BCDF5B4DF6C047FB107B11C"
)
CANONICAL_REPO_RELATIVE_PATH = (
    "m14_quality_sentinel/assets/ocr_page_exam/v3/reference_pack"
)
RENDER_PROFILE_ID = "p02-t08-m14-ocr-page-self-synthetic-200dpi-v1"
NORMALIZATION_PROFILE_ID = "p02-t08-m14-ocr-page-normalization-v3"
SCORING_PROTOCOL_ID = "p02-t08-m14-ocr-page-scoring-v4"

FONT_PATHS = {
    "primary": "C:/Windows/Fonts/msyh.ttc",
    "scientific": "C:/Windows/Fonts/cambria.ttc",
    "symbols": "C:/Windows/Fonts/seguisym.ttf",
    "mono": "C:/Windows/Fonts/consola.ttf",
}
FONT_SHA256 = {
    "primary": "D79C55E68B1131EEA0CC1C47BE4F572D964F28C682E143DB2AD09C1E4CB07A3F",
    "scientific": "84E70CCC1664482F4A960442C7A166C91A1B2CF98FF88C33CB73F79403F66D7B",
    "symbols": "D2F8326A354456D93E78B0537C58793E7072C3617AF7D9EE187F10D6D595F510",
    "mono": "C6E6CE8119FDD47EC6A5449A08E2D2AD7F41EA03143AAE193068ED9FA58EAEBC",
}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _difficulty_for(ordinal: int) -> str:
    return ("easy", "easy", "medium", "medium", "hard", "hard")[ordinal - 1]


def _form_seed(form_name: str) -> int:
    return {"A": 41001, "B": 87001}[form_name]


def _case_id(form_name: str, family_index: int, ordinal: int) -> str:
    return f"OCR-T-{form_name}-{family_index:02d}-{ordinal:02d}"


def _critical(
    token: str,
    token_type: str,
    *,
    hard_gate: bool = True,
) -> dict[str, Any]:
    return {
        "token": token,
        "token_type": token_type,
        "hard_gate": hard_gate,
    }


def _segment(
    segment_id: str,
    role: str,
    text: str,
    *,
    hard_gate: bool = False,
) -> dict[str, Any]:
    return {
        "segment_id": segment_id,
        "role": role,
        "text": text,
        "hard_gate": hard_gate,
    }


def _case_content(
    form_name: str,
    family: str,
    ordinal: int,
) -> dict[str, Any]:
    serial = (0 if form_name == "A" else 500) + FAMILIES.index(family) * 10 + ordinal
    tag = f"{form_name}{serial:03d}"
    difficulty = _difficulty_for(ordinal)
    title = f"OCR reference region {tag}"
    segments: list[dict[str, Any]] = []
    boundaries: list[dict[str, Any]] = []
    forbidden: list[str] = []
    required_source_tokens: list[str] = []
    uncertainty: dict[str, Any] = {"mode": "none"}
    clean = False
    render_kind = "single"

    if family == "literal_text":
        phrase = (
            f"Batch {tag} was not re-tested; the pre-filtered sample "
            f"remained blue-green after cycle {17 + ordinal}."
        )
        expected = f"{title}\n\n{phrase}"
        critical = [
            _critical(tag, "identity", hard_gate=False),
            _critical("not", "negation"),
            _critical("pre-filtered", "literal"),
            _critical("blue-green", "literal"),
        ]
        segments = [
            _segment("title", "heading", title),
            _segment("body", "body", phrase),
        ]
    elif family == "numeric_direction":
        value = f"−0.{70 + serial % 23:02d}"
        percentage = f"{81 + ordinal}.{serial % 10}%"
        p_value = f"0.0{20 + ordinal}"
        phrase = (
            f"Sample {tag}: Δ = {value} mg·kg⁻¹; retention = {percentage}; "
            f"p < {p_value}."
        )
        expected = f"{title}\n\n{phrase}"
        critical = [
            _critical(value, "direction"),
            _critical("mg·kg⁻¹", "unit"),
            _critical(percentage, "numeric"),
            _critical("<", "direction"),
            _critical(p_value, "numeric"),
        ]
        segments = [
            _segment("title", "heading", title),
            _segment("body", "body", phrase),
        ]
    elif family == "units_symbols_scripts":
        coefficient = f"{1 + ordinal}.{20 + serial % 70:02d}"
        alpha = f"0.{30 + ordinal + serial % 10}"
        phrase = (
            f"Flux J_{tag} = {coefficient} × 10⁻³ mol·m⁻²·s⁻¹; "
            f"α = {alpha}; temperature = 23 °C."
        )
        expected = f"{title}\n\n{phrase}"
        critical = [
            _critical(f"J_{tag}", "symbol"),
            _critical(coefficient, "numeric"),
            _critical("×", "symbol"),
            _critical("10⁻³", "symbol"),
            _critical("mol·m⁻²·s⁻¹", "unit"),
            _critical("α", "symbol"),
            _critical(alpha, "numeric"),
            _critical("23 °C", "unit"),
        ]
        segments = [
            _segment("title", "heading", title),
            _segment("body", "body", phrase),
        ]
        render_kind = "scientific"
    elif family == "reading_order_layout":
        left_one = f"LEFT {tag}-1: influent = {110 + serial % 50} mg/L."
        left_two = f"LEFT {tag}-2: phase ended before dosing."
        right_one = f"RIGHT {tag}-1: effluent = {20 + ordinal}.{serial % 10} mg/L."
        right_two = f"RIGHT {tag}-2: recovery started after sampling."
        expected = (
            f"{title}\n\n{left_one}\n{left_two}\n\n"
            f"{right_one}\n{right_two}"
        )
        critical = [
            _critical(f"LEFT {tag}-1", "identity", hard_gate=False),
            _critical(f"RIGHT {tag}-1", "identity", hard_gate=False),
            _critical("before", "direction"),
            _critical("after", "direction"),
        ]
        segments = [
            _segment("title", "heading", title),
            _segment("left", "body", f"{left_one} {left_two}", hard_gate=True),
            _segment("right", "body", f"{right_one} {right_two}", hard_gate=True),
        ]
        render_kind = "columns"
    elif family == "page_role_separation":
        header = f"JOURNAL HEADER {tag} · PAGE {100 + ordinal}"
        body = (
            f"Body {tag}: removal reached {70 + ordinal}.{serial % 10}% "
            f"after the controlled stage."
        )
        footnote = f"Footnote {tag}: value is dry-weight normalized."
        reference = (
            f"[{20 + ordinal}] Author {tag}. Controlled observation report."
        )
        expected = f"{header}\n\n{body}\n\n{footnote}\n\n{reference}"
        critical = [
            _critical(f"{70 + ordinal}.{serial % 10}%", "numeric"),
            _critical("after", "direction"),
            _critical("dry-weight", "literal"),
        ]
        segments = [
            _segment("header", "header_footer", header),
            _segment("body", "body", body, hard_gate=True),
            _segment("footnote", "footnote", footnote, hard_gate=True),
            _segment("reference", "references", reference, hard_gate=True),
        ]
        boundaries = [
            {
                "before_segment_id": "header",
                "after_segment_id": "body",
                "hard_gate": True,
            },
            {
                "before_segment_id": "body",
                "after_segment_id": "footnote",
                "hard_gate": True,
            },
            {
                "before_segment_id": "footnote",
                "after_segment_id": "reference",
                "hard_gate": True,
            },
        ]
        render_kind = "roles"
    elif family == "noise_degradation":
        code = f"ND-{tag}-{700 + serial}"
        value = f"{4 + ordinal}.{serial % 10}{(serial + 3) % 10}"
        phrase = (
            f"Degraded scan {code}: conductivity = {value} mS·cm⁻¹; "
            f"direction remained downward."
        )
        expected = f"{title}\n\n{phrase}"
        critical = [
            _critical(code, "identity", hard_gate=False),
            _critical(value, "numeric"),
            _critical("mS·cm⁻¹", "unit"),
            _critical("downward", "direction"),
        ]
        segments = [
            _segment("title", "heading", title),
            _segment("body", "body", phrase),
        ]
        render_kind = "degraded"
    elif family == "faithfulness_uncertainty":
        printed = f"O.{40 + ordinal}"
        hidden = f"K{serial % 10}X{ordinal}"
        prefix = f"Record {tag}: source printed {printed} mg/L; obscured code:"
        expected = f"{title}\n\n{prefix} [unclear]"
        critical = [
            _critical(printed, "literal"),
            _critical("mg/L", "unit"),
        ]
        segments = [
            _segment("title", "heading", title),
            _segment("body", "body", prefix),
        ]
        required_source_tokens = [printed]
        forbidden = [printed.replace("O.", "0.")]
        uncertainty = {
            "mode": "mark_or_omit",
            "ambiguous_anchor": "obscured code:",
            "accepted_markers": ["[unclear]", "[illegible]", "[unreadable]", "<?>"],
            "forbidden_confident_tokens": [hidden],
        }
        render_kind = "uncertain"
    elif family == "clean_control":
        phrase = (
            f"Clean control {tag}: the sample was stored locally for "
            f"{24 + ordinal} hours."
        )
        expected = f"{title}\n\n{phrase}"
        critical = [
            _critical(tag, "identity", hard_gate=False),
            _critical(f"{24 + ordinal}", "numeric"),
            _critical("locally", "literal"),
        ]
        segments = [
            _segment("title", "heading", title),
            _segment("body", "body", phrase),
        ]
        clean = True
        render_kind = "clean"
    else:
        raise ValueError(f"UNKNOWN_FAMILY:{family}")

    return {
        "title": title,
        "expected_text": expected,
        "critical_tokens": critical,
        "ordered_segments": segments,
        "role_boundaries": boundaries,
        "forbidden_normalizations": forbidden,
        "required_source_tokens": required_source_tokens,
        "uncertainty_policy": uncertainty,
        "clean_control": clean,
        "render_kind": render_kind,
        "hidden_text": hidden if family == "faithfulness_uncertainty" else None,
        "difficulty": difficulty,
    }


def _font_sha256(path: str) -> str:
    file_path = Path(path)
    if not file_path.is_file():
        raise RuntimeError(f"FONT_NOT_FOUND:{path}")
    return _sha256_file(file_path)


def _validate_fonts() -> None:
    for role, path in FONT_PATHS.items():
        observed = _font_sha256(path)
        expected = FONT_SHA256[role]
        if observed != expected:
            raise RuntimeError(f"FONT_HASH_MISMATCH:{role}:{observed}:{expected}")


def _draw_wrapped(draw: Any, position: tuple[int, int], text: str, font: Any, width: int, fill: int) -> None:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    draw.multiline_text(position, "\n".join(lines), font=font, fill=fill, spacing=12)


def _render_image(
    *,
    path: Path,
    content: dict[str, Any],
    form_name: str,
    family: str,
    ordinal: int,
) -> None:
    try:
        from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
    except ModuleNotFoundError as exc:
        raise RuntimeError("PIL_REQUIRED_FOR_ASSET_BUILD") from exc

    image = Image.new("L", (1200, 800), color=248)
    draw = ImageDraw.Draw(image)
    primary = ImageFont.truetype(FONT_PATHS["primary"], 34)
    small = ImageFont.truetype(FONT_PATHS["primary"], 28)
    scientific = ImageFont.truetype(FONT_PATHS["scientific"], 34)
    mono = ImageFont.truetype(FONT_PATHS["mono"], 30)
    title_font = ImageFont.truetype(FONT_PATHS["primary"], 39)
    fill = 24

    title = content["title"]
    kind = content["render_kind"]
    if kind == "columns":
        segments = {item["segment_id"]: item["text"] for item in content["ordered_segments"]}
        draw.text((70, 55), title, font=title_font, fill=fill)
        draw.line((70, 120, 1130, 120), fill=90, width=2)
        left_parts = segments["left"].split(". ")
        right_parts = segments["right"].split(". ")
        _draw_wrapped(draw, (75, 180), ".\n".join(left_parts), small, 455, fill)
        _draw_wrapped(draw, (650, 180), ".\n".join(right_parts), small, 455, fill)
        draw.line((600, 155, 600, 690), fill=175, width=2)
    elif kind == "roles":
        segments = {item["segment_id"]: item["text"] for item in content["ordered_segments"]}
        draw.text((70, 45), segments["header"], font=small, fill=75)
        draw.line((70, 95, 1130, 95), fill=180, width=2)
        _draw_wrapped(draw, (95, 245), segments["body"], primary, 1010, fill)
        draw.line((95, 575, 1105, 575), fill=160, width=2)
        _draw_wrapped(draw, (95, 600), segments["footnote"], small, 1010, 55)
        draw.text((95, 720), segments["reference"], font=small, fill=55)
    else:
        draw.text((70, 60), title, font=title_font, fill=fill)
        draw.line((70, 125, 1130, 125), fill=175, width=2)
        body = content["expected_text"].split("\n\n", 1)[1]
        font = (
            scientific
            if kind == "scientific"
            or family
            in {
                "numeric_direction",
                "units_symbols_scripts",
                "noise_degradation",
            }
            else primary
        )
        if kind == "uncertain":
            visible = body.replace(" [unclear]", "")
            _draw_wrapped(draw, (90, 235), visible, font, 980, fill)
            hidden = str(content["hidden_text"])
            anchor_width = draw.textlength(visible, font=font)
            hidden_x = min(1000, 90 + int(anchor_width) + 18)
            draw.text((hidden_x, 235), hidden, font=mono, fill=70)
            draw.rectangle((hidden_x - 4, 226, hidden_x + 115, 284), fill=198)
            for offset in range(0, 110, 9):
                shade = 170 + (offset * 7) % 55
                draw.line(
                    (hidden_x + offset, 228, hidden_x + offset + 14, 282),
                    fill=shade,
                    width=5,
                )
        else:
            _draw_wrapped(draw, (90, 235), body, font, 1000, fill)

    difficulty = content["difficulty"]
    if kind == "degraded" or (difficulty == "hard" and family != "clean_control"):
        seed = _form_seed(form_name) + FAMILIES.index(family) * 100 + ordinal
        rng = random.Random(seed)
        if difficulty == "medium":
            image = ImageEnhance.Contrast(image).enhance(0.78)
            angle = 0.35 if ordinal % 2 else -0.35
            image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=248)
        elif difficulty == "hard":
            image = ImageEnhance.Contrast(image).enhance(0.58)
            angle = 0.75 if ordinal % 2 else -0.75
            image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=248)
            image = image.filter(ImageFilter.GaussianBlur(radius=0.55))
        pixels = image.load()
        noise_points = 900 if difficulty == "hard" else 350
        for _ in range(noise_points):
            x = rng.randrange(0, image.width)
            y = rng.randrange(0, image.height)
            pixels[x, y] = rng.randrange(150, 245)

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=False, dpi=(200, 200))


def _build_form(
    *,
    form_name: str,
    pack_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    gold_cases: list[dict[str, Any]] = []
    selected_stability_families = {
        "numeric_direction",
        "units_symbols_scripts",
        "reading_order_layout",
        "page_role_separation",
        "noise_degradation",
        "faithfulness_uncertainty",
    }
    for family_index, family in enumerate(FAMILIES, start=1):
        for ordinal in range(1, 7):
            case_id = _case_id(form_name, family_index, ordinal)
            content = _case_content(form_name, family, ordinal)
            image_relative = f"images/{form_name}/{case_id}.png"
            image_path = pack_root / image_relative
            _render_image(
                path=image_path,
                content=content,
                form_name=form_name,
                family=family,
                ordinal=ordinal,
            )
            image_sha256 = _sha256_file(image_path)
            serial = family_index * 100 + ordinal
            source_id = f"SELF-SYNTH-{form_name}-SRC-{serial:04d}"
            page_id = f"SELF-SYNTH-{form_name}-PAGE-{serial:04d}"
            region_id = f"SELF-SYNTH-{form_name}-REGION-{serial:04d}"
            case = {
                "case_id": case_id,
                "family": family,
                "difficulty": content["difficulty"],
                "image_ref": image_relative,
                "image_sha256": image_sha256,
                "source_id": source_id,
                "page_id": page_id,
                "region_id": region_id,
                "render_profile_id": RENDER_PROFILE_ID,
                "prompt_sha256": PROMPT_SHA256,
                "source_origin": "self_synthetic",
                "rights_class": "SELF_OWNED_SYNTHETIC_OUTPUT",
                "restricted_source": False,
                "subject_visible_metadata": [
                    "image_bytes",
                    "production_prompt_exact_bytes",
                ],
                "stability_subset": (
                    family in selected_stability_families and ordinal == 1
                ),
            }
            gold_case = {
                "case_id": case_id,
                "expected_text": content["expected_text"],
                "accepted_normalizations": [
                    "Unicode NFC",
                    "CRLF/CR to LF",
                    "Unicode whitespace collapse for literal comparison",
                    "common Latin presentation ligatures to lexical letters",
                ],
                "forbidden_normalizations": content["forbidden_normalizations"],
                "critical_tokens": content["critical_tokens"],
                "ordered_segments": content["ordered_segments"],
                "role_boundaries": content["role_boundaries"],
                "required_source_tokens": content["required_source_tokens"],
                "uncertainty_policy": content["uncertainty_policy"],
                "clean_control": content["clean_control"],
                "gold_identity_sha256": "",
            }
            gold_identity_source = dict(gold_case)
            gold_identity_source.pop("gold_identity_sha256")
            gold_case["gold_identity_sha256"] = sha256_json(gold_identity_source)
            cases.append(case)
            gold_cases.append(gold_case)

    form = {
        "schema_version": FORM_SCHEMA_VERSION,
        "reference_pack_id": REFERENCE_PACK_ID,
        "form_id": f"{REFERENCE_PACK_ID}-FORM-{form_name}",
        "form_name": form_name,
        "role_id": "ocr_page",
        "case_count": len(cases),
        "family_quota": {family: 6 for family in FAMILIES},
        "difficulty_quota_per_family": {"easy": 2, "medium": 2, "hard": 2},
        "prompt_ref": "prompts/ocr_prompt_v3.md",
        "prompt_sha256": PROMPT_SHA256,
        "provider_output_contract": "PLAIN_MARKDOWN_TEXT",
        "diagnostic_track_in_primary_score": False,
        "content_directed_repair_allowed": False,
        "cases": cases,
    }
    gold = {
        "schema_version": GOLD_SCHEMA_VERSION,
        "reference_pack_id": REFERENCE_PACK_ID,
        "form_id": form["form_id"],
        "form_name": form_name,
        "case_count": len(gold_cases),
        "provider_visible": False,
        "gold_sent_to_provider": False,
        "cases": gold_cases,
    }
    form["form_sha256"] = sha256_json(form)
    gold["gold_sha256"] = sha256_json(gold)
    return form, gold


def _schemas() -> dict[str, dict[str, Any]]:
    form_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "pr-os://m14/ocr-page/form/v1",
        "type": "object",
        "required": [
            "schema_version",
            "form_id",
            "form_name",
            "role_id",
            "case_count",
            "cases",
        ],
        "properties": {
            "schema_version": {"const": FORM_SCHEMA_VERSION},
            "form_name": {"enum": ["A", "B"]},
            "role_id": {"const": "ocr_page"},
            "case_count": {"const": 48},
            "cases": {
                "type": "array",
                "minItems": 48,
                "maxItems": 48,
                "items": {
                    "type": "object",
                    "required": [
                        "case_id",
                        "family",
                        "difficulty",
                        "image_ref",
                        "image_sha256",
                        "source_id",
                        "page_id",
                        "region_id",
                    ],
                },
            },
        },
    }
    gold_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "pr-os://m14/ocr-page/gold/v1",
        "type": "object",
        "required": [
            "schema_version",
            "form_id",
            "form_name",
            "case_count",
            "provider_visible",
            "cases",
        ],
        "properties": {
            "schema_version": {"const": GOLD_SCHEMA_VERSION},
            "provider_visible": {"const": False},
            "case_count": {"const": 48},
            "cases": {
                "type": "array",
                "minItems": 48,
                "maxItems": 48,
                "items": {
                    "type": "object",
                    "required": [
                        "case_id",
                        "expected_text",
                        "critical_tokens",
                        "ordered_segments",
                        "uncertainty_policy",
                    ],
                },
            },
        },
    }
    result_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "pr-os://m14/ocr-page/local-output-bundle/v1",
        "type": "object",
        "required": ["schema_version", "form_id", "outputs"],
        "properties": {
            "schema_version": {"const": "m14-ocr-page-local-output-bundle-v1"},
            "outputs": {
                "type": "array",
                "minItems": 48,
                "maxItems": 48,
                "items": {
                    "type": "object",
                    "required": [
                        "case_id",
                        "image_sha256",
                        "output_text",
                        "output_sha256",
                    ],
                },
            },
        },
    }
    return {
        "ocr_page_form.schema.json": form_schema,
        "ocr_page_gold.schema.json": gold_schema,
        "ocr_page_result.schema.json": result_schema,
    }


def _normalization_profile() -> dict[str, Any]:
    return {
        "schema_version": "m14-ocr-page-normalization-profile-v3",
        "profile_id": NORMALIZATION_PROFILE_ID,
        "raw_transcription_rule": "source-as-printed; no scientific correction",
        "raw_output_preserved": True,
        "unicode_normalization": "NFC",
        "line_endings": "CRLF and CR become LF",
        "literal_comparison_steps": [
            "NFC",
            "map common Latin presentation ligatures to lexical letters",
            "remove only line-leading Markdown ATX heading control markers",
            (
                "render the bounded LaTeX math presentation subset to the "
                "same Unicode symbols without correcting values or signs"
            ),
            "collapse all Unicode whitespace runs to one ASCII space",
            "strip leading and trailing whitespace",
        ],
        "markdown_presentation_syntax": {
            "ignored_for_scoring": [
                "line-leading ATX heading marker: 1-6 hash characters plus whitespace"
            ],
            "source_punctuation_removed": False,
        },
        "latex_math_presentation": {
            "applied_only_when_latex_syntax_is_present": True,
            "supported_wrappers": [
                "inline and display math delimiters",
                "text",
                "mathrm",
                "mathit",
                "mathbf",
                "operatorname",
            ],
            "supported_symbols": [
                "Delta",
                "alpha",
                "times",
                "cdot",
                "circ",
                "numeric superscripts",
                "simple alphanumeric subscripts",
            ],
            "missing_sign_or_value_inference": False,
            "scientific_correction": False,
            "raw_output_mutated": False,
        },
        "layout_order_match_view": {
            "applied_to": "ordered-segment coverage and order only",
            "ignored_presentation_gap": (
                "ASCII whitespace after an identifier hyphen and before a digit"
            ),
            "applied_to_literal_or_critical_token_scoring": False,
        },
        "automatic_dehyphenation": False,
        "case_folding": False,
        "punctuation_folding": False,
        "scientific_correction": False,
    }


def _scoring_protocol() -> dict[str, Any]:
    return {
        "schema_version": "m14-ocr-page-scoring-protocol-v4",
        "protocol_id": SCORING_PROTOCOL_ID,
        "provider_blind": True,
        "content_directed_repair_allowed": False,
        "scoring_basis": (
            "provider-blind five-layer aggregation of frozen per-case scores"
        ),
        "layer_max_points": {
            "overall_case_quality": 45,
            "hard_case_quality": 20,
            "worst_family_quality": 12,
            "bottom_decile_quality": 8,
            "strict_output_contract": 15,
        },
        "curves": {
            "overall_case_quality": [
                [0, 0],
                [50, 0],
                [65, 9],
                [75, 18],
                [85, 30],
                [92, 39],
                [96, 43],
                [99, 44.5],
                [100, 45],
            ],
            "hard_case_quality": [
                [0, 0],
                [60, 0],
                [75, 5],
                [85, 10],
                [92, 15],
                [97, 18.5],
                [100, 20],
            ],
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
        },
        "strict_output_contract_points": {
            "valid_complete_form": 15,
            "invalid_or_incomplete": "NOT_ASSESSED",
        },
        "critical_hard_failure_score_cap": None,
        "score_bands": [
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
        ],
        "recommendation_thresholds": {
            "not_recommended_below": 60,
            "use_with_caution_below": 80,
            "recommended_at_or_above": 80,
            "quality_hard_gate_reported_separately": True,
        },
        "capacity_status": "NOT_ASSESSED_CAPACITY",
        "non_delivery_status": "NOT_ASSESSED_NONDELIVERY",
        "wrong_image_or_hash_status": "INVALIDATED_METHOD",
    }


def _render_profile() -> dict[str, Any]:
    return {
        "schema_version": "m14-ocr-page-render-profile-v1",
        "profile_id": RENDER_PROFILE_ID,
        "source_origin": "self_synthetic",
        "width_px": 1200,
        "height_px": 800,
        "nominal_dpi": 200,
        "color_mode": "L",
        "format": "PNG",
        "post_freeze_enhancement_allowed": False,
        "fonts": [
            {
                "role": role,
                "path_build_environment": path,
                "sha256": FONT_SHA256[role],
                "font_bytes_in_pack": False,
            }
            for role, path in FONT_PATHS.items()
        ],
        "font_rendering_note": (
            "Fixed PNG assets are authoritative; system font paths are build "
            "provenance only and are not required at score time."
        ),
    }


def _member_visibility(relative: str) -> tuple[bool, bool, str]:
    if relative.startswith("images/"):
        return True, True, "FORM_IMAGE"
    if relative.startswith("prompts/"):
        return True, True, "PRODUCTION_PROMPT"
    if relative.startswith("forms/"):
        return False, False, "LOCAL_FORM_CONTROL"
    if relative.startswith("gold/"):
        return False, False, "LOCAL_GOLD"
    if relative.startswith("scoring/"):
        return False, False, "LOCAL_SCORING"
    if relative.startswith("normalization/"):
        return False, False, "LOCAL_NORMALIZATION"
    if relative.startswith("render/"):
        return False, False, "LOCAL_RENDER_PROVENANCE"
    if relative.startswith("schemas/"):
        return False, False, "LOCAL_SCHEMA"
    raise ValueError(f"UNCLASSIFIED_PACK_MEMBER:{relative}")


def _collect_members(pack_root: Path) -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = []
    for path in sorted(item for item in pack_root.rglob("*") if item.is_file()):
        relative = path.relative_to(pack_root).as_posix()
        if relative in {"manifest.reference.json", "SHA256SUMS.txt"}:
            continue
        subject_visible, provider_visible, role = _member_visibility(relative)
        members.append(
            {
                "path": relative,
                "role": role,
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
                "subject_visible": subject_visible,
                "provider_visible": provider_visible,
                "rights_class": "SELF_OWNED_SYNTHETIC_OUTPUT",
                "provenance": (
                    "PR_OS_PRODUCTION_PROMPT_EXACT_BYTES"
                    if relative.startswith("prompts/")
                    else "DETERMINISTIC_SELF_SYNTHETIC_BUILD"
                ),
            }
        )
    return members


def build_reference_pack(
    destination: Path,
    *,
    production_prompt_path: Path,
) -> dict[str, Any]:
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"PACK_DESTINATION_EXISTS:{destination}")
    prompt_bytes = production_prompt_path.read_bytes()
    observed_prompt_sha = hashlib.sha256(prompt_bytes).hexdigest().upper()
    if observed_prompt_sha != PROMPT_SHA256:
        raise RuntimeError(
            f"PRODUCTION_PROMPT_HASH_MISMATCH:{observed_prompt_sha}:{PROMPT_SHA256}"
        )
    _validate_fonts()

    staging = destination.parent / f"{destination.name}.staging"
    if staging.exists():
        raise FileExistsError(f"PACK_STAGING_EXISTS:{staging}")
    staging.mkdir(parents=True)
    try:
        (staging / "prompts").mkdir(parents=True)
        (staging / "prompts" / "ocr_prompt_v3.md").write_bytes(prompt_bytes)

        form_a, gold_a = _build_form(form_name="A", pack_root=staging)
        form_b, gold_b = _build_form(form_name="B", pack_root=staging)
        _write_json(staging / "forms" / "form_A.json", form_a)
        _write_json(staging / "forms" / "form_B.json", form_b)
        _write_json(staging / "gold" / "form_A_gold.json", gold_a)
        _write_json(staging / "gold" / "form_B_gold.json", gold_b)
        for filename, schema in _schemas().items():
            _write_json(staging / "schemas" / filename, schema)
        _write_json(
            staging / "normalization" / "normalization_profile.json",
            _normalization_profile(),
        )
        _write_json(staging / "render" / "render_profile.json", _render_profile())
        _write_json(
            staging / "scoring" / "scoring_protocol.json",
            _scoring_protocol(),
        )

        source_overlap = (
            {case["source_id"] for case in form_a["cases"]}
            & {case["source_id"] for case in form_b["cases"]}
        )
        page_overlap = (
            {case["page_id"] for case in form_a["cases"]}
            & {case["page_id"] for case in form_b["cases"]}
        )
        region_overlap = (
            {case["region_id"] for case in form_a["cases"]}
            & {case["region_id"] for case in form_b["cases"]}
        )
        image_overlap = (
            {case["image_sha256"] for case in form_a["cases"]}
            & {case["image_sha256"] for case in form_b["cases"]}
        )
        text_overlap = (
            {sha256_text(case["expected_text"]) for case in gold_a["cases"]}
            & {sha256_text(case["expected_text"]) for case in gold_b["cases"]}
        )
        gold_overlap = (
            {case["gold_identity_sha256"] for case in gold_a["cases"]}
            & {case["gold_identity_sha256"] for case in gold_b["cases"]}
        )
        overlaps = {
            "source_id": sorted(source_overlap),
            "page_id": sorted(page_overlap),
            "region_id": sorted(region_overlap),
            "image_sha256": sorted(image_overlap),
            "expected_text_sha256": sorted(text_overlap),
            "gold_identity_sha256": sorted(gold_overlap),
        }
        if any(overlaps.values()):
            raise RuntimeError(f"FORM_AB_OVERLAP:{overlaps}")

        members = _collect_members(staging)
        manifest = {
            "schema_version": PACK_SCHEMA_VERSION,
            "reference_pack_id": REFERENCE_PACK_ID,
            "revision": REFERENCE_PACK_REVISION,
            "classification": "REFERENCE_EXAM_PACK",
            "reference_regression_only": True,
            "role_id": "ocr_page",
            "source_origin": "self_synthetic",
            "rights_class": "SELF_OWNED_SYNTHETIC_OUTPUT",
            "external_source_used": False,
            "restricted_source_used": False,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "declassification_authorization_id": DECLASSIFICATION_AUTHORIZATION_ID,
            "canonical_repo_relative_path": CANONICAL_REPO_RELATIVE_PATH,
            "formalization_source": {
                "reference_pack_id": PREDECESSOR_PACK_ID,
                "revision": PREDECESSOR_PACK_REVISION,
                "manifest_sha256": FORMALIZATION_SOURCE_MANIFEST_SHA256,
                "change_surface": "NO_CAP_NATURAL_SCORE_CURVE_CALIBRATION_SUCCESSOR",
            },
            "provider_requests_during_build": 0,
            "gold_sent_to_subject": False,
            "gold_sent_to_provider": False,
            "form_case_count": {"A": 48, "B": 48},
            "family_quota_per_form": {family: 6 for family in FAMILIES},
            "difficulty_quota_per_family": {"easy": 2, "medium": 2, "hard": 2},
            "production_prompt_sha256": PROMPT_SHA256,
            "normalization_profile_id": NORMALIZATION_PROFILE_ID,
            "render_profile_id": RENDER_PROFILE_ID,
            "scoring_protocol_id": SCORING_PROTOCOL_ID,
            "content_directed_repair_allowed": False,
            "diagnostic_track_in_primary_score": False,
            "form_ab_overlap": {key: len(value) for key, value in overlaps.items()},
            "members": members,
        }
        manifest["member_set_sha256"] = sha256_json(members)
        manifest["manifest_payload_sha256"] = sha256_json(manifest)
        _write_json(staging / "manifest.reference.json", manifest)

        checksum_lines = [
            f"{member['sha256']}  {member['path']}" for member in members
        ]
        checksum_lines.append(
            f"{_sha256_file(staging / 'manifest.reference.json')}  manifest.reference.json"
        )
        (staging / "SHA256SUMS.txt").write_text(
            "\n".join(checksum_lines) + "\n",
            encoding="ascii",
            newline="\n",
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    return {
        "reference_pack_id": REFERENCE_PACK_ID,
        "path": str(destination),
        "filesystem_file_count": sum(1 for path in destination.rglob("*") if path.is_file()),
        "member_set_sha256": manifest["member_set_sha256"],
        "manifest_sha256": _sha256_file(destination / "manifest.reference.json"),
        "checksums_sha256": _sha256_file(destination / "SHA256SUMS.txt"),
        "form_case_count": {"A": 48, "B": 48},
        "provider_requests": 0,
    }


def build_reference_pack_successor(
    destination: Path,
    *,
    predecessor_pack: Path,
    production_prompt_path: Path,
) -> dict[str, Any]:
    """Build v2 without rerendering the frozen v1 images or Gold case payloads."""

    destination = destination.resolve()
    predecessor_pack = predecessor_pack.resolve()
    if destination.exists():
        raise FileExistsError(f"PACK_DESTINATION_EXISTS:{destination}")
    predecessor_manifest_path = predecessor_pack / "manifest.reference.json"
    predecessor_manifest_sha = _sha256_file(predecessor_manifest_path)
    if predecessor_manifest_sha != FORMALIZATION_SOURCE_MANIFEST_SHA256:
        raise RuntimeError(
            "PREDECESSOR_MANIFEST_HASH_MISMATCH:"
            f"{predecessor_manifest_sha}:{FORMALIZATION_SOURCE_MANIFEST_SHA256}"
        )
    predecessor_manifest = json.loads(
        predecessor_manifest_path.read_text(encoding="utf-8")
    )
    if (
        predecessor_manifest.get("reference_pack_id") != PREDECESSOR_PACK_ID
        or predecessor_manifest.get("revision") != PREDECESSOR_PACK_REVISION
    ):
        raise RuntimeError("PREDECESSOR_ID_OR_REVISION_MISMATCH")

    prompt_bytes = production_prompt_path.read_bytes()
    observed_prompt_sha = hashlib.sha256(prompt_bytes).hexdigest().upper()
    if observed_prompt_sha != PROMPT_SHA256:
        raise RuntimeError(
            f"PRODUCTION_PROMPT_HASH_MISMATCH:{observed_prompt_sha}:{PROMPT_SHA256}"
        )

    staging = destination.parent / f"{destination.name}.staging"
    if staging.exists():
        raise FileExistsError(f"PACK_STAGING_EXISTS:{staging}")
    staging.mkdir(parents=True)
    inherited_paths: list[str] = []
    regenerated_prefixes = (
        "forms/",
        "gold/",
        "prompts/",
        "scoring/",
    )
    try:
        for source in sorted(
            path for path in predecessor_pack.rglob("*") if path.is_file()
        ):
            relative = source.relative_to(predecessor_pack).as_posix()
            if relative in {"manifest.reference.json", "SHA256SUMS.txt"}:
                continue
            if relative.startswith(regenerated_prefixes):
                continue
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if _sha256_file(target) != _sha256_file(source):
                raise RuntimeError(f"INHERITED_MEMBER_HASH_MISMATCH:{relative}")
            inherited_paths.append(relative)

        (staging / "prompts").mkdir(parents=True)
        (staging / "prompts" / "ocr_prompt_v3.md").write_bytes(prompt_bytes)

        forms: dict[str, dict[str, Any]] = {}
        golds: dict[str, dict[str, Any]] = {}
        for form_name in ("A", "B"):
            form = json.loads(
                (
                    predecessor_pack / "forms" / f"form_{form_name}.json"
                ).read_text(encoding="utf-8")
            )
            gold = json.loads(
                (
                    predecessor_pack / "gold" / f"form_{form_name}_gold.json"
                ).read_text(encoding="utf-8")
            )
            form.pop("form_sha256", None)
            form["reference_pack_id"] = REFERENCE_PACK_ID
            form["form_id"] = f"{REFERENCE_PACK_ID}-FORM-{form_name}"
            form["prompt_ref"] = "prompts/ocr_prompt_v3.md"
            form["prompt_sha256"] = PROMPT_SHA256
            for case in form["cases"]:
                case["prompt_sha256"] = PROMPT_SHA256
            form["form_sha256"] = sha256_json(form)

            gold.pop("gold_sha256", None)
            gold["reference_pack_id"] = REFERENCE_PACK_ID
            gold["form_id"] = form["form_id"]
            gold["gold_sha256"] = sha256_json(gold)
            forms[form_name] = form
            golds[form_name] = gold
            _write_json(staging / "forms" / f"form_{form_name}.json", form)
            _write_json(staging / "gold" / f"form_{form_name}_gold.json", gold)

        _write_json(
            staging / "scoring" / "scoring_protocol.json",
            _scoring_protocol(),
        )

        overlaps = {
            "source_id": sorted(
                {case["source_id"] for case in forms["A"]["cases"]}
                & {case["source_id"] for case in forms["B"]["cases"]}
            ),
            "page_id": sorted(
                {case["page_id"] for case in forms["A"]["cases"]}
                & {case["page_id"] for case in forms["B"]["cases"]}
            ),
            "region_id": sorted(
                {case["region_id"] for case in forms["A"]["cases"]}
                & {case["region_id"] for case in forms["B"]["cases"]}
            ),
            "image_sha256": sorted(
                {case["image_sha256"] for case in forms["A"]["cases"]}
                & {case["image_sha256"] for case in forms["B"]["cases"]}
            ),
            "expected_text_sha256": sorted(
                {
                    sha256_text(case["expected_text"])
                    for case in golds["A"]["cases"]
                }
                & {
                    sha256_text(case["expected_text"])
                    for case in golds["B"]["cases"]
                }
            ),
            "gold_identity_sha256": sorted(
                {
                    case["gold_identity_sha256"]
                    for case in golds["A"]["cases"]
                }
                & {
                    case["gold_identity_sha256"]
                    for case in golds["B"]["cases"]
                }
            ),
        }
        if any(overlaps.values()):
            raise RuntimeError(f"FORM_AB_OVERLAP:{overlaps}")

        members = _collect_members(staging)
        inherited_receipts = [
            {
                "path": relative,
                "bytes": (staging / relative).stat().st_size,
                "sha256": _sha256_file(staging / relative),
            }
            for relative in inherited_paths
        ]
        manifest = {
            "schema_version": PACK_SCHEMA_VERSION,
            "reference_pack_id": REFERENCE_PACK_ID,
            "revision": REFERENCE_PACK_REVISION,
            "classification": "REFERENCE_EXAM_PACK",
            "reference_regression_only": True,
            "role_id": "ocr_page",
            "source_origin": "self_synthetic",
            "rights_class": "SELF_OWNED_SYNTHETIC_OUTPUT",
            "external_source_used": False,
            "restricted_source_used": False,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "declassification_authorization_id": DECLASSIFICATION_AUTHORIZATION_ID,
            "canonical_repo_relative_path": CANONICAL_REPO_RELATIVE_PATH,
            "formalization_source": {
                "reference_pack_id": PREDECESSOR_PACK_ID,
                "revision": PREDECESSOR_PACK_REVISION,
                "manifest_sha256": FORMALIZATION_SOURCE_MANIFEST_SHA256,
                "change_surface": "NO_CAP_NATURAL_SCORE_CURVE_CALIBRATION_SUCCESSOR",
            },
            "predecessor_inheritance": {
                "unchanged_member_count": len(inherited_receipts),
                "unchanged_member_set_sha256": sha256_json(inherited_receipts),
                "changed_member_paths": [
                    "forms/form_A.json",
                    "forms/form_B.json",
                    "gold/form_A_gold.json",
                    "gold/form_B_gold.json",
                    "prompts/ocr_prompt_v3.md",
                    "scoring/scoring_protocol.json",
                ],
            },
            "provider_requests_during_build": 0,
            "gold_sent_to_subject": False,
            "gold_sent_to_provider": False,
            "form_case_count": {"A": 48, "B": 48},
            "family_quota_per_form": {family: 6 for family in FAMILIES},
            "difficulty_quota_per_family": {
                "easy": 2,
                "medium": 2,
                "hard": 2,
            },
            "production_prompt_sha256": PROMPT_SHA256,
            "normalization_profile_id": NORMALIZATION_PROFILE_ID,
            "render_profile_id": RENDER_PROFILE_ID,
            "scoring_protocol_id": SCORING_PROTOCOL_ID,
            "content_directed_repair_allowed": False,
            "diagnostic_track_in_primary_score": False,
            "form_ab_overlap": {
                key: len(value) for key, value in overlaps.items()
            },
            "members": members,
        }
        manifest["member_set_sha256"] = sha256_json(members)
        manifest["manifest_payload_sha256"] = sha256_json(manifest)
        _write_json(staging / "manifest.reference.json", manifest)

        checksum_lines = [
            f"{member['sha256']}  {member['path']}" for member in members
        ]
        checksum_lines.append(
            f"{_sha256_file(staging / 'manifest.reference.json')}  "
            "manifest.reference.json"
        )
        (staging / "SHA256SUMS.txt").write_text(
            "\n".join(checksum_lines) + "\n",
            encoding="ascii",
            newline="\n",
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    return {
        "reference_pack_id": REFERENCE_PACK_ID,
        "revision": REFERENCE_PACK_REVISION,
        "path": str(destination),
        "filesystem_file_count": sum(
            1 for path in destination.rglob("*") if path.is_file()
        ),
        "member_set_sha256": manifest["member_set_sha256"],
        "manifest_sha256": _sha256_file(
            destination / "manifest.reference.json"
        ),
        "checksums_sha256": _sha256_file(destination / "SHA256SUMS.txt"),
        "form_case_count": {"A": 48, "B": 48},
        "unchanged_member_count": len(inherited_receipts),
        "provider_requests": 0,
    }
