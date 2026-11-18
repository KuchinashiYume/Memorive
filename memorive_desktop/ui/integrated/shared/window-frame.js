/* The window frame of every route: the left sidebar, the right and bottom panels and their resizers, the
   keyboard shortcuts, the toolbar tooltips, the logo animation, and the generic switches and segmented buttons.

   Each route section of the page carries its own copy of the frame markup (#desktop-<component>-preview).
   This script gives every copy the same behaviour; only the size rule of the right panel differs by route
   (RIGHT_PANEL_RULES below). For each route it publishes Memorive.frames.<component>:
   { app, root, setInspector(open, options), setSidebar(open), setBottom(open) }, plus resetInspectorRatio()
   on the inbox. shared/app.js and the route scripts use these objects. */
(() => {
  'use strict';
  const i18n = Memorive.i18n;

  // Default panel sizes at 100% font size; they grow with the font size setting (app.js sets the factor).
  const BOTTOM_DEFAULT = 346;
  const fontScale = () => Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--desktop-font-factor')) || 1;
  const bottomDefault = () => Math.round(BOTTOM_DEFAULT * fontScale());

  // Width of the content area: the workspace minus the sidebar when it is open.
  const contentWidth = (frame) => {
    const workspaceWidth = frame.workspace.getBoundingClientRect().width;
    const leftWidth = frame.app.dataset.left === 'true' ? frame.navigation.getBoundingClientRect().width : 0;
    return Math.max(0, workspaceWidth - leftWidth);
  };

  // The width an inline --desktop-right-size holds, or NaN when none is set.
  const inlineSize = (frame, property) => Number.parseFloat(frame.workspace.style.getPropertyValue(property));

  // The current inline size of a panel when one is set, else `otherwise`.
  const inlineOr = (frame, property, otherwise) => {
    const inline = inlineSize(frame, property);
    return Number.isFinite(inline) ? inline : otherwise;
  };

  // How each route sizes its right panel. Every rule answers:
  //   current(frame)              the panel's width now (the keyboard steps from it; a drop keeps it)
  //   fit(frame)                  the width to show when the panel opens or the window resizes (default: current)
  //   limits(frame)               {min, max} for dragging and the keyboard
  //   resized(frame, next, how)   after a resize: how is 'drag' (pointer moving), 'drop' (pointer released),
  //                               'key' (keyboard) or 'fit' (window resized, panel opened)
  //   opening(frame, options)     before the panel opens; may return a width (options.targetRatio asks for a
  //                               share of the content)
  const RIGHT_PANEL_RULES = {
    // 40% of the content until the person resizes it; resetInspectorRatio() goes back to 40%.
    shareUntilResized: () => {
      let resized = false;
      const share = (frame) => contentWidth(frame) * 0.4;
      return {
        current: (frame) => inlineOr(frame, '--desktop-right-size', share(frame)),
        fit: (frame) => (resized ? inlineOr(frame, '--desktop-right-size', share(frame)) : share(frame)),
        limits: (frame) => {
          const content = contentWidth(frame);
          const min = Math.min(240, Math.max(160, content * 0.28));
          const max = Math.max(min, content - Math.min(240, Math.max(180, content * 0.28)));
          return { min, max };
        },
        resized: (frame, next, how) => {
          if (how === 'drop' || how === 'key') resized = true;
        },
        opening: () => {},
        reset: (frame) => {
          resized = false;
          return frame.setPanelSize('right', share(frame));
        },
      };
    },
    // A share of the content (40% unless the caller asks for another), kept while the person resizes it.
    keptShare: () => {
      let ratio = 0.4;
      return {
        current: (frame) => contentWidth(frame) * ratio,
        limits: (frame) => {
          const content = contentWidth(frame);
          const minPrimary = Math.min(360, Math.max(220, content * 0.36));
          const max = Math.max(160, content - minPrimary);
          const requestedMin = (content <= 620 ? 200 : 280) * fontScale();
          return { min: Math.min(requestedMin, max), max };
        },
        resized: (frame, next, how) => {
          if ((how === 'drag' || how === 'key') && contentWidth(frame) > 0) ratio = next / contentWidth(frame);
        },
        opening: (frame, options) => {
          if (frame.app.dataset.right !== 'true') ratio = Number(options.targetRatio) || 0.4;
        },
        watchSidebar: true,
      };
    },
    // 40% of the content each time it opens (or the share the caller asks for); a resize lasts until then.
    freshShare: () => ({
      current: (frame) => inlineOr(frame, '--desktop-right-size', contentWidth(frame) * 0.4),
      limits: (frame) => {
        const max = Math.max(48, contentWidth(frame) - 240);
        return { min: Math.min(160, max), max };
      },
      resized: () => {},
      opening: (frame, options) => (options.targetRatio ? contentWidth(frame) * Number(options.targetRatio) : undefined),
      noRefitOnOpen: true,
    }),
    // A fixed width (smaller in a narrow window) until the person resizes it.
    fixed: (large, small, smallest) => () => ({
      current: (frame) => inlineOr(frame, '--desktop-right-size', (frame.workspace.getBoundingClientRect().width <= 620 ? small : large) * fontScale()),
      limits: (frame) => {
        const max = Math.max(48, Math.min(440 * fontScale(), contentWidth(frame) - 112));
        return { min: Math.min(smallest * fontScale(), max), max };
      },
      resized: () => {},
      opening: () => {},
    }),
    // A width in pixels that stays as the person last set it, for the right and the bottom panel; a narrower
    // window clamps it for the moment only.
    remembered: () => {
      // null until the person resizes the panel: then the default for the current font size applies.
      const remembered = { right: null, bottom: null };
      const right = () => remembered.right ?? Math.round(480 * fontScale());
      const bottom = () => remembered.bottom ?? bottomDefault();
      return {
        current: (frame) => inlineOr(frame, '--desktop-right-size', right()),
        fit: right,
        bottomCurrent: (frame) => inlineOr(frame, '--desktop-bottom-size', bottom()),
        bottomFit: bottom,
        limits: (frame) => {
          const workspaceWidth = frame.workspace.getBoundingClientRect().width;
          const leftWidth = frame.app.dataset.left === 'true' ? frame.navigation.getBoundingClientRect().width : 0;
          const centralMinimum = workspaceWidth >= 1100 ? 620 : 360;
          const max = Math.max(48, Math.min(620, workspaceWidth - leftWidth - centralMinimum));
          return { min: Math.min(320, max), max };
        },
        resized: (frame, next, how, panel = 'right') => {
          if (how !== 'fit') remembered[panel] = next;
        },
        opening: () => {},
        remembersBottom: true,
      };
    },
  };

  // detail: on routes whose right panel shows the selected item, the selector of a selected item. Without one
  // the right panel button looks unavailable (aria-disabled) until something is selected or the panel is open;
  // the route's own click handler still says what to select.
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

  const createFrame = ({ component, rule: makeRule, ownTooltips = false, detail = null }) => {
    const root = document.getElementById(`desktop-${component}-preview`);
    if (!root) return;
    const rule = makeRule();
    const stage = root.querySelector('#desktop-preview-stage');
    const app = root.querySelector('#desktop-window');
    const workspace = root.querySelector('.desktop-workspace');
    const navigation = root.querySelector('.desktop-navigation');
    const logoButton = root.querySelector('#desktop-logo');
    const liveState = root.querySelector('#desktop-live-state');
    const leftButton = root.querySelector('#desktop-toggle-left');
    const rightButton = root.querySelector('#desktop-toggle-right');
    const bottomButton = root.querySelector('#desktop-toggle-bottom');
    const rightPanel = root.querySelector('#desktop-right-panel');
    const bottomPanel = root.querySelector('#desktop-bottom-panel');
    const rightResizer = root.querySelector('#desktop-resize-right');
    const bottomResizer = root.querySelector('#desktop-resize-bottom');
    const tooltip = root.querySelector('#desktop-tooltip');
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const frame = { app, workspace, navigation };
    let tooltipTimer = 0;
    let tooltipTarget = null;
    let activeResize = null;
    let logoClickCount = 0;

    const clampNumber = (value, min, max) => Math.min(max, Math.max(min, value));

    const getPanelSize = (panel) => {
      if (panel === 'right') return rule.current(frame);
      return rule.bottomCurrent ? rule.bottomCurrent(frame) : inlineOr(frame, '--desktop-bottom-size', bottomDefault());
    };

    // The size a panel takes when it opens or the window resizes.
    const fitSize = (panel) => {
      if (panel === 'right') return rule.fit ? rule.fit(frame) : rule.current(frame);
      return rule.bottomFit ? rule.bottomFit() : getPanelSize('bottom');
    };

    const getRightLimits = () => rule.limits(frame);

    const getBottomLimits = () => {
      const workspaceHeight = workspace.getBoundingClientRect().height;
      const bottomGap = Number.parseFloat(getComputedStyle(workspace).getPropertyValue('--desktop-bottom-gap')) || 0;
      // The page above keeps at least 300px (times the font size) for its header and, on chat, the composer.
      const max = Math.max(64, Math.min(528 * fontScale(), workspaceHeight - bottomGap - 300 * fontScale()));
      return { min: Math.min(240 * fontScale(), max), max };
    };

    const updateResizerAria = (resizer, limits, value) => {
      const rounded = Math.round(value);
      resizer.setAttribute('aria-valuemin', String(Math.round(limits.min)));
      resizer.setAttribute('aria-valuemax', String(Math.round(limits.max)));
      resizer.setAttribute('aria-valuenow', String(rounded));
      i18n.set(resizer, 'frame.pixels', { size: rounded }, 'aria-valuetext');
    };

    // Set a panel's size within its limits. how: 'fit', 'drag', 'drop' or 'key' (see RIGHT_PANEL_RULES).
    const setPanelSize = (panel, value, announce = false, how = 'fit') => {
      const limits = panel === 'right' ? getRightLimits() : getBottomLimits();
      const next = clampNumber(value, limits.min, limits.max);
      const property = panel === 'right' ? '--desktop-right-size' : '--desktop-bottom-size';
      const resizer = panel === 'right' ? rightResizer : bottomResizer;
      workspace.style.setProperty(property, `${Math.round(next)}px`);
      if (panel === 'right') rule.resized(frame, next, how);
      else if (rule.remembersBottom) rule.resized(frame, next, how, 'bottom');
      updateResizerAria(resizer, limits, next);
      if (announce) {
        liveState.textContent = i18n.text(panel === 'right' ? 'frame.panel.width' : 'frame.panel.height', { size: Math.round(next) });
      }
      return next;
    };
    frame.setPanelSize = setPanelSize;

    const updateResizerBounds = () => {
      // Hidden routes have no layout; keep the last visible panel sizes.
      const bounds = workspace.getBoundingClientRect();
      if (bounds.width <= 0 || bounds.height <= 0) return;
      const rightLimits = getRightLimits();
      const bottomLimits = getBottomLimits();
      const rightSize = fitSize('right');
      const bottomSize = fitSize('bottom');
      if (app.dataset.right === 'true') setPanelSize('right', rightSize);
      else updateResizerAria(rightResizer, rightLimits, clampNumber(rightSize, rightLimits.min, rightLimits.max));
      if (app.dataset.bottom === 'true') setPanelSize('bottom', bottomSize);
      else updateResizerAria(bottomResizer, bottomLimits, clampNumber(bottomSize, bottomLimits.min, bottomLimits.max));
    };

    const setPanel = (panel, value, button, panelElement, options = {}) => {
      if (value && panel === 'right') {
        const asked = rule.opening(frame, options);
        setPanelSize('right', asked === undefined ? fitSize('right') : asked);
      }
      if (value && panel === 'bottom') setPanelSize('bottom', fitSize('bottom'));
      app.dataset[panel] = String(value);
      button.setAttribute('aria-pressed', String(value));
      if (panelElement) panelElement.setAttribute('aria-hidden', String(!value));
      if (panel === 'right') rightResizer.hidden = !value;
      if (panel === 'bottom') bottomResizer.hidden = !value;
      liveState.textContent = i18n.text(value ? 'frame.panel.opened' : 'frame.panel.closed', { panel: { key: `frame.panel.${panel}` } });
      if (!(rule.noRefitOnOpen && value && panel === 'right')) window.requestAnimationFrame(updateResizerBounds);
    };

    const toggleLeft = () => {
      const next = app.dataset.left !== 'true';
      setPanel('left', next, leftButton, null);
      if (!next && navigation.contains(document.activeElement)) leftButton.focus();
    };

    const toggleRight = () => {
      const next = app.dataset.right !== 'true';
      setPanel('right', next, rightButton, rightPanel);
      if (!next && (rightPanel.contains(document.activeElement) || document.activeElement === rightResizer)) rightButton.focus();
    };

    const toggleBottom = () => {
      const next = app.dataset.bottom !== 'true';
      setPanel('bottom', next, bottomButton, bottomPanel);
      if (!next && (bottomPanel.contains(document.activeElement) || document.activeElement === bottomResizer)) bottomButton.focus();
    };

    const beginResize = (panel, event) => {
      if (event.button !== 0 || app.dataset[panel] !== 'true') return;
      event.preventDefault();
      hideTooltip();
      activeResize = { panel, pointerId: event.pointerId };
      app.dataset.resizing = panel;
      event.currentTarget.setPointerCapture(event.pointerId);
    };

    const moveResize = (panel, event) => {
      if (!activeResize || activeResize.panel !== panel || activeResize.pointerId !== event.pointerId) return;
      const rect = workspace.getBoundingClientRect();
      const next = panel === 'right' ? rect.right - event.clientX : rect.bottom - event.clientY;
      setPanelSize(panel, next, false, 'drag');
    };

    const endResize = (panel, event) => {
      if (!activeResize || activeResize.panel !== panel || activeResize.pointerId !== event.pointerId) return;
      const target = event.currentTarget;
      if (target.hasPointerCapture(event.pointerId)) target.releasePointerCapture(event.pointerId);
      activeResize = null;
      delete app.dataset.resizing;
      setPanelSize(panel, getPanelSize(panel), true, 'drop');
    };

    const resizeWithKeyboard = (panel, event) => {
      if (app.dataset[panel] !== 'true') return;
      const limits = panel === 'right' ? getRightLimits() : getBottomLimits();
      const current = getPanelSize(panel);
      let next = current;
      if (event.key === 'Home') next = limits.min;
      else if (event.key === 'End') next = limits.max;
      else if (panel === 'right' && event.key === 'ArrowLeft') next = current + 10;
      else if (panel === 'right' && event.key === 'ArrowRight') next = current - 10;
      else if (panel === 'bottom' && event.key === 'ArrowUp') next = current + 10;
      else if (panel === 'bottom' && event.key === 'ArrowDown') next = current - 10;
      else return;
      event.preventDefault();
      setPanelSize(panel, next, true, 'key');
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
    for (const [panel, resizer] of [['right', rightResizer], ['bottom', bottomResizer]]) {
      resizer.addEventListener('pointerdown', (event) => beginResize(panel, event));
      resizer.addEventListener('pointermove', (event) => moveResize(panel, event));
      resizer.addEventListener('pointerup', (event) => endResize(panel, event));
      resizer.addEventListener('pointercancel', (event) => endResize(panel, event));
      resizer.addEventListener('keydown', (event) => resizeWithKeyboard(panel, event));
    }

    root.addEventListener('keydown', (event) => {
      if (!root.contains(document.activeElement)) return;
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
    // native title of such an element is removed, also when one is added later.
    const hideTooltip = () => {
      tooltipTarget = null;
      window.clearTimeout(tooltipTimer);
      tooltip.hidden = true;
      tooltip.textContent = '';
    };

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
      if (ownTooltips || boundTooltipTargets.has(target)) return;
      boundTooltipTargets.add(target);
      target.addEventListener('mouseenter', () => showTooltip(target));
      target.addEventListener('mouseleave', hideTooltip);
      target.addEventListener('focus', () => showTooltip(target));
      target.addEventListener('blur', hideTooltip);
    };
    root.querySelectorAll('[data-desktop-tip]').forEach(bindTooltip);
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
      if (tooltipTarget && (!root.contains(tooltipTarget) || !tooltipTarget.getClientRects().length)) hideTooltip();
    }).observe(root, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['title', 'data-desktop-tip', 'hidden', 'aria-hidden'],
    });
    window.addEventListener('desktop-integrated-route', hideTooltip);

    // Switches: click (or Space and Enter on one that is not a checkbox) flips aria-checked; a switch without a
    // name takes the title of its row. The console-access switch is driven by the settings page itself.
    root.querySelectorAll('.switch').forEach((control) => {
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
    root.querySelectorAll('[data-segmented]').forEach((group) => {
      group.addEventListener('click', (event) => {
        const button = event.target.closest('button');
        if (!button || !group.contains(button)) return;
        group.querySelectorAll('button').forEach((candidate) => candidate.setAttribute('aria-pressed', String(candidate === button)));
      });
    });

    const shell = {
      app,
      root,
      setInspector: (open, options = {}) => setPanel('right', Boolean(open), rightButton, rightPanel, options),
      setSidebar: (open) => setPanel('left', Boolean(open), leftButton, null),
      setBottom: (open) => setPanel('bottom', Boolean(open), bottomButton, bottomPanel),
    };
    if (rule.reset) shell.resetInspectorRatio = () => rule.reset(frame);
    Memorive.frames[component] = shell;

    if (detail) {
      const showDetailAvailability = () => {
        const available = app.dataset.right === 'true' || Boolean(root.querySelector(detail));
        rightButton.setAttribute('aria-disabled', String(!available));
      };
      new MutationObserver(showDetailAvailability).observe(root, { subtree: true, attributes: true, attributeFilter: ['aria-selected', 'aria-current', 'data-right'] });
      showDetailAvailability();
    }

    const resizeObserver = new ResizeObserver(updateResizerBounds);
    resizeObserver.observe(stage);
    if (rule.watchSidebar) resizeObserver.observe(navigation);
    updateResizerBounds();
    app.dataset.shortHeight = 'false';
    stage.style.maxWidth = '1440px';
    stage.style.aspectRatio = '1440 / 900';
  };

  ROUTES.forEach(createFrame);
  Object.freeze(Memorive.frames);
})();
