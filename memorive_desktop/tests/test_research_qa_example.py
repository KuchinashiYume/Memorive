"""The research Q&A example (examples/research_qa.py) runs from question to saved answer.

This is the regression entry for the research Q&A flow: it goes through the same steps as the
desktop app (queue the job, select excerpts, build the prompt, call the model, check the
citations, save the answer) with a synthetic paper and a stand-in model.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "research_qa.py"


def _example():
    spec = importlib.util.spec_from_file_location("research_qa_example", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_question_about_the_synthetic_paper_is_answered_and_saved(tmp_path):
    result = _example().run(tmp_path)
    assert (result["job_status"], result["job_error"]) == ("COMPLETE", None)
    assert result["answer"] == "Growth at 37 C was twice the growth at 25 C."
    assert result["excerpts_shown_to_model"], "the prompt carried no excerpt of the paper"
    [citation] = result["citations"]
    assert citation["excerpt"] == result["excerpts_shown_to_model"][0]
    assert citation["title"] == "Temperature and growth of K12"
    assert "Growth at 37 C was twice" in citation["text"]


def short_name(path):
    """The 8.3 short form of a Windows path (such as LONGNA~1 for a long folder name), or the path itself."""
    if sys.platform != "win32":
        return path
    import ctypes
    buffer = ctypes.create_unicode_buffer(1024)
    length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    return Path(buffer.value) if 0 < length < len(buffer) else path


def test_the_example_runs_as_developing_md_shows_it(tmp_path):
    # The system temp folder, the example's default, is often given in its short form.
    folder = short_name(tmp_path)
    run = subprocess.run([sys.executable, str(EXAMPLE), str(folder)], capture_output=True, encoding="utf-8",
                         env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=300)
    assert run.returncode == 0, run.stderr[-2000:]
    assert json.loads(run.stdout)["job_status"] == "COMPLETE"
