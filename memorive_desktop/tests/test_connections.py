"""Connected apps (memorive_research_workspace/connections.py): bringing papers in through a preview,
their outputs, writing a managed block into a note, exporting, and actions queued for the desktop.
Everything runs in a temporary folder with plain markdown papers.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from memorive_research_workspace.service import ResearchWorkspace

PAPER = "# Growth of K12\n\n" + "\n".join(f"Line {n}: growth doubled at 37 C." for n in range(1, 30))


@pytest.fixture
def workspace(tmp_path):
    opened = ResearchWorkspace(tmp_path / "workspace", catalog=lambda: [])
    yield opened
    opened.close()


@pytest.fixture
def vault(tmp_path):
    folder = tmp_path / "vault"
    folder.mkdir()
    (folder / "k12.md").write_bytes(PAPER.encode("utf-8"))
    return folder


def bring_in(workspace, connection, *paths):
    preview = workspace.connections.preview(connection["id"], paths=list(paths))
    return preview, workspace.connections.commit(preview["id"], preview["preview_hash"])


def test_a_picked_file_is_previewed_then_brought_in(workspace, vault):
    connection = workspace.connections.save("default", "Notes", "obsidian", str(vault), writeback=True)
    preview, job = bring_in(workspace, connection, "k12.md")
    assert preview["counts"] == {"new": 1, "updated": 0, "unchanged": 0}
    assert job["status"] == "COMPLETE"
    [document] = workspace.store.list("connector_document", "default")
    assert document["state"] == "active" and len(document["artifact_ids"]) == 2  # the file and its bibliography note
    assert document["file_hash"] == hashlib.sha256(PAPER.encode("utf-8")).hexdigest()
    again = workspace.connections.preview(connection["id"], paths=["k12.md"])
    assert again["counts"]["unchanged"] == 1


def test_what_an_app_sends_is_checked(workspace, vault):
    connection = workspace.connections.save("default", "Library", "zotero", str(vault))
    with pytest.raises(ValueError, match="ZOTERO_KEY_INVALID"):
        workspace.connections.preview(connection["id"], items=[{"title": "K12", "item_key": "bad"}])
    with pytest.raises(ValueError, match="ANNOTATION_PAGE_INVALID"):
        workspace.connections.preview(connection["id"], items=[{"title": "K12", "annotations": [{"page": 0}]}])
    with pytest.raises(ValueError, match="CONNECTOR_ITEM_FIELDS_INVALID"):
        workspace.connections.preview(connection["id"], items=[{"title": "K12", "secret": 1}])
    preview = workspace.connections.preview(connection["id"], items=[{"title": "K12", "item_key": "ABCD1234",
                                                                     "annotations": [{"text": "note", "page": 2}]}])
    assert preview["items"][0]["warnings"] == ["METADATA_ONLY_ATTACH_PDF_TO_PROCESS"]


def test_a_file_changed_after_the_preview_is_not_brought_in(workspace, vault):
    connection = workspace.connections.save("default", "Notes", "folder", str(vault))
    preview = workspace.connections.preview(connection["id"], paths=["k12.md"])
    (vault / "k12.md").write_bytes(b"changed after the preview")
    job = workspace.connections.commit(preview["id"], preview["preview_hash"])
    assert job["status"] == "ERROR" and job["steps"][0]["status"] == "ERROR"
    assert workspace.store.list("connector_document", "default") == []
    # Nothing is written for it either, not even its bibliography note.
    assert not (workspace.store.root / "connector_sources").exists()


def test_a_paper_brought_in_before_is_not_reused_once_its_file_changed(workspace, vault):
    connection = workspace.connections.save("default", "Notes", "folder", str(vault))
    bring_in(workspace, connection, "k12.md")
    earlier = workspace.connections.preview(connection["id"], paths=["k12.md"])  # unchanged at this point
    (vault / "k12.md").write_bytes(b"changed after the preview")
    job = workspace.connections.commit(earlier["id"], earlier["preview_hash"])
    assert job["status"] == "ERROR" and job["steps"][0]["error"] == "PREVIEW_SOURCE_CHANGED"


def test_a_pdf_replaced_by_one_without_text_is_not_brought_in(workspace, tmp_path):
    fitz = pytest.importorskip("fitz")
    folder = tmp_path / "pdfs"
    folder.mkdir()
    document = fitz.open()
    document.new_page().insert_text((72, 72), "Growth of strain K12 doubled at 37 C.")
    document.save(str(folder / "k12.pdf"))
    connection = workspace.connections.save("default", "Papers", "folder", str(folder))
    preview = workspace.connections.preview(connection["id"], paths=["k12.pdf"])
    blank = fitz.open()
    blank.new_page()
    (folder / "k12.pdf").write_bytes(blank.tobytes())  # same name, no text layer
    job = workspace.connections.commit(preview["id"], preview["preview_hash"])
    assert job["status"] == "ERROR" and job["steps"][0]["error"] == "PREVIEW_SOURCE_CHANGED"


def test_outputs_and_a_changed_file(workspace, vault):
    connection = workspace.connections.save("default", "Notes", "folder", str(vault))
    bring_in(workspace, connection, "k12.md")
    [document] = workspace.store.list("connector_document", "default")
    outputs = workspace.connections.outputs(connection["id"], document["id"])
    assert outputs["items"][0]["id"] == "source-summary"
    export = workspace.connections.export(connection["id"], document["id"])
    assert export["sha256"] == hashlib.sha256(Path(export["path"]).read_bytes()).hexdigest()
    (vault / "k12.md").write_bytes(b"edited in the app")
    with pytest.raises(ValueError, match="DOCUMENT_SOURCE_CHANGED"):
        workspace.connections.outputs(connection["id"], document["id"])
    refreshed = workspace.connections.refresh(connection["id"])
    assert refreshed["changed"] == [document["id"]]
    assert workspace.store.get("connector_document", document["id"])["state"] == "changed"


def test_a_managed_block_is_written_once_and_a_user_edit_is_kept(workspace, vault):
    connection = workspace.connections.save("default", "Notes", "obsidian", str(vault), writeback=True)
    bring_in(workspace, connection, "k12.md")
    [document] = workspace.store.list("connector_document", "default")
    plan = workspace.connections.writeback_prepare(connection["id"], document["id"], "note-1", existing_text="My note")
    assert plan["status"] == "PREPARED" and plan["text"].startswith("My note\n\n<!-- memo:" + document["id"] + ":start -->")
    synced = workspace.connections.writeback_ack(connection["id"], plan["id"], plan["text_hash"])
    assert synced["status"] == "SYNCED" and "text" not in synced
    edited = plan["text"].replace("Bibliography and source", "my own words").replace("书目与来源", "my own words")
    conflict = workspace.connections.writeback_prepare(connection["id"], document["id"], "note-1", existing_text=edited)
    assert (conflict["status"], conflict["reason"]) == ("CONFLICT", "USER_EDITED_MANAGED_BLOCK")
    with pytest.raises(ValueError, match="WRITEBACK_RECEIPT_INVALID"):
        workspace.connections.writeback_ack(connection["id"], plan["id"], "0" * 64)


def test_actions_are_queued_once_and_dispatched_by_the_desktop(workspace, vault):
    connection = workspace.connections.save("default", "Notes", "folder", str(vault))
    bring_in(workspace, connection, "k12.md")
    [document] = workspace.store.list("connector_document", "default")
    action = workspace.connections.queue(connection["id"], "ask", source_id=document["id"], question="What doubled?",
                                         request_id="companion-0001")
    again = workspace.connections.queue(connection["id"], "ask", source_id=document["id"], question="What doubled?",
                                        request_id="companion-0001")
    assert again["id"] == action["id"] and action["status"] == "QUEUED"
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        workspace.connections.queue(connection["id"], "ask", source_id=document["id"], question="Other?", request_id="companion-0001")
    with pytest.raises(ValueError, match="ATTACHMENT_REQUIRED"):
        workspace.connections.queue(connection["id"], "process")
    workspace.connections.pump()
    dispatched = workspace.store.get("connector_action", action["id"])
    assert dispatched["status"] == "DISPATCHED"
    for future in list(workspace.futures.values()):
        future.result(timeout=60)
    assert workspace.job_status(dispatched["result"]["job_id"])["status"] == "COMPLETE"


def test_the_companion_reaches_only_its_own_connection(workspace, vault, tmp_path):
    mine = workspace.connections.save("default", "Notes", "folder", str(vault))
    other_folder = tmp_path / "other"
    other_folder.mkdir()
    (other_folder / "x.md").write_bytes(b"other")
    other = workspace.connections.save("default", "Other", "folder", str(other_folder))
    preview = workspace.connections.preview(other["id"], paths=["x.md"])
    with pytest.raises(ValueError, match="PREVIEW_SCOPE_MISMATCH"):
        workspace.connections.companion("connector.commit", {"connection_id": mine["id"], "preview_id": preview["id"],
                                                             "preview_hash": preview["preview_hash"]})
    with pytest.raises(ValueError, match="CONNECTOR_METHOD_NOT_ALLOWED"):
        workspace.connections.companion("memo.state", {})
