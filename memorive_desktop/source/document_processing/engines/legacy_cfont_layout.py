"""Layout-preserving recovery for one audited legacy C<number> PDF family.

Hard decisions use only embedded font-program hashes, the PDF Encoding map,
raw character codes, texttrace glyph ids, origins, and Layout geometry.  Text
context is counted for audit hints only and never admits, rejects, or maps a
character.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .legacy_cfont import (
    LegacyCFontRecoveryError,
    _AUDITED_FONT_NAMES,
    _build_font_maps,
    _is_forbidden_control,
    _render_line,
    _trace_by_origin,
    _validate_trace_relations,
)
from .legacy_ymath_layout import (
    _assemble_markdown,
    _choose_target,
    _patch_table_cell,
    _patch_text_line,
    _render_text_target,
    _serialize_markdown,
    _structure_signature,
    _targets_for_page,
)


_AUDITED_PYMUPDF4LLM_VERSION = "1.28.2"
_PICTURE_START = "<!-- Start of picture text -->"
_PICTURE_END = "<!-- End of picture text -->"
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}[a-z]?\b", re.IGNORECASE)
_NUMERIC_CITATION_RE = re.compile(r"\[[0-9,; -]+\]")
_NARRATIVE_CITATION_RE = re.compile(
    r"(?:\b[^\W\d_][\w'’.-]*\s+et\s+al\."
    r"|\b[^\W\d_][\w'’.-]*\s*\(\s*(?:19|20)\d{2}[a-z]?\s*\))",
    re.IGNORECASE,
)
_AUTHOR_CONTEXT_RE = re.compile(
    r"\b(?:corresponding\s+author|co-?authors?|authors?\s*:|"
    r"affiliations?|department|university|institute|e-?mail)\b",
    re.IGNORECASE,
)
_MATH_CONTEXT_RE = re.compile(
    r"(?:[=<>≤≥≈≠±×÷∑∏√∫^_]|[₀-₉⁰-⁹])"
)


class LegacyCFontLayoutRecoveryError(ValueError):
    """The input resembles the audited C-font family but is ambiguous."""


@dataclass(frozen=True)
class LegacyCFontLayoutRecovery:
    markdown: str
    pages_total: int
    mapped_chars: int
    synthetic_chars: int
    passthrough_chars: int
    font_count: int
    modified_lines: int
    modified_cells: int
    cleared_auxiliary_lines: int
    modified_picture_boxes: int
    modified_picture_chars: int
    omitted_formula_chars: int
    assignment_counts: dict[str, int]
    context_hints: dict[str, int]
    content_sha256: str


def _context_hint(target: dict[str, Any], recovered_text: str) -> str:
    """Return a fallible source hint; never use it for a hard decision."""
    target_class = str(target.get("class") or "")
    if target_class == "formula":
        return "scientific_symbol_or_variable"
    if target_class in {"table", "caption"}:
        return "table_or_figure"
    if target_class == "page-header":
        return "running_header"
    if _AUTHOR_CONTEXT_RE.search(recovered_text):
        return "author_or_byline"
    if target_class == "list-item":
        return "bibliography_or_reference"
    if _NARRATIVE_CITATION_RE.search(recovered_text):
        return "narrative_citation"
    if (
        _YEAR_RE.search(recovered_text)
        and _NUMERIC_CITATION_RE.search(recovered_text)
    ):
        return "bibliography_or_reference"
    has_non_ascii_letter = any(
        ord(char) > 127
        and unicodedata.category(char).startswith("L")
        for char in recovered_text
    )
    if _MATH_CONTEXT_RE.search(recovered_text) and (
        has_non_ascii_letter
        or any(unicodedata.category(char) == "Sm" for char in recovered_text)
    ):
        return "scientific_symbol_or_variable"
    if has_non_ascii_letter:
        return "multilingual_name_term_or_prose_uncertain"
    return "body_text"


def _is_auxiliary_layout_line(
    target: dict[str, Any],
    audited_fonts: frozenset[str],
) -> bool:
    """Identify a detached replacement-only shadow from audited fonts."""
    line = target.get("line")
    if not line:
        return False
    saw_content = False
    for span in line.get("spans", []):
        font = str(span.get("font") or "")
        for char in str(span.get("text") or ""):
            if char.isspace():
                continue
            saw_content = True
            if font not in audited_fonts or char != "\ufffd":
                return False
    return saw_content


def _protect_markdown_literal(
    target: dict[str, Any],
    recovered_text: str,
) -> str:
    """Keep a literal footnote star from becoming a Markdown list."""
    if (
        str(target.get("class") or "") != "list-item"
        and recovered_text.startswith("* ")
    ):
        return "\\" + recovered_text
    return recovered_text


def _validate_picture_payload(
    payload: str,
    *,
    expected_replacements: int,
) -> None:
    if payload.count(_PICTURE_START) != 1 or payload.count(_PICTURE_END) != 1:
        raise LegacyCFontLayoutRecoveryError(
            "picture payload markers do not match the audited Layout format"
        )
    body = payload.split(_PICTURE_START, 1)[1].split(_PICTURE_END, 1)[0]
    body = body.replace("<br>", "")
    visible = "".join(char for char in body if not char.isspace())
    if visible != "\ufffd" * expected_replacements:
        raise LegacyCFontLayoutRecoveryError(
            "picture payload replacement count/content mismatch: "
            f"expected={expected_replacements}, actual={payload.count(chr(0xFFFD))}"
        )


def _box_payload(page: dict, box_index: int) -> str:
    boxes = page.get("page_boxes") or []
    if box_index >= len(boxes):
        raise LegacyCFontLayoutRecoveryError(
            f"baseline page has no box index {box_index}"
        )
    box = boxes[box_index]
    start, stop = box.get("pos") or (None, None)
    if not isinstance(start, int) or not isinstance(stop, int):
        raise LegacyCFontLayoutRecoveryError(
            f"baseline box {box_index} has invalid text positions"
        )
    return str(page.get("text") or "")[start:stop]


def _validate_page_boxes(parsed: Any, baseline_pages: list[dict]) -> None:
    import pymupdf

    if len(parsed.pages) != len(baseline_pages):
        raise LegacyCFontLayoutRecoveryError(
            "Layout parser page count differs from production page chunks"
        )
    for page_number, (layout_page, baseline_page) in enumerate(
        zip(parsed.pages, baseline_pages),
        start=1,
    ):
        baseline_boxes = baseline_page.get("page_boxes") or []
        if len(layout_page.boxes) != len(baseline_boxes):
            raise LegacyCFontLayoutRecoveryError(
                f"page {page_number}: Layout box count differs from baseline"
            )
        for box_index, (box, baseline_box) in enumerate(
            zip(layout_page.boxes, baseline_boxes)
        ):
            expected_bbox = tuple(
                pymupdf.IRect(box.x0, box.y0, box.x1, box.y1)
            )
            if (
                int(baseline_box.get("index", -1)) != box_index
                or str(baseline_box.get("class") or "") != box.boxclass
                or tuple(baseline_box.get("bbox") or ()) != expected_bbox
            ):
                raise LegacyCFontLayoutRecoveryError(
                    f"page {page_number} box {box_index}: "
                    "Layout identity differs from production page chunk"
                )


def _picture_textlines(records: list[dict]) -> list[dict]:
    selected = [
        record
        for record in records
        if record["result"].text.strip()
    ]
    selected.sort(
        key=lambda record: (
            float(record["rect"][1]),
            float(record["rect"][0]),
            int(record["order"]),
        )
    )
    return [
        {
            "bbox": record["rect"],
            "spans": [{"text": record["result"].text}],
        }
        for record in selected
    ]


def _recover_layout(
    path: Path,
    *,
    baseline_markdown: str,
) -> LegacyCFontLayoutRecovery | None:
    import pymupdf
    import pymupdf4llm
    from pymupdf4llm.helpers.document_layout import parse_document

    if str(getattr(pymupdf4llm, "__version__", "")) != (
        _AUDITED_PYMUPDF4LLM_VERSION
    ):
        raise LegacyCFontLayoutRecoveryError(
            "unaudited pymupdf4llm version: "
            f"{getattr(pymupdf4llm, '__version__', None)!r}"
        )

    document = pymupdf.open(path)
    try:
        font_maps = _build_font_maps(document)
    finally:
        document.close()
    if font_maps is None:
        return None

    try:
        import pymupdf.layout
        pymupdf.layout.activate()
    except (ImportError, OSError) as exc:
        raise LegacyCFontLayoutRecoveryError("LAYOUT_RUNTIME_UNAVAILABLE") from exc
    parsed = parse_document(path, use_ocr=False, force_text=True)
    from copy import deepcopy
    baseline_pages = deepcopy(parsed).to_markdown(page_chunks=True)
    if _assemble_markdown(baseline_pages) != baseline_markdown:
        raise LegacyCFontLayoutRecoveryError(
            "production page-chunk baseline changed during recovery"
        )
    _validate_page_boxes(parsed, baseline_pages)
    baseline_structure = _structure_signature(baseline_markdown)

    document = pymupdf.open(path)
    metrics: Counter[str] = Counter()
    assignment_counts: Counter[str] = Counter()
    context_hints: Counter[str] = Counter()
    affected_groups: dict[tuple, list[dict]] = defaultdict(list)
    page_records: list[list[dict]] = []
    target_maps: list[dict[tuple, dict]] = []
    trace_relations: set[tuple[str, int, int, int]] = set()
    shadow_keys: set[tuple] = set()
    primary_keys: set[tuple] = set()
    problems: list[dict[str, Any]] = []
    try:
        if len(parsed.pages) != len(document):
            raise LegacyCFontLayoutRecoveryError(
                "Layout page count differs from PDF page count"
            )
        for page_number, (layout_page, page) in enumerate(
            zip(parsed.pages, document),
            start=1,
        ):
            table_targets, line_targets, box_targets = _targets_for_page(
                layout_page
            )
            target_maps.append(
                {
                    target["key"]: target
                    for target in table_targets + line_targets + box_targets
                }
            )
            trace = _trace_by_origin(page)
            raw = page.get_text(
                "rawdict",
                flags=int(pymupdf.TEXTFLAGS_TEXT),
            )
            records: list[dict] = []
            order = 0
            for block in raw.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for raw_line in block.get("lines", []):
                    result = _render_line(
                        raw_line,
                        trace,
                        font_maps,
                        trace_relations,
                        page_number=page_number,
                    )
                    metrics["mapped_chars"] += result.mapped_chars
                    metrics["synthetic_chars"] += result.synthetic_chars
                    metrics["passthrough_chars"] += result.passthrough_chars
                    rect = (
                        pymupdf.Rect(raw_line["bbox"])
                        * page.rotation_matrix
                    )
                    center = pymupdf.Point(
                        (rect.x0 + rect.x1) / 2,
                        (rect.y0 + rect.y1) / 2,
                    )
                    target, candidates = _choose_target(
                        rect,
                        center,
                        table_targets,
                        line_targets,
                        box_targets,
                    )
                    record = {
                        "order": order,
                        "rect": rect,
                        "center": center,
                        "result": result,
                        "target_key": (
                            target["key"] if target is not None else None
                        ),
                    }
                    order += 1
                    records.append(record)
                    if not result.mapped_chars:
                        continue
                    if target is None:
                        problems.append(
                            {
                                "page": page_number,
                                "bbox": tuple(round(value, 3) for value in rect),
                                "candidate_count": len(candidates),
                            }
                        )
                        continue
                    key = (page_number,) + target["key"]
                    affected_groups[key].append(record)
                    primary_keys.add(key)
                    assignment_counts[target["class"]] += 1
                    context_hints[_context_hint(target, result.text)] += 1
                    for shadow in line_targets:
                        if shadow["key"] == target["key"]:
                            continue
                        shadow_rect = shadow["rect"]
                        shadow_center = pymupdf.Point(
                            (shadow_rect.x0 + shadow_rect.x1) / 2,
                            (shadow_rect.y0 + shadow_rect.y1) / 2,
                        )
                        if (
                            rect.contains(shadow_center)
                            and _is_auxiliary_layout_line(
                                shadow,
                                _AUDITED_FONT_NAMES,
                            )
                        ):
                            shadow_keys.add(
                                (page_number,) + shadow["key"]
                            )
            page_records.append(records)
    finally:
        document.close()

    if problems:
        raise LegacyCFontLayoutRecoveryError(
            f"{len(problems)} C-font lines have ambiguous Layout targets"
        )
    _validate_trace_relations(trace_relations)

    modified_lines = 0
    modified_cells = 0
    modified_picture_boxes = 0
    modified_picture_chars = 0
    omitted_formula_chars = 0
    for key in sorted(affected_groups):
        page_number = key[0]
        target_key = key[1:]
        target = target_maps[page_number - 1][target_key]
        if target_key[0] == "table":
            _patch_table_cell(
                target,
                page_records[page_number - 1],
            )
            modified_cells += 1
            continue
        if target_key[0] == "line":
            corrected = _render_text_target(
                page_records[page_number - 1],
                target_key,
            )
            corrected = _protect_markdown_literal(target, corrected)
            _patch_text_line(target["line"], corrected)
            modified_lines += 1
            continue

        box = target["box"]
        box_index = int(target_key[1])
        target_records = [
            record
            for record in page_records[page_number - 1]
            if record.get("target_key") == target_key
        ]
        mapped = sum(
            record["result"].mapped_chars
            for record in target_records
        )
        payload = _box_payload(
            baseline_pages[page_number - 1],
            box_index,
        )
        if box.boxclass == "picture":
            _validate_picture_payload(
                payload,
                expected_replacements=mapped,
            )
            box.textlines = _picture_textlines(target_records)
            modified_picture_boxes += 1
            modified_picture_chars += mapped
            continue
        if box.boxclass == "formula":
            if payload.strip():
                raise LegacyCFontLayoutRecoveryError(
                    f"page {page_number} formula box {box_index} "
                    "was not omitted by the production baseline"
                )
            omitted_formula_chars += mapped
            continue
        raise LegacyCFontLayoutRecoveryError(
            f"page {page_number} box {box_index}: "
            f"unsupported mapped box class {box.boxclass!r}"
        )

    cleared_auxiliary_lines = 0
    for key in sorted(shadow_keys - primary_keys):
        page_number = key[0]
        target = target_maps[page_number - 1][key[1:]]
        _patch_text_line(target["line"], "")
        cleared_auxiliary_lines += 1

    markdown = _serialize_markdown(parsed)
    if _structure_signature(markdown) != baseline_structure:
        raise LegacyCFontLayoutRecoveryError(
            "Layout Markdown structure changed during C-font recovery"
        )
    if markdown.count(_PICTURE_START) != baseline_markdown.count(
        _PICTURE_START
    ):
        raise LegacyCFontLayoutRecoveryError(
            "picture-text container count changed during C-font recovery"
        )
    if "\ufffd" in markdown:
        raise LegacyCFontLayoutRecoveryError(
            "Layout Markdown still contains U+FFFD after C-font recovery"
        )
    forbidden_controls = sum(
        _is_forbidden_control(char)
        for char in markdown
    )
    if forbidden_controls:
        raise LegacyCFontLayoutRecoveryError(
            "Layout Markdown contains forbidden control characters: "
            f"{forbidden_controls}"
        )

    return LegacyCFontLayoutRecovery(
        markdown=markdown,
        pages_total=len(parsed.pages),
        mapped_chars=metrics["mapped_chars"],
        synthetic_chars=metrics["synthetic_chars"],
        passthrough_chars=metrics["passthrough_chars"],
        font_count=len(font_maps),
        modified_lines=modified_lines,
        modified_cells=modified_cells,
        cleared_auxiliary_lines=cleared_auxiliary_lines,
        modified_picture_boxes=modified_picture_boxes,
        modified_picture_chars=modified_picture_chars,
        omitted_formula_chars=omitted_formula_chars,
        assignment_counts=dict(assignment_counts),
        context_hints=dict(context_hints),
        content_sha256=hashlib.sha256(
            markdown.encode("utf-8")
        ).hexdigest(),
    )


def recover_legacy_cfont_layout_pdf(
    path: Path,
    *,
    baseline_markdown: str,
) -> LegacyCFontLayoutRecovery | None:
    """Recover the exact audited C-font family without changing the PDF."""
    try:
        return _recover_layout(
            path,
            baseline_markdown=baseline_markdown,
        )
    except LegacyCFontRecoveryError as exc:
        raise LegacyCFontLayoutRecoveryError(str(exc)) from exc
