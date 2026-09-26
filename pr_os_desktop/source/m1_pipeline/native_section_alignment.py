"""Resolve Chinese section provenance from uniquely matched native PDF text.

The converter's reading order is not authoritative for section membership.
Alignment is allowed only against the exact source hash and a monotonic native
numbered-heading stream. It changes metadata, never the extracted body text.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

from .chinese_structure import heading

_HAN = re.compile(r'[\u3400-\u9fff]')
_NUM = re.compile(r'^(\d+(?:\.\d+)*)\s+(.+)$')
_REFERENCE_ENTRY = re.compile(r'^\s*[\[［【]\s*\d+\s*[\]］】]')


def _match_text(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold()
                   if c.isalnum() or c == '-')


def _formula_match_text(text):
    # Unlike bibliography matching, formula provenance must retain decimal
    # points, signs, slashes, exponents, letter case, and every other symbol.
    return ''.join(c for c in unicodedata.normalize('NFKC', text)
                   if not c.isspace())


def _bibliography_pattern(text):
    value = _match_text(text)
    parts = []
    for i, char in enumerate(value):
        before, after = value[i-1:i] if i else '', value[i+1:i+2]
        # The existing PDF converter sometimes emits '-' for Latin 'o'.
        # Permit that single observed glyph substitution in matching only;
        # the complete remaining text must match uniquely. Numeric signs and
        # original text are never modified.
        alias = (char == '-' and not before.isdigit() and not after.isdigit()
                 and any(c.isascii() and c.isalpha() for c in before+after))
        parts.append('[o-]' if alias else re.escape(char))
    return value, re.compile(''.join(parts))


def build_native_section_index(folder, source_sha256):
    matches = [p for p in Path(folder).glob('*.pdf') if p.name.startswith('[PDF]')]
    if len(matches) != 1 or not source_sha256:
        return {}, {'status': 'NOT_AVAILABLE'}
    pdf = matches[0]
    if hashlib.sha256(pdf.read_bytes()).hexdigest().upper() != source_sha256.upper():
        raise ValueError('CHINESE_SECTION_SOURCE_HASH_MISMATCH')
    import pymupdf
    index, headings, stack, previous_number = {}, [], [], None
    reference_started = False
    with pymupdf.open(pdf) as document:
        for page_number, page in enumerate(document, 1):
            letters, labels = [], []
            all_letters, all_labels = [], []
            formula_letters, formula_labels = [], []
            blocks = page.get_text('dict')['blocks']
            reference_start = 0 if reference_started else None
            reference_last = None
            for bi, block in enumerate(blocks):
                if block.get('type') != 0:
                    continue
                lines = [''.join(s['text'] for s in line['spans']) for line in block['lines']]
                if any(heading(line) == ('参考文献', None) for line in lines):
                    reference_started = True
                    reference_start = bi + 1
                if reference_started and any(_REFERENCE_ENTRY.match(line) for line in lines):
                    reference_last = bi
            for block_index, block in enumerate(blocks):
                if block.get('type') != 0:
                    continue
                lines = [''.join(s['text'] for s in line['spans']) for line in block['lines']]
                in_bibliography = (reference_start is not None and reference_last is not None
                                   and reference_start <= block_index <= reference_last)
                for li, line in enumerate(lines):
                    normalized = _match_text(line)
                    all_letters.extend(normalized)
                    all_labels.extend([(in_bibliography, block_index, li)] * len(normalized))
                i = 0
                while i < len(lines):
                    line = lines[i]
                    candidate = line
                    combined = False
                    if re.fullmatch(r'\s*\d+(?:\s*[.．]\s*\d+)*\s*', line) and i+1 < len(lines):
                        candidate = line.strip()+' '+lines[i+1].strip()
                        combined = True
                    # A model number wrapped after a hyphen is a continuation,
                    # e.g. V- / 560, not a new numbered section.
                    continuation = i > 0 and re.search(r'[\w][－-]\s*$', lines[i-1])
                    parsed = None if continuation else heading(candidate)
                    numbered = _NUM.match(parsed[0]) if parsed else None
                    if numbered and _HAN.search(parsed[0]):
                        number = tuple(int(p) for p in numbered[1].split('.'))
                        if previous_number is not None and number < previous_number:
                            return {}, {'status': 'AMBIGUOUS_NATIVE_ORDER',
                                        'page': page_number, 'previous': previous_number,
                                        'current': number}
                        previous_number = number
                        while stack and not (len(stack[-1][0]) < len(number)
                                             and number[:len(stack[-1][0])] == stack[-1][0]):
                            stack.pop()
                        stack.append((number, parsed[0]))
                        headings.append({'page': page_number, 'block': block_index,
                                         'line': i, 'title': parsed[0]})
                        if combined:
                            i += 1
                    elif parsed and parsed[0] == '参考文献':
                        stack = [((), '参考文献')]
                    else:
                        section = ' > '.join(title for _, title in stack)
                        for char in _HAN.findall(line):
                            letters.append(char)
                            labels.append((section, block_index, i))
                        exact = _formula_match_text(line)
                        formula_letters.extend(exact)
                        formula_labels.extend([(section, block_index, i)] * len(exact))
                    i += 1
            index[page_number] = (''.join(letters), labels, ''.join(all_letters), all_labels,
                                  ''.join(formula_letters), formula_labels)
    return index, {'status': 'READY', 'headings': headings,
                   'source_sha256': source_sha256.upper()}


def resolve_native_section(text, page_start, page_end, index):
    if page_start != page_end or page_start not in index:
        return None
    needle = ''.join(_HAN.findall(text))
    if len(needle) < 6:
        return resolve_native_formula_section(text, page_start, page_end, index)
    haystack, labels = index[page_start][:2]
    start = haystack.find(needle)
    if start < 0 or haystack.find(needle, start+1) >= 0:
        return None
    # Running titles/headers repeated across pages cannot establish a unique
    # body-section location, even when their printed page numbers differ.
    if any(needle in other[0] for page, other in index.items() if page != page_start):
        return None
    hits = labels[start:start+len(needle)]
    sections = {row[0] for row in hits}
    if len(sections) != 1 or not next(iter(sections)):
        return None
    return {'section': next(iter(sections)), 'page': page_start,
            'native_positions': [list(row[1:]) for row in dict.fromkeys(hits)]}


def resolve_native_formula_section(text, page_start, page_end, index):
    """Locate short-Han formulas without guessing from a nearby text chunk."""
    if page_start != page_end or page_start not in index:
        return None
    needle = _formula_match_text(text)
    if (len(needle) < 12 or not any(c.isdigit() for c in needle)
            or sum(c.isalpha() for c in needle) < 3):
        return None
    matches = []
    for page, row in index.items():
        if len(row) < 6:
            continue
        start = row[4].find(needle)
        while start >= 0:
            matches.append((page, start))
            start = row[4].find(needle, start + 1)
    if len(matches) != 1 or matches[0][0] != page_start:
        return None
    page, start = matches[0]
    hits = index[page][5][start:start + len(needle)]
    sections = {row[0] for row in hits}
    if len(sections) != 1 or not next(iter(sections)):
        return None
    return {'section': next(iter(sections)), 'page': page,
            'native_positions': [list(row[1:]) for row in dict.fromkeys(hits)],
            'matching': 'unique_full_native_formula_width_and_whitespace_only'}


def align_bibliography(block, text, index):
    """Bind reference entries and their continuations to the exact PDF.

    Reference regions end at the last native block containing an entry marker;
    later-emitted figure labels are not swept into the bibliography. Matching
    includes Latin text because Chinese papers frequently cite English titles.
    """
    if block['type'] not in {'paragraph', 'list'} or block['page'] != block['page_end']:
        return None
    value, pattern = _bibliography_pattern(text)
    if len(value) < 6:
        return None
    matches = [(page, m) for page, row in index.items() for m in pattern.finditer(row[2])]
    if len(matches) != 1 or matches[0][0] != block['page']:
        return None
    page, match = matches[0]
    labels = index[page][3][match.start():match.end()]
    if not labels or not all(label[0] for label in labels):
        return None
    return {'section':'参考文献', 'page':page, 'block_type':'reference_entry',
            'native_positions':[list(row[1:]) for row in dict.fromkeys(labels)],
            'matching':'unique_full_text_whitespace_width_and_observed_o_dash_glyph'}


def align_numbered_section(block, text, index, titles):
    """Correct numeric membership without reclassifying front matter/captions
    as bibliography from the PDF's late-emitted auxiliary text objects.
    """
    old = _NUM.match(block['section'].split(' > ')[-1])
    if not old or block['page'] <= 1:
        return None
    resolved = resolve_native_section(text, block['page'], block['page_end'], index)
    if (resolved and resolved.get('matching') == 'unique_full_native_formula_width_and_whitespace_only'
            and block['type'] not in {'paragraph', 'list'}):
        return None
    new = _NUM.match(resolved['section'].split(' > ')[-1]) if resolved else None
    if not new or new[1] == old[1] or new[1] not in titles:
        return None
    # The converter supplies the full observed title; the native stream binds
    # its number and exact body membership. Both must agree on the title prefix.
    compact = lambda s: re.sub(r'\s', '', s)
    if not compact(titles[new[1]]).startswith(compact(new.group())):
        return None
    numbers = new[1].split('.')
    parts = ['.'.join(numbers[:n]) for n in range(1, len(numbers)+1)]
    resolved['section'] = ' > '.join(titles[n] for n in parts if n in titles)
    return resolved
