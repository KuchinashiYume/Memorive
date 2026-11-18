"""The made-up data of sample mode, checked against what its readers use.

Sample mode (app.py --desktop-test-mode, ProductApi(test_fixture_mode=True)) reads the folders described in
fixtures/README.md as they are; the app does not check data that ships with the repository. These tests do:
each row holds the fields its reader uses, ids are unique, locators match their ids, and the files the
library rows point to exist. Rows and fields may be added freely. Nothing calls a model.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

DESKTOP = Path(__file__).resolve().parents[1]
for candidate in (DESKTOP / "source", DESKTOP / "product" / "desktop"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from memorive.desktop.messages.product_controller import _ApplicationServiceFirstResolver  # noqa: E402
from memorive.desktop.service.service_main import build_inbox_controller  # noqa: E402
from memorive.desktop.work_log.product_controller import FORBIDDEN_EXPORT_KEYS  # noqa: E402

LIBRARY_FIELDS = {"artifact_id", "view_snapshot_id", "stable_locator", "display_name", "mode", "kind", "status",
                  "rights_status", "privacy_class", "lineage", "relative_path", "file_exists", "file_state",
                  "products", "external_target", "task", "updated_at", "size_bytes"}
WORK_LOG_FIELDS = {"age_bucket", "date", "event_sequence", "event_time_utc", "evidence_locators", "job_id", "job_locator",
                   "ledger_type", "log_entry_id", "milestones", "parse_status", "public_payload", "severity",
                   "source_locator", "state_axis", "summary", "title", "type_label"}


def read(relative: str):
    return json.loads((DESKTOP / relative).read_text(encoding="utf-8"))


def test_library_rows_hold_what_the_library_page_reads_and_point_to_their_files():
    rows = read("fixtures_integration/synthetic_library_catalog.json")["artifacts"]
    files = DESKTOP / "fixtures_integration" / "files"
    assert len({row["artifact_id"] for row in rows}) == len(rows)
    for row in rows:
        assert LIBRARY_FIELDS <= set(row), row["artifact_id"]
        assert row["stable_locator"] == f"memorive://artifact/{row['artifact_id']}"
        assert row["mode"] in {"literature", "memorive"} and row["privacy_class"] in {"PUBLIC_SAFE", "PRIVATE", "RESTRICTED"}
        assert (files / row["relative_path"]).is_file() is row["file_exists"], row["artifact_id"]


def test_work_log_entries_hold_what_the_work_log_page_reads():
    entries = read("fixtures_logging/synthetic_work_log_stream.json")["entries"]
    assert len({row["log_entry_id"] for row in entries}) == len(entries)
    assert len({row["event_sequence"] for row in entries}) == len(entries)
    for row in entries:
        assert WORK_LOG_FIELDS <= set(row), row["log_entry_id"]
        assert row["job_locator"] == f"memorive://job/{row['job_id']}"
        assert row["source_locator"].startswith("memorive://ledger/")
        assert all(value.startswith("memorive://evidence/") for value in row["evidence_locators"])
        assert row["ledger_type"] in {"branch", "central"} and row["parse_status"] in {"ready", "partial", "failed"}
        assert row["severity"] in {"INFO", "WARNING", "ERROR"} and row["age_bucket"] in {"today", "7", "30", "older"}


def test_the_export_canaries_cover_every_forbidden_key():
    # The export preview pairs canaries["fields"] with canaries["values"] by position and checks that none of
    # these values reaches an exported file.
    canaries = read("fixtures_logging/synthetic_canary_corpus.json")
    assert sorted(canaries["fields"]) == sorted(FORBIDDEN_EXPORT_KEYS)
    assert len(canaries["values"]) == len(canaries["fields"])
    for source in read("fixtures_logging/source_authority_sentinels.json")["sources"]:
        assert re.fullmatch(r"[A-F0-9]{64}", source["sha256"])


def test_every_message_target_is_a_valid_locator():
    resolver = _ApplicationServiceFirstResolver(None, DESKTOP / "fixtures_retrieval" / "locator_catalog.json")
    assert sorted(target["kind"] for target in resolver.targets.values()) == ["action", "artifact", "attempt", "event", "job"]


def test_the_inbox_imports_its_six_sample_files(tmp_path):
    inbox = build_inbox_controller(tmp_path / "profile", include_sample_track=True)
    cards = {card["name"]: card for card in inbox.projection()["cards"]}
    assert sorted(cards) == ["basic.txt", "sample.pdf", "sample.pptx", "sample.xlsx", "unsupported.exe", "研究_合成样例.md"]
    assert cards["unsupported.exe"]["execution_state"] == "ERROR"
    assert all((DESKTOP / "fixtures" / "source" / name).is_file() for name in ("sample.docx", "sample.png"))
