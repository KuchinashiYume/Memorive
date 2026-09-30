"""A bound, exact-target successor for single-paper RESEARCH_ANALYSIS Analysis review findings."""
from __future__ import annotations
from dataclasses import replace, asdict
from pathlib import Path
import json

from retrieval.context_pack import validate_context_pack_binding
from retrieval.ownership import OwnershipSnapshot, propagation_receipt, stable_sha256
from evidence_review.ownership_binding import build_ownership_review_task
from model_gateway import call
from .source_language import validate_generated_language
from .analysis import (analysis_binding_payload, build_payload, build_analysis_request_envelope,
    _default_region_check, _block_origin, _block_facets, _analysis_quality_metrics)
from .types import LiteratureClaim

REVISION_MARKER='RESEARCH_ANALYSIS_ANALYSIS_TARGETED_REVISION_V1'
REPAIR_SCHEMA={'type':'object','properties':{'repairs':{'type':'array','items':{
    'type':'object','properties':{'index':{'type':'integer'},'text':{'type':'string'},
    'source_chunk_id':{'type':'string'}},'required':['index','text','source_chunk_id'],
    'additionalProperties':False}}},'required':['repairs'],'additionalProperties':False}

def bind_successor(result):
    """Rebind changed content to its already-validated ContextPack and envelope."""
    snapshot=OwnershipSnapshot.from_dict(result.ownership_snapshot)
    digest=stable_sha256(analysis_binding_payload(result,snapshot=snapshot))
    ref=f"A-{digest.removeprefix('sha256:')[:24]}"
    receipt=propagation_receipt(snapshot,subject_chain={
        'context_pack':{'context_pack_ref':result.input_context_pack_ref,
            'context_pack_hash':result.input_context_pack_hash,'ownership_digest':snapshot.ownership_digest},
        'analysis_request_envelope':{'envelope_ref':result.ownership_request_envelope_ref,
            'envelope_hash':result.ownership_request_envelope_hash},
        'analysis_result':{'analysis_ref':ref,'analysis_hash':digest,'ownership_digest':snapshot.ownership_digest}},
        checks=('PACK_TO_ENVELOPE_MATCH','ENVELOPE_TO_ANALYSIS_MATCH'))
    return replace(result,analysis_ref=ref,analysis_hash=digest,ownership_propagation_receipt=receipt.to_dict())

def revise_analysis(previous, context_pack, review, *, output_language='en', model_call=None, region_check=None):
    validate_context_pack_binding(context_pack)
    bound=build_ownership_review_task(previous,legacy_data_ownership='self')
    envelope=build_analysis_request_envelope(context_pack,legacy_data_ownership='self')
    if (previous.input_context_pack_ref!=envelope.context_pack_ref or
            previous.input_context_pack_hash!=envelope.context_pack_hash or envelope.preflight_error_codes):
        raise ValueError('ANALYSIS_REVISION_CONTEXT_MISMATCH')
    task=review.get('ownership_review_task',{})
    if task.get('analysis_hash')!=bound.analysis_hash or task.get('analysis_ref')!=bound.analysis_ref:
        raise ValueError('ANALYSIS_REVISION_STALE_REVIEW')
    if review.get('deferred') or review.get('drives_knowledge_admission') is not False:
        raise ValueError('ANALYSIS_REVISION_REVIEW_NOT_READY')
    targets={}
    for row in review.get('rulings',[]):
        if row.get('gpt_sent') is True and row.get('agrees') is False:
            i=row.get('i')
            if type(i) is not int or not 0<=i<len(previous.literature_support) or i in targets:
                raise ValueError('ANALYSIS_REVISION_TARGET_INVALID')
            if not row.get('disputes'): raise ValueError('ANALYSIS_REVISION_EVIDENCE_MISSING')
            targets[i]={'index':i,'before':asdict(previous.literature_support[i]),'disputes':row['disputes']}
    if not targets: raise ValueError('ANALYSIS_REVISION_NO_TARGETS')
    if envelope.required_route_class!='cloud_allowed' and envelope.required_route_class!='cloud_eligible':
        # Authoritative ownership remains the source of permission; accept the
        # actual self route via the existing preflight instead of guessing it.
        if envelope.ownership_snapshot.get('effective_data_ownership')!='self':
            raise ValueError('ANALYSIS_REVISION_OWNERSHIP_BLOCKED')
    (region_check or _default_region_check)()
    base=build_payload(context_pack,previous.question,output_language=output_language)
    base['response_contract']='analysis_targeted_revision_v1'
    template=(Path(__file__).parents[1]/'model_gateway/prompts/analysis/revision_v1.md').read_text(encoding='utf-8')
    base['messages'][0]['content']+='\n\n'+template.replace('__TARGETS_JSON__',json.dumps(
        {'base_analysis_hash':previous.analysis_hash,'targets':[targets[i] for i in sorted(targets)]},
        ensure_ascii=False,sort_keys=True))
    response=(model_call or (lambda payload:call('analysis',payload)))(base)
    raw=json.loads(response.get('text') or '')
    import jsonschema
    jsonschema.validate(raw,REPAIR_SCHEMA)
    repairs=raw['repairs'];indexes=[row['index'] for row in repairs]
    if len(indexes)!=len(set(indexes)) or set(indexes)!=set(targets):
        raise ValueError('ANALYSIS_REVISION_EXACT_SET_MISMATCH')
    blocks={b.source['chunk_id']:b for b in context_pack.blocks}
    claims=list(previous.literature_support)
    for row in repairs:
        cid=row['source_chunk_id'];text=row['text'].strip()
        if cid not in blocks or not text: raise ValueError('ANALYSIS_REVISION_SOURCE_INVALID')
        block=blocks[cid]
        validate_generated_language(source_text='\n'.join(b.content for b in context_pack.blocks),
            generated_text=text,source_language=output_language,artifact_name='Analysis revision')
        claims[row['index']]=LiteratureClaim(text=text,source_id=dict(block.source),
            origin=_block_origin(block),facet_ids=_block_facets(block))
    result=bind_successor(replace(previous,literature_support=tuple(claims),
        quality_metrics=_analysis_quality_metrics(claims,blocks,context_pack)))
    build_ownership_review_task(result,legacy_data_ownership='self')
    unchanged={str(i):stable_sha256(asdict(claim)) for i,claim in enumerate(previous.literature_support) if i not in targets}
    if any(stable_sha256(asdict(result.literature_support[int(i)]))!=digest for i,digest in unchanged.items()):
        raise ValueError('ANALYSIS_REVISION_UNCHANGED_DRIFT')
    receipt={'schema_version':'ResearchAnalysisDirectedRevision-v1','base_analysis_hash':previous.analysis_hash,
        'successor_analysis_hash':result.analysis_hash,'target_indexes':sorted(targets),
        'unchanged_claim_hashes':unchanged,'targets':[targets[i] for i in sorted(targets)],
        'repairs':repairs,'status':'REVISED_AWAITING_INDEPENDENT_REVIEW','cache_hit_is_acceptance':False}
    return result,receipt
