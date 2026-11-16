import pytest

from memorive_settings.exam_tiers import nested_selection, tier_manifest, scored_projection, validate_tier


def test_nested_prefix_is_model_independent_and_preserves_controls():
    rows = [{"id": str(i), "language": ["en", "zh", "ja"][i % 3]} for i in range(50)]
    results = [nested_selection(rows, tier=t, id_key="id", required_ids=["49"], strata=["language"])
               for t in ("LIGHT", "HALF", "FULL")]
    assert [len(x) for x in results] == [10, 25, 50]
    sets = [{x["id"] for x in r} for r in results]
    assert {"49"} <= sets[0] < sets[1] < sets[2]
    assert {r["language"] for r in results[0]} == {"en", "zh", "ja"}


def test_two_repairs_have_identical_non_linear_penalty_across_tiers():
    # Category coefficients supplied by its scorer; the tier must not scale them.
    rounds = [{"category_id": "CARD_REVIEWER", "round_number": 1, "nonlinear_category_penalty": 2.3},
              {"category_id": "CARD_REVIEWER", "round_number": 2, "nonlinear_category_penalty": 5.7, "fixed_penalty": 3}]
    scores = [scored_projection("CARD_REVIEWER", 94, rounds, t) for t in ("LIGHT", "HALF", "FULL")]
    assert {x["repair_adjusted_exam_score"] for x in scores} == {83.0}
    assert {x["max_directed_repairs"] for x in scores} == {2}
    with pytest.raises(ValueError): scored_projection("ANALYSIS_PRIMARY", 94, rounds, "FULL")
    with pytest.raises(ValueError): scored_projection("CARD_REVIEWER", 94, rounds + rounds[:1], "FULL")


def test_floor_is_explicit_and_no_unmeasured_fail():
    m = tier_manifest(role="CARD_REVIEWER", tier="LIGHT", full_ids=list("abcdef"), selected_ids=list("abcdef"),
                      floor_reason="FROZEN_SCORER_REQUIRES_ALL_SAFETY_FAMILIES")
    assert m["requested_fraction"] == .2 and m["actual_fraction"] == 1
    assert not m["cost_fraction_guaranteed"] and not m["model_fail_established"]
    assert not m["formal_qualification_eligible"]
    for bad in [None, "STANDARD", "20%", 20]:
        with pytest.raises(ValueError): validate_tier(bad)
