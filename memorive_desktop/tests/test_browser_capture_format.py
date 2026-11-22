"""Links and code keep their form from the web page to the session detail: the extension writes a message as
light Markdown (content.js structuredMessageText; content_format MARKDOWN_V1 in the export), and the import keeps
the inner whitespace of such messages, so indented Python can be copied from the detail and still runs."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from memorive.sessions.controller import SessionProductError
from tests.test_universal_current_page_capture import _controller, _current_page_bundle  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "source" / "memorive" / "sessions" / "companion" / "extension" / "content.js"

ANSWER = ("Study [source](https://example.invalid/paper)\n"
          "```python\nif ready:\n    print(42)\n```\n"
          "Run `pip install x` first.")

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const content = fs.readFileSync(process.argv[2], "utf8");
const part = (first, next) => {
  const start = content.indexOf(first), end = content.indexOf(next, start + first.length);
  if (start < 0 || end < 0) throw Error("content.js part not found: " + first);
  return content.slice(start, end);
};
class Element {
  constructor(tagName, attrs, ...childNodes) {
    Object.assign(this, { tagName: tagName.toUpperCase(), attrs, childNodes, nodeType: 1,
                          className: attrs.class || "", id: attrs.id || "" });
  }
  getAttribute(name) { return this.attrs[name] ?? null; }
  matches(selectors) { return selectors.split(",").map(s => s.trim().toUpperCase()).includes(this.tagName); }
  querySelector(tag) {
    for (const child of this.childNodes) {
      if (child.nodeType !== 1) continue;
      if (child.tagName === tag.toUpperCase()) return child;
      const found = child.querySelector(tag);
      if (found) return found;
    }
    return null;
  }
  get textContent() { return this.childNodes.map(node => node.textContent).join(""); }
}
const text = value => ({ nodeType: 3, nodeValue: value, textContent: value });
const el = (tag, attrs, ...children) => new Element(tag, attrs, ...children);
const structuredMessageText = vm.runInNewContext(
  part("  const cleanText", "  const simpleHash") + part("  const MESSAGE_BLOCK_TAGS", "  const roleMarker") +
  "\nstructuredMessageText;",
  { Element, Node: { TEXT_NODE: 3 }, getComputedStyle: () => ({ display: "block", visibility: "visible" }) });
const answer = el("article", {},
  el("p", {}, text("Study "), el("a", { href: "https://example.invalid/paper" }, text("source")),
     el("a", { class: "citation", href: "https://example.invalid/citation" }, text("[1]"))),
  el("pre", {}, el("code", { class: "language-python" }, text("if ready:\n    print(42)\n"))),
  el("p", {}, text("Run "), el("code", {}, text("pip install x")), text(" first.")));
process.stdout.write(JSON.stringify({ answer: structuredMessageText(answer) }));
"""


def test_the_extension_writes_links_and_code_as_markdown(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    harness = tmp_path / "format.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    run = subprocess.run([node, str(harness), str(CONTENT)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60, check=False)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["answer"] == ANSWER


def _bundle_with_answer(content: str, content_format: str | None) -> dict[str, object]:
    bundle = copy.deepcopy(_current_page_bundle())
    message = bundle["conversations"][0]["messages"][1]
    message["content"] = content
    if content_format is not None:
        message["content_format"] = content_format
    return bundle


def _detail_answer(controller, bundle) -> str:
    controller.import_browser_bundle(bundle)
    row = controller.call("sessions.list", {"source": "universal", "search": "", "limit_per_source": 10})["rows"][0]
    detail = controller.call("sessions.get", {"local_projection_id": row["local_projection_id"]})
    return [item["content"] for item in detail["messages"] if item["role"] == "assistant"][0]


def test_a_markdown_message_reaches_the_session_detail_unchanged(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    assert _detail_answer(controller, _bundle_with_answer(ANSWER, "MARKDOWN_V1")) == ANSWER


def test_an_older_plain_message_keeps_the_old_tidy_up(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    assert "\n    print(42)" not in _detail_answer(controller, _bundle_with_answer(ANSWER, None))


def test_an_unknown_message_format_is_refused(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    with pytest.raises(SessionProductError, match="BROWSER_MESSAGE_FORMAT_UNSUPPORTED"):
        controller.import_browser_bundle(_bundle_with_answer(ANSWER, "HTML"))
