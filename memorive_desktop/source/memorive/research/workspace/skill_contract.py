"""Wire contract shared by MCP, the scoped API and file returns."""

def obj(properties, required=None):
    return {'type': 'object', 'properties': properties, 'required': list(properties) if required is None else required,
            'additionalProperties': False}

def text(maximum=16000):
    return {'type': 'string', 'minLength': 1, 'maxLength': maximum, 'pattern': r'\S'}

BINDING = {k: text(200) for k in ('project', 'handoff_id', 'handoff_hash')}
SUPPORT = obj({'evidence_id': text(200), 'content_hash': text(64), 'quote': text(4000)})
CLAIM = obj({'text': text(), 'kind': {'enum': ['source_statement', 'inference', 'background', 'user_hypothesis']},
             'supports': {'type': 'array', 'maxItems': 24, 'items': SUPPORT}})
COVERAGE = obj({'artifact_id': text(200), 'status': {'enum': ['read', 'partial', 'unread']},
                'gaps': {'type': 'string', 'maxLength': 4000}})
ANSWER = obj({**BINDING, 'request_id': text(96), 'answer': text(48000),
              'claims': {'type': 'array', 'maxItems': 100, 'items': CLAIM},
              'coverage': {'type': 'array', 'maxItems': 2000, 'items': COVERAGE}})
CONTEXT = obj({**BINDING, 'artifact_id': text(200), 'cursor': {'type': 'integer', 'minimum': 0},
               'limit': {'type': 'integer', 'minimum': 1, 'maximum': 24}}, list(BINDING))

def validate_answer(value):
    import jsonschema
    try:
        jsonschema.validate(value, ANSWER)
    except jsonschema.ValidationError:
        raise ValueError('SKILL_ANSWER_INVALID') from None
