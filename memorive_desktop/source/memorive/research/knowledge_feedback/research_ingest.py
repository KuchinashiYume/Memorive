"""Knowledge drafts: a draft cites excerpts and waits for a person; an accepted draft becomes a note.

Agents and the desktop may propose a draft; only the desktop reviews or retracts one. Accepting a
draft writes it as a Markdown note in the workspace, registers the note and the sources it cites
with the artifact registry, and adds the note to the evidence index, so it can be cited like any
other material. A revision is a new draft that names its predecessor; accepting it retires the
versions it replaces.

Draft states: review_pending -> active or rejected; active -> retracted.
"""
from contextlib import nullcontext
from pathlib import Path

from memorive.research.workspace.store import digest, now, uid

DERIVED_TYPES = {'synthesis', 'concept', 'annotation'}
MAX_FIELD_CHARS = 16000
MAX_EVIDENCE = 24


class ResearchFeedback:
    def __init__(self, store, index, handoff_validate):
        """handoff_validate(draft, db): raises unless the hand-off a draft answers is still current."""
        self.store = store
        self.index = index
        self.handoff_validate = handoff_validate

    def propose(self, *, project, title, claim, scope, limitations, evidence_ids, origin='desktop',
                derived_type='synthesis', predecessor=None, handoff_binding=None, language_context=None, db=None):
        """Save a draft for review. Proposing the same draft again returns the saved one."""
        if language_context is not None:
            from memorive.platform.language import validate
            language_context = validate(language_context)
        if derived_type not in DERIVED_TYPES:
            raise ValueError('DERIVED_TYPE_INVALID')
        for value in (title, claim, scope, limitations):
            if not isinstance(value, str) or not value.strip() or len(value) > MAX_FIELD_CHARS:
                raise ValueError('FEEDBACK_FIELDS_REQUIRED')
        if not isinstance(evidence_ids, list) or not 1 <= len(evidence_ids) <= MAX_EVIDENCE:
            raise ValueError('FEEDBACK_EVIDENCE_REQUIRED')
        with (self.store.tx() if db is None else nullcontext(db)) as db:
            refs = [self.index.read(i, project, db=db) for i in sorted(set(evidence_ids))]
            cited = [(r['id'], r['content_hash']) for r in refs]
            text = [title.strip(), claim.strip(), scope.strip(), limitations.strip()]
            fingerprint = digest([project, *text, derived_type, predecessor, cited])
            # Folded in only when given, so drafts without them keep the fingerprint they were saved with.
            if handoff_binding:
                fingerprint = digest([fingerprint, handoff_binding])
            if language_context is not None:
                fingerprint = digest([fingerprint, language_context])
            for existing in self.store.list('feedback', project, db=db):
                if existing.get('fingerprint') == fingerprint and existing['state'] in {'review_pending', 'active'}:
                    return existing
            value = {
                'schema_version': 'DesktopResearchFeedback-v1', 'fingerprint': fingerprint, 'project': project,
                'title': title, 'claim': claim, 'scope': scope, 'limitations': limitations,
                'derived_type': derived_type, 'predecessor': predecessor,
                'parents': [{'id': r['id'], 'artifact_id': r['artifact_id'], 'content_hash': r['content_hash']} for r in refs],
                'state': 'review_pending', 'origin': origin, 'created_at': now(), 'human_confirmed': False}
            if handoff_binding:
                value['handoff_binding'] = handoff_binding
            if language_context is not None:
                value['language_context'] = language_context
            value['proposal_hash'] = digest(value)
            row = self.store.put('feedback', uid('fb_'), project, value, db=db)
            self.store.event('KNOWLEDGE_FEEDBACK_PROPOSED', row['id'], {'proposal_hash': value['proposal_hash']}, db)
            return row

    def review(self, identity, *, expected_revision, proposal_hash, decision, confirmed):
        """A person accepts or rejects a pending draft."""
        if confirmed is not True or decision not in {'accept', 'reject'}:
            raise ValueError('HUMAN_REVIEW_REQUIRED')
        accept = decision == 'accept'
        with self.store.tx() as db:
            draft = self._draft(identity, db)
            # Repeating the review already applied returns its result and writes nothing.
            if draft['proposal_hash'] == proposal_hash and draft['state'] == ('active' if accept else 'rejected'):
                return draft
            if draft['state'] != 'review_pending':
                raise ValueError('FEEDBACK_TRANSITION_INVALID')
            earlier = self._earlier_versions(draft, db) if accept else []
            if draft['revision'] != expected_revision or draft['proposal_hash'] != proposal_hash:
                raise ValueError('REVISION_CONFLICT')
            if accept:
                self._admit(draft, earlier, db)
            draft['state'] = 'active' if accept else 'rejected'
            draft['human_confirmed'] = True
            draft['reviewed_at'] = now()
            for old in earlier:
                self._retire(old, draft, db)
            row = self.store.put('feedback', identity, draft['project'], draft, expected=expected_revision, db=db)
            self.store.event('KNOWLEDGE_ADMISSION_FEEDBACK_' + draft['state'].upper(), identity,
                             {'proposal_hash': proposal_hash, 'artifact_id': draft.get('artifact_id')}, db)
            return row

    def retract(self, identity, *, expected_revision, confirmed):
        """A person withdraws an accepted note; it stops being evidence."""
        with self.store.tx() as db:
            note = self._draft(identity, db)
            if note['revision'] != expected_revision:
                raise ValueError('REVISION_CONFLICT')
            if note['state'] != 'active':
                raise ValueError('FEEDBACK_TRANSITION_INVALID')
            if confirmed is not True:
                raise ValueError('HUMAN_REVIEW_REQUIRED')
            note['state'] = 'retracted'
            artifact = self.store.get('artifact', note['artifact_id'], db=db)
            self.store.put('artifact', artifact['id'], note['project'], dict(artifact, state='retracted'), db=db)
            self.store.event('KNOWLEDGE_ADMISSION_FEEDBACK_RETRACTED', identity, {'artifact_id': artifact['id']}, db)
            return self.store.put('feedback', identity, note['project'], note, expected=expected_revision, db=db)

    def _draft(self, identity, db):
        draft = self.store.get('feedback', identity, db=db)
        if not draft:
            raise ValueError('FEEDBACK_NOT_FOUND')
        return draft

    def _earlier_versions(self, draft, db):
        """The drafts a revision replaces: its predecessors back to the last accepted version.
        Raises if one of them was already replaced by another revision."""
        chain = []
        child = draft
        while child.get('predecessor'):
            prior = self.store.get('feedback', child['predecessor'], db=db)
            if not prior or prior['project'] != draft['project'] or prior.get('superseded_by') not in {None, child['id']}:
                raise ValueError('FEEDBACK_REVISION_SUPERSEDED')
            chain.append(prior)
            if prior.get('artifact_id'):  # an accepted version: what came before it was settled then
                break
            child = prior
        return chain

    def _admit(self, draft, earlier, db):
        """Check that the draft's sources and hand-off are still current, then register it as a note."""
        if draft.get('handoff_binding'):
            self.handoff_validate(draft, db)
        refs = [self.index.read(r['id'], draft['project'], expected_hash=r['content_hash'], db=db) for r in draft['parents']]
        if earlier:
            self._check_not_built_on(earlier, refs, db)
        draft['artifact_id'], draft['evidence_id'] = self._register(draft, refs, db)

    def _check_not_built_on(self, earlier, refs, db):
        """A revision must not rest on a version it replaces: that version is retracted when the
        revision is accepted, and the revision would stop being readable."""
        replaced = {old['artifact_id'] for old in earlier if old.get('artifact_id')}
        pending = [r['artifact_id'] for r in refs]
        seen = set()
        while pending:
            artifact_id = pending.pop()
            if artifact_id in replaced:
                raise ValueError('FEEDBACK_REVISION_PARENT_CONFLICT')
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            source = self.store.get('artifact', artifact_id, db=db)
            if source:
                pending.extend(r['artifact_id'] for r in source.get('parents', []))

    def _retire(self, old, successor, db):
        """An accepted revision closes the versions it replaces: a pending draft is rejected and an
        accepted note is retracted."""
        if old['state'] == 'review_pending':
            self.store.put('feedback', old['id'], successor['project'],
                           dict(old, superseded_by=successor['id'], state='rejected'), db=db)
        elif old['state'] == 'active':
            note = self.store.get('artifact', old['artifact_id'], db=db)
            self.store.put('artifact', note['id'], successor['project'], dict(note, state='retracted'), db=db)
            self.store.put('feedback', old['id'], successor['project'],
                           dict(old, superseded_by=successor['id'], state='retracted'), db=db)
        else:
            return
        self.store.event('KNOWLEDGE_ADMISSION_FEEDBACK_SUPERSEDED', old['id'], {'successor': successor['id']}, db)

    def _register(self, draft, refs, db):
        """Write the note, register it with the sources it cites, and add it to the evidence index."""
        from memorive.documents.registry.registry_v2 import ArtifactRegistry
        from memorive.documents.registry.schema_v2 import build_envelope
        folder = self.store.root / 'feedback_artifacts'
        folder.mkdir(exist_ok=True)
        registry = ArtifactRegistry(self.store.root / 'artifact_registry_registry')
        parents = []
        for ref in refs:
            link = self._source_link(ref, draft, registry, folder, db)
            if link not in parents:
                parents.append(link)
        artifact_id = 'art_' + digest(draft['id'])[:32]
        text = note_text(draft, refs)
        raw = text.encode('utf-8')
        path = folder / (artifact_id + '.md')
        _write_once(path, raw, 'FEEDBACK_ARTIFACT_CONFLICT')
        registry.register(build_envelope(
            artifact_id=artifact_id, artifact_type='derived_knowledge_artifact', content_hash=digest(raw),
            locator_path=path, registered_at=draft['created_at'], schema_ref='DesktopResearchFeedback-v1',
            run_ref=draft['id'], parent_artifacts=parents, legacy_import=False, evidence_refs=[r['id'] for r in refs],
            metadata={'human_confirmed': True, 'proposal_hash': draft['proposal_hash'], 'state_owner': 'M08',
                      'scientific_judgment': 'HUMAN_REVIEWED_WITH_LIMITATIONS'}))
        self.store.put('artifact', artifact_id, draft['project'], {
            'project': draft['project'], 'title': draft['title'], 'path': str(path), 'content_hash': digest(raw),
            'state': 'active', 'kind': 'derived', 'derived_type': draft.get('derived_type', 'synthesis'),
            'parents': draft['parents'], 'indexed_at': draft['created_at'], 'feedback_id': draft['id']}, db=db)
        evidence_id = 'ev_' + digest([artifact_id, digest(raw)])[:32]
        db.execute('INSERT OR IGNORE INTO chunks VALUES(?,?,?,?,?,?,?,?)',
                   (evidence_id, artifact_id, draft['project'], text, 1, len(text.splitlines()), None, digest(text)))
        return artifact_id, evidence_id

    def _source_link(self, ref, draft, registry, folder, db):
        """The registry link to one cited source. An accepted note is linked directly; any other
        document through a registered copy of the version that was cited."""
        from memorive.documents.registry.errors import UnknownArtifactError
        from memorive.documents.registry.schema_v2 import build_envelope, parent_link
        source = self.store.get('artifact', ref['artifact_id'], db=db)
        if source['kind'] == 'derived':
            parent_id = source['id']
        else:
            raw = Path(source['path']).read_bytes()
            if digest(raw) != ref['content_hash']:
                raise ValueError('EVIDENCE_STALE')
            parent_id = 'art_' + digest([ref['artifact_id'], ref['content_hash']])[:32]
            snapshot = folder / (parent_id + '.source')
            _write_once(snapshot, raw, 'SOURCE_SNAPSHOT_CONFLICT')
            try:
                registered = registry.get(parent_id)
            except UnknownArtifactError:
                registry.register(build_envelope(
                    artifact_id=parent_id, artifact_type='research_source_snapshot', content_hash=ref['content_hash'],
                    locator_path=snapshot, registered_at=source['indexed_at'], schema_ref='DesktopResearchSourceSnapshot-v1',
                    run_ref='research-feedback', legacy_import=False, evidence_refs=[ref['artifact_id'] + ':' + ref['content_hash']],
                    metadata={'source_artifact_id': ref['artifact_id'], 'source_scope': draft['project']}))
            else:
                if registered['content_hash']['value'].lower() != ref['content_hash'].lower():
                    raise ValueError('SOURCE_REGISTRY_CONFLICT')
        return parent_link(parent_artifact_id=parent_id, parent_content_hash=ref['content_hash'], relation='derived_from',
                           evidence_kind='source_snapshot', source_field='parents', evidence_ref=ref['id'])


def note_text(draft, refs):
    """The Markdown of an accepted note. Drafts saved without a language get the English headings."""
    from memorive.platform.language.text import choose
    language = draft.get('language_context') or 'en-US'

    def heading(zh, en, ja):
        return '## ' + choose(language, zh, en, ja) + '\n'

    evidence = ''.join('- ' + r['id'] + ' · ' + r['title'] + '\n' for r in refs)
    return ('# ' + draft['title'] + '\n\n' + draft['claim'] + '\n\n'
            + heading('适用范围', 'Scope', '適用範囲') + draft['scope'] + '\n\n'
            + heading('局限', 'Limitations', '限界') + draft['limitations'] + '\n\n'
            + heading('证据', 'Evidence', '証拠') + evidence)


def _write_once(path, raw, conflict):
    """Write a file that never changes afterwards; a copy already there must hold the same bytes."""
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError(conflict)
    else:
        with path.open('xb') as f:
            f.write(raw)
