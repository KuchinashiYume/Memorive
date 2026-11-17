"""The vector store restart check (memorive/documents/processing/chroma_durability.py) on real Chroma stores.

Chroma saves the HNSW index files only after about 1,000 records; until then a new process rebuilds the
index from Chroma's write log, so a small store without index_metadata.pickle is fine and must stay where
it is. Once Chroma has saved the index, a store missing that file cannot be queried and is moved aside.
"""
from __future__ import annotations

import random
import shutil

import pytest

chromadb = pytest.importorskip("chromadb")

from memorive.documents.processing import chroma_durability as durability  # noqa: E402

READY, BROKEN = "READY", durability.ERROR_CODE


def write_store(store, count):
    client = chromadb.PersistentClient(path=str(store))
    collection = client.get_or_create_collection("memorive_sandbox")
    rng = random.Random(count)
    collection.add(
        ids=[f"PAPER#c{i:04d}" for i in range(count)],
        embeddings=[[rng.random() for _ in range(8)] for _ in range(count)],
        metadatas=[{"paper_id": "PAPER"} for _ in range(count)],
    )
    client.close()


def copy_without(store, target, left_out):
    shutil.copytree(store, target, ignore=lambda folder, names: [n for n in names if n in left_out])
    return target


@pytest.fixture
def sandbox_store(tmp_path, monkeypatch):
    """Point the sandbox store at tmp_path/active and forget earlier checks."""
    active = tmp_path / "vector-store" / "active"
    monkeypatch.setenv("MEMORIVE_DOCUMENT_PROCESSING_SANDBOX_CHROMA_DIR", str(active))
    durability.forget_session_state_for_test()
    yield active
    durability.forget_session_state_for_test()


def test_a_small_store_without_index_files_stays(tmp_path, sandbox_store):
    write_store(tmp_path / "written", 48)
    store = copy_without(tmp_path / "written", sandbox_store, {"index_metadata.pickle"})
    assert durability.inspect_chroma_restart_durability(root=store)["status"] == READY

    result = durability.ensure_chroma_writable("sandbox")

    assert result["action"] == "UNCHANGED"
    assert (store / "chroma.sqlite3").is_file()
    assert not (store.parent / "chroma-quarantine").exists()


def test_a_saved_store_missing_its_pickle_is_moved_aside(tmp_path, sandbox_store):
    write_store(tmp_path / "written", 1100)
    assert durability.inspect_chroma_restart_durability(root=tmp_path / "written")["status"] == READY
    store = copy_without(tmp_path / "written", sandbox_store, {"index_metadata.pickle"})
    state = durability.inspect_chroma_restart_durability(root=store)
    assert (state["status"], state["missing_index_files"]) == (BROKEN, ["index_metadata.pickle"])

    result = durability.ensure_chroma_writable("sandbox")

    assert result["action"] == "QUARANTINED"
    assert list(store.iterdir()) == []
    moved = list((store.parent / "chroma-quarantine").iterdir())
    assert len(moved) == 1 and (moved[0] / "chroma.sqlite3").is_file()
