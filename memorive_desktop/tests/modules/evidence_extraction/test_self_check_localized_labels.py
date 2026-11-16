"""Regression tests for bilingual key_data labels in the EVIDENCE_EXTRACTION self-check."""
from __future__ import annotations

import copy
import sys
import unittest
from importlib import import_module
from pathlib import Path

REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

distill = import_module("memorive.documents.card_distillation.distill")

CONTENT = {
    "research_question": "How does the process perform?",
    "research_object": "Anaerobic treatment",
    "method": "A controlled reactor study",
    "key_results": ["COD removal changed under load."],
    "author_conclusion": "The observed response depends on operating scope.",
    "boundary_conditions": ["The stated reactor conditions apply."],
}


def _entry(sample: str, stat: str) -> dict:
    return {
        "value": "78~85",
        "unit": "%",
        "metric": "COD 去除率",
        "sample": sample,
        "stat": stat,
        "quote": "COD 总去除率可达到 78%~85%",
        "chunk_id": "Zhang2024#c0010",
        "needs_review": True,
    }


def _card(entries: list[dict]) -> dict:
    return {
        **CONTENT,
        "key_data": entries,
        "source_anchor": {"paper_id": "Zhang2024", "by_field": {}},
    }


def _rules(result: dict) -> list[str]:
    return [flag["rule"] for flag in result["flags"]]


class SelfCheckLocalizedLabelsTest(unittest.TestCase):
    def test_english_card_lang_recognizes_real_chinese_scopes_and_stat(self) -> None:
        entries = [
            _entry(
                "样本/对照组: 两级升流式厌氧污泥床反应器处理稀释后的发酵类抗生素废水 | "
                "阶段·时段: 未给 | 子过程: 未给 | 对照: 未给",
                "统计类型: 范围 | n: 未给 | 显著性: 未给",
            ),
            _entry(
                "样本/对照组: UASB 处理土霉素生产废水中试 | "
                "阶段·时段: 有机负荷为 1.2±0.2 kg·m−3·d−1 时 | "
                "子过程: 未给 | 对照: 未给",
                "统计类型: 均值±标准差 | n: 未给 | 显著性: 未给",
            ),
        ]

        result = distill._self_check(_card(entries), "en")

        self.assertNotIn("R3", _rules(result))
        self.assertNotIn("R4", _rules(result))

    def test_chinese_card_lang_recognizes_english_labels(self) -> None:
        entry = _entry(
            "sample/control: pilot UASB | phase/time: inhibition stage | "
            "subprocess: methanogenesis | comparator: Not given",
            "stat_type: range | n: Not given | significance: Not given",
        )

        result = distill._self_check(_card([entry]), "zh")

        self.assertNotIn("R3", _rules(result))
        self.assertNotIn("R4", _rules(result))

    def test_all_four_scope_slots_missing_still_flags_r4(self) -> None:
        entry = _entry(
            "样本/对照组: 未给 | 阶段·时段: 未给 | 子过程: 未给 | 对照: 未给",
            "统计类型: 范围 | n: 未给 | 显著性: 未给",
        )

        result = distill._self_check(_card([entry]), "en")

        self.assertIn("R4", _rules(result))

    def test_missing_localized_stat_type_still_flags_r3(self) -> None:
        entry = _entry(
            "样本/对照组: 中试 UASB | 阶段·时段: 抑制阶段 | "
            "子过程: 未给 | 对照: 未给",
            "统计类型: 未给 | n: 未给 | 显著性: 未给",
        )

        result = distill._self_check(_card([entry]), "en")

        self.assertIn("R3", _rules(result))

    def test_self_check_does_not_mutate_card_or_change_blind_spots(self) -> None:
        card = _card([
            _entry(
                "样本/对照组: 中试 UASB | 阶段·时段: 抑制阶段 | "
                "子过程: 未给 | 对照: 未给",
                "统计类型: 范围 | n: 未给 | 显著性: 未给",
            )
        ])
        before = copy.deepcopy(card)

        result = distill._self_check(card, "en")

        self.assertEqual(card, before)
        self.assertEqual(
            result["not_machine_checkable"],
            ["R2", "R5", "R6", "R7"],
        )
        self.assertEqual(
            result["partial"],
            ["R1", "R3", "R4", "R8", "R9"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
