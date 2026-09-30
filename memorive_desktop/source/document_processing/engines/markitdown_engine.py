"""txt / office 等 → Markdown(markitdown,轻量)。"""
from __future__ import annotations

from .base import ConvertEngine
from . import register
from ..types import ConvertReport, RawObject


@register
class MarkitdownEngine(ConvertEngine):
    name = "markitdown"

    def convert(self, raw: RawObject) -> tuple[str, ConvertReport]:
        from markitdown import MarkItDown    # 懒加载
        result = MarkItDown(enable_plugins=False).convert(str(raw.path))
        text = result.text_content or ""
        return text, ConvertReport(engine=self.name, pages_total=1, pages_failed=[], warnings=[])
