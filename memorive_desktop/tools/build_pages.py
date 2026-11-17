"""Generate the window's two pages from their sources under memorive_desktop/ui/.

    python memorive_desktop/tools/build_pages.py           write the pages
    python memorive_desktop/tools/build_pages.py --check   change nothing; exit 1 if a page differs from its sources

The window shows product/desktop/bundle_integrated.html (the main window) and
product/desktop/bundle_assistant.html (the floating assistant). Both are made
from the files under ui/integrated/ and ui/assistant/. Edit those, run this tool,
then run sync_manifests.py. The build stops with UI_PAGE_NOT_GENERATED when a
page does not match its sources.

Each folder's page.html is the page itself, with every style, script and route
section replaced by an include line:

    <!-- include: routes/inbox/view.js -->

A line that holds only an include is replaced by the whole file; an include
inside a line is replaced in place. An included file may hold include lines of
its own (each route's markup.html includes the window frame parts under
shared/frame/). Paths are always relative to the page's folder. Files are
copied byte for byte, so keep each file's line endings as they are.

The page's Content-Security-Policy lists the sha256 of every inline script; the
browser runs no other script. page.html holds %SCRIPT_HASHES% in its place, and
this tool fills in the hashes of the scripts the generated page contains.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
SOURCES = DESKTOP / "ui"
PAGES = {"integrated": "product/desktop/bundle_integrated.html",
         "assistant": "product/desktop/bundle_assistant.html"}
INCLUDE = re.compile(rb"^[ \t]*<!-- include: ([^ >]+) -->(?:\r\n|\n)|<!-- include: ([^ >]+) -->", re.M)
SCRIPT_HASHES = b"%SCRIPT_HASHES%"


class Scripts(HTMLParser):
    """Collect inline scripts the way the build's own check reads them."""

    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict] = []
        self.current: dict | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.current = {"attrs": dict(attrs), "text": ""}

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.scripts.append(self.current)
            self.current = None


def needed_hashes(text: str) -> list[str]:
    """CSP tokens of the page's inline scripts, in page order, each once."""
    parser = Scripts()
    # Browsers and the build's check read CR LF and a lone CR as LF before hashing.
    parser.feed(text.replace("\r\n", "\n").replace("\r", "\n"))
    found: list[str] = []
    for script in parser.scripts:
        kind = (script["attrs"].get("type") or "").lower()
        if "src" in script["attrs"] or kind not in ("", "text/javascript", "application/javascript", "module"):
            continue
        if not script["text"].strip():
            continue
        token = "sha256-" + base64.b64encode(hashlib.sha256(script["text"].encode()).digest()).decode()
        if token not in found:
            found.append(token)
    return found


def generate(name: str) -> tuple[bytes, set[Path]]:
    """The page made from ui/<name>/, and the files it took in."""
    folder = SOURCES / name
    used: set[Path] = set()

    def insert(match: re.Match) -> bytes:
        path = folder / (match.group(1) or match.group(2)).decode("utf-8")
        used.add(path)
        return INCLUDE.sub(insert, path.read_bytes())   # an included file may include others

    page = INCLUDE.sub(insert, (folder / "page.html").read_bytes())
    hashes = " ".join(f"&#x27;{token}&#x27;" for token in needed_hashes(page.decode("utf-8")))
    return page.replace(SCRIPT_HASHES, hashes.encode("ascii")), used


def stale_pages() -> list[str]:
    """Pages that differ from what their sources generate."""
    return [page for name, page in PAGES.items() if generate(name)[0] != (DESKTOP / page).read_bytes()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the UI pages from memorive_desktop/ui/.")
    parser.add_argument("--check", action="store_true", help="report pages that differ and change nothing")
    arguments = parser.parse_args()
    failed = False
    for name, page in PAGES.items():
        text, used = generate(name)
        unused = sorted(path.relative_to(SOURCES).as_posix() for path in (SOURCES / name).rglob("*")
                        if path.is_file() and path.name != "page.html" and path not in used)
        for path in unused:
            print(f"ui/{path}: no include line names this file")
        failed = failed or bool(unused)
        if text == (DESKTOP / page).read_bytes():
            print(f"{page}: up to date")
        elif arguments.check:
            print(f"{page}: differs from ui/{name}/")
            failed = True
        else:
            (DESKTOP / page).write_bytes(text)
            print(f"{page}: written")
    if arguments.check and failed:
        print("\nRun: python memorive_desktop/tools/build_pages.py")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
