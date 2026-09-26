from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


SHA256_RE = re.compile(r"^[0-9A-F]{64}$")


@dataclass(frozen=True)
class ComponentBinding:
    name: str
    path: str
    expected_sha256: str


class ComponentBindingViolation(ValueError):
    def __init__(self, code: str, component: str | None = None):
        super().__init__(code if component is None else f"{code}:{component}")
        self.code = code
        self.component = component


def bindings_from_policy(policy: Any, hash_fields: Sequence[str]) -> tuple[ComponentBinding, ...]:
    paths = getattr(policy, "component_paths", None)
    if not isinstance(paths, Mapping):
        raise ComponentBindingViolation("COMPONENT_PATH_MANIFEST_MISSING")
    expected_names = tuple(hash_fields)
    if set(paths) != set(expected_names):
        raise ComponentBindingViolation("COMPONENT_PATH_MANIFEST_EXACT_SET_MISMATCH")
    bindings: list[ComponentBinding] = []
    for name in expected_names:
        path = Path(str(paths[name]))
        digest = str(getattr(policy, name, "")).upper()
        if not path.is_absolute() or not SHA256_RE.fullmatch(digest):
            raise ComponentBindingViolation("COMPONENT_BINDING_INVALID", name)
        bindings.append(ComponentBinding(name, str(path), digest))
    return tuple(bindings)


def verify_component_bindings(bindings: Sequence[ComponentBinding]) -> str | None:
    if not bindings:
        return "COMPONENT_BINDING_MISSING"
    seen: set[str] = set()
    for binding in bindings:
        if binding.name in seen:
            return "COMPONENT_BINDING_DUPLICATE"
        seen.add(binding.name)
        path = Path(binding.path)
        if not path.is_absolute() or not path.is_file():
            return "COMPONENT_PATH_MISSING"
        actual = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        if actual != binding.expected_sha256.upper():
            return "COMPONENT_HASH_MISMATCH"
    return None
