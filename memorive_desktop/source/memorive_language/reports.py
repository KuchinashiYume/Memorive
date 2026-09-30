"""Fixed report templates, localized before dynamic source values are inserted."""
from contextvars import ContextVar
from functools import wraps
from .policy import locale

_LANGUAGE=ContextVar('memo_report_language', default='zh-CN')

def localized(function):
    @wraps(function)
    def call(*args, language=None, **kwargs):
        token=_LANGUAGE.set(locale(language))
        try:return function(*args,**kwargs)
        finally:_LANGUAGE.reset(token)
    return call

COPY={
 '研究变更日志 · 日报':('Research change log · Daily report','研究変更記録 · 日報'),
 '研究变更日志 · 周报':('Research change log · Weekly report','研究変更記録 · 週報'),
 '研究变更日志 · 月报':('Research change log · Monthly report','研究変更記録 · 月報'),
 '研究工作日志 · 日报':('Research activity · Daily report','研究活動 · 日報'),
 '研究工作日志 · 周报':('Research activity · Weekly report','研究活動 · 週報'),
 '研究工作日志 · 月报':('Research activity · Monthly report','研究活動 · 月報'),
 '新增':('Added','追加'),'修改':('Modified','変更'),'待审':('Pending review','確認待ち'),'失败':('Failed','失敗'),
 '研究进展':('Research progress','研究の進捗'),'明确标记的重要变化':('Explicitly marked important changes','重要と明示された変更'),
 '未验证项':('Unverified items','未検証の項目'),'明确来源的潜在张力':('Source-backed potential tensions','出典の明確な潜在的矛盾'),
 '风险与待办':('Risks and tasks','リスクと対応事項'),'知识库结构变化':('Knowledge structure changes','知識構造の変化'),
 '明确记录的研究方向变化':('Explicitly recorded research direction changes','明記された研究方向の変化'),
 '长期待办':('Long-term tasks','長期的な課題'),'风险与未评估维度':('Risks and unassessed dimensions','リスクと未評価の項目'),
 '周期摘要':('Period summary','期間の要約'),'优先处理':('Priorities','優先事項'),
 '- 当前窗口没有来源支持的需优先处理记录。':('- No source-backed priority records in this window.','- この期間には、出典のある優先対応記録はありません。'),
 '- 无来源支持的记录。':('- No source-backed records.','- 出典のある記録はありません。'),
 '- 无。':('- None.','- なし。'),
 '| 类别 | 数量 |':('| Category | Count |','| 分類 | 件数 |'),
 '### 2.1 迟到事件':('### 2.1 Late arrivals','### 2.1 遅着イベント'),
 '### 2.2 冲突':('### 2.2 Conflicts','### 2.2 競合'),
 '### 2.3 不可解析':('### 2.3 Unparseable records','### 2.3 解析不能な記録'),
 ' · 原窗口 ':(' · Original window ',' · 元の期間 '),' · 原截止 ':(' · Original cutoff ',' · 元の締切 '),
 '- event {v0} · source event {v1} · {v2} · {v3} · 状态轴 {v4}={v5} · 对象 {v6} · 来源 {v7} · 证据 {v8}':
 ('- event {v0} · source event {v1} · {v2} · {v3} · Status axis {v4}={v5} · Objects {v6} · Source {v7} · Evidence {v8}',
  '- event {v0} · source event {v1} · {v2} · {v3} · 状態軸 {v4}={v5} · 対象 {v6} · 出典 {v7} · 証拠 {v8}'),
 '- [{v0}] event {v1} · {v2} · {v3}={v4} · 来源 {v5}':('- [{v0}] event {v1} · {v2} · {v3}={v4} · Source {v5}','- [{v0}] event {v1} · {v2} · {v3}={v4} · 出典 {v5}'),
 '| 迟到事件 | {v0} |':('| Late arrivals | {v0} |','| 遅着イベント | {v0} |'),
 '| 冲突 | {v0} |':('| Conflicts | {v0} |','| 競合 | {v0} |'),
 '| 不可解析 | {v0} |':('| Unparseable | {v0} |','| 解析不能 | {v0} |'),
 '- window: {v0} 至 {v1}（左闭右开）':('- window: {v0} to {v1} (start inclusive, end exclusive)','- window: {v0} から {v1}（開始を含み、終了を含まない）'),
 '- [冲突] event {v0} · 未自动择一 · 来源 ':('- [Conflict] event {v0} · No automatic selection · Sources ','- [競合] event {v0} · 自動選択なし · 出典 '),
 '- [阻断] {v0} · {v1} · 来源 {v2}':('- [Blocked] {v0} · {v1} · Source {v2}','- [ブロック] {v0} · {v1} · 出典 {v2}'),
 '- 另有 {v0} 项，见详细记录。':('- {v0} more item(s); see detailed records.','- ほか {v0} 件。詳細記録を参照してください。'),
 '- {v0} · {v1} · {v2} · 来源 {v3}':('- {v0} · {v1} · {v2} · Source {v3}','- {v0} · {v1} · {v2} · 出典 {v3}'),
 '- event {v0} → 交叉引用：首次完整记录见「{v1}」。':('- event {v0} → Cross-reference: first complete record in “{v1}”.','- event {v0} → 相互参照：最初の完全な記録は「{v1}」にあります。'),
 '- {v0} 存在内容冲突；未自动择一；来源 ':('- {v0} has conflicting content; no automatic selection; sources ','- {v0} の内容に競合があります。自動選択なし。出典 '),
 '> Shadow / non-authoritative；仅报告已有来源支持的变化，不形成科研结论。':('> Shadow / non-authoritative. Reports only changes supported by existing sources; does not establish scientific conclusions.','> Shadow / non-authoritative。既存の出典で裏付けられた変更だけを報告し、科学的結論は確定しません。'),
 '## 0. 今日先看':('## 0. Today at a glance','## 0. 今日の概要'),
 '## 0. 本周先看':('## 0. This week at a glance','## 0. 今週の概要'),
 '## 0. 本月先看':('## 0. This month at a glance','## 0. 今月の概要'),
 '### 0.1 需要处理':('### 0.1 Needs attention','### 0.1 対応が必要な項目'),
 '### 0.2 今日变化总览':('### 0.2 Changes today','### 0.2 今日の変更'),
 '### 0.2 本周变化总览':('### 0.2 Changes this week','### 0.2 今週の変更'),
 '### 0.3 报告完整性':('### 0.3 Report completeness','### 0.3 レポートの完全性'),
 '### 0.3 明确标记的重要变化':('### 0.3 Explicitly marked important changes','### 0.3 重要と明示された変更'),
 '### 0.4 明确来源的潜在张力':('### 0.4 Source-backed potential tensions','### 0.4 出典の明確な潜在的矛盾'),
 '### 0.5 覆盖与风险':('### 0.5 Coverage and risks','### 0.5 対象範囲とリスク'),
 '### 0.2 知识结构变化':('### 0.2 Knowledge structure changes','### 0.2 知識構造の変化'),
 '### 0.3 明确记录的研究方向变化':('### 0.3 Explicitly recorded research direction changes','### 0.3 明記された研究方向の変化'),
 '### 0.4 长期未决事项':('### 0.4 Long-standing open items','### 0.4 長期の未解決事項'),
 '## 1. 详细记录':('## 1. Detailed records','## 1. 詳細記録'),
 '## 2. 迟到、冲突与不可解析':('## 2. Late arrivals, conflicts and unparseable records','## 2. 遅着・競合・解析不能な記録'),
 '## 3. 未评估维度':('## 3. Unassessed dimensions','## 3. 未評価の項目'),
 '## 附录 · 审计与重放信息':('## Appendix · Audit and replay information','## 付録 · 監査と再現の情報'),
}

def tr(key, *values):
    lang=_LANGUAGE.get(); pair=COPY.get(key)
    template=key if lang=='zh-CN' or pair is None else pair[0 if lang=='en-US' else 1]
    return template.format(**{'v'+str(i):v for i,v in enumerate(values)}) if values else template

def template(text):
    return '\n'.join(tr(line) for line in text.split('\n'))

HEADING_FIELDS={'human_title','attention_section','overview_section','section_title','cross_reference_section','api_narrative_label'}
