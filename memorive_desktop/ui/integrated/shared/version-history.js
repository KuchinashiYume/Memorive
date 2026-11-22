/* Version history views shared by Research Chat's answer versions (routes/chat/workspace.js) and the edit history
   of a session refinement (routes/sessions/view.js): the versions, newest first, and a version compared with the
   current one word by word (the blocks of memorive/platform/text_diff.py: red only in that version, green only in
   the current one). Styles: shared/version-history.css. Publishes Memorive.versionHistory. */
(() => {
  'use strict';
  const t = (s) => Memorive.app?.translate?.(s) || s;
  const create = (tag, text, cls) => {
    const e = document.createElement(tag);
    if (text != null) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  };

  // When a version was saved, as the lists show it.
  const when = (iso) => new Date(iso).toLocaleString(undefined, { dateStyle:'short', timeStyle:'short' });

  // The versions, newest first, each a button with its name and lines of facts; onPick(version) when pressed.
  const list = (versions, { chosen, id, name, details, onPick }) => {
    const ol = create('ol', null, 'memo-history-list');
    for (const v of [...versions].reverse()) {
      const item = create('li'), b = create('button', null, 'memo-history-version');
      b.type = 'button';
      b.setAttribute('aria-pressed', String(id(v) === chosen));
      b.append(create('span', name(v), 'memo-history-version-name'), ...details(v).map((line) => create('small', line)));
      b.onclick = () => onPick(v);
      item.append(b);
      ol.append(item);
    }
    return ol;
  };

  // The blocks as one text with the removed and added words marked; more than six unchanged lines in a row fold
  // behind a button that shows them.
  const diffText = (blocks) => {
    const text = create('div', null, 'memo-message-body memo-history-diff');
    for (const block of blocks) {
      if (block.kind === 'equal') {
        const lines = block.new_lines;
        if (lines.length <= 6) {
          text.append(lines.join(''));
          continue;
        }
        const more = create('button', t('展开未改动的行') + ' · ' + (lines.length - 4), 'memo-history-skip');
        more.type = 'button';
        more.onclick = () => more.replaceWith(lines.slice(2, -2).join(''));
        text.append(lines.slice(0, 2).join(''), more, lines.slice(-2).join(''));
      } else if (block.kind === 'change') {
        for (const w of block.words) text.append(w.op === 'equal' ? w.text : create(w.op === 'insert' ? 'ins' : 'del', w.text));
      } else {
        text.append(create(block.kind === 'insert' ? 'ins' : 'del', (block.kind === 'insert' ? block.new_lines : block.old_lines).join('')));
      }
    }
    return text;
  };

  // What the two colours mean.
  const legend = (oldLabel, newLabel) => {
    const p = create('p', null, 'memo-history-legend');
    p.append(create('span', oldLabel, 'memo-history-old'), create('span', newLabel, 'memo-history-new'));
    return p;
  };

  const unchanged = (blocks) => blocks.every((b) => b.kind === 'equal');

  Memorive.versionHistory = Object.freeze({ when, list, diffText, legend, unchanged });
})();
