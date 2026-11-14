"""Owned-root-only migration and rollback for ServiceContracts candidate indexes."""

from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from .canonical import canonical_json_bytes
from .errors import ContractViolation, FieldVectorError, IdentityConflict
from .locator import (
    PathBoundaryError,
)


FAIL_POINTS = frozenset(
    {
        "after_snapshot",
        "after_staging_create",
        "after_candidate_build",
        "after_validation",
        "before_finalize",
    }
)


__all__ = [
    "FAIL_POINTS",
]
