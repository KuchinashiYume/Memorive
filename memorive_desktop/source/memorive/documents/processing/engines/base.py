"""The base of the conversion engines: convert(raw) -> (raw_md_text, ConvertReport). An engine turns the
source file into RawMD text only; writing, naming and quality checks are document_convert_convert.py's."""
from __future__ import annotations

from memorive.documents.processing.types import ConvertReport, RawObject


class ConvertEngine:
    name: str = ""

    def convert(self, raw: RawObject) -> tuple[str, ConvertReport]:
        raise NotImplementedError
