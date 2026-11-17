"""What the research workspace reads from the library: the files that document processing produced.

The library controller (memorive/documents/library/product_controller.py) learns about those files from the
processing jobs and offers them through core_artifacts() and resolve_core_private_artifact().
The research workspace uses them in two places:
  memorive/research/workspace/library_bridge.py       adding cards, analyses and documents to a project
  memorive/research/workspace/desktop_connections.py  showing a connected device what a job produced
These tests build one processed paper in a temporary folder; nothing else is read.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from memorive.desktop.service.control_store import ControlStore
from memorive.documents.library import LibraryProductController
from memorive.research.workspace import desktop_connections, library_bridge
from memorive.research.workspace.desktop_connections import job_results
from memorive.research.workspace.store import Store

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "integration"
JOB = "job-paper-1"
FILES = {  # artifact kind: (file name, content)
    "CORE_CARD": ("card.md", "# Card\nGrowth doubles at 37 C."),
    "CORE_ANALYSIS": ("analysis.json", '{"claim": "growth doubles"}'),
    "CORE_RAW_DOCUMENT": ("raw.md", "# Paper\nFull text."),
    "CORE_CHUNKS": ("chunks.jsonl", '{"chunk": 1}\n'),
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


class Paper:
    """One source PDF processed by one job, as the processing pipeline reports it to the library."""

    def __init__(self, root: Path):
        self.root = root / "jobs" / JOB / "artifacts"
        self.root.mkdir(parents=True)
        self.source = self.root / "paper.pdf"
        self.source.write_bytes(b"%PDF synthetic")
        (self.root / "structure").mkdir()
        (self.root / "structure" / "bundle.json").write_text("{}", encoding="utf-8")
        self.ids, self.rows = {}, []
        for number, (kind, (name, text)) in enumerate(FILES.items()):
            path = self.root / name
            path.write_bytes(text.encode("utf-8"))  # exact bytes, no newline translation
            artifact_id = "core-product-" + f"{number:024x}"
            self.ids[kind] = artifact_id
            self.rows.append({
                "artifact_id": artifact_id, "job_id": JOB, "artifact_kind": kind, "kind": kind,
                "display_name": f"paper · {name}", "content_sha256": sha256(path.read_bytes()),
                "private_path": str(path), "artifact_root": str(self.root),
                "source_private_path": str(self.source), "source_root": str(self.root),
                "source_content_sha256": sha256(self.source.read_bytes()), "source_file_name": "paper.pdf",
                "updated_at": "2026-11-15T00:00:00Z",
            })

    def hash_of(self, kind: str) -> str:
        return next(row["content_sha256"] for row in self.rows if row["artifact_kind"] == kind)


@pytest.fixture
def paper(tmp_path):
    return Paper(tmp_path)


@pytest.fixture
def library(tmp_path, paper):
    return LibraryProductController(
        service=None, control_store=ControlStore(tmp_path / "ui"), contract_root=CONTRACTS,
        fixture_root=tmp_path / "unused", state_root=tmp_path / "library",
        include_synthetic_projection=False, core_artifact_provider=lambda: paper.rows)


class Index:
    """Stands in for the project's search index (workspace.index)."""

    def __init__(self, fail_for=()):
        self.added, self.fail_for = [], set(fail_for)

    def add_library_file(self, project, **file):
        if file["external_id"] in self.fail_for:
            raise ValueError("INDEX_FAILED")
        self.added.append(dict(file, project=project))
        return {"id": "mat_" + file["external_id"][-4:], "title": file["title"]}


def research_workspace(library, index=None):
    workspace = SimpleNamespace(index=index or Index())
    library_bridge.bind(workspace, library)
    return workspace


def test_core_artifacts_lists_every_product_and_the_document(library, paper):
    records = library.core_artifacts()
    kinds = {record["artifact_kind"] for record in records.values()}
    assert kinds == set(FILES) | {"CORE_DOCUMENT"}  # one extra record stands for the whole paper
    card = records[paper.ids["CORE_CARD"]]
    assert (card["job_id"], card["content_sha256"]) == (JOB, paper.hash_of("CORE_CARD"))
    assert card["source_content_sha256"] == sha256(paper.source.read_bytes())
    assert library.get_artifact(paper.ids["CORE_CARD"])["artifact_kind"] == "CORE_CARD"


def test_research_is_offered_the_card_analysis_and_documents(library, paper):
    listed = research_workspace(library).library_list()["items"]
    assert {item["kind"] for item in listed} == {"CORE_CARD", "CORE_ANALYSIS", "CORE_RAW_DOCUMENT"}
    card = next(item for item in listed if item["kind"] == "CORE_CARD")
    assert card == {"id": paper.ids["CORE_CARD"], "title": "paper · card.md", "kind": "CORE_CARD",
                    "content_hash": paper.hash_of("CORE_CARD"), "state": "待人工复核",
                    "document_id": "doc_" + sha256(paper.source.read_bytes())}


def test_chosen_files_are_added_with_their_layer(library, paper):
    index = Index()
    workspace = research_workspace(library, index)
    chosen = [{"id": paper.ids[kind], "content_hash": paper.hash_of(kind)} for kind in ("CORE_CARD", "CORE_RAW_DOCUMENT")]
    result = workspace.library_add("project-1", chosen)
    assert (result["status"], result["indexed"], result["failures"]) == ("PASS", 2, [])
    card, raw = index.added
    assert (card["material_layer"], card["structure_bundle"]) == ("card", None)
    assert card["path"] == paper.root / "card.md" and card["root"] == str(paper.root)
    assert card["expected_hash"] == paper.hash_of("CORE_CARD") and card["project"] == "project-1"
    # An original document brings its page layout, so answers can cite pages.
    assert (raw["material_layer"], raw["structure_bundle"]) == ("original", paper.root / "structure")
    assert raw["document_id"] == card["document_id"] == "doc_" + sha256(paper.source.read_bytes())


def test_a_file_that_changed_since_the_list_must_be_chosen_again(library, paper):
    workspace = research_workspace(library)
    with pytest.raises(ValueError, match="LIBRARY_SELECTION_STALE"):
        workspace.library_add("project-1", [{"id": paper.ids["CORE_CARD"], "content_hash": "0" * 64}])
    with pytest.raises(ValueError, match="LIBRARY_SELECTION_STALE"):  # chunks are not offered
        workspace.library_add("project-1", [{"id": paper.ids["CORE_CHUNKS"], "content_hash": paper.hash_of("CORE_CHUNKS")}])
    (paper.root / "card.md").write_text("edited", encoding="utf-8")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        workspace.library_add("project-1", [{"id": paper.ids["CORE_CARD"], "content_hash": paper.hash_of("CORE_CARD")}])


def test_a_file_the_index_refuses_is_reported_and_the_rest_are_added(library, paper):
    index = Index(fail_for={paper.ids["CORE_ANALYSIS"]})
    workspace = research_workspace(library, index)
    chosen = [{"id": paper.ids[kind], "content_hash": paper.hash_of(kind)} for kind in ("CORE_CARD", "CORE_ANALYSIS")]
    result = workspace.library_add("project-1", chosen)
    assert (result["status"], result["indexed"]) == ("PARTIAL", 1)
    assert result["failures"] == [{"id": paper.ids["CORE_ANALYSIS"], "code": "INDEX_FAILED"}]


def test_a_connected_device_reads_only_its_job(tmp_path, paper):
    """The status poll runs every second: it builds and reads its own job's files, not the whole library."""
    asked = []

    def one_job(job_id):
        asked.append(job_id)
        return [row for row in paper.rows if row["job_id"] == job_id]

    library = LibraryProductController(
        service=None, control_store=ControlStore(tmp_path / "ui"), contract_root=CONTRACTS,
        fixture_root=tmp_path / "unused", state_root=tmp_path / "library", include_synthetic_projection=False,
        core_artifact_provider=lambda: pytest.fail("the whole library was read"), core_job_artifact_provider=one_job)
    results = job_results(library, JOB)
    assert [item["kind"] for item in results] == ["CORE_CARD", "CORE_ANALYSIS"] and asked == [JOB]


def test_the_rows_of_one_job_ask_the_service_for_that_job_only(tmp_path):
    from memorive.documents.library.core_products import CoreProducts

    calls = []

    class Service:
        def call(self, method, params):
            calls.append((method, params.get("job_id")))
            if method == "list_jobs":
                return {"jobs": [{"job_id": f"job-{n}", "job_type": "memorive-core"} for n in range(3)]}
            return {"job_id": params["job_id"], "control_state": "SUCCEEDED", "artifact_bindings": [{"artifact_id": "x"}]}

    def write_json_file(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    products = CoreProducts(service=Service(), profile_path=lambda name: tmp_path / name, state_dir=tmp_path / "state",
                            write_json_file=write_json_file, is_reparse_point=lambda path: False)
    rows = products.rows("job-1")         # the job folder is missing: one row saying the files cannot be shown
    assert [row["job_id"] for row in rows] == ["job-1"]
    assert calls == [("list_jobs", None), ("get_job", "job-1"), ("get_job", "job-1")]


def test_a_connected_device_gets_the_card_and_analysis_of_its_job(library, paper):
    results = {item["kind"]: item for item in job_results(library, JOB)}
    assert set(results) == {"CORE_CARD", "CORE_ANALYSIS"}
    assert results["CORE_CARD"]["markdown"] == FILES["CORE_CARD"][1]
    assert results["CORE_ANALYSIS"]["markdown"] == "```json\n" + json.dumps({"claim": "growth doubles"}, indent=2) + "\n```"
    card_bytes = (paper.root / "card.md").read_bytes()
    assert results["CORE_CARD"]["source_hash"] == hashlib.sha256(card_bytes).hexdigest()
    assert job_results(library, "another-job") == []


def test_a_connected_device_is_never_given_a_changed_file(library, paper):
    # What is handed on is the content whose hash was checked, read once.
    (paper.root / "card.md").write_bytes(b"replaced after the job finished")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        job_results(library, JOB)


def test_a_connected_device_follows_its_job_until_the_results_are_ready(tmp_path, library):
    store = Store(tmp_path / "workspace")
    store.put("connector_action", "action-1", "project-1", {"status": "RUNNING", "project": "project-1"})
    stopped = threading.Event()
    stopped.set()  # the background loop ends at once
    workspace = SimpleNamespace(store=store, connections=SimpleNamespace(), conversations=None, connector_stop=stopped)
    desktop_connections.bind(
        workspace, library=library,
        service=SimpleNamespace(call=lambda method, params: {"control_state": "SUCCEEDED"}),
        desktop_call=lambda method, params: {"item_id": params["item_id"], "job_id": JOB, "state": "DONE"},
        stage_inbox_paths=None)
    workspace.connector_thread.join(5)
    # An action that was running when the desktop stopped is marked, so the user checks it first.
    assert store.get("connector_action", "action-1")["status"] == "INTERRUPTED"
    status = workspace.connections.processing_status({"processing": {"item_id": "item-1"}})
    assert (status["processing"]["job_id"], status["processing"]["control_state"]) == (JOB, "SUCCEEDED")
    assert {item["kind"] for item in status["results"]} == {"CORE_CARD", "CORE_ANALYSIS"}
    assert workspace.connections.processing_status({}) is None
