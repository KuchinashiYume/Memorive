"""JavaScript the page check runs inside the page.

EARLY runs before any page script (it is added with AddScriptToExecuteOnDocumentCreated): it records script
errors, security-policy refusals and console.error calls, and counts DOM changes.

HELPERS is evaluated once the page is ready: it wraps the native bridge so the check knows when calls are
pending, and defines the functions that settle the page and record it.
"""

EARLY = r"""
(() => {
  if (window.__check) return;
  const check = window.__check = { errors: [], mutations: 0, pending: 0 };
  const note = (kind, text) => { if (check.errors.length < 200) check.errors.push(kind + ': ' + String(text).slice(0, 400)); };
  window.addEventListener('error', (e) => note('error', (e.message || e) + ' @' + (e.filename || '').split('/').pop() + ':' + e.lineno));
  window.addEventListener('unhandledrejection', (e) => note('rejection', e.reason && (e.reason.stack || e.reason.message) || e.reason));
  document.addEventListener('securitypolicyviolation', (e) => note('csp', e.violatedDirective + ' ' + e.blockedURI + ' ' + (e.sample || '')));
  const original = console.error.bind(console);
  console.error = (...args) => { note('console', args.map(String).join(' ')); original(...args); };
  const start = () => new MutationObserver((list) => { check.mutations += list.length; })
    .observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
  if (document.documentElement) start(); else document.addEventListener('readystatechange', start, { once: true });
})();
"""

HELPERS = r"""
(() => {
  const check = window.__check;
  if (check.helpers) return 'already';
  check.helpers = true;
  // Count bridge calls in flight, so that settle() waits for the desktop service's answers.
  const api = window.pywebview && window.pywebview.api;
  if (api) for (const name of Object.keys(api)) {
    const fn = api[name];
    if (typeof fn !== 'function') continue;
    api[name] = function (...args) {
      check.pending++;
      let result;
      try { result = fn.apply(this, args); } catch (error) { check.pending--; throw error; }
      return Promise.resolve(result).finally(() => { check.pending--; });
    };
  }

  // Finish what has an end and park what loops; report DOM changes so far and bridge calls in flight.
  // probe.py calls this repeatedly until both stay still.
  check.tick = () => {
    for (const animation of document.getAnimations()) {
      const timing = animation.effect && animation.effect.getComputedTiming ? animation.effect.getComputedTiming() : {};
      if (timing.endTime === Infinity) { animation.pause(); animation.currentTime = 0; }
      else { try { animation.finish(); } catch (_) {} }
    }
    return [check.mutations, check.pending];
  };

  // Properties that depend on the element's own size go with the geometry, so that the remaining style maps
  // repeat across elements and can be stored once.
  const SIZED = new Set(['width', 'height', 'inline-size', 'block-size', 'transform-origin', 'perspective-origin',
                         'd', 'r', 'cx', 'cy', 'x', 'y', 'rx', 'ry']);
  const ATTRS = ['aria-label', 'aria-description', 'aria-pressed', 'aria-expanded', 'aria-selected', 'aria-checked',
                 'aria-current', 'aria-hidden', 'aria-disabled', 'role', 'title', 'placeholder', 'disabled', 'hidden',
                 'data-desktop-tip', 'data-memo-type', 'tabindex', 'type', 'value'];

  const ownText = (el) => [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.nodeValue).join('').replace(/\s+/g, ' ').trim();
  const signature = (el) => {
    const tag = el.tagName.toLowerCase();
    const id = el.id ? '#' + el.id : '';
    const cls = typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\s+/).sort().join('.') : '';
    const key = el.getAttribute('data-category') || el.getAttribute('data-route') || el.getAttribute('data-action') || '';
    const text = ownText(el).slice(0, 32);
    return tag + id + cls + (key ? '[' + key + ']' : '') + (text ? ' "' + text + '"' : '');
  };
  const path = (el) => { const out = []; for (let n = el.parentElement; n && out.length < 4; n = n.parentElement) out.push(n.tagName.toLowerCase() + (n.id ? '#' + n.id : '')); return out.join(' < '); };

  // Record the scene. Which element has the focus is recorded, then the focus is taken away before the
  // styles are read: whether the browser draws a focus ring (and a tooltip opens) for a focus the script set
  // differs from run to run, while where the focus is does not.
  check.capture = () => {
    const styles = [], styleIndex = new Map(), rows = [], seen = new Map();
    const active = document.activeElement;
    const focus = active && active !== document.body && active !== document.documentElement ? signature(active) : null;
    if (focus) active.blur();
    const intern = (map) => { const text = JSON.stringify(map); let i = styleIndex.get(text); if (i === undefined) { i = styles.length; styles.push(map); styleIndex.set(text, i); } return i; };
    const styleOf = (el, pseudo) => {
      const cs = getComputedStyle(el, pseudo), map = {}, sized = {};
      const names = []; for (let i = 0; i < cs.length; i++) names.push(cs[i]);
      names.sort();
      for (const name of names) { const value = cs.getPropertyValue(name); if (SIZED.has(name)) sized[name] = value; else map[name] = value; }
      return [intern(map), sized];
    };
    for (const el of [document.documentElement, ...document.documentElement.querySelectorAll('*')]) {
      const rects = el.getClientRects();
      if (!rects.length) continue;
      const sig = signature(el);
      const n = (seen.get(sig) || 0) + 1; seen.set(sig, n);
      const r = el.getBoundingClientRect();
      const row = { label: n > 1 ? sig + ' ~' + n : sig, path: path(el),
                    rect: [r.x, r.y, r.width, r.height].map((v) => Math.round(v * 2) / 2) };
      const text = ownText(el); if (text) row.text = text.slice(0, 200);
      const attrs = {}; for (const name of ATTRS) if (el.hasAttribute(name)) attrs[name] = el.getAttribute(name);
      if ((el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') && el.value) attrs['.value'] = String(el.value).slice(0, 200);
      if (Object.keys(attrs).length) row.attrs = attrs;
      [row.style, row.sized] = styleOf(el, null);
      for (const pseudo of ['::before', '::after']) {
        const content = getComputedStyle(el, pseudo).getPropertyValue('content');
        if (content && content !== 'none' && content !== 'normal') row[pseudo] = styleOf(el, pseudo);
      }
      rows.push(row);
    }
    return JSON.stringify({ viewport: [innerWidth, innerHeight], focus, styles, rows });
  };

  // Every style rule the browser parsed, with its enclosing at-rules, in document order.
  check.rules = () => {
    const out = [];
    const walk = (rules, context) => {
      for (const rule of rules) {
        const grouping = rule.cssRules && !(rule instanceof CSSStyleRule) && !(rule instanceof CSSKeyframesRule);
        if (grouping) walk(rule.cssRules, context + ' > ' + rule.cssText.slice(0, rule.cssText.indexOf('{')).trim());
        else out.push(context + ' || ' + rule.cssText);
        if (rule instanceof CSSStyleRule && rule.cssRules && rule.cssRules.length) walk(rule.cssRules, context + ' > ' + rule.selectorText);
      }
    };
    for (const sheet of document.styleSheets) walk(sheet.cssRules, (sheet.ownerNode && sheet.ownerNode.id) || '');
    return JSON.stringify(out);
  };

  // The shape of every page global another script or the desktop service can reach.
  check.globals = () => {
    const out = {};
    for (const name of Object.getOwnPropertyNames(window)) {
      if (!/^(__|Desktop)/.test(name) || name === '__check') continue;
      const value = window[name];
      out[name] = value && typeof value === 'object' ? Object.keys(value).sort() : typeof value;
    }
    return JSON.stringify(out);
  };

  // Click an element the way a person would, then let the page react.
  check.click = (selector) => {
    const el = [...document.querySelectorAll(selector)].find((node) => node.getClientRects().length);
    if (!el) return 'missing: ' + selector;
    el.scrollIntoView({ block: 'nearest' });
    el.click();
    return 'clicked';
  };
  // Type into a field: set its value as the browser does and send the events a key press sends.
  check.type = (selector, text) => {
    const el = [...document.querySelectorAll(selector)].find((node) => node.getClientRects().length);
    if (!el) return 'missing: ' + selector;
    el.focus();
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value').set;
    setter.call(el, text);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return 'typed';
  };

  // Press a key on the element that has the focus (or the page).
  check.key = (name) => {
    const target = document.activeElement || document.body;
    target.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true }));
    target.dispatchEvent(new KeyboardEvent('keyup', { key: name, bubbles: true, cancelable: true }));
    return 'pressed';
  };
  return 'ready';
})();
"""
