"""Reading a stored workflow exam score catalog of revision 4 to 7 (validate_workflow_exam_catalog).

The catalogs are made here: an API card reviewer (from revision 7 on, scored over three successor
samples), a command-line analysis model with a legacy comparison scope and no operational assessment,
and a local embedding model that may be compared within its cohort. Revision 7 adds the cache fields,
the self-hashes and one historical entry. Each hash is computed as the writer did. The tests check that
each revision is read back as stored, and that each kind of damage to a revision 7 catalog is refused
with its reason.
"""
from __future__ import annotations

import copy

import pytest

from memorive.exams.runner.catalog_validation import validate_workflow_exam_catalog
from memorive.exams.runner.metadata import WORKFLOW_EXAM_REPAIR_METADATA_FIELDS_V6
from memorive.models.endpoints import json_sha256

SERIES_METHOD = "MEAN_FIRST_PASS_NONLINEAR_ROLE_ABILITY_ACROSS_THREE_DISJOINT_SLOTS"
SUCCESSOR_FIELDS = ("repair_prompt_projection_revision", "pointer_normalization_revision",
                    "subject_transport_projection_revision")


def sha(letter):
    return letter * 64


def mean(values):
    return round(sum(values) / len(values), 9)


def reviewer_slot(number, score, *, operational="SUITABLE", closure="DELTA_PASS", calibration=0):
    return {
        "sample_slot": number, "sample_manifest_sha256": sha("ABC"[number - 1]), "score_exact": score,
        "verdict": "PASS", "qualification_verdict": "PASS", "operational_eligibility_verdict": operational,
        "operational_safety_gate": "PASS", "safety_blocking_residual_count": 0,
        "calibration_residual_count": calibration, "core_closure_verdict": closure,
        "core_closure_score": score - 2.0, "failed_exam_item_count": number - 1, "semantic_repair_rounds": 1,
        "exact_case_repair_rate": 0.5, "case_score_deficit_recovery_rate": 0.25 * number,
        "repair_metadata_collision_suspect_count": number % 2, "collision_adjusted_repair_diagnostic_rate": 0.75,
        "evidence_sha256": sha("789"[number - 1]), "comparison_binding_sha256": sha("F"),
    }


def comparison(row, successor_series):
    """The comparison binding hash and cohort of an entry that may be compared within its cohort."""
    binding = {key: row[key] for key in (
        "exam_category_id", "reference_pack_id", "reference_pack_revision", "reference_pack_sha256", "role_id",
        "score_semantics", "scoring_projection_revision", "scoring_protocol_revision", "scoring_protocol_sha256")}
    if successor_series:
        binding.update({key: row[key] for key in ("executor_ref", "sample_revision") + SUCCESSOR_FIELDS})
        binding["sample_manifest_sha256s"] = [
            slot["sample_manifest_sha256"] for slot in sorted(row["series_slots"], key=lambda s: s["sample_slot"])]
    digest = json_sha256(binding)
    row["comparison_binding_sha256"] = digest
    row["comparison_cohort_id"] = f"{row['role_id']}:SERIES:{digest}" if successor_series else f"{row['role_id']}:{digest}"


def base(node_id, role_id, identity, *, score_exact, verdict="PASS", eligible=True):
    row = {
        "node_id": node_id, "model_name": "model-" + node_id, "verdict": verdict,
        "score": int(round(score_exact)), "evidence_sha256": sha("B"), "score_exact": score_exact,
        "score_semantics": "QUALITY_SCORE", "qualification_verdict": "PASS",
        "exam_category_id": "CATEGORY_" + role_id, "role_id": role_id,
        "reference_pack_id": "PACK", "reference_pack_revision": "R1", "reference_pack_sha256": sha("D"),
        "scoring_protocol_revision": "P1", "scoring_protocol_sha256": sha("E"),
        "scoring_projection_revision": "J1", "comparison_binding_sha256": None, "comparison_cohort_id": None,
        "horizontal_comparison_eligible": eligible, "horizontal_comparison_allowed_only_with_same_cohort": True,
        "cross_category_comparison_forbidden": True, "global_ranking_forbidden": True,
    }
    if not eligible:
        row.update(dict.fromkeys(("reference_pack_id", "reference_pack_revision", "reference_pack_sha256",
                                  "scoring_protocol_revision", "scoring_protocol_sha256")))
        row["comparison_cohort_id"] = "LEGACY_SELF:" + row["evidence_sha256"]
    row.update(identity)
    return row


def single_frozen(row):
    row.update({
        "cache_reuse_status": "STABLE_REUSE", "cache_provenance_kind": "SINGLE_FROZEN_EVIDENCE",
        "executor_ref": "executor", "sample_revision": "NOT_APPLICABLE",
        "series_aggregate_method": "SINGLE_FROZEN_EXAM", "core_closure_verdict": "NOT_ASSESSED",
        "core_closure_score": None, "semantic_repair_rounds": None, "failed_exam_item_count": None,
        "series_slots": [], **dict.fromkeys(SUCCESSOR_FIELDS, "NOT_APPLICABLE"),
    })


def make_entries(revision):
    reviewer = base("transport_review", "CARD_REVIEWER",
                    {"profile_kind": "API", "provider_family": "OPENAI_COMPATIBLE", "thinking": False,
                     "tier": "standard"}, score_exact=85.0)
    analysis = base("analysis", "ANALYSIS_PRIMARY",
                    {"profile_kind": "CLI", "adapter_id": "codex_cli", "thinking_mode": "high"},
                    score_exact=71.5, eligible=False)
    embedding = base("chunk_embedding", "EMBEDDING_TEXT",
                     {"profile_kind": "LOCAL", "endpoint_kind": "ollama", "model_digest": sha("2")},
                     score_exact=40.0, verdict="FAIL")
    if revision >= 5:
        reviewer.update({"operational_eligibility_verdict": "SUITABLE_WITH_CALIBRATION",
                         "operational_policy_revision": "OP1", "operational_safety_gate": "PASS",
                         "safety_blocking_residual_count": 0, "calibration_residual_count": 1})
        analysis.update({"operational_eligibility_verdict": "NOT_ASSESSED", "operational_policy_revision": "OP1",
                         "operational_safety_gate": "NOT_ASSESSED", "safety_blocking_residual_count": None,
                         "calibration_residual_count": None})
        embedding.update({"operational_eligibility_verdict": "NOT_RECOMMENDED", "operational_policy_revision": "OP1",
                          "operational_safety_gate": "FAIL", "safety_blocking_residual_count": 2,
                          "calibration_residual_count": 0})
    if revision >= 6:
        reviewer.update({"exact_case_repair_rate": 0.5, "case_score_deficit_recovery_rate": 0.5,
                         "repair_metadata_collision_suspect_count": 2,
                         "collision_adjusted_repair_diagnostic_rate": 0.75})
        for row in (analysis, embedding):
            row.update(dict.fromkeys(WORKFLOW_EXAM_REPAIR_METADATA_FIELDS_V6))
    if revision >= 7:
        slots = [reviewer_slot(1, 90.0), reviewer_slot(2, 85.5, operational="SUITABLE_WITH_CALIBRATION",
                                                       closure="PASS_WITH_CALIBRATION", calibration=1),
                 reviewer_slot(3, 79.5)]
        reviewer.update({
            "cache_reuse_status": "STABLE_REUSE", "cache_provenance_kind": "THREE_SLOT_SUCCESSOR_SERIES",
            "executor_ref": "executor", "sample_revision": "S1", "series_aggregate_method": SERIES_METHOD,
            "core_closure_verdict": "PASS_WITH_CALIBRATION",
            "core_closure_score": mean([s["core_closure_score"] for s in slots]),
            "semantic_repair_rounds": 3, "failed_exam_item_count": 3, "series_slots": slots,
            "score_exact": mean([s["score_exact"] for s in slots]),
            "case_score_deficit_recovery_rate": mean([s["case_score_deficit_recovery_rate"] for s in slots]),
            **dict.fromkeys(SUCCESSOR_FIELDS, "SUCCESSOR_R1"),
        })
        single_frozen(analysis)
        single_frozen(embedding)
    for row in (reviewer, embedding):
        comparison(row, successor_series=revision >= 7 and row is reviewer)
    return [reviewer, analysis, embedding]


def seal(catalog):
    """Compute every self-hash of a revision 7 catalog again, as the writer did."""
    for row in catalog["entries"]:
        row.pop("cache_binding_sha256", None)
        row["cache_binding_sha256"] = json_sha256(row)
    for row in catalog["historical_entries"]:
        row.pop("historical_record_sha256", None)
        row["historical_record_sha256"] = json_sha256(row)
    catalog.pop("catalog_sha256", None)
    catalog["catalog_sha256"] = json_sha256(catalog)
    return catalog


def make_catalog(revision=7):
    catalog = {"schema_version": f"WorkflowModelExamScoreCatalog-v{revision}", "entries": make_entries(revision)}
    if revision < 7:
        return catalog
    catalog["historical_entries"] = [{
        "cache_reuse_status": "HISTORICAL_ONLY",
        "historical_reason": "INCOMPATIBLE_CARD_REVIEWER_PRE_SUCCESSOR_SINGLE_RUN",
        "source_catalog_schema_version": "WorkflowModelExamScoreCatalog-v6",
        "source_entry": {"role_id": "CARD_REVIEWER", "score": 70, "evidence_sha256": sha("6")},
    }]
    return seal(catalog)


@pytest.mark.parametrize("revision", [4, 5, 6, 7])
def test_each_revision_reads_back_as_stored(revision):
    catalog = make_catalog(revision)
    entries, historical = validate_workflow_exam_catalog(copy.deepcopy(catalog))
    assert list(entries) == catalog["entries"]
    assert list(historical) == catalog.get("historical_entries", [])


def damage_hash(catalog):
    catalog["entries"][2]["score"] = 41
    return catalog


def damage_binding(catalog):
    catalog["entries"][1]["executor_ref"] = "other"
    catalog.pop("catalog_sha256")
    catalog["catalog_sha256"] = json_sha256(catalog)
    return catalog


def damage_series_mean(catalog):
    catalog["entries"][0]["series_slots"][1]["score_exact"] = 86.5
    return seal(catalog)


def damage_series_closure(catalog):
    catalog["entries"][0]["series_slots"][1]["core_closure_verdict"] = "HUMAN_REQUIRED"
    return seal(catalog)


def damage_series_slot(catalog):
    catalog["entries"][0]["series_slots"][2]["collision_adjusted_repair_diagnostic_rate"] = 0.25
    return seal(catalog)


def damage_series_method(catalog):
    catalog["entries"][0]["series_aggregate_method"] = "SINGLE_FROZEN_EXAM"
    return seal(catalog)


def damage_single_cache(catalog):
    catalog["entries"][2]["semantic_repair_rounds"] = 0
    return seal(catalog)


def damage_cache_fields(catalog):
    catalog["entries"][2]["core_closure_verdict"] = "MAYBE"
    return seal(catalog)


def damage_comparison(catalog):
    catalog["entries"][2]["reference_pack_revision"] = "R2"
    return seal(catalog)


def damage_legacy_scope(catalog):
    catalog["entries"][1]["comparison_cohort_id"] = "LEGACY_SELF:" + sha("C")
    return seal(catalog)


def damage_operational(catalog):
    catalog["entries"][1]["safety_blocking_residual_count"] = 0
    return seal(catalog)


def damage_repair(catalog):
    catalog["entries"][2]["exact_case_repair_rate"] = 0.5
    return seal(catalog)


def damage_score(catalog):
    catalog["entries"][1]["score_exact"] = 73.0
    return seal(catalog)


def damage_role(catalog):
    catalog["entries"][1]["node_id"] = "judgment_review"
    return seal(catalog)


def damage_identity(catalog):
    catalog["entries"][0]["provider_family"] = "DEEPSEEK"
    return seal(catalog)


def damage_duplicate(catalog):
    catalog["entries"].append(copy.deepcopy(catalog["entries"][1]))
    return seal(catalog)


def damage_historical(catalog):
    catalog["historical_entries"][0]["source_catalog_schema_version"] = "WorkflowModelExamScoreCatalog-v5"
    return seal(catalog)


def damage_shape(catalog):
    catalog["note"] = "x"
    return catalog


@pytest.mark.parametrize("damage, reason", [
    (damage_hash, "CATALOG_HASH_INVALID"),
    (damage_binding, "CATALOG_CACHE_BINDING_INVALID"),
    (damage_series_mean, "CATALOG_SERIES_AGGREGATE_INVALID"),
    (damage_series_closure, "CATALOG_SERIES_AGGREGATE_INVALID"),
    (damage_series_slot, "CATALOG_SERIES_SLOT_INVALID"),
    (damage_series_method, "CATALOG_SERIES_INCOMPLETE"),
    (damage_single_cache, "CATALOG_SINGLE_CACHE_INVALID"),
    (damage_cache_fields, "CATALOG_CACHE_METADATA_INVALID"),
    (damage_comparison, "CATALOG_COMPARISON_BINDING_INVALID"),
    (damage_legacy_scope, "CATALOG_LEGACY_SCOPE_INVALID"),
    (damage_operational, "CATALOG_OPERATIONAL_METADATA_INVALID"),
    (damage_repair, "CATALOG_REPAIR_METADATA_INVALID"),
    (damage_score, "CATALOG_SCORE_METADATA_INVALID"),
    (damage_role, "CATALOG_ROLE_NODE_MISMATCH"),
    (damage_identity, "CATALOG_ENTRY_INVALID"),
    (damage_duplicate, "CATALOG_DUPLICATE"),
    (damage_historical, "HISTORICAL_ENTRY_INVALID"),
    (damage_shape, "CATALOG_V7_SHAPE_INVALID"),
])
def test_damage_to_revision_7_is_refused_with_its_reason(damage, reason):
    with pytest.raises(ValueError, match=f"^WORKFLOW_MODEL_EXAM_{reason}$"):
        validate_workflow_exam_catalog(damage(make_catalog()))
