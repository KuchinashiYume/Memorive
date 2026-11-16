"""Zero-side-effect Retrieval vision request and wrapper tests."""

from __future__ import annotations

import hashlib
import unittest

from memorive.models.gateway.vision_qualification import VisionRequestContractError, build_claude_api_projection, build_openai_api_image_projection, build_openai_cli_image_projection, build_presend_receipt, evaluate_presend_readiness, inspect_cli_event_stream, not_configured_local_capability, project_terminal_evidence, vision_channel_doctor
SHA_A = "A" * 64
SHA_B = "B" * 64
SHA_C = "C" * 64
SHA_D = "D" * 64
SHA_E = "E" * 64
TASK_TEXT = "Transcribe one page as JSON."
TASK_SHA = hashlib.sha256(TASK_TEXT.encode("utf-8")).hexdigest().upper()


def page(candidate: str = "claude_api_visual") -> dict:
    subscription = candidate == "openai_subscription_cli_visual"
    openai_api = candidate == "openai_api_visual"
    return {
        "schema_version": "PageRecognitionPageSubjectInput-v1",
        "pack_id": "pack-a",
        "attempt_id": f"attempt-{candidate}",
        "attempt_ordinal": 1,
        "page_id": "page-01",
        "source_document_id": "doc-a",
        "source_page_number": 1,
        "source_file_sha256": SHA_A,
        "render_recipe_sha256": SHA_B,
        "source_image_sha256": SHA_C,
        "image_ref": "sandbox/images/page-01.png",
        "image": {"mime_type": "image/png", "width_px": 1200, "height_px": 1600, "dpi": 200, "color_mode": "RGB", "crop_box": None},
        "data_ownership": "public-safe",
        "rights_custody_verdict": "ALLOWED_PUBLIC_SAFE",
        "provider_visibility": "SINGLE_PAGE_IMAGE_ONLY",
        "page_family_primary": "table_numeric",
        "modifiers": [],
        "semantic_task": {"ref": "prompt:v4", "sha256": TASK_SHA},
        "channel_wrapper": {"ref": "wrapper:v1", "sha256": SHA_B},
        "output_schema": {"ref": "schema:v1", "sha256": SHA_C},
        "candidate": {
            "candidate_key": candidate,
            "channel_kind": "subscription_cli" if subscription else "usage_based_api",
            "provider": "openai_subscription_cli" if subscription else "openai" if openai_api else "anthropic",
            "profile_id": "profile-a",
            "requested_model": "gpt-test" if subscription or openai_api else "claude-test",
            "route": "controlled-route",
            "region": "test-region",
            "egress": "test-egress",
        },
        "visibility": {"gold_visible": False, "other_candidate_outputs_visible": False, "hidden_text_visible": False, "whole_pdf_visible": False},
        "retry_count": 0,
        "timeout_seconds": 120,
        "token_ceiling": None if subscription else 1000,
        "cash_cost_ceiling": None if subscription else "1.00",
        "subscription_unit_ceiling": 1 if subscription else None,
        "credential_env_name": None if subscription else "OPENAI_API_KEY" if openai_api else "MEMORIVE_ANTHROPIC_KEY",
        "authorization_ref": "authority:test",
        "external_request_authorized": True,
    }


def binding(candidate: str = "claude_api_visual") -> dict:
    subscription = candidate == "openai_subscription_cli_visual"
    openai_api = candidate == "openai_api_visual"
    source = page(candidate)
    return {
        "pack_id": source["pack_id"],
        "attempt_id": source["attempt_id"],
        "page_id": source["page_id"],
        "source_document_id": source["source_document_id"],
        "source_page_number": source["source_page_number"],
        "source_file_sha256": source["source_file_sha256"],
        "render_recipe_sha256": source["render_recipe_sha256"],
        "candidate_key": source["candidate"]["candidate_key"],
        "channel_kind": source["candidate"]["channel_kind"],
        "provider": source["candidate"]["provider"],
        "profile_id": "profile-a",
        "requested_model": source["candidate"]["requested_model"],
        "route": "controlled-route",
        "region": "test-region",
        "egress": "test-egress",
        "semantic_task_sha256": TASK_SHA,
        "channel_wrapper_sha256": SHA_B,
        "output_schema_sha256": SHA_C,
        "image_sha256": SHA_C,
        "runner_sha256": SHA_D,
        "scorer_sha256": SHA_E,
        "behavior_sha256": SHA_A,
        "authorization_ref": "authority:test",
        "retry_count": 0,
        "call_ordinal": 1,
        "call_ceiling": 2,
        "timeout_seconds": 120,
        "credential_env_name": None if subscription else "OPENAI_API_KEY" if openai_api else "MEMORIVE_ANTHROPIC_KEY",
        "token_ceiling": None if subscription else 1000,
        "cash_cost_ceiling": None if subscription else "1.00",
        "subscription_unit_ceiling": 1 if subscription else None,
    }


class PreSendContractTest(unittest.TestCase):
    def test_valid_api_presend_is_metadata_only(self) -> None:
        readiness = evaluate_presend_readiness(page(), binding())
        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["external_request_start_count"], 0)
        receipt = build_presend_receipt(page(), binding())
        self.assertEqual(receipt["request_disposition"], "READY_TO_SEND_METADATA_ONLY")
        self.assertEqual(receipt["credential_read_count"], 0)
        self.assertEqual(receipt["external_request_start_count"], 0)
        self.assertRegex(receipt["receipt_sha256"], r"^[0-9A-F]{64}$")

    def test_absent_authority_blocks_before_process_start(self) -> None:
        value = page()
        value["external_request_authorized"] = False
        result = evaluate_presend_readiness(value, binding())
        self.assertFalse(result["ready"])
        self.assertIn("EXTERNAL_REQUEST_AUTHORITY_ABSENT", result["blocker_codes"])
        self.assertEqual(result["physical_process_start_count"], 0)

    def test_gold_or_peer_visibility_is_schema_blocked(self) -> None:
        value = page()
        value["visibility"]["gold_visible"] = True
        result = evaluate_presend_readiness(value, binding())
        self.assertFalse(result["ready"])
        self.assertIn("PAGE_SUBJECT_INPUT_SCHEMA_INVALID", result["blocker_codes"])
        self.assertIn("FORBIDDEN_VISIBILITY_PRESENT", result["blocker_codes"])

    def test_blocked_rights_cannot_be_sent_even_with_safe_label(self) -> None:
        value = page()
        value["rights_custody_verdict"] = "BLOCKED"
        result = evaluate_presend_readiness(value, binding())
        self.assertIn("RIGHTS_OWNERSHIP_MISMATCH", result["blocker_codes"])

    def test_missing_route_and_hash_bindings_fail_closed(self) -> None:
        value = binding()
        value["route"] = None
        value["runner_sha256"] = "bad"
        result = evaluate_presend_readiness(page(), value)
        self.assertIn("BINDING_ROUTE_MISMATCH", result["blocker_codes"])
        self.assertIn("RUNNER_SHA256_INVALID", result["blocker_codes"])

    def test_page_and_candidate_binding_drift_fails_closed(self) -> None:
        value = binding()
        value["page_id"] = "page-02"
        value["candidate_key"] = "openai_subscription_cli_visual"
        result = evaluate_presend_readiness(page(), value)
        self.assertIn("BINDING_PAGE_ID_MISMATCH", result["blocker_codes"])
        self.assertIn("BINDING_CANDIDATE_KEY_MISMATCH", result["blocker_codes"])

    def test_subscription_cash_ceiling_is_na_and_units_are_bound(self) -> None:
        receipt = build_presend_receipt(
            page("openai_subscription_cli_visual"),
            binding("openai_subscription_cli_visual"),
        )
        self.assertIsNone(receipt["cash_cost_ceiling"])
        self.assertEqual(receipt["subscription_unit_ceiling"], 1)
        self.assertIsNone(receipt["credential_env_name"])


class WrapperProjectionTest(unittest.TestCase):
    def test_claude_projection_contains_one_image_and_no_tools(self) -> None:
        source = page()
        receipt = build_presend_receipt(source, binding())
        projection = build_claude_api_projection(
            source, receipt, semantic_task_text=TASK_TEXT
        )
        self.assertEqual(projection["transport"], "MODEL_GATEWAY_CONTROLLED_RELAY_ONLY")
        self.assertEqual(projection["single_page_image"]["sha256"], SHA_C)
        self.assertEqual(projection["tools"], [])
        self.assertFalse(projection["web"])
        self.assertFalse(projection["external_request_started"])

    def test_openai_api_projection_is_one_image_responses_api_and_no_tools(self) -> None:
        source = page("openai_api_visual")
        receipt = build_presend_receipt(source, binding("openai_api_visual"))
        projection = build_openai_api_image_projection(
            source,
            receipt,
            semantic_task_text=TASK_TEXT,
            reasoning_effort="xhigh",
        )
        self.assertEqual(projection["endpoint"], "/v1/responses")
        self.assertEqual(projection["input"]["single_page_image"]["sha256"], SHA_C)
        self.assertEqual(projection["input"]["single_page_image"]["detail"], "original")
        self.assertEqual(projection["reasoning"], {"effort": "xhigh"})
        self.assertEqual(projection["thinking_parameter"], "NOT_APPLICABLE_OPENAI_RESPONSES_API")
        self.assertFalse(projection["structured_output"]["strict"])
        self.assertFalse(projection["pro_mode"])
        self.assertEqual(projection["tools"], [])
        self.assertFalse(projection["external_request_started"])

    def test_openai_api_projection_rejects_unknown_reasoning_effort(self) -> None:
        source = page("openai_api_visual")
        receipt = build_presend_receipt(source, binding("openai_api_visual"))
        with self.assertRaisesRegex(
            VisionRequestContractError, "OPENAI_REASONING_EFFORT_INVALID"
        ):
            build_openai_api_image_projection(
                source,
                receipt,
                semantic_task_text=TASK_TEXT,
                reasoning_effort="extreme",
            )

    def test_openai_cli_projection_is_not_executed_and_is_read_only(self) -> None:
        source = page("openai_subscription_cli_visual")
        receipt = build_presend_receipt(source, binding("openai_subscription_cli_visual"))
        projection = build_openai_cli_image_projection(
            source,
            receipt,
            semantic_task_text=TASK_TEXT,
            output_path_ref="sandbox/output/page-01.json",
        )
        self.assertEqual(projection["image_count"], 1)
        self.assertIn("--image", projection["argv"])
        self.assertEqual(projection["workspace_policy"], "isolated_create_only_output")
        self.assertEqual(projection["physical_process_start_count"], 0)
        self.assertFalse(projection["shell"])

    def test_wrapper_refuses_candidate_mismatch(self) -> None:
        source = page()
        receipt = build_presend_receipt(source, binding())
        with self.assertRaisesRegex(VisionRequestContractError, "WRAPPER_CANDIDATE_MISMATCH"):
            build_openai_cli_image_projection(
                source,
                receipt,
                semantic_task_text=TASK_TEXT,
                output_path_ref="out.json",
            )

    def test_wrapper_refuses_semantic_task_text_drift(self) -> None:
        source = page()
        receipt = build_presend_receipt(source, binding())
        with self.assertRaisesRegex(VisionRequestContractError, "SEMANTIC_TASK_HASH_MISMATCH"):
            build_claude_api_projection(
                source, receipt, semantic_task_text="Different task text."
            )

    def test_wrapper_refuses_mutated_presend_receipt(self) -> None:
        source = page()
        receipt = build_presend_receipt(source, binding())
        receipt["route"] = "drifted-route"
        with self.assertRaisesRegex(
            VisionRequestContractError, "PRESEND_RECEIPT_HASH_MISMATCH"
        ):
            build_claude_api_projection(
                source, receipt, semantic_task_text=TASK_TEXT
            )

    def test_cli_event_stream_rejects_tool_and_write_events(self) -> None:
        result = inspect_cli_event_stream(
            [
                {"type": "command_execution", "command": "whoami"},
                {"type": "final_output", "path_ref": "out.json"},
            ],
            allowed_output_path_ref="out.json",
        )
        self.assertEqual(result["result"], "SECURITY_CONTRACT_VIOLATION")
        self.assertTrue(any("FORBIDDEN_EVENT" in item for item in result["violation_codes"]))
        self.assertFalse(result["fallback_authorized"])

    def test_cli_event_stream_accepts_one_final_output_only(self) -> None:
        result = inspect_cli_event_stream(
            [{"type": "status"}, {"type": "final_output", "path_ref": "out.json"}],
            allowed_output_path_ref="out.json",
        )
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["final_output_count"], 1)


class DoctorAndTerminalTest(unittest.TestCase):
    def test_doctor_does_not_probe_runtime_network_or_credentials(self) -> None:
        result = vision_channel_doctor(
            page(),
            binding(),
            runtime_metadata={
                "runtime_version": "offline-recorded",
                "profile_hash": SHA_A,
                "behavior_hash": SHA_B,
                "output_create_only": True,
            },
        )
        self.assertEqual(result["result"], "READY_METADATA_ONLY")
        self.assertEqual(result["image_body_read_count"], 0)
        self.assertEqual(result["credential_read_count"], 0)
        self.assertEqual(result["dns_or_network_request_count"], 0)
        self.assertEqual(result["physical_process_start_count"], 0)

    def test_subscription_terminal_preserves_provider_not_exposed(self) -> None:
        source = page("openai_subscription_cli_visual")
        presend = build_presend_receipt(source, binding("openai_subscription_cli_visual"))
        terminal = project_terminal_evidence(
            presend,
            {
                "terminal_state": "completed",
                "returned_model": "PROVIDER_NOT_EXPOSED",
                "provider_request_id_hash": None,
                "usage": None,
                "cost_status": "NOT_APPLICABLE",
                "actual_cost": None,
                "subscription_usage_status": "NOT_EXPOSED",
                "latency_ms": 100,
                "output_hash": SHA_A,
                "error_hash": None,
                "disposition": "RECORDED",
            },
        )
        self.assertEqual(terminal["identity_verdict"], "PROVIDER_NOT_EXPOSED")
        self.assertEqual(terminal["accounting_verdict"], "PASS")

    def test_api_missing_actual_cost_remains_not_assessed(self) -> None:
        presend = build_presend_receipt(page(), binding())
        terminal = project_terminal_evidence(
            presend,
            {
                "terminal_state": "completed",
                "returned_model": "claude-test",
                "provider_request_id_hash": SHA_A,
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "cost_status": "ACTUAL",
                "actual_cost": None,
                "subscription_usage_status": None,
                "latency_ms": 100,
                "output_hash": SHA_B,
                "error_hash": None,
                "disposition": "RECORDED",
            },
        )
        self.assertEqual(terminal["accounting_verdict"], "NOT_ASSESSED")
        self.assertIn("API_ACTUAL_COST_MISSING", terminal["limitation_codes"])

    def test_error_terminal_requires_error_hash_and_degrades_resource(self) -> None:
        presend = build_presend_receipt(page(), binding())
        terminal = project_terminal_evidence(
            presend,
            {
                "terminal_state": "ERROR",
                "returned_model": "claude-test",
                "provider_request_id_hash": SHA_A,
                "usage": None,
                "cost_status": "PENDING",
                "actual_cost": None,
                "subscription_usage_status": None,
                "latency_ms": 100,
                "output_hash": None,
                "error_hash": SHA_B,
                "disposition": "RECORDED_ERROR",
            },
        )
        self.assertEqual(terminal["resource_verdict"], "FAIL")
        self.assertEqual(terminal["terminal"]["terminal_state"], "ERROR")

    def test_completed_terminal_rejects_error_hash(self) -> None:
        presend = build_presend_receipt(page(), binding())
        with self.assertRaisesRegex(
            VisionRequestContractError, "COMPLETED_TERMINAL_HASH_CONFLICT"
        ):
            project_terminal_evidence(
                presend,
                {
                    "terminal_state": "completed",
                    "returned_model": "claude-test",
                    "provider_request_id_hash": SHA_A,
                    "usage": None,
                    "cost_status": "PENDING",
                    "actual_cost": None,
                    "subscription_usage_status": None,
                    "latency_ms": 100,
                    "output_hash": SHA_B,
                    "error_hash": SHA_C,
                    "disposition": "RECORDED",
                },
            )

    def test_local_unconfigured_is_not_assessed_without_cloud_fallback(self) -> None:
        report = not_configured_local_capability()
        self.assertEqual(report["state"], "NOT_CONFIGURED_NOT_ASSESSED")
        self.assertEqual(report["download_attempt_count"], 0)
        self.assertEqual(report["network_request_count"], 0)
        self.assertFalse(report["cloud_fallback_authorized"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
