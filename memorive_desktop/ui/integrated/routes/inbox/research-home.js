/* EVO E7: true-home actions and one shared, explicit AI briefing consumer. */
(()=>{
 'use strict';
 if(Memorive.researchHome)return;
 const api=(method,params={})=>Memorive.service.call(method,params);
 const copy={"想构建什么呢？":["What would you like to explore?","今日は何を調べましょう？"],"上次的研究，继续吗？":["Ready to pick up where you left off?","前回の研究を続けますか？"],"看看研究方向的最新进展？":["What's new in your research area?","研究テーマの新しい動きを見てみますか？"],"找几篇高度相关的文献？":["Find a few papers close to your topic?","テーマに合う論文を探してみますか？"],"收件箱有什么待处理？":["Anything waiting in your inbox?","受信箱を確認してみますか？"],"看看最近整理的资料？":["Revisit your recent materials?","最近整理した資料を見てみますか？"],"看看最近的 AI 进展？":["Catch up on what's new in AI?","最近の AI の動きを見てみますか？"],"接着研究":["Pick up where I left off","続きを進める"],"继续上次的":["Resume my research","前回の続きへ"],"好，继续看看":["Let's keep going","もう少し見てみる"],"我看看":["Take a look","見てみる"],"看看新进展":["See what's new","新しい動きを見る"],"找几篇看看":["Find a few papers","いくつか探してみる"],"帮我找找":["Help me find some","探してもらう"],"看看相关的":["Explore related papers","関連する論文を見る"],"去处理一下":["Work through my inbox","確認して整理する"],"打开看看":["Open it up","開いてみる"],"翻翻资料":["Browse my materials","資料を見返す"],"看看最近进展":["Catch up on AI","最近の動きを知る"],"了解一下":["Get up to speed","詳しく見てみる"],"换一个问题":["Try another idea","別の話題へ"],"开始提问":["Ask a question","質問する"],"AI 近况":["AI updates","AI の最新動向"],"查看进度":["View progress","進捗を見る"],"查看结果":["View results","結果を見る"],"恢复结果投递":["Restore delivery","結果の反映を再開"],"研究方向":["Research direction","研究テーマ"],"确定":["Continue","決定"],"检索已开始":["Search started","検索を開始しました"],"目标已变化，已打开对应工作区":["That item has changed. Its workspace is open.","対象が変更されたため、対応する画面を開きました"],"返回":["Back","戻る"],"来源与模型":["Sources and model","情報源とモデル"],"尚未配置":["Not configured","未設定"],"尚未取得":["Not retrieved yet","未取得"],"生成":["Generate","生成"],"更新":["Update","更新"],"查看任务":["View task","タスクを見る"],"取消":["Cancel","キャンセル"],"取消生成":["Cancel generation","生成を中止"],"正在取消":["Cancelling…","中止しています…"],"重试未完成步骤":["Retry unfinished steps","未完了の処理を再試行"],"在资料库查看":["View in library","ライブラリで見る"],"打开 AI 近况":["Open AI updates","AI の最新動向を開く"],"尚未生成 AI 近况。":["No AI update has been generated yet.","AI の最新動向はまだ生成されていません。"],"正在读取…":["Loading…","読み込み中…"],"AI 近况模型":["AI updates model","AI の最新動向用モデル"],"调整 AI 近况模型":["Change the AI updates model","AI の最新動向用モデルを変更"],"调整":["Change","変更"],"继承问答模型":["Use the chat model","対話モデルを使用"],"AI 近况范围":["AI updates scope","AI の最新動向の対象範囲"],"日期范围":["Date range","対象期間"],"过去 7 天":["Past 7 days","過去 7 日間"],"过去 30 天":["Past 30 days","過去 30 日間"],"自定义日期":["Custom dates","期間を指定"],"开始日期":["Start date","開始日"],"结束日期":["End date","終了日"],"关注点（可选）":["Focus (optional)","注目する点（任意）"],"AI 近况来源":["AI updates sources","AI の最新動向の情報源"],"官方工具发布记录":["Official tool release notes","公式ツールのリリース情報"],"官方公告与研究进展":["Official announcements and research","公式発表と研究の進展"],"OpenAI 公告":["OpenAI announcements","OpenAI の公式発表"],"Google DeepMind 公告":["Google DeepMind announcements","Google DeepMind の公式発表"],"Claude Code 发布记录":["Claude Code releases","Claude Code のリリース情報"],"来源摘要":["Source summary","情報源の要約"],"发布记录":["Release notes","リリース情報"],"发布记录节选":["Release notes excerpt","リリース情報の抜粋"],"页面正文":["Article text","記事本文"],"正文节选":["Article excerpt","本文の抜粋"],"正文范围有限":["Limited source text","取得できた本文は一部のみ"],"部分来源读取失败，本次仅依据已取得的材料。":["Some sources could not be retrieved. This update uses only the available material.","取得できなかった情報源があります。今回は取得済みの資料のみを使用しています。"],"等待中":["Queued","待機中"],"进行中":["In progress","処理中"],"已完成":["Completed","完了"],"部分完成":["Partially completed","一部完了"],"未完成":["Failed","未完了"],"已取消":["Cancelled","中止済み"],"已中断":["Interrupted","中断"],"请查看任务记录":["See the task record","タスク記録を確認してください"],"请在研究问答与连接的模型设置中选择可用模型":["Choose an available model in Research & connections → Models & chat.","研究・接続設定の「モデルと対話」で利用可能なモデルを選択してください"],"请在外部数据来源中选择 AI 近况来源":["Choose AI update sources in External data sources.","「外部データソース」で AI の最新動向の情報源を選択してください"],"请先选择可用的问答或 AI 近况模型":["Choose an available chat or AI updates model first.","利用可能な対話モデルまたは AI の最新動向用モデルを選択してください"],"所选模型当前不可用":["The selected model is unavailable.","選択したモデルは現在利用できません"],"请先在外部数据来源中选择资讯来源":["Choose news sources in External data sources first.","「外部データソース」で情報源を選択してください"],"请选择有效的起止日期，结束日期不能晚于今天":["Choose a valid date range ending no later than today.","終了日が今日以前となる有効な期間を選択してください"],"资讯来源读取失败，请检查网络后重试":["Sources could not be retrieved. Check your connection and try again.","情報源を取得できませんでした。接続を確認して再試行してください"],"所选范围没有足够的带日期材料；未调用模型":["No usable dated material was found in this range. The model was not called.","対象期間の有効な日付付き資料がありません。モデルは呼び出していません"],"引用未通过核对，本次内容未发布":["Citation checks failed. This update was not published.","出典の確認に失敗したため、内容は保存されていません"],"来源链接未通过核对，本次内容未发布":["Source link checks failed. This update was not published.","出典リンクの確認に失敗したため、内容は保存されていません"],"模型执行未完成，原有报告仍可阅读":["Generation failed. Your previous report is still available.","生成を完了できませんでした。前のレポートは引き続き読めます"],"已有研究任务正在运行":["A research task is already running.","研究タスクがすでに実行中です"],"设置已变化，请重新载入":["Settings have changed. Reload them to continue.","設定が変更されました。再読み込みしてください"],"研究方向已变化，请重新选择":["The research direction has changed. Select it again.","研究テーマが変更されました。選び直してください"],"请先设置文献来源":["Set up literature sources first.","先に文献の情報源を設定してください"],"操作未完成，请查看任务记录后重试":["The action could not be completed. Check the task record and retry.","操作を完了できませんでした。タスク記録を確認して再試行してください"]};
 const t=text=>{
   const lang=document.documentElement.lang||'zh-CN',i=lang.startsWith('en')?0:lang.startsWith('ja')?1:-1;
   if(i<0)return text;
   if(copy[text])return copy[text][i];
   let m;
  if((m=text.match(/^(\d+) 项待处理$/)))return i===0?`${m[1]} items to review`:`未処理 ${m[1]} 件`;
  if((m=text.match(/^模型：(.*)$/)))return (i===0?'Model: ':'モデル：')+t(m[1]);
  if((m=text.match(/^发布 (.*)$/)))return (i===0?'Published ':'公開 ')+t(m[1]);
  if((m=text.match(/^取得 (.*)$/)))return (i===0?'Retrieved ':'取得 ')+m[1];
  if(text.includes(' · '))return text.split(' · ').map(t).join(' · ');
  return Memorive.app?.translate?.(text)||text;
 };
 const localized=new Map(),localizedAttrs=new Map();
 const paint=(n,source)=>{
   n.setAttribute('data-desktop-no-translate','');
   localized.set(n,source);
   let text=[...n.childNodes].find(c=>c.nodeType===Node.TEXT_NODE);
   if(!text){
     text=document.createTextNode('');
     n.prepend(text);
   }
   text.nodeValue=t(source);
 };
 const attr=(n,key,source)=>{
   let values=localizedAttrs.get(n);
   if(!values){
     values={};
     localizedAttrs.set(n,values);
   }
   values[key]=source;
   n.setAttribute(key,t(source));
 };
 const el=(tag,text,cls)=>{
   const n=document.createElement(tag);
   if(text!=null)paint(n,text);
   if(cls)n.className=cls;
   return n;
 };
 const actionIcons={'继续研究':'message','查找新文献':'search','查找相关文献':'search','查看收件箱':'mail-check','查看资料库':'archive','AI 近况':'ai-updates','查看进度':'current-task','查看结果':'archive','恢复结果投递':'restore','生成':'auto','更新':'refresh','查看任务':'current-task','取消':'reset','取消生成':'reset','重试未完成步骤':'refresh','在资料库查看':'archive','打开 AI 近况':'ai-updates','确定':'mail-check'};
 const setIcon=(n,label,icon)=>{
   paint(n,label);
   attr(n,'aria-label',label);
   attr(n,'title',label);
   for(const c of [...n.classList])if(c.startsWith('desktop-action-'))n.classList.remove(c);
   n.classList.add('desktop-toolbar-icon','desktop-action-'+icon);
 };
 const btn=(text,fn,cls='soft-button')=>{
   const n=el('button',text,cls);
   n.type='button';
   n.onclick=fn;
   if(actionIcons[text])setIcon(n,text,actionIcons[text]);
   return n;
 };
 const app=()=>Memorive.app,route=()=>app()?.inspect().active_route;
 const active=s=>['QUEUED','RUNNING'].includes(s?.status);
 const labels={QUEUED:'等待中',RUNNING:'进行中',SUCCEEDED:'已完成',PARTIAL:'部分完成',FAILED:'未完成',CANCELLED:'已取消',INTERRUPTED:'已中断'};
 const errors={AI_MODEL_REQUIRED:'请先选择可用的问答或 AI 近况模型',AI_MODEL_UNAVAILABLE:'所选模型当前不可用',AI_SOURCE_REQUIRED:'请先在外部数据来源中选择资讯来源',AI_RANGE_INVALID:'请选择有效的起止日期，结束日期不能晚于今天',AI_ALL_SOURCES_FAILED:'资讯来源读取失败，请检查网络后重试',AI_NO_DATED_MATERIALS:'所选范围没有足够的带日期材料；未调用模型',AI_REPORT_CITATION_INVALID:'引用未通过核对，本次内容未发布',AI_REPORT_MODEL_URL_FORBIDDEN:'来源链接未通过核对，本次内容未发布',CHAT_MODEL_EXECUTION_FAILED:'模型执行未完成，原有报告仍可阅读',RESEARCH_ALREADY_RUNNING:'已有研究任务正在运行',AI_SETTINGS_REVISION_CONFLICT:'设置已变化，请重新载入',RESEARCH_DIRECTION_NOT_FOUND:'研究方向已变化，请重新选择',RESEARCH_SOURCE_REQUIRED:'请先设置文献来源'};
 const message=e=>errors[e?.message]||errors[e?.error_code]||'操作未完成，请查看任务记录后重试';
 const prompts=[
  {id:'continue',question:'上次的研究，继续吗？',actions:['接着研究','继续上次的','好，继续看看'],weight:30,available:s=>!!s.thread},
  {id:'latest',question:'看看研究方向的最新进展？',actions:['我看看','看看新进展','找几篇看看'],weight:15,available:s=>s.literature_available},
  {id:'related',question:'找几篇高度相关的文献？',actions:['找几篇看看','帮我找找','看看相关的'],weight:10,available:s=>s.literature_available},
  {id:'inbox',question:'收件箱有什么待处理？',actions:['我看看','去处理一下','打开看看'],weight:20,available:s=>s.inbox_count>0},
  {id:'library',question:'看看最近整理的资料？',actions:['我看看','翻翻资料','打开看看'],weight:15,available:s=>!!s.material},
  {id:'ask',question:'想构建什么呢？',actions:['开始提问'],weight:10,available:s=>!s.thread&&!s.material&&!s.inbox_count&&!s.literature_available},
  {id:'ai',question:'看看最近的 AI 进展？',actions:['我看看','看看最近进展','了解一下'],weight:10,available:()=>true}
 ];
 function choose(summary,previous,random=Math.random){
   let candidates=prompts.filter(p=>p.available(summary));
   if(candidates.length>1)candidates=candidates.filter(p=>p.id!==previous);
   if(!candidates.length)return null;
   let v=random()*candidates.reduce((n,p)=>n+p.weight,0);
   return candidates.find(p=>(v-=p.weight)<0)||candidates.at(-1);
 }
 const home=document.querySelector('#desktop-inbox-preview #desktop-blank-state');
 let question=null,
   bubble=null,
   action=null,
   context=null,
   homeStatus=null,
   homeCopy=null,
   homeEpoch=0,
   homeTimer=null,
   homeBusy=false,
   homeLastClick=-Infinity;
 const discovery=new Map();
 function homeRender(){
  if(!bubble)return;
  paint(bubble,question?.prompt.question||'想构建什么呢？');
  action.style.visibility=question?'visible':'hidden';
  action.tabIndex=question?0:-1;
  action.disabled=homeBusy;
  paint(context,question?.context||'');
  context.hidden=!question?.context;
  const task=question&&discovery.get(question.key);
  const label=active(task)?'查看进度':task?.result_available?'查看结果':task?.delivery_recoverable?'恢复结果投递':question?.actionLabel||'开始提问';
  paint(action,label);
  attr(action,'aria-label',label);
 }
 async function cycle(){
  if(homeBusy||route()!=='home')return;
  const epoch=++homeEpoch;
  homeBusy=true;
  bubble.disabled=true;
  homeCopy.classList.add('memo-research-changing');
  const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  try{
    const [s]=await Promise.all([api('home.summary'),new Promise(resolve=>setTimeout(resolve,reduced?0:140))]);
    if(epoch!==homeEpoch||route()!=='home')return;
   const p=choose(s,question?.prompt.id);
    if(!p){
      question=null;
      return;
    }
   const target=p.id==='continue'?s.thread:p.id==='library'?s.material:null;
   question={prompt:p,actionLabel:p.actions[Math.floor(Math.random()*p.actions.length)],summary:s,target,context:p.id==='continue'?'':target?.display_name||(p.id==='inbox'?s.inbox_count+' 项待处理':''),key:p.id};
   if(['latest','related'].includes(p.id)){
     question.direction=s.latest_direction||s.directions.at(-1);
     if(question.direction){
       question.context=question.direction.name;
       question.key=p.id+':'+(question.direction.id||'default');
     }
   }
   paint(homeStatus,'');
  }catch(e){paint(homeStatus,message(e));}finally{
    homeBusy=false;
    bubble.disabled=false;
    homeRender();
    requestAnimationFrame(()=>homeCopy.classList.remove('memo-research-changing'));
    pollHome();
  }
 }
 async function pollHome(){
  clearTimeout(homeTimer);
  if(route()!=='home')return;
  const selected=question,task=selected&&discovery.get(selected.key);
  if(!active(task))return;
  try{
    const state=await api('research.get',{run_id:task.run_id});
    if(question!==selected||route()!=='home')return;
   discovery.set(selected.key,{...state,result_available:!!state.library_locator});
    homeRender();
  }catch(e){paint(homeStatus,message(e));}
  if(route()==='home'&&active(discovery.get(question?.key)))homeTimer=setTimeout(pollHome,1400);
 }
 async function act(){
  const selected=question;
  if(!selected||homeBusy)return;
  const kind=selected.prompt.id;
  if(kind==='ask'){
    app().activate('chat');
    document.getElementById('memo-question')?.focus();
    return;
  }
  if(kind==='ai'){
    app().activate('leaderboard');
    await openAI(document.getElementById('memo-ai-leaderboard'));
    return;
  }
  homeBusy=true;
  homeRender();
  try{
   if(['latest','related'].includes(kind)){
    // Resolve again at click time so edits made after showing the prompt are honored.
    const summary=await api('home.summary'),direction=summary.latest_direction||summary.directions.at(-1);
    if(!summary.literature_available||!direction)throw Error('RESEARCH_DIRECTION_NOT_FOUND');
    selected.direction=direction;
    selected.context=direction.name;
    selected.key=kind+':'+(direction.id||'default');
    const previous=discovery.get(selected.key);
    if(previous){
      const fresh=await api('research.get',{run_id:previous.run_id});
      discovery.set(selected.key,{...fresh,result_available:!!fresh.library_locator});
     if(active(fresh)){
       app().activate('current-task');
       await Memorive.research.openTask(fresh.run_id);
       return;
     }
     if(fresh.library_locator){
       app().activate('library');
       await Memorive.research.openResult(fresh.run_id);
       return;
     }
     if(fresh.delivery_recoverable){
       await api('research.repair_delivery',{run_id:fresh.run_id});
       app().activate('library');
       await Memorive.research.openResult(fresh.run_id);
       return;
     }
    }
    const receipt=await api('research.discover',{request_id:'home-'+crypto.randomUUID(),objective:kind,...(selected.direction.id?{direction_id:selected.direction.id}:{}),count:3,days:90});
    discovery.set(selected.key,receipt);
    paint(homeStatus,'检索已开始');
    app().activate('current-task');
    await Memorive.research.openTask(receipt.run_id);
    return;
   }
   const r=await api('home.resolve',{kind:kind==='continue'?'thread':kind==='library'?'material':'inbox',target:selected.target?.id||selected.target?.artifact_id||null});
   app().activate(r.route);
   if(!r.available){
     window.dispatchEvent(new CustomEvent('desktop:toast',{detail:{message:t('目标已变化，已打开对应工作区')}}));
     return;
   }
   if(kind==='continue')await Memorive.chat.openThread(r.thread_id);
   if(kind==='library')await Memorive.libraryBridge.openArtifactByLocator('memorive://artifact/'+r.artifact_id);
  }catch(e){paint(homeStatus,message(e));}finally{
    homeBusy=false;
    homeRender();
  }
 }
 function initHome(){
  if(!home)return;
  const group=home.querySelector(':scope > .desktop-home-prompt'),old=group.querySelector('.desktop-home-speech');
  const stack=el('div',null,'memo-research-home-copy');
  bubble=btn('想构建什么呢？',null,'desktop-home-speech memo-research-bubble');
  attr(bubble,'aria-label','换一个问题');
  bubble.setAttribute('aria-live','polite');
  action=btn('',act,'soft-button memo-research-home-action');
  context=el('small',null,'memo-research-home-context');
  homeStatus=el('p',null,'memo-research-home-status');
  homeStatus.setAttribute('role','status');
  homeCopy=stack;
  stack.append(bubble,context);
  old.replaceWith(stack);
  const controls=el('div',null,'memo-research-home-controls');
  controls.append(action,homeStatus);
  home.append(controls);
  group.classList.add('memo-research-cycle-region');
  // Capture applies the same interval to the inherited character animation and
  // prompt changes, while keyboard-generated clicks retain normal behavior.
  group.addEventListener('click',event=>{
    const now=performance.now();
    if(event.button!==0||homeBusy||now-homeLastClick<700){
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    homeLastClick=now;
  },true);
  group.addEventListener('click',()=>cycle());
  homeRender();
 }
 let pane=null,paneOrigin=null,paneTimer=null,paneEpoch=0,paneState=null,paneRun=null;
 function closeAI(restore=true){
  clearTimeout(paneTimer);
  paneEpoch++;
  const previous=pane;
  pane=null;
  previous?.remove();
  Memorive.leaderboard?.closeAI();
  if(restore&&paneOrigin?.isConnected)paneOrigin.focus();
 }
 function openShell(origin){
  Memorive.research?.close();
  closeAI(false);
  paneOrigin=origin||document.activeElement;
  pane=el('section',null,'memo-research-ai-pane');
  attr(pane,'aria-label','AI 近况');
  const head=el('header',null,'desktop-research-head'),back=btn('‹',()=>closeAI(),'icon-button memo-research-back');
  attr(back,'aria-label','返回');
  attr(back,'title','返回');
  head.append(back,el('h3','AI 近况'));
  const body=el('div',null,'desktop-research-body');
  pane.append(head,body);
  Memorive.leaderboard.openAI(pane,()=>closeAI(false));
  back.focus();
  pane.addEventListener('keydown',e=>{if(e.key==='Escape'){
    e.stopPropagation();
    closeAI();
  }});
  return body;
 }
 function showSources(body,result,run){
  function statement(value){
    if(!value)return;
    const p=el('p',value.text,'memo-research-mainline');
    for(const id of value.source_ids){
      const source=result.sources.find(s=>s.source_id===id);
      if(!source)continue;
      const a=el('a',' '+t(source.source_name));
      a.href=source.url;
      a.onclick=async e=>{
        e.preventDefault();
        await api('ai.open_source',{run_id:run,source_id:id,url:source.url});
      };
      p.append(a);
    }
    body.append(p);
  }
  statement(result.mainline);
  for(const [index,item] of (result.items||[]).entries()){
    const row=el('article',null,'desktop-research-paper');
    row.append(el('h4',(index+1)+'. '+item.title),el('p',item.what),el('p',item.why));
   for(const id of item.source_ids){
     const source=result.sources.find(s=>s.source_id===id);
     if(!source)continue;
    const a=el('a',t(source.source_name)+' · '+source.title);
     a.href=source.url;
     a.rel='noopener noreferrer';
     a.onclick=async e=>{
       e.preventDefault();
       try{await api('ai.open_source',{run_id:run,source_id:id,url:source.url});}catch(err){a.title=t(message(err));}
     };
    row.append(a,el('small','发布 '+source.published_at.slice(0,10)+' · 取得 '+source.retrieved_at.slice(0,10)+' · '+({feed_summary:'来源摘要',release_notes:'发布记录',release_notes_excerpt:'发布记录节选',article_main_text:'页面正文',article_excerpt:'正文节选'}[source.body_extent]||'正文范围有限'),'memo-research-source-time'));
   }
    body.append(row);
  }
  statement(result.trend);
  if(result.source_failures?.length)body.append(el('p','部分来源读取失败，本次仅依据已取得的材料。','desktop-research-muted'));
 }
 function renderAI(body){
  if(!paneState||!body.isConnected)return;
  body.replaceChildren();
  const state=paneState;
  const controls=el('div',null,'memo-research-ai-controls'),rangeWindow=state.effective_window||state.default_window;
  const range=el('p',rangeWindow.start+' — '+rangeWindow.end,'desktop-research-muted');
  const run=paneRun||state.active_run||state.runs[0],
    running=active(run),
    actions=el('div',null,'desktop-research-actions');
  const generate=btn(state.latest?'更新':'生成',async()=>{
    generate.disabled=true;
    try{
      paneRun=await api('ai.generate',{request_id:'ai-ui-'+crypto.randomUUID(),refresh:!!state.latest});
      await refreshAI(body);
    }catch(e){paint(status,message(e));}finally{generate.disabled=running;}
  } ,'primary-button');
  generate.disabled=running||!state.effective_model||!state.config.sources.length;
  actions.append(generate);
  const unavailable=!state.effective_model?'请在研究问答与连接的模型设置中选择可用模型':!state.config.sources.length?'请在外部数据来源中选择 AI 近况来源':'';
  const status=el('p',run?(labels[run.status]||run.status)+(run.error_code?' · '+(errors[run.error_code]||'请查看任务记录'):''):unavailable,'desktop-research-status');
  status.setAttribute('role','status');
  status.hidden=!status.textContent;
  if(run){
    actions.append(btn('查看任务',()=>{
      closeAI(false);
      return Memorive.research.openTask(run.run_id);
    }));
   if(running)actions.append(btn('取消',async()=>{
     await api('ai.cancel',{run_id:run.run_id});
     status.hidden=false;
     paint(status,'正在取消');
   }));
   else if(['FAILED','INTERRUPTED','PARTIAL'].includes(run.status))actions.append(btn('重试未完成步骤',async e=>{
     e.currentTarget.disabled=true;
     try{
       paneRun=await api('ai.retry',{run_id:run.run_id,request_id:'ai-retry-'+crypto.randomUUID()});
       await refreshAI(body);
     }catch(err){
       status.hidden=false;
       paint(status,message(err));
       e.currentTarget.disabled=false;
     }
   }));
  }
  controls.append(range,actions,status);
  body.append(controls);
  const latest=run?.result?run:state.latest;
  if(latest?.result){
    body.append(el('h4',latest.title));
    showSources(body,latest.result,latest.reused_run_id||latest.run_id);
    body.append(btn('在资料库查看',()=>{
      closeAI(false);
      return Memorive.research.openResult(latest.reused_run_id||latest.run_id);
    }));
  }
 }
 async function refreshAI(body){
  clearTimeout(paneTimer);
  const epoch=paneEpoch;
  const fresh=await api('ai.state');
  if(epoch!==paneEpoch||!body.isConnected)return;
  paneState=fresh;
  if(paneRun)paneRun=await api('ai.get',{run_id:paneRun.run_id});
  if(epoch!==paneEpoch||!body.isConnected)return;
  renderAI(body);
  if(active(paneRun||fresh.active_run))paneTimer=setTimeout(()=>refreshAI(body).catch(()=>{}),1500);
 }
 async function openAI(origin){
   if(route()!=='leaderboard'){
     app().activate('leaderboard');
     origin=document.getElementById('memo-ai-leaderboard');
   }
   const body=openShell(origin);
   body.append(el('p','正在读取…'));
   try{await refreshAI(body);}catch(e){if(body.isConnected)body.replaceChildren(el('p',message(e)));}
 }
 let saved=null,draft=null,settingsLoading=null,picker=null;
 const dirty=()=>!!saved&&JSON.stringify(saved.config)!==JSON.stringify(draft);
 const sync=()=>Memorive.settingsBridge?.syncSaveState();
 function renderSettings(){
  if(!saved)return;
  const options=[{profileRef:'',model:t('继承问答模型'),state:saved.effective_model?'green':'yellow',note:saved.effective_model?.display_name||''},...saved.model_options.map(o=>({profileRef:o.profile_ref,model:o.display_name||o.model_name,state:o.eligible?'green':'yellow',disabled:!o.eligible,note:o.profile_kind}))];
  picker?.render(options,draft.profile_ref||'');
  const mode=document.getElementById('memo-ai-range-mode');
  if(mode){
    mode.value=draft.range_mode;
    const custom=draft.range_mode==='custom';
    document.getElementById('memo-ai-date-fields').hidden=!custom;
    for(const key of ['start','end']){
      const input=document.getElementById('memo-ai-range-'+key);
      input.value=draft['range_'+key]||saved.default_window[key];
      input.max=saved.default_window.end;
    }
    document.getElementById('memo-ai-focus').value=draft.focus||'';
  }
  const list=document.getElementById('memo-ai-sources');
  list.replaceChildren();
  for(const source of saved.sources){
    const row=el('label',null,'switch-row'),copy=el('span',null,'switch-copy'),check=el('input',null,'switch');
    check.type='checkbox';
    check.setAttribute('role','switch');
    check.checked=draft.sources.includes(source.id);
    copy.append(el('strong',source.label),el('span',source.category==='tool'?'官方工具发布记录':'官方公告与研究进展'));
    check.onchange=()=>{
      draft.sources=check.checked?[...draft.sources,source.id]:draft.sources.filter(id=>id!==source.id);
      sync();
    };
    row.append(copy,check);
    list.append(row);
  }
 }
 async function loadSettings(force=false){
   if(settingsLoading)return settingsLoading;
   if(dirty()&&!force)return;
   settingsLoading=(async()=>{
     try{
       saved=await api('ai.config_get');
       draft=structuredClone(saved.config);
       renderSettings();
       sync();
     }finally{settingsLoading=null;}
   })();
   return settingsLoading;
 }
 function initSettings(){
  const host=document.querySelector('#memo-settings-models .desktop-settings-form-stack');
  if(!host)return;
  const box=el('div',null,'desktop-settings-model-setting'),
    heading=el('span','AI 近况模型','desktop-settings-model-heading');
  heading.id='memo-ai-model-heading';
  heading.tabIndex=-1;
  const choice=el('div',null,'desktop-settings-model-choice'),
    dot=el('span',null,'status-dot yellow'),
    name=el('strong','—'),
    adjust=btn('调整',()=>{},'soft-button'),
    menu=el('div',null,'desktop-settings-model-menu');
  menu.id='memo-ai-model-menu';
  menu.setAttribute('role','menu');
  menu.dataset.open='false';
  attr(adjust,'aria-label','调整 AI 近况模型');
  adjust.setAttribute('aria-haspopup','menu');
  adjust.setAttribute('aria-controls',menu.id);
  choice.append(dot,name,adjust);
  box.append(heading,choice,menu);
  host.children[0].after(box);
  picker=Memorive.modelPicker.bind({button:adjust,menu,label:name,dot,onSelect:o=>{
    draft.profile_ref=o.profileRef||null;
    renderSettings();
    sync();
  }});
  const detail=el('details',null,'desktop-settings-local-model-advanced memo-settings-details'),
    summary=el('summary','AI 近况范围','desktop-settings-disclosure'),
    settingsBody=el('div',null,'memo-settings-disclosure-body');
  detail.id='memo-ai-range-settings';
  const modeLabel=el('label','日期范围','field-label'),mode=el('select',null,'select');
  mode.id='memo-ai-range-mode';
  for(const [value,name] of [['7','过去 7 天'],['30','过去 30 天'],['custom','自定义日期']]){
    const option=el('option',name);
    option.value=value;
    mode.append(option);
  }
  modeLabel.append(mode);
  const dateFields=el('div',null,'memo-research-ai-dates');
  dateFields.id='memo-ai-date-fields';
  for(const [key,name] of [['start','开始日期'],['end','结束日期']]){
    const label=el('label',name,'field-label'),input=el('input',null,'field');
    input.type='date';
    input.id='memo-ai-range-'+key;
    input.onchange=()=>{
      if(!draft)return;
      draft['range_'+key]=input.value;
      sync();
    };
    label.append(input);
    dateFields.append(label);
  }
  mode.onchange=()=>{
    if(!draft)return;
    draft.range_mode=mode.value;
    draft.range_start=mode.value==='custom'?(draft.range_start||saved.default_window.start):null;
    draft.range_end=mode.value==='custom'?(draft.range_end||saved.default_window.end):null;
    renderSettings();
    sync();
  };
  const focusLabel=el('label','关注点（可选）','field-label'),focus=el('input',null,'field');
  focus.id='memo-ai-focus';
  focus.maxLength=500;
  focus.oninput=()=>{if(draft){
    draft.focus=focus.value;
    sync();
  }};
  focusLabel.append(focus);
  settingsBody.append(modeLabel,dateFields,focusLabel);
  detail.append(summary,settingsBody);
  box.after(detail);
  const panel=document.querySelector('#desktop-settings-preview [data-panel="external-data"]'),
    sourceBox=el('section',null,'desktop-settings-form-stack');
  sourceBox.append(el('h3','AI 近况来源'));
  const list=el('div');
  list.id='memo-ai-sources';
  sourceBox.append(list);
  panel.append(sourceBox);
  Memorive.settingsBridge.registerSaveParticipant('ai-briefing',{isDirty:dirty,validate:()=>!dirty()||((draft.profile_ref===null||saved.model_options.some(o=>o.profile_ref===draft.profile_ref&&o.eligible))&&(draft.range_mode!=='custom'||!!draft.range_start&&!!draft.range_end&&draft.range_start<=draft.range_end&&draft.range_end<=saved.default_window.end)),save:async()=>{
    saved=await api('ai.config_save',{config:draft,expected_revision:saved.revision});
    draft=structuredClone(saved.config);
    renderSettings();
    sync();
  },discard:()=>loadSettings(true),afterUnlock:renderSettings});
  window.addEventListener('desktop:settings-category-changed',e=>{if(['research-workspace','external-data'].includes(e.detail.category))loadSettings().catch(()=>{});});
  window.addEventListener('desktop:external-sources-changed',()=>loadSettings().catch(()=>{}));
 }
 function taskActions(run,binding,container){
  if(binding.result_locator)container.append(btn('查看结果',()=>Memorive.research.openResult(run)));
  if(['QUEUED','RUNNING'].includes(binding.source_status))container.append(btn('取消生成',async e=>{
    e.currentTarget.disabled=true;
    await api('ai.cancel',{run_id:run});
  }));
  else container.append(btn('打开 AI 近况',e=>{
    paneRun={run_id:run};
    return openAI(e.currentTarget);
  }));
 }
 function init(){
   initHome();
   initSettings();
   const tools=document.querySelector('#desktop-leaderboard-preview .lb-heading-tools');
   if(tools){
     const b=btn('AI 近况',e=>openAI(e.currentTarget),'soft-button');
     b.id='memo-ai-leaderboard';
     tools.prepend(b);
   }
 }
 window.addEventListener('desktop-integrated-route',()=>{
   homeEpoch++;
   clearTimeout(homeTimer);
   closeAI(false);
   if(route()==='home'){
     homeRender();
     pollHome();
   }
 });
 window.addEventListener('desktop:research-published',()=>{
   if(route()==='home')pollHome();
   if(pane)refreshAI(pane.querySelector('.desktop-research-body')).catch(()=>{});
 });
 Memorive.researchHome=Object.freeze({choose,prompts:prompts.map(({available,...p})=>p),openAI,closeAI,taskActions,renderAIResult:showSources,loadSettings,inspect:()=>({question:question?{id:question.prompt.id,key:question.key,context:question.context}:null,ai_open:!!pane,ai_run_id:paneRun?.run_id,settings_dirty:dirty()})});
 new MutationObserver(()=>{
   for(const [n,s] of localized){if(n.isConnected)paint(n,s);else localized.delete(n);}
   for(const [n,values] of localizedAttrs){
     if(n.isConnected){for(const [key,s] of Object.entries(values))n.setAttribute(key,t(s));}else localizedAttrs.delete(n);
   }
   homeRender();
   if(saved)renderSettings();
   if(paneState&&pane)renderAI(pane.querySelector('.desktop-research-body'));
 }).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();

