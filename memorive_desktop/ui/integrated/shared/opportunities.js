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
  const MARKED = { kept: '已星标', asked: '已追问', dismissed: '已忽略' };
  const RUNS = { QUEUED: '等待中', RUNNING: '进行中', SUCCEEDED: '已完成', PARTIAL: '部分完成', FAILED: '未完成', CANCELLED: '已取消', INTERRUPTED: '已中断' };
  const errors = { OPPORTUNITIES_MODEL_REQUIRED: '先在研究问答的设置里选择模型', RESEARCH_ALREADY_RUNNING: '另一项研究任务正在运行，结束后再试',
    OPPORTUNITIES_REVISION_CONFLICT: '列表已更新，请再操作一次', OPPORTUNITY_NOT_FOUND: '这条机会已不存在，列表已刷新',
    OPPORTUNITIES_NO_PAPERS: '资料库里还没有处理完的论文', LIBRARY_SELECTION_STALE: '论文文件已变化，请重新寻找研究机会' };
  // keywords: the focus of the next search, at most MAX_KEYWORDS, sent to the model joined (opportunities.find focus).
  const MAX_KEYWORDS = 5;
  let state = null, timer = 0, epoch = 0, keywords = [];
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
    // One row: the keyword field, find, and with a run its task and (while it runs) cancel; under it the keywords
    // as chips (the composer's attachment chips), then the run's state.
    const controls = make('section', null, 'desktop-research-opportunity-controls'), row = make('div', null, 'desktop-research-opportunity-search');
    const field = make('input', null, 'field'), chips = make('div', null, 'memo-attachments desktop-research-opportunity-keywords');
    field.maxLength = 40;
    field.setAttribute('aria-label', '关注点关键词');
    const add = () => {
      const word = field.value.trim();
      if (word && keywords.length < MAX_KEYWORDS && !keywords.includes(word)) keywords.push(word);
      field.value = '';
      showKeywords();
    };
    const showKeywords = () => {
      chips.replaceChildren(...keywords.map((word) => {
        const chip = make('div', null, 'memo-attachment'), remove = make('button', '×', 'memo-quiet-button');
        remove.type = 'button';
        remove.setAttribute('aria-label', '移除关键词 ' + word);
        remove.onclick = () => {
          keywords = keywords.filter((w) => w !== word);
          showKeywords();
          field.focus();
        };
        chip.append(make('span', word, 'memo-attachment-name'), remove);
        return chip;
      }));
      chips.hidden = !keywords.length;
      const full = keywords.length >= MAX_KEYWORDS;
      field.disabled = full;
      field.placeholder = full ? '最多 5 个关键词' : '关注点关键词，回车添加（最多 5 个）';
    };
    field.onkeydown = (e) => {
      if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); add(); }
      else if (e.key === 'Backspace' && !field.value && keywords.length) { keywords.pop(); showKeywords(); }
    };
    showKeywords();
    const unavailable = !state.model ? '先在研究问答的设置里选择模型' : !state.card_count ? '资料库里还没有处理完的论文' : '';
    const find = icon('寻找研究机会', async () => {
      add();   // a word typed but not yet added counts too
      await call('opportunities.find', { request_id: 'opportunities-' + crypto.randomUUID(), ...(keywords.length ? { focus: keywords.join('；') } : {}) });
      await refresh();
    }, 'desktop-action-auto', 'desktop-toolbar-primary');
    find.disabled = running || !!unavailable;
    row.append(field, find);
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
    controls.append(row, chips, s);
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
      // The star of the window's other lists (the leaderboard's): outlined, filled once starred.
      const keep = icon(item.status === 'kept' ? '取消星标' : '星标', () => mark(item, item.status === 'kept' ? 'new' : 'kept'), 'desktop-research-opportunity-star', 'desktop-toolbar-ghost');
      keep.setAttribute('aria-pressed', String(item.status === 'kept'));
      actions.append(icon(item.status === 'asked' ? '再次追问' : '在研究问答里追问', () => ask(item), 'desktop-action-message', 'desktop-toolbar-primary'),
        keep, icon('忽略', () => mark(item, 'dismissed'), 'desktop-action-archive', 'desktop-toolbar-ghost'));
    }
    head.append(tags, actions);
    row.append(head, make('h4', item.title));
    if (item.outdated) row.append(make('p', '这条依据的论文卡片已经改动，建议重新寻找', 'desktop-research-status'));
    const question = make('div', null, 'desktop-research-opportunity-part');
    question.append(make('p', item.question));
    row.append(question);
    // The rest is folded, in the same disclosure as an answer's context: why, the comparison, how to resolve it,
    // the limitations and the card facts.
    const more = make('details', null, 'memo-saved-memory memo-context-details desktop-research-opportunity-more');
    more.append(make('summary', '详情', 'desktop-settings-disclosure'), section('判断依据', item.reasoning));
    if (item.dimensions.length) {
      const box = make('div', null, 'desktop-research-opportunity-part'), list = make('ul', null, 'desktop-research-opportunity-dimensions');
      for (const d of item.dimensions) {
        const li = make('li');
        li.append(make('span', DIMENSIONS[d.dimension] || d.dimension), make('span', COMPARED[d.status] || d.status, 'desktop-research-opportunity-compared'), make('span', d.note));
        li.dataset.compared = d.status;
        list.append(li);
      }
      box.append(make('small', '逐项比较', 'desktop-research-muted'), list);
      more.append(box);
    }
    more.append(section('建议怎么解决', item.suggestion), section('局限', item.limitations));
    if (item.evidence.length) {
      const box = make('div', null, 'desktop-research-opportunity-part'), label = make('small', null, 'desktop-research-muted');
      label.append(make('span', '证据'), ' · ' + item.evidence.length);
      box.append(label);
      for (const fact of item.evidence) {
        const factBox = make('div', null, 'desktop-research-opportunity-fact'), where = make('small', null, 'desktop-research-muted');
        where.append('《' + fact.paper_title + '》 · ', make('span', FIELDS[fact.field] || fact.field));
        if (fact.page) where.append(' · p. ' + fact.page);
        if (fact.section) where.append(' · ' + fact.section);
        factBox.append(where, make('p', fact.text));
        if (fact.quote && !fact.text.includes(fact.quote)) factBox.append(make('blockquote', fact.quote));
        box.append(factBox);
      }
      more.append(box);
    }
    row.append(more);
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
