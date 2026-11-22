/* The faces of the main window come from the expression pack (表情包) in use: each position (idle, interaction,
   question, empty, loading, ...) has one or more looks, and a look is a face written as text, a picture, or frames
   shown in turn. Reads the pack and its pictures through the window methods of desktop/api/expression_packs.py.
   decorations.js draws the home, empty-page and loading faces with look() and paint(); the settings panel
   (routes/settings/expression-packs.js) switches the pack and calls reload(). Publishes Memorive.expressionPack. */
(() => {
  'use strict';
  if (Memorive.expressionPack) return;
  const api = () => window.pywebview?.api;
  let pack = null, loaded = false, resolveReady;
  const ready = new Promise(resolve => { resolveReady = resolve; });
  const listeners = new Set();
  const pictures = new Map();  // `${pack id}/${file}` -> promise of a data: URL (the page may not fetch)
  const painted = new WeakMap();  // element -> {timer, look} of what it shows
  const reducedMotion = () => Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);

  const picture = (id, file) => {
    const key = id + '/' + file;
    if (!pictures.has(key)) pictures.set(key, api().expression_pack_image({ id, file }).then(answer => {
      if (answer?.status !== 'OK') throw new Error(answer?.code || 'EXPRESSION_PICTURE_UNREADABLE');
      return `data:${answer.type};base64,${answer.data}`;
    }));
    return pictures.get(key);
  };

  // One look of a position, picked at random when the pack gives several; null without a pack.
  const look = position => {
    const looks = pack?.looks?.[position];
    return looks?.length ? looks[Math.floor(Math.random() * looks.length)] : null;
  };
  const isText = value => typeof value?.text === 'string';

  // Shows a look in element: text as the face itself (.desktop-kaomoji), a picture or frames in an img
  // (.desktop-expression-picture). Frames run on their own timer; a newer paint stops the older one. The pictures are
  // the pack in use's, or those of the pack id (the settings panel's previews).
  const paint = (element, value, id = pack?.id) => {
    const before = painted.get(element);
    clearTimeout(before?.timer);
    const entry = { timer: 0, look: value };
    painted.set(element, entry);
    if (!value) {
      element.replaceChildren();
      return;
    }
    if (isText(value)) {
      element.classList.add('desktop-kaomoji');
      element.classList.remove('desktop-expression-picture');
      if (element.firstElementChild || element.textContent !== value.text) element.textContent = value.text;
      return;
    }
    element.classList.remove('desktop-kaomoji');
    element.classList.add('desktop-expression-picture');
    let image = element.firstElementChild;
    if (!(image instanceof HTMLImageElement)) {
      image = document.createElement('img');
      image.alt = '';
      image.draggable = false;
      element.replaceChildren(image);
    }
    const files = value.image ? [value.image] : value.frames;
    const show = index => picture(id, files[index])
      .then(url => { if (painted.get(element) === entry) image.src = url; })
      .catch(() => {});
    if (files.length === 1 || reducedMotion()) {
      void show(0);
      return;
    }
    let index = 0;
    const next = () => {
      void show(index);
      const wait = value.ms[index];
      index += 1;
      if (index === files.length) {
        if (!value.loop) return;
        index = 0;
      }
      entry.timer = setTimeout(next, wait);
    };
    next();
  };
  const shown = element => painted.get(element)?.look || null;

  // The pack's text style (font and colour of text faces), as the variables .desktop-kaomoji reads.
  const applyStyle = () => {
    const style = pack?.text_style || {}, root = document.documentElement.style;
    if (style.font) root.setProperty('--expression-font', style.font); else root.removeProperty('--expression-font');
    if (style.color) root.setProperty('--expression-color', style.color); else root.removeProperty('--expression-color');
  };

  // Reads the pack in use again (at start, and after the settings panel changed it) and tells the listeners.
  const reload = async () => {
    let next = null;
    try {
      next = await api().expression_pack_active();
    } catch (_) {
      next = null;
    }
    pictures.clear();
    pack = next || null;
    loaded = true;
    applyStyle();
    resolveReady(pack);
    listeners.forEach(listener => listener(pack));
    return pack;
  };

  const start = () => {
    if (typeof api()?.expression_pack_active === 'function') void reload();
    else if (window.chrome?.webview) window.addEventListener('pywebviewready', () => void reload(), { once: true });
    else {
      // A plain browser (page checks without the desktop): no pack, the page shows no faces.
      loaded = true;
      resolveReady(null);
    }
  };

  Memorive.expressionPack = Object.freeze({
    ready,
    reload,
    look,
    isText,
    paint,
    picture,
    shown,
    active: () => pack,
    loaded: () => loaded,
    onChange: listener => listeners.add(listener)
  });
  start();
})();
