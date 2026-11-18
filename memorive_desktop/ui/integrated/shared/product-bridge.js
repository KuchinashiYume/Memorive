/* What the page tells the desktop service, and what the service tells the page.

   dispatch(eventType, payload, source) sends a typed event. The page sends one kind: navigation.select with
   {route}, which the service keeps so that the window can reopen on that route. shared/app.js, the library,
   messages, work log and chat pages, and the built-in self-test in app.py use it.

   When jobs, documents or messages change, the service pushes desktop:core-durable-event
   (memorive/desktop/api/page_events.py); as a fallback this script asks core_event_poll every 15 seconds. Either
   way refreshCoreSurfaces() refreshes the current-task, inbox, messages and library pages and tells the work log. */
(() => {
  'use strict';
  let requestSequence = 0;
  let coreRefreshPromise = null;
  let lastCoreRevision = 0;

  const dispatch = async (eventType, payload = {}, source = 'product-dom') => {
    if (!window.pywebview?.api?.dispatch) return null;
    requestSequence += 1;
    return window.pywebview.api.dispatch({
      event_type: eventType,
      payload,
      request_id: `desktop-ui-${String(requestSequence).padStart(8, '0')}`,
      source,
    });
  };

  const refreshCoreSurfaces = (detail = {}) => {
    const revision = Number(detail.revision || 0);
    if (revision && revision <= lastCoreRevision) return coreRefreshPromise || Promise.resolve([]);
    if (revision) lastCoreRevision = revision;
    Memorive.workLogBridge?.notifyPending?.(detail);
    if (coreRefreshPromise) return coreRefreshPromise;
    coreRefreshPromise = Promise.allSettled([
      Memorive.currentTaskBridge?.refresh?.(),
      Memorive.inboxBridge?.refresh?.(),
      Memorive.messagesBridge?.refresh?.(),
      Memorive.libraryBridge?.refresh?.()
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

  Memorive.productBridge = { dispatch, refreshCoreSurfaces };
})();
