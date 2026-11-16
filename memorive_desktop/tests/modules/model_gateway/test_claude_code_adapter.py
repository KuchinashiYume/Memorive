from __future__ import annotations

from copy import deepcopy
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

from memorive.models.gateway.execution_core.adapters.claude_code import ClaudeCodePolicy, ClaudeCodeViolation, RELAY_REPORTED_UNVERIFIED
from memorive.models.gateway.execution_core.adapters.claude_code import ClaudeCodeAdapter
from memorive.models.gateway.execution_core.contracts import canonical_sha256  # noqa: E402
from memorive.models.gateway.execution_core.runner import IsolatedJobRunner  # noqa: E402
from memorive.models.gateway.execution_core.workspace import JobWorkspace  # noqa: E402


CORE_SCHEMA_PATH = REPO_ROOT / "memorive_workflow_bridge" / "contracts" / "execution_execution_core.schema.json"
CALL_SCHEMA_PATH = REPO_ROOT / "memorive_workflow_bridge" / "contracts" / "external_model_call_receipt.schema.json"
CORE_SCHEMA = json.loads(CORE_SCHEMA_PATH.read_text(encoding="utf-8"))
CALL_SCHEMA = json.loads(CALL_SCHEMA_PATH.read_text(encoding="utf-8"))
FAKE_CLAUDE = Path(__file__).with_name("fake_claude.py").resolve()
PYTHON = str(Path(sys.executable).resolve())
FIXED_TIME = "2026-08-14T06:00:00Z"
FIXED_CLOCK = lambda: FIXED_TIME
MODEL = "claude-sonnet-4-6[1m]"
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
    names = {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CONFIG_DIR",
        "TEMP",
        "TMP",
    }
    if os.name == "nt":
        names.add("SYSTEMROOT")
    return tuple(sorted(names))


def profile_names() -> dict[str, tuple[str, ...]]:
    common = {"TEMP", "TMP"}
    if os.name == "nt":
        common.add("SYSTEMROOT")
    return {
        "intelalloc_relay_api": tuple(
            sorted(
                common
                | {
                    "ANTHROPIC_AUTH_TOKEN",
                    "ANTHROPIC_BASE_URL",
                    "ANTHROPIC_CUSTOM_HEADERS",
                    "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
                    "CLAUDE_CODE_ENTRYPOINT",
                }
            )
        ),
        "anthropic_api": tuple(sorted(common | {"ANTHROPIC_API_KEY"})),
        "claude_subscription": tuple(sorted(common | {"CLAUDE_CONFIG_DIR"})),
    }


def policy() -> ClaudeCodePolicy:
    return ClaudeCodePolicy(
        executable=PYTHON,
        command_prefix=(str(FAKE_CLAUDE),),
        exact_version="2.1.229 (Claude Code)",
        binary_sha256=sha256(Path(PYTHON)),
        help_sha256=sha256(FAKE_CLAUDE),
        runner_sha256=sha256(REPO_ROOT / "memorive" / "models" / "gateway" / "execution_core" / "runner.py"),
        model_catalog_sha256=sha256(FAKE_CLAUDE),
        allowed_models=(MODEL, "claude-opus-4-6"),
        allowed_efforts=("high", "max"),
        allowed_profiles=("intelalloc_relay_api", "anthropic_api", "claude_subscription"),
        relay_profiles=("intelalloc_relay_api",),
        profile_environment_names=profile_names(),
        profile_evidence_classes={
            "intelalloc_relay_api": "api",
            "anthropic_api": "api",
            "claude_subscription": "subscription_cli",
        },
        environment_allowlist=environment_names(),
        route_id="intelalloc_claude_compatible_v1",
        provider_region="NOT_EXPOSED",
        egress_identity="JP_SCOPED_PROXY",
        max_timeout_seconds=5,
        max_raw_cap_bytes=65536,
        component_paths={
            "binary_sha256": PYTHON,
            "help_sha256": str(FAKE_CLAUDE),
            "runner_sha256": str(REPO_ROOT / "memorive" / "models" / "gateway" / "execution_core" / "runner.py"),
            "model_catalog_sha256": str(FAKE_CLAUDE),
        },
    )


def make_adapter() -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter(policy(), CORE_SCHEMA, CALL_SCHEMA, clock=FIXED_CLOCK)


def environment(workspace: JobWorkspace, profile: str) -> dict[str, str]:
    values = {"TEMP": str(workspace.scratch_root), "TMP": str(workspace.scratch_root)}
    if os.name == "nt":
        values["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    if profile == "intelalloc_relay_api":
        values.update(
            {
                "ANTHROPIC_AUTH_TOKEN": "fixture-token-never-recorded",
                "ANTHROPIC_BASE_URL": "https://relay.invalid",
                "ANTHROPIC_CUSTOM_HEADERS": "x-fixture: 1",
                "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
                "CLAUDE_CODE_ENTRYPOINT": "cli",
            }
        )
    elif profile == "anthropic_api":
        values["ANTHROPIC_API_KEY"] = "fixture-key-never-recorded"
    else:
        values["CLAUDE_CONFIG_DIR"] = str(workspace.scratch_root / "claude-home")
    return values


def sidecar(
    adapter: ClaudeCodeAdapter,
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
        effort="high",
        output_contract_sha256=output_hash,
        access_profile=profile,
    )
    return {
        "schema_version": "1.0",
        "record_type": "call_binding_receipt",
        "binding_mode": "PROSPECTIVE_STRICT",
        "recorded_at": FIXED_TIME,
        "phase_id": "Execution",
        "task_id": "Intake",
        "run_id": "CLAUDE_CODE_OFFLINE_FIXTURE",
        "attempt_id": attempt_id,
        "call_id": f"call_{attempt_id}",
        "retry_of_call_id": None,
        "role_family": "card_distiller_primary",
        "evidence_class": "subscription_cli" if profile == "claude_subscription" else "api",
        "request_disposition": "BLOCKED_BEFORE_SEND",
        "profile_identity": {
            "profile_id": profile,
            "profile_version": "fixture-v1",
            "profile_hash": "C" * 64,
            "identity_status": "VERIFIED",
        },
        "model_identity": {
            "provider": "IntelAllocRelay" if profile == "intelalloc_relay_api" else "Anthropic",
            "requested_model": model,
            "returned_model": None,
            "returned_model_status": "UNKNOWN",
            "provider_request_id_hash": None,
        },
        "route_identity": {
            "route_id": "intelalloc_claude_compatible_v1",
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
            "source_tree_hash": "D" * 64,
            "runner_hash": adapter.policy.runner_sha256,
            "prompt_hash": prompt_hash,
            "output_contract_hash": output_hash,
            "scorer_hash": None,
            "input_manifest_hash": "E" * 64,
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
                "ref": "fixture://CLAUDE-ADAPTER/pre-send",
                "sha256": "F" * 64,
                "evidence_status": "EXACT_RECORDED",
            }
        ],
        "extensions": {
            "pre_send_state": "FROZEN_READY",
            "behavior_hash": behavior_hash,
            "credential_env_name": "ANTHROPIC_AUTH_TOKEN" if profile == "intelalloc_relay_api" else None,
            "session_body_read": False,
        },
    }


class Harness:
    def __init__(self, *, mode: str = "valid", profile: str = "intelalloc_relay_api"):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.mode = mode
        self.profile = profile
        self.adapter = make_adapter()
        self.workspace = JobWorkspace.create(
            self.root,
            "job_claude_fixture",
            CORE_SCHEMA,
            process_allowlist=(PYTHON,),
            environment_allowlist=environment_names(),
            extra_read_roots=(Path(PYTHON), FAKE_CLAUDE),
            protected_roots=(REPO_ROOT,),
            selected_adapter_id="claude_code",
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
        return self.adapter.build_invocation(
            self.workspace,
            attempt_id=attempt_id,
            model=model,
            effort=overrides.pop("effort", "high"),
            access_profile=profile,
            prompt=prompt,
            output_schema=overrides.pop("output_schema", PAYLOAD_SCHEMA),
            pre_send_sidecar=evidence,
            environment=overrides.pop("environment", environment(self.workspace, profile)),
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


class ClaudeManifestAndCommandTest(unittest.TestCase):
    def test_manifest_is_valid_and_disabled(self):
        manifest = make_adapter().build_manifest()
        self.assertEqual(manifest["adapter_id"], "claude_code")
        self.assertEqual(manifest["extensions"]["activation"], "DISABLED")
        self.assertFalse(manifest["extensions"]["model_text_self_report_used"])

    def test_command_is_headless_no_tools_no_fallback_and_exact_model(self):
        h = Harness()
        try:
            invocation = h.invocation()
            argv = invocation.process_spec.argv
            self.assertIn("--print", argv)
            self.assertEqual(argv[argv.index("--model") + 1], MODEL)
            self.assertEqual(argv[argv.index("--permission-mode") + 1], "plan")
            self.assertEqual(argv[argv.index("--tools") + 1], "")
            self.assertNotIn("--fallback-model", argv)
            self.assertNotIn("--worktree", argv)
            self.assertEqual(invocation.process_spec.stdin_bytes, h.prompt)
        finally:
            h.close()

    def test_model_effort_and_profile_allowlists_fail_closed(self):
        h = Harness()
        try:
            for kwargs, code in (
                ({"model": "unknown"}, "CLAUDE_MODEL_NOT_ALLOWED"),
                ({"effort": "ultra"}, "CLAUDE_EFFORT_NOT_ALLOWED"),
                ({"access_profile": "unknown"}, "CLAUDE_PROFILE_NOT_ALLOWED"),
            ):
                with self.subTest(code=code), self.assertRaises(ClaudeCodeViolation) as caught:
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
            evidence["model_identity"]["requested_model"] = "wrong"
            with self.assertRaises(ClaudeCodeViolation) as caught:
                h.invocation(pre_send_sidecar=evidence)
            self.assertEqual(caught.exception.code, "CALL_SIDECAR_BINDING_MISMATCH")
            self.assertFalse(any(h.workspace.output_root.iterdir()))
        finally:
            h.close()

    def test_profile_environment_sets_are_exact_and_credentials_never_enter_argv(self):
        h = Harness()
        try:
            env = environment(h.workspace, h.profile)
            invocation = h.invocation(environment=env)
            self.assertNotIn(env["ANTHROPIC_AUTH_TOKEN"], invocation.process_spec.argv)
            bad = dict(env)
            bad["ANTHROPIC_API_KEY"] = "wrong-axis"
            with self.assertRaises(ClaudeCodeViolation) as caught:
                h.invocation(environment=bad)
            self.assertEqual(caught.exception.code, "CLAUDE_ENVIRONMENT_SET_MISMATCH")
        finally:
            h.close()

    def test_access_profile_changes_behavior_hash(self):
        adapter = make_adapter()
        output_hash = canonical_sha256(PAYLOAD_SCHEMA)
        relay = adapter.behavior_hash(
            model=MODEL,
            effort="high",
            output_contract_sha256=output_hash,
            access_profile="intelalloc_relay_api",
        )
        direct = adapter.behavior_hash(
            model=MODEL,
            effort="high",
            output_contract_sha256=output_hash,
            access_profile="anthropic_api",
        )
        self.assertNotEqual(relay, direct)


class ClaudeParserAndRunnerTest(unittest.TestCase):
    def _normalized(self, h: Harness):
        invocation, process = h.run()
        summary = h.adapter.parse_event_stream(process.stdout, invocation=invocation)
        normalized = h.adapter.normalize_result(
            process,
            summary,
            logical_role="card_distiller_primary",
            payload_schema_ref="schema:intake-fixture@v1",
            payload_schema=PAYLOAD_SCHEMA,
        )
        return invocation, process, summary, normalized

    def test_valid_relay_run_preserves_unicode_and_restricts_model_identity(self):
        h = Harness()
        try:
            invocation, process, summary, normalized = self._normalized(h)
            self.assertEqual(process.receipt["exit_code"], 0)
            self.assertTrue(normalized["payload_valid"])
            self.assertIn("Ω", normalized["payload"]["value"])
            self.assertEqual(summary["model_identity_grade"], RELAY_REPORTED_UNVERIFIED)
            self.assertIsNone(summary["returned_model"])
            self.assertEqual(summary["relay_or_provider_reported_model"], MODEL)
            self.assertFalse(summary["model_text_self_report_used"])
            self.assertEqual(summary["request_model_binding_sha256"], invocation.request_model_binding_sha256)
        finally:
            h.close()

    def test_generated_answer_model_name_is_ignored(self):
        h = Harness()
        try:
            _, _, summary, _ = self._normalized(h)
            self.assertEqual(summary["relay_or_provider_reported_model"], MODEL)
            self.assertNotEqual(summary["relay_or_provider_reported_model"], "totally-different-model")
            self.assertFalse(summary["model_text_self_report_used"])
        finally:
            h.close()

    def test_relay_label_mismatch_is_conflict_not_backend_truth(self):
        h = Harness(mode="relay_label_mismatch")
        try:
            invocation, process = h.run()
            summary = h.adapter.parse_event_stream(process.stdout, invocation=invocation)
            self.assertEqual(summary["model_identity_grade"], "RELAY_REPORTED_CONFLICT")
            self.assertIsNone(summary["returned_model"])
            self.assertEqual(summary["relay_or_provider_reported_model"], "relay-remapped-backend")
        finally:
            h.close()

    def test_relay_qualification_is_not_eligible_without_independent_backend_evidence(self):
        h = Harness()
        try:
            _, _, summary, normalized = self._normalized(h)
            projection = h.adapter.qualification_projection(
                normalized,
                summary,
                independent_backend_identity_status="UNVERIFIED",
            )
            self.assertFalse(projection["qualification_eligible"])
            self.assertIn(
                "RELAY_BACKEND_IDENTITY_NOT_INDEPENDENTLY_VERIFIED",
                projection["reason_codes"],
            )
            self.assertEqual(projection["verification_result"], "NOT_ASSESSED")
        finally:
            h.close()

    def test_direct_profile_can_use_independent_control_plane_but_not_answer_text(self):
        h = Harness(profile="anthropic_api")
        try:
            _, _, summary, normalized = self._normalized(h)
            projection = h.adapter.qualification_projection(
                normalized,
                summary,
                independent_backend_identity_status="VERIFIED",
            )
            self.assertTrue(projection["qualification_eligible"])
            self.assertEqual(summary["model_identity_grade"], "DIRECT_EVENT_MATCH")
            self.assertFalse(projection["model_text_self_report_used"])
        finally:
            h.close()

    def test_missing_relay_model_remains_unexposed(self):
        h = Harness(mode="no_model")
        try:
            invocation, process = h.run()
            summary = h.adapter.parse_event_stream(process.stdout, invocation=invocation)
            self.assertEqual(summary["model_identity_grade"], "RELAY_NOT_EXPOSED")
            self.assertIsNone(summary["returned_model"])
        finally:
            h.close()

    def test_tool_event_fails_closed(self):
        h = Harness(mode="tool")
        try:
            invocation, process = h.run()
            with self.assertRaises(ClaudeCodeViolation) as caught:
                h.adapter.parse_event_stream(process.stdout, invocation=invocation)
            self.assertEqual(caught.exception.code, "CLAUDE_TOOL_EVENT_FORBIDDEN")
        finally:
            h.close()

    def test_partial_stream_fails_closed(self):
        h = Harness(mode="partial")
        try:
            invocation, process = h.run()
            with self.assertRaises(ClaudeCodeViolation) as caught:
                h.adapter.parse_event_stream(process.stdout, invocation=invocation)
            self.assertEqual(caught.exception.code, "CLAUDE_STREAM_TERMINAL_INVALID")
        finally:
            h.close()

    def test_ansi_stream_fails_closed(self):
        h = Harness(mode="ansi")
        try:
            invocation, process = h.run()
            with self.assertRaises(ClaudeCodeViolation) as caught:
                h.adapter.parse_event_stream(process.stdout, invocation=invocation)
            self.assertEqual(caught.exception.code, "CLAUDE_STREAM_ANSI_FORBIDDEN")
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

    def test_result_error_is_not_success(self):
        h = Harness(mode="result_error")
        try:
            _, _, summary, normalized = self._normalized(h)
            self.assertEqual(summary["finish_status"], "ERROR")
            self.assertFalse(normalized["payload_valid"])
            self.assertEqual(normalized["error_code"], "CLAUDE_RESULT_ERROR")
        finally:
            h.close()

    def test_nonzero_exit_cannot_be_normalized_as_success(self):
        h = Harness(mode="nonzero")
        try:
            invocation, process = h.run()
            self.assertEqual(process.receipt["exit_code"], 9)
            with self.assertRaises(ClaudeCodeViolation):
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

    def test_model_receipt_keeps_relay_provider_and_null_returned_model(self):
        h = Harness()
        try:
            invocation, process, summary, normalized = self._normalized(h)
            receipt = h.adapter.build_model_receipt(
                process,
                summary,
                invocation=invocation,
                input_hash=invocation.prompt_sha256,
                output_hash=normalized["source_output_hash"],
                latency_ms=12.5,
            )
            self.assertEqual(receipt["provider"], "IntelAllocRelay")
            self.assertIsNone(receipt["returned_model"])
            self.assertEqual(receipt["model_identity_grade"], RELAY_REPORTED_UNVERIFIED)
            self.assertEqual(receipt["cost_status"], "UNKNOWN_RELAY_BILLING")
        finally:
            h.close()


class ClaudeStaticBoundaryTest(unittest.TestCase):
    def test_adapter_has_no_network_retry_activation_or_model_text_identity(self):
        source = (
            REPO_ROOT / "memorive" / "models" / "gateway" / "execution_core" / "adapters" / "claude_code.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("requests.", source)
        self.assertNotIn("urllib", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("git push", source)
        self.assertIn('"activation": "DISABLED"', source)
        self.assertIn('"model_text_self_report_used": False', source)

    def test_fake_and_adapter_do_not_modify_protected_repo(self):
        before = sorted(str(p.relative_to(REPO_ROOT)) for p in REPO_ROOT.rglob("*") if p.is_file())
        h = Harness()
        try:
            self.assertTrue(self._run_valid(h))
        finally:
            h.close()
        after = sorted(str(p.relative_to(REPO_ROOT)) for p in REPO_ROOT.rglob("*") if p.is_file())
        self.assertEqual(before, after)

    @staticmethod
    def _run_valid(h: Harness) -> bool:
        invocation, process = h.run()
        summary = h.adapter.parse_event_stream(process.stdout, invocation=invocation)
        return summary["finish_status"] == "SUCCESS"


if __name__ == "__main__":
    unittest.main()
