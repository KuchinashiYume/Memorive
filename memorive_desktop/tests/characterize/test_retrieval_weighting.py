"""retrieval_weighting code still in the product: query_dimensions, field_registry, contracts.

Pinned results with envelopes left out (content_hash and values equal to it; the query
profile binds the field registry by its envelope, which shows up as <envelope>). None
of these write to disk. The envelope helpers themselves (make_hashed_payload,
verify_hashed_payload) are not pinned: they are what the cleanup changes.
"""
import json
from copy import deepcopy
from pathlib import Path

from memorive.research.ranking import contracts, field_registry, query_dimensions
from memorive.platform.jcs import typed_payload_hash
from tests.characterize.golden import check, outcome, rejected_as

HERE = Path(__file__).parent
FIXTURES = HERE.parent / "modules" / "retrieval_weighting" / "field_vector_acceptance" / "fixtures" / "source_snapshot_public_safe.json"
CARDS = ["FX-ACTIVE-SELF-LITERATURE-R1", "FX-ACTIVE-ENTRUSTED-NOTE-R1", "FX-ACTIVE-SELF-LITERATURE-R2-SUCCESSOR",
         "FX-PENDING-SELF-LITERATURE-R1"]
ALLOCATIONS = [
    {"field_id": "author_conclusion", "allocation": 0.4, "reason_code": "CONCLUSION_INTENT",
     "evidence_ref": "fixture://query/1#conclusion"},
    {"field_id": "method/{item}", "allocation": 0.6, "reason_code": "METHOD_INTENT",
     "evidence_ref": "fixture://query/1#method"},
]


def _profile_args(**changes):
    registry = field_registry.get_field_registry()
    args = dict(query_hash=typed_payload_hash({"label": "query"}), field_registry_hash=registry["content_hash"],
                allocations=deepcopy(ALLOCATIONS), allocation_method="frozen_keyword_soft_allocation_v1",
                fallback_floor=0.0, missing_fields=["author_limitations_outlook/limitations/{item}/quote"],
                reason_codes=["CONCLUSION_INTENT", "METHOD_INTENT"], policy_version="query-policy-v1",
                frozen_registry=registry)
    args.update(changes)
    return args


def test_query_dimension_profile():
    registry = field_registry.get_field_registry()
    variants = {
        "as_built": {},
        "single_allocation": {"allocations": [dict(ALLOCATIONS[0], allocation=1.0)], "reason_codes": ["CONCLUSION_INTENT"]},
        "no_allocation_with_missing": {"allocations": [], "reason_codes": []},
        "no_allocation_no_missing": {"allocations": [], "reason_codes": [], "missing_fields": []},
        "floor_above_allocation": {"fallback_floor": 0.5},
        "reason_code_omitted": {"reason_codes": ["CONCLUSION_INTENT"]},
        "field_allocated_and_missing": {"missing_fields": ["author_conclusion"]},
        "unknown_field": {"allocations": [dict(ALLOCATIONS[0], field_id="a_unknown_field"), ALLOCATIONS[1]]},
        "allocations_not_summing": {"allocations": [dict(ALLOCATIONS[0], allocation=0.9), ALLOCATIONS[1]]},
        "negative_allocation": {"allocations": [dict(ALLOCATIONS[0], allocation=-0.4), dict(ALLOCATIONS[1], allocation=1.4)]},
        "duplicate_field": {"allocations": [ALLOCATIONS[0], dict(ALLOCATIONS[0], allocation=0.6)]},
        "empty_policy_version": {"policy_version": ""},
    }
    cases = {name: outcome(query_dimensions.build_query_dimension_profile, **_profile_args(**changes))
             for name, changes in variants.items()}
    # Today the profile binds the registry by its envelope hash: keep only that it is rejected.
    cases["other_registry_hash"] = rejected_as(query_dimensions.build_query_dimension_profile, **_profile_args(
        field_registry_hash=typed_payload_hash({"label": "other-registry"})))
    profile = query_dimensions.build_query_dimension_profile(**_profile_args())
    cases["validate_as_built"] = outcome(query_dimensions.validate_query_dimension_profile, profile, frozen_registry=registry)
    check("retrieval_weighting_query_dimensions", cases, HERE)


def test_field_registry_and_card_expansion():
    registry = field_registry.get_field_registry()
    records = {row["fixture_id"]: row for row in json.loads(FIXTURES.read_text(encoding="utf-8"))["records"]}
    cases = {"registry": outcome(lambda: registry)}
    for text in ("  Mixed   spacing\tand\nlines  ", "全角　空格与标点，", "", "x" * 3):
        cases[f"canonical_{text[:8]!r}"] = outcome(field_registry.canonicalize_field_text, text, "summary")
    for card_id in CARDS:
        record = records[card_id]
        cases[f"expand_{card_id}"] = outcome(field_registry.expand_card_fields, record["card"],
                                             card_artifact_ref=record["authority"]["card_artifact_ref"])
    card = deepcopy(records[CARDS[0]]["card"])
    card.pop(sorted(card)[0])
    cases["expand_card_missing_first_key"] = outcome(field_registry.expand_card_fields, card, card_artifact_ref="fixture://card")
    check("retrieval_weighting_field_registry", cases, HERE)


def test_contract_helpers():
    cases = {}
    for value in ("text", "", "  ", None, 3, ["x"]):
        cases[f"non_empty_string_{value!r}"] = outcome(contracts.require_non_empty_string, value, "field")
    for value in ("A" * 64, "a" * 64, "A" * 63, "G" * 64, None):
        cases[f"sha_{value!r}"] = outcome(contracts.require_sha256, value, "field")
    for value in (["a", "b"], ["b", "a"], ["a", "a"], [], ["a", ""], "a", None):
        cases[f"refs_{value!r}"] = outcome(contracts.require_non_empty_refs, value, "field")
    descriptor = typed_payload_hash({"label": "x"})
    for name, value in (("typed", descriptor), ("upper", dict(descriptor, value=descriptor["value"].upper())),
                        ("lower", dict(descriptor, value=descriptor["value"].lower())),
                        ("extra_key", dict(descriptor, extra=1)), ("missing_value", {k: v for k, v in descriptor.items() if k != "value"}),
                        ("not_mapping", "x")):
        cases[f"hash_descriptor_{name}"] = outcome(contracts.validate_hash_descriptor, value, "field")
    for value in ("text", {"b": 1, "a": [1, 2.5, None]}, [], 0, None):
        cases[f"value_hash_{value!r}"] = outcome(contracts.make_value_hash, value)
    check("retrieval_weighting_contract_helpers", cases, HERE)
