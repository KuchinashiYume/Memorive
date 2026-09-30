from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Callable, Mapping, Sequence

from .contracts import (
    base_object,
    canonical_json_bytes,
    immutable_copy,
    object_ref,
    utc_now,
    validate_initialization_object,
)
from .registry import AdapterRegistry, RegistryViolation


QUALIFICATION_ORDER = {
    "NOT_ASSESSED": 0,
    "Q0": 1,
    "Q1": 2,
    "Q2": 3,
    "Q3": 4,
    "Q4": 5,
    "Q5": 6,
    "EXPIRED": -1,
    "REVOKED": -1,
}
ROUTABLE_STATUSES = {"ENABLED", "ACTIVE", "SHADOW", "TEST_ONLY"}


@dataclass(frozen=True)
class SelectionOutcome:
    decision: dict[str, Any]
    selected_profile: dict[str, Any] | None
    selected_manifest: dict[str, Any] | None
    block_receipt: dict[str, Any] | None


def _stable_id(prefix: str, value: Any) -> str:
    suffix = hashlib.sha256(canonical_json_bytes(value)).hexdigest()[:24]
    return f"{prefix}_{suffix}"


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


class ChannelSelector:
    """Deterministic, fail-closed channel selection without process start."""

    def __init__(
        self,
        core_schema: Mapping[str, Any],
        registry: AdapterRegistry,
        *,
        clock: Callable[[], str] = utc_now,
    ):
        self._core_schema = immutable_copy(core_schema)
        self._registry = registry
        self._clock = clock

    def select(
        self,
        request: Mapping[str, Any],
        role_binding: Mapping[str, Any],
        profiles: Sequence[Mapping[str, Any]],
        *,
        exact_user_override: str | None = None,
        task_contract_profiles: Sequence[str] = (),
        global_default_profiles: Sequence[str] = (),
    ) -> SelectionOutcome:
        req = validate_initialization_object(request, "ExecutionRequest", self._core_schema)
        binding = validate_initialization_object(
            role_binding, "LogicalRoleBinding", self._core_schema
        )
        accepted_profiles = [
            validate_initialization_object(profile, "ExecutionProfile", self._core_schema)
            for profile in profiles
        ]
        profile_by_id: dict[str, dict[str, Any]] = {}
        for profile in accepted_profiles:
            profile_id = profile.get("profile_id")
            if not isinstance(profile_id, str) or not profile_id:
                continue
            if profile_id in profile_by_id:
                raise ValueError(f"EXECUTION_PROFILE_ID_DUPLICATE: {profile_id}")
            profile_by_id[profile_id] = profile

        if exact_user_override is not None:
            decision_source = "exact_user_override"
            candidate_ids = [exact_user_override]
        elif task_contract_profiles:
            decision_source = "task_contract"
            candidate_ids = list(task_contract_profiles)
        elif binding.get("candidate_profile_allowlist"):
            decision_source = "logical_role_binding"
            candidate_ids = _as_string_list(binding["candidate_profile_allowlist"])
        else:
            decision_source = "global_default"
            candidate_ids = list(global_default_profiles)

        evaluations: list[dict[str, Any]] = []
        selected: dict[str, Any] | None = None
        selected_manifest: dict[str, Any] | None = None
        selected_rank: int | None = None
        for rank, profile_id in enumerate(candidate_ids):
            profile = profile_by_id.get(profile_id)
            reasons = self._ineligibility_reasons(req, binding, profile)
            evaluations.append(
                {
                    "profile_id": profile_id,
                    "fallback_rank": rank,
                    "eligible": not reasons,
                    "reason_codes": reasons,
                }
            )
            if selected is None and profile is not None and not reasons:
                selected = profile
                extensions = profile["extensions"]
                record = self._registry.resolve(
                    profile["adapter_id"],
                    extensions["adapter_manifest_revision"],
                    profile["adapter_manifest_hash"],
                )
                selected_manifest = record.manifest
                selected_rank = rank

        decision_seed = {
            "request_ref": object_ref(req),
            "role_binding_ref": object_ref(binding),
            "decision_source": decision_source,
            "candidate_ids": candidate_ids,
            "evaluations": evaluations,
            "selected": object_ref(selected) if selected else None,
        }
        decision = base_object(
            "ChannelSelectionDecision",
            _stable_id("channelselectiondecision", decision_seed),
            producer="Execution_ROUTER",
            single_writer="Execution_ROUTER",
            consumers=("Execution_JOB_RUNNER", "RUNTIME_LOG", "ARTIFACT_REGISTRY"),
            clock=self._clock,
            source_evidence_refs=(object_ref(req), object_ref(binding)),
            extensions={"candidate_evaluations": evaluations},
        )
        decision.update(
            {
                "request_ref": object_ref(req),
                "role_binding_ref": object_ref(binding),
                "candidate_profiles": candidate_ids,
                "selected_profile_ref": object_ref(selected) if selected else None,
                "decision_code": "ROUTE_SELECTED" if selected else "ROUTE_BLOCKED",
                "reason_codes": []
                if selected
                else sorted({reason for item in evaluations for reason in item["reason_codes"]})
                or ["NO_CANDIDATE_PROFILE"],
                "decision_source": decision_source,
                "fallback_rank": selected_rank,
                "blocked": selected is None,
                "block_receipt_ref": None,
            }
        )

        if selected is not None:
            accepted_decision = validate_initialization_object(
                decision, "ChannelSelectionDecision", self._core_schema
            )
            return SelectionOutcome(
                accepted_decision, selected, selected_manifest, None
            )

        block_seed = {"decision": decision_seed, "reasons": decision["reason_codes"]}
        block = base_object(
            "RouteBlockReceipt",
            _stable_id("routeblockreceipt", block_seed),
            producer="Execution_ROUTER",
            single_writer="Execution_ROUTER",
            consumers=("RUNTIME_LOG", "ARTIFACT_REGISTRY"),
            clock=self._clock,
            source_evidence_refs=(object_ref(req), object_ref(binding)),
            extensions={"candidate_evaluations": evaluations},
        )
        block.update(
            {
                "request_ref": object_ref(req),
                "logical_role": req.get("logical_role"),
                "candidate_profiles": candidate_ids,
                "block_code": "ROUTE_POLICY_BLOCKED",
                "reason_codes": decision["reason_codes"],
                "failed_gate": "route_eligibility",
                "physical_process_start_count": 0,
                "external_request_start_count": 0,
                "token_status": "N/A_BLOCKED",
                "cost_status": "N/A_BLOCKED",
                "resolution_hint": "qualify or explicitly enable an allowed profile",
                "observed_at": self._clock(),
            }
        )
        accepted_block = validate_initialization_object(
            block, "RouteBlockReceipt", self._core_schema
        )
        decision["block_receipt_ref"] = object_ref(accepted_block)
        accepted_decision = validate_initialization_object(
            decision, "ChannelSelectionDecision", self._core_schema
        )
        return SelectionOutcome(accepted_decision, None, None, accepted_block)

    def _ineligibility_reasons(
        self,
        request: Mapping[str, Any],
        binding: Mapping[str, Any],
        profile: Mapping[str, Any] | None,
    ) -> list[str]:
        if profile is None:
            return ["PROFILE_NOT_FOUND"]
        reasons: list[str] = []
        if request.get("logical_role") != binding.get("role_id"):
            reasons.append("LOGICAL_ROLE_BINDING_MISMATCH")
        request_schema = request.get("output_schema_ref")
        binding_schema = binding.get("output_schema_ref")
        if request_schema != binding_schema:
            reasons.append("OUTPUT_SCHEMA_INCOMPATIBLE")
        status = profile.get("status")
        if status not in ROUTABLE_STATUSES:
            reasons.append("PROFILE_DISABLED")

        allowed_profiles = _as_string_list(binding.get("candidate_profile_allowlist"))
        profile_id = profile.get("profile_id")
        if allowed_profiles and profile_id not in allowed_profiles:
            reasons.append("PROFILE_NOT_ROLE_ALLOWED")

        accepted_evidence = _as_string_list(binding.get("acceptable_evidence_classes"))
        if accepted_evidence and profile.get("evidence_class") not in accepted_evidence:
            reasons.append("EVIDENCE_CLASS_INCOMPATIBLE")

        data_rules = binding.get("data_rules")
        if isinstance(data_rules, Mapping):
            classifications = _as_string_list(data_rules.get("allowed_classifications"))
            if classifications and request.get("data_classification") not in classifications:
                reasons.append("DATA_CLASSIFICATION_INCOMPATIBLE")

        allowed_egress = request.get("allowed_egress_class")
        profile_egress = profile.get("egress")
        if allowed_egress not in (None, "any") and profile_egress != allowed_egress:
            reasons.append("EGRESS_INCOMPATIBLE")

        extensions = profile.get("extensions")
        if not isinstance(extensions, Mapping):
            extensions = {}
        required_capabilities = set(_as_string_list(binding.get("required_capabilities")))
        actual_capabilities = set(_as_string_list(extensions.get("capabilities")))
        if not required_capabilities.issubset(actual_capabilities):
            reasons.append("CAPABILITY_MISSING")

        qualification = str(extensions.get("qualification_state", "NOT_ASSESSED"))
        minimum = str(extensions.get("minimum_route_qualification", "Q5"))
        if QUALIFICATION_ORDER.get(qualification, -1) < QUALIFICATION_ORDER.get(minimum, 999):
            reasons.append("QUALIFICATION_INSUFFICIENT")

        if status == "TEST_ONLY":
            fake_allowed = (
                extensions.get("data_contracts_fake_adapter") is True
                and request.get("data_classification") == "synthetic_test"
                and profile.get("evidence_class") == "synthetic_test"
                and profile.get("egress") == "none"
            )
            if not fake_allowed:
                reasons.append("AXIS_COMBINATION_INVALID")

        network_policy = profile.get("network_policy")
        if not isinstance(network_policy, Mapping):
            reasons.append("NETWORK_POLICY_VIOLATION")
        else:
            allowed_network = network_policy.get("allow", [])
            if allowed_network not in ([], ()):
                reasons.append("NETWORK_POLICY_VIOLATION")

        workspace_policy = profile.get("workspace_policy")
        if not isinstance(workspace_policy, Mapping) or workspace_policy.get(
            "mode"
        ) != "isolated_create_only":
            reasons.append("WORKSPACE_POLICY_VIOLATION")
        elif not isinstance(workspace_policy.get("environment_allowlist"), list):
            reasons.append("WORKSPACE_POLICY_VIOLATION")

        output_contract = profile.get("output_contract")
        if not isinstance(output_contract, Mapping):
            reasons.append("OUTPUT_SCHEMA_INCOMPATIBLE")
        else:
            if output_contract.get("schema_ref") != request_schema:
                reasons.append("OUTPUT_SCHEMA_INCOMPATIBLE")
            schema_hash = output_contract.get("schema_sha256")
            if not isinstance(schema_hash, str) or len(schema_hash) != 64 or any(
                character not in "0123456789ABCDEF" for character in schema_hash
            ):
                reasons.append("OUTPUT_SCHEMA_HASH_INVALID")
            raw_cap = output_contract.get("raw_cap_bytes")
            if (
                not isinstance(raw_cap, int)
                or isinstance(raw_cap, bool)
                or raw_cap <= 0
            ):
                reasons.append("OUTPUT_SCHEMA_INCOMPATIBLE")

        request_tools = request.get("tool_policy")
        permission_policy = profile.get("permission_policy")
        if not isinstance(request_tools, Mapping) or not isinstance(
            permission_policy, Mapping
        ):
            reasons.append("WORKSPACE_POLICY_VIOLATION")
        else:
            if request_tools.get("shell") is not False or permission_policy.get(
                "shell"
            ) is not False:
                reasons.append("WORKSPACE_POLICY_VIOLATION")
            requested = set(_as_string_list(request_tools.get("tools")))
            allowed = set(_as_string_list(permission_policy.get("tools")))
            if not requested.issubset(allowed):
                reasons.append("WORKSPACE_POLICY_VIOLATION")

        adapter_id = profile.get("adapter_id")
        revision = extensions.get("adapter_manifest_revision")
        digest = profile.get("adapter_manifest_hash")
        if not all(isinstance(value, str) and value for value in (adapter_id, revision, digest)):
            reasons.append("ADAPTER_MANIFEST_REF_INVALID")
        else:
            try:
                self._registry.resolve(adapter_id, revision, digest)
            except RegistryViolation as exc:
                reasons.append(exc.code)

        return sorted(set(reasons))
