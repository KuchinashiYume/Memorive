"""Create-only encryption for a user-selected file or directory.

LOCAL-INFERENCE deliberately uses the Windows current-user DPAPI boundary.  Every file
is split into independently authenticated chunks; chunk order and object
identity are bound through DPAPI optional entropy.  Logical names and the
directory layout live only in the encrypted manifest.

The public operation is copy-first: it never overwrites, renames or deletes the
selected source.  A caller needs a separate destructive authority to perform a
later source disposition step; this module intentionally has no such API.
"""

from __future__ import annotations

from collections.abc import Iterator
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
from typing import Any, BinaryIO
import uuid


SCHEMA_VERSION = "LocalInferenceLocalEncryptionTargetReceipt-v1"
ENCRYPTION_PROFILE = "WINDOWS_DPAPI_CURRENT_USER_CHUNKED_AUTHENTICATED_CONTAINER_V1"
DEFAULT_CHUNK_SIZE = 1024 * 1024
MIN_CHUNK_SIZE = 4096
MAX_CHUNK_SIZE = 16 * 1024 * 1024
CRYPTPROTECT_UI_FORBIDDEN = 0x1
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
CHUNK_MAGIC = b"PROSP11C"
MANIFEST_MAGIC = b"PROSP11M"
FORMAT_VERSION = 1
CHUNK_HEADER = struct.Struct(">8sB16s16sII")
MANIFEST_HEADER = struct.Struct(">8sB16sI")
LENGTH = struct.Struct(">I")
RECEIPT_NAME = "ENCRYPTION_RECEIPT.json"
MANIFEST_NAME = "manifest.p11m"
SUMS_NAME = "SHA256SUMS"


class SecureStorageError(RuntimeError):
    """A fail-closed storage error carrying a stable public-safe code."""

    def __init__(self, code: str, **details: object) -> None:
        self.code = code
        self.details = details
        super().__init__(code)


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _blob(value: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(value, max(1, len(value)))
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return _DataBlob(len(value), pointer), buffer


def _dpapi_protect(plaintext: bytes, entropy: bytes) -> bytes:
    if os.name != "nt":
        raise SecureStorageError("WINDOWS_DPAPI_REQUIRED")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source, source_buffer = _blob(plaintext)
    optional_entropy, entropy_buffer = _blob(entropy)
    output = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "Memorive Capabilities Runtime secure storage",
        ctypes.byref(optional_entropy),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    ):
        raise SecureStorageError("DPAPI_PROTECT_FAILED", winerror=ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def _dpapi_unprotect(ciphertext: bytes, entropy: bytes) -> bytes:
    if os.name != "nt":
        raise SecureStorageError("WINDOWS_DPAPI_REQUIRED")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source, source_buffer = _blob(ciphertext)
    optional_entropy, entropy_buffer = _blob(entropy)
    output = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        ctypes.byref(optional_entropy),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    ):
        raise SecureStorageError("DPAPI_UNPROTECT_FAILED", winerror=ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def _chunk_entropy(dataset_id: bytes, object_id: bytes, index: int) -> bytes:
    return hashlib.sha256(
        b"Memorive-LOCAL-INFERENCE-CHUNK-V1\x00"
        + dataset_id
        + object_id
        + index.to_bytes(8, "big")
    ).digest()


def _manifest_entropy(dataset_id: bytes) -> bytes:
    return hashlib.sha256(b"Memorive-LOCAL-INFERENCE-MANIFEST-V1\x00" + dataset_id).digest()


def _is_reparse(path: Path) -> bool:
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def _require_plain_path(path: Path, *, code: str) -> None:
    try:
        if _is_reparse(path):
            raise SecureStorageError(code, locator=str(path), reason="REPARSE_POINT")
    except OSError as exc:
        raise SecureStorageError(code, locator=str(path), reason=type(exc).__name__) from exc


def _iter_directory(source: Path) -> Iterator[tuple[Path, list[str], list[str]]]:
    for current, directory_names, file_names in os.walk(source, topdown=True, followlinks=False):
        current_path = Path(current)
        directory_names.sort()
        file_names.sort()
        for name in directory_names:
            _require_plain_path(current_path / name, code="REPARSE_POINT_FORBIDDEN")
        for name in file_names:
            _require_plain_path(current_path / name, code="REPARSE_POINT_FORBIDDEN")
        yield current_path, directory_names, file_names


def _hash_regular_file(path: Path) -> tuple[int, str]:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise SecureStorageError("NON_REGULAR_FILE_FORBIDDEN", locator=str(path))
    return info.st_size, _sha256_file(path)


def _snapshot_source(source: Path) -> tuple[dict[str, Any], list[tuple[str, Path]]]:
    _require_plain_path(source, code="SOURCE_REPARSE_POINT_FORBIDDEN")
    if source.is_file():
        size, digest = _hash_regular_file(source)
        snapshot = {
            "target_kind": "FILE",
            "selected_name": source.name,
            "directories": [],
            "files": [{"relative_path": "", "bytes": size, "sha256": digest}],
        }
        return snapshot, [("", source)]
    if not source.is_dir():
        raise SecureStorageError("SOURCE_MUST_BE_FILE_OR_DIRECTORY", locator=str(source))
    directories = [""]
    files: list[dict[str, object]] = []
    file_paths: list[tuple[str, Path]] = []
    for current, directory_names, file_names in _iter_directory(source):
        current_relative = current.relative_to(source).as_posix()
        current_relative = "" if current_relative == "." else current_relative
        for name in directory_names:
            relative = (PurePosixPath(current_relative) / name).as_posix()
            directories.append(relative)
        for name in file_names:
            path = current / name
            relative = (PurePosixPath(current_relative) / name).as_posix()
            size, digest = _hash_regular_file(path)
            files.append({"relative_path": relative, "bytes": size, "sha256": digest})
            file_paths.append((relative, path))
    directories.sort()
    files.sort(key=lambda item: str(item["relative_path"]))
    file_paths.sort(key=lambda item: item[0])
    return {
        "target_kind": "DIRECTORY",
        "selected_name": source.name,
        "directories": directories,
        "files": files,
    }, file_paths


def _source_set_sha256(snapshot: dict[str, Any]) -> str:
    return _sha256_bytes(_canonical_bytes(snapshot))


def _normalise_existing_source(value: str | os.PathLike[str]) -> Path:
    source = Path(value).expanduser()
    try:
        source = source.resolve(strict=True)
    except OSError as exc:
        raise SecureStorageError("SOURCE_NOT_FOUND", locator=str(source)) from exc
    if not source.name:
        raise SecureStorageError("VOLUME_ROOT_NOT_ADMITTED", locator=str(source))
    return source


def _normalise_create_only_root(value: str | os.PathLike[str]) -> Path:
    target = Path(value).expanduser()
    if target.exists() or target.is_symlink():
        raise SecureStorageError("DESTINATION_MUST_BE_ABSENT", locator=str(target))
    try:
        parent = target.parent.resolve(strict=True)
    except OSError as exc:
        raise SecureStorageError("DESTINATION_PARENT_NOT_FOUND", locator=str(target.parent)) from exc
    _require_plain_path(parent, code="DESTINATION_PARENT_REPARSE_FORBIDDEN")
    return parent / target.name


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_relative_path(value: str, *, allow_empty: bool = False) -> PurePosixPath:
    if allow_empty and value == "":
        return PurePosixPath(".")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise SecureStorageError("ENCRYPTED_MANIFEST_PATH_INVALID")
    return path


def _validate_selected_name(value: object) -> str:
    if not isinstance(value, str):
        raise SecureStorageError("ENCRYPTED_MANIFEST_NAME_INVALID")
    path = _validate_relative_path(value)
    if len(path.parts) != 1 or ":" in value:
        raise SecureStorageError("ENCRYPTED_MANIFEST_NAME_INVALID")
    return value


def _encrypt_file(
    source: Path,
    destination: Path,
    *,
    dataset_id: bytes,
    object_id: bytes,
    chunk_size: int,
    expected_size: int,
    expected_sha256: str,
) -> dict[str, object]:
    chunk_count = max(1, (expected_size + chunk_size - 1) // chunk_size)
    digest = hashlib.sha256()
    observed_size = 0
    with source.open("rb") as source_handle, destination.open("xb") as output:
        output.write(
            CHUNK_HEADER.pack(
                CHUNK_MAGIC,
                FORMAT_VERSION,
                dataset_id,
                object_id,
                chunk_size,
                chunk_count,
            )
        )
        for index in range(chunk_count):
            plaintext = source_handle.read(chunk_size)
            if index < chunk_count - 1 and len(plaintext) != chunk_size:
                raise SecureStorageError("SOURCE_CHANGED_DURING_ENCRYPTION")
            digest.update(plaintext)
            observed_size += len(plaintext)
            ciphertext = _dpapi_protect(
                plaintext,
                _chunk_entropy(dataset_id, object_id, index),
            )
            output.write(LENGTH.pack(len(ciphertext)))
            output.write(ciphertext)
        if source_handle.read(1):
            raise SecureStorageError("SOURCE_CHANGED_DURING_ENCRYPTION")
    observed_sha256 = digest.hexdigest().upper()
    if observed_size != expected_size or observed_sha256 != expected_sha256:
        raise SecureStorageError("SOURCE_CHANGED_DURING_ENCRYPTION")
    return {
        "container_bytes": destination.stat().st_size,
        "container_sha256": _sha256_file(destination),
        "chunk_count": chunk_count,
    }


def _write_manifest(path: Path, dataset_id: bytes, manifest: dict[str, Any]) -> None:
    ciphertext = _dpapi_protect(
        _canonical_bytes(manifest),
        _manifest_entropy(dataset_id),
    )
    with path.open("xb") as handle:
        handle.write(
            MANIFEST_HEADER.pack(
                MANIFEST_MAGIC,
                FORMAT_VERSION,
                dataset_id,
                len(ciphertext),
            )
        )
        handle.write(ciphertext)


def _read_manifest(path: Path) -> tuple[bytes, dict[str, Any]]:
    with path.open("rb") as handle:
        header = handle.read(MANIFEST_HEADER.size)
        if len(header) != MANIFEST_HEADER.size:
            raise SecureStorageError("MANIFEST_CONTAINER_TRUNCATED")
        magic, version, dataset_id, ciphertext_bytes = MANIFEST_HEADER.unpack(header)
        if magic != MANIFEST_MAGIC or version != FORMAT_VERSION:
            raise SecureStorageError("MANIFEST_CONTAINER_FORMAT_INVALID")
        if ciphertext_bytes < 1 or ciphertext_bytes > 64 * 1024 * 1024:
            raise SecureStorageError("MANIFEST_CIPHERTEXT_SIZE_INVALID")
        ciphertext = handle.read(ciphertext_bytes)
        if len(ciphertext) != ciphertext_bytes or handle.read(1):
            raise SecureStorageError("MANIFEST_CONTAINER_LENGTH_INVALID")
    plaintext = _dpapi_unprotect(ciphertext, _manifest_entropy(dataset_id))
    try:
        manifest = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SecureStorageError("MANIFEST_PLAINTEXT_INVALID") from exc
    if not isinstance(manifest, dict) or manifest.get("dataset_id") != dataset_id.hex().upper():
        raise SecureStorageError("MANIFEST_DATASET_ID_MISMATCH")
    return dataset_id, manifest


def _read_exact(handle: BinaryIO, count: int, code: str) -> bytes:
    value = handle.read(count)
    if len(value) != count:
        raise SecureStorageError(code)
    return value


def _verify_or_restore_object(
    container: Path,
    record: dict[str, Any],
    dataset_id: bytes,
    output: BinaryIO | None = None,
) -> None:
    try:
        object_id = bytes.fromhex(str(record["object_id"]))
    except (KeyError, ValueError) as exc:
        raise SecureStorageError("OBJECT_ID_INVALID") from exc
    if len(object_id) != 16:
        raise SecureStorageError("OBJECT_ID_INVALID")
    if _sha256_file(container) != record.get("container_sha256"):
        raise SecureStorageError("OBJECT_CONTAINER_HASH_MISMATCH")
    if container.stat().st_size != record.get("container_bytes"):
        raise SecureStorageError("OBJECT_CONTAINER_SIZE_MISMATCH")
    digest = hashlib.sha256()
    observed_size = 0
    with container.open("rb") as handle:
        header = _read_exact(handle, CHUNK_HEADER.size, "OBJECT_CONTAINER_TRUNCATED")
        magic, version, header_dataset, header_object, chunk_size, chunk_count = CHUNK_HEADER.unpack(header)
        if magic != CHUNK_MAGIC or version != FORMAT_VERSION:
            raise SecureStorageError("OBJECT_CONTAINER_FORMAT_INVALID")
        if header_dataset != dataset_id or header_object != object_id:
            raise SecureStorageError("OBJECT_CONTAINER_ID_MISMATCH")
        if not MIN_CHUNK_SIZE <= chunk_size <= MAX_CHUNK_SIZE:
            raise SecureStorageError("OBJECT_CHUNK_SIZE_INVALID")
        if chunk_count != record.get("chunk_count") or chunk_count < 1:
            raise SecureStorageError("OBJECT_CHUNK_COUNT_MISMATCH")
        for index in range(chunk_count):
            cipher_size = LENGTH.unpack(_read_exact(handle, LENGTH.size, "OBJECT_CHUNK_LENGTH_MISSING"))[0]
            if cipher_size < 1 or cipher_size > chunk_size + 1024 * 1024:
                raise SecureStorageError("OBJECT_CIPHERTEXT_SIZE_INVALID")
            ciphertext = _read_exact(handle, cipher_size, "OBJECT_CIPHERTEXT_TRUNCATED")
            plaintext = _dpapi_unprotect(
                ciphertext,
                _chunk_entropy(dataset_id, object_id, index),
            )
            if index < chunk_count - 1 and len(plaintext) != chunk_size:
                raise SecureStorageError("OBJECT_PLAINTEXT_CHUNK_SIZE_INVALID")
            observed_size += len(plaintext)
            digest.update(plaintext)
            if output is not None:
                output.write(plaintext)
        if handle.read(1):
            raise SecureStorageError("OBJECT_CONTAINER_TRAILING_BYTES")
    if observed_size != record.get("plaintext_bytes"):
        raise SecureStorageError("OBJECT_PLAINTEXT_SIZE_MISMATCH")
    if digest.hexdigest().upper() != record.get("plaintext_sha256"):
        raise SecureStorageError("OBJECT_PLAINTEXT_HASH_MISMATCH")


def _verify_sums(root: Path) -> None:
    sums_path = root / SUMS_NAME
    if not sums_path.is_file():
        raise SecureStorageError("ENCRYPTED_ROOT_SUMS_MISSING")
    seen: set[str] = set()
    for line in sums_path.read_text(encoding="ascii").splitlines():
        try:
            expected, name = line.split("  ", 1)
        except ValueError as exc:
            raise SecureStorageError("ENCRYPTED_ROOT_SUMS_INVALID") from exc
        if name in seen or name == SUMS_NAME or not (root / name).is_file():
            raise SecureStorageError("ENCRYPTED_ROOT_MEMBER_INVALID")
        if _sha256_file(root / name) != expected:
            raise SecureStorageError("ENCRYPTED_ROOT_MEMBER_HASH_MISMATCH", member=name)
        seen.add(name)
    actual = {path.name for path in root.iterdir() if path.is_file() and path.name != SUMS_NAME}
    if seen != actual or any(path.is_dir() for path in root.iterdir()):
        raise SecureStorageError("ENCRYPTED_ROOT_EXACT_SET_MISMATCH")


def _load_and_verify_root(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = root.resolve(strict=True)
    _require_plain_path(root, code="ENCRYPTED_ROOT_REPARSE_FORBIDDEN")
    if not root.is_dir():
        raise SecureStorageError("ENCRYPTED_ROOT_NOT_DIRECTORY")
    _verify_sums(root)
    try:
        receipt = json.loads((root / RECEIPT_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SecureStorageError("ENCRYPTION_RECEIPT_INVALID") from exc
    if receipt.get("schema_version") != SCHEMA_VERSION:
        raise SecureStorageError("ENCRYPTION_RECEIPT_VERSION_INVALID")
    if receipt.get("encryption_profile") != ENCRYPTION_PROFILE:
        raise SecureStorageError("ENCRYPTION_PROFILE_INVALID")
    if receipt.get("round_trip_verdict") != "PASS" or receipt.get("source_unchanged") is not True:
        raise SecureStorageError("ENCRYPTION_RECEIPT_NOT_VERIFIED")
    manifest_path = root / str(receipt.get("encrypted_manifest", {}).get("name", ""))
    if manifest_path.name != MANIFEST_NAME or not manifest_path.is_file():
        raise SecureStorageError("ENCRYPTED_MANIFEST_MISSING")
    if _sha256_file(manifest_path) != receipt["encrypted_manifest"].get("sha256"):
        raise SecureStorageError("ENCRYPTED_MANIFEST_HASH_MISMATCH")
    dataset_id, manifest = _read_manifest(manifest_path)
    if manifest.get("schema_version") != "LocalInferenceEncryptedSelectionManifest-v1":
        raise SecureStorageError("ENCRYPTED_MANIFEST_VERSION_INVALID")
    if manifest.get("operation_id") != receipt.get("operation_id"):
        raise SecureStorageError("ENCRYPTED_MANIFEST_OPERATION_MISMATCH")
    selected_name = _validate_selected_name(manifest.get("selected_name"))
    del selected_name
    directories = manifest.get("directories")
    files = manifest.get("files")
    if not isinstance(directories, list) or not isinstance(files, list):
        raise SecureStorageError("ENCRYPTED_MANIFEST_MEMBERS_INVALID")
    if len(files) != receipt.get("object_count") or len(directories) != receipt.get("directory_count"):
        raise SecureStorageError("ENCRYPTED_MANIFEST_DENOMINATOR_MISMATCH")
    seen_paths: set[str] = set()
    seen_objects: set[str] = set()
    object_projection: list[dict[str, object]] = []
    for relative in directories:
        if not isinstance(relative, str):
            raise SecureStorageError("ENCRYPTED_MANIFEST_PATH_INVALID")
        _validate_relative_path(relative, allow_empty=True)
    for record in files:
        if not isinstance(record, dict):
            raise SecureStorageError("ENCRYPTED_MANIFEST_FILE_INVALID")
        relative = str(record.get("relative_path", ""))
        if manifest.get("target_kind") == "DIRECTORY":
            _validate_relative_path(relative)
        elif relative != "":
            raise SecureStorageError("FILE_TARGET_RELATIVE_PATH_INVALID")
        object_id = str(record.get("object_id", ""))
        container_name = str(record.get("container_name", ""))
        if relative in seen_paths or object_id in seen_objects:
            raise SecureStorageError("ENCRYPTED_MANIFEST_DUPLICATE_MEMBER")
        if container_name != f"{object_id}.memoenc" or len(object_id) != 32:
            raise SecureStorageError("OBJECT_CONTAINER_NAME_INVALID")
        seen_paths.add(relative)
        seen_objects.add(object_id)
        container = root / container_name
        if not container.is_file():
            raise SecureStorageError("OBJECT_CONTAINER_MISSING")
        _verify_or_restore_object(container, record, dataset_id)
        object_projection.append(
            {
                "object_id": object_id,
                "container_sha256": record.get("container_sha256"),
                "container_bytes": record.get("container_bytes"),
            }
        )
    object_projection.sort(key=lambda item: str(item["object_id"]))
    if _sha256_bytes(_canonical_bytes(object_projection)) != receipt.get("encrypted_object_set_sha256"):
        raise SecureStorageError("ENCRYPTED_OBJECT_SET_HASH_MISMATCH")
    snapshot = {
        "target_kind": manifest.get("target_kind"),
        "selected_name": manifest.get("selected_name"),
        "directories": directories,
        "files": [
            {
                "relative_path": record.get("relative_path"),
                "bytes": record.get("plaintext_bytes"),
                "sha256": record.get("plaintext_sha256"),
            }
            for record in files
        ],
    }
    if _source_set_sha256(snapshot) != receipt.get("source_set_sha256"):
        raise SecureStorageError("SOURCE_SET_HASH_MISMATCH")
    return receipt, manifest


def encrypt_selected_path(
    selected_path: str | os.PathLike[str],
    encrypted_root: str | os.PathLike[str],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> dict[str, Any]:
    """Create and round-trip verify an encrypted copy of a file or directory.

    ``encrypted_root`` must not exist.  The source is read twice and its exact
    inventory must remain stable throughout the operation.  No source mutation
    or deletion API exists in this module.
    """

    if not isinstance(chunk_size, int) or not MIN_CHUNK_SIZE <= chunk_size <= MAX_CHUNK_SIZE:
        raise SecureStorageError("CHUNK_SIZE_OUT_OF_RANGE")
    source = _normalise_existing_source(selected_path)
    target = _normalise_create_only_root(encrypted_root)
    if source == target or (source.is_dir() and _is_within(target, source)):
        raise SecureStorageError("DESTINATION_OVERLAPS_SOURCE")
    snapshot_before, source_files = _snapshot_source(source)
    dataset_id = os.urandom(16)
    operation_id = uuid.uuid4().hex.upper()
    temporary = target.parent / f".secure-build-{uuid.uuid4().hex}"
    if temporary.exists():
        raise SecureStorageError("TEMPORARY_ROOT_COLLISION")
    temporary.mkdir()
    published = False
    try:
        manifest_files: list[dict[str, object]] = []
        snapshot_by_relative = {
            str(item["relative_path"]): item for item in snapshot_before["files"]
        }
        for relative, path in source_files:
            expected = snapshot_by_relative[relative]
            object_id = os.urandom(16)
            object_hex = object_id.hex().upper()
            container_name = f"{object_hex}.memoenc"
            encrypted = _encrypt_file(
                path,
                temporary / container_name,
                dataset_id=dataset_id,
                object_id=object_id,
                chunk_size=chunk_size,
                expected_size=int(expected["bytes"]),
                expected_sha256=str(expected["sha256"]),
            )
            manifest_files.append(
                {
                    "relative_path": relative,
                    "object_id": object_hex,
                    "container_name": container_name,
                    "plaintext_bytes": int(expected["bytes"]),
                    "plaintext_sha256": str(expected["sha256"]),
                    **encrypted,
                }
            )
        manifest_files.sort(key=lambda item: str(item["relative_path"]))
        manifest = {
            "schema_version": "LocalInferenceEncryptedSelectionManifest-v1",
            "operation_id": operation_id,
            "dataset_id": dataset_id.hex().upper(),
            "target_kind": snapshot_before["target_kind"],
            "selected_name": snapshot_before["selected_name"],
            "directories": snapshot_before["directories"],
            "files": manifest_files,
        }
        _write_manifest(temporary / MANIFEST_NAME, dataset_id, manifest)
        snapshot_after, _ = _snapshot_source(source)
        if snapshot_after != snapshot_before:
            raise SecureStorageError("SOURCE_CHANGED_DURING_ENCRYPTION")
        object_projection = sorted(
            (
                {
                    "object_id": row["object_id"],
                    "container_sha256": row["container_sha256"],
                    "container_bytes": row["container_bytes"],
                }
                for row in manifest_files
            ),
            key=lambda item: str(item["object_id"]),
        )
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "operation_id": operation_id,
            "target_kind": snapshot_before["target_kind"],
            "encryption_profile": ENCRYPTION_PROFILE,
            "encryption_scope": "WINDOWS_CURRENT_USER",
            "encrypted_root": str(target),
            "dataset_id": dataset_id.hex().upper(),
            "object_count": len(manifest_files),
            "directory_count": len(snapshot_before["directories"]),
            "source_set_sha256": _source_set_sha256(snapshot_before),
            "encrypted_manifest": {
                "name": MANIFEST_NAME,
                "sha256": _sha256_file(temporary / MANIFEST_NAME),
                "bytes": (temporary / MANIFEST_NAME).stat().st_size,
            },
            "encrypted_object_set_sha256": _sha256_bytes(_canonical_bytes(object_projection)),
            "round_trip_verdict": "PASS",
            "source_unchanged": True,
            "source_overwrite": False,
            "source_delete": False,
            "plaintext_persisted_in_managed_root": False,
            "opaque_object_names": True,
            "key_exported": False,
            "recovery_material_read": False,
            "external_requests": 0,
            "provider_cost_cny": "0",
        }
        _write_json(temporary / RECEIPT_NAME, receipt)
        members = sorted(path.name for path in temporary.iterdir() if path.is_file())
        (temporary / SUMS_NAME).write_text(
            "".join(f"{_sha256_file(temporary / name)}  {name}\n" for name in members),
            encoding="ascii",
            newline="\n",
        )
        _load_and_verify_root(temporary)
        os.replace(temporary, target)
        published = True
        return receipt
    finally:
        if not published and temporary.exists():
            shutil.rmtree(temporary)


def verify_encrypted_copy(encrypted_root: str | os.PathLike[str]) -> dict[str, Any]:
    """Authenticate the manifest and every plaintext stream without persisting it."""

    receipt, _manifest = _load_and_verify_root(Path(encrypted_root))
    return dict(receipt)


def restore_encrypted_copy(
    encrypted_root: str | os.PathLike[str],
    destination_root: str | os.PathLike[str],
) -> dict[str, Any]:
    """Restore into a new directory; existing destinations are never overwritten."""

    root = Path(encrypted_root).resolve(strict=True)
    receipt, manifest = _load_and_verify_root(root)
    destination = _normalise_create_only_root(destination_root)
    if destination == root or _is_within(destination, root):
        raise SecureStorageError("RESTORE_DESTINATION_OVERLAPS_ENCRYPTED_ROOT")
    temporary = destination.parent / f".secure-restore-{uuid.uuid4().hex}"
    temporary.mkdir()
    published = False
    try:
        selected_name = _validate_selected_name(manifest["selected_name"])
        selected_root = temporary / selected_name
        if manifest["target_kind"] == "FILE":
            if len(manifest["files"]) != 1 or manifest["directories"]:
                raise SecureStorageError("FILE_TARGET_MANIFEST_INVALID")
            record = manifest["files"][0]
            with selected_root.open("xb") as output:
                _verify_or_restore_object(
                    root / record["container_name"],
                    record,
                    bytes.fromhex(manifest["dataset_id"]),
                    output,
                )
        elif manifest["target_kind"] == "DIRECTORY":
            selected_root.mkdir()
            for relative in manifest["directories"]:
                if relative:
                    (selected_root / Path(*_validate_relative_path(relative).parts)).mkdir()
            for record in manifest["files"]:
                relative = _validate_relative_path(record["relative_path"])
                output_path = selected_root / Path(*relative.parts)
                with output_path.open("xb") as output:
                    _verify_or_restore_object(
                        root / record["container_name"],
                        record,
                        bytes.fromhex(manifest["dataset_id"]),
                        output,
                    )
        else:
            raise SecureStorageError("TARGET_KIND_INVALID")
        restored_snapshot, _ = _snapshot_source(selected_root)
        if _source_set_sha256(restored_snapshot) != receipt["source_set_sha256"]:
            raise SecureStorageError("RESTORED_SOURCE_SET_HASH_MISMATCH")
        os.replace(temporary, destination)
        published = True
        return {
            "target_kind": receipt["target_kind"],
            "restored_root": str(destination / selected_name),
            "object_count": receipt["object_count"],
            "source_set_sha256": receipt["source_set_sha256"],
            "verification_result": "PASS",
        }
    finally:
        if not published and temporary.exists():
            shutil.rmtree(temporary)
