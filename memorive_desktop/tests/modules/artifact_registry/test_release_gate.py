"""Memorive ANALYSIS-ADMISSION/CROSS six-scenario release-gate regression."""

from __future__ import annotations

import copy
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from run_ledger.analysis_admission_runtime_log_publication_preview import build_publication_candidate_manifest, build_publication_dry_run
from run_ledger.errors import RealPublicationForbidden
from run_ledger.analysis_admission_runtime_log_publication_preview import append_publication_ledger, execute_publication, update_canonical_or_latest_pointer
from memorive.documents.registry import CompatibilityPolicy, SandboxBoundaryViolation, validate_envelope
from memorive.documents.registry import unresolved_requirement
from memorive.documents.registry.analysis_products import artifact_ref
from memorive.documents.registry import build_compatibility_assessment
from memorive.documents.registry.analysis_materialization import SandboxBoundary
from memorive.documents.registry.analysis_materialization import ArtifactFactory
from tests.modules.artifact_registry.release_gate_fixtures import (
    SyntheticGraph,
    build_synthetic_graph,
    load_json,
)
from knowledge_admission.analysis_admission_knowledge_admission_analysis_admission import build_analysis_admission_request, validate_real_downstream_eligibility
from knowledge_admission.errors import AdmissionBlocked, RealStateTransitionForbidden
from knowledge_admission.analysis_admission_knowledge_admission_analysis_admission import execute_state_transition
REGISTERED_AT = "2026-07-17T12:49:04+08:00"
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "release_gate"
POLICY_PATH = (
    FIXTURE_ROOT / "compatibility_policy_v1.json"
)
GRAPH_PATH = (
    FIXTURE_ROOT / "synthetic_graph_fixture_v1.json"
)
STATE_PATH = (
    FIXTURE_ROOT
    / "analysis_state_contract_fixture_v1.json"
)
EVIDENCE_PATH = (
    FIXTURE_ROOT
    / "admission_publication_evidence_fixture_v1.json"
)


@dataclass(frozen=True)
class ScenarioContext:
    factory: ArtifactFactory
    graph: SyntheticGraph
    policy_snapshot: dict[str, Any]
    registry_snapshot: dict[str, Any]


class QualityT04ReleaseGateTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="memorive-analysis_admission-")
        self.addCleanup(self._temp.cleanup)
        self.run_root = Path(self._temp.name).resolve(strict=False)
        repo_root = (Path(__file__).resolve().parents[3] / "source")
        self.boundary = SandboxBoundary.build(self.run_root, [repo_root])
        input_root = self.run_root / "inputs"
        input_root.mkdir(parents=True, exist_ok=True)
        copied_inputs: dict[Path, Path] = {}
        for source in (POLICY_PATH, GRAPH_PATH, STATE_PATH, EVIDENCE_PATH):
            target = input_root / source.name
            target.write_bytes(source.read_bytes())
            copied_inputs[source] = target
        self.policy_path = copied_inputs[POLICY_PATH]
        self.policy = CompatibilityPolicy.from_file(self.policy_path)
        self.graph_fixture = load_json(copied_inputs[GRAPH_PATH])
        self.state_contract = load_json(copied_inputs[STATE_PATH])
        self.evidence = load_json(copied_inputs[EVIDENCE_PATH])

    def _new_factory(self, name: str) -> ArtifactFactory:
        return ArtifactFactory(
            boundary=self.boundary,
            scenario_root=self.run_root / "scenarios" / name,
            run_ref="analysis_admission-regression:" + name,
            registered_at=REGISTERED_AT,
        )

    def _context(self, name: str) -> ScenarioContext:
        factory = self._new_factory(name)
        graph = build_synthetic_graph(factory, self.graph_fixture)
        policy_snapshot = factory.create_policy_snapshot(
            self.policy_path,
            policy_id=self.policy.policy_id,
            policy_version=self.policy.policy_version,
            policy_schema_version=self.policy.schema_version,
            created_at=self.policy.created_at,
            evidence_refs=["test-policy:" + str(self.policy_path)],
        )
        registry_snapshot, _payload = factory.create_registry_snapshot(
            captured_at=REGISTERED_AT,
            evidence_refs=["test-run:analysis_admission-regression"],
        )
        return ScenarioContext(
            factory=factory,
            graph=graph,
            policy_snapshot=policy_snapshot,
            registry_snapshot=registry_snapshot,
        )

    def _assessment(
        self,
        context: ScenarioContext,
        *,
        card: dict[str, Any] | None = None,
        pack: dict[str, Any] | None | object = ...,
        analysis: dict[str, Any] | None = None,
    ):
        selected_pack = context.graph.pack_p1 if pack is ... else pack
        return build_compatibility_assessment(
            factory=context.factory,
            analysis=analysis or context.graph.analysis_a1,
            candidate_card=card or context.graph.card_c1,
            context_pack=selected_pack,
            policy=self.policy,
            registry_snapshot=context.registry_snapshot,
            policy_snapshot=context.policy_snapshot,
            assessed_at=REGISTERED_AT,
        )

    def _admission(
        self,
        context: ScenarioContext,
        assessment,
        **overrides: Any,
    ):
        values: dict[str, Any] = {
            "analysis": context.graph.analysis_a1,
            "candidate_card": context.graph.card_c1,
            "context_pack": context.graph.pack_p1,
            "state_contract": self.state_contract,
            "research_analysis_source_resolution": self.evidence["research_analysis_source_resolution"],
            "evidence_review_review": self.evidence["evidence_review_review"],
        }
        values.update(overrides)
        return build_analysis_admission_request(
            factory=context.factory,
            assessment=assessment,
            requested_at=REGISTERED_AT,
            **values,
        )

    def _manifest(
        self,
        context: ScenarioContext,
        assessment,
        admission,
        **overrides: Any,
    ):
        values: dict[str, Any] = {
            "analysis": context.graph.analysis_a1,
            "candidate_card": context.graph.card_c1,
            "context_pack": context.graph.pack_p1,
            "coverage": self.evidence["coverage"],
            "paper_outcome": self.evidence["paper_outcome"],
        }
        values.update(overrides)
        return build_publication_candidate_manifest(
            factory=context.factory,
            admission=admission,
            assessment=assessment,
            created_at=REGISTERED_AT,
            **values,
        )

    def test_01_complete_chain_is_buildable_but_not_real_eligible(self) -> None:
        context = self._context("positive")
        assessment = self._assessment(context)
        self.assertEqual(
            {axis["status"] for axis in assessment.payload["axes"].values()},
            {"compatible"},
        )
        self.assertEqual(assessment.payload["freshness"]["status"], "fresh")
        admission = self._admission(context, assessment)
        self.assertEqual(admission.payload["request_build_status"], "buildable")
        with self.assertRaises(AdmissionBlocked):
            validate_real_downstream_eligibility(admission, self.state_contract)
        with self.assertRaises(RealStateTransitionForbidden):
            execute_state_transition(admission)
        manifest = self._manifest(context, assessment, admission)
        preview = build_publication_dry_run(
            factory=context.factory,
            manifest=manifest,
            evaluated_at=REGISTERED_AT,
        )
        self.assertFalse(manifest.payload["eligible_for_real_publication"])
        self.assertEqual(
            set(preview.report.payload["writes_performed"].values()),
            {False},
        )
        self.assertFalse(
            preview.receipt_preview.payload["authoritative_receipt_created"]
        )
        for product in (
            assessment,
            admission,
            manifest,
            preview.report,
            preview.receipt_preview,
        ):
            validate_envelope(product.envelope)
            self.assertEqual(product.envelope["ledger_record_refs"], [])

    def test_02_explicit_supersedes_blocks_publication_manifest(self) -> None:
        context = self._context("explicit-stale")
        assessment = self._assessment(context, card=context.graph.card_c2)
        self.assertEqual(assessment.payload["freshness"]["status"], "stale")
        self.assertEqual(
            assessment.payload["freshness"]["stale_against"],
            artifact_ref(context.graph.card_c2),
        )
        admission = self._admission(
            context,
            assessment,
            candidate_card=context.graph.card_c2,
        )
        with self.assertRaises(AdmissionBlocked):
            self._manifest(
                context,
                assessment,
                admission,
                candidate_card=context.graph.card_c2,
            )

    def test_03_later_card_without_lineage_keeps_freshness_unknown(self) -> None:
        context = self._context("unknown-freshness")
        assessment = self._assessment(context, card=context.graph.card_c3)
        freshness = assessment.payload["freshness"]
        self.assertEqual(freshness["status"], "unknown")
        self.assertEqual(freshness["reason_codes"], ["NO_PERMITTED_FRESHNESS_EVIDENCE"])
        self.assertIsNone(freshness["stale_against"])
        self.assertTrue(assessment.payload["human_review_required"])

    def test_04_unresolved_lineage_and_missing_review_block_admission(self) -> None:
        context = self._context("unresolved-lineage")
        analysis = context.factory.create(
            artifact_type="analysis",
            payload={"kind": "unresolved-analysis"},
            schema_ref="memorive://analysis/v1",
            paper_ids=["paper-synthetic-001"],
            unresolved=[
                unresolved_requirement(
                    "exact_card_version",
                    "EXACT_CARD_VERSION_NOT_PROVEN",
                    evidence_refs=["test:unresolved-card"],
                ),
                unresolved_requirement(
                    "independent_context_pack",
                    "INDEPENDENT_CONTEXT_PACK_NOT_PERSISTED",
                    evidence_refs=["test:unresolved-pack"],
                ),
            ],
            evidence_refs=["test:unresolved-analysis"],
            semantic_key="unresolved-analysis",
        )
        assessment = self._assessment(context, analysis=analysis, pack=None)
        admission = self._admission(
            context,
            assessment,
            analysis=analysis,
            context_pack=None,
            state_contract=None,
            research_analysis_source_resolution={
                "all_literature_source_ids_resolve_to_pack": True,
                "evidence_ref": "test:research_analysis-source-resolution",
            },
            evidence_review_review=None,
        )
        reasons = set(admission.payload["reason_codes"])
        self.assertEqual(admission.payload["request_build_status"], "blocked")
        self.assertIn("ANALYSIS_SCOPE_STATE_CONTRACT_MISSING", reasons)
        self.assertIn("EVIDENCE_REVIEW_INDEPENDENT_REVIEW_EVIDENCE_MISSING", reasons)
        self.assertIn("ANALYSIS_EXACT_CARD_VERSION_UNRESOLVED", reasons)
        self.assertIn("ANALYSIS_INDEPENDENT_CONTEXT_PACK_UNRESOLVED", reasons)
        self.assertNotIn("RESEARCH_ANALYSIS_SOURCE_RESOLUTION_EVIDENCE_MISSING", reasons)
        with self.assertRaises(AdmissionBlocked):
            self._manifest(
                context,
                assessment,
                admission,
                analysis=analysis,
                context_pack=None,
            )

    def test_05_dimensions_are_orthogonal_and_scope_is_non_nullable(self) -> None:
        context = self._context("orthogonal-dimensions")
        bad_card = copy.deepcopy(context.graph.card_c1)
        bad_card["source_scope"]["paper_ids"] = ["paper-synthetic-001", None]
        bad_scope = self._assessment(context, card=bad_card)
        self.assertEqual(
            bad_scope.payload["axes"]["source_scope"]["status"],
            "incompatible",
        )
        chunk_only = self._assessment(context, pack=context.graph.chunk_only_pack)
        self.assertEqual(
            chunk_only.payload["axes"]["card_pack"]["status"],
            "not_applicable",
        )
        positive = self._assessment(context)
        admission = self._admission(context, positive)
        manifest = self._manifest(
            context,
            positive,
            admission,
            coverage={"status": "not_assessed", "evidence_refs": []},
        )
        dimensions = manifest.payload["dimensions"]
        self.assertEqual(dimensions["structural_compatibility"]["status"], "passed")
        self.assertEqual(dimensions["coverage"]["status"], "not_assessed")
        self.assertEqual(dimensions["paper_outcome"]["status"], "passed")
        self.assertEqual(dimensions["user_selection"]["status"], "pending_confirmation")

    def test_06_real_publication_surfaces_fail_before_registry_write(self) -> None:
        context = self._context("redlines")
        assessment = self._assessment(context)
        admission = self._admission(context, assessment)
        manifest = self._manifest(context, assessment, admission)
        before = len(context.factory.registry.read_events())
        with self.assertRaises(RealPublicationForbidden):
            build_publication_dry_run(
                factory=context.factory,
                manifest=manifest,
                evaluated_at=REGISTERED_AT,
                mode="real",
            )
        for forbidden in (
            execute_publication,
            update_canonical_or_latest_pointer,
            append_publication_ledger,
        ):
            with self.assertRaises(RealPublicationForbidden):
                forbidden(manifest)
        self.assertEqual(before, len(context.factory.registry.read_events()))

    def test_07_a_sandbox_that_overlaps_a_protected_root_is_refused(self) -> None:
        library = self.run_root / "library"
        with self.assertRaises(SandboxBoundaryViolation):
            SandboxBoundary.build(library / "run", [library])
        with self.assertRaises(SandboxBoundaryViolation):
            SandboxBoundary(sandbox_root=self.run_root, protected_roots=(library,))
        with self.assertRaises(SandboxBoundaryViolation):
            self.boundary.require_write_path(self.run_root.parent / "outside.json")

    def test_08_any_folder_apart_from_the_protected_roots_can_be_the_sandbox(self) -> None:
        # The sandbox is not tied to a drive or folder name: the caller picks it and the roots to protect.
        boundary = SandboxBoundary.build(self.run_root / "run", [self.run_root / "library"])
        self.assertEqual(boundary.sandbox_root, self.run_root / "run")
        self.assertEqual(boundary.protected_roots, (self.run_root / "library",))
        self.assertEqual(boundary.require_write_path(self.run_root / "run" / "a.json"), self.run_root / "run" / "a.json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
