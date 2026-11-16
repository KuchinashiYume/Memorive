from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from model_gateway.execution_core import (
    IsolatedJobRunner,
    JobWorkspace,
    WorkspaceViolation,
    canonical_sha256,
)
from model_gateway.execution_core.adapters import (
    CodexAdapterViolation,
    CodexCliAdapter,
    CodexCliPolicy,
)


FIXED_TIME = "2026-08-14T02:30:00Z"
FIXED_CLOCK = lambda: FIXED_TIME
REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
CONTRACT_ROOT = REPO_ROOT / "memorive_workflow_bridge" / "contracts"
CORE_SCHEMA = json.loads(
    (CONTRACT_ROOT / "execution_execution_core.schema.json").read_bytes()
)
EXTERNAL_SCHEMA = json.loads(
    (REPO_ROOT / "memorive_workflow_bridge" / "contracts" / "external_model_call_receipt.schema.json").read_bytes()
)
FAKE_CODEX = Path(__file__).resolve().with_name("fake_codex.py")
ADAPTER_SOURCE = REPO_ROOT / "model_gateway" / "execution_core" / "adapters" / "codex_cli.py"
PYTHON = str(Path(sys.executable).resolve())
PAYLOAD_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "message"],
    "properties": {
        "status": {"const": "ok"},
        "message": {"type": "string"},
    },
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def environment_names() -> tuple[str, ...]:
    names = ["CODEX_HOME", "OPENAI_API_KEY", "MEMORIVE_FAKE_CODEX_MODE", "TEMP", "TMP"]
    if os.name == "nt":
        names.append("SYSTEMROOT")
    return tuple(sorted(names))


def api_environment_names() -> tuple[str, ...]:
    return environment_names()


def subscription_environment_names() -> tuple[str, ...]:
    return tuple(name for name in environment_names() if name != "OPENAI_API_KEY")


def policy(
    *,
    access_profiles: tuple[str, ...] = ("openai_api", "codex_chatgpt_session"),
    subscription_codex_home: Path | None = None,
) -> CodexCliPolicy:
    subscription_home = (subscription_codex_home or Path(tempfile.gettempdir())).resolve()
    return CodexCliPolicy(
        executable=PYTHON,
        command_prefix=(str(FAKE_CODEX),),
        exact_version="codex-cli 0.147.0",
        binary_sha256=sha256(Path(PYTHON)),
        exec_help_sha256=sha256(FAKE_CODEX),
        runner_sha256=sha256(REPO_ROOT / "model_gateway" / "execution_core" / "runner.py"),
        allowed_models=("gpt-5.6-luna", "gpt-5.6-terra"),
        allowed_reasoning_efforts=("high",),
        allowed_access_profiles=access_profiles,
        environment_allowlist=environment_names(),
        api_environment_names=api_environment_names(),
        subscription_environment_names=subscription_environment_names(),
        subscription_codex_home=str(subscription_home),
        api_provider_id="codex_openai_api",
        subscription_provider_id="codex_chatgpt",
        route_id="codex_cli_openai_responses_v1",
        provider_region="NOT_EXPOSED",
        egress_identity="JP_SCOPED_PROXY",
        max_timeout_seconds=5,
        max_raw_cap_bytes=65536,
        component_paths={
            "binary_sha256": PYTHON,
            "exec_help_sha256": str(FAKE_CODEX),
            "runner_sha256": str(REPO_ROOT / "model_gateway" / "execution_core" / "runner.py"),
        },
    )


def make_adapter(
    *,
    access_profiles: tuple[str, ...] = ("openai_api", "codex_chatgpt_session"),
    subscription_codex_home: Path | None = None,
) -> CodexCliAdapter:
    return CodexCliAdapter(
        policy(
            access_profiles=access_profiles,
            subscription_codex_home=subscription_codex_home,
        ),
        CORE_SCHEMA,
        EXTERNAL_SCHEMA,
        clock=FIXED_CLOCK,
    )


def workspace_environment(
    workspace: JobWorkspace,
    mode: str,
    *,
    access_profile: str = "openai_api",
    subscription_codex_home: Path | None = None,
) -> dict[str, str]:
    result = {
        "CODEX_HOME": str(
            workspace.scratch_root / "codex_home"
            if access_profile == "openai_api"
            else subscription_codex_home
        ),
        "MEMORIVE_FAKE_CODEX_MODE": mode,
        "TEMP": str(workspace.scratch_root),
        "TMP": str(workspace.scratch_root),
    }
    if access_profile == "openai_api":
        result["OPENAI_API_KEY"] = "fixture-credential-never-logged"
    if os.name == "nt":
        result["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return result


def sidecar(
    adapter: CodexCliAdapter,
    *,
    attempt_id: str,
    model: str,
    prompt: bytes,
    schema: dict = PAYLOAD_SCHEMA,
    access_profile: str = "openai_api",
) -> dict:
    output_hash = canonical_sha256(schema)
    prompt_hash = hashlib.sha256(prompt).hexdigest().upper()
    behavior_hash = adapter.behavior_hash(
        model=model,
        reasoning_effort="high",
        output_contract_sha256=output_hash,
        access_profile=access_profile,
    )
    return {
        "schema_version": "1.0",
        "record_type": "call_binding_receipt",
        "binding_mode": "PROSPECTIVE_STRICT",
        "recorded_at": FIXED_TIME,
        "phase_id": "Execution",
        "task_id": "Intake",
        "run_id": "CODEX_CLI_OFFLINE_FIXTURE",
        "attempt_id": attempt_id,
        "call_id": f"call_{attempt_id}",
        "retry_of_call_id": None,
        "role_family": "card_distiller_primary",
        "evidence_class": "api" if access_profile == "openai_api" else "subscription_cli",
        "request_disposition": "BLOCKED_BEFORE_SEND",
        "profile_identity": {
            "profile_id": access_profile,
            "profile_version": "fixture-v1",
            "profile_hash": "B" * 64,
            "identity_status": "VERIFIED",
        },
        "model_identity": {
            "provider": "OpenAI",
            "requested_model": model,
            "returned_model": None,
            "returned_model_status": "UNKNOWN",
            "provider_request_id_hash": None,
        },
        "route_identity": {
            "route_id": "codex_cli_openai_responses_v1",
            "route_snapshot_id": "route_snapshot_fixture_001",
            "provider_region": "NOT_EXPOSED",
            "egress_identity": "JP_SCOPED_PROXY",
            "egress_identity_kind": "OPAQUE_LABEL",
            "egress_receipt_id": "egress_fixture_001",
            "identity_status": "RECORDED_UNVERIFIED",
        },
        "artifact_bindings": {
            "source_snapshot_id": "source_fixture_001",
            "source_commit": "32b1e215edc10a10426420bcb4f0adb3ca63a76a",
            "source_tree_hash": "C" * 64,
            "runner_hash": adapter.policy.runner_sha256,
            "prompt_hash": prompt_hash,
            "output_contract_hash": output_hash,
            "scorer_hash": None,
            "input_manifest_hash": "D" * 64,
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
                "ref": "fixture://CODEX-ADAPTER/pre-send",
                "sha256": "E" * 64,
                "evidence_status": "EXACT_RECORDED",
            }
        ],
        "extensions": {
            "pre_send_state": "FROZEN_READY",
            "behavior_hash": behavior_hash,
            "credential_env_name": "OPENAI_API_KEY" if access_profile == "openai_api" else None,
            "session_body_read": False,
        },
    }


class Harness:
    def __init__(
        self,
        testcase: unittest.TestCase,
        *,
        mode: str = "valid",
        access_profile: str = "openai_api",
    ):
        self.testcase = testcase
        self.mode = mode
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.subscription_home = self.root / "subscription_auth_home"
        self.subscription_home.mkdir()
        self.access_profile = access_profile
        self.adapter = make_adapter(subscription_codex_home=self.subscription_home)
        self.schema_bytes = json.dumps(PAYLOAD_SCHEMA, ensure_ascii=False).encode("utf-8")
        self.workspace = JobWorkspace.create(
            self.root,
            "job_codex_fixture",
            CORE_SCHEMA,
            process_allowlist=(PYTHON,),
            environment_allowlist=environment_names(),
            extra_read_roots=(Path(PYTHON), FAKE_CODEX, self.subscription_home),
            protected_roots=(REPO_ROOT,),
            selected_adapter_id="codex_cli",
            selected_manifest_hash="F" * 64,
            expected_attempt_id="attempt_001",
            contract_timeout_seconds=5,
            raw_cap_bytes=65536,
            input_hashes=(
                {
                    "relative_path": "contracts/output.schema.json",
                    "bytes": len(self.schema_bytes),
                    "sha256": hashlib.sha256(self.schema_bytes).hexdigest().upper(),
                },
            ),
        )
        self.workspace.stage_input("contracts/output.schema.json", self.schema_bytes)
        self.prompt = "严格返回 JSON；保留 Unicode Ω 与 shell 字符 ; && | $(x) `y`。".encode("utf-8")
        self.schema_path = self.workspace.input_root / "contracts" / "output.schema.json"
        self.final_path = self.workspace.output_root / "final.json"

    def invocation(self, **overrides):
        model = overrides.pop("model", "gpt-5.6-luna")
        attempt_id = overrides.pop("attempt_id", "attempt_001")
        prompt = overrides.pop("prompt", self.prompt)
        access_profile = overrides.pop("access_profile", self.access_profile)
        evidence = overrides.pop(
            "pre_send_sidecar",
            sidecar(
                self.adapter,
                attempt_id=attempt_id,
                model=model,
                prompt=prompt,
                access_profile=access_profile,
            ),
        )
        return self.adapter.build_invocation(
            self.workspace,
            attempt_id=attempt_id,
            model=model,
            reasoning_effort=overrides.pop("reasoning_effort", "high"),
            access_profile=access_profile,
            prompt=prompt,
            output_schema_path=overrides.pop("output_schema_path", self.schema_path),
            final_output_path=overrides.pop("final_output_path", self.final_path),
            pre_send_sidecar=evidence,
            environment=overrides.pop(
                "environment",
                workspace_environment(
                    self.workspace,
                    self.mode,
                    access_profile=access_profile,
                    subscription_codex_home=self.subscription_home,
                ),
            ),
            timeout_seconds=overrides.pop("timeout_seconds", 2),
            raw_cap_bytes=overrides.pop("raw_cap_bytes", 65536),
            **overrides,
        )

    def run(self):
        invocation = self.invocation()
        result = IsolatedJobRunner(CORE_SCHEMA, clock=FIXED_CLOCK).run(
            self.workspace, invocation.process_spec
        )
        return invocation, result

    def close(self):
        self.temp.cleanup()


class CodexManifestAndBuilderTest(unittest.TestCase):
    def test_manifest_is_valid_and_activation_remains_disabled(self):
        manifest = make_adapter().build_manifest()
        self.assertEqual(manifest["adapter_id"], "codex_cli")
        self.assertEqual(manifest["version_range"], "codex-cli 0.147.0")
        self.assertEqual(manifest["extensions"]["activation"], "DISABLED")
        self.assertIn("NO_APPROVAL_BYPASS", manifest["permission_flags"])

    def test_command_is_structured_ephemeral_read_only_and_prompt_uses_stdin(self):
        harness = Harness(self)
        try:
            invocation = harness.invocation()
            argv = invocation.process_spec.argv
            self.assertEqual(argv[0], PYTHON)
            self.assertIn("--ignore-user-config", argv)
            self.assertIn("--strict-config", argv)
            self.assertIn("--ephemeral", argv)
            self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
            self.assertEqual(argv[-1], "-")
            self.assertNotIn(harness.prompt.decode("utf-8"), argv)
            self.assertEqual(invocation.process_spec.stdin_bytes, harness.prompt)
            self.assertNotIn("fixture-credential-never-logged", json.dumps(argv))
            self.assertFalse(any("dangerously" in item for item in argv))
        finally:
            harness.close()

    def test_model_reasoning_and_profile_allowlists_fail_closed(self):
        for field, value, code in (
            ("model", "other-model", "CODEX_MODEL_NOT_ALLOWED"),
            ("reasoning_effort", "max", "CODEX_REASONING_NOT_ALLOWED"),
            ("access_profile", "unknown_profile", "CODEX_ACCESS_PROFILE_NOT_ALLOWED"),
        ):
            harness = Harness(self)
            try:
                with self.assertRaises(CodexAdapterViolation) as caught:
                    harness.invocation(**{field: value})
                self.assertEqual(caught.exception.code, code)
            finally:
                harness.close()

    def test_sidecar_missing_field_hash_drift_and_not_ready_block_before_process(self):
        mutations = []
        harness = Harness(self)
        try:
            base = sidecar(
                harness.adapter,
                attempt_id="attempt_001",
                model="gpt-5.6-luna",
                prompt=harness.prompt,
            )
            missing = deepcopy(base)
            del missing["route_identity"]
            mutations.append((missing, "CALL_SIDECAR_SCHEMA_INVALID"))
            drift = deepcopy(base)
            drift["artifact_bindings"]["prompt_hash"] = "0" * 64
            mutations.append((drift, "CALL_SIDECAR_BINDING_MISMATCH"))
            not_ready = deepcopy(base)
            not_ready["extensions"]["pre_send_state"] = "PENDING"
            mutations.append((not_ready, "CALL_SIDECAR_BINDING_MISMATCH"))
            for evidence, code in mutations:
                with self.subTest(code=code):
                    with self.assertRaises(CodexAdapterViolation) as caught:
                        harness.invocation(pre_send_sidecar=evidence)
                    self.assertEqual(caught.exception.code, code)
            self.assertEqual(list(harness.workspace.output_root.iterdir()), [])
        finally:
            harness.close()

    def test_environment_extra_missing_secret_and_codex_home_escape_fail_closed(self):
        for mutate, code in (
            (lambda env: env.pop("OPENAI_API_KEY"), "CODEX_ENVIRONMENT_SET_MISMATCH"),
            (lambda env: env.update({"UNAPPROVED": "1"}), "CODEX_ENVIRONMENT_SET_MISMATCH"),
            (lambda env: env.update({"OPENAI_API_KEY": ""}), "CODEX_API_CREDENTIAL_MISSING"),
            (lambda env: env.update({"CODEX_HOME": str(REPO_ROOT)}), "CODEX_HOME_OUTSIDE_WORKSPACE"),
        ):
            harness = Harness(self)
            try:
                env = workspace_environment(harness.workspace, "valid")
                mutate(env)
                with self.assertRaises(CodexAdapterViolation) as caught:
                    harness.invocation(environment=env)
                self.assertEqual(caught.exception.code, code)
            finally:
                harness.close()

    def test_subscription_command_uses_chatgpt_auth_zero_retry_and_no_api_key(self):
        harness = Harness(self, access_profile="codex_chatgpt_session")
        try:
            invocation = harness.invocation()
            argv = invocation.process_spec.argv
            rendered = "\n".join(argv)
            self.assertIn('model_provider="codex_chatgpt"', argv)
            self.assertIn("model_providers.codex_chatgpt.requires_openai_auth=true", argv)
            self.assertIn("model_providers.codex_chatgpt.request_max_retries=0", argv)
            self.assertIn("model_providers.codex_chatgpt.stream_max_retries=0", argv)
            self.assertIn("features.shell_tool=false", argv)
            self.assertIn("features.code_mode_host=false", argv)
            self.assertIn("tools.web_search=false", argv)
            self.assertIn("agents.enabled=false", argv)
            self.assertNotIn("tools.view_image=false", argv)
            self.assertNotIn("OPENAI_API_KEY", invocation.process_spec.environment)
            self.assertNotIn("api.openai.com", rendered)
            self.assertIn(str(harness.subscription_home), invocation.process_spec.declared_read_paths)
            self.assertNotIn(str(harness.subscription_home), invocation.process_spec.declared_write_paths)
        finally:
            harness.close()

    def test_subscription_profile_rejects_api_key_and_wrong_auth_home(self):
        for mutate, code in (
            (
                lambda env, harness: env.update({"OPENAI_API_KEY": "must-not-be-present"}),
                "CODEX_ENVIRONMENT_SET_MISMATCH",
            ),
            (
                lambda env, harness: env.update({"CODEX_HOME": str(harness.root)}),
                "CODEX_SUBSCRIPTION_HOME_MISMATCH",
            ),
        ):
            harness = Harness(self, access_profile="codex_chatgpt_session")
            try:
                env = workspace_environment(
                    harness.workspace,
                    "valid",
                    access_profile="codex_chatgpt_session",
                    subscription_codex_home=harness.subscription_home,
                )
                mutate(env, harness)
                with self.assertRaises(CodexAdapterViolation) as caught:
                    harness.invocation(environment=env)
                self.assertEqual(caught.exception.code, code)
            finally:
                harness.close()

    def test_access_profile_is_bound_into_behavior_hash(self):
        adapter = make_adapter()
        common = {
            "model": "gpt-5.6-luna",
            "reasoning_effort": "high",
            "output_contract_sha256": "A" * 64,
        }
        self.assertNotEqual(
            adapter.behavior_hash(**common, access_profile="openai_api"),
            adapter.behavior_hash(**common, access_profile="codex_chatgpt_session"),
        )

    def test_schema_and_final_paths_cannot_escape_workspace(self):
        harness = Harness(self)
        try:
            for field, path in (
                ("output_schema_path", REPO_ROOT / "requirements.txt"),
                ("final_output_path", REPO_ROOT / "forbidden.json"),
            ):
                with self.subTest(field=field):
                    with self.assertRaises(WorkspaceViolation):
                        harness.invocation(**{field: path})
        finally:
            harness.close()

    def test_existing_final_output_is_create_only_violation(self):
        harness = Harness(self)
        try:
            harness.final_path.write_text("{}", encoding="utf-8")
            with self.assertRaises(CodexAdapterViolation) as caught:
                harness.invocation()
            self.assertEqual(caught.exception.code, "CODEX_FINAL_OUTPUT_CREATE_ONLY")
        finally:
            harness.close()


class CodexParserAndRunnerTest(unittest.TestCase):
    def test_valid_unicode_run_has_schema_model_usage_cost_and_redacted_receipt(self):
        harness = Harness(self)
        try:
            invocation, process = harness.run()
            self.assertEqual(process.receipt["exit_code"], 0)
            summary = harness.adapter.parse_event_stream(
                process.stdout, requested_model="gpt-5.6-luna"
            )
            normalized = harness.adapter.normalize_final_output(
                process,
                final_output_path=invocation.final_output_path,
                logical_role="card_distiller_primary",
                payload_schema_ref="schema:configuration-output@v1",
                payload_schema=PAYLOAD_SCHEMA,
            )
            self.assertTrue(normalized["payload_valid"])
            self.assertIn("Unicode Ω", normalized["payload"]["message"])
            self.assertEqual(summary["returned_model"], "gpt-5.6-luna")
            self.assertEqual(summary["model_identity_grade"], "VERIFIED")
            self.assertEqual(summary["token_usage"]["total_tokens"], 140)
            receipt = harness.adapter.build_model_receipt(
                process,
                summary,
                behavior_hash=invocation.behavior_hash,
                input_hash=invocation.prompt_sha256,
                output_hash=normalized["source_output_hash"],
                route="codex_cli_openai_responses_v1",
                region="NOT_EXPOSED",
                egress="JP_SCOPED_PROXY",
                latency_ms=12.5,
                access_profile="openai_api",
                input_price_per_mtok=0.2,
                output_price_per_mtok=1.2,
            )
            self.assertEqual(receipt["cost_value"], 0.000068)
            self.assertTrue(
                harness.adapter.qualification_projection(normalized, summary)[
                    "qualification_eligible"
                ]
            )
            rendered = json.dumps(process.receipt, ensure_ascii=False)
            self.assertNotIn("fixture-credential-never-logged", rendered)
            self.assertEqual(process.receipt["environment_names"], list(api_environment_names()))
        finally:
            harness.close()

    def test_subscription_receipt_uses_na_cost_without_erasing_tokens(self):
        harness = Harness(self, access_profile="codex_chatgpt_session")
        try:
            invocation, process = harness.run()
            summary = harness.adapter.parse_event_stream(
                process.stdout, requested_model="gpt-5.6-luna"
            )
            normalized = harness.adapter.normalize_final_output(
                process,
                final_output_path=invocation.final_output_path,
                logical_role="card_distiller_primary",
                payload_schema_ref="schema:configuration-output@v1",
                payload_schema=PAYLOAD_SCHEMA,
            )
            receipt = harness.adapter.build_model_receipt(
                process,
                summary,
                behavior_hash=invocation.behavior_hash,
                input_hash=invocation.prompt_sha256,
                output_hash=normalized["source_output_hash"],
                route="codex_cli_chatgpt_subscription_responses_v1",
                region="NOT_EXPOSED",
                egress="JP_SCOPED_PROXY",
                latency_ms=12.5,
                access_profile="codex_chatgpt_session",
            )
            self.assertEqual(receipt["evidence_class"], "subscription_cli")
            self.assertEqual(receipt["cost_status"], "N/A_SUBSCRIPTION_CLI")
            self.assertIsNone(receipt["cost_value"])
            self.assertIsNone(receipt["currency"])
            self.assertEqual(receipt["token_usage"]["total_tokens"], 140)
            self.assertTrue(
                harness.adapter.qualification_projection(
                    normalized,
                    summary,
                    access_profile="codex_chatgpt_session",
                )["qualification_eligible"]
            )
        finally:
            harness.close()

    def test_exit_zero_but_schema_invalid_is_not_success(self):
        harness = Harness(self, mode="invalid-schema")
        try:
            invocation, process = harness.run()
            self.assertEqual(process.receipt["exit_code"], 0)
            normalized = harness.adapter.normalize_final_output(
                process,
                final_output_path=invocation.final_output_path,
                logical_role="analysis_primary",
                payload_schema_ref="schema:configuration-output@v1",
                payload_schema=PAYLOAD_SCHEMA,
            )
            self.assertFalse(normalized["payload_valid"])
            self.assertEqual(normalized["error_code"], "OUTPUT_SCHEMA_INVALID")
        finally:
            harness.close()

    def test_missing_or_mismatched_returned_model_is_restricted(self):
        for mode, reason in (
            ("missing-model", "RETURNED_MODEL_NOT_EXPOSED"),
            ("mismatch-model", "RETURNED_MODEL_CONFLICT"),
        ):
            harness = Harness(self, mode=mode)
            try:
                invocation, process = harness.run()
                summary = harness.adapter.parse_event_stream(
                    process.stdout, requested_model="gpt-5.6-luna"
                )
                normalized = harness.adapter.normalize_final_output(
                    process,
                    final_output_path=invocation.final_output_path,
                    logical_role="analysis_reviewer_primary",
                    payload_schema_ref="schema:configuration-output@v1",
                    payload_schema=PAYLOAD_SCHEMA,
                )
                projection = harness.adapter.qualification_projection(normalized, summary)
                self.assertFalse(projection["qualification_eligible"])
                self.assertIn(reason, projection["reason_codes"])
                self.assertEqual(projection["verification_result"], "NOT_ASSESSED")
            finally:
                harness.close()

    def test_direct_cli_request_binding_is_eligible_and_model_text_is_ignored(self):
        harness = Harness(
            self,
            mode="missing-model",
            access_profile="codex_chatgpt_session",
        )
        try:
            invocation, process = harness.run()
            summary = harness.adapter.parse_event_stream(
                process.stdout,
                requested_model="gpt-5.6-luna",
                invocation=invocation,
            )
            self.assertEqual(summary["returned_model"], None)
            self.assertEqual(summary["model_identity_grade"], "PROVIDER_NOT_EXPOSED")
            self.assertEqual(
                summary["request_model_binding_status"],
                "EXACT_ARGV_AND_FROZEN_ALLOWLIST",
            )
            self.assertEqual(summary["model_allowlist_sha256"], harness.adapter.model_allowlist_sha256())

            # Generated answer text is deliberately contradictory. It is task
            # output, not runtime/provider metadata, and must not affect identity.
            normalized = {
                "payload_valid": True,
                "payload": {"reported_model": "gpt-5.3-codex"},
                "error_code": None,
            }
            projection = harness.adapter.qualification_projection(
                normalized,
                summary,
                access_profile="codex_chatgpt_session",
                identity_evidence_method="DIRECT_CLI_REQUEST_BINDING_V1",
                identity_transport="direct_cli_process",
                identity_authority_ref="USER_DECISION_2026-08-14_DIRECT_CLI_PARAMETER_BINDING",
            )
            self.assertTrue(projection["qualification_eligible"])
            self.assertIsNone(projection["self_reported_model"])
            self.assertIsNone(projection["effective_returned_model"])
            self.assertEqual(projection["effective_model_identity_grade"], "RECORDED_UNVERIFIED")

            receipt = harness.adapter.build_model_receipt(
                process,
                summary,
                behavior_hash=invocation.behavior_hash,
                input_hash=invocation.prompt_sha256,
                output_hash=None,
                route="codex_cli_chatgpt_subscription_responses_v1",
                region="NOT_EXPOSED",
                egress="JP_SCOPED_PROXY",
                latency_ms=1.0,
                access_profile="codex_chatgpt_session",
                normalized_output=normalized,
                identity_evidence_method="DIRECT_CLI_REQUEST_BINDING_V1",
                identity_transport="direct_cli_process",
                identity_authority_ref="USER_DECISION_2026-08-14_DIRECT_CLI_PARAMETER_BINDING",
            )
            self.assertIsNone(receipt["returned_model"])
            self.assertEqual(receipt["model_identity_grade"], "RECORDED_UNVERIFIED")
            self.assertFalse(receipt["extensions"]["model_text_self_report_used"])
        finally:
            harness.close()

    def test_direct_cli_request_binding_fails_closed_for_relay_unbound_or_drift(self):
        harness = Harness(
            self,
            mode="missing-model",
            access_profile="codex_chatgpt_session",
        )
        try:
            invocation, process = harness.run()
            summary = harness.adapter.parse_event_stream(
                process.stdout,
                requested_model="gpt-5.6-luna",
                invocation=invocation,
            )
            normalized = {"payload_valid": True, "payload": {}, "error_code": None}

            relay = harness.adapter.qualification_projection(
                normalized,
                summary,
                access_profile="codex_chatgpt_session",
                identity_evidence_method="DIRECT_CLI_REQUEST_BINDING_V1",
                identity_transport="relay_cli_process",
                identity_authority_ref="USER_DECISION_2026-08-14_DIRECT_CLI_PARAMETER_BINDING",
            )
            self.assertFalse(relay["qualification_eligible"])
            self.assertIn("DIRECT_CLI_REQUEST_BINDING_TRANSPORT_INVALID", relay["reason_codes"])

            unbound_summary = deepcopy(summary)
            unbound_summary["request_model_binding_status"] = "UNBOUND"
            unbound_summary["request_model_binding_sha256"] = None
            unbound = harness.adapter.qualification_projection(
                normalized,
                unbound_summary,
                access_profile="codex_chatgpt_session",
                identity_evidence_method="DIRECT_CLI_REQUEST_BINDING_V1",
                identity_transport="direct_cli_process",
                identity_authority_ref="USER_DECISION_2026-08-14_DIRECT_CLI_PARAMETER_BINDING",
            )
            self.assertFalse(unbound["qualification_eligible"])
            self.assertIn("DIRECT_CLI_REQUEST_MODEL_UNBOUND", unbound["reason_codes"])

            no_authority = harness.adapter.qualification_projection(
                normalized,
                summary,
                access_profile="codex_chatgpt_session",
                identity_evidence_method="DIRECT_CLI_REQUEST_BINDING_V1",
                identity_transport="direct_cli_process",
            )
            self.assertFalse(no_authority["qualification_eligible"])
            self.assertIn("DIRECT_CLI_REQUEST_BINDING_AUTHORITY_MISSING", no_authority["reason_codes"])

            with self.assertRaises(CodexAdapterViolation) as caught:
                harness.adapter.parse_event_stream(
                    process.stdout,
                    requested_model="gpt-5.6-terra",
                    invocation=invocation,
                )
            self.assertEqual(caught.exception.code, "CODEX_REQUEST_MODEL_BINDING_MISMATCH")

            obsolete = harness.adapter.qualification_projection(
                normalized,
                summary,
                access_profile="codex_chatgpt_session",
                identity_evidence_method="USER_ACCEPTED_DIRECT_CLI_SELF_REPORT_V1",
                identity_transport="direct_cli_process",
                identity_authority_ref="obsolete",
            )
            self.assertFalse(obsolete["qualification_eligible"])
            self.assertIn("MODEL_IDENTITY_EVIDENCE_METHOD_UNKNOWN", obsolete["reason_codes"])
        finally:
            harness.close()

    def test_missing_usage_is_not_zero_and_is_not_eligible(self):
        harness = Harness(self, mode="missing-usage")
        try:
            invocation, process = harness.run()
            summary = harness.adapter.parse_event_stream(
                process.stdout, requested_model="gpt-5.6-luna"
            )
            self.assertEqual(summary["usage_status"], "UNKNOWN")
            self.assertTrue(all(value is None for value in summary["token_usage"].values()))
            normalized = harness.adapter.normalize_final_output(
                process,
                final_output_path=invocation.final_output_path,
                logical_role="report_daily_narrative",
                payload_schema_ref="schema:configuration-output@v1",
                payload_schema=PAYLOAD_SCHEMA,
            )
            self.assertIn(
                "TOKEN_USAGE_NOT_EXPOSED",
                harness.adapter.qualification_projection(normalized, summary)["reason_codes"],
            )
        finally:
            harness.close()

    def test_partial_ansi_and_missing_terminal_jsonl_fail_closed(self):
        expected = {
            "partial-jsonl": "CODEX_JSONL_INVALID",
            "ansi": "CODEX_JSONL_ANSI_FORBIDDEN",
            "no-terminal": "CODEX_JSONL_TERMINAL_INVALID",
        }
        for mode, code in expected.items():
            harness = Harness(self, mode=mode)
            try:
                _, process = harness.run()
                with self.assertRaises(CodexAdapterViolation) as caught:
                    harness.adapter.parse_event_stream(
                        process.stdout, requested_model="gpt-5.6-luna"
                    )
                self.assertEqual(caught.exception.code, code)
            finally:
                harness.close()

    def test_any_tool_event_is_rejected_even_when_the_cli_has_no_disable_key(self):
        harness = Harness(self, mode="tool-event")
        try:
            _, process = harness.run()
            with self.assertRaises(CodexAdapterViolation) as caught:
                harness.adapter.parse_event_stream(
                    process.stdout, requested_model="gpt-5.6-luna"
                )
            self.assertEqual(caught.exception.code, "CODEX_TOOL_EVENT_FORBIDDEN")
        finally:
            harness.close()

    def test_provider_error_followed_by_turn_failed_has_one_terminal_turn(self):
        harness = Harness(self, mode="error-then-turn-failed")
        try:
            _, process = harness.run()
            summary = harness.adapter.parse_event_stream(
                process.stdout, requested_model="gpt-5.6-luna"
            )
            self.assertEqual(summary["finish_status"], "ERROR")
            self.assertEqual(summary["event_types"][-2:], ["error", "turn.failed"])
            self.assertTrue(summary["external_request_started"])
        finally:
            harness.close()

    def test_nonzero_process_never_becomes_schema_success(self):
        harness = Harness(self, mode="nonzero")
        try:
            invocation, process = harness.run()
            self.assertEqual(process.receipt["exit_code"], 3)
            normalized = harness.adapter.normalize_final_output(
                process,
                final_output_path=invocation.final_output_path,
                logical_role="report_weekly_narrative",
                payload_schema_ref="schema:configuration-output@v1",
                payload_schema=PAYLOAD_SCHEMA,
            )
            self.assertFalse(normalized["payload_valid"])
            self.assertEqual(normalized["error_code"], "PROCESS_EXIT_NONZERO")
        finally:
            harness.close()

    def test_timeout_uses_tree_kill_and_preserves_terminal_receipt(self):
        harness = Harness(self, mode="timeout")
        try:
            invocation = harness.invocation(timeout_seconds=0.2)
            process = IsolatedJobRunner(CORE_SCHEMA, clock=FIXED_CLOCK).run(
                harness.workspace, invocation.process_spec
            )
            self.assertTrue(process.receipt["timeout"])
            self.assertTrue(process.receipt["killed"])
            self.assertEqual(
                process.receipt["error_code"], "PROCESS_TIMEOUT"
            )
            self.assertIn(
                process.receipt["extensions"]["process_tree_control"],
                {"WINDOWS_JOB_OBJECT", "POSIX_PROCESS_GROUP"},
            )
            self.assertFalse(Path(invocation.final_output_path).exists())
        finally:
            harness.close()

    def test_raw_cap_truncation_cannot_be_normalized_as_success(self):
        harness = Harness(self)
        try:
            invocation = harness.invocation(prompt=b"x", raw_cap_bytes=64)
            process = IsolatedJobRunner(CORE_SCHEMA, clock=FIXED_CLOCK).run(
                harness.workspace, invocation.process_spec
            )
            self.assertTrue(process.receipt["extensions"]["raw_truncated"])
            normalized = harness.adapter.normalize_final_output(
                process,
                final_output_path=invocation.final_output_path,
                logical_role="report_monthly_narrative",
                payload_schema_ref="schema:configuration-output@v1",
                payload_schema=PAYLOAD_SCHEMA,
            )
            self.assertFalse(normalized["payload_valid"])
            self.assertEqual(normalized["error_code"], "RAW_OUTPUT_TRUNCATED")
        finally:
            harness.close()


class CodexStaticBoundaryTest(unittest.TestCase):
    def test_adapter_has_no_retry_activation_git_or_real_ledger_writer(self):
        source = ADAPTER_SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("subprocess.Popen", source)
        self.assertNotIn("git commit", source.casefold())
        self.assertNotIn("git push", source.casefold())
        self.assertNotIn("platform.logs", source)
        self.assertNotIn("ChannelLedgerStore", source)
        self.assertNotIn("automatic_retry", source)

    def test_reasoning_tokens_are_not_double_counted(self):
        adapter = make_adapter()
        raw = b"\n".join(
            [
                b'{"type":"thread.started","model":"gpt-5.6-luna"}',
                b'{"type":"turn.started"}',
                b'{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":7,"reasoning_output_tokens":6}}',
            ]
        )
        summary = adapter.parse_event_stream(raw, requested_model="gpt-5.6-luna")
        self.assertEqual(summary["token_usage"]["reasoning_tokens"], 6)
        self.assertEqual(summary["token_usage"]["total_tokens"], 12)

    def test_reasoning_token_field_conflict_fails_closed(self):
        adapter = make_adapter()
        raw = b"\n".join(
            [
                b'{"type":"thread.started","model":"gpt-5.6-luna"}',
                b'{"type":"turn.started"}',
                b'{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":7,"reasoning_output_tokens":6,"reasoning_tokens":5}}',
            ]
        )
        with self.assertRaises(CodexAdapterViolation) as caught:
            adapter.parse_event_stream(raw, requested_model="gpt-5.6-luna")
        self.assertEqual(str(caught.exception), "CODEX_REASONING_TOKEN_FIELDS_CONFLICT")


if __name__ == "__main__":
    unittest.main()
