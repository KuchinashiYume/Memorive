"""Connect explicitly selected research Cards to the existing RETRIEVAL_WEIGHTING field contracts.

This index belongs to the research workspace, never the frozen production RETRIEVAL
index. It does not change a Card, EVIDENCE_REVIEW decision, KNOWLEDGE_ADMISSION state or prior qualification.
"""
import json,re
from pathlib import Path
from memorive_research_workspace.store import digest,now
from .field_registry import expand_card_fields,get_field_registry
from .query_dimensions import build_query_dimension_profile
from .canonical import typed_payload_hash
from .errors import ContractViolation

LABELS={'method':'方法','research_question':'研究问题','research_object':'研究对象','key_results':'结果',
        'key_data':'数据','author_conclusion':'作者结论','boundary_conditions':'适用条件',
        'comparison_context':'比较条件','author_limitations_outlook':'局限与展望'}
FOCUS={'method':r'方法|机理|机制|method|mechanism|procedure',
       'key_results':r'结果|结论|result|conclusion|finding','author_conclusion':r'结论|conclusion',
       'key_data':r'数值|数据|多少|\d|data|numeric|value|percent|flux',
       'boundary_conditions':r'条件|范围|condition|boundary|scope',
       'comparison_context':r'比较|对比|compare|comparison|difference',
       'author_limitations_outlook':r'局限|不足|展望|空白|limit|outlook|future|gap'}

def ensure_fields(index,artifacts):
    counts={};warnings=[];rejected=[]
    for aid,a in artifacts.items():
        if Path(a.get('path','')).suffix.lower()!='.json' or a.get('kind')=='derived':continue
        old=index.store.get('research_field_index',aid)
        if old and old.get('content_hash')==a['content_hash']:
            if old.get('status')=='READY':counts[aid]=old['field_count']
            continue
        try:
            with index.store.tx() as db:
                first=db.execute('SELECT id FROM chunks WHERE artifact_id=? LIMIT 1',(aid,)).fetchone()
                if not first:continue
                index.read(first['id'],a['project'],db=db)
            raw=Path(a['path']).read_bytes()
            if digest(raw)!=a['content_hash']:raise ValueError('EVIDENCE_STALE')
            card=json.loads(raw)
            if not isinstance(card,dict) or card.get('schema_version')!=6:continue
            fields=expand_card_fields(card,card_artifact_ref=aid)
            if not fields:continue
            rows=[]
            for field in fields:
                text=field['canonical_value'];path=field['field_path']
                # The registry anchors numeric items on their metric. Retain
                # their value/unit with that same source item, without inference.
                if path.startswith('key_data/'):
                    item=card['key_data'][int(path.split('/')[1])]
                    values={k:v for k,v in item.items() if k in {'value','unit','condition','group','n'} and v is not None}
                    if values:text+=' '+json.dumps(values,ensure_ascii=False,sort_keys=True)
                fid='evf_'+digest([aid,a['content_hash'],path,text])[:32]
                rows.append((fid,text,field))
            with index.store.tx() as db:
                current=index.store.get('artifact',aid,db=db)
                if not current or current['state']!='active' or current['content_hash']!=digest(raw):raise ValueError('EVIDENCE_STALE')
                for fid,text,field in rows:
                    db.execute('INSERT OR IGNORE INTO chunks VALUES(?,?,?,?,?,?,?,?)',(fid,aid,a['project'],text,1,1,None,digest(text)))
                    index.store.put('research_field',fid,a['project'],dict(field,artifact_id=aid,content_hash=a['content_hash'],
                        field_label=LABELS.get(field['field_path'].split('/')[0],'Card'),text_hash=digest(text),
                        namespace='USER_SELECTED_RESEARCH_MATERIAL',production_index_modified=False),db=db)
                value={'status':'READY','content_hash':a['content_hash'],'field_count':len(rows),'field_ids':[r[0] for r in rows],
                       'registry_hash':get_field_registry()['content_hash'],'indexed_at':now()}
                index.store.put('research_field_index',aid,a['project'],value,db=db)
                index.store.event('RETRIEVAL_WEIGHTING_RESEARCH_CARD_FIELDS_INDEXED',aid,{'field_count':len(rows),'content_hash':a['content_hash'],'namespace':'USER_SELECTED_RESEARCH_MATERIAL'},db)
            counts[aid]=len(rows)
        except (ValueError,OSError,TypeError,KeyError,ContractViolation) as exc:
            rejected.append(aid)
            warnings.append({'component':'CardFields','code':'CARD_FIELDS_UNAVAILABLE','fallback':'excluded_unverified_card','artifact_id':aid})
    return {'counts':counts,'warnings':warnings,'rejected':rejected}

def dimension_profile(query,fields):
    paths=sorted({f['field_path'] for f in fields.values()})
    if not paths:return None
    raw={p:(4.0 if re.search(FOCUS.get(p.split('/')[0],r'(?!)'),query,re.I) else 1.0) for p in paths}
    # A long list must not outweigh a short one merely by having more items.
    families={}
    for p in paths:families[p.split('/')[0]]=families.get(p.split('/')[0],0)+1
    raw={p:v/families[p.split('/')[0]] for p,v in raw.items()};total=sum(raw.values())
    allocations=[{'field_id':p,'allocation':raw[p]/total,'reason_code':'SOFT_QUERY_DIMENSION',
                  'evidence_ref':'query:'+digest(query)} for p in paths]
    registry=get_field_registry()
    return build_query_dimension_profile(query_hash=typed_payload_hash({'query':query}),
        field_registry_hash=registry['content_hash'],allocations=allocations,allocation_method='RESEARCH_SOFT_LEXICAL_DIMENSION_V1',
        fallback_floor=min(v['allocation'] for v in allocations),missing_fields=[],reason_codes=['SOFT_QUERY_DIMENSION'],
        policy_version='MemoResearchFields-v1',frozen_registry=registry)

def field_semantics(scores,fields,profile):
    if not profile:return scores
    weights={r['field_id']:r['allocation'] for r in profile['allocations']};groups={}
    for fid,f in fields.items():
        if fid in scores:groups.setdefault(f['artifact_id'],[]).append(fid)
    result=dict(scores)
    for ids in groups.values():
        mass=sum(weights[fields[i]['field_path']] for i in ids)
        aggregate=sum(weights[fields[i]['field_path']]*scores[i]['score'] for i in ids)/mass
        for fid in ids:
            # Preserve a relevant individual field when unrelated fields exist.
            result[fid]=dict(scores[fid],score=.75*scores[fid]['score']+.25*aggregate,
                field_aggregate=aggregate,field_dimension_profile=profile['content_hash'])
    return result
