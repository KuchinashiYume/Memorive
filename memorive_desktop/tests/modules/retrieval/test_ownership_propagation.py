"""DATA-OWNERSHIP offline ownership truth table, adversarial, replay, and full chain."""
from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


_TEMP_LOG = None
if not os.environ.get("MEMORIVE_RUNTIME_LOG_LOG_DIR"):
    _TEMP_LOG = tempfile.TemporaryDirectory(prefix="ownership_log_")
    os.environ["MEMORIVE_RUNTIME_LOG_LOG_DIR"] = _TEMP_LOG.name
    atexit.register(_TEMP_LOG.cleanup)

REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from jsonschema import Draft202012Validator

from memorive.research.retrieval.context_pack import build_context_pack, validate_context_pack_binding
from memorive.research.retrieval.ownership import EMPTY_CONTEXT_PACK, OWNERSHIP_AUTHORITY_INSUFFICIENT, OWNERSHIP_BINDING_MISMATCH, OWNERSHIP_CONFLICT, OWNERSHIP_DOWNGRADE_ATTEMPT, OWNERSHIP_INVALID, OWNERSHIP_LEGACY_ADAPTER_REQUIRED, OWNERSHIP_LOCAL_ROUTE_UNAVAILABLE, OWNERSHIP_MISSING, OWNERSHIP_PROJECTION_STALE, OWNERSHIP_VERSION_UNSUPPORTED, OwnershipAssertion, OwnershipContractError, OwnershipContributor, OwnershipGuard, OwnershipSnapshot, adapt_legacy_authorities, aggregate_blocks, aggregate_ownership, amendment_candidate, bind_authority_projection, compare_legacy_binding, ownership_cache_key, ownership_preflight, propagation_receipt, snapshot_fields, trace_ownership, validate_cache_binding, validate_snapshot
from memorive.research.retrieval.ownership import parse_snapshot_frontmatter, render_snapshot_frontmatter
from memorive.research.retrieval.structured_context import (
    _neighbor_candidate,
    build_card_anchor_candidates,
    build_logical_spans,
)
from memorive.research.retrieval.types import Candidate, CandidateSet, ContextBlock, ContextPack, QueryFacet
from memorive.research.analysis.analysis import (
    OwnershipFailClosed as M4OwnershipFailClosed,
    analyze,
    build_analysis_request_envelope,
)
from memorive.documents.card_review import build_ownership_review_task, verify_research_analysis
from memorive.documents.card_review.errors import OwnershipFailClosed as M6OwnershipFailClosed


def contributor(value: str | None, index: int = 1, *, subject: str | None = None):
    return OwnershipContributor(
        subject_ref=subject or f"chunk:Paper:c{index}",
        data_ownership=value,
        assertion_ref=f"OS-{index}",
        revision=1,
        basis_ref=f"basis:{index}",
    )


def snapshot(values, *, subject_ref="subject:test"):
    return aggregate_ownership(
        tuple(contributor(value, index + 1) for index, value in enumerate(values)),
        subject_ref=subject_ref,
    )


def candidate(
    value: str | None,
    index: int,
    *,
    projection: str | None | object = ...,
    projection_revision: int | None = 1,
    text: str | None = None,
) -> Candidate:
    cid = f"Paper#c{index}"
    if projection is ...:
        projection = value
    bound = bind_authority_projection(
        subject_ref=f"chunk:Paper:{cid}",
        authoritative_value=value,
        assertion_ref=f"OS-{index}",
        revision=1,
        basis_ref=f"basis:{index}",
        projection_value=projection,
        projection_revision=projection_revision,
        projection_ref=f"vector:{cid}",
    )
    authority = bound.ownership_contributors[0]
    return Candidate(
        chunk_id=cid,
        paper_id="Paper",
        text=text if text is not None else f"synthetic evidence {index}.",
        distance=float(index) / 100,
        section_path="Results",
        block_type="paragraph",
        metadata_status="vector",
        source_role="direct_result",
        ownership_assertion_ref=authority.assertion_ref,
        ownership_revision=authority.revision,
        ownership_basis_ref=authority.basis_ref,
        ownership_projection_value=projection if isinstance(projection, str) else None,
        ownership_projection_revision=projection_revision,
        **snapshot_fields(bound),
    )


def pack_from_candidates(*items: Candidate) -> ContextPack:
    return build_context_pack(
        CandidateSet(
            candidates=tuple(items),
            target_active_count=len(items),
            raw_recall_count=len(items),
            filtered_non_active_count=0,
            final_active_count=len(items),
            reached_overfetch_cap=False,
        )
    )


def model_response(chunk_id="Paper#c1"):
    return {
        "text": json.dumps(
            {
                "literature_support": [
                    {"text": "Synthetic result was 77%.", "source_chunk_id": chunk_id}
                ],
                "possibly_irrelevant": [],
                "ai_general_knowledge": [],
                "risks_and_uncertainties": [],
                "recheck_locations": [],
            }
        ),
        "model": "synthetic-local",
    }


class OwnershipTruthTableTest(unittest.TestCase):
    def test_o01_o08_truth_table(self):
        cases = (
            ("O-01", ("self",), "self", "uniform", "policy_decides"),
            ("O-02", ("entrusted",), "entrusted", "uniform", "local_only"),
            ("O-03", ("unknown",), "unknown", "uniform", "local_only_unknown"),
            ("O-04", ("self", "entrusted"), "entrusted", "mixed", "local_only"),
            ("O-05", ("self", "unknown"), "unknown", "mixed", "local_only_unknown"),
            ("O-06", ("entrusted", "unknown"), "unknown", "mixed", "local_only_unknown"),
            ("O-07", ("self", "entrusted", "unknown"), "unknown", "mixed", "local_only_unknown"),
        )
        for case_id, values, effective, mix, route in cases:
            with self.subTest(case_id=case_id):
                observed = snapshot(values, subject_ref=f"pack:{case_id}")
                self.assertEqual(observed.effective_data_ownership, effective)
                self.assertEqual(observed.ownership_mix_state, mix)
                self.assertEqual(ownership_preflight(observed).required_route_class, route)
        empty = aggregate_blocks((), subject_ref="CP-empty")
        self.assertEqual(empty.ownership_resolution_status, "blocked")
        self.assertEqual(empty.ownership_error_codes, (EMPTY_CONTEXT_PACK,))

    def test_order_does_not_change_digest_or_escalation(self):
        values = (contributor("unknown", 3), contributor("self", 1), contributor("entrusted", 2))
        forward = aggregate_ownership(values, subject_ref="pack:ordered")
        reverse = aggregate_ownership(reversed(values), subject_ref="pack:ordered")
        self.assertEqual(forward, reverse)
        self.assertEqual(forward.ownership_escalated_by, ("chunk:Paper:c3",))


class OwnershipAdversarialTest(unittest.TestCase):
    def test_n01_missing_card_with_vector_self_never_becomes_self(self):
        observed = bind_authority_projection(
            subject_ref="chunk:Paper:c1",
            authoritative_value=None,
            assertion_ref=None,
            revision=None,
            basis_ref=None,
            projection_value="self",
            projection_revision=1,
            projection_ref="vector:c1",
        )
        self.assertIsNone(observed.effective_data_ownership)
        self.assertIn(OWNERSHIP_MISSING, observed.ownership_error_codes)
        self.assertIn(OWNERSHIP_AUTHORITY_INSUFFICIENT, observed.ownership_error_codes)
        self.assertNotIn(OWNERSHIP_INVALID, observed.ownership_error_codes)

    def test_n02_illegal_enum(self):
        observed = snapshot(("owned",), subject_ref="pack:illegal")
        self.assertIn(OWNERSHIP_INVALID, observed.ownership_error_codes)

    def test_n03_card_vector_conflict_is_stale_and_conflict(self):
        observed = bind_authority_projection(
            subject_ref="chunk:Paper:c1",
            authoritative_value="entrusted",
            assertion_ref="OS-1",
            revision=2,
            basis_ref="basis:1",
            projection_value="self",
            projection_revision=1,
            projection_ref="vector:c1",
        )
        self.assertIn(OWNERSHIP_PROJECTION_STALE, observed.ownership_error_codes)
        self.assertIn(OWNERSHIP_CONFLICT, observed.ownership_error_codes)

    def test_n04_n05_legacy_argument_is_compare_only(self):
        entrusted = snapshot(("entrusted",), subject_ref="pack:entrusted")
        self.assertRaisesRegex(
            OwnershipContractError,
            OWNERSHIP_DOWNGRADE_ATTEMPT,
            compare_legacy_binding,
            entrusted,
            "self",
        )
        self_owned = snapshot(("self",), subject_ref="pack:self")
        self.assertRaisesRegex(
            OwnershipContractError,
            OWNERSHIP_BINDING_MISMATCH,
            compare_legacy_binding,
            self_owned,
            "entrusted",
        )

    def test_n06_stale_cache_revision(self):
        observed = snapshot(("self",), subject_ref="pack:cache")
        key = ownership_cache_key(observed)
        with self.assertRaises(OwnershipContractError) as caught:
            validate_cache_binding(observed, key, {"chunk:Paper:c1": 2})
        self.assertEqual(caught.exception.code, OWNERSHIP_PROJECTION_STALE)

    def test_n07_tampered_digest(self):
        observed = snapshot(("self",), subject_ref="pack:digest")
        tampered = replace(observed, ownership_digest="sha256:" + "0" * 64)
        with self.assertRaises(OwnershipContractError) as caught:
            validate_snapshot(tampered)
        self.assertEqual(caught.exception.code, OWNERSHIP_BINDING_MISMATCH)

    def test_n08_empty_and_duplicate_block_identity(self):
        pack = pack_from_candidates(candidate("self", 1), candidate("self", 2))
        empty_ref = replace(pack.blocks[0], block_ref="")
        invalid = aggregate_blocks((empty_ref,), subject_ref="CP-invalid")
        self.assertIn(OWNERSHIP_INVALID, invalid.ownership_error_codes)
        duplicate = replace(pack.blocks[1], block_ref=pack.blocks[0].block_ref)
        conflicted = aggregate_blocks((pack.blocks[0], duplicate), subject_ref="CP-duplicate")
        self.assertIn(OWNERSHIP_CONFLICT, conflicted.ownership_error_codes)

    def test_n09_logical_span_aggregates_every_member(self):
        first = candidate("self", 1, text="Evidence continues")
        second = candidate("entrusted", 2, text="with a second member.")
        spans = build_logical_spans((first, second))
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].data_ownership, "entrusted")
        self.assertEqual(spans[0].ownership_mix_state, "mixed")
        self.assertEqual(
            {item.subject_ref for item in spans[0].ownership_contributors},
            {first.ownership_subject_ref, second.ownership_subject_ref},
        )

    def test_n10_logical_span_missing_member_blocks(self):
        first = candidate("self", 1, text="Evidence continues")
        legacy = Candidate(
            "Paper#c2", "Paper", "with an unbound legacy member.", 0.02,
            section_path="Results", block_type="paragraph",
        )
        span = build_logical_spans((first, legacy))[0]
        self.assertEqual(span.ownership_resolution_status, "blocked")
        self.assertIn(OWNERSHIP_MISSING, span.ownership_error_codes)
        self.assertIn(OWNERSHIP_LEGACY_ADAPTER_REQUIRED, span.ownership_error_codes)

    def test_n11_old_pack_and_arg_cannot_self_prove(self):
        old_pack = ContextPack(
            blocks=(), estimated_tokens=0, token_hard_limit=6000,
            counterexample_slots={"target": 0, "filled": 0}, truncated=False,
            raw_recall_count=0, filtered_non_active_count=0, final_active_count=0,
        )
        with self.assertRaises(OwnershipContractError) as caught:
            build_analysis_request_envelope(old_pack, legacy_data_ownership="self")
        self.assertEqual(caught.exception.code, OWNERSHIP_LEGACY_ADAPTER_REQUIRED)
        with self.assertRaises(OwnershipContractError) as adapter_error:
            adapt_legacy_authorities(subject_ref="legacy:pack", authorities=())
        self.assertEqual(adapter_error.exception.code, OWNERSHIP_AUTHORITY_INSUFFICIENT)

    def test_n12_relaxing_amendment_requires_authority(self):
        original = OwnershipAssertion(
            "OS-1", "chunk:Paper:c1", "entrusted", 1, "basis:1",
            "2026-08-17T00:00:00+09:00",
        )
        with self.assertRaises(OwnershipContractError) as caught:
            amendment_candidate(
                original, new_value="self", reason_code="USER_CORRECTION",
                authority_ref=None,
            )
        self.assertEqual(caught.exception.code, OWNERSHIP_AUTHORITY_INSUFFICIENT)

    def test_n13_conflict_blocks_before_fallback(self):
        base = candidate("self", 1)
        blocked = aggregate_ownership(
            base.ownership_contributors,
            subject_ref=base.ownership_subject_ref,
            inherited_errors=(OWNERSHIP_CONFLICT,),
        )
        bad_candidate = replace(base, **snapshot_fields(blocked))
        pack = pack_from_candidates(bad_candidate)
        calls = []
        with self.assertRaises(M4OwnershipFailClosed) as caught:
            analyze(pack, "question", model_call=lambda payload: calls.append(payload))
        self.assertEqual(caught.exception.error_code, OWNERSHIP_CONFLICT)
        self.assertEqual(calls, [])

    def test_n14_unknown_without_qualified_local_profile_blocks(self):
        pack = pack_from_candidates(candidate("unknown", 1))
        calls = []
        with self.assertRaises(M4OwnershipFailClosed) as caught:
            analyze(pack, "question", model_call=lambda payload: calls.append(payload))
        self.assertEqual(caught.exception.error_code, OWNERSHIP_LOCAL_ROUTE_UNAVAILABLE)
        self.assertEqual(calls, [])


class OwnershipPropagationPathTest(unittest.TestCase):
    def test_neighbor_rebinds_authority_and_compares_projection(self):
        seed = candidate("self", 1)
        neighbor = _neighbor_candidate(
            {
                "chunk_id": "Paper#c2", "paper_id": "Paper", "text": "neighbor",
                "section_path": "Results", "block_type": "paragraph",
                "data_ownership": "self", "ownership_revision": 1,
            },
            seed,
            document_is_review=False,
        )
        self.assertEqual(neighbor.data_ownership, "self")
        self.assertEqual(neighbor.ownership_subject_ref, "chunk:Paper:Paper#c2")
        stale = _neighbor_candidate(
            {
                "chunk_id": "Paper#c3", "paper_id": "Paper", "text": "neighbor",
                "section_path": "Results", "block_type": "paragraph",
                "data_ownership": "entrusted", "ownership_revision": 1,
            },
            seed,
            document_is_review=False,
        )
        self.assertIn(OWNERSHIP_PROJECTION_STALE, stale.ownership_error_codes)

    def test_card_anchor_is_authority_and_chunk_is_projection(self):
        card = {
            "paper_id": "Paper",
            "data_ownership": "entrusted",
            "ownership_revision": 2,
            "source_anchor": {"by_field": {"key_results": [{"chunk_id": "Paper#c1"}]}},
        }
        chunks = [{
            "chunk_id": "Paper#c1", "paper_id": "Paper", "text": "result",
            "section_path": "Results", "block_type": "paragraph",
            "data_ownership": "entrusted", "ownership_revision": 2,
        }]
        facets = (QueryFacet("performance", preferred_card_paths=("key_results",)),)
        result = build_card_anchor_candidates(card, chunks, facets)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].data_ownership, "entrusted")
        self.assertEqual(result[0].ownership_resolution_status, "resolved")

    def test_context_pack_binds_selected_order_and_content(self):
        pack = pack_from_candidates(candidate("self", 1), candidate("entrusted", 2))
        validated = validate_context_pack_binding(pack)
        self.assertEqual(validated.effective_data_ownership, "entrusted")
        self.assertEqual(validated.ownership_mix_state, "mixed")
        tampered_block = replace(pack.blocks[0], content="tampered")
        tampered_pack = replace(pack, blocks=(tampered_block, *pack.blocks[1:]))
        with self.assertRaises(OwnershipContractError) as caught:
            validate_context_pack_binding(tampered_pack)
        self.assertEqual(caught.exception.code, OWNERSHIP_BINDING_MISMATCH)

    def test_transformers_do_not_heal_a_tampered_candidate(self):
        original = candidate("self", 1, text="Evidence continues")
        tampered = replace(original, data_ownership="entrusted")
        neighbor = _neighbor_candidate(
            {
                "chunk_id": "Paper#c2", "paper_id": "Paper", "text": "neighbor",
                "section_path": "Results", "block_type": "paragraph",
                "data_ownership": "self", "ownership_revision": 1,
            },
            tampered,
            document_is_review=False,
        )
        self.assertIn(OWNERSHIP_BINDING_MISMATCH, neighbor.ownership_error_codes)
        span = build_logical_spans(
            (tampered, candidate("self", 2, text="with the second member."))
        )[0]
        self.assertIn(OWNERSHIP_BINDING_MISMATCH, span.ownership_error_codes)
        pack = pack_from_candidates(tampered)
        self.assertIn(OWNERSHIP_BINDING_MISMATCH, pack.ownership_error_codes)

    def test_full_self_chain_to_m6_and_payload_excludes_control_metadata(self):
        pack = pack_from_candidates(candidate("self", 1))
        envelope = build_analysis_request_envelope(pack, legacy_data_ownership="self")
        captured = []
        analysis = analyze(
            pack,
            "question",
            legacy_data_ownership="self",
            model_call=lambda payload: captured.append(payload) or model_response(),
        )
        self.assertEqual(len(captured), 1)
        provider_text = json.dumps(captured[0], ensure_ascii=False)
        self.assertNotIn("ownership_assertion_ref", provider_text)
        self.assertNotIn("ownership_basis_ref", provider_text)
        self.assertNotIn("ownership_snapshot", provider_text)
        task = build_ownership_review_task(analysis, legacy_data_ownership="self")
        report = verify_research_analysis(
            analysis,
            {"Paper#c1": "synthetic evidence 1."},
            legacy_data_ownership="self",
            max_gpt=0,
        )
        self.assertEqual(envelope.ownership_snapshot["ownership_digest"], analysis.ownership_snapshot["ownership_digest"])
        self.assertEqual(analysis.ownership_snapshot["ownership_digest"], task.ownership_snapshot["ownership_digest"])
        self.assertEqual(report["ownership_propagation_receipt"]["result"], "PASS")
        self.assertEqual(len(trace_ownership(task.ownership_propagation_receipt)), 3)

    def test_m6_rejects_analysis_tamper_before_verifier(self):
        pack = pack_from_candidates(candidate("self", 1))
        analysis = analyze(pack, "question", model_call=lambda payload: model_response())
        tampered = replace(analysis, question="changed")
        calls = []
        with self.assertRaises(M6OwnershipFailClosed) as caught:
            verify_research_analysis(
                tampered,
                {"Paper#c1": "synthetic evidence 1."},
                legacy_data_ownership="self",
                verifier_call=lambda *_: calls.append(True),
            )
        self.assertIn(OWNERSHIP_BINDING_MISMATCH, str(caught.exception))
        self.assertEqual(calls, [])

        tampered_metrics = replace(analysis, quality_metrics={"tampered": True})
        with self.assertRaises(M6OwnershipFailClosed) as metrics_error:
            verify_research_analysis(
                tampered_metrics,
                {"Paper#c1": "synthetic evidence 1."},
                legacy_data_ownership="self",
                verifier_call=lambda *_: calls.append(True),
            )
        self.assertIn(OWNERSHIP_BINDING_MISMATCH, str(metrics_error.exception))

        tampered_envelope = replace(
            analysis, ownership_request_envelope_hash="sha256:" + "0" * 64
        )
        with self.assertRaises(M6OwnershipFailClosed) as envelope_error:
            verify_research_analysis(
                tampered_envelope,
                {"Paper#c1": "synthetic evidence 1."},
                legacy_data_ownership="self",
                verifier_call=lambda *_: calls.append(True),
            )
        self.assertIn(OWNERSHIP_BINDING_MISMATCH, str(envelope_error.exception))
        self.assertEqual(calls, [])


class OwnershipReplayAndSchemaTest(unittest.TestCase):
    def test_json_frontmatter_cross_process_and_version_boundary(self):
        original = snapshot(("self", "entrusted"), subject_ref="包:中文定位")
        round_trip = OwnershipSnapshot.from_dict(json.loads(json.dumps(original.to_dict(), ensure_ascii=False)))
        self.assertEqual(validate_snapshot(round_trip), original)
        self.assertEqual(parse_snapshot_frontmatter(render_snapshot_frontmatter(original)), original)
        code = (
            "import json,sys; "
            "from memorive.research.retrieval.ownership import OwnershipSnapshot,validate_snapshot; "
            "x=validate_snapshot(OwnershipSnapshot.from_dict(json.load(sys.stdin))); "
            "print(x.ownership_digest)"
        )
        process = subprocess.run(
            [sys.executable, "-c", code],
            input=json.dumps(original.to_dict(), ensure_ascii=False),
            text=True,
            capture_output=True,
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), original.ownership_digest)
        old = replace(original, ownership_contract_version="OWNERSHIP_V0")
        with self.assertRaises(OwnershipContractError) as caught:
            validate_snapshot(old)
        self.assertEqual(caught.exception.code, OWNERSHIP_VERSION_UNSUPPORTED)

    def test_schema_examples_validate(self):
        schema_root = REPO_ROOT / "memorive" / "research" / "retrieval" / "schemas"
        snapshot_schema = json.loads(
            (schema_root / "data_ownership_ownership_snapshot_v1.schema.json").read_text(encoding="utf-8")
        )
        amendment_schema = json.loads(
            (schema_root / "data_ownership_ownership_amendment_v1.schema.json").read_text(encoding="utf-8")
        )
        receipt_schema = json.loads(
            (schema_root / "data_ownership_ownership_propagation_receipt_v1.schema.json").read_text(encoding="utf-8")
        )
        observed = snapshot(("self", "entrusted"), subject_ref="pack:schema")
        amendment = amendment_candidate(
            OwnershipAssertion(
                "OS-1", "chunk:Paper:c1", "self", 1, "basis:1",
                "2026-08-17T00:00:00+09:00",
            ),
            new_value="entrusted",
            reason_code="TIGHTENED_BY_OWNER",
            authority_ref=None,
            created_at="2026-08-17T01:00:00+09:00",
        )
        receipt = propagation_receipt(
            observed,
            subject_chain={"source": "S-1", "pack": "CP-1"},
            checks=("SOURCE_TO_PACK_MATCH",),
        )
        Draft202012Validator(snapshot_schema).validate(observed.to_dict())
        Draft202012Validator(amendment_schema).validate(amendment.to_dict())
        Draft202012Validator(receipt_schema).validate(receipt.to_dict())


if __name__ == "__main__":
    unittest.main(verbosity=2)
