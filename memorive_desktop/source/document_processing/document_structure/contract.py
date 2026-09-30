"""Bounded source identities and coordinates for document structure v1."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from ..document_conversion.models import DocumentConversionError

SCHEMA = "MemoriveDocumentStructure-v1"
ENVELOPE = "MemoriveMarkerChunksEnvelope-v1"
RECEIPT = "MemoriveStructureReceipt-v1"
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_PAGES = 2000
MAX_BLOCKS = 100000
MAX_HTML_CHARS = 2 * 1024 * 1024


def fail(code: str, message: str):
    raise DocumentConversionError(code, message)


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_value(value, field="sha256") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", value):
        fail("STRUCTURE_HASH_INVALID", f"{field} must be a SHA256")
    return value.lower()


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            fail("STRUCTURE_DUPLICATE_JSON_KEY", f"Duplicate JSON key: {key[:80]}")
        out[key] = value
    return out


def read_json(path: Path) -> tuple[dict, bytes]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
    except OSError:
        fail("STRUCTURE_INPUT_UNAVAILABLE", "Cannot read structure JSON input")
    if len(raw) > MAX_JSON_BYTES:
        fail("STRUCTURE_INPUT_TOO_LARGE", "Structure JSON exceeds 32 MiB")
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique,
                           parse_constant=lambda _: fail("STRUCTURE_NONFINITE", "Nonfinite JSON number"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        fail("STRUCTURE_JSON_INVALID", f"Invalid structure JSON: {type(exc).__name__}")
    if not isinstance(value, dict):
        fail("STRUCTURE_JSON_INVALID", "Structure JSON root must be an object")
    return value, raw


def number(value) -> bool:
    return type(value) in (int, float) and -1e9 <= value <= 1e9 and math.isfinite(value)


def bbox(value, *, enclosing=None) -> list[float]:
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or not all(number(x) for x in value)):
        fail("STRUCTURE_BBOX_INVALID", "bbox needs four finite numbers")
    x0, y0, x1, y1 = value
    if x0 > x1 or y0 > y1:
        fail("STRUCTURE_BBOX_INVALID", "bbox bounds are inverted")
    if enclosing is not None:
        a, b, c, d = enclosing
        # Only accommodate subpixel rounding, never a different coordinate space.
        if x0 < a - 0.01 or y0 < b - 0.01 or x1 > c + 0.01 or y1 > d + 0.01:
            fail("STRUCTURE_BBOX_OUTSIDE_PAGE", "Block lies outside its declared page")
    return [float(x) for x in value]


def polygon(value, box) -> list[list[float]]:
    if not isinstance(value, list) or not 3 <= len(value) <= 64:
        fail("STRUCTURE_POLYGON_INVALID", "Polygon needs 3..64 points")
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2 or not all(number(x) for x in point):
            fail("STRUCTURE_POLYGON_INVALID", "Polygon points must be finite pairs")
        bbox([point[0], point[1], point[0], point[1]], enclosing=box)
    return [[float(x), float(y)] for x, y in value]


def rectangle(box) -> list[list[float]]:
    x0, y0, x1, y1 = box
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def structure_id(value: dict) -> str:
    return "Structure-" + digest(canonical({k: v for k, v in value.items() if k != "structure_id"}))[:32]


def validate_structure(value: dict):
    if value.get("schema_version") != SCHEMA:
        fail("STRUCTURE_SCHEMA_UNSUPPORTED", "Unsupported document structure schema")
    hash_value(value.get("source_sha256"))
    hash_value(value.get("rawmd_sha256"))
    if value.get("structure_id") != structure_id(value):
        fail("STRUCTURE_ID_MISMATCH", "Structure semantic identity differs")
    pages = value.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
        fail("STRUCTURE_PAGES_INVALID", "Invalid page list")
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or page.get("page_index") != index or page.get("physical_page") != index + 1:
            fail("STRUCTURE_PAGE_MAPPING_INVALID", "Physical pages must be complete and contiguous")
        box = bbox(page.get("bbox"))
        if box[0] >= box[2] or box[1] >= box[3]:
            fail("STRUCTURE_PAGE_MAPPING_INVALID", "Page must have positive area")
    blocks = value.get("blocks")
    if not isinstance(blocks, list) or len(blocks) > MAX_BLOCKS:
        fail("STRUCTURE_BLOCKS_INVALID", "Invalid block list")
    ids = set()
    for ordinal, block in enumerate(blocks):
        if not isinstance(block, dict):
            fail("STRUCTURE_BLOCKS_INVALID", "Block must be an object")
        page = block.get("page_index")
        if type(page) is not int or not 0 <= page < len(pages) or block.get("physical_page") != page + 1:
            fail("STRUCTURE_PAGE_MAPPING_INVALID", "Block has no matching page")
        if block.get("reading_order") != ordinal or not isinstance(block.get("block_id"), str):
            fail("STRUCTURE_BLOCKS_INVALID", "Block order/identity is invalid")
        if block["block_id"] in ids:
            fail("STRUCTURE_DUPLICATE_BLOCK", "Duplicate block identity")
        ids.add(block["block_id"])
        box = bbox(block.get("bbox"), enclosing=pages[page]["bbox"])
        polygon(block.get("polygon"), box)
