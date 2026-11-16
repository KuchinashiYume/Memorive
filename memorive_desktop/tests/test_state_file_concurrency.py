"""Hold a real reader open while the polling writer tries atomic replacement."""
import threading
from pathlib import Path


def test_research_state_read_and_atomic_replace_are_serialized(tmp_path,monkeypatch):
    from memorive.research import common
    target=tmp_path/'state.json'
    common.write(target,common.sealed({'state':'RUNNING'}))
    opened=threading.Event();release=threading.Event();writer_started=threading.Event()
    original_open=Path.open;original_replace=common.os.replace
    conflicts=[];errors=[]
    class HeldReader:
        def __init__(self,stream):self.stream=stream
        def __enter__(self):
            opened.set()
            if not release.wait(5):raise TimeoutError('TEST_READER_NOT_RELEASED')
            return self.stream
        def __exit__(self,*args):self.stream.close()
    def held_open(path,*args,**kwargs):
        stream=original_open(path,*args,**kwargs)
        return HeldReader(stream) if path==target and threading.current_thread().name=='held-reader' else stream
    def checked_replace(source,destination):
        if Path(destination)==target and not release.is_set():conflicts.append('replace while reader open')
        return original_replace(source,destination)
    monkeypatch.setattr(Path,'open',held_open);monkeypatch.setattr(common.os,'replace',checked_replace)
    def reader():
        try:common.read(target)
        except Exception as exc:errors.append(type(exc).__name__)
    def writer():
        writer_started.set()
        try:common.write(target,common.sealed({'state':'COMPLETE'}))
        except Exception as exc:errors.append(type(exc).__name__)
    r=threading.Thread(target=reader,name='held-reader');w=threading.Thread(target=writer,name='atomic-writer')
    try:
        r.start();assert opened.wait(5);w.start();assert writer_started.wait(5)
        # Test observation window only; no product retry/delay is introduced.
        w.join(.2)
    finally:
        release.set();r.join(5);w.join(5)
    assert not r.is_alive() and not w.is_alive()
    assert not conflicts and not errors,(conflicts,errors)
    assert common.read(target)['state']=='COMPLETE'
