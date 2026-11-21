"""Literature logic review: which model may run it, and what is kept from its answer.

Only a configured cloud API or CLI profile may run the review. Every quote the model returns
must be found, word for word, on the cited line of the text it was given; a finding marked as a
priority without explicit conditions, impact and an alternative checked is kept only as a
clarification.
"""
from __future__ import annotations

import copy

import jsonschema
import pytest

from memorive.documents.data_review.logic import build_report, cloud_profile, global_segment, prompt, validate_response
from memorive.models.cli.templates import ADAPTER

TEXT = "\n".join([
    "Samples were incubated at 37 C.",
    "Growth doubled after 20 minutes.",
    "We conclude the enzyme is temperature independent.",
    "Only one temperature was tested.",
])
SEGMENT = {"context_start_line": 1, "end_line": 4}


def _citation(line, quote):
    return {"line": line, "quote": quote}


def _finding(**changes):
    value = {"claim": "The conclusion is broader than the test.", "reasoning": "Only one temperature was used.",
             "conditions": ["if no other temperature data exists"],
             "citations": [_citation(3, "temperature independent"), _citation(4, "Only one temperature")],
             "alternative_explanation": "Other data may be in the supplement.", "alternative_checked": True,
             "missing_material": [], "impact": "CORE", "level": "PRIORITY", "impact_reason": "It is the main claim."}
    value.update(changes)
    return value


def _response(*findings, **changes):
    value = {"completed": True, "scope_summary": "Whole text.", "limitations": [], "findings": list(findings)}
    value.update(changes)
    return value


def test_a_fully_evidenced_priority_finding_is_kept():
    result = validate_response(_response(_finding()), text=TEXT, segment=SEGMENT)
    finding = result["findings"][0]
    assert (finding["level"], finding["source_aligned"], finding["conditions_explicit"]) == ("PRIORITY", True, True)
    assert finding["finding_id"].startswith("logic-")


def test_an_answer_outside_the_schema_or_unfinished_is_refused():
    with pytest.raises(jsonschema.ValidationError):
        validate_response(_response(_finding(), verdict="bad paper"), text=TEXT, segment=SEGMENT)
    with pytest.raises(jsonschema.ValidationError):
        validate_response(_response(_finding(level="FATAL")), text=TEXT, segment=SEGMENT)
    with pytest.raises(ValueError, match="LOGIC_OUTPUT_INCOMPLETE"):
        validate_response(_response(_finding(), completed=False), text=TEXT, segment=SEGMENT)


@pytest.mark.parametrize("citation, segment", [
    (_citation(0, "Samples"), SEGMENT),
    (_citation(5, "Samples"), SEGMENT),
    (_citation(3, "temperature dependent"), SEGMENT),
    (_citation(3, "Samples were incubated"), SEGMENT),
    (_citation(3, " "), SEGMENT),
    (_citation(1, "Samples"), {"context_start_line": 2, "end_line": 4}),
    (_citation(4, "Only one"), {"context_start_line": 1, "end_line": 3}),
    (_citation(0, "Only one temperature"), {"context_start_line": 0, "end_line": 4}),
    (_citation(6, "Samples"), {"context_start_line": 1, "end_line": 10}),
])
def test_a_quote_not_on_the_cited_line_of_the_supplied_text_is_refused(citation, segment):
    with pytest.raises(ValueError, match="LOGIC_CITATION_NOT_IN_SUPPLIED_SOURCE"):
        validate_response(_response(_finding(citations=[citation])), text=TEXT, segment=segment)


def test_a_quote_outside_the_lines_sent_for_a_global_check_is_refused():
    segment = dict(SEGMENT, allowed_lines=[1, 2, 3])
    with pytest.raises(ValueError, match="LOGIC_CITATION_NOT_IN_GLOBAL_SOURCE_PACKET"):
        validate_response(_response(_finding()), text=TEXT, segment=segment)


@pytest.mark.parametrize("changes", [
    {"citations": []}, {"claim": " "}, {"reasoning": ""}, {"alternative_explanation": " "},
])
def test_a_finding_without_its_evidence_is_refused(changes):
    with pytest.raises(ValueError, match="LOGIC_FINDING_EVIDENCE_INCOMPLETE"):
        validate_response(_response(_finding(**changes)), text=TEXT, segment=SEGMENT)


@pytest.mark.parametrize("changes", [
    {"conditions": []}, {"conditions": [" "]}, {"impact": "SECONDARY"}, {"alternative_checked": False},
    {"impact_reason": " "}, {"missing_material": ["supplementary table"]},
])
def test_a_priority_finding_missing_a_prerequisite_becomes_a_clarification(changes):
    finding = validate_response(_response(_finding(**changes)), text=TEXT, segment=SEGMENT)["findings"][0]
    assert finding["level"] == "CLARIFY"
    assert finding["escalation_limited"]


def test_a_minor_finding_with_a_large_impact_becomes_a_clarification():
    finding = validate_response(_response(_finding(level="MINOR", impact="PRIMARY")), text=TEXT, segment=SEGMENT)
    assert finding["findings"][0]["level"] == "CLARIFY"
    kept = validate_response(_response(_finding(level="MINOR", impact="DETAIL")), text=TEXT, segment=SEGMENT)
    assert kept["findings"][0]["level"] == "MINOR"


def test_the_relation_inventory_of_a_segment_is_checked_too():
    segment = dict(SEGMENT, mode="LOCAL_SEGMENT")
    inventory = {"complete": True, "limitations": [],
                 "items": [{"role": "RESULT", "citation": _citation(2, "Growth doubled"), "summary": "s"}]}
    validate_response(_response(_finding(), relation_inventory=inventory), text=TEXT, segment=segment)
    inventory["items"][0]["citation"] = _citation(2, "Growth tripled")
    with pytest.raises(ValueError, match="LOGIC_CITATION_NOT_IN_SUPPLIED_SOURCE"):
        validate_response(_response(_finding(), relation_inventory=inventory), text=TEXT, segment=segment)


GLOBAL = dict(SEGMENT, mode="GLOBAL_RELATIONS", source_segment_ids=["s1", "s2"])


def _global(assessment="POTENTIAL_CONFLICT", covered=("s2", "s1"), relation="Result versus conclusion", **changes):
    check = {"relation": relation, "assessment": assessment, "reasoning": "One temperature cannot show independence.",
             "citations": [_citation(3, "temperature independent"), _citation(4, "one temperature")]}
    check.update(changes)
    return {"complete": True, "covered_segment_ids": list(covered), "checks": [check]}


def test_a_global_conflict_backed_by_a_finding_is_kept():
    result = validate_response(_response(_finding(), global_relations=_global()), text=TEXT, segment=GLOBAL)
    assert len(result["findings"]) == 1


@pytest.mark.parametrize("relations, findings, code", [
    (_global(covered=("s1",)), [_finding()], "GLOBAL_SEGMENT_COVERAGE_INCOMPLETE"),
    (_global(relation=" "), [_finding()], "GLOBAL_RELATION_REASON_REQUIRED"),
    (_global(reasoning=""), [_finding()], "GLOBAL_RELATION_REASON_REQUIRED"),
    (_global(), [], "GLOBAL_CONFLICT_WITHOUT_EVIDENCED_FINDING"),
    (_global(), [_finding(citations=[_citation(3, "temperature independent"), _citation(1, "Samples")])],
     "GLOBAL_CONFLICT_WITHOUT_EVIDENCED_FINDING"),
    (_global(citations=[_citation(3, "independent"), _citation(4, "two temperatures")]), [_finding()],
     "LOGIC_CITATION_NOT_IN_SUPPLIED_SOURCE"),
])
def test_a_global_check_without_coverage_or_evidence_is_refused(relations, findings, code):
    with pytest.raises(ValueError, match=code):
        validate_response(_response(*findings, global_relations=relations), text=TEXT, segment=GLOBAL)


def test_an_undetermined_global_relation_needs_no_finding():
    validate_response(_response(global_relations=_global(assessment="UNDETERMINED")), text=TEXT, segment=GLOBAL)


def test_the_answer_given_is_not_changed():
    value = _response(_finding(conditions=[]))
    before = copy.deepcopy(value)
    validate_response(value, text=TEXT, segment=SEGMENT)
    assert value == before


# Which model may run the review

def _node(kind="API", **service_changes):
    service = {"connection_status": "AVAILABLE", "endpoint_kind": "openai_compatible", "execution_channel": "API",
               "enabled": True, "adapter_id": "codex_cli"}
    service.update(service_changes)
    return {"profile_kind": kind, "profile_ref": "api:provider:model",
            "execution_profile": {"service": service, "model": {"model_name": "model-a"}}}


def test_a_configured_cloud_profile_is_used():
    node = _node()
    assert cloud_profile(node) == node and cloud_profile(node) is not node
    cloud_profile(_node("CLI"))


@pytest.mark.parametrize("node, code", [
    (None, "LOGIC_CLOUD_PROFILE_REQUIRED"),
    (dict(_node(), profile_kind="LOCAL"), "LOGIC_CLOUD_PROFILE_REQUIRED"),
    (dict(_node(), execution_profile=None), "LOGIC_PROFILE_NOT_CONFIGURED"),
    (dict(_node(), execution_profile={"service": {}, "model": None}), "LOGIC_PROFILE_NOT_CONFIGURED"),
    (dict(_node(), profile_ref="local:ollama:qwen"), "LOGIC_LOCAL_MODEL_NOT_PROVIDED"),
    (_node(endpoint_kind="ollama"), "LOGIC_LOCAL_MODEL_NOT_PROVIDED"),
    (_node(execution_channel="LOCAL_INFERENCE"), "LOGIC_LOCAL_MODEL_NOT_PROVIDED"),
    (_node(connection_status="UNVERIFIED"), "LOGIC_PROFILE_UNAVAILABLE"),
    (_node("CLI", enabled=False), "LOGIC_CLOUD_CLI_PROFILE_REQUIRED"),
    (_node("CLI", adapter_id="my_script"), "LOGIC_CLOUD_CLI_PROFILE_REQUIRED"),
    (_node("CLI", adapter_id=ADAPTER), "CLI_TEMPLATE_CAPABILITY_NOT_VERIFIED"),
])
def test_a_local_unconfigured_or_unverified_profile_is_refused(node, code):
    with pytest.raises(ValueError) as caught:
        cloud_profile(node)
    assert str(caught.value) == code


def test_the_model_status_overrides_the_service_status():
    node = _node()
    node["execution_profile"]["model"]["connection_status"] = "UNAVAILABLE"
    with pytest.raises(ValueError, match="LOGIC_PROFILE_UNAVAILABLE"):
        cloud_profile(node)


@pytest.mark.parametrize("citations", [
    [_citation(3, "temperature independent")],
    [_citation(3, "temperature independent"), _citation(3, "temperature independent")],
    [_citation(3, "temperature independent"), _citation(3, "independent")],
])
def test_one_source_passage_does_not_support_priority_by_itself(citations):
    result = validate_response(_response(_finding(citations=citations)), text=TEXT, segment=SEGMENT)
    assert result["findings"][0]["level"] == "CLARIFY"


def test_two_separate_passages_on_one_line_can_support_priority():
    line = "Only one temperature was tested. The study claims the enzyme is temperature independent."
    finding = _finding(citations=[_citation(1, "Only one temperature was tested"),
                                  _citation(1, "the enzyme is temperature independent")])
    result = validate_response(_response(finding), text=line, segment={"context_start_line": 1, "end_line": 1})
    assert result["findings"][0]["level"] == "PRIORITY"


def test_repeating_the_same_quote_is_not_a_global_conflict():
    same = [_citation(3, "temperature independent")] * 2
    result = validate_response(_response(global_relations=_global(citations=same)), text=TEXT, segment=GLOBAL)
    assert result["global_relations"]["checks"][0]["assessment"] == "UNDETERMINED"


def _local(part_id, lo, hi, role, citation, *findings):
    part = {"segment_id": part_id, "mode": "LOCAL_SEGMENT",
            "start_line": lo, "context_start_line": lo, "end_line": hi}
    inventory = {"complete": True, "limitations": [], "items": [
        {"role": role, "citation": citation, "summary": citation["quote"]},
    ] if citation else []}
    return part, _response(*findings, relation_inventory=inventory)


def test_global_packet_includes_local_evidence_missing_from_the_inventory():
    lines = ["Only one temperature was tested.", "The enzyme is temperature independent.",
             *[f"Context line {i}" for i in range(3, 13)],
             "The main result at 37 C.", "The limitation to this experimental setup is explicit."]
    text = "\n".join(lines)
    finding = _finding(citations=[_citation(1, lines[0]), _citation(2, lines[1])])
    first, a = _local("segment-1", 1, 13, "RESULT", _citation(13, lines[12]), finding)
    second, b = _local("segment-2", 14, 14, "LIMITATION", _citation(14, lines[13]))
    part = global_segment(text, [first, second], [a, b], max_chars=12000)
    assert {1, 2, 13, 14} <= set(part["allowed_lines"])


@pytest.mark.parametrize("third_role, expected", [("CONCLUSION", "LIMITED"), ("OTHER", "CHECKED")])
def test_global_report_coverage_comes_from_compared_sources_not_the_segment_id_list(third_role, expected):
    parts, answers = map(list, zip(
        _local("segment-1", 1, 1, "METHOD", _citation(1, "Samples were incubated at 37 C.")),
        _local("segment-2", 2, 2, "RESULT", _citation(2, "Growth doubled after 20 minutes.")),
        _local("segment-3", 3, 4, third_role, _citation(3, "temperature independent")),
    ))
    part = global_segment(TEXT, parts, answers, max_chars=12000)
    comparison = _global(assessment="CONSISTENT", covered=part["source_segment_ids"],
                         relation="Result under the one tested temperature",
                         citations=[_citation(1, "incubated at 37 C"), _citation(2, "Growth doubled")])
    report = build_report(source_id="synthetic", source_sha256="source", text=TEXT,
                          parts=[*parts, part], responses=[*answers, _response(global_relations=comparison)],
                          node=_node(), data_report_id=None)
    scope = report["scope"]["global_relationship_check"]
    assert scope["status"] == expected
    assert scope["compared_segment_ids"] == ["segment-1", "segment-2"]
    assert scope["uncompared_material_segment_ids"] == (["segment-3"] if expected == "LIMITED" else [])


def test_a_full_source_response_that_needed_a_missing_table_is_limited():
    part = dict(SEGMENT, mode="FULL_DOCUMENT", start_line=1)
    result = _response(_finding(missing_material=["Table S2"]))
    report = build_report(source_id="synthetic", source_sha256="source", text=TEXT,
                          parts=[part], responses=[result], node=_node(), data_report_id=None)
    assert report["status"] == "COMPLETED_LIMITED"
    assert report["scope"]["global_relationship_check"]["status"] == "LIMITED"
    assert "Table S2" in report["scope"]["limitations"]


def test_numeric_context_surfaces_in_scope_inconsistencies_before_compatible_rows():
    import json
    from memorive.documents.data_review.engine import analyze

    rows = [f"| Group {i} | 2/5 (40.0%) |" for i in range(25)]
    rows += ["| Wrong | 2/5 (90.0%) |", "| Not supplied | 2/5 (95.0%) |"]
    document = "\n".join(["| Group | n/N (%) |", "| --- | --- |", *rows])
    data = analyze(document, source_id="synthetic")
    target = data["checks"][-2]["rule_id"]
    excluded = data["checks"][-1]["rule_id"]
    part = {"mode": "LOCAL_SEGMENT", "context_start_line": 1, "end_line": 28}
    message = prompt(part, data_report=data, source_sha256=data["source_sha256"])
    checks = json.loads(message.split("\nLITERATURE_INPUT_JSON\n")[1])["data_calculations"]
    ids = [check["rule_id"] for check in checks["checks"]]
    assert ids[0] == target and excluded not in ids
    assert len(ids) == 24 and checks["omitted_checks"] == 3
