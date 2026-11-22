"""The window methods of expression packs (表情包): ExpressionPacksMixin.

The main window's faces (ui/integrated/shared/expression-pack.js) and Settings › 外观与辅助功能 › 表情包
(ui/integrated/routes/settings/expression-packs.js) call these; the work is done by
memorive/desktop/settings/expression_packs.py. The desk assistant (product/desktop/app.py) reads the pack in use
through expression_pack_for_assistant and redraws its faces when attach_expression_pack_listener tells it the
choice changed.
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from memorive.desktop.settings.expression_packs import ExpressionPacks, PackError


def _refusal(error: PackError) -> dict[str, Any]:
    return {"status": "REFUSED", "code": error.code, "detail": error.detail}


class ExpressionPacksMixin:
    """Expression pack calls of ProductApi."""

    def _expression_packs(self) -> ExpressionPacks:
        packs = getattr(self, "_expression_pack_store", None)
        if packs is None:
            # Shipped packs: beside source/ in the packaged program, under product/desktop when run from source.
            base = self._source_root.parent
            shipped = base / "assets" if getattr(sys, "frozen", False) else base / "product" / "desktop" / "assets"
            packs = ExpressionPacks(shipped / "expression_packs", self._profile_path("EXPRESSION_PACKS"),
                                    self._profile_path("UI_STATE") / "expression_pack_choice_v1.json")
            self._expression_pack_store = packs
        return packs

    def attach_expression_pack_listener(self, listener: Callable[[], None]) -> None:
        """The desk assistant: called after the pack in use changed."""
        if callable(listener):
            self._expression_pack_listener = listener

    def _expression_pack_changed(self, state: dict[str, Any]) -> dict[str, Any]:
        listener = getattr(self, "_expression_pack_listener", None)
        if listener is not None:
            listener()
        return state

    def expression_packs_state(self) -> dict[str, Any]:
        return self._expression_packs().state()

    def expression_pack_active(self) -> dict[str, Any] | None:
        """The pack in use for the main window (pictures are fetched with expression_pack_image), or None."""
        pack = self._expression_packs().active()
        if pack is not None:
            pack.pop("root")
        return pack

    def expression_pack_for_assistant(self) -> dict[str, Any] | None:
        """The pack in use for the desk assistant: as for the main window, with the folder its pictures are in."""
        return self._expression_packs().active()

    def expression_pack_image(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            data, media_type = self._expression_packs().picture(str(request.get("id")), str(request.get("file")))
        except PackError as error:
            return _refusal(error)
        return {"status": "OK", "type": media_type, "data": base64.b64encode(data).decode("ascii")}

    def expression_pack_use(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._expression_pack_changed(self._expression_packs().use(str(request.get("id"))))
        except PackError as error:
            return _refusal(error)

    def expression_pack_remove(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._expression_pack_changed(self._expression_packs().remove(str(request.get("id"))))
        except PackError as error:
            return _refusal(error)

    def expression_pack_install(self) -> dict[str, Any]:
        """Install the pack in a folder the person picks; {"status": "CANCELLED"} when they pick none."""
        if self._window is None:
            return {"status": "CANCELLED"}
        import webview
        picked = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not picked:
            return {"status": "CANCELLED"}
        try:
            return self._expression_pack_changed(self._expression_packs().install(Path(picked[0])))
        except PackError as error:
            return _refusal(error)

    def expression_pack_open_guide(self) -> dict[str, Any]:
        """Open the pack makers' guide shipped beside the shipped packs, in Notepad (any computer has it)."""
        guide = self._expression_packs().builtin_root / "表情包制作说明.md"
        subprocess.Popen(["notepad.exe", str(guide)])
        return {"status": "OK"}

    def expression_pack_open_folder(self) -> dict[str, Any]:
        """Open the folder of installed packs in Explorer (a person can copy packs in by hand)."""
        folder = self._expression_packs().user_root
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))
        return {"status": "OK", "folder": str(folder)}
