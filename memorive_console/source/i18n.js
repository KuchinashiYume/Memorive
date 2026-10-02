'use strict';
const consoleLocale = window.CONSOLE_I18N.locale;
const localeDirtyFields = new Set();
for (const eventName of ['input', 'change']) document.addEventListener(eventName, event => {
  if (event.target.matches('input[id],textarea[id],select[id]')) localeDirtyFields.add(event.target.id);
});
function tr(key, ...values) {
  const value = window.CONSOLE_I18N.messages[key] || key;
  return value.replace(/\$\{(\d+)\}/g, (_, index) => String(values[Number(index)] ?? ''));
}
function localizeInitialPage() {
  document.documentElement.lang = consoleLocale;
  document.title = tr(document.title);
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    if (node.parentElement.closest('script,style,pre,code,[data-no-translate]')) continue;
    const key = node.textContent.trim();
    if (key && window.CONSOLE_I18N.messages[key]) node.textContent = node.textContent.replace(key, tr(key));
  }
  for (const element of document.querySelectorAll('[title],[aria-label],[placeholder],input[value],textarea')) {
    for (const attr of ['title', 'aria-label', 'placeholder']) {
      const value = element.getAttribute(attr);
      if (value) element.setAttribute(attr, tr(value));
    }
    if (element.matches('input[value],textarea')) element.value = tr(element.value);
  }
}
localizeInitialPage();
const languageToggle = document.querySelector('#switchLanguage');
const languageOptions = document.querySelector('#languageOptions');
languageToggle.addEventListener('click', () => {
  languageOptions.hidden = !languageOptions.hidden;
  languageToggle.setAttribute('aria-expanded', String(!languageOptions.hidden));
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !languageOptions.hidden) {
    languageOptions.hidden = true; languageToggle.setAttribute('aria-expanded', 'false'); languageToggle.focus();
  }
});
for (const button of document.querySelectorAll('[data-console-language]')) {
  button.setAttribute('aria-pressed', String(button.dataset.consoleLanguage === consoleLocale));
  button.addEventListener('click', async () => {
    const next = button.dataset.consoleLanguage;
    if (next === consoleLocale) { languageOptions.hidden = true; languageToggle.setAttribute('aria-expanded', 'false'); return; }
    if (busy) { notice(tr('正在处理，请稍候')); return; }
    busy = true; availability();
    try {
      const fields = {};
      for (const input of document.querySelectorAll('input[id],textarea[id],select[id]')) {
        if (input.type !== 'file' && (localeDirtyFields.has(input.id) ||
            ['operation','suite-preset','build-path','expression-duration'].includes(input.id)))
          fields[input.id] = {value:input.value, checked:input.checked};
      }
      const view = document.querySelector('[data-view][aria-current="page"]')?.dataset.view || 'overview';
      sessionStorage.setItem('memorive-console-locale-draft', JSON.stringify({fields, view,
        selectedEventId, eventGroup, selectedExpressionId, expressionGroup, activeTab,
        selectedCommandId, eventLabCommandId, eventLabScenario, sessionId:snapshot?.session?.session_id}));
      await api('preferences', {language:next});
      location.reload();
    } catch (error) { busy = false; availability(); notice(error.message, true); }
  });
}
function restoreLanguageDraft() {
  let draft;
  try { draft = JSON.parse(sessionStorage.getItem('memorive-console-locale-draft') || 'null'); }
  catch (_) { return; }
  sessionStorage.removeItem('memorive-console-locale-draft');
  if (!draft) return;
  showView(draft.view);
  if (draft.sessionId !== snapshot?.session?.session_id) return;
  selectedEventId = draft.selectedEventId; eventGroup = draft.eventGroup;
  selectedExpressionId = draft.selectedExpressionId; expressionGroup = draft.expressionGroup;
  activeTab = draft.activeTab; selectedCommandId = draft.selectedCommandId;
  eventLabCommandId = draft.eventLabCommandId; eventLabScenario = draft.eventLabScenario;
  eventConfiguredId = null; renderEventLab();
  if (draft.fields.operation) { $('#operation').value = draft.fields.operation.value; renderFields(); }
  for (const [id, field] of Object.entries(draft.fields)) {
    const input = document.getElementById(id);
    if (!input || ['builds', 'allow-model'].includes(id) || input.readOnly) continue;
    input.value = field.value;
    localeDirtyFields.add(id);
    if (input.type === 'checkbox') input.checked = field.checked;
  }
  // Model permission is never restored by a language change.
  $('#allow-model').checked = false;
  document.querySelectorAll('[data-tab]').forEach(b => b.classList.toggle('selected', b.dataset.tab === activeTab));
  availability(); renderSuite(); rows();
}
