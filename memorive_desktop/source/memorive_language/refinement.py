from .text import choose

JA={'会话精炼成品 · 待人工复核':'会話の精錬結果 · 人による確認待ち',
    '以下条目保留原始精炼内容；不代表已经作出真实性判断。':'以下の項目は精錬結果を保持したもので、真偽を判定したものではありません。',
    '条目':'項目','类型':'種類','范围':'対象範囲','证据状态':'証拠の状態','来源消息':'元のメッセージ','限制':'制限',
    '来源与校验':'出典と検証','来源会话':'元の会話','生成时间':'生成日時'}

def label(language,zh,en):return choose(language,zh,en,JA[zh])

ENUMS={
 'USER_DECISION_CLAIM':('用户决策陈述','User decision claim','ユーザーの決定に関する記述'),
 'CONSTRAINT_OR_PREFERENCE':('约束或偏好','Constraint or preference','制約または設定'),
 'REPOSITORY_VERIFIABLE_FACT_CLAIM':('可由仓库核验的事实主张','Repository-verifiable claim','リポジトリで確認可能な主張'),
 'REUSABLE_METHOD':('可复用方法','Reusable method','再利用可能な方法'),
 'AI_SUGGESTION':('AI 建议','AI suggestion','AI の提案'),
 'HYPOTHESIS':('假设','Hypothesis','仮説'),
 'OPEN_QUESTION':('未解决问题','Open question','未解決の問い'),
 'FAILED_ATTEMPT':('失败尝试','Failed attempt','失敗した試み'),
 'OVERTURNED_CONCLUSION':('被推翻的结论','Overturned conclusion','覆された結論'),
 'PROVENANCE_ONLY':('仅作来源记录','Provenance only','出典の記録のみ'),
 'FORBIDDEN_REUSE':('禁止复用','Reuse forbidden','再利用禁止'),
 'UNVERIFIED_CLAIM':('未经核验的主张','Unverified claim','未検証の主張'),
 'FORBIDDEN':('禁止使用','Forbidden','利用禁止'),
 'REQUIRES_INDEPENDENT_EVIDENCE_OR_USER_DECISION':('需要独立证据或用户决策','Independent evidence or a user decision is required','独立した証拠またはユーザーの判断が必要'),
 'CONTEXT_INSUFFICIENT':('上下文不足','Insufficient context','コンテキスト不足'),
 'SELECTED_CONVERSATIONS':('选定会话','Selected conversations','選択した会話'),
 'ALL_HISTORY_BOUNDED':('有界历史范围','Bounded history','限定された履歴範囲'),
}

def enum(language,value):
    return choose(language,*ENUMS[value]) if value in ENUMS else value
