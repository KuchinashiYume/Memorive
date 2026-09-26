"""Durable, single-attempt CLI verification. IPC lifetime is not model lifetime."""
from copy import deepcopy
import json
import re
import threading

from run_ledger.writer_lock import KernelFileLock
from .contracts import canonical_sha256, scan_sensitive, utc_now


class CliVerificationJobs:
    def __init__(self, store, execute, prepare=None):
        self.store, self.execute = store, execute
        self.root = store.profile_root / 'cli_verification_jobs'
        self.root.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.RLock()
        self.prepare=prepare

    def _path(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise ValueError('CLI_VERIFICATION_JOB_ID_INVALID')
        return self.root / (job_id + '.json')

    def _write(self, row):
        scan_sensitive(row)
        self.store._atomic_write(self._path(row['job_id']), row)

    def _read(self, job_id):
        return json.loads(self._path(job_id).read_text(encoding='utf-8'))

    def _lock(self):
        return KernelFileLock(self.root / 'worker.lock')

    def start(self, *, params, idempotency_key):
        if not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9-]{16,80}', idempotency_key):
            raise ValueError('CLI_VERIFICATION_IDEMPOTENCY_KEY_INVALID')
        job_id = canonical_sha256(idempotency_key)[:32].lower()
        request_hash = canonical_sha256(params)
        with self._mutex:
            if self._path(job_id).exists():
                row = self._read(job_id)
                if row['request_sha256'] != request_hash:
                    raise ValueError('CLI_VERIFICATION_IDEMPOTENCY_CONFLICT')
                return self.status(job_id=job_id)
            lock = self._lock()
            try:
                lock.acquire()
            except (OSError, RuntimeError) as error:
                raise ValueError('CLI_VERIFICATION_ALREADY_RUNNING') from error
            try:
                if self.prepare is not None:self.prepare(**params)
                row = dict(schema_version='CliVerificationJob-v1', job_id=job_id,
                           request_sha256=request_hash, config_id=params['config_id'],
                           profile_ref=params['profile_ref'], target_sha256=params['target_sha256'],
                           state='RUNNING', stage='WAITING_RESULT', started_at=utc_now(),
                           updated_at=utc_now(), receipt=None)
                self._write(row)
                threading.Thread(target=self._run, args=(job_id, deepcopy(params), lock),
                                 name='cli-verification-' + job_id, daemon=True).start()
                return deepcopy(row)
            except BaseException:
                lock.release()
                raise

    def _run(self, job_id, params, lock):
        try:
            from .cli_verification_transport import verification_context
            def progress(update):
                with self._mutex:
                    current=self._read(job_id)
                    current.update({k:v for k,v in update.items() if k in {'stage','elapsed_ms','silent_ms','slow_response','attempt_id'}})
                    if current.get('cancel_requested'):current['stage']='STOPPING'
                    current['updated_at']=utc_now()
                    self._write(current)
            def cancelled():
                with self._mutex:return self._read(job_id).get('cancel_requested') is True
            from .task_scheduling import TASK_CATEGORIES
            def checkpoint():
                if cancelled():raise ValueError('CLI_VERIFICATION_CANCELLED')
            progress({'stage':'QUEUED_TASK_CATEGORY'})
            with TASK_CATEGORIES.enter('MODEL_VERIFICATION', checkpoint=checkpoint), verification_context(progress,cancelled):
                receipt = self.execute(**params)
            with self._mutex:
                row = self._read(job_id)
                row.update(state='CANCELLED' if receipt.get('reason')=='CLI_VERIFICATION_CANCELLED' else 'COMPLETE', stage='FINISHED', receipt=receipt,
                           updated_at=utc_now(), finished_at=utc_now())
                self._write(row)
        except Exception as error:
            with self._mutex:
                row = self._read(job_id)
                row.update(state='CANCELLED' if row.get('cancel_requested') else 'ERROR', stage='FINISHED', error_type=type(error).__name__,
                           reason='CLI_VERIFICATION_CANCELLED' if row.get('cancel_requested') else 'CLI_VERIFICATION_NOT_ASSESSED', updated_at=utc_now())
                self._write(row)
        finally:
            lock.release()

    def cancel(self, *, job_id):
        with self._mutex:
            row=self._read(job_id)
            if row['state']=='RUNNING':
                row.update(cancel_requested=True,stage='STOPPING',updated_at=utc_now())
                self._write(row)
            return deepcopy(row)

    def status(self, *, job_id=None, profile_ref=None, idempotency_key=None):
        with self._mutex:
            if idempotency_key is not None:
                if job_id is not None or profile_ref is not None: raise ValueError('CLI_VERIFICATION_LOOKUP_AMBIGUOUS')
                if not isinstance(idempotency_key,str) or not re.fullmatch(r'[A-Za-z0-9-]{16,80}',idempotency_key):
                    raise ValueError('CLI_VERIFICATION_IDEMPOTENCY_KEY_INVALID')
                job_id = canonical_sha256(idempotency_key)[:32].lower()
                if not self._path(job_id).exists(): return {'state':'IDLE'}
            if job_id is None:
                rows = [json.loads(p.read_text(encoding='utf-8')) for p in self.root.glob('*.json')]
                rows = [r for r in rows if profile_ref is None or r['profile_ref'] == profile_ref]
                if not rows:
                    return {'state': 'IDLE'}
                row = max(rows, key=lambda r: (r['state'] == 'RUNNING', r['started_at']))
            else:
                row = self._read(job_id)
            if row['state'] == 'RUNNING':
                lock = self._lock()
                try:
                    lock.acquire()
                except (OSError, RuntimeError):
                    pass
                else:
                    try:
                        row.update(state='INTERRUPTED', stage='FINISHED', updated_at=utc_now(),
                                   reason='CLI_VERIFICATION_WORKER_LOST')
                        self._write(row)
                    finally:
                        lock.release()
            return deepcopy(row)
