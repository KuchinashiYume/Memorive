"""The folders of a user profile and how they are named on disk.

policy.py maps a logical folder (INBOX, SETTINGS, MODEL_VALIDATION ...) to its path in the profile
and gives windows_io_path for long Windows paths; migration.py copies files of older folder layouts
into the current one. Almost every desktop package uses it.
"""

from .policy import (
    FolderManagementPolicy,
    ProfileFolderManager,
    document_artifact_path,
    document_folder,
    load_folder_management_policy,
)
from .migration import ensure_legacy_core_projection

__all__ = [
    "FolderManagementPolicy",
    "ProfileFolderManager",
    "document_artifact_path",
    "document_folder",
    "ensure_legacy_core_projection",
    "load_folder_management_policy",
]
