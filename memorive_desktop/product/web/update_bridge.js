(() => {
  'use strict';
  const panel = document.querySelector('[data-panel="update"] .desktop-settings-form-stack');
  if (!panel) return;
  const words = {
    title:['软件更新','Software updates','ソフトウェア更新'],
    automatic:['自动检查更新','Check for updates automatically','更新を自動確認'],
    autoHelp:['只检查正式版本，不会自动下载或安装。','Checks stable releases. Downloads and installation require your action.','正式リリースのみ確認します。ダウンロードとインストールは手動です。'],
    check:['检查更新','Check for updates','更新を確認'],
    prepare:['查看下载方案','Review download','ダウンロード内容を確認'],
    download:['下载更新','Download update','更新をダウンロード'],
    full:['同意下载完整包','Download full package','完全パッケージをダウンロード'],
    cancel:['取消下载','Cancel download','ダウンロードをキャンセル'],
    apply:['退出后更新','Update after exit','終了後に更新'],
    recover:['打开恢复助手','Open recovery assistant','復元アシスタントを開く'],
    details:['诊断详情','Diagnostic details','診断の詳細'],
    IDLE:['尚未检查更新。','Updates have not been checked.','更新はまだ確認されていません。'],
    CHECKING:['正在检查正式版本…','Checking stable releases…','正式リリースを確認しています…'],
    UP_TO_DATE:['当前已是最新正式版本。','You have the latest stable release.','最新の正式リリースです。'],
    AVAILABLE:['发现可用更新。请先确认下载方案。','An update is available. Review the download first.','更新があります。ダウンロード内容を確認してください。'],
    NO_COMPATIBLE_UPDATE:['当前发布尚未提供兼容更新包。','This release does not provide a compatible update package.','このリリースには互換性のある更新パッケージがありません。'],
    VERIFYING_BASE:['正在核对当前版本，选择可用的增量包…','Verifying this version to select a compatible delta…','互換性のある差分を選ぶため現在のバージョンを検証しています…'],
    WAITING_FULL_CONSENT:['需要下载完整包。确认大小后再继续。','A full download is required. Review its size before continuing.','完全パッケージが必要です。サイズを確認してから続行してください。'],
    DOWNLOADING:['正在下载，可继续使用。','Downloading. You can keep working.','ダウンロード中です。作業を続けられます。'],
    VERIFIED:['下载已验证。启动更新助手后，请正常退出此实例。','Download verified. Open the assistant, then exit this instance normally.','ダウンロードを検証しました。アシスタントを開いてから通常の操作でこのインスタンスを終了してください。'],
    WAITING_IDLE:['更新助手正在等待此实例完成任务并退出。','The update assistant is waiting for this instance to finish and exit.','更新アシスタントはこのインスタンスのタスク完了と終了を待っています。'],
    CANCELLED:['本次下载已取消。','This download was cancelled.','ダウンロードをキャンセルしました。'],
    COMPLETE:['更新已完成。','Update complete.','更新が完了しました。'],
    RECOVERY_REQUIRED:['上次更新未完成，请打开恢复助手。','The last update is incomplete. Open the recovery assistant.','前回の更新は未完了です。復元アシスタントを開いてください。'],
    failure:['操作未完成，原版本仍保留。请在高级配置中查看诊断详情。','The operation did not complete. The previous version is retained. See diagnostic details in Advanced configuration.','処理が完了しませんでした。以前のバージョンは保持されています。詳細設定で診断の詳細を確認してください。'],
    pendingKey:['正式发布签名尚未配置，当前候选无法验证线上更新。','Production signing is not configured. This candidate cannot verify online updates.','正式リリースの署名が未設定のため、この候補ではオンライン更新を検証できません。'],
    baseMismatch:['当前文件与增量基线不同。','Current files do not match the delta baseline.','現在のファイルが差分の基準と一致しません。'],
    noDelta:['没有适合当前版本且更小的增量包。','No smaller delta matches this version.','このバージョンに対応する小さい差分がありません。']
  };
  const language=()=>window.__Desktop_SETTINGS_I18N__?.language?.()||document.documentElement.lang||'zh-CN';
  const text=key=>(words[key]||words.failure)[{'en-US':1,'ja-JP':2}[language()]||0];
  // Reuse the existing settings list. No nested padded form stack.
  const section=panel, template=document.createElement('template');
  template.innerHTML='<div class="switch-row"><span class="switch-copy"><strong data-update-text="automatic"></strong><span data-update-text="autoHelp"></span></span><button class="switch" type="button" role="switch" aria-checked="true" data-update-action="preference"></button></div><div class="desktop-settings-control-row"><span><strong data-update-text="title"></strong><small data-update-status role="status" aria-live="polite"></small><small data-update-size hidden></small></span><button class="soft-button" type="button" data-update-action="check" data-update-text="check"></button></div><div class="desktop-settings-action-grid" data-update-buttons hidden></div><progress data-update-progress hidden></progress><small data-update-reason hidden></small><p class="desktop-settings-help" data-update-notes hidden></p><details class="desktop-settings-control-row" data-about-advanced hidden><summary data-update-text="details"></summary><pre data-update-diagnostics></pre></details>';
  for(const child of template.content.children)child.dataset.desktopNoTranslate='';
  const version=panel.querySelector('[data-memo-release-version]').closest('.desktop-settings-control-row');
  version.after(template.content);
  const releaseNotes=panel.querySelector('[data-update-notes]');
  function mode(){
    const advanced=panel.closest('.desktop-settings-shell').dataset.settingsMode==='advanced';
    for(const row of panel.querySelectorAll('[data-about-advanced]')){row.hidden=!advanced;if(!advanced&&row.tagName==='DETAILS')row.open=false;}
  }
  window.addEventListener('desktop:settings-mode-changed',mode);
  window.addEventListener('desktop:settings-mode-scope-changed',mode);
  let state={},requesting=false,timer=null;
  const query=selector=>section.querySelector(selector);
  const call=async(action,params={})=>window.pywebview.api.call('updates.'+action,params);
  function render(){
    section.querySelectorAll('[data-update-text]').forEach(node=>node.textContent=text(node.dataset.updateText));
    const update=state.update||{},stage=update.stage||update.state||'IDLE',error=state.error||update.error;
    query('[data-update-status]').textContent=error==='UPDATE_RELEASE_TRUST_NOT_CONFIGURED'?text('pendingKey'):error?text('failure'):text(stage);
    query('[data-update-diagnostics]').textContent=JSON.stringify({error:error||null,state:stage,operation:state.operation_id||null},null,2);
    const size=update.asset?.size||update.total||0;query('[data-update-size]').textContent=size?(size/(1024*1024)).toLocaleString(language(),{maximumFractionDigits:1})+' MiB':'';
    query('[data-update-reason]').textContent=update.fallback_reason?text(update.fallback_reason==='UPDATE_BASE_MISMATCH'?'baseMismatch':'noDelta'):'';
    releaseNotes.textContent=state.release?.notes?.[language()]||'';
    for(const node of [query('[data-update-size]'),query('[data-update-reason]'),releaseNotes])node.hidden=!node.textContent;
    mode();
    const automatic=query('[data-update-action="preference"]');automatic.setAttribute('aria-checked',String(state.automatic_enabled!==false));automatic.setAttribute('aria-label',text('automatic'));
    query('[data-update-action="check"]').disabled=requesting||state.busy||(update.stage&&!['COMPLETE','FAILED','CANCELLED','ROLLED_BACK'].includes(stage));
    const buttons=query('[data-update-buttons]');buttons.replaceChildren();
    let actions=[];
    if(stage==='AVAILABLE')actions=update.asset?['download']:['prepare'];
    if(stage==='WAITING_FULL_CONSENT')actions=['full'];
    if(stage==='DOWNLOADING')actions=state.busy?['cancel']:['download','cancel'];
    if(stage==='VERIFYING_BASE')actions=['cancel'];
    if(stage==='DOWNLOAD_FAILED')actions=['download','cancel'];
    if(stage==='VERIFIED')actions=['apply','cancel'];
    if(stage==='WAITING_IDLE'&&update.new_version&&!state.busy)actions=['recover'];
    if(['RECOVERY_REQUIRED','MIGRATING','MIGRATED','ACTIVATING','BACKING_UP','BACKED_UP','VERIFYING_START','APPLYING'].includes(stage)&&!state.busy)actions=['recover'];
    for(const action of actions){const button=document.createElement('button');button.type='button';button.className='soft-button';button.textContent=text(action);button.dataset.updateAction=action;button.disabled=requesting||(state.busy&&action!=='cancel');buttons.append(button);}
    buttons.hidden=!actions.length;
    const progress=query('[data-update-progress]');progress.hidden=!['DOWNLOADING','VERIFYING_BASE'].includes(stage);progress.max=update.total||1;progress.value=update.current||0;
  }
  async function refresh(){try{state=await call('status');render();}catch(error){state.error=String(error);render();}}
  section.addEventListener('click',async event=>{
    const button=event.target.closest('[data-update-action]');if(!button||requesting)return;
    const action=button.dataset.updateAction;requesting=true;render();
    try{
      if(action==='preference')state=await call(action,{enabled:state.automatic_enabled===false});
      else if(action==='prepare')state=await call(action,{target:state.update?.package_id});
      else if(action==='full')state=await call('download',{allow_full:true});
      else if(action==='apply'||action==='recover'){await call(action);state.update={...state.update,stage:'WAITING_IDLE'};}
      else state=await call(action);
    }catch(error){state.error=String(error);}
    finally{requesting=false;render();}
  });
  window.addEventListener('desktop:settings-locale-change',render);
  window.addEventListener('pywebviewready',async()=>{
    await refresh();timer=setInterval(()=>{if(!document.hidden&&!panel.closest('[hidden]'))void refresh();},1500);
    setTimeout(async()=>{if(state.automatic_enabled!==false){try{state=await call('check',{automatic:true});render();}catch(error){state.error=String(error);render();}}},30000);
  });
  window.addEventListener('beforeunload',()=>clearInterval(timer));render();
})();
