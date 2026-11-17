/* The research Q&A part of the settings page (#memo-settings-page): switching between its categories, and its model
   and agent pickers. Publishes window.__MEMO_SETTINGS_R2__ (show, renderAgentOptions, renderModelOptions,
   refreshModelPicker, inspect), through which routes/chat/workspace.js fills the pickers. What is chosen is saved
   by the research workspace's own actions. */
(() => {
  'use strict';
  const page = document.getElementById('memo-settings-page');
  if (!page || window.__MEMO_SETTINGS_R2__) return;
  const buttons = [...page.querySelectorAll('[data-memo-settings-category]')];
  const panels = [...page.querySelectorAll('[data-memo-settings-panel]')];
  const detail = document.getElementById('settings-primary');
  const modelSelect = page.querySelector('#memo-model');
  let current = 'models';
  let modelOptions = null;
  const modelPicker = window.__Desktop_MODEL_PICKER__.bind({
    button: page.querySelector('#memo-adjust-model'),
    menu: page.querySelector('#memo-model-menu'),
    label: page.querySelector('#memo-current-model'),
    dot: page.querySelector('#memo-model-dot'),
    onSelect(option) {
      modelSelect.value = option.profileRef || '';
      modelSelect.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });
  const agentSelect=page.querySelector('#memo-agent-model');
  const agentPicker=window.__Desktop_MODEL_PICKER__.bind({button:page.querySelector('#memo-agent-model-adjust'),menu:page.querySelector('#memo-agent-model-menu'),label:page.querySelector('#memo-agent-model-label'),dot:page.querySelector('#memo-agent-model-dot'),onSelect:option=>{
    agentSelect.value=option.profileRef||'';
    agentSelect.dispatchEvent(new Event('change',{bubbles:true}));
  }});
  function renderAgentOptions(options,currentRef){
    agentPicker.render([{profileRef:'',model:'使用常用 Agent 的可用模型',state:'green',note:''},...options.map(m=>({profileRef:m.profile_ref,model:m.display_name||m.model_name,state:m.eligible?'green':'yellow',note:m.profile_kind,disabled:!m.eligible}))],currentRef);
  }
  function refreshModelPicker() {
    const values = modelOptions || [...modelSelect.options].map(option => ({
      profileRef: option.value,
      model: option.label || option.textContent,
      state: option.disabled ? 'yellow' : 'green',
      note: '',
      disabled: option.disabled
    }));
    modelPicker.render(values, modelSelect.value);
  }
  function renderModelOptions(options, currentRef = modelSelect.value) {
    modelOptions = Array.isArray(options) ? options : null;
    if (modelOptions) modelPicker.render(modelOptions, currentRef);
    else refreshModelPicker();
  }
  modelSelect.addEventListener('change', refreshModelPicker);
  new MutationObserver(refreshModelPicker).observe(modelSelect, { childList: true, subtree: true, attributes: true, characterData: true });

  function show(name, { focus = false } = {}) {
    const panel = panels.find(item => item.dataset.memoSettingsPanel === name);
    if (!panel) return false;
    current = name;
    modelPicker.setOpen(false);
    agentPicker.setOpen(false);
    if (window.__MEMO_CONNECTIONS__?.inspect().opened) window.__MEMO_CONNECTIONS__.close();
    buttons.forEach(button => {
      if (button.dataset.memoSettingsCategory === name) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
      button.setAttribute('aria-pressed', String(button.dataset.memoSettingsCategory === name));
    });
    panels.forEach(item => { item.hidden = item !== panel; });
    if (detail) detail.scrollTop = 0;
    if (focus) panel.querySelector('h3')?.focus({ preventScroll: true });
    return true;
  }

  buttons.forEach((button, index) => {
    button.addEventListener('click', () => show(button.dataset.memoSettingsCategory));
    button.addEventListener('keydown', event => {
      let next = null;
      if (event.key === 'ArrowDown' || event.key === 'ArrowRight') next = (index + 1) % buttons.length;
      if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') next = (index - 1 + buttons.length) % buttons.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = buttons.length - 1;
      if (next === null) return;
      event.preventDefault();
      buttons[next].focus();
      show(buttons[next].dataset.memoSettingsCategory);
    });
  });
  const memoryList = page.querySelector('#memo-memory-list');
  const emptyMemory = page.querySelector('#memo-settings-memory-empty');
  const updateMemoryEmpty = () => { emptyMemory.hidden = memoryList.childElementCount > 0; };
  new MutationObserver(updateMemoryEmpty).observe(memoryList, { childList: true });
  updateMemoryEmpty();
  window.addEventListener('desktop:settings-category-changed', event => {
    if (event.detail.category !== 'research-workspace') {
      modelPicker.setOpen(false);
      agentPicker.setOpen(false);
      if (window.__MEMO_CONNECTIONS__?.inspect().opened) window.__MEMO_CONNECTIONS__.close();
    }
  });
  show(current);
  refreshModelPicker();
  window.__MEMO_SETTINGS_R2__ = {
    show,
    renderAgentOptions,
    renderModelOptions,
    refreshModelPicker,
    inspect: () => ({ category: current, visible_panels: panels.filter(panel => !panel.hidden).map(panel => panel.dataset.memoSettingsPanel) })
  };
})();

