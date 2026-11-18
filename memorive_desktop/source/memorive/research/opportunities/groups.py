"""Comparison groups: the papers the user chose to look for research opportunities among.

The research opportunities page lists the processed papers (papers: by name over all time, or those
of the last 30 days) and lets the user tick the ones whose card is admitted. The ticked papers are
kept as a named group in research/opportunity_groups.json, so the next search can use them again;
opportunities.find with a group_id looks only among a group's papers (runs.py).

Files picked from disk for a group (add_files) are imported into the inbox and their processing is
started, as a file dropped into the inbox. Until a file's card is admitted it waits in the group's
pending list; a group read after that shows it among its papers. Nothing is written when a group is
read: the next save keeps what the page then shows.

Opportunities.call forwards opportunities.papers, groups, group_save, group_delete and
group_add_files here.
"""
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from memorive.documents.inbox.contracts import start_key
from memorive.platform.language.policy import from_settings
from memorive.platform.language.text import choose
from memorive.research.common import read, write, sealed, now
from memorive.research.opportunities.facts import card_fields

METHODS = frozenset({'opportunities.papers', 'opportunities.groups', 'opportunities.group_save',
                     'opportunities.group_delete', 'opportunities.group_add_files'})
RECENT_DAYS = 30
MIN_PAPERS = 2


def library_papers(core_artifacts: dict, resolve) -> dict[str, dict]:
    """job id -> {job_id, title, name, processed_at, card_status} of every processed paper, newest first.

    card_status is the card's review status: active (it can be compared), pending (waiting for
    review) or quarantined (taken out of use); missing when the job left no card. name is the
    source file's name in the library, title the card's title (the name when the card has none)."""
    names = {record['job_id']: record['row'].get('display_name') for record in core_artifacts.values()
             if record['artifact_kind'] == 'CORE_DOCUMENT'}
    papers = {}
    for artifact_id, record in core_artifacts.items():
        if record['artifact_kind'] != 'CORE_CARD':
            continue
        job_id = record['job_id']
        card = card_fields(resolve(artifact_id))
        name = names.get(job_id) or record['row'].get('display_name') or job_id
        papers[job_id] = {'job_id': job_id, 'title': ' '.join(str(card.get('title') or '').split()) or name,
                          'name': name, 'processed_at': record['row'].get('updated_at'),
                          'card_status': card.get('review_status', 'active')}
    for record in core_artifacts.values():
        if record['artifact_kind'] == 'CORE_DOCUMENT' and record['job_id'] not in papers:
            name = record['row'].get('display_name') or record['job_id']
            papers[record['job_id']] = {'job_id': record['job_id'], 'title': name, 'name': name,
                                        'processed_at': record['row'].get('updated_at'), 'card_status': 'missing'}
    return dict(sorted(papers.items(), key=lambda pair: str(pair[1]['processed_at'] or ''), reverse=True))


def recent(paper: dict, days: int = RECENT_DAYS) -> bool:
    try:
        stamp = datetime.fromisoformat(str(paper['processed_at']))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp >= datetime.now(timezone.utc) - timedelta(days=days)


class Groups:
    def __init__(self, runtime):
        self.r = runtime

    def path(self) -> Path:
        return self.r.root / 'opportunity_groups.json'

    def stored(self) -> dict:
        return read(self.path()) if self.path().exists() else {'revision': 0, 'groups': {}}

    def store(self, value: dict) -> None:
        write(self.path(), sealed(dict(value, revision=value['revision'] + 1)))

    def library(self) -> dict[str, dict]:
        library = self.r.host.library
        return library_papers(library.core_artifacts(), library.resolve_core_private_artifact)

    def call(self, method, p):
        if method == 'opportunities.papers':
            if set(p) - {'query', 'limit'}:
                raise ValueError('OPPORTUNITIES_PARAMS_INVALID')
            return self.papers(**p)
        if method == 'opportunities.groups':
            if p:
                raise ValueError('OPPORTUNITIES_PARAMS_INVALID')
            return self.groups()
        if method == 'opportunities.group_save':
            if not {'job_ids', 'expected_revision'} <= set(p) <= {'group_id', 'name', 'job_ids', 'pending_item_ids',
                                                                  'expected_revision'}:
                raise ValueError('OPPORTUNITIES_PARAMS_INVALID')
            return self.save(**p)
        if method == 'opportunities.group_delete':
            if set(p) != {'group_id', 'expected_revision'}:
                raise ValueError('OPPORTUNITIES_PARAMS_INVALID')
            return self.delete(**p)
        if set(p) != {'group_id', 'paths', 'request_id'}:
            raise ValueError('OPPORTUNITIES_PARAMS_INVALID')
        return self.add_files(**p)

    def papers(self, query='', limit=200):
        """Papers by name over all time, or with no query those processed in the last 30 days."""
        if not isinstance(query, str) or len(query) > 200 or isinstance(limit, bool) or not isinstance(limit, int) \
                or not 1 <= limit <= 500:
            raise ValueError('OPPORTUNITIES_PARAMS_INVALID')
        words = query.casefold().split()
        rows = [paper for paper in self.library().values()
                if (all(word in (paper['title'] + ' ' + paper['name']).casefold() for word in words) if words
                    else recent(paper))]
        return {'items': rows[:limit], 'total': len(rows)}

    def pending_state(self, entry: dict, papers: dict) -> dict:
        """Where a file added to a group stands: queued, processing, failed (with its error), no_card,
        card_pending, card_quarantined, removed (taken out of the inbox), or admitted with its job."""
        try:
            item = self.r.host.desktop_call('inbox.item_detail', {'item_id': entry['item_id']})
        except Exception as exc:
            # Trashed or deleted in the inbox: the file is no longer on its way to the group.
            if 'INBOX_ITEM_NOT_VISIBLE' not in str(exc) and 'INBOX_ITEM_NOT_FOUND' not in str(exc):
                raise
            return dict(entry, status='removed', job_id=None)
        job_id = item.get('job_id')
        paper = papers.get(job_id) or {}
        if paper.get('card_status', 'missing') != 'missing':
            status = 'admitted' if paper['card_status'] == 'active' else 'card_' + paper['card_status']
        elif item['state'] == 'ERROR':
            return dict(entry, status='failed', job_id=job_id, error=(item.get('error') or {}).get('code'))
        elif job_id:
            # The library lists a document while its job still runs; no card means something only once it ended.
            control = self.r.host.service.call('get_job', {'job_id': job_id}).get('control_state')
            status = 'failed' if control in {'FAILED', 'CANCELLED'} else 'no_card' if control == 'SUCCEEDED' else 'processing'
        else:
            status = 'queued' if item['state'] == 'QUEUED' else 'processing'
        return dict(entry, status=status, job_id=job_id)

    def view(self, group: dict, papers: dict) -> dict:
        members = [papers.get(job_id) or {'job_id': job_id, 'title': job_id, 'card_status': 'missing'}
                   for job_id in group['job_ids']]
        pending = []
        for entry in group['pending']:
            state = self.pending_state(entry, papers)
            if state['status'] == 'admitted':
                # Processed and admitted: one of the group's papers from now on.
                if state['job_id'] not in {paper['job_id'] for paper in members}:
                    members.append(papers[state['job_id']])
                continue
            pending.append(state)
        return {'group_id': group['group_id'], 'name': group['name'], 'updated_at': group['updated_at'],
                'papers': [{key: paper[key] for key in ('job_id', 'title', 'card_status')} for paper in members],
                'pending': pending}

    def groups(self) -> dict:
        stored, papers = self.stored(), self.library()
        rows = sorted(stored['groups'].values(), key=lambda group: group['created_at'])
        return {'revision': stored['revision'], 'groups': [self.view(group, papers) for group in rows]}

    def group(self, group_id) -> dict:
        """One group as the page shows it."""
        group = self.stored()['groups'].get(group_id)
        if group is None:
            raise ValueError('OPPORTUNITIES_GROUP_NOT_FOUND')
        return self.view(group, self.library())

    def default_name(self, groups: dict) -> str:
        label = choose(from_settings(self.r.host.service), '比较组', 'Comparison group', '比較グループ')
        taken = {group['name'] for group in groups.values()}
        number = len(groups) + 1
        while f'{label} {number}' in taken:
            number += 1
        return f'{label} {number}'

    def save(self, job_ids, expected_revision, group_id=None, name=None, pending_item_ids=None):
        """Create a group (no group_id) or replace one's papers; pending_item_ids keeps only those
        files still on their way (all of them when it is not given)."""
        if not isinstance(job_ids, list) or not all(isinstance(job_id, str) for job_id in job_ids) \
                or len(set(job_ids)) != len(job_ids) or len(job_ids) > 500 \
                or not (name is None or isinstance(name, str) and len(name) <= 60) \
                or not (pending_item_ids is None or isinstance(pending_item_ids, list)):
            raise ValueError('OPPORTUNITIES_PARAMS_INVALID')
        papers = self.library()
        for job_id in job_ids:
            if (papers.get(job_id) or {}).get('card_status') != 'active':
                raise ValueError('OPPORTUNITIES_PAPER_NOT_ACTIVE:' + job_id)
        with self.r.lock:
            stored = self.stored()
            if stored['revision'] != expected_revision:
                raise ValueError('OPPORTUNITIES_GROUP_REVISION_CONFLICT')
            groups = dict(stored['groups'])
            if group_id is None:
                group_id = 'group-' + uuid.uuid4().hex[:12]
                group = {'group_id': group_id, 'name': '', 'job_ids': [], 'pending': [], 'created_at': now()}
            elif group_id in groups:
                group = groups[group_id]
            else:
                raise ValueError('OPPORTUNITIES_GROUP_NOT_FOUND')
            pending = [entry for entry in group['pending']
                       if pending_item_ids is None or entry['item_id'] in pending_item_ids]
            others = {key: value for key, value in groups.items() if key != group_id}
            groups[group_id] = dict(group, job_ids=job_ids, pending=pending, updated_at=now(),
                                    name=(name or '').strip() or group['name'] or self.default_name(others))
            self.store(dict(stored, groups=groups))
        return self.groups()

    def delete(self, group_id, expected_revision):
        with self.r.lock:
            stored = self.stored()
            if stored['revision'] != expected_revision:
                raise ValueError('OPPORTUNITIES_GROUP_REVISION_CONFLICT')
            if group_id not in stored['groups']:
                raise ValueError('OPPORTUNITIES_GROUP_NOT_FOUND')
            self.store(dict(stored, groups={key: value for key, value in stored['groups'].items() if key != group_id}))
        return self.groups()

    def add_files(self, group_id, paths, request_id):
        """Import files into the inbox and start their processing; each joins the group as a paper
        once its card is admitted (at once for a file processed before with an admitted card).

        paths are the copies pick_opportunity_files placed in the inbox's staging folder."""
        if not isinstance(paths, list) or not 1 <= len(paths) <= 32 or not all(isinstance(path, str) for path in paths) \
                or not isinstance(request_id, str) or not 1 <= len(request_id) <= 160:
            raise ValueError('OPPORTUNITIES_PARAMS_INVALID')
        if group_id not in self.stored()['groups']:
            raise ValueError('OPPORTUNITIES_GROUP_NOT_FOUND')
        call = self.r.host.desktop_call
        batch = call('inbox.import_paths', {'paths': paths, 'request_id': request_id, 'source_kind': 'picker'})
        papers = self.library()
        admitted, waiting = [], {}
        for row in batch['items']:
            item = row['item']
            if item['state'] == 'DEDUPLICATED':
                # The same file is in the inbox already: follow that item.
                item = call('inbox.item_detail', {'item_id': item['duplicate_of']})
            paper = papers.get(item.get('job_id'))
            if paper and paper['card_status'] == 'active':
                admitted.append(paper['job_id'])
                continue
            if item['state'] == 'QUEUED' and not item.get('job_id'):
                # The key the inbox's automatic run uses: when that is on, it has started the file already.
                call('inbox.dispatch', {'item_id': item['item_id'], 'idempotency_key': start_key(item['item_id'])})
            waiting[item['item_id']] = {'item_id': item['item_id'], 'added_at': now(),
                                        'title': item.get('source_name') or item.get('name') or item['item_id']}
        with self.r.lock:
            stored = self.stored()
            group = stored['groups'].get(group_id)
            if group is None:
                raise ValueError('OPPORTUNITIES_GROUP_NOT_FOUND')
            job_ids = group['job_ids'] + [job_id for job_id in dict.fromkeys(admitted) if job_id not in group['job_ids']]
            known = {entry['item_id'] for entry in group['pending']}
            pending = group['pending'] + [entry for entry in waiting.values() if entry['item_id'] not in known]
            groups = dict(stored['groups'], **{group_id: dict(group, job_ids=job_ids, pending=pending, updated_at=now())})
            self.store(dict(stored, groups=groups))
        return self.groups()
