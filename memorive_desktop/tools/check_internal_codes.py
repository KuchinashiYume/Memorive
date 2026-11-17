"""Check that the published files carry no internal development codes.

    python memorive_desktop/tools/check_internal_codes.py                summary; exit 1 if a code is not registered
    python memorive_desktop/tools/check_internal_codes.py --list         also print the registered hits
    python memorive_desktop/tools/check_internal_codes.py --words FILE   also look for the words in FILE

A release must not carry names that only mean something inside the development
process: phase, task and module numbers, build, candidate and run ids, feature
codenames, the working name of the project. A reader cannot know what they stand
for, and they get in the way of anyone who continues the work.

The check reads the name and the text of every file listed in
SOURCE_REVIEW_MANIFEST.json (run sync_manifests.py first so that new files are
listed), and of the tests that Git tracks under memorive_desktop/tests/, which the
manifests leave out because they are not part of a release. A hit is accepted only
when historical_names.json, next to this file, covers it:
  "names"  exact strings that are saved in users' data or exchanged with copies
           already installed; they can only change together with a data upgrade
  "files"  files or folders whose cleanup is planned separately
Rename everything else, or register it together with the reason it has to stay.

The patterns below are general shapes. Codenames of your own that no pattern can
know go in a word list, one per line, passed with --words. Keep that list
outside the repository.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOT_MANIFEST = "SOURCE_REVIEW_MANIFEST.json"
REGISTER = Path(__file__).with_name("historical_names.json")
B, E = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"
PATTERNS = [(kind, re.compile(pattern)) for kind, pattern in (
    ("phase number", B + r"P0[1-9]" + E + r"|Phase\s*0?[1-9]" + E + r"|阶段\s*(?:[0-9二三四五六七八]|一(?!致))"),
    ("task number", B + r"T(?:0[1-9]|1[0-9])" + E),
    ("module number", B + r"M(?:[1-9]|1[0-9])" + E),
    ("build or stage id", r"EVO-E[0-9]|" + B + r"[Bb]uild[0-9]{2,3}" + E + "|" + B + r"E[0-9]{1,2}_[A-Z]|"
     + B + r"[Dd][0-9]{2}" + E + r"|Round[0-9]"),
    # e2-review-dialog, memo-e7-pane, desktop-evo-e2-local-v1; e10Alt, memoE1Result; __MEMO_E7__
    ("stage number inside a name", B + r"(?:[A-Za-z]+-)*[eE](?:[1-9]|1[0-9])-[a-z]|"
     + r"(?<![A-Za-z0-9_])(?:[a-z]+E|e)(?:[1-9]|1[0-9])[A-Z][a-z]|__[A-Z]+_E[0-9]{1,2}__"),
    ("candidate or run id", r"(?:candidate|successor)[0-9]{3}|_20[0-9]{6}_[0-9]{4,6}|"
     r"_CROSS_(?=[a-z0-9]|[A-Z][a-z]|[^\x00-\x7f])"),
    ("doubled word left by renaming", r"Desktop_DESKTOP|DesktopDesktop|desktop-desktop|Desktop_Desktop|Desktop_Core|"
     r"Core_CORE|Core_Core|Shared_SHARED"),
    ("release-process name", r"public[-_]candidate"),
)]
# In pages, scripts and drawings "M10 4" and "d12" are drawing commands, not codes.
DRAWING = {".html", ".htm", ".js", ".mjs", ".cjs", ".css", ".svg"}
SKIPPED_IN_DRAWINGS = {"module number"}
TEXT = {".py", ".js", ".mjs", ".cjs", ".html", ".htm", ".css", ".json", ".md", ".txt", ".spec", ".iss", ".ps1", ".bat",
        ".cmd", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".xml", ".sh", ".cs", ".tmpl", ".svg", ""}
MANIFEST_PATH_LINE = re.compile(r'"(path|source|destination)"\s*:')
# Script hashes in a page's Content-Security-Policy are random letters and digits ("sha256-...rE1Tz...").
CONTENT_HASH = re.compile(r"sha(?:256|384|512)-[A-Za-z0-9+/]+=*")


def word_patterns(path: Path) -> list[tuple[str, re.Pattern]]:
    patterns = []
    for word in path.read_text("utf-8-sig").splitlines():
        word = word.strip()
        if not word or word.startswith("#"):
            continue
        ascii_edge = lambda char: char.isascii() and char.isalnum()
        pattern = (B if ascii_edge(word[0]) else "") + re.escape(word) + (E if ascii_edge(word[-1]) else "")
        patterns.append(("listed word", re.compile(pattern)))
    return patterns


def covered_by_name(text: str, start: int, end: int, names: list[str]) -> str | None:
    for name in names:
        at = text.find(name)
        while at != -1:
            if at <= start and end <= at + len(name):
                return name
            at = text.find(name, at + 1)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the published files for internal development codes.")
    parser.add_argument("--list", action="store_true", help="also print the hits that the register accepts")
    parser.add_argument("--words", type=Path, help="a file with further words to look for, one per line")
    arguments = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    register = json.loads(REGISTER.read_text("utf-8"))
    names = [entry["name"] for entry in register["names"]]
    set_aside = [entry["path"].rstrip("/") for entry in register["files"]]
    patterns = PATTERNS + (word_patterns(arguments.words) if arguments.words else [])
    own = {Path(__file__).resolve(), REGISTER.resolve()}

    files = [entry["path"] for entry in json.loads((REPO / ROOT_MANIFEST).read_text("utf-8"))["files"]]
    files.append(ROOT_MANIFEST)
    try:
        tracked = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "memorive_desktop/tests"],
                                 capture_output=True, check=True).stdout.decode("utf-8").split("\0")
        files.extend(name for name in tracked if name)
    except (OSError, subprocess.CalledProcessError):
        pass  # no Git (a downloaded archive): the archive carries no tests
    open_hits: list[str] = []
    accepted: list[str] = []
    used_names: set[str] = set()
    used_files: set[str] = set()

    def record(path: str, line: int, kind: str, code: str, text: str, start: int) -> None:
        where = f"{path}:{line}" if line else path
        shown = text[max(0, start - 50):start + 70].strip()
        folder = next((item for item in set_aside if path == item or path.startswith(item + "/")), None)
        name = covered_by_name(text, start, start + len(code), names)
        if name:
            used_names.add(name)
            accepted.append(f"  {where}  [{kind}] {code}  registered name: {name}")
        elif folder:
            used_files.add(folder)
            accepted.append(f"  {where}  [{kind}] {code}  set aside: {folder}")
        else:
            open_hits.append(f"  {where}  [{kind}] {code}  | {shown}")

    for path in files:
        full = REPO / path
        if not full.is_file() or full.resolve() in own:
            continue
        for kind, pattern in patterns:
            for match in pattern.finditer(path):
                record(path, 0, kind, match.group(0), path, match.start())
        suffix = full.suffix.lower()
        if suffix not in TEXT:
            continue
        manifest = "MANIFEST" in full.name or full.name.startswith("BUILD_RESOURCES") or full.name == "SOURCE_PROVENANCE.json"
        with open(full, encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle, 1):
                if manifest and MANIFEST_PATH_LINE.search(line):
                    continue  # manifests repeat every file name; the names themselves are checked above
                line = CONTENT_HASH.sub(lambda hit: " " * len(hit.group()), line)
                for kind, pattern in patterns:
                    if suffix in DRAWING and kind in SKIPPED_IN_DRAWINGS:
                        continue
                    for match in pattern.finditer(line):
                        record(path, number, kind, match.group(0), line, match.start())

    print(f"Checked {len(files)} files.")
    print(f"Accepted by historical_names.json: {len(accepted)} hits "
          f"({len(used_names)} registered names, {len(used_files)} files or folders set aside)")
    if arguments.list:
        print("\n".join(accepted))
    unused = [name for name in names if name not in used_names] + [item for item in set_aside if item not in used_files]
    if unused:
        print("\nRegistered but no longer found; remove these from historical_names.json:")
        print("\n".join(f"  {item}" for item in unused))
    if open_hits:
        print(f"\nNot registered: {len(open_hits)}")
        print("\n".join(open_hits))
        print("\nRename these, or add them to historical_names.json with the reason they have to stay.")
        return 1
    print("No unregistered internal codes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
