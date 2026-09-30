/* E1 results in the existing context disclosure. No new controls or model calls. */
(function(){
  'use strict';
  const statuses={SUPPORTED:'有支持',PARTIAL:'部分支持',UNSUPPORTED:'缺少支持',CONFLICT:'存在冲突',NEEDS_REVIEW:'需复核',NOT_REVIEWED:'未审核'};
  const layers={original:'原文',card:'Card',analysis:'Analysis',confirmed_knowledge:'已确认知识',unknown:'材料层未确认'};
  const translations={'原文':['Original','原文'],'已确认知识':['Confirmed knowledge','確認済み知識'],'材料层未确认':['Unknown material layer','資料の層が未確認'],'已加载片段':['Loaded excerpts','読み込み済み断片'],'本次上下文':['Answer context','今回のコンテキスト'],'有支持':['Supported','支持あり'],'部分支持':['Partial support','部分的支持'],'缺少支持':['Unsupported','支持なし'],'存在冲突':['Conflicting evidence','証拠と矛盾'],'需复核':['Needs review','要確認'],'未审核':['Not reviewed','未確認'],'线索已进入上下文，充分性未核实':['Context contains leads; sufficiency unassessed','コンテキスト内に手掛かりあり、十分性未確認'],'线索未进入上下文':['Leads omitted from context','手掛かりはコンテキスト外'],'加载范围内未定位线索':['No lead located in loaded excerpts','読み込み範囲で手掛かり未特定'],'来源已变化':['Source changed','資料が変更済み'],'本次问题':['Current question','今回の質問'],'局限与相反证据':['Limitations and contrary evidence','限界と反証'],'研究对象与条件':['Objects and conditions','対象と条件'],'方法与比较基线':['Methods and baselines','方法と比較基準'],'样本与独立性':['Samples and independence','標本と独立性'],'结果与不确定性':['Results and uncertainty','結果と不確実性']};
  function bind(details,message,{threadId,call,create,t}){
    if(message.e1_action)return;
    const translate=t;t=s=>{const lang=document.documentElement.lang||'zh';return translations[s]?.[lang.startsWith('en')?0:lang.startsWith('ja')?1:-1]||translate(s);};
    let sequence=0;
    details.addEventListener('toggle',async()=>{
      if(!details.open)return;
      const ticket=++sequence;
      try {
        const check=await call('memo.answer_check',{thread_id:threadId,answer_id:message.id});
        if(!details.isConnected||!details.open||ticket!==sequence)return;
        details.querySelectorAll('[data-memo-e1-result]').forEach(n=>n.remove());
        const add=(tag,text)=>{const node=create(tag,text);node.dataset.memoE1Result='';details.append(node);};
        for(const source of check.coverage.sources){
          const count=check.retained_evidence.filter(r=>r.artifact_id===source.id).length;
          add('p',source.title+' · '+t(layers[source.material_layer]||'材料层未确认')+' · '+t('已加载片段')+' '+(source.read_ranges||[]).length+' · '+t('本次上下文')+' '+count);
        }
        for(const facet of check.coverage.facets){
          const values=facet.sources.map(s=>s.title+'：'+t(({partial:'线索已进入上下文，充分性未核实',missing:'线索未进入上下文',not_assessed:'加载范围内未定位线索',covered:'已覆盖'})[s.status]||s.status));
          add('p',t(facet.label)+' · '+values.join('；'));
        }
        check.claims.forEach((claim,index)=>{
          add('p',(index+1)+'. '+claim.text+' · '+t(statuses[claim.status]));
          if(claim.status!=='NOT_REVIEWED'){
            add('p',claim.reason);
            if(claim.conditions)add('p',claim.conditions);
            for(const issue of claim.mechanical_issues||[])add('p',issue);
          }
        });
        if(check.freshness!=='CURRENT')add('p',t('来源已变化'));
        for(const old of check.history||[])add('pre','v'+old.version+'\n'+old.text);
      } catch(error){
        if(details.isConnected&&details.open&&ticket===sequence){
          details.querySelectorAll('[data-memo-e1-result]').forEach(n=>n.remove());
          const node=create('p',String(error.message||error));node.dataset.memoE1Result='';details.append(node);
        }
      }
    });
  }
  window.__MEMO_EVIDENCE_CHECK__=Object.freeze({bind});
})();
