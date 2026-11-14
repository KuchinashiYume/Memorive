"""Refresh the script hashes in the Content-Security-Policy of the two UI pages.

    python memorive_desktop/tools/sync_page_hashes.py           update the pages
    python memorive_desktop/tools/sync_page_hashes.py --check   only report; exit 1 if a page is out of date

bundle_integrated.html and bundle_assistant.html carry their scripts inline, and
each page's Content-Security-Policy lists the sha256 of every inline script. The
browser refuses a script whose hash is not listed, and the build stops with
UI_CSP_SCRIPT_HASH_MISMATCH. Run this after you edit a script inside one of the
pages, then run sync_manifests.py, then build.

Only the hash list changes: hashes of scripts that no longer exist are replaced
by the hashes of the scripts that are there now.
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
PAGES = ("product/desktop/bundle_integrated.html", "product/desktop/bundle_assistant.html")
POLICY = re.compile(r'(<meta\b[^>]*\bhttp-equiv="Content-Security-Policy"[^>]*\bcontent=")([^"]*)(")', re.I)
TOKEN = re.compile(r"sha256-[A-Za-z0-9+/]+=*")


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


def sync(page: str, write: bool) -> tuple[int, int]:
    path = DESKTOP / page
    text = path.read_bytes().decode("utf-8")
    policies = POLICY.findall(text)
    if len(policies) != 1:
        raise SystemExit(f"{page}: expected one Content-Security-Policy tag, found {len(policies)}")
    content = policies[0][1]
    listed = TOKEN.findall(content)
    needed = needed_hashes(text)
    # The same hash may be listed several times; treat each stale hash once.
    stale = list(dict.fromkeys(token for token in listed if token not in needed))
    missing = [token for token in needed if token not in listed]
    new_content = content
    for old, new in zip(stale, missing):
        new_content = new_content.replace(old, new)
    for old in stale[len(missing):]:        # a script was removed: drop its hash and the quotes around it
        new_content = re.sub(r"\s*(?:&#x27;|&#39;|')" + re.escape(old) + r"(?:&#x27;|&#39;|')", "", new_content)
    if missing[len(stale):]:                # a script was added: list it after the last hash already there
        if not listed:
            raise SystemExit(f"{page}: the policy lists no script hashes to extend")
        last = list(TOKEN.finditer(new_content))[-1]
        quote = re.match(r"(&#x27;|&#39;|')", new_content[last.end():]).group(1)
        addition = "".join(f" {quote}{token}{quote}" for token in missing[len(stale):])
        new_content = new_content[:last.end() + len(quote)] + addition + new_content[last.end() + len(quote):]
    if new_content != content and write:
        path.write_bytes(text.replace(content, new_content, 1).encode("utf-8"))
    return len(stale), len(missing)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the script hashes of the UI pages.")
    parser.add_argument("--check", action="store_true", help="report what is out of date and change nothing")
    arguments = parser.parse_args()
    changed = False
    for page in PAGES:
        stale, missing = sync(page, write=not arguments.check)
        if stale or missing:
            changed = True
            print(f"{page}: {missing} script hashes to add, {stale} to drop")
        else:
            print(f"{page}: up to date")
    if arguments.check and changed:
        print("\nThe script hashes are out of date. Run: python memorive_desktop/tools/sync_page_hashes.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
