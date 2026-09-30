"""Keep native PDF CPU work outside the desktop service's control process.

Only the existing local converter runs here. OCR, model calls, durable product
artifacts and task state stay with the parent. The owned child can be discarded
on cancellation without interrupting a write to a completed source checkpoint.
"""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

MODE_ARG = '--memo-native-pdf'


def _digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _command(request, response):
    if getattr(sys, 'frozen', False):
        return [sys.executable, MODE_ARG, str(request), str(response)]
    return [sys.executable, '-B', '-m', __name__, str(request), str(response)]


def _wait_owned(command, *, environment, interrupt):
    from model_gateway.execution_core.runner import _TreeController
    from memorive_settings.cli_verification_transport import resume_owned_process, stop_owned_tree
    tree = _TreeController()
    process = None
    try:
        if interrupt is not None:
            interrupt()
        process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False,
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 4)
                if os.name == 'nt' else 0, start_new_session=os.name != 'nt')
        tree.attach(process)
        resume_owned_process(process)
        while True:
            if interrupt is not None:
                interrupt()
            try:
                return process.wait(timeout=.1)
            except subprocess.TimeoutExpired:
                pass
    finally:
        if process is not None and process.poll() is None:
            stop_owned_tree(tree, process)
        tree.close()


def convert_isolated(raw):
    from ..types import ConvertReport
    from memorive_settings.call_ledger import execution_checkpoint, execution_interrupt_callback
    execution_checkpoint()
    source = Path(raw.path).resolve(strict=True)
    source_sha = _digest(source)
    payload = {'schema_version': 'NativePdfConversion-v1', 'source': str(source),
        'source_sha256': source_sha, 'meta': asdict(raw.meta)}
    environment = dict(os.environ)
    # Propagate the running candidate's import roots, not another installed build.
    environment['PYTHONPATH'] = os.pathsep.join(str(p) for p in sys.path if p)
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    with tempfile.TemporaryDirectory(prefix='memorive-native-pdf-') as folder:
        request, response = Path(folder)/'request.json', Path(folder)/'response.json'
        request.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf8')
        request_sha = _digest(request)
        code = _wait_owned(_command(request, response), environment=environment,
            interrupt=execution_interrupt_callback())
        execution_checkpoint()
        if not response.is_file():
            raise RuntimeError(f'PDF_NATIVE_WORKER_NO_RESULT:{code}')
        result = json.loads(response.read_text('utf8'))
        if result.get('request_sha256') != request_sha or _digest(source) != source_sha:
            raise RuntimeError('PDF_NATIVE_WORKER_SOURCE_BINDING_INVALID')
        if code != 0 or result.get('status') != 'PASS':
            raise RuntimeError('PDF_NATIVE_WORKER_FAILED:' + str(result.get('error_type', code))
                + ':' + str(result.get('error_message', '')))
        markdown = result['markdown']
        if hashlib.sha256(markdown.encode('utf8')).hexdigest() != result['markdown_sha256']:
            raise RuntimeError('PDF_NATIVE_WORKER_OUTPUT_BINDING_INVALID')
        return markdown, ConvertReport(**result['report'])


def main(argv=None):
    from ..types import IngestMeta, RawObject
    from .pymupdf_engine import PymupdfLightEngine
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        raise ValueError('PDF_NATIVE_WORKER_ARGUMENTS_INVALID')
    request, response = map(Path, args)
    result = {'request_sha256': _digest(request), 'status': 'ERROR'}
    code = 1
    try:
        payload = json.loads(request.read_text('utf8'))
        source = Path(payload['source']).resolve(strict=True)
        if payload.get('schema_version') != 'NativePdfConversion-v1' or _digest(source) != payload['source_sha256']:
            raise ValueError('PDF_NATIVE_WORKER_INPUT_BINDING_INVALID')
        raw = RawObject(path=source, meta=IngestMeta(**payload['meta']))
        markdown, report = PymupdfLightEngine().convert_in_process(raw)
        if _digest(source) != payload['source_sha256']:
            raise ValueError('PDF_NATIVE_WORKER_INPUT_CHANGED')
        result.update(status='PASS', markdown=markdown, report=asdict(report),
            markdown_sha256=hashlib.sha256(markdown.encode('utf8')).hexdigest())
        code = 0
    except Exception as error:
        result['error_type'] = type(error).__name__
        result['error_message'] = str(error)
    with response.open('x', encoding='utf8') as stream:
        json.dump(result, stream, ensure_ascii=False)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
