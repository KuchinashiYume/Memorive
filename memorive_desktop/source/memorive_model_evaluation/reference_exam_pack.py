"""Fail-closed loader for repository-resident MODEL_EVALUATION reference exam packs.

Reference packs are fixed, non-blind regression assets.  The subject-facing
transport may read only members explicitly marked provider-visible; Gold is
loaded later by the local MODEL_EVALUATION scorer.  A public projection, a locator, or a
hash-only record is deliberately insufficient here.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Any, Mapping

from .errors import ModelEvaluationReferenceExamPackError


REFERENCE_PACK_SCHEMA_VERSION = "model_evaluation-reference-exam-pack-manifest-v1"
REFERENCE_PACK_CLASSIFICATION = "REFERENCE_EXAM_PACK"
MANIFEST_NAME = "manifest.reference.json"
CHECKSUM_NAME = "SHA256SUMS.txt"

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ANALYSIS_REFERENCE_PACK = (
    REPO_ROOT
    / "model_evaluation_quality_sentinel"
    / "assets"
    / "analysis_exam"
    / "v1"
    / "reference_pack"
)
DEFAULT_CARD_REVIEWER_REFERENCE_PACK = (
    REPO_ROOT
    / "model_evaluation_quality_sentinel"
    / "assets"
    / "card_reviewer_exam"
    / "v2"
    / "reference_pack"
)
DEFAULT_CARD_DISTILLER_REFERENCE_PACK = (
    REPO_ROOT
    / "model_evaluation_quality_sentinel"
    / "assets"
    / "card_distiller_exam"
    / "v1"
    / "reference_pack"
)

ANALYSIS_REQUIRED_MEMBER_ROLES = MappingProxyType(
    {
        "forms/form_A.json": "FORM_A",
        "forms/form_B.json": "FORM_B",
        "gold/form_A_gold.json": "GOLD_A",
        "gold/form_B_gold.json": "GOLD_B",
        "schemas/analysis_primary_output.schema.json": "OUTPUT_SCHEMA",
        "schemas/analysis_primary_claim_map.schema.json": "CLAIM_MAP_SCHEMA",
        "schemas/analysis_primary_analysis_summary.schema.json": (
            "ANALYSIS_SUMMARY_SCHEMA"
        ),
    }
)

ANALYSIS_ROLE_VISIBILITY = MappingProxyType(
    {
        "FORM_A": (True, True),
        "FORM_B": (True, True),
        "GOLD_A": (False, False),
        "GOLD_B": (False, False),
        "OUTPUT_SCHEMA": (True, True),
        "CLAIM_MAP_SCHEMA": (True, True),
        "ANALYSIS_SUMMARY_SCHEMA": (True, True),
    }
)

CARD_REVIEWER_REQUIRED_MEMBER_ROLES = MappingProxyType(
    {
        "forms/form_A.json": "FORM_A",
        "forms/form_B.json": "FORM_B",
        "gold/form_A_gold.json": "GOLD_A",
        "gold/form_B_gold.json": "GOLD_B",
        "prompts/card_reviewer_prompt.json": "PROMPT",
        "repair/directed_repair_contract.json": "REPAIR_CONTRACT",
        "schemas/card_reviewer_form.schema.json": "FORM_SCHEMA",
        "schemas/card_reviewer_gold.schema.json": "GOLD_SCHEMA",
        "schemas/card_reviewer_output.schema.json": "OUTPUT_SCHEMA",
        "scoring/scoring_protocol.json": "SCORING_PROTOCOL",
    }
)

CARD_REVIEWER_ROLE_VISIBILITY = MappingProxyType(
    {
        "FORM_A": (True, True),
        "FORM_B": (True, True),
        "GOLD_A": (False, False),
        "GOLD_B": (False, False),
        "PROMPT": (True, True),
        "REPAIR_CONTRACT": (False, False),
        "FORM_SCHEMA": (False, False),
        "GOLD_SCHEMA": (False, False),
        "OUTPUT_SCHEMA": (True, True),
        "SCORING_PROTOCOL": (False, False),
    }
)

CARD_DISTILLER_REQUIRED_MEMBER_ROLES = MappingProxyType(
    {
        "forms/form_A.json": "FORM_A",
        "forms/form_B.json": "FORM_B",
        "gold/form_A_gold.json": "GOLD_A",
        "gold/form_B_gold.json": "GOLD_B",
        "prompts/card_distiller_prompt.json": "PROMPT_CONTRACT",
        "repair/directed_repair_contract.json": "REPAIR_CONTRACT",
        "schemas/card_distiller_primary_output.schema.json": "OUTPUT_SCHEMA",
        "schemas/card_distiller_primary_fact_map_shard.schema.json": (
            "FACT_MAP_SHARD_SCHEMA"
        ),
        "schemas/card_distiller_primary_card_projection_shard.schema.json": (
            "CARD_PROJECTION_SHARD_SCHEMA"
        ),
        "scoring/scoring_protocol.json": "SCORING_PROTOCOL",
    }
)

CARD_DISTILLER_ROLE_VISIBILITY = MappingProxyType(
    {
        "FORM_A": (True, True),
        "FORM_B": (True, True),
        "GOLD_A": (False, False),
        "GOLD_B": (False, False),
        "PROMPT_CONTRACT": (True, True),
        "REPAIR_CONTRACT": (False, False),
        "OUTPUT_SCHEMA": (True, True),
        "FACT_MAP_SHARD_SCHEMA": (True, True),
        "CARD_PROJECTION_SHARD_SCHEMA": (True, True),
        "SCORING_PROTOCOL": (False, False),
    }
)

_FORBIDDEN_PATH_TOKENS = frozenset(
    {
        "api_key",
        "credential",
        "nonce",
        "private_holdout",
        "raw_response",
        "secret",
        "token",
    }
)


@dataclass(frozen=True)
class ReferenceExamPack:
    """Verified paths and non-sensitive receipts for one immutable pack."""

    root: Path
    manifest: Mapping[str, Any]
    members: Mapping[str, Path]
    member_receipts: tuple[Mapping[str, Any], ...]
    manifest_receipt: Mapping[str, Any]
    checksum_receipt: Mapping[str, Any]
    pack_fingerprint: str

    def member(self, relative: str) -> Path:
        try:
            return self.members[relative]
        except KeyError as exc:
            raise ModelEvaluationReferenceExamPackError(
                f"REFERENCE_EXAM_PACK_MEMBER_NOT_VERIFIED:{relative}"
            ) from exc

    def public_receipt(self) -> dict[str, Any]:
        return {
            "pack_id": self.manifest["pack_id"],
            "pack_revision": self.manifest["pack_revision"],
            "classification": self.manifest["classification"],
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "pack_fingerprint": self.pack_fingerprint,
            "manifest": dict(self.manifest_receipt),
            "checksums": dict(self.checksum_receipt),
            "member_count": len(self.member_receipts),
            "members": [dict(item) for item in self.member_receipts],
            "gold_sent_to_subject": False,
            "gold_sent_to_provider": False,
        }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _receipt(path: Path, *, role: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
    }
    if role is not None:
        value["role"] = role
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ModelEvaluationReferenceExamPackError(
                f"REFERENCE_EXAM_PACK_DUPLICATE_JSON_KEY:{key}"
            )
        value[key] = item
    return value


def _load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except ModelEvaluationReferenceExamPackError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelEvaluationReferenceExamPackError(
            f"REFERENCE_EXAM_PACK_JSON_INVALID:{path.name}:{type(exc).__name__}"
        ) from exc


def _safe_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ModelEvaluationReferenceExamPackError("REFERENCE_EXAM_PACK_MEMBER_PATH_INVALID")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "." in relative.parts
        or (relative.parts and ":" in relative.parts[0])
    ):
        raise ModelEvaluationReferenceExamPackError(
            f"REFERENCE_EXAM_PACK_MEMBER_PATH_UNSAFE:{value}"
        )
    lowered = value.lower()
    if any(token in lowered for token in _FORBIDDEN_PATH_TOKENS):
        raise ModelEvaluationReferenceExamPackError(
            f"REFERENCE_EXAM_PACK_FORBIDDEN_MEMBER_PATH:{value}"
        )
    return relative.as_posix()


def _parse_checksums(path: Path) -> dict[str, str]:
    rows: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ModelEvaluationReferenceExamPackError(
            f"REFERENCE_EXAM_PACK_CHECKSUMS_UNREADABLE:{type(exc).__name__}"
        ) from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2 or re.fullmatch(r"[0-9A-Fa-f]{64}", parts[0]) is None:
            raise ModelEvaluationReferenceExamPackError(
                f"REFERENCE_EXAM_PACK_CHECKSUM_ROW_INVALID:{line_number}"
            )
        relative = _safe_relative_path(parts[1])
        if relative in rows:
            raise ModelEvaluationReferenceExamPackError(
                f"REFERENCE_EXAM_PACK_CHECKSUM_DUPLICATE:{relative}"
            )
        rows[relative] = parts[0].upper()
    return rows


def verify_reference_exam_pack(
    root: str | Path,
    *,
    expected_suite: str,
    required_member_roles: Mapping[str, str],
    role_visibility: Mapping[str, tuple[bool, bool]],
) -> ReferenceExamPack:
    """Verify classification, exact-set, hashes, rights, and visibility."""

    requested_root = Path(root)
    if requested_root.is_symlink():
        raise ModelEvaluationReferenceExamPackError(
            "REFERENCE_EXAM_PACK_ROOT_SYMLINK_FORBIDDEN"
        )
    pack_root = requested_root.resolve()
    if not pack_root.is_dir():
        raise ModelEvaluationReferenceExamPackError(
            f"BLOCKED_EXAM_PACK_UNAVAILABLE:{pack_root}"
        )
    manifest_path = pack_root / MANIFEST_NAME
    checksum_path = pack_root / CHECKSUM_NAME
    if not manifest_path.is_file() or not checksum_path.is_file():
        raise ModelEvaluationReferenceExamPackError(
            "BLOCKED_EXAM_PACK_UNAVAILABLE:MANIFEST_OR_CHECKSUMS_MISSING"
        )
    if manifest_path.is_symlink() or checksum_path.is_symlink():
        raise ModelEvaluationReferenceExamPackError(
            "REFERENCE_EXAM_PACK_CONTROL_FILE_SYMLINK_FORBIDDEN"
        )

    manifest = _load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ModelEvaluationReferenceExamPackError(
            "REFERENCE_EXAM_PACK_MANIFEST_OBJECT_REQUIRED"
        )
    required_scalars = {
        "schema_version": REFERENCE_PACK_SCHEMA_VERSION,
        "suite": expected_suite,
        "classification": REFERENCE_PACK_CLASSIFICATION,
        "reference_regression_only": True,
        "blind_holdout_eligible": False,
        "qualification_eligible": False,
    }
    for field, expected in required_scalars.items():
        if manifest.get(field) != expected:
            raise ModelEvaluationReferenceExamPackError(
                f"REFERENCE_EXAM_PACK_MANIFEST_FIELD_INVALID:{field}"
            )
    for field in ("pack_id", "pack_revision"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise ModelEvaluationReferenceExamPackError(
                f"REFERENCE_EXAM_PACK_MANIFEST_FIELD_REQUIRED:{field}"
            )

    declassification = manifest.get("declassification")
    if (
        not isinstance(declassification, dict)
        or declassification.get("authorized") is not True
        or not isinstance(declassification.get("authority"), str)
        or not declassification["authority"].strip()
    ):
        raise ModelEvaluationReferenceExamPackError(
            "REFERENCE_EXAM_PACK_DECLASSIFICATION_NOT_AUTHORIZED"
        )

    member_rows = manifest.get("members")
    if not isinstance(member_rows, list) or not member_rows:
        raise ModelEvaluationReferenceExamPackError("REFERENCE_EXAM_PACK_MEMBERS_REQUIRED")
    members: dict[str, Path] = {}
    receipts: list[Mapping[str, Any]] = []
    roles: dict[str, str] = {}
    for row in member_rows:
        if not isinstance(row, dict):
            raise ModelEvaluationReferenceExamPackError(
                "REFERENCE_EXAM_PACK_MEMBER_OBJECT_REQUIRED"
            )
        relative = _safe_relative_path(row.get("path"))
        if relative in members:
            raise ModelEvaluationReferenceExamPackError(
                f"REFERENCE_EXAM_PACK_MEMBER_DUPLICATE:{relative}"
            )
        role = row.get("role")
        if not isinstance(role, str) or role not in role_visibility:
            raise ModelEvaluationReferenceExamPackError(
                f"REFERENCE_EXAM_PACK_MEMBER_ROLE_INVALID:{relative}"
            )
        expected_visibility = role_visibility[role]
        observed_visibility = (
            row.get("subject_visible"),
            row.get("provider_visible"),
        )
        if observed_visibility != expected_visibility:
            raise ModelEvaluationReferenceExamPackError(
                f"REFERENCE_EXAM_PACK_VISIBILITY_INVALID:{relative}"
            )
        provenance = row.get("provenance")
        rights = row.get("rights")
        if not isinstance(provenance, dict) or not provenance:
            raise ModelEvaluationReferenceExamPackError(
                f"REFERENCE_EXAM_PACK_PROVENANCE_REQUIRED:{relative}"
            )
        if (
            not isinstance(rights, dict)
            or rights.get("repository_inclusion") != "AUTHORIZED"
        ):
            raise ModelEvaluationReferenceExamPackError(
                f"REFERENCE_EXAM_PACK_RIGHTS_NOT_AUTHORIZED:{relative}"
            )
        expected_bytes = row.get("bytes")
        expected_sha = row.get("sha256")
        if (
            isinstance(expected_bytes, bool)
            or not isinstance(expected_bytes, int)
            or expected_bytes < 0
            or not isinstance(expected_sha, str)
            or re.fullmatch(r"[0-9A-Fa-f]{64}", expected_sha) is None
        ):
            raise ModelEvaluationReferenceExamPackError(
                f"REFERENCE_EXAM_PACK_MEMBER_RECEIPT_INVALID:{relative}"
            )
        member_path = (pack_root / Path(*PurePosixPath(relative).parts)).resolve()
        try:
            member_path.relative_to(pack_root)
        except ValueError as exc:
            raise ModelEvaluationReferenceExamPackError(
                f"REFERENCE_EXAM_PACK_MEMBER_ESCAPES_ROOT:{relative}"
            ) from exc
        if not member_path.is_file() or member_path.is_symlink():
            raise ModelEvaluationReferenceExamPackError(
                f"REFERENCE_EXAM_PACK_MEMBER_MISSING_OR_SYMLINK:{relative}"
            )
        observed = _receipt(member_path, role=role)
        if (
            observed["bytes"] != expected_bytes
            or observed["sha256"] != expected_sha.upper()
        ):
            raise ModelEvaluationReferenceExamPackError(
                f"REFERENCE_EXAM_PACK_MEMBER_HASH_MISMATCH:{relative}"
            )
        _load_json(member_path)
        members[relative] = member_path
        roles[relative] = role
        receipts.append(
            {
                "path": relative,
                "role": role,
                "bytes": observed["bytes"],
                "sha256": observed["sha256"],
                "subject_visible": row["subject_visible"],
                "provider_visible": row["provider_visible"],
            }
        )

    required = dict(required_member_roles)
    if set(members) != set(required):
        missing = sorted(set(required) - set(members))
        extra = sorted(set(members) - set(required))
        raise ModelEvaluationReferenceExamPackError(
            f"REFERENCE_EXAM_PACK_MEMBER_EXACT_SET_MISMATCH:missing={missing}:extra={extra}"
        )
    for relative, expected_role in required.items():
        if roles.get(relative) != expected_role:
            raise ModelEvaluationReferenceExamPackError(
                f"REFERENCE_EXAM_PACK_REQUIRED_ROLE_MISMATCH:{relative}"
            )

    observed_files = set()
    for path in pack_root.rglob("*"):
        if path.is_symlink():
            raise ModelEvaluationReferenceExamPackError(
                f"REFERENCE_EXAM_PACK_SYMLINK_FORBIDDEN:{path.name}"
            )
        if path.is_file():
            observed_files.add(path.relative_to(pack_root).as_posix())
    expected_files = set(members) | {MANIFEST_NAME, CHECKSUM_NAME}
    if observed_files != expected_files:
        raise ModelEvaluationReferenceExamPackError(
            "REFERENCE_EXAM_PACK_FILESYSTEM_EXACT_SET_MISMATCH:"
            f"missing={sorted(expected_files - observed_files)}:"
            f"extra={sorted(observed_files - expected_files)}"
        )

    checksums = _parse_checksums(checksum_path)
    checksum_exact_set = set(members) | {MANIFEST_NAME}
    if set(checksums) != checksum_exact_set:
        raise ModelEvaluationReferenceExamPackError(
            "REFERENCE_EXAM_PACK_CHECKSUM_EXACT_SET_MISMATCH"
        )
    for relative, expected_sha in checksums.items():
        path = manifest_path if relative == MANIFEST_NAME else members[relative]
        if _sha256_file(path) != expected_sha:
            raise ModelEvaluationReferenceExamPackError(
                f"REFERENCE_EXAM_PACK_CHECKSUM_MISMATCH:{relative}"
            )

    return ReferenceExamPack(
        root=pack_root,
        manifest=MappingProxyType(manifest),
        members=MappingProxyType(members),
        member_receipts=tuple(receipts),
        manifest_receipt=MappingProxyType(_receipt(manifest_path)),
        checksum_receipt=MappingProxyType(_receipt(checksum_path)),
        pack_fingerprint=_sha256_file(manifest_path),
    )


def load_analysis_reference_exam_pack(
    root: str | Path = DEFAULT_ANALYSIS_REFERENCE_PACK,
) -> ReferenceExamPack:
    return verify_reference_exam_pack(
        root,
        expected_suite="analysis_primary",
        required_member_roles=ANALYSIS_REQUIRED_MEMBER_ROLES,
        role_visibility=ANALYSIS_ROLE_VISIBILITY,
    )


def load_card_reviewer_reference_exam_pack(
    root: str | Path = DEFAULT_CARD_REVIEWER_REFERENCE_PACK,
) -> ReferenceExamPack:
    """Load the immutable, non-blind card-reviewer regression pack."""

    return verify_reference_exam_pack(
        root,
        expected_suite="card_reviewer_primary",
        required_member_roles=CARD_REVIEWER_REQUIRED_MEMBER_ROLES,
        role_visibility=CARD_REVIEWER_ROLE_VISIBILITY,
    )


def load_card_distiller_reference_exam_pack(
    root: str | Path = DEFAULT_CARD_DISTILLER_REFERENCE_PACK,
) -> ReferenceExamPack:
    """Load the immutable, non-blind card-distiller regression pack."""

    return verify_reference_exam_pack(
        root,
        expected_suite="card_distiller_primary",
        required_member_roles=CARD_DISTILLER_REQUIRED_MEMBER_ROLES,
        role_visibility=CARD_DISTILLER_ROLE_VISIBILITY,
    )
