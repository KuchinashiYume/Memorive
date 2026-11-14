from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class ComponentBinding:
    name: str
    path: str
    expected_sha256: str


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
