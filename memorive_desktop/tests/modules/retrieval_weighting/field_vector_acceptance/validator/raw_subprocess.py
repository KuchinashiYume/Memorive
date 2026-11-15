"""Raw-byte child-process capture for FIELD-VECTORS A/B validation runners.

This module is control-plane infrastructure only.  It never creates an A
attempt, consumes a ledger entry, or interprets a product result as PASS.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import os
from pathlib import Path
import subprocess
from typing import Mapping, Sequence


SAFE_INHERITED_ENV = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
)


class RawRunnerError(RuntimeError):
    """Base class for deterministic runner control-plane errors."""


class RawCaptureTypeError(RawRunnerError):
    """Raised when subprocess capture is not bytes under binary mode."""


class RawOutputBoundaryError(RawRunnerError):
    """Raised when a log path leaves its authorized output root."""


@dataclass(frozen=True)
class RawProcessReceipt:
    command: tuple[str, ...]
    cwd: str
    exit_code: int | None
    timed_out: bool
    start_error_type: str | None
    stdout_path: str
    stderr_path: str
    stdout_bytes: int
    stderr_bytes: int
    stdout_sha256: str
    stderr_sha256: str
    capture_mode: str = "raw_bytes"
    python_utf8: str = "1"
    python_io_encoding: str = "utf-8"
    python_hash_seed: str = "0"
    python_dont_write_bytecode: str = "1"

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable receipt without environment values."""

        payload = asdict(self)
        payload["command"] = list(self.command)
        return payload


def _assert_scoped(path: Path, allowed_root: Path) -> Path:
    root = allowed_root.resolve(strict=True)
    candidate = path.resolve(strict=False)
    if candidate != root and root not in candidate.parents:
        raise RawOutputBoundaryError(f"output path leaves authorized root: {candidate}")

    cursor = candidate.parent
    while cursor != root:
        if cursor.exists() and cursor.is_symlink():
            raise RawOutputBoundaryError(f"symlinked output parent is forbidden: {cursor}")
        if cursor.parent == cursor:
            raise RawOutputBoundaryError(f"unable to prove scoped output path: {candidate}")
        cursor = cursor.parent
    return candidate


def _write_new_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)


def _require_bytes(value: object, stream_name: str) -> bytes:
    if not isinstance(value, bytes):
        raise RawCaptureTypeError(
            f"{stream_name} capture must be bytes, got {type(value).__name__}"
        )
    return value


def _safe_env(extra_env: Mapping[str, str] | None) -> dict[str, str]:
    env = {name: os.environ[name] for name in SAFE_INHERITED_ENV if name in os.environ}
    if extra_env:
        for name, value in extra_env.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise TypeError("environment names and values must be strings")
            env[name] = value
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_raw(
    command: Sequence[str],
    *,
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    allowed_output_root: Path,
    timeout_seconds: float = 30.0,
    extra_env: Mapping[str, str] | None = None,
) -> RawProcessReceipt:
    """Execute one child and persist stdout/stderr as untouched bytes.

    Output files are create-only.  A timeout is represented in the receipt and
    preserves any captured bytes.  A process-start error also produces two
    empty create-only logs, making the failure boundary explicit.
    """

    argv = tuple(command)
    if not argv or not all(isinstance(item, str) and item for item in argv):
        raise ValueError("command must contain one or more non-empty strings")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    resolved_cwd = cwd.resolve(strict=True)
    out_path = _assert_scoped(stdout_path, allowed_output_root)
    err_path = _assert_scoped(stderr_path, allowed_output_root)
    if out_path == err_path:
        raise RawOutputBoundaryError("stdout and stderr paths must differ")
    if out_path.exists() or err_path.exists():
        raise FileExistsError("raw capture files are immutable and must not exist")

    stdout = b""
    stderr = b""
    exit_code: int | None = None
    timed_out = False
    start_error_type: str | None = None

    try:
        completed = subprocess.run(
            list(argv),
            cwd=resolved_cwd,
            env=_safe_env(extra_env),
            capture_output=True,
            check=False,
            text=False,
            timeout=timeout_seconds,
        )
        stdout = _require_bytes(completed.stdout, "stdout")
        stderr = _require_bytes(completed.stderr, "stderr")
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = b"" if exc.stdout is None else _require_bytes(exc.stdout, "stdout")
        stderr = b"" if exc.stderr is None else _require_bytes(exc.stderr, "stderr")
    except OSError as exc:
        start_error_type = type(exc).__name__

    _write_new_bytes(out_path, stdout)
    _write_new_bytes(err_path, stderr)
    return RawProcessReceipt(
        command=argv,
        cwd=str(resolved_cwd),
        exit_code=exit_code,
        timed_out=timed_out,
        start_error_type=start_error_type,
        stdout_path=str(out_path),
        stderr_path=str(err_path),
        stdout_bytes=len(stdout),
        stderr_bytes=len(stderr),
        stdout_sha256=sha256(stdout).hexdigest(),
        stderr_sha256=sha256(stderr).hexdigest(),
    )


def decode_utf8_strict(payload: bytes) -> str:
    """Decode only after raw capture; invalid UTF-8 remains a hard error."""

    if not isinstance(payload, bytes):
        raise RawCaptureTypeError(f"decode input must be bytes, got {type(payload).__name__}")
    return payload.decode("utf-8", errors="strict")
