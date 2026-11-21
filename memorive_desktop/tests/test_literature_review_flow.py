"""Replay the real review worker against synthetic RawMD and recorded model replies."""
from __future__ import annotations

import json
from hashlib import sha256
from types import SimpleNamespace

import pytest

from memorive.documents.data_review.service import ReviewService
from memorive.documents.workflow.contracts import LOGIC_NODE


MODEL = "fixture-review-model"
METHOD = "Methods. One cartridge was tested at 37 C."
RESULT = "Results. The paper claims the cartridge is temperature independent."
REPORT_CONDITIONS = {
    "profile_ref": "api:test:review",
    "profile_kind": "API",
    "enabled": True,
    "retry_count": 0,
    "capacity": {"effective_capacity": {"input_tokens": 14336}},
    "execution_profile": {
        "service": {"connection_status": "AVAILABLE", "endpoint_kind": "openai_compatible"},
        "model": {"model_name": MODEL},
    },
}


def _reply(response):
    return {"status": "PASS", "returned_model": MODEL, "response": response,
            "execution_receipt": {"status": "PASS", "profile_kind": "API", "returned_model": MODEL,
                                  "purpose": "core_document_processing", "behavior_sha256": "fixture",
                                  "token_usage": {"input_tokens": 1, "output_tokens": 1}},
            "external_network_calls": 0, "external_model_calls": 0}


def _findings(last_line, report_level="PRIORITY"):
    return [{
        "claim": "The range of the conclusion exceeds the design.",
        "reasoning": "One temperature does not demonstrate invariance across temperatures.",
        "conditions": ["The stated test used only one temperature."],
        "citations": [{"line": 1, "quote": METHOD}, {"line": last_line, "quote": RESULT}],
        "alternative_explanation": "A separate temperature series could support the claim. None is described.",
        "alternative_checked": True, "missing_material": [],
        "impact": "CORE", "level": report_level, "impact_reason": "It is the paper's main claim.",
    }]


class ReplayRunner:
    def __init__(self, method_line, result_line):
        self.method_line = method_line
        self.result_line = result_line
        self.prompts = []

    def execute_structured_chat(self, *, prompt, **_kwargs):
        assert len(prompt.encode("utf-8")) <= 6144
        payload = json.loads(prompt.split("\nLITERATURE_INPUT_JSON\n")[1])
        part = payload["segment"]
        self.prompts.append(payload)
        response = {"completed": True, "scope_summary": "Supplied source.", "limitations": [], "findings": []}
        if part["mode"] == "LOCAL_SEGMENT":
            items = []
            for line, quote, role in [(self.method_line, METHOD, "METHOD"), (self.result_line, RESULT, "CONCLUSION")]:
                if part["start_line"] <= line <= part["end_line"]:
                    items.append({"role": role, "citation": {"line": line, "quote": quote}, "summary": quote})
            response["relation_inventory"] = {"complete": True, "limitations": [], "items": items}
        else:
            response["findings"] = _findings(self.result_line)
            if part["mode"] == "GLOBAL_RELATIONS":
                assert {self.method_line, self.result_line} <= set(part["allowed_lines"])
                response["global_relations"] = {
                    "complete": True, "covered_segment_ids": part["source_segment_ids"],
                    "checks": [{"relation": "Compare methods with the concluding claim.",
                                "reasoning": response["findings"][0]["reasoning"],
                                "citations": response["findings"][0]["citations"], "assessment": "POTENTIAL_CONFLICT"}],
                }
        return _reply(response)


def _setup(tmp_path, text, runner):
    root = tmp_path / "core"
    job_root = root / "review-1"
    job_root.mkdir(parents=True)
    source = job_root / "synthetic.md"
    source.write_bytes(text.encode("utf-8"))
    bound = []
    config = {"nodes": {LOGIC_NODE: REPORT_CONDITIONS}, "literature_review": {"logic_selected": True}}
    item = {"source_name": source.name}
    job = {"attempt_id": "attempt-1", "control_state": "RUNNING",
           "snapshots": {"workflow_definition": {"content": {}}, "workflow_config": {"content": config}}}
    facade = SimpleNamespace(get_job=lambda _: job, store=SimpleNamespace(read_job=lambda _: job),
                             record_node_progress=lambda *_: None)
    worker = SimpleNamespace(run_root=root, facade=facade, inbox=SimpleNamespace(store=SimpleNamespace(read_item=lambda _: item)),
                             source_path=lambda _: source, execution_checkpoint=lambda _: None,
                             execution_interrupt=lambda _: False, bind_artifact=lambda _c, artifact: bound.append(artifact))
    context = SimpleNamespace(job_id="review-1", item_id="item-1", run_root=job_root, workflow_config=config,
                              source_name=source.name, source_sha256=sha256(source.read_bytes()).hexdigest().upper())
    return ReviewService(worker, runner), context, source, bound


@pytest.mark.parametrize("long_source", [False, True])
def test_review_worker_publishes_grounded_logic_and_local_number_findings(tmp_path, long_source):
    lines = [METHOD, "", "| Group | n/N (%) |", "| --- | --- |", "| A | 2/5 (70.0%) |", ""]
    if long_source:
        # Unique Unicode/escaped text forces a real split and exercises wire size.
        lines.extend(f'Method archive "{i}" · 記録 {sha256(str(i).encode()).hexdigest()}'
                     for i in range(50))
    lines.append(RESULT)
    runner = ReplayRunner(1, len(lines))
    service, context, source, bound = _setup(tmp_path, "\n".join(lines), runner)
    ready = service.prepare_raw(context, source)
    assert ready["logic_state"] == "QUEUED"
    data_state = service.data(context)
    data = service.store.report(data_state["data_report_id"])
    assert len(runner.prompts) == 0 and data["external_model_calls"] == 0
    assert [(check["status"], check["calculation"]["expected"]) for check in data["checks"]] == [
        ("INCONSISTENT", "40"),
    ]
    assert service.claim() == context.job_id
    state = service.run_claimed(context.job_id)
    assert state["logic_state"] == ("COMPLETED_LIMITED" if long_source else "COMPLETED"), state
    report = service.store.report(state["logic_report_id"])
    assert report["data_report_id"] == data["report_id"]
    assert report["findings"][0]["level"] == "PRIORITY"
    assert report["scope"]["global_relationship_check"]["status"] == ("CHECKED" if long_source else "FULL_SOURCE_CHECKED")
    assert report["requests"] == len(runner.prompts) == len(ready["segments"])
    assert runner.prompts[-1]["segment"]["mode"] == ("GLOBAL_RELATIONS" if long_source else "FULL_DOCUMENT")
    assert runner.prompts[0]["data_calculations"]["checks"][0]["status"] == "INCONSISTENT"
    assert {artifact.kind for artifact in bound} == {"CORE_DATA_REVIEW", "CORE_DATA_REVIEW_RECORD",
                                                    "CORE_LOGIC_REVIEW", "CORE_LOGIC_REVIEW_RECORD"}


def test_a_saved_response_is_reused_even_when_rebuilding_the_new_prompt_would_not_fit(tmp_path):
    runner = ReplayRunner(1, 2)
    service, context, source, _ = _setup(tmp_path, METHOD + "\n" + RESULT, runner)
    service.prepare_raw(context, source)
    service.data(context, enabled=False)
    path = context.run_root / "logic_responses" / "1-segment-1-0.json"
    path.parent.mkdir()
    response = {"completed": True, "scope_summary": "Supplied source.", "limitations": [], "findings": _findings(2)}
    path.write_text(json.dumps(_reply(response)), encoding="utf-8")
    service.store.update(context.job_id, "REQUEST_PREPARED", {"logic_state": "SENDING", "response_path": str(path),
                                                             "input_capacity_chars": 1})
    restarted = ReviewService(service.worker, runner)
    assert restarted.claim() == context.job_id
    state = restarted.run_claimed(context.job_id)
    assert state["logic_state"] == "COMPLETED", state
    assert not runner.prompts
