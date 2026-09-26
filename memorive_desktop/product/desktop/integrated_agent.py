"""Inherited stdio for the windowed Memorive executable; never allocates a console."""
import os,sys,io

def attach_stdio():
    if os.name!='nt':return
    import ctypes,msvcrt
    from ctypes import wintypes
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.GetStdHandle.argtypes=[wintypes.DWORD];k.GetStdHandle.restype=wintypes.HANDLE
    k.GetCurrentProcess.restype=wintypes.HANDLE
    k.DuplicateHandle.argtypes=[wintypes.HANDLE,wintypes.HANDLE,wintypes.HANDLE,ctypes.POINTER(wintypes.HANDLE),wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    k.DuplicateHandle.restype=wintypes.BOOL
    for attr,number,mode in [('stdin',-10,'r'),('stdout',-11,'w'),('stderr',-12,'w')]:
        stream=getattr(sys,attr)
        if stream is not None and not stream.closed:continue
        handle=k.GetStdHandle(wintypes.DWORD(number));duplicate=wintypes.HANDLE()
        if not handle or handle==wintypes.HANDLE(-1).value:raise RuntimeError('MEMO_STDIO_REQUIRED')
        process=k.GetCurrentProcess()
        if not k.DuplicateHandle(process,handle,process,ctypes.byref(duplicate),0,False,2):raise ctypes.WinError(ctypes.get_last_error())
        fd=msvcrt.open_osfhandle(duplicate.value,os.O_BINARY|(os.O_RDONLY if mode=='r' else os.O_WRONLY))
        setattr(sys,attr,io.TextIOWrapper(os.fdopen(fd,mode+'b'),encoding='utf-8',newline='\n',write_through=True))

def _run():
    attach_stdio()
    from memorive_research_workspace.agent_entry import main as run
    sys.argv=[sys.argv[0]]+[v for v in sys.argv[1:] if v!='--memo-agent']
    return run()

def main():
    from memorive_research_workspace.agent_entry import entrypoint
    return entrypoint(_run)
