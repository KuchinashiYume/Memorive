"""Gold-isolated subject protocol for the M14 card-reviewer exam."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from .card_reviewer_exam import (
    ReviewerExamError,
    audit_response,
    build_directed_repair_package,
    mechanically_normalize,
    merge_directed_repair,
    score_form,
    sha256,
)


SubjectCall = Callable[[dict[str, Any]], Mapping[str, Any]]
GoldLoader = Callable[[], Mapping[str, Any]]


def build_initial_request(
    *,
    form: Mapping[str, Any],
    schema: Mapping[str, Any],
    prompt_contract: Mapping[str, Any],
) -> dict[str, Any]:
    request = {
        "phase": "INITIAL_FULL_REVIEW",
        "system": prompt_contract["system"],
        "user": prompt_contract["user_template"],
        "form": deepcopy(dict(form)),
        "output_schema": deepcopy(dict(schema)),
    }
    lowered = str(request).lower()
    if "expected_verdict" in lowered or "required_evidence_refs" in lowered:
        raise ReviewerExamError("INITIAL_REQUEST_CONTAINS_SCORING_FIELDS")
    request["request_payload_hash"] = sha256(request)
    return request


def execute_form(
    *,
    form: Mapping[str, Any],
    schema: Mapping[str, Any],
    prompt_contract: Mapping[str, Any],
    scoring_protocol: Mapping[str, Any],
    subject_call: SubjectCall,
    gold_loader: GoldLoader,
) -> dict[str, Any]:
    initial_request = build_initial_request(form=form, schema=schema, prompt_contract=prompt_contract)
    initial_envelope = subject_call(initial_request)
    execution_status = initial_envelope.get("status")
    if execution_status in {"CAPACITY_LIMIT_REACHED", "TRANSPORT_ERROR", "CONNECTIVITY_ERROR"}:
        return {
            "status": "NOT_ASSESSED_CAPACITY" if execution_status == "CAPACITY_LIMIT_REACHED" else "NOT_ASSESSED_TRANSPORT",
            "score": None,
            "quality_verdict": "NOT_ASSESSED",
            "capacity_is_quality_failure": False,
            "repair_turns": 0,
            "gold_loaded": False,
            "gold_sent_to_subject": False,
            "gold_sent_to_provider": False,
            "requests": [initial_request],
        }
    if execution_status != "COMPLETED":
        raise ReviewerExamError(f"UNKNOWN_SUBJECT_EXECUTION_STATUS:{execution_status}")
    response = initial_envelope.get("response")
    audit = audit_response(form=form, response=response, schema=schema)
    requests = [initial_request]
    repair_case_count = 0
    repair_turns = 0

    if audit["status"] in {"PASS", "MECHANICAL_RECOVERY"}:
        if not isinstance(response, Mapping):
            raise ReviewerExamError("MAPPING_RESPONSE_REQUIRED_AFTER_PASS_AUDIT")
        normalized = mechanically_normalize(form=form, response=response, schema=schema)
        final_response = normalized["normalized_response"]
    elif audit["status"] == "REPAIRABLE":
        if not isinstance(response, Mapping):
            raise ReviewerExamError("REPAIRABLE_RESPONSE_MAPPING_REQUIRED")
        package = build_directed_repair_package(
            form=form,
            original_response=response,
            audit=audit,
            schema=schema,
        )
        repair_request = {
            "phase": "DIRECTED_REPAIR",
            "system": (
                "Perform one exact-set directed repair. Replace all and only the requested rows. "
                "Do not answer any other case and do not include Markdown."
            ),
            "user": package["prompt"],
            "form": package["repair_form"],
            "output_schema": package["repair_schema"],
            "repair_contract_sha256": package["contract"]["contract_sha256"],
        }
        repair_request["request_payload_hash"] = sha256(repair_request)
        requests.append(repair_request)
        repair_envelope = subject_call(repair_request)
        repair_turns = 1
        repair_case_count = len(package["contract"]["repair_exact_set"])
        repair_status = repair_envelope.get("status")
        if repair_status == "CAPACITY_LIMIT_REACHED":
            return {
                "status": "NOT_ASSESSED_CAPACITY",
                "score": None,
                "quality_verdict": "NOT_ASSESSED",
                "capacity_is_quality_failure": False,
                "repair_turns": 1,
                "repair_case_count": repair_case_count,
                "gold_loaded": False,
                "gold_sent_to_subject": False,
                "gold_sent_to_provider": False,
                "requests": requests,
            }
        if repair_status in {"TRANSPORT_ERROR", "CONNECTIVITY_ERROR"}:
            return {
                "status": "NOT_ASSESSED_TRANSPORT",
                "score": None,
                "quality_verdict": "NOT_ASSESSED",
                "capacity_is_quality_failure": False,
                "repair_turns": 1,
                "repair_case_count": repair_case_count,
                "gold_loaded": False,
                "gold_sent_to_subject": False,
                "gold_sent_to_provider": False,
                "requests": requests,
            }
        if repair_status != "COMPLETED" or not isinstance(repair_envelope.get("response"), Mapping):
            return {
                "status": "INVALID_OUTPUT_AFTER_DIRECTED_REPAIR",
                "score": 0.0,
                "quality_verdict": "FAIL",
                "hard_gate_verdict": "FAIL",
                "repair_turns": 1,
                "repair_case_count": repair_case_count,
                "gold_loaded": False,
                "gold_sent_to_subject": False,
                "gold_sent_to_provider": False,
                "requests": requests,
            }
        try:
            merged = merge_directed_repair(
                form=form,
                original_response=response,
                repair_response=repair_envelope["response"],
                schema=schema,
                repair_contract=package["contract"],
            )
        except ReviewerExamError as exc:
            return {
                "status": "INVALID_OUTPUT_AFTER_DIRECTED_REPAIR",
                "score": 0.0,
                "quality_verdict": "FAIL",
                "hard_gate_verdict": "FAIL",
                "error": str(exc),
                "repair_turns": 1,
                "repair_case_count": repair_case_count,
                "gold_loaded": False,
                "gold_sent_to_subject": False,
                "gold_sent_to_provider": False,
                "requests": requests,
            }
        final_response = merged["merged_response"]
    else:
        return {
            "status": "INVALID_OUTPUT_UNRECOVERABLE",
            "score": 0.0,
            "quality_verdict": "FAIL",
            "hard_gate_verdict": "FAIL",
            "repair_turns": 0,
            "repair_case_count": 0,
            "gold_loaded": False,
            "gold_sent_to_subject": False,
            "gold_sent_to_provider": False,
            "requests": requests,
            "audit": audit,
        }

    final_response_hash = sha256(final_response)
    gold = gold_loader()
    score = score_form(
        form=form,
        final_response=final_response,
        gold=gold,
        schema=schema,
        scoring_protocol=scoring_protocol,
        repair_metrics={
            "form_case_count": len(form["cases"]),
            "repair_turns": repair_turns,
            "directed_repair_case_count": repair_case_count,
            "all_repairs_succeeded": True,
        },
    )
    return {
        **score,
        "final_response": final_response,
        "final_response_hash_before_gold_load": final_response_hash,
        "gold_loaded": True,
        "gold_loaded_after_final_response_freeze": True,
        "gold_sent_to_subject": False,
        "gold_sent_to_provider": False,
        "requests": requests,
        "repair_turns": repair_turns,
        "repair_case_count": repair_case_count,
    }
