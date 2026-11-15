from __future__ import annotations

import json
from pathlib import Path
import sys


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
WEB_ROOT = ATTEMPT_ROOT / "product" / "web"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from memorive_settings.controller import SettingsController  # noqa: E402
from memorive_settings.model_validation import (  # noqa: E402
    HttpResponse,
    LiveModelValidationRunner,
)


class CatalogTransport:
    def __init__(self, model_ids: list[str]) -> None:
        self.model_ids = model_ids
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> HttpResponse:
        self.calls.append(dict(kwargs))
        return HttpResponse(
            status_code=200,
            body=json.dumps(
                {"data": [{"id": model_id} for model_id in self.model_ids]}
            ).encode("utf-8"),
        )


def _runner(tmp_path: Path, catalog: CatalogTransport) -> LiveModelValidationRunner:
    return LiveModelValidationRunner(
        credential_resolver=lambda _reference: b"fixture-credential",
        preferences_loader=lambda: {
            "proxy_mode": "NONE",
            "proxy_address": "",
            "request_timeout_seconds": 7,
        },
        scratch_root=tmp_path / "validation",
        http_transport=catalog,
    )


def _service(
    model_name: str,
    *,
    provider: str = "DEEPSEEK_API_KEY",
    api_protocol: str | None = None,
    api_base_url: str | None = None,
) -> dict[str, object]:
    service: dict[str, object] = {
        "config_id": "deepseek-primary",
        "provider": provider,
        "credential_ref": "REF:windows:Memorive/deepseek/primary",
        "plan": "MAXIMUM",
        "thinking": False,
        "model_name": model_name,
        "tier": "maximum",
        "thinking_mode": "",
        "connection_status": "UNVERIFIED",
    }
    if api_protocol is not None:
        service["api_protocol"] = api_protocol
    if api_base_url is not None:
        service["api_base_url"] = api_base_url
    return service


def test_live_catalog_failure_returns_exact_choices_and_safe_prefix_suggestion(
    tmp_path: Path,
) -> None:
    for configured, expected in (
        ("DeepSeek-V4-Flash-0731-maximum", "deepseek-v4-flash"),
        ("DeepSeek-V4-Pro-0813-high-quality", "deepseek-v4-pro"),
    ):
        catalog = CatalogTransport(["deepseek-v4-flash", "deepseek-v4-pro"])

        result = _runner(tmp_path, catalog).verify_api_model(_service(configured))

        assert result["status"] == "INVALID"
        assert result["reason"] == "API_MODEL_NOT_LISTED"
        assert result["returned_model"] is None
        assert result["available_models"] == [
            "deepseek-v4-flash",
            "deepseek-v4-pro",
        ]
        assert result["suggested_model"] == expected
        assert result["external_model_calls"] == 0
        assert len(catalog.calls) == 1


def test_live_catalog_never_guesses_an_unrelated_custom_provider_model(
    tmp_path: Path,
) -> None:
    model_ids = [f"relay-model-{index:02d}" for index in range(60)]
    catalog = CatalogTransport(model_ids)

    result = _runner(tmp_path, catalog).verify_api_model(
        _service(
            "different-user-model",
            provider="Private Relay",
            api_protocol="OPENAI_COMPATIBLE",
            api_base_url="https://relay.example.com/v1",
        )
    )

    assert result["status"] == "INVALID"
    assert result["suggested_model"] is None
    assert result["available_models"] == model_ids[:50]


def test_validation_receipt_keeps_bounded_catalog_recovery_metadata() -> None:
    controller = object.__new__(SettingsController)
    result = {
        "status": "INVALID",
        "reason": "API_MODEL_NOT_LISTED",
        "requested_model": "DeepSeek-V4-Pro-0813-high-quality",
        "returned_model": None,
        "available_models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "suggested_model": "deepseek-v4-pro",
        "duration_ms": 2,
        "external_network_calls": 1,
        "provider_calls": 1,
        "external_model_calls": 0,
        "external_process_launches": 0,
    }

    receipt = controller._validation_receipt(
        kind="API",
        target_ref="deepseek-primary",
        result=result,
        envelope={"revision": 3, "settings_sha256": "a" * 64},
        connection_status="INVALID",
        persistent_mutation=True,
    )

    assert receipt["available_models"] == [
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    ]
    assert receipt["suggested_model"] == "deepseek-v4-pro"


def test_api_validation_ui_offers_the_live_catalog_model_without_guessing_pass() -> None:
    bridge = (WEB_ROOT / "settings_bridge.js").read_text(encoding="utf-8")

    assert "renderApiValidationReceipt" in bridge
    assert "receipt.available_models" in bridge
    assert "receipt.suggested_model" in bridge
    assert ".dataset.useCatalogModel" in bridge
    assert "applyCatalogModel" in bridge
