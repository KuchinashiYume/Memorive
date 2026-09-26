"""Persist one exact-target Analysis successor and its independent Delta review."""
from __future__ import annotations
from dataclasses import asdict
import hashlib,json,pickle
from pathlib import Path

def _write(path,value):
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)

def complete_analysis_revision(analysis,pack,review,chunks,*,bridge,evidence_root,output_language,
        model_call=None, region_check=None, verifier_call=None):
    from m4_analysis.revision import revise_analysis
    from m6_verify import verify_m4_analysis
    root=Path(evidence_root);root.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256(json.dumps(review,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    identity={'base_analysis_hash':analysis.analysis_hash,'context_pack_hash':pack.context_pack_hash,'review_sha256':digest}
    completed=root/'completed.json';dispatch=root/'dispatch.json'
    if completed.exists():
        result=json.loads(completed.read_text(encoding='utf-8'))
        if result['identity']!=identity:raise RuntimeError('ANALYSIS_REVISION_CHECKPOINT_MISMATCH')
        payload=(root/'analysis.pkl').read_bytes()
        if hashlib.sha256(payload).hexdigest()!=result['analysis_pickle_sha256']:
            raise RuntimeError('ANALYSIS_REVISION_CHECKPOINT_TAMPERED')
        current=pickle.loads(payload)
        if current.analysis_hash!=result['successor_analysis_hash']:
            raise RuntimeError('ANALYSIS_REVISION_CHECKPOINT_CONTENT_MISMATCH')
        return current,result['combined_review']
    if dispatch.exists():
        raise RuntimeError('ANALYSIS_REVISION_INCOMPLETE_ATTEMPT_REQUIRES_RECEIPT_RECONCILIATION')
    targets=[r for r in review.get('rulings',[]) if r.get('gpt_sent') is True and r.get('agrees') is False]
    if not targets:return analysis,review
    _write(dispatch,{'identity':identity,'state':'BOUND_BEFORE_REVISION_SEND'})
    _write(root/'initial_review.json',review)
    revised,receipt=revise_analysis(analysis,pack,review,output_language=output_language,
        model_call=model_call,region_check=region_check)
    _write(root/'revision_receipt.json',receipt)
    _write(root/'revised_before_delta.json',asdict(revised))
    delta=verify_m4_analysis(revised,chunks,data_ownership='self',
        target={**review.get('target',{}),'analysis_hash':revised.analysis_hash},
        max_gpt=len(receipt['target_indexes']),claim_indexes=receipt['target_indexes'],
        candidate_resolver=bridge.select_judgment_candidates,output_language=output_language,
        verifier_call=verifier_call,region_check=region_check)
    _write(root/'delta_review.json',delta)
    from .analysis_omission import finalize_delta, omission_notice
    revised,combined,tombstones=finalize_delta(analysis,revised,pack,review,receipt,delta)
    _write(root/'tombstones.json',tombstones)
    _write(root/'analysis.json',asdict(revised))
    payload=pickle.dumps(revised)
    with (root/'analysis.pkl').open('xb') as stream:stream.write(payload)
    with (root/'analysis.md').open('x',encoding='utf-8') as stream:
        from inspect import signature
        rendered = revised.render(language=output_language) if 'language' in signature(revised.render).parameters else revised.render()
        stream.write(rendered+omission_notice(tombstones,output_language))
    _write(completed,{'identity':identity,'successor_analysis_hash':revised.analysis_hash,
        'analysis_pickle_sha256':hashlib.sha256(payload).hexdigest(),'combined_review':combined})
    return revised,combined
