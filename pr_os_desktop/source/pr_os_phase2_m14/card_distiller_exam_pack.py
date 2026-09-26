"""Contracts and fail-closed replay for the formal Card reference exam pack."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from .card_distiller_exam_assets import build_rehearsal_assets
from .card_distiller_exam_protocol import (
    AGGREGATE_WEIGHTS,
    ARTIFACTS,
    AXIS_WEIGHTS,
    CASES_PER_FORM,
    FAMILIES,
    PACKS_PER_FORM,
    SCORING_CURVES,
    aggregate_dual_form,
    artifact_schema,
    build_artifact_prompt,
    form_equivalence,
    output_schema,
    perfect_response,
    score_response,
)
from .reference_exam_pack import (
    DEFAULT_CARD_DISTILLER_REFERENCE_PACK,
    load_card_distiller_reference_exam_pack,
)


FORM_SCORE_SCHEMA_VERSION = "m14-card-distiller-form-score-v3-calibration-successor"
DUAL_FORM_SCORE_SCHEMA_VERSION = (
    "m14-card-distiller-dual-form-scorecard-v2-calibration-successor"
)
LANGUAGE_SUCCESSOR_PACK_REVISION = "card_distiller_language_bank_r2.0"
LANGUAGE_SUCCESSOR_CASES_PER_FORM = 86
LANGUAGE_SUCCESSOR_PACKS_PER_FORM = 9
LANGUAGE_SUCCESSOR_COUNTS_PER_FORM = {"en": 50, "zh": 18, "ja": 18}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _content_sha256(value: dict[str, Any]) -> str:
    unsigned = deepcopy(value)
    unsigned.pop("content_sha256", None)
    return hashlib.sha256(_canonical_bytes(unsigned)).hexdigest().upper()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"CARD_DISTILLER_DUPLICATE_JSON_KEY:{key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )
    if not isinstance(value, dict):
        raise ValueError(f"CARD_DISTILLER_JSON_OBJECT_REQUIRED:{path.name}")
    return value


def prompt_contract() -> dict[str, Any]:
    return {
        "schema_version": "m14-card-distiller-prompt-contract-v1",
        "suite": "card_distiller_primary",
        "builder": "m14_quality_sentinel.card_distiller_exam_protocol.build_artifact_prompt",
        "provider_identity_blind": True,
        "normal_logical_calls": 8,
        "artifacts": {
            "fact_map": {"shards_per_form": 2, "targets_per_shard": 36},
            "card_projection": {"shards_per_form": 2, "targets_per_shard": 4},
        },
        "provider_visible_inputs": ["FORM", "TARGET_IDS", "SHARD_SCHEMA"],
        "forbidden_provider_inputs": [
            "GOLD",
            "EXPECTED_VALUES",
            "DIFFICULTY",
            "FAMILY_LABEL",
            "SCORING_PROTOCOL",
            "PROVIDER_OR_MODEL_IDENTITY",
        ],
        "output_mode": "STRICT_JSON_OBJECT",
    }


def repair_contract() -> dict[str, Any]:
    return {
        "schema_version": "m14-card-distiller-directed-repair-contract-v1",
        "suite": "card_distiller_primary",
        "repair_scope": "EXACT_FAILED_SHARD_ONLY",
        "maximum_repairs_per_normal_shard": 1,
        "maximum_repair_logical_calls": 8,
        "normal_merge_is_repair": False,
        "allowed_defect_classes": [
            "SCHEMA_DIAGNOSTIC",
            "MISSING_IDS",
            "DUPLICATE_IDS",
            "EXTRA_IDS",
        ],
        "gold_visible": False,
        "expected_values_visible": False,
        "scoring_truth_visible": False,
        "quality_rewrite_allowed": False,
    }


def scoring_protocol() -> dict[str, Any]:
    curves = {
        key: [[float(x), float(y)] for x, y in values]
        for key, values in SCORING_CURVES.items()
    }
    return {
        "schema_version": "m14-card-distiller-scoring-protocol-v3",
        "suite": "card_distiller_primary",
        "provider_blind": True,
        "model_identity_input_allowed": False,
        "form_score_schema_version": FORM_SCORE_SCHEMA_VERSION,
        "dual_form_score_schema_version": DUAL_FORM_SCORE_SCHEMA_VERSION,
        "axis_weights": AXIS_WEIGHTS,
        "aggregate_weights": AGGREGATE_WEIGHTS,
        "piecewise_curves": curves,
        "score_caps": {
            "critical_scientific_failure_maximum": 59.0,
            "major_finding_maximum_below": 90.0,
            "successful_repair_maximum_below": 90.0,
        },
        "interpretation_bands": [
            {
                "minimum": 0.0,
                "maximum_exclusive": 60.0,
                "code": "UNSTABLE_OR_INSUFFICIENT",
            },
            {
                "minimum": 60.0,
                "maximum_exclusive": 75.0,
                "code": "BARELY_USABLE_AND_UNSTABLE",
            },
            {
                "minimum": 75.0,
                "maximum_exclusive": 85.0,
                "code": "USABLE_BELOW_PHASE1_PRIMARY_TIER",
            },
            {
                "minimum": 85.0,
                "maximum_inclusive": 100.0,
                "code": "PHASE1_PRIMARY_TIER_IF_HARD_GATES_PASS",
            },
        ],
        "quality_hard_gate": "FAIL_IF_ANY_CRITICAL_FINDING",
        "reference_pack_use": "REFERENCE_REGRESSION_ONLY",
        "qualification_eligible": False,
    }


def _verify_content_self_hash(value: dict[str, Any], label: str) -> None:
    if value.get("content_sha256") != _content_sha256(value):
        raise ValueError(f"CARD_DISTILLER_CONTENT_SELF_HASH_MISMATCH:{label}")


def _provider_prompt_audit(
    form: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    forbidden = (
        '"expected"',
        '"difficulty"',
        *FAMILIES,
        "deepseek",
        "anthropic",
        "qwen",
        "openai",
    )
    prompt_count = 0
    for artifact in ARTIFACTS:
        id_field = "case_id" if artifact == "fact_map" else "pack_id"
        rows = form["cases"] if artifact == "fact_map" else form["packs"]
        target_count = 36 if artifact == "fact_map" else 4
        for offset in range(0, len(rows), target_count):
            target_ids = [
                row[id_field] for row in rows[offset : offset + target_count]
            ]
            shard_schema = artifact_schema(
                schema,
                artifact,
                target_count=len(target_ids),
            )
            prompt = build_artifact_prompt(
                form,
                artifact,
                target_ids=target_ids,
                schema=shard_schema,
            )
            lowered = prompt.casefold()
            for marker in forbidden:
                if marker.casefold() in lowered:
                    raise ValueError(
                        f"CARD_DISTILLER_PROVIDER_PROMPT_LEAK:{artifact}:{marker}"
                    )
            prompt_count += 1
    return {
        "status": "PASS",
        "prompt_count": prompt_count,
        "gold_sent_to_provider": False,
        "provider_identity_blind": True,
    }


def verify_language_successor_reference_pack(
    root: str | Path,
) -> dict[str, Any]:
    """Verify the 172-item language bank without weakening the frozen v1 gate."""

    pack = load_card_distiller_reference_exam_pack(root)
    manifest = dict(pack.manifest)
    if manifest.get("pack_revision") != LANGUAGE_SUCCESSOR_PACK_REVISION:
        raise ValueError("CARD_DISTILLER_LANGUAGE_PACK_REVISION_MISMATCH")
    form_a = _load_json(pack.member("forms/form_A.json"))
    form_b = _load_json(pack.member("forms/form_B.json"))
    gold_a = _load_json(pack.member("gold/form_A_gold.json"))
    gold_b = _load_json(pack.member("gold/form_B_gold.json"))
    for label, value in (
        ("FORM_A", form_a),
        ("FORM_B", form_b),
        ("GOLD_A", gold_a),
        ("GOLD_B", gold_b),
    ):
        _verify_content_self_hash(value, label)
    if [form_a.get("form_id"), form_b.get("form_id")] != ["A", "B"]:
        raise ValueError("CARD_DISTILLER_LANGUAGE_FORM_ID_MISMATCH")
    if [gold_a.get("form_id"), gold_b.get("form_id")] != ["A", "B"]:
        raise ValueError("CARD_DISTILLER_LANGUAGE_GOLD_ID_MISMATCH")

    topology: dict[str, Any] = {}
    identities: list[set[str]] = []
    for form_id, form, gold in (
        ("A", form_a, gold_a),
        ("B", form_b, gold_b),
    ):
        if (
            len(form.get("cases", [])) != LANGUAGE_SUCCESSOR_CASES_PER_FORM
            or len(gold.get("cases", [])) != LANGUAGE_SUCCESSOR_CASES_PER_FORM
            or len(form.get("packs", [])) != LANGUAGE_SUCCESSOR_PACKS_PER_FORM
            or len(gold.get("packs", [])) != LANGUAGE_SUCCESSOR_PACKS_PER_FORM
        ):
            raise ValueError("CARD_DISTILLER_LANGUAGE_TOPOLOGY_MISMATCH")
        form_case_ids = [str(row["case_id"]) for row in form["cases"]]
        gold_case_ids = [str(row["case_id"]) for row in gold["cases"]]
        if form_case_ids != gold_case_ids or len(set(form_case_ids)) != len(form_case_ids):
            raise ValueError("CARD_DISTILLER_LANGUAGE_CASE_EXACT_SET_MISMATCH")
        language_by_case = {
            str(row["case_id"]): str(row["source_language"])
            for row in form["cases"]
        }
        language_counts = {
            language: sum(value == language for value in language_by_case.values())
            for language in ("en", "zh", "ja")
        }
        if language_counts != LANGUAGE_SUCCESSOR_COUNTS_PER_FORM:
            raise ValueError("CARD_DISTILLER_LANGUAGE_DISTRIBUTION_MISMATCH")
        difficulty_counts = {
            language: {
                difficulty: sum(
                    language_by_case[str(row["case_id"])] == language
                    and row.get("difficulty") == difficulty
                    for row in gold["cases"]
                )
                for difficulty in ("easy", "medium", "hard")
            }
            for language in ("en", "zh", "ja")
        }
        if difficulty_counts["zh"] != {"easy": 6, "medium": 6, "hard": 6}:
            raise ValueError("CARD_DISTILLER_ZH_DIFFICULTY_MISMATCH")
        if difficulty_counts["ja"] != {"easy": 6, "medium": 6, "hard": 6}:
            raise ValueError("CARD_DISTILLER_JA_DIFFICULTY_MISMATCH")
        if sum(difficulty_counts["en"].values()) != 50 or any(
            value < 16 for value in difficulty_counts["en"].values()
        ):
            raise ValueError("CARD_DISTILLER_EN_DIFFICULTY_MISMATCH")
        for language in ("en", "zh", "ja"):
            families = {
                str(row["family"])
                for row in gold["cases"]
                if language_by_case[str(row["case_id"])] == language
            }
            if families != set(FAMILIES):
                raise ValueError("CARD_DISTILLER_LANGUAGE_FAMILY_COVERAGE_MISMATCH")
        topology[form_id] = {
            "cases": len(form_case_ids),
            "packs": len(form["packs"]),
            "languages": language_counts,
            "difficulties": difficulty_counts,
        }
        identities.append(set(form_case_ids))
    if identities[0] & identities[1]:
        raise ValueError("CARD_DISTILLER_CROSS_FORM_IDENTITY_COLLISION")

    full_schema = _load_json(
        pack.member("schemas/card_distiller_primary_output.schema.json")
    )
    if full_schema != output_schema(
        fact_count=LANGUAGE_SUCCESSOR_CASES_PER_FORM,
        pack_count=LANGUAGE_SUCCESSOR_PACKS_PER_FORM,
    ):
        raise ValueError("CARD_DISTILLER_LANGUAGE_OUTPUT_SCHEMA_DRIFT")
    if _load_json(
        pack.member("schemas/card_distiller_primary_fact_map_shard.schema.json")
    ) != artifact_schema(full_schema, "fact_map", target_count=36):
        raise ValueError("CARD_DISTILLER_LANGUAGE_FACT_SCHEMA_DRIFT")
    if _load_json(
        pack.member(
            "schemas/card_distiller_primary_card_projection_shard.schema.json"
        )
    ) != artifact_schema(full_schema, "card_projection", target_count=4):
        raise ValueError("CARD_DISTILLER_LANGUAGE_PROJECTION_SCHEMA_DRIFT")
    prompt = _load_json(pack.member("prompts/card_distiller_prompt.json"))
    if (
        prompt.get("schema_version")
        != "m14-card-distiller-language-bank-prompt-v2"
        or prompt.get("provider_identity_blind") is not True
        or prompt.get("bank_language_distribution")
        != {"en": 100, "zh": 36, "ja": 36}
    ):
        raise ValueError("CARD_DISTILLER_LANGUAGE_PROMPT_CONTRACT_DRIFT")
    scoring = _load_json(pack.member("scoring/scoring_protocol.json"))
    if (
        scoring.get("schema_version")
        != "m14-card-distiller-scoring-protocol-v4-language-successor"
        or scoring.get("repair_burden", {}).get("deducted_from_capability_score")
        is not False
    ):
        raise ValueError("CARD_DISTILLER_LANGUAGE_SCORING_CONTRACT_DRIFT")

    perfect_scores = []
    prompt_audits = []
    for form, gold in ((form_a, gold_a), (form_b, gold_b)):
        score = score_response(perfect_response(gold), form, gold, full_schema)
        if (
            score.get("form_score") != 100.0
            or score.get("quality_hard_gate") != "PASS"
            or score.get("critical_any")
            or score.get("major_any")
        ):
            raise ValueError("CARD_DISTILLER_LANGUAGE_PERFECT_REPLAY_FAIL")
        perfect_scores.append(score)
        prompt_audits.append(_provider_prompt_audit(form, full_schema))
    hidden_roles = {
        row["role"]
        for row in pack.member_receipts
        if row["provider_visible"] is False
    }
    if not {"GOLD_A", "GOLD_B", "SCORING_PROTOCOL"} <= hidden_roles:
        raise ValueError("CARD_DISTILLER_LANGUAGE_GOLD_VISIBILITY_FAIL")
    return {
        "status": "PASS",
        "formal_exam_pack_ready": True,
        "pack_id": manifest["pack_id"],
        "pack_revision": manifest["pack_revision"],
        "classification": manifest["classification"],
        "reference_regression_only": True,
        "blind_holdout_eligible": False,
        "qualification_eligible": False,
        "data_member_count": len(pack.members),
        "filesystem_member_count": len(pack.members) + 2,
        "pack_fingerprint": pack.pack_fingerprint,
        "manifest_sha256": pack.manifest_receipt["sha256"],
        "checksums_sha256": pack.checksum_receipt["sha256"],
        "topology": topology,
        "bank_language_distribution": {"en": 100, "zh": 36, "ja": 36},
        "perfect_replay": {
            "form_A": perfect_scores[0]["form_score"],
            "form_B": perfect_scores[1]["form_score"],
            "dual_form": aggregate_dual_form(perfect_scores[0], perfect_scores[1]),
        },
        "provider_prompt_audit": prompt_audits,
        "gold_sent_to_subject": False,
        "gold_sent_to_provider": False,
        "provider_api_calls": 0,
    }


def verify_reference_pack(
    root: str | Path = DEFAULT_CARD_DISTILLER_REFERENCE_PACK,
) -> dict[str, Any]:
    """Verify exact-set, E/F topology, local Gold isolation, and perfect replay."""

    manifest_path = Path(root).resolve() / "manifest.reference.json"
    if manifest_path.is_file():
        candidate_manifest = _load_json(manifest_path)
        if candidate_manifest.get("pack_revision") == LANGUAGE_SUCCESSOR_PACK_REVISION:
            return verify_language_successor_reference_pack(root)

    pack = load_card_distiller_reference_exam_pack(root)
    form_a = _load_json(pack.member("forms/form_A.json"))
    form_b = _load_json(pack.member("forms/form_B.json"))
    gold_a = _load_json(pack.member("gold/form_A_gold.json"))
    gold_b = _load_json(pack.member("gold/form_B_gold.json"))
    for label, value in (
        ("FORM_A", form_a),
        ("FORM_B", form_b),
        ("GOLD_A", gold_a),
        ("GOLD_B", gold_b),
    ):
        _verify_content_self_hash(value, label)

    if (
        form_a.get("form_id") != "E"
        or form_b.get("form_id") != "F"
        or gold_a.get("form_id") != "E"
        or gold_b.get("form_id") != "F"
    ):
        raise ValueError("CARD_DISTILLER_REFERENCE_IDENTITY_E_F_REQUIRED")
    if any(value.get("qualification_eligible") is not False for value in (form_a, form_b, gold_a, gold_b)):
        raise ValueError("CARD_DISTILLER_REFERENCE_QUALIFICATION_FLAG_INVALID")

    full_schema = _load_json(
        pack.member("schemas/card_distiller_primary_output.schema.json")
    )
    fact_schema = _load_json(
        pack.member("schemas/card_distiller_primary_fact_map_shard.schema.json")
    )
    projection_schema = _load_json(
        pack.member(
            "schemas/card_distiller_primary_card_projection_shard.schema.json"
        )
    )
    if full_schema != output_schema():
        raise ValueError("CARD_DISTILLER_OUTPUT_SCHEMA_DRIFT")
    if fact_schema != artifact_schema(
        full_schema,
        "fact_map",
        target_count=36,
    ):
        raise ValueError("CARD_DISTILLER_FACT_SHARD_SCHEMA_DRIFT")
    if projection_schema != artifact_schema(
        full_schema,
        "card_projection",
        target_count=4,
    ):
        raise ValueError("CARD_DISTILLER_PROJECTION_SHARD_SCHEMA_DRIFT")

    if _load_json(pack.member("prompts/card_distiller_prompt.json")) != prompt_contract():
        raise ValueError("CARD_DISTILLER_PROMPT_CONTRACT_DRIFT")
    if _load_json(
        pack.member("repair/directed_repair_contract.json")
    ) != repair_contract():
        raise ValueError("CARD_DISTILLER_REPAIR_CONTRACT_DRIFT")
    if _load_json(pack.member("scoring/scoring_protocol.json")) != scoring_protocol():
        raise ValueError("CARD_DISTILLER_SCORING_PROTOCOL_DRIFT")

    equivalence = form_equivalence(form_a, gold_a, form_b, gold_b)
    if equivalence.get("status") != "PASS":
        raise ValueError("CARD_DISTILLER_FORM_EQUIVALENCE_FAIL")
    scores = []
    for form, gold in ((form_a, gold_a), (form_b, gold_b)):
        score = score_response(perfect_response(gold), form, gold, full_schema)
        if (
            score.get("form_score") != 100.0
            or score.get("quality_hard_gate") != "PASS"
            or score.get("critical_any")
            or score.get("major_any")
        ):
            raise ValueError("CARD_DISTILLER_PERFECT_REPLAY_FAIL")
        scores.append(score)
    dual = aggregate_dual_form(scores[0], scores[1])
    prompts = [
        _provider_prompt_audit(form_a, full_schema),
        _provider_prompt_audit(form_b, full_schema),
    ]
    hidden_roles = {
        row["role"]
        for row in pack.member_receipts
        if row["provider_visible"] is False
    }
    if not {"GOLD_A", "GOLD_B", "SCORING_PROTOCOL"} <= hidden_roles:
        raise ValueError("CARD_DISTILLER_GOLD_VISIBILITY_FAIL")

    return {
        "status": "PASS",
        "formal_exam_pack_ready": True,
        "pack_id": pack.manifest["pack_id"],
        "pack_revision": pack.manifest["pack_revision"],
        "classification": pack.manifest["classification"],
        "reference_regression_only": True,
        "blind_holdout_eligible": False,
        "qualification_eligible": False,
        "data_member_count": len(pack.members),
        "filesystem_member_count": len(pack.members) + 2,
        "pack_fingerprint": pack.pack_fingerprint,
        "manifest_sha256": pack.manifest_receipt["sha256"],
        "checksums_sha256": pack.checksum_receipt["sha256"],
        "topology": {
            "forms": 2,
            "internal_form_ids": ["E", "F"],
            "cases_each": CASES_PER_FORM,
            "packs_each": PACKS_PER_FORM,
            "families": list(FAMILIES),
        },
        "equivalence": equivalence,
        "perfect_replay": {
            "form_A": scores[0]["form_score"],
            "form_B": scores[1]["form_score"],
            "dual_form": dual,
        },
        "provider_prompt_audit": prompts,
        "gold_sent_to_subject": False,
        "gold_sent_to_provider": False,
        "provider_api_calls": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser("verify-reference-pack")
    verify_parser.add_argument(
        "--pack-root",
        default=str(DEFAULT_CARD_DISTILLER_REFERENCE_PACK),
    )
    build_parser = subparsers.add_parser("build-rehearsal-assets")
    build_parser.add_argument("--output-root", required=True)
    build_parser.add_argument("--form-a-id", default="G")
    build_parser.add_argument("--form-b-id", default="H")
    args = parser.parse_args(argv)
    if args.command == "verify-reference-pack":
        result = verify_reference_pack(args.pack_root)
    else:
        result = build_rehearsal_assets(
            args.output_root,
            form_ids=(args.form_a_id, args.form_b_id),
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
