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

A version also keeps what a claim check of it reads (answer_evidence.check): the evidence context the
answer was written from (question, coverage, the excerpts kept) and its material scope (artifact_ids).
An edit keeps those of the version it edits; taking over an Agent's reply takes the reply's; restoring
takes the restored version's, and a version that kept only its citations is then checked against its
citations alone. How the answer was first generated (model, usage, receipts) stays on the message.

Restoring never moves back in history: it saves the old content as a new version and every version
stays readable. A save that changes neither the text, the citations nor the evidence makes no version.
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
# What a claim check reads besides the text and citations (answer_evidence.check).
REVIEW_INPUTS = ('evidence_context', 'artifact_ids')
# Word-level comparison: a run of spaces, one CJK character, a word, or one other character.
TOKEN = re.compile(r'\s+|[぀-ヿ㐀-鿿豈-﫿]|[^\W_぀-ヿ㐀-鿿豈-﫿]+|.', re.S)


def _content(state):
    """The saved content of a version or a message: text, citations and the review inputs."""
    content = {'text': state['text'], 'citations': copy.deepcopy(state.get('citations') or [])}
    content.update({key: copy.deepcopy(state.get(key)) for key in REVIEW_INPUTS})
    return content


def _content_hash(content):
    return digest(content)


def _meta(record):
    keys = ('version_id', 'number', 'parent_version_id', 'saved_at', 'source', 'restored_from', 'adopted_from',
            'seeded_from', 'content_sha256', 'answer_version')
    return dict({k: record.get(k) for k in keys}, characters=len(record['text']), citation_count=len(record['citations']),
                review_inputs='evidence_context' if record.get('evidence_context') else 'citations')


def _history(thread_id, answer_id, versions):
    return {'schema_version': HISTORY, 'thread_id': thread_id, 'answer_id': answer_id,
            'current_version_id': versions[-1]['version_id'], 'versions': [_meta(v) for v in versions]}


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
        ('answer_history', oldest first) and the current text. Not written; see ensure."""
        legacy = sorted((r for r in self.store.list('answer_history', thread['project'], db=db)
                         if r.get('thread_id') == thread['id'] and r.get('answer_id') == message['id']),
                        key=lambda r: r['message'].get('answer_version', 1))
        states = [(r['message'], 'answer_history') for r in legacy] + [(message, None)]
        versions, parent = [], None
        for index, (state, seeded_from) in enumerate(states):
            # Version k+1 came into being when version k was archived, so its time is that record's.
            saved_at = state.get('created_at') if index == 0 else legacy[index - 1].get('saved_at')
            record = self._record(thread, message['id'], index + 1, parent, 'GENERATED' if index == 0 else 'CLAIM_REVISION',
                                  _content(state), state.get('answer_version', 1), saved_at=saved_at, seeded_from=seeded_from)
            versions.append(record)
            parent = record['version_id']
        return versions

    @staticmethod
    def _record(thread, answer_id, number, parent, source, content, answer_version, *, saved_at=None,
                restored_from=None, adopted_from=None, seeded_from=None):
        return dict(content, thread_id=thread['id'], answer_id=answer_id, version_id=f"{thread['id']}-{answer_id}-s{number}",
                    number=number, parent_version_id=parent, saved_at=saved_at or now(), source=source,
                    restored_from=restored_from, adopted_from=adopted_from, seeded_from=seeded_from,
                    content_sha256=_content_hash(content), answer_version=answer_version)

    def _versions(self, thread, message, db=None):
        return self._stored(thread, message['id'], db) or self._seed(thread, message, db)

    def history(self, thread_id, answer_id):
        thread, message = self._answer(thread_id, answer_id)
        return _history(thread_id, answer_id, self._versions(thread, message))

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

    def _append(self, thread, message, latest, source, content, content_hash, db, **links):
        """The next version after latest, for content message now holds."""
        record = self._record(thread, message['id'], latest['number'] + 1, latest['version_id'], source, content,
                              message.get('answer_version', 1), **links)
        self.store.put('answer_save', record['version_id'], thread['project'], record, expected=0, db=db)
        message['saved_version_id'] = record['version_id']
        return record

    def record(self, thread, message, source, db):
        """After a change made elsewhere (a claim revision): one version for what message holds now,
        unless it equals the latest version."""
        latest = self._stored(thread, message['id'], db)[-1]
        content = _content(message)
        content_hash = _content_hash(content)
        if content_hash == latest['content_sha256']:
            return latest
        return self._append(thread, message, latest, source, content, content_hash, db)

    def _change(self, thread_id, answer_id, expected_version_id, change, source, **links):
        """Save change(thread, message, versions) (the new content) as the answer's next version, together
        with the answer itself, in one transaction."""
        with self.store.tx() as db:
            thread, message = self._answer(thread_id, answer_id, db)
            if thread.get('archived'):
                raise ValueError('THREAD_ARCHIVED')
            if any(j.get('thread_id') == thread_id and j['status'] in {'QUEUED', 'RUNNING'}
                   for j in self.store.list('job', thread['project'], db=db)):
                raise ValueError('THREAD_BUSY')
            versions = self.ensure(thread, message, db)
            latest = versions[-1]
            if expected_version_id != latest['version_id']:
                # The current version goes with the error, so the window can offer to compare.
                raise ValueError('ANSWER_VERSION_CONFLICT:' + latest['version_id'])
            content = change(thread, message, versions)
            text = content['text']
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
                raise ValueError('ANSWER_TEXT_INVALID')
            content_hash = _content_hash(content)
            if content_hash == latest['content_sha256']:
                return {'saved': False, 'version': _meta(latest), 'history': _history(thread_id, answer_id, versions)}
            # Same consequences as a claim revision: reviews and drafts made for the old text no longer hold.
            message.update(text=text, citations=copy.deepcopy(content['citations']),
                           answer_version=message.get('answer_version', 1) + 1, claim_reviews=[], review_binding=None,
                           edited_at=now())
            for key in REVIEW_INPUTS:
                if content[key] is None:
                    message.pop(key, None)
                else:
                    message[key] = copy.deepcopy(content[key])
            message.pop('feedback_id', None)
            record = self._append(thread, message, latest, source, content, content_hash, db, **links)
            self.store.put('thread', thread_id, thread['project'], thread, expected=thread['revision'], db=db)
            self.store.event('ANSWER_SAVED', answer_id, {'thread_id': thread_id, 'version_id': record['version_id'],
                                                         'source': source}, db)
            return {'saved': True, 'version': _meta(record), 'history': _history(thread_id, answer_id, versions + [record])}

    def save(self, thread_id, answer_id, text, expected_version_id, source='EDIT'):
        """A person's edit of the answer text; citations and evidence stay as they are."""
        if source not in {'EDIT', 'AUTOSAVE'}:
            raise ValueError('ANSWER_SAVE_SOURCE_INVALID')
        return self._change(thread_id, answer_id, expected_version_id,
                            lambda thread, message, versions: dict(_content(message), text=text), source)

    def adopt(self, thread_id, answer_id, from_message_id, expected_version_id):
        """Take over an Agent's reply in the same conversation: its text, citations and evidence."""
        def change(thread, message, versions):
            reply = next((m for m in thread['messages'] if m['id'] == from_message_id and m['role'] == 'assistant'), None)
            if reply is None or reply['id'] == answer_id or not reply.get('agent_result'):
                raise ValueError('AGENT_REPLY_NOT_FOUND')
            return _content(reply)
        return self._change(thread_id, answer_id, expected_version_id, change, 'AGENT', adopted_from=from_message_id)

    def restore(self, thread_id, answer_id, version_id, expected_version_id):
        """Save the content of an earlier version, with its evidence, as the newest version."""
        def change(thread, message, versions):
            return _content(self._find(versions, version_id))
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
    """Word edits from old to new. The words both ends share are set aside first, so a long block
    with a small change compares only the part that changed."""
    a, b = TOKEN.findall(old), TOKEN.findall(new)
    head = 0
    while head < min(len(a), len(b)) and a[head] == b[head]:
        head += 1
    tail = 0
    while tail < min(len(a), len(b)) - head and a[len(a) - 1 - tail] == b[len(b) - 1 - tail]:
        tail += 1
    out = [{'op': 'equal', 'text': ''.join(a[:head])}] if head else []
    middle_a, middle_b = a[head:len(a) - tail], b[head:len(b) - tail]
    for kind, i1, i2, j1, j2 in difflib.SequenceMatcher(None, middle_a, middle_b, autojunk=False).get_opcodes():
        if kind == 'equal':
            out.append({'op': 'equal', 'text': ''.join(middle_a[i1:i2])})
            continue
        if i2 > i1:
            out.append({'op': 'delete', 'text': ''.join(middle_a[i1:i2])})
        if j2 > j1:
            out.append({'op': 'insert', 'text': ''.join(middle_b[j1:j2])})
    if tail:
        out.append({'op': 'equal', 'text': ''.join(a[len(a) - tail:])})
    return out


def citation_diff(old, new):
    """Citations added and removed, citations kept under the same id whose saved record changed (the
    fields that differ), and whether the kept ones changed order."""
    before = {c.get('id'): c for c in old}
    after = {c.get('id'): c for c in new}
    row = lambda c: {'id': c.get('id'), 'title': c.get('title')}
    kept_before = [k for k in before if k in after]
    kept_after = [k for k in after if k in before]
    changed = [dict(row(after[k]), fields=sorted(f for f in set(before[k]) | set(after[k]) if before[k].get(f) != after[k].get(f)))
               for k in kept_after if before[k] != after[k]]
    return {'added': [row(after[k]) for k in after if k not in before],
            'removed': [row(before[k]) for k in before if k not in after],
            'changed': changed, 'reordered': kept_before != kept_after}
