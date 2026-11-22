/* Connects the assistant window to the desktop service: asks for the assistant's state every 3 seconds, shows it on the
   pet and in the bubble, and runs the menu's shortcuts. Publishes window.__Desktop_ASSISTANT_RUNTIME__ (refresh,
   setAutoRefreshEnabled, applyPresentation, closeMenu, fitMenuToViewport, inspect), which
   memorive/desktop/assistant/desktop.py and product/desktop/console_expressions.py call through evaluate_js. */
(() => {
  'use strict';
  const root = document.querySelector('#memorive-desktop-assistant-preview');
  const pet = document.querySelector('#memorive-pet-window');
  const anchor = document.querySelector('#memorive-pet-anchor');
  const bubble = document.querySelector('#memorive-pet-bubble');
  const bubbleTitle = document.querySelector('#memorive-pet-bubble-title');
  const bubbleCopy = document.querySelector('#memorive-pet-bubble-copy');
  const bubbleOpen = document.querySelector('#memorive-pet-bubble-open');
  const statusDot = document.querySelector('#memorive-pet-status-dot');
  const menu = document.querySelector('#memorive-pet-menu');
  const menuButtons = [...document.querySelectorAll('[data-memorive-menu]')];
  if (!root || !pet || !anchor || !bubble || !bubbleTitle || !bubbleCopy || !statusDot || !menu) {
    throw new Error('ASSISTANT_RUNTIME_DOM_MISSING');
  }
  [document.documentElement, document.body, root, root.querySelector('.memorive-desktop')]
    .filter(Boolean)
    .forEach((node) => {
      node.style.setProperty('background', 'transparent', 'important');
      node.style.setProperty('background-color', 'transparent', 'important');
    });
  pet.title = '左键打开 Memorive，右键更多';

  let lastProjection = null;
  let drag = null;
  let movePending = false;
  let primaryActivationCount = 0;
  let lastPrimaryActivationReceipt = null;
  let menuFitCount = 0;
  let menuOutsideDismissCount = 0;
  let menuBlurDismissCount = 0;
  let lastMenuCloseReason = null;
  let refreshTimer = null;

  const api = (name, payload) => {
    if (!window.pywebview || !window.pywebview.api || typeof window.pywebview.api[name] !== 'function') {
      return Promise.reject(new Error('ASSISTANT_NATIVE_BRIDGE_UNAVAILABLE'));
    }
    return Promise.resolve(window.pywebview.api[name](payload));
  };

  const toneFor = (attention) => ({
    INFO: 'blue',
    SUCCESS: 'green',
    WARNING: 'yellow',
    ERROR: 'red',
    NONE: 'none',
  })[attention] || 'none';

  const frameFor = (animation) => ({
    STATIC: 'idle',
    WAKE: 'interaction',
    WORKING: 'working',
    MESSAGE: 'interaction',
    BLINK: 'idle',
    ATTENTION: 'interaction',
    SUCCESS: 'success',
    ERROR: 'interaction',
    DRAG: 'interaction',
    SLEEP: 'blink-closed',
  })[animation] || 'idle';

  let visibleDeliveryId = null;
  const applyProjection = (projection) => {
    if (!projection || projection.schema_version !== 'DesktopAssistantAssistantRuntimeProjection-v1') {
      throw new Error('ASSISTANT_RUNTIME_PROJECTION_INVALID');
    }
    lastProjection = projection;
    const presentation = projection.presentation;
    // The native renderer acknowledges after its actual completion. When it
    // is absent, retain and animate this delivery in the existing HTML pet.
    if (presentation.delivery_id && projection.native_presentation_status !== 'APPLIED'
        && visibleDeliveryId !== presentation.delivery_id
        && !['HIDDEN','DISABLED'].includes(presentation.display_state)) {
      visibleDeliveryId = presentation.delivery_id;
      const deliveryId = visibleDeliveryId;
      const motion = presentation.animation_state === 'ERROR' ? 'memorive-is-shaking-error' : 'memorive-is-nodding';
      pet.classList.remove('memorive-is-shaking-error', 'memorive-is-nodding');
      void pet.offsetWidth;
      const finish = () => {
        pet.classList.remove(motion);
        api('assistant_ack_delivery', {delivery_id:deliveryId}).catch(() => { visibleDeliveryId = null; });
      };
      if (presentation.reduced_motion) requestAnimationFrame(finish);
      else {
        pet.classList.add(motion);
        pet.addEventListener('animationend', finish, {once:true});
      }
    }
    root.dataset.reducedMotion = String(Boolean(presentation.reduced_motion));
    pet.dataset.frame = frameFor(presentation.animation_state);
    pet.setAttribute('aria-label', 'Memorive 桌面助手，' + presentation.safe_summary);
    statusDot.dataset.tone = toneFor(presentation.attention_state);
    statusDot.dataset.spinner = String(
      ['JOB_RUNNING', 'MESSAGE_UPDATE'].includes(presentation.reason_code) && !presentation.reduced_motion
    );
    bubbleTitle.textContent = presentation.safe_summary;
    bubbleCopy.textContent = presentation.display_state === 'NOTIFYING'
      ? '点击打开 Memorive 主程序'
      : presentation.reason_code === 'NO_ACTIVE_REAL_JOB'
        ? '待命中'
        : presentation.reason_code;
    bubble.hidden = presentation.display_state !== 'NOTIFYING';
    anchor.hidden = presentation.display_state === 'HIDDEN' || presentation.display_state === 'DISABLED';
    document.body.dataset.assistantReady = 'true';
  };

  const applyPresentation = (presentation) => applyProjection({
    ...(lastProjection || {}),
    schema_version: 'DesktopAssistantAssistantRuntimeProjection-v1',
    presentation,
  });

  const refresh = () => api('assistant_bootstrap', {}).then(applyProjection).catch(() => {
    statusDot.dataset.tone = 'red';
    statusDot.dataset.spinner = 'false';
    bubbleTitle.textContent = '桌面助手已暂停';
    bubbleCopy.textContent = '本地服务不可用';
    bubble.hidden = false;
  });

  const setAutoRefreshEnabled = (enabled) => {
    if (refreshTimer !== null) {
      window.clearInterval(refreshTimer);
      refreshTimer = null;
    }
    if (enabled) refreshTimer = window.setInterval(refresh, 3000);
    return refreshTimer !== null;
  };

  const runShortcut = (actionId, locator = null) => api('assistant_shortcut', {
    action_id: actionId,
    locator,
  }).then((receipt) => {
    if (actionId === 'OPEN_Desktop_MAIN' && receipt?.main_window_focus?.status === 'PASS') {
      primaryActivationCount += 1;
      lastPrimaryActivationReceipt = receipt;
    }
    if (receipt && receipt.presentation) applyProjection(receipt.presentation);
    return receipt;
  });

  const closeMenu = (reason = 'runtime') => {
    const wasOpen = !menu.hidden;
    menu.hidden = true;
    menu.style.removeProperty('transform');
    pet.setAttribute('aria-expanded', 'false');
    if (wasOpen) {
      lastMenuCloseReason = reason;
      if (reason === 'outside-pointer') menuOutsideDismissCount += 1;
      if (reason === 'window-blur' || reason === 'document-hidden') menuBlurDismissCount += 1;
    }
    return wasOpen;
  };

  const fitMenuToViewport = () => {
    if (menu.hidden) return false;
    menu.style.removeProperty('transform');
    const margin = 8;
    const rect = menu.getBoundingClientRect();
    let translateX = 0;
    let translateY = 0;
    if (rect.left < margin) translateX += margin - rect.left;
    if (rect.right > window.innerWidth - margin) translateX -= rect.right - (window.innerWidth - margin);
    if (rect.top < margin) translateY += margin - rect.top;
    if (rect.bottom > window.innerHeight - margin) translateY -= rect.bottom - (window.innerHeight - margin);
    if (translateX || translateY) {
      menu.style.transform = `translate(${Math.round(translateX)}px, ${Math.round(translateY)}px)`;
    }
    menuFitCount += 1;
    return true;
  };

  const scheduleMenuFit = () => {
    window.requestAnimationFrame(() => window.requestAnimationFrame(fitMenuToViewport));
  };

  pet.addEventListener('contextmenu', scheduleMenuFit, true);
  new MutationObserver(() => {
    if (!menu.hidden) scheduleMenuFit();
  }).observe(menu, { attributes: true, attributeFilter: ['hidden'] });
  window.addEventListener('resize', scheduleMenuFit);
  document.addEventListener('pointerdown', (event) => {
    if (menu.hidden || menu.contains(event.target)) return;
    closeMenu('outside-pointer');
  }, true);
  window.addEventListener('blur', () => closeMenu('window-blur'));
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) closeMenu('document-hidden');
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !menu.hidden) closeMenu('escape');
  }, true);

  pet.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    drag = {
      pointerId: event.pointerId,
      startX: event.screenX,
      startY: event.screenY,
      windowX: Math.round(window.screenX),
      windowY: Math.round(window.screenY),
      moved: false,
      targetX: Math.round(window.screenX),
      targetY: Math.round(window.screenY),
    };
    if (event.isTrusted) pet.setPointerCapture(event.pointerId);
  }, true);

  pet.addEventListener('pointermove', (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const dx = event.screenX - drag.startX;
    const dy = event.screenY - drag.startY;
    if (!drag.moved && Math.hypot(dx, dy) < 6) return;
    drag.moved = true;
    drag.targetX = Math.round(drag.windowX + dx);
    drag.targetY = Math.round(drag.windowY + dy);
    if (movePending) return;
    movePending = true;
    api('assistant_move_window', { x: drag.targetX, y: drag.targetY, persist: false })
      .finally(() => { movePending = false; });
  }, true);

  const finishPointer = (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (event.isTrusted && pet.hasPointerCapture(event.pointerId)) pet.releasePointerCapture(event.pointerId);
    const completed = drag;
    drag = null;
    if (completed.moved) {
      api('assistant_move_window', { x: completed.targetX, y: completed.targetY, persist: true })
        .then(refresh)
        .catch(refresh);
    } else {
      runShortcut('OPEN_Desktop_MAIN').catch(refresh);
    }
  };
  pet.addEventListener('pointerup', finishPointer, true);
  pet.addEventListener('pointercancel', finishPointer, true);
  pet.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);

  bubbleOpen?.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
    const locator = lastProjection?.presentation?.locator || null;
    const action = locator ? 'OPEN_MESSAGE_DETAIL' : 'OPEN_Desktop_MAIN';
    runShortcut(action, locator).catch(refresh);
  }, true);

  menuButtons.forEach((button) => {
    const action = button.dataset.prosMenu;
    if (['codex', 'claude-code', 'cc-switch'].includes(action)) {
      button.disabled = true;
      button.title = '外部应用未启用';
      return;
    }
    button.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      closeMenu('menu-action');
      if (action === 'open-desktop') runShortcut('OPEN_Desktop_MAIN').catch(refresh);
      else if (action === 'settings') runShortcut('OPEN_DESKTOP_ASSISTANT_SETTINGS').catch(refresh);
      else if (action === 'hide') runShortcut('HIDE_DESKTOP_ASSISTANT').catch(refresh);
    }, true);
  });

  window.__Desktop_ASSISTANT_RUNTIME__ = Object.freeze({
    refresh,
    setAutoRefreshEnabled,
    applyPresentation,
    closeMenu,
    fitMenuToViewport,
    inspect: () => {
      const surface = root.querySelector('.memorive-desktop');
      const transparencyNodes = [document.documentElement, document.body, root, surface].filter(Boolean);
      const surfaceBackgrounds = transparencyNodes.map((node) => getComputedStyle(node).backgroundColor);
      const petRect = pet.getBoundingClientRect();
      const menuRect = menu.getBoundingClientRect();
      const visibleMenuButtons = menuButtons.filter((button) => getComputedStyle(button).display !== 'none');
      const menuWithinViewport = menu.hidden || (
        menuRect.left >= 7
        && menuRect.top >= 7
        && menuRect.right <= window.innerWidth - 7
        && menuRect.bottom <= window.innerHeight - 7
      );
      return {
        ready: document.body.dataset.assistantReady === 'true',
        auto_refresh_enabled: refreshTimer !== null,
        visible: !anchor.hidden,
        frame: pet.dataset.frame,
        tone: statusDot.dataset.tone,
        bubble_visible: !bubble.hidden,
        visible_debug_chrome_count: [...root.querySelectorAll('.memorive-desktop-state,.memorive-taskbar,.memorive-pet-event,.memorive-desktop-shortcut,.memorive-desktop-document')]
          .filter((node) => getComputedStyle(node).display !== 'none').length,
        pet_only_default: !anchor.hidden && bubble.hidden && statusDot.dataset.tone === 'none',
        surface_backgrounds: surfaceBackgrounds,
        transparent_surface_exact: surfaceBackgrounds.length === 4
          && surfaceBackgrounds.every((value) => value === 'rgba(0, 0, 0, 0)'),
        pet_sprite_opacity: Number.parseFloat(
          getComputedStyle(root.querySelector('.memorive-pet-sprite') || pet).opacity
        ),
        pet_hit_point_css: {
          x: petRect.left + petRect.width / 2,
          y: petRect.top + petRect.height / 2,
        },
        device_pixel_ratio: Number(window.devicePixelRatio || 1),
        viewport_size_css: {
          width: Number(window.innerWidth || 0),
          height: Number(window.innerHeight || 0),
        },
        primary_activation_count: primaryActivationCount,
        last_primary_activation_receipt: lastPrimaryActivationReceipt,
        menu: {
          open: !menu.hidden,
          rect: {
            left: menuRect.left,
            top: menuRect.top,
            right: menuRect.right,
            bottom: menuRect.bottom,
            width: menuRect.width,
            height: menuRect.height,
          },
          fully_within_viewport: menuWithinViewport,
          content_fully_visible: menu.hidden || (
            menu.scrollWidth <= menu.clientWidth + 1
            && menu.scrollHeight <= menu.clientHeight + 1
          ),
          visible_button_count: visibleMenuButtons.length,
          visible_actions: visibleMenuButtons.map((button) => button.dataset.prosMenu),
          fit_count: menuFitCount,
          outside_dismiss_count: menuOutsideDismissCount,
          blur_dismiss_count: menuBlurDismissCount,
          last_close_reason: lastMenuCloseReason,
        },
        exit_control_visible: false,
        sample_job_consumed: Boolean(lastProjection?.sample_job_consumed),
        presentation: lastProjection?.presentation || null,
      };
    },
  });

  const start = () => {
    refresh();
    setAutoRefreshEnabled(true);
  };
  if (window.pywebview?.api) start();
  else window.addEventListener('pywebviewready', start, { once: true });
})();