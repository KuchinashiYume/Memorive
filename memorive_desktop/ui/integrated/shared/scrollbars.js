/* Marks scrollable areas (data-memo-scroll) and the one being scrolled or dragged (data-memo-scroll-active), so that
   scrollbars.css shows a thin scrollbar only there. Publishes window.__MEMO_SCROLLBARS__. */
(() => {
  'use strict';
  if (window.__MEMO_SCROLLBARS__) return;
  const tracked = new Set();
  const active = new Set();
  const pending = new Set();
  let frame = 0;
  let dragging = false;
  const inspect = element => {
    if (!(element instanceof HTMLElement)) return;
    const style = getComputedStyle(element);
    const scrollable = /^(auto|scroll)$/.test(style.overflowX) || /^(auto|scroll)$/.test(style.overflowY);
    // Do not reserve space on clipping-only shells (overflow:hidden).
    if (scrollable) {
      if (!element.hasAttribute('data-memo-scroll')) element.setAttribute('data-memo-scroll', '');
      tracked.add(element);
    } else if (tracked.delete(element)) {
      element.removeAttribute('data-memo-scroll');
      element.removeAttribute('data-memo-scroll-active');
      active.delete(element);
    }
  };
  const scan = root => {
    inspect(root);
    root.querySelectorAll('*').forEach(inspect);
  };
  const flush = () => {
    frame = 0;
    const roots = [...pending].filter(node => node.isConnected);
    pending.clear();
    roots.filter(node => !roots.some(other => other !== node && other.contains(node))).forEach(scan);
    for (const node of tracked) if (!node.isConnected) {
      tracked.delete(node);
      active.delete(node);
    }
  };
  const schedule = root => {
    if (!(root instanceof HTMLElement)) return;
    pending.add(root);
    if (!frame) frame = requestAnimationFrame(flush);
  };
  const setActive = next => {
    for (const node of active) if (!next.has(node)) node.removeAttribute('data-memo-scroll-active');
    for (const node of next) if (!active.has(node)) node.setAttribute('data-memo-scroll-active', '');
    active.clear();
    next.forEach(node => active.add(node));
  };
  const move = event => {
    if (dragging || event.pointerType === 'touch') return;
    const next = new Set();
    // Only ancestors under the pointer: no document scan on mouse movement.
    for (const node of event.composedPath()) {
      if (!tracked.has(node)) continue;
      const box = node.getBoundingClientRect();
      const rtl = getComputedStyle(node).direction === 'rtl';
      const vertical = node.scrollHeight > node.clientHeight && Math.abs(event.clientX - (rtl ? box.left : box.right)) <= 24;
      const horizontal = node.scrollWidth > node.clientWidth && Math.abs(event.clientY - box.bottom) <= 24;
      if (vertical || horizontal) next.add(node);
    }
    setActive(next);
  };
  scan(document.documentElement);
  new MutationObserver(records => {
    for (const record of records) {
      if (record.type === 'attributes') schedule(record.target);
      else {
        for (const node of record.addedNodes) schedule(node);
        if (record.removedNodes.length) schedule(record.target);
      }
    }
  }).observe(document.documentElement, {subtree:true, childList:true, attributes:true, attributeFilter:['class','style','hidden','open']});
  window.addEventListener('resize', () => schedule(document.body), {passive:true});
  document.addEventListener('pointermove', move, {passive:true});
  document.addEventListener('pointerdown', () => { dragging = active.size > 0; }, {passive:true});
  window.addEventListener('pointerup', event => {
    dragging = false;
    move(event);
  }, {passive:true});
  document.addEventListener('pointerleave', () => { if (!dragging) setActive(new Set()); }, {passive:true});
  window.addEventListener('blur', () => {
    dragging = false;
    setActive(new Set());
  });
  window.__MEMO_SCROLLBARS__ = Object.freeze({refresh:() => scan(document.documentElement)});
})();

