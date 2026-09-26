"""Per-node pipeline lanes with cooperative cancellation and task-local ownership."""
from contextlib import contextmanager
from collections import deque
import threading
import time
from pr_os_settings.call_ledger import execution_checkpoint


class PipelineLanes:
    def __init__(self, *, card_release_seconds: float = 15.0):
        self.card_release_seconds = max(0.0, float(card_release_seconds))
        self._guard = threading.Lock()
        self._changed = threading.Condition(self._guard)
        self._queues = {}
        self._owners = {}
        self._ready_at = {}
        self._papers = {}

    @staticmethod
    def acquire(lock):
        while not lock.acquire(timeout=.1):
            execution_checkpoint()
        try:
            execution_checkpoint()
        except BaseException:
            lock.release()
            raise

    @contextmanager
    def paper(self, identity):
        with self._guard:
            lock, count = self._papers.get(identity, (threading.Lock(), 0))
            self._papers[identity] = (lock, count + 1)
        acquired = False
        try:
            self.acquire(lock); acquired = True
            yield
        finally:
            if acquired: lock.release()
            with self._guard:
                _, count = self._papers[identity]
                if count == 1: self._papers.pop(identity)
                else: self._papers[identity] = (lock, count - 1)

    def enter(self, node_id):
        # Register before a cooperative checkpoint: a paused or descheduled
        # earlier waiter must not lose its place to a later task.
        ticket = object()
        with self._changed:
            self._queues.setdefault(node_id, deque()).append(ticket)
        try:
            while True:
                # Never run task callbacks under the shared scheduling lock.
                execution_checkpoint()
                with self._changed:
                    queue = self._queues[node_id]
                    delay = self._ready_at.get(node_id, 0) - time.monotonic()
                    if (queue[0] is ticket and node_id not in self._owners
                            and delay <= 0):
                        queue.popleft()
                        if not queue:
                            del self._queues[node_id]
                        self._owners[node_id] = ticket
                        return ticket
                    self._changed.wait(timeout=min(.1, delay) if delay > 0 else .1)
        except BaseException:
            with self._changed:
                queue = self._queues.get(node_id)
                if queue is not None:
                    queue.remove(ticket)
                    if not queue:
                        del self._queues[node_id]
                self._changed.notify_all()
            raise

    def leave(self, node_id, lock):
        with self._changed:
            if node_id not in self._owners or self._owners[node_id] is not lock:
                raise ValueError('PIPELINE_LANE_OWNER_MISMATCH')
            del self._owners[node_id]
            self._ready_at[node_id] = time.monotonic() + (
                self.card_release_seconds if node_id == '03_CARD_DISTILL' else 0)
            self._changed.notify_all()


PIPELINE_LANES = PipelineLanes()
