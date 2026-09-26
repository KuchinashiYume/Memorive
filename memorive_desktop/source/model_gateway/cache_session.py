"""Bound Codex continuation; every operation still makes a fresh model request."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import time
import uuid

REVISION = 'CODEX_DOCUMENT_ROLE_SESSION_V2'
IDLE_TTL_SECONDS = 1800

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')

def sha(data):
    return hashlib.sha256(data).hexdigest()

def split_source(prompt):
    opening, closing = '<SOURCE_CONTEXT>\n', '\n</SOURCE_CONTEXT>'
    if prompt.count(opening) != 1 or prompt.count(closing) != 1:
        return None
    start = prompt.index(opening)
    stop = prompt.index(closing)
    if stop <= start + len(opening):
        return None
    stop += len(closing)
    return prompt[:stop], prompt[stop:]

def _read_record(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    checksum = value.pop('record_sha256')
    if sha(canonical(value)) != checksum:
        raise ValueError('CACHE_SESSION_RECORD_HASH_MISMATCH')
    return value

def _write_record(path, value):
    value = dict(value)
    value['record_sha256'] = sha(canonical(value))
    with path.open('xb') as stream:
        stream.write(canonical(value))

class Continuation:
    def __init__(self, workspace, model, prompt_bytes, schema, *, clock=time.time):
        self.workspace = Path(workspace).resolve()
        self.clock = clock
        self.enabled = model.get('prompt_cache_session') == REVISION
        self.resume_id = None
        self.sequence = None
        self.previous_usage = None
        self.attempt_usage = None
        self.binding = None
        self.prompt_bytes = prompt_bytes
        self.diagnostic = {'session_cache_mode': 'DISABLED', 'response_reused': False}
        if not self.enabled:
            return
        pieces = split_source(prompt_bytes.decode('utf-8'))
        if pieces is None:
            self.enabled = False
            self.diagnostic['session_cache_mode'] = 'SOURCE_BOUNDARY_UNAVAILABLE_FRESH_REQUEST'
            return
        prefix, suffix = pieces
        self.binding = {'revision': REVISION, 'workspace': str(self.workspace),
            'profile_sha256': sha(canonical(model)), 'source_prefix_sha256': sha(prefix.encode('utf-8')),
            'transport_schema_sha256': sha(canonical(schema))}
        records = sorted(self.workspace.glob('session-call-*.complete.json'))
        pending = sorted(self.workspace.glob('session-call-*.pending.json'))
        latest = _read_record(records[-1]) if records else None
        reason = 'COLD_START'
        if latest and latest['binding'] != self.binding:
            prior = latest['binding']
            # A portable profile may move with its immutable cache receipts.
            # Keep that evidence, but never resume a CLI thread in a different
            # workspace or subtract its cumulative usage from a fresh request.
            if (isinstance(prior, dict) and set(prior) == set(self.binding)
                    and isinstance(prior.get('workspace'), str) and prior['workspace']
                    and prior['workspace'] != self.binding['workspace']
                    and all(prior[key] == value for key, value in self.binding.items()
                            if key != 'workspace')):
                latest = None
                reason = 'WORKSPACE_RELOCATED_FRESH_REQUEST'
            else:
                raise ValueError('CACHE_SESSION_BINDING_MISMATCH')
        if latest:
            age = self.clock() - latest['completed_at_epoch']
            newer_pending = pending and pending[-1].name[:19] > records[-1].name[:19]
            if latest['status'] != 'SUCCESS' or newer_pending or not latest.get('cumulative_usage'):
                reason = 'PREVIOUS_ATTEMPT_FAILED_OR_UNCERTAIN'
            elif age < 0 or age >= IDLE_TTL_SECONDS:
                reason = 'LOCAL_REUSE_TTL_EXPIRED'
            else:
                self.resume_id = str(uuid.UUID(latest['thread_id']))
                self.previous_usage = latest['cumulative_usage']
                reason = 'EXACT_THREAD_CONTINUATION'
        indexes = [int(p.name.split('-')[2].split('.')[0]) for p in pending]
        self.sequence = max(indexes, default=0) + 1
        if self.resume_id:
            text = ('Continue the same document and role. The exact SOURCE_CONTEXT in the earlier '
                'message remains the source evidence. Treat earlier answers as drafts, not as '
                'evidence. Apply only the current TASK and its requested result contract.\n' + suffix)
        else:
            text = prompt_bytes.decode('utf-8')
        text += ('\n\nTransport envelope instruction: return one JSON object with exactly the key '
            '"payload". Its value must be the JSON object requested by the current task and '
            'current result schema above. Do not return a different task result merely because '
            'the transport union permits it. Do not quote or stringify the payload object.')
        self.prompt_bytes = text.encode('utf-8')
        self.diagnostic = {'session_cache_mode': reason, 'resume_thread_id': self.resume_id,
            'source_prefix_sha256': self.binding['source_prefix_sha256'],
            'logical_prompt_sha256': sha(prompt_bytes), 'wire_stdin_sha256': sha(self.prompt_bytes),
            'source_prefix_chars': len(prefix), 'wire_stdin_chars': len(text),
            'logical_prompt_chars': len(prompt_bytes.decode('utf-8')),
            'local_idle_ttl_seconds': IDLE_TTL_SECONDS, 'server_ttl_status': 'PROVIDER_MANAGED_UNKNOWN',
            'response_reused': False}

    def prepare_argv(self, argv):
        if not self.enabled:
            return argv
        if argv[-1] != '-' or 'resume' in argv or '--last' in argv:
            raise ValueError('CACHE_SESSION_ARGV_INVALID')
        result = [part for part in argv[:-1] if part != '--ephemeral']
        # Native CLI manages its existing account/session store. Never search
        # unrelated history or create a fresh SQLite backfill for each paper/role.
        for value in ('history.persistence="none"', 'features.memories=false',
                'features.apps=false', 'features.plugins=false', 'features.hooks=false',
                'features.shell_snapshot=false'):
            result.extend(('--config', value))
        if self.resume_id:
            result.extend(('resume', self.resume_id))
        result.append('-')
        _write_record(self.workspace / f'session-call-{self.sequence:06d}.pending.json',
            {'binding': self.binding, 'resume_thread_id': self.resume_id, 'started_at_epoch': self.clock(),
             'argv_sha256': sha(canonical(result)), 'wire_stdin_sha256': sha(self.prompt_bytes)})
        return tuple(result)

    def complete(self, process, response, source_schema, *, events_accepted=True):
        if not self.enabled:
            return
        import jsonschema
        events = []
        for line in process.stdout.decode('utf-8', errors='replace').splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        thread_ids = {e['thread_id'] for e in events if e.get('type') == 'thread.started' and e.get('thread_id')}
        completed = [e for e in events if e.get('type') == 'turn.completed']
        status = 'FAILED'
        cumulative = completed[0].get('usage') if len(completed) == 1 else None
        keys = ('input_tokens', 'cached_input_tokens', 'output_tokens')
        valid_usage = isinstance(cumulative, dict) and all(type(cumulative.get(k)) is int and cumulative[k] >= 0 for k in keys)
        if valid_usage and self.previous_usage and not set(self.previous_usage).issubset(cumulative):
            valid_usage = False
        if valid_usage:
            self.attempt_usage = {}
            for key, value in cumulative.items():
                if type(value) is not int or value < 0:
                    valid_usage = False
                    break
                prior = (self.previous_usage or {}).get(key, 0)
                if type(prior) is not int or value < prior:
                    valid_usage = False
                    break
                self.attempt_usage[key] = value - prior
            if valid_usage and self.attempt_usage['cached_input_tokens'] > self.attempt_usage['input_tokens']:
                valid_usage = False
        if not valid_usage:
            self.attempt_usage = None
        self.diagnostic.update(cli_usage_basis='SESSION_CUMULATIVE_DIFFERENCE' if self.resume_id else 'FIRST_SESSION_TURN',
            cli_cumulative_usage=cumulative, cli_attempt_usage=self.attempt_usage,
            cli_usage_status='VERIFIED_COUNTER_DIFFERENCE' if valid_usage else 'COUNTER_MISSING_OR_INCONSISTENT',
            native_session_store='EXISTING_CLI_STORE_EXACT_NEW_SESSION_IDS_ONLY')
        thread_id = next(iter(thread_ids)) if len(thread_ids) == 1 else None
        if (events_accepted and valid_usage and process.started and process.returncode == 0 and not process.timed_out
                and not process.output_truncated and len(completed) == 1 and thread_id):
            try:
                uuid.UUID(thread_id)
                if self.resume_id and thread_id != self.resume_id:
                    raise ValueError('CACHE_SESSION_RETURNED_THREAD_MISMATCH')
                if not isinstance(response, dict) or set(response) != {'payload'}:
                    raise ValueError('CACHE_SESSION_ENVELOPE_INVALID')
                jsonschema.validate(response['payload'], source_schema)
                status = 'SUCCESS'
            except (ValueError, jsonschema.ValidationError):
                status = 'FAILED'
        _write_record(self.workspace / f'session-call-{self.sequence:06d}.complete.json',
            {'binding': self.binding, 'status': status, 'thread_id': thread_id, 'cumulative_usage': cumulative,
             'completed_at_epoch': self.clock(), 'stdout_sha256': sha(process.stdout)})
        self.diagnostic.update(session_record_status=status, native_thread_id=thread_id)
