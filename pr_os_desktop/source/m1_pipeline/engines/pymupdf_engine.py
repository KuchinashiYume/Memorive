"""⚠ 轻量沙盒引擎、非正式主力(承拍板 1)。

pymupdf4llm:纯 wheel、无 torch、快;仅用于**沙盒数字版 PDF** 的快速跑通。
正式主力是 marker(见 config.PDF_PRIMARY);复杂表格 / 多栏 / 扫描件的保真留给 marker,
别拿本引擎的产出去评切块阶段的表格保真。
"""
from __future__ import annotations

from .base import ConvertEngine
from . import register
from .legacy_cfont_layout import (
    LegacyCFontLayoutRecoveryError,
    recover_legacy_cfont_layout_pdf,
)
from .legacy_symbol_layout import (
    LegacySymbolLayoutRecoveryError,
    recover_legacy_symbol_layout_pdf,
    _exact_font_programs,
)
from .legacy_ymath import LegacyYMathRecoveryError
from .legacy_ymath_layout import recover_legacy_ymath_layout_pdf
from ..types import ConvertReport, RawObject


@register
class PymupdfLightEngine(ConvertEngine):
    name = "pymupdf4llm"

    def convert(self, raw: RawObject) -> tuple[str, ConvertReport]:
        from m9_gateway.gateway import current_gateway
        if getattr(current_gateway(), 'isolate_native_pdf', False):
            from .isolated_pdf import convert_isolated
            return convert_isolated(raw)
        return self.convert_in_process(raw)

    def convert_in_process(self, raw: RawObject) -> tuple[str, ConvertReport]:
        from .legacy_founder_eu import open_text_pdf
        with open_text_pdf(raw.path) as (text_document, font_recovery):
            return self._convert(raw, text_document, font_recovery)

    def _convert(self, raw, text_document, font_recovery):
        import pymupdf4llm                   # 懒加载
        # Keep the accepted lightweight conversion independent of optional Layout availability.
        pymupdf4llm.use_layout(False)
        # page_chunks=True 逐页转,拿权威页号;页间插机器可解析页标记 <!-- pros:page:N -->
        # (N=物理页序 1..)。**跨页表的识别/合并/pagespan 不在此做**——留到 M1c 删完页眉页脚后、
        # M1d 切块时按「表身份连续性」判(那时两半表间只剩纯页标记,才判得准;承第2步护栏三)。
        # Select the audited Layout baseline before recovery. The public wrapper
        # has process-global state, so call parse_document explicitly below.
        from .legacy_cfont import _build_font_maps, LegacyCFontRecoveryError
        from .legacy_ymath import recover_legacy_ymath_pdf
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
            from .legacy_founder_eu import orient_recovered_pages
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
        from .legacy_pua_ligatures import recover_pua_ligatures
        ligatures = recover_pua_ligatures(raw.path, pages)
        if ligatures is not None:
            pages = ligatures.pages
        parts = []
        for i, p in enumerate(pages, start=1):
            parts.append(f"<!-- pros:page:{i} -->")
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
                    parts.extend([f"<!-- pros:page:{i} -->", (page.get("text") or "").rstrip()])
                md = "\n\n".join(parts).strip() + "\n"
                pages_total = len(pages)
        warnings = [
            "轻量沙盒引擎(pymupdf4llm)、非主力;复杂表格/多栏/扫描件保真留给 marker",
            "页标记 <!-- pros:page:N --> 按 page_chunks 逐页生成(N=物理页序)",
        ]
        if symbol_layout_selected:
            warnings.append("已核对旧符号字体程序；使用匹配的 Layout 基线进行字形恢复")
        if ligatures is not None:
            warnings.append(f"内嵌字体连字恢复: chars={ligatures.recovered_chars}, font_sha256s={','.join(ligatures.font_sha256s)}")
        if font_recovery:
            warnings.append('已按内嵌字体哈希及原始字形恢复字符编码；保留原生文字，不修改源 PDF')

        # 先按字体程序 SHA / raw code / glyph id / 坐标恢复已审计科学符号。
        # 不根据正文字符或语言猜测；部分可恢复、未知 glyph、结构变化均拒绝。
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

        # 若仍有 U+FFFD，再尝试既有受限 legacy C-font 恢复。
        # 不适用/拒绝时保留原文，交 M1b 安全阀③ fail-closed，绝不静默放行。
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

        # 不根据正文字符猜测：只读核验 PDF 内嵌字体程序。六个程序哈希
        # 全部匹配后才按 glyph id / 坐标桥接回 Layout Markdown；未知项拒绝。
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
        from .chinese_text_coverage import recover_chinese_text, font_unicode_conflicts
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
