"""Product-neutral directory-role contracts for DIRECTORY-ROLES.

The current four development roots are custody locations, not installer
defaults.  This module therefore has no built-in Windows product path.  A
caller must provide candidate anchors explicitly and receives an immutable,
validated role map that a later Desktop installer can consume.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping


class DirectoryRole(str, Enum):
    PROGRAM_ROOT = "PROGRAM_ROOT"
    CONFIG_ROOT = "CONFIG_ROOT"
    USER_DATA_ROOT = "USER_DATA_ROOT"
    CACHE_ROOT = "CACHE_ROOT"
    LOG_ROOT = "LOG_ROOT"
    JOB_TEMP_ROOT = "JOB_TEMP_ROOT"
    VECTOR_STORE_ROOT = "VECTOR_STORE_ROOT"
    MODEL_STORE_ROOT = "MODEL_STORE_ROOT"
    BACKUP_STAGING_ROOT = "BACKUP_STAGING_ROOT"


class DeploymentMode(str, Enum):
    PER_USER = "PER_USER"
    MACHINE_WIDE = "MACHINE_WIDE"
    PORTABLE = "PORTABLE"
    MULTI_USER_SHARED_RUNTIME = "MULTI_USER_SHARED_RUNTIME"
    DEVELOPER = "DEVELOPER"


class PathContractViolation(ValueError):
    """Fail-closed role or path contract violation."""


@dataclass(frozen=True)
class RolePolicy:
    role: DirectoryRole
    scope: str
    mutability: str
    persistence: str
    default_uninstall: str
    backup_policy: str
    ascii_required: bool = False
    single_writer_required: bool = False
    rebuildable: bool = False
    acl_expectation: str = "LEAST_PRIVILEGE_OWNER_SCOPED"
    git_policy: str = "RUNTIME_CONTENT_EXCLUDED"
    secret_policy: str = "SECRET_VALUES_FORBIDDEN_USE_REFERENCES_ONLY"
    cleanup_authority: str = "SEPARATE_EXPLICIT_AUTHORIZATION_REQUIRED"
    invalidation_policy: str = "PATH_OR_OWNER_OR_SCHEMA_DRIFT_INVALIDATES_BINDING"


ROLE_POLICIES: Mapping[DirectoryRole, RolePolicy] = {
    DirectoryRole.PROGRAM_ROOT: RolePolicy(
        DirectoryRole.PROGRAM_ROOT,
        "INSTALL_SCOPE",
        "READ_ONLY_AFTER_INSTALL",
        "VERSIONED",
        "REMOVE_PROGRAM_ONLY",
        "REINSTALL_FROM_SIGNED_DISTRIBUTION",
    ),
    DirectoryRole.CONFIG_ROOT: RolePolicy(
        DirectoryRole.CONFIG_ROOT,
        "USER_OR_MACHINE",
        "MUTABLE_SCHEMA_VERSIONED",
        "PERSISTENT",
        "RETAIN",
        "EXPORT_AND_SNAPSHOT",
        single_writer_required=True,
    ),
    DirectoryRole.USER_DATA_ROOT: RolePolicy(
        DirectoryRole.USER_DATA_ROOT,
        "PER_USER",
        "MUTABLE",
        "PERSISTENT",
        "RETAIN",
        "REQUIRED",
        single_writer_required=True,
    ),
    DirectoryRole.CACHE_ROOT: RolePolicy(
        DirectoryRole.CACHE_ROOT,
        "PER_USER",
        "MUTABLE",
        "REBUILDABLE",
        "CLEANUP_CANDIDATE_AFTER_OWNER_LOCK_TTL_CHECK",
        "NOT_REQUIRED",
        rebuildable=True,
    ),
    DirectoryRole.LOG_ROOT: RolePolicy(
        DirectoryRole.LOG_ROOT,
        "PER_USER_OR_MACHINE",
        "APPEND_ROTATE",
        "RETENTION_BOUND",
        "RETAIN",
        "OPTIONAL_REDACTED",
        single_writer_required=True,
    ),
    DirectoryRole.JOB_TEMP_ROOT: RolePolicy(
        DirectoryRole.JOB_TEMP_ROOT,
        "PER_JOB",
        "MUTABLE_OWNER_SCOPED",
        "EPHEMERAL",
        "CLEANUP_CANDIDATE_AFTER_OWNER_LOCK_TTL_CHECK",
        "NOT_REQUIRED",
        rebuildable=True,
        single_writer_required=True,
    ),
    DirectoryRole.VECTOR_STORE_ROOT: RolePolicy(
        DirectoryRole.VECTOR_STORE_ROOT,
        "USER_OR_MACHINE",
        "MUTABLE_LOCKED",
        "PERSISTENT_OR_REBUILDABLE_BY_DECLARATION",
        "RETAIN",
        "APPLICATION_CONSISTENT_SNAPSHOT",
        ascii_required=True,
        single_writer_required=True,
    ),
    DirectoryRole.MODEL_STORE_ROOT: RolePolicy(
        DirectoryRole.MODEL_STORE_ROOT,
        "MACHINE_OR_USER",
        "RUNTIME_MANAGED",
        "LARGE_REBUILDABLE_WITH_LICENSE_AND_DIGEST",
        "RETAIN",
        "MANIFEST_FIRST_OPTIONAL_BLOB",
        ascii_required=True,
        single_writer_required=True,
    ),
    DirectoryRole.BACKUP_STAGING_ROOT: RolePolicy(
        DirectoryRole.BACKUP_STAGING_ROOT,
        "MIGRATION_RUN",
        "CREATE_ONLY_RUN_SCOPED",
        "RETENTION_BOUND",
        "RETAIN_UNTIL_MIGRATION_COMMIT_OR_USER_DISPOSITION",
        "IS_THE_STAGING_AREA",
        single_writer_required=True,
    ),
}


@dataclass(frozen=True)
class ProductPathLayout:
    mode: DeploymentMode
    roots: Mapping[DirectoryRole, Path]
    layout_revision: str = "DirectoryRolesDirectoryRoleLayout-v1"

    def root(self, role: DirectoryRole | str) -> Path:
        accepted = role if isinstance(role, DirectoryRole) else DirectoryRole(role)
        return self.roots[accepted]

    def doctor(self) -> dict[str, object]:
        paths = list(self.roots.values())
        checks = {
            "complete_role_denominator": set(self.roots) == set(DirectoryRole),
            "all_absolute": all(path.is_absolute() for path in paths),
            "all_distinct": len({_canonical(path) for path in paths}) == len(paths),
            "ascii_sensitive_roles_are_ascii": all(
                not ROLE_POLICIES[role].ascii_required or _is_ascii_path(path)
                for role, path in self.roots.items()
            ),
            "no_role_is_filesystem_root": all(path.parent != path for path in paths),
        }
        return {
            "schema_version": "DirectoryRolesProductPathDoctor-v1",
            "mode": self.mode.value,
            "checks": checks,
            "status": "PASS" if all(checks.values()) else "FAIL",
        }


def _canonical(path: Path) -> str:
    value = str(path)
    return value.casefold() if path.drive else value


def _is_ascii_path(path: Path) -> bool:
    try:
        str(path).encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def _child(anchor: Path, *parts: str) -> Path:
    return anchor.joinpath(*parts)


def build_candidate_layout(
    mode: DeploymentMode | str,
    *,
    program_anchor: str | Path,
    user_anchor: str | Path,
    machine_anchor: str | Path,
    portable_anchor: str | Path | None = None,
    ascii_runtime_anchor: str | Path,
) -> ProductPathLayout:
    """Build a role candidate from caller-owned anchors.

    The names below are role suffixes, not a selected Windows install path.
    Desktop remains responsible for choosing actual anchors and ACLs.
    """

    accepted_mode = mode if isinstance(mode, DeploymentMode) else DeploymentMode(mode)
    anchors = {
        "program": Path(program_anchor),
        "user": Path(user_anchor),
        "machine": Path(machine_anchor),
        "ascii": Path(ascii_runtime_anchor),
    }
    if portable_anchor is not None:
        anchors["portable"] = Path(portable_anchor)
    if any(not path.is_absolute() for path in anchors.values()):
        raise PathContractViolation("CANDIDATE_ANCHOR_MUST_BE_ABSOLUTE")

    if accepted_mode is DeploymentMode.PORTABLE:
        base = anchors.get("portable")
        if base is None:
            raise PathContractViolation("PORTABLE_ANCHOR_REQUIRED")
        roots = {
            DirectoryRole.PROGRAM_ROOT: _child(base, "program"),
            DirectoryRole.CONFIG_ROOT: _child(base, "state", "config"),
            DirectoryRole.USER_DATA_ROOT: _child(base, "state", "data"),
            DirectoryRole.CACHE_ROOT: _child(base, "state", "cache"),
            DirectoryRole.LOG_ROOT: _child(base, "state", "logs"),
            DirectoryRole.JOB_TEMP_ROOT: _child(base, "state", "jobs"),
            DirectoryRole.VECTOR_STORE_ROOT: _child(anchors["ascii"], "vector"),
            DirectoryRole.MODEL_STORE_ROOT: _child(anchors["ascii"], "models"),
            DirectoryRole.BACKUP_STAGING_ROOT: _child(base, "state", "backup-staging"),
        }
    else:
        program = anchors["program"]
        state = anchors["machine"] if accepted_mode is DeploymentMode.MACHINE_WIDE else anchors["user"]
        if accepted_mode is DeploymentMode.MULTI_USER_SHARED_RUNTIME:
            program = anchors["machine"] / "program"
            state = anchors["user"]
        roots = {
            DirectoryRole.PROGRAM_ROOT: program,
            DirectoryRole.CONFIG_ROOT: _child(state, "config"),
            DirectoryRole.USER_DATA_ROOT: _child(state, "data"),
            DirectoryRole.CACHE_ROOT: _child(state, "cache"),
            DirectoryRole.LOG_ROOT: _child(state, "logs"),
            DirectoryRole.JOB_TEMP_ROOT: _child(state, "jobs"),
            DirectoryRole.VECTOR_STORE_ROOT: _child(anchors["ascii"], "vector"),
            DirectoryRole.MODEL_STORE_ROOT: _child(anchors["ascii"], "models"),
            DirectoryRole.BACKUP_STAGING_ROOT: _child(state, "backup-staging"),
        }
    return validate_layout(ProductPathLayout(accepted_mode, roots))


def validate_layout(layout: ProductPathLayout) -> ProductPathLayout:
    if set(layout.roots) != set(DirectoryRole):
        raise PathContractViolation("DIRECTORY_ROLE_DENOMINATOR_INCOMPLETE")
    if any(not isinstance(path, Path) for path in layout.roots.values()):
        raise PathContractViolation("DIRECTORY_ROLE_PATH_TYPE_INVALID")
    doctor = layout.doctor()
    failed = [name for name, passed in doctor["checks"].items() if not passed]
    if failed:
        raise PathContractViolation("DIRECTORY_ROLE_LAYOUT_INVALID:" + ",".join(failed))
    return layout


def uninstall_retention_policy() -> dict[str, str]:
    return {role.value: ROLE_POLICIES[role].default_uninstall for role in DirectoryRole}


def directory_role_contract() -> dict[str, object]:
    """Return the product-neutral contract consumed by later Integration/Desktop work."""

    return {
        "schema_version": "DirectoryRolesDirectoryRoleContract-v1",
        "status": "PASS",
        "roles": [
            {
                "role_id": role.value,
                "scope": policy.scope,
                "mutability": policy.mutability,
                "persistence": policy.persistence,
                "default_uninstall": policy.default_uninstall,
                "backup_policy": policy.backup_policy,
                "ascii_required": policy.ascii_required,
                "single_writer_required": policy.single_writer_required,
                "rebuildable": policy.rebuildable,
                "acl_expectation": policy.acl_expectation,
                "git_policy": policy.git_policy,
                "secret_policy": policy.secret_policy,
                "cleanup_authority": policy.cleanup_authority,
                "invalidation_policy": policy.invalidation_policy,
            }
            for role, policy in ROLE_POLICIES.items()
        ],
        "resolver_precedence": [
            "EXPLICIT_INSTALLER_OR_LAUNCHER_BINDING",
            "VALIDATED_MODE_PROFILE",
            "NO_IMPLICIT_MACHINE_PATH_FALLBACK",
        ],
        "current_four_roots_are_custody_not_product_defaults": True,
        "desktop_exact_path_selection_deferred": True,
    }


def deployment_compatibility_matrix() -> dict[str, object]:
    return {
        "schema_version": "DirectoryRolesDeploymentModeCompatibilityMatrix-v1",
        "status": "PASS",
        "modes": [
            {
                "mode": mode.value,
                "role_contract_compatible": True,
                "requires_explicit_anchors": True,
                "secret_acl_can_be_silently_weakened": False,
                "desktop_selected": False,
                "blockers": (
                    ["Desktop_INSTALLER_ACL_AND_UPDATE_DESIGN_REQUIRED"]
                    if mode is not DeploymentMode.DEVELOPER
                    else ["DEVELOPMENT_CUSTODY_IS_NOT_A_PRODUCT_DEFAULT"]
                ),
            }
            for mode in DeploymentMode
        ],
    }
