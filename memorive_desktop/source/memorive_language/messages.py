"""Typed system copy. Projection has no writes, model calls or notification effects."""
from copy import deepcopy
from .text import choose

VERSION = 'MemoMessages-v1'

def content(key, **params):
    value = dict(catalog_version=VERSION, message_key=key, parameters=params)
    validate(value)
    return value

PARAMS = {
    'system': {'severity': str},
    'runtime': {'display_name': str, 'core': bool, 'state': str, 'failure_kind': str,
                'node': str, 'retained': int, 'original': int, 'dropped': int},
    'discovery.ready': {'topic': str, 'works': int, 'versions': int, 'relations': int},
    'report.ready': {'period': str, 'start': str, 'end': str, 'revision': int},
    'ai.ready': {'start': str, 'end': str, 'partial': bool},
    'research.failed': {'kind': str, 'title': str, 'stage': str},
}

def validate(value):
    if value is None: return None
    if not isinstance(value, dict) or set(value) != {'catalog_version','message_key','parameters'}:
        raise ValueError('MESSAGE_CONTENT_FIELDS_INVALID')
    if value['catalog_version'] != VERSION or value['message_key'] not in PARAMS:
        raise ValueError('MESSAGE_CONTENT_VERSION_INVALID')
    params = value['parameters']; shape = PARAMS[value['message_key']]
    if not isinstance(params, dict) or params.keys() != shape.keys():
        raise ValueError('MESSAGE_PARAMETERS_INVALID')
    for key, typ in shape.items():
        if type(params[key]) is not typ or (typ is str and len(params[key]) > 512) or (typ is int and params[key] < 0):
            raise ValueError('MESSAGE_PARAMETER_INVALID')
    return deepcopy(value)

def period_name(period, language):
    return choose(language, {'daily':'日报','weekly':'周报','monthly':'月报'},
                  {'daily':'Daily report','weekly':'Weekly report','monthly':'Monthly report'},
                  {'daily':'日報','weekly':'週報','monthly':'月報'}).get(period, period)

def render(value, language):
    validate(value); p=value['parameters']; key=value['message_key']
    def t(zh,en,ja): return choose(language,zh,en,ja)
    if key == 'system':
        title=t({'RED':'任务需要注意','YELLOW':'任务等待处理','GREEN':'任务已完成','BLUE':'有新的应用内消息'},
                {'RED':'Task needs attention','YELLOW':'Task awaiting action','GREEN':'Task completed','BLUE':'New message'},
                {'RED':'確認が必要なタスク','YELLOW':'対応待ちのタスク','GREEN':'タスクが完了しました','BLUE':'新しいメッセージ'}).get(p['severity'],'Memorive')
        body=t('请在应用内查看详细信息。','Open the app for details.','詳細はアプリで確認してください。')
    elif key == 'runtime':
        name=p['display_name'] or t('文献处理' if p['core'] else '精炼对话','Document processing' if p['core'] else 'Conversation refinement','文献処理' if p['core'] else '会話の精錬')
        verb={'RUNNING':t('正在处理','in progress','処理中'),'SUCCEEDED':t('已完成','completed','完了'),'FAILED':t('失败','failed','失敗'),'CANCELLED':t('已取消','cancelled','キャンセル済み')}[p['state']]
        title=name+' · '+verb
        if p['state']=='RUNNING':
            body=t('正在处理，完成后可查看结果。','Processing is in progress. Results will be available when it finishes.','処理中です。完了後に結果を確認できます。')
        elif p['state']=='SUCCEEDED':
            body=t('产物已生成，可在资料库查看。','The output is ready in the library.','生成結果をライブラリで確認できます。')
            if p['original']:
                body+=t('分析上下文保留 {retained} / {original} 个估算 tokens，丢弃 {dropped} 个证据块。可更换更大上下文模型后从整理分析材料节点重试。',
                        ' The analysis context retained {retained} / {original} estimated tokens and omitted {dropped} evidence blocks. To include more evidence, select a model with a larger context and retry from context preparation.',
                        '分析用コンテキストには推定 {original} トークンのうち {retained} トークンを保持し、証拠ブロック {dropped} 件を除外しました。より大きなコンテキストのモデルを選び、分析資料の準備から再実行できます。').format(**p)
        elif p['state']=='CANCELLED':
            body=t('任务已由用户取消。','The user cancelled this task.','ユーザーがタスクをキャンセルしました。')
        else:
            body={'length':t('输出不完整。重新处理时将分段生成，可先调整模型。','The output is incomplete. Processing will be split on retry; you can change the model first.','出力が不完全です。再実行時は分割して生成します。先にモデルを変更できます。'),
                  'capacity':t('向量处理容量不足。可更换向量模型后分段重试。','Embedding capacity was exceeded. Change the embedding model and retry in segments.','ベクトル処理の容量を超えました。モデルを変更して分割再実行できます。')}.get(p['failure_kind'],t('未生成有效结果。请查看任务，可调整模型后重试。','No valid result was produced. Open the task for details; you can change the model and retry.','有効な結果を生成できませんでした。タスクの詳細を確認し、モデルを変更して再実行できます。'))
            if p['node']:body=t('出错节点：','Failed node: ','失敗したノード：')+p['node']+'。 '+body
    elif key=='discovery.ready':
        title=t('外部文献','External literature','外部文献')+' · '+p['topic']
        body=t('检索完成，保存 {works} 篇新增题录、{versions} 个新版本、{relations} 个新方向关联。请前往资料库查看正文。',
               'Search complete: {works} new work record(s), {versions} new version(s), and {relations} new direction relation(s) saved. Read the report in the library.',
               '検索が完了しました。新規文献 {works} 件、新バージョン {versions} 件、研究方向との新しい関連 {relations} 件を保存しました。本文はライブラリで確認できます。').format(**p)
    elif key=='report.ready':
        title=period_name(p['period'],language)+' · '+p['start']+' — '+p['end']+' · r'+str(p['revision'])
        body=t('报告已生成并保存。请前往资料库查看正文。','The report is saved. Read it in the library.','レポートを生成して保存しました。本文はライブラリで確認できます。')
    elif key=='ai.ready':
        title=t('AI 近况','AI updates','AI の近況')+' · '+p['start']+' — '+p['end']
        body=t('AI 近况已生成并保存。请前往资料库查看正文。','AI updates are saved. Read the report in the library.','AI の近況を生成して保存しました。本文はライブラリで確認できます。')
        if p['partial']:body+=' '+t('部分来源未能读取。','Some sources could not be read.','一部のソースを読み取れませんでした。')
    elif key=='research.failed':
        title=t('任务需要注意','Task needs attention','タスクの確認が必要です')+' · '+p['title']
        body=t('结果已生成，但投递未完成。请修复投递，无需重新生成。','The result is ready, but delivery is incomplete. Repair delivery without regenerating.','結果は生成済みですが、配信が完了していません。再生成せずに配信を修復してください。') if p['stage']=='PUBLISHING' else t('未生成有效结果。请查看任务及来源配置后重试。','No valid result was produced. Check the task and source configuration before retrying.','有効な結果を生成できませんでした。タスクとソース設定を確認してから再実行してください。')
        body+=' '+t('阶段：','Stage: ','段階：')+p['stage']
    return dict(title=title[:180],summary=body[:320],body=body[:4000])

def project(record, language):
    result=dict(record)
    if record.get('message_content'):result.update(render(record['message_content'],language))
    return result

def runtime_failure_kind(error_code, failed_node_id=''):
    # Shared by legacy safe copy and localized projection: preserve the original aliases.
    marker=f'{failed_node_id} {error_code}'.upper()
    if any(needle in marker for needle in ('FINISH_REASON_LENGTH','FINISH_REASON=LENGTH','OUTPUT_TRUNCAT','仍被截断','8192_TO_16384','LENGTH_EXHAUSTED')):return 'length'
    if any(needle in marker for needle in ('PHYSICAL BATCH SIZE','INPUT_TOO_LARGE','TOO LARGE TO PROCESS')):return 'capacity'
    return 'unknown'

def runtime_content(event, *, core, state, failed_node_id, raw_error):
    failure=runtime_failure_kind(raw_error,failed_node_id)
    pack=event.get('context_pack') or {}
    return content('runtime',display_name=str(event.get('display_name') or '')[:120],core=core,state=state,
                   failure_kind=failure,node=failed_node_id,retained=int(pack.get('retained_estimated_tokens') or 0) if pack.get('truncated') else 0,
                   original=int(pack.get('original_estimated_tokens') or 0) if pack.get('truncated') else 0,
                   dropped=int(pack.get('dropped_evidence_block_count') or 0) if pack.get('truncated') else 0)
