"""Discover supported browser executables without inspecting user profiles."""
from __future__ import annotations

import os
from pathlib import Path
import shutil

BROWSERS = (
    ("EDGE", "Microsoft Edge", "msedge.exe", "edge://extensions/", ("Microsoft", "Edge", "Application", "msedge.exe")),
    ("CHROME", "Google Chrome", "chrome.exe", "chrome://extensions/", ("Google", "Chrome", "Application", "chrome.exe")),
    ("BRAVE", "Brave", "brave.exe", "brave://extensions/", ("BraveSoftware", "Brave-Browser", "Application", "brave.exe")),
)
FAMILIES = frozenset(row[0] for row in BROWSERS)

def family(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().casefold()
    return next((key for key,label,exe,_,_ in BROWSERS if value in {key.casefold(),label.casefold(),exe.casefold()}), None)

def registered_executables(executable: str) -> list[Path]:
    if os.name != "nt":
        return []
    import winreg
    result = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{executable}", 0, winreg.KEY_READ | view) as key:
                    raw,_ = winreg.QueryValueEx(key, "")
                if isinstance(raw, str) and raw.strip():
                    result.append(Path(os.path.expandvars(raw.strip().strip('"'))))
            except OSError:
                continue
    return result

def discover() -> list[tuple[str, Path, str]]:
    result = []
    for _,label,exe,manager,suffix in BROWSERS:
        candidates = registered_executables(exe)
        if path := shutil.which(exe):
            candidates.append(Path(path))
        candidates.extend(Path(root,*suffix) for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA") if (root := os.environ.get(key)))
        for path in candidates:
            try:
                path = path.resolve()
                if path.name.casefold() != exe or not path.is_file():
                    continue
            except OSError:
                continue
            result.append((label,path,manager))
            break
    return result

def select(available: list[tuple[str, Path, str]], preferred: str | None = None):
    if preferred:
        target = family(preferred)
        if target is None:
            raise RuntimeError("BROWSER_SELECTION_REQUIRED")
        match = next((row for row in available if family(row[0]) == target), None)
        if match is None:
            raise RuntimeError("SELECTED_BROWSER_NOT_FOUND")
        return match
    if not available:
        raise RuntimeError("SUPPORTED_BROWSER_NOT_FOUND")
    return available[0]
