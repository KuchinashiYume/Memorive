"""Verifier and perfect-response replay for card-reviewer candidate packs."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


from .card_reviewer_exam import audit_response, score_form
from .reference_exam_pack import (
    DEFAULT_CARD_REVIEWER_REFERENCE_PACK,
    ReferenceExamPack,
    load_card_reviewer_reference_exam_pack,
)


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"DUPLICATE_JSON_KEY:{key}")
        value[key] = item
    return value


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys)
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def parse_checksums(path: Path) -> dict[str, str]:
    rows = {}
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line:
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ValueError(f"CHECKSUM_ROW_INVALID:{index}")
        if parts[1] in rows:
            raise ValueError(f"CHECKSUM_DUPLICATE:{parts[1]}")
        rows[parts[1]] = parts[0]
    return rows


def validate_repair_contract(value: dict[str, Any]) -> None:
    version = value.get("schema_version")
    if version == "m14-card-reviewer-directed-repair-contract-v2":
        maximum_turns = value.get(
            "maximum_model_repair_turns_per_form"
        )
    elif version == "m14-card-reviewer-directed-repair-contract-v3":
        structural = value.get("structural_recovery")
        exam = value.get("exam_quality_repair")
        if not isinstance(structural, dict) or not isinstance(exam, dict):
            raise ValueError(
                "REPAIR_CONTRACT_V3_STRUCTURAL_AND_EXAM_SECTIONS_REQUIRED"
            )
        maximum_turns = structural.get(
            "maximum_model_repair_turns_per_form"
        )
        if (
            exam.get("maximum_model_repair_rounds_per_exam") != 1
            or exam.get("repair_after_both_initial_forms_scored")
            is not True
            or exam.get("wrong_item_limit") is not None
            or exam.get("all_and_only_wrong_items_required") is not True
            or exam.get(
                "any_remaining_or_new_wrong_item_disqualifies"
            )
            is not True
            or exam.get("failed_complete_repair_final_score") != 0
        ):
            raise ValueError(
                "REPAIR_CONTRACT_V3_EXAM_QUALITY_REPAIR_INVALID"
            )
    else:
        raise ValueError(
            f"REPAIR_CONTRACT_SCHEMA_VERSION_UNSUPPORTED:{version}"
        )
    if maximum_turns != 1:
        raise ValueError("REPAIR_CONTRACT_MAXIMUM_TURNS_MUST_EQUAL_ONE")
    if value.get("repair_exact_set_required") is not True:
        raise ValueError("REPAIR_CONTRACT_EXACT_SET_REQUIRED")
    if value.get("whole_form_regeneration_forbidden") is not True:
        raise ValueError("REPAIR_CONTRACT_WHOLE_FORM_REGENERATION_MUST_BE_FORBIDDEN")
    excluded = value.get("repair_prompt_excludes")
    if not isinstance(excluded, list) or not {
        "gold",
        "expected_answer",
        "scoring_detail",
        "private_holdout_identity",
    }.issubset(set(excluded)):
        raise ValueError("REPAIR_CONTRACT_PROMPT_EXCLUSIONS_INCOMPLETE")


def perfect_response(gold: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for item in gold["cases"]:
        if item["expected_verdict"] == "PASS":
            rows.append(
                {
                    "case_id": item["case_id"],
                    "verdict": "PASS",
                    "issue_family": "none",
                    "severity": "none",
                    "target_pointer": "",
                    "status": "SUPPORTED",
                    "evidence_refs": [],
                    "action": "no_issue",
                    "reason": "No substantive defect is supported by the supplied evidence.",
                }
            )
        else:
            rows.append(
                {
                    "case_id": item["case_id"],
                    "verdict": "FAIL",
                    "issue_family": item["issue_family"],
                    "severity": item["severity"],
                    "target_pointer": item["accepted_target_pointers"][0],
                    "status": item["expected_status"],
                    "evidence_refs": item["required_evidence_refs"],
                    "action": item["accepted_actions"][0],
                    "reason": "The candidate conflicts with the cited synthetic evidence.",
                }
            )
    return {"reviews": rows}


def verify_reference_pack(
    pack: Path | str = DEFAULT_CARD_REVIEWER_REFERENCE_PACK,
) -> dict[str, Any]:
    """Verify the canonical pack and replay a perfect local score for A/B."""

    verified: ReferenceExamPack = load_card_reviewer_reference_exam_pack(pack)
    form_schema = load_json(verified.member("schemas/card_reviewer_form.schema.json"))
    gold_schema = load_json(verified.member("schemas/card_reviewer_gold.schema.json"))
    output_schema = load_json(verified.member("schemas/card_reviewer_output.schema.json"))
    scoring = load_json(verified.member("scoring/scoring_protocol.json"))
    prompt = load_json(verified.member("prompts/card_reviewer_prompt.json"))
    repair = load_json(verified.member("repair/directed_repair_contract.json"))
    validate_repair_contract(repair)
    if (
        prompt.get("gold_available_to_subject") is not False
        or prompt.get("gold_available_to_provider") is not False
    ):
        raise ValueError("PROMPT_GOLD_VISIBILITY_INVALID")

    form_receipts: dict[str, Any] = {}
    evidence_text_hashes: dict[str, set[str]] = {}
    for form_name in ("A", "B"):
        form = load_json(verified.member(f"forms/form_{form_name}.json"))
        gold = load_json(verified.member(f"gold/form_{form_name}_gold.json"))
        form_errors = list(Draft202012Validator(form_schema).iter_errors(form))
        gold_errors = list(Draft202012Validator(gold_schema).iter_errors(gold))
        if form_errors or gold_errors:
            raise ValueError(
                f"FORM_OR_GOLD_SCHEMA_INVALID:{form_name}:"
                f"{len(form_errors)}:{len(gold_errors)}"
            )
        case_ids = [case["case_id"] for case in form["cases"]]
        gold_ids = [case["case_id"] for case in gold["cases"]]
        if case_ids != gold_ids or len(case_ids) != len(set(case_ids)):
            raise ValueError(f"FORM_GOLD_IDENTITY_MISMATCH:{form_name}")
        family_counts = Counter(
            item["issue_family"]
            if item["issue_family"] != "none"
            else "clean_control"
            for item in gold["cases"]
        )
        if set(family_counts.values()) != {12} or len(family_counts) != 6:
            raise ValueError(f"FAMILY_QUOTA_INVALID:{form_name}:{family_counts}")
        evidence_count = sum(len(case["evidence_units"]) for case in form["cases"])
        if evidence_count != 720:
            raise ValueError(
                f"EVIDENCE_UNIT_COUNT_INVALID:{form_name}:{evidence_count}"
            )
        perfect = perfect_response(gold)
        output_errors = list(Draft202012Validator(output_schema).iter_errors(perfect))
        if output_errors:
            raise ValueError(
                f"PERFECT_OUTPUT_SCHEMA_INVALID:{form_name}:{len(output_errors)}"
            )
        audit = audit_response(form=form, response=perfect, schema=output_schema)
        if audit["status"] != "PASS":
            raise ValueError(
                f"PERFECT_OUTPUT_AUDIT_INVALID:{form_name}:{audit['status']}"
            )
        score = score_form(
            form=form,
            final_response=perfect,
            gold=gold,
            schema=output_schema,
            scoring_protocol=scoring,
        )
        if score["score"] != 100.0 or score["hard_gate_verdict"] != "PASS":
            raise ValueError(
                f"PERFECT_SCORE_REPLAY_FAILED:{form_name}:{score['score']}"
            )
        evidence_text_hashes[form_name] = {
            hashlib.sha256(unit["text"].encode("utf-8")).hexdigest()
            for case in form["cases"]
            for unit in case["evidence_units"]
        }
        form_receipts[form_name] = {
            "cases": len(case_ids),
            "evidence_units": evidence_count,
            "family_counts": dict(sorted(family_counts.items())),
            "perfect_score": score["score"],
            "hard_gate_verdict": score["hard_gate_verdict"],
        }
    overlap = evidence_text_hashes["A"] & evidence_text_hashes["B"]
    if overlap:
        raise ValueError(f"FORM_EVIDENCE_TEXT_HASH_OVERLAP:{len(overlap)}")
    return {
        "status": "PASS",
        "classification": "REFERENCE_EXAM_PACK",
        "reference_regression_only": True,
        "blind_holdout_eligible": False,
        "qualification_eligible": False,
        "member_count": len(verified.members),
        "filesystem_file_count": len(verified.members) + 2,
        "forms": form_receipts,
        "form_evidence_text_hash_overlap": 0,
        "gold_sent_to_subject": False,
        "gold_sent_to_provider": False,
        "provider_api_calls": 0,
        "pack_fingerprint": verified.pack_fingerprint,
        "manifest_sha256": verified.manifest_receipt["sha256"],
        "checksums_sha256": verified.checksum_receipt["sha256"],
    }


def verify_candidate_pack(pack: Path) -> dict[str, Any]:
    manifest_path = pack / "manifest.candidate.json"
    checksum_path = pack / "SHA256SUMS.txt"
    manifest = load_json(manifest_path)
    if manifest.get("classification") != "REFERENCE_EXAM_PACK_CANDIDATE":
        raise ValueError("CANDIDATE_CLASSIFICATION_REQUIRED")
    if (manifest.get("declassification") or {}).get("authorized") is not False:
        raise ValueError("CANDIDATE_MUST_NOT_CLAIM_DECLASSIFICATION")
    members = manifest.get("members")
    if not isinstance(members, list) or len(members) != 10:
        raise ValueError("TEN_CANDIDATE_MEMBERS_REQUIRED")
    expected_files = {"manifest.candidate.json", "SHA256SUMS.txt"}
    expected_visibility = {
        "FORM_A": (True, True),
        "FORM_B": (True, True),
        "PROMPT": (True, True),
        "OUTPUT_SCHEMA": (True, True),
        "GOLD_A": (False, False),
        "GOLD_B": (False, False),
        "FORM_SCHEMA": (False, False),
        "GOLD_SCHEMA": (False, False),
        "SCORING_PROTOCOL": (False, False),
        "REPAIR_CONTRACT": (False, False),
    }
    member_paths: dict[str, Path] = {}
    for row in members:
        relative = row["path"]
        path = pack / relative
        if not path.is_file():
            raise ValueError(f"MEMBER_MISSING:{relative}")
        if path.stat().st_size != row["bytes"] or sha256_file(path) != row["sha256"]:
            raise ValueError(f"MEMBER_RECEIPT_MISMATCH:{relative}")
        role = row["role"]
        if role not in expected_visibility:
            raise ValueError(f"MEMBER_ROLE_INVALID:{relative}:{role}")
        observed_visibility = (row.get("subject_visible"), row.get("provider_visible"))
        if observed_visibility != expected_visibility[role]:
            raise ValueError(f"ROLE_VISIBILITY_INVALID:{relative}:{observed_visibility}")
        load_json(path)
        member_paths[relative] = path
        expected_files.add(relative)
    observed_files = {
        path.relative_to(pack).as_posix()
        for path in pack.rglob("*")
        if path.is_file()
    }
    if observed_files != expected_files:
        raise ValueError(f"FILESYSTEM_EXACT_SET_MISMATCH:{sorted(observed_files ^ expected_files)}")
    checksums = parse_checksums(checksum_path)
    expected_checksums = expected_files - {"SHA256SUMS.txt"}
    if set(checksums) != expected_checksums:
        raise ValueError("CHECKSUM_EXACT_SET_MISMATCH")
    for relative, expected in checksums.items():
        if sha256_file(pack / relative) != expected:
            raise ValueError(f"CHECKSUM_MISMATCH:{relative}")

    form_schema = load_json(member_paths["schemas/card_reviewer_form.schema.json"])
    gold_schema = load_json(member_paths["schemas/card_reviewer_gold.schema.json"])
    output_schema = load_json(member_paths["schemas/card_reviewer_output.schema.json"])
    scoring = load_json(member_paths["scoring/scoring_protocol.json"])
    prompt = load_json(member_paths["prompts/card_reviewer_prompt.json"])
    repair = load_json(member_paths["repair/directed_repair_contract.json"])
    validate_repair_contract(repair)
    if prompt.get("gold_available_to_subject") is not False or prompt.get("gold_available_to_provider") is not False:
        raise ValueError("PROMPT_GOLD_VISIBILITY_INVALID")

    form_receipts: dict[str, Any] = {}
    evidence_text_hashes: dict[str, set[str]] = {}
    for form_name in ("A", "B"):
        form = load_json(member_paths[f"forms/form_{form_name}.json"])
        gold = load_json(member_paths[f"gold/form_{form_name}_gold.json"])
        form_errors = list(Draft202012Validator(form_schema).iter_errors(form))
        gold_errors = list(Draft202012Validator(gold_schema).iter_errors(gold))
        if form_errors or gold_errors:
            raise ValueError(f"FORM_OR_GOLD_SCHEMA_INVALID:{form_name}:{len(form_errors)}:{len(gold_errors)}")
        case_ids = [case["case_id"] for case in form["cases"]]
        gold_ids = [case["case_id"] for case in gold["cases"]]
        if case_ids != gold_ids or len(case_ids) != len(set(case_ids)):
            raise ValueError(f"FORM_GOLD_IDENTITY_MISMATCH:{form_name}")
        family_counts = Counter(item["issue_family"] if item["issue_family"] != "none" else "clean_control" for item in gold["cases"])
        if set(family_counts.values()) != {12} or len(family_counts) != 6:
            raise ValueError(f"FAMILY_QUOTA_INVALID:{form_name}:{family_counts}")
        variant_counts = Counter((item["issue_family"], item["variant"]) for item in gold["cases"])
        if len(variant_counts) != 72 or any(count != 1 for count in variant_counts.values()):
            raise ValueError(f"STRUCTURAL_VARIANTS_NOT_UNIQUE:{form_name}")
        evidence_count = sum(len(case["evidence_units"]) for case in form["cases"])
        if evidence_count != 720:
            raise ValueError(f"EVIDENCE_UNIT_COUNT_INVALID:{form_name}:{evidence_count}")
        form_serialized = json.dumps(form, ensure_ascii=False).lower()
        for forbidden_key in ("issue_family", "expected_verdict", "severity", "difficulty", "variant", "accepted_target_pointers"):
            if f'"{forbidden_key}"' in form_serialized:
                raise ValueError(f"FORM_LEAKS_GOLD_FIELD:{form_name}:{forbidden_key}")
        perfect = perfect_response(gold)
        output_errors = list(Draft202012Validator(output_schema).iter_errors(perfect))
        if output_errors:
            raise ValueError(f"PERFECT_OUTPUT_SCHEMA_INVALID:{form_name}:{len(output_errors)}")
        audit = audit_response(form=form, response=perfect, schema=output_schema)
        if audit["status"] != "PASS":
            raise ValueError(f"PERFECT_OUTPUT_AUDIT_INVALID:{form_name}:{audit['status']}")
        score = score_form(
            form=form,
            final_response=perfect,
            gold=gold,
            schema=output_schema,
            scoring_protocol=scoring,
        )
        if score["score"] != 100.0 or score["hard_gate_verdict"] != "PASS":
            raise ValueError(f"PERFECT_SCORE_REPLAY_FAILED:{form_name}:{score['score']}")
        hashes = {
            hashlib.sha256(unit["text"].encode("utf-8")).hexdigest()
            for case in form["cases"]
            for unit in case["evidence_units"]
        }
        evidence_text_hashes[form_name] = hashes
        form_receipts[form_name] = {
            "cases": len(case_ids),
            "evidence_units": evidence_count,
            "family_counts": dict(sorted(family_counts.items())),
            "unique_variants": len(variant_counts),
            "perfect_score": score["score"],
            "hard_gate_verdict": score["hard_gate_verdict"],
        }
    overlap = evidence_text_hashes["A"] & evidence_text_hashes["B"]
    if overlap:
        raise ValueError(f"FORM_EVIDENCE_TEXT_HASH_OVERLAP:{len(overlap)}")
    return {
        "status": "PASS",
        "candidate_only": True,
        "declassification_authorized": False,
        "member_count": len(members),
        "filesystem_file_count": len(observed_files),
        "forms": form_receipts,
        "form_evidence_text_hash_overlap": 0,
        "gold_sent_to_subject": False,
        "gold_sent_to_provider": False,
        "provider_api_calls": 0,
        "manifest_sha256": sha256_file(manifest_path),
        "checksums_sha256": sha256_file(checksum_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify_candidate_pack(args.pack.resolve()), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
