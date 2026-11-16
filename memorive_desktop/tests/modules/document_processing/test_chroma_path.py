"""Regression tests for Windows-safe Chroma persist paths."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
PATH_ENV_NAMES = (
    "MEMORIVE_DOCUMENT_PROCESSING_CHROMA_ROOT",
    "MEMORIVE_DOCUMENT_PROCESSING_SANDBOX_CHROMA_DIR",
    "MEMORIVE_DOCUMENT_PROCESSING_CHROMA_DIR",
)
PROBE = (
    "import json; "
    "from memorive.documents.processing.config import CHROMA_ROOT, chroma_dir; "
    "print(json.dumps({'root': str(CHROMA_ROOT), "
    "'sandbox': str(chroma_dir('sandbox')), "
    "'vault': str(chroma_dir('vault'))}))"
)


def resolve_paths(overrides: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    for name in PATH_ENV_NAMES:
        env.pop(name, None)
    env.update(overrides or {})
    output = subprocess.check_output(
        [sys.executable, "-c", PROBE],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
    )
    return json.loads(output)


class ChromaPathTest(unittest.TestCase):
    def test_default_paths_lie_under_local_app_data(self) -> None:
        # Without the variables the store goes to the runtime root under LOCALAPPDATA
        # (memorive/platform/data_folders.py), not to a fixed drive.
        paths = resolve_paths({"LOCALAPPDATA": r"C:\local"})
        self.assertEqual(paths["root"], r"C:\local\Memorive\runtime\vector-store")
        self.assertEqual(paths["sandbox"], r"C:\local\Memorive\runtime\vector-store\chroma-sandbox-v2")
        self.assertEqual(paths["vault"], r"C:\local\Memorive\runtime\vector-store\chroma-vault")

    def test_root_override_keeps_sandbox_and_vault_isolated(self) -> None:
        paths = resolve_paths({"MEMORIVE_DOCUMENT_PROCESSING_CHROMA_ROOT": r"G:\custom-root"})
        self.assertEqual(paths["sandbox"], r"G:\custom-root\chroma-sandbox-v2")
        self.assertEqual(paths["vault"], r"G:\custom-root\chroma-vault")

    def test_direct_overrides_take_precedence_over_root(self) -> None:
        paths = resolve_paths(
            {
                "MEMORIVE_DOCUMENT_PROCESSING_CHROMA_ROOT": r"G:\custom-root",
                "MEMORIVE_DOCUMENT_PROCESSING_SANDBOX_CHROMA_DIR": r"G:\direct-sandbox",
                "MEMORIVE_DOCUMENT_PROCESSING_CHROMA_DIR": r"G:\direct-vault",
            }
        )
        self.assertEqual(paths["sandbox"], r"G:\direct-sandbox")
        self.assertEqual(paths["vault"], r"G:\direct-vault")


if __name__ == "__main__":
    unittest.main(verbosity=2)
