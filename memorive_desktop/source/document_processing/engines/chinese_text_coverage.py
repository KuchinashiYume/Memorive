"""Recover omitted digital Chinese text from the PDF text layer, never by guessing.

The layout converter can classify body text as a picture (including malformed
glyph bounding boxes). Disabling pictures globally also mixes chart ticks into
paragraphs. Instead, retain the converter output and restore only missing text
runs, with page/block/coordinate and content-hash receipts. Original characters,
including questionable units and printed mistakes, are kept unchanged.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from contextlib import nullcontext

_HAN = re.compile(r"[\u3400-\u9fff]")
_PAGE = re.compile(r"<!--\s*memorive:page:(\d+)\s*-->")


def _han(text):
    return ''.join(_HAN.findall(text))


def _han_positions(text):
    pairs = [(m.group(), m.start()) for m in _HAN.finditer(text)]
    return ''.join(c for c, _ in pairs), [p for _, p in pairs]


def _read_document(source):
    import pymupdf
    return nullcontext(source) if isinstance(source, pymupdf.Document) else pymupdf.open(source)


def _native_line_text(line):
    """Preserve directly observed numeric superscripts on recovered PDF lines.

    Reuse DocumentChunk's existing <sup> to ^ conversion. Ordinary digits, subscripts,
    and inferred powers are never rewritten from the sentence's meaning.
    """
    parts = []
    previous = None
    for span in line['spans']:
        text = span['text']
        if (previous is not None and span.get('flags', 0) & 1
                and re.fullmatch(r'\s*[+\-−]?\d+(?:[.,]\d+)?\s*', text)
                and span['size'] < previous['size'] * .9
                and span['origin'][1] < previous['origin'][1] - previous['size'] * .15):
            text = '<sup>' + text + '</sup>'
        parts.append(text)
        if span['text'].strip() and not span.get('flags', 0) & 1:
            previous = span
    return ''.join(parts)


def font_unicode_conflicts(path):
    """Detect the observed Founder EU Latin/symbol-font Han decoding conflict.

    This is a source-font risk signal, never a Han replacement dictionary.
    Font name alone cannot supply correct text: affected pages require the
    existing image transcription route. Ordinary Chinese fonts stay native.
    """
    import pymupdf
    details = []
    with _read_document(path) as document:
        programs = {}
        for number, page in enumerate(document, 1):
            spans = page.get_texttrace()
            visible_chars = sum(len(s['chars']) for s in spans if s.get('type') != 3 and s.get('opacity', 1) > 0)
            invisible_chars = sum(len(s['chars']) for s in spans if s.get('type') == 3 or s.get('opacity', 1) == 0)
            image_fraction = max((__import__('pymupdf').Rect(i['bbox']).get_area() / page.rect.get_area()
                                  for i in page.get_image_info()), default=0)
            if invisible_chars > visible_chars and image_fraction >= 0.8:
                details.append({'page_number': number, 'method': 'PDF_HIDDEN_OCR_LAYER',
                    'risk_reason': 'unverified_hidden_ocr', 'requires_page_ocr': True,
                    'invisible_characters': invisible_chars, 'visible_characters': visible_chars,
                    'largest_image_fraction': image_fraction})
            traced_characters = sum(len(s['chars']) for s in spans)
            unmapped = sum(cp == 0xfffd for s in spans for cp, *_ in s['chars'])
            if unmapped:
                unmapped_ratio = unmapped / max(traced_characters, 1)
                requires_page_ocr = unmapped_ratio >= 0.20
                details.append({'page_number': number, 'method': 'PDF_UNMAPPED_GLYPHS',
                    'risk_reason': ('unmapped_source_glyph' if requires_page_ocr
                                    else 'sparse_unmapped_source_glyph_retained'),
                    'requires_page_ocr': requires_page_ocr,
                    'unmapped_characters': unmapped,
                    'traced_characters': traced_characters,
                    'unmapped_ratio': unmapped_ratio})
            blank_glyphs = {}
            for span in spans:
                for cp, gid, *_ in span['chars']:
                    if cp == 32 and gid:
                        blank_glyphs.setdefault(span['font'], set()).add(gid)
            collisions = {name: sorted(gids) for name, gids in blank_glyphs.items() if len(gids) >= 4}
            if collisions:
                details.append({'page_number': number, 'method': 'PDF_BLANK_UNICODE_COLLISION',
                    'risk_reason': 'blank_glyph_collision', 'requires_page_ocr': True,
                    'font_glyph_ids': collisions})
            affected = {}
            fonts = {}
            for row in page.get_fonts(full=True):
                name = row[3].split('+', 1)[-1]
                if not re.fullmatch(r'EU-[A-Z0-9]+', name):
                    continue
                if row[0] not in programs:
                    programs[row[0]] = hashlib.sha256(document.extract_font(row[0])[3]).hexdigest()
                fonts[name] = programs[row[0]]
            for span in spans:
                name = span['font'].split('+', 1)[-1]
                if name not in fonts:
                    continue
                count = sum(0x3400 <= cp <= 0x9fff for cp, *_ in span['chars'])
                if count:
                    affected[name] = affected.get(name, 0) + count
            if affected:
                details.append({'page_number': number, 'method': 'PDF_FONT_UNICODE_CONFLICT',
                    'requires_page_ocr': True,
                    'fonts': [{'name': name, 'font_sha256': fonts[name], 'conflicting_characters': count}
                              for name, count in sorted(affected.items())]})
    return details


def recover_chinese_text(path, markdown, *, excluded_pages=(), include_short_runs=False):
    import pymupdf

    marks = list(_PAGE.finditer(markdown))
    if not marks:
        return markdown, []
    output, receipts = [markdown[:marks[0].start()]], []
    with _read_document(path) as document:
        for pi, mark in enumerate(marks):
            number = int(mark.group(1))
            end = marks[pi + 1].start() if pi + 1 < len(marks) else len(markdown)
            body = markdown[mark.end():end]
            if number in excluded_pages:
                output.extend([mark.group(), body])
                continue
            page = document[number - 1]
            blocks = []
            for bi, block in enumerate(page.get_text('dict')['blocks']):
                if block.get('type') != 0:
                    continue
                lines = [_native_line_text(line) for line in block['lines']]
                blocks.append((bi, block, lines))
            # Normal paragraph recovery uses clauses. On the audited-font path
            # the Layout parser can also omit short author/label runs; retain
            # every nonempty native Han run there, with the same provenance.
            minimum_han = 1 if include_short_runs else 6
            searchable, positions = _han_positions(body)
            inserts = []
            previous_end = None
            for bi, block, lines in blocks:
                missing = [i for i, line in enumerate(lines)
                           if len(_han(line)) >= minimum_han and _han(line) not in searchable]
                present = [line for line in lines if len(_han(line)) >= minimum_han and _han(line) in searchable]
                if not missing:
                    for line in present:
                        hit = searchable.find(_han(line))
                        previous_end = positions[hit + len(_han(line)) - 1] + 1
                    continue
                indexes = set(missing)
                # Include adjacent Latin/unit-only continuations and separated
                # section numbers, without bringing in separate chart blocks.
                for index in missing:
                    j = index - 1
                    while j >= 0 and not _han(lines[j]):
                        indexes.add(j); j -= 1
                    j = index + 1
                    while j < len(lines) and not _han(lines[j]):
                        indexes.add(j); j += 1
                restored = '\n'.join(lines[i] for i in sorted(indexes)).strip()
                if not restored or '\ufffd' in restored:
                    continue  # Existing undecodable-glyph gate remains authoritative.
                anchor = previous_end
                if anchor is not None:
                    # Finish the existing line, so a Han-only match cannot cut
                    # its attached units, punctuation or closing markup.
                    line_end = body.find('\n', anchor)
                    anchor = line_end if line_end >= 0 else len(body)
                else:
                    # No reliable preceding clause: retain the text on its
                    # authoritative physical page rather than inventing order.
                    anchor = len(body)
                inserts.append((anchor, '\n\n<!-- memorive:native-text-recovery -->\n'+restored+'\n\n'))
                receipts.append({'page':number, 'block_index':bi,
                    'bbox':list(block['bbox']), 'method':'PDF_NATIVE_OMITTED_TEXT',
                    'text_sha256':hashlib.sha256(restored.encode('utf8')).hexdigest(),
                    'restored_lines':len(indexes), 'anchored_to_preceding_clause':previous_end is not None})
            for at, value in reversed(sorted(inserts, key=lambda x:x[0])):
                body = body[:at]+value+body[at:]
            output.extend([mark.group(), body])
    restored, latin_receipts = recover_latin_text(path, ''.join(output), excluded_pages=excluded_pages)
    return restored, [*receipts, *latin_receipts]


def _search_text(text):
    # HTML superscript tags and Markdown link destinations are presentation,
    # not source characters. Keep original offsets for safe insertion anchors.
    visible = list(text)
    for pattern in (r'<!--.*?-->', r'<[^>\n]+>', r'\]\([^\n)]*\)'):
        for match in re.finditer(pattern, text, re.DOTALL):
            for index in range(match.start(), match.end()):
                visible[index] = ' '
    chars, positions = [], []
    for index, char in enumerate(visible):
        for value in unicodedata.normalize('NFKC', char).casefold():
            if value.isalnum():
                chars.append(value)
                positions.append(index)
    return ''.join(chars), positions


def recover_latin_text(path, markdown, *, excluded_pages=()):
    """Restore omitted prose using literal native text and page-local anchors.

    Short chart ticks/labels are not prose. No spelling, formula or scientific
    notation repair is inferred here. Suspect OCR layers are excluded upstream.
    """
    marks = list(_PAGE.finditer(markdown))
    if not marks:
        return markdown, []
    output, receipts = [markdown[:marks[0].start()]], []
    with _read_document(path) as document:
        for pi, mark in enumerate(marks):
            number = int(mark.group(1))
            end = marks[pi+1].start() if pi+1 < len(marks) else len(markdown)
            body = markdown[mark.end():end]
            if number in excluded_pages:
                output.extend([mark.group(), body])
                continue
            searchable, positions = _search_text(body)
            previous_end, inserts, recovered = None, [], set()
            for bi, block in enumerate(document[number-1].get_text('dict')['blocks']):
                if block.get('type') != 0:
                    continue
                lines = [_native_line_text(line).strip() for line in block['lines']]
                prose = [i for i, line in enumerate(lines)
                         if len(re.findall(r'[A-Za-z]{2,}', line)) >= 4
                         and len(re.findall(r'[A-Za-z]', line)) >= 20 and not _han(line)]
                missing = []
                for i in prose:
                    norm, _ = _search_text(lines[i])
                    hit = searchable.find(norm)
                    if hit >= 0:
                        if searchable.count(norm) == 1:
                            previous_end = positions[hit+len(norm)-1]+1
                    elif norm not in recovered and '\ufffd' not in lines[i]:
                        missing.append(i)
                if not missing:
                    continue
                # Include an adjacent short continuation only when this same
                # text block has omitted prose. Independent chart labels stay out.
                indexes = set(missing)
                for i in missing:
                    for j in (i-1, i+1):
                        if 0 <= j < len(lines) and j not in prose:
                            norm, _ = _search_text(lines[j])
                            if (len(norm) >= 4 and not norm.isdigit() and
                                    norm not in searchable and '\ufffd' not in lines[j]):
                                indexes.add(j)
                restored = '\n'.join(lines[i] for i in sorted(indexes)).strip()
                for i in indexes:
                    recovered.add(_search_text(lines[i])[0])
                anchor = len(body)
                if previous_end is not None:
                    line_end = body.find('\n', previous_end)
                    anchor = line_end if line_end >= 0 else len(body)
                inserts.append((anchor, '\n\n<!-- memorive:native-text-recovery -->\n'+restored+'\n\n'))
                receipts.append({'page': number, 'block_index': bi, 'bbox': list(block['bbox']),
                    'method': 'PDF_NATIVE_OMITTED_TEXT', 'script': 'LATIN',
                    'text_sha256': hashlib.sha256(restored.encode('utf8')).hexdigest(),
                    'restored_lines': len(indexes), 'anchored_to_preceding_clause': previous_end is not None})
            for at, value in reversed(sorted(inserts, key=lambda row: row[0])):
                body = body[:at]+value+body[at:]
            output.extend([mark.group(), body])
    return ''.join(output), receipts
