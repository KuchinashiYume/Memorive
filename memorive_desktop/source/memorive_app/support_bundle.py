from __future__ import annotations

import re
from typing import Any, Mapping

from .contracts import canonical_json_bytes, canonical_sha256, immutable_copy, require_text, require_timestamp
from .errors import SupportBundleForbidden


ALLOWED_PROJECTIONS = frozenset({"build.json", "doctor.json", "jobs.json", "errors.json", "capabilities.json"})
REDACT_KEYS = frozenset({
    "absolute_path",
    "api_key",
    "authorization",
    "cookie",
    "credential_value",
    "evidence_ref",
    "host_name",
    "hostname",
    "machine_name",
    "password",
    "proxy_subscription",
    "schema_root",
    "source_path",
    "token",
    "user_name",
    "username",
})
FORBIDDEN_PATTERNS = (
    re.compile(r"(?i)(?:^|[^A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)private[_ -]?gold|private[_ -]?holdout|answer[_ -]?key|credential[_ -]?value"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def _scan(value: Any) -> list[str]:
    text = canonical_json_bytes(value).decode("utf-8")
    return [pattern.pattern for pattern in FORBIDDEN_PATTERNS if pattern.search(text)]


def _redact(value: Any, *, key: str | None = None) -> Any:
    if key in REDACT_KEYS:
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(k): _redact(v, key=str(k)) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class SupportBundleBuilder:
    def build(self, *, scope: str, redaction_profile: str, generated_at: str, projections: Mapping[str, Any]) -> dict[str, Any]:
        accepted_scope = require_text(scope, "scope")
        if redaction_profile != "PUBLIC_SUPPORT_V1":
            raise SupportBundleForbidden("unknown redaction profile")
        names = set(projections)
        if not names.issubset(ALLOWED_PROJECTIONS):
            raise SupportBundleForbidden("projection is not allowlisted")
        hits = _scan(projections)
        if hits:
            raise SupportBundleForbidden("forbidden content detected", patterns=hits)
        accepted = {name: _redact(immutable_copy(projections[name])) for name in sorted(names)}
        post_hits = _scan(accepted)
        if post_hits:
            raise SupportBundleForbidden("forbidden content remains after redaction", patterns=post_hits)
        members = []
        for name, value in accepted.items():
            encoded = canonical_json_bytes(value) + b"\n"
            members.append({"name": name, "bytes": len(encoded), "sha256": canonical_sha256(value)})
        descriptor = {
            "schema_version": "SupportBundlePreview-v1",
            "contract_revision": "1.0",
            "scope": accepted_scope,
            "redaction_profile": redaction_profile,
            "generated_at": require_timestamp(generated_at, "generated_at"),
            "members": members,
            "projections": accepted,
            "redaction_report": {"redacted_keys": sorted(REDACT_KEYS), "upload_authorized": False},
            "forbidden_scan_count": 0,
            "local_only": True,
            "upload_performed": False,
        }
        descriptor["bundle_fingerprint"] = canonical_sha256(descriptor)
        return descriptor


__all__ = ["SupportBundleBuilder"]
