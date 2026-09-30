"""Marker 2 JSON-lines worker. Runs only in the separately locked environment."""
from __future__ import annotations
import contextlib, hashlib, importlib.metadata, json, os
from pathlib import Path
import socket, subprocess, sys, time, traceback, urllib.request

PROTOCOL_OUT = sys.stdout
CHILDREN = []
HANDLES = []

def emit(value):
    PROTOCOL_OUT.write(json.dumps(value, ensure_ascii=False, allow_nan=False)+'\n')
    PROTOCOL_OUT.flush()

def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); return sock.getsockname()[1]

def server(command, port, name, work):
    log=(work/(name+'.log')).open('ab'); HANDLES.append(log)
    proc=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    CHILDREN.append(proc)
    deadline=time.monotonic()+240
    while time.monotonic()<deadline:
        if proc.poll() is not None:
            raise RuntimeError(name+'_SERVER_EXITED:'+str(proc.returncode))
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=1) as response:
                if response.status==200: return
        except (OSError, ValueError): pass
        time.sleep(.2)
    raise TimeoutError(name+'_SERVER_STARTUP_TIMEOUT')

def start(rt, work, profile):
    if os.environ.get('E10_LOCAL_WORKER') != '1':
        raise RuntimeError('OFFLINE_GUARD_REQUIRED')
    for name,version in [('marker-pdf','2.0.0'),('surya-ocr','0.22.1')]:
        if importlib.metadata.version(name)!=version:
            raise RuntimeError('RUNTIME_VERSION_MISMATCH:'+name)
    lock=json.loads((rt/'RUNTIME_LOCK.json').read_text('utf-8'))
    for item in lock['packages']:
        if importlib.metadata.version(item['name'])!=item['version']:
            raise RuntimeError('RUNTIME_DEPENDENCY_MISMATCH:'+item['name'])
    os.environ.update(TORCH_DEVICE='cpu', FAST_DETECTOR_DEVICE='cpu',
        FAST_LAYOUT_MODEL_CHECKPOINT=str(rt/'models/layout'),
        FAST_ORDER_MODEL_CHECKPOINT=str(rt/'models/layout/order'),
        OCR_ERROR_MODEL_CHECKPOINT=str(rt/'models/ocr-error'),
        MODEL_CACHE_DIR=str(rt/'models'), FONT_PATH=str(rt/'models/fonts/GoNotoCurrent-Regular.ttf'),
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        SURYA_INFERENCE_AUTOSTART='false', FAST_LAYOUT_SERVER_AUTOSTART='false',
        OCR_ERROR_SERVER_AUTOSTART='false', DISABLE_TQDM='true',
        FAST_LAYOUT_NUM_THREADS='4', OMP_NUM_THREADS='4', MKL_NUM_THREADS='4')
    layout, error=free_port(),free_port()
    os.environ['FAST_LAYOUT_SERVER_URL']=f'http://127.0.0.1:{layout}'
    os.environ['OCR_ERROR_SERVER_URL']=f'http://127.0.0.1:{error}'
    server([sys.executable,'-m','surya.fast_layout.server','--port',str(layout)],layout,'layout',work)
    server([sys.executable,'-m','surya.ocr_error.server','--port',str(error)],error,'ocr_error',work)
    if profile in {'fast_ocr','balanced_ocr'}:
        port=free_port()
        binaries=list((rt/'llamacpp').rglob('llama-server.exe'))
        if len(binaries)!=1: raise RuntimeError('LLAMA_SERVER_MISSING_OR_AMBIGUOUS')
        os.environ['SURYA_INFERENCE_URL']=f'http://127.0.0.1:{port}/v1'
        os.environ['SURYA_INFERENCE_BACKEND']='llamacpp'
        os.environ['SURYA_INFERENCE_PARALLEL']='1'
        server([str(binaries[0]),'-m',str(rt/'models/surya2/surya-2.gguf'),
                '--mmproj',str(rt/'models/surya2/surya-2-mmproj.gguf'),
                '-ngl','99','--host','127.0.0.1','--port',str(port),
                '--parallel','1','--ctx-size','16384','--alias','datalab-to/surya-ocr-2','--jinja'],
               port,'llama',work)
    from marker.models import create_model_dict
    return create_model_dict(inference_backend='llamacpp')

def recover_image_only_text(document, models):
    """One bounded second reading of image-only Text, never a value correction.

    Keep the first HTML and every attempted result in the source-bound envelope.
    A recovered candidate is still unverified and never replaces RawMD.
    The normal page OCR can return <img/> for an entire partly obscured line.
    Restrict this fallback to a declared Text block with no transcribed text;
    real figures, empty headers and merely surprising values are not triggers.
    """
    from bs4 import BeautifulSoup
    from marker.builders.ocr import OcrBuilder
    from marker.schema import BlockTypes
    from marker.schema.labels import block_type_to_surya_label
    from surya.layout.schema import LayoutBox, LayoutResult
    audit=[];requests=0
    cleaner=OcrBuilder(models['recognition_model'])
    for page in document.pages:
        for block in page.structure_blocks(document):
            original=getattr(block,'html',None) or ''
            soup=BeautifulSoup(original,'html.parser')
            if block.block_type!=BlockTypes.Text or soup.get_text(strip=True) or not soup.find('img'):
                continue
            row={'upstream_id':str(block.id),'original_html':original,
                 'status':'UNTRANSCRIBED','additional_ocr_requests':0}
            audit.append(row)
            if requests>=4:
                row['status']='RECOVERY_LIMIT_REACHED';continue
            image=page.get_image(highres=True)
            polygon=block.polygon.rescale(page.polygon.size,image.size).fit_to_bounds((0,0,*image.size))
            layout=LayoutResult(bboxes=[LayoutBox(polygon=polygon.polygon,
                label=block_type_to_surya_label(block.block_type),raw_label=str(block.block_type),
                position=0,count=block.layout_token_count or 1200)],image_bbox=[0,0,*image.size])
            row['additional_ocr_requests']=1
            requests+=1
            result=models['recognition_model'](images=[image],layout_results=[layout],full_page=False)
            if len(result)!=1 or len(result[0].blocks)!=1:
                row['status']='RECOVERY_INVALID_RESPONSE';continue
            candidate=result[0].blocks[0]
            row['candidate_html']=candidate.html
            if candidate.error or candidate.skipped:
                row['status']='RECOVERY_FAILED';continue
            html=cleaner.clean_html(candidate.html)
            text=BeautifulSoup(html,'html.parser').get_text(strip=True)
            if not text:
                row['status']='RECOVERY_NO_TEXT';continue
            block.html=html
            row['status']='RECOVERED_CANDIDATE_REVIEW_REQUIRED'
    return audit

def convert(request, models, profile):
    from marker.converters.pdf import PdfConverter
    from marker.renderers.chunk import ChunkRenderer
    from marker.renderers.markdown import MarkdownRenderer
    source=Path(request['source']).resolve()
    dest=Path(request['destination']).resolve()
    if not source.is_file() or source.stat().st_size>512*1024*1024:
        raise ValueError('INPUT_MISSING_OR_TOO_LARGE')
    if source.suffix.lower() not in {'.pdf','.png','.jpg','.jpeg','.tif','.tiff','.webp'}:
        raise ValueError('MARKER_INPUT_UNSUPPORTED')
    if dest.exists(): raise ValueError('OUTPUT_ALREADY_EXISTS')
    digest=sha(source)
    if digest != request['source_sha256']: raise ValueError('SOURCE_HASH_MISMATCH')
    config={'mode':'balanced' if profile=='balanced_ocr' else 'fast',
            'disable_ocr':profile=='fast_no_ocr','use_llm':False}
    # Physical page anchors live in chunks.json. Renderer-generated separators
    # are presentation metadata and must not become transcribed source text.
    actual={**config,'disable_image_extraction':True,'paginate_output':False}
    converter=PdfConverter(artifact_dict=models,config=actual)
    t=time.perf_counter()
    document=converter.build_document(str(source))
    recovery=[] if config['disable_ocr'] else recover_image_only_text(document,models)
    chunks=ChunkRenderer(actual)(document).model_dump(mode='json')
    markdown=MarkdownRenderer(actual)(document).markdown
    if sha(source)!=digest: raise ValueError('SOURCE_CHANGED_DURING_CONVERSION')
    value={'schema_version':'MemoriveMarkerChunksEnvelope-v1','source_sha256':digest,
           'engine':'marker','engine_version':'2.0.0','config':config,'chunks':chunks,
           'recognition_audit':{'version':'MemoriveTextRecovery-v1','attempts':recovery}}
    encoded=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
    if len(encoded)>32*1024*1024 or len(markdown.encode('utf-8'))>32*1024*1024:
        raise ValueError('MARKER_OUTPUT_TOO_LARGE')
    dest.mkdir(parents=True,exist_ok=False)
    (dest/'chunks.json').write_bytes(encoded)
    (dest/'candidate.md').write_text(markdown,encoding='utf-8')
    result={'status':'PASS','source_sha256':digest,'page_count':len(document.pages),
            'block_count':len(chunks['blocks']),'conversion_seconds':time.perf_counter()-t,
            'candidate_sha256':sha(dest/'chunks.json'),'markdown_sha256':sha(dest/'candidate.md'),
            'config':config,'profile':profile,'model_calls':'LOCAL_INFERENCE',
            'additional_recovery_ocr_requests':sum(r['additional_ocr_requests'] for r in recovery),
            'cloud_calls':0,'python_network_policy':'LOOPBACK_ONLY_AUDIT_HOOK',
            'llama_network_policy':'LOCAL_FILES_LOOPBACK_BIND_NO_REMOTE_FLAGS',
            'server_pids':[p.pid for p in CHILDREN]}
    (dest/'execution.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result

def main():
    rt,work,profile=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]
    if profile not in {'fast_no_ocr','fast_ocr','balanced_ocr'}: raise ValueError('PROFILE_INVALID')
    work.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter(); models=start(rt,work,profile)
    emit({'event':'ready','startup_seconds':time.perf_counter()-started,'pid':os.getpid(),
          'server_pids':[p.pid for p in CHILDREN]})
    for line in sys.stdin:
        if len(line)>65536: raise ValueError('REQUEST_TOO_LARGE')
        request=json.loads(line)
        if request.get('operation')=='shutdown': break
        try:
            emit({'event':'result','request_id':request['request_id'],**convert(request,models,profile)})
        except Exception as error:
            traceback.print_exc()
            emit({'event':'result','request_id':request.get('request_id'),'status':'FAIL',
                  'error_type':type(error).__name__,'error':str(error)[:2000]})

if __name__=='__main__':
    sys.stdout = sys.stderr
    try: main()
    except Exception as error:
        traceback.print_exc(); emit({'event':'fatal','error':str(error)[:2000]}); sys.exit(2)
    finally:
        for proc in reversed(CHILDREN):
            if proc.poll() is None: proc.terminate()
        for proc in reversed(CHILDREN):
            try: proc.wait(timeout=10)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=10)
        for handle in HANDLES: handle.close()
