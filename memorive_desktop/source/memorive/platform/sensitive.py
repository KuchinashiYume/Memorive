"""The check that keeps secrets out of what the app writes: scan_sensitive refuses a value whose keys
name a secret (api key, password, token, gold answers and the like) or whose strings look like one
(sk-..., GitHub tokens, private keys).

The settings (memorive_settings/contracts.py) and model execution run it on what they save.
memorive_current_task/actions.py and memorive_messages/privacy.py have rules of their own and do not
use this one.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

SENSITIVE_KEY = re.compile(
    r"(?i)^(api[_-]?key|authorization|cookie|credential[_-]?value|password|secret|token|private[_-]?payload|gold|holdout)$"
)
SENSITIVE_VALUE = (
    re.compile(r"(?i)(?:^|[^A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def scan_sensitive(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            rendered = str(key)
            if SENSITIVE_KEY.fullmatch(rendered):
                raise ValueError(f"SENSITIVE_KEY_REJECTED:{path}.{rendered}")
            scan_sensitive(child, f"{path}.{rendered}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan_sensitive(child, f"{path}[{index}]")
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in SENSITIVE_VALUE):
            raise ValueError(f"SENSITIVE_VALUE_REJECTED:{path}")
