"""Language tracks of the card distiller language-bank exam (QualityNewCardDistillerLanguageBankExamExecutor
._stage_score_projection) with the repository's scoring protocol and made-up case scores.

Each form holds 12 English cases (two per family, one of them hard) and two Chinese and two Japanese
cases. The English cases alone make the score and the verdict; the other languages are reported as
their mean and change nothing. A critical English finding caps the score at 59, a major one below 90;
the first-pass score comes from the scores before the repairs. Nothing calls a model.
"""
from __future__ import annotations

import pytest

from memorive.exams.card_distiller import QualityNewCardDistillerLanguageBankExamExecutor
from memorive.exams.evaluation import card_distiller_exam_protocol as protocol


def form(form_id):
    cases = [{"case_id": f"{form_id}-en-{family}-{n}", "source_language": "en"}
             for family in protocol.FAMILIES for n in (1, 2)]
    cases += [{"case_id": f"{form_id}-{lang}-{n}", "source_language": lang} for lang in ("zh", "ja") for n in (1, 2)]
    return {"cases": cases}


FORMS = {form_id: form(form_id) for form_id in ("A", "B")}
FAMILY_OF = {f"{form_id}-en-{family}-{n}": family for form_id in FORMS for family in protocol.FAMILIES for n in (1, 2)}


def scores(**changes):
    """Every case at 100 with no findings; changes maps a case id to its new detail fields."""
    result = {}
    for form_id, value in FORMS.items():
        details = []
        for row in value["cases"]:
            case_id = row["case_id"]
            detail = {"case_id": case_id, "score": 100.0, "family": FAMILY_OF.get(case_id, protocol.FAMILIES[0]),
                      "difficulty": "hard" if case_id.endswith("-1") else "medium",
                      "critical_reasons": [], "major_reasons": []}
            detail.update(changes.get(case_id, {}))
            details.append(detail)
        result[form_id] = {"details": details}
    return result


def project(tmp_path, initial, final):
    executor = QualityNewCardDistillerLanguageBankExamExecutor(
        scratch_root=tmp_path, quality_package="memorive.exams.evaluation", reference_pack_root=tmp_path / "pack")
    return executor._stage_score_projection(protocol=protocol, forms=FORMS, initial_scores=initial,
                                            final_scores=final, repair_rounds=1)


def missed(**fields):
    return {"score": 0.0, "critical_reasons": ["MISSED"], **fields}


OTHER_LANGUAGES = {f"{form_id}-{lang}-{n}": missed() for form_id in FORMS for lang in ("zh", "ja") for n in (1, 2)}


def test_only_the_english_cases_make_the_score_and_the_verdict(tmp_path):
    result = project(tmp_path, scores(), scores(**OTHER_LANGUAGES))
    assert result["score_override"] == 100.0 and result["quality_verdict_override"] == "PASS"
    assert result["primary_case_ids"] == sorted(FAMILY_OF)
    tracks = result["language_tracks"]
    assert tracks["zh"]["post_repair"]["score"] == 0.0 and tracks["zh"]["post_repair"]["critical_count"] == 4
    assert tracks["zh"]["affects_primary_score"] is False and tracks["en"]["affects_primary_score"] is True
    assert tracks["en"]["post_repair"]["case_count"] == 24


def test_a_critical_english_finding_caps_the_score_at_59_and_fails(tmp_path):
    result = project(tmp_path, scores(), scores(**{"A-en-D3_MULTI_OCCURRENCE-2": {"critical_reasons": ["WRONG_VALUE"]}}))
    form_a = result["language_tracks"]["en"]["post_repair"]["forms"]["A"]
    assert form_a["score"] == 59.0 and form_a["score_caps_applied"] == ["CATASTROPHIC_SCIENTIFIC_FAILURE_CAP_59"]
    assert result["score_override"] == 79.5  # the mean of A (59) and B (100)
    assert result["quality_verdict_override"] == "FAIL"


def test_a_major_english_finding_keeps_the_score_below_90(tmp_path):
    result = project(tmp_path, scores(), scores(**{"B-en-D1_NUMERIC_UNIT_COMPARATOR-2": {"major_reasons": ["UNIT"]}}))
    form_b = result["language_tracks"]["en"]["post_repair"]["forms"]["B"]
    assert form_b["score"] == 89.99 and form_b["score_caps_applied"] == ["MAJOR_FINDING_CEILING_BELOW_90"]
    assert result["quality_verdict_override"] == "PASS"  # the mean of 89.99 and 100 is above the passing 85


def test_the_first_pass_score_comes_from_the_scores_before_the_repairs(tmp_path):
    result = project(tmp_path, scores(**{"A-en-D2_TIME_SAMPLE_SCOPE-1": missed()}), scores())
    assert result["first_pass_score_override"] == 79.5 and result["score_override"] == 100.0
    assert result["repair_burden_affects_capability_score"] is False and result["repair_rounds_used"] == 1


def test_an_english_track_missing_a_family_is_refused(tmp_path):
    lost = {f"{form_id}-en-D6_CLEAN_DIRECT_EXTRACTION-{n}": {"family": "D1_NUMERIC_UNIT_COMPARATOR"}
            for form_id in FORMS for n in (1, 2)}
    with pytest.raises(ValueError, match="CARD_DISTILLER_ENGLISH_FAMILY_COVERAGE_INVALID"):
        project(tmp_path, scores(), scores(**lost))
