"""Observe a saved Codex executable before image dispatch; explicit pins stay strict."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import uuid4


FIELDS = ('cli_binary_sha256', 'cli_version', 'cli_identity_authority_ref')


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest().upper()


def bind_saved_cli_identity(service, executable, *, process, environment, root):
    # Any explicit pin (even partial/invalid) is checked by the existing caller.
    # Never replace a supplied pin with the currently installed binary.
    if any(key in service for key in FIELDS):
        return dict(service), 0
    root = Path(root) / 'cli_image_identity'
    root.mkdir(parents=True, exist_ok=True)
    identity = 'identity-' + uuid4().hex
    before = _sha(executable)
    pre = {'schema_version': 'CliImageIdentityObservation-v1', 'identity': identity,
           'recorded_at': datetime.now(timezone.utc).isoformat(),
           'binary_sha256': before, 'adapter_id': 'codex_cli',
           'operation': 'LOCAL_VERSION_ONLY', 'model_calls': 0}
    def write(phase, value):
        import os
        with (root / (identity + '.' + phase + '.json')).open('x', encoding='utf8') as f:
            json.dump(value, f, ensure_ascii=False, sort_keys=True)
            f.flush(); os.fsync(f.fileno())
    write('pre', pre)
    workspace = root / identity
    workspace.mkdir(exist_ok=False)
    observed = process.run(argv=[executable, '--version'], cwd=str(workspace),
        environment=environment, stdin_bytes=b'', timeout_seconds=10, shell=False)
    if (not observed.started or observed.timed_out or observed.output_truncated
            or observed.returncode != 0):
        write('post', {**pre, 'status': 'FAILED', 'reason': 'VERSION_PROBE_FAILED'})
        raise ValueError('CLI_OCR_VERSION_PROBE_FAILED')
    try:
        version = observed.stdout.decode('utf8', errors='strict').strip()
    except UnicodeError:
        version = ''
    if not re.fullmatch(r'codex-cli [0-9][A-Za-z0-9.+_-]{0,100}', version):
        write('post', {**pre, 'status': 'FAILED', 'reason': 'VERSION_INVALID'})
        raise ValueError('CLI_OCR_VERSION_EVIDENCE_INVALID')
    if _sha(executable) != before:
        write('post', {**pre, 'status': 'FAILED', 'reason': 'BINARY_CHANGED'})
        raise ValueError('CLI_OCR_BINARY_IDENTITY_BINDING_INVALID')
    write('post', {**pre, 'status': 'PASS', 'cli_version': version})
    binding = hashlib.sha256((before + '\n' + version).encode('utf8')).hexdigest().upper()
    return {**service, 'executable': executable, 'cli_binary_sha256': before,
            'cli_version': version,
            'cli_identity_authority_ref': 'Desktop_LOCAL_CLI_IDENTITY_OBSERVATION_V1:' + binding}, 1
