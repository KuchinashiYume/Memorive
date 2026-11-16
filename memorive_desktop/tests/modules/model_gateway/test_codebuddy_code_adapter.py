from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memorive.models.gateway.execution_core.adapters.codebuddy_code import CodeBuddyCodePolicy, CodeBuddyCodeViolation
from memorive.models.gateway.execution_core.adapters.codebuddy_code import CodeBuddyCodeAdapter
from memorive.models.gateway.execution_core.contracts import canonical_sha256  # noqa: E402
from memorive.models.gateway.execution_core.runner import IsolatedJobRunner  # noqa: E402
from memorive.models.gateway.execution_core.workspace import JobWorkspace, WorkspaceViolation  # noqa: E402


CORE_SCHEMA_PATH = REPO_ROOT / "memorive" / "models" / "gateway" / "execution_core" / "schemas" / "execution_execution_core.schema.json"
CALL_SCHEMA_PATH = REPO_ROOT / "memorive" / "models" / "gateway" / "execution_core" / "schemas" / "external_model_call_receipt.schema.json"
CORE_SCHEMA = json.loads(CORE_SCHEMA_PATH.read_text(encoding="utf-8"))
CALL_SCHEMA = json.loads(CALL_SCHEMA_PATH.read_text(encoding="utf-8"))
FAKE = Path(__file__).with_name("fake_codebuddy.py").resolve()
PYTHON = str(Path(sys.executable).resolve())
FIXED_TIME = "2026-08-14T09:23:04Z"
FIXED_CLOCK = lambda: FIXED_TIME
MODEL = "deepseek-v4-pro"
PROFILE = "codebuddy_user_login"
PAYLOAD_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "value"],
    "properties": {
        "status": {"const": "ok"},
        "value": {"type": "string"},
    },
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def environment_names() -> tuple[str, ...]:
    names = {"CODEBUDDY_RUNTIME_DIR", "TEMP", "TMP"}
    if os.name == "nt":
        names.add("SYSTEMROOT")
    return tuple(sorted(names))


def policy() -> CodeBuddyCodePolicy:
    names = environment_names()
    return CodeBuddyCodePolicy(
        launcher=PYTHON,
        command_prefix=(str(FAKE),),
        exact_version="2.136.0",
        launcher_sha256=sha256(Path(PYTHON)),
        wrapper_sha256=sha256(FAKE),
        entrypoint_sha256=sha256(FAKE),
        help_sha256=sha256(FAKE),
        runner_sha256=sha256(REPO_ROOT / "memorive" / "models" / "gateway" / "execution_core" / "runner.py"),
        model_catalog_sha256=sha256(FAKE),
        allowed_models=(MODEL, "deepseek-v4-flash"),
        allowed_profiles=(PROFILE,),
        profile_environment_names={PROFILE: names},
        profile_evidence_classes={PROFILE: "subscription_cli"},
        environment_allowlist=names,
        user_login_profile=PROFILE,
        route_id="tencent_codebuddy_code_v1",
        provider_region="NOT_EXPOSED",
        egress_identity="NOT_APPLICABLE_OFFLINE_Q2",
        max_timeout_seconds=5,
        max_raw_cap_bytes=65536,
        component_paths={
            "launcher_sha256": PYTHON,
            "wrapper_sha256": str(FAKE),
            "entrypoint_sha256": str(FAKE),
            "help_sha256": str(FAKE),
            "runner_sha256": str(REPO_ROOT / "memorive" / "models" / "gateway" / "execution_core" / "runner.py"),
            "model_catalog_sha256": str(FAKE),
        },
    )


def login_fact(**overrides) -> dict:
    value = {
        "profile_id": PROFILE,
        "status": "LOGIN_USER_CONFIRMED_SUCCESS",
        "source": "USER_COORDINATION_UPDATE",
        "confirmed_at": FIXED_TIME,
        "login_domain": "NOT_SAFELY_OBSERVED",
        "credential_material_read": False,
        "entitlement_status": "ENTITLEMENT_UNVERIFIED/NOT_REQUIRED_FOR_Q2",
    }
    value.update(overrides)
    return value


def environment(workspace: JobWorkspace) -> dict[str, str]:
    values = {
        "CODEBUDDY_RUNTIME_DIR": str(workspace.scratch_root / "codebuddy-runtime"),
        "TEMP": str(workspace.scratch_root),
        "TMP": str(workspace.scratch_root),
    }
    if os.name == "nt":
        values["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return values


def sidecar(
    adapter: CodeBuddyCodeAdapter,
    *,
    attempt_id: str,
    model: str,
    prompt: bytes,
    output_mode: str = "stream-json",
    schema: dict = PAYLOAD_SCHEMA,
) -> dict:
    output_hash = canonical_sha256(schema)
    prompt_hash = hashlib.sha256(prompt).hexdigest().upper()
    behavior_hash = adapter.behavior_hash(
        model=model,
        output_contract_sha256=output_hash,
        access_profile=PROFILE,
        output_mode=output_mode,
    )
    return {
        "schema_version": "1.0",
        "record_type": "call_binding_receipt",
        "binding_mode": "PROSPECTIVE_STRICT",
        "recorded_at": FIXED_TIME,
        "phase_id": "Execution",
        "task_id": "Integration",
        "run_id": "CODEBUDDY_CODE_OFFLINE_FIXTURE",
        "attempt_id": attempt_id,
        "call_id": f"call_{attempt_id}",
        "retry_of_call_id": None,
        "role_family": "card_distiller_primary",
        "evidence_class": "subscription_cli",
        "request_disposition": "BLOCKED_BEFORE_SEND",
        "profile_identity": {
            "profile_id": PROFILE,
            "profile_version": "fixture-v1",
            "profile_hash": "D" * 64,
            "identity_status": "RECORDED_UNVERIFIED",
        },
        "model_identity": {
            "provider": "Tencent Cloud",
            "requested_model": model,
            "returned_model": None,
            "returned_model_status": "UNKNOWN",
            "provider_request_id_hash": None,
        },
        "route_identity": {
            "route_id": "tencent_codebuddy_code_v1",
            "route_snapshot_id": "route_offline_fixture_001",
            "provider_region": "NOT_EXPOSED",
            "egress_identity": "NOT_APPLICABLE_OFFLINE_Q2",
            "egress_identity_kind": "NOT_APPLICABLE",
            "egress_receipt_id": None,
            "identity_status": "RECORDED_UNVERIFIED",
        },
        "artifact_bindings": {
            "source_snapshot_id": "source_fixture_001",
            "source_commit": "49b43332b74cbdee8950e96c2b88461eb100f080",
            "source_tree_hash": "E" * 64,
            "runner_hash": adapter.policy.runner_sha256,
            "prompt_hash": prompt_hash,
            "output_contract_hash": output_hash,
            "scorer_hash": None,
            "input_manifest_hash": "F" * 64,
            "input_hash": prompt_hash,
            "output_hash": None,
            "error_hash": None,
        },
        "accounting": {
            "request_started_at": None,
            "response_completed_at": None,
            "latency_ms": None,
            "input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "total_tokens": None,
            "actual_cost": None,
            "estimated_cost": None,
            "currency": None,
            "usage_status": "UNKNOWN",
            "pricing_status": "NOT_APPLICABLE",
        },
        "outcome": {
            "call_status": "BLOCKED_BEFORE_SEND",
            "error_code": "PRE_SEND_FROZEN_READY",
        },
        "evidence_status": "EXACT_RECORDED",
        "qualification_eligibility": "NOT_APPLICABLE",
        "reason_codes": ["PRE_SEND_FROZEN_READY"],
        "source_evidence_refs": [
            {
                "ref": "fixture://DEEPCODE-ADAPTER/pre-send",
                "sha256": "1" * 64,
                "evidence_status": "EXACT_RECORDED",
            }
        ],
        "extensions": {
            "pre_send_state": "FROZEN_READY",
            "behavior_hash": behavior_hash,
            "credential_env_name": None,
            "session_body_read": False,
        },
    }


class Harness:
    def __init__(self, *, mode: str = "valid", output_mode: str = "stream-json"):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output_mode = output_mode
        self.adapter = CodeBuddyCodeAdapter(policy(), CORE_SCHEMA, CALL_SCHEMA, clock=FIXED_CLOCK)
        self.workspace = JobWorkspace.create(
            self.root,
            "job_codebuddy_fixture",
            CORE_SCHEMA,
            process_allowlist=(PYTHON,),
            environment_allowlist=environment_names(),
            extra_read_roots=(Path(PYTHON), FAKE),
            protected_roots=(REPO_ROOT,),
            selected_adapter_id="codebuddy_code",
            selected_manifest_hash="A" * 64,
            expected_attempt_id="attempt_001",
            contract_timeout_seconds=5,
            raw_cap_bytes=65536,
            input_hashes=(),
            clock=FIXED_CLOCK,
        )
        self.prompt = json.dumps(
            {"mode": mode, "value": "保留 Unicode Ω；&& | $(x) `y`"},
            ensure_ascii=False,
        ).encode("utf-8")

    def invocation(self, **overrides):
        attempt_id = overrides.pop("attempt_id", "attempt_001")
        model = overrides.pop("model", MODEL)
        output_mode = overrides.pop("output_mode", self.output_mode)
        prompt = overrides.pop("prompt", self.prompt)
        evidence = overrides.pop(
            "pre_send_sidecar",
            sidecar(
                self.adapter,
                attempt_id=attempt_id,
                model=model,
                prompt=prompt,
                output_mode=output_mode,
            ),
        )
        return self.adapter.build_invocation(
            self.workspace,
            attempt_id=attempt_id,
            model=model,
            access_profile=overrides.pop("access_profile", PROFILE),
            prompt=prompt,
            output_schema=overrides.pop("output_schema", PAYLOAD_SCHEMA),
            pre_send_sidecar=evidence,
            login_fact=overrides.pop("login_fact", login_fact()),
            environment=overrides.pop("environment", environment(self.workspace)),
            timeout_seconds=overrides.pop("timeout_seconds", 2),
            raw_cap_bytes=overrides.pop("raw_cap_bytes", 65536),
            output_mode=output_mode,
            **overrides,
        )

    def run(self, **overrides):
        invocation = self.invocation(**overrides)
        process = IsolatedJobRunner(CORE_SCHEMA, clock=FIXED_CLOCK).run(
            self.workspace, invocation.process_spec
        )
        return invocation, process

    def close(self):
        self.temp.cleanup()


class CodeBuddyManifestAndCommandTest(unittest.TestCase):
    def test_manifest_is_valid_disabled_and_q2_only(self):
        manifest = CodeBuddyCodeAdapter(policy(), CORE_SCHEMA, CALL_SCHEMA, clock=FIXED_CLOCK).build_manifest()
        self.assertEqual(manifest["adapter_id"], "codebuddy_code")
        self.assertEqual(manifest["extensions"]["activation"], "DISABLED")
        self.assertEqual(manifest["extensions"]["external_prompt_request_limit"], 0)
        self.assertFalse(manifest["extensions"]["model_text_self_report_used"])

    def test_stream_command_is_headless_exact_model_and_tool_empty(self):
        h = Harness()
        try:
            argv = h.invocation().process_spec.argv
            self.assertIn("-p", argv)
            self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
            self.assertEqual(argv[argv.index("--model") + 1], MODEL)
            self.assertEqual(argv[argv.index("--tools") + 1], "")
            self.assertEqual(argv[argv.index("--permission-mode") + 1], "plan")
            self.assertIn("--strict-mcp-config", argv)
            self.assertIn("--no-session-persistence", argv)
            for forbidden in ("-y", "--dangerously-skip-permissions", "--worktree", "--fallback-model"):
                self.assertNotIn(forbidden, argv)
            self.assertEqual(h.invocation().process_spec.stdin_bytes, h.prompt)
        finally:
            h.close()

    def test_json_mode_binds_json_schema_and_changes_behavior(self):
        h = Harness(output_mode="json")
        try:
            invocation = h.invocation()
            argv = invocation.process_spec.argv
            self.assertEqual(argv[argv.index("--output-format") + 1], "json")
            schema_arg = json.loads(argv[argv.index("--json-schema") + 1])
            self.assertEqual(schema_arg, PAYLOAD_SCHEMA)
            stream_hash = h.adapter.behavior_hash(
                model=MODEL,
                output_contract_sha256=canonical_sha256(PAYLOAD_SCHEMA),
                access_profile=PROFILE,
            )
            self.assertNotEqual(stream_hash, invocation.behavior_hash)
        finally:
            h.close()

    def test_text_and_unknown_model_or_profile_fail_closed(self):
        h = Harness()
        try:
            cases = (
                ({"output_mode": "text"}, "CODEBUDDY_OUTPUT_MODE_NOT_ALLOWED"),
                ({"model": "unknown"}, "CODEBUDDY_MODEL_NOT_ALLOWED"),
                ({"access_profile": "unknown"}, "CODEBUDDY_PROFILE_NOT_ALLOWED"),
            )
            for kwargs, code in cases:
                with self.subTest(code=code), self.assertRaises(CodeBuddyCodeViolation) as caught:
                    h.invocation(**kwargs)
                self.assertEqual(caught.exception.code, code)
        finally:
            h.close()

    def test_login_fact_is_abstract_hash_bound_and_entitlement_unverified(self):
        h = Harness()
        try:
            invocation = h.invocation()
            self.assertEqual(invocation.login_fact_sha256, canonical_sha256(login_fact()))
            self.assertNotIn("CODEBUDDY_HOME", invocation.process_spec.environment)
        finally:
            h.close()

    def test_login_fact_overclaims_or_credential_read_fail_closed(self):
        h = Harness()
        try:
            cases = (
                (login_fact(status="ACTIVE"), "CODEBUDDY_LOGIN_NOT_CONFIRMED"),
                (login_fact(login_domain="example.com"), "CODEBUDDY_LOGIN_DOMAIN_OVERCLAIMED"),
                (login_fact(credential_material_read=True), "CODEBUDDY_CREDENTIAL_READ_FORBIDDEN"),
                (login_fact(entitlement_status="ACTIVE"), "CODEBUDDY_ENTITLEMENT_STATUS_INVALID"),
            )
            for fact, code in cases:
                with self.subTest(code=code), self.assertRaises(CodeBuddyCodeViolation) as caught:
                    h.invocation(login_fact=fact)
                self.assertEqual(caught.exception.code, code)
        finally:
            h.close()

    def test_credential_environment_name_is_rejected(self):
        h = Harness()
        try:
            env = environment(h.workspace)
            env["CODEBUDDY_API_KEY"] = "fixture"
            with self.assertRaises(CodeBuddyCodeViolation) as caught:
                h.invocation(environment=env)
            self.assertEqual(caught.exception.code, "CODEBUDDY_ENVIRONMENT_SET_MISMATCH")
        finally:
            h.close()

    def test_sidecar_drift_blocks_before_process(self):
        h = Harness()
        try:
            evidence = sidecar(h.adapter, attempt_id="attempt_001", model=MODEL, prompt=h.prompt)
            evidence["route_identity"]["route_id"] = "wrong"
            with self.assertRaises(CodeBuddyCodeViolation) as caught:
                h.invocation(pre_send_sidecar=evidence)
            self.assertEqual(caught.exception.code, "CALL_SIDECAR_BINDING_MISMATCH")
        finally:
            h.close()


class CodeBuddyParserAndRunnerTest(unittest.TestCase):
    def normalized(self, h: Harness):
        invocation, process = h.run()
        summary = (
            h.adapter.parse_json_output(process.stdout, invocation=invocation)
            if h.output_mode == "json"
            else h.adapter.parse_event_stream(process.stdout, invocation=invocation)
        )
        normalized = h.adapter.normalize_result(
            process,
            summary,
            logical_role="card_distiller_primary",
            payload_schema_ref="schema:t09-fixture@v1",
            payload_schema=PAYLOAD_SCHEMA,
        )
        return invocation, process, summary, normalized

    def test_valid_stream_is_exact_bound_and_zero_real_requests(self):
        h = Harness()
        try:
            invocation, process, summary, normalized = self.normalized(h)
            projection = h.adapter.offline_q2_projection(normalized, summary)
            self.assertEqual(process.receipt["exit_code"], 0)
            self.assertTrue(normalized["payload_valid"])
            self.assertEqual(summary["request_model_binding_sha256"], invocation.request_model_binding_sha256)
            self.assertFalse(summary["external_request_started"])
            self.assertTrue(projection["qualification_eligible"])
            self.assertEqual(projection["real_model_request_count"], 0)
        finally:
            h.close()

    def test_valid_json_schema_projection(self):
        h = Harness(output_mode="json")
        try:
            _, _, summary, normalized = self.normalized(h)
            self.assertEqual(summary["parser_version"], "DEEPCODE_ADAPTER_CODEBUDDY_JSON_SCHEMA_V1")
            self.assertTrue(normalized["payload_valid"])
        finally:
            h.close()

    def test_content_blocks_are_joined_deterministically(self):
        h = Harness(mode="content_blocks")
        try:
            _, _, _, normalized = self.normalized(h)
            self.assertTrue(normalized["payload_valid"])
            self.assertIn("Unicode Ω", normalized["payload"]["value"])
        finally:
            h.close()

    def test_generated_model_name_is_not_identity(self):
        h = Harness()
        try:
            _, _, summary, normalized = self.normalized(h)
            self.assertIn("totally-different-model", normalized["payload"]["value"])
            self.assertEqual(summary["returned_model"], MODEL)
            self.assertFalse(summary["model_text_self_report_used"])
        finally:
            h.close()

    def test_missing_model_is_unexposed_but_q2_can_pass(self):
        h = Harness(mode="no_model")
        try:
            _, _, summary, normalized = self.normalized(h)
            projection = h.adapter.offline_q2_projection(normalized, summary)
            self.assertEqual(summary["model_identity_grade"], "PROVIDER_NOT_EXPOSED")
            self.assertIsNone(summary["returned_model"])
            self.assertTrue(projection["qualification_eligible"])
        finally:
            h.close()

    def test_machine_model_mismatch_is_conflict(self):
        h = Harness(mode="label_mismatch")
        try:
            _, _, summary, normalized = self.normalized(h)
            projection = h.adapter.offline_q2_projection(normalized, summary)
            self.assertEqual(summary["model_identity_grade"], "CONFLICT")
            self.assertFalse(projection["qualification_eligible"])
        finally:
            h.close()

    def test_tool_and_unknown_events_fail_closed(self):
        for mode, code in (
            ("tool", "CODEBUDDY_TOOL_EVENT_FORBIDDEN"),
            ("unknown_event", "CODEBUDDY_STREAM_EVENT_TYPE_UNKNOWN"),
        ):
            h = Harness(mode=mode)
            try:
                invocation, process = h.run()
                with self.subTest(mode=mode), self.assertRaises(CodeBuddyCodeViolation) as caught:
                    h.adapter.parse_event_stream(process.stdout, invocation=invocation)
                self.assertEqual(caught.exception.code, code)
            finally:
                h.close()

    def test_partial_and_duplicate_terminal_fail_closed(self):
        for mode in ("partial", "duplicate_terminal"):
            h = Harness(mode=mode)
            try:
                invocation, process = h.run()
                with self.subTest(mode=mode), self.assertRaises(CodeBuddyCodeViolation) as caught:
                    h.adapter.parse_event_stream(process.stdout, invocation=invocation)
                self.assertEqual(caught.exception.code, "CODEBUDDY_STREAM_TERMINAL_INVALID")
            finally:
                h.close()

    def test_empty_ansi_and_invalid_json_fail_closed(self):
        cases = (
            ("empty", "CODEBUDDY_STREAM_EMPTY"),
            ("ansi", "CODEBUDDY_STREAM_ANSI_FORBIDDEN"),
            ("invalid_json", None),
        )
        for mode, parser_code in cases:
            h = Harness(mode=mode)
            try:
                invocation, process = h.run()
                if parser_code:
                    with self.subTest(mode=mode), self.assertRaises(CodeBuddyCodeViolation) as caught:
                        h.adapter.parse_event_stream(process.stdout, invocation=invocation)
                    self.assertEqual(caught.exception.code, parser_code)
                else:
                    summary = h.adapter.parse_event_stream(process.stdout, invocation=invocation)
                    normalized = h.adapter.normalize_result(
                        process, summary, logical_role="card_distiller_primary",
                        payload_schema_ref="schema:test", payload_schema=PAYLOAD_SCHEMA,
                    )
                    self.assertEqual(normalized["error_code"], "OUTPUT_JSON_INVALID")
            finally:
                h.close()

    def test_schema_invalid_and_result_error_are_not_success(self):
        for mode, code in (
            ("schema_invalid", "OUTPUT_SCHEMA_INVALID"),
            ("result_error", "CODEBUDDY_RESULT_ERROR"),
        ):
            h = Harness(mode=mode)
            try:
                _, _, _, normalized = self.normalized(h)
                self.assertFalse(normalized["payload_valid"])
                self.assertEqual(normalized["error_code"], code)
            finally:
                h.close()

    def test_token_conflict_fails_closed(self):
        h = Harness(mode="token_conflict")
        try:
            invocation, process = h.run()
            with self.assertRaises(CodeBuddyCodeViolation) as caught:
                h.adapter.parse_event_stream(process.stdout, invocation=invocation)
            self.assertEqual(caught.exception.code, "CODEBUDDY_TOKEN_TOTAL_CONFLICT")
        finally:
            h.close()

    def test_nonzero_exit_cannot_be_normalized(self):
        h = Harness(mode="nonzero")
        try:
            invocation, process = h.run()
            self.assertEqual(process.receipt["exit_code"], 8)
            with self.assertRaises(CodeBuddyCodeViolation):
                h.adapter.parse_event_stream(process.stdout, invocation=invocation)
        finally:
            h.close()

    def test_timeout_and_cancel_kill_fixture_tree(self):
        h = Harness(mode="sleep")
        try:
            invocation = h.invocation(timeout_seconds=0.1)
            process = IsolatedJobRunner(CORE_SCHEMA, clock=FIXED_CLOCK).run(h.workspace, invocation.process_spec)
            self.assertTrue(process.receipt["timeout"])
        finally:
            h.close()
        h = Harness(mode="sleep")
        try:
            invocation = h.invocation()
            event = threading.Event()
            event.set()
            spec = replace(invocation.process_spec, cancel_event=event)
            process = IsolatedJobRunner(CORE_SCHEMA, clock=FIXED_CLOCK).run(h.workspace, spec)
            self.assertEqual(process.receipt["extensions"]["runner_reason_code"], "PROCESS_CANCELLED")
        finally:
            h.close()

    def test_model_receipt_is_offline_and_cost_is_not_zero_dollars(self):
        h = Harness()
        try:
            invocation, process, summary, normalized = self.normalized(h)
            receipt = h.adapter.build_model_receipt(
                process,
                summary,
                invocation=invocation,
                input_hash=invocation.prompt_sha256,
                output_hash=normalized["source_output_hash"],
                latency_ms=8.0,
            )
            self.assertEqual(receipt["cost_status"], "N/A_OFFLINE_FIXTURE")
            self.assertIsNone(receipt["cost_value"])
            self.assertFalse(receipt["external_request_started"])
            self.assertEqual(receipt["extensions"]["entitlement_status"], "ENTITLEMENT_UNVERIFIED/NOT_REQUIRED_FOR_Q2")
        finally:
            h.close()


class CodeBuddyStaticBoundaryTest(unittest.TestCase):
    def test_workspace_does_not_authorize_protected_write(self):
        h = Harness()
        try:
            with self.assertRaises(WorkspaceViolation):
                h.workspace.assert_write_path(REPO_ROOT / "forbidden.txt")
            invocation = h.invocation()
            self.assertEqual(invocation.process_spec.declared_write_paths, (str(h.workspace.scratch_root / "codebuddy-runtime"),))
        finally:
            h.close()

    def test_adapter_has_no_network_retry_git_or_activation(self):
        source = (REPO_ROOT / "memorive" / "models" / "gateway" / "execution_core" / "adapters" / "codebuddy_code.py").read_text(encoding="utf-8")
        self.assertNotIn("requests.", source)
        self.assertNotIn("urllib", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("git push", source)
        self.assertIn('"activation": "DISABLED"', source)
        self.assertIn('"external_prompt_request_limit": 0', source)


if __name__ == "__main__":
    unittest.main()
