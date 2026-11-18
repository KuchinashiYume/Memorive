"""The fixed text of daily, weekly and monthly reports (research/reports/renderer.py) in English and
Japanese; the Chinese text is the key. The template's lines are translated before the dynamic values
(paper names, model sentences, dates) go in."""
from contextvars import ContextVar
from functools import wraps
from memorive.platform.language.policy import locale

_LANGUAGE=ContextVar('memo_report_language', default='zh-CN')

def localized(function):
    @wraps(function)
    def call(*args, language=None, **kwargs):
        token=_LANGUAGE.set(locale(language))
        try:
            return function(*args,**kwargs)
        finally:
            _LANGUAGE.reset(token)
    return call

COPY={
 # Titles, headings and section names.
 '日报':('Daily report','日報'),'周报':('Weekly report','週報'),'月报':('Monthly report','月報'),
 '这一天的进展':('Progress of the day','この日の進捗'),'这一周的进展':('Progress of the week','今週の進捗'),
 '这个月的进展':('Progress of the month','今月の進捗'),
 '## 需要处理':('## Needs attention','## 対応が必要なこと'),'## 数字一览':('## Counts','## 件数'),
 '## 附录':('## Appendix','## 付録'),'### 记录清单':('### Records','### 記録一覧'),
 '### 记录是否完整':('### Completeness','### 記録の完全性'),
 '### 本报告不评估':('### Not assessed in this report','### このレポートで評価しないこと'),
 '### 报告信息':('### About this report','### レポート情報'),
 '新增':('Added','追加'),'修改':('Modified','変更'),'待审':('Pending review','確認待ち'),'失败':('Failed','失敗'),
 '研究进展':('Research progress','研究の進捗'),'明确标记的重要变化':('Explicitly marked important changes','重要と明示された変更'),
 '未验证项':('Unverified items','未検証の項目'),'明确来源的潜在张力':('Source-backed potential tensions','出典の明確な潜在的矛盾'),
 '风险与待办':('Risks and tasks','リスクと対応事項'),'知识库结构变化':('Knowledge structure changes','知識構造の変化'),
 '明确记录的研究方向变化':('Explicitly recorded research direction changes','明記された研究方向の変化'),
 '长期待办':('Long-term tasks','長期的な課題'),'风险与未评估维度':('Risks and unassessed dimensions','リスクと未評価の項目'),
 # The lead line.
 '这一天的记录：{v0}。':('Records of the day: {v0}.','この日の記録：{v0}。'),
 '这一周的记录：{v0}。':('Records of the week: {v0}.','今週の記録：{v0}。'),
 '这个月的记录：{v0}。':('Records of the month: {v0}.','今月の記録：{v0}。'),
 '这一天没有新的记录。':('No new records this day.','この日の新しい記録はありません。'),
 '这一周没有新的记录。':('No new records this week.','今週の新しい記録はありません。'),
 '这个月没有新的记录。':('No new records this month.','今月の新しい記録はありません。'),
 '{v0} {v1} 条':('{v0} {v1}','{v0} {v1} 件'),'、':(', ','、'),
 # Needs attention and progress.
 '- 没有需要马上处理的事。':('- Nothing needs attention right now.','- すぐに対応が必要なことはありません。'),
 '- {v0} 的内容互相冲突，报告没有替你选择，见附录“记录是否完整”。':
 ('- {v0} has conflicting content; the report does not choose for you. See “Completeness” in the appendix.',
  '- {v0} の内容が競合しています。レポートは選択しません。付録の「記録の完全性」を参照してください。'),
 '- 有记录无法解析（{v0}），报告可能不完整，见附录。':
 ('- A record could not be parsed ({v0}); the report may be incomplete. See the appendix.',
  '- 解析できない記録があります（{v0}）。レポートが不完全な可能性があります。付録を参照してください。'),
 '这一时段没有新的进展记录。':('No progress was recorded in this period.','この期間に進捗の記録はありません。'),
 # Counts and records.
 '| 类别 | 数量 |':('| Category | Count |','| 分類 | 件数 |'),
 '| 迟到 | {v0} |':('| Late | {v0} |','| 遅着 | {v0} |'),'| 冲突 | {v0} |':('| Conflicts | {v0} |','| 競合 | {v0} |'),
 '| 不可解析 | {v0} |':('| Unparseable | {v0} |','| 解析不能 | {v0} |'),
 '- 没有记录。':('- No records.','- 記録はありません。'),
 '{v0}. {v1} · {v2}{v3} · {v4} · 编号 {v5}':('{v0}. {v1} · {v2}{v3} · {v4} · id {v5}','{v0}. {v1} · {v2}{v3} · {v4} · 番号 {v5}'),
 '（迟到）':(' (late)','（遅着）'),
 # Completeness.
 '记录完整：没有迟到、冲突或无法解析的记录。':('Complete: no late, conflicting or unparseable records.',
  '記録は完全です。遅着・競合・解析不能な記録はありません。'),
 '记录不完整（{v0}）。':('Incomplete ({v0}).','記録は不完全です（{v0}）。'),
 '- 迟到 {v0} 条：发生在这一时段，但在上一份报告截止后才记录，已列在记录清单里，标“迟到”。':
 ("- {v0} late: they happened in this period but were recorded after the previous report's cutoff; they are among the records, marked “late”.",
  '- 遅着 {v0} 件：この期間に起きたが、前回のレポートの締切後に記録されたもの。記録一覧に「遅着」として載せています。'),
 '- {v0}：内容互相冲突，报告没有替你选择；来源 {v1}。':('- {v0}: conflicting content; the report does not choose for you. Sources {v1}.',
  '- {v0}：内容が競合しています。レポートは選択しません。出典 {v1}。'),
 '- {v0}：无法解析（{v1}），{v2}；来源 {v3}。':('- {v0}: could not be parsed ({v1}); {v2}. Source {v3}.',
  '- {v0}：解析できません（{v1}）。{v2}。出典 {v3}。'),
 '影响报告完整性':('the report is incomplete','レポートは不完全です'),'不影响报告完整性':('the report is still complete','レポートの完全性には影響しません'),
 # Not assessed.
 '- 哪些变化更重要：记录里没有重要性的标注，报告按时间排列，不排先后。':
 ('- Which changes matter more: the records carry no importance, so the report lists them by time.',
  '- どの変更がより重要か：記録に重要度がないため、時刻順に並べています。'),
 '- 文献之间的分歧：这一时段没有研究机会的记录。':('- Disagreements between papers: no research opportunities were recorded in this period.',
  '- 文献間の相違：この期間に研究機会の記録はありません。'),
 '- 研究方向的变化：这一时段没有记录方向决定。':('- Changes of research direction: no direction decisions were recorded in this period.',
  '- 研究方向の変化：この期間に方向の決定は記録されていません。'),
 '- 部分可选来源的记录没能读取，上面的数字可能不全。':('- Some optional sources could not be read; the counts above may be incomplete.',
  '- 一部の任意の出典を読み取れませんでした。上の件数は不完全な可能性があります。'),
 '- 有迟到的记录无法确认属于哪个时段。':('- Some late records could not be placed in a period.','- 一部の遅着記録の期間を確認できませんでした。'),
 '- 没有。':('- None.','- なし。'),
 # About this report.
 '- 统计时段：{v0} 至 {v1}（{v2}，含开始、不含结束）':('- Period: {v0} to {v1} ({v2}; start included, end excluded)',
  '- 対象期間：{v0} から {v1}（{v2}。開始を含み、終了を含まない）'),
 '- 生成时间：{v0}':('- Generated: {v0}','- 作成日時：{v0}'),
 '- 记录 {v0} 条；来源清单校验值 {v1}':('- {v0} records; source list checksum {v1}','- 記録 {v0} 件。出典一覧のチェックサム {v1}'),
 '- 摘要：由 {v0} 根据上面的记录撰写，每句后的编号是它依据的记录。':
 ('- Summary: written by {v0} from the records above; the numbers after each sentence are the records it rests on.',
  '- 要約：{v0} が上の記録をもとに作成しました。各文の後の番号は根拠とした記録です。'),
 '- 摘要：这份报告没有模型摘要。':('- Summary: this report has no model summary.','- 要約：このレポートにはモデルによる要約はありません。'),
 '- 报告编号：{v0}，第 {v1} 版':('- Report id: {v0}, revision {v1}','- レポート番号：{v0}、第 {v1} 版'),
 '，取代 {v0}':(', replaces {v0}','。{v0} を置き換え'),
}

def tr(key, *values):
    lang=_LANGUAGE.get()
    pair=COPY.get(key)
    template=key if lang=='zh-CN' or pair is None else pair[0 if lang=='en-US' else 1]
    return template.format(**{'v'+str(i):v for i,v in enumerate(values)}) if values else template

def template(text):
    return '\n'.join(tr(line) for line in text.split('\n'))
