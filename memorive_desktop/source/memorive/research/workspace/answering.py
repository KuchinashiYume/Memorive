"""How a queued research question is answered, step by step.

Where a question goes:

  ResearchWorkspace.ask()        service.py          the window's "ask" arrives here
  AnswerVersions.enqueue()       answer_versions.py  checks the request; freezes the model choice, answer
                                                     style, memories and research topic in a QUEUED job;
                                                     hands the job to the workspace's worker pool
  answer_job()                   this file           what the worker runs, in this order:
    AnswerJob.start                  the job becomes RUNNING, unless it was cancelled or the workspace is closing
    AnswerJob.gather_evidence        search the selected material and build the model context; each source file
                                     is hashed once while doing so (evidence.checking_each_file_once)
      or reuse_evidence_of_retried_answer: answering again uses the evidence frozen with that answer
    AnswerJob.build_request          bind the answer schema to the excerpt ids, check that the context still
                                     fits and is unchanged, build the prompt
    AnswerJob.ask_model              call the model function; with no model chosen, list the excerpts instead
    AnswerJob.save_answer            in one transaction: the conversation, the answer version, a knowledge
                                     draft, the research progress, the interest signal; the job becomes COMPLETE
    AnswerJob.record_failure         any error ends the job as ERROR (CANCELLED when it was cancelled)

The model function is given to the workspace by desktop.py: it freezes the chosen model profile in the
settings service and runs it there (settings.research_chat_freeze / settings.research_chat_execute).

Cancelling: ResearchWorkspace.cancel() marks the job CANCELLED at once. The checkpoints between the steps
stop a cancelled job. A model call that is already running is not stopped; save_answer discards what it
returns and keeps its usage on the job as late_usage.
Restart: a workspace opened with recover=True marks QUEUED and RUNNING jobs INTERRUPTED. A job whose model call
had finished is then answered again through these same steps with the result the settings service kept, in place
of the call (ResearchWorkspace.finish_answers_received_before_restart); nothing is sent again. It is answered from
the evidence and prompt it was sent with (sent_request, kept on the job before the call), so the saved answer
records what the model was actually given. A job from before sent_request was kept searches its excerpts again.
"""
from __future__ import annotations

import copy
import re
import time

from memorive.platform.language import freeze as freeze_language
from memorive.platform.language.text import choose

from memorive.research.workspace import answer_coverage, answer_versions
from memorive.research.workspace.evidence import checking_each_file_once
from memorive.research.workspace.store import digest, now, packed, uid

# What the question asks for. A comparison may draw on one paper (methods or results inside it);
# two papers are required only when the question explicitly asks across documents.
COMPARISON = re.compile(r'比较|对比|异同|差异|张力|矛盾|冲突|compare|comparison|differ|tension|conflict', re.I)
CROSS_DOCUMENT = re.compile(r'跨篇|跨文献|不同文献|两篇|多篇|这些研究|这些文献|这些论文|between\s+(?:the\s+)?(?:papers|documents|studies)'
                            r'|across\s+(?:papers|documents|studies)', re.I)
KNOWLEDGE = re.compile(r'回流|保存.*知识|整理.*结论|save.*knowledge', re.I)
# Discussion depth is not a reason to hand off; only a concrete request for external or long-running work is.
HANDOFF = re.compile(r'^(?:请|帮我)?(?:交给|转交).{0,16}(?:Agent|智能体)|^(?:请|帮我)?(?:持续|自动|跨应用|跨仓库).{0,24}(?:执行|运行|抓取|监控)'
                     r'|^(?:please\s+)?(?:hand off|delegate).{0,24}agent|^(?:please\s+)?(?:continuously|autonomously).{0,24}(?:run|execute|monitor)',
                     re.I)
LIVE = {'QUEUED', 'RUNNING'}


def answer_job(workspace, job_id, question, artifact_ids, received=None):
    """Answer one queued question; the workspace's worker pool runs this."""
    AnswerJob(workspace, job_id, question, artifact_ids, received).run()


class AnswerJob:
    """One research question being answered. Each step keeps what later steps need, listed below."""

    def __init__(self, workspace, job_id, question, artifact_ids, received=None):
        self.ws = workspace
        self.store = workspace.store
        self.job_id = job_id
        self.question = question
        self.artifact_ids = artifact_ids
        # After a restart: (model snapshot, the result the settings service kept for it), used in place
        # of the model call (ResearchWorkspace.finish_answers_received_before_restart).
        self.received = received
        self.started = time.monotonic()
        # Set by start:
        self.job = None              # the job record, now RUNNING
        self.thread = None           # the conversation the answer goes to
        self.settings = None         # model settings frozen with the question
        self.request_context = None  # language, answer style and answer path, frozen with the question
        # Set by gather_evidence, or by reuse_evidence_of_retried_answer when answering again:
        self.search = {}             # the index search, with its ranking receipts
        self.intent = None           # 'question', 'comparison' or 'knowledge' (question_intent)
        self.context = None          # everything the model is shown (model_context)
        self.summary = None          # older conversation turns, shortened
        self.refs = None             # the excerpts in the context
        self.admitted = None         # their ids: an answer may cite only these
        self.coverage_snapshot = None  # how far the excerpts cover the question; saved with the job
        self.evidence_context = None   # question, coverage, excerpts kept and read; saved with the answer
        # Set by build_request:
        self.response_schema = None  # the answer format, bound to the excerpt ids
        self.prompt = None           # what the model is sent
        # Set by ask_model and check_answer:
        self.value = None            # the model's reply as returned
        self.text = None             # the answer text that is saved
        self.citations = None        # ids of the excerpts the answer cites
        self.engine = None           # the model's name, or 'evidence_only' without a model
        self.handoff = None          # whether the question asks to hand the work to an agent
        self.usage = {}              # what the model call used; kept even when the job fails

    def elapsed_ms(self):
        return round((time.monotonic() - self.started) * 1000)

    def checkpoint(self):
        """Stop here when the job was cancelled or removed, or the workspace is closing."""
        row = self.store.get('job', self.job_id)
        if self.ws.closed or not row or row.get('cancel_requested'):
            raise ValueError('RESEARCH_CANCELLED')

    def run(self):
        try:
            from memorive.research.workspace.answer_conversation import run_if_requested
            # A request to work on an earlier conversation is answered by answer_conversation instead.
            if not self.ws.job_status(self.job_id).get('retry_of') and run_if_requested(self.ws, self.job_id, self.question):
                return
            from memorive.models.scheduling import TASK_CATEGORIES
            with TASK_CATEGORIES.enter('RESEARCH_CHAT', checkpoint=self.checkpoint):
                if not self.start():
                    return
                if self.job.get('retry_snapshot'):
                    self.reuse_evidence_of_retried_answer()
                elif self.received and self.job.get('sent_request'):
                    self.reuse_evidence(self.job['sent_request'])
                else:
                    with checking_each_file_once():  # a source file is hashed once, not once per excerpt
                        self.gather_evidence()
                self.build_request()
                self.ask_model()
                self.save_answer()
        except BaseException as exc:  # noqa: BLE001 - every failure ends the job
            self.record_failure(exc)

    # --- 1. start ---------------------------------------------------------------------------------

    def start(self):
        """Mark the job RUNNING. False when it is gone, cancelled, already finished or the workspace closes."""
        if not self.store.get('job', self.job_id):
            return False
        with self.store.tx() as db:
            job = self.store.get('job', self.job_id, db=db)
            if self.ws.closed or not job or job['status'] not in LIVE or job.get('cancel_requested'):
                return False
            self.job = self.store.put('job', self.job_id, job['project'], dict(job, status='RUNNING'), db=db)
        self.thread = self.ws.versions.generation_thread(self.job)
        self.settings = dict(self.job.get('model_settings') or self.ws.settings())
        if 'memory_snapshot' in self.job:
            self.settings['_memory_snapshot'] = self.job['memory_snapshot']
        # Frozen with the question; it is part of the model context and of the saved answer.
        self.request_context = {
            'language_context': copy.deepcopy(self.job.get('language_context') or freeze_language()),
            'answer_style': copy.deepcopy(self.job['answer_style']),
            '_thread_scope': digest(self.thread['id']),
            '_answer_path': {'ids': [m['id'] for m in self.job.get('parent_path', [])],
                             'hash': self.job.get('parent_path_hash')},
        }
        return True

    # --- 2. evidence ------------------------------------------------------------------------------

    def reuse_evidence_of_retried_answer(self):
        """Answering again reuses the evidence and context frozen with the earlier answer."""
        prior = self.job['retry_snapshot']
        self.reuse_evidence(prior)
        self.ws.versions.validate_retry_topic(self.job, prior, self.context)

    def reuse_evidence(self, prior):
        """Take the evidence and context of an earlier request: the earlier answer's when answering again,
        or this job's own sent_request when a result received before a restart is saved."""
        self.context = copy.deepcopy(prior['context'])
        self.summary = copy.deepcopy(prior['summary'])
        self.refs = copy.deepcopy(prior['refs'])
        self.search = copy.deepcopy(prior['search'])
        self.intent = prior['intent']
        self.coverage_snapshot = copy.deepcopy(prior['coverage_snapshot'])
        self.evidence_context = copy.deepcopy(prior['evidence_context'])
        self.admitted = {r['id'] for r in self.refs}
        self.context.update(self.request_context, task_intent=self.intent)

    def gather_evidence(self):
        """Search the selected material, choose the excerpts and build the model context."""
        question, project, artifact_ids = self.question, self.thread['project'], self.artifact_ids
        coverage_plan = answer_coverage.plan(question, artifact_ids)
        unavailable = self.ws.index.unavailable_derived(project, artifact_ids)
        if unavailable:
            coverage_plan['unavailable_sources'] = [dict(artifact_id=aid, **value) for aid, value in sorted(unavailable.items())]
        coverage_plan['project'] = project
        coverage_plan['hash'] = digest({k: v for k, v in coverage_plan.items() if k != 'hash'})
        self.search = self.ws.index.search(question, project, limit=8, artifact_ids=artifact_ids,
                                           policy_config=(self.job.get('weight_policy') or {}).get('config'))
        refs = answer_coverage.local_candidates(self.ws.index, artifact_ids, self.search['results'], coverage_plan)
        retrieved_refs = list(refs)
        if not refs:
            refs.extend(self.citations_of_previous_answer())
        read_refs = list(refs)
        if artifact_ids:
            read_refs = self.pages_of_selected_documents(refs, unavailable)
            # Excerpts already read stay eligible until the context's character budget is applied.
            refs = answer_coverage.pack_order(read_refs, question)
        if unavailable and not refs:
            coverage = answer_coverage.snapshot(self.store, coverage_plan, retrieved_refs, read_refs, refs)
            self.store.put('job', self.job_id, self.job['project'],
                           dict(self.ws.job_status(self.job_id), evidence_coverage=coverage))
            raise ValueError('EVIDENCE_NO_CURRENT_SOURCE')
        cross_document = bool(CROSS_DOCUMENT.search(question))
        self.intent = self.question_intent()
        if cross_document and len({answer_coverage.document_key(r) for r in refs}) < 2:
            raise ValueError('TENSION_NEEDS_TWO_PAPERS')
        task_intent = 'agent' if self.job.get('kind') == 'agent' else self.intent
        self.context, self.summary = self.model_context(refs, coverage_plan, dict(self.request_context, task_intent=task_intent))
        self.admitted = {e['id'] for e in self.context['evidence']}
        refs = [r for r in refs if r['id'] in self.admitted]
        self.coverage_snapshot = answer_coverage.snapshot(self.store, coverage_plan, retrieved_refs, read_refs, refs)
        self.evidence_context = {'question': question, 'coverage': self.coverage_snapshot, 'retained': refs, 'read': read_refs}
        self.refs = refs
        self.checkpoint()
        # The context budget may have dropped one of the papers the question compares.
        if cross_document and len({answer_coverage.document_key(r) for r in refs}) < 2:
            raise ValueError('COMPARISON_CONTEXT_TOO_SMALL')

    def question_intent(self):
        """What the question asks for: a comparison, knowledge to keep, or an answer."""
        if COMPARISON.search(self.question):
            return 'comparison'
        return 'knowledge' if KNOWLEDGE.search(self.question) else 'question'

    def citations_of_previous_answer(self):
        """A short follow-up may omit the topic: reuse the still-current citations of the last answer."""
        found = []
        previous = next((m for m in reversed(self.thread['messages']) if m['role'] == 'assistant' and m.get('citations')), None)
        for citation in (previous or {}).get('citations', [])[:8]:
            if self.artifact_ids is not None and citation['artifact_id'] not in self.artifact_ids:
                continue
            try:
                ref = self.ws.index.read(citation['id'], self.thread['project'], expected_hash=citation['content_hash'])
            except (ValueError, OSError):
                continue
            found.append(dict(ref, selection='previous_turn'))
        return found

    def pages_of_selected_documents(self, refs, unavailable):
        """Explicitly selected documents are covered page by page, even when few excerpts matched the
        question (a Chinese question about an English PDF). Excerpts alternate between documents, so
        that trimming the tail to the budget keeps every document."""
        per_document = []
        with self.store.tx() as db:
            for aid in self.artifact_ids:
                if aid in unavailable:
                    continue
                rows = db.execute('SELECT id,page FROM chunks WHERE artifact_id=? ORDER BY page,line_start,id', (aid,)).fetchall()
                chosen = [r for r in refs if r['artifact_id'] == aid][:2]
                seen = {r['id'] for r in chosen}
                pages = {r.get('page') for r in chosen}
                for row in rows:  # first one excerpt from every page not yet covered
                    if row['id'] not in seen and row['page'] not in pages:
                        chosen.append(dict(self.ws.index.read(row['id'], self.thread['project'], db=db), selection='selected_document_page'))
                        seen.add(row['id'])
                        pages.add(row['page'])
                for row in rows:  # then the rest of the document
                    if row['id'] not in seen:
                        chosen.append(dict(self.ws.index.read(row['id'], self.thread['project'], db=db), selection='selected_document_context'))
                        seen.add(row['id'])
                per_document.append(chosen)
        balanced = []
        for index in range(max((len(rows) for rows in per_document), default=0)):
            for rows in per_document:
                if index < len(rows):
                    balanced.append(rows[index])
        return balanced

    def model_context(self, refs, coverage_plan, request_context):
        """The context the model sees: memories, conversation history, research topic and excerpts,
        trimmed to the configured character budget (oldest history first, then memories, then excerpts)."""
        ws, thread, settings, question = self.ws, self.thread, self.settings, self.question
        memories = [] if thread['temporary'] or not settings['memory_enabled'] else [
            m for m in ws.store.list('memory') if m['active'] and (m['scope'] == 'user' or m['project'] == thread['project'])]
        if '_memory_snapshot' in settings:
            memories = settings['_memory_snapshot']
        selected = coverage_plan.get('scope_ids', ws.attachments.scope_ids(thread))
        topic_snapshot = self.job.get('topic_snapshot') or ws.topic.snapshot(thread, selected)
        messages, withheld_history = [], []
        for message in thread['messages']:
            if message['id'] in topic_snapshot.get('withheld_message_ids', []):
                withheld_history.append(message['id'])
                continue
            try:  # history that cites material outside the scope, or changed since, is left out
                for citation in message.get('citations', []):
                    if selected is not None and citation['artifact_id'] not in selected:
                        raise ValueError('HISTORY_OUTSIDE_SCOPE')
                    ws.index.read(citation['id'], thread['project'], expected_hash=citation['content_hash'])
            except (ValueError, OSError):
                withheld_history.append(message['id'])
                continue
            messages.append(message)
        keep = settings['recent_messages']
        older = messages[:-keep] if len(messages) > keep else []
        summary = [{'message_id': m['id'], 'role': m['role'], 'excerpt': m['text'][:300]} for m in older[-16:]]
        excerpt_fields = ('id', 'artifact_id', 'document_id', 'title', 'text', 'content_hash', 'page', 'line_start', 'line_end')
        optional_fields = ('material_layer', 'source_content_hash', 'knowledge_review', 'structure_refs', 'line_basis', 'source_warnings')
        payload = {
            '_thread_scope': digest(thread['id']), '_temporary': thread['temporary'],
            'confirmed_memory': [{'id': m['id'], 'revision': m['revision'], 'text': m['text']} for m in memories[:12]],
            'extractive_summary': summary,
            'recent_messages': [{'message_id': m['id'], 'role': m['role'], 'text': m['text']} for m in messages[-keep:]],
            'evidence': [{**{k: r.get(k) for k in excerpt_fields}, **{k: r[k] for k in optional_fields if k in r}} for r in refs],
            'question': question,
            'withheld_history_ids': withheld_history,
        }
        # The whole request counts against the budget, before the topic is allocated and anything trimmed.
        payload.update(copy.deepcopy(request_context))
        budget = settings['context_chars']
        payload['source_scope'] = {
            'artifact_ids': coverage_plan['scope_ids'], 'facets': [f['label'] for f in coverage_plan['facets']],
            'notice': 'Only retained excerpts below were supplied. Missing excerpts or Card fields are not absence in the '
                      'source. Facet sufficiency remains unassessed.'}
        if coverage_plan.get('unavailable_sources'):
            payload['source_scope']['unavailable_sources'] = coverage_plan['unavailable_sources']
            payload['source_scope']['notice'] += (' Excluded knowledge has unavailable source versions; its saved review state '
                                                  'is unchanged. Do not treat it as current evidence or infer scientific rejection.')
        # The research topic gets what the rest leaves of the budget; optional model notes at most a third.
        structural = dict(payload, evidence=[], confirmed_memory=[], extractive_summary=[], recent_messages=[], research_topic={})
        structural['source_scope'] = dict(payload['source_scope'], retained_artifact_ids=[], missing_artifact_ids=coverage_plan['scope_ids'])
        topic_budget = max(0, budget - len(packed(structural)) + 2)
        payload['research_topic'] = ws.topic.context(topic_snapshot, topic_budget, optional_limit=budget // 3)

        def size():
            retained = list(dict.fromkeys(r['artifact_id'] for r in payload['evidence']))
            missing = [a for a in coverage_plan['scope_ids'] if a not in retained] if coverage_plan['scope_ids'] is not None else None
            payload['source_scope'].update(retained_artifact_ids=retained, missing_artifact_ids=missing)
            return len(packed(payload))

        # Drop the least important context first; the question itself is never cut or summarized.
        while size() > budget and payload['recent_messages']:
            payload['recent_messages'].pop(0)
        while size() > budget and payload['extractive_summary']:
            payload['extractive_summary'].pop(0)
        while size() > budget and payload['confirmed_memory']:
            payload['confirmed_memory'].pop()
        if size() > budget and payload['evidence']:
            from memorive.research.workspace.context_selection import select
            by_id = {r['id']: r for r in payload['evidence']}

            def fits(selected_refs):
                payload['evidence'] = [by_id[r['id']] for r in selected_refs]
                return size() <= budget

            payload['evidence'] = [by_id[r['id']] for r in select(refs, question, fits)]
        if size() > budget:
            raise ValueError('QUESTION_EXCEEDS_CONTEXT_BUDGET')
        return payload, summary

    # --- 3. request -------------------------------------------------------------------------------

    def build_request(self):
        """Bind the answer schema to the excerpts shown, check the context, and build the prompt."""
        from memorive.models.gateway.research_prompt_cache import CHAT_RULES, research_prompt
        from memorive.research.workspace.skill_bridge import METHOD_RULES
        self.response_schema = None
        response_contract = None
        rules = CHAT_RULES
        evidence_ids = [e['id'] for e in self.context['evidence']]
        if self.settings['profile_ref']:
            from memorive.research.workspace.desktop import ANSWER_SCHEMA
            from memorive.research.workspace.response_contract import REVISION, bind_evidence
            base_schema = ANSWER_SCHEMA
            if self.job.get('kind') == 'agent':
                # An agent job answers in the agent's format: its own instructions, then the chat rules
                # (citation marks, language, knowledge draft).
                from memorive.research.opportunities.research_opportunities import instructions, schema
                base_schema = schema()
                rules = instructions() + '\n' + CHAT_RULES
            self.response_schema = bind_evidence(base_schema, evidence_ids)
            response_contract = {'revision': REVISION, 'schema_sha256': digest(self.response_schema), 'evidence_ids': evidence_ids}
        self.store.put('job', self.job_id, self.job['project'], dict(self.ws.job_status(self.job_id),
                       evidence_coverage=self.coverage_snapshot, response_contract=response_contract))
        # A retry's frozen evidence is never trimmed again, for instance after a style change.
        if len(packed(self.context)) > self.settings['context_chars']:
            raise ValueError('RETRY_CONTEXT_EXCEEDS_CONTEXT_BUDGET' if self.job.get('retry_of') else 'QUESTION_EXCEEDS_CONTEXT_BUDGET')
        self.ws.versions.validate_context(self.job, self.context, self.refs)
        self.checkpoint()
        self.prompt = research_prompt(rules + '\n' + METHOD_RULES, self.context)

    # --- 4. model ---------------------------------------------------------------------------------

    def ask_model(self):
        """Call the chosen model, or list the excerpts found when no model is chosen."""
        self.value = {}
        self.engine = 'evidence_only'
        self.citations = [r['id'] for r in self.refs]
        self.handoff = bool(HANDOFF.search(self.question))
        if not self.settings['profile_ref']:
            self.text = self.excerpt_list()
            return
        model = self.ws.agent_model if self.job.get('kind') == 'agent' else self.ws.model
        if not model:
            raise ValueError('CHAT_MODEL_RUNNER_UNAVAILABLE')
        if self.job.get('kind') == 'agent':
            self.save_agent_handoff()
        if self.received:
            from memorive.research.workspace.model_receipts import verified_result  # imported here, as in desktop.py: it loads memorive/desktop/settings
            snapshot, kept = self.received
            result = verified_result(kept, snapshot)  # as the model function checks a result it receives
            if self.job.get('sent_request'):
                self.prompt = self.job['sent_request']['prompt']  # what the model was given, as it was given
        else:
            self.keep_sent_request()
            result = model(profile_ref=self.settings['profile_ref'], prompt=self.prompt, job_id=self.job_id,
                           response_schema=self.response_schema)
        self.value = result['response']
        self.usage = result.get('execution_receipt', {})
        self.engine = result.get('model_name', 'configured_model')
        self.check_answer()

    def keep_sent_request(self):
        """Keep on the job what the model is about to be sent: if the app stops before the answer is saved,
        the result received for this call is saved with this evidence and prompt."""
        sent = {'context': self.context, 'summary': self.summary, 'refs': self.refs, 'search': self.search,
                'intent': self.intent, 'coverage_snapshot': self.coverage_snapshot,
                'evidence_context': self.evidence_context, 'prompt': self.prompt}
        with self.store.tx() as db:
            job = self.store.get('job', self.job_id, db=db)
            if job:
                self.store.put('job', self.job_id, job['project'], dict(job, sent_request=copy.deepcopy(sent)), db=db)

    def check_answer(self):
        """The answer must have text, and cite only excerpts the model was shown."""
        value = self.value
        if not isinstance(value, dict) or not isinstance(value.get('answer'), str) or not isinstance(value.get('citations'), list):
            raise ValueError('CHAT_RESPONSE_INVALID')
        if any(not isinstance(c, str) or c not in self.admitted for c in value['citations']):
            raise ValueError('CHAT_CITATION_NOT_IN_CONTEXT')
        self.text = value['answer']
        self.citations = value['citations']
        if not self.text.strip():
            raise ValueError('CHAT_RESPONSE_EMPTY')

    def excerpt_list(self):
        """The reply without a model: the excerpts found, said in the conversation's language."""
        language = self.request_context['language_context']
        if not self.refs:
            return choose(language,
                          '当前项目中没有找到足够相关的证据。请检查资料范围、刷新索引，或准备材料交给 Agent。',
                          'No sufficiently relevant evidence was found in this project. Check the material scope, '
                          'refresh the index, or prepare materials for an agent.',
                          'このプロジェクトでは十分に関連する証拠が見つかりませんでした。資料の範囲を確認し、索引を更新するか、'
                          'エージェントに渡す資料を用意してください。')
        heading = choose(language,
                         '已找到以下资料片段。当前为本地检索模式，可在输入框选择模型继续提问。\n\n',
                         'The excerpts below were retrieved locally. Select a model in the input area to continue.\n\n',
                         '以下の断片をローカル検索で取得しました。入力欄でモデルを選択して質問を続けられます。\n\n')
        return heading + '\n\n'.join(f'[{i + 1}] {r["title"]}\n{r["text"][:550]}' for i, r in enumerate(self.refs))

    def save_agent_handoff(self):
        """An agent job: save the hand-off pack the agent works from (its prompt is made in build_request)."""
        from memorive.research.opportunities.research_opportunities import instructions
        context, thread = self.context, self.thread
        pack = {
            'schema_version': 'MemoAgentHandoff-v2', 'project': thread['project'], 'thread_id': thread['id'],
            'question': self.question, 'agent': self.settings['agent'], 'evidence': self.refs,
            'conversation_summary': [{'role': m['role'], 'text': m.get('text', m.get('excerpt', ''))}
                                     for m in context['extractive_summary'] + context['recent_messages']],
            'instructions': instructions(), 'language_context': copy.deepcopy(self.request_context['language_context']),
            'expression_preference': self.job['answer_style'],
            'expression_scope': 'User preference only; does not replace receiving agent permissions or task instructions',
            'created_at': now(), 'external_transmission': True, 'artifact_ids': self.artifact_ids,
            'profile_ref': self.settings['profile_ref'],
        }
        pack['content_hash'] = digest(pack)
        self.store.put('handoff', 'handoff_' + self.job_id, thread['project'], pack)
        self.store.put('job', self.job_id, self.job['project'],
                       dict(self.ws.job_status(self.job_id), handoff_id='handoff_' + self.job_id, progress='AGENT_RUNNING'))

    # --- 5. save ----------------------------------------------------------------------------------

    def save_answer(self):
        """Save the answer, unless the job ended or the workspace closed while the model was running."""
        job, thread = self.job, self.thread
        with self.store.tx() as db:
            latest = self.store.get('job', self.job_id, db=db)
            if not latest:
                return
            if self.ws.closed and latest['status'] in LIVE:
                self.store.put('job', self.job_id, job['project'], dict(latest, status='INTERRUPTED', usage=self.usage,
                               error='Workspace closed; result discarded', elapsed_ms=self.elapsed_ms()), db=db)
                return
            if latest['status'] not in LIVE:  # cancelled, or marked interrupted by a restart, meanwhile
                if self.usage:
                    self.store.put('job', self.job_id, job['project'], dict(latest, late_usage=self.usage,
                                   late_result='DISCARDED', elapsed_ms=self.elapsed_ms()), db=db)
                return
            if latest['cancel_requested']:
                self.store.put('job', self.job_id, job['project'],
                               dict(latest, status='CANCELLED', usage=self.usage, elapsed_ms=self.elapsed_ms()), db=db)
                return
            current = self.store.get('thread', thread['id'], db=db)
            if not current:
                return
            if current.get('archived'):
                raise ValueError('THREAD_ARCHIVED')
            # The inputs frozen with the question (model, memories, excerpts) must still hold.
            self.ws.versions.validate_context(job, self.context, self.refs, db=db)
            user = copy.deepcopy(job['question_message'])
            answer = self.answer_message()
            if job.get('kind') == 'agent':
                answer['opportunities'] = self.checked_opportunities(answer, db)
                answer['agent_result'] = True
                answer['needs_agent'] = False
            self.propose_knowledge(answer, db)
            user['artifact_ids'] = self.artifact_ids or []
            topic_result = self.ws.topic.commit_answer(job['topic_snapshot'], self.ws.versions.generation_thread(job, db=db), user,
                                                       answer, self.value.get('research_notes', []), self.artifact_ids, db)
            answer['research_progress'] = topic_result
            answer['context']['research_topic'] = self.context['research_topic']
            answer['context']['history_message_ids'] = [m['message_id'] for m in self.context['extractive_summary'] + self.context['recent_messages']]
            answer['context']['withheld_history_ids'] = self.context['withheld_history_ids']
            answer_versions.append(current, user, answer, selection_revision=job['selection_revision'])
            current['summary'] = []
            current['summary_ids'] = []
            if len(current['messages']) == 2 and not current.get('title_custom'):
                current['title'] = self.question[:70]
            current['updated_at'] = now()
            self.store.put('thread', thread['id'], thread['project'], current, db=db)
            self.store.put('job', self.job_id, job['project'], dict(latest, status='COMPLETE', answer_id=answer['id'],
                           elapsed_ms=answer['elapsed_ms'], usage=self.usage), db=db)
            if self.settings['use_recent_interests'] and not thread['temporary'] and not job.get('retry_of'):
                from memorive.research.ranking.research_interests import term_hashes
                self.store.event('EXPLICIT_RESEARCH_INTEREST', thread['id'], {
                    'query_hash': digest(self.question), 'term_hashes': term_hashes(self.question),
                    'evidence_ids': self.citations, 'project': thread['project']}, db)

    def answer_message(self):
        """The assistant message as saved in the conversation, with how it was generated."""
        job, search, context = self.job, self.search, self.context
        by_evidence_id = {r['id']: r for r in self.refs}
        language = copy.deepcopy(self.request_context['language_context'])
        answer = {
            'id': uid('msg_'), 'role': 'assistant', 'text': self.text, 'created_at': now(),
            'citations': [by_evidence_id[i] for i in self.citations],
            'answer_version': 1, 'language_context': language, 'evidence_context': self.evidence_context,
            'claim_reviews': [], 'review_binding': None, 'engine': self.engine, 'usage': self.usage,
            'ranking_policy_hash': search.get('policy_hash'), 'ranking_receipts': search.get('ranking_receipts', []),
            'ranking_warnings': search.get('warnings', []), 'intent': self.intent, 'artifact_ids': self.artifact_ids or [],
            'elapsed_ms': self.elapsed_ms(), 'needs_agent': self.handoff,
            'context': {'memory_ids': [m['id'] for m in context['confirmed_memory']],
                        'summary_message_ids': [m['message_id'] for m in context['extractive_summary']],
                        'chars': len(packed(context)), 'estimated_input_tokens': (len(self.prompt) + 2) // 3,
                        'token_estimate_only': True},
        }
        # Everything needed to answer again with the same inputs (AnswerVersions.retry).
        answer['generation'] = {
            'schema_version': 'MemoAnswerGeneration-v1', 'job_id': self.job_id, 'request_id': job['request_id'],
            'topic_snapshot': copy.deepcopy((job.get('retry_snapshot') or {}).get('topic_snapshot') or job['topic_snapshot']),
            'language_context': copy.deepcopy(self.request_context['language_context']),
            'topic_commit_binding': job['topic_snapshot']['binding'], 'style': copy.deepcopy(job['answer_style']),
            'model_binding': copy.deepcopy(job.get('model_binding')), 'model_settings': copy.deepcopy(job['model_settings']),
            'weight_policy': copy.deepcopy(job['weight_policy']), 'artifact_ids': self.artifact_ids,
            'context': copy.deepcopy(context), 'summary': self.summary, 'refs': self.refs, 'search': search,
            'intent': self.intent, 'coverage_snapshot': self.coverage_snapshot, 'evidence_context': self.evidence_context,
            'prompt_sha256': digest(self.prompt.encode()),
            'actual_instruction_sha256': digest(self.prompt.split('<SOURCE_CONTEXT>', 1)[0].encode()),
            'parent_path_hash': job['parent_path_hash'], 'retry_of': job.get('retry_of'),
        }
        return answer

    def checked_opportunities(self, answer, db):
        """The agent's research opportunities that pass their check. One that fails (an excerpt the agent
        was not given, a missing field) is left out and noted on the answer; the answer itself is kept, as
        with a knowledge draft, so the user does not have to run the agent again."""
        from memorive.research.opportunities.research_opportunities import MAX_OPPORTUNITIES, validate
        items = self.value.get('opportunities', [])
        if not isinstance(items, list):
            items = [items]
        kept, issues = [], ['OPPORTUNITY_RESULT_INVALID'] * max(0, len(items) - MAX_OPPORTUNITIES)
        items = items[:MAX_OPPORTUNITIES]
        for row in items:
            try:
                kept += validate([row], self.admitted)
            except ValueError as exc:
                issues.append(str(exc))
        if issues:
            answer['opportunity_issues'] = issues
            self.store.event('OPPORTUNITY_WITHHELD', self.job_id, {'reasons': issues, 'answer_preserved': True}, db)
        return kept

    def propose_knowledge(self, answer, db):
        """A comparison, knowledge or agent answer may carry a knowledge draft: propose it for review,
        or note on the answer why it was withheld."""
        draft = self.value.get('knowledge_draft')
        if draft is None or (self.intent not in {'comparison', 'knowledge'} and self.job.get('kind') != 'agent'):
            return
        issue = None
        if not isinstance(draft, dict) or set(draft) != {'title', 'claim', 'scope', 'limitations', 'evidence_ids'}:
            issue = 'KNOWLEDGE_DRAFT_INVALID'
        elif any(not isinstance(draft[k], str) or not draft[k].strip() for k in ('title', 'claim', 'scope', 'limitations')):
            issue = 'KNOWLEDGE_DRAFT_INVALID'
        elif (not isinstance(draft['evidence_ids'], list) or not draft['evidence_ids']
              or any(not isinstance(i, str) or i not in self.admitted for i in draft['evidence_ids'])):
            issue = 'KNOWLEDGE_DRAFT_CITATION_INVALID'
        if issue:
            answer['knowledge_draft_issue'] = issue
            self.store.event('KNOWLEDGE_DRAFT_WITHHELD', self.job_id, {'reason': issue, 'draft_hash': digest(draft), 'answer_preserved': True}, db)
            return
        proposal = self.ws.feedback.propose(project=self.thread['project'], **draft, origin='desktop',
                                            language_context=self.request_context['language_context'], db=db)
        answer['feedback_id'] = proposal['id']
        self.store.put('answer_feedback', answer['id'], self.thread['project'],
                       dict(thread_id=self.thread['id'], answer_id=answer['id'], feedback_id=proposal['id'], job_id=self.job_id), db=db)

    # --- failure ----------------------------------------------------------------------------------

    def record_failure(self, exc):
        """End the job with the error. A late error never undoes a saved answer or a restart's decision."""
        usage = getattr(exc, 'execution_receipt', None) or self.usage
        with self.store.tx() as db:
            job = self.store.get('job', self.job_id, db=db)
            if job and job['status'] in {'QUEUED', 'RUNNING', 'CANCELLED'}:
                self.store.put('job', self.job_id, job['project'], dict(
                    job, status='CANCELLED' if job.get('cancel_requested') else 'ERROR', error=str(exc)[:180], usage=usage,
                    ranking_receipts=self.search.get('ranking_receipts', []), elapsed_ms=self.elapsed_ms()), db=db)
