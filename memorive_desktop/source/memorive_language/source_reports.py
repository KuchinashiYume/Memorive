"""Readable literature review projection. Scientific records remain byte-stable."""
import json
from .text import choose

COPY={
 'Check conversion, labels and the supplied method binding.':('请核对换算、标签和所提供的方法绑定。','Check conversion, labels and the supplied method binding.','換算、ラベル、指定された手法との対応を確認してください。'),
 'Reported values do not overlap under the supplied conditions.':('在所提供的条件下，报告数值的区间不重叠。','Reported values do not overlap under the supplied conditions.','指定された条件では報告値の区間が重なりません。'),
 'Descriptive only; mixed numeric objects are not a research sample; no uniform-digit assumption.':('仅作描述；不同数值对象不构成同一研究样本，也不假设数字均匀分布。','Descriptive only; mixed numeric objects are not a research sample; no uniform-digit assumption.','記述のみです。異なる数値項目は一つの研究標本ではなく、数字の一様分布も仮定しません。'),
 'No explicit rule bindings; numeric descriptions are available.':('没有明确的规则绑定；可查看数值描述。','No explicit rule bindings; numeric descriptions are available.','明示的なルールの対応付けはありません。数値の記述は利用できます。'),
}

def render(report):
    language=report['language_context']
    def t(zh,en,ja):return choose(language,zh,en,ja)
    def generated(value):return choose(language,*COPY[value]) if value in COPY else value
    color={
      'GREEN':t('已检查范围未见重要疑点','No important concerns in the checked scope','確認範囲に重要な疑問点なし'),
      'YELLOW':t('存在需要澄清的疑点','Concerns need clarification','説明を要する疑問点あり'),
      'RED':t('存在重要疑点，建议优先复核','Important concerns; prioritize review','重要な疑問点あり。優先的な確認を推奨'),
      None:t('核查受限；没有风险色结论','Limited review; no risk color conclusion','確認範囲に制限があり、色による結論はありません')}
    heading=t('数据分析摘要','Data review summary','データ確認の要約') if report['kind']=='DATA' else t('逻辑分析报告','Logic review report','論理確認レポート')
    lines=['# '+heading,'',color[report.get('color')],t('状态：','Status: ','状態：')+report['status'],
      t('报告：','Report: ','レポート：')+report['report_id'],'Source SHA-256: '+report['source_sha256'],'',
      t('本报告为派生复核意见。统计相容不证明实验真实发生，不相容不判断作者动机。最终科研判断由人完成。','This report provides derived review observations. Statistical compatibility does not prove that an experiment occurred; incompatibility does not establish author intent. People make the final scientific judgment.','本レポートは派生的な確認所見です。統計的な整合性は実験の実施を証明せず、不整合は著者の意図を判定しません。最終的な科学的判断は人が行います。'),'']
    scope=report['scope']
    lines+=['## '+t('已检查范围','Checked scope','確認範囲'),'']
    for key,names in {
        'processed_lines':('原文行数','Source lines','原文行数'),
        'numeric_objects':('数值对象','Numeric objects','数値項目'),
        'completed_checks':('已完成核查','Completed checks','完了した確認'),
        'planned_checks':('计划核查','Planned checks','計画した確認'),
    }.items():
        if key in scope:lines+=['- '+t(*names)+': '+str(scope[key])]
    if report.get('descriptions'):
        d=report['descriptions'];lines+=['','## '+t('数值描述','Numeric descriptions','数値の記述'),'',
          t('数值对象：','Numeric objects: ','数値項目：')+str(d['record_count'])+'; '+t('表格：','Tables: ','表：')+str(d['table_count']),
          t('最小值：','Minimum: ','最小値：')+str(d['minimum'])+'; '+t('最大值：','Maximum: ','最大値：')+str(d['maximum']),
          generated(d['interpretation']),'','| '+t('原样数值 | 原文位置 | 单位字面值','Exact value | Source location | Literal unit','原表記の数値 | 原文位置 | 単位の原表記')+' |','| --- | --- | --- |']
        for r in report['records']:lines.append(f'| {r["raw"]} | L{r["source"]["line"]}:C{r["source"]["column"]} | {r["unit_literal"]} |')
    if report.get('numeric_blocks'):
        lines+=['','## '+t('数值列与格式摘要','Numeric columns and formats','数値列と形式の要約'),'']
        for c in report['numeric_blocks']['table_columns']:
            lines += ['- L'+str(c['header_line'])+' / '+c['header_literal']+': '+str(c['record_count'])+'; '+str(c['minimum'])+' — '+str(c['maximum'])]
        lines+=[t('字面加减对：','Literal plus/minus pairs: ','原表記のプラスマイナス対：')+str(len(report['numeric_blocks']['plus_minus_pairs'])),t('未据此推断 SD/SE 类型。','The SD/SE type is not inferred from this notation.','この表記から SD/SE の種類を推定していません。')]
    global_review=scope.get('global_relationship_check')
    if global_review:
        lines+=['','## '+t('跨章节关系检查','Cross-section relationship review','章をまたぐ関係の確認'),'',global_review['status'],'']
        for relation in global_review.get('checks',[]):
            lines += [relation['relation'],relation['reasoning'],'']
            for c in relation['citations']:lines+=['> L'+str(c['line'])+': '+c['quote'],'']
    labels={'COMPATIBLE':('给定条件下相容','Compatible under the conditions','条件下で整合'),'INCONSISTENT':('给定条件下不相容','Inconsistent under the conditions','条件下で不整合'),'INSUFFICIENT_INFORMATION':('信息不足','Insufficient information','情報不足'),'INPUT_ERROR':('输入问题','Input issue','入力問題'),'NOT_APPLICABLE':('不适用','Not applicable','適用外')}
    for check in report.get('checks',[]):
        lines+=['','### '+check['rule_id']+' · '+t(*labels[check['status']]),'']
        if check.get('calculation',{}).get('formula'):lines += [t('计算：','Calculation: ','計算：')+check['calculation']['formula']]
        if check.get('missing'):lines += [t('缺少参数：','Missing parameters: ','不足する条件：')+', '.join(check['missing'])]
        if check.get('reason'):lines += [generated(check['reason'])]
    for f in report.get('findings',[]):
        lines+=['','### '+(f.get('claim') or f['finding_id']),'',generated(f.get('reasoning') or f.get('statement','')),'']
        if f.get('alternative_explanation'):lines += [t('解释与反例：','Explanations and counterevidence: ','説明と反証：')+generated(f['alternative_explanation'])]
        if f.get('escalation_limited'):lines += [t('分级受限说明：','Classification limit: ','区分の制限：')+f['escalation_limited']]
        for r in f.get('citations',[]):lines+=['> L'+str(r['line'])+': '+r['quote'],'']
    # Full records, machine reasons, formula definitions and provenance stay literal.
    lines+=['','## '+t('原始计算与范围记录','Calculation and scope records','計算と確認範囲の記録'),'','```json',json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2),'```','']
    return '\n'.join(lines)
