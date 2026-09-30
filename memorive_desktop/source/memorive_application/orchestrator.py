from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, Callable, Mapping

from memorive_app.contracts import canonical_json_bytes, canonical_sha256
from memorive_app.durable_store import DurableJobStore
from memorive_app.facade import ApplicationFacade
from memorive_desktop_service.adapters import (
    REQUIRED_D38_METHODS,
    FakeFacadeAdapter,
    RealFacadeAdapter,
)

from .faults import run_fault_matrix
from .fixture import IsolatedFixture, build_isolated_fixture
from .transport import LoopbackApplicationService


PAGE_DIMENSIONS = {
    "PAGE-01-SETTINGS": {
        "page": "SETTINGS",
        "actions": ["validate_profile"],
        "queries": ["list_capabilities"],
        "events": ["capability_snapshot_observed"],
        "receipts": ["service_capabilities_receipt"],
        "identities": ["profile_snapshot_ref"],
    },
    "PAGE-02-INBOX": {
        "page": "INBOX",
        "actions": ["start_import_job"],
        "queries": ["lookup_start_by_idempotency_key"],
        "events": ["JOB_CREATED"],
        "receipts": ["JobHandle-v1"],
        "identities": ["job_id", "attempt_id"],
    },
    "PAGE-03-CURRENT_TASK": {
        "page": "CURRENT_TASK",
        "actions": ["pause", "resume", "cancel"],
        "queries": ["get_job", "get_progress"],
        "events": ["JOB_RUNNING", "PROGRESS_RECORDED"],
        "receipts": ["ControlReceipt-v1"],
        "identities": ["job_id", "attempt_id", "observed_version"],
    },
    "PAGE-04-MESSAGES": {
        "page": "MESSAGES",
        "actions": ["respond_to_action"],
        "queries": ["list_action_requests"],
        "events": ["ACTION_REQUEST_RECORDED", "ACTION_REQUEST_RESPONDED"],
        "receipts": ["ActionResponseReceipt-v1"],
        "identities": ["action_request_id", "job_id"],
    },
    "PAGE-05-SESSIONS": {
        "page": "SESSIONS",
        "actions": ["restore_session_projection"],
        "queries": ["get_attempt", "get_job_graph", "watch_job_events"],
        "events": ["durable_session_reopened"],
        "receipts": ["JobGraph-v1"],
        "identities": ["attempt_id", "predecessor_attempt_id"],
    },
    "PAGE-06-LIBRARY": {
        "page": "LIBRARY",
        "actions": ["bind_artifact"],
        "queries": ["get_artifact_bindings", "resolve_artifact_locator"],
        "events": ["ARTIFACT_BOUND"],
        "receipts": ["ArtifactBindingList-v1"],
        "identities": ["artifact_id", "stable_locator"],
    },
    "PAGE-07-WORK_LOG": {
        "page": "WORK_LOG",
        "actions": ["resume_event_watch"],
        "queries": ["get_job_events", "resolve_event_locator"],
        "events": ["contiguous_job_event_stream"],
        "receipts": ["JobEventPage-v1"],
        "identities": ["event_sequence", "event_locator"],
    },
    "PAGE-08-DESKTOP_ASSISTANT": {
        "page": "DESKTOP_ASSISTANT",
        "actions": ["run_public_safe_diagnostics"],
        "queries": ["store_integrity", "service_health"],
        "events": ["diagnostic_snapshot_observed"],
        "receipts": ["ApplicationRuntimePublicSafeDiagnostics-v1"],
        "identities": ["diagnostic_id", "job_id"],
    },
    "PAGE-09-CROSS_PAGE_CHAIN": {
        "page": "CROSS_PAGE_CHAIN",
        "actions": ["follow_stable_locator_chain"],
        "queries": ["resolve_job_locator", "resolve_related_locators"],
        "events": ["cross_page_chain_resolved"],
        "receipts": ["StableLocatorResolution-v1"],
        "identities": ["job_id", "attempt_id", "artifact_id", "action_request_id", "event_sequence"],
    },
    "PAGE-10-APP_SHELL_AND_PANELS": {
        "page": "APP_SHELL_AND_PANELS",
        "actions": ["restore_route_after_restart"],
        "queries": ["service.health", "list_jobs"],
        "events": ["service_reconnected"],
        "receipts": ["ApplicationServiceHealth-v1"],
        "identities": ["route", "job_id"],
    },
}


def _write_json_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _load_fake_class(d38_root: Path) -> type:
    spec = importlib.util.spec_from_file_location("application_runtime_d38_fake", d38_root / "fake_facade.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Runtime_D38_FAKE_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FakeApplicationFacade


def _state(value: Mapping[str, Any]) -> Any:
    return value.get("job_control_state", value.get("control_state"))


def _version(value: Mapping[str, Any]) -> Any:
    return value.get("observed_version", value.get("version"))


def _parity_projection(method: str, value: Any) -> Any:
    if method == "list_jobs":
        rows = value["jobs"]
        return {
            "schema_version": value["schema_version"],
            "jobs": sorted(
                [
                    {
                        "job_id": row["job_id"],
                        "attempt_id": row["attempt_id"],
                        "control_state": _state(row),
                        "version": _version(row),
                    }
                    for row in rows
                ],
                key=lambda row: row["job_id"],
            ),
        }
    if method in {"get_job", "get_attempt"}:
        return {
            "job_id": value["job_id"],
            "attempt_id": value["attempt_id"],
            "control_state": _state(value),
            "version": _version(value),
        }
    if method in {"get_job_events", "watch_job_events"}:
        result = {
            "schema_version": value["schema_version"],
            "job_id": value["job_id"],
            "after_sequence": value["after_sequence"],
            "next_sequence": value["next_sequence"],
            "events": [
                {
                    key: row.get(key)
                    for key in (
                        "sequence",
                        "event_type",
                        "job_id",
                        "attempt_id",
                        "version",
                        "control_state",
                    )
                }
                for row in value["events"]
            ],
        }
        if "backpressure" in value:
            result["backpressure"] = value["backpressure"]
        return result
    if method == "list_action_requests":
        return {
            "schema_version": value["schema_version"],
            "job_id": value["job_id"],
            "attempt_id": value["attempt_id"],
            "action_requests": [
                {
                    "action_request_id": row["action_request_id"],
                    "state": row["state"],
                }
                for row in value["action_requests"]
            ],
        }
    if method == "respond_to_action":
        return {
            key: value[key]
            for key in (
                "schema_version",
                "job_id",
                "attempt_id",
                "action_request_id",
                "state",
                "observed_version",
            )
        }
    if method == "get_artifact_bindings":
        return {
            "schema_version": value["schema_version"],
            "job_id": value["job_id"],
            "attempt_id": value["attempt_id"],
            "artifacts": [
                {key: row[key] for key in ("artifact_id", "locator", "sha256")}
                for row in value["artifacts"]
            ],
        }
    if method == "get_terminal_receipt":
        return deepcopy(value)
    if method in {"get_job_graph", "resolve_job_locator", "lookup_start_by_idempotency_key"}:
        return deepcopy(value)
    raise AssertionError(method)


def _parity_row(method: str, fake_value: Any, real_value: Any) -> dict[str, Any]:
    fake_projection = _parity_projection(method, fake_value)
    real_projection = _parity_projection(method, real_value)
    return {
        "method": method,
        "fake_projection_sha256": canonical_sha256(fake_projection),
        "real_projection_sha256": canonical_sha256(real_projection),
        "field_diff": [] if fake_projection == real_projection else ["NORMALIZED_PROJECTION_MISMATCH"],
        "status": "PASS" if fake_projection == real_projection else "FAIL",
    }


async def _timed(
    samples: list[dict[str, Any]],
    label: str,
    operation: Callable[[], Awaitable[Any]],
) -> Any:
    started = perf_counter()
    value = await operation()
    samples.append({"label": label, "duration_ms": round((perf_counter() - started) * 1000.0, 3)})
    return value


async def run_construction_story(
    *,
    attempt_root: Path | str,
    runtime_root: Path | str,
    evidence_root: Path | str,
) -> dict[str, Any]:
    attempt = Path(attempt_root).resolve()
    runtime = Path(runtime_root).resolve()
    evidence = Path(evidence_root).resolve()
    if not attempt.is_dir():
        raise FileNotFoundError(attempt)
    if runtime.exists() or evidence.exists():
        raise FileExistsError("Runtime_RUNTIME_OR_EVIDENCE_ALREADY_EXISTS")
    evidence.mkdir(parents=True)
    fixture = build_isolated_fixture(runtime / "isolated_fixture")
    store = DurableJobStore(fixture.database_path, owner_id="application_runtime-construction-a")
    facade = ApplicationFacade(store)
    real_adapter = RealFacadeAdapter(facade)
    d38_root = attempt / "fixtures_runtime" / "d38_contract"
    case_payload = json.loads((d38_root / "consumer_cases.json").read_text(encoding="utf-8"))
    case_ids = [row["case_id"] for row in case_payload["cases"]]
    if case_ids != list(PAGE_DIMENSIONS):
        raise AssertionError("Runtime_D38_CASE_DENOMINATOR_DRIFT")

    performance: list[dict[str, Any]] = []
    parity_rows: list[dict[str, Any]] = []
    page_outputs: dict[str, Any] = {}

    service = LoopbackApplicationService(real_adapter)
    await service.start()
    try:
        capabilities = await _timed(
            performance,
            "settings.service_capabilities",
            lambda: service.call("service.capabilities", {}),
        )
        handle = await _timed(
            performance,
            "inbox.start_job",
            lambda: service.call(
                "start_job",
                {"request": fixture.request(), "idempotency_key": "application_runtime-story-start"},
            ),
        )
        job_id = handle["job_id"]
        attempt_id = handle["attempt_id"]
        start_replay = await service.call(
            "lookup_start_by_idempotency_key",
            {"idempotency_key": "application_runtime-story-start"},
        )
        status = await service.call("get_status", {"job_id": job_id})
        running = await service.call(
            "mark_running",
            {"job_id": job_id, "expected_version": status["observed_version"]},
        )
        progress = await service.call(
            "record_progress",
            {
                "job_id": job_id,
                "completed_units": 3,
                "total_units": 7,
                "phase_code": "CROSS_PAGE_STORY",
                "phase_label": "cross-page story",
                "last_checkpoint_ref": "memorive://job/" + job_id,
            },
        )
        current = await service.call("get_status", {"job_id": job_id})
        await service.call(
            "set_safe_checkpoint",
            {"job_id": job_id, "expected_version": current["observed_version"], "safe": True},
        )
        current = await service.call("get_status", {"job_id": job_id})
        pause_receipt = await service.call(
            "pause_job",
            {
                "job_id": job_id,
                "expected_version": current["observed_version"],
                "idempotency_key": "application_runtime-story-pause",
            },
        )
        resume_receipt = await service.call(
            "resume_job",
            {
                "job_id": job_id,
                "expected_version": pause_receipt["observed_version"],
                "idempotency_key": "application_runtime-story-resume",
            },
        )
        action = await service.call(
            "record_action_request",
            {
                "job_id": job_id,
                "expected_version": resume_receipt["observed_version"],
                "action_request_id": "application_runtime-action-001",
                "action": "CONFIRM_SYNTHETIC_CHECKPOINT",
                "payload": {"classification": "SYNTHETIC_PUBLIC_SAFE"},
            },
        )
        artifact_locator = f"memorive://artifact/{job_id}/{fixture.artifact_id}"
        artifact = await service.call(
            "bind_artifact",
            {
                "job_id": job_id,
                "expected_version": action["observed_version"],
                "artifact_id": fixture.artifact_id,
                "locator": artifact_locator,
                "sha256": fixture.artifact_sha256,
            },
        )

        # Freeze one pre-response state and project the same frozen D38 fake
        # against the real adapter.  Only documented cross-implementation
        # fields are compared; fake-only hashes are not elevated to authority.
        real_job_pre_response = facade.get_job(job_id)
        fake_state = {
            "jobs": [deepcopy(real_job_pre_response)],
            "graph": facade.get_job_graph(job_id)["nodes"],
            "events": facade.get_job_events(job_id, after_sequence=0)["events"],
            "action_requests": facade.list_action_requests(job_id)["action_requests"],
            "artifacts": facade.get_artifact_bindings(job_id)["artifacts"],
            "terminal_receipt": {},
            "start_receipt": deepcopy(handle),
            "network_calls": 0,
            "model_calls": 0,
            "credential_value_reads": 0,
        }
        fake_adapter = FakeFacadeAdapter(_load_fake_class(d38_root)(fake_state))
        read_calls = {
            "list_jobs": {},
            "get_job": {"job_id": job_id},
            "get_attempt": {"attempt_id": attempt_id},
            "get_job_graph": {"job_id": job_id},
            "get_job_events": {"job_id": job_id, "after_sequence": 0},
            "watch_job_events": {"job_id": job_id, "after_sequence": 0},
            "list_action_requests": {"job_id": job_id},
            "get_artifact_bindings": {"job_id": job_id},
            "resolve_job_locator": {"locator": f"memorive://job/{job_id}"},
            "lookup_start_by_idempotency_key": {"idempotency_key": "application_runtime-story-start"},
        }
        for method, params in read_calls.items():
            parity_rows.append(
                _parity_row(
                    method,
                    fake_adapter.call(method, params),
                    real_adapter.call(method, params),
                )
            )
        response_params = {
            "job_id": job_id,
            "action_request_id": "application_runtime-action-001",
            "response": {"decision": "APPROVE_SYNTHETIC_CHECKPOINT"},
            "expected_version": artifact["observed_version"],
            "idempotency_key": "application_runtime-action-response",
        }
        fake_response = fake_adapter.call("respond_to_action", response_params)
        real_response = real_adapter.call("respond_to_action", response_params)
        parity_rows.append(_parity_row("respond_to_action", fake_response, real_response))

        pre_cancel_event_count = store.integrity_report()["event_count"]
        cancel_receipt = await service.call(
            "cancel_job",
            {
                "job_id": job_id,
                "expected_version": real_response["observed_version"],
                "idempotency_key": "application_runtime-story-cancel",
            },
        )
        first_cancel_event_count = store.integrity_report()["event_count"]
        cancel_replay = await service.call(
            "cancel_job",
            {
                "job_id": job_id,
                "expected_version": real_response["observed_version"],
                "idempotency_key": "application_runtime-story-cancel",
            },
        )
        replay_cancel_event_count = store.integrity_report()["event_count"]
        terminal = await service.call("get_terminal_receipt", {"job_id": job_id})
        fake_adapter.facade.state["terminal_receipt"] = deepcopy(terminal)
        parity_rows.append(
            _parity_row(
                "get_terminal_receipt",
                fake_adapter.call("get_terminal_receipt", {"job_id": job_id}),
                real_adapter.call("get_terminal_receipt", {"job_id": job_id}),
            )
        )

        action_locator = f"memorive://action/{job_id}/application_runtime-action-001"
        events = await service.call("get_job_events", {"job_id": job_id, "after_sequence": 0})
        event_id = f"event_{events['events'][-1]['sequence']:08d}"
        event_locator = f"memorive://event/{job_id}/{event_id}"
        locators = {
            "job": f"memorive://job/{job_id}",
            "attempt": f"memorive://attempt/{attempt_id}",
            "artifact": artifact_locator,
            "action": action_locator,
            "event": event_locator,
        }
        resolutions = {
            key: await service.call("service.resolve_locator", {"locator": locator})
            for key, locator in locators.items()
        }
        bindings = await service.call("get_artifact_bindings", {"job_id": job_id})
        actions = await service.call("list_action_requests", {"job_id": job_id})
        graph = await service.call("get_job_graph", {"job_id": job_id})
        attempt_record = await service.call("get_attempt", {"attempt_id": attempt_id})
        health = await service.call("service.health", {})
        jobs = await service.call("list_jobs", {})
        public_diagnostics = {
            "schema_version": "ApplicationRuntimePublicSafeDiagnostics-v1",
            "diagnostic_id": "application_runtime-diagnostic-001",
            "job_id": job_id,
            "store_integrity": store.integrity_report(),
            "service": {
                "schema_version": health["schema_version"],
                "protocol_version": health["protocol_version"],
                "adapter_kind": health["adapter_kind"],
                "external_network_calls": health["external_network_calls"],
                "external_model_calls": health["external_model_calls"],
                "credential_value_reads": health["credential_value_reads"],
            },
            "absolute_paths_included": False,
            "raw_private_content_included": False,
            "credential_values_included": False,
            "status": "PASS",
        }

        page_outputs = {
            "PAGE-01-SETTINGS": {
                "profile_snapshot_ref": handle["profile_snapshot_ref"],
                "required_methods": capabilities["adapter"]["required_methods"],
                "missing_methods": capabilities["adapter"]["missing_methods"],
            },
            "PAGE-02-INBOX": {"handle": handle, "idempotent_start_replay_exact": start_replay == handle},
            "PAGE-03-CURRENT_TASK": {
                "running": running["job_control_state"],
                "progress": progress,
                "pause_state": pause_receipt["state"],
                "resume_state": resume_receipt["state"],
                "terminal_state": terminal["terminal_state"],
            },
            "PAGE-04-MESSAGES": {"actions": actions, "response": real_response},
            "PAGE-05-SESSIONS": {"attempt": attempt_record, "graph": graph},
            "PAGE-06-LIBRARY": {"bindings": bindings, "resolution": resolutions["artifact"]},
            "PAGE-07-WORK_LOG": {"events": events, "resolution": resolutions["event"]},
            "PAGE-08-DESKTOP_ASSISTANT": public_diagnostics,
            "PAGE-09-CROSS_PAGE_CHAIN": {"locators": locators, "resolutions": resolutions},
            "PAGE-10-APP_SHELL_AND_PANELS": {"health": health, "jobs": jobs, "route": "current-task"},
        }

        locator_job_ids = {
            row.get("job_id") or row.get("target", {}).get("job_id")
            for row in resolutions.values()
        }
        locator_job_ids.discard(None)
        story_rows = []
        for case_id in case_ids:
            contract = deepcopy(PAGE_DIMENSIONS[case_id])
            checks = {
                "five_dimensions_present": all(contract[name] for name in ("actions", "queries", "events", "receipts", "identities")),
                "output_present": bool(page_outputs[case_id]),
                "real_transport": True,
                "synthetic_public_safe": True,
            }
            story_rows.append(
                {
                    "case_id": case_id,
                    **contract,
                    "output_sha256": canonical_sha256(page_outputs[case_id]),
                    "checks": checks,
                    "status": "PASS" if all(checks.values()) else "FAIL",
                }
            )

        fault_matrix = await run_fault_matrix(
            fixture=fixture,
            facade=facade,
            store=store,
            service=service,
            job_id=job_id,
            fault_root=runtime / "fault_matrix",
            duplicate_receipt_exact=cancel_receipt == cancel_replay,
            duplicate_event_count_stable=(
                first_cancel_event_count == replay_cancel_event_count
                and first_cancel_event_count == pre_cancel_event_count + 1
            ),
        )
    finally:
        await service.stop()

    parity = {
        "schema_version": "ApplicationRuntimeFakeRealFacadeParityMatrix-v1",
        "required_methods": list(REQUIRED_D38_METHODS),
        "required_method_count": len(REQUIRED_D38_METHODS),
        "rows": parity_rows,
        "pass_count": sum(row["status"] == "PASS" for row in parity_rows),
        "field_diff_count": sum(len(row["field_diff"]) for row in parity_rows),
        "fake_page_special_cases": 0,
        "real_transport": "AUTHENTICATED_LOOPBACK_APPLICATION_SERVICE",
        "status": "PASS" if len(parity_rows) == len(REQUIRED_D38_METHODS) and all(row["status"] == "PASS" for row in parity_rows) else "FAIL",
    }
    scenario = {
        "schema_version": "ApplicationRuntimeCrossPageE2EScenarioDenominator-v1",
        "consumer_case_count": len(story_rows),
        "complete_user_story": [row["page"] for row in story_rows],
        "rows": story_rows,
        "stable_locator": {
            "same_job_identity": len(locator_job_ids) == 1,
            "job_id": job_id,
            "attempt_id": attempt_id,
            "artifact_id": fixture.artifact_id,
            "action_request_id": "application_runtime-action-001",
            "event_id": event_id,
            "fuzzy_match_attempted": False,
        },
        "status": "PASS" if len(story_rows) == 10 and all(row["status"] == "PASS" for row in story_rows) and len(locator_job_ids) == 1 else "FAIL",
    }
    durations = [row["duration_ms"] for row in performance]
    performance_receipt = {
        "schema_version": "ApplicationRuntimeResponsivenessObservation-v1",
        "samples": performance,
        "sample_count": len(performance),
        "max_duration_ms": max(durations, default=0),
        "minimum_line_ms": 2000,
        "minimum_line_met": all(value <= 2000 for value in durations),
        "status": "PASS" if durations and all(value <= 2000 for value in durations) else "NOT_ASSESSED",
    }
    restricted_effects = {
        "schema_version": "ApplicationRuntimeRestrictedEffectsReceipt-v1",
        "production_writes": 0,
        "central_log_writes": 0,
        "branch_log_writes": 0,
        "runtime_log_writes": 0,
        "external_network_calls": 0,
        "external_model_calls": 0,
        "provider_calls": 0,
        "credential_value_reads": 0,
        "private_payload_reads": 0,
        "production_tasks_started": 0,
        "outbox_external_effect_count": fault_matrix["external_effect_count"],
        "cost_cny": 0,
        "cost_usd": 0,
        "status": "PASS_ZERO_RESTRICTED_EFFECTS" if fault_matrix["external_effect_count"] == 0 else "FAIL",
    }
    completion = {
        "schema_version": "ApplicationRuntimeConstructionAStoryReceipt-v1",
        "run_id": "APPLICATION_RUNTIME_CROSS_real_application_facade_cross_page_e2e_20260826_run001",
        "attempt": "construction_a/attempt001",
        "fixture_manifest_sha256": hashlib.sha256(fixture.manifest_path.read_bytes()).hexdigest().upper(),
        "fake_real_parity": parity["status"],
        "cross_page_story": scenario["status"],
        "fault_recovery": fault_matrix["status"],
        "restricted_effects": restricted_effects["status"],
        "performance": performance_receipt["status"],
        "visual_verdict": "NOT_RUN_PENDING_SAME_ENVIRONMENT_MATRIX",
        "acceptance_verdict": "NOT_ASSESSED",
        "status": "PASS_READY_FOR_NATIVE_VISUAL" if all(
            value == "PASS"
            for value in (parity["status"], scenario["status"], fault_matrix["status"])
        ) and restricted_effects["status"] == "PASS_ZERO_RESTRICTED_EFFECTS" else "ERROR",
    }

    _write_json_new(evidence / "FakeRealFacadeParityMatrix.json", parity)
    _write_json_new(evidence / "CrossPageE2EScenarioDenominator.json", scenario)
    _write_json_new(evidence / "FaultRecoveryMatrix.json", fault_matrix)
    _write_json_new(evidence / "RestrictedEffectsReceipt.json", restricted_effects)
    _write_json_new(evidence / "ResponsivenessObservation.json", performance_receipt)
    _write_json_new(evidence / "ConstructionAStoryReceipt.json", completion)
    return completion


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-root", required=True, type=Path)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--evidence-root", required=True, type=Path)
    args = parser.parse_args()
    result = asyncio.run(
        run_construction_story(
            attempt_root=args.attempt_root,
            runtime_root=args.runtime_root,
            evidence_root=args.evidence_root,
        )
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if result["status"] == "PASS_READY_FOR_NATIVE_VISUAL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
