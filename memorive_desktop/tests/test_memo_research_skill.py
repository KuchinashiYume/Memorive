"""The memo-research Skill's bridge with a real hand-off, through Memo's own local transport.

A conversation with two made-up papers is handed off (memo.handoff, memo.export_handoff); the bridge
script of integrations/skills/memo-research reads the evidence, turns a draft with [E1: "quote"]
marks into memo-answer.json and returns it. The answer lands in the same conversation with its
citations, bound to the question and the source versions, and nothing enters the knowledge base.
Without Memo, sources.py prepares a paper and turns a draft with [S1 L7: "quote"] marks into
analysis.md and citations.json; that answer can later be bound to Memo evidence and returned.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from memorive.research.workspace.service import ResearchWorkspace, desktop_handoff

SCRIPTS = Path(__file__).resolve().parents[1] / "integrations" / "skills" / "memo-research" / "scripts"
BRIDGE = SCRIPTS / "memo_bridge.py"
CALIBRATION = ("# Calibrating retrieval scores\n\n"
               "Temperature scaling reduced the expected calibration error from 0.17 to 0.05 on 1,200 questions.\n"
               "Isotonic regression fitted per topic reached 0.04.\n"
               "The method was not tested on live query logs.\n")
DRIFT = ("# Evidence drift in long reports\n\n"
         "Accuracy fell from 81% to 64% when the supporting passage moved more than 20,000 tokens away.\n")
QUESTION = "How much did temperature scaling lower the calibration error?"


@pytest.fixture
def handoff(tmp_path):
    ws = ResearchWorkspace(tmp_path / "ws")
    thread = ws.thread_create()
    files = []
    for name, text in (("calibration.md", CALIBRATION), ("drift.md", DRIFT)):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        files.append(str(path))
    job = ws.attachments.attach(thread["id"], files)
    for future in list(ws.futures.values()):
        future.result(timeout=60)
    assert ws.attachments.status(job["id"])["status"] == "COMPLETE"
    pack = ws.handoffs.create(thread["id"], QUESTION)
    folder = Path(ws.export_handoff(pack["id"])["path"]).parent
    yield ws, thread["id"], folder
    ws.close()


def bridge(folder, *args, check=True):
    run = subprocess.run([sys.executable, str(BRIDGE), *map(str, args[:1]), str(folder / "handoff.json"),
                          *map(str, args[1:])],
                         capture_output=True, text=True, encoding="utf-8", timeout=180)
    if check and run.returncode:
        raise AssertionError(run.stderr)
    return run


def alias(folder, words):
    """The alias of the passage holding these words."""
    store = json.loads((folder / "work" / "evidence.json").read_text(encoding="utf-8"))
    return next(a for a, row in store["evidence"].items() if words in row["text"])


def draft(folder, text):
    path = folder / "draft.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_read_numbers_every_passage_and_writes_one_file_to_read(handoff):
    _, _, folder = handoff
    printed = bridge(folder, "read").stdout
    text = (folder / "work" / "evidence.md").read_text(encoding="utf-8")
    assert text.startswith("# Evidence\n\nQuestion: " + QUESTION)
    assert "### E1 · lines" in text and "0.17 to 0.05" in text and "81% to 64%" in text
    assert "calibration.md: 1 of 1 chunks read" in printed and "drift.md: 1 of 1 chunks read" in printed
    # Reading again keeps the aliases.
    first = (folder / "work" / "evidence.json").read_text(encoding="utf-8")
    bridge(folder, "read")
    assert (folder / "work" / "evidence.json").read_text(encoding="utf-8") == first


def test_a_draft_becomes_a_checked_answer_that_returns_to_the_conversation(handoff):
    ws, thread_id, folder = handoff
    calibration = next(a for a in ws.store.list("artifact", "default") if a["title"] == "calibration.md")
    bridge(folder, "read", "--artifact", calibration["id"])
    e = alias(folder, "0.17 to 0.05")
    path = draft(folder, f'**Answer**: Temperature scaling lowered the expected calibration error from 0.17 to 0.05 '
                         f'[{e}: "reduced the expected calibration error from 0.17 to 0.05"]. '
                         f'That is a drop of 0.12 [inference {e}: "from 0.17 to 0.05"].\n')
    report = json.loads(bridge(folder, "answer", "--draft", path, "--gap", "Only the calibration paper bears on this.",
                               "--return").stdout)
    saved = json.loads((folder / "memo-answer.json").read_text(encoding="utf-8"))
    assert saved["answer"] == ("**Answer**: Temperature scaling lowered the expected calibration error from 0.17 to 0.05 [1]. "
                               "That is a drop of 0.12 [1].")
    assert [(c["kind"], c["text"]) for c in saved["claims"]] == [
        ("source_statement", "Temperature scaling lowered the expected calibration error from 0.17 to 0.05"),
        ("inference", "That is a drop of 0.12")]
    assert sorted(c["status"] for c in saved["coverage"]) == ["read", "unread"]
    unread = next(c for c in saved["coverage"] if c["status"] == "unread")
    assert unread["gaps"] == "Not read. Only the calibration paper bears on this."
    assert report["returned"]["status"] == "RETURNED" and report["returned"]["knowledge_admitted"] is False
    thread = ws.thread_get(thread_id)
    question, reply = thread["messages"][-2:]
    assert question["text"] == QUESTION
    assert reply["engine"] == "memo-research-skill" and reply["text"] == saved["answer"]
    assert [c["id"] for c in reply["citations"]] == [saved["claims"][0]["supports"][0]["evidence_id"]]
    assert reply["skill_result"]["handoff_id"] == saved["handoff_id"]
    assert ws.store.list("feedback", "default") == []           # nothing proposed or admitted as knowledge
    # The same answer returned again is the same return; the used hand-off cannot be read again.
    again = json.loads(bridge(folder, "return", "--result", str(folder / "memo-answer.json")).stdout)
    assert again == report["returned"]
    stale = bridge(folder, "read", check=False)
    assert stale.returncode == 2 and "HANDOFF_VERSION_CHANGED" in stale.stderr


def test_every_problem_of_a_draft_is_reported_and_nothing_is_saved(handoff):
    _, _, folder = handoff
    bridge(folder, "read")
    e = alias(folder, "0.17 to 0.05")
    path = draft(folder, f'Calibration error fell to 0.03 [{e}: "reduced the expected calibration error from 0.17 to 0.05"]. '
                         f'It was tested on live logs [{e}: "tested on live logs"]. '
                         f'Drift is large [E9: "fell"]. Something else [E1 "no colon"].')
    run = bridge(folder, "answer", "--draft", path, check=False)
    assert run.returncode == 2
    assert "0.03 not in the cited passages" in run.stderr
    assert f'{e}: the quote is not in the passage: "tested on live logs"' in run.stderr
    assert "E9 has not been read" in run.stderr
    assert "a citation mark is not in the form" in run.stderr
    assert not (folder / "memo-answer.json").exists()


# A desktop agent app: the hand-off folder carries the Skill

SKILL = SCRIPTS.parent


def shipped_files(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


@pytest.fixture
def desktop(handoff, tmp_path):
    ws, thread_id, _ = handoff
    settings = ws.settings()
    ws.settings_save({"agent": "claude-desktop", "desktop_agents": {"claude-desktop": {"model": "opus"}}},
                     settings["revision"])
    return ws, thread_id, desktop_handoff(ws, thread_id, QUESTION, tmp_path / "desktop", SKILL)


def test_a_desktop_handoff_folder_carries_the_skill_where_both_agents_find_it(desktop):
    ws, _, prepared = desktop
    folder = Path(prepared["path"])
    claude, codex = folder / ".claude" / "skills" / "memo-research", folder / ".agents" / "skills" / "memo-research"
    assert shipped_files(claude) == shipped_files(codex) == shipped_files(SKILL)
    # The prompt the user pastes names the Skill; START_HERE.md says where both copies are.
    assert str(claude / "SKILL.md") in prepared["prompt"]
    start = (folder / "START_HERE.md").read_text(encoding="utf-8")
    assert ".claude/skills/memo-research" in start and ".agents/skills/memo-research" in start
    # The copies are bound like every other file of the folder.
    row = ws.store.get("desktop_handoff", prepared["id"])
    assert {".claude/skills/memo-research/SKILL.md", ".agents/skills/memo-research/scripts/memo_bridge.py"} <= set(row["files"])
    assert ws.handoffs.validate_desktop(row)["status"] == "READY"
    (codex / "scripts" / "memo_bridge.py").write_text("print('changed')\n", encoding="utf-8")
    with pytest.raises(ValueError, match="HANDOFF_CHANGED"):
        ws.handoffs.validate_desktop(row)


def test_an_agent_returns_its_answer_with_the_skill_copy_in_the_folder(desktop):
    ws, thread_id, prepared = desktop
    folder = Path(prepared["path"])
    copy = folder / ".claude" / "skills" / "memo-research" / "scripts" / "memo_bridge.py"

    def run(*args):
        done = subprocess.run([sys.executable, str(copy), *map(str, args)], cwd=folder,
                              capture_output=True, text=True, encoding="utf-8", timeout=180)
        assert done.returncode == 0, done.stderr
        return done.stdout

    run("read", folder / "handoff.json")
    e = alias(folder, "0.17 to 0.05")
    path = draft(folder, f'Temperature scaling lowered the expected calibration error from 0.17 to 0.05 '
                         f'[{e}: "reduced the expected calibration error from 0.17 to 0.05"].\n')
    report = json.loads(run("answer", folder / "handoff.json", "--draft", path,
                            "--gap", "The drift paper does not bear on this.", "--return"))
    assert report["returned"]["status"] == "RETURNED" and report["returned"]["knowledge_admitted"] is False
    reply = ws.thread_get(thread_id)["messages"][-1]
    assert reply["engine"] == "memo-research-skill" and reply["agent_result"] is True
    assert reply["text"] == "Temperature scaling lowered the expected calibration error from 0.17 to 0.05 [1]."
    assert reply["skill_result"]["handoff_id"] == json.loads((folder / "handoff.json").read_text(encoding="utf-8"))["id"]


# Without Memo: prepare, cite, and later bind to Memo

def helper(*args, check=True):
    run = subprocess.run([sys.executable, str(SCRIPTS / "sources.py"), *map(str, args)],
                         capture_output=True, text=True, encoding="utf-8", timeout=180)
    if check and run.returncode:
        raise AssertionError(run.stdout + run.stderr)
    return json.loads(run.stdout)


@pytest.fixture
def prepared(tmp_path):
    paper = tmp_path / "calibration.md"
    paper.write_text(CALIBRATION, encoding="utf-8")
    task = tmp_path / "task"
    [result] = helper("prepare", "--out", task / "sources", paper)["results"]
    raw = (Path(result["manifest"]).parent / "raw.md").read_text(encoding="utf-8").splitlines()
    line = next(n for n, text in enumerate(raw, 1) if "0.17 to 0.05" in text)
    return task, result, line


def standalone_draft(task, line):
    (task / "draft.md").write_text(
        f'Temperature scaling lowered the expected calibration error from 0.17 to 0.05 '
        f'[S1 L{line}: "reduced the expected calibration error from 0.17 to 0.05"].\n\n'
        '<!-- coverage\nS1 read\n-->\n', encoding="utf-8")


def test_a_standalone_draft_becomes_an_analysis_with_checked_citations(prepared):
    task, result, line = prepared
    assert result["alias"] == "S1"
    standalone_draft(task, line)
    report = helper("cite", "--sources", task / "sources", "--draft", task / "draft.md", "--question", QUESTION)
    assert report["claims"] == 1 and report["coverage"] == {"S1": "read"}
    analysis = (task / "analysis.md").read_text(encoding="utf-8")
    assert analysis == f"Temperature scaling lowered the expected calibration error from 0.17 to 0.05 [L{line}].\n"
    citations = json.loads((task / "citations.json").read_text(encoding="utf-8"))
    [claim] = citations["claims"]
    assert claim["text"] in analysis
    assert claim["supports"][0]["manifest"].startswith("sources/") and claim["supports"][0]["checked"] is True


def test_every_problem_of_a_standalone_draft_is_reported(prepared):
    task, _, line = prepared
    (task / "draft.md").write_text(
        f'The error fell to 0.03 [S1 L{line}: "reduced the expected calibration error"]. '
        f'It was tested on live logs [S1 L{line}: "tested on live logs"]. '
        f'Another claim [S2 L1: "x"].\n', encoding="utf-8")
    error = helper("cite", "--sources", task / "sources", "--draft", task / "draft.md", "--question", QUESTION,
                   check=False)["error"]
    assert "0.03 not in the cited passages" in error and "Quote not found in selected span" in error
    assert "S2 is not a prepared source" in error and "coverage: say whether S1 was read" in error
    assert not (task / "analysis.md").exists()


def test_a_standalone_answer_is_bound_to_memo_evidence_and_returned(prepared, tmp_path):
    task, result, line = prepared
    standalone_draft(task, line)
    helper("cite", "--sources", task / "sources", "--draft", task / "draft.md", "--question", QUESTION)
    ws = ResearchWorkspace(tmp_path / "ws")
    try:
        thread = ws.thread_create()
        job = ws.attachments.attach(thread["id"], [result["manifest"]])      # the bundle itself, not re-extracted
        for future in list(ws.futures.values()):
            future.result(timeout=60)
        assert ws.attachments.status(job["id"])["status"] == "COMPLETE"
        pack = ws.handoffs.create(thread["id"], QUESTION)
        folder = Path(ws.export_handoff(pack["id"])["path"]).parent
        bridge(folder, "bind", "--analysis", task / "analysis.md", "--citations", task / "citations.json",
               "--request-id", "standalone-0001", "--out", folder / "memo-answer.json")
        returned = json.loads(bridge(folder, "return", "--result", folder / "memo-answer.json").stdout)
        assert returned["status"] == "RETURNED" and returned["citation_count"] == 1
        reply = ws.thread_get(thread["id"])["messages"][-1]
        assert reply["engine"] == "memo-research-skill" and "0.17 to 0.05" in reply["text"]
    finally:
        ws.close()
