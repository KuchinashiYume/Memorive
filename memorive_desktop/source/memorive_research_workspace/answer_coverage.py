"""Answer-scoped coverage; progress is never scientific sufficiency.

Reuses retrieval facet matching on exact, source-bound chunks. Its lexical
signals remain partial until a semantic/human judgement exists. No network,
model, source expansion or Card reconstruction occurs here.
"""
import hashlib,re,math,collections
from .store import digest
from .evidence import tokens,material_layer,source_document_hash
from retrieval.retrieval_quality_retrieval_coverage_artifacts import (
    ArtifactPointer,ChunkSnapshot,FacetSpec,derive_facet_coverage_inputs,
)

FACETS=(
    ('conditions','研究对象与条件','population conditions temperature 对象 条件 温度'),
    ('methods','方法与比较基线','method treatment comparator baseline 方法 对照 基线'),
    ('sample','样本与独立性','sample replicate independent dataset 样本 重复 独立 数据集'),
    ('results','结果与不确定性','result effect confidence uncertainty 结果 效应 区间 不确定性'),
    ('limitations','局限与相反证据','limitation exception contrary uncertainty footnote 局限 限定 反例 相反 脚注'),
)
CONSTRAINT_HINT=re.compile(r'\b(?:however|limitation|except|not significant|no significant|counterexample|contrary|footnote)\b|但|局限|不显著|未显著|例外|脚注',re.I)

def plan(question,scope_ids):
    comparative=bool(re.search(r'比较|对比|异同|差异|跨篇|两篇|多篇|compare|comparison|differ',question,re.I))
    rows=list(FACETS) if comparative else [('question','本次问题',question),FACETS[-1]]
    value={'revision':1,'question':question,'scope_ids':scope_ids,'origin':'question_facet_proposal',
           'facets':[{'id':key,'label':label,'query':query,'required':True} for key,label,query in rows]}
    value['hash']=digest(value);return value

def constraint_candidate(ref):
    return ref.get('counterevidence_candidate') is True or bool(CONSTRAINT_HINT.search(ref['text']))

def document_key(ref):
    return ('source',ref['source_content_hash'].lower()) if ref.get('source_content_hash') else ('document',ref.get('document_id',ref['artifact_id']))

def pack_order(refs,question=None):
    """Protect an observed constraint candidate and source diversity, stably.

    A negative word is a selection hint, never a contradiction verdict.
    Every omitted member is still recorded in the coverage snapshot.
    """
    if question:
        # Query relevance precedes generic negative words and PDF page headers.
        # Preserve one relevant hit and one constraint per document, then fill
        # round-robin. Loaded-but-trimmed ranges remain in the coverage record.
        terms=set(tokens(question));vocab={r['id']:set(tokens(r['text'])) for r in refs}
        frequency=collections.Counter(t for words in vocab.values() for t in words & terms)
        ranked=sorted(refs,key=lambda r:-sum(math.log(1+len(refs)/(1+frequency[t])) for t in vocab[r['id']] & terms))
        groups={}
        for r in ranked:groups.setdefault(document_key(r),[]).append(r)
        head=ranked[:1]
        first_key=document_key(head[0]) if head else None
        constraint=next((r for r in ranked if document_key(r)!=first_key and CONSTRAINT_HINT.search(r['text'])),None)
        if constraint is not None:head.append(constraint)
        represented={document_key(r) for r in head}
        head += [rows[0] for key,rows in groups.items() if key not in represented]
        for rows in groups.values():
            constraint=next((r for r in rows if CONSTRAINT_HINT.search(r['text'])),None)
            if constraint is not None:head.append(constraint)
        # Several lexical hits can come from the same introductory page.
        # After protecting relevant/constraint heads, admit other observed
        # pages before those repeats. This is a coverage heuristic, not a
        # semantic sufficiency claim or a reason to expand the source scope.
        def location(r):
            return ('page',r['page']) if r.get('page') is not None else ('chunk',r['id'])
        used_ids={r['id'] for r in head}
        used_pages={(document_key(r),location(r)) for r in head}
        page_groups=[];repeat_groups=[]
        for key,rows in groups.items():
            pages=[];repeats=[]
            for r in rows:
                if r['id'] in used_ids:continue
                place=(key,location(r))
                if place in used_pages:repeats.append(r)
                else:pages.append(r);used_pages.add(place)
            page_groups.append(pages);repeat_groups.append(repeats)
        def round_robin(rows):
            return [group[i] for i in range(max((len(g) for g in rows),default=0)) for group in rows if i<len(group)]
        tail=round_robin(page_groups)+round_robin(repeat_groups)
        seen=set();out=[]
        for r in head+tail:
            if r['id'] not in seen:out.append(r);seen.add(r['id'])
        return out
    preferred=[r for r in refs if constraint_candidate(r)]
    first={}
    for r in preferred+refs:first.setdefault(document_key(r),r)
    out=[];seen=set()
    for r in list(first.values())+preferred+refs:
        if r['id'] not in seen:out.append(r);seen.add(r['id'])
    return out

def local_candidates(index,question,scope_ids,initial,coverage_plan):
    """One bounded local pass per facet, strictly in the selected materials."""
    if not scope_ids:return list(initial)
    refs=list(initial);seen={r['id'] for r in refs}
    for f in coverage_plan['facets']:
        result=index.legacy_lexical_search(f['query'],coverage_plan['project'],limit=6,artifact_ids=scope_ids)
        for r in result['results']:
            if r['id'] not in seen and len(refs)<36:
                refs.append(dict(r,selection='selected_scope_facet',facet_hint=f['id']));seen.add(r['id'])
    return refs

def _facet_matches(facets,refs):
    specs=[FacetSpec(f['id'],f['query'],True) for f in facets]
    vocab={f['id']:set(tokens(f['query'])) for f in facets}
    by_id={}
    for r in refs:
        if not r.get('text','').strip():continue
        h=hashlib.sha256(r['text'].replace('\r\n','\n').replace('\r','\n').encode()).hexdigest()
        pointer=ArtifactPointer('art_'+digest([r['id'],h])[:32],h,'chunk','memo_answer_snapshot',True,False)
        member=ChunkSnapshot.from_content(chunk_ref=pointer,chunk_id=r['id'],paper_id=r.get('document_id',r['artifact_id']),
            content=r['text'],section_path=None,block_type=None,page_start=r.get('page'),page_end=r.get('page'),sequence_index=0)
        matches=derive_facet_coverage_inputs(specs,[member],token_pattern=r'[A-Za-z0-9_]+|[\u3400-\u9fff]{2,}',minimum_token_length=2,uncovered_status='not_assessed')
        strict={m.facet_id:m for m in matches};candidate_tokens=set(tokens(r['text']));by_id[r['id']]={}
        for f in facets:
            exact=strict.get(f['id']);siblings=set().union(*(v for k,v in vocab.items() if k!=f['id']))
            lexical=sorted((vocab[f['id']]-siblings)&candidate_tokens)
            # Generic retrieval hints do not satisfy the strict span predicate.
            # Keep them visibly as candidates, never promote them to covered.
            terms=list(exact.matched_terms) if exact and exact.matched_terms else lexical
            by_id[r['id']][f['id']]={'matched_terms':terms,'basis':'strict_span_candidate' if exact and exact.matched_terms else 'local_keyword_candidate'}
    return by_id

def snapshot(store,project,coverage_plan,retrieved,read,retained,*,recorded=True):
    scope_ids=coverage_plan.get('scope_ids')
    source_ids=list(dict.fromkeys(scope_ids if scope_ids is not None else [r['artifact_id'] for r in read]))
    sets={name:{r['artifact_id'] for r in refs} for name,refs in [('retrieved',retrieved),('read',read),('retained',retained)]}
    retained_ids={r['id'] for r in retained};sources=[]
    for aid in source_ids:
        a=store.get('artifact',aid) or {};state=a.get('state','unavailable')
        s={'id':aid,'title':a.get('title',aid),'state':state,'source_kind':a.get('kind','unknown'),
           'content_hash':a.get('content_hash'),'document_id':(a.get('source_binding') or {}).get('document_id',a.get('document_id',aid)),
           'material_layer':material_layer(a),'source_content_hash':source_document_hash(a),
           'acquired':'yes' if state=='active' else 'failed' if state=='error' else 'unavailable',
           'parsed':'yes' if state=='active' and a.get('chunks',0)>0 else 'failed' if state=='error' else 'unknown',
           'read_ranges':[{'id':r['id'],'page':r.get('page'),'line_start':r.get('line_start'),'line_end':r.get('line_end'),'content_hash':r['content_hash']} for r in read if r['artifact_id']==aid]}
        for key in sets:s[key]=('yes' if aid in sets[key] else 'no') if recorded else 'unknown'
        unavailable=next((r for r in coverage_plan.get('unavailable_sources',[]) if r['artifact_id']==aid),None)
        if unavailable:s.update(unavailable_reason=unavailable['reason'],freshness_notice='该知识的来源版本当前不可用；保留既有审核状态，等待核对。')
        if not recorded:
            s.update(acquired='unknown',parsed='unknown',read_ranges=[])
        sources.append(s)
    matches=_facet_matches(coverage_plan['facets'],read) if recorded else {}
    facets=[]
    for f in coverage_plan['facets']:
        cells=[]
        for s in sources:
            matched=[r['id'] for r in read if r['artifact_id']==s['id'] and matches.get(r['id'],{}).get(f['id'],{}).get('matched_terms')]
            kept=[i for i in matched if i in retained_ids]
            status='partial' if kept else 'missing' if matched else 'not_assessed'
            notice='候选线索已保留；相关性与内容是否足够仍需核查' if kept else '候选线索被裁剪，当前回答仍有缺口' if matched else '已读范围未定位到此方面，不能判为原文未报告'
            if not recorded:notice='旧回答没有记录该处理阶段'
            elif s.get('unavailable_reason'):notice=s['freshness_notice']
            cells.append({'source_id':s['id'],'title':s['title'],'status':status,'notice':notice,'matched_ids':matched,'retained_ids':kept,
                'candidate_basis':{i:matches[i][f['id']] for i in matched},'semantic_relevance':'not_assessed'})
        facets.append({'id':f['id'],'label':f['label'],'query':f['query'],'required':f['required'],'sources':cells,
            'status':'partial' if any(c['retained_ids'] for c in cells) else 'not_assessed',
            'notice':'按最终上下文列出线索与缺口；尚未判断科学充分性。'})
    # Same bytes OR the same known document cannot count as independent papers.
    groups=[]
    for s in sources:
        keys={('document',s['document_id'])}
        if s['source_content_hash']:keys.add(('source',s['source_content_hash']))
        if s['content_hash']:keys.add(('content',s['content_hash']))
        overlapping=[g for g in groups if g['keys']&keys]
        merged={'keys':set(keys),'ids':[s['id']]}
        for g in overlapping:merged['keys'].update(g['keys']);merged['ids']+=g['ids'];groups.remove(g)
        groups.append(merged)
    duplicate_groups=[sorted(g['ids']) for g in groups if len(g['ids'])>1]
    value={'schema_version':'MemoAnswerCoverage-v1','scope_ids':scope_ids,'explicit_scope':scope_ids is not None and bool(scope_ids),'plan':coverage_plan,
        'scope_notice':('范围固定为本次选中的 '+str(len(source_ids))+' 份材料；未扩展到全库或外部来源。') if scope_ids is not None else '本次为项目检索；没有完整来源分母，不能声称全库或领域已覆盖。',
        'sources':sources,'facets':facets,'retained_ids':sorted(retained_ids),
        'trimmed_count':len({r['id'] for r in read}-retained_ids),'duplicate_groups':duplicate_groups,
        'independence_notice':('检测到 '+str(len(duplicate_groups))+' 组相同内容或同一文献的产物，不能重复计为独立证据。' if duplicate_groups else '')+'其余来源的数据集独立性尚未核查；不同文献数量不等于独立实证数量。',
        'provenance_notice':'Card、已确认知识或摘要仅表示该层材料；缺失仍需回到获准原文核查，不能据此判断原文未报告。',
        'scientific_sufficiency':'not_assessed','recorded':recorded,
        'constraint_candidate_ids':[r['id'] for r in read if constraint_candidate(r)],
        'retained_constraint_ids':[r['id'] for r in retained if constraint_candidate(r)]}
    value['hash']=digest(value);return value
