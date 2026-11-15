  (() => {
    const root = document.getElementById('desktop-library-preview');
    if (!root) return;

    const stage = root.querySelector('#desktop-preview-stage');
    const app = root.querySelector('#desktop-window');
    const workspace = root.querySelector('.desktop-workspace');
    const navigation = root.querySelector('.desktop-navigation');
    const logoButton = root.querySelector('#desktop-logo');
    const widthInput = root.querySelector('#desktop-preview-width');
    const heightInput = root.querySelector('#desktop-preview-height');
    const sizeOutput = root.querySelector('#desktop-preview-size-output');
    const sizeError = root.querySelector('#desktop-preview-size-error');
    const liveState = root.querySelector('#desktop-live-state');
    const leftButton = root.querySelector('#desktop-toggle-left');
    const rightButton = root.querySelector('#desktop-toggle-right');
    const bottomButton = root.querySelector('#desktop-toggle-bottom');
    const rightPanel = root.querySelector('#desktop-right-panel');
    const bottomPanel = root.querySelector('#desktop-bottom-panel');
    const rightResizer = root.querySelector('#desktop-resize-right');
    const bottomResizer = root.querySelector('#desktop-resize-bottom');
    const blankState = root.querySelector('#desktop-blank-state');
    const pagePlaceholder = root.querySelector('#desktop-page-placeholder');
    const pageCopy = root.querySelector('#desktop-page-copy');
    const pageTitle = root.querySelector('#desktop-page-title');
    const viewName = root.querySelector('#desktop-view-name');
    const returnButton = root.querySelector('#desktop-return-main');
    const tooltip = root.querySelector('#desktop-tooltip');
    const pageView = root.querySelector('#desktop-page-view');
    const currentPage = "资料库";
    const navItems = Array.from(root.querySelectorAll('.desktop-nav-item'));
    let logicalWidth = 1440;
    let logicalHeight = 900;
    let tooltipTimer = 0;
    let activeResize = null;
    let pageTransitionTimer = 0;
    let pageTransitionVersion = 0;
    let logoClickCount = 0;
    let rightRatio = 0.4;
    const DEFAULT_RIGHT_RATIO = 0.4;

    const clampNumber = (value, min, max) => Math.min(max, Math.max(min, value));
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

    const getPrimarySecondaryWidth = () => {
      const workspaceWidth = workspace.getBoundingClientRect().width;
      const leftWidth = app.dataset.left === 'true' ? navigation.getBoundingClientRect().width : 0;
      return Math.max(0, workspaceWidth - leftWidth);
    };

    const getPanelSize = (panel) => {
      if (panel === 'right') return getPrimarySecondaryWidth() * rightRatio;
      const property = panel === 'right' ? '--desktop-right-size' : '--desktop-bottom-size';
      const inlineValue = Number.parseFloat(workspace.style.getPropertyValue(property));
      if (Number.isFinite(inlineValue)) return inlineValue;
      return 346;
    };

    const getRightLimits = () => {
      const contentWidth = getPrimarySecondaryWidth();
      const minPrimary = Math.min(360, Math.max(220, contentWidth * 0.36));
      const max = Math.max(160, contentWidth - minPrimary);
      const requestedMin = contentWidth <= 620 ? 200 : 280;
      return { min: Math.min(requestedMin, max), max };
    };

    const getBottomLimits = () => {
      const workspaceHeight = workspace.getBoundingClientRect().height;
      const bottomGap = Number.parseFloat(getComputedStyle(workspace).getPropertyValue('--desktop-bottom-gap')) || 0;
      const max = Math.max(64, Math.min(528, workspaceHeight - bottomGap - 112));
      return { min: Math.min(240, max), max };
    };

    const updateResizerAria = (resizer, limits, value) => {
      const rounded = Math.round(value);
      resizer.setAttribute('aria-valuemin', String(Math.round(limits.min)));
      resizer.setAttribute('aria-valuemax', String(Math.round(limits.max)));
      resizer.setAttribute('aria-valuenow', String(rounded));
      resizer.setAttribute('aria-valuetext', `${rounded} 像素`);
    };

    const setPanelSize = (panel, value, announce = false, rememberRatio = false) => {
      const limits = panel === 'right' ? getRightLimits() : getBottomLimits();
      const next = clampNumber(value, limits.min, limits.max);
      if (panel === 'right' && rememberRatio && getPrimarySecondaryWidth() > 0) {
        rightRatio = next / getPrimarySecondaryWidth();
      }
      const property = panel === 'right' ? '--desktop-right-size' : '--desktop-bottom-size';
      const resizer = panel === 'right' ? rightResizer : bottomResizer;
      workspace.style.setProperty(property, `${Math.round(next)}px`);
      updateResizerAria(resizer, limits, next);
      if (announce) {
        liveState.textContent = `${panel === 'right' ? '右侧面板宽度' : '底部面板高度'} ${Math.round(next)} 像素`;
      }
      return next;
    };

    const updateResizerBounds = () => {
      // Hidden routes have no layout; keep the last visible panel sizes.
      const bounds = workspace.getBoundingClientRect();
      if (bounds.width <= 0 || bounds.height <= 0) return;
      const rightLimits = getRightLimits();
      const bottomLimits = getBottomLimits();
      const rightSize = getPanelSize('right');
      const bottomSize = getPanelSize('bottom');
      if (app.dataset.right === 'true') setPanelSize('right', rightSize);
      else updateResizerAria(rightResizer, rightLimits, clampNumber(rightSize, rightLimits.min, rightLimits.max));
      if (app.dataset.bottom === 'true') setPanelSize('bottom', bottomSize);
      else updateResizerAria(bottomResizer, bottomLimits, clampNumber(bottomSize, bottomLimits.min, bottomLimits.max));
    };

    const updateScaleLabel = () => {};

    const applySize = () => {
      logicalWidth = 1440;
      logicalHeight = 900;
      app.dataset.shortHeight = 'false';
      stage.style.maxWidth = '1440px';
      stage.style.aspectRatio = '1440 / 900';
    };

    const setPanel = (panel, value, button, panelElement, label, options = {}) => {
      if (value && panel === 'right') {
        if (app.dataset.right !== 'true') rightRatio = Number(options.targetRatio) || DEFAULT_RIGHT_RATIO;
        setPanelSize('right', getPanelSize('right'));
      }
      if (value && panel === 'bottom') setPanelSize('bottom', getPanelSize('bottom'));
      app.dataset[panel] = String(value);
      button.setAttribute('aria-pressed', String(value));
      if (panelElement) panelElement.setAttribute('aria-hidden', String(!value));
      if (panel === 'right') rightResizer.hidden = !value;
      if (panel === 'bottom') bottomResizer.hidden = !value;
      liveState.textContent = `${label}${value ? '已打开' : '已关闭'}`;
      window.requestAnimationFrame(updateResizerBounds);
    };

    const toggleLeft = () => {
      const next = app.dataset.left !== 'true';
      setPanel('left', next, leftButton, null, '左侧导航栏');
      if (!next && root.querySelector('.desktop-navigation')?.contains(document.activeElement)) leftButton.focus();
    };

    const toggleRight = () => {
      const next = app.dataset.right !== 'true';
      setPanel('right', next, rightButton, rightPanel, '右侧面板');
      if (!next && (rightPanel.contains(document.activeElement) || document.activeElement === rightResizer)) rightButton.focus();
    };

    const toggleBottom = () => {
      const next = app.dataset.bottom !== 'true';
      setPanel('bottom', next, bottomButton, bottomPanel, '底部面板');
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
      setPanelSize(panel, next, false, true);
    };

    const endResize = (panel, event) => {
      if (!activeResize || activeResize.panel !== panel || activeResize.pointerId !== event.pointerId) return;
      const target = event.currentTarget;
      if (target.hasPointerCapture(event.pointerId)) target.releasePointerCapture(event.pointerId);
      activeResize = null;
      delete app.dataset.resizing;
      setPanelSize(panel, getPanelSize(panel), true);
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
      setPanelSize(panel, next, true, true);
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

    const commitPage = (page) => {
      const isCurrentPage = page === currentPage;
      blankState.hidden = true;
      blankState.classList.remove('desktop-text-exit');
      pageView.hidden = !isCurrentPage;
      pagePlaceholder.hidden = isCurrentPage;
      returnButton.hidden = false;
      navItems.forEach((item) => {
        const current = item.dataset.page === page;
        item.setAttribute('aria-pressed', String(current));
        if (current) item.setAttribute('aria-current', 'page');
        else item.removeAttribute('aria-current');
      });
      pageTitle.textContent = page;
      viewName.textContent = page;
      if (!isCurrentPage && app.dataset.right === 'true') {
        setPanel('right', false, rightButton, rightPanel, '右侧面板');
      }
      liveState.textContent = `已打开${page}`;
    };

    const returnToMain = () => {
      if (window.parent !== window) {
        window.parent.postMessage({ type: 'desktop-integrated-return-main', page: currentPage }, '*');
        return;
      }
      ++pageTransitionVersion;
      window.clearTimeout(pageTransitionTimer);
      navItems.forEach((item) => {
        item.setAttribute('aria-pressed', 'false');
        item.removeAttribute('aria-current');
      });
      if (app.dataset.right === 'true') setPanel('right', false, rightButton, rightPanel, '右侧面板');
      if (app.dataset.bottom === 'true') setPanel('bottom', false, bottomButton, bottomPanel, '底部面板');
      blankState.hidden = false;
      blankState.classList.remove('desktop-text-exit');
      pageView.hidden = true;
      pagePlaceholder.hidden = true;
      pageCopy.classList.remove('desktop-text-exit', 'desktop-text-enter');
      returnButton.hidden = true;
      viewName.classList.remove('desktop-text-exit', 'desktop-text-enter');
      viewName.textContent = '主体界面';
      liveState.textContent = '已返回主界面';
    };

    const showPage = (page) => {
      const version = ++pageTransitionVersion;
      window.clearTimeout(pageTransitionTimer);
      pageCopy.classList.remove('desktop-text-enter');
      viewName.classList.remove('desktop-text-enter');

      if (prefersReducedMotion.matches) {
        pageCopy.classList.remove('desktop-text-exit');
        viewName.classList.remove('desktop-text-exit');
        commitPage(page);
        return;
      }

      const outgoing = blankState.hidden ? pageCopy : blankState;
      outgoing.classList.add('desktop-text-exit');
      viewName.classList.add('desktop-text-exit');

      pageTransitionTimer = window.setTimeout(() => {
        if (version !== pageTransitionVersion) return;
        commitPage(page);
        pageCopy.classList.remove('desktop-text-exit');
        pageCopy.classList.add('desktop-text-enter');
        viewName.classList.remove('desktop-text-exit');
        viewName.classList.add('desktop-text-enter');

        window.requestAnimationFrame(() => {
          window.requestAnimationFrame(() => {
            if (version !== pageTransitionVersion) return;
            pageCopy.classList.remove('desktop-text-enter');
            viewName.classList.remove('desktop-text-enter');
          });
        });
      }, 120);
    };

    leftButton.addEventListener('click', toggleLeft);
    rightButton.addEventListener('click', toggleRight);
    bottomButton.addEventListener('click', toggleBottom);
    logoButton.addEventListener('click', playLogoAnimation);
    logoButton.addEventListener('animationend', finishLogoAnimation);
    logoButton.addEventListener('animationcancel', finishLogoAnimation);
    rightResizer.addEventListener('pointerdown', (event) => beginResize('right', event));
    rightResizer.addEventListener('pointermove', (event) => moveResize('right', event));
    rightResizer.addEventListener('pointerup', (event) => endResize('right', event));
    rightResizer.addEventListener('pointercancel', (event) => endResize('right', event));
    rightResizer.addEventListener('keydown', (event) => resizeWithKeyboard('right', event));
    bottomResizer.addEventListener('pointerdown', (event) => beginResize('bottom', event));
    bottomResizer.addEventListener('pointermove', (event) => moveResize('bottom', event));
    bottomResizer.addEventListener('pointerup', (event) => endResize('bottom', event));
    bottomResizer.addEventListener('pointercancel', (event) => endResize('bottom', event));
    bottomResizer.addEventListener('keydown', (event) => resizeWithKeyboard('bottom', event));
    returnButton.addEventListener('click', returnToMain);

    navItems.forEach((item) => {
      item.addEventListener('click', () => {
        navItems.forEach((candidate) => candidate.setAttribute('aria-pressed', String(candidate === item)));
        const page = item.dataset.page || '页面';
        showPage(page);
      });
    });

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

    const hideTooltip = () => {
      window.clearTimeout(tooltipTimer);
      tooltip.hidden = true;
      tooltip.textContent = '';
    };

    const showTooltip = (target) => {
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
      if (target instanceof Element && target.matches('[data-desktop-tip]')) {
        target.removeAttribute('title');
      }
    };

    root.querySelectorAll('[data-desktop-tip]').forEach((target) => {
      suppressNativeTooltip(target);
      target.addEventListener('mouseenter', () => showTooltip(target));
      target.addEventListener('mouseleave', hideTooltip);
      target.addEventListener('focus', () => showTooltip(target));
      target.addEventListener('blur', hideTooltip);
    });
    const nativeTooltipObserver = new MutationObserver((records) => {
      records.forEach((record) => suppressNativeTooltip(record.target));
    });
    nativeTooltipObserver.observe(root, {
      subtree:true,
      attributes:true,
      attributeFilter:['title']
    });


    root.querySelectorAll('.switch').forEach((control) => {
      control.addEventListener('click', () => {
        const next = control.getAttribute('aria-checked') !== 'true';
        control.setAttribute('aria-checked', String(next));
      });
    });

    root.querySelectorAll('[data-segmented]').forEach((group) => {
      group.addEventListener('click', (event) => {
        const button = event.target.closest('button');
        if (!button || !group.contains(button)) return;
        group.querySelectorAll('button').forEach((candidate) => candidate.setAttribute('aria-pressed', String(candidate === button)));
      });
    });

    window.DesktopPreview = window.DesktopPreview_library = {
      app,
      root,
      setInspector: (open, options = {}) => setPanel('right', Boolean(open), rightButton, rightPanel, '右侧面板', options),
      setSidebar: (open) => setPanel('left', Boolean(open), leftButton, null, '左侧导航栏'),
      setBottom: (open) => setPanel('bottom', Boolean(open), bottomButton, bottomPanel, '底部面板'),
    };

    const resizeObserver = new ResizeObserver(() => {
      updateScaleLabel();
      updateResizerBounds();
    });
    resizeObserver.observe(stage);
    resizeObserver.observe(navigation);
    updateResizerBounds();
    applySize();
    if (window.parent !== window) commitPage(currentPage);
  })();

