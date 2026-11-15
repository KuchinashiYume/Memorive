"""RESEARCH_ANALYSIS producer attribution and immutable legacy projection tests."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from retrieval.coverage_artifacts import ArtifactPointer, ChunkSnapshot, FacetCoverageInput, FacetSpec, MemberRole
from retrieval.coverage_artifacts import build_facet_definition_artifact, build_references_bias_policy, build_logical_span_artifact, build_retrieval_coverage_assessment, build_source_role_assessment, build_source_role_policy, build_span_assembly_policy
from retrieval.types import LogicalSpan
from research_analysis.retrieval_quality_research_analysis_quality_artifacts import AnalysisQualityValidationError, build_analysis_quality_assessment, project_coverage_onto_analysis
from research_analysis.types import AnalysisResult, DISCLAIMER, LiteratureClaim


NOW = "2026-07-18T14:45:00+08:00"
RUN = "logical-span-coverage-v1"
ROUTE = "route:none/offline"


def _pointer(label: str, artifact_type: str, *, fixture: bool = False) -> ArtifactPointer:
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()
    return ArtifactPointer(
        artifact_id=f"art_{digest[:32]}",
        content_hash=digest,
        artifact_type=artifact_type,
        authority="test-fixture" if fixture else "frozen-replay",
        frozen=True,
        approved=True,
        fixture_only=fixture,
    )


class T6QualityArtifactsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _coverage(self, *, status: str = "covered"):
        chunk_text = "The result is supported."
        chunk = ArtifactPointer(
            artifact_id=_pointer("chunk", "chunk").artifact_id,
            content_hash=hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
            artifact_type="chunk", authority="frozen-replay", frozen=True,
            approved=True, fixture_only=False,
        )
        subject = _pointer("pack", "context_pack")
        snapshot = ChunkSnapshot.from_content(
            chunk_ref=chunk, chunk_id="Paper#c1", paper_id="Paper",
            content=chunk_text, section_path="Results", block_type="paragraph",
            page_start=1, page_end=1, sequence_index=1,
        )
        span_policy = build_span_assembly_policy(
            policy_version="1.0", producer_version="verification-a0", run_ref=RUN,
            route_snapshot_ref=ROUTE, created_at=NOW,
            locator_path=self.root / "span-policy.json",
        )
        runtime_span = LogicalSpan(
            logical_span_id="S-quality-fixture", member_chunk_ids=("Paper#c1",),
            content=chunk_text, paper_id="Paper", section_path="Results",
            logical_role="single_chunk", block_type="paragraph", page_start=1,
            page_end=1, distance=0.0, facet_ids=("support",),
            retrieval_reasons=("fixture",), source_role="body_text",
        )
        span = build_logical_span_artifact(
            runtime_span, members=(snapshot,), span_policy=span_policy,
            producer_version="verification-a0", run_ref=RUN, route_snapshot_ref=ROUTE,
            created_at=NOW, locator_path=self.root / "span.json",
        )
        role_policy = build_source_role_policy(
            policy_version="1.0", classification_rules=({"rule": "structure"},),
            confidence_rules=({"rule": "explicit"},), producer_version="verification-a0",
            run_ref=RUN, route_snapshot_ref=ROUTE, created_at=NOW,
            locator_path=self.root / "role-policy.json",
        )
        role = build_source_role_assessment(
            subject_ref=span.as_pointer(), subject_kind="logical_span", subject_artifact=span,
            policy=role_policy,
            member_roles=(MemberRole(chunk, "body_text", 1.0, ("fixture",)),),
            producer_version="verification-a0", run_ref=RUN, route_snapshot_ref=ROUTE,
            computed_at=NOW, locator_path=self.root / "role.json",
        )
        references = build_references_bias_policy(
            policy_version="1.0", section_confidence_threshold=0.9,
            entry_pattern_confidence_threshold=0.9,
            exclusion_rules=({"rule": "dual"},), producer_version="verification-a0",
            run_ref=RUN, route_snapshot_ref=ROUTE, created_at=NOW,
            locator_path=self.root / "references.json",
        )
        facets = build_facet_definition_artifact(
            question_ref=_pointer("question", "question_snapshot"),
            question="What is supported?", source="frozen_fixture",
            policy_ref=_pointer("facet-policy", "facet_definition_policy"),
            facets=(FacetSpec("support", "supported answer"),),
            producer_version="verification-a0", run_ref=RUN, route_snapshot_ref=ROUTE,
            created_at=NOW, locator_path=self.root / "facets.json",
        )
        coverage = build_retrieval_coverage_assessment(
            context_pack_ref=subject,
            facet_definition=facets,
            source_role_assessment=role,
            references_bias_policy=references,
            summary_policy_ref=_pointer("summary", "coverage_summary_policy"),
            answer_complete_policy_ref=_pointer("answer", "answer_complete_policy"),
            facet_results=(FacetCoverageInput("support", status, (chunk,) if status != "missing" else ()),),
            unique_chunk_count=1,
            unique_section_count=1,
            excluded_candidate_refs=(),
            unused_capacity_reason=None,
            required_slot_assessment_ref=_pointer("required", "required_slot_assessment", fixture=True),
            fact_identity_sidecar_ref=_pointer("sidecar", "fact_identity_sidecar", fixture=True),
            producer_version="verification-a0",
            run_ref=RUN,
            route_snapshot_ref=ROUTE,
            computed_at=NOW,
            locator_path=self.root / f"coverage-{status}.json",
            allow_fixture_refs=True,
        )
        return chunk, coverage, span

    def _analysis(self, chunk_id: str = "Paper#c1", *, ai=()) -> AnalysisResult:
        return AnalysisResult(
            question="What is supported?",
            literature_support=(LiteratureClaim(
                text="The result is supported.",
                source_id={"chunk_id": chunk_id, "paper_id": "Paper", "field": "results"},
                origin="direct_result",
                facet_ids=("support",),
            ),),
            ai_general_knowledge=tuple(ai),
            risks_and_uncertainties=(),
            recheck_locations=(),
            flagged_unsourced=(),
            disclaimer=DISCLAIMER,
            analysis_model="fixture",
            analyzed_at=NOW,
            quality_metrics={
                "direct_answer_ratio": 1.0,
                "subquestion_coverage": {"covered": 1, "partial": 0, "missing": 0},
                "source_dominance": {"max_claims_from_one_span": 1, "max_share_from_one_span": 1.0},
            },
        )

    def test_m4_builds_separate_assessment_and_projection_without_writeback(self) -> None:
        _, coverage, span = self._coverage(status="covered")
        original_coverage = coverage.payload_json
        original_analysis = self._analysis()
        projected = project_coverage_onto_analysis(original_analysis, coverage)
        self.assertEqual(original_analysis.coverage_status, "not_assessed")
        self.assertIsNone(original_analysis.coverage_assessment_ref)
        self.assertEqual(projected.coverage_status, "sufficient")
        self.assertEqual(projected.coverage_assessment_ref, coverage.artifact_id)
        self.assertEqual(coverage.payload_json, original_coverage)

        quality = build_analysis_quality_assessment(
            projected,
            analysis_artifact_ref=_pointer("analysis", "analysis"),
            retrieval_coverage=coverage,
            method_policy_ref=_pointer("quality-policy", "analysis_quality_policy"),
            source_authority_artifacts=(span,),
            producer_version="verification-a0",
            run_ref=RUN,
            route_snapshot_ref=ROUTE,
            computed_at=NOW,
            locator_path=self.root / "quality.json",
        )
        self.assertEqual(quality.payload["producer_module"], "RESEARCH_ANALYSIS")
        self.assertEqual(set(quality.payload["field_provenance"].values()), {"RESEARCH_ANALYSIS"})
        self.assertEqual(quality.payload["retrieval_coverage_ref"]["artifact_id"], coverage.artifact_id)
        self.assertEqual(coverage.payload["producer_module"], "RETRIEVAL")

    def test_invalid_source_id_is_rejected_before_quality_persistence(self) -> None:
        _, coverage, span = self._coverage(status="covered")
        with self.assertRaisesRegex(AnalysisQualityValidationError, "not in immutable RETRIEVAL source authority"):
            build_analysis_quality_assessment(
                self._analysis(chunk_id="Paper#forged"),
                analysis_artifact_ref=_pointer("analysis", "analysis"),
                retrieval_coverage=coverage,
                method_policy_ref=_pointer("quality-policy", "analysis_quality_policy"),
                source_authority_artifacts=(span,),
                producer_version="verification-a0", run_ref=RUN, route_snapshot_ref=ROUTE,
                computed_at=NOW, locator_path=self.root / "invalid.json",
            )

    def test_partial_projection_refuses_ai_general_filler(self) -> None:
        _, coverage, _ = self._coverage(status="missing")
        with self.assertRaisesRegex(AnalysisQualityValidationError, "suppress ai_general_knowledge"):
            project_coverage_onto_analysis(self._analysis(ai=("generic filler",)), coverage)
        safe = replace(self._analysis(ai=("generic filler",)), ai_general_knowledge=())
        projected = project_coverage_onto_analysis(safe, coverage)
        self.assertEqual(projected.coverage_status, "insufficient")
        self.assertFalse(projected.answer_complete)


if __name__ == "__main__":
    unittest.main()
