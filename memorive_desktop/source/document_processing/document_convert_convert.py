"""DocumentConvert 转换/OCR —— 传送带第二节。

把 RawObject 转成 Raw MD:引擎可插拔(改配置即换;正式主力 marker、沙盒轻量 pymupdf4llm、
txt 用 markitdown、图片经 MODEL_GATEWAY OCR),输出 Raw MD + 转换报告,并按命名规范落
文件夹 / [PDF] / [RawMD] / paper_id、写全 frontmatter 交 DocumentClean。
三道安全阀在此:① 需 OCR 但 model_gateway_ocr 未就绪;② 数字版 PDF 疑似扫描件;
③ PDF 转换文本含灾难性 U+FFFD 损坏。
均报错挡下 + 写 RUNTIME_LOG detail,绝不静默降级。
守边界:DocumentConvert 只转换,不清洗页眉页脚/断行(DocumentClean)、不判质量(EVIDENCE_REVIEW)。
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import re
import shutil
import unicodedata
from pathlib import Path

from runtime_log import log, log_error

from .config import (CORRUPT_PAGE_FRACTION_FAIL_RATIO, OCR_VIA_MODEL_GATEWAY_READY,
                     PDF_SANDBOX_LIGHT, SCAN_SUSPECT_MIN_CHARS_PER_PAGE,
                     TEXT_REPLACEMENT_FAIL_RATIO, engine_for, library_root,
                     now_iso)
from .engines import get_engine
from .errors import OcrSafetyValve, ScanSuspectError, TextIntegrityError
from .naming import PaperIdRegistry, display_title, file_name, folder_name
from .ocr_page_rescue import rescue_pdf_pages
from .types import ConvertResult, RawObject


_PAGE_MARKER_RE = re.compile(r"<!--\s*memorive:page:\d+\s*-->")
_PAGE_BLOCK_RE = re.compile(
    r"<!--\s*memorive:page:(\d+)\s*-->(.*?)(?=<!--\s*memorive:page:\d+\s*-->|$)",
    re.DOTALL,
)
_ALLOWED_TEXT_CONTROLS = frozenset({"\t", "\n", "\r"})


def _is_forbidden_control(character: str) -> bool:
    return (
        character not in _ALLOWED_TEXT_CONTROLS
        and unicodedata.category(character) == "Cc"
    )


def _forbidden_control_count(text: str) -> int:
    return sum(_is_forbidden_control(character) for character in text)


def _validate_pdf_text_integrity(raw_md_text: str, *, pages_total: int) -> None:
    """校验确定性 U+FFFD / Cc 损坏；不判断内容正确性或重要性。"""
    text = raw_md_text or ""
    content_only = _PAGE_MARKER_RE.sub("", text)
    replacement_count = content_only.count("\ufffd")
    replacement_ratio = replacement_count / max(len(content_only), 1)
    if replacement_ratio >= TEXT_REPLACEMENT_FAIL_RATIO:
        raise TextIntegrityError(
            "PDF 转换文本完整性安全阀③触发:"
            f"全文替换字符比例 {replacement_ratio:.4%} >= 阈值 "
            f"{TEXT_REPLACEMENT_FAIL_RATIO:.4%} "
            f"(U+FFFD {replacement_count}/{len(content_only)});"
            "拒绝把确定性损坏文本送入 DocumentClean/DocumentChunk/DocumentEmbed。请改用可正确解析该 PDF 的引擎。")

    bad_pages: list[int] = []
    observed_pages = 0
    for match in _PAGE_BLOCK_RE.finditer(text):
        observed_pages += 1
        nonspace = "".join(match.group(2).split())
        page_ratio = nonspace.count("\ufffd") / max(len(nonspace), 1)
        if page_ratio >= TEXT_REPLACEMENT_FAIL_RATIO:
            bad_pages.append(int(match.group(1)))

    page_denominator = max(pages_total, observed_pages, 1)
    bad_page_fraction = len(bad_pages) / page_denominator
    if bad_page_fraction >= CORRUPT_PAGE_FRACTION_FAIL_RATIO:
        raise TextIntegrityError(
            "PDF 转换文本完整性安全阀③触发:"
            f"高替换字符页 {len(bad_pages)}/{page_denominator} = "
            f"{bad_page_fraction:.4%} >= 阈值 "
            f"{CORRUPT_PAGE_FRACTION_FAIL_RATIO:.4%};"
            f"单页判据 U+FFFD/非空白字符 >= {TEXT_REPLACEMENT_FAIL_RATIO:.4%};"
            f"页码={bad_pages};拒绝把确定性损坏文本送入 DocumentClean/DocumentChunk/DocumentEmbed。"
            "请改用可正确解析该 PDF 的引擎。")

    control_count = _forbidden_control_count(content_only)
    control_ratio = control_count / max(len(content_only), 1)
    if control_ratio >= TEXT_REPLACEMENT_FAIL_RATIO:
        raise TextIntegrityError(
            "PDF 转换文本完整性安全阀③触发:"
            f"全文控制字符比例 {control_ratio:.4%} >= 阈值 "
            f"{TEXT_REPLACEMENT_FAIL_RATIO:.4%} "
            f"(Unicode Cc {control_count}/{len(content_only)};"
            "已豁免 TAB/LF/CR);拒绝把确定性损坏文本送入 DocumentClean/DocumentChunk/DocumentEmbed。"
            "请改用可正确解析该 PDF 的引擎。")

    bad_control_pages: list[int] = []
    observed_control_pages = 0
    for match in _PAGE_BLOCK_RE.finditer(text):
        observed_control_pages += 1
        page_text = match.group(2)
        page_basis = "".join(
            character
            for character in page_text
            if not character.isspace() or _is_forbidden_control(character)
        )
        page_control_ratio = (
            _forbidden_control_count(page_basis) / max(len(page_basis), 1)
        )
        if page_control_ratio >= TEXT_REPLACEMENT_FAIL_RATIO:
            bad_control_pages.append(int(match.group(1)))

    control_page_denominator = max(
        pages_total, observed_control_pages, 1)
    bad_control_page_fraction = (
        len(bad_control_pages) / control_page_denominator)
    if bad_control_page_fraction >= CORRUPT_PAGE_FRACTION_FAIL_RATIO:
        raise TextIntegrityError(
            "PDF 转换文本完整性安全阀③触发:"
            f"高控制字符页 {len(bad_control_pages)}/"
            f"{control_page_denominator} = {bad_control_page_fraction:.4%} "
            f">= 阈值 {CORRUPT_PAGE_FRACTION_FAIL_RATIO:.4%};"
            "单页判据 Unicode Cc(豁免 TAB/LF/CR)/有效字符 >= "
            f"{TEXT_REPLACEMENT_FAIL_RATIO:.4%};页码={bad_control_pages};"
            "拒绝把确定性损坏文本送入 DocumentClean/DocumentChunk/DocumentEmbed。"
            "请改用可正确解析该 PDF 的引擎。")


def _yaml_scalar(s: str) -> str:
    """把任意字符串写成合法 YAML 标量(JSON 双引号形式即合法 YAML)。"""
    return json.dumps(s, ensure_ascii=False)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _anchor_projection(raw_md_text: str) -> list[dict]:
    """Project legacy page markers and Intake typed markers without body copies."""
    from .document_conversion.diagnostics import parse_source_marker

    anchors: list[dict] = [
        {"kind": "page", "ordinal": int(value)}
        for value in re.findall(r"<!--\s*memorive:page:(\d+)\s*-->", raw_md_text)
    ]
    for line in raw_md_text.splitlines():
        parsed = parse_source_marker(line)
        if parsed is not None:
            anchors.append(parsed)
    return anchors


def _frontmatter(paper_id, title, meta, report, engine_name, converted_at) -> str:
    """[RawMD] frontmatter,补充要求②:9 项写全,确保 DocumentClean 能接。"""
    cr = {"engine": report.engine, "engine_version": report.engine_version,
          "pages_total": report.pages_total,
          "pages_failed": report.pages_failed, "warnings": report.warnings,
          "complete": report.complete, "page_details": report.page_details,
          "source_sha256": report.source_sha256,
          "output_sha256": report.output_sha256,
          "config_hash": report.config_hash,
          "format_verdict": report.format_verdict,
          "selection_status": report.selection_status,
          "recognition_status": report.recognition_status,
          "fallback_trace": report.fallback_trace,
          "anchor_map": report.anchor_map}
    receipt_basis = "|".join(filter(None, (
        report.source_sha256, report.output_sha256, report.config_hash)))
    receipt_id = "IntakeL-" + hashlib.sha256(receipt_basis.encode("utf-8")).hexdigest()[:24]
    lines = [
        "---",
        f"paper_id: {paper_id}",
        f"title: {_yaml_scalar(title)}",
        f"data_ownership: {meta.data_ownership}",   # 红线标记:经此 frontmatter 传给 DocumentClean/DocumentChunk/DocumentEmbed
        f"doc_type: {meta.doc_type}",
        f"original_format: {meta.original_format}",
        "review_status: pending",                   # KNOWLEDGE_ADMISSION 占位(正式状态机 Intake);DOCUMENT_PROCESSING 产物不放行 Active
        f"convert_engine: {engine_name}",
        "converter_profile_id: legacy_document_convert_compat_v1",
        f"source_sha256: {report.source_sha256}",
        f"convert_receipt_id: {receipt_id}",
        f"converted_at: {converted_at}",
        f"convert_report: {json.dumps(cr, ensure_ascii=False)}",
        "---",
        "",
    ]
    return "\n".join(lines)


def convert(raw: RawObject, *, paper_id: str, title: str,
            target: str = "sandbox", engine: str | None = None,
            allow_short: bool = False,
            ocr_pages: list[int] | None = None) -> ConvertResult:
    """转换一个 RawObject。paper_id/title 由人工给定/确认(承确认 4),不凭元数据自动定。
    allow_short=True:人工确认「非扫描件、正文本就短」时放行安全阀②(留痕,非静默)。"""
    fmt = raw.meta.original_format
    engine_name = engine or engine_for(fmt, target)
    source_sha256 = _sha256(raw.path)
    # Desktop jobs bind their selected OCR model through the existing MODEL_GATEWAY scope.
    # Legacy callers retain their explicit readiness switch; no global route is changed.
    from model_gateway.gateway import current_gateway
    ocr_ready = OCR_VIA_MODEL_GATEWAY_READY or bool(getattr(current_gateway(), 'page_ocr_ready', False))

    # ── 安全阀①:需 OCR(图片/扫描)但 model_gateway_ocr 未就绪 → 报错挡下,不静默降级 ──
    if (
        engine_name == "model_gateway_ocr"
        or (fmt == "pdf" and ocr_pages)
    ) and not ocr_ready:
        log("DocumentConvert", "OCR 安全阀①触发", is_error=True, context={
            "step": "ocr_gate",
            "error": ("输入需 OCR(图片/扫描件),但 model_gateway_ocr 未就绪(ocr 槽+adapter 未建);"
                      "拒绝静默降级(不改走 pytesseract / marker 内部 OCR / 直连 SDK)。"),
            "dependency": "model_gateway_ocr", "paper_id": paper_id, "original_format": fmt,
            "source": raw.meta.source})
        raise OcrSafetyValve(
            "需 OCR 但 model_gateway_ocr 未就绪;拒绝静默降级。加 ocr 槽 + adapter 后再处理该输入。")

    # ── paper_id 人工确认表:碰撞走人工消歧,不自动按导入顺序定(承确认 4)──
    reg = PaperIdRegistry(target=target)
    disp = display_title(title)
    folder = library_root(target) / folder_name(paper_id, disp)
    reg.ensure(paper_id, folder)

    # ── 转换(只转换、不清洗、不判质量)──
    eng = get_engine(engine_name)
    try:
        raw_md_text, report = eng.convert(raw)
    except Exception as e:
        log_error("DocumentConvert", "转换失败", context={
            "step": "convert", "error": str(e), "dependency": engine_name,
            "paper_id": paper_id, "source": raw.meta.source})
        raise

    # ── 页面级救援：主引擎先跑，只有候选页经 MODEL_GATEWAY 单页 OCR；默认关闭、显式 readiness 才进入 ──
    if any(row.get('requires_page_ocr') for row in report.page_details) and not ocr_ready:
        raise TextIntegrityError('PDF_FONT_UNICODE_CONFLICT: configured page OCR is required')
    if fmt == "pdf" and ocr_ready:
        try:
            raw_md_text, report = rescue_pdf_pages(
                raw,
                raw_md_text,
                report,
                forced_pages=ocr_pages,
            )
        except Exception as exc:
            log_error("DocumentConvert", "页面级 OCR 救援中止", context={
                "step": "ocr_page_rescue",
                "error": f"{type(exc).__name__}: {exc}",
                "dependency": "model_gateway/ocr",
                "paper_id": paper_id,
            })
            raise

    # ── 安全阀③:字符很多但含灾难性 U+FFFD → 报错挡下,不产出伪成功 RawMD ──
    if fmt == "pdf":
        try:
            _validate_pdf_text_integrity(
                raw_md_text, pages_total=report.pages_total)
        except TextIntegrityError as exc:
            log("DocumentConvert", "文本完整性安全阀③触发", is_error=True, context={
                "step": "text_integrity", "error": str(exc),
                "dependency": engine_name, "paper_id": paper_id,
                "pages": str(report.pages_total)})
            if report.complete:
                raise
            # OCR 已逐页尝试但仍有失败页：保留其他完成页，交编排层停在 DocumentConvert 后。
            report = replace(
                report,
                warnings=[
                    *report.warnings,
                    f"text_integrity_pending:{type(exc).__name__}",
                ],
            )

    # ── 安全阀②:数字版 PDF 文本过少 → 疑似扫描件,报错挡下、不产出空 [RawMD] ──
    if fmt == "pdf":
        # 扫描件判据只数正文,排除工程页标记(否则标记字符会虚增计数、削弱安全阀②)
        content_only = _PAGE_MARKER_RE.sub("", raw_md_text or "")
        nonspace = len("".join(content_only.split()))
        floor = SCAN_SUSPECT_MIN_CHARS_PER_PAGE * max(report.pages_total, 1)
        if nonspace < floor:
            if not report.complete:
                report = replace(
                    report,
                    warnings=[
                        *report.warnings,
                        "scan_suspect_pending:partial_ocr_result",
                    ],
                )
                log("DocumentConvert", "疑似扫描件部分救援结果已保留", data={
                    "paper_id": paper_id,
                    "chars": nonspace,
                    "floor": floor,
                    "pages_failed": report.pages_failed,
                })
            elif allow_short:
                # 人工确认「非扫描件、正文本就短」→ 放行,但留痕(显式 override、非静默)
                log("DocumentConvert", "短文本放行(人工确认非扫描件)", data={
                    "paper_id": paper_id, "chars": nonspace, "floor": floor,
                    "pages": report.pages_total, "note": "allow_short=True 人工 override"})
            else:
                log("DocumentConvert", "疑似扫描件安全阀②触发", is_error=True, context={
                    "step": "scan_suspect",
                    "error": (f"数字版引擎提取文本过少({nonspace} 字 < 阈值 {floor} = "
                              f"{SCAN_SUSPECT_MIN_CHARS_PER_PAGE}/页×{report.pages_total}页);疑似扫描/图片型 PDF。"
                              "拒绝静默产出空 [RawMD]。处理:①确是扫描/图片型→改走 OCR(经 MODEL_GATEWAY);"
                              "②确是正文本就短的数字版(补充材料/一页摘要)→人工确认后 "
                              "convert(allow_short=True) 放行。"),
                    "dependency": engine_name, "paper_id": paper_id,
                    "chars": str(nonspace), "pages": str(report.pages_total),
                    "threshold_per_page": str(SCAN_SUSPECT_MIN_CHARS_PER_PAGE)})
                raise ScanSuspectError(
                    f"疑似扫描件:提取文本 {nonspace} 字 < 阈值 {floor};未产出 [RawMD]。"
                    "改走 OCR(经 MODEL_GATEWAY),或经人工确认非扫描件后 convert(allow_short=True) 放行。")

    output_sha256 = hashlib.sha256(raw_md_text.encode("utf-8")).hexdigest().upper()
    config_projection = {
        "profile_id": "legacy_document_convert_compat_v1",
        "format": fmt,
        "engine": engine_name,
        "target": target,
        "allow_short": bool(allow_short),
        "ocr_pages": list(ocr_pages or []),
    }
    config_hash = hashlib.sha256(
        json.dumps(
            config_projection,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest().upper()
    report = replace(
        report,
        source_sha256=source_sha256,
        output_sha256=output_sha256,
        config_hash=config_hash,
        format_verdict="supported" if report.complete else "degraded",
        selection_status="primary_selected" if report.complete else "not_selected",
        recognition_status=(
            "not_run_requires_retrieval"
            if fmt in {"image", "jpeg", "png", "tiff"}
            else "not_applicable"
        ),
        anchor_map=_anchor_projection(raw_md_text),
    )

    # ── 命名落盘:[PDF] 存档 + [RawMD](frontmatter 9 项)──
    folder.mkdir(parents=True, exist_ok=True)
    rawmd_path = folder / file_name("RawMD", paper_id, disp, "md")
    pdf_path: Path | None = None
    original_path: Path | None = None
    if fmt == "pdf":
        pdf_path = folder / file_name("PDF", paper_id, disp, "pdf")
        shutil.copy2(raw.path, pdf_path)            # 原件存档为 [PDF]
        original_path = pdf_path
    else:
        original_extension = raw.path.suffix.lower().lstrip(".") or fmt
        original_path = folder / file_name(
            "Original", paper_id, disp, original_extension)
        shutil.copy2(raw.path, original_path)
    if _sha256(original_path) != source_sha256:
        raise DocumentProcessingError("原件存档 hash 与导入源不一致;已阻断 RawMD 投影")

    converted_at = now_iso()
    rawmd_path.write_text(
        _frontmatter(paper_id, title, raw.meta, report, report.engine, converted_at) + raw_md_text,
        encoding="utf-8")
    reg.commit(paper_id, folder)                    # 落盘成功后确认绑定,重跑幂等

    is_light = (engine_name == PDF_SANDBOX_LIGHT)
    log("DocumentConvert", "转换完成" if report.complete else "OCR 部分结果已保留", data={
        "paper_id": paper_id, "engine": report.engine,
        "engine_role": "轻量沙盒引擎(非主力)" if is_light else "主力/常规",
        "target": target, "pages_total": report.pages_total,
        "pages_failed": report.pages_failed, "complete": report.complete,
        "chars": len(raw_md_text),
        "rawmd": str(rawmd_path)})
    if report.pages_failed:                         # 有失败页 → 升 detail 留痕
        log("DocumentConvert", "转换有失败页", is_error=True, context={
            "step": "convert", "error": f"{len(report.pages_failed)} 页转换失败",
            "paper_id": paper_id, "pages_failed": str(report.pages_failed)})

    return ConvertResult(
        paper_id=paper_id,
        title=title,
        folder=folder,
        rawmd_path=rawmd_path,
        pdf_path=pdf_path,
        report=report,
        meta=raw.meta,
        original_path=original_path,
    )
