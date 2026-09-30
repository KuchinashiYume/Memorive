"""Deterministic E1 explanations; claim/source text and verdict identifiers stay literal."""
from .text import choose

FACETS={
 'conditions':('研究对象与条件','Objects and conditions','対象と条件'),
 'methods':('方法与比较基线','Methods and baselines','方法と比較基準'),
 'sample':('样本与独立性','Samples and independence','標本と独立性'),
 'results':('结果与不确定性','Results and uncertainty','結果と不確実性'),
 'limitations':('局限与相反证据','Limitations and contrary evidence','限界と反証'),
 'question':('本次问题','Current question','今回の質問'),
}
COPY={
 '部分数值未在所引片段中精确出现，需回查单位、口径与上下文。':('Some numbers do not occur exactly in the cited excerpts. Recheck units, definitions and context.','引用断片に完全一致しない数値があります。単位、定義、文脈を確認してください。'),
 '包含强结论或推广表述，请特别核对对象、条件与结论强度。':('This includes a strong or general claim. Recheck its objects, conditions and strength.','強い結論や一般化が含まれます。対象、条件、結論の強さを確認してください。'),
 '尚未执行语义审核。':('Semantic review has not run.','意味内容の確認は未実施です。'),
 '来源已变化或暂时不可回查，需重新审核。':('The source changed or cannot currently be checked. Review it again.','出典が変更されたか、現在参照できません。再確認が必要です。'),
 '逐句列出待核对内容；主张拆分、表格含义与组合推论是否完整仍待人工核查。':('Claims are listed sentence by sentence. People still need to check claim separation, table meaning and combined inferences.','確認対象を文ごとに示しています。主張の分割、表の意味、複合的な推論の網羅性は人による確認が必要です。'),
 '局部修订；本版主张和直接依赖关系需重新审核。':('Local revision: review this version’s claims and direct dependencies again.','部分的な修正です。この版の主張と直接の依存関係を再確認してください。'),
 '仅在本次选定材料中进行本地补查。新片段尚未进入原回答；缺口不因检索命中自动消失。':('This local search uses only the selected materials. New excerpts have not entered the original answer; a search hit does not automatically close an evidence gap.','今回選択した資料だけをローカルで追加検索しました。新しい断片は元の回答に未反映です。検索結果が得られても証拠の不足が自動的に解消するわけではありません。'),
}
def generated(value,language):
    if value in COPY:return choose(language,value,*COPY[value])
    prefix='审核给出的支持关系未通过原文绑定或限定检查：'
    if value.startswith(prefix):
        return choose(language,prefix,'The proposed support failed source binding or scope checks: ','提示された支持関係は原文との対応または範囲の確認を通過しませんでした：')+value[len(prefix):]
    return value

def followup(question,facet,language):
    label=choose(language,*FACETS[facet])
    return question+'\n'+choose(language,'请重点补查“'+label+'”，列明原文条件、反例和仍未提供的信息。','Search specifically for “'+label+'”; list source conditions, counterevidence and information still missing.','「'+label+'」を重点的に追加検索し、原文の条件、反証、まだ提示されていない情報を列挙してください。')
