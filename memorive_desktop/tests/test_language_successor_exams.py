from __future__ import annotations


def test_phase2_new_projection_does_not_invent_a_repair_penalty() -> None:
    from memorive_settings.quality_new import _entry_repair_burden

    projected = _entry_repair_burden(
        {
            "semantic_repair_rounds": 2,
            "repair_burden_affects_capability_score": False,
        }
    )
    assert projected["class"] == "R2_USED"
    assert projected["repair_burden_affects_capability_score"] is False
    assert (
        projected["source_category_score_already_contains_its_frozen_penalty"]
        is False
    )
