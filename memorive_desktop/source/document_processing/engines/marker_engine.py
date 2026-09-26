"""正式主力 marker-pdf 引擎。

依赖保持懒加载；未安装或 API 不兼容时 fail closed，绝不静默降级到轻量引擎。
"""
from __future__ import annotations

import hashlib
from importlib import metadata
import re
from typing import Any, Callable

from .base import ConvertEngine
from . import register
from ..errors import EngineNotAvailable
from ..types import ConvertReport, RawObject


_PAGE_MARKER_RE = re.compile(r"<!--\s*memorive:page:(\d+)\s*-->")


def _sha256_file(raw: RawObject) -> str:
    digest = hashlib.sha256()
    with raw.path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _marker_version() -> str | None:
    try:
        return metadata.version("marker-pdf")
    except metadata.PackageNotFoundError:
        return None


def _metadata_dict(rendered: object) -> dict[str, Any]:
    value = getattr(rendered, "metadata", None)
    return value if isinstance(value, dict) else {}


def _pages_total(markdown: str, rendered: object) -> int:
    markers = [int(value) for value in _PAGE_MARKER_RE.findall(markdown)]
    if markers:
        expected = list(range(1, max(markers) + 1))
        if markers != expected:
            raise RuntimeError(
                f"marker 页标记不连续: observed={markers}, expected={expected}"
            )
        return len(markers)

    metadata_value = _metadata_dict(rendered)
    for key in ("page_stats", "pages"):
        pages = metadata_value.get(key)
        if isinstance(pages, (list, tuple)) and pages:
            return len(pages)
    for key in ("page_count", "pages_total"):
        count = metadata_value.get(key)
        if isinstance(count, int) and count > 0:
            return count
    return 1


def _extract_markdown(
    rendered: object,
    text_from_rendered: Callable[[object], tuple[object, ...]],
) -> str:
    direct = getattr(rendered, "markdown", None)
    if isinstance(direct, str):
        markdown = direct
    else:
        extracted = text_from_rendered(rendered)
        markdown = extracted[0] if isinstance(extracted, tuple) and extracted else None
    if not isinstance(markdown, str) or not markdown.strip():
        raise RuntimeError("marker 返回空或非文本 Markdown；拒绝生成假成功报告。")
    return markdown.strip() + "\n"


@register
class MarkerEngine(ConvertEngine):
    name = "marker"

    def convert(self, raw: RawObject) -> tuple[str, ConvertReport]:
        try:
            from marker.converters.pdf import PdfConverter
            from marker.models import create_model_dict
            from marker.output import text_from_rendered
        except ImportError as e:
            raise EngineNotAvailable(
                "正式主力 marker 未安装(marker-pdf)。安装后本引擎生效;"
                "沙盒测试请显式用 pymupdf4llm(轻量、非主力)。绝不静默降级。") from e

        config = {
            "output_format": "markdown",
            "paginate_output": True,
            "page_separator": "\n\n<!-- memorive:page:{page} -->\n\n",
        }
        try:
            converter = PdfConverter(
                artifact_dict=create_model_dict(),
                config=config,
            )
            rendered = converter(str(raw.path))
            markdown = _extract_markdown(rendered, text_from_rendered)
            pages_total = _pages_total(markdown, rendered)
        except EngineNotAvailable:
            raise
        except Exception as exc:
            raise RuntimeError(f"marker 转换失败并已阻断: {exc}") from exc

        warnings: list[str] = []
        if not _PAGE_MARKER_RE.search(markdown):
            warnings.append(
                "marker 当前输出未暴露应用页标记；页总数来自 marker metadata，"
                "页面级 OCR rescue 在获得逐页边界前保持不可用。"
            )
        report = ConvertReport(
            engine=self.name,
            pages_total=pages_total,
            pages_failed=[],
            warnings=warnings,
            complete=True,
            engine_version=_marker_version(),
            source_sha256=_sha256_file(raw),
            output_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        )
        return markdown, report
