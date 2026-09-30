"""Published, immutable structure candidates and explicit review receipts."""
from __future__ import annotations
from pathlib import Path
import base64, json, os, re, shutil, uuid
from .contract import canonical,digest,file_sha,read_json,structure_id,validate_structure,fail
from .service import write_structure
from .office import OFFICE_SCHEMA,office_structure

BUNDLE='MemoriveStructureBundle-v1'
def _json(path,value):
    data=canonical(value)+b'\n'
    if len(data)>32*1024*1024:fail('STRUCTURE_OUTPUT_TOO_LARGE','Structure output exceeds 32 MiB')
    path.write_bytes(data)

def normalize_config(value,*,verify_runtime=True):
    if not isinstance(value,dict):raise ValueError('STRUCTURE_CONFIG_INVALID')
    allowed={'mode','profile','runtime_path','timeout_seconds','runtime_lock_sha256'}
    if set(value)-allowed:raise ValueError('STRUCTURE_CONFIG_UNKNOWN_FIELDS')
    out={'mode':'off','profile':'fast_no_ocr','runtime_path':'','timeout_seconds':600,'runtime_lock_sha256':None,**value}
    if out['mode'] not in {'off','native','marker'} or out['profile'] not in {'fast_no_ocr','fast_ocr','balanced_ocr'}:
        raise ValueError('STRUCTURE_CONFIG_INVALID')
    if type(out['timeout_seconds']) is not int or not 1<=out['timeout_seconds']<=3600:raise ValueError('STRUCTURE_TIMEOUT_INVALID')
    if not isinstance(out['runtime_path'],str):raise ValueError('STRUCTURE_RUNTIME_PATH_INVALID')
    if out['mode']=='marker' and verify_runtime:
        from .marker_runtime import inspect_runtime
        identity=inspect_runtime(out['runtime_path'])
        if out['runtime_lock_sha256'] is not None and out['runtime_lock_sha256']!=identity['lock_sha256']:
            raise ValueError('STRUCTURE_FROZEN_RUNTIME_CHANGED')
        out['runtime_lock_sha256']=identity['lock_sha256']
    return out

def _calibrate(value,source):
    """PDFium/Marker page space to displayed PDF points; only compatible extents."""
    import pymupdf
    with pymupdf.open(source) as doc:
        for page in value['pages']:
            native=doc[page['page_index']]; box=page['bbox']; w=box[2]-box[0];h=box[3]-box[1]
            ratio=native.rect.width/native.rect.height
            supported=abs(w/h-ratio)<max(.002,ratio*.002)
            page['pdf_display_bbox']=list(native.rect);page['pdf_rotation']=native.rotation
            page['overlay_status']='CALIBRATED_DISPLAY_POINTS' if supported else 'PAGE_ONLY'
            for block in value['blocks']:
                if block['page_index']!=page['page_index']:continue
                block_supported=supported and block.get('coordinate_status')!='PAGE_ONLY_OUTSIDE_PAGE'
                if block_supported:
                    a,b,c,d=block['bbox']
                    block['pdf_display_bbox']=[(a-box[0])*native.rect.width/w,(b-box[1])*native.rect.height/h,
                                               (c-box[0])*native.rect.width/w,(d-box[1])*native.rect.height/h]
                else:block.pop('pdf_display_bbox',None)
                block['source_location']={'kind':'pdf','physical_page':page['physical_page'],
                                          'bbox':block.get('pdf_display_bbox'),'coordinate_system':'pdf_display_points' if block_supported else 'page_only'}
    value['warnings']=[w for w in value['warnings'] if w!='MARKER_COORDINATES_REQUIRE_CALIBRATION_FOR_PDF_OVERLAY']
    value['structure_id']=structure_id(value)

def build_bundle(source,rawmd,destination,config,*,interrupt=None,progress=None):
    source,rawmd,destination=map(lambda p:Path(p).resolve(),(source,rawmd,destination))
    config=normalize_config(config)
    if config['mode']=='off':raise ValueError('STRUCTURE_DISABLED')
    source_sha=file_sha(source);raw_sha=file_sha(rawmd)
    binding={'source_sha256':source_sha,'rawmd_sha256':raw_sha,'config':config}
    if destination.exists():
        old=verify(destination)
        if old['binding']!=binding:raise ValueError('STRUCTURE_DESTINATION_CONFLICT')
        return old
    destination.parent.mkdir(parents=True,exist_ok=True)
    temp=destination.with_name('.'+destination.name+'-'+uuid.uuid4().hex+'.pending');temp.mkdir()
    try:
        if interrupt:interrupt()
        if progress:progress('probe',{'format':source.suffix.lower()})
        original=temp/('Original'+source.suffix.lower());raw=temp/'RawMD.md'
        shutil.copyfile(source,original);shutil.copyfile(rawmd,raw)
        if file_sha(original)!=source_sha or file_sha(raw)!=raw_sha:raise ValueError('STRUCTURE_COPY_CHANGED')
        if progress:progress('parse',{'format':source.suffix.lower(),'profile':config['profile'] if config['mode']=='marker' else 'native'})
        execution=None
        if source.suffix.lower() in {'.xlsx','.docx','.pptx'}:
            value=office_structure(original,raw);_json(temp/'structure.json',value)
        elif source.suffix.lower()=='.pdf':
            mode='native_pdf';candidate=None
            if config['mode']=='marker':
                from .marker_runtime import MarkerSession
                with MarkerSession(config['runtime_path'],temp/'worker',config['profile'],
                                   timeout=config['timeout_seconds'],interrupt=interrupt) as session:
                    execution=session.convert(original,temp/'marker')
                candidate=temp/'marker/chunks.json';mode='marker_chunks'
            paths=write_structure(source=original,source_sha256=source_sha,rawmd=raw,paper_id='E10',
                                  conversion_receipt_id='E10-'+digest(canonical(binding))[:24],mode=mode,marker_chunks_path=candidate,allow_marker_page_fallback=True)
            value,_=read_json(paths['structure_path'])
            if mode=='marker_chunks':
                _calibrate(value,original)
                value['provenance']='LOCAL_MARKER_EXECUTION_BOUND_TO_RUNTIME_LOCK'
                value['warnings']=[w for w in value['warnings'] if w!='EXTERNAL_MARKER_EXECUTION_NOT_VERIFIED']
                value['capabilities']['ocr']=not execution['config']['disable_ocr']
            else:
                for page in value['pages']:page['overlay_status']='CALIBRATED_UNROTATED_POINTS'
                for block in value['blocks']:
                    block['source_location']={'kind':'pdf','physical_page':block['physical_page'],
                        'bbox':block['bbox'],'coordinate_system':'pdf_unrotated_points'}
            value['structure_id']=structure_id(value);validate_structure(value)
            _json(temp/'structure.json',value)
        else:raise ValueError('STRUCTURE_FORMAT_UNSUPPORTED')
        if progress:progress('validate',{'blocks':len(value['blocks']),'tables':sum(len(b['tables']) for b in value['blocks'])})
        if interrupt:interrupt()
        if file_sha(source)!=source_sha or file_sha(rawmd)!=raw_sha:raise ValueError('STRUCTURE_INPUT_CHANGED')
        selected=[original,raw,temp/'structure.json']
        if execution:selected += [temp/'marker/chunks.json',temp/'marker/candidate.md',temp/'marker/execution.json']
        receipt={'schema_version':BUNDLE,'binding':binding,'structure_id':value['structure_id'],
                 'files':{p.relative_to(temp).as_posix():file_sha(p) for p in selected},
                 'source_file':original.name,'rawmd_file':raw.name,'structure_file':'structure.json',
                 'execution':execution,'status':'CANDIDATE_NOT_ADMITTED','external_cloud_calls':0}
        receipt['quality_policy']={'automatic_rawmd_replacement':False,'automatic_fact_admission':False,
                                   'recognition_accuracy':'NOT_GUARANTEED','review_required':True}
        _json(temp/'bundle.json',receipt)
        verify(temp);os.replace(temp,destination)
        if progress:progress('review',{'status':'CANDIDATE_NOT_ADMITTED'})
        return receipt
    except BaseException as error:
        _json(temp/'FAILED.json',{'status':'FAIL','error_type':type(error).__name__,'error':str(error)[:2000]})
        os.replace(temp,temp.with_name(temp.name.replace('.pending','.failed')))
        raise

def _sha(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-fA-F0-9]{64}',value):
        raise ValueError('STRUCTURE_BUNDLE_HASH_INVALID')
    return value.lower()

def _member(folder,name):
    # A selector and its manifest entry have one canonical local spelling.
    # Reject Windows drive/ADS, backslash and trailing-dot aliases as well.
    if (not isinstance(name,str) or not name or '\\' in name or ':' in name
            or '\x00' in name or any(p in {'','.','..'} or p!=p.rstrip(' .') for p in name.split('/'))):
        raise ValueError('STRUCTURE_BUNDLE_STALE:INVALID_MEMBER_PATH')
    candidate=folder
    try:
        for part in name.split('/'):
            candidate=candidate/part
            if candidate.is_symlink() or getattr(candidate.stat(),'st_file_attributes',0)&0x400:
                raise ValueError('STRUCTURE_BUNDLE_STALE:REPARSE_MEMBER')
        resolved=candidate.resolve(strict=True)
        if not resolved.is_relative_to(folder) or not resolved.is_file():
            raise ValueError('STRUCTURE_BUNDLE_STALE:MEMBER_NOT_LOCAL_FILE')
    except OSError as error:
        raise ValueError('STRUCTURE_BUNDLE_STALE:MISSING_MEMBER') from error
    return resolved

def _verified(folder):
    folder=Path(folder).resolve(strict=True)
    manifest=_member(folder,'bundle.json');receipt,_=read_json(manifest)
    fields={'schema_version','binding','structure_id','files','source_file','rawmd_file',
            'structure_file','execution','status','external_cloud_calls','quality_policy'}
    if (set(receipt)!=fields or receipt.get('schema_version')!=BUNDLE
            or receipt.get('status')!='CANDIDATE_NOT_ADMITTED'
            or type(receipt.get('external_cloud_calls')) is not int or receipt['external_cloud_calls']!=0
            or not isinstance(receipt.get('structure_id'),str)
            or not re.fullmatch(r'Structure-[a-f0-9]{32}',receipt['structure_id'])):
        raise ValueError('STRUCTURE_BUNDLE_SCHEMA_INVALID')
    policy=receipt['quality_policy']
    if (not isinstance(policy,dict) or set(policy)!={'automatic_rawmd_replacement','automatic_fact_admission','recognition_accuracy','review_required'}
            or policy['automatic_rawmd_replacement'] is not False or policy['automatic_fact_admission'] is not False
            or policy['review_required'] is not True or policy['recognition_accuracy']!='NOT_GUARANTEED'):
        raise ValueError('STRUCTURE_BUNDLE_SCHEMA_INVALID')
    binding=receipt['binding']
    if not isinstance(binding,dict) or set(binding)!={'source_sha256','rawmd_sha256','config'}:
        raise ValueError('STRUCTURE_BUNDLE_BINDING_MISMATCH')
    source_sha,raw_sha=_sha(binding['source_sha256']),_sha(binding['rawmd_sha256'])
    config=normalize_config(binding['config'],verify_runtime=False)
    if config!=binding['config'] or config['mode']=='off':
        raise ValueError('STRUCTURE_BUNDLE_BINDING_MISMATCH')
    if config['runtime_lock_sha256'] is not None:_sha(config['runtime_lock_sha256'])
    if config['mode']=='marker' and (not config['runtime_path'] or config['runtime_lock_sha256'] is None):
        raise ValueError('STRUCTURE_BUNDLE_BINDING_MISMATCH')
    files=receipt['files']
    if not isinstance(files,dict) or not files or len(files)>10000:
        raise ValueError('STRUCTURE_BUNDLE_SCHEMA_INVALID')
    roles=[receipt[key] for key in ['source_file','rawmd_file','structure_file']]
    if any(not isinstance(name,str) or name not in files for name in roles) or len(set(roles))!=3:
        raise ValueError('STRUCTURE_BUNDLE_ROLE_INVALID')
    if any(not isinstance(name,str) for name in files) or len({name.casefold() for name in files})!=len(files) or any(name.casefold()=='bundle.json' for name in files):
        raise ValueError('STRUCTURE_BUNDLE_ROLE_INVALID')
    members={name:_member(folder,name) for name in files}
    identities=[(members[name].stat().st_dev,members[name].stat().st_ino) for name in roles]
    if len(set(identities))!=3:raise ValueError('STRUCTURE_BUNDLE_ROLE_INVALID')
    # Parse the exact structure bytes whose digest was checked. load/preview
    # consume this object, never a second unchecked selector read.
    value,structure_bytes=read_json(members[receipt['structure_file']])
    observed={}
    for name,path in members.items():
        observed[name]=digest(structure_bytes) if name==receipt['structure_file'] else file_sha(path)
        if observed[name]!=_sha(files[name]):raise ValueError('STRUCTURE_BUNDLE_STALE:MEMBER_HASH')
    if observed[receipt['source_file']]!=source_sha or observed[receipt['rawmd_file']]!=raw_sha:
        raise ValueError('STRUCTURE_BUNDLE_BINDING_MISMATCH')
    suffix=members[receipt['source_file']].suffix.lower()
    if members[receipt['rawmd_file']].suffix.lower()!='.md' or members[receipt['structure_file']].suffix.lower()!='.json':
        raise ValueError('STRUCTURE_BUNDLE_ROLE_INVALID')
    if value.get('schema_version')==OFFICE_SCHEMA:
        if suffix not in {'.docx','.xlsx','.pptx'}:raise ValueError('STRUCTURE_BUNDLE_ROLE_INVALID')
        if value.get('structure_id')!=structure_id(value):raise ValueError('STRUCTURE_ID_MISMATCH')
    else:
        if suffix!='.pdf':raise ValueError('STRUCTURE_BUNDLE_ROLE_INVALID')
        validate_structure(value)
    if value['structure_id']!=receipt['structure_id'] or _sha(value['source_sha256'])!=source_sha or _sha(value['rawmd_sha256'])!=raw_sha:
        raise ValueError('STRUCTURE_BUNDLE_BINDING_MISMATCH')
    execution=receipt['execution']
    if config['mode']=='marker' and suffix=='.pdf':
        required={'marker/chunks.json','marker/candidate.md','marker/execution.json'}
        if not required<=set(files) or not isinstance(execution,dict):
            raise ValueError('STRUCTURE_BUNDLE_MARKER_EXECUTION_INVALID')
        disk_execution,raw_execution=read_json(members['marker/execution.json'])
        chunks,raw_chunks=read_json(members['marker/chunks.json'])
        expected_config={'mode':'balanced' if config['profile']=='balanced_ocr' else 'fast',
                         'disable_ocr':config['profile']=='fast_no_ocr','use_llm':False}
        if (digest(raw_execution)!=_sha(files['marker/execution.json']) or disk_execution!=execution
                or digest(raw_chunks)!=_sha(files['marker/chunks.json'])
                or execution.get('status')!='PASS' or execution.get('source_sha256')!=source_sha
                or execution.get('candidate_sha256')!=_sha(files['marker/chunks.json'])
                or execution.get('markdown_sha256')!=_sha(files['marker/candidate.md'])
                or execution.get('profile')!=config['profile'] or execution.get('config')!=expected_config
                or type(execution.get('cloud_calls')) is not int or execution['cloud_calls']!=0
                or not isinstance(execution.get('runtime'),dict)
                or execution['runtime'].get('lock_sha256')!=config['runtime_lock_sha256']
                or chunks.get('schema_version')!='MemoriveMarkerChunksEnvelope-v1'
                or chunks.get('source_sha256')!=source_sha or chunks.get('config')!=expected_config
                or chunks.get('engine')!='marker' or chunks.get('engine_version')!='2.0.0'):
            raise ValueError('STRUCTURE_BUNDLE_MARKER_EXECUTION_INVALID')
        _sha(execution.get('worker_sha256'));_sha(execution.get('guard_sha256'))
    elif execution is not None:raise ValueError('STRUCTURE_BUNDLE_MARKER_EXECUTION_INVALID')
    members['bundle.json']=manifest
    return receipt,value,members

def verify(folder):
    return _verified(folder)[0]

def load(folder):
    receipt,value,_=_verified(folder);return receipt,value

def preview(folder,block_id):
    receipt,value,members=_verified(folder);block=next((b for b in value['blocks'] if b['block_id']==block_id),None)
    if block is None:raise ValueError('STRUCTURE_BLOCK_NOT_FOUND')
    locator=block.get('source_location',{})
    result={'structure_id':value['structure_id'],'block_id':block_id,'source_sha256':value['source_sha256'],
            'source_location':locator,'text':block['text'],'tables':block['tables']}
    if locator.get('kind')=='pdf':
        import pymupdf
        source_bytes=members[receipt['source_file']].read_bytes()
        if digest(source_bytes)!=_sha(receipt['binding']['source_sha256']):raise ValueError('STRUCTURE_BUNDLE_STALE:PREVIEW_SOURCE')
        with pymupdf.open(stream=source_bytes,filetype='pdf') as doc:
            page=doc[locator['physical_page']-1]
            # The original page is rendered. Overlay is data separately, never drawn into the source.
            pix=page.get_pixmap(matrix=pymupdf.Matrix(min(1.5,1200/page.rect.width),min(1.5,1200/page.rect.width)),alpha=False)
            result['page_image']='data:image/png;base64,'+base64.b64encode(pix.tobytes('png')).decode('ascii')
            result['page_width']=page.rect.width;result['page_height']=page.rect.height
            box=locator.get('bbox')
            if box and locator['coordinate_system']=='pdf_unrotated_points':box=list(pymupdf.Rect(box)*page.rotation_matrix)
            result['overlay_bbox']=box
    return result

def context_pack(folder,confirmed,*,budget):
    receipt,value=load(folder)
    if type(budget) is not int or not 256<=budget<=100000:raise ValueError('STRUCTURE_CONTEXT_BUDGET_INVALID')
    wanted=set(confirmed)
    if len(wanted)!=len(confirmed):raise ValueError('STRUCTURE_DUPLICATE_SELECTION')
    blocks={b['block_id']:b for b in value['blocks']}
    if wanted-set(blocks):raise ValueError('STRUCTURE_BLOCK_NOT_FOUND')
    out={'schema_version':'MemoriveStructuredContext-v1','source_sha256':value['source_sha256'],
         'structure_id':value['structure_id'],'fragments':[],'omitted_block_ids':sorted(wanted),'truncated':bool(wanted)}
    if len(canonical(out).decode('utf-8'))>budget:raise ValueError('STRUCTURE_CONTEXT_BUDGET_TOO_SMALL')
    for key in confirmed:
        b=blocks[key];entry={'block_id':key,'source_location':b.get('source_location'), 'text':b['text'],'tables':b['tables']}
        candidate={**out,'fragments':[*out['fragments'],entry],
                   'omitted_block_ids':[k for k in out['omitted_block_ids'] if k!=key]}
        candidate['truncated']=bool(candidate['omitted_block_ids'])
        if len(canonical(candidate).decode('utf-8'))<=budget:out=candidate
    return out


def publication_specs(folder,destination):
    destination=Path(destination)
    receipt,_,members=_verified(folder)
    return tuple(('core-structure-'+digest(canonical([name,receipt['files'].get(name) or file_sha(members[name])]))[:20],
                  'CORE_STRUCTURE_CANDIDATE',members[name],destination/name,'结构候选 · '+Path(name).name)
                 for name in [*receipt['files'],'bundle.json'])
