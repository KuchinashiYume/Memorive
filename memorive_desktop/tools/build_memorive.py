"""Build a clean, unsigned local candidate from declared source; no predecessor EXE.

Run with the Python interpreter from an isolated environment installed using
requirements-build-baseline.lock.  This command never publishes or installs a build.
"""
from pathlib import Path
from hashlib import sha256
import argparse, json, os, shutil, subprocess, sys, importlib.metadata as md, re, base64, ast, ssl
from html.parser import HTMLParser

DESKTOP=Path(__file__).resolve().parents[1]
REPO=DESKTOP.parent
EXCLUDE={'source/private_test_bootstrap.py','source/automated_acceptance.py','product/desktop/private_test_bootstrap.py','product/desktop/automated_acceptance.py'}

def digest(p):return sha256(p.read_bytes()).hexdigest()
def write_json(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
def copy_checked(source,target,expected):
    if digest(source)!=expected:raise ValueError('BUILD_INPUT_CHANGED:'+str(source))
    target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)

def check_public_roots(source):
    """Treat certificate encoding as immutable data, independent of product naming."""
    settings=source/'memorive_settings'
    tree=ast.parse((settings/'metadata_transport.py').read_text('utf8'))
    expected=next(ast.literal_eval(node.value) for node in tree.body
                  if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PUBLIC_ROOTS_SHA256' for t in node.targets))
    bundle=(settings/'public_roots.pem').read_bytes()
    if sha256(bundle).hexdigest()!=expected:
        raise ValueError('PUBLIC_ROOTS_INTEGRITY_MISMATCH')
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=bundle.decode('ascii'))
    stats=context.cert_store_stats()
    if not stats['x509_ca']:raise ValueError('PUBLIC_ROOTS_EMPTY')
    return {'sha256':expected,'certificates':stats['x509_ca'],'status':'PASS'}

def check_inline_script_policy(path):
    """Validate the effective HTML policy, including normalized script newlines."""
    class Document(HTMLParser):
        def __init__(self):
            super().__init__();self.policies=[];self.scripts=[];self.current=None
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='meta' and attrs.get('http-equiv','').lower()=='content-security-policy':
                self.policies.append(attrs.get('content',''))
            if tag=='script':self.current={'attrs':attrs,'text':''}
        def handle_data(self,data):
            if self.current is not None:self.current['text']+=data
        def handle_endtag(self,tag):
            if tag=='script' and self.current is not None:
                self.scripts.append(self.current);self.current=None
    document=Document();document.feed(path.read_text('utf8'))
    if not document.policies:raise ValueError('UI_CSP_MISSING:'+path.name)
    allowed=[]
    for policy in document.policies:
        directives={}
        for value in policy.split(';'):
            tokens=value.strip().split()
            if tokens:
                if not re.fullmatch(r'[a-z][a-z0-9-]*',tokens[0]):
                    raise ValueError('UI_CSP_MALFORMED_DIRECTIVE:'+path.name)
                if tokens[0] not in directives:directives[tokens[0]]=tokens[1:]
        allowed.append(directives.get('script-src-elem',directives.get('script-src',directives.get('default-src',[]))))
    checked=0
    for index,script in enumerate(document.scripts):
        if 'src' in script['attrs'] or script['attrs'].get('type','').lower() not in ('','text/javascript','application/javascript','module'):continue
        if not script['text'].strip():continue
        token="'sha256-"+base64.b64encode(sha256(script['text'].encode()).digest()).decode()+"'"
        if any(token not in sources for sources in allowed):raise ValueError('UI_CSP_SCRIPT_HASH_MISMATCH:'+path.name+':'+str(index))
        checked+=1
    return {'path':path.name,'inline_scripts':checked,'status':'PASS'}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--stage-only',action='store_true')
    ap.add_argument('--dependency-lock',type=Path,default=DESKTOP/'requirements-build-agpl-candidate.lock')
    ap.add_argument('--source-manifest',type=Path,default=DESKTOP/'SOURCE_MANIFEST.public-candidate.json')
    a=ap.parse_args();out=a.output.resolve()
    roots_check=check_public_roots(DESKTOP/'source')
    normal=lambda s:re.sub(r'[-_.]+','-',s).lower()
    installed={normal(d.metadata['Name']):d.version for d in md.distributions() if d.metadata['Name']}
    required={}
    for line in a.dependency_lock.read_text('utf8').splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        name,version=line.split('==',1);required[normal(name)]=version.split()[0]
    mismatches={n:{'required':v,'installed':installed.get(n)} for n,v in required.items() if installed.get(n)!=v}
    extras=sorted(set(installed)-set(required)-{'pip'})
    if mismatches or extras:raise ValueError('DEPENDENCY_ENVIRONMENT_MISMATCH:'+json.dumps({'versions':mismatches,'extras':extras}))
    if sys.version_info[:3]!=(3,13,14):raise ValueError('CPython 3.13.14 is required for this build profile')
    if out.exists():raise FileExistsError('Use a new output directory: '+str(out))
    if out==REPO or out.is_relative_to(DESKTOP):raise ValueError('Build outputs must be outside the source tree')
    out.mkdir(parents=True);stage=out/'inputs';stage.mkdir()
    write_json(out/'public-roots-integrity.json',roots_check)
    source_manifest=json.loads(a.source_manifest.read_text('utf8'))
    resources=json.loads((DESKTOP/'BUILD_RESOURCES.public-candidate.json').read_text('utf8'))
    for row in source_manifest['files']:
        if row['path'] in EXCLUDE:continue
        copy_checked(DESKTOP/row['path'],stage/row['path'],row['sha256'])
    for row in resources['files']:
        copy_checked(REPO/row['source'],stage/row['destination'],row['sha256'])
    copy_checked(REPO/resources['icon']['path'],stage/'Memorive.ico',resources['icon']['sha256'])
    ui_policy=[check_inline_script_policy(stage/'product/desktop'/name) for name in ('bundle_integrated.html','bundle_assistant.html')]
    write_json(out/'ui-script-policy.json',ui_policy)
    # The private deliverable imports a private capsule during ordinary startup.
    # Only the public build staging copy receives this bounded clean-start guard.
    app=stage/'product/desktop/app.py';before=digest(app);text=app.read_text('utf8')
    needle='if frozen and not args.desktop_test_mode and console_launch is None:'
    assert text.count(needle)==1,'PRIVATE_BOOTSTRAP_GUARD_SHAPE_CHANGED'
    text=text.replace(needle,needle[:-1]+" and BINDING.get('private_test_capsule_sha256'):")
    acceptance='    if args.memo_automated_acceptance:\n'
    assert text.count(acceptance)==1
    text=text.replace(acceptance,acceptance+"        if not BINDING.get('private_test_capsule_sha256'):\n            raise RuntimeError('PRIVATE_ACCEPTANCE_NOT_INCLUDED_IN_CLEAN_BUILD')\n")
    app.write_text(text,encoding='utf8',newline='\n')
    adaptations=[{'path':'product/desktop/app.py','before':before,'after':digest(app),'reason':'clean build skips private capsule and rejects private model test mode'}]
    for path in (stage/'release_identity_binding.json',stage/'product/desktop/release_identity_binding.json'):
        binding=json.loads(path.read_text('utf8'))
        binding={k:v for k,v in binding.items() if not k.startswith('private_test_capsule')}
        binding.update(package_id='v1.01',release_authorized=False,acceptance_verdict='NOT_ASSESSED',canonical_profile='brand_profile.json',authorization_locator='local-source-build',source_exact_set_locator='build-receipt.json')
        write_json(path,binding)
    # Build paths and data are all explicit; neither tests nor a private capsule
    # are copied from the repository or a previous installation.
    data=[]
    for p in sorted(stage.rglob('*')):
        if p.is_file() and p.name!='Memorive.ico':data.append((str(p),str(p.relative_to(stage).parent).replace('\\','/')))
    hidden=set()
    for p in (stage/'source').rglob('*.py'):
        rel=p.relative_to(stage/'source').with_suffix('').as_posix().replace('/','.')
        hidden.add(rel.removesuffix('.__init__'))
    hidden.update(['assistant_alpha','assistant_native_motion','console_expressions','console_permission','install_health','integrated_agent','notification_runtime','offline_help','product_identity','runtime','window_chrome','network_diagnostics','clr','pythonnet','fitz','docx','websockets','sqlite3.dbapi2'])
    spec=out/'Memorive.spec'
    version=out/'version.txt'
    version.write_text("VSVersionInfo(ffi=FixedFileInfo(filevers=(1,1,0,0),prodvers=(1,1,0,0),mask=0x3f,flags=0,OS=0x40004,fileType=1,subtype=0,date=(0,0)),kids=[StringFileInfo([StringTable('040904B0',[StringStruct('FileDescription','Memorive local source candidate'),StringStruct('ProductName','Memorive'),StringStruct('FileVersion','1.01'),StringStruct('ProductVersion','1.01'),StringStruct('OriginalFilename','Memorive.exe')])]),VarFileInfo([VarStruct('Translation',[1033,1200])])])",encoding='utf8')
    spec.write_text('''from PyInstaller.utils.hooks import collect_all, copy_metadata
datas = '''+repr(data)+'''
binaries = []
hiddenimports = '''+repr(sorted(hidden))+'''
for package in ('chromadb','pymupdf','pymupdf4llm','markitdown','docx'):
    d,b,h = collect_all(package)
    datas += d; binaries += b; hiddenimports += h
for name in '''+repr(sorted({d.metadata['Name'] for d in md.distributions() if d.metadata['Name']}))+''':
    try: datas += copy_metadata(name)
    except Exception: pass
a = Analysis(['''+repr(str(app))+'''], pathex='''+repr([str(stage/'source'),str(stage/'product/desktop')])+''', binaries=binaries, datas=datas, hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['private_test_bootstrap','automated_acceptance','tkinter','PyQt5','PyQt6','PySide2','PySide6','pytest'], noarchive=False, optimize=0)
from pathlib import Path
import os, json, hashlib
a.datas = [item for item in a.datas if not item[0].replace(chr(92),'/').startswith('chromadb/test/')]
system32 = (Path(os.environ['WINDIR'])/'System32').resolve()
excluded_runtime = []
retained_binaries = []
for item in a.binaries:
    source = Path(item[1]).resolve()
    if source.parent == system32 and source.name.lower() in ('msvcp140.dll','msvcp140_1.dll'):
        excluded_runtime.append({'file':item[0], 'source':str(source), 'sha256':hashlib.sha256(source.read_bytes()).hexdigest()})
    else:
        retained_binaries.append(item)
a.binaries = retained_binaries
Path('excluded-system-runtime.json').write_text(json.dumps(excluded_runtime,indent=2),encoding='utf8')
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name='Memorive',debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False,icon='''+repr(str(stage/'Memorive.ico'))+''',manifest='''+repr(str(stage/'product/desktop/Memorive.manifest'))+''',version='''+repr(str(version))+''')
coll = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='Memorive')
''',encoding='utf8')
    lock={d.metadata['Name']:d.version for d in md.distributions() if d.metadata['Name']}
    receipt={'schema':'MemoriveSourceBuild-v1','package_id':'v1.01','source_manifest_sha256':digest(a.source_manifest),'source_manifest':a.source_manifest.name,'resource_map_sha256':digest(DESKTOP/'BUILD_RESOURCES.public-candidate.json'),'dependency_lock_sha256':digest(a.dependency_lock),'dependency_lock':a.dependency_lock.name,'builder_sha256':digest(Path(__file__)),'python':sys.version,'dependencies':lock,'adaptations':adaptations,'private_capsule_included':False,'previous_exe_used':False,'public_release_authorized':False,'status':'STAGED'}
    write_json(out/'build-receipt.json',receipt)
    if a.stage_only:return 0
    if os.name=='nt':
        import ctypes
        k=ctypes.windll.kernel32;k.SetProcessAffinityMask(k.GetCurrentProcess(),3);k.SetPriorityClass(k.GetCurrentProcess(),0x4000)
    env=os.environ.copy();env.pop('PYTHONPATH',None);env.update(PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',PYTHONHASHSEED='0',SOURCE_DATE_EPOCH='1790290800')
    # Native dependency lookup must not capture DLLs from unrelated developer tools.
    # Keep Windows and the explicitly selected interpreter as the only PATH inputs.
    if os.name=='nt':
        windir=Path(os.environ['WINDIR'])
        build_path=[Path(sys.executable).parent,Path(sys.base_prefix),Path(sys.base_prefix)/'DLLs',windir/'System32',windir]
        env['PATH']=os.pathsep.join(str(p) for p in build_path)
        receipt['native_search_policy']='SELECTED_PYTHON_AND_WINDOWS_ONLY'
        receipt['native_search_path']=env['PATH']
        write_json(out/'build-receipt.json',receipt)
    with (out/'build.log').open('w',encoding='utf8') as log:
        result=subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--log-level','WARN','--distpath',str(out/'dist'),'--workpath',str(out/'work'),str(spec)],env=env,cwd=out,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
    receipt['exit_code']=result.returncode;receipt['status']='BUILT' if result.returncode==0 else 'BUILD_FAILED'
    exclusion_record=out/'excluded-system-runtime.json'
    if exclusion_record.exists():receipt['excluded_system_runtime']=json.loads(exclusion_record.read_text('utf8'))
    exe=out/'dist/Memorive/Memorive.exe'
    if exe.exists():receipt['exe_sha256']=digest(exe)
    write_json(out/'build-receipt.json',receipt)
    print(json.dumps({'status':receipt['status'],'output':str(out),'previous_exe_used':False}))
    return result.returncode

if __name__=='__main__':raise SystemExit(main())
