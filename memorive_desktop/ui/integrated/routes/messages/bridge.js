/* The messages page's calls to the desktop service (messages.*): the message list, reading and archiving, bulk actions,
   and following a message to the page it is about. Refreshes every 5 seconds while the page is open. Publishes
   window.__Desktop_MESSAGES_BRIDGE__; shared/product-bridge.js and memorive/desktop/api/page_events.py call its
   refresh(). */
(() => {
  'use strict';

  const methods = Object.freeze([
    'messages.get_contract',
    'messages.bootstrap',
    'messages.list',
    'messages.switch_mode',
    'messages.open_detail',
    'messages.close_detail',
    'messages.toggle_star',
    'messages.confirm',
    'messages.archive_confirmed',
    'messages.mark_all_read',
    'messages.bulk_apply',
    'messages.refresh_archive',
    'messages.navigate',
    'messages.notification_deliver',
    'messages.notification_activate',
    'messages.effect_metrics'
  ]);
  const bindings = new Map();
  let receiptCount = 0;
  let selectedMessageId = null;
  document.getElementById('desktop-messages-preview')?.addEventListener('desktop:inspector-closed', () => { selectedMessageId = null; });
  let bootstrapPromise = null;
  let refreshPromise = null;

  const nativeCall = async (method, params = {}) => {
    if (!window.pywebview?.api?.call) {
      return {
        schema_version: 'MessagesBrowserOnlyReceipt-v1',
        method,
        params,
        status: 'BROWSER_TEST_NO_NATIVE_TRANSPORT'
      };
    }
    const result = await (window.DesktopDecorations?.trackCall(method, () => window.pywebview.api.call(method, params)) || window.pywebview.api.call(method, params));
    receiptCount += 1;
    window.dispatchEvent(new CustomEvent('desktop:messages-receipt', { detail: { method, result } }));
    return result;
  };

  const bindRows = (projection) => {
    const rows = Array.isArray(projection?.records) ? projection.records : [];
    window.__Desktop_MESSAGES_VIEW__?.renderRows(rows);
    bindings.clear();
    const byId = new Map(rows.map((row) => [row.message_id, row]));
    document.querySelectorAll('.desktop-messages-card[data-message-id]').forEach((card) => {
      const row = byId.get(card.dataset.messageId);
      if (!row) return;
      bindings.set(row.message_id, { card, record: row });
    });
    if (selectedMessageId && bindings.has(selectedMessageId)) {
      window.__Desktop_MESSAGES_VIEW__?.openDetailById(selectedMessageId);
    } else if (selectedMessageId) {
      selectedMessageId = null;
      window.__Desktop_MESSAGES_VIEW__?.closeDetail?.();
    }
    return rows;
  };

  const refresh = () => {
    if (refreshPromise) return refreshPromise;
    refreshPromise = refreshRows().finally(() => { refreshPromise = null; });
    return refreshPromise;
  };
  const refreshRows = async () => {
    const list = document.querySelector('#messages-list');
    const scrollTop = list?.scrollTop || 0;
    const focusedMessageId = document.activeElement?.closest?.('.desktop-messages-card')?.dataset?.messageId || null;
    await nativeCall('messages.refresh_archive', {});
    const projection = await nativeCall('messages.list', {});
    const records = Array.isArray(projection?.rows) ? projection.rows : [];
    const checkedIds = selectedIds();
    const previousRows = [...bindings.values()].map(row => row.record);
    if (JSON.stringify(records) !== JSON.stringify(previousRows)) {
      bindRows({ records });
      checkedIds.forEach(id => {
        const box = document.querySelector(`.desktop-messages-card[data-message-id="${CSS.escape(id)}"] .desktop-messages-check`);
        if (box) box.checked = true;
      });
    }
    if (list) list.scrollTop = scrollTop;
    if (focusedMessageId) {
      document.querySelector(`.desktop-messages-card[data-message-id="${CSS.escape(focusedMessageId)}"]`)
        ?.focus?.({ preventScroll:true });
    }
    return { records, record_count: records.length, status: projection.status };
  };

  const bootstrap = async () => {
    if (!bootstrapPromise) {
      bootstrapPromise = nativeCall('messages.bootstrap', {}).then((projection) => ({
        schema_version: 'MessagesMessagesBridgeBootstrap-v1',
        records: projection.records,
        record_count: projection.record_count,
        binding_count: bindRows(projection).length,
        dom_binding_count: bindings.size,
        current_count: projection.current_count ?? 0,
        history_count: projection.history_count ?? 0,
        production_event_reads: 0,
        production_message_reads: 0,
        synthetic_only: projection.synthetic_only === true,
        status: 'PASS'
      })).catch((error) => {
        bootstrapPromise = null;
        throw error;
      });
    }
    return bootstrapPromise;
  };

  const messageIdForCard = (card) => card?.dataset?.messageId || null;
  const selectedIds = () => [...document.querySelectorAll('.desktop-messages-card .desktop-messages-check:checked')]
    .map((box) => messageIdForCard(box.closest('.desktop-messages-card')))
    .filter(Boolean);

  const navigateToTarget = async (locator, messageId = null, nodeId = null) => {
    const receipt = await nativeCall('messages.navigate', {
      locator,
      ...(messageId ? { message_id:messageId } : {}),
      ...(nodeId ? { node_id:nodeId } : {})
    });
    if (receipt?.research_run_id) {
      await window.__Desktop_RESEARCH__?.openResult(receipt.research_run_id);
      return receipt;
    }
    if(receipt?.research_task_id){
      await window.__Desktop_RESEARCH__?.openTask(receipt.research_task_id);
      return receipt;
    }
    if (receipt?.status !== 'PASS') {
      if (receipt?.status === 'TARGET_UNAVAILABLE') await refresh();
      window.dispatchEvent(new CustomEvent('desktop:messages-navigation-result', {
        detail:{ ...receipt, message_id:messageId }
      }));
      return receipt;
    }
    const route = receipt.route;
    if (window.__Desktop_INTEGRATED_APP__?.activate) {
      window.__Desktop_INTEGRATED_APP__.activate(route);
    } else {
      await window.__Desktop_PRODUCT_BRIDGE__?.dispatch(
        'navigation.select', { route }, 'messages-target'
      );
    }
    if (route === 'current-task') {
      const currentTaskBridge = window.__Desktop_CURRENT_TASK_BRIDGE__;
      if (!currentTaskBridge?.openProductionRun) {
        const unavailable = { ...receipt, status:'CURRENT_TASK_UI_UNAVAILABLE', inspector_open:false };
        window.dispatchEvent(new CustomEvent('desktop:messages-navigation-result', {
          detail:{ ...unavailable, message_id:messageId }
        }));
        return unavailable;
      }
      const opened = await currentTaskBridge.openProductionRun(
        receipt.job_id || receipt.object_id,
        receipt.target_node_id || nodeId || null
      );
      const exact = opened?.status === 'PASS'
        && opened?.inspector_open === true
        && opened?.target_node_id === (receipt.target_node_id || nodeId || null);
      const result = {
        ...receipt,
        inspector_open:Boolean(exact),
        task_opened:opened?.task_opened === true,
        status:exact ? 'PASS' : 'CURRENT_TASK_INSPECTOR_NOT_OPEN'
      };
      window.dispatchEvent(new CustomEvent('desktop:messages-navigation-result', {
        detail:{ ...result, message_id:messageId }
      }));
      return result;
    }
    window.dispatchEvent(new CustomEvent('desktop:messages-navigation-result', {
      detail:{ ...receipt, message_id:messageId }
    }));
    return receipt;
  };

  document.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const card = target.closest('.desktop-messages-card');
    const messageId = messageIdForCard(card);

    if (card && target.closest('.desktop-messages-copy')) {
      if (messageId) window.__Desktop_MESSAGES_VIEW__?.openDetailById(messageId);
      if (selectedMessageId === messageId) return;
      selectedMessageId = messageId;
      if (messageId) void nativeCall('messages.open_detail', { message_id: messageId }).then(refresh);
      return;
    }
    if (card && target.closest('.desktop-messages-star')) {
      if (messageId) void nativeCall('messages.toggle_star', { message_id: messageId });
      return;
    }
    const cardAction = card && target.closest('.desktop-messages-action');
    if (cardAction && messageId) {
      if (cardAction.dataset.command === 'goto' && card.dataset.locator) {
        void navigateToTarget(
          card.dataset.locator,
          messageId,
          card.dataset.targetNodeId || null
        );
      } else if (cardAction.dataset.command === 'confirm') {
        void nativeCall('messages.confirm', { message_id: messageId, from_detail: false }).then(refresh);
      } else if (cardAction.dataset.command === 'archive-confirmed') {
        void nativeCall('messages.archive_confirmed', { message_id: messageId }).then(refresh);
      }
      return;
    }

    const mode = target.closest('#messages-mode button[data-mode]');
    if (mode) {
      selectedMessageId = null;
      void nativeCall('messages.switch_mode', { mode: mode.dataset.mode.toUpperCase() });
      return;
    }
    if (target.closest('#messages-read')) {
      void nativeCall('messages.mark_all_read', {});
      return;
    }
    if (target.closest('#messages-refresh')) {
      selectedMessageId = null;
      void window.__Desktop_UI_CONTROLS__.busy(target.closest('#messages-refresh'),()=>nativeCall('messages.refresh_archive', {}).then(refresh));
      return;
    }
    if (target.closest('#messages-batch-read')) {
      void nativeCall('messages.bulk_apply', { message_ids: selectedIds(), action: 'MARK_READ' });
      return;
    }
    if (target.closest('#messages-batch-confirm')) {
      void nativeCall('messages.bulk_apply', { message_ids: selectedIds(), action: 'CONFIRM' });
      return;
    }
    if (target.closest('#messages-star-add')) {
      void nativeCall('messages.bulk_apply', { message_ids: selectedIds(), action: 'STAR' });
      return;
    }
    if (target.closest('#messages-star-remove')) {
      void nativeCall('messages.bulk_apply', { message_ids: selectedIds(), action: 'UNSTAR' });
      return;
    }
    if (target.closest('#messages-close')) {
      selectedMessageId = null;
      window.__Desktop_MESSAGES_VIEW__?.closeDetail?.();
      void nativeCall('messages.close_detail', {});
      return;
    }
    if (target.closest('#messages-detail-goto') && selectedMessageId) {
      const selected = bindings.get(selectedMessageId)?.card;
      const locator = selected?.dataset?.locator;
      if (locator) void navigateToTarget(
        locator,
        selectedMessageId,
        selected?.dataset?.targetNodeId || null
      );
      return;
    }
    if (target.closest('#messages-detail-confirm') && selectedMessageId) {
      void nativeCall('messages.confirm', {
        message_id: selectedMessageId,
        from_detail: true
      }).then(refresh);
    }
  }, true);

  window.addEventListener('desktop-integrated-route', (event) => {
    const route = event.detail?.route;
    if (route === 'messages') void refresh();
  });
  window.setInterval(() => {
    if (!document.hidden && document.body.dataset.desktopActiveRoute === 'messages'
        && window.pywebview?.api?.call) {
      void refresh().catch(() => {}); // Preserve the last complete projection on transport failure.
    }
  }, 5000);

  window.__Desktop_MESSAGES_BRIDGE__ = Object.freeze({
    bootstrap,
    refresh,
    call: nativeCall,
    navigateToTarget,
    deliverNotification: (messageId) => nativeCall('messages.notification_deliver', { message_id: messageId }),
    activateNotification: (messageId) => nativeCall('messages.notification_activate', { message_id: messageId }),
    inspect: () => ({
      method_count: methods.length,
      methods: [...methods],
      binding_count: bindings.size,
      receipt_count: receiptCount,
      selected_message_id: selectedMessageId,
      native_transport: Boolean(window.pywebview?.api?.call),
      production_event_reads: 0,
      production_message_reads: 0,
      windows_notification_registrations: 0,
      synthetic_only: false
    })
  });
})();

