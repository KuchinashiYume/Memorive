from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "source", ROOT / "product" / "desktop"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from tests.test_browser_capture_successor_repair import (  # noqa: E402
    _controller,
    _conversation,
    _library,
)
from runtime import ProductApi  # noqa: E402
from memorive_session_archive import (  # noqa: E402
    ArchiveInputError,
    BrowserLibraryImporter,
    SessionArchive,
)


def test_terminal_sync_report_accepts_runner_timeout_metadata(tmp_path: Path) -> None:
    report = {
        "schema_version": "MEMORIVE_BROWSER_SESSION_SYNC_REPORT_V2",
        "source": "MEMORIVE_BROWSER_COMPANION_RUNNER_V4_DRAFT",
        "extension_id": "jfgfakepddanncmccgbckjpllhaklghi",
        "extension_version": "3.5.0",
        "per_provider_limit": 2,
        "sync_mode": "PREFLIGHT",
        "browser_family": "EDGE",
        "generated_at": "2026-08-29T07:39:28.932Z",
        "trigger": "APPROVED_PROVIDER_FRAGMENT_PREFLIGHT_V1",
        "status": "COMPLETE",
        "error_code": "",
        "hard_timeout_ms": 1_200_000,
        "active_provider_count": 3,
        "provider_parallelism": True,
        "maximum_capture_windows": 1,
        "per_provider_capture_mode": "DEDICATED_MINIMIZED_WINDOW_OWNED_TABS",
        "window_mode": "DEDICATED_MINIMIZED",
        "user_tab_reused": False,
        "providers": [
            {
                "provider_id": provider,
                "status": "COMPLETE",
                "indexed_count": 2,
                "candidate_count": 2,
                "capture_target": 2,
                "body_read_count": 2,
                "captured_count": 2,
                "unchanged_count": 0,
                "failed_count": 0,
                "skipped_candidate_count": 0,
                "error_code": "",
                "skipped_error_codes": [],
                "safe_diagnostic": None,
            }
            for provider in ("deepseek", "gemini", "kimi")
        ],
        "cookie_value_reads": 0,
        "browser_storage_value_reads": 0,
        "hidden_provider_api_calls": 0,
        "remote_session_mutations": 0,
    }
    report_path = tmp_path / "memorive-session-library-sync-report-real-3-5-0.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    api = ProductApi.__new__(ProductApi)
    api._browser_session_download_roots = lambda: [tmp_path]

    loaded = api._latest_browser_companion_sync_report(  # noqa: SLF001
        expected_sync_mode="PREFLIGHT"
    )

    assert loaded is not None
    assert loaded["extension_version"] == "3.5.0"
    assert loaded["status"] == "COMPLETE"
    assert all(row["captured_count"] == 2 for row in loaded["providers"].values())


def test_verified_zero_change_incremental_bundle_is_an_accepted_noop(
    tmp_path: Path,
) -> None:
    payload = {
        "schema_version": "MEMORIVE_BROWSER_SESSION_LIBRARY_V4_DRAFT",
        "source": "MEMORIVE_BROWSER_COMPANION_VISIBLE_UI_V4_DRAFT",
        "generated_at": "2026-08-29T07:47:13.791Z",
        "sync_mode": "INCREMENTAL",
        "sync_scope": "RECENT_14_DAYS_OR_30_CONVERSATIONS",
        "provider_id": "kimi",
        "enumerated_count": 0,
        "candidate_count": 15,
        "capture_target": 2,
        "body_read_count": 2,
        "captured_count": 0,
        "unchanged_count": 2,
        "failed_count": 0,
        "skipped_candidate_count": 0,
        "conversations": [],
        "failures": [],
        "skipped_candidates": [],
    }
    raw = json.dumps(payload).encode("utf-8")
    batch = BrowserLibraryImporter.parse_payload(payload, raw_bytes=raw)
    archive = SessionArchive(
        tmp_path / "archive.sqlite3",
        tmp_path / "snapshots",
    )
    try:
        receipt = archive.ingest(batch)
    finally:
        archive.close()

    assert batch.expected_count == 0
    assert receipt["verification_status"] == "VERIFIED_COMPLETE"
    assert receipt["captured_count"] == 0
    assert receipt["failed_count"] == 0

    payload["sync_mode"] = "PREFLIGHT"
    with pytest.raises(ArchiveInputError, match="BROWSER_LIBRARY_EMPTY"):
        BrowserLibraryImporter.parse_payload(payload, raw_bytes=raw)


def test_incremental_append_never_truncates_a_verified_existing_turn(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    full_answer = (
        "这是已经通过边界证明的完整旧回答。"
        "后半段包含用户仍需保留的配置说明、限制条件和最终结论。"
    )
    controller.import_browser_bundle(
        _library(
            _conversation(
                capture_method="BROWSER_COMPANION_BACKGROUND_TAB_V4",
                messages=[
                    {"role": "user", "content": "第一轮问题"},
                    {"role": "assistant", "content": full_answer},
                ],
            )
        )
    )

    receipt = controller.import_browser_bundle(
        _library(
            _conversation(
                capture_method="BROWSER_COMPANION_BACKGROUND_TAB_V4",
                updated_at="2026-08-28T08:10:00Z",
                messages=[
                    {"role": "user", "content": "第一轮问题"},
                    {"role": "assistant", "content": "这是已经通过边界证明的完整旧回答。"},
                    {"role": "user", "content": "第二轮追问"},
                    {"role": "assistant", "content": "这是第二轮新增回答。"},
                ],
            )
        )
    )

    row = controller.call("sessions.list", {"source": "kimi"})["rows"][0]
    detail = controller.call(
        "sessions.get", {"local_projection_id": row["local_projection_id"]}
    )
    revision = controller._session_archive._connection.execute(  # noqa: SLF001
        """
        SELECT disposition FROM revisions
        WHERE provider = 'kimi'
          AND provider_conversation_id = 'kimi-successor-repair-001'
        ORDER BY revision_id DESC
        LIMIT 1
        """
    ).fetchone()

    assert receipt["imported_count"] == 1
    assert [message["role"] for message in detail["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert detail["messages"][1]["content"] == full_answer
    assert detail["messages"][2]["content"] == "第二轮追问"
    assert detail["messages"][3]["content"] == "这是第二轮新增回答。"
    assert revision["disposition"] == "APPLIED_VERIFIED_APPEND_PREFIX_MERGE"


def test_equal_count_cannot_mask_one_truncated_turn_with_another_longer_turn(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    controller.import_browser_bundle(
        _library(
            _conversation(
                capture_method="BROWSER_COMPANION_BACKGROUND_TAB_V4",
                messages=[
                    {"role": "user", "content": "第一轮问题"},
                    {
                        "role": "assistant",
                        "content": "第一轮完整回答，尾段包含必须保留的限制条件。",
                    },
                    {"role": "user", "content": "第二轮问题"},
                    {"role": "assistant", "content": "第二轮短回答"},
                ],
            )
        )
    )
    controller.import_browser_bundle(
        _library(
            _conversation(
                capture_method="BROWSER_COMPANION_BACKGROUND_TAB_V4",
                updated_at="2026-08-28T08:10:00Z",
                messages=[
                    {"role": "user", "content": "第一轮问题"},
                    {"role": "assistant", "content": "第一轮完整回答"},
                    {"role": "user", "content": "第二轮问题"},
                    {
                        "role": "assistant",
                        "content": "第二轮短回答，随后加载出同样需要保留的新尾段。",
                    },
                ],
            )
        )
    )

    row = controller.call("sessions.list", {"source": "kimi"})["rows"][0]
    detail = controller.call(
        "sessions.get", {"local_projection_id": row["local_projection_id"]}
    )

    assert detail["messages"][1]["content"] == (
        "第一轮完整回答，尾段包含必须保留的限制条件。"
    )
    assert detail["messages"][3]["content"] == (
        "第二轮短回答，随后加载出同样需要保留的新尾段。"
    )


def test_regenerated_branch_is_not_spliced_with_previous_answer(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    controller.import_browser_bundle(
        _library(
            _conversation(
                capture_method="BROWSER_COMPANION_BACKGROUND_TAB_V4",
                messages=[
                    {"role": "user", "content": "问题"},
                    {"role": "assistant", "content": "旧分支回答，结论为方案甲。"},
                ],
            )
        )
    )
    controller.import_browser_bundle(
        _library(
            _conversation(
                capture_method="BROWSER_COMPANION_BACKGROUND_TAB_V4",
                updated_at="2026-08-28T08:10:00Z",
                messages=[
                    {"role": "user", "content": "问题"},
                    {"role": "assistant", "content": "重新生成后的回答，结论为方案乙。"},
                    {"role": "user", "content": "继续"},
                    {"role": "assistant", "content": "方案乙的补充。"},
                ],
            )
        )
    )

    row = controller.call("sessions.list", {"source": "kimi"})["rows"][0]
    detail = controller.call(
        "sessions.get", {"local_projection_id": row["local_projection_id"]}
    )

    assert detail["messages"][1]["content"] == "重新生成后的回答，结论为方案乙。"
