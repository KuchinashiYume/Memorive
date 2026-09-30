"""Metadata-based recommendation copy; no new source or model requests."""
from .text import choose

JA = {'外部文献':'外部文献','查询目标':'検索の目的','最新文献':'最新文献','高度相关文献':'関連性の高い文献',
      '首次发表日期范围':'初回公開日の範囲','新增题录 / 新版本 / 新方向关联':'新規文献 / 新バージョン / 新しい研究方向との関連',
      '旧资料身份索引仍在回填，查重覆盖尚不完整。':'既存資料の識別索引を補完中のため、重複確認の範囲はまだ不完全です。',
      '作者未提供':'著者情報なし','来源':'ソース','发表日期':'公開日','未提供':'情報なし','期刊或平台':'雑誌またはプラットフォーム',
      '文献类型':'文献の種類','标识':'識別子','更新时间':'更新日時','方向':'研究方向','推荐理由':'推薦理由',
      '来源查询召回，相关性未进一步评估。':'ソース検索で取得した候補です。関連性の追加評価はしていません。',
      '依据范围':'根拠の範囲','未读取全文':'全文は未読','来源摘要（原文）':'ソースの要旨（原文）',
      '来源未提供摘要。':'ソースから要旨が提供されていません。',
      '存在同标题记录，身份尚不足以自动合并。':'同名の記録がありますが、自動統合に必要な識別情報が不足しています。',
      '本次没有符合条件的新候选。':'今回は条件を満たす新しい候補がありません。','来源与覆盖':'ソースと検索範囲'}

def label(language, zh, en):
    return choose(language,zh,en,JA[zh])

def reason(row, language):
    relation=row['relation_evidence']; terms=relation['matched_keywords'] or relation['matched_query_terms']
    scope=choose(language,'标题与摘要' if row.get('abstract') else '标题','title and abstract' if row.get('abstract') else 'title','題名と要旨' if row.get('abstract') else '題名')
    text=choose(language,'与「{name}」在{scope}中共有主题词：{terms}。','Shares topic terms with “{name}” in the {scope}: {terms}.','「{name}」と{scope}に共通する主題語：{terms}。').format(name=row.get('direction_name',''),scope=scope,terms=', '.join(terms[:6]) or choose(language,'证据不足','insufficient evidence','証拠不足'))
    if relation['seed_comparisons']:
        seed=relation['seed_comparisons'][0]
        text+=choose(language,'与种子「{seed}」共有 {terms}。',' Shared with seed “{seed}”: {terms}.','種文献「{seed}」との共通語：{terms}。').format(seed=seed['seed_title'] or seed['seed_id'],terms=', '.join(seed['shared_terms'][:5]))
    if not row.get('abstract'):
        text+=choose(language,'来源未提供摘要，关系仅供初筛。',' No abstract is available; this relation supports initial screening only.','要旨がないため、この関連は一次選別の参考に限ります。')
    text+=choose(language,'共同术语不能证明同一机制。',' Shared terms do not establish a shared mechanism.','共通の用語だけでは同一の機序を証明できません。')
    return text
