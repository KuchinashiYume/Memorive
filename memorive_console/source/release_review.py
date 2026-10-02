"""Small, allowlisted review report. Never serialize the native snapshot wholesale."""
from copy import deepcopy
import re

from common import OPERATIONS, build_suite_plan, now
from localization import translate

CONSOLE_VERSION = '1.02'
CHECKS = [
    ('notifications', '消息与桌宠', '消息到达、失败与取消时，核对通知、表情和恢复状态。'),
    ('documents', '资料库与报告', '从消息跳转资料库，核对日报、周报、月报的位置与内容。'),
    ('research', '研究问答与归档', '核对回答、引用、分支切换、归档与恢复；模拟回答不代表真实研究通过。'),
    ('preferences', '偏好与设置', '核对设置副本、保存反馈与页面一致性，不改动日常配置。'),
    ('discovery', '文献发现', '核对来源、重复项、分页、查询保存及失败后的恢复。'),
    ('ai_updates', '首页与 AI 动态', '核对侧栏、排行榜来源、加载状态和重试。'),
    ('languages', '多语言', '切换中／英／日，核对正文、控件、换行与缺失翻译。'),
    ('cli', '自定义 CLI', '另行授权后核对参数、取消、失败与恢复；安全测试不会启动真实模型。'),
    ('updates', '程序更新', '使用独立安装测试核对签名、重试和更新恢复；控制台不会安装更新。'),
]
STATUSES = {'NOT_RUN': '未检查', 'PASS': '已观察通过', 'FAIL': '发现问题', 'BLOCKED': '条件不足'}


def code(value, allowed, default='UNKNOWN'):
    return value if isinstance(value, str) and value in allowed else default


def token(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', value) else None


def error_code(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,100}', value) else None


def review_document(snapshot, checks):
    session = snapshot.get('session') or {}
    build = session.get('build') or {}
    version = build.get('release_version')
    sha = session.get('build_sha256')
    reference = session.get('mode') == 'reference'
    supported = set(snapshot.get('supported_operations') or []) & OPERATIONS.keys()
    suite_plan = build_suite_plan('full_safe')
    ready = snapshot.get('state') in {'CONNECTED', 'REFERENCE_ONLY'} and snapshot.get('business_operations_enabled') is True
    automated = []
    for row in snapshot.get('suites') or []:
        if row.get('session_id') != session.get('session_id'):
            continue
        cases = []
        for case in row.get('cases', []):
            cases.append({'case_id': token(case.get('case_id')), 'operation': code(case.get('operation'), OPERATIONS),
                'state': code(case.get('state'), {'PASSED', 'FAILED', 'SKIPPED', 'CANCELLED'}),
                'error_code': error_code(case.get('error_code'))})
        automated.append({'suite_id': token(row.get('suite_id')), 'preset_id': token(row.get('preset_id')),
            'state': code(row.get('state'), {'QUEUED', 'RUNNING', 'COMPLETED', 'CANCELLED', 'CANCEL_REQUESTED'}),
            'verdict': code(row.get('verdict'), {'PASS', 'FAIL', 'NOT_ASSESSED', 'PARTIAL'}),
            'evidence_scope': 'REFERENCE_PROTOCOL' if reference else 'APPLICATION_BRIDGE', 'cases': cases})
    manual = [{'id': key, 'title': title, 'instruction': instruction,
        'status': code(checks.get(key), STATUSES, 'NOT_RUN'), 'evidence_scope': 'USER_OBSERVATION'}
        for key, title, instruction in CHECKS]
    return {
        'schema_version': 'Memorive-ConsoleReleaseReview-v1', 'console_version': CONSOLE_VERSION,
        'generated_at': now(), 'session_id': token(session.get('session_id')),
        'release_version': version if isinstance(version, str) and re.fullmatch(r'\d+\.\d{2}', version) else None,
        'executable_sha256': sha if isinstance(sha, str) and re.fullmatch(r'[a-f0-9]{64}', sha) else None,
        'evidence_scope': 'REFERENCE_PROTOCOL' if reference else 'APPLICATION_BRIDGE' if session else 'NO_SESSION',
        'release_verdict': 'NOT_ASSESSED', 'manual_editable': bool(session) and not reference and ready,
        'automatic_execution': False, 'business_content_recorded': False,
        'supported_operations': sorted(supported),
        'safe_suite': {'total': len(suite_plan), 'declared': sum(c['operation'] in supported for c in suite_plan),
            'runnable': sum(c['operation'] in supported for c in suite_plan) if ready else 0,
            'model_calls_allowed': False},
        'automated_suites': automated, 'manual_checks': manual,
        'commands': [{'command_id': token(row.get('command_id')), 'operation': code(row.get('operation'), OPERATIONS),
            'state': code(row.get('state'), {'QUEUED', 'ACCEPTED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED'}),
            'error_code': error_code(row.get('error_code')), 'model_calls_allowed': row.get('allow_model_calls') is True}
            for row in snapshot.get('commands', [])],
        'cleanup_status': 'PENDING_SESSION_CLOSE' if session else 'NO_SESSION',
        'limits': ['接口回执不证明界面、通知或真实模型结果正确。',
            '人工检查由用户记录，控制台不自动判定产品发布通过。',
            '导出仅包含版本、摘要和固定检查项；不含正文、设置、路径或令牌。'],
    }


def closed_review(archive, cleanup, state):
    report = deepcopy(archive)
    report['generated_at'] = now()
    report['manual_editable'] = False
    report['safe_suite']['runnable'] = 0
    report['cleanup_status'] = ('CLEANUP_FAILED' if state == 'CLEANUP_FAILED' else
        code((cleanup or {}).get('status'), {'CLEANED', 'DELETED', 'PURGED', 'PASS'}, 'NOT_CONFIRMED'))
    return report


def markdown(report, language='zh-CN'):
    labels = {'zh-CN': ['Memorive 测试审核', '接口测试', '尚未运行', '指令摘要', '人工检查', '检查项', '观察结果', '边界'],
        'en-US': ['Memorive test review', 'Bridge tests', 'Not run yet', 'Command summary', 'Manual checks', 'Check', 'Observation', 'Limits'],
        'ja-JP': ['Memorive テストレビュー', 'インターフェーステスト', '未実行', '指令概要', '手動確認', '確認項目', '観察結果', '範囲']}[language]
    lines = ['# ' + labels[0], '', f"- Console: {CONSOLE_VERSION}",
        f"- Version: {report['release_version'] or '—'}", f"- EXE SHA256: {report['executable_sha256'] or '—'}",
        f"- Evidence: {report['evidence_scope']}", '- Release verdict: NOT_ASSESSED',
        f"- Cleanup: {report['cleanup_status']}", '', '## ' + labels[1], '']
    for suite in report['automated_suites']:
        lines.append(f"- {suite['preset_id']}: {suite['state']} / {suite['verdict']} ({suite['evidence_scope']})")
        for case in suite['cases']:
            if case['state'] != 'PASSED':
                lines.append(f"  - {case['case_id']}: {case['state']} / {case['error_code'] or '—'}")
    if not report['automated_suites']:
        lines.append('- ' + labels[2])
    lines += ['', '## ' + labels[3], '', '| Operation | State | Error | Model permission |', '|---|---|---|---|']
    lines += [f"| {r['operation']} | {r['state']} | {r['error_code'] or '—'} | {r['model_calls_allowed']} |" for r in report['commands']]
    lines += ['', '## ' + labels[4], '', f'| {labels[5]} | {labels[6]} |', '|---|---|']
    lines += [f"| {r['title']} | {translate(STATUSES[r['status']], language)} |" for r in report['manual_checks']]
    lines += ['', '## ' + labels[7], ''] + ['- ' + value for value in report['limits']]
    return '\n'.join(lines) + '\n'
