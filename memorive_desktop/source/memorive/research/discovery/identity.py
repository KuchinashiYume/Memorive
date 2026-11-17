"""The research runtime's entry to bibliographic identity resolution.

verify_source_observation and _claim_from_observation read the desktop's source observations with
DESKTOP_RULES (the shared module's checks; the desktop's observations include the kind whose authors
may be empty). resolve_identity_batch runs the
shared algorithm (resolve_identity_batch_with in
memorive/research/discovery/bibliographic_identity_literature_discovery_bibliographic_identity.py) with
this module's PRODUCER. acquisition.py calls it.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from memorive.research.discovery.bibliographic_identity_literature_discovery_bibliographic_identity import (  # the code both modules share
    IdentityResolutionError,
    _normalization_result,
    normalize_doi_identifier,
    normalize_arxiv_identifier,
    normalize_pmid_identifier,
    normalize_identifier,
    _manifestation_role,
    initialization_candidate_base_projection,
    resolve_identity_batch_with,
    ObservationRules,
    verify_source_observation as shared_verify_source_observation,
    _claim_from_observation as shared_claim_from_observation,
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


DESKTOP_RULES = ObservationRules(
    schema_versions=frozenset({
        "discovery.data_contracts.source-observation.0.1",
        "desktop.desktop.source-observation.1",
        "desktop.source-observation.e6.1",
    }),
    offline_schema="discovery.data_contracts.source-observation.0.1",
    snapshot_classification="DESKTOP_METADATA_SNAPSHOT",
    snapshot_evidence_keys=frozenset({
        "snapshot_sha256", "normalized_record_sha256", "fixture_classification",
        "network_used", "cache_hit", "acquisition_request_id"
    }),
    cache_flags=True,
    optional_metadata_schema="desktop.source-observation.e6.1",
)


def verify_source_observation(
    observation: Mapping[str, Any],
    evidence_registry: Mapping[str, Mapping[str, Any]],
) -> None:
    """The shared check of a source observation, with the desktop's rules."""
    shared_verify_source_observation(observation, evidence_registry, DESKTOP_RULES)


def _claim_from_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    """The shared identity claim of a source observation, with the desktop's rules."""
    return shared_claim_from_observation(observation, DESKTOP_RULES)


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
