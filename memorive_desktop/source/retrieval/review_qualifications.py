"""Carry unresolved admitted-Card review facts without declaring a scientific verdict."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json


def card_review_qualifications(card, *, paper_id=None):
    if not card or not card.get('omissions'):
        return ()
    declared = card.get('paper_id') or (card.get('source_anchor') or {}).get('paper_id')
    if not declared or (paper_id and declared != paper_id):
        raise ValueError('CARD_REVIEW_QUALIFICATION_SCOPE_MISMATCH')
    projection = json.dumps(card, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(projection.encode('utf8')).hexdigest()
    out = []
    for row in card['omissions']:
        if not isinstance(row, dict) or (row.get('paper_id') and row['paper_id'] != declared):
            raise ValueError('CARD_REVIEW_QUALIFICATION_SCOPE_MISMATCH')
        removed = row.get('removed_value')
        if isinstance(removed, dict):
            # Model/scientific content only; old localized application warnings
            # and mutable runtime paths are not evidence sent to the producer.
            removed = {k:deepcopy(removed[k]) for k in ('value','unit','metric','quote','chunk_id') if k in removed}
        out.append({'paper_id':declared, 'card_projection_sha256':digest,
                    'location':row.get('location'), 'removed_claim':deepcopy(removed),
                    'status':row.get('status'), 'reason':row.get('reason'),
                    'rules':row.get('final_rules') or row.get('initial_rules') or [],
                    'related_anchors':deepcopy(row.get('related_anchors') or []),
                    'initial_report_id':row.get('initial_report_id'),
                    'final_report_id':row.get('final_report_id')})
    return tuple(out)


def render_review_context(qualifications):
    if not qualifications:
        return ''
    template = (Path(__file__).parents[1] / 'model_gateway/prompts/analysis/card_review_qualifications_v1.md').read_text('utf8')
    return template.replace('__REVIEW_JSON__', json.dumps(qualifications, ensure_ascii=False, sort_keys=True))


def review_limitations(qualifications, language):
    labels = {
        'en':('Unresolved Card review qualification',
              'This item was omitted after review and is not verified. Its occurrence in the source does not resolve the recorded concern. Recheck the source and report before adopting it.',
              'Omitted content', 'Review report'),
        'zh':('卡片审核尚未解决的问题',
              '此项经审核移出卡片，尚未验证通过。原文中存在该内容不表示审核问题已解决，采用前需回查原文与审核记录。',
              '移出的内容', '审核记录'),
        'ja':('カード審査で未解決の事項',
              'この項目は審査後に除外され、未検証です。原文に存在しても指摘の解消を意味しません。採用前に原文と審査記録を確認してください。',
              '除外された内容', '審査記録'),
    }
    heading, caution, content, report = labels.get(language, labels['en'])
    return tuple(f"{heading}: {row['paper_id']} / {row.get('location')}. {caution} "
                 f"{content}: {json.dumps(row.get('removed_claim'), ensure_ascii=False, sort_keys=True)}. "
                 f"{report}: {row.get('final_report_id') or row.get('initial_report_id') or 'unavailable'}."
                 for row in qualifications)
