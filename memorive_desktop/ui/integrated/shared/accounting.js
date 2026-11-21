/* The bottom accounting panel of the window (one for every route): cost, requests and tokens by model and period, with its chart
   (asked from the desktop service). Its texts are the accounting.* messages in shared/frame/messages.js.
   Publishes Memorive.accounting. */
(() => {
  'use strict';
  const i18n = Memorive.i18n;
  if (Memorive.accounting) return;
  const initialize = (panel) => {
  if (!panel || panel.dataset.accountingInitialized === 'true') return null;
  panel.dataset.accountingInitialized = 'true';
  const one = (selector) => panel.querySelector(selector);
  const svgNode = (name, attrs = {}) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', name);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  };
  const scope = one('#desktop-accounting-scope');
  const range = one('#desktop-accounting-range');
  const model = one('#desktop-accounting-model');
  const refreshButton = one('#desktop-accounting-refresh');
  const refreshIcon = Memorive.controls?.createIcon('refresh');
  if (refreshIcon) refreshButton.replaceChildren(refreshIcon);
  const currencyButton = one('#desktop-accounting-currency');
  const currencyCode = one('#desktop-accounting-currency-code');
  const sync = one('#desktop-accounting-sync');
  const closeButton = one('#desktop-accounting-close');
  const chart = one('#desktop-accounting-chart');
  const chartTitle = one('#desktop-accounting-chart-title');
  const chartUnit = one('#desktop-accounting-chart-unit');
  const chartIcon = one('#desktop-accounting-chart-icon');
  const legend = one('#desktop-accounting-legend');
  const legendWrap = document.createElement('span');
  legendWrap.className = 'desktop-accounting-legend-wrap';
  const legendButton = document.createElement('button');
  legendButton.type = 'button';
  legendButton.className = 'desktop-accounting-legend-button icon-button';
  ['aria-label', 'title', 'tip'].forEach((attribute) => i18n.set(legendButton, 'accounting.legend.show', null, attribute));
  legendButton.setAttribute('aria-expanded', 'false');
  const legendIcon = svgNode('svg', {viewBox:'0 0 24 24', 'aria-hidden':'true'});
  legendIcon.append(svgNode('use', {href:'#desktop-icon-models'}));
  legendButton.append(legendIcon);
  legend.parentNode.insertBefore(legendWrap, legend);
  legendWrap.append(legendButton, legend);
  legend.hidden = true;
  legend.setAttribute('role','region');
  i18n.set(legend, 'accounting.legend', null, 'aria-label');
  const closeLegend = () => {
    legend.hidden = true;
    legendButton.setAttribute('aria-expanded','false');
  };
  legendButton.addEventListener('click', () => {
    legend.hidden = !legend.hidden;
    legendButton.setAttribute('aria-expanded', String(!legend.hidden));
  });
  document.addEventListener('pointerdown', e => { if (!legendWrap.contains(e.target)) closeLegend(); });
  legendWrap.addEventListener('keydown', e => { if (e.key === 'Escape') {
    closeLegend();
    legendButton.focus();
  } });
  // One span per line; each line is [message key, values].
  const detailLines = (target, lines) => {
    i18n.set(target, null);
    target.replaceChildren(...lines.map(([key, values]) => {
      const n=document.createElement('span');
      i18n.set(n, key, values);
      return n;
    }));
  };
  // Text that is data, not a message: a number, a currency code.
  const plain = (target, value) => {
    i18n.set(target, null);
    target.textContent = value;
  };
  const kind = (code) => code === 'LOCAL' ? { key: 'accounting.local' } : code || '';

  const plot = one('#desktop-accounting-plot');
  const svg = one('#desktop-accounting-svg');
  const svgTitle = one('#desktop-accounting-svg-title');
  const svgDesc = one('#desktop-accounting-svg-desc');
  const tooltip = one('#desktop-accounting-tooltip');
  const empty = one('#desktop-accounting-empty');
  const metricButtons = [...panel.querySelectorAll('[data-metric]')];
  const currencies = ['USD', 'CNY', 'JPY'];
  const currencyLabel = (code) => code === 'CNY' ? 'RMB' : code;
  // The chart title of each metric is accounting.chart.<metric>.
  const metricMeta = Object.freeze({
    COST: { icon: '#desktop-icon-cost', color: '#C8704F' },
    REQUESTS: { icon: '#desktop-icon-requests', color: '#557C9E' },
    TOKENS: { icon: '#desktop-icon-tokens', color: '#4E7E65' },
  });
  let selectedMetric = 'COST';
  let preferredCurrency = 'USD';
  let currencyInitialized = false;
  // The task "this task" means: the one selected on the current task page ({job_id, title}), or none.
  let job = null;
  let projection = null;
  let requestSerial = 0;

  const safeArray = (value) => Array.isArray(value) ? value : [];
  const numeric = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
  const compact = (value) => new Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 1 }).format(numeric(value));
  const integer = (value) => Math.round(numeric(value)).toLocaleString('zh-CN');
  const amount = (value, code) => {
    if(value===null || value===undefined)return '—';
    const n=numeric(value);
    if(n>0 && n<1e-12)return `< 0.000000000001 ${currencyLabel(code)}`;
    const digits=n>0 && n<.01 ? Math.min(12,Math.ceil(-Math.log10(n))+2) : 2;
    return `${new Intl.NumberFormat('en-US',{minimumFractionDigits:2,maximumFractionDigits:digits}).format(n)} ${currencyLabel(code)}`;
  };
  const shortDate = (value) => {
    const parts = String(value || '').split('-');
    return parts.length === 3 ? `${Number(parts[1])}/${Number(parts[2])}` : String(value || '');
  };
  const parseDay = (value) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ''));
    if (!match) return null;
    const day = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
    return Number.isFinite(day.getTime()) && day.toISOString().slice(0, 10) === match[0] ? day : null;
  };
  const dayKey = (value) => value.toISOString().slice(0, 10);
  const normalizedSeries = () => {
    const sourceRows = safeArray(projection?.series).filter((row) => parseDay(row?.bucket));
    const start = parseDay(projection?.range?.start_date);
    const end = parseDay(projection?.range?.end_date);
    if (!start || !end || start > end) return sourceRows;
    const byDay = new Map(sourceRows.map((row) => [row.bucket, row]));
    const rows = [];
    for (let cursor = new Date(start); cursor <= end && rows.length < 370; cursor.setUTCDate(cursor.getUTCDate() + 1)) {
      const key = dayKey(cursor);
      rows.push(byDay.get(key) || { bucket: key, measures: [] });
    }
    return rows;
  };
  const timestamp = (value) => {
    const parsed = new Date(value);
    return Number.isFinite(parsed.getTime()) ? parsed.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '—';
  };
  const call = (method, params) => Memorive.service.call(method, params);
  const params = () => ({
    scope: scope.value,
    current_job_id: job?.job_id || null,
    time_range: range.value,
    model_profile_ref: model.value || 'ALL',
    preferred_currency: preferredCurrency,
    group_by: 'MODEL_PROFILE',
  });

  const setModelOptions = (rows) => {
    const previous = model.value || 'ALL';
    const fragment = document.createDocumentFragment();
    const all = document.createElement('option');
    all.value = 'ALL';
    i18n.set(all, 'accounting.model.all-count', { count: rows.length });
    fragment.append(all);
    for (const code of ['API', 'CLI', 'LOCAL']) {
      const groupRows = rows.filter((row) => row.profile_kind === code);
      if (!groupRows.length) continue;
      const group = document.createElement('optgroup');
      i18n.set(group, `accounting.kind.${code}`, null, 'label');
      groupRows.forEach((row) => {
        const option = document.createElement('option');
        option.value = row.profile_ref;
        i18n.set(option, 'accounting.model.option', { kind: kind(row.profile_kind), provider: row.provider_label, model: row.model_label });
        group.append(option);
      });
      fragment.append(group);
    }
    model.replaceChildren(fragment);
    model.value = [...model.options].some((option) => option.value === previous) ? previous : 'ALL';
  };

  // The scope is set together with its options' selected attribute, so the dressed select shows it at once.
  const setScope = (value) => {
    scope.value = value;
    for (const option of scope.options) option.toggleAttribute('selected', option.value === value);
  };
  // wantJob: the person chose "this task"; it is shown again whenever the selected task has records in the range.
  let wantJob = false;
  scope.addEventListener('change', () => { wantJob = scope.value === 'CURRENT_JOB'; });
  const applyScopeCapability = () => {
    const option = scope.querySelector('option[value="CURRENT_JOB"]');
    // Only for the task the projection was read for: after another task is selected the old answer does not hold.
    const ready = Boolean(job) && projection?.filters?.current_job_id === job.job_id
      && projection?.scope_capabilities?.current_job?.status === 'READY';
    option.disabled = !ready;
    if (!job) i18n.set(option, 'accounting.scope.job-none');
    else if (ready) i18n.set(option, 'accounting.scope.job-named', { title: job.title });
    else i18n.set(option, 'accounting.scope.job-partial');
    if (!ready && scope.value === 'CURRENT_JOB') setScope('ALL');
  };

  const renderSummary = () => {
    const display = projection?.cost_display || {};
    const currency = projection?.display_currency || preferredCurrency;
    const usable = ['NATIVE', 'AVAILABLE', 'CACHED'].includes(display.conversion_status) && display.assessable_cost !== null;
    one('#desktop-accounting-cost').textContent = usable ? amount(display.assessable_cost, currency) : '—';
    detailLines(one('#desktop-accounting-cost-note'), usable
      ? [['accounting.cost.actual', { amount: amount(display.actual_cost, currency) }], ['accounting.cost.estimated', { amount: amount(display.estimated_cost, currency) }]]
      : [[display.conversion_status==='NOT_ASSESSED' ? 'accounting.cost.not-reported' : 'accounting.cost.no-rate']]);
    if (projection?.attempt_count === 0) {
      one('#desktop-accounting-cost').textContent = '—';
      i18n.set(one('#desktop-accounting-cost-note'), 'accounting.cost.no-records');
    }
    one('#desktop-accounting-attempts').textContent = integer(projection?.attempt_count);
    detailLines(one('#desktop-accounting-attempt-note'), [['accounting.requests.failed', { count: integer(projection?.failed_attempt_count) }], ['accounting.requests.unpriced', { count: integer(projection?.unpriced_attempt_count) }]]);
    const usage = projection?.tokens || {};
    one('#desktop-accounting-tokens').textContent = compact(numeric(usage.input) + numeric(usage.output));
    const inputKey = projection?.source_summary?.reason_codes?.includes('TOKEN_USAGE_PARTIAL') ? 'accounting.tokens.input-partial' : 'accounting.tokens.input';
    detailLines(one('#desktop-accounting-token-note'), [[inputKey, { count: compact(usage.input) }], ['accounting.tokens.output', { count: compact(usage.output) }]]);
    preferredCurrency = currency;
    currencyCode.textContent = currencyLabel(currency);
    const next = currencies[(currencies.indexOf(currency) + 1) % currencies.length];
    i18n.set(currencyButton, 'accounting.currency', { current: currencyLabel(currency), next: currencyLabel(next) }, 'aria-label');
    i18n.set(sync, projection?.refresh?.status === 'REFRESHED' ? 'accounting.sync.refreshed' : 'accounting.sync.synced', { time: timestamp(projection?.generated_at) });
  };

  const measureValue = (measure, metric) => {
    if (metric === 'COST') return numeric(measure.display_actual_cost) + numeric(measure.display_estimated_cost);
    if (metric === 'REQUESTS') return numeric(measure.request_count);
    return numeric(measure.tokens_input) + numeric(measure.tokens_output);
  };
  // One model's value on one day, for the chart tooltip.
  const measureDetail = (measure, metric) => {
    if (metric !== 'COST') return i18n.text(`accounting.measure.${metric}`, { count: integer(measureValue(measure, metric)) });
    if (numeric(measure.unpriced_attempt_count) > 0 && measureValue(measure, metric) === 0) return i18n.text('accounting.requests.unpriced', { count: integer(measure.unpriced_attempt_count) });
    const actual = numeric(measure.display_actual_cost);
    const estimated = numeric(measure.display_estimated_cost);
    const money = (value) => amount(value, projection.display_currency);
    return i18n.text('accounting.measure.cost', { total: money(actual + estimated), actual: money(actual), estimated: money(estimated) });
  };
  const niceCeiling = (value, metric) => {
    if (value <= 0) return 1;
    const magnitude = 10 ** Math.floor(Math.log10(value));
    const normalized = value / magnitude;
    const ceiling = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
    const result = ceiling * magnitude;
    return metric === 'COST' ? result : Math.ceil(result);
  };
  const modelMap = () => new Map(safeArray(projection?.models).map((row) => [row.profile_ref, row]));

  const renderLegend = () => {
    legend.replaceChildren();
    safeArray(projection?.models).forEach((row) => {
      const item = document.createElement('span');
      item.className = 'desktop-accounting-legend-item';
      const swatch = document.createElement('span');
      swatch.className = 'desktop-accounting-legend-swatch';
      swatch.style.background = row.color;
      const label = document.createElement('span');
      i18n.set(label, 'accounting.model.short', { kind: kind(row.profile_kind), model: row.model_label });
      item.append(swatch, label);
      legend.append(item);
    });
  };

  const renderChart = () => {
    if (!projection) return;
    const meta = metricMeta[selectedMetric];
    panel.dataset.metric = selectedMetric;
    i18n.set(chartTitle, `accounting.chart.${selectedMetric}`);
    if (selectedMetric === 'REQUESTS') i18n.set(chartUnit, 'accounting.unit.REQUESTS');
    else plain(chartUnit, selectedMetric === 'COST' ? currencyLabel(projection.display_currency) : 'Tokens');
    chartIcon.setAttribute('href', meta.icon);
    chart.setAttribute('aria-labelledby', `desktop-accounting-tab-${selectedMetric.toLowerCase()}`);
    i18n.set(svgTitle, 'accounting.chart.label', { range: { key: range.selectedOptions[0].dataset.i18n }, metric: { key: `accounting.chart.${selectedMetric}` } });
    i18n.set(svgDesc, 'accounting.chart.axis', { start: shortDate(projection.range.start_date), end: shortDate(projection.range.end_date) });
    renderLegend();
    const rows = normalizedSeries();
    const map = modelMap();
    const rect = plot.getBoundingClientRect();
    const width = Math.max(360, Math.round(rect.width));
    const height = Math.max(150, Math.round(rect.height));
    const margin = { top: 8, right: 8, bottom: 25, left: 52 };
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.dataset.bucketCount = String(rows.length);
    svg.dataset.rangeStart = rows[0]?.bucket || '';
    svg.dataset.rangeEnd = rows[rows.length - 1]?.bucket || '';
    svg.replaceChildren(svgTitle, svgDesc);
    const totals = rows.map((row) => safeArray(row.measures).reduce((sum, measure) => sum + measureValue(measure, selectedMetric), 0));
    const yMax = niceCeiling(Math.max(0, ...totals), selectedMetric);
    for (let index = 0; index <= 2; index += 1) {
      const y = margin.top + (plotHeight * index / 2);
      const line = svgNode('line', { x1: margin.left, y1: y, x2: width - margin.right, y2: y, class: 'desktop-accounting-grid-line' });
      const label = svgNode('text', { x: margin.left - 7, y: y + 3, 'text-anchor': 'end', class: 'desktop-accounting-axis-label' });
      const value = yMax * (1 - index / 2);
      label.textContent = selectedMetric === 'COST' ? value.toFixed(value < 1 ? 2 : 1) : compact(value);
      svg.append(line, label);
    }
    const slot = plotWidth / Math.max(1, rows.length);
    const barWidth = Math.max(2, Math.min(18, slot * .62));
    rows.forEach((row, index) => {
      const x = margin.left + slot * index + (slot - barWidth) / 2;
      let cursor = margin.top + plotHeight;
      safeArray(row.measures).forEach((measure) => {
        const value = measureValue(measure, selectedMetric);
        const segmentHeight = yMax ? value / yMax * plotHeight : 0;
        cursor -= segmentHeight;
        const info = map.get(measure.profile_ref) || {};
        if (value > 0) svg.append(svgNode('rect', { x, y: cursor, width: barWidth, height: Math.max(1, segmentHeight), rx: 1.5, fill: info.color || meta.color, class: 'desktop-accounting-bar' }));
        else if (selectedMetric === 'COST' && numeric(measure.unpriced_attempt_count) > 0) svg.append(svgNode('rect', { x, y: margin.top + plotHeight - 3, width: barWidth, height: 3, rx: 1.5, fill: 'none', stroke: info.color || meta.color, 'stroke-width': 1.5, class: 'desktop-accounting-bar desktop-accounting-unpriced' }));
      });
      const hit = svgNode('rect', { x: margin.left + slot * index, y: margin.top, width: slot, height: plotHeight, class: 'desktop-accounting-hit' });
      hit.addEventListener('pointermove', (event) => {
        const lines = safeArray(row.measures).filter((measure) => measureValue(measure, selectedMetric) > 0 || (selectedMetric === 'COST' && numeric(measure.unpriced_attempt_count) > 0)).map((measure) => {
          const info = map.get(measure.profile_ref) || {};
          const name = i18n.text('accounting.model.short', { kind: kind(info.profile_kind), model: info.model_label || measure.profile_ref });
          return `${name}: ${measureDetail(measure, selectedMetric)}`;
        });
        tooltip.textContent = `${row.bucket}\n${lines.length ? lines.join('\n') : i18n.text('accounting.tooltip.none')}`;
        tooltip.hidden = false;
        const bounds = plot.getBoundingClientRect();
        tooltip.style.left = `${Math.min(Math.max(4, event.clientX - bounds.left + 10), Math.max(4, bounds.width - tooltip.offsetWidth - 4))}px`;
        tooltip.style.top = `${Math.max(4, event.clientY - bounds.top - tooltip.offsetHeight - 8)}px`;
      });
      hit.addEventListener('pointerleave', () => { tooltip.hidden = true; });
      svg.append(hit);
    });
    const maxTicks = width < 620 ? 4 : width < 900 ? 6 : 8;
    const step = Math.max(1, Math.ceil(rows.length / Math.max(1, maxTicks - 1)));
    const indices = [...new Set(rows.map((_, index) => index).filter((index) => index === 0 || index === rows.length - 1 || index % step === 0))];
    indices.forEach((index) => {
      const x = margin.left + slot * index + slot / 2;
      const label = svgNode('text', { x, y: height - 7, 'text-anchor': index === 0 ? 'start' : index === rows.length - 1 ? 'end' : 'middle', class: 'desktop-accounting-axis-label' });
      label.textContent = shortDate(rows[index].bucket);
      svg.append(label);
    });
    const hasUnpriced = selectedMetric === 'COST' && numeric(projection?.unpriced_attempt_count) > 0;
    if (hasUnpriced) i18n.set(empty, 'accounting.chart.unpriced', { count: integer(projection.unpriced_attempt_count) });
    else i18n.set(empty, 'accounting.chart.empty');
    empty.hidden = totals.some((value) => value > 0) || hasUnpriced;
  };

  const selectMetric = (next) => {
    if (!metricMeta[next]) return;
    selectedMetric = next;
    metricButtons.forEach((button) => {
      const active = button.dataset.metric === next;
      button.setAttribute('aria-selected', String(active));
      button.closest('[data-metric-row]')?.classList.toggle('is-active', active);
    });
    renderChart();
  };

  // settle: after this read, show "this task" again when the person chose it and it has become available (the reads
  // that follow from here do not, so a task whose records come and go cannot make the panel read in a loop).
  const load = async ({ manual = false, currencyChanged = false, settle = true } = {}) => {
    const serial = ++requestSerial;
    const finishLoading = Memorive.decorations?.beginLoading(plot) || (()=>{});
    refreshButton.disabled = true;
    refreshButton.setAttribute('aria-busy', 'true');
    i18n.set(sync, manual ? 'accounting.sync.refreshing' : 'accounting.sync.syncing');
    try {
      if (!currencyInitialized) {
        const context = await call('models.get_commerce_context_v1', {});
        if (serial !== requestSerial) return null;
        if (context?.schema_version === 'SharedModelCommerceContext-v1' && currencies.includes(context.display_currency)) preferredCurrency = context.display_currency;
        currencyInitialized = true;
      }
      const method = manual ? 'accounting.refresh_projection_v3' : 'accounting.get_projection_v3';
      const next = await call(method, params());
      if (serial !== requestSerial) return null;
      if (next?.schema_version !== 'AccountingPanelProjection-v3') throw new Error('ACCOUNTING_PROJECTION_SCHEMA_MISMATCH');
      projection = next;
      setModelOptions(safeArray(next.model_options));
      applyScopeCapability();
      // Read for all records while "this task" waited for the new task's: show the task's own now that it has records.
      if (settle && wantJob && scope.value !== 'CURRENT_JOB' && job && projection.scope_capabilities?.current_job?.status === 'READY') {
        setScope('CURRENT_JOB');
        return load({ manual, settle: false });
      }
      renderSummary();
      renderChart();
      if (currencyChanged) {
        window.dispatchEvent(new CustomEvent('desktop:display-currency-changed', { detail: { currency: preferredCurrency, display_label: currencyLabel(preferredCurrency), source: 'accounting-panel', commerce_context_revision: next.commerce_context?.revision || null } }));
      }
      return next;
    } catch (error) {
      finishLoading(true);
      // The task has no records in this range or for this model: read all records instead (once; "this task" comes
      // back when it has records again).
      if (serial === requestSerial && scope.value === 'CURRENT_JOB' && String(error?.message || '').includes('ACCOUNTING_CURRENT_JOB_SCOPE_NOT_READY')) {
        setScope('ALL');
        void load({ manual, settle: false });
        return null;
      }
      if (serial === requestSerial) i18n.set(sync, 'accounting.sync.failed', { error: error?.message || 'UNKNOWN' });
      return null;
    } finally {
      finishLoading(false);
      if (serial === requestSerial) {
        refreshButton.disabled = false;
        refreshButton.removeAttribute('aria-busy');
      }
    }
  };

  const visible = () => panel.getAttribute('aria-hidden') === 'false' && !panel.hidden && panel.getClientRects().length > 0;
  const loadVisible = () => { if (visible()) void load(); };
  new MutationObserver(loadVisible).observe(panel, { attributes: true, attributeFilter: ['aria-hidden', 'hidden'] });
  window.addEventListener('desktop-integrated-route', loadVisible);

  metricButtons.forEach((button) => button.addEventListener('click', () => selectMetric(button.dataset.metric)));
  currencyButton.addEventListener('click', async () => {
    selectMetric('COST');
    currencyInitialized = true;
    preferredCurrency = currencies[(currencies.indexOf(preferredCurrency) + 1) % currencies.length];
    await load({ currencyChanged: true });
  });
  refreshButton.addEventListener('click', () => void load({ manual: true }));
  [scope, range, model].forEach((control) => control.addEventListener('change', () => void load()));
  closeButton.addEventListener('click', () => {
    const toggle = document.getElementById('desktop-toggle-bottom');
    if (toggle.getAttribute('aria-pressed') === 'true') toggle.click();
  });
  window.addEventListener('desktop:model-commerce-context-changed', loadVisible);
  window.addEventListener('desktop:display-currency-changed', (event) => {
    const code = event.detail?.currency === 'RMB' ? 'CNY' : event.detail?.currency;
    if (event.detail?.source === 'accounting-panel' || !currencies.includes(code) || code === preferredCurrency) return;
    currencyInitialized = true;
    preferredCurrency = code;
    loadVisible();
  });
  new ResizeObserver(() => { if (projection) renderChart(); }).observe(plot);
  loadVisible();
  const selectJob = (next) => {
    job = next?.job_id ? { job_id: String(next.job_id), title: String(next.title || next.job_id) } : null;
    applyScopeCapability();
    loadVisible();
  };
  return Object.freeze({ load, visible, selectJob });
  };
  const instances = [...document.querySelectorAll('.desktop-accounting-panel')].map(initialize).filter(Boolean);
  Memorive.accounting = Object.freeze({
    instanceCount: instances.length,
    refresh: (manual = false) => instances.filter((item) => item.visible()).forEach((item) => void item.load({ manual })),
    // The current task page names the task it shows ({job_id, title}), or null when none is selected.
    selectJob: (job) => instances.forEach((item) => item.selectJob(job)),
  });
  window.addEventListener('pywebviewready', () => Memorive.accounting.refresh());
  window.requestAnimationFrame(() => Memorive.accounting.refresh());
})();

