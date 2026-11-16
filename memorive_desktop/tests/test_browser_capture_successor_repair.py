from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "source", ROOT / "product" / "desktop"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from memorive.sessions.controller import SessionsProductController  # noqa: E402


class _HealthyService:
    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        if method == "service.health":
            return {
                "schema_version": "ApplicationServiceHealth-v1",
                "protocol_version": "1.0",
                "adapter_kind": "sample-test-service",
                "request_count": 0,
                "auth_rejection_count": 0,
                "connection_count": 1,
                "uptime_seconds": 1,
                "external_network_calls": 0,
                "external_model_calls": 0,
                "credential_value_reads": 0,
            }
        if method == "settings.get_state":
            return {"api_profiles": []}
        raise AssertionError(method)


def _controller(tmp_path: Path) -> SessionsProductController:
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    codex_home.mkdir()
    claude_home.mkdir()
    return SessionsProductController(
        _HealthyService(),
        object(),
        ROOT / "contracts" / "analysis",
        ROOT / "fixtures_t08",
        tmp_path / "state",
        source_roots={"codex_home": codex_home, "claude_home": claude_home},
        legacy_deepseek_visible_ui_projection=False,
    )


def _conversation(
    *,
    capture_method: str,
    messages: list[dict[str, str]],
    updated_at: str = "2026-08-28T08:00:00Z",
) -> dict[str, object]:
    return {
        "provider_id": "kimi",
        "provider_session_id": "kimi-successor-repair-001",
        "title": "Kimi k3 本地部署",
        "created_at": "2026-08-28T07:30:00Z",
        "updated_at": updated_at,
        "recency_bucket": "TODAY",
        "source_url": "https://www.kimi.com/chat/kimi-successor-repair-001",
        "capture_method": capture_method,
        "capture_status": "COMPLETE",
        "source_truncated": False,
        "top_boundary_reached": True,
        "bottom_boundary_reached": True,
        "messages": [
            {
                "message_id": f"message-{index}",
                "role": message["role"],
                "content_kind": "text",
                "content": message["content"],
                "occurred_at": "UNKNOWN",
            }
            for index, message in enumerate(messages)
        ],
    }


def _library(conversation: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "MEMORIVE_BROWSER_SESSION_LIBRARY_V4_DRAFT",
        "source": "MEMORIVE_BROWSER_COMPANION_VISIBLE_UI_V4_DRAFT",
        "generated_at": "2026-08-28T08:30:00Z",
        "sync_scope": "RECENT_30_DAYS",
        "provider_id": "kimi",
        "enumerated_count": 1,
        "captured_count": 1,
        "failed_count": 0,
        "conversations": [conversation],
        "failures": [],
    }


@pytest.mark.parametrize(
    "deployed_source_kind",
    [
        None,
        "BROWSER_COMPANION_VISIBLE_UI_LIBRARY_V4_DRAFT",
    ],
)
def test_structured_dom_v4_replaces_legacy_corrupt_body_even_when_shorter(
    tmp_path: Path,
    deployed_source_kind: str | None,
) -> None:
    controller = _controller(tmp_path)
    legacy = _conversation(
        capture_method="BROWSER_COMPANION_BACKGROUND_TAB_V3",
        messages=[
            {"role": "user", "content": "Kimi k3 实现本地部署需要什么设备？"},
            {"role": "assistant", "content": "https://api.moonshot.ai/v1"},
            {"role": "assistant", "content": "kimi-k3"},
            {"role": "assistant", "content": "引用"},
            {"role": "user", "content": "使用 8 台 Mac Studio 组网是否可行？"},
            {"role": "assistant", "content": "mlx.launch"},
        ],
    )
    controller.import_browser_bundle(_library(legacy))
    if deployed_source_kind is not None:
        controller._session_archive._connection.execute(  # noqa: SLF001
            "UPDATE conversations SET source_kind = ? WHERE provider = 'kimi'",
            (deployed_source_kind,),
        )
        controller._session_archive._connection.commit()  # noqa: SLF001

    repaired = _conversation(
        capture_method="BROWSER_COMPANION_BACKGROUND_TAB_V4",
        updated_at="2026-08-28T08:05:00Z",
        messages=[
            {"role": "user", "content": "Kimi k3 实现本地部署需要什么设备？"},
            {
                "role": "assistant",
                "content": "Kimi K3 的本地部署需要根据模型规模、量化方式和并发目标选择硬件。",
            },
        ],
    )
    receipt = controller.import_browser_bundle(_library(repaired))
    row = controller.call("sessions.list", {"source": "kimi"})["rows"][0]
    detail = controller.call(
        "sessions.get", {"local_projection_id": row["local_projection_id"]}
    )
    archived = controller._session_archive.get_session(  # noqa: SLF001
        "kimi", "kimi-successor-repair-001"
    )

    assert receipt["imported_count"] == 1
    assert receipt["retained_previous_complete_count"] == 0
    assert [message["content"] for message in detail["messages"]] == [
        "Kimi k3 实现本地部署需要什么设备？",
        "Kimi K3 的本地部署需要根据模型规模、量化方式和并发目标选择硬件。",
    ]
    assert (
        archived["source_kind"]
        == "BROWSER_COMPANION_VISIBLE_UI_LIBRARY_V4_STRUCTURED_DOM"
    )


def test_structured_dom_v4_still_retains_a_longer_structured_dom_body(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    complete = _conversation(
        capture_method="BROWSER_COMPANION_BACKGROUND_TAB_V4",
        messages=[
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "完整回答包含必须保留的后半段"},
        ],
    )
    controller.import_browser_bundle(_library(complete))
    shortened = _conversation(
        capture_method="BROWSER_COMPANION_BACKGROUND_TAB_V4",
        updated_at="2026-08-28T08:05:00Z",
        messages=[
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "完整回答"},
        ],
    )

    receipt = controller.import_browser_bundle(_library(shortened))
    row = controller.call("sessions.list", {"source": "kimi"})["rows"][0]
    detail = controller.call(
        "sessions.get", {"local_projection_id": row["local_projection_id"]}
    )

    assert receipt["retained_previous_complete_count"] == 1
    assert detail["messages"][1]["content"] == "完整回答包含必须保留的后半段"


def test_archive_normalizes_provider_bullet_and_punctuation_fragments(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    fragmented = _conversation(
        capture_method="BROWSER_COMPANION_BACKGROUND_TAB_V4",
        messages=[
            {"role": "user", "content": "请给出清单"},
            {
                "role": "assistant",
                "content": "结论\n•\n第一项\n•\n第二项\n。\n补充说明",
            },
        ],
    )

    controller.import_browser_bundle(_library(fragmented))
    row = controller.call("sessions.list", {"source": "kimi"})["rows"][0]
    detail = controller.call(
        "sessions.get", {"local_projection_id": row["local_projection_id"]}
    )

    assert detail["messages"][1]["content"] == (
        "结论\n• 第一项\n• 第二项。\n补充说明"
    )
