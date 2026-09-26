"""Source-bound desktop successor: proposal -> human review -> ARTIFACT_REGISTRY -> KNOWLEDGE_ADMISSION active.

No legacy synthetic-only contract is widened or relabelled. External agent tools
can propose; only the trusted desktop controller calls review/retract.
"""
from pathlib import Path
from contextlib import nullcontext
from memorive_research_workspace.store import digest,uid,now,packed
from knowledge_admission.research_feedback import transition_feedback

class ResearchFeedback:
    def __init__(self,store,index):self.store=store;self.index=index

    def propose(self,*,project,title,claim,scope,limitations,evidence_ids,origin='desktop',derived_type='synthesis',predecessor=None,db=None):
        if derived_type not in {'synthesis','concept','annotation'}:raise ValueError('DERIVED_TYPE_INVALID')
        for value in (title,claim,scope,limitations):
            if not isinstance(value,str) or not value.strip() or len(value)>16000:raise ValueError('FEEDBACK_FIELDS_REQUIRED')
        if not isinstance(evidence_ids,list) or not 1<=len(evidence_ids)<=24:raise ValueError('FEEDBACK_EVIDENCE_REQUIRED')
        with (self.store.tx() if db is None else nullcontext(db)) as db:
            refs=[self.index.read(i,project,db=db) for i in sorted(set(evidence_ids))]
            fingerprint=digest([project,title.strip(),claim.strip(),scope.strip(),limitations.strip(),derived_type,predecessor,[(r['id'],r['content_hash']) for r in refs]])
            for existing in self.store.list('feedback',project,db=db):
                if existing.get('fingerprint')==fingerprint and existing['state'] in {'review_pending','active'}:return existing
            value={'fingerprint':fingerprint,'derived_type':derived_type,'predecessor':predecessor,'schema_version':'DesktopResearchFeedback-v1','project':project,'title':title,'claim':claim,'scope':scope,
                'limitations':limitations,'parents':[{'id':r['id'],'artifact_id':r['artifact_id'],'content_hash':r['content_hash']} for r in refs],
                'state':'review_pending','origin':origin,'created_at':now(),'human_confirmed':False}
            value['proposal_hash']=digest(value)
            row=self.store.put('feedback',uid('fb_'),project,value,db=db)
            self.store.event('KNOWLEDGE_FEEDBACK_PROPOSED',row['id'],{'proposal_hash':value['proposal_hash']},db)
            return row

    def _register(self,proposal,refs,db):
        from artifact_registry.registry_v2 import ArtifactRegistry
        from artifact_registry.schema_v2 import build_envelope,parent_link
        root=self.store.root/'feedback_artifacts';root.mkdir(exist_ok=True)
        registry=ArtifactRegistry(self.store.root/'artifact_registry_registry')
        from artifact_registry.errors import UnknownArtifactError
        parents=[]
        for ref in refs:
            source=self.store.get('artifact',ref['artifact_id'],db=db)
            raw=Path(source['path']).read_bytes()
            if digest(raw)!=ref['content_hash']:raise ValueError('EVIDENCE_STALE')
            parent_id='art_'+digest([ref['artifact_id'],ref['content_hash']])[:32]
            snapshot=root/(parent_id+'.source')
            if snapshot.exists():
                if digest(snapshot.read_bytes())!=ref['content_hash']:raise ValueError('SOURCE_SNAPSHOT_CONFLICT')
            else:
                with snapshot.open('xb') as f:f.write(raw);f.flush()
            if source['kind']=='derived':
                # Use the registered derived object itself, preserving the full ARTIFACT_REGISTRY chain.
                parent_id=source['id']
            else:
                envelope=build_envelope(artifact_id=parent_id,artifact_type='research_source_snapshot',
                    content_hash=ref['content_hash'],locator_path=snapshot,registered_at=source['indexed_at'],
                    schema_ref='DesktopResearchSourceSnapshot-v1',run_ref='research-feedback',legacy_import=False,
                    evidence_refs=[ref['artifact_id']+':'+ref['content_hash']],
                    metadata={'source_artifact_id':ref['artifact_id'],'source_scope':proposal['project']})
                try:registered=registry.get(parent_id)
                except UnknownArtifactError:registry.register(envelope)
                else:
                    if registered['content_hash']['value'].lower()!=ref['content_hash'].lower():raise ValueError('SOURCE_REGISTRY_CONFLICT')
            link=parent_link(parent_artifact_id=parent_id,parent_content_hash=ref['content_hash'],relation='derived_from',
                evidence_kind='source_snapshot',source_field='parents',evidence_ref=ref['id'])
            if link not in parents:parents.append(link)
        artifact_id='art_'+digest(proposal['id'])[:32]
        text='# '+proposal['title']+'\n\n'+proposal['claim']+'\n\n## Scope\n'+proposal['scope']+'\n\n## Limitations\n'+proposal['limitations']+'\n\n## Evidence\n'+''.join('- '+r['id']+' · '+r['title']+'\n' for r in refs)
        path=root/(artifact_id+'.md');raw=text.encode('utf-8')
        if path.exists():
            if path.read_bytes()!=raw:raise ValueError('FEEDBACK_ARTIFACT_CONFLICT')
        else:
            with path.open('xb') as f:f.write(raw);f.flush()
        registry.register(build_envelope(artifact_id=artifact_id,artifact_type='derived_knowledge_artifact',
            content_hash=digest(raw),locator_path=path,registered_at=proposal['created_at'],schema_ref='DesktopResearchFeedback-v1',
            run_ref=proposal['id'],parent_artifacts=parents,legacy_import=False,evidence_refs=[r['id'] for r in refs],
            metadata={'human_confirmed':True,'proposal_hash':proposal['proposal_hash'],'state_owner':'M08','scientific_judgment':'HUMAN_REVIEWED_WITH_LIMITATIONS'}))
        value={'project':proposal['project'],'title':proposal['title'],'path':str(path),'content_hash':digest(raw),
            'state':'active','kind':'derived','derived_type':proposal.get('derived_type','synthesis'),'parents':proposal['parents'],'indexed_at':proposal['created_at'],'feedback_id':proposal['id']}
        self.store.put('artifact',artifact_id,proposal['project'],value,db=db)
        evidence_id='ev_'+digest([artifact_id,digest(raw)])[:32]
        db.execute('INSERT OR IGNORE INTO chunks VALUES(?,?,?,?,?,?,?,?)',(evidence_id,artifact_id,proposal['project'],text,1,len(text.splitlines()),None,digest(text)))
        return artifact_id,evidence_id

    def review(self,identity,*,expected_revision,proposal_hash,decision,confirmed):
        if confirmed is not True or decision not in {'accept','reject'}:raise ValueError('HUMAN_REVIEW_REQUIRED')
        with self.store.tx() as db:
            p=self.store.get('feedback',identity,db=db)
            if not p:raise ValueError('FEEDBACK_NOT_FOUND')
            # Replaying the exact accepted review is a no-op, never another write.
            if p['proposal_hash']==proposal_hash and p['state']==('active' if decision=='accept' else 'rejected'):return p
            if p['state']!='review_pending':raise ValueError('FEEDBACK_TRANSITION_INVALID')
            if p.get('predecessor') and decision=='accept':
                prior=self.store.get('feedback',p['predecessor'],db=db)
                if not prior or prior.get('superseded_by') not in {None,identity}:raise ValueError('FEEDBACK_REVISION_SUPERSEDED')
            if p['revision']!=expected_revision or p['proposal_hash']!=proposal_hash:raise ValueError('REVISION_CONFLICT')
            refs=[self.index.read(r['id'],p['project'],expected_hash=r['content_hash'],db=db) for r in p['parents']] if decision=='accept' else []
            if decision=='accept':p['artifact_id'],p['evidence_id']=self._register(p,refs,db)
            p['state']=transition_feedback(p['state'],'active' if decision=='accept' else 'rejected',
                human_confirmed=True,source_fresh=bool(refs),registry_registered=bool(p.get('artifact_id')))
            p['human_confirmed']=True;p['reviewed_at']=now()
            if decision=='accept' and p.get('predecessor'):
                old=self.store.get('feedback',p['predecessor'],db=db)
                if old and old['state']=='review_pending':
                    state=transition_feedback(old['state'],'rejected',human_confirmed=True,source_fresh=True,registry_registered=False)
                    self.store.put('feedback',old['id'],p['project'],dict(old,superseded_by=identity,state=state),db=db)
                    self.store.event('KNOWLEDGE_ADMISSION_FEEDBACK_SUPERSEDED',old['id'],{'successor':identity},db)
                if old and old['state']=='active':
                    prior=self.store.get('artifact',old['artifact_id'],db=db)
                    state=transition_feedback(old['state'],'retracted',human_confirmed=True,source_fresh=True,registry_registered=True)
                    self.store.put('artifact',prior['id'],p['project'],dict(prior,state=state),db=db)
                    self.store.put('feedback',old['id'],p['project'],dict(old,superseded_by=identity,state=state),db=db)
                    self.store.event('KNOWLEDGE_ADMISSION_FEEDBACK_SUPERSEDED',old['id'],{'successor':identity},db)
            row=self.store.put('feedback',identity,p['project'],p,expected=expected_revision,db=db)
            self.store.event('KNOWLEDGE_ADMISSION_FEEDBACK_'+p['state'].upper(),identity,{'proposal_hash':proposal_hash,'artifact_id':p.get('artifact_id')},db)
            return row

    def retract(self,identity,*,expected_revision,confirmed):
        with self.store.tx() as db:
            p=self.store.get('feedback',identity,db=db)
            if not p:raise ValueError('FEEDBACK_NOT_FOUND')
            if p['revision']!=expected_revision:raise ValueError('REVISION_CONFLICT')
            p['state']=transition_feedback(p['state'],'retracted',human_confirmed=confirmed is True,source_fresh=True,registry_registered=True)
            a=self.store.get('artifact',p['artifact_id'],db=db)
            self.store.put('artifact',a['id'],p['project'],dict(a,state='retracted'),db=db)
            self.store.event('KNOWLEDGE_ADMISSION_FEEDBACK_RETRACTED',identity,{'artifact_id':a['id']},db)
            return self.store.put('feedback',identity,p['project'],p,expected=expected_revision,db=db)
