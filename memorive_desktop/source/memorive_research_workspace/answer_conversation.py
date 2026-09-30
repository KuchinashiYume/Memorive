"""E1 operations through the existing chat input; opening details never calls a model."""
import re,time
from .store import uid,now,packed
from .answer_evidence import CONTRACT
from .answer_intents import intent,language,say,pending_target

STATUSES={'SUPPORTED':'有支持','PARTIAL':'部分支持','UNSUPPORTED':'缺少支持','CONFLICT':'存在冲突','NEEDS_REVIEW':'需复核','NOT_REVIEWED':'未审核'}

def append_reply(thread,question,text,refs,*,source,action,engine='evidence_only',usage=None,elapsed_ms=0,job=None):
    message={'id':uid('msg_'),'role':'assistant','text':text,'created_at':now(),'citations':refs,'engine':engine,'usage':usage or {},
        'answer_version':1,'artifact_ids':source.get('artifact_ids',[]),'elapsed_ms':elapsed_ms,'needs_agent':False,
        'language_context':(job or {}).get('language_context'),'e1_action':action,'e1_target_answer_id':source['id'],'context':{'memory_ids':[],'summary_message_ids':[],'chars':sum(len(r['text']) for r in refs)}}
    from . import answer_versions
    path=answer_versions.visible(thread)
    user=(job or {}).get('question_message') or {'id':uid('msg_'),'role':'user','text':question,'created_at':now(),
        'artifact_ids':message['artifact_ids'],'parent_answer_id':path[-1]['id'] if path else None}
    answer_versions.append(thread,user,message,selection_revision=(job or {}).get('selection_revision',thread['selection_revision']))
    return message

def review_reply(check,rows,question='',language_context=None):
    from memorive_language import locale
    lang=locale(language_context).split('-')[0] if language_context else language(question or check['question'])
    texts={c['id']:c['text'] for c in check['claims']};refs=[];lines=[]
    for n,row in enumerate(rows,1):
        citations=[]
        for ref in row['evidence']:
            if ref['id'] not in {r['id'] for r in refs}:refs.append(ref)
            citations.append('['+str(next(i for i,r in enumerate(refs,1) if r['id']==ref['id']))+']')
        from .answer_evidence import CITATION_MARK
        claim=re.sub(CITATION_MARK,'',texts[row['id']]).strip()
        labels={'SUPPORTED':('有支持','Supported','支持あり'),'PARTIAL':('部分支持','Partial support','部分的支持'),'UNSUPPORTED':('缺少支持','Unsupported','支持なし'),'CONFLICT':('存在冲突','Conflicting evidence','証拠と矛盾'),'NEEDS_REVIEW':('需复核','Needs review','要確認'),'NOT_REVIEWED':('未审核','Not reviewed','未確認')}
        number=next(i for i,c in enumerate(check['claims'],1) if c['id']==row['id'])
        line=f"{number}. {claim}\n{say(lang,*labels[row['status']])}：{row['reason']}"
        if row.get('conditions'):line+='\n'+say(lang,'适用条件：','Conditions: ','適用条件：')+row['conditions']
        if row.get('mechanical_issues'):
            from memorive_language.evidence import generated
            line+='\n'+ '；'.join(generated(issue,language_context) for issue in row['mechanical_issues'])
        if citations:line+=' '+''.join(citations)
        lines.append(line)
    return '\n\n'.join(lines),refs

def coverage_reply(check,lang='zh'):
    cov=check['coverage'];lines=[]
    for source in cov['sources']:
        count=sum(r['artifact_id']==source['id'] for r in check['retained_evidence'])
        layer=say(lang,*{'original':('原文','Original','原文'),'card':('Card','Card','Card'),'analysis':('Analysis','Analysis','Analysis'),'confirmed_knowledge':('已确认知识','Confirmed knowledge','確認済み知識')}.get(source.get('material_layer'),('材料层未确认','Unconfirmed material layer','資料層未確認')))
        lines.append(source['title']+' · '+say(lang,'已加载片段','Loaded excerpts','読み込み済み断片')+' '+str(len(source.get('read_ranges',[])))+' · '+say(lang,'本次上下文','Answer context','今回のコンテキスト')+' '+str(count)+' · '+layer)
    for facet in cov['facets']:
        states=[]
        for source in facet['sources']:
            state_labels={'partial':('上下文中有线索，充分性未核实','Context contains leads; sufficiency unassessed','コンテキスト内に手掛かりあり、十分性未確認'),'missing':('线索未进入上下文','Leads omitted from context','手掛かりはコンテキスト外'),'not_assessed':('加载范围内未定位线索','No lead located in loaded excerpts','読み込み範囲で手掛かり未特定'),'covered':('已覆盖','Covered','確認済み')}
            state=say(lang,*state_labels[source['status']])
            states.append(source['title']+'：'+state)
        labels={'conditions':('研究对象与条件','Objects and conditions','対象と条件'),'methods':('方法与比较基线','Methods and baselines','方法と比較基準'),'sample':('样本与独立性','Samples and independence','標本と独立性'),'results':('结果与不确定性','Results and uncertainty','結果と不確実性'),'limitations':('局限与相反证据','Limitations and contrary evidence','限界と反証'),'question':('本次问题','Current question','今回の質問')}
        lines.append(say(lang,*labels[facet['id']])+'\n'+'；'.join(states))
    return '\n\n'.join(lines),check['retained_evidence']

def run_if_requested(ws,identity,question):
    request=intent(question)
    job=ws.store.get('job',identity)
    if not job or job['status'] not in {'QUEUED','RUNNING'}:return True
    thread=ws.versions.generation_thread(job)
    if request is None:
        previous=thread['messages'][-1] if thread['messages'] else {}
        pending=previous.get('e1_pending_request');sel=pending_target(question)
        if pending=='review' and sel and len(question)<160:request={'action':'review',**sel}
        else:return False
    source=next((m for m in reversed(thread['messages']) if m['role']=='assistant' and not m.get('e1_action')),None)
    if source is None:raise ValueError('ANSWER_NOT_FOUND')
    check=ws.answer_evidence.check(thread['id'],source['id']);action=request['action'];started=time.monotonic()
    from memorive_language import locale
    lang=locale(job.get('language_context')).split('-')[0]
    if 'quote' in request:
        matches=[i for i,c in enumerate(check['claims']) if request['quote'] in c['text']]
        if len(matches)==1:request['index']=matches[0]
        else:request={'action':'clarify','reason':'target','pending':action};action='clarify'
    if request.get('ambiguous') or ('index' in request and not 0<=request['index']<len(check['claims'])):
        request={'action':'clarify','reason':'target','pending':action};action='clarify'
    if action=='review' and 'index' not in request and len(check['claims'])>16:
        request={'action':'clarify','reason':'many','pending':'review'};action='clarify'
    if job.get('cancel_requested'):raise ValueError('RESEARCH_CANCELLED')
    if check['freshness']!='CURRENT':raise ValueError('EVIDENCE_CHANGED')
    if action=='review':
        profile=next((p for p in check['review_options'] if p['profile_ref']==job['model_settings']['profile_ref']),None)
        if profile is None:raise ValueError('REVIEW_UNAVAILABLE')
        if 'index' in request and not 0<=request['index']<len(check['claims']):raise ValueError('CLAIM_NOT_FOUND')
        selected=[check['claims'][request['index']]['id']] if 'index' in request else [c['id'] for c in check['claims']]
        if not 1<=len(selected)<=16:raise ValueError('CLAIM_SELECTION_INVALID')
        payload=ws.answer_evidence._review_payload(check,selected);payload['question']=question;payload['language_context']=job.get('language_context')
        if len(packed(payload))>ws.settings()['context_chars']:raise ValueError('REVIEW_CONTEXT_TOO_LARGE')
        with ws.store.tx() as db:
            latest=_active_job(ws,identity,db)
            ws.store.put('job',identity,job['project'],dict(latest,kind='answer_review',answer_id=source['id'],expected_binding=check['binding'],
                profile=profile,claim_ids=selected,contract=CONTRACT,conversation_question=question),db=db)
        ws.answer_evidence._run_review(identity,payload)
        return True
    with ws.store.tx() as db:
        latest=_active_job(ws,identity,db)
        ws.store.put('job',identity,job['project'],dict(latest,status='RUNNING',kind='answer_'+action),db=db)
    if action=='clarify':
        prompts={'multiple':('这次包含多个操作。要先核对、补查，还是修订哪条结论？','This asks for several actions. Which should run first: review, evidence search, or a specific revision?','複数の操作があります。根拠の確認、追加検索、結論の修正のどれを先に行いますか？'),'target':('要处理上一回答的哪条结论？请指出序号或原句。','Which claim in the previous answer do you mean? Give its number or exact sentence.','前の回答のどの結論ですか？番号か原文を示してください。'),'facet':('要补查哪个方面：条件、方法、样本、结果，还是局限与反例？','Which aspect should I search: conditions, methods, samples, results, or limitations?','条件、方法、標本、結果、限界のどれを追加検索しますか？'),'many':('上一回答有超过 16 条待核对内容。这次先核对哪一条？','The previous answer has more than 16 claims. Which claim should I review first?','前の回答には16件を超える確認対象があります。最初にどれを確認しますか？')}
        text=say(lang,*prompts[request.get('reason','target')]);refs=[]
    elif action=='coverage':text,refs=coverage_reply(check,lang)
    elif action=='probe':
        facet=next((f for f in check['coverage']['facets'] if request['facet']==f['id'] or request['facet'] in f['label']),None)
        if facet is None:raise ValueError('FACET_NOT_FOUND')
        value=ws.answer_evidence.probe(thread['id'],source['id'],check['binding'],facet['id'],language_context=job.get('language_context'))
        refs=value['results'];text='\n\n'.join(f'[{n}] {r["title"]}\n{r["text"]}' for n,r in enumerate(refs,1)) or say(lang,'本次选定材料中未检索到对应片段。','No matching excerpt was retrieved from the selected materials.','今回選択した資料から該当断片は見つかりませんでした。')
    else:
        if not 0<=request['index']<len(check['claims']):raise ValueError('CLAIM_NOT_FOUND')
        claim=check['claims'][request['index']];replacement=request['replacement'];hypothesis=bool(re.fullmatch(r'(?:待验证假设|(?:a )?(?:testable )?hypothesis|仮説)[。.!]?',replacement,re.I))
        text=say(lang,'第 '+str(request['index']+1)+' 条：','Claim '+str(request['index']+1)+': ',str(request['index']+1)+' 番目：')+(say(lang,'待验证假设：','Hypothesis: ','検証待ち仮説：')+claim['text'] if hypothesis else replacement)
        ws.answer_evidence._revise(thread['id'],source['id'],check['binding'],claim['id'],claim['text'] if hypothesis else replacement,hypothesis,job_id=identity,
            completion={'question':question,'text':text,'refs':claim['evidence'],'started':started})
        return True
    with ws.store.tx() as db:
        latest=_active_job(ws,identity,db)
        thread=ws.store.get('thread',thread['id'],db=db)
        if not thread:raise ValueError('THREAD_NOT_FOUND')
        if thread.get('archived'):raise ValueError('THREAD_ARCHIVED')
        reply=append_reply(thread,question,text,refs,source=source,action=action,elapsed_ms=round((time.monotonic()-started)*1000),job=job)
        if action=='clarify':reply['e1_pending_request']=request.get('pending')
        ws.store.put('thread',thread['id'],thread['project'],thread,expected=thread['revision'],db=db)
        ws.store.put('job',identity,job['project'],dict(latest,status='COMPLETE',answer_id=reply['id'],usage={},elapsed_ms=reply['elapsed_ms']),db=db)
    return True


def _active_job(ws,identity,db):
    job=ws.store.get('job',identity,db=db)
    if ws.closed or not job or job.get('cancel_requested') or job['status'] not in {'QUEUED','RUNNING'}:
        raise ValueError('RESEARCH_CANCELLED')
    return job
