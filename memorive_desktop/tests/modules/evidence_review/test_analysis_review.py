from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from memorive.models.gateway.config import GatewayConfig
from evidence_review.research_handoff_cross_analysis_review import BLOCKED, DEFAULT_REVIEW_PROFILE, PASS, PASS_WITH_OMISSIONS, CrossAnalysisReviewError, FrozenEvidenceSnippet, ReviewProfile, build_cross_analysis_review_view, run_cross_analysis_review_closure
SECTION_IDS = (
    "scope_and_evidence_boundary",
    "comparison_dimensions",
    "evolution_path",
    "consensus_and_continuities",
    "differences_and_tensions",
    "mechanisms_and_tradeoffs",
    "limitations_and_uncertainties",
    "recheck_recommendations",
)


def _analysis() -> dict:
    sections = []
    for section_index, section_id in enumerate(SECTION_IDS):
        items = []
        for item_index in range(2 if section_index == 0 else 1):
            source_id = f"PaperA#c{section_index * 2 + item_index + 1:04d}"
            items.append(
                {
                    "item_id": f"I-{section_index + 1}-{item_index + 1}",
                    "statement": f"Statement {section_index + 1}.{item_index + 1} is grounded in the cited frozen source text.",
                    "paper_ids": ["G1-RETRIEVAL"],
                    "evidence_refs": [
                        {
                            "paper_id": "G1-RETRIEVAL",
                            "artifact_type": "CARD",
                            "source_id": source_id,
                            "artifact_sha256": "A" * 64,
                            "evidence_depth": "CARD_ONLY",
                        }
                    ],
                    "qualifiers": ["Only the frozen evidence is in scope."],
                }
            )
        sections.append(
            {
                "section_id": section_id,
                "heading": section_id.replace("_", " "),
                "summary": f"Summary for {section_id} based only on the listed section items and their evidence.",
                "items": items,
            }
        )
    return {
        "schema_version": "memorive.research.analysis.cross-topic-analysis.v2",
        "artifact_id": "HANDOFF-TEST-CROSS-ANALYSIS",
        "group_id": "G1",
        "title": "Synthetic cross-paper analysis fixture",
        "primary_question": "How do the frozen papers relate, and which conclusions remain uncertain?",
        "executive_answer": "The frozen papers show a bounded technical progression, while direct comparison remains limited by evidence and evaluation differences.",
        "execution": {
            "provider": "anthropic",
            "analysis_model": "claude-sonnet-5",
            "thinking_type": "adaptive",
            "effort": "xhigh",
            "max_tokens": 65536,
            "analyzed_at": "2026-08-09T20:00:00+09:00",
            "source_commit": "b" * 40,
            "source_tree_manifest_sha256": "B" * 64,
            "request_id": "HANDOFF-TEST-REQUEST",
            "request_template_id": "Memorive-HANDOFF-CROSS-CUSTOM-TOPIC-ANALYSIS-REQUEST",
            "request_template_revision": "r1.0",
            "request_content_sha256": "C" * 64,
            "call_id": "HANDOFF-TEST-ANALYSIS-CALL",
        },
        "evidence_scope": {
            "input_snapshot_id": "HANDOFF-TEST-SNAPSHOT",
            "input_manifest_sha256": "D" * 64,
            "papers": [
                {
                    "paper_id": "G1-RETRIEVAL",
                    "canonical_paper_id": "PaperA",
                    "version_note": "Frozen Paper A version",
                    "evidence_depth": "CARD_ONLY",
                    "card_sha256": "A" * 64,
                    "analysis_sha256": "NONE",
                }
            ],
            "card_only_paper_id": "G1-RETRIEVAL",
            "card_only_analysis_absent": True,
        },
        "required_facet_coverage": [],
        "sections": sections,
        "paper_trajectory": [
            {
                "sequence": 1,
                "paper_id": "G1-RETRIEVAL",
                "role": "The frozen starting point",
                "transition_from_prior": "This is the first frozen stage, so no predecessor transition is asserted.",
                "evidence_refs": copy.deepcopy(sections[0]["items"][0]["evidence_refs"]),
            }
        ],
        "judgment_boundary": {
            "winner_selected": False,
            "overall_scientific_score_assigned": False,
            "final_user_judgment_made": False,
            "scientific_maturity": "NOT_ASSESSED",
        },
    }


def _snippets(analysis: dict) -> list[FrozenEvidenceSnippet]:
    seen = set()
    result = []
    for section in analysis["sections"]:
        for item in section["items"]:
            for ref in item["evidence_refs"]:
                if ref["source_id"] in seen:
                    continue
                seen.add(ref["source_id"])
                result.append(
                    FrozenEvidenceSnippet(
                        source_id=ref["source_id"],
                        paper_id=ref["paper_id"],
                        artifact_type=ref["artifact_type"],
                        artifact_sha256=ref["artifact_sha256"],
                        text=f"Frozen evidence for {ref['source_id']} supports only the bounded fixture statement.",
                    )
                )
    return result


def _provider_response(value: dict, *, provider: str, model: str) -> dict:
    return {
        "provider": provider,
        "model": model,
        "text": json.dumps(value, ensure_ascii=False),
        "stop_reason": "stop" if provider == "deepseek" else "end_turn",
        "transport": {"stream": True, "complete": True},
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def _full_review(view: dict, failed_claim_id: str | None = None) -> dict:
    rows = []
    for claim in view["claims"]:
        failed = claim["claim_id"] == failed_claim_id
        rows.append(
            {
                "claim_id": claim["claim_id"],
                "status": "REPAIR_REQUIRED" if failed else "SUPPORTED",
                "severity": "major" if failed else None,
                "evidence_source_ids": claim["allowed_evidence_source_ids"][:1],
                "reason": "Claim overstates the cited evidence." if failed else "",
            }
        )
    return {
        "schema_version": "memorive.research.analysis.cross-analysis-review.v1",
        "review_mode": "FULL",
        "artifact_sha256": view["artifact_sha256"],
        "claim_reviews": rows,
        "cross_checks": [
            {
                "check_id": item["check_id"],
                "status": "PASS",
                "target_claim_id": item["target_claim_id"],
                "evidence_source_ids": [],
                "reason": "The frozen cross-check passes at the reviewed scope.",
            }
            for item in view["cross_checks"]
        ],
    }


class CrossAnalysisReviewTests(unittest.TestCase):
    def test_full_pass_makes_one_low_reasoning_review_call(self) -> None:
        analysis = _analysis()
        build_cross_analysis_review_view(analysis, _snippets(analysis))
        captured = []

        def review_call(payload: dict) -> dict:
            captured.append(payload)
            return _provider_response(
                _full_review(payload["memorive_contract"]["view"]),
                provider="deepseek",
                model="deepseek-v4-pro",
            )

        result = run_cross_analysis_review_closure(
            analysis=analysis,
            evidence_snippets=_snippets(analysis),
            expected_primary_question=analysis["primary_question"],
            review_call=review_call,
        )
        self.assertEqual(result.status, PASS)
        self.assertEqual(result.review_call_count, 1)
        self.assertEqual(result.repair_call_count, 0)
        self.assertEqual(result.final_artifact, analysis)
        self.assertEqual(captured[0]["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", captured[0])

    def test_supported_reason_is_deterministically_canonicalized(self) -> None:
        analysis = _analysis()

        def review_call(payload: dict) -> dict:
            value = _full_review(payload["memorive_contract"]["view"])
            value["claim_reviews"][0]["reason"] = (
                "The supplied evidence supports this positive row."
            )
            return _provider_response(
                value,
                provider="deepseek",
                model="deepseek-v4-pro",
            )

        result = run_cross_analysis_review_closure(
            analysis=analysis,
            evidence_snippets=_snippets(analysis),
            expected_primary_question=analysis["primary_question"],
            review_call=review_call,
        )

        self.assertEqual(result.status, PASS)
        self.assertEqual(result.full_review["claim_reviews"][0]["reason"], "")

    def test_supported_non_null_severity_still_fails_closed(self) -> None:
        analysis = _analysis()

        def review_call(payload: dict) -> dict:
            value = _full_review(payload["memorive_contract"]["view"])
            value["claim_reviews"][0]["severity"] = "major"
            value["claim_reviews"][0]["reason"] = "Contradictory positive shape."
            return _provider_response(
                value,
                provider="deepseek",
                model="deepseek-v4-pro",
            )

        with self.assertRaisesRegex(
            CrossAnalysisReviewError,
            "FULL_REVIEW_FINDING_SHAPE_INVALID",
        ):
            run_cross_analysis_review_closure(
                analysis=analysis,
                evidence_snippets=_snippets(analysis),
                expected_primary_question=analysis["primary_question"],
                review_call=review_call,
            )

    def test_low_cost_rehearsal_profile_is_explicit_and_model_bound(self) -> None:
        analysis = _analysis()
        captured = []
        profile = ReviewProfile(
            slot="verify_judgment",
            provider="deepseek",
            model="deepseek-v4-flash",
            thinking={"type": "disabled"},
            effort=None,
            full_max_tokens=4096,
            delta_max_tokens=2048,
            timeout_seconds=300,
        )

        def review_call(payload: dict) -> dict:
            captured.append(payload)
            return _provider_response(
                _full_review(payload["memorive_contract"]["view"]),
                provider="deepseek",
                model="deepseek-v4-flash",
            )

        result = run_cross_analysis_review_closure(
            analysis=analysis,
            evidence_snippets=_snippets(analysis),
            expected_primary_question=analysis["primary_question"],
            review_call=review_call,
            review_profile=profile,
        )
        self.assertEqual(result.status, PASS)
        self.assertEqual(captured[0]["max_tokens"], 4096)
        self.assertEqual(
            captured[0]["memorive_contract"]["review_profile"]["model"],
            "deepseek-v4-flash",
        )
        self.assertEqual(captured[0]["response_format"], {"type": "json_object"})
        self.assertNotIn("output_config", captured[0])

    def test_anthropic_low_profile_uses_prompt_json_and_local_schema(self) -> None:
        analysis = _analysis()
        captured = []
        profile = ReviewProfile(
            slot="verify_judgment",
            provider="anthropic",
            model="claude-sonnet-5",
            thinking={"type": "adaptive"},
            effort="low",
            full_max_tokens=4096,
            delta_max_tokens=2048,
            timeout_seconds=300,
        )

        def review_call(payload: dict) -> dict:
            captured.append(payload)
            return _provider_response(
                _full_review(payload["memorive_contract"]["view"]),
                provider="anthropic",
                model="claude-sonnet-5",
            )

        result = run_cross_analysis_review_closure(
            analysis=analysis,
            evidence_snippets=_snippets(analysis),
            expected_primary_question=analysis["primary_question"],
            review_call=review_call,
            review_profile=profile,
        )
        self.assertEqual(result.status, PASS)
        self.assertEqual(captured[0]["effort"], "low")
        self.assertEqual(captured[0]["output_config"]["effort"], "low")
        self.assertNotIn("format", captured[0]["output_config"])
        self.assertEqual(
            captured[0]["memorive_contract"]["structured_output_mode"],
            "PROMPT_JSON_PLUS_LOCAL_SCHEMA",
        )
        self.assertNotIn("response_format", captured[0])

    def test_repair_token_ceiling_can_be_narrowed_for_governed_run(self) -> None:
        analysis = _analysis()
        first_claim = build_cross_analysis_review_view(
            analysis, _snippets(analysis)
        )["claims"][0]["claim_id"]
        calls = []

        def review_call(payload: dict) -> dict:
            calls.append(payload)
            view = payload["memorive_contract"].get("view")
            if view is not None:
                value = _full_review(view, failed_claim_id=first_claim)
            else:
                delta = payload["memorive_contract"]["delta_view"]
                value = {
                    "schema_version": "memorive.research.analysis.cross-analysis-delta-review.v1",
                    "review_mode": "DELTA",
                    "source_artifact_sha256": delta["source_artifact_sha256"],
                    "candidate_artifact_sha256": delta["candidate_artifact_sha256"],
                    "target_reviews": [
                        {
                            "target_id": row["target_id"],
                            "status": "RESOLVED",
                            "evidence_source_ids": row["allowed_evidence_source_ids"][:1],
                            "reason": "",
                        }
                        for row in delta["targets"]
                    ],
                }
            return _provider_response(
                value, provider="deepseek", model="deepseek-v4-pro"
            )

        def repair_call(payload: dict) -> dict:
            calls.append(payload)
            bundle = payload["memorive_contract"]["repair_bundle"]
            value = {
                "schema_version": "memorive.research.analysis.cross-analysis-targeted-repair.v1",
                "source_artifact_sha256": bundle["source_artifact_sha256"],
                "repairs": [
                    {
                        "target_id": target["target_id"],
                        "finding_ids": target["finding_ids"],
                        "action": "replace",
                        "replacement": "Narrow source-grounded replacement.",
                        "reason": "The replacement removes the overstatement.",
                    }
                    for target in bundle["targets"]
                ],
            }
            return _provider_response(
                value, provider="anthropic", model="claude-sonnet-5"
            )

        run_cross_analysis_review_closure(
            analysis=analysis,
            evidence_snippets=_snippets(analysis),
            expected_primary_question=analysis["primary_question"],
            review_call=review_call,
            repair_call=repair_call,
            repair_max_tokens=2048,
        )
        repair_payload = next(
            payload
            for payload in calls
            if payload["memorive_contract"]["mode"] == "TARGETED_REPAIR"
        )
        self.assertEqual(repair_payload["max_tokens"], 2048)

    def test_default_review_profile_reuses_the_existing_gateway_slot(self) -> None:
        config = GatewayConfig((Path(__file__).resolve().parents[3] / "source") / "memorive" / "models" / "gateway" / "models.yaml")
        slot = config.slot(DEFAULT_REVIEW_PROFILE.slot)
        self.assertTrue(slot.enabled)
        self.assertEqual(slot.provider, DEFAULT_REVIEW_PROFILE.provider)
        self.assertEqual(slot.model_id, DEFAULT_REVIEW_PROFILE.model)
        self.assertEqual(DEFAULT_REVIEW_PROFILE.thinking, {"type": "disabled"})

    def test_one_batched_targeted_repair_then_delta_pass(self) -> None:
        analysis = _analysis()
        view = build_cross_analysis_review_view(analysis, _snippets(analysis))
        failed = next(claim for claim in view["claims"] if claim["criticality"] == "optional")
        review_modes = []

        def review_call(payload: dict) -> dict:
            contract = payload["memorive_contract"]
            review_modes.append(contract["mode"])
            if contract["mode"] == "FULL":
                value = _full_review(contract["view"], failed["claim_id"])
            else:
                delta = contract["delta_view"]
                value = {
                    "schema_version": "memorive.research.analysis.cross-analysis-delta-review.v1",
                    "review_mode": "DELTA",
                    "source_artifact_sha256": delta["source_artifact_sha256"],
                    "candidate_artifact_sha256": delta["candidate_artifact_sha256"],
                    "target_reviews": [
                        {
                            "target_id": target["target_id"],
                            "status": "RESOLVED",
                            "evidence_source_ids": target["allowed_evidence_source_ids"][:1],
                            "reason": "",
                        }
                        for target in delta["targets"]
                    ],
                }
            return _provider_response(value, provider="deepseek", model="deepseek-v4-pro")

        def repair_call(payload: dict) -> dict:
            bundle = payload["memorive_contract"]["repair_bundle"]
            value = {
                "schema_version": "memorive.research.analysis.cross-analysis-targeted-repair.v1",
                "source_artifact_sha256": bundle["source_artifact_sha256"],
                "repairs": [
                    {
                        "target_id": target["target_id"],
                        "finding_ids": target["finding_ids"],
                        "action": "replace",
                        "replacement": "A narrower statement supported by the exact frozen evidence and no broader claim.",
                        "reason": "Narrowed to the cited evidence.",
                    }
                    for target in bundle["targets"]
                ],
            }
            return _provider_response(value, provider="anthropic", model="claude-sonnet-5")

        result = run_cross_analysis_review_closure(
            analysis=analysis,
            evidence_snippets=_snippets(analysis),
            expected_primary_question=analysis["primary_question"],
            review_call=review_call,
            repair_call=repair_call,
        )
        self.assertEqual(result.status, PASS)
        self.assertEqual(review_modes, ["FULL", "DELTA"])
        self.assertEqual(result.review_call_count, 2)
        self.assertEqual(result.repair_call_count, 1)
        self.assertNotEqual(result.final_artifact, analysis)
        self.assertEqual(result.tombstones, ())

    def _run_unresolved(self, failed_claim_id: str):
        analysis = _analysis()
        view = build_cross_analysis_review_view(analysis, _snippets(analysis))

        def review_call(payload: dict) -> dict:
            contract = payload["memorive_contract"]
            if contract["mode"] == "FULL":
                value = _full_review(contract["view"], failed_claim_id)
            else:
                delta = contract["delta_view"]
                value = {
                    "schema_version": "memorive.research.analysis.cross-analysis-delta-review.v1",
                    "review_mode": "DELTA",
                    "source_artifact_sha256": delta["source_artifact_sha256"],
                    "candidate_artifact_sha256": delta["candidate_artifact_sha256"],
                    "target_reviews": [
                        {
                            "target_id": target["target_id"],
                            "status": "UNRESOLVED",
                            "evidence_source_ids": target["allowed_evidence_source_ids"][:1],
                            "reason": "The supplied evidence cannot safely resolve the claim.",
                        }
                        for target in delta["targets"]
                    ],
                }
            return _provider_response(value, provider="deepseek", model="deepseek-v4-pro")

        def repair_call(payload: dict) -> dict:
            bundle = payload["memorive_contract"]["repair_bundle"]
            value = {
                "schema_version": "memorive.research.analysis.cross-analysis-targeted-repair.v1",
                "source_artifact_sha256": bundle["source_artifact_sha256"],
                "repairs": [
                    {
                        "target_id": target["target_id"],
                        "finding_ids": target["finding_ids"],
                        "action": "no_change",
                        "replacement": None,
                        "reason": "No safe correction is available.",
                    }
                    for target in bundle["targets"]
                ],
            }
            return _provider_response(value, provider="anthropic", model="claude-sonnet-5")

        return view, run_cross_analysis_review_closure(
            analysis=analysis,
            evidence_snippets=_snippets(analysis),
            expected_primary_question=analysis["primary_question"],
            review_call=review_call,
            repair_call=repair_call,
        )

    def test_unresolved_optional_claim_is_tombstoned_with_lineage(self) -> None:
        analysis = _analysis()
        view = build_cross_analysis_review_view(analysis, _snippets(analysis))
        failed = next(claim for claim in view["claims"] if claim["criticality"] == "optional")
        view, result = self._run_unresolved(failed["claim_id"])
        self.assertEqual(result.status, PASS_WITH_OMISSIONS)
        self.assertEqual(len(result.tombstones), 1)
        self.assertEqual(result.tombstones[0]["target_path"], failed["target_path"])
        self.assertEqual(result.tombstones[0]["source_artifact_sha256"], view["artifact_sha256"])

    def test_unresolved_required_claim_blocks_and_never_returns_candidate(self) -> None:
        _view, result = self._run_unresolved("executive_answer")
        self.assertEqual(result.status, BLOCKED)
        self.assertIsNone(result.final_artifact)
        self.assertEqual(len(result.tombstones), 0)

    def test_missing_call_binding_and_evidence_mismatch_fail_before_send(self) -> None:
        analysis = _analysis()
        with self.assertRaisesRegex(CrossAnalysisReviewError, "STRICT_REVIEW_CALL_BINDING_REQUIRED"):
            run_cross_analysis_review_closure(
                analysis=analysis,
                evidence_snippets=_snippets(analysis),
                expected_primary_question=analysis["primary_question"],
            )
        bad = _snippets(analysis)
        bad[0] = FrozenEvidenceSnippet(
            source_id=bad[0].source_id,
            paper_id=bad[0].paper_id,
            artifact_type=bad[0].artifact_type,
            artifact_sha256="F" * 64,
            text=bad[0].text,
        )
        called = []
        with self.assertRaisesRegex(CrossAnalysisReviewError, "EVIDENCE_BINDING_MISMATCH"):
            run_cross_analysis_review_closure(
                analysis=analysis,
                evidence_snippets=bad,
                expected_primary_question=analysis["primary_question"],
                review_call=lambda payload: called.append(payload),
            )
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
