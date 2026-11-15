(() => {
  'use strict';
  if (window.__Desktop_PRODUCT_BRIDGE_ACTIVE__) return;
  Object.defineProperty(window, '__Desktop_PRODUCT_BRIDGE_ACTIVE__', {
    value: true,
    configurable: false,
    enumerable: false,
    writable: false
  });
  const allowedEvents = new Set([
    'navigation.select', 'window.sidebar.toggle', 'window.inspector.toggle',
    'window.bottom.toggle', 'window.panel.resize', 'logo.activate',
    'motion.replay', 'control.activate', 'keyboard.shortcut'
  ]);
  let requestSequence = 0;
  const receipts = [];
  let coreRefreshPromise = null;
  let lastCoreRevision = 0;
  const routeByFile = new Map();

  const safeValue = (value) => {
    if (value === null || ['string', 'number', 'boolean'].includes(typeof value)) return value;
    if (Array.isArray(value)) return value.slice(0, 32).map(safeValue);
    if (typeof value === 'object') {
      const result = {};
      Object.keys(value).sort().slice(0, 32).forEach((key) => { result[key] = safeValue(value[key]); });
      return result;
    }
    return String(value);
  };

  const dispatch = async (eventType, payload = {}, source = 'product-dom') => {
    if (!allowedEvents.has(eventType)) throw new Error(`Desktop_TYPED_EVENT_NOT_ALLOWLISTED:${eventType}`);
    requestSequence += 1;
    const request = {
      event_type: eventType,
      payload: safeValue(payload),
      request_id: `desktop-ui-${String(requestSequence).padStart(8, '0')}`,
      source
    };
    let receipt;
    if (window.pywebview?.api?.dispatch) {
      receipt = await window.pywebview.api.dispatch(request);
    } else {
      receipt = {
        schema_version: 'DesktopTypedEventReceipt-v1',
        request_id: request.request_id,
        event_type: eventType,
        transport_status: 'BROWSER_TEST_NO_NATIVE_TRANSPORT',
        business_state_mutated: false
      };
    }
    receipts.push(receipt);
    window.dispatchEvent(new CustomEvent('desktop:typed-event-receipt', { detail: receipt }));
    return receipt;
  };

  document.addEventListener('click', (event) => {
    const anchor = event.target.closest('a.nav-link');
    if (anchor) {
      const file = anchor.getAttribute('href') || '';
      const route = routeByFile.get(file);
      if (route) {
        event.preventDefault();
        void dispatch('navigation.select', { route, adopted_href: file }, 'part2-navigation');
        return;
      }
    }
    const target = event.target.closest('button, [role="button"], [role="switch"]');
    if (!target) return;
    let eventType = 'control.activate';
    const payload = {
      id: target.id || null,
      action: target.dataset.action || null,
      page: target.dataset.page || null,
      role: target.getAttribute('role') || target.tagName.toLowerCase()
    };
    if (target.matches('.desktop-nav-item')) eventType = 'navigation.select';
    else if (target.id === 'desktop-toggle-left' || target.dataset.action === 'toggle-sidebar') eventType = 'window.sidebar.toggle';
    else if (target.id === 'desktop-toggle-right' || target.dataset.action === 'toggle-inspector') eventType = 'window.inspector.toggle';
    else if (target.id === 'desktop-toggle-bottom') eventType = 'window.bottom.toggle';
    else if (target.id === 'desktop-logo' || target.hasAttribute('data-logo')) eventType = 'logo.activate';
    else if (target.id === 'replay-motion') eventType = 'motion.replay';
    void dispatch(eventType, payload);
  }, true);

  document.addEventListener('keydown', (event) => {
    if (!event.ctrlKey) return;
    if (event.code === 'KeyB' || event.code === 'KeyJ') {
      void dispatch('keyboard.shortcut', { code: event.code, alt: event.altKey, shift: event.shiftKey });
    }
  }, true);

  const refreshCoreSurfaces = (detail = {}) => {
    const revision = Number(detail.revision || 0);
    if (revision && revision <= lastCoreRevision) return coreRefreshPromise || Promise.resolve([]);
    if (revision) lastCoreRevision = revision;
    window.__Desktop_WORK_LOG_BRIDGE__?.notifyPending?.(detail);
    if (coreRefreshPromise) return coreRefreshPromise;
    coreRefreshPromise = Promise.allSettled([
      window.__Desktop_CURRENT_TASK_BRIDGE__?.refresh?.(),
      window.__Desktop_INBOX_BRIDGE__?.refresh?.(),
      window.__Desktop_MESSAGES_BRIDGE__?.refresh?.(),
      window.__Desktop_LIBRARY_BRIDGE__?.refresh?.()
    ]).finally(() => { coreRefreshPromise = null; });
    return coreRefreshPromise;
  };

  window.addEventListener('desktop:core-durable-event', (event) => {
    void refreshCoreSurfaces(event.detail || {});
  });

  window.setInterval(() => {
    const poll = window.pywebview?.api?.core_event_poll;
    if (typeof poll !== 'function') return;
    void poll({}).then((receipt) => {
      if (receipt?.changed) {
        window.dispatchEvent(new CustomEvent('desktop:core-durable-event', {
          detail: {
            schema_version:'DesktopCoreDurableEvent-v1',
            revision:receipt.revision,
            fingerprint:receipt.fingerprint,
            snapshot:receipt.snapshot,
            observed_at:receipt.observed_at,
            fallback_poll:true
          }
        }));
      }
    }).catch(() => {});
  }, 15000);

  window.__Desktop_PRODUCT_BRIDGE__ = {
    dispatch,
    refreshCoreSurfaces,
    inspect: () => ({
      request_sequence: requestSequence,
      receipt_count: receipts.length,
      native_transport: Boolean(window.pywebview?.api?.dispatch),
      iframe_count: document.querySelectorAll('iframe').length,
      preview_control_count: document.querySelectorAll('.viz-controls, .desktop-preview-controls').length
    })
  };
})();

