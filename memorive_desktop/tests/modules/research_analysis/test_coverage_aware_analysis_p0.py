"""quality-C tests for coverage-aware RESEARCH_ANALYSIS post-processing without Prompt changes."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from memorive.research.retrieval.context_pack import _context_pack_identity
from memorive.research.retrieval.ownership import (
    aggregate_blocks,
    aggregate_ownership,
    bind_authority_projection,
    propagation_receipt,
    snapshot_fields,
)
from memorive.research.retrieval.types import ContextBlock, ContextPack, QueryFacet
from memorive.research.analysis.analysis import analyze
from memorive.research.analysis.types import LABEL_RETRIEVAL_LIMITATIONS


def block(
    chunk_id: str,
    *,
    section: str,
    facet_ids: tuple[str, ...],
    source_role: str,
    span_id: str,
) -> ContextBlock:
    candidate_snapshot = bind_authority_projection(
        subject_ref=f"chunk:Paper:{chunk_id}",
        authoritative_value="self",
        assertion_ref="OS-COVERAGE-TEST",
        revision=1,
        basis_ref="synthetic:test-fixture",
        projection_value="self",
        projection_revision=1,
        projection_ref=f"fixture:{chunk_id}",
    )
    block_ref = f"block:{span_id}"
    block_snapshot = aggregate_ownership(
        candidate_snapshot.ownership_contributors,
        subject_ref=block_ref,
    )
    return ContextBlock(
        content=f"evidence from {chunk_id}",
        source={"chunk_id": chunk_id, "paper_id": "Paper", "field": "key_results"},
        weight=1.0,
        is_counterexample=False,
        tokens=20,
        member_chunk_ids=(chunk_id,),
        logical_span_id=span_id,
        logical_role="single_chunk",
        facet_ids=facet_ids,
        source_role=source_role,
        section_path=section,
        block_type="paragraph",
        metadata_status="vector",
        block_ref=block_ref,
        ownership_assertion_ref="OS-COVERAGE-TEST",
        ownership_revision=1,
        ownership_basis_ref="synthetic:test-fixture",
        **snapshot_fields(block_snapshot),
    )


def pack(
    *blocks: ContextBlock,
    coverage_status: str,
    missing_facets: tuple[str, ...] = (),
    coverage_by_facet: dict | None = None,
) -> ContextPack:
    block_items = tuple(blocks)
    context_pack_ref, context_pack_hash = _context_pack_identity(block_items)
    ownership_snapshot = aggregate_blocks(block_items, subject_ref=context_pack_ref)
    ownership_receipt = propagation_receipt(
        ownership_snapshot,
        subject_chain={
            "blocks": [item.block_ref for item in block_items],
            "pack": context_pack_ref,
        },
        checks=("TEST_NATIVE_OWNERSHIP_BINDING",),
    )
    return ContextPack(
        blocks=block_items,
        estimated_tokens=sum(item.tokens for item in blocks),
        token_hard_limit=6000,
        counterexample_slots={"target": 0, "filled": 0},
        truncated=False,
        raw_recall_count=len(blocks),
        filtered_non_active_count=0,
        final_active_count=len(blocks),
        scope_mode="paper_id",
        scope_paper_id="Paper",
        retrieval_mode="single_paper_extraction",
        query_facets=(
            QueryFacet("performance"),
            QueryFacet("kinetics"),
        ),
        coverage_status=coverage_status,
        missing_facets=missing_facets,
        coverage_by_facet=coverage_by_facet or {},
        answer_complete=coverage_status == "complete",
        context_pack_ref=context_pack_ref,
        context_pack_hash=context_pack_hash,
        ownership_propagation_receipt=ownership_receipt.to_dict(),
        **snapshot_fields(ownership_snapshot, pack=True),
    )


def response(literature_support, *, ai=None, risks=None):
    return {
        "text": json.dumps(
            {
                "literature_support": literature_support,
                "possibly_irrelevant": [],
                "ai_general_knowledge": ai or [],
                "risks_and_uncertainties": risks or [],
                "recheck_locations": [],
            }
        ),
        "model": "stub-sonnet-5",
    }


class CoverageAwareAnalysisP0Test(unittest.TestCase):
    def test_partial_pack_suppresses_ai_filler_and_separates_retrieval_gap(self) -> None:
        context = pack(
            block(
                "Paper#c16",
                section="Results",
                facet_ids=("performance",),
                source_role="direct_result",
                span_id="S-performance",
            ),
            coverage_status="partial",
            missing_facets=("kinetics",),
            coverage_by_facet={
                "performance": {"required": True, "satisfied": True, "evidence_span_ids": ["S-performance"]},
                "kinetics": {"required": True, "satisfied": False, "evidence_span_ids": []},
            },
        )

        result = analyze(
            context,
            "performance and kinetics",
            data_ownership="self",
            region_check=lambda: None,
            model_call=lambda _payload: response(
                [{"text": "Performance reached 77%.", "source_chunk_id": "Paper#c16"}],
                ai=["Generic kinetic knowledge."],
                risks=["The study did not report kinetics in the supplied evidence."],
            ),
        )

        self.assertFalse(result.answer_complete)
        self.assertEqual(result.coverage_status, "partial")
        self.assertEqual(result.ai_general_knowledge, ())
        self.assertTrue(any("kinetics" in item for item in result.retrieval_limitations))
        self.assertFalse(any("kinetics" in item for item in result.study_limitations))
        self.assertIn(LABEL_RETRIEVAL_LIMITATIONS, result.render())

    def test_claim_origin_and_facets_are_rebuilt_from_authoritative_block(self) -> None:
        context = pack(
            block(
                "Paper#c16",
                section="Results",
                facet_ids=("performance",),
                source_role="direct_result",
                span_id="S-performance",
            ),
            block(
                "Paper#c46",
                section="Discussion",
                facet_ids=("kinetics",),
                source_role="secondary_citation_reported_by_paper",
                span_id="S-discussion",
            ),
            coverage_status="complete",
            coverage_by_facet={
                "performance": {"required": True, "satisfied": True, "evidence_span_ids": ["S-performance"]},
                "kinetics": {"required": True, "satisfied": True, "evidence_span_ids": ["S-discussion"]},
            },
        )

        result = analyze(
            context,
            "performance and kinetics",
            data_ownership="self",
            region_check=lambda: None,
            model_call=lambda _payload: response([
                {"text": "Performance reached 77%.", "source_chunk_id": "Paper#c16"},
                {"text": "Prior work reported a mechanism.", "source_chunk_id": "Paper#c46"},
            ]),
        )

        by_chunk = {item.source_id["chunk_id"]: item for item in result.literature_support}
        self.assertEqual(by_chunk["Paper#c16"].origin, "direct_result")
        self.assertEqual(by_chunk["Paper#c16"].facet_ids, ("performance",))
        self.assertEqual(
            by_chunk["Paper#c46"].origin,
            "secondary_citation_reported_by_paper",
        )
        self.assertEqual(result.quality_metrics["unique_chunk_count"], 2)
        self.assertEqual(result.quality_metrics["unique_logical_span_count"], 2)
        self.assertEqual(result.quality_metrics["unique_section_count"], 2)
        self.assertEqual(result.quality_metrics["direct_result_claim_count"], 1)
        self.assertEqual(result.quality_metrics["secondary_citation_claim_count"], 1)
        self.assertEqual(result.quality_metrics["subquestion_coverage"]["covered"], 2)
        self.assertEqual(result.quality_metrics["max_source_dominance"], 0.5)
        self.assertTrue(result.answer_complete)

    def test_render_organises_supported_claims_under_facets(self) -> None:
        context = pack(
            block(
                "Paper#c16",
                section="Results",
                facet_ids=("performance",),
                source_role="direct_result",
                span_id="S-performance",
            ),
            coverage_status="complete",
            coverage_by_facet={
                "performance": {"required": True, "satisfied": True, "evidence_span_ids": ["S-performance"]},
            },
        )
        result = analyze(
            context,
            "performance",
            data_ownership="self",
            region_check=lambda: None,
            model_call=lambda _payload: response([
                {"text": "Performance reached 77%.", "source_chunk_id": "Paper#c16"}
            ]),
        )

        rendered = result.render()
        self.assertIn("### performance", rendered)
        self.assertIn("origin=direct_result", rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
