"""The current time as the desktop writes it into receipts: UTC, ISO 8601 with microseconds and a Z.

Other packages keep their own formats (seconds only, +00:00); they are not merged here, because the
stored strings would change.
"""
from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
