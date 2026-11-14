"""Structured bibliographic matches. Free-text mentions are never identity evidence."""
from literature_discovery.connector_framework import normalize_doi, normalize_arxiv_id, ConnectorError
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
