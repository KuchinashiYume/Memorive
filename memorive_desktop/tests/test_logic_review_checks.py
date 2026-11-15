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

from memorive_review.logic import cloud_profile, validate_response
from memorive_settings.cli_templates import ADAPTER

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
