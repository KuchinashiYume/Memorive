"""Packing recalled chunks into the analysis context (research/retrieval/context_pack.py).

Made-up chunks of one paper: the token limit comes first, whole blocks only; room is reserved for
likely counterexamples, which the rest cannot push out; single-paper extraction reserves none; the
same input gives the same pack.
"""
from __future__ import annotations

import pytest

from memorive.research.retrieval import context_pack
from memorive.research.retrieval.context_pack import build_context_pack
from memorive.research.retrieval.ownership import bind_authority_projection, snapshot_fields
from memorive.research.retrieval.types import Candidate, CandidateSet


def candidate(index, text):
    cid = f"Paper#c{index}"
    bound = bind_authority_projection(subject_ref=f"chunk:Paper:{cid}", authoritative_value="self", assertion_ref=f"OS-{index}",
                                      revision=1, basis_ref=f"basis:{index}", projection_value="self",
                                      projection_revision=1, projection_ref=f"vector:{cid}")
    authority = bound.ownership_contributors[0]
    return Candidate(chunk_id=cid, paper_id="Paper", text=text, distance=index / 100, section_path="Results",
                     block_type="paragraph", metadata_status="vector", source_role="direct_result",
                     ownership_assertion_ref=authority.assertion_ref, ownership_revision=authority.revision,
                     ownership_basis_ref=authority.basis_ref, ownership_projection_value="self",
                     ownership_projection_revision=1, **snapshot_fields(bound))


def pack(items, **kwargs):
    return build_context_pack(CandidateSet(candidates=tuple(items), target_active_count=len(items),
                                           raw_recall_count=len(items), filtered_non_active_count=0,
                                           final_active_count=len(items), reached_overfetch_cap=False), **kwargs)


def tokens(item):
    settings = context_pack._cfg()
    return context_pack._estimate_tokens(item.text, settings["token_ratio"], settings["overhead_per_block"])


MAIN = [candidate(n, f"Growth at 37 C was higher in run {n}, by a clear margin in every replicate.") for n in (1, 2, 3)]
AGAINST = candidate(9, "A later replication did not support the growth difference at 37 C.")


def chunk_ids(result):
    return [block.source["chunk_id"] for block in result.blocks]


@pytest.mark.parametrize("limit", [0, -5, True, "100"])
def test_an_invalid_token_limit_is_refused(limit):
    with pytest.raises(ValueError, match="CONTEXT_PACK_TOKEN_HARD_LIMIT_INVALID"):
        pack(MAIN, token_hard_limit=limit)


def test_a_block_larger_than_the_limit_is_dropped_whole_and_the_rest_fits():
    huge = candidate(0, "Growth " * 400)
    limit = sum(tokens(c) for c in MAIN)
    result = pack([huge, *MAIN], token_hard_limit=limit)
    assert chunk_ids(result) == [c.chunk_id for c in MAIN]
    assert result.estimated_tokens <= limit and result.truncated
    assert result.dropped_reason_counts["single_block_over_limit"] == 1


def test_room_kept_for_a_likely_counterexample_is_not_taken_by_closer_chunks():
    limit = tokens(MAIN[0]) + tokens(AGAINST)
    result = pack([*MAIN, AGAINST], token_hard_limit=limit)
    assert set(chunk_ids(result)) == {MAIN[0].chunk_id, AGAINST.chunk_id}
    assert result.counterexample_slots["filled"] == 1
    assert result.dropped_reason_counts["mainstream_capacity_dropped"] == 2


def test_single_paper_extraction_reserves_no_room_for_counterexamples():
    limit = tokens(MAIN[0]) + tokens(MAIN[1])
    result = pack([*MAIN, AGAINST], token_hard_limit=limit, retrieval_mode="single_paper_extraction")
    assert chunk_ids(result) == [MAIN[0].chunk_id, MAIN[1].chunk_id]
    assert result.counterexample_slots == {"target": 0, "filled": 0}


def test_the_same_chunks_give_the_same_pack():
    first, second = pack([*MAIN, AGAINST]), pack([AGAINST, *reversed(MAIN)])
    assert chunk_ids(first) == chunk_ids(second) and first.context_pack_hash == second.context_pack_hash
