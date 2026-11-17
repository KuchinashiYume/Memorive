(() => {
  'use strict';
  const root = document.getElementById('desktop-worklog-preview');
  const preview = window.DesktopPreview_worklog;
  if (!root || !preview) return;
  const q = (selector) => root.querySelector(selector);
  const qa = (selector) => [...root.querySelectorAll(selector)];
  const cards = () => qa('.desktop-worklog-card');
  const filterButton = q('#worklog-filter-button');
  const filterPopover = q('#worklog-filter-popover');
  const milestonesByEntry = new Map();
  let appliedTime = '7';
  let appliedLedger = 'all';
  let snapshot = 0;
  let toastTimer = 0;

  const formatActivityTimestamp = (raw) => {
    const parsed = new Date(String(raw || ''));
    if (Number.isNaN(parsed.getTime())) return { date:'日期未知', card:'—', full:'时间未知' };
    const date = new Intl.DateTimeFormat('zh-CN', {
      year:'numeric', month:'2-digit', day:'2-digit'
    }).format(parsed);
    const clock = new Intl.DateTimeFormat('zh-CN', {
      hour:'2-digit', minute:'2-digit', hourCycle:'h23'
    }).format(parsed);
    const full = new Intl.DateTimeFormat('zh-CN', {
      year:'numeric', month:'2-digit', day:'2-digit',
      hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23'
    }).format(parsed);
    return { date, card:date.slice(5) + ' ' + clock, full };
  };

  const showToast = (message) => {
    window.clearTimeout(toastTimer);
    q('#worklog-toast-copy').textContent = message;
    q('#worklog-toast').hidden = false;
    toastTimer = window.setTimeout(() => { q('#worklog-toast').hidden = true; }, 4200);
  };
  const hideToast = () => {
    window.clearTimeout(toastTimer);
    q('#worklog-toast').hidden = true;
  };
  const makeElement = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };

  const closeDetail = (restoreFocus = true) => {
    const selected = q('.desktop-worklog-card[aria-selected="true"]');
    cards().forEach((card) => {
      card.setAttribute('aria-selected', 'false');
      card.setAttribute('aria-expanded', 'false');
    });
    preview.setInspector(false);
    if (restoreFocus && selected && !selected.hidden) {
      requestAnimationFrame(() => selected.focus({ preventScroll:true }));
    }
  };

  const applyFilters = () => {
    let visible = 0;
    cards().forEach((card) => {
      const timeOk = appliedTime === 'all'
        || (appliedTime === '30' && ['today', '7', '30'].includes(card.dataset.age))
        || (appliedTime === '7' && ['today', '7'].includes(card.dataset.age))
        || (appliedTime === 'today' && card.dataset.age === 'today');
      const ledgerOk = appliedLedger === 'all' || card.dataset.ledger === appliedLedger;
      card.hidden = !(timeOk && ledgerOk);
      if (!card.hidden) visible += 1;
    });
    q('#worklog-count').textContent = String(visible);
    q('#worklog-empty').hidden = visible > 0;
    const activeCount = (appliedTime === '7' ? 0 : 1) + (appliedLedger === 'all' ? 0 : 1);
    filterButton.textContent = activeCount ? '筛选 ' + activeCount + ' ▾' : '筛选 ▾';
    if (q('.desktop-worklog-card[aria-selected="true"]')?.hidden) closeDetail(false);
  };

  const renderRows = (rows) => {
    const list = q('#worklog-list');
    list.replaceChildren(...(Array.isArray(rows) ? rows : []).map((row) => {
      const card = document.createElement('article');
      const timestamp = formatActivityTimestamp(row.event_time_utc || row.date || '');
      card.className = 'desktop-worklog-card';
      card.tabIndex = 0;
      card.setAttribute('aria-selected', 'false');
      card.setAttribute('aria-expanded', 'false');
      card.dataset.id = String(row.log_entry_id || '');
      card.dataset.jobId = String(row.job_id || '');
      card.dataset.jobLocator = String(row.job_locator || '');
      card.dataset.artifactId = String(row.related_artifact_id || '');
      card.dataset.artifactLocator = String(row.related_artifact_locator || '');
      card.dataset.type = String(row.type_label || '工作活动');
      card.dataset.title = String(row.title || '未命名活动');
      card.dataset.date = timestamp.date;
      card.dataset.time = timestamp.full;
      card.dataset.age = String(row.age_bucket || 'older');
      card.dataset.ledger = String(row.ledger_type || 'branch');
      card.dataset.parse = String(row.parse_status || 'ready');
      card.dataset.state = String(row.state_axis || '状态未知');
      card.dataset.summary = String(row.summary || '暂无摘要');
      card.dataset.severity = String(row.severity || 'INFO');
      card.dataset.live = String(String(row.log_entry_id || '').startsWith('activity-'));
      const mark = makeElement('span', 'desktop-worklog-mark');
      mark.setAttribute('aria-hidden','true');
      const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');
      icon.setAttribute('viewBox','0 0 24 24');
      icon.setAttribute('class','desktop-icon');
      const use=document.createElementNS('http://www.w3.org/2000/svg','use');
      const type=String(row.type_label||'');
      use.setAttribute('href',/报告|日报|周报|月报/.test(type+' '+String(row.title||''))?'#desktop-icon-calendar-range':
        /外部文献/.test(type)?'#desktop-icon-crosshair':/文献/.test(type)?'#desktop-icon-library':'#desktop-icon-messages');
      icon.append(use);
      mark.append(icon);
      const copy = makeElement('div', 'desktop-worklog-card-copy');
      copy.append(
        makeElement('strong', '', card.dataset.title),
        makeElement('small', row.severity === 'ERROR' ? 'desktop-worklog-warning' : '', card.dataset.state + ' · ' + card.dataset.summary)
      );
      const time = makeElement('time', '', timestamp.card);
      time.dateTime = String(row.event_time_utc || '');
      const arrow = makeElement('span', '', '›');
      card.append(mark, copy, time, arrow);
      return card;
    }));
    applyFilters();
    return cards().length;
  };

  const renderMilestones = (card) => {
    const container = q('#worklog-milestones');
    const milestones = milestonesByEntry.get(card.dataset.id) || [];
    container.replaceChildren();
    if (milestones.length === 0) {
      container.append(makeElement('p', 'desktop-worklog-empty-copy', milestonesByEntry.has(card.dataset.id) ? '暂无处理记录' : '正在读取任务里程碑…'));
      return;
    }
    milestones.forEach((milestone) => {
      const article = makeElement('article', 'desktop-worklog-milestone');
      const head = makeElement('div', 'desktop-worklog-milestone-head');
      head.append(
        makeElement('strong', '', milestone.title || '任务事件'),
        makeElement('span', '', formatActivityTimestamp(milestone.time).full)
      );
      const fields = makeElement('dl', 'desktop-worklog-milestone-fields');
      (Array.isArray(milestone.fields) ? milestone.fields : []).forEach(([label, value]) => {
        const field = makeElement('div');
        field.append(makeElement('dt', '', label), makeElement('dd', '', value));
        fields.append(field);
      });
      article.append(head, fields);
      if (milestone.technical_public) {
        const details = makeElement('details', 'desktop-worklog-tech-details');
        const summary = makeElement('summary', '', '展开技术细节');
        details.append(summary, makeElement('pre', 'desktop-worklog-tech', milestone.technical_public));
        details.addEventListener('toggle', () => {
          summary.textContent = details.open ? '收起技术细节' : '展开技术细节';
        });
        article.append(details);
      }
      container.append(article);
    });
  };

  const setMilestones = (logEntryId, milestones) => {
    milestonesByEntry.set(String(logEntryId || ''), Array.isArray(milestones) ? milestones : []);
    const selector = '.desktop-worklog-card[data-id="' + CSS.escape(String(logEntryId || '')) + '"][aria-selected="true"]';
    const selected = q(selector);
    if (selected) renderMilestones(selected);
  };

  const openDetail = (card) => {
    cards().forEach((item) => {
      const current = item === card;
      item.setAttribute('aria-selected', String(current));
      item.setAttribute('aria-expanded', String(current));
    });
    q('#worklog-detail-type').textContent = card.dataset.type;
    q('#worklog-detail-title').textContent = card.dataset.title;
    q('#worklog-detail-headline').textContent = card.dataset.type + ' · ' + card.dataset.time;
    q('#worklog-detail-id').textContent = card.dataset.id;
    q('#worklog-detail-date').textContent = card.dataset.date;
    q('#worklog-detail-state').textContent = card.dataset.state;
    q('#worklog-detail-summary').textContent = card.dataset.summary;
    q('#worklog-detail-modified').textContent = card.dataset.time;
    const failed = card.dataset.parse === 'failed';
    const partial = card.dataset.parse === 'partial';
    q('#worklog-parse-banner').hidden = !failed && !partial;
    q('#worklog-parse-banner').textContent = failed
      ? '日志读取不完整'
      : partial ? '该活动记录只完成部分解析；缺失内容保持未评估。' : '';
    q('#worklog-summary-section').hidden = false;
    q('#worklog-milestones-section').hidden = failed;
    q('#worklog-receipt-section').hidden = card.dataset.live === 'true' || failed;
    q('#worklog-locate').hidden = !card.dataset.jobLocator;
    q('#worklog-open-current-folder').hidden = !card.dataset.artifactLocator;
    if (!failed) renderMilestones(card);
    preview.setInspector(true);
    requestAnimationFrame(() => q('#worklog-detail-title').focus({ preventScroll:true }));
  };
  const openDetailById = (logEntryId) => {
    const card = cards().find((item) => item.dataset.id === String(logEntryId || ''));
    if (!card || card.hidden) return false;
    openDetail(card);
    return true;
  };

  filterButton.addEventListener('click', (event) => {
    event.stopPropagation();
    filterPopover.hidden = !filterPopover.hidden;
    filterButton.setAttribute('aria-expanded', String(!filterPopover.hidden));
  });
  filterPopover.addEventListener('click', (event) => event.stopPropagation());
  q('#worklog-apply').addEventListener('click', () => {
    appliedTime = q('input[name="worklog-time"]:checked')?.value || '7';
    appliedLedger = q('input[name="worklog-ledger"]:checked')?.value || 'all';
    filterPopover.hidden = true;
    filterButton.setAttribute('aria-expanded', 'false');
    applyFilters();
  });
  q('#worklog-reset').addEventListener('click', () => {
    q('input[name="worklog-time"][value="7"]').checked = true;
    q('input[name="worklog-ledger"][value="all"]').checked = true;
  });
  document.addEventListener('click', (event) => {
    if (!q('.desktop-worklog-filter-wrap').contains(event.target)) {
      filterPopover.hidden = true;
      filterButton.setAttribute('aria-expanded', 'false');
    }
  });
  q('#worklog-list').addEventListener('click', (event) => {
    const card = event.target.closest('.desktop-worklog-card');
    if (card) openDetail(card);
  });
  q('#worklog-list').addEventListener('keydown', (event) => {
    const card = event.target.closest('.desktop-worklog-card');
    if (!card || !['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    openDetail(card);
  });
  q('#worklog-close').addEventListener('click', () => closeDetail(true));
  q('#worklog-locate').addEventListener('click', () => showToast('正在前往对应当前任务。'));
  q('#worklog-open-current-folder').addEventListener('click', () => showToast('正在打开资料库'));
  q('#worklog-open-folder').addEventListener('click', () => showToast('工作日志自动生成'));
  q('#worklog-refresh').addEventListener('click', () => {
    snapshot += 1;
    q('#worklog-snapshot').textContent = '实时活动 · ' + String(snapshot).padStart(3, '0');
    showToast('已刷新');
  });
  q('#worklog-toast button').addEventListener('click', hideToast);

  applyFilters();
  window.__Desktop_WORK_LOG_VIEW__ = Object.freeze({
    renderRows,
    setMilestones,
    openDetailById,
    closeDetail:() => closeDetail(false),
    formatActivityTimestamp
  });
})();

