/* Local behaviour of the inbox page that needs no service call: selecting cards, the batch bar, the filter menus and
   the item preview area. bridge.js fills the page with the service's data. It still holds the design prototype's
   sample preview texts and import progress (previewFor, the import timer); bridge.js writes the real preview. */
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
    const undo = q('#inbox-undo');
    const redo = q('#inbox-redo');
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
    let undoStack = [];
    let redoStack = [];
    let contextCard = null;
    let importTimer = 0;
    let importSequence = 0;
    let toastTimer = 0;
    let toastFadeTimer = 0;
    let toastVersion = 0;
    const toastReducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)') || { matches:false };
    const clearToastTimers = () => {
      window.clearTimeout(toastTimer);
      window.clearTimeout(toastFadeTimer);
      toastTimer = 0;
      toastFadeTimer = 0;
    };
    const hideToast = ({ animate = true } = {}) => {
      const toast = q('#inbox-toast');
      clearToastTimers();
      const version = ++toastVersion;
      if (toast.hidden) return;
      if (!animate || toastReducedMotion.matches) {
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
    const showToast = (copy, canUndo = false) => {
      const toast = q('#inbox-toast');
      clearToastTimers();
      const version = ++toastVersion;
      q('#inbox-toast-copy').textContent = copy;
      q('#inbox-toast-undo').hidden = !canUndo;
      toast.classList.remove('is-fading');
      toast.hidden = false;
      toastTimer = window.setTimeout(() => {
        if (version === toastVersion) hideToast({ animate:true });
      }, 5000);
    };
    const updateHistoryButtons = () => {
      undo.disabled = undoStack.length === 0;
      redo.disabled = redoStack.length === 0;
    };
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
    const previewFor = (card) => {
      const title = card.dataset.title;
      const kind = card.dataset.kind;
      if (kind === 'office') return '<h5>Office 文件属性</h5><p>工作簿正文不在收件箱内渲染。</p><div class="desktop-inbox-preview-code">文件类型  Microsoft Excel 工作簿\n文件大小  14.6 MB\n工作表数  8\n文件状态  '+card.dataset.state+'</div>';
      if (kind === 'presentation') return '<h5>Office 文件属性</h5><p>演示文稿正文不在收件箱内渲染。</p><div class="desktop-inbox-preview-code">文件类型  Microsoft PowerPoint 演示文稿\n文件大小  9.2 MB\n幻灯片数  36\n文件状态  '+card.dataset.state+'</div>';
      if (kind === 'pdf') return '<h5>PDF 只读预览</h5><div class="desktop-inbox-preview-page">第 1 页缩略视图 · 共 31 页</div><p>这里只展示安全缩略内容，不编辑原文件。</p>';
      if (kind === 'image') return '<h5>图像属性</h5><div class="desktop-inbox-preview-error">属性读取失败。文件保留在收件箱，等待用户处理。</div>';
      return '<h5>文本只读预览</h5><div class="desktop-inbox-preview-code"># '+title.replace(/[<>]/g,'')+'\n\n完整内容由桌面服务从受控副本读取。</div>';
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
      q('#inbox-detail-created').textContent = card.dataset.created || '—';
      q('#inbox-detail-modified').textContent = card.dataset.modified || '—';
      q('#inbox-detail-path').textContent = card.dataset.path || '—';
      q('#inbox-detail-id').textContent = 'asset_' + Math.abs([...card.dataset.title].reduce((sum,char) => ((sum * 31) + char.charCodeAt(0)) | 0, 17)).toString(16).toUpperCase().padStart(8,'0');
      q('#inbox-preview-heading').textContent = ['office','presentation'].includes(card.dataset.kind) ? '文件属性' : '只读查看';
      q('#inbox-preview').innerHTML = previewFor(card);
      preview.setInspector(true);
    };
    const rowWeight = (row) => {
      if (row.classList.contains('new-file')) return 2;
      if (row.dataset.state === '处理中') return 0;
      if (row.dataset.state === '异常') return 5;
      if (row.classList.contains('starred')) return 1;
      return 3;
    };
    const applyStableSort = () => {
      const rows=[...list.children].map((row,index)=>({row,index}));
      rows.sort((a,b)=>rowWeight(a.row)-rowWeight(b.row)||a.index-b.index);
      rows.forEach(({row})=>list.appendChild(row));
    };
    const performRemove = (removeCards, skipped = 0, label = '已移除') => {
      const entries = removeCards.map((card) => ({card,index:[...list.children].indexOf(card)}));
      entries.forEach(({card}) => card.remove());
      if (entries.length) {
        undoStack.push({entries,label});
        redoStack=[];
      }
      closeDetail();
      refreshCount();
      updateHistoryButtons();
      updateBulkActions();
      showToast(label + ' ' + entries.length + ' 项' + (skipped ? ' · 跳过 ' + skipped + ' 项' : '') + '，可在本次会话撤销。', entries.length > 0);
    };
    const restoreAction = () => {
      const action=undoStack.pop();
      if (!action) return;
      action.entries.sort((a,b)=>a.index-b.index).forEach(({card,index}) => list.insertBefore(card,list.children[index] || null));
      redoStack.push(action);
      refreshCount();
      updateHistoryButtons();
      showToast('已撤销“'+action.label+'”，恢复 '+action.entries.length+' 项。', false);
    };
    const redoAction = () => {
      const action=redoStack.pop();
      if (!action) return;
      action.entries.forEach(({card}) => card.remove());
      undoStack.push(action);
      refreshCount();
      updateHistoryButtons();
      showToast('已重做“'+action.label+'”，再次移除 '+action.entries.length+' 项。', true);
    };
    list.addEventListener('click', (event) => {
      const card = event.target.closest('.desktop-inbox-card');
      if (!card || card.classList.contains('new-file')) return;
      const star = event.target.closest('.desktop-inbox-star');
      if (star) {
        event.stopPropagation();
        if (star.disabled) return;
        const next = star.getAttribute('aria-pressed') !== 'true';
        star.setAttribute('aria-pressed', String(next));
        star.textContent = next ? '★' : '☆';
        star.setAttribute('aria-label',next?'取消星标':'添加星标');
        card.classList.toggle('starred', next);
        applyStableSort();
        return;
      }
      const remove = event.target.closest('.desktop-inbox-delete');
      if (remove) {
        event.stopPropagation();
        performRemove([card],0,'已移除“'+card.dataset.title+'”');
        return;
      }
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
      if (action.dataset.openAction==='preview') selectCard(contextCard); else showToast(action.dataset.openAction==='folder'?'浏览器测试模式未连接桌面文件管理器。':'浏览器测试模式未连接系统默认程序。');
    });
    undo.addEventListener('click', restoreAction);
    q('#inbox-toast-undo').addEventListener('click', restoreAction);
    redo.addEventListener('click', redoAction);
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
    q('#inbox-star-selected').addEventListener('click', () => {
      checks().filter((box)=>box.checked&&!box.disabled).forEach((box)=>{
        const card=box.closest('.desktop-inbox-card');
        const star=card.querySelector('.desktop-inbox-star');
        card.classList.add('starred');
        star.setAttribute('aria-pressed','true');
        star.textContent='★';
      });
      applyStableSort();
      showToast('已为当前选择加星；浏览器测试状态已更新。');
    });
    q('#inbox-unstar-selected').addEventListener('click', () => {
      checks().filter((box)=>box.checked&&!box.disabled).forEach((box)=>{
        const card=box.closest('.desktop-inbox-card');
        const star=card.querySelector('.desktop-inbox-star');
        card.classList.remove('starred');
        star.setAttribute('aria-pressed','false');
        star.textContent='☆';
      });
      applyStableSort();
      showToast('已取消当前选择的星标。');
    });
    q('#inbox-remove-selected').addEventListener('click', () => {
      const selected=checks().filter((box)=>box.checked&&!box.disabled).map((box)=>box.closest('.desktop-inbox-card'));
      const skipped=selected.filter((card)=>card.dataset.raceSkip==='true');
      skipped.forEach((card)=>card.querySelector('.desktop-inbox-check').checked=false);
      performRemove(selected.filter((card)=>card.dataset.raceSkip!=='true'),skipped.length,'已批量移除');
    });
    q('#inbox-auto').addEventListener('click', () => {
      if (window.pywebview && window.pywebview.api) return;
      const button=q('#inbox-auto');
      const on=button.getAttribute('aria-checked')!=='true';
      button.setAttribute('aria-checked',String(on));
      q('#inbox-auto-warning').hidden=!on;
      showToast(on?'浏览器预览已开启自动执行演示。':'浏览器预览已关闭自动执行演示。');
    });
    q('#inbox-add').addEventListener('click', () => {
      const ghost=q('.importing-progress');
      clearInterval(importTimer);
      ghost.hidden=false;
      let progress=0;
      ghost.querySelector('.desktop-inbox-progress-copy').textContent='0%';
      ghost.querySelector('.desktop-inbox-progress i').style.width='0%';
      refreshCount();
      showToast('浏览器测试文件已选择；桌面程序中将创建真实受控副本。');
      importTimer=setInterval(()=>{
        progress+=20;
        ghost.querySelector('.desktop-inbox-progress-copy').textContent=progress+'%';
        ghost.querySelector('.desktop-inbox-progress i').style.width=progress+'%';
        if(progress>=100){
          clearInterval(importTimer);
          importTimer=0;
          importSequence+=1;
          const suffix=importSequence===1?'':' '+importSequence;
          const title='新导入的研究笔记'+suffix+'.md';
          const card=document.createElement('article');
          card.className='desktop-inbox-card';
          card.tabIndex=0;
          card.setAttribute('aria-selected','false');
          Object.assign(card.dataset,{kind:'text',state:'已排队',title,meta:'Markdown · 12 KB',created:'2026-08-23 15:04',modified:'2026-08-23 15:04',path:'受控收件箱 / imported-research-note-'+importSequence+'.md'});
          card.innerHTML='<div class="desktop-inbox-starcell"><input class="desktop-inbox-check" type="checkbox" aria-label="选择 '+title+'" hidden><button class="desktop-inbox-star" type="button" aria-pressed="false" aria-label="添加星标">☆</button></div><div><strong>'+title+'</strong><small>Markdown · 12 KB</small></div><div class="desktop-inbox-state">已排队</div><div class="desktop-inbox-deletecell"><button class="desktop-inbox-delete" type="button" aria-label="从收件箱移除">×</button></div>';
          card.querySelector('.desktop-inbox-check').hidden=!list.classList.contains('bulk');
          list.appendChild(card);
          ghost.hidden=true;
          ghost.querySelector('.desktop-inbox-progress-copy').textContent='0%';
          ghost.querySelector('.desktop-inbox-progress i').style.width='0%';
          applyStableSort();
          refreshCount();
          updateBulkActions();
          showToast('导入完成：新文件已进入“已排队”。');
        }
      },320);
    });
    q('#inbox-refresh').addEventListener('click', () => {
      const button=q('#inbox-refresh');
      if(button.disabled)return;
      button.disabled=true;
      button.classList.add('refreshing');
      setTimeout(()=>{
        button.disabled=false;
        button.classList.remove('refreshing');
        applyStableSort();
        refreshCount();
        showToast('收件箱快照已刷新；运行中的输入快照未改变。');
      },850);
    });
    q('#inbox-close-detail').addEventListener('click', closeDetail);
    q('#inbox-locate').addEventListener('click', () => showToast('浏览器测试模式未连接桌面文件管理器。'));
    q('#inbox-external').addEventListener('click', () => showToast('浏览器测试模式未连接系统默认程序。'));
    q('#inbox-body').addEventListener('dragenter', (event) => {
      event.preventDefault();
      q('#inbox-drop').hidden=false;
    });
    q('#inbox-body').addEventListener('dragover', (event) => event.preventDefault());
    q('#inbox-body').addEventListener('dragleave', (event) => { if(!q('#inbox-body').contains(event.relatedTarget))q('#inbox-drop').hidden=true; });
    q('#inbox-body').addEventListener('drop', (event) => {
      event.preventDefault();
      q('#inbox-drop').hidden=true;
      if(!(window.pywebview && window.pywebview.api))q('#inbox-add').click();
    });
    root.addEventListener('click', (event) => { if(!event.target.closest('#inbox-context'))q('#inbox-context').hidden=true; });
    root.addEventListener('keydown', (event) => {
      if(event.key==='Escape')q('#inbox-context').hidden=true;
      if(event.ctrlKey&&event.key.toLowerCase()==='z'){
        event.preventDefault();
        restoreAction();
      }
      if(event.ctrlKey&&event.key.toLowerCase()==='y'){
        event.preventDefault();
        redoAction();
      }
    });
    applyStableSort();
    refreshCount();
    updateHistoryButtons();
    updateBulkActions();
  })();

