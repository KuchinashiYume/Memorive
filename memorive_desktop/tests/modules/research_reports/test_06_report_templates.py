"""The layout of daily, weekly and monthly reports (memorive/research/reports/renderer.py) and their
records (periodic.render): one record per processed paper or refined conversation.

A report reads from the top: title and period, a line of counts, the model summary (if any), what
needs attention, the progress of the period, the counts, and an appendix with every record, its
completeness, what is not assessed and where the report comes from. All records are made up.
"""
from __future__ import annotations

import re
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from memorive.research.reports import renderer
from memorive.research.reports.renderer import render_markdown, render_markdown_with_narrative

TITLES = {
    "daily": ["新增", "修改", "待审", "失败"],
    "weekly": ["研究进展", "明确标记的重要变化", "未验证项", "明确来源的潜在张力", "风险与待办"],
    "monthly": ["知识库结构变化", "明确记录的研究方向变化", "长期待办", "风险与未评估维度"],
}
WINDOWS = {
    "daily": ("2026-08-08T00:00:00+08:00", "2026-08-09T00:00:00+08:00"),
    "weekly": ("2026-08-03T00:00:00+08:00", "2026-08-10T00:00:00+08:00"),
    "monthly": ("2026-08-01T00:00:00+08:00", "2026-09-01T00:00:00+08:00"),
}
# What only the appendix may show: internal names, ids and hashes.
INTERNAL = re.compile(r"status_axis|artifact_registry|RESULT_AVAILABLE|digest_id|RE-[0-9A-F]|evidence-|sha256")


def _event(event_id: str, text: str, hour: int = 9) -> dict:
    return {
        "event_id": event_id,
        "source_event_ref": event_id,
        "occurred_at": f"2026-08-08T{hour:02d}:00:00+08:00",
        "observed_at": f"2026-08-08T{hour:02d}:01:00+08:00",
        "text": text,
        "status_axis": "artifact_registry",
        "status": "RESULT_AVAILABLE",
        "verification_status": "not_applicable",
        "artifact_or_ledger_refs": [f"memorive://artifact/{event_id.lower()}"],
        "source_record_ref": f"memorive://artifact/{event_id.lower()}",
        "evidence_refs": [f"evidence-{event_id}"],
    }


def _digest(kind: str) -> dict:
    sections = [{"section_id": f"S{i:02d}", "title": title, "items": []} for i, title in enumerate(TITLES[kind], 1)]
    paper = _event("RE-0A1", "《Sleep survey.pdf》文献处理完成，12 个成果待人工复核")
    sections[0]["items"].append(paper)
    if kind == "weekly":
        sections[1]["items"].append(deepcopy(paper))
        sections[2]["items"].append(_event("RE-0B2", "《Diet trial.pdf》数值待核对", 10))
        sections[3]["items"].append(_event("RE-0C3", "两篇文献对样本量的说法不一致", 11))
        sections[4]["items"].append(_event("RE-0D4", "《Old cohort.pdf》处理失败", 12))
    elif kind == "monthly":
        sections[1]["items"].append(_event("RE-0B2", "研究方向改为睡眠与记忆", 10))
        sections[2]["items"].append(_event("RE-0C3", "复核三月的旧卡片", 11))
        sections[3]["items"].append(_event("RE-0D4", "两个来源无法读取", 12))
    else:
        sections[2]["items"].append(_event("RE-0B2", "《Diet trial.pdf》数值待核对", 10))
        sections[3]["items"].append(_event("RE-0C3", "《Old cohort.pdf》处理失败", 11))
    start, end = WINDOWS[kind]
    return {
        "object_type": "ChangeDigest", "schema_version": "1.0", "domain_contract_version": "1.1",
        "digest_id": f"digest-fixture-{kind}", "revision": 1, "digest_kind": kind,
        "report_timezone": "Asia/Shanghai", "window_start": start, "window_end": end,
        "cutoff_at": end, "input_manifest_sha256": "A" * 64, "generator_version": "fixture-generator",
        "source_snapshot_refs": ["fixture/snapshot.json"], "report_status": "complete",
        "authority_status": "shadow_non_authoritative", "sections": sections, "late_arrivals": [],
        "conflicts": [], "unparseable_events": [],
        "not_assessed_dimensions": [{"dimension": "semantic_priority_quality", "status": "NOT_ASSESSED",
                                     "reason_code": "NO_SEMANTIC_PRIORITY_AUTHORITY_IN_DataContracts_INPUT"}],
        "supersedes_digest_id": None, "supersedes_binding": None, "generated_at": end,
        "external_calls": 0, "external_tokens": 0, "cost_cny": 0, "production_mutations": 0,
        "production_activation": False,
    }


def _before_appendix(text: str) -> str:
    return text.split("## 附录", 1)[0]


class ReportLayoutTests(unittest.TestCase):
    def test_a_daily_report_reads_from_the_top(self) -> None:
        text = render_markdown(_digest("daily"))
        order = ["# 日报 · 2026-08-08", "这一天的记录：新增 1 条、待审 1 条、失败 1 条。", "## 需要处理",
                 "## 这一天的进展", "## 数字一览", "## 附录", "### 记录清单", "### 报告信息"]
        self.assertEqual([text.index(part) for part in order], sorted(text.index(part) for part in order))
        attention = text.split("## 需要处理", 1)[1].split("##", 1)[0]
        self.assertIn("失败：11:00 · 《Old cohort.pdf》处理失败［3］", attention)
        self.assertIn("待审：10:00 · 《Diet trial.pdf》数值待核对［2］", attention)
        self.assertIn("- 09:00 · 《Sleep survey.pdf》文献处理完成，12 个成果待人工复核［1］", text)
        self.assertIn("1. 2026-08-08 09:00 · 《Sleep survey.pdf》文献处理完成，12 个成果待人工复核 · memorive://artifact/re-0a1", text)
        self.assertIsNone(INTERNAL.search(_before_appendix(text)))
        self.assertIn("- 摘要：这份报告没有模型摘要。", text)

    def test_a_weekly_report_lists_each_record_once_and_its_tensions_as_progress(self) -> None:
        text = render_markdown(_digest("weekly"))
        self.assertIn("# 周报 · 2026-08-03 — 2026-08-09", text)
        progress = text.split("## 这一周的进展", 1)[1].split("## 数字一览", 1)[0]
        self.assertIn("### 明确来源的潜在张力", progress)
        self.assertIn("两篇文献对样本量的说法不一致", progress)
        attention = text.split("## 需要处理", 1)[1].split("##", 1)[0]
        self.assertIn("风险与待办：", attention)
        self.assertIn("未验证项：", attention)
        records = text.split("### 记录清单", 1)[1].split("###", 1)[0]
        self.assertEqual(records.count("Sleep survey"), 1)      # in two sections, one record
        self.assertIsNone(INTERNAL.search(_before_appendix(text)))

    def test_a_monthly_report_shows_direction_changes_as_progress(self) -> None:
        text = render_markdown(_digest("monthly"))
        self.assertIn("# 月报 · 2026-08", text)
        progress = text.split("## 这个月的进展", 1)[1].split("## 数字一览", 1)[0]
        self.assertIn("### 明确记录的研究方向变化", progress)
        attention = text.split("## 需要处理", 1)[1].split("##", 1)[0]
        self.assertIn("长期待办：", attention)

    def test_the_model_summary_comes_first_with_the_numbers_of_its_records(self) -> None:
        digest = _digest("daily")
        digest["sections"][0]["items"] += [_event(f"RE-1{i}", f"《Paper {i}.pdf》文献处理完成，3 个成果待人工复核", 13 + i)
                                           for i in range(3)]
        narrative = {
            "period_summary": [{"text": "四篇文献处理完成。", "event_ids": ["RE-0A1", "RE-10", "RE-11", "RE-12"]}],
            "priority_items": [{"text": "先看处理失败的一篇。", "event_ids": ["RE-0C3"]}],
            "section_summaries": [
                {"section_title": "新增", "text": "新增的都是睡眠研究。", "event_ids": ["RE-0A1"]},
                {"section_title": "修改", "text": "NONE", "event_ids": []},
                {"section_title": "待审", "text": "NONE", "event_ids": []},
                {"section_title": "失败", "text": "NONE", "event_ids": []},
            ],
        }
        text = render_markdown_with_narrative(digest, narrative, {"model": "fixture-model"})
        self.assertLess(text.index("四篇文献处理完成。［1, 4–6］"), text.index("## 需要处理"))
        attention = text.split("## 需要处理", 1)[1].split("##", 1)[0]
        self.assertIn("- 先看处理失败的一篇。［3］", attention)
        self.assertNotIn("失败：", attention)            # already covered by the priority it cites
        self.assertIn("新增的都是睡眠研究。［1］", text)   # four records: the section summary is shown
        self.assertIn("- 摘要：由 fixture-model 根据上面的记录撰写", text)

    def test_a_short_section_shows_its_records_without_the_summary_sentence(self) -> None:
        narrative = {"period_summary": [{"text": "一篇处理完成。", "event_ids": ["RE-0A1"]}], "priority_items": [],
                     "section_summaries": [{"section_title": t, "text": "只有一篇。" if t == "新增" else "NONE",
                                            "event_ids": ["RE-0A1"] if t == "新增" else []} for t in TITLES["daily"]]}
        text = render_markdown_with_narrative(_digest("daily"), narrative, {"model": "fixture-model"})
        self.assertNotIn("只有一篇。", text)

    def test_a_paper_name_cannot_add_markup_but_stays_readable(self) -> None:
        digest = _digest("daily")
        digest["sections"][0]["items"][0]["text"] = "《[click](http://x.test) <b>bold</b> Long_Context_Evaluation.pdf》完成"
        text = render_markdown(digest)
        self.assertIn("\\[click\\](http://x.test) \\<b\\>bold\\</b\\> Long_Context_Evaluation.pdf", text)
        self.assertNotIn("[click](", text)

    def test_the_report_is_written_in_the_language_asked_for(self) -> None:
        english = render_markdown(_digest("daily"), language="en-US")
        self.assertIn("# Daily report · 2026-08-08", english)
        self.assertIn("## Needs attention", english)
        self.assertIn("Records of the day: Added 1, Pending review 1, Failed 1.", english)
        japanese = render_markdown(_digest("weekly"), language="ja-JP")
        self.assertIn("## 対応が必要なこと", japanese)
        self.assertIn("# 週報 · 2026-08-03 — 2026-08-09", japanese)

    def test_an_unknown_template_placeholder_fails(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            broken = Path(folder) / "report.md.tmpl"
            broken.write_text(renderer.TEMPLATE.read_text(encoding="utf-8") + "\n{{unknown_placeholder}}\n", encoding="utf-8")
            with patch.object(renderer, "TEMPLATE", broken):
                with self.assertRaisesRegex(ValueError, "placeholder"):
                    render_markdown(_digest("daily"))


class PeriodicRecordTests(unittest.TestCase):
    def test_the_output_files_of_a_paper_make_one_record(self) -> None:
        from memorive.research.reports.periodic import render
        written = {}
        runtime = SimpleNamespace(run_path=lambda run_id: Path("."), update_state=lambda *a, **k: None)
        files = [{"material_kind": "PAPER", "task_id": "job-1", "document_name": "Sleep survey.pdf",
                  "display_name": f"[{kind}][DOC_1] Sleep survey.md", "stable_locator": f"memorive://artifact/p-{kind}",
                  "updated_at": f"2026-08-08T0{hour}:00:00Z"} for hour, kind in enumerate(("Card", "Analysis", "RawMD"), 1)]
        files.append({"material_kind": "REFINEMENT", "task_id": "session-9", "display_name": "Interview notes",
                      "stable_locator": "memorive://artifact/r-9", "updated_at": "2026-08-08T05:00:00Z"})
        params = {"report_window": {"period": "daily", "start": "2026-08-08T00:00:00+08:00", "end": "2026-08-09T00:00:00+08:00"},
                  "material_progress": files, "report_cutoff": "2026-08-09T00:30:00+08:00", "language_context": None}
        with patch("memorive.research.reports.periodic.write", lambda path, value: written.update(snapshot=value)):
            result = render(runtime, "run-1", params)
        events = written["snapshot"]["events"]
        self.assertEqual(sorted(e["display_text"] for e in events),
                         ["《Interview notes》对话精炼完成，待人工复核", "《Sleep survey.pdf》文献处理完成，3 个成果待人工复核"])
        paper = next(e for e in events if "Sleep" in e["display_text"])
        self.assertEqual(len(paper["subject_refs"]), 3)
        self.assertEqual(paper["occurred_at"], "2026-08-08T03:00:00Z")    # the latest of its files
        self.assertEqual(result["material_progress_count"], 4)
        self.assertIn("这一天的记录：新增 2 条。", result["markdown"])
