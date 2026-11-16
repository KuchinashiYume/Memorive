"""Markdown input passes through as it is."""
from __future__ import annotations

from pathlib import Path

from memorive.documents.processing.engines.base import ConvertEngine
from memorive.documents.processing.engines import register
from memorive.documents.processing.types import ConvertReport, RawObject


@register
class PassthroughEngine(ConvertEngine):
    name = "passthrough"

    def convert(self, raw: RawObject) -> tuple[str, ConvertReport]:
        text = Path(raw.path).read_text(encoding="utf-8")
        return text, ConvertReport(engine=self.name, pages_total=1, pages_failed=[],
                                   warnings=["已是 MD、直通不转换"])
