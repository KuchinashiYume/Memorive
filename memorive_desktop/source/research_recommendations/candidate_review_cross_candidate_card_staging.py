"""Pure-offline CANDIDATE-REVIEW Candidate Card staging adapter.

This run-local construction module deliberately exposes only the deterministic
DocumentIngest-DocumentChunk, EVIDENCE_EXTRACTION Card writer, and deterministic EVIDENCE_REVIEW path.  It has no production
activation, promotion, Analysis, model, OCR, embedding, or network capability.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from document_processing import config as document_processing_config
from document_processing.document_ingest_ingest import ingest
from document_processing.document_convert_convert import convert
from document_processing.document_clean_clean import clean
from document_processing.document_chunk_chunk import chunk
from document_processing.naming import make_paper_id
from evidence_extraction.card import CONTENT_FIELDS, build_card, write_card
from evidence_review.atomic_review import build_atomic_review_view, deterministic_precheck
from evidence_review.mechanical import mechanical_check


ADAPTER_VERSION = "candidate_review-candidate-staging-a0-v1"
RUN_ID = "CANDIDATE_REVIEW_CROSS_20260811_165122_candidate001"
PRODUCTION_FILE_ROOT = Path(r"G:\Memorive-运维\候选卡片空间")
PRODUCTION_INDEX_ROOT = Path(r"G:\Memorive-ops\vector-store\discovery-candidate-card-space-v1")
PRODUCTION_NAMESPACE = "memorive_discovery_candidate_cards_v1"
CANDIDATE_READER = "CANDIDATE_REVIEW.CANDIDATE_CARD_BUNDLE_WRITER"
PROMOTION_READER = "KNOWLEDGE_PROMOTION.EXACT_PROMOTION_WRITER"

FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "analysis",
        "analysis_result",
        "analysisresult",
        "quality_synthesis",
        "cross_paper_synthesis",
        "theory_synthesis",
        "authority_weight",
        "formal_weight",
        "knowledge_feedback",
        "knowledge_return",
    }
)
FORBIDDEN_PAYLOAD_MARKERS = (
    "analysisresult",
    "quality_synthesis",
    "cross_paper_synthesis",
    "theory_synthesis",
    "authority_weight",
    "formal_weight",
)

UPSTREAM_FACT_KEYS = {
    "data_contracts_source", "service_contracts_identity", "configuration_relation_novelty",
    "intake_selection", "verification_exposure_feedback",
}


class GuardDenied(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest().upper()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GuardDenied("FIXTURE_INVALID", f"JSON root must be an object: {path}")
    return value


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        raise GuardDenied("ATOMIC_TEMP_EXISTS", f"temporary file already exists: {temporary}")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")


def _has_reparse_attribute(path: Path) -> bool:
    try:
        result = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = getattr(result, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def safe_relative_path(
    root: Path,
    relative: str | Path,
    *,
    simulated_reparse: bool = False,
) -> Path:
    root = Path(root)
    candidate_relative = Path(relative)
    raw = str(relative)
    if candidate_relative.is_absolute() or candidate_relative.drive or re.match(r"^[A-Za-z]:", raw):
        raise GuardDenied("ABSOLUTE_PATH_DENIED", f"absolute path is not allowed: {relative}")
    if ".." in candidate_relative.parts:
        raise GuardDenied("PATH_ESCAPE_DENIED", f"parent traversal is not allowed: {relative}")
    if simulated_reparse:
        raise GuardDenied("REPARSE_POINT_DENIED", f"junction/reparse component denied: {relative}")
    root_resolved = root.resolve(strict=False)
    target = root.joinpath(candidate_relative)
    target_resolved = target.resolve(strict=False)
    try:
        target_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise GuardDenied("PATH_ESCAPE_DENIED", f"path resolves outside root: {relative}") from exc
    probe = root
    if probe.exists() and _has_reparse_attribute(probe):
        raise GuardDenied("REPARSE_POINT_DENIED", f"root is a reparse point: {probe}")
    for part in candidate_relative.parts:
        probe = probe / part
        if probe.exists() and _has_reparse_attribute(probe):
            raise GuardDenied("REPARSE_POINT_DENIED", f"reparse component denied: {probe}")
    return target


def _parse_time(value: Any, *, code: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise GuardDenied(code, "timestamp must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise GuardDenied(code, f"invalid ISO-8601 timestamp: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GuardDenied(code, f"timezone-aware timestamp required: {value}")
    return parsed


def _read_card(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    fences = list(re.finditer(r"(?m)^---[ \t]*\r?$", raw))
    if len(fences) < 2 or fences[0].start() != 0:
        raise GuardDenied("CARD_PARSE_FAILED", f"invalid Card frontmatter: {path}")
    value = yaml.safe_load(raw[fences[0].end() : fences[1].start()])
    if not isinstance(value, dict):
        raise GuardDenied("CARD_PARSE_FAILED", f"Card frontmatter is not a mapping: {path}")
    return value


def _load_chunks(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise GuardDenied("CHUNK_INVALID", f"chunk line {line_number} is not an object")
        rows.append(value)
    return rows


def _copy_atomic(source: Path, target: Path) -> None:
    atomic_write_bytes(target, source.read_bytes())


def _problem_to_dict(problem: Any) -> dict[str, Any]:
    try:
        return asdict(problem)
    except TypeError:
        return dict(vars(problem))


@dataclass
class ExecutionCounters:
    materializations: int = 0
    card_writes: int = 0
    bundle_writes: int = 0
    candidate_index_writes: int = 0
    candidate_registry_writes: int = 0
    external_source_calls: int = 0
    network_calls: int = 0
    model_api_calls: int = 0
    ocr_calls: int = 0
    embedding_calls: int = 0
    knowledge_admission_calls: int = 0
    artifact_registry_calls: int = 0
    analysis_calls: int = 0
    promotion_calls: int = 0
    verification_feedback_writes: int = 0
    tokens: int = 0
    actual_cost_cny: int = 0
    credential_reads: int = 0
    production_reads: int = 0
    production_writes: int = 0
    production_mutations: int = 0


class CandidateCardStager:
    """Single-writer, run-local CandidateCardSpace shadow adapter."""

    def __init__(self, *, space_root: Path, fixture_root: Path, repo_root: Path):
        self.space_root = Path(space_root)
        self.fixture_root = Path(fixture_root)
        self.repo_root = Path(repo_root)
        self.counters = ExecutionCounters()
        self._index_path = self.space_root / "index" / "candidate_index.json"
        self._registry_path = self.space_root / "receipts" / "candidate_paper_registry.json"

    def _contract_schema(self, token: str, revision: str) -> dict[str, Any]:
        matches = list(
            (self.repo_root / "项目文档" / "Discovery_文献发现" / "10_模块契约").glob(
                f"*{token}*{revision}.schema.json"
            )
        )
        if len(matches) != 1:
            raise GuardDenied("AUTHORITY_SCHEMA_MISSING_OR_AMBIGUOUS", f"{token}@{revision}:{len(matches)}")
        return read_json(matches[0])

    def _validate_contract(self, token: str, revision: str, value: dict[str, Any]) -> None:
        schema = self._contract_schema(token, revision)
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
            key=lambda item: list(item.path),
        )
        if errors:
            raise GuardDenied(
                "AUTHORITY_SCHEMA_INVALID",
                f"{token}@{revision}: " + "; ".join(error.message for error in errors[:3]),
            )

    @staticmethod
    def _validate_upstream_facts(request: dict[str, Any]) -> dict[str, Any]:
        facts = request.get("upstream_facts")
        if not isinstance(facts, dict) or set(facts) != UPSTREAM_FACT_KEYS:
            raise GuardDenied("UPSTREAM_FACTS_INCOMPLETE", "exact DataContracts-Verification upstream facts required")
        service_contracts = facts.get("service_contracts_identity") or {}
        intake = facts.get("intake_selection") or {}
        verification = facts.get("verification_exposure_feedback") or {}
        if (
            service_contracts.get("work_cluster_id") != request.get("work_cluster_id")
            or service_contracts.get("manifestation_id") != request.get("manifestation_id")
        ):
            raise GuardDenied("UPSTREAM_FACTS_BINDING_MISMATCH", "ServiceContracts identity differs from staging request")
        if intake.get("primary_direction_id") != verification.get("direction_id"):
            raise GuardDenied("UPSTREAM_FACTS_BINDING_MISMATCH", "Intake/Verification direction differs")
        CandidateCardStager.guard_payload(facts)
        return copy.deepcopy(facts)

    @staticmethod
    def guard_operation(operation: str) -> None:
        normalized = operation.strip().casefold()
        if "research_analysis" in normalized or "analysisresult" in normalized or normalized.startswith("analysis"):
            raise GuardDenied("ANALYSIS_OPERATION_DENIED", operation)
        if "knowledge_admission" in normalized and ("active" in normalized or "transition" in normalized or "verdict" in normalized):
            raise GuardDenied("KNOWLEDGE_ADMISSION_ACTIVE_DENIED", operation)
        if any(marker in normalized for marker in ("document_embed", "embed", "ocr", "network", "external_source", "http")):
            raise GuardDenied("EXTERNAL_OR_EMBEDDING_OPERATION_DENIED", operation)
        if "evidence_extraction.distill" in normalized or "evidence_review_model" in normalized or "model" in normalized or "gateway" in normalized:
            raise GuardDenied("MODEL_OPERATION_DENIED", operation)
        if "promotion" in normalized or "analysis" in normalized:
            raise GuardDenied("PROMOTION_DENIED", operation)
        if "artifact_registry" in normalized or "formal_writer" in normalized:
            raise GuardDenied("FORMAL_WRITER_DENIED", operation)
        if "run_pipeline" in normalized:
            raise GuardDenied("DOCUMENT_PROCESSING_RUN_PIPELINE_DENIED", operation)

    @staticmethod
    def guard_payload(value: Any, path: str = "$") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized_key = str(key).casefold()
                if normalized_key in FORBIDDEN_PAYLOAD_KEYS or any(marker in normalized_key for marker in FORBIDDEN_PAYLOAD_MARKERS):
                    raise GuardDenied("ANALYSIS_FIELD_DENIED", f"forbidden key at {path}.{key}")
                CandidateCardStager.guard_payload(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                CandidateCardStager.guard_payload(child, f"{path}[{index}]")
        elif isinstance(value, str):
            normalized_value = value.casefold().replace(" ", "")
            if "analysisresult" in normalized_value:
                raise GuardDenied("ANALYSIS_FIELD_DENIED", f"forbidden Analysis type marker at {path}")

    @staticmethod
    def project_decision(card_axes: dict[str, str], decision_status: str) -> dict[str, str]:
        if decision_status not in {"SNOOZED", "REJECTED"}:
            raise GuardDenied("DECISION_STATUS_DENIED", decision_status)
        projected = dict(card_axes)
        projected["decision_status"] = decision_status
        return projected

    @staticmethod
    def guard_feedback_route(route: str) -> None:
        if "NEEDS_REPAIR" in route.upper() and any(token in route.upper() for token in ("INTEREST", "NEGATIVE", "Verification", "PROFILE", "POLICY")):
            raise GuardDenied("Verification_FEEDBACK_ROUTE_DENIED", route)

    def read_index(self, reader: str, *, namespace: str = "candidate-local-json") -> dict[str, Any]:
        if reader not in {CANDIDATE_READER, PROMOTION_READER}:
            raise GuardDenied("FORMAL_READER_DENIED", reader)
        if namespace in {"memorive_main", "memorive_sandbox", PRODUCTION_NAMESPACE}:
            raise GuardDenied("FORMAL_READER_DENIED", namespace)
        return self._load_index()

    def _load_index(self) -> dict[str, Any]:
        if not self._index_path.exists():
            return {"schema_version": "0.1", "records": {}}
        return read_json(self._index_path)

    def _load_registry(self) -> dict[str, Any]:
        if not self._registry_path.exists():
            return {"schema_version": "0.1", "bindings": {}}
        return read_json(self._registry_path)

    def _load_fixture(self, descriptor: dict[str, Any] | None, *, missing_code: str) -> tuple[Path, dict[str, Any]]:
        if not isinstance(descriptor, dict):
            raise GuardDenied(missing_code, "fixture descriptor missing")
        fixture_path = safe_relative_path(self.fixture_root, descriptor.get("relative_path", ""))
        if not fixture_path.is_file():
            raise GuardDenied("FIXTURE_MISSING", str(fixture_path))
        observed = file_sha256(fixture_path)
        if observed != descriptor.get("sha256"):
            raise GuardDenied("FIXTURE_HASH_MISMATCH", f"{fixture_path.name}: {observed}")
        return fixture_path, read_json(fixture_path)

    def _load_source_fixture(self, descriptor: dict[str, Any]) -> Path:
        if not isinstance(descriptor, dict):
            raise GuardDenied("SOURCE_FIXTURE_MISSING", "source descriptor missing")
        source = safe_relative_path(self.fixture_root, descriptor.get("relative_path", ""))
        if not source.is_file():
            raise GuardDenied("SOURCE_FIXTURE_MISSING", str(source))
        observed = file_sha256(source)
        if observed != descriptor.get("sha256"):
            raise GuardDenied("FIXTURE_HASH_MISMATCH", f"source: {observed}")
        return source

    @staticmethod
    def _apply_override(value: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
        if override is None:
            return value
        result = copy.deepcopy(value)
        for key, child in override.items():
            result[key] = child
        return result

    @staticmethod
    def _registry_binding(request: dict[str, Any]) -> dict[str, Any]:
        identity = request["paper_identity"]
        return {
            "candidate_id": request["candidate_id"],
            "folder_key": identity["folder_key"],
            "manifestation_id": request["manifestation_id"],
            "paper_id": identity["paper_id"],
            "work_cluster_id": request["work_cluster_id"],
        }

    def _expected_registry_after(
        self,
        request: dict[str, Any],
        registry_before: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        before = copy.deepcopy(registry_before if registry_before is not None else self._load_registry())
        if before.get("schema_version") != "0.1" or not isinstance(before.get("bindings"), dict):
            raise GuardDenied("PAPER_ID_REGISTRY_INVALID", "candidate registry shape is invalid")
        identity = request["paper_identity"]
        before["bindings"][identity["paper_id"]] = self._registry_binding(request)
        return before

    def preflight(
        self,
        request: dict[str, Any],
        *,
        approval_override: dict[str, Any] | None = None,
        rights_override: dict[str, Any] | None = None,
        reservation_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if request.get("identity_status") != "VERIFIED":
            raise GuardDenied("IDENTITY_NOT_VERIFIED", str(request.get("identity_status")))
        source_path = self._load_source_fixture(request.get("source_fixture"))
        approval_path, approval = self._load_fixture(request.get("first_approval_fixture"), missing_code="FIRST_APPROVAL_MISSING")
        approval = self._apply_override(approval, approval_override)
        rights_path, rights = self._load_fixture(request.get("rights_fixture"), missing_code="RIGHTS_RECEIPT_MISSING")
        rights = self._apply_override(rights, rights_override)
        reservation_path, reservation = self._load_fixture(request.get("reservation_fixture"), missing_code="PAPER_ID_RESERVATION_MISSING")
        reservation = self._apply_override(reservation, reservation_override)
        draft_path, draft = self._load_fixture(request.get("card_draft_fixture"), missing_code="CARD_DRAFT_MISSING")

        binding_fields = ("candidate_id", "work_cluster_id", "manifestation_id", "source_revision")
        if any(approval.get(field) != request.get(field) for field in binding_fields):
            raise GuardDenied("FIRST_APPROVAL_BINDING_MISMATCH", "approval candidate/work/manifestation/source binding differs")
        if approval.get("candidate_revision") != request.get("candidate_revision"):
            raise GuardDenied("FIRST_APPROVAL_BINDING_MISMATCH", "candidate revision differs")
        if approval.get("approved_source_sha256") != request["source_fixture"]["sha256"]:
            raise GuardDenied("FIRST_APPROVAL_BINDING_MISMATCH", "source hash differs")
        if approval.get("fixture_authority") != "MEMORIVE_SELF_AUTHORED_PUBLIC_SAFE_SYNTHETIC":
            raise GuardDenied("FIRST_APPROVAL_SCOPE_INVALID", str(approval.get("fixture_authority")))
        if approval.get("scope") != "PURE_OFFLINE_A0_SYNTHETIC_CARD_STAGING_ONLY":
            raise GuardDenied("FIRST_APPROVAL_SCOPE_INVALID", str(approval.get("scope")))
        if approval.get("decision_status") != "APPROVED_FOR_CARD_STAGING":
            raise GuardDenied("FIRST_APPROVAL_NOT_APPROVED", str(approval.get("decision_status")))
        if approval.get("revoked") is not False:
            raise GuardDenied("FIRST_APPROVAL_REVOKED", approval_path.name)
        upstream_facts: dict[str, Any] | None = None
        if approval.get("schema_version") == "0.2":
            self._validate_contract("FirstApprovalReceipt", "r0.2", approval)
            core = {key: value for key, value in approval.items() if key != "content_hash"}
            if approval.get("content_hash") != canonical_sha256(core):
                raise GuardDenied("FIRST_APPROVAL_CONTENT_HASH_MISMATCH", approval_path.name)
            upstream_facts = self._validate_upstream_facts(request)
            if approval.get("approved_input_hash") != canonical_sha256(upstream_facts):
                raise GuardDenied("FIRST_APPROVAL_BINDING_MISMATCH", "approved DataContracts-Verification facts hash differs")
            direction = upstream_facts["intake_selection"]["primary_direction_id"]
            if approval.get("direction_id") != direction:
                raise GuardDenied("FIRST_APPROVAL_BINDING_MISMATCH", "approved direction differs")
            if (
                approval.get("decided_by") != "TEST_ONLY_SYNTHETIC_HUMAN_A"
                or approval.get("authority_class") != "TEST_ONLY_NON_AUTHORITATIVE"
                or approval.get("revoked_at") is not None
                or approval.get("revocation_ref") is not None
            ):
                raise GuardDenied("FIRST_APPROVAL_SCOPE_INVALID", "r0.2 synthetic authority fields invalid")
        elif approval.get("schema_version") != "0.1":
            raise GuardDenied("FIRST_APPROVAL_SCOPE_INVALID", str(approval.get("schema_version")))
        event_time = _parse_time(request.get("event_time"), code="REQUEST_TIME_INVALID")
        approval_issued_at = _parse_time(approval.get("issued_at"), code="FIRST_APPROVAL_TIME_INVALID")
        approval_expires_at = _parse_time(approval.get("expires_at"), code="FIRST_APPROVAL_TIME_INVALID")
        if approval_expires_at < event_time:
            raise GuardDenied("FIRST_APPROVAL_EXPIRED", approval["expires_at"])
        if approval_issued_at > event_time or approval_expires_at < approval_issued_at:
            raise GuardDenied("FIRST_APPROVAL_TIME_INVALID", "approval validity window does not contain its issuance")

        if any(rights.get(field) != request.get(field) for field in binding_fields if field != "candidate_revision"):
            raise GuardDenied("RIGHTS_BINDING_MISMATCH", "rights candidate/work/manifestation/source binding differs")
        if rights.get("access_status") != "RIGHTS_VERIFIED":
            raise GuardDenied("RIGHTS_NOT_VERIFIED", str(rights.get("access_status")))
        if (
            rights.get("fixture_authority") != "MEMORIVE_SELF_AUTHORED_PUBLIC_SAFE_SYNTHETIC"
            or rights.get("rights_basis") != "MEMORIVE_SELF_AUTHORED_PUBLIC_SAFE_SYNTHETIC"
        ):
            raise GuardDenied("RIGHTS_BASIS_INVALID", str(rights.get("rights_basis")))
        if rights.get("source_sha256") != request["source_fixture"]["sha256"]:
            raise GuardDenied("RIGHTS_BINDING_MISMATCH", "rights source hash differs")
        if rights.get("external_retrieval_required") is not False:
            raise GuardDenied("RIGHTS_NOT_VERIFIED", "external retrieval would be required")
        rights_verified_at = _parse_time(rights.get("verified_at"), code="RIGHTS_TIME_INVALID")
        if rights_verified_at > event_time:
            raise GuardDenied("RIGHTS_TIME_INVALID", "rights receipt is later than the staging event")

        if reservation.get("registry_conflict") is True:
            raise GuardDenied("PAPER_ID_COLLISION", "injected conflict")
        identity = request.get("paper_identity")
        if not isinstance(identity, dict):
            raise GuardDenied("PAPER_ID_RESERVATION_INVALID", "paper_identity missing")
        surname = identity.get("surname")
        year = identity.get("year")
        explicit_suffix = identity.get("explicit_suffix")
        folder_key = identity.get("folder_key")
        if not isinstance(surname, str) or re.fullmatch(r"[A-Za-z][A-Za-z'-]*", surname) is None:
            raise GuardDenied("PAPER_ID_RESERVATION_INVALID", "surname must use the approved DOCUMENT_PROCESSING-compatible form")
        if isinstance(year, bool) or not isinstance(year, int) or year < 1000 or year > 2999:
            raise GuardDenied("PAPER_ID_RESERVATION_INVALID", "year must be an integer in 1000..2999")
        if explicit_suffix not in {"", "a", "b"}:
            raise GuardDenied("PAPER_ID_SUFFIX_NOT_EXPLICIT", str(explicit_suffix))
        if not isinstance(folder_key, str) or not folder_key:
            raise GuardDenied("PAPER_ID_RESERVATION_INVALID", "folder_key missing")
        expected_paper_id = make_paper_id(identity.get("surname", ""), identity.get("year", ""), identity.get("explicit_suffix", ""))
        if identity.get("paper_id") != expected_paper_id or reservation.get("paper_id") != expected_paper_id:
            raise GuardDenied("PAPER_ID_RESERVATION_INVALID", f"expected {expected_paper_id}")
        reservation_fields = ("candidate_id", "work_cluster_id", "manifestation_id")
        if any(reservation.get(field) != request.get(field) for field in reservation_fields):
            raise GuardDenied("PAPER_ID_RESERVATION_INVALID", "reservation binding differs")
        if any(
            reservation.get(field) != identity.get(field)
            for field in ("surname", "year", "explicit_suffix", "folder_key")
        ):
            raise GuardDenied("PAPER_ID_RESERVATION_INVALID", "reservation paper identity binding differs")
        if (
            reservation.get("schema_version") != "0.2"
            or reservation.get("fixture_authority") != "MEMORIVE_SELF_AUTHORED_PUBLIC_SAFE_SYNTHETIC"
            or reservation.get("reservation_status") != "RESERVED_CANDIDATE"
        ):
            raise GuardDenied("PAPER_ID_RESERVATION_INVALID", str(reservation.get("reservation_status")))
        if reservation.get("first_approval_receipt_hash") != file_sha256(approval_path):
            raise GuardDenied("PAPER_ID_RESERVATION_INVALID", "approval receipt hash differs")
        if reservation.get("rights_receipt_hash") != file_sha256(rights_path):
            raise GuardDenied("PAPER_ID_RESERVATION_INVALID", "rights receipt hash differs")
        registry_before = self._load_registry()
        existing = registry_before.get("bindings", {}).get(expected_paper_id)
        if existing is not None:
            desired = self._registry_binding(request)
            if existing != desired:
                raise GuardDenied("PAPER_ID_COLLISION", "paper_id is bound to a different candidate/folder")
        elif canonical_sha256(registry_before) != reservation.get("candidate_registry_before_hash"):
            raise GuardDenied("PAPER_ID_REGISTRY_BEFORE_HASH_MISMATCH", canonical_sha256(registry_before))
        registry_after = self._expected_registry_after(request, registry_before)
        if canonical_sha256(registry_after) != reservation.get("candidate_registry_after_hash"):
            raise GuardDenied("PAPER_ID_REGISTRY_AFTER_HASH_MISMATCH", canonical_sha256(registry_after))

        self.guard_payload(draft)
        return {
            "source_path": source_path,
            "approval_path": approval_path,
            "approval": approval,
            "rights_path": rights_path,
            "rights": rights,
            "reservation_path": reservation_path,
            "reservation": reservation,
            "draft_path": draft_path,
            "draft": draft,
            "registry_after": registry_after,
            "upstream_facts": upstream_facts,
        }

    def _input_fingerprint(self, request: dict[str, Any]) -> str:
        descriptors = [
            request.get("source_fixture"),
            request.get("first_approval_fixture"),
            request.get("rights_fixture"),
            request.get("reservation_fixture"),
            request.get("card_draft_fixture"),
        ]
        return canonical_sha256({"adapter_version": ADAPTER_VERSION, "request": request, "descriptors": descriptors})

    def _ensure_layout(self) -> None:
        for relative in ("source", "document_processing", "cards", "index", "receipts"):
            safe_relative_path(self.space_root, relative).mkdir(parents=True, exist_ok=True)

    def _write_registry(self, value: dict[str, Any]) -> None:
        atomic_write_json(self._registry_path, value)
        self.counters.candidate_registry_writes += 1

    def _write_index(self, value: dict[str, Any]) -> None:
        atomic_write_json(self._index_path, value)
        self.counters.candidate_index_writes += 1

    def stage(self, request: dict[str, Any]) -> dict[str, Any]:
        original_document_processing_sandbox_root = document_processing_config.SANDBOX_ROOT
        document_processing_config.SANDBOX_ROOT = self.space_root / "document_processing"
        try:
            return self._stage_with_isolated_document_processing(request)
        finally:
            document_processing_config.SANDBOX_ROOT = original_document_processing_sandbox_root

    def _stage_with_isolated_document_processing(self, request: dict[str, Any]) -> dict[str, Any]:
        fingerprint = self._input_fingerprint(request)
        index = self._load_index()
        existing_record = index.get("records", {}).get(request["candidate_id"])
        if isinstance(existing_record, dict) and existing_record.get("input_fingerprint") == fingerprint:
            latest = existing_record["revisions"][-1]
            manifest_path = safe_relative_path(self.space_root, latest["manifest_relative_path"])
            verified = self.verify_bundle(manifest_path.parent)
            return {**verified, "idempotent_replay": True, "input_fingerprint": fingerprint}

        preflight = self.preflight(request)
        self._ensure_layout()
        source_target = safe_relative_path(
            self.space_root,
            Path("source") / request["candidate_id"] / request["source_revision"] / preflight["source_path"].name,
        )
        _copy_atomic(preflight["source_path"], source_target)
        self.counters.materializations += 1
        self._write_registry(preflight["registry_after"])
        receipt_target = safe_relative_path(
            self.space_root,
            Path("receipts") / request["candidate_id"] / "paper_identity_reservation_receipt.json",
        )
        _copy_atomic(preflight["reservation_path"], receipt_target)

        raw = ingest(source_target, data_ownership="self", doc_type="literature")
        converted = convert(
            raw,
            paper_id=request["paper_identity"]["paper_id"],
            title=preflight["draft"]["title"],
            target="sandbox",
            engine="passthrough",
        )
        clean_path = clean(converted.rawmd_path, target="sandbox")
        chunks_path = chunk(clean_path, target="sandbox")
        chunks = _load_chunks(chunks_path)
        if len(chunks) != preflight["draft"].get("expected_chunk_count"):
            raise GuardDenied("CHUNK_COUNT_MISMATCH", f"observed {len(chunks)}")

        result = self._write_revision(
            request=request,
            draft=preflight["draft"],
            chunks_path=chunks_path,
            fingerprint=fingerprint,
            approval_sha256=file_sha256(preflight["approval_path"]),
            rights_sha256=file_sha256(preflight["rights_path"]),
            reservation_sha256=file_sha256(preflight["reservation_path"]),
            source_sha256=file_sha256(preflight["source_path"]),
            upstream_facts=preflight["upstream_facts"],
            repair_of=None,
            defect_kind=None,
        )
        return result

    def stage_revision_from_existing(
        self,
        request: dict[str, Any],
        *,
        card_revision: str,
        repair_of: str,
        defect_kind: str | None,
    ) -> dict[str, Any]:
        index = self._load_index()
        record = index.get("records", {}).get(request["candidate_id"])
        if not isinstance(record, dict) or not record.get("revisions"):
            raise GuardDenied("REPAIR_SOURCE_MISSING", request["candidate_id"])
        predecessor = next(
            (
                item
                for item in record["revisions"]
                if isinstance(item, dict) and item.get("bundle_sha256") == repair_of
            ),
            None,
        )
        if predecessor is None:
            raise GuardDenied("REPAIR_SOURCE_MISSING", repair_of)
        source_manifest_path = safe_relative_path(self.space_root, predecessor["manifest_relative_path"])
        source_manifest = read_json(source_manifest_path)
        verified_source = self.verify_bundle(source_manifest_path.parent)
        if verified_source.get("bundle_sha256") != repair_of:
            raise GuardDenied("REPAIR_SOURCE_MISSING", repair_of)
        source_envelope = read_json(
            source_manifest_path.parent / source_manifest["artifacts"]["envelope"]["relative_path"]
        )
        immutable_manifest_fields = ("candidate_id", "candidate_revision", "source_revision", "materialization_revision")
        if any(source_manifest.get(field) != request.get(field) for field in immutable_manifest_fields):
            raise GuardDenied("REPAIR_IDENTITY_DRIFT", "candidate/source/materialization identity changed")
        if source_manifest.get("paper_id") != request.get("paper_identity", {}).get("paper_id"):
            raise GuardDenied("REPAIR_IDENTITY_DRIFT", "paper_id changed")
        if any(
            source_envelope.get(field) != request.get(field)
            for field in ("work_cluster_id", "manifestation_id")
        ):
            raise GuardDenied("REPAIR_IDENTITY_DRIFT", "work or manifestation identity changed")
        source_bundle = source_manifest_path.parent
        chunks_path = source_bundle / source_manifest["artifacts"]["chunks"]["relative_path"]
        _, draft = self._load_fixture(request.get("card_draft_fixture"), missing_code="CARD_DRAFT_MISSING")
        self.guard_payload(draft)
        revision_request = copy.deepcopy(request)
        revision_request["card_revision"] = card_revision
        fingerprint = canonical_sha256(
            {
                "adapter_version": ADAPTER_VERSION,
                "request": revision_request,
                "repair_of": repair_of,
                "defect_kind": defect_kind,
            }
        )
        return self._write_revision(
            request=revision_request,
            draft=draft,
            chunks_path=chunks_path,
            fingerprint=fingerprint,
            approval_sha256=source_manifest["bindings"]["first_approval_receipt_sha256"],
            rights_sha256=source_manifest["bindings"]["rights_receipt_sha256"],
            reservation_sha256=source_manifest["bindings"]["paper_identity_reservation_receipt_sha256"],
            source_sha256=source_manifest["bindings"]["source_sha256"],
            upstream_facts=source_envelope.get("upstream_facts"),
            repair_of=repair_of,
            defect_kind=defect_kind,
        )

    @staticmethod
    def _mutate_card(card: dict[str, Any], defect_kind: str | None) -> None:
        if defect_kind is None:
            return
        if defect_kind == "missing_method":
            card.pop("method", None)
        elif defect_kind == "active_enum":
            card["review_status"] = "active"
        elif defect_kind == "completion_mismatch":
            card["completion_status"] = "complete"
        elif defect_kind == "missing_anchor":
            card["source_anchor"]["by_field"]["method"] = []
        elif defect_kind == "wrong_paper":
            card["source_anchor"]["paper_id"] = "Other2026"
        elif defect_kind == "wrong_chunk":
            card["source_anchor"]["by_field"]["method"][0]["chunk_id"] = "Synthetic2026#c9999"
        elif defect_kind == "missing_page":
            card["source_anchor"]["by_field"]["method"][0]["section_page"] = None
        elif defect_kind == "forced_mechanical_failure":
            return
        else:
            raise GuardDenied("UNKNOWN_DEFECT_KIND", defect_kind)

    def _write_revision(
        self,
        *,
        request: dict[str, Any],
        draft: dict[str, Any],
        chunks_path: Path,
        fingerprint: str,
        approval_sha256: str,
        rights_sha256: str,
        reservation_sha256: str,
        source_sha256: str,
        upstream_facts: dict[str, Any] | None,
        repair_of: str | None,
        defect_kind: str | None,
    ) -> dict[str, Any]:
        card_dir = safe_relative_path(
            self.space_root,
            Path("cards") / request["candidate_id"] / request["card_revision"],
        )
        if card_dir.exists():
            raise GuardDenied("SEALED_REVISION_OVERWRITE_DENIED", str(card_dir))
        card_dir.mkdir(parents=True, exist_ok=False)
        card = build_card(
            paper_id=request["paper_identity"]["paper_id"],
            title=draft["title"],
            data_ownership="self",
            fields=draft["fields"],
            by_field=draft["by_field"],
            distill_model=draft["distill_model"],
            distilled_at=draft["distilled_at"],
            schema_version=draft["card_schema_version"],
            key_data=draft.get("key_data", []),
            aux=None,
        )
        self._mutate_card(card, defect_kind)
        self.guard_payload(card)
        card_path = card_dir / "candidate_card.md"
        write_card(card_path, card, overwrite=False)
        self.counters.card_writes += 1
        chunks_target = card_dir / "[Chunks]candidate_source.jsonl"
        _copy_atomic(Path(chunks_path), chunks_target)

        evaluation = self.evaluate_card(
            card_path,
            chunks_target,
            expected_paper_id=request["paper_identity"]["paper_id"],
            forced_mechanical_failure=defect_kind == "forced_mechanical_failure",
        )
        generation_receipt_sha256 = canonical_sha256(evaluation)
        envelope = {
            "schema_version": "0.2" if upstream_facts is not None else "0.1",
            "candidate_id": request["candidate_id"],
            "work_cluster_id": request["work_cluster_id"],
            "manifestation_id": request["manifestation_id"],
            "candidate_revision": request["candidate_revision"],
            "source_revision": request["source_revision"],
            "materialization_revision": request["materialization_revision"],
            "card_revision": request["card_revision"],
            "processing_status": evaluation["processing_status"],
            "decision_status": "APPROVED_FOR_CARD_STAGING",
            "review_status": evaluation["review_status"],
            "completion_status": evaluation["completion_status"],
            "promotion_status": "NOT_ELIGIBLE",
            "semantic_verification": "NOT_PERFORMED",
            "deterministic_checks": evaluation,
            "repair_of_bundle_sha256": repair_of,
            "provenance": {
                "fixture_authority": "MEMORIVE_SELF_AUTHORED_PUBLIC_SAFE_SYNTHETIC",
                "source_sha256": source_sha256,
                "recommendation_context_location": "ENVELOPE_ONLY_NOT_CARD_CORE",
            },
        }
        if upstream_facts is not None:
            envelope["upstream_facts"] = copy.deepcopy(upstream_facts)
            envelope["receipt_bindings"] = {
                "first_approval_receipt_sha256": approval_sha256,
                "rights_receipt_sha256": rights_sha256,
                "paper_identity_reservation_receipt_sha256": reservation_sha256,
                "generation_receipt_sha256": generation_receipt_sha256,
            }
            self._validate_contract("CandidateReviewEnvelope", "r0.2", envelope)
        envelope_path = card_dir / "candidate_review_envelope.json"
        atomic_write_json(envelope_path, envelope)
        artifact_records = {
            "card": {"relative_path": card_path.name, "sha256": file_sha256(card_path)},
            "chunks": {"relative_path": chunks_target.name, "sha256": file_sha256(chunks_target)},
            "envelope": {"relative_path": envelope_path.name, "sha256": file_sha256(envelope_path)},
        }
        manifest_core = {
            "schema_version": "0.1",
            "adapter_version": ADAPTER_VERSION,
            "run_id": RUN_ID,
            "candidate_id": request["candidate_id"],
            "paper_id": request["paper_identity"]["paper_id"],
            "candidate_revision": request["candidate_revision"],
            "source_revision": request["source_revision"],
            "materialization_revision": request["materialization_revision"],
            "card_revision": request["card_revision"],
            "input_fingerprint": fingerprint,
            "processing_status": evaluation["processing_status"],
            "review_status": evaluation["review_status"],
            "completion_status": evaluation["completion_status"],
            "promotion_status": "NOT_ELIGIBLE",
            "semantic_verification": "NOT_PERFORMED",
            "repair_of_bundle_sha256": repair_of,
            "sealed_at": request["event_time"],
            "bindings": {
                "source_sha256": source_sha256,
                "first_approval_receipt_sha256": approval_sha256,
                "rights_receipt_sha256": rights_sha256,
                "paper_identity_reservation_receipt_sha256": reservation_sha256,
            },
            "artifacts": artifact_records,
        }
        bundle_sha256 = canonical_sha256(manifest_core)
        manifest = {**manifest_core, "bundle_sha256": bundle_sha256}
        manifest_path = card_dir / "bundle_manifest.json"
        atomic_write_json(manifest_path, manifest)
        atomic_write_json(card_dir / "SEALED.json", {"bundle_sha256": bundle_sha256, "overwrite": "DENIED"})
        self.counters.bundle_writes += 1

        index = self._load_index()
        records = index.setdefault("records", {})
        record = records.setdefault(
            request["candidate_id"],
            {
                "candidate_id": request["candidate_id"],
                "paper_id": request["paper_identity"]["paper_id"],
                "input_fingerprint": fingerprint,
                "revisions": [],
            },
        )
        if record.get("paper_id") != request["paper_identity"]["paper_id"]:
            raise GuardDenied("PAPER_ID_COLLISION", "candidate index paper_id changed")
        relative_manifest = str(manifest_path.relative_to(self.space_root)).replace("\\", "/")
        if any(item.get("manifest_relative_path") == relative_manifest for item in record["revisions"]):
            raise GuardDenied("DUPLICATE_INDEX_ENTRY", relative_manifest)
        record["revisions"].append(
            {
                "card_revision": request["card_revision"],
                "processing_status": evaluation["processing_status"],
                "bundle_sha256": bundle_sha256,
                "manifest_relative_path": relative_manifest,
                "repair_of_bundle_sha256": repair_of,
            }
        )
        if defect_kind is None and repair_of is None:
            record["input_fingerprint"] = fingerprint
        self._write_index(index)
        return {
            "candidate_id": request["candidate_id"],
            "paper_id": request["paper_identity"]["paper_id"],
            "card_revision": request["card_revision"],
            "processing_status": evaluation["processing_status"],
            "review_status": evaluation["review_status"],
            "completion_status": evaluation["completion_status"],
            "promotion_status": "NOT_ELIGIBLE",
            "semantic_verification": "NOT_PERFORMED",
            "bundle_sha256": bundle_sha256,
            "manifest_path": str(manifest_path),
            "input_fingerprint": fingerprint,
            "idempotent_replay": False,
        }

    def evaluate_card(
        self,
        card_path: Path,
        chunks_path: Path,
        *,
        expected_paper_id: str,
        forced_mechanical_failure: bool = False,
    ) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        card = _read_card(card_path)
        chunks = _load_chunks(chunks_path)
        chunk_map = {item.get("chunk_id"): item for item in chunks}
        self.guard_payload(card)
        for field in CONTENT_FIELDS:
            if field not in card or card[field] is None or card[field] == []:
                issues.append({"code": "CARD_REQUIRED_FIELD_MISSING", "location": field})
        if card.get("review_status") != "pending":
            issues.append({"code": "CARD_REVIEW_STATUS_INVALID", "observed": card.get("review_status")})
        if card.get("completion_status") != "pending_review":
            issues.append({"code": "CARD_COMPLETION_STATUS_INVALID", "observed": card.get("completion_status")})
        source_anchor = card.get("source_anchor")
        if not isinstance(source_anchor, dict) or source_anchor.get("paper_id") != expected_paper_id:
            issues.append({"code": "CARD_PAPER_ID_MISMATCH", "observed": source_anchor.get("paper_id") if isinstance(source_anchor, dict) else None})
        by_field = source_anchor.get("by_field") if isinstance(source_anchor, dict) else None
        for field in CONTENT_FIELDS:
            anchors = by_field.get(field) if isinstance(by_field, dict) else None
            if not isinstance(anchors, list) or not anchors:
                issues.append({"code": "CARD_ANCHOR_MISSING", "location": field})
                continue
            for index, anchor in enumerate(anchors):
                location = f"source_anchor.by_field.{field}[{index}]"
                if not isinstance(anchor, dict):
                    issues.append({"code": "CARD_ANCHOR_INVALID", "location": location})
                    continue
                chunk_id = anchor.get("chunk_id")
                chunk_value = chunk_map.get(chunk_id)
                if chunk_value is None:
                    issues.append({"code": "CARD_ANCHOR_CHUNK_MISSING", "location": location, "chunk_id": chunk_id})
                elif chunk_value.get("paper_id") != expected_paper_id:
                    issues.append({"code": "CARD_ANCHOR_PAPER_MISMATCH", "location": location, "chunk_id": chunk_id})
                if not anchor.get("section_page"):
                    issues.append({"code": "CARD_ANCHOR_PAGE_MISSING", "location": location})

        try:
            mechanical = mechanical_check(card_path)
            if not mechanical.ok:
                issues.extend(
                    {"code": "EVIDENCE_REVIEW_MECHANICAL_FAILURE", "problem": _problem_to_dict(problem)}
                    for problem in mechanical.problems
                )
            mechanical_summary = {
                "ok": mechanical.ok,
                "checked": mechanical.checked,
                "resolved": mechanical.resolved,
                "broken": mechanical.broken,
                "malformed": mechanical.malformed,
            }
        except Exception as exc:
            issues.append(
                {
                    "code": "EVIDENCE_REVIEW_MECHANICAL_FAILURE",
                    "problem": f"{type(exc).__name__}: {exc}",
                }
            )
            mechanical_summary = {
                "ok": False,
                "checked": 0,
                "resolved": 0,
                "broken": 0,
                "malformed": 1,
            }
        if forced_mechanical_failure:
            issues.append({"code": "EVIDENCE_REVIEW_MECHANICAL_FAILURE", "problem": "injected deterministic failure"})
        view = build_atomic_review_view(card, chunks)
        precheck = deterministic_precheck(card, chunks, view)
        if not precheck.get("ok"):
            issues.extend({"code": "EVIDENCE_REVIEW_ATOMIC_PRECHECK_FAILURE", "issue": issue} for issue in precheck.get("issues", []))
        return {
            "processing_status": "REVIEW_READY" if not issues else "NEEDS_REPAIR",
            "review_status": card.get("review_status"),
            "completion_status": card.get("completion_status"),
            "promotion_status": "NOT_ELIGIBLE",
            "semantic_verification": "NOT_PERFORMED",
            "issues": issues,
            "mechanical": mechanical_summary,
            "atomic_precheck": precheck,
        }

    def verify_bundle(self, bundle_dir: Path) -> dict[str, Any]:
        bundle_dir = Path(bundle_dir)
        manifest_path = bundle_dir / "bundle_manifest.json"
        manifest = read_json(manifest_path)
        required_manifest_keys = {
            "schema_version",
            "adapter_version",
            "run_id",
            "candidate_id",
            "paper_id",
            "candidate_revision",
            "source_revision",
            "materialization_revision",
            "card_revision",
            "input_fingerprint",
            "processing_status",
            "review_status",
            "completion_status",
            "promotion_status",
            "semantic_verification",
            "repair_of_bundle_sha256",
            "sealed_at",
            "bindings",
            "artifacts",
            "bundle_sha256",
        }
        if set(manifest) != required_manifest_keys:
            raise GuardDenied("BUNDLE_CONTRACT_INVALID", "manifest fields are incomplete or unexpected")
        if (
            manifest.get("schema_version") != "0.1"
            or manifest.get("processing_status") not in {"REVIEW_READY", "NEEDS_REPAIR"}
            or manifest.get("review_status") != "pending"
            or manifest.get("completion_status") != "pending_review"
            or manifest.get("promotion_status") != "NOT_ELIGIBLE"
            or manifest.get("semantic_verification") != "NOT_PERFORMED"
        ):
            raise GuardDenied("BUNDLE_CONTRACT_INVALID", "bundle axes violate the Retrieval pending-only contract")
        for field in (
            "adapter_version",
            "run_id",
            "candidate_id",
            "paper_id",
            "candidate_revision",
            "source_revision",
            "materialization_revision",
            "card_revision",
        ):
            if not isinstance(manifest.get(field), str) or not manifest[field]:
                raise GuardDenied("BUNDLE_CONTRACT_INVALID", f"missing identity field: {field}")
        for field in ("input_fingerprint", "bundle_sha256"):
            if not isinstance(manifest.get(field), str) or re.fullmatch(r"[A-F0-9]{64}", manifest[field]) is None:
                raise GuardDenied("BUNDLE_CONTRACT_INVALID", f"invalid hash field: {field}")
        repair_of = manifest.get("repair_of_bundle_sha256")
        if repair_of is not None and (
            not isinstance(repair_of, str) or re.fullmatch(r"[A-F0-9]{64}", repair_of) is None
        ):
            raise GuardDenied("BUNDLE_CONTRACT_INVALID", "repair_of_bundle_sha256 is invalid")
        _parse_time(manifest.get("sealed_at"), code="BUNDLE_CONTRACT_INVALID")
        bindings = manifest.get("bindings")
        expected_binding_keys = {
            "source_sha256",
            "first_approval_receipt_sha256",
            "rights_receipt_sha256",
            "paper_identity_reservation_receipt_sha256",
        }
        if not isinstance(bindings, dict) or set(bindings) != expected_binding_keys:
            raise GuardDenied("BUNDLE_CONTRACT_INVALID", "bundle receipt bindings are incomplete or unexpected")
        if any(not isinstance(value, str) or re.fullmatch(r"[A-F0-9]{64}", value) is None for value in bindings.values()):
            raise GuardDenied("BUNDLE_CONTRACT_INVALID", "bundle receipt binding hash is invalid")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict) or set(artifacts) != {"card", "chunks", "envelope"}:
            raise GuardDenied("BUNDLE_CONTRACT_INVALID", "exact card/chunks/envelope artifact set required")
        core = {key: value for key, value in manifest.items() if key != "bundle_sha256"}
        observed_bundle_hash = canonical_sha256(core)
        if observed_bundle_hash != manifest.get("bundle_sha256"):
            raise GuardDenied("BUNDLE_HASH_MISMATCH", f"manifest: {observed_bundle_hash}")
        artifact_paths: dict[str, Path] = {}
        for name, descriptor in artifacts.items():
            if not isinstance(descriptor, dict) or set(descriptor) != {"relative_path", "sha256"}:
                raise GuardDenied("BUNDLE_CONTRACT_INVALID", f"artifact descriptor invalid: {name}")
            relative_path = descriptor.get("relative_path")
            if not isinstance(relative_path, str) or not relative_path or Path(relative_path).name != relative_path:
                raise GuardDenied("BUNDLE_CONTRACT_INVALID", f"artifact must be a direct bundle child: {name}")
            if not isinstance(descriptor.get("sha256"), str) or re.fullmatch(r"[A-F0-9]{64}", descriptor["sha256"]) is None:
                raise GuardDenied("BUNDLE_CONTRACT_INVALID", f"artifact hash invalid: {name}")
            path = safe_relative_path(bundle_dir, descriptor.get("relative_path", ""))
            if not path.is_file() or file_sha256(path) != descriptor.get("sha256"):
                raise GuardDenied("BUNDLE_HASH_MISMATCH", f"artifact {name}")
            artifact_paths[name] = path
        sealed = read_json(bundle_dir / "SEALED.json")
        if sealed.get("bundle_sha256") != manifest.get("bundle_sha256") or sealed.get("overwrite") != "DENIED":
            raise GuardDenied("BUNDLE_HASH_MISMATCH", "seal")
        card = _read_card(artifact_paths["card"])
        envelope = read_json(artifact_paths["envelope"])
        self.guard_payload(manifest)
        self.guard_payload(card)
        self.guard_payload(envelope)
        card_source_anchor = card.get("source_anchor")
        if (
            not isinstance(card_source_anchor, dict)
            or card_source_anchor.get("paper_id") != manifest["paper_id"]
            or card.get("review_status") != "pending"
            or card.get("completion_status") != "pending_review"
        ):
            raise GuardDenied("BUNDLE_BINDING_MISMATCH", "Card identity or pending axes differ from manifest")
        envelope_manifest_fields = (
            "candidate_id",
            "candidate_revision",
            "source_revision",
            "materialization_revision",
            "card_revision",
            "processing_status",
            "review_status",
            "completion_status",
            "promotion_status",
            "semantic_verification",
            "repair_of_bundle_sha256",
        )
        if any(envelope.get(field) != manifest.get(field) for field in envelope_manifest_fields):
            raise GuardDenied("BUNDLE_BINDING_MISMATCH", "envelope identity, axes, or repair link differs from manifest")
        if envelope.get("schema_version") == "0.2":
            self._validate_contract("CandidateReviewEnvelope", "r0.2", envelope)
            receipt_bindings = envelope.get("receipt_bindings") or {}
            expected = {
                "first_approval_receipt_sha256": manifest["bindings"]["first_approval_receipt_sha256"],
                "rights_receipt_sha256": manifest["bindings"]["rights_receipt_sha256"],
                "paper_identity_reservation_receipt_sha256": manifest["bindings"]["paper_identity_reservation_receipt_sha256"],
            }
            if any(receipt_bindings.get(key) != value for key, value in expected.items()):
                raise GuardDenied("BUNDLE_BINDING_MISMATCH", "r0.2 envelope receipt binding differs")
            facts = envelope.get("upstream_facts") or {}
            if (
                facts.get("service_contracts_identity", {}).get("work_cluster_id") != envelope.get("work_cluster_id")
                or facts.get("service_contracts_identity", {}).get("manifestation_id") != envelope.get("manifestation_id")
                or facts.get("intake_selection", {}).get("primary_direction_id")
                != facts.get("verification_exposure_feedback", {}).get("direction_id")
            ):
                raise GuardDenied("BUNDLE_BINDING_MISMATCH", "r0.2 envelope upstream binding differs")
        evaluated = self.evaluate_card(
            artifact_paths["card"],
            artifact_paths["chunks"],
            expected_paper_id=manifest["paper_id"],
        )
        if (
            evaluated.get("processing_status") != manifest["processing_status"]
            or evaluated.get("review_status") != manifest["review_status"]
            or evaluated.get("completion_status") != manifest["completion_status"]
        ):
            raise GuardDenied("BUNDLE_BINDING_MISMATCH", "deterministic Card evaluation differs from bundle axes")
        if envelope.get("schema_version") == "0.2" and (
            envelope.get("receipt_bindings", {}).get("generation_receipt_sha256")
            != canonical_sha256(evaluated)
        ):
            raise GuardDenied("BUNDLE_BINDING_MISMATCH", "generation receipt differs from deterministic replay")
        return {
            "candidate_id": manifest["candidate_id"],
            "paper_id": manifest["paper_id"],
            "card_revision": manifest["card_revision"],
            "processing_status": manifest["processing_status"],
            "review_status": manifest["review_status"],
            "completion_status": manifest["completion_status"],
            "promotion_status": manifest["promotion_status"],
            "semantic_verification": manifest["semantic_verification"],
            "bundle_sha256": manifest["bundle_sha256"],
            "manifest_path": str(manifest_path),
        }

    def counters_dict(self) -> dict[str, Any]:
        return asdict(self.counters)


__all__ = [
    "ADAPTER_VERSION",
    "CANDIDATE_READER",
    "CandidateCardStager",
    "ExecutionCounters",
    "GuardDenied",
    "PRODUCTION_FILE_ROOT",
    "PRODUCTION_INDEX_ROOT",
    "PRODUCTION_NAMESPACE",
    "canonical_sha256",
    "file_sha256",
    "read_json",
    "safe_relative_path",
]
