"""Human read-confirmation, bound to every file of one paper's current run.

Receipts are separate from generated scientific content and never grant KNOWLEDGE_ADMISSION/ARTIFACT_REGISTRY
admission. Prior versions and source language remain untouched.
"""
from copy import deepcopy
import hashlib
import json
import os
import threading
import uuid


class PaperReview:
    def __init__(self, library):
        self.library = library
        self.path = library.state_root / 'paper_review_state.json'
        self.lock = threading.RLock()

    def _load(self):
        if not self.path.exists():
            return {'schema_version':'DesktopPaperReviewState-v1','versions':{}}
        value = json.loads(self.path.read_text(encoding='utf-8'))
        if value.get('schema_version') != 'DesktopPaperReviewState-v1' or not isinstance(value.get('versions'), dict):
            raise ValueError('PAPER_REVIEW_STATE_INVALID')
        return value

    def _write(self, state):
        temporary = self.path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def snapshot(self, artifact_id, verify=False):
        lib = self.library
        logical = lib._core_row_aliases.get(artifact_id, artifact_id)
        entry = lib._core_entries_by_artifact_id[logical]
        children = {key:value for key,value in lib._core_entries_by_artifact_id.items()
                    if value['job_id'] == entry['job_id'] and key != logical
                    and value['source_content_sha256'] == entry['source_content_sha256']}
        required = {key for key,value in children.items()
                    if value['artifact_kind'] in {'CORE_CARD','CORE_ANALYSIS'}}
        kinds = {children[key]['artifact_kind'] for key in required}
        files = {key:value['content_sha256'] for key,value in children.items()}
        manifest = {'source_sha256':entry['source_content_sha256'], 'job_id':entry['job_id'], 'files':files}
        digest = hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
        if verify:
            lib._resolve_core_entry_file(entry,root_field='source_root',path_field='source_private_path',
                sha256_field='source_content_sha256',error_scope='SOURCE')
            for child in children.values():
                lib._resolve_core_entry_file(child,root_field='artifact_root',path_field='private_path',
                    sha256_field='content_sha256',error_scope='ARTIFACT')
        return logical, digest, manifest, required, kinds == {'CORE_CARD','CORE_ANALYSIS'}

    def metadata(self, artifact_id):
        with self.lock:
            logical,digest,manifest,required,complete = self.snapshot(artifact_id)
            row = self._load()['versions'].get(logical + ':' + digest, {})
            valid = True
            if row:
                try: self.snapshot(artifact_id, verify=True)
                except (OSError, ValueError): valid = False
            opened = valid and complete and required.issubset(set(row.get('opened_files',{})))
            confirmed = opened and bool(row.get('confirmed_at'))
            return {'review_opened':opened, 'review_confirmed':confirmed,
                    'review_opened_at':row.get('opened_at') if opened else None,
                    'review_confirmed_at':row.get('confirmed_at') if confirmed else None,
                    'review_files_valid':valid, 'review_manifest_sha256':digest,
                    'file_review_states':{key:('复核通过' if confirmed else '待人工复核') for key in manifest['files']}}

    def open(self, artifact_id):
        # Native caller invokes only after a successful external file open.
        with self.lock:
            logical,digest,manifest,required,complete = self.snapshot(artifact_id, verify=True)
            state = self._load(); key = logical + ':' + digest
            row = state['versions'].setdefault(key, {'artifact_id':logical,'manifest':manifest,'opened_files':{}})
            if artifact_id in manifest['files']:
                row['opened_files'][artifact_id] = manifest['files'][artifact_id]
            row['opened_at'] = self.library._utc_now()
            self._write(state)
            return self.metadata(logical)

    def confirm(self, artifact_id):
        with self.lock:
            logical,digest,manifest,required,complete = self.snapshot(artifact_id, verify=True)
            state = self._load(); row = state['versions'].get(logical + ':' + digest)
            if not complete or not row or not required.issubset(set(row['opened_files'])):
                raise ValueError('PAPER_REVIEW_OPEN_CURRENT_CARD_AND_ANALYSIS_REQUIRED')
            row.setdefault('confirmed_at', self.library._utc_now())
            self._write(state)
            return {'status':'PASS','artifact_id':logical,'review':self.metadata(logical),
                    'source_file_mutations':0,'canonical_registry_mutations':0}
