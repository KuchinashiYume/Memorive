"""RETRIEVAL immutable span, policy, coverage, and holdout guards."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from memorive.documents.registry import build_envelope, canonical_json, parent_link
from memorive.research.retrieval.coverage_artifacts import ArtifactPointer, ChunkSnapshot, FacetCoverageInput, FacetSpec, FrozenArtifactRecord, MemberRole, EvidenceArtifactValidationError, derive_facet_coverage_inputs, validate_verification_payload
from memorive.research.retrieval.coverage_artifacts import admit_chunk_snapshots, build_facet_definition_artifact, build_logical_span_artifact, build_references_bias_policy, build_retrieval_coverage_assessment, build_source_role_assessment, build_source_role_policy, build_span_assembly_policy, validate_artifact_graph, validate_holdout_public_manifest
from memorive.research.retrieval.types import LogicalSpan


NOW = "2026-07-18T14:45:00+08:00"
RUN = "logical-span-coverage-v1"
ROUTE = "route:none/offline"


def _pointer(
    label: str,
    artifact_type: str,
    *,
    authority: str = "frozen-replay",
    approved: bool = True,
    fixture_only: bool = False,
    content_hash: str | None = None,
) -> ArtifactPointer:
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()
    return ArtifactPointer(
        artifact_id=f"art_{digest[:32]}",
        content_hash=content_hash or digest,
        artifact_type=artifact_type,
        authority=authority,
        frozen=True,
        approved=approved,
        fixture_only=fixture_only,
    )


class T6CoverageArtifactsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _path(self, name: str) -> Path:
        return self.root / name

    def _core(self, *, reference_roles: bool = False) -> dict:
        chunks = [
            ChunkSnapshot.from_content(
                chunk_ref=_pointer(
                    "chunk-24", "chunk",
                    content_hash=hashlib.sha256("Table 1. Performance summary".encode("utf-8")).hexdigest(),
                ),
                chunk_id="Paper#c24",
                paper_id="Paper",
                content="Table 1. Performance summary",
                section_path="Results/Table1",
                block_type="table_caption",
                page_start=4,
                page_end=4,
                sequence_index=24,
            ),
            ChunkSnapshot.from_content(
                chunk_ref=_pointer(
                    "chunk-25", "chunk",
                    content_hash=hashlib.sha256("| metric | value |\n| yield | 91% |".encode("utf-8")).hexdigest(),
                ),
                chunk_id="Paper#c25",
                paper_id="Paper",
                content="| metric | value |\n| yield | 91% |",
                section_path="Results/Table1",
                block_type="table",
                page_start=4,
                page_end=4,
                sequence_index=25,
            ),
        ]
        span_policy = build_span_assembly_policy(
            policy_version="1.0",
            producer_version="verification-a0",
            run_ref=RUN,
            route_snapshot_ref=ROUTE,
            created_at=NOW,
            locator_path=self._path("span-policy.json"),
        )
        runtime_span = LogicalSpan(
            logical_span_id="S-fixture",
            member_chunk_ids=("Paper#c24", "Paper#c25"),
            content="Table 1. Performance summary\n| metric | value |\n| yield | 91% |",
            paper_id="Paper",
            section_path="Results/Table1",
            logical_role="table_with_footnotes",
            block_type="table",
            page_start=4,
            page_end=4,
            distance=0.1,
            facet_ids=("performance", "method"),
            retrieval_reasons=("fixture",),
            source_role="mixed",
        )
        span = build_logical_span_artifact(
            runtime_span,
            members=chunks,
            span_policy=span_policy,
            producer_version="verification-a0",
            run_ref=RUN,
            route_snapshot_ref=ROUTE,
            created_at=NOW,
            locator_path=self._path("span.json"),
        )
        role_policy = build_source_role_policy(
            policy_version="1.0",
            classification_rules=({"rule": "structure-first"},),
            confidence_rules=({"rule": "unknown-on-conflict"},),
            producer_version="verification-a0",
            run_ref=RUN,
            route_snapshot_ref=ROUTE,
            created_at=NOW,
            locator_path=self._path("role-policy.json"),
        )
        role = "references" if reference_roles else "table"
        role_assessment = build_source_role_assessment(
            subject_ref=span.as_pointer(),
            subject_kind="logical_span",
            subject_artifact=span,
            policy=role_policy,
            member_roles=tuple(
                MemberRole(item.chunk_ref, role, 0.99, ("fixture:structure",))
                for item in chunks
            ),
            producer_version="verification-a0",
            run_ref=RUN,
            route_snapshot_ref=ROUTE,
            computed_at=NOW,
            locator_path=self._path("role.json"),
        )
        references_policy = build_references_bias_policy(
            policy_version="1.0",
            section_confidence_threshold=0.9,
            entry_pattern_confidence_threshold=0.9,
            exclusion_rules=({"rule": "dual-high-confidence"},),
            producer_version="verification-a0",
            run_ref=RUN,
            route_snapshot_ref=ROUTE,
            created_at=NOW,
            locator_path=self._path("references-policy.json"),
        )
        question_ref = _pointer("question", "question_snapshot")
        facet_policy_ref = _pointer("facet-policy", "facet_definition_policy")
        facet_definition = build_facet_definition_artifact(
            question_ref=question_ref,
            question="What method and performance are reported?",
            source="frozen_fixture",
            policy_ref=facet_policy_ref,
            facets=(
                FacetSpec("method", "reported method"),
                FacetSpec("performance", "reported performance"),
            ),
            producer_version="verification-a0",
            run_ref=RUN,
            route_snapshot_ref=ROUTE,
            created_at=NOW,
            locator_path=self._path("facets.json"),
        )
        return {
            "chunks": chunks,
            "runtime_span": runtime_span,
            "span_policy": span_policy,
            "span": span,
            "role_policy": role_policy,
            "role_assessment": role_assessment,
            "references_policy": references_policy,
            "question_ref": question_ref,
            "facet_policy_ref": facet_policy_ref,
            "facet_definition": facet_definition,
            "context_pack_ref": _pointer("context-pack", "context_pack"),
            "summary_policy_ref": _pointer("summary-policy", "coverage_summary_policy"),
            "answer_policy_ref": _pointer("answer-policy", "answer_complete_policy"),
            "required_slot_ref": _pointer(
                "required-slot", "required_slot_assessment",
                authority="test-fixture", fixture_only=True,
            ),
            "fact_sidecar_ref": _pointer(
                "fact-sidecar", "fact_identity_sidecar",
                authority="test-fixture", fixture_only=True,
            ),
        }

    def _coverage(self, core: dict, results, **overrides) -> FrozenArtifactRecord:
        values = {
            "context_pack_ref": core["context_pack_ref"],
            "facet_definition": core["facet_definition"],
            "source_role_assessment": core["role_assessment"],
            "references_bias_policy": core["references_policy"],
            "summary_policy_ref": core["summary_policy_ref"],
            "answer_complete_policy_ref": core["answer_policy_ref"],
            "facet_results": results,
            "unique_chunk_count": 2,
            "unique_section_count": 1,
            "excluded_candidate_refs": (),
            "unused_capacity_reason": None,
            "required_slot_assessment_ref": core["required_slot_ref"],
            "fact_identity_sidecar_ref": core["fact_sidecar_ref"],
            "producer_version": "verification-a0",
            "run_ref": RUN,
            "route_snapshot_ref": ROUTE,
            "computed_at": NOW,
            "locator_path": self._path("coverage.json"),
            "allow_fixture_refs": True,
        }
        values.update(overrides)
        return build_retrieval_coverage_assessment(**values)

    def test_logical_span_preserves_members_and_has_no_role_fields(self) -> None:
        core = self._core()
        payload = core["span"].payload
        self.assertEqual(payload["member_chunk_ids"], ["Paper#c24", "Paper#c25"])
        self.assertEqual(payload["primary_source_id"], "Paper#c24")
        self.assertNotIn("source_role", payload)
        self.assertTrue(all(set(item) == {"chunk_ref", "original_boundary"} for item in payload["member_provenance"]))
        self.assertTrue(all("role" not in item for item in payload["member_provenance"]))
        self.assertEqual(
            core["span"].content_hash,
            hashlib.sha256(core["span"].payload_json.encode("utf-8")).hexdigest(),
        )

    def test_exact_paragraph_slice_assembly_preserves_canonical_hash(self) -> None:
        paragraph = "Biogas production increased while methane production remained stable."
        split = paragraph.index("while")
        parts = ((0, split), (split, len(paragraph)))
        chunks = []
        for index, (start, end) in enumerate(parts):
            content = paragraph[start:end]
            chunks.append(ChunkSnapshot.from_content(
                chunk_ref=_pointer(
                    f"slice-{index}", "chunk",
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                ),
                chunk_id=f"Paper#c{index + 1}", paper_id="Paper", content=content,
                section_path="Results", block_type="paragraph", page_start=1,
                page_end=1, sequence_index=index, paragraph_start=start,
                paragraph_end=end,
            ))
        policy = build_span_assembly_policy(
            policy_version="1.1", producer_version="verification-a1-repair", run_ref=RUN,
            route_snapshot_ref=ROUTE, created_at=NOW,
            locator_path=self._path("exact-slice-policy.json"), assembly_separator="",
        )
        runtime = LogicalSpan(
            logical_span_id="S-exact", member_chunk_ids=tuple(item.chunk_id for item in chunks),
            content=paragraph, paper_id="Paper", section_path="Results",
            logical_role="paragraph_continuation", block_type="paragraph",
            page_start=1, page_end=1, distance=0.0,
            facet_ids=("biogas", "methane"), retrieval_reasons=("exact-slices",),
            source_role="body_text",
        )
        span = build_logical_span_artifact(
            runtime, members=chunks, span_policy=policy, producer_version="verification-a1-repair",
            run_ref=RUN, route_snapshot_ref=ROUTE, created_at=NOW,
            locator_path=self._path("exact-slice-span.json"),
        )
        self.assertEqual(span.payload["content"], paragraph)
        self.assertEqual(span.payload["text_hash"], hashlib.sha256(paragraph.encode("utf-8")).hexdigest())

    def test_source_inventory_rejects_poisoned_member_before_context_admission(self) -> None:
        valid_text = "Valid frozen source text."
        forged_text = "Forged cross-paper text."
        candidates = (
            ChunkSnapshot.from_content(
                chunk_ref=_pointer("valid", "chunk", content_hash=hashlib.sha256(valid_text.encode()).hexdigest()),
                chunk_id="Paper#c1", paper_id="Paper", content=valid_text,
                section_path="Results", block_type="paragraph", page_start=1,
                page_end=1, sequence_index=1,
            ),
            ChunkSnapshot.from_content(
                chunk_ref=_pointer("forged", "chunk", content_hash=hashlib.sha256(forged_text.encode()).hexdigest()),
                chunk_id="OtherPaper#c9999", paper_id="Paper", content=forged_text,
                section_path="Results", block_type="paragraph", page_start=1,
                page_end=1, sequence_index=2,
            ),
        )
        admitted, dispositions = admit_chunk_snapshots(
            candidates,
            expected_paper_id="Paper",
            frozen_inventory=({
                "chunk_id": "Paper#c1", "paper_id": "Paper",
                "text_sha256": hashlib.sha256(valid_text.encode()).hexdigest(),
            },),
        )
        self.assertEqual([item.chunk_id for item in admitted], ["Paper#c1"])
        self.assertEqual(dispositions[1].status, "rejected")
        self.assertIn("SOURCE_ID_NOT_IN_FROZEN_INVENTORY", dispositions[1].reason_codes)

    def test_discriminative_facet_selector_prevents_sibling_evidence_bleed(self) -> None:
        texts = (
            "Single cell pair rate ranking reached 317 units. ",
            "Repeated simulations reported stable rates.",
        )
        chunks = []
        cursor = 0
        for index, content in enumerate(texts):
            chunks.append(ChunkSnapshot.from_content(
                chunk_ref=_pointer(
                    f"facet-{index}", "chunk",
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                ),
                chunk_id=f"Paper#c{index + 1}", paper_id="Paper", content=content,
                section_path="Results", block_type="paragraph", page_start=1,
                page_end=1, sequence_index=index, paragraph_start=cursor,
                paragraph_end=cursor + len(content),
            ))
            cursor += len(content)
        results = derive_facet_coverage_inputs(
            (
                FacetSpec("single_cell_pair_rate_ranking", "single cell pair rate ranking"),
                FacetSpec("multicellular_system_rate_comparison", "multicellular system rate comparison"),
            ),
            chunks,
        )
        self.assertEqual(results[0].status, "covered")
        self.assertEqual([ref.artifact_id for ref in results[0].supporting_source_refs], [chunks[0].chunk_ref.artifact_id])
        self.assertEqual(results[1].status, "missing")
        self.assertEqual(results[1].supporting_source_refs, ())

    def test_minimal_clause_ranges_do_not_join_remote_keyword_hits(self) -> None:
        paragraph = (
            "Compared to controls, the treated reactors showed increased methane production rates by 4 units. "
            "The lag times decreased by 2 days. Unrelated cumulative conversion discussion followed."
        )
        split = paragraph.index(" Unrelated")
        chunks = []
        for index, (start, end) in enumerate(((0, split), (split, len(paragraph)))):
            content = paragraph[start:end]
            chunks.append(ChunkSnapshot.from_content(
                chunk_ref=_pointer(
                    f"minimal-range-{index}", "chunk",
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                ),
                chunk_id=f"Paper#c{index + 1}", paper_id="Paper", content=content,
                section_path="Results", block_type="paragraph", page_start=1,
                page_end=1, sequence_index=index, paragraph_start=start,
                paragraph_end=end,
            ))
        result = derive_facet_coverage_inputs(
            (FacetSpec("rate_lag_response", "treated methane production rate and lag time response"),),
            chunks,
        )[0]
        first = paragraph.index("the treated reactors")
        first_end = paragraph.index(". ", first) + 1
        second = paragraph.index("The lag times")
        second_end = paragraph.index(". ", second) + 1
        self.assertEqual(result.evidence_char_ranges, ((first, first_end), (second, second_end)))
        self.assertEqual(
            [item.artifact_id for item in result.supporting_source_refs],
            [chunks[0].chunk_ref.artifact_id],
        )

    def test_single_paragraph_uncovered_facet_is_not_assessed_when_requested(self) -> None:
        text = "Methane yield increased by 10 percent."
        chunk = ChunkSnapshot.from_content(
            chunk_ref=_pointer("single-paragraph", "chunk", content_hash=hashlib.sha256(text.encode()).hexdigest()),
            chunk_id="Paper#c1", paper_id="Paper", content=text,
            section_path="Results", block_type="paragraph", page_start=1,
            page_end=1, sequence_index=1, paragraph_start=0, paragraph_end=len(text),
        )
        result = derive_facet_coverage_inputs(
            (FacetSpec("lag_time_comparison", "lag time comparison"),),
            (chunk,),
            uncovered_status="not_assessed",
        )[0]
        self.assertEqual(result.status, "not_assessed")
        self.assertEqual(result.supporting_source_refs, ())
        self.assertEqual(result.evidence_char_ranges, ())

    def test_explicit_source_authority_is_independent_of_fixture_transport(self) -> None:
        core = self._core()
        results = (
            FacetCoverageInput("method", "covered", (core["chunks"][0].chunk_ref,)),
            FacetCoverageInput("performance", "not_assessed", ()),
        )
        real = self._coverage(core, results, source_non_authoritative=False)
        synthetic = self._coverage(core, results, source_non_authoritative=True)
        self.assertFalse(real.payload["non_authoritative"])
        self.assertTrue(synthetic.payload["non_authoritative"])
        self.assertEqual(real.payload["retrieval_coverage_status"], "partial")
        self.assertNotIn("performance", real.payload["missing_facet_ids"])

    def test_modified_chunk_cross_section_and_non_neighbour_fusion_are_rejected(self) -> None:
        core = self._core()
        with self.assertRaisesRegex(EvidenceArtifactValidationError, "modifies members"):
            build_logical_span_artifact(
                replace(core["runtime_span"], content=core["runtime_span"].content + " altered"),
                members=core["chunks"], span_policy=core["span_policy"],
                producer_version="verification-a0", run_ref=RUN, route_snapshot_ref=ROUTE,
                created_at=NOW, locator_path=self._path("bad-content.json"),
            )
        cross_section = replace(core["chunks"][1], section_path="Discussion")
        with self.assertRaisesRegex(EvidenceArtifactValidationError, "cross-section"):
            build_logical_span_artifact(
                core["runtime_span"], members=(core["chunks"][0], cross_section),
                span_policy=core["span_policy"], producer_version="verification-a0",
                run_ref=RUN, route_snapshot_ref=ROUTE, created_at=NOW,
                locator_path=self._path("bad-section.json"),
            )
        non_neighbour = replace(core["chunks"][1], sequence_index=30)
        with self.assertRaisesRegex(EvidenceArtifactValidationError, "strict structural neighbours"):
            build_logical_span_artifact(
                core["runtime_span"], members=(core["chunks"][0], non_neighbour),
                span_policy=core["span_policy"], producer_version="verification-a0",
                run_ref=RUN, route_snapshot_ref=ROUTE, created_at=NOW,
                locator_path=self._path("bad-neighbour.json"),
            )

    def test_role_and_references_policies_are_separate_and_updates_append(self) -> None:
        core = self._core()
        old_policy_bytes = core["role_policy"].payload_json
        successor = build_source_role_policy(
            policy_version="1.1",
            classification_rules=({"rule": "structure-first-v2"},),
            confidence_rules=({"rule": "unknown-on-conflict"},),
            producer_version="verification-a0",
            run_ref=RUN,
            route_snapshot_ref=ROUTE,
            created_at=NOW,
            locator_path=self._path("role-policy-v2.json"),
            supersedes=core["role_policy"].as_pointer(),
        )
        self.assertNotEqual(core["role_policy"].artifact_id, successor.artifact_id)
        self.assertEqual(core["role_policy"].payload_json, old_policy_bytes)
        with self.assertRaisesRegex(EvidenceArtifactValidationError, "SourceRolePolicy"):
            build_source_role_assessment(
                subject_ref=core["span"].as_pointer(), subject_kind="logical_span",
                policy=core["references_policy"],
                member_roles=(MemberRole(core["chunks"][0].chunk_ref, "table", 1.0),),
                producer_version="verification-a0", run_ref=RUN, route_snapshot_ref=ROUTE,
                computed_at=NOW, locator_path=self._path("policy-mixup.json"),
            )

    def test_chunk_count_never_substitutes_for_facet_coverage_and_drift_is_rejected(self) -> None:
        core = self._core()
        coverage = self._coverage(
            core,
            (
                FacetCoverageInput("method", "covered", (core["chunks"][0].chunk_ref,)),
                FacetCoverageInput("performance", "missing", ()),
            ),
            unique_chunk_count=999,
        )
        self.assertEqual(coverage.payload["retrieval_coverage_status"], "partial")
        self.assertFalse(coverage.payload["answer_complete"])
        self.assertEqual(coverage.payload["missing_facet_ids"], ["performance"])
        drifted = [dict(item) for item in core["facet_definition"].payload["facets"]]
        drifted[0]["text"] = "silently changed"
        with self.assertRaisesRegex(EvidenceArtifactValidationError, "drifted"):
            self._coverage(core, (), query_facets_projection=drifted)

    def test_references_only_support_cannot_be_sufficient(self) -> None:
        core = self._core(reference_roles=True)
        coverage = self._coverage(
            core,
            (
                FacetCoverageInput("method", "covered", (core["chunks"][0].chunk_ref,)),
                FacetCoverageInput("performance", "covered", (core["chunks"][1].chunk_ref,)),
            ),
        )
        self.assertEqual(coverage.payload["retrieval_coverage_status"], "partial")
        self.assertTrue(coverage.payload["references_only_warning"])
        self.assertFalse(coverage.payload["answer_complete"])

    def test_missing_upstream_refs_force_not_assessed_without_fabrication(self) -> None:
        core = self._core()
        coverage = self._coverage(
            core,
            (),
            required_slot_assessment_ref=None,
            fact_identity_sidecar_ref=None,
            allow_fixture_refs=False,
        )
        payload = coverage.payload
        self.assertEqual(payload["retrieval_coverage_status"], "not_assessed")
        self.assertIsNone(payload["answer_complete"])
        self.assertEqual(payload["intake_context_status"], "not_assessed")
        self.assertIsNone(payload["required_slot_assessment_ref"])
        self.assertIsNone(payload["fact_identity_sidecar_ref"])
        self.assertEqual({row["status"] for row in payload["facet_coverage"]}, {"not_assessed"})

    def test_schema_rejects_missing_producer(self) -> None:
        core = self._core()
        coverage = self._coverage(
            core,
            (
                FacetCoverageInput("method", "covered", (core["chunks"][0].chunk_ref,)),
                FacetCoverageInput("performance", "covered", (core["chunks"][1].chunk_ref,)),
            ),
        )
        tampered = coverage.payload
        tampered.pop("producer_module")
        with self.assertRaisesRegex(EvidenceArtifactValidationError, "fields mismatch"):
            validate_verification_payload(tampered)

    def test_parent_graph_rejects_cycle_and_accepts_fixed_chain(self) -> None:
        core = self._core()
        coverage = self._coverage(
            core,
            (
                FacetCoverageInput("method", "covered", (core["chunks"][0].chunk_ref,)),
                FacetCoverageInput("performance", "covered", (core["chunks"][1].chunk_ref,)),
            ),
        )
        records = (
            core["span_policy"], core["span"], core["role_policy"],
            core["role_assessment"], core["references_policy"],
            core["facet_definition"], coverage,
        )
        externals = [
            *(item.chunk_ref for item in core["chunks"]), core["question_ref"],
            core["facet_policy_ref"], core["context_pack_ref"],
            core["summary_policy_ref"], core["answer_policy_ref"],
            core["required_slot_ref"], core["fact_sidecar_ref"],
        ]
        validate_artifact_graph(records, external_pointers=externals)

        first, second = core["span_policy"], core["role_policy"]
        cyclic = []
        for record, parent in ((first, second), (second, first)):
            envelope = build_envelope(
                artifact_id=record.artifact_id,
                artifact_type=record.artifact_type,
                schema_ref=record.envelope["schema_ref"],
                content_hash=record.content_hash,
                locator_path=self._path(f"{record.artifact_id}.json"),
                run_ref=RUN,
                route_snapshot_ref=ROUTE,
                paper_ids=(),
                source_artifact_ids=(parent.artifact_id,),
                created_at=NOW,
                registered_at=NOW,
                parent_artifacts=(parent_link(
                    parent_artifact_id=parent.artifact_id,
                    parent_content_hash=parent.content_hash,
                    relation="cycle_fixture",
                    evidence_kind="test_fixture",
                    source_field="parent",
                    evidence_ref=RUN,
                ),),
                unresolved_parent_requirements=(),
                conflicting_candidates=(),
                legacy_import=False,
                evidence_refs=(RUN,),
                metadata={"fixture_only": True},
            )
            cyclic.append(FrozenArtifactRecord(record.payload_json, canonical_json(envelope)))
        with self.assertRaisesRegex(EvidenceArtifactValidationError, "cycle"):
            validate_artifact_graph(cyclic)

    def test_public_holdout_manifest_has_only_opaque_commitments(self) -> None:
        manifest = {
            "manifest_id": "model_evaluation-verification-holdout-public-v1",
            "opaque_fixture_ids": ["fx_0123456789abcdef0123456789abcdef"],
            "count": 1,
            "commitment_policy_ref": "Memorive-HOLDOUT-COMMITMENT-v1",
            "commitment_algorithm": "sha256-canonical-bytes-plus-secret-nonce-v1",
            "input_package_commitment": "1" * 64,
            "gold_package_commitment": "2" * 64,
            "curator_role": "independent_curator",
            "sealed_at": NOW,
        }
        validate_holdout_public_manifest(manifest)
        leaked = dict(manifest, paper_id="Paper")
        with self.assertRaisesRegex(EvidenceArtifactValidationError, "fields mismatch"):
            validate_holdout_public_manifest(leaked)


if __name__ == "__main__":
    unittest.main()
