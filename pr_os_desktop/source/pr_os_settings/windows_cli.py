"""Resolve supported Windows CLIs for validation and background workers."""
import os,re,shutil
from pathlib import Path

def native_process_path(value):
    """Keep the same file identity while satisfying CreateProcess MAX_PATH."""
    text=str(value)
    if os.name!='nt' or len(text)<248:return text
    import ctypes
    original=Path(text)
    if not original.exists():raise ValueError('CLI_WORKSPACE_PATH_UNAVAILABLE')
    buffer=ctypes.create_unicode_buffer(32768)
    size=ctypes.windll.kernel32.GetShortPathNameW(text,buffer,len(buffer))
    if not size or size>=len(buffer) or len(buffer.value)>=248:raise ValueError('CLI_WORKSPACE_PATH_TOO_LONG')
    if Path(buffer.value).resolve()!=original.resolve():raise ValueError('CLI_WORKSPACE_PATH_IDENTITY_CONFLICT')
    return buffer.value

def _known_folder(csidl):
    import ctypes
    buffer=ctypes.create_unicode_buffer(32768)
    # Known-folder locations are independent of Memo's isolated APPDATA.
    if ctypes.windll.shell32.SHGetFolderPathW(None,csidl,None,0,buffer)!=0:return None
    return Path(buffer.value)

def _local_app_data():return _known_folder(28)
def _roaming_app_data():return _known_folder(26)
def _user_profile():return _known_folder(40)

def native_codex_from_shim(shim):
    path=Path(shim)
    if path.name.lower() not in {'codex.cmd','codex.bat'} or not path.is_file():return None
    if path.stat().st_size>4096:return None
    try:lines=[s.strip() for s in path.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
    except (OSError,UnicodeError):return None
    if len(lines)!=3 or lines[0].lower()!='@echo off' or lines[2].lower()!='exit /b %errorlevel%':return None
    match=re.fullmatch(r'"(%LOCALAPPDATA%[\\/]Programs[\\/]OpenAI[\\/]Codex[\\/]bin[\\/]codex\.exe)" %\*',lines[1],re.I)
    if not match:return None
    base=_local_app_data()
    if base is None:return None
    target=base/'Programs/OpenAI/Codex/bin/codex.exe'
    if not target.is_file():return None
    return str(target.resolve())

def _desktop_codex_candidates():
    """Return current Codex Desktop binaries without binding a version hash."""
    base=_local_app_data()
    if base is None:return []
    roots=(base/'OpenAI/Codex/bin',base/'Programs/OpenAI/Codex/bin')
    found=[]
    for root in roots:
        direct=root/'codex.exe'
        if direct.is_file():found.append(direct)
        try:children=tuple(root.iterdir()) if root.is_dir() else ()
        except OSError:children=()
        for child in children:
            candidate=child/'codex.exe'
            if child.is_dir() and candidate.is_file():found.append(candidate)
    unique={str(path.resolve()).casefold():path.resolve() for path in found}
    return sorted(
        unique.values(),
        key=lambda path:(path.stat().st_mtime_ns,str(path).casefold()),
        reverse=True,
    )

def resolve_codex_executable(value):
    """Resolve the same native Codex binary for validation and background jobs."""
    if not isinstance(value,str) or value.casefold() not in {'codex','codex.cmd','codex.exe'}:
        return None
    for name in ('codex.exe',value):
        located=shutil.which(name)
        if not located:continue
        path=Path(located)
        if path.name.lower() in {'codex.cmd','codex.bat'}:
            native=native_codex_from_shim(path)
            if native:return native
        elif path.is_file():
            return str(path.resolve())
    candidates=_desktop_codex_candidates()
    return str(candidates[0]) if candidates else None

_SUPPORTED_CLI_NAMES={
    'codex','claude','gemini','qwen','kimi','codebuddy','copilot',
}

def _supported_name(value):
    if not isinstance(value,str):return None
    name=value.casefold()
    for suffix in ('.exe','.cmd','.bat'):
        if name.endswith(suffix):name=name[:-len(suffix)];break
    return name if name in _SUPPORTED_CLI_NAMES else None

def _candidate_directories(name):
    local=_local_app_data();roaming=_roaming_app_data();profile=_user_profile()
    rows=[]
    if local is not None:
        rows.extend((
            local/'Programs/PR-OS-CLI/bin',
            local/'pnpm',
            local/'Microsoft/WinGet/Links',
            local/'Microsoft/WindowsApps',
            local/'Programs'/name/'bin',
            local/name/'bin',
        ))
        vendor={
            'claude':('Anthropic/Claude','Programs/Claude'),
            'gemini':('Google/Gemini','Programs/Gemini'),
            'qwen':('Alibaba/Qwen','Programs/Qwen'),
            'kimi':('Moonshot/Kimi','Programs/Kimi'),
            'codebuddy':('Tencent/CodeBuddy','Programs/CodeBuddy'),
            'copilot':('GitHub/Copilot','Programs/GitHub Copilot'),
        }.get(name,())
        rows.extend(local/part/'bin' for part in vendor)
    if roaming is not None:rows.append(roaming/'npm')
    if profile is not None:
        rows.extend((profile/'.local/bin',profile/'scoop/shims',profile/'.bun/bin'))
        if name=='claude':rows.append(profile/'.claude/local')
    for env_name in ('PNPM_HOME','NPM_CONFIG_PREFIX'):
        value=os.environ.get(env_name)
        if isinstance(value,str) and value and '\x00' not in value:
            path=Path(value)
            if path.is_absolute():rows.extend((path,path/'bin'))
    seen=set();accepted=[]
    for row in rows:
        key=str(row).casefold()
        if key not in seen:
            seen.add(key);accepted.append(row)
    return accepted

def _winget_package_candidates(name,filenames):
    local=_local_app_data()
    if local is None:return []
    root=local/'Microsoft/WinGet/Packages'
    prefixes={
        'codex':('openai.codex_',),
        'claude':('anthropic.claudecode_','anthropic.claude-code_'),
        'gemini':('google.geminicli_','google.gemini-cli_'),
        'qwen':('alibaba.qwencode_','alibaba.qwen-code_'),
        'kimi':('moonshot.kimicode_','moonshot.kimi-code_'),
        'codebuddy':('tencent.codebuddy_','tencent.codebuddy-code_'),
        'copilot':('github.copilot_',),
    }.get(name,())
    try:children=tuple(root.iterdir()) if root.is_dir() else ()
    except OSError:children=()
    found=[]
    for child in children:
        if not child.is_dir() or not child.name.casefold().startswith(prefixes):continue
        for filename in filenames:
            candidate=child/filename
            if candidate.is_file():found.append(candidate)
    return found

def resolve_supported_cli_executable(value):
    """Find every CLI exposed by the settings contract in common user roots."""
    name=_supported_name(value)
    if name is None:return None
    if name=='codex':
        native=resolve_codex_executable(value)
        if native:return native
    filenames=(name+'.exe',name+'.cmd',name+'.bat')
    for filename in filenames:
        located=shutil.which(filename)
        if located and Path(located).is_file():return str(Path(located).resolve())
    for directory in _candidate_directories(name):
        for filename in filenames:
            candidate=directory/filename
            if candidate.is_file():return str(candidate.resolve())
    winget=_winget_package_candidates(name,filenames)
    if winget:
        winget.sort(key=lambda path:(path.stat().st_mtime_ns,str(path).casefold()),reverse=True)
        return str(winget[0].resolve())
    return None
