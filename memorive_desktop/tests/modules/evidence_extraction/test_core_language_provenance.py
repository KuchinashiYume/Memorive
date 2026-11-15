"""Source-based core-language gate without character/name whitelists."""
from __future__ import annotations

import importlib
import unittest

from evidence_extraction.errors import CardParseError


class CoreLanguageProvenanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = importlib.import_module("evidence_extraction.distill")
        self.validator = getattr(
            self.module,
            "_validate_core_source_language",
            None,
        )
        self.assertIsNotNone(
            self.validator,
            "_validate_core_source_language must exist",
        )

    @staticmethod
    def _fields(key_results: str) -> dict:
        return {
            "research_question": "How does the process perform?",
            "research_object": ["Anaerobic membrane bioreactor"],
            "method": ["Long-term reactor operation"],
            "key_results": key_results,
            "author_conclusion": "The process may improve treatment.",
            "boundary_conditions": ["At 35 °C"],
        }

    def test_generated_cross_language_narrative_is_rejected(self) -> None:
        fields = self._fields(
            "The initial flux was 3.21 LMH. "
            "(原文另有一处写作 3.23 LMH，以 Table 1 的 3.21 LMH 为准)。"
        )
        records = [{"text": "The initial flux was 3.21 LMH in Table 1."}]
        with self.assertRaises(CardParseError):
            self.validator(fields, records, "Saquib2024")


    def test_long_foreign_text_copied_from_source_is_allowed(self) -> None:
        title = "厌氧膜生物反应器处理抗生素废水研究进展"
        fields = self._fields(f"The review cites {title} as prior work.")
        records = [{"text": f"Reference title: {title}. Main English discussion follows."}]
        self.validator(fields, records, "SourceBackedTitle")

    def test_chinese_source_with_english_terms_is_allowed(self) -> None:
        fields = {
            "research_question": "AnMBR 如何去除 COD？",
            "research_object": ["抗生素废水"],
            "method": ["采用 AnMBR 长期运行"],
            "key_results": "COD 去除率超过 95%。",
            "author_conclusion": "AnMBR 可降低出水风险。",
            "boundary_conditions": ["HRT 为 24 h"],
        }
        records = [{"text": "采用 AnMBR 处理抗生素废水，COD 去除率超过 95%。"}]
        self.validator(fields, records, "ChinesePaper")


if __name__ == "__main__":
    unittest.main(verbosity=2)
