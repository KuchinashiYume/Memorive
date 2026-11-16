"""The light engine for digital PDFs in the sandbox, not the main one.

pymupdf4llm: a plain wheel, no torch, fast. The main engine is marker (config.PDF_PRIMARY); complex
tables, columns and scans are marker's, so this engine's output is no measure of table fidelity in
chunking.
"""
from __future__ import annotations

from memorive.documents.processing.engines.base import ConvertEngine
from memorive.documents.processing.engines import register
from memorive.documents.processing.engines.legacy_cfont_layout import (
    LegacyCFontLayoutRecoveryError,
    recover_legacy_cfont_layout_pdf,
)
from memorive.documents.processing.engines.legacy_symbol_layout import (
    LegacySymbolLayoutRecoveryError,
    recover_legacy_symbol_layout_pdf,
    _exact_font_programs,
)
from memorive.documents.processing.engines.legacy_ymath import LegacyYMathRecoveryError
from memorive.documents.processing.engines.legacy_ymath_layout import recover_legacy_ymath_layout_pdf
from memorive.documents.processing.types import ConvertReport, RawObject


@register
class PymupdfLightEngine(ConvertEngine):
    name = "pymupdf4llm"

    def convert(self, raw: RawObject) -> tuple[str, ConvertReport]:
        from memorive.models.gateway.gateway import current_gateway
        if getattr(current_gateway(), 'isolate_native_pdf', False):
            from memorive.documents.processing.engines.isolated_pdf import convert_isolated
            return convert_isolated(raw)
        return self.convert_in_process(raw)

    def convert_in_process(self, raw: RawObject) -> tuple[str, ConvertReport]:
        from memorive.documents.processing.engines.legacy_founder_eu import open_text_pdf
        with open_text_pdf(raw.path) as (text_document, font_recovery):
            return self._convert(raw, text_document, font_recovery)

    def _convert(self, raw, text_document, font_recovery):
        import pymupdf4llm                   # loaded when used
        # Keep the accepted lightweight conversion independent of optional Layout availability.
        pymupdf4llm.use_layout(False)
        # page_chunks=True converts page by page with the real page numbers; a marker
        # <!-- memorive:page:N --> (N the physical page, from 1) goes between pages. Tables across
        # pages are joined later, in chunking, after cleaning has removed headers and footers, when
        # only the page marker sits between the two halves.
        # Select the audited Layout baseline before recovery. The public wrapper
        # has process-global state, so call parse_document explicitly below.
        from memorive.documents.processing.engines.legacy_cfont import _build_font_maps, LegacyCFontRecoveryError
        from memorive.documents.processing.engines.legacy_ymath import recover_legacy_ymath_pdf
        try:
            audited_cfont = _build_font_maps(text_document) is not None
        except LegacyCFontRecoveryError:
            # Shared font names alone do not select the complete audited family.
            # Any remaining unknown text still goes through strict recovery below.
            audited_cfont = False
        audited_ymath = recover_legacy_ymath_pdf(raw.path) is not None
        if audited_cfont or audited_ymath or any(row.get('requires_layout', True) for row in font_recovery):
            # Reuse the Layout parser already used by the audited legacy-font
            # paths. The lightweight line grouping splits this font's tall
            # Latin metrics away from Chinese and drops sideways table cells.
            from memorive.documents.processing.engines.legacy_founder_eu import orient_recovered_pages
            orientations = orient_recovered_pages(text_document,
                {row['page_number'] for row in font_recovery})
            font_recovery = [*font_recovery, *orientations]
            import pymupdf.layout
            from pymupdf4llm.helpers.document_layout import parse_document
            pymupdf.layout.activate()
            pages = parse_document(text_document, use_ocr=False,
                force_text=True).to_markdown(page_chunks=True)
        else:
            pages = pymupdf4llm.to_markdown(text_document, page_chunks=True)
        from memorive.documents.processing.engines.legacy_pua_ligatures import recover_pua_ligatures
        ligatures = recover_pua_ligatures(raw.path, pages)
        if ligatures is not None:
            pages = ligatures.pages
        parts = []
        for i, p in enumerate(pages, start=1):
            parts.append(f"<!-- memorive:page:{i} -->")
            parts.append((p.get("text") or "").rstrip())
        md = "\n\n".join(parts).strip() + "\n"
        pages_total = len(pages)
        symbol_layout_selected = False
        if "\ufffd" in md:
            import pymupdf
            with pymupdf.open(raw.path) as document:
                symbol_layout_selected = bool(_exact_font_programs(document))
            if symbol_layout_selected:
                # The audited symbol recovery maps Layout spans to source glyphs.
                # Produce its baseline with the same parser before any replacement.
                import pymupdf.layout
                from pymupdf4llm.helpers.document_layout import parse_document
                pymupdf.layout.activate()
                pages = parse_document(raw.path, use_ocr=False, force_text=True).to_markdown(page_chunks=True)
                parts = []
                for i, page in enumerate(pages, start=1):
                    parts.extend([f"<!-- memorive:page:{i} -->", (page.get("text") or "").rstrip()])
                md = "\n\n".join(parts).strip() + "\n"
                pages_total = len(pages)
        warnings = [
            "轻量沙盒引擎(pymupdf4llm)、非主力;复杂表格/多栏/扫描件保真留给 marker",
            "页标记 <!-- memorive:page:N --> 按 page_chunks 逐页生成(N=物理页序)",
        ]
        if symbol_layout_selected:
            warnings.append("已核对旧符号字体程序；使用匹配的 Layout 基线进行字形恢复")
        if ligatures is not None:
            warnings.append(f"内嵌字体连字恢复: chars={ligatures.recovered_chars}, font_sha256s={','.join(ligatures.font_sha256s)}")
        if font_recovery:
            warnings.append('已按内嵌字体哈希及原始字形恢复字符编码；保留原生文字，不修改源 PDF')

        # First restore audited scientific symbols by font program SHA, raw code, glyph id and position.
        # Nothing is guessed from the text or its language; a partial restore, an unknown glyph or a
        # changed structure is refused.
        if "\ufffd" in md:
            try:
                symbols = recover_legacy_symbol_layout_pdf(
                    raw.path,
                    baseline_markdown=md,
                )
            except LegacySymbolLayoutRecoveryError as exc:
                raise RuntimeError(
                    f"legacy symbol 来源恢复拒绝:{exc}"
                ) from exc
            if symbols is not None:
                if symbols.pages_total != len(pages):
                    raise RuntimeError(
                        "legacy symbol 来源恢复拒绝:"
                        f"页数 {symbols.pages_total} != {len(pages)}"
                    )
                md = symbols.markdown
                pages_total = symbols.pages_total
                replacements = ",".join(
                    f"U+{ord(key):04X}:{value}"
                    for key, value in sorted(
                        symbols.replacement_counts.items()
                    )
                )
                context_hints = ",".join(
                    f"{key}:{value}"
                    for key, value in sorted(
                        symbols.context_hints.items()
                    )
                )
                warnings.append(
                    "legacy symbol 来源确定性恢复:"
                    f"recovered={symbols.recovered_chars},"
                    f"fonts={symbols.font_count},"
                    f"replacements={replacements},"
                    f"context_hints={context_hints},"
                    f"sha256={symbols.content_sha256}"
                )

        # If U+FFFD is still there, try the restricted legacy C-font restore. When it does not apply
        # or refuses, the text stays as it is and conversion's check stops it.
        if "\ufffd" in md:
            try:
                recovered = recover_legacy_cfont_layout_pdf(
                    raw.path,
                    baseline_markdown=md,
                )
            except LegacyCFontLayoutRecoveryError as exc:
                warnings.append(f"legacy C-font 恢复拒绝:{exc}")
            else:
                if recovered is None:
                    warnings.append("检测到 U+FFFD;不符合受限 legacy C-font 格式")
                elif recovered.pages_total != len(pages):
                    warnings.append(
                        "legacy C-font 恢复拒绝:"
                        f"页数 {recovered.pages_total} != {len(pages)}")
                else:
                    md = recovered.markdown
                    pages_total = recovered.pages_total
                    warnings.append(
                        "legacy C-font 确定性恢复:"
                        f"mapped={recovered.mapped_chars},"
                        f"synthetic={recovered.synthetic_chars},"
                        f"fonts={recovered.font_count},"
                        f"modified_lines={recovered.modified_lines},"
                        f"modified_cells={recovered.modified_cells},"
                        f"cleared_auxiliary_lines="
                        f"{recovered.cleared_auxiliary_lines},"
                        f"modified_picture_boxes="
                        f"{recovered.modified_picture_boxes},"
                        f"omitted_formula_chars="
                        f"{recovered.omitted_formula_chars},"
                        f"context_hints={recovered.context_hints},"
                        f"sha256={recovered.content_sha256}")

        # Nothing is guessed from the text: the PDF's embedded font programs are checked, and only when
        # all six program hashes match are glyph ids and positions mapped back into the Layout
        # Markdown; anything unknown is refused.
        try:
            ymath = recover_legacy_ymath_layout_pdf(
                raw.path,
                baseline_markdown=md,
            )
        except LegacyYMathRecoveryError as exc:
            raise RuntimeError(
                f"legacy YMath 恢复拒绝:{exc}"
            ) from exc
        if ymath is not None:
            if ymath.pages_total != len(pages):
                raise RuntimeError(
                    "legacy YMath 恢复拒绝:"
                    f"页数 {ymath.pages_total} != {len(pages)}"
                )
            md = ymath.markdown
            pages_total = ymath.pages_total
            context_hints = ",".join(
                f"{key}:{value}"
                for key, value in sorted(ymath.context_hints.items())
            )
            warnings.append(
                "legacy YMath 来源确定性恢复:"
                f"mapped={ymath.mapped_chars},"
                f"superscript_runs={ymath.superscript_runs},"
                f"suppressed_spaces={ymath.suppressed_accent_spaces},"
                f"fonts={ymath.font_count},"
                f"modified_lines={ymath.modified_lines},"
                f"modified_cells={ymath.modified_cells},"
                f"cleared_auxiliary_lines={ymath.cleared_auxiliary_lines},"
                f"omitted_headers={ymath.omitted_headers},"
                f"context_hints={context_hints},"
                f"sha256={ymath.content_sha256}"
            )
        from memorive.documents.processing.engines.chinese_text_coverage import recover_chinese_text, font_unicode_conflicts
        font_conflicts = font_unicode_conflicts(text_document)
        md, chinese_coverage = recover_chinese_text(text_document, md,
            excluded_pages={row['page_number'] for row in font_conflicts},
            include_short_runs=bool(font_recovery))
        if font_conflicts:
            warnings.append('PDF 来源文字层存在编码、空白字形或隐藏 OCR 风险：受影响页面需图像转录，禁止从该文字层补写')
        if chinese_coverage:
            warnings.append(
                f"PDF 文字层覆盖恢复: runs={len(chinese_coverage)};"
                "仅恢复原生文字，原文单位与歧义保留；坐标与哈希见 page_details"
            )
        report = ConvertReport(
            engine=self.name, pages_total=pages_total, pages_failed=[],
            warnings=warnings, page_details=[*font_recovery, *font_conflicts, *chinese_coverage])
        return md, report
