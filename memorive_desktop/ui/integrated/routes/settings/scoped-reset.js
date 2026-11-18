/* Restoring the open settings category to its defaults (#settings-reset). The first click arms a ten-second
   countdown on the button, a second click cancels it, and at zero settings.reset_scope resets the category the
   page shows. Only the categories in RESETTABLE_SCOPES have the button; the ones with credentials or models
   (SENSITIVE_RESET_SCOPES) never do. settings.js calls arm() from its click handler and cancel() before a
   save or revert, and also offers both on Memorive.settingsBridge (armScopedReset, cancelScopedReset).
   Uses Memorive.settingsBridge for the service calls. Publishes Memorive.settingsReset. */
(() => {
  'use strict';
  const root = document.getElementById('desktop-settings-preview');
  if (!root) return;
  const q = (selector) => root.querySelector(selector);
  const { call, refresh, bootstrap, notify: showToast } = Memorive.settingsBridge;
  const SENSITIVE_RESET_SCOPES = new Set(['api', 'cli', 'local-models', 'workflow', 'refinement']);
  const RESETTABLE_SCOPES = new Set(['viewer', 'notice', 'behavior', 'network', 'privacy', 'appearance']);
  const RESET_COUNTDOWN_SECONDS = 10;

  let activeResetScope = 'none';
  let resetArmedScope = null;
  let resetArmedRevision = null;
  let resetDeadline = 0;
  let resetCountdownTimer = 0;

  const clearResetCountdownTimer = () => {
    if (resetCountdownTimer) window.clearInterval(resetCountdownTimer);
    resetCountdownTimer = 0;
  };

  const renderScopedResetIdle = () => {
    const button = q('#settings-reset');
    if (!button) return;
    button.textContent = '恢复当前项默认';
    button.removeAttribute('aria-pressed');
    button.removeAttribute('data-reset-armed');
    button.disabled = false;
  };

  const cancelScopedReset = ({ announce = false } = {}) => {
    const wasArmed = Boolean(resetArmedScope);
    clearResetCountdownTimer();
    resetArmedScope = null;
    resetArmedRevision = null;
    resetDeadline = 0;
    renderScopedResetIdle();
    if (announce && wasArmed) showToast('已取消恢复默认，当前设置未改变。');
    return wasArmed;
  };

  const updateScopedResetAvailability = (category) => {
    const next = String(category || 'none');
    if (next !== activeResetScope) cancelScopedReset();
    activeResetScope = next;
    const button = q('#settings-reset');
    if (!button) return;
    const isSensitive = SENSITIVE_RESET_SCOPES.has(next);
    button.hidden = isSensitive || !RESETTABLE_SCOPES.has(next);
    button.dataset.resetScope = button.hidden ? '' : next;
    if (!button.hidden && !resetArmedScope) renderScopedResetIdle();
  };

  const executeScopedReset = async (scope, expectedRevision) => {
    clearResetCountdownTimer();
    resetArmedScope = null;
    resetArmedRevision = null;
    resetDeadline = 0;
    if (scope !== activeResetScope || !RESETTABLE_SCOPES.has(scope)) {
      updateScopedResetAvailability(activeResetScope);
      return null;
    }
    const button = q('#settings-reset');
    if (button) {
      button.textContent = '正在恢复当前项…';
      button.disabled = true;
      button.removeAttribute('aria-pressed');
    }
    try {
      const receipt = await call('settings.reset_scope', {
        scope,
        expected_revision: expectedRevision
      });
      if(scope==='notice')await Memorive.reports?.reset();
      await refresh();
      showToast('当前项已恢复默认');
      return receipt;
    } finally {
      updateScopedResetAvailability(activeResetScope);
    }
  };

  const renderScopedResetCountdown = () => {
    const button = q('#settings-reset');
    if (!button || !resetArmedScope) return;
    const remaining = Math.max(0, Math.ceil((resetDeadline - Date.now()) / 1000));
    if (remaining <= 0) {
      const scope = resetArmedScope;
      const expectedRevision = resetArmedRevision;
      void executeScopedReset(scope, expectedRevision)
        .catch((error) => {
          updateScopedResetAvailability(activeResetScope);
          showToast(`恢复默认失败：${error.message}`);
        });
      return;
    }
    button.textContent = `再次点击取消 · ${remaining}s`;
    button.setAttribute('aria-pressed', 'true');
    button.dataset.resetArmed = 'true';
  };

  const armScopedReset = async () => {
    await bootstrap();
    const scope = activeResetScope;
    if (!RESETTABLE_SCOPES.has(scope) || SENSITIVE_RESET_SCOPES.has(scope)) return null;
    if (resetArmedScope === scope) {
      cancelScopedReset({ announce: true });
      return null;
    }
    cancelScopedReset();
    resetArmedScope = scope;
    resetArmedRevision = Memorive.settingsBridge.revision();
    resetDeadline = Date.now() + RESET_COUNTDOWN_SECONDS * 1000;
    renderScopedResetCountdown();
    resetCountdownTimer = window.setInterval(renderScopedResetCountdown, 250);
    return {
      status: 'ARMED',
      scope,
      countdown_seconds: RESET_COUNTDOWN_SECONDS,
      persistent_mutation: false
    };
  };

  window.addEventListener('desktop:settings-category-changed', (event) => {
    updateScopedResetAvailability(event.detail?.category || 'none');
  });

  Memorive.settingsReset = Object.freeze({ arm: armScopedReset, cancel: cancelScopedReset });
})();
