"""FIFO task categories; only a category with node lanes may share a turn."""
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import threading
import os
import sqlite3
import time
from pathlib import Path
from .call_ledger import execution_checkpoint


class TaskCategories:
    def __init__(self):
        self.changed = threading.Condition()
        self.queue = deque()
        self.active = None
        self.owners = set()
        self.scope = ContextVar('memo_task_category', default=None)
        self.root = None

    def bind(self, profile_root):
        self.root = Path(profile_root).resolve() / 'task_scheduler'
        self.root.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.root / 'categories.sqlite3') as db:
            db.execute('CREATE TABLE IF NOT EXISTS queue (id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, pipeline INTEGER, pid INTEGER, marker TEXT, active INTEGER DEFAULT 0)')

    @contextmanager
    def _durable(self, root, category, pipeline, checkpoint):
        from run_ledger.writer_lock import process_start_marker, process_identity_status
        path = root / 'categories.sqlite3'
        with sqlite3.connect(path) as db:
            ticket = db.execute('INSERT INTO queue(category,pipeline,pid,marker) VALUES (?,?,?,?)',
                                (category, int(pipeline), os.getpid(), process_start_marker(os.getpid()))).lastrowid
        try:
            while True:
                checkpoint()
                with sqlite3.connect(path) as db:
                    db.execute('BEGIN IMMEDIATE')
                    rows = db.execute('SELECT id,category,pipeline,pid,marker,active FROM queue ORDER BY id').fetchall()
                    live = []
                    for row in rows:
                        if row[3] != os.getpid() and process_identity_status(row[3], row[4]) in {'DEAD', 'PID_REUSED'}:
                            db.execute('DELETE FROM queue WHERE id=?', (row[0],))
                        else:
                            live.append(row)
                    owners = [row for row in live if row[5]]
                    waiting = [row for row in live if not row[5]]
                    ready = bool(waiting and waiting[0][0] == ticket and (not owners or (
                        pipeline and all(row[1] == category and row[2] for row in owners))))
                    if ready:
                        db.execute('UPDATE queue SET active=1 WHERE id=?', (ticket,))
                if ready:
                    break
                time.sleep(.1)
            yield
        finally:
            with sqlite3.connect(path) as db:
                db.execute('DELETE FROM queue WHERE id=?', (ticket,))

    @contextmanager
    def enter(self, category, *, pipeline=False, checkpoint=execution_checkpoint):
        # Nested model calls belong to their enclosing job, never a new queue.
        if self.scope.get() is not None:
            checkpoint()
            yield
            return
        if self.root is not None:
            with self._durable(self.root, category, pipeline, checkpoint):
                token = self.scope.set(category)
                try:
                    checkpoint()
                    yield
                finally:
                    self.scope.reset(token)
            return
        ticket = object()
        with self.changed:
            self.queue.append(ticket)
        acquired = False
        token = None
        try:
            while not acquired:
                checkpoint()
                with self.changed:
                    if self.queue[0] is ticket and (
                        not self.owners or (pipeline and self.active == category)
                    ):
                        self.queue.popleft()
                        self.owners.add(ticket)
                        self.active = category
                        acquired = True
                        self.changed.notify_all()
                    else:
                        self.changed.wait(.1)
            token = self.scope.set(category)
            checkpoint()
            yield
        finally:
            if token is not None:
                self.scope.reset(token)
            with self.changed:
                if acquired:
                    self.owners.remove(ticket)
                    if not self.owners:
                        self.active = None
                else:
                    self.queue.remove(ticket)
                self.changed.notify_all()


_activity = ContextVar('memo_execution_activity', default=None)

@contextmanager
def activity_scope(observer):
    token = _activity.set(observer)
    try: yield
    finally: _activity.reset(token)

def execution_activity(state, node_id=None):
    observer = _activity.get()
    if observer is not None: observer(state, node_id)

TASK_CATEGORIES = TaskCategories()


def scheduled(category, *, pipeline=False):
    def decorate(fn):
        @wraps(fn)
        def run(*args, **kwargs):
            execution_activity('WAITING')
            with TASK_CATEGORIES.enter(category, pipeline=pipeline):
                execution_activity('RUNNING')
                return fn(*args, **kwargs)
        return run
    return decorate
