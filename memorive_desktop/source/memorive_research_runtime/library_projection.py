"""Hash-bound local Library projections; no canonical registry promotion."""
from .common import read, sealed, write, sha

def current_row(row):
    row=dict(row)
    if row.get('kind') in {'文献发现','外部文献'}:
        row['kind']='外部文献';row['mode']='memorive'
        from memorive_language.text import choose
        prefix=choose(row.get('language_context'),'外部文献','External literature','外部文献')+'·'
        if not row['display_name'].startswith(prefix):
            row['display_name']=prefix+row['display_name']
    return row


def publish(root, run_id, kind, title, result, created_at):
    artifact_id = run_id + '/result'
    labels = {'daily': '日报', 'weekly': '周报', 'monthly': '月报'}
    row = {
        'artifact_id': artifact_id, 'stable_locator': 'memorive://artifact/' + artifact_id,
        'view_snapshot_id': 'research-library-' + result['sha256'],
        'display_name': title, 'mode': 'memorive' if kind in {'report','ai_briefing'} else 'literature',
        'kind': 'AI 近况' if kind=='ai_briefing' else labels.get(result.get('period'), '文献发现'),
        'status': '已完成' if result.get('status') == 'SUCCEEDED' else '部分完成',
        'privacy_class': 'PRIVATE', 'rights_status': 'ALLOWED',
        'lineage': ['run:' + run_id, 'sha256:' + result['sha256']],
        'relative_path': 'research/runs/' + run_id + '/result.md',
        'file_exists': True, 'file_state': '已保存', 'products': [],
        'external_target': '', 'task': '', 'updated_at': created_at,
        'size_bytes': len(result['markdown'].encode('utf8')),
        'research_run_id': run_id, 'result_sha256': result['sha256'],
        'product_kind': 'AI_BRIEFING' if kind=='ai_briefing' else 'RESEARCH_RESULT', 'report_period': result.get('period'),
        'canonical_registry_row_included': False,
    }
    if result.get('language_context') is not None:row['language_context']=result['language_context']
    row=current_row(row)
    path = root / 'runs' / run_id / 'library.json'
    if path.exists():
        old = read(path)
        if old['result_sha256']!=result['sha256'] or current_row(old['row']) != row:
            raise ValueError('RESEARCH_LIBRARY_BINDING_CONFLICT')
    else:
        write(path, sealed({'row': row, 'result_sha256': result['sha256']}))
    return row


def rows(root):
    from .report_identity import project
    reports={item['run_id']:item for item in project(root) if item.get('kind')=='report'}
    result = []
    for path in sorted((root / 'runs').glob('*/library.json')):
        binding = read(path)
        body = read(path.parent / 'result.json')
        row = binding['row']
        if binding['result_sha256'] != body['sha256'] or row['result_sha256'] != body['sha256']:
            raise ValueError('RESEARCH_LIBRARY_RESULT_HASH_MISMATCH')
        projected=current_row(row)
        report=reports.get(row.get('research_run_id'))
        if report and report.get('report_window'):
            projected.update(display_name=report['title'],report_window=report['report_window'],report_revision=report['report_revision'])
        readable=path.parent/'result.md'
        if sealed(body)['sha256']!=body['sha256'] or not readable.is_file() or readable.read_text(encoding='utf8')!=body['markdown']:
            projected.update(file_exists=False,file_state='文件缺失或校验失败',status='需检查')
        result.append(projected)
    return result
