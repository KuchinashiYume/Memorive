"""Checks around writing one paper's chunk vectors (document_embed_embed).

Before writing: every chunk needs an id, a paper id and text, ids are unique, the collection holds
no vectors from another embedding model or vector type, and none of the ids already exist. After
writing: every id reads back with this run's id, model and vector type; otherwise the run's rows
are deleted again. The collection here is a small in-memory stand-in with the calls these
checks make.
"""
from __future__ import annotations

import pytest

from memorive.documents.processing import document_embed_embed as embed
from memorive.documents.processing.errors import (DocumentProcessingError, VectorDuplicateError, VectorModelConflictError,
                                        VectorWriteVerifyError)

MODEL, VTYPE = embed.CANON_EMBED_MODEL, embed.VECTOR_TYPE


class _Collection:
    def __init__(self, rows=None, *, short_read=False, fail_add=False):
        self.rows = dict(rows or {})
        self.short_read = short_read
        self.fail_add = fail_add
        self.deleted = []

    def count(self):
        return len(self.rows)

    def get(self, ids=None, include=None):
        keys = [k for k in (ids if ids is not None else self.rows) if k in self.rows]
        if self.short_read:
            keys = keys[:-1]
        return {"ids": keys, "metadatas": [self.rows[k] for k in keys]}

    def add(self, ids, embeddings, metadatas, documents):
        if self.fail_add:
            raise RuntimeError("disk full")
        self.rows.update(zip(ids, metadatas))

    def delete(self, where):
        run = where["embedding_run_id"]
        self.deleted.append(run)
        self.rows = {k: v for k, v in self.rows.items() if v.get("embedding_run_id") != run}


def _meta(run="run-1", **changes):
    value = {"embedding_run_id": run, "embedding_model": MODEL, "vector_type": VTYPE}
    value.update(changes)
    return value


# Records

def _records(*changes):
    rows = [{"chunk_id": f"P1-c{n}", "paper_id": "P1", "text": f"text {n}"} for n in range(3)]
    for index, change in changes:
        rows[index].update(change)
    return rows


def test_complete_records_with_unique_ids_pass():
    rows = _records()
    assert embed._validate_records(rows, [r["chunk_id"] for r in rows], "P1", "chunks.jsonl") is None


@pytest.mark.parametrize("change", [
    {"chunk_id": None}, {"chunk_id": " "}, {"chunk_id": 7},
    {"paper_id": None}, {"paper_id": ""},
    {"text": None}, {"text": "  "}, {"text": ["text"]},
])
def test_a_record_missing_its_id_paper_or_text_is_refused(change):
    rows = _records((1, change))
    with pytest.raises(DocumentProcessingError):
        embed._validate_records(rows, [r.get("chunk_id") for r in rows], "P1", "chunks.jsonl")


def test_a_repeated_chunk_id_is_refused():
    rows = _records((2, {"chunk_id": "P1-c0"}))
    with pytest.raises(VectorDuplicateError):
        embed._validate_records(rows, [r["chunk_id"] for r in rows], "P1", "chunks.jsonl")


# Before writing

@pytest.mark.parametrize("rows", [{}, {"other-c1": _meta("old")}, {"a": _meta("old"), "b": _meta("older")}])
def test_a_collection_of_the_same_model_and_type_may_take_more(rows):
    assert embed._check_rebuild_conflict(_Collection(rows), "chunks", "P1") is None


@pytest.mark.parametrize("rows", [
    {"a": _meta(embedding_model="other-model")},
    {"a": _meta(), "b": _meta(embedding_model=None)},
    {"a": {"embedding_run_id": "old", "vector_type": VTYPE}},
    {"a": None},
    {"a": _meta(vector_type="card_field")},
    {"a": _meta(), "b": {"embedding_run_id": "old", "embedding_model": MODEL}},
])
def test_a_collection_holding_another_model_or_vector_type_is_refused(rows):
    with pytest.raises(VectorModelConflictError):
        embed._check_rebuild_conflict(_Collection(rows), "chunks", "P1")


def test_a_collection_that_cannot_be_read_in_full_is_refused():
    with pytest.raises(VectorModelConflictError):
        embed._check_rebuild_conflict(_Collection({"a": _meta(), "b": _meta()}, short_read=True), "chunks", "P1")


def test_ids_already_in_the_collection_are_not_written_again():
    col = _Collection({"P1-c1": _meta("old")})
    with pytest.raises(VectorDuplicateError):
        embed._check_duplicates(col, ["P1-c0", "P1-c1"], "P1")
    assert embed._check_duplicates(col, ["P1-c0", "P1-c2"], "P1") is None


# After writing

IDS = ["P1-c0", "P1-c1"]


def test_a_complete_write_is_verified():
    col = _Collection({i: _meta() for i in IDS})
    assert embed._verify_written(col, IDS, "run-1", "P1") is None


@pytest.mark.parametrize("rows", [
    {"P1-c0": _meta()},
    {"P1-c0": _meta(), "P1-c1": _meta("run-2")},
    {"P1-c0": _meta(), "P1-c1": _meta(embedding_model="other-model")},
    {"P1-c0": _meta(), "P1-c1": _meta(vector_type="card_field")},
    {"P1-c0": _meta(), "P1-c1": {}},
])
def test_a_write_that_does_not_read_back_as_this_run_is_refused(rows):
    with pytest.raises(VectorWriteVerifyError):
        embed._verify_written(_Collection(rows), IDS, "run-1", "P1")


def _write(col, metadatas=None):
    embed._write_atomic(col, IDS, [[0.1], [0.2]], metadatas or [_meta(), _meta()], ["a", "b"], "run-1", "P1")


def test_a_verified_write_is_kept():
    col = _Collection({"other": _meta("old")})
    _write(col)
    assert set(col.rows) == {"other", *IDS} and col.deleted == []


@pytest.mark.parametrize("col, metadatas", [
    (_Collection({"other": _meta("old")}), [_meta(), _meta(vector_type="card_field")]),
    (_Collection({"other": _meta("old")}, fail_add=True), None),
])
def test_a_failed_write_removes_this_runs_rows_only(col, metadatas):
    with pytest.raises(VectorWriteVerifyError):
        _write(col, metadatas)
    assert col.deleted == ["run-1"] and set(col.rows) == {"other"}
