"""Memorive DOCUMENT-PROCESSING/DOCUMENT_PROCESSING · DOCUMENT_PROCESSING 转换引擎基类。约定:convert(raw) -> (raw_md_text, ConvertReport)。
只负责「原始文件 → Raw MD 文本」,不落盘、不命名、不判质量(那些在 document_convert_convert)。"""
from __future__ import annotations

from ..types import ConvertReport, RawObject


class ConvertEngine:
    name: str = ""

    def convert(self, raw: RawObject) -> tuple[str, ConvertReport]:
        raise NotImplementedError
