"""Associate actual Card omission anchors with a currently missing numeric input.

Removed Card values are deliberately never read. A literal source formula and its
known inputs establish the association; page numbers come from its current chunk.
"""
import re
from .engine import digest

def automatic_bindings(rules,checks,omissions,raw,source_rows):
    chunks={}
    for row in source_rows or []:
        if isinstance(row,dict) and row.get('chunk_id'):
            chunks.setdefault(row['chunk_id'],[]).append(row)
    result=[]
    for rule in rules:
        check=checks.get(rule.get('rule_id'),{})
        origin=rule.get('binding_source',{});context=origin.get('quote','').strip()
        labels=origin.get('input_labels',{})
        if check.get('status')!='INSUFFICIENT_INFORMATION' or not labels or not context or raw.count(context)!=1:continue
        if origin.get('ambiguous_labels'):continue
        for parameter,label in labels.items():
            if 'inputs.'+parameter not in check.get('missing',[]):continue
            if any(b.get('parameter')==parameter for b in rule.get('card_gap_bindings',[])):continue
            candidates=[]
            for omission in omissions:
                if not isinstance(omission,dict) or omission.get('status')!='removed_from_final_card':continue
                accepted=[];pages=set();excluded=False;chunk_hashes=[]
                for i,anchor in enumerate(omission.get('related_anchors',[])):
                    if not isinstance(anchor,dict):continue
                    rows=chunks.get(anchor.get('chunk_id'),[])
                    row=rows[0] if len(rows)==1 else {}
                    quote=anchor.get('quote') or ''
                    chunk_text=row.get('text') or ''
                    if context not in quote and not (quote and quote in chunk_text and context in chunk_text):continue
                    accepted.append(i)
                    excluded=excluded or any(x.get('access_excluded') is True or x.get('excluded') is True for x in (anchor,row))
                    start,end=row.get('page_start'),row.get('page_end')
                    if type(start)==int and type(end)==int and 1<=start<=end and end-start<2:
                        pages.update(range(start,end+1));chunk_hashes.append(digest(row))
                if accepted:
                    candidates.append(dict(parameter=parameter,card_location=omission.get('location'),
                        card_omission_id=digest(omission),label=label,pages=sorted(pages),access_excluded=excluded,
                        anchor_indices=accepted,automatic=True,association='EXACT_SOURCE_FORMULA_IN_OMISSION_ANCHOR_OR_BOUND_CHUNK',
                        source_context=context,chunk_sha256s=chunk_hashes))
            # Competing tombstones may refer to different groups: do not choose one.
            if len(candidates)==1:
                rule.setdefault('card_gap_bindings',[]).append(candidates[0]);result.append(candidates[0])
    return result
