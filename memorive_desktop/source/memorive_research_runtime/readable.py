"""One readable projection of source metadata, links and relation evidence."""
import re
from .metadata_v3 import links

def text(value):
    return str(value or '').replace('<','&lt;').replace('>','&gt;')

def discovery_markdown(topic,recommendations,failures,result=None,language=None):
    from memorive_language.discovery import label as select_label
    label=lambda cn,en:select_label(language,cn,en)
    lines=['# '+label('外部文献','External literature')+' · '+text(topic),'']
    if result:
        options=result.get('discovery_options',{})
        lines += [label('查询目标','Query objective')+': '+label('最新文献' if options.get('objective')=='latest' else '高度相关文献',options.get('objective','related')),'']
        window=options.get('publication_window')
        if window: lines += [label('首次发表日期范围','First publication window')+': '+window['from']+' — '+window['until'],'']
        lines += [label('新增题录 / 新版本 / 新方向关联','New works / versions / direction relations')+': '+
            ' / '.join(str(result.get(k,0)) for k in ('new_work_count','new_version_count','new_relation_count')),'']
        coverage=result.get('index_coverage',{})
        if any(coverage.get(k,{}).get('status')!='COMPLETE' for k in ('inbox','legacy')):
            lines += [label('旧资料身份索引仍在回填，查重覆盖尚不完整。','Legacy identity indexing is incomplete; duplicate coverage remains partial.'),'']
    for item in recommendations:
        lines += ['## '+str(item['position'])+'. '+text(item['title']),'',
            text('; '.join(item.get('authors',[]))) or label('作者未提供','Authors not provided'),'',
            '- '+label('来源','Sources')+': '+' / '.join(item['sources']),
            '- '+label('发表日期','Published')+': '+text(item.get('published_date') or label('未提供','Not provided'))+' · '+item.get('date_precision','UNKNOWN'),
            '- '+label('期刊或平台','Journal or platform')+': '+text(item.get('journal') or item.get('platform') or label('未提供','Not provided')),
            '- '+label('文献类型','Record type')+': '+text(item.get('record_type') or 'unknown'),
            '- '+label('标识','Identifier')+': '+text(item['identity_key'])]
        if item.get('updated_date'): lines += ['- '+label('更新时间','Updated')+': '+text(item['updated_date'])]
        for link in item.get('links') or links(item['identifiers'],item.get('locations',[])):
            safe=link['url'].replace('>','%3E').replace('<','%3C')
            lines += ['- ['+text(link['label'])+'](<'+safe+'>)']
        if item.get('direction_name'): lines += ['- '+label('方向','Direction')+': '+text(item['direction_name'])+' · r'+str(item['direction_revision'])]
        lines += ['',label('推荐理由','Recommendation reason')+': '+text(item.get('recommendation_reason') or label('来源查询召回，相关性未进一步评估。','Source query recall; further relevance not assessed.')),'']
        relation=item.get('relation_evidence',{})
        if relation: lines += [label('依据范围','Evidence scope')+': '+relation.get('evidence_scope','UNKNOWN')+' · '+label('未读取全文','Full text not read'),'']
        if item.get('abstract'): lines += ['### '+label('来源摘要（原文）','Source abstract (original language)'),'',text(item['abstract']),'']
        else: lines += [label('来源未提供摘要。','The source did not provide an abstract.'),'']
        comparison=item.get('relation_comparison',{})
        if comparison.get('indexed_match',{}).get('possible_duplicates'):
            lines += [label('存在同标题记录，身份尚不足以自动合并。','A same-title record exists; identity is insufficient for automatic merging.'),'']
    if not recommendations: lines += [label('本次没有符合条件的新候选。','No eligible new candidates in this run.'),'']
    if failures:
        lines += ['### '+label('来源与覆盖','Source coverage'),'']
        lines += ['- '+text(f.get('source',''))+': '+text(f.get('error_code','SOURCE_INCOMPLETE')) for f in failures]
        lines += ['']
    return '\n'.join(lines)
