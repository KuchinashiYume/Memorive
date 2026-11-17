/* The node test row in the settings workflow panel: runs the model exam of the selected workflow node and shows its
   progress, elapsed time and score. Publishes window.__Desktop_EXAM_PANEL__ for settings.js and external-sources.js. */
(() => {
  'use strict';
  const root = document.getElementById('desktop-settings-preview');
  if (!root) return;
  const q = s => root.querySelector(s), panel = q('#settings-node-test-row');
  let epoch = null, selected = null, active = null, clock = null, pendingConfirmation = null;
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const number = value => finite(value) ? Number(value.toFixed(2)).toString() : '—';
  const examScore = () => {
    const text = q('#settings-node-test-score').textContent.trim();
    return /^\d+(\.\d+)?$/.test(text) && Number(text) <= 100 ? Number(text) : null;
  };
  function core() {
    const exam = examScore(), hasExam = finite(exam);
    q('#settings-exam-core-score').textContent = hasExam ? number(exam) : finite(epoch) ? `${number(epoch)}%` : '—';
    q('#settings-exam-core-score').setAttribute('aria-label', hasExam ? '当前节点考试分数' : 'Epoch AI 参考分数');
    q('#settings-exam-core-dot').className = 'status-dot ' + (hasExam ? 'green' : 'yellow');
    let kind=q('#settings-exam-core-kind');
    if(!kind){
      kind=document.createElement('small');
      kind.id='settings-exam-core-kind';
      q('#settings-exam-core-score').before(kind);
    }
    kind.textContent=hasExam?'实测':finite(epoch)?'参考':'';
    kind.hidden=!kind.textContent;
    panel.querySelector('summary').title = hasExam ? '当前节点实测分数' : 'Epoch AI 参考 · 不同来源不可横向比较';
  }
  function clearReferences() {
    epoch = null;
    for (const id of ['epoch','aa','openrouter']) {
      const n = q('#settings-exam-' + id);
      n.textContent = '—';
      n.removeAttribute('title');
    }
    core();
  }
  function references(result) {
    clearReferences();
    const slot=result.source_slots?.find(row=>row.source_id==='artificial-analysis');
    q('#settings-exam-secondary-source').textContent=slot?.display_name || 'LiveBench';
    const definitions = [
      ['epoch-ai','epoch',['eci']],
      ['artificial-analysis','aa',['livebench_global','artificial_analysis_intelligence_index']],
      ['openrouter','openrouter',['artificial_analysis_intelligence_index','intelligence_index']]
    ];
    for (const [source,id,metrics] of definitions) {
      // Do not substitute popularity, price or an unrelated benchmark for a
      // capability score. OpenRouter may not publish any comparable index.
      const rows = (result.references || []).filter(r => r.source_id === source);
      let found;
      for (const row of rows) {
        const values = row.display_metrics || row.metrics;
        const key = metrics.find(k => finite(values?.[k]?.value ?? values?.[k]));
        if (key) {
          found = {row,key,value:values[key]?.value ?? values[key]};
          break;
        }
      }
      if (!found) continue;
      const {row,key,value} = found, n = q('#settings-exam-' + id);
      const percentile = row.reference_percentiles?.[key];
      n.textContent = finite(percentile?.value) ? `${number(percentile.value)}%` : '—';
      n.title = `${row.source_name || source} · ${key} · 原值 ${value} · 同来源百分位（${percentile?.population_count || 0} 个模型） · ${row.version || '来源未提供版本'} · ${row.retrieved_at || ''}${row.stale ? ' · 缓存待更新' : ''}${row.local_base_model_reference ? ' · 基础模型参考，量化版本未实测' : ''}${row.benchmark_effort ? ' · 来源思考档 '+row.benchmark_effort : ''}${row.effort_mismatch ? '（与当前档位不同）' : ''}`;
      if (id === 'epoch') epoch = percentile?.value ?? null;
    }
    core();
  }
  function renderProgress() {
    const box = q('#settings-exam-progress'), bar = box.querySelector('progress');
    const step = q('button.desktop-settings-node[aria-current="true"]')?.dataset.step;
    box.hidden = !active || (!!active.step && active.step !== step);
    if (!active) return;
    const job = active.job;
    const elapsed = Math.max(0, Math.floor(job?.started_at ? ((Date.parse(job.finished_at || new Date().toISOString()) - Date.parse(job.started_at)) / 1000) : ((performance.now() - active.started) / 1000)));
    const phases = {PLANNING:'准备中', RUNNING:'测试中', COMPLETE:'测试结束', ERROR:'未完成'};
    const terminal = ['COMPLETE','ERROR'].includes(active.phase);
    if (terminal && active.phase === 'COMPLETE') {
      bar.max = 1;
      bar.value = 1;
    }
    else if (job && Number.isInteger(job.maximum_calls) && job.maximum_calls > 0) {
      bar.max = job.maximum_calls;
      bar.value = Math.min(job.calls_returned,job.maximum_calls);
    }
    else bar.removeAttribute('value');
    bar.hidden = false;
    const stage = active.connectionLost ? '连接中断 · 正在重新查询' : job?.cancel_requested && !terminal ? '停止中 · 保留当前返回' : !terminal && job?.stage === 'WAITING_MODEL' ? '模型处理中' : phases[active.phase];
    const counts = job ? ` · 已返回 ${job.calls_returned} / ${job.maximum_calls ?? '—'} 次` : '';
    q('#settings-exam-progress-copy').textContent = `${stage} · ${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, '0')}${counts}`;
    bar.title = '按实际调用推进；分母为调用上限，包含两次定向修复的预留，不代表题目通过率';
    box.title = active.label;
  }
  function progress(phase, context = {}) {
    if (phase === 'PLANNING') active = {...context, phase, started:performance.now()};
    else active = {...(active || {started:performance.now()}), connectionLost:false, ...context, phase};
    if (clock) clearInterval(clock);
    if (phase === 'IDLE') active = null;
    if (active && !['COMPLETE','ERROR'].includes(phase)) clock = setInterval(renderProgress, 1000);
    renderProgress();
  }
  function confirmRun(plan) {
    pendingConfirmation?.(false);
    const box = q('#settings-exam-confirm'), tier = plan.exam_tier;
    const calls = plan.maximum_model_calls ?? plan.maximum_provider_calls;
    const cost = plan.profile_kind === 'LOCAL' ? '本地 · ¥0' : plan.profile_kind === 'CLI' ? '订阅 CLI · 单次费用未知' : `费用上限 ¥${plan.budget_cap_cny ?? '—'}`;
    q('#settings-exam-plan-copy').textContent = (tier ? `${tier.label} · ${tier.selected_count}/${tier.full_count} · ${Math.round(tier.actual_fraction*1000)/10}%` : '当前节点考试') + `\n最多 ${calls ?? '—'} 次 · ${cost}`;
    box.hidden = false;
    return new Promise(resolve => {
      pendingConfirmation = accepted => {
        box.hidden = true;
        pendingConfirmation = null;
        resolve(accepted);
      };
      q('#settings-exam-plan-start').onclick = () => pendingConfirmation?.(true);
      q('#settings-exam-plan-cancel').onclick = () => pendingConfirmation?.(false);
    });
  }
  root.addEventListener('click', event => {
    const node = event.target.closest('button.desktop-settings-node[data-step]');
    if (node && node !== selected) {
      pendingConfirmation?.(false);
      selected = node;
      panel.open = false;
      clearReferences();
      setTimeout(renderProgress,0);
    }
    if (event.target.closest('#settings-model-menu')) clearReferences();
  });
  window.addEventListener('desktop:external-model-reference', event => references(event.detail));
  new MutationObserver(core).observe(q('#settings-node-test-score'), {childList:true,subtree:true,characterData:true});
  window.__Desktop_EXAM_PANEL__ = Object.freeze({progress, confirmRun, references, core, clearReferences});
  core();
})();

