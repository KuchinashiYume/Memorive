"""The readable Markdown of a refinement product in the library; the JSON it is made from is untouched.

A v4 draft is a conversation brief (sessions/refinement/brief.py): overview, then one section per kind, each
item with the messages it cites (#n is message mn). A v3 draft, made before v2.00, lists one typed item per
message and keeps its old layout.
"""
import re
from memorive.platform.naming import display_title, file_name

# Section titles in brief.KINDS order (Chinese and English; Japanese comes from platform/language/refinement.py).
_SECTIONS = {'decision': ('已定事项', 'Decisions'), 'next_step': ('下一步', 'Next steps'),
             'result': ('结果与进展', 'Results and progress'), 'open_question': ('待确认', 'Open questions'),
             'risk': ('风险', 'Risks'), 'ai_suggestion': ('AI 建议 · 未采纳', 'AI suggestions · not adopted')}
_MOVED = {'decision': ('没有引用用户原话，从已定事项移来', 'Cites no user message; moved here from decisions'),
          'next_step': ('没有引用用户原话，从下一步移来', 'Cites no user message; moved here from next steps')}
# Notes for the user's edits while reviewing (library: refinement_edits_v1.json, by item index).
_ADOPTED = ('由你采纳（原为 AI 建议）', 'Adopted by you (an AI suggestion)')
_ANSWERED = ('由你确认（原为待确认）', 'Settled by you (an open question)')
_REWORDED = ('你改过措辞', 'Reworded by you')

def readable_name(artifact_id, title):
    return file_name('Refinement',artifact_id.removeprefix('refinement-product-'),display_title(title),'md')

def _render_brief(draft, digest, title, label, edits):
    brief = draft['brief']
    lines = ['# ' + title, '', label('会话纪要 · 待人工复核', 'Conversation brief · Human review required'), '',
             '## ' + label('概况', 'Overview'), '', brief['overview'], '']
    order = list(_SECTIONS)
    # (section, item, edit): an adopted suggestion or a settled question counts as a decision; the brief's order
    # holds within a section.
    rows = []
    for index, item in enumerate(brief['items']):
        edit = edits.get(str(index), {})
        rows.append(('decision' if edit.get('adopted') or edit.get('answer') else item['kind'], item, edit))
    shown = sorted(rows, key=lambda row: order.index(row[0]))
    kind = None
    for section, item, edit in shown:
        if section != kind:
            lines += ([''] if kind else []) + ['## ' + label(*_SECTIONS[section]), '']
            kind = section
        refs = ', '.join('#' + ref.removeprefix('m') for ref in item['message_refs'])
        text = edit.get('text') or item['text']
        if edit.get('answer'):
            text += ' → ' + edit['answer']
        lines.append(('- [ ] ' if kind == 'next_step' else '- ') + text + ' (' + refs + ')')
        if 'NUMBER_NOT_IN_SOURCE' in item['flags'] and not edit.get('text'):
            lines.append('  - ' + label('数字未在所引原话中找到', 'Number not found in the cited messages'))
        if item.get('moved_from') in _MOVED:
            lines.append('  - ' + label(*_MOVED[item['moved_from']]))
        for key, note in (('adopted', _ADOPTED), ('answer', _ANSWERED), ('text', _REWORDED)):
            if edit.get(key):
                lines.append('  - ' + label(*note))
    lines += ['', '---', '', label('来源与校验', 'Source and verification'), '',
              '- ' + label('来源会话', 'Source session') + ': ' + draft['local_projection_id'],
              '- ' + label('生成时间', 'Created at') + ': ' + draft['created_at'],
              '- Source SHA256: ' + draft['source_snapshot_sha256'],
              '- Draft SHA256: ' + digest, '']
    return '\n'.join(lines)


def render(draft, digest, edits=None):
    # UI title prefixes do not determine the source language.
    from memorive.platform.language.refinement import label as select_label
    language=draft.get('language_context')
    title=re.sub(r'^(?:精炼对话|Conversation refinement|会話の精錬)\s*[·・.]\s*','',draft['display_name'])
    if draft.get('schema_version') == 'DesktopSessionRefinementDraft-v4':
        if language is None:
            language='zh-CN' if re.search(r'[\u3400-\u9fff]', draft['brief']['overview']) else 'en-US'
        return _render_brief(draft, digest, title, lambda cn, en: select_label(language, cn, en), edits or {})
    if language is None:
        # Reproduce the legacy template for existing drafts without rewriting them.
        language='zh-CN' if re.search(r'[\u3400-\u9fff]', ''.join(item.get('candidate_text','') for item in draft['items'])) else 'en-US'
    from memorive.platform.language.refinement import enum as render_enum
    enum_label=render_enum if draft.get("language_context") else lambda lang,value:value
    label=lambda cn,en:select_label(language,cn,en)
    lines=['# '+title,'',label('会话精炼成品 · 待人工复核','Conversation refinement · Human review required'),'',
           label('以下条目保留原始精炼内容；不代表已经作出真实性判断。','The following items preserve the refinement output; no final truth judgment is implied.'),'']
    for i,item in enumerate(draft['items'],1):
        lines += ['## '+label('条目','Item')+' '+str(i),'',item['candidate_text'],'',
            '- '+label('类型','Type')+': '+enum_label(language,item['refinement_type']),
            '- '+label('范围','Scope')+': '+enum_label(language,item['scope']),
            '- '+label('证据状态','Evidence status')+': '+enum_label(language,item['evidence_state']),
            '- '+label('来源消息','Source messages')+': '+', '.join(item['message_refs']),'']
        if item['limitations']:
            lines += ['### '+label('限制','Limitations'),'']
            lines += ['- '+enum_label(language,text) for text in item['limitations']]
            lines += ['']
    lines += ['---','',label('来源与校验','Source and verification'),'',
        '- '+label('来源会话','Source session')+': '+draft['local_projection_id'],
        '- '+label('生成时间','Created at')+': '+draft['created_at'],
        '- Source SHA256: '+draft['source_snapshot_sha256'],
        '- Draft SHA256: '+digest,'']
    return '\n'.join(lines)
