"""The document pipeline's folders and engines, set in this one place.

Every folder can be set by an environment variable; each default appears once, here. Two PDF engines:
  - PDF_PRIMARY = "marker": the main engine; if it is missing, the vault target stops with an error
  - PDF_SANDBOX_LIGHT = "pymupdf4llm": a light engine for digital PDFs in the sandbox only
The vault target uses the main engine, the sandbox target the light one; convert(engine=...) overrides.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from memorive.platform.data_folders import default_runtime_root, ops_root


def _resolve(default: Path, *env_names: str) -> Path:
    """The first of the variables that is set, otherwise default."""
    for name in env_names:
        v = os.environ.get(name)
        if v and v.strip():
            return Path(v)
    return default


# The four roots, each overridden by its variable. The desktop service binds all four to its profile
# (memorive/documents/workflow/executor.py); without that they lie under the runtime root that components use
# when nothing has bound them (memorive/platform/data_folders.py).
VAULT_ROOT = _resolve(default_runtime_root() / "vault", "MEMORIVE_VAULT_ROOT")  # the formal library
OPS_ROOT = ops_root()  # logs, caches, backups
SANDBOX_ROOT = _resolve(default_runtime_root() / "sandbox", "MEMORIVE_DOCUMENT_PROCESSING_SANDBOX_ROOT")
# The native HNSW index on Windows drops index files under a non-ASCII persist path, so the vector
# store has a root of its own that can be set to an ASCII path.
CHROMA_ROOT = _resolve(default_runtime_root() / "vector-store", "MEMORIVE_DOCUMENT_PROCESSING_CHROMA_ROOT")


def root_for(target: str = "sandbox") -> Path:
    """The root folder of a target: the sandbox root or the vault root."""
    if target == "sandbox":
        return SANDBOX_ROOT
    if target == "vault":
        return VAULT_ROOT
    raise ValueError(f"未知 target: {target!r}(应为 'sandbox' | 'vault')")


def library_root(target: str = "sandbox") -> Path:
    """The library root, where each paper's folder lies."""
    return root_for(target) / "文献库"


# ── the two PDF engines ──────────────────────────────────────────
PDF_PRIMARY = "marker"              # the main engine; if missing, an error says to install it
PDF_SANDBOX_LIGHT = "pymupdf4llm"   # the light engine, for digital PDFs in the sandbox only


def engine_for(fmt: str, target: str = "sandbox") -> str:
    """The engine for a format and target; convert(engine=...) can override it."""
    if fmt == "pdf":
        return PDF_SANDBOX_LIGHT if target == "sandbox" else PDF_PRIMARY
    exact_engines = {
        "txt": "markitdown",
        "md": "passthrough",
        "csv": "markitdown",
        "html": "markitdown",
        "docx": "markitdown",
        "pptx": "markitdown",
        "xlsx": "markitdown",
        "doc": "markitdown",
        "ppt": "markitdown",
        "xls": "markitdown",
        "jpeg": "model_gateway_ocr",
        "png": "model_gateway_ocr",
        "tiff": "model_gateway_ocr",
        # Compatibility for already-materialized pre-Intake RawObject values. New
        # ingest never emits this generic key.
        "image": "model_gateway_ocr",
    }
    try:
        return exact_engines[fmt]
    except KeyError as exc:
        raise ValueError(
            f"未知格式: {fmt!r};只允许 DOCUMENT-FORMATS 冻结的 14 种格式"
        ) from exc


# ── whether OCR through the gateway is ready ─────────────────────────────
def _bool_setting(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true/false，实际为 {raw!r}")


# Off by default; switched on only in an isolated acceptance run or an approved environment, so the
# code being present does not mean it is in use.
OCR_VIA_MODEL_GATEWAY_READY = _bool_setting("MEMORIVE_DOCUMENT_PROCESSING_OCR_VIA_MODEL_GATEWAY_READY", False)
OCR_PAGE_RENDER_DPI = int(os.environ.get("MEMORIVE_DOCUMENT_PROCESSING_OCR_PAGE_RENDER_DPI") or 200)
if not 72 <= OCR_PAGE_RENDER_DPI <= 400:
    raise ValueError("MEMORIVE_DOCUMENT_PROCESSING_OCR_PAGE_RENDER_DPI 必须在 72..400")

# ── the threshold for a likely scan ─────────────────────────────
# Fewer characters per page than this from the digital engine: probably a scan or picture PDF, and an
# empty RawMD is refused. 50 per page by default (real digital pages have far more, scans about 0);
# MEMORIVE_DOCUMENT_PROCESSING_SCAN_MIN_CHARS_PER_PAGE changes it. A genuinely short document (a
# supplement, a one-page abstract) is let through by a person with convert(allow_short=True).
SCAN_SUSPECT_MIN_CHARS_PER_PAGE = int(os.environ.get("MEMORIVE_DOCUMENT_PROCESSING_SCAN_MIN_CHARS_PER_PAGE") or 50)


def _ratio_setting(name: str, default: float) -> float:
    """A threshold between 0 and 1 from the environment; an invalid value raises, so the check is never
    switched off silently."""
    raw = os.environ.get(name)
    try:
        value = float(raw) if raw and raw.strip() else default
    except ValueError as exc:
        raise ValueError(f"{name} 必须是 0～1 之间的数字，实际为 {raw!r}") from exc
    if not 0 < value <= 1:
        raise ValueError(f"{name} 必须满足 0 < value <= 1，实际为 {value!r}")
    return value


# ── PDF text damage (U+FFFD only) ─────────────────────────────────
# Measured on 10 frozen sample papers: the 8 readable ones had 0 to 0.0521% replacement characters
# and no bad page; VanDerZee2001 had 91.4050% and 8 of 8 pages bad; Guo2011 44 of 200 pages bad.
# The same threshold applies to the whole text and to each page; with enough bad pages the whole
# paper stops.
TEXT_REPLACEMENT_FAIL_RATIO = _ratio_setting(
    "MEMORIVE_DOCUMENT_PROCESSING_TEXT_REPLACEMENT_FAIL_RATIO", 0.20)
CORRUPT_PAGE_FRACTION_FAIL_RATIO = _ratio_setting(
    "MEMORIVE_DOCUMENT_PROCESSING_CORRUPT_PAGE_FRACTION_FAIL_RATIO", 0.20)

# ── Chroma collection names (the sandbox has its own) ──
COLLECTION = {"sandbox": "memorive_sandbox", "vault": "memorive_main"}


def chroma_dir(target: str = "sandbox") -> Path:
    """Chroma's folder, outside the library on an ASCII-only path: {CHROMA_ROOT}/chroma-sandbox-v2/ for
    the sandbox, {CHROMA_ROOT}/chroma-vault/ for the vault. MEMORIVE_DOCUMENT_PROCESSING_CHROMA_ROOT
    moves the root; tests can set each folder directly. Sandbox and vault differ in both folder and
    collection (memorive_sandbox, memorive_main)."""
    if target == "sandbox":
        v = os.environ.get("MEMORIVE_DOCUMENT_PROCESSING_SANDBOX_CHROMA_DIR")
        return Path(v) if v and v.strip() else CHROMA_ROOT / "chroma-sandbox-v2"
    if target == "vault":
        v = os.environ.get("MEMORIVE_DOCUMENT_PROCESSING_CHROMA_DIR")
        return Path(v) if v and v.strip() else CHROMA_ROOT / "chroma-vault"
    raise ValueError(f"未知 target: {target!r}(应为 'sandbox' | 'vault')")


def now_iso() -> str:
    """Local time with its offset, to the second, as the runtime log writes it."""
    return datetime.now().astimezone().isoformat(timespec="seconds")
