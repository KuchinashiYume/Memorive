"""Constrain generated references to this request's retained evidence.

The existing exact-ID and source-freshness checks remain authoritative.  This
schema prevents avoidable copying/annotation errors; it never repairs an ID or
claims that a legal reference scientifically supports an answer.
"""
import copy

REVISION = 'ResearchEvidenceResponse-v1'


def bind_evidence(schema, evidence_ids):
    allowed = list(dict.fromkeys(evidence_ids))
    if any(not isinstance(identity, str) or not identity for identity in allowed):
        raise ValueError('RESPONSE_EVIDENCE_ID_INVALID')
    # Match the stable prompt projection and avoid cache churn from hit order.
    allowed.sort()
    value = copy.deepcopy(schema)

    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            for name, field in node.get('properties', {}).items():
                if name in {'citations', 'evidence_ids'} and field.get('type') == 'array':
                    field['description'] = (
                        'Exact complete IDs of evidence retained in this request. '
                        'Copy an allowed ID verbatim. No shortened IDs, page numbers, '
                        'brackets or explanatory text in these strings. Put locators '
                        'in the answer prose. Return [] if no reference is used. '
                        'A legal ID does not itself establish support for a claim.'
                    )
                    field['items'] = {'type': 'string'}
                    if allowed:
                        field['items']['enum'] = allowed.copy()
                    else:
                        field['maxItems'] = 0
            for child in node.values():
                visit(child)

    visit(value)
    return value
