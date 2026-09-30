"""Structured bibliographic matches. Free-text mentions are never identity evidence."""
from literature_discovery.source_connectors_literature_discovery_connector_framework import normalize_doi, normalize_arxiv_id, ConnectorError
import re

def canonical_doi(value):
    """The same spelling boundary for work aliases and DOI manifestations."""
    token=value.strip()
    while True:
        stripped=re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:)\s*','',token,flags=re.I).strip()
        if stripped==token:break
        token=stripped
    return normalize_doi(token)

def identity_keys(identifiers):
    keys=set()
    if not isinstance(identifiers,dict):return keys
    for name in ('doi','arxiv_id','wos_uid'):
        value=identifiers.get(name)
        if not isinstance(value,str) or not value.strip():continue
        try:
            normalized=canonical_doi(value) if name=='doi' else normalize_arxiv_id(value.strip())[0] if name=='arxiv_id' else value.strip().upper()
        except (ValueError,TypeError,ConnectorError):continue
        if normalized:keys.add(name+':'+normalized)
    return keys

def record_identifiers(record):
    raw=record.get('identifiers')
    result=dict(raw) if isinstance(raw,dict) else {}
    for box in (record,record.get('metadata') or {},record.get('bibliographic') or {}):
        for key in ('doi','arxiv_id','wos_uid'):
            if box.get(key):result[key]=box[key]
    return result

def exact_library_matches(identifiers,records):
    keys=identity_keys(identifiers)
    return sorted({str(row.get('artifact_id') or row.get('paper_id')) for row in records
        if isinstance(row,dict) and keys.intersection(identity_keys(record_identifiers(row)))})

def metadata_comparison(candidate,records):
    """Discovery relation renderer, explicit metadata-only successor; no fake vectors."""
    from literature_discovery import novelty_assessment_literature_discovery_library_relation_novelty as original
    from .common import sha
    relations=[];unidentified=0
    candidate_keys=identity_keys(candidate['identifiers'])
    c={'candidate_id':candidate['candidate_id'],'content_hash':sha(candidate).upper()}
    for record in records:
        local_keys=identity_keys(record_identifiers(record))
        if not local_keys:unidentified+=1
        overlap=candidate_keys & local_keys
        title=record.get('title') or record.get('display_name') or ''
        same_title=bool(title and original._normalized_text(title)==original._normalized_text(candidate['title']))
        if not overlap and not same_title:continue
        local={'paper_id':str(record.get('artifact_id') or record.get('paper_id')),
            'title':title,'content_hash':sha(record).upper()}
        relations.append(original._make_relation(c,local,'EXACT_SAME_WORK' if overlap else 'POSSIBLE_DUPLICATE',
            evidence_method='NORMALIZED_EXACT_IDENTIFIER' if overlap else 'TITLE_ONLY_REVIEW_SIGNAL',
            evidence_refs=[c['content_hash'],local['content_hash'],*sorted(overlap)],
            similar_dimensions=['work_identity' if overlap else 'title'],different_dimensions=['source_record'],
            evidence_level='EXACT_IDENTITY' if overlap else 'METADATA_ONLY',evidence_scope='IDENTITY_ONLY' if overlap else 'METADATA_ONLY',
            reason_codes=['VERIFIED_IDENTITY_MATCH' if overlap else 'TITLE_MATCH_NOT_IDENTITY_PROOF']))
    exact=any(r['relation_type']=='EXACT_SAME_WORK' for r in relations)
    possible=any(r['relation_type']=='POSSIBLE_DUPLICATE' for r in relations)
    return {'relations':relations,'library_new':'FALSE' if exact else 'UNKNOWN' if unidentified or possible else 'TRUE',
        'identifier_scope':'CURRENT_DESKTOP_STRUCTURED_IDENTIFIERS','unidentified_local_records':unidentified,
        'topic_similarity':'NOT_ASSESSED','method_similarity':'NOT_ASSESSED','object_similarity':'NOT_ASSESSED',
        'citation_relation':'NOT_ASSESSED','reason':'NO_CALIBRATED_SAME_SPACE_VECTOR_OR_CITATION_SNAPSHOT',
        'partial_metadata_recommendation':not exact,'formal_library_writes':0}
