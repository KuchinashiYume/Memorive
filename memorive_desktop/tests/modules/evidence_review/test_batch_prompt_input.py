"""The card-review batch prompt carries the card items it asks the model to check.

Since v1.01 the function that writes the batch input into the prompt was split in two and
returned None, so the prompt said ``null`` where the card items belong (fixed in 142a4d2).
The card below is synthetic.
"""
from __future__ import annotations

import json
from collections import namedtuple

from memorive.documents.card_review import transcription as tx

Subject = namedtuple("Subject", "location field value_text chunk_ids")
CHUNKS = {
    "c1": "We recruited 48 adult participants (24 women) from the university community.",
    "c2": "Samples were annealed at 450 °C for 2 h under argon.",
    "c3": "Mean reaction time decreased by 12% after training (p = 0.03).",
}
SUBJECTS = [
    Subject("by_field.sample", "sample", "- 共招募 84 名成年参与者，其中 24 名女性", ["c1"]),
    Subject("by_field.method", "method", "- 样品在氩气中于 540 °C 退火 2 小时", ["c2"]),
    Subject("by_field.result", "result", "- 训练后平均反应时间下降 12%（p = 0.03）", ["c3"]),
]


def _batch_input_block(prompt: str) -> dict:
    """The JSON object that follows the batch-input heading of the prompt."""
    decoder = json.JSONDecoder()
    for start in (index for index, char in enumerate(prompt) if char == "{"):
        try:
            value, _ = decoder.raw_decode(prompt[start:])
        except ValueError:
            continue
        if isinstance(value, dict) and "subjects" in value:
            return value
    raise AssertionError("no batch input object in the prompt")


def test_the_prompt_carries_every_card_item_and_its_sources():
    batch_input = tx._build_batch_input(SUBJECTS, CHUNKS)
    prompt = tx._build_batch_initial_prompt(batch_input)
    block = _batch_input_block(prompt)
    assert [row["location"] for row in block["subjects"]] == [s.location for s in SUBJECTS]
    assert [row["card_items"][0]["text"] for row in block["subjects"]] == [
        "共招募 84 名成年参与者，其中 24 名女性",
        "样品在氩气中于 540 °C 退火 2 小时",
        "训练后平均反应时间下降 12%（p = 0.03）",
    ]
    assert [row["chunk_ids"] for row in block["subjects"]] == [["c1"], ["c2"], ["c3"]]
    for text in CHUNKS.values():
        assert text in prompt


def test_a_subject_using_part_of_the_sources_keeps_its_own_scope():
    long_ids = {f"chunk-{'x' * 60}-{n}": f"source text {n}" for n in range(6)}
    everything = Subject("by_field.summary", "summary", "- 总结", list(long_ids))
    narrow = Subject("by_field.limit", "limit", "- 局限", [next(iter(long_ids))])
    batch_input = tx._build_batch_input([everything, narrow], long_ids)
    wired = tx._wire_batch_input(batch_input, batch_input["sources"])
    rows = {row["location"]: row for row in wired["subjects"]}
    # Whichever form is shorter may be sent, but the narrower subject never widens.
    assert rows["by_field.limit"]["chunk_ids"] == [next(iter(long_ids))]
    assert "source_scope_ref" not in rows["by_field.limit"]
    summary = rows["by_field.summary"]
    assert summary.get("chunk_ids") == list(long_ids) or summary.get("source_scope_ref") == "ALL_SOURCE_CONTEXT"
