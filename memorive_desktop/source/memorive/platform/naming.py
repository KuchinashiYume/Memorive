"""How a paper's files and folders are named, from its title and paper id.

Pure string functions, used by document processing (convert, clean, chunk), card distillation,
the library, the folder policy and research recommendations: sanitize_title, truncate_title,
display_title, make_paper_id, folder_name, file_name. The paper id registry, which decides ids
that collide, is memorive/documents/processing/registration.py.
"""
from __future__ import annotations

import re

# Of the nine characters Windows forbids: ':' becomes ' -', '/' becomes '_', the rest (\ * ? " < > |) are removed.
_ILLEGAL_REMOVE = set('\\*?"<>|')
TITLE_MAX = 60


def sanitize_title(title: str) -> str:
    """Replace the colon with ' -' and the slash with '_', remove the other forbidden characters, collapse spaces."""
    title = title.replace(":", " -").replace("/", "_")
    title = "".join(c for c in title if c not in _ILLEGAL_REMOVE)
    return re.sub(r"\s+", " ", title).strip()


def truncate_title(title: str, limit: int = TITLE_MAX) -> str:
    """Cut a long title at a word boundary near limit characters and add '…' (the card keeps the full title)."""
    if len(title) <= limit:
        return title
    cut = title[:limit].rsplit(" ", 1)[0].rstrip()
    return (cut or title[:limit].rstrip()) + "…"


def display_title(title: str) -> str:
    """The title used in folder and file names: sanitized, then truncated."""
    return truncate_title(sanitize_title(title))


def make_paper_id(surname: str, year: str | int, suffix: str = "") -> str:
    """paper_id = surname + year (+ a suffix after a collision), without brackets. The caller decides the surname's spelling."""
    return f"{surname}{year}{suffix}"


def folder_name(paper_id: str, display: str) -> str:
    """Folder name: [paper_id] title (display is already sanitized and truncated)."""
    return f"[{paper_id}] {display}"


def file_name(kind: str, paper_id: str, display: str, ext: str) -> str:
    """File name: [kind][paper_id] title.ext."""
    return f"[{kind}][{paper_id}] {display}.{ext}"
