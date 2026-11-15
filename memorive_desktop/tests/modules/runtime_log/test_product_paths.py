from __future__ import annotations

from pathlib import Path

import pytest

from runtime_log.product_paths import DirectoryRole, DeploymentMode, PathContractViolation, ROLE_POLICIES, build_candidate_layout, deployment_compatibility_matrix, directory_role_contract, uninstall_retention_policy


def _skip_on_non_ascii_temp(tmp_path: Path) -> None:
    # The vector store and model roots must have ASCII paths, and these tests put every root
    # under the temp folder.
    if not str(tmp_path).isascii():
        pytest.skip("the temp folder path has non-ASCII characters; vector and model paths must be ASCII")


def anchors(tmp_path: Path) -> dict[str, Path]:
    return {
        "program_anchor": tmp_path / "程序 Program",
        "user_anchor": tmp_path / "用户 data",
        "machine_anchor": tmp_path / "machine-data",
        "portable_anchor": tmp_path / "便携 portable",
        "ascii_runtime_anchor": tmp_path / "ascii-runtime",
    }


@pytest.mark.parametrize("mode", list(DeploymentMode))
def test_all_deployment_candidates_cover_exact_roles(tmp_path: Path, mode: DeploymentMode) -> None:
    _skip_on_non_ascii_temp(tmp_path)
    layout = build_candidate_layout(mode, **anchors(tmp_path))
    assert set(layout.roots) == set(DirectoryRole)
    assert layout.doctor()["status"] == "PASS"
    assert layout.root(DirectoryRole.VECTOR_STORE_ROOT).name == "vector"
    assert layout.root(DirectoryRole.MODEL_STORE_ROOT).name == "models"


def test_unicode_is_supported_but_ascii_sensitive_roles_remain_ascii(tmp_path: Path) -> None:
    _skip_on_non_ascii_temp(tmp_path)
    layout = build_candidate_layout(DeploymentMode.PER_USER, **anchors(tmp_path))
    assert "用户" in str(layout.root(DirectoryRole.USER_DATA_ROOT))
    assert str(layout.root(DirectoryRole.VECTOR_STORE_ROOT)).isascii()
    assert str(layout.root(DirectoryRole.MODEL_STORE_ROOT)).isascii()


def test_no_builtin_product_drive_or_username_is_selected() -> None:
    source = (Path(__file__).resolve().parents[3] / "source" / "runtime_log").joinpath("product_paths.py").read_text(encoding="utf-8")
    assert "G:\\Memorive" not in source
    assert "LOCALAPPDATA" not in source
    assert "PROGRAMFILES" not in source


def test_relative_anchor_and_non_ascii_runtime_fail_closed(tmp_path: Path) -> None:
    values = anchors(tmp_path)
    values["program_anchor"] = Path("relative")
    with pytest.raises(PathContractViolation, match="ABSOLUTE"):
        build_candidate_layout(DeploymentMode.PER_USER, **values)

    values = anchors(tmp_path)
    values["ascii_runtime_anchor"] = tmp_path / "运行"
    with pytest.raises(PathContractViolation, match="ascii_sensitive"):
        build_candidate_layout(DeploymentMode.PER_USER, **values)


def test_portable_requires_explicit_anchor(tmp_path: Path) -> None:
    values = anchors(tmp_path)
    values["portable_anchor"] = None
    with pytest.raises(PathContractViolation, match="PORTABLE_ANCHOR_REQUIRED"):
        build_candidate_layout(DeploymentMode.PORTABLE, **values)


def test_role_policies_and_uninstall_defaults_preserve_user_state() -> None:
    assert set(ROLE_POLICIES) == set(DirectoryRole)
    retention = uninstall_retention_policy()
    assert retention["PROGRAM_ROOT"] == "REMOVE_PROGRAM_ONLY"
    for role in (
        "CONFIG_ROOT",
        "USER_DATA_ROOT",
        "LOG_ROOT",
        "VECTOR_STORE_ROOT",
        "MODEL_STORE_ROOT",
    ):
        assert retention[role] == "RETAIN"


def test_role_contract_records_acl_git_secret_writer_cleanup_and_invalidation() -> None:
    contract = directory_role_contract()
    assert contract["status"] == "PASS"
    assert len(contract["roles"]) == len(DirectoryRole)
    assert contract["current_four_roots_are_custody_not_product_defaults"] is True
    for row in contract["roles"]:
        assert row["acl_expectation"]
        assert row["git_policy"] == "RUNTIME_CONTENT_EXCLUDED"
        assert row["secret_policy"] == "SECRET_VALUES_FORBIDDEN_USE_REFERENCES_ONLY"
        assert row["cleanup_authority"] == "SEPARATE_EXPLICIT_AUTHORIZATION_REQUIRED"
        assert row["invalidation_policy"]


def test_deployment_matrix_defers_all_exact_paths_to_packaging() -> None:
    matrix = deployment_compatibility_matrix()
    assert {row["mode"] for row in matrix["modes"]} == {mode.value for mode in DeploymentMode}
    assert all(row["role_contract_compatible"] for row in matrix["modes"])
    assert all(row["requires_explicit_anchors"] for row in matrix["modes"])
    assert all(row["desktop_selected"] is False for row in matrix["modes"])


def test_case_collision_and_root_anchor_fail_closed(tmp_path: Path) -> None:
    values = anchors(tmp_path)
    values["program_anchor"] = Path(tmp_path.anchor)
    with pytest.raises(PathContractViolation, match="no_role_is_filesystem_root"):
        build_candidate_layout(DeploymentMode.PER_USER, **values)


def test_long_unicode_space_paths_remain_role_valid(tmp_path: Path) -> None:
    _skip_on_non_ascii_temp(tmp_path)
    long_component = "长路径 with spaces " + "x" * 120
    values = anchors(tmp_path)
    values["user_anchor"] = tmp_path / long_component
    layout = build_candidate_layout(DeploymentMode.PER_USER, **values)
    assert layout.doctor()["status"] == "PASS"
    assert long_component in str(layout.root(DirectoryRole.USER_DATA_ROOT))
