"""DATA-OWNERSHIP ownership contract and deterministic propagation guard.

This module owns no route, database, registry, or production migration.  It
turns one authoritative ownership assertion into immutable, hash-bound
derived snapshots.  Missing, malformed, conflicting, stale, and legacy-only
inputs remain explicit and can never silently become ``self``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


OWNERSHIP_CONTRACT_VERSION = "DATA_OWNERSHIP_OWNERSHIP_V1"
OWNERSHIP_AGGREGATION_POLICY = "STRICTEST_V1"
OWNERSHIP_COMPATIBILITY_WINDOW = "DATA_OWNERSHIP_LEGACY_COMPARE_WINDOW_V1"
OWNERSHIP_COMPATIBILITY_REMOVAL = "Distribution_COMPLETED_PASS_AND_SEPARATE_REMOVAL_AUTHORITY"

ATOMIC_OWNERSHIP_VALUES = ("self", "entrusted", "unknown")
OWNERSHIP_STRICTNESS = {"self": 0, "entrusted": 1, "unknown": 2}

OWNERSHIP_MISSING = "OWNERSHIP_MISSING"
OWNERSHIP_INVALID = "OWNERSHIP_INVALID"
OWNERSHIP_CONFLICT = "OWNERSHIP_CONFLICT"
OWNERSHIP_DOWNGRADE_ATTEMPT = "OWNERSHIP_DOWNGRADE_ATTEMPT"
OWNERSHIP_BINDING_MISMATCH = "OWNERSHIP_BINDING_MISMATCH"
OWNERSHIP_PROJECTION_STALE = "OWNERSHIP_PROJECTION_STALE"
OWNERSHIP_AUTHORITY_INSUFFICIENT = "OWNERSHIP_AUTHORITY_INSUFFICIENT"
OWNERSHIP_LEGACY_ADAPTER_REQUIRED = "OWNERSHIP_LEGACY_ADAPTER_REQUIRED"
OWNERSHIP_LOCAL_ROUTE_UNAVAILABLE = "OWNERSHIP_LOCAL_ROUTE_UNAVAILABLE"
OWNERSHIP_VERSION_UNSUPPORTED = "OWNERSHIP_VERSION_UNSUPPORTED"
EMPTY_CONTEXT_PACK = "EMPTY_CONTEXT_PACK"

_KNOWN_ERRORS = {
    OWNERSHIP_MISSING,
    OWNERSHIP_INVALID,
    OWNERSHIP_CONFLICT,
    OWNERSHIP_DOWNGRADE_ATTEMPT,
    OWNERSHIP_BINDING_MISMATCH,
    OWNERSHIP_PROJECTION_STALE,
    OWNERSHIP_AUTHORITY_INSUFFICIENT,
    OWNERSHIP_LEGACY_ADAPTER_REQUIRED,
    OWNERSHIP_LOCAL_ROUTE_UNAVAILABLE,
    OWNERSHIP_VERSION_UNSUPPORTED,
    EMPTY_CONTEXT_PACK,
}


class OwnershipContractError(ValueError):
    """Machine-readable fail-closed ownership error."""

    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None):
        if code not in _KNOWN_ERRORS:
            raise ValueError(f"unknown ownership error code: {code}")
        self.code = code
        self.details = dict(details or {})
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "details": dict(self.details)}


def _primitive(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Mapping):
        return {str(key): _primitive(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_primitive(item) for item in value]
    return value


def stable_json(value: Any) -> str:
    return json.dumps(_primitive(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def _codes(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _valid_ref(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True)
class OwnershipAssertion:
    assertion_ref: str
    subject_ref: str
    data_ownership: str
    revision: int
    basis_ref: str
    created_at: str
    supersedes_ref: str | None = None

    def validate(self) -> "OwnershipAssertion":
        if not all(_valid_ref(value) for value in (self.assertion_ref, self.subject_ref, self.basis_ref, self.created_at)):
            raise OwnershipContractError(OWNERSHIP_MISSING, "assertion identity/basis/time is missing")
        if self.data_ownership not in OWNERSHIP_STRICTNESS:
            raise OwnershipContractError(OWNERSHIP_INVALID, f"invalid atomic ownership {self.data_ownership!r}")
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 1:
            raise OwnershipContractError(OWNERSHIP_INVALID, "assertion revision must be a positive integer")
        return self

    def to_dict(self) -> dict[str, Any]:
        return _primitive(self)


@dataclass(frozen=True)
class OwnershipContributor:
    subject_ref: str
    data_ownership: str | None
    assertion_ref: str | None = None
    revision: int | None = None
    basis_ref: str | None = None
    binding_digest: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "OwnershipContributor":
        return cls(
            subject_ref=value.get("subject_ref"),
            data_ownership=value.get("data_ownership"),
            assertion_ref=value.get("assertion_ref"),
            revision=value.get("revision"),
            basis_ref=value.get("basis_ref"),
            binding_digest=value.get("binding_digest") or value.get("ownership_digest"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _primitive(self)


@dataclass(frozen=True)
class OwnershipSnapshot:
    subject_ref: str
    effective_data_ownership: str | None
    ownership_mix_state: str | None
    ownership_contributors: tuple[OwnershipContributor, ...]
    ownership_escalated_by: tuple[str, ...]
    ownership_digest: str
    ownership_resolution_status: str
    ownership_error_codes: tuple[str, ...] = ()
    ownership_warnings: tuple[str, ...] = ()
    ownership_contract_version: str = OWNERSHIP_CONTRACT_VERSION
    ownership_aggregation_policy: str = OWNERSHIP_AGGREGATION_POLICY
    legacy_bucket: str = "L0_native_v1"

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "OwnershipSnapshot":
        raw_contributors = value.get("ownership_contributors") or value.get("contributors") or ()
        return cls(
            subject_ref=value.get("subject_ref") or value.get("ownership_subject_ref"),
            effective_data_ownership=value.get("effective_data_ownership") or value.get("data_ownership"),
            ownership_mix_state=value.get("ownership_mix_state"),
            ownership_contributors=tuple(
                item if isinstance(item, OwnershipContributor) else OwnershipContributor.from_dict(item)
                for item in raw_contributors
            ),
            ownership_escalated_by=tuple(value.get("ownership_escalated_by") or ()),
            ownership_digest=value.get("ownership_digest"),
            ownership_resolution_status=value.get("ownership_resolution_status") or "legacy_missing",
            ownership_error_codes=tuple(value.get("ownership_error_codes") or ()),
            ownership_warnings=tuple(value.get("ownership_warnings") or ()),
            ownership_contract_version=value.get("ownership_contract_version") or "",
            ownership_aggregation_policy=value.get("ownership_aggregation_policy") or "",
            legacy_bucket=value.get("legacy_bucket") or "L0_native_v1",
        )

    def to_dict(self) -> dict[str, Any]:
        return _primitive(self)


@dataclass(frozen=True)
class OwnershipPreflight:
    subject_ref: str
    required_route_class: str
    preflight_status: str
    effective_data_ownership: str | None
    ownership_mix_state: str | None
    ownership_digest: str | None
    error_codes: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.preflight_status in {"ownership_resolved_policy_pending", "qualified_local_profile"}

    def to_dict(self) -> dict[str, Any]:
        return _primitive(self)


@dataclass(frozen=True)
class OwnershipAmendment:
    ownership_amendment_id: str
    subject_ref: str
    previous_assertion_ref: str
    previous_value: str
    new_value: str
    change_direction: str
    reason_code: str
    authority_ref: str | None
    created_at: str
    supersedes_ref: str
    adopted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _primitive(self)


@dataclass(frozen=True)
class OwnershipPropagationReceipt:
    receipt_id: str
    subject_chain: dict[str, Any]
    ownership_contract_version: str
    effective_data_ownership: str | None
    ownership_digest: str | None
    checks: tuple[str, ...]
    result: str
    error_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return _primitive(self)


@dataclass(frozen=True)
class LegacyAdapterResult:
    legacy_bucket: str
    compatibility_window: str
    compatibility_removal_condition: str
    snapshot: OwnershipSnapshot
    adopted_authority: tuple[str, ...]
    warnings: tuple[str, ...]
    receipt_digest: str

    def to_dict(self) -> dict[str, Any]:
        return _primitive(self)


def _contributor(value: OwnershipContributor | Mapping[str, Any]) -> OwnershipContributor:
    if isinstance(value, OwnershipContributor):
        return value
    if isinstance(value, Mapping):
        return OwnershipContributor.from_dict(value)
    raise OwnershipContractError(OWNERSHIP_INVALID, f"invalid contributor type {type(value).__name__}")


def _snapshot_digest_payload(
    *,
    subject_ref: str,
    effective: str | None,
    mix_state: str | None,
    contributors: tuple[OwnershipContributor, ...],
    escalated_by: tuple[str, ...],
    resolution_status: str,
    error_codes: tuple[str, ...],
    warnings: tuple[str, ...],
    legacy_bucket: str,
) -> dict[str, Any]:
    return {
        "ownership_contract_version": OWNERSHIP_CONTRACT_VERSION,
        "ownership_aggregation_policy": OWNERSHIP_AGGREGATION_POLICY,
        "subject_ref": subject_ref,
        "effective_data_ownership": effective,
        "ownership_mix_state": mix_state,
        "ownership_contributors": [item.to_dict() for item in contributors],
        "ownership_escalated_by": list(escalated_by),
        "ownership_resolution_status": resolution_status,
        "ownership_error_codes": list(error_codes),
        "ownership_warnings": list(warnings),
        "legacy_bucket": legacy_bucket,
    }


def aggregate_ownership(
    contributors: Iterable[OwnershipContributor | Mapping[str, Any]],
    *,
    subject_ref: str,
    inherited_errors: Iterable[str] = (),
    warnings: Iterable[str] = (),
    empty_error: str = OWNERSHIP_MISSING,
    resolution_status: str | None = None,
    legacy_bucket: str = "L0_native_v1",
) -> OwnershipSnapshot:
    raw = tuple(_contributor(value) for value in contributors)
    ordered = tuple(sorted(raw, key=lambda item: (str(item.subject_ref), str(item.assertion_ref), item.revision or -1)))
    errors = list(inherited_errors)
    seen: set[str] = set()
    for item in ordered:
        if not _valid_ref(item.subject_ref):
            errors.append(OWNERSHIP_INVALID)
        elif item.subject_ref in seen:
            errors.append(OWNERSHIP_CONFLICT)
        else:
            seen.add(item.subject_ref)
        if item.data_ownership is None:
            errors.append(OWNERSHIP_MISSING)
        elif item.data_ownership not in OWNERSHIP_STRICTNESS:
            errors.append(OWNERSHIP_INVALID)
        if not _valid_ref(item.assertion_ref):
            errors.append(OWNERSHIP_MISSING)
        if not _valid_ref(item.basis_ref):
            errors.append(OWNERSHIP_MISSING)
        if item.revision is None:
            errors.append(OWNERSHIP_MISSING)
        elif not isinstance(item.revision, int) or isinstance(item.revision, bool) or item.revision < 1:
            errors.append(OWNERSHIP_INVALID)
        if item.binding_digest is not None and not (
            isinstance(item.binding_digest, str)
            and item.binding_digest.startswith("sha256:")
            and len(item.binding_digest) == 71
        ):
            errors.append(OWNERSHIP_INVALID)
    if not ordered:
        errors.append(empty_error)

    codes = _codes(errors)
    warning_values = _codes(warnings)
    valid_values = [item.data_ownership for item in ordered if item.data_ownership in OWNERSHIP_STRICTNESS]
    if codes:
        effective = None
        mix_state = None
        escalated_by: tuple[str, ...] = ()
        status = "blocked"
    else:
        effective = max(valid_values, key=lambda item: OWNERSHIP_STRICTNESS[item])
        mix_state = "mixed" if len(set(valid_values)) > 1 else "uniform"
        minimum_rank = min(OWNERSHIP_STRICTNESS[item] for item in valid_values)
        effective_rank = OWNERSHIP_STRICTNESS[effective]
        escalated_by = (
            tuple(item.subject_ref for item in ordered if item.data_ownership == effective)
            if effective_rank > minimum_rank
            else ()
        )
        status = resolution_status or ("adapted" if warning_values else "resolved")

    payload = _snapshot_digest_payload(
        subject_ref=subject_ref,
        effective=effective,
        mix_state=mix_state,
        contributors=ordered,
        escalated_by=escalated_by,
        resolution_status=status,
        error_codes=codes,
        warnings=warning_values,
        legacy_bucket=legacy_bucket,
    )
    return OwnershipSnapshot(
        subject_ref=subject_ref,
        effective_data_ownership=effective,
        ownership_mix_state=mix_state,
        ownership_contributors=ordered,
        ownership_escalated_by=escalated_by,
        ownership_digest=stable_sha256(payload),
        ownership_resolution_status=status,
        ownership_error_codes=codes,
        ownership_warnings=warning_values,
        legacy_bucket=legacy_bucket,
    )


def _snapshot_from_object(value: Any) -> OwnershipSnapshot:
    direct = getattr(value, "ownership_snapshot", None)
    if isinstance(direct, OwnershipSnapshot):
        return direct
    if isinstance(direct, Mapping):
        return OwnershipSnapshot.from_dict(direct)
    if isinstance(value, OwnershipSnapshot):
        return value
    if isinstance(value, Mapping) and ("ownership_digest" in value or "ownership_contract_version" in value):
        return OwnershipSnapshot.from_dict(value)

    version = getattr(value, "ownership_contract_version", None)
    policy = getattr(value, "ownership_aggregation_policy", None)
    subject_ref = (
        getattr(value, "ownership_subject_ref", None)
        or getattr(value, "context_pack_ref", None)
        or getattr(value, "block_ref", None)
    )
    contributors = getattr(value, "ownership_contributors", None)
    digest = getattr(value, "ownership_digest", None)
    if not version or not policy or not subject_ref or contributors is None or not digest:
        raise OwnershipContractError(
            OWNERSHIP_LEGACY_ADAPTER_REQUIRED,
            "object lacks a native ownership version/subject/contributors/digest; caller hints cannot self-prove it",
            details={"legacy_bucket": "L3_insufficient_authority"},
        )
    return OwnershipSnapshot(
        subject_ref=subject_ref,
        effective_data_ownership=(
            getattr(value, "effective_data_ownership", None)
            if hasattr(value, "effective_data_ownership")
            else getattr(value, "data_ownership", None)
        ),
        ownership_mix_state=getattr(value, "ownership_mix_state", None),
        ownership_contributors=tuple(_contributor(item) for item in contributors),
        ownership_escalated_by=tuple(getattr(value, "ownership_escalated_by", ()) or ()),
        ownership_digest=digest,
        ownership_resolution_status=getattr(value, "ownership_resolution_status", "legacy_missing"),
        ownership_error_codes=tuple(getattr(value, "ownership_error_codes", ()) or ()),
        ownership_warnings=tuple(getattr(value, "ownership_warnings", ()) or ()),
        ownership_contract_version=version,
        ownership_aggregation_policy=policy,
        legacy_bucket=getattr(value, "legacy_bucket", "L0_native_v1"),
    )


def validate_snapshot(value: Any) -> OwnershipSnapshot:
    snapshot = _snapshot_from_object(value)
    if snapshot.ownership_contract_version != OWNERSHIP_CONTRACT_VERSION:
        raise OwnershipContractError(
            OWNERSHIP_VERSION_UNSUPPORTED,
            f"unsupported contract version {snapshot.ownership_contract_version!r}",
        )
    if snapshot.ownership_aggregation_policy != OWNERSHIP_AGGREGATION_POLICY:
        raise OwnershipContractError(
            OWNERSHIP_VERSION_UNSUPPORTED,
            f"unsupported aggregation policy {snapshot.ownership_aggregation_policy!r}",
        )
    if snapshot.ownership_resolution_status == "blocked" or snapshot.ownership_error_codes:
        code = snapshot.ownership_error_codes[0] if snapshot.ownership_error_codes else OWNERSHIP_INVALID
        raise OwnershipContractError(code, "ownership snapshot is blocked", details=snapshot.to_dict())
    expected = aggregate_ownership(
        snapshot.ownership_contributors,
        subject_ref=snapshot.subject_ref,
        warnings=snapshot.ownership_warnings,
        resolution_status=snapshot.ownership_resolution_status,
        legacy_bucket=snapshot.legacy_bucket,
    )
    if (
        expected.effective_data_ownership != snapshot.effective_data_ownership
        or expected.ownership_mix_state != snapshot.ownership_mix_state
        or expected.ownership_escalated_by != snapshot.ownership_escalated_by
        or expected.ownership_digest != snapshot.ownership_digest
    ):
        raise OwnershipContractError(
            OWNERSHIP_BINDING_MISMATCH,
            "ownership snapshot fields or digest do not match contributors",
            details={"expected": expected.to_dict(), "observed": snapshot.to_dict()},
        )
    return snapshot


def snapshot_fields(snapshot: OwnershipSnapshot, *, pack: bool = False) -> dict[str, Any]:
    common = {
        "ownership_subject_ref": snapshot.subject_ref,
        "ownership_contract_version": snapshot.ownership_contract_version,
        "ownership_aggregation_policy": snapshot.ownership_aggregation_policy,
        "ownership_mix_state": snapshot.ownership_mix_state,
        "ownership_contributors": snapshot.ownership_contributors,
        "ownership_escalated_by": snapshot.ownership_escalated_by,
        "ownership_digest": snapshot.ownership_digest,
        "ownership_resolution_status": snapshot.ownership_resolution_status,
        "ownership_error_codes": snapshot.ownership_error_codes,
        "ownership_warnings": snapshot.ownership_warnings,
        "legacy_bucket": snapshot.legacy_bucket,
    }
    common["effective_data_ownership" if pack else "data_ownership"] = snapshot.effective_data_ownership
    return common


def bind_authority_projection(
    *,
    subject_ref: str,
    authoritative_value: str | None,
    assertion_ref: str | None,
    revision: int | None,
    basis_ref: str | None,
    projection_value: str | None = None,
    projection_revision: int | None = None,
    projection_ref: str | None = None,
) -> OwnershipSnapshot:
    errors: list[str] = []
    warnings: list[str] = []
    if authoritative_value is None:
        errors.extend((OWNERSHIP_MISSING, OWNERSHIP_AUTHORITY_INSUFFICIENT))
    elif authoritative_value not in OWNERSHIP_STRICTNESS:
        errors.append(OWNERSHIP_INVALID)
    if not _valid_ref(assertion_ref) or not _valid_ref(basis_ref):
        errors.append(OWNERSHIP_AUTHORITY_INSUFFICIENT)
    if revision is None:
        errors.append(OWNERSHIP_MISSING)
    elif not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        errors.append(OWNERSHIP_INVALID)

    if projection_value is None:
        warnings.append("LEGACY_PROJECTION_ABSENT")
    elif projection_value not in OWNERSHIP_STRICTNESS:
        errors.append(OWNERSHIP_INVALID)
    elif authoritative_value in OWNERSHIP_STRICTNESS and projection_value != authoritative_value:
        errors.extend((OWNERSHIP_PROJECTION_STALE, OWNERSHIP_CONFLICT))
    if projection_revision is not None:
        if not isinstance(projection_revision, int) or isinstance(projection_revision, bool) or projection_revision < 1:
            errors.append(OWNERSHIP_INVALID)
        elif isinstance(revision, int) and projection_revision != revision:
            errors.append(OWNERSHIP_PROJECTION_STALE)

    contributor = OwnershipContributor(
        subject_ref=subject_ref,
        data_ownership=authoritative_value,
        assertion_ref=assertion_ref,
        revision=revision,
        basis_ref=basis_ref,
        binding_digest=(
            stable_sha256({"projection_ref": projection_ref, "value": projection_value, "revision": projection_revision})
            if projection_ref and projection_value is not None
            else None
        ),
    )
    bucket = "L1_traceable_card" if warnings and not errors else "L2_conflicting_projection" if errors else "L0_native_v1"
    return aggregate_ownership(
        (contributor,),
        subject_ref=subject_ref,
        inherited_errors=errors,
        warnings=warnings,
        legacy_bucket=bucket,
    )


def bind_atomic_assertion(assertion: OwnershipAssertion) -> OwnershipSnapshot:
    assertion.validate()
    return aggregate_ownership(
        (
            OwnershipContributor(
                subject_ref=assertion.subject_ref,
                data_ownership=assertion.data_ownership,
                assertion_ref=assertion.assertion_ref,
                revision=assertion.revision,
                basis_ref=assertion.basis_ref,
            ),
        ),
        subject_ref=assertion.subject_ref,
    )


def block_contributor(block: Any) -> OwnershipContributor:
    snapshot = validate_snapshot(block)
    block_ref = getattr(block, "block_ref", None) or snapshot.subject_ref
    max_revision = max((item.revision or 1 for item in snapshot.ownership_contributors), default=1)
    return OwnershipContributor(
        subject_ref=block_ref,
        data_ownership=snapshot.effective_data_ownership,
        assertion_ref=f"OB-{snapshot.ownership_digest.removeprefix('sha256:')[:24]}",
        revision=max_revision,
        basis_ref=snapshot.subject_ref,
        binding_digest=snapshot.ownership_digest,
    )


def aggregate_blocks(blocks: Iterable[Any], *, subject_ref: str) -> OwnershipSnapshot:
    items = tuple(blocks)
    if not items:
        return aggregate_ownership((), subject_ref=subject_ref, empty_error=EMPTY_CONTEXT_PACK)
    contributors: list[OwnershipContributor] = []
    errors: list[str] = []
    block_refs: set[str] = set()
    for index, block in enumerate(items):
        block_ref = getattr(block, "block_ref", None)
        if not _valid_ref(block_ref):
            errors.append(OWNERSHIP_INVALID)
            block_ref = f"missing-block-{index}"
        elif block_ref in block_refs:
            errors.append(OWNERSHIP_CONFLICT)
        else:
            block_refs.add(block_ref)
        try:
            contributors.append(block_contributor(block))
        except OwnershipContractError as exc:
            errors.append(exc.code)
            contributors.append(
                OwnershipContributor(
                    subject_ref=block_ref or f"missing-block-{index}",
                    data_ownership=getattr(block, "data_ownership", None),
                    assertion_ref=getattr(block, "ownership_assertion_ref", None),
                    revision=getattr(block, "ownership_revision", None),
                    basis_ref=getattr(block, "ownership_basis_ref", None),
                    binding_digest=getattr(block, "ownership_digest", None),
                )
            )
    return aggregate_ownership(contributors, subject_ref=subject_ref, inherited_errors=errors)


def compare_legacy_binding(snapshot_or_object: Any, legacy_value: str | None) -> str:
    snapshot = validate_snapshot(snapshot_or_object)
    if legacy_value is None:
        return "NATIVE_PACK_AUTHORITY"
    if legacy_value not in OWNERSHIP_STRICTNESS:
        raise OwnershipContractError(OWNERSHIP_INVALID, f"invalid legacy ownership value {legacy_value!r}")
    effective = snapshot.effective_data_ownership
    if legacy_value == effective:
        return "DEPRECATED_MATCHED_ASSERTION"
    if OWNERSHIP_STRICTNESS[legacy_value] < OWNERSHIP_STRICTNESS[effective]:
        raise OwnershipContractError(
            OWNERSHIP_DOWNGRADE_ATTEMPT,
            f"legacy value {legacy_value!r} is less strict than pack {effective!r}",
        )
    raise OwnershipContractError(
        OWNERSHIP_BINDING_MISMATCH,
        f"legacy value {legacy_value!r} does not match pack {effective!r}",
    )


def ownership_preflight(snapshot_or_object: Any, route_profile: Mapping[str, Any] | None = None) -> OwnershipPreflight:
    try:
        snapshot = validate_snapshot(snapshot_or_object)
    except OwnershipContractError as exc:
        subject_ref = getattr(snapshot_or_object, "context_pack_ref", None) or getattr(snapshot_or_object, "ownership_subject_ref", None) or "unbound"
        return OwnershipPreflight(subject_ref, "blocked", "blocked_contract_error", None, None, None, (exc.code,))

    value = snapshot.effective_data_ownership
    if value == "self":
        return OwnershipPreflight(
            snapshot.subject_ref,
            "policy_decides",
            "ownership_resolved_policy_pending",
            value,
            snapshot.ownership_mix_state,
            snapshot.ownership_digest,
        )

    required = "local_only" if value == "entrusted" else "local_only_unknown"
    profile = dict(route_profile or {})
    accepted = set(profile.get("accepts") or ())
    qualified = profile.get("kind") == "local" and profile.get("qualified") is True and value in accepted
    if value == "unknown":
        qualified = qualified and profile.get("accepts_unknown") is True
    if qualified:
        return OwnershipPreflight(
            snapshot.subject_ref,
            required,
            "qualified_local_profile",
            value,
            snapshot.ownership_mix_state,
            snapshot.ownership_digest,
        )
    return OwnershipPreflight(
        snapshot.subject_ref,
        required,
        "blocked_no_qualified_local_profile",
        value,
        snapshot.ownership_mix_state,
        snapshot.ownership_digest,
        (OWNERSHIP_LOCAL_ROUTE_UNAVAILABLE,),
    )


def amendment_candidate(
    previous: OwnershipAssertion,
    *,
    new_value: str,
    reason_code: str,
    authority_ref: str | None,
    created_at: str | None = None,
) -> OwnershipAmendment:
    previous.validate()
    if new_value not in OWNERSHIP_STRICTNESS:
        raise OwnershipContractError(OWNERSHIP_INVALID, f"invalid amendment value {new_value!r}")
    direction = (
        "same"
        if new_value == previous.data_ownership
        else "relaxing"
        if OWNERSHIP_STRICTNESS[new_value] < OWNERSHIP_STRICTNESS[previous.data_ownership]
        else "tightening"
    )
    if direction == "relaxing" and not _valid_ref(authority_ref):
        raise OwnershipContractError(
            OWNERSHIP_AUTHORITY_INSUFFICIENT,
            "relaxing amendment requires explicit authority",
        )
    if not _valid_ref(reason_code):
        raise OwnershipContractError(OWNERSHIP_MISSING, "amendment reason is missing")
    timestamp = created_at or datetime.now(timezone.utc).isoformat()
    digest = stable_sha256(
        {
            "subject_ref": previous.subject_ref,
            "previous_assertion_ref": previous.assertion_ref,
            "previous_value": previous.data_ownership,
            "new_value": new_value,
            "reason_code": reason_code,
            "authority_ref": authority_ref,
            "created_at": timestamp,
        }
    )
    return OwnershipAmendment(
        ownership_amendment_id=f"OA-{digest.removeprefix('sha256:')[:24]}",
        subject_ref=previous.subject_ref,
        previous_assertion_ref=previous.assertion_ref,
        previous_value=previous.data_ownership,
        new_value=new_value,
        change_direction=direction,
        reason_code=reason_code,
        authority_ref=authority_ref,
        created_at=timestamp,
        supersedes_ref=previous.assertion_ref,
        adopted=False,
    )


def adapt_legacy_authorities(
    *,
    subject_ref: str,
    authorities: Iterable[Mapping[str, Any]],
) -> LegacyAdapterResult:
    contributors: list[OwnershipContributor] = []
    authority_refs: list[str] = []
    for item in authorities:
        contributor = OwnershipContributor(
            subject_ref=item.get("subject_ref"),
            data_ownership=item.get("data_ownership"),
            assertion_ref=item.get("assertion_ref"),
            revision=item.get("revision"),
            basis_ref=item.get("basis_ref"),
        )
        contributors.append(contributor)
        if contributor.basis_ref:
            authority_refs.append(contributor.basis_ref)
    if not contributors:
        raise OwnershipContractError(
            OWNERSHIP_AUTHORITY_INSUFFICIENT,
            "legacy caller/vector hint without traceable Card authority cannot prove ownership",
        )
    snapshot = aggregate_ownership(
        contributors,
        subject_ref=subject_ref,
        warnings=("EXPLICIT_LEGACY_ADAPTER",),
        resolution_status="adapted",
        legacy_bucket="L1_traceable_card",
    )
    validate_snapshot(snapshot)
    receipt_payload = {
        "legacy_bucket": "L1_traceable_card",
        "subject_ref": subject_ref,
        "adopted_authority": sorted(authority_refs),
        "snapshot": snapshot.to_dict(),
        "compatibility_window": OWNERSHIP_COMPATIBILITY_WINDOW,
        "removal": OWNERSHIP_COMPATIBILITY_REMOVAL,
    }
    return LegacyAdapterResult(
        legacy_bucket="L1_traceable_card",
        compatibility_window=OWNERSHIP_COMPATIBILITY_WINDOW,
        compatibility_removal_condition=OWNERSHIP_COMPATIBILITY_REMOVAL,
        snapshot=snapshot,
        adopted_authority=tuple(sorted(authority_refs)),
        warnings=("EXPLICIT_LEGACY_ADAPTER", "NO_FORMAL_OBJECT_REWRITTEN"),
        receipt_digest=stable_sha256(receipt_payload),
    )


def ownership_cache_key(snapshot_or_object: Any) -> str:
    snapshot = validate_snapshot(snapshot_or_object)
    revisions = sorted((item.subject_ref, item.revision) for item in snapshot.ownership_contributors)
    return stable_sha256(
        {
            "subject_ref": snapshot.subject_ref,
            "revisions": revisions,
            "contract": snapshot.ownership_contract_version,
            "policy": snapshot.ownership_aggregation_policy,
            "digest": snapshot.ownership_digest,
        }
    )


def validate_cache_binding(snapshot_or_object: Any, cached_key: str, cached_revisions: Mapping[str, int]) -> bool:
    snapshot = validate_snapshot(snapshot_or_object)
    current = {item.subject_ref: item.revision for item in snapshot.ownership_contributors}
    if dict(cached_revisions) != current or cached_key != ownership_cache_key(snapshot):
        raise OwnershipContractError(OWNERSHIP_PROJECTION_STALE, "ownership cache revision/key is stale")
    return True


def render_snapshot_frontmatter(snapshot_or_object: Any) -> str:
    import yaml

    snapshot = validate_snapshot(snapshot_or_object)
    payload = {"ownership_snapshot": snapshot.to_dict()}
    return "---\n" + yaml.safe_dump(payload, allow_unicode=True, sort_keys=True) + "---\n"


def parse_snapshot_frontmatter(text: str) -> OwnershipSnapshot:
    import yaml

    if not isinstance(text, str) or not text.startswith("---\n"):
        raise OwnershipContractError(OWNERSHIP_INVALID, "ownership frontmatter is malformed")
    try:
        body = text.split("---\n", 2)[1]
        payload = yaml.safe_load(body)
    except Exception as exc:
        raise OwnershipContractError(OWNERSHIP_INVALID, "ownership frontmatter parse failed") from exc
    if not isinstance(payload, Mapping) or not isinstance(payload.get("ownership_snapshot"), Mapping):
        raise OwnershipContractError(OWNERSHIP_MISSING, "ownership_snapshot is missing from frontmatter")
    return validate_snapshot(OwnershipSnapshot.from_dict(payload["ownership_snapshot"]))


def propagation_receipt(
    snapshot_or_object: Any,
    *,
    subject_chain: Mapping[str, Any],
    checks: Iterable[str],
) -> OwnershipPropagationReceipt:
    try:
        snapshot = validate_snapshot(snapshot_or_object)
        result = "PASS"
        errors: tuple[str, ...] = ()
        effective = snapshot.effective_data_ownership
        digest = snapshot.ownership_digest
    except OwnershipContractError as exc:
        result = "BLOCKED"
        errors = (exc.code,)
        effective = None
        digest = None
    payload = {
        "subject_chain": dict(subject_chain),
        "ownership_contract_version": OWNERSHIP_CONTRACT_VERSION,
        "effective_data_ownership": effective,
        "ownership_digest": digest,
        "checks": list(checks),
        "result": result,
        "error_codes": list(errors),
    }
    return OwnershipPropagationReceipt(
        receipt_id=f"OPR-{stable_sha256(payload).removeprefix('sha256:')[:24]}",
        subject_chain=dict(subject_chain),
        ownership_contract_version=OWNERSHIP_CONTRACT_VERSION,
        effective_data_ownership=effective,
        ownership_digest=digest,
        checks=tuple(checks),
        result=result,
        error_codes=errors,
    )


def explain_ownership(value: Any) -> dict[str, Any]:
    if isinstance(value, OwnershipContractError):
        return {
            "error_codes": [value.code],
            "effective_data_ownership": None,
            "ownership_mix_state": None,
            "trigger_refs": value.details.get("trigger_refs", []),
            "recovery": "supply a traceable assertion/amendment or rebuild the stale projection under separate authority",
            "formal_objects_rewritten": False,
        }
    try:
        snapshot = _snapshot_from_object(value)
        preflight = ownership_preflight(snapshot)
        return {
            "error_codes": list(snapshot.ownership_error_codes or preflight.error_codes),
            "effective_data_ownership": snapshot.effective_data_ownership,
            "ownership_mix_state": snapshot.ownership_mix_state,
            "trigger_refs": list(snapshot.ownership_escalated_by),
            "legacy_bucket": snapshot.legacy_bucket,
            "required_route_class": preflight.required_route_class,
            "preflight_status": preflight.preflight_status,
            "recovery": (
                "wait for a qualified local profile"
                if preflight.error_codes == (OWNERSHIP_LOCAL_ROUTE_UNAVAILABLE,)
                else "none"
            ),
            "formal_objects_rewritten": False,
        }
    except OwnershipContractError as exc:
        return explain_ownership(exc)


def trace_ownership(value: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(value, OwnershipPropagationReceipt):
        chain = value.subject_chain
    elif isinstance(value, Mapping) and "subject_chain" in value:
        chain = value["subject_chain"]
    else:
        chain = getattr(value, "subject_chain", None)
    if not isinstance(chain, Mapping):
        raise OwnershipContractError(OWNERSHIP_BINDING_MISMATCH, "no bound subject chain is available for trace")
    trace = []
    for hop, binding in chain.items():
        if binding in (None, "", [], {}):
            raise OwnershipContractError(
                OWNERSHIP_BINDING_MISMATCH,
                f"ownership trace breaks at {hop}",
                details={"hop": hop},
            )
        trace.append({"hop": hop, "binding": binding, "binding_result": "matched"})
    return tuple(trace)


class OwnershipGuard:
    """Stable facade; all methods are pure or return immutable values."""

    validate = staticmethod(validate_snapshot)
    aggregate = staticmethod(aggregate_ownership)
    aggregate_blocks = staticmethod(aggregate_blocks)
    bind_assertion = staticmethod(bind_atomic_assertion)
    bind_authority_projection = staticmethod(bind_authority_projection)
    compare_legacy = staticmethod(compare_legacy_binding)
    preflight = staticmethod(ownership_preflight)
    explain = staticmethod(explain_ownership)
    trace = staticmethod(trace_ownership)
    amendment_candidate = staticmethod(amendment_candidate)
    adapt_legacy = staticmethod(adapt_legacy_authorities)
    cache_key = staticmethod(ownership_cache_key)
    validate_cache = staticmethod(validate_cache_binding)
    propagation_receipt = staticmethod(propagation_receipt)


__all__ = [
    "OWNERSHIP_CONTRACT_VERSION", "OWNERSHIP_AGGREGATION_POLICY",
    "OWNERSHIP_COMPATIBILITY_WINDOW", "OWNERSHIP_COMPATIBILITY_REMOVAL",
    "ATOMIC_OWNERSHIP_VALUES", "OWNERSHIP_STRICTNESS",
    "OWNERSHIP_MISSING", "OWNERSHIP_INVALID", "OWNERSHIP_CONFLICT",
    "OWNERSHIP_DOWNGRADE_ATTEMPT", "OWNERSHIP_BINDING_MISMATCH",
    "OWNERSHIP_PROJECTION_STALE", "OWNERSHIP_AUTHORITY_INSUFFICIENT",
    "OWNERSHIP_LEGACY_ADAPTER_REQUIRED", "OWNERSHIP_LOCAL_ROUTE_UNAVAILABLE",
    "OWNERSHIP_VERSION_UNSUPPORTED", "EMPTY_CONTEXT_PACK",
    "OwnershipContractError", "OwnershipAssertion", "OwnershipContributor",
    "OwnershipSnapshot", "OwnershipPreflight", "OwnershipAmendment",
    "OwnershipPropagationReceipt", "LegacyAdapterResult", "OwnershipGuard",
    "stable_json", "stable_sha256", "snapshot_fields", "block_contributor",
    "render_snapshot_frontmatter", "parse_snapshot_frontmatter",
]
