/* Local behaviour of the inbox page that needs no service call: selecting cards and opening their details, the batch
   bar and its check boxes, the context menu and the drop target. bridge.js fills the page with the service's data and
   handles every button that changes something (adding, removing, stars, undo, refresh, automatic processing, opening
   a file); it catches those clicks first. */
  (() => {
    const root = document.getElementById('desktop-inbox-preview');
    const preview = Memorive.frames.inbox;
    if (!root || !preview) return;
    const q = (selector) => root.querySelector(selector);
    const qa = (selector) => [...root.querySelectorAll(selector)];
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let batchbarTransitionVersion = 0;
    const setBatchbarOpen = (bar, open) => {
      const version = ++batchbarTransitionVersion;
      bar.classList.remove('desktop-batch-entering', 'desktop-batch-leaving');
      if (open) {
        bar.hidden = false;
        if (reduceMotion.matches) return;
        void bar.offsetWidth;
        bar.classList.add('desktop-batch-entering');
        window.setTimeout(() => { if (version === batchbarTransitionVersion) bar.classList.remove('desktop-batch-entering'); }, 210);
        return;
      }
      if (bar.hidden) return;
      if (reduceMotion.matches) {
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
    const list = q('#inbox-list');
    const checks = () => qa('.desktop-inbox-check');
    const cards = () => qa('.desktop-inbox-card:not(.new-file)');
    // UI draft only. No task payload, persistence, model call or scheduler binding yet.
    const logicDrafts = new Map();
    const logicOption = q('#inbox-logic-analysis-option');
    const logicCheck = q('#inbox-logic-analysis');
    let logicDraftKey = null;
    const selectLogicDraft = (card) => {
      logicDraftKey = card && card.dataset.itemKind !== 'SESSION_REFINEMENT'
        ? (card.dataset.itemId || card.dataset.path || null) : null;
      logicOption.hidden = !logicDraftKey;
      logicCheck.checked = Boolean(logicDraftKey && logicDrafts.get(logicDraftKey));
    };
    logicCheck.addEventListener('change', () => {
      if (!logicDraftKey) {
        logicCheck.checked = false;
        return;
      }
      logicDrafts.set(logicDraftKey, logicCheck.checked);
    });
    new MutationObserver(() => selectLogicDraft(q('.desktop-inbox-card[aria-selected="true"]')))
      .observe(list, { childList:true, subtree:true, attributes:true,
        attributeFilter:['aria-selected','data-item-id','data-item-kind'] });
    let contextCard = null;
    const refreshCount = () => {
      const counts = { queued:0, processing:0, error:0, importing:qa('.desktop-inbox-card.new-file:not([hidden])').length };
      cards().forEach((card) => {
        if (card.dataset.state === '已排队') counts.queued += 1; else if (card.dataset.state === '处理中') counts.processing += 1; else if (card.dataset.state === '异常') counts.error += 1;
      });
      q('#inbox-empty').hidden = cards().length > 0 || counts.importing > 0;
      const queueCount = q('#inbox-queued');
      const summary = `已排队 ${counts.queued} · 处理中 ${counts.processing} · 异常 ${counts.error} · 导入中 ${counts.importing}`;
      queueCount.textContent = counts.queued;
      queueCount.setAttribute('aria-label', `已排队 ${counts.queued} 项`);
      queueCount.dataset.desktopTip = summary;
    };
    const updateBulkActions = () => {
      const selected = checks().filter((box) => box.checked && !box.disabled);
      q('#inbox-selection').textContent = ' · 已选 ' + selected.length + ' 项';
      [q('#inbox-clear-selection'),q('#inbox-star-selected'),q('#inbox-unstar-selected'),q('#inbox-remove-selected')].forEach((button) => button.disabled = selected.length === 0);
    };
    const closeDetail = () => {
      cards().forEach((card) => card.setAttribute('aria-selected','false'));
      selectLogicDraft(null);
      q('#inbox-locate').hidden=false;
      q('#inbox-external').hidden=false;
      q('#inbox-open-session').hidden=true;
      q('#inbox-properties-heading').textContent='文件属性';
      preview.setInspector(false);
    };
    const selectCard = (card) => {
      if (card.classList.contains('new-file')) return;
      if (card.getAttribute('aria-selected') === 'true' && preview.app.dataset.right === 'true') {
        // SAME_INBOX_CARD_ALREADY_OPEN: keep the current preview and scroll position.
        return;
      }
      cards().forEach((item) => item.setAttribute('aria-selected', String(item === card)));
      selectLogicDraft(card);
      if (card.dataset.itemKind === 'SESSION_REFINEMENT') {
        q('#inbox-detail-title').textContent = card.dataset.title;
        q('#inbox-properties-heading').textContent = '任务属性';
        q('#inbox-preview-heading').textContent = '任务说明';
        q('#inbox-preview').textContent = '正在读取精炼任务状态……';
        q('#inbox-locate').hidden = true;
        q('#inbox-external').hidden = true;
        q('#inbox-open-session').hidden = false;
        preview.setInspector(true);
        return;
      }
      q('#inbox-properties-heading').textContent = '文件属性';
      q('#inbox-locate').hidden = false;
      q('#inbox-external').hidden = false;
      q('#inbox-open-session').hidden = true;
      q('#inbox-detail-title').textContent = card.dataset.title;
      const kindLabel = {text:'文本 / Markdown',pdf:'PDF 文档',office:'Microsoft Excel 工作簿',presentation:'Microsoft PowerPoint 演示文稿',image:'图像文件'}[card.dataset.kind] || '其他文件';
      q('#inbox-detail-type').textContent = kindLabel;
      q('#inbox-detail-size').textContent = (card.dataset.meta || '—').split(' · ').pop();
      q('#inbox-detail-state').textContent = card.dataset.state;
      q('#inbox-detail-created').textContent = Memorive.i18n.dateTime(card.dataset.created);
      q('#inbox-detail-modified').textContent = Memorive.i18n.dateTime(card.dataset.modified);
      q('#inbox-detail-path').textContent = card.dataset.path || '—';
      q('#inbox-preview-heading').textContent = ['office','presentation'].includes(card.dataset.kind) ? '文件属性' : '只读查看';
      preview.setInspector(true);
    };
    list.addEventListener('click', (event) => {
      const card = event.target.closest('.desktop-inbox-card');
      if (!card || card.classList.contains('new-file')) return;
      if (event.target.closest('.desktop-inbox-check')) {
        updateBulkActions();
        return;
      }
      if (!list.classList.contains('bulk')) selectCard(card);
    });
    list.addEventListener('change', (event) => { if (event.target.matches('.desktop-inbox-check')) updateBulkActions(); });
    list.addEventListener('contextmenu', (event) => {
      const card=event.target.closest('.desktop-inbox-card:not(.new-file)');
      if (!card) return;
      event.preventDefault();
      if(card.dataset.itemKind==='SESSION_REFINEMENT'){
        q('#inbox-context').hidden=true;
        return;
      }
      contextCard=card;
      const menu=q('#inbox-context');
      menu.style.left=Math.min(event.clientX,window.innerWidth-220)+'px';
      menu.style.top=Math.min(event.clientY,window.innerHeight-130)+'px';
      menu.hidden=false;
    });
    q('#inbox-context').addEventListener('click', (event) => {
      const action=event.target.closest('[data-open-action]');
      if (!action || !contextCard) return;
      q('#inbox-context').hidden=true;
      if (action.dataset.openAction==='preview') selectCard(contextCard);
    });
    q('#inbox-bulk').addEventListener('click', () => {
      const button=q('#inbox-bulk');
      const on=button.getAttribute('aria-pressed')!=='true';
      button.setAttribute('aria-pressed',String(on));
      button.textContent=on?'退出批量':'批量管理';
      setBatchbarOpen(q('#inbox-batchbar'),on);
      list.classList.toggle('bulk',on);
      closeDetail();
      checks().forEach((box)=>{
        box.hidden=!on;
        if(!on) box.checked=false;
      });
      updateBulkActions();
    });
    q('#inbox-select-all').addEventListener('click', () => {
      checks().filter((box)=>!box.disabled).forEach((box)=>box.checked=true);
      updateBulkActions();
    });
    q('#inbox-clear-selection').addEventListener('click', () => {
      checks().forEach((box)=>box.checked=false);
      updateBulkActions();
    });
    q('#inbox-close-detail').addEventListener('click', closeDetail);
    q('#inbox-body').addEventListener('dragenter', (event) => {
      event.preventDefault();
      q('#inbox-drop').hidden=false;
    });
    q('#inbox-body').addEventListener('dragover', (event) => event.preventDefault());
    q('#inbox-body').addEventListener('dragleave', (event) => { if(!q('#inbox-body').contains(event.relatedTarget))q('#inbox-drop').hidden=true; });
    q('#inbox-body').addEventListener('drop', (event) => {
      event.preventDefault();
      q('#inbox-drop').hidden=true;
    });
    root.addEventListener('click', (event) => { if(!event.target.closest('#inbox-context'))q('#inbox-context').hidden=true; });
    root.addEventListener('keydown', (event) => {
      if(event.key==='Escape')q('#inbox-context').hidden=true;
    });
    refreshCount();
    updateBulkActions();
  })();

