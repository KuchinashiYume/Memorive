"""Research Q&A from end to end, with a synthetic paper and a stand-in model.

    python memorive_desktop/examples/research_qa.py [work folder]

This is the path a question takes in the desktop app, without the window and without a real
model:

1. A research workspace (memorive_research_workspace.ResearchWorkspace) is made in a new folder.
2. A synthetic paper is added as material. The evidence index cuts it into numbered excerpts.
   The conversation is set to use the stand-in model and that paper.
3. A question is asked about that paper. The workspace queues a job, selects excerpts, builds the
   prompt and calls the model function it was given.
4. The model here is ExampleModel: it reads the excerpt ids from the prompt and answers with the
   first one. In the app the model function is wired in memorive_research_workspace/desktop.py
   and runs the configured model through the settings service.
5. The workspace checks that the answer cites only excerpts it was shown, saves it in the
   conversation and marks the job COMPLETE.

Everything is written under the work folder (a new temporary folder if none is given); no real
data folder is touched and no network is used. tests/test_research_qa_example.py runs this file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "source"

PAPER = """# Temperature and growth of strain K12

We grew strain K12 at 25 C, 30 C and 37 C for 24 hours.

## Results

Growth at 37 C was twice the growth at 25 C. Growth at 30 C lay between the two.

## Limitations

Only one strain was tested, and every condition was run three times.
"""
QUESTION = "How did temperature change the growth of strain K12?"
# What the model catalog offers; in the app this comes from the configured model services.
PROFILE = {"profile_ref": "api:example", "eligible": True, "profile_kind": "API", "binding_hash": "example",
           "model_name": "example-model"}
EXCERPT_ID = re.compile(r"ev_[0-9a-f]{32}")


class ExampleModel:
    """Stands in for a configured chat model. Answers with the first excerpt it was shown."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, *, profile_ref, prompt, job_id, response_schema):
        excerpts = list(dict.fromkeys(EXCERPT_ID.findall(prompt)))
        self.calls.append({"profile_ref": profile_ref, "job_id": job_id, "excerpts": excerpts})
        answer = {"answer": "Growth at 37 C was twice the growth at 25 C.", "citations": excerpts[:1],
                  "needs_agent": False}
        # The same envelope the settings service returns for a real model call.
        return {"response": answer, "model_name": PROFILE["model_name"],
                "execution_receipt": {"status": "PASS", "token_usage": {"input_tokens": len(prompt) // 3}}}


def keep_data_inside(folder: Path) -> None:
    """Point every data folder the product code may use into `folder`, before it is imported."""
    for name in ("MEMORIVE_OPS_ROOT", "MEMORIVE_RUNTIME_LOG_LOG_DIR", "MEMORIVE_VAULT_ROOT",
                 "MEMORIVE_DOCUMENT_PROCESSING_SANDBOX_ROOT", "MEMORIVE_DOCUMENT_PROCESSING_CHROMA_ROOT"):
        target = folder / "data" / name.lower()
        target.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(target)
    if str(SOURCE) not in sys.path:
        sys.path.insert(0, str(SOURCE))


def run(folder: Path) -> dict:
    """Ask one question about the synthetic paper; return what was saved."""
    # The evidence index compares full paths; a short 8.3 name (as in the temp folder) would not match.
    folder = Path(folder).resolve()
    keep_data_inside(folder)
    from memorive_research_workspace.service import ResearchWorkspace

    library = folder / "library"
    library.mkdir(parents=True, exist_ok=True)
    paper = library / "k12-temperature.md"
    paper.write_text(PAPER, encoding="utf-8")

    model = ExampleModel()
    workspace = ResearchWorkspace(folder / "workspace", catalog=lambda: [dict(PROFILE)], model=model)
    try:
        thread = workspace.thread_create()
        material = workspace.index.add_library_file(
            thread["project"], path=paper, root=library, external_id="example-paper",
            expected_hash=hashlib.sha256(paper.read_bytes()).hexdigest(), title="Temperature and growth of K12",
            material_layer="cleaned")
        # The conversation's settings: which model answers, and which material it may draw on.
        workspace.attachments.configure(thread["id"], thread["revision"],
                                        {"profile_ref": PROFILE["profile_ref"], "artifact_ids": [material["id"]]})
        job = workspace.ask(thread["id"], QUESTION, "example-request-0001", artifact_ids=[material["id"]])
        for future in list(workspace.futures.values()):
            future.result(timeout=120)
        job = workspace.job_status(job["id"])
        messages = workspace.thread_get(thread["id"])["messages"]
        answer = next((m for m in reversed(messages) if m["role"] == "assistant"), None)
        return {
            "job_status": job["status"],
            "job_error": job.get("error"),
            "question": QUESTION,
            "answer": answer and answer["text"],
            "citations": [{"excerpt": c["id"], "title": c["title"], "text": c["text"]}
                          for c in (answer or {}).get("citations", [])],
            "excerpts_shown_to_model": model.calls[0]["excerpts"] if model.calls else [],
            "workspace_folder": str(folder / "workspace"),
        }
    finally:
        workspace.close()


if __name__ == "__main__":
    work = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="memorive_example_"))
    print(json.dumps(run(work), ensure_ascii=False, indent=2))
