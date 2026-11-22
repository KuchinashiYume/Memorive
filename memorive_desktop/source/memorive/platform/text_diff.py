"""Comparing two saved texts, line by line and word by word: what a version history shows as removed and added.

Research Chat compares versions of an answer (research/workspace/answer_saves.py), the library versions of a
session refinement's edits (documents/library/product_controller.py); the pages draw the result with
ui/integrated/shared/version-history.js.
"""
import difflib
import re

# Word-level comparison: a run of spaces, one CJK character, a word, or one other character.
TOKEN = re.compile(r'\s+|[぀-ヿ㐀-鿿豈-﫿]|[^\W_぀-ヿ㐀-鿿豈-﫿]+|.', re.S)


def line_diff(old, new):
    """Blocks of equal, inserted, deleted and changed lines; lines keep their line ends. A changed
    block also lists its word-level edits."""
    a, b = old.splitlines(keepends=True), new.splitlines(keepends=True)
    blocks = []
    for kind, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        block = {'kind': {'replace': 'change'}.get(kind, kind), 'old_start': i1 + 1, 'new_start': j1 + 1,
                 'old_lines': a[i1:i2], 'new_lines': b[j1:j2]}
        if kind == 'replace':
            block['words'] = word_diff(''.join(a[i1:i2]), ''.join(b[j1:j2]))
        blocks.append(block)
    return blocks


def word_diff(old, new):
    """Word edits from old to new. The words both ends share are set aside first, so a long block
    with a small change compares only the part that changed."""
    a, b = TOKEN.findall(old), TOKEN.findall(new)
    head = 0
    while head < min(len(a), len(b)) and a[head] == b[head]:
        head += 1
    tail = 0
    while tail < min(len(a), len(b)) - head and a[len(a) - 1 - tail] == b[len(b) - 1 - tail]:
        tail += 1
    out = [{'op': 'equal', 'text': ''.join(a[:head])}] if head else []
    middle_a, middle_b = a[head:len(a) - tail], b[head:len(b) - tail]
    for kind, i1, i2, j1, j2 in difflib.SequenceMatcher(None, middle_a, middle_b, autojunk=False).get_opcodes():
        if kind == 'equal':
            out.append({'op': 'equal', 'text': ''.join(middle_a[i1:i2])})
            continue
        if i2 > i1:
            out.append({'op': 'delete', 'text': ''.join(middle_a[i1:i2])})
        if j2 > j1:
            out.append({'op': 'insert', 'text': ''.join(middle_b[j1:j2])})
    if tail:
        out.append({'op': 'equal', 'text': ''.join(a[len(a) - tail:])})
    return out


__all__ = ["TOKEN", "line_diff", "word_diff"]
