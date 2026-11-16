"""The research runtime's entry to bibliographic identity resolution.

verify_source_observation and _claim_from_observation read the desktop's source observations,
including the kind whose title, authors and year are optional. resolve_identity_batch runs the
shared algorithm (resolve_identity_batch_with in
literature_discovery/bibliographic_identity_literature_discovery_bibliographic_identity.py) with
this module's PRODUCER. memorive_research_runtime/acquisition.py calls it.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping, Sequence

from literature_discovery.connector_framework import canonical_json, sha256_json
from literature_discovery.bibliographic_identity_literature_discovery_bibliographic_identity import (  # the code both modules share
    IdentityResolutionError,
    _normalized_text,
    _published_year,
    _normalization_result,
    normalize_doi_identifier,
    normalize_arxiv_identifier,
    normalize_pmid_identifier,
    normalize_identifier,
    _canonical_identifier,
    _identifier_token,
    _manifestation_role,
    initialization_candidate_base_projection,
    resolve_identity_batch_with,
)


SCHEMA_REVISION = "0.1"
PRODUCER = "Desktop/DESKTOP/LITERATURE_DISCOVERY_SUCCESSOR"
IDENTITY_WRITER = "LITERATURE_DISCOVERY.IDENTITY_RESOLVER"
CANDIDATE_WRITER = "LITERATURE_DISCOVERY.CANDIDATE_REGISTRY"

CONFLICT_REASON_CODES = {
    "EXACT_IDENTIFIER_METADATA_CONFLICT",
    "CROSS_IDENTIFIER_CONFLICT",
    "UPSTREAM_IDENTITY_CONFLICT",
    "INVALID_IDENTIFIER",
    "VERSION_EVIDENCE_CONFLICT",
}

VERSION_RELATION_TYPES = {
    "ARXIV_VERSION_SUCCESSOR",
    "PREPRINT_TO_CONFERENCE",
    "PREPRINT_TO_VOR",
    "CONFERENCE_TO_VOR",
    "AAM_TO_VOR",
}

VERSION_RELATION_ROLE_PAIRS = {
    "PREPRINT_TO_CONFERENCE": ("PREPRINT", "CONFERENCE"),
    "PREPRINT_TO_VOR": ("PREPRINT", "VERSION_OF_RECORD"),
    "CONFERENCE_TO_VOR": ("CONFERENCE", "VERSION_OF_RECORD"),
    "AAM_TO_VOR": ("AUTHOR_ACCEPTED_MANUSCRIPT", "VERSION_OF_RECORD"),
}

PROHIBITED_REVIEW_ACTIONS = [
    "AUTO_MERGE",
    "AUTO_SELECT",
    "CANDIDATE_CARD_ELIGIBILITY",
]


def verify_source_observation(
    observation: Mapping[str, Any],
    evidence_registry: Mapping[str, Mapping[str, Any]],
) -> None:
    object_id = str(observation.get("object_id", ""))
    if observation.get("schema_version") not in {
        "discovery.data_contracts.source-observation.0.1",
        "desktop.desktop.source-observation.1",
        "desktop.source-observation.e6.1",
    }:
        raise IdentityResolutionError("SOURCE_OBSERVATION_SCHEMA_MISMATCH", [object_id])
    if observation.get("object_type") != "SourceObservation":
        raise IdentityResolutionError("SOURCE_OBSERVATION_TYPE_MISMATCH", [object_id])
    if not re.fullmatch(r"source_observation_[a-f0-9]{24}", object_id):
        raise IdentityResolutionError("INVALID_SOURCE_OBSERVATION_ID", [object_id])
    body = copy.deepcopy(dict(observation))
    recorded_hash = body.pop("content_hash", None)
    if recorded_hash != sha256_json(body):
        raise IdentityResolutionError("SOURCE_OBSERVATION_HASH_MISMATCH", [object_id])
    state_contract = {
        "identity_status": {"VERIFIED", "CONFLICT", "UNRESOLVED"},
        "access_status": {"METADATA_ONLY"},
        "decision_status": {"PENDING"},
        "candidate_card_status": {"NOT_REQUESTED"},
        "promotion_status": {"NOT_ELIGIBLE"},
        "source_connector": {"ARXIV", "CROSSREF"},
    }
    if any(observation.get(field) not in allowed for field, allowed in state_contract.items()):
        raise IdentityResolutionError("SOURCE_OBSERVATION_SCHEMA_MISMATCH", [object_id])
    registry_entry = evidence_registry.get(object_id)
    if not isinstance(registry_entry, Mapping):
        raise IdentityResolutionError("EVIDENCE_REGISTRY_ENTRY_MISSING", [object_id])
    source_evidence = observation.get("source_evidence")
    if not isinstance(source_evidence, Mapping):
        raise IdentityResolutionError("SOURCE_EVIDENCE_MISSING", [object_id])
    exact_fields = {
        "content_hash": recorded_hash,
        "source_snapshot_id": observation.get("source_snapshot_id"),
        "snapshot_sha256": source_evidence.get("snapshot_sha256"),
        "normalized_record_sha256": source_evidence.get("normalized_record_sha256"),
    }
    if dict(registry_entry) != exact_fields:
        raise IdentityResolutionError("EVIDENCE_REGISTRY_HASH_MISMATCH", [object_id])
    for field in ("snapshot_sha256", "normalized_record_sha256"):
        if not re.fullmatch(r"[a-f0-9]{64}", str(exact_fields[field])):
            raise IdentityResolutionError("SOURCE_EVIDENCE_HASH_INVALID", [object_id])
    if observation.get("schema_version") == "discovery.data_contracts.source-observation.0.1" and source_evidence.get("network_used") is not False:
        raise IdentityResolutionError("NETWORK_EVIDENCE_NOT_OFFLINE", [object_id])
    classification = source_evidence.get("fixture_classification")
    if classification not in {"SYNTHETIC_PUBLIC_SAFE_A0", "DESKTOP_METADATA_SNAPSHOT"}:
        raise IdentityResolutionError("FIXTURE_CLASSIFICATION_REJECTED", [object_id])
    if observation.get("schema_version") == "discovery.data_contracts.source-observation.0.1":
        if classification != "SYNTHETIC_PUBLIC_SAFE_A0" or set(source_evidence) != {
            "snapshot_sha256", "normalized_record_sha256", "fixture_classification", "network_used"
        }:
            raise IdentityResolutionError("SOURCE_EVIDENCE_SCHEMA_MISMATCH", [object_id])
    elif (
        classification != "DESKTOP_METADATA_SNAPSHOT"
        or set(source_evidence) != {
            "snapshot_sha256", "normalized_record_sha256", "fixture_classification",
            "network_used", "cache_hit", "acquisition_request_id"
        }
        or type(source_evidence.get("network_used")) is not bool
        or type(source_evidence.get("cache_hit")) is not bool
        or (source_evidence["network_used"] and source_evidence["cache_hit"])
        or not isinstance(source_evidence.get("acquisition_request_id"), str)
        or not source_evidence["acquisition_request_id"]
        or observation.get("discovery_origin") != "KEYWORD_SEARCH"
    ):
        raise IdentityResolutionError("SOURCE_EVIDENCE_SCHEMA_MISMATCH", [object_id])


def _claim_from_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    identifiers = observation.get("identifiers")
    bibliographic = observation.get("bibliographic")
    if not isinstance(identifiers, Mapping) or not isinstance(bibliographic, Mapping):
        raise IdentityResolutionError("SOURCE_OBSERVATION_REQUIRED_FIELDS_MISSING", [str(observation.get("object_id"))])
    normalized_identifiers: list[dict[str, Any]] = []
    invalid_kinds: list[str] = []
    doi_value = identifiers.get("doi")
    if doi_value is not None:
        result = normalize_doi_identifier(doi_value)
        (normalized_identifiers if result["valid"] else invalid_kinds).append(
            _canonical_identifier(result) if result["valid"] else "DOI"
        )
    arxiv_value = identifiers.get("arxiv_id")
    if arxiv_value is not None:
        source_record_id = identifiers.get("source_record_id")
        candidate_value = source_record_id if isinstance(source_record_id, str) else arxiv_value
        candidate_result = normalize_arxiv_identifier(candidate_value)
        if candidate_result["valid"] and candidate_result["base_value"] != str(arxiv_value).lower():
            candidate_result = normalize_arxiv_identifier(arxiv_value)
        (normalized_identifiers if candidate_result["valid"] else invalid_kinds).append(
            _canonical_identifier(candidate_result) if candidate_result["valid"] else "ARXIV"
        )
    pmid_value = identifiers.get("pmid")
    if pmid_value is not None:
        result = normalize_pmid_identifier(pmid_value)
        (normalized_identifiers if result["valid"] else invalid_kinds).append(
            _canonical_identifier(result) if result["valid"] else "PMID"
        )
    normalized_identifiers = sorted(
        {canonical_json(item): item for item in normalized_identifiers}.values(),
        key=lambda item: (item["kind"], item["value"]),
    )
    authors_value = bibliographic.get("authors")
    if not isinstance(authors_value, list) or (not authors_value and observation.get('schema_version')!='desktop.source-observation.e6.1'):
        raise IdentityResolutionError("BIBLIOGRAPHIC_AUTHORS_MISSING", [str(observation.get("object_id"))])
    authors = []
    for author in authors_value:
        if not isinstance(author, Mapping) or not isinstance(author.get("display_name"), str):
            raise IdentityResolutionError("BIBLIOGRAPHIC_AUTHORS_INVALID", [str(observation.get("object_id"))])
        authors.append(str(author["display_name"]))
    title = bibliographic.get("title")
    if not isinstance(title, str) or not title.strip():
        raise IdentityResolutionError("BIBLIOGRAPHIC_TITLE_MISSING", [str(observation.get("object_id"))])
    metadata_signature = {
        "title": _normalized_text(title),
        "authors": sorted(_normalized_text(author) for author in authors),
        "published_year": _published_year(bibliographic.get("published_date")),
    }
    return {
        "observation_ref": observation["object_id"],
        "identifiers": normalized_identifiers,
        "identifier_tokens": sorted(_identifier_token(item) for item in normalized_identifiers),
        "invalid_identifier_kinds": sorted(set(invalid_kinds)),
        "metadata_signature": metadata_signature,
        "optional_metadata": observation.get("schema_version") == "desktop.source-observation.e6.1",
        "bibliographic_claim": {
            "observation_ref": observation["object_id"],
            "title": title.strip(),
            "authors": authors,
            "published_year": metadata_signature["published_year"],
            "record_type": str(bibliographic.get("record_type") or "unknown"),
        },
        "identity_status": observation.get("identity_status"),
        "upstream_errors": sorted(str(value) for value in observation.get("errors", [])),
        "discovery_origin": str(observation.get("discovery_origin") or "SYNTHETIC_A0_REPLAY"),
        "evidence_hash": str(observation["source_evidence"]["normalized_record_sha256"]),
    }


def resolve_identity_batch(
    observations: Sequence[Mapping[str, Any]],
    evidence_registry: Mapping[str, Mapping[str, Any]],
    *,
    version_links: Sequence[Mapping[str, Any]] = (),
    hints: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Resolve one deterministic offline batch of research runtime observations without any I/O.

    The algorithm is the one literature discovery uses (resolve_identity_batch_with); this module
    passes its own observation check and claim reader, its PRODUCER, and is_synthetic=False."""

    return resolve_identity_batch_with(
        observations,
        evidence_registry,
        version_links=version_links,
        hints=hints,
        verify=verify_source_observation,
        claim_from_observation=_claim_from_observation,
        producer=PRODUCER,
        is_synthetic=False,
    )


__all__ = [
    "IdentityResolutionError",
    "normalize_doi_identifier",
    "normalize_arxiv_identifier",
    "normalize_pmid_identifier",
    "resolve_identity_batch",
    "verify_source_observation",
]

# Names this module exported before; restored with the code behind them.
__all__ = [*__all__, 'normalize_identifier', 'initialization_candidate_base_projection']
