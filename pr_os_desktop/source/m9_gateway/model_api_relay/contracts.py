from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import ipaddress
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from m9_gateway.execution_core.contracts import canonical_sha256, immutable_copy


SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MODEL_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}$")


class RelayContractError(ValueError):
    def __init__(self, code: str, details: Sequence[str] | None = None):
        self.code = code
        self.details = tuple(details or ())
        suffix = f": {'; '.join(self.details)}" if self.details else ""
        super().__init__(f"{code}{suffix}")


PROFILE_FIELDS = {
    "schema_version", "profile_id", "revision", "status", "transport_kind",
    "relay_mode", "access_mode", "protocol_family", "protocol_revision",
    "capabilities", "allowed_roles",
    "endpoint_operator", "claimed_upstream_provider", "scheme", "origin",
    "path_template", "allowed_query_keys", "redirect_policy", "allowed_origins",
    "credential_binding_id", "credential_binding_revision", "credential_ref",
    "auth_scheme", "header_name", "logical_model_id",
    "request_model_alias", "alias_revision", "route_id", "region", "egress",
    "dns_proxy_profile", "billing_mode", "usage_source", "cost_source", "currency",
    "behavior_hash", "request_contract_hash", "output_contract_hash",
    "qualification_report_ref", "valid_from", "expires_at", "revocation_conditions",
    "expected_embedding_dimension", "rerank_score_range",
}
BINDING_FIELDS = {
    "schema_version", "credential_binding_id", "revision", "credential_ref",
    "binding_kind", "endpoint_operator", "allowed_origin", "auth_scheme",
    "header_name",
}
ALIAS_FIELDS = {
    "schema_version", "logical_model_id", "request_model_alias", "alias_revision",
    "endpoint_operator", "claimed_upstream_provider", "identity_threshold",
    "fallback_aliases", "pool_identity", "selection_policy",
}
MANIFEST_FIELDS = {
    "schema_version", "adapter_id", "protocol_family", "protocol_revision",
    "capabilities", "request_contract_hash", "output_contract_hash",
    "max_response_bytes", "streaming", "relay_mode", "allowed_headers",
}

PROTOCOLS = {
    "OPENAI_CHAT_COMPLETIONS",
    "OPENAI_RESPONSES",
    "ANTHROPIC_MESSAGES",
    "GEMINI_GENERATE_CONTENT",
    "OPENAI_EMBEDDINGS",
    "PROVIDER_RERANK",
}
TRANSPORT_KINDS = {"DIRECT_PROVIDER", "MODEL_API_RELAY", "SELF_HOSTED_LOOPBACK_RELAY"}
PROFILE_STATUSES = {"CANDIDATE", "QUALIFIED_DISABLED", "ACTIVE", "DISABLED", "REVOKED"}
IDENTITY_THRESHOLDS = {
    "DIRECT_PROVIDER_VERIFIED", "RELAY_BACKEND_VERIFIED",
    "RELAY_REPORTED_UNVERIFIED", "RELAY_REPORTED_CONFLICT",
    "PROVIDER_NOT_EXPOSED",
}
RELAY_MODES = {"PROTOCOL_PRESERVING", "PROTOCOL_TRANSLATING", "MODEL_ALIAS_ROUTER"}


def _object(value: Mapping[str, Any], fields: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RelayContractError(f"{code}_NOT_OBJECT")
    missing = sorted(fields - set(value))
    extra = sorted(set(value) - fields)
    if missing or extra:
        raise RelayContractError(f"{code}_FIELDS_INVALID", [f"missing={missing}", f"extra={extra}"])
    return immutable_copy(value)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RelayContractError("FIELD_INVALID", [field])
    return value


def _identifier(value: Any, field: str) -> str:
    text = _text(value, field)
    if not ID_RE.fullmatch(text):
        raise RelayContractError("IDENTIFIER_INVALID", [field])
    return text


def _model_alias(value: Any, field: str) -> str:
    """Accept provider-native model namespaces without accepting URL syntax."""
    text = _text(value, field)
    if (
        not MODEL_ALIAS_RE.fullmatch(text)
        or "//" in text
        or any(part in {"", ".", ".."} for part in text.split("/"))
    ):
        raise RelayContractError("MODEL_ALIAS_INVALID", [field])
    return text


def _string_list(value: Any, field: str, *, nonempty: bool = True) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise RelayContractError("STRING_LIST_INVALID", [field])
    accepted = []
    for item in value:
        accepted.append(_text(item, field))
    if len(set(accepted)) != len(accepted):
        raise RelayContractError("STRING_LIST_DUPLICATE", [field])
    return accepted


def _timestamp(value: Any, field: str) -> datetime:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RelayContractError("TIMESTAMP_INVALID", [field]) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RelayContractError("TIMESTAMP_INVALID", [field])
    return parsed


def _sha(value: Any, field: str) -> str:
    text = _text(value, field).upper()
    if not SHA256_RE.fullmatch(text):
        raise RelayContractError("SHA256_INVALID", [field])
    return text


def _origin(value: Any, *, transport_kind: str, field: str = "origin") -> str:
    text = _text(value, field)
    parsed = urlsplit(text)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RelayContractError("ORIGIN_COMPONENT_FORBIDDEN", [field])
    if not parsed.hostname or parsed.path not in {"", "/"}:
        raise RelayContractError("ORIGIN_FORMAT_INVALID", [field])
    try:
        parsed.port
    except ValueError as exc:
        raise RelayContractError("ORIGIN_PORT_INVALID", [field]) from exc
    host = parsed.hostname.lower()
    if "*" in host or host.endswith("."):
        raise RelayContractError("ORIGIN_HOST_CONFUSABLE", [field])
    try:
        host.encode("ascii", errors="strict")
    except UnicodeEncodeError as exc:
        raise RelayContractError("ORIGIN_UNICODE_HOST_FORBIDDEN", [field]) from exc
    loopback = host == "localhost"
    try:
        ip = ipaddress.ip_address(host)
        loopback = ip.is_loopback
        unsafe_ip = ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_unspecified
    except ValueError:
        unsafe_ip = False
    if transport_kind == "SELF_HOSTED_LOOPBACK_RELAY":
        if parsed.scheme != "http" or not loopback:
            raise RelayContractError("LOOPBACK_RELAY_ORIGIN_INVALID", [field])
    else:
        if parsed.scheme != "https":
            raise RelayContractError("REMOTE_HTTPS_REQUIRED", [field])
        if loopback or unsafe_ip:
            raise RelayContractError("REMOTE_PRIVATE_ORIGIN_FORBIDDEN", [field])
    return text.rstrip("/")


def validate_endpoint_profile(value: Mapping[str, Any]) -> dict[str, Any]:
    accepted = _object(value, PROFILE_FIELDS, "ENDPOINT_PROFILE")
    if accepted["schema_version"] != "P05_T12_MODEL_API_ENDPOINT_PROFILE_V1":
        raise RelayContractError("ENDPOINT_PROFILE_SCHEMA_VERSION")
    for field in (
        "profile_id", "revision", "protocol_revision", "endpoint_operator",
        "claimed_upstream_provider", "logical_model_id",
        "alias_revision", "route_id", "region", "egress", "dns_proxy_profile",
        "billing_mode", "usage_source", "cost_source", "currency", "access_mode",
        "credential_binding_id", "credential_binding_revision",
        "qualification_report_ref",
    ):
        _identifier(accepted[field], field) if field not in {"qualification_report_ref"} else _text(accepted[field], field)
    _model_alias(accepted["request_model_alias"], "request_model_alias")
    if accepted["status"] not in PROFILE_STATUSES:
        raise RelayContractError("PROFILE_STATUS_INVALID")
    if accepted["transport_kind"] not in TRANSPORT_KINDS:
        raise RelayContractError("TRANSPORT_KIND_INVALID")
    if accepted["relay_mode"] not in RELAY_MODES:
        raise RelayContractError("RELAY_MODE_INVALID")
    if accepted["protocol_family"] not in PROTOCOLS:
        raise RelayContractError("PROTOCOL_FAMILY_INVALID")
    _string_list(accepted["capabilities"], "capabilities")
    _string_list(accepted["allowed_roles"], "allowed_roles")
    accepted["origin"] = _origin(accepted["origin"], transport_kind=accepted["transport_kind"])
    if accepted["scheme"] != urlsplit(accepted["origin"]).scheme:
        raise RelayContractError("ORIGIN_SCHEME_MISMATCH")
    path = _text(accepted["path_template"], "path_template")
    if not path.startswith("/") or ".." in path or "?" in path or "#" in path or "://" in path:
        raise RelayContractError("PATH_TEMPLATE_INVALID")
    _string_list(accepted["allowed_query_keys"], "allowed_query_keys", nonempty=False)
    if accepted["redirect_policy"] != "DENY":
        raise RelayContractError("REDIRECT_POLICY_UNSAFE")
    allowed_origins = _string_list(accepted["allowed_origins"], "allowed_origins")
    normalized_allowed = [
        _origin(item, transport_kind=accepted["transport_kind"], field="allowed_origins")
        for item in allowed_origins
    ]
    if normalized_allowed != [accepted["origin"]]:
        raise RelayContractError("ALLOWED_ORIGIN_SET_INVALID")
    credential = _text(accepted["credential_ref"], "credential_ref")
    if not ENV_RE.fullmatch(credential):
        raise RelayContractError("CREDENTIAL_REF_INVALID")
    if accepted["auth_scheme"] not in {"Bearer", "x-api-key", "x-goog-api-key"}:
        raise RelayContractError("AUTH_SCHEME_INVALID")
    if accepted["header_name"] not in {"Authorization", "x-api-key", "x-goog-api-key"}:
        raise RelayContractError("AUTH_HEADER_INVALID")
    for field in ("behavior_hash", "request_contract_hash", "output_contract_hash"):
        accepted[field] = _sha(accepted[field], field)
    valid_from = _timestamp(accepted["valid_from"], "valid_from")
    expires_at = _timestamp(accepted["expires_at"], "expires_at")
    if valid_from >= expires_at:
        raise RelayContractError("PROFILE_VALIDITY_WINDOW_INVALID")
    _string_list(accepted["revocation_conditions"], "revocation_conditions")
    dimension = accepted["expected_embedding_dimension"]
    if "embedding" in accepted["capabilities"]:
        if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
            raise RelayContractError("EXPECTED_EMBEDDING_DIMENSION_INVALID")
    elif dimension is not None:
        raise RelayContractError("UNUSED_EMBEDDING_DIMENSION_FORBIDDEN")
    score_range = accepted["rerank_score_range"]
    if "rerank" in accepted["capabilities"]:
        if (
            not isinstance(score_range, list) or len(score_range) != 2
            or any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) for item in score_range)
            or score_range[0] >= score_range[1]
        ):
            raise RelayContractError("RERANK_SCORE_RANGE_INVALID")
    elif score_range is not None:
        raise RelayContractError("UNUSED_RERANK_SCORE_RANGE_FORBIDDEN")
    accepted["profile_fingerprint"] = canonical_sha256(accepted)
    return accepted


def validate_credential_binding(
    value: Mapping[str, Any], profile: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    accepted = _object(value, BINDING_FIELDS, "CREDENTIAL_BINDING")
    if accepted["schema_version"] != "P05_T12_CREDENTIAL_ENDPOINT_BINDING_V1":
        raise RelayContractError("CREDENTIAL_BINDING_SCHEMA_VERSION")
    _identifier(accepted["credential_binding_id"], "credential_binding_id")
    _identifier(accepted["revision"], "revision")
    _identifier(accepted["endpoint_operator"], "endpoint_operator")
    ref = _text(accepted["credential_ref"], "credential_ref")
    if not ENV_RE.fullmatch(ref):
        raise RelayContractError("CREDENTIAL_REF_INVALID")
    if accepted["binding_kind"] not in {"direct", "relay", "self_hosted_relay_client"}:
        raise RelayContractError("CREDENTIAL_BINDING_KIND_INVALID")
    transport = {
        "direct": "DIRECT_PROVIDER",
        "relay": "MODEL_API_RELAY",
        "self_hosted_relay_client": "SELF_HOSTED_LOOPBACK_RELAY",
    }[accepted["binding_kind"]]
    accepted["allowed_origin"] = _origin(accepted["allowed_origin"], transport_kind=transport, field="allowed_origin")
    if accepted["auth_scheme"] not in {"Bearer", "x-api-key", "x-goog-api-key"}:
        raise RelayContractError("AUTH_SCHEME_INVALID")
    if accepted["header_name"] not in {"Authorization", "x-api-key", "x-goog-api-key"}:
        raise RelayContractError("AUTH_HEADER_INVALID")
    if profile is not None:
        checked = validate_endpoint_profile({k: v for k, v in profile.items() if k in PROFILE_FIELDS})
        if accepted["allowed_origin"] != checked["origin"]:
            raise RelayContractError("CREDENTIAL_ORIGIN_MISMATCH")
        if accepted["credential_binding_id"] != checked["credential_binding_id"]:
            raise RelayContractError("CREDENTIAL_BINDING_ID_MISMATCH")
        if accepted["revision"] != checked["credential_binding_revision"]:
            raise RelayContractError("CREDENTIAL_BINDING_REVISION_MISMATCH")
        if accepted["endpoint_operator"] != checked["endpoint_operator"]:
            raise RelayContractError("CREDENTIAL_OPERATOR_MISMATCH")
        if transport != checked["transport_kind"]:
            raise RelayContractError("CREDENTIAL_TRANSPORT_MISMATCH")
        if accepted["credential_ref"] != checked["credential_ref"]:
            raise RelayContractError("CREDENTIAL_REF_MISMATCH")
        if accepted["auth_scheme"] != checked["auth_scheme"] or accepted["header_name"] != checked["header_name"]:
            raise RelayContractError("CREDENTIAL_AUTH_MISMATCH")
    return accepted


def validate_model_alias_binding(value: Mapping[str, Any]) -> dict[str, Any]:
    accepted = _object(value, ALIAS_FIELDS, "MODEL_ALIAS_BINDING")
    if accepted["schema_version"] != "P05_T12_MODEL_ALIAS_BINDING_V1":
        raise RelayContractError("MODEL_ALIAS_SCHEMA_VERSION")
    for field in (
        "logical_model_id", "alias_revision",
        "endpoint_operator", "claimed_upstream_provider",
    ):
        _identifier(accepted[field], field)
    _model_alias(accepted["request_model_alias"], "request_model_alias")
    if accepted["identity_threshold"] not in IDENTITY_THRESHOLDS:
        raise RelayContractError("IDENTITY_THRESHOLD_INVALID")
    _string_list(accepted["fallback_aliases"], "fallback_aliases", nonempty=False)
    if accepted["fallback_aliases"]:
        raise RelayContractError("MODEL_FALLBACK_ALIASES_FORBIDDEN")
    if accepted["pool_identity"] is not None:
        _identifier(accepted["pool_identity"], "pool_identity")
    if accepted["selection_policy"] not in {"EXACT_MODEL", "EXPLICIT_POOL"}:
        raise RelayContractError("MODEL_SELECTION_POLICY_INVALID")
    if accepted["selection_policy"] == "EXACT_MODEL" and accepted["pool_identity"] is not None:
        raise RelayContractError("EXACT_MODEL_POOL_FORBIDDEN")
    return accepted


def validate_protocol_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    accepted = _object(value, MANIFEST_FIELDS, "PROTOCOL_MANIFEST")
    if accepted["schema_version"] != "P05_T12_PROTOCOL_ADAPTER_MANIFEST_V1":
        raise RelayContractError("PROTOCOL_MANIFEST_SCHEMA_VERSION")
    _identifier(accepted["adapter_id"], "adapter_id")
    if accepted["protocol_family"] not in PROTOCOLS:
        raise RelayContractError("PROTOCOL_FAMILY_INVALID")
    _identifier(accepted["protocol_revision"], "protocol_revision")
    _string_list(accepted["capabilities"], "capabilities")
    if accepted["relay_mode"] not in RELAY_MODES:
        raise RelayContractError("RELAY_MODE_INVALID")
    _string_list(accepted["allowed_headers"], "allowed_headers", nonempty=False)
    normalized_headers = [item.lower() for item in accepted["allowed_headers"]]
    if len(set(normalized_headers)) != len(normalized_headers):
        raise RelayContractError("HEADER_ALLOWLIST_DUPLICATE")
    for field in ("request_contract_hash", "output_contract_hash"):
        accepted[field] = _sha(accepted[field], field)
    size = accepted["max_response_bytes"]
    if isinstance(size, bool) or not isinstance(size, int) or not 1024 <= size <= 16 * 1024 * 1024:
        raise RelayContractError("MAX_RESPONSE_BYTES_INVALID")
    if not isinstance(accepted["streaming"], bool):
        raise RelayContractError("STREAMING_FLAG_INVALID")
    return accepted


@dataclass(frozen=True)
class ResolvedEndpointProfile:
    profile: dict[str, Any]
    credential_binding: dict[str, Any]
    model_alias_binding: dict[str, Any]
    protocol_manifest: dict[str, Any]


class EndpointProfileCatalog:
    def __init__(
        self,
        *,
        profiles: Sequence[Mapping[str, Any]],
        credential_bindings: Sequence[Mapping[str, Any]],
        model_alias_bindings: Sequence[Mapping[str, Any]],
        protocol_manifests: Sequence[Mapping[str, Any]],
    ):
        self._profiles = self._unique(profiles, validate_endpoint_profile, "profile_id", "PROFILE_DUPLICATE")
        self._bindings = self._unique(credential_bindings, validate_credential_binding, "credential_binding_id", "CREDENTIAL_BINDING_DUPLICATE")
        self._aliases = self._unique(model_alias_bindings, validate_model_alias_binding, "logical_model_id", "MODEL_ALIAS_DUPLICATE")
        self._manifests = self._unique(protocol_manifests, validate_protocol_manifest, "protocol_family", "PROTOCOL_MANIFEST_DUPLICATE")
        for item in self._profiles.values():
            binding = self._bindings.get(item["credential_binding_id"])
            alias = self._aliases.get(item["logical_model_id"])
            manifest = self._manifests.get(item["protocol_family"])
            if binding is None or alias is None or manifest is None:
                raise RelayContractError("PROFILE_DEPENDENCY_MISSING", [item["profile_id"]])
            validate_credential_binding(binding, item)
            if (
                alias["request_model_alias"] != item["request_model_alias"]
                or alias["alias_revision"] != item["alias_revision"]
                or alias["endpoint_operator"] != item["endpoint_operator"]
                or alias["claimed_upstream_provider"] != item["claimed_upstream_provider"]
            ):
                raise RelayContractError("PROFILE_ALIAS_BINDING_MISMATCH", [item["profile_id"]])
            if (
                manifest["protocol_revision"] != item["protocol_revision"]
                or manifest["relay_mode"] != item["relay_mode"]
                or not set(item["capabilities"]).issubset(manifest["capabilities"])
                or manifest["request_contract_hash"] != item["request_contract_hash"]
                or manifest["output_contract_hash"] != item["output_contract_hash"]
            ):
                raise RelayContractError("PROFILE_PROTOCOL_MANIFEST_MISMATCH", [item["profile_id"]])

    @classmethod
    def from_bundle(
        cls,
        path: Path,
        *,
        allowed_root: Path,
        expected_sha256: str,
    ) -> "EndpointProfileCatalog":
        root = Path(allowed_root).resolve()
        resolved = Path(path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise RelayContractError("PROFILE_BUNDLE_OUTSIDE_ALLOWED_ROOT") from exc
        if resolved.is_symlink() or not resolved.is_file():
            raise RelayContractError("PROFILE_BUNDLE_NOT_REGULAR_FILE")
        raw = resolved.read_bytes()
        if len(raw) > 2 * 1024 * 1024:
            raise RelayContractError("PROFILE_BUNDLE_OVERSIZE")
        import hashlib
        actual = hashlib.sha256(raw).hexdigest().upper()
        if actual != expected_sha256.upper():
            raise RelayContractError("PROFILE_BUNDLE_HASH_MISMATCH")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RelayContractError("PROFILE_BUNDLE_JSON_INVALID") from exc
        fields = {
            "schema_version", "profiles", "credential_bindings",
            "model_alias_bindings", "protocol_manifests",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise RelayContractError("PROFILE_BUNDLE_FIELDS_INVALID")
        if value["schema_version"] != "P05_T12_ENDPOINT_PROFILE_BUNDLE_V1":
            raise RelayContractError("PROFILE_BUNDLE_SCHEMA_VERSION")
        for field in fields - {"schema_version"}:
            if not isinstance(value[field], list):
                raise RelayContractError("PROFILE_BUNDLE_COLLECTION_INVALID", [field])
        return cls(
            profiles=value["profiles"],
            credential_bindings=value["credential_bindings"],
            model_alias_bindings=value["model_alias_bindings"],
            protocol_manifests=value["protocol_manifests"],
        )

    @staticmethod
    def _unique(values, validator, key, code):
        result = {}
        for value in values:
            accepted = validator(value)
            identity = accepted[key]
            if identity in result:
                raise RelayContractError(code, [identity])
            result[identity] = accepted
        return result

    def resolve(self, profile_id: str, *, role: str, capability: str, at: str) -> ResolvedEndpointProfile:
        item = self._profiles.get(profile_id)
        if item is None:
            raise RelayContractError("PROFILE_NOT_FOUND", [profile_id])
        if item["status"] not in {"QUALIFIED_DISABLED", "ACTIVE"}:
            raise RelayContractError("PROFILE_NOT_ELIGIBLE", [item["status"]])
        if role not in item["allowed_roles"]:
            raise RelayContractError("PROFILE_ROLE_MISMATCH", [role])
        if capability not in item["capabilities"]:
            raise RelayContractError("PROFILE_CAPABILITY_MISMATCH", [capability])
        instant = _timestamp(at, "at")
        if not (_timestamp(item["valid_from"], "valid_from") <= instant < _timestamp(item["expires_at"], "expires_at")):
            raise RelayContractError("PROFILE_EXPIRED_OR_NOT_YET_VALID")
        return ResolvedEndpointProfile(
            profile=immutable_copy(item),
            credential_binding=immutable_copy(self._bindings[item["credential_binding_id"]]),
            model_alias_binding=immutable_copy(self._aliases[item["logical_model_id"]]),
            protocol_manifest=immutable_copy(self._manifests[item["protocol_family"]]),
        )
