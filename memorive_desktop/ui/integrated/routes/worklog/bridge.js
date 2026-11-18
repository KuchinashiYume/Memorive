/* The work log page's calls to the desktop service (work_log.*): entries, milestones, export, and following an entry
   to its job. Publishes Memorive.workLogBridge; shared/product-bridge.js and
   memorive/desktop/api/page_events.py call it when the service reports a change. */
(() => {
  'use strict';

  const methods = Object.freeze([
    'work_log.get_contract',
    'work_log.bootstrap',
    'work_log.list_entries',
    'work_log.get_entry',
    'work_log.get_milestones',
    'work_log.set_filter',
    'work_log.select',
    'work_log.resolve_job_locator',
    'work_log.resolve_evidence_locator',
    'work_log.get_source_status',
    'work_log.refresh',
    'work_log.export_preview',
    'work_log.export_redacted_view',
    'work_log.verify_export',
    'work_log.inject_fault',
    'work_log.restore',
    'work_log.effect_metrics'
  ]);
  const coreProjectionSource = 'core_job_provider';
  const bindings = new Map();
  let receiptCount = 0;
  let selectedLogEntryId = null;
  document.getElementById('desktop-worklog-preview')?.addEventListener('desktop:inspector-closed', () => { selectedLogEntryId = null; });
  let sampleDirectoryLaunches = 0;
  let sampleProjectionLocates = 0;
  let syntheticOnly = false;
  let bootstrapPromise = null;
  let refreshPromise = null;
  let pendingUpdateCount = 0;

  const notifyPending = () => {
    pendingUpdateCount += 1;
    const refreshButton = document.querySelector('#worklog-refresh');
    if (refreshButton) {
      refreshButton.dataset.pending = 'true';
      refreshButton.dataset.pendingCount = String(pendingUpdateCount);
      refreshButton.setAttribute('aria-label', `刷新，有 ${pendingUpdateCount} 个新事件`);
    }
    return pendingUpdateCount;
  };

  const clearPending = () => {
    pendingUpdateCount = 0;
    const refreshButton = document.querySelector('#worklog-refresh');
    if (refreshButton) {
      refreshButton.dataset.pending = 'false';
      refreshButton.dataset.pendingCount = '0';
      refreshButton.setAttribute('aria-label', '刷新');
    }
  };

  const nativeCall = async (method, params = {}) => {
    let result;
    if (!window.pywebview?.api?.call) {
      result = {
        schema_version: 'WorkLogBrowserOnlyReceipt-v1',
        method,
        records: [],
        record_count: 0,
        catalog_count: 0,
        status: 'BROWSER_TEST_NO_NATIVE_TRANSPORT'
      };
    } else {
      result = await Memorive.service.call(method, params);
    }
    receiptCount += 1;
    window.dispatchEvent(new CustomEvent('desktop:work-log-receipt', { detail: { method, result } }));
    return result;
  };

  const nativeDesktopAction = async (method) => {
    const action = window.pywebview?.api?.[method];
    if (typeof action !== 'function') {
      return { schema_version: 'DesktopAssistantBrowserOnlyWorkLogActionReceipt-v1', method, sample: true, status: 'BROWSER_TEST_NO_NATIVE_TRANSPORT' };
    }
    const result = await action.call(window.pywebview.api);
    if (result?.status === 'OPENED' && method === 'open_work_log_sample_directory') sampleDirectoryLaunches += 1;
    if (result?.status === 'OPENED' && method === 'locate_work_log_sample_projection') sampleProjectionLocates += 1;
    receiptCount += 1;
    window.dispatchEvent(new CustomEvent('desktop:work-log-receipt', { detail: { method: `desktop.${method}`, result } }));
    return result;
  };

  const bindRows = (projection) => {
    const rows = Array.isArray(projection?.records)
      ? projection.records
      : Array.isArray(projection?.rows) ? projection.rows : [];
    Memorive.workLogView?.renderRows(rows);
    bindings.clear();
    const byId = new Map(rows.map((row) => [row.log_entry_id, row]));
    document.querySelectorAll('#desktop-worklog-preview .desktop-worklog-card[data-id]').forEach((card) => {
      const logEntryId = card.dataset.id;
      if (!logEntryId) return;
      bindings.set(logEntryId, { card, record: byId.get(logEntryId) || null });
    });
    if (selectedLogEntryId && bindings.has(selectedLogEntryId)) {
      Memorive.workLogView?.openDetailById(selectedLogEntryId);
    } else if (selectedLogEntryId) {
      selectedLogEntryId = null;
      Memorive.workLogView?.closeDetail?.();
    }
    return rows;
  };

  const refresh = () => {
    if (refreshPromise) return refreshPromise;
    refreshPromise = nativeCall('work_log.list_entries', {
      time_range: 'all', ledger_type: 'all', page: 1, page_size: 500
    }).then((projection) => {
      const rows = bindRows(projection);
      clearPending();
      return { ...projection, records: rows, record_count: rows.length };
    }).finally(() => { refreshPromise = null; });
    return refreshPromise;
  };

  const loadDetail = async (logEntryId) => {
    const [detail, milestones] = await Promise.all([
      nativeCall('work_log.get_entry', { log_entry_id:logEntryId }),
      nativeCall('work_log.get_milestones', { log_entry_id:logEntryId })
    ]);
    Memorive.workLogView?.setMilestones(logEntryId, milestones.milestones || []);
    return { detail, milestones };
  };

  const bootstrap = async () => {
    if (!bootstrapPromise) {
      bootstrapPromise = refresh().then((projection) => {
        const rows = projection.records;
        syntheticOnly = projection.synthetic_only === true;
        return {
          schema_version: 'WorkLogWorkLogBridgeBootstrap-v1',
          records: rows,
          record_count: projection.record_count || 0,
          catalog_count: projection.catalog_count || 0,
          native_binding_count: projection.binding_count || 0,
          dom_binding_count: bindings.size,
          source_authority_access: 'HASH_METADATA_ONLY_NO_RAW_BYTES',
          synthetic_only: syntheticOnly,
          status: projection.status === 'PASS' ? 'PASS' : projection.status
        };
      }).catch((error) => {
        bootstrapPromise = null;
        throw error;
      });
    }
    return bootstrapPromise;
  };

  const selectedBinding = () => bindings.get(selectedLogEntryId) || null;

  const openRelatedJob = async () => {
    const record = selectedBinding()?.record;
    const locator = record?.job_locator;
    if (!locator) throw new Error('任务稳定定位符缺失');
    const receipt = await nativeCall('work_log.resolve_job_locator', { job_locator:locator });
    if (receipt?.status !== 'PASS') throw new Error(receipt?.status || '任务定位失败');
    await Memorive.productBridge?.dispatch(
      'navigation.select', { route:'current-task' }, 'work-log-job'
    );
    if (Memorive.app?.activate) {
      Memorive.app.activate('current-task');
    }
    window.dispatchEvent(new CustomEvent('memorive:open-current-task', {
      detail: {
        job_id: receipt.job_id || record.job_id,
        node_id: null,
        source: 'work-log'
      }
    }));
    return receipt;
  };

  const openRelatedArtifact = async () => {
    const locator = selectedBinding()?.record?.related_artifact_locator;
    if (!locator) throw new Error('关联产物稳定定位符缺失');
    const bridge = Memorive.libraryBridge;
    if (!bridge?.openArtifactByLocator) throw new Error('资料库定位服务未就绪');
    return bridge.openArtifactByLocator(locator);
  };

  document.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const card = target.closest('#desktop-worklog-preview .desktop-worklog-card[data-id]');
    if (card) {
      const nextLogEntryId = card.dataset.id || null;
      if (nextLogEntryId) Memorive.workLogView?.openDetailById(nextLogEntryId);
      if (selectedLogEntryId !== nextLogEntryId) {
        selectedLogEntryId = nextLogEntryId;
        if (selectedLogEntryId) {
          void nativeCall('work_log.select', { log_entry_id: selectedLogEntryId });
          void loadDetail(selectedLogEntryId);
        }
      }
    }
    if (target.closest('#worklog-apply')) {
      const timeRange = document.querySelector('#desktop-worklog-preview input[name="worklog-time"]:checked')?.value || '7';
      const ledgerType = document.querySelector('#desktop-worklog-preview input[name="worklog-ledger"]:checked')?.value || 'all';
      void nativeCall('work_log.set_filter', { time_range: timeRange, ledger_type: ledgerType }).then(refresh);
    }
    if (target.closest('#worklog-locate')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      void openRelatedJob();
    }
    if (target.closest('#worklog-open-current-folder')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      void openRelatedArtifact();
    }
    if (target.closest('#worklog-close')) selectedLogEntryId = null;
    if (target.closest('#worklog-refresh')) void Memorive.controls.busy(target.closest('#worklog-refresh'),()=>nativeCall('work_log.refresh', {}).then(refresh));
  }, true);

  window.addEventListener('desktop-integrated-route', (event) => {
    if (event.detail?.route === 'work-log') void refresh();
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { if (window.pywebview?.api?.call) void bootstrap(); }, { once: true });
  } else if (window.pywebview?.api?.call) {
    void bootstrap();
  }

  Memorive.workLogBridge = {
    bootstrap,
    refresh,
    notifyPending,
    openRelatedJob,
    openRelatedArtifact,
    call: nativeCall,
    inspect: () => ({
      method_count: methods.length,
      methods: [...methods],
      binding_count: bindings.size,
      selected_log_entry_id: selectedLogEntryId,
      receipt_count: receiptCount,
      native_transport: Boolean(window.pywebview?.api?.call),
      raw_authority_content_reads: 0,
      source_authority_mutations: 0,
      core_projection_source: coreProjectionSource,
      public_exports_written_by_bridge: 0,
      sample_directory_launches: sampleDirectoryLaunches,
      sample_projection_locates: sampleProjectionLocates,
      synthetic_only: syntheticOnly
    })
  };
})();

