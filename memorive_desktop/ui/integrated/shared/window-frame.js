/* The window frame: the left sidebar, the right and bottom panels and their resizers, the keyboard shortcuts, the
   toolbar tooltips, the logo animation, and the generic switches and segmented buttons.

   The frame is in page.html once (#desktop-shell). Every route section (#desktop-<component>-preview) brings its
   central area, its right panel (#desktop-right-panel) and that panel's resizer. The sidebar and the bottom panel
   belong to the whole window. The right panel belongs to its route: the route's root keeps data-right and the
   width the panel was last given, and show() makes them the window's while the route is open. Only the size rule
   of the right panel differs by route (RIGHT_PANEL_RULES below).

   For each route this script publishes Memorive.frames.<component>:
     app, root            the route's root element; its data-right says whether the route's right panel is open
     show()               make this route the open one (shared/app.js calls it when it opens a route)
     setInspector(open, options), setInspectorSize(width), setSidebar(open), setBottom(open)
     announce(text)       say something in the window's live region
     rightButtonUnavailable(label)  this route has no right panel: the right panel button is off, with this label
     resetSizes()         forget the width the person gave this route's right panel and the bottom panel's height
   plus resetInspectorRatio() on the inbox. The right panel button and Ctrl+Alt+B first send a cancelable
   'desktop:right-button' event to the open route's root; a route that handles the button itself cancels it. */
(() => {
  'use strict';
  const i18n = Memorive.i18n;

  // Default panel sizes at 100% font size; they grow with the font size setting (app.js sets the factor).
  const BOTTOM_DEFAULT = 346;
  const fontScale = () => Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--desktop-font-factor')) || 1;
  const bottomDefault = () => Math.round(BOTTOM_DEFAULT * fontScale());
  const clampNumber = (value, min, max) => Math.min(max, Math.max(min, value));

  const stage = document.getElementById('desktop-preview-stage');
  const app = document.getElementById('desktop-window');
  const workspace = app.querySelector('.desktop-workspace');
  const navigation = app.querySelector('.desktop-navigation');
  const logoButton = document.getElementById('desktop-logo');
  const liveState = document.getElementById('desktop-live-state');
  const leftButton = document.getElementById('desktop-toggle-left');
  const rightButton = document.getElementById('desktop-toggle-right');
  const bottomButton = document.getElementById('desktop-toggle-bottom');
  const bottomPanel = document.getElementById('desktop-bottom-panel');
  const bottomResizer = document.getElementById('desktop-resize-bottom');
  const tooltip = document.getElementById('desktop-tooltip');
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  // Width of the content area: the workspace minus the sidebar when it is open.
  const contentWidth = () => {
    const workspaceWidth = workspace.getBoundingClientRect().width;
    const leftWidth = app.dataset.left === 'true' ? navigation.getBoundingClientRect().width : 0;
    return Math.max(0, workspaceWidth - leftWidth);
  };

  // How each route sizes its right panel. Every rule answers, for the route r (r.root, and r.size: the width the
  // panel was last given, or null):
  //   current(r)              the panel's width now (the keyboard steps from it; a drop keeps it)
  //   fit(r)                  the width to show when the panel opens or the window resizes (default: current)
  //   limits(r)               {min, max} for dragging and the keyboard
  //   resized(r, next, how)   after a resize: how is 'drag' (pointer moving), 'drop' (pointer released),
  //                           'key' (keyboard) or 'fit' (window resized, panel opened)
  //   opening(r, options)     before the panel opens; may return a width (options.targetRatio asks for a
  //                           share of the content)
  const given = (r, otherwise) => r.size ?? otherwise;
  const RIGHT_PANEL_RULES = {
    // 40% of the content until the person resizes it; resetInspectorRatio() goes back to 40%.
    shareUntilResized: () => {
      let resized = false;
      const share = () => contentWidth() * 0.4;
      return {
        current: (r) => given(r, share()),
        fit: (r) => (resized ? given(r, share()) : share()),
        limits: () => {
          const content = contentWidth();
          const min = Math.min(240, Math.max(160, content * 0.28));
          const max = Math.max(min, content - Math.min(240, Math.max(180, content * 0.28)));
          return { min, max };
        },
        resized: (r, next, how) => {
          if (how === 'drop' || how === 'key') resized = true;
        },
        opening: () => {},
        reset: (r) => {
          resized = false;
          return setRightSize(r, share());
        },
        forget: () => { resized = false; },
      };
    },
    // A share of the content (40% unless the caller asks for another), kept while the person resizes it.
    keptShare: () => {
      let ratio = 0.4;
      return {
        current: () => contentWidth() * ratio,
        limits: () => {
          const content = contentWidth();
          // The page keeps its own minimum, grown with the font size like the panel's.
          const minPrimary = Math.min(360 * fontScale(), Math.max(220 * fontScale(), content * 0.36));
          const max = Math.max(160, content - minPrimary);
          const requestedMin = (content <= 620 ? 200 : 280) * fontScale();
          return { min: Math.min(requestedMin, max), max };
        },
        resized: (r, next, how) => {
          if ((how === 'drag' || how === 'key') && contentWidth() > 0) ratio = next / contentWidth();
        },
        opening: (r, options) => {
          if (r.root.dataset.right !== 'true') ratio = Number(options.targetRatio) || 0.4;
        },
        forget: () => { ratio = 0.4; },
        watchSidebar: true,
      };
    },
    // 40% of the content each time it opens (or the share the caller asks for); a resize lasts until then.
    freshShare: () => ({
      current: (r) => given(r, contentWidth() * 0.4),
      limits: () => {
        const max = Math.max(48, contentWidth() - 240);
        return { min: Math.min(160, max), max };
      },
      resized: () => {},
      opening: (r, options) => (options.targetRatio ? contentWidth() * Number(options.targetRatio) : undefined),
      noRefitOnOpen: true,
    }),
    // A fixed width (smaller in a narrow window) until the person resizes it.
    fixed: (large, small, smallest) => () => ({
      current: (r) => given(r, (workspace.getBoundingClientRect().width <= 620 ? small : large) * fontScale()),
      limits: () => {
        const max = Math.max(48, Math.min(440 * fontScale(), contentWidth() - 112));
        return { min: Math.min(smallest * fontScale(), max), max };
      },
      resized: () => {},
      opening: () => {},
    }),
    // A width in pixels that stays as the person last set it; a narrower window clamps it for the moment only.
    remembered: () => {
      // null until the person resizes the panel: then the default for the current font size applies.
      let remembered = null;
      const right = () => remembered ?? Math.round(480 * fontScale());
      return {
        current: (r) => given(r, right()),
        fit: right,
        limits: () => {
          const workspaceWidth = workspace.getBoundingClientRect().width;
          const leftWidth = app.dataset.left === 'true' ? navigation.getBoundingClientRect().width : 0;
          const centralMinimum = workspaceWidth >= 1100 ? 620 : 360;
          const max = Math.max(48, Math.min(620, workspaceWidth - leftWidth - centralMinimum));
          return { min: Math.min(320, max), max };
        },
        resized: (r, next, how) => {
          if (how !== 'fit') remembered = next;
        },
        opening: () => {},
        forget: () => { remembered = null; },
      };
    },
  };

  // detail: on routes whose right panel shows the selected item, the selector of a selected item. Without one
  // the right panel button looks unavailable (aria-disabled) until something is selected or the panel is open;
  // the route's own handler of 'desktop:right-button' still says what to select.
  const ROUTES = [
    { component: 'settings', rule: RIGHT_PANEL_RULES.remembered, detail: '.desktop-settings-node[aria-current="true"]' },
    { component: 'inbox', rule: RIGHT_PANEL_RULES.shareUntilResized },
    { component: 'tasks', rule: RIGHT_PANEL_RULES.fixed(390, 280, 280), detail: '.desktop-tasks-task[aria-selected="true"]' },
    { component: 'messages', rule: RIGHT_PANEL_RULES.freshShare, detail: '.desktop-messages-card[aria-selected="true"]' },
    { component: 'sessions', rule: RIGHT_PANEL_RULES.fixed(260, 130, 160) },
    { component: 'library', rule: RIGHT_PANEL_RULES.keptShare },
    { component: 'worklog', rule: RIGHT_PANEL_RULES.keptShare },
    // The leaderboard shows its own tooltips (routes/leaderboard/view.js).
    { component: 'leaderboard', rule: RIGHT_PANEL_RULES.keptShare, ownTooltips: true },
    { component: 'chat', rule: RIGHT_PANEL_RULES.keptShare },
  ];

  let current = null;     // the open route
  let bottomWanted = null;  // the height the person gave the bottom panel; null: the default for the font size
  let tooltipTimer = 0;
  let tooltipTarget = null;
  let activeResize = null;
  let logoClickCount = 0;

  const bottomFit = () => bottomWanted ?? bottomDefault();
  const bottomNow = () => {
    const inline = Number.parseFloat(workspace.style.getPropertyValue('--desktop-bottom-size'));
    return Number.isFinite(inline) ? inline : bottomFit();
  };
  const fitRight = (r) => (r.rule.fit ? r.rule.fit(r) : r.rule.current(r));

  const getBottomLimits = () => {
    const workspaceHeight = workspace.getBoundingClientRect().height;
    const bottomGap = Number.parseFloat(getComputedStyle(workspace).getPropertyValue('--desktop-bottom-gap')) || 0;
    // The page above keeps at least 330px (times the font size): on chat, the header, toolbar and composer.
    const max = Math.max(64, Math.min(528 * fontScale(), workspaceHeight - bottomGap - 330 * fontScale()));
    return { min: Math.min(240 * fontScale(), max), max };
  };

  const updateResizerAria = (resizer, limits, value) => {
    const rounded = Math.round(value);
    resizer.setAttribute('aria-valuemin', String(Math.round(limits.min)));
    resizer.setAttribute('aria-valuemax', String(Math.round(limits.max)));
    resizer.setAttribute('aria-valuenow', String(rounded));
    i18n.set(resizer, 'frame.pixels', { size: rounded }, 'aria-valuetext');
  };

  // Set a route's right panel width within its limits. how: 'fit', 'drag', 'drop' or 'key' (see RIGHT_PANEL_RULES).
  const setRightSize = (r, value, announce = false, how = 'fit') => {
    const limits = r.rule.limits(r);
    const next = clampNumber(value, limits.min, limits.max);
    r.size = Math.round(next);
    if (r === current) workspace.style.setProperty('--desktop-right-size', `${r.size}px`);
    r.rule.resized(r, next, how);
    if (r.rightResizer) updateResizerAria(r.rightResizer, limits, next);
    if (announce) liveState.textContent = i18n.text('frame.panel.width', { size: r.size });
    return next;
  };

  const setBottomSize = (value, announce = false, how = 'fit') => {
    const limits = getBottomLimits();
    const next = clampNumber(value, limits.min, limits.max);
    workspace.style.setProperty('--desktop-bottom-size', `${Math.round(next)}px`);
    if (how !== 'fit') bottomWanted = next;
    updateResizerAria(bottomResizer, limits, next);
    if (announce) liveState.textContent = i18n.text('frame.panel.height', { size: Math.round(next) });
    return next;
  };

  // After the window or the sidebar changes size: fit the open panels again.
  const updateResizerBounds = () => {
    const bounds = workspace.getBoundingClientRect();
    if (bounds.width <= 0 || bounds.height <= 0) return;
    if (current?.rightResizer) {
      const size = fitRight(current);
      const limits = current.rule.limits(current);
      if (current.root.dataset.right === 'true') setRightSize(current, size);
      else updateResizerAria(current.rightResizer, limits, clampNumber(size, limits.min, limits.max));
    }
    if (app.dataset.bottom === 'true') setBottomSize(bottomFit());
    else {
      const limits = getBottomLimits();
      updateResizerAria(bottomResizer, limits, clampNumber(bottomFit(), limits.min, limits.max));
    }
  };

  const announcePanel = (panel, open) => {
    liveState.textContent = i18n.text(open ? 'frame.panel.opened' : 'frame.panel.closed', { panel: { key: `frame.panel.${panel}` } });
  };

  // The right panel button: unavailable on a route without a right panel, and on a route with a detail until
  // something is selected or the panel is open.
  const showRightButton = () => {
    if (!current) return;
    rightButton.setAttribute('aria-pressed', String(current.root.dataset.right === 'true'));
    rightButton.disabled = Boolean(current.unavailableLabel);
    if (current.unavailableLabel) {
      i18n.set(rightButton, null, null, 'aria-label');
      rightButton.setAttribute('aria-label', current.unavailableLabel);
    } else i18n.set(rightButton, 'frame.bar.right', null, 'aria-label');
    if (current.detail) {
      const available = current.root.dataset.right === 'true' || Boolean(current.root.querySelector(current.detail));
      rightButton.setAttribute('aria-disabled', String(!available));
    } else rightButton.removeAttribute('aria-disabled');
  };

  const setRight = (r, open, options = {}) => {
    if (open) {
      const asked = r.rule.opening(r, options);
      setRightSize(r, asked === undefined ? fitRight(r) : asked);
    }
    r.root.dataset.right = String(open);
    r.rightPanel?.setAttribute('aria-hidden', String(!open));
    if (r.rightResizer) r.rightResizer.hidden = !open;
    if (r !== current) return;
    app.dataset.right = String(open);
    showRightButton();
    announcePanel('right', open);
    if (!(r.rule.noRefitOnOpen && open)) window.requestAnimationFrame(updateResizerBounds);
  };

  const setLeft = (open) => {
    app.dataset.left = String(open);
    leftButton.setAttribute('aria-pressed', String(open));
    announcePanel('left', open);
    window.requestAnimationFrame(updateResizerBounds);
  };

  const setBottom = (open) => {
    if (open) setBottomSize(bottomFit());
    app.dataset.bottom = String(open);
    bottomButton.setAttribute('aria-pressed', String(open));
    bottomPanel.setAttribute('aria-hidden', String(!open));
    bottomResizer.hidden = !open;
    announcePanel('bottom', open);
    window.requestAnimationFrame(updateResizerBounds);
  };

  // Make route r the open one: its right panel state, width and button state become the window's.
  const show = (r) => {
    current = r;
    hideTooltip();
    app.dataset.route = r.component;
    app.dataset.right = r.root.dataset.right;
    if (r.size === null) workspace.style.removeProperty('--desktop-right-size');
    else workspace.style.setProperty('--desktop-right-size', `${r.size}px`);
    showRightButton();
    updateResizerBounds();
  };

  const toggleLeft = () => {
    const next = app.dataset.left !== 'true';
    setLeft(next);
    if (!next && navigation.contains(document.activeElement)) leftButton.focus();
  };

  // The right panel button and Ctrl+Alt+B: the open route may handle them itself (see the header).
  const toggleRight = () => {
    if (!current || rightButton.disabled) return;
    const asked = new CustomEvent('desktop:right-button', { cancelable: true });
    if (!current.root.dispatchEvent(asked) || !current.rightPanel) return;
    const next = current.root.dataset.right !== 'true';
    setRight(current, next);
    if (!next && (current.rightPanel.contains(document.activeElement) || document.activeElement === current.rightResizer)) rightButton.focus();
  };

  const toggleBottom = () => {
    const next = app.dataset.bottom !== 'true';
    setBottom(next);
    if (!next && (bottomPanel.contains(document.activeElement) || document.activeElement === bottomResizer)) bottomButton.focus();
  };

  // Dragging and the keyboard on the resizers; panel is 'right' (the open route's) or 'bottom'.
  const panelOpen = (panel) => (panel === 'right' ? current?.root.dataset.right === 'true' : app.dataset.bottom === 'true');
  const panelNow = (panel) => (panel === 'right' ? current.rule.current(current) : bottomNow());
  const resizeTo = (panel, value, announce, how) => (panel === 'right' ? setRightSize(current, value, announce, how) : setBottomSize(value, announce, how));

  const beginResize = (panel, event) => {
    if (event.button !== 0 || !panelOpen(panel)) return;
    event.preventDefault();
    hideTooltip();
    activeResize = { panel, pointerId: event.pointerId };
    app.dataset.resizing = panel;
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const moveResize = (panel, event) => {
    if (!activeResize || activeResize.panel !== panel || activeResize.pointerId !== event.pointerId) return;
    const rect = workspace.getBoundingClientRect();
    resizeTo(panel, panel === 'right' ? rect.right - event.clientX : rect.bottom - event.clientY, false, 'drag');
  };

  const endResize = (panel, event) => {
    if (!activeResize || activeResize.panel !== panel || activeResize.pointerId !== event.pointerId) return;
    const target = event.currentTarget;
    if (target.hasPointerCapture(event.pointerId)) target.releasePointerCapture(event.pointerId);
    activeResize = null;
    delete app.dataset.resizing;
    resizeTo(panel, panelNow(panel), true, 'drop');
  };

  const resizeWithKeyboard = (panel, event) => {
    if (!panelOpen(panel)) return;
    const limits = panel === 'right' ? current.rule.limits(current) : getBottomLimits();
    const now = panelNow(panel);
    let next = now;
    if (event.key === 'Home') next = limits.min;
    else if (event.key === 'End') next = limits.max;
    else if (panel === 'right' && event.key === 'ArrowLeft') next = now + 10;
    else if (panel === 'right' && event.key === 'ArrowRight') next = now - 10;
    else if (panel === 'bottom' && event.key === 'ArrowUp') next = now + 10;
    else if (panel === 'bottom' && event.key === 'ArrowDown') next = now - 10;
    else return;
    event.preventDefault();
    resizeTo(panel, next, true, 'key');
  };

  const bindResizer = (panel, resizer) => {
    resizer.addEventListener('pointerdown', (event) => beginResize(panel, event));
    resizer.addEventListener('pointermove', (event) => moveResize(panel, event));
    resizer.addEventListener('pointerup', (event) => endResize(panel, event));
    resizer.addEventListener('pointercancel', (event) => endResize(panel, event));
    resizer.addEventListener('keydown', (event) => resizeWithKeyboard(panel, event));
  };

  const clearLogoAnimation = () => {
    logoButton.classList.remove('desktop-logo-compress', 'desktop-logo-shake');
  };

  const finishLogoAnimation = (event) => {
    if (event.animationName === 'desktop-logo-compress') logoButton.classList.remove('desktop-logo-compress');
    if (event.animationName === 'desktop-logo-shake') logoButton.classList.remove('desktop-logo-shake');
  };

  const playLogoAnimation = () => {
    logoClickCount += 1;
    clearLogoAnimation();
    if (prefersReducedMotion.matches) return;
    void logoButton.offsetWidth;
    logoButton.classList.add(logoClickCount % 2 === 1 ? 'desktop-logo-compress' : 'desktop-logo-shake');
  };

  leftButton.addEventListener('click', toggleLeft);
  rightButton.addEventListener('click', toggleRight);
  bottomButton.addEventListener('click', toggleBottom);
  logoButton.addEventListener('click', playLogoAnimation);
  logoButton.addEventListener('animationend', finishLogoAnimation);
  logoButton.addEventListener('animationcancel', finishLogoAnimation);
  bindResizer('bottom', bottomResizer);

  stage.addEventListener('keydown', (event) => {
    if (!stage.contains(document.activeElement)) return;
    if (event.ctrlKey && event.altKey && event.code === 'KeyB') {
      event.preventDefault();
      toggleRight();
    } else if (event.ctrlKey && !event.altKey && event.code === 'KeyB') {
      event.preventDefault();
      toggleLeft();
    } else if (event.ctrlKey && !event.altKey && event.code === 'KeyJ') {
      event.preventDefault();
      toggleBottom();
    }
  });

  // Tooltips: a toolbar element with data-desktop-tip shows it after a short pause, inside the window. The
  // native title of such an element is removed, also when one is added later. A route with its own tooltips
  // (data-desktop-own-tooltips on its root) shows them itself.
  function hideTooltip() {
    tooltipTarget = null;
    window.clearTimeout(tooltipTimer);
    tooltip.hidden = true;
    tooltip.textContent = '';
  }

  const showTooltip = (target) => {
    tooltipTarget = target;
    window.clearTimeout(tooltipTimer);
    tooltipTimer = window.setTimeout(() => {
      tooltip.textContent = document.body.dataset.desktopTooltips === 'false'
        ? (target.getAttribute('aria-label') || target.dataset.desktopTip?.split('  ')[0] || '')
        : (target.dataset.desktopTip || target.getAttribute('aria-label') || '');
      tooltip.hidden = false;
      const appRect = app.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      const tipRect = tooltip.getBoundingClientRect();
      const placement = target.dataset.desktopPlacement || 'bottom';
      let left = targetRect.left - appRect.left + (targetRect.width - tipRect.width) / 2;
      let top = targetRect.bottom - appRect.top + 8;
      if (placement === 'right') {
        left = targetRect.right - appRect.left + 10;
        top = targetRect.top - appRect.top + (targetRect.height - tipRect.height) / 2;
      }
      left = Math.max(8, Math.min(left, appRect.width - tipRect.width - 8));
      top = Math.max(8, Math.min(top, appRect.height - tipRect.height - 8));
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${top}px`;
    }, 350);
  };

  const suppressNativeTooltip = (target) => {
    if (target instanceof Element && target.matches('[data-desktop-tip]')) target.removeAttribute('title');
  };

  const boundTooltipTargets = new WeakSet();
  const bindTooltip = (target) => {
    if (!(target instanceof Element) || !target.matches('[data-desktop-tip]')) return;
    suppressNativeTooltip(target);
    if (target.closest('[data-desktop-own-tooltips]') || boundTooltipTargets.has(target)) return;
    boundTooltipTargets.add(target);
    target.addEventListener('mouseenter', () => showTooltip(target));
    target.addEventListener('mouseleave', hideTooltip);
    target.addEventListener('focus', () => showTooltip(target));
    target.addEventListener('blur', hideTooltip);
  };

  // Switches: click (or Space and Enter on one that is not a checkbox) flips aria-checked; a switch without a
  // name takes the title of its row. The console-access switch is driven by the settings page itself.
  stage.querySelectorAll('.switch').forEach((control) => {
    if (control.id === 'settings-console-access') return;
    const switchName = control.closest('.switch-row')?.querySelector('.switch-copy strong')?.textContent.trim();
    if (switchName && !control.hasAttribute('aria-label')) control.setAttribute('aria-label', switchName);
    if (control.matches('input[type="checkbox"]')) return;   // the browser handles a checkbox
    control.addEventListener('keydown', (event) => {
      if (control.disabled || event.repeat || ![' ', 'Space', 'Spacebar', 'Enter'].includes(event.key)) return;
      event.preventDefault();
      control.click();
    });
    control.addEventListener('click', () => {
      if (control.disabled) return;
      const next = control.getAttribute('aria-checked') !== 'true';
      control.setAttribute('aria-checked', String(next));
    });
  });

  // Segmented buttons: the clicked one is pressed, the others are not.
  stage.querySelectorAll('[data-segmented]').forEach((group) => {
    group.addEventListener('click', (event) => {
      const button = event.target.closest('button');
      if (!button || !group.contains(button)) return;
      group.querySelectorAll('button').forEach((candidate) => candidate.setAttribute('aria-pressed', String(candidate === button)));
    });
  });

  const createRoute = ({ component, rule: makeRule, ownTooltips = false, detail = null }) => {
    const root = document.getElementById(`desktop-${component}-preview`);
    if (!root) return;
    const r = {
      component,
      root,
      rule: makeRule(),
      detail,
      rightPanel: root.querySelector('#desktop-right-panel'),
      rightResizer: root.querySelector('#desktop-resize-right'),
      size: null,
      unavailableLabel: '',
    };
    root.dataset.right = 'false';
    if (ownTooltips) root.dataset.desktopOwnTooltips = 'true';
    if (r.rightResizer) bindResizer('right', r.rightResizer);
    if (detail) {
      new MutationObserver(() => { if (r === current) showRightButton(); })
        .observe(root, { subtree: true, attributes: true, attributeFilter: ['aria-selected', 'aria-current', 'data-right'] });
    }
    const frame = {
      app: root,
      root,
      show: () => show(r),
      setInspector: (open, options = {}) => setRight(r, Boolean(open), options),
      setInspectorSize: (width) => setRightSize(r, width),
      setSidebar: (open) => setLeft(Boolean(open)),
      setBottom: (open) => setBottom(Boolean(open)),
      announce: (text) => { liveState.textContent = text; },
      rightButtonUnavailable: (label) => {
        r.unavailableLabel = label;
        if (r === current) showRightButton();
      },
      resetSizes: () => {
        r.size = null;
        r.rule.forget?.();
        bottomWanted = null;
        workspace.style.removeProperty('--desktop-bottom-size');
        if (r === current) workspace.style.removeProperty('--desktop-right-size');
        updateResizerBounds();
      },
    };
    if (r.rule.reset) frame.resetInspectorRatio = () => r.rule.reset(r);
    Memorive.frames[component] = frame;
    return r;
  };

  const routes = ROUTES.map(createRoute).filter(Boolean);
  Object.freeze(Memorive.frames);

  stage.querySelectorAll('[data-desktop-tip]').forEach(bindTooltip);
  new MutationObserver((records) => {
    records.forEach((record) => {
      bindTooltip(record.target);
      record.addedNodes.forEach((node) => {
        if (!(node instanceof Element)) return;
        bindTooltip(node);
        node.querySelectorAll('[data-desktop-tip]').forEach(bindTooltip);
      });
      if (record.attributeName === 'data-desktop-tip' && record.target === tooltipTarget && !tooltip.hidden) showTooltip(record.target);
    });
    if (tooltipTarget && (!stage.contains(tooltipTarget) || !tooltipTarget.getClientRects().length)) hideTooltip();
  }).observe(stage, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['title', 'data-desktop-tip', 'hidden', 'aria-hidden'],
  });

  const resizeObserver = new ResizeObserver(updateResizerBounds);
  resizeObserver.observe(stage);
  new ResizeObserver(() => { if (current?.rule.watchSidebar) updateResizerBounds(); }).observe(navigation);
  app.dataset.shortHeight = 'false';
  stage.style.maxWidth = '1440px';
  stage.style.aspectRatio = '1440 / 900';
  // The route shown already (one opened before this script ran), else the inbox.
  show(routes.find((r) => !r.root.closest('[hidden]')) || routes.find((r) => r.component === 'inbox'));
})();
