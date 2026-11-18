/* Research opportunities across the processed papers of the library (opportunities.*, research/opportunities/
   runs.py): divergences, different conditions and gaps, each checked against the papers' card facts. The pane opens
   from the library page's "Research opportunities" button and from the home view (research-home.js), in the research
   pane of shared/research.js. It starts a search (never by itself), follows the run, lists what was found, and lets
   the person keep, dismiss or restore an opportunity, or ask about it: that starts a research Q&A conversation with
   the papers added and the question in the input box, not sent. Publishes Memorive.opportunities. */
(() => {
  'use strict';
  const call = (method, params = {}) => Memorive.service.call(method, params);
  const make = (tag, text, cls) => {
    const e = document.createElement(tag);
    if (text != null) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  };
  const button = (text, fn, cls = 'soft-button') => {
    const b = make('button', text, cls);
    b.type = 'button';
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
  let view = null, state = null, timer = 0, epoch = 0, focusText = '', status = null;

  function notify(e) {
    if (!status?.isConnected) return;
    status.textContent = errors[e?.message] || e?.message || '操作未完成，请查看任务记录后重试';
    status.dataset.error = 'true';
    status.hidden = false;
  }
  async function open(origin = null) {
    if (Memorive.app.inspect().active_route !== 'library') {
      Memorive.app.activate('library');
      origin = document.getElementById('library-opportunities');
    }
    view = Memorive.research.openPane('研究机会', origin);
    view.body.append(make('p', '正在读取…', 'desktop-research-muted'));
    await refresh();
  }
  async function refresh() {
    clearTimeout(timer);
    const mine = ++epoch, pane = view?.pane;
    const fresh = await call('opportunities.state');
    if (mine !== epoch || !pane?.isConnected) return;
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
    if (view?.pane.isConnected) render();
  }
  // Asking: a new conversation with the opportunity's title, its papers added, the question saved as the draft (so
  // the input box shows it) and the opportunity marked as asked; the person edits or sends the question.
  async function ask(item) {
    // The papers go into the project first: when their files changed, nothing has been created yet.
    const files = [...new Map(item.papers.flatMap(p => p.library_files || []).map(f => [f.id, f])).values()];
    const ids = files.length ? (await call('memo.library_add', { project: 'default', artifacts: files })).items.map(row => row.id) : [];
    const thread = await call('memo.thread_create', { project: 'default', title: item.title });
    if (ids.length) {
      const fresh = await call('memo.thread_get', { thread_id: thread.id });
      await call('memo.thread_configure', { thread_id: thread.id, expected_revision: fresh.revision, changes: { artifact_ids: ids } });
    }
    const draft = await call('memo.answer_draft_get', { thread_id: thread.id, branch_key: 'root' });
    await call('memo.answer_draft_save', { thread_id: thread.id, branch_key: 'root', text: item.question, expected_revision: draft.revision });
    await mark(item, 'asked');
    Memorive.research.closePane();
    Memorive.app.activate('chat');
    await Memorive.chat.openThread(thread.id);
    document.getElementById('memo-question')?.focus();
  }
  function render() {
    const body = view.body, run = state.active_run || state.runs[0], running = !!state.active_run;
    body.replaceChildren();
    const controls = make('section', null, 'desktop-research-opportunity-controls');
    const label = make('label', null, 'field-label'), focus = make('input', null, 'field');
    label.append(make('span', '关注点（可选）'), focus);
    focus.maxLength = 500;
    focus.value = focusText;
    focus.oninput = () => { focusText = focus.value; };
    const unavailable = !state.model ? '先在研究问答的设置里选择模型' : !state.card_count ? '资料库里还没有处理完的论文' : '';
    const find = button('寻找研究机会', async () => {
      await call('opportunities.find', { request_id: 'opportunities-' + crypto.randomUUID(), ...(focusText.trim() ? { focus: focusText.trim() } : {}) });
      await refresh();
    }, 'primary-button');
    find.disabled = running || !!unavailable;
    const actions = make('div', null, 'desktop-research-actions');
    actions.append(find);
    if (run) {
      actions.append(button('查看任务', () => {
        Memorive.research.closePane();
        return Memorive.research.openTask(run.run_id);
      }));
      if (running) actions.append(button('取消', async () => {
        await call('opportunities.cancel', { run_id: run.run_id });
        await refresh();
      }));
    }
    status = make('p', null, 'desktop-research-status');
    status.setAttribute('role', 'status');
    status.textContent = run ? (RUNS[run.status] || run.status) + (run.error_code ? ' · ' + (errors[run.error_code] || '请查看任务记录') : '') : unavailable;
    status.hidden = !status.textContent;
    controls.append(label, actions, status);
    if (state.papers_since_last_run > 0 && state.runs.length) {
      // Separate text nodes, so the label is translated and the number kept.
      const fresh = make('p', null, 'desktop-research-muted');
      fresh.append(make('span', '上次寻找之后新增的论文'), ' · ' + state.papers_since_last_run);
      controls.append(fresh);
    }
    body.append(controls);
    const open = state.items.filter(item => item.status !== 'dismissed'),
      dismissed = state.items.filter(item => item.status === 'dismissed');
    if (!state.items.length) body.append(make('p', running ? '正在寻找…' : '还没有找到研究机会。', 'desktop-research-muted'));
    for (const item of open) body.append(card(item));
    if (dismissed.length) {
      const fold = make('details', null, 'desktop-research-opportunity-dismissed'), summary = make('summary');
      summary.append(make('span', '已忽略'), ' · ' + dismissed.length);
      fold.append(summary, ...dismissed.map(card));
      body.append(fold);
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
    const tags = make('p', null, 'desktop-research-opportunity-tags');
    tags.append(make('span', KINDS[item.kind] || item.kind, 'desktop-research-opportunity-kind'));
    if (MARKED[item.status]) tags.append(make('span', MARKED[item.status], 'desktop-research-muted'));
    row.append(tags, make('h4', item.title));
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
    const actions = make('div', null, 'desktop-research-actions');
    if (item.status === 'dismissed') actions.append(button('恢复', () => mark(item, 'new')));
    else {
      actions.append(button(item.status === 'asked' ? '再次追问' : '在研究问答里追问', () => ask(item), 'primary-button'));
      actions.append(item.status === 'kept' ? button('取消保留', () => mark(item, 'new')) : button('保留', () => mark(item, 'kept')));
      actions.append(button('忽略', () => mark(item, 'dismissed')));
    }
    row.append(actions);
    return row;
  }
  window.addEventListener('desktop:research-published', () => {
    if (view?.pane.isConnected) refresh().catch(notify);
  });
  Memorive.opportunities = Object.freeze({ open });
  const init = () => {
    const b = document.getElementById('library-opportunities');
    if (b) b.onclick = () => open(b).catch(notify);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true }); else init();
})();
