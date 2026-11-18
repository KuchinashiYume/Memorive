"""The main window's type rule (ui/integrated/shared/typography.css): three typefaces, three sizes, two weights.

Every style sheet, script and markup file of the window names a face, a size or a weight only through the rule's
variables, so a new page or a later version cannot bring its own fonts or sizes in.
"""
from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "ui" / "integrated"

FACES = {"var(--memo-font-ui)", "var(--memo-font-read)", "var(--memo-font-code)", "inherit"}
SIZES = {"var(--memo-size-title)", "var(--memo-size-body)", "var(--memo-size-small)", "0", "inherit"}
WEIGHTS = {"400", "700", "inherit"}
DECLARATION = re.compile(r"(?<![-\w])(font-family|font-size|font-weight|font)\s*:\s*([^;{}\"'`]+?)\s*(?:!important)?\s*(?=[;}\"'`])")
SHORTHAND = re.compile(r"(?:(400|700)\s+)?(var\(--memo-size-\w+\))(?:/[\d.]+)?\s+(var\(--memo-font-\w+\))")


def _files():
    return sorted(p for p in UI.rglob("*") if p.suffix in {".css", ".js", ".html"})


def test_faces_sizes_and_weights_come_from_the_rule():
    found = []
    for path in _files():
        text = path.read_text(encoding="utf-8")
        for match in DECLARATION.finditer(text):
            prop, value = match.group(1), match.group(2).strip()
            if prop == "font-family" and value not in FACES:
                found.append((path.name, prop, value))
            elif prop == "font-size" and value not in SIZES:
                found.append((path.name, prop, value))
            elif prop == "font-weight" and value not in WEIGHTS:
                found.append((path.name, prop, value))
            elif prop == "font" and value != "inherit":
                shorthand = SHORTHAND.fullmatch(value)
                if not shorthand or shorthand.group(2) not in SIZES or shorthand.group(3) not in FACES:
                    found.append((path.name, prop, value))
        for name in re.findall(r"\.style\.(fontSize|fontFamily|fontWeight)\s*=", text):
            found.append((path.name, name, "set from a script"))
    assert not found, "type outside the rule (shared/typography.css):\n" + "\n".join(map(str, found))


def test_the_rule_has_three_faces_and_three_sizes():
    text = (UI / "shared" / "typography.css").read_text(encoding="utf-8")
    root = text[text.index(":root {"):text.index("}", text.index(":root {"))]
    assert sorted(re.findall(r"--memo-font-(\w+):", root)) == ["code", "read", "ui"]
    assert sorted(re.findall(r"--memo-size-(\w+):", root)) == ["body", "small", "title"]
    for path in _files():
        if path.name != "typography.css":
            defined = re.findall(r"--memo-(?:font|size)-\w+\s*:", path.read_text(encoding="utf-8"))
            assert not defined, f"{path.name} defines its own type variable: {defined}"
