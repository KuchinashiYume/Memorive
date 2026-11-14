"""Owned-process, local-only Office legacy bridge."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .models import DocumentConversionError


_OUTPUT_SUFFIX = {"doc": ".docx", "ppt": ".pptx", "xls": ".xlsx"}


def convert_legacy_to_ooxml(source: Path, *, format_key: str, output_dir: Path, timeout_seconds: int = 60) -> tuple[Path, dict]:
    if format_key not in _OUTPUT_SUFFIX:
        raise DocumentConversionError("OFFICE_BRIDGE_FORMAT_INVALID", f"unsupported legacy key: {format_key}")
    source = source.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"bridge_output{_OUTPUT_SUFFIX[format_key]}"
    script = Path(__file__).with_name("office_legacy_bridge.ps1")
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-SourcePath",
        str(source),
        "-OutputPath",
        str(output),
        "-FormatKey",
        format_key,
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise DocumentConversionError(
            "OFFICE_BRIDGE_TIMEOUT",
            f"Office bridge exceeded {timeout_seconds}s",
            details={"format_key": format_key},
        ) from exc
    if completed.returncode != 0:
        raise DocumentConversionError(
            "OFFICE_BRIDGE_FAILED",
            "Office legacy bridge failed",
            details={
                "format_key": format_key,
                "returncode": completed.returncode,
                "stderr": completed.stderr[-2000:],
                "stdout": completed.stdout[-2000:],
            },
        )
    try:
        receipt = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DocumentConversionError(
            "OFFICE_BRIDGE_RECEIPT_INVALID",
            "Office bridge did not return one JSON receipt",
            details={"stdout": completed.stdout[-2000:]},
        ) from exc
    if receipt.get("status") != "PASS" or not output.is_file():
        raise DocumentConversionError("OFFICE_BRIDGE_OUTPUT_MISSING", "Office bridge reported success without output")
    return output, receipt
