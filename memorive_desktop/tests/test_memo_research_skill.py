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
    assert '"[E1 "no colon"]" is not a citation mark' in run.stderr
    assert not (folder / "memo-answer.json").exists()


def test_a_failing_local_command_is_reported_even_in_another_encoding(handoff):
    # On Windows a command that fails (blocked by an agent's sandbox, for instance) may report in the
    # console's code page; the bridge must pass the message on, not fail decoding it.
    _, _, folder = handoff
    failing = "import sys; sys.stderr.buffer.write('无法打开文件：权限不足'.encode('gbk')); sys.exit(1)"
    (folder / "transport.json").write_text(json.dumps({"command": [sys.executable, "-c", failing]}), encoding="utf-8")
    run = bridge(folder, "read", check=False)
    assert run.returncode == 2 and "Traceback" not in run.stderr and run.stderr.strip()
    # It also says how to go on: answer from the texts in the folder and tell the user.
    assert "could not be reached" in run.stderr and "materials/" in run.stderr


def test_find_prints_and_keeps_only_the_passages_with_a_term(handoff):
    _, _, folder = handoff
    printed = bridge(folder, "find", "Calibration Error", "isotonic").stdout
    assert "E1 · calibration.md" in printed
    assert "  > Temperature scaling reduced the expected calibration error from 0.17 to 0.05 on 1,200 questions." in printed
    assert "drift.md" not in printed
    store = json.loads((folder / "work" / "evidence.json").read_text(encoding="utf-8"))
    assert [row["title"] for row in store["evidence"].values()] == ["calibration.md"]
    # A later read keeps E1 and numbers the rest after it.
    bridge(folder, "read")
    store = json.loads((folder / "work" / "evidence.json").read_text(encoding="utf-8"))
    assert [(alias, row["title"]) for alias, row in store["evidence"].items()] == [("E1", "calibration.md"), ("E2", "drift.md")]
    assert "No passage contains these terms" in bridge(folder, "find", "quantum").stdout


def test_an_answer_from_found_passages_returns_with_partial_coverage_and_typed_quotes(handoff):
    ws, thread_id, folder = handoff
    bridge(folder, "find", "temperature scaling")
    e = alias(folder, "0.17 to 0.05")
    # Chinese quotation marks and colon are read like "..." and ":".
    # Two marks on one passage show one number.
    path = draft(folder, f'温度缩放把期望校准误差从 0.17 降到 0.05 [{e}：“reduced the expected calibration error from 0.17 to 0.05”] '
                         f'[{e}: "1,200 questions"]。\n')
    report = json.loads(bridge(folder, "answer", "--draft", path, "--return").stdout)
    assert (report["status"], report["returned"]["status"]) == ("RETURNED", "RETURNED")
    assert "nothing else is needed" in report["next"]
    saved = json.loads((folder / "memo-answer.json").read_text(encoding="utf-8"))
    assert saved["answer"] == "温度缩放把期望校准误差从 0.17 降到 0.05 [1]。"
    assert sorted(c["status"] for c in saved["coverage"]) == ["read", "unread"]
    assert ws.thread_get(thread_id)["messages"][-1]["text"] == saved["answer"]


def test_a_draft_problem_says_how_to_fix_it(handoff):
    _, _, folder = handoff
    bridge(folder, "read")
    calibration, drift = alias(folder, "0.17 to 0.05"), alias(folder, "81% to 64%")
    path = draft(folder, f'Accuracy fell from 81% to 64% [{calibration}: "Temperature scaling reduced"]. '
                         f'It was not tested on live logs [{calibration}: "not tested on live logs"].\n'
                         'Other studies report 0.91 accuracy.\n')
    run = bridge(folder, "answer", "--draft", path, check=False)
    assert run.returncode == 2
    assert f"81 not in the cited passages; it is in {drift}: cite that passage" in run.stderr
    assert (f'{calibration}: the quote is not in the passage: "not tested on live logs"; the closest text in '
            f'{calibration} is: "The method was not tested on live query logs."') in run.stderr
    # Fixed, only the value nobody reports is left.
    path = draft(folder, f'Accuracy fell from 81% to 64% [{drift}: "fell from 81% to 64%"]. '
                         'Other studies report 0.91 accuracy.\n')
    run = bridge(folder, "answer", "--draft", path, check=False)
    assert run.returncode == 2 and "0.91: in the answer without a cited passage" in run.stderr
    assert not (folder / "memo-answer.json").exists()


def test_a_quote_cited_under_the_wrong_passage_is_cited_from_the_one_that_holds_it(handoff):
    ws, thread_id, folder = handoff
    bridge(folder, "read")
    calibration, drift = alias(folder, "0.17 to 0.05"), alias(folder, "81% to 64%")
    path = draft(folder, f'Accuracy fell from 81% to 64% [{calibration}: "Accuracy fell from 81% to 64%"].\n')
    report = json.loads(bridge(folder, "answer", "--draft", path).stdout)
    assert report["status"] == "SAVED"
    assert report["corrected"] == [f'{calibration} → {drift}: "Accuracy fell from 81% to 64%" is in {drift}, which is cited instead']
    saved = json.loads((folder / "memo-answer.json").read_text(encoding="utf-8"))
    store = json.loads((folder / "work" / "evidence.json").read_text(encoding="utf-8"))
    assert saved["claims"][0]["supports"][0]["evidence_id"] == store["evidence"][drift]["id"]


def test_an_answer_that_cannot_be_returned_is_saved_and_says_what_to_tell_the_user(handoff):
    ws, thread_id, folder = handoff
    bridge(folder, "find", "calibration")
    e = alias(folder, "0.17 to 0.05")
    path = draft(folder, f'The error fell from 0.17 to 0.05 [{e}: "from 0.17 to 0.05"].\n')
    blocked = "import sys; sys.stderr.write('Permission denied'); sys.exit(1)"
    (folder / "transport.json").write_text(json.dumps({"command": [sys.executable, "-c", blocked]}), encoding="utf-8")
    report = json.loads(bridge(folder, "answer", "--draft", path, "--return").stdout)
    assert report["status"] == "NOT_RETURNED" and "Permission denied" in report["returned"]["reason"]
    assert "attachment button" in report["next"]
    assert json.loads((folder / "memo-answer.json").read_text(encoding="utf-8"))["answer"] == "The error fell from 0.17 to 0.05 [1]."
    assert ws.thread_get(thread_id)["messages"] == []


@pytest.mark.parametrize("quote, passage, found", [
    ("overfitting", "is not caused by overﬁtting, and", "overﬁtting"),
    ("degrades rapidly", "and then de-\ngrades rapidly.", "de-\ngrades rapidly"),
    ("state-of-the-art results", "the state-\nof-the-art results", "state-\nof-the-art results"),
    ("“degradation” problem", 'the "degradation" problem', '"degradation" problem'),
    ("34 layers 28.54", "34 layers\n28.54\n25.03", "34 layers\n28.54"),
    ("error — low", "error - low", "error - low"),
    ("VOC07 mAP\\n57.1\\n59.2", "VOC07 mAP\n57.1\n59.2\n66.9", "VOC07 mAP\n57.1\n59.2"),
    ("not caused by noise", "is not caused by overfitting", None),
])
def test_a_quote_is_found_as_the_passage_writes_it(quote, passage, found):
    sys.path.insert(0, str(SCRIPTS))
    import drafts
    assert drafts.locate(quote, passage) == found


def test_marks_after_a_full_stop_or_after_another_mark_support_that_sentence():
    # The ways writers place marks: after the full stop, one after another, at the end of a table row;
    # page references, years and names with digits in a sentence are not values to cite.
    sys.path.insert(0, str(SCRIPTS))
    import drafts
    passages = {"E1": "Table 2. Top-1 error (%, 10-crop testing).\n34 layers\n28.54\n25.03",
                "E2": "ResNet reduces the top-1 error by 3.5% (Table 2)."}

    def resolve(alias, where, quote):
        return {"id": alias}, passages[alias]

    draft = ('The 34-layer plain net (VGG16 style, PDF page 5, printed page 774, 2016) has 28.54% top-1 error.'
             '[E1: "28.54"]\nThe residual net has 25.03% [E1: "25.03"][E2: "reduces the top-1 error"].\n'
             '| plain-34 | 28.54 | [E1: "28.54"]\n')
    text, claims, problems = drafts.apply(draft, resolve, lambda supports: "[n]" * len(supports))
    assert problems == []
    assert [(c["text"], len(c["supports"])) for c in claims] == [
        ("The 34-layer plain net (VGG16 style, PDF page 5, printed page 774, 2016) has 28.54% top-1 error.", 1),
        ("The residual net has 25.03%", 2),
        ("plain-34 | 28.54", 1)]
    assert all(c["text"] in text for c in claims)
    # A value the cited passage does not report is still refused.
    _, _, problems = drafts.apply('The error was 27.10% [E2: "reduces the top-1 error"].', resolve, lambda s: "[n]")
    assert problems == ['"The error was 27.10%": 27.1 not in the cited passages; cite the passage that reports it, '
                        'or mark a computed value as [inference ...]']


@pytest.mark.parametrize("sentence, values", [
    # places in the paper are not values (Codex wrote these and the drafts were refused, 2026-11-21)
    ("原论文第 3 页 §2.2 给出候选框和特征的耗时，GPU 13 秒/图", {13.0}),
    ("训练时先采样图像再采样 RoI（第 3 页 §2.3）", set()),
    ("Section 2.2 and Table 12 report 66.9% mAP; see Fig. 3 and Eq. (14).", {66.9}),
    ("见表 12，第 4.1 节和 2.3 节的设置下 mAP 为 58.5%", {58.5}),
    # 表 inside a word is not a place: the number after it is still a value
    ("这一行代表 28.54% 的错误率", {28.54}),
])
def test_places_in_the_paper_are_not_values_to_cite(sentence, values):
    sys.path.insert(0, str(SCRIPTS))
    import drafts
    assert drafts.stated_values(sentence) == values


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
