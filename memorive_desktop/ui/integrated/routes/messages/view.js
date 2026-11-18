/* Draws the messages page from what bridge.js loaded: message cards, tones (failure, needs attention, done, notice),
   selection and the detail panel. Publishes Memorive.messagesView for bridge.js. */
  (() => {
    const root = document.getElementById('desktop-messages-preview');
    const preview = Memorive.frames.messages;
    if (!root || !preview) return;
    const q = (selector) => root.querySelector(selector);
    const qa = (selector) => [...root.querySelectorAll(selector)];
    let mode = 'current';
    let bulk = false;
    let selectedCard = null;
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let batchbarTransitionVersion = 0;
    const setBatchbarOpen = (bar, open) => {
      const version = ++batchbarTransitionVersion;
      bar.classList.remove('desktop-batch-entering', 'desktop-batch-leaving');
      if (open) {
        bar.hidden = false;
        if (reducedMotion.matches) return;
        void bar.offsetWidth;
        bar.classList.add('desktop-batch-entering');
        window.setTimeout(() => { if (version === batchbarTransitionVersion) bar.classList.remove('desktop-batch-entering'); }, 210);
        return;
      }
      if (bar.hidden) return;
      if (reducedMotion.matches) {
        bar.hidden = true;
        return;
      }
      void bar.offsetWidth;
      bar.classList.add('desktop-batch-leaving');
      window.setTimeout(() => {
        if (version !== batchbarTransitionVersion) return;
        bar.hidden = true;
        bar.classList.remove('desktop-batch-leaving');
      }, 170);
    };
    let toastTimer = 0;
    let toastFadeTimer = 0;
    let toastVersion = 0;
    const cards = () => qa('.desktop-messages-card');
    const visibleCards = () => cards().filter((card) => !card.hidden);
    const formatMessageTimestamp = (raw) => {
      const parsed = new Date(String(raw || ''));
      if (Number.isNaN(parsed.getTime())) {
        return { raw:String(raw || ''), day:'日期未知', clock:'—', full:'时间未知' };
      }
      const now = new Date();
      const sameLocalDay = parsed.getFullYear() === now.getFullYear()
        && parsed.getMonth() === now.getMonth()
        && parsed.getDate() === now.getDate();
      const day = sameLocalDay
        ? '今天'
        : new Intl.DateTimeFormat('zh-CN', { month:'2-digit', day:'2-digit' })
          .format(parsed).replace('/', '-');
      const clock = new Intl.DateTimeFormat('zh-CN', {
        hour:'2-digit', minute:'2-digit', hourCycle:'h23'
      }).format(parsed);
      const full = Memorive.i18n.dateTime(raw);
      return { raw:String(raw), day, clock, full };
    };
    const initializeCard = (card) => {
      const copy = card.querySelector('.desktop-messages-copy');
      if (copy) {
        copy.setAttribute('aria-controls', 'desktop-right-panel');
        copy.setAttribute('aria-expanded', 'false');
      }
      const status = card.querySelector(':scope > .status-dot');
      if (status) status.tabIndex = 0;
    };
    cards().forEach(initializeCard);
    const clearToastTimers = () => {
      if (toastTimer) window.clearTimeout(toastTimer);
      if (toastFadeTimer) window.clearTimeout(toastFadeTimer);
      toastTimer = 0;
      toastFadeTimer = 0;
    };
    const hideToast = ({ animate = true } = {}) => {
      const toast = q('#messages-toast');
      clearToastTimers();
      const version = ++toastVersion;
      if (toast.hidden) return;
      if (!animate || reducedMotion.matches) {
        toast.hidden = true;
        toast.classList.remove('is-fading');
        return;
      }
      toast.classList.add('is-fading');
      toastFadeTimer = window.setTimeout(() => {
        if (version !== toastVersion) return;
        toast.hidden = true;
        toast.classList.remove('is-fading');
        toastFadeTimer = 0;
      }, 280);
    };
    const showToast = (copy) => {
      const toast = q('#messages-toast');
      clearToastTimers();
      const version = ++toastVersion;
      q('#messages-toast-copy').textContent = copy;
      toast.classList.remove('is-fading');
      toast.hidden = false;
      toastTimer = window.setTimeout(() => {
        if (version === toastVersion) hideToast({ animate: true });
      }, 5000);
    };
    const closeDetail = ({ restoreFocus = false } = {}) => {
      const closingCard = selectedCard;
      selectedCard = null;
      cards().forEach((card) => {
        card.setAttribute('aria-selected','false');
        card.querySelector('.desktop-messages-copy')?.setAttribute('aria-expanded', 'false');
      });
      preview.setInspector(false);
      if (restoreFocus && closingCard && !closingCard.hidden) {
        requestAnimationFrame(() => closingCard.querySelector('.desktop-messages-copy')?.focus({ preventScroll:true }));
      }
    };
    const eligibleRead = (card) => card.dataset.view === 'current' && card.classList.contains('unread') && !card.classList.contains('protected');
    const eligibleConfirm = (card) => card.dataset.view === 'current' && !card.dataset.pendingHistory && ['green','blue'].includes(card.dataset.tone);
    const markCardRead = (card) => {
      if (!card || card.dataset.view !== 'current') return false;
      const changed = card.classList.contains('unread');
      card.classList.remove('unread');
      updateReadButton();
      updateBatch();
      return changed;
    };
    const setStar = (card, on) => {
      const star = card.querySelector('.desktop-messages-star');
      star.setAttribute('aria-pressed', String(on));
      star.setAttribute('aria-label', on ? '取消星标' : '添加星标');
      star.textContent = on ? '★' : '☆';
    };
    const ensureHistoryLabel = (card) => {
      let label = card.querySelector('.desktop-messages-history');
      if (!label) {
        label = document.createElement('span');
        label.className = 'desktop-messages-history';
        label.textContent = '已确认';
        card.append(label);
      }
      label.hidden = false;
    };
    const renderCardAction = (card) => {
      const action = card.querySelector('.desktop-messages-action');
      const history = card.querySelector('.desktop-messages-history');
      if (history) history.hidden = true;
      if (card.dataset.pendingHistory === 'true') {
        card.classList.add('confirmed');
        if (action) {
          action.hidden = true;
          action.disabled = true;
          delete action.dataset.command;
        }
        ensureHistoryLabel(card);
        return;
      }
      if (card.dataset.view === 'history') {
        card.classList.add('confirmed');
        if (['red','yellow'].includes(card.dataset.tone) && card.dataset.targetValid === 'true' && action) {
          action.hidden = false;
          action.disabled = false;
          action.dataset.command = 'goto';
          action.textContent = '前往';
          action.setAttribute('aria-label', '前往对应任务节点');
        } else {
          if (action) action.hidden = true;
          ensureHistoryLabel(card);
        }
        return;
      }
      card.classList.remove('confirmed');
      if (!action) return;
      action.hidden = false;
      action.disabled = false;
      if (['red','yellow'].includes(card.dataset.tone)) {
        action.dataset.command = 'goto';
        action.textContent = '前往';
        action.setAttribute('aria-label', '前往对应任务节点');
      } else {
        action.dataset.command = 'confirm';
        action.textContent = '确认';
        action.setAttribute('aria-label', '确认消息');
      }
    };
    const updateReadButton = () => {
      const button = q('#messages-read');
      if (bulk) {
        button.disabled = true;
        button.textContent = '一键已读';
        button.title = '批量模式只处理当前勾选范围';
        return;
      }
      button.textContent = '一键已读';
      const visible = visibleCards();
      const count = visible.filter(eligibleRead).length;
      const unreadProtected = visible.some((card) => card.classList.contains('unread') && (card.classList.contains('protected') || ['red','yellow'].includes(card.dataset.tone)));
      button.disabled = count === 0;
      button.title = count ? `可标记 ${count} 条为已读` : (unreadProtected ? '当前消息需要逐条查看' : '当前范围没有可处理的普通未读消息');
    };
    const selectedCards = () => visibleCards().filter((card) => card.querySelector('.desktop-messages-check').checked);
    const updateBatch = () => {
      const selected = selectedCards();
      const readCount = selected.filter(eligibleRead).length;
      const confirmCount = selected.filter(eligibleConfirm).length;
      q('#messages-selected').textContent = `已选 ${selected.length} 项`;
      q('#messages-eligibility').textContent = readCount ? ` · 可已读 ${readCount}` : '';
      q('#messages-star-add').disabled = selected.length === 0;
      q('#messages-star-remove').disabled = selected.length === 0;
      q('#messages-batch-read').textContent = `标为已读 ${readCount} 条`;
      q('#messages-batch-confirm').textContent = confirmCount ? `确认 ${confirmCount} 条` : '确认可处理消息';
      q('#messages-batch-read').disabled = readCount === 0;
      q('#messages-batch-confirm').disabled = confirmCount === 0;
      const historyMode = mode === 'history';
      q('#messages-batch-read').hidden = historyMode;
      q('#messages-batch-confirm').hidden = historyMode;
    };
    const applyMode = ({ preserveDetail = false } = {}) => {
      let visible = 0;
      cards().forEach((card) => {
        renderCardAction(card);
        const show = mode === 'starred' ? card.querySelector('.desktop-messages-star').getAttribute('aria-pressed') === 'true' : card.dataset.view === mode;
        card.hidden = !show;
        if (!show) card.querySelector('.desktop-messages-check').checked = false;
        if (show) visible += 1;
      });
      q('#messages-count').textContent = String(visible);
      q('#messages-count').setAttribute('aria-label', `当前显示 ${visible} 条消息`);
      q('#messages-empty').hidden = visible > 0;
      if (!preserveDetail) closeDetail();
      updateReadButton();
      updateBatch();
    };
    const contentRows=new WeakMap();
    const renderRows = (rows) => {
      const list = q('#messages-list');
      const toneBySeverity = { RED:'red', YELLOW:'yellow', GREEN:'green', BLUE:'blue' };
      list.replaceChildren(...(Array.isArray(rows) ? rows : []).map((row) => {
        const originalRow=row;
        row=Memorive.contentLanguage.project(row);
        const tone = toneBySeverity[row.severity] || 'blue';
        const card = document.createElement('article');
        card.className = 'desktop-messages-card';
        contentRows.set(card,originalRow);
        if (row.read_state === 'UNREAD') card.classList.add('unread');
        if (row.protected_bulk_read === true) card.classList.add('protected');
        if (row.confirmation_state === 'CONFIRMED') card.classList.add('confirmed');
        if (row.confirmation_state === 'CONFIRMED_PENDING_HISTORY') {
          card.classList.add('confirmed');
          card.dataset.pendingHistory = 'true';
        }
        card.dataset.messageId = String(row.message_id || '');
        card.dataset.title = String(row.title || '消息');
        card.dataset.summary = String(row.summary || row.body || '');
        const timestamp = formatMessageTimestamp(row.last_seen_at || row.created_at || '');
        card.dataset.time = timestamp.raw;
        card.dataset.displayTime = timestamp.full;
        card.dataset.tone = tone;
        card.dataset.view = row.collection_state === 'HISTORY' ? 'history' : 'current';
        card.dataset.locator = String(row.target_locator || '');
        card.dataset.targetValid = String(Boolean(row.target_locator) && row.target_available !== false);
        card.dataset.targetNodeId = String(row.target_node_id || '');
        card.dataset.attachment = String(Array.isArray(row.attachment_refs) && row.attachment_refs.length > 0);
        card.setAttribute('aria-selected', 'false');

        const starCell = document.createElement('div');
        starCell.className = 'desktop-messages-starcell';
        const check = document.createElement('input');
        check.className = 'desktop-messages-check';
        check.type = 'checkbox';
        check.setAttribute('aria-label', `选择消息：${card.dataset.title}`);
        const star = document.createElement('button');
        star.className = 'desktop-messages-star';
        star.type = 'button';
        star.setAttribute('aria-pressed', String(row.starred === true));
        star.setAttribute('aria-label', row.starred === true ? '取消星标' : '添加星标');
        star.textContent = row.starred === true ? '★' : '☆';
        starCell.append(check, star);

        const copy = document.createElement('button');
        copy.className = 'desktop-messages-copy';
        copy.type = 'button';
        const title = document.createElement('strong');
        title.textContent = tone === 'red' && card.dataset.title.endsWith(' 处理失败') ? card.dataset.title.slice(0, -5) : card.dataset.title;
        title.title = card.dataset.title;
        const summary = document.createElement('small');
        summary.textContent = card.dataset.summary;
        copy.append(title, summary);
        const time = document.createElement('time');
        time.dateTime = timestamp.raw;
        const timeDay = document.createElement('span');
        timeDay.className = 'desktop-messages-time-day';
        timeDay.textContent = timestamp.day;
        const timeClock = document.createElement('span');
        timeClock.className = 'desktop-messages-time-clock';
        timeClock.textContent = timestamp.clock;
        time.append(timeDay, timeClock);
        const status = document.createElement('span');
        status.className = `status-dot ${tone}`;
        status.setAttribute('aria-label', ({red:'处理失败',yellow:'需要处理',green:'任务完成',blue:'普通通知'})[tone]);
        const action = document.createElement('button');
        action.className = 'desktop-messages-action';
        action.type = 'button';
        card.append(starCell, copy, time, status, action);
        initializeCard(card);
        return card;
      }));
      applyMode({ preserveDetail:true });
      return cards().length;
    };
    const showDetail = (card) => {
      if (selectedCard === card && preview.app.dataset.right === 'true') {
        // SAME_MESSAGE_ALREADY_OPEN: keep the current detail state.
        return;
      }
      selectedCard = card;
      markCardRead(card);
      cards().forEach((item) => {
        const current = item === card;
        item.setAttribute('aria-selected', String(current));
        item.querySelector('.desktop-messages-copy')?.setAttribute('aria-expanded', String(current));
      });
      q('#messages-title').textContent = card.dataset.tone === 'red' && card.dataset.title.endsWith(' 处理失败') ? card.dataset.title.slice(0, -5) : card.dataset.title;
      q('#messages-title').title = card.dataset.title;
      q('#messages-summary').textContent = card.dataset.summary;
      q('#messages-time').textContent = card.dataset.displayTime;
      q('#messages-locator').textContent = card.dataset.locator;
      const tone = card.dataset.tone;
      q('#messages-tone').className = 'badge ' + tone;
      q('#messages-tone').innerHTML = '<span class="status-dot ' + tone + '"></span>' + ({red:'处理失败',yellow:'需要处理',green:'任务完成',blue:'普通通知'})[tone];
      const pending = card.dataset.view === 'current' && card.dataset.pendingHistory === 'true';
      const current = card.dataset.view === 'current' && !pending;
      const historical = card.dataset.view === 'history';
      const canGoto = ['red','yellow'].includes(tone) && card.dataset.targetValid === 'true' && (current || historical);
      const canConfirm = current;
      q('#messages-detail-goto').hidden = !canGoto;
      q('#messages-detail-confirm').hidden = !canConfirm;
      q('#messages-detail-confirm').textContent = '确认';
      q('#messages-detail-confirmed').hidden = current;
      q('#messages-locator-section').hidden = !['red','yellow'].includes(tone);
      q('#messages-attachment').hidden = card.dataset.attachment !== 'true';
      preview.setInspector(true, { targetRatio:0.4 });
      requestAnimationFrame(() => q('#messages-title').focus({ preventScroll:true }));
    };
    const gotoTarget = (card) => {
      if (card.dataset.targetValid !== 'true') {
        showToast('找不到关联任务或步骤。');
        return;
      }
      showToast('正在定位对应任务与流程节点…');
    };
    const confirmMessage = (card, allowProtected = false) => {
      if (['red','yellow'].includes(card.dataset.tone) && !allowProtected) {
        showToast('红色与黄色消息只能在详情中手动确认。');
        return false;
      }
      if (card.dataset.view !== 'current' || card.dataset.pendingHistory === 'true') return false;
      card.classList.add('confirmed');
      card.classList.remove('unread');
      card.dataset.pendingHistory = 'true';
      renderCardAction(card);
      updateReadButton();
      showToast('已确认');
      return true;
    };
    q('#messages-list').addEventListener('click', (event) => {
      const card = event.target.closest('.desktop-messages-card');
      if (!card) return;
      if (event.target.closest('.desktop-messages-check')) {
        updateBatch();
        return;
      }
      const star = event.target.closest('.desktop-messages-star');
      if (star) {
        event.stopPropagation();
        setStar(card, star.getAttribute('aria-pressed') !== 'true');
        if (mode === 'starred') applyMode(); else updateBatch();
        return;
      }
      const action = event.target.closest('.desktop-messages-action');
      if (action) {
        event.stopPropagation();
        if (action.dataset.command === 'goto') gotoTarget(card);
        else if (action.dataset.command === 'confirm') confirmMessage(card);
        else if (action.dataset.command === 'archive-confirmed') {
          card.dataset.view = 'history';
          delete card.dataset.pendingHistory;
          renderCardAction(card);
          applyMode();
        }
        return;
      }
      if (event.target.closest('.desktop-messages-copy')) showDetail(card);
      else if (!event.target.closest('button,input,a,label,select')) card.querySelector('.desktop-messages-copy')?.click();
    });
    const openDetailById = (messageId) => {
      const card = cards().find((item) => item.dataset.messageId === String(messageId || ''));
      if (!card || card.hidden) return false;
      showDetail(card);
      return true;
    };
    qa('#messages-mode button').forEach((button) => button.addEventListener('click', () => {
      mode = button.dataset.mode;
      applyMode();
    }));
    q('#messages-read').addEventListener('click', () => {
      const eligible = visibleCards().filter(eligibleRead);
      eligible.forEach((card) => {
        card.classList.remove('unread');
        card.dataset.pendingHistory = 'true';
        renderCardAction(card);
      });
      if (selectedCard && eligible.includes(selectedCard)) {
        q('#messages-detail-confirm').hidden = true;
        q('#messages-detail-confirmed').hidden = false;
      }
      updateReadButton();
      showToast(`已标记 ${eligible.length} 条为已读`);
    });
    q('#messages-bulk').addEventListener('click', () => {
      bulk = !bulk;
      q('#messages-bulk').setAttribute('aria-pressed', String(bulk));
      q('#messages-bulk').textContent = bulk ? '退出批量' : '批量管理';
      setBatchbarOpen(q('#messages-batchbar'), bulk);
      q('#messages-list').classList.toggle('bulk', bulk);
      closeDetail();
      if (!bulk) qa('.desktop-messages-check').forEach((box) => { box.checked = false; });
      updateReadButton();
      updateBatch();
    });
    q('#messages-select-visible').addEventListener('click', () => {
      visibleCards().forEach((card) => { card.querySelector('.desktop-messages-check').checked = true; });
      updateBatch();
    });
    q('#messages-clear-selected').addEventListener('click', () => {
      qa('.desktop-messages-check').forEach((box) => { box.checked = false; });
      updateBatch();
    });
    q('#messages-star-add').addEventListener('click', () => {
      const selected = selectedCards();
      selected.forEach((card) => setStar(card, true));
      showToast(`已加星 ${selected.length} 条`);
      updateBatch();
    });
    q('#messages-star-remove').addEventListener('click', () => {
      const selected = selectedCards();
      selected.forEach((card) => setStar(card, false));
      showToast(`已取消 ${selected.length} 条加星`);
      if (mode === 'starred') applyMode(); else updateBatch();
    });
    q('#messages-batch-read').addEventListener('click', () => {
      const selected = selectedCards();
      const eligible = selected.filter(eligibleRead);
      eligible.forEach((card) => card.classList.remove('unread'));
      showToast(`已标记 ${eligible.length} 条为已读` + (selected.length > eligible.length ? ` · 跳过 ${selected.length - eligible.length} 条` : ''));
      updateReadButton();
      updateBatch();
    });
    q('#messages-batch-confirm').addEventListener('click', () => {
      const selected = selectedCards();
      const eligible = selected.filter(eligibleConfirm);
      eligible.forEach((card) => confirmMessage(card));
      showToast(`已确认 ${eligible.length} 条` + (selected.length > eligible.length ? ` · 跳过 ${selected.length - eligible.length} 条` : ''));
      updateBatch();
    });
    q('#messages-refresh').addEventListener('click', () => {
      const pending = cards().filter((card) => card.dataset.pendingHistory === 'true');
      pending.forEach((card) => {
        card.dataset.view = 'history';
        delete card.dataset.pendingHistory;
        renderCardAction(card);
      });
      showToast(`${pending.length} 条消息已移至历史`);
      applyMode();
    });
    q('#messages-close').addEventListener('click', () => closeDetail({ restoreFocus:true }));
    q('#messages-toast button').addEventListener('click', () => hideToast({ animate: true }));
    q('#messages-detail-goto').addEventListener('click', () => { if (selectedCard) gotoTarget(selectedCard); });
    q('#messages-detail-confirm').addEventListener('click', () => {
      if (!selectedCard) return;
      if (selectedCard.dataset.pendingHistory === 'true') {
        selectedCard.dataset.view = 'history';
        delete selectedCard.dataset.pendingHistory;
        renderCardAction(selectedCard);
        applyMode();
        return;
      }
      if (confirmMessage(selectedCard, true)) showDetail(selectedCard);
    });
    window.addEventListener('desktop:messages-navigation-result', (event) => {
      const detail = event.detail || {};
      const card = detail.message_id
        ? q(`.desktop-messages-card[data-message-id="${CSS.escape(detail.message_id)}"]`)
        : selectedCard;
      if (detail.status === 'PASS' && detail.inspector_open === true) {
        showToast('已定位到对应任务与流程节点。');
        return;
      }
      if (detail.status === 'TARGET_UNAVAILABLE') {
        if (card) {
          card.dataset.targetValid = 'false';
          card.classList.remove('protected');
          renderCardAction(card);
          updateReadButton();
          updateBatch();
        }
        showToast('对应任务已删除；该消息现在可使用一键已读。');
        return;
      }
      showToast('已找到任务，但未能完整打开对应流程节点，请重试。');
    });

    const rightToggle = q('#desktop-toggle-right');
    rightToggle.addEventListener('click', (event) => {
      if (!selectedCard && preview.app.dataset.right !== 'true') {
        event.preventDefault();
        event.stopImmediatePropagation();
        q('#desktop-live-state').textContent = '请先选择一条消息';
      }
    }, true);
    rightToggle.addEventListener('click', () => {
      if (selectedCard && preview.app.dataset.right !== 'true') closeDetail({ restoreFocus:true });
    });
    root.addEventListener('keydown', (event) => {
      if (event.ctrlKey && event.altKey && event.code === 'KeyB' && !selectedCard && preview.app.dataset.right !== 'true') {
        event.preventDefault();
        event.stopImmediatePropagation();
        q('#desktop-live-state').textContent = '请先选择一条消息';
      }
    }, true);
    root.addEventListener('keydown', (event) => {
      if (event.ctrlKey && event.altKey && event.code === 'KeyB' && selectedCard && preview.app.dataset.right !== 'true') {
        closeDetail({ restoreFocus:true });
      }
    });
    applyMode();
    // Language switching updates existing loaded nodes without reads, state writes or detail reopening.
    const projectLoaded=()=>{
      for(const card of cards()){
        const original=contentRows.get(card);
        if(!original)continue;
        const row=Memorive.contentLanguage.project(original);
        card.dataset.title=row.title;
        card.dataset.summary=row.summary||row.body||'';
        const title=card.querySelector('.desktop-messages-copy strong'),
          summary=card.querySelector('.desktop-messages-copy small');
        title.textContent=card.dataset.title;
        title.title=card.dataset.title;
        summary.textContent=card.dataset.summary;
        const lang=document.documentElement.lang;
        card.querySelector('.desktop-messages-check')?.setAttribute('aria-label',({'zh-CN':'选择消息：','en-US':'Select message: ','ja-JP':'メッセージを選択：'}[lang]||'')+row.title);
        if(selectedCard===card){
          q('#messages-title').textContent=row.title;
          q('#messages-title').title=row.title;
          q('#messages-summary').textContent=card.dataset.summary;
        }
      }
    };
    new MutationObserver(projectLoaded).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
    Memorive.messagesView = Object.freeze({
      projectLoaded,
      renderRows,
      openDetailById,
      markCardRead,
      formatMessageTimestamp,
      closeDetail:() => closeDetail({ restoreFocus:false })
    });
  })();

