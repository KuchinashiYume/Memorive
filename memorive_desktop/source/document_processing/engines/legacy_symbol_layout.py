"""Exact-font recovery for legacy scientific symbols in Layout Markdown.

Hard decisions use embedded font-program SHA-256, raw character code,
texttrace Unicode / glyph id, and geometry. Context hints are audit-only and
never participate in mapping, acceptance, or rejection.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .legacy_ymath import _trace_by_origin
from .legacy_ymath_layout import _structure_signature, _serialize_markdown


_AUDITED_PYMUPDF4LLM_VERSION = "1.28.2"
_PAGE_MARKER_RE = re.compile(r"<!-- memorive:page:(\d+) -->")
_AUDITED_FONT_SHA256 = {
    "AdvPSSym": (
        "f481dab1d75d93885ef23b2ff52d60482ee201b7a5f99108321bea1fc587ed12"
    ),
    "AdvP4C4E74": (
        "d8b89eb316dbfe23514a728d71679382b6c4d83741b1e9e59d6e33e4e1eac69e"
    ),
    "AdvMacMthSy": (
        "96623c54e51874464b5909b6f527c4007591bf0516f75941b25ddd2d3afa105c"
    ),
}
_GLYPH_RULES = {
    # Bonakdarpour2011: /C211 copyright glyph.
    (_AUDITED_FONT_SHA256["AdvPSSym"], 1, 0xFFFD, 1): "©",
    # Bonakdarpour2011: /C14 degree and /C2 multiplication glyphs.
    (_AUDITED_FONT_SHA256["AdvP4C4E74"], 2, 0xFFFD, 5): "°",
    (_AUDITED_FONT_SHA256["AdvP4C4E74"], 3, 0xFFFD, 6): "×",
    # Laspidou2002: /C0 mathematical minus glyph.
    (_AUDITED_FONT_SHA256["AdvMacMthSy"], 1, 0xFFFD, 1): "−",
}


class LegacySymbolLayoutRecoveryError(ValueError):
    """An exact-font symbol candidate cannot be recovered unambiguously."""


@dataclass(frozen=True)
class LegacySymbolLayoutRecovery:
    markdown: str
    pages_total: int
    recovered_chars: int
    font_count: int
    replacement_counts: dict[str, int]
    context_hints: dict[str, int]
    content_sha256: str


def _lookup_replacement(
    font_sha256: str,
    raw_codepoint: int,
    trace_unicode: int,
    glyph_id: int,
) -> str | None:
    return _GLYPH_RULES.get(
        (font_sha256, raw_codepoint, trace_unicode, glyph_id)
    )


def _replace_unknowns(text: str, replacements: list[str]) -> str:
    if text.count("\ufffd") != len(replacements):
        raise LegacySymbolLayoutRecoveryError(
            "Layout replacement count does not match source glyph count"
        )
    output: list[str] = []
    index = 0
    for char in text:
        if char != "\ufffd":
            output.append(char)
            continue
        output.append(replacements[index])
        index += 1
    return "".join(output)


def _replace_markdown_pages(
    markdown: str,
    replacements_by_page: dict[int, list[str]],
    *,
    pages_total: int,
) -> str:
    markers = list(_PAGE_MARKER_RE.finditer(markdown))
    marker_pages = [int(match.group(1)) for match in markers]
    if marker_pages != list(range(1, pages_total + 1)):
        raise LegacySymbolLayoutRecoveryError(
            "production Markdown page markers are not exactly sequential"
        )
    unknown_pages = set(replacements_by_page) - set(marker_pages)
    if unknown_pages:
        raise LegacySymbolLayoutRecoveryError(
            f"replacement pages are outside the document: {sorted(unknown_pages)}"
        )
    output = [markdown[: markers[0].start()]]
    for index, marker in enumerate(markers):
        page_number = int(marker.group(1))
        end = (
            markers[index + 1].start()
            if index + 1 < len(markers)
            else len(markdown)
        )
        segment = markdown[marker.start():end]
        replacements = replacements_by_page.get(page_number, [])
        output.append(_replace_unknowns(segment, replacements))
    return "".join(output)


def _context_hint(box_class: str, replacements: list[str]) -> str:
    """Return an audit-only hint; callers must not use it for decisions."""
    if "©" in replacements:
        return "copyright_or_legal_notice"
    if any(value in {"°", "×", "−"} for value in replacements):
        return "scientific_unit_or_formula"
    if box_class == "list-item":
        return "reference_or_list_item"
    return "body_text"


def _exact_font_programs(document: Any) -> dict[str, str]:
    observed: dict[str, set[str]] = {}
    seen: set[tuple[int, str]] = set()
    for page in document:
        for item in page.get_fonts(full=True):
            xref = int(item[0])
            name = str(item[3]).split("+", 1)[-1]
            if name not in _AUDITED_FONT_SHA256:
                continue
            key = (xref, name)
            if key in seen:
                continue
            seen.add(key)
            extracted = document.extract_font(xref)
            data = extracted[3] if len(extracted) > 3 else b""
            if not data:
                raise LegacySymbolLayoutRecoveryError(
                    f"font {name}: embedded program cannot be extracted"
                )
            observed.setdefault(name, set()).add(
                hashlib.sha256(data).hexdigest()
            )
    exact: dict[str, str] = {}
    for name, hashes in observed.items():
        if len(hashes) != 1:
            raise LegacySymbolLayoutRecoveryError(
                f"font {name}: conflicting embedded program hashes"
            )
        value = next(iter(hashes))
        if value == _AUDITED_FONT_SHA256[name]:
            exact[name] = value
    return exact


def _raw_rule_records(
    page: Any,
    exact_fonts: dict[str, str],
) -> list[dict[str, Any]]:
    import pymupdf

    trace = _trace_by_origin(page)
    raw = page.get_text("rawdict", flags=int(pymupdf.TEXTFLAGS_TEXT))
    records: list[dict[str, Any]] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                font = str(span.get("font") or "")
                font_sha256 = exact_fonts.get(font)
                if font_sha256 is None:
                    continue
                for char in span.get("chars", []):
                    value = str(char.get("c") or "")
                    if len(value) != 1 or char.get("synthetic") is True:
                        continue
                    origin = tuple(
                        round(float(component), 3)
                        for component in char.get("origin", ())
                    )
                    traced = trace.get((font, origin))
                    if traced is None:
                        continue
                    replacement = _lookup_replacement(
                        font_sha256,
                        ord(value),
                        int(traced[0]),
                        int(traced[1]),
                    )
                    if replacement is None:
                        continue
                    rect = (
                        pymupdf.Rect(char["bbox"])
                        * page.rotation_matrix
                    )
                    records.append(
                        {
                            "font": font,
                            "origin": origin,
                            "rect": rect,
                            "center": pymupdf.Point(
                                (rect.x0 + rect.x1) / 2,
                                (rect.y0 + rect.y1) / 2,
                            ),
                            "replacement": replacement,
                        }
                    )
    return records


def recover_legacy_symbol_layout_pdf(
    path: Path,
    *,
    baseline_markdown: str,
) -> LegacySymbolLayoutRecovery | None:
    """Recover exact audited symbol glyphs while preserving Layout structure."""
    if "\ufffd" not in baseline_markdown:
        return None

    import pymupdf
    import pymupdf4llm
    from pymupdf4llm.helpers.document_layout import parse_document

    if str(getattr(pymupdf4llm, "__version__", "")) != (
        _AUDITED_PYMUPDF4LLM_VERSION
    ):
        raise LegacySymbolLayoutRecoveryError(
            "unaudited pymupdf4llm version: "
            f"{getattr(pymupdf4llm, '__version__', None)!r}"
        )

    document = pymupdf.open(Path(path))
    try:
        exact_fonts = _exact_font_programs(document)
        if not exact_fonts:
            return None

        try:
            import pymupdf.layout
            pymupdf.layout.activate()
        except (ImportError, OSError) as exc:
            raise LegacySymbolLayoutRecoveryError("LAYOUT_RUNTIME_UNAVAILABLE") from exc
        parsed = parse_document(Path(path), use_ocr=False, force_text=True)
        if len(parsed.pages) != len(document):
            raise LegacySymbolLayoutRecoveryError(
                "Parsed Layout page count differs from PDF page count"
            )
        if _serialize_markdown(parsed) != baseline_markdown:
            raise LegacySymbolLayoutRecoveryError('Layout parser baseline differs from the engine Markdown')
        baseline_structure = _structure_signature(baseline_markdown)

        affected: list[dict[str, Any]] = []
        unrecognized_count = 0
        for page_number, layout_page in enumerate(parsed.pages, start=1):
            for box in layout_page.boxes:
                if box.boxclass == "table":
                    table = box.table
                    extract = table.get("extract") or []
                    cells = table.get("cells") or []
                    cell_count = 0
                    for ri, row in enumerate(extract):
                        for ci, value in enumerate(row):
                            count = str(value or "").count("\ufffd")
                            if not count:
                                continue
                            if count != 1:
                                raise LegacySymbolLayoutRecoveryError('multiple unknown glyphs in one table cell require a reading-order audit')
                            if ri >= len(cells) or ci >= len(cells[ri]) or cells[ri][ci] is None:
                                raise LegacySymbolLayoutRecoveryError("unknown table cell lacks unique geometry")
                            affected.append({"page": page_number, "box_class": "table",
                                "span": {"bbox": cells[ri][ci], "text": str(value)},
                                "font": None, "count": count})
                            cell_count += count
                    if cell_count != str(table.get("markdown") or "").count("\ufffd"):
                        raise LegacySymbolLayoutRecoveryError("table text and Markdown unknown counts differ")
                for line in box.textlines or []:
                    for span in line.get("spans", []):
                        text = str(span.get("text") or "")
                        count = text.count("\ufffd")
                        if not count:
                            continue
                        font = str(span.get("font") or "")
                        if font not in exact_fonts:
                            unrecognized_count += count
                            continue
                        affected.append(
                            {
                                "page": page_number,
                                "box_class": box.boxclass,
                                "span": span,
                                "font": font,
                                "count": count,
                            }
                        )
        if not affected:
            return None
        if unrecognized_count:
            raise LegacySymbolLayoutRecoveryError(
                "exact-font recovery would leave "
                f"{unrecognized_count} unrecognized replacement characters"
            )

        raw_by_page = {
            page_number: _raw_rule_records(page, exact_fonts)
            for page_number, page in enumerate(document, start=1)
        }
        used_sources: set[tuple[int, str, tuple[float, float]]] = set()
        replacement_counts: Counter = Counter()
        context_hints: Counter = Counter()
        used_fonts: set[str] = set()
        replacements_by_page: dict[int, list[str]] = {}
        for item in affected:
            span = item["span"]
            span_rect = pymupdf.Rect(span["bbox"])
            candidates = [
                record
                for record in raw_by_page[item["page"]]
                if (item["font"] is None or record["font"] == item["font"])
                and (span_rect.contains(record["center"])
                     or (item["font"] is not None and (span_rect & record["rect"]).get_area() > 0))
            ]
            if item["font"] is None:
                admitted = {(r["font"], r["origin"]) for r in candidates}
                page = document[item["page"] - 1]
                for trace_span in page.get_texttrace():
                    for char in trace_span["chars"]:
                        if int(char[0]) != 0xFFFD:
                            continue
                        rect = pymupdf.Rect(char[3]) * page.rotation_matrix
                        center = pymupdf.Point((rect.x0 + rect.x1)/2, (rect.y0 + rect.y1)/2)
                        key = (str(trace_span["font"]), tuple(round(float(v), 3) for v in char[2]))
                        if span_rect.contains(center) and key not in admitted:
                            raise LegacySymbolLayoutRecoveryError("table cell contains an unaudited unknown glyph")
                candidates.sort(key=lambda r: (float(r["rect"].y0), float(r["rect"].x0)))
            else:
                candidates.sort(key=lambda r: (float(r["rect"].x0), float(r["rect"].y0)))
            if len(candidates) != item["count"]:
                raise LegacySymbolLayoutRecoveryError(
                    f"page {item['page']} font {item['font']}: "
                    f"{item['count']} Layout replacements map to "
                    f"{len(candidates)} audited raw glyphs"
                )
            source_keys = [
                (item["page"], record["font"], record["origin"])
                for record in candidates
            ]
            if any(key in used_sources for key in source_keys):
                raise LegacySymbolLayoutRecoveryError(
                    "one audited raw glyph maps to multiple Layout spans"
                )
            used_sources.update(source_keys)
            replacements = [
                str(record["replacement"]) for record in candidates
            ]
            _replace_unknowns(
                str(span.get("text") or ""),
                replacements,
            )
            replacements_by_page.setdefault(item["page"], []).extend(
                replacements
            )
            replacement_counts.update(replacements)
            context_hints[
                _context_hint(item["box_class"], replacements)
            ] += len(replacements)
            used_fonts.update(record["font"] for record in candidates)

        markdown = _replace_markdown_pages(
            baseline_markdown,
            replacements_by_page,
            pages_total=len(parsed.pages),
        )
        if _structure_signature(markdown) != baseline_structure:
            raise LegacySymbolLayoutRecoveryError(
                "Layout Markdown structure changed during symbol recovery"
            )
        if "\ufffd" in markdown:
            raise LegacySymbolLayoutRecoveryError(
                "Layout Markdown still contains U+FFFD after symbol recovery"
            )
        forbidden_controls = sum(
            unicodedata.category(char) == "Cc"
            and char not in "\t\n\r"
            for char in markdown
        )
        if forbidden_controls:
            raise LegacySymbolLayoutRecoveryError(
                "Layout Markdown contains forbidden controls after recovery: "
                f"{forbidden_controls}"
            )
        recovered_chars = sum(replacement_counts.values())
        if recovered_chars != len(used_sources):
            raise LegacySymbolLayoutRecoveryError(
                "recovered character count differs from source glyph count"
            )
        return LegacySymbolLayoutRecovery(
            markdown=markdown,
            pages_total=len(parsed.pages),
            recovered_chars=recovered_chars,
            font_count=len(used_fonts),
            replacement_counts=dict(replacement_counts),
            context_hints=dict(context_hints),
            content_sha256=hashlib.sha256(
                markdown.encode("utf-8")
            ).hexdigest(),
        )
    finally:
        document.close()
