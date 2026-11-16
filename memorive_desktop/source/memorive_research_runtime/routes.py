"""Requests to other areas that research answers itself.

ProductApi.call hands these here before its own routing, because only this package knows research
run ids and draws a research run on the current-task page:
- navigate: messages.navigate to a research run (memorive://job/research-...) or to its result
  (memorive://artifact/research-.../result);
- current_task: the current-task page lists research runs next to document jobs and shows a
  selected research run itself;
- literature_notification: whether a message about a result is a literature discovery, which
  gets its own Windows notification kind.
"""
from .task_projection import bindings, view

RUN_LOCATOR = 'memorive://job/research-'
RESULT_LOCATOR = 'memorive://artifact/research-'


def navigate(runtime, params):
    """The route for a message that points at a research run or result; None when it points elsewhere."""
    text = str(params.get('locator', ''))
    if text.startswith(RUN_LOCATOR):
        locator = params['locator']
        run = locator.split('/')[-1]
        if params.get('message_id') and runtime.host.messages.store.get(params['message_id']).target_locator != locator:
            raise ValueError('RESEARCH_MESSAGE_TARGET_MISMATCH')
        runtime.call('research.get', {'run_id': run})
        return {'status': 'PASS', 'research_task_id': run, 'route': 'current-task', 'job_id': run}
    if text.startswith(RESULT_LOCATOR):
        from memorive_desktop_service.locator import StableLocator
        locator = StableLocator.parse(params['locator'])
        if locator.object_id != 'result':
            raise ValueError('RESEARCH_ARTIFACT_INVALID')
        run_id = locator.job_id
        if params.get('message_id') and runtime.host.messages.store.get(params['message_id']).target_locator != params['locator']:
            raise ValueError('RESEARCH_MESSAGE_TARGET_MISMATCH')
        result = runtime.call('research.get', {'run_id': run_id})
        if result['status'] not in {'SUCCEEDED', 'PARTIAL'}:
            raise ValueError('RESEARCH_RESULT_UNAVAILABLE')
        row = runtime.host.library.get_artifact(run_id + '/result')
        return {'status': 'PASS', 'research_run_id': run_id, 'route': 'library', 'view': row['mode'],
                'artifact_id': row['artifact_id'], 'result_sha256': row['result_sha256'], 'target_locator': row['stable_locator']}
    return None


def current_task(runtime, method, params, current_task_call):
    """A current_task.* request while research runs are listed: the answer, or None when the
    current-task area answers it alone (no research run selected, or a method research leaves alone).

    current_task_call is the current-task area's own call; list and refresh go there and get the
    research runs added as research_bindings.
    """
    if method == 'current_task.select_task':
        runtime.selected_run = params.get('run') if str(params.get('run', '')).startswith('research-') else None
    selected = runtime.selected_run
    if method in {'current_task.list', 'current_task.refresh'}:
        result = current_task_call(method, params)
        if isinstance(result, dict):
            result['research_bindings'] = bindings(runtime)
        return result
    if selected and method in {'current_task.select_task', 'current_task.select_node', 'current_task.view'}:
        return view(runtime, selected)
    if selected and method == 'current_task.close_all':
        runtime.selected_run = None
        return {'status': 'PASS'}
    if selected and method not in {'current_task.get_contract', 'current_task.effect_metrics'}:
        raise ValueError('文献发现任务请使用流程图中的取消、重新检索或查看结果按钮')
    return None


def literature_notification(runtime, locator):
    """True when a message points at the result of a literature discovery run."""
    if not str(locator or '').startswith(RESULT_LOCATOR):
        return False
    run = str(locator).split('/')[3]
    return runtime.call('research.get', {'run_id': run})['kind'] == 'discovery'
