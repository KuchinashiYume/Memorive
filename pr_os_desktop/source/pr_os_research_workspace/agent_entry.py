"""Integrated stdio entry for source Python and Memorive.exe --memo-agent."""
import argparse,json,re,sys
from pathlib import Path

def main():
    for stream in (sys.stdin,sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    if getattr(sys,'frozen',False):
        source=Path(sys.executable).parent/'_internal/source'
    else:source=Path(__file__).resolve().parents[1]
    if str(source) not in sys.path:sys.path.insert(0,str(source))
    from pr_os_research_workspace.service import ResearchWorkspace
    from pr_os_research_workspace.agent_protocol import serve,validate_arguments
    parser=argparse.ArgumentParser(description='Memorive research tools: read evidence and propose drafts')
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--client',help='Desktop-approved local client id; selects API v1 permissions')
    mode=parser.add_mutually_exclusive_group(required=True);mode.add_argument('--mcp',action='store_true');mode.add_argument('--call');mode.add_argument('--connector-call')
    args=parser.parse_args()
    # A misspelled workspace must not silently create an empty alternate knowledge base.
    if not (args.workspace/'research_workspace_v1.sqlite3').is_file():raise ValueError('MEMO_WORKSPACE_NOT_INITIALIZED')
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stdin,'reconfigure'):sys.stdin.reconfigure(encoding='utf-8')
    ws=ResearchWorkspace(args.workspace)
    try:
        from pr_os_research_workspace.headless_models import bind
        bind(ws)
        if args.client and args.connector_call:raise ValueError('SCOPED_CLIENT_USES_API_CALL')
        if args.mcp and args.client:
            from pr_os_research_workspace.developer_protocol import serve as scoped_serve
            scoped_serve(ws,args.client)
        elif args.mcp:serve(ws)
        else:
            maximum=8*1024*1024 if args.connector_call else 1024*1024
            raw=sys.stdin.read(maximum+1)
            if len(raw)>maximum:raise ValueError('TOOL_MESSAGE_TOO_LARGE')
            params=json.loads(raw or '{}')
            if not isinstance(params,dict):raise ValueError('TOOL_ARGUMENTS_INVALID')
            if args.client:
                result=ws.developer.call(args.client,args.call,params)
                print(json.dumps(result,ensure_ascii=False));return 0
            result=ws.connections.companion(args.connector_call,params) if args.connector_call else ws.call(args.call,validate_arguments(args.call,params))
            print(json.dumps(result,ensure_ascii=False))
    finally:ws.close()
    return 0

def _write_error(code):
    # A disconnected caller must not turn an error response into a GUI crash.
    if sys.stderr is not None:
        try:print(code,file=sys.stderr,flush=True)
        except (OSError,ValueError):pass

def entrypoint(run=None):
    """Shared process boundary for source scripts and the windowed EXE."""
    try:return (run or main)()
    except (ValueError,KeyError,TypeError,OSError) as exc:
        code='MEMO_IO_ERROR' if isinstance(exc,OSError) else 'TOOL_ARGUMENTS_INVALID'
        if isinstance(exc,ValueError) and not isinstance(exc,json.JSONDecodeError):
            message=str(exc)
            if re.fullmatch(r'[A-Z][A-Z0-9_]{1,95}',message):code=message
            elif message.startswith('CLIENT_SCOPE_DENIED:') and message.split(':',1)[1] in {'read','events','import','draft','export','writeback','automation','model'}:code=message
        _write_error(code)
        return 2
    except KeyboardInterrupt:
        _write_error('MEMO_AGENT_INTERRUPTED')
        return 130
    except Exception:
        _write_error('MEMO_AGENT_INTERNAL_ERROR')
        return 1

if __name__=='__main__':raise SystemExit(entrypoint())
