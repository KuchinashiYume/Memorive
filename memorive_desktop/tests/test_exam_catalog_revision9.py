"""Reading a stored revision 9 workflow exam score catalog (validate_workflow_exam_catalog).

The catalog is made here: an API card reviewer scored over three successor samples, a command-line
analysis model from a frozen cohort with a compact panel of three runs, and a local embedding model from
a complete reference cohort, plus one historical entry. Each self-hash is computed as the writer did.
The tests check that such a catalog is read back as stored, and that each kind of damage is refused
with its reason.
"""
from __future__ import annotations

import copy

import pytest

from memorive.exams.runner.catalog_validation import validate_workflow_exam_catalog
from memorive.models.endpoints import json_sha256

COUNTS = ("failed_exam_item_count", "safety_blocking_residual_count", "calibration_residual_count",
          "semantic_repair_rounds", "physical_model_calls")


def sha(letter):
    return letter * 64


def entry(node_id, role_id, provenance, aggregate, identity, *, score_exact, verdict="PASS",
          quality_verdict="PASS", counts=(0, 0, 0, 1, 6), evidence="B", samples="C"):
    row = {
        "node_id": node_id, "model_name": "model-" + node_id, "score_semantics": "QUALITY_SCORE",
        "exam_category_id": "CATEGORY_" + role_id, "role_id": role_id,
        "reference_pack_id": "PACK", "reference_pack_revision": "R1", "scoring_protocol_revision": "P1",
        "scoring_projection_revision": "J1", "comparison_cohort_id": "COHORT", "executor_ref": "executor",
        "sample_revision": "S1", "terminal_disposition": "SCORED", "executor_binding_kind": "BUILTIN",
        "series_aggregate_method": aggregate,
        "evidence_sha256": sha(evidence), "reference_pack_sha256": sha("D"), "scoring_protocol_sha256": sha("E"),
        "comparison_binding_sha256": sha("F"), "executor_binding_sha256": sha("1"),
        "sample_manifest_sha256": sha(samples),
        "cache_reuse_status": "STABLE_REUSE", "cache_provenance_kind": provenance,
        "execution_outcome": "COMPLETED", "scorability": "SCOREABLE", "verdict": verdict,
        "quality_verdict": quality_verdict, "score": int(round(score_exact)), "score_exact": score_exact,
        "qualification_verdict": "PASS", "operational_eligibility_verdict": "SUITABLE",
        "operational_safety_gate": "PASS", "category_score_is_descriptive": quality_verdict == "NOT_ASSESSED",
        "horizontal_comparison_allowed_only_with_same_cohort": True,
        "cross_category_comparison_forbidden": True, "global_ranking_forbidden": True,
        "provider_received_gold": False, "local_reference_pack_embedded": False,
        "token_usage": {"prompt_tokens": 1000, "completion_tokens": 200},
        "series_slots": [],
    }
    row.update(zip(COUNTS, counts))
    row.update(identity)
    return row


def successor_slot(number, score, verdict="PASS"):
    return {
        "sample_slot": number, "sample_manifest_sha256": sha("ABC"[number - 1]), "score_exact": score,
        "verdict": verdict, "quality_verdict": verdict, "operational_eligibility_verdict": "SUITABLE",
        "operational_safety_gate": "PASS", "terminal_disposition": "SCORED",
        "failed_exam_item_count": 0, "safety_blocking_residual_count": 0, "calibration_residual_count": 0,
        "semantic_repair_rounds": 0, "physical_model_calls": 2,
        "evidence_sha256": sha("789"[number - 1]), "comparison_binding_sha256": sha("F"),
        "token_usage": {"prompt_tokens": 300, "completion_tokens": 60}, "estimated_cost_cny": 0.01,
    }


def panel_slot(number, score, *, calls=3, repairs=0):
    return {
        "sample_slot": number, "score_exact": score, "first_pass_score_exact": score - 1.0,
        "physical_model_calls": calls, "semantic_repair_rounds": repairs,
        "exam_evidence_sha256": sha("456"[number - 1]), "exam_run_id": f"run-{number}",
    }


def model_identity(row):
    kind = row["profile_kind"]
    fields = {"API": ("provider_family", "model_name", "thinking", "tier", "api_platform", "base_url"),
              "CLI": ("adapter_id", "model_name", "thinking_mode"),
              "LOCAL": ("endpoint_kind", "model_name", "model_digest")}[kind]
    return {"node_id": row["node_id"], "profile_kind": kind, **{field: row[field] for field in fields}}


def seal(catalog):
    """Compute every self-hash again, as the writer did: bindings, then the catalog."""
    for row in catalog["entries"]:
        binding = {
            "schema_version": "WorkflowModelExamCacheBinding-v2",
            "category_binding": {
                "category_id": row["exam_category_id"], "role_id": row["role_id"],
                **{key: row[key] for key in (
                    "reference_pack_id", "reference_pack_revision", "reference_pack_sha256",
                    "scoring_protocol_revision", "scoring_protocol_sha256", "scoring_projection_revision",
                    "executor_ref", "sample_revision", "sample_manifest_sha256")},
            },
            "model_identity": model_identity(row),
            "executor_binding_sha256": row["executor_binding_sha256"],
            "executor_binding_kind": row["executor_binding_kind"],
            "evidence_set_sha256": row["evidence_sha256"],
            "cache_provenance_kind": row["cache_provenance_kind"],
            "outcome_contract_revision": "Desktop_EXAM_OUTCOME_CONTRACT_V1",
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
        }
        binding["cache_binding_sha256"] = json_sha256(binding)
        row["cache_binding"] = binding
        row["cache_binding_sha256"] = binding["cache_binding_sha256"]
    for row in catalog["historical_entries"]:
        row.pop("historical_record_sha256", None)
        row["historical_record_sha256"] = json_sha256(row)
    catalog.pop("catalog_sha256", None)
    catalog["catalog_sha256"] = json_sha256(catalog)
    return catalog


def make_catalog():
    reviewer = entry(
        "transport_review", "CARD_REVIEWER", "THREE_SLOT_SUCCESSOR_SERIES", "MEAN_OF_THREE",
        {"profile_kind": "API", "provider_family": "deepseek", "thinking": False, "tier": "standard",
         "api_platform": "deepseek", "base_url": "https://api.deepseek.com"},
        score_exact=round((90.0 + 80.0 + 70.5) / 3.0, 6), counts=(0, 0, 0, 0, 6),
    )
    reviewer["series_slots"] = [successor_slot(1, 90.0), successor_slot(2, 80.0), successor_slot(3, 70.5)]
    reviewer["sample_manifest_sha256"] = json_sha256([sha("A"), sha("B"), sha("C")])
    reviewer["evidence_sha256"] = json_sha256([sha("7"), sha("8"), sha("9")])
    analysis = entry(
        "analysis", "ANALYSIS_PRIMARY", "FROZEN_SUCCESSOR_COHORT", "FROZEN_SUCCESSOR_COHORT",
        {"profile_kind": "CLI", "adapter_id": "codex_cli", "thinking_mode": "high"},
        score_exact=81.25, counts=(1, 0, 0, 2, 12),
    )
    analysis["series_slots"] = [panel_slot(1, 80.0), panel_slot(2, 84.5, repairs=2), panel_slot(3, 79.0)]
    analysis["cohort_panel_count"] = 3
    analysis["cohort_panel_score_range"] = 5.5
    embedding = entry(
        "chunk_embedding", "EMBEDDING_TEXT", "COMPLETE_REFERENCE_COHORT", "COMPLETE_REFERENCE_COHORT",
        {"profile_kind": "LOCAL", "endpoint_kind": "ollama", "model_digest": sha("2")},
        score_exact=66.4, verdict="FAIL", quality_verdict="NOT_ASSESSED",
    )
    historical = {
        "cache_reuse_status": "HISTORICAL_ONLY", "historical_reason": "SUPERSEDED_BY_REVISION_9",
        "source_catalog_schema_version": "WorkflowModelExamScoreCatalog-v8",
        "source_entry": {"node_id": "analysis", "score": 70},
    }
    return seal({"schema_version": "WorkflowModelExamScoreCatalog-v9",
                 "entries": [reviewer, analysis, embedding], "historical_entries": [historical]})


def test_a_revision_9_catalog_reads_back_as_stored():
    catalog = make_catalog()
    entries, historical = validate_workflow_exam_catalog(copy.deepcopy(catalog))
    assert list(entries) == catalog["entries"]
    assert list(historical) == catalog["historical_entries"]


def test_revision_8_has_no_frozen_cohort():
    catalog = make_catalog()
    catalog["schema_version"] = "WorkflowModelExamScoreCatalog-v8"
    with pytest.raises(ValueError, match="^WORKFLOW_MODEL_EXAM_CATALOG_V8_ENTRY_INVALID$"):
        validate_workflow_exam_catalog(seal(catalog))


def damage_hash(catalog):
    catalog["entries"][2]["score"] = 67
    return catalog


def damage_series_mean(catalog):
    catalog["entries"][0]["series_slots"][2]["score_exact"] = 71.0
    return seal(catalog)


def damage_series_counts(catalog):
    catalog["entries"][0]["series_slots"][1]["semantic_repair_rounds"] = 1
    return seal(catalog)


def damage_panel_range(catalog):
    catalog["entries"][1]["cohort_panel_score_range"] = 4.0
    return seal(catalog)


def damage_panel_slot(catalog):
    catalog["entries"][1]["series_slots"][0]["exam_run_id"] = "run 1"
    return seal(catalog)


def damage_reference_cohort(catalog):
    catalog["entries"][2]["series_slots"] = [panel_slot(1, 66.4)]
    return seal(catalog)


def damage_duplicate(catalog):
    catalog["entries"].append(copy.deepcopy(catalog["entries"][2]))
    return seal(catalog)


def damage_route(catalog):
    catalog["entries"][0]["base_url"] = "http://api.deepseek.com"
    return seal(catalog)


def damage_role(catalog):
    catalog["entries"][2]["role_id"] = "RERANKER_TEXT"
    return seal(catalog)


def damage_descriptive(catalog):
    catalog["entries"][2]["category_score_is_descriptive"] = False
    return seal(catalog)


def damage_usage(catalog):
    catalog["entries"][1]["token_usage"] = {"prompt_tokens": 1, "completion_tokens": -1}
    return seal(catalog)


def damage_binding(catalog):
    catalog = seal(catalog)
    binding = catalog["entries"][1]["cache_binding"]
    binding["model_identity"]["thinking_mode"] = "low"
    binding["cache_binding_sha256"] = json_sha256({k: v for k, v in binding.items() if k != "cache_binding_sha256"})
    catalog["entries"][1]["cache_binding_sha256"] = binding["cache_binding_sha256"]
    catalog.pop("catalog_sha256")
    catalog["catalog_sha256"] = json_sha256(catalog)
    return catalog


def damage_historical(catalog):
    catalog["historical_entries"][0]["cache_reuse_status"] = "STABLE_REUSE"
    catalog.pop("catalog_sha256")
    catalog["catalog_sha256"] = json_sha256(catalog)
    return catalog


@pytest.mark.parametrize("damage, reason", [
    (damage_hash, "CATALOG_HASH_INVALID"),
    (damage_series_mean, "CATALOG_V8_SERIES_INVALID"),
    (damage_series_counts, "CATALOG_V8_SERIES_INVALID"),
    (damage_panel_range, "CATALOG_V9_COMPACT_PANEL_SERIES_INVALID"),
    (damage_panel_slot, "CATALOG_V9_COMPACT_PANEL_SLOT_INVALID"),
    (damage_reference_cohort, "CATALOG_V8_COHORT_INVALID"),
    (damage_duplicate, "CATALOG_DUPLICATE_IDENTITY"),
    (damage_route, "CATALOG_V9_API_ROUTE_IDENTITY_INVALID"),
    (damage_role, "CATALOG_ROLE_NODE_MISMATCH"),
    (damage_descriptive, "CATALOG_V8_DESCRIPTIVE_INVALID"),
    (damage_usage, "CATALOG_V8_USAGE_INVALID"),
    (damage_binding, "CATALOG_V8_BINDING_INVALID"),
    (damage_historical, "HISTORICAL_ENTRY_INVALID"),
])
def test_damage_is_refused_with_its_reason(damage, reason):
    with pytest.raises(ValueError, match=f"^WORKFLOW_MODEL_EXAM_{reason}$"):
        validate_workflow_exam_catalog(damage(make_catalog()))
