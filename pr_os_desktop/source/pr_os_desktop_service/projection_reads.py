"""Coalesce duplicate source reads within one UI query, never across queries."""
from contextvars import ContextVar
from functools import wraps

_reads = ContextVar('projection_reads', default=None)


def projection_request(method):
    @wraps(method)
    def query(*args, **kwargs):
        if _reads.get() is not None:
            return method(*args, **kwargs)
        token = _reads.set(set())
        try:
            return method(*args, **kwargs)
        finally:
            _reads.reset(token)
    return query


def once_per_projection(method):
    @wraps(method)
    def read(self, *args, **kwargs):
        reads = _reads.get()
        key = (id(self), method)
        if reads is not None and key in reads:
            return
        result = method(self, *args, **kwargs)
        if reads is not None:
            reads.add(key)
        return result
    return read
