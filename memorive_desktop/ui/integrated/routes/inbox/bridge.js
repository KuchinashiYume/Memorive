/* The inbox page's calls to the desktop service (inbox.*): the item list and its detail, importing files (picker and
   drag and drop), dispatching items to processing, undo and redo, stars, deletion, and the preview of an item's file.
   Publishes window.__Desktop_INBOX_BRIDGE__; shared/product-bridge.js and memorive/desktop/api/page_events.py call its
   refresh() when the service reports a change. */
(() => {
  'use strict';
  const root = document.getElementById('desktop-inbox-preview');
  if (!root) return;
  const preview = window.DesktopPreview_inbox;
  const METHODS = Object.freeze([
    'inbox.get_contract', 'inbox.import_paths', 'inbox.get_auto_run',
    'inbox.set_auto_run', 'inbox.retry_import', 'inbox.recover',
    'inbox.dispatch', 'inbox.record_dispatch_receipt', 'inbox.cancel_dispatch',
    'inbox.set_starred', 'inbox.soft_delete', 'inbox.undo', 'inbox.redo',
    'inbox.refresh', 'inbox.projection', 'inbox.item_detail', 'inbox.close_session',
    'inbox.effect_metrics', 'sessions.refinement_cancel',
    'current_task.begin_cancel', 'current_task.confirm_cancel'
  ]);
  const released = new WeakSet();
  const q = (selector) => root.querySelector(selector);
  const qa = (selector) => [...root.querySelectorAll(selector)];
  const nativeReady = () => Boolean(window.pywebview && window.pywebview.api && window.pywebview.api.call);
  let contract = null;
  let projection = null;
  let effects = null;
  let bootstrapPromise = null;
  let lastReceipt = null;
  let sequence = 0;
  let operationSequence = 0;
  let toastTimer = 0;
  let previewSequence = 0;
  let previewObjectUrl = null;
  let previewPdfDocument = null;
  let previewPdfRenderTask = null;
  let previewPdfPage = 1;
  let contextItemId = null;
  let inspectorDeleteItem = null;
  let inspectorDeleteBusy = false;
  let selectedRefinementProjectionId = null;
  let loadedPreviewItemId = null;
  let workspaceDragDepth = 0;
  const workspaceDropBoundary = 'WORKSPACE_DROP_DEFAULT_NAVIGATION_BLOCKED';

  const hasDraggedFiles = (event) => {
    const transfer = event?.dataTransfer;
    if (!transfer) return false;
    if (transfer.files?.length) return true;
    return Array.from(transfer.types || []).includes('Files');
  };

  const showWorkspaceDropTarget = (visible) => {
    const overlay = q('#inbox-drop');
    if (overlay) overlay.hidden = !visible;
    root.classList.toggle('is-workspace-file-drag', Boolean(visible));
  };

  document.addEventListener('dragenter', (event) => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    workspaceDragDepth += 1;
    showWorkspaceDropTarget(true);
  }, true);

  document.addEventListener('dragover', (event) => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
    showWorkspaceDropTarget(true);
  }, true);

  document.addEventListener('dragleave', (event) => {
    if (!hasDraggedFiles(event)) return;
    workspaceDragDepth = Math.max(0, workspaceDragDepth - 1);
    if (!workspaceDragDepth) showWorkspaceDropTarget(false);
  }, true);

  document.addEventListener('drop', (event) => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    workspaceDragDepth = 0;
    showWorkspaceDropTarget(false);
    const fileCount = event.dataTransfer?.files?.length || 0;
    if (!fileCount) {
      showToast('拖放内容中没有可导入的本地文件。');
      return;
    }
    showToast(nativeReady()
      ? '正在读取文件…'
      : '浏览器预览已阻止文件导航；桌面程序中会把文件导入收件箱。');
    void workspaceDropBoundary;
  }, true);

  const call = async (method, params = {}) => {
    sequence += 1;
    const receipt = await window.__Desktop_SERVICE__.call(method, params);
    lastReceipt = {
      method,
      request_sequence: sequence,
      schema_version: receipt && receipt.schema_version || null
    };
    window.dispatchEvent(new CustomEvent('desktop:inbox-receipt', { detail: lastReceipt }));
    return receipt;
  };

  const showToast = (copy, canUndo = false) => {
    const toast = q('#inbox-toast');
    if (!toast) return;
    window.clearTimeout(toastTimer);
    q('#inbox-toast-copy').textContent = copy;
    q('#inbox-toast-undo').hidden = !canUndo;
    toast.classList.remove('is-fading');
    toast.hidden = false;
    toastTimer = window.setTimeout(() => {
      toast.classList.add('is-fading');
      window.setTimeout(() => {
        toast.hidden = true;
        toast.classList.remove('is-fading');
      }, 280);
    }, 5000);
  };

  const publicInboxFailure = (error) => {
    const diagnostic = String(error?.message || error || '');
    console.warn('Memorive inbox action diagnostic', diagnostic);
    const marker = diagnostic.toUpperCase();
    if (marker.includes('IPC_') || marker.includes('CONNECTION') || marker.includes('SERVICE')) {
      return '尚未确认移除结果，请刷新核对。';
    }
    if (marker.includes('PROCESSING') || marker.includes('RUNNING')) {
      return '请先停止任务再移除';
    }
    return '尚未确认操作结果，请刷新核对。';
  };

  const cards = () => qa('.desktop-inbox-card:not(.new-file)');
  const refreshCount = () => {
    const counts = {
      queued: 0,
      processing: 0,
      paused: 0,
      error: 0,
      importing: qa('.desktop-inbox-card.new-file:not([hidden])').length
    };
    cards().forEach((card) => {
      if (card.dataset.state === '已排队') counts.queued += 1;
      else if (['处理中', '已设置暂停点'].includes(card.dataset.state)) counts.processing += 1;
      else if (['已暂停','暂停中'].includes(card.dataset.state)) counts.paused += 1;
      else if (card.dataset.state === '异常') counts.error += 1;
    });
    const empty = q('#inbox-empty');
    if (empty) empty.hidden = cards().length > 0 || counts.importing > 0;
    const counter = q('#inbox-queued');
    const summary = '已排队 ' + counts.queued + ' · 处理中 ' + counts.processing
      + ' · 已暂停 ' + counts.paused + ' · 异常 ' + counts.error + ' · 导入中 ' + counts.importing;
    counter.textContent = String(cards().length);
    counter.setAttribute('aria-label', '收件箱 ' + cards().length + ' 项');
    counter.dataset.desktopTip = summary;
  };

  const rowWeight = (row) => {
    if (row.classList.contains('new-file')) return 2;
    if (['处理中','已设置暂停点','已暂停','暂停中'].includes(row.dataset.state)) return 0;
    if (row.dataset.state === '异常') return 5;
    if (row.classList.contains('starred')) return 1;
    return 3;
  };
  const stableSort = () => {
    const list = q('#inbox-list');
    const rows = [...list.children].map((row, index) => ({ row, index }));
    rows.sort((a, b) => rowWeight(a.row) - rowWeight(b.row) || a.index - b.index);
    rows.forEach(({ row }) => list.appendChild(row));
  };

  const mapInitialCards = (nextProjection) => {
    const pool = [...(nextProjection && nextProjection.cards || [])];
    const take = (predicate) => {
      const index = pool.findIndex(predicate);
      if (index < 0) return null;
      return pool.splice(index, 1)[0];
    };
    cards().forEach((card) => {
      if (card.dataset.itemId) return;
      let row = null;
      if (card.dataset.state === '处理中') row = take((item) => item.state === 'PROCESSING');
      else if (card.dataset.state === '异常') row = take((item) => item.state === 'ERROR');
      else if (card.classList.contains('starred')) {
        row = take((item) => item.state === 'QUEUED' && item.starred);
      } else {
        row = take((item) => item.state === 'QUEUED' && !item.starred);
      }
      row = row || pool.shift() || null;
      if (row) bindCard(card, row);
    });
  };

  const formatWorkload = (row) => {
    const workload = row && row.workload;
    if (!workload) return row && row.error && row.error.code || '属性读取中';
    if (row?.item_kind === 'SESSION_REFINEMENT') {
      if (row.state === 'ERROR') {
        const code = String(row.error?.code || 'SESSION_REFINEMENT_EXECUTION_FAILED');
        return `执行异常 · ${code}`;
      }
      return `${workload.label || '消息'} ${workload.value || row.message_count || 0} · 会话精炼队列`;
    }
    const size = Number(workload.bytes || 0);
    const rendered = size >= 1048576
      ? (size / 1048576).toFixed(1) + ' MB'
      : size >= 1024 ? (size / 1024).toFixed(1) + ' KB' : size + ' B';
    return workload.label + ' ' + workload.value + ' · ' + rendered;
  };

  const formatBytes = (value) => {
    const size = Number(value || 0);
    return size >= 1048576
      ? (size / 1048576).toFixed(1) + ' MB'
      : size >= 1024 ? (size / 1024).toFixed(1) + ' KB' : size + ' B';
  };

  const kindForExtension = (extension) => {
    const value = String(extension || '').toLowerCase();
    if (value === '.pdf') return 'pdf';
    if (['.png', '.jpg', '.jpeg', '.tif', '.tiff'].includes(value)) return 'image';
    if (value === '.pptx') return 'presentation';
    if (['.docx', '.xlsx'].includes(value)) return 'office';
    return 'text';
  };

  const bindCard = (card, row) => {
    const state = row.display_state || (row.state === 'IMPORTING' ? '导入中' : row.state);
    const meta = formatWorkload(row) + (row.sample ? ' · 示例' : '');
    Object.assign(card.dataset, {
      itemId: row.item_id,
      itemKind: row.item_kind || 'FILE',
      localProjectionId: row.local_projection_id || '',
      kind: row.item_kind === 'SESSION_REFINEMENT' ? 'refinement' : kindForExtension(row.extension),
      extension: row.extension || '',
      state,
      title: row.name,
      meta,
      created: row.created_at || '—',
      modified: row.updated_at || '—',
      path: '收件箱 / ' + row.name,
      sample: String(Boolean(row.sample))
    });
    card.classList.toggle('starred', Boolean(row.starred));
    card.classList.toggle('error', row.state === 'ERROR');
    const strong = card.querySelector('strong');
    const small = card.querySelector('small');
    const status = card.querySelector('.desktop-inbox-state');
    const star = card.querySelector('.desktop-inbox-star');
    const check = card.querySelector('.desktop-inbox-check');
    if (strong) strong.textContent = row.name;
    if (small) small.textContent = meta;
    if (status) {
      if(window.__MEMORIVE_REVIEW_UI__)window.__MEMORIVE_REVIEW_UI__.renderInboxState(status,row);else status.textContent=state;
    }
    if (star) {
      star.disabled = row.state === 'PROCESSING';
      star.setAttribute('aria-pressed', String(Boolean(row.starred)));
      star.setAttribute('aria-label', row.starred ? '取消星标' : '添加星标');
      star.textContent = row.starred ? '★' : '☆';
    }
    if (check) {
      check.disabled = !['QUEUED', 'ERROR'].includes(row.state);
      check.setAttribute('aria-label', '选择 ' + row.name);
    }
    const deleteCell = card.querySelector('.desktop-inbox-deletecell');
    const shouldDelete = row.delete_visible !== false
      && (row.state === 'QUEUED' || row.state === 'ERROR'
        || (row.item_kind === 'SESSION_REFINEMENT' && row.state === 'PROCESSING'));
    if (deleteCell && shouldDelete && !deleteCell.querySelector('.desktop-inbox-delete')) {
      const remove = document.createElement('button');
      remove.className = 'desktop-inbox-delete';
      remove.type = 'button';
      remove.setAttribute('aria-label', row.item_kind === 'SESSION_REFINEMENT' ? '取消精炼任务' : '从收件箱移除');
      remove.textContent = '×';
      deleteCell.append(remove);
    } else if (deleteCell && !shouldDelete) {
      deleteCell.querySelector('.desktop-inbox-delete')?.remove();
    }
    return card;
  };

  const createCard = (row) => {
    const card = document.createElement('article');
    card.className = 'desktop-inbox-card' + (row.starred ? ' starred' : '')
      + (row.state === 'ERROR' ? ' error' : '');
    card.tabIndex = 0;
    card.setAttribute('aria-selected', 'false');
    const kind = kindForExtension(row.extension);
    const state = row.display_state || (row.state === 'IMPORTING' ? '导入中' : '已排队');
    Object.assign(card.dataset, {
      itemId: row.item_id,
      itemKind: row.item_kind || 'FILE',
      localProjectionId: row.local_projection_id || '',
      kind,
      state,
      title: row.name,
      meta: formatWorkload(row),
      created: row.created_at || '—',
      modified: row.updated_at || '—',
      path: '收件箱 / ' + row.name
    });
    const starCell = document.createElement('div');
    starCell.className = 'desktop-inbox-starcell';
    const check = document.createElement('input');
    check.className = 'desktop-inbox-check';
    check.type = 'checkbox';
    check.hidden = !q('#inbox-list').classList.contains('bulk');
    check.disabled = !row.selectable && row.state !== 'QUEUED' && row.state !== 'ERROR';
    check.setAttribute('aria-label', '选择 ' + row.name);
    const star = document.createElement('button');
    star.className = 'desktop-inbox-star';
    star.type = 'button';
    star.disabled = row.state === 'PROCESSING';
    star.setAttribute('aria-pressed', String(Boolean(row.starred)));
    star.setAttribute('aria-label', row.starred ? '取消星标' : '添加星标');
    star.textContent = row.starred ? '★' : '☆';
    starCell.append(check, star);
    const name = document.createElement('div');
    const strong = document.createElement('strong');
    const small = document.createElement('small');
    strong.textContent = row.name;
    small.textContent = formatWorkload(row);
    name.append(strong, small);
    const stateCell = document.createElement('div');
    stateCell.className = 'desktop-inbox-state';
    if(window.__MEMORIVE_REVIEW_UI__)window.__MEMORIVE_REVIEW_UI__.renderInboxState(stateCell,row);else stateCell.textContent=state;
    const deleteCell = document.createElement('div');
    deleteCell.className = 'desktop-inbox-deletecell';
    if (row.delete_visible !== false && (
      row.state === 'QUEUED' || row.state === 'ERROR'
      || (row.item_kind === 'SESSION_REFINEMENT' && row.state === 'PROCESSING')
    )) {
      const remove = document.createElement('button');
      remove.className = 'desktop-inbox-delete';
      remove.type = 'button';
      remove.setAttribute('aria-label', row.item_kind === 'SESSION_REFINEMENT' ? '取消精炼任务' : '从收件箱移除');
      remove.textContent = '×';
      deleteCell.append(remove);
    }
    card.append(starCell, name, stateCell, deleteCell);
    return bindCard(card, row);
  };

  const closeInvalidatedInspector = (itemId) => {
    previewSequence += 1;
    resetPreviewObject();
    setRefinementInspectorMode(false);
    selectedRefinementProjectionId = null;
    setInspectorDeleteItem(null);
    cards().forEach((card) => card.setAttribute('aria-selected', 'false'));
    if (!itemId || contextItemId === itemId) contextItemId = null;
    const previewHost = q('#inbox-preview');
    if (previewHost) previewHost.replaceChildren();
    const placeholders = {
      '#inbox-detail-title': '文件', '#inbox-detail-type': '—', '#inbox-detail-size': '—',
      '#inbox-detail-state': '—', '#inbox-detail-created': '—', '#inbox-detail-modified': '—',
      '#inbox-detail-path': '—', '#inbox-detail-id': '—', '#inbox-preview-heading': '预览'
    };
    Object.entries(placeholders).forEach(([selector, value]) => {
      const node = q(selector);
      if (node) node.textContent = value;
    });
    if (preview && typeof preview.setInspector === 'function') {
      preview.setInspector(false);
    }
  };

  const syncAutoRun = (nextProjection) => {
    const auto = q('#inbox-auto');
    if (!auto) return;
    const enabled = nextProjection?.auto_run?.enabled === true;
    auto.setAttribute('aria-checked', String(enabled));
    auto.setAttribute('aria-label', enabled ? '自动执行已开启' : '自动执行已关闭');
    auto.setAttribute('aria-disabled', 'false');
    auto.disabled = false;
    q('#inbox-auto-warning').hidden = true;
  };

  const syncHistory = (nextProjection) => {
    const history = nextProjection?.history || {};
    const undo = q('#inbox-undo');
    const redo = q('#inbox-redo');
    if (undo) {
      undo.disabled = history.undo_available !== true;
      undo.setAttribute('aria-disabled', String(undo.disabled));
    }
    if (redo) {
      redo.disabled = history.redo_available !== true;
      redo.setAttribute('aria-disabled', String(redo.disabled));
    }
  };

  const syncProjection = (nextProjection, options = {}) => {
    const appendNew = options.appendNew !== false;
    const list = q('#inbox-list');
    const scrollTop = list?.scrollTop || 0;
    const selectedBeforeSync = q('.desktop-inbox-card[aria-selected="true"]')?.dataset.itemId || null;
    mapInitialCards(nextProjection);
    const rows = new Map((nextProjection && nextProjection.cards || []).map((row) => [row.item_id, row]));
    cards().forEach((card) => {
      const row = rows.get(card.dataset.itemId);
      if (row) bindCard(card, row);
      else if (nextProjection) card.remove();
    });
    if (appendNew) {
      const known = new Set(cards().map((card) => card.dataset.itemId).filter(Boolean));
      (nextProjection && nextProjection.cards || []).forEach((row) => {
        if (!known.has(row.item_id)) q('#inbox-list').appendChild(createCard(row));
      });
    }
    if (selectedBeforeSync && !rows.has(selectedBeforeSync)) {
      closeInvalidatedInspector(selectedBeforeSync);
    }
    stableSort();
    if (list) list.scrollTop = scrollTop;
    refreshCount();
    syncAutoRun(nextProjection);
    syncHistory(nextProjection);
  };

  const bootstrap = () => {
    if (bootstrapPromise) return bootstrapPromise;
    if (!nativeReady()) return Promise.resolve({ native_transport: false, status: 'NOT_RUN' });
    bootstrapPromise = (async () => {
      contract = await call('inbox.get_contract', {});
      projection = await call('inbox.projection', { batch_mode: false });
      effects = await call('inbox.effect_metrics', {});
      syncProjection(projection);
      syncAutoRun(projection);
      return {
        contract_schema: contract.schema_version,
        card_count: projection.cards.length,
        bound_dom_cards: cards().filter((card) => card.dataset.itemId).length,
        status: 'PASS'
      };
    })().catch((error) => {
      bootstrapPromise = null;
      throw error;
    });
    return bootstrapPromise;
  };

  const importPaths = async (paths, sourceKind = 'picker', requestId = null) => {
    await bootstrap();
    if (!Array.isArray(paths) || paths.length === 0) throw new Error('INBOX_IMPORT_PATHS_EMPTY');
    const ghost = q('.importing-progress');
    if (ghost) {
      ghost.hidden = false;
      ghost.querySelector('.desktop-inbox-progress-copy').textContent = '处理中';
      ghost.querySelector('.desktop-inbox-progress i').style.width = '55%';
    }
    refreshCount();
    try {
      operationSequence += 1;
      const receipt = await call('inbox.import_paths', {
        paths,
        request_id: requestId || 'ui-import-' + Date.now() + '-' + operationSequence,
        source_kind: sourceKind
      });
      projection = await call('inbox.projection', { batch_mode: false });
      syncProjection(projection);
      const imported = (receipt.items || []).filter((row) => row?.item?.state === 'QUEUED').length;
      const reused = (receipt.items || []).filter((row) => row?.item?.state === 'DEDUPLICATED').length;
      showToast(reused
        ? `导入完成：新增 ${imported} 项，复用 ${reused} 项已有内容。`
        : `已添加 ${imported} 项`);
      return receipt;
    } finally {
      if (ghost) {
        ghost.hidden = true;
        ghost.querySelector('.desktop-inbox-progress-copy').textContent = '0%';
        ghost.querySelector('.desktop-inbox-progress i').style.width = '0%';
      }
      refreshCount();
    }
  };

  const replayClick = (target) => {
    released.add(target);
    target.click();
  };
  const stop = (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
  };
  const itemIdFor = (target) => {
    const card = target.closest('.desktop-inbox-card');
    return card && card.dataset.itemId || null;
  };
  const selectedItemIds = () => qa('.desktop-inbox-check:checked')
    .map((box) => box.closest('.desktop-inbox-card'))
    .filter((card) => card && card.dataset.raceSkip !== 'true')
    .map((card) => card.dataset.itemId)
    .filter(Boolean);

  const selectedItemId = () => q('.desktop-inbox-card[aria-selected="true"]')?.dataset.itemId || null;

  const setInspectorDeleteItem = (item = null) => {
    if (inspectorDeleteItem?.item_id !== item?.item_id
        || inspectorDeleteItem?.job_id !== item?.job_id) {
      window.__Desktop_UI_CONTROLS__?.cancelAction();
    }
    inspectorDeleteItem = item;
    const button = q('#inbox-delete-task');
    if (button) button.disabled = inspectorDeleteBusy
      || !item || !['QUEUED', 'PROCESSING', 'ERROR'].includes(item.state);
  };

  const deleteInspectorTask = async (button) => {
    const item = inspectorDeleteItem;
    if (inspectorDeleteBusy || !item || selectedItemId() !== item.item_id) return;
    const key = 'inbox-delete:' + item.item_id + ':' + (item.job_id || 'unbound');
    if (!window.__Desktop_UI_CONTROLS__?.confirmAction(button, key)) return;
    inspectorDeleteBusy = true;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    try {
      const fresh = await call('inbox.item_detail', { item_id:item.item_id });
      if ((fresh.job_id || '') !== (item.job_id || '')) {
        if (selectedItemId() === item.item_id) setInspectorDeleteItem(fresh);
        showToast('任务状态已变化，请重新确认删除。');
        return;
      }
      let cancelled = false;
      if (fresh.item_kind === 'SESSION_REFINEMENT') {
        await call('sessions.refinement_cancel', { inbox_item_id:item.item_id });
        cancelled = true;
      } else if (fresh.state === 'PROCESSING' && fresh.job_id) {
        const pending = await call('current_task.begin_cancel', { job_id:fresh.job_id });
        await call('current_task.confirm_cancel', {
          token:pending.token,
          idempotency_key:'inbox-inspector-cancel-' + fresh.job_id + '-' + (++operationSequence)
        });
        cancelled = true;
      } else if (['QUEUED', 'ERROR'].includes(fresh.state)) {
        const removal = await call('inbox.soft_delete', {
          item_ids:[item.item_id],
          operation_id:'ui-inspector-delete-' + Date.now() + '-' + (++operationSequence)
        });
        if (removal.success_count !== 1) {
          await refresh();
          showToast('任务状态已变化，请重新确认删除。');
          return;
        }
      } else {
        await refresh();
        showToast('任务状态已变化，请刷新查看。');
        return;
      }
      await refresh();
      const stillVisible = projection?.cards?.some(row => row.item_id === item.item_id);
      showToast(stillVisible && cancelled
        ? '已请求取消，任务停止后会移出收件箱。'
        : cancelled ? '已取消任务并移出收件箱。' : '已从收件箱移除，可在本次会话撤销。',
        !cancelled && !stillVisible);
    } finally {
      inspectorDeleteBusy = false;
      button.removeAttribute('aria-busy');
      setInspectorDeleteItem(inspectorDeleteItem);
    }
  };

  const setRefinementInspectorMode = (enabled) => {
    q('#inbox-locate').hidden = Boolean(enabled);
    q('#inbox-external').hidden = Boolean(enabled);
    q('#inbox-open-session').hidden = !enabled;
    q('#inbox-properties-heading').textContent = enabled ? '任务属性' : '文件属性';
  };

  const formatRefinementError = (code) => ({
    REFINEMENT_MESSAGE_FIELDS_INVALID: '会话格式不兼容，请重新创建精炼任务。',
    SESSION_REFINEMENT_CANCELLED: '任务已由用户取消，没有继续调用模型。',
    SESSION_REFINEMENT_PROFILE_EXECUTION_UNAVAILABLE: '所选模型当前没有可用的精炼执行通道。',
    SESSION_REFINEMENT_MODEL_NOT_CONFIGURED: '尚未选择精炼模型，请先前往设置完成配置。'
  })[String(code || '')] || (code ? `执行失败：${code}` : '排队中');

  const renderRefinementDetail = (item) => {
    setInspectorDeleteItem(item);
    const panel = q('#inbox-preview');
    const errorCode = item?.error?.code || '';
    resetPreviewObject();
    setRefinementInspectorMode(true);
    selectedRefinementProjectionId = String(item?.local_projection_id || '');
    q('#inbox-detail-title').textContent = item?.name || '精炼对话';
    q('#inbox-detail-type').textContent = '会话精炼任务';
    q('#inbox-detail-size').textContent = `${Number(item?.message_count || 0)} 条消息`;
    q('#inbox-detail-state').textContent = item?.display_state || item?.state || '—';
    q('#inbox-detail-created').textContent = item?.created_at || '—';
    q('#inbox-detail-modified').textContent = item?.updated_at || '—';
    q('#inbox-detail-path').textContent = selectedRefinementProjectionId
      ? (item?.name || '会话管理') : '会话管理';
    q('#inbox-detail-id').textContent = item?.item_id || '—';
    q('#inbox-preview-heading').textContent = '任务说明';
    panel.replaceChildren();
    const title = document.createElement('h5');
    title.textContent = item?.state === 'ERROR' ? '精炼未完成' : '';
    const description = document.createElement('p');
    description.textContent = errorCode
      ? formatRefinementError(errorCode)
      : '排队中';
    const profile = document.createElement('div');
    profile.className = 'desktop-inbox-preview-code';
    profile.textContent = [
      `来源会话  ${selectedRefinementProjectionId || '—'}`,
      `精炼模型  ${item?.selected_profile_ref || '—'}`,
      `任务状态  ${item?.display_state || item?.state || '—'}`,
      errorCode ? `异常原因  ${errorCode}` : ''
    ].filter(Boolean).join('\n');
    const technical = document.createElement('details');
    technical.className = 'desktop-copy-technical';
    const technicalLabel = document.createElement('summary');
    technicalLabel.textContent = '技术详情';
    technical.append(technicalLabel, profile);
    panel.append(title, description, technical);
    loadedPreviewItemId = item?.item_id || null;
    if (preview && typeof preview.setInspector === 'function') preview.setInspector(true);
  };

  const loadRefinementDetail = async (itemId, card = null) => {
    if (!nativeReady() || !itemId) return;
    const item = await call('inbox.item_detail', { item_id:itemId });
    cards().forEach((candidate) => candidate.setAttribute('aria-selected', String(candidate === card)));
    renderRefinementDetail(item);
  };

  const bytesFromBase64 = (value) => {
    const binary = window.atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return bytes;
  };

  const resetPreviewObject = () => {
    if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = null;
    if (previewPdfRenderTask && typeof previewPdfRenderTask.cancel === 'function') {
      try { previewPdfRenderTask.cancel(); } catch (_) { /* already complete */ }
    }
    previewPdfRenderTask = null;
    if (previewPdfDocument && typeof previewPdfDocument.destroy === 'function') {
      void previewPdfDocument.destroy().catch(() => undefined);
    }
    previewPdfDocument = null;
    previewPdfPage = 1;
  };

  const pdfEngine = async () => {
    const engine = await (window.__Desktop_LOAD_PDFJS__ ? window.__Desktop_LOAD_PDFJS__() : window.__Desktop_PDFJS_READY__);
    if (!engine || typeof engine.getDocument !== 'function' || !globalThis.pdfjsWorker?.WorkerMessageHandler) {
      throw new Error('PDF_RENDERER_NOT_READY');
    }
    return engine;
  };

  const renderPdfPage = async (documentHandle, pageNumber, canvas, status, previous, next) => {
    const page = await documentHandle.getPage(pageNumber);
    const baseViewport = page.getViewport({ scale: 1 });
    const availableWidth = Math.max(260, canvas.parentElement.clientWidth - 20);
    const scale = Math.min(2, Math.max(0.65, availableWidth / baseViewport.width));
    const viewport = page.getViewport({ scale });
    const outputScale = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
    canvas.width = Math.max(1, Math.floor(viewport.width * outputScale));
    canvas.height = Math.max(1, Math.floor(viewport.height * outputScale));
    canvas.style.width = `${Math.floor(viewport.width)}px`;
    canvas.style.height = `${Math.floor(viewport.height)}px`;
    canvas.dataset.rendered = 'false';
    const context = canvas.getContext('2d', { alpha: false });
    previewPdfRenderTask = page.render({
      canvasContext: context,
      viewport,
      transform: outputScale === 1 ? null : [outputScale, 0, 0, outputScale, 0, 0],
      background: '#FFFFFF'
    });
    await previewPdfRenderTask.promise;
    previewPdfRenderTask = null;
    previewPdfPage = pageNumber;
    canvas.dataset.rendered = 'true';
    status.textContent = `第 ${pageNumber} / ${documentHandle.numPages} 页`;
    previous.disabled = pageNumber <= 1;
    next.disabled = pageNumber >= documentHandle.numPages;
  };

  const renderPdf = async (receipt, panel, item) => {
    const engine = await pdfEngine();
    const bytes = bytesFromBase64(receipt.data_base64);
    const loadingTask = engine.getDocument({
      data: bytes,
      isEvalSupported: false,
      useWorkerFetch: false,
      isImageDecoderSupported: false
    });
    previewPdfDocument = await loadingTask.promise;
    const shell = document.createElement('div');
    shell.className = 'desktop-inbox-pdf-shell';
    const toolbar = document.createElement('div');
    toolbar.className = 'desktop-inbox-pdf-toolbar';
    const previous = document.createElement('button');
    previous.className = 'soft-button';
    previous.type = 'button';
    previous.textContent = '上一页';
    const status = document.createElement('span');
    status.className = 'desktop-inbox-pdf-page-status';
    status.setAttribute('aria-live', 'polite');
    const next = document.createElement('button');
    next.className = 'soft-button';
    next.type = 'button';
    next.textContent = '下一页';
    toolbar.append(previous, status, next);
    const canvasWrap = document.createElement('div');
    canvasWrap.className = 'desktop-inbox-pdf-canvas-wrap';
    const canvas = document.createElement('canvas');
    canvas.className = 'desktop-inbox-pdf-canvas';
    canvas.setAttribute('aria-label', `${item.name || 'PDF'} 第 1 页`);
    canvasWrap.append(canvas);
    shell.append(toolbar, canvasWrap);
    panel.append(shell);
    const show = async (pageNumber) => {
      if (previewPdfRenderTask) return;
      await renderPdfPage(previewPdfDocument, pageNumber, canvas, status, previous, next);
      canvas.setAttribute('aria-label', `${item.name || 'PDF'} 第 ${pageNumber} 页`);
    };
    previous.addEventListener('click', () => void show(Math.max(1, previewPdfPage - 1)));
    next.addEventListener('click', () => void show(Math.min(previewPdfDocument.numPages, previewPdfPage + 1)));
    await show(1);
  };

  const renderPreview = async (receipt) => {
    const panel = q('#inbox-preview');
    const item = receipt.item || {};
    setInspectorDeleteItem(item);
    resetPreviewObject();
    setRefinementInspectorMode(false);
    selectedRefinementProjectionId = null;
    panel.replaceChildren();
    q('#inbox-detail-title').textContent = item.name || '文件';
    q('#inbox-detail-type').textContent = {
      TEXT: '文本 / Markdown', PDF: 'PDF 文档', IMAGE: '图像文件',
      PROPERTY_ONLY: 'Office 文件', UNSUPPORTED: '其他文件'
    }[receipt.mode] || '其他文件';
    q('#inbox-detail-size').textContent = formatBytes(item.bytes);
    q('#inbox-detail-state').textContent = item.display_state || item.state || '—';
    q('#inbox-detail-created').textContent = item.created_at || '—';
    q('#inbox-detail-modified').textContent = item.updated_at || '—';
    q('#inbox-detail-path').textContent = '收件箱 / ' + (item.name || '—');
    q('#inbox-detail-id').textContent = item.item_id || '—';
    q('#inbox-preview-heading').textContent = receipt.mode === 'PROPERTY_ONLY' ? '文件属性' : '预览';
    const heading = document.createElement('h5');
    heading.textContent = (receipt.sample ? '示例 · ' : '') + ({
      TEXT: '预览', PDF: '预览', IMAGE: '预览',
      PROPERTY_ONLY: 'Office 文件属性', UNSUPPORTED: '文件属性'
    }[receipt.mode] || '预览');
    panel.append(heading);
    if (receipt.mode === 'TEXT') {
      const block = document.createElement('pre');
      block.className = 'desktop-inbox-preview-code';
      block.textContent = receipt.text || '';
      panel.append(block);
      if (receipt.truncated) {
        const note = document.createElement('p');
        note.textContent = '文件较大，仅显示前 256 KB。';
        panel.append(note);
      }
    } else if (receipt.mode === 'PDF') {
      await renderPdf(receipt, panel, item);
    } else if (receipt.mode === 'IMAGE') {
      const blob = new Blob([bytesFromBase64(receipt.data_base64)], { type: receipt.mime_type });
      previewObjectUrl = URL.createObjectURL(blob);
      const image = document.createElement('img');
      image.className = 'desktop-inbox-image-preview';
      image.src = previewObjectUrl;
      image.alt = item.name || '图像预览';
      panel.append(image);
    } else {
      const block = document.createElement('div');
      block.className = 'desktop-inbox-preview-code';
      const workload = item.workload || {};
      block.textContent = [
        '文件名  ' + (item.name || '—'),
        '文件大小  ' + formatBytes(item.bytes),
        (workload.label || '属性') + '  ' + (workload.value || '—'),
        '文件状态  ' + (item.display_state || item.state || '—')
      ].join('\n');
      panel.append(block);
    }
  };

  const loadPreview = async (itemId) => {
    if (!nativeReady() || !itemId) return;
    if (loadedPreviewItemId === itemId) return;
    const sequenceId = ++previewSequence;
    q('#inbox-preview').textContent = '加载中';
    const receipt = await window.pywebview.api.get_inbox_preview(itemId);
    if (sequenceId !== previewSequence) return;
    await renderPreview(receipt);
    loadedPreviewItemId = itemId;
  };

  root.addEventListener('contextmenu', (event) => {
    const card = event.target.closest('.desktop-inbox-card[data-item-id]');
    contextItemId = card?.dataset.itemId || null;
    if (card?.dataset.itemKind === 'SESSION_REFINEMENT') {
      event.preventDefault();
      const menu = q('#inbox-context');
      if (menu) menu.hidden = true;
    }
  }, true);

  root.addEventListener('click', (event) => {
    const card = event.target.closest('.desktop-inbox-card[data-item-id]');
    const previewAction = event.target.closest('[data-open-action="preview"]');
    if (!card && !previewAction) return;
    if (event.target.closest('button,input') && !previewAction) return;
    const itemId = card?.dataset.itemId || contextItemId;
    if (card?.dataset.itemKind === 'SESSION_REFINEMENT') {
      void loadRefinementDetail(itemId, card)
        .catch((error) => showToast('读取精炼任务失败：' + error.message));
      return;
    }
    void loadPreview(itemId).catch((error) => showToast('预览失败：' + error.message));
  });

  document.addEventListener('click', (event) => {
    const target = event.target.closest('button');
    if (!target || !root.contains(target)) return;
    if (released.has(target)) {
      released.delete(target);
      return;
    }
    const effectful = target.matches(
      '#inbox-add,#inbox-refresh,#inbox-auto,#inbox-undo,#inbox-redo,#inbox-toast-undo,'
      + '#inbox-star-selected,#inbox-unstar-selected,#inbox-remove-selected,'
      + '.desktop-inbox-star,.desktop-inbox-delete,#inbox-locate,#inbox-external,#inbox-open-session,#inbox-delete-task,'
      + '[data-open-action="folder"],[data-open-action="external"]'
    );
    if (!effectful) return;
    stop(event);
    if (!nativeReady()) {
      showToast('本地服务未连接，请重试。');
      return;
    }
    void (async () => {
      await bootstrap();
      if (target.id === 'inbox-delete-task') {
        await deleteInspectorTask(target);
      } else if (target.id === 'inbox-add') {
        const picked = await window.pywebview.api.pick_inbox_files();
        if (picked && picked.paths && picked.paths.length) await importPaths(picked.paths, 'picker');
        else showToast('未选择文件；收件箱保持不变。');
      } else if (target.id === 'inbox-refresh') {
        if(target.getAttribute('aria-busy')==='true')return;
        target.setAttribute('aria-busy','true');
        try {
        await call('inbox.refresh', {});
        projection = await call('inbox.projection', { batch_mode: false });
        syncProjection(projection);
        showToast('收件箱已从本地持久化状态刷新。');
        } finally { target.removeAttribute('aria-busy'); }
      } else if (target.id === 'inbox-auto') {
        const enabled = target.getAttribute('aria-checked') !== 'true';
        await call('inbox.set_auto_run', { enabled });
        projection = await call('inbox.projection', { batch_mode: false });
        syncProjection(projection);
        target.setAttribute('aria-checked', String(projection?.auto_run?.enabled === true));
        showToast(enabled ? '已开启自动处理' : '已关闭自动处理');
      } else if (target.id === 'inbox-undo' || target.id === 'inbox-toast-undo') {
        await call('inbox.undo', {});
        projection = await call('inbox.projection', { batch_mode: false });
        syncProjection(projection);
        showToast('已撤销上一次移除。');
      } else if (target.id === 'inbox-redo') {
        await call('inbox.redo', {});
        projection = await call('inbox.projection', { batch_mode: false });
        syncProjection(projection);
        showToast('已重做上一次移除。');
      } else if (target.id === 'inbox-star-selected' || target.id === 'inbox-unstar-selected') {
        const starred = target.id === 'inbox-star-selected';
        for (const item_id of selectedItemIds()) {
          await call('inbox.set_starred', { item_id, starred });
        }
        projection = await call('inbox.projection', { batch_mode: false });
        syncProjection(projection);
      } else if (target.id === 'inbox-remove-selected') {
        const item_ids = selectedItemIds();
        if (item_ids.length) {
          await call('inbox.soft_delete', {
            item_ids,
            operation_id: 'ui-batch-' + Date.now() + '-' + (++operationSequence)
          });
        }
        projection = await call('inbox.projection', { batch_mode: false });
        syncProjection(projection);
        showToast('已移除所选文件，可在本次会话撤销。', true);
      } else if (target.classList.contains('desktop-inbox-star')) {
        const item_id = itemIdFor(target);
        const starred = target.getAttribute('aria-pressed') !== 'true';
        await call('inbox.set_starred', { item_id, starred });
        projection = await call('inbox.projection', { batch_mode: false });
        syncProjection(projection);
      } else if (target.classList.contains('desktop-inbox-delete')) {
        const item_id = itemIdFor(target);
        const card = target.closest('.desktop-inbox-card');
        if (card?.dataset.itemKind === 'SESSION_REFINEMENT') {
          await call('sessions.refinement_cancel', { inbox_item_id:item_id });
        } else {
          await call('inbox.soft_delete', {
            item_ids: [item_id],
            operation_id: 'ui-delete-' + Date.now() + '-' + (++operationSequence)
          });
        }
        projection = await call('inbox.projection', { batch_mode: false });
        syncProjection(projection);
        showToast(card?.dataset.itemKind === 'SESSION_REFINEMENT'
          ? '已取消精炼任务并从收件箱移除。'
          : '已从收件箱移除，可在本次会话撤销。', card?.dataset.itemKind !== 'SESSION_REFINEMENT');
      } else if (target.id === 'inbox-open-session') {
        if (!selectedRefinementProjectionId) throw new Error('精炼任务缺少来源会话身份');
        window.__Desktop_SESSION_NAV_TARGET__ = selectedRefinementProjectionId;
        root.querySelector('.desktop-nav-item[data-page="会话管理"]')?.click();
        window.requestAnimationFrame(() => window.dispatchEvent(new CustomEvent('desktop:open-session', {
          detail:{ local_projection_id:selectedRefinementProjectionId }
        })));
      } else if (target.id === 'inbox-locate' || target.dataset.openAction === 'folder') {
        const itemId = target.dataset.openAction === 'folder' ? contextItemId : selectedItemId();
        if (!itemId) throw new Error('请先选择文件');
        await window.pywebview.api.locate_inbox_item(itemId);
        showToast('已定位文件');
      } else if (target.id === 'inbox-external' || target.dataset.openAction === 'external') {
        const itemId = target.dataset.openAction === 'external' ? contextItemId : selectedItemId();
        if (!itemId) throw new Error('请先选择文件');
        await window.pywebview.api.open_inbox_item(itemId);
        showToast('已打开文件');
      } else {
        throw new Error('未知收件箱动作');
      }
    })().catch((error) => showToast(publicInboxFailure(error)));
  }, true);

  q('#inbox-body').addEventListener('drop', (event) => {
    event.preventDefault();
    q('#inbox-drop').hidden = true;
    const fileCount = event.dataTransfer && event.dataTransfer.files
      ? event.dataTransfer.files.length : 0;
    if (!nativeReady()) {
      showToast('本地服务未连接，请重试。');
      return;
    }
    if (!fileCount) {
      showToast('拖放内容中没有可导入的本地文件。');
      return;
    }
    showToast('正在读取文件…');
  }, true);

  root.addEventListener('keydown', (event) => {
    if (!nativeReady() || !event.ctrlKey) return;
    const key = event.key.toLowerCase();
    if (key === 'z' || key === 'y') {
      stop(event);
      q(key === 'z' ? '#inbox-undo' : '#inbox-redo').click();
    }
  }, true);

  const refresh = async () => {
    await call('inbox.refresh', {});
    projection = await call('inbox.projection', { batch_mode: false });
    syncProjection(projection);
    return projection;
  };

  window.__Desktop_INBOX_BRIDGE__ = Object.freeze({
    methods: METHODS,
    call,
    bootstrap,
    importPaths,
    renderPreview,
    loadPreview,
    acceptNativeDrop: async (stagedPaths) => {
      if (!Array.isArray(stagedPaths) || stagedPaths.length === 0) {
        throw new Error('INBOX_NATIVE_DROP_STAGE_EMPTY');
      }
      return importPaths(stagedPaths, 'drop');
    },
    reportNativeDropFailure: (reasonCode) => {
      const copy = {
        INBOX_DROP_LOCATOR_MISSING: '无法读取拖放文件的位置',
        INBOX_DROP_TOO_MANY_FILES: '一次最多拖放 32 个文件',
        INBOX_DROP_STAGE_REJECTED: '拖放文件未通过检查',
        INBOX_DROP_IMPORT_FAILED: '拖放导入失败；文件仍保留在原位置。'
      }[String(reasonCode)] || '拖放导入失败；文件仍保留在原位置。';
      showToast(copy);
      return { status: 'REPORTED', reason_code: String(reasonCode || 'INBOX_DROP_IMPORT_FAILED') };
    },
    refresh,
    inspect: () => ({
      method_count: METHODS.length,
      native_transport: nativeReady(),
      bootstrapped: Boolean(contract && projection && effects),
      service_card_count: projection && projection.cards && projection.cards.length || null,
      bound_dom_card_count: cards().filter((card) => card.dataset.itemId).length,
      selected_item_id: selectedItemId(),
      inspector_open: q('#desktop-right-panel')?.getAttribute('aria-hidden') === 'false',
      native_drop_bridge_ready: true,
      workspace_drop_default_navigation_blocked: workspaceDropBoundary,
      last_receipt: lastReceipt,
      external_network_calls: effects && effects.external_network_calls || 0,
      external_model_calls: effects && effects.external_model_calls || 0,
      production_ingestion: effects && effects.production_ingestion || 0
    })
  });

  root.addEventListener('desktop:inspector-closed', () => {
    setInspectorDeleteItem(null);
    loadedPreviewItemId = null;
  });

  window.addEventListener('desktop-integrated-route', (event) => {
    const route = event.detail?.route;
    if (route === 'inbox' && nativeReady()) {
      void refresh().catch((error) => showToast('收件箱刷新失败：' + error.message));
    }
  });

  window.addEventListener('pywebviewready', () => {
    void bootstrap().catch((error) => showToast('收件箱启动失败：' + error.message));
  }, { once: true });
  if (nativeReady()) void bootstrap();
})();

