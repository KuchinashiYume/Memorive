from __future__ import annotations

import unicodedata
from typing import Any, Callable, Mapping


KIMI_ENDPOINTS = {
    "CN": "https://api.moonshot.cn/v1",
    "AI": "https://api.moonshot.ai/v1",
}


def _compact(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def is_kimi_service(value: Mapping[str, Any]) -> bool:
    """Recognize a Kimi service from public labels, never from key contents."""

    provider = value.get("provider")
    credential_ref = value.get("credential_ref")
    labels = [
        _compact(candidate)
        for candidate in (provider, credential_ref)
        if isinstance(candidate, str)
    ]
    return any(
        "kimi" in label or "moonshot" in label or "月之暗面" in label
        for label in labels
    )


def kimi_auto_resolution_requested(value: Mapping[str, Any]) -> bool:
    if not is_kimi_service(value):
        return False
    platform = value.get("api_platform")
    if platform is not None:
        return platform == "AUTO"
    return (
        value.get("api_protocol", "AUTO") == "AUTO"
        and value.get("api_base_url", "") == ""
    )


def _platform_name(value: str) -> str | None:
    normalized = value.strip().upper().rstrip("/")
    aliases = {
        "CN": "CN",
        "DOMESTIC": "CN",
        "MOONSHOT_CN": "CN",
        KIMI_ENDPOINTS["CN"].upper(): "CN",
        "AI": "AI",
        "INTERNATIONAL": "AI",
        "MOONSHOT_AI": "AI",
        KIMI_ENDPOINTS["AI"].upper(): "AI",
        "AUTO": "AUTO",
    }
    return aliases.get(normalized)


def _count(value: Mapping[str, Any], key: str) -> int:
    candidate = value.get(key)
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 0:
        return 0
    return candidate


def _safe_attempt(
    platform: str,
    endpoint: str,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "platform": platform,
        "api_base_url": endpoint,
        "status": value.get("status"),
        "reason": value.get("reason"),
        "requested_model": value.get("requested_model"),
        "returned_model": value.get("returned_model"),
        "external_network_calls": _count(value, "external_network_calls"),
        "provider_metadata_calls": _count(value, "provider_calls"),
        "external_model_calls": _count(value, "external_model_calls"),
        "validation_endpoint_kind": value.get("validation_endpoint_kind"),
        "secret_material_recorded": False,
    }


def _result(
    *,
    status: str,
    reason: str,
    platform: str | None,
    api_base_url: str | None,
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "KimiEndpointResolutionResult-v1",
        "status": status,
        "reason": reason,
        "platform": platform,
        "api_base_url": api_base_url,
        "attempts": attempts,
        "external_network_calls": sum(
            int(item["external_network_calls"]) for item in attempts
        ),
        "provider_metadata_calls": sum(
            int(item["provider_metadata_calls"]) for item in attempts
        ),
        "external_model_calls": sum(
            int(item["external_model_calls"]) for item in attempts
        ),
        "exam_payload_included": False,
        "answer_key_included": False,
        "key_text_inspected": False,
        "secret_hash_recorded": False,
        "cache_binding_fields": [
            "credential_ref",
            "credential_revision",
            "platform",
            "api_base_url",
            "requested_model",
        ],
    }


def resolve_kimi_api_platform(
    *,
    credential_ref: str,
    requested_model: str,
    platform: str,
    verify_api_model: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    allow_cross_platform_metadata_probe: bool = False,
) -> dict[str, Any]:
    """Resolve Kimi domestic/international routing through `/models` only.

    Explicit platform selection wins. AUTO is opt-in, tries CN then AI, and
    crosses platforms only after a conclusive authentication rejection.
    """

    if (
        not isinstance(credential_ref, str)
        or not credential_ref
        or not isinstance(requested_model, str)
        or not requested_model
        or not isinstance(platform, str)
        or not callable(verify_api_model)
    ):
        return _result(
            status="NOT_ASSESSED",
            reason="KIMI_PLATFORM_INPUT_INVALID",
            platform=None,
            api_base_url=None,
            attempts=[],
        )
    selected = _platform_name(platform)
    if selected is None:
        return _result(
            status="NOT_ASSESSED",
            reason="KIMI_PLATFORM_INVALID",
            platform=None,
            api_base_url=None,
            attempts=[],
        )
    if selected == "AUTO" and allow_cross_platform_metadata_probe is not True:
        return _result(
            status="NOT_ASSESSED",
            reason="KIMI_PLATFORM_SELECTION_REQUIRED",
            platform=None,
            api_base_url=None,
            attempts=[],
        )

    candidates = ("CN", "AI") if selected == "AUTO" else (selected,)
    attempts: list[dict[str, Any]] = []
    for candidate in candidates:
        endpoint = KIMI_ENDPOINTS[candidate]
        service = {
            "config_id": (
                f"{credential_ref}-kimi-platform-probe-{candidate.casefold()}"
            ),
            "provider": "Kimi",
            "credential_ref": credential_ref,
            "api_protocol": "OPENAI_COMPATIBLE",
            "api_base_url": endpoint,
            "model_name": requested_model,
            "connection_status": "AVAILABLE",
        }
        verification = verify_api_model(service)
        if not isinstance(verification, Mapping):
            return _result(
                status="NOT_ASSESSED",
                reason="KIMI_PLATFORM_PROBE_RECEIPT_INVALID",
                platform=None,
                api_base_url=None,
                attempts=attempts,
            )
        attempt = _safe_attempt(candidate, endpoint, verification)
        attempts.append(attempt)
        if attempt["external_model_calls"] != 0:
            return _result(
                status="NOT_ASSESSED",
                reason="KIMI_PLATFORM_PROBE_NOT_METADATA_ONLY",
                platform=None,
                api_base_url=None,
                attempts=attempts,
            )
        if (
            attempt["status"] == "AVAILABLE"
            and attempt["requested_model"] == requested_model
            and attempt["returned_model"] == requested_model
        ):
            return _result(
                status="RESOLVED",
                reason="KIMI_PLATFORM_MODEL_CATALOG_MATCH",
                platform=candidate,
                api_base_url=endpoint,
                attempts=attempts,
            )
        if attempt["reason"] == "API_MODEL_NOT_LISTED":
            return _result(
                status="NOT_ASSESSED",
                reason="KIMI_MODEL_NOT_LISTED_ON_RESOLVED_PLATFORM",
                platform=candidate,
                api_base_url=endpoint,
                attempts=attempts,
            )
        if selected == "AUTO" and attempt["reason"] != "API_AUTHENTICATION_REJECTED":
            return _result(
                status="NOT_ASSESSED",
                reason="KIMI_PLATFORM_PROBE_INCONCLUSIVE",
                platform=None,
                api_base_url=None,
                attempts=attempts,
            )

    return _result(
        status="NOT_ASSESSED",
        reason="KIMI_PLATFORM_NOT_RESOLVED",
        platform=None,
        api_base_url=None,
        attempts=attempts,
    )


__all__ = [
    "KIMI_ENDPOINTS",
    "is_kimi_service",
    "kimi_auto_resolution_requested",
    "resolve_kimi_api_platform",
]
