"""Deterministic readable projection; source text and JSON evidence are untouched."""
import re
from document_processing.naming import display_title, file_name

def readable_name(artifact_id, title):
    return file_name('Refinement',artifact_id.removeprefix('refinement-product-'),display_title(title),'md')

def render(draft, digest):
    # UI title prefixes do not determine the source language.
    zh=any(re.search(r'[\u3400-\u9fff]',str(x.get('candidate_text',''))) for x in draft['items'])
    label=lambda cn,en:cn if zh else en
    title=re.sub(r'^精炼对话\s*[·・.]\s*','',draft['display_name'])
    lines=['# '+title,'',label('会话精炼成品 · 待人工复核','Conversation refinement · Human review required'),'',
           label('以下条目保留原始精炼内容；不代表已经作出真实性判断。','The following items preserve the refinement output; no final truth judgment is implied.'),'']
    for i,item in enumerate(draft['items'],1):
        lines += ['## '+label('条目','Item')+' '+str(i),'',item['candidate_text'],'',
            '- '+label('类型','Type')+': '+item['refinement_type'],
            '- '+label('范围','Scope')+': '+item['scope'],
            '- '+label('证据状态','Evidence status')+': '+item['evidence_state'],
            '- '+label('来源消息','Source messages')+': '+', '.join(item['message_refs']),'']
        if item['limitations']:
            lines += ['### '+label('限制','Limitations'),'']
            lines += ['- '+text for text in item['limitations']]
            lines += ['']
    lines += ['---','',label('来源与校验','Source and verification'),'',
        '- '+label('来源会话','Source session')+': '+draft['local_projection_id'],
        '- '+label('生成时间','Created at')+': '+draft['created_at'],
        '- Source SHA256: '+draft['source_snapshot_sha256'],
        '- Draft SHA256: '+digest,'']
    return '\n'.join(lines)
