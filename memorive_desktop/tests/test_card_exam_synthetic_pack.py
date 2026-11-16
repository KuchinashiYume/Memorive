"""The card-distiller exam with a synthetic reference pack.

The real reference packs (exam forms and their gold answers) are kept out of the repository. This test
builds a pack of the same shape from forms the exam's own generator makes up
(card_distiller_exam_assets.generate_form), loads it the way the exam does, and scores answers
against it. No model is called.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from memorive_model_evaluation.card_distiller_exam_assets import generate_form
from memorive_model_evaluation.card_distiller_exam_protocol import (
    artifact_schema,
    output_schema,
    perfect_response,
    score_response,
)
from memorive_model_evaluation.errors import ModelEvaluationReferenceExamPackError
from memorive_model_evaluation.reference_exam_pack import (
    CARD_DISTILLER_REQUIRED_MEMBER_ROLES,
    CARD_DISTILLER_ROLE_VISIBILITY,
    CHECKSUM_NAME,
    MANIFEST_NAME,
    REFERENCE_PACK_CLASSIFICATION,
    REFERENCE_PACK_SCHEMA_VERSION,
    load_card_distiller_reference_exam_pack,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _write_pack(root: Path) -> None:
    """Write a complete card-distiller pack under root, with two generated forms (G and H)."""
    form_a, gold_a = generate_form("G")
    form_b, gold_b = generate_form("H")
    full_schema = output_schema()
    contents = {
        "forms/form_A.json": form_a,
        "forms/form_B.json": form_b,
        "gold/form_A_gold.json": gold_a,
        "gold/form_B_gold.json": gold_b,
        # The loader only needs these three to be JSON; scoring below calls the protocol directly.
        "prompts/card_distiller_prompt.json": {"note": "synthetic pack"},
        "repair/directed_repair_contract.json": {"note": "synthetic pack"},
        "scoring/scoring_protocol.json": {"note": "synthetic pack"},
        "schemas/card_distiller_primary_output.schema.json": full_schema,
        "schemas/card_distiller_primary_fact_map_shard.schema.json": artifact_schema(
            full_schema, "fact_map", target_count=36
        ),
        "schemas/card_distiller_primary_card_projection_shard.schema.json": artifact_schema(
            full_schema, "card_projection", target_count=4
        ),
    }
    members = []
    for relative, value in contents.items():
        data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        role = CARD_DISTILLER_REQUIRED_MEMBER_ROLES[relative]
        subject_visible, provider_visible = CARD_DISTILLER_ROLE_VISIBILITY[role]
        members.append({
            "path": relative,
            "role": role,
            "bytes": len(data),
            "sha256": _sha256(data),
            "subject_visible": subject_visible,
            "provider_visible": provider_visible,
            "provenance": {"generator": "card_distiller_exam_assets.generate_form"},
            "rights": {"repository_inclusion": "AUTHORIZED"},
        })
    manifest = json.dumps({
        "schema_version": REFERENCE_PACK_SCHEMA_VERSION,
        "suite": "card_distiller_primary",
        "classification": REFERENCE_PACK_CLASSIFICATION,
        "pack_id": "synthetic-card-distiller",
        "pack_revision": "1",
        "reference_regression_only": True,
        "blind_holdout_eligible": False,
        "qualification_eligible": False,
        "declassification": {"authorized": True, "authority": "synthetic test data"},
        "members": members,
    }, sort_keys=True).encode("utf-8")
    (root / MANIFEST_NAME).write_bytes(manifest)
    rows = [f"{row['sha256']}  {row['path']}" for row in members] + [f"{_sha256(manifest)}  {MANIFEST_NAME}"]
    (root / CHECKSUM_NAME).write_text("\n".join(rows) + "\n", encoding="utf-8")


def _json(pack, relative: str) -> dict:
    return json.loads(pack.member(relative).read_text(encoding="utf-8"))


def test_synthetic_pack_loads_and_keeps_the_gold_answers_from_the_model(tmp_path: Path) -> None:
    _write_pack(tmp_path)
    pack = load_card_distiller_reference_exam_pack(tmp_path)
    assert set(pack.members) == set(CARD_DISTILLER_REQUIRED_MEMBER_ROLES)
    shown_to_model = {row["path"] for row in pack.member_receipts if row["subject_visible"]}
    assert "forms/form_A.json" in shown_to_model
    assert not any(path.startswith("gold/") for path in shown_to_model)


def test_perfect_answers_score_100_and_a_wrong_anchor_scores_less(tmp_path: Path) -> None:
    _write_pack(tmp_path)
    pack = load_card_distiller_reference_exam_pack(tmp_path)
    schema = _json(pack, "schemas/card_distiller_primary_output.schema.json")
    for form_id in ("A", "B"):
        form = _json(pack, f"forms/form_{form_id}.json")
        gold = _json(pack, f"gold/form_{form_id}_gold.json")
        answer = perfect_response(gold)
        assert score_response(answer, form, gold, schema)["form_score"] == 100.0
        answer["fact_map"][0]["source_anchor"]["chunk_id"] = "NOT_THE_SOURCE_CHUNK"
        assert score_response(answer, form, gold, schema)["form_score"] < 100.0


def test_a_changed_pack_file_is_refused(tmp_path: Path) -> None:
    _write_pack(tmp_path)
    gold = tmp_path / "gold" / "form_A_gold.json"
    gold.write_bytes(gold.read_bytes().replace(b'"', b"'", 1))
    with pytest.raises(ModelEvaluationReferenceExamPackError, match="MEMBER_HASH_MISMATCH"):
        load_card_distiller_reference_exam_pack(tmp_path)
