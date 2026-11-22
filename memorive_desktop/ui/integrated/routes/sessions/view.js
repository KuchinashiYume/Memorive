/* Draws the session management page: the source tabs, session cards, the selected session's turns, the removal
   confirm countdown and the refinement state. Publishes Memorive.sessionsView and reads its data through
   Memorive.sessionsBridge. */
(() => {
  'use strict';

  const root = document.getElementById('desktop-sessions-preview');
  if (!root || !Memorive.frames.sessions) return;
  const q = (selector) => root.querySelector(selector);
  const qa = (selector) => [...root.querySelectorAll(selector)];
  const setRefineState = (message = '', { show = true, title = '' } = {}) => {
    const state = q('#sessions-refine-state');
    q('#sessions-refine-state-text').textContent = message;
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
  let refinementModelState = 'UNSELECTED';
  let refinementExecutionReady = false;
  let selectedRefinementEligible = false;
  let activeRefinementJobId = null;
  let sessionView = 'turns';            // 'turns' (原对话) or 'brief' (精炼结果)
  let shownBriefQuotes = {};            // the cited messages as the shown version read them
  let shownDraftId = null;              // the version on the page
  let shownBrief = null;                // its brief (null for a draft made before v2.00)
  let shownReview = null;               // library.refinement_review of the shown version: product, edits
  let editing = null;                   // the item being edited: { index, field:'text' | 'answer' }
  // 历史版本: open or not, the version chosen in it (a number) and how it shows (compared with the current one, or full).
  const historyView = { open:false, chosen:null, mode:'compare', ticket:0 };
  let shownRefinementCurrent = false;   // the shown version read the conversation the page shows now
  const ALL_SOURCE_PAGE_SIZE = 30;
  const ALL_SOURCE_PREFETCH_ROW = 20;
  let pageTriggerObserver = null;
  let pageTriggerElement = null;
  let pageLoadRequested = false;
  let pageTriggerOptions = null;
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
    const bridge = Memorive.sessionsBridge;
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
    banner.querySelector('strong').textContent = '网页录入插件尚未连接，请在设置中连接浏览器';
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
    refinementModelState = String(projection?.model_state || 'UNSELECTED');
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
    if (announce) Memorive.frames.sessions.announce(`一级列表约 ${listPercent}%，会话详情约 ${detailPercent}%`);
  };

  // The source's letter mark: local CLI sessions, earlier synced web sessions, and web recordings by their site
  // (source_site); a recorded site without its own reading rules shows "?".
  const SITE_MARKS = Object.freeze({ chatgpt:'GPT', claude:'CL', gemini:'GM', deepseek:'DS', kimi:'KM' });
  const sourceMark = (record) => record.provider_id === 'universal'
    ? SITE_MARKS[record.source_site] || '?'
    : ({ codex:'CX', claude:'CC', deepseek:'DS', gemini:'GM', kimi:'KM' }[record.provider_id] || '?');
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
    source.textContent = sourceMark(record);
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
    const badge = q('#sessions-count');
    badge.textContent = visible === total ? String(total) : `${visible} / ${total}`;
    badge.setAttribute('aria-label', `显示 ${visible} 项，共 ${total} 项`);
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
    const canDelete = Boolean(card.dataset.delete);
    q('#sessions-readonly').hidden = canDelete;
    q('#sessions-delete-start').hidden = !canDelete;
    q('#sessions-delete-confirm').hidden = true;
    q('#sessions-delete-help').hidden = true;
    q('#sessions-refine').hidden = false;
    q('#sessions-refine').disabled = true;
    q('#sessions-refine').textContent = '精炼对话';
    delete q('#sessions-refine').dataset.desktopTip;
    selectedRefinementEligible = false;
    activeRefinementJobId = null;
    setRefineState('', { show: false });
    q('#sessions-view-switch').hidden = true;
    shownDraftId = null;
    shownBrief = null;
    shownReview = null;
    editing = null;
    Object.assign(historyView, { open:false, chosen:null, mode:'compare' });
    void drawEditHistory();
    q('#sessions-open-library').hidden = true;
    showSessionView('turns');
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
      void Memorive.sessionsBridge?.loadDetail(selectedProjectionId);
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
    const pendingTarget = String(Memorive.sessionNavTarget || '').trim();
    if (pendingTarget && focusProjection(pendingTarget)) {
      Memorive.sessionNavTarget = '';
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
    let refinementNumber = 0;
    (projection.messages || []).forEach((message) => {
      if (!['user', 'assistant'].includes(message?.role) || !message.content) return;
      const turn = document.createElement('div');
      turn.className = `desktop-sessions-turn ${message.role}`;
      // The number a refinement gives this message (m1, m2, ...): user and assistant text, in order.
      if (message.content.trim()) turn.dataset.ref = `m${refinementNumber += 1}`;
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
      ? '会话未完整读取，请重新录入。'
      : refinementModelState === 'UNSELECTED'
        ? '请先在设置中选择精炼模型'
        : refinementModelState === 'REMOVED'
          ? '原来的精炼模型已移除，请在设置中重新选择'
        : !refinementProfileStartEligible
          ? ['API', 'CLI'].includes(refinementProfileKind) ? '请先验证模型' : '当前模型不可用'
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

  // Section titles of a conversation brief (a v4 draft; sessions/refinement/brief.py), in the brief's order.
  const briefSections = Object.freeze({
    decision:'已定事项',
    next_step:'下一步',
    result:'结果与进展',
    open_question:'待确认',
    risk:'风险',
    ai_suggestion:'AI 建议 · 未采纳'
  });
  const briefNotes = Object.freeze({
    NUMBER_NOT_IN_SOURCE:'数字未在所引原话中找到',
    decision:'没有引用用户原话，从已定事项移来',
    next_step:'没有引用用户原话，从下一步移来',
    adopted:'由你采纳（原为 AI 建议）',
    answered:'由你确认（原为待确认）',
    reworded:'你改过措辞'
  });

  const refinementRow = (label, text, notes = []) => {
    const row = document.createElement('article');
    row.className = 'desktop-sessions-refinement-item';
    if (label) {
      const small = document.createElement('small');
      small.textContent = label;
      row.append(small);
    }
    const copy = document.createElement('p');
    copy.textContent = text;
    row.append(copy);
    for (const note of notes) {
      const line = document.createElement('small');
      line.textContent = note;
      row.append(line);
    }
    return row;
  };

  const pill = (text, data) => {
    const button = document.createElement('button');
    button.type = 'button';
    Object.assign(button.dataset, data);
    button.textContent = text;
    return button;
  };

  // The item being edited: its text (or its conclusion) in a box, with 保存 and 取消.
  const itemEditor = (index, field, value) => {
    const editor = document.createElement('div');
    editor.className = 'desktop-sessions-refinement-editor';
    const box = document.createElement('textarea');
    box.className = 'field';
    box.rows = 3;
    box.value = value;
    box.setAttribute('aria-label', field === 'answer' ? '结论' : '修改措辞');
    const actions = document.createElement('div');
    actions.className = 'desktop-sessions-refinement-refs';
    actions.append(pill('保存', { act:'save', item:String(index) }), pill('取消', { act:'cancel', item:String(index) }));
    editor.append(box, actions);
    return editor;
  };

  // The overview, then the items under their sections, each with buttons for the messages it cites (#n is mn).
  // The user's edits (kept by the library per version) show here: new wording, an adopted suggestion and a
  // question's conclusion, both listed with the decisions. The edit buttons show once the version's library
  // product is known.
  const briefRows = (brief, edits = {}, canEdit = false) => {
    const fragment = document.createDocumentFragment();
    fragment.append(refinementRow('概况', String(brief.overview || '')));
    const order = Object.keys(briefSections);
    const shown = (brief.items || []).map((item, index) => {
      const edit = edits[String(index)] || {};
      return { item, index, edit, kind:edit.adopted || edit.answer ? 'decision' : item.kind };
    }).sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind) || a.index - b.index);
    let kind = null;
    for (const { item, index, edit, kind:section } of shown) {
      if (section !== kind) {
        kind = section;
        const heading = document.createElement('h4');
        heading.className = 'desktop-sessions-refinement-section';
        heading.textContent = briefSections[kind] || kind;
        fragment.append(heading);
      }
      const notes = [...(edit.text ? [] : item.flags || []), ...(item.moved_from ? [item.moved_from] : []),
        ...(edit.adopted ? ['adopted'] : []), ...(edit.answer ? ['answered'] : []), ...(edit.text ? ['reworded'] : [])]
        .map((key) => briefNotes[key]).filter(Boolean);
      const row = refinementRow('', String(edit.text || item.text || ''), notes);
      if (edit.answer) {
        const answer = document.createElement('p');
        answer.className = 'desktop-sessions-refinement-answer';
        answer.textContent = `结论：${edit.answer}`;
        row.querySelector('p').after(answer);
      }
      if (canEdit && editing?.index === index) {
        row.append(itemEditor(index, editing.field,
          editing.field === 'answer' ? (edit.answer || '') : (edit.text || item.text || '')));
        fragment.append(row);
        continue;
      }
      const refs = document.createElement('div');
      refs.className = 'desktop-sessions-refinement-refs';
      for (const ref of item.message_refs || []) {
        const button = pill(`#${String(ref).replace(/^m/, '')}`, { ref });
        button.dataset.desktopTip = '查看原话';
        refs.append(button);
      }
      if (canEdit) {
        const own = { item:String(index) };
        const actions = [pill('编辑', { ...own, act:'text' })];
        if (edit.text) actions.push(pill('恢复原文', { ...own, act:'untext' }));
        if (item.kind === 'ai_suggestion') {
          actions.push(edit.adopted ? pill('撤销采纳', { ...own, act:'unadopt' }) : pill('采纳', { ...own, act:'adopt' }));
        }
        if (item.kind === 'open_question') {
          actions.push(pill(edit.answer ? '改结论' : '写结论', { ...own, act:'answer' }));
          if (edit.answer) actions.push(pill('撤销结论', { ...own, act:'unanswer' }));
        }
        actions[0].classList.add('desktop-sessions-refinement-choice');   // the item's actions start at the right
        refs.append(...actions);
      }
      row.append(refs);
      fragment.append(row);
    }
    return fragment;
  };

  const drawBrief = () => {
    if (!shownBrief) return;
    q('#sessions-refinement-items').replaceChildren(
      briefRows(shownBrief, shownReview?.edits || {}, Boolean(shownReview?.artifact_id)));
  };

  // Save one item's edit: the fields given are set, a null one is taken back; nothing left takes the edit back.
  const saveItemEdit = (index, change) => {
    const review = shownReview;
    const current = { ...(review.edits || {})[String(index)] };
    for (const [key, value] of Object.entries(change)) {
      if (value === null) delete current[key];
      else current[key] = value;
    }
    return Memorive.sessionsBridge.call('library.edit_refinement_item', {
      artifact_id:review.artifact_id, item:index, edit:Object.keys(current).length ? current : null
    })
      .then((receipt) => {
        if (shownReview !== review) return;
        shownReview = { ...review, edits:receipt.edits, history:receipt.history };
        editing = null;
        drawBrief();
      })
      .catch((error) => {
        drawBrief();
        showToast(`没有保存：${error?.message || error}`);
      });
  };

  // 历史版本: the versions of the shown refinement's edits (the library keeps them; version 1 is the model's brief),
  // newest first, and the chosen one compared word by word with the current one or shown in full, as Research
  // Chat shows an answer's versions (shared/version-history.js). Restoring saves the chosen one as the newest.
  const tr = (s) => Memorive.app?.translate?.(s) || s;
  const editSources = Object.freeze({
    ORIGINAL:'模型生成', REWORD:'改措辞', UNREWORD:'恢复原文', ADOPT:'采纳建议', UNADOPT:'撤销采纳',
    ANSWER:'写结论', UNANSWER:'撤销结论', RESTORE:'恢复'
  });
  const editLabel = (v) => `v${v.number} · ${tr(editSources[v.source] || v.source)}`
    + (v.source === 'RESTORE' && v.restored_from ? ` v${v.restored_from}` : '');
  const historyIcon = (label, kind, onClick) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = `icon-button desktop-toolbar-icon desktop-toolbar-soft ${kind}`;
    b.setAttribute('aria-label', tr(label));
    b.dataset.desktopTip = tr(label);
    b.onclick = onClick;
    return b;
  };
  const drawEditHistory = async () => {
    const panel = q('#sessions-edit-history-panel');
    const toggle = q('#sessions-edit-history');
    const review = shownReview;
    toggle.hidden = !review?.history || !shownBrief;
    toggle.setAttribute('aria-pressed', String(historyView.open));
    if (!historyView.open || toggle.hidden) {
      panel.hidden = true;
      q('#sessions-refinement-items').hidden = false;
      return;
    }
    const mine = ++historyView.ticket;
    const history = review.history;
    const current = history.at(-1).number;
    if (!history.some((v) => v.number === historyView.chosen)) historyView.chosen = history.at(-2)?.number ?? current;
    const chosen = historyView.chosen;
    const version = history.find((v) => v.number === chosen);
    const isCurrent = chosen === current;
    const diff = !isCurrent && historyView.mode === 'compare'
      ? await Memorive.sessionsBridge.call('library.refinement_edits_diff', {
        artifact_id:review.artifact_id, from_number:chosen, to_number:current })
      : null;
    if (mine !== historyView.ticket || shownReview !== review) return;
    const H = Memorive.versionHistory;
    const itemText = (index) => String(shownBrief.items?.[index]?.text || '');
    const list = H.list(history, {
      chosen, id:(v) => v.number,
      name:(v) => editLabel(v) + (v.number === current ? ' · ' + tr('当前') : ''),
      details:(v) => [H.when(v.saved_at) + (v.item == null ? '' : ' · ' + (itemText(v.item).length > 24
        ? itemText(v.item).slice(0, 24) + '…' : itemText(v.item)))],
      onPick:(v) => {
        historyView.chosen = v.number;
        void drawEditHistory().then(() => q('#sessions-edit-history-panel .memo-history-version[aria-pressed="true"]')?.focus());
      }
    });
    const bar = document.createElement('div');
    bar.className = 'memo-history-head';
    const title = document.createElement('h4');
    title.textContent = editLabel(version) + (isCurrent ? ' · ' + tr('当前') : '');
    const tools = document.createElement('div');
    tools.className = 'memo-history-tools';
    if (!isCurrent) {
      const modes = document.createElement('div');
      modes.className = 'segmented desktop-icon-segmented';
      modes.setAttribute('role', 'group');
      modes.setAttribute('aria-label', tr('查看方式'));
      for (const [key, text, icon] of [['compare', '与当前比较', 'desktop-action-sync-code'], ['full', '全文', 'desktop-action-preview']]) {
        const b = historyIcon(text, icon, () => {
          historyView.mode = key;
          void drawEditHistory();
        });
        b.className = `desktop-toolbar-icon desktop-toolbar-ghost ${icon}`;
        b.textContent = tr(text);
        b.setAttribute('aria-pressed', String(historyView.mode === key));
        modes.append(b);
      }
      tools.append(modes, historyIcon('恢复为当前版本', 'desktop-action-restore', () => {
        void Memorive.sessionsBridge.call('library.restore_refinement_edits', { artifact_id:review.artifact_id, number:chosen })
          .then((receipt) => {
            if (shownReview !== review) return;
            shownReview = { ...review, edits:receipt.edits, history:receipt.history };
            historyView.chosen = null;
            drawBrief();
            void drawEditHistory();
            showToast('已恢复，并保存为新版本');
          })
          .catch((error) => showToast(`没有保存：${error?.message || error}`));
      }));
    }
    bar.append(title, tools);
    const body = [];
    if (diff) {
      body.push(...(H.unchanged(diff.blocks)
        ? [Object.assign(document.createElement('p'), { className:'memo-history-label', textContent:tr('与当前版本内容相同') })]
        : [H.legend(tr('只在此版本') + ' · v' + chosen, tr('只在当前版本')), H.diffText(diff.blocks)]));
    } else {
      const full = document.createElement('div');
      full.className = 'desktop-sessions-edit-history-full';
      full.append(briefRows(shownBrief, version.edits, false));
      body.push(full);
    }
    panel.replaceChildren(list, bar, ...body);
    panel.hidden = false;
    q('#sessions-refinement-items').hidden = true;
  };

  // The shown version's library product: the arrow opens it, the edits are kept there.
  const loadVersionReview = (draft) => {
    const draftId = draft?.draft_id;
    void Memorive.sessionsBridge?.call('library.refinement_review', { draft_id:draftId })
      .then((review) => {
        if (shownDraftId !== draftId || review?.status !== 'PASS') return;
        shownReview = review;
        q('#sessions-open-library').hidden = false;
        drawBrief();
        void drawEditHistory();
      })
      .catch(() => undefined);   // not in the library (yet): no arrow, no choices
  };

  const showSessionView = (view) => {
    sessionView = view;
    q('#sessions-turns').hidden = view === 'brief';
    q('#sessions-refinement-draft').hidden = view !== 'brief';
    for (const [id, on] of [['#sessions-show-turns', view !== 'brief'], ['#sessions-show-brief', view === 'brief']]) {
      const tab = q(id);
      tab.setAttribute('aria-selected', String(on));
      tab.setAttribute('aria-pressed', String(on));
      tab.tabIndex = on ? 0 : -1;
    }
  };

  // The turn of a cited message in 原对话, scrolled to and outlined for a moment.
  const locateTurn = (ref) => {
    const turn = q(`#sessions-turns [data-ref="${ref}"]`);
    if (!turn) return;
    showSessionView('turns');
    turn.scrollIntoView({ block:'center', behavior:reducedMotion.matches ? 'auto' : 'smooth' });
    turn.classList.add('is-cited');
    window.setTimeout(() => turn.classList.remove('is-cited'), 2400);
  };

  // A cited message, shown under the item as this version read it (a second press closes it). When this version
  // read the conversation as it is now, 在原对话中定位 jumps to the turn.
  const showCitedMessage = (button) => {
    const ref = button.dataset.ref;
    const row = button.closest('.desktop-sessions-refinement-item');
    const open = row.querySelector(`.desktop-sessions-refinement-quote[data-ref="${ref}"]`);
    if (open) {
      open.remove();
      return;
    }
    const quote = shownBriefQuotes[ref];
    if (!quote) return;
    const block = document.createElement('blockquote');
    block.className = 'desktop-sessions-refinement-quote';
    block.dataset.ref = ref;
    const current = shownRefinementCurrent && q(`#sessions-turns [data-ref="${ref}"]`);
    const who = document.createElement('small');
    who.textContent = current ? (quote.role === 'user' ? '原话 · 你' : '原话 · AI')
      : (quote.role === 'user' ? '这一版读到的原话 · 你' : '这一版读到的原话 · AI');
    const text = document.createElement('p');
    text.textContent = String(quote.text || '');
    block.append(who, text);
    if (current) {
      const actions = document.createElement('div');
      actions.className = 'desktop-sessions-refinement-refs';
      actions.append(pill('在原对话中定位', { locate:ref }));
      block.append(actions);
    }
    row.append(block);
  };

  // Another refinement of the conversation, from the ‹ › switcher.
  const showRefinementRun = (jobId) => {
    void Memorive.sessionsBridge?.call('sessions.refinement_get', { local_projection_id:selectedProjectionId, job_id:jobId })
      .then((projection) => renderRefinementDraft(projection, true))
      .catch((error) => showToast(Memorive.refinementStatus.failureText(error?.message)));
  };

  // A saved version of the session's refinement (sessions.refinement_get: draft, versions), whatever the state of
  // a newer one. open: switch to it (a refinement just finished, or another version was picked).
  const renderRefinementDraft = (projection, open = false) => {
    const draft = projection?.draft;
    const brief = draft?.schema_version === 'DesktopSessionRefinementDraft-v4' ? draft.brief : null;
    // A draft made before v2.00 (v3) lists one typed item per message.
    const subjectOutput = draft?.subject_output;
    if (
      draft?.local_projection_id !== selectedProjectionId
      || !brief && (subjectOutput?.schema_version !== 'CONVERSATION_REFINEMENT_SUBJECT_OUTPUT_V1'
        || !Array.isArray(subjectOutput?.items))
    ) return false;
    const versions = Array.isArray(projection?.versions) ? projection.versions : [];
    shownRefinementCurrent = projection?.source_changed === false && versions.at(-1)?.job_id === draft.task_id;
    shownBriefQuotes = brief?.quotes || {};
    shownBrief = brief;
    if (draft.draft_id !== shownDraftId) {   // another version: its product and choices are looked up again
      shownDraftId = draft.draft_id;
      shownReview = null;
      editing = null;
      Object.assign(historyView, { open:false, chosen:null, mode:'compare' });
      void drawEditHistory();
      q('#sessions-open-library').hidden = true;
      loadVersionReview(draft);
    }
    // The refinements of this conversation switch like Research Chat's answers: ‹ n / m ›; the counter's tooltip
    // says when the shown one was made.
    const shownIndex = versions.findIndex((row) => row.job_id === draft.task_id);
    const runs = q('#sessions-refinement-runs');
    runs.replaceChildren(...(versions.length > 1 ? Memorive.versionHistory.switcher(shownIndex, versions.length, {
      labels:['上一次精炼', '下一次精炼'],
      pick:(to) => showRefinementRun(versions[to].job_id)
    }) : []));
    const counter = runs.querySelector('.memo-answer-counter');
    if (counter) counter.dataset.desktopTip = Memorive.versionHistory.when(draft.created_at);
    q('#sessions-view-switch').hidden = false;
    showSessionView(open ? 'brief' : sessionView);
    const target = q('#sessions-refinement-items');
    if (brief) {
      drawBrief();
      return true;
    }
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
    const queued = ['INBOX_QUEUED', 'TASK_CREATED', 'TASK_QUEUED'].includes(status);
    if (queued || ['INBOX_PROCESSING', 'TASK_RUNNING'].includes(status)) {
      activeRefinementJobId = jobId;
      button.disabled = true;
      button.textContent = '请在收件箱中查看';
      // A queued refinement starts only while the inbox runs tasks automatically.
      setRefineState(!queued ? '精炼中，可在收件箱查看进度'
        : projection?.auto_run === false ? '已加入队列；收件箱的自动处理已关闭，请在收件箱开启' : '已加入队列');
      renderRefinementDraft(projection);   // the newest version stays readable meanwhile
      return true;
    }
    if (status === 'PASS') {
      const finished = activeRefinementJobId !== null;
      activeRefinementJobId = null;
      // New messages since the newest version: it can be refined again (the versions stay readable).
      button.disabled = projection?.source_changed ? refinementStartDisabled() : true;
      button.textContent = projection?.source_changed ? '精炼对话' : '精炼完成';
      // The greyed-out button says why it cannot be pressed (the page's own tooltip; titles are removed).
      if (projection?.source_changed) delete button.dataset.desktopTip;
      else button.dataset.desktopTip = '这段会话已经精炼过；会话有新内容后，可以再次精炼';
      if (!renderRefinementDraft(projection, finished)) return false;
      const items = projection.draft.brief ? null : projection.draft.subject_output.items.length;
      setRefineState(projection.source_changed ? '会话有新内容，可重新精炼'
        : items === null ? '纪要已生成，请在资料库打开并确认复核' : `已生成 ${items} 条，待复核`);
      return true;
    }
    if (['TASK_FAILED', 'INBOX_FAILED', 'TASK_CANCELLED'].includes(status)) {
      activeRefinementJobId = null;
      button.disabled = refinementStartDisabled();
      button.textContent = '精炼对话';
      renderRefinementDraft(projection);   // an earlier version, if any, stays readable
      if (status === 'TASK_CANCELLED') {
        setRefineState('精炼任务已取消，可重新精炼。');
      } else {
        // The sentence says why and what to do; the error code is the tooltip.
        const errorCode = projection?.task?.error_code || projection?.inbox_item?.error?.code || projection?.error_code || '';
        setRefineState(Memorive.refinementStatus.failureText(errorCode), { title:errorCode });
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
    const bridge = Memorive.sessionsBridge;
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
    Memorive.sessionNavTarget = projectionId;
    q('#sessions-source').value = 'all';
    q('#sessions-search').value = '';
    if (!focusProjection(projectionId)) void Memorive.sessionsBridge?.openProjection(projectionId);
    else Memorive.sessionNavTarget = '';
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
    const bridge = Memorive.sessionsBridge;
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
    const bridge = Memorive.sessionsBridge;
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
    void Memorive.sessionsBridge?.bulkDelete(ids)
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
  q('#sessions-show-turns').addEventListener('click', () => showSessionView('turns'));
  q('#sessions-show-brief').addEventListener('click', () => showSessionView('brief'));
  // #n and 在原对话中定位, in the items and in 历史版本's full text alike.
  q('#sessions-refinement-draft').addEventListener('click', (event) => {
    const cited = event.target.closest('button[data-ref]');
    if (cited) showCitedMessage(cited);
    const locate = event.target.closest('button[data-locate]');
    if (locate) locateTurn(locate.dataset.locate);
  });
  q('#sessions-refinement-items').addEventListener('click', (event) => {
    const action = event.target.closest('button[data-act]');
    if (!action || !shownReview?.artifact_id) return;
    const index = Number(action.dataset.item);
    const act = action.dataset.act;
    if (act === 'text' || act === 'answer') {
      editing = { index, field:act };
      drawBrief();
      q('#sessions-refinement-items .desktop-sessions-refinement-editor textarea')?.focus();
      return;
    }
    if (act === 'cancel') {
      editing = null;
      drawBrief();
      return;
    }
    if (act === 'save') {
      const value = action.closest('.desktop-sessions-refinement-editor').querySelector('textarea').value.trim();
      if (!value) {
        showToast('内容为空，没有保存');
        return;
      }
      action.disabled = true;
      void saveItemEdit(index, { [editing.field]:value });
      return;
    }
    action.disabled = true;
    void saveItemEdit(index, {
      adopt:{ adopted:true }, unadopt:{ adopted:null }, unanswer:{ answer:null }, untext:{ text:null }
    }[act]);
  });
  q('#sessions-edit-history').addEventListener('click', () => {
    historyView.open = !historyView.open;
    historyView.chosen = null;
    void drawEditHistory();
  });
  q('#sessions-open-library').addEventListener('click', () => {
    if (!shownReview?.stable_locator) return;
    void Memorive.libraryBridge?.openArtifactByLocator(shownReview.stable_locator)
      .catch((error) => showToast(`没有在资料库打开：${error?.message || error}`));
  });
  q('#sessions-refine').addEventListener('click', () => {
    if (!selectedProjectionId || q('#sessions-refine').disabled) return;
    const button = q('#sessions-refine');
    button.disabled = true;
    button.textContent = '精炼中…';
    void Memorive.sessionsBridge?.refine(selectedProjectionId)
      .then((receipt) => {
        if (['INBOX_QUEUED', 'TASK_QUEUED', 'TASK_RUNNING'].includes(receipt?.status)) {
          activeRefinementJobId = receipt.job_id || 'inbox';
          button.disabled = true;
          button.textContent = '请在收件箱中查看';
          showToast(receipt?.deduplicated
            ? '该会话已有活动精炼任务，未重复创建。'
            : '已加入队列');
          document.querySelector('.desktop-nav-item[data-route="inbox"]')?.click();
        } else if (['TASK_SUCCEEDED', 'TASK_FAILED', 'TASK_CANCELLED'].includes(receipt?.status)) {
          activeRefinementJobId = receipt.job_id || 'terminal';
          button.disabled = true;
          button.textContent = receipt.status === 'TASK_SUCCEEDED' ? '精炼完成' : '任务已结束';
          showToast('该会话已有精炼任务，未重复创建。');
        }
      })
      .catch((error) => showToast(Memorive.refinementStatus.failureText(error?.code || error?.message)))
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
    void Memorive.sessionsBridge?.importBrowserSessionsFile()
      .catch((error) => showToast(`网页会话导入失败：${error?.message || error}`))
      .finally(() => { button.disabled = false; });
  });
  q('#sessions-capture-current-page').addEventListener('click', () => {
    const button = q('#sessions-capture-current-page');
    button.disabled = true;
    void Memorive.sessionsBridge?.captureCurrentPage()
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
    void Memorive.sessionsBridge?.deleteLocal(projectionId)
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
  document.querySelectorAll('.desktop-nav-item').forEach((item) => item.addEventListener('click', () => {
    if (item.dataset.route !== 'sessions') closeDetail({ immediate:true });
  }, true));
  // This page shows details in the middle: the right panel button is off here, with its own label.
  Memorive.frames.sessions.rightButtonUnavailable('本页完整详情使用中央宽阅读区');
  root.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !q('#sessions-searchbox').hidden) {
      event.preventDefault();
      q('#sessions-search-clear').click();
    }
  }, true);

  Memorive.sessionsView = Object.freeze({
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

