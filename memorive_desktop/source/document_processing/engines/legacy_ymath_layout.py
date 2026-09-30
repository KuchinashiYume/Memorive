"""将已审计 legacy glyph 按坐标桥接回 PyMuPDF-Layout Markdown。

字符是否合法不由作者名、正文位置或字符白名单判断。硬决策只使用字体程序、
glyph id、页面坐标与 Layout 归属；语境分类仅进入审计元数据。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import unicodedata
from pathlib import Path
from typing import Any

from .legacy_ymath import (
    LegacyYMathRecoveryError,
    _GLYPH_RULES,
    _SUPERSCRIPT_FONT,
    _ZMATH_FONT,
    _render_line,
    _trace_by_origin,
    recover_legacy_ymath_pdf,
)


_AUDITED_PYMUPDF4LLM_VERSION = "1.28.2"


@dataclass(frozen=True)
class LegacyYMathLayoutRecovery:
    markdown: str
    pages_total: int
    mapped_chars: int
    superscript_runs: int
    superscript_chars: int
    suppressed_accent_spaces: int
    font_count: int
    modified_lines: int
    modified_cells: int
    cleared_auxiliary_lines: int
    omitted_headers: int
    assignment_counts: dict[str, int]
    context_hints: dict[str, int]
    content_sha256: str


def _is_auxiliary_layout_line(target: dict[str, Any]) -> bool:
    """仅识别完全由已审计特殊 glyph 组成的 Layout 游离行。"""
    line = target.get("line")
    if not line:
        return False
    saw_content = False
    for span in line.get("spans", []):
        font = str(span.get("font") or "")
        rules = _GLYPH_RULES.get(font, {})
        for char in str(span.get("text") or ""):
            if char.isspace():
                continue
            saw_content = True
            if (
                font in {_SUPERSCRIPT_FONT, _ZMATH_FONT}
                or char in rules
            ):
                continue
            return False
    return saw_content


def _context_hint(target: dict[str, Any], recovered_text: str) -> str:
    """仅供审计参考；返回值不参与映射、放行或拒绝。"""
    target_class = str(target.get("class") or "")
    if target_class == "page-header":
        return "running_header"
    if target_class == "table":
        return "table_cell"
    if target_class == "section-header":
        return "section_heading"
    if "^" in recovered_text or any(
        token in recovered_text for token in ("°", "−", "=", "′")
    ):
        return "scientific_notation_or_formula"
    if target_class == "list-item":
        return "reference_or_list_item"
    if any(
        unicodedata.category(char).startswith("L") and ord(char) > 127
        for char in recovered_text
    ):
        return "multilingual_name_term_or_citation"
    return "body_text"


def _targets_for_page(layout_page: Any) -> tuple[list[dict], list[dict], list[dict]]:
    import pymupdf

    table_targets: list[dict] = []
    line_targets: list[dict] = []
    box_targets: list[dict] = []
    for box_index, box in enumerate(layout_page.boxes):
        box_targets.append(
            {
                "key": ("box", box_index),
                "class": box.boxclass,
                "rect": pymupdf.Rect(box.x0, box.y0, box.x1, box.y1),
                "box": box,
            }
        )
        if box.boxclass == "table":
            table = box.table
            if not isinstance(table, dict):
                raise LegacyYMathRecoveryError(
                    "PyMuPDF-Layout table payload is not a dictionary"
                )
            for row_index, row in enumerate(table.get("cells", [])):
                for col_index, cell in enumerate(row):
                    table_targets.append(
                        {
                            "key": (
                                "table",
                                box_index,
                                row_index,
                                col_index,
                            ),
                            "class": "table",
                            "rect": pymupdf.Rect(cell),
                            "box": box,
                            "row": row_index,
                            "col": col_index,
                        }
                    )
        elif box.textlines:
            for line_index, line in enumerate(box.textlines):
                line_targets.append(
                    {
                        "key": ("line", box_index, line_index),
                        "class": box.boxclass,
                        "rect": pymupdf.Rect(line["bbox"]),
                        "box": box,
                        "line": line,
                    }
                )
    return table_targets, line_targets, box_targets


def _choose_target(
    rect: Any,
    center: Any,
    table_targets: list[dict],
    line_targets: list[dict],
    box_targets: list[dict],
) -> tuple[dict | None, list[dict]]:
    table_candidates = [
        target for target in table_targets if target["rect"].contains(center)
    ]
    if len(table_candidates) == 1:
        return table_candidates[0], []
    if len(table_candidates) > 1:
        return None, table_candidates

    line_overlaps = [
        target
        for target in line_targets
        if (rect & target["rect"]).get_area() > 0
    ]
    containing = [
        target for target in line_targets if target["rect"].contains(center)
    ]
    substantive = [
        target
        for target in containing
        if not _is_auxiliary_layout_line(target)
    ]
    pool = substantive or containing
    if pool:
        target = max(
            pool,
            key=lambda item: (rect & item["rect"]).get_area(),
        )
        shadows = [
            item
            for item in line_overlaps
            if item["key"] != target["key"]
            and _is_auxiliary_layout_line(item)
        ]
        return target, shadows

    scored = sorted(
        (
            ((rect & target["rect"]).get_area(), target)
            for target in table_targets + line_targets
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    if (
        scored
        and scored[0][0] > 0
        and (
            len(scored) == 1
            or scored[0][0] > scored[1][0] * 1.05
        )
    ):
        return scored[0][1], []

    box_candidates = [
        target for target in box_targets if target["rect"].contains(center)
    ]
    if len(box_candidates) == 1:
        return box_candidates[0], []
    return None, box_candidates


def _patch_table_cell(target: dict, records: list[dict]) -> None:
    cell_records = [
        record
        for record in records
        if target["rect"].contains(record["center"])
    ]
    corrected = "<br>".join(
        record["result"].text.strip()
        for record in cell_records
        if record["result"].text.strip()
    )
    if "|" in corrected:
        raise LegacyYMathRecoveryError(
            "recovered table cell contains an unescaped pipe"
        )
    box = target["box"]
    markdown = str(box.table.get("markdown") or "")
    ending = markdown[len(markdown.rstrip("\n")):]
    lines = markdown.rstrip("\n").splitlines()
    line_index = 0 if target["row"] == 0 else target["row"] + 1
    if line_index >= len(lines):
        raise LegacyYMathRecoveryError(
            "table Markdown row does not match cell geometry"
        )
    cells = lines[line_index].split("|")[1:-1]
    if len(cells) != int(box.table.get("col_count") or 0):
        raise LegacyYMathRecoveryError(
            "table Markdown columns do not match cell geometry"
        )
    cells[target["col"]] = corrected
    lines[line_index] = "|" + "|".join(cells) + "|"
    box.table["markdown"] = "\n".join(lines) + ending


def _patch_text_line(line: dict, corrected: str) -> None:
    if not line.get("spans"):
        raise LegacyYMathRecoveryError("target Layout line has no spans")
    span = deepcopy(line["spans"][0])
    # Preserve the original list decision when decoded numerals become visible.
    # The serializer would otherwise reinterpret a page number as an ordered list.
    line.setdefault("_memorive_original_first_text", str(span.get("text") or ""))
    span["text"] = corrected
    line["spans"] = [span]


def _render_text_target(records: list[dict], target_key: tuple) -> str:
    target_records = [
        record
        for record in records
        if record.get("target_key") == target_key
        and record["result"].text.strip()
    ]
    if not target_records:
        raise LegacyYMathRecoveryError(
            "Layout text target has no source records"
        )
    target_records.sort(
        key=lambda record: (
            float(record["rect"][0]),
            int(record["order"]),
        )
    )
    return " ".join(
        record["result"].text.strip() for record in target_records
    )


def _assemble_markdown(chunks: list[dict]) -> str:
    parts: list[str] = []
    for page_number, chunk in enumerate(chunks, start=1):
        parts.extend(
            [
                f"<!-- memorive:page:{page_number} -->",
                (chunk.get("text") or "").rstrip(),
            ]
        )
    return "\n\n".join(parts).strip() + "\n"


def _serialize_markdown(parsed: Any) -> str:
    """Serialize a copy because pymupdf4llm mutates list-item spans."""
    import re
    cloned = deepcopy(parsed)
    numbered = re.compile(r"^([0-9]+)\.(?=\s|$)")
    for page in cloned.pages:
        for box in page.boxes:
            if box.boxclass != "list-item":
                continue
            for line in box.textlines or []:
                old = line.get("_memorive_original_first_text")
                if old is None or numbered.match(old.strip()) or not line.get("spans"):
                    continue
                span = line["spans"][0]
                value = str(span.get("text") or "")
                if numbered.match(value.strip()):
                    # Markdown escape preserves the visible source punctuation.
                    span["text"] = value.replace(".", "\\.", 1)
    return _assemble_markdown(cloned.to_markdown(page_chunks=True))


def _structure_signature(markdown: str) -> dict[str, Any]:
    heading_levels: list[int] = []
    table_columns: list[int] = []
    list_markers: list[str] = []
    for line in markdown.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            heading_levels.append(len(stripped) - len(stripped.lstrip("#")))
        if line.count("|") >= 2:
            table_columns.append(line.count("|"))
        if stripped.startswith(("- ", "* ", "+ ")):
            list_markers.append(stripped[:1])
    return {
        "pages": markdown.count("<!-- memorive:page:"),
        "heading_levels": tuple(heading_levels),
        "table_columns": tuple(table_columns),
        "list_markers": tuple(list_markers),
    }


def _recover_layout(
    path: Path,
    *,
    baseline_markdown: str,
) -> tuple[str, dict[str, Any]]:
    import pymupdf
    import pymupdf4llm
    from pymupdf4llm.helpers.document_layout import parse_document

    if str(getattr(pymupdf4llm, "__version__", "")) != (
        _AUDITED_PYMUPDF4LLM_VERSION
    ):
        raise LegacyYMathRecoveryError(
            "unaudited pymupdf4llm version: "
            f"{getattr(pymupdf4llm, '__version__', None)!r}"
        )
    try:
        import pymupdf.layout
        pymupdf.layout.activate()
    except (ImportError, OSError) as exc:
        raise LegacyYMathRecoveryError("LAYOUT_RUNTIME_UNAVAILABLE") from exc
    parsed = parse_document(path, use_ocr=False, force_text=True)
    unmodified = _serialize_markdown(parsed)
    if unmodified != baseline_markdown:
        raise LegacyYMathRecoveryError(
            "Layout parser baseline differs from the engine Markdown"
        )
    baseline_structure = _structure_signature(unmodified)

    document = pymupdf.open(path)
    metrics: Counter = Counter()
    assignment_counts: Counter = Counter()
    context_hints: Counter = Counter()
    affected_groups: dict[tuple, list[dict]] = defaultdict(list)
    page_records: list[list[dict]] = []
    target_maps: list[dict[tuple, dict]] = []
    shadow_keys: set[tuple] = set()
    primary_keys: set[tuple] = set()
    problems: list[dict] = []
    metric_names = (
        "mapped_chars",
        "superscript_runs",
        "superscript_chars",
        "suppressed_accent_spaces",
    )

    try:
        if len(parsed.pages) != len(document):
            raise LegacyYMathRecoveryError(
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
                        page_number=page_number,
                    )
                    rect = (
                        pymupdf.Rect(raw_line["bbox"])
                        * page.rotation_matrix
                    )
                    center = pymupdf.Point(
                        (rect.x0 + rect.x1) / 2,
                        (rect.y0 + rect.y1) / 2,
                    )
                    record = {
                        "order": order,
                        "rect": rect,
                        "center": center,
                        "result": result,
                        "raw_line": raw_line,
                    }
                    target, shadows = _choose_target(
                        rect,
                        center,
                        table_targets,
                        line_targets,
                        box_targets,
                    )
                    record["target_key"] = (
                        target["key"] if target is not None else None
                    )
                    order += 1
                    records.append(record)
                    if not (
                        result.mapped_chars
                        or result.superscript_runs
                        or result.suppressed_accent_spaces
                    ):
                        continue
                    for name in metric_names:
                        metrics[name] += getattr(result, name)
                    if target is None:
                        problems.append(
                            {
                                "page": page_number,
                                "bbox": tuple(
                                    round(value, 3) for value in rect
                                ),
                                "candidate_count": len(shadows),
                            }
                        )
                        continue
                    key = (page_number,) + target["key"]
                    affected_groups[key].append(record)
                    primary_keys.add(key)
                    assignment_counts[target["class"]] += 1
                    context_hints[
                        _context_hint(target, result.text)
                    ] += 1
                    shadow_keys.update(
                        (page_number,) + item["key"] for item in shadows
                    )
            page_records.append(records)
    finally:
        document.close()

    if problems:
        raise LegacyYMathRecoveryError(
            f"{len(problems)} audited glyph lines have ambiguous Layout targets"
        )

    modified_lines = 0
    modified_cells = 0
    omitted_headers = 0
    for key, affected in affected_groups.items():
        page_number = key[0]
        target_key = key[1:]
        target = target_maps[page_number - 1][target_key]
        if target_key[0] == "table":
            _patch_table_cell(target, page_records[page_number - 1])
            modified_cells += 1
            continue
        if target_key[0] == "line":
            line = target["line"]
        else:
            box = target["box"]
            if box.boxclass == "page-header" and not box.textlines:
                omitted_headers += len(affected)
                continue
            # 1.28.2 can classify a vertical source line as text while its
            # horizontal-only extractor returns no textlines. Restore only
            # uniquely assigned vertical raw lines from the exact-font audit.
            selected = [r for r in page_records[page_number - 1]
                        if r.get("target_key") == target_key and r["result"].text.strip()]
            if (box.boxclass != "text" or box.textlines or not selected
                    or target["rect"].height <= target["rect"].width * 3
                    or any(r["rect"].height <= r["rect"].width * 3 for r in selected)):
                raise LegacyYMathRecoveryError("non-header Layout box has no unique text line")
            restored = []
            for record in sorted(selected, key=lambda r: r["order"]):
                span = deepcopy(record["raw_line"]["spans"][0])
                span.pop("chars", None)
                span.update(text=record["result"].text, bbox=record["rect"],
                            block=record["order"], line=0, char_flags=span.get("char_flags", 0))
                restored.append({"bbox": record["rect"], "spans": [span]})
            box.textlines = restored
            modified_lines += len(restored)
            continue
        corrected = _render_text_target(
            page_records[page_number - 1],
            target_key,
        )
        _patch_text_line(line, corrected)
        modified_lines += 1

    cleared_auxiliary_lines = 0
    for key in sorted(shadow_keys - primary_keys):
        page_number = key[0]
        target = target_maps[page_number - 1][key[1:]]
        _patch_text_line(target["line"], "")
        cleared_auxiliary_lines += 1

    markdown = _serialize_markdown(parsed)
    if _structure_signature(markdown) != baseline_structure:
        raise LegacyYMathRecoveryError(
            "Layout Markdown structure changed during glyph recovery"
        )
    if "\ufffd" in markdown:
        raise LegacyYMathRecoveryError(
            "Layout Markdown still contains U+FFFD after recovery"
        )
    forbidden_controls = sum(
        unicodedata.category(char) == "Cc"
        and char not in "\t\n\r"
        for char in markdown
    )
    if forbidden_controls:
        raise LegacyYMathRecoveryError(
            "Layout Markdown contains forbidden control characters: "
            f"{forbidden_controls}"
        )
    evidence = {
        "metrics": dict(metrics),
        "assignment_counts": dict(assignment_counts),
        "context_hints": dict(context_hints),
        "modified_lines": modified_lines,
        "modified_cells": modified_cells,
        "cleared_auxiliary_lines": cleared_auxiliary_lines,
        "omitted_headers": omitted_headers,
    }
    return markdown, evidence


def recover_legacy_ymath_layout_pdf(
    path: Path,
    *,
    baseline_markdown: str,
) -> LegacyYMathLayoutRecovery | None:
    """只读恢复 Layout Markdown；不适用时返回 None。"""
    audit = recover_legacy_ymath_pdf(Path(path))
    if audit is None:
        return None
    markdown, evidence = _recover_layout(
        Path(path),
        baseline_markdown=baseline_markdown,
    )
    metrics = evidence["metrics"]
    expected_metrics = {
        "mapped_chars": audit.mapped_chars,
        "superscript_runs": audit.superscript_runs,
        "superscript_chars": audit.superscript_chars,
        "suppressed_accent_spaces": audit.suppressed_accent_spaces,
    }
    if metrics != expected_metrics:
        raise LegacyYMathRecoveryError(
            "Layout provenance coverage differs from raw glyph audit: "
            f"actual={metrics}, expected={expected_metrics}"
        )
    return LegacyYMathLayoutRecovery(
        markdown=markdown,
        pages_total=audit.pages_total,
        mapped_chars=audit.mapped_chars,
        superscript_runs=audit.superscript_runs,
        superscript_chars=audit.superscript_chars,
        suppressed_accent_spaces=audit.suppressed_accent_spaces,
        font_count=audit.font_count,
        modified_lines=int(evidence["modified_lines"]),
        modified_cells=int(evidence["modified_cells"]),
        cleared_auxiliary_lines=int(
            evidence["cleared_auxiliary_lines"]
        ),
        omitted_headers=int(evidence["omitted_headers"]),
        assignment_counts=dict(evidence["assignment_counts"]),
        context_hints=dict(evidence["context_hints"]),
        content_sha256=hashlib.sha256(
            markdown.encode("utf-8")
        ).hexdigest(),
    )
