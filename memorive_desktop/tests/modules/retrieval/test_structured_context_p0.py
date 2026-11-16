"""quality-A tests for facets, bibliography eligibility, logical spans, and coverage."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from memorive.research.retrieval import Candidate, CandidateSet, QueryFacet
from memorive.research.retrieval.structured_context import bibliography_is_eligible, build_card_anchor_candidates, build_logical_spans, build_structured_context_pack, detect_query_facets, infer_source_role, is_high_confidence_bibliography
from memorive.research.retrieval.structured_context import retrieve_faceted_candidates
def candidate_set(*candidates: Candidate) -> CandidateSet:
    return CandidateSet(
        candidates=tuple(candidates),
        target_active_count=len(candidates),
        raw_recall_count=len(candidates),
        filtered_non_active_count=0,
        final_active_count=len(candidates),
        reached_overfetch_cap=False,
        scope_mode="paper_id",
        scope_paper_id="Paper",
    )


class StructuredContextP0Test(unittest.TestCase):
    def test_compound_question_detects_six_required_facets(self) -> None:
        facets = detect_query_facets(
            "Under which substrate, redox and dye conditions does the sludge "
            "decolourise dyes, what kinetics and performance are reported, "
            "and what limits interpretation?"
        )
        self.assertEqual(
            {facet.id for facet in facets if facet.required},
            {
                "substrate_conditions",
                "redox_conditions",
                "dye_conditions",
                "kinetics",
                "performance",
                "interpretation_limits",
            },
        )

    def test_blind_question_detects_generic_facets_without_false_redox(self) -> None:
        facets = detect_query_facets(
            "In anaerobic digestion, what performance changes are attributable "
            "to biochar, and what direct evidence distinguishes adsorption from "
            "direct interspecies electron transfer? State the tested conditions, "
            "comparator, quantitative outcomes, mechanistic evidence, and limitations."
        )
        facet_ids = {facet.id for facet in facets if facet.required}
        self.assertTrue({
            "experimental_conditions",
            "comparator",
            "quantitative_performance",
            "mechanism_direct_evidence",
            "performance",
            "interpretation_limits",
        }.issubset(facet_ids))
        self.assertNotIn("redox_conditions", facet_ids)

    def test_bibliography_requires_both_high_confidence_structure_and_intent(self) -> None:
        reference = Candidate(
            "Paper#c99",
            "Paper",
            "[42] Prior study.",
            0.01,
            section_path="References",
            block_type="bibliography_entry",
            metadata_status="vector",
        )
        uncertain = Candidate(
            "Paper#c98",
            "Paper",
            "Prior work was discussed in the results.",
            0.02,
            section_path="References",
            block_type=None,
            metadata_status="unknown",
        )
        self.assertTrue(is_high_confidence_bibliography(reference))
        self.assertFalse(is_high_confidence_bibliography(uncertain))
        self.assertFalse(bibliography_is_eligible("experimental_conditions"))
        self.assertTrue(bibliography_is_eligible("citation_lineage"))

        real_list_entry = Candidate(
            "Paper#c100",
            "Paper",
            "- [42] Prior study.",
            0.01,
            section_path="References",
            block_type="list",
            metadata_status="chunk_sidecar",
        )
        self.assertTrue(is_high_confidence_bibliography(real_list_entry))

    def test_table_caption_body_and_footnote_form_one_logical_span(self) -> None:
        candidates = (
            Candidate(
                "Paper#c15",
                "Paper",
                "Table 1. Decolourisation performance.",
                0.03,
                section_path="Results",
                block_type="table_caption",
                page_start=3,
                page_end=3,
            ),
            Candidate(
                "Paper#c16",
                "Paper",
                "| Dye | k |\n| Red 2 | 0.3 |",
                0.02,
                section_path="Results",
                block_type="table",
                page_start=3,
                page_end=3,
            ),
            Candidate(
                "Paper#c17",
                "Paper",
                "Footnote: rate estimate was rough.",
                0.04,
                section_path="Results",
                block_type="table_footnote",
                page_start=3,
                page_end=3,
            ),
        )

        spans = build_logical_spans(candidates)

        table_span = next(span for span in spans if span.logical_role == "table_with_footnotes")
        self.assertEqual(
            table_span.member_chunk_ids,
            ("Paper#c15", "Paper#c16", "Paper#c17"),
        )

    def test_real_chunk_table_encoding_keeps_caption_title_body_and_note(self) -> None:
        candidates = (
            Candidate(
                "Paper#c14", "Paper", "Table 1", 0.04,
                section_path="Results", block_type="figure_caption",
            ),
            Candidate(
                "Paper#c15", "Paper", "Overall performance.", 0.03,
                section_path="Results", block_type="paragraph",
            ),
            Candidate(
                "Paper#c16", "Paper", "| Dye | k |", 0.02,
                section_path="Results", block_type="table",
            ),
            Candidate(
                "Paper#c17", "Paper", "a k-values were fitted.", 0.01,
                section_path="Results", block_type="paragraph",
            ),
        )

        spans = build_logical_spans(candidates)

        self.assertEqual(len(spans), 1)
        self.assertEqual(
            spans[0].member_chunk_ids,
            ("Paper#c14", "Paper#c15", "Paper#c16", "Paper#c17"),
        )

    def test_cross_page_and_reversed_continuations_preserve_logical_order(self) -> None:
        candidates = (
            Candidate(
                "Paper#c24",
                "Paper",
                "The measured rate of",
                0.01,
                section_path="Results",
                block_type="paragraph",
            ),
            Candidate(
                "Paper#c25",
                "Paper",
                "decolourisation increased over time.",
                0.02,
                section_path="Results",
                block_type="paragraph",
            ),
            Candidate(
                "Paper#c38",
                "Paper",
                "tested were found to be reduced by sulphide.",
                0.03,
                section_path="Discussion",
                block_type="paragraph",
            ),
            Candidate(
                "Paper#c37",
                "Paper",
                "All eight dyes",
                0.04,
                section_path="Discussion",
                block_type="paragraph",
            ),
        )

        spans = build_logical_spans(candidates)
        members = {span.member_chunk_ids for span in spans}

        self.assertIn(("Paper#c24", "Paper#c25"), members)
        self.assertIn(("Paper#c37", "Paper#c38"), members)

    def test_non_adjacent_paragraphs_never_form_a_continuation_span(self) -> None:
        spans = build_logical_spans(
            (
                Candidate(
                    "Paper#c24",
                    "Paper",
                    "The measured rate of",
                    0.01,
                    section_path="Results",
                    block_type="paragraph",
                ),
                Candidate(
                    "Paper#c34",
                    "Paper",
                    "decolourisation increased over time.",
                    0.02,
                    section_path="Results",
                    block_type="paragraph",
                ),
            )
        )

        self.assertEqual(
            {span.member_chunk_ids for span in spans},
            {("Paper#c24",), ("Paper#c34",)},
        )

    def test_paragraph_continuation_respects_logical_span_token_limit(self) -> None:
        spans = build_logical_spans(
            (
                Candidate(
                    "Paper#c24",
                    "Paper",
                    "A" * 80,
                    0.01,
                    section_path="Results",
                    block_type="paragraph",
                ),
                Candidate(
                    "Paper#c25",
                    "Paper",
                    "and " + "b" * 80 + ".",
                    0.02,
                    section_path="Results",
                    block_type="paragraph",
                ),
            ),
            token_limit=128,
        )

        self.assertEqual(
            {span.member_chunk_ids for span in spans},
            {("Paper#c24",), ("Paper#c25",)},
        )

    def test_explicit_full_chunk_index_fetches_only_strict_continuation_neighbor(self) -> None:
        initial = candidate_set(
            Candidate(
                "Paper#c24",
                "Paper",
                "The measured rate of",
                0.01,
                section_path="Results",
                block_type="paragraph",
                facet_ids=("performance",),
            )
        )
        chunks = [
            {
                "paper_id": "Paper",
                "chunk_id": "Paper#c24",
                "text": "The measured rate of",
                "section_path": "Results",
                "block_type": "paragraph",
            },
            {
                "paper_id": "Paper",
                "chunk_id": "Paper#c25",
                "text": "decolourisation increased over time.",
                "section_path": "Results",
                "block_type": "paragraph",
            },
            {
                "paper_id": "Paper",
                "chunk_id": "Paper#c34",
                "text": "and this distant block must not be fetched.",
                "section_path": "Results",
                "block_type": "paragraph",
            },
        ]

        pack = build_structured_context_pack(
            initial,
            "What performance was measured?",
            facets=(QueryFacet("performance", True, ("performance",)),),
            retrieval_mode="single_paper_extraction",
            chunks=chunks,
        )

        self.assertEqual(len(pack.blocks), 1)
        self.assertEqual(pack.blocks[0].member_chunk_ids, ("Paper#c24", "Paper#c25"))
        self.assertNotIn("Paper#c34", pack.blocks[0].member_chunk_ids)
        self.assertIn("logical_span_neighbor", pack.blocks[0].retrieval_reasons)

    def test_explicit_full_chunk_index_fetches_adjacent_table_context(self) -> None:
        initial = candidate_set(
            Candidate(
                "Paper#c16",
                "Paper",
                "| Dye | k |\n| Red 2 | 0.3 |",
                0.01,
                section_path="Results",
                block_type="table",
                facet_ids=("performance",),
            )
        )
        chunks = [
            {
                "paper_id": "Paper",
                "chunk_id": "Paper#c14",
                "text": "Table 1",
                "section_path": "Results",
                "block_type": "figure_caption",
            },
            {
                "paper_id": "Paper",
                "chunk_id": "Paper#c15",
                "text": "Decolourisation performance.",
                "section_path": "Results",
                "block_type": "paragraph",
            },
            {
                "paper_id": "Paper",
                "chunk_id": "Paper#c16",
                "text": "| Dye | k |\n| Red 2 | 0.3 |",
                "section_path": "Results",
                "block_type": "table",
            },
            {
                "paper_id": "Paper",
                "chunk_id": "Paper#c17",
                "text": "a k-values were fitted.",
                "section_path": "Results",
                "block_type": "paragraph",
            },
        ]

        pack = build_structured_context_pack(
            initial,
            "What performance was measured?",
            facets=(QueryFacet("performance", True, ("performance",)),),
            retrieval_mode="single_paper_extraction",
            chunks=chunks,
        )

        self.assertEqual(len(pack.blocks), 1)
        self.assertEqual(
            pack.blocks[0].member_chunk_ids,
            ("Paper#c14", "Paper#c15", "Paper#c16", "Paper#c17"),
        )
        self.assertEqual(pack.blocks[0].logical_role, "table_with_footnotes")

    def test_without_full_chunk_index_logical_span_stays_candidate_only(self) -> None:
        initial = candidate_set(
            Candidate(
                "Paper#c24",
                "Paper",
                "The measured rate of",
                0.01,
                section_path="Results",
                block_type="paragraph",
                facet_ids=("performance",),
            )
        )

        pack = build_structured_context_pack(
            initial,
            "What performance was measured?",
            facets=(QueryFacet("performance", True, ("performance",)),),
            retrieval_mode="single_paper_extraction",
        )

        self.assertEqual(pack.blocks[0].member_chunk_ids, ("Paper#c24",))

    def test_each_required_facet_gets_an_independent_retrieval_query(self) -> None:
        facets = (
            QueryFacet("performance", True, ("performance",)),
            QueryFacet("kinetics", True, ("kinetics",)),
        )
        calls = []

        def retrieve(query, **_kwargs):
            calls.append(query)
            facet = "performance" if "performance" in query else "kinetics"
            return candidate_set(
                Candidate(
                    f"Paper#{facet}",
                    "Paper",
                    f"{facet} evidence",
                    0.01,
                )
            )

        result = retrieve_faceted_candidates(
            "compound question",
            facets,
            retrieve_fn=retrieve,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(
            {candidate.facet_ids[0] for candidate in result.candidates},
            {"performance", "kinetics"},
        )

    def test_non_citation_pack_excludes_references_and_reports_complete_coverage(self) -> None:
        facets = (
            QueryFacet("performance", True, ("performance",)),
            QueryFacet("kinetics", True, ("kinetics", "rate")),
        )
        candidates = candidate_set(
            Candidate(
                "Paper#c16",
                "Paper",
                "Performance reached 77%.",
                0.01,
                section_path="Results",
                block_type="paragraph",
                facet_ids=("performance",),
            ),
            Candidate(
                "Paper#c33",
                "Paper",
                "First-order kinetics gave a rate constant.",
                0.02,
                section_path="Results",
                block_type="paragraph",
                facet_ids=("kinetics",),
            ),
            Candidate(
                "Paper#c99",
                "Paper",
                "[42] Prior study.",
                0.001,
                section_path="References",
                block_type="bibliography_entry",
                metadata_status="vector",
                facet_ids=("kinetics",),
            ),
        )

        pack = build_structured_context_pack(
            candidates,
            "performance and kinetics",
            facets=facets,
            retrieval_mode="single_paper_extraction",
            query_intent="experimental_conditions",
        )

        chunk_ids = {
            chunk_id
            for block in pack.blocks
            for chunk_id in block.member_chunk_ids
        }
        self.assertEqual(pack.coverage_status, "complete")
        self.assertEqual(pack.missing_facets, ())
        self.assertNotIn("Paper#c99", chunk_ids)
        self.assertEqual(pack.counterexample_slots["target"], 0)
        self.assertEqual(pack.bibliography_share, 0.0)

    def test_missing_facet_gets_one_controlled_retry(self) -> None:
        facets = (
            QueryFacet("performance", True, ("performance",)),
            QueryFacet("kinetics", True, ("kinetics",)),
        )
        initial = candidate_set(
            Candidate(
                "Paper#c16",
                "Paper",
                "Performance reached 77%.",
                0.01,
                facet_ids=("performance",),
            )
        )
        calls = []

        def retry(missing_facets):
            calls.append(tuple(missing_facets))
            return candidate_set(
                Candidate(
                    "Paper#c33",
                    "Paper",
                    "Kinetics were first-order.",
                    0.01,
                    facet_ids=("kinetics",),
                )
            )

        pack = build_structured_context_pack(
            initial,
            "performance and kinetics",
            facets=facets,
            retrieval_mode="single_paper_extraction",
            retry_fn=retry,
        )

        self.assertEqual(calls, [("kinetics",)])
        self.assertEqual(pack.retry_count, 1)
        self.assertEqual(pack.coverage_status, "complete")

    def test_missing_facet_without_retry_is_partial_not_complete(self) -> None:
        facets = (
            QueryFacet("performance", True, ("performance",)),
            QueryFacet("kinetics", True, ("kinetics",)),
        )
        initial = candidate_set(
            Candidate(
                "Paper#c16",
                "Paper",
                "Performance reached 77%.",
                0.01,
                facet_ids=("performance",),
            )
        )

        pack = build_structured_context_pack(
            initial,
            "performance and kinetics",
            facets=facets,
            retrieval_mode="single_paper_extraction",
        )

        self.assertEqual(pack.coverage_status, "partial")
        self.assertEqual(pack.missing_facets, ("kinetics",))
        self.assertFalse(pack.answer_complete)

    def test_card_anchor_seeds_original_chunk_without_replacing_source(self) -> None:
        facets = (
            QueryFacet(
                "performance",
                True,
                ("performance",),
                preferred_card_paths=("key_results", "key_data"),
            ),
        )
        card = {
            "paper_id": "Paper",
            "source_anchor": {
                "by_field": {
                    "key_results": [
                        {"chunk_id": "Paper#c16", "quote": "Performance reached 77%."}
                    ]
                }
            },
        }
        chunks = [
            {
                "paper_id": "Paper",
                "chunk_id": "Paper#c16",
                "text": "Performance reached 77%.",
                "section_path": "Results",
                "block_type": "paragraph",
                "page_start": 3,
                "page_end": 3,
            }
        ]

        seeded = build_card_anchor_candidates(card, chunks, facets)

        self.assertEqual(len(seeded), 1)
        self.assertEqual(seeded[0].chunk_id, "Paper#c16")
        self.assertIn("card_anchor", seeded[0].retrieval_reasons)
        self.assertEqual(seeded[0].facet_ids, ("performance",))
        self.assertEqual(seeded[0].source_role, "direct_result")

    def test_source_role_inference_distinguishes_structure_and_review_body(self) -> None:
        self.assertEqual(
            infer_source_role("2. Materials and Methods", "paragraph"),
            "direct_method",
        )
        self.assertEqual(
            infer_source_role("3. Results", "table"),
            "direct_result",
        )
        self.assertEqual(
            infer_source_role("4. Discussion", "paragraph"),
            "author_interpretation",
        )
        self.assertEqual(
            infer_source_role("References", "list"),
            "secondary_citation_reported_by_paper",
        )
        self.assertEqual(
            infer_source_role(
                "2. Evidence overview",
                "paragraph",
                document_is_review=True,
            ),
            "secondary_citation_reported_by_paper",
        )

    def test_source_role_inference_combines_section_and_text_semantics(self) -> None:
        self.assertEqual(
            infer_source_role(
                "1. Introduction",
                "paragraph",
                "Qi et al. associated enhanced conductivity with DIET.",
            ),
            "secondary_citation_reported_by_paper",
        )
        self.assertEqual(
            infer_source_role(
                "3. Results and discussion > 3.5.2. Model predictions",
                "paragraph",
                "Future studies should provide more direct evidence; the model "
                "accuracy was 87% and gives qualitative support only.",
            ),
            "author_interpretation",
        )

    def test_source_role_gate_keeps_indirect_mechanism_evidence_missing(self) -> None:
        question = (
            "What performance changes are attributable to biochar, and what direct "
            "evidence distinguishes adsorption from DIET? State the tested conditions, "
            "comparator, quantitative outcomes, mechanistic evidence, and limitations."
        )
        candidates = candidate_set(
            Candidate(
                "Paper#c25",
                "Paper",
                "Seven groups including a control used 10 g/L biochar at 37 C for 29 days.",
                0.01,
                section_path="2. Materials and methods > Experimental setup",
                block_type="paragraph",
            ),
            Candidate(
                "Paper#c38",
                "Paper",
                "Biochar-E yield was 185.2 mL CH4/g COD, 9.6% above the control performance.",
                0.02,
                section_path="3. Results > Performance",
                block_type="paragraph",
            ),
            Candidate(
                "Paper#c20",
                "Paper",
                "Qi et al. found conductivity related to DIET, while direct evidence "
                "is lacking in most studies [10].",
                0.03,
                section_path="1. Introduction",
                block_type="paragraph",
                source_role="direct_result",
            ),
            Candidate(
                "Paper#c88",
                "Paper",
                "Future studies should provide more direct evidence for DIET; the "
                "prediction model accuracy was 87% and gives qualitative support only.",
                0.04,
                section_path="3. Results and discussion > Model predictions",
                block_type="paragraph",
                source_role="direct_result",
            ),
            Candidate(
                "Paper#c90",
                "Paper",
                "The interpretation is limited by a small sample size and missing values.",
                0.05,
                section_path="4. Discussion > Limitations",
                block_type="paragraph",
            ),
            Candidate(
                "Paper#c112",
                "Paper",
                "- [17] Evidence for direct interspecies electron transfer.",
                0.001,
                section_path="References",
                block_type="list",
                metadata_status="chunk_sidecar",
            ),
        )

        pack = build_structured_context_pack(
            candidates,
            question,
            facets=None,
            retrieval_mode="single_paper_extraction",
            query_intent=None,
        )

        self.assertEqual(pack.coverage_status, "partial")
        self.assertEqual(set(pack.missing_facets), {"mechanism_direct_evidence"})
        self.assertFalse(pack.answer_complete)
        self.assertEqual(pack.excluded_bibliography_count, 1)
        by_id = {block.source["chunk_id"]: block for block in pack.blocks}
        self.assertEqual(
            by_id["Paper#c20"].source_role,
            "secondary_citation_reported_by_paper",
        )
        self.assertEqual(by_id["Paper#c88"].source_role, "author_interpretation")
        self.assertNotIn("mechanism_direct_evidence", by_id["Paper#c20"].facet_ids)
        self.assertNotIn("mechanism_direct_evidence", by_id["Paper#c88"].facet_ids)

    def test_source_role_gate_accepts_direct_mechanism_result(self) -> None:
        candidates = candidate_set(
            Candidate(
                "Paper#c44",
                "Paper",
                "Measured sludge conductivity stayed near zero, providing direct "
                "evidence that DIET was not dominant.",
                0.01,
                section_path="3. Results",
                block_type="paragraph",
            )
        )
        pack = build_structured_context_pack(
            candidates,
            "What direct mechanistic evidence distinguishes DIET from mediated transfer?",
            facets=None,
            retrieval_mode="single_paper_extraction",
        )
        self.assertEqual(pack.coverage_status, "complete")
        self.assertEqual(pack.missing_facets, ())
        self.assertIn("mechanism_direct_evidence", pack.blocks[0].facet_ids)

    def test_quantitative_performance_requires_numeric_direct_result(self) -> None:
        candidates = candidate_set(
            Candidate(
                "Paper#c38",
                "Paper",
                "Performance improved substantially relative to the control.",
                0.01,
                section_path="3. Results",
                block_type="paragraph",
            )
        )
        pack = build_structured_context_pack(
            candidates,
            "What quantitative outcomes were reported?",
            facets=None,
            retrieval_mode="single_paper_extraction",
        )
        self.assertEqual(pack.coverage_status, "partial")
        self.assertIn("quantitative_performance", pack.missing_facets)


if __name__ == "__main__":
    unittest.main(verbosity=2)
