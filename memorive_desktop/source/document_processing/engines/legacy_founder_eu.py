"""Recover audited embedded glyphs before the existing PDF converter.

Some legacy PDFs render correct Latin glyphs but map them to unrelated Han in
ToUnicode. Bind recovery to the embedded font SHA, Identity CID/GID mapping and
the observed old Unicode. Never replace Han strings in extracted paragraphs or
write back to the source PDF. The glyph audit is CF126-20 (166 visible glyphs).
CF126-22 also binds legacy Type1 symbol programs, including a range dash whose
incorrect Unicode was a valid Latin e. Unknown mappings remain unmodified.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections import Counter
import hashlib
import re

FONT_GLYPHS = {'367bab1ec672d94b5b7715e08819762c7d44e3fee752669b2610b36605de74c4': {222: (20113, 'F'),
                                                                      656: (20991, 'd'),
                                                                      663: (21017, 'r'),
                                                                      979: (21592, '1'),
                                                                      1300: (22253, '0'),
                                                                      1315: (22278, '2'),
                                                                      2281: (24590, 'u'),
                                                                      2286: (24616, '9'),
                                                                      2295: (24742, 'C'),
                                                                      2301: (24895, '8'),
                                                                      2305: (24974, 'w'),
                                                                      2425: (25588, '.'),
                                                                      2439: (25671, ' '),
                                                                      2646: (26089, 'g'),
                                                                      2855: (26376, 'B'),
                                                                      2898: (26434, 'S'),
                                                                      2959: (26531, 'f'),
                                                                      3060: (26685, 'T'),
                                                                      3815: (27901, 's'),
                                                                      4029: (28304, '4'),
                                                                      4170: (28577, 'h'),
                                                                      4261: (28790, 'n'),
                                                                      4467: (29157, 'o'),
                                                                      4602: (29503, '3'),
                                                                      4674: (30338, 'm'),
                                                                      4724: (30776, 'R'),
                                                                      4827: (31908, 'A'),
                                                                      4830: (31967, 'c'),
                                                                      4871: (32536, '5'),
                                                                      5010: (33489, '7'),
                                                                      5177: (33900, 'a'),
                                                                      5347: (34299, 'e'),
                                                                      5408: (34468, 'i'),
                                                                      6227: (36131, 'p'),
                                                                      6233: (36156, 't'),
                                                                      6245: (36192, 'y'),
                                                                      6301: (36481, 'j'),
                                                                      6353: (36757, '/'),
                                                                      6366: (36816, 'K'),
                                                                      6372: (36828, '6'),
                                                                      6400: (36896, 'l'),
                                                                      6407: (36973, 'b'),
                                                                      6444: (37124, '-'),
                                                                      6457: (37213, 'M'),
                                                                      6581: (38405, 'D'),
                                                                      6599: (38504, 'I'),
                                                                      6627: (38901, 'O')},
 '386314dfdff521aa6be748421ed1f182b6121b53a08182e6e578083c65c6401b': {222: (20113, 'F'),
                                                                      323: (20381, '±'),
                                                                      548: (20801, 'J'),
                                                                      597: (20877, 'Y'),
                                                                      613: (20900, ')'),
                                                                      626: (20918, '”'),
                                                                      657: (20991, 'd'),
                                                                      664: (21017, 'r'),
                                                                      822: (21248, 'H'),
                                                                      838: (21277, 'Q'),
                                                                      871: (21407, '−'),
                                                                      981: (21592, '1'),
                                                                      1029: (21681, '['),
                                                                      1032: (21705, 'U'),
                                                                      1194: (22122, 'k'),
                                                                      1303: (22253, '0'),
                                                                      1318: (22278, '2'),
                                                                      1329: (22312, 'Z'),
                                                                      1389: (22435, '+'),
                                                                      1519: (22686, 'v'),
                                                                      1760: (23381, 'P'),
                                                                      1778: (23472, 'W'),
                                                                      1820: (23591, '、'),
                                                                      1846: (23731, '@'),
                                                                      2290: (24590, 'u'),
                                                                      2295: (24616, '9'),
                                                                      2304: (24742, 'C'),
                                                                      2310: (24895, '8'),
                                                                      2314: (24974, 'w'),
                                                                      2323: (25166, 'z'),
                                                                      2358: (25321, 'q'),
                                                                      2436: (25588, '.'),
                                                                      2450: (25671, ' '),
                                                                      2657: (26089, 'g'),
                                                                      2730: (26197, 'N'),
                                                                      2758: (26242, ']'),
                                                                      2847: (26352, ';'),
                                                                      2856: (26366, 'x'),
                                                                      2866: (26376, 'B'),
                                                                      2909: (26434, 'S'),
                                                                      2970: (26531, 'f'),
                                                                      3071: (26685, 'T'),
                                                                      3829: (27901, 's'),
                                                                      3965: (28170, '('),
                                                                      4043: (28304, '4'),
                                                                      4086: (28363, 'μ'),
                                                                      4184: (28577, 'h'),
                                                                      4275: (28790, 'n'),
                                                                      4282: (28798, 'V'),
                                                                      4481: (29157, 'o'),
                                                                      4617: (29503, '3'),
                                                                      4689: (30338, 'm'),
                                                                      4701: (30410, '℃'),
                                                                      4739: (30776, 'R'),
                                                                      4784: (31377, '·'),
                                                                      4842: (31908, 'A'),
                                                                      4845: (31967, 'c'),
                                                                      4868: (32422, '<'),
                                                                      4886: (32536, '5'),
                                                                      4906: (32792, 'E'),
                                                                      5026: (33489, '7'),
                                                                      5193: (33900, 'a'),
                                                                      5302: (34164, 'L'),
                                                                      5364: (34299, 'e'),
                                                                      5425: (34468, 'i'),
                                                                      5512: (34945, ','),
                                                                      5609: (35201, '—'),
                                                                      6178: (35947, '%'),
                                                                      6245: (36131, 'p'),
                                                                      6251: (36156, 't'),
                                                                      6263: (36192, 'y'),
                                                                      6320: (36481, 'j'),
                                                                      6370: (36733, 'X'),
                                                                      6372: (36757, '/'),
                                                                      6385: (36816, 'K'),
                                                                      6391: (36828, '6'),
                                                                      6419: (36896, 'l'),
                                                                      6426: (36973, 'b'),
                                                                      6460: (37095, 'G'),
                                                                      6463: (37124, '-'),
                                                                      6465: (37154, '*'),
                                                                      6476: (37213, 'M'),
                                                                      6489: (37326, '“'),
                                                                      6601: (38405, 'D'),
                                                                      6617: (38498, ':'),
                                                                      6619: (38504, 'I'),
                                                                      6647: (38901, 'O'),
                                                                      6822: (39533, '&'),
                                                                      6960: (39789, 'Ê')},
 '4479f6177e1eef6405b126bf96a28ad983d9a4571da7b2fdc9024295143b351e': {5495: (34945, ',')},
 'a4077676ff7cdba73fda62d05d018e26326b0aa5b89261a5dc7d83bb883408d4': {596: (20877, 'Y'),
                                                                      821: (21248, 'H'),
                                                                      1030: (21705, 'U'),
                                                                      1326: (22312, 'Z'),
                                                                      1751: (23381, 'P'),
                                                                      2281: (24590, 'u'),
                                                                      2295: (24742, 'C'),
                                                                      2347: (25321, 'q'),
                                                                      2646: (26089, 'g'),
                                                                      2719: (26197, 'N'),
                                                                      2845: (26366, 'x'),
                                                                      2898: (26434, 'S'),
                                                                      4170: (28577, 'h'),
                                                                      4261: (28790, 'n'),
                                                                      4467: (29157, 'o'),
                                                                      4827: (31908, 'A'),
                                                                      4830: (31967, 'c'),
                                                                      4891: (32792, 'E'),
                                                                      5177: (33900, 'a'),
                                                                      5286: (34164, 'L'),
                                                                      5347: (34299, 'e'),
                                                                      5408: (34468, 'i'),
                                                                      6245: (36192, 'y'),
                                                                      6351: (36733, 'X'),
                                                                      6441: (37095, 'G'),
                                                                      6599: (38504, 'I'),
                                                                      6627: (38901, 'O')},
 'f6ae5fbb30beaecbf884d1dc9fc1ee7ad86f52f40f4fda9cd64c9929579a4722': {612: (20900, ')'),
                                                                      3951: (28170, '(')}}


# Exact raw-code / Unicode / GID bindings from the source glyph atlas. The
# standalone grave accent remains a glyph, not a guessed author-name rewrite.
_TYPE1_NAMES = {'AdvPS44A44B', 'AdvP4C4E74', 'AdvP4C4E59'}
_TYPE1_RULES = {
    '27f0cfeb8b2790e956df94ea3727d214c53bfefd5174d8cd3f8b2c5e5e7b9397': {
        'encoding': '/WinAnsiEncoding',
        'glyphs': {1: (101, 101, '–')},
    },
    'bcb981b144f21b91308e243ced210fd88f751efddb0bbb79b69f1f4d97fcfa82': {
        'encoding': (1, '/C14', 3, '/C2', '/C0', 188, '/onequarter', 254, '/thorn'),
        'glyphs': {1: (188, 188, '='), 2: (254, 254, '+'),
                   3: (1, 65533, '°'), 4: (3, 65533, '×'), 5: (4, 65533, '−')},
    },
    'd48632151a59e1f834410f0235dfcb673bbe84e67a46c5958eff14f020ca7b28': {
        'encoding': (1, '/C18'),
        'glyphs': {1: (1, 65533, '`')},
    },
}

# This extends the existing audited-glyph path, not a parallel converter.
from .legacy_type1_audit import AUDITED_TYPE1_NAMES, AUDITED_TYPE1_RULES
_TYPE1_NAMES.update(AUDITED_TYPE1_NAMES)
_TYPE1_RULES.update(AUDITED_TYPE1_RULES)


def _type1_encoding(document, xref):
    kind, value = document.xref_get_key(xref, 'Encoding')
    if kind == 'name':
        return value
    if kind != 'xref':
        return None
    encoding = int(value.split()[0])
    if document.xref_get_key(encoding, 'BaseEncoding')[0] != 'null':
        return None
    kind, value = document.xref_get_key(encoding, 'Differences')
    if kind != 'array':
        return None
    tokens = re.findall(r'/[^\s\[\]/]+|\d+', value)
    return tuple(token if token.startswith('/') else int(token) for token in tokens)


def _font_plan(document, xref, observed):
    digest = hashlib.sha256(document.extract_font(xref)[3]).hexdigest()
    rules = FONT_GLYPHS.get(digest)
    simple = _TYPE1_RULES.get(digest)
    codes = None
    if simple:
        if (document.xref_get_key(xref, 'Subtype') != ('name', '/Type1')
                or _type1_encoding(document, xref) != simple['encoding']):
            return None
        rules = {gid: (old, text) for gid, (_, old, text) in simple['glyphs'].items()}
        codes = {gid: code for gid, (code, _, _) in simple['glyphs'].items()}
    if not rules or not observed:
        return None
    if not simple:
        if document.xref_get_key(xref, 'Encoding') != ('name', '/Identity-H'):
            return None
        descendants = document.xref_get_key(xref, 'DescendantFonts')
        match = re.fullmatch(r'\[\s*(\d+) 0 R\s*\]', descendants[1])
        if not match or document.xref_get_key(int(match[1]), 'CIDToGIDMap') != ('name', '/Identity'):
            return None
    for cp, gid in observed:
        if gid not in rules or cp not in (rules[gid][0], ord(rules[gid][1])):
            return None
    if all(cp == ord(rules[gid][1]) for cp, gid in observed):
        return None
    # All used codes are covered. Keep mappings for every audited glyph in this
    # program so the plan is not tied to a paper title or its page coordinates.
    width = 2 if simple else 4
    pairs = [f'<{(codes[gid] if simple else gid):0{width}X}> <{text.encode("utf-16-be").hex().upper()}>'
             for gid, (_, text) in sorted(rules.items())]
    cmap = ('/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n'
            '/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n'
            '/CMapName /MemoAuditedEU def\n/CMapType 2 def\n'
            + ('1 begincodespacerange\n<00> <FF>\nendcodespacerange\n' if simple else
               '1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n')
            + f'{len(pairs)} beginbfchar\n' + '\n'.join(pairs)
            + '\nendbfchar\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n')
    return digest, cmap.encode('ascii'), rules


def orient_recovered_pages(document, recovered_pages):
    """Orient sideways horizontal text for the existing Layout parser.

    Only an unambiguous majority of horizontal-writing glyphs establishes a
    cardinal direction. True vertical writing is excluded. Preserve physical
    page numbers and record the transform; the caller owns this in-memory copy.
    """
    receipts = []
    directions = {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}
    for number in sorted(recovered_pages):
        page = document[number - 1]
        counts = Counter()
        for span in page.get_texttrace():
            if span.get('wmode', 0) != 0:
                continue
            direction = tuple(round(v, 3) for v in span['dir'])
            if direction in directions:
                counts[directions[direction]] += sum(not chr(cp).isspace() for cp, *_ in span['chars'])
        if not counts:
            continue
        angle, count = counts.most_common(1)[0]
        if angle == 0 or count <= sum(counts.values()) - count:
            continue
        page.set_rotation(angle)
        matrix = list(page.rotation_matrix)
        page.remove_rotation()
        receipts.append({'page_number': number, 'method': 'PDF_NATIVE_TEXT_ORIENTATION',
            'clockwise_degrees': angle, 'source_to_text_matrix': matrix,
            'direction_glyph_counts': dict(counts)})
    return receipts


@contextmanager
def open_text_pdf(path):
    """Yield a private text document and recovery receipts; source stays read-only."""
    import pymupdf

    document = pymupdf.open(path)
    try:
        observed, by_page, names = {}, {}, {}
        for number, page in enumerate(document, 1):
            fonts = {}
            for row in page.get_fonts(full=True):
                name = row[3].split('+', 1)[-1]
                if re.fullmatch(r'EU-[A-Z0-9]+', name) or name in _TYPE1_NAMES:
                    fonts.setdefault(name, set()).add(row[0])
            # Native text tracing runs the entire page, including scan images.
            # With no audited font resource there is nothing this recovery can
            # change; avoid that work before OCR and during task restart.
            if not fonts:
                continue
            for span in page.get_texttrace():
                name = span['font'].split('+', 1)[-1]
                refs = fonts.get(name, set())
                if len(refs) != 1:
                    continue  # Ambiguous resources cannot identify the font.
                xref = next(iter(refs))
                chars = [(cp, gid) for cp, gid, *_ in span['chars']]
                observed.setdefault(xref, set()).update(chars)
                by_page.setdefault((number, xref), []).extend(chars)
                names[xref] = name
        plans = {xref: plan for xref, chars in observed.items()
                 if (plan := _font_plan(document, xref, chars)) is not None}
        receipts = []
        if plans:
            for xref, (digest, cmap, rules) in plans.items():
                stream = document.get_new_xref()
                document.update_object(stream, '<< >>')
                document.update_stream(stream, cmap)
                document.xref_set_key(xref, 'ToUnicode', f'{stream} 0 R')
                for (number, font), chars in by_page.items():
                    if font == xref:
                        receipts.append({'page_number': number,
                            'method': 'PDF_AUDITED_FONT_UNICODE_RECOVERY',
                            'font_name': names[xref], 'font_sha256': digest,
                            'requires_layout': digest in FONT_GLYPHS,
                            'recovered_characters': sum(cp != ord(rules[gid][1]) for cp, gid in chars),
                            'glyph_pairs_sha256': hashlib.sha256(repr(sorted(set(chars))).encode()).hexdigest()})
            # Reopen only in memory to invalidate the library's cached font maps.
            data = document.tobytes()
            document.close()
            document = pymupdf.open(stream=data, filetype='pdf')
            for page in document:
                resources = {row[3].split('+', 1)[-1]: row[0] for row in page.get_fonts(full=True)}
                for span in page.get_texttrace():
                    xref = resources.get(span['font'].split('+', 1)[-1])
                    if xref in plans:
                        rules = plans[xref][2]
                        for cp, gid, *_ in span['chars']:
                            if gid not in rules or cp != ord(rules[gid][1]):
                                raise RuntimeError('AUDITED_FONT_UNICODE_ROUNDTRIP_MISMATCH')
        yield document, receipts
    finally:
        document.close()
