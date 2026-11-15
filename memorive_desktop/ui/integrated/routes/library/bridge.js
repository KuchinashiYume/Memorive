(() => {
  'use strict';

  const methods = Object.freeze([
    'library.get_contract',
    'library.bootstrap',
    'library.list_artifacts',
    'library.get_artifact',
    'library.get_artifact_bindings',
    'library.get_lineage',
    'library.get_status_counts',
    'library.set_view',
    'library.select',
    'library.resolve_locator',
    'library.validate_external_view',
    'library.open_artifact',
    'library.confirm_refinement_review',
    'library.confirm_core_review',
    'library.refresh',
    'library.inject_fault',
    'library.restore',
    'library.effect_metrics'
  ]);
  const methodSet = new Set(methods);
  const bindings = new Map();
  const receipts = [];
  let selectedArtifactId = null;
  let bootstrapPromise = null;
  const root = document.getElementById('desktop-library-preview');
  root?.addEventListener('desktop:inspector-closed', () => { selectedArtifactId = null; });

  const nativeCall = async (method, params = {}) => {
    if (!methodSet.has(method)) throw new Error(`LIBRARY_METHOD_NOT_ALLOWLISTED:${method}`);
    let result;
    if (!window.pywebview?.api?.call) {
      result = {
        schema_version: 'LibraryBrowserOnlyReceipt-v1',
        method,
        records: [],
        record_count: 0,
        catalog_count: 0,
        status: 'BROWSER_TEST_NO_NATIVE_TRANSPORT'
      };
    } else {
      result = await (window.DesktopDecorations?.trackCall(method, () => window.pywebview.api.call(method, params)) || window.pywebview.api.call(method, params));
    }
    receipts.push({ method, result });
    if(method === 'library.select' && result?.status === 'PASS' && result.artifact_id === selectedArtifactId)window.dispatchEvent(new CustomEvent('desktop:library-selection',{detail:{record:bindings.get(result.artifact_id)?.record || null}}));
    window.dispatchEvent(new CustomEvent('desktop:library-receipt', { detail: { method, result } }));
    return result;
  };

  const bindRows = (projection) => {
    const rows = Array.isArray(projection?.records) ? projection.records : [];
    window.__Desktop_LIBRARY_VIEW__?.renderRows(projection.render_records || rows, {append:projection.append === true, totalCount:projection.total_count, paginated:projection.paginated === true});
    bindings.clear();
    const byId = new Map(rows.map((row) => [row.artifact_id, row]));
    document.querySelectorAll('#desktop-library-preview .desktop-library-card[data-id]').forEach((card) => {
      const artifactId = card.dataset.id;
      if (!artifactId) return;
      bindings.set(artifactId, { card, record: byId.get(artifactId) || null });
    });
    if (selectedArtifactId && bindings.has(selectedArtifactId)) {
      window.__Desktop_LIBRARY_VIEW__?.openDetailById(selectedArtifactId);
    } else if (selectedArtifactId) {
      selectedArtifactId = null;
      window.__Desktop_LIBRARY_VIEW__?.closeDetail?.();
    }
    window.dispatchEvent(new CustomEvent('desktop:library-selection',{detail:{record:bindings.get(selectedArtifactId)?.record || null}}));
    return rows;
  };

  const PAGE_SIZE = 30;
  let generation = 0;
  let paging = { page:0, total:0, hasMore:false, loading:false, query:null };
  let refreshPromise = null;
  let allRows = [];
  const query = () => window.__Desktop_LIBRARY_VIEW__?.getQuery?.() || { view:'literature', status_filter:'all', kind_filter:'all' };
  const showPage = (projection, append) => {
    const incoming = projection.rows || [];
    const known = new Set(append ? allRows.map(row => row.artifact_id) : []);
    const newRows = incoming.filter(row => !known.has(row.artifact_id));
    allRows = [...new Map([...(append ? allRows : []), ...incoming].map(row => [row.artifact_id, row])).values()];
    const list = document.querySelector('#library-list');
    const pane = list?.closest('.desktop-library-main-scroll') || list?.parentElement;
    const top = pane?.scrollTop || 0;
    bindRows({records:allRows, render_records:append ? newRows : allRows, append, total_count:projection.total_count, paginated:true});
    if (pane) pane.scrollTop = append ? top : 0;
  };
  const requestPage = async (page, append, request, epoch) => {
    paging.loading = true;
    try {
      const projection = await nativeCall('library.list_artifacts', {...request, page, page_size:PAGE_SIZE});
      if (epoch !== generation) return {status:'SUPERSEDED'};
      paging = {page, total:projection.total_count || 0,
        hasMore:page * PAGE_SIZE < (projection.total_count || 0), loading:false, query:request};
      showPage(projection, append);
      return projection;
    } finally {
      if (epoch === generation) paging.loading = false;
    }
  };
  const refresh = () => {
    const request = query();
    if (refreshPromise && JSON.stringify(request) === JSON.stringify(paging.query)) return refreshPromise;
    const epoch = ++generation;
    paging = {...paging, query:request, hasMore:false};
    const pending = requestPage(1, false, request, epoch);
    refreshPromise = pending;
    pending.finally(() => { if (refreshPromise === pending) refreshPromise = null; }).catch(() => {});
    return pending;
  };
  const loadNextPage = () => {
    if (!paging.hasMore || paging.loading) return Promise.resolve({status:'NO_NEXT_PAGE'});
    return requestPage(paging.page + 1, true, paging.query, generation);
  };
  const bootstrap = () => {
    if (!bootstrapPromise) bootstrapPromise = refresh().catch(error => { bootstrapPromise=null; throw error; });
    return bootstrapPromise;
  };
  const maybeLoadMore = () => {
    if (!root || root.hidden || !root.getClientRects().length || !paging.hasMore || paging.loading) return;
    const cards = root.querySelectorAll('.desktop-library-card');
    const marker = cards[Math.max(0, cards.length - 10)];
    const box = root.getBoundingClientRect();
    // A fast scroll can jump completely past the prefetch marker.
    if (marker && marker.getBoundingClientRect().top < Math.min(window.innerHeight, box.bottom)) {
      void loadNextPage().catch(error => showToast(`继续加载资料失败：${error.message}`));
    }
  };
  root?.addEventListener('scroll', maybeLoadMore, {capture:true, passive:true});
  root?.addEventListener('desktop:library-query', () => { void refresh().catch(error => showToast(`资料加载失败：${error.message}`)); });
  window.addEventListener('resize', maybeLoadMore);

  const selectedRecord = () => bindings.get(selectedArtifactId)?.record || null;
  const showToast = (copy) => window.__Desktop_LIBRARY_VIEW__?.showToast?.(copy);
  const activateRoute = (route, source) => {
    if (window.__Desktop_INTEGRATED_APP__?.activate) {
      window.__Desktop_INTEGRATED_APP__?.activate(route);
    } else {
      const page = {
        'current-task':'当前任务', sessions:'会话管理'
      }[route];
      if (page) root?.querySelector(`.desktop-nav-item[data-page="${page}"]`)?.click();
    }
    void window.__Desktop_PRODUCT_BRIDGE__?.dispatch(
      'navigation.select', { route }, source
    );
  };

  const openSourceSession = async () => {
    const projectionId = String(selectedRecord()?.source_session_id || '').trim();
    if (!projectionId) throw new Error('来源会话稳定身份缺失');
    activateRoute('sessions', 'library-source-session');
    const bridge = window.__Desktop_SESSIONS_BRIDGE__;
    if (!bridge?.openProjection) throw new Error('会话管理定位服务未就绪');
    return bridge.openProjection(projectionId);
  };

  const openRelatedTask = async () => {
    const record = selectedRecord();
    const taskId = String(record?.task_id || record?.task || record?.job_id || '').trim();
    if (!taskId) throw new Error('关联任务稳定身份缺失');
    activateRoute('current-task', 'library-related-task');
    const bridge = window.__Desktop_CURRENT_TASK_BRIDGE__;
    if (record?.product_kind === 'Core_ARTIFACT') {
      if (!bridge?.openProductionRun) throw new Error('文献处理任务定位服务未就绪');
      return bridge.openProductionRun(taskId);
    }
    if (!bridge?.openHistoricalRun) throw new Error('当前任务定位服务未就绪');
    return bridge.openHistoricalRun(taskId);
  };

  const locateCoreSource = async () => {
    if (!selectedArtifactId) throw new Error('请先选择文献记录');
    const action = window.pywebview?.api?.locate_library_core_source;
    if (typeof action !== 'function') throw new Error('来源文件定位服务未就绪');
    return action(selectedArtifactId);
  };

  const openCoreArtifact = async (artifactId = selectedArtifactId) => {
    const productArtifactId = String(artifactId || '').trim();
    if (!productArtifactId) throw new Error('文献处理产物身份缺失');
    const action = window.pywebview?.api?.open_library_core_artifact;
    if (typeof action !== 'function') throw new Error('文献产物打开服务未就绪');
    const receipt=await action(productArtifactId);
    await refresh();
    return receipt;
  };

  const openArtifactByLocator = async (stableLocator) => {
    const locator = String(stableLocator || '').trim();
    if (!locator) throw new Error('资料稳定定位符缺失');
    const receipt = await nativeCall('library.resolve_locator', { stable_locator:locator });
    if (receipt?.status !== 'PASS') throw new Error(receipt?.status || '资料定位失败');
    activateRoute('library', 'library-stable-locator');
    const targetArtifactId = receipt.artifact_id;
    window.__Desktop_LIBRARY_VIEW__?.setMode?.(receipt.view || 'literature');
    await refresh();
    // Stable locators must remain usable beyond the loaded first page.
    if (!bindings.has(targetArtifactId)) {
      const detail = await nativeCall('library.get_artifact', {artifact_id:targetArtifactId});
      allRows.push(detail.artifact);
      bindRows({records:allRows, total_count:paging.total, paginated:true});
    }
    // Changing Literature / Memorive Documents closes the old inspector, whose
    // close event clears selection. Bind the new target after that transition.
    selectedArtifactId = targetArtifactId;
    window.dispatchEvent(new CustomEvent('desktop:library-selection',{detail:{record:null}}));
    await nativeCall('library.select', { artifact_id:targetArtifactId });
    if (!window.__Desktop_LIBRARY_VIEW__?.openDetailById?.(targetArtifactId)) {
      throw new Error('资料记录已不存在或当前筛选不可见');
    }
    selectedArtifactId = targetArtifactId;
    return receipt;
  };

  const locateRefinementProduct = async () => {
    if (!selectedArtifactId) throw new Error('请先选择精炼成品');
    const action = window.pywebview?.api?.locate_library_refinement_artifact;
    if (typeof action !== 'function') throw new Error('文件管理器定位服务未就绪');
    return action(selectedArtifactId);
  };

  const openRefinementProduct = async () => {
    if (!selectedArtifactId) throw new Error('请先选择精炼成品');
    const action = window.pywebview?.api?.open_library_refinement_artifact;
    if (typeof action !== 'function') throw new Error('精炼成品打开服务未就绪');
    const receipt = await action(selectedArtifactId);
    await refresh();
    return receipt;
  };

  const confirmRefinementReview = async () => {
    if (!selectedArtifactId) throw new Error('请先选择精炼成品');
    const isPaper=bindings.get(selectedArtifactId)?.record?.product_kind==='Core_ARTIFACT';
    const receipt = await nativeCall(isPaper?'library.confirm_core_review':'library.confirm_refinement_review', {
      artifact_id:selectedArtifactId
    });
    await refresh();
    return receipt;
  };

  document.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const card = target.closest('#desktop-library-preview .desktop-library-card[data-id]');
    if (card) {
      const nextArtifactId = card.dataset.id || null;
      if (nextArtifactId) window.__Desktop_LIBRARY_VIEW__?.openDetailById(nextArtifactId);
      if (selectedArtifactId !== nextArtifactId) {
        selectedArtifactId = nextArtifactId;
        window.dispatchEvent(new CustomEvent('desktop:library-selection',{detail:{record:null}}));
        if (selectedArtifactId) {
          void nativeCall('library.select', { artifact_id: selectedArtifactId });
          void nativeCall('library.get_artifact', { artifact_id: selectedArtifactId });
        }
      }
    }
    if (target.closest('#library-external,.desktop-library-file-action') && selectedArtifactId) {
      event.preventDefault();
      event.stopImmediatePropagation();
      const record = bindings.get(selectedArtifactId)?.record;
      if (record?.product_kind === 'Core_ARTIFACT') {
        const file = target.closest('.desktop-library-file-action');
        const sourceRequested = file?.dataset.fileRole === 'source';
        const productArtifactId = String(file?.dataset.artifactId || '').trim();
        const operation = sourceRequested
          ? locateCoreSource()
          : openCoreArtifact(productArtifactId);
        void operation.catch((error) => showToast(
          `${sourceRequested ? '来源文件定位' : '文献产物打开'}失败：${error.message}`
        ));
      } else if (record?.direct_file_open_supported !== false) {
        void nativeCall('library.open_artifact', { artifact_id: selectedArtifactId });
      }
    }
    if (target.closest('#library-go-task')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      void openRelatedTask().catch((error) => showToast(`关联任务定位失败：${error.message}`));
    }
    if (target.closest('#library-go-session,#library-refinement-source-hash')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      void openSourceSession().catch((error) => showToast(
        `来源会话定位失败：${error.message}。已保留来源快照校验值，可重新导入或继续仅保留快照。`
      ));
    }
    if (target.closest('#library-refinement-locate')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      void locateRefinementProduct()
        .then(() => showToast('已在文件管理器中选中精炼结果；本次定位不计作打开。'))
        .catch((error) => showToast(`文件定位失败：${error.message}`));
    }
    if (target.closest('#library-refinement-open,#library-refinement-draft-hash')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      void openRefinementProduct()
        .then(() => showToast('已打开并校验精炼成品；现在可以确认复核。'))
        .catch((error) => showToast(`成品打开失败：${error.message}`));
    }
    if (target.closest('#library-refinement-confirm')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      void confirmRefinementReview()
        .then(() => showToast('已确认复核'))
        .catch((error) => showToast(`确认复核失败：${error.message}`));
    }
    if (target.closest('#library-close')) selectedArtifactId = null;
    if (target.closest('#library-refresh')) void window.__Desktop_UI_CONTROLS__.busy(target.closest('#library-refresh'),()=>nativeCall('library.refresh', {}).then(refresh));
    if (target.closest('#library-mode-literature')) void nativeCall('library.set_view', { view: 'literature' });
    if (target.closest('#library-mode-memorive')) void nativeCall('library.set_view', { view: 'memorive' });
    const filter = target.closest('#library-filter-menu [data-filter]');

  }, true);

  window.addEventListener('desktop-integrated-route', (event) => {
    const route = event.detail?.route;
    if (route === 'library') void refresh().catch(error => showToast(`资料加载失败：${error.message}`));
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { if (window.pywebview?.api?.call) void bootstrap(); }, { once: true });
  } else if (window.pywebview?.api?.call) {
    void bootstrap();
  }

  window.__Desktop_LIBRARY_BRIDGE__ = {
    bootstrap,
    refresh,
    loadNextPage,
    openSourceSession,
    openRelatedTask,
    locateCoreSource,
    openCoreArtifact,
    openArtifactByLocator,
    openRefinementProduct,
    confirmRefinementReview,
    call: nativeCall,
    inspect: () => ({
      method_count: methods.length,
      methods: [...methods],
      binding_count: bindings.size,
      paging:{...paging},
      selected_artifact_id: selectedArtifactId,
      receipt_count: receipts.length,
      native_transport: Boolean(window.pywebview?.api?.call),
      canonical_artifact_registry_actual_reads: 0,
      external_view_launches: 0,
      synthetic_only: false
    })
  };
})();

