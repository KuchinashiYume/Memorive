"""Metadata verification outlives a short IPC query; never auto-repeat a request."""
from copy import deepcopy
import json
import hashlib
import re
import threading
from m11_terminal_ledger.writer_lock import KernelFileLock
from .contracts import canonical_sha256, scan_sensitive, utc_now


class ApiVerificationJobs:
    def __init__(self, store, execute, prepare):
        self.store, self.execute, self.prepare = store, execute, prepare
        self.root = store.profile_root / 'api_verification_jobs'
        self.root.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.RLock()

    def _path(self, job_id):
        if not isinstance(job_id,str) or not re.fullmatch(r'[a-f0-9]{32}',job_id):
            raise ValueError('API_VERIFICATION_JOB_ID_INVALID')
        return self.root / (job_id + '.json')

    def _read(self, job_id):
        return json.loads(self._path(job_id).read_text(encoding='utf8'))

    def _write(self, row):
        scan_sensitive(row)
        self.store._atomic_write(self._path(row['job_id']),row)

    def _lock(self):
        return KernelFileLock(self.root / 'worker.lock')

    def start(self, *, params, idempotency_key):
        if not isinstance(idempotency_key,str) or not re.fullmatch(r'[A-Za-z0-9-]{16,80}',idempotency_key):
            raise ValueError('API_VERIFICATION_IDEMPOTENCY_KEY_INVALID')
        job_id = canonical_sha256(idempotency_key)[:32].lower()
        request_hash = canonical_sha256(params)
        with self._mutex:
            if self._path(job_id).exists():
                row = self._read(job_id)
                if row['request_sha256'] != request_hash: raise ValueError('API_VERIFICATION_IDEMPOTENCY_CONFLICT')
                return self.status(job_id=job_id)
            lock = self._lock()
            try: lock.acquire()
            except (OSError,RuntimeError) as exc: raise ValueError('API_VERIFICATION_ALREADY_RUNNING') from exc
            try:
                self.prepare(**params)
                row = dict(schema_version='ApiVerificationJob-v1', job_id=job_id,
                    request_sha256=request_hash, config_id=params['config_id'],
                    target_sha256=params['target_sha256'], state='RUNNING', stage='WAITING_RESULT',
                    started_at=utc_now(), updated_at=utc_now(), receipt=None)
                self._write(row)
                threading.Thread(target=self._run,args=(job_id,deepcopy(params),lock),
                    name='api-verification-'+job_id,daemon=True).start()
                return deepcopy(row)
            except BaseException:
                lock.release(); raise

    def _run(self, job_id, params, lock):
        try:
            receipt = self.execute(**params)
            with self._mutex:
                row = self._read(job_id)
                row.update(state='COMPLETE',stage='FINISHED',receipt=receipt,
                    updated_at=utc_now(),finished_at=utc_now())
                self._write(row)
        except Exception as exc:
            with self._mutex:
                row = self._read(job_id)
                safe_reasons = {
                    'API_VERIFICATION_TARGET_CHANGED':'API_VERIFICATION_TARGET_CHANGED',
                    'RETEST_MODEL_NOT_AUTHORIZED':'API_VERIFICATION_TEST_POLICY_BLOCKED',
                    'API_REASONING_EFFORT_UNSUPPORTED':'API_REASONING_EFFORT_UNSUPPORTED',
                }
                reason = safe_reasons.get(str(exc), 'API_VERIFICATION_INTERNAL_ERROR')
                row.update(state='ERROR',stage='FINISHED',reason=reason,
                    error_type=type(exc).__name__,error_sha256=hashlib.sha256(str(exc).encode()).hexdigest(),
                    updated_at=utc_now(),finished_at=utc_now())
                self._write(row)
        finally: lock.release()

    def status(self, *, job_id=None, config_id=None, idempotency_key=None):
        with self._mutex:
            if idempotency_key is not None:
                if job_id is not None or config_id is not None: raise ValueError('API_VERIFICATION_LOOKUP_AMBIGUOUS')
                if not isinstance(idempotency_key,str) or not re.fullmatch(r'[A-Za-z0-9-]{16,80}',idempotency_key):
                    raise ValueError('API_VERIFICATION_IDEMPOTENCY_KEY_INVALID')
                job_id = canonical_sha256(idempotency_key)[:32].lower()
                if not self._path(job_id).exists(): return {'state':'IDLE'}
            if job_id is None:
                rows = [json.loads(p.read_text(encoding='utf8')) for p in self.root.glob('*.json')]
                rows = [r for r in rows if config_id is None or r['config_id'] == config_id]
                if not rows: return {'state':'IDLE'}
                row = max(rows,key=lambda r:(r['state']=='RUNNING',r['started_at']))
            else: row = self._read(job_id)
            if row['state'] == 'RUNNING':
                lock = self._lock()
                try: lock.acquire()
                except (OSError,RuntimeError): pass
                else:
                    try:
                        row.update(state='INTERRUPTED',stage='FINISHED',reason='API_VERIFICATION_WORKER_LOST',updated_at=utc_now())
                        self._write(row)
                    finally: lock.release()
            return deepcopy(row)
