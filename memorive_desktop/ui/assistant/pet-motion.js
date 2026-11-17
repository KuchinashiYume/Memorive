/* The pet in the assistant window: its frames and animations (blink, attention, working, success, error), the speech
   bubble, the menu, and dragging the pet. runtime-bridge.js feeds it the desktop service's state. */
    (() => {
      const root = document.getElementById('memorive-desktop-assistant-preview');
      if (!root || root.dataset.ready === 'true') return;
      root.dataset.ready = 'true';

      const desktop = document.getElementById('memorive-pet-desktop');
      const anchor = document.getElementById('memorive-pet-anchor');
      const pet = document.getElementById('memorive-pet-window');
      const bubble = document.getElementById('memorive-pet-bubble');
      const bubbleOpen = document.getElementById('memorive-pet-bubble-open');
      const bubbleClose = document.getElementById('memorive-pet-bubble-close');
      const bubbleTitle = document.getElementById('memorive-pet-bubble-title');
      const bubbleCopy = document.getElementById('memorive-pet-bubble-copy');
      const menu = document.getElementById('memorive-pet-menu');
      const statusDot = document.getElementById('memorive-pet-status-dot');
      const desktopStateDot = document.getElementById('memorive-desktop-state-dot');
      const desktopStateLabel = document.getElementById('memorive-desktop-state-label');
      const eventDot = document.getElementById('memorive-pet-event-dot');
      const eventText = document.getElementById('memorive-pet-event-text');
      const visibilityButton = document.getElementById('memorive-pet-visibility');
      const privacyToggle = document.getElementById('memorive-pet-privacy');
      const actionButtons = Array.from(root.querySelectorAll('[data-memorive-action]'));
      const menuButtons = Array.from(root.querySelectorAll('[data-memorive-menu]'));
      const prefersReduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

      const frameMeta = {
        idle: '常驻',
        'blink-open': '眨眼 01',
        'blink-closed': '眨眼 02',
        interaction: '互动',
        working: '施工中',
        success: '已完成'
      };

      const alerts = {
        attention: {
          tone: 'yellow',
          title: '《论文 B》需要你的处理',
          privateTitle: '有一项内容需要你的处理',
          copy: '点击查看待处理详情'
        },
        working: {
          tone: 'blue',
          spinner: true,
          title: '《论文 B》正在处理中',
          privateTitle: '有一项任务正在处理中',
          copy: '点击查看当前任务'
        },
        success: {
          tone: 'green',
          title: '《论文 A》已完成',
          privateTitle: '有一项任务已完成',
          copy: '点击查看生成结果'
        },
        error: {
          tone: 'red',
          title: '《论文 C》处理失败',
          privateTitle: '有一项任务出现异常',
          copy: '点击查看异常详情'
        }
      };

      let visible = true;
      let baseFrame = 'idle';
      let attentionTone = 'none';
      let currentAlert = null;
      let displayState = 'IDLE';
      let animationLocked = false;
      let transient = null;
      let transientTimer = 0;
      let sequenceTimers = [];
      let autoBlinkTimer = 0;
      let bubbleDismissTimer = 0;
      let drag = null;
      let suppressClick = false;

      function setEvent(message, tone = 'blue') {
        eventText.textContent = message;
        eventDot.dataset.tone = tone;
      }

      function clearMotion() {
        pet.classList.remove(
          'memorive-is-tilting',
          'memorive-is-waking',
          'memorive-is-shaking-working',
          'memorive-is-shaking-success',
          'memorive-is-shaking-error'
        );
      }

      function clearSequence() {
        sequenceTimers.forEach(window.clearTimeout);
        sequenceTimers = [];
      }

      function setInteractionLock(locked) {
        animationLocked = locked;
        root.dataset.animationLocked = locked ? 'true' : 'false';
        pet.disabled = locked;
        actionButtons.forEach((button) => { button.disabled = locked; });
        privacyToggle.disabled = locked;
        bubbleOpen.disabled = locked;
        bubbleClose.disabled = locked;
      }

      function updateStateLabel() {
        const displayLabel = !visible ? '隐藏' : displayState === 'MENU_OPEN' ? '菜单打开' : displayState === 'DRAGGING' ? '拖动中' : displayState === 'NOTIFYING' ? '提醒中' : displayState === 'APPEARING' ? '正在唤醒' : '可见';
        const frameLabel = frameMeta[pet.dataset.frame] || frameMeta.idle;
        const attentionLabel = attentionTone === 'none' ? '' : ` · ${attentionTone === 'red' ? '红色异常' : attentionTone === 'yellow' ? '黄色待处理' : attentionTone === 'green' ? '绿色完成' : '蓝色处理中'}`;
        desktopStateLabel.textContent = `${displayLabel} · ${frameLabel}${attentionLabel}`;
        desktopStateDot.dataset.tone = attentionTone;
        pet.setAttribute('aria-label', `Memorive 桌面助手，${displayLabel}，${frameLabel}${attentionLabel}。左键打开 Memorive 主界面；Shift+F10 打开桌面助手设置与控制。`);
      }

      function applyFrame(frame) {
        pet.dataset.frame = frame;
        root.dataset.frame = frame;
        updateStateLabel();
      }

      function updateBubbleCopy() {
        if (!currentAlert) return;
        const content = alerts[currentAlert];
        bubbleTitle.textContent = privacyToggle.checked ? content.privateTitle : content.title;
        bubbleCopy.textContent = content.copy;
      }

      function updatePopupSide() {
        const desktopRect = desktop.getBoundingClientRect();
        const anchorRect = anchor.getBoundingClientRect();
        const useRight = anchorRect.left - desktopRect.left < Math.min(310, desktopRect.width * .42);
        bubble.classList.toggle('memorive-bubble-right', useRight);
        menu.classList.toggle('memorive-menu-right', useRight);
      }

      function clearBubbleDismiss() {
        if (bubbleDismissTimer) window.clearTimeout(bubbleDismissTimer);
        bubbleDismissTimer = 0;
      }

      function scheduleBubbleDismiss() {
        clearBubbleDismiss();
        const alertAtSchedule = currentAlert;
        bubbleDismissTimer = window.setTimeout(() => {
          bubbleDismissTimer = 0;
          if (!alertAtSchedule || currentAlert !== alertAtSchedule || bubble.hidden) return;
          hideBubble({ clearTimer: false });
          setEvent('状态气泡已在 8 秒后自动收起；右下状态标记继续保留。', attentionTone === 'none' ? 'blue' : attentionTone);
        }, 8000);
      }

      function showBubble(kind) {
        const content = alerts[kind];
        if (!content) return false;
        currentAlert = kind;
        attentionTone = content.tone;
        bubble.dataset.tone = content.tone;
        statusDot.dataset.tone = content.tone;
        statusDot.dataset.spinner = content.spinner ? 'true' : 'false';
        updateBubbleCopy();
        updatePopupSide();
        bubble.hidden = false;
        displayState = 'NOTIFYING';
        updateStateLabel();
        scheduleBubbleDismiss();
        return true;
      }

      function hideBubble(options = {}) {
        if (options.clearTimer !== false) clearBubbleDismiss();
        bubble.hidden = true;
        displayState = visible ? 'IDLE' : 'HIDDEN';
        updateStateLabel();
        scheduleAutoBlink();
      }

      function closeMenu(options = {}) {
        if (menu.hidden) return;
        menu.hidden = true;
        pet.setAttribute('aria-expanded', 'false');
        displayState = bubble.hidden ? 'IDLE' : 'NOTIFYING';
        updateStateLabel();
        if (options.focusPet) pet.focus();
        scheduleAutoBlink();
      }

      function openMenu() {
        if (animationLocked || !visible) {
          setEvent('动画播放中；右键菜单暂不可用。', attentionTone === 'none' ? 'blue' : attentionTone);
          return false;
        }
        if (autoBlinkTimer) window.clearTimeout(autoBlinkTimer);
        autoBlinkTimer = 0;
        clearBubbleDismiss();
        bubble.hidden = true;
        updatePopupSide();
        menu.hidden = false;
        pet.setAttribute('aria-expanded', 'true');
        displayState = 'MENU_OPEN';
        updateStateLabel();
        menuButtons[0]?.focus();
        return true;
      }

      function clearTransient(unlock = true) {
        if (transientTimer) window.clearTimeout(transientTimer);
        transientTimer = 0;
        clearSequence();
        clearMotion();
        transient = null;
        if (unlock) setInteractionLock(false);
      }

      function scheduleAutoBlink() {
        if (autoBlinkTimer) window.clearTimeout(autoBlinkTimer);
        autoBlinkTimer = 0;
        if (
          prefersReduced ||
          document.hidden ||
          !visible ||
          baseFrame !== 'idle' ||
          !bubble.hidden ||
          !menu.hidden ||
          animationLocked
        ) return;
        const delay = 8000 + Math.round(Math.random() * 10000);
        autoBlinkTimer = window.setTimeout(() => playBlink('auto'), delay);
      }

      function restoreBase() {
        const completed = transient;
        transientTimer = 0;
        clearSequence();
        clearMotion();
        transient = null;
        setInteractionLock(false);
        displayState = bubble.hidden ? 'IDLE' : 'NOTIFYING';
        applyFrame(baseFrame);
        if (completed?.endMessage) setEvent(completed.endMessage, completed.endTone || 'blue');
        scheduleAutoBlink();
      }

      function beginTransient(frame, duration, message, tone, options = {}) {
        if (animationLocked || !visible) return false;
        closeMenu();
        clearTransient(false);
        if (autoBlinkTimer) window.clearTimeout(autoBlinkTimer);
        autoBlinkTimer = 0;
        transient = {
          frame,
          endMessage: options.endMessage || '动画结束；互动已恢复。',
          endTone: options.endTone || tone
        };
        displayState = options.displayState || (bubble.hidden ? 'IDLE' : 'NOTIFYING');
        setInteractionLock(true);
        applyFrame(frame);
        clearMotion();
        if (options.motionClass && !prefersReduced) {
          void pet.offsetWidth;
          pet.classList.add(options.motionClass);
        }
        setEvent(message, tone);
        transientTimer = window.setTimeout(restoreBase, duration);
        return true;
      }

      function playInteraction(source = 'direct') {
        if (animationLocked) {
          if (source !== 'auto') setEvent('动画播放中；本次互动未执行。', attentionTone === 'none' ? 'pink' : attentionTone);
          return false;
        }
        if (!visible) return wake();
        return beginTransient(
          'interaction',
          prefersReduced ? 320 : 1100,
          '桌面助手歪头回应中；动画期间其他互动已锁定。',
          'pink',
          { motionClass: 'memorive-is-tilting', endMessage: '点击回应结束；互动已恢复。', endTone: 'pink' }
        );
      }

      function playBlink(source = 'manual') {
        if (animationLocked || !visible) {
          if (source !== 'auto') setEvent('当前不能播放眨眼。', 'blue');
          return false;
        }
        if (prefersReduced) {
          if (source !== 'auto') setEvent('系统已启用减少动态效果；眨眼保持静态降级。', 'blue');
          return false;
        }
        if (baseFrame !== 'idle' || !bubble.hidden || !menu.hidden) {
          if (source !== 'auto') setEvent('仅在空闲静态状态播放眨眼。', attentionTone === 'none' ? 'blue' : attentionTone);
          scheduleAutoBlink();
          return false;
        }

        clearTransient(false);
        if (autoBlinkTimer) window.clearTimeout(autoBlinkTimer);
        autoBlinkTimer = 0;
        transient = { frame: 'blink-open', endMessage: '双眨眼结束；互动已恢复。', endTone: 'blue' };
        setInteractionLock(true);
        applyFrame('blink-open');
        setEvent('播放一次双眨眼；第一次睁眼保持 1.2 秒，动画期间互动锁定。', 'blue');
        sequenceTimers.push(window.setTimeout(() => applyFrame('blink-closed'), 1200));
        sequenceTimers.push(window.setTimeout(() => applyFrame('blink-open'), 1650));
        sequenceTimers.push(window.setTimeout(() => applyFrame('blink-closed'), 2250));
        sequenceTimers.push(window.setTimeout(() => applyFrame('blink-open'), 2500));
        sequenceTimers.push(window.setTimeout(restoreBase, 3000));
        return true;
      }

      function wake() {
        if (animationLocked) return false;
        visible = true;
        anchor.hidden = false;
        visibilityButton.textContent = '暂时隐藏';
        displayState = 'APPEARING';
        applyFrame('interaction');
        return beginTransient(
          'interaction',
          prefersReduced ? 320 : 900,
          '桌面助手已显示；正在播放一次唤醒反馈。',
          'pink',
          { motionClass: 'memorive-is-waking', displayState: 'APPEARING', endMessage: '唤醒结束；桌面助手保持可见。', endTone: 'pink' }
        );
      }

      function setVisible(next) {
        if (animationLocked) return false;
        if (next) return wake();
        clearTransient();
        closeMenu();
        clearBubbleDismiss();
        bubble.hidden = true;
        anchor.hidden = true;
        visible = false;
        displayState = 'HIDDEN';
        visibilityButton.textContent = '显示桌宠';
        if (autoBlinkTimer) window.clearTimeout(autoBlinkTimer);
        autoBlinkTimer = 0;
        updateStateLabel();
        setEvent('桌面助手已暂时隐藏；状态变化不会擅自重新显示它。', 'blue');
        return true;
      }

      function clearAlert() {
        clearBubbleDismiss();
        currentAlert = null;
        attentionTone = 'none';
        statusDot.dataset.tone = 'none';
        statusDot.dataset.spinner = 'false';
        bubble.hidden = true;
      }

      function playAttention() {
        if (animationLocked || !visible) return false;
        showBubble('attention');
        return beginTransient(
          'interaction',
          prefersReduced ? 520 : 1100,
          '有一项内容需要处理；黄色提醒不会自动批准任务。',
          'yellow',
          { motionClass: 'memorive-is-tilting', displayState: 'NOTIFYING', endMessage: '待处理动画结束；气泡将在出现 8 秒后自动收起。' }
        );
      }

      function playWorking() {
        if (animationLocked || !visible) return false;
        baseFrame = 'working';
        showBubble('working');
        if (prefersReduced) {
          applyFrame('working');
          setEvent('已进入施工状态；摇摆动画静态降级，蓝色处理标记保留。', 'blue');
          return true;
        }
        return beginTransient(
          'working',
          900,
          '进入施工：左右摇摆振动中；动画期间互动锁定。',
          'blue',
          { motionClass: 'memorive-is-shaking-working', displayState: 'NOTIFYING', endMessage: '施工入场动画结束；蓝色处理标记保留，气泡将在出现 8 秒后自动收起。', endTone: 'blue' }
        );
      }

      function playSuccess() {
        if (animationLocked || !visible) return false;
        showBubble('success');
        return beginTransient(
          'success',
          prefersReduced ? 700 : 1400,
          '任务完整成功：左右摇摆振动中；气泡不抢焦点。',
          'green',
          { motionClass: 'memorive-is-shaking-success', displayState: 'NOTIFYING', endMessage: '完成反馈结束；气泡将在出现 8 秒后自动收起。', endTone: 'green' }
        );
      }

      function playError() {
        if (animationLocked || !visible) return false;
        showBubble('error');
        return beginTransient(
          'idle',
          prefersReduced ? 500 : 900,
          '任务出现异常：播放一次短抖动；不持续闪烁。',
          'red',
          { motionClass: 'memorive-is-shaking-error', displayState: 'NOTIFYING', endMessage: '异常动画结束；气泡将在出现 8 秒后自动收起。', endTone: 'red' }
        );
      }

      function defaultPosition() {
        anchor.style.left = '';
        anchor.style.top = '';
        anchor.style.right = '24px';
        anchor.style.bottom = '48px';
        updatePopupSide();
      }

      function reset() {
        if (animationLocked) return false;
        clearTransient();
        closeMenu();
        clearAlert();
        visible = true;
        anchor.hidden = false;
        visibilityButton.textContent = '暂时隐藏';
        baseFrame = 'idle';
        displayState = 'IDLE';
        applyFrame('idle');
        defaultPosition();
        setEvent('已恢复常驻：无提醒、静态待机、默认右下位置。', 'blue');
        scheduleAutoBlink();
        return true;
      }

      function clampAnchor(left, top) {
        const desktopRect = desktop.getBoundingClientRect();
        const maxLeft = Math.max(8, desktopRect.width - anchor.offsetWidth - 8);
        const maxTop = Math.max(8, desktopRect.height - anchor.offsetHeight - 46);
        return {
          left: Math.min(maxLeft, Math.max(8, left)),
          top: Math.min(maxTop, Math.max(8, top))
        };
      }

      function startDrag(event) {
        if (animationLocked || !visible || !menu.hidden || event.button !== 0) return;
        const desktopRect = desktop.getBoundingClientRect();
        const anchorRect = anchor.getBoundingClientRect();
        drag = {
          pointerId: event.pointerId,
          startX: event.clientX,
          startY: event.clientY,
          left: anchorRect.left - desktopRect.left,
          top: anchorRect.top - desktopRect.top,
          active: false
        };
        pet.setPointerCapture(event.pointerId);
      }

      function moveDrag(event) {
        if (!drag || drag.pointerId !== event.pointerId) return;
        const deltaX = event.clientX - drag.startX;
        const deltaY = event.clientY - drag.startY;
        if (!drag.active && Math.hypot(deltaX, deltaY) < 6) return;
        if (!drag.active) {
          drag.active = true;
          suppressClick = true;
          clearBubbleDismiss();
          bubble.hidden = true;
          displayState = 'DRAGGING';
          anchor.classList.add('memorive-is-dragging');
          applyFrame('interaction');
          setEvent('正在拖动桌面助手；松开后吸附最近安全边缘。', attentionTone === 'none' ? 'pink' : attentionTone);
        }
        const next = clampAnchor(drag.left + deltaX, drag.top + deltaY);
        anchor.style.right = 'auto';
        anchor.style.bottom = 'auto';
        anchor.style.left = `${next.left}px`;
        anchor.style.top = `${next.top}px`;
        updatePopupSide();
      }

      function endDrag(event) {
        if (!drag || drag.pointerId !== event.pointerId) return;
        if (pet.hasPointerCapture(event.pointerId)) pet.releasePointerCapture(event.pointerId);
        const wasActive = drag.active;
        drag = null;
        if (!wasActive) return;
        anchor.classList.remove('memorive-is-dragging');
        const desktopRect = desktop.getBoundingClientRect();
        const anchorRect = anchor.getBoundingClientRect();
        const currentLeft = anchorRect.left - desktopRect.left;
        const currentTop = anchorRect.top - desktopRect.top;
        const snapLeft = currentLeft + anchorRect.width / 2 < desktopRect.width / 2 ? 10 : desktopRect.width - anchorRect.width - 10;
        const next = clampAnchor(snapLeft, currentTop);
        anchor.style.left = `${next.left}px`;
        anchor.style.top = `${next.top}px`;
        displayState = 'IDLE';
        applyFrame(baseFrame);
        updatePopupSide();
        setEvent('拖动结束；已吸附最近安全边缘。', attentionTone === 'none' ? 'pink' : attentionTone);
        window.setTimeout(() => { suppressClick = false; }, 0);
        scheduleAutoBlink();
      }

      function handleAction(action) {
        if (action === 'wake') wake();
        else if (action === 'interaction') playInteraction('control');
        else if (action === 'blink') playBlink('manual');
        else if (action === 'attention') playAttention();
        else if (action === 'working') playWorking();
        else if (action === 'success') playSuccess();
        else if (action === 'error') playError();
        else if (action === 'reset') reset();
        else if (action === 'visibility') setVisible(!visible);
      }

      actionButtons.forEach((button) => {
        button.addEventListener('click', () => handleAction(button.dataset.prosAction));
      });

      pet.addEventListener('click', () => {
        if (suppressClick || animationLocked) return;
        closeMenu();
        hideBubble();
        setEvent('已受理激活 Memorive 主界面；不会创建第二实例。', 'blue');
      });

      pet.addEventListener('contextmenu', (event) => {
        event.preventDefault();
        openMenu();
      });

      pet.addEventListener('keydown', (event) => {
        if ((event.shiftKey && event.key === 'F10') || event.key === 'ContextMenu') {
          event.preventDefault();
          openMenu();
        }
      });

      pet.addEventListener('pointerdown', startDrag);
      pet.addEventListener('pointermove', moveDrag);
      pet.addEventListener('pointerup', endDrag);
      pet.addEventListener('pointercancel', endDrag);

      bubbleOpen.addEventListener('click', () => {
        if (animationLocked) return;
        const routes = {
          attention: '待处理详情',
          working: '当前任务',
          success: '生成结果',
          error: '异常详情'
        };
        const route = routes[currentAlert] || '任务详情';
        hideBubble();
        setEvent(`将打开 Memorive ${route}；状态确认保持不变。`, attentionTone === 'none' ? 'blue' : attentionTone);
      });

      bubbleClose.addEventListener('click', () => {
        if (animationLocked) return;
        hideBubble();
        setEvent('气泡已收起；这不等于确认、批准或完成任务。', attentionTone === 'none' ? 'blue' : attentionTone);
      });

      menuButtons.forEach((button) => {
        button.addEventListener('click', () => {
          const action = button.dataset.prosMenu;
          if (action === 'hide') {
            closeMenu();
            setVisible(false);
            return;
          }
          const messages = {
            codex: '预览：fake launcher 已受理 Codex；未启动真实外部应用。',
            'claude-code': '预览：fake launcher 已受理 Claude Code；未读取账号或会话。',
            'cc-switch': '预览：fake launcher 已受理 CC Switch；未传递任务内容。',
            settings: '预览：将打开“设置 > 应用行为 > 桌面助手与快捷入口”。'
          };
          closeMenu({ focusPet: true });
          setEvent(messages[action] || '预览操作已受理。', 'blue');
        });
      });

      privacyToggle.addEventListener('change', () => {
        updateBubbleCopy();
        setEvent(privacyToggle.checked ? '隐私文案已开启；任务和文件名称已隐藏。' : '隐私文案已关闭；恢复安全显示名称。', 'blue');
      });

      document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        if (!menu.hidden) closeMenu({ focusPet: true });
        else if (!bubble.hidden && !animationLocked) {
          hideBubble();
          pet.focus();
        }
      });

      document.addEventListener('pointerdown', (event) => {
        if (!menu.hidden && !menu.contains(event.target) && event.target !== pet) closeMenu();
      });

      document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
          if (autoBlinkTimer) window.clearTimeout(autoBlinkTimer);
          autoBlinkTimer = 0;
        } else {
          scheduleAutoBlink();
        }
      });

      const resizeObserver = new ResizeObserver(() => {
        if (anchor.style.left) {
          const next = clampAnchor(Number.parseFloat(anchor.style.left) || 0, Number.parseFloat(anchor.style.top) || 0);
          anchor.style.left = `${next.left}px`;
          anchor.style.top = `${next.top}px`;
        }
        updatePopupSide();
      });
      resizeObserver.observe(desktop);

      setInteractionLock(false);
      applyFrame('idle');
      updatePopupSide();
      scheduleAutoBlink();
    })();
