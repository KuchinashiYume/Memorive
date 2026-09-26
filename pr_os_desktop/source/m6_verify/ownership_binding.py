"""P06/T08 immutable ownership binding for M4 -> M6 review tasks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from m4_analysis.analysis import analysis_binding_payload
from m3_retrieval.ownership import (
    OWNERSHIP_BINDING_MISMATCH,
    OwnershipContractError,
    OwnershipGuard,
    OwnershipSnapshot,
    propagation_receipt,
    stable_sha256,
)


@dataclass(frozen=True)
class OwnershipReviewTask:
    review_task_ref: str
    review_task_hash: str
    analysis_ref: str
    analysis_hash: str
    context_pack_ref: str
    context_pack_hash: str
    ownership_snapshot: dict
    ownership_binding_status: str
    legacy_binding_status: str
    required_route_class: str
    preflight_status: str
    preflight_error_codes: tuple[str, ...]
    ownership_propagation_receipt: dict


def compute_analysis_binding_hash(analysis_result: Any) -> str:
    raw = getattr(analysis_result, "ownership_snapshot", None)
    if not isinstance(raw, Mapping):
        raise OwnershipContractError(
            OWNERSHIP_BINDING_MISMATCH,
            "AnalysisResult lacks an immutable ownership snapshot",
        )
    snapshot = OwnershipGuard.validate(OwnershipSnapshot.from_dict(raw))
    return stable_sha256(analysis_binding_payload(analysis_result, snapshot=snapshot))


def build_ownership_review_task(
    analysis_result: Any,
    *,
    legacy_data_ownership: str | None = None,
    route_profile: Mapping[str, Any] | None = None,
) -> OwnershipReviewTask:
    raw = getattr(analysis_result, "ownership_snapshot", None)
    if not isinstance(raw, Mapping):
        raise OwnershipContractError(
            OWNERSHIP_BINDING_MISMATCH,
            "legacy AnalysisResult requires an explicit traceable adapter; caller ownership is insufficient",
        )
    snapshot = OwnershipGuard.validate(OwnershipSnapshot.from_dict(raw))
    expected_hash = stable_sha256(analysis_binding_payload(analysis_result, snapshot=snapshot))
    observed_hash = getattr(analysis_result, "analysis_hash", None)
    expected_ref = f"A-{expected_hash.removeprefix('sha256:')[:24]}"
    observed_ref = getattr(analysis_result, "analysis_ref", None)
    if observed_hash != expected_hash or observed_ref != expected_ref:
        raise OwnershipContractError(
            OWNERSHIP_BINDING_MISMATCH,
            "AnalysisResult content/ref/hash binding is stale or tampered",
            details={
                "expected_ref": expected_ref,
                "observed_ref": observed_ref,
                "expected_hash": expected_hash,
                "observed_hash": observed_hash,
            },
        )
    context_pack_ref = getattr(analysis_result, "input_context_pack_ref", None)
    context_pack_hash = getattr(analysis_result, "input_context_pack_hash", None)
    if not isinstance(context_pack_ref, str) or not isinstance(context_pack_hash, str):
        raise OwnershipContractError(
            OWNERSHIP_BINDING_MISMATCH,
            "AnalysisResult lacks its input Context Pack binding",
        )
    if getattr(analysis_result, "ownership_binding_status", None) != "matched":
        raise OwnershipContractError(
            OWNERSHIP_BINDING_MISMATCH,
            "AnalysisResult ownership binding status is not matched",
        )
    native_preflight = OwnershipGuard.preflight(snapshot)
    if getattr(analysis_result, "ownership_required_route_class", None) != native_preflight.required_route_class:
        raise OwnershipContractError(
            OWNERSHIP_BINDING_MISMATCH,
            "AnalysisResult route constraint does not match its ownership snapshot",
        )
    envelope_ref = getattr(analysis_result, "ownership_request_envelope_ref", None)
    envelope_hash = getattr(analysis_result, "ownership_request_envelope_hash", None)
    if not isinstance(envelope_ref, str) or not isinstance(envelope_hash, str):
        raise OwnershipContractError(
            OWNERSHIP_BINDING_MISMATCH,
            "AnalysisResult lacks its request envelope binding",
        )
    envelope_payload = {
        "context_pack_ref": context_pack_ref,
        "context_pack_hash": context_pack_hash,
        "ownership_snapshot": snapshot.to_dict(),
        "ownership_binding_status": getattr(
            analysis_result, "ownership_legacy_binding_status", None
        ),
        "required_route_class": getattr(
            analysis_result, "ownership_required_route_class", None
        ),
        "preflight_status": getattr(analysis_result, "ownership_preflight_status", None),
        "preflight_error_codes": [],
    }
    expected_envelope_hash = stable_sha256(envelope_payload)
    expected_envelope_ref = f"ARE-{expected_envelope_hash.removeprefix('sha256:')[:24]}"
    if envelope_hash != expected_envelope_hash or envelope_ref != expected_envelope_ref:
        raise OwnershipContractError(
            OWNERSHIP_BINDING_MISMATCH,
            "AnalysisResult request envelope ref/hash is stale or tampered",
        )
    receipt = getattr(analysis_result, "ownership_propagation_receipt", None)
    receipt_chain = receipt.get("subject_chain") if isinstance(receipt, Mapping) else None
    receipt_pack = receipt_chain.get("context_pack") if isinstance(receipt_chain, Mapping) else None
    receipt_analysis = receipt_chain.get("analysis_result") if isinstance(receipt_chain, Mapping) else None
    if (
        not isinstance(receipt, Mapping)
        or receipt.get("ownership_digest") != snapshot.ownership_digest
        or receipt.get("result") != "PASS"
        or not isinstance(receipt_pack, Mapping)
        or receipt_pack.get("context_pack_ref") != context_pack_ref
        or receipt_pack.get("context_pack_hash") != context_pack_hash
        or not isinstance(receipt_analysis, Mapping)
        or receipt_analysis.get("analysis_ref") != observed_ref
        or receipt_analysis.get("analysis_hash") != observed_hash
    ):
        raise OwnershipContractError(
            OWNERSHIP_BINDING_MISMATCH,
            "AnalysisResult propagation receipt does not bind its ownership snapshot",
        )
    legacy_status = OwnershipGuard.compare_legacy(snapshot, legacy_data_ownership)
    preflight = OwnershipGuard.preflight(snapshot, route_profile)
    task_payload = {
        "analysis_ref": observed_ref,
        "analysis_hash": observed_hash,
        "context_pack_ref": context_pack_ref,
        "context_pack_hash": context_pack_hash,
        "ownership_snapshot": snapshot.to_dict(),
        "legacy_binding_status": legacy_status,
        "required_route_class": preflight.required_route_class,
        "preflight_status": preflight.preflight_status,
        "preflight_error_codes": list(preflight.error_codes),
    }
    task_hash = stable_sha256(task_payload)
    task_ref = f"ORT-{task_hash.removeprefix('sha256:')[:24]}"
    task_receipt = propagation_receipt(
        snapshot,
        subject_chain={
            "context_pack": {
                "context_pack_ref": context_pack_ref,
                "context_pack_hash": context_pack_hash,
            },
            "analysis_result": {
                "analysis_ref": observed_ref,
                "analysis_hash": observed_hash,
            },
            "m6_review_task": {
                "review_task_ref": task_ref,
                "review_task_hash": task_hash,
            },
        },
        checks=("PACK_TO_ANALYSIS_MATCH", "ANALYSIS_TO_M6_MATCH"),
    )
    return OwnershipReviewTask(
        review_task_ref=task_ref,
        review_task_hash=task_hash,
        analysis_ref=observed_ref,
        analysis_hash=observed_hash,
        context_pack_ref=context_pack_ref,
        context_pack_hash=context_pack_hash,
        ownership_snapshot=snapshot.to_dict(),
        ownership_binding_status="matched",
        legacy_binding_status=legacy_status,
        required_route_class=preflight.required_route_class,
        preflight_status=preflight.preflight_status,
        preflight_error_codes=preflight.error_codes,
        ownership_propagation_receipt=task_receipt.to_dict(),
    )


__all__ = [
    "OwnershipReviewTask",
    "build_ownership_review_task",
    "compute_analysis_binding_hash",
]
