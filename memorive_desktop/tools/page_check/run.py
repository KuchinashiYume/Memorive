"""Record what the main window page shows, so that a run before a change and a run after it can be compared.

    python memorive_desktop/tools/page_check/run.py <output folder> [options]

    --desktop DIR     the memorive_desktop folder to check (default: the one this tool is in). To check an older
                      commit, export it (git worktree add, or git archive) and pass its memorive_desktop folder;
                      run the same copy of this tool for both runs, so both walk the same scenes.
    --modes zh,en     only these modes of scenarios.MODES (default: all)
    --only TEXT       only the scenes whose name contains TEXT
    --pictures KIND   "routes" (default) pictures each route's plain view, "all" every scene, "none" none
    --data KIND       "sample" (default) puts made-up records in first (seed.py), "empty" leaves the profile empty

The page is opened from product/desktop/bundle_integrated.html, so run tools/build_pages.py after changing ui/.
Every user folder points at a new throw-away folder, and the network is limited to this computer, as in
tests/run_tests.py. The output folder must not exist yet. After a good run the throw-away folder is removed, except
files that other programs still hold open there (an input method or a crash reporter that hooks into every
window writes into the redirected AppData); run.py then names the folder, and it can be deleted later.

Then compare two runs:
    python memorive_desktop/tools/page_check/compare.py <run before> <run after> --report <file.md>
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("output")
    parser.add_argument("--desktop", default=str(HERE.parents[1]))
    parser.add_argument("--modes", default="")
    parser.add_argument("--only", default="")
    parser.add_argument("--pictures", default="routes", choices=("routes", "all", "none"))
    parser.add_argument("--data", default="sample", choices=("sample", "empty"))
    options = parser.parse_args()
    output, desktop = Path(options.output).resolve(), Path(options.desktop).resolve()
    if output.exists():
        parser.error(f"{output} exists; pick a new output folder")
    output.mkdir(parents=True)

    # A short ASCII path: WebView2 refuses long storage paths and the vector store mishandles non-ASCII ones.
    sandbox = Path(tempfile.mkdtemp(prefix="memo_page_"))
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
        # The page would otherwise show whatever the network answered at the time (rankings, exchange rates).
        # offline/sitecustomize.py refuses connections to other computers in this process and in the desktop
        # service it starts; the proxy settings send curl and urllib to a closed port on this computer.
        MEMORIVE_PAGE_CHECK_OFFLINE="1",
        HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9", ALL_PROXY="http://127.0.0.1:9",
        http_proxy="http://127.0.0.1:9", https_proxy="http://127.0.0.1:9", all_proxy="http://127.0.0.1:9",
        NO_PROXY="localhost,127.0.0.1,::1", no_proxy="localhost,127.0.0.1,::1",
        CHECK_MODES=options.modes, CHECK_ONLY=options.only, CHECK_PICTURES=options.pictures, CHECK_DATA=options.data,
    )
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [
        str(HERE / "offline"), str(desktop / "source"), str(desktop / "product" / "desktop"), env.get("PYTHONPATH")]))
    if not options.modes:
        env.pop("CHECK_MODES")
    code = subprocess.call([sys.executable, str(HERE / "probe.py"), str(desktop), str(output), str(sandbox)],
                           cwd=desktop, env=env)
    run = json.loads((output / "run.json").read_text(encoding="utf-8"))
    print("status", run.get("status"), "| scenes", len(run.get("scenes", {})), "| seconds", run.get("seconds"),
          "| page errors", len(run.get("errors") or []))
    for key in ("drive_error", "error"):
        if run.get(key):
            print(run[key][-1500:])
    if code == 0 and run.get("status") == "DONE":
        shutil.rmtree(sandbox, ignore_errors=True)
        if sandbox.exists():
            print(f"throw-away folder left with files other programs still hold open: {sandbox}")
        return 0
    print(f"throw-away folder kept: {sandbox}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
