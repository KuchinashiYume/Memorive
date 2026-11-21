"""Read a Memo hand-off's sources and return a cited answer, through Memo's published local transport.

    find    HANDOFF TERM [TERM ...] [--artifact ID]     print the passages that contain a term, numbered
                                                        E1, E2 ..., and keep them like read does
    read    HANDOFF [--artifact ID] [--pages A-B]       fetch the evidence, number it E1, E2 ... and write
                                                        work/evidence.md (to read) and work/evidence.json
    answer  HANDOFF --draft DRAFT.md [--gap TEXT] [--return]
                                                        turn a draft with [E3: "quote"] marks into
                                                        memo-answer.json, check it, and optionally return it
    return  HANDOFF --result memo-answer.json           return a saved answer to the Memo conversation
    context HANDOFF [--artifact ID] [--cursor N] [--limit N]
                                                        one raw page of memo.skill_context (JSON)
    bind    HANDOFF --analysis A.md --citations C.json --request-id ID --out memo-answer.json
                                                        map a standalone answer's local citations to Memo IDs

The transport is the command in transport.json beside handoff.json; this script never opens Memo's
database. Work files go to <handoff folder>/work unless --work names another folder.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import drafts

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8')

ANSWER_SCHEMA = 'memo-research-answer/1'
BRIDGE_METHODS = {'memo.capabilities', 'memo.skill_context', 'memo.skill_answer'}
# [E3: "quote"] · [E3: "quote"; E5: "quote"] · [inference E3: "quote"; E5: "quote"]
# What Memo's refusals mean for the agent: keep the answer, the hand-off cannot take it any more.
REFUSALS = {
    'HANDOFF_VERSION_CHANGED': 'the conversation or its sources changed after this hand-off was made',
    'HANDOFF_SOURCE_CHANGED': 'a source of this hand-off changed after it was made',
    'HANDOFF_THREAD_CHANGED': 'the conversation was archived or moved after this hand-off was made',
    'HANDOFF_NOT_FOUND': 'Memorive does not know this hand-off',
}
NOT_REACHED = ('Answer from the extracted texts in materials/ (sources.md says which file is which paper), giving '
               'page numbers, and tell the user the answer could not be returned: approving this command lets it '
               'be returned, or the user can add the saved answer in the Memorive conversation.')


class NotReached(ValueError):
    """Memorive's local command could not be run (blocked, missing, timed out), as opposed to Memo
    refusing a request."""


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


class Memo:
    """The Memo host behind one hand-off: its transport command and binding."""

    def __init__(self, handoff, transport=None):
        self.handoff = Path(handoff).resolve()
        self.pack = read_json(self.handoff)
        self.transport = read_json(transport or self.handoff.parent / 'transport.json')
        self.binding = {'project': self.pack['project'], 'handoff_id': self.pack['id'],
                        'handoff_hash': self.pack['content_hash']}
        self._methods = None

    def call(self, method, params):
        command = self.transport['command']
        if not isinstance(command, list) or not command or any(not isinstance(s, str) for s in command):
            raise ValueError('Invalid configured Memo transport command')
        if method not in BRIDGE_METHODS:
            raise ValueError('Unsupported bridge method')
        # errors='replace': a failing command may report in the console's own encoding (a Windows path
        # in GBK, for instance); its message must still reach the user instead of a decoding error.
        try:
            run = subprocess.run(command + ['--call', method], input=json.dumps(params, ensure_ascii=False),
                                 encoding='utf-8', errors='replace', capture_output=True, timeout=120, shell=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise NotReached(f'Memorive could not be reached: {error}') from error
        if run.returncode:
            message = (run.stderr or '').strip()
            # Memo answers a refused request with one code; anything else means the command did not run.
            if re.fullmatch(r'[A-Z][A-Z0-9_:]{1,95}', message):
                meaning = REFUSALS.get(message)
                raise ValueError(message + (f': {meaning}. Keep your answer; a new hand-off is needed to return it.'
                                            if meaning else ''))
            raise NotReached('Memorive could not be reached: ' + (message[-300:] or 'the local command failed'))
        return json.loads(run.stdout)

    def require(self, method):
        """Older Memo releases offer only some methods: keep local results instead of claiming a return."""
        if self._methods is None:
            caps = self.call('memo.capabilities', {})
            self._methods = set(caps.get('tools', [])) | set(caps.get('methods', {}))
        if method not in self._methods:
            raise ValueError('Memo host does not support ' + method + '; keep local artifacts, do not claim a return')

    def pages(self, artifact_id=None):
        """Every evidence chunk of the hand-off (or of one source), page by page."""
        self.require('memo.skill_context')
        cursor, refs, first = 0, [], None
        while cursor is not None:
            params = dict(self.binding, cursor=cursor, limit=24)
            if artifact_id:
                params['artifact_id'] = artifact_id
            page = self.call('memo.skill_context', params)
            first = first or page
            refs.extend(page['evidence'])
            cursor = page['next_cursor']
        return first, refs


# --- read: fetch the evidence once, number it, write it to read ------------------------------------

def page_range(text):
    if not text:
        return None
    low, _, high = text.partition('-')
    return int(low), int(high or low)


def fetch(memo, work, artifact_id=None):
    """The kept evidence of work/evidence.json and every passage of the hand-off (or of one source)."""
    work.mkdir(parents=True, exist_ok=True)
    store_path = work / 'evidence.json'
    store = read_json(store_path) if store_path.is_file() else {'handoff_id': memo.binding['handoff_id'], 'evidence': {}}
    if store['handoff_id'] != memo.binding['handoff_id']:
        raise ValueError('work/evidence.json belongs to another hand-off; use a new --work folder')
    first, refs = memo.pages(artifact_id)
    store['question'], store['sources'] = first['question'], first['sources']
    totals = store.setdefault('totals', {})
    for artifact in {ref['artifact_id'] for ref in refs}:
        totals[artifact] = sum(ref['artifact_id'] == artifact for ref in refs)
    return store, refs


def keep(store, refs, work):
    """Add refs to the kept evidence (aliases E1, E2 ... kept across calls), write work/evidence.json
    and work/evidence.md, and return the aliases of refs."""
    aliases = {row['id']: alias for alias, row in store['evidence'].items()}
    for ref in refs:
        alias = aliases.get(ref['id']) or 'E' + str(len(store['evidence']) + 1)
        aliases[ref['id']] = alias
        store['evidence'][alias] = {k: ref.get(k) for k in ('id', 'content_hash', 'artifact_id', 'title', 'page',
                                                               'line_start', 'line_end', 'text')}
    (work / 'evidence.json').write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding='utf-8')
    (work / 'evidence.md').write_text(evidence_markdown(store), encoding='utf-8')
    return [aliases[ref['id']] for ref in refs]


def read_sources(memo, work, artifact_id=None, pages=None):
    """Fetch the evidence into work/evidence.json and write work/evidence.md, every chunk read so far
    by source and position."""
    store, refs = fetch(memo, work, artifact_id)
    span = page_range(pages)
    keep(store, [ref for ref in refs if not span or ref.get('page') is None or span[0] <= ref['page'] <= span[1]], work)
    return store


# --- find: the passages that hold the question's terms ---------------------------------------------

CAPTION = re.compile(r'^\s*(?:Table|Tab\.|Figure|Fig\.|表|图)\s*[\dA-Z]', re.IGNORECASE)


def find_passages(memo, work, terms, artifact_id=None, shown=12):
    """Keep the passages holding any term (most terms first, then in reading order) and return what to
    print: each passage's alias, source, page and its matching lines, with table and figure titles."""
    store, refs = fetch(memo, work, artifact_id)
    wanted = [drafts._even(t)[0].strip().lower() for t in terms if t.strip()]
    order = {s['id']: n for n, s in enumerate(store['sources'])}
    hits = []
    for ref in refs:
        lines = ref['text'].splitlines()
        even = [drafts._even(line)[0].lower() for line in lines]
        whole = ' '.join(even)
        matched = [t for t in wanted if t in whole]
        if matched:
            hits.append((-len(set(matched)), order.get(ref['artifact_id'], 99), ref.get('page') or 0, ref['line_start'],
                         ref, lines, even))
    hits.sort(key=lambda h: h[:4])
    aliases = keep(store, [h[4] for h in hits[:shown]], work)
    out = []
    for alias, (_, _, _, _, ref, lines, even) in zip(aliases, hits):
        rows = set()
        for n, line in enumerate(even):
            if any(t in line for t in wanted):
                rows.update(range(max(0, n - 2), min(len(lines), n + 3)))
        rows.update(n for n, line in enumerate(lines) if CAPTION.match(line))
        out.append(f"\n{alias} · {ref.get('title') or ref['artifact_id']} · {position(ref)}")
        out += [('  > ' if any(t in even[n] for t in wanted) else '    ') + lines[n] for n in sorted(rows)]
    if len(hits) > shown:
        out.append(f'\n{len(hits) - shown} more passages match; search with more specific terms to see them.')
    if not hits:
        out.append('No passage contains these terms. Try other words or the numbers you expect, or read the '
                   'extracted texts in materials/.')
    return out


def position(row):
    page = f"p.{row['page']} · " if row.get('page') is not None else ''
    return f"{page}lines {row['line_start']}–{row['line_end']}"


def evidence_markdown(store):
    lines = ['# Evidence', '', 'Question: ' + store['question'], '',
             'Cite a passage as [E3: "short exact quote"]. Quotes must be copied from the passage text.', '']
    order = {s['id']: n for n, s in enumerate(store['sources'])}
    rows = sorted(store['evidence'].items(), key=lambda item: (order.get(item[1]['artifact_id'], 99),
                                                               item[1].get('page') or 0, item[1]['line_start']))
    current = None
    for alias, row in rows:
        if row['artifact_id'] != current:
            current = row['artifact_id']
            lines += ['## ' + (row.get('title') or current), '']
        lines += [f'### {alias} · {position(row)}', '', row['text'].rstrip(), '']
    return '\n'.join(lines)


def read_summary(store):
    read = {}
    for row in store['evidence'].values():
        read.setdefault(row['artifact_id'], set()).add(row['id'])
    out = []
    for source in store['sources']:
        out.append(f"{source.get('title') or source['id']}: {len(read.get(source['id'], ()))} of "
                   f"{source.get('chunks') or '?'} chunks read")
    return out


# --- answer: draft with marks -> checked memo-answer.json ------------------------------------------

def build_answer(store, draft, gaps=None, request_id=None):
    """The memo-answer.json of a draft (drafts.py): each [E..] mark becomes [n], n numbering the
    passages in first-use order, and the text before it a claim with its supports. Every problem
    is reported at once."""
    cited = []

    def resolve(alias, where, quote):
        row = store['evidence'].get(alias)
        if row is None:
            raise ValueError(f'{alias} has not been read: run `find` or `read` for its source first')
        exact = drafts.locate(quote, row['text'])   # the passage's own text, which Memo checks again
        if exact is None:
            elsewhere = [other for other, text in store['evidence'].items()
                         if other != alias and drafts.locate(quote, text['text']) is not None]
            hint = (f'it is in {", ".join(elsewhere)}: cite that passage' if elsewhere else
                    f'the closest text in {alias} is: "{drafts.closest(quote, row["text"])}"')
            raise ValueError(f'{alias}: the quote is not in the passage: "{quote[:120]}"; {hint}')
        return {'evidence_id': row['id'], 'content_hash': row['content_hash'], 'quote': exact}, row['text']

    def render(supports):
        ids = list(dict.fromkeys(s['evidence_id'] for s in supports))
        cited.extend(i for i in ids if i not in cited)
        return ''.join(f'[{cited.index(i) + 1}]' for i in ids)

    def where_value(value):
        return [alias for alias, row in store['evidence'].items() if value in drafts.numbers(row['text'])]

    answer, claims, problems = drafts.apply(draft, resolve, render, where_value)
    loose = [] if problems else uncited_values(answer, claims, store)
    if loose:
        problems.append(f'{", ".join(f"{v:g}" for v in loose)}: in the answer without a cited passage. Put each value '
                        'in a sentence with its passage and quote (find it with the `find` command), mark a value '
                        'you computed as [inference ...], or delete it: a value from outside the material cannot be '
                        'cited here.')
    if problems:
        raise ValueError('The draft needs changes:\n- ' + '\n- '.join(problems))
    coverage = []
    for source in store['sources']:
        read = sorted((row for row in store['evidence'].values() if row['artifact_id'] == source['id']),
                      key=lambda row: (row.get('page') or 0, row['line_start']))
        total = store.get('totals', {}).get(source['id'], 0)
        status = 'unread' if not read else 'read' if total and len(read) >= total else 'partial'
        note = '' if status == 'read' else 'Not read.' if status == 'unread' else (
            f'{len(read)} of {total} chunks read' + (f" (pages {read[0]['page']}–{read[-1]['page']})"
                                                      if read[0].get('page') is not None else '') + '.')
        if status != 'read' and gaps:
            note += ' ' + gaps
        coverage.append({'artifact_id': source['id'], 'status': status, 'gaps': note.strip()})
    request_id = request_id or 'skill-' + hashlib.sha256(json.dumps([store['handoff_id'], answer, claims],
                                                                     ensure_ascii=False).encode()).hexdigest()[:24]
    return {'answer': answer, 'claims': claims, 'coverage': coverage, 'request_id': request_id}


def uncited_values(answer, claims, store):
    """Values in the answer outside its claims that no kept passage or the question reports (page
    references, names and years aside): the place a value recalled from outside the material shows up."""
    text = re.sub(r'\[\d+\]', ' ', answer)
    for claim in claims:
        text = text.replace(claim['text'], ' ')
    known = set(drafts.numbers(store.get('question') or ''))
    for row in store['evidence'].values():
        known |= drafts.numbers(row['text'])
    return sorted(drafts.stated_values(text) - known)


def return_answer(memo, result):
    if result.pop('schema', None) != ANSWER_SCHEMA:
        raise ValueError('Unsupported answer file')
    if any(result.get(k) != v for k, v in memo.binding.items()):
        raise ValueError('Answer belongs to another handoff')
    memo.require('memo.skill_answer')
    return memo.call('memo.skill_answer', result)


# --- bind: a standalone answer's local citations -> Memo evidence ----------------------------------

def bind_local(memo, analysis, citations, request_id):
    """Map verified standalone RawMD spans to real Memo IDs without re-extracting."""
    import sources
    first, refs = memo.pages()
    local = read_json(citations)
    if local['question'].strip() != memo.pack['question'].strip():
        raise ValueError('Question changed: reason about the current question before binding a saved answer')
    answer = Path(analysis).read_text(encoding='utf-8-sig')
    claims, used = [], set()
    for claim in local['claims']:
        if claim['text'] not in answer:
            raise ValueError('Claim text must occur verbatim in the delivered answer')
        mapped = []
        for support in claim['supports']:
            if support.get('checked') is not True:
                raise ValueError('Unchecked source support')
            manifest = Path(support['manifest'])
            if not manifest.is_absolute():
                manifest = Path(citations).resolve().parent / manifest
            data = sources.verify(manifest)
            if support['source_sha256'] != data['source_sha256']:
                raise ValueError('Local source identity mismatch')
            start, end = support['line_start'], support['line_end']
            sources.evidence(manifest, page=support['page'], span=f'{start}:{end}', quote=support.get('quote'))
            matches = [r for r in refs if r.get('source_content_hash') == data['source_sha256']
                       and r['content_hash'] == data['raw']['sha256'] and r.get('line_basis') == 'rawmd_global'
                       and r['page'] == support['page'] and r['line_start'] <= end and r['line_end'] >= start]
            covered = set()
            for ref in matches:
                low, high = max(start, ref['line_start']), min(end, ref['line_end'])
                quote = '\n'.join(ref['text'].splitlines()[low - ref['line_start']:high - ref['line_start'] + 1])
                if quote.strip():
                    mapped.append({'evidence_id': ref['id'], 'content_hash': ref['content_hash'], 'quote': quote})
                    used.add(ref['artifact_id'])
                covered.update(range(low, high + 1))
            if not set(range(start, end + 1)) <= covered or not matches:
                raise ValueError('Source/span has no exact Memo mapping; attach its source.json in Memo and create a fresh handoff')
        claims.append({'text': claim['text'], 'kind': claim['kind'], 'supports': mapped})
    coverage = [{'artifact_id': s['id'], 'status': 'partial' if s['id'] in used else 'unread',
                 'gaps': 'Only mapped claims recorded; whole-source reading not assessed.' if s['id'] in used
                         else 'Not covered by this standalone answer.'} for s in first['sources']]
    return dict(memo.binding, schema=ANSWER_SCHEMA, request_id=request_id, answer=answer, claims=claims, coverage=coverage)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--transport', type=Path, help='Explicit configured transport; defaults beside handoff.json')
    sub = parser.add_subparsers(dest='action', required=True)
    find = sub.add_parser('find'); find.add_argument('handoff', type=Path); find.add_argument('terms', nargs='+')
    find.add_argument('--artifact'); find.add_argument('--work', type=Path)
    read = sub.add_parser('read'); read.add_argument('handoff', type=Path)
    read.add_argument('--artifact'); read.add_argument('--pages'); read.add_argument('--work', type=Path)
    answer = sub.add_parser('answer'); answer.add_argument('handoff', type=Path)
    answer.add_argument('--draft', type=Path, required=True); answer.add_argument('--gap')
    answer.add_argument('--request-id'); answer.add_argument('--work', type=Path)
    answer.add_argument('--out', type=Path); answer.add_argument('--return', dest='send', action='store_true')
    submit = sub.add_parser('return'); submit.add_argument('handoff', type=Path)
    submit.add_argument('--result', type=Path, required=True)
    context = sub.add_parser('context'); context.add_argument('handoff', type=Path)
    context.add_argument('--artifact'); context.add_argument('--cursor', type=int, default=0)
    context.add_argument('--limit', type=int, default=12)
    bind = sub.add_parser('bind'); bind.add_argument('handoff', type=Path)
    bind.add_argument('--analysis', type=Path, required=True); bind.add_argument('--citations', type=Path, required=True)
    bind.add_argument('--request-id', required=True); bind.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    memo = Memo(args.handoff, args.transport)
    work = getattr(args, 'work', None) or memo.handoff.parent / 'work'
    if args.action in {'find', 'read'}:
        try:
            if args.action == 'find':
                print('\n'.join(find_passages(memo, work, args.terms, args.artifact)
                                + [f'\nThese passages are kept in {work / "evidence.md"}; cite them as [E1: "quote"].']))
            else:
                store = read_sources(memo, work, args.artifact, args.pages)
                print('\n'.join([f'Read into {work / "evidence.md"} ({len(store["evidence"])} passages, '
                                 f'E1–E{len(store["evidence"])}).'] + read_summary(store)))
        except NotReached as error:
            raise NotReached(f'{error}\n{NOT_REACHED}') from error
    elif args.action == 'answer':
        if not (work / 'evidence.json').is_file():
            raise ValueError('No passages kept yet: run `find` or `read` first')
        store = read_json(work / 'evidence.json')
        built = build_answer(store, Path(args.draft).read_text(encoding='utf-8-sig'), args.gap, args.request_id)
        result = dict(memo.binding, schema=ANSWER_SCHEMA, **built)
        out = args.out or memo.handoff.parent / 'memo-answer.json'
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        report = {'status': 'SAVED', 'next': f'The answer is checked and saved in {out}. Return it with the `return` '
                                             'command, or run `answer` again with --return.',
                  'saved': str(out), 'claims': len(result['claims']),
                  'citations': len({s['evidence_id'] for c in result['claims'] for s in c['supports']}),
                  'coverage': {c['artifact_id']: c['status'] for c in result['coverage']}}
        if args.send:
            try:
                report['returned'] = return_answer(memo, dict(result))
                report['status'] = 'RETURNED'
                report['next'] = ('The answer is in the Memorive conversation with its citations. Give the user the '
                                  'answer in brief and say it was returned; nothing else is needed.')
            except NotReached as error:
                report['returned'] = {'status': 'NOT_RETURNED', 'reason': str(error)}
                report['status'] = 'NOT_RETURNED'
                report['next'] = (f'The answer is complete and saved in {out}, but Memorive could not be reached. '
                                  'Give the user the answer, and tell them they can approve this command and run it '
                                  'again, or add that file with the attachment button of the Memorive conversation.')
        print(json.dumps(report, ensure_ascii=False, indent=1))
    elif args.action == 'return':
        print(json.dumps(return_answer(memo, read_json(args.result)), ensure_ascii=False))
    elif args.action == 'context':
        memo.require('memo.skill_context')
        params = dict(memo.binding, cursor=args.cursor, limit=args.limit)
        if args.artifact:
            params['artifact_id'] = args.artifact
        print(json.dumps(memo.call('memo.skill_context', params), ensure_ascii=False))
    else:
        result = bind_local(memo, args.analysis, args.citations, args.request_id)
        with args.out.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        print(json.dumps({'status': 'BOUND_LOCAL', 'path': str(args.out), 'returned': False}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
