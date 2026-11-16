"""Adversarial regression tests for the RESEARCH-OPPORTUNITIES package."""

from __future__ import annotations

import ast
import json
import math
import struct
import unittest
from copy import deepcopy
from pathlib import Path

from research_opportunities import canonical_json_bytes, ContractRejected, EvidenceRejected, typed_payload_hash
from research_opportunities import CandidateRejected, SourceRejected, assess_comparability, build_opportunity_candidate, classify_tension, extract_author_stated_gaps, external_hash_is_valid, make_anchor_resolution_receipt, make_ignore_decision, make_pair_ledger, make_source_snapshot, make_trigger_scope_receipt, project_comparison_concepts, validate_anchor_resolution_receipt, validate_source_card, with_content_hash
from memorive.documents.card_review import verify_opportunity_candidate
from memorive.documents.card_review.opportunity_verification import _ignore_valid as m6_ignore_valid
from memorive.documents.card_states.opportunity_submission import make_review_submission_request, validate_knowledge_admission_validation_receipt, validate_review_submission_request
LIMITS = {
    "max_clusters": 2,
    "max_cards_per_cluster": 4,
    "max_pairs_per_cluster": 3,
    "max_candidates_total": 8,
}
ServiceContracts_HASH = {
    "algorithm": "sha256",
    "hash_kind": "raw_file_bytes_sha256",
    "scope": "ServiceContracts_FIELD_REGISTRY",
    "value": "3" * 64,
}
Configuration_HASH = {
    "algorithm": "sha256",
    "hash_kind": "raw_file_bytes_sha256",
    "scope": "Configuration_POLICY_SNAPSHOT",
    "value": "4" * 64,
}
EMPTY_IGNORE_AUTHORITY_REGISTRY: dict[str, str] = {}
EMPTY_IGNORE_AUTHORITY_REGISTRY_HASH = typed_payload_hash(
    EMPTY_IGNORE_AUTHORITY_REGISTRY
)


def make_card(
    card_id: str,
    *,
    conclusion: str = "Treatment increased yield.",
    limitations: list[str] | None = None,
    review_status: str = "active",
    is_derived: bool = False,
    condition: str = "fixed temperature",
    groups: list[str] | None = None,
    scale: str = "laboratory scale",
    author_anchor_quote: str | None = None,
    chunk_override: str | None = None,
) -> tuple[dict, dict[str, str]]:
    limitations = limitations or []
    groups = groups or ["treated", "control"]
    chunk_id = f"{card_id}:chunk:001"
    values = [
        "synthetic catalyst",
        "controlled assay",
        "yield",
        condition,
        *groups,
        "ambient pressure",
        "24 hours",
        scale,
        "synthetic benchmark",
        conclusion,
        *limitations,
    ]
    chunk = chunk_override if chunk_override is not None else " ".join(values)
    card = {
        "card_id": card_id,
        "paper_id": f"paper:{card_id}",
        "revision": 1,
        "schema_version": 6,
        "review_status": review_status,
        "doc_type": "literature",
        "is_derived": is_derived,
        "data_ownership": "self",
        "language": "en",
        "research_object": "synthetic catalyst",
        "method": "controlled assay",
        "boundary_conditions": "ambient pressure",
        "author_conclusion": conclusion,
        "key_results": [],
        "key_data": [
            {
                "metric": "yield",
                "value": "1.0",
                "unit": "synthetic-unit",
                "quote": "yield",
                "chunk_id": chunk_id,
            }
        ],
        "comparison_context": {
            "object": "synthetic catalyst",
            "condition": condition,
            "groups": groups,
            "time": "24 hours",
            "scale": scale,
            "scenario": "synthetic benchmark",
            "applicability": "ambient pressure",
        },
        "author_limitations_outlook": {
            "limitations": [{"quote": value, "chunk_id": chunk_id} for value in limitations],
            "outlook": [],
        },
        "source_anchor": {
            "by_field": {
                "research_object": [{"chunk_id": chunk_id, "quote": "synthetic catalyst"}],
                "method": [{"chunk_id": chunk_id, "quote": "controlled assay"}],
                "boundary_conditions": [{"chunk_id": chunk_id, "quote": "ambient pressure"}],
                "author_conclusion": [
                    {"chunk_id": chunk_id, "quote": author_anchor_quote or conclusion}
                ],
                "key_results": [],
            }
        },
        "fixture_classification": "PUBLIC_SAFE_SYNTHETIC",
    }
    return with_content_hash(card), {chunk_id: chunk}


def build_bundle() -> dict:
    gap_card, gap_chunks = make_card(
        "gap-card", limitations=["The small sample limits generalization."]
    )
    left, left_chunks = make_card("left-card", conclusion="Treatment increased yield.")
    right, right_chunks = make_card("right-card", conclusion="Treatment decreased yield.")
    cards = {item["card_id"]: item for item in (gap_card, left, right)}
    chunks = {
        "gap-card": gap_chunks,
        "left-card": left_chunks,
        "right-card": right_chunks,
    }
    trigger = make_trigger_scope_receipt(
        trigger_type="weekly",
        topic_scope=["synthetic-topic"],
        paper_scope=[],
        limits=LIMITS,
    )
    snapshot = make_source_snapshot(
        snapshot_id="source-sample",
        source_cutoff="2026-08-04T00:00:00Z",
        cards=list(cards.values()),
        chunks_by_card=chunks,
        source_rights={key: "PUBLIC_SAFE_SYNTHETIC" for key in cards},
        cluster_topk={"cluster-a": ["gap-card", "left-card", "right-card"]},
        trigger_receipt=trigger,
        service_contracts_registry_ref="ServiceContracts-registry",
        service_contracts_registry_hash=ServiceContracts_HASH,
        configuration_policy_ref="relation-policy",
        configuration_policy_hash=Configuration_HASH,
    )
    gaps, rejected = extract_author_stated_gaps(gap_card, gap_chunks, snapshot)
    assert not rejected
    projections = {
        card_id: project_comparison_concepts(cards[card_id], chunks[card_id], snapshot)
        for card_id in ("left-card", "right-card")
    }
    anchor_receipts = {
        card_id: make_anchor_resolution_receipt(projection)
        for card_id, projection in projections.items()
    }
    ledger = make_pair_ledger(snapshot, cluster_id="cluster-a")
    assessment = assess_comparability(
        projections["left-card"],
        projections["right-card"],
        first_anchor_receipt=anchor_receipts["left-card"],
        second_anchor_receipt=anchor_receipts["right-card"],
        pair_ledger=ledger,
        source_snapshot=snapshot,
    )
    tension = classify_tension(
        projections["left-card"],
        projections["right-card"],
        first_anchor_receipt=anchor_receipts["left-card"],
        second_anchor_receipt=anchor_receipts["right-card"],
        assessment=assessment,
        pair_ledger=ledger,
        source_snapshot=snapshot,
    )
    candidate = build_opportunity_candidate(
        opportunity_kind="hybrid",
        source_snapshot=snapshot,
        gaps=gaps,
        tensions=[tension],
        anchor_resolution_receipts=list(anchor_receipts.values()),
        assessments=[assessment],
        pair_ledgers=[ledger],
    )
    evidence = {gaps[0]["evidence_id"]: gaps[0], tension["tension_id"]: tension}
    assessments = {assessment["assessment_id"]: assessment}
    ledgers = {ledger["ledger_id"]: ledger}
    verify_kwargs = {
        "candidate": candidate,
        "source_snapshot": snapshot,
        "trigger_receipt": trigger,
        "service_contracts_registry_hash": ServiceContracts_HASH,
        "configuration_policy_hash": Configuration_HASH,
        "cards_by_id": cards,
        "chunks_by_card": chunks,
        "projections_by_card": projections,
        "anchor_receipts_by_card": anchor_receipts,
        "evidence_by_ref": evidence,
        "assessments_by_ref": assessments,
        "pair_ledgers_by_ref": ledgers,
        "authorized_human_authorities": EMPTY_IGNORE_AUTHORITY_REGISTRY,
        "ignore_authority_registry_hash": EMPTY_IGNORE_AUTHORITY_REGISTRY_HASH,
        "ignore_decisions_by_ref": {},
    }
    report = verify_opportunity_candidate(**verify_kwargs)
    return {
        **verify_kwargs,
        "report": report,
        "gap": gaps[0],
        "tension": tension,
        "assessment": assessment,
        "ledger": ledger,
    }


VERIFY_ARGUMENT_KEYS = {
    "candidate",
    "source_snapshot",
    "trigger_receipt",
    "service_contracts_registry_hash",
    "configuration_policy_hash",
    "cards_by_id",
    "chunks_by_card",
    "projections_by_card",
    "anchor_receipts_by_card",
    "evidence_by_ref",
    "assessments_by_ref",
    "pair_ledgers_by_ref",
    "authorized_human_authorities",
    "ignore_authority_registry_hash",
    "ignore_decisions_by_ref",
}


def verify_arguments(bundle: dict) -> dict:
    return {key: value for key, value in bundle.items() if key in VERIFY_ARGUMENT_KEYS}


class Successor003Tests(unittest.TestCase):
    def test_happy_hybrid_is_role_separated_recomputed_and_submission_is_create_only(self) -> None:
        bundle = build_bundle()
        self.assertEqual("PASS", bundle["report"]["verification_result"], bundle["report"]["issues"])
        request = make_review_submission_request(
            candidate=bundle["candidate"], verification_report=bundle["report"], derived_penalty=-99
        )
        self.assertTrue(request["submission_eligible"])
        self.assertEqual("candidate", request["state_after_request"])
        self.assertFalse(request["automatic_transition_performed"])
        validation_receipt = validate_review_submission_request(
            request, candidate=bundle["candidate"], verification_report=bundle["report"]
        )
        self.assertEqual("PASS", validation_receipt["validation_result"])
        validate_knowledge_admission_validation_receipt(
            validation_receipt,
            request=request,
            candidate=bundle["candidate"],
            verification_report=bundle["report"],
        )

    def test_anchor_resolution_receipts_are_distinct_mandatory_and_recomputed(self) -> None:
        bundle = build_bundle()
        receipts = bundle["anchor_receipts_by_card"]
        self.assertEqual(
            sorted(item["receipt_id"] for item in receipts.values()),
            bundle["candidate"]["anchor_resolution_receipt_refs"],
        )
        self.assertEqual(
            {
                card_id: receipt["content_hash"]
                for card_id, receipt in sorted(receipts.items())
            },
            bundle["assessment"]["anchor_resolution_receipt_hashes"],
        )
        self.assertTrue(
            bundle["report"]["required_check_registry"][
                "anchor_resolution_receipt_exact_set_and_recompute"
            ]
        )
        for card_id, receipt in receipts.items():
            validate_anchor_resolution_receipt(
                receipt, bundle["projections_by_card"][card_id]
            )

        with self.assertRaises(TypeError):
            assess_comparability(
                bundle["projections_by_card"]["left-card"],
                bundle["projections_by_card"]["right-card"],
                pair_ledger=bundle["ledger"],
                source_snapshot=bundle["source_snapshot"],
            )

        forged = deepcopy(receipts["left-card"])
        forged.pop("content_hash")
        forged["recovery_scope"] = "WHOLE_CARD_UNFROZEN_SCAN"
        forged = with_content_hash(forged)
        with self.assertRaises(EvidenceRejected):
            validate_anchor_resolution_receipt(
                forged, bundle["projections_by_card"]["left-card"]
            )

        kwargs = verify_arguments(bundle)
        kwargs["anchor_receipts_by_card"] = {
            **receipts,
            "left-card": forged,
        }
        self.assertEqual(
            "FAIL", verify_opportunity_candidate(**kwargs)["verification_result"]
        )
        kwargs["anchor_receipts_by_card"] = {"left-card": receipts["left-card"]}
        self.assertEqual(
            "FAIL", verify_opportunity_candidate(**kwargs)["verification_result"]
        )

    def test_admission_validation_receipt_is_hash_bound_and_tamper_evident(self) -> None:
        bundle = build_bundle()
        request = make_review_submission_request(
            candidate=bundle["candidate"], verification_report=bundle["report"]
        )
        receipt = validate_review_submission_request(
            request,
            candidate=bundle["candidate"],
            verification_report=bundle["report"],
        )
        validate_knowledge_admission_validation_receipt(
            receipt,
            request=request,
            candidate=bundle["candidate"],
            verification_report=bundle["report"],
        )
        forged = deepcopy(receipt)
        forged.pop("content_hash")
        forged["state_after"] = "review_pending"
        forged = with_content_hash(forged)
        with self.assertRaises(CandidateRejected):
            validate_knowledge_admission_validation_receipt(
                forged,
                request=request,
                candidate=bundle["candidate"],
                verification_report=bundle["report"],
            )

    def test_rfc8785_official_utf16_sorting_and_string_escaping_vectors(self) -> None:
        sorting_fixture = {
            "\u20ac": "Euro Sign",
            "\r": "Carriage Return",
            "\ufb33": "Hebrew Letter Dalet With Dagesh",
            "1": "One",
            "\U0001f600": "Emoji: Grinning Face",
            "\u0080": "Control",
            "\u00f6": "Latin Small Letter O With Diaeresis",
        }
        canonical = canonical_json_bytes(sorting_fixture).decode("utf-8")
        self.assertEqual(
            [
                "Carriage Return",
                "One",
                "Control",
                "Latin Small Letter O With Diaeresis",
                "Euro Sign",
                "Emoji: Grinning Face",
                "Hebrew Letter Dalet With Dagesh",
            ],
            list(json.loads(canonical).values()),
        )
        self.assertEqual(
            '"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"',
            canonical_json_bytes("€$\x0f\nA'B\"\\\\\"/").decode("utf-8"),
        )
        self.assertEqual(
            "[333333333.3333333,1e+30,4.5,0.002,1e-27]",
            canonical_json_bytes(
                [333333333.33333329, 1e30, 4.50, 2e-3, 1e-27]
            ).decode("utf-8"),
        )

    def test_rfc8785_appendix_b_complete_finite_number_vectors(self) -> None:
        vectors = {
            "0000000000000000": "0",
            "8000000000000000": "0",
            "0000000000000001": "5e-324",
            "8000000000000001": "-5e-324",
            "7fefffffffffffff": "1.7976931348623157e+308",
            "ffefffffffffffff": "-1.7976931348623157e+308",
            "4340000000000000": "9007199254740992",
            "c340000000000000": "-9007199254740992",
            "4430000000000000": "295147905179352830000",
            "44b52d02c7e14af5": "9.999999999999997e+22",
            "44b52d02c7e14af6": "1e+23",
            "44b52d02c7e14af7": "1.0000000000000001e+23",
            "444b1ae4d6e2ef4e": "999999999999999700000",
            "444b1ae4d6e2ef4f": "999999999999999900000",
            "444b1ae4d6e2ef50": "1e+21",
            "3eb0c6f7a0b5ed8c": "9.999999999999997e-7",
            "3eb0c6f7a0b5ed8d": "0.000001",
            "41b3de4355555553": "333333333.3333332",
            "41b3de4355555554": "333333333.33333325",
            "41b3de4355555555": "333333333.3333333",
            "41b3de4355555556": "333333333.3333334",
            "41b3de4355555557": "333333333.33333343",
            "becbf647612f3696": "-0.0000033333333333333333",
            "43143ff3c1cb0959": "1424953923781206.2",
        }
        for ieee_hex, expected in vectors.items():
            with self.subTest(ieee754=ieee_hex):
                value = struct.unpack(">d", bytes.fromhex(ieee_hex))[0]
                self.assertEqual(
                    expected, canonical_json_bytes(value).decode("utf-8")
                )

    def test_rfc8785_ijson_rejections_integer_boundary_and_hash_domains(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    canonical_json_bytes(value)
        for value in ("\ud800", "\udead"):
            with self.subTest(surrogate=repr(value)):
                with self.assertRaises(ValueError):
                    canonical_json_bytes(value)
                with self.assertRaises(ValueError):
                    canonical_json_bytes({value: "forbidden"})
        self.assertEqual(
            "9007199254740992", canonical_json_bytes(2**53).decode("utf-8")
        )
        self.assertEqual(
            "295147905179352830000",
            canonical_json_bytes(2**68).decode("utf-8"),
        )
        with self.assertRaises(ValueError):
            canonical_json_bytes(2**53 + 1)

        precomposed = typed_payload_hash({"unicode": "é"})
        decomposed = typed_payload_hash({"unicode": "e\u0301"})
        self.assertNotEqual(precomposed, decomposed)
        self.assertEqual("rfc8785_jcs_sha256", precomposed["hash_kind"])
        self.assertEqual("PAYLOAD_EXCLUDING_CONTENT_HASH", precomposed["scope"])
        self.assertTrue(external_hash_is_valid(Configuration_HASH))
        self.assertEqual("raw_file_bytes_sha256", Configuration_HASH["hash_kind"])
        self.assertNotEqual(Configuration_HASH["hash_kind"], precomposed["hash_kind"])

        public_card, public_chunks = make_card("rights-boundary")
        trigger = make_trigger_scope_receipt(
            trigger_type="weekly", topic_scope=["x"], paper_scope=[], limits=LIMITS
        )
        for forbidden_right in ("PRIVATE", "FORBIDDEN", "UNKNOWN"):
            with self.subTest(rights=forbidden_right):
                with self.assertRaises((SourceRejected, ContractRejected)):
                    make_source_snapshot(
                        snapshot_id=f"forbidden-{forbidden_right.lower()}",
                        source_cutoff="2026-08-04T00:00:00Z",
                        cards=[public_card],
                        chunks_by_card={public_card["card_id"]: public_chunks},
                        source_rights={public_card["card_id"]: forbidden_right},
                        cluster_topk={"cluster": [public_card["card_id"]]},
                        trigger_receipt=trigger,
                        service_contracts_registry_ref="service_contracts",
                        service_contracts_registry_hash=ServiceContracts_HASH,
                        configuration_policy_ref="configuration",
                        configuration_policy_hash=Configuration_HASH,
                    )

    def test_non_active_derived_or_missing_hash_source_fails_closed(self) -> None:
        pending, _ = make_card("pending", review_status="pending")
        derived, _ = make_card("derived", is_derived=True)
        no_hash, _ = make_card("no-hash")
        no_hash = deepcopy(no_hash)
        no_hash.pop("content_hash")
        for card in (pending, derived, no_hash):
            with self.subTest(card=card["card_id"]):
                with self.assertRaises(SourceRejected):
                    validate_source_card(card)

    def test_invalid_relation_policy_hash_is_rejected(self) -> None:
        card, chunks = make_card("policy-card")
        trigger = make_trigger_scope_receipt(
            trigger_type="weekly", topic_scope=["x"], paper_scope=[], limits=LIMITS
        )
        with self.assertRaises(Exception):
            make_source_snapshot(
                snapshot_id="bad-policy",
                source_cutoff="2026-08-04T00:00:00Z",
                cards=[card],
                chunks_by_card={card["card_id"]: chunks},
                source_rights={card["card_id"]: "PUBLIC_SAFE_SYNTHETIC"},
                cluster_topk={"cluster": [card["card_id"]]},
                trigger_receipt=trigger,
                service_contracts_registry_ref="service_contracts",
                service_contracts_registry_hash=ServiceContracts_HASH,
                configuration_policy_ref="configuration",
                configuration_policy_hash={},
            )

    def test_unanchored_context_and_unrelated_claim_anchor_cannot_be_A(self) -> None:
        left, left_chunks = make_card(
            "ua-left",
            conclusion="Treatment increased yield.",
            author_anchor_quote="synthetic catalyst",
            chunk_override="synthetic catalyst controlled assay yield ambient pressure 24 hours treated control synthetic benchmark",
        )
        right, right_chunks = make_card(
            "ua-right",
            conclusion="Treatment decreased yield.",
            author_anchor_quote="synthetic catalyst",
            chunk_override="synthetic catalyst controlled assay yield ambient pressure 24 hours treated control synthetic benchmark",
        )
        cards = {left["card_id"]: left, right["card_id"]: right}
        chunks = {left["card_id"]: left_chunks, right["card_id"]: right_chunks}
        trigger = make_trigger_scope_receipt(
            trigger_type="weekly", topic_scope=["x"], paper_scope=[], limits=LIMITS
        )
        snapshot = make_source_snapshot(
            snapshot_id="unanchored",
            source_cutoff="2026-08-04T00:00:00Z",
            cards=[left, right],
            chunks_by_card=chunks,
            source_rights={key: "PUBLIC_SAFE_SYNTHETIC" for key in cards},
            cluster_topk={"cluster": sorted(cards)},
            trigger_receipt=trigger,
            service_contracts_registry_ref="service_contracts",
            service_contracts_registry_hash=ServiceContracts_HASH,
            configuration_policy_ref="configuration",
            configuration_policy_hash=Configuration_HASH,
        )
        projections = {
            card_id: project_comparison_concepts(card, chunks[card_id], snapshot)
            for card_id, card in cards.items()
        }
        anchor_receipts = {
            card_id: make_anchor_resolution_receipt(projection)
            for card_id, projection in projections.items()
        }
        self.assertTrue(all(item["coverage_status"] == "coverage_limited" for item in projections.values()))
        self.assertTrue(all(item["conclusion_direction"] is None for item in projections.values()))
        ledger = make_pair_ledger(snapshot, cluster_id="cluster")
        assessment = assess_comparability(
            projections["ua-left"],
            projections["ua-right"],
            first_anchor_receipt=anchor_receipts["ua-left"],
            second_anchor_receipt=anchor_receipts["ua-right"],
            pair_ledger=ledger,
            source_snapshot=snapshot,
        )
        tension = classify_tension(
            projections["ua-left"],
            projections["ua-right"],
            first_anchor_receipt=anchor_receipts["ua-left"],
            second_anchor_receipt=anchor_receipts["ua-right"],
            assessment=assessment,
            pair_ledger=ledger,
            source_snapshot=snapshot,
        )
        self.assertEqual("coverage_limited", assessment["overall"])
        self.assertEqual("coverage_limited", tension["classification"])

    def test_ascii_recovery_uses_token_boundaries(self) -> None:
        card, chunks = make_card(
            "token-card",
            groups=["control"],
            chunk_override="synthetic catalyst controlled assay yield fixed temperature ambient pressure 24 hours laboratory scale synthetic benchmark Treatment increased yield.",
        )
        trigger = make_trigger_scope_receipt(
            trigger_type="weekly", topic_scope=["x"], paper_scope=[], limits=LIMITS
        )
        snapshot = make_source_snapshot(
            snapshot_id="token-snapshot",
            source_cutoff="2026-08-04T00:00:00Z",
            cards=[card],
            chunks_by_card={card["card_id"]: chunks},
            source_rights={card["card_id"]: "PUBLIC_SAFE_SYNTHETIC"},
            cluster_topk={"cluster": [card["card_id"]]},
            trigger_receipt=trigger,
            service_contracts_registry_ref="service_contracts",
            service_contracts_registry_hash=ServiceContracts_HASH,
            configuration_policy_ref="configuration",
            configuration_policy_hash=Configuration_HASH,
        )
        projection = project_comparison_concepts(card, chunks, snapshot)
        comparator = next(row for row in projection["concepts"] if row["concept"] == "comparator")
        self.assertEqual("unanchored", comparator["bindings"][0]["status"])
        self.assertEqual("coverage_limited", projection["coverage_status"])

    def test_malformed_candidate_and_forged_assessment_fail_m06(self) -> None:
        bundle = build_bundle()
        malformed = with_content_hash(
            {
                "object_id": "opp_malformed",
                "schema_version": "3.1",
                "state": "candidate",
                "scientific_judgment_status": "NOT_ASSESSED",
                "automatic_state_transition": False,
                "gap_refs": [],
                "tension_refs": [],
            }
        )
        kwargs = verify_arguments(bundle)
        kwargs["candidate"] = malformed
        report = verify_opportunity_candidate(**kwargs)
        self.assertEqual("FAIL", report["verification_result"])

        forged = deepcopy(bundle["assessment"])
        forged["concept_results"] = [deepcopy(forged["concept_results"][0]) for _ in range(8)]
        forged = with_content_hash(forged)
        kwargs = verify_arguments(bundle)
        kwargs["assessments_by_ref"] = {forged["assessment_id"]: forged}
        report = verify_opportunity_candidate(**kwargs)
        self.assertEqual("FAIL", report["verification_result"])

    def test_score_authority_and_stale_report_are_rejected(self) -> None:
        bundle = build_bundle()
        changed = deepcopy(bundle["candidate"])
        changed.pop("content_hash")
        changed["score_snapshot"]["state_authority"] = True
        changed["score_snapshot"]["research_value_total_score"] = 999
        changed = with_content_hash(changed)
        kwargs = verify_arguments(bundle)
        kwargs.pop("candidate")
        report = verify_opportunity_candidate(candidate=changed, **kwargs)
        self.assertEqual("FAIL", report["verification_result"])
        with self.assertRaises(CandidateRejected):
            make_review_submission_request(candidate=changed, verification_report=bundle["report"])

        stale = deepcopy(bundle["candidate"])
        stale.pop("content_hash")
        stale["parent_refs"] = ["forged-parent"]
        stale = with_content_hash(stale)
        with self.assertRaises(CandidateRejected):
            make_review_submission_request(candidate=stale, verification_report=bundle["report"])

    def test_unstructured_ignore_decision_and_forged_request_are_rejected(self) -> None:
        bundle = build_bundle()
        authority_ref = "gate-a:human-reviewer-001"
        authority_registry = {authority_ref: "human-reviewer"}
        authority_registry_hash = typed_payload_hash(authority_registry)
        with self.assertRaises((EvidenceRejected, ContractRejected)):
            classify_tension(
                bundle["projections_by_card"]["left-card"],
                bundle["projections_by_card"]["right-card"],
                first_anchor_receipt=bundle["anchor_receipts_by_card"]["left-card"],
                second_anchor_receipt=bundle["anchor_receipts_by_card"]["right-card"],
                assessment=bundle["assessment"],
                pair_ledger=bundle["ledger"],
                source_snapshot=bundle["source_snapshot"],
                ignore_decision={"decision_id": "fake", "decision": "IGNORE"},
            )
        valid_ignore = make_ignore_decision(
            decision_id="ignore-001",
            decided_by="human-reviewer",
            decided_at="2026-08-04T00:00:00Z",
            authority_ref=authority_ref,
            authorized_human_authorities=authority_registry,
            ignore_authority_registry_hash=authority_registry_hash,
            evidence_refs=sorted(
                [bundle["assessment"]["assessment_id"], authority_ref],
                key=lambda value: value.encode("utf-8"),
            ),
            assessment=bundle["assessment"],
        )
        with self.assertRaises(EvidenceRejected):
            classify_tension(
                bundle["projections_by_card"]["left-card"],
                bundle["projections_by_card"]["right-card"],
                first_anchor_receipt=bundle["anchor_receipts_by_card"]["left-card"],
                second_anchor_receipt=bundle["anchor_receipts_by_card"]["right-card"],
                assessment=bundle["assessment"],
                pair_ledger=bundle["ledger"],
                source_snapshot=bundle["source_snapshot"],
                ignore_decision=valid_ignore,
            )
        ignored = classify_tension(
            bundle["projections_by_card"]["left-card"],
            bundle["projections_by_card"]["right-card"],
            first_anchor_receipt=bundle["anchor_receipts_by_card"]["left-card"],
            second_anchor_receipt=bundle["anchor_receipts_by_card"]["right-card"],
            assessment=bundle["assessment"],
            pair_ledger=bundle["ledger"],
            source_snapshot=bundle["source_snapshot"],
            ignore_decision=valid_ignore,
            authorized_human_authorities=authority_registry,
            ignore_authority_registry_hash=authority_registry_hash,
        )
        self.assertEqual("D", ignored["classification"])

        ignored_candidate = build_opportunity_candidate(
            opportunity_kind="hybrid",
            source_snapshot=bundle["source_snapshot"],
            gaps=[bundle["gap"]],
            tensions=[ignored],
            anchor_resolution_receipts=list(bundle["anchor_receipts_by_card"].values()),
            assessments=[bundle["assessment"]],
            pair_ledgers=[bundle["ledger"]],
        )
        ignored_kwargs = verify_arguments(bundle)
        ignored_kwargs.update(
            {
                "candidate": ignored_candidate,
                "evidence_by_ref": {
                    bundle["gap"]["evidence_id"]: bundle["gap"],
                    ignored["tension_id"]: ignored,
                },
                "authorized_human_authorities": authority_registry,
                "ignore_authority_registry_hash": authority_registry_hash,
                "ignore_decisions_by_ref": {valid_ignore["decision_id"]: valid_ignore},
            }
        )
        ignored_report = verify_opportunity_candidate(**ignored_kwargs)
        self.assertEqual("PASS", ignored_report["verification_result"], ignored_report["issues"])
        self.assertEqual(
            authority_registry_hash,
            ignored_report["ignore_authority_registry_hash"],
        )

        malformed_updates = (
            ("blank_decision_id", {"decision_id": ""}),
            ("blank_decided_by", {"decided_by": ""}),
            ("wrong_authority_kind", {"authority_kind": "SELF_ASSERTED"}),
            ("unknown_authority_ref", {"authority_ref": "gate-a:unknown"}),
            ("naive_datetime", {"decided_at": "2026-08-04T00:00:00"}),
            ("blank_evidence_ref", {"evidence_refs": [""]}),
            (
                "duplicate_evidence_refs",
                {"evidence_refs": [authority_ref, authority_ref]},
            ),
            (
                "utf8_unsorted_evidence_refs",
                {"evidence_refs": list(reversed(valid_ignore["evidence_refs"]))},
            ),
            (
                "assessment_ref_missing_from_evidence",
                {"evidence_refs": [authority_ref]},
            ),
            (
                "authority_ref_missing_from_evidence",
                {"evidence_refs": [bundle["assessment"]["assessment_id"]]},
            ),
            ("decided_by_identity_mismatch", {"decided_by": "attacker"}),
            (
                "stale_authority_registry_binding",
                {"authority_registry_hash": EMPTY_IGNORE_AUTHORITY_REGISTRY_HASH},
            ),
        )
        for label, updates in malformed_updates:
            with self.subTest(malformed_ignore=label):
                forged_ignore = deepcopy(valid_ignore)
                forged_ignore.pop("content_hash")
                forged_ignore.update(deepcopy(updates))
                forged_ignore = with_content_hash(forged_ignore)
                self.assertFalse(
                    m6_ignore_valid(
                        forged_ignore,
                        bundle["assessment"],
                        authority_registry,
                        authority_registry_hash,
                    )
                )
                with self.assertRaises((EvidenceRejected, ContractRejected)):
                    classify_tension(
                        bundle["projections_by_card"]["left-card"],
                        bundle["projections_by_card"]["right-card"],
                        first_anchor_receipt=bundle["anchor_receipts_by_card"]["left-card"],
                        second_anchor_receipt=bundle["anchor_receipts_by_card"]["right-card"],
                        assessment=bundle["assessment"],
                        pair_ledger=bundle["ledger"],
                        source_snapshot=bundle["source_snapshot"],
                        ignore_decision=forged_ignore,
                        authorized_human_authorities=authority_registry,
                        ignore_authority_registry_hash=authority_registry_hash,
                    )

        self.assertFalse(
            m6_ignore_valid(
                valid_ignore,
                bundle["assessment"],
                {authority_ref: "attacker"},
                typed_payload_hash({authority_ref: "attacker"}),
            )
        )
        with self.assertRaises(EvidenceRejected):
            classify_tension(
                bundle["projections_by_card"]["left-card"],
                bundle["projections_by_card"]["right-card"],
                first_anchor_receipt=bundle["anchor_receipts_by_card"]["left-card"],
                second_anchor_receipt=bundle["anchor_receipts_by_card"]["right-card"],
                assessment=bundle["assessment"],
                pair_ledger=bundle["ledger"],
                source_snapshot=bundle["source_snapshot"],
                ignore_decision=valid_ignore,
                authorized_human_authorities={authority_ref: "attacker"},
                ignore_authority_registry_hash=typed_payload_hash(
                    {authority_ref: "attacker"}
                ),
            )
        registry_hash_mismatch_kwargs = deepcopy(ignored_kwargs)
        registry_hash_mismatch_kwargs[
            "ignore_authority_registry_hash"
        ] = EMPTY_IGNORE_AUTHORITY_REGISTRY_HASH
        mismatch_report = verify_opportunity_candidate(**registry_hash_mismatch_kwargs)
        self.assertEqual("FAIL", mismatch_report["verification_result"])
        self.assertTrue(
            any(
                row["field"].startswith("inputs.ignore_authority_registry.hash")
                and row["pass"] is False
                for row in mismatch_report["field_checks"]
            ),
            mismatch_report["field_checks"],
        )

        request = make_review_submission_request(
            candidate=bundle["candidate"], verification_report=bundle["report"]
        )
        forged = deepcopy(request)
        forged.pop("content_hash")
        forged["current_state"] = "active"
        forged["submission_eligible"] = False
        forged["human_decision"] = {"decision": "APPROVE"}
        forged = with_content_hash(forged)
        with self.assertRaises(CandidateRejected):
            validate_review_submission_request(
                forged, candidate=bundle["candidate"], verification_report=bundle["report"]
            )

    def test_cross_snapshot_projection_and_pair_outside_ledger_fail_closed(self) -> None:
        bundle = build_bundle()
        other_snapshot = make_source_snapshot(
            snapshot_id="source-sample-other",
            source_cutoff="2026-08-04T00:00:01Z",
            cards=list(bundle["cards_by_id"].values()),
            chunks_by_card=bundle["chunks_by_card"],
            source_rights={key: "PUBLIC_SAFE_SYNTHETIC" for key in bundle["cards_by_id"]},
            cluster_topk={"cluster-a": ["gap-card", "left-card", "right-card"]},
            trigger_receipt=bundle["trigger_receipt"],
            service_contracts_registry_ref="ServiceContracts-registry",
            service_contracts_registry_hash=ServiceContracts_HASH,
            configuration_policy_ref="relation-policy",
            configuration_policy_hash=Configuration_HASH,
        )
        other_ledger = make_pair_ledger(other_snapshot, cluster_id="cluster-a")
        with self.assertRaises(EvidenceRejected):
            assess_comparability(
                bundle["projections_by_card"]["left-card"],
                bundle["projections_by_card"]["right-card"],
                first_anchor_receipt=bundle["anchor_receipts_by_card"]["left-card"],
                second_anchor_receipt=bundle["anchor_receipts_by_card"]["right-card"],
                pair_ledger=other_ledger,
                source_snapshot=other_snapshot,
            )

        narrow_trigger = make_trigger_scope_receipt(
            trigger_type="weekly",
            topic_scope=["synthetic-topic"],
            paper_scope=[],
            limits={**LIMITS, "max_pairs_per_cluster": 1},
        )
        narrow_snapshot = make_source_snapshot(
            snapshot_id="source-sample-narrow",
            source_cutoff="2026-08-04T00:00:02Z",
            cards=list(bundle["cards_by_id"].values()),
            chunks_by_card=bundle["chunks_by_card"],
            source_rights={key: "PUBLIC_SAFE_SYNTHETIC" for key in bundle["cards_by_id"]},
            cluster_topk={"cluster-a": ["gap-card", "left-card", "right-card"]},
            trigger_receipt=narrow_trigger,
            service_contracts_registry_ref="ServiceContracts-registry",
            service_contracts_registry_hash=ServiceContracts_HASH,
            configuration_policy_ref="relation-policy",
            configuration_policy_hash=Configuration_HASH,
        )
        narrow_projections = {
            card_id: project_comparison_concepts(
                bundle["cards_by_id"][card_id], bundle["chunks_by_card"][card_id], narrow_snapshot
            )
            for card_id in ("left-card", "right-card")
        }
        narrow_ledger = make_pair_ledger(narrow_snapshot, cluster_id="cluster-a")
        narrow_receipts = {
            card_id: make_anchor_resolution_receipt(projection)
            for card_id, projection in narrow_projections.items()
        }
        self.assertNotIn(
            ["left-card", "right-card"],
            [item["card_ids"] for item in narrow_ledger["pairs"]],
        )
        with self.assertRaises(EvidenceRejected):
            assess_comparability(
                narrow_projections["left-card"],
                narrow_projections["right-card"],
                first_anchor_receipt=narrow_receipts["left-card"],
                second_anchor_receipt=narrow_receipts["right-card"],
                pair_ledger=narrow_ledger,
                source_snapshot=narrow_snapshot,
            )

    def test_forged_assessment_overall_and_builder_extra_field_fail_closed(self) -> None:
        bundle = build_bundle()
        forged_assessment = deepcopy(bundle["assessment"])
        forged_assessment.pop("content_hash")
        forged_assessment["overall"] = "not_comparable"
        forged_assessment = with_content_hash(forged_assessment)
        with self.assertRaises(EvidenceRejected):
            classify_tension(
                bundle["projections_by_card"]["left-card"],
                bundle["projections_by_card"]["right-card"],
                first_anchor_receipt=bundle["anchor_receipts_by_card"]["left-card"],
                second_anchor_receipt=bundle["anchor_receipts_by_card"]["right-card"],
                assessment=forged_assessment,
                pair_ledger=bundle["ledger"],
                source_snapshot=bundle["source_snapshot"],
            )

        extra_gap = deepcopy(bundle["gap"])
        extra_gap.pop("content_hash")
        extra_gap["attacker_extra"] = True
        extra_gap = with_content_hash(extra_gap)
        with self.assertRaises(CandidateRejected):
            build_opportunity_candidate(
                opportunity_kind="hybrid",
                source_snapshot=bundle["source_snapshot"],
                gaps=[extra_gap],
                tensions=[bundle["tension"]],
                anchor_resolution_receipts=list(bundle["anchor_receipts_by_card"].values()),
                assessments=[bundle["assessment"]],
                pair_ledgers=[bundle["ledger"]],
            )

    def test_assessment_rejects_duplicate_refs_extra_assessment_and_source_contract_tampering(self) -> None:
        bundle = build_bundle()

        duplicate_candidate = deepcopy(bundle["candidate"])
        duplicate_candidate.pop("content_hash")
        duplicate_candidate["gap_refs"].append(duplicate_candidate["gap_refs"][0])
        duplicate_candidate = with_content_hash(duplicate_candidate)
        kwargs = verify_arguments(bundle)
        kwargs["candidate"] = duplicate_candidate
        self.assertEqual("FAIL", verify_opportunity_candidate(**kwargs)["verification_result"])

        kwargs = verify_arguments(bundle)
        kwargs["assessments_by_ref"] = {
            **bundle["assessments_by_ref"],
            "extra-unreferenced-assessment": bundle["assessment"],
        }
        self.assertEqual("FAIL", verify_opportunity_candidate(**kwargs)["verification_result"])

        mutations: list[tuple[str, dict]] = []
        trigger_extra = deepcopy(bundle["trigger_receipt"])
        trigger_extra.pop("content_hash")
        trigger_extra["attacker_extra"] = True
        mutations.append(("trigger_extra", {"trigger_receipt": with_content_hash(trigger_extra)}))

        trigger_bad_limit = deepcopy(bundle["trigger_receipt"])
        trigger_bad_limit.pop("content_hash")
        trigger_bad_limit["limits"]["max_clusters"] = 0
        mutations.append(("trigger_bad_limit", {"trigger_receipt": with_content_hash(trigger_bad_limit)}))

        source_flag = deepcopy(bundle["source_snapshot"])
        source_flag.pop("content_hash")
        source_flag["real_card_reads"] = 1
        mutations.append(("source_flag", {"source_snapshot": with_content_hash(source_flag)}))

        cards_bad_ownership = deepcopy(bundle["cards_by_id"])
        changed_card = deepcopy(cards_bad_ownership["left-card"])
        changed_card.pop("content_hash")
        changed_card["data_ownership"] = "unknown"
        cards_bad_ownership["left-card"] = with_content_hash(changed_card)
        mutations.append(("card_data_ownership", {"cards_by_id": cards_bad_ownership}))

        for label, mutation in mutations:
            with self.subTest(label=label):
                kwargs = verify_arguments(bundle)
                kwargs.update(mutation)
                self.assertEqual("FAIL", verify_opportunity_candidate(**kwargs)["verification_result"])

    def test_admission_rejects_forged_full_report_missing_required_check_wrong_evidence_hash_and_extra_field(self) -> None:
        bundle = build_bundle()
        report = bundle["report"]

        forged_full = deepcopy(report)
        forged_full.pop("content_hash")
        forged_full["field_checks"] = [
            {"field": "attacker.claims.complete", "expected": True, "observed": True, "pass": True}
        ]
        forged_full["dynamic_check_commitment"] = typed_payload_hash(
            {"field_checks": forged_full["field_checks"]}
        )
        forged_full["coverage_summary"]["field_check_count"] = 1

        missing_required = deepcopy(report)
        missing_required.pop("content_hash")
        missing_field = missing_required["required_check_registry"][missing_required["required_check_ids"][0]]
        missing_required["field_checks"] = [
            item for item in missing_required["field_checks"] if item["field"] != missing_field
        ]
        missing_required["dynamic_check_commitment"] = typed_payload_hash(
            {"field_checks": missing_required["field_checks"]}
        )
        missing_required["coverage_summary"]["field_check_count"] = len(missing_required["field_checks"])

        wrong_evidence = deepcopy(report)
        wrong_evidence.pop("content_hash")
        wrong_evidence["evidence_set_hash"] = {
            "algorithm": "sha256",
            "hash_kind": "rfc8785_jcs_sha256",
            "scope": "PAYLOAD_EXCLUDING_CONTENT_HASH",
            "value": "0" * 64,
        }

        extra_report_field = deepcopy(report)
        extra_report_field.pop("content_hash")
        extra_report_field["attacker_extra"] = True

        for label, forged in (
            ("forged_full", forged_full),
            ("missing_required", missing_required),
            ("wrong_evidence", wrong_evidence),
            ("extra_field", extra_report_field),
        ):
            with self.subTest(label=label):
                with self.assertRaises(CandidateRejected):
                    make_review_submission_request(
                        candidate=bundle["candidate"],
                        verification_report=with_content_hash(forged),
                    )

    def test_assessment_has_no_semantic_import_from_the_producer(self) -> None:
        path = (Path(__file__).resolve().parents[3] / "source") / "memorive" / "documents" / "card_review" / "opportunity_verification.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
        self.assertNotIn("research_opportunities.core", imports)


if __name__ == "__main__":
    unittest.main()
