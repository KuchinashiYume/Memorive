from __future__ import annotations

from copy import deepcopy
import gc
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import warnings

from memorive.models.gateway.execution_core import (
    AdapterRegistry,
    ChannelSelector,
    ContractViolation,
    ExecutionCore,
    HandoffContract,
    HandoffViolation,
    IsolatedJobRunner,
    JobWorkspace,
    OutputNormalizer,
    ProcessSpec,
    RegistryViolation,
    WorkspaceViolation,
    canonical_sha256,
    validate_initialization_object,
)
from memorive.models.gateway.execution_core.contracts import base_object


FIXED_TIME = "2026-08-13T08:00:00Z"
FIXED_CLOCK = lambda: FIXED_TIME
REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
CONTRACT_ROOT = REPO_ROOT / "memorive" / "models" / "gateway" / "execution_core" / "schemas"
CORE_SCHEMA_PATH = CONTRACT_ROOT / "execution_execution_core.schema.json"
CORE_SCHEMA = json.loads(CORE_SCHEMA_PATH.read_bytes())
FAKE_PROCESS = Path(__file__).resolve().with_name("fake_process.py")
PAYLOAD_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": True,
    "required": ["status", "message"],
    "properties": {
        "status": {"const": "ok"},
        "message": {"type": "string"},
    },
}


def common(object_type: str, object_id: str, producer: str, consumers: tuple[str, ...]):
    return base_object(
        object_type,
        object_id,
        producer=producer,
        single_writer=producer,
        consumers=consumers,
        clock=FIXED_CLOCK,
    )


def adapter_manifest(adapter_id: str = "fake") -> dict:
    value = common(
        "AdapterManifest",
        f"adaptermanifest_{adapter_id}",
        "ADAPTER_OWNER",
        ("Execution_DISCOVERY_RUNNER", "Execution_JOB_RUNNER"),
    )
    value.update(
        {
            "adapter_id": adapter_id,
            "adapter_type": "local_fake_process",
            "executable_name": str(Path(sys.executable).resolve()),
            "install_discovery": None,
            "version_discovery": None,
            "auth_discovery": None,
            "session_discovery": None,
            "model_discovery": None,
            "capability_discovery": None,
            "permission_flags": ["NO_SHELL"],
            "workspace_flags": ["ISOLATED_CWD"],
            "network_flags": ["NETWORK_DISABLED"],
            "output_modes": ["JSON_STDOUT"],
            "exit_code_mapping": [{"exit_code": 0, "state": "COMPLETED"}],
            "timeout_kill_policy": "KILL_PROCESS_TREE",
            "redaction_policy": "ARGV_VALUES_AND_ENV_VALUES_OMITTED",
            "version_range": "FAKE_ONLY",
            "platforms": [sys.platform],
        }
    )
    value["extensions"] = {"read_roots": [str(FAKE_PROCESS)]}
    return value


def execution_profile(
    manifest_hash: str,
    *,
    profile_id: str = "profile_fake",
    adapter_id: str = "fake",
    status: str = "TEST_ONLY",
    qualification: str = "Q5",
    capabilities: tuple[str, ...] = ("json_output",),
) -> dict:
    value = common(
        "ExecutionProfile",
        f"executionprofile_{profile_id}",
        "Execution_PROFILE_REGISTRY",
        ("Execution_ROUTER", "Execution_JOB_RUNNER", "Execution_EVIDENCE_ADAPTER"),
    )
    value["extensions"] = {
        "adapter_manifest_revision": "candidate001",
        "qualification_state": qualification,
        "minimum_route_qualification": "Q5",
        "capabilities": list(capabilities),
        "data_contracts_fake_adapter": True,
    }
    value.update(
        {
            "profile_id": profile_id,
            "status": status,
            "adapter_id": adapter_id,
            "adapter_manifest_hash": manifest_hash,
            "channel_axes": {
                "execution_channel": "subscription_cli",
                "runtime_location": "local_machine",
                "model_service": "none_fake",
                "evidence_class": "synthetic_test",
            },
            "evidence_class": "synthetic_test",
            "provider": "local_fake",
            "requested_model": None,
            "credential_ref": None,
            "route": "local_fake_process",
            "region": "local",
            "egress": "none",
            "permission_policy": {"shell": False, "tools": []},
            "workspace_policy": {
                "mode": "isolated_create_only",
                "environment_allowlist": list(environment()[1]),
            },
            "network_policy": {"allow": []},
            "output_contract": {
                "mode": "json_stdout",
                "schema_ref": "schema:test-output@v1",
                "schema_sha256": canonical_sha256(PAYLOAD_SCHEMA),
                "raw_cap_bytes": 65536,
            },
            "retry_policy": {"max_attempts": 1, "automatic_retry": False},
            "qualification_report_ref": "qualificationreport_fake@candidate001",
        }
    )
    return value


def role_binding(*profile_ids: str) -> dict:
    value = common(
        "LogicalRoleBinding",
        "logicalrolebinding_test_role",
        "EXECUTION_CORE_CONTRACT_OWNER",
        ("Execution_ROUTER", "MODEL_EVALUATION_QUALIFICATION_RUNNER"),
    )
    value.update(
        {
            "role_id": "test_role",
            "input_schema_ref": "schema:test-input@v1",
            "output_schema_ref": "schema:test-output@v1",
            "acceptable_evidence_classes": ["synthetic_test"],
            "minimum_model_identity_grade": "NOT_APPLICABLE",
            "required_capabilities": ["json_output"],
            "data_rules": {"allowed_classifications": ["synthetic_test"]},
            "timeout_seconds": 5,
            "attempt_limit": 1,
            "retry_classes": [],
            "candidate_profile_allowlist": list(profile_ids),
            "fallback_policy": {
                "mode": "PRE_START_ELIGIBILITY_SCAN_ONLY",
                "new_attempt_required_after_process_start": True,
            },
        }
    )
    return value


def execution_request() -> dict:
    value = common(
        "ExecutionRequest",
        "executionrequest_test_001",
        "REQUESTING_MODULE",
        ("Execution_ROUTER", "Execution_JOB_RUNNER"),
    )
    value.update(
        {
            "logical_operation_id": "operation_test_001",
            "attempt_no": 1,
            "logical_role": "test_role",
            "purpose_code": "EXECUTION_CORE_OFFLINE_TEST",
            "caller_module": "TEST_CALLER",
            "input_refs": [],
            "input_hashes": [],
            "output_schema_ref": "schema:test-output@v1",
            "data_classification": "synthetic_test",
            "allowed_egress_class": "none",
            "route_policy_ref": "routepolicy_test@v1",
            "working_directory_policy": "isolated_create_only",
            "read_roots": [],
            "write_roots": [],
            "tool_policy": {"shell": False, "tools": []},
            "timeout_seconds": 5,
            "idempotency_key": "test-idempotency-001",
            "requester": "TEST_CALLER",
        }
    )
    return value


def request_with_input(relative_path: str, data: bytes) -> dict:
    value = execution_request()
    value["input_refs"] = [f"job_input:{relative_path}"]
    value["input_hashes"] = [
        {
            "relative_path": relative_path,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest().upper(),
        }
    ]
    return value


def registry_with_profile(**profile_kwargs):
    registry = AdapterRegistry(CORE_SCHEMA)
    record = registry.register(adapter_manifest(), enabled=True)
    profile = execution_profile(record.manifest_sha256, **profile_kwargs)
    return registry, profile


def environment() -> tuple[dict[str, str], tuple[str, ...]]:
    names = ["MEMORIVE_FAKE_PROCESS_SENTINEL", "PYTHONDONTWRITEBYTECODE"]
    values = {
        "MEMORIVE_FAKE_PROCESS_SENTINEL": "边界值 Ω",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if os.name == "nt":
        names.extend(["SYSTEMROOT", "TEMP", "TMP"])
        for name in ("SYSTEMROOT", "TEMP", "TMP"):
            values[name] = os.environ[name]
    return values, tuple(sorted(names))


def workspace_environment(workspace: JobWorkspace) -> tuple[dict[str, str], tuple[str, ...]]:
    values, names = environment()
    if os.name == "nt":
        values["TEMP"] = str(workspace.scratch_root)
        values["TMP"] = str(workspace.scratch_root)
    return values, names


def process_spec(
    workspace: JobWorkspace,
    *,
    mode: str,
    attempt_id: str = "attempt_001",
    value: str = "",
    marker: Path | None = None,
    timeout: float = 3.0,
    cap: int = 65536,
    cwd: str | None = None,
    env_override: dict[str, str] | None = None,
    allowlist_override: tuple[str, ...] | None = None,
    cancel_event: threading.Event | None = None,
    stdin_bytes: bytes | None = None,
) -> ProcessSpec:
    values, allowlist = workspace_environment(workspace)
    if env_override is not None:
        values = env_override
    if allowlist_override is not None:
        allowlist = allowlist_override
    argv = [str(Path(sys.executable).resolve()), str(FAKE_PROCESS), "--mode", mode]
    if value:
        argv.extend(["--value", value])
    if marker is not None:
        argv.extend(["--marker", str(marker)])
    return ProcessSpec(
        adapter_id="fake",
        attempt_id=attempt_id,
        argv=tuple(argv),
        cwd=cwd or str(workspace.scratch_root),
        environment=values,
        environment_allowlist=allowlist,
        timeout_seconds=timeout,
        raw_cap_bytes=cap,
        cancel_event=cancel_event,
        stdin_bytes=stdin_bytes,
        declared_read_paths=(str(FAKE_PROCESS),),
        declared_write_paths=(
            str(marker),
        )
        if marker is not None
        else (),
    )


class InitializationContractAndRegistryTest(unittest.TestCase):
    def test_core_objects_validate_and_extra_fields_fail_closed(self):
        manifest = adapter_manifest()
        accepted = validate_initialization_object(manifest, "AdapterManifest", CORE_SCHEMA)
        self.assertEqual(accepted["adapter_id"], "fake")
        manifest["unregistered_field"] = True
        with self.assertRaises(ContractViolation) as caught:
            validate_initialization_object(manifest, "AdapterManifest", CORE_SCHEMA)
        self.assertEqual(caught.exception.code, "Initialization_SCHEMA_INVALID")

    def test_registry_is_exact_immutable_and_duplicate_safe(self):
        registry = AdapterRegistry(CORE_SCHEMA)
        manifest = adapter_manifest()
        record = registry.register(manifest, enabled=True)
        manifest["adapter_id"] = "mutated"
        self.assertEqual(record.manifest["adapter_id"], "fake")
        self.assertEqual(record.manifest_sha256, canonical_sha256(record.manifest))
        self.assertEqual(
            registry.resolve("fake", record.revision, record.manifest_sha256), record
        )
        with self.assertRaises(RegistryViolation) as caught:
            registry.register(adapter_manifest(), enabled=True)
        self.assertEqual(caught.exception.code, "ADAPTER_EXACT_KEY_DUPLICATE")

    def test_registry_rejects_disabled_stale_missing_and_hash_mismatch(self):
        cases = [
            (False, False, "ADAPTER_MANIFEST_DISABLED"),
            (True, True, "ADAPTER_MANIFEST_STALE"),
        ]
        for enabled, stale, code in cases:
            with self.subTest(code=code):
                registry = AdapterRegistry(CORE_SCHEMA)
                record = registry.register(
                    adapter_manifest(), enabled=enabled, stale=stale
                )
                with self.assertRaises(RegistryViolation) as caught:
                    registry.resolve("fake", record.revision, record.manifest_sha256)
                self.assertEqual(caught.exception.code, code)
        registry = AdapterRegistry(CORE_SCHEMA)
        record = registry.register(adapter_manifest(), enabled=True)
        with self.assertRaises(RegistryViolation) as caught:
            registry.resolve("fake", record.revision, "0" * 64)
        self.assertEqual(caught.exception.code, "ADAPTER_MANIFEST_HASH_MISMATCH")
        with self.assertRaises(RegistryViolation) as caught:
            registry.resolve("missing", record.revision, record.manifest_sha256)
        self.assertEqual(caught.exception.code, "ADAPTER_MANIFEST_NOT_FOUND")


class ChannelSelectionTest(unittest.TestCase):
    def test_priority_and_pre_start_candidate_scan_are_deterministic(self):
        registry = AdapterRegistry(CORE_SCHEMA)
        records = {}
        profiles = []
        for adapter_id, profile_id in (
            ("fake_a", "profile_a"),
            ("fake_b", "profile_b"),
            ("fake_c", "profile_c"),
        ):
            record = registry.register(adapter_manifest(adapter_id), enabled=True)
            records[adapter_id] = record
            profiles.append(
                execution_profile(
                    record.manifest_sha256,
                    profile_id=profile_id,
                    adapter_id=adapter_id,
                )
            )
        selector = ChannelSelector(CORE_SCHEMA, registry, clock=FIXED_CLOCK)
        binding = role_binding("profile_a", "profile_b", "profile_c")
        override = selector.select(
            execution_request(),
            binding,
            profiles,
            exact_user_override="profile_c",
            task_contract_profiles=("profile_b",),
            global_default_profiles=("profile_a",),
        )
        self.assertEqual(override.selected_profile["profile_id"], "profile_c")
        self.assertEqual(override.selected_manifest["adapter_id"], "fake_c")
        self.assertEqual(override.decision["decision_source"], "exact_user_override")
        repeat = selector.select(
            execution_request(), binding, profiles, exact_user_override="profile_c"
        )
        self.assertEqual(override.decision, repeat.decision)

        profiles[0]["status"] = "DISABLED"
        selected = selector.select(execution_request(), binding, profiles)
        self.assertEqual(selected.selected_profile["profile_id"], "profile_b")
        self.assertEqual(selected.decision["fallback_rank"], 1)
        evaluations = selected.decision["extensions"]["candidate_evaluations"]
        self.assertIn("PROFILE_DISABLED", evaluations[0]["reason_codes"])

    def test_no_eligible_route_blocks_before_process_start_with_all_reasons(self):
        registry, profile = registry_with_profile(status="DISABLED", qualification="Q2")
        profile["network_policy"] = {"allow": ["internet"]}
        selector = ChannelSelector(CORE_SCHEMA, registry, clock=FIXED_CLOCK)
        outcome = selector.select(
            execution_request(), role_binding("profile_fake"), [profile]
        )
        self.assertIsNone(outcome.selected_profile)
        self.assertTrue(outcome.decision["blocked"])
        self.assertEqual(outcome.block_receipt["physical_process_start_count"], 0)
        self.assertEqual(outcome.block_receipt["external_request_start_count"], 0)
        reasons = set(outcome.block_receipt["reason_codes"])
        self.assertTrue(
            {"PROFILE_DISABLED", "QUALIFICATION_INSUFFICIENT", "NETWORK_POLICY_VIOLATION"}
            <= reasons
        )

    def test_role_schema_and_tool_policy_mismatches_block_before_start(self):
        registry, profile = registry_with_profile()
        selector = ChannelSelector(CORE_SCHEMA, registry, clock=FIXED_CLOCK)
        request = execution_request()
        request["logical_role"] = "wrong_role"
        request["output_schema_ref"] = "schema:wrong@v1"
        request["tool_policy"] = {"shell": True, "tools": ["git"]}
        outcome = selector.select(
            request, role_binding("profile_fake"), [profile]
        )
        self.assertIsNone(outcome.selected_profile)
        reasons = set(outcome.block_receipt["reason_codes"])
        self.assertTrue(
            {
                "LOGICAL_ROLE_BINDING_MISMATCH",
                "OUTPUT_SCHEMA_INCOMPATIBLE",
                "WORKSPACE_POLICY_VIOLATION",
            }
            <= reasons
        )


class WorkspaceTest(unittest.TestCase):
    def test_create_only_manifest_and_path_boundaries(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            values, names = environment()
            workspace = JobWorkspace.create(
                parent,
                "job_001",
                CORE_SCHEMA,
                process_allowlist=(str(Path(sys.executable).resolve()),),
                environment_allowlist=names,
                network_allowlist=(),
                input_hashes=(
                    {
                        "relative_path": "中文/input.json",
                        "bytes": 2,
                        "sha256": hashlib.sha256(b"{}").hexdigest().upper(),
                    },
                ),
                clock=FIXED_CLOCK,
            )
            self.assertEqual(workspace.manifest["network_allowlist"], [])
            self.assertTrue(workspace.input_root.is_dir())
            staged = workspace.stage_input("中文/input.json", b"{}")
            self.assertEqual(staged["sha256"], hashlib.sha256(b"{}").hexdigest().upper())
            workspace.assert_cwd(workspace.scratch_root)
            workspace.assert_write_path(workspace.output_root / "result.json")
            with self.assertRaises(WorkspaceViolation):
                workspace.assert_cwd(parent)
            with self.assertRaises(WorkspaceViolation):
                workspace.assert_write_path(parent / "escape.txt")
            with self.assertRaises(WorkspaceViolation) as caught:
                workspace.stage_input("undeclared.bin", b"x")
            self.assertEqual(caught.exception.code, "INPUT_NOT_DECLARED")
            with self.assertRaises(WorkspaceViolation) as caught:
                JobWorkspace.create(
                    parent,
                    "job_001",
                    CORE_SCHEMA,
                    process_allowlist=(),
                    environment_allowlist=(),
                )
            self.assertEqual(caught.exception.code, "WORKSPACE_CREATE_ONLY_COLLISION")

    def test_network_and_protected_root_overlap_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            with self.assertRaises(WorkspaceViolation) as caught:
                JobWorkspace.create(
                    parent,
                    "job_network",
                    CORE_SCHEMA,
                    process_allowlist=(),
                    environment_allowlist=(),
                    network_allowlist=("internet",),
                )
            self.assertEqual(caught.exception.code, "NETWORK_POLICY_VIOLATION")
            with self.assertRaises(WorkspaceViolation) as caught:
                JobWorkspace.create(
                    parent,
                    "job_protected",
                    CORE_SCHEMA,
                    process_allowlist=(),
                    environment_allowlist=(),
                    protected_roots=(parent,),
                )
            self.assertEqual(caught.exception.code, "PROTECTED_ROOT_OVERLAP")

    def test_symlink_or_junction_input_escape_is_rejected_when_supported(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            parent = Path(temp)
            outside_path = Path(outside)
            payload = b"safe"
            workspace = JobWorkspace.create(
                parent,
                "job_reparse",
                CORE_SCHEMA,
                process_allowlist=(),
                environment_allowlist=(),
                input_hashes=(
                    {
                        "relative_path": "linked/payload.bin",
                        "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest().upper(),
                    },
                ),
            )
            link = workspace.input_root / "linked"
            try:
                link.symlink_to(outside_path, target_is_directory=True)
            except OSError:
                self.assertFalse(link.exists())
                with self.assertRaises(WorkspaceViolation) as caught:
                    workspace.stage_input("linked/payload.bin", b"tampered")
                self.assertEqual(caught.exception.code, "INPUT_HASH_MISMATCH")
                return
            with self.assertRaises(WorkspaceViolation) as caught:
                workspace.stage_input("linked/payload.bin", payload)
            self.assertEqual(caught.exception.code, "INPUT_REPARSE_POINT_FORBIDDEN")
            self.assertFalse((outside_path / "payload.bin").exists())


class RunnerAndNormalizationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.parent = Path(self.temp.name)
        values, names = environment()
        self.values = values
        self.names = names
        self.workspace = JobWorkspace.create(
            self.parent,
            "job_runner",
            CORE_SCHEMA,
            process_allowlist=(str(Path(sys.executable).resolve()),),
            environment_allowlist=names,
            extra_read_roots=(str(FAKE_PROCESS),),
            clock=FIXED_CLOCK,
        )
        self.values, self.names = workspace_environment(self.workspace)
        self.runner = IsolatedJobRunner(CORE_SCHEMA, clock=FIXED_CLOCK)
        self.normalizer = OutputNormalizer(CORE_SCHEMA, clock=FIXED_CLOCK)

    def tearDown(self):
        self.temp.cleanup()

    def normalize(self, run):
        return self.normalizer.normalize(
            run,
            logical_role="test_role",
            payload_schema_ref="schema:test-output@v1",
            payload_schema=PAYLOAD_SCHEMA,
        )

    def test_repeated_runs_close_owned_pipes_without_resource_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            for index in range(8):
                run = self.runner.run(
                    self.workspace,
                    process_spec(
                        self.workspace,
                        mode="valid",
                        attempt_id=f"resource_{index:02d}",
                    ),
                )
                self.assertTrue(run.receipt["process_started"])
            gc.collect()
        resource_warnings = [item for item in caught if issubclass(item.category, ResourceWarning)]
        self.assertEqual(resource_warnings, [])

    def test_unicode_and_shell_metacharacters_are_literal_argv(self):
        dangerous = '中文 Ω ; & | $(whoami) `echo` "quoted"'
        run = self.runner.run(
            self.workspace,
            process_spec(self.workspace, mode="valid", value=dangerous),
        )
        result = self.normalize(run)
        self.assertTrue(result["payload_valid"])
        self.assertEqual(result["payload"]["message"], dangerous)
        self.assertEqual(run.receipt["exit_code"], 0)
        self.assertEqual(run.stderr, b"fake diagnostic\n")
        self.assertNotIn(dangerous, run.receipt["argv_redacted"])
        self.assertNotIn("边界值", json.dumps(run.receipt, ensure_ascii=False))
        extensions = run.receipt["extensions"]
        self.assertIsInstance(extensions["process_id"], int)
        self.assertEqual(extensions["parent_process_id"], os.getpid())
        self.assertEqual(len(extensions["command_template_sha256"]), 64)
        self.assertEqual(len(extensions["argv_redacted_sha256"]), 64)
        self.assertTrue(extensions["tree_container_closed"])

    def test_stdout_stderr_are_separate_and_hash_bound(self):
        run = self.runner.run(self.workspace, process_spec(self.workspace, mode="split"))
        self.assertEqual(json.loads(run.stdout)["message"], "split")
        self.assertEqual(run.stderr, b"part-one|part-two")
        self.assertEqual(
            run.receipt["stdout_hash"], hashlib.sha256(run.stdout).hexdigest().upper()
        )
        self.assertEqual(
            run.receipt["stderr_hash"], hashlib.sha256(run.stderr).hexdigest().upper()
        )
        for output in run.receipt["output_files"]:
            self.assertTrue(Path(output).is_file())
            self.assertTrue(str(output).startswith(str(self.workspace.receipt_root)))

    def test_exit_zero_invalid_json_and_schema_do_not_succeed(self):
        for mode in ("invalid-json", "schema-invalid"):
            with self.subTest(mode=mode):
                workspace = JobWorkspace.create(
                    self.parent,
                    f"job_{mode}",
                    CORE_SCHEMA,
                    process_allowlist=(str(Path(sys.executable).resolve()),),
                    environment_allowlist=self.names,
                    extra_read_roots=(str(FAKE_PROCESS),),
                    clock=FIXED_CLOCK,
                )
                run = self.runner.run(workspace, process_spec(workspace, mode=mode))
                self.assertEqual(run.receipt["exit_code"], 0)
                result = self.normalize(run)
                self.assertFalse(result["payload_valid"])
                self.assertEqual(result["error_code"], "OUTPUT_SCHEMA_INVALID")
                self.assertEqual(result["source_output_hash"], run.receipt["stdout_hash"])
                self.assertFalse(result["extensions"]["model_repair_attempted"])

    def test_nonzero_preserves_partial_raw_and_never_normalizes_success(self):
        run = self.runner.run(
            self.workspace, process_spec(self.workspace, mode="nonzero-partial")
        )
        self.assertEqual(run.receipt["exit_code"], 7)
        self.assertEqual(run.receipt["error_code"], "PROCESS_EXIT_NONZERO")
        self.assertIn(b"preserved", run.stdout)
        result = self.normalize(run)
        self.assertFalse(result["payload_valid"])
        self.assertEqual(result["error_code"], "PROCESS_EXIT_NONZERO")

    def test_timeout_kills_process_tree_without_orphan(self):
        marker = self.workspace.output_root / "orphan-marker.txt"
        run = self.runner.run(
            self.workspace,
            process_spec(
                self.workspace,
                mode="spawn-child",
                marker=marker,
                timeout=0.2,
            ),
        )
        self.assertTrue(run.receipt["timeout"])
        self.assertTrue(run.receipt["killed"])
        self.assertEqual(run.receipt["error_code"], "PROCESS_TIMEOUT")
        self.assertTrue(
            run.receipt["extensions"]["graceful_termination_requested"]
        )
        self.assertEqual(
            run.receipt["extensions"]["orphan_scan_status"],
            "TREE_CONTAINER_CLOSED_AND_MARKER_PROBE_REQUIRED",
        )
        time.sleep(1.0)
        self.assertFalse(marker.exists(), "child process survived the tree kill")

    def test_raw_cap_kills_and_keeps_only_bounded_bytes(self):
        run = self.runner.run(
            self.workspace,
            process_spec(self.workspace, mode="raw-cap", cap=1024, timeout=3),
        )
        self.assertTrue(run.receipt["killed"])
        self.assertEqual(run.receipt["error_code"], "WORKSPACE_POLICY_VIOLATION")
        self.assertEqual(
            run.receipt["extensions"]["runner_reason_code"],
            "RAW_OUTPUT_CAP_EXCEEDED",
        )
        self.assertTrue(run.receipt["extensions"]["raw_truncated"])
        self.assertLessEqual(len(run.stdout) + len(run.stderr), 1024)

    def test_env_cwd_and_process_violations_block_before_start(self):
        cases = [
            (
                process_spec(
                    self.workspace,
                    mode="valid",
                    env_override={**self.values, "UNLISTED_SECRET": "not-logged"},
                ),
                "ENVIRONMENT_POLICY_VIOLATION",
            ),
            (
                process_spec(self.workspace, mode="valid", cwd=str(self.parent)),
                "WORKSPACE_POLICY_VIOLATION",
            ),
        ]
        for spec, code in cases:
            with self.subTest(code=code):
                run = self.runner.run(self.workspace, spec)
                self.assertFalse(run.receipt["process_started"])
                self.assertEqual(
                    run.receipt["error_code"], "WORKSPACE_POLICY_VIOLATION"
                )
                self.assertEqual(
                    run.receipt["extensions"]["runner_reason_code"], code
                )
                self.assertEqual(run.receipt["environment_names"], sorted(spec.environment))
                self.assertNotIn("not-logged", json.dumps(run.receipt))


    def test_spawn_failure_is_a_structured_not_started_receipt(self):
        bad_workspace = JobWorkspace.create(
            self.parent,
            "job_missing_exec",
            CORE_SCHEMA,
            process_allowlist=(str(self.parent / "missing.exe"),),
            environment_allowlist=self.names,
            clock=FIXED_CLOCK,
        )
        bad_values, bad_names = workspace_environment(bad_workspace)
        spec = ProcessSpec(
            adapter_id="fake",
            attempt_id="attempt_001",
            argv=(str(self.parent / "missing.exe"),),
            cwd=str(bad_workspace.scratch_root),
            environment=bad_values,
            environment_allowlist=bad_names,
            timeout_seconds=1,
            raw_cap_bytes=1024,
        )
        run = self.runner.run(bad_workspace, spec)
        self.assertFalse(run.receipt["process_started"])
        self.assertEqual(run.receipt["error_code"], "WORKSPACE_POLICY_VIOLATION")
        self.assertEqual(
            run.receipt["extensions"]["runner_reason_code"],
            "PROCESS_START_FAILED",
        )

    def test_explicit_cancellation_kills_tree_and_is_structured(self):
        cancel = threading.Event()
        timer = threading.Timer(0.1, cancel.set)
        timer.start()
        try:
            run = self.runner.run(
                self.workspace,
                process_spec(
                    self.workspace,
                    mode="timeout",
                    timeout=3,
                    cancel_event=cancel,
                ),
            )
        finally:
            timer.cancel()
        self.assertTrue(run.receipt["killed"])
        self.assertFalse(run.receipt["timeout"])
        self.assertEqual(run.receipt["error_code"], "WORKSPACE_POLICY_VIOLATION")
        self.assertEqual(
            run.receipt["extensions"]["runner_reason_code"], "PROCESS_CANCELLED"
        )

    def test_oversize_stdin_is_killed_before_payload_write(self):
        spec = process_spec(self.workspace, mode="timeout", cap=32, timeout=3)
        spec = ProcessSpec(
            adapter_id=spec.adapter_id,
            attempt_id=spec.attempt_id,
            argv=spec.argv,
            cwd=spec.cwd,
            environment=spec.environment,
            environment_allowlist=spec.environment_allowlist,
            timeout_seconds=spec.timeout_seconds,
            raw_cap_bytes=spec.raw_cap_bytes,
            stdin_bytes=b"x" * 33,
        )
        run = self.runner.run(self.workspace, spec)
        self.assertTrue(run.receipt["killed"])
        self.assertEqual(run.receipt["error_code"], "WORKSPACE_POLICY_VIOLATION")
        self.assertEqual(
            run.receipt["extensions"]["runner_reason_code"], "STDIN_CAP_EXCEEDED"
        )

    def test_blocked_stdin_writer_does_not_bypass_timeout(self):
        started = time.monotonic()
        run = self.runner.run(
            self.workspace,
            process_spec(
                self.workspace,
                mode="stdin-block",
                timeout=0.2,
                stdin_bytes=b"x" * 65536,
            ),
        )
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(run.receipt["timeout"])
        self.assertTrue(run.receipt["killed"])
        self.assertEqual(run.receipt["error_code"], "PROCESS_TIMEOUT")

    def test_unsafe_attempt_id_is_blocked_before_start_and_path_creation(self):
        escaped = self.workspace.receipt_root.parent / "escape.stdout.bin"
        run = self.runner.run(
            self.workspace,
            process_spec(self.workspace, mode="valid", attempt_id="../escape"),
        )
        self.assertFalse(run.receipt["process_started"])
        self.assertEqual(
            run.receipt["extensions"]["runner_reason_code"], "PROCESS_SPEC_ID_INVALID"
        )
        self.assertFalse(escaped.exists())


class HandoffAndCoreTest(unittest.TestCase):
    def setUp(self):
        self.registry, self.profile = registry_with_profile()
        self.selector = ChannelSelector(CORE_SCHEMA, self.registry, clock=FIXED_CLOCK)
        self.handoff = HandoffContract(CORE_SCHEMA, clock=FIXED_CLOCK)

    def test_blocked_core_emits_selection_block_terminal_only(self):
        blocked = deepcopy(self.profile)
        blocked["status"] = "DISABLED"
        core = ExecutionCore(
            CORE_SCHEMA,
            self.selector,
            runner=IsolatedJobRunner(CORE_SCHEMA, clock=FIXED_CLOCK),
            normalizer=OutputNormalizer(CORE_SCHEMA, clock=FIXED_CLOCK),
            handoff=self.handoff,
        )
        with tempfile.TemporaryDirectory() as temp:
            outcome = core.execute(
                execution_request(),
                role_binding("profile_fake"),
                [blocked],
                workspace_parent=temp,
                job_id="job_blocked",
                attempt_id="attempt_001",
                predecessor_attempt_ref=None,
                input_materials={},
                protected_roots=(REPO_ROOT,),
                process_spec_factory=lambda workspace, profile, manifest: process_spec(
                    workspace, mode="valid"
                ),
                payload_schema_ref="schema:test-output@v1",
                payload_schema=PAYLOAD_SCHEMA,
            )
        self.assertFalse(outcome.success)
        self.assertEqual(
            [event["event_type"] for event in outcome.handoff["events"]],
            ["selection", "route_block", "terminal"],
        )
        terminal = outcome.handoff["events"][-1]["payload"]
        self.assertEqual(terminal["physical_process_start_count"], 0)
        self.assertEqual(terminal["external_request_start_count"], 0)

    def test_successful_core_handoff_is_complete_hash_bound_and_leaves_run_ledger_untouched(self):
        core = ExecutionCore(
            CORE_SCHEMA,
            self.selector,
            runner=IsolatedJobRunner(CORE_SCHEMA, clock=FIXED_CLOCK),
            normalizer=OutputNormalizer(CORE_SCHEMA, clock=FIXED_CLOCK),
            handoff=self.handoff,
        )
        ledger_before = tree_hash(REPO_ROOT / "memorive" / "platform" / "ledger")
        with tempfile.TemporaryDirectory() as temp:
            outcome = core.execute(
                execution_request(),
                role_binding("profile_fake"),
                [self.profile],
                workspace_parent=temp,
                job_id="job_success",
                attempt_id="attempt_001",
                predecessor_attempt_ref=None,
                input_materials={},
                protected_roots=(REPO_ROOT,),
                process_spec_factory=lambda workspace, profile, manifest: process_spec(
                    workspace, mode="valid", value="handoff"
                ),
                payload_schema_ref="schema:test-output@v1",
                payload_schema=PAYLOAD_SCHEMA,
            )
            self.assertTrue(outcome.success)
            self.assertEqual(
                [event["event_type"] for event in outcome.handoff["events"]],
                ["selection", "workspace", "process", "schema", "terminal"],
            )
            validated = self.handoff.validate(outcome.handoff)
            self.assertEqual(validated["single_writer"], "Execution_EXECUTION_CORE")
            self.assertEqual(validated["consumer"], "CHANNEL_ACCOUNTING_RUNTIME_LOG")
            terminal = validated["events"][-1]["payload"]
            self.assertEqual(terminal["token_status"], "N/A_NON_MODEL_LOCAL")
            self.assertEqual(terminal["cost_status"], "N/A_NON_MODEL_LOCAL")
            self.assertEqual(terminal["acceptance_verdict"], "NOT_ASSESSED")
            self.assertEqual(terminal["verification_result"], "NOT_ASSESSED")
            self.assertEqual(terminal["execution_result"], "SUCCESS")
        self.assertEqual(ledger_before, tree_hash(REPO_ROOT / "memorive" / "platform" / "ledger"))

    def test_selected_manifest_is_the_only_executable_authority(self):
        mismatched = deepcopy(self.profile)
        mismatched["extensions"]["data_contracts_fake_adapter"] = True
        core = ExecutionCore(
            CORE_SCHEMA,
            self.selector,
            runner=IsolatedJobRunner(CORE_SCHEMA, clock=FIXED_CLOCK),
            normalizer=OutputNormalizer(CORE_SCHEMA, clock=FIXED_CLOCK),
            handoff=self.handoff,
        )
        with tempfile.TemporaryDirectory() as temp:
            outcome = core.execute(
                execution_request(),
                role_binding("profile_fake"),
                [mismatched],
                workspace_parent=temp,
                job_id="job_manifest_binding",
                attempt_id="attempt_001",
                predecessor_attempt_ref=None,
                input_materials={},
                protected_roots=(REPO_ROOT,),
                process_spec_factory=lambda workspace, profile, manifest: ProcessSpec(
                    adapter_id="fake",
                    attempt_id="attempt_001",
                    argv=(str(Path(temp) / "unregistered.exe"),),
                    cwd=str(workspace.scratch_root),
                    environment=environment()[0],
                    environment_allowlist=environment()[1],
                    timeout_seconds=1,
                    raw_cap_bytes=1024,
                ),
                payload_schema_ref="schema:test-output@v1",
                payload_schema=PAYLOAD_SCHEMA,
            )
        self.assertFalse(outcome.success)
        self.assertFalse(outcome.process_receipt["process_started"])
        self.assertEqual(
            outcome.process_receipt["extensions"]["runner_reason_code"],
            "PROCESS_NOT_ALLOWLISTED",
        )

    def test_process_spec_adapter_and_timeout_cannot_escape_selected_contract(self):
        core = ExecutionCore(
            CORE_SCHEMA,
            self.selector,
            runner=IsolatedJobRunner(CORE_SCHEMA, clock=FIXED_CLOCK),
            normalizer=OutputNormalizer(CORE_SCHEMA, clock=FIXED_CLOCK),
            handoff=self.handoff,
        )
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "PROCESS_SPEC_ADAPTER_ID_MISMATCH"):
                core.execute(
                    execution_request(),
                    role_binding("profile_fake"),
                    [self.profile],
                    workspace_parent=temp,
                    job_id="job_adapter_mismatch",
                    attempt_id="attempt_001",
                    predecessor_attempt_ref=None,
                    input_materials={},
                    protected_roots=(REPO_ROOT,),
                    process_spec_factory=lambda workspace, profile, manifest: ProcessSpec(
                        adapter_id="other",
                        attempt_id="attempt_001",
                        argv=(str(Path(sys.executable).resolve()),),
                        cwd=str(workspace.scratch_root),
                        environment=environment()[0],
                        environment_allowlist=environment()[1],
                        timeout_seconds=1,
                        raw_cap_bytes=1024,
                    ),
                    payload_schema_ref="schema:test-output@v1",
                    payload_schema=PAYLOAD_SCHEMA,
                )
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(
                ValueError, "PROCESS_SPEC_TIMEOUT_EXCEEDS_CONTRACT"
            ):
                core.execute(
                    execution_request(),
                    role_binding("profile_fake"),
                    [self.profile],
                    workspace_parent=temp,
                    job_id="job_timeout_escape",
                    attempt_id="attempt_001",
                    predecessor_attempt_ref=None,
                    input_materials={},
                    protected_roots=(REPO_ROOT,),
                    process_spec_factory=lambda workspace, profile, manifest: process_spec(
                        workspace, mode="valid", timeout=6
                    ),
                    payload_schema_ref="schema:test-output@v1",
                    payload_schema=PAYLOAD_SCHEMA,
                )

    def test_input_hashes_are_bound_before_process_start(self):
        core = ExecutionCore(
            CORE_SCHEMA,
            self.selector,
            runner=IsolatedJobRunner(CORE_SCHEMA, clock=FIXED_CLOCK),
            normalizer=OutputNormalizer(CORE_SCHEMA, clock=FIXED_CLOCK),
            handoff=self.handoff,
        )
        data = "输入 Ω".encode("utf-8")
        request = request_with_input("prompt/input.bin", data)
        with tempfile.TemporaryDirectory() as temp:
            outcome = core.execute(
                request,
                role_binding("profile_fake"),
                [self.profile],
                workspace_parent=temp,
                job_id="job_input_bound",
                attempt_id="attempt_001",
                predecessor_attempt_ref=None,
                input_materials={"prompt/input.bin": data},
                protected_roots=(REPO_ROOT,),
                process_spec_factory=lambda workspace, profile, manifest: process_spec(
                    workspace, mode="valid", value="bound"
                ),
                payload_schema_ref="schema:test-output@v1",
                payload_schema=PAYLOAD_SCHEMA,
            )
            self.assertTrue(outcome.success)
            manifest_hashes = outcome.workspace_manifest["input_hashes"]
            self.assertEqual(manifest_hashes, request["input_hashes"])
            self.assertEqual(
                (Path(outcome.workspace_manifest["input_root"]) / "prompt/input.bin").read_bytes(),
                data,
            )
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "EXECUTION_REQUEST_INPUT_HASH_MISMATCH"):
                core.execute(
                    request,
                    role_binding("profile_fake"),
                    [self.profile],
                    workspace_parent=temp,
                    job_id="job_input_mismatch",
                    attempt_id="attempt_001",
                    predecessor_attempt_ref=None,
                    input_materials={"prompt/input.bin": b"tampered"},
                    protected_roots=(REPO_ROOT,),
                    process_spec_factory=lambda workspace, profile, manifest: process_spec(
                        workspace, mode="valid"
                    ),
                    payload_schema_ref="schema:test-output@v1",
                    payload_schema=PAYLOAD_SCHEMA,
                )


    def test_schema_bytes_are_bound_before_workspace_or_process_start(self):
        core = ExecutionCore(
            CORE_SCHEMA,
            self.selector,
            runner=IsolatedJobRunner(CORE_SCHEMA, clock=FIXED_CLOCK),
            normalizer=OutputNormalizer(CORE_SCHEMA, clock=FIXED_CLOCK),
            handoff=self.handoff,
        )
        changed_schema = deepcopy(PAYLOAD_SCHEMA)
        changed_schema["required"] = ["status"]
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "OUTPUT_SCHEMA_HASH_MISMATCH"):
                core.execute(
                    execution_request(),
                    role_binding("profile_fake"),
                    [self.profile],
                    workspace_parent=temp,
                    job_id="job_schema_hash",
                    attempt_id="attempt_001",
                    predecessor_attempt_ref=None,
                    input_materials={},
                    protected_roots=(REPO_ROOT,),
                    process_spec_factory=lambda workspace, profile, manifest: process_spec(
                        workspace, mode="valid"
                    ),
                    payload_schema_ref="schema:test-output@v1",
                    payload_schema=changed_schema,
                )
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_attempt_ids_are_explicit_and_no_automatic_retry_occurs(self):
        core = ExecutionCore(
            CORE_SCHEMA,
            self.selector,
            runner=IsolatedJobRunner(CORE_SCHEMA, clock=FIXED_CLOCK),
            normalizer=OutputNormalizer(CORE_SCHEMA, clock=FIXED_CLOCK),
            handoff=self.handoff,
        )
        with tempfile.TemporaryDirectory() as temp:
            first = core.execute(
                execution_request(),
                role_binding("profile_fake"),
                [self.profile],
                workspace_parent=temp,
                job_id="job_attempt_1",
                attempt_id="attempt_001",
                predecessor_attempt_ref="attempt_000@candidate001",
                input_materials={},
                protected_roots=(REPO_ROOT,),
                process_spec_factory=lambda workspace, profile, manifest: process_spec(
                    workspace, mode="nonzero-partial", attempt_id="attempt_001"
                ),
                payload_schema_ref="schema:test-output@v1",
                payload_schema=PAYLOAD_SCHEMA,
            )
            self.assertFalse(first.success)
            self.assertEqual(first.process_receipt["attempt_id"], "attempt_001")
            self.assertEqual(first.handoff["event_count"], 5)
            self.assertEqual(
                first.handoff["events"][-1]["payload"]["attempt_lineage"],
                {
                    "attempt_id": "attempt_001",
                    "predecessor_attempt_ref": "attempt_000@candidate001",
                },
            )


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest().upper()


class StaticBoundaryTest(unittest.TestCase):
    def test_execution_core_does_not_write_the_run_ledger_or_runtime_log(self):
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((REPO_ROOT / "memorive" / "models" / "gateway" / "execution_core").glob("*.py"))
        )
        self.assertNotIn("run_ledger", source)
        self.assertNotIn("platform.logs", source)
        self.assertNotIn("requests.", source)
        self.assertNotIn("urllib", source)


if __name__ == "__main__":
    unittest.main()
