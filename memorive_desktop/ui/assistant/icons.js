/* Draws Lucide icons when the Lucide script is on the page. The assistant page loads no Lucide script and has no
   data-lucide elements, so in the shipped page this does nothing; it came with the page template. */
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
