"""已是 Markdown 的输入直通(读取原文,不改内容)。"""
from __future__ import annotations

from pathlib import Path

from .base import ConvertEngine
from . import register
from ..types import ConvertReport, RawObject


@register
class PassthroughEngine(ConvertEngine):
    name = "passthrough"

    def convert(self, raw: RawObject) -> tuple[str, ConvertReport]:
        text = Path(raw.path).read_text(encoding="utf-8")
        return text, ConvertReport(engine=self.name, pages_total=1, pages_failed=[],
                                   warnings=["已是 MD、直通不转换"])
