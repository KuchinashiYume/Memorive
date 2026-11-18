"""The made-up data of sample mode, checked the way the app reads it.

Sample mode (app.py --desktop-test-mode, ProductApi(test_fixture_mode=True)) reads the folders described in
fixtures/README.md when it starts. Each test runs the format check of the controller that reads one folder,
so a format change that leaves the sample data behind fails here instead of at start. Nothing calls a model.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

DESKTOP = Path(__file__).resolve().parents[1]
for candidate in (DESKTOP / "source", DESKTOP / "product" / "desktop"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from memorive.desktop.messages.product_controller import _ApplicationServiceFirstResolver  # noqa: E402
from memorive.desktop.service.service_main import build_inbox_controller  # noqa: E402
from memorive.desktop.work_log.product_controller import WorkLogProductController  # noqa: E402
from memorive.documents.library.product_controller import LibraryProductController  # noqa: E402


def read(relative: str):
    return json.loads((DESKTOP / relative).read_text(encoding="utf-8"))


def test_the_library_rows_pass_the_catalog_check_and_point_to_their_files():
    catalog = read("fixtures_integration/synthetic_library_catalog.json")
    LibraryProductController._validate_catalog(catalog)
    files = DESKTOP / "fixtures_integration" / "files"
    for row in catalog["artifacts"]:
        assert (files / row["relative_path"]).is_file() is row["file_exists"], row["artifact_id"]


def test_the_work_log_entries_canaries_and_sentinels_pass_their_checks():
    WorkLogProductController._validate_stream(read("fixtures_logging/synthetic_work_log_stream.json"))
    WorkLogProductController._validate_canaries(read("fixtures_logging/synthetic_canary_corpus.json"))
    WorkLogProductController._validate_sentinels(read("fixtures_logging/source_authority_sentinels.json"))


def test_every_message_target_is_a_valid_locator():
    resolver = _ApplicationServiceFirstResolver(None, DESKTOP / "fixtures_retrieval" / "locator_catalog.json")
    assert sorted(target["kind"] for target in resolver.targets.values()) == ["action", "artifact", "attempt", "event", "job"]


def test_the_inbox_imports_its_six_sample_files(tmp_path):
    inbox = build_inbox_controller(tmp_path / "profile", include_sample_track=True)
    cards = {card["name"]: card for card in inbox.projection()["cards"]}
    assert sorted(cards) == ["basic.txt", "sample.pdf", "sample.pptx", "sample.xlsx", "unsupported.exe", "研究_合成样例.md"]
    assert cards["unsupported.exe"]["execution_state"] == "ERROR"
    assert all((DESKTOP / "fixtures" / "source" / name).is_file() for name in ("sample.docx", "sample.png"))
