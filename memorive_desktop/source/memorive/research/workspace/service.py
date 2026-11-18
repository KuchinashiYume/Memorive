"""The research workspace: research Q&A over a project's material, with conversations, memories and
knowledge drafts.

ResearchWorkspace is the entry point. The desktop window and external agents reach it through
call(method, params): the window may use DESKTOP_METHODS, an external agent only PUBLIC_METHODS.
Each method is short here and hands the work to the module that owns it:

  answer_versions.py   queuing a question (ask), answering again, answer branches
  answer_saves.py      every save of an answer kept as a version: history, compare, restore
  answering.py         answering a queued question, step by step
  evidence.py          the evidence index: excerpts, reading them, searching
  attachments.py       a conversation's material and files added to it
  conversations.py     the conversation list, archive, duplicate, export
  connections.py       connected apps (Zotero, Obsidian ...)
  handoffs.py          hand-off packs for agents and what comes back
  agents.py            agent settings, folders for desktop agent apps
  topic.py             the project's research topic

The model is optional. Without one a question gets the excerpts found, said as such; nothing then
pretends to be a generated answer or starts an external agent.
"""
import json,threading,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from memorive.research.workspace.store import Store,digest,uid,now,packed
from memorive.research.workspace.evidence import EvidenceIndex,material_layer,source_document_hash
from memorive.research.workspace.attachments import Attachments,METHODS as ATTACHMENT_METHODS
from memorive.research.workspace.conversations import Conversations,METHODS as CONVERSATION_METHODS
from memorive.research.workspace.connections import Connections,DESKTOP as CONNECTION_METHODS
from memorive.research.workspace.answer_evidence import AnswerEvidence,METHODS as ANSWER_EVIDENCE_METHODS
from memorive.research.workspace.answer_saves import AnswerSaves,METHODS as ANSWER_SAVE_METHODS
from memorive.research.workspace.agents import CLI_ADAPTERS,agent_matches,desktop_agent,desktop_handoff,validate_agent_settings  # noqa: F401 (desktop_handoff: runtime.py)
from memorive.research.workspace import answer_coverage
from memorive.research.workspace import answer_versions
from memorive.platform.language import freeze as freeze_language
from memorive.platform.language.text import choose

PUBLIC_METHODS=frozenset({'memo.capabilities','memo.search','memo.read_evidence','memo.prepare_tension',
    'memo.propose_feedback','memo.submit_draft','memo.job_status','memo.skill_context','memo.skill_answer'})
DEVELOPER_METHODS=frozenset({'memo.developer_state','memo.developer_save','memo.automation_save','memo.developer_config'})
DESKTOP_METHODS=(PUBLIC_METHODS|DEVELOPER_METHODS|frozenset({
    'memo.state','memo.settings_save','memo.project_save','memo.index_refresh','memo.thread_create','memo.thread_get',
    'memo.thread_forget','memo.ask','memo.cancel','memo.memory_save','memo.memory_forget','memo.topic_change',
    'memo.agent_dispatch','memo.knowledge_health','memo.feedback_revise','memo.interests','memo.interest_remove',
    'memo.weight_settings','memo.weight_save','memo.weight_preview','memo.material_metadata','memo.material_metadata_save',
    'memo.feedback_review','memo.feedback_retract','memo.handoff','memo.export_handoff','memo.library_list','memo.library_add'})
    |CONNECTION_METHODS|CONVERSATION_METHODS|ATTACHMENT_METHODS|ANSWER_EVIDENCE_METHODS|answer_versions.METHODS
    |ANSWER_SAVE_METHODS)
SETTINGS_FIELDS={'profile_ref','memory_enabled','use_recent_interests','context_chars','recent_messages','agent',
                 'agent_profile_ref','default_project','custom_agents','desktop_agents'}
# Kinds of record that belong to one conversation and go with it when it is forgotten.
THREAD_RECORDS=('answer_history','answer_save','answer_review','coverage_probe','answer_feedback','answer_draft','handoff',
                'desktop_handoff','agent_return','skill_return')


class ResearchWorkspace:
    def __init__(self, root, *, model=None, catalog=None, recover=False, interest_get=None, interest_set=None, vision=None,
                 user_preferences_get=None, language_settings_get=None, lifecycle_clock=None):
        """root: the workspace folder. model, vision: functions that call the chosen model (see
        desktop.py); catalog: the model profiles that may be chosen. recover: this process owns the
        desktop's state, so jobs left running by a previous process are marked interrupted."""
        from memorive.research.knowledge_feedback.research_ingest import ResearchFeedback
        from memorive.research.ranking.research_policy import ResearchPolicy
        from memorive.research.workspace.handoffs import Handoffs
        from memorive.research.workspace.skill_bridge import ResearchSkill
        from memorive.research.workspace.topic import Topic
        self.store = Store(root, lifecycle_clock=lifecycle_clock)
        self.index = EvidenceIndex(self.store)
        self.model = model
        self.catalog = catalog or (lambda: [])
        self.embedding_catalog = lambda: []
        self.interest_get = interest_get
        self.interest_set = interest_set
        self.vision = vision
        self.agent_model = None
        self.handoffs = Handoffs(self)
        self.feedback = ResearchFeedback(self.store, self.index, self.handoffs.validate_review)
        self.topic = Topic(self)
        self.skill = ResearchSkill(self)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='memo-research')
        self.closed = False
        self.lock = threading.RLock()
        self.futures = {}
        self.owns_desktop_state = recover
        self.temporary_threads = set()
        self.weights = ResearchPolicy(self.store)
        self.attachments = Attachments(self)
        self.conversations = Conversations(self)
        self.connections = Connections(self)
        self.connector_stop = threading.Event()
        self.connector_thread = None
        self.answer_evidence = AnswerEvidence(self)
        self.versions = answer_versions.AnswerVersions(self)
        self.saves = AnswerSaves(self)
        self.language_settings_get = language_settings_get or (lambda: dict(revision=0, settings=dict(preferences=dict(language='zh-CN'))))
        self.user_preferences_get = user_preferences_get or (lambda: dict(revision=0, config=dict(answer_style='professional')))
        # Legacy answer trees are normalized on individual reads and persisted
        # with their next actual content edit, never by a startup body scan.
        self._developer = None
        self.interrupted_during_model_call = []  # filled by _recover_after_restart
        if recover:
            self._recover_after_restart()

    def _recover_after_restart(self):
        """Jobs and attachment preparations a previous process left running did not finish: mark them
        interrupted (the user retries). Temporary conversations of that process are forgotten.
        Jobs whose model call had started are remembered for finish_answers_received_before_restart."""
        with self.store.tx() as db:
            for job in self.store.recoverable_jobs('attachment_job', db):
                if job['status'] not in {'QUEUED', 'RUNNING'}:
                    continue
                for item in job['items']:
                    if item['status'] == 'QUEUED':
                        item.update(status='ERROR', error='ATTACHMENT_INTERRUPTED')
                        artifact = self.store.get('artifact', item['id'], db=db)
                        if artifact and artifact['state'] == 'preparing':
                            self.store.put('artifact', artifact['id'], artifact['project'],
                                           dict(artifact, state='error', error='ATTACHMENT_INTERRUPTED'), db=db)
                self.store.put('attachment_job', job['id'], job['project'], dict(job, status='PARTIAL'), db=db)
            for job in self.store.recoverable_jobs('job', db):
                if job['status'] in {'QUEUED', 'RUNNING'}:
                    self.store.put('job', job['id'], job['project'], dict(job, status='INTERRUPTED', error='Restarted; retry explicitly'), db=db)
                    if job.get('model_snapshot'):
                        self.interrupted_during_model_call.append(job['id'])
        # A crash cannot run close(). Temporary branches and drafts from
        # that process must not reappear in the next desktop session.
        with self.store.read() as db:
            temporary = [(r['id'], r['source_revision']) for r in db.execute('SELECT id,source_revision FROM conversation_catalog WHERE temporary=1')]
        for identity, revision in temporary:
            self.thread_forget(identity, revision)
        # Forgetting a temporary conversation deletes its jobs too; only the jobs still here are
        # answered again (finish_answers_received_before_restart reads each one).
        self.interrupted_during_model_call = [job_id for job_id in self.interrupted_during_model_call
                                              if self.store.get('job', job_id) is not None]

    def note_model_snapshot(self, job_id, snapshot):
        """Keep on a running question the model snapshot it is sent with, so that after a restart the
        result kept under it can still be saved (finish_answers_received_before_restart). Only the
        questions AnswerJob answers keep it: kind 'chat' or 'agent' (AnswerVersions.enqueue). The
        same model function also serves calls that are not such an answer: ranking has no job, and
        a claim review runs as a job of kind 'answer_review' that is not answered again."""
        with self.store.tx() as db:
            job = self.store.get('job', job_id, db=db)
            if job and job['status'] == 'RUNNING' and job.get('kind') in ('chat', 'agent'):
                self.store.put('job', job_id, job['project'], dict(job, model_snapshot=snapshot), db=db)

    def finish_answers_received_before_restart(self, stored_result):
        """The second half of a restart, run once the desktop has wired the workspace. A job the restart
        interrupted while its model call was running is answered again through the normal steps when
        stored_result(snapshot) gives the result the settings service kept for that call: the excerpts
        are searched again and nothing is sent. Without a kept result the job stays INTERRUPTED."""
        from memorive.research.workspace.answering import answer_job
        for job_id in self.interrupted_during_model_call:
            job = self.store.get('job', job_id)
            kept = stored_result(job['model_snapshot'])
            if kept is None:
                continue
            with self.store.tx() as db:
                job = self.store.get('job', job_id, db=db)
                if job['status'] != 'INTERRUPTED':
                    continue
                queued = {key: value for key, value in job.items() if key != 'error'}
                self.store.put('job', job_id, job['project'], dict(queued, status='QUEUED'), db=db)
            self.futures[job_id] = self.pool.submit(answer_job, self, job_id, job['question_message']['text'],
                                                    job['artifact_ids'], received=(job['model_snapshot'], kept))

    @property
    def developer(self):
        # Opening a conversation does not need the separate developer console
        # schemas. Initialize that API at its first actual consumer, under the
        # workspace lock so concurrent external callers share one instance.
        with self.lock:
            if self._developer is None:
                from memorive.research.workspace.developer_api import DeveloperAPI
                self._developer = DeveloperAPI(self)
            return self._developer

    def settings(self):
        value = self.store.get('settings', 'default')
        if self.interest_get is not None:
            value['use_recent_interests'] = self.interest_get()
        return value

    def state(self, project='default', view_thread_id=None):
        """What the window shows for a project (memo.state); view_thread_id limits it to one conversation."""
        if not self.store.get('project', project):
            raise ValueError('PROJECT_NOT_FOUND')
        if view_thread_id is not None and not isinstance(view_thread_id, str):
            raise ValueError('THREAD_VIEW_INVALID')
        # The desktop navigation has its own metadata index. Refreshing the
        # open conversation must not transfer every other conversation body
        # or completed job's retained context on each update. Omitted scope
        # preserves the complete state response for existing callers.
        if view_thread_id is None:
            threads = self.store.list('thread', project)
            jobs = self.store.list('job', project)[:40]
        else:
            selected = self.store.get('thread', view_thread_id) if view_thread_id else None
            threads = [selected] if selected and selected['project'] == project else []
            jobs = self.store.active_jobs(view_thread_id, project) if threads else []
        if self.owns_desktop_state:
            threads = [t for t in threads if not t['temporary'] or t['id'] in self.temporary_threads]
        return {'settings': self.settings(), 'projects': self.store.list('project'),
                'threads': [answer_versions.project(t) for t in threads],
                'memories': [r for r in self.store.list('memory') if r['active'] and (r['scope'] == 'user' or r['project'] == project)],
                'artifacts': [dict(a, material_layer=material_layer(a), source_content_hash=source_document_hash(a))
                              for a in self.store.list('artifact', project)],
                'feedback': self.store.list('feedback', project), 'jobs': jobs, 'model_options': self.catalog(),
                'embedding_options': self.embedding_catalog(), 'research_topic': self.topic.get(project)}

    def settings_save(self, config, expected_revision, thread_id=None, thread_revision=None):
        """Save research settings from the window; with thread_id, also set that conversation's model."""
        if not isinstance(config, dict) or set(config) - SETTINGS_FIELDS:
            raise ValueError('SETTINGS_FIELDS_INVALID')
        current = self.settings()
        value = {**current, **config}
        if type(value['memory_enabled']) is not bool or type(value['use_recent_interests']) is not bool:
            raise ValueError('SETTINGS_INVALID')
        if type(value['context_chars']) is not int or not 8000 <= value['context_chars'] <= 64000:
            raise ValueError('CONTEXT_BUDGET_INVALID')
        if type(value['recent_messages']) is not int or not 2 <= value['recent_messages'] <= 24:
            raise ValueError('RECENT_MESSAGES_INVALID')
        validate_agent_settings(value)
        agent_ref = value.get('agent_profile_ref')
        if not desktop_agent(value) and agent_ref and not any(
                o['profile_ref'] == agent_ref and o['eligible'] and o['profile_kind'] == 'CLI' and agent_matches(o, value['agent'])
                for o in self.catalog()):
            raise ValueError('AGENT_MODEL_UNAVAILABLE')
        ref = value['profile_ref']
        if ref is not None and not any(o['profile_ref'] == ref and o['eligible'] and o['profile_kind'] in {'API', 'LOCAL', 'CLI'}
                                       for o in self.catalog()):
            raise ValueError('CHAT_MODEL_UNAVAILABLE')
        with self.store.tx() as db:
            # Checked before interest_set below changes the settings service, not only at the final put.
            if self.store.get('settings', 'default', db=db)['revision'] != expected_revision:
                raise ValueError('REVISION_CONFLICT')
            thread = None
            if thread_id is not None:
                if 'profile_ref' not in config:
                    raise ValueError('THREAD_CONFIG_INVALID')
                thread = self.conversations._get(thread_id, thread_revision, db)
                if thread.get('archived'):
                    raise ValueError('THREAD_ARCHIVED')
            if self.interest_set is not None and value['use_recent_interests'] != current['use_recent_interests']:
                self.interest_set(value['use_recent_interests'])
            if thread is not None:
                self.store.put('thread', thread_id, thread['project'], dict(thread, profile_ref=ref), expected=thread_revision, db=db)
            return self.store.put('settings', 'default', '', value, expected=expected_revision, db=db)

    def project_save(self, name, vault=None, project=None, expected_revision=0):
        """Create or rename a project; vault is its own folder of notes (see EvidenceIndex.refresh)."""
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise ValueError('PROJECT_NAME_INVALID')
        identity = project or uid('project_')
        old = self.store.get('project', identity)
        if vault:
            folder = Path(vault).resolve(strict=True)
            if not folder.is_dir() or folder.parent == folder:
                raise ValueError('VAULT_INVALID')
            vault = str(folder)
        value = {'name': name.strip(), 'vault': vault, 'index_epoch': (old or {}).get('index_epoch', 0)}
        with self.store.tx() as db:
            result = self.store.put('project', identity, identity, value, expected=expected_revision, db=db)
            if old and old.get('vault') != vault:  # notes of the previous folder are no longer material
                for artifact in self.store.list('artifact', identity, db=db):
                    if artifact['kind'] == 'source':
                        self.store.put('artifact', artifact['id'], identity, dict(artifact, state='unavailable'), db=db)
            return result

    def thread_create(self, project='default', title='', temporary=False):
        if not self.store.get('project', project):
            raise ValueError('PROJECT_NOT_FOUND')
        if type(temporary) is not bool:
            raise ValueError('THREAD_INVALID')
        title = str(title)[:120] or choose(freeze_language(self.language_settings_get()), '新对话', 'New conversation', '新しい会話')
        result = self.store.put('thread', uid('chat_'), project, {
            'project': project, 'title': title, 'temporary': temporary, 'created_at': now(), 'messages': [], 'summary': [],
            'summary_ids': [], 'artifact_ids': [], 'scope': 'conversation', 'include_project': False, 'excluded_artifact_ids': []})
        if temporary:
            self.temporary_threads.add(result['id'])
        return answer_versions.project(result)

    def thread_get(self, thread_id):
        """A conversation as the window shows it. A temporary conversation of an earlier session is
        forgotten here."""
        row = self.store.get('thread', thread_id)
        if not row:
            raise ValueError('THREAD_NOT_FOUND')
        if row['temporary'] and self.owns_desktop_state and thread_id not in self.temporary_threads:
            self.thread_forget(thread_id, row['revision'])
            raise ValueError('THREAD_NOT_FOUND')
        return answer_versions.project(row)

    def thread_forget(self, thread_id, expected_revision):
        """Delete a conversation with its jobs and records; its temporary attachments become unavailable."""
        with self.store.tx() as db:
            row = self.store.get('thread', thread_id, db=db)
            if not row or row['revision'] != expected_revision:
                raise ValueError('REVISION_CONFLICT')
            self.conversations._get(thread_id, expected_revision, db)  # refused while the conversation is busy
            for job in self.store.list('job', row['project'], db=db):
                if job.get('thread_id') == thread_id:
                    db.execute("DELETE FROM objects WHERE kind='job' AND id=?", (job['id'],))
            for artifact in self.store.list('artifact', row['project'], db=db):
                if artifact.get('temporary_thread') == thread_id:
                    db.execute('DELETE FROM chunks WHERE artifact_id=?', (artifact['id'],))
                    self.store.put('artifact', artifact['id'], row['project'], dict(artifact, state='unavailable'), db=db)
            db.execute("DELETE FROM objects WHERE kind='thread' AND id=?", (thread_id,))
            self.topic.forget_thread(thread_id, row['project'], db)
            for kind in THREAD_RECORDS:
                for record in self.store.list(kind, row['project'], db=db):
                    if record.get('thread_id') == thread_id:
                        db.execute('DELETE FROM objects WHERE kind=? AND id=?', (kind, record['id']))
            self.store.event('THREAD_FORGOTTEN', thread_id, {}, db)
        return {'status': 'FORGOTTEN', 'removed_from_future_context': True}

    def memory_save(self, text, scope='project', project='default', memory_id=None, expected_revision=0):
        if scope not in {'user', 'project'} or not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('MEMORY_INVALID')
        if not self.store.get('project', project):
            raise ValueError('PROJECT_NOT_FOUND')
        return self.store.put('memory', memory_id or uid('mem_'), project, {
            'text': text.strip(), 'scope': scope, 'project': project, 'active': True, 'confirmed_by': 'user',
            'kind': 'preference_or_project_context', 'created_at': now()}, expected=expected_revision)

    def memory_forget(self, memory_id, expected_revision):
        with self.store.tx() as db:
            memory = self.store.get('memory', memory_id, db=db)
            if not memory or memory['revision'] != expected_revision:
                raise ValueError('REVISION_CONFLICT')
            # Tombstones contain no previous content; worker context is rechecked before publication.
            self.store.put('memory', memory_id, memory['project'], dict(memory, text='', active=False), expected=expected_revision, db=db)
            self.store.event('MEMORY_FORGOTTEN', memory_id, {}, db)
        return {'status': 'FORGOTTEN'}

    def interests(self, project):
        """The recent questions that weigh in search (memo.interests), each with its decay."""
        from memorive.research.ranking.research_interests import snapshot
        config = self.weights.settings(project)['config']
        value = snapshot(self.store, enabled=self.settings()['use_recent_interests'], project=project,
                         window_days=config['interest_days'], half_life=config['interest_half_life'])
        items = []
        with self.store.tx() as db:
            for event in value['events']:
                thread = self.store.get('thread', event['thread_id'], db=db)
                text = next((m['text'] for m in (thread or {}).get('messages', [])
                             if m['role'] == 'user' and digest(m['text']) == event['query_hash']), '')
                if text:
                    items.append({'id': str(event['event_id']), 'label': text[:90], 'decay': event['decay']})
        return {'items': items, 'enabled': value['enabled']}

    def interest_remove(self, project, identity):
        with self.store.tx() as db:
            row = db.execute("SELECT body FROM events WHERE seq=? AND event='EXPLICIT_RESEARCH_INTEREST'", (identity,)).fetchone()
            if not row or json.loads(row['body'])['project'] != project:
                raise ValueError('INTEREST_NOT_FOUND')
            self.store.put('interest_exclusion', str(identity), project, {'project': project}, db=db)
        return {'status': 'REMOVED'}

    def knowledge_health(self, project):
        from memorive.research.workspace.knowledge_health import health
        return health(self, project)

    def feedback_revise(self, identity, expected_revision, changes, evidence_ids=None):
        from memorive.research.workspace.knowledge_health import revise
        return revise(self, identity, expected_revision, changes, evidence_ids)

    def agent_dispatch(self, thread_id, question, request_id, profile_ref=None):
        """Ask the chosen command-line agent instead of the chat model (memo.agent_dispatch)."""
        settings = self.settings()
        agent = settings['agent']
        options = [o['profile_ref'] for o in self.catalog()
                   if o['profile_kind'] == 'CLI' and o['eligible'] and agent_matches(o, agent)]
        # Without a chosen model, Codex and Claude Code run on their first connected command line.
        ref = profile_ref or settings.get('agent_profile_ref') or (options[0] if options and agent in CLI_ADAPTERS else None)
        if ref not in options:
            raise ValueError('AGENT_MODEL_UNAVAILABLE')
        if not self.agent_model:
            raise ValueError('AGENT_RUNNER_UNAVAILABLE')
        return self.ask(thread_id, question, request_id, agent_profile_ref=ref)

    def weight_preview(self, project, query, config):
        """Search results with the saved and with proposed ranking weights; nothing is saved."""
        from memorive.research.ranking.research_policy import validate
        proposed = validate(config)
        # Preview never invokes a model or changes the saved policy.
        index = EvidenceIndex(self.store)
        index.profile_identity = self.index.profile_identity
        before = index.search(query, project, limit=8, record=False)
        after = index.search(query, project, limit=8, policy_config=proposed, record=False)
        return {'before': before, 'after': after, 'saved': False}

    def prepare_tension(self, project, evidence_ids):
        """A pack for comparing excerpts of at least two papers (memo.prepare_tension)."""
        from memorive.research.opportunities.core import CONCEPT_ORDER
        if not isinstance(evidence_ids, list) or not 2 <= len(set(evidence_ids)) <= 12:
            raise ValueError('TENSION_NEEDS_TWO_SOURCES')
        refs = [self.index.read(i, project) for i in evidence_ids]
        if len({answer_coverage.document_key(r) for r in refs}) < 2:
            raise ValueError('TENSION_NEEDS_TWO_PAPERS')
        return {'schema_version': 'DesktopAgentTensionPack-v1', 'project': project, 'evidence': refs,
                'comparison_facets': list(CONCEPT_ORDER), 'classification': 'NOT_ASSESSED',
                'instructions': 'Compare each facet with quoted evidence. Distinguish incomparable conditions from conflicting findings. Return a proposed interpretation with limitations; never declare novelty from missing search results.',
                'source_snapshot_hash': digest([{k: r[k] for k in ('id', 'artifact_id', 'content_hash', 'chunk_hash')} for r in refs])}

    def ask(self, thread_id, question, request_id, artifact_ids=None, agent_profile_ref=None, path_token=None, output_language=None):
        """A question from the window (memo.ask): queued as a job, see AnswerVersions.enqueue."""
        return self.versions.enqueue(thread_id, question, request_id, artifact_ids, agent_profile_ref,
                                     path_token_expected=path_token, output_language=output_language)

    def _answer(self, identity, question, artifact_ids):
        """Answer a queued question; the worker pool runs this. The steps are in answering.py."""
        from memorive.research.workspace.answering import answer_job
        answer_job(self, identity, question, artifact_ids)

    def job_status(self, job_id):
        row = self.store.get('job', job_id)
        if not row:
            raise ValueError('JOB_NOT_FOUND')
        return row

    def cancel(self, job_id):
        """Cancel a queued or running question at once (memo.cancel). A model call already running is
        not stopped; its answer is discarded (answering.py)."""
        with self.store.tx() as db:
            job = self.store.get('job', job_id, db=db)
            if not job:
                raise ValueError('JOB_NOT_FOUND')
            if job['status'] not in {'QUEUED', 'RUNNING'}:
                return job
            return self.store.put('job', job_id, job['project'],
                                  dict(job, cancel_requested=True, status="CANCELLED", result_will_be_discarded=True), db=db)

    def handoff(self, thread_id, question='', evidence_ids=None, agent=None):
        return self.handoffs.create(thread_id, question, evidence_ids, agent)

    def export_handoff(self, handoff_id):
        """Write a hand-off pack as files for an agent to read (memo.export_handoff)."""
        from memorive.research.workspace.skill_bridge import transport
        with self.store.tx() as db:
            value = self.handoffs.get(handoff_id, db)
            self.handoffs.validate(value, db)
            root = self.store.root / 'handoffs' / handoff_id
            root.mkdir(parents=True, exist_ok=True)
            language = value.get('language_context')
            text = '# ' + choose(language, 'Memo 研究交接', 'Memo research handoff', 'Memo 研究の引き継ぎ') + '\n\n'
            text += value['question'] + '\n\n' + value['instructions'] + '\n\n'
            text += (choose(language, '回传绑定：', 'Return binding: ', '返却時の識別情報：')
                     + 'handoff_id=' + value['id'] + '; handoff_hash=' + value['content_hash'] + '\n\n')
            for ref in value['evidence']:
                text += ('## ' + ref['id'] + ' · ' + ref['title'] + '\n\n' + ref['text'] + '\n\n'
                         + choose(language, '来源哈希：', 'Source hash: ', '出典のハッシュ：') + ref['content_hash'] + '\n\n')
            text += ('## ' + choose(language, '会话上下文', 'Conversation context', '会話のコンテキスト') + '\n\n'
                     + '\n\n'.join(m['role'] + ': ' + m['text'] for m in value['conversation_summary']))
            text += ('\n\nUse the memo-research skill: python <skill>/scripts/memo_bridge.py read handoff.json, draft with '
                     '[E3: "quote"] marks, then answer handoff.json --draft <draft> --return. Without the skill, the local '
                     'transport command is in transport.json; append --call <method> and send JSON on stdin.\n')
            for name, data in [('handoff.json', packed(value)), ('START_HERE.md', text), ('transport.json', packed(transport(self)))]:
                path = root / name
                # Exported before: the files on disk must still be this pack.
                if path.exists() and path.read_text(encoding='utf-8') != data:
                    raise ValueError('HANDOFF_EXPORT_CONFLICT')
                if not path.exists():
                    path.write_text(data, encoding='utf-8')
        return {'status': 'EXPORTED', 'path': str(root / 'START_HERE.md'), 'external_transmission': False}

    def submit_draft(self, project, title, claim, scope, limitations, evidence_ids, handoff_id=None, handoff_hash=None, request_id=None):
        """A knowledge draft from an agent; one that answers a hand-off must name it completely."""
        fields = dict(project=project, title=title, claim=claim, scope=scope, limitations=limitations, evidence_ids=evidence_ids)
        if any(v is not None for v in (handoff_id, handoff_hash, request_id)):
            if not all(v is not None for v in (handoff_id, handoff_hash, request_id)):
                raise ValueError('HANDOFF_BINDING_REQUIRED')
            return self.handoffs.submit(fields, handoff_id=handoff_id, handoff_hash=handoff_hash, request_id=request_id)
        return self.feedback.propose(**fields, origin='external_agent')

    def call(self, method, params=None, *, desktop=False):
        """Run a memo.* method for the window (desktop=True) or an external agent."""
        if method not in (DESKTOP_METHODS if desktop else PUBLIC_METHODS):
            raise ValueError('METHOD_NOT_ALLOWED')
        if desktop and method != 'memo.thread_view':
            self.conversations.foreground_until = time.monotonic() + .5
        p = dict(params or {})
        # Methods owned by another module go there.
        if method in answer_versions.METHODS:
            return self.versions.call(method, p)
        if method in ANSWER_EVIDENCE_METHODS:
            return self.answer_evidence.call(method, p)
        if method in ANSWER_SAVE_METHODS:
            return self.saves.call(method, p)
        if method in DEVELOPER_METHODS:
            return self.developer.desktop(method, p)
        if method in CONVERSATION_METHODS:
            return self.conversations.call(method, p)
        if method in ATTACHMENT_METHODS:
            return self.attachments.call(method, p)
        if method in CONNECTION_METHODS:
            return self.connections.desktop(method, p)
        if method == 'memo.skill_context':
            return self.skill.context(**p)
        if method == 'memo.skill_answer':
            return self.skill.answer(**p)
        if method in {'memo.library_list', 'memo.library_add'}:
            # Bound by the desktop (library_bridge.py); a workspace without the library has none.
            handler = getattr(self, method.split('.')[1], None)
            if handler is None:
                raise ValueError('DESKTOP_LIBRARY_REQUIRED')
            return handler(**p)
        if method == 'memo.capabilities':
            from memorive.research.workspace.agent_protocol import SCHEMAS
            return {'schema_version': 'MemoCapabilities-v1', 'tools': sorted(PUBLIC_METHODS), 'methods': SCHEMAS,
                    'search': 'shared weighted source-bound; configured model calls and metadata lookup may occur',
                    'approval': 'trusted desktop only', 'agent_transmission': 'explicit handoff',
                    'projects': [{'id': r['id'], 'name': r['name']} for r in self.store.list('project')]}
        if method == 'memo.propose_feedback' and desktop:
            return self.feedback.propose(**p, origin='desktop')
        routes = {
            'memo.state': self.state, 'memo.settings_save': self.settings_save, 'memo.project_save': self.project_save,
            'memo.index_refresh': self.index.refresh, 'memo.topic_change': self.topic.change,
            'memo.thread_create': self.thread_create, 'memo.thread_get': self.thread_get, 'memo.thread_forget': self.thread_forget,
            'memo.ask': self.ask, 'memo.cancel': self.cancel, 'memo.job_status': self.job_status,
            'memo.agent_dispatch': self.agent_dispatch, 'memo.memory_save': self.memory_save, 'memo.memory_forget': self.memory_forget,
            'memo.search': self.index.search, 'memo.read_evidence': self.index.read, 'memo.prepare_tension': self.prepare_tension,
            'memo.knowledge_health': self.knowledge_health, 'memo.feedback_revise': self.feedback_revise,
            'memo.propose_feedback': self.submit_draft, 'memo.submit_draft': self.submit_draft,
            'memo.feedback_review': self.feedback.review, 'memo.feedback_retract': self.feedback.retract,
            'memo.interests': self.interests, 'memo.interest_remove': self.interest_remove,
            'memo.weight_settings': self.weights.settings, 'memo.weight_save': self.weights.save,
            'memo.weight_preview': self.weight_preview, 'memo.material_metadata': self.weights.metadata,
            'memo.material_metadata_save': self.weights.metadata_save,
            'memo.handoff': self.handoff, 'memo.export_handoff': self.export_handoff}
        return routes[method](**p)

    def close(self):
        self.connector_stop.set()
        with self.lock:
            self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.conversations.close_views()
        # Temporary threads are persisted only while the app is running; remove at close.
        with self.store.read() as db:
            temporary = [(r['id'], r['source_revision']) for r in db.execute('SELECT id,source_revision FROM conversation_catalog WHERE temporary=1')]
        for identity, revision in temporary:
            if self.owns_desktop_state or identity in self.temporary_threads:
                try:
                    self.thread_forget(identity, revision)
                except ValueError:
                    pass
