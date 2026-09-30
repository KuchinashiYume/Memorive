"""Automatic bounded rawPDF support only for a relevant recorded Card gap."""
from pathlib import Path
import re
import time
from .engine import digest,extract

def recover_gap(path,*,source_sha256,gap,cache,checkpoint=lambda:None,max_pages=2,max_seconds=5):
    required=('rule_id','parameter','card_omission_id','reason','pages','label','source_sha256')
    if not isinstance(gap,dict) or any(k not in gap for k in required):return {'status':'NOT_APPLICABLE','reason':'NO_BOUND_RELEVANT_CARD_GAP'}
    if gap.get('access_excluded') is True:return {'status':'BLOCKED','reason':'SOURCE_ACCESS_EXCLUDED'}
    if gap['source_sha256'].upper()!=source_sha256.upper():return {'status':'UNRESOLVED','reason':'SOURCE_VERSION_MISMATCH'}
    if not gap['card_omission_id'] or gap['reason'] not in {'MISSING','REMOVED','INVALID_ANCHOR','MISSING_QUALIFIER'}:
        return {'status':'NOT_APPLICABLE','reason':'NO_BOUND_RELEVANT_CARD_GAP'}
    pages=gap['pages']
    if not isinstance(pages,list) or not pages or len(set(pages))>max_pages or any(type(p)!=int or p<1 for p in pages):
        return {'status':'UNRESOLVED','reason':'LOCAL_PAGE_BUDGET_OR_LOCATOR_INVALID'}
    key=digest({'kind':'RawPDFAssist-v3','source_sha256':source_sha256,'gap':gap,'max_pages':max_pages})
    saved=cache(key)
    if saved:return {**saved,'cache_reused':True}
    start=time.monotonic();checkpoint();path=Path(path)
    if path.suffix.casefold()!='.pdf' or path.stat().st_size>64*1024*1024 or digest(path.read_bytes())!=source_sha256.upper():
        return {'status':'UNRESOLVED','reason':'PDF_SOURCE_HASH_MISMATCH'}
    matches=[];read=[]
    try:
        import fitz
        with fitz.open(path) as pdf:
            from memorive_workflow.node_progress import scope
            with scope('PDF_ASSIST', sorted(set(pages))) as node_units:
                for page in sorted(set(pages)):
                    checkpoint()
                    if time.monotonic()-start>max_seconds:raise TimeoutError('PDF_LOCAL_TIME_BUDGET_EXCEEDED')
                    if page>pdf.page_count:raise ValueError('PDF_PAGE_OUT_OF_RANGE')
                    text=pdf[page-1].get_text();read.append(page)
                    if time.monotonic()-start>max_seconds:raise TimeoutError('PDF_LOCAL_TIME_BUDGET_EXCEEDED')
                    if gap.get('automatic'):
                        # The page locator alone is not a parameter/group binding.
                        # Require the same exact source formula context, then inspect
                        # only its bounded neighboring text; never use a remote n.
                        normalized=re.sub(r'\s+',' ',text)
                        context=re.sub(r'\s+',' ',str(gap.get('source_context') or '')).strip()
                        if not context or normalized.count(context)!=1:
                            node_units.complete(page)
                            continue
                        offset=normalized.index(context)
                        text=normalized[max(0,offset-160):offset+len(context)+240]
                    label=str(gap['label'])
                    if len(label)>80 or not label:raise ValueError('PDF_LABEL_INVALID')
                    pattern=re.compile(r'(?<!\w)'+re.escape(label)+r'\s*[:=]\s*([+−-]?(?:\d+(?:\.\d+)?|\.\d+))(?!\w|\.\d)')
                    for line_no,line in enumerate(text.splitlines(),1):
                        for match in pattern.finditer(line):
                            matches.append({'value':match.group(1),'raw':match.group(1),'relation':'=',
                                'record_id':f'PDF{page}:L{line_no}:C{match.start(1)+1}',
                                'source':{'kind':'rawPDF','sha256':source_sha256,'page':page,'line':line_no,'quote':line,
                                    'line_scope':'Whitespace-normalized bounded context window' if gap.get('automatic') else 'Extracted PDF page text'},
                                'semantic_role':'EXPLICIT_CARD_GAP_BINDING','parameter':gap['parameter'],'rule_id':gap['rule_id']})
                    node_units.complete(page)
        result={'status':'RESOLVED' if len(matches)==1 else 'UNRESOLVED','reason':'ONE_EXPLICIT_LABEL' if len(matches)==1 else 'NO_UNIQUE_LABEL_IN_READ_PAGES',
                'records':matches if len(matches)==1 else [],'candidate_count':len(matches),'pages_read':read,'source_sha256':source_sha256,
                'gap':gap,'card_written':False,'external_model_calls':0,'whole_document_absence_claim':False}
    except Exception as error:
        from memorive_settings.call_ledger import ExecutionControlSignal
        if isinstance(error,ExecutionControlSignal):raise
        result={'status':'UNRESOLVED','reason':type(error).__name__,'pages_read':read,'source_sha256':source_sha256,'card_written':False,'external_model_calls':0}
    cache(key,result);return result
