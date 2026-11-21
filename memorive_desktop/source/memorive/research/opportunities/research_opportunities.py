"""Research opportunities in an answer: interpretations an agent proposes from cited evidence.

They are kept as drafts. Nothing here judges whether an idea is new or scientifically right.
"""
import copy

FIELDS = ('question', 'hypothesis', 'rationale', 'limitations', 'evidence_ids', 'classification')
TEXT_FIELDS = ('question', 'hypothesis', 'rationale', 'limitations')
CLASSIFICATIONS = ('comparable_tension', 'condition_difference', 'missing_evidence', 'research_direction')
MAX_OPPORTUNITIES = 6


def schema():
    """The answer schema with a list of opportunities added."""
    from memorive.research.workspace.desktop import ANSWER_SCHEMA
    value = copy.deepcopy(ANSWER_SCHEMA)
    properties = {name: {'type': 'string'} for name in TEXT_FIELDS}
    properties['evidence_ids'] = {'type': 'array', 'items': {'type': 'string'}}
    properties['classification'] = {'type': 'string', 'enum': list(CLASSIFICATIONS)}
    value['properties']['opportunities'] = {'type': 'array', 'maxItems': MAX_OPPORTUNITIES, 'items': {
        'type': 'object', 'properties': properties, 'required': list(FIELDS), 'additionalProperties': False}}
    value['required'] = list(value['properties'])
    return value


def instructions():
    """What an agent job is asked; answering.py puts the chat rules after it, so citation marks, the
    answer language and the knowledge draft follow the same rules as a chat answer. The first sentence
    stays as it is: the sessions view (sessions/controller/common.py) recognizes a Memo research task by it."""
    from memorive.research.opportunities.core import CONCEPT_ORDER
    return ('You are the user-selected research agent. Complete the research question using the supplied versioned evidence and conversation. '
            'The user handed it to you for deeper synthesis than a quick chat answer; for citations, language and the knowledge draft '
            'follow the Memorive rules below. '
            'Where relevant, compare '+', '.join(CONCEPT_ORDER)+'; first assess comparability of conditions, methods and measures. '
            'Separate genuine tension, differences in conditions, missing evidence and tentative research directions. '
            'Return opportunities only when asked for deep synthesis, research directions or tensions; otherwise an empty list. '
            'Each opportunity needs evidence_ids, rationale and limitations. Missing search results do not demonstrate novelty. '
            'Do not claim scientific validation.\n')


def validate(items, allowed):
    """The model's opportunities, each citing only excerpts in allowed, marked as unassessed drafts."""
    if not isinstance(items, list) or len(items) > MAX_OPPORTUNITIES:
        raise ValueError('OPPORTUNITY_RESULT_INVALID')
    for row in items:
        if not isinstance(row, dict) or set(row) != set(FIELDS):
            raise ValueError('OPPORTUNITY_RESULT_INVALID')
        if any(not isinstance(row[k], str) or not row[k].strip() for k in TEXT_FIELDS):
            raise ValueError('OPPORTUNITY_RESULT_INVALID')
        if row['classification'] not in CLASSIFICATIONS:
            raise ValueError('OPPORTUNITY_RESULT_INVALID')
        if not isinstance(row['evidence_ids'], list) or not row['evidence_ids'] or any(i not in allowed for i in row['evidence_ids']):
            raise ValueError('OPPORTUNITY_CITATION_INVALID')
    return [dict(r, state='draft', scientific_verdict='NOT_ASSESSED') for r in items]
