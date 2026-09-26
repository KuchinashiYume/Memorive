from __future__ import annotations

from typing import Any, Mapping

from .contracts import ReviewOrchestrationContractError, canonical_hash


EXPECTED_FAULT_GROUPS = {
    "DUPLICATE_CREATE",
    "EVENT_REORDER",
    "CONCURRENT_CLAIM",
    "PRE_SEND_CRASH",
    "UNKNOWN_AFTER_SEND",
    "DURABLE_TERMINAL_REPLAY",
    "TIMEOUT_KILL_TREE",
    "PAUSE_RESUME",
    "REPEATED_CANCEL",
    "LOCK_MATRIX",
    "PARTIAL_OUTPUT",
    "DUPLICATE_TERMINAL_AND_COST",
}


def qualify_offline_run(
    *,
    denominator: Mapping[str, int],
    scenario_results: Mapping[str, bool],
    fault_results: Mapping[str, bool],
    replay_results: Mapping[str, bool],
    effects: Mapping[str, Any],
) -> dict[str, Any]:
    required_denominator = {
        "raw_event_deliveries": 14,
        "unique_event_identities": 12,
        "unique_analysis_subjects": 12,
        "trigger_decisions": 12,
    }
    denominator_pass = all(denominator.get(key) == value for key, value in required_denominator.items())
    fault_exact = set(fault_results) == EXPECTED_FAULT_GROUPS and all(fault_results.values())
    replay_pass = bool(replay_results) and all(replay_results.values())
    zero_effects = (
        effects.get("network_calls") == 0
        and effects.get("external_model_api_cli_calls") == 0
        and effects.get("credential_reads") == 0
        and str(effects.get("cost_cny")) in {"0", "0.0", "0.00"}
        and effects.get("m8_mutation_count") == 0
        and effects.get("card_mutation_count") == 0
        and effects.get("formal_m11_m13_analysis_mutation_count") == 0
        and effects.get("production_scheduler_activation") is False
        and effects.get("production_worker_activation") is False
    )
    checks = {
        "denominator_12_subjects_14_deliveries": denominator_pass,
        "all_scenarios_pass": bool(scenario_results) and all(scenario_results.values()),
        "twelve_fault_groups_pass": fault_exact,
        "projection_and_serialization_replay_pass": replay_pass,
        "zero_external_network_credential_cost": zero_effects,
        "zero_m8_card_formal_mutation": zero_effects,
        "production_activation_disabled": zero_effects,
    }
    body = {
        "schema_version": "P06T09ReviewOrchestrationQualification-v1",
        "run": {
            "lifecycle_status": "completed",
            "verification_result": "PASS" if all(checks.values()) else "FAIL",
            "acceptance_verdict": "NOT_ASSESSED",
        },
        "capability_verdict": "REVIEW_ELIGIBLE_PASS" if all(checks.values()) else "TARGETED_SUCCESSOR_REQUIRED",
        "denominator": dict(denominator),
        "scenario_passed": sum(scenario_results.values()),
        "scenario_total": len(scenario_results),
        "fault_passed": sum(fault_results.values()),
        "fault_total": len(fault_results),
        "replay_passed": sum(replay_results.values()),
        "replay_total": len(replay_results),
        "effects": dict(effects),
        "checks": checks,
        "human_gate_b": "PENDING",
        "formalization": False,
        "production_activation": False,
    }
    return {**body, "content_hash": canonical_hash(body)}


__all__ = ["EXPECTED_FAULT_GROUPS", "qualify_offline_run"]
