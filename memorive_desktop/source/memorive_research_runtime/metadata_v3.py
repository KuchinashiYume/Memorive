"""EVO E6 metadata successor: optional fields never invent bibliographic facts.

Legacy fixture connectors remain immutable. This production adapter retains raw
page boundaries, source date precision and version identities separately.
"""
from datetime import date
from calendar import monthrange
from html import unescape
import json, re
from urllib.parse import urlsplit, quote
from defusedxml import ElementTree as ET
from literature_discovery.source_connectors_literature_discovery_connector_framework import normalize_doi, normalize_arxiv_id
from .common import sha

SCHEMA = 'E6NormalizedMetadata-v1'

def plain(value):
    if not isinstance(value, str): return None
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', ' ', value))).strip() or None

def safe_url(value):
    if not isinstance(value, str) or any(ord(c)<32 for c in value): return None
    try: p = urlsplit(value)
    except ValueError: return None
    if p.scheme == 'http' and p.hostname in {'arxiv.org','export.arxiv.org','doi.org','dx.doi.org'}:
        value='https:'+value[5:];p=urlsplit(value)
    return value if p.scheme == 'https' and p.hostname and not p.username and not p.password else None

def links(ids, locations):
    rows=[];seen=set()
    values=[]
    if ids.get('doi'): values.append(('DOI','https://doi.org/'+quote(ids['doi'],safe='/():;._-')))
    values += [('来源', x.get('locator')) for x in locations if x.get('kind')!='PDF_LOCATION_CLAIM']
    if ids.get('arxiv_id'): values.append(('arXiv','https://arxiv.org/abs/'+(ids.get('source_record_id') or ids['arxiv_id'])))
    for label, raw in values:
        url=safe_url(raw)
        if url and url not in seen: rows.append({'label':label,'url':url});seen.add(url)
    return rows

def source_date(parts):
    try:
        raw=parts['date-parts'][0]
        if not 1<=len(raw)<=3 or any(type(x) is not int for x in raw): return None
        date(raw[0],raw[1] if len(raw)>1 else 1,raw[2] if len(raw)>2 else 1)
        return '-'.join([str(raw[0]).zfill(4)]+[str(x).zfill(2) for x in raw[1:]])
    except (KeyError,IndexError,TypeError,ValueError): return None

def date_bounds(value):
    try:
        parts=[int(x) for x in value[:10].split('-')]
        y=parts[0];m=parts[1] if len(parts)>1 else 1;d=parts[2] if len(parts)>2 else 1
        first=date(y,m,d)
        last=date(y,m,monthrange(y,m)[1]) if len(parts)==2 else date(y,12,31) if len(parts)==1 else first
        return first,last
    except (AttributeError,TypeError,ValueError,IndexError): return None,None

def precision(value):
    return 'DAY' if value and len(value)>=10 else 'MONTH' if value and len(value)==7 else 'YEAR' if value and len(value)==4 else 'UNKNOWN'

def _record(provider,record_id,ids,bib,locations):
    fields=[]
    for name in ('authors','abstract','published_date','journal','institutions'):
        if not bib.get(name): fields.append('bibliographic.'+name)
    if not ids.get('doi'): fields.append('identifiers.doi')
    bib['date_precision']=precision(bib.get('published_date'))
    bib['abstract_status']='PROVIDED_BY_SOURCE' if bib.get('abstract') else 'NOT_PROVIDED_BY_SOURCE'
    return {'schema_version':SCHEMA,'source_record_id':record_id,'identifiers':ids,'bibliographic':bib,
        'location_claims':locations,'partial_fields':fields,'source_provider':provider,
        'identity_evidence_level':'EXACT_SOURCE_IDENTIFIER' if ids.get('doi') or ids.get('arxiv_id') else 'SOURCE_RECORD_ONLY',
        'links':links(ids,locations)}

def parse_crossref(raw):
    try:
        payload=json.loads(raw);msg=payload['message'];items=msg['items']
        if payload.get('status')!='ok' or not isinstance(items,list): raise ValueError()
    except (ValueError,KeyError,TypeError): raise ValueError('SOURCE_SCHEMA_DRIFT')
    records=[];skipped=[]
    for n,item in enumerate(items):
        if not isinstance(item,dict): skipped.append({'item_index':n,'reason_codes':['SOURCE_ITEM_INVALID']});continue
        title=plain((item.get('title') or [None])[0]) if isinstance(item.get('title'),list) else None
        try: doi=normalize_doi(item.get('DOI')) if isinstance(item.get('DOI'),str) else None
        except Exception: doi=None
        if doi and not re.fullmatch(r'10\.\d{4,9}/[^\s<>]+',doi): doi=None
        url=safe_url(item.get('URL'))
        if not title or not (doi or url):
            skipped.append({'item_index':n,'reason_codes':['TITLE_OR_SOURCE_IDENTITY_MISSING']});continue
        authors=[];institutions=[]
        for author in item.get('author') or []:
            if not isinstance(author,dict): continue
            given=plain(author.get('given'));family=plain(author.get('family'))
            display=plain(author.get('name')) or plain(' '.join(x for x in (given,family) if x))
            if display: authors.append({'display_name':display,'given':given,'family':family})
            for affiliation in author.get('affiliation') or []:
                name=plain(affiliation.get('name')) if isinstance(affiliation,dict) else None
                if name: institutions.append(name)
        dates={k:source_date(item.get(k)) for k in ('published','published-online','published-print','posted')}
        valid=[(v,k) for k,v in dates.items() if v]
        # The source's first publication assertion is distinct from indexing/deposit.
        published,field=min(valid,key=lambda x:date_bounds(x[0])[0]) if valid else (None,None)
        journal=next((plain(x) for x in item.get('container-title',[]) if plain(x)),None)
        rid=doi or 'crossref-url:'+sha(url)[:32]
        ids={'doi':doi,'arxiv_id':None,'pmid':None,'source_record_id':rid}
        loc=[{'kind':'METADATA_RECORD','locator':url,'materialized':False,'rights_status':'LOCATION_CLAIM_ONLY'}] if url else []
        records.append(_record('CROSSREF',rid,ids,{
            'title':title,'authors':authors,'published_date':published,'publication_date_source':field,
            'publication_dates':dates,'updated_date':None,'indexed_date':(item.get('indexed') or {}).get('date-time'),
            'deposited_date':(item.get('deposited') or {}).get('date-time'),'abstract':plain(item.get('abstract')),
            'record_type':str(item.get('type') or 'unknown'),'journal':journal,
            'publisher':plain(item.get('publisher')),'institutions':sorted(set(institutions)),
            'version':None},loc))
    return {'schema_version':SCHEMA,'records':records,'skipped_records':skipped,'raw_count':len(items),
        'total_results':msg.get('total-results'),'next_source_cursor':msg.get('next-cursor')}

def parse_arxiv(raw):
    try: root=ET.fromstring(raw)
    except Exception as exc: raise ValueError('SOURCE_SCHEMA_DRIFT') from exc
    a='{http://www.w3.org/2005/Atom}';ar='{http://arxiv.org/schemas/atom}';o='{http://a9.com/-/spec/opensearch/1.1/}'
    if root.tag!=a+'feed': raise ValueError('SOURCE_SCHEMA_DRIFT')
    entries=root.findall(a+'entry');records=[];skipped=[]
    for n,entry in enumerate(entries):
        title=plain(entry.findtext(a+'title'));rawid=entry.findtext(a+'id')
        try: aid,rid=normalize_arxiv_id(rawid or '')
        except Exception: skipped.append({'item_index':n,'reason_codes':['ARXIV_ID_INVALID']});continue
        if not title: skipped.append({'item_index':n,'reason_codes':['CORE_TITLE_MISSING']});continue
        authors=[{'display_name':plain(x.findtext(a+'name')),'given':None,'family':None} for x in entry.findall(a+'author') if plain(x.findtext(a+'name'))]
        try: doi=normalize_doi(entry.findtext(ar+'doi'))
        except Exception: doi=None
        ids={'doi':doi,'arxiv_id':aid,'pmid':None,'source_record_id':rid}
        locations=[{'kind':'LANDING_PAGE','locator':'https://arxiv.org/abs/'+rid,'materialized':False,'rights_status':'LOCATION_CLAIM_ONLY'}]
        pub=entry.findtext(a+'published');updated=entry.findtext(a+'updated')
        if not date_bounds(pub)[0]: pub=None
        affiliations=[plain(x.text) for x in entry.iter(ar+'affiliation') if plain(x.text)]
        records.append(_record('ARXIV',rid,ids,{'title':title,'authors':authors,'published_date':pub,
            'publication_date_source':'arxiv.published','updated_date':updated,'indexed_date':None,
            'abstract':plain(entry.findtext(a+'summary')),'record_type':'arxiv-preprint',
            'journal':plain(entry.findtext(ar+'journal_ref')),'platform':'arXiv','institutions':sorted(set(affiliations)),
            'version':rid},locations))
    return {'schema_version':SCHEMA,'records':records,'skipped_records':skipped,'raw_count':len(entries),
        'total_results':int(root.findtext(o+'totalResults') or len(entries)),
        'start_index':int(root.findtext(o+'startIndex') or 0),'next_source_cursor':None}
