"""BIBLIOGRAPHIC-IDENTITY/LITERATURE_DISCOVERY deterministic, pure-offline bibliographic identity resolver.

The resolver consumes already-frozen DataContracts SourceObservation objects and an exact
evidence registry.  It contains no transport, provider SDK, model integration,
production writer, registry adapter, clock, randomness, or environment lookup.
"""

from __future__ import annotations

import copy
import re
import unicodedata
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from literature_discovery.source_connectors_literature_discovery_connector_framework import (
    ConnectorError,
    canonical_json,
    normalize_arxiv_id,
    normalize_doi,
    sha256_json,
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


class IdentityResolutionError(RuntimeError):
    """Fail-closed error with a stable machine code and evidence references."""

    def __init__(self, code: str, evidence_refs: Iterable[str] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.evidence_refs = sorted(set(evidence_refs))

    def as_record(self) -> dict[str, Any]:
        return {"code": self.code, "evidence_refs": self.evidence_refs}


class _DisjointSet:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        first, second = sorted((left_root, right_root))
        self.parent[second] = first

    def groups(self) -> list[list[str]]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for value in sorted(self.parent):
            grouped[self.find(value)].append(value)
        return sorted((sorted(values) for values in grouped.values()), key=lambda values: values[0])


def _stable_id(prefix: str, payload: Any) -> str:
    return prefix + sha256_json(payload)[:24]


def _finalize_content_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result.pop("content_hash", None)
    result["content_hash"] = sha256_json(result).upper()
    return result


def _normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    tokens: list[str] = []
    current: list[str] = []
    for character in normalized:
        if character.isalnum():
            current.append(character)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return " ".join(tokens)


def _published_year(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.match(r"^(\d{4})", value)
    return int(match.group(1)) if match else None


def _normalization_result(
    kind: str,
    input_value: Any,
    *,
    valid: bool,
    canonical_value: str | None,
    base_value: str | None,
    version: int | None,
    error_code: str | None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "input": input_value,
        "valid": valid,
        "canonical_value": canonical_value,
        "base_value": base_value,
        "version": version,
        "error_code": error_code,
        "existence_verified": False,
    }


def normalize_doi_identifier(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return _normalization_result(
            "DOI", value, valid=False, canonical_value=None, base_value=None,
            version=None, error_code="INVALID_DOI"
        )
    try:
        canonical = normalize_doi(value)
    except ConnectorError:
        canonical = None
    if canonical is None:
        return _normalization_result(
            "DOI", value, valid=False, canonical_value=None, base_value=None,
            version=None, error_code="INVALID_DOI"
        )
    return _normalization_result(
        "DOI", value, valid=True, canonical_value=canonical, base_value=None,
        version=None, error_code=None
    )


def normalize_arxiv_identifier(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return _normalization_result(
            "ARXIV", value, valid=False, canonical_value=None, base_value=None,
            version=None, error_code="INVALID_ARXIV_ID"
        )
    try:
        base_value, canonical = normalize_arxiv_id(value.strip())
    except ConnectorError:
        return _normalization_result(
            "ARXIV", value, valid=False, canonical_value=None, base_value=None,
            version=None, error_code="INVALID_ARXIV_ID"
        )
    match = re.fullmatch(r"\d{4}\.\d{4,5}(?:v(\d+))?", canonical)
    version = int(match.group(1)) if match and match.group(1) else None
    if match is None or version == 0:
        return _normalization_result(
            "ARXIV", value, valid=False, canonical_value=None, base_value=None,
            version=None, error_code="INVALID_ARXIV_ID"
        )
    return _normalization_result(
        "ARXIV", value, valid=True, canonical_value=canonical,
        base_value=base_value, version=version, error_code=None
    )


def normalize_pmid_identifier(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return _normalization_result(
            "PMID", value, valid=False, canonical_value=None, base_value=None,
            version=None, error_code="INVALID_PMID"
        )
    token = value.strip()
    prefix_match = re.fullmatch(r"(?i)pmid\s*:\s*([0-9]+)", token)
    url_match = re.fullmatch(
        r"(?i)https?://pubmed\.ncbi\.nlm\.nih\.gov/([0-9]+)/?", token
    )
    if prefix_match:
        digits = prefix_match.group(1)
    elif url_match:
        digits = url_match.group(1)
    else:
        digits = token
    if not re.fullmatch(r"[0-9]+", digits) or len(digits) > 12:
        return _normalization_result(
            "PMID", value, valid=False, canonical_value=None, base_value=None,
            version=None, error_code="INVALID_PMID"
        )
    canonical = digits.lstrip("0")
    if not canonical or len(canonical) > 9:
        return _normalization_result(
            "PMID", value, valid=False, canonical_value=None, base_value=None,
            version=None, error_code="INVALID_PMID"
        )
    return _normalization_result(
        "PMID", value, valid=True, canonical_value=canonical,
        base_value=None, version=None, error_code=None
    )


def normalize_identifier(kind: str, value: Any) -> dict[str, Any]:
    normalized_kind = kind.strip().upper() if isinstance(kind, str) else ""
    if normalized_kind == "DOI":
        return normalize_doi_identifier(value)
    if normalized_kind == "ARXIV":
        return normalize_arxiv_identifier(value)
    if normalized_kind == "PMID":
        return normalize_pmid_identifier(value)
    return _normalization_result(
        normalized_kind or "UNKNOWN", value, valid=False, canonical_value=None,
        base_value=None, version=None, error_code="UNSUPPORTED_IDENTIFIER_KIND"
    )


def _canonical_identifier(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": result["kind"],
        "value": result["canonical_value"],
        "base_value": result["base_value"],
        "version": result["version"],
    }


def _identifier_token(identifier: Mapping[str, Any]) -> str:
    return f"{identifier['kind']}:{identifier['value']}"


def _observation_alias(observation_ref: str) -> str:
    match = re.fullmatch(r"source_observation_([a-f0-9]{24})", observation_ref)
    if not match:
        raise IdentityResolutionError("INVALID_SOURCE_OBSERVATION_ID", [observation_ref])
    return "SOURCE_OBSERVATION:" + match.group(1).upper()


def verify_source_observation(
    observation: Mapping[str, Any],
    evidence_registry: Mapping[str, Mapping[str, Any]],
) -> None:
    object_id = str(observation.get("object_id", ""))
    if observation.get("schema_version") not in {
        "discovery.data_contracts.source-observation.0.1",
        "desktop.desktop.source-observation.1",
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


def _manifestation_role(record_type: Any) -> str:
    normalized = _normalized_text(str(record_type or ""))
    if "arxiv" in normalized or "preprint" in normalized:
        return "PREPRINT"
    if "proceedings" in normalized or "conference" in normalized:
        return "CONFERENCE"
    if "accepted" in normalized or normalized == "aam":
        return "AUTHOR_ACCEPTED_MANUSCRIPT"
    if "journal" in normalized or "version of record" in normalized or normalized == "vor":
        return "VERSION_OF_RECORD"
    return "UNKNOWN"


def _role_from_claims(claims: Sequence[Mapping[str, Any]]) -> str:
    priority = {
        "UNKNOWN": 0,
        "PREPRINT": 1,
        "CONFERENCE": 2,
        "AUTHOR_ACCEPTED_MANUSCRIPT": 3,
        "VERSION_OF_RECORD": 4,
    }
    roles = [_manifestation_role(claim["record_type"]) for claim in claims]
    return max(roles, key=lambda role: (priority[role], role))


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
    if not isinstance(authors_value, list) or not authors_value:
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


def _dedupe_conflict_specs(specs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[bytes, dict[str, Any]] = {}
    for spec in specs:
        normalized = {
            "reason_code": spec["reason_code"],
            "source_observation_refs": sorted(set(spec["source_observation_refs"])),
            "identifier_claims": sorted(set(spec.get("identifier_claims", []))),
        }
        unique[canonical_json(normalized)] = normalized
    return sorted(unique.values(), key=lambda item: canonical_json(item))


def resolve_identity_batch(
    observations: Sequence[Mapping[str, Any]],
    evidence_registry: Mapping[str, Mapping[str, Any]],
    *,
    version_links: Sequence[Mapping[str, Any]] = (),
    hints: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Resolve one deterministic offline batch without performing any I/O."""

    copied_observations = [copy.deepcopy(dict(item)) for item in observations]
    object_ids = [str(item.get("object_id", "")) for item in copied_observations]
    if len(object_ids) != len(set(object_ids)):
        raise IdentityResolutionError("DUPLICATE_SOURCE_OBSERVATION_ID", object_ids)
    for observation in copied_observations:
        verify_source_observation(observation, evidence_registry)

    claims = [_claim_from_observation(item) for item in copied_observations]
    claims_by_ref = {claim["observation_ref"]: claim for claim in claims}
    conflict_specs: list[dict[str, Any]] = []

    for claim in claims:
        if claim["invalid_identifier_kinds"] or not claim["identifiers"]:
            conflict_specs.append({
                "reason_code": "INVALID_IDENTIFIER",
                "source_observation_refs": [claim["observation_ref"]],
                "identifier_claims": claim["invalid_identifier_kinds"],
            })
        if claim["identity_status"] == "CONFLICT" or claim["upstream_errors"]:
            conflict_specs.append({
                "reason_code": "UPSTREAM_IDENTITY_CONFLICT",
                "source_observation_refs": [claim["observation_ref"]],
                "identifier_claims": claim["identifier_tokens"],
            })

    by_token: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        for token in claim["identifier_tokens"]:
            by_token[token].append(claim)
    for token, token_claims in sorted(by_token.items()):
        if len(token_claims) < 2:
            continue
        signatures = {canonical_json(claim["metadata_signature"]) for claim in token_claims}
        if len(signatures) > 1:
            conflict_specs.append({
                "reason_code": "EXACT_IDENTIFIER_METADATA_CONFLICT",
                "source_observation_refs": [claim["observation_ref"] for claim in token_claims],
                "identifier_claims": [token],
            })

    for pivot_kind, secondary_kind in (("ARXIV", "DOI"), ("PMID", "DOI"), ("DOI", "PMID")):
        for pivot_token, token_claims in sorted(by_token.items()):
            if not pivot_token.startswith(pivot_kind + ":"):
                continue
            secondary_values = {
                token
                for claim in token_claims
                for token in claim["identifier_tokens"]
                if token.startswith(secondary_kind + ":")
            }
            if len(secondary_values) > 1:
                conflict_specs.append({
                    "reason_code": "CROSS_IDENTIFIER_CONFLICT",
                    "source_observation_refs": [claim["observation_ref"] for claim in token_claims],
                    "identifier_claims": [pivot_token, *sorted(secondary_values)],
                })

    conflict_specs = _dedupe_conflict_specs(conflict_specs)
    conflicted_refs = {
        ref for spec in conflict_specs for ref in spec["source_observation_refs"]
    }
    unresolved_refs = {
        claim["observation_ref"]
        for claim in claims
        if claim["identity_status"] == "UNRESOLVED"
    }

    observation_dsu = _DisjointSet(object_ids)
    for token_claims in by_token.values():
        eligible_refs = sorted(
            claim["observation_ref"]
            for claim in token_claims
            if claim["observation_ref"] not in conflicted_refs | unresolved_refs
        )
        for ref in eligible_refs[1:]:
            observation_dsu.union(eligible_refs[0], ref)

    manifestation_interim: dict[str, dict[str, Any]] = {}
    observation_to_manifestation: dict[str, str] = {}
    bibliographic_identities: list[dict[str, Any]] = []
    for ref_group in observation_dsu.groups():
        group_claims = [claims_by_ref[ref] for ref in ref_group]
        identifiers = sorted(
            {
                canonical_json(identifier): identifier
                for claim in group_claims
                for identifier in claim["identifiers"]
            }.values(),
            key=lambda item: (item["kind"], item["value"]),
        )
        if not identifiers:
            raise IdentityResolutionError("NO_VALID_EXACT_IDENTIFIER", ref_group)
        group_conflicted = any(ref in conflicted_refs for ref in ref_group)
        group_unresolved = any(ref in unresolved_refs for ref in ref_group)
        if group_conflicted:
            identity_status = "CONFLICT"
        elif group_unresolved:
            identity_status = "UNRESOLVED"
        else:
            identity_status = "VERIFIED"
        id_payload = sorted(_identifier_token(item) for item in identifiers)
        if identity_status == "VERIFIED":
            manifestation_id = _stable_id("manifestation_identity_", id_payload)
        else:
            manifestation_id = _stable_id("manifestation_claim_", [id_payload, ref_group])
        identity_id = _stable_id("bibliographic_identity_", [manifestation_id, id_payload])
        evidence_refs = sorted("sha256:" + claims_by_ref[ref]["evidence_hash"] for ref in ref_group)
        identity = _finalize_content_hash({
            "schema_version": "discovery.service_contracts.bibliographic-identity.0.1",
            "object_type": "BibliographicIdentity",
            "object_id": identity_id,
            "revision": 1,
            "producer": PRODUCER,
            "single_writer": IDENTITY_WRITER,
            "consumers": ["LITERATURE_DISCOVERY.CANDIDATE_REGISTRY", "NOVELTY-ASSESSMENT/CROSS"],
            "identity_status": identity_status,
            "canonical_identifiers": identifiers,
            "manifestation_id": manifestation_id,
            "source_observation_refs": sorted(ref_group),
            "evidence_refs": evidence_refs,
            "existence_evidence_level": (
                "TRUSTED_FROZEN_SOURCE_OBSERVATION"
                if identity_status == "VERIFIED"
                else "CONFLICTING_FROZEN_SOURCE_OBSERVATION"
            ),
            "auto_merge_allowed": identity_status == "VERIFIED",
        })
        bibliographic_identities.append(identity)
        manifestation_interim[manifestation_id] = {
            "manifestation_id": manifestation_id,
            "identity_ref": identity_id,
            "identity_status": identity_status,
            "identifier_set": identifiers,
            "source_observation_refs": sorted(ref_group),
            "bibliographic_claims": sorted(
                (claims_by_ref[ref]["bibliographic_claim"] for ref in ref_group),
                key=lambda item: item["observation_ref"],
            ),
        }
        for ref in ref_group:
            observation_to_manifestation[ref] = manifestation_id

    manifestation_ids = sorted(manifestation_interim)
    work_dsu = _DisjointSet(manifestation_ids)
    version_edges: list[dict[str, Any]] = []
    edge_keys: set[bytes] = set()

    arxiv_versions: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for manifestation_id, manifestation in manifestation_interim.items():
        if manifestation["identity_status"] != "VERIFIED":
            continue
        for identifier in manifestation["identifier_set"]:
            if identifier["kind"] == "ARXIV" and identifier["version"] is not None:
                arxiv_versions[str(identifier["base_value"])].append(
                    (int(identifier["version"]), manifestation_id)
                )
    for base_value, versions in sorted(arxiv_versions.items()):
        ordered = sorted(set(versions))
        for (from_version, from_id), (to_version, to_id) in zip(ordered, ordered[1:]):
            if from_id == to_id:
                continue
            work_dsu.union(from_id, to_id)
            edge = {
                "from_manifestation_id": from_id,
                "to_manifestation_id": to_id,
                "relation_type": "ARXIV_VERSION_SUCCESSOR",
                "evidence_ref": f"identifier:arxiv:{base_value}:v{from_version}->v{to_version}",
                "work_anchor": f"ARXIV_BASE:{base_value}",
            }
            edge["edge_id"] = _stable_id("version_edge_", edge)
            key = canonical_json(edge)
            if key not in edge_keys:
                edge_keys.add(key)
                version_edges.append(edge)

    explicit_anchor_members: dict[str, set[str]] = defaultdict(set)
    version_conflict_specs: list[dict[str, Any]] = []
    for link in sorted((copy.deepcopy(dict(item)) for item in version_links), key=canonical_json):
        from_ref = str(link.get("from_observation_ref", ""))
        to_ref = str(link.get("to_observation_ref", ""))
        relation_type = str(link.get("relation_type", ""))
        evidence_ref = str(link.get("evidence_ref", ""))
        work_anchor = str(link.get("work_anchor", ""))
        known_refs = [ref for ref in (from_ref, to_ref) if ref in observation_to_manifestation]
        from_manifestation = observation_to_manifestation.get(from_ref)
        to_manifestation = observation_to_manifestation.get(to_ref)
        expected_roles = VERSION_RELATION_ROLE_PAIRS.get(relation_type)
        actual_roles = None
        if from_manifestation is not None and to_manifestation is not None:
            actual_roles = (
                _role_from_claims(manifestation_interim[from_manifestation]["bibliographic_claims"]),
                _role_from_claims(manifestation_interim[to_manifestation]["bibliographic_claims"]),
            )
        invalid = (
            len(known_refs) != 2
            or from_ref == to_ref
            or relation_type not in VERSION_RELATION_TYPES - {"ARXIV_VERSION_SUCCESSOR"}
            or len(evidence_ref.strip()) < 3
            or len(work_anchor.strip()) < 3
            or expected_roles is None
            or actual_roles != expected_roles
        )
        if invalid:
            if not known_refs:
                raise IdentityResolutionError("VERSION_EVIDENCE_ENDPOINT_MISSING", [from_ref, to_ref])
            version_conflict_specs.append({
                "reason_code": "VERSION_EVIDENCE_CONFLICT",
                "source_observation_refs": known_refs,
                "identifier_claims": [relation_type or "MISSING_RELATION", work_anchor or "MISSING_ANCHOR"],
            })
            continue
        from_manifestation = observation_to_manifestation[from_ref]
        to_manifestation = observation_to_manifestation[to_ref]
        if (
            from_manifestation == to_manifestation
            or manifestation_interim[from_manifestation]["identity_status"] != "VERIFIED"
            or manifestation_interim[to_manifestation]["identity_status"] != "VERIFIED"
        ):
            version_conflict_specs.append({
                "reason_code": "VERSION_EVIDENCE_CONFLICT",
                "source_observation_refs": [from_ref, to_ref],
                "identifier_claims": [relation_type, work_anchor],
            })
            continue
        work_dsu.union(from_manifestation, to_manifestation)
        explicit_anchor_members[work_anchor].update((from_manifestation, to_manifestation))
        edge = {
            "from_manifestation_id": from_manifestation,
            "to_manifestation_id": to_manifestation,
            "relation_type": relation_type,
            "evidence_ref": evidence_ref,
            "work_anchor": work_anchor,
        }
        edge["edge_id"] = _stable_id("version_edge_", edge)
        key = canonical_json(edge)
        if key not in edge_keys:
            edge_keys.add(key)
            version_edges.append(edge)
    for members in explicit_anchor_members.values():
        ordered = sorted(members)
        for member in ordered[1:]:
            work_dsu.union(ordered[0], member)
    conflict_specs = _dedupe_conflict_specs([*conflict_specs, *version_conflict_specs])

    component_by_manifestation: dict[str, list[str]] = {}
    for component in work_dsu.groups():
        for manifestation_id in component:
            component_by_manifestation[manifestation_id] = component

    edges_by_component: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in sorted(version_edges, key=lambda item: item["edge_id"]):
        root = work_dsu.find(edge["from_manifestation_id"])
        if work_dsu.find(edge["to_manifestation_id"]) != root:
            raise IdentityResolutionError("VERSION_EDGE_COMPONENT_MISMATCH")
        edges_by_component[root].append(edge)

    work_clusters: list[dict[str, Any]] = []
    manifestation_to_cluster: dict[str, str] = {}
    cluster_conflicted_refs: set[str] = set()
    for spec in version_conflict_specs:
        cluster_conflicted_refs.update(spec["source_observation_refs"])
    for component in work_dsu.groups():
        root = work_dsu.find(component[0])
        edges = edges_by_component.get(root, [])
        explicit_anchors = sorted({
            edge["work_anchor"] for edge in edges
            if edge["relation_type"] != "ARXIV_VERSION_SUCCESSOR"
        })
        arxiv_anchors = sorted({
            edge["work_anchor"] for edge in edges
            if edge["relation_type"] == "ARXIV_VERSION_SUCCESSOR"
        })
        source_refs = sorted({
            ref
            for manifestation_id in component
            for ref in manifestation_interim[manifestation_id]["source_observation_refs"]
        })
        if len(explicit_anchors) > 1:
            conflict_specs.append({
                "reason_code": "VERSION_EVIDENCE_CONFLICT",
                "source_observation_refs": source_refs,
                "identifier_claims": explicit_anchors,
            })
            stable_anchor = "CONFLICT:" + sha256_json(explicit_anchors)[:24]
            cluster_conflicted_refs.update(source_refs)
        elif explicit_anchors:
            stable_anchor = explicit_anchors[0]
        elif len(arxiv_anchors) == 1:
            stable_anchor = arxiv_anchors[0]
        else:
            stable_anchor = "MANIFESTATION:" + component[0]
        if explicit_anchors:
            cluster_basis = "EXPLICIT_VERSION_EVIDENCE"
        elif arxiv_anchors:
            cluster_basis = "ARXIV_BASE_VERSION_CHAIN"
        else:
            cluster_basis = "SINGLE_MANIFESTATION"
        identity_statuses = {
            manifestation_interim[manifestation_id]["identity_status"]
            for manifestation_id in component
        }
        if "CONFLICT" in identity_statuses or any(ref in cluster_conflicted_refs for ref in source_refs):
            cluster_status = "CONFLICT"
        elif "UNRESOLVED" in identity_statuses:
            cluster_status = "UNRESOLVED"
        else:
            cluster_status = "VERIFIED"
        cluster_id = _stable_id("work_cluster_", stable_anchor)
        cluster = _finalize_content_hash({
            "schema_version": "discovery.service_contracts.work-cluster.0.1",
            "object_type": "WorkCluster",
            "object_id": cluster_id,
            "work_cluster_id": cluster_id,
            "revision": 1,
            "producer": PRODUCER,
            "single_writer": IDENTITY_WRITER,
            "consumers": ["LITERATURE_DISCOVERY.CANDIDATE_REGISTRY", "NOVELTY-ASSESSMENT/CROSS"],
            "identity_status": cluster_status,
            "cluster_basis": cluster_basis,
            "stable_work_anchor": stable_anchor,
            "manifestation_ids": sorted(component),
            "version_edges": sorted(edges, key=lambda item: item["edge_id"]),
            "source_observation_refs": source_refs,
            "manifestations_preserved": True,
        })
        work_clusters.append(cluster)
        for manifestation_id in component:
            manifestation_to_cluster[manifestation_id] = cluster_id

    work_clusters_by_id = {item["work_cluster_id"]: item for item in work_clusters}
    manifestations: list[dict[str, Any]] = []
    for manifestation_id in manifestation_ids:
        interim = manifestation_interim[manifestation_id]
        cluster_id = manifestation_to_cluster[manifestation_id]
        relevant_edges = sorted(
            edge["edge_id"]
            for edge in work_clusters_by_id[cluster_id]["version_edges"]
            if manifestation_id in (edge["from_manifestation_id"], edge["to_manifestation_id"])
        )
        manifestation = _finalize_content_hash({
            "schema_version": "discovery.service_contracts.manifestation.0.1",
            "object_type": "Manifestation",
            "object_id": manifestation_id,
            "manifestation_id": manifestation_id,
            "revision": 1,
            "producer": PRODUCER,
            "single_writer": IDENTITY_WRITER,
            "consumers": ["LITERATURE_DISCOVERY.CANDIDATE_REGISTRY", "NOVELTY-ASSESSMENT/CROSS"],
            "identity_ref": interim["identity_ref"],
            "identity_status": interim["identity_status"],
            "manifestation_role": _role_from_claims(interim["bibliographic_claims"]),
            "identifier_set": interim["identifier_set"],
            "source_observation_refs": interim["source_observation_refs"],
            "bibliographic_claims": interim["bibliographic_claims"],
            "work_cluster_id": cluster_id,
            "version_edge_refs": relevant_edges,
            "source_observations_preserved": True,
        })
        manifestations.append(manifestation)

    observation_to_cluster = {
        ref: manifestation_to_cluster[manifestation_id]
        for ref, manifestation_id in observation_to_manifestation.items()
    }
    conflict_specs = _dedupe_conflict_specs(conflict_specs)
    identity_conflicts: list[dict[str, Any]] = []
    review_queue: list[dict[str, Any]] = []
    for spec in conflict_specs:
        refs = spec["source_observation_refs"]
        manifestation_refs = sorted({observation_to_manifestation[ref] for ref in refs})
        conflict_id = _stable_id(
            "identity_conflict_",
            [spec["reason_code"], refs, spec["identifier_claims"]],
        )
        review_id = _stable_id("identity_review_", ["CONFLICT", conflict_id])
        conflict = _finalize_content_hash({
            "schema_version": "discovery.service_contracts.identity-conflict.0.1",
            "object_type": "IdentityConflict",
            "object_id": conflict_id,
            "conflict_id": conflict_id,
            "revision": 1,
            "producer": PRODUCER,
            "single_writer": IDENTITY_WRITER,
            "consumers": ["HUMAN_IDENTITY_REVIEW", "LITERATURE_DISCOVERY.CANDIDATE_REGISTRY"],
            "reason_code": spec["reason_code"],
            "identifier_claims": spec["identifier_claims"],
            "source_observation_refs": refs,
            "manifestation_claim_refs": manifestation_refs,
            "resolution_status": "OPEN",
            "review_queue_item_id": review_id,
            "prohibited_actions": PROHIBITED_REVIEW_ACTIONS,
            "auto_resolved": False,
        })
        identity_conflicts.append(conflict)
        review_queue.append(_finalize_content_hash({
            "schema_version": "discovery.service_contracts.identity-review-queue-item.0.1",
            "object_type": "IdentityReviewQueueItem",
            "object_id": review_id,
            "review_item_id": review_id,
            "revision": 1,
            "producer": PRODUCER,
            "single_writer": IDENTITY_WRITER,
            "consumers": ["HUMAN_IDENTITY_REVIEW"],
            "reason_code": spec["reason_code"],
            "status": "OPEN",
            "conflict_ref": conflict_id,
            "source_observation_refs": refs,
            "candidate_work_cluster_refs": sorted({observation_to_cluster[ref] for ref in refs}),
            "required_action": "HUMAN_IDENTITY_REVIEW",
            "prohibited_actions": PROHIBITED_REVIEW_ACTIONS,
            "external_task_created": False,
            "formal_write_performed": False,
        }))

    titles_by_cluster: dict[str, set[str]] = defaultdict(set)
    refs_by_cluster: dict[str, set[str]] = defaultdict(set)
    for claim in claims:
        cluster_id = observation_to_cluster[claim["observation_ref"]]
        titles_by_cluster[cluster_id].add(claim["metadata_signature"]["title"])
        refs_by_cluster[cluster_id].add(claim["observation_ref"])
    cluster_ids = sorted(titles_by_cluster)
    for index, left_id in enumerate(cluster_ids):
        for right_id in cluster_ids[index + 1:]:
            shared_titles = sorted(titles_by_cluster[left_id] & titles_by_cluster[right_id])
            if not shared_titles:
                continue
            refs = sorted(refs_by_cluster[left_id] | refs_by_cluster[right_id])
            review_id = _stable_id(
                "identity_review_",
                ["POSSIBLE_DUPLICATE_TITLE_ONLY", left_id, right_id, shared_titles],
            )
            review_queue.append(_finalize_content_hash({
                "schema_version": "discovery.service_contracts.identity-review-queue-item.0.1",
                "object_type": "IdentityReviewQueueItem",
                "object_id": review_id,
                "review_item_id": review_id,
                "revision": 1,
                "producer": PRODUCER,
                "single_writer": IDENTITY_WRITER,
                "consumers": ["HUMAN_IDENTITY_REVIEW"],
                "reason_code": "POSSIBLE_DUPLICATE_TITLE_ONLY",
                "status": "OPEN",
                "conflict_ref": None,
                "source_observation_refs": refs,
                "candidate_work_cluster_refs": [left_id, right_id],
                "required_action": "HUMAN_IDENTITY_REVIEW",
                "prohibited_actions": PROHIBITED_REVIEW_ACTIONS,
                "external_task_created": False,
                "formal_write_performed": False,
            }))

    review_queue = sorted(
        {item["review_item_id"]: item for item in review_queue}.values(),
        key=lambda item: item["review_item_id"],
    )
    reviews_by_cluster: dict[str, list[str]] = defaultdict(list)
    for item in review_queue:
        for cluster_id in item["candidate_work_cluster_refs"]:
            reviews_by_cluster[cluster_id].append(item["review_item_id"])

    identities_by_id = {item["object_id"]: item for item in bibliographic_identities}
    manifestations_by_id = {item["manifestation_id"]: item for item in manifestations}
    candidate_projections: list[dict[str, Any]] = []
    for cluster in sorted(work_clusters, key=lambda item: item["work_cluster_id"]):
        cluster_id = cluster["work_cluster_id"]
        cluster_manifestations = [manifestations_by_id[item] for item in cluster["manifestation_ids"]]
        observation_refs = sorted(cluster["source_observation_refs"])
        identity_refs = sorted({item["identity_ref"] for item in cluster_manifestations})
        status = cluster["identity_status"]
        configuration_eligible = status == "VERIFIED"
        reasons = [
            "IDENTITY_VERIFIED_FOR_Configuration_ONLY"
            if configuration_eligible
            else ("IDENTITY_CONFLICT" if status == "CONFLICT" else "IDENTITY_UNRESOLVED"),
            "ServiceContracts_NEVER_GRANTS_CANDIDATE_CARD",
        ]
        candidate_id = "CANDIDATE:" + sha256_json([cluster_id, identity_refs])[:24].upper()
        object_id = "CANDIDATE_OBJECT:" + sha256_json(candidate_id)[:24].upper()
        origins = sorted({claims_by_ref[ref]["discovery_origin"] for ref in observation_refs})
        origin = origins[0] if len(origins) == 1 else "SYNTHETIC_A0_REPLAY"
        if origin == "SYNTHETIC_A0_REPLAY":
            origin = "SYNTHETIC_FIXTURE"
        if origin not in {"KEYWORD_SEARCH", "CITATION_CHAIN", "SYNTHETIC_FIXTURE"}:
            origin = "SYNTHETIC_FIXTURE"
        projection = _finalize_content_hash({
            "schema_version": "discovery.service_contracts.candidate-record-identity-projection.0.1",
            "object_type": "CandidateRecord",
            "object_id": object_id,
            "revision": 1,
            "producer": CANDIDATE_WRITER,
            "consumers": ["RESEARCH_RECOMMENDATIONS.SLATE_BUILDER", "NOVELTY-ASSESSMENT/CROSS"],
            "single_writer": CANDIDATE_WRITER,
            "parents": sorted(_observation_alias(ref) for ref in observation_refs),
            "supersedes": None,
            "immutable_fields": [
                "candidate_id", "source_observation_refs", "identity_evidence_refs",
                "bibliographic_identity_refs", "work_cluster_id", "manifestation_ids"
            ],
            "candidate_id": candidate_id,
            "source_observation_refs": sorted(_observation_alias(ref) for ref in observation_refs),
            "identity_evidence_refs": observation_refs,
            "bibliographic_identity_refs": identity_refs,
            "work_cluster_id": cluster_id,
            "manifestation_ids": sorted(item["manifestation_id"] for item in cluster_manifestations),
            "review_queue_ref": sorted(reviews_by_cluster.get(cluster_id, []))[0] if reviews_by_cluster.get(cluster_id) else None,
            "discovery_origin": origin,
            "identity_status": status,
            "access_status": "METADATA_ONLY",
            "decision_status": "PENDING",
            "candidate_card_status": "NOT_REQUESTED",
            "promotion_status": "NOT_ELIGIBLE",
            "configuration_input_eligible": configuration_eligible,
            "candidate_card_eligible": False,
            "eligibility_reason_codes": sorted(reasons),
            "is_synthetic": False,
        })
        candidate_projections.append(projection)

    hint_decisions: list[dict[str, Any]] = []
    for hint in sorted((copy.deepcopy(dict(item)) for item in hints), key=canonical_json):
        hint_id = str(hint.get("hint_id", ""))
        origin = str(hint.get("discovery_origin", ""))
        if not hint_id or origin not in {"AI_HINT", "TITLE_AUTHOR_YEAR_HINT"}:
            raise IdentityResolutionError("HINT_SCHEMA_REJECTED", [hint_id])
        hint_decisions.append({
            "hint_id": hint_id,
            "discovery_origin": origin,
            "identity_status": "HINT_UNVERIFIED",
            "candidate_record_created": False,
            "configuration_input_eligible": False,
            "candidate_card_eligible": False,
            "reason_code": "EXACT_SOURCE_OBSERVATION_REQUIRED",
        })

    result: dict[str, Any] = {
        "schema_version": "discovery.service_contracts.identity-resolution-batch.0.1",
        "bibliographic_identities": sorted(bibliographic_identities, key=lambda item: item["object_id"]),
        "manifestations": sorted(manifestations, key=lambda item: item["manifestation_id"]),
        "work_clusters": sorted(work_clusters, key=lambda item: item["work_cluster_id"]),
        "identity_conflicts": sorted(identity_conflicts, key=lambda item: item["conflict_id"]),
        "review_queue": review_queue,
        "candidate_identity_projections": sorted(candidate_projections, key=lambda item: item["candidate_id"]),
        "hint_decisions": hint_decisions,
        "observation_to_manifestation": dict(sorted(observation_to_manifestation.items())),
        "observation_to_work_cluster": dict(sorted(observation_to_cluster.items())),
        "statistics": {
            "observations_input": len(copied_observations),
            "observations_preserved": len(observation_to_manifestation),
            "bibliographic_identities": len(bibliographic_identities),
            "manifestations": len(manifestations),
            "work_clusters": len(work_clusters),
            "identity_conflicts": len(identity_conflicts),
            "review_queue_items": len(review_queue),
            "hint_decisions": len(hint_decisions),
            "exact_dedupe_reduction": len(copied_observations) - len(manifestations),
            "network_calls": 0,
            "external_api_calls": 0,
            "model_calls": 0,
            "tokens": 0,
            "cost_cny": 0,
            "fulltext_materializations": 0,
            "artifact_registry_reads": 0,
            "artifact_registry_writes": 0,
            "formal_writes": 0,
            "candidate_cards_created": 0,
        },
    }
    result["replay_fingerprint"] = sha256_json(result).upper()
    return result


def initialization_candidate_base_projection(projection: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact Initialization base fields for compatibility validation."""

    fields = {
        "object_type", "object_id", "revision", "content_hash", "producer",
        "consumers", "single_writer", "parents", "supersedes", "immutable_fields",
        "candidate_id", "source_observation_refs", "discovery_origin",
        "identity_status", "access_status", "decision_status",
        "candidate_card_status", "promotion_status", "is_synthetic",
    }
    return {key: copy.deepcopy(projection[key]) for key in sorted(fields)}


__all__ = [
    "IdentityResolutionError",
    "normalize_identifier",
    "normalize_doi_identifier",
    "normalize_arxiv_identifier",
    "normalize_pmid_identifier",
    "resolve_identity_batch",
    "initialization_candidate_base_projection",
    "verify_source_observation",
]
