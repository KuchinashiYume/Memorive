from __future__ import annotations

import json
import unittest
from pathlib import Path

from memorive.models.gateway.config import GatewayConfig
from research_reports.api_briefing import BLOCKED_INPUT_INCOMPLETE, GENERATED, SKIPPED_NO_LIBRARY_CHANGE, BriefingApiError, qualifying_library_change_ids
from research_reports.api_briefing import run_api_briefing
TITLES = {
    "daily": ["新增", "修改", "待审", "失败"],
    "weekly": ["研究进展", "明确标记的重要变化", "未验证项", "明确来源的潜在张力", "风险与待办"],
    "monthly": ["知识库结构变化", "明确记录的研究方向变化", "长期待办", "风险与未评估维度"],
}


def _item(event_id: str, axis: str = "artifact_registry") -> dict:
    return {
        "event_id": event_id,
        "source_event_ref": event_id,
        "source_record_ref": f"fixture/{event_id}.json#line=1",
        "artifact_or_ledger_refs": [f"artifact-{event_id}"],
        "occurred_at": "2026-08-08T09:00:00+08:00",
        "observed_at": "2026-08-08T09:01:00+08:00",
        "status_axis": axis,
        "status": "recorded",
        "verification_status": "not_applicable",
        "evidence_refs": [f"evidence-{event_id}"],
        "text": f"frozen event {event_id}",
    }


def _digest(kind: str, *, axis: str = "artifact_registry", blocked: bool = False) -> dict:
    sections = [
        {"section_id": f"S{i:02d}", "title": title, "items": []}
        for i, title in enumerate(TITLES[kind], 1)
    ]
    sections[0]["items"].append(_item("RE-LIBRARY-CHANGE", axis))
    return {
        "object_type": "ChangeDigest",
        "schema_version": "2.0",
        "domain_contract_version": "1.1",
        "digest_id": f"CD-{kind}-fixture",
        "revision": 1,
        "digest_kind": kind,
        "report_timezone": "Asia/Shanghai",
        "window_start": "2026-08-08T00:00:00+08:00",
        "window_end": "2026-08-09T00:00:00+08:00",
        "cutoff_at": "2026-08-09T00:30:00+08:00",
        "input_manifest_sha256": "A" * 64,
        "generator_version": "fixture-generator",
        "source_snapshot_refs": ["fixture/snapshot.json"],
        "report_status": "blocked_incomplete" if blocked else "complete",
        "authority_status": "shadow_non_authoritative",
        "sections": sections,
        "late_arrivals": [],
        "conflicts": [
            {
                "event_id": "RE-CONFLICT",
                "source_record_refs": ["fixture/a#line=1", "fixture/b#line=1"],
            }
        ] if blocked else [],
        "unparseable_events": [],
        "not_assessed_dimensions": [
            {
                "dimension": "scientific_value",
                "status": "NOT_ASSESSED",
                "reason_code": "OUT_OF_SCOPE",
            }
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


def _narrative(contract: dict) -> dict:
    digest = contract["digest"]
    section_rows = []
    for section in digest["sections"]:
        ids = [item["event_id"] for item in section["items"]]
        section_rows.append(
            {
                "section_title": section["title"],
                "text": "NONE" if not ids else f"Source-bound summary for {section['title']}.",
                "event_ids": ids[:2],
            }
        )
    all_ids = contract["allowed_event_ids"]
    return {
        "schema_version": "memorive.research.research_reports.brief-narrative.v1",
        "digest_id": digest["digest_id"],
        "digest_kind": digest["digest_kind"],
        "period_summary": [
            {
                "text": "The period contains one source-bound library change.",
                "event_ids": all_ids[:1],
            }
        ],
        "priority_items": [
            {
                "text": "Review the admitted library change first.",
                "event_ids": all_ids[:1],
            }
        ],
        "section_summaries": section_rows,
    }


def _response(value: dict, provider: str, model: str) -> dict:
    return {
        "provider": provider,
        "model": model,
        "text": json.dumps(value, ensure_ascii=False),
        "stop_reason": "stop" if provider == "deepseek" else "end_turn",
        "transport": {"stream": True, "complete": True},
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


class ApiBriefingTests(unittest.TestCase):
    def test_profiles_match_gateway_slots_exactly(self) -> None:
        from research_reports.api_briefing import PROFILES

        config = GatewayConfig((Path(__file__).resolve().parents[3] / "source") / "memorive" / "models" / "gateway" / "models.yaml")
        for profile in PROFILES.values():
            slot = config.slot(profile.slot)
            self.assertTrue(slot.enabled)
            self.assertEqual(slot.provider, profile.provider)
            self.assertEqual(slot.model_id, profile.model)
            self.assertEqual(slot.thinking_type, profile.thinking["type"])
            self.assertEqual(slot.effort, profile.effort)
            self.assertEqual(slot.max_tokens, profile.max_tokens)
            self.assertEqual(slot.timeout_seconds, profile.timeout_seconds)

    def test_no_qualifying_library_change_means_no_api_call(self) -> None:
        digest = _digest("daily", axis="attempt_result")
        calls = []
        result = run_api_briefing(
            digest=digest,
            model_call=lambda *args: calls.append(args),
        )
        self.assertEqual(result.status, SKIPPED_NO_LIBRARY_CHANGE)
        self.assertEqual(result.model_call_count, 0)
        self.assertEqual(calls, [])
        self.assertEqual(qualifying_library_change_ids(digest), ())

    def test_incomplete_change_status_blocks_without_api_call(self) -> None:
        calls = []
        result = run_api_briefing(
            digest=_digest("daily", blocked=True),
            model_call=lambda *args: calls.append(args),
        )
        self.assertEqual(result.status, BLOCKED_INPUT_INCOMPLETE)
        self.assertEqual(result.model_call_count, 0)
        self.assertEqual(calls, [])

    def test_each_period_uses_an_independent_default_slot_and_profile(self) -> None:
        expected = {
            "daily": (
                "report_daily", "deepseek", "deepseek-v4-flash",
                {"type": "disabled"}, None, 4096,
            ),
            "weekly": (
                "report_weekly", "deepseek", "deepseek-v4-pro",
                {"type": "enabled"}, "high", 8192,
            ),
            "monthly": (
                "report_monthly", "anthropic", "claude-sonnet-5",
                {"type": "adaptive"}, "max", 65536,
            ),
        }
        for kind, (slot, provider, model, thinking, effort, max_tokens) in expected.items():
            calls = []

            def model_call(task_type: str, payload: dict) -> dict:
                calls.append((task_type, payload))
                return _response(
                    _narrative(payload["memorive_contract"]), provider, model
                )

            result = run_api_briefing(digest=_digest(kind), model_call=model_call)
            self.assertEqual(result.status, GENERATED)
            self.assertEqual(result.slot, slot)
            self.assertEqual(result.model_call_count, 1)
            self.assertEqual(calls[0][0], slot)
            self.assertEqual(calls[0][1]["thinking"], thinking)
            self.assertEqual(
                calls[0][1].get("reasoning_effort") or calls[0][1].get("effort"),
                effort,
            )
            self.assertEqual(calls[0][1]["max_tokens"], max_tokens)
            self.assertIn("## 1. 详细记录", result.markdown_text)
            self.assertIn("API narrative", result.markdown_text)

    def test_explicit_anthropic_low_override_does_not_change_defaults(self) -> None:
        from research_reports.api_briefing import BriefProfile, PROFILES

        default_daily = PROFILES["daily"]
        profile = BriefProfile(
            slot="sandbox_report_daily_sonnet_low",
            provider="anthropic",
            model="claude-sonnet-5",
            thinking={"type": "adaptive"},
            effort="low",
            max_tokens=4096,
            timeout_seconds=300,
        )
        calls = []

        def model_call(slot: str, payload: dict) -> dict:
            calls.append((slot, payload))
            return _response(
                _narrative(payload["memorive_contract"]),
                "anthropic",
                "claude-sonnet-5",
            )

        result = run_api_briefing(
            digest=_digest("daily"),
            model_call=model_call,
            profile_override=profile,
        )
        self.assertEqual(result.status, GENERATED)
        self.assertEqual(result.slot, profile.slot)
        self.assertEqual(calls[0][1]["effort"], "low")
        self.assertEqual(calls[0][1]["output_config"]["effort"], "low")
        self.assertNotIn("format", calls[0][1]["output_config"])
        self.assertEqual(
            calls[0][1]["memorive_contract"]["structured_output_mode"],
            "PROMPT_JSON_PLUS_LOCAL_SCHEMA",
        )
        self.assertNotIn("response_format", calls[0][1])
        self.assertIs(PROFILES["daily"], default_daily)

    def test_section_identity_and_empty_rows_are_runtime_canonicalized(self) -> None:
        digest = _digest("weekly")

        def decorated(_slot: str, payload: dict) -> dict:
            value = _narrative(payload["memorive_contract"])
            for section, row in zip(
                payload["memorive_contract"]["digest"]["sections"],
                value["section_summaries"],
                strict=True,
            ):
                row["section_title"] = f"{section['section_id']}-{section['title']}"
                if not row["event_ids"]:
                    row["text"] = "No recorded changes in this section."
            return _response(value, "deepseek", "deepseek-v4-pro")

        result = run_api_briefing(digest=digest, model_call=decorated)

        self.assertEqual(result.status, GENERATED)
        self.assertEqual(
            [row["section_title"] for row in result.narrative["section_summaries"]],
            TITLES["weekly"],
        )
        self.assertTrue(
            all(
                row["text"] == "NONE"
                for row in result.narrative["section_summaries"][1:]
            )
        )

    def test_section_count_mismatch_still_fails_closed(self) -> None:
        digest = _digest("weekly")

        def missing_row(_slot: str, payload: dict) -> dict:
            value = _narrative(payload["memorive_contract"])
            value["section_summaries"].pop()
            return _response(value, "deepseek", "deepseek-v4-pro")

        with self.assertRaisesRegex(
            BriefingApiError,
            "NARRATIVE_SECTION_COVERAGE_INVALID",
        ):
            run_api_briefing(digest=digest, model_call=missing_row)

    def test_unknown_event_reference_and_model_drift_fail_closed(self) -> None:
        digest = _digest("daily")

        def bad_ref(_slot: str, payload: dict) -> dict:
            value = _narrative(payload["memorive_contract"])
            value["period_summary"][0]["event_ids"] = ["RE-INVENTED"]
            return _response(value, "deepseek", "deepseek-v4-flash")

        with self.assertRaisesRegex(BriefingApiError, "NARRATIVE_EVENT_REF_UNKNOWN"):
            run_api_briefing(digest=digest, model_call=bad_ref)

        def drift(_slot: str, payload: dict) -> dict:
            return _response(
                _narrative(payload["memorive_contract"]),
                "deepseek",
                "deepseek-v4-pro",
            )

        with self.assertRaisesRegex(BriefingApiError, "RETURNED_MODEL_MISMATCH"):
            run_api_briefing(digest=digest, model_call=drift)

    def test_missing_strict_call_binding_only_blocks_when_change_exists(self) -> None:
        with self.assertRaisesRegex(BriefingApiError, "STRICT_MODEL_CALL_BINDING_REQUIRED"):
            run_api_briefing(digest=_digest("daily"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
