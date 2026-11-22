"""How the browser companion builds one conversation from the windows a page shows while it scrolls
(extension/recording.js createMerger, used by the recording and by the site sync in content.js), and how
content.js joins the text blocks of one message (groupMessageBlocks).

One synthetic conversation of seven messages, written down first with its roles, texts and order: both
assistant replies say "I can continue.", and two other replies share a long beginning. It is read through
windows of different sizes and in both scroll directions; every case compares the whole message list.
Node.js runs the extension's own files; without Node the tests are skipped.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "source" / "memorive" / "sessions" / "companion" / "extension"

HARNESS = r"""
const fs = require("fs"), path = require("path"), vm = require("vm");
const extension = path.resolve(process.argv[2]);
require(path.join(extension, "recording.js"));
const { createMerger, createRecorder } = globalThis.MEMORIVE_SCROLL_RECORDING;
const content = fs.readFileSync(path.join(extension, "content.js"), "utf8");
const part = (first, next) => {
  const start = content.indexOf(first), end = content.indexOf(next, start + first.length);
  if (start < 0 || end < 0) throw Error("content.js part not found: " + first);
  return content.slice(start, end);
};
const groupMessageBlocks = vm.runInNewContext(part("  const groupMessageBlocks", "  const messageRows") + "\ngroupMessageBlocks;");

const row = (role, text, extra = {}) => ({ role, text, ...extra });
const shared = "Detailed analysis of the common study, method and shared reporting structure: ";
const conversation = [
  row("user", "S01 user opening"),
  row("assistant", "I can continue."),
  row("user", "S03 user middle"),
  row("assistant", shared + "result A"),
  row("user", "S05 user new question"),
  row("assistant", "I can continue."),
  row("user", "S07 user end"),
];
const slice = (from, to, rows = conversation) => rows.slice(from, to);
const texts = rows => rows.map(r => r.role + ":" + r.text);
const merge = (windows, options) => {
  const merger = createMerger();
  const outcomes = windows.map(w => merger.add(w, options));
  return { ...merger.result(), outcomes, fragments: merger.fragmentCount() };
};
const out = {};
const expected = texts(conversation);

// Different window sizes and both scroll directions give the same seven messages.
const forward4 = merge([slice(0, 4), slice(3, 7)]);
const forward3 = merge([slice(0, 3), slice(1, 4), slice(2, 5), slice(3, 6), slice(4, 7)]);
const forward2 = merge([slice(0, 2), slice(1, 3), slice(2, 4), slice(3, 5), slice(4, 6), slice(5, 7)]);
const backward = merge([slice(4, 7), slice(2, 5), slice(0, 3)]);
out.sizes_and_directions = [forward4, forward3, forward2, backward].map(r => JSON.stringify(texts(r.messages)) === JSON.stringify(expected));

// A window may hold one role only (one long reply), or nothing while the page loads.
out.one_role = texts(merge([slice(0, 2), slice(1, 2), slice(1, 3)]).messages);
out.empty_first = merge([[], slice(0, 2)]);

// Two replies that share a long beginning stay two messages.
const prefixed = [row("user", "Q1"), row("assistant", shared + "result A"), row("user", "Q2"),
                  row("assistant", shared + "different result B"), row("user", "Q3"), row("assistant", "A3")];
out.shared_prefix = texts(merge([slice(0, 3, prefixed), slice(2, 5, prefixed), slice(4, 6, prefixed)]).messages);
out.shared_prefix_expected = texts(prefixed);

// A window that lines up nowhere is a gap: kept apart, joined once a window bridges it.
const gap = createMerger();
gap.add(slice(0, 4));
out.gap_outcome = gap.add(slice(5, 7));
out.gap_result = gap.result();
out.gap_bridge_outcome = gap.add(slice(3, 6));
out.gap_bridged = { ...gap.result(), fragments: gap.fragmentCount() };
const strict = createMerger();
strict.add(slice(0, 4));
out.strict_gap = { outcome: strict.add(slice(5, 7), { keepGaps: false }), fragments: strict.fragmentCount() };

// A window that lines up in two places with different results is skipped.
const ambiguous = createMerger();
ambiguous.add([row("user", "OK"), row("assistant", "Go on.")]);
out.ambiguous = { outcome: ambiguous.add([row("assistant", "Go on."), row("user", "OK")]), ...ambiguous.result() };

// A reply still being written grows at the end of both the record and the window.
out.growth = merge([[row("user", "Q"), row("assistant", "I can")], [row("user", "Q"), row("assistant", "I can continue now.")]]).messages;

// A pinned message at the top of every window does not prove the windows are next to each other.
const pinned = row("user", "Pinned note");
out.pinned = merge([[pinned, ...slice(0, 2)], [pinned, ...slice(2, 4)]]);

// Site message IDs: the same text twice is two messages; the same ID is one message.
const keyed = conversation.map((r, i) => ({ ...r, source_key: r.role + ":m" + (i + 1) }));
const keyedResult = merge([slice(0, 4, keyed), slice(3, 7, keyed)]);
out.keyed = { count: keyedResult.messages.length,
              repeats: keyedResult.messages.filter(r => r.text === "I can continue.").length };

// Site turn numbers order fragments even across a gap.
const numbered = conversation.map((r, i) => ({ ...r, source_order: i + 1 }));
out.numbered_gap = merge([slice(0, 3, numbered), slice(5, 7, numbered)]);

// content.js: blocks inside one message container join; two containers stay two messages.
const one = {}, two = {}, three = {};
out.blocks = groupMessageBlocks([
  { role: "assistant", text: "Part one.", container: one },
  { role: "assistant", text: "Part two.", container: one },
  { role: "assistant", text: "Part one.", container: one },
  { role: "assistant", text: "Another reply.", container: two },
  { role: "user", text: "Question", container: three },
]).map(r => r.role + ":" + r.text);

// The recording saves what lines up and starts in the middle of a long reply.
(async () => {
  const run = async (windows) => {
    let index = 0, saved = null;
    const recorder = createRecorder({
      capture: () => ({ source_url: "https://example.invalid/c/1", messages: windows[Math.min(index, windows.length - 1)] }),
      validate: () => {}, now: () => 100, schedule: () => 1, cancel: () => {},
      finish: async (conversation) => { saved = conversation; return { ok: true }; },
    });
    recorder.start({ token: "t", deadline: 1000 });
    for (index = 1; index < windows.length; index += 1) recorder.sample(true);
    const state = await recorder.stop();
    return { status: state.status, error: state.error_code || "", message_count: state.message_count,
             unplaced: state.unplaced_count, saved: saved ? texts(saved.messages) : null };
  };
  out.recorder_gap = await run([slice(0, 2), [conversation[4], row("assistant", "S06 assistant end")]]);
  out.recorder_mid_reply = await run([slice(1, 2), slice(0, 2)]);
  out.recorder_one_role = await run([slice(1, 2)]);
  process.stdout.write(JSON.stringify(out));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    harness = tmp_path_factory.mktemp("windows") / "windows.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    run = subprocess.run([node, str(harness), str(EXTENSION)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60, check=False)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_every_window_size_and_direction_gives_the_same_seven_messages(results: dict) -> None:
    assert results["sizes_and_directions"] == [True, True, True, True]


def test_windows_with_one_role_or_none_still_count(results: dict) -> None:
    assert results["one_role"] == ["user:S01 user opening", "assistant:I can continue.", "user:S03 user middle"]
    assert results["empty_first"]["outcomes"] == ["EMPTY", "JOINED"]
    assert len(results["empty_first"]["messages"]) == 2


def test_two_replies_with_a_long_common_beginning_stay_two(results: dict) -> None:
    assert results["shared_prefix"] == results["shared_prefix_expected"]


def test_a_gap_is_kept_apart_and_joined_when_bridged(results: dict) -> None:
    assert results["gap_outcome"] == "GAP"
    assert len(results["gap_result"]["messages"]) == 4
    assert results["gap_result"]["unplaced_count"] == 2
    assert results["gap_result"]["gap_count"] == 1
    assert results["gap_bridge_outcome"] == "JOINED"
    assert len(results["gap_bridged"]["messages"]) == 7
    assert results["gap_bridged"]["fragments"] == 1
    assert results["strict_gap"] == {"outcome": "GAP", "fragments": 1}


def test_a_window_that_lines_up_twice_is_skipped(results: dict) -> None:
    assert results["ambiguous"]["outcome"] == "AMBIGUOUS"
    assert [r["text"] for r in results["ambiguous"]["messages"]] == ["OK", "Go on."]
    assert results["ambiguous"]["ambiguous_count"] == 1


def test_a_reply_being_written_grows_in_place(results: dict) -> None:
    assert [r["text"] for r in results["growth"]] == ["Q", "I can continue now."]


def test_a_pinned_message_does_not_join_windows(results: dict) -> None:
    assert results["pinned"]["fragments"] == 2
    assert results["pinned"]["unplaced_count"] == 3


def test_site_message_ids_and_turn_numbers(results: dict) -> None:
    assert results["keyed"] == {"count": 7, "repeats": 2}
    numbered = results["numbered_gap"]
    assert [r["source_order"] for r in numbered["messages"]] == [1, 2, 3, 6, 7]
    assert numbered["gap_count"] == 1 and numbered["unplaced_count"] == 0


def test_blocks_join_only_inside_one_message_container(results: dict) -> None:
    assert results["blocks"] == ["assistant:Part one.\n\nPart two.", "assistant:Another reply.", "user:Question"]


def test_the_recording_saves_what_lines_up(results: dict) -> None:
    gap = results["recorder_gap"]
    assert gap["status"] == "SAVED"
    assert gap["saved"] == ["user:S01 user opening", "assistant:I can continue."]
    mid = results["recorder_mid_reply"]
    assert mid["status"] == "SAVED"
    assert mid["saved"] == ["user:S01 user opening", "assistant:I can continue."]
    one_role = results["recorder_one_role"]
    assert one_role["status"] == "ERROR" and one_role["error"] == "CURRENT_PAGE_ROLES_UNCLEAR"
