"""Bring the source manifests in line with the files in this checkout.

    python memorive_desktop/tools/sync_manifests.py           update the manifests
    python memorive_desktop/tools/sync_manifests.py --check   only report; exit 1 if they are out of date

The build copies exactly the files the manifests list and stops when a file's
sha256 differs from the recorded one. Run this after you change, add or delete
files, then build.

What it does to each manifest:
  * refreshes sha256 (and bytes) of files whose content changed
  * removes files that no longer exist
  * adds new files:
      SOURCE_REVIEW_MANIFEST.json                             every file in the repository
      memorive_desktop/SOURCE_MANIFEST.json  files under source/, product/ and integrations/
      memorive_console/SOURCE_MANIFEST.json                   files under memorive_console/
      memorive_desktop/BUILD_RESOURCES.json  nothing; it says where in the package each
                                                              resource goes, so new non-Python files under
                                                              product/desktop/ are reported for you to add
      memorive_desktop/installer/windows/SOURCE_PROVENANCE.json
                                                              nothing; the installer builder checks the
                                                              installer sources against this list

A file counts as new when SOURCE_REVIEW_MANIFEST.json does not list it yet.
memorive_desktop/tests/ is never listed: the tests are not part of a release.
Files that Git ignores are left out. Without Git (a downloaded archive, or a
copy placed inside another repository), only the folders named above are
scanned for new files. Everything else in the manifests (header fields,
entry order, formatting, line endings) is left as it is.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOT_MANIFEST = "SOURCE_REVIEW_MANIFEST.json"
DESKTOP_MANIFEST = "memorive_desktop/SOURCE_MANIFEST.json"
RESOURCE_MANIFEST = "memorive_desktop/BUILD_RESOURCES.json"
CONSOLE_MANIFEST = "memorive_console/SOURCE_MANIFEST.json"
INSTALLER_SOURCES = "memorive_desktop/installer/windows/SOURCE_PROVENANCE.json"

# manifest -> (key that names the file, prefix its paths are relative to, folders whose new files it takes in,
#              key that holds the hash)
MANIFESTS = {
    INSTALLER_SOURCES: ("path", "memorive_desktop/installer/windows/", (), "candidate_sha256"),
    DESKTOP_MANIFEST: ("path", "memorive_desktop/", (
        "memorive_desktop/source/", "memorive_desktop/product/", "memorive_desktop/integrations/"), "sha256"),
    RESOURCE_MANIFEST: ("source", "", (), "sha256"),
    CONSOLE_MANIFEST: ("path", "memorive_console/", ("memorive_console/",), "sha256"),
    ROOT_MANIFEST: ("path", "", ("",), "sha256"),  # last: it also records the manifests above
}
SCANNED_WITHOUT_GIT = ("memorive_desktop/source", "memorive_desktop/product", "memorive_desktop/integrations",
                       "memorive_console")
NOT_RELEASED = "memorive_desktop/tests/"
SKIPPED_WITHOUT_GIT = re.compile(r"(^|/)(__pycache__|private-test|sandbox_profile|\.venv)(/|$)|\.py[cod]$|\.log$")
ENTRY = re.compile(r"\{[^{}]*\}")


def file_facts(relative: str) -> tuple[str, int]:
    data = (REPO / relative).read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def repository_files(known: set[str]) -> tuple[set[str], bool]:
    """Files of this checkout, and whether Git supplied the list."""
    try:
        # Ask Git only when this folder is the work tree itself. Inside some other
        # repository Git would answer for that one and report none of these files.
        top = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--show-toplevel"],
                             capture_output=True, check=True).stdout.decode("utf-8").strip()
        if not os.path.samefile(top, REPO):
            raise OSError("not the Git work tree")
        listed = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, check=True,
        ).stdout.decode("utf-8").split("\0")
        return {name for name in listed
                if name and not name.startswith(NOT_RELEASED) and (REPO / name).is_file()}, True
    except (OSError, subprocess.CalledProcessError):
        found = {name for name in known if (REPO / name).is_file()}
        for folder in SCANNED_WITHOUT_GIT:
            for path in (REPO / folder).rglob("*"):
                relative = path.relative_to(REPO).as_posix()
                if path.is_file() and not SKIPPED_WITHOUT_GIT.search(relative):
                    found.add(relative)
        return found, False


def render(entry: dict, template: str) -> str:
    """Write an entry the way the manifest's own entries are written."""
    indent = re.match(r"\{(\s*)", template).group(1)
    closing = re.search(r"(\s*)\}$", template).group(1)
    body = ("," + indent).join(f"{json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}"
                               for key, value in entry.items())
    return "{" + indent + body + closing + "}"


def sync(manifest: str, current: set[str], new_files: set[str], write: bool) -> dict[str, list[str]]:
    key, prefix, intake, hash_key = MANIFESTS[manifest]
    path = REPO / manifest
    text = path.read_bytes().decode("utf-8")
    start = text.index("[", text.index('"files"'))
    entries, end = json.JSONDecoder().raw_decode(text, start)
    blocks = [match.group(0) for match in ENTRY.finditer(text, start, end)]
    if len(blocks) != len(entries) or not blocks:
        raise SystemExit(f"{manifest}: the file list is not in the expected one-object-per-file form")
    separator = text[text.index(blocks[0], start) + len(blocks[0]):text.index(blocks[1], start)] if len(blocks) > 1 else ","
    lead = text[start + 1:text.index(blocks[0], start)]
    tail = text[text.rindex(blocks[-1], start, end) + len(blocks[-1]):end - 1]
    has_bytes = "bytes" in entries[0]
    upper = entries[0][hash_key].isupper()
    report = {"updated": [], "removed": [], "added": []}

    kept: list[tuple[str, str]] = []  # (repository path, entry text)
    for entry, block in zip(entries, blocks):
        relative = prefix + entry[key]
        if relative not in current:
            report["removed"].append(relative)
            continue
        digest, size = file_facts(relative)
        digest = digest.upper() if upper else digest
        if entry[hash_key] != digest or (has_bytes and entry["bytes"] != size):
            block = re.sub(rf'("{hash_key}": ")[0-9A-Fa-f]{{64}}(")', rf"\g<1>{digest}\g<2>", block)
            block = re.sub(r'("bytes": )\d+', rf"\g<1>{size}", block)
            report["updated"].append(relative)
        kept.append((relative, block))

    listed = {relative for relative, _ in kept}
    was_sorted = [entry[key] for entry in entries] == sorted(entry[key] for entry in entries)
    for relative in sorted(new_files):
        if relative == manifest or relative in listed or not relative.startswith(prefix):
            continue
        if not any(relative.startswith(folder) for folder in intake):
            continue
        digest, size = file_facts(relative)
        entry = {name: None for name in entries[0]}
        entry.update({key: relative[len(prefix):], hash_key: digest.upper() if upper else digest})
        if has_bytes:
            entry["bytes"] = size
        block = render(entry, blocks[0])
        if was_sorted:
            position = next((i for i, (other, _) in enumerate(kept) if other > relative), len(kept))
        else:  # keep it next to the other files of its folder
            folder = relative.rsplit("/", 1)[0] + "/" if "/" in relative else ""
            position = max((i + 1 for i, (other, _) in enumerate(kept) if other.startswith(folder)), default=len(kept))
        kept.insert(position, (relative, block))
        report["added"].append(relative)

    new_text = text[:start + 1] + lead + separator.join(block for _, block in kept) + tail + text[end - 1:]
    if manifest == RESOURCE_MANIFEST:  # the icon is recorded outside the file list
        icon = json.loads(text).get("icon")
        if icon and (REPO / icon["path"]).is_file():
            digest = file_facts(icon["path"])[0]
            if icon["sha256"].lower() != digest:
                at = new_text.index('"icon"')
                new_text = new_text[:at] + re.sub(r'("sha256": ")[0-9A-Fa-f]{64}(")', rf"\g<1>{digest}\g<2>",
                                                  new_text[at:], count=1)
                report["updated"].append(icon["path"])
    json.loads(new_text)
    if write and new_text != text:
        path.write_bytes(new_text.encode("utf-8"))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Update the source manifests to match this checkout.")
    parser.add_argument("--check", action="store_true", help="report what is out of date and change nothing")
    arguments = parser.parse_args()

    root_entries = json.loads((REPO / ROOT_MANIFEST).read_text("utf-8"))["files"]
    known = {entry["path"] for entry in root_entries}
    current, from_git = repository_files(known)
    if len(known & current) < len(known) // 2:
        raise SystemExit(f"Fewer than half of the {len(known)} listed files exist under {REPO}. "
                         "Nothing was changed; check that this is a complete checkout.")
    new_files = current - known - {ROOT_MANIFEST}
    if not from_git:
        print("Git is not available here: new files are looked for only under "
              + ", ".join(SCANNED_WITHOUT_GIT) + ".")

    changed = False
    for manifest in MANIFESTS:
        report = sync(manifest, current, new_files, write=not arguments.check)
        counts = ", ".join(f"{len(names)} {word}" for word, names in report.items() if names)
        print(f"{manifest}: {counts or 'up to date'}")
        for word, names in report.items():
            for name in names[:20]:
                print(f"    {word}: {name}")
            if len(names) > 20:
                print(f"    ... and {len(names) - 20} more {word}")
        changed = changed or any(report.values())

    resources = {entry["source"] for entry in json.loads((REPO / RESOURCE_MANIFEST).read_text("utf-8"))["files"]}
    unpackaged = sorted(name for name in new_files if name.startswith("memorive_desktop/product/desktop/")
                        and not name.endswith(".py") and name not in resources)
    if unpackaged:
        print(f"\nNew non-Python files under product/desktop/: the app loads resources from the destination")
        print(f"that {RESOURCE_MANIFEST} gives.")
        print('Add an entry {"source", "destination", "sha256"} there for each of these that the app should load:')
        for name in unpackaged:
            print(f"    {name}")

    if arguments.check and changed:
        print("\nThe manifests are out of date. Run: python memorive_desktop/tools/sync_manifests.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
