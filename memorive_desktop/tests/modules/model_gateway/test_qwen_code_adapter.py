from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memorive.models.gateway.execution_core.adapters.qwen_code import QwenCodePolicy, QwenCodeViolation
from memorive.models.gateway.execution_core.adapters.qwen_code import QwenCodeAdapter
from memorive.models.gateway.execution_core.contracts import canonical_sha256  # noqa: E402
from memorive.models.gateway.execution_core.runner import IsolatedJobRunner  # noqa: E402
from memorive.models.gateway.execution_core.workspace import JobWorkspace  # noqa: E402


CORE_SCHEMA_PATH = REPO_ROOT / "memorive_workflow_bridge" / "contracts" / "execution_execution_core.schema.json"
CALL_SCHEMA_PATH = REPO_ROOT / "memorive_workflow_bridge" / "contracts" / "external_model_call_receipt.schema.json"
CORE_SCHEMA = json.loads(CORE_SCHEMA_PATH.read_text(encoding="utf-8"))
CALL_SCHEMA = json.loads(CALL_SCHEMA_PATH.read_text(encoding="utf-8"))
FAKE_QWEN = Path(__file__).with_name("fake_qwen.py").resolve()
PYTHON = str(Path(sys.executable).resolve())
FIXED_TIME = "2026-08-14T07:00:00Z"
FIXED_CLOCK = lambda: FIXED_TIME
MODEL = "qwen3-coder-flash"
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
    names = {"QWEN_HOME", "QWEN_RUNTIME_DIR", "DASHSCOPE_API_KEY", "TEMP", "TMP"}
    if os.name == "nt":
        names.add("SYSTEMROOT")
    return tuple(sorted(names))


def profile_names() -> dict[str, tuple[str, ...]]:
    common = {"QWEN_HOME", "QWEN_RUNTIME_DIR", "TEMP", "TMP"}
    if os.name == "nt":
        common.add("SYSTEMROOT")
    return {
        "qwen_oauth": tuple(sorted(common)),
        "dashscope_api": tuple(sorted(common | {"DASHSCOPE_API_KEY"})),
    }


def policy(subscription_home: Path) -> QwenCodePolicy:
    return QwenCodePolicy(
        launcher=PYTHON,
        command_prefix=(str(FAKE_QWEN),),
        exact_version="0.21.10",
        launcher_sha256=sha256(Path(PYTHON)),
        wrapper_sha256=sha256(FAKE_QWEN),
        entrypoint_sha256=sha256(FAKE_QWEN),
        help_sha256=sha256(FAKE_QWEN),
        runner_sha256=sha256(REPO_ROOT / "memorive" / "models" / "gateway" / "execution_core" / "runner.py"),
        model_catalog_sha256=sha256(FAKE_QWEN),
        allowed_models=(MODEL, "qwen3-coder-plus"),
        allowed_profiles=("qwen_oauth", "dashscope_api"),
        profile_environment_names=profile_names(),
        profile_evidence_classes={
            "qwen_oauth": "subscription_cli",
            "dashscope_api": "api",
        },
        environment_allowlist=environment_names(),
        subscription_profile="qwen_oauth",
        api_profile="dashscope_api",
        subscription_home=str(subscription_home),
        route_id="alibaba_qwen_code_v1",
        provider_region="NOT_EXPOSED",
        egress_identity="JP_SCOPED_PROXY",
        max_entitlement_ttl_seconds=86400,
        max_timeout_seconds=5,
        max_raw_cap_bytes=65536,
        component_paths={
            "launcher_sha256": PYTHON,
            "wrapper_sha256": str(FAKE_QWEN),
            "entrypoint_sha256": str(FAKE_QWEN),
            "help_sha256": str(FAKE_QWEN),
            "runner_sha256": str(REPO_ROOT / "memorive" / "models" / "gateway" / "execution_core" / "runner.py"),
            "model_catalog_sha256": str(FAKE_QWEN),
        },
    )


def entitlement(**overrides) -> dict:
    value = {
        "snapshot_id": "entitlement_fixture_001",
        "profile_id": "qwen_oauth",
        "status": "ACTIVE",
        "observed_at": "2026-08-14T05:30:00Z",
        "expires_at": "2026-08-14T07:30:00Z",
        "source_sha256": "D" * 64,
        "session_body_read": False,
    }
    value.update(overrides)
    return value


def environment(workspace: JobWorkspace, profile: str, subscription_home: Path) -> dict[str, str]:
    values = {"TEMP": str(workspace.scratch_root), "TMP": str(workspace.scratch_root)}
    if os.name == "nt":
        values["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    values["QWEN_RUNTIME_DIR"] = str(workspace.scratch_root / "qwen-runtime")
    if profile == "qwen_oauth":
        values["QWEN_HOME"] = str(subscription_home)
    else:
        values["QWEN_HOME"] = str(workspace.scratch_root / "qwen-home")
        values["DASHSCOPE_API_KEY"] = "fixture-key-never-recorded"
    return values


def sidecar(
    adapter: QwenCodeAdapter,
    *,
    attempt_id: str,
    model: str,
    profile: str,
    prompt: bytes,
    schema: dict = PAYLOAD_SCHEMA,
) -> dict:
    output_hash = canonical_sha256(schema)
    prompt_hash = hashlib.sha256(prompt).hexdigest().upper()
    behavior_hash = adapter.behavior_hash(
        model=model,
        output_contract_sha256=output_hash,
        access_profile=profile,
    )
    return {
        "schema_version": "1.0",
        "record_type": "call_binding_receipt",
        "binding_mode": "PROSPECTIVE_STRICT",
        "recorded_at": FIXED_TIME,
        "phase_id": "Execution",
        "task_id": "Retrieval",
        "run_id": "QWEN_CODE_OFFLINE_FIXTURE",
        "attempt_id": attempt_id,
        "call_id": f"call_{attempt_id}",
        "retry_of_call_id": None,
        "role_family": "card_distiller_primary",
        "evidence_class": "subscription_cli" if profile == "qwen_oauth" else "api",
        "request_disposition": "BLOCKED_BEFORE_SEND",
        "profile_identity": {
            "profile_id": profile,
            "profile_version": "fixture-v1",
            "profile_hash": "E" * 64,
            "identity_status": "VERIFIED",
        },
        "model_identity": {
            "provider": "Alibaba Cloud",
            "requested_model": model,
            "returned_model": None,
            "returned_model_status": "UNKNOWN",
            "provider_request_id_hash": None,
        },
        "route_identity": {
            "route_id": "alibaba_qwen_code_v1",
            "route_snapshot_id": "route_snapshot_fixture_001",
            "provider_region": "NOT_EXPOSED",
            "egress_identity": "JP_SCOPED_PROXY",
            "egress_identity_kind": "OPAQUE_LABEL",
            "egress_receipt_id": "egress_fixture_001",
            "identity_status": "RECORDED_UNVERIFIED",
        },
        "artifact_bindings": {
            "source_snapshot_id": "source_fixture_001",
            "source_commit": "1c4cecfa8367d11193d541cb46a78b91f2a85942",
            "source_tree_hash": "F" * 64,
            "runner_hash": adapter.policy.runner_sha256,
            "prompt_hash": prompt_hash,
            "output_contract_hash": output_hash,
            "scorer_hash": None,
            "input_manifest_hash": "1" * 64,
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
            "pricing_status": "RECORDED_UNVERIFIED",
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
                "ref": "fixture://QWEN-ADAPTER/pre-send",
                "sha256": "2" * 64,
                "evidence_status": "EXACT_RECORDED",
            }
        ],
        "extensions": {
            "pre_send_state": "FROZEN_READY",
            "behavior_hash": behavior_hash,
            "credential_env_name": None if profile == "qwen_oauth" else "DASHSCOPE_API_KEY",
            "session_body_read": False,
        },
    }


class Harness:
    def __init__(self, *, mode: str = "valid", profile: str = "qwen_oauth"):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.subscription_home = self.root / "subscription-home"
        self.subscription_home.mkdir()
        self.mode = mode
        self.profile = profile
        self.adapter = QwenCodeAdapter(
            policy(self.subscription_home), CORE_SCHEMA, CALL_SCHEMA, clock=FIXED_CLOCK
        )
        self.workspace = JobWorkspace.create(
            self.root,
            "job_qwen_fixture",
            CORE_SCHEMA,
            process_allowlist=(PYTHON,),
            environment_allowlist=environment_names(),
            extra_read_roots=(Path(PYTHON), FAKE_QWEN, self.subscription_home),
            protected_roots=(REPO_ROOT,),
            selected_adapter_id="qwen_code",
            selected_manifest_hash="A" * 64,
            expected_attempt_id="attempt_001",
            contract_timeout_seconds=5,
            raw_cap_bytes=65536,
            input_hashes=(),
        )
        self.prompt = json.dumps(
            {"mode": mode, "value": "保留 Unicode Ω；&& | $(x) `y`"},
            ensure_ascii=False,
        ).encode("utf-8")

    def invocation(self, **overrides):
        attempt_id = overrides.pop("attempt_id", "attempt_001")
        model = overrides.pop("model", MODEL)
        profile = overrides.pop("access_profile", self.profile)
        prompt = overrides.pop("prompt", self.prompt)
        evidence = overrides.pop(
            "pre_send_sidecar",
            sidecar(
                self.adapter,
                attempt_id=attempt_id,
                model=model,
                profile=profile,
                prompt=prompt,
            ),
        )
        snapshot = overrides.pop(
            "entitlement_snapshot",
            entitlement() if profile == "qwen_oauth" else None,
        )
        return self.adapter.build_invocation(
            self.workspace,
            attempt_id=attempt_id,
            model=model,
            access_profile=profile,
            prompt=prompt,
            output_schema=overrides.pop("output_schema", PAYLOAD_SCHEMA),
            pre_send_sidecar=evidence,
            entitlement_snapshot=snapshot,
            environment=overrides.pop(
                "environment",
                environment(self.workspace, profile, self.subscription_home),
            ),
            timeout_seconds=overrides.pop("timeout_seconds", 2),
            raw_cap_bytes=overrides.pop("raw_cap_bytes", 65536),
            **overrides,
        )

    def run(self):
        invocation = self.invocation()
        process = IsolatedJobRunner(CORE_SCHEMA, clock=FIXED_CLOCK).run(
            self.workspace, invocation.process_spec
        )
        return invocation, process

    def close(self):
        self.temp.cleanup()


class QwenManifestAndCommandTest(unittest.TestCase):
    def test_manifest_is_valid_and_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = QwenCodeAdapter(
                policy(Path(temp)), CORE_SCHEMA, CALL_SCHEMA, clock=FIXED_CLOCK
            )
            manifest = adapter.build_manifest()
        self.assertEqual(manifest["adapter_id"], "qwen_code")
        self.assertEqual(manifest["extensions"]["activation"], "DISABLED")
        self.assertFalse(manifest["extensions"]["model_text_self_report_used"])

    def test_command_is_headless_safe_sandbox_exact_model_no_fallback(self):
        h = Harness()
        try:
            invocation = h.invocation()
            argv = invocation.process_spec.argv
            self.assertEqual(argv[argv.index("--prompt") + 1], "")
            self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
            self.assertEqual(argv[argv.index("--model") + 1], MODEL)
            self.assertIn("--safe-mode", argv)
            self.assertIn("--sandbox", argv)
            self.assertNotIn("--yolo", argv)
            self.assertNotIn("--worktree", argv)
            self.assertNotIn("--fallback-model", argv)
            self.assertNotIn("--continue", argv)
            self.assertNotIn("--resume", argv)
            self.assertEqual(invocation.process_spec.stdin_bytes, h.prompt)
        finally:
            h.close()

    def test_model_and_profile_allowlists_fail_closed(self):
        h = Harness()
        try:
            for kwargs, code in (
                ({"model": "unknown"}, "QWEN_MODEL_NOT_ALLOWED"),
                ({"access_profile": "unknown"}, "QWEN_PROFILE_NOT_ALLOWED"),
            ):
                with self.subTest(code=code), self.assertRaises(QwenCodeViolation) as caught:
                    h.invocation(**kwargs)
                self.assertEqual(caught.exception.code, code)
        finally:
            h.close()

    def test_sidecar_drift_blocks_before_process(self):
        h = Harness()
        try:
            evidence = sidecar(
                h.adapter,
                attempt_id="attempt_001",
                model=MODEL,
                profile=h.profile,
                prompt=h.prompt,
            )
            evidence["route_identity"]["route_id"] = "wrong"
            with self.assertRaises(QwenCodeViolation) as caught:
                h.invocation(pre_send_sidecar=evidence)
            self.assertEqual(caught.exception.code, "CALL_SIDECAR_BINDING_MISMATCH")
        finally:
            h.close()

    def test_active_subscription_entitlement_is_hash_bound(self):
        h = Harness()
        try:
            invocation = h.invocation()
            self.assertEqual(invocation.entitlement_snapshot_sha256, canonical_sha256(entitlement()))
        finally:
            h.close()

    def test_expired_or_not_yet_valid_entitlement_blocks(self):
        h = Harness()
        try:
            for snapshot in (
                entitlement(expires_at="2026-08-14T05:59:59Z"),
                entitlement(observed_at="2026-08-14T07:01:00Z"),
            ):
                with self.subTest(snapshot=snapshot), self.assertRaises(QwenCodeViolation) as caught:
                    h.invocation(entitlement_snapshot=snapshot)
                self.assertEqual(caught.exception.code, "QWEN_ENTITLEMENT_EXPIRED_OR_NOT_YET_VALID")
        finally:
            h.close()

    def test_entitlement_ttl_cannot_exceed_policy(self):
        h = Harness()
        try:
            snapshot = entitlement(
                observed_at="2026-08-14T05:00:00Z",
                expires_at="2026-08-16T05:00:00Z",
            )
            with self.assertRaises(QwenCodeViolation) as caught:
                h.invocation(entitlement_snapshot=snapshot)
            self.assertEqual(caught.exception.code, "QWEN_ENTITLEMENT_TTL_EXCEEDED")
        finally:
            h.close()

    def test_subscription_profile_cannot_carry_paid_api_fallback(self):
        h = Harness()
        try:
            env = environment(h.workspace, h.profile, h.subscription_home)
            env["DASHSCOPE_API_KEY"] = "forbidden-fallback"
            with self.assertRaises(QwenCodeViolation) as caught:
                h.invocation(environment=env)
            self.assertEqual(caught.exception.code, "QWEN_ENVIRONMENT_SET_MISMATCH")
        finally:
            h.close()

    def test_api_profile_rejects_subscription_entitlement_and_uses_ephemeral_home(self):
        h = Harness(profile="dashscope_api")
        try:
            with self.assertRaises(QwenCodeViolation) as caught:
                h.invocation(entitlement_snapshot=entitlement())
            self.assertEqual(caught.exception.code, "QWEN_API_PROFILE_ENTITLEMENT_FORBIDDEN")
            invocation = h.invocation()
            self.assertIn(str(h.workspace.scratch_root / "qwen-home"), invocation.process_spec.declared_write_paths)
            self.assertNotIn(
                environment(h.workspace, "dashscope_api", h.subscription_home)["DASHSCOPE_API_KEY"],
                invocation.process_spec.argv,
            )
        finally:
            h.close()

    def test_profile_changes_behavior_hash(self):
        h = Harness()
        try:
            output_hash = canonical_sha256(PAYLOAD_SCHEMA)
            subscription = h.adapter.behavior_hash(
                model=MODEL,
                output_contract_sha256=output_hash,
                access_profile="qwen_oauth",
            )
            api = h.adapter.behavior_hash(
                model=MODEL,
                output_contract_sha256=output_hash,
                access_profile="dashscope_api",
            )
            self.assertNotEqual(subscription, api)
        finally:
            h.close()


class QwenParserAndRunnerTest(unittest.TestCase):
    def _normalized(self, h: Harness):
        invocation, process = h.run()
        summary = h.adapter.parse_event_stream(process.stdout, invocation=invocation)
        normalized = h.adapter.normalize_result(
            process,
            summary,
            logical_role="card_distiller_primary",
            payload_schema_ref="schema:retrieval-fixture@v1",
            payload_schema=PAYLOAD_SCHEMA,
        )
        return invocation, process, summary, normalized

    def test_valid_offline_q2_run_is_exact_bound_and_zero_real_requests(self):
        h = Harness()
        try:
            invocation, process, summary, normalized = self._normalized(h)
            projection = h.adapter.offline_q2_projection(normalized, summary)
            self.assertEqual(process.receipt["exit_code"], 0)
            self.assertTrue(normalized["payload_valid"])
            self.assertEqual(summary["model_identity_grade"], "DIRECT_EVENT_MATCH")
            self.assertEqual(summary["request_model_binding_sha256"], invocation.request_model_binding_sha256)
            self.assertTrue(projection["qualification_eligible"])
            self.assertEqual(projection["qualification_level"], "Q2_OFFLINE")
            self.assertEqual(projection["real_model_request_count"], 0)
        finally:
            h.close()

    def test_generated_answer_model_name_is_not_identity(self):
        h = Harness()
        try:
            _, _, summary, normalized = self._normalized(h)
            self.assertIn("totally-different-model", normalized["payload"]["value"])
            self.assertEqual(summary["returned_model"], MODEL)
            self.assertFalse(summary["model_text_self_report_used"])
        finally:
            h.close()

    def test_init_model_mismatch_is_conflict(self):
        h = Harness(mode="label_mismatch")
        try:
            invocation, process = h.run()
            summary = h.adapter.parse_event_stream(process.stdout, invocation=invocation)
            self.assertEqual(summary["model_identity_grade"], "CONFLICT")
            self.assertEqual(summary["returned_model"], "qwen-relabeled-model")
        finally:
            h.close()

    def test_missing_init_model_stays_unexposed_but_offline_q2_can_pass(self):
        h = Harness(mode="no_model")
        try:
            _, _, summary, normalized = self._normalized(h)
            projection = h.adapter.offline_q2_projection(normalized, summary)
            self.assertEqual(summary["model_identity_grade"], "PROVIDER_NOT_EXPOSED")
            self.assertIsNone(summary["returned_model"])
            self.assertTrue(projection["qualification_eligible"])
        finally:
            h.close()

    def test_tool_event_fails_closed(self):
        h = Harness(mode="tool")
        try:
            invocation, process = h.run()
            with self.assertRaises(QwenCodeViolation) as caught:
                h.adapter.parse_event_stream(process.stdout, invocation=invocation)
            self.assertEqual(caught.exception.code, "QWEN_TOOL_EVENT_FORBIDDEN")
        finally:
            h.close()

    def test_partial_stream_fails_closed(self):
        h = Harness(mode="partial")
        try:
            invocation, process = h.run()
            with self.assertRaises(QwenCodeViolation) as caught:
                h.adapter.parse_event_stream(process.stdout, invocation=invocation)
            self.assertEqual(caught.exception.code, "QWEN_STREAM_TERMINAL_INVALID")
        finally:
            h.close()

    def test_ansi_stream_fails_closed(self):
        h = Harness(mode="ansi")
        try:
            invocation, process = h.run()
            with self.assertRaises(QwenCodeViolation) as caught:
                h.adapter.parse_event_stream(process.stdout, invocation=invocation)
            self.assertEqual(caught.exception.code, "QWEN_STREAM_ANSI_FORBIDDEN")
        finally:
            h.close()

    def test_schema_invalid_is_not_success(self):
        h = Harness(mode="schema_invalid")
        try:
            _, _, _, normalized = self._normalized(h)
            self.assertFalse(normalized["payload_valid"])
            self.assertEqual(normalized["error_code"], "OUTPUT_SCHEMA_INVALID")
        finally:
            h.close()

    def test_invalid_json_is_not_success(self):
        h = Harness(mode="invalid_json")
        try:
            _, _, _, normalized = self._normalized(h)
            self.assertFalse(normalized["payload_valid"])
            self.assertEqual(normalized["error_code"], "OUTPUT_JSON_INVALID")
        finally:
            h.close()

    def test_result_error_is_not_success(self):
        h = Harness(mode="result_error")
        try:
            _, _, summary, normalized = self._normalized(h)
            self.assertEqual(summary["finish_status"], "ERROR")
            self.assertEqual(normalized["error_code"], "QWEN_RESULT_ERROR")
        finally:
            h.close()

    def test_token_total_conflict_fails_closed(self):
        h = Harness(mode="token_conflict")
        try:
            invocation, process = h.run()
            with self.assertRaises(QwenCodeViolation) as caught:
                h.adapter.parse_event_stream(process.stdout, invocation=invocation)
            self.assertEqual(caught.exception.code, "QWEN_TOKEN_TOTAL_CONFLICT")
        finally:
            h.close()

    def test_nonzero_exit_cannot_be_normalized_as_success(self):
        h = Harness(mode="nonzero")
        try:
            invocation, process = h.run()
            self.assertEqual(process.receipt["exit_code"], 8)
            with self.assertRaises(QwenCodeViolation):
                h.adapter.parse_event_stream(process.stdout, invocation=invocation)
        finally:
            h.close()

    def test_timeout_uses_runner_tree_kill(self):
        h = Harness(mode="sleep")
        try:
            invocation = h.invocation(timeout_seconds=0.1)
            process = IsolatedJobRunner(CORE_SCHEMA, clock=FIXED_CLOCK).run(
                h.workspace, invocation.process_spec
            )
            self.assertTrue(process.receipt["timeout"])
            self.assertEqual(
                process.receipt["extensions"]["process_tree_control"],
                "WINDOWS_JOB_OBJECT",
            )
        finally:
            h.close()

    def test_model_receipt_is_offline_and_cost_is_not_zero_dollars(self):
        h = Harness()
        try:
            invocation, process, summary, normalized = self._normalized(h)
            receipt = h.adapter.build_model_receipt(
                process,
                summary,
                invocation=invocation,
                input_hash=invocation.prompt_sha256,
                output_hash=normalized["source_output_hash"],
                latency_ms=9.5,
            )
            self.assertEqual(receipt["provider"], "Alibaba Cloud")
            self.assertEqual(receipt["cost_status"], "N/A_OFFLINE_FIXTURE")
            self.assertIsNone(receipt["cost_value"])
            self.assertTrue(receipt["extensions"]["offline_q2_fixture"])
        finally:
            h.close()


class QwenStaticBoundaryTest(unittest.TestCase):
    def test_adapter_has_no_network_retry_activation_or_answer_text_identity(self):
        source = (
            REPO_ROOT / "memorive" / "models" / "gateway" / "execution_core" / "adapters" / "qwen_code.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("requests.", source)
        self.assertNotIn("urllib", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("git push", source)
        self.assertIn('"activation": "DISABLED"', source)
        self.assertIn('"model_text_self_report_used": False', source)


if __name__ == "__main__":
    unittest.main()
