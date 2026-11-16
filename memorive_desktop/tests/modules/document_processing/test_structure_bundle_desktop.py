"""Desktop structure bundles (document_structure.bundle): built once, then checked on every read.

A bundle folder holds a copy of the source, the RawMD, the structure file and bundle.json, which
binds them by hash. load / preview / context_pack go through the same check, so any changed,
missing, redirected or re-labelled member stops the read. The bundles here are built from a
one-page PDF in native mode, which needs no external recognition program.
"""
from __future__ import annotations

import json
import os

import pymupdf
import pytest

from memorive.documents.processing.document_conversion.models import DocumentConversionError
from memorive.documents.processing.document_structure.bundle import build_bundle, load, normalize_config, preview, verify
from memorive.documents.processing.document_structure.contract import digest, file_sha, structure_id, validate_structure

NATIVE = {"mode": "native"}


@pytest.fixture
def inputs(tmp_path):
    pdf = tmp_path / "paper.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Samples were incubated at 37 C for 20 minutes.")
    page.insert_text((72, 100), "Results improved by twelve points.")
    document.save(pdf)
    rawmd = tmp_path / "paper.md"
    rawmd.write_text("Samples were incubated at 37 C for 20 minutes.\n", encoding="utf-8")
    return pdf, rawmd


@pytest.fixture
def bundle(inputs, tmp_path):
    folder = tmp_path / "bundle"
    build_bundle(*inputs, folder, NATIVE)
    return folder


def _manifest(folder):
    return json.loads((folder / "bundle.json").read_text(encoding="utf-8"))


def _edit_manifest(folder, change):
    value = _manifest(folder)
    change(value)
    (folder / "bundle.json").write_text(json.dumps(value), encoding="utf-8")


def _error(folder, call=verify):
    with pytest.raises((ValueError, DocumentConversionError)) as caught:
        call(folder)
    return getattr(caught.value, "code", None) or str(caught.value)


def test_a_built_bundle_reads_back(bundle, inputs):
    receipt = verify(bundle)
    assert (receipt["status"], receipt["external_cloud_calls"], receipt["execution"]) == ("CANDIDATE_NOT_ADMITTED", 0, None)
    assert receipt["binding"]["source_sha256"] == file_sha(inputs[0])
    receipt, value = load(bundle)
    first = value["blocks"][0]
    assert preview(bundle, first["block_id"])["text"] == first["text"]
    assert _error(bundle, lambda folder: preview(folder, "missing")) == "STRUCTURE_BLOCK_NOT_FOUND"


def test_building_again_returns_the_same_bundle_unless_the_binding_changed(bundle, inputs):
    assert build_bundle(*inputs, bundle, NATIVE) == verify(bundle)
    with pytest.raises(ValueError, match="STRUCTURE_DESTINATION_CONFLICT"):
        build_bundle(*inputs, bundle, {"mode": "native", "timeout_seconds": 60})


def test_an_unsupported_source_leaves_no_bundle(inputs, tmp_path):
    source = tmp_path / "paper.txt"
    source.write_text("text", encoding="utf-8")
    with pytest.raises(ValueError, match="STRUCTURE_FORMAT_UNSUPPORTED"):
        build_bundle(source, inputs[1], tmp_path / "out", NATIVE)
    assert not (tmp_path / "out").exists()
    failed = [p for p in tmp_path.iterdir() if p.name.endswith(".failed")]
    assert len(failed) == 1 and (failed[0] / "FAILED.json").is_file()


@pytest.mark.parametrize("config, code", [
    ([], "STRUCTURE_CONFIG_INVALID"),
    ({"mode": "native", "endpoint": "x"}, "STRUCTURE_CONFIG_UNKNOWN_FIELDS"),
    ({"mode": "cloud"}, "STRUCTURE_CONFIG_INVALID"),
    ({"mode": "native", "profile": "best"}, "STRUCTURE_CONFIG_INVALID"),
    ({"mode": "native", "timeout_seconds": 0}, "STRUCTURE_TIMEOUT_INVALID"),
    ({"mode": "native", "timeout_seconds": 3601}, "STRUCTURE_TIMEOUT_INVALID"),
    ({"mode": "native", "timeout_seconds": True}, "STRUCTURE_TIMEOUT_INVALID"),
    ({"mode": "native", "runtime_path": None}, "STRUCTURE_RUNTIME_PATH_INVALID"),
])
def test_a_structure_setting_out_of_range_is_refused(config, code):
    with pytest.raises(ValueError) as caught:
        normalize_config(config)
    assert str(caught.value) == code


def test_structure_recognition_must_be_switched_on(inputs, tmp_path):
    with pytest.raises(ValueError, match="STRUCTURE_DISABLED"):
        build_bundle(*inputs, tmp_path / "out", {"mode": "off"})


@pytest.mark.parametrize("name", ["Original.pdf", "RawMD.md", "structure.json"])
def test_a_changed_member_stops_every_read(bundle, name):
    path = bundle / name
    path.write_bytes(path.read_bytes() + b" ")
    for call in (verify, load):
        assert _error(bundle, call).startswith(("STRUCTURE_BUNDLE_STALE:MEMBER_HASH", "STRUCTURE_INPUT"))


def test_a_missing_member_stops_the_read(bundle):
    (bundle / "RawMD.md").unlink()
    assert _error(bundle) == "STRUCTURE_BUNDLE_STALE:MISSING_MEMBER"


@pytest.mark.parametrize("change, code", [
    (lambda r: r.update(status="ADMITTED"), "STRUCTURE_BUNDLE_SCHEMA_INVALID"),
    (lambda r: r.update(external_cloud_calls=1), "STRUCTURE_BUNDLE_SCHEMA_INVALID"),
    (lambda r: r.update(external_cloud_calls=False), "STRUCTURE_BUNDLE_SCHEMA_INVALID"),
    (lambda r: r.update(schema_version="other"), "STRUCTURE_BUNDLE_SCHEMA_INVALID"),
    (lambda r: r.update(structure_id="Structure-x"), "STRUCTURE_BUNDLE_SCHEMA_INVALID"),
    (lambda r: r.pop("quality_policy"), "STRUCTURE_BUNDLE_SCHEMA_INVALID"),
    (lambda r: r["quality_policy"].update(automatic_fact_admission=True), "STRUCTURE_BUNDLE_SCHEMA_INVALID"),
    (lambda r: r["quality_policy"].update(review_required=False), "STRUCTURE_BUNDLE_SCHEMA_INVALID"),
    (lambda r: r["quality_policy"].update(recognition_accuracy="HIGH"), "STRUCTURE_BUNDLE_SCHEMA_INVALID"),
    (lambda r: r.update(files={}), "STRUCTURE_BUNDLE_SCHEMA_INVALID"),
    (lambda r: r["binding"]["config"].update(mode="off"), "STRUCTURE_BUNDLE_BINDING_MISMATCH"),
    (lambda r: r["binding"]["config"].update(extra=1), "STRUCTURE_CONFIG_UNKNOWN_FIELDS"),
    (lambda r: r["binding"].update(rawmd_sha256="0" * 64), "STRUCTURE_BUNDLE_BINDING_MISMATCH"),
    (lambda r: r["binding"].pop("config"), "STRUCTURE_BUNDLE_BINDING_MISMATCH"),
    (lambda r: r.update(rawmd_file="structure.json"), "STRUCTURE_BUNDLE_ROLE_INVALID"),
    (lambda r: r.update(source_file="Missing.pdf"), "STRUCTURE_BUNDLE_ROLE_INVALID"),
    (lambda r: r["files"].update({"BUNDLE.json": "0" * 64}), "STRUCTURE_BUNDLE_ROLE_INVALID"),
    (lambda r: r["files"].update({"rawmd.MD": r["files"]["RawMD.md"]}), "STRUCTURE_BUNDLE_ROLE_INVALID"),
    (lambda r: r["files"].update({"../paper.md": "0" * 64}), "STRUCTURE_BUNDLE_STALE:INVALID_MEMBER_PATH"),
    (lambda r: r["files"].update({"/RawMD.md": r["files"]["RawMD.md"]}), "STRUCTURE_BUNDLE_STALE:INVALID_MEMBER_PATH"),
    (lambda r: r["files"].update({"RawMD.md.": "0" * 64}), "STRUCTURE_BUNDLE_STALE:INVALID_MEMBER_PATH"),
    (lambda r: r["files"].update({"RawMD.md:stream": "0" * 64}), "STRUCTURE_BUNDLE_STALE:INVALID_MEMBER_PATH"),
    (lambda r: r["files"].update({"sub\\RawMD.md": "0" * 64}), "STRUCTURE_BUNDLE_STALE:INVALID_MEMBER_PATH"),
    (lambda r: r.update(execution={"status": "PASS"}), "STRUCTURE_BUNDLE_MARKER_EXECUTION_INVALID"),
])
def test_a_manifest_whose_bindings_do_not_hold_stops_the_read(bundle, change, code):
    _edit_manifest(bundle, change)
    assert _error(bundle) == code


def test_an_extra_member_is_hash_checked_and_cannot_take_a_bound_role(bundle, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("x", encoding="utf-8")
    _edit_manifest(bundle, lambda r: r["files"].update({"link.md": file_sha(outside)}))
    os.link(outside, bundle / "link.md")
    verify(bundle)
    _edit_manifest(bundle, lambda r: r.update(rawmd_file="link.md"))
    assert _error(bundle) == "STRUCTURE_BUNDLE_BINDING_MISMATCH"


def test_two_roles_cannot_be_the_same_file(bundle):
    os.link(bundle / "structure.json", bundle / "Second.pdf")
    _edit_manifest(bundle, lambda r: r["files"].update({"Second.pdf": r["files"]["structure.json"]}) or r.update(
        source_file="Second.pdf"))
    assert _error(bundle) == "STRUCTURE_BUNDLE_ROLE_INVALID"


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows feature")
def test_a_member_reached_through_a_junction_is_refused(bundle):
    import _winapi
    (bundle / "real").mkdir()
    (bundle / "real" / "notes.md").write_text("notes", encoding="utf-8")
    _winapi.CreateJunction(str(bundle / "real"), str(bundle / "extra"))
    _edit_manifest(bundle, lambda r: r["files"].update({"extra/notes.md": file_sha(bundle / "real" / "notes.md")}))
    assert _error(bundle) == "STRUCTURE_BUNDLE_STALE:REPARSE_MEMBER"


def _rewrite_structure(folder, change, *, keep_id=False):
    path = folder / "structure.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    change(value)
    if not keep_id:
        value["structure_id"] = structure_id(value)
    data = json.dumps(value).encode("utf-8")
    path.write_bytes(data)
    _edit_manifest(folder, lambda r: r.update(structure_id=value["structure_id"]) or r["files"].update(
        {"structure.json": digest(data)}))


@pytest.mark.parametrize("change, keep_id, code", [
    (lambda v: v["blocks"][0].update(text="Edited text"), True, "STRUCTURE_ID_MISMATCH"),
    (lambda v: v.update(schema_version="other"), False, "STRUCTURE_SCHEMA_UNSUPPORTED"),
    (lambda v: v.update(pages=[]), False, "STRUCTURE_PAGES_INVALID"),
    (lambda v: v["pages"][0].update(physical_page=2), False, "STRUCTURE_PAGE_MAPPING_INVALID"),
    (lambda v: v["pages"][0].update(bbox=[0, 0, 0, 10]), False, "STRUCTURE_PAGE_MAPPING_INVALID"),
    (lambda v: v["blocks"][0].update(page_index=1), False, "STRUCTURE_PAGE_MAPPING_INVALID"),
    (lambda v: v["blocks"][0].update(reading_order=5), False, "STRUCTURE_BLOCKS_INVALID"),
    (lambda v: v["blocks"][1].update(block_id=v["blocks"][0]["block_id"]), False, "STRUCTURE_DUPLICATE_BLOCK"),
    (lambda v: v.update(blocks="none"), False, "STRUCTURE_BLOCKS_INVALID"),
    (lambda v: v.update(blocks=None), False, "STRUCTURE_BLOCKS_INVALID"),
])
def test_a_structure_file_that_breaks_the_page_and_block_rules_is_refused(bundle, change, keep_id, code):
    _rewrite_structure(bundle, change, keep_id=keep_id)
    assert _error(bundle) == code


def test_the_structure_rules_hold_for_the_built_file(bundle):
    _, value = load(bundle)
    assert validate_structure(value) is None
    assert len(value["blocks"]) == 2
