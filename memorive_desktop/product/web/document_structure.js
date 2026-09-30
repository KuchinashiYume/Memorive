/* E10: node 01 settings and in-place Library source review. */
(() => {
  'use strict';
  const words={
    previous:['上一段','Previous','前へ'],next:['下一段','Next','次へ'],rows:['行','Rows','行'],columns:['列','Columns','列'],blocks:['区块','Blocks','ブロック'],gridNote:['行列编号表示源位置；工作表显示存储值，格式化显示请以原文件为准。','Row and column numbers mark source positions. Worksheets show stored values; check formatted values in the original file.','行列番号は出典の位置です。シートは保存値を表示します。書式付きの値は元ファイルで確認してください。'],pageOnly:['此块仅能定位到页，未显示不可靠的选区。','Only the source page is available for this block; the region could not be verified.','このブロックはページのみ特定でき、領域は検証できていません。'],original:['打开原文件','Open original file','元ファイルを開く'],officePreview:['下方为解析结果。请打开原文件，核对相应工作表、段落或幻灯片后再确认。','The view below is parsed data. Open the original and check the matching sheet, paragraph, or slide before confirming.','以下は解析結果です。元ファイルの該当するシート、段落、スライドを確認してください。'],title:['文献结构与来源','Document structure & sources','文書構造と出典'],settings:['解析设置','Parsing settings','解析設定'],candidates:['结构候选','Structure candidates','構造候補'],close:['关闭','Close','閉じる'],save:['保存设置','Save settings','設定を保存'],off:['关闭增强','Enhancement off','拡張なし'],native:['原生结构（PDF / Office）','Native structure (PDF / Office)','標準構造（PDF / Office）'],marker:['Marker 本地结构','Local Marker structure','ローカル Marker 構造'],runtime:['本地运行环境目录','Local runtime folder','ローカル実行環境'],profile:['解析方式','Parsing mode','解析方式'],fast:['数字版 PDF，不使用 OCR','Digital PDF, no OCR','文字 PDF・OCR なし'],mixed:['混合页面，按需 OCR','Mixed pages, selective OCR','混在ページ・必要時 OCR'],balanced:['扫描页面，完整 OCR','Scanned pages, full OCR','スキャン・全文 OCR'],saved:['已保存，仅影响新任务。','Saved. Applies to new tasks.','保存しました。新しいタスクに適用します。'],empty:['暂无结构候选。启用增强后，处理新文献时生成。','No candidates yet. Enable enhancement and process a new document.','候補はまだありません。拡張を有効にして新しい文献を処理してください。'],pending:['待人工核对','Needs human review','確認待ち'],confirmed:['已记录人工核对','Review recorded','確認を記録しました'],open:['查看原文与结构','View source & structure','原文と構造を表示'],confirm:['记录此块已核对','Record this block as reviewed','このブロックの確認を記録'],note:['填写核对依据或差异','Describe what you checked or any differences','確認した根拠や相違点を記入'],quote:['查看可用引用片段','View usable source excerpts','利用可能な引用を表示'],numeric:['核对表格数值','Check table values','表の数値を確認'],details:['详细记录','Details','詳細記録'],refresh:['刷新','Refresh','更新'],limits:['解析结果可能有遗漏或识别错误。请对照原文件核对；原件和现有正文均保留。','Parsed data may contain omissions or recognition errors. Check it against the original; the original and existing text are retained.','解析結果に欠落や認識誤りがある場合があります。元ファイルと照合してください。原文と既存の本文は保持されます。'],formula:['公式保留；缓存值未重新计算。','Formulas retained; cached values were not recalculated.','数式を保持しています。キャッシュ値は再計算していません。'],percentage:['按原文核对百分比','Check the reported percentage','記載された割合を確認'],count:['部分数量','Count','部分の数'],total:['总数量','Total count','全体の数'],percent:['报告百分比','Reported percentage','記載の割合'],unweighted:['已确认是未加权计数，且分子与分母属于同一组。','I verified these are unweighted counts from the same group.','同じ集団の重み付けなしの個数であることを確認しました。'],calculate:['执行核对','Run check','確認する'],noNumbers:['请先核对包含数值的表格块。','First review a table block containing values.','数値を含む表のブロックを先に確認してください。'],compatible:['在指定条件下相容','Compatible under these conditions','指定条件の下で整合'],inconsistent:['与指定条件不相容','Inconsistent under these conditions','指定条件の下で不整合'],insufficient:['信息不足或输入不适用','Insufficient or inapplicable input','情報不足または入力が対象外'],notWhole:['确认范围仅为当前块；不代表整篇文献或科研结论已通过。','This records review of this block only. It does not validate the whole paper or its conclusions.','このブロックのみの確認です。論文全体や結論を検証したものではありません。']
  };

  Object.assign(words, {
    title:['结构与来源','Structure & sources','構造と出典'],
    settings:['结构解析','Structure parsing','構造解析'],
    off:['不补充结构','No additional structure','構造を追加しない'],
    native:['原生解析','Native parser','標準パーサー'],
    marker:['Marker 本地解析','Local Marker parser','ローカル Marker パーサー'],
    parsingHelp:['在正文处理后补充表格、版面与来源定位，可在文献库中核对。','Add tables, layout and source locations after text processing; review them in the library.','本文処理後に表、レイアウト、出典位置を追加します。文献庫で確認できます。'],
    nativeHelp:['PDF 提取已有文字和位置；Word、Excel、PPT 保留原生结构。','PDF uses existing text and positions; Word, Excel and PPT retain their native structure.','PDF の既存テキストと位置を抽出し、Word、Excel、PPT の構造を保持します。'],
    markerHelp:['Marker 处理 PDF 的结构；Word、Excel、PPT 使用原生解析。','Marker handles PDF structure; Word, Excel and PPT use native parsing.','PDF は Marker、Word、Excel、PPT は標準パーサーを使用します。'],
    offHelp:['沿用上方模型的正文处理结果。','Use the text produced by the model above.','上のモデルによる本文処理結果を使用します。'],
    profile:['PDF 识别方式','PDF recognition','PDF の認識方式'],
    relation:['仅影响结构解析；正文仍使用上方主模型、备用模型和逐页重试设置。','Applies to structure parsing. Text processing uses the primary model, fallback and page retries above.','構造解析に適用します。本文処理には上の主モデル、代替モデル、ページ別再試行を使用します。'],
    runtimeTitle:['Marker 运行环境','Marker runtime','Marker 実行環境'],
    runtime:['运行环境目录','Runtime folder','実行環境フォルダー'],
    runtimeHelp:['填写已准备好的本地 Marker 环境目录。检查环境不会下载模型或开始解析。','Use a prepared local Marker runtime. Checking does not download models or start parsing.','準備済みのローカル Marker 環境を指定します。確認時にモデル取得や解析は行いません。'],
    timeout:['单份文献等待上限（秒）','Time limit per document (seconds)','文献ごとの制限時間（秒）'],
    timeoutHelp:['超时后保留正文，并在该文献中记录结构解析失败；不计入上方的逐页重试。','On timeout, retain text and record a structure failure for the document. It does not consume the page retries above.','時間切れ時は本文を保持して構造解析の失敗を記録します。上のページ別再試行には数えません。'],
    probe:['检查环境','Check runtime','環境を確認'],checking:['正在检查…','Checking…','確認中…'],
    ready:['环境文件检查通过；解析质量需按文献核对。','Runtime files verified; review parsing quality for each document.','実行環境ファイルを確認しました。解析品質は文献ごとに確認してください。'],
    notChecked:['环境尚未检查','Runtime not checked','実行環境は未確認です'],
    required:['请填写 Marker 运行环境目录。','Enter the Marker runtime folder.','Marker 実行環境フォルダーを入力してください。'],
    invalidTimeout:['等待上限需为 1–3600 秒的整数。','Enter a whole number from 1 to 3600 seconds.','1～3600 秒の整数を入力してください。'],
    loadFailure:['设置加载失败，请重试。','Settings could not be loaded. Retry.','設定を読み込めませんでした。再試行してください。'],
    environmentFailure:['运行环境不完整或文件已变化，请检查目录。','The runtime is incomplete or its files changed. Check the folder.','実行環境が不完全か、ファイルが変更されています。フォルダーを確認してください。'],
    conflict:['设置已在其他位置更新。请撤销本地修改后重新编辑。','Settings changed elsewhere. Revert local edits, then edit again.','別の場所で設定が更新されています。変更を元に戻して再編集してください。'],
    failure:['操作未完成，请检查后重试。','The operation did not complete. Check and retry.','操作が完了しませんでした。確認して再試行してください。'],
    newTasks:['保存后用于新任务。已开始的任务沿用创建时的设置。','Saved settings apply to new tasks. Existing tasks keep their original settings.','保存後の新規タスクに適用します。開始済みタスクは元の設定を使用します。'],
    retry:['重试','Retry','再試行'],review:['展开来源核对','Review source','出典を確認'],
    parsed:['解析内容','Parsed content','解析内容'],location:['来源位置','Source location','出典位置'],
    selectBlock:['选择区块','Select a block','ブロックを選択'],
    noContext:['尚无已核对的引用片段。','No reviewed excerpts yet.','確認済みの引用はまだありません。'],
    contextLimited:['部分区块超出引用长度上限，未纳入本次片段。','Some blocks exceed the excerpt limit and were omitted.','一部のブロックは引用上限を超えるため含まれていません。'],
    expired:['原文件或解析结果已变化，请重新处理文献。','The source or parsed result changed. Process the document again.','元ファイルまたは解析結果が変更されています。文献を再処理してください。'],
    failed:['结构解析未完成，正文仍可使用。','Structure parsing did not finish; the existing text remains available.','構造解析は完了していません。本文は引き続き利用できます。'],
    processing:['正在生成结构与来源…','Preparing structure and sources…','構造と出典を生成しています…'],
    sheets:['工作表','Sheet','シート'],slide:['幻灯片','Slide','スライド'],paragraph:['段落','Paragraph','段落'],page:['页','Page','ページ'],
    textBlock:['文字','Text','テキスト'],tableBlock:['表格','Table','表'],figureBlock:['图像','Figure','図'],otherBlock:['内容','Content','内容'],
    reviewed:['已核对','Reviewed','確認済み']
  });
  const lang=()=>{const v=document.documentElement.lang||'zh';return v.startsWith('en')?1:v.startsWith('ja')?2:0;};
  const t=k=>(words[k]||[k,k,k])[lang()];
  const node=(tag,txt,cls)=>{const e=document.createElement(tag);if(txt!==undefined)e.textContent=txt;if(cls)e.className=cls;return e;};
  const localized=(tag,key,cls)=>{const e=node(tag,t(key),cls);e.dataset.e10I18n=key;return e;};
  // Re-project presentation text on language changes without reloading source data
  // or discarding a selection, review note, or unsaved settings.
  const dynamicText=new WeakMap();
  const dynamic=(tag,render,cls)=>{const e=node(tag,render(),cls);e.dataset.e10Dynamic='';dynamicText.set(e,render);return e;};
  const call=async(method,params={})=>{if(!window.pywebview?.api?.call)throw Error('NATIVE_TRANSPORT_REQUIRED');const result=await window.pywebview.api.call(method,params);if(result?.status==='BLOCKED')throw Error(result.code||'STRUCTURE_CONFIG_INVALID');return result;};
  const button=(key,run)=>{const b=localized('button',key,'soft-button');b.type='button';b.onclick=run;return b;};
  const select=items=>{const e=node('select',undefined,'select');for(const [v,k] of items){const o=localized('option',k);o.value=v;e.append(o);}return e;};
  const field=(key,input)=>{const label=localized('span',key),wrap=node('label',undefined,'field-label');wrap.append(label,input);return wrap;};
  const help=key=>localized('small',key,'desktop-settings-model-description');
  const errorKey=error=>{const v=String(error?.message||error);return /REVISION_CONFLICT/.test(v)?'conflict':/MARKER_RUNTIME|RUNTIME_/.test(v)?'environmentFailure':/STALE|CHANGED|REVISION_CHANGED/.test(v)?'expired':'failure';};
  function setMessage(target,key){target.dataset.e10I18n=key;target.textContent=t(key);}
  function details(parent,value){const d=node('details',undefined,'desktop-copy-technical'),s=localized('summary','details'),pre=node('pre',JSON.stringify(value,null,2));d.append(s,pre);parent.append(d);}
  function guarded(target,fn){return async event=>{event?.preventDefault();const control=event?.currentTarget;control?.setAttribute('aria-busy','true');if(control)control.disabled=true;
    target.textContent='';delete target.dataset.e10I18n;try{return await fn();}catch(error){setMessage(target,errorKey(error));}finally{if(control){control.disabled=false;control.removeAttribute('aria-busy');}}};}

  // This group participates in the existing Settings save/revert transaction.
  let saved=null,settingsRoot,mode,profile,runtime,timeout,advanced,markerFields,settingStatus,probeStatus,probeButton,retryButton,modeHelp;
  let loading=null,checking=false;
  const settingsBridge=()=>window.__Desktop_SETTINGS_BRIDGE__;
  const draft=()=>({mode:mode.value,profile:profile.value,runtime_path:runtime.value.trim(),timeout_seconds:Number(timeout.value),runtime_lock_sha256:saved?.config.runtime_path===runtime.value.trim()?saved.config.runtime_lock_sha256:null});
  const isDirty=()=>Boolean(saved)&&Object.entries(draft()).some(([key,value])=>value!==saved.config[key]);
  const valid=()=>Boolean(saved)&&Number.isInteger(Number(timeout.value))&&Number(timeout.value)>=1&&Number(timeout.value)<=3600&&(mode.value!=='marker'||Boolean(runtime.value.trim()));
  function dependencyState(){
    const marker=mode.value==='marker';markerFields.hidden=!marker;mode.disabled=!saved;profile.disabled=!saved||!marker;runtime.disabled=!saved||!marker;timeout.disabled=!saved||!marker;probeButton.disabled=!saved||!marker||!runtime.value.trim()||checking;
    setMessage(modeHelp,marker?'markerHelp':mode.value==='native'?'nativeHelp':'offHelp');
    let key='';if(saved&&!valid())key=marker&&!runtime.value.trim()?'required':'invalidTimeout';
    if(key)setMessage(settingStatus,key);else if(['required','invalidTimeout'].includes(settingStatus.dataset.e10I18n)){settingStatus.textContent='';delete settingStatus.dataset.e10I18n;}
  }
  function acceptSettings(value){saved=value;mode.value=value.config.mode;profile.value=value.config.profile;runtime.value=value.config.runtime_path;timeout.value=value.config.timeout_seconds;dependencyState();settingsBridge()?.syncSaveState();}
  async function loadSettings(){
    if(loading)return loading;
    loading=(async()=>{try{acceptSettings(await call('structure.settings'));retryButton.hidden=true;settingStatus.textContent='';delete settingStatus.dataset.e10I18n;}
      catch(error){setMessage(settingStatus,'loadFailure');retryButton.hidden=false;throw error;}finally{loading=null;}})();return loading;
  }
  async function saveSettings(){
    if(!valid())throw Error('STRUCTURE_CONFIG_INVALID');
    try{const result=await call('structure.configure',{expected_revision:saved.revision,config:draft()});acceptSettings(result);if(result.config.mode==='marker')setMessage(probeStatus,'ready');setMessage(settingStatus,'saved');}
    catch(error){setMessage(settingStatus,errorKey(error));throw error;}
  }
  function installSettings(){
    const target=document.querySelector('#desktop-settings-preview #settings-node-test-row');if(!target)return;
    settingsRoot=node('div',undefined,'desktop-settings-model-setting');settingsRoot.id='settings-ingest-structure';settingsRoot.hidden=true;settingsRoot.setAttribute('data-desktop-no-translate','');
    mode=select([['off','off'],['native','native'],['marker','marker']]);mode.id='settings-structure-mode';
    modeHelp=help('offHelp');
    settingsRoot.append(localized('strong','title','desktop-settings-model-heading'),help('parsingHelp'),field('settings',mode),modeHelp);
    markerFields=node('div',undefined,'e10-stack');markerFields.id='settings-structure-marker-fields';markerFields.hidden=true;
    profile=select([['fast_no_ocr','fast'],['fast_ocr','mixed'],['balanced_ocr','balanced']]);profile.id='settings-structure-profile';
    advanced=node('details',undefined,'desktop-settings-local-model-advanced');advanced.id='settings-structure-runtime-details';
    const summary=node('summary',undefined,'desktop-settings-disclosure');summary.append(localized('span','runtimeTitle'),node('span','›','desktop-settings-disclosure-icon'));advanced.append(summary);
    runtime=node('input',undefined,'field');runtime.id='settings-structure-runtime';runtime.type='text';runtime.autocomplete='off';runtime.spellcheck=false;
    timeout=node('input',undefined,'field');timeout.id='settings-structure-timeout';timeout.type='number';timeout.min='1';timeout.max='3600';timeout.step='1';
    probeStatus=help('notChecked');probeStatus.id='settings-structure-runtime-status';probeStatus.setAttribute('role','status');
    probeButton=button('probe',async()=>{
      if(checking)return;checking=true;dependencyState();setMessage(probeStatus,'checking');const before=JSON.stringify(draft());
      try{await call('structure.probe',{config:draft()});if(before===JSON.stringify(draft()))setMessage(probeStatus,'ready');}
      catch(error){if(before===JSON.stringify(draft()))setMessage(probeStatus,errorKey(error));}
      finally{checking=false;dependencyState();}
    });probeButton.id='settings-structure-probe';
    advanced.append(field('runtime',runtime),help('runtimeHelp'),field('timeout',timeout),help('timeoutHelp'),probeButton,probeStatus);
    markerFields.append(field('profile',profile),help('relation'),advanced);settingsRoot.append(markerFields,help('newTasks'));
    settingStatus=help('');settingStatus.id='settings-structure-status';settingStatus.setAttribute('role','status');
    retryButton=button('retry',()=>void loadSettings().catch(()=>{}));retryButton.hidden=true;settingsRoot.append(settingStatus,retryButton);target.before(settingsRoot);
    for(const input of [mode,profile,runtime,timeout])for(const type of ['input','change'])input.addEventListener(type,event=>{event.stopPropagation();dependencyState();setMessage(probeStatus,'notChecked');settingsBridge()?.syncSaveState();});
    mode.addEventListener('change',()=>{if(mode.value==='marker'&&!runtime.value.trim())advanced.open=true;});
    const panel=document.querySelector('#desktop-settings-preview #settings-node-settings');
    const syncVisibility=()=>{settingsRoot.hidden=panel.dataset.ingestActive!=='true';};
    new MutationObserver(syncVisibility).observe(panel,{attributes:true,attributeFilter:['data-ingest-active']});syncVisibility();
    settingsBridge()?.registerSaveParticipant('ingest-structure',{
      isDirty,validate:valid,save:saveSettings,
      discard:async()=>{await loadSettings();setMessage(probeStatus,'notChecked');},afterUnlock:dependencyState
    });
    dependencyState();
    if(window.pywebview?.api?.call)void loadSettings().catch(()=>{});
    window.addEventListener('pywebviewready',()=>void loadSettings().catch(()=>{}),{once:true});
  }

  // Candidate review lives inside the selected Library document inspector.
  let librarySection,libraryBody,libraryStatus,activeJob=null,selectionVersion=0;
  function sourceLabel(location={},block={}){
    if(location.physical_page||block.physical_page)return t('page')+' '+(location.physical_page||block.physical_page);
    if(location.sheet)return t('sheets')+' '+location.sheet;
    if(location.slide)return t('slide')+' '+location.slide;
    if(location.paragraph!==undefined)return t('paragraph')+' '+(location.paragraph+1);
    return '';
  }
  function blockLabel(block){const type=block.table_count?'tableBlock':/Figure|Image/.test(block.block_type)?'figureBlock':/Text|Paragraph|Heading|Header|Footer/.test(block.block_type)?'textBlock':'otherBlock';return [sourceLabel(block.source_location,block),t(type),(block.preview||'').slice(0,70)].filter(Boolean).join(' · ');}
  async function selectDocument(record){
    const version=++selectionVersion;activeJob=null;librarySection.hidden=true;delete librarySection.dataset.jobId;libraryBody.replaceChildren();libraryStatus.textContent='';delete libraryStatus.dataset.e10I18n;
    const jobId=record?.task_id||record?.task||record?.job_id;if(!jobId)return;
    try{const result=await call('structure.status',{job_id:jobId});if(version!==selectionVersion||result.status==='NOT_FOUND')return;
      activeJob=jobId;librarySection.dataset.jobId=jobId;librarySection.hidden=false;
      if(result.job.status==='FAILED'){libraryBody.append(help('failed'));details(libraryBody,{status:result.job.status,error_type:result.job.error_type});return;}
      if(!['CANDIDATE_NOT_ADMITTED','PARTIALLY_CONFIRMED'].includes(result.job.status)){libraryBody.append(help('processing'));return;}
      libraryBody.append(dynamic('p',()=>`${result.job.block_count} ${t('blocks')} · ${result.job.table_count} ${t('tableBlock')}`,'desktop-library-source-note'));
      const review=node('details');review.id='library-structure-review';review.append(localized('summary','review'));const content=node('div',undefined,'e10-stack');review.append(content);libraryBody.append(review);
      review.addEventListener('toggle',()=>{if(review.open&&!content.childElementCount)void guarded(libraryStatus,()=>showDocument(jobId,content,0,version))();});
    }catch(error){if(version===selectionVersion){librarySection.hidden=false;setMessage(libraryStatus,errorKey(error));}}
  }
  async function showDocument(jobId,parent,offset,version){
    const value=await call('structure.inspect',{job_id:jobId,offset,limit:100});if(version!==selectionVersion)return;parent.replaceChildren();
    const choices=node('select',undefined,'select');choices.id='library-structure-block';const placeholder=localized('option','selectBlock');placeholder.value='';choices.append(placeholder);
    for(const block of value.blocks){const o=dynamic('option',()=>blockLabel(block));o.value=block.block_id;choices.append(o);}
    const viewer=node('div',undefined,'e10-stack');
    choices.onchange=guarded(libraryStatus,async()=>{if(choices.value)await showBlock(jobId,choices.value,viewer,version);else viewer.replaceChildren();});
    parent.append(field('selectBlock',choices));
    if(value.block_total>100){const paging=node('div',undefined,'e10-actions');paging.append(node('small',`${offset+1}–${Math.min(value.block_total,offset+100)} / ${value.block_total}`));const prev=button('previous',guarded(libraryStatus,()=>showDocument(jobId,parent,Math.max(0,offset-100),version))),next=button('next',guarded(libraryStatus,()=>showDocument(jobId,parent,offset+100,version)));prev.disabled=offset===0;next.disabled=offset+100>=value.block_total;paging.append(prev,next);parent.append(paging);}
    parent.append(viewer);
    const excerpts=node('details');excerpts.append(localized('summary','quote'));const excerptBody=node('div',undefined,'e10-stack');excerpts.append(excerptBody);excerpts.addEventListener('toggle',()=>{if(excerpts.open)void guarded(libraryStatus,()=>showContext(jobId,excerptBody,version))();});
    const numbers=node('details');numbers.append(localized('summary','numeric'));const numericBody=node('div',undefined,'e10-stack');numbers.append(numericBody);numbers.addEventListener('toggle',()=>{if(numbers.open)void guarded(libraryStatus,()=>showNumbers(jobId,numericBody,version))();});
    parent.append(excerpts,numbers);
  }
  async function showBlock(jobId,blockId,viewer,version){
    viewer.dataset.pendingBlock=blockId;
    const data=await call('structure.preview',{job_id:jobId,block_id:blockId});if(version!==selectionVersion||viewer.dataset.pendingBlock!==blockId)return;viewer.replaceChildren();
    viewer.append(dynamic('strong',()=>sourceLabel(data.source_location)),button('original',guarded(libraryStatus,async()=>{await window.pywebview.api.open_structure_original(jobId);}))); 
    if(data.page_image){const frame=node('div',undefined,'e10-page'),img=node('img');img.src=data.page_image;img.alt=t('open');img.dataset.e10Alt='open';frame.append(img);
      if(data.overlay_bbox){const [x0,y0,x1,y1]=data.overlay_bbox,overlay=node('span',undefined,'e10-overlay');Object.assign(overlay.style,{left:(x0/data.page_width*100)+'%',top:(y0/data.page_height*100)+'%',width:((x1-x0)/data.page_width*100)+'%',height:((y1-y0)/data.page_height*100)+'%'});frame.append(overlay);}viewer.append(frame);}
    if(data.page_image&&!data.overlay_bbox)viewer.append(help('pageOnly'));
    if(!data.page_image)viewer.append(help('officePreview'));
    const parsed=node('details');parsed.open=!data.tables.length;parsed.append(localized('summary','parsed'),node('pre',data.text));viewer.append(parsed);
    for(const table of data.tables)renderTable(viewer,table);
    const note=node('textarea',undefined,'textarea');note.maxLength=1000;note.id='library-structure-review-note';
    const confirmed=node('small');confirmed.setAttribute('role','status');
    const confirm=button('confirm',guarded(confirmed,async()=>{await call('structure.confirm',{job_id:jobId,block_id:blockId,structure_id:data.structure_id,note:note.value,operation_id:crypto.randomUUID()});setMessage(confirmed,'confirmed');}));confirm.id='library-structure-confirm';confirm.disabled=true;
    note.oninput=()=>{confirm.disabled=!note.value.trim();};
    viewer.append(help('notWhole'),field('note',note),confirm,confirmed);details(viewer,data.source_location);
  }
  function renderTable(parent,table){
    const host=node('section',undefined,'e10-table');parent.append(host);
    const lookup=new Map(table.cells.map(c=>[`${c.row}:${c.column}`,c]));
    const address=s=>{const m=/^([A-Z]+)([1-9][0-9]*)$/.exec(s);if(!m)return null;let c=0;for(const ch of m[1])c=c*26+ch.charCodeAt(0)-64;return [+m[2]-1,c-1];};
    const spans=table.cells.filter(c=>(c.rowspan||1)>1||(c.colspan||1)>1).map(c=>({r:c.row,c:c.column,r1:c.row+(c.rowspan||1)-1,c1:c.column+(c.colspan||1)-1}));
    for(const ref of table.merged_ranges||[]){const parts=ref.split(':').map(address);if(parts.length!==2||parts.some(p=>!p))continue;spans.push({r:parts[0][0],c:parts[0][1],r1:parts[1][0],c1:parts[1][1]});}
    const rows=Math.max(table.rows||0,...spans.map(s=>s.r1+1)),cols=Math.max(table.columns||0,...spans.map(s=>s.c1+1));
    let r0=0,c0=0;
    const draw=()=>{
      host.replaceChildren(localized('p','gridNote'));const controls=node('div',undefined,'e10-actions');
      controls.append(localized('span','rows'),node('span',`${r0+1}–${Math.min(rows,r0+30)} / ${rows}`));
      const rp=button('previous',()=>{r0=Math.max(0,r0-30);draw();}),rn=button('next',()=>{r0+=30;draw();});rp.disabled=r0===0;rn.disabled=r0+30>=rows;controls.append(rp,rn,localized('span','columns'),node('span',`${c0+1}–${Math.min(cols,c0+12)} / ${cols}`));
      const cp=button('previous',()=>{c0=Math.max(0,c0-12);draw();}),cn=button('next',()=>{c0+=12;draw();});cp.disabled=c0===0;cn.disabled=c0+12>=cols;controls.append(cp,cn);host.append(controls);
      const endR=Math.min(rows,r0+30),endC=Math.min(cols,c0+12),covered=new Map();
      for(const span of spans)for(let r=Math.max(r0,span.r);r<=Math.min(endR-1,span.r1);r++)for(let c=Math.max(c0,span.c);c<=Math.min(endC-1,span.c1);c++)covered.set(`${r}:${c}`,span);
      const scroll=node('div',undefined,'e10-table-scroll'),element=node('table'),tbody=node('tbody');
      for(let r=r0;r<endR;r++){const tr=node('tr');for(let c=c0;c<endC;c++){
        const span=covered.get(`${r}:${c}`);if(span&&(r!==Math.max(r0,span.r)||c!==Math.max(c0,span.c)))continue;
        const cell=lookup.get(span?`${span.r}:${span.c}`:`${r}:${c}`)||{row:r,column:c,text:''};
        const td=node(cell.header?'th':'td',cell.text);td.dataset.sourceRow=cell.row;td.dataset.sourceColumn=cell.column;td.title=cell.address||`${cell.row+1}:${cell.column+1}`;
        if(span){td.rowSpan=Math.min(endR,span.r1+1)-r;td.colSpan=Math.min(endC,span.c1+1)-c;}
        if(cell.formula!==null&&cell.formula!==undefined){const small=node('small',' ='+cell.formula+' · ');small.append(localized('span','formula'));td.append(small);}
        tr.append(td);
      }tbody.append(tr);}element.append(tbody);scroll.append(element);host.append(scroll);
    };draw();
  }

  async function showContext(jobId,parent,version){const data=await call('structure.context',{job_id:jobId,budget:8000});if(version!==selectionVersion)return;parent.replaceChildren();if(!data.fragments.length)parent.append(help('noContext'));for(const f of data.fragments)parent.append(node('pre',f.text));if(data.truncated)parent.append(help('contextLimited'));details(parent,data);}
  async function showNumbers(jobId,parent,version){
    const data=await call('structure.numeric_review',{job_id:jobId});if(version!==selectionVersion)return;parent.replaceChildren();
    const records=data.confirmed_structure_records;if(!records.length){parent.append(help('noNumbers'));return;}
    const form=node('form',undefined,'e10-stack');form.append(localized('strong','percentage'));const inputs={};
    for(const [key,label] of [['count','count'],['denominator','total'],['percent','percent']]){const sel=node('select',undefined,'select');sel.required=true;const empty=node('option','—');empty.value='';sel.append(empty);for(const r of records){const o=node('option',r.source.quote+' · '+(r.source.row+1)+':'+(r.source.column+1));o.value=r.record_id;sel.append(o);}form.append(field(label,sel));inputs[key]=sel;}
    const check=node('input');check.type='checkbox';check.required=true;const agree=node('label',undefined,'e10-check');agree.append(check,localized('span','unweighted'));form.append(agree);
    const run=button('calculate',null);run.type='submit';form.append(run);const output=node('div',undefined,'e10-stack');
    form.onsubmit=async event=>{event.preventDefault();if(!form.reportValidity())return;run.disabled=true;try{const rule={rule_id:'structure-user-percentage',type:'percentage',inputs:Object.fromEntries(Object.entries(inputs).map(([k,e])=>[k,e.value])),parameters:{unweighted_count:check.checked,rounding:'nearest'},binding_source:{kind:'EXPLICIT_USER_MAPPING'}};const result=await call('structure.numeric_review',{job_id:jobId,rules:[rule]});if(version!==selectionVersion)return;output.replaceChildren();const hit=result.report.checks?.find(v=>v.rule_id===rule.rule_id);output.append(help(hit?.status==='COMPATIBLE'?'compatible':hit?.status==='INCONSISTENT'?'inconsistent':'insufficient'));details(output,result);}catch(error){setMessage(libraryStatus,errorKey(error));}finally{run.disabled=false;}};
    parent.append(form,output);details(parent,records);
  }
  function installLibrary(){
    const files=document.querySelector('#desktop-library-preview #library-files-section');if(!files)return;
    librarySection=node('section',undefined,'inspector-section');librarySection.id='library-structure-section';librarySection.hidden=true;librarySection.setAttribute('data-desktop-no-translate','');
    libraryBody=node('div',undefined,'e10-stack');libraryStatus=node('small');libraryStatus.setAttribute('role','status');librarySection.append(localized('h4','title'),libraryBody,libraryStatus);files.after(librarySection);
    window.addEventListener('desktop:library-selection',event=>void selectDocument(event.detail?.record));
    document.querySelector('#desktop-library-preview').addEventListener('desktop:inspector-closed',()=>{++selectionVersion;activeJob=null;librarySection.hidden=true;});
  }
  function install(){
    const style=node('style');style.textContent=`
      #settings-ingest-structure[hidden],#settings-structure-marker-fields[hidden],#library-structure-section[hidden]{display:none}
      #settings-ingest-structure{gap:10px}
      #settings-ingest-structure .desktop-settings-local-model-advanced{margin:0}
      #settings-ingest-structure .field-label{font-size:var(--desktop-settings-font-helper);line-height:1.5}
      #settings-ingest-structure [role=status]:empty,#library-structure-section [role=status]:empty{display:none}
      #settings-ingest-structure .e10-stack,#library-structure-section .e10-stack{display:grid;gap:10px;min-width:0}
      #library-structure-section summary{cursor:pointer;color:var(--text-primary);font-size:12px;line-height:1.6}
      #library-structure-section details[open]>summary{margin-bottom:10px}
      #library-structure-section small,#library-structure-section .e10-actions{color:var(--text-secondary);font-size:11px;line-height:1.6}
      #library-structure-section pre{white-space:pre-wrap;overflow-wrap:anywhere;font:11px/1.6 var(--font-mono);max-height:320px;overflow:auto;margin:0}
      #library-structure-section .e10-actions{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
      #library-structure-section .e10-page{position:relative}#library-structure-section .e10-page img{display:block;width:100%;height:auto}
      #library-structure-section .e10-overlay{position:absolute;border:2px solid var(--text-secondary);background:color-mix(in srgb,var(--text-secondary) 12%,transparent);pointer-events:none}
      #library-structure-section .e10-table-scroll{overflow:auto;max-width:100%;margin-top:8px}
      #library-structure-section table{border-collapse:collapse;min-width:100%;font-size:11px}
      #library-structure-section td,#library-structure-section th{border:1px solid var(--border-subtle);padding:7px;min-width:48px;white-space:pre-wrap}
      #library-structure-section .e10-check{display:flex;align-items:flex-start;gap:8px;font-size:11px;color:var(--text-secondary)}
      #library-structure-section .e10-table small{display:block}
    `;document.head.append(style);installSettings();installLibrary();
    new MutationObserver(()=>{
      for(const e of document.querySelectorAll('[data-e10-i18n]'))e.textContent=t(e.dataset.e10I18n);
      for(const e of document.querySelectorAll('[data-e10-dynamic]')){const render=dynamicText.get(e);if(render)e.textContent=render();}
      for(const e of document.querySelectorAll('[data-e10-alt]'))e.alt=t(e.dataset.e10Alt);
    }).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
  }
  window.__E10_STRUCTURE_UI__=Object.freeze({refreshSettings:loadSettings});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
