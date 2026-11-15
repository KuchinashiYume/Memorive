"""Run the tests with every data folder pointed at a new throw-away folder.

    python memorive_desktop/tests/run_tests.py [pytest arguments]

Without arguments it runs the whole folder. Needs pytest (and pytest-timeout for the
time limit) in the Python that runs it. The temporary folder is kept when a test fails,
so its files can be inspected; otherwise it is removed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TESTS = Path(__file__).resolve().parent
DESKTOP = TESTS.parent


def main() -> int:
    # A short ASCII path: WebView2 refuses long storage paths and the vector store
    # mishandles non-ASCII ones.
    sandbox = Path(tempfile.mkdtemp(prefix="memo_tests_"))
    folders = {name: sandbox / name for name in
               ("localappdata", "appdata", "home", "tmp", "vault", "ops", "docsandbox", "chroma", "logs")}
    for folder in folders.values():
        folder.mkdir()
    env = dict(os.environ)
    env.update(
        LOCALAPPDATA=str(folders["localappdata"]), APPDATA=str(folders["appdata"]),
        USERPROFILE=str(folders["home"]), HOME=str(folders["home"]),
        TEMP=str(folders["tmp"]), TMP=str(folders["tmp"]),
        MEMORIVE_VAULT_ROOT=str(folders["vault"]), MEMORIVE_OPS_ROOT=str(folders["ops"]),
        MEMORIVE_DOCUMENT_PROCESSING_SANDBOX_ROOT=str(folders["docsandbox"]),
        MEMORIVE_DOCUMENT_PROCESSING_CHROMA_ROOT=str(folders["chroma"]),
        MEMORIVE_RUNTIME_LOG_LOG_DIR=str(folders["logs"]),
        PYTHONDONTWRITEBYTECODE="1",
    )
    # pytest runs from memorive_desktop; test paths given from elsewhere are made absolute first.
    arguments = []
    for argument in sys.argv[1:]:
        path, _, rest = argument.partition("::")
        if not argument.startswith("-") and Path(path).exists():
            argument = str(Path(path).resolve()) + ("::" + rest if rest else "")
        arguments.append(argument)
    arguments = arguments or [str(TESTS)]
    options = ["-q", "-p", "no:cacheprovider"]
    try:
        import pytest_timeout  # noqa: F401
        options.append("--timeout=120")
    except ImportError:
        pass
    code = subprocess.call([sys.executable, "-m", "pytest", *arguments, *options], cwd=DESKTOP, env=env)
    if code == 0:
        shutil.rmtree(sandbox, ignore_errors=True)
    else:
        print(f"temporary folder kept: {sandbox}")
    return code


if __name__ == "__main__":
    sys.exit(main())
