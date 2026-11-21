"""The memo-research Skill without Python, checked against scripts/memo_bridge.py on one hand-off.

Two ways an agent runs the Skill's commands on a computer without Python (compatibility code; its
callers are agents outside this repository, so these tests are what keeps it in use):

- Memorive running the script with its own Python (research/workspace/skill_runner.py, given to
  agents as transport.json "bridge" and Memorive.exe --memo-bridge);
- the C version memo_bridge.exe (integrations/skills/memo-research/c/), built here with Visual
  Studio's compiler (tools/build_skill_bridge.py); without a compiler that part is skipped.

Each prints what the Python script prints, writes the same evidence files and the same answer file
(the same request_id), and refuses a draft with the same problems.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import memorive
from memorive.research.workspace.service import ResearchWorkspace

DESKTOP = Path(__file__).resolve().parents[1]
SCRIPTS = DESKTOP / "integrations" / "skills" / "memo-research" / "scripts"
SOURCE = Path(memorive.__file__).resolve().parents[1]
CALIBRATION = ("# Calibrating retrieval scores\n\n"
               "Temperature scaling reduced the expected calibration error from 0.17 to 0.05 on 1,200 questions.\n"
               "Isotonic regression fitted per topic reached 0.04.\n"
               "The method was not tested on live query logs.\n\n"
               "Table 2 Results by topic\n")
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


@pytest.fixture(scope="module")
def c_bridge(tmp_path_factory):
    sys.path.insert(0, str(DESKTOP / "tools"))
    try:
        from build_skill_bridge import build, compiler_environment
    finally:
        sys.path.pop(0)
    if compiler_environment() is None:
        pytest.skip("no Visual Studio C compiler on this computer")
    return build(tmp_path_factory.mktemp("bridge") / "memo_bridge.exe")


RUNNER = ("import sys; from memorive.research.workspace.skill_runner import run; "
          "sys.exit(run('--memo-bridge', sys.argv[1:]))")


def runner(kind, c_exe=None):
    """The command line of one way to run the bridge."""
    if kind == "python":
        return [sys.executable, str(SCRIPTS / "memo_bridge.py")]
    if kind == "memorive":
        return [sys.executable, "-c", RUNNER]
    return [str(c_exe)]


def run(command, *args):
    env = dict(os.environ, PYTHONPATH=str(SOURCE))
    done = subprocess.run([*command, *map(str, args)], capture_output=True, timeout=180, env=env)
    return done.returncode, done.stdout.decode("utf-8"), done.stderr.decode("utf-8")


def session(command, folder, name):
    """Every command of the bridge on one hand-off, in its own work folder; what each printed and wrote."""
    handoff, work = folder / "handoff.json", folder / f"work-{name}"
    out = {"read": run(command, "read", handoff, "--work", work),
           "find": run(command, "find", handoff, "calibration", "ACCURACY", "--work", f"{work}-find"),
           "find nothing": run(command, "find", handoff, "zebra", "--work", f"{work}-none")}
    kept = json.loads((work / "evidence.json").read_text(encoding="utf-8"))["evidence"]
    e = next(a for a, row in kept.items() if "0.17 to 0.05" in row["text"])
    d = next(a for a, row in kept.items() if "81%" in row["text"])
    drafts = {
        "checked": f'Temperature scaling lowered the expected calibration error from 0.17 to 0.05 '
                   f'[{e}: "reduced the expected calibration error from 0.17 to 0.05"]. '
                   f'That is a drop of 0.12 [inference {e}: "from 0.17 to 0.05"].\n',
        "problems": f'The error fell to 0.03 [{e}: "reduced the error to 0.03"]. Accuracy was 99% '
                    f'[{d}: "Accuracy fell from 81% to 64%"]. [E9: "x"] stray [E2 here\n',
        "moved quote": f'Accuracy fell from 81% to 64% [{e}: "Accuracy fell from 81% to 64%"].\n',
        "value from memory": f'Temperature scaling lowered the error to 0.05 [{e}: "0.05"]. Other work reports 0.31.\n',
        # Places in the paper are not values (§4.1, Table 2, Section 2.3, Fig. 3, 第 4 节, 2.3 节, 见表 12); 代表 28.54% is.
        "places": f'As Table 2, Section 2.3, Fig. 3, Eq. (4) and §4.1 show (第 4 节, 2.3 节, 见表 12), temperature scaling '
                  f'lowered the expected calibration error from 0.17 to 0.05 '
                  f'[{e}: "reduced the expected calibration error from 0.17 to 0.05"]. '
                  f'该方法代表 28.54% 的改进 [{e}: "0.05"].\n',
    }
    for case, text in drafts.items():
        path = folder / f"draft-{name}-{case}.md"
        path.write_text(text, encoding="utf-8")
        answer = folder / f"answer-{name}-{case}.json"
        out["answer: " + case] = run(command, "answer", handoff, "--draft", path, "--work", work, "--out", answer)
        out["answer file: " + case] = answer.read_bytes().decode("utf-8") if answer.exists() else None
    # Compared as JSON: memo_bridge.py writes "totals" in the order of a set, which differs from run to run.
    out["evidence.json"] = json.loads((work / "evidence.json").read_text(encoding="utf-8"))
    out["evidence.json"]["totals"] = dict(sorted(out["evidence.json"]["totals"].items()))
    out["evidence.md"] = (work / "evidence.md").read_bytes().decode("utf-8")
    out["context"] = run(command, "context", handoff, "--limit", "2")
    return {key: json.dumps(value, ensure_ascii=False).replace(f"work-{name}", "work-X").replace(f"-{name}-", "-X-")
            for key, value in out.items()}


def test_memorive_runs_the_script_as_python_does(handoff):
    _, _, folder = handoff
    expected, got = session(runner("python"), folder, "py"), session(runner("memorive"), folder, "mv")
    assert got == expected
    assert "SAVED" in expected["answer: checked"] and "The draft needs changes" in expected["answer: problems"]


def test_the_c_version_prints_and_writes_what_python_does(handoff, c_bridge):
    _, _, folder = handoff
    expected, got = session(runner("python"), folder, "py"), session(runner("c", c_bridge), folder, "c")
    assert got == expected
    # The cases say what they cover: the closest text, a quote cited from the passage holding it, a value
    # no passage reports, a mark that is not a citation, and the same request_id.
    assert "the closest text in" in expected["answer: problems"] and "is not a citation mark" in expected["answer: problems"]
    assert "which is cited instead" in expected["answer: moved quote"]
    assert "0.31: in the answer without a cited passage" in expected["answer: value from memory"]
    assert "28.54 not in the cited passages" in expected["answer: places"]
    assert "4.1 not" not in expected["answer: places"] and "12 not" not in expected["answer: places"]
    assert "request_id" in expected["answer file: checked"] and "skill-" in expected["answer file: checked"]


def test_the_c_version_returns_an_answer_to_the_conversation(handoff, c_bridge):
    ws, thread_id, folder = handoff
    handoff_file = folder / "handoff.json"
    assert run([str(c_bridge)], "read", handoff_file)[0] == 0
    kept = json.loads((folder / "work" / "evidence.json").read_text(encoding="utf-8"))["evidence"]
    e = next(a for a, row in kept.items() if "0.17 to 0.05" in row["text"])
    draft = folder / "draft.md"
    draft.write_text(f'Temperature scaling lowered the expected calibration error from 0.17 to 0.05 '
                     f'[{e}: "reduced the expected calibration error from 0.17 to 0.05"].\n', encoding="utf-8")
    code, printed, error = run([str(c_bridge)], "answer", handoff_file, "--draft", draft, "--return")
    assert code == 0, error
    report = json.loads(printed)
    assert report["status"] == "RETURNED" and report["returned"]["knowledge_admitted"] is False
    reply = ws.thread_get(thread_id)["messages"][-1]
    assert reply["engine"] == "memo-research-skill"
    assert reply["text"] == "Temperature scaling lowered the expected calibration error from 0.17 to 0.05 [1]."


def test_the_transport_gives_the_way_without_python(handoff):
    _, _, folder = handoff
    transport = json.loads((folder / "transport.json").read_text(encoding="utf-8"))
    code, printed, error = run(transport["bridge"], "read", folder / "handoff.json")
    assert code == 0, error
    assert "2 passages" in printed


def test_bind_tells_the_agent_to_use_python(handoff, c_bridge):
    _, _, folder = handoff
    code, _, error = run([str(c_bridge)], "bind", folder / "handoff.json")
    assert code == 2 and "bind needs Python" in error
