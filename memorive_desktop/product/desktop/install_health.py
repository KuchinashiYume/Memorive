"""Owned, offline pre-activation check. Creates no user data or demo records."""
from pathlib import Path
import hashlib
import json
import os
import sys

LISTS = {'current_task.list': 'tasks', 'messages.list': 'rows',
         'sessions.list': 'rows', 'library.list_artifacts': 'rows',
         'work_log.list_entries': 'rows'}

def empty_projection(api):
    counts = {method: len(api.call(method, {})[field]) for method, field in LISTS.items()}
    settings = api.call('settings.get_state', {})['settings']
    counts.update(model_services=len(settings['model_services']),
                  credential_references=len(settings['credential_references']))
    user = api.call('settings.user_get', {})['config']
    metrics = api.call('settings.effect_metrics', {})
    return {'counts': counts, 'empty': not any(counts.values()),
            'username_empty': user['username'] == '', 'avatar_empty': user['avatar_png'] == '',
            'model_calls': metrics['external_model_calls'],
            'credential_reads': metrics['credential_value_reads']}

def main(root):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--memo-install-health', type=Path, required=True)
    probe = parser.parse_args().memo_install_health.resolve(strict=True)
    marker = json.loads((probe/'health-probe-owner.json').read_text(encoding='utf8'))
    version = Path(sys.executable).resolve().parents[1]
    program = version.parents[1]
    owner = 'Memorive-INSTALLER-TRIAL-V1'
    if (marker.get('owner') != owner or marker.get('role') != 'INSTALLER_HEALTH_PROBE'
        or Path(marker['probe_root']).resolve() != probe
        or Path(marker['source_version']).resolve() != version
        or Path(marker['program_root']).resolve() != program
        or probe.parent != program/'health'
        or json.loads((program/'install-owner.json').read_text())['owner'] != owner):
        raise RuntimeError('INSTALL_HEALTH_OWNERSHIP_INVALID')
    for p in [probe, version, program, *probe.parents]:
        if getattr(p.stat(), 'st_file_attributes', 0) & 0x400:
            raise RuntimeError('INSTALL_HEALTH_REPARSE_FORBIDDEN')
    manifest_bytes = (version/'manifest.json').read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest().upper() != marker['manifest_sha256'].upper():
        raise RuntimeError('INSTALL_HEALTH_MANIFEST_MISMATCH')
    manifest = json.loads(manifest_bytes)
    expected = next(x['sha256'] for x in manifest['members'] if x['path']=='app/Memorive.exe')
    if hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest().upper()!=expected.upper():
        raise RuntimeError('INSTALL_HEALTH_EXE_MISMATCH')
    if Path(os.environ['LOCALAPPDATA']).resolve() != probe/'localappdata':
        raise RuntimeError('INSTALL_HEALTH_ENVIRONMENT_INVALID')
    state = probe/'localappdata/Memorive/desktop-review/state'
    if state.exists():
        raise RuntimeError('INSTALL_HEALTH_REQUIRES_FRESH_STATE')
    from runtime import ProductApi
    api = None
    result = {'status':'FAIL', 'kind':'EMPTY_OFFLINE_RUNTIME', 'release_version':'1.01'}
    try:
        # Generated here from generic text; never a distributed PDF or user's document.
        import pymupdf
        doc = pymupdf.open(); page=doc.new_page(); page.insert_text((36,36),'Memorive offline health')
        pdf = probe/'synthetic-health.pdf'; doc.save(str(pdf)); doc.close()
        with pymupdf.open(str(pdf)) as check:
            result['pdf_runtime_ok'] = 'Memorive offline health' in check[0].get_text()
        roots={k:probe/'session-sources'/k for k in ('codex_home','claude_home')}
        for p in roots.values():p.mkdir(parents=True)
        api=ProductApi(state,root/'source',root/'source/facade_method_binding.json',
                       system_effects_enabled=False,session_source_roots=roots,
                       session_visible_ui_reader={},test_fixture_mode=False)
        result.update(empty_projection(api))
        result['status']='PASS' if (result['pdf_runtime_ok'] and result['empty'] and
            result['username_empty'] and result['avatar_empty'] and
            result['model_calls']==result['credential_reads']==0) else 'FAIL'
    finally:
        if api is not None:api.close()
        out=probe/'localappdata/Memorive/desktop-review/evidence'
        out.mkdir(parents=True,exist_ok=True)
        (out/'v101_health.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    return 0 if result['status']=='PASS' else 1
