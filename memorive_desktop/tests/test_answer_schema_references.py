"""Which references the answer schema holds to the evidence of the request (research/workspace/response_contract.py).

The answer's own citations may name only the evidence the request retained. The references of an opportunity, a
knowledge draft or a research note are checked one by one after the answer is kept, and a wrong one is left out
and noted (answering.py). A command-line tool set up from a template checks its whole answer against this schema
(models/cli/template_execution.py), so a wrong reference inside an opportunity used to turn the whole answer away.
"""
from __future__ import annotations

import jsonschema
import pytest

from memorive.research.opportunities.research_opportunities import schema, validate
from memorive.research.workspace.response_contract import bind_evidence

ALLOWED = ["ev_a", "ev_b"]


def answer(citations, opportunity_ids):
    return {"answer": "Growth doubled.", "citations": citations, "needs_agent": False, "knowledge_draft": None,
            "research_notes": [], "opportunities": [{
                "question": "q", "hypothesis": "h", "rationale": "r", "limitations": "l",
                "classification": "missing_evidence", "evidence_ids": opportunity_ids}]}


def test_an_opportunity_citing_other_evidence_does_not_turn_away_the_answer():
    bound = bind_evidence(schema(), ALLOWED)
    jsonschema.validate(answer(["ev_a"], ["ev_unknown"]), bound)  # used to fail: the enum covered opportunities
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(answer(["ev_unknown"], ["ev_a"]), bound)


def test_a_reference_that_is_not_a_text_is_a_citation_error():
    row = answer([], [{}])["opportunities"][0]
    with pytest.raises(ValueError, match="OPPORTUNITY_CITATION_INVALID"):  # used to be a TypeError
        validate([row], set(ALLOWED))
