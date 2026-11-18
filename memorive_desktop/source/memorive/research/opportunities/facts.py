"""Paper fact sheets: what research opportunities are found from.

Document processing leaves a card and the text chunks of each paper in the library
(memorive/documents/library: core_artifacts). papers() turns them into one sheet per paper: the
paper's research question, objects, methods, comparison contexts, reported data (metric, value, unit,
sample, statistics), results, conclusion, boundary conditions, and the limitations and future work its
authors state. Each fact has an id (P2.5: paper 2, fact 5), the card field it comes from, and where in
the paper it is: the chunk, its page and section, and the quoted words when the card kept them. A fact
sheet holds only what the card says; nothing is inferred here.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from memorive.documents.front_matter import split_front_matter
from memorive.research.common import sha

LIST_FIELDS = ('research_object', 'method', 'boundary_conditions')
# The files of a paper a follow-up conversation reads: its card, cleaned text and analysis.
LIBRARY_FILES = ('CORE_CARD', 'CORE_CLEAN_DOCUMENT', 'CORE_ANALYSIS')
CONTEXT_PARTS = (('object', '对象'), ('condition', '条件'), ('groups', '分组'), ('scale', '尺度'),
                 ('time', '时间'), ('scenario', '场景'), ('applicability', '适用'))


def card_fields(path: Path) -> dict:
    """The structured fields of a card file (its front matter)."""
    parts = split_front_matter(Path(path).read_text(encoding='utf-8'))
    value = yaml.safe_load(parts[1]) if parts else None
    return value if isinstance(value, dict) else {}


def chunk_texts(path: Path | None) -> dict[str, dict]:
    """chunk id -> {text, page, section} of a paper's chunks file."""
    if path is None or not Path(path).is_file():
        return {}
    chunks = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.strip():
            row = json.loads(line)
            chunks[row['chunk_id']] = {'text': row.get('text') or '', 'page': row.get('page_start'),
                                       'section': row.get('section_path')}
    return chunks


EMPTY = {'未给', 'None', 'null', 'Not given', 'not given', 'n/a', 'N/A'}


def _clean(value) -> str:
    if isinstance(value, list):
        return '；'.join(_clean(item) for item in value if _clean(item))
    text = ' '.join(str(value or '').split())
    return '' if text in EMPTY else text


def _given(value) -> str:
    """A card's "label: value | label: value" text without the parts it marks as not given."""
    parts = [part.strip() for part in _clean(value).split(' | ')]
    return ' | '.join(part for part in parts if part and part.rsplit(':', 1)[-1].strip() not in EMPTY)


def _anchor(card: dict, field: str) -> dict:
    rows = ((card.get('source_anchor') or {}).get('by_field') or {}).get(field) or []
    return rows[0] if rows and isinstance(rows[0], dict) else {}


def sheet(number: int, card: dict, chunks: dict[str, dict]) -> list[dict]:
    """The facts of paper number `number`, in card order."""
    facts: list[dict] = []

    def add(field, text, *, chunk_id=None, quote=None):
        text = _clean(text)
        if not text:
            return
        place = chunks.get(chunk_id or '') or {}
        facts.append({'id': f'P{number}.{len(facts) + 1}', 'field': field, 'text': text,
                      'quote': _clean(quote) or None, 'chunk_id': chunk_id,
                      'page': place.get('page'), 'section': place.get('section')})

    def anchored(field, text):
        anchor = _anchor(card, field)
        add(field, text, chunk_id=anchor.get('chunk_id'), quote=anchor.get('quote'))

    anchored('research_question', card.get('research_question'))
    for field in LIST_FIELDS[:2]:
        anchored(field, card.get(field))
    for context in card.get('comparison_context') or []:
        if isinstance(context, dict):
            parts = [f'{label}：{_clean(context.get(key))}' for key, label in CONTEXT_PARTS if _clean(context.get(key))]
            add('comparison_context', '；'.join(parts))
    for item in card.get('key_data') or []:
        if not isinstance(item, dict):
            continue
        value, unit = _clean(item.get('value')), _clean(item.get('unit'))
        if unit and unit != 'unitless' and not value.endswith(unit):
            value += unit if unit == '%' else ' ' + unit
        extra = '；'.join(part for part in (_given(item.get('sample')), _given(item.get('stat'))) if part)
        add('key_data', f"{_clean(item.get('metric'))}：{value}" + (f'（{extra}）' if extra else ''),
            chunk_id=item.get('chunk_id'), quote=item.get('quote'))
    anchored('key_results', card.get('key_results'))
    anchored('author_conclusion', card.get('author_conclusion'))
    for condition in card.get('boundary_conditions') or []:
        anchor = _anchor(card, 'boundary_conditions')
        add('boundary_conditions', condition, chunk_id=anchor.get('chunk_id'))
    outlook = card.get('author_limitations_outlook') or {}
    for block, field in (('limitations', 'limitation'), ('outlook', 'outlook')):
        for item in outlook.get(block) or []:
            if isinstance(item, dict):
                add(field, item.get('quote') or item.get('text'), chunk_id=item.get('chunk_id'), quote=item.get('quote'))
            else:
                add(field, item)
    return facts


def papers(core_artifacts: dict[str, dict], resolve, only=None) -> list[dict]:
    """One fact sheet per processed paper whose card is admitted (active), oldest job first.

    core_artifacts is the library's core_artifacts(); resolve(artifact_id) gives the file of a
    product (resolve_core_private_artifact); only, when given, is the set of job ids to take (a
    comparison group). Each sheet also names the paper's library files with their content hashes,
    as memo.library_add takes them, so a follow-up conversation can add them."""
    by_job: dict[str, dict[str, str]] = {}
    for artifact_id, record in core_artifacts.items():
        by_job.setdefault(record['job_id'], {})[record['artifact_kind']] = artifact_id
    sheets = []
    for job_id, products in sorted(by_job.items()):
        card_id = products.get('CORE_CARD')
        if not card_id or only is not None and job_id not in only:
            continue
        card = card_fields(resolve(card_id))
        # Only admitted cards, as in retrieval: a pending card has not passed review, a quarantined one
        # was taken out of use.
        if card.get('review_status', 'active') != 'active':
            continue
        chunks_id = products.get('CORE_CHUNKS')
        chunks = chunk_texts(resolve(chunks_id) if chunks_id else None)
        number = len(sheets) + 1
        facts = sheet(number, card, chunks)
        if not facts:
            continue
        title = _clean(card.get('title')) or core_artifacts[card_id]['row'].get('display_name') or job_id
        sheets.append({'paper': f'P{number}', 'title': title, 'job_id': job_id, 'card_artifact_id': card_id,
                       'card_sha256': core_artifacts[card_id].get('content_sha256'),
                       'library_files': [{'id': products[kind], 'content_hash': core_artifacts[products[kind]]['content_sha256']}
                                         for kind in LIBRARY_FILES if kind in products],
                       'facts': facts, 'chunks': chunks})
    return sheets


def fact_key(paper: dict, fact: dict) -> str:
    """A fact's identity across runs: the same card statement keeps its key while the card is unchanged."""
    return sha({'card': paper['card_sha256'], 'field': fact['field'], 'text': fact['text']})[:20]
