"""Reading cards written before field_credibility existed (card versions 1 to 4).

An old card has no field_credibility: every one of the six content fields reads as unknown,
with the "not checked" warning, and nothing fails. A newer card returns its own values.
"""
from evidence_extraction.card import CONTENT_FIELDS, CRED_UNKNOWN, DANGER_UNKNOWN_FIELD, read_field_credibility


def test_old_card_without_field_credibility_reads_six_unknown_fields():
    old_card = {"schema_version": "card-v4", "paper_id": "p1", "research_question": "q"}
    result = read_field_credibility(old_card)
    assert len(CONTENT_FIELDS) == 6
    assert list(result) == list(CONTENT_FIELDS)
    for field in CONTENT_FIELDS:
        assert result[field] == {"credibility": CRED_UNKNOWN, "note": None, "danger": DANGER_UNKNOWN_FIELD}


def test_field_credibility_that_is_not_a_mapping_is_treated_as_missing():
    result = read_field_credibility({"field_credibility": None})
    assert {value["credibility"] for value in result.values()} == {CRED_UNKNOWN}


def test_card_with_field_credibility_returns_its_own_values():
    stored = {field: {"credibility": "verified", "note": "ok", "danger": None} for field in CONTENT_FIELDS}
    assert read_field_credibility({"field_credibility": stored}) is stored


def test_results_for_two_old_cards_are_independent():
    first = read_field_credibility({})
    first[CONTENT_FIELDS[0]]["note"] = "changed"
    assert read_field_credibility({})[CONTENT_FIELDS[0]]["note"] is None
