import json
from pathlib import Path

import pytest

from manager import build_info


CAPABILITIES = {
    "schema_version": "Memorive-ConsoleBuildCapabilities-v1",
    "protocol_version": "1.0",
    "build_id": "capability-fixture",
    "launch_transport": "ENV_V1",
    "session_mode": "EPHEMERAL_ONLY",
}

IDENTITY = {
    "schema_version": "DesktopReleaseIdentityBinding-v1",
    "build_id": "local-release",
    "release_version": "1.01",
    "display_version": "v1.01",
    "channel": "SANDBOX_TEST_ONLY_UNSIGNED",
    "arch": "x64",
    "main_executable": "Memorive.exe",
    "first_party_exe_inventory": ["Memorive.exe"],
    "release_authorized": False,
    "acceptance_verdict": "NOT_ASSESSED",
}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def make_build(tmp_path, *, manifest=CAPABILITIES, identity=IDENTITY):
    root = tmp_path / "Memorive"
    root.mkdir()
    executable = root / "Memorive.exe"
    executable.write_bytes(b"MZ fixture; never execute")
    desktop = root / "_internal" / "product" / "desktop"
    if manifest is not None:
        write_json(desktop / "memorive-console-capabilities.json", manifest)
    if identity is not None:
        write_json(desktop / "release_identity_binding.json", identity)
    return executable


def test_v101_nested_manifest_and_release_identity_are_recognized(tmp_path):
    executable = make_build(tmp_path)

    info = build_info(executable)

    assert info["bridge_declared"] is True
    assert info["identity_verified"] is True
    assert info["status"] == "READY_TO_CONNECT"
    assert info["build_id"] == "local-release"
    assert info["capability_build_id"] == "capability-fixture"
    assert info["release_version"] == "1.01"
    assert info["display_version"] == "v1.01"
    assert info["label"] == "v1.01"
    assert Path(info["manifest_path"]).relative_to(executable.parent).as_posix() == (
        "_internal/product/desktop/memorive-console-capabilities.json"
    )
    assert Path(info["identity_path"]).relative_to(executable.parent).as_posix() == (
        "_internal/product/desktop/release_identity_binding.json"
    )
    assert info["compatibility_warnings"] == ["CAPABILITY_BUILD_ID_MISMATCH"]


def test_formal_release_does_not_require_a_build_number(tmp_path):
    identity = {key: value for key, value in IDENTITY.items() if key != "build_id"}
    identity.update({"release_version": "1.02", "display_version": "v1.02"})
    executable = make_build(tmp_path, identity=identity)

    info = build_info(executable)

    assert info["identity_verified"] is True
    assert info["build_id"] is None
    assert info["release_version"] == "1.02"
    assert info["label"] == "v1.02"
    assert "RELEASE_BUILD_ID_NOT_DECLARED" not in info["compatibility_warnings"]


@pytest.mark.parametrize("name", ["Other.exe", "Renamed.exe", "Memorive.txt"])
def test_only_memorive_main_executable_name_is_accepted(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"fixture")
    with pytest.raises(ValueError, match="BUILD_EXECUTABLE_REQUIRED"):
        build_info(path)


def test_renamed_or_unbound_executable_is_rejected(tmp_path):
    executable = tmp_path / "Memorive.exe"
    executable.write_bytes(b"renamed fixture")
    with pytest.raises(ValueError, match="BUILD_IDENTITY_REQUIRED"):
        build_info(executable)


def test_manifest_search_uses_fixed_locations_and_never_recurses(tmp_path):
    executable = make_build(tmp_path, manifest=None, identity=None)
    hidden = executable.parent / "_internal" / "untrusted" / "product" / "desktop"
    write_json(hidden / "memorive-console-capabilities.json", CAPABILITIES)
    write_json(hidden / "release_identity_binding.json", IDENTITY)
    with pytest.raises(ValueError, match="BUILD_IDENTITY_REQUIRED"):
        build_info(executable)


def test_release_identity_must_bind_memorive_executable(tmp_path):
    identity = {**IDENTITY, "main_executable": "Other.exe"}
    executable = make_build(tmp_path, identity=identity)
    with pytest.raises(ValueError, match="BUILD_IDENTITY_INVALID"):
        build_info(executable)


def test_invalid_capability_contract_is_not_connectable(tmp_path):
    manifest = {**CAPABILITIES, "session_mode": "PERSISTENT"}
    executable = make_build(tmp_path, manifest=manifest)
    info = build_info(executable)
    assert info["identity_verified"] is True
    assert info["bridge_declared"] is False
    assert info["status"] == "WAITING_FOR_VERSION_INTERFACE"
