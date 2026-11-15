"""Regression tests for unique CJK PDF word-space quote recovery."""
from __future__ import annotations

import sys
import unittest
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

distill = import_module("evidence_extraction.distill")
normalizer = import_module("evidence_extraction.normalize")

PAPER_ID = "Zhang2024"
CHUNK_ID = "Zhang2024#c0046"
MODEL_QUOTE = (
    "强化水解技术可消除土霉素对厌氧生化处理阶段的抑制，"
    "土霉素效价去除率大于 99.9%"
)
SOURCE_QUOTE = (
    "强化水解技术可消除土霉素对厌氧生化处理 阶段的抑制，"
    "土霉素效价去除率大于 99.9%"
)
CHUNK_TEXT = f"笔者团队前期开展研究。发现{SOURCE_QUOTE}，厌氧进水负荷随后提升。"
MODEL_UNIT_QUOTE = "AnMBR 在有机负荷 10 kg·m−3·d−1 的条件下能够稳定运行"
SOURCE_UNIT_QUOTE = "AnMBR 在有机 负荷 10 kg·m−3 ·d−1 的条件下能够稳定运行"
UNIT_CHUNK_TEXT = f"近期小试发现{SOURCE_UNIT_QUOTE}，COD 去除率保持稳定。"
MODEL_CARET_UNIT_QUOTE = (
    "在有机负荷为 1.2±0.2 kg·m^−3·d^−1 (COD) 时 "
    "COD 去除率仅为 51%±4%"
)
SOURCE_CARET_UNIT_QUOTE = (
    "在有机负荷为 1.2±0.2 kg·m^−3 ·d^−1 (COD) 时 "
    "COD 去除率仅为 51%±4%"
)
CARET_UNIT_CHUNK_TEXT = (
    f"YI 等开展中试研究，{SOURCE_CARET_UNIT_QUOTE}。"
)
MODEL_PERCENT_QUOTE = (
    "和没有预处理的厌氧系统相比，耐药基因降低了83%以上"
)
SOURCE_PERCENT_QUOTE = (
    "和没 有预处理的厌氧系统相比，耐药基因降低了 83% 以上"
)
PERCENT_CHUNK_TEXT = (
    f"工程实施后，{SOURCE_PERCENT_QUOTE}，从而实现协同控制。"
)
MODEL_MIXED_SCRIPT_QUOTE = (
    "发现AnMBR在有机负荷10 kg·m^−3 ·d^−1 的条件下能够稳定运行，"
    "COD去除率可达到65.0%±1.0%"
)
SOURCE_MIXED_SCRIPT_QUOTE = (
    "发现 AnMBR 在有机 负荷 10 kg·m^−3 ·d^−1 的条件下能够稳定运行，"
    "COD 去除率可达到 65.0%±1.0%"
)
MIXED_SCRIPT_CHUNK_TEXT = (
    f"近期小试探究发现，{SOURCE_MIXED_SCRIPT_QUOTE}，"
    "为工程应用提供了基础。"
)
MODEL_UNICODE_PROVENANCE_QUOTE = (
    "Müller等引用早期研究，并以变量α表示传质系数"
)
SOURCE_UNICODE_PROVENANCE_QUOTE = (
    "Müller 等引用早期研究，并以变量 α 表示传质系数"
)
UNICODE_PROVENANCE_CHUNK_TEXT = (
    f"在方法回顾中，{SOURCE_UNICODE_PROVENANCE_QUOTE}。"
)


class CjkQuoteRecoveryTest(unittest.TestCase):
    def _recover(self, quote: str, chunk: str) -> str | None:
        recover = getattr(normalizer, "recover_unique_cjk_pdf_quote", None)
        self.assertTrue(callable(recover), "recover_unique_cjk_pdf_quote must exist")
        return recover(quote, chunk)

    def _recover_unit(self, quote: str, chunk: str) -> str | None:
        recover = getattr(
            normalizer,
            "recover_unique_scientific_unit_pdf_quote",
            None,
        )
        self.assertTrue(
            callable(recover),
            "recover_unique_scientific_unit_pdf_quote must exist",
        )
        return recover(quote, chunk)

    def test_actual_zhang_quote_rehydrates_to_literal_source(self) -> None:
        recovered = self._recover(MODEL_QUOTE, CHUNK_TEXT)
        self.assertEqual(recovered, SOURCE_QUOTE)
        self.assertIn(recovered, CHUNK_TEXT)

    def test_actual_zhang_cjk_percentage_space_rehydrates_source(self) -> None:
        recovered = self._recover(MODEL_PERCENT_QUOTE, PERCENT_CHUNK_TEXT)
        self.assertEqual(recovered, SOURCE_PERCENT_QUOTE)
        self.assertIn(recovered, PERCENT_CHUNK_TEXT)

    def test_actual_zhang_mixed_script_space_rehydrates_source(self) -> None:
        recovered = self._recover(
            MODEL_MIXED_SCRIPT_QUOTE,
            MIXED_SCRIPT_CHUNK_TEXT,
        )
        self.assertEqual(recovered, SOURCE_MIXED_SCRIPT_QUOTE)
        self.assertIn(recovered, MIXED_SCRIPT_CHUNK_TEXT)

    def test_unicode_author_and_variable_characters_are_preserved(self) -> None:
        recovered = self._recover(
            MODEL_UNICODE_PROVENANCE_QUOTE,
            UNICODE_PROVENANCE_CHUNK_TEXT,
        )
        self.assertEqual(recovered, SOURCE_UNICODE_PROVENANCE_QUOTE)
        self.assertIn("ü", recovered)
        self.assertIn("α", recovered)

    def test_mixed_script_recovery_rejects_unsafe_changes(self) -> None:
        cases = (
            (
                "semantic digit change",
                MODEL_MIXED_SCRIPT_QUOTE.replace("65.0", "66.0"),
                MIXED_SCRIPT_CHUNK_TEXT,
            ),
            (
                "unit symbol change",
                MODEL_MIXED_SCRIPT_QUOTE.replace("d^−1", "h^−1"),
                MIXED_SCRIPT_CHUNK_TEXT,
            ),
            (
                "latin token change",
                MODEL_MIXED_SCRIPT_QUOTE.replace("AnMBR", "AnMPR"),
                MIXED_SCRIPT_CHUNK_TEXT,
            ),
            (
                "unicode author change",
                MODEL_UNICODE_PROVENANCE_QUOTE.replace("Müller", "Muller"),
                UNICODE_PROVENANCE_CHUNK_TEXT,
            ),
            (
                "unicode variable change",
                MODEL_UNICODE_PROVENANCE_QUOTE.replace("α", "β"),
                UNICODE_PROVENANCE_CHUNK_TEXT,
            ),
            (
                "latin word boundary",
                "方法invitro分析能够保持稳定",
                "方法 in vitro 分析能够保持稳定",
            ),
            (
                "pure latin context",
                "AnMBRwas stable throughout testing",
                "AnMBR was stable throughout testing",
            ),
            (
                "ambiguous repeated match",
                MODEL_MIXED_SCRIPT_QUOTE,
                f"{SOURCE_MIXED_SCRIPT_QUOTE}。{SOURCE_MIXED_SCRIPT_QUOTE}",
            ),
            (
                "short mixed-script quote",
                "用AnMBR",
                "用 AnMBR",
            ),
        )
        for label, quote, chunk in cases:
            with self.subTest(label=label):
                self.assertIsNone(self._recover(quote, chunk))

    def test_cjk_percentage_recovery_rejects_unsafe_changes(self) -> None:
        cases = (
            (
                "semantic digit change",
                MODEL_PERCENT_QUOTE.replace("83", "84"),
                PERCENT_CHUNK_TEXT,
            ),
            (
                "ambiguous repeated match",
                MODEL_PERCENT_QUOTE,
                f"{SOURCE_PERCENT_QUOTE}。{SOURCE_PERCENT_QUOTE}",
            ),
            (
                "english context",
                "removal reached83%above baseline",
                "removal reached 83% above baseline",
            ),
            (
                "symbol change",
                MODEL_PERCENT_QUOTE.replace("%", "‰"),
                PERCENT_CHUNK_TEXT,
            ),
        )
        for label, quote, chunk in cases:
            with self.subTest(label=label):
                self.assertIsNone(self._recover(quote, chunk))

    def test_recovery_rejects_unsafe_or_ambiguous_changes(self) -> None:
        cases = (
            ("semantic digit change", MODEL_QUOTE.replace("99.9", "99.8"), CHUNK_TEXT),
            ("ambiguous repeated match", MODEL_QUOTE, f"{SOURCE_QUOTE}。{SOURCE_QUOTE}"),
            ("english word boundary", "process was notable", "process was not able"),
            ("short CJK quote", "处理阶段", "处理 阶段"),
        )
        for label, quote, chunk in cases:
            with self.subTest(label=label):
                self.assertIsNone(self._recover(quote, chunk))

    def test_resolve_quote_recovers_before_model_reanchor(self) -> None:
        events = []

        def capture_log(module, event, **kwargs):
            events.append((module, event, kwargs))

        with (
            patch.object(distill, "log", side_effect=capture_log),
            patch.object(distill, "log_error"),
            patch.object(distill, "_reanchor_quote", return_value=None) as reanchor,
        ):
            resolved = distill._resolve_quote(
                MODEL_QUOTE,
                CHUNK_ID,
                {CHUNK_ID: CHUNK_TEXT},
                PAPER_ID,
                path="key_data[7].quote",
                datum_desc="土霉素效价去除率 >99.9%",
                locator={"block": "key_data", "entry_index": 7, "entry_no": 8},
                gate=True,
                soft=False,
            )

        self.assertEqual(resolved, SOURCE_QUOTE)
        self.assertIn(resolved, CHUNK_TEXT)
        reanchor.assert_not_called()
        recovery_events = [event for _, event, _ in events if event == "quote_cjk_whitespace_recovered"]
        self.assertEqual(recovery_events, ["quote_cjk_whitespace_recovered"])

    def test_resolve_quote_recovers_cjk_percentage_before_model(self) -> None:
        events = []

        def capture_log(module, event, **kwargs):
            events.append((module, event, kwargs))

        with (
            patch.object(distill, "log", side_effect=capture_log),
            patch.object(distill, "log_error"),
            patch.object(
                distill,
                "_reanchor_quote",
                side_effect=AssertionError("model reanchor must not run"),
            ) as reanchor,
        ):
            resolved = distill._resolve_quote(
                MODEL_PERCENT_QUOTE,
                CHUNK_ID,
                {CHUNK_ID: PERCENT_CHUNK_TEXT},
                PAPER_ID,
                path="key_data[2].quote",
                datum_desc="耐药基因降低率 >83%",
                locator={"block": "key_data", "entry_index": 2, "entry_no": 3},
                gate=True,
                soft=False,
            )

        self.assertEqual(resolved, SOURCE_PERCENT_QUOTE)
        self.assertIn(resolved, PERCENT_CHUNK_TEXT)
        reanchor.assert_not_called()
        recovery_events = [
            event
            for _, event, _ in events
            if event == "quote_cjk_whitespace_recovered"
        ]
        self.assertEqual(
            recovery_events,
            ["quote_cjk_whitespace_recovered"],
        )

    def test_resolve_quote_recovers_mixed_script_before_model(self) -> None:
        events = []

        def capture_log(module, event, **kwargs):
            events.append((module, event, kwargs))

        with (
            patch.object(distill, "log", side_effect=capture_log),
            patch.object(distill, "log_error"),
            patch.object(
                distill,
                "_reanchor_quote",
                side_effect=AssertionError("model reanchor must not run"),
            ) as reanchor,
        ):
            resolved = distill._resolve_quote(
                MODEL_MIXED_SCRIPT_QUOTE,
                CHUNK_ID,
                {CHUNK_ID: MIXED_SCRIPT_CHUNK_TEXT},
                PAPER_ID,
                path="key_data[4].quote",
                datum_desc="COD 去除率 65.0%±1.0%",
                locator={"block": "key_data", "entry_index": 4, "entry_no": 5},
                gate=True,
                soft=False,
            )

        self.assertEqual(resolved, SOURCE_MIXED_SCRIPT_QUOTE)
        self.assertIn(resolved, MIXED_SCRIPT_CHUNK_TEXT)
        reanchor.assert_not_called()
        recovery_events = [
            event
            for _, event, _ in events
            if event == "quote_cjk_whitespace_recovered"
        ]
        self.assertEqual(
            recovery_events,
            ["quote_cjk_whitespace_recovered"],
        )

    def test_actual_zhang_scientific_unit_space_rehydrates_literal_source(self) -> None:
        recovered = self._recover_unit(MODEL_UNIT_QUOTE, UNIT_CHUNK_TEXT)
        self.assertEqual(recovered, SOURCE_UNIT_QUOTE)
        self.assertIn(recovered, UNIT_CHUNK_TEXT)

    def test_actual_zhang_caret_unit_space_rehydrates_literal_source(self) -> None:
        recovered = self._recover_unit(
            MODEL_CARET_UNIT_QUOTE,
            CARET_UNIT_CHUNK_TEXT,
        )
        self.assertEqual(recovered, SOURCE_CARET_UNIT_QUOTE)
        self.assertIn(recovered, CARET_UNIT_CHUNK_TEXT)

    def test_caret_unit_recovery_rejects_character_or_scope_changes(self) -> None:
        cases = (
            (
                "caret removed",
                MODEL_CARET_UNIT_QUOTE.replace("m^−3", "m−3"),
                CARET_UNIT_CHUNK_TEXT,
            ),
            (
                "exponent changed",
                MODEL_CARET_UNIT_QUOTE.replace("m^−3", "m^−2"),
                CARET_UNIT_CHUNK_TEXT,
            ),
            (
                "unit changed",
                MODEL_CARET_UNIT_QUOTE.replace("d^−1", "h^−1"),
                CARET_UNIT_CHUNK_TEXT,
            ),
            (
                "ambiguous repeated match",
                MODEL_CARET_UNIT_QUOTE,
                f"{SOURCE_CARET_UNIT_QUOTE}。{SOURCE_CARET_UNIT_QUOTE}",
            ),
        )
        for label, quote, chunk in cases:
            with self.subTest(label=label):
                self.assertIsNone(self._recover_unit(quote, chunk))

    def test_scientific_unit_recovery_rejects_semantic_or_broad_changes(self) -> None:
        cases = (
            (
                "semantic digit change",
                MODEL_UNIT_QUOTE.replace(" 10 ", " 11 "),
                UNIT_CHUNK_TEXT,
            ),
            (
                "unit symbol change",
                MODEL_UNIT_QUOTE.replace("d−1", "h−1"),
                UNIT_CHUNK_TEXT,
            ),
            (
                "ordinary middle-dot separator",
                "实验分组 A·B 用于普通项目符号说明",
                "实验分组 A · B 用于普通项目符号说明",
            ),
            (
                "ambiguous repeated match",
                MODEL_UNIT_QUOTE,
                f"{SOURCE_UNIT_QUOTE}。{SOURCE_UNIT_QUOTE}",
            ),
            (
                "english word boundary",
                "process was notable at 10 kg·m−3·d−1",
                "process was not able at 10 kg·m−3 ·d−1",
            ),
        )
        for label, quote, chunk in cases:
            with self.subTest(label=label):
                self.assertIsNone(self._recover_unit(quote, chunk))

    def test_resolve_quote_recovers_unit_space_before_model_reanchor(self) -> None:
        events = []

        def capture_log(module, event, **kwargs):
            events.append((module, event, kwargs))

        with (
            patch.object(distill, "log", side_effect=capture_log),
            patch.object(distill, "log_error"),
            patch.object(distill, "_reanchor_quote", return_value=None) as reanchor,
        ):
            resolved = distill._resolve_quote(
                MODEL_UNIT_QUOTE,
                CHUNK_ID,
                {CHUNK_ID: UNIT_CHUNK_TEXT},
                PAPER_ID,
                path="key_data[8].quote",
                datum_desc="有机负荷 ≥10 kg·m−3·d−1",
                locator={"block": "key_data", "entry_index": 8, "entry_no": 9},
                gate=True,
                soft=False,
            )

        self.assertEqual(resolved, SOURCE_UNIT_QUOTE)
        self.assertIn(resolved, UNIT_CHUNK_TEXT)
        reanchor.assert_not_called()
        recovery_events = [
            event for _, event, _ in events
            if event == "quote_unit_whitespace_recovered"
        ]
        self.assertEqual(
            recovery_events,
            ["quote_unit_whitespace_recovered"],
        )

    def test_resolve_quote_recovers_caret_unit_before_model_reanchor(self) -> None:
        events = []

        def capture_log(module, event, **kwargs):
            events.append((module, event, kwargs))

        with (
            patch.object(distill, "log", side_effect=capture_log),
            patch.object(distill, "log_error"),
            patch.object(
                distill,
                "_reanchor_quote",
                side_effect=AssertionError("model reanchor must not run"),
            ) as reanchor,
        ):
            resolved = distill._resolve_quote(
                MODEL_CARET_UNIT_QUOTE,
                CHUNK_ID,
                {CHUNK_ID: CARET_UNIT_CHUNK_TEXT},
                PAPER_ID,
                path="key_data[0].quote",
                datum_desc="COD 去除率 51±4%",
                locator={"block": "key_data", "entry_index": 0, "entry_no": 1},
                gate=True,
                soft=False,
            )

        self.assertEqual(resolved, SOURCE_CARET_UNIT_QUOTE)
        self.assertIn(resolved, CARET_UNIT_CHUNK_TEXT)
        reanchor.assert_not_called()
        recovery_events = [
            event for _, event, _ in events
            if event == "quote_unit_whitespace_recovered"
        ]
        self.assertEqual(
            recovery_events,
            ["quote_unit_whitespace_recovered"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
