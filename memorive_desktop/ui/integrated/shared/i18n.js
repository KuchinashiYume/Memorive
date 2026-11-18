/* Interface text by key. Each part of the page defines its messages once, in the three interface languages:
   define({ 'accounting.refresh': ['立即刷新', 'Refresh now', '今すぐ更新'] }) (order: zh-CN, en-US, ja-JP).
   Markup names a key in data-i18n (the element's text) or data-i18n-<attribute> (one of ATTRIBUTES below);
   scripts call set(element, key, values, attribute), which writes the same attributes, or text(key, values)
   for text that is shown once and not kept in the page. A message takes values by name ({count}); a value
   given as {key, values} is itself a message, so it follows the language too.
   shared/app.js calls render() when the language changes and for every element added to the page, so keyed
   text always shows in the current language. Text without a key is still translated from its Chinese wording
   by shared/app.js. Publishes window.__Desktop_I18N__; shared/frame/messages.js and the route scripts use it. */
(() => {
  'use strict';
  const LANGUAGES = Object.freeze(['zh-CN', 'en-US', 'ja-JP']);
  // data-i18n-<name> sets this attribute.
  const ATTRIBUTES = Object.freeze({
    'aria-label': 'aria-label',
    'aria-description': 'aria-description',
    'aria-valuetext': 'aria-valuetext',
    title: 'title',
    tip: 'data-desktop-tip',
    placeholder: 'placeholder',
    label: 'label',
  });
  const SELECTOR = ['[data-i18n]', ...Object.keys(ATTRIBUTES).map((name) => `[data-i18n-${name}]`)].join(',');
  const messages = new Map();

  const define = (table) => {
    Object.entries(table).forEach(([key, row]) => messages.set(key, row));
  };

  const language = () => LANGUAGES.includes(document.documentElement.lang) ? document.documentElement.lang : 'zh-CN';

  const text = (key, values = {}, lang = language()) => {
    const row = messages.get(key);
    if (!row) return key;
    const message = row[Math.max(0, LANGUAGES.indexOf(lang))];
    return message.replace(/\{(\w+)\}/g, (whole, name) => {
      if (!values || !(name in values)) return whole;
      const value = values[name];
      return value && typeof value === 'object' ? text(value.key, value.values, lang) : String(value);
    });
  };

  // The data attributes that hold the key and the values of the text (attribute null) or of one attribute.
  const slots = (attribute) => attribute ? [`data-i18n-${attribute}`, `data-i18n-${attribute}-values`] : ['data-i18n', 'data-i18n-values'];

  const show = (element, attribute, lang) => {
    const [keySlot, valuesSlot] = slots(attribute);
    const key = element.getAttribute(keySlot);
    if (!key) return;
    const stored = element.getAttribute(valuesSlot);
    const next = text(key, stored ? JSON.parse(stored) : {}, lang);
    if (!attribute) {
      // Change the text node in place, as the translation by Chinese wording does: scripts that watch for
      // added nodes then see a language change as no new content.
      const only = element.childNodes.length === 1 && element.firstChild.nodeType === Node.TEXT_NODE ? element.firstChild : null;
      if (only) {
        if (only.nodeValue !== next) only.nodeValue = next;
      } else if (element.textContent !== next) element.textContent = next;
      return;
    }
    const target = ATTRIBUTES[attribute];
    if (element.getAttribute(target) !== next) element.setAttribute(target, next);
  };

  const renderElement = (element, lang) => {
    show(element, null, lang);
    Object.keys(ATTRIBUTES).forEach((attribute) => show(element, attribute, lang));
  };

  const render = (root = document.body, lang = language()) => {
    if (!(root instanceof Element)) return;
    if (root.matches(SELECTOR)) renderElement(root, lang);
    root.querySelectorAll(SELECTOR).forEach((element) => renderElement(element, lang));
  };

  // Show a message in the element's text (attribute omitted) or in one of ATTRIBUTES, and keep it there in
  // the current language. key null takes the key away and leaves the text as it is, for text the script
  // writes itself from then on.
  const set = (element, key, values = null, attribute = null) => {
    const [keySlot, valuesSlot] = slots(attribute);
    if (!key) {
      element.removeAttribute(keySlot);
      element.removeAttribute(valuesSlot);
      return;
    }
    element.setAttribute(keySlot, key);
    if (values) element.setAttribute(valuesSlot, JSON.stringify(values));
    else element.removeAttribute(valuesSlot);
    show(element, attribute, language());
  };

  // Whether the element's text (attribute omitted) or this attribute ('aria-label', 'data-desktop-tip', ...)
  // comes from a key.
  const keyed = (element, attribute = null) => {
    if (!attribute) return element.hasAttribute('data-i18n');
    const name = Object.keys(ATTRIBUTES).find((candidate) => ATTRIBUTES[candidate] === attribute);
    return Boolean(name && element.hasAttribute(`data-i18n-${name}`));
  };

  window.__Desktop_I18N__ = Object.freeze({ LANGUAGES, define, text, set, render, keyed, language });
})();
