'use strict';
let reviewSignature = '', reviewExportSession = null;
function renderReleaseReview() {
  const report = snapshot?.release_review;
  if (!report) return;
  const reference = report.evidence_scope === 'REFERENCE_PROTOCOL';
  const manual = report.manual_checks || [];
  const failed = manual.filter(row => row.status === 'FAIL').length;
  const checked = manual.filter(row => ['PASS', 'FAIL'].includes(row.status)).length;
  const suites = report.automated_suites || [];
  const last = suites.at(-1);
  const states = {PASS:tr("通过"), FAIL:tr("失败"), NOT_ASSESSED:tr("未判定"), PARTIAL:tr("部分完成")};
  $('#review-source').textContent = reference ? tr("协议参考端 · 不作为产品验收证据") : report.session_id ? tr("Memorive ${0} · 绑定本轮 EXE 与会话",report.release_version || tr("当前版本")) : tr("连接版本后记录本轮检查。");
  $('#review-auto-summary').textContent = last ? tr("${0} · ${1} / ${2} 项",states[last.verdict] || last.verdict,last.cases.filter(row => row.state === 'PASSED').length,last.cases.length) : tr("尚未运行");
  $('#review-manual-summary').textContent = tr("${0} / ${1} 项已检查${2}",checked,manual.length,failed ? tr(" · ${0} 项问题",failed) : '');
  $('#review-manual-gate').textContent = reference ? tr("参考端不可登记产品检查") : report.manual_editable ? tr("由你观察后登记") : tr("连接并开启许可后登记");
  $('#export-review').disabled = busy || !report.session_id;
  if (reviewExportSession !== report.session_id) { $('#review-export-result').hidden = true; reviewExportSession = report.session_id; }
  const signature = JSON.stringify([report.session_id, manual, report.manual_editable, busy]);
  if (signature === reviewSignature) return;
  reviewSignature = signature;
  const root = $('#review-checks'); root.replaceChildren();
  for (const row of manual) {
    const item = document.createElement('div'); item.className = 'review-check';
    const copy = document.createElement('div'), title = document.createElement('b'), instruction = document.createElement('p');
    title.textContent = tr(row.title); instruction.textContent = tr(row.instruction); copy.append(title, instruction);
    const select = document.createElement('select'); select.setAttribute('aria-label', tr(row.title) + tr("检查结果"));
    for (const [value, label] of Object.entries({NOT_RUN:tr("未检查"), PASS:tr("已观察通过"), FAIL:tr("发现问题"), BLOCKED:tr("条件不足")})) select.append(option(value, label));
    select.value = row.status; select.disabled = !report.manual_editable || busy;
    select.addEventListener('change', () => act(async () => {
      await api('review/check', {session_id:report.session_id, check_id:row.id, status:select.value});
      notice(tr("${0}：已记录观察结果",row.title));
    }));
    item.append(copy, select); root.append(item);
  }
}
$('#export-review').addEventListener('click', () => act(async () => {
  const result = await api('review/export', {});
  $('#review-export-path').value = result.markdown_path;
  $('#review-export-result').hidden = false;
  notice(tr("已生成 Markdown 与 JSON 摘要。关闭会话后会更新清理结果。"));
}));
$('#copy-release-review').addEventListener('click', async () => {
  const input = $('#review-export-path');
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(input.value);
    else { input.select(); if (!document.execCommand('copy')) throw new Error('COPY_FAILED'); }
    notice(tr("审核摘要路径已复制。"));
  } catch (_) { input.focus(); input.select(); notice(tr("请复制选中的路径。"), true); }
});
