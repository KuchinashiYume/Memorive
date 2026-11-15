from __future__ import annotations

from pathlib import Path
import sys

import pytest


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
DESKTOP_ROOT = ATTEMPT_ROOT / "product" / "desktop"
for candidate in (SOURCE_ROOT, DESKTOP_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from memorive_desktop_service.control_store import ControlStore  # noqa: E402
from memorive_inbox import InboxController  # noqa: E402
from memorive_library import LibraryProductController, LibraryProductError  # noqa: E402
from tests.test_refinement_runtime_coordination import (  # noqa: E402
    _HealthyService,
)
from tests.test_lifecycle_surfaces import _refinement_draft  # noqa: E402


def _library(tmp_path: Path) -> LibraryProductController:
    return LibraryProductController(
        _HealthyService(),
        ControlStore(tmp_path / "ui"),
        ATTEMPT_ROOT / "contracts" / "t09",
        ATTEMPT_ROOT / "fixtures_t09",
        tmp_path / "library",
        include_synthetic_projection=False,
    )


def test_inbox_deduplication_never_targets_a_hidden_deduplicated_chain(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "paper.md"
    source.write_text("same bytes", encoding="utf-8")
    inbox = InboxController(
        source_roots=[source_root],
        data_root=tmp_path / "inbox",
        include_sample_projection=False,
    )

    first = inbox.import_paths([source], request_id="sample-first")["items"][0]["item"]
    duplicate = inbox.import_paths([source], request_id="sample-duplicate")["items"][0]["item"]
    assert first["state"] == "QUEUED"
    assert duplicate["state"] == "DEDUPLICATED"

    inbox.soft_delete([first["item_id"]], operation_id="ui-delete-sample-first")
    third = inbox.import_paths([source], request_id="sample-after-delete")["items"][0]["item"]

    assert third["state"] == "QUEUED"
    assert [row["item_id"] for row in inbox.projection()["cards"]] == [third["item_id"]]


def test_inbox_projection_drives_durable_undo_redo_buttons(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "note.txt"
    source.write_text("undo me", encoding="utf-8")
    inbox = InboxController(
        source_roots=[source_root],
        data_root=tmp_path / "inbox",
        include_sample_projection=False,
    )
    item = inbox.import_paths([source], request_id="sample-history")["items"][0]["item"]

    initial = inbox.projection()
    assert initial["history"] == {"undo_available": False, "redo_available": False}

    inbox.soft_delete([item["item_id"]], operation_id="ui-delete-sample-history")
    deleted = inbox.projection()
    assert deleted["cards"] == []
    assert deleted["history"] == {"undo_available": True, "redo_available": False}

    inbox.undo()
    restored = inbox.projection()
    assert len(restored["cards"]) == 1
    assert restored["history"] == {"undo_available": False, "redo_available": True}

    inbox.redo()
    redone = inbox.projection()
    assert redone["cards"] == []
    assert redone["history"] == {"undo_available": True, "redo_available": False}


