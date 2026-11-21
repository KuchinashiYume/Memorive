"""The research workspace's desktop actions: ResearchMemoMixin.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


class ResearchMemoMixin:
    """The research workspace's desktop actions: picking the vault and materials, opening
    evidence and handoffs, and handing work to desktop agent apps."""

    def memo_pick_vault(self):
        if self._window is None:
            return {'path':None}
        import webview
        value=self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return {'path':str(value[0]) if value else None}

    def memo_pick_materials(self):
        if self._window is None:
            return {'paths':[]}
        import webview
        value=self._window.create_file_dialog(webview.FileDialog.OPEN,allow_multiple=True,
            file_types=('Research materials (*.pdf;*.md;*.txt;*.ris;*.bib;*.bibtex;*.xml)',))
        return {'paths':[str(p) for p in value] if value else []}

    def memo_open_evidence(self, request):
        self.call('memo.capabilities',{})
        ref=self._memo.index.read(request['evidence_id'],request['project'],expected_hash=request.get('expected_hash'))
        os.startfile(ref['path'])
        return {'status':'OPENED','artifact_id':ref['artifact_id']}

    def memo_open_handoff(self, request):
        self.call('memo.capabilities',{})
        value=self._memo.export_handoff(request['handoff_id'])
        os.startfile(value['path'])
        return {'status':'OPENED'}

    def memo_desktop_apps(self):
        import subprocess,json,time
        cached=getattr(self,'_memo_desktop_apps_cache',None)
        if cached and time.monotonic()-cached[0]<60:
            return cached[1]
        cmd="Get-StartApps | Where-Object { ($_.Name -match '^(Codex|Claude)( |$)') -or ($_.AppID -match '^OpenAI.Codex_') } | Select-Object Name,AppID | ConvertTo-Json -Compress"
        try:
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',cmd],capture_output=True,timeout=12,creationflags=subprocess.CREATE_NO_WINDOW)
            rows=json.loads(result.stdout.decode('utf-8-sig',errors='replace') or '[]')
            if isinstance(rows,dict):
                rows=[rows]
        except (OSError,ValueError,subprocess.TimeoutExpired):
            rows=[]
        apps={}
        for row in rows:
            name=str(row.get('Name',''))
            appid=str(row.get('AppID',''))
            if not appid or any(c in appid for c in '\r\n\x00'):
                continue
            key='codex-desktop' if name.lower().startswith('codex') or appid.startswith('OpenAI.Codex_') else 'claude-desktop'
            apps.setdefault(key,{'name':name,'app_id':appid})
        self._memo_desktop_apps_cache=(time.monotonic(),apps)
        return apps

    def memo_pick_agent_app(self):
        if self._window is None:
            return {'path':None}
        import webview
        value=self._window.create_file_dialog(webview.FileDialog.OPEN,allow_multiple=False,file_types=('Application (*.exe)',))
        return {'path':str(value[0]) if value else None}

    def memo_prepare_desktop_handoff(self,request):
        self.call('memo.capabilities',{})
        from memorive.research.workspace.agents import desktop_handoff
        # GetFolderPath uses the Windows account path, not the packaged private profile override.
        import ctypes
        buffer=ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None,28,None,0,buffer)!=0:
            raise ValueError('HANDOFF_DIRECTORY_UNAVAILABLE')
        root=Path(buffer.value)/'Memorive'/'Handoff'
        skill=self._integrations_root()/'skills'/'memo-research'
        return desktop_handoff(self._memo,request['thread_id'],request.get('question',''),root,skill if skill.is_dir() else None)

    def memo_desktop_handoff_action(self,request):
        self.call('memo.capabilities',{})
        row=self._memo.store.get('desktop_handoff',request.get('id',''))
        if not row:
            raise ValueError('HANDOFF_NOT_FOUND')
        ready=self._memo.handoffs.validate_desktop(row)
        if request.get('action')=='validate':
            return ready
        folder=Path(row['path'])
        if request.get('action')=='folder':
            os.startfile(str(folder))
            return {'status':'OPEN_REQUESTED'}
        if request.get('action')!='launch':
            raise ValueError('HANDOFF_ACTION_INVALID')
        path=row.get('app_path','')
        if path:
            p=Path(path)
            if not p.is_absolute() or not p.is_file() or p.suffix.lower()!='.exe' or str(p).startswith(('\\\\','//')) or p.name.lower() in {'cmd.exe','powershell.exe','pwsh.exe','wscript.exe','cscript.exe','mshta.exe','rundll32.exe'}:
                raise ValueError('AGENT_APP_PATH_INVALID')
            os.startfile(str(p))
        else:
            app=self.memo_desktop_apps().get(row['agent'])
            if not app:
                raise ValueError('AGENT_DESKTOP_NOT_FOUND')
            # App identity comes only from the local registered Start menu inventory.
            os.startfile('shell:AppsFolder\\'+app['app_id'])
        return {'status':'OPEN_REQUESTED','model_called':False,'task_started':False}

    def _integrations_root(self):
        """The integrations shipped with the app (companions, developer API, research skills): beside the
        executable when packaged, memorive_desktop/integrations when run from source."""
        base=Path(sys.executable).parent if getattr(sys,'frozen',False) else self._source_root.parent
        return base/'integrations'

    # Installing the memo-research Skill into the agents' own skills folders (settings, Agent and connections).
    # Compatibility code: research/workspace/skill_install.py says which agents and why.
    def _skill_targets(self):
        from memorive.research.workspace.skill_install import SkillTargets
        return SkillTargets(self._integrations_root()/'skills'/'memo-research',self._memo.store.root,Path.home())

    def memo_skill_targets(self):
        self.call('memo.capabilities',{})
        return self._skill_targets().targets()

    def memo_skill_install(self,request):
        self.call('memo.capabilities',{})
        return self._skill_targets().install(agent=request.get('agent'),folder=request.get('folder'))

    def memo_skill_pick_folder(self):
        """A skills folder the person chooses: the Skill is installed there and the folder remembered."""
        if self._window is None:
            return {'path':None}
        import webview
        value=self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not value:
            return {'path':None}
        self.call('memo.capabilities',{})
        return dict(self._skill_targets().choose(value[0]),path=value[0])

    def memo_skill_forget(self,request):
        self.call('memo.capabilities',{})
        return self._skill_targets().forget(request['folder'])

    def memo_agent_config(self):
        self.call('memo.capabilities',{})
        base=self._integrations_root().parent
        helper=Path(sys.executable) if getattr(sys,'frozen',False) else base/'Memorive.exe'
        args=['--memo-agent','--workspace',str(self._memo.store.root),'--mcp']
        if not helper.exists():
            helper=Path(sys.executable)
            args=[str(self._source_root/'memorive/research/workspace/agent_entry.py'),*[v for v in args if v!='--memo-agent']]
        toml='[mcp_servers.memo]\ncommand = '+json.dumps(str(helper),ensure_ascii=False)+'\nargs = '+json.dumps(args,ensure_ascii=False)+'\n'
        return {'mcpServers':{'memo':{'command':str(helper),'args':args}},'codex_config_toml':toml,
                'obsidian_plugin':str(base/'integrations/obsidian/memorive-companion'),
                'zotero_plugin':str(base/'integrations/zotero/memorive-companion.xpi'),
                'helper':str(helper),
                'skills':str(base/'integrations/skills'),
                'workspace':str(self._memo.store.root),
                'permissions':'Search and draft submission; human approval stays in Memorive'}
