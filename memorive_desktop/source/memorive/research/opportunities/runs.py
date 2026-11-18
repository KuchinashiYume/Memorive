"""Finding research opportunities: a run of the research runtime, like the AI briefing.

ProductApi forwards opportunities.* here (Opportunities.call). opportunities.find starts a run in its
own thread:

  COLLECTING   the fact sheets of the processed papers (facts.papers)
  COMPOSING    one request per group of papers (finder.groups) to the model chosen for research Q&A,
               each answer checked (finder.checked); a group whose request fails is reported and the
               other groups go on (the run is then PARTIAL)
  PUBLISHING   result.json / result.md in the run's folder, published to the library
  NOTIFYING    a message that the result is ready

The ledger, research/opportunities.json, keeps every opportunity found, by fingerprint, with what the
user did with it: new, kept, dismissed, or asked (a follow-up conversation was started). Finding it
again keeps that status. Cancelling stops the run before the next group's request; a request already
sent is answered first. Nothing runs by itself: papers processed after the last run only show up as
papers_since_last_run in the state, and the user decides when to look again.
"""
import re, threading
from memorive.research.common import read, write, sealed, sha, now
from memorive.research.opportunities import facts, finder
from memorive.platform.language.policy import from_settings
from memorive.platform.language.text import choose

METHODS = frozenset({'opportunities.state', 'opportunities.find', 'opportunities.get', 'opportunities.cancel',
                     'opportunities.mark'})
STATUSES = ('new', 'kept', 'asked', 'dismissed')
TENSION_KINDS = ('divergence', 'condition_difference')


def title(language, day):
    return choose(language, '研究机会', 'Research opportunities', '研究の機会') + ' · ' + day


def today():
    from datetime import datetime
    from memorive.research.reports.policy import ZONE
    return datetime.now(ZONE).date().isoformat()


def ledger_path(root):
    return root / 'opportunities.json'


def read_ledger(root):
    path = ledger_path(root)
    return read(path) if path.exists() else {'revision': 0, 'items': {}}


def report_rows(root):
    """Divergences and condition differences the user has not dismissed, as material progress of
    the periodic reports (reports/periodic.py): the weekly report lists them as tensions between sources."""
    rows = []
    for item in read_ledger(root)['items'].values():
        if item['kind'] in TENSION_KINDS and item['status'] != 'dismissed':
            rows.append({'material_kind': 'OPPORTUNITY', 'task_id': item['fingerprint'], 'opportunity_kind': item['kind'],
                         'display_name': item['title'], 'paper_titles': [p['title'] for p in item['papers']],
                         'stable_locator': 'memorive://artifact/' + item['first_run_id'] + '/result',
                         'updated_at': item['first_found_at']})
    return rows


class Opportunities:
    def __init__(self, runtime):
        self.r = runtime

    def papers(self):
        library = self.r.host.library
        return facts.papers(library.core_artifacts(), library.resolve_core_private_artifact)

    def model(self):
        """The model chosen for research Q&A, when it can be used; None otherwise."""
        from memorive.research.briefing.jobs import workspace_settings
        from memorive.research.reports.models import catalog
        ref = workspace_settings(self.r.host).get('profile_ref')
        return next((row for row in catalog(self.r) if row['profile_ref'] == ref and row['eligible']), None)

    def runs(self):
        return [s for s in self.r.state()['runs'] if s['kind'] == 'opportunities']

    def state(self):
        ledger = read_ledger(self.r.root)
        cards = {record['content_sha256'] for record in self.r.host.library.core_artifacts().values()
                 if record['artifact_kind'] == 'CORE_CARD'}
        runs = self.runs()
        last = next((run for run in runs if run['status'] in {'SUCCEEDED', 'PARTIAL'}), None)
        seen = set(read(self.r.run_path(last['run_id']) / 'result.json')['card_sha256s']) if last else set()
        items = []
        for item in ledger['items'].values():
            # A card changed or removed since: the opportunity rests on statements no longer in the library.
            outdated = any(paper['card_sha256'] not in cards for paper in item['papers'])
            items.append(dict(item, outdated=outdated))
        # New ones first, then kept, asked and dismissed; the newest first within each.
        items.sort(key=lambda i: i['first_found_at'], reverse=True)
        items.sort(key=lambda i: STATUSES.index(i['status']))
        return {'revision': ledger['revision'], 'items': items, 'model': self.model(), 'card_count': len(cards),
                'papers_since_last_run': len(cards - seen), 'runs': runs[:20],
                'active_run': next((run for run in runs if run['status'] in {'QUEUED', 'RUNNING'}), None)}

    def get(self, run_id):
        state = self.r.call('research.get', {'run_id': run_id})
        if state['kind'] != 'opportunities':
            raise ValueError('OPPORTUNITIES_RUN_INVALID')
        return state

    def call(self, method, p):
        if method == 'opportunities.state':
            if p:
                raise ValueError('OPPORTUNITIES_PARAMS_INVALID')
            return self.state()
        if method == 'opportunities.find':
            if set(p) - {'request_id', 'focus'}:
                raise ValueError('OPPORTUNITIES_PARAMS_INVALID')
            return self.start(**p)
        if method == 'opportunities.mark':
            if set(p) != {'fingerprint', 'status', 'expected_revision'}:
                raise ValueError('OPPORTUNITIES_PARAMS_INVALID')
            return self.mark(**p)
        if set(p) != {'run_id'}:
            raise ValueError('OPPORTUNITIES_PARAMS_INVALID')
        self.get(p['run_id'])
        return self.get(p['run_id']) if method == 'opportunities.get' else self.r.call('research.cancel', p)

    def mark(self, fingerprint, status, expected_revision):
        """Record what the user did with an opportunity: kept, dismissed, asked, or new again."""
        if status not in STATUSES:
            raise ValueError('OPPORTUNITIES_STATUS_INVALID')
        with self.r.lock:
            ledger = read_ledger(self.r.root)
            if ledger['revision'] != expected_revision:
                raise ValueError('OPPORTUNITIES_REVISION_CONFLICT')
            item = ledger['items'].get(fingerprint)
            if item is None:
                raise ValueError('OPPORTUNITY_NOT_FOUND')
            ledger['items'][fingerprint] = dict(item, status=status, status_changed_at=now())
            write(ledger_path(self.r.root), sealed(dict(ledger, revision=ledger['revision'] + 1)))
        return self.state()

    def start(self, request_id, focus=''):
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 160 or not isinstance(focus, str) or len(focus) > 500:
            raise ValueError('OPPORTUNITIES_PARAMS_INVALID')
        identity = 'research-' + sha('opportunities:' + request_id)[:24]
        path = self.r.run_path(identity)
        with self.r.lock:
            if self.r.closed:
                raise ValueError('RESEARCH_CLOSED')
            if (path / 'state.json').exists():
                return self.get(identity)
            model = self.model()
            if not model:
                raise ValueError('OPPORTUNITIES_MODEL_REQUIRED')
            if self.r.has_active_run():
                raise ValueError('RESEARCH_ALREADY_RUNNING')
            language = from_settings(self.r.host.service, current_user_text=focus)
            params = {'kind': 'opportunities', 'period': None, 'config': {'push_enabled': True}, 'focus': focus.strip(),
                      'model_ref': model['profile_ref'], 'language_context': language}
            write(path / 'request.json', sealed(params))
            self.r.stop = threading.Event()
            self.r.current = identity
            item = self.r.update_state(identity, run_id=identity, kind='opportunities', period=None,
                                       title=title(language, today()), language_context=language, status='QUEUED',
                                       stage='WAITING', created_at=now(), request_sha256=sha(params),
                                       model_calls=0, paid_model_calls=0)
            self.r.thread = threading.Thread(target=self.execute, args=(identity, params), name='desktop-research-opportunities', daemon=True)
            self.r.thread.start()
            return item

    def _ask(self, run_id, number, group, params, check):
        """One group's request and its checked opportunities: (kept, dropped, snapshot, calls)."""
        from memorive.research.workspace.model_receipts import verified_result
        service = self.r.host.service
        # Each request needs a snapshot of its own: a snapshot is sent once.
        snap = service.call('settings.research_chat_freeze', {'run_id': 'research-' + sha([run_id, number])[:24],
                                                              'profile_ref': params['model_ref']})
        # What was found in these papers before, so that a later run does not find it again in other words.
        numbers = {paper['job_id']: paper['paper'] for paper in group}
        known = [{'kind': item['kind'], 'title': item['title'], 'papers': [numbers[p['job_id']] for p in item['papers']]}
                 for item in read_ledger(self.r.root)['items'].values() if all(p['job_id'] in numbers for p in item['papers'])]
        text = finder.prompt(group, focus=params['focus'], language_instruction=params['language_context']['instruction'],
                             known=known)
        check()
        response = service.call('settings.research_chat_execute', {'snapshot_id': snap['snapshot_id'], 'prompt': text,
                                                                   'response_schema': finder.SCHEMA})
        write(self.r.run_path(run_id) / f'model_result_{number}.json', sealed(response))
        calls = len((response.get('execution_receipt') or {}).get('physical_call_ids') or [])
        verified_result(response, snap)
        return (*finder.checked(response.get('response'), group), snap, calls)

    def _remember(self, run_id, found, stamp):
        """Add this run's opportunities to the ledger; one found before keeps its status and first date."""
        with self.r.lock:
            ledger = read_ledger(self.r.root)
            items = dict(ledger['items'])
            for item in found:
                old = items.get(item['fingerprint']) or {}
                items[item['fingerprint']] = dict(item, status=old.get('status', 'new'),
                                                  first_found_at=old.get('first_found_at', stamp),
                                                  first_run_id=old.get('first_run_id', run_id), last_run_id=run_id, last_found_at=stamp)
            write(ledger_path(self.r.root), sealed({'revision': ledger['revision'] + 1, 'items': items}))

    def execute(self, run_id, params):
        from memorive.models.scheduling import TASK_CATEGORIES
        from memorive.models.calls.ledger import execution_control, ExecutionControlSignal
        from memorive.platform.progress import session_scope
        r = self.r
        path = r.run_path(run_id)

        def check():
            if r.stop.is_set():
                raise ExecutionControlSignal('CANCELLED')
        try:
            with execution_control(check), TASK_CATEGORIES.enter('RESEARCH_OPPORTUNITIES', checkpoint=check), \
                    session_scope(r.progress_session(run_id)):
                r.update_state(run_id, status='RUNNING', stage='COLLECTING')
                papers = self.papers()
                if not papers:
                    raise ValueError('OPPORTUNITIES_NO_PAPERS')
                write(path / 'papers.json', sealed({'papers': [{k: v for k, v in paper.items() if k != 'chunks'} for paper in papers]}))
                groups = finder.groups(papers)
                r.update_state(run_id, stage='COMPOSING', paper_count=len(papers), group_count=len(groups))
                found, dropped, failed, calls, model = [], [], [], 0, None
                for number, group in enumerate(groups, 1):
                    check()
                    try:
                        kept, lost, snap, used = self._ask(run_id, number, group, params, check)
                    except Exception as exc:
                        failed.append({'group': number, 'papers': [p['title'] for p in group], 'error': str(exc)[:180]})
                        continue
                    model = model or snap
                    calls += used
                    found += kept
                    dropped += [dict(row, group=number) for row in lost]
                    r.update_state(run_id, report_model=model, model_calls=calls,
                                   paid_model_calls=calls if model['profile_kind'] == 'API' else 0)
                if len(failed) == len(groups):
                    raise ValueError(failed[0]['error'] if re.fullmatch('[A-Z0-9_: -]{1,180}', failed[0]['error']) else 'OPPORTUNITIES_MODEL_FAILED')
                check()
                stamp = now()
                language = params['language_context']
                unreviewed = sum(not paper['card_reviewed'] for paper in papers)
                # The result lists what is new; one found again stays in the ledger as it was.
                earlier = read_ledger(r.root)['items']
                new = [item for item in found if item['fingerprint'] not in earlier]
                result = sealed({
                    'schema_version': 'ResearchOpportunities-v1', 'kind': 'opportunities', 'period': None,
                    'status': 'PARTIAL' if failed else 'SUCCEEDED', 'language_context': language, 'focus': params['focus'],
                    'paper_count': len(papers), 'unreviewed_count': unreviewed,
                    'card_sha256s': sorted(paper['card_sha256'] for paper in papers),
                    'opportunities': new, 'dropped': dropped, 'failed_groups': failed,
                    'model': {k: model[k] for k in ('model_name', 'display_name', 'profile_kind')},
                    'model_calls': calls, 'paid_model_calls': calls if model['profile_kind'] == 'API' else 0,
                    'markdown': finder.markdown(new, day=today(), paper_count=len(papers), unreviewed=unreviewed,
                                                earlier=len(earlier), language=language)})
                write(path / 'result.json', result)
                (path / 'result.md').write_text(result['markdown'], encoding='utf8')
                self._remember(run_id, found, stamp)
                check()
                r.update_state(run_id, stage='PUBLISHING', result_sha256=result['sha256'])
                r.publish_to_library(run_id, 'opportunities', result)
                r.update_state(run_id, stage='NOTIFYING')
                mid = r.post_result_message(run_id, 'opportunities', result, None)
                r.update_state(run_id, status=result['status'], stage='COMPLETE', finished_at=now(), message_id=mid,
                               notification_status='MESSAGE_CREATED')
        except (Exception, ExecutionControlSignal) as exc:
            previous = read(path / 'state.json')
            code = str(exc)
            if not re.fullmatch('[A-Z0-9_: -]{1,180}', code):
                code = 'OPPORTUNITIES_EXECUTION_FAILED'
            stopped = 'INTERRUPTED' if r.closed else 'CANCELLED' if r.stop.is_set() else 'FAILED'
            r.update_state(run_id, status=stopped, stage='STOPPED', failed_stage=previous.get('stage'), error_code=code, finished_at=now())
        r.notify_ui()
