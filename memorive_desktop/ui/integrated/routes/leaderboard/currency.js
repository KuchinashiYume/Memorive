/* Currency handling for the model leaderboard: the three display currencies (USD, CNY, JPY), conversion with the day's
   rates, and formatting. Publishes Memorive.leaderboardCurrency for routes/leaderboard/view.js. */
(() => {

const LeaderboardCurrency = (() => {
  const codes = Object.freeze(['USD', 'CNY', 'JPY']);
  const labels = Object.freeze({USD: '美元', CNY: '人民币', JPY: '日元'});
  const validAmount = n => typeof n === 'number' && Number.isFinite(n) && n >= 0;
  function currency(code) {
    if (typeof code !== 'string') return null;
    const canonical = code.trim().toUpperCase() === 'RMB' ? 'CNY' : code.trim().toUpperCase();
    return codes.includes(canonical) ? canonical : null;
  }
  function day(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    const time = Date.parse(value + 'T00:00:00Z');
    return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value ? time : null;
  }
  function snapshotStatus(snapshot, now, maxAgeDays = 7) {
    if (!snapshot) return 'missing';
    const date = day(snapshot.rateDate), instant = Date.parse(now);
    const fetched = Date.parse(snapshot.fetchedAt), rates = snapshot.unitsPerUsd;
    if (!Number.isFinite(instant) || !Number.isFinite(fetched) || date === null ||
        !Number.isInteger(maxAgeDays) || maxAgeDays < 0 ||
        !snapshot.id || !snapshot.source || !rates || rates.USD !== 1 ||
        codes.some(code => !validAmount(rates[code]) || rates[code] === 0) ||
        fetched > instant || date > fetched || date > instant) return 'invalid';
    const today = day(new Date(instant).toISOString().slice(0, 10));
    if ((today - date) / 86400000 > maxAgeDays) return 'expired';
    return snapshot.refreshFailed ? 'cached' : 'available';
  }
  function convert(amount, from, to, snapshot, now) {
    const base = currency(from), quote = currency(to);
    const result = (value, status) => ({amount: value, currency: quote, status,
      snapshotId: status === 'native' ? null : snapshot?.id ?? null});
    if (!base || !quote) return result(null, 'unsupported_currency');
    if (amount === null || amount === undefined) return result(null, 'unquoted');
    if (!validAmount(amount)) return result(null, 'invalid_amount');
    if (base === quote) return result(amount, 'native');
    const status = snapshotStatus(snapshot, now);
    if (!['available', 'cached'].includes(status)) return result(null, status);
    const converted = (amount / snapshot.unitsPerUsd[base]) * snapshot.unitsPerUsd[quote];
    if (!validAmount(converted) || (amount > 0 && converted === 0)) return result(null, 'invalid_amount');
    return result(converted, status);
  }
  function quoteIn(quote, target, snapshot, now) {
    const original = Object.freeze({currency: currency(quote?.currency),
      input: quote?.input ?? null, output: quote?.output ?? null});
    return {original, currency: currency(target), unit: 'PER_MILLION_TOKENS',
      input: convert(original.input, original.currency, target, snapshot, now),
      output: convert(original.output, original.currency, target, snapshot, now)};
  }
  // Comparison precision is independent of payment minor units: JPY also keeps
  // fractional prices per million tokens. Never use this formatter for scoring.
  function format(amount, code, withCode = true) {
    const canonical = currency(code);
    if (!canonical || !validAmount(amount)) return '—';
    const text = amount > 0 && amount < .000001 ? '< 0.000001' :
      new Intl.NumberFormat('en-US', {minimumFractionDigits: 2,
        maximumFractionDigits: amount > 0 && amount < .01 ? 6 : 2}).format(amount);
    return (withCode ? canonical + ' ' : '') + text;
  }
  // A saved range lives in USD. Switching display currency never rewrites it.
  // Preserve the original bound when a displayed/rounded input is re-applied.
  function rangeInput(bound, code, snapshot, now) {
    if (bound === null || bound === undefined) return '';
    const amount = convert(bound, 'USD', code, snapshot, now).amount;
    return amount === null ? '' : String(Number(amount.toPrecision(12)));
  }
  function rangeBound(text, originalBound, initialText, code, snapshot, now) {
    if (text === initialText && originalBound !== undefined) return originalBound;
    if (text === '') return null;
    const amount = Number(text);
    if (!validAmount(amount)) throw new Error('INVALID_PRICE_RANGE');
    const converted = convert(amount, code, 'USD', snapshot, now);
    if (converted.amount === null) throw new Error('FX_UNAVAILABLE');
    return converted.amount;
  }
  function inRange(value, min, max) {
    if (!validAmount(value)) return false;
    const tolerance = Number.EPSILON * 8 * Math.max(1, value, min ?? 0, max ?? 0);
    return (min === null || value >= min - tolerance) && (max === null || value <= max + tolerance);
  }
  return Object.freeze({codes, labels, currency, snapshotStatus, convert, quoteIn,
    format, rangeInput, rangeBound, inRange});
})();

Memorive.leaderboardCurrency = LeaderboardCurrency;
})();

