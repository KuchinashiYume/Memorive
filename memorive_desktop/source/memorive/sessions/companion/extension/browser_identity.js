"use strict";
(() => {
  const detect = async (nav = navigator) => {
    const ua = String(nav.userAgent || "");
    if (/Edg\//.test(ua)) return "EDGE";
    try {
      if (typeof nav.brave?.isBrave === "function" && await nav.brave.isBrave()) return "BRAVE";
    } catch (_) { return "CHROMIUM"; }
    if (/Chrome\//.test(ua)) return "CHROME";
    return "CHROMIUM";
  };
  globalThis.MEMORIVE_BROWSER_IDENTITY = Object.freeze({detect});
})();
