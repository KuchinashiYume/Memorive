"""Use Memo's published local transport; never open or modify its database."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

import sources


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def call(transport, method, params):
    command = transport['command']
    if not isinstance(command, list) or not command or any(not isinstance(s, str) for s in command):
        raise ValueError('Invalid configured Memo transport command')
    if method not in {'memo.capabilities', 'memo.skill_context', 'memo.skill_answer'}:
        raise ValueError('Unsupported bridge method')
    run = subprocess.run(command + ['--call', method], input=json.dumps(params, ensure_ascii=False),
                         encoding='utf-8', capture_output=True, timeout=90, shell=False)
    if run.returncode:
        raise ValueError(run.stderr.strip()[:300] or 'Memo transport failed')
    return json.loads(run.stdout)


def binding(pack):
    return {'project': pack['project'], 'handoff_id': pack['id'], 'handoff_hash': pack['content_hash']}


def check_capabilities(transport, method):
    caps = call(transport, 'memo.capabilities', {})
    if method not in caps.get('tools', []) and method not in caps.get('methods', {}):
        raise ValueError('Memo host does not support ' + method + '; keep local artifacts, do not claim a return')


def bind_local(pack, transport, analysis, citations, request_id):
    """Map verified standalone RawMD spans to real Memo IDs without re-extracting."""
    check_capabilities(transport, 'memo.skill_context')
    params = binding(pack); refs = []; cursor = 0
    while True:
        page = call(transport, 'memo.skill_context', dict(params, cursor=cursor, limit=24))
        refs.extend(page['evidence'])
        cursor = page['next_cursor']
        if cursor is None:
            break
    local = read(citations)
    if local['question'].strip() != pack['question'].strip():
        raise ValueError('Question changed: reason about the current question before binding a saved answer')
    answer = Path(analysis).read_text(encoding='utf-8-sig')
    claims = []; used = set()
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
                first, last = max(start, ref['line_start']), min(end, ref['line_end'])
                quote = '\n'.join(ref['text'].splitlines()[first-ref['line_start']:last-ref['line_start']+1])
                if quote.strip():
                    mapped.append({'evidence_id': ref['id'], 'content_hash': ref['content_hash'], 'quote': quote})
                    used.add(ref['artifact_id'])
                covered.update(range(first, last + 1))
            if not set(range(start, end + 1)) <= covered or not matches:
                raise ValueError('Source/span has no exact Memo mapping; attach its source.json in Memo and create a fresh handoff')
        claims.append({'text': claim['text'], 'kind': claim['kind'], 'supports': mapped})
    coverage = [{'artifact_id': s['id'], 'status': 'partial' if s['id'] in used else 'unread',
                 'gaps': 'Only mapped claims recorded; whole-source reading not assessed.' if s['id'] in used
                         else 'Not covered by this standalone answer.'} for s in page['sources']]
    return dict(params, schema='memo-research-answer/1', request_id=request_id,
                answer=answer, claims=claims, coverage=coverage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transport', type=Path, help='Explicit configured transport; defaults beside handoff.json')
    sub = parser.add_subparsers(dest='action', required=True)
    context = sub.add_parser('context'); context.add_argument('handoff', type=Path)
    context.add_argument('--artifact'); context.add_argument('--cursor', type=int, default=0)
    context.add_argument('--limit', type=int, default=12)
    bind = sub.add_parser('bind'); bind.add_argument('handoff', type=Path)
    bind.add_argument('--analysis', type=Path, required=True); bind.add_argument('--citations', type=Path, required=True)
    bind.add_argument('--request-id', required=True); bind.add_argument('--out', type=Path, required=True)
    submit = sub.add_parser('return'); submit.add_argument('handoff', type=Path)
    submit.add_argument('--result', type=Path, required=True)
    args = parser.parse_args(); pack = read(args.handoff)
    transport = read(args.transport or args.handoff.parent / 'transport.json')
    if args.action == 'context':
        check_capabilities(transport, 'memo.skill_context')
        params = dict(binding(pack), cursor=args.cursor, limit=args.limit)
        if args.artifact:
            params['artifact_id'] = args.artifact
        result = call(transport, 'memo.skill_context', params)
    elif args.action == 'bind':
        result = bind_local(pack, transport, args.analysis, args.citations, args.request_id)
        with args.out.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        result = {'status': 'BOUND_LOCAL', 'path': str(args.out), 'returned': False}
    else:
        check_capabilities(transport, 'memo.skill_answer')
        result = read(args.result)
        if result.pop('schema', None) != 'memo-research-answer/1':
            raise ValueError('Unsupported answer file')
        if any(result.get(k) != v for k, v in binding(pack).items()):
            raise ValueError('Answer belongs to another handoff')
        result = call(transport, 'memo.skill_answer', result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
