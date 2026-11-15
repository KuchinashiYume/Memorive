from __future__ import annotations

import inspect
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
for candidate in (
    ROOT / "source",
    ROOT / "product" / "desktop",
    ROOT.parents[1] / "build_runtime_deps",
):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import app  # noqa: E402


def inspection(card_count: int, turn_roles: list[str]) -> dict[str, object]:
    return {
        "route": "sessions",
        "card_count": card_count,
        "all_cards_match_provider": True,
        "source_marks_exact": True,
        "card_title_lengths": [12] * card_count,
        "card_time_nonempty_count": card_count,
        "detail_title_chars": 12,
        "detail_source_present": True,
        "detail_time_present": True,
        "turn_count": len(turn_roles),
        "user_turn_count": turn_roles.count("user"),
        "assistant_turn_count": turn_roles.count("assistant"),
        "turn_roles": turn_roles,
        "turn_text_lengths": [80] * len(turn_roles),
        "list_ratio": 0.4,
        "horizontal_overflow": False,
    }


def test_visual_checks_keep_old_two_turn_synthetic_fixture_compatible() -> None:
    checks = app.packaged_provider_visual_checks(
        inspection(2, ["user", "assistant"])
    )

    assert all(checks.values())


def test_visual_checks_accept_realistic_multi_conversation_multi_turn_shape() -> None:
    checks = app.packaged_provider_visual_checks(
        inspection(
            20,
            ["user", "assistant", "user", "assistant", "assistant"],
        )
    )

    assert all(checks.values())
    assert checks["provider_conversation_count_bounded"] is True
    assert checks["complete_user_and_assistant_body"] is True


def test_visual_checks_reject_empty_provider_or_incomplete_role_pair() -> None:
    empty_checks = app.packaged_provider_visual_checks(inspection(0, []))
    user_only_checks = app.packaged_provider_visual_checks(
        inspection(1, ["user", "user"])
    )

    assert empty_checks["provider_conversation_count_bounded"] is False
    assert empty_checks["complete_user_and_assistant_body"] is False
    assert user_only_checks["complete_user_and_assistant_body"] is False


def test_visual_receipt_projection_never_returns_title_or_message_text() -> None:
    source = inspect.getsource(app.capture_packaged_visual_qa)

    for forbidden in (
        "card_titles:",
        "card_times:",
        "turn_texts:",
        "detail_title:",
        "detail_source:",
        "detail_time:",
    ):
        assert forbidden not in source
    for safe_projection in (
        "card_title_lengths:",
        "card_time_nonempty_count:",
        "turn_roles:",
        "turn_text_lengths:",
        "detail_title_chars:",
    ):
        assert safe_projection in source
