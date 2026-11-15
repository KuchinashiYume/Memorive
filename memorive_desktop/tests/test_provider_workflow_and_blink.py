from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import sys

import pytest


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
DESKTOP_ROOT = ATTEMPT_ROOT / "product" / "desktop"
WEB_ROOT = ATTEMPT_ROOT / "product" / "web"
for candidate in (SOURCE_ROOT, DESKTOP_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from memorive_settings.contracts import default_settings, validate_settings  # noqa: E402


def _configured_settings() -> dict:
    settings = default_settings()
    settings["credential_references"] = [
        {
            "credential_ref": "REF:windows:Memorive/provider-a/primary",
            "provider": "Provider A",
            "logical_key": "primary",
            "status": "STORED_UNVERIFIED",
            "created_at": "2026-08-27T00:00:00Z",
            "updated_at": "2026-08-27T00:00:00Z",
        },
        {
            "credential_ref": "REF:windows:Memorive/provider-b/primary",
            "provider": "Provider B",
            "logical_key": "primary",
            "status": "STORED_UNVERIFIED",
            "created_at": "2026-08-27T00:00:00Z",
            "updated_at": "2026-08-27T00:00:00Z",
        },
    ]
    settings["model_services"] = [
        {
            "config_id": "provider-a-primary",
            "provider": "Provider A",
            "credential_ref": "REF:windows:Memorive/provider-a/primary",
            "plan": "STANDARD",
            "thinking": False,
            "model_name": "sonnet5",
            "tier": "max",
            "thinking_mode": "",
            "connection_status": "UNVERIFIED",
        },
        {
            "config_id": "provider-b-primary",
            "provider": "Provider B",
            "credential_ref": "REF:windows:Memorive/provider-b/primary",
            "plan": "STANDARD",
            "thinking": False,
            "model_name": "gpt5.6luna",
            "tier": "high",
            "thinking_mode": "",
            "connection_status": "UNVERIFIED",
        },
    ]
    return settings


def _node(settings: dict, node_id: str) -> dict:
    return next(row for row in settings["workflow"]["nodes"] if row["node_id"] == node_id)


def test_same_provider_can_expose_distinct_models_but_not_duplicate_profiles() -> None:
    settings = _configured_settings()
    settings["model_services"][1]["provider"] = "ＰＲＯＶＩＤＥＲ　Ａ"
    settings["credential_references"][1]["provider"] = "ＰＲＯＶＩＤＥＲ　Ａ"
    accepted = validate_settings(settings)
    assert len(accepted["model_services"]) == 2

    settings["model_services"][1]["model_name"] = settings["model_services"][0]["model_name"]
    settings["model_services"][1]["tier"] = settings["model_services"][0]["tier"]
    settings["model_services"][1]["thinking_mode"] = settings["model_services"][0]["thinking_mode"]
    with pytest.raises(ValueError, match="^MODEL_SERVICE_PROFILE_DUPLICATE$"):
        validate_settings(settings)


def test_both_heterogeneous_review_nodes_accept_a_different_configured_model() -> None:
    settings = _configured_settings()
    _node(settings, "card_distill")["profile_ref"] = "provider-a-primary"
    _node(settings, "transport_review")["profile_ref"] = "provider-b-primary"
    _node(settings, "analysis")["profile_ref"] = "provider-b-primary"
    _node(settings, "judgment_review")["profile_ref"] = "provider-a-primary"
    accepted = validate_settings(settings)
    assert _node(accepted, "transport_review")["profile_ref"] == "provider-b-primary"
    assert _node(accepted, "judgment_review")["profile_ref"] == "provider-a-primary"


@pytest.mark.parametrize(
    ("producer", "reviewer"),
    [
        ("card_distill", "transport_review"),
        ("analysis", "judgment_review"),
    ],
)
def test_heterogeneous_review_rejects_the_same_model(
    producer: str, reviewer: str
) -> None:
    settings = _configured_settings()
    _node(settings, producer)["profile_ref"] = "provider-a-primary"
    _node(settings, reviewer)["profile_ref"] = "provider-a-primary"
    with pytest.raises(
        ValueError,
        match=rf"^WORKFLOW_HETEROGENEOUS_MODEL_CONFLICT:{reviewer}:{producer}$",
    ):
        validate_settings(settings)


def test_repeated_idle_projection_preserves_the_existing_blink_deadline() -> None:
    app = (DESKTOP_ROOT / "app.py").read_text(encoding="utf-8")
    assert "preserve_existing=True" in app
    scheduler = app.split("def schedule_auto_blink", 1)[1].split(
        "def finish_motion", 1
    )[0]
    assert "auto_blink_timer.Enabled" in scheduler
    assert 'state["auto_blink_preserved_count"]' in scheduler
    repeated = app.split("elif not bool(overlay.get(\"animation_locked\")):", 1)[1].split(
        "holder.update", 1
    )[0]
    assert "preserve_existing=True" in repeated


def test_assistant_move_uses_the_native_ui_thread_when_a_form_is_attached() -> None:
    runtime = (DESKTOP_ROOT.parents[1] / "source" / "runtime.py").read_text(encoding="utf-8")
    mover = runtime.split("def _move_assistant_window_native", 1)[1].split(
        "def assistant_move_window", 1
    )[0]
    assert "native.Invoke(Action(apply_move))" in mover
    assert "native.Left = x" in mover
    assert "native.Top = y" in mover
    public_move = runtime.split("def assistant_move_window", 1)[1].split(
        "def assistant_shortcut", 1
    )[0]
    assert "self._move_assistant_window_native(" in public_move
    assert "native_width=visible_width" in public_move
    assert "native_height=visible_height" in public_move
    assert "self._assistant_window.move(" not in public_move


def test_transparency_probe_accounts_for_a_changing_underlay_without_accepting_a_box() -> None:
    app = (DESKTOP_ROOT / "app.py").read_text(encoding="utf-8")
    probe = app.split("def inspect_assistant_desktop_transparency", 1)[1].split(
        "def bring_product_window_to_front", 1
    )[0]
    assert "0.82 if corner_dynamic_underlay_pixels else 0.90" in probe
    assert "minimum_corner_exact_ratio = 0.75" in probe
    assert "corner_exact_ratio >= minimum_corner_exact_ratio" in probe
