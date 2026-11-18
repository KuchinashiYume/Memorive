"""Explicit query objectives, and evidence of how a candidate's metadata relates to them that a person can inspect."""
from datetime import date,datetime,timedelta,timezone
import math,re
from memorive.research.common import sha
from memorive.research.discovery.metadata import date_bounds

OBJECTIVES=('latest','related')
LIMITS={'max_requests':12,'max_pages_per_query':2,'page_size':20,'max_elapsed_seconds':240,
    'max_seed_requests':4,'automatic_retries':0,'paid_model_calls':0}
STOP={'and','or','the','of','in','on','for','to','a','an','with','by','from','as','at','is','are',
    'study','using','based','effects','effect','research','system','systems','review','role','into','via'}

def tokens(value):
    return set(x for x in re.findall(r'[a-z0-9]+|[\u3400-\u9fff]{2,}',str(value).casefold()) if x not in STOP and len(x)>2)

def keyword_in_text(keyword,text):
    parts=re.findall(r'[a-z0-9]+|[\u3400-\u9fff]+',keyword.casefold())
    if not parts:
        return False
    return bool(re.search(r'(?<!\w)'+r'[\W_]+'.join(re.escape(x) for x in parts)+r'(?!\w)',text.casefold())
                or (len(parts)>1 and ''.join(p[0] for p in parts).upper() in acronyms(text)))

def acronyms(text):
    """Upper-case abbreviations written in text, plural s dropped: RAG, LLMs -> {'RAG', 'LLM'}."""
    return {m.rstrip('s') for m in re.findall(r'(?<![\w-])[A-Z][A-Z0-9]{1,5}s?(?![\w-])',str(text))}

def acronym_terms(query,text):
    """Query terms that text names by an abbreviation of the query words they stand in: with the query
    "retrieval augmented generation citation", a text saying RAG names retrieval, augmented and
    generation. Only runs of two to five consecutive query words count."""
    words=[w for w in re.findall(r'[a-z0-9]+',str(query).casefold()) if w not in {'and','or','the','of','in','for','to'}]
    found=acronyms(text)
    terms=set()
    for size in range(2,6):
        for start in range(len(words)-size+1):
            run=words[start:start+size]
            if ''.join(w[0] for w in run).upper() in found:
                terms|=tokens(' '.join(run))
    return terms

def request_options(objective=None,direction_id=None,days=None,as_of=None):
    objective=objective or 'related'
    if objective not in OBJECTIVES:
        raise ValueError('RESEARCH_OBJECTIVE_INVALID')
    if direction_id is not None and (not isinstance(direction_id,str) or not direction_id.startswith('direction-')):
        raise ValueError('RESEARCH_DIRECTION_ID_INVALID')
    if days is None:
        days=90
    if type(days) is not int or not 1<=days<=365:
        raise ValueError('RESEARCH_DATE_WINDOW_INVALID')
    end=date.fromisoformat(as_of) if as_of else datetime.now(timezone.utc).date()
    return {'objective':objective,'direction_id':direction_id,'as_of':end.isoformat(),
        'publication_window':{'from':(end-timedelta(days=days)).isoformat(),'until':end.isoformat(),'days':days} if objective=='latest' else None,
        'limits':dict(LIMITS),'method':'EXPLICIT_METADATA_TERMS_AND_SEED_COMPARISON_V1',
        'quality_calibration':'NOT_A_CALIBRATED_SEMANTIC_PROBABILITY'}

def plans(direction,seeds,options):
    query=direction['query'].strip()
    keywords=list(dict.fromkeys(x.strip() for x in direction.get('keywords',[]) if x.strip()))
    rows=[{'kind':'DIRECTION_QUERY','query':query}]
    if keywords:
        rows.append({'kind':'KEYWORD_EXPANSION','query':query+' '+ ' '.join(keywords[:6]),
            'arxiv_query':'('+arxiv_query(query)+') AND ('+' OR '.join('all:"'+x.replace('"',' ')+'"' for x in keywords[:6])+')'})
    if options['objective']=='related' and seeds:
        seed=seeds[0]
        bib=seed.get('bibliographic',seed)
        title=bib.get('title')
        if title:
            rows.append({'kind':'SEED_TITLE_RECALL','query':title,'seed_id':seed.get('seed_id')})
    return [{**row,'direction_id':direction['id'],'direction_revision':direction['revision'],
        'keywords':keywords,'seed_ids':direction.get('seed_ids',[]),'query_sha256':sha(row)} for row in rows]

def arxiv_query(query):
    # Preserve an explicitly supplied provider expression; ordinary text is a phrase.
    if re.search(r'\b(?:all|ti|abs|au|cat):',query):
        return query
    terms=re.findall(r'"[^"]+"|[^\s]+',query)
    result=[]
    previous=False
    for term in terms:
        if term.upper() in {'AND','OR','ANDNOT'}:
            if previous:
                result.append(term.upper())
                previous=False
        else:
            if previous:
                result.append('AND')
            result.append('all:'+term)
            previous=True
    if not previous and result:
        result.pop()
    return ' '.join(result)

def source_query(plan,options):
    return {'text':plan['query'],'arxiv_query':plan.get('arxiv_query') or arxiv_query(plan['query']),
        'objective':options['objective'],'publication_window':options['publication_window']}

def assess(record,direction,seeds,options):
    bib=record['bibliographic']
    title=bib['title']
    abstract=bib.get('abstract') or ''
    title_tokens=tokens(title)
    all_tokens=title_tokens|tokens(abstract)
    base=tokens(direction['query'])
    matched=base&all_tokens
    # A paper may name a phrase of the query only by its abbreviation ("RAG"). Its words count when the
    # paper also shares another query term written out, so an unrelated use of the same letters alone
    # (a project report's RAG status) does not make a paper related.
    abbreviated=acronym_terms(direction['query'],title+' '+abstract)&base
    if matched-abbreviated:
        matched|=abbreviated
    matched=sorted(matched)
    keywords=[k for k in direction.get('keywords',[]) if keyword_in_text(k,title+' '+abstract)]
    # How many query terms a candidate must share: half of them, at least two (all of a shorter query), at most
    # three (so a long query still finds papers). Two everyday words are not enough: with only "two terms and one
    # keyword", a review of a drug's use in Cushing syndrome passed for a direction on citation evidence in RAG,
    # because its abstract mentions citation searching and an evidence map.
    needed=max(min(2,len(base)),min(3,math.ceil(len(base)/2)))
    # Explicit context terms disambiguate shared words across unrelated fields.
    context_required=bool(direction.get('keywords'))
    supported=bool(base and len(matched)>=needed and (not context_required or keywords))
    comparisons=[]
    for seed in seeds:
        sb=seed.get('bibliographic',seed)
        shared=sorted(tokens(sb.get('title','')+' '+(sb.get('abstract') or ''))&all_tokens&(base|set().union(*(tokens(k) for k in direction.get('keywords',[])))))
        if shared: comparisons.append({'seed_id':seed.get('seed_id'),'seed_title':sb.get('title'),
            'shared_terms':shared,'evidence_scope':'TITLE_AND_ABSTRACT' if abstract and sb.get('abstract') else 'TITLE_OR_PARTIAL_ABSTRACT',
            'relationship':'SHARED_TOPIC_TERMS_NOT_MECHANISM_PROOF'})
    earliest,latest=date_bounds(bib.get('published_date'))
    window=options['publication_window']
    date_eligible=True
    if window:
        date_eligible=bool(earliest and earliest>=date.fromisoformat(window['from']) and latest<=date.fromisoformat(window['until']))
    scope='TITLE_AND_ABSTRACT' if abstract else 'TITLE_ONLY'
    labels=keywords or matched
    reason='与「'+direction['name']+'」共有'+('摘要与标题中的' if abstract else '标题中的')+'主题词：'+('、'.join(labels[:6]) or '证据不足')+'。'
    if comparisons:
        first=comparisons[0]
        reason+='与种子「'+str(first['seed_title'] or first['seed_id'])+'」共有 '+', '.join(first['shared_terms'][:5])+'。'
    if not abstract:
        reason+='来源未提供摘要，关系仅供初筛。'
    if any(x in all_tokens for x in {'diet','mechanism','mechanisms'}):
        reason+='共同术语不能证明同一机制。'
    evidence=[]
    for term in labels[:6]:
        needle=next(iter(sorted(tokens(term),key=len,reverse=True)),'')
        source=title if needle in title.casefold() else abstract
        at=source.casefold().find(needle)
        if at>=0:
            evidence.append({'term':term,'field':'title' if source==title else 'abstract','excerpt':source[max(0,at-65):at+150]})
    return {'schema_version':'E6MetadataRelation-v1','direction_id':direction['id'],'direction_revision':direction['revision'],
        'supported':supported,'context_terms_required':context_required,'evidence_scope':scope,'matched_query_terms':matched,'matched_keywords':keywords,
        'seed_comparisons':comparisons,'evidence':evidence,'recommendation_reason':reason,
        'ranking_value':len(matched)+2*len(keywords)+sum(len(x['shared_terms']) for x in comparisons)/max(1,len(comparisons)),
        'query_token_coverage':len(matched)/len(base) if base else 0,'publication_eligible':date_eligible,
        'date_precision':bib.get('date_precision','UNKNOWN'),'objective':options['objective'],
        'claim_scope':'METADATA_RELATION_ONLY_FULLTEXT_NOT_READ','calibrated_similarity':None}
