"""Drafts with support marks, shared by memo_bridge.py (Memo passages E1, E2 ...) and sources.py
(local spans S1 p.3 L120-124). A mark follows the claim it supports:

    claim text [E3: "short exact quote"]
    claim text [S1 p.3 L120-124: "quote"; S2 L8: "quote"]
    computed value [inference E3: "quote"; E5: "quote"]

The claim is the text from the previous mark, sentence end, line start or bold label up to the mark.
Background and the user's hypotheses carry no mark and are not claims.
"""
import re

MARK = re.compile(r'\[(inference\s+)?((?:[A-Z]\d+[^:\[\]\n"]*:\s*"[^"\n]+"\s*;?\s*)+)\]')
SUPPORT = re.compile(r'([A-Z]\d+)([^:\[\]\n"]*):\s*"([^"\n]+)"')
LEFTOVER = re.compile(r'\[(?:inference\s+)?[A-Z]\d+[\s:]')
BOUNDARY = re.compile(r'[。！？!?；;]|\.\s|\n|\*\*[^*\n]*\*\*\s*[：:]?')
NUMBER = re.compile(r'\d+(?:,\d{3})*(?:\.\d+)?')


def squash(text):
    return ' '.join(text.split())


def numbers(text):
    """Reported values in a text; whole numbers up to ten are counts and ordinals, not values."""
    found = set()
    for match in NUMBER.finditer(text):
        value = float(match.group().replace(',', ''))
        if '.' in match.group() or value > 10 or text[match.end():match.end() + 1] in {'%', '％'}:
            found.add(value)
    return found


def apply(draft, resolve, render):
    """Turn a marked draft into (text, claims, problems).

    resolve(alias, where, quote) -> (support, passage text) for one support, or raises ValueError
    with the problem; render(supports) -> what replaces the mark in the text. A source statement's
    numbers must be in the passages it cites; a computed value belongs in an inference."""
    problems, claims = [], []
    text, last, boundary = '', 0, 0
    for mark in MARK.finditer(draft):
        text += draft[last:mark.start()]
        last = mark.end()
        start = max([boundary] + [m.end() for m in BOUNDARY.finditer(text, boundary)])
        claim = text[start:].strip().lstrip('-*>#·•').strip().rstrip('，,：:').strip()
        kind = 'inference' if mark.group(1) else 'source_statement'
        supports, passages = [], []
        for alias, where, quote in SUPPORT.findall(mark.group(2)):
            try:
                support, passage = resolve(alias, where.strip(), quote)
            except ValueError as error:
                problems.append(str(error))
                continue
            supports.append(support)
            passages.append(passage)
        if not claim:
            problems.append(f'a mark has no claim before it: {mark.group(0)[:80]}')
        elif kind == 'source_statement' and passages:
            missing = numbers(claim) - set().union(*(numbers(p) for p in passages))
            if missing:
                values = ', '.join(sorted(f'{v:g}' for v in missing))
                problems.append(f'"{claim[:80]}": {values} not in the cited passages; cite the passage that reports '
                                'it, or mark a computed value as [inference ...]')
        text += render(supports)
        boundary = len(text)
        if claim:
            claims.append({'text': claim, 'kind': kind, 'supports': supports})
    text = (text + draft[last:]).strip()
    if LEFTOVER.search(text):
        problems.append('a citation mark is not in the form [E3: "quote"] / [S1 p.3 L120-124: "quote"] '
                        'or [inference ...; ...]')
    if not claims:
        problems.append('the draft cites no passage')
    return text, claims, problems
