(() => {
  'use strict';

  const root = document.getElementById('desktop-sessions-preview');
  if (!root || !window.DesktopPreview_sessions) return;
  const q = (selector) => root.querySelector(selector);
  const qa = (selector) => [...root.querySelectorAll(selector)];
  const setRefineState = (message = '', { show = true, title = '' } = {}) => {
    const state = q('#sessions-refine-state');
    state.textContent = message;
    state.title = title;
    state.hidden = !show || !message;
  };
  const stage = q('#sessions-stage');
  const listPane = q('#sessions-listpane');
  const list = q('#sessions-list');
  const detail = q('#sessions-detail');
  const detailResizer = q('#sessions-detail-resizer');
  const emptyState = q('#sessions-empty');
  const recordsById = new Map();
  let selectedCard = null;
  let selectedProjectionId = null;
  let batch = false;
  let detailSize = null;
  let detailSizeCustomized = false;
  let detailResizeState = null;
  let detailTransitionTimer = 0;
  let detailTransitionVersion = 0;
  let searchTimer = 0;
  let toastTimer = 0;
  let toastFadeTimer = 0;
  let toastVersion = 0;
  let batchConfirmTimer = 0;
  let batchPendingIds = [];
  let deleteConfirmTimer = 0;
  let refinementModelConfigured = false;
  let refinementProfileExecutionEligible = false;
  let refinementProfileStartEligible = false;
  let refinementProfileKind = '';
  let refinementProfileExecutionStatus = 'MODEL_NOT_CONFIGURED';
  let refinementExecutionReady = false;
  let selectedRefinementEligible = false;
  let activeRefinementJobId = null;
  const ALL_SOURCE_PAGE_SIZE = 30;
  const ALL_SOURCE_PREFETCH_ROW = 20;
  let pageTriggerObserver = null;
  let pageTriggerElement = null;
  let pageLoadRequested = false;
  let pageTriggerOptions = null;
  const REFINEMENT_RETRY_COUNTDOWN_SECONDS = 3;
  let refinementRetryTimer = 0;
  let refinementRetryProjectionId = null;
  const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)') || { matches:false };

  const cards = () => qa('.desktop-sessions-card');
  const visibleCards = () => cards().filter((card) => !card.hidden);
  const clampNumber = (value, min, max) => Math.min(max, Math.max(min, value));

  const currentFilter = () => ({
    source: q('#sessions-source').value || 'all',
    search: q('#sessions-search').value || '',
    limit_per_source: Number(q('#sessions-limit').value || 30)
  });

  const clearPageTrigger = () => {
    pageTriggerObserver?.disconnect();
    pageTriggerObserver = null;
    if (pageTriggerElement) delete pageTriggerElement.dataset.pageTrigger;
    pageTriggerElement = null;
  };
  const requestNextPage = () => {
    if (pageLoadRequested) return;
    const bridge = window.__Desktop_SESSIONS_BRIDGE__;
    if (!bridge?.loadNextPage) return;
    const retryOptions = pageTriggerOptions ? { ...pageTriggerOptions } : null;
    pageLoadRequested = true;
    clearPageTrigger();
    void bridge.loadNextPage().catch((error) => {
      showToast(`继续加载会话失败：${String(error?.message || error)}`);
      if (retryOptions) window.setTimeout(() => armPageTrigger(retryOptions), 0);
    }).finally(() => {
      pageLoadRequested = false;
      window.requestAnimationFrame(maybeRequestNextPage);
    });
  };
  const armPageTrigger = (options = {}) => {
    clearPageTrigger();
    pageTriggerOptions = { ...options };
    if (currentFilter().source !== 'all' || options.hasMore !== true) return;
    const pageOffset = Number(options.pageOffset) || 0;
    const triggerIndex = pageOffset + ALL_SOURCE_PREFETCH_ROW - 1;
    const loadedCards = cards();
    const trigger = loadedCards[triggerIndex];
    if (!trigger) return;
    pageTriggerElement = trigger;
    trigger.dataset.pageTrigger = 'next';
    if ('IntersectionObserver' in window) {
      pageTriggerObserver = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) requestNextPage();
      }, { root:listPane, threshold:.01 });
      pageTriggerObserver.observe(trigger);
    }
  };
  const maybeRequestNextPage = () => {
    if (!pageTriggerElement || pageLoadRequested) return;
    const triggerBox = pageTriggerElement.getBoundingClientRect();
    const paneBox = listPane.getBoundingClientRect();
    if (triggerBox.top < paneBox.bottom) requestNextPage();
  };
  listPane.addEventListener('scroll', maybeRequestNextPage, { passive:true });

  const setBrowserCompanionStatus = (projection) => {
    const configured = projection?.configured === true;
    const manualImport = projection?.manual_import_available === true || configured;
    const manualCapture = projection?.manual_capture_available === true || configured;
    const banner = q('#sessions-browser-setup-required');
    banner.hidden = configured;
    banner.querySelector('strong').textContent = projection?.manual_import_observed
      ? '网页录入已可用，自动同步连接尚未确认'
      : '自动同步连接尚未确认';
    q('#sessions-import-browser-sessions').disabled = !manualImport;
    q('#sessions-capture-current-page').disabled = !manualCapture;
    q('#sessions-import-browser-sessions').title = '导入 Memo 网页会话插件生成的 JSON 会话包';
    q('#sessions-capture-current-page').title = manualCapture
      ? '在目标 AI 网页点击 Memo 扩展，选择“录入当前界面”'
      : '请先在设置中准备 Memo 网页会话插件';
    return configured;
  };

  const setRefinementConfiguration = (projection) => {
    refinementModelConfigured = projection?.configured === true;
    refinementProfileExecutionEligible = projection?.selected_profile_execution_eligible === true;
    refinementProfileStartEligible = projection?.selected_profile_start_eligible === true
      || refinementProfileExecutionEligible;
    refinementProfileKind = String(projection?.selected_profile_kind || '').toUpperCase();
    refinementProfileExecutionStatus = String(
      projection?.selected_profile_execution_status || 'PROFILE_NOT_CONFIGURED'
    );
    refinementExecutionReady = projection?.execution_ready === true;
    return refinementModelConfigured
      && refinementProfileStartEligible
      && refinementExecutionReady;
  };

  const refinementStartDisabled = () => (
    !selectedRefinementEligible
    || !refinementModelConfigured
    || !refinementProfileStartEligible
    || !refinementExecutionReady
  );

  const clearRefinementRetryReset = () => {
    window.clearInterval(refinementRetryTimer);
    refinementRetryTimer = 0;
    refinementRetryProjectionId = null;
  };

  const scheduleRefinementRetryReset = (projectionId, errorCode = '') => {
    clearRefinementRetryReset();
    if (projectionId !== selectedProjectionId) return;
    refinementRetryProjectionId = projectionId;
    let remaining = REFINEMENT_RETRY_COUNTDOWN_SECONDS;
    const button = q('#sessions-refine');
    const render = () => {
      if (refinementRetryProjectionId !== selectedProjectionId) {
        clearRefinementRetryReset();
        return;
      }
      button.disabled = false;
      button.textContent = `执行失败 · ${remaining}`;
      setRefineState(`精炼任务执行失败${errorCode ? `（${errorCode}）` : ''}；可立即点击重试，或等待 ${remaining} 秒恢复。`);
    };
    render();
    refinementRetryTimer = window.setInterval(() => {
      remaining -= 1;
      if (remaining > 0) {
        render();
        return;
      }
      clearRefinementRetryReset();
      if (projectionId !== selectedProjectionId) return;
      button.disabled = refinementStartDisabled();
      button.textContent = '精炼对话';
      setRefineState('上次执行失败，现已可重新精炼。');
    }, 1000);
  };

  const clearToastTimers = () => {
    window.clearTimeout(toastTimer);
    window.clearTimeout(toastFadeTimer);
    toastTimer = 0;
    toastFadeTimer = 0;
  };

  const hideToast = ({ animate = true } = {}) => {
    const toast = q('#sessions-toast');
    clearToastTimers();
    const version = ++toastVersion;
    if (toast.hidden) return;
    if (!animate || reducedMotion.matches) {
      toast.hidden = true;
      toast.classList.remove('is-fading');
      return;
    }
    toast.classList.add('is-fading');
    toastFadeTimer = window.setTimeout(() => {
      if (version !== toastVersion) return;
      toast.hidden = true;
      toast.classList.remove('is-fading');
      toastFadeTimer = 0;
    }, 280);
  };

  const showToast = (copy) => {
    const toast = q('#sessions-toast');
    clearToastTimers();
    const version = ++toastVersion;
    q('#sessions-toast-copy').textContent = copy;
    toast.classList.remove('is-fading');
    toast.hidden = false;
    toastTimer = window.setTimeout(() => {
      if (version === toastVersion) hideToast({ animate:true });
    }, 5000);
  };

  const getDetailLimits = () => {
    const style = window.getComputedStyle(stage);
    const horizontalPadding = (Number.parseFloat(style.paddingLeft) || 0) + (Number.parseFloat(style.paddingRight) || 0);
    const contentWidth = Math.max(0, stage.clientWidth - horizontalPadding);
    const dividerWidth = 12;
    const minimumListWidth = 148;
    const splitWidth = Math.max(0, contentWidth - dividerWidth);
    const absoluteMaximum = Math.max(0, splitWidth - minimumListWidth);
    const min = absoluteMaximum >= 320 ? 320 : absoluteMaximum >= 220 ? 220 : absoluteMaximum;
    const max = Math.max(min, absoluteMaximum);
    const defaultSize = clampNumber(splitWidth * .6, min, max);
    return { min, max, splitWidth, defaultSize };
  };

  const setDetailSize = (value, announce = false, customized = false) => {
    const limits = getDetailLimits();
    detailSize = clampNumber(Number.isFinite(value) ? value : limits.defaultSize, limits.min, limits.max);
    if (customized) detailSizeCustomized = true;
    stage.style.setProperty('--desktop-sessions-detail-size', `${Math.round(detailSize)}px`);
    detailResizer.setAttribute('aria-valuemin', String(Math.round(limits.min)));
    detailResizer.setAttribute('aria-valuemax', String(Math.round(limits.max)));
    detailResizer.setAttribute('aria-valuenow', String(Math.round(detailSize)));
    const detailPercent = limits.splitWidth ? Math.round(detailSize / limits.splitWidth * 100) : 60;
    const listPercent = Math.max(0, 100 - detailPercent);
    detailResizer.setAttribute('aria-valuetext', `一级列表 ${listPercent}% · 会话详情 ${detailPercent}%`);
    if (announce) q('#desktop-live-state').textContent = `一级列表约 ${listPercent}%，会话详情约 ${detailPercent}%`;
  };

  const sourceMark = (provider) => ({ codex:'CX', claude:'CC', deepseek:'DS', gemini:'GM', kimi:'KM', universal:'UN' }[provider] || 'SE');
  const webProvider = (provider) => ['deepseek', 'gemini', 'kimi', 'universal'].includes(provider);

  const displayTime = (value, recencyBucket = 'UNKNOWN', ingestedAt = 'UNKNOWN', timeBasis = '') => {
    if ((!value || value === 'UNKNOWN') && ingestedAt && ingestedAt !== 'UNKNOWN') {
      const ingested = new Date(ingestedAt);
      if (!Number.isNaN(ingested.getTime())) {
        const time = new Intl.DateTimeFormat(undefined, { hour:'2-digit', minute:'2-digit', hour12:false })
          .format(ingested)
          .replace(/^0/, '');
        const language = String(document.documentElement.lang || 'zh-CN').toLowerCase();
        if (language.startsWith('en')) return `(${time} ingested)`;
        if (language.startsWith('ja')) return `（${time} 登録）`;
        return `（${time}入库）`;
      }
    }
    if (!value || value === 'UNKNOWN') return ({
      TODAY:'今天', LAST_7_DAYS:'7 日内', LAST_14_DAYS:'14 日内', LAST_30_DAYS:'30 日内'
    }[recencyBucket] || '时间未知');
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const target = new Date(parsed.getFullYear(), parsed.getMonth(), parsed.getDate());
    const days = Math.round((today - target) / 86400000);
    const time = new Intl.DateTimeFormat(undefined, { hour:'2-digit', minute:'2-digit', hour12:false }).format(parsed);
    if (days === 0) return `今天 ${time}`;
    if (days === 1) return `昨天 ${time}`;
    return new Intl.DateTimeFormat(undefined, { year:'numeric', month:'2-digit', day:'2-digit' }).format(parsed);
  };

  const createCard = (record) => {
    const card = document.createElement('article');
    const canDelete = Boolean(record.delete_capability && record.delete_capability !== 'none');
    card.className = `desktop-sessions-card${canDelete ? '' : ' readonly'}`;
    card.tabIndex = 0;
    card.setAttribute('aria-selected', 'false');
    card.setAttribute('aria-expanded', 'false');
    card.setAttribute('aria-controls', 'sessions-detail');
    card.dataset.localProjectionId = record.local_projection_id;
    card.dataset.key = `${record.provider_id}:${record.provider_session_id}`;
    card.dataset.source = record.provider_id;
    card.dataset.sourceName = record.source_name || record.provider_id;
    card.dataset.title = record.title || '会话';
    card.dataset.summary = record.summary || '';
    card.dataset.time = record.captured_at || 'UNKNOWN';
    card.dataset.ingestedAt = record.ingested_at || 'UNKNOWN';
    card.dataset.timeBasis = record.time_basis || '';
    card.dataset.recencyBucket = record.recency_bucket || 'UNKNOWN';
    card.dataset.recencyRank = String(record.source_recency_rank ?? 239);
    card.dataset.syncStatus = record.sync_status || 'COMPLETE';
    card.dataset.dir = record.local_dir || '';
    card.dataset.delete = canDelete ? record.delete_capability : '';
    card.dataset.model = record.model || '';
    card.dataset.refine = record.refine_status || 'disabled';
    card.dataset.partial = String(Boolean(record.partial));

    const check = document.createElement('input');
    check.className = 'desktop-sessions-check';
    check.type = 'checkbox';
    check.hidden = !batch;
    check.disabled = !canDelete;
    check.setAttribute('aria-label', `选择：${card.dataset.title}`);
    if (check.disabled) check.title = '此来源只读';

    const source = document.createElement('span');
    source.className = 'desktop-sessions-source';
    source.textContent = sourceMark(record.provider_id);
    source.title = card.dataset.sourceName;
    source.setAttribute('aria-label', `来源：${card.dataset.sourceName}`);

    const copy = document.createElement('span');
    copy.className = 'desktop-sessions-card-copy';
    const title = document.createElement('strong');
    title.textContent = card.dataset.title;
    const summary = document.createElement('small');
    summary.textContent = [card.dataset.dir, card.dataset.summary].filter(Boolean).join(' · ') || '本机只读会话';
    copy.append(title, summary);

    const time = document.createElement('time');
    if (record.captured_at && record.captured_at !== 'UNKNOWN') time.dateTime = record.captured_at;
    else if (record.ingested_at && record.ingested_at !== 'UNKNOWN') time.dateTime = record.ingested_at;
    time.textContent = displayTime(record.captured_at, record.recency_bucket, record.ingested_at, record.time_basis);
    if (record.time_basis === 'INGESTED_AT') time.title = '对话时间未知；按首次进入 Memorive 的时间排序';
    const chevron = document.createElement('span');
    chevron.className = 'desktop-sessions-chevron';
    chevron.setAttribute('aria-hidden', 'true');
    chevron.textContent = '›';
    card.append(check, source, copy, time, chevron);
    return card;
  };

  const setEmpty = (message, helper = '') => {
    emptyState.querySelector('strong').textContent = message;
    emptyState.querySelector('span').textContent = helper;
    emptyState.hidden = false;
  };

  const updateCounts = (visible, total) => {
    q('#sessions-visible-count').textContent = String(visible);
    q('#sessions-total-count').textContent = String(total);
    q('#sessions-count').setAttribute('aria-label', `显示 ${visible} 项，共 ${total} 项`);
  };

  const updateBatch = () => {
    const selected = visibleCards().filter((card) => {
      const checkbox = card.querySelector('.desktop-sessions-check');
      return checkbox.checked && !checkbox.disabled;
    });
    const mutableCount = cards().filter((card) => Boolean(card.dataset.delete)).length;
    q('#sessions-bulk').disabled = false;
    q('#sessions-bulk').title = mutableCount === 0
      ? '可以进入批量管理；只读会话仍会显示，但不会修改源会话'
      : `可移除 ${mutableCount} 条本地投影；只读会话仍会显示`;
    q('#sessions-selected').textContent = `已选 ${selected.length} 项`;
    q('#sessions-batch-delete').textContent = '批量移除本地记录';
    q('#sessions-batch-delete').disabled = selected.length === 0;
  };

  const selectedBatchIds = () => visibleCards().filter((card) => {
    const checkbox = card.querySelector('.desktop-sessions-check');
    return checkbox.checked && !checkbox.disabled;
  }).map((card) => card.dataset.localProjectionId).filter(Boolean);

  const resetBatchConfirm = () => {
    window.clearInterval(batchConfirmTimer);
    batchConfirmTimer = 0;
    batchPendingIds = [];
    q('#sessions-batch-actions').hidden = false;
    q('#sessions-batch-confirm').hidden = true;
    q('#sessions-batch-commit').disabled = false;
  };

  const startBatchConfirm = () => {
    const ids = selectedBatchIds();
    if (!ids.length) return;
    resetBatchConfirm();
    batchPendingIds = ids;
    q('#sessions-batch-actions').hidden = true;
    q('#sessions-batch-confirm').hidden = false;
    let remaining = 10;
    const render = () => { q('#sessions-batch-commit').textContent = `确认移除 ${ids.length} 项 · ${remaining}`; };
    render();
    batchConfirmTimer = window.setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        resetBatchConfirm();
        showToast('批量移除确认已超时，未更改任何记录。');
        return;
      }
      render();
    }, 1000);
  };

  const exitBatchMode = () => {
    resetBatchConfirm();
    batch = false;
    q('#sessions-bulk').setAttribute('aria-pressed', 'false');
    q('#sessions-bulk').textContent = '批量管理';
    q('#sessions-batchbar').hidden = true;
    list.classList.remove('batch');
    qa('.desktop-sessions-check').forEach((box) => {
      box.hidden = true;
      box.checked = false;
    });
    updateBatch();
  };

  const resetDeleteConfirm = () => {
    window.clearInterval(deleteConfirmTimer);
    deleteConfirmTimer = 0;
    q('#sessions-delete-confirm').hidden = true;
    q('#sessions-delete-commit').disabled = false;
    q('#sessions-delete-start').hidden = !selectedCard?.dataset.delete;
  };

  const startDeleteConfirm = () => {
    if (!selectedProjectionId || !selectedCard?.dataset.delete) return;
    resetDeleteConfirm();
    q('#sessions-delete-start').hidden = true;
    q('#sessions-delete-confirm').hidden = false;
    let remaining = 10;
    const render = () => { q('#sessions-delete-commit').textContent = `确认移除 · ${remaining}`; };
    render();
    deleteConfirmTimer = window.setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        resetDeleteConfirm();
        showToast('移除确认已超时，未更改记录。');
        return;
      }
      render();
    }, 1000);
  };

  const closeDetail = ({ restoreFocus = false, immediate = false } = {}) => {
    const closingCard = selectedCard;
    resetDeleteConfirm();
    clearRefinementRetryReset();
    selectedCard = null;
    selectedProjectionId = null;
    selectedRefinementEligible = false;
    activeRefinementJobId = null;
    cards().forEach((card) => {
      card.setAttribute('aria-selected', 'false');
      card.setAttribute('aria-expanded', 'false');
    });
    window.clearTimeout(detailTransitionTimer);
    const version = ++detailTransitionVersion;
    stage.classList.remove('detail-open');
    stage.classList.add('detail-closing');
    const finish = () => {
      if (version !== detailTransitionVersion || stage.classList.contains('detail-open')) return;
      detail.hidden = true;
      detail.setAttribute('aria-hidden', 'true');
      detailResizer.hidden = true;
      stage.classList.remove('detail-closing');
      if (restoreFocus && closingCard?.isConnected) closingCard.focus({ preventScroll:true });
    };
    if (immediate || detail.hidden || reducedMotion.matches) finish();
    else detailTransitionTimer = window.setTimeout(finish, 190);
  };

  const renderCapability = (card) => {
    window.clearInterval(deleteConfirmTimer);
    deleteConfirmTimer = 0;
    clearRefinementRetryReset();
    const canDelete = Boolean(card.dataset.delete);
    q('#sessions-readonly').hidden = canDelete;
    q('#sessions-delete-start').hidden = !canDelete;
    q('#sessions-delete-confirm').hidden = true;
    q('#sessions-delete-help').hidden = true;
    q('#sessions-refine').hidden = false;
    q('#sessions-refine').disabled = true;
    q('#sessions-refine').textContent = '精炼对话';
    selectedRefinementEligible = false;
    activeRefinementJobId = null;
    setRefineState('', { show: false });
    q('#sessions-refinement-draft').hidden = true;
    q('#sessions-refinement-items').replaceChildren();
  };

  const renderTurnState = (copy, error = false) => {
    const turns = q('#sessions-turns');
    const state = document.createElement('div');
    state.className = `desktop-sessions-turn-placeholder${error ? ' error' : ''}`;
    state.textContent = copy;
    turns.replaceChildren(state);
  };

  const showDetail = (card) => {
    if (selectedProjectionId === card.dataset.localProjectionId && stage.classList.contains('detail-open') && !detail.hidden) {
      // SAME_SESSION_ALREADY_OPEN: preserve scroll position and avoid duplicate native reads or transitions.
      return;
    }
    closeDetail({ immediate:true });
    const version = ++detailTransitionVersion;
    selectedCard = card;
    selectedProjectionId = card.dataset.localProjectionId || null;
    cards().forEach((item) => {
      const current = item === card;
      item.setAttribute('aria-selected', String(current));
      item.setAttribute('aria-expanded', String(current));
    });
    q('#sessions-detail-source').textContent = card.dataset.sourceName;
    q('#sessions-detail-title').textContent = card.dataset.title;
    q('#sessions-detail-time').textContent = displayTime(card.dataset.time, card.dataset.recencyBucket);
    q('#sessions-detail-key').textContent = card.dataset.key;
    q('#sessions-detail-dir-wrap').hidden = !card.dataset.dir;
    q('#sessions-detail-dir').textContent = card.dataset.dir;
    q('#sessions-detail-model-wrap').hidden = !card.dataset.model;
    q('#sessions-detail-model').textContent = card.dataset.model;
    q('#sessions-partial').hidden = card.dataset.partial !== 'true';
    renderCapability(card);
    renderTurnState('正在读取所选会话正文…');
    detail.hidden = false;
    detail.setAttribute('aria-hidden', 'false');
    detailResizer.hidden = false;
    stage.classList.remove('detail-open', 'detail-closing');
    const limits = getDetailLimits();
    setDetailSize(detailSizeCustomized ? detailSize : limits.defaultSize);
    void stage.offsetWidth;
    requestAnimationFrame(() => {
      if (version !== detailTransitionVersion || selectedCard !== card) return;
      stage.classList.add('detail-open');
      q('#sessions-detail-title').focus({ preventScroll:true });
    });
    if (selectedProjectionId) {
      void window.__Desktop_SESSIONS_BRIDGE__?.loadDetail(selectedProjectionId);
    }
  };

  const focusProjection = (projectionId) => {
    const accepted = String(projectionId || '').trim();
    if (!accepted) return false;
    const card = list.querySelector(`[data-local-projection-id="${CSS.escape(accepted)}"]`);
    if (!card) return false;
    showDetail(card);
    card.scrollIntoView({ block:'nearest', behavior:reducedMotion.matches ? 'auto' : 'smooth' });
    return true;
  };

  const ensureRecord = (record) => {
    const projectionId = String(record?.local_projection_id || '').trim();
    if (!projectionId) return false;
    recordsById.set(projectionId, record);
    const existing = list.querySelector(
      `[data-local-projection-id="${CSS.escape(projectionId)}"]`
    );
    const card = createCard(record);
    if (existing) existing.replaceWith(card);
    else list.prepend(card);
    emptyState.hidden = true;
    updateCounts(recordsById.size, Math.max(recordsById.size, Number(q('#sessions-total')?.textContent) || 0));
    return true;
  };

  const replaceRows = (rows, options = {}) => {
    const nextRows = Array.isArray(rows) ? rows : [];
    const previousSelection = selectedProjectionId;
    clearPageTrigger();
    recordsById.clear();
    const fragment = document.createDocumentFragment();
    nextRows.forEach((record) => {
      if (!record?.local_projection_id) return;
      recordsById.set(record.local_projection_id, record);
      fragment.append(createCard(record));
    });
    list.replaceChildren(fragment);
    const total = Number.isFinite(options.totalCount) ? options.totalCount : nextRows.length;
    updateCounts(nextRows.length, total);
    if (nextRows.length) emptyState.hidden = true;
    else setEmpty(
      options.emptyMessage || (options.error ? '读取本机会话失败' : '没有符合条件的会话'),
      options.emptyHelper || options.error || '可调整来源筛选或搜索条件。'
    );
    if (previousSelection && !recordsById.has(previousSelection)) closeDetail({ immediate:true });
    else if (previousSelection) {
      selectedCard = list.querySelector(`[data-local-projection-id="${CSS.escape(previousSelection)}"]`);
      selectedCard?.setAttribute('aria-selected', 'true');
      selectedCard?.setAttribute('aria-expanded', 'true');
    }
    updateBatch();
    const pendingTarget = String(window.__Desktop_SESSION_NAV_TARGET__ || '').trim();
    if (pendingTarget && focusProjection(pendingTarget)) {
      window.__Desktop_SESSION_NAV_TARGET__ = '';
    }
    armPageTrigger(options);
    return { record_count:recordsById.size, selected_projection_id:selectedProjectionId };
  };

  const appendRows = (rows, options = {}) => {
    const nextRows = Array.isArray(rows) ? rows : [];
    const fragment = document.createDocumentFragment();
    nextRows.forEach((record) => {
      const projectionId = String(record?.local_projection_id || '').trim();
      if (!projectionId) return;
      recordsById.set(projectionId, record);
      const existing = list.querySelector(
        `[data-local-projection-id="${CSS.escape(projectionId)}"]`
      );
      const card = createCard(record);
      if (existing) existing.replaceWith(card);
      else fragment.append(card);
      if (projectionId === selectedProjectionId) {
        selectedCard = card;
        selectedCard.setAttribute('aria-selected', 'true');
        selectedCard.setAttribute('aria-expanded', 'true');
      }
    });
    list.append(fragment);
    const total = Number.isFinite(options.totalCount) ? options.totalCount : recordsById.size;
    updateCounts(recordsById.size, total);
    emptyState.hidden = recordsById.size > 0;
    updateBatch();
    armPageTrigger(options);
    return { record_count:recordsById.size, selected_projection_id:selectedProjectionId };
  };

  const renderDetail = (projection) => {
    const session = projection?.session;
    if (!session || session.local_projection_id !== selectedProjectionId) return false;
    q('#sessions-detail-source').textContent = session.source_name;
    q('#sessions-detail-title').textContent = session.title || '会话';
    q('#sessions-detail-time').textContent = displayTime(session.captured_at, session.recency_bucket, session.ingested_at, session.time_basis);
    q('#sessions-detail-time').title = session.time_basis === 'INGESTED_AT'
      ? '对话时间未知；此处显示首次进入 Memorive 的时间'
      : '';
    q('#sessions-detail-key').textContent = `${session.provider_id}:${session.provider_session_id}`;
    q('#sessions-detail-dir-wrap').hidden = !session.local_dir;
    q('#sessions-detail-dir').textContent = session.local_dir || '';
    q('#sessions-detail-model-wrap').hidden = !session.model;
    q('#sessions-detail-model').textContent = session.model || '';
    q('#sessions-partial').hidden = !session.partial;
    const turns = q('#sessions-turns');
    const fragment = document.createDocumentFragment();
    (projection.messages || []).forEach((message) => {
      if (!['user', 'assistant'].includes(message?.role) || !message.content) return;
      const turn = document.createElement('div');
      turn.className = `desktop-sessions-turn ${message.role}`;
      const label = document.createElement('small');
      label.textContent = message.role === 'user' ? '你' : 'AI';
      const content = document.createElement('p');
      content.textContent = message.content;
      turn.append(label, content);
      fragment.append(turn);
    });
    if (!fragment.childNodes.length) {
      const state = document.createElement('div');
      state.className = 'desktop-sessions-turn-placeholder';
      state.textContent = '这条会话没有可显示的用户或助手文本。';
      fragment.append(state);
    }
    turns.replaceChildren(fragment);
    const complete = projection?.completeness?.status === 'COMPLETE' && !session.partial;
    const currentPagePartial = projection?.completeness?.status === 'PARTIAL'
      && session.partial === true
      && session.provider_id === 'universal'
      && session.source_format === 'browser_current_page_v1'
      && (projection.messages || []).some((message) => (
        ['user', 'assistant'].includes(message?.role)
        && typeof message?.content === 'string'
        && message.content.trim()
      ));
    selectedRefinementEligible = complete || currentPagePartial;
    q('#sessions-refine').disabled = refinementStartDisabled();
    const refinementState = currentPagePartial
      ? '该记录仅包含录入时的当前页可见内容；可按该范围精炼，任务快照会保留部分内容标记。'
      : !complete
      ? '会话未完整读取，请重新同步。'
      : !refinementModelConfigured
        ? '请先选择精炼模型'
        : !refinementProfileStartEligible
          ? ['API', 'CLI'].includes(refinementProfileKind)
            ? '请先验证模型'
            : refinementProfileExecutionStatus === 'LOCAL_REFINEMENT_EXECUTION_NOT_ELIGIBLE'
              ? '当前模型不可用'
              : '当前模型不可用'
        : !refinementExecutionReady
          ? '精炼服务未就绪'
        : !refinementProfileExecutionEligible
          && refinementProfileExecutionStatus === 'LOCAL_IDENTITY_REFRESH_REQUIRED'
          ? '开始前将检查模型'
        : '';
    setRefineState(refinementState, {
      show: Boolean(refinementState),
      title: currentPagePartial ? '仅含录入时可见内容' : '',
    });
    return true;
  };

  const capabilitiesTypeLabels = Object.freeze({
    USER_DECISION_CLAIM:'用户决定声明',
    CONSTRAINT_OR_PREFERENCE:'约束或偏好',
    REPOSITORY_VERIFIABLE_FACT_CLAIM:'可核验事实声明',
    REUSABLE_METHOD:'可复用方法',
    AI_SUGGESTION:'AI 建议',
    HYPOTHESIS:'假设',
    OPEN_QUESTION:'开放问题',
    FAILED_ATTEMPT:'失败尝试',
    OVERTURNED_CONCLUSION:'已推翻结论',
    PROVENANCE_ONLY:'仅溯源',
    FORBIDDEN_REUSE:'禁止复用'
  });

  const renderRefinementDraft = (projection) => {
    const draft = projection?.draft;
    const subjectOutput = draft?.subject_output;
    if (
      projection?.status !== 'PASS'
      || draft?.local_projection_id !== selectedProjectionId
      || subjectOutput?.schema_version !== 'CONVERSATION_REFINEMENT_SUBJECT_OUTPUT_V1'
      || !Array.isArray(subjectOutput?.items)
    ) return false;
    const panel = q('#sessions-refinement-draft');
    const target = q('#sessions-refinement-items');
    const fragment = document.createDocumentFragment();
    for (const item of subjectOutput.items) {
      const row = document.createElement('article');
      row.className = 'desktop-sessions-refinement-item';
      const label = document.createElement('small');
      const refinementType = String(item?.refinement_type || 'UNKNOWN_TYPE');
      const evidenceState = String(item?.evidence_state || 'UNKNOWN_EVIDENCE');
      label.textContent = `${capabilitiesTypeLabels[refinementType] || refinementType} · ${evidenceState}`;
      const copy = document.createElement('p');
      copy.textContent = String(item?.candidate_text || '');
      row.append(label, copy);
      if (Array.isArray(item?.limitations) && item.limitations.length) {
        const limitations = document.createElement('small');
        limitations.textContent = `限制：${item.limitations.join(' · ')}`;
        row.append(limitations);
      }
      fragment.append(row);
    }
    target.replaceChildren(fragment);
    panel.hidden = false;
    setRefineState(`已生成 ${subjectOutput.items.length} 条，待复核`);
    panel.scrollIntoView({ block:'nearest', behavior:reducedMotion.matches ? 'auto' : 'smooth' });
    return true;
  };

  const renderRefinementTaskState = (projection) => {
    const projectionId = projection?.draft?.local_projection_id
      || projection?.task?.local_projection_id
      || projection?.local_projection_id;
    if (projectionId !== selectedProjectionId) return false;
    const status = projection?.status;
    const button = q('#sessions-refine');
    const jobId = projection?.task?.job_id || projection?.job_id || activeRefinementJobId;
    if (['INBOX_QUEUED', 'INBOX_PROCESSING', 'TASK_CREATED', 'TASK_QUEUED', 'TASK_RUNNING'].includes(status)) {
      clearRefinementRetryReset();
      activeRefinementJobId = jobId;
      button.disabled = true;
      button.textContent = '请在收件箱中查看';
      setRefineState(status === 'INBOX_QUEUED' ? '已加入队列' : '精炼中');
      return true;
    }
    if (status === 'PASS') {
      clearRefinementRetryReset();
      activeRefinementJobId = null;
      button.disabled = true;
      button.textContent = '精炼完成';
      return renderRefinementDraft(projection);
    }
    if (status === 'TASK_FAILED' || status === 'TASK_CANCELLED') {
      activeRefinementJobId = null;
      const errorCode = projection?.task?.error_code || projection?.error_code || '';
      if (status === 'TASK_FAILED') {
        scheduleRefinementRetryReset(projectionId, errorCode);
      } else {
        clearRefinementRetryReset();
        button.disabled = refinementStartDisabled();
        button.textContent = '精炼对话';
        setRefineState('精炼任务已取消，可重新精炼。');
      }
      return true;
    }
    if (status === 'TASK_MONITOR_TIMEOUT' || status === 'TASK_MONITOR_FAILED') {
      button.disabled = true;
      button.textContent = '请在收件箱中查看';
      setRefineState('状态更新中断，请到收件箱查看。');
      return true;
    }
    return false;
  };

  const renderDetailError = (projectionId, copy) => {
    if (projectionId !== selectedProjectionId) return;
    renderTurnState(copy || '读取会话正文失败。', true);
  };

  list.addEventListener('click', (event) => {
    const card = event.target.closest('.desktop-sessions-card');
    if (!card) return;
    if (event.target.closest('.desktop-sessions-check')) {
      updateBatch();
      return;
    }
    if (!batch) showDetail(card);
  });
  list.addEventListener('keydown', (event) => {
    if (!batch && event.key === 'Enter' && event.target.matches('.desktop-sessions-card')) {
      event.preventDefault();
      showDetail(event.target);
    }
  });
  q('#sessions-close').addEventListener('click', () => closeDetail({ restoreFocus:true }));

  detailResizer.addEventListener('pointerdown', (event) => {
    if (detail.hidden) return;
    event.preventDefault();
    detailResizeState = { pointerId:event.pointerId, startX:event.clientX, startWidth:detail.getBoundingClientRect().width };
    detailResizer.setPointerCapture?.(event.pointerId);
    stage.dataset.detailResizing = 'true';
  });
  detailResizer.addEventListener('pointermove', (event) => {
    if (!detailResizeState || event.pointerId !== detailResizeState.pointerId) return;
    setDetailSize(detailResizeState.startWidth + detailResizeState.startX - event.clientX, false, true);
  });
  const finishDetailResize = (event) => {
    if (!detailResizeState || event.pointerId !== detailResizeState.pointerId) return;
    detailResizer.releasePointerCapture?.(event.pointerId);
    detailResizeState = null;
    delete stage.dataset.detailResizing;
    setDetailSize(detailSize, true, true);
  };
  detailResizer.addEventListener('pointerup', finishDetailResize);
  detailResizer.addEventListener('pointercancel', finishDetailResize);
  detailResizer.addEventListener('keydown', (event) => {
    const limits = getDetailLimits();
    let next = Number.isFinite(detailSize) ? detailSize : limits.defaultSize;
    if (event.key === 'ArrowLeft') next += 24;
    else if (event.key === 'ArrowRight') next -= 24;
    else if (event.key === 'Home') next = limits.min;
    else if (event.key === 'End') next = limits.max;
    else return;
    event.preventDefault();
    setDetailSize(next, true, true);
  });
  new ResizeObserver(() => {
    if (!stage.classList.contains('detail-open')) return;
    const limits = getDetailLimits();
    setDetailSize(detailSizeCustomized ? detailSize : limits.defaultSize);
  }).observe(stage);

  const queryBridge = () => {
    const filter = currentFilter();
    const bridge = window.__Desktop_SESSIONS_BRIDGE__;
    if (bridge) return bridge.query(filter);
    const term = filter.search.trim().toLowerCase();
    const rows = [...recordsById.values()].filter((record) => {
      const domain = [record.source_name, record.title, record.summary, record.local_dir, record.model].join(' ').toLowerCase();
      return (filter.source === 'all' || record.provider_id === filter.source) && (!term || domain.includes(term));
    });
    replaceRows(rows, { totalCount:recordsById.size });
    return Promise.resolve({ rows });
  };

  window.addEventListener('desktop:open-session', (event) => {
    const projectionId = String(event?.detail?.local_projection_id || '').trim();
    if (!projectionId) return;
    window.__Desktop_SESSION_NAV_TARGET__ = projectionId;
    q('#sessions-source').value = 'all';
    q('#sessions-search').value = '';
    if (!focusProjection(projectionId)) void window.__Desktop_SESSIONS_BRIDGE__?.openProjection(projectionId);
    else window.__Desktop_SESSION_NAV_TARGET__ = '';
  });

  q('#sessions-search-toggle').addEventListener('click', () => {
    q('#sessions-searchbox').hidden = false;
    q('#sessions-search-toggle').setAttribute('aria-expanded', 'true');
    q('#sessions-search').focus();
  });
  q('#sessions-search-clear').addEventListener('click', () => {
    q('#sessions-search').value = '';
    q('#sessions-searchbox').hidden = true;
    q('#sessions-search-toggle').setAttribute('aria-expanded', 'false');
    void queryBridge();
    q('#sessions-search-toggle').focus();
  });
  q('#sessions-search').addEventListener('input', () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => void queryBridge(), 160);
  });
  q('#sessions-source').addEventListener('change', () => {
    resetBatchConfirm();
    closeDetail();
    void queryBridge();
  });
  q('#sessions-limit').addEventListener('change', () => {
    resetBatchConfirm();
    closeDetail();
    void queryBridge();
  });
  q('#sessions-refresh').addEventListener('click', () => {
    closeDetail();
    const bridge = window.__Desktop_SESSIONS_BRIDGE__;
    if (!bridge) return;
    const refresh = q('#sessions-refresh');
    refresh.disabled = true;
    refresh.classList.add('is-spinning');
    refresh.setAttribute('aria-busy', 'true');
    void bridge.refresh(currentFilter()).finally(() => {
      refresh.disabled = false;
      refresh.classList.remove('is-spinning');
      refresh.setAttribute('aria-busy', 'false');
    });
  });

  q('#sessions-refresh-local').addEventListener('click', () => {
    closeDetail();
    const bridge = window.__Desktop_SESSIONS_BRIDGE__;
    if (!bridge) return;
    const button = q('#sessions-refresh-local');
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    void bridge.refreshLocalSessions(currentFilter()).finally(() => {
      button.disabled = false;
      button.setAttribute('aria-busy', 'false');
    });
  });

  q('#sessions-bulk').addEventListener('click', () => {
    if (batch) {
      exitBatchMode();
      return;
    }
    batch = true;
    closeDetail();
    q('#sessions-bulk').setAttribute('aria-pressed', String(batch));
    q('#sessions-bulk').textContent = batch ? '退出批量' : '批量管理';
    q('#sessions-batchbar').hidden = !batch;
    list.classList.toggle('batch', batch);
    qa('.desktop-sessions-check').forEach((box) => { box.hidden = false; });
    updateBatch();
  });
  q('#sessions-select-visible').addEventListener('click', () => {
    visibleCards().forEach((card) => {
      const box = card.querySelector('.desktop-sessions-check');
      if (!box.disabled) box.checked = true;
    });
    updateBatch();
  });
  q('#sessions-clear-selected').addEventListener('click', () => {
    resetBatchConfirm();
    qa('.desktop-sessions-check').forEach((box) => { box.checked = false; });
    updateBatch();
  });
  q('#sessions-batch-delete').addEventListener('click', startBatchConfirm);
  q('#sessions-batch-cancel').addEventListener('click', resetBatchConfirm);
  q('#sessions-batch-commit').addEventListener('click', () => {
    if (!batchPendingIds.length) return;
    const ids = [...batchPendingIds];
    const button = q('#sessions-batch-commit');
    button.disabled = true;
    window.clearInterval(batchConfirmTimer);
    batchConfirmTimer = 0;
    void window.__Desktop_SESSIONS_BRIDGE__?.bulkDelete(ids)
      .then((receipt) => {
        showToast(`已从 Memorive 移除 ${receipt?.success_count || 0} 条`);
        exitBatchMode();
      })
      .catch((error) => {
        button.disabled = false;
        showToast(`批量移除失败：${error?.message || error}`);
        resetBatchConfirm();
      });
  });
  q('#sessions-refine').addEventListener('click', () => {
    if (!selectedProjectionId || q('#sessions-refine').disabled) return;
    clearRefinementRetryReset();
    const button = q('#sessions-refine');
    button.disabled = true;
    button.textContent = '精炼中…';
    void window.__Desktop_SESSIONS_BRIDGE__?.refine(selectedProjectionId)
      .then((receipt) => {
        if (['INBOX_QUEUED', 'TASK_QUEUED', 'TASK_RUNNING'].includes(receipt?.status)) {
          activeRefinementJobId = receipt.job_id || 'inbox';
          button.disabled = true;
          button.textContent = '请在收件箱中查看';
          showToast(receipt?.deduplicated
            ? '该会话已有活动精炼任务，未重复创建。'
            : '已加入队列');
          root.querySelector('.desktop-nav-item[data-page="收件箱"]')?.click();
        } else if (['TASK_SUCCEEDED', 'TASK_FAILED', 'TASK_CANCELLED'].includes(receipt?.status)) {
          activeRefinementJobId = receipt.job_id || 'terminal';
          button.disabled = true;
          button.textContent = receipt.status === 'TASK_SUCCEEDED' ? '精炼完成' : '任务已结束';
          showToast('该会话已有精炼任务，未重复创建。');
        }
      })
      .catch((error) => showToast(`对话精炼失败：${error?.message || error}`))
      .finally(() => {
        if (!activeRefinementJobId) {
          button.disabled = refinementStartDisabled();
          button.textContent = '精炼对话';
        }
      });
  });
  q('#sessions-import-browser-sessions').addEventListener('click', () => {
    const button = q('#sessions-import-browser-sessions');
    button.disabled = true;
    void window.__Desktop_SESSIONS_BRIDGE__?.importBrowserSessionsFile()
      .catch((error) => showToast(`网页会话导入失败：${error?.message || error}`))
      .finally(() => { button.disabled = false; });
  });
  q('#sessions-capture-current-page').addEventListener('click', () => {
    const button = q('#sessions-capture-current-page');
    button.disabled = true;
    void window.__Desktop_SESSIONS_BRIDGE__?.captureCurrentPage()
      .catch((error) => showToast(`当前界面录入失败：${error?.message || error}`))
      .finally(() => { button.disabled = false; });
  });
  q('#sessions-delete').addEventListener('click', startDeleteConfirm);
  q('#sessions-delete-cancel').addEventListener('click', resetDeleteConfirm);
  q('#sessions-delete-commit').addEventListener('click', () => {
    if (!selectedProjectionId) return;
    const projectionId = selectedProjectionId;
    const button = q('#sessions-delete-commit');
    button.disabled = true;
    window.clearInterval(deleteConfirmTimer);
    deleteConfirmTimer = 0;
    void window.__Desktop_SESSIONS_BRIDGE__?.deleteLocal(projectionId)
      .then(() => {
        closeDetail({ immediate:true });
        showToast('已从 Memorive 移除');
      })
      .catch((error) => {
        button.disabled = false;
        showToast(`移除失败：${error?.message || error}`);
        resetDeleteConfirm();
      });
  });
  q('#sessions-toast button').addEventListener('click', () => hideToast({ animate:true }));
  qa('.desktop-nav-item').forEach((item) => item.addEventListener('click', () => {
    if (item.dataset.page !== '会话管理') closeDetail({ immediate:true });
  }, true));
  const globalRight = q('#desktop-toggle-right');
  globalRight.disabled = true;
  globalRight.setAttribute('aria-label', '本页完整详情使用中央宽阅读区');
  root.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !q('#sessions-searchbox').hidden) {
      event.preventDefault();
      q('#sessions-search-clear').click();
    }
  }, true);

  window.__Desktop_SESSION_PAGE__ = Object.freeze({
    replaceRows,
    appendRows,
    renderDetail,
    renderDetailError,
    renderRefinementDraft,
    renderRefinementTaskState,
    setBrowserCompanionStatus,
    setRefinementConfiguration,
    focusProjection,
    ensureRecord,
    currentFilter,
    closeDetail,
    showToast,
    selectedId: () => selectedProjectionId,
    inspect: () => ({
      record_count:recordsById.size,
      selected_projection_id:selectedProjectionId,
      detail_open:stage.classList.contains('detail-open'),
      page_trigger_index:pageTriggerElement ? cards().indexOf(pageTriggerElement) : -1,
      list_read_only:cards().every((card) => !card.dataset.delete)
    })
  });
  replaceRows([], { totalCount:0 });
  setEmpty('正在读取…', '');
})();

