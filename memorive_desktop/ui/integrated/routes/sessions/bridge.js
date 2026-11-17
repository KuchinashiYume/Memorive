(() => {
  'use strict';

  const methods = Object.freeze([
    'sessions.get_contract', 'sessions.bootstrap', 'sessions.list', 'sessions.get',
    'sessions.get_provider_capabilities', 'sessions.capture_intent',
    'sessions.capture_progress', 'sessions.capture_cancel', 'sessions.capture_retry',
    'sessions.refine', 'sessions.refinement_settings_get', 'sessions.refinement_settings_save', 'sessions.refinement_get',
    'sessions.delete_local', 'sessions.bulk_delete_local',
    'sessions.refresh', 'sessions.resolve_locator', 'sessions.inject_fault',
    'sessions.restore', 'sessions.effect_metrics'
  ]);
  let receiptCount = 0;
  let bootstrapPromise = null;
  let assessedSourceCount = 0;
  let webRefreshPromise = null;
  let currentPageCapturePromise = null;
  const refinementWatchers = new Map();
  const ALL_SOURCE_PAGE_SIZE = 30;
  let listGeneration = 0;
  let paging = {
    generation:0,
    filterKey:'',
    filters:null,
    nextOffset:null,
    hasMore:false,
    loading:false
  };

  const page = () => window.__Desktop_SESSION_PAGE__;
  const nativeCall = async (method, params = {}) => {
    if (!window.pywebview?.api?.call) throw new Error('SESSIONS_NATIVE_TRANSPORT_UNAVAILABLE');
    const result = await (window.DesktopDecorations?.trackCall(method, () => window.pywebview.api.call(method, params)) || window.pywebview.api.call(method, params));
    receiptCount += 1;
    window.dispatchEvent(new CustomEvent('desktop:sessions-receipt', { detail:{ method, result } }));
    return result;
  };

  const normalizeFilters = (filters = {}) => ({
    source:filters.source || 'all',
    search:filters.search || '',
    limit_per_source:normalizeLimit(filters.limit_per_source)
  });
  const filterKey = (filters) => JSON.stringify([
    filters.source,
    filters.search,
    filters.limit_per_source
  ]);
  const pagedParams = (filters, offset = 0) => filters.source === 'all'
    ? { ...filters, page_offset:offset, page_size:ALL_SOURCE_PAGE_SIZE }
    : { ...filters };
  const renderList = (projection, { append = false } = {}) => {
    const rows = Array.isArray(projection?.records)
      ? projection.records
      : (Array.isArray(projection?.rows) ? projection.rows : []);
    const totalCount = Number.isFinite(projection?.total_count)
      ? projection.total_count
      : (Number.isFinite(projection?.record_count) ? projection.record_count : rows.length);
    const options = {
      totalCount,
      pageOffset:Number.isFinite(projection?.page_offset) ? projection.page_offset : 0,
      pageSize:Number.isFinite(projection?.page_size) ? projection.page_size : rows.length,
      hasMore:projection?.has_more === true,
      nextOffset:Number.isFinite(projection?.next_offset) ? projection.next_offset : null
    };
    const codexStatus = projection?.source_status?.codex;
    if (!rows.length && codexStatus?.isolated_source === true) {
      options.emptyMessage = '当前使用隔离会话数据';
      options.emptyHelper = `Codex 来源：${codexStatus.resolved_source_root || '<LOCAL>'} · ${codexStatus.status || 'SOURCE_EMPTY'}`;
    } else if (!rows.length && codexStatus?.status && codexStatus.status !== 'PASS') {
      const statusCopy = {
        SOURCE_DIRECTORY_MISSING:'Codex 目录不存在',
        SOURCE_DATABASE_MISSING:'Codex 会话数据库不存在',
        SOURCE_EMPTY:'Codex 会话源为空',
        SOURCE_PARSE_FAILED:'Codex 会话源解析失败',
        SOURCE_PERMISSION_DENIED:'没有权限读取 Codex 会话源'
      };
      options.emptyMessage = statusCopy[codexStatus.status] || '没有符合条件的会话';
      options.emptyHelper = `来源：${codexStatus.resolved_source_root || '<LOCAL>'}`;
    }
    return append
      ? page()?.appendRows(rows, options)
      : page()?.replaceRows(rows, options);
  };
  const acceptFirstPage = (projection, filters, generation) => {
    if (generation !== listGeneration) return { status:'STALE_LIST_PROJECTION_IGNORED' };
    const rendered = renderList(projection) || {};
    paging = {
      generation,
      filterKey:filterKey(filters),
      filters:{ ...filters },
      nextOffset:Number.isFinite(projection?.next_offset) ? projection.next_offset : null,
      hasMore:filters.source === 'all' && projection?.has_more === true,
      loading:false
    };
    return rendered;
  };

  const detailErrorCopy = (error) => {
    const raw = String(error?.message || error || '');
    if (raw.includes('SESSION_SOURCE_RECORD_MALFORMED_JSONL')) {
      return '源会话文件包含损坏记录，无法完整读取。';
    }
    if (raw.includes('SESSION_SOURCE_RECORD_CHANGED_DURING_READ')) {
      return '源文件读取期间发生变化，请重试。';
    }
    if (raw.includes('DEEPSEEK_VISIBLE_UI_CONVERSATION_INCOMPLETE')) {
      return '会话未完整读取，请重新同步。';
    }
    if (raw.includes('DEEPSEEK_VISIBLE_UI_FULL_SCROLL_PROOF_MISSING')) {
      return '会话未完整读取，请重新同步。';
    }
    if (raw.includes('BROWSER_SESSION_INCOMPLETE')) {
      return '会话未完整读取，请重新同步。';
    }
    if (raw.includes('BROWSER_SESSION_CAPTURE_FAILED')) {
      return '会话正文同步失败，请重新同步。';
    }
    return `读取会话正文失败：${raw || '未知错误'}`;
  };

  const syncBrowserSessionsFromDownloads = async () => {
    const action = window.pywebview?.api?.sync_browser_sessions_from_downloads;
    if (typeof action !== 'function') return null;
    const receipt = await action();
    receiptCount += 1;
    return receipt;
  };
  const normalizeLimit = (value) => {
    const parsed = Number(value);
    return [10, 30, 50].includes(parsed) ? parsed : 30;
  };
  const wait = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  const runBrowserCompanionSync = async () => {
    const action = window.pywebview?.api?.run_browser_companion_sync;
    if (typeof action !== 'function') throw new Error('BROWSER_COMPANION_SYNC_UNAVAILABLE');
    const receipt = await action();
    receiptCount += 1;
    return receipt;
  };
  const notifyBrowserCompanionUpdated = (projection) => {
    page()?.setBrowserCompanionStatus(projection);
    window.dispatchEvent(new CustomEvent('desktop:browser-companion-updated', { detail:projection }));
  };
  const refreshWebSessions = async (initialStatus) => {
    if (webRefreshPromise) return webRefreshPromise;
    webRefreshPromise = (async () => {
      if (initialStatus?.preflight_eligible !== true) {
        return { triggered:false, completed:false, status:initialStatus, imported_session_count:0 };
      }
      const previousReportSha = initialStatus?.latest_sync_report_sha256 || null;
      const launch = await runBrowserCompanionSync();
      const accepted = new Set([
        'SYNC_TRIGGERED', 'SYNC_READY',
        'INCREMENTAL_SYNC_TRIGGERED', 'INCREMENTAL_SYNC_READY'
      ]);
      if (!accepted.has(launch?.status)) {
        const status = await browserCompanionStatus();
        notifyBrowserCompanionUpdated(status);
        return { triggered:false, completed:false, status, imported_session_count:0 };
      }
      let imported = 0;
      let latest = initialStatus;
      for (let attempt = 0; attempt < 240; attempt += 1) {
        if (attempt) await wait(1500);
        const importReceipt = await syncBrowserSessionsFromDownloads().catch(() => null);
        imported += Number(importReceipt?.imported_session_count) || 0;
        latest = await browserCompanionStatus();
        notifyBrowserCompanionUpdated(latest);
        const nextReportSha = latest?.latest_sync_report_sha256 || null;
        if (nextReportSha && nextReportSha !== previousReportSha) {
          return { triggered:true, completed:true, status:latest, imported_session_count:imported, launch };
        }
      }
      return { triggered:true, completed:false, status:latest, imported_session_count:imported, launch };
    })().finally(() => { webRefreshPromise = null; });
    return webRefreshPromise;
  };

  const bootstrap = async () => {
    if (!bootstrapPromise) {
      const filters = normalizeFilters({ source:'all', search:'', limit_per_source:30 });
      const generation = ++listGeneration;
      bootstrapPromise = syncBrowserSessionsFromDownloads().catch(() => null).then(
        () => nativeCall('sessions.bootstrap', {})
      ).then(async (projection) => {
        const [browserStatus, refinementSettings] = await Promise.all([
          browserCompanionStatus(),
          nativeCall('sessions.refinement_settings_get', {})
        ]);
        page()?.setBrowserCompanionStatus(browserStatus);
        page()?.setRefinementConfiguration(refinementSettings);
        assessedSourceCount = Number(projection.real_source_channels_assessed) || 0;
        const rendered = acceptFirstPage(projection, filters, generation) || {};
        return {
          schema_version:'SessionsSessionsBridgeBootstrap-v2',
          records:projection.records,
          record_count:projection.record_count,
          binding_count:rendered.record_count || 0,
          dom_binding_count:page()?.inspect().record_count || 0,
          source_count:projection.source_count,
          source_access_envelope_status:projection.source_access_envelope_status,
          real_source_channels_assessed:projection.real_source_channels_assessed,
          synthetic_only: false,
          status:'PASS'
        };
      }).catch((error) => {
        bootstrapPromise = null;
        page()?.replaceRows([], { error:`读取本机会话失败：${String(error?.message || error)}` });
        throw error;
      });
    }
    return bootstrapPromise;
  };

  const query = async (filters = {}) => {
    const normalized = normalizeFilters(filters);
    const generation = ++listGeneration;
    try {
      const projection = await nativeCall('sessions.list', pagedParams(normalized));
      acceptFirstPage(projection, normalized, generation);
      return projection;
    } catch (error) {
      if (generation === listGeneration) {
        page()?.replaceRows([], { error:`筛选本机会话失败：${String(error?.message || error)}` });
      }
      throw error;
    }
  };

  const loadNextPage = async () => {
    if (!paging.hasMore || paging.loading || !paging.filters || paging.nextOffset === null) {
      return { status:'NO_NEXT_SESSION_PAGE' };
    }
    const request = { ...paging.filters };
    const generation = paging.generation;
    const offset = paging.nextOffset;
    paging.loading = true;
    try {
      const projection = await nativeCall('sessions.list', pagedParams(request, offset));
      if (generation !== listGeneration || paging.filterKey !== filterKey(request)) {
        return { status:'STALE_SESSION_PAGE_IGNORED' };
      }
      const rendered = renderList(projection, { append:true }) || {};
      paging = {
        ...paging,
        nextOffset:Number.isFinite(projection?.next_offset) ? projection.next_offset : null,
        hasMore:projection?.has_more === true,
        loading:false
      };
      return { ...projection, binding_count:rendered.record_count || 0 };
    } catch (error) {
      if (generation === listGeneration) paging.loading = false;
      throw error;
    }
  };

  const loadDetail = async (localProjectionId) => {
    try {
      const projection = await nativeCall('sessions.get', { local_projection_id:localProjectionId });
      page()?.renderDetail(projection);
      try {
        const refinement = await nativeCall('sessions.refinement_get', { local_projection_id:localProjectionId });
        page()?.renderRefinementTaskState(refinement);
        if (['TASK_QUEUED', 'TASK_RUNNING'].includes(refinement?.status) && refinement?.task?.job_id) {
          void watchRefinementTask(localProjectionId, refinement.task.job_id);
        }
      } catch (error) {
        if (!String(error?.message || error).includes('SESSION_REFINEMENT_DRAFT_NOT_FOUND')) throw error;
      }
      return projection;
    } catch (error) {
      page()?.renderDetailError(localProjectionId, detailErrorCopy(error));
      throw error;
    }
  };

  const openProjection = async (localProjectionId) => {
    const projectionId = String(localProjectionId || '').trim();
    if (!projectionId) throw new Error('SESSION_STABLE_ID_REQUIRED');
    try {
      const projection = await nativeCall('sessions.get', {
        local_projection_id:projectionId
      });
      if (!page()?.ensureRecord(projection?.session)) {
        throw new Error('SESSION_DETAIL_PROJECTION_INVALID');
      }
      window.__Desktop_SESSION_NAV_TARGET__ = '';
      page()?.focusProjection(projectionId);
      page()?.renderDetail(projection);
      return projection;
    } catch (error) {
      page()?.showToast(
        '来源会话已不在当前读取范围。来源快照仍保留；可重新导入该会话，或继续仅保留快照。'
      );
      throw error;
    }
  };

  const refresh = async (filters = {}) => {
    const normalized = normalizeFilters(filters);
    const generation = ++listGeneration;
    try {
      const initialImportReceipt = await syncBrowserSessionsFromDownloads();
      const initialStatus = await browserCompanionStatus();
      notifyBrowserCompanionUpdated(initialStatus);
      const webReceipt = await refreshWebSessions(initialStatus);
      const projection = await nativeCall('sessions.refresh', pagedParams(normalized));
      assessedSourceCount = Object.values(projection.source_status || {}).filter((value) => value?.available).length;
      acceptFirstPage(projection, normalized, generation);
      const finalStatus = webReceipt?.status || await browserCompanionStatus();
      notifyBrowserCompanionUpdated(finalStatus);
      const imported = (Number(initialImportReceipt?.imported_session_count) || 0)
        + (Number(webReceipt?.imported_session_count) || 0);
      page()?.showToast(imported
        ? `网页增量同步完成，已合并 ${imported} 条；当前显示 ${projection.record_count || 0} 条。`
        : webReceipt?.triggered && !webReceipt?.completed
          ? '后台同步中'
          : webReceipt?.completed
            ? `网页增量同步完成；当前显示 ${projection.record_count || 0} 条。`
            : `已刷新 ${projection.record_count || 0} 条本地会话；网页桥接尚未连接。`);
      return projection;
    } catch (error) {
      page()?.showToast(`刷新会话失败：${String(error?.message || error)}`);
      throw error;
    }
  };

  const refreshLocalSessions = async (filters = {}) => {
    const normalized = normalizeFilters(filters);
    const generation = ++listGeneration;
    try {
      const projection = await nativeCall('sessions.refresh', pagedParams(normalized));
      assessedSourceCount = Object.values(projection.source_status || {})
        .filter((value) => value?.available).length;
      acceptFirstPage(projection, normalized, generation);
      const codexCount = Number(projection.source_status?.codex?.record_count) || 0;
      const claudeCount = Number(projection.source_status?.claude?.record_count) || 0;
      const claudeCopy = claudeCount
        ? `Claude Code ${claudeCount} 条`
        : 'Claude Code 未发现本地会话';
      page()?.showToast(`本地同步完成：Codex ${codexCount} 条；${claudeCopy}。`);
      return projection;
    } catch (error) {
      page()?.showToast(`本地会话同步失败：${String(error?.message || error)}`);
      throw error;
    }
  };

  const browserCompanionStatus = async () => {
    const action = window.pywebview?.api?.browser_companion_status;
    if (typeof action !== 'function') throw new Error('BROWSER_COMPANION_STATUS_UNAVAILABLE');
    const projection = await action();
    receiptCount += 1;
    return projection;
  };

  const installBrowserCompanion = async () => {
    throw new Error('BROWSER_COMPANION_SETUP_MOVED_TO_REFINEMENT_SETTINGS');
  };

  const importBrowserSessionsFile = async () => {
    const action = window.pywebview?.api?.import_browser_sessions_file;
    if (typeof action !== 'function') throw new Error('BROWSER_SESSION_FILE_IMPORT_UNAVAILABLE');
    try {
      const receipt = await action();
      receiptCount += 1;
      if (receipt?.status === 'CANCELLED') return receipt;
      await query(page()?.currentFilter?.() || {});
      page()?.setBrowserCompanionStatus(await browserCompanionStatus());
      page()?.showToast(`已导入 ${receipt.imported_count || 0} 条会话`);
      return receipt;
    } catch (error) {
      const raw = String(error?.message || error || '');
      if (raw.includes('BROWSER_SESSION_IMPORT_JSON_INVALID')) {
        throw new Error('选择的文件不是有效 JSON；请导入只读桥接扩展生成的会话包。');
      }
      if (raw.includes('BROWSER_SESSION_BUNDLE') || raw.includes('BROWSER_SESSION_CONVERSATION')) {
        throw new Error('会话包结构或版本不受支持；请重新加载最新版只读桥接扩展后再导出。');
      }
      if (raw.includes('BROWSER_SESSION_IMPORT_FILE_INVALID')) {
        throw new Error('请选择扩展导出的 .json 会话包。');
      }
      throw error;
    }
  };

  const universalSnapshot = async () => {
    const projection = await nativeCall('sessions.list', {
      source:'universal', search:'', limit_per_source:50
    });
    const rows = Array.isArray(projection?.records)
      ? projection.records
      : (Array.isArray(projection?.rows) ? projection.rows : []);
    return new Map(rows.map((row) => [
      String(row?.local_projection_id || ''),
      [row?.content_hash, row?.title, row?.summary, row?.ingested_at]
        .map((value) => String(value || ''))
        .join('|')
    ]).filter(([projectionId]) => projectionId));
  };

  const universalSnapshotChanged = (before, after) => (
    [...after].some(([projectionId, fingerprint]) => (
      !before.has(projectionId) || before.get(projectionId) !== fingerprint
    ))
  );

  const captureCurrentPage = async () => {
    if (currentPageCapturePromise) return currentPageCapturePromise;
    currentPageCapturePromise = (async () => {
      const before = await universalSnapshot();
      let importedCount = 0;
      let sawUnrelatedImport = false;
      page()?.showToast('请在目标 AI 网页点击浏览器工具栏中的 Memorive 扩展，再选择“录入当前界面”。');
      for (let attempt = 0; attempt < 60; attempt += 1) {
        if (attempt) await wait(1500);
        const receipt = await syncBrowserSessionsFromDownloads().catch(() => null);
        const importedNow = Number(receipt?.imported_session_count) || Number(receipt?.imported_count) || 0;
        importedCount += importedNow;
        sawUnrelatedImport ||= importedNow > 0;
        const after = await universalSnapshot();
        const universalCount = Number(receipt?.provider_counts?.universal) || 0;
        const universalImported = universalCount > 0
          || receipt?.provider_id === 'universal'
          || universalSnapshotChanged(before, after);
        if (universalImported) {
          await query(page()?.currentFilter?.() || {});
          page()?.showToast(`当前界面已作为 UN 通用网页会话录入，共合并 ${Math.max(1, importedCount)} 条。`);
          return { status:'PASS', provider_id:'universal', imported_count:Math.max(1, importedCount) };
        }
      }
      const status = sawUnrelatedImport ? 'NO_UNIVERSAL_CHANGE' : 'WAITING_USER_GESTURE';
      page()?.showToast(sawUnrelatedImport
        ? '没有新内容'
        : '尚未收到当前界面记录；请保持 Memorive 打开，并在目标网页的扩展弹窗中点击“录入当前界面”。');
      return { status, provider_id:'universal', imported_count:0 };
    })().finally(() => { currentPageCapturePromise = null; });
    return currentPageCapturePromise;
  };

  const deleteLocal = async (localProjectionId) => {
    const receipt = await nativeCall('sessions.delete_local', { local_projection_id:localProjectionId });
    await query(page()?.currentFilter?.() || {});
    return receipt;
  };

  const bulkDelete = async (localProjectionIds) => {
    const receipt = await nativeCall('sessions.bulk_delete_local', { local_projection_ids:localProjectionIds });
    await query(page()?.currentFilter?.() || {});
    return receipt;
  };

  const refine = async (localProjectionId) => {
    const receipt = await nativeCall('sessions.refine', { local_projection_id:localProjectionId });
    if (!['INBOX_QUEUED', 'TASK_QUEUED', 'TASK_RUNNING', 'TASK_SUCCEEDED', 'TASK_FAILED', 'TASK_CANCELLED'].includes(receipt?.status)) {
      throw new Error(`SESSION_REFINEMENT_NOT_QUEUED:${receipt?.status || 'UNKNOWN'}`);
    }
    page()?.renderRefinementTaskState({
      status:receipt.status,
      local_projection_id:localProjectionId,
      inbox_item:receipt.inbox_item_id ? { item_id:receipt.inbox_item_id, state:'QUEUED' } : null,
      task:receipt.job_id ? { ...receipt, control_state:receipt.status === 'TASK_RUNNING' ? 'RUNNING' : 'QUEUED' } : null
    });
    window.dispatchEvent(new CustomEvent('desktop:session-refinement-inbox-enqueued', { detail:receipt }));
    if (['INBOX_QUEUED', 'TASK_QUEUED', 'TASK_RUNNING'].includes(receipt?.status)) {
      void watchRefinementTask(localProjectionId, receipt.job_id || 'inbox');
    }
    return receipt;
  };

  const watchRefinementTask = (localProjectionId, jobId) => {
    const watcherKey = `${localProjectionId}:${jobId}`;
    if (refinementWatchers.has(watcherKey)) return refinementWatchers.get(watcherKey);
    const watcher = (async () => {
      let lastPublishedStatus = null;
      for (let attempt = 0; attempt < 2400; attempt += 1) {
        const projection = await nativeCall('sessions.refinement_get', {
          local_projection_id:localProjectionId
        });
        if (jobId !== 'inbox' && projection?.task?.job_id && projection.task.job_id !== jobId) {
          throw new Error('SESSION_REFINEMENT_TASK_ID_DRIFT');
        }
        page()?.renderRefinementTaskState(projection);
        if (projection?.status !== lastPublishedStatus) {
          lastPublishedStatus = projection?.status;
          window.dispatchEvent(new CustomEvent('desktop:session-refinement-task-updated', {
            detail:{ local_projection_id:localProjectionId, job_id:jobId, status:projection?.status }
          }));
        }
        if (projection?.status === 'PASS') {
          return projection;
        }
        if (['TASK_FAILED', 'TASK_CANCELLED'].includes(projection?.status)) return projection;
        await wait(750);
      }
      const timeout = { status:'TASK_MONITOR_TIMEOUT', local_projection_id:localProjectionId, job_id:jobId };
      page()?.renderRefinementTaskState(timeout);
      return timeout;
    })().catch((error) => {
      page()?.renderRefinementTaskState({
        status:'TASK_MONITOR_FAILED',
        local_projection_id:localProjectionId,
        job_id:jobId,
        error_code:String(error?.message || error)
      });
      return { status:'TASK_MONITOR_FAILED', job_id:jobId, error_code:String(error?.message || error) };
    }).finally(() => refinementWatchers.delete(watcherKey));
    refinementWatchers.set(watcherKey, watcher);
    return watcher;
  };

  window.__Desktop_SESSIONS_BRIDGE__ = Object.freeze({
    bootstrap, query, loadNextPage, loadDetail, openProjection, refresh, refreshLocalSessions, refine, deleteLocal, bulkDelete,
    importBrowserSessionsFile, captureCurrentPage, browserCompanionStatus, installBrowserCompanion, call:nativeCall,
    inspect:() => ({
      method_count:methods.length,
      methods:[...methods],
      binding_count:page()?.inspect().record_count || 0,
      receipt_count:receiptCount,
      selected_projection_id:page()?.selectedId() || null,
      native_transport:Boolean(window.pywebview?.api?.call),
      real_source_channels_assessed:assessedSourceCount,
      page_has_more:paging.hasMore,
      page_next_offset:paging.nextOffset,
      page_loading:paging.loading,
      remote_session_mutations:0,
      synthetic_only: false
    })
  });

  const start = () => { if (window.pywebview?.api?.call) void bootstrap(); };
  let focusRefreshRunning = false;
  window.addEventListener('focus', async () => {
    if (!window.pywebview?.api?.call || focusRefreshRunning) return;
    focusRefreshRunning = true;
    try {
      const imported = await syncBrowserSessionsFromDownloads().catch(() => null);
      page()?.setBrowserCompanionStatus(await browserCompanionStatus());
      if (Number(imported?.imported_session_count) > 0) {
        await query(page()?.currentFilter?.() || {});
      }
    } finally {
      focusRefreshRunning = false;
    }
  });
  window.addEventListener('pywebviewready', start, { once:true });
  if (window.pywebview?.api?.call) start();
})();

