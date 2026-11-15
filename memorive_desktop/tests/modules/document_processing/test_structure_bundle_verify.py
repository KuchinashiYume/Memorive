"""verify_bundle: the integrity check behind `document_conversion verify-structure`.

A structure bundle is the receipt, the preserved source, the RawMD and the structure file, bound to
each other by hashes. The test writes a real native-PDF bundle for a one-page PDF and checks that any
change to its files or bindings is reported with its code.
"""
from __future__ import annotations

import hashlib
import json

import pymupdf
import pytest

from document_processing.document_conversion.models import DocumentConversionError
from document_processing.document_structure.service import verify_bundle, write_structure


@pytest.fixture
def bundle(tmp_path):
    pdf = tmp_path / "paper.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Samples were incubated at 37 C for 20 minutes.")
    page.insert_text((72, 100), "Results improved by twelve points.")
    document.save(pdf)
    rawmd = tmp_path / "[RawMD] paper.md"
    rawmd.write_text("Samples were incubated at 37 C for 20 minutes.\n", encoding="utf-8")
    written = write_structure(source=pdf, source_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest().upper(),
                              rawmd=rawmd, paper_id="P1", conversion_receipt_id="conv-1", mode="native_pdf")
    return written["receipt_path"]


def _edit_receipt(path, change):
    value = json.loads(path.read_text(encoding="utf-8"))
    change(value)
    path.write_text(json.dumps(value), encoding="utf-8")


def _code(path):
    with pytest.raises(DocumentConversionError) as caught:
        verify_bundle(path)
    return caught.value.code


def test_an_untouched_bundle_verifies(bundle):
    result = verify_bundle(bundle)
    assert result["status"] == "INTEGRITY_VERIFIED"
    assert (result["page_count"], result["block_count"]) == (1, 2)
    assert result["review_status"] == "CANDIDATE_NOT_ADMITTED"


@pytest.mark.parametrize("name", ["paper.pdf", "[RawMD] paper.md", "[DocumentStructure] P1.json"])
def test_a_changed_artifact_is_reported(bundle, name):
    target = bundle.parent / name
    target.write_bytes(target.read_bytes() + b" ")
    assert _code(bundle) == "STRUCTURE_ARTIFACT_CHANGED"


def test_a_missing_artifact_is_reported(bundle):
    (bundle.parent / "[RawMD] paper.md").unlink()
    assert _code(bundle) == "STRUCTURE_ARTIFACT_MISSING"


@pytest.mark.parametrize("change, code", [
    (lambda r: r.update(schema_version="other"), "STRUCTURE_SCHEMA_UNSUPPORTED"),
    (lambda r: r.update(mode="ocr"), "STRUCTURE_MODE_INVALID"),
    (lambda r: r["files"].pop("rawmd"), "STRUCTURE_RECEIPT_INVALID"),
    (lambda r: r.update(mode="marker_chunks"), "STRUCTURE_RECEIPT_INVALID"),
    (lambda r: r["files"]["rawmd"].update(name="../paper.md"), "STRUCTURE_ARTIFACT_PATH_INVALID"),
    (lambda r: r["files"]["rawmd"].update(name="paper.md."), "STRUCTURE_ARTIFACT_PATH_INVALID"),
    (lambda r: r["files"].update(rawmd=dict(r["files"]["source"])), "STRUCTURE_RECEIPT_INVALID"),
    (lambda r: r.update(structure_id="Structure-" + "0" * 32), "STRUCTURE_RECEIPT_MISMATCH"),
    (lambda r: r.update(config_sha256="0" * 64), "STRUCTURE_RECEIPT_MISMATCH"),
])
def test_a_receipt_whose_bindings_do_not_hold_is_reported(bundle, change, code):
    _edit_receipt(bundle, change)
    assert _code(bundle) == code
