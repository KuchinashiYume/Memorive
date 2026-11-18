"""Saved versions of an answer: every successful save of an answer keeps a version that can be read,
compared and restored.

An answer is an assistant message of a conversation; its text and citations are what a person reads,
edits and cites. Each save writes one 'answer_save' record holding the text and the citations exactly
as saved, the version it follows (parent_version_id), when and how it was saved (source):

  GENERATED       the answer as the model or an Agent first wrote it (recorded on the first save)
  EDIT, AUTOSAVE  the person's edit, saved by hand or by the editor (memo.answer_save)
  AGENT           an Agent's reply in the same conversation taken over as this answer (memo.answer_adopt)
  CLAIM_REVISION  one claim replaced (answer_evidence.py, memo.answer_revise)
  RESTORE         the content of an earlier version saved again (memo.answer_restore)

Restoring never moves back in history: it saves the old content as a new version and every version
stays readable. A save whose text and citations equal the current version's makes no version.
Answering a question again (answer_versions.py) is a different thing: it adds another answer, with a
history of its own. Answers revised before this module existed keep their earlier texts in
'answer_history' records (answer_evidence.py); the first save turns those into versions, marked
seeded_from 'answer_history'.

Window methods (DESKTOP only): memo.answer_history, memo.answer_version_get, memo.answer_save,
memo.answer_adopt, memo.answer_restore, memo.answer_diff. Nothing here calls a model.
"""
import copy
import difflib
import re
from memorive.research.workspace.store import digest, now

METHODS = frozenset({'memo.answer_history', 'memo.answer_version_get', 'memo.answer_save', 'memo.answer_adopt',
                     'memo.answer_restore', 'memo.answer_diff'})
HISTORY = 'MemoAnswerHistory-v1'
SOURCES = ('GENERATED', 'EDIT', 'AUTOSAVE', 'AGENT', 'CLAIM_REVISION', 'RESTORE')
MAX_TEXT = 200000
# Word-level comparison: a run of spaces, one CJK character, a word, or one other character.
TOKEN = re.compile(r'\s+|[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]|[^\W_\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]+|.', re.S)


def _content_hash(text, citations):
    return digest({'text': text, 'citations': citations})


def _meta(record):
    keys = ('version_id', 'number', 'parent_version_id', 'saved_at', 'source', 'restored_from', 'adopted_from',
            'seeded_from', 'content_sha256', 'answer_version')
    return dict({k: record.get(k) for k in keys}, characters=len(record['text']), citation_count=len(record['citations']))


class AnswerSaves:
    def __init__(self, workspace):
        self.ws = workspace
        self.store = workspace.store

    # Reading -----------------------------------------------------------------------------------

    def _answer(self, thread_id, answer_id, db=None):
        thread = self.store.get('thread', thread_id, db=db)
        if not thread:
            raise ValueError('THREAD_NOT_FOUND')
        message = next((m for m in thread['messages'] if m['id'] == answer_id and m['role'] == 'assistant'), None)
        if message is None:
            raise ValueError('ANSWER_NOT_FOUND')
        return thread, message

    def _stored(self, thread, answer_id, db=None):
        rows = [r for r in self.store.list('answer_save', thread['project'], db=db)
                if r.get('thread_id') == thread['id'] and r.get('answer_id') == answer_id]
        return sorted(rows, key=lambda r: r['number'])

    def _seed(self, thread, message, db=None):
        """The versions of an answer that was never saved here: its texts kept by claim revisions
        ('answer_history', oldest first) and the current text. Not written; see _ensure."""
        legacy = sorted((r for r in self.store.list('answer_history', thread['project'], db=db)
                         if r.get('thread_id') == thread['id'] and r.get('answer_id') == message['id']),
                        key=lambda r: r['message'].get('answer_version', 1))
        states = [(r['message'], 'answer_history') for r in legacy] + [(message, None)]
        versions, parent = [], None
        for index, (state, seeded_from) in enumerate(states):
            number = index + 1
            # Version k+1 came into being when version k was archived, so its time is that record's.
            saved_at = state.get('created_at') if index == 0 else legacy[index - 1].get('saved_at')
            record = self._record(thread, message['id'], number, parent, 'GENERATED' if index == 0 else 'CLAIM_REVISION',
                                  state['text'], state.get('citations') or [], state.get('answer_version', 1),
                                  saved_at=saved_at, seeded_from=seeded_from)
            versions.append(record)
            parent = record['version_id']
        return versions

    @staticmethod
    def _record(thread, answer_id, number, parent, source, text, citations, answer_version, *, saved_at=None,
                restored_from=None, adopted_from=None, seeded_from=None):
        return {'thread_id': thread['id'], 'answer_id': answer_id, 'version_id': f"{thread['id']}-{answer_id}-s{number}",
                'number': number, 'parent_version_id': parent, 'saved_at': saved_at or now(), 'source': source,
                'restored_from': restored_from, 'adopted_from': adopted_from, 'seeded_from': seeded_from,
                'text': text, 'citations': copy.deepcopy(citations), 'content_sha256': _content_hash(text, citations),
                'answer_version': answer_version}

    def _versions(self, thread, message, db=None):
        return self._stored(thread, message['id'], db) or self._seed(thread, message, db)

    def history(self, thread_id, answer_id):
        thread, message = self._answer(thread_id, answer_id)
        versions = self._versions(thread, message)
        return {'schema_version': HISTORY, 'thread_id': thread_id, 'answer_id': answer_id,
                'current_version_id': versions[-1]['version_id'], 'versions': [_meta(v) for v in versions]}

    def version_get(self, thread_id, answer_id, version_id):
        thread, message = self._answer(thread_id, answer_id)
        record = self._find(self._versions(thread, message), version_id)
        return dict(_meta(record), text=record['text'], citations=record['citations'])

    @staticmethod
    def _find(versions, version_id):
        record = next((v for v in versions if v['version_id'] == version_id), None)
        if record is None:
            raise ValueError('ANSWER_VERSION_NOT_FOUND')
        return record

    # Saving ------------------------------------------------------------------------------------

    def ensure(self, thread, message, db):
        """Write the seeded versions of a never-saved answer; called before its content changes."""
        stored = self._stored(thread, message['id'], db)
        if stored:
            return stored
        seeded = self._seed(thread, message, db)
        for record in seeded:
            self.store.put('answer_save', record['version_id'], thread['project'], record, expected=0, db=db)
        return seeded

    def record(self, thread, message, source, db, *, restored_from=None, adopted_from=None):
        """One version for the content message holds now (after the change), unless it equals the
        latest version. Returns (version record, saved)."""
        stored = self._stored(thread, message['id'], db)
        latest = stored[-1]
        citations = message.get('citations') or []
        if latest['content_sha256'] == _content_hash(message['text'], citations):
            return latest, False
        record = self._record(thread, message['id'], latest['number'] + 1, latest['version_id'], source, message['text'],
                              citations, message.get('answer_version', 1), restored_from=restored_from,
                              adopted_from=adopted_from)
        self.store.put('answer_save', record['version_id'], thread['project'], record, expected=0, db=db)
        message['saved_version_id'] = record['version_id']
        return record, True

    def _change(self, thread_id, answer_id, expected_version_id, change, source, **links):
        """Apply change(thread, message, versions) to the answer and save it as a version, in one transaction."""
        with self.store.tx() as db:
            thread, message = self._answer(thread_id, answer_id, db)
            if thread.get('archived'):
                raise ValueError('THREAD_ARCHIVED')
            if any(j.get('thread_id') == thread_id and j['status'] in {'QUEUED', 'RUNNING'}
                   for j in self.store.list('job', thread['project'], db=db)):
                raise ValueError('THREAD_BUSY')
            versions = self.ensure(thread, message, db)
            if expected_version_id != versions[-1]['version_id']:
                # The current version goes with the error, so the window can offer to compare.
                raise ValueError('ANSWER_VERSION_CONFLICT:' + versions[-1]['version_id'])
            text, citations = change(thread, message, versions)
            if (not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT
                    or not isinstance(citations, list)):
                raise ValueError('ANSWER_TEXT_INVALID')
            if _content_hash(text, citations) == versions[-1]['content_sha256']:
                return {'saved': False, 'version': _meta(versions[-1]), 'history': self._history_of(thread, answer_id, db)}
            # Same consequences as a claim revision: reviews and drafts made for the old text no longer hold.
            message.update(text=text, citations=copy.deepcopy(citations), answer_version=message.get('answer_version', 1) + 1,
                           claim_reviews=[], review_binding=None, edited_at=now())
            message.pop('feedback_id', None)
            record, _ = self.record(thread, message, source, db, **links)
            self.store.put('thread', thread_id, thread['project'], thread, expected=thread['revision'], db=db)
            self.store.event('ANSWER_SAVED', answer_id, {'thread_id': thread_id, 'version_id': record['version_id'],
                                                         'source': source}, db)
            return {'saved': True, 'version': _meta(record), 'history': self._history_of(thread, answer_id, db)}

    def _history_of(self, thread, answer_id, db):
        versions = self._stored(thread, answer_id, db)
        return {'schema_version': HISTORY, 'thread_id': thread['id'], 'answer_id': answer_id,
                'current_version_id': versions[-1]['version_id'], 'versions': [_meta(v) for v in versions]}

    def save(self, thread_id, answer_id, text, expected_version_id, source='EDIT'):
        """A person's edit of the answer text; the citations stay as they are."""
        if source not in {'EDIT', 'AUTOSAVE'}:
            raise ValueError('ANSWER_SAVE_SOURCE_INVALID')
        return self._change(thread_id, answer_id, expected_version_id,
                            lambda thread, message, versions: (text, message.get('citations') or []), source)

    def adopt(self, thread_id, answer_id, from_message_id, expected_version_id):
        """Take over the text and citations of an Agent's reply in the same conversation."""
        def change(thread, message, versions):
            reply = next((m for m in thread['messages'] if m['id'] == from_message_id and m['role'] == 'assistant'), None)
            if reply is None or reply['id'] == answer_id or not reply.get('agent_result'):
                raise ValueError('AGENT_REPLY_NOT_FOUND')
            return reply['text'], reply.get('citations') or []
        return self._change(thread_id, answer_id, expected_version_id, change, 'AGENT', adopted_from=from_message_id)

    def restore(self, thread_id, answer_id, version_id, expected_version_id):
        """Save the content of an earlier version as the newest version."""
        def change(thread, message, versions):
            record = self._find(versions, version_id)
            return record['text'], record['citations']
        return self._change(thread_id, answer_id, expected_version_id, change, 'RESTORE', restored_from=version_id)

    # Comparing ---------------------------------------------------------------------------------

    def diff(self, thread_id, answer_id, from_version_id, to_version_id):
        """Line by line, and word by word inside changed lines (one CJK character counts as a word).
        Joining the equal and deleted words gives the old lines of a block exactly, the equal and
        inserted words the new ones, whitespace included."""
        thread, message = self._answer(thread_id, answer_id)
        versions = self._versions(thread, message)
        old, new = self._find(versions, from_version_id), self._find(versions, to_version_id)
        return {'schema_version': 'MemoAnswerDiff-v1', 'from_version_id': from_version_id, 'to_version_id': to_version_id,
                'blocks': line_diff(old['text'], new['text']), 'citations': citation_diff(old['citations'], new['citations'])}

    def call(self, method, params):
        handlers = {'memo.answer_history': self.history, 'memo.answer_version_get': self.version_get,
                    'memo.answer_save': self.save, 'memo.answer_adopt': self.adopt, 'memo.answer_restore': self.restore,
                    'memo.answer_diff': self.diff}
        return handlers[method](**params)


def line_diff(old, new):
    """Blocks of equal, inserted, deleted and changed lines; lines keep their line ends. A changed
    block also lists its word-level edits."""
    a, b = old.splitlines(keepends=True), new.splitlines(keepends=True)
    blocks = []
    for kind, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        block = {'kind': {'replace': 'change'}.get(kind, kind), 'old_start': i1 + 1, 'new_start': j1 + 1,
                 'old_lines': a[i1:i2], 'new_lines': b[j1:j2]}
        if kind == 'replace':
            block['words'] = word_diff(''.join(a[i1:i2]), ''.join(b[j1:j2]))
        blocks.append(block)
    return blocks


def word_diff(old, new):
    a, b = TOKEN.findall(old), TOKEN.findall(new)
    out = []
    for kind, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if kind == 'equal':
            out.append({'op': 'equal', 'text': ''.join(a[i1:i2])})
            continue
        if i2 > i1:
            out.append({'op': 'delete', 'text': ''.join(a[i1:i2])})
        if j2 > j1:
            out.append({'op': 'insert', 'text': ''.join(b[j1:j2])})
    return out


def citation_diff(old, new):
    before = {c.get('id'): c for c in old}
    after = {c.get('id'): c for c in new}
    row = lambda c: {'id': c.get('id'), 'title': c.get('title')}
    return {'added': [row(after[k]) for k in after if k not in before],
            'removed': [row(before[k]) for k in before if k not in after]}
