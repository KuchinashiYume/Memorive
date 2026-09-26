"""M1b PDF 页面级 OCR 救援与双路确定性校核。

数字版主引擎仍是第一路；本模块只处理无文本、低文本、确定性乱码页或显式抽检页。
所有 OCR 调用逐页进行，受托数据在读取源文件、渲染和 base64 编码之前挡下。
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Callable, Iterable

from m11_log import log, log_error
from m9_gateway import ocr
from m9_gateway.errors import OcrOwnershipBlocked

from .config import (
    OCR_PAGE_RENDER_DPI,
    SCAN_SUSPECT_MIN_CHARS_PER_PAGE,
    TEXT_REPLACEMENT_FAIL_RATIO,
)
from .types import ConvertReport, RawObject


_PAGE_BLOCK_RE = re.compile(
    r"<!--\s*pros:page:(\d+)\s*-->(.*?)(?=<!--\s*pros:page:\d+\s*-->|$)",
    re.DOTALL,
)
_ESSENTIAL_TOKEN_RE = re.compile(
    r"(?<![\w.])[-+]?\d+(?:\.\d+)*(?:%)?",
    re.UNICODE,
)
_ALLOWED_TEXT_CONTROLS = frozenset({"\t", "\n", "\r"})
_OCR_ENGINE = "deepseek-ai/DeepSeek-OCR"


def _is_forbidden_control(character: str) -> bool:
    return (
        character not in _ALLOWED_TEXT_CONTROLS
        and unicodedata.category(character) == "Cc"
    )


def _compact(text: str) -> str:
    return "".join((text or "").split())


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _page_risk_reasons(text: str) -> list[str]:
    compact = _compact(text)
    reasons: list[str] = []
    if not compact:
        return ["no_text"]
    if len(compact) < SCAN_SUSPECT_MIN_CHARS_PER_PAGE:
        reasons.append("low_text")
    if compact.count("\ufffd") / len(compact) >= TEXT_REPLACEMENT_FAIL_RATIO:
        reasons.append("replacement_corruption")
    control_count = sum(_is_forbidden_control(char) for char in compact)
    if control_count / len(compact) >= TEXT_REPLACEMENT_FAIL_RATIO:
        reasons.append("control_corruption")
    return reasons


def _primary_is_unusable(reasons: Iterable[str], *, forced: bool) -> bool:
    reason_set = set(reasons)
    if reason_set & {"no_text", "replacement_corruption", "control_corruption", "font_unicode_conflict",
                     "unverified_hidden_ocr", "unmapped_source_glyph", "blank_glyph_collision"}:
        return True
    return "low_text" in reason_set and not forced


def _ocr_text_error(text: object) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return "empty_text"
    compact = _compact(text)
    if len(compact) < 8:
        return "too_short"
    if compact.count("\ufffd") / len(compact) >= TEXT_REPLACEMENT_FAIL_RATIO:
        return "replacement_corruption"
    control_count = sum(_is_forbidden_control(char) for char in compact)
    if control_count / len(compact) >= TEXT_REPLACEMENT_FAIL_RATIO:
        return "control_corruption"
    return None


def _essential_tokens(text: str) -> list[str]:
    return sorted(set(_ESSENTIAL_TOKEN_RE.findall(text or "")))


def _comparison(primary: str, ocr_text: str) -> tuple[str, list[str], list[str]]:
    primary_normalized = " ".join(primary.split()).casefold()
    ocr_normalized = " ".join(ocr_text.split()).casefold()
    primary_tokens = set(_essential_tokens(primary))
    ocr_tokens = set(_essential_tokens(ocr_text))
    if primary_normalized == ocr_normalized:
        label = "consistent"
    elif primary_tokens == ocr_tokens:
        label = "token_consistent"
    else:
        label = "conflict"
    return (
        label,
        sorted(primary_tokens - ocr_tokens),
        sorted(ocr_tokens - primary_tokens),
    )


def _parse_pages(raw_md_text: str, pages_total: int) -> dict[int, str]:
    pages: dict[int, str] = {}
    for match in _PAGE_BLOCK_RE.finditer(raw_md_text or ""):
        number = int(match.group(1))
        if number in pages:
            raise ValueError(f"重复页标记: {number}")
        pages[number] = match.group(2).strip("\n")
    expected = set(range(1, pages_total + 1))
    if set(pages) != expected:
        missing = sorted(expected - set(pages))
        unexpected = sorted(set(pages) - expected)
        raise ValueError(
            f"页标记与 pages_total 不一致: missing={missing}, unexpected={unexpected}"
        )
    return pages


def _rebuild_pages(pages: dict[int, str], pages_total: int) -> str:
    return "".join(
        f"<!-- pros:page:{number} -->\n\n{pages[number].strip()}\n\n"
        for number in range(1, pages_total + 1)
    )


class _PdfRenderer:
    """一次打开 PDF，按需渲染候选页，避免每页重复打开整篇。"""

    def __init__(self, path: Path):
        import fitz

        self._document = fitz.open(path)

    def __call__(self, path: Path, page_number: int) -> bytes:
        del path
        if page_number < 1 or page_number > len(self._document):
            raise IndexError(f"PDF 页码越界: {page_number}/{len(self._document)}")
        page = self._document.load_page(page_number - 1)
        pixmap = page.get_pixmap(dpi=OCR_PAGE_RENDER_DPI, alpha=False)
        return pixmap.tobytes("png")

    def close(self) -> None:
        self._document.close()


def rescue_pdf_pages(
    raw: RawObject,
    raw_md_text: str,
    report: ConvertReport,
    *,
    forced_pages: Iterable[int] | None = None,
    render_page: Callable[[Path, int], bytes] | None = None,
    ocr_client: Callable[..., dict] | None = None,
) -> tuple[str, ConvertReport]:
    """救援候选页并返回重建 Markdown 与不可变报告副本。

    可用数字文本在冲突时始终保留；只有主路不可用且 OCR 合法时才替换。
    OCR 单页失败不会丢弃已完成页，但主路也不可用时报告 complete=False。
    """
    pages = _parse_pages(raw_md_text, report.pages_total)
    forced = set(forced_pages or ())
    invalid_forced = sorted(
        page for page in forced if page < 1 or page > report.pages_total
    )
    if invalid_forced:
        raise ValueError(f"forced_pages 越界: {invalid_forced}")

    candidates: list[tuple[int, list[str], bool]] = []
    source_risks = {}
    for row in report.page_details:
        if row.get('requires_page_ocr') is True:
            source_risks.setdefault(row['page_number'], []).append(row.get('risk_reason', 'font_unicode_conflict'))
    for page_number in range(1, report.pages_total + 1):
        reasons = _page_risk_reasons(pages[page_number])
        reasons.extend(source_risks.get(page_number, ()))
        is_forced = page_number in forced
        if reasons or is_forced:
            candidates.append((page_number, reasons, is_forced))
    if not candidates:
        return raw_md_text, report

    # 红线：候选已由主引擎内存文本判定，但在此之前未打开/读取源 PDF。
    if raw.meta.data_ownership != "self":
        log_error("M1b", "OCR 数据归属闸门触发", context={
            "step": "ocr_page_rescue/ownership_gate",
            "error": (
                f"data_ownership={raw.meta.data_ownership!r};"
                "已在源文件读取、页面渲染和远程调用前挡下"
            ),
        })
        raise OcrOwnershipBlocked(
            "受托 PDF 禁止远程 OCR；已在读取源文件、渲染和编码前挡下。"
        )

    source_sha256 = _sha256_file(raw.path)
    actual_renderer = render_page
    renderer_owner: _PdfRenderer | None = None
    if actual_renderer is None:
        renderer_owner = _PdfRenderer(raw.path)
        actual_renderer = renderer_owner
    actual_ocr = ocr_client or ocr

    details = list(report.page_details)
    warnings = list(report.warnings)
    failed = set(report.pages_failed)
    unresolved = False
    attempted = False
    try:
        for page_number, reasons, is_forced in candidates:
            attempted = True
            primary = pages[page_number]
            primary_unusable = _primary_is_unusable(
                reasons,
                forced=is_forced,
            )
            detail = {
                "page_number": page_number,
                "ocr_reasons": reasons + (["forced_dual_check"] if is_forced else []),
                "source_sha256": source_sha256,
                "primary_sha256": _sha256_text(primary),
                "selected_engine": report.engine,
            }
            try:
                image_bytes = actual_renderer(raw.path, page_number)
                response = actual_ocr(
                    "ocr",
                    image_bytes,
                    mime_type="image/png",
                    page_number=page_number,
                    source_sha256=source_sha256,
                    data_ownership=raw.meta.data_ownership,
                )
                ocr_text = response["text"].strip()
                ocr_error = _ocr_text_error(ocr_text)
                if ocr_error is not None:
                    raise ValueError(f"OCR text invalid:{ocr_error}")
                comparison, primary_only, ocr_only = _comparison(
                    primary,
                    ocr_text,
                )
                detail.update({
                    "ocr_text_sha256": _sha256_text(ocr_text),
                    "ocr_response_sha256": response["response_sha256"],
                    "ocr_request_id": response.get("request_id"),
                    "comparison": comparison,
                    "primary_only_tokens": primary_only,
                    "ocr_only_tokens": ocr_only,
                })
                if primary_unusable:
                    pages[page_number] = ocr_text
                    failed.discard(page_number)
                    detail["selected_engine"] = response.get('model') or _OCR_ENGINE
                    detail["selection_reason"] = "primary_unusable_ocr_valid"
                else:
                    detail["selection_reason"] = "usable_primary_retained"
                log("M1b", "OCR 页面校核完成", data={
                    "page_number": page_number,
                    "comparison": comparison,
                    "selected_engine": detail["selected_engine"],
                    "selection_reason": detail["selection_reason"],
                    "source_sha256": source_sha256,
                    "primary_sha256": detail["primary_sha256"],
                    "ocr_text_sha256": detail["ocr_text_sha256"],
                    "ocr_response_sha256": detail["ocr_response_sha256"],
                })
            except Exception as exc:
                detail.update({
                    "comparison": "unavailable",
                    "selection_reason": "ocr_failed",
                    "error_type": type(exc).__name__,
                })
                warnings.append(
                    f"ocr_page_failed:{page_number}:{type(exc).__name__}"
                )
                if primary_unusable:
                    failed.add(page_number)
                    unresolved = True
                log_error("M1b", "OCR 页面失败，已保留其他页面", context={
                    "step": f"ocr_page_rescue/page_{page_number}",
                    "error": (
                        f"{type(exc).__name__};正文不入日志；"
                        f"primary_unusable={primary_unusable}"
                    ),
                    "page_number": str(page_number),
                    "source_sha256": source_sha256,
                })
            details.append(detail)
    finally:
        if renderer_owner is not None:
            renderer_owner.close()

    engine = report.engine
    if attempted and "page_ocr" not in engine:
        engine = f"{engine}+page_ocr"
    updated_report = replace(
        report,
        engine=engine,
        pages_failed=sorted(failed),
        warnings=warnings,
        complete=report.complete and not unresolved and not failed,
        page_details=details,
    )
    return _rebuild_pages(pages, report.pages_total), updated_report
