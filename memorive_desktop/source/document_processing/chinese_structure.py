"""Chinese heading and wrapped-clause recognition; no translation or inference."""
import re

HAN = r'\u3400-\u9fff'
_LABEL = re.compile(r'^(摘要|关键词|引言|绪论|结论(?:与展望)?|总结(?:与展望)?|参考文献|致谢|附录)(?:\s*[:：]\s*(.*))?$')
_NUMBER = re.compile(r'^(\d+(?:\s*[.．]\s*\d+)*)(?:[.．])?\s*(.+)$')
_CHAPTER = re.compile(r'^第[一二三四五六七八九十百零〇\d]+[章节篇]\s*.+$')


def join_han_spaces(text):
    return re.sub(fr'(?<=[{HAN}])\s+(?=[{HAN}])', '', text)


def heading(line):
    text = join_han_spaces(re.sub(r'^[#>\s]+|[*_]', '', re.sub(r'<[^>]+>', '', line)).strip())
    # Strip matched wrappers only around a complete recognized section label;
    # numbered citations and bracketed body sentences remain ordinary text.
    wrapped = re.fullmatch(r'(?:\[\s*(.*?)\s*\]|【\s*(.*?)\s*】|［\s*(.*?)\s*］)', text)
    if wrapped:
        label_text = next(value for value in wrapped.groups() if value is not None).strip()
        if _LABEL.fullmatch(label_text):
            text = label_text
    if is_running_heading(text):
        return None
    label = _LABEL.fullmatch(text)
    if label:
        return label.group(1), label.group(2)
    if _CHAPTER.fullmatch(text) and not re.search(r'[。；;]', text):
        return text, None
    m = _NUMBER.fullmatch(text)
    if not m or not re.search(fr'[{HAN}]', m.group(2)):
        return None
    num = re.sub(r'\s|．', lambda x: '.' if x.group() == '．' else '', m.group(1))
    title = m.group(2).strip()
    if (len(num.split('.')[0]) >= 4 or re.match(r'^[)）\-−－·⋅/\\,，]', title)
            or re.search(r'[。；;，,＝=±%℃]', title)
            or re.match(r'^(?:kgCOD|kg|mg|g|mmol|mol|mL|retrieval|evidence_extraction|L|μg|µg)\s*[/·⋅]', title)
            or re.match(r'^(?:次|天|年|月|小时|分钟|毫[升克摩]|微[升克摩])(?:\s|\b)', title)):
        return None
    return num + ' ' + title, None


def is_running_heading(text):
    return bool(re.search(r'第\s*\d+\s*[卷期]', text))


def structure_lines(body):
    lines = body.splitlines()
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Native PDF text can put a heading number on a line of its own.
        if re.fullmatch(r'\s*\d+(?:\s*[.．]\s*\d+)*\s*', line) and i+1 < len(lines):
            parsed = heading(line.strip()+' '+lines[i+1].strip())
            if parsed:
                i += 1
            else:
                parsed = heading(line)
        else:
            parsed = heading(line)
        if parsed:
            title, rest = parsed
            out.extend(['', '# '+title, ''])
            if rest:
                out.extend([rest, ''])
        else:
            # Some converters themselves mark an isolated exponent/quantity as
            # a heading. Keep its text; it must not become a section and vanish.
            if (line.lstrip().startswith('#') and re.search(fr'[{HAN}]', line)
                    and re.match(r'^\s*#+\s*[*_]*\d', line) and not is_running_heading(line)):
                line = re.sub(r'^\s*#+\s*', '', line)
            out.append(line)
        i += 1
    return '\n'.join(out)


def merge_wrapped_paragraphs(blocks):
    out = []
    for block in blocks:
        prev = out[-1] if out else None
        a = '\n'.join(prev['lines']).rstrip() if prev else ''
        b = '\n'.join(block['lines']).lstrip()
        if (prev and prev['type'] == block['type'] == 'paragraph'
                and prev['section'] == block['section']
                and prev['page_end'] <= block['page'] <= prev['page_end'] + 1
                and re.search(fr'[{HAN}]$', a) and re.match(fr'[{HAN}]', b)
                and '参考文献' not in prev['section']):
            # Merely retain an uninterrupted Chinese clause across a converter
            # blank line/page boundary; page range still covers both sources.
            prev['lines'].extend(block['lines'])
            prev['page_end'] = block['page_end']
        else:
            out.append(dict(block, lines=list(block['lines'])))
    return out
