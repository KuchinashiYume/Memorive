"""Deterministic low-cost rehearsal-pack builder for card-reviewer exams.

The canonical T-namespace reference pack is never generated at exam time.
This module is retained only to create the disjoint R-namespace rehearsal
pack required by the low-cost API gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "model_evaluation-card-reviewer-reference-exam-pack-candidate-v1"
FORM_SCHEMA_VERSION = "model_evaluation-card-reviewer-exam-form-v2"
GOLD_SCHEMA_VERSION = "model_evaluation-card-reviewer-exam-gold-v2"
OUTPUT_SCHEMA_VERSION = "model_evaluation-card-reviewer-exam-output-v2"
FAMILIES = (
    "numeric_direction",
    "population_mismatch",
    "boundary_omission",
    "causal_overclaim",
    "stale_lineage",
    "clean_control",
)
DEFECT_FAMILIES = FAMILIES[:-1]
VARIANT_COUNT = 12
IDENTITY_NAMESPACES = ("R", "T")


NUMERIC_VARIANTS = (
    "sign_reversal",
    "percentage_point_vs_percent",
    "denominator_swap",
    "mean_vs_median",
    "range_endpoint_shift",
    "unit_scale_error",
    "sample_size_mismatch",
    "time_window_value_swap",
    "fold_change_vs_absolute",
    "confidence_interval_direction",
    "subgroup_aggregate_swap",
    "decimal_shift",
)
POPULATION_VARIANTS = (
    "treatment_control_swap",
    "species_strain_swap",
    "inclusion_cohort_swap",
    "phase_sample_swap",
    "age_subgroup_swap",
    "exposure_subgroup_swap",
    "site_batch_swap",
    "comparator_baseline_swap",
    "biological_technical_replicate_swap",
    "responder_nonresponder_swap",
    "device_modality_swap",
    "pooled_single_population_swap",
)
BOUNDARY_VARIANTS = (
    "temperature_omission",
    "time_window_omission",
    "dose_range_omission",
    "pretreatment_only_omission",
    "control_absence_omission",
    "exclusion_condition_omission",
    "detection_limit_omission",
    "phase_specific_omission",
    "oxygen_regime_omission",
    "laboratory_scale_omission",
    "subset_only_omission",
    "follow_up_duration_omission",
)
CAUSAL_VARIANTS = (
    "association_to_cause",
    "suggestion_to_proof",
    "prediction_to_therapy",
    "temporal_to_causal",
    "uncertain_mediation_to_mechanism",
    "observational_to_intervention",
    "nonrandomized_to_effect",
    "hypothesis_to_established_pathway",
    "correlation_to_elimination",
    "risk_factor_to_determinant",
    "exploratory_to_definitive",
    "secondary_citation_to_direct_proof",
)
LINEAGE_VARIANTS = (
    "source_version_stale",
    "producer_mismatch",
    "method_revision_stale",
    "card_revision_precedes_evidence",
    "mixed_paper_anchor",
    "superseded_field_projection",
    "profile_lineage_mismatch",
    "cross_run_splice",
    "retired_artifact",
    "parent_hash_mismatch",
    "field_level_source_stale",
    "timestamp_before_correction",
)
CLEAN_VARIANTS = (
    "faithful_paraphrase",
    "equivalent_unit_conversion",
    "allowed_rounding",
    "legitimate_normalization",
    "conservative_wording",
    "stable_population_alias",
    "boundary_preserved_cross_field",
    "current_lineage",
    "style_only_difference",
    "multiple_valid_anchors",
    "order_only_change",
    "faithful_negative_result",
)


FORM_CONTEXTS = {
    "A": (
        ("anaerobic reactor", "acetate-fed consortium", "methane yield", "mL CH4/g VS"),
        ("soil mesocosm", "drought-conditioned maize", "root biomass", "g dry mass"),
        ("cell culture assay", "hypoxia-exposed epithelial cells", "viability", "%"),
        ("clinical cohort", "stage-II recovery patients", "relapse rate", "%"),
        ("photobioreactor", "nitrogen-limited algae", "lipid productivity", "mg/L/day"),
        ("battery cycling study", "coated cathode cells", "capacity retention", "%"),
        ("microfluidic assay", "shear-conditioned biofilm", "detachment rate", "1/hour"),
        ("field plot", "late-sown wheat", "grain yield", "kg/ha"),
        ("enzyme screen", "mutant beta hydrolase", "specific activity", "U/mg"),
        ("ecological survey", "urban wetland plots", "species richness", "count"),
        ("sensor validation", "humid-air calibration samples", "absolute error", "ppm"),
        ("fermentation trial", "salt-adapted starter culture", "acidification time", "hour"),
    ),
    "B": (
        ("aerobic bioreactor", "glucose-fed consortium", "oxygen uptake", "mmol/L/hour"),
        ("forest microcosm", "warming-conditioned seedlings", "carbon uptake", "mg/day"),
        ("organoid assay", "drug-exposed intestinal organoids", "barrier integrity", "%"),
        ("prospective registry", "postoperative surveillance patients", "readmission rate", "%"),
        ("raceway pond", "phosphate-limited cyanobacteria", "biomass productivity", "g/evidence_extraction/day"),
        ("supercapacitor study", "doped carbon electrodes", "capacitance retention", "%"),
        ("flow-cell assay", "pressure-conditioned membrane biofilm", "permeability loss", "%"),
        ("greenhouse trial", "salinity-treated tomato", "fruit mass", "g/plant"),
        ("catalyst screen", "alloyed nickel catalyst", "turnover frequency", "1/s"),
        ("marine survey", "sheltered reef quadrats", "juvenile density", "count/evidence_extraction"),
        ("imaging validation", "low-light phantom samples", "localization error", "mm"),
        ("starter trial", "cold-adapted lactic culture", "coagulation time", "minute"),
    ),
}


SEVERITIES = (
    "critical", "major", "major", "major", "critical", "major",
    "major", "minor", "critical", "major", "minor", "critical",
)
DIFFICULTIES = (
    "medium", "medium", "hard", "hard", "hard", "hard",
    "adversarial", "adversarial", "adversarial", "adversarial", "hard", "adversarial",
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def apply_identity_namespace(value: Any, namespace: str) -> Any:
    """Create disjoint rehearsal/target identities without changing task semantics."""
    if namespace not in IDENTITY_NAMESPACES:
        raise ValueError(f"IDENTITY_NAMESPACE_INVALID:{namespace}")
    if isinstance(value, list):
        return [apply_identity_namespace(item, namespace) for item in value]
    if isinstance(value, dict):
        mapped = {key: apply_identity_namespace(item, namespace) for key, item in value.items()}
        lineage = mapped.get("lineage_context")
        if isinstance(lineage, dict) and "authoritative_parent_source_sha256" in lineage:
            source_version = lineage.get("authoritative_source_version")
            if isinstance(source_version, str):
                lineage["authoritative_parent_source_sha256"] = sha256_bytes(source_version.encode("utf-8"))
        return mapped
    if not isinstance(value, str):
        return value
    replacements = (
        ("CRX-A-", f"CRX-{namespace}-A-"),
        ("CRX-B-", f"CRX-{namespace}-B-"),
        ("SYN-A-", f"SYN-{namespace}-A-"),
        ("SYN-B-", f"SYN-{namespace}-B-"),
        ("CARD-REVIEWER-REFERENCE-A-V1", f"CARD-REVIEWER-REFERENCE-{namespace}-A-V1"),
        ("CARD-REVIEWER-REFERENCE-B-V1", f"CARD-REVIEWER-REFERENCE-{namespace}-B-V1"),
        ("a-src-", f"{namespace.lower()}-a-src-"),
        ("b-src-", f"{namespace.lower()}-b-src-"),
        ("a-lineage-run-", f"{namespace.lower()}-a-lineage-run-"),
        ("b-lineage-run-", f"{namespace.lower()}-b-lineage-run-"),
        ("RETIRED-ARTIFACT", f"{namespace}-RETIRED-ARTIFACT"),
        ("unrelated-run-splice", f"{namespace.lower()}-unrelated-run-splice"),
    )
    mapped = value
    for source, target in replacements:
        mapped = mapped.replace(source, target)
    return mapped


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with path.open("xb") as handle:
        handle.write(raw)


def evidence_positions(family_index: int, variant_index: int) -> tuple[int, int]:
    first = (family_index * 2 + variant_index * 3) % 10
    second = (first + 4 + variant_index % 3) % 10
    if second == first:
        second = (second + 1) % 10
    return first, second


def make_evidence(
    *,
    form: str,
    case_id: str,
    family_index: int,
    variant_index: int,
    relevant_texts: tuple[str, str],
    source_version: str,
    paper_id: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    positions = evidence_positions(family_index, variant_index)
    contexts = FORM_CONTEXTS[form]
    units: list[dict[str, Any]] = []
    required_refs: list[str] = []
    for position in range(10):
        evidence_id = f"{case_id}-E{position + 1:02d}"
        if position == positions[0]:
            text = relevant_texts[0]
            required_refs.append(evidence_id)
            section = "Results"
        elif position == positions[1]:
            text = relevant_texts[1]
            required_refs.append(evidence_id)
            section = "Methods and limitations"
        else:
            other = contexts[(variant_index + position + family_index) % len(contexts)]
            text = (
                f"Synthetic distractor {position + 1}: the {other[0]} tracked {other[2]} in "
                f"{other[1]}, but this observation concerns a separate endpoint and does not "
                f"change the focal comparison. Quality-control replicate Q{variant_index + 1}-{position + 1} remained within tolerance."
            )
            section = ("Methods", "Results", "Supplement", "Discussion")[position % 4]
        text = f"{case_id} synthetic evidence record: {text}"
        units.append(
            {
                "evidence_id": evidence_id,
                "paper_id": paper_id,
                "chunk_id": f"{paper_id}#c{position + 1:04d}",
                "section": section,
                "source_version": source_version,
                "producer": "synthetic-card-source-builder-v2",
                "text": text,
            }
        )
    return units, required_refs


def base_card(
    *,
    paper_id: str,
    source_version: str,
    context: tuple[str, str, str, str],
    relevant_refs: list[str],
) -> dict[str, Any]:
    setting, population, metric, unit = context
    def anchors() -> list[dict[str, str]]:
        return [
            {
                "evidence_id": ref,
                "source_version": source_version,
                "producer": "synthetic-card-source-builder-v2",
            }
            for ref in relevant_refs
        ]
    return {
        "paper_id": paper_id,
        "card_version": "card-v6-synthetic",
        "source_version": source_version,
        "producer_profile": "card-distiller-primary-synthetic-v2",
        "research_question": f"How does the focal condition affect {metric} in the {setting}?",
        "research_object": population,
        "method": [f"The {setting} comparison used a prespecified matched-control protocol."],
        "key_results": [f"The focal group showed a bounded change in {metric} ({unit})."],
        "author_conclusion": "The evidence supports a limited association under the reported conditions.",
        "boundary_conditions": ["Interpretation is restricted to the reported population, comparator, and observation window."],
        "key_data": [
            {
                "metric": metric,
                "value": "12.0",
                "unit": unit,
                "sample": f"sample/control: {population} / matched control | phase/time: reported window",
                "stat": "stat_type: mean | n: 24 | significance: exploratory",
            }
        ],
        "source_anchor": {
            "by_field": {
                "research_question": anchors(),
                "research_object": anchors(),
                "method": anchors(),
                "key_results": anchors(),
                "author_conclusion": anchors(),
                "boundary_conditions": anchors(),
            }
        },
    }


def numeric_case(form: str, idx: int, case_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    setting, population, metric, unit = FORM_CONTEXTS[form][idx]
    source_version = f"{form.lower()}-src-{idx + 1:02d}-v4"
    paper_id = f"SYN-{form}-NUM-{idx + 1:02d}"
    true_value = 18 + idx * 3
    wrong_value = true_value + (7 if idx % 2 == 0 else -6)
    variants = (
        (f"The focal arm decreased {metric} by {true_value}% relative to control.", f"The signed contrast was -{true_value}% and n=24.", f"The focal arm increased {metric} by {true_value}% relative to control.", "/candidate_card/key_results/0"),
        (f"The rate moved from {true_value}% to {true_value + 8}%, an 8 percentage-point increase.", "The authors did not report an eight-percent relative change.", "The rate increased by 8 percent relative to baseline.", "/candidate_card/key_results/0"),
        (f"{metric} was {true_value} per 100 treated units.", f"The control denominator was 1,000 units and cannot be substituted for the treated denominator.", f"{metric} was {true_value} per 1,000 treated units.", "/candidate_card/key_data/0/value"),
        (f"The median {metric} was {true_value} {unit}; the mean was not reported.", "The distribution was skewed and the protocol prespecified the median.", f"The mean {metric} was {true_value} {unit}.", "/candidate_card/key_data/0/stat"),
        (f"The observed interval was {true_value} to {true_value + 5} {unit}.", "No observation exceeded the upper endpoint.", f"The observed interval was {true_value} to {true_value + 15} {unit}.", "/candidate_card/key_results/0"),
        (f"The concentration was {true_value} mg/L.", f"This equals {true_value / 1000:.3f} g/L, not {true_value} g/L.", f"The concentration was {true_value} g/L.", "/candidate_card/key_data/0/unit"),
        (f"The analysis included n={true_value} biological samples.", "Technical repeats were not counted as independent samples.", f"The analysis included n={wrong_value} biological samples.", "/candidate_card/key_data/0/stat"),
        (f"During days 8-14, {metric} was {true_value} {unit}.", f"During days 1-7 the value was {wrong_value} {unit}.", f"During days 1-7, {metric} was {true_value} {unit}.", "/candidate_card/key_results/0"),
        (f"The focal value was {true_value} {unit}, an absolute difference of 4 {unit}.", "A fold change was not calculated.", f"The focal condition produced a {true_value}-fold change.", "/candidate_card/author_conclusion"),
        (f"The confidence interval for the contrast was -{true_value} to -{true_value - 5} {unit}.", "Both interval bounds were below zero.", f"The confidence interval was +{true_value - 5} to +{true_value} {unit}.", "/candidate_card/key_results/0"),
        (f"The responder subgroup reached {true_value} {unit}; the pooled cohort reached {wrong_value} {unit}.", "Subgroup and pooled estimates are reported separately.", f"The pooled cohort reached {true_value} {unit}.", "/candidate_card/key_results/0"),
        (f"The calibrated value was {true_value / 10:.1f} {unit}.", "Raw instrument output was scaled by exactly 0.1.", f"The calibrated value was {true_value:.1f} {unit}.", "/candidate_card/key_data/0/value"),
    )
    source_a, source_b, candidate_text, target = variants[idx]
    units, refs = make_evidence(
        form=form, case_id=case_id, family_index=0, variant_index=idx,
        relevant_texts=(source_a, source_b), source_version=source_version, paper_id=paper_id,
    )
    card = base_card(paper_id=paper_id, source_version=source_version, context=(setting, population, metric, unit), relevant_refs=refs)
    if target.endswith("key_results/0"):
        card["key_results"][0] = candidate_text
    elif target.endswith("author_conclusion"):
        card["author_conclusion"] = candidate_text
    elif target.endswith("/unit"):
        card["key_data"][0]["unit"] = candidate_text.rsplit(" ", 1)[-1].rstrip(".")
    elif target.endswith("/stat"):
        card["key_data"][0]["stat"] = candidate_text
    else:
        card["key_data"][0]["value"] = candidate_text
    case = make_case(case_id, card, units, source_version)
    gold = make_gold(case_id, "numeric_direction", idx, target, "CONFLICT", refs, ("flag", "tombstone"))
    return case, gold


def population_case(form: str, idx: int, case_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    setting, population, metric, unit = FORM_CONTEXTS[form][idx]
    source_version = f"{form.lower()}-src-pop-{idx + 1:02d}-v5"
    paper_id = f"SYN-{form}-POP-{idx + 1:02d}"
    variants = (
        ("treated units", "matched untreated controls", "matched untreated controls", "/candidate_card/research_object"),
        ("strain AX-7", "related species AX", "species AX", "/candidate_card/research_object"),
        ("participants meeting both inclusion criteria", "all screened participants", "all screened participants", "/candidate_card/research_object"),
        ("recovery-phase focal samples", "pre-shock focal samples", "pre-shock focal samples", "/candidate_card/key_results/0"),
        ("participants aged 65 years or older", "participants younger than 40", "participants younger than 40", "/candidate_card/research_object"),
        ("high-exposure subgroup", "low-exposure subgroup", "low-exposure subgroup", "/candidate_card/author_conclusion"),
        ("site North, batch 3", "site South, batch 1", "site South, batch 1", "/candidate_card/method/0"),
        ("active comparator baseline", "historical untreated baseline", "historical untreated baseline", "/candidate_card/research_question"),
        ("24 biological samples", "72 technical replicate wells", "72 technical replicate wells", "/candidate_card/key_data/0/stat"),
        ("prespecified responder subgroup", "nonresponder subgroup", "nonresponder subgroup", "/candidate_card/author_conclusion"),
        ("device EVIDENCE_EXTRACTION imaging mode", "device DOCUMENT_PROCESSING screening mode", "device DOCUMENT_PROCESSING screening mode", "/candidate_card/method/0"),
        ("pooled population across four sites", "single-site convenience sample", "single-site convenience sample", "/candidate_card/research_object"),
    )
    focal, other, wrong, target = variants[idx]
    source_a = f"The reported {metric} estimate applies only to {focal}."
    source_b = f"{other.capitalize()} produced a separate estimate and was not interchangeable with the focal population."
    units, refs = make_evidence(
        form=form, case_id=case_id, family_index=1, variant_index=idx,
        relevant_texts=(source_a, source_b), source_version=source_version, paper_id=paper_id,
    )
    card = base_card(paper_id=paper_id, source_version=source_version, context=(setting, population, metric, unit), relevant_refs=refs)
    if target.endswith("research_object"):
        card["research_object"] = wrong
    elif target.endswith("key_results/0"):
        card["key_results"][0] = f"{wrong.capitalize()} showed the focal {metric} response."
    elif target.endswith("author_conclusion"):
        card["author_conclusion"] = f"The conclusion applies to {wrong}."
    elif target.endswith("method/0"):
        card["method"][0] = f"The focal comparison used {wrong}."
    elif target.endswith("research_question"):
        card["research_question"] = f"How did the intervention compare with {wrong}?"
    else:
        card["key_data"][0]["stat"] = f"stat_type: mean | n: {wrong}"
    case = make_case(case_id, card, units, source_version)
    gold = make_gold(case_id, "population_mismatch", idx, target, "CONFLICT", refs, ("flag", "tombstone"))
    return case, gold


def boundary_case(form: str, idx: int, case_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    setting, population, metric, unit = FORM_CONTEXTS[form][idx]
    source_version = f"{form.lower()}-src-bound-{idx + 1:02d}-v3"
    paper_id = f"SYN-{form}-BND-{idx + 1:02d}"
    boundaries = (
        "only at 30 degrees Celsius",
        "only during days 8-14",
        "only for doses between 2 and 5 mg/L",
        "only after alkaline pretreatment",
        "without an untreated control",
        "after excluding samples with baseline contamination",
        "only above the assay detection limit",
        "only during the recovery phase",
        "only under anaerobic conditions",
        "only in laboratory-scale vessels",
        "only in the prespecified responder subset",
        "only through the 14-day follow-up",
    )
    boundary = boundaries[idx]
    source_a = f"The observed {metric} difference was reported {boundary}."
    source_b = f"The authors explicitly warned that extrapolation beyond this boundary was not evaluated."
    units, refs = make_evidence(
        form=form, case_id=case_id, family_index=2, variant_index=idx,
        relevant_texts=(source_a, source_b), source_version=source_version, paper_id=paper_id,
    )
    card = base_card(paper_id=paper_id, source_version=source_version, context=(setting, population, metric, unit), relevant_refs=refs)
    target = "/candidate_card/boundary_conditions"
    if idx % 3 == 0:
        card["boundary_conditions"] = []
    elif idx % 3 == 1:
        card["boundary_conditions"] = ["The result applies generally to the focal system."]
    else:
        card["boundary_conditions"] = [f"Interpretation is restricted to results reported {boundary}."]
        target = "/candidate_card/author_conclusion"
        card["author_conclusion"] = f"The {metric} effect applies across operating conditions."
    case = make_case(case_id, card, units, source_version)
    expected_status = "PARTIAL" if idx % 3 == 0 else "CONFLICT"
    gold = make_gold(case_id, "boundary_omission", idx, target, expected_status, refs, ("downgrade", "flag"))
    if target == "/candidate_card/boundary_conditions" and card["boundary_conditions"]:
        gold["accepted_target_pointers"].append("/candidate_card/boundary_conditions/0")
    return case, gold


def causal_case(form: str, idx: int, case_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    setting, population, metric, unit = FORM_CONTEXTS[form][idx]
    source_version = f"{form.lower()}-src-causal-{idx + 1:02d}-v6"
    paper_id = f"SYN-{form}-CAU-{idx + 1:02d}"
    source_claims = (
        "was associated with",
        "suggested a possible relation to",
        "predicted",
        "preceded",
        "was compatible with partial mediation of",
        "was observed alongside",
        "differed in the nonrandomized comparison of",
        "motivated a mechanistic hypothesis about",
        "was correlated with",
        "was identified as a risk marker for",
        "was observed in an exploratory analysis of",
        "was discussed in a secondary citation concerning",
    )
    overclaims = (
        "caused", "proved", "is a therapy that improves", "caused", "fully mediates",
        "will improve when applied", "produced the effect in", "establishes the pathway controlling",
        "eliminates", "determines", "definitively establishes", "directly proved",
    )
    source_a = f"The focal condition {source_claims[idx]} {metric}."
    source_b = "The study design did not identify a randomized causal contrast, and the authors retained qualified language."
    units, refs = make_evidence(
        form=form, case_id=case_id, family_index=3, variant_index=idx,
        relevant_texts=(source_a, source_b), source_version=source_version, paper_id=paper_id,
    )
    card = base_card(paper_id=paper_id, source_version=source_version, context=(setting, population, metric, unit), relevant_refs=refs)
    target = "/candidate_card/author_conclusion" if idx % 4 else "/candidate_card/key_results/0"
    claim = f"The focal condition {overclaims[idx]} {metric}."
    if target.endswith("key_results/0"):
        card["key_results"][0] = claim
    else:
        card["author_conclusion"] = claim
    case = make_case(case_id, card, units, source_version)
    gold = make_gold(case_id, "causal_overclaim", idx, target, "UNSUPPORTED", refs, ("downgrade", "flag"))
    return case, gold


def lineage_case(form: str, idx: int, case_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    setting, population, metric, unit = FORM_CONTEXTS[form][idx]
    current_version = f"{form.lower()}-src-lineage-{idx + 1:02d}-v7"
    stale_version = f"{form.lower()}-src-lineage-{idx + 1:02d}-v{1 + idx % 3}"
    authoritative_run_id = f"{form.lower()}-lineage-run-{idx + 1:02d}"
    paper_id = f"SYN-{form}-LIN-{idx + 1:02d}"
    source_a = (
        f"The authoritative {metric} record is version {current_version}, produced by "
        f"synthetic-card-source-builder-v2 in run {authoritative_run_id}."
    )
    source_b = f"Version {stale_version} is superseded and must not support active Card conclusions."
    units, refs = make_evidence(
        form=form, case_id=case_id, family_index=4, variant_index=idx,
        relevant_texts=(source_a, source_b), source_version=current_version, paper_id=paper_id,
    )
    card = base_card(paper_id=paper_id, source_version=current_version, context=(setting, population, metric, unit), relevant_refs=refs)
    target = "/candidate_card/source_version"
    if idx == 0:
        card["source_version"] = stale_version
    elif idx == 1:
        target = "/candidate_card/producer_profile"
        card["producer_profile"] = "retired-card-importer-v1"
    elif idx == 2:
        target = "/candidate_card/method/0"
        card["method"][0] = f"Method projected from superseded revision {stale_version}."
    elif idx == 3:
        target = "/candidate_card/card_version"
        card["card_version"] = "card-v3-before-correction"
    elif idx == 4:
        target = "/candidate_card/source_anchor/by_field/key_results/0/paper_id"
        card["source_anchor"]["by_field"]["key_results"][0]["paper_id"] = f"SYN-{form}-OTHER-PAPER"
    elif idx == 5:
        target = "/candidate_card/source_anchor/by_field/author_conclusion/0/source_version"
        card["source_anchor"]["by_field"]["author_conclusion"][0]["source_version"] = stale_version
    elif idx == 6:
        target = "/candidate_card/producer_profile"
        card["producer_profile"] = "card-distiller-primary-retired-profile"
    elif idx == 7:
        target = "/candidate_card/source_anchor/by_field/method/0/run_id"
        card["source_anchor"]["by_field"]["method"][0]["run_id"] = "unrelated-run-splice"
    elif idx == 8:
        target = "/candidate_card/source_version"
        card["source_version"] = "RETIRED-ARTIFACT"
    elif idx == 9:
        target = "/candidate_card/parent_source_sha256"
        card["parent_source_sha256"] = "0" * 64
    elif idx == 10:
        target = "/candidate_card/source_anchor/by_field/boundary_conditions/0/source_version"
        card["source_anchor"]["by_field"]["boundary_conditions"][0]["source_version"] = stale_version
    else:
        target = "/candidate_card/source_timestamp"
        card["source_timestamp"] = "2025-01-01T00:00:00Z"
    case = make_case(case_id, card, units, current_version)
    case["lineage_context"] = {
        "authoritative_source_version": current_version,
        "authoritative_producer": "synthetic-card-source-builder-v2",
        "authoritative_profile": "card-distiller-primary-synthetic-v2",
        "authoritative_paper_id": paper_id,
        "authoritative_run_id": authoritative_run_id,
        "minimum_valid_card_version": "card-v6-synthetic",
        "authoritative_parent_source_sha256": sha256_bytes(current_version.encode("utf-8")),
        "correction_published_at": "2026-06-01T00:00:00Z",
        "superseded_source_versions": [stale_version],
    }
    gold = make_gold(case_id, "stale_lineage", idx, target, "CONFLICT", refs, ("tombstone", "flag"))
    return case, gold


def clean_case(form: str, idx: int, case_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    setting, population, metric, unit = FORM_CONTEXTS[form][idx]
    source_version = f"{form.lower()}-src-clean-{idx + 1:02d}-v8"
    paper_id = f"SYN-{form}-CLN-{idx + 1:02d}"
    source_pairs = (
        (f"The focal condition was associated with a limited change in {metric}.", f"The conclusion was restricted to {population}."),
        (f"The analyte concentration for {population} in the {setting} was 2,000 mg/L, equivalent to 2.0 g/L.", "Both unit expressions refer to the same measured concentration."),
        (f"The estimate was 12.04 {unit} before display rounding.", f"Reporting 12.0 {unit} is within the prespecified one-decimal display rule."),
        (f"The label '{population}' and its registered short name refer to the same cohort.", "The normalization changes typography only."),
        (f"The result may indicate a relation with {metric}.", "No causal claim was made."),
        (f"Population alias P{idx + 1} is the registry label for {population}.", "The alias is stable across the source and Card."),
        (f"The boundary is reported in the method and applies to the conclusion.", "Cross-field placement preserves the restriction without changing meaning."),
        (f"Version {source_version} is the current released source.", "No superseded lineage member is cited."),
        (f"The Card uses a reordered sentence with unchanged factual content about {metric}.", "Style and ordering differences are not substantive defects."),
        (f"Two independent chunks support the same bounded {metric} statement.", "Both anchors are current and belong to the same paper."),
        ("The list order follows the Card template rather than the source paragraph order.", "All items and scopes remain present."),
        (f"No statistically clear change in {metric} was detected.", "The Card faithfully retains the negative result and uncertainty."),
    )
    units, refs = make_evidence(
        form=form, case_id=case_id, family_index=5, variant_index=idx,
        relevant_texts=source_pairs[idx], source_version=source_version, paper_id=paper_id,
    )
    card = base_card(paper_id=paper_id, source_version=source_version, context=(setting, population, metric, unit), relevant_refs=refs)
    if idx == 0:
        card["author_conclusion"] = f"A bounded association with {metric} was observed in {population}."
    elif idx == 1:
        card["research_question"] = f"What analyte concentration was reported for {population} in the {setting}?"
        card["key_results"] = ["The reported analyte concentration was 2.0 g/L."]
        card["key_data"][0].update({"metric": "analyte concentration", "value": "2.0", "unit": "g/L"})
        card["author_conclusion"] = "The two equivalent unit expressions report the same analyte concentration."
    elif idx == 2:
        card["key_data"][0].update({"value": "12.0", "unit": unit})
    elif idx == 3:
        card["research_object"] = f"registered short name for {population}"
    elif idx == 4:
        card["author_conclusion"] = f"The finding may indicate a relation with {metric}."
    elif idx == 5:
        card["research_object"] = f"P{idx + 1} ({population})"
    elif idx == 6:
        card["boundary_conditions"] = ["Restriction retained in the documented method and applies to the conclusion."]
    elif idx == 7:
        card["source_version"] = source_version
    elif idx == 8:
        card["key_results"] = [f"A limited change in {metric} was observed in the focal condition."]
    elif idx == 9:
        pass
    elif idx == 10:
        card["method"] = list(reversed(card["method"]))
    else:
        card["key_results"] = [f"No statistically clear change in {metric} was detected."]
        card["author_conclusion"] = "The result remains inconclusive."
    case = make_case(case_id, card, units, source_version)
    gold = {
        "case_id": case_id,
        "expected_verdict": "PASS",
        "issue_family": "none",
        "severity": "none",
        "difficulty": DIFFICULTIES[idx],
        "variant": CLEAN_VARIANTS[idx],
        "accepted_target_pointers": [""],
        "expected_status": "SUPPORTED",
        "required_evidence_refs": [],
        "allowed_evidence_refs": refs,
        "accepted_actions": ["no_issue"],
        "critical": False,
    }
    return case, gold


def make_case(
    case_id: str,
    card: dict[str, Any],
    evidence_units: list[dict[str, Any]],
    source_version: str,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "candidate_card": card,
        "lineage_context": {
            "authoritative_source_version": source_version,
            "authoritative_producer": "synthetic-card-source-builder-v2",
            "authoritative_profile": "card-distiller-primary-synthetic-v2",
            "authoritative_paper_id": card["paper_id"],
        },
        "evidence_units": evidence_units,
    }


def make_gold(
    case_id: str,
    family: str,
    idx: int,
    target: str,
    status: str,
    refs: list[str],
    actions: tuple[str, ...],
) -> dict[str, Any]:
    variants = {
        "numeric_direction": NUMERIC_VARIANTS,
        "population_mismatch": POPULATION_VARIANTS,
        "boundary_omission": BOUNDARY_VARIANTS,
        "causal_overclaim": CAUSAL_VARIANTS,
        "stale_lineage": LINEAGE_VARIANTS,
    }
    accepted_target_pointers = [target]
    if "/source_anchor/" in target and target.rsplit("/", 1)[-1] in {"paper_id", "source_version", "run_id"}:
        accepted_target_pointers.append(target.rsplit("/", 1)[0])
    return {
        "case_id": case_id,
        "expected_verdict": "FAIL",
        "issue_family": family,
        "severity": SEVERITIES[idx],
        "difficulty": DIFFICULTIES[idx],
        "variant": variants[family][idx],
        "accepted_target_pointers": accepted_target_pointers,
        "expected_status": status,
        "required_evidence_refs": refs,
        "allowed_evidence_refs": refs,
        "accepted_actions": list(actions),
        "critical": SEVERITIES[idx] == "critical",
    }


BUILDERS = {
    "numeric_direction": numeric_case,
    "population_mismatch": population_case,
    "boundary_omission": boundary_case,
    "causal_overclaim": causal_case,
    "stale_lineage": lineage_case,
    "clean_control": clean_case,
}


def build_form(form: str) -> tuple[dict[str, Any], dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    gold_cases: list[dict[str, Any]] = []
    counter = 0
    for variant_index in range(VARIANT_COUNT):
        rotation = (variant_index + (1 if form == "B" else 0)) % len(FAMILIES)
        order = FAMILIES[rotation:] + FAMILIES[:rotation]
        for family in order:
            counter += 1
            case_id = f"CRX-{form}-{counter:03d}"
            case, gold = BUILDERS[family](form, variant_index, case_id)
            cases.append(case)
            gold_cases.append(gold)
    payload = {
        "schema_version": FORM_SCHEMA_VERSION,
        "suite": "card_reviewer_primary",
        "form": form,
        "form_id": f"CARD-REVIEWER-REFERENCE-{form}-V1",
        "instructions_ref": "prompts/card_reviewer_prompt.json",
        "output_schema_ref": "schemas/card_reviewer_output.schema.json",
        "cases": cases,
    }
    gold = {
        "schema_version": GOLD_SCHEMA_VERSION,
        "suite": "card_reviewer_primary",
        "form": form,
        "form_id": payload["form_id"],
        "case_count": len(gold_cases),
        "family_quota": {family: 12 for family in FAMILIES},
        "cases": gold_cases,
    }
    return payload, gold


def output_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:memorive:model_evaluation:card-reviewer-exam-output:v2",
        "type": "object",
        "additionalProperties": False,
        "required": ["reviews"],
        "properties": {
            "reviews": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "case_id", "verdict", "issue_family", "severity",
                        "target_pointer", "status", "evidence_refs", "action", "reason",
                    ],
                    "properties": {
                        "case_id": {"type": "string", "minLength": 1},
                        "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
                        "issue_family": {"type": "string", "enum": [*DEFECT_FAMILIES, "none"]},
                        "severity": {"type": "string", "enum": ["critical", "major", "minor", "none"]},
                        "target_pointer": {"type": "string"},
                        "status": {"type": "string", "enum": ["SUPPORTED", "PARTIAL", "UNSUPPORTED", "CONFLICT"]},
                        "evidence_refs": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
                        "action": {"type": "string", "enum": ["flag", "downgrade", "tombstone", "no_issue"]},
                        "reason": {"type": "string", "minLength": 1},
                    },
                },
            }
        },
    }


def form_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:memorive:model_evaluation:card-reviewer-exam-form:v2",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "suite", "form", "form_id", "instructions_ref", "output_schema_ref", "cases"],
        "properties": {
            "schema_version": {"const": FORM_SCHEMA_VERSION},
            "suite": {"const": "card_reviewer_primary"},
            "form": {"enum": ["A", "B"]},
            "form_id": {"type": "string", "minLength": 1},
            "instructions_ref": {"const": "prompts/card_reviewer_prompt.json"},
            "output_schema_ref": {"const": "schemas/card_reviewer_output.schema.json"},
            "cases": {
                "type": "array", "minItems": 72, "maxItems": 72,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["case_id", "candidate_card", "lineage_context", "evidence_units"],
                    "properties": {
                        "case_id": {"type": "string", "pattern": "^CRX-[RT]-[AB]-[0-9]{3}$"},
                        "candidate_card": {"type": "object"},
                        "lineage_context": {"type": "object"},
                        "evidence_units": {
                            "type": "array", "minItems": 10, "maxItems": 10,
                            "items": {
                                "type": "object", "additionalProperties": False,
                                "required": ["evidence_id", "paper_id", "chunk_id", "section", "source_version", "producer", "text"],
                                "properties": {
                                    "evidence_id": {"type": "string"}, "paper_id": {"type": "string"},
                                    "chunk_id": {"type": "string"}, "section": {"type": "string"},
                                    "source_version": {"type": "string"}, "producer": {"type": "string"},
                                    "text": {"type": "string", "minLength": 20},
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def gold_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:memorive:model_evaluation:card-reviewer-exam-gold:v2",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "suite", "form", "form_id", "case_count", "family_quota", "cases"],
        "properties": {
            "schema_version": {"const": GOLD_SCHEMA_VERSION},
            "suite": {"const": "card_reviewer_primary"},
            "form": {"enum": ["A", "B"]},
            "form_id": {"type": "string"},
            "case_count": {"const": 72},
            "family_quota": {
                "type": "object", "additionalProperties": False,
                "required": list(FAMILIES),
                "properties": {family: {"const": 12} for family in FAMILIES},
            },
            "cases": {
                "type": "array", "minItems": 72, "maxItems": 72,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": [
                        "case_id", "expected_verdict", "issue_family", "severity", "difficulty", "variant",
                        "accepted_target_pointers", "expected_status", "required_evidence_refs", "allowed_evidence_refs",
                        "accepted_actions", "critical",
                    ],
                    "properties": {
                        "case_id": {"type": "string"}, "expected_verdict": {"enum": ["PASS", "FAIL"]},
                        "issue_family": {"enum": [*DEFECT_FAMILIES, "none"]},
                        "severity": {"enum": ["critical", "major", "minor", "none"]},
                        "difficulty": {"enum": ["medium", "hard", "adversarial"]},
                        "variant": {"type": "string"},
                        "accepted_target_pointers": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                        "expected_status": {"enum": ["SUPPORTED", "PARTIAL", "UNSUPPORTED", "CONFLICT"]},
                        "required_evidence_refs": {"type": "array", "items": {"type": "string"}},
                        "allowed_evidence_refs": {"type": "array", "items": {"type": "string"}},
                        "accepted_actions": {"type": "array", "minItems": 1, "items": {"enum": ["flag", "downgrade", "tombstone", "no_issue"]}},
                        "critical": {"type": "boolean"},
                    },
                },
            },
        },
    }


def prompt_contract() -> dict[str, Any]:
    return {
        "schema_version": "model_evaluation-card-reviewer-exam-prompt-v4",
        "system": (
            "You are auditing independent synthetic Memorive Card projections. Review every case against only its evidence units and lineage context. "
            "Return exactly one review row per case. A case has either one substantive defect or is a clean control. Do not flag style, ordering, faithful paraphrase, allowed rounding, or equivalent unit normalization. "
            "For a defect, target_pointer MUST be an RFC 6901 JSON pointer rooted at the complete case object and therefore begin with /candidate_card/. "
            "Point to the narrowest existing Card field or list item that contains the defect; do not use a pointer relative to candidate_card. "
            "Use issue_family=numeric_direction for wrong direction, number, denominator, unit, statistic, or quantitative scope; "
            "population_mismatch for cohort, comparator, species, phase, site, sample, or modality substitution; "
            "boundary_omission for a missing condition or an explicit over-generalization beyond a reported condition; "
            "causal_overclaim only for causal, mechanistic, therapeutic, predictive-to-interventional, or proof language unsupported by the study design, not for mere scope generalization; "
            "and stale_lineage for source version, producer/profile, paper, run, parent hash, timestamp, retired artifact, or field-anchor lineage mismatch. "
            "Use status=CONFLICT for direct factual or lineage contradiction, PARTIAL for an omission or incomplete scope, UNSUPPORTED for a claim not supported by the evidence, and SUPPORTED only for a clean control. "
            "Use severity=critical when the defect can reverse the central conclusion/comparator or invalidate an active conclusion's lineage; major when it materially changes a value, population, scope, or interpretation; and minor only for a contained substantive defect that does not alter the central interpretation. "
            "Cite only evidence IDs from that case and choose the least destructive safe action. "
            "For a clean case return PASS, issue_family=none, severity=none, target_pointer='', status=SUPPORTED, action=no_issue. "
            "Do not omit cases and do not include Markdown."
        ),
        "user_template": "Audit the complete form JSON supplied after this instruction and return an object matching the frozen output schema.",
        "gold_available_to_subject": False,
        "gold_available_to_provider": False,
    }


def scoring_protocol() -> dict[str, Any]:
    return {
        "schema_version": "model_evaluation-card-reviewer-scoring-protocol-v6",
        "case_points": {
            "defect": {
                "verdict": 15, "issue_family": 25, "status": 15, "target_pointer": 15,
                "evidence": 20, "severity": 5, "action": 5,
            },
            "clean": {"verdict": 60, "issue_family": 10, "status": 10, "action": 10, "clean_integrity": 10},
        },
        "piecewise_components": {
            "overall_semantic": {"points": 30, "knots": [[0, 0], [40, 0], [55, 3.75], [65, 9], [75, 15.75], [85, 23.25], [92, 27], [97, 29.25], [100, 30]]},
            "hard_case": {"points": 15, "knots": [[0, 0], [45, 0], [60, 2.25], [70, 5.25], [80, 9], [88, 12], [94, 13.875], [100, 15]]},
            "worst_family": {"points": 10, "knots": [[0, 0], [40, 0], [55, 1.333333], [65, 3.333333], [75, 5.333333], [85, 7.333333], [92, 8.666667], [100, 10]]},
            "bottom_quartile": {"points": 10, "knots": [[0, 0], [30, 0], [45, 1], [60, 3], [70, 5], [80, 7], [90, 9], [100, 10]]},
            "clean_specificity": {"points": 10, "knots": [[0, 0], [50, 0], [75, 5], [90, 8], [100, 10]]},
            "evidence_localization": {"points": 5, "knots": [[0, 0], [50, 0], [65, 1], [75, 2], [85, 3.5], [95, 4.5], [100, 5]]},
            "critical_detection": {"points": 12, "knots": [[0, 0], [80, 0], [90, 1.5], [95, 4], [99, 8], [100, 12]]},
            "stale_lineage_detection": {"points": 8, "knots": [[0, 0], [75, 0], [85, 1], [91.666667, 2], [95, 3.5], [99, 6], [100, 8]]},
        },
        "directed_repair": {
            "maximum_turns_per_form": 1,
            "scope": "GOLD_BLIND_PRE_SCORE_STRUCTURAL_RECOVERY",
            "fixed_turn_penalty": 1.0,
            "volume_penalty_knots": [[0, 0], [0.02, 0.25], [0.05, 0.5], [0.1, 1], [0.25, 2.5], [0.5, 5], [1, 10]],
            "failed_repair_form_score": 0,
            "capacity_or_transport_score": None,
        },
        "exam_directed_repair": {
            "maximum_rounds_per_exam": 1,
            "wrong_item_definition": "EVERY_CASE_WITH_FROZEN_INITIAL_LOCAL_SCORE_BELOW_100",
            "wrong_item_limit": None,
            "volume_penalty_knots": [[0, 0], [0.05, 0.5], [0.1, 0.8], [0.25, 1.5], [0.5, 2.0], [1, 4.0]],
            "artifact_category_points": 0.5,
            "severity_category_points": {"major": 0.5, "critical": 1.0},
            "distinct_issue_category_points": 0.1,
            "distinct_issue_category_cap": 1.5,
            "successful_repair_ceiling": 89.99,
            "failed_repair_final_score": 0,
            "capacity_or_transport_score": None,
            "any_remaining_or_new_wrong_item_disqualifies": True,
        },
        "hard_gates": [
            "MISS_ANY_CRITICAL_CASE",
            "CRITICAL_LOCALIZATION_RATE_BELOW_THRESHOLD",
            "STALE_LINEAGE_MISSED",
            "SUBSTANTIVE_FALSE_POSITIVE_ON_ANY_CLEAN_CONTROL",
            "FINAL_CASE_SET_OR_SCHEMA_INVALID",
            "GOLD_EXPOSURE",
            "MODEL_IDENTITY_MISMATCH",
        ],
        "hard_gate_parameters": {"minimum_critical_localization_rate": 0.8},
        "recommendation_bands": [
            {"minimum": 0, "maximum_exclusive": 60, "code": "REJECT_AS_CARD_REVIEWER"},
            {"minimum": 60, "maximum_exclusive": 80, "code": "NOT_RECOMMENDED_AS_CARD_REVIEWER"},
            {"minimum": 80, "maximum_inclusive": 100, "code": "SUITABLE_AS_CARD_REVIEWER_IF_HARD_GATES_PASS"},
        ],
    }


def repair_contract() -> dict[str, Any]:
    return {
        "schema_version": "model_evaluation-card-reviewer-directed-repair-contract-v3",
        "repair_exact_set_required": True,
        "allowed_action": "REPLACE_REVIEW_ROW",
        "whole_form_regeneration_forbidden": True,
        "structural_recovery": {
            "maximum_model_repair_turns_per_form": 1,
            "performed_before_initial_local_score": True,
            "gold_blind": True,
            "separately_accounted_from_exam_quality_repair": True,
        },
        "exam_quality_repair": {
            "maximum_model_repair_rounds_per_exam": 1,
            "repair_after_both_initial_forms_scored": True,
            "wrong_item_definition": "EVERY_CASE_WITH_FROZEN_INITIAL_LOCAL_SCORE_BELOW_100",
            "wrong_item_limit": None,
            "all_and_only_wrong_items_required": True,
            "any_remaining_or_new_wrong_item_disqualifies": True,
            "failed_complete_repair_final_score": 0,
        },
        "repair_prompt_includes": ["all_target_cases_across_completed_forms", "current_rows", "machine_issue_codes", "failed_output_fields", "output_schema", "hash_bindings"],
        "repair_prompt_excludes": ["gold", "expected_answer", "scoring_detail", "private_holdout_identity", "penalty_contract"],
        "merge_requires": ["exact_case_id_set", "original_response_hash", "target_row_hashes", "full_post_merge_audit", "full_post_merge_local_rescore"],
    }


def build_pack(output: Path, identity_namespace: str) -> dict[str, Any]:
    if identity_namespace != "R":
        raise ValueError(
            "FORMAL_BUILDER_ONLY_ALLOWS_DISJOINT_LOW_COST_REHEARSAL_NAMESPACE_R"
        )
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"candidate output must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    form_a, gold_a = build_form("A")
    form_b, gold_b = build_form("B")
    form_a = apply_identity_namespace(form_a, identity_namespace)
    gold_a = apply_identity_namespace(gold_a, identity_namespace)
    form_b = apply_identity_namespace(form_b, identity_namespace)
    gold_b = apply_identity_namespace(gold_b, identity_namespace)
    members: dict[str, Any] = {
        "forms/form_A.json": form_a,
        "forms/form_B.json": form_b,
        "gold/form_A_gold.json": gold_a,
        "gold/form_B_gold.json": gold_b,
        "schemas/card_reviewer_form.schema.json": form_schema(),
        "schemas/card_reviewer_gold.schema.json": gold_schema(),
        "schemas/card_reviewer_output.schema.json": output_schema(),
        "prompts/card_reviewer_prompt.json": prompt_contract(),
        "scoring/scoring_protocol.json": scoring_protocol(),
        "repair/directed_repair_contract.json": repair_contract(),
    }
    for relative, value in members.items():
        write_json(output / relative, value)

    roles = {
        "forms/form_A.json": "FORM_A",
        "forms/form_B.json": "FORM_B",
        "gold/form_A_gold.json": "GOLD_A",
        "gold/form_B_gold.json": "GOLD_B",
        "schemas/card_reviewer_form.schema.json": "FORM_SCHEMA",
        "schemas/card_reviewer_gold.schema.json": "GOLD_SCHEMA",
        "schemas/card_reviewer_output.schema.json": "OUTPUT_SCHEMA",
        "prompts/card_reviewer_prompt.json": "PROMPT",
        "scoring/scoring_protocol.json": "SCORING_PROTOCOL",
        "repair/directed_repair_contract.json": "REPAIR_CONTRACT",
    }
    manifest_members = []
    provider_visible_roles = {"FORM_A", "FORM_B", "PROMPT", "OUTPUT_SCHEMA"}
    for relative in sorted(members):
        path = output / relative
        raw = path.read_bytes()
        role = roles[relative]
        visible_to_subject = role in provider_visible_roles
        manifest_members.append(
            {
                "path": relative,
                "role": role,
                "bytes": len(raw),
                "sha256": sha256_bytes(raw),
                "subject_visible": visible_to_subject,
                "provider_visible": visible_to_subject,
                "rights": {"repository_inclusion": "PENDING_EXACT_DECLASSIFICATION", "basis": "self_synthetic"},
                "provenance": {"owner": "Memorive", "source": "deterministic_self_synthetic_generator", "external_source": False},
            }
        )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "suite": "card_reviewer_primary",
        "pack_id": f"MODEL-EVALUATION-MODEL_EVALUATION-CARD-REVIEWER-REFERENCE-EXAM-{identity_namespace}-V1",
        "pack_revision": "r1.0-rc5",
        "identity_namespace": identity_namespace,
        "classification": "REFERENCE_EXAM_PACK_CANDIDATE",
        "reference_regression_only": True,
        "blind_holdout_eligible": False,
        "qualification_eligible": False,
        "declassification": {
            "authorized": False,
            "authority": None,
            "reason": "Exact pack declassification and formal-write authorization are pending user review.",
        },
        "topology": {
            "forms": 2,
            "cases_each": 72,
            "evidence_units_each": 720,
            "families": list(FAMILIES),
            "samples_per_family_each": 12,
        },
        "members": manifest_members,
    }
    manifest_path = output / "manifest.candidate.json"
    write_json(manifest_path, manifest)
    checksum_rows = []
    for relative in sorted([*members, "manifest.candidate.json"]):
        checksum_rows.append(f"{sha256_bytes((output / relative).read_bytes())}  {relative}")
    checksum_path = output / "SHA256SUMS.txt"
    with checksum_path.open("xb") as handle:
        handle.write(("\n".join(checksum_rows) + "\n").encode("utf-8"))
    return {
        "output": str(output),
        "member_count": len(members),
        "identity_namespace": identity_namespace,
        "form_A_cases": len(form_a["cases"]),
        "form_B_cases": len(form_b["cases"]),
        "form_A_evidence_units": sum(len(case["evidence_units"]) for case in form_a["cases"]),
        "form_B_evidence_units": sum(len(case["evidence_units"]) for case in form_b["cases"]),
        "manifest_sha256": sha256_bytes(manifest_path.read_bytes()),
        "checksums_sha256": sha256_bytes(checksum_path.read_bytes()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--identity-namespace", choices=("R",), required=True)
    args = parser.parse_args()
    print(json.dumps(build_pack(args.output.resolve(), args.identity_namespace), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
