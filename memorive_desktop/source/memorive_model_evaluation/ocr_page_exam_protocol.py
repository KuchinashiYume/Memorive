"""Production-shaped request/envelope helpers with live calls disabled."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .ocr_page_exam import ExamMethodError, sha256_json, sha256_text
from .ocr_page_exam_assets import PROMPT_SHA256


class ExternalCallNotAuthorized(RuntimeError):
    """The sandbox candidate intentionally has no provider execution path."""


def _case_by_id(form: Mapping[str, Any], case_id: str) -> Mapping[str, Any]:
    cases = form.get("cases")
    if not isinstance(cases, list):
        raise ExamMethodError("FORM_CASES_NOT_LIST")
    matches = [
        case
        for case in cases
        if isinstance(case, Mapping) and case.get("case_id") == case_id
    ]
    if len(matches) != 1:
        raise ExamMethodError(f"FORM_CASE_LOOKUP_NOT_UNIQUE:{case_id}:{len(matches)}")
    return matches[0]


def build_provider_visible_payload(
    *,
    pack: Path,
    form: Mapping[str, Any],
    case_id: str,
) -> dict[str, Any]:
    case = _case_by_id(form, case_id)
    prompt_path = pack / str(form.get("prompt_ref"))
    prompt_bytes = prompt_path.read_bytes()
    prompt_sha = hashlib.sha256(prompt_bytes).hexdigest().upper()
    if prompt_sha != PROMPT_SHA256 or prompt_sha != case.get("prompt_sha256"):
        raise ExamMethodError(f"PROMPT_HASH_MISMATCH:{case_id}")
    image_path = pack / str(case.get("image_ref"))
    image_bytes = image_path.read_bytes()
    image_sha = hashlib.sha256(image_bytes).hexdigest().upper()
    if image_sha != case.get("image_sha256"):
        raise ExamMethodError(f"IMAGE_HASH_MISMATCH:{case_id}")
    payload = {
        "schema_version": "model_evaluation-ocr-page-provider-visible-payload-v1",
        "task_type": "ocr_page",
        "mime_type": "image/png",
        "prompt_utf8": prompt_bytes.decode("utf-8"),
        "image_base64": base64.b64encode(image_bytes).decode("ascii"),
        "prompt_sha256": prompt_sha,
        "image_sha256": image_sha,
    }
    payload["payload_sha256"] = sha256_json(payload)
    return payload


def build_gateway_call(
    *,
    pack: Path,
    form: Mapping[str, Any],
    case_id: str,
    data_ownership: str,
) -> dict[str, Any]:
    if data_ownership != "self":
        raise PermissionError(f"OCR_REMOTE_OWNERSHIP_BLOCK:{data_ownership}")
    payload = build_provider_visible_payload(
        pack=pack,
        form=form,
        case_id=case_id,
    )
    case = _case_by_id(form, case_id)
    call = {
        "schema_version": "model_evaluation-ocr-page-local-gateway-call-v1",
        "case_id": case_id,
        "task_type": "ocr_page",
        "mime_type": "image/png",
        "page_number": 1,
        "source_sha256": case["image_sha256"],
        "data_ownership": "self",
        "provider_visible_payload": payload,
        "gold_included": False,
        "scoring_included": False,
        "expected_role_included": False,
    }
    call["gateway_call_sha256"] = sha256_json(call)
    return call


def freeze_output_envelope(
    *,
    form: Mapping[str, Any],
    case_id: str,
    output_text: str,
    transport_status: str,
    finish_reason: str,
) -> dict[str, Any]:
    case = _case_by_id(form, case_id)
    if not isinstance(output_text, str) or not output_text.strip():
        raise ValueError("OCR_OUTPUT_TEXT_EMPTY")
    envelope = {
        "case_id": case_id,
        "image_sha256": case["image_sha256"],
        "output_text": output_text,
        "output_sha256": sha256_text(output_text),
        "transport_status": transport_status,
        "finish_reason": finish_reason,
    }
    envelope["envelope_sha256"] = sha256_json(envelope)
    return envelope


def repair_envelope_preserving_text(
    *,
    original: Mapping[str, Any],
    metadata_updates: Mapping[str, Any],
) -> dict[str, Any]:
    allowed = {"transport_receipt_ref", "local_envelope_note", "finish_reason"}
    unknown = set(metadata_updates) - allowed
    if unknown:
        raise ExamMethodError(f"ENVELOPE_REPAIR_FIELD_NOT_ALLOWED:{sorted(unknown)}")
    original_text = original.get("output_text")
    original_hash = original.get("output_sha256")
    if not isinstance(original_text, str) or original_hash != sha256_text(original_text):
        raise ExamMethodError("ORIGINAL_ENVELOPE_TEXT_HASH_INVALID")
    repaired = dict(original)
    repaired.pop("envelope_sha256", None)
    repaired.update(metadata_updates)
    if repaired.get("output_text") != original_text:
        raise ExamMethodError("ENVELOPE_REPAIR_CHANGED_TEXT")
    if repaired.get("output_sha256") != original_hash:
        raise ExamMethodError("ENVELOPE_REPAIR_CHANGED_TEXT_HASH")
    repaired["envelope_repair"] = {
        "kind": "LOCAL_METADATA_ONLY",
        "output_sha256_before": original_hash,
        "output_sha256_after": original_hash,
        "content_regenerated": False,
    }
    repaired["envelope_sha256"] = sha256_json(repaired)
    return repaired


def execute_external_provider(*_: Any, **__: Any) -> None:
    raise ExternalCallNotAuthorized(
        "PROVIDER_CALL_NOT_AUTHORIZED_IN_OCR_EXAM_OPTIMIZATION_SANDBOX"
    )
