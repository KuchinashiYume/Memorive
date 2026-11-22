/* The browser companion on the settings page: the extension that records web chat pages into the sessions page.
   Shows whether it is prepared and connected, prepares or updates it and helps load it in the browser
   (browser_companion_status, install_browser_companion, open_browser_companion_folder).
   Publishes Memorive.browserCompanion (render, rerender, prepare), which routes/settings/settings.js uses.
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
  let browserCompanionSetupInFlight = false;
  // setup: preparing the folder; manual: waiting for the user to load it; identity: waiting for the extension's
  // version receipt; complete / error: the last attempt's end.
  let progressState = { mode:'idle', attempt:0, total:0, error:'' };

  const renderProgress = (projection) => {
    const label = q('#settings-browser-progress-label');
    const bar = q('#settings-browser-read-progress');
    if (!label || !bar) return;
    const prepared = projection?.prepared === true;
    const connected = projection?.bridge_identity_verified === true;
    let copy = connected ? uiText('browser.stage.connected-short', '浏览器已连接')
      : prepared ? uiText('browser.stage.prepared', '组件已准备')
      : uiText('browser.progress.not-started', '未开始');
    if (prepared && !connected && !projection?.extension_id && ['idle', 'manual'].includes(progressState.mode)) {
      copy = uiText('browser.progress.manual-load', '待加载插件');
    } else if (progressState.mode === 'identity' && !connected) {
      copy = uiText('browser.progress.identity', '等待浏览器连接');
    } else if (progressState.mode === 'setup') {
      copy = uiText('browser.progress.setup', '正在准备浏览器组件');
    } else if (progressState.mode === 'error') {
      copy = progressState.error || uiText('browser.progress.error', '连接失败，请重试。');
    }
    label.textContent = copy;
    // Poll rounds are waiting progress, not seconds.
    const waiting = ['setup', 'identity'].includes(progressState.mode);
    const percent = Math.min(99, Math.max(0, progressState.total ? progressState.attempt / progressState.total * 100 : 0));
    bar.hidden = !waiting;
    bar.dataset.mode = progressState.mode;
    bar.setAttribute('aria-valuetext', `等待进度 ${Math.round(percent)}%`);
    if (progressState.mode === 'setup') bar.removeAttribute('aria-valuenow');
    else bar.setAttribute('aria-valuenow', String(Math.round(percent)));
    bar.title = '等待进度';
    bar.firstElementChild.style.width = progressState.mode === 'setup' ? '35%' : `${percent}%`;
  };

  const setProgressState = (nextState) => {
    progressState = {
      mode:nextState?.mode || 'idle',
      attempt:Number(nextState?.attempt) || 0,
      total:Number(nextState?.total) || 0,
      error:String(nextState?.error || '')
    };
    const details = q('#settings-browser-companion-details');
    if (details && ['setup', 'identity', 'error'].includes(progressState.mode)) details.open = true;
    else if (details && progressState.mode === 'complete') details.open = false;
    renderProgress(browserCompanionState);
    if (progressState.mode === 'error') {
      q('#settings-browser-companion-badge').textContent = uiText('browser.badge.incomplete', '未完成');
    }
  };

  // The extension's folder, shown while it is prepared but not yet loaded in the browser.
  const showBridgeSetupGuide = (status) => {
    const tools = q('#memo-bridge-setup-guide');
    if (!tools) return;
    tools.hidden = !status?.prepared || status?.bridge_identity_verified === true;
    q('#memo-bridge-folder').value = status?.extension_path || '';
  };

  const renderBrowserCompanionStatus = (projection) => {
    browserCompanionState = projection;
    const picker = q('#settings-browser-companion-browser');
    const options = Array.isArray(projection?.browser_options) ? projection.browser_options : [];
    const selected = projection?.selected_browser || '';
    if (browserCompanionDraft === selected) browserCompanionDraft = null;
    const choice = browserCompanionDraft ?? (selected || options.find(row => row.installed)?.id || '');
    const busy = ['setup', 'identity'].includes(progressState.mode);
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
    // Another browser picked but not yet set up: show it as not prepared.
    if (choice && choice !== selected) {
      projection = { ...projection, configured:false, bridge_identity_verified:false, prepared:false, update_available:false };
    }
    const configured = projection?.configured === true;
    const identityVerified = projection?.bridge_identity_verified === true;
    const prepared = projection?.prepared === true;
    const updateAvailable = projection?.update_available === true;
    q('#settings-browser-companion-badge').textContent = progressState.mode === 'error'
      ? uiText('browser.badge.incomplete', '未完成')
      : updateAvailable
        ? uiText('browser.badge.update', '可更新')
        : !prepared
          ? uiText('browser.badge.unconfigured', '未配置')
          : !configured
            ? (!projection?.extension_id ? uiText('browser.progress.manual-load', '待加载插件') : uiText('browser.badge.prepared', '待连接'))
            : uiText('browser.badge.connected', '已连接');
    const setStage = (id, complete, copy) => {
      const stage = q(`#${id}`);
      if (!stage) return;
      const dot = stage.querySelector('.status-dot');
      if (dot) dot.className = `status-dot ${complete ? 'green' : 'yellow'}`;
      stage.dataset.state = complete ? 'complete' : 'pending';
      stage.querySelector('small').textContent = copy;
    };
    setStage('settings-browser-companion-prepared', prepared, prepared
      ? uiText('browser.stage.prepared', '组件已准备')
      : uiText('browser.stage.wait-prepared', '等待准备组件'));
    setStage('settings-browser-companion-connected', identityVerified, identityVerified
      ? uiText('browser.stage.connected', `${projection?.connected_browser || '浏览器'} 已连接`, { browser: projection?.connected_browser || '浏览器' })
      : uiText('browser.stage.wait-connected', '等待插件连接'));
    showBridgeSetupGuide(projection);
    const setupButton = q('#settings-browser-companion-setup');
    if (setupButton) {
      const setupBusy = browserCompanionSetupInFlight || progressState.mode === 'setup';
      const alreadyConfigured = configured && !updateAvailable;
      setupButton.disabled = setupBusy || alreadyConfigured || !options.some(row => row.id === choice && row.installed);
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
    q('.desktop-settings-browser-stage-grid').hidden = identityVerified;
    renderProgress(projection);
    return projection;
  };

  q('#settings-browser-companion-browser')?.addEventListener('change', event => {
    browserCompanionDraft = event.target.value;
    if (browserCompanionState) renderBrowserCompanionStatus(browserCompanionState);
  });
  q('#memo-bridge-copy-folder')?.addEventListener('click', async () => {
    const value = q('#memo-bridge-folder')?.value;
    if (!value) return;
    try { await navigator.clipboard.writeText(value); }
    catch (_) {
      const focus = document.activeElement, input = document.createElement('textarea');
      input.value = value;
      input.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
      document.body.append(input);
      input.select();
      let copied = false;
      try { copied = document.execCommand('copy'); } finally {
        input.remove();
        focus?.focus();
      }
      if (!copied) {
        showToast('复制失败，请打开文件夹');
        return;
      }
    }
    showToast('路径已复制');
  });
  q('#memo-bridge-open-folder')?.addEventListener('click', () => void nativeAction('open_browser_companion_folder').catch(() => showToast('文件夹无法打开，请复制路径手动打开')));
  q('#memo-bridge-check')?.addEventListener('click', async () => {
    const button = q('#memo-bridge-check');
    button.disabled = true;
    try {
      const status = renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
      showToast(status?.bridge_identity_verified ? '浏览器已连接' : '等待插件连接');
    } catch (_) { showToast('连接检查失败，请重试'); }
    finally { button.disabled = false; }
  });
  // Back from the browser's extensions page: check whether the extension has been loaded.
  window.addEventListener('focus', () => {
    if (progressState.mode !== 'manual') return;
    void nativeAction('browser_companion_status').then(status => {
      if (status?.bridge_identity_verified) setProgressState({ mode:'complete' });
      renderBrowserCompanionStatus(status);
    }).catch(() => {});
  });

  const wait = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  // The extension writes a version receipt when it starts; wait for the one of the prepared version.
  const pollIdentity = async ({ attempts = 1 } = {}) => {
    let latest = null;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      setProgressState({ mode:'identity', attempt:attempt + 1, total:attempts });
      latest = renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
      if (latest?.bridge_identity_verified === true) {
        setProgressState({ mode:'complete' });
        break;
      }
      if (attempt + 1 < attempts) await wait(1000);
    }
    return latest;
  };

  // Prepare or update the extension's folder for the chosen browser, then wait until the browser has loaded it.
  const prepareBrowserCompanion = async () => {
    if (browserCompanionSetupInFlight) return { status:'PREPARING' };
    const updating = browserCompanionState?.update_available === true;
    // Reopening setup while waiting reuses the running wait instead of starting another one.
    if (progressState.mode === 'identity') {
      browserCompanionSetupInFlight = true;
      if (browserCompanionState) renderBrowserCompanionStatus(browserCompanionState);
      try {
        const receipt = await nativeAction('install_browser_companion', q('#settings-browser-companion-browser')?.value || null);
        showToast(receipt.browser_manager_opened ? '已打开插件配置' : '等待插件连接');
        return receipt;
      } finally {
        browserCompanionSetupInFlight = false;
        if (browserCompanionState) renderBrowserCompanionStatus(browserCompanionState);
      }
    }
    setProgressState({ mode:'setup' });
    let receipt;
    let status;
    try {
      receipt = await nativeAction('install_browser_companion', q('#settings-browser-companion-browser')?.value || null);
      browserCompanionDraft = null;
      status = renderBrowserCompanionStatus(await nativeAction('browser_companion_status'));
    } catch (error) {
      const message = /SELECTED_BROWSER_NOT_FOUND|SUPPORTED_BROWSER_NOT_FOUND/.test(String(error))
        ? '未找到所选浏览器，请检查安装位置或重新选择。'
        : uiText('browser.error.disconnected', '浏览器插件未连接，请确认扩展已启用后重试。');
      setProgressState({ mode:'error', error:message });
      throw error;
    }
    if (receipt?.status === 'ALREADY_CONFIGURED') {
      setProgressState({ mode:'complete' });
      showToast(uiText('browser.toast.current', '已配置'));
      return receipt;
    }
    showBridgeSetupGuide(status);
    if (receipt.manual_load_required && !status?.bridge_identity_verified) {
      setProgressState({ mode:'manual' });
      renderBrowserCompanionStatus(status);
      showToast(uiText('browser.toast.load-plugin', '请在扩展页加载插件文件夹'));
      return receipt;
    }
    // An update (here, or copied in when Memorive started) needs a reload on the extensions page, not a new load.
    showToast(updating || receipt?.status === 'AWAITING_EXTENSION_RELOAD'
      ? uiText('browser.toast.updated', '插件文件夹已更新；请在扩展页重新加载插件。')
      : uiText('browser.toast.prepared', '已准备组件文件夹；请在所选浏览器的扩展页加载一次。'));
    void pollIdentity({ attempts: 300 }).then((latest) => {
      if (latest?.bridge_identity_verified === true) {
        showToast(uiText('browser.toast.identity', `网页录入插件 ${latest.extension_version || ''} 已连接`, { version: latest.extension_version || '' }));
      } else {
        setProgressState({ mode:'error', error:'等待插件连接；可继续配置或检查连接。' });
      }
    }).catch(() => {
      setProgressState({ mode:'error', error:uiText('browser.error.disconnected', '浏览器插件未连接，请确认扩展已启用后重试。') });
    }).finally(() => {
      if (browserCompanionState) renderBrowserCompanionStatus(browserCompanionState);
    });
    return receipt;
  };

  Memorive.browserCompanion = Object.freeze({
    render: renderBrowserCompanionStatus,
    rerender: () => { if (browserCompanionState) renderBrowserCompanionStatus(browserCompanionState); },
    prepare: prepareBrowserCompanion,
  });
})();
