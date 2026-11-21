"""Drafts with support marks, shared by memo_bridge.py (Memo passages E1, E2 ...) and sources.py
(local spans S1 p.3 L120-124). A mark follows the claim it supports:

    claim text [E3: "short exact quote"]
    claim text [S1 p.3 L120-124: "quote"; S2 L8: "quote"]
    computed value [inference E3: "quote"; E5: "quote"]

Chinese quotation marks, colon and semicolon (“…”, ：, ；) are read the same way. The claim is the
text from the previous mark, sentence end, line start or bold label up to the mark. Background and
the user's hypotheses carry no mark and are not claims.

Quotes are compared after the usual differences between extracted PDF text and typed text are
evened out (ligatures such as "ﬁ", curly quotes, dashes, a word split by a hyphen at a line end,
line breaks); locate() returns the passage's own text for such a quote, which is what is stored.
"""
import difflib
import re
import unicodedata

QUOTE = r'(?:"([^"\n]+)"|“([^”\n]+)”)'
MARK = re.compile(r'\[(inference\s+)?((?:[A-Z]\d+[^:：\[\]\n"“]*[:：]\s*' + QUOTE.replace('(', '(?:').replace('(?:?:', '(?:')
                  + r'\s*[;；]?\s*)+)\]')
SUPPORT = re.compile(r'([A-Z]\d+)([^:：\[\]\n"“]*)[:：]\s*' + QUOTE)
LEFTOVER = re.compile(r'\[(?:inference\s+)?[A-Z]\d+[\s:：\]]')
BOUNDARY = re.compile(r'[。！？!?；;]|\.\s|\n|\*\*[^*\n]*\*\*\s*[：:]?')
NUMBER = re.compile(r'\d+(?:,\d{3})*(?:\.\d+)?')
SAME = str.maketrans({'‘': "'", '’': "'", '“': '"', '”': '"', '–': '-', '—': '-', '−': '-', '‐': '-', '‑': '-'})


def squash(text):
    return ' '.join(text.split())


def _even(text, hyphens=None):
    """text with the differences above evened out, and for each of its characters the index of the
    character of text it came from. hyphens: what to do with a hyphen that ends a line inside a word,
    'drop' it with the line break (de-|grades) or keep it 'tight' (state-|of-the-art)."""
    out, where, i = [], [], 0
    while i < len(text):
        char = text[i]
        if hyphens and char == '-' and 0 < i and text[i - 1].isalpha():
            gap = i + 1
            while gap < len(text) and text[gap].isspace():
                gap += 1
            if '\n' in text[i + 1:gap] and gap < len(text) and text[gap].isalpha():
                if hyphens == 'tight':
                    out.append('-')
                    where.append(i)
                i = gap
                continue
        if char.isspace():
            if out and out[-1] != ' ':
                out.append(' ')
                where.append(i)
        else:
            for part in unicodedata.normalize('NFKC', char).translate(SAME):
                out.append(part)
                where.append(i)
        i += 1
    return ''.join(out), where


def locate(quote, passage):
    """The passage's own text for quote, or None when the quote is not in it. A line break typed as
    \\n inside a quote (as when copying table cells) counts as a line break."""
    wanted = _even(quote.replace('\\n', '\n').replace('\\t', '\t'))[0].strip()
    if not wanted:
        return None
    for hyphens in (None, 'drop', 'tight'):
        text, where = _even(passage, hyphens)
        start = text.find(wanted)
        if start >= 0:
            return passage[where[start]:where[start + len(wanted) - 1] + 1]
    return None


def closest(quote, passage):
    """The line or sentence of the passage most like quote, to copy from."""
    pieces = [p.strip() for line in passage.splitlines() for p in re.split(r'(?<=[.;。；])\s+', line) if p.strip()]
    wanted = _even(quote)[0]
    scored = [(difflib.SequenceMatcher(None, wanted, _even(p)[0]).ratio(), p) for p in pieces]
    return max(scored)[1][:200] if scored else ''


def numbers(text):
    """Reported values in a text; whole numbers up to ten are counts and ordinals, not values."""
    found = set()
    for match in NUMBER.finditer(text):
        value = float(match.group().replace(',', ''))
        if '.' in match.group() or value > 10 or text[match.end():match.end() + 1] in {'%', '％'}:
            found.add(value)
    return found


# Numbers in a writer's sentence that are not reported values: page references (p. 774, 第 5 页,
# 期刊页 774), places in the paper (§2.3, Section 2.2, Table 12, Fig. 3, 第 4 节, 2.3 节, 见表 12),
# digits of a name (VGG16, ResNet-34, fc7) and years.
PAGE_REFERENCE = re.compile(r'(?:\bpp?\.|\bpages?|第|页)\s*\d+(?:\s*[-–]\s*\d+)?|\d+(?:\s*[-–]\s*\d+)?\s*页')
# 表 and 图 count as a place only after a boundary or a pointing word (见表 12), so 代表 28.54% stays a value.
PLACE_REFERENCE = re.compile(
    r'§+\s*\d+(?:\.\d+)*'
    r'|\b(?:sections?|sec|tables?|tab|figures?|fig|equations?|eq|appendix)\.?\s*\(?\d+(?:\.\d+)*\)?'
    r'|第\s*\d+(?:\.\d+)*\s*[节章表图]|\d+(?:\.\d+)*\s*[节章]'
    r'|(?:^|(?<=[\s(（,，.。;；:：见如在由从据及与和]))[表图]\s*\d+(?:\.\d+)*', re.I)
NAME_DIGITS = re.compile(r'(?<=[A-Za-z])-?\d+')


def stated_values(text):
    """The values a written sentence reports (numbers() without page and place references, names and years)."""
    text = NAME_DIGITS.sub(' ', PLACE_REFERENCE.sub(' ', PAGE_REFERENCE.sub(' ', text)))
    return {v for v in numbers(text) if not (v.is_integer() and 1900 <= v <= 2100)}


def _claim(text, floor):
    """The claim a mark at the end of text supports: from the last sentence end, mark or line start
    (not before floor) to the mark. A mark written after the sentence's own full stop supports that
    sentence."""
    end = len(text)
    for _ in range(2):
        found = list(BOUNDARY.finditer(text, floor, end))
        start = found[-1].end() if found else floor
        claim = text[start:end].strip().lstrip('-*>#·•|').strip().rstrip('，,：:|').strip()
        if claim or not found:
            return claim
        end = found[-1].start()
    return claim


def apply(draft, resolve, render, where_value=None):
    """Turn a marked draft into (text, claims, problems).

    resolve(alias, where, quote) -> (support, passage text) for one support, or raises ValueError
    with the problem; render(supports) -> what replaces the mark in the text. A source statement's
    numbers must be in the passages it cites; a computed value belongs in an inference. Marks
    written one after another support the same claim.
    where_value(value) -> names of passages that report value, to say where to cite it from."""
    problems, claims, passages_of = [], [], []
    text, last, boundary, claimed = '', 0, 0, False
    for mark in MARK.finditer(draft):
        text += draft[last:mark.start()]
        last = mark.end()
        follows = claimed and not text[boundary:].strip(' \t，,、;；')   # right after the previous mark
        claim = '' if follows else _claim(text, boundary)
        kind = 'inference' if mark.group(1) else 'source_statement'
        supports, passages = [], []
        for alias, where, straight, curly in SUPPORT.findall(mark.group(2)):
            quote = straight or curly
            try:
                support, passage = resolve(alias, where.strip(), quote)
            except ValueError as error:
                problems.append(str(error))
                continue
            supports.append(support)
            passages.append(passage)
        if follows:
            claims[-1]['supports'] += supports
            passages_of[-1] += passages
        elif claim:
            claims.append({'text': claim, 'kind': kind, 'supports': supports})
            passages_of.append(passages)
        else:
            problems.append(f'a mark has no claim before it: {mark.group(0)[:80]}')
        claimed = bool(follows or claim)
        text += render(supports)
        boundary = len(text)
    for claim, passages in zip(claims, passages_of):
        if claim['kind'] != 'source_statement' or not passages:
            continue
        for value in sorted(stated_values(claim['text']) - set().union(*(numbers(p) for p in passages))):
            found = where_value(value) if where_value else []
            hint = f'it is in {", ".join(found)}: cite that passage' if found else 'cite the passage that reports it'
            problems.append(f'"{claim["text"][:80]}": {value:g} not in the cited passages; {hint}, '
                            'or mark a computed value as [inference ...]')
    text = (text + draft[last:]).strip()
    rest = MARK.sub(' ', draft)    # in the draft: rendered locators such as [L7] are fine
    for odd in LEFTOVER.finditer(rest):
        end = rest.find(']', odd.start())
        problems.append(f'"{rest[odd.start():end + 1 if 0 <= end - odd.start() < 160 else odd.start() + 80]}" is not '
                        'a citation mark: write [E3: "quote"] / [S1 p.3 L120-124: "quote"] or [inference ...; ...], '
                        'every mark with a quote copied from its passage')
    if not claims:
        problems.append('the draft cites no passage')
    return text, claims, problems
