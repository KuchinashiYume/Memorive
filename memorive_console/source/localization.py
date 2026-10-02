import json
import locale
from pathlib import Path

LANGUAGES = ('zh-CN', 'en-US', 'ja-JP')
RESOURCES = json.loads((Path(__file__).parent / 'locales.json').read_text(encoding='utf-8'))


def default_language():
    try:
        language = (locale.getlocale()[0] or '').lower()
    except (ValueError, TypeError):
        language = ''
    return 'zh-CN' if language.startswith(('zh', 'chinese')) else 'ja-JP' if language.startswith(('ja', 'japanese')) else 'en-US'


def translate(text, language='zh-CN'):
    return RESOURCES.get(language, {}).get(text, text)
