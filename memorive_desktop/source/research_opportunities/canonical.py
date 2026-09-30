"""RFC 8785/I-JSON hashing shared only as a byte-level primitive.

Semantic producer and verifier logic remain in separate modules.  Sharing the
canonical byte primitive avoids two hash domains while preserving separate
semantic recomputation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from decimal import Decimal
from typing import Any, Mapping


_SHA256_RE = re.compile(r"^[A-F0-9]{64}$")


def _reject_lone_surrogates(value: str) -> None:
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError("RFC8785 JCS rejects lone UTF-16 surrogate code points")


def jcs_string(value: str) -> str:
    _reject_lone_surrogates(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def jcs_number(value: int | float) -> str:
    if isinstance(value, bool):
        raise TypeError("boolean is not a JCS number")
    if isinstance(value, int):
        # JSON numbers in RFC 8785 use the IEEE-754 binary64 domain.  Python's
        # parser retains an arbitrary-precision ``int`` token, so normalize it
        # into that domain and reject values that binary64 cannot represent
        # exactly.  Routing exact integers through the float serializer also
        # keeps int/float host representations byte-identical (for example
        # 2**53 and the Appendix-B 2**68 vector).
        try:
            binary64 = float(value)
        except OverflowError as exc:
            raise ValueError("integer is outside the finite IEEE-754 range") from exc
        if not math.isfinite(binary64) or int(binary64) != value:
            raise ValueError("integer is not exactly representable as IEEE-754 binary64")
        value = binary64
    if not math.isfinite(value):
        raise ValueError("RFC8785 JCS rejects NaN and infinity")
    if value == 0:
        return "0"
    rendered = repr(value).lower()
    magnitude = abs(value)
    if 1e-6 <= magnitude < 1e21:
        fixed = format(Decimal(rendered), "f")
        return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed
    if "e" not in rendered:
        rendered = format(Decimal(rendered).normalize(), "e")
    coefficient, exponent_text = rendered.split("e", 1)
    if coefficient.endswith(".0"):
        coefficient = coefficient[:-2]
    exponent = int(exponent_text)
    return f"{coefficient}e{'+' if exponent >= 0 else ''}{exponent}"


def jcs_serialize(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return jcs_string(value)
    if isinstance(value, (int, float)):
        return jcs_number(value)
    if isinstance(value, list):
        return "[" + ",".join(jcs_serialize(item) for item in value) + "]"
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("RFC8785 JCS object keys must be strings")
        for key in value:
            _reject_lone_surrogates(key)
        keys = sorted(value, key=lambda key: key.encode("utf-16-be"))
        return "{" + ",".join(
            f"{jcs_string(key)}:{jcs_serialize(value[key])}" for key in keys
        ) + "}"
    raise TypeError(f"unsupported RFC8785 JCS value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    return jcs_serialize(value).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def typed_payload_hash(value: Mapping[str, Any]) -> dict[str, str]:
    payload = deepcopy(dict(value))
    payload.pop("content_hash", None)
    return {
        "algorithm": "sha256",
        "hash_kind": "rfc8785_jcs_sha256",
        "scope": "PAYLOAD_EXCLUDING_CONTENT_HASH",
        "value": sha256_bytes(canonical_json_bytes(payload)),
    }


def with_content_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(value))
    result.pop("content_hash", None)
    result["content_hash"] = typed_payload_hash(result)
    return result


def content_hash_matches(value: Mapping[str, Any]) -> bool:
    supplied = value.get("content_hash")
    return isinstance(supplied, dict) and supplied == typed_payload_hash(value)


def external_hash_is_valid(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"algorithm", "hash_kind", "scope", "value"}
        and value.get("algorithm") == "sha256"
        and isinstance(value.get("hash_kind"), str)
        and bool(value["hash_kind"].strip())
        and isinstance(value.get("scope"), str)
        and bool(value["scope"].strip())
        and isinstance(value.get("value"), str)
        and bool(_SHA256_RE.fullmatch(value["value"]))
    )


def typed_hash_is_valid(value: Any) -> bool:
    return (
        external_hash_is_valid(value)
        and value["hash_kind"] == "rfc8785_jcs_sha256"
        and value["scope"] == "PAYLOAD_EXCLUDING_CONTENT_HASH"
    )
