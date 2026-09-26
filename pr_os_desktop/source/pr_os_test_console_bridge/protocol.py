"""Bridge v1.0 command contract; copied from the public console specification."""
from pathlib import Path
import re,json
def encode(value):
 return json.dumps(value,ensure_ascii=False,allow_nan=False).encode("utf8")

OPERATIONS = {
    'message.test': {'title': '消息测试', 'required': ['kind', 'title', 'body'], 'optional': [], 'model': False},
    'event.inject': {'title': '模拟事件', 'required': ['event', 'note'], 'optional': [], 'model': False},
    'report.run': {'title': '日报 / 周报 / 月报', 'required': ['period'], 'optional': [], 'model': False},
    'radar.recommend': {'title': '外部雷达推荐文章', 'required': ['topic', 'count'], 'optional': [], 'model': True},
    'inbox.import': {'title': '导入测试文件', 'required': ['paths'], 'optional': [], 'model': False},
    'inbox.dispatch': {'title': '手动执行收件项', 'required': ['item_id'], 'optional': [], 'model': True},
    'task.pause': {'title': '暂停任务', 'required': ['job_id'], 'optional': [], 'model': False},
    'task.resume': {'title': '继续任务', 'required': ['job_id'], 'optional': [], 'model': True},
    'task.cancel': {'title': '取消任务', 'required': ['job_id'], 'optional': [], 'model': False},
    'task.retry': {'title': '重试任务', 'required': ['job_id'], 'optional': [], 'model': True},
    'message.confirm': {'title': '确认消息', 'required': ['message_id'], 'optional': [], 'model': False},
    'message.star': {'title': '消息星标', 'required': ['message_id'], 'optional': [], 'model': False},
    'diagnostics': {'title': '产品诊断', 'required': [], 'optional': [], 'model': False},
    'expression.trigger': {'title': '表情自测', 'required': ['event_id','duration_ms'], 'optional': [], 'model': False},
    'expression.cancel': {'title': '停止表情自测', 'required': [], 'optional': [], 'model': False},
}
MESSAGE_KINDS = ['info', 'success', 'warning', 'error', 'daily', 'weekly', 'monthly', 'report_failed', 'radar', 'imported', 'review', 'cancelled', 'stalled', 'target_missing']
EVENT_KINDS = ['import', 'complete', 'fail', 'review', 'cancel', 'stall']
TEST_PACK_ID = 'PR-OS-ConsoleBuiltInTestPack-v1'


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', value):
        raise ValueError('IDENTIFIER_INVALID')
    return value


def validate_command(value):
    if not isinstance(value, dict) or set(value) != {'request_id', 'operation', 'params', 'allow_model_calls'}:
        raise ValueError('COMMAND_FIELDS_INVALID')
    identifier(value['request_id'])
    if type(value['allow_model_calls']) is not bool:
        raise ValueError('MODEL_PERMISSION_BOOLEAN_REQUIRED')
    operation = value['operation']
    if operation not in OPERATIONS:
        raise ValueError('OPERATION_UNKNOWN')
    specification = OPERATIONS[operation]
    params = value['params']
    if not isinstance(params, dict) or set(params) != set(specification['required']):
        raise ValueError('PARAMETER_FIELDS_INVALID')
    if specification['model'] and value['allow_model_calls'] is not True:
        raise ValueError('MANUAL_MODEL_PERMISSION_REQUIRED')
    if len(encode(params)) > 128 * 1024:
        raise ValueError('PARAMETERS_TOO_LARGE')
    if re.search(r'(?<![A-Za-z0-9_])(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|Bearer\s+\S+)', encode(params).decode()):
        raise ValueError('CREDENTIAL_VALUE_NOT_ALLOWED')
    if operation.startswith('expression.'):
        from .expressions import BY_ID
        if value['allow_model_calls'] is not False:
            raise ValueError('EXPRESSION_MODEL_PERMISSION_FORBIDDEN')
        if operation=='expression.trigger':
            if not isinstance(params['event_id'],str) or params['event_id'] not in BY_ID:
                raise ValueError('EXPRESSION_EVENT_UNKNOWN')
            if type(params['duration_ms']) is not int or not 500 <= params['duration_ms'] <= 5000:
                raise ValueError('EXPRESSION_DURATION_INVALID')
        return value
    for key, item in params.items():
        if key in {'job_id', 'item_id', 'message_id'}:
            identifier(item)
        elif key == 'paths':
            if not isinstance(item, list) or not 1 <= len(item) <= 32 or any(not isinstance(p, str) or not Path(p).is_absolute() for p in item):
                raise ValueError('ABSOLUTE_IMPORT_PATHS_REQUIRED')
        elif key == 'count':
            if type(item) is not int or not 1 <= item <= 20:
                raise ValueError('COUNT_INVALID')
        elif not isinstance(item, str) or len(item) > 3500:
            raise ValueError('TEXT_PARAMETER_INVALID')
    if operation == 'message.test' and (params['kind'] not in MESSAGE_KINDS or not params['title'].strip() or not params['body'].strip()):
        raise ValueError('MESSAGE_INVALID')
    if operation == 'message.test' and len(params['title']) > 180:
        raise ValueError('MESSAGE_TITLE_TOO_LONG')
    if operation == 'radar.recommend' and not params['topic'].strip():
        raise ValueError('RADAR_TOPIC_REQUIRED')
    if operation == 'event.inject' and params['event'] not in EVENT_KINDS:
        raise ValueError('EVENT_INVALID')
    if operation == 'report.run' and params['period'] not in {'daily', 'weekly', 'monthly'}:
        raise ValueError('REPORT_PERIOD_INVALID')
    return value


