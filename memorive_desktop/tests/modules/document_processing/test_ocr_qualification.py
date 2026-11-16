"""Offline Construction-A tests for PAGE-RECOGNITION OCR qualification."""

from __future__ import annotations

import copy
import unittest

from jsonschema import Draft202012Validator

from memorive.documents.processing.ocr_qualification import OCRQualificationContractError, QualificationThresholds, compare_page, diagnose_page, evaluate_accounting, explain_route, normalize_transcription, normalize_provider_payload, project_family_qualification, score_page, summarize_qualification_matrix, validate_cell_denominator, validate_page_subject_input
from memorive.documents.processing.ocr_qualification.contracts import load_schema
SHA_A = "A" * 64
SHA_B = "B" * 64
SHA_C = "C" * 64


def page_input(*, candidate: str = "claude_api_visual") -> dict:
    channel = {
        "claude_api_visual": "usage_based_api",
        "deepseek_ocr_specialist_baseline": "usage_based_api",
        "openai_api_visual": "usage_based_api",
        "openai_subscription_cli_visual": "subscription_cli",
        "local_visual_adapter": "local_resource",
    }[candidate]
    return {
        "schema_version": "PageRecognitionPageSubjectInput-v1",
        "pack_id": "pack-a",
        "attempt_id": "attempt-a",
        "attempt_ordinal": 1,
        "page_id": "page-01",
        "source_document_id": "doc-a",
        "source_page_number": 1,
        "source_file_sha256": SHA_A,
        "render_recipe_sha256": SHA_B,
        "source_image_sha256": SHA_C,
        "image_ref": "images/page-01.png",
        "image": {
            "mime_type": "image/png",
            "width_px": 1200,
            "height_px": 1600,
            "dpi": 200,
            "color_mode": "RGB",
            "crop_box": None,
        },
        "data_ownership": "public-safe",
        "rights_custody_verdict": "ALLOWED_PUBLIC_SAFE",
        "provider_visibility": "SINGLE_PAGE_IMAGE_ONLY",
        "page_family_primary": "table_numeric",
        "modifiers": [],
        "semantic_task": {"ref": "prompt:v4", "sha256": SHA_A},
        "channel_wrapper": {"ref": "wrapper:v1", "sha256": SHA_B},
        "output_schema": {"ref": "schema:v1", "sha256": SHA_C},
        "candidate": {
            "candidate_key": candidate,
            "channel_kind": channel,
            "provider": "test-provider",
            "profile_id": None,
            "requested_model": None,
            "route": None,
            "region": None,
            "egress": None,
        },
        "visibility": {
            "gold_visible": False,
            "other_candidate_outputs_visible": False,
            "hidden_text_visible": False,
            "whole_pdf_visible": False,
        },
        "retry_count": 0,
        "timeout_seconds": 120,
        "token_ceiling": None,
        "cash_cost_ceiling": None,
        "subscription_unit_ceiling": None,
        "credential_env_name": None,
        "authorization_ref": None,
        "external_request_authorized": False,
    }


def output(*, candidate: str = "claude_api_visual") -> dict:
    return {
        "schema_version": "PageRecognitionNormalizedPageTranscription-v1",
        "page_id": "page-01",
        "source_image_sha256": SHA_C,
        "candidate_key": candidate,
        "terminal_state": "completed",
        "literal_text": "TITLE\nValue 7.",
        "ordered_blocks": [
            {"block_id": "b1", "role": "title", "text": "TITLE", "bbox": None, "reading_order": 0},
            {"block_id": "b2", "role": "body", "text": "Value 7.", "bbox": None, "reading_order": 1},
        ],
        "critical_items": [
            {"kind": "NUMBER", "value": "7", "block_id": "b2", "confidence_state": "ASSERTED"}
        ],
        "table_cells": [
            {"table_id": "t1", "row": 1, "column": 1, "text": "7"}
        ],
        "visual_labels": [{"role": "caption", "text": "Value", "bbox": None}],
        "illegible_spans": [],
        "warnings": [],
        "visibility": {
            "gold_visible": False,
            "other_candidate_outputs_visible": False,
            "hidden_text_visible": False,
            "whole_pdf_visible": False,
        },
    }


def gold() -> dict:
    return {
        "page_id": "page-01",
        "source_image_sha256": SHA_C,
        "literal_text": "TITLE\nValue 7.",
        "normalization_rules": ["COLLAPSE_WHITESPACE"],
        "ordered_blocks": [
            {"block_id": "b1", "role": "title", "text": "TITLE"},
            {"block_id": "b2", "role": "body", "text": "Value 7."},
        ],
        "critical_items": [{"kind": "NUMBER", "value": "7", "block_id": "b2"}],
        "table_cells": [{"table_id": "t1", "row": 1, "column": 1, "text": "7"}],
        "visual_labels": [{"role": "caption", "text": "Value"}],
    }


def thresholds() -> QualificationThresholds:
    return QualificationThresholds(
        literal_accuracy_min=0.95,
        critical_exact_min=1.0,
        structure_accuracy_min=1.0,
        omission_hallucination_min=1.0,
        reviewer_detection_min=0.5,
        material_improvement_margin=0.02,
    )


def runtime(**overrides) -> dict:
    value = {
        "pre_send_sidecar_present": True,
        "external_request_started": True,
        "external_request_authorized": True,
        "identity_verdict": "PASS",
        "accounting_verdict": "PASS",
        "resource_verdict": "PASS",
    }
    value.update(overrides)
    return value


def scored(candidate: str = "claude_api_visual", **runtime_overrides) -> dict:
    return score_page(
        gold(),
        output(candidate=candidate),
        page_family="table_numeric",
        modifiers=[],
        output_lane="LITERAL_OCR",
        thresholds=thresholds(),
        runtime_receipt=runtime(**runtime_overrides),
    )


class ContractAndNormalizationTest(unittest.TestCase):
    def test_three_schemas_are_valid_draft_2020_12(self) -> None:
        for name in (
            "page_subject_input",
            "normalized_page_transcription",
            "page_family_qualification",
        ):
            Draft202012Validator.check_schema(load_schema(name))

    def test_offline_subject_input_is_valid_without_send_authority(self) -> None:
        accepted = validate_page_subject_input(page_input())
        self.assertFalse(accepted["external_request_authorized"])

    def test_entrusted_page_cannot_become_provider_visible(self) -> None:
        value = page_input()
        value.update(data_ownership="entrusted", rights_custody_verdict="BLOCKED")
        with self.assertRaisesRegex(OCRQualificationContractError, "PROVIDER_VISIBILITY_FORBIDDEN"):
            validate_page_subject_input(value)

    def test_external_authority_requires_exact_envelope(self) -> None:
        value = page_input()
        value["external_request_authorized"] = True
        with self.assertRaisesRegex(OCRQualificationContractError, "EXTERNAL_ENVELOPE_INCOMPLETE"):
            validate_page_subject_input(value)

    def test_candidate_channel_kind_cannot_be_guessed(self) -> None:
        value = page_input(candidate="openai_subscription_cli_visual")
        value["candidate"]["channel_kind"] = "usage_based_api"
        with self.assertRaisesRegex(OCRQualificationContractError, "CANDIDATE_CHANNEL_KIND_MISMATCH"):
            validate_page_subject_input(value)

    def test_openai_api_candidate_is_usage_based(self) -> None:
        value = page_input(candidate="openai_api_visual")
        accepted = validate_page_subject_input(value)
        self.assertEqual(accepted["candidate"]["channel_kind"], "usage_based_api")

    def test_normalizer_enforces_local_page_anchor(self) -> None:
        value = output()
        value["source_image_sha256"] = SHA_A
        with self.assertRaisesRegex(OCRQualificationContractError, "SOURCE_ENVELOPE_MISMATCH"):
            normalize_transcription(value, page_input())

    def test_normalizer_is_deterministic_and_sorts_safe_collections(self) -> None:
        value = output()
        value["warnings"] = ["zeta", "alpha", "zeta"]
        normalized = normalize_transcription(value, page_input())
        self.assertEqual(normalized["warnings"], ["alpha", "zeta"])
        self.assertEqual(normalized["ordered_blocks"][0]["block_id"], "b1")

    def test_provider_normalizer_rebuilds_literal_and_enriches_numeric_table_cells(self) -> None:
        value = output()
        value["literal_text"] = "stale provider literal"
        value["ordered_blocks"][1].update(
            role="table_row", text="A | 21 | 12.5"
        )
        value["critical_items"] = []
        value["table_cells"] = [
            {"table_id": "table-1", "row": 1, "column": 1, "text": "A"},
            {"table_id": "table-1", "row": 1, "column": 2, "text": "21"},
            {"table_id": "table-1", "row": 1, "column": 3, "text": "12.5"},
        ]
        normalized = normalize_provider_payload(value)
        self.assertEqual(normalized["literal_text"], "TITLE\nA | 21 | 12.5")
        self.assertEqual(
            [(item["kind"], item["value"], item["block_id"]) for item in normalized["critical_items"]],
            [("NUMBER", "12.5", "b2"), ("NUMBER", "21", "b2")],
        )

    def test_provider_normalizer_projects_visible_tokens_and_chart_labels(self) -> None:
        value = output()
        value["ordered_blocks"] = [
            {"block_id": "b1", "reading_order": 0, "role": "body", "text": "Expected: ZERO.", "bbox": None},
            {"block_id": "b2", "reading_order": 1, "role": "chart_item", "text": "Quarter A | 4", "bbox": None},
            {"block_id": "b3", "reading_order": 2, "role": "body", "text": "Trend = +3.", "bbox": None},
        ]
        value["critical_items"] = []
        value["table_cells"] = []
        value["visual_labels"] = []
        normalized = normalize_provider_payload(value)
        critical = {(item["kind"], item["value"], item["block_id"]) for item in normalized["critical_items"]}
        labels = {(item["role"], item["text"]) for item in normalized["visual_labels"]}
        self.assertIn(("TOKEN", "ZERO", "b1"), critical)
        self.assertIn(("NUMBER", "+3", "b3"), critical)
        self.assertEqual(normalized["ordered_blocks"][2]["role"], "caption")
        self.assertEqual(
            labels,
            {("chart_label", "Quarter A"), ("chart_label", "4"), ("chart_label", "+3")},
        )
        self.assertEqual(normalized["literal_text"], "Expected: ZERO.\nQuarter A | 4\nTrend = +3.")

    def test_duplicate_reading_order_is_rejected(self) -> None:
        value = output()
        value["ordered_blocks"][1]["reading_order"] = 0
        with self.assertRaisesRegex(OCRQualificationContractError, "READING_ORDER_DUPLICATE"):
            normalize_transcription(value, page_input())

    def test_illegible_critical_item_must_use_marker(self) -> None:
        value = output()
        value["critical_items"][0].update(confidence_state="ILLEGIBLE", value="7?")
        with self.assertRaisesRegex(OCRQualificationContractError, "ILLEGIBLE_VALUE_MUST_USE_MARKER"):
            normalize_transcription(value, page_input())


class DiagnosticTest(unittest.TestCase):
    def test_native_text_pass_forces_digital_family_without_model(self) -> None:
        result = diagnose_page(
            "Readable native text " * 20,
            data_ownership="public-safe",
            signals={"text_coverage_ratio": 0.95},
        )
        self.assertEqual(result["native_text_quality"], "PASS")
        self.assertEqual(result["page_family_primary"], "digital")
        self.assertEqual(result["visual_call_count"], 0)
        self.assertFalse(result["model_or_network_used"])

    def test_table_numeric_family_is_deterministic(self) -> None:
        result = diagnose_page(
            "",
            data_ownership="self",
            signals={"table_cell_count": 8, "numeric_token_ratio": 0.4},
        )
        self.assertEqual(result["page_family_primary"], "table_numeric")
        self.assertEqual(result["risk_level"], "high-risk")

    def test_formula_and_degradation_modifiers_are_preserved(self) -> None:
        result = diagnose_page(
            "",
            data_ownership="self",
            signals={
                "formula_token_ratio": 0.3,
                "blur_score": 0.5,
                "contrast_score": 0.2,
                "dense_symbol_ratio": 0.4,
            },
        )
        self.assertEqual(result["page_family_primary"], "formula")
        self.assertEqual(result["modifiers"], ["blur", "dense_symbols", "low_contrast"])

    def test_entrusted_diagnostic_never_grants_visibility(self) -> None:
        result = diagnose_page("", data_ownership="entrusted")
        self.assertFalse(result["provider_visibility_allowed"])
        self.assertIn("OWNERSHIP_BLOCKS_PROVIDER_VISIBILITY", result["reasons"])

    def test_unknown_signal_fails_closed(self) -> None:
        with self.assertRaisesRegex(OCRQualificationContractError, "DIAGNOSTIC_SIGNAL_UNKNOWN"):
            diagnose_page("", data_ownership="self", signals={"mystery": 1})


class ScoringAndQualificationTest(unittest.TestCase):
    def test_perfect_page_passes_five_metric_groups(self) -> None:
        card = scored()
        self.assertTrue(card["hard_gate_pass"])
        self.assertTrue(card["quality_pass"])
        self.assertEqual(card["metrics"]["DOCUMENT_PROCESSING_literal_accuracy"], 1.0)
        self.assertEqual(card["metrics"]["EVIDENCE_EXTRACTION_critical_exact_ratio"], 1.0)
        self.assertEqual(card["metrics"]["RETRIEVAL_structure_accuracy"], 1.0)
        self.assertEqual(card["metrics"]["RESEARCH_ANALYSIS_integrity_score"], 1.0)

    def test_provider_local_block_ids_do_not_change_score(self) -> None:
        value = output()
        value["ordered_blocks"][0]["block_id"] = "subject-block-7"
        value["ordered_blocks"][1]["block_id"] = "subject-block-9"
        value["critical_items"][0]["block_id"] = "subject-block-9"
        card = score_page(
            gold(), value, page_family="table_numeric", modifiers=[],
            output_lane="LITERAL_OCR", thresholds=thresholds(), runtime_receipt=runtime(),
        )
        self.assertTrue(card["quality_pass"])
        self.assertEqual(card["metrics"]["DOCUMENT_PROCESSING_block_text_accuracy"], 1.0)

    def test_decimal_and_number_labels_are_equivalent_for_exact_numeric_value(self) -> None:
        expected = gold()
        expected["critical_items"][0]["kind"] = "NUMBER"
        value = output()
        value["critical_items"][0]["kind"] = "DECIMAL"
        card = score_page(
            expected, value, page_family="table_numeric", modifiers=[],
            output_lane="LITERAL_OCR", thresholds=thresholds(), runtime_receipt=runtime(),
        )
        self.assertTrue(card["quality_pass"])
        self.assertEqual(card["metrics"]["EVIDENCE_EXTRACTION_critical_exact_ratio"], 1.0)
        self.assertEqual(card["metrics"]["EVIDENCE_EXTRACTION_silent_critical_errors"], 0)

    def test_visible_noncritical_item_is_not_unsupported_insertion(self) -> None:
        value = output()
        value["critical_items"].append(
            {"kind": "TOKEN", "value": "TITLE", "block_id": "b1", "confidence_state": "ASSERTED"}
        )
        card = score_page(
            gold(), value, page_family="table_numeric", modifiers=[],
            output_lane="LITERAL_OCR", thresholds=thresholds(), runtime_receipt=runtime(),
        )
        self.assertNotIn("UNSUPPORTED_CRITICAL_INSERTION", card["hard_failure_codes"])
        self.assertEqual(card["metrics"]["RESEARCH_ANALYSIS_unsupported_insertions"], 0)
        self.assertTrue(card["quality_pass"])

    def test_wrong_role_remains_structure_failure(self) -> None:
        value = output()
        value["ordered_blocks"][0]["role"] = "subtitle"
        card = score_page(
            gold(), value, page_family="table_numeric", modifiers=[],
            output_lane="LITERAL_OCR", thresholds=thresholds(), runtime_receipt=runtime(),
        )
        self.assertFalse(card["metrics"]["RETRIEVAL_block_order_exact"])
        self.assertFalse(card["quality_pass"])

    def test_silent_critical_error_is_harder_than_uncertainty(self) -> None:
        wrong = output()
        wrong["critical_items"][0]["value"] = "8"
        wrong_card = score_page(
            gold(), wrong, page_family="table_numeric", modifiers=[],
            output_lane="LITERAL_OCR", thresholds=thresholds(), runtime_receipt=runtime(),
        )
        uncertain = output()
        uncertain["critical_items"][0].update(value="7?", confidence_state="UNCERTAIN")
        uncertain_card = score_page(
            gold(), uncertain, page_family="table_numeric", modifiers=[],
            output_lane="LITERAL_OCR", thresholds=thresholds(), runtime_receipt=runtime(),
        )
        self.assertEqual(wrong_card["metrics"]["EVIDENCE_EXTRACTION_silent_critical_errors"], 1)
        self.assertIn("UNSUPPORTED_CRITICAL_INSERTION", wrong_card["hard_failure_codes"])
        self.assertEqual(uncertain_card["metrics"]["EVIDENCE_EXTRACTION_silent_critical_errors"], 0)
        self.assertEqual(uncertain_card["metrics"]["EVIDENCE_EXTRACTION_honest_uncertain"], 1)
        self.assertFalse(uncertain_card["quality_pass"])

    def test_critical_token_matching_preserves_duplicate_multiplicity(self) -> None:
        duplicate_gold = gold()
        duplicate_gold["critical_items"].append(
            copy.deepcopy(duplicate_gold["critical_items"][0])
        )
        card = score_page(
            duplicate_gold,
            output(),
            page_family="table_numeric",
            modifiers=[],
            output_lane="LITERAL_OCR",
            thresholds=thresholds(),
            runtime_receipt=runtime(),
        )
        self.assertEqual(card["metrics"]["EVIDENCE_EXTRACTION_critical_exact_ratio"], 0.5)
        self.assertEqual(card["metrics"]["EVIDENCE_EXTRACTION_silent_critical_errors"], 1)
        self.assertEqual(card["metrics"]["RESEARCH_ANALYSIS_unsupported_insertions"], 0)

    def test_source_image_mismatch_fails_anchor_gate(self) -> None:
        value = output()
        value["source_image_sha256"] = SHA_A
        card = score_page(
            gold(), value, page_family="table_numeric", modifiers=[],
            output_lane="LITERAL_OCR", thresholds=thresholds(), runtime_receipt=runtime(),
        )
        self.assertFalse(card["anchor_match"])
        self.assertIn("SOURCE_PAGE_IMAGE_ANCHOR_MISMATCH", card["hard_failure_codes"])

    def test_missing_presend_sidecar_fails_hard_gate(self) -> None:
        card = scored(pre_send_sidecar_present=False)
        self.assertIn("PRE_SEND_SIDECAR_MISSING", card["hard_failure_codes"])

    def test_exact_cell_denominator_rejects_duplicate_and_missing(self) -> None:
        cards = [
            {**scored(candidate=candidate), "page_id": page}
            for page in ("page-01", "page-02")
            for candidate in ("a", "b")
        ]
        self.assertEqual(
            validate_cell_denominator(
                cards, expected_page_ids=["page-01", "page-02"], expected_candidate_keys=["a", "b"]
            )["result"],
            "PASS",
        )
        broken = cards[:-1] + [copy.deepcopy(cards[0])]
        self.assertEqual(
            validate_cell_denominator(
                broken, expected_page_ids=["page-01", "page-02"], expected_candidate_keys=["a", "b"]
            )["result"],
            "FAIL_DENOMINATOR",
        )

    def test_usage_api_requires_actual_cost_and_returned_model(self) -> None:
        good = evaluate_accounting(
            "usage_based_api",
            {"requested_model": "m", "returned_model": "m", "cost_status": "ACTUAL", "actual_cost": "0.01"},
        )
        pending = evaluate_accounting(
            "usage_based_api",
            {"requested_model": "m", "returned_model": "m", "cost_status": "PENDING", "actual_cost": None},
        )
        self.assertEqual(good["verdict"], "PASS")
        self.assertEqual(pending["verdict"], "NOT_ASSESSED")

    def test_subscription_cost_is_na_not_zero(self) -> None:
        valid = {
            "requested_model": "gpt-x",
            "returned_model": "PROVIDER_NOT_EXPOSED",
            "actual_cost": None,
            "cost_status": "NOT_APPLICABLE",
            "subscription_usage_status": "NOT_EXPOSED",
        }
        self.assertEqual(evaluate_accounting("subscription_cli", valid)["verdict"], "PASS")
        invalid = {**valid, "actual_cost": "0"}
        self.assertEqual(evaluate_accounting("subscription_cli", invalid)["verdict"], "FAIL")

    def test_primary_requires_frozen_material_improvement(self) -> None:
        cell = project_family_qualification(
            [scored()],
            candidate_key="claude_api_visual",
            channel_kind="usage_based_api",
            profile_id="profile-a",
            requested_model="claude-a",
            page_family="table_numeric",
            modifiers=[],
            output_lane="LITERAL_OCR",
            thresholds=thresholds(),
            baseline_literal_accuracy=0.90,
            evidence_refs=["receipt:a"],
        )
        self.assertEqual(cell["role_eligibility"], "PRIMARY")
        self.assertFalse(cell["default_enabled"])
        self.assertFalse(cell["activation_authorized"])

    def test_identity_gap_keeps_candidate_disabled(self) -> None:
        card = scored(identity_verdict="PROVIDER_NOT_EXPOSED")
        cell = project_family_qualification(
            [card], candidate_key="claude_api_visual", channel_kind="usage_based_api",
            profile_id="profile-a", requested_model="claude-a", page_family="table_numeric",
            modifiers=[], output_lane="LITERAL_OCR", thresholds=thresholds(),
            baseline_literal_accuracy=0.90, evidence_refs=["receipt:a"],
        )
        self.assertEqual(cell["identity_verdict"], "PROVIDER_NOT_EXPOSED")
        self.assertEqual(cell["role_eligibility"], "KEEP_DISABLED")

    def test_adjacent_family_is_not_inferred(self) -> None:
        cell = project_family_qualification(
            [scored()], candidate_key="claude_api_visual", channel_kind="usage_based_api",
            profile_id="profile-a", requested_model="claude-a", page_family="formula",
            modifiers=[], output_lane="LITERAL_OCR", thresholds=thresholds(),
            baseline_literal_accuracy=0.90, evidence_refs=[],
        )
        self.assertEqual(cell["role_eligibility"], "NOT_ASSESSED")
        self.assertEqual(cell["sample_denominator"], 0)


class RoutingAndReportingTest(unittest.TestCase):
    def _primary_cell(self) -> dict:
        return project_family_qualification(
            [scored()], candidate_key="claude_api_visual", channel_kind="usage_based_api",
            profile_id="profile-a", requested_model="claude-a", page_family="table_numeric",
            modifiers=[], output_lane="LITERAL_OCR", thresholds=thresholds(),
            baseline_literal_accuracy=0.90, evidence_refs=["receipt:a"],
        )

    def test_native_text_always_zero_visual_calls(self) -> None:
        decision = explain_route(
            {"native_text_quality": "PASS", "page_family_primary": "digital", "modifiers": [], "data_ownership": "public-safe"},
            [self._primary_cell()], output_lane="LITERAL_OCR",
        )
        self.assertEqual(decision["route"], "LOCAL_TEXT_NO_VISUAL_CALL")
        self.assertEqual(decision["visual_call_count"], 0)

    def test_qualification_is_recommendation_not_activation(self) -> None:
        decision = explain_route(
            {"native_text_quality": "FAIL", "page_family_primary": "table_numeric", "modifiers": [], "data_ownership": "public-safe"},
            [self._primary_cell()], output_lane="LITERAL_OCR",
        )
        self.assertEqual(decision["route"], "QUALIFIED_RECOMMENDATION_KEEP_DISABLED")
        self.assertEqual(decision["recommended_candidate"], "claude_api_visual")
        self.assertIsNone(decision["selected_candidate"])
        self.assertEqual(decision["visual_call_count"], 0)
        self.assertFalse(decision["activation_authorized"])

    def test_modifier_mismatch_is_unassessed_not_guessed(self) -> None:
        decision = explain_route(
            {"native_text_quality": "FAIL", "page_family_primary": "table_numeric", "modifiers": ["blur"], "data_ownership": "self"},
            [self._primary_cell()], output_lane="LITERAL_OCR",
        )
        self.assertEqual(decision["route"], "HUMAN_REVIEW_KEEP_DISABLED")
        self.assertIn("EXACT_QUALIFICATION_CELL_UNTESTED", decision["reason_codes"])

    def test_unavailable_channel_never_silently_falls_back(self) -> None:
        decision = explain_route(
            {"native_text_quality": "FAIL", "page_family_primary": "table_numeric", "modifiers": [], "data_ownership": "self"},
            [self._primary_cell()], output_lane="LITERAL_OCR",
            runtime_availability={"claude_api_visual": False},
        )
        self.assertIn("SILENT_FALLBACK_FORBIDDEN", decision["reason_codes"])
        self.assertEqual(decision["visual_call_count"], 0)

    def test_compare_page_never_votes_or_mutates_candidates(self) -> None:
        left = output(candidate="claude_api_visual")
        right = output(candidate="deepseek_ocr_specialist_baseline")
        right["literal_text"] = "TITLE\nValue 8."
        before = copy.deepcopy([left, right])
        report = compare_page(
            [left, right],
            [scored(candidate="claude_api_visual"), scored(candidate="deepseek_ocr_specialist_baseline")],
        )
        self.assertTrue(report["disagreement"]["literal_text"])
        self.assertFalse(report["disagreement"]["automatic_vote_or_merge"])
        self.assertEqual([left, right], before)

    def test_compare_page_rejects_duplicate_scorecard(self) -> None:
        left = output(candidate="claude_api_visual")
        right = output(candidate="deepseek_ocr_specialist_baseline")
        duplicate = scored(candidate="claude_api_visual")
        with self.assertRaisesRegex(
            OCRQualificationContractError,
            "COMPARE_PAGE_SCORECARD_DENOMINATOR_MISMATCH",
        ):
            compare_page([left, right], [duplicate, copy.deepcopy(duplicate)])

    def test_all_keep_disabled_matrix_can_still_be_method_pass(self) -> None:
        cell = self._primary_cell()
        disabled = {**cell, "role_eligibility": "KEEP_DISABLED", "limitation_codes": ["NO_MATERIAL_GAIN"]}
        summary = summarize_qualification_matrix([disabled])
        self.assertEqual(summary["method_result"], "PASS")
        self.assertFalse(summary["task_pass_requires_any_candidate_enabled"])
        self.assertTrue(summary["all_default_disabled"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
