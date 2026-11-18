"""An exam pack reads its members from inside its own folder only.

A manifest lists each member by a path relative to the pack. A path such as ../outside.json, or an
absolute one, would make the exam read a file elsewhere on the computer, and forms and prompts are sent
to the model provider. The loaders used to stop this only as a side effect of requiring the files on disk
to be exactly the listed ones; that requirement is gone (a __pycache__ folder or a Thumbs.db stopped a
valid pack), so the boundary is checked on each member. In these made-up packs the outside file has the
size and hash the manifest gives, so nothing but that boundary stops it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from memorive.exams.evaluation import card_reviewer_exam_pack, ocr_page_exam_pack
from memorive.exams.evaluation.ocr_page_exam import ExamMethodError, sha256_json
from memorive.exams.evaluation.ocr_page_exam_assets import (
    CANONICAL_REPO_RELATIVE_PATH,
    DECLASSIFICATION_AUTHORIZATION_ID,
    PACK_SCHEMA_VERSION,
    PROMPT_SHA256,
    REFERENCE_PACK_ID,
    REFERENCE_PACK_REVISION,
)


def _outside(tmp_path: Path) -> tuple[Path, Path]:
    """A pack folder and, next to it, a JSON file that is not part of the pack."""
    pack = tmp_path / "pack"
    pack.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"cases": []}), encoding="utf-8")
    return pack, outside


def _member_paths(outside: Path) -> list[str]:
    return ["../outside.json", outside.as_posix()]


@pytest.mark.parametrize("which", [0, 1], ids=["parent folder", "absolute path"])
def test_card_reviewer_pack_refuses_a_member_outside_the_pack(tmp_path, which):
    pack, outside = _outside(tmp_path)
    relative = _member_paths(outside)[which]
    first = {"path": relative, "role": "FORM_A", "bytes": outside.stat().st_size,
             "sha256": card_reviewer_exam_pack.sha256_file(outside), "subject_visible": True, "provider_visible": True}
    rest = [{"path": f"forms/missing_{n}.json", "role": "FORM_B", "bytes": 0, "sha256": "0" * 64} for n in range(9)]
    (pack / "manifest.candidate.json").write_text(json.dumps({
        "classification": "REFERENCE_EXAM_PACK_CANDIDATE",
        "declassification": {"authorized": False},
        "members": [first, *rest],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="MEMBER_ESCAPES_PACK"):
        card_reviewer_exam_pack.verify_candidate_pack(pack)


@pytest.mark.parametrize("which", [0, 1], ids=["parent folder", "absolute path"])
def test_ocr_page_pack_refuses_a_member_outside_the_pack(tmp_path, which):
    pack, outside = _outside(tmp_path)
    relative = _member_paths(outside)[which]
    members = [{"path": relative, "bytes": outside.stat().st_size, "sha256": ocr_page_exam_pack.sha256_file(outside),
                "subject_visible": True, "provider_visible": True, "rights_class": "SELF_OWNED_SYNTHETIC_OUTPUT",
                "provenance": "made up for this test"}]
    manifest = {
        "schema_version": PACK_SCHEMA_VERSION,
        "reference_pack_id": REFERENCE_PACK_ID,
        "classification": "REFERENCE_EXAM_PACK",
        "revision": REFERENCE_PACK_REVISION,
        "reference_regression_only": True,
        "declassification_authorization_id": DECLASSIFICATION_AUTHORIZATION_ID,
        "canonical_repo_relative_path": CANONICAL_REPO_RELATIVE_PATH,
        "role_id": "ocr_page",
        "source_origin": "self_synthetic",
        "external_source_used": False,
        "restricted_source_used": False,
        "blind_holdout_eligible": False,
        "qualification_eligible": False,
        "provider_requests_during_build": 0,
        "gold_sent_to_provider": False,
        "production_prompt_sha256": PROMPT_SHA256,
        "content_directed_repair_allowed": False,
        "members": members,
        "member_set_sha256": sha256_json(members),
    }
    manifest["manifest_payload_sha256"] = sha256_json(manifest)
    (pack / "manifest.reference.json").write_text(json.dumps(manifest), encoding="utf-8")
    (pack / "SHA256SUMS.txt").write_text("", encoding="utf-8")
    with pytest.raises(ExamMethodError, match="MANIFEST_MEMBER_ESCAPES_PACK"):
        ocr_page_exam_pack.verify_reference_pack(pack)
