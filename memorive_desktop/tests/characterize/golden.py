"""Helpers for characterization tests: envelope-free normalization and golden files."""
import json
import os
import re
from pathlib import Path

_HEX = re.compile(r"[0-9a-fA-F]{64}")


def envelope_hashes(value, found=None):
    """Collect the hex values of every content_hash envelope inside value."""
    found = set() if found is None else found
    if isinstance(value, dict):
        envelope = value.get("content_hash")
        if isinstance(envelope, dict) and isinstance(envelope.get("value"), str):
            found.add(envelope["value"].lower())
        for item in value.values():
            envelope_hashes(item, found)
    elif isinstance(value, (list, tuple)):
        for item in value:
            envelope_hashes(item, found)
    return found


def strip_envelopes(value, hashes=None):
    """Drop content_hash keys and replace values equal to an envelope hash with a marker."""
    hashes = envelope_hashes(value) if hashes is None else hashes
    if isinstance(value, dict):
        return {key: strip_envelopes(item, hashes) for key, item in sorted(value.items()) if key != "content_hash"}
    if isinstance(value, (list, tuple)):
        return [strip_envelopes(item, hashes) for item in value]
    if isinstance(value, str):
        return _HEX.sub(lambda m: "<envelope>" if m.group(0).lower() in hashes else m.group(0), value)
    return value


def outcome(function, *args, **kwargs):
    """Call function; return its envelope-free result, or the error type and message."""
    try:
        return {"result": strip_envelopes(function(*args, **kwargs))}
    except Exception as error:  # noqa: BLE001 - the error is part of the behaviour
        return {"error": type(error).__name__, "message": strip_envelopes(str(error))}


def rejected_as(function, *args, **kwargs):
    """Like outcome, but keep only the error type: for checks that today compare
    envelope hashes, where the rejection must stay but its wording may change."""
    result = outcome(function, *args, **kwargs)
    return {"error": result["error"]} if "error" in result else result


def check(name, actual, folder):
    """Compare actual with folder/golden/<name>.json (write it when asked to)."""
    path = Path(folder) / "golden" / f"{name}.json"
    text = json.dumps(actual, ensure_ascii=False, indent=1, sort_keys=True, default=repr) + "\n"
    if os.environ.get("MEMORIVE_TESTS_WRITE_GOLDEN") == "1" or not path.exists():
        if not path.exists() and os.environ.get("MEMORIVE_TESTS_WRITE_GOLDEN") != "1":
            raise AssertionError(f"golden file missing: {path}; run with MEMORIVE_TESTS_WRITE_GOLDEN=1 once")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf8", newline="\n")
        return
    expected = path.read_text(encoding="utf8")
    assert text == expected, f"{name} differs from {path.name}"
