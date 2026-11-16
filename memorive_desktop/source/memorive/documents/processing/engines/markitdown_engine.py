"""Text and Office files to Markdown (markitdown, light)."""
from __future__ import annotations

from memorive.documents.processing.engines.base import ConvertEngine
from memorive.documents.processing.engines import register
from memorive.documents.processing.types import ConvertReport, RawObject


@register
class MarkitdownEngine(ConvertEngine):
    name = "markitdown"

    def convert(self, raw: RawObject) -> tuple[str, ConvertReport]:
        from markitdown import MarkItDown    # loaded when used
        result = MarkItDown(enable_plugins=False).convert(str(raw.path))
        text = result.text_content or ""
        return text, ConvertReport(engine=self.name, pages_total=1, pages_failed=[], warnings=[])
