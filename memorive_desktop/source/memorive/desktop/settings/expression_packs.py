"""Expression packs (表情包): the faces of the main window and the desk assistant come from the pack in use, which a
person installs, switches and removes in Settings › 外观与辅助功能 › 表情包.

A pack is a folder with pack.json and pictures, no program code. The kaomoji pack ships with Memorive
(product/desktop/assets/expression_packs/kaomoji); a person's own packs are copied into the profile folder
expression_packs. The format is written up for pack makers in
product/desktop/assets/expression_packs/表情包制作说明.md.

Called by desktop/api/expression_packs.py (the window methods behind the settings panel and the main window's
faces) and by product/desktop/app.py (the desk assistant's faces).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Any, Callable

FORMAT = "MemoriveExpressionPack-v1"
CHOICE_SCHEMA = "ExpressionPackChoice-v1"
BUILTIN_DEFAULT = "kaomoji"  # in use until the person chooses; the installer's check box ships it or not

# Where each position shows, and the position used when a pack leaves one out. `loading` has none: without it
# the main window alternates `open` and `closed` while a page waits.
POSITIONS = ("idle", "open", "closed", "greeting", "interaction", "question", "working", "success", "saved",
             "error", "empty", "message", "drag", "sleep", "loading")
FALLBACK = {"open": "idle", "closed": "open", "greeting": "interaction", "interaction": "idle",
            "question": "interaction", "working": "idle", "success": "idle", "saved": "success", "error": "idle",
            "empty": "idle", "message": "interaction", "drag": "interaction", "sleep": "closed"}

IMAGE_TYPES = {".png": "image/png", ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
               ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".bmp": "image/bmp"}
PET_TYPES = frozenset({".png", ".gif", ".jpg", ".jpeg", ".bmp"})  # what the desk assistant's drawing (GDI+) reads

# Limits that keep one pack from stalling the window: each picture is read whole into the page.
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_TEXT = 40
MAX_VARIANTS = 32
MAX_FRAMES = 240
FRAME_MS = (20, 10_000)
_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")


class PackError(ValueError):
    """Why a pack cannot be installed or used: a code the settings panel words, and the field or file it is about."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}:{detail}" if detail else code)
        self.code = code
        self.detail = detail


def _short_text(value: Any, limit: int, code: str, detail: str = "") -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit \
            or any(unicodedata.category(c).startswith("C") for c in value):
        raise PackError(code, detail)
    return unicodedata.normalize("NFC", value)


def _picture(root: Path, name: Any, files: set[str]) -> str:
    """A picture of the pack: a path inside the pack folder, of a supported type and size."""
    if not isinstance(name, str) or not name or "\\" in name or name.startswith("/") or ":" in name:
        raise PackError("PACK_FILE_OUTSIDE", str(name))
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or ".." in Path(name).parts:
        raise PackError("PACK_FILE_OUTSIDE", name)
    if path.suffix.lower() not in IMAGE_TYPES:
        raise PackError("PACK_FILE_TYPE", name)
    if not path.is_file():
        raise PackError("PACK_FILE_MISSING", name)
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise PackError("PACK_FILE_TOO_LARGE", name)
    files.add(name)
    return name


def _variant(root: Path, position: str, value: Any, files: set[str]) -> dict[str, Any]:
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            raise PackError("PACK_TEXT_TOO_LONG", position)
        return {"text": _short_text(value, MAX_TEXT, "PACK_LOOK_INVALID", position)}
    if isinstance(value, dict) and set(value) == {"image"}:
        return {"image": _picture(root, value["image"], files)}
    if isinstance(value, dict) and "frames" in value and set(value) <= {"frames", "ms", "loop"}:
        frames, ms, loop = value["frames"], value.get("ms", 120), value.get("loop", True)
        if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_FRAMES or type(loop) is not bool:
            raise PackError("PACK_LOOK_INVALID", position)
        if not isinstance(ms, list):
            ms = [ms] * len(frames)
        if len(ms) != len(frames) or any(type(m) is not int or not FRAME_MS[0] <= m <= FRAME_MS[1] for m in ms):
            raise PackError("PACK_LOOK_INVALID", position)
        return {"frames": [_picture(root, name, files) for name in frames], "ms": ms, "loop": loop}
    raise PackError("PACK_LOOK_INVALID", position)


def read_pack(folder: Path) -> dict[str, Any]:
    """The pack in folder, checked: its name, text style and looks (each position a list of variants, each variant
    {"text"} or {"image"} or {"frames", "ms", "loop"}). Fields this version does not know are left out."""
    root = Path(folder)
    try:
        raw = json.loads((root / "pack.json").read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise PackError("PACK_JSON_MISSING") from None
    except (OSError, ValueError):
        raise PackError("PACK_JSON_INVALID") from None
    if not isinstance(raw, dict):
        raise PackError("PACK_JSON_INVALID")
    if raw.get("format") != FORMAT:
        raise PackError("PACK_FORMAT_UNSUPPORTED")
    if not isinstance(raw.get("id"), str) or not _ID.fullmatch(raw["id"]):
        raise PackError("PACK_ID_INVALID")
    pack: dict[str, Any] = {"id": raw["id"], "name": _short_text(raw.get("name"), 64, "PACK_FIELD_INVALID", "name")}
    for field in ("author", "version"):
        if field in raw:
            pack[field] = _short_text(raw[field], 64, "PACK_FIELD_INVALID", field)
    style = raw.get("text_style", {})
    if not isinstance(style, dict) or not set(style) <= {"font", "color"}:
        raise PackError("PACK_FIELD_INVALID", "text_style")
    if "font" in style and (not isinstance(style["font"], str) or not style["font"].strip() or len(style["font"]) > 200
                            or any(c in style["font"] for c in ";{}<>\\") or not style["font"].isprintable()):
        raise PackError("PACK_FIELD_INVALID", "text_style.font")
    if "color" in style and (not isinstance(style["color"], str) or not _COLOR.fullmatch(style["color"])):
        raise PackError("PACK_FIELD_INVALID", "text_style.color")
    pack["text_style"] = dict(style)
    looks = raw.get("looks")
    if not isinstance(looks, dict):
        raise PackError("PACK_FIELD_INVALID", "looks")
    if "idle" not in looks:
        raise PackError("PACK_IDLE_MISSING")
    files: set[str] = set()
    pack["looks"] = {}
    for position in POSITIONS:
        if position not in looks:
            continue
        value = looks[position]
        variants = value if isinstance(value, list) else [value]
        if not 1 <= len(variants) <= MAX_VARIANTS:
            raise PackError("PACK_LOOK_INVALID", position)
        pack["looks"][position] = [_variant(root, position, item, files) for item in variants]
    pack["files"] = sorted(files)
    return pack


def resolved_looks(pack: dict[str, Any]) -> dict[str, Any]:
    """Every position's variants, filled in from FALLBACK where the pack leaves one out (`loading` stays None)."""
    looks = dict(pack["looks"])
    for position in POSITIONS:
        name = position
        while name not in looks and name in FALLBACK:
            name = FALLBACK[name]
        looks[position] = looks.get(name)
    return looks


def to_recycle_bin(path: Path) -> None:
    """Remove a folder by moving it to the Windows Recycle Bin, so a pack removed by mistake can be restored."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    fo_delete, flags = 3, 0x0040 | 0x0010 | 0x0004 | 0x0400  # FOF_ALLOWUNDO, NOCONFIRMATION, SILENT, NOERRORUI
    operation = SHFILEOPSTRUCTW(None, fo_delete, str(path) + "\0", None, flags, False, None, None)
    if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation)) or operation.fAnyOperationsAborted:
        raise OSError("EXPRESSION_PACK_REMOVE_FAILED")


class ExpressionPacks:
    """The packs on this computer and the one in use.

    builtin_root holds the packs shipped with Memorive (read-only; removing one only hides it), user_root the packs
    the person installed (one folder each), choice_path the person's choice: the pack in use and the shipped packs
    they removed. remove_folder takes a user pack's folder away (the Recycle Bin by default)."""

    def __init__(self, builtin_root: Path, user_root: Path, choice_path: Path,
                 remove_folder: Callable[[Path], None] = to_recycle_bin):
        self.builtin_root, self.user_root, self.choice_path = Path(builtin_root), Path(user_root), Path(choice_path)
        self.remove_folder = remove_folder

    # The person's choice.
    def _choice(self) -> dict[str, Any]:
        try:
            value = json.loads(self.choice_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            value = None
        if not isinstance(value, dict) or value.get("schema_version") != CHOICE_SCHEMA:
            value = {"active": BUILTIN_DEFAULT, "removed_builtin": []}
        return {"active": value.get("active"), "removed_builtin": list(value.get("removed_builtin") or [])}

    def _save_choice(self, choice: dict[str, Any]) -> None:
        self.choice_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.choice_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"schema_version": CHOICE_SCHEMA, **choice}, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        os.replace(temporary, self.choice_path)

    # The packs.
    def _folders(self) -> list[tuple[Path, bool]]:
        found = []
        for root, builtin in ((self.builtin_root, True), (self.user_root, False)):
            if root.is_dir():
                found += [(path, builtin) for path in sorted(root.iterdir()) if path.is_dir() and not path.name.startswith(".")]
        return found

    def _packs(self) -> list[dict[str, Any]]:
        """Every pack folder: {"folder", "builtin", "pack"} or, for a folder that is not a usable pack, {"error"}.
        A shipped pack keeps its id; a person's pack with the same id is reported as taken."""
        rows, seen = [], set()
        for folder, builtin in self._folders():
            row: dict[str, Any] = {"folder": folder, "builtin": builtin}
            try:
                pack = read_pack(folder)
                if pack["id"] in seen:
                    raise PackError("PACK_ID_TAKEN", pack["id"])
                seen.add(pack["id"])
                row["pack"] = pack
            except PackError as error:
                row["error"] = {"code": error.code, "detail": error.detail}
            rows.append(row)
        return rows

    def _find(self, pack_id: str) -> dict[str, Any]:
        for row in self._packs():
            if "pack" in row and row["pack"]["id"] == pack_id:
                return row
        raise PackError("PACK_NOT_FOUND", str(pack_id))

    def state(self) -> dict[str, Any]:
        """What the settings panel lists: each pack (or why a folder is not one), which is in use, the user folder."""
        choice = self._choice()
        packs = []
        for row in self._packs():
            if "error" in row:
                packs.append({"folder": row["folder"].name, "builtin": row["builtin"], "error": row["error"]})
                continue
            pack = row["pack"]
            installed = not (row["builtin"] and pack["id"] in choice["removed_builtin"])
            packs.append({"id": pack["id"], "name": pack["name"], "author": pack.get("author", ""),
                          "version": pack.get("version", ""), "builtin": row["builtin"], "installed": installed,
                          "active": installed and pack["id"] == choice["active"],
                          "preview": pack["looks"]["idle"][0], "text_style": pack["text_style"]})
        return {"packs": packs, "active": next((p["id"] for p in packs if p.get("active")), None),
                "folder": str(self.user_root)}

    def active(self) -> dict[str, Any] | None:
        """The pack in use with every position resolved, and its folder; None when no pack is in use."""
        choice = self._choice()
        if not choice["active"] or choice["active"] in choice["removed_builtin"]:
            return None
        try:
            row = self._find(choice["active"])
        except PackError:
            return None
        pack = row["pack"]
        return {"id": pack["id"], "name": pack["name"], "text_style": pack["text_style"],
                "looks": resolved_looks(pack), "root": row["folder"]}

    def use(self, pack_id: str) -> dict[str, Any]:
        """Use a pack; a shipped pack that was removed is installed again."""
        self._find(pack_id)
        choice = self._choice()
        choice["active"] = pack_id
        choice["removed_builtin"] = [name for name in choice["removed_builtin"] if name != pack_id]
        self._save_choice(choice)
        return self.state()

    def remove(self, pack_id: str) -> dict[str, Any]:
        """Remove a pack: a shipped one is hidden (its files belong to the program), a person's folder goes to the
        Recycle Bin. When it was in use, no pack is in use afterwards."""
        row = self._find(pack_id)
        choice = self._choice()
        if row["builtin"]:
            choice["removed_builtin"] = sorted(set(choice["removed_builtin"]) | {pack_id})
        else:
            self.remove_folder(row["folder"])
        if choice["active"] == pack_id:
            choice["active"] = None
        self._save_choice(choice)
        return self.state()

    def install(self, folder: Path) -> dict[str, Any]:
        """Copy the pack in folder (pack.json and the pictures it names, nothing else) into the user folder, replacing
        an earlier version of the same pack, and use it."""
        source = Path(folder).resolve()
        pack = read_pack(source)
        for row in self._packs():
            if row["builtin"] and row.get("pack", {}).get("id") == pack["id"]:
                raise PackError("PACK_ID_TAKEN", pack["id"])
        target = self.user_root / pack["id"]
        if source != target.resolve():
            staging = self.user_root / f".installing-{pack['id']}"
            if staging.exists():
                shutil.rmtree(staging)
            for name in ["pack.json", *pack["files"]]:
                (staging / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / name, staging / name)
            if target.exists():
                self.remove_folder(target)
            os.replace(staging, target)
        state = self.use(pack["id"])
        return {**state, "installed": pack["id"]}

    def picture(self, pack_id: str, name: str) -> tuple[bytes, str]:
        """A picture the pack names, with its media type (only those: the page cannot read other files)."""
        row = self._find(pack_id)
        if name not in row["pack"]["files"]:
            raise PackError("PACK_FILE_MISSING", str(name))
        path = row["folder"] / name
        return path.read_bytes(), IMAGE_TYPES[path.suffix.lower()]
