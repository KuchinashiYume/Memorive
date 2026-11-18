"""The page's messages with a key (ui/integrated/shared/i18n.js): every key the markup or a script names is
defined, every message has its three languages, and the three take the same values.

A key that is not defined shows on the page as the key itself, and a value missing in one language shows as
{name}; these tests catch both before the page is built.
"""
from __future__ import annotations

import re

from page_sources import INTEGRATED

ROW = re.compile(r"^\s*'([^']+)':\s*\[\s*'([^']*)',\s*'([^']*)',\s*'([^']*)'\s*\],?\s*$")
MARKUP_KEY = re.compile(r'\bdata-i18n(?:-(?!values\b)[a-z-]+(?<!-values))?="([^"]+)"')
SCRIPT_KEY = re.compile(r"\bi18n\.(?:set\([^,()]+(?:\([^()]*\))?,|text\()\s*'([^']+)'")
VALUE = re.compile(r"\{(\w+)\}")


def messages() -> dict[str, tuple[str, str, str]]:
    rows = {}
    for path in INTEGRATED.rglob("messages.js"):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = ROW.match(line)
            if match:
                assert match.group(1) not in rows, f"{match.group(1)} is defined twice"
                rows[match.group(1)] = match.group(2, 3, 4)
    return rows


def test_every_message_has_three_languages_with_the_same_values() -> None:
    rows = messages()
    assert rows
    for key, texts in rows.items():
        assert all(texts), key
        assert len({frozenset(VALUE.findall(text)) for text in texts}) == 1, key


def test_every_key_the_page_names_is_defined() -> None:
    rows = messages()
    named = set()
    for path in INTEGRATED.rglob("*.html"):
        named |= set(MARKUP_KEY.findall(path.read_text(encoding="utf-8")))
    for path in INTEGRATED.rglob("*.js"):
        named |= set(SCRIPT_KEY.findall(path.read_text(encoding="utf-8")))
    assert named
    assert sorted(named - set(rows)) == []
