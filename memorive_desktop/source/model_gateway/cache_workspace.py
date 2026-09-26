"""Content/role-bound Codex workspace with exclusive continuation dispatch."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile

_INTERNAL_MARKER='.memorive-internal-codex-session.json'

def _mark_internal(workspace, purpose):
    path=Path(workspace)/_INTERNAL_MARKER
    if not path.exists():
        path.write_text(json.dumps({'schema_version':'MemoriveInternalCodexSession-v1','purpose':purpose},sort_keys=True),encoding='utf-8')

_WINDOWS_PROCESS_PATH_LIMIT=248


def _fits_process_path(path):
    return len(str(path).encode('utf-16-le')) // 2 < _WINDOWS_PROCESS_PATH_LIMIT


def _fits_workspace(path):
    # Reserve the complete attempt output, not just the capture directory.
    return _fits_process_path(path/'captures'/('c'+'0'*32)/'stdout.bin')


def _workspace_candidates(root, digest):
    return (
        root/'prompt-cache-lanes'/digest,
        root/'prompt-cache-lanes'/digest[:32],
        root/'pcl'/digest[:32],
        root/'pcl'/digest[:16],
    )


def _short_cache_roots(original_root, digest):
    profile_digest=hashlib.sha256(
        str(original_root).casefold().encode('utf-8')
    ).hexdigest()[:16]
    explicit=os.environ.get('MEMORIVE_CLI_CACHE_ROOT')
    preferred=[]
    if explicit:preferred.append(Path(explicit))
    user_profile=os.environ.get('USERPROFILE')
    if user_profile:
        preferred.append(Path(user_profile)/'AppData'/'Local'/'Memorive'/'cli-cache')
    local_app_data=os.environ.get('LOCALAPPDATA')
    if local_app_data:
        preferred.append(Path(local_app_data)/'Memorive'/'cli-cache')
    preferred.append(Path(tempfile.gettempdir())/'memorive-cli-cache')
    unique=[];seen=set()
    for base in preferred:
        try:resolved=base.resolve(strict=False)
        except OSError:continue
        key=str(resolved).casefold()
        if key in seen:continue
        seen.add(key);unique.append(resolved)
    explicit_root=unique[:1] if explicit and unique else []
    remaining=unique[len(explicit_root):]
    ordered=explicit_root+sorted(remaining,key=lambda value:(len(str(value)),str(value).casefold()))
    for base in ordered:
        root=base/f'p-{profile_digest}'
        candidates=_workspace_candidates(root,digest)
        if not any(_fits_workspace(candidate)
                   for candidate in candidates):
            continue
        try:
            root.mkdir(parents=True,exist_ok=True)
            binding=root/'.memorive-cli-cache-root.json'
            payload=json.dumps({
                'schema_version':'MemoriveCliCacheRootBinding-v1',
                'source_root_sha256':hashlib.sha256(
                    str(original_root).encode('utf-8')
                ).hexdigest().upper(),
            },sort_keys=True)
            if binding.exists():
                if binding.read_text(encoding='utf-8')!=payload:
                    continue
            else:
                binding.write_text(payload,encoding='utf-8')
        except OSError:
            continue
        yield root


@contextmanager
def structured_chat_workspace(scratch_root, *, profile_kind, service, model, purpose):
    scope=model.get('prompt_cache_scope')
    if not (purpose in {'core_document_processing','research_chat'} and profile_kind=='CLI'
            and service.get('adapter_id')=='codex_cli' and isinstance(scope,str) and scope):
        temporary_root=Path(scratch_root).resolve()
        # Every CLI needs a native-process-safe cwd, including calls without
        # a reusable Codex lane. Keep the existing per-call cleanup semantics.
        if profile_kind=='CLI' and not _fits_workspace(temporary_root/('memorive-structured-chat-'+'0'*8)):
            digest=hashlib.sha256(str(temporary_root).encode('utf-8')).hexdigest()
            temporary_root=next((root for root in _short_cache_roots(temporary_root,digest)
                if _fits_workspace(root/('memorive-structured-chat-'+'0'*8))),None)
            if temporary_root is None:raise ValueError('CACHE_WORKSPACE_PATH_BUDGET_EXCEEDED')
        with tempfile.TemporaryDirectory(prefix='memorive-structured-chat-',dir=temporary_root,
                ignore_cleanup_errors=True) as temporary:
            workspace=Path(temporary).resolve();_mark_internal(workspace,purpose);yield workspace
        return
    identity={'scope':scope,'model':model,'service':service,'purpose':purpose}
    digest=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    root=Path(scratch_root).resolve()
    candidates=_workspace_candidates(root,digest)
    workspace=next((candidate for candidate in candidates
                    if _fits_workspace(candidate)),None)
    if workspace is None:
        for fallback_root in _short_cache_roots(root,digest):
            fallback_candidates=_workspace_candidates(fallback_root,digest)
            workspace=next((candidate for candidate in fallback_candidates
                            if _fits_workspace(candidate)),None)
            if workspace is not None:
                root=fallback_root
                break
    if workspace is None:raise ValueError('CACHE_WORKSPACE_PATH_BUDGET_EXCEEDED')
    if not workspace.resolve().is_relative_to(root): raise ValueError('CACHE_WORKSPACE_PATH_ESCAPE')
    workspace.mkdir(parents=True,exist_ok=True)
    _mark_internal(workspace,purpose)
    lock=workspace/'.exclusive-call.lock'
    # Concurrent calls fail closed rather than sharing a mutable schema or history.
    try:
        with lock.open('xb'):pass
    except FileExistsError:
        if purpose!='research_chat':raise
        # Concurrent or interrupted lanes must not block another conversation.
        with tempfile.TemporaryDirectory(prefix='research-parallel-',dir=root,ignore_cleanup_errors=True) as temporary:
            workspace=Path(temporary).resolve();_mark_internal(workspace,purpose);yield workspace
        return
    try: yield workspace
    finally: lock.unlink()


def write_response_schema(workspace, payload: bytes) -> Path:
    digest=hashlib.sha256(payload).hexdigest()
    candidates=(
        Path(workspace)/f'response_schema_{digest}.json',
        Path(workspace)/f'rs_{digest[:32]}.json',
        Path(workspace)/f'rs_{digest[:16]}.json',
    )
    path=next((candidate for candidate in candidates if _fits_process_path(candidate)),None)
    if path is None:
        raise ValueError('CACHE_SCHEMA_PATH_BUDGET_EXCEEDED')
    if path.exists():
        if path.read_bytes()!=payload: raise ValueError('CACHE_SCHEMA_HASH_MISMATCH')
    else:
        with path.open('xb') as stream: stream.write(payload)
    return path
