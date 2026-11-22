"use strict";
// The extension's words in three languages, all in _locales/<language>/messages.json (manifest.json uses the
// same keys). The browser's language picks one; the Aa button in the popup picks another, kept in this
// extension's localStorage for the popup. The extension's name in the browser's
// own lists follows the browser's language only.
(() => {
  const LANGUAGES = Object.freeze([["zh_CN", "简体中文"], ["en", "English"], ["ja", "日本語"]]);
  const STORAGE_KEY = "memorive.extension.language";
  const browserLanguage = () => {
    const tag = String(chrome.i18n?.getUILanguage?.() || navigator.language || "en").toLowerCase();
    return tag.startsWith("zh") ? "zh_CN" : tag.startsWith("ja") ? "ja" : "en";
  };
  const chosenLanguage = () => {
    try {
      const value = localStorage.getItem(STORAGE_KEY);
      return LANGUAGES.some(([code]) => code === value) ? value : null;
    } catch (_) {
      return null;
    }
  };
  let language = chosenLanguage() || browserLanguage(), table = null;
  // Until the chosen table is loaded, words come from the browser's language.
  const t = (key, ...values) => {
    const raw = table ? table[key]?.message : chrome.i18n.getMessage(key, values.map(String));
    if (!raw) return key;
    return table ? raw.replace(/\$(\d)/g, (_, n) => String(values[Number(n) - 1] ?? "")) : raw;
  };
  const apply = (root = document) => {
    root.querySelectorAll("[data-i18n]").forEach((node) => { node.textContent = t(node.dataset.i18n); });
    root.querySelectorAll("[data-i18n-title]").forEach((node) => { node.title = t(node.dataset.i18nTitle); });
    root.querySelectorAll("[data-i18n-aria-label]").forEach((node) => {
      node.setAttribute("aria-label", t(node.dataset.i18nAriaLabel));
    });
    document.documentElement.lang = language.replace("_", "-");
  };
  const load = async (code) => {
    const response = await fetch(chrome.runtime.getURL(`_locales/${code}/messages.json`));
    table = await response.json();
    language = code;
  };
  const ready = load(language).catch(() => { table = null; }).then(() => apply());
  const choose = async (code) => {
    if (!LANGUAGES.some(([known]) => known === code)) return;
    try { localStorage.setItem(STORAGE_KEY, code); } catch (_) { /* the choice lasts for this page only */ }
    await load(code);
    apply();
    document.dispatchEvent(new CustomEvent("memorive-language"));
  };
  globalThis.MEMORIVE_I18N = Object.freeze({ t, apply, ready, choose, languages: LANGUAGES, current: () => language });
})();
