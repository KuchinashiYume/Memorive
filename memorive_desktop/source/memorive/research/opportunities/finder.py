"""Finding research opportunities in the fact sheets of processed papers (facts.py).

  groups(papers)          papers on related questions, objects and measures together, at most
                          GROUP_SIZE per group; each group is one model request
  prompt(group, ...)      the request: prompts/research_opportunities, with the group's fact sheets
  checked(response, ...)  the model's opportunities that pass the checks below; the others are
                          dropped with the reason
  markdown(...)           the readable result saved in the library

The model compares, the code checks. An opportunity is kept only when every fact id it cites is in
its group, a divergence or condition difference cites at least two papers, and every number in its
title, question, reasoning and dimension notes is reported by a fact of a paper it cites. A number
the model took from a fact of such a paper without citing that fact adds the fact to the evidence,
so the reader sees where each number comes from. Each kept opportunity gets a fingerprint from its
kind and the facts it rests on, so finding it again in a later run is recognised as the same
opportunity.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from memorive.models.gateway.prompts import PromptRegistry
from memorive.platform.language.text import choose
from memorive.research.common import sha
from memorive.research.opportunities.facts import fact_key
from memorive.research.reports.renderer import MARKUP
from memorive.research.workspace.evidence import tokens

PROMPTS = PromptRegistry(Path(__file__).resolve().parents[2] / 'models' / 'gateway' / 'prompts')
MODULE = 'research_opportunities'
GROUP_SIZE = 12
KINDS = ('divergence', 'condition_difference', 'gap')
DIMENSIONS = ('object', 'method', 'sample', 'metric', 'condition', 'result')
TEXT_FIELDS = ('title', 'question', 'reasoning', 'suggestion', 'limitations')
FACT_CHARS = 600
GROUPING_FIELDS = {'research_question', 'research_object', 'method', 'key_data'}

_IDS = {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}}
SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['opportunities'], 'properties': {
    'opportunities': {'type': 'array', 'maxItems': 8, 'items': {
        'type': 'object', 'additionalProperties': False,
        'required': ['kind', *TEXT_FIELDS, 'dimensions', 'fact_ids'],
        'properties': {
            'kind': {'type': 'string', 'enum': list(KINDS)},
            **{field: {'type': 'string'} for field in TEXT_FIELDS},
            'dimensions': {'type': 'array', 'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['dimension', 'status', 'note', 'fact_ids'],
                'properties': {'dimension': {'type': 'string', 'enum': list(DIMENSIONS)},
                               'status': {'type': 'string', 'enum': ['same', 'different', 'unclear']},
                               'note': {'type': 'string'}, 'fact_ids': _IDS}}},
            'fact_ids': _IDS}}}}}


def _terms(paper: dict) -> set[str]:
    return set(tokens(' '.join(f['text'] for f in paper['facts'] if f['field'] in GROUPING_FIELDS)))


def groups(papers: list[dict], size: int = GROUP_SIZE) -> list[list[dict]]:
    """Up to `size` papers per group: each group starts from the first paper left and takes the
    papers whose question, object, method and measures share the most words with it."""
    left = list(papers)
    terms = {paper['paper']: _terms(paper) for paper in left}
    result = []
    while left:
        seed = left.pop(0)

        def overlap(paper):
            a, b = terms[seed['paper']], terms[paper['paper']]
            return len(a & b) / len(a | b) if a | b else 0.0
        left.sort(key=overlap, reverse=True)
        result.append([seed, *left[:size - 1]])
        left = left[size - 1:]
    return result


def prompt(group: list[dict], *, focus: str, language_instruction: str, known: list[dict] = ()) -> str:
    """The request for one group. known: the opportunities found in these papers before ({kind,
    title, papers}, papers by their numbers here), which the model is asked not to return again."""
    sheets = [{'paper': paper['paper'], 'title': paper['title'],
               'facts': [{'id': f['id'], 'field': f['field'], 'text': f['text'][:FACT_CHARS]} for f in paper['facts']]}
              for paper in group]
    template = PROMPTS.get(MODULE).body
    return (template.replace('{{language_instruction}}', language_instruction)
            .replace('{{focus}}', focus.strip() or '-')
            .replace('{{known}}', json.dumps(list(known), ensure_ascii=False, indent=1) if known else '-')
            .replace('{{papers}}', json.dumps(sheets, ensure_ascii=False, indent=1)))


# Numbers as written in facts and in the model's text: 1,200 / 0.05 / 12.5 / 30%
_NUMBER = re.compile(r'\d+(?:,\d{3})*(?:\.\d+)?|\.\d+')
_FACT_ID = re.compile(r'\bP\d+(?:\.\d+)?\b')


def _numbers(text: str) -> set[float]:
    return {float(match.replace(',', '')) for match in _NUMBER.findall(_FACT_ID.sub(' ', text))}


def _reported(fact: dict) -> set[float]:
    return _numbers(fact['text'] + ' ' + (fact['quote'] or ''))


def _unsupported_numbers(text: str, facts: list[dict]) -> list[float]:
    """The numbers in the model's text that none of the facts reports. Whole numbers up to ten
    without a percent sign are left out: they are counts and ordinals ("two papers", "step 3")."""
    known = set().union(*(_reported(f) for f in facts))
    plain = _FACT_ID.sub(' ', text)
    found = []
    for match in _NUMBER.finditer(plain):
        value = float(match.group().replace(',', ''))
        whole = '.' not in match.group() and value <= 10
        percent = plain[match.end():match.end() + 1] in {'%', '％'}
        if value not in known and not (whole and not percent):
            found.append(value)
    return found


def _number_sources(item: dict, facts_by_id: dict[str, tuple[dict, dict]]) -> list[str] | None:
    """The facts to add so that every number in the opportunity's text is in its evidence: a number
    the cited facts do not report is looked up in the other facts of the cited papers. None when a
    number is in no fact of those papers."""
    cited = list(dict.fromkeys(item['fact_ids'] + [i for row in item['dimensions'] for i in row['fact_ids']]))
    papers = {facts_by_id[i][0]['paper'] for i in cited}
    added: list[str] = []
    for text in [item[field] for field in ('title', 'question', 'reasoning')] + [row['note'] for row in item['dimensions']]:
        for value in _unsupported_numbers(text, [facts_by_id[i][1] for i in cited + added]):
            source = next((i for i, (paper, fact) in facts_by_id.items()
                           if paper['paper'] in papers and value in _reported(fact)), None)
            if source is None:
                return None
            if source not in added:
                added.append(source)
    return added


def _problem(item, facts_by_id: dict[str, tuple[dict, dict]]) -> str | None:
    """Why an opportunity cannot be kept, or None."""
    if not isinstance(item, dict) or item.get('kind') not in KINDS:
        return 'KIND_INVALID'
    if any(not isinstance(item.get(field), str) or not item[field].strip() or len(item[field]) > 2000 for field in TEXT_FIELDS):
        return 'TEXT_INVALID'
    rows = item.get('dimensions')
    if not isinstance(rows, list) or any(
            not isinstance(row, dict) or row.get('dimension') not in DIMENSIONS or row.get('status') not in {'same', 'different', 'unclear'}
            or not isinstance(row.get('note'), str) or len(row['note']) > 1000 for row in rows):
        return 'DIMENSIONS_INVALID'
    cited = [item.get('fact_ids'), *[row.get('fact_ids') for row in rows]]
    if any(not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in facts_by_id for i in ids) for ids in cited):
        return 'FACT_ID_UNKNOWN'
    if item['kind'] != 'gap' and len({facts_by_id[i][0]['paper'] for i in item['fact_ids']}) < 2:
        return 'ONE_PAPER_ONLY'
    return None


def fingerprint(kind: str, keys: list[str]) -> str:
    """The same kind of opportunity resting on the same card statements is the same opportunity."""
    return sha({'kind': kind, 'facts': sorted(set(keys))})[:20]


def checked(response, group: list[dict]) -> tuple[list[dict], list[dict]]:
    """(kept, dropped): the opportunities of one group's response that pass the checks, each with
    its evidence and fingerprint, and the others with the reason."""
    if not isinstance(response, dict) or not isinstance(response.get('opportunities'), list):
        raise ValueError('OPPORTUNITIES_RESPONSE_INVALID')
    facts_by_id = {fact['id']: (paper, fact) for paper in group for fact in paper['facts']}
    kept, dropped, seen = [], [], set()
    for item in response['opportunities']:
        problem = _problem(item, facts_by_id)
        added = None if problem else _number_sources(item, facts_by_id)
        if problem or added is None:
            title = item.get('title') if isinstance(item, dict) and isinstance(item.get('title'), str) else ''
            dropped.append({'title': title[:300], 'reason': problem or 'NUMBER_NOT_IN_CITED_PAPERS'})
            continue
        ids = list(dict.fromkeys(item['fact_ids'] + [i for row in item['dimensions'] for i in row['fact_ids']] + added))
        mark = fingerprint(item['kind'], [fact_key(*facts_by_id[i]) for i in ids])
        if mark in seen:
            continue
        seen.add(mark)
        papers = list(dict.fromkeys(facts_by_id[i][0]['paper'] for i in ids))
        kept.append({
            'fingerprint': mark, 'kind': item['kind'],
            **{field: item[field].strip() for field in TEXT_FIELDS},
            'dimensions': [{k: row[k] for k in ('dimension', 'status', 'note', 'fact_ids')} for row in item['dimensions']],
            'fact_ids': list(dict.fromkeys(item['fact_ids'] + added)),
            'papers': [{'paper': p['paper'], 'title': p['title'], 'job_id': p['job_id'], 'card_sha256': p['card_sha256'],
                        'library_files': p['library_files']} for p in group if p['paper'] in papers],
            'evidence': [{'fact_id': i, 'fact_key': fact_key(*facts_by_id[i]), 'paper': facts_by_id[i][0]['paper'],
                          'paper_title': facts_by_id[i][0]['title'], 'card_artifact_id': facts_by_id[i][0]['card_artifact_id'],
                          **{k: facts_by_id[i][1][k] for k in ('field', 'text', 'quote', 'chunk_id', 'page', 'section')}}
                         for i in ids]})
    return kept, dropped


def _plain(text) -> str:
    """Text that cannot add links, images, HTML, code or table cells; the same marks the reports
    escape (reports/renderer.py), so file names stay readable."""
    return ''.join('\\' + c if c in MARKUP else c for c in ' '.join(str(text or '').split()))


def markdown(opportunities: list[dict], *, day: str, paper_count: int, earlier: int, language) -> str:
    def t(zh, en, ja):
        return choose(language, zh, en, ja)
    kind_names = {'divergence': t('分歧', 'Divergence', '相違'),
                  'condition_difference': t('条件差异', 'Different conditions', '条件の違い'),
                  'gap': t('缺口', 'Gap', '空白')}
    dimension_names = dict(zip(DIMENSIONS, (t('对象', 'Object', '対象'), t('方法', 'Method', '方法'), t('样本', 'Sample', '標本'),
                                            t('指标', 'Measure', '指標'), t('条件', 'Conditions', '条件'), t('结果', 'Result', '結果'))))
    status_names = {'same': t('相同', 'same', '同じ'), 'different': t('不同', 'different', '異なる'), 'unclear': t('不清楚', 'unclear', '不明')}
    field_names = {'research_question': t('研究问题', 'research question', '研究課題'), 'research_object': t('研究对象', 'object', '研究対象'),
                   'method': t('方法', 'method', '方法'), 'comparison_context': t('比较条件', 'comparison context', '比較条件'),
                   'key_data': t('关键数据', 'key data', '主要データ'), 'key_results': t('主要结果', 'key results', '主な結果'),
                   'author_conclusion': t('作者结论', 'authors\' conclusion', '著者の結論'),
                   'boundary_conditions': t('边界条件', 'boundary conditions', '境界条件'),
                   'limitation': t('作者所述局限', 'stated limitation', '著者が述べた限界'), 'outlook': t('作者所述展望', 'stated future work', '著者が述べた展望')}
    counts = {kind: sum(o['kind'] == kind for o in opportunities) for kind in KINDS}
    lines = [f"# {t('研究机会', 'Research opportunities', '研究の機会')} · {day}", '']
    parts = t('，', ', ', '、').join(t('{0} {1} 条', '{1} {0}', '{0} {1} 件').format(kind_names[kind], counts[kind])
                                     for kind in KINDS if counts[kind])
    lead = t('从 {0} 篇论文中找到 {1} 条新的研究机会', 'From {0} papers, {1} new research opportunities',
             '{0} 本の論文から新しい研究の機会を {1} 件').format(paper_count, len(opportunities))
    lead += (t('：', ': ', '：') + parts if parts else '') + t('。', '.', '。')
    if earlier:
        lead += t('之前找到的 {0} 条不再重复列出，见研究机会页。', ' The {0} found before are not repeated here; see the opportunities page.',
                  '以前に見つけた {0} 件はここでは繰り返さず、研究の機会のページにあります。').format(earlier)
    lines += [lead, '']
    if not opportunities:
        lines += [t('这些论文里没有找到新的、有依据的分歧或缺口。', 'No new divergence or gap with enough support was found in these papers.',
                    'これらの論文には、新しく根拠のある相違や空白は見つかりませんでした。'), '']
    for number, item in enumerate(opportunities, 1):
        lines += [f"## {number}. {kind_names[item['kind']]}：{_plain(item['title'])}", '',
                  f"**{t('要追问的问题', 'Question to ask', '問うべき問い')}**：{_plain(item['question'])}", '',
                  f"**{t('判断依据', 'Why', '根拠')}**：{_plain(item['reasoning'])}", '']
        for row in item['dimensions']:
            lines.append(f"- {dimension_names[row['dimension']]}（{status_names[row['status']]}）：{_plain(row['note'])}")
        if item['dimensions']:
            lines.append('')
        lines += [f"**{t('建议怎么解决', 'How to resolve it', '解決の手がかり')}**：{_plain(item['suggestion'])}", '',
                  f"**{t('局限', 'Limitations', '限界')}**：{_plain(item['limitations'])}", '',
                  f"**{t('证据', 'Evidence', '根拠となる記述')}**", '']
        # Evidence by paper, in the order the papers appear in the opportunity.
        order = [p['paper'] for p in item['papers']]
        for fact in sorted(item['evidence'], key=lambda f: order.index(f['paper'])):
            place = '，'.join(part for part in (
                t('第 {0} 页', 'page {0}', '{0} ページ').format(fact['page']) if fact['page'] else '',
                _plain(fact['section']) if fact['section'] else '') if part)
            quote = f" “{_plain(fact['quote'])}”" if fact['quote'] and fact['quote'] not in fact['text'] else ''
            lines.append(f"- 《{_plain(fact['paper_title'])}》· {field_names.get(fact['field'], fact['field'])}"
                         + (f"（{place}）" if place else '') + f"：{_plain(fact['text'])}{quote}")
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'
