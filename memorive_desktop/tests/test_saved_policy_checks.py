"""Checks on settings and decisions that are saved and read back later: the frozen output
language of a job, report due times, research-opportunity drafts from a model, retrieval
weights and report windows.
"""
from __future__ import annotations

import copy

import pytest

from memorive.platform.language import policy as language
from memorive_research_runtime import report_schedule
from research_opportunities.research_opportunities import validate as opportunities
from research_reports.errors import WindowContractError
from research_reports.policy import validate_window
from retrieval_weighting import research_policy


# Frozen output language

@pytest.mark.parametrize("kwargs", [
    {},
    {"explicit_locale": "ja-JP"},
    {"current_user_text": "Please answer in English."},
    {"current_user_text": "Please answer in French."},
    {"source_language": "de"},
])
def test_a_frozen_language_decision_reads_back_unchanged(kwargs):
    value = language.freeze({"revision": 3, "preferences": {"language": "zh-CN"}}, **kwargs)
    assert language.validate(value) == value


def _frozen(**changes):
    value = language.freeze({"revision": 3, "preferences": {"language": "zh-CN"}})
    value.update(changes)
    if "instruction" not in changes:
        value["instruction"] = language.instruction(value)
    return value


@pytest.mark.parametrize("value, code", [
    ("zh-CN", "LANGUAGE_CONTEXT_FIELDS_INVALID"),
    ({k: v for k, v in _frozen().items() if k != "ui_locale"}, "LANGUAGE_CONTEXT_FIELDS_INVALID"),
    (_frozen(extra="x"), "LANGUAGE_CONTEXT_FIELDS_INVALID"),
    (_frozen(policy_version="MemoContentLanguage-v0"), "LANGUAGE_CONTEXT_VERSION_INVALID"),
    (_frozen(template_version="other"), "LANGUAGE_CONTEXT_VERSION_INVALID"),
    (_frozen(ui_locale="fr-FR"), "LANGUAGE_CONTEXT_VERSION_INVALID"),
    (_frozen(decision_mode="GUESS_FROM_TITLE"), "LANGUAGE_CONTEXT_MODE_INVALID"),
    (_frozen(effective_output_locale="fr-FR"), "OUTPUT_LOCALE_UNSUPPORTED"),
    (_frozen(effective_output_locale="fr-FR", language_support="UNVERIFIED"), "OUTPUT_LOCALE_UNSUPPORTED"),
    (_frozen(effective_output_locale="fr-FR", decision_mode="CURRENT_USER_INSTRUCTION"), "OUTPUT_LOCALE_UNSUPPORTED"),
    (_frozen(effective_output_locale="xx-XX", decision_mode="CURRENT_USER_INSTRUCTION", language_support="UNVERIFIED"),
     "OUTPUT_LOCALE_UNSUPPORTED"),
    (_frozen(settings_revision=-1), "LANGUAGE_CONTEXT_INVALID"),
    (_frozen(settings_revision="3"), "LANGUAGE_CONTEXT_INVALID"),
    (_frozen(instruction="Write everything in English."), "LANGUAGE_CONTEXT_INVALID"),
])
def test_a_changed_language_decision_is_refused(value, code):
    with pytest.raises(ValueError) as caught:
        language.validate(value)
    assert str(caught.value) == code


# Report due times

def test_report_due_times_within_the_calendar_are_accepted():
    value = dict(report_schedule.defaults(), daily_time="23:59", weekly_day=6, monthly_day=31)
    assert report_schedule.validate(value) == value


@pytest.mark.parametrize("changes, code", [
    ({"extra": 1}, "REPORT_SCHEDULE_FIELDS_INVALID"),
    ({"daily_time": "24:00"}, "REPORT_SCHEDULE_TIME_INVALID"),
    ({"weekly_time": "9:30"}, "REPORT_SCHEDULE_TIME_INVALID"),
    ({"monthly_time": "12:60"}, "REPORT_SCHEDULE_TIME_INVALID"),
    ({"daily_time": 930}, "REPORT_SCHEDULE_TIME_INVALID"),
    ({"weekly_day": 7}, "REPORT_SCHEDULE_DAY_INVALID"),
    ({"weekly_day": -1}, "REPORT_SCHEDULE_DAY_INVALID"),
    ({"weekly_day": True}, "REPORT_SCHEDULE_DAY_INVALID"),
    ({"monthly_day": 0}, "REPORT_SCHEDULE_DAY_INVALID"),
    ({"monthly_day": 32}, "REPORT_SCHEDULE_DAY_INVALID"),
])
def test_report_due_times_outside_the_calendar_are_refused(changes, code):
    with pytest.raises(ValueError) as caught:
        report_schedule.validate(dict(report_schedule.defaults(), **changes))
    assert str(caught.value) == code


def test_report_due_times_need_every_field():
    value = report_schedule.defaults()
    value.pop("monthly_day")
    with pytest.raises(ValueError, match="REPORT_SCHEDULE_FIELDS_INVALID"):
        report_schedule.validate(value)


# Research-opportunity drafts returned by a model

def _opportunity(**changes):
    value = {"question": "Does temperature matter?", "hypothesis": "Higher is faster.", "rationale": "Two papers differ.",
             "limitations": "Small samples.", "evidence_ids": ["e1", "e2"], "classification": "condition_difference"}
    value.update(changes)
    return value


def test_opportunities_citing_given_evidence_are_kept_as_unassessed_drafts():
    rows = opportunities([_opportunity()], {"e1", "e2"})
    assert (rows[0]["state"], rows[0]["scientific_verdict"]) == ("draft", "NOT_ASSESSED")


@pytest.mark.parametrize("items, code", [
    ({"question": "q"}, "OPPORTUNITY_RESULT_INVALID"),
    ([_opportunity()] * 7, "OPPORTUNITY_RESULT_INVALID"),
    ([_opportunity(extra="x")], "OPPORTUNITY_RESULT_INVALID"),
    ([{k: v for k, v in _opportunity().items() if k != "limitations"}], "OPPORTUNITY_RESULT_INVALID"),
    ([_opportunity(hypothesis=" ")], "OPPORTUNITY_RESULT_INVALID"),
    ([_opportunity(rationale=None)], "OPPORTUNITY_RESULT_INVALID"),
    ([_opportunity(classification="proven_fact")], "OPPORTUNITY_RESULT_INVALID"),
    ([_opportunity(evidence_ids=[])], "OPPORTUNITY_CITATION_INVALID"),
    ([_opportunity(evidence_ids="e1")], "OPPORTUNITY_CITATION_INVALID"),
    ([_opportunity(evidence_ids=["e1", "e9"])], "OPPORTUNITY_CITATION_INVALID"),
])
def test_opportunities_outside_the_form_or_citing_other_evidence_are_refused(items, code):
    with pytest.raises(ValueError) as caught:
        opportunities(items, {"e1", "e2"})
    assert str(caught.value) == code


# Retrieval weights

def _weights(**changes):
    value = copy.deepcopy(research_policy.DEFAULT)
    for key, item in changes.items():
        if isinstance(item, dict) and isinstance(value.get(key), dict):
            value[key] = dict(value[key], **item)
        else:
            value[key] = item
    return value


def test_the_default_and_preset_weights_are_accepted():
    assert research_policy.validate(_weights()) == research_policy.DEFAULT
    names = list(research_policy.WEIGHTS)
    for preset in research_policy.PRESETS:
        research_policy.validate(_weights(weights=dict(zip(names, [w / 100 for w in preset["weights"]]))))


def test_older_saved_weights_without_the_rule_fields_are_completed():
    value = _weights()
    del value["rule_enrichment"], value["rule_credential_env"]
    result = research_policy.validate(value)
    assert (result["rule_enrichment"], result["rule_credential_env"]) == (True, "OPENALEX_API_KEY")


SHIFTED = {"Similarity": .30, "Semantic": .10, "Manual": .25, "Rule": .20, "Note": .15, "Citation": 0}


@pytest.mark.parametrize("changes, code", [
    ({"extra": 1}, "WEIGHT_SETTINGS_INVALID"),
    ({"weights": {"Other": .1}}, "WEIGHT_SETTINGS_INVALID"),
    ({"penalties": []}, "WEIGHT_SETTINGS_INVALID"),
    ({"penalties": list(research_policy.PENALTIES)}, "WEIGHT_SETTINGS_INVALID"),
    ({"weights": {"Similarity": .50}}, "WEIGHT_TOTAL_REQUIRED"),
    ({"weights": {"Similarity": float("nan")}}, "WEIGHT_TOTAL_REQUIRED"),
    ({"weights": {"Similarity": True, "Semantic": .65}}, "WEIGHT_TOTAL_REQUIRED"),
    ({"weights": {"Similarity": 1.2, "Semantic": -.55}}, "WEIGHT_TOTAL_REQUIRED"),
    ({"weights": {"Similarity": 0, "Semantic": 0, "Manual": .5, "Rule": .2, "Note": .2, "Citation": .1}},
     "WEIGHT_RELEVANCE_REQUIRED"),
    ({"weights": {"Similarity": .65, "Semantic": 0}}, "WEIGHT_RANGE_INVALID"),
    ({"weights": SHIFTED}, "WEIGHT_RELEVANCE_RANGE_INVALID"),
    ({"penalties": {"concept": 1.5}}, "WEIGHT_PENALTY_INVALID"),
    ({"penalties": {"concept": "0.8"}}, "WEIGHT_PENALTY_INVALID"),
    ({"penalties": {"concept": True}}, "WEIGHT_PENALTY_INVALID"),
    ({"semantic_profile_ref": ""}, "WEIGHT_PROFILE_INVALID"),
    ({"embedding_profile_ref": "p" * 501}, "WEIGHT_PROFILE_INVALID"),
    ({"embedding_profile_ref": 3}, "WEIGHT_PROFILE_INVALID"),
    ({"semantic_enabled": 1}, "WEIGHT_SETTINGS_INVALID"),
    ({"keep_counterevidence": "yes"}, "WEIGHT_SETTINGS_INVALID"),
    ({"rule_enrichment": None}, "WEIGHT_SETTINGS_INVALID"),
    ({"rule_credential_env": "OPENALEX KEY"}, "WEIGHT_SETTINGS_INVALID"),
    ({"rule_credential_env": "1KEY"}, "WEIGHT_SETTINGS_INVALID"),
    ({"review_days": 6}, "WEIGHT_SETTINGS_INVALID"),
    ({"review_days": 731}, "WEIGHT_SETTINGS_INVALID"),
    ({"interest_days": 91}, "WEIGHT_SETTINGS_INVALID"),
    ({"interest_half_life": 0}, "WEIGHT_SETTINGS_INVALID"),
    ({"interest_half_life": 7.0}, "WEIGHT_SETTINGS_INVALID"),
    ({"interest_boost": .21}, "WEIGHT_SETTINGS_INVALID"),
    ({"interest_boost": float("inf")}, "WEIGHT_SETTINGS_INVALID"),
    ({"interest_boost": False}, "WEIGHT_SETTINGS_INVALID"),
])
def test_weights_outside_their_limits_are_refused(changes, code):
    with pytest.raises(ValueError) as caught:
        research_policy.validate(_weights(**changes))
    assert str(caught.value) == code


def test_the_range_limits_can_be_lifted_for_older_saved_weights():
    research_policy.validate(_weights(weights=SHIFTED), enforce_limits=False)
    research_policy.validate(_weights(weights={"Similarity": .65, "Semantic": 0}), enforce_limits=False)
    with pytest.raises(ValueError, match="WEIGHT_TOTAL_REQUIRED"):
        research_policy.validate(_weights(weights={"Similarity": .50}), enforce_limits=False)


def test_a_refused_weight_change_leaves_the_defaults_alone():
    with pytest.raises(ValueError):
        research_policy.validate(_weights(weights={"Similarity": .50}))
    assert research_policy.DEFAULT["weights"]["Similarity"] == .45


# Report windows (Asia/Shanghai calendar)

@pytest.mark.parametrize("kind, start, end", [
    ("daily", "2026-11-02T00:00:00+08:00", "2026-11-03T00:00:00+08:00"),
    ("daily", "2026-11-01T16:00:00Z", "2026-11-02T16:00:00Z"),
    ("weekly", "2026-11-02T00:00:00+08:00", "2026-11-09T00:00:00+08:00"),
    ("monthly", "2026-12-01T00:00:00+08:00", "2027-01-01T00:00:00+08:00"),
    ("monthly", "2028-02-01T00:00:00+08:00", "2028-03-01T00:00:00+08:00"),
])
def test_calendar_aligned_windows_are_accepted(kind, start, end):
    window = validate_window(kind, start, end, end)
    assert window[0].utcoffset().total_seconds() == 8 * 3600


@pytest.mark.parametrize("kind, start, end, cutoff, message", [
    ("yearly", "2026-01-01T00:00:00+08:00", "2027-01-01T00:00:00+08:00", None, "unsupported digest kind"),
    ("daily", "2026-11-02T00:00:00", "2026-11-03T00:00:00+08:00", None, "timezone-aware"),
    ("daily", "", "2026-11-03T00:00:00+08:00", None, "non-empty"),
    ("daily", "1991-12-31T00:00:00+08:00", "1992-01-01T00:00:00+08:00", None, "1992 onward"),
    ("daily", "2026-11-02T01:00:00+08:00", "2026-11-03T01:00:00+08:00", None, "window_start must be local midnight"),
    ("daily", "2026-11-02T00:00:00+08:00", "2026-11-03T00:00:01+08:00", None, "window_end must be local midnight"),
    ("daily", "2026-11-02T00:00:00+08:00", "2026-11-04T00:00:00+08:00", None, "exactly one local day"),
    ("weekly", "2026-11-03T00:00:00+08:00", "2026-11-10T00:00:00+08:00", None, "Monday through next Monday"),
    ("weekly", "2026-11-02T00:00:00+08:00", "2026-11-16T00:00:00+08:00", None, "Monday through next Monday"),
    ("monthly", "2026-11-02T00:00:00+08:00", "2026-12-02T00:00:00+08:00", None, "calendar-month aligned"),
    ("monthly", "2026-11-02T00:00:00+08:00", "2026-12-01T00:00:00+08:00", None, "calendar-month aligned"),
    ("monthly", "2026-11-01T00:00:00+08:00", "2026-12-31T00:00:00+08:00", None, "calendar-month aligned"),
    ("daily", "2026-11-02T00:00:00+08:00", "2026-11-03T00:00:00+08:00", "2026-11-02T23:00:00+08:00",
     "cutoff_at must not precede window_end"),
])
def test_windows_off_the_calendar_are_refused(kind, start, end, cutoff, message):
    with pytest.raises(WindowContractError, match=message):
        validate_window(kind, start, end, cutoff or end)
