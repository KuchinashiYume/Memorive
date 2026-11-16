"""Regression tests for EVIDENCE_REVIEW structured transcription evidence."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from memorive.documents.card_states import DecisionError
from memorive.documents.card_review import grading, parser, transcription
from memorive.documents.card_review.problems import Problem


def _response(violation: dict) -> str:
    return json.dumps({"violations": [violation]}, ensure_ascii=False)


def _direct_violation(**overrides) -> dict:
    violation = {
        "rule": "R9",
        "card_quote": '"value": ">70"',
        "source_quote": "more than 70%",
        "evidence_relation": "direct_conflict",
    }
    violation.update(overrides)
    return violation


def _omission_violation(**overrides) -> dict:
    violation = {
        "rule": "R5",
        "card_quote": '"sample": "pilot reactor"',
        "source_quote": "under 35 °C after pretreatment",
        "evidence_relation": "explicit_omission",
    }
    violation.update(overrides)
    return violation


class RenderValueTest(unittest.TestCase):
    def test_prompt_preserves_equivalent_representations_and_unicode_context(self) -> None:
        prompt = transcription._prompt_template()
        self.assertIn("可逆、互补或单调等价表示", prompt)
        self.assertIn("不同对象、群体层级、阶段或统计口径", prompt)
        self.assertIn("人名、文内引用、变量、希腊字母", prompt)

    def test_key_data_greater_than_is_unambiguous_json(self) -> None:
        value = {
            "value": ">70",
            "unit": "%",
            "metric": "net profit increase",
            "sample": "other processes A through D",
            "stat": "stat_type: lower bound",
            "quote": "ignored by EVIDENCE_REVIEW subject rendering",
        }
        rendered = transcription._render_value(value)
        self.assertEqual(
            json.loads(rendered),
            {
                "value": ">70",
                "unit": "%",
                "metric": "net profit increase",
                "sample": "other processes A through D",
                "stat": "stat_type: lower bound",
            },
        )
        self.assertIn('">70"', rendered)
        self.assertNotIn("value=>70", rendered)

    def test_key_data_plus_minus_is_unambiguous_json(self) -> None:
        value = {
            "value": "65.0±1.0",
            "unit": "%",
            "metric": "COD removal",
            "sample": "pilot AnMBR",
            "stat": "mean±; meaning of ± not given",
        }
        rendered = transcription._render_value(value)
        decoded = json.loads(rendered)
        self.assertEqual(decoded["value"], "65.0±1.0")
        self.assertEqual(decoded["stat"], "mean±; meaning of ± not given")


class EvidenceParserTest(unittest.TestCase):
    def test_evidence_mode_accepts_strict_structured_violation(self) -> None:
        parsed = parser.parse_violations(
            _response(_direct_violation()),
            require_evidence=True,
        )
        self.assertEqual(
            parsed,
            [
                {
                    "rule": "R9",
                    "card_quote": '"value": ">70"',
                    "source_quote": "more than 70%",
                    "evidence_relation": "direct_conflict",
                    "detail": (
                        'R9·双侧逐字证据冲突；卡片摘录:"value": ">70"；'
                        "原文摘录:more than 70%"
                    ),
                }
            ],
        )

    def test_evidence_mode_accepts_explicit_omission(self) -> None:
        parsed = parser.parse_violations(
            _response(_omission_violation()),
            require_evidence=True,
        )
        self.assertEqual(parsed[0]["rule"], "R5")
        self.assertEqual(
            parsed[0]["evidence_relation"],
            "explicit_omission",
        )
        self.assertIn("关键内容遗漏", parsed[0]["detail"])

    def test_explicit_omission_requires_literal_source_quote(self) -> None:
        with self.assertRaises(DecisionError):
            parser.parse_violations(
                _response(_omission_violation(source_quote=None)),
                require_evidence=True,
            )

    def test_evidence_mode_rejects_legacy_ambiguous_quote(self) -> None:
        legacy = {
            "rule": "R9",
            "quote": "value=>70",
            "detail": "ambiguous legacy evidence",
        }
        with self.assertRaises(DecisionError):
            parser.parse_violations(_response(legacy), require_evidence=True)

    def test_evidence_mode_rejects_model_generated_detail(self) -> None:
        violation = _direct_violation(detail="free-form model detail")
        with self.assertRaises(DecisionError):
            parser.parse_violations(_response(violation), require_evidence=True)

    def test_legacy_mode_remains_backward_compatible(self) -> None:
        legacy = {
            "rule": "R9",
            "quote": "source says 77.8%",
            "detail": "card says 71%",
        }
        self.assertEqual(
            parser.parse_violations(_response(legacy)),
            [
                {
                    "rule": "R9",
                    "quote": "source says 77.8%",
                    "detail": "card says 71%",
                }
            ],
        )


class EvidenceValidationTest(unittest.TestCase):
    def _validate(self, violation: dict, value_text: str, chunk_text: str) -> dict:
        validator = getattr(
            transcription,
            "_validate_violation_evidence",
            None,
        )
        self.assertTrue(
            callable(validator),
            "_validate_violation_evidence must exist",
        )
        return validator(violation, value_text, chunk_text)

    def test_direct_conflict_requires_literal_card_quote(self) -> None:
        violation = _direct_violation(card_quote='"value": "70"')
        with self.assertRaises(DecisionError):
            self._validate(
                violation,
                '{"value": ">70", "unit": "%"}',
                "net profit increased by more than 70%",
            )

    def test_direct_conflict_requires_literal_source_quote(self) -> None:
        violation = _direct_violation(source_quote="exactly 70%")
        with self.assertRaises(DecisionError):
            self._validate(
                violation,
                '{"value": ">70", "unit": "%"}',
                "net profit increased by more than 70%",
            )

    def test_direct_conflict_preserves_real_r9(self) -> None:
        violation = _direct_violation(
            card_quote='"value": "71"',
            source_quote="77.8%",
        )
        normalized = self._validate(
            violation,
            '{"value": "71", "unit": "%"}',
            "the measured removal was 77.8%",
        )
        self.assertEqual(normalized["rule"], "R9")
        self.assertEqual(normalized["evidence_relation"], "direct_conflict")

    def test_direct_conflict_marks_source_internal_conflict(self) -> None:
        violation = _direct_violation(
            rule="R4",
            card_quote="Five continuously stirred 0.5 L bioreactors",
            source_quote="eight continuously stirred 0.5 L bioreactors",
        )
        normalized = self._validate(
            violation,
            "Five continuously stirred 0.5 L bioreactors",
            (
                "The abstract says Five continuously stirred 0.5 L "
                "bioreactors. The methods say eight continuously stirred "
                "0.5 L bioreactors."
            ),
        )

        self.assertIn("锚定原文内部冲突", normalized["detail"])

    def test_explicit_omission_preserves_two_sided_literal_evidence(self) -> None:
        violation = _omission_violation()
        normalized = self._validate(
            violation,
            '{"sample": "pilot reactor"}',
            "Removal reached 80% under 35 °C after pretreatment.",
        )
        self.assertEqual(normalized["rule"], "R5")
        self.assertEqual(
            normalized["evidence_relation"],
            "explicit_omission",
        )
        self.assertIn("关键内容遗漏", normalized["detail"])

    def test_missing_from_given_chunks_is_forced_to_anchor_uncertain(self) -> None:
        violation = _direct_violation(
            rule="R9",
            card_quote="环丙沙星",
            source_quote=None,
            evidence_relation="missing_from_given_chunks",
        )
        normalized = self._validate(
            violation,
            "研究对象包括环丙沙星和左氧氟沙星",
            "给定块只讨论土霉素",
        )
        self.assertEqual(normalized["rule"], "R8")
        self.assertTrue(normalized["detail"].startswith("疑锚点归位"))
        self.assertIsNone(normalized["source_quote"])

    def test_batch_item_prefix_preserves_anchor_uncertain_classification(self) -> None:
        problem = Problem(
            source="transcription",
            severity="error",
            kind="R8",
            location="by_field.method",
            field="method",
            detail="item_index=5；疑锚点归位·仅当前给定 chunks 未见",
        )
        self.assertTrue(grading._is_anchor_uncertain(problem))

        genuine = Problem(
            source="transcription",
            severity="error",
            kind="R8",
            location="by_field.method",
            field="method",
            detail="item_index=5；越界·真冲突，不能因后文含疑锚点归位而降级",
        )
        self.assertFalse(grading._is_anchor_uncertain(genuine))

    def test_missing_relation_rejects_fabricated_source_quote(self) -> None:
        violation = _direct_violation(
            card_quote="环丙沙星",
            source_quote="土霉素",
            evidence_relation="missing_from_given_chunks",
        )
        with self.assertRaises(DecisionError):
            self._validate(
                violation,
                "研究对象包括环丙沙星",
                "给定块只讨论土霉素",
            )

    def test_unique_cjk_pdf_whitespace_is_rehydrated_to_literal_source(self) -> None:
        chunk_text = (
            "发现 AnMBR 在有机 负荷 10 kg·m−3 ·d−1 的条件下能够稳定运行"
        )
        violation = _direct_violation(
            rule="R5",
            card_quote="小试AnMBR",
            source_quote="在有机负荷 10 kg·m−3 ·d−1 的条件下",
        )
        normalized = self._validate(
            violation,
            "样本/对照组: 小试AnMBR",
            chunk_text,
        )
        self.assertEqual(
            normalized["source_quote"],
            "在有机 负荷 10 kg·m−3 ·d−1 的条件下",
        )
        self.assertIn(normalized["source_quote"], chunk_text)
        self.assertIn(
            "原文摘录:在有机 负荷 10 kg·m−3 ·d−1 的条件下",
            normalized["detail"],
        )
        self.assertNotIn("原文摘录:在有机负荷 10", normalized["detail"])

    def test_cjk_pdf_whitespace_repair_rejects_semantic_character_change(self) -> None:
        violation = _direct_violation(
            rule="R5",
            card_quote="小试AnMBR",
            source_quote="在有机负荷 11 kg·m−3 ·d−1 的条件下",
        )
        with self.assertRaises(DecisionError):
            self._validate(
                violation,
                "样本/对照组: 小试AnMBR",
                "发现 AnMBR 在有机 负荷 10 kg·m−3 ·d−1 的条件下运行",
            )

    def test_cjk_pdf_whitespace_repair_rejects_ambiguous_matches(self) -> None:
        violation = _direct_violation(
            rule="R5",
            card_quote="小试AnMBR",
            source_quote="有机负荷 10 条件",
        )
        with self.assertRaises(DecisionError):
            self._validate(
                violation,
                "样本/对照组: 小试AnMBR",
                "甲段有机 负荷 10 条件；乙段有机 负荷 10 条件。",
            )

    def test_cjk_pdf_whitespace_repair_rejects_english_word_boundary_change(self) -> None:
        violation = _direct_violation(
            rule="R5",
            card_quote="process A",
            source_quote="process was notable to run",
        )
        with self.assertRaises(DecisionError):
            self._validate(
                violation,
                "sample: process A",
                "the process was not able to run under load",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
