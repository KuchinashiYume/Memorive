"""Short kernel lock for the complete JSON read/modify/write transaction."""
import os,time
from contextlib import contextmanager

@contextmanager
def projection_lock(root):
    # This stable lock file is never replaced with the JSON document.
    with (root/'message_projection_store.lock').open('a+b') as stream:
        stream.seek(0,2)
        if stream.tell()==0:stream.write(b'0');stream.flush()
        deadline=time.monotonic()+30
        while True:
            stream.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic()>=deadline:raise TimeoutError('MESSAGE_STORE_LOCK_TIMEOUT')
                time.sleep(.02)
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
