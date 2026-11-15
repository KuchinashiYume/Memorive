"""Review a new Card revision without downgrading the previous admitted Card."""
from dataclasses import asdict
import hashlib, json, os, shutil, uuid
from pathlib import Path
from memorive_settings.call_ledger import execution_checkpoint

def _hash(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()

def _publish(source, destination):
    destination=Path(destination)
    temporary=destination.with_name('.'+destination.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(Path(source).read_bytes());stream.flush();os.fsync(stream.fileno())
        execution_checkpoint()
        os.replace(temporary,destination)
    finally:
        if temporary.exists():temporary.unlink()

class CardAdmissionError(RuntimeError):
    def __init__(self,result):
        self.root_reason_code=getattr(result,'root_reason_code',None)
        self.caused_by=getattr(result,'failure_reason',None)
        message='CORE_CARD_ADMISSION_FAILED:'+str(result.outcome)
        if self.root_reason_code:message+=':'+str(self.root_reason_code)
        super().__init__(message)


def review_card_revision(card_path, source_folder, stage_root, *, reviewer=None, before_publish=None):
    from evidence_review import grade_and_admit
    from knowledge_admission.card_io import read_status, write_status
    card_path,source_folder,stage_root=map(Path,(card_path,source_folder,stage_root))
    root=stage_root/'04_card_revision';root.mkdir(parents=True,exist_ok=True)
    seed=root/'card_input.snapshot'
    if not seed.exists():shutil.copy2(card_path,seed)
    chunks=sorted(source_folder.glob('[[]Chunks]*.jsonl'))
    if len(chunks)!=1:raise ValueError('CARD_REVIEW_CHUNKS_CARDINALITY')
    binding={'card_input_sha256':_hash(seed),'chunks_sha256':_hash(chunks[0])}
    draft=root/card_path.name
    receipt_path=stage_root/'04_05_review_admission.json'
    if receipt_path.exists():
        receipt=json.loads(receipt_path.read_text(encoding='utf8'))
        if receipt.get('input_binding')!=binding or receipt.get('outcome')!='active' or not draft.is_file() or receipt.get('card_sha256')!=_hash(draft) or read_status(draft)!='active':
            raise ValueError('CARD_REVIEW_RECEIPT_BINDING_MISMATCH')
    else:
        # Only the newly created revision becomes pending. The user's prior
        # active Card and predecessor job artifacts remain valid and untouched.
        shutil.copy2(seed,draft)
        shutil.copy2(chunks[0],root/chunks[0].name)
        write_status(draft,'pending')
        result=(reviewer or grade_and_admit)(draft,bypass_cache=True)
        if result.outcome!='active' or read_status(draft)!='active':
            raise CardAdmissionError(result)
        receipt={**asdict(result),'input_binding':binding,'card_sha256':_hash(draft),
                 'schema_version':'MemoCardReviewRevision-v1'}
        tmp=receipt_path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        with tmp.open('x',encoding='utf8') as stream:
            json.dump(receipt,stream,ensure_ascii=False,sort_keys=True);stream.flush();os.fsync(stream.fileno())
        os.replace(tmp,receipt_path)
    if before_publish is not None:
        before_publish()
    destinations=list(source_folder.glob('[[]Card]*.md'))
    if len(destinations)>1:raise ValueError('CARD_REVIEW_PUBLICATION_TARGET_AMBIGUOUS')
    destination=destinations[0] if destinations else source_folder/card_path.name
    _publish(draft,destination)
    return draft
