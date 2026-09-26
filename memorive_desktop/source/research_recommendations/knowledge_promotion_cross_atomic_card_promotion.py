"""KNOWLEDGE-PROMOTION sandbox-only idempotent atomic Card promotion coordinator.

This candidate is intentionally scoped to the public-safe synthetic A0/B mirror.
It never resolves or writes a production target and it has no network/model path.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from document_processing.naming import PaperIdRegistry, display_title, file_name, folder_name
from evidence_review.atomic_review import build_atomic_review_view, deterministic_precheck
from knowledge_admission.card_io import read_data_id, read_status, write_status
from knowledge_admission.states import is_legal
from artifact_registry.analysis_admission_artifact_registry_materialization import SandboxBoundary
from artifact_registry.registry_v2 import ArtifactRegistry
from artifact_registry.schema_v2 import build_envelope
from research_recommendations.candidate_review_cross_candidate_card_staging import (
    CandidateCardStager,
    GuardDenied as RetrievalGuardDenied,
    canonical_sha256 as retrieval_canonical_sha256,
    file_sha256 as retrieval_file_sha256,
)


APPROVAL_SCHEMA = "memorive.discovery.analysis.second-promotion-approval-receipt.v1"
PREFLIGHT_SCHEMA = "memorive.discovery.analysis.promotion-preflight.v1"
INTENT_SCHEMA = "memorive.discovery.analysis.card-promotion-intent.v1"
JOURNAL_SCHEMA = "memorive.discovery.analysis.promotion-transaction-journal-entry.v1"
RECEIPT_SCHEMA = "memorive.discovery.analysis.promotion-receipt.v1"
FAILURE_RECEIPT_SCHEMA = "memorive.discovery.analysis.promotion-failure-receipt.v1"
FIXTURE_AUTHORITY = "MEMORIVE_SELF_AUTHORED_PUBLIC_SAFE_SYNTHETIC"
TEST_AUTHORITY_CLASS = "TEST_ONLY_NON_AUTHORITATIVE"
TEST_APPROVER = "TEST_ONLY_SYNTHETIC_HUMAN_A"
PROMOTION_WRITER = "KNOWLEDGE_PROMOTION.PROMOTION_COORDINATOR"
RECEIPT_WRITER = "KNOWLEDGE_PROMOTION.ATOMIC_PROMOTION_WRITER"
TERMINAL_STATES = {"COMMITTED", "ROLLED_BACK", "COMPENSATION_REQUIRED"}
TARGET_RESOURCE_DIRS = (
    "formal_library",
    "paper_id_registry.yaml",
    "identifier_registry",
    "knowledge_admission",
    "artifact_registry",
    "formal_index",
    "visibility",
)
_NAMESPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_ANALYSIS_KEYS = {"analysis", "analysisresult", "analysis_result", "analysis_payload"}
_FORBIDDEN_TYPE_TOKENS = {
    "analysis",
    "analysisresult",
    "analysispayload",
    "research_analysis",
    "quality",
    "theory",
    "crosspapersynthesis",
    "formalauthorityweight",
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest().upper()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def with_content_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result.pop("content_hash", None)
    result["content_hash"] = canonical_sha256(result)
    return result


def verify_content_hash(value: Mapping[str, Any], *, code: str) -> None:
    core = {key: item for key, item in value.items() if key != "content_hash"}
    if value.get("content_hash") != canonical_sha256(core):
        raise PromotionError(code, "canonical content_hash mismatch")


def read_json(path: Path | str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _contains(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def _parse_time(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise PromotionError("SECOND_APPROVAL_EXPIRED_OR_REVOKED", f"{field_name} missing")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone offset is required")
        return parsed
    except (TypeError, ValueError) as exc:
        raise PromotionError(
            "SECOND_APPROVAL_EXPIRED_OR_REVOKED", f"{field_name} is invalid"
        ) from exc


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _normalize_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def _walk_type_tokens(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized_key = _normalize_token(key)
            if normalized_key in _FORBIDDEN_TYPE_TOKENS:
                yield normalized_key
            if normalized_key in {"type", "artifacttype", "payloadtype", "objecttype"}:
                normalized_value = _normalize_token(child)
                if normalized_value in _FORBIDDEN_TYPE_TOKENS:
                    yield normalized_value
            yield from _walk_type_tokens(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_type_tokens(child)


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError, OSError):
        attributes = 0
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _safe_relative_target(root: Path, relative_path: Any) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise PromotionError("RECEIPT_HASH_INVALID", "formal file path is missing")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise PromotionError("RECEIPT_HASH_INVALID", f"unsafe formal file path: {relative_path}")
    target = (root / relative).resolve(strict=False)
    if not _contains(root.resolve(strict=False), target):
        raise PromotionError("RECEIPT_HASH_INVALID", f"formal file path escapes target: {relative_path}")
    return target


def _read_card_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_bytes().decode("utf-8")
    fences = list(re.finditer(r"(?m)^---[ \t]*\r?$", text))
    if len(fences) < 2 or fences[0].start() != 0:
        raise PromotionError("CARD_SCHEMA_OR_COMPLETION_INVALID", "Card frontmatter invalid")
    value = yaml.safe_load(text[fences[0].end() : fences[1].start()])
    if not isinstance(value, dict):
        raise PromotionError("CARD_SCHEMA_OR_COMPLETION_INVALID", "Card frontmatter not object")
    return value


def _read_chunks(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise PromotionError("CARD_ANCHOR_INVALID", "chunk is not object")
        result.append(item)
    return result


def inventory(root: Path, *, include_dirs: Iterable[str] = TARGET_RESOURCE_DIRS) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for name in include_dirs:
        base = root / name
        if not base.exists():
            continue
        if base.is_file():
            records.append(
                {
                    "relative_path": base.relative_to(root).as_posix(),
                    "sha256": sha256_file(base),
                    "bytes": base.stat().st_size,
                }
            )
            continue
        for path in sorted((item for item in base.rglob("*") if item.is_file()), key=str):
            records.append(
                {
                    "relative_path": path.relative_to(root).as_posix(),
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                }
            )
    return records


def effective_target_hash(root: Path) -> str:
    return canonical_sha256(inventory(root))


class PromotionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class InjectedFault(RuntimeError):
    def __init__(self, fault_point: str):
        super().__init__(fault_point)
        self.fault_point = fault_point


@dataclass
class PromotionResult:
    outcome: str
    transaction_state: str
    error_code: str | None = None
    error_message: str | None = None
    promotion_id: str | None = None
    intent_hash: str | None = None
    receipt_path: str | None = None
    receipt_bytes: bytes | None = field(default=None, repr=False)
    idempotent_replay: bool = False
    visibility: bool = False
    visibility_reasons: list[str] = field(default_factory=list)
    write_counts: dict[str, int] = field(
        default_factory=lambda: {
            "formal_file_writes": 0,
            "knowledge_admission_appends": 0,
            "artifact_registry_appends": 0,
            "index_writes": 0,
            "receipt_writes": 0,
        }
    )
    external_effects: dict[str, int | float] = field(
        default_factory=lambda: {
            "network_calls": 0,
            "model_calls": 0,
            "ocr_calls": 0,
            "embedding_calls": 0,
            "credential_reads": 0,
            "tokens": 0,
            "cost_cny": 0.0,
        }
    )
    production_effects: dict[str, int] = field(
        default_factory=lambda: {"writes": 0, "mutations": 0, "payload_reads": 0}
    )

    def evidence_dict(self) -> dict[str, Any]:
        value = asdict(self)
        raw = value.pop("receipt_bytes")
        value["receipt_bytes_sha256"] = sha256_bytes(raw) if raw is not None else None
        value["receipt_bytes_length"] = len(raw) if raw is not None else 0
        return value


class AtomicCardPromotionCoordinator:
    """One sandbox writer coordinating formal files, KNOWLEDGE_ADMISSION, ARTIFACT_REGISTRY, index and receipt."""

    def __init__(
        self,
        *,
        run_root: Path | str,
        repo_root: Path | str,
        contract_root: Path | str,
        protected_roots: Iterable[Path | str],
        event_time: str,
    ) -> None:
        self.run_root = Path(run_root).resolve(strict=False)
        self.repo_root = Path(repo_root).resolve(strict=True)
        self.contract_root = Path(contract_root).resolve(strict=True)
        self.event_time = event_time
        self.event_datetime = _parse_time(event_time, "event_time")
        self.boundary = SandboxBoundary.build(self.run_root, protected_roots)
        self.protected_roots = tuple(
            Path(item).resolve(strict=False) for item in self.boundary.protected_roots
        )
        self._schema_paths = {
            APPROVAL_SCHEMA: self.contract_root
            / "Memorive_KNOWLEDGE_PROMOTION_CROSS_SecondPromotionApprovalReceipt_schema_candidate001.json",
            PREFLIGHT_SCHEMA: self.contract_root
            / "Memorive_KNOWLEDGE_PROMOTION_CROSS_PromotionPreflight_schema_candidate001.json",
            INTENT_SCHEMA: self.contract_root
            / "Memorive_KNOWLEDGE_PROMOTION_CROSS_CardPromotionIntent_schema_candidate001.json",
            JOURNAL_SCHEMA: self.contract_root
            / "Memorive_KNOWLEDGE_PROMOTION_CROSS_PromotionTransactionJournal_schema_candidate001.json",
            RECEIPT_SCHEMA: self.contract_root
            / "Memorive_KNOWLEDGE_PROMOTION_CROSS_PromotionReceipt_schema_candidate001.json",
        }
        failure_candidates = list(
            (self.repo_root / "项目文档" / "Discovery_文献发现" / "10_模块契约").glob(
                "*PromotionFailureReceipt*r0.1.schema.json"
            )
        )
        if len(failure_candidates) != 1:
            raise PromotionError(
                "CONTRACT_SCHEMA_INVALID",
                f"PromotionFailureReceipt schema count={len(failure_candidates)}",
            )
        self._schema_paths[FAILURE_RECEIPT_SCHEMA] = failure_candidates[0]

    def _validate_schema(self, schema_id: str, value: Mapping[str, Any]) -> None:
        schema = read_json(self._schema_paths[schema_id])
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
            key=lambda item: list(item.path),
        )
        if errors:
            detail = "; ".join(error.message for error in errors[:3])
            raise PromotionError("CONTRACT_SCHEMA_INVALID", f"{schema_id}: {detail}")

    def _write_bytes(self, path: Path, data: bytes, *, exclusive: bool = False) -> None:
        target = self.boundary.require_write_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if exclusive and target.exists():
            raise PromotionError("IMMUTABLE_WRITE_CONFLICT", str(target))
        fd, temp_name = tempfile.mkstemp(dir=target.parent, prefix=".analysis_tmp_", suffix=".tmp")
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if exclusive and target.exists():
                raise PromotionError("IMMUTABLE_WRITE_CONFLICT", str(target))
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink()

    def _write_json(self, path: Path, value: Mapping[str, Any], *, exclusive: bool = False) -> bytes:
        data = _json_bytes(value)
        self._write_bytes(path, data, exclusive=exclusive)
        return data

    def _append_journal(
        self,
        path: Path,
        *,
        promotion_id: str,
        intent_hash: str,
        state: str,
        event: str,
        evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        target = self.boundary.require_write_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        sequence = 1
        if target.exists():
            sequence = len(self._journal_entries(target)) + 1
        entry = with_content_hash(
            {
                "schema_version": JOURNAL_SCHEMA,
                "sequence": sequence,
                "promotion_id": promotion_id,
                "intent_hash": intent_hash,
                "state": state,
                "event": event,
                "recorded_at": self.event_time,
                "evidence": copy.deepcopy(dict(evidence)),
            }
        )
        self._validate_schema(JOURNAL_SCHEMA, entry)
        with target.open("ab") as stream:
            stream.write(canonical_bytes(entry) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        return entry

    def _journal_entries(
        self,
        path: Path,
        *,
        expected_promotion_id: str | None = None,
        expected_intent_hash: str | None = None,
    ) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            raw_entries = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line
            ]
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PromotionError("UNFINISHED_JOURNAL_BLOCKED", f"journal is unreadable: {exc}") from exc
        for sequence, entry in enumerate(raw_entries, start=1):
            if not isinstance(entry, dict):
                raise PromotionError("UNFINISHED_JOURNAL_BLOCKED", "journal entry is not an object")
            try:
                self._validate_schema(JOURNAL_SCHEMA, entry)
                verify_content_hash(entry, code="UNFINISHED_JOURNAL_BLOCKED")
            except PromotionError as exc:
                raise PromotionError("UNFINISHED_JOURNAL_BLOCKED", exc.message) from exc
            if entry.get("sequence") != sequence:
                raise PromotionError("UNFINISHED_JOURNAL_BLOCKED", "journal sequence drift")
            if expected_promotion_id is not None and entry.get("promotion_id") != expected_promotion_id:
                raise PromotionError("UNFINISHED_JOURNAL_BLOCKED", "journal promotion_id drift")
            if expected_intent_hash is not None and entry.get("intent_hash") != expected_intent_hash:
                raise PromotionError("UNFINISHED_JOURNAL_BLOCKED", "journal intent_hash drift")
            entries.append(entry)
        return entries

    @staticmethod
    def _load_ledger(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"schema_version": "memorive.discovery.analysis.idempotency-ledger.v1", "promotions": {}}
        try:
            ledger = read_json(path)
        except (OSError, UnicodeError, ValueError) as exc:
            raise PromotionError(
                "UNFINISHED_JOURNAL_BLOCKED", "idempotency ledger is unreadable"
            ) from exc
        if (
            ledger.get("schema_version") != "memorive.discovery.analysis.idempotency-ledger.v1"
            or not isinstance(ledger.get("promotions"), dict)
        ):
            raise PromotionError("UNFINISHED_JOURNAL_BLOCKED", "idempotency ledger shape drift")
        for promotion_id, record in ledger["promotions"].items():
            if (
                not isinstance(promotion_id, str)
                or not promotion_id
                or not isinstance(record, dict)
                or record.get("state") not in TERMINAL_STATES
                or not isinstance(record.get("intent_hash"), str)
            ):
                raise PromotionError("UNFINISHED_JOURNAL_BLOCKED", "idempotency ledger record drift")
        return ledger

    def _validate_target(self, target_root: Path, target_namespace: str) -> Path:
        lexical = target_root.absolute()
        cursor = lexical
        while cursor != cursor.parent:
            if cursor.exists() and _is_reparse_point(cursor):
                raise PromotionError("TARGET_PATH_DENIED", f"reparse/symlink: {cursor}")
            if cursor == self.run_root:
                break
            cursor = cursor.parent
        resolved = lexical.resolve(strict=False)
        if any(_contains(root, resolved) for root in self.protected_roots):
            raise PromotionError("PRODUCTION_TARGET_DENIED", str(resolved))
        try:
            self.boundary.require_write_path(resolved)
        except Exception as exc:
            raise PromotionError("TARGET_PATH_DENIED", str(resolved)) from exc
        if not _NAMESPACE.fullmatch(target_namespace):
            raise PromotionError("TARGET_PATH_DENIED", target_namespace)
        if Path(target_namespace).is_absolute() or ".." in Path(target_namespace).parts:
            raise PromotionError("TARGET_PATH_DENIED", target_namespace)
        if resolved.exists():
            for item in (resolved, *resolved.rglob("*")):
                if _is_reparse_point(item):
                    raise PromotionError("TARGET_PATH_DENIED", f"reparse/symlink: {item}")
        return resolved

    def _validate_read_input(self, path: Path, *, label: str) -> Path:
        lexical = path.absolute()
        resolved = lexical.resolve(strict=True)
        if not _contains(self.run_root, resolved):
            raise PromotionError("TARGET_PATH_DENIED", f"{label} is outside current run root")
        cursor = lexical
        while cursor != self.run_root and cursor != cursor.parent:
            if cursor.exists() and _is_reparse_point(cursor):
                raise PromotionError("TARGET_PATH_DENIED", f"{label} traverses reparse point: {cursor}")
            cursor = cursor.parent
        if resolved.is_dir():
            for item in resolved.rglob("*"):
                if _is_reparse_point(item):
                    raise PromotionError("TARGET_PATH_DENIED", f"{label} contains reparse point: {item}")
        return resolved

    def _validate_approval(
        self,
        approval: Mapping[str, Any] | None,
        *,
        manifest: Mapping[str, Any],
        target_namespace: str,
        prestate_hash: str,
    ) -> dict[str, Any]:
        if approval is None:
            raise PromotionError("SECOND_APPROVAL_MISSING", "receipt is required")
        value = copy.deepcopy(dict(approval))
        decided_at = _parse_time(value.get("decided_at"), "decided_at")
        expires_at = _parse_time(value.get("expires_at"), "expires_at")
        try:
            self._validate_schema(APPROVAL_SCHEMA, value)
        except PromotionError as exc:
            raise PromotionError("SECOND_APPROVAL_BINDING_MISMATCH", exc.message) from exc
        verify_content_hash(value, code="SECOND_APPROVAL_BINDING_MISMATCH")
        if value["decision"] != "APPROVE":
            raise PromotionError("SECOND_APPROVAL_DECISION_NOT_APPROVE", value["decision"])
        if value["revoked_at"] is not None or value["revocation_ref"] is not None:
            raise PromotionError("SECOND_APPROVAL_EXPIRED_OR_REVOKED", "receipt revoked")
        if expires_at <= self.event_datetime:
            raise PromotionError("SECOND_APPROVAL_EXPIRED_OR_REVOKED", "receipt expired")
        if decided_at > self.event_datetime or decided_at >= expires_at:
            raise PromotionError("SECOND_APPROVAL_EXPIRED_OR_REVOKED", "decision is future-dated")
        if (
            value["synthetic_fixture"] is not True
            or value["authority_class"] != TEST_AUTHORITY_CLASS
            or value["fixture_authority"] != FIXTURE_AUTHORITY
            or value["decided_by"] != TEST_APPROVER
            or not all(str(item).startswith("fixture://") for item in value["evidence_refs"])
        ):
            raise PromotionError("SECOND_APPROVAL_AUTHORITY_INVALID", "A/B requires fixture-only authority")
        bindings = {
            "candidate_id": manifest.get("candidate_id"),
            "card_revision": manifest.get("card_revision"),
            "candidate_bundle_hash": manifest.get("bundle_sha256"),
            "target_namespace": target_namespace,
            "paper_id": manifest.get("paper_id"),
            "target_prestate_hash": prestate_hash,
        }
        mismatches = [key for key, expected in bindings.items() if value.get(key) != expected]
        if mismatches:
            raise PromotionError(
                "SECOND_APPROVAL_BINDING_MISMATCH", "fields=" + ",".join(mismatches)
            )
        return value

    @staticmethod
    def _authority_file(authority_root: Path, token: str) -> Path:
        matches = sorted(path for path in authority_root.glob(f"*{token}*") if path.is_file())
        if len(matches) != 1:
            raise PromotionError("RIGHTS_OR_SOURCE_BINDING_DRIFT", f"{token} exact file missing")
        return matches[0]

    def _validate_bundle_contract(self, bundle: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        manifest_path = bundle / "bundle_manifest.json"
        manifest = read_json(manifest_path)
        self._validate_no_analysis(bundle, (manifest,))
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
            raise PromotionError("BUNDLE_HASH_OR_MEMBER_DRIFT", "manifest exact fields drift")
        if (
            manifest.get("schema_version") != "0.1"
            or manifest.get("processing_status") != "REVIEW_READY"
            or manifest.get("review_status") != "pending"
            or manifest.get("completion_status") != "pending_review"
            or manifest.get("promotion_status") != "NOT_ELIGIBLE"
            or manifest.get("semantic_verification") != "NOT_PERFORMED"
        ):
            raise PromotionError("CANDIDATE_STATE_NOT_READY", "bundle axes violate pending-only promotion input")
        for field_name in (
            "adapter_version",
            "run_id",
            "candidate_id",
            "paper_id",
            "candidate_revision",
            "source_revision",
            "materialization_revision",
            "card_revision",
        ):
            if not isinstance(manifest.get(field_name), str) or not manifest[field_name]:
                raise PromotionError("BUNDLE_HASH_OR_MEMBER_DRIFT", f"invalid manifest identity: {field_name}")
        if any(
            not isinstance(manifest.get(field_name), str)
            or re.fullmatch(r"[A-F0-9]{64}", manifest[field_name]) is None
            for field_name in ("input_fingerprint", "bundle_sha256")
        ):
            raise PromotionError("BUNDLE_HASH_OR_MEMBER_DRIFT", "invalid manifest hash")
        try:
            _parse_time(manifest.get("sealed_at"), "sealed_at")
        except PromotionError as exc:
            raise PromotionError("BUNDLE_HASH_OR_MEMBER_DRIFT", exc.message) from exc
        bindings = manifest.get("bindings")
        binding_keys = {
            "source_sha256",
            "first_approval_receipt_sha256",
            "rights_receipt_sha256",
            "paper_identity_reservation_receipt_sha256",
        }
        if not isinstance(bindings, dict) or set(bindings) != binding_keys or any(
            not isinstance(value, str) or re.fullmatch(r"[A-F0-9]{64}", value) is None
            for value in bindings.values()
        ):
            raise PromotionError("BUNDLE_HASH_OR_MEMBER_DRIFT", "manifest authority bindings drift")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict) or set(artifacts) != {"card", "chunks", "envelope"}:
            raise PromotionError("BUNDLE_HASH_OR_MEMBER_DRIFT", "exact card/chunks/envelope artifacts required")
        expected_members = {"bundle_manifest.json", "SEALED.json"}
        for name, descriptor in artifacts.items():
            if not isinstance(descriptor, dict) or set(descriptor) != {"relative_path", "sha256"}:
                raise PromotionError("BUNDLE_HASH_OR_MEMBER_DRIFT", f"artifact descriptor drift: {name}")
            relative_path = descriptor.get("relative_path")
            if (
                not isinstance(relative_path, str)
                or not relative_path
                or Path(relative_path).name != relative_path
                or not isinstance(descriptor.get("sha256"), str)
                or re.fullmatch(r"[A-F0-9]{64}", descriptor["sha256"]) is None
            ):
                raise PromotionError("BUNDLE_HASH_OR_MEMBER_DRIFT", f"artifact path/hash drift: {name}")
            expected_members.add(relative_path)
        actual_members = {
            path.relative_to(bundle).as_posix()
            for path in bundle.rglob("*")
            if path.is_file()
        }
        if actual_members != expected_members:
            raise PromotionError("BUNDLE_HASH_OR_MEMBER_DRIFT", "bundle member set drift")
        try:
            stager = CandidateCardStager(
                space_root=bundle.parent,
                fixture_root=bundle.parent,
                repo_root=self.repo_root,
            )
            verified = stager.verify_bundle(bundle)
        except RetrievalGuardDenied as exc:
            if exc.code == "BUNDLE_BINDING_MISMATCH":
                envelope = read_json(bundle / artifacts["envelope"]["relative_path"])
                envelope_state_fields = (
                    "processing_status",
                    "review_status",
                    "completion_status",
                    "promotion_status",
                    "semantic_verification",
                )
                if any(
                    envelope.get(field_name) != manifest.get(field_name)
                    for field_name in envelope_state_fields
                ):
                    raise PromotionError(
                        "CANDIDATE_STATE_NOT_READY",
                        "envelope axes differ from manifest",
                    ) from exc
                try:
                    self._validate_card(bundle, manifest, envelope)
                except PromotionError as semantic_exc:
                    if semantic_exc.code in {
                        "CARD_SCHEMA_OR_COMPLETION_INVALID",
                        "CARD_ANCHOR_INVALID",
                    }:
                        raise semantic_exc from exc
            raise PromotionError("BUNDLE_HASH_OR_MEMBER_DRIFT", str(exc)) from exc
        except Exception as exc:
            raise PromotionError("BUNDLE_HASH_OR_MEMBER_DRIFT", str(exc)) from exc
        envelope = read_json(bundle / artifacts["envelope"]["relative_path"])
        envelope_identity_fields = (
            "candidate_id",
            "candidate_revision",
            "source_revision",
            "materialization_revision",
            "card_revision",
            "repair_of_bundle_sha256",
        )
        envelope_state_fields = (
            "processing_status",
            "review_status",
            "completion_status",
            "promotion_status",
            "semantic_verification",
        )
        if any(envelope.get(field_name) != manifest.get(field_name) for field_name in envelope_state_fields):
            raise PromotionError("CANDIDATE_STATE_NOT_READY", "envelope axes differ from manifest")
        if any(envelope.get(field_name) != manifest.get(field_name) for field_name in envelope_identity_fields):
            raise PromotionError("BUNDLE_HASH_OR_MEMBER_DRIFT", "envelope identity differs from manifest")
        return manifest, verified

    def _validate_prestate(
        self,
        target: Path,
        target_namespace: str,
        prestate: Mapping[str, Any],
    ) -> None:
        required = {
            "schema_version",
            "fixture_id",
            "fixture_authority",
            "target_namespace",
            "formal_library",
            "paper_id_registry",
            "identifier_registry",
            "knowledge_admission",
            "artifact_registry",
            "formal_index",
            "visibility",
            "promotion_receipts",
            "transaction_journals",
            "content_hash",
        }
        if set(prestate) != required or (
            prestate.get("schema_version") != "memorive.discovery.analysis.formal-target-prestate-fixture.v1"
            or prestate.get("fixture_authority") != FIXTURE_AUTHORITY
            or prestate.get("target_namespace") != target_namespace
        ):
            raise PromotionError("TARGET_PRESTATE_DRIFT", "prestate contract or namespace drift")
        formal_expected = prestate.get("formal_library", {}).get("objects")
        if not isinstance(formal_expected, list):
            raise PromotionError("TARGET_PRESTATE_DRIFT", "formal_library.objects invalid")
        formal_actual = inventory(target, include_dirs=("formal_library",))
        if formal_expected != formal_actual:
            raise PromotionError("TARGET_PRESTATE_DRIFT", "formal library inventory differs from approved prestate")

        registry_path = target / "paper_id_registry.yaml"
        registry_actual: dict[str, Any] = {}
        if registry_path.is_file():
            loaded = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                raise PromotionError("TARGET_PRESTATE_DRIFT", "paper_id registry is not a mapping")
            for paper_id, binding in loaded.items():
                if isinstance(binding, Mapping):
                    registry_actual[str(paper_id)] = binding.get("folder")
                else:
                    registry_actual[str(paper_id)] = binding
        registry_expected = prestate.get("paper_id_registry", {}).get("bindings")
        if not isinstance(registry_expected, dict) or registry_actual != registry_expected:
            raise PromotionError("TARGET_PRESTATE_DRIFT", "paper_id registry differs from approved prestate")

        knowledge_admission_expected = prestate.get("knowledge_admission")
        if not isinstance(knowledge_admission_expected, dict) or not isinstance(knowledge_admission_expected.get("states"), dict) or not isinstance(knowledge_admission_expected.get("events"), list):
            raise PromotionError("TARGET_PRESTATE_DRIFT", "KNOWLEDGE_ADMISSION prestate shape invalid")
        states_path = target / "knowledge_admission" / "states.json"
        states_doc = read_json(states_path) if states_path.is_file() else {"states": {}}
        states_actual = states_doc.get("states")
        if not isinstance(states_actual, dict):
            raise PromotionError("TARGET_PRESTATE_DRIFT", "live KNOWLEDGE_ADMISSION states invalid")
        expected_knowledge_admission_head = knowledge_admission_expected.get("head")
        actual_knowledge_admission_head = canonical_sha256(states_actual) if states_actual else None
        stored_knowledge_admission_head = states_doc.get("head")
        if states_actual and stored_knowledge_admission_head != actual_knowledge_admission_head:
            raise PromotionError("TARGET_PRESTATE_DRIFT", "live KNOWLEDGE_ADMISSION head invalid")
        events_path = target / "knowledge_admission" / "events.jsonl"
        try:
            events_actual = [
                json.loads(line)
                for line in events_path.read_text(encoding="utf-8").splitlines()
                if line
            ] if events_path.is_file() else []
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PromotionError("TARGET_PRESTATE_DRIFT", f"live KNOWLEDGE_ADMISSION events invalid: {exc}") from exc
        if (
            states_actual != knowledge_admission_expected["states"]
            or events_actual != knowledge_admission_expected["events"]
            or actual_knowledge_admission_head != expected_knowledge_admission_head
        ):
            raise PromotionError("TARGET_PRESTATE_DRIFT", "KNOWLEDGE_ADMISSION differs from approved prestate")

        index_expected = prestate.get("formal_index")
        if not isinstance(index_expected, dict) or not isinstance(index_expected.get("entries"), dict):
            raise PromotionError("TARGET_PRESTATE_DRIFT", "formal index prestate invalid")
        index_path = target / "formal_index" / "active.json"
        index_doc = read_json(index_path) if index_path.is_file() else {"entries": {}, "head": None}
        index_entries = index_doc.get("entries")
        actual_index_head = canonical_sha256(index_entries) if index_entries else None
        if (
            not isinstance(index_entries, dict)
            or index_doc.get("head") != actual_index_head
            or index_entries != index_expected["entries"]
            or actual_index_head != index_expected.get("head")
        ):
            raise PromotionError("TARGET_PRESTATE_DRIFT", "formal index differs from approved prestate")

        artifact_registry_expected = prestate.get("artifact_registry")
        if not isinstance(artifact_registry_expected, dict) or not isinstance(artifact_registry_expected.get("events"), list):
            raise PromotionError("TARGET_PRESTATE_DRIFT", "ARTIFACT_REGISTRY prestate invalid")
        artifact_registry_events: list[dict[str, Any]] = []
        for path in sorted((target / "artifact_registry" / "registry").glob("*.jsonl")):
            try:
                artifact_registry_events.extend(
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line
                )
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise PromotionError("TARGET_PRESTATE_DRIFT", f"ARTIFACT_REGISTRY registry invalid: {exc}") from exc
        artifact_registry_head = canonical_sha256(artifact_registry_events) if artifact_registry_events else None
        if artifact_registry_events != artifact_registry_expected["events"] or artifact_registry_head != artifact_registry_expected.get("head"):
            raise PromotionError("TARGET_PRESTATE_DRIFT", "ARTIFACT_REGISTRY differs from approved prestate")

        receipts_expected = prestate.get("promotion_receipts")
        if not isinstance(receipts_expected, dict):
            raise PromotionError("TARGET_PRESTATE_DRIFT", "promotion receipt prestate invalid")
        receipts_actual = {
            path.stem: sha256_file(path)
            for path in sorted((target / "receipts").glob("*.json"))
        }
        if receipts_actual != receipts_expected:
            raise PromotionError("TARGET_PRESTATE_DRIFT", "promotion receipts differ from approved prestate")

    def _validate_authority(
        self,
        authority_root: Path,
        manifest: Mapping[str, Any],
        envelope: Mapping[str, Any],
        card: Mapping[str, Any],
    ) -> list[tuple[str, str]]:
        base_path = self._authority_file(authority_root, "BaseStagingRequest")
        first_path = self._authority_file(authority_root, "FirstApprovalReceipt")
        rights_path = self._authority_file(authority_root, "RightsVerificationReceipt")
        reservation_path = self._authority_file(authority_root, "PaperIdentityReservationReceipt")
        source_path = self._authority_file(authority_root, "public_safe_synthetic_source")
        bindings = manifest.get("bindings") or {}
        observed = {
            "base_staging_request_sha256": sha256_file(base_path),
            "first_approval_receipt_sha256": sha256_file(first_path),
            "rights_receipt_sha256": sha256_file(rights_path),
            "paper_identity_reservation_receipt_sha256": sha256_file(reservation_path),
            "source_sha256": sha256_file(source_path),
        }
        if any(
            bindings.get(key) != value
            for key, value in observed.items()
            if key != "base_staging_request_sha256"
        ):
            raise PromotionError("RIGHTS_OR_SOURCE_BINDING_DRIFT", "authority byte hash drift")
        base = read_json(base_path)
        first = read_json(first_path)
        rights = read_json(rights_path)
        reservation = read_json(reservation_path)
        envelope_schema_version = str(envelope.get("schema_version") or "")
        first_schema_version = str(first.get("schema_version") or "")
        if envelope_schema_version == "0.2":
            if first_schema_version != "0.2":
                raise PromotionError(
                    "RIGHTS_OR_SOURCE_BINDING_DRIFT",
                    "r0.2 envelope requires r0.2 first approval authority",
                )
            retrieval_verifier = CandidateCardStager(
                space_root=self.run_root / "__analysis_authority_verifier_unused_space__",
                fixture_root=authority_root,
                repo_root=self.repo_root,
            )
            retrieval_verifier._validate_contract("FirstApprovalReceipt", "r0.2", first)
            retrieval_verifier._validate_contract("CandidateReviewEnvelope", "r0.2", dict(envelope))
            verify_content_hash(first, code="RIGHTS_OR_SOURCE_BINDING_DRIFT")
            upstream_facts = envelope.get("upstream_facts") or {}
            if (
                not isinstance(upstream_facts, dict)
                or first.get("approved_input_hash") != canonical_sha256(upstream_facts)
                or first.get("direction_id")
                != upstream_facts.get("intake_selection", {}).get("primary_direction_id")
                or upstream_facts.get("intake_selection", {}).get("primary_direction_id")
                != upstream_facts.get("verification_exposure_feedback", {}).get("direction_id")
                or upstream_facts.get("service_contracts_identity", {}).get("work_cluster_id")
                != envelope.get("work_cluster_id")
                or upstream_facts.get("service_contracts_identity", {}).get("manifestation_id")
                != envelope.get("manifestation_id")
            ):
                raise PromotionError(
                    "RIGHTS_OR_SOURCE_BINDING_DRIFT",
                    "r0.2 upstream facts/approval direction binding drift",
                )
            receipt_bindings = envelope.get("receipt_bindings") or {}
            expected_receipt_bindings = {
                "first_approval_receipt_sha256": observed["first_approval_receipt_sha256"],
                "rights_receipt_sha256": observed["rights_receipt_sha256"],
                "paper_identity_reservation_receipt_sha256": observed[
                    "paper_identity_reservation_receipt_sha256"
                ],
            }
            if any(
                receipt_bindings.get(key) != expected_hash
                for key, expected_hash in expected_receipt_bindings.items()
            ):
                raise PromotionError(
                    "RIGHTS_OR_SOURCE_BINDING_DRIFT", "r0.2 envelope receipt binding drift"
                )
        elif envelope_schema_version != "0.1" or first_schema_version != "0.1":
            raise PromotionError(
                "RIGHTS_OR_SOURCE_BINDING_DRIFT",
                "Retrieval authority/envelope revisions must both be 0.1 or 0.2",
            )
        manifest_fields = (
            "candidate_id",
            "candidate_revision",
            "source_revision",
            "materialization_revision",
            "card_revision",
        )
        if base.get("identity_status") != "VERIFIED" or any(
            base.get(field_name) != manifest.get(field_name) for field_name in manifest_fields
        ):
            raise PromotionError("CANDIDATE_STATE_NOT_READY", "identity is not VERIFIED")
        common_identity = ("candidate_id", "work_cluster_id", "manifestation_id", "source_revision")
        if any(first.get(field_name) != base.get(field_name) for field_name in common_identity) or first.get(
            "candidate_revision"
        ) != base.get("candidate_revision"):
            raise PromotionError("RIGHTS_OR_SOURCE_BINDING_DRIFT", "first approval identity/version drift")
        if (
            first.get("fixture_authority") != FIXTURE_AUTHORITY
            or first.get("scope") != "PURE_OFFLINE_A0_SYNTHETIC_CARD_STAGING_ONLY"
            or first.get("approved_source_sha256") != observed["source_sha256"]
            or first.get("revoked") is not False
        ):
            raise PromotionError("CANDIDATE_STATE_NOT_READY", "first approval authority/scope is not active")
        try:
            issued_at = _parse_time(first.get("issued_at"), "issued_at")
            first_expires = _parse_time(first.get("expires_at"), "expires_at")
        except PromotionError as exc:
            raise PromotionError("RIGHTS_OR_SOURCE_BINDING_DRIFT", exc.message) from exc
        if issued_at > self.event_datetime or first_expires <= self.event_datetime or issued_at >= first_expires:
            raise PromotionError("CANDIDATE_STATE_NOT_READY", "first approval time window is inactive")
        if rights.get("access_status") not in {"RIGHTS_VERIFIED", "MATERIALIZED"}:
            raise PromotionError("RIGHTS_OR_SOURCE_BINDING_DRIFT", "rights are not verified")
        if (
            any(rights.get(field_name) != base.get(field_name) for field_name in common_identity)
            or rights.get("source_sha256") != observed["source_sha256"]
            or rights.get("fixture_authority") != FIXTURE_AUTHORITY
            or rights.get("rights_basis") != FIXTURE_AUTHORITY
            or rights.get("external_retrieval_required") is not False
        ):
            raise PromotionError("RIGHTS_OR_SOURCE_BINDING_DRIFT", "source binding drift")
        try:
            rights_time = _parse_time(rights.get("verified_at"), "verified_at")
        except PromotionError as exc:
            raise PromotionError("RIGHTS_OR_SOURCE_BINDING_DRIFT", exc.message) from exc
        if rights_time > self.event_datetime:
            raise PromotionError("RIGHTS_OR_SOURCE_BINDING_DRIFT", "rights receipt is future-dated")
        if first.get("decision_status") != "APPROVED_FOR_CARD_STAGING" or first.get("revoked"):
            raise PromotionError("CANDIDATE_STATE_NOT_READY", "first decision is not active")
        reservation_identity = ("candidate_id", "work_cluster_id", "manifestation_id")
        year = reservation.get("year")
        if (
            any(reservation.get(field_name) != base.get(field_name) for field_name in reservation_identity)
            or reservation.get("fixture_authority") != FIXTURE_AUTHORITY
            or reservation.get("reservation_status") != "RESERVED_CANDIDATE"
            or reservation.get("paper_id") != manifest.get("paper_id")
            or reservation.get("paper_id") != base.get("paper_identity", {}).get("paper_id")
            or reservation.get("folder_key") != manifest.get("candidate_id")
            or not isinstance(year, int)
            or isinstance(year, bool)
            or f"{reservation.get('surname')}{year}{reservation.get('explicit_suffix')}" != reservation.get("paper_id")
            or reservation.get("first_approval_receipt_hash") != observed["first_approval_receipt_sha256"]
            or reservation.get("rights_receipt_hash") != observed["rights_receipt_sha256"]
        ):
            raise PromotionError("PAPER_ID_CONFLICT", "reservation paper_id mismatch")
        pointer_bindings = {
            "source_fixture": observed["source_sha256"],
            "first_approval_fixture": observed["first_approval_receipt_sha256"],
            "rights_fixture": observed["rights_receipt_sha256"],
            "reservation_fixture": observed["paper_identity_reservation_receipt_sha256"],
        }
        if any(base.get(name, {}).get("sha256") != expected for name, expected in pointer_bindings.items()):
            raise PromotionError("RIGHTS_OR_SOURCE_BINDING_DRIFT", "base authority pointer drift")
        envelope_identity = (
            "candidate_id",
            "work_cluster_id",
            "manifestation_id",
            "candidate_revision",
            "source_revision",
            "materialization_revision",
            "card_revision",
        )
        if any(envelope.get(field_name) != base.get(field_name) for field_name in envelope_identity) or (
            envelope.get("provenance", {}).get("fixture_authority") != FIXTURE_AUTHORITY
            or envelope.get("provenance", {}).get("source_sha256") != observed["source_sha256"]
        ):
            raise PromotionError("RIGHTS_OR_SOURCE_BINDING_DRIFT", "CandidateReviewEnvelope authority binding drift")
        if card.get("source_anchor", {}).get("paper_id") != manifest.get("paper_id"):
            raise PromotionError("CARD_ANCHOR_INVALID", "Card paper_id mismatch")
        return sorted(observed.items())

    @staticmethod
    def _validate_no_analysis(bundle_dir: Path, values: Iterable[Mapping[str, Any]]) -> None:
        for path in bundle_dir.rglob("*"):
            if not path.is_file():
                continue
            normalized_name = _normalize_token(path.name)
            if any(_normalize_token(token) in normalized_name for token in _ANALYSIS_KEYS):
                raise PromotionError("ANALYSIS_PAYLOAD_FORBIDDEN", path.name)
            if path.suffix.casefold() == ".json":
                value = read_json(path)
                bad_types = list(_walk_type_tokens(value))
                if bad_types:
                    raise PromotionError("ANALYSIS_PAYLOAD_FORBIDDEN", bad_types[0])
            elif path.suffix.casefold() == ".jsonl":
                for item in _read_chunks(path):
                    bad_types = list(_walk_type_tokens(item))
                    if bad_types:
                        raise PromotionError("ANALYSIS_PAYLOAD_FORBIDDEN", bad_types[0])
        for value in values:
            bad = [key for key in _walk_keys(value) if key.casefold() in _ANALYSIS_KEYS]
            bad_types = list(_walk_type_tokens(value))
            if bad or bad_types:
                raise PromotionError("ANALYSIS_PAYLOAD_FORBIDDEN", (bad or bad_types)[0])

    def _validate_card(
        self, bundle_dir: Path, manifest: Mapping[str, Any], envelope: Mapping[str, Any]
    ) -> dict[str, Any]:
        card_path = bundle_dir / str(manifest["artifacts"]["card"]["relative_path"])
        chunks_path = bundle_dir / str(manifest["artifacts"]["chunks"]["relative_path"])
        card = _read_card_frontmatter(card_path)
        required = {
            "title",
            "research_question",
            "research_object",
            "method",
            "key_results",
            "author_conclusion",
            "boundary_conditions",
            "source_anchor",
            "key_data",
            "field_credibility",
            "completion_status",
            "omissions",
            "review_status",
            "is_derived",
            "data_ownership",
            "schema_version",
        }
        if not required.issubset(card) or card.get("schema_version") != 6:
            raise PromotionError("CARD_SCHEMA_OR_COMPLETION_INVALID", "Card v6 required keys")
        if (
            card.get("completion_status") != "pending_review"
            or card.get("omissions") != []
            or card.get("review_status") != "pending"
            or card.get("is_derived") is not False
            or card.get("data_ownership") != "self"
            or not isinstance(card.get("title"), str)
            or not card["title"].strip()
        ):
            raise PromotionError("CARD_SCHEMA_OR_COMPLETION_INVALID", "completion/status invariant")
        chunks = _read_chunks(chunks_path)
        view = build_atomic_review_view(card, chunks)
        precheck = deterministic_precheck(card, chunks, view)
        if not precheck.get("ok"):
            raise PromotionError("CARD_ANCHOR_INVALID", json.dumps(precheck.get("issues", [])[:2]))
        if envelope.get("deterministic_checks", {}).get("mechanical", {}).get("ok") is not True:
            raise PromotionError("CARD_ANCHOR_INVALID", "Retrieval mechanical evidence is not PASS")
        if envelope.get("deterministic_checks", {}).get("atomic_precheck") != precheck:
            raise PromotionError("CARD_ANCHOR_INVALID", "Retrieval atomic precheck differs from deterministic replay")
        return {"card": card, "atomic_precheck": precheck, "card_path": card_path, "chunks_path": chunks_path}

    def preflight(
        self,
        *,
        bundle_dir: Path | str,
        authority_root: Path | str,
        approval: Mapping[str, Any] | None,
        prestate: Mapping[str, Any],
        target_root: Path | str,
        target_namespace: str,
        injection: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        injection = dict(injection or {})
        target = self._validate_target(Path(target_root), target_namespace)
        if injection.get("target_unwritable") or injection.get("cross_volume"):
            raise PromotionError("TARGET_NOT_WRITABLE_OR_CROSS_VOLUME", "test injection")
        bundle = self._validate_read_input(Path(bundle_dir), label="candidate bundle")
        authority = self._validate_read_input(Path(authority_root), label="authority root")
        manifest, verified = self._validate_bundle_contract(bundle)
        prestate_value = copy.deepcopy(dict(prestate))
        verify_content_hash(prestate_value, code="TARGET_PRESTATE_DRIFT")
        self._validate_prestate(target, target_namespace, prestate_value)
        prestate_hash = prestate_value["content_hash"]
        approval_value = self._validate_approval(
            approval,
            manifest=manifest,
            target_namespace=target_namespace,
            prestate_hash=prestate_hash,
        )
        envelope = read_json(bundle / str(manifest["artifacts"]["envelope"]["relative_path"]))
        required_states = {
            "processing_status": "REVIEW_READY",
            "decision_status": "APPROVED_FOR_CARD_STAGING",
            "review_status": "pending",
            "completion_status": "pending_review",
            "promotion_status": "NOT_ELIGIBLE",
            "semantic_verification": "NOT_PERFORMED",
        }
        if any(envelope.get(key) != expected for key, expected in required_states.items()):
            raise PromotionError("CANDIDATE_STATE_NOT_READY", "CandidateReviewEnvelope state mismatch")
        card_info = self._validate_card(bundle, manifest, envelope)
        authority_hashes = self._validate_authority(
            authority,
            manifest,
            envelope,
            card_info["card"],
        )
        self._validate_no_analysis(bundle, (manifest, envelope, card_info["card"]))
        identifiers = prestate_value.get("identifier_registry", {}).get("bindings", {})
        if identifiers:
            raise PromotionError("FORMAL_IDENTIFIER_CONFLICT", "formal identifier already bound")
        if (
            manifest["paper_id"] in prestate_value.get("knowledge_admission", {}).get("states", {})
            or manifest["paper_id"] in prestate_value.get("formal_index", {}).get("entries", {})
        ):
            raise PromotionError("FORMAL_IDENTIFIER_CONFLICT", "paper_id already exists in formal target state")
        expected_folder = target / "formal_library" / folder_name(
            manifest["paper_id"], display_title(card_info["card"]["title"])
        )
        registry = PaperIdRegistry(path=target / "paper_id_registry.yaml")
        try:
            registry.ensure(manifest["paper_id"], expected_folder)
        except Exception as exc:
            raise PromotionError("PAPER_ID_CONFLICT", str(exc)) from exc
        logical_binding = prestate_value.get("paper_id_registry", {}).get("bindings", {}).get(
            manifest["paper_id"]
        )
        if logical_binding and Path(str(logical_binding)) != expected_folder:
            raise PromotionError("PAPER_ID_CONFLICT", "prestate paper_id binding differs")
        target_hash = effective_target_hash(target)
        write_set = [
            f"formal_library/{expected_folder.name}",
            "knowledge_admission/states.json",
            "knowledge_admission/events.jsonl",
            "artifact_registry/registry",
            "formal_index/active.json",
            f"receipts/{approval_value['promotion_id']}.json",
        ]
        checks = [
            ("PF-01", "bundle manifest/member/seal/canonical hash"),
            ("PF-02", "identity VERIFIED"),
            ("PF-03", "rights/source exact binding"),
            ("PF-04", "first decision approved for Card staging"),
            ("PF-05", "Candidate Card REVIEW_READY"),
            ("PF-06", "Card Schema v6 completion/omissions"),
            ("PF-07", "atomic anchor precheck"),
            ("PF-08", "Analysis payload absent"),
            ("PF-09", "formal identifier unique"),
            ("PF-10", "paper_id read-only ensure"),
            ("PF-11", "sandbox target/path/protected roots"),
            ("PF-12", "second approval exact binding/time/authority"),
            ("PF-13", "target prestate and heads frozen"),
        ]
        heads = {
            "knowledge_admission": prestate_value.get("knowledge_admission", {}).get("head"),
            "artifact_registry": prestate_value.get("artifact_registry", {}).get("head"),
            "formal_index": prestate_value.get("formal_index", {}).get("head"),
            "promotion_receipts": sorted(prestate_value.get("promotion_receipts", {}).keys()),
        }
        preflight = with_content_hash(
            {
                "schema_version": PREFLIGHT_SCHEMA,
                "preflight_id": f"PREFLIGHT-{approval_value['promotion_id']}",
                "promotion_id": approval_value["promotion_id"],
                "candidate_bundle_hash": verified["bundle_sha256"],
                "approval_receipt_hash": approval_value["content_hash"],
                "target_namespace": target_namespace,
                "paper_id": manifest["paper_id"],
                "checks": [
                    {"check_id": check_id, "status": "PASS", "evidence": evidence}
                    for check_id, evidence in checks
                ],
                "expected_heads": heads,
                "target_prestate_hash": prestate_hash,
                "target_effective_hash": target_hash,
                "write_set": write_set,
                "write_set_hash": canonical_sha256(write_set),
                "status": "PASS",
                "generated_at": self.event_time,
            }
        )
        self._validate_schema(PREFLIGHT_SCHEMA, preflight)
        card_info["authority_hashes"] = authority_hashes
        return preflight, approval_value, manifest, card_info

    def _build_intent(
        self,
        preflight: Mapping[str, Any],
        approval: Mapping[str, Any],
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        intent = with_content_hash(
            {
                "schema_version": INTENT_SCHEMA,
                "promotion_id": approval["promotion_id"],
                "candidate_id": manifest["candidate_id"],
                "card_revision": manifest["card_revision"],
                "paper_id": manifest["paper_id"],
                "candidate_bundle_hash": manifest["bundle_sha256"],
                "approval_receipt_hash": approval["content_hash"],
                "preflight_hash": preflight["content_hash"],
                "target_namespace": approval["target_namespace"],
                "target_prestate_hash": approval["target_prestate_hash"],
                "write_set_hash": preflight["write_set_hash"],
                "expected_heads": copy.deepcopy(preflight["expected_heads"]),
                "writer": PROMOTION_WRITER,
            }
        )
        self._validate_schema(INTENT_SCHEMA, intent)
        return intent

    def _acquire_lock(self, path: Path, payload: Mapping[str, Any]) -> None:
        target = self.boundary.require_write_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise PromotionError("PROMOTION_IN_PROGRESS", str(target)) from exc
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json_bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _release_known_locks(paths: Iterable[Path]) -> None:
        for path in paths:
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def _copy_prepared_files(
        self,
        *,
        bundle_dir: Path,
        approval: Mapping[str, Any],
        manifest: Mapping[str, Any],
        card_info: Mapping[str, Any],
        stage_folder: Path,
    ) -> tuple[Path, list[Path]]:
        title = display_title(card_info["card"]["title"])
        card_name = file_name("Card", manifest["paper_id"], title, "md")
        chunks_name = file_name("Chunks", manifest["paper_id"], title, "jsonl")
        stage_folder.mkdir(parents=True, exist_ok=False)
        card_target = stage_folder / card_name
        chunks_target = stage_folder / chunks_name
        shutil.copy2(card_info["card_path"], card_target)
        shutil.copy2(card_info["chunks_path"], chunks_target)
        if (
            sha256_file(card_target) != manifest["artifacts"]["card"]["sha256"]
            or sha256_file(chunks_target) != manifest["artifacts"]["chunks"]["sha256"]
        ):
            raise PromotionError("PREPARE_WRITESET_MISMATCH", "prepared Card/chunks bytes drift")
        if read_data_id(card_target) != manifest["paper_id"] or read_status(card_target) != "pending":
            raise PromotionError("CARD_SCHEMA_OR_COMPLETION_INVALID", "prepared Card identity/state")
        if not is_legal("pending", "active"):
            raise PromotionError("KNOWLEDGE_ADMISSION_TRANSITION_INVALID", "pending->active")
        write_status(card_target, "active")
        evidence = stage_folder / "promotion_evidence"
        evidence.mkdir()
        review_source = bundle_dir / str(manifest["artifacts"]["envelope"]["relative_path"])
        manifest_source = bundle_dir / "bundle_manifest.json"
        shutil.copy2(review_source, evidence / "candidate_review_envelope.json")
        shutil.copy2(manifest_source, evidence / "source_bundle_manifest.json")
        self._write_json(evidence / "second_promotion_approval.json", approval, exclusive=True)
        if (
            sha256_file(evidence / "candidate_review_envelope.json")
            != manifest["artifacts"]["envelope"]["sha256"]
            or sha256_file(evidence / "source_bundle_manifest.json") != sha256_file(manifest_source)
            or read_json(evidence / "second_promotion_approval.json") != dict(approval)
        ):
            raise PromotionError("PREPARE_WRITESET_MISMATCH", "prepared evidence bytes/content drift")
        files = sorted((item for item in stage_folder.rglob("*") if item.is_file()), key=str)
        if len(files) != 5:
            raise PromotionError("PREPARE_WRITESET_MISMATCH", f"expected 5 files, got {len(files)}")
        return card_target, files

    @staticmethod
    def _formal_file_records(folder: Path, target_root: Path) -> list[dict[str, Any]]:
        return [
            {
                "relative_path": path.relative_to(target_root).as_posix(),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for path in sorted((item for item in folder.rglob("*") if item.is_file()), key=str)
        ]

    def _write_failure_receipt(
        self,
        tx_dir: Path,
        *,
        promotion_id: str,
        intent_hash: str,
        state: str,
        code: str,
        message: str,
        actions: list[str],
        prestate_hash: str,
        observed_failure_state_hash: str,
        post_compensation_state_hash: str,
        effective_projection: Mapping[str, Any],
        remaining_differences: list[str],
        journal_head_hash: str,
        original_error_evidence_hash: str,
        compensation_error_evidence_hash: str | None,
        lock_disposition: str,
    ) -> tuple[Path, bytes]:
        value = with_content_hash(
            {
                "schema_version": "memorive.discovery.analysis.promotion-failure-receipt.v1",
                "receipt_id": f"FAILURE-{promotion_id}",
                "promotion_id": promotion_id,
                "intent_hash": intent_hash,
                "transaction_state": state,
                "error_code": code,
                "error_message": message,
                "compensation_actions": actions,
                "prestate_hash": prestate_hash,
                "observed_failure_state_hash": observed_failure_state_hash,
                "post_compensation_state_hash": post_compensation_state_hash,
                "effective_projection": copy.deepcopy(dict(effective_projection)),
                "remaining_differences": sorted(set(remaining_differences)),
                "journal_head_hash": journal_head_hash,
                "original_error_evidence_hash": original_error_evidence_hash,
                "compensation_error_evidence_hash": compensation_error_evidence_hash,
                "lock_disposition": lock_disposition,
                "formal_reader_visibility": False,
                "automatic_retry": "DENIED",
                "recorded_at": self.event_time,
            }
        )
        self._validate_schema(FAILURE_RECEIPT_SCHEMA, value)
        path = tx_dir / "failure_receipt.json"
        return path, self._write_json(path, value)

    @staticmethod
    def _remaining_differences(prestate_hash: str, poststate_hash: str) -> list[str]:
        return [] if prestate_hash == poststate_hash else ["EFFECTIVE_TARGET_HASH_DIFFERS_FROM_PRESTATE"]

    def _capture_effective_state(
        self, target_root: Path
    ) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
        """Capture the exact effective target bytes before a transaction starts.

        Transaction journals, locks, ledgers, receipts, and quarantine are deliberately
        outside ``TARGET_RESOURCE_DIRS``.  The returned records therefore use the same
        hash domain and ordering as ``effective_target_hash``.
        """
        records = inventory(target_root)
        blobs: dict[str, bytes] = {}
        for record in records:
            relative_path = str(record["relative_path"])
            path = _safe_relative_target(target_root, relative_path)
            data = path.read_bytes()
            if len(data) != record["bytes"] or sha256_bytes(data) != record["sha256"]:
                raise PromotionError(
                    "TARGET_PRESTATE_DRIFT", f"effective target changed during capture: {relative_path}"
                )
            blobs[relative_path] = data
        return records, blobs

    def _restore_effective_state(
        self,
        target_root: Path,
        *,
        expected_records: list[dict[str, Any]],
        expected_blobs: Mapping[str, bytes],
    ) -> None:
        """Restore the exact pre-transaction bytes inside the sandbox target."""
        expected_paths = set(expected_blobs)
        for record in reversed(inventory(target_root)):
            relative_path = str(record["relative_path"])
            if relative_path in expected_paths:
                continue
            path = _safe_relative_target(target_root, relative_path)
            self.boundary.require_write_path(path)
            path.unlink()
        for relative_path, data in expected_blobs.items():
            self._write_bytes(_safe_relative_target(target_root, relative_path), data)
        if inventory(target_root) != expected_records:
            raise PromotionError("COMPENSATION_FAILED", "exact effective prestate restoration drift")

    def _failure_projection(
        self,
        target_root: Path,
        *,
        paper_id: str,
        promotion_id: str,
        artifact_id: str,
    ) -> dict[str, Any]:
        formal_object = any((target_root / "formal_library").glob(f"*{paper_id}*"))
        states_path = target_root / "knowledge_admission" / "states.json"
        states = read_json(states_path).get("states", {}) if states_path.is_file() else {}
        knowledge_admission_active = isinstance(states, dict) and states.get(paper_id) == "active"
        artifact_registry_registration = False
        registry_root = target_root / "artifact_registry" / "registry"
        if registry_root.is_dir():
            try:
                artifact_registry_registration = ArtifactRegistry(registry_root).get(artifact_id).get("artifact_id") == artifact_id
            except Exception:
                artifact_registry_registration = False
        index_path = target_root / "formal_index" / "active.json"
        index = read_json(index_path).get("entries", {}) if index_path.is_file() else {}
        formal_index = isinstance(index, dict) and paper_id in index
        receipt_path = target_root / "receipts" / f"{promotion_id}.json"
        valid_commit_marker = False
        if receipt_path.is_file():
            try:
                receipt = read_json(receipt_path)
                verify_content_hash(receipt, code="RECEIPT_HASH_INVALID")
                valid_commit_marker = receipt.get("transaction_state") == "COMMITTED"
            except Exception:
                valid_commit_marker = False
        visible, _ = self.visibility_probe(target_root, promotion_id)
        return {
            "formal_object": formal_object,
            "knowledge_admission_active": knowledge_admission_active,
            "artifact_registry_registration": artifact_registry_registration,
            "formal_index": formal_index,
            "valid_commit_marker": valid_commit_marker,
            "formal_reader_visibility": visible,
        }

    def _compensate(
        self,
        *,
        target_root: Path,
        tx_dir: Path,
        stage_folder: Path,
        formal_folder: Path,
        expected_prestate_records: list[dict[str, Any]],
        expected_prestate_blobs: Mapping[str, bytes],
        fail_compensation: bool,
    ) -> list[str]:
        if fail_compensation:
            raise PromotionError("COMPENSATION_FAILED", "injected compensation failure")
        actions: list[str] = []
        quarantine = tx_dir / "quarantine"
        quarantine.mkdir(parents=True, exist_ok=True)
        moved_folder: Path | None = None
        if formal_folder.exists():
            moved_folder = quarantine / formal_folder.name
            if moved_folder.exists():
                raise PromotionError("COMPENSATION_FAILED", "quarantine target exists")
            os.replace(formal_folder, moved_folder)
            actions.append("FORMAL_FOLDER_MOVED_TO_TRANSACTION_QUARANTINE")
        elif stage_folder.exists():
            moved_folder = quarantine / stage_folder.name
            os.replace(stage_folder, moved_folder)
            actions.append("STAGING_MOVED_TO_TRANSACTION_QUARANTINE")
        self._restore_effective_state(
            target_root,
            expected_records=expected_prestate_records,
            expected_blobs=expected_prestate_blobs,
        )
        actions.append("EFFECTIVE_TARGET_EXACT_PRESTATE_RESTORED")
        return actions

    def visibility_probe(self, target_root: Path | str, promotion_id: str) -> tuple[bool, list[str]]:
        root = Path(target_root).resolve(strict=False)
        receipt_path = root / "receipts" / f"{promotion_id}.json"
        if not receipt_path.is_file():
            return False, ["PROMOTION_RECEIPT_MISSING"]
        try:
            receipt = read_json(receipt_path)
            self._validate_target(root, str(receipt.get("target_namespace") or ""))
            self._validate_schema(RECEIPT_SCHEMA, receipt)
            verify_content_hash(receipt, code="RECEIPT_HASH_INVALID")
            if (
                receipt.get("promotion_id") != promotion_id
                or receipt.get("receipt_id") != f"PROMOTION-{promotion_id}"
            ):
                raise PromotionError("RECEIPT_HASH_INVALID", "receipt promotion identity drift")

            journal = root / "transactions" / promotion_id / "journal.jsonl"
            entries = self._journal_entries(
                journal,
                expected_promotion_id=promotion_id,
                expected_intent_hash=receipt["intent_hash"],
            )
            if (
                not entries
                or entries[-1].get("state") != "COMMITTED"
                or entries[-1].get("event") != "ALL_RESOURCES_VERIFIED_RECEIPT_READY"
                or entries[-1].get("evidence", {}).get("receipt_content_hash") != receipt["content_hash"]
            ):
                raise PromotionError("RECEIPT_HASH_INVALID", "committed journal marker drift")

            ledger_path = root / "transactions" / "idempotency_ledger.json"
            ledger = self._load_ledger(ledger_path)
            ledger_record = ledger["promotions"].get(promotion_id)
            raw_receipt = receipt_path.read_bytes()
            if (
                not isinstance(ledger_record, dict)
                or ledger_record.get("state") != "COMMITTED"
                or ledger_record.get("intent_hash") != receipt["intent_hash"]
                or ledger_record.get("candidate_bundle_hash") != receipt["candidate_bundle_hash"]
                or ledger_record.get("receipt_bytes_sha256") != sha256_bytes(raw_receipt)
            ):
                raise PromotionError("RECEIPT_HASH_INVALID", "ledger/receipt binding drift")

            states_path = root / "knowledge_admission" / "states.json"
            states_doc = read_json(states_path)
            states = states_doc.get("states")
            if (
                not isinstance(states, dict)
                or states.get(receipt["paper_id"]) != "active"
                or states_doc.get("head") != canonical_sha256(states)
                or receipt["knowledge_admission"]["head_hash"] != states_doc["head"]
            ):
                raise PromotionError("RECEIPT_HASH_INVALID", "KNOWLEDGE_ADMISSION state/head drift")
            events_path = root / "knowledge_admission" / "events.jsonl"
            knowledge_admission_events = [
                json.loads(line)
                for line in events_path.read_text(encoding="utf-8").splitlines()
                if line
            ]
            matching_knowledge_admission = []
            for event in knowledge_admission_events:
                verify_content_hash(event, code="RECEIPT_HASH_INVALID")
                if event.get("event_ref") == receipt["knowledge_admission"]["event_ref"]:
                    matching_knowledge_admission.append(event)
            if len(matching_knowledge_admission) != 1 or any(
                matching_knowledge_admission[0].get(key) != value
                for key, value in {
                    "paper_id": receipt["paper_id"],
                    "from_status": receipt["knowledge_admission"]["from_status"],
                    "to_status": receipt["knowledge_admission"]["to_status"],
                }.items()
            ):
                raise PromotionError("RECEIPT_HASH_INVALID", "KNOWLEDGE_ADMISSION event binding drift")

            registry = ArtifactRegistry(root / "artifact_registry" / "registry")
            envelope = registry.get(receipt["artifact_id"])
            if (
                envelope.get("artifact_id") != receipt["artifact_id"]
                or Path(envelope["locator"]["path"]).resolve(strict=False)
                != Path(receipt["artifact_registry"]["locator"]).resolve(strict=False)
                or envelope.get("source_scope", {}).get("paper_ids") != [receipt["paper_id"]]
                or envelope.get("metadata", {}).get("promotion_id") != promotion_id
            ):
                raise PromotionError("RECEIPT_HASH_INVALID", "ARTIFACT_REGISTRY envelope/locator/lineage drift")
            artifact_registry_events: list[dict[str, Any]] = []
            for path in sorted((root / "artifact_registry" / "registry").glob("*.jsonl")):
                artifact_registry_events.extend(
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line
                )
            artifact_registry_head = canonical_sha256(artifact_registry_events)
            state_events = [
                item
                for item in artifact_registry_events
                if item.get("event_type") == "state_transition_observed"
                and item.get("payload", {}).get("artifact_id") == receipt["artifact_id"]
                and item.get("payload", {}).get("event_ref") == receipt["knowledge_admission"]["event_ref"]
                and item.get("payload", {}).get("from_status") == "pending"
                and item.get("payload", {}).get("to_status") == "active"
            ]
            if receipt["artifact_registry"]["head_hash"] != artifact_registry_head or len(state_events) != 1:
                raise PromotionError("RECEIPT_HASH_INVALID", "ARTIFACT_REGISTRY head/state observation drift")

            index_path = root / "formal_index" / "active.json"
            index = read_json(index_path)
            entries_map = index.get("entries")
            if not isinstance(entries_map, dict) or index.get("head") != canonical_sha256(entries_map):
                raise PromotionError("RECEIPT_HASH_INVALID", "formal index head drift")
            index_entry = entries_map.get(receipt["paper_id"])
            if (
                not isinstance(index_entry, dict)
                or index_entry.get("artifact_id") != receipt["artifact_id"]
                or index_entry.get("promotion_id") != promotion_id
                or Path(str(index_entry.get("locator"))).resolve(strict=False)
                != Path(receipt["artifact_registry"]["locator"]).resolve(strict=False)
                or receipt["formal_index"]["entry_key"] != receipt["paper_id"]
                or receipt["formal_index"]["head_hash"] != index["head"]
            ):
                raise PromotionError("RECEIPT_HASH_INVALID", "formal index binding drift")

            relative_paths = [record["relative_path"] for record in receipt["formal_files"]]
            if len(relative_paths) != len(set(relative_paths)):
                raise PromotionError("RECEIPT_HASH_INVALID", "duplicate formal file descriptors")
            card_record: dict[str, Any] | None = None
            for record in receipt["formal_files"]:
                path = _safe_relative_target(root, record["relative_path"])
                if (
                    not path.is_file()
                    or sha256_file(path) != record["sha256"]
                    or path.stat().st_size != record["bytes"]
                ):
                    raise PromotionError("RECEIPT_HASH_INVALID", f"formal file drift: {record['relative_path']}")
                if path.suffix.casefold() == ".md" and path.name.startswith("[Card]"):
                    card_record = record
            if card_record is None or envelope.get("content_hash", {}).get("value") != card_record["sha256"].lower():
                raise PromotionError("RECEIPT_HASH_INVALID", "ARTIFACT_REGISTRY content hash/Card binding drift")
            if index_entry.get("content_hash") != card_record["sha256"]:
                raise PromotionError("RECEIPT_HASH_INVALID", "formal index content hash drift")
            if effective_target_hash(root) != receipt["poststate_hash"]:
                raise PromotionError("RECEIPT_HASH_INVALID", "poststate hash drift")
        except Exception as exc:
            return False, [f"VISIBILITY_INVALID:{exc}"]
        return True, []

    def _early_existing_promotion(
        self,
        *,
        bundle_dir: Path | str,
        approval: Mapping[str, Any] | None,
        target_root: Path | str,
        target_namespace: str,
    ) -> PromotionResult | None:
        """Resolve a previously bound promotion before live-target preflight.

        A committed target necessarily differs from its approved prestate.  Rebuilding a
        fresh preflight first would therefore change preflight_hash and falsely turn an
        exact replay into PROMOTION_ID_CONFLICT.  The immutable original intent is the
        authority for replay comparison; only a byte-valid original receipt may return.
        """
        if not isinstance(approval, Mapping) or not isinstance(approval.get("promotion_id"), str):
            return None
        target = self._validate_target(Path(target_root), target_namespace)
        promotion_id = str(approval["promotion_id"])
        result = PromotionResult(
            outcome="BLOCKED",
            transaction_state="PRECHECK_BLOCKED",
            promotion_id=promotion_id,
        )
        ledger_path = target / "transactions" / "idempotency_ledger.json"
        try:
            ledger = self._load_ledger(ledger_path)
        except PromotionError as exc:
            result.error_code = exc.code
            result.error_message = exc.message
            return result
        existing = ledger.get("promotions", {}).get(promotion_id)
        if not existing:
            if ledger.get("promotions"):
                try:
                    bundle = self._validate_read_input(Path(bundle_dir), label="candidate bundle")
                    manifest, _ = self._validate_bundle_contract(bundle)
                except PromotionError:
                    return None
                for other_id, other in ledger["promotions"].items():
                    if (
                        other_id != promotion_id
                        and other.get("state") == "COMMITTED"
                        and other.get("candidate_bundle_hash") == manifest["bundle_sha256"]
                    ):
                        return PromotionResult(
                            outcome="BLOCKED",
                            transaction_state="PRECHECK_BLOCKED",
                            error_code="BUNDLE_ALREADY_PROMOTED",
                            error_message=f"bundle committed by {other_id}",
                            promotion_id=promotion_id,
                        )
            return None
        result.transaction_state = str(existing.get("state") or "PRECHECK_BLOCKED")
        intent_path = target / "transactions" / promotion_id / "intent.json"
        if not intent_path.is_file():
            result.error_code = "UNFINISHED_JOURNAL_BLOCKED"
            result.error_message = "bound promotion is missing immutable original intent"
            return result
        original_intent = read_json(intent_path)
        result.intent_hash = original_intent.get("content_hash")
        try:
            self._validate_schema(INTENT_SCHEMA, original_intent)
            verify_content_hash(original_intent, code="UNFINISHED_JOURNAL_BLOCKED")
            if existing.get("intent_hash") != original_intent.get("content_hash"):
                raise PromotionError("UNFINISHED_JOURNAL_BLOCKED", "ledger/original intent hash drift")
            self._validate_schema(APPROVAL_SCHEMA, approval)
            verify_content_hash(approval, code="PROMOTION_ID_CONFLICT")
            bundle = self._validate_read_input(Path(bundle_dir), label="candidate bundle")
            manifest, _ = self._validate_bundle_contract(bundle)
        except Exception as exc:
            result.error_code = (
                exc.code
                if isinstance(exc, PromotionError) and exc.code == "UNFINISHED_JOURNAL_BLOCKED"
                else "PROMOTION_ID_CONFLICT"
            )
            result.error_message = f"bound replay input is invalid: {exc}"
            result.transaction_state = "PRECHECK_BLOCKED"
            return result
        exact_bindings = {
            "promotion_id": approval.get("promotion_id"),
            "candidate_id": approval.get("candidate_id"),
            "card_revision": approval.get("card_revision"),
            "paper_id": approval.get("paper_id"),
            "candidate_bundle_hash": approval.get("candidate_bundle_hash"),
            "approval_receipt_hash": approval.get("content_hash"),
            "target_namespace": target_namespace,
            "target_prestate_hash": approval.get("target_prestate_hash"),
        }
        manifest_bindings = {
            "candidate_id": manifest.get("candidate_id"),
            "card_revision": manifest.get("card_revision"),
            "paper_id": manifest.get("paper_id"),
            "candidate_bundle_hash": manifest.get("bundle_sha256"),
        }
        mismatch = [
            key
            for key, observed in exact_bindings.items()
            if original_intent.get(key) != observed
        ]
        mismatch.extend(
            f"manifest.{key}"
            for key, observed in manifest_bindings.items()
            if original_intent.get(key) != observed
        )
        if mismatch:
            result.error_code = "PROMOTION_ID_CONFLICT"
            result.error_message = "promotion_id is already bound; fields=" + ",".join(mismatch)
            result.transaction_state = "PRECHECK_BLOCKED"
            return result
        if existing.get("state") != "COMMITTED":
            result.error_code = "FAILED_PROMOTION_ID_REUSE_DENIED"
            result.error_message = f"promotion_id state={existing.get('state')} is not retryable"
            failure_path_value = existing.get("failure_receipt_path")
            if not isinstance(failure_path_value, str):
                result.error_code = "UNFINISHED_JOURNAL_BLOCKED"
                result.error_message = "failed promotion is missing original failure receipt"
                result.transaction_state = "PRECHECK_BLOCKED"
                return result
            failure_path = Path(failure_path_value).resolve(strict=False)
            expected_failure_root = (target / "transactions" / promotion_id).resolve(strict=False)
            if not _contains(expected_failure_root, failure_path) or not failure_path.is_file():
                result.error_code = "UNFINISHED_JOURNAL_BLOCKED"
                result.error_message = "failure receipt path is missing or escapes transaction"
                result.transaction_state = "PRECHECK_BLOCKED"
                return result
            failure_raw = failure_path.read_bytes()
            try:
                failure_receipt = read_json(failure_path)
                self._validate_schema(FAILURE_RECEIPT_SCHEMA, failure_receipt)
                verify_content_hash(failure_receipt, code="UNFINISHED_JOURNAL_BLOCKED")
            except Exception as exc:
                result.error_code = "UNFINISHED_JOURNAL_BLOCKED"
                result.error_message = f"failure receipt invalid: {exc}"
                result.transaction_state = "PRECHECK_BLOCKED"
                return result
            if (
                sha256_bytes(failure_raw) != existing.get("failure_receipt_bytes_sha256")
                or failure_receipt.get("promotion_id") != promotion_id
                or failure_receipt.get("intent_hash") != original_intent.get("content_hash")
                or failure_receipt.get("transaction_state") != existing.get("state")
            ):
                result.error_code = "UNFINISHED_JOURNAL_BLOCKED"
                result.error_message = "failure receipt binding drift"
                result.transaction_state = "PRECHECK_BLOCKED"
                return result
            result.receipt_path = str(failure_path)
            result.receipt_bytes = failure_raw
            result.idempotent_replay = True
            return result
        receipt_path = target / "receipts" / f"{promotion_id}.json"
        if not receipt_path.is_file():
            result.error_code = "UNFINISHED_JOURNAL_BLOCKED"
            result.error_message = "committed ledger receipt is missing"
            result.transaction_state = "PRECHECK_BLOCKED"
            return result
        raw = receipt_path.read_bytes()
        if sha256_bytes(raw) != existing.get("receipt_bytes_sha256"):
            result.error_code = "UNFINISHED_JOURNAL_BLOCKED"
            result.error_message = "committed receipt bytes drifted"
            result.transaction_state = "PRECHECK_BLOCKED"
            return result
        visible, reasons = self.visibility_probe(target, promotion_id)
        if not visible:
            result.error_code = "UNFINISHED_JOURNAL_BLOCKED"
            result.error_message = ";".join(reasons)
            result.transaction_state = "PRECHECK_BLOCKED"
            result.visibility_reasons = reasons
            return result
        result.outcome = "COMMITTED"
        result.transaction_state = "COMMITTED"
        result.receipt_path = str(receipt_path)
        result.receipt_bytes = raw
        result.idempotent_replay = True
        result.visibility = visible
        result.visibility_reasons = reasons
        return result

    def promote(
        self,
        *,
        bundle_dir: Path | str,
        authority_root: Path | str,
        approval: Mapping[str, Any] | None,
        prestate: Mapping[str, Any],
        target_root: Path | str,
        target_namespace: str,
        injection: Mapping[str, Any] | None = None,
    ) -> PromotionResult:
        injection = dict(injection or {})
        result = PromotionResult(outcome="BLOCKED", transaction_state="PRECHECK_BLOCKED")
        try:
            existing_result = self._early_existing_promotion(
                bundle_dir=bundle_dir,
                approval=approval,
                target_root=target_root,
                target_namespace=target_namespace,
            )
        except PromotionError as exc:
            result.error_code = exc.code
            result.error_message = exc.message
            result.promotion_id = approval.get("promotion_id") if isinstance(approval, Mapping) else None
            return result
        if existing_result is not None:
            return existing_result
        try:
            preflight, approval_value, manifest, card_info = self.preflight(
                bundle_dir=bundle_dir,
                authority_root=authority_root,
                approval=approval,
                prestate=prestate,
                target_root=target_root,
                target_namespace=target_namespace,
                injection=injection,
            )
            intent = self._build_intent(preflight, approval_value, manifest)
        except PromotionError as exc:
            result.error_code = exc.code
            result.error_message = exc.message
            result.promotion_id = approval.get("promotion_id") if isinstance(approval, Mapping) else None
            return result

        target = Path(target_root).resolve(strict=False)
        promotion_id = intent["promotion_id"]
        paper_id = intent["paper_id"]
        intent_hash = intent["content_hash"]
        result.promotion_id = promotion_id
        result.intent_hash = intent_hash
        transactions = target / "transactions"
        locks = target / "locks"
        ledger_path = transactions / "idempotency_ledger.json"
        tx_dir = transactions / promotion_id
        journal_path = tx_dir / "journal.jsonl"
        receipt_path = target / "receipts" / f"{promotion_id}.json"
        ledger = self._load_ledger(ledger_path)
        existing = ledger.get("promotions", {}).get(promotion_id)
        if existing:
            if existing.get("intent_hash") != intent_hash:
                result.error_code = "PROMOTION_ID_CONFLICT"
                result.error_message = "promotion_id is already bound to another intent"
                return result
            if existing.get("state") == "COMMITTED" and receipt_path.is_file():
                raw = receipt_path.read_bytes()
                if sha256_bytes(raw) != existing.get("receipt_bytes_sha256"):
                    result.error_code = "UNFINISHED_JOURNAL_BLOCKED"
                    result.error_message = "committed ledger receipt is missing or drifted"
                    return result
                visible, reasons = self.visibility_probe(target, promotion_id)
                result.outcome = "COMMITTED"
                result.transaction_state = "COMMITTED"
                result.receipt_path = str(receipt_path)
                result.receipt_bytes = raw
                result.idempotent_replay = True
                result.visibility = visible
                result.visibility_reasons = reasons
                return result
            result.error_code = "FAILED_PROMOTION_ID_REUSE_DENIED"
            result.error_message = f"promotion_id terminal/nonterminal state={existing.get('state')}"
            result.transaction_state = str(existing.get("state") or "PRECHECK_BLOCKED")
            return result
        for other_id, other in ledger.get("promotions", {}).items():
            if (
                other_id != promotion_id
                and other.get("state") == "COMMITTED"
                and other.get("candidate_bundle_hash") == intent["candidate_bundle_hash"]
            ):
                result.error_code = "BUNDLE_ALREADY_PROMOTED"
                result.error_message = f"bundle committed by {other_id}"
                return result
        try:
            unfinished = self._journal_entries(
                journal_path,
                expected_promotion_id=promotion_id,
                expected_intent_hash=intent_hash,
            )
        except PromotionError as exc:
            result.error_code = "UNFINISHED_JOURNAL_BLOCKED"
            result.error_message = exc.message
            try:
                raw_entries = [
                    json.loads(line)
                    for line in journal_path.read_text(encoding="utf-8").splitlines()
                    if line
                ]
                reported_state = raw_entries[-1].get("state") if raw_entries else None
            except Exception:
                reported_state = None
            result.transaction_state = (
                str(reported_state)
                if reported_state in {"PRECHECKED", "PREPARED", "COMMITTING"}
                else "PRECHECK_BLOCKED"
            )
            return result
        if unfinished:
            result.error_code = "UNFINISHED_JOURNAL_BLOCKED"
            result.error_message = f"orphan journal last_state={unfinished[-1].get('state')}"
            result.transaction_state = str(unfinished[-1].get("state"))
            return result
        if tx_dir.exists():
            result.error_code = "UNFINISHED_JOURNAL_BLOCKED"
            result.error_message = "unknown pre-existing transaction directory"
            return result

        acquired: list[Path] = []
        promotion_lock = locks / f"promotion-{hashlib.sha256(promotion_id.encode()).hexdigest()}.lock"
        paper_lock = locks / f"paper-{hashlib.sha256(paper_id.encode()).hexdigest()}.lock"
        try:
            self._acquire_lock(
                promotion_lock,
                {"promotion_id": promotion_id, "intent_hash": intent_hash, "acquired_at": self.event_time},
            )
            acquired.append(promotion_lock)
            self._acquire_lock(
                paper_lock,
                {"paper_id": paper_id, "promotion_id": promotion_id, "acquired_at": self.event_time},
            )
            acquired.append(paper_lock)
        except PromotionError as exc:
            self._release_known_locks(acquired)
            result.error_code = exc.code
            result.error_message = exc.message
            return result
        if injection.get("hold_after_lock_seconds"):
            time.sleep(float(injection["hold_after_lock_seconds"]))
        if injection.get("drift_after_lock"):
            drift = target / "formal_library" / "__concurrent_prestate_drift__.txt"
            self._write_bytes(drift, b"test-only concurrent drift\n", exclusive=True)
        if effective_target_hash(target) != preflight["target_effective_hash"]:
            self._release_known_locks(acquired)
            result.error_code = "TARGET_PRESTATE_DRIFT"
            result.error_message = "effective target changed after lock acquisition"
            return result
        try:
            self._validate_prestate(target, target_namespace, prestate)
        except PromotionError as exc:
            self._release_known_locks(acquired)
            result.error_code = exc.code
            result.error_message = exc.message
            return result

        effective_prestate_records, effective_prestate_blobs = self._capture_effective_state(target)
        effective_prestate_hash = canonical_sha256(effective_prestate_records)
        if effective_prestate_hash != preflight["target_effective_hash"]:
            self._release_known_locks(acquired)
            result.error_code = "TARGET_PRESTATE_DRIFT"
            result.error_message = "captured effective target differs from frozen preflight"
            return result

        artifact_id = "art_" + hashlib.sha256(promotion_id.encode("utf-8")).hexdigest()[:32]
        title = display_title(card_info["card"]["title"])
        final_folder = target / "formal_library" / folder_name(paper_id, title)
        stage_folder = tx_dir / "staging" / final_folder.name
        flags = {"knowledge_admission": False, "artifact_registry": False, "index": False}
        fault_point = injection.get("fault_point")
        try:
            tx_dir.mkdir(parents=True, exist_ok=True)
            self._append_journal(
                journal_path,
                promotion_id=promotion_id,
                intent_hash=intent_hash,
                state="PRECHECKED",
                event="PREFLIGHT_AND_DOUBLE_LOCK_PASS",
                evidence={"preflight_hash": preflight["content_hash"]},
            )
            self._write_json(tx_dir / "preflight.json", preflight, exclusive=True)
            self._write_json(tx_dir / "intent.json", intent, exclusive=True)
            self._copy_prepared_files(
                bundle_dir=Path(bundle_dir),
                approval=approval_value,
                manifest=manifest,
                card_info=card_info,
                stage_folder=stage_folder,
            )
            prepared_manifest, _ = self._validate_bundle_contract(
                self._validate_read_input(Path(bundle_dir), label="candidate bundle")
            )
            if prepared_manifest["bundle_sha256"] != intent["candidate_bundle_hash"]:
                raise PromotionError("BUNDLE_HASH_OR_MEMBER_DRIFT", "bundle drifted during prepare")
            self._validate_prestate(target, target_namespace, prestate)
            prepared_records = self._formal_file_records(stage_folder, tx_dir / "staging")
            self._append_journal(
                journal_path,
                promotion_id=promotion_id,
                intent_hash=intent_hash,
                state="PREPARED",
                event="WRITESET_PREPARED_AND_VERIFIED",
                evidence={"prepared_files": prepared_records},
            )
            if fault_point == "PREPARE":
                raise InjectedFault("PREPARE")
            self._append_journal(
                journal_path,
                promotion_id=promotion_id,
                intent_hash=intent_hash,
                state="COMMITTING",
                event="COMMIT_SEQUENCE_STARTED",
                evidence={"order": ["files", "knowledge_admission", "artifact_registry", "index", "receipt"]},
            )
            final_folder.parent.mkdir(parents=True, exist_ok=True)
            if final_folder.exists():
                raise PromotionError("FORMAL_IDENTIFIER_CONFLICT", str(final_folder))
            os.replace(stage_folder, final_folder)
            result.write_counts["formal_file_writes"] = len(
                [item for item in final_folder.rglob("*") if item.is_file()]
            )
            if fault_point in {"AFTER_FILE_PUBLISH", "COMPENSATION"}:
                raise InjectedFault(str(fault_point))
            if fault_point == "KNOWLEDGE_ADMISSION_APPEND":
                raise InjectedFault("KNOWLEDGE_ADMISSION_APPEND")
            knowledge_admission_root = target / "knowledge_admission"
            knowledge_admission_root.mkdir(parents=True, exist_ok=True)
            knowledge_admission_event_ref = f"knowledge_admission://{promotion_id}/active"
            states_path = knowledge_admission_root / "states.json"
            states = read_json(states_path) if states_path.is_file() else {"states": {}}
            if not isinstance(states.get("states"), dict) or paper_id in states["states"]:
                raise PromotionError("FORMAL_IDENTIFIER_CONFLICT", "KNOWLEDGE_ADMISSION paper state already exists")
            states["states"] = copy.deepcopy(states["states"])
            states["states"][paper_id] = "active"
            states["head"] = canonical_sha256(states["states"])
            self._write_json(states_path, states)
            knowledge_admission_event = with_content_hash(
                {
                    "schema_version": "memorive.discovery.analysis.knowledge_admission-mirror-event.v1",
                    "event_ref": knowledge_admission_event_ref,
                    "paper_id": paper_id,
                    "from_status": "pending",
                    "to_status": "active",
                    "reason": "SECOND_PROMOTION_APPROVAL_AND_ATOMIC_COMMIT",
                    "recorded_at": self.event_time,
                }
            )
            with (knowledge_admission_root / "events.jsonl").open("ab") as stream:
                stream.write(canonical_bytes(knowledge_admission_event) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            flags["knowledge_admission"] = True
            result.write_counts["knowledge_admission_appends"] = 1
            card_path = next(final_folder.glob("[[]Card[]]*.md"))
            registry = ArtifactRegistry(
                target / "artifact_registry" / "registry",
                corruption_evidence_root=target / "artifact_registry" / "corruption-evidence",
                clock=lambda: self.event_time,
            )
            envelope = build_envelope(
                artifact_id=artifact_id,
                artifact_type="MEMORIVE_FORMAL_CARD",
                content_hash=sha256_file(card_path).lower(),
                locator_path=card_path,
                registered_at=self.event_time,
                schema_ref="repo://contracts/card_schema.md@v6",
                run_ref=f"analysis://{promotion_id}",
                route_snapshot_ref=None,
                paper_ids=[paper_id],
                source_artifact_ids=[],
                created_at=self.event_time,
                parent_artifacts=[],
                unresolved_parent_requirements=[],
                conflicting_candidates=[],
                ledger_record_refs=[],
                legacy_import=False,
                evidence_refs=[
                    f"retrieval-bundle-sha256:{intent['candidate_bundle_hash']}",
                    f"analysis-approval-sha256:{intent['approval_receipt_hash']}",
                ],
                metadata={
                    "promotion_id": promotion_id,
                    "candidate_id": intent["candidate_id"],
                    "card_revision": intent["card_revision"],
                    "synthetic_fixture": True,
                },
            )
            registration_result = registry.register(envelope)
            flags["artifact_registry"] = True
            result.write_counts["artifact_registry_appends"] = 1
            if fault_point == "ARTIFACT_REGISTRY_APPEND":
                raise InjectedFault("ARTIFACT_REGISTRY_APPEND")
            registry.observe_knowledge_admission_state_transition(
                artifact_id,
                event_ref=knowledge_admission_event_ref,
                from_status="pending",
                to_status="active",
            )
            result.write_counts["artifact_registry_appends"] = 2
            if fault_point == "INDEX_PUBLISH":
                raise InjectedFault("INDEX_PUBLISH")
            index_path = target / "formal_index" / "active.json"
            index = read_json(index_path) if index_path.is_file() else {
                "schema_version": "memorive.discovery.analysis.formal-index-mirror.v1",
                "entries": {},
                "head": None,
            }
            if (
                index.get("schema_version") != "memorive.discovery.analysis.formal-index-mirror.v1"
                or not isinstance(index.get("entries"), dict)
                or index.get("head") != (canonical_sha256(index["entries"]) if index["entries"] else None)
                or paper_id in index["entries"]
            ):
                raise PromotionError("FORMAL_IDENTIFIER_CONFLICT", "formal index prestate is invalid/conflicting")
            index["entries"] = copy.deepcopy(index["entries"])
            index["entries"][paper_id] = {
                "artifact_id": artifact_id,
                "promotion_id": promotion_id,
                "locator": str(card_path),
                "content_hash": sha256_file(card_path),
            }
            index["head"] = canonical_sha256(index["entries"])
            self._write_json(index_path, index)
            flags["index"] = True
            result.write_counts["index_writes"] = 1
            if fault_point == "RECEIPT_PUBLISH":
                raise InjectedFault("RECEIPT_PUBLISH")
            formal_records = self._formal_file_records(final_folder, target)
            poststate_hash = effective_target_hash(target)
            artifact_registry_head = canonical_sha256(
                [
                    json.loads(line)
                    for path in sorted((target / "artifact_registry" / "registry").glob("*.jsonl"))
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line
                ]
            )
            receipt = with_content_hash(
                {
                    "schema_version": RECEIPT_SCHEMA,
                    "receipt_id": f"PROMOTION-{promotion_id}",
                    "promotion_id": promotion_id,
                    "intent_hash": intent_hash,
                    "candidate_bundle_hash": intent["candidate_bundle_hash"],
                    "approval_receipt_hash": intent["approval_receipt_hash"],
                    "target_namespace": intent["target_namespace"],
                    "paper_id": paper_id,
                    "transaction_state": "COMMITTED",
                    "commit_marker_type": "PROMOTION_RECEIPT_LAST_VISIBILITY_MARKER",
                    "committed_at": self.event_time,
                    "artifact_id": artifact_id,
                    "formal_files": formal_records,
                    "knowledge_admission": {
                        "from_status": "pending",
                        "to_status": "active",
                        "event_ref": knowledge_admission_event_ref,
                        "head_hash": states["head"],
                    },
                    "artifact_registry": {
                        "artifact_id": artifact_id,
                        "registration_result": registration_result,
                        "head_hash": artifact_registry_head,
                        "locator": str(card_path),
                    },
                    "formal_index": {"entry_key": paper_id, "head_hash": index["head"]},
                    "prestate_hash": intent["target_prestate_hash"],
                    "poststate_hash": poststate_hash,
                    "write_counts": {
                        **result.write_counts,
                        "receipt_writes": 1,
                    },
                    "compensation": {"required": False, "actions": []},
                    "external_effects": copy.deepcopy(result.external_effects),
                    "production_effects": copy.deepcopy(result.production_effects),
                    "visibility_contract": {
                        "expected": "VISIBLE",
                        "conditions": [
                            "valid_receipt_hash",
                            "transaction_committed",
                            "knowledge_admission_active",
                            "artifact_registry_locator_and_lineage_match",
                            "formal_index_match",
                            "formal_file_bytes_match",
                        ],
                    },
                }
            )
            self._validate_schema(RECEIPT_SCHEMA, receipt)
            receipt_bytes = _json_bytes(receipt)
            self._append_journal(
                journal_path,
                promotion_id=promotion_id,
                intent_hash=intent_hash,
                state="COMMITTED",
                event="ALL_RESOURCES_VERIFIED_RECEIPT_READY",
                evidence={"receipt_content_hash": receipt["content_hash"]},
            )
            ledger.setdefault("promotions", {})[promotion_id] = {
                "intent_hash": intent_hash,
                "candidate_bundle_hash": intent["candidate_bundle_hash"],
                "state": "COMMITTED",
                "receipt_path": str(receipt_path),
                "receipt_bytes_sha256": sha256_bytes(receipt_bytes),
            }
            self._write_json(ledger_path, ledger)
            self._write_bytes(receipt_path, receipt_bytes, exclusive=True)
            result.write_counts["receipt_writes"] = 1
            visible, reasons = self.visibility_probe(target, promotion_id)
            if not visible:
                raise PromotionError("POSTCOMMIT_VISIBILITY_INVALID", ";".join(reasons))
            self._release_known_locks(acquired)
            result.outcome = "COMMITTED"
            result.transaction_state = "COMMITTED"
            result.receipt_path = str(receipt_path)
            result.receipt_bytes = receipt_bytes
            result.visibility = visible
            result.visibility_reasons = reasons
            return result
        except (InjectedFault, PromotionError, Exception) as exc:
            code = f"INJECTED_{exc.fault_point}_FAILURE" if isinstance(exc, InjectedFault) else (
                exc.code if isinstance(exc, PromotionError) else "UNEXPECTED_TRANSACTION_FAILURE"
            )
            message = str(exc)
            observed_failure_state_hash = effective_target_hash(target)
            original_error_evidence_hash = canonical_sha256(
                {"error_code": code, "error_message": message, "intent_hash": intent_hash}
            )
            fail_compensation = fault_point == "COMPENSATION"
            try:
                actions = self._compensate(
                    target_root=target,
                    tx_dir=tx_dir,
                    stage_folder=stage_folder,
                    formal_folder=final_folder,
                    expected_prestate_records=effective_prestate_records,
                    expected_prestate_blobs=effective_prestate_blobs,
                    fail_compensation=fail_compensation,
                )
                terminal = "ROLLED_BACK"
                terminal_entry = self._append_journal(
                    journal_path,
                    promotion_id=promotion_id,
                    intent_hash=intent_hash,
                    state=terminal,
                    event="COMPENSATION_COMPLETED_READER_HIDDEN",
                    evidence={"error_code": code, "actions": actions},
                )
                post_compensation_state_hash = effective_target_hash(target)
                remaining_differences = self._remaining_differences(
                    effective_prestate_hash, post_compensation_state_hash
                )
                if remaining_differences:
                    raise PromotionError(
                        "COMPENSATION_FAILED",
                        "effective target does not match approved prestate after compensation",
                    )
                failure_path, failure_bytes = self._write_failure_receipt(
                    tx_dir,
                    promotion_id=promotion_id,
                    intent_hash=intent_hash,
                    state=terminal,
                    code=code,
                    message=message,
                    actions=actions,
                    prestate_hash=effective_prestate_hash,
                    observed_failure_state_hash=observed_failure_state_hash,
                    post_compensation_state_hash=post_compensation_state_hash,
                    effective_projection=self._failure_projection(
                        target,
                        paper_id=paper_id,
                        promotion_id=promotion_id,
                        artifact_id=artifact_id,
                    ),
                    remaining_differences=remaining_differences,
                    journal_head_hash=terminal_entry["content_hash"],
                    original_error_evidence_hash=original_error_evidence_hash,
                    compensation_error_evidence_hash=None,
                    lock_disposition="RELEASED_AFTER_ROLLBACK",
                )
                ledger.setdefault("promotions", {})[promotion_id] = {
                    "intent_hash": intent_hash,
                    "candidate_bundle_hash": intent["candidate_bundle_hash"],
                    "state": terminal,
                    "failure_receipt_path": str(failure_path),
                    "failure_receipt_bytes_sha256": sha256_bytes(failure_bytes),
                }
                self._write_json(ledger_path, ledger)
                self._release_known_locks(acquired)
                result.receipt_path = str(failure_path)
                result.receipt_bytes = failure_bytes
            except Exception as compensation_exc:
                terminal = "COMPENSATION_REQUIRED"
                actions = ["MANUAL_RECOVERY_REQUIRED", "LOCKS_RETAINED", "READER_VISIBILITY_DENIED"]
                compensation_error_evidence_hash = canonical_sha256(
                    {
                        "type": type(compensation_exc).__name__,
                        "message": str(compensation_exc),
                        "intent_hash": intent_hash,
                    }
                )
                terminal_entry = self._append_journal(
                    journal_path,
                    promotion_id=promotion_id,
                    intent_hash=intent_hash,
                    state=terminal,
                    event="COMPENSATION_FAILED_LOCKS_RETAINED",
                    evidence={"original_error": code, "compensation_error": str(compensation_exc)},
                )
                post_compensation_state_hash = effective_target_hash(target)
                remaining_differences = self._remaining_differences(
                    effective_prestate_hash, post_compensation_state_hash
                )
                if not remaining_differences:
                    remaining_differences = ["MANUAL_RECOVERY_AND_LOCK_RELEASE_REQUIRED"]
                failure_path, failure_bytes = self._write_failure_receipt(
                    tx_dir,
                    promotion_id=promotion_id,
                    intent_hash=intent_hash,
                    state=terminal,
                    code="COMPENSATION_FAILED",
                    message=str(compensation_exc),
                    actions=actions,
                    prestate_hash=effective_prestate_hash,
                    observed_failure_state_hash=observed_failure_state_hash,
                    post_compensation_state_hash=post_compensation_state_hash,
                    effective_projection=self._failure_projection(
                        target,
                        paper_id=paper_id,
                        promotion_id=promotion_id,
                        artifact_id=artifact_id,
                    ),
                    remaining_differences=remaining_differences,
                    journal_head_hash=terminal_entry["content_hash"],
                    original_error_evidence_hash=original_error_evidence_hash,
                    compensation_error_evidence_hash=compensation_error_evidence_hash,
                    lock_disposition="RETAINED_FOR_COMPENSATION",
                )
                ledger.setdefault("promotions", {})[promotion_id] = {
                    "intent_hash": intent_hash,
                    "candidate_bundle_hash": intent["candidate_bundle_hash"],
                    "state": terminal,
                    "failure_receipt_path": str(failure_path),
                    "failure_receipt_bytes_sha256": sha256_bytes(failure_bytes),
                    "locks_retained": [str(path) for path in acquired],
                }
                self._write_json(ledger_path, ledger)
                code = "COMPENSATION_FAILED"
                message = str(compensation_exc)
                result.receipt_path = str(failure_path)
                result.receipt_bytes = failure_bytes
            visible, reasons = self.visibility_probe(target, promotion_id)
            result.outcome = terminal
            result.transaction_state = terminal
            result.error_code = code
            result.error_message = message
            result.visibility = visible
            result.visibility_reasons = reasons
            return result


__all__ = [
    "AtomicCardPromotionCoordinator",
    "PromotionError",
    "PromotionResult",
    "canonical_sha256",
    "effective_target_hash",
    "inventory",
    "with_content_hash",
]
