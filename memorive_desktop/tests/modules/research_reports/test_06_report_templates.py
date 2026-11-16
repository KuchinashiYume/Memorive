from __future__ import annotations

import json
import re
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from memorive.research.reports import renderer
from memorive.research.reports.renderer import render_markdown


TEMPLATE_ROOT = (Path(__file__).resolve().parents[3] / "source" / "memorive" / "research" / "reports") / "templates"
TOKEN_PATTERN = re.compile(r"{{([a-z_]+)}}")


def _event(event_id: str, text: str) -> dict:
    return {
        "event_id": event_id,
        "source_event_ref": event_id,
        "occurred_at": "2026-08-08T09:00:00+08:00",
        "text": text,
        "status_axis": "lifecycle_status",
        "status": "recorded",
        "artifact_or_ledger_refs": [f"artifact-{event_id}"],
        "source_record_ref": f"fixture/{event_id}.json#line=1",
        "evidence_refs": [f"evidence-{event_id}"],
    }


def _digest(kind: str) -> dict:
    titles = {
        "daily": ["新增", "修改", "待审", "失败"],
        "weekly": [
            "研究进展",
            "明确标记的重要变化",
            "未验证项",
            "明确来源的潜在张力",
            "风险与待办",
        ],
        "monthly": [
            "知识库结构变化",
            "明确记录的研究方向变化",
            "长期待办",
            "风险与未评估维度",
        ],
    }[kind]
    shared = _event("RE-SHARED", "shared evidence")
    sections = [
        {"title": title, "items": []}
        for title in titles
    ]
    sections[0]["items"].append(shared)
    if kind == "weekly":
        sections[1]["items"].append(deepcopy(shared))
        sections[2]["items"].append(_event("RE-UNVERIFIED", "needs verification"))
        sections[3]["items"].append(_event("RE-TENSION", "authority tension"))
        sections[4]["items"].append(_event("RE-RISK", "risk and todo"))
    elif kind == "monthly":
        sections[1]["items"].append(_event("RE-DIRECTION", "recorded direction"))
        sections[2]["items"].append(_event("RE-LONGTERM", "long term todo"))
        sections[3]["items"].append(_event("RE-MONTH-RISK", "monthly risk"))
    else:
        sections[2]["items"].append(_event("RE-REVIEW", "awaiting review"))
        sections[3]["items"].append(_event("RE-FAIL", "failed check"))
    return {
        "object_type": "ChangeDigest",
        "schema_version": "1.0",
        "domain_contract_version": "1.1",
        "digest_id": f"digest-fixture-{kind}",
        "revision": 1,
        "digest_kind": kind,
        "report_timezone": "+08:00",
        "window_start": "2026-08-08T00:00:00+08:00",
        "window_end": "2026-08-09T00:00:00+08:00",
        "cutoff_at": "2026-08-09T00:30:00+08:00",
        "input_manifest_sha256": "A" * 64,
        "generator_version": "fixture-generator",
        "source_snapshot_refs": ["fixture/snapshot.json"],
        "report_status": "complete",
        "authority_status": "shadow_non_authoritative",
        "sections": sections,
        "late_arrivals": [],
        "conflicts": [],
        "unparseable_events": [],
        "not_assessed_dimensions": [
            {"dimension": "scientific_value", "reason_code": "OUT_OF_SCOPE"}
        ],
        "supersedes_digest_id": None,
        "supersedes_binding": None,
        "generated_at": "2026-08-09T00:30:00+08:00",
        "external_calls": 0,
        "external_tokens": 0,
        "cost_cny": 0,
        "production_mutations": 0,
        "production_activation": False,
    }


class ReportTemplateTests(unittest.TestCase):
    def test_sidecar_placeholder_contracts_are_exact(self) -> None:
        for kind in ("daily", "weekly", "monthly"):
            sidecar = json.loads(
                (TEMPLATE_ROOT / f"{kind}.json").read_text(encoding="utf-8")
            )
            template = (TEMPLATE_ROOT / sidecar["source_file"]).read_text(
                encoding="utf-8"
            )
            self.assertEqual(sidecar["digest_kind"], kind)
            self.assertEqual(
                set(TOKEN_PATTERN.findall(template)),
                set(sidecar["required_placeholders"]),
            )

    def test_daily_puts_effective_information_before_details(self) -> None:
        rendered = render_markdown(_digest("daily"))
        self.assertIn("## 0. 今日先看", rendered)
        self.assertIn("失败", rendered)
        self.assertIn("待审", rendered)
        self.assertLess(rendered.index("## 0. 今日先看"), rendered.index("## 1. 详细记录"))
        self.assertLess(
            rendered.index("## 1. 详细记录"),
            rendered.index("## 附录 · 审计与重放信息"),
        )
        self.assertNotIn("digest_id:", rendered.split("## 1. 详细记录", 1)[0])

    def test_weekly_front_view_uses_explicit_authority_and_deduplicates(self) -> None:
        rendered = render_markdown(_digest("weekly"))
        self.assertIn("### 0.3 明确标记的重要变化", rendered)
        self.assertIn("### 0.4 明确来源的潜在张力", rendered)
        details = rendered.split("## 1. 详细记录", 1)[1].split("## 2.", 1)[0]
        self.assertEqual(details.count("shared evidence"), 1)
        self.assertIn("交叉引用", details)

    def test_monthly_front_view_keeps_direction_and_long_term_separate(self) -> None:
        rendered = render_markdown(_digest("monthly"))
        self.assertIn("### 0.2 知识结构变化", rendered)
        self.assertIn("### 0.3 明确记录的研究方向变化", rendered)
        self.assertIn("### 0.4 长期未决事项", rendered)
        self.assertIn("recorded direction", rendered)
        self.assertIn("long term todo", rendered)

    def test_unknown_template_placeholder_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sidecar = json.loads(
                (TEMPLATE_ROOT / "daily.json").read_text(encoding="utf-8")
            )
            (root / "daily.json").write_text(
                json.dumps(sidecar, ensure_ascii=False), encoding="utf-8"
            )
            template = (TEMPLATE_ROOT / "daily.md.tmpl").read_text(encoding="utf-8")
            (root / "daily.md.tmpl").write_text(
                template + "\n{{unknown_placeholder}}\n", encoding="utf-8"
            )
            with patch.object(renderer, "TEMPLATE_ROOT", root, create=True):
                with self.assertRaisesRegex(ValueError, "placeholder"):
                    render_markdown(_digest("daily"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
