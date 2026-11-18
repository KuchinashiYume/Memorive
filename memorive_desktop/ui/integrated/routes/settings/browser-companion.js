/* The browser companion on the settings page: the extension that reads web chat sessions into the sessions page.
   Shows its status and reading progress, prepares, reloads and checks the extension, and starts an incremental or
   full sync (browser_companion_status, install_browser_companion, the run_browser_companion_* window methods and
   sync_browser_sessions_from_downloads).
   Publishes Memorive.browserCompanion (render, rerender, prepare, reload, sync, syncFull, reloadState,
   fullSyncState), which routes/settings/settings.js uses; the site list is routes/settings/browser-sites.js.
   Its texts come from Memorive.settingsText, notices and window methods from the settings bridge
   (notify, nativeAction). */
(() => {
  'use strict';
  const root = document.getElementById('desktop-settings-preview');
  if (!root) return;
  const q = (selector) => root.querySelector(selector);
  const settings = () => Memorive.settingsBridge;
  const uiText = (...args) => Memorive.settingsText.text(...args);
  const showToast = (message) => settings().notify(message);
  const nativeAction = (...args) => settings().nativeAction(...args);

  let browserCompanionState = null;
  let browserCompanionDraft = null;
  let browserCompanionReloadState = 'idle';
  let browserCompanionSetupInFlight = false;
  let browserCompanionSyncState = 'idle';
  let browserCompanionActiveSyncMode = null;
  let browserCompanionFullSyncState = 'idle';
  let browserCompanionProgressState = { mode:'idle', attempt:0, total:0, error:'' };

  const browserReadOutcome = (projection) => {
    const rows = (projection?.providers || []).map(id => projection?.provider_sync?.[id] || {});
    const failed = rows.filter(row => ['ERROR','FAILED','NOT_OPEN'].includes(row.status) || Number(row.failed_count || 0) > 0);
    return { rows, failed, count:rows.reduce((sum,row)=>sum+Number(row.captured_count || 0),0),
      complete:rows.length > 0 && rows.every(row=>row.status==='COMPLETE' && Number(row.failed_count || 0)===0) };
  };
  const browserReadSummary = (projection) => {
    const result=browserReadOutcome(projection);
    return result.failed.length ? uiText('browser.result.partial','已读取 {count} 条 · {failed} 个网站需重试',{count:result.count,failed:result.failed.length})
      : result.complete && projection?.import_verified ? uiText('browser.result.done','会话已同步 · 本次读取 {count} 条',{count:result.count}) : '';
  };
  const renderBrowserCompanionProgress = (projection) => {
    const label = q('#settings-browser-progress-label');
    const bar = q('#settings-browser-read-progress');
    if (!label || !bar) return;
    const prepared = projection?.prepared === true;
    const connected = projection?.bridge_identity_verified === true;
    const providerRows = Object.values(projection?.provider_sync || {});
    const synced = projection?.import_verified === true && browserReadOutcome(projection).complete;
    const completed = Number(prepared) + Number(connected) + Number(synced);
    const progress = browserCompanionProgressState;
    const stage = completed ? connected ? synced
      ? uiText('browser.stage.synced', '会话已同步')
      : uiText('browser.stage.connected-short', '浏览器已连接')
      : uiText('browser.stage.prepared', '组件已准备')
      : uiText('browser.progress.not-started', '未开始');
    let copy = uiText('browser.progress.ready', stage, { completed, stage });
    let remaining = null;
    if (prepared && !connected && !projection?.extension_id && ['idle','manual'].includes(progress.mode)) {
      copy = uiText('browser.progress.manual-load','待加载插件');
    } else if (progress.mode === 'identity' && !connected) {
      remaining = Math.max(0, progress.total - progress.attempt);
      copy = uiText('browser.progress.identity', '等待浏览器连接', { attempt:progress.attempt, total:progress.total, remaining });
    } else if (progress.mode === 'sync') {
      copy = uiText('browser.progress.sync-bar', '正在读取网页会话');
    } else if (progress.mode === 'import') {
      copy = uiText('browser.progress.importing', '正在导入会话');
    } else if (progress.mode === 'setup') {
      copy = uiText('browser.progress.setup', '正在准备浏览器组件');
    } else if (progress.mode === 'error') {
      copy = progress.error || uiText('browser.progress.error', '连接失败，请重试。');
    }
    if (['idle','complete'].includes(progress.mode)) copy = browserReadSummary(projection) || copy;
    label.textContent = copy;
    // Poll rounds are waiting progress, not seconds or completed conversations.
    const waiting = ['setup','identity','sync','import'].includes(progress.mode);
    const percent = progress.mode === 'complete' ? 100
      : Math.min(99, Math.max(0, progress.total ? progress.attempt / progress.total * 100 : 0));
    bar.hidden = !waiting;
    bar.dataset.mode = progress.mode;
    bar.setAttribute('aria-valuetext', progress.mode === 'complete' ? '读取结束' : `等待进度 ${Math.round(percent)}%`);
    if (['setup','import'].includes(progress.mode)) bar.removeAttribute('aria-valuenow');
    else bar.setAttribute('aria-valuenow', String(Math.round(percent)));
    bar.title = waiting ? '等待进度 · 不代表已读取的会话比例' : '读取结束';
    bar.firstElementChild.style.width = ['setup','import'].includes(progress.mode) ? '35%' : `${percent}%`;
  };

  const setBrowserCompanionProgressState = (nextState) => {
    browserCompanionProgressState = {
      mode:nextState?.mode || 'idle',
      attempt:Number(nextState?.attempt) || 0,
      total:Number(nextState?.total) || 0,
      error:String(nextState?.error || '')
    };
    const details = q('#settings-browser-companion-details');
    if (details && ['setup', 'identity', 'sync', 'import', 'error'].includes(browserCompanionProgressState.mode)) {
      details.open = true;
    } else if (details && browserCompanionProgressState.mode === 'complete') {
      details.open = false;
    }
    renderBrowserCompanionProgress(browserCompanionState);
    if (browserCompanionProgressState.mode === 'error') {
      q('#settings-browser-companion-badge').textContent = uiText('browser.badge.incomplete', '未完成');
    }
  };

  const renderBrowserCompanionStatus = (projection) => {
    browserCompanionState = projection;
    const picker = q('#settings-browser-companion-browser');
    const options = Array.isArray(projection?.browser_options) ? projection.browser_options : [];
    const selected = projection?.selected_browser || '';
    if (browserCompanionDraft === selected) browserCompanionDraft = null;
    const choice = browserCompanionDraft ?? (selected || options.find(row => row.installed)?.id || '');
    const selectionChanged = Boolean(choice && choice !== selected);
    const busy = ['setup','identity','sync','import'].includes(browserCompanionProgressState.mode)
      || browserCompanionSyncState !== 'idle' || browserCompanionFullSyncState !== 'idle'
      || browserCompanionReloadState !== 'idle';
    if (picker) {
      picker.replaceChildren(...options.map(row => {
        const option = document.createElement('option');
        option.value = row.id;
        option.textContent = row.name + (row.installed ? '' : ' · 未安装');
        option.disabled = !row.installed;
        return option;
      }));
      picker.value = choice;
      picker.disabled = busy;
    }
    if (selectionChanged) projection = {...projection, configured:false, bridge_identity_verified:false,
      preflight_eligible:false, full_sync_eligible:false, prepared:false, update_available:false,
      bridge_reload_required:false, provider_sync:{}, full_sync_gate:{}, preflight_status:'NOT_RUN'};
    const configured = projection?.configured === true;
    const identityVerified = projection?.bridge_identity_verified === true;
    // Cached bridge identity does not resolve a failed or unfinished read.
    // Keep the last attempt visible until the user starts another operation.
    const preflightEligible = projection?.preflight_eligible === true;
    const prepared = projection?.prepared === true;
    const reloadRequired = projection?.bridge_reload_required === true;
    const updateAvailable = projection?.update_available === true;
    const preflightStatus = ['NOT_RUN', 'RUNNING', 'PASS', 'FAIL'].includes(projection?.preflight_status)
      ? projection.preflight_status
      : 'NOT_RUN';
    const nextSyncMode = projection?.next_sync_mode === 'INCREMENTAL' ? 'INCREMENTAL' : 'PREFLIGHT';
    const providerSync = projection?.provider_sync || {};
    const fullSyncGate = projection?.full_sync_gate || {};
    const fullSyncProviders = fullSyncGate?.providers || {};
    const fullSyncEligible = projection?.full_sync_eligible === true;
    const providerRows = (projection.providers || ['deepseek', 'gemini', 'kimi']).map((provider) => providerSync[provider] || {});
    const capturedTotal = providerRows.reduce((total, row) => total + Number(row.captured_count || 0), 0);
    const failedTotal = providerRows.reduce((total, row) => total + Number(row.failed_count || 0), 0);
    const providersComplete = providerRows.length > 0 && providerRows.every((row) => row.status === 'COMPLETE') && failedTotal === 0;
    const syncRunning = ['sync','import'].includes(browserCompanionProgressState.mode)
      || browserCompanionSyncState !== 'idle' || browserCompanionFullSyncState !== 'idle';
    const syncFailed = !syncRunning && (browserCompanionProgressState.mode === 'error' || browserReadOutcome(projection).failed.length > 0);
    const syncComplete = !syncRunning && !syncFailed && projection?.import_verified === true
      && providersComplete;
    q('#settings-browser-companion-badge').textContent = syncFailed
      ? uiText('browser.badge.incomplete', '未完成')
      : reloadRequired
      ? uiText('browser.badge.reload', '待重载桥接')
      : updateAvailable
        ? uiText('browser.badge.update', '可更新')
        : !prepared
          ? uiText('browser.badge.unconfigured', '未配置')
          : !configured
            ? (!projection?.extension_id ? uiText('browser.progress.manual-load','待加载插件') : uiText('browser.badge.prepared', '待连接'))
            : syncRunning ? uiText('browser.badge.syncing', '已连接 · 同步中')
            : providersComplete && capturedTotal === 0
              ? uiText('browser.badge.empty', '已连接 · 0 条会话')
              : syncComplete
                ? uiText('browser.badge.synced', '已同步')
                : syncRunning
                  ? uiText('browser.badge.syncing', '已连接 · 同步中')
                  : uiText('browser.badge.connected', '已连接');
    const setStage = (id, complete, copy) => {
      const stage = q(`#${id}`);
      if (!stage) return;
      const dot = stage.querySelector('.status-dot');
      if (dot) dot.className = `status-dot ${complete ? 'green' : 'yellow'}`;
      stage.dataset.state = complete ? 'complete' : 'pending';
      if (copy) stage.querySelector('small').textContent = copy;
    };
    setStage('settings-browser-companion-prepared', prepared, prepared
      ? uiText('browser.stage.prepared', '组件已准备')
      : uiText('browser.stage.wait-prepared', '等待准备组件'));
    setStage('settings-browser-companion-connected', identityVerified, identityVerified
      ? uiText('browser.stage.connected', `${projection?.connected_browser || '浏览器'} 身份已确认`, { browser: projection?.connected_browser || '浏览器' })
      : uiText('browser.stage.wait-connected', '等待桥接身份回执'));
    setStage('settings-browser-companion-synced', syncComplete, syncRunning
      ? (browserCompanionProgressState.mode === 'import' ? uiText('browser.progress.importing','正在导入会话') : uiText('browser.progress.sync-bar','正在读取网页会话'))
      : syncFailed ? uiText('browser.stage.read-failed','本次读取未完成')
      : syncComplete ? uiText('browser.stage.synced', '会话已同步')
      : uiText('browser.stage.wait-synced', '等待会话同步'));
    if(syncFailed)q('#settings-browser-companion-synced .status-dot').className='status-dot red';
    showBridgeSetupGuide(projection);
    Memorive.browserSites?.render(projection, browserCompanionSyncState !== 'idle' || browserCompanionFullSyncState !== 'idle');
    for (const provider of Memorive.browserSites && projection.site_settings ? [] : ['deepseek', 'gemini', 'kimi']) {
      const row = q(`#settings-browser-provider-${provider}`);
      if (!row) continue;
      const status = providerSync[provider] || {};
      const safety = fullSyncProviders[provider] || {};
      const captured = Number(status.captured_count || 0);
      const failed = Number(status.failed_count || 0);
      const complete = status.status === 'COMPLETE' && failed === 0;
      row.dataset.safetyState = safety.status || 'PREFLIGHT_REQUIRED';
      if (safety.status === 'HUMAN_VERIFICATION_BLOCKED') {
        row.dataset.state = 'error';
        row.querySelector('small').textContent = uiText('browser.provider.safe.human', '请在浏览器完成人机验证。');
      } else if (safety.status === 'PREFLIGHT_FAILED') {
        row.dataset.state = 'error';
        row.querySelector('small').textContent = uiText('browser.provider.safe.failed', `试读失败 ${Number(safety.failed_count || 0)} 条`, { count: Number(safety.failed_count || 0) });
      } else if (safety.status === 'PREFLIGHT_PASS') {
        row.dataset.state = 'complete';
        row.querySelector('small').textContent = uiText('browser.provider.safe.pass', `已读取 ${Number(safety.captured_count || 0)} 条`, { count: Number(safety.captured_count || 0) });
      } else if (safety.status === 'NO_CAPTURED_CONVERSATIONS') {
        row.dataset.state = 'pending';
        row.querySelector('small').textContent = uiText('browser.provider.safe.empty', '未读取到会话');
      } else {
        row.dataset.state = failed ? 'error' : complete ? 'complete' : 'pending';
        row.querySelector('small').textContent = identityVerified
          ? uiText('browser.provider.safe.required', '等待已启用网站试读')
          : uiText('browser.provider.wait-connect', '等待身份确认');
      }
    }
    const reloadButton = q('#settings-browser-companion-reload');
    if (reloadButton) {
      const reloadBusy = browserCompanionReloadState !== 'idle';
      reloadButton.disabled = !prepared || updateAvailable || busy || reloadBusy;
      reloadButton.setAttribute('aria-disabled', String(reloadButton.disabled));
      reloadButton.setAttribute('aria-busy', String(browserCompanionReloadState === 'starting'));
    }
    const setupButton = q('#settings-browser-companion-setup');
    if (setupButton) {
      const setupBusy = browserCompanionSetupInFlight || browserCompanionProgressState.mode === 'setup';
      const alreadyConfigured = configured && !reloadRequired && !updateAvailable;
      setupButton.disabled = (busy && browserCompanionProgressState.mode !== 'identity') || setupBusy || alreadyConfigured || !options.some(row => row.id === choice && row.installed);
      setupButton.setAttribute('aria-disabled', String(setupButton.disabled));
      setupButton.setAttribute('aria-busy', String(setupBusy));
      setupButton.textContent = alreadyConfigured
        ? uiText('browser.setup.current', '浏览器已连接')
        : updateAvailable
          ? uiText('browser.setup.update', '更新浏览器扩展')
          : prepared
            ? uiText('browser.setup.continue', '继续连接')
            : uiText('browser.setup', '连接浏览器');
    }
    const preflightButton = q('#settings-browser-companion-sync');
    if (preflightButton) {
      const preflightBusy = browserCompanionSyncState !== 'idle' || browserCompanionFullSyncState !== 'idle';
      preflightButton.disabled = !preflightEligible || ((projection?.incremental_sync_eligible !== true || syncFailed) && root.querySelector('.desktop-settings-shell')?.dataset.settingsMode!=='normal') || preflightBusy;
      preflightButton.setAttribute('aria-disabled', String(preflightButton.disabled));
      if (!preflightBusy) {
        preflightButton.textContent = root.querySelector('.desktop-settings-shell')?.dataset.settingsMode==='normal'?'同步会话':'增量同步';
      }
    }
    const fullButton = q('#settings-browser-companion-full-sync');
    const testButton = q('#settings-browser-companion-test');
    if (testButton) testButton.disabled = !preflightEligible || browserCompanionSyncState !== 'idle' || browserCompanionFullSyncState !== 'idle';
    if (fullButton) {
      const fullBusy = browserCompanionFullSyncState !== 'idle' || browserCompanionSyncState !== 'idle';
      fullButton.disabled = !fullSyncEligible || fullBusy || syncFailed;
      fullButton.setAttribute('aria-disabled', String(!fullSyncEligible || fullBusy || syncFailed));
    }
    const gateRows = Object.values(fullSyncProviders);
    const humanVerification = gateRows.some((row) => row?.human_verification_detected === true);
    const gateDot = q('#settings-browser-full-gate-dot');
    const gateStatus = q('#settings-browser-full-gate-status');
    if (gateDot) gateDot.className = `status-dot ${syncFailed ? 'red' : syncRunning ? 'yellow' : fullSyncEligible ? 'green' : humanVerification ? 'red' : 'yellow'}`;
    if (gateStatus) gateStatus.textContent = syncFailed ? uiText('browser.stage.read-failed','本次读取未完成')
      : syncRunning ? (browserCompanionProgressState.mode === 'import' ? uiText('browser.progress.importing','正在导入会话') : uiText('browser.progress.sync-bar','正在读取网页会话'))
      : fullSyncEligible
      ? uiText('browser.gate.ready', '可以完整同步')
      : humanVerification
        ? uiText('browser.gate.human', '请在浏览器完成人机验证。')
        : preflightStatus === 'FAIL'
          ? uiText('browser.gate.pending', '试同步未完成')
          : uiText('browser.gate.required', '请先完成试同步');
    q('.desktop-settings-browser-stage-grid').hidden = identityVerified;
    if (gateStatus) gateStatus.parentElement.hidden = identityVerified;
    renderBrowserCompanionProgress(projection);
    return projection;
  };

  q('#settings-browser-companion-browser')?.addEventListener('change', event => {
    browserCompanionDraft = event.target.value;
    if (browserCompanionState) renderBrowserCompanionStatus(browserCompanionState);
  });


  const showBridgeSetupGuide = (status) => {
    const tools=q('#memo-bridge-setup-guide');
    if(!tools)return;
    tools.hidden=!status?.prepared || status?.bridge_identity_verified===true;
    q('#memo-bridge-folder').value=status?.extension_path || '';
  };
  q('#memo-bridge-copy-folder')?.addEventListener('click', async () => {
    const value=q('#memo-bridge-folder')?.value;
    if(!value)return;
    try { await navigator.clipboard.writeText(value); }
    catch (_) {
      const focus=document.activeElement,input=document.createElement('textarea');
      input.value=value;
      input.style.cssText='position:fixed;opacity:0;pointer-events:none';
      document.body.append(input);
      input.select();
      let copied=false;
      try { copied=document.execCommand('copy'); } finally {
        input.remove();
        focus?.focus();
      }
      if(!copied){
        showToast('复制失败，请打开文件夹');
        return;
      }
    }
    showToast('路径已复制');
  });
  q('#memo-bridge-open-folder')?.addEventListener('click',()=>void nativeAction('open_browser_companion_folder').catch(()=>showToast('文件夹无法打开，请复制路径手动打开')));
  q('#memo-bridge-check')?.addEventListener('click',async()=>{
    const button=q('#memo-bridge-check');
    button.disabled=true;
    try {
      const status=renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
      showBridgeSetupGuide(status);
      showToast(status?.bridge_identity_verified ? '浏览器已连接' : '等待插件连接');
    } catch (_) { showToast('连接检查失败，请重试'); }
    finally { button.disabled=false; }
  });

  window.addEventListener('focus', () => {
    if(browserCompanionProgressState.mode !== 'manual')return;
    void nativeAction('browser_companion_status').then(status=>{
      if(status?.bridge_identity_verified)setBrowserCompanionProgressState({mode:'complete'});
      renderBrowserCompanionStatus(status);
    }).catch(()=>{});
  });

  const prepareBrowserCompanion = async () => {
    if(Memorive.browserSites?.isDirty()){
      showToast('请先保存网页设置');
      return {status:'UNSAVED'};
    }
    if (browserCompanionSetupInFlight) return {status:'PREPARING'};
    // Waiting for the extension must not lock the user out of reopening setup.
    // Reuse the existing identity poll instead of starting another five-minute poll.
    if (browserCompanionProgressState.mode === 'identity') {
      browserCompanionSetupInFlight = true;
      if(browserCompanionState)renderBrowserCompanionStatus(browserCompanionState);
      try {
        const receipt=await nativeAction('install_browser_companion',q('#settings-browser-companion-browser')?.value || null);
        showToast(receipt.browser_manager_opened ? '已打开插件配置' : receipt.bridge_reload_requested ? '正在重新连接' : '等待插件连接');
        return receipt;
      } finally {
        browserCompanionSetupInFlight = false;
        if(browserCompanionState)renderBrowserCompanionStatus(browserCompanionState);
      }
    }
    setBrowserCompanionProgressState({ mode:'setup' });
    let receipt;
    let status;
    try {
      receipt = await nativeAction('install_browser_companion', q('#settings-browser-companion-browser')?.value || null);
      browserCompanionDraft = null;
      status = renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
    } catch (error) {
      const message = /SELECTED_BROWSER_NOT_FOUND|SUPPORTED_BROWSER_NOT_FOUND/.test(String(error)) ? '未找到所选浏览器，请检查安装位置或重新选择。' : uiText('browser.error.disconnected', '浏览器桥接已断连，请确认扩展已启用后重试。');
      setBrowserCompanionProgressState({ mode:'error', error:message });
      throw error;
    }
    if (receipt?.status === 'BROWSER_NO_VERIFIED_OFFICIAL_SITES') {
      const error = '请先启用已核实的模型官网';
      setBrowserCompanionProgressState({ mode:'error', error });
      showToast(error);
      return receipt;
    }
    if (receipt?.status === 'ALREADY_CONFIGURED') {
      setBrowserCompanionProgressState({ mode:'complete' });
      showToast(uiText('browser.toast.current', '已配置'));
      return receipt;
    }
    showBridgeSetupGuide(status);
    if (receipt.manual_load_required && !status?.bridge_identity_verified) {
      setBrowserCompanionProgressState({mode:'manual'});
      renderBrowserCompanionStatus(status);
      showToast(uiText('browser.toast.load-plugin','请在扩展页加载插件文件夹'));
      return receipt;
    }
    const reloadTriggered = ['BRIDGE_RELOAD_TRIGGERED', 'BRIDGE_RELOAD_READY'].includes(receipt.status);
    showToast(reloadTriggered
      ? uiText('browser.toast.reload', '正在重新连接')
      : receipt.manual_load_required
        ? uiText('browser.toast.manual', '已打开扩展管理页和组件文件夹；加载后将验证桥接身份。')
        : uiText('browser.toast.prepared', '已准备组件文件夹；请在所选浏览器的扩展页加载一次。'));
    if (reloadTriggered) setBrowserCompanionReloadState('triggered');
    setBrowserCompanionProgressState({ mode:'identity', attempt:0, total:300 });
    void pollBrowserCompanionIdentity({ attempts: 300 }).then((status) => {
      if (status?.bridge_identity_verified === true) {
        setBrowserCompanionProgressState({ mode:'complete' });
        showToast(uiText('browser.toast.identity', `只读桥接 ${status.extension_version || '3.6.1'} 已连接`));
      } else {
        const error = '等待插件连接；可继续配置或检查连接。';
        setBrowserCompanionProgressState({ mode:'error', error });
      }
    }).catch(() => {
      setBrowserCompanionProgressState({ mode:'error', error:uiText('browser.error.disconnected', '浏览器桥接已断连，请确认扩展已启用后重试。') });
    }).finally(() => {
      setBrowserCompanionReloadState('idle');
      if (browserCompanionState) renderBrowserCompanionStatus(browserCompanionState);
    });
    return receipt;
  };

  const wait = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  const setBrowserCompanionReloadState = (nextState) => {
    browserCompanionReloadState = nextState;
    const button = q('#settings-browser-companion-reload');
    if (!button) return;
    const starting = nextState === 'starting';
    const triggered = nextState === 'triggered';
    button.disabled = starting || triggered || browserCompanionSyncState !== 'idle' || browserCompanionFullSyncState !== 'idle';
    button.setAttribute('aria-busy', String(starting));
    button.setAttribute('aria-disabled', String(button.disabled));
    button.textContent = starting
      ? uiText('browser.reload.starting', '正在重新连接')
      : triggered
        ? uiText('browser.reload.triggered', '只读桥接已触发重载')
        : uiText('browser.reload', '重新连接');
  };
  const setBrowserCompanionSyncState = (nextState, syncMode = null) => {
    browserCompanionSyncState = nextState;
    browserCompanionActiveSyncMode = nextState === 'idle'
      ? null
      : (syncMode || browserCompanionActiveSyncMode || browserCompanionState?.next_sync_mode || 'PREFLIGHT');
    const busy = nextState !== 'idle' || browserCompanionFullSyncState !== 'idle';
    for (const [id, mode, label] of [
      ['settings-browser-companion-test','PREFLIGHT','测试读取'],
      ['settings-browser-companion-sync','INCREMENTAL','增量同步']
    ]) {
      const button=q('#'+id);
      if (!button) continue;
      const eligible=browserCompanionState?.preflight_eligible === true &&
        (mode==='PREFLIGHT' || browserCompanionState?.incremental_sync_eligible===true || root.querySelector('.desktop-settings-shell')?.dataset.settingsMode==='normal');
      button.disabled=busy || !eligible;
      button.setAttribute('aria-disabled',String(button.disabled));
      const active=nextState!=='idle' && browserCompanionActiveSyncMode===mode;
      button.setAttribute('aria-busy',String(active));
      button.textContent=active ? (nextState==='starting' ? '启动中…' : '读取中…') : label;
    }
  };
  const setBrowserCompanionFullSyncState = (nextState) => {
    browserCompanionFullSyncState = nextState;
    const button = q('#settings-browser-companion-full-sync');
    if (!button) return;
    const starting = nextState === 'starting';
    const triggered = nextState === 'triggered';
    button.disabled = starting || triggered || browserCompanionState?.full_sync_eligible !== true;
    button.setAttribute('aria-busy', String(starting));
    button.setAttribute('aria-disabled', String(button.disabled));
    button.textContent = starting
      ? uiText('browser.full.starting', '正在启动完整同步')
      : triggered
        ? uiText('browser.full.triggered', '完整同步已触发')
        : uiText('browser.full', '完整同步');
  };
  const pollBrowserCompanion = async ({ attempts = 1, requestSync = false, expectedMode = null, previousReportSha = null } = {}) => {
    if (requestSync) await nativeAction('run_browser_companion_sync');
    let latest = null;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      setBrowserCompanionProgressState({ mode:'sync', attempt:attempt + 1, total:attempts });
      latest = renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
      const latestReportSha = latest?.latest_sync_report_sha256 || null;
      const expectedReportArrived = Boolean(
        expectedMode
        && latest?.latest_sync_mode === expectedMode
        && latestReportSha
        && latestReportSha !== previousReportSha
      );
      if (expectedMode ? expectedReportArrived : (latest?.configured && latest?.import_verified)) {
        setBrowserCompanionProgressState({mode:'import'});
        let imported;
        try { imported = await nativeAction('sync_browser_sessions_from_downloads'); }
        catch (error) {
          setBrowserCompanionProgressState({mode:'error',error:uiText('browser.progress.import-failed','会话导入失败 · 可重试')});
          return latest;
        }
        latest = renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
        if (Number(imported?.failure_count || 0) > 0 || !latest?.import_verified) {
          setBrowserCompanionProgressState({mode:'error',error:uiText('browser.progress.import-failed','会话导入失败 · 可重试')});
          return latest;
        }
        const failed = browserReadOutcome(latest).failed.length > 0;
        setBrowserCompanionProgressState(failed
          ? { mode:'error', error:browserReadSummary(latest) }
          : { mode:'complete' });
        return latest;
      }
      if (attempt + 1 < attempts) await wait(1500);
    }
    if (browserCompanionProgressState.mode !== 'complete') {
      setBrowserCompanionProgressState({ mode:'error', error:uiText('browser.progress.no-receipt','未收到本次读取结果 · 可重试') });
    }
    return latest;
  };

  const pollBrowserCompanionIdentity = async ({ attempts = 1 } = {}) => {
    let latest = null;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      setBrowserCompanionProgressState({ mode:'identity', attempt:attempt + 1, total:attempts });
      latest = renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
      if (latest?.bridge_identity_verified === true) {
        setBrowserCompanionProgressState({ mode:'complete' });
        break;
      }
      if (attempt + 1 < attempts) await wait(1000);
    }
    return latest;
  };

  const reloadBrowserCompanion = async () => {
    if (browserCompanionReloadState !== 'idle') return browserCompanionState;
    setBrowserCompanionReloadState('starting');
    try {
      const launch = await nativeAction('run_browser_companion_reload');
      if (!['BRIDGE_RELOAD_TRIGGERED', 'BRIDGE_RELOAD_READY'].includes(launch?.status)) {
        return renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
      }
      setBrowserCompanionReloadState('triggered');
      showToast(uiText('browser.toast.reload', '正在重新连接'));
      const status = await pollBrowserCompanionIdentity({ attempts: 300 });
      if (status?.bridge_identity_verified === true) {
        const version = status.extension_version || '3.6.1';
        showToast(uiText('browser.toast.identity', `只读桥接 ${version} 身份已确认，可以开始网站试读。`, { version }));
      } else {
        const error = '等待插件连接；可继续配置或检查连接。';
        setBrowserCompanionProgressState({ mode:'error', error });
      }
      return status;
    } catch (error) {
      setBrowserCompanionProgressState({ mode:'error', error:uiText('browser.error.disconnected', '浏览器桥接已断连，请确认扩展已启用后重试。') });
      throw error;
    } finally {
      setBrowserCompanionReloadState('idle');
      if (browserCompanionState) renderBrowserCompanionStatus(browserCompanionState);
    }
  };

  const syncBrowserCompanion = async (explicitMode = 'INCREMENTAL') => {
    if (browserCompanionSyncState !== 'idle' || browserCompanionFullSyncState !== 'idle') return browserCompanionState;
    if (browserCompanionState?.preflight_eligible !== true) {
      showToast(uiText('browser.toast.identity-required', '请先确认当前只读桥接身份，再开始网站试读。'));
      return browserCompanionState;
    }
    if(Memorive.browserSites?.isDirty()){
      showToast('请先保存网页设置');
      return browserCompanionState;
    }
    const requestedMode = explicitMode==='AUTO' ? (browserCompanionState?.incremental_sync_eligible && !browserReadOutcome(browserCompanionState).failed.length ? 'INCREMENTAL' : 'PREFLIGHT') : explicitMode;
    if (requestedMode === 'INCREMENTAL' && browserCompanionState?.incremental_sync_eligible !== true) {
      showToast('请先测试读取');
      return browserCompanionState;
    }
    setBrowserCompanionSyncState('starting', requestedMode);
    renderBrowserCompanionStatus(browserCompanionState);
    try {
      const previousReportSha = (requestedMode === 'PREFLIGHT' ? browserCompanionState?.preflight_report_sha256 : browserCompanionState?.latest_sync_report_sha256) || null;
      const launch = requestedMode === 'PREFLIGHT' ? await nativeAction('run_browser_companion_test_read') : await nativeAction('run_browser_companion_sync','INCREMENTAL');
      if (!['SYNC_TRIGGERED', 'SYNC_READY', 'INCREMENTAL_SYNC_TRIGGERED', 'INCREMENTAL_SYNC_READY'].includes(launch?.status)) {
        const status = renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
        showToast(uiText('browser.toast.identity-required', '请先确认当前只读桥接身份，再开始网站试读。'));
        return status;
      }
      const launchedMode = launch?.sync_mode === 'INCREMENTAL' ? 'INCREMENTAL' : 'PREFLIGHT';
      setBrowserCompanionSyncState('triggered', launchedMode);
      showToast(launchedMode === 'INCREMENTAL'
        ? uiText('browser.toast.incremental-triggered', '网站增量同步已触发，每站最多读取 10 条正文。')
        : uiText('browser.toast.triggered', '已启用网站试读已触发，每站最多 2 条。'));
      const status = await pollBrowserCompanion({ attempts: ({PREFLIGHT:820, INCREMENTAL:1220, FULL:3620}[launchedMode] || 820), requestSync: false, expectedMode: launchedMode, previousReportSha });
      if (browserCompanionProgressState.mode === 'error') return status;
      if (launchedMode === 'PREFLIGHT' && status?.full_sync_eligible) {
        showToast(uiText('browser.toast.preflight-ready', '已启用网站安全通过，现可手动启动完整同步。'));
      } else if (status?.import_verified) {
        showToast(uiText('browser.toast.synced', '浏览器会话库已验证并同步。'));
      }
      return status;
    } catch (error) {
      setBrowserCompanionProgressState({ mode:'error', error:uiText('browser.error.disconnected', '浏览器桥接已断连，请确认扩展已启用后重试。') });
      throw error;
    } finally {
      setBrowserCompanionSyncState('idle');
      if (browserCompanionState) renderBrowserCompanionStatus(browserCompanionState);
    }
  };

  const syncBrowserCompanionFull = async () => {
    if (browserCompanionFullSyncState !== 'idle' || browserCompanionSyncState !== 'idle') return browserCompanionState;
    if (browserCompanionState?.full_sync_eligible !== true) {
      showToast(uiText('browser.toast.full-blocked', '最近一次已启用网站试读尚未全部通过，完整同步保持禁用。'));
      return browserCompanionState;
    }
    setBrowserCompanionFullSyncState('starting');
    renderBrowserCompanionStatus(browserCompanionState);
    try {
      const previousReportSha = browserCompanionState?.latest_sync_report_sha256 || null;
      const launch = await nativeAction('run_browser_companion_full_sync');
      if (!['FULL_SYNC_TRIGGERED', 'FULL_SYNC_READY'].includes(launch?.status)) {
        const status = renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
        showToast(uiText('browser.toast.full-blocked', '最近一次已启用网站试读尚未全部通过，完整同步保持禁用。'));
        return status;
      }
      setBrowserCompanionFullSyncState('triggered');
      showToast(uiText('browser.toast.full-triggered', '完整同步已触发，正在等待后台会话包。'));
      return await pollBrowserCompanion({ attempts: 3620, requestSync: false, expectedMode: 'FULL', previousReportSha });
    } catch (error) {
      setBrowserCompanionProgressState({ mode:'error', error:uiText('browser.error.disconnected', '浏览器桥接已断连，请确认扩展已启用后重试。') });
      throw error;
    } finally {
      setBrowserCompanionFullSyncState('idle');
      if (browserCompanionState) renderBrowserCompanionStatus(browserCompanionState);
    }
  };

  Memorive.browserCompanion = Object.freeze({
    render: renderBrowserCompanionStatus,
    rerender: () => { if (browserCompanionState) renderBrowserCompanionStatus(browserCompanionState); },
    prepare: prepareBrowserCompanion,
    reload: reloadBrowserCompanion,
    sync: syncBrowserCompanion,
    syncFull: syncBrowserCompanionFull,
    reloadState: () => browserCompanionReloadState,
    fullSyncState: () => browserCompanionFullSyncState,
  });
})();
