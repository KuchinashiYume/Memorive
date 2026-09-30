from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .handoff import HandoffContract
from .contracts import bytes_sha256, canonical_sha256, validate_initialization_object
from .normalization import OutputNormalizer
from .routing import ChannelSelector, SelectionOutcome
from .runner import IsolatedJobRunner, ProcessRun, ProcessSpec
from .workspace import JobWorkspace


@dataclass(frozen=True)
class ExecutionOutcome:
    success: bool
    selection: dict[str, Any]
    route_block_receipt: dict[str, Any] | None
    workspace_manifest: dict[str, Any] | None
    process_receipt: dict[str, Any] | None
    normalized_result: dict[str, Any] | None
    handoff: dict[str, Any]


class ExecutionCore:
    """Compose routing, local process execution, normalization, and ServiceContracts handoff."""

    def __init__(
        self,
        core_schema: Mapping[str, Any],
        selector: ChannelSelector,
        *,
        runner: IsolatedJobRunner | None = None,
        normalizer: OutputNormalizer | None = None,
        handoff: HandoffContract | None = None,
    ):
        self._core_schema = core_schema
        self._selector = selector
        self._runner = runner or IsolatedJobRunner(core_schema)
        self._normalizer = normalizer or OutputNormalizer(core_schema)
        self._handoff = handoff or HandoffContract(core_schema)

    def execute(
        self,
        request: Mapping[str, Any],
        role_binding: Mapping[str, Any],
        profiles: Sequence[Mapping[str, Any]],
        *,
        workspace_parent: str | Path,
        job_id: str,
        attempt_id: str,
        predecessor_attempt_ref: str | None,
        input_materials: Mapping[str, bytes],
        protected_roots: Sequence[str | Path],
        process_spec_factory: Callable[
            [JobWorkspace, Mapping[str, Any], Mapping[str, Any]], ProcessSpec
        ],
        payload_schema_ref: str,
        payload_schema: Mapping[str, Any],
        exact_user_override: str | None = None,
        task_contract_profiles: Sequence[str] = (),
        global_default_profiles: Sequence[str] = (),
    ) -> ExecutionOutcome:
        request_copy = validate_initialization_object(
            request, "ExecutionRequest", self._core_schema
        )
        binding_copy = validate_initialization_object(
            role_binding, "LogicalRoleBinding", self._core_schema
        )
        if payload_schema_ref != request_copy.get("output_schema_ref") or (
            payload_schema_ref != binding_copy.get("output_schema_ref")
        ):
            raise ValueError("OUTPUT_SCHEMA_REF_MISMATCH")
        if predecessor_attempt_ref is not None and (
            not isinstance(predecessor_attempt_ref, str)
            or not predecessor_attempt_ref
            or predecessor_attempt_ref == attempt_id
        ):
            raise ValueError("PREDECESSOR_ATTEMPT_REF_INVALID")
        selection = self._selector.select(
            request_copy,
            binding_copy,
            profiles,
            exact_user_override=exact_user_override,
            task_contract_profiles=task_contract_profiles,
            global_default_profiles=global_default_profiles,
        )
        if selection.selected_profile is None:
            terminal = {
                "terminal_state": "BLOCKED",
                "verification_result": "NOT_ASSESSED",
                "acceptance_verdict": "NOT_ASSESSED",
                "execution_result": "NOT_RUN",
                "error_code": "ROUTE_POLICY_BLOCKED",
                "result_ref": None,
                "physical_process_start_count": 0,
                "external_request_start_count": 0,
                "token_status": "N/A_BLOCKED",
                "cost_status": "N/A_BLOCKED",
                "attempt_lineage": {
                    "attempt_id": attempt_id,
                    "predecessor_attempt_ref": predecessor_attempt_ref,
                },
            }
            assert selection.block_receipt is not None
            handoff = self._handoff.build(
                job_id=job_id,
                attempt_id=attempt_id,
                objects=(
                    ("selection", selection.decision),
                    ("route_block", selection.block_receipt),
                ),
                terminal=terminal,
            )
            return ExecutionOutcome(
                False,
                selection.decision,
                selection.block_receipt,
                None,
                None,
                None,
                handoff,
            )

        assert selection.selected_manifest is not None
        selected_manifest = selection.selected_manifest
        selected_adapter_id = selected_manifest.get("adapter_id")
        executable_name = selected_manifest.get("executable_name")
        if not isinstance(executable_name, str) or not executable_name:
            raise ValueError("SELECTED_ADAPTER_EXECUTABLE_INVALID")
        request_timeout = request_copy.get("timeout_seconds")
        binding_timeout = binding_copy.get("timeout_seconds")
        timeout_limits = [
            float(value)
            for value in (request_timeout, binding_timeout)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        contract_timeout = min(timeout_limits) if timeout_limits else None
        manifest_extensions = selected_manifest.get("extensions")
        if not isinstance(manifest_extensions, Mapping):
            raise ValueError("SELECTED_ADAPTER_EXTENSIONS_INVALID")
        adapter_read_roots = manifest_extensions.get("read_roots", [])
        if not isinstance(adapter_read_roots, list) or any(
            not isinstance(value, str) for value in adapter_read_roots
        ):
            raise ValueError("SELECTED_ADAPTER_READ_ROOTS_INVALID")
        workspace_policy = selection.selected_profile.get("workspace_policy")
        output_contract = selection.selected_profile.get("output_contract")
        if not isinstance(workspace_policy, Mapping) or not isinstance(
            output_contract, Mapping
        ):
            raise ValueError("SELECTED_PROFILE_PROCESS_POLICY_INVALID")
        environment_allowlist = workspace_policy.get("environment_allowlist")
        raw_cap_bytes = output_contract.get("raw_cap_bytes")
        declared_schema_hash = output_contract.get("schema_sha256")
        if (
            not isinstance(environment_allowlist, list)
            or any(not isinstance(value, str) for value in environment_allowlist)
            or not isinstance(raw_cap_bytes, int)
            or isinstance(raw_cap_bytes, bool)
            or raw_cap_bytes <= 0
            or not isinstance(declared_schema_hash, str)
        ):
            raise ValueError("SELECTED_PROFILE_PROCESS_POLICY_INVALID")
        if declared_schema_hash != canonical_sha256(payload_schema):
            raise ValueError("OUTPUT_SCHEMA_HASH_MISMATCH")
        declared_input_hashes = request_copy.get("input_hashes")
        if not isinstance(declared_input_hashes, list):
            raise ValueError("EXECUTION_REQUEST_INPUT_HASHES_INVALID")
        material_names = sorted(input_materials)
        declaration_names = sorted(
            entry.get("relative_path")
            for entry in declared_input_hashes
            if isinstance(entry, Mapping)
            and isinstance(entry.get("relative_path"), str)
        )
        if material_names != declaration_names:
            raise ValueError("EXECUTION_REQUEST_INPUT_SET_MISMATCH")
        for name, data in input_materials.items():
            if not isinstance(data, bytes):
                raise ValueError("EXECUTION_INPUT_MATERIAL_TYPE_INVALID")
            declaration = next(
                entry for entry in declared_input_hashes if entry.get("relative_path") == name
            )
            if declaration.get("bytes") != len(data) or declaration.get(
                "sha256"
            ) != bytes_sha256(data):
                raise ValueError("EXECUTION_REQUEST_INPUT_HASH_MISMATCH")
        workspace = JobWorkspace.create(
            workspace_parent,
            job_id,
            self._core_schema,
            process_allowlist=(executable_name,),
            environment_allowlist=environment_allowlist,
            extra_read_roots=adapter_read_roots,
            protected_roots=protected_roots,
            network_allowlist=(),
            selected_adapter_id=str(selected_manifest.get("adapter_id") or ""),
            selected_manifest_hash=str(
                selection.selected_profile.get("adapter_manifest_hash") or ""
            ),
            expected_attempt_id=attempt_id,
            contract_timeout_seconds=contract_timeout,
            input_hashes=declared_input_hashes,
            raw_cap_bytes=raw_cap_bytes,
        )
        for relative_path in material_names:
            workspace.stage_input(relative_path, input_materials[relative_path])
        process_spec = process_spec_factory(
            workspace, selection.selected_profile, selected_manifest
        )
        if process_spec.adapter_id != selected_adapter_id:
            raise ValueError("PROCESS_SPEC_ADAPTER_ID_MISMATCH")
        if process_spec.attempt_id != attempt_id:
            raise ValueError("PROCESS_SPEC_ATTEMPT_ID_MISMATCH")
        if contract_timeout is not None and process_spec.timeout_seconds > contract_timeout:
            raise ValueError("PROCESS_SPEC_TIMEOUT_EXCEEDS_CONTRACT")
        process_run: ProcessRun = self._runner.run(workspace, process_spec)
        normalized = self._normalizer.normalize(
            process_run,
            logical_role=str(request_copy.get("logical_role") or ""),
            payload_schema_ref=payload_schema_ref,
            payload_schema=payload_schema,
        )
        success = bool(normalized["payload_valid"])
        process_started = bool(process_run.receipt["process_started"])
        if success:
            terminal_state = "COMPLETED"
        elif process_run.receipt.get("extensions", {}).get("runner_reason_code") == "PROCESS_CANCELLED":
            terminal_state = "ABORTED"
        elif process_run.receipt.get("timeout"):
            terminal_state = "TIMEOUT"
        elif process_run.receipt.get("killed"):
            terminal_state = "KILLED"
        else:
            terminal_state = "ERROR"
        terminal = {
            "terminal_state": terminal_state,
            "verification_result": "NOT_ASSESSED",
            "acceptance_verdict": "NOT_ASSESSED",
            "execution_result": "SUCCESS" if success else "FAILED",
            "error_code": normalized.get("error_code"),
            "result_ref": (
                f"{normalized['object_id']}@{normalized['revision']}" if success else None
            ),
            "physical_process_start_count": 1 if process_started else 0,
            "external_request_start_count": 0,
            "token_status": "N/A_NON_MODEL_LOCAL",
            "cost_status": "N/A_NON_MODEL_LOCAL",
            "attempt_lineage": {
                "attempt_id": attempt_id,
                "predecessor_attempt_ref": predecessor_attempt_ref,
            },
        }
        handoff = self._handoff.build(
            job_id=job_id,
            attempt_id=attempt_id,
            objects=(
                ("selection", selection.decision),
                ("workspace", workspace.manifest),
                ("process", process_run.receipt),
                ("schema", normalized),
            ),
            terminal=terminal,
        )
        return ExecutionOutcome(
            success,
            selection.decision,
            None,
            workspace.manifest,
            process_run.receipt,
            normalized,
            handoff,
        )
