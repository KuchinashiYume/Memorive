"""Readable metadata projection; original-language titles and abstracts, no LLM."""
import re
from urllib.parse import urlsplit

def discovery_markdown(topic, recommendations, failures):
    zh=bool(re.search(r'[\u3400-\u9fff]',topic))
    label=lambda cn,en:cn if zh else en
    lines=['# '+label('外部文献','External literature')+' · '+topic,'',
           label('文献元数据推荐；未下载全文，尚未经人工复核。','Metadata recommendations; full texts have not been downloaded or reviewed.'),'']
    for item in recommendations:
        lines += ['## '+str(item['position'])+'. '+item['title'],'',
                  '; '.join(item['authors']),'',
                  '- '+label('来源','Sources')+': '+' / '.join(item['sources']),
                  '- '+label('出版日期','Published date')+': '+(item['published_date'] or label('未提供精确日期','Exact date not provided')),
                  '- '+label('标识','Identifier')+': '+item['identity_key'],'']
        if item.get('abstract'):lines += [item['abstract'],'']
        if item.get('direction_name'):
            lines += ['- '+label('研究方向','Research direction')+': '+item['direction_name'],
                '- '+label('召回依据','Recall evidence')+': '+label('已保存方向的来源查询；不是语义匹配分数。','Saved direction query; not a semantic similarity score.'),'']
        if item.get('relation_comparison'):
            comparison=item['relation_comparison']
            lines += [label('本地关系','Local relations')+': '+(
                label('相同标识','Exact identifier') if comparison['library_new']=='FALSE' else
                label('身份资料不完整，新颖性未评估','Incomplete identity coverage; novelty not assessed') if comparison['library_new']=='UNKNOWN' else
                label('当前结构化标识范围内未发现重复','No duplicate in the current structured identifier scope')),
                label('主题、方法、对象相似度与引文关系尚未评估。','Topic, method, object similarity and citation relations are not assessed.'),'']
        for location in item.get('locations',[]):
            url=location.get('locator','');parts=urlsplit(url)
            if parts.scheme=='https' and parts.hostname and not parts.username:
                safe=url.replace('>','%3E').replace('<','%3C').replace('\n','').replace('\r','')
                lines += ['['+label('来源页面','Source record')+'](<'+safe+'>)','']
    if not recommendations:lines += [label('本次没有符合条件的新候选。','No eligible new candidates in this run.'),'']
    if failures:lines += [label('部分来源未完成，结果可能不完整。','Some sources were unavailable; results may be incomplete.'),'']
    return '\n'.join(lines)
