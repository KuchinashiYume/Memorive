from __future__ import annotations

import re

from model_gateway.execution_core.contracts import canonical_sha256

from .contracts import ModelConfigurationError


ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")


def legacy_env_name_reference(env_name: str) -> dict:
    """Project an existing env *name* without reading the environment or its value."""
    if not isinstance(env_name, str) or not ENV_NAME_RE.fullmatch(env_name):
        raise ModelConfigurationError("LEGACY_ENV_NAME_INVALID")
    payload = {
        "schema_version": "MODEL_CONFIGURATION_LEGACY_ENV_REFERENCE_V1",
        "credential_ref": f"REF:LEGACY_ENV:{env_name}",
        "env_name": env_name,
        "secret_value_read": False,
        "resolvable_in_analysis": False,
        "migration_status": "REFERENCE_ONLY_REAL_BACKEND_NOT_ASSESSED",
    }
    payload["projection_sha256"] = canonical_sha256(payload)
    return payload


__all__ = ["legacy_env_name_reference"]
