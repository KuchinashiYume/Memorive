"""A research question with no material to answer from.

AnswerJob (answering.py) comes here when a model is chosen and the search kept no excerpt: build_request
takes RULES and SCHEMA instead of the chat rules, and ask_model turns the reply into the saved answer with
answer(). The answer follows the memo-research Skill's rule for missing material: it says that nothing was
found and what kind of material would answer the question, and gives no value from the model's memory.
It may end with a background paragraph marked [AI], from the model's general knowledge: qualitative only
(what the concepts mean, which methods or kinds of papers address the question, search terms). That
paragraph is shown apart from the answer (the page folds it), is never cited, and does not become
knowledge or a research note: the reply has no knowledge draft and no notes.

Every sentence that holds a number is left out of both parts, so a value recalled from memory cannot
reach the answer; digits inside names (ResNet-34, VGG16) do not count.
"""
import re

from memorive.platform.language.text import choose

REVISION = 'ResearchNoMaterialAnswer-v1'

SCHEMA = {
    'type': 'object',
    'properties': {
        'missing': {'type': 'string', 'description': (
            'One to three sentences: what kind of material would answer the question (which paper, table, '
            'dataset or measurement). No value from memory.')},
        'background': {'type': 'string', 'description': (
            'Optional general background, qualitative only: what the concepts mean, which methods or kinds of '
            'papers address this, search terms. No numbers, percentages, sample sizes, dates, quotations, or '
            'statements of what a particular paper reports. Empty when there is nothing reliable to say.')},
    },
    'required': ['missing', 'background'],
    'additionalProperties': False,
}

RULES = """No excerpt of the user's material was found for this question: the evidence list is empty.
Do not answer the question from memory, not even as background.
In "missing", say in one to three sentences what kind of material would answer it: which paper, table,
dataset or measurement to add. Give no value.
In "background", you may give general background that helps the user look for material: what the concepts
mean, which methods or kinds of papers address the question, useful search terms. Qualitative only: no
numbers, percentages, sample sizes, dates, quotations, and no statement of what a particular paper reports.
Leave it empty if you have nothing reliable to say.
Write both in the output language of the request."""

NAME_DIGITS = re.compile(r'(?<=[A-Za-z])-?\d+')
SENTENCE_END = re.compile(r'(?<=[。！？；])|(?<=[.!?;])\s+|\n+')


def without_values(text):
    """text without the sentences that hold a number, and how many were left out."""
    kept, left_out = [], 0
    for sentence in SENTENCE_END.split(text or ''):
        sentence = sentence.strip()
        if not sentence:
            continue
        if re.search(r'\d', NAME_DIGITS.sub('', sentence)):
            left_out += 1
        else:
            kept.append(sentence)
    joined = ''
    for sentence in kept:
        if joined and not re.search(r'[。！？；]$', joined):
            joined += ' '
        joined += sentence
    return joined, left_out


def answer(value, thread, language):
    """The saved text and the record the page shows it from (the message's no_material field)."""
    if (not isinstance(value, dict) or set(value) != {'missing', 'background'}
            or not all(isinstance(value[k], str) for k in ('missing', 'background'))):
        raise ValueError('CHAT_RESPONSE_INVALID')
    missing, missing_left_out = without_values(value['missing'])
    background, background_left_out = without_values(value['background'])
    searched_project = bool(thread.get('include_project', thread.get('scope', 'project') == 'project'))
    head = choose(language,
                  '没有找到能回答这个问题的资料。' + ('本对话和整个项目里都没有相关的资料。' if searched_project
                                                    else '这个对话只检索本对话的资料。'),
                  'No material was found that answers this question. ' + (
                      'Neither this conversation nor the project holds relevant material.' if searched_project
                      else 'This conversation searches only its own material.'),
                  'この質問に答えられる資料が見つかりませんでした。' + (
                      'この会話にもプロジェクト全体にも関連する資料はありません。' if searched_project
                      else 'この会話は会話内の資料だけを検索しています。'))
    actions = choose(language,
                     '可以添加相关论文，或在文献发现里检索' + ('。' if searched_project else '，或打开“检索整个项目”。'),
                     'Add the relevant papers, or search for them in literature discovery' + (
                         '.' if searched_project else ', or turn on searching the whole project.'),
                     '関連する論文を追加するか、文献探索で検索してください' + (
                         '。' if searched_project else '。またはプロジェクト全体の検索をオンにしてください。'))
    main = '\n\n'.join(part for part in (head, missing, actions) if part)
    label = choose(language, '[AI] 以下是 AI 根据自身知识给出的背景，未经资料核实，不能作为引用。',
                   "[AI] Background from the AI's own knowledge, not checked against your material; not a citation.",
                   '[AI] AI 自身の知識による背景です。資料では確認しておらず、引用として使えません。')
    text = main + ('\n\n' + label + '\n' + background if background else '')
    record = {'revision': REVISION, 'main_text': main, 'ai_background': background, 'ai_label': label,
              'searched_project': searched_project,
              'sentences_with_values_left_out': missing_left_out + background_left_out}
    return text, record
