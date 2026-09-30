/* Optional execution counts in the existing icon slot. No timers or model calls. */
(() => {
  'use strict';
  const NS='http://www.w3.org/2000/svg', saved=new WeakMap(), active=new Set();
  let connected=true, diagnostics=0;
  const phases={
    DOCUMENT_STRUCTURE:['STAGE','文献结构校验','Document structure verification','文書構造の確認'],
    OCR_RESCUE:['PAGE','OCR 页面救援','OCR page recovery','OCR ページ復旧'],
    VECTOR_GENERATION:['CHUNK','生成向量','Vector generation','ベクトル生成'],
    CARD_SEGMENTS:['SEGMENT','分段提炼','Segment distillation','分割抽出'],
    CARD_REVIEW:['ROUND','批次复核','Batch review','一括照合'],
    JUDGMENT_REVIEW:['CLAIM','代表结论核查','Selected claim review','代表結論の確認'],
    RAW_EXTRACTION:['LINE','提取原文','Source extraction','原文抽出'],
    RULE_EVALUATION:['RULE','处理核查规则','Rule processing','確認ルールの処理'],
    LOCAL_LOGIC:['SEGMENT','局部逻辑核查','Local logic review','局所論理の確認'],
    PDF_ASSIST:['PAGE','原文辅助核验','Source PDF assistance','原文 PDF の補助確認'],
    SOURCE_BRANCHES:['BRANCH','检索来源分支','Source branch search','検索元ブランチ'],
    CANDIDATE_RELATIONS:['ITEM','本地关系比较','Local relation comparison','ローカル関係の比較'],
    REPORT_ITEMS:['ITEM','汇总成果','Collecting outputs','成果の集計']
  };
  const units={STAGE:['步','stages','段階'],PAGE:['页','pages','ページ'],CHUNK:['块','chunks','チャンク'],SEGMENT:['段','segments','区間'],
    ROUND:['轮','rounds','回'],CLAIM:['项','claims','件'],LINE:['行','lines','行'],RULE:['项','rules','件'],
    BRANCH:['个分支','branches','ブランチ'],ITEM:['项','items','件']};
  const integer=n=>Number.isSafeInteger(n)&&n>=0&&n<=2147483647;
  const capabilities={'01_DOCUMENT_INGEST':['OCR_RESCUE','DOCUMENT_STRUCTURE'],'02_CHUNK_EMBEDDING':['VECTOR_GENERATION'],
    '03_CARD_DISTILL':['CARD_SEGMENTS'],'04_CARD_CROSS_CHECK':['CARD_REVIEW'],
    '08_JUDGMENT_CROSS_CHECK':['JUDGMENT_REVIEW'],'E2_DATA_REVIEW':['RAW_EXTRACTION','RULE_EVALUATION','PDF_ASSIST'],
    'E2_LOGIC_REVIEW':['LOCAL_LOGIC'],'COLLECTING':['SOURCE_BRANCHES','REPORT_ITEMS'],'RESOLVING_IDENTITIES':['CANDIDATE_RELATIONS']};
  const processed=new Set(['RAW_EXTRACTION','RULE_EVALUATION','SOURCE_BRANCHES','CANDIDATE_RELATIONS','REPORT_ITEMS']);
  const id=s=>typeof s==='string'&&/^[A-Za-z0-9._:-]{1,192}$/.test(s);
  const required=['schema_version','job_id','attempt_id','node_id','node_execution_id','scope_id','scope_kind','phase','unit','basis','completed_units','total_units','plan_hash','progress_revision','scope_state'];
  function valid(p,b,n){
    if(!p||typeof p!=='object'||Array.isArray(p))return false;
    return required.every(k=>Object.hasOwn(p,k))&&Object.keys(p).every(k=>required.includes(k)||k==='limited_units')&&
      p.schema_version==='NodeProgress-v1'&&p.scope_kind==='SUBPHASE'&&p.scope_state==='ACTIVE'&&
      ['job_id','attempt_id','node_id','node_execution_id','scope_id'].every(k=>id(p[k]))&&
      p.job_id===b.job_id&&p.attempt_id===b.attempt_id&&p.node_id===n.node_id&&
      phases[p.phase]?.[0]===p.unit&&capabilities[n.node_id]?.includes(p.phase)&&p.basis===(processed.has(p.phase)?'PROCESSED_ITEMS':'VALIDATED_OUTPUTS')&&
      integer(p.completed_units)&&integer(p.total_units)&&p.total_units>0&&p.completed_units<=p.total_units&&
      integer(p.progress_revision)&&p.progress_revision>0&&integer(p.limited_units??0)&&(p.limited_units??0)<=p.completed_units&&
      typeof p.plan_hash==='string'&&/^[a-f0-9]{64}$/.test(p.plan_hash);
  }
  function clear(node){
    const old=saved.get(node);
    if(!old)return;
    if(old.svg.isConnected)old.svg.replaceWith(old.icon);
    for(const [key,value] of Object.entries(old.attributes)){
      if(node.getAttribute(key)===old.applied[key]){
        if(value==null)node.removeAttribute(key);else node.setAttribute(key,value);
      }
    }
    delete node.dataset.nodeProgressHint;active.delete(node);saved.delete(node);
  }
  function apply(node,definition,binding){
    if(!node)return;
    const p=definition?.node_progress;
    const eligible=connected&&definition?.state==='RUNNING'&&['RUNNING','PAUSE_REQUESTED'].includes(binding?.control_state)&&
      !binding?.historical_read_only&&!binding?.stale&&(!binding?.synthetic_only||binding.workflow_kind==='CONSOLE_SIMULATION');
    if(!eligible||!valid(p,binding||{},definition||{})){
      clear(node);
      if(p&&eligible&&diagnostics++<5)console.warn('NODE_PROGRESS_IGNORED');
      return;
    }
    if(p.completed_units===0){clear(node);return;}
    const logic=node.classList.contains('e2-logic-node');
    const slot=node.querySelector(logic?'.desktop-tasks-node-index':'.desktop-tasks-node-status');
    if(!slot)return;
    const previous=saved.get(node);
    const identity=[p.job_id,p.attempt_id,p.node_id,p.node_execution_id,p.scope_id,p.plan_hash].join('|');
    if(previous?.identity===identity&&(p.progress_revision<previous.revision||p.completed_units<previous.done))return;
    clear(node);
    const icon=logic?slot.firstElementChild:slot.querySelector('.status-dot,.desktop-tasks-neutral-dot,.desktop-tasks-disabled-mark');
    if(!icon)return;
    const language=document.documentElement.lang||'zh-CN',l=language.startsWith('en')?1:language.startsWith('ja')?2:0;
    const basis=p.basis==='PROCESSED_ITEMS'?['已处理','Processed','処理済み'][l]:['已完成','Completed','完了'][l];
    let hint=phases[p.phase][l+1]+' · '+basis+' '+p.completed_units+' / '+p.total_units+' '+units[p.unit][l];
    if(p.limited_units)hint+=['；其中 '+p.limited_units+' 项信息不足或不适用','; '+p.limited_units+' insufficient or not applicable','（'+p.limited_units+' 件は情報不足または適用外）'][l];
    if(binding.synthetic_only)hint=['控制台模拟 · ','Console simulation · ','コンソール模擬 · '][l]+hint;
    const svg=(previous?.identity===identity?previous.svg:null)||document.createElementNS(NS,'svg');
    svg.setAttribute('viewBox','0 0 18 18');svg.setAttribute('width','18');svg.setAttribute('height','18');
    svg.setAttribute('class','memo-node-progress');svg.setAttribute('aria-hidden','true');svg.setAttribute('focusable','false');
    if(!svg.firstChild){
      for(const type of ['track','arc']){
        const c=document.createElementNS(NS,'circle');c.setAttribute('cx','9');c.setAttribute('cy','9');c.setAttribute('r','7');
        c.setAttribute('fill','none');c.setAttribute('stroke-width','2');c.setAttribute('pathLength','100');c.setAttribute('class','memo-node-progress-'+type);
        if(type==='arc'){c.setAttribute('stroke-dasharray','100');c.setAttribute('stroke-linecap','round');c.setAttribute('transform','rotate(-90 9 9)');}
        svg.append(c);
      }
    }
    svg.querySelector('.memo-node-progress-arc').setAttribute('stroke-dashoffset',String(100*(1-p.completed_units/p.total_units)));
    const attributes={},applied={};
    for(const key of ['title','data-desktop-dynamic-title','aria-label']){
      attributes[key]=node.getAttribute(key);
      applied[key]=(attributes[key]?attributes[key]+' · ':'')+hint;
      node.setAttribute(key,applied[key]);
    }
    icon.replaceWith(svg);node.dataset.nodeProgressHint=hint;
    saved.set(node,{identity,revision:p.progress_revision,done:p.completed_units,svg,icon,attributes,applied});active.add(node);
  }
  function transport(value){connected=value;if(!value)for(const node of [...active])clear(node);}
  window.__MEMO_NODE_PROGRESS__={apply,clear,transport};
})();
