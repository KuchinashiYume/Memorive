/* Research opportunities across the processed papers of the library (opportunities.*, research/opportunities/
   runs.py): divergences, different conditions and gaps, each checked against the papers' card facts. They are the
   research Q&A page's second mode: the switch in its header (the library's mode switch button) or a click on its
   title swaps the whole Q&A page for this one (#memo-opportunities in routes/chat/markup.html). The page starts a
   search (never by itself), follows the run, lists what was found, and keeps, dismisses or restores an opportunity,
   or asks about it: that adds its papers to the open project, starts a conversation there with the question in the
   input box (not sent) and goes back to the Q&A mode. Publishes Memorive.opportunities (open, used by the home view
   in routes/inbox/research-home.js). */
(() => {
  'use strict';
  const call = (method, params = {}) => Memorive.service.call(method, params);
  const q = (selector) => document.querySelector(selector);
  const make = (tag, text, cls) => {
    const e = document.createElement(tag);
    if (text != null) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  };
  // An icon button of the window (toolbar-icons.css): the label is its tooltip and accessible name.
  const icon = (label, fn, iconClass, form = 'desktop-toolbar-soft') => {
    const b = make('button', label, 'icon-button desktop-toolbar-icon ' + form + ' ' + iconClass);
    b.type = 'button';
    b.setAttribute('aria-label', label);
    b.dataset.desktopTip = label;
    b.onclick = async () => {
      b.disabled = true;
      try { await fn(); } catch (e) { notify(e); } finally { if (b.isConnected) b.disabled = false; }
    };
    return b;
  };
  const KINDS = { divergence: '分歧', condition_difference: '条件差异', gap: '缺口' };
  const DIMENSIONS = { object: '对象', method: '方法', sample: '样本', metric: '指标', condition: '条件', result: '结果' };
  const COMPARED = { same: '相同', different: '不同', unclear: '不清楚' };
  const FIELDS = { research_question: '研究问题', research_object: '研究对象', method: '方法', comparison_context: '比较条件',
    key_data: '关键数据', key_results: '主要结果', author_conclusion: '作者结论', boundary_conditions: '边界条件',
    limitation: '作者所述局限', outlook: '作者所述展望' };
  const MARKED = { kept: '已保留', asked: '已追问', dismissed: '已忽略' };
  const RUNS = { QUEUED: '等待中', RUNNING: '进行中', SUCCEEDED: '已完成', PARTIAL: '部分完成', FAILED: '未完成', CANCELLED: '已取消', INTERRUPTED: '已中断' };
  const errors = { OPPORTUNITIES_MODEL_REQUIRED: '先在研究问答的设置里选择模型', RESEARCH_ALREADY_RUNNING: '另一项研究任务正在运行，结束后再试',
    OPPORTUNITIES_REVISION_CONFLICT: '列表已更新，请再操作一次', OPPORTUNITY_NOT_FOUND: '这条机会已不存在，列表已刷新',
    OPPORTUNITIES_NO_PAPERS: '资料库里还没有处理完的论文', LIBRARY_SELECTION_STALE: '论文文件已变化，请重新寻找研究机会' };
  let state = null, timer = 0, epoch = 0, focusText = '';
  const page = () => q('#memo-chat-page'), body = () => q('#memo-opportunities-body'), status = () => q('#memo-opportunities-status');
  const shown = () => page()?.dataset.mode === 'opportunities';

  function notify(e) {
    const s = status();
    if (!s) return;
    s.textContent = errors[e?.message] || e?.message || '操作未完成，请查看任务记录后重试';
    s.dataset.error = 'true';
    s.hidden = false;
  }
  // The two modes of the research Q&A page. Leaving the Q&A mode closes its right panel (citations and knowledge).
  function setMode(mode) {
    const opportunities = mode === 'opportunities';
    page().dataset.mode = opportunities ? 'opportunities' : 'qa';
    q('#memo-opportunities').hidden = !opportunities;
    q('#memo-mode-title').textContent = opportunities ? '研究机会' : '研究问答';
    const target = opportunities ? '切换到研究问答' : '切换到研究机会';
    for (const b of [q('#memo-mode'), q('#memo-mode-title')]) {
      b.setAttribute('aria-label', target);
      b.dataset.desktopTip = target;
    }
    q('#memo-mode').dataset.mode = page().dataset.mode;
    if (opportunities) {
      Memorive.chat?.closeEvidence();
      return refresh();
    }
    clearTimeout(timer);
    return Promise.resolve();
  }
  async function open() {
    if (Memorive.app.inspect().active_route !== 'chat') Memorive.app.activate('chat');
    await setMode('opportunities');
  }
  async function refresh() {
    clearTimeout(timer);
    const mine = ++epoch;
    const fresh = await call('opportunities.state');
    if (mine !== epoch || !shown()) return;
    state = fresh;
    render();
    if (state.active_run) timer = setTimeout(() => refresh().catch(notify), 1500);
  }
  // A mark answers with the whole state; a stale revision or a removed opportunity reloads it.
  async function mark(item, next) {
    try {
      state = await call('opportunities.mark', { fingerprint: item.fingerprint, status: next, expected_revision: state.revision });
    } catch (e) {
      if (!['OPPORTUNITIES_REVISION_CONFLICT', 'OPPORTUNITY_NOT_FOUND'].includes(e.message)) throw e;
      await refresh();
      throw e;
    }
    if (shown()) render();
  }
  // Asking: the papers go into the project open in the Q&A mode first (when their files changed, nothing has been
  // created yet), then a conversation there with the opportunity's title and those papers, the question saved as
  // its draft (so the input box shows it), the opportunity marked as asked, and the Q&A mode with the conversation.
  async function ask(item) {
    const project = Memorive.chat?.inspect().project || 'default';
    const files = [...new Map(item.papers.flatMap(p => p.library_files || []).map(f => [f.id, f])).values()];
    const ids = files.length ? (await call('memo.library_add', { project, artifacts: files })).items.map(row => row.id) : [];
    const thread = await call('memo.thread_create', { project, title: item.title });
    if (ids.length) {
      const fresh = await call('memo.thread_get', { thread_id: thread.id });
      await call('memo.thread_configure', { thread_id: thread.id, expected_revision: fresh.revision, changes: { artifact_ids: ids } });
    }
    const draft = await call('memo.answer_draft_get', { thread_id: thread.id, branch_key: 'root' });
    await call('memo.answer_draft_save', { thread_id: thread.id, branch_key: 'root', text: item.question, expected_revision: draft.revision });
    await mark(item, 'asked');
    await setMode('qa');
    await Memorive.chat.openThread(thread.id);
    q('#memo-question')?.focus();
  }
  function render() {
    const target = body(), run = state.active_run || state.runs[0], running = !!state.active_run;
    target.replaceChildren();
    // One row: the optional focus, find, and with a run its task and (while it runs) cancel; then its state.
    const controls = make('section', null, 'desktop-research-opportunity-controls'), row = make('div', null, 'desktop-research-opportunity-search');
    const focus = make('input', null, 'field');
    focus.maxLength = 500;
    focus.placeholder = '关注点（可选）';
    focus.setAttribute('aria-label', '关注点（可选）');
    focus.value = focusText;
    focus.oninput = () => { focusText = focus.value; };
    const unavailable = !state.model ? '先在研究问答的设置里选择模型' : !state.card_count ? '资料库里还没有处理完的论文' : '';
    const find = icon('寻找研究机会', async () => {
      await call('opportunities.find', { request_id: 'opportunities-' + crypto.randomUUID(), ...(focusText.trim() ? { focus: focusText.trim() } : {}) });
      await refresh();
    }, 'desktop-action-auto', 'desktop-toolbar-primary');
    find.disabled = running || !!unavailable;
    focus.onkeydown = (e) => { if (e.key === 'Enter' && !find.disabled) find.click(); };
    row.append(focus, find);
    if (run) {
      row.append(icon('查看任务', () => Memorive.research.openTask(run.run_id), 'desktop-action-current-task'));
      if (running) row.append(icon('取消', async () => {
        await call('opportunities.cancel', { run_id: run.run_id });
        await refresh();
      }, 'desktop-action-reset'));
    }
    const s = make('p', null, 'desktop-research-status');
    s.id = 'memo-opportunities-status';
    s.setAttribute('role', 'status');
    s.textContent = run ? (RUNS[run.status] || run.status) + (run.error_code ? ' · ' + (errors[run.error_code] || '请查看任务记录') : '') : unavailable;
    s.hidden = !s.textContent;
    controls.append(row, s);
    if (state.papers_since_last_run > 0 && state.runs.length) {
      // Separate text nodes, so the label is translated and the number kept.
      const fresh = make('p', null, 'desktop-research-muted');
      fresh.append(make('span', '上次寻找之后新增的论文'), ' · ' + state.papers_since_last_run);
      controls.append(fresh);
    }
    target.append(controls);
    const open = state.items.filter(item => item.status !== 'dismissed'),
      dismissed = state.items.filter(item => item.status === 'dismissed');
    if (!state.items.length) target.append(make('p', running ? '正在寻找…' : '还没有找到研究机会。', 'desktop-research-muted'));
    for (const item of open) target.append(card(item));
    if (dismissed.length) {
      const fold = make('details', null, 'desktop-research-opportunity-dismissed'), summary = make('summary');
      summary.append(make('span', '已忽略'), ' · ' + dismissed.length);
      fold.append(summary, ...dismissed.map(card));
      target.append(fold);
    }
  }
  function section(title, text) {
    const box = make('div', null, 'desktop-research-opportunity-part');
    box.append(make('small', title, 'desktop-research-muted'), make('p', text));
    return box;
  }
  function card(item) {
    const row = make('article', null, 'desktop-research-opportunity');
    row.dataset.status = item.status;
    const head = make('header', null, 'desktop-research-opportunity-head'), tags = make('p', null, 'desktop-research-opportunity-tags');
    tags.append(make('span', KINDS[item.kind] || item.kind, 'desktop-research-opportunity-kind'));
    if (MARKED[item.status]) tags.append(make('span', MARKED[item.status], 'desktop-research-muted'));
    const actions = make('div', null, 'desktop-research-actions');
    if (item.status === 'dismissed') actions.append(icon('恢复', () => mark(item, 'new'), 'desktop-action-restore'));
    else {
      const keep = icon(item.status === 'kept' ? '取消保留' : '保留', () => mark(item, item.status === 'kept' ? 'new' : 'kept'), 'desktop-action-star', 'desktop-toolbar-ghost desktop-toolbar-toggle');
      keep.setAttribute('aria-pressed', String(item.status === 'kept'));
      actions.append(icon(item.status === 'asked' ? '再次追问' : '在研究问答里追问', () => ask(item), 'desktop-action-message', 'desktop-toolbar-primary'),
        keep, icon('忽略', () => mark(item, 'dismissed'), 'desktop-action-archive', 'desktop-toolbar-ghost'));
    }
    head.append(tags, actions);
    row.append(head, make('h4', item.title));
    if (item.outdated) row.append(make('p', '这条依据的论文卡片已经改动，建议重新寻找', 'desktop-research-status'));
    row.append(section('要追问的问题', item.question), section('判断依据', item.reasoning));
    if (item.dimensions.length) {
      const box = make('div', null, 'desktop-research-opportunity-part'), list = make('ul', null, 'desktop-research-opportunity-dimensions');
      for (const d of item.dimensions) {
        const li = make('li');
        li.append(make('span', DIMENSIONS[d.dimension] || d.dimension), make('span', COMPARED[d.status] || d.status, 'desktop-research-opportunity-compared'), make('span', d.note));
        li.dataset.compared = d.status;
        list.append(li);
      }
      box.append(make('small', '逐项比较', 'desktop-research-muted'), list);
      row.append(box);
    }
    row.append(section('建议怎么解决', item.suggestion), section('局限', item.limitations));
    if (item.evidence.length) {
      const fold = make('details', null, 'desktop-research-opportunity-evidence'), summary = make('summary');
      summary.append(make('span', '证据'), ' · ' + item.evidence.length);
      fold.append(summary);
      for (const fact of item.evidence) {
        const box = make('div', null, 'desktop-research-opportunity-fact'), where = make('small', null, 'desktop-research-muted');
        where.append('《' + fact.paper_title + '》 · ', make('span', FIELDS[fact.field] || fact.field));
        if (fact.page) where.append(' · p. ' + fact.page);
        if (fact.section) where.append(' · ' + fact.section);
        box.append(where, make('p', fact.text));
        if (fact.quote && !fact.text.includes(fact.quote)) box.append(make('blockquote', fact.quote));
        fold.append(box);
      }
      row.append(fold);
    }
    return row;
  }
  window.addEventListener('desktop:research-published', () => {
    if (shown()) refresh().catch(notify);
  });
  Memorive.opportunities = Object.freeze({ open, setMode });
  const init = () => {
    if (!page()) return;
    page().dataset.mode = 'qa';
    const toggle = () => setMode(shown() ? 'qa' : 'opportunities').catch(notify);
    q('#memo-mode').addEventListener('click', toggle);
    q('#memo-mode-title').addEventListener('click', toggle);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true }); else init();
})();
