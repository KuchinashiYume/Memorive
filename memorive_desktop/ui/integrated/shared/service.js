/* The one way the page's scripts call the desktop service. window.__Desktop_SERVICE__.call(method, params) sends
   window.pywebview.api.call(method, params) to ProductApi.call (memorive/desktop/api/product_api.py) and returns
   its answer as a promise. While a route waits for its first list, DesktopDecorations.trackCall (shared/decorations.js)
   shows the route's loading placeholder. Before pywebview is ready the promise is rejected with
   DESKTOP_SERVICE_NOT_CONNECTED; scripts that call at start wait for the pywebviewready event.
   Every route bridge and settings script calls through this; a bridge adds only what is its own (a receipt event,
   a request number). */
(() => {
  'use strict';
  const call = async (method, params = {}) => {
    const api = window.pywebview?.api;
    if (typeof api?.call !== 'function') throw new Error('DESKTOP_SERVICE_NOT_CONNECTED');
    const send = () => api.call(method, params);
    return window.DesktopDecorations ? window.DesktopDecorations.trackCall(method, send) : send();
  };
  window.__Desktop_SERVICE__ = Object.freeze({ call });
})();
