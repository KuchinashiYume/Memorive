from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from model_gateway.local_capability import secure_storage
from model_gateway.local_capability.secure_storage import SecureStorageError, encrypt_selected_path, restore_encrypted_copy, verify_encrypted_copy
SCHEMA = Path(secure_storage.__file__).parent / "schemas" / "local_encryption_target_v1.schema.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def make_directory_fixture(root: Path) -> Path:
    source = root / "用户选择的资料夹"
    (source / "nested" / "empty").mkdir(parents=True)
    (source / "alpha.txt").write_text("alpha\n", encoding="utf-8")
    (source / "nested" / "零字节.bin").write_bytes(b"")
    (source / "nested" / "multi.bin").write_bytes(bytes(range(251)) * 41)
    return source


def test_schema_is_strict_draft_2020_12() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False


def test_file_target_round_trip_and_schema(tmp_path: Path) -> None:
    source = tmp_path / "selected report.txt"
    source.write_bytes("用户输入\n".encode("utf-8") + bytes(range(256)))
    before = source.read_bytes()
    encrypted = tmp_path / "encrypted-file"
    receipt = encrypt_selected_path(source, encrypted, chunk_size=4096)
    Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(receipt)
    assert receipt["target_kind"] == "FILE"
    assert receipt["object_count"] == 1
    assert source.read_bytes() == before
    assert verify_encrypted_copy(encrypted) == receipt
    restored = restore_encrypted_copy(encrypted, tmp_path / "restore-file")
    assert Path(restored["restored_root"]).read_bytes() == before


def test_directory_target_preserves_nested_unicode_empty_and_multichunk_files(tmp_path: Path) -> None:
    source = make_directory_fixture(tmp_path)
    before = {path.relative_to(source).as_posix(): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    receipt = encrypt_selected_path(source, tmp_path / "encrypted-dir", chunk_size=4096)
    assert receipt["target_kind"] == "DIRECTORY"
    assert receipt["object_count"] == 3
    assert receipt["directory_count"] == 3
    restored = restore_encrypted_copy(tmp_path / "encrypted-dir", tmp_path / "restore-dir")
    restored_root = Path(restored["restored_root"])
    after = {path.relative_to(restored_root).as_posix(): path.read_bytes() for path in restored_root.rglob("*") if path.is_file()}
    assert after == before
    assert (restored_root / "nested" / "empty").is_dir()


def test_public_root_has_only_opaque_objects_and_public_safe_metadata(tmp_path: Path) -> None:
    source = make_directory_fixture(tmp_path)
    encrypted = tmp_path / "encrypted-dir"
    receipt = encrypt_selected_path(source, encrypted, chunk_size=4096)
    names = {path.name for path in encrypted.iterdir()}
    assert source.name not in names
    assert "alpha.txt" not in names
    assert all(name in {"manifest.p11m", "ENCRYPTION_RECEIPT.json", "SHA256SUMS"} or (len(name) == 40 and name.endswith(".memoenc")) for name in names)
    public_text = (encrypted / "ENCRYPTION_RECEIPT.json").read_text(encoding="utf-8") + (encrypted / "SHA256SUMS").read_text(encoding="ascii")
    assert source.name not in public_text
    assert "alpha.txt" not in public_text
    assert str(source) not in public_text
    assert receipt["plaintext_persisted_in_managed_root"] is False


def test_source_is_unchanged_and_no_disposition_api_exists(tmp_path: Path) -> None:
    source = make_directory_fixture(tmp_path)
    before = {path.relative_to(source).as_posix(): digest(path) for path in source.rglob("*") if path.is_file()}
    receipt = encrypt_selected_path(source, tmp_path / "encrypted-dir", chunk_size=4096)
    after = {path.relative_to(source).as_posix(): digest(path) for path in source.rglob("*") if path.is_file()}
    assert before == after
    assert receipt["source_overwrite"] is False
    assert receipt["source_delete"] is False
    assert not hasattr(secure_storage, "delete_source")
    assert not hasattr(secure_storage, "overwrite_source")


@pytest.mark.parametrize("chunk_size", [0, 4095, 16 * 1024 * 1024 + 1, "4096"])
def test_invalid_chunk_size_fails_closed(tmp_path: Path, chunk_size: object) -> None:
    source = tmp_path / "input.bin"
    source.write_bytes(b"input")
    with pytest.raises(SecureStorageError, match="CHUNK_SIZE_OUT_OF_RANGE"):
        encrypt_selected_path(source, tmp_path / "encrypted", chunk_size=chunk_size)  # type: ignore[arg-type]
    assert not (tmp_path / "encrypted").exists()


def test_missing_source_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(SecureStorageError, match="SOURCE_NOT_FOUND"):
        encrypt_selected_path(tmp_path / "missing", tmp_path / "encrypted")


def test_existing_destination_is_never_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "input.bin"
    source.write_bytes(b"input")
    destination = tmp_path / "encrypted"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(SecureStorageError, match="DESTINATION_MUST_BE_ABSENT"):
        encrypt_selected_path(source, destination)
    assert marker.read_text(encoding="utf-8") == "keep"


def test_destination_inside_selected_directory_is_forbidden(tmp_path: Path) -> None:
    source = make_directory_fixture(tmp_path)
    with pytest.raises(SecureStorageError, match="DESTINATION_OVERLAPS_SOURCE"):
        encrypt_selected_path(source, source / "encrypted")


def test_source_reparse_point_is_forbidden(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "input.bin"
    source.write_bytes(b"input")
    monkeypatch.setattr(secure_storage, "_is_reparse", lambda path: path == source.resolve())
    with pytest.raises(SecureStorageError, match="SOURCE_REPARSE_POINT_FORBIDDEN"):
        encrypt_selected_path(source, tmp_path / "encrypted")


def test_ciphertext_tamper_is_detected(tmp_path: Path) -> None:
    source = tmp_path / "input.bin"
    source.write_bytes(b"A" * 9000)
    encrypted = tmp_path / "encrypted"
    encrypt_selected_path(source, encrypted, chunk_size=4096)
    object_path = next(encrypted.glob("*.memoenc"))
    payload = bytearray(object_path.read_bytes())
    payload[-1] ^= 1
    object_path.write_bytes(payload)
    with pytest.raises(SecureStorageError, match="ENCRYPTED_ROOT_MEMBER_HASH_MISMATCH"):
        verify_encrypted_copy(encrypted)


def test_manifest_tamper_is_detected(tmp_path: Path) -> None:
    source = tmp_path / "input.bin"
    source.write_bytes(b"input")
    encrypted = tmp_path / "encrypted"
    encrypt_selected_path(source, encrypted)
    manifest = encrypted / "manifest.p11m"
    payload = bytearray(manifest.read_bytes())
    payload[-1] ^= 1
    manifest.write_bytes(payload)
    with pytest.raises(SecureStorageError, match="ENCRYPTED_ROOT_MEMBER_HASH_MISMATCH"):
        verify_encrypted_copy(encrypted)


def test_missing_object_is_detected(tmp_path: Path) -> None:
    source = tmp_path / "input.bin"
    source.write_bytes(b"input")
    encrypted = tmp_path / "encrypted"
    encrypt_selected_path(source, encrypted)
    next(encrypted.glob("*.memoenc")).unlink()
    with pytest.raises(SecureStorageError, match="ENCRYPTED_ROOT_MEMBER_INVALID"):
        verify_encrypted_copy(encrypted)


def test_extra_plaintext_member_is_detected(tmp_path: Path) -> None:
    source = tmp_path / "input.bin"
    source.write_bytes(b"input")
    encrypted = tmp_path / "encrypted"
    encrypt_selected_path(source, encrypted)
    (encrypted / "leak.txt").write_text("plaintext", encoding="utf-8")
    with pytest.raises(SecureStorageError, match="ENCRYPTED_ROOT_EXACT_SET_MISMATCH"):
        verify_encrypted_copy(encrypted)


def test_existing_restore_destination_is_never_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "input.bin"
    source.write_bytes(b"input")
    encrypted = tmp_path / "encrypted"
    encrypt_selected_path(source, encrypted)
    destination = tmp_path / "restore"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(SecureStorageError, match="DESTINATION_MUST_BE_ABSENT"):
        restore_encrypted_copy(encrypted, destination)
    assert marker.read_text(encoding="utf-8") == "keep"


def test_zero_byte_file_is_authenticated(tmp_path: Path) -> None:
    source = tmp_path / "empty.bin"
    source.write_bytes(b"")
    encrypted = tmp_path / "encrypted"
    receipt = encrypt_selected_path(source, encrypted, chunk_size=4096)
    assert receipt["object_count"] == 1
    restored = restore_encrypted_copy(encrypted, tmp_path / "restore")
    assert Path(restored["restored_root"]).read_bytes() == b""


def test_verification_is_read_only(tmp_path: Path) -> None:
    source = tmp_path / "input.bin"
    source.write_bytes(b"input")
    encrypted = tmp_path / "encrypted"
    encrypt_selected_path(source, encrypted)
    before = {path.name: (path.stat().st_mtime_ns, digest(path)) for path in encrypted.iterdir()}
    verify_encrypted_copy(encrypted)
    after = {path.name: (path.stat().st_mtime_ns, digest(path)) for path in encrypted.iterdir()}
    assert after == before


def test_empty_directory_target_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "empty-directory"
    source.mkdir()
    encrypted = tmp_path / "encrypted"
    receipt = encrypt_selected_path(source, encrypted)
    assert receipt["object_count"] == 0
    assert receipt["directory_count"] == 1
    restored = restore_encrypted_copy(encrypted, tmp_path / "restore")
    assert Path(restored["restored_root"]).is_dir()
    assert not list(Path(restored["restored_root"]).iterdir())


def test_receipt_has_zero_external_cost_and_no_key_export(tmp_path: Path) -> None:
    source = tmp_path / "input.bin"
    source.write_bytes(b"input")
    receipt = encrypt_selected_path(source, tmp_path / "encrypted")
    assert receipt["external_requests"] == 0
    assert receipt["provider_cost_cny"] == "0"
    assert receipt["key_exported"] is False
    assert receipt["recovery_material_read"] is False
    assert receipt["encryption_scope"] == "WINDOWS_CURRENT_USER"


def test_module_has_no_network_or_subprocess_imports() -> None:
    tree = ast.parse(Path(secure_storage.__file__).read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert imports.isdisjoint({"socket", "http", "urllib", "requests", "subprocess"})
