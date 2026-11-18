/* Draws the library page from what bridge.js loaded: the paper cards, the filters and the selected paper's files.
   Publishes Memorive.libraryView for bridge.js. */
  (() => {
    const root = document.getElementById('desktop-library-preview');
    const preview = Memorive.frames.library;
    if (!root || !preview) return;
    const q = (selector) => root.querySelector(selector);
    const qa = (selector) => [...root.querySelectorAll(selector)];
    let mode = 'literature';
    let activeFilter = 'all';
    let pageInfo = null;
    const getQuery = () => ({view:mode, status_filter:mode === 'literature' ? activeFilter : 'all', kind_filter:mode === 'memorive' ? activeFilter : 'all'});
    const queryChanged = () => root.dispatchEvent(new CustomEvent('desktop:library-query'));
    const cards = () => qa('.desktop-library-card');
    const options = {
      literature: [
        ['all', '全部', '全部状态'],
        ['pending', '未处理', '未处理'],
        ['complete', '已完成', '已完成'],
        ['error', '异常', '异常'],
      ],
      memorive: [
        ['all', '全部', '全部类型'],
        ['daily', '日报', '日报'],
        ['weekly', '周报', '周报'],
        ['monthly', '月报', '月报'],
        ['conversation', '对话分析', '对话分析'],
        ['other', '其他正式文档', '其他正式文档'],
      ],
    };
    const filterButton = q('#library-filter-button');
    const filterMenu = q('#library-filter-menu');
    const toastReducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)') || { matches:false };
    let toastTimer = 0;
    let toastFadeTimer = 0;
    let toastVersion = 0;

    const formatLibraryCardTime = (raw) => {
      const value = String(raw || '').trim();
      const parsed = new Date(value);
      if (!value || Number.isNaN(parsed.getTime())) return value || '—';
      const now = new Date();
      const sameDay = parsed.getFullYear() === now.getFullYear()
        && parsed.getMonth() === now.getMonth()
        && parsed.getDate() === now.getDate();
      const time = new Intl.DateTimeFormat('zh-CN', {
        hour:'2-digit', minute:'2-digit', hour12:false
      }).format(parsed);
      if (sameDay) return `今天 ${time}`;
      return new Intl.DateTimeFormat('zh-CN', {
        month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false
      }).format(parsed).replaceAll('/', '-');
    };

    const libraryStatusTone = (status) => {
      const value = String(status || '');
      if (value === '待复核' || value.includes('待') || value === 'pending') return 'yellow';
      if (value === '复核通过' || value.includes('完成') || value === 'complete') return 'green';
      if (value.includes('异常') || value === 'error') return 'red';
      return 'neutral';
    };

    const clearToastTimers = () => {
      window.clearTimeout(toastTimer);
      window.clearTimeout(toastFadeTimer);
      toastTimer = 0;
      toastFadeTimer = 0;
    };

    const hideToast = ({ animate = true } = {}) => {
      const toast = q('#library-toast');
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

    const showToast = (message) => {
      const toast = q('#library-toast');
      clearToastTimers();
      const version = ++toastVersion;
      q('#library-toast-copy').textContent = message;
      toast.classList.remove('is-fading');
      toast.hidden = false;
      toastTimer = window.setTimeout(() => {
        if (version === toastVersion) hideToast({ animate:true });
      }, 5000);
    };

    const closeFilterMenu = (restoreFocus = false) => {
      filterMenu.hidden = true;
      filterButton.setAttribute('aria-expanded', 'false');
      if (restoreFocus) filterButton.focus();
    };

    const closeDetail = (restoreFocus = true) => {
      const selected = q('.desktop-library-card[aria-selected="true"]');
      cards().forEach((card) => {
        card.setAttribute('aria-selected', 'false');
        card.setAttribute('aria-expanded', 'false');
      });
      preview.setInspector(false);
      if (restoreFocus && selected && !selected.hidden) requestAnimationFrame(() => selected.focus({ preventScroll:true }));
    };

    const renderFilterMenu = () => {
      filterMenu.replaceChildren();
      filterMenu.setAttribute('aria-label', mode === 'literature' ? '文献状态' : 'Memorive 文档类型');
      options[mode].forEach(([value, menuLabel]) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.setAttribute('role', 'option');
        button.setAttribute('aria-selected', String(value === activeFilter));
        button.dataset.filter = value;
        button.textContent = menuLabel;
        filterMenu.append(button);
      });
      const selectedOption = options[mode].find(([value]) => value === activeFilter) || options[mode][0];
      q('#library-filter-label').textContent = selectedOption[2];
    };

    const applyFilters = () => {
      let visible = 0;
      cards().forEach((card) => {
        const show = card.dataset.mode === mode && (pageInfo || activeFilter === 'all' || card.dataset.filter === activeFilter);
        card.hidden = !show;
        if (show) visible += 1;
      });
      q('#library-count').textContent = pageInfo ? `${visible} / ${pageInfo.totalCount}` : String(visible);
      q('#library-empty').hidden = visible > 0;
      q('#library-empty-copy').textContent = mode === 'literature' ? '最近 30 天没有文献活动' : '最近 30 天没有 Memorive 正式文档';
      const selected = q('.desktop-library-card[aria-selected="true"]');
      if (selected?.hidden) closeDetail(false);
    };

    const setLibraryMode = (nextMode) => {
      if (!Object.prototype.hasOwnProperty.call(options, nextMode)) return false;
      const changed = mode !== nextMode;
      mode = nextMode;
      activeFilter = 'all';
      closeFilterMenu();
      closeDetail(false);
      const modeButton = q('#library-mode');
      const targetLabel = mode === 'literature' ? '切换到 Memorive 文档' : '切换到文献';
      modeButton.dataset.mode = mode;
      modeButton.setAttribute('aria-label', targetLabel);
      modeButton.dataset.desktopTip = targetLabel;
      modeButton.removeAttribute('title');
      q('#library-mode-title').setAttribute('aria-label', targetLabel);
      q('#library-mode-title').dataset.desktopTip = targetLabel;
      q('#library-mode-label').textContent = mode === 'literature' ? '文献' : 'Memorive 文档';
      renderFilterMenu();
      applyFilters();
      if (changed) queryChanged();
      return true;
    };

    const renderRows = (rows, info = {}) => {
      pageInfo = info.paginated ? {totalCount:Number(info.totalCount) || 0} : null;
      const list = q('#library-list');
      const elements = (Array.isArray(rows) ? rows : []).map((row) => {
        const card = document.createElement('article');
        card.className = 'desktop-library-card';
        card.tabIndex = 0;
        card.setAttribute('aria-selected', 'false');
        card.setAttribute('aria-expanded', 'false');
        card.dataset.id = String(row.artifact_id || '');
        card.dataset.mode = row.mode === 'memorive' ? 'memorive' : 'literature';
        card.dataset.filter = row.mode === 'memorive'
          ? (row.report_period || (row.kind === '对话分析' ? 'conversation' : 'other'))
          : ({pending:'pending', complete:'complete', error:'error'}[row.status] || 'all');
        card.dataset.type = String(row.kind || '资料');
        card.dataset.title = String(row.display_name || '未命名资料');
        card.dataset.meta = `${row.privacy_class || '—'} · ${row.rights_status || '—'}`;
        card.dataset.time = String(row.updated_at || '—');
        card.dataset.status = String(row.status || '—');
        card.dataset.source = Array.isArray(row.lineage) ? row.lineage.join(' · ') : '—';
        card.dataset.sourceFile = String(
          row.source_file_name || String(row.relative_path || '—').split(/[\\/]/).pop()
        );
        card.dataset.sourceFileExists = String(
          row.source_file_exists === true || (!('source_file_exists' in row) && row.file_exists === true)
        );
        card.dataset.sourceFileDescriptor = JSON.stringify(row.source_file || {});
        card.dataset.fileState = String(row.file_state || '—');
        card.dataset.products = Array.isArray(row.products) ? row.products.join('|') : '';
        card.dataset.productFiles = JSON.stringify(
          Array.isArray(row.product_files) ? row.product_files : []
        );
        card.dataset.libraryFiles = JSON.stringify(
          Array.isArray(row.library_files) ? row.library_files : []
        );
        card.dataset.external = String(row.external_target || '');
        card.dataset.task = String(row.task_id || row.task || '');
        card.dataset.productKind = String(row.product_kind || '');
        card.dataset.researchRunId = String(row.research_run_id || '');
        card.dataset.sourceSessionId = String(row.source_session_id || '');
        card.dataset.sourceSnapshotSha256 = String(row.source_snapshot_sha256 || '');
        card.dataset.draftId = String(row.draft_id || '');
        card.dataset.draftSha256 = String(row.draft_sha256 || '');
        card.dataset.itemCount = String(Number(row.item_count || 0));
        card.dataset.directOpenSupported = String(row.direct_file_open_supported !== false);
        card.dataset.reviewOpened = String(row.review_opened === true);
        card.dataset.reviewConfirmed = String(row.review_confirmed === true);
        card.dataset.reviewFilesValid = String(row.review_files_valid !== false);
        card.dataset.reviewOpenedAt = String(row.review_opened_at || '');
        card.dataset.reviewConfirmedAt = String(row.review_confirmed_at || '');

        const copy = document.createElement('div');
        copy.className = 'desktop-library-card-copy';
        const title = document.createElement('strong');
        title.textContent = card.dataset.title;
        const meta = document.createElement('small');
        meta.textContent = `${card.dataset.type} · ${card.dataset.meta}`;
        copy.append(title, meta);
        const time = document.createElement('time');
        time.dateTime = card.dataset.time;
        time.textContent = formatLibraryCardTime(card.dataset.time);
        time.title = card.dataset.time;
        const badge = document.createElement('span');
        badge.className = 'badge';
        const statusDot = document.createElement('span');
        statusDot.className = `status-dot desktop-library-status-dot ${libraryStatusTone(card.dataset.status)}`;
        statusDot.setAttribute('aria-hidden', 'true');
        badge.append(statusDot, document.createTextNode(card.dataset.status));
        badge.setAttribute('aria-label', `状态：${card.dataset.status}`);
        const arrow = document.createElement('span');
        arrow.textContent = '›';
        card.append(copy, time, badge, arrow);
        return card;
      });
      if (info.append) list.append(...elements); else list.replaceChildren(...elements);
      const hasLiteratureRows = cards().some((card) => card.dataset.mode === 'literature');
      const hasProsRows = cards().some((card) => card.dataset.mode === 'memorive');
      if (!pageInfo && mode === 'literature' && !hasLiteratureRows && hasProsRows) {
        setLibraryMode('memorive');
      } else {
        applyFilters();
      }
      return cards().length;
    };

    const createFileButton = (descriptor) => {
      const value = typeof descriptor === 'string'
        ? { file_name:descriptor, artifact_id:'', role:'product' }
        : (descriptor || {});
      const filename = String(value.file_name || value.filename || '未命名文件');
      const displayName = String(value.display_name || filename);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'desktop-library-file-action';
      if (value.divider_before === true) button.classList.add('is-support-boundary');
      button.textContent = `${displayName} ↗`;
      button.title = `${filename} · 在文件资源管理器中显示`;
      button.setAttribute('aria-label', `${displayName}，在文件资源管理器中显示`);
      button.dataset.filename = filename;
      button.dataset.artifactId = String(value.artifact_id || '');
      button.dataset.fileRole = String(value.role || 'product');
      if(value.literature_review){
        const group=document.createElement('span');
        group.className='paper-review-library-report';
        group.append(button);
        const badge=Memorive.review?.reportButton(value.literature_review);
        if(badge)group.append(badge);
        return group;
      }
      return button;
    };

    const openDetail = (card) => {
      if (card.getAttribute('aria-selected') === 'true' && preview.app.dataset.right === 'true') {
        // SAME_LIBRARY_ITEM_ALREADY_OPEN: keep the existing inspector.
        return;
      }
      const inspectorWasOpen = preview.app.dataset.right === 'true';
      cards().forEach((item) => {
        const current = item === card;
        item.setAttribute('aria-selected', String(current));
        item.setAttribute('aria-expanded', String(current));
      });
      q('#library-detail-type').textContent = card.dataset.type;
      q('#library-detail-title').textContent = card.dataset.title;
      q('#library-detail-meta').textContent = card.dataset.meta;
      q('#library-detail-time').textContent = card.dataset.time;
      q('#library-detail-status').textContent = card.dataset.status;
      q('#library-detail-id').textContent = card.dataset.id;
      q('#library-detail-source').textContent = card.dataset.source;
      const isRefinement = card.dataset.productKind === 'SESSION_REFINEMENT';
      const isPaper = card.dataset.productKind === 'Core_ARTIFACT';
      const isResearch = card.dataset.productKind === 'RESEARCH_RESULT';
      let researchSection = q('#library-research-result');
      if (!researchSection) {
        researchSection = document.createElement('section');
        researchSection.id='library-research-result';
          researchSection.className=q('#library-files-section').className;
        q('#library-files-section').before(researchSection);
      }
      researchSection.hidden = !isResearch;
      if (isResearch) Memorive.research?.renderResult(card.dataset.researchRunId,researchSection)
        .catch(()=>{researchSection.textContent='资料读取失败，请刷新后重试';});

      const sourceContainer = q('#library-source-file');
      sourceContainer.replaceChildren();
      let sourceDescriptor = {};
      try {
        const parsed = JSON.parse(card.dataset.sourceFileDescriptor || '{}');
        if (parsed && typeof parsed === 'object') sourceDescriptor = parsed;
      } catch (_error) {
        sourceDescriptor = {};
      }
      if (!isRefinement && card.dataset.sourceFileExists === 'true') {
        sourceContainer.append(createFileButton({
          file_name:sourceDescriptor.file_name || card.dataset.sourceFile,
          display_name:sourceDescriptor.display_name || card.dataset.sourceFile,
          artifact_id:sourceDescriptor.artifact_id || card.dataset.id,
          role:'source',
        }));
      } else if (!isRefinement) {
        const missing = document.createElement('div');
        missing.className = 'desktop-library-file-missing';
        const filename = document.createElement('span');
        filename.textContent = card.dataset.sourceFile;
        const state = document.createElement('strong');
        state.textContent = card.dataset.fileState || '文件不存在';
        missing.append(filename, state);
        sourceContainer.append(missing);
      }

      const productContainer = q('#library-products');
      let productFiles = [];
      try {
        const parsed = JSON.parse(card.dataset.libraryFiles || '[]');
        if (Array.isArray(parsed)) productFiles = parsed;
      } catch (_error) {
        productFiles = [];
      }
      if (!productFiles.length) {
        try {
          const parsed = JSON.parse(card.dataset.productFiles || '[]');
          if (Array.isArray(parsed)) productFiles = parsed;
        } catch (_error) {
          productFiles = [];
        }
      }
      if (!productFiles.length) {
        productFiles = (card.dataset.products || '').split('|').filter(Boolean).map(
          (fileName) => ({ file_name:fileName, artifact_id:card.dataset.id, role:'product' })
        );
      }
      productContainer.replaceChildren(...productFiles.map((product) => createFileButton({
        ...product,
        role:'product'
      }))); 
      q('#library-products-empty').hidden = productFiles.length > 0;
      q('#library-files-section').hidden = isRefinement || isResearch;

      const refinementSection = q('#library-refinement-section');
      const reviewRow=q('#library-refinement-confirm').closest('.desktop-library-review-row');
      if(reviewRow.parentElement===refinementSection)refinementSection.after(reviewRow);
      reviewRow.hidden=!(isRefinement||isPaper);
      refinementSection.hidden = !isRefinement;
      if (isRefinement) {
        q('#library-go-session').textContent = card.dataset.sourceSessionId || '—';
        q('#library-go-session').disabled = !card.dataset.sourceSessionId;
        q('#library-refinement-items').textContent = `${card.dataset.itemCount || '0'} 项`;
        q('#library-refinement-draft-hash').textContent = card.dataset.draftSha256 || '—';
        q('#library-refinement-draft-hash').disabled = !card.dataset.draftSha256;
        q('#library-refinement-draft-hash').title = '打开该校验值绑定的精炼成品';
        q('#library-refinement-source-hash').textContent = card.dataset.sourceSnapshotSha256 || '—';
        q('#library-refinement-source-hash').disabled = !card.dataset.sourceSessionId;
        q('#library-refinement-source-hash').title = '前往该快照绑定的来源会话';
      }
      if (isRefinement || isPaper) {
        const confirmed = card.dataset.reviewConfirmed === 'true';
        const opened = card.dataset.reviewOpened === 'true';
        const confirm = q('#library-refinement-confirm');
        confirm.disabled = !opened || confirmed;
        confirm.textContent = confirmed ? '复核通过' : '确认复核';
        q('#library-refinement-review-help').textContent = confirmed
          ? `已确认 · ${formatLibraryCardTime(card.dataset.reviewConfirmedAt)}`
          : opened
            ? '成品已打开，可以确认复核'
            : isPaper ? (card.dataset.reviewFilesValid==='false'?'文件已变化或不可用，请重新核对当前版本':'请先打开 Card.md 和 Analysis.md') : '请先打开结果';
      }

      const external = q('#library-external');
      external.hidden = isRefinement || !card.dataset.external;
      external.textContent = card.dataset.external ? `${card.dataset.external} ↗` : '';
      external.title = card.dataset.external ? `在 ${card.dataset.external} 中打开` : '';
      q('#library-refinement-locate').hidden = !isRefinement;
      q('#library-refinement-open').hidden = !isRefinement;

      const taskSection = q('#library-task-section');
      taskSection.hidden = !card.dataset.task;
      q('#library-task-copy').textContent = card.dataset.task || '';
      preview.setInspector(true, inspectorWasOpen ? {} : { targetRatio:0.4 });
      requestAnimationFrame(() => q('#library-detail-title').focus({ preventScroll:true }));
    };
    const openDetailById = (artifactId) => {
      const card = cards().find((item) => item.dataset.id === String(artifactId || ''));
      if (!card || card.hidden) return false;
      openDetail(card);
      return true;
    };

    const applyLibraryMode = () => setLibraryMode(
      mode === 'literature' ? 'memorive' : 'literature'
    );
    let libraryTransitionVersion = 0;
    let libraryTransitionTimer = 0;
    // The page title switches the mode too.
    q('#library-mode-title').addEventListener('click', () => q('#library-mode').click());
    q('#library-mode').addEventListener('click', () => {
      const list = q('#library-list');
      const version = ++libraryTransitionVersion;
      window.clearTimeout(libraryTransitionTimer);
      list.classList.remove('is-library-entering', 'is-library-leaving');
      if (toastReducedMotion.matches) {
        applyLibraryMode();
        return;
      }
      void list.offsetWidth;
      list.classList.add('is-library-leaving');
      libraryTransitionTimer = window.setTimeout(() => {
        if (version !== libraryTransitionVersion) return;
        applyLibraryMode();
        list.classList.remove('is-library-leaving');
        void list.offsetWidth;
        list.classList.add('is-library-entering');
        libraryTransitionTimer = window.setTimeout(() => {
          if (version === libraryTransitionVersion) list.classList.remove('is-library-entering');
        }, 180);
      }, 120);
    });

    filterButton.addEventListener('click', (event) => {
      event.stopPropagation();
      const willOpen = filterMenu.hidden;
      filterMenu.hidden = !willOpen;
      filterButton.setAttribute('aria-expanded', String(willOpen));
      if (willOpen) filterMenu.querySelector('[aria-selected="true"]')?.focus();
    });

    filterMenu.addEventListener('click', (event) => {
      event.stopPropagation();
      const option = event.target.closest('[data-filter]');
      if (!option) return;
      activeFilter = option.dataset.filter;
      renderFilterMenu();
      closeFilterMenu(true);
      applyFilters();
      queryChanged();
    });

    q('#library-list').addEventListener('click', (event) => {
      const card = event.target.closest('.desktop-library-card');
      if (card) openDetail(card);
    });

    q('#library-list').addEventListener('keydown', (event) => {
      const card = event.target.closest('.desktop-library-card');
      if (!card || !['Enter', ' '].includes(event.key)) return;
      event.preventDefault();
      openDetail(card);
    });

    q('#library-source-file').addEventListener('click', (event) => {
      const file = event.target.closest('[data-filename]');
      if (file) showToast(`正在定位 ${file.dataset.filename}`);
    });

    q('#library-products').addEventListener('click', (event) => {
      const file = event.target.closest('[data-filename]');
      if (file) showToast(`正在定位 ${file.dataset.filename}`);
    });

    q('#library-external').addEventListener('click', () => showToast('已请求打开'));
    q('#library-go-task').addEventListener('click', () => showToast('正在打开关联任务'));
    q('#library-go-session').addEventListener('click', () => showToast('正在前往来源会话。'));
    q('#library-refinement-locate').addEventListener('click', () => showToast('正在文件管理器中定位精炼结果。'));
    q('#library-refinement-open').addEventListener('click', () => showToast('正在打开精炼结果。'));
    q('#library-close').addEventListener('click', () => closeDetail(true));
    q('#library-refresh').addEventListener('click', () => showToast('已刷新'));
    q('#library-toast button').addEventListener('click', () => hideToast({ animate:true }));
    document.addEventListener('click', (event) => {
      if (!q('.desktop-library-filter-wrap').contains(event.target)) closeFilterMenu();
    });
    root.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !filterMenu.hidden) {
        event.preventDefault();
        closeFilterMenu(true);
      }
    });

    cards().forEach((card) => card.setAttribute('aria-expanded', 'false'));
    renderFilterMenu();
    applyFilters();
    Memorive.libraryView = Object.freeze({
      renderRows,
      getQuery,
      setMode:setLibraryMode,
      openDetailById,
      showToast,
      closeDetail:() => closeDetail(false)
    });
  })();

