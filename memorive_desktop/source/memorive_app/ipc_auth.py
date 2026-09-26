from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import time
from typing import Any, Callable, Mapping

from .contracts import canonical_json_bytes, immutable_copy


def _loopback(peer_host: str) -> str:
    try:
        address = ipaddress.ip_address(peer_host)
    except ValueError as exc:
        raise ValueError("IPC_PEER_ADDRESS_INVALID") from exc
    if not address.is_loopback:
        raise ValueError("IPC_PEER_NOT_LOOPBACK")
    return address.compressed


def compute_challenge_response(secret: bytes, challenge: Mapping[str, Any]) -> str:
    if not isinstance(secret, bytes) or len(secret) < 16:
        raise ValueError("IPC_SECRET_TOO_SHORT")
    return hmac.new(secret, canonical_json_bytes(dict(challenge)), hashlib.sha256).hexdigest().upper()


class LoopbackPeerAuthenticator:
    """Single-use HMAC challenge for a loopback IPC transport."""

    def __init__(self, secret: bytes, *, ttl_seconds: int = 30, clock: Callable[[], float] = time.time):
        if not isinstance(secret, bytes) or len(secret) < 16:
            raise ValueError("IPC_SECRET_TOO_SHORT")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds <= 0 or ttl_seconds > 300:
            raise ValueError("IPC_TTL_INVALID")
        self._secret = bytes(secret)
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._issued: dict[str, dict[str, Any]] = {}
        self._consumed: set[str] = set()

    def issue_challenge(self, peer_host: str) -> dict[str, Any]:
        peer = _loopback(peer_host)
        issued_at = int(self._clock())
        challenge = {
            "schema_version": "LoopbackPeerChallenge-v1",
            "peer_host": peer,
            "nonce": secrets.token_hex(32),
            "issued_at_unix": issued_at,
            "expires_at_unix": issued_at + self._ttl_seconds,
        }
        self._issued[challenge["nonce"]] = immutable_copy(challenge)
        return immutable_copy(challenge)

    def verify(self, peer_host: str, challenge: Mapping[str, Any], response: str) -> bool:
        try:
            peer = _loopback(peer_host)
        except ValueError:
            return False
        frozen = immutable_copy(challenge)
        nonce = frozen.get("nonce")
        if not isinstance(nonce, str) or nonce in self._consumed:
            return False
        if self._issued.get(nonce) != frozen:
            return False
        if frozen.get("peer_host") != peer or int(self._clock()) > frozen.get("expires_at_unix", -1):
            return False
        expected = compute_challenge_response(self._secret, frozen)
        if not isinstance(response, str) or not hmac.compare_digest(expected, response.upper()):
            return False
        self._consumed.add(nonce)
        self._issued.pop(nonce, None)
        return True


__all__ = ["LoopbackPeerAuthenticator", "compute_challenge_response"]
