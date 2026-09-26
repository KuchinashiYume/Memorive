from __future__ import annotations

import base64
import hashlib
from typing import Any, Mapping

from m9_gateway.execution_core.contracts import canonical_sha256

from .contracts import RelayContractError


ALLOWED_IMAGE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/webp"}


def prepare_public_synthetic_ocr(
    payload: Mapping[str, Any], *, max_image_bytes: int = 2 * 1024 * 1024
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prepare an OCR request while exposing only a redacted evidence projection."""

    allowed = {
        "data_classification", "mime_type", "image_bytes", "page_number",
        "source_sha256", "prompt", "detail", "max_tokens",
    }
    if set(payload) - allowed:
        raise RelayContractError("OCR_PAYLOAD_FIELDS_INVALID", sorted(set(payload) - allowed))
    if payload.get("data_classification") != "PUBLIC_SYNTHETIC":
        raise RelayContractError("OCR_DATA_CLASSIFICATION_FORBIDDEN")
    mime_type = payload.get("mime_type")
    if mime_type not in ALLOWED_IMAGE_MEDIA_TYPES:
        raise RelayContractError("OCR_MEDIA_TYPE_FORBIDDEN")
    image = payload.get("image_bytes")
    if not isinstance(image, bytes) or not image or len(image) > max_image_bytes:
        raise RelayContractError("OCR_IMAGE_SIZE_INVALID")
    page_number = payload.get("page_number")
    if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number <= 0:
        raise RelayContractError("OCR_PAGE_NUMBER_INVALID")
    source_sha256 = payload.get("source_sha256")
    if not isinstance(source_sha256, str) or len(source_sha256) != 64:
        raise RelayContractError("OCR_SOURCE_HASH_INVALID")
    try:
        int(source_sha256, 16)
    except ValueError as exc:
        raise RelayContractError("OCR_SOURCE_HASH_INVALID") from exc
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        raise RelayContractError("OCR_PROMPT_INVALID")
    detail = payload.get("detail", "high")
    if detail not in {"auto", "low", "high"}:
        raise RelayContractError("OCR_DETAIL_INVALID")
    encoded = base64.b64encode(image).decode("ascii")
    request_payload = {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}", "detail": detail}},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": payload.get("max_tokens", 128),
        "stream": False,
    }
    evidence = {
        "schema_version": "P05_T12_OCR_PUBLIC_SYNTHETIC_EVIDENCE_V1",
        "data_classification": "PUBLIC_SYNTHETIC",
        "mime_type": mime_type,
        "image_byte_count": len(image),
        "image_sha256": hashlib.sha256(image).hexdigest().upper(),
        "page_number": page_number,
        "source_sha256": source_sha256.upper(),
        "request_payload_sha256": canonical_sha256(request_payload),
        "payload_projection": "IMAGE_AND_PROMPT_REDACTED",
    }
    return request_payload, evidence
