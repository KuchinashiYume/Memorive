(() => {
  const initialize = () => {
    globalThis.lucide?.createIcons({ attrs: { width: 16, height: 16 } });
  };
  if (globalThis.lucide != null) {
    initialize();
    return;
  }
  document
    .getElementById("codex-visualization-lucide")
    ?.addEventListener("load", initialize, { once: true });
})();
