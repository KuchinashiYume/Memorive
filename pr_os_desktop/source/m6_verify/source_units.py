"""Conservative source-bound checks for invented molar-unit prefixes/denominators.

This does not correct scientific units or infer author intent. A finding needs
the same explicit scalar and unit stem in the claim and its own source anchors,
with no source occurrence supporting the claimed denominator. Broken ``m-l``
glyphs remain broken in the evidence; they are never rewritten into ``mol``.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_QUANTITY = re.compile(
    r"(?<![\d.])(?P<number>\d(?:[ \t]*\d)*(?:[ \t]*\.[ \t]*\d(?:[ \t]*\d)*)?)"
    r"\s*(?P<unit>(?P<prefix>[munpµμk]?)[ \t]*m[ \t]*(?:o|-)[ \t]*l)"
    r"(?P<denominator>[ \t]*[/／][ \t]*[LlＬｌ])?(?![A-Za-z])"
)
_MOLAR_SCALE = {'': Decimal(1), 'k': Decimal('1e3'), 'm': Decimal('1e-3'),
                'u': Decimal('1e-6'), 'μ': Decimal('1e-6'), 'n': Decimal('1e-9'),
                'p': Decimal('1e-12')}

_INDEXED = r'[A-Za-z](?:_\{?[A-Za-z0-9]+\}?|[ᵢⱼ₀-₉])'
_RATIO = re.compile(r'(?P<plain>(?P<a>'+_INDEXED+r')\s*/\s*(?P<b>'+_INDEXED+r'))'
                    r'|\\frac\s*\{(?P<fa>'+_INDEXED+r')\}\s*\{(?P<fb>'+_INDEXED+r')\}')
_BAD_RANGE = re.compile(r'(?<![\w.])(?P<a>\d+(?:\.\d+)?)\s*e\s*(?P<b>\d+\.\d+)(?![\w.])')
_RANGE = re.compile(r'(?<![\w.])(?P<a>\d+(?:\.\d+)?)\s*[–−—]\s*(?P<b>\d+(?:\.\d+)?)(?![\w.])')
_POWER_QUANTITY = re.compile(
    r'(?<![\w.])10\s*\^\s*\{?(?P<exponent>[+\-−]?\d{1,3})\}?'
    r'\s+(?P<suffix>times\b|fold\b|倍|[pnumkµμ]?A\b|[pnumkµμ]?mol\s*/\s*s\b)')


def _ratios(text):
    for match in _RATIO.finditer(text):
        if _negated_or_criticized(text, match.start(), match.end()):
            continue
        a, b = match['a'] or match['fa'], match['b'] or match['fb']
        def norm(value):
            return value.replace('{','').replace('}','').translate(str.maketrans({'ᵢ':'_i','ⱼ':'_j'}))
        a, b = norm(a), norm(b)
        if a[0] == b[0] and a != b:
            yield a, b, match.group()


def _negated_or_criticized(text, start, end):
    prefix = text[:start].rstrip()
    suffix = text[end:].lstrip()
    return bool(re.search(r'(?:\bnot|\brather than|\binstead of|\bwithout|不是|并非|而非|不能|不要)'
                          r'(?:\s+(?:use|using|be|write|written as)|写为|使用)?\s*[“"\x27]?$', prefix, re.I)
                or re.match(r'["”\x27]?\s*(?:is|would be|was)?\s*(?:incorrect|wrong|erroneous)\b', suffix, re.I))


def symbol_conflicts(claim, sources):
    """Exact, source-bound symbol errors; never infer a missing source value."""
    for cid, text in sources.items():
        for power in _POWER_QUANTITY.finditer(text):
            if _negated_or_criticized(text, power.start(), power.end()):
                continue
            fused = re.compile(r'(?<![\w.^])10' + re.escape(power['exponent'])
                               + r'\s+' + re.escape(power['suffix']) + r'(?!\w)')
            if any(fused.search(other) for other in sources.values()):
                continue  # A literal alternative in the bound source is ambiguous.
            for hit in fused.finditer(claim):
                if not _negated_or_criticized(claim, hit.start(), hit.end()):
                    yield {'card_quote': hit.group(), 'source_quote': power.group(),
                           'chunk_id': cid, 'reason': 'SOURCE_POWER_EXPONENT_FLATTENED'}
    ratios = [(cid,a,b,quote) for cid,text in sources.items() for a,b,quote in _ratios(text)]
    for a,b,quote in _ratios(claim):
        if any((sa,sb)==(a,b) for _,sa,sb,_ in ratios):
            continue
        reverse = [(cid,sq) for cid,sa,sb,sq in ratios if (sa,sb)==(b,a)]
        if reverse:
            cid,sq=reverse[0]
            yield {'card_quote':quote,'source_quote':sq,'chunk_id':cid,
                   'reason':'SOURCE_RATIO_NUMERATOR_DENOMINATOR_REVERSED'}
    for match in _BAD_RANGE.finditer(claim):
        if _negated_or_criticized(claim, match.start(), match.end()):
            continue
        if any(match.group() in text for text in sources.values()):
            continue
        peers = [(cid,other.group()) for cid,text in sources.items() for other in _RANGE.finditer(text)
                 if Decimal(other['a'])==Decimal(match['a']) and Decimal(other['b'])==Decimal(match['b'])]
        if peers:
            cid,quote=peers[0]
            yield {'card_quote':match.group(),'source_quote':quote,'chunk_id':cid,
                   'reason':'DECIMAL_RANGE_MISREAD_AS_EXPONENT'}


def _quantities(text):
    found = []
    for match in _QUANTITY.finditer(text):
        try:
            value = Decimal(re.sub(r"\s", "", match['number']))
        except InvalidOperation:
            continue
        found.append({
            'value': value, 'prefix': match['prefix'].replace('µ', 'μ'),
            'denominator': bool(match['denominator']),
            # Unparsed unit continuations (e.g. mol·L^-1, mol per L) cannot
            # prove a missing denominator. Leave them to semantic review.
            'uncertain_suffix': bool(re.match(r'[A-Za-z/／·⋅^⁻−-]', text[match.end():].lstrip())),
            'quote': match.group(), 'start': match.start(), 'end': match.end(),
        })
    return found


def denominator_conflicts(claim, sources):
    """Yield exact quote pairs, never an inferred replacement value.

Only positive assertions with /L are considered. Source-quoted ambiguity with
no normalized assertion is allowed. Equivalent unit conversions, other unit
families, and absent/ambiguous numeric matches are left to semantic review.
"""
    source_quantities = [(cid, item) for cid, text in sources.items()
                         for item in _quantities(text)]
    for item in _quantities(claim):
        prefix = claim[:item['start']].rstrip()
        if re.search(r'(?:不(?:应|宜|能|是)|并非)(?:写为|改写为|视为|按|记作)?\s*[“"\x27]?$'
                     r'|未(?:给出|报告|写作)\s*[“"\x27]?$', prefix):
            continue
        same_scalar = [(cid, other) for cid, other in source_quantities
                       if item['value'] == other['value']]
        peers = [(cid, other) for cid, other in same_scalar
                 if item['prefix'] == other['prefix']]
        # Prefix changes also alter the reported quantity. Only flag the same
        # explicit scalar; valid conversions and ambiguous suffixes stay with
        # semantic review. Original m-l glyphs are never repaired here.
        if same_scalar and not peers and not any(other['uncertain_suffix'] for _, other in same_scalar):
            equivalent = any(
                other['denominator'] == item['denominator']
                and other['value'] * _MOLAR_SCALE[other['prefix']] ==
                    item['value'] * _MOLAR_SCALE[item['prefix']]
                for _, other in source_quantities)
            if not equivalent:
                cid, other = same_scalar[0]
                yield {'card_quote': item['quote'], 'source_quote': other['quote'],
                       'chunk_id': cid, 'reason': 'SOURCE_HAS_NO_CLAIMED_MOLAR_PREFIX'}
            continue
        if not item['denominator']:
            continue
        if not peers or any(other['denominator'] or other['uncertain_suffix'] for _, other in peers):
            continue
        cid, other = peers[0]
        # Keep the claim's minimal assertion and the original source spelling.
        yield {'card_quote': item['quote'], 'source_quote': other['quote'],
               'chunk_id': cid, 'reason': 'SOURCE_HAS_NO_CLAIMED_MOLAR_DENOMINATOR'}


def augment_review_rows(rows, subjects, chunk_texts, item_texts):
    result = list(rows)
    keys = {(row['location'], row['item_index'], row['card_quote'], row['source_quote'])
            for row in rows}
    for location, subject in subjects.items():
        sources = {cid: chunk_texts[cid] for cid in subject.chunk_ids if cid in chunk_texts}
        for index, claim in enumerate(item_texts(subject.value_text)):
            for finding in (*denominator_conflicts(claim, sources), *symbol_conflicts(claim, sources)):
                key = location, index, finding['card_quote'], finding['source_quote']
                if key not in keys:
                    result.append({'location': location, 'item_index': index, 'rule': 'R7',
                                   'card_quote': finding['card_quote'],
                                   'source_quote': finding['source_quote'],
                                   'evidence_relation': 'direct_conflict'})
                    keys.add(key)
    return result
