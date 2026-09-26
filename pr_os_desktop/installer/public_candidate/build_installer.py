"""Build an unsigned clean-payload installer candidate without a private capsule.

This prepares a local review artifact. It never installs, registers or publishes.
"""
from pathlib import Path
import argparse, base64, hashlib, json, os, shutil, struct, subprocess, zipfile, re

ROOT = Path(__file__).resolve().parent


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest().upper()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def safe(name):
    parts=name.split('/')
    if not name or '\\' in name or ':' in name or name.startswith('/') or any(p in ('', '.', '..') for p in parts):
        raise ValueError('UNSAFE_MEMBER')
    if any(p.lower() in ('private-test', 'sandbox_profile', '.git', '.env', '__pycache__') for p in parts):
        raise ValueError('PRIVATE_MEMBER')
    if any(p.lower() in ('private_test_bootstrap.py', 'automated_acceptance.py') for p in parts):
        raise ValueError('PRIVATE_TEST_CODE')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('app', 'artifact-manifest', 'toolchain', 'notices', 'output'):
        ap.add_argument('--'+name, type=Path, required=True)
    a=ap.parse_args()
    out=a.output.resolve()
    if out.is_relative_to(ROOT.parents[1]) or out.exists():
        raise ValueError('USE_NEW_OUTPUT_OUTSIDE_SOURCE_TREE')
    inventory=json.loads(a.artifact_manifest.read_text('utf8'))['members']
    expected={r['path'] for r in inventory}
    actual={p.relative_to(a.app).as_posix() for p in a.app.rglob('*') if p.is_file()}
    if actual != expected:
        raise ValueError('ARTIFACT_MEMBER_SET_DRIFT')
    for r in inventory:
        safe(r['path'])
        p=a.app/r['path']
        if p.is_symlink() or getattr(p.stat(),'st_file_attributes',0)&0x400 or sha(p)!=r['sha256'].upper():
            raise ValueError('ARTIFACT_HASH_OR_REPARSE_MISMATCH:'+r['path'])
    binding=json.loads((a.app/'_internal/release_identity_binding.json').read_text('utf8'))
    if any(k.startswith('private_test_capsule') for k in binding) or binding.get('release_authorized') is not False:
        raise ValueError('CLEAN_LOCAL_CANDIDATE_REQUIRED')
    toolchain=json.loads((a.toolchain/'toolchain-source.json').read_text('utf8'))
    for row in toolchain['files']:
        if sha(a.toolchain/row['file'])!=row['sha256'].upper():raise ValueError('TOOLCHAIN_DRIFT')
    provenance=json.loads((ROOT/'SOURCE_PROVENANCE.json').read_text('utf8'))
    for row in provenance['files']:
        if sha(ROOT/row['path'])!=row['candidate_sha256'].upper():raise ValueError('INSTALLER_SOURCE_DRIFT')
    for name in ('install.html','uninstall.html'):
        html=(ROOT/name).read_text('utf8');scripts=re.findall(r'<script>(.*?)</script>',html,re.S)
        policy=re.search(r"script-src 'sha256-([^']+)'",html)
        if len(scripts)!=1 or not policy or base64.b64encode(hashlib.sha256(scripts[0].encode()).digest()).decode()!=policy.group(1):raise ValueError('UI_CSP_MISMATCH')
    out.mkdir(parents=True);work=out/'work';work.mkdir()
    if os.name=='nt':
        import ctypes
        kernel=ctypes.windll.kernel32
        kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(),3);kernel.SetPriorityClass(kernel.GetCurrentProcess(),0x4000)
    compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    engine=work/'Memorive.SetupHost.exe'
    icon=ROOT.parents[1]/'product/desktop/assets/memorive.ico'
    cmd=[str(compiler),'/nologo','/utf8output','/target:winexe','/platform:x64','/optimize+',
         '/out:'+str(engine),'/win32manifest:'+str(ROOT/'app.manifest'),'/win32icon:'+str(icon)]
    cmd+=['/reference:'+s for s in ('System.Windows.Forms.dll','System.Drawing.dll','System.Web.Extensions.dll','System.IO.Compression.dll','System.IO.Compression.FileSystem.dll','Microsoft.CSharp.dll')]
    cmd+=[str(ROOT/s) for s in ('p08_t13_cross_setup.cs','lifecycle.cs','html_host.cs','prerequisite_wizard.cs','release_metadata.cs')]
    for name in ('Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll'):
        cmd+=['/reference:'+str(a.toolchain/name),'/resource:'+str(a.toolchain/name)+','+name]
    cmd+=['/resource:'+str(icon)+',memorive.ico']
    cmd+=['/resource:'+str(a.toolchain/'WebView2Loader.dll')+',WebView2Loader.dll']
    cmd+=['/resource:'+str(ROOT/name)+','+name for name in ('install.html','uninstall.html')]
    result=subprocess.run(cmd,capture_output=True,text=True,encoding='utf8',timeout=90,creationflags=subprocess.CREATE_NO_WINDOW)
    (work/'compiler.log').write_text(result.stdout+result.stderr,encoding='utf8')
    if result.returncode:raise RuntimeError('COMPILATION_FAILED; see compiler.log')
    inputs=[(a.app/r['path'], 'app/'+r['path']) for r in inventory]
    notice_exclusions=[]
    for p in sorted(a.notices.rglob('*')):
        if not p.is_file():continue
        # packaging/licenses is a Python module, not a license-text directory.
        if '__pycache__' in p.parts or p.suffix in ('.py', '.pyc', '.pyo'):
            notice_exclusions.append(p.relative_to(a.notices).as_posix());continue
        if p.name in {'documentation-content-review.json', 'legacy-font-compatibility.json'}:continue
        inputs.append((p,'app/notices/'+p.relative_to(a.notices).as_posix()))
    members=[];payload=work/'payload.zip'
    with zipfile.ZipFile(payload,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as archive:
        for path,name in inputs:
            safe(name);member={'path':name,'size':path.stat().st_size,'sha256':sha(path)};members.append(member)
            info=zipfile.ZipInfo(name,date_time=(2026,9,25,0,0,0));info.compress_type=zipfile.ZIP_STORED if name.startswith('prerequisites/') else zipfile.ZIP_DEFLATED
            with path.open('rb') as src,archive.open(info,'w',force_zip64=True) as dst:shutil.copyfileobj(src,dst,1024*1024)
            if sha(path)!=member['sha256']:raise ValueError('INPUT_CHANGED_DURING_PACKAGING')
    required=['Memorive.exe','_internal/python313.dll','_internal/release_identity_binding.json','notices/ASSET_NOTICE.md']
    manifest={'schema':1,'build_id':binding['build_id'],'label':'TEST_ONLY_UNSIGNED','contract':'p08-desktop-review-v1',
              'health_phase':'v101-empty','app_exe':'Memorive.exe','release_version':binding['release_version'],
              'engine_sha256':sha(engine),'payload_sha256':sha(payload),'members':members,'required_files':required,
              'total_bytes':sum(m['size'] for m in members),'contains_authorized_private_capsule':False,
              'public_distribution_qualified':False,'application_acceptance':'NOT_ASSESSED','installer_candidate_id':'build139','installer_build_id':'build139', 'microsoft_runtime_deployment':'USER_INSTALLED_OFFICIAL_PREREQUISITES'}
    blob=json.dumps(manifest,ensure_ascii=False,separators=(',',':')).encode('utf8')
    target=out/'Memorive-Setup-1.01-x64.exe'
    with target.open('xb') as dst:
        for path in (engine,payload):
            with path.open('rb') as src:shutil.copyfileobj(src,dst,1024*1024)
        dst.write(blob);dst.write(struct.pack('<qqq',engine.stat().st_size,payload.stat().st_size,len(blob)));dst.write(b'PROSSETUP0000001')
    write(out/'package-manifest.json',manifest)
    receipt={'status':'BUILT_LOCAL_CANDIDATE','installer_build_id':'build139','application_build_id':binding['build_id'],'installer_sha256':sha(target),'bytes':target.stat().st_size,
             'app_exe_sha256':sha(a.app/'Memorive.exe'),'artifact_manifest_sha256':sha(a.artifact_manifest),
             'builder_sha256':sha(Path(__file__)),'installer_source_manifest_sha256':sha(ROOT/'SOURCE_PROVENANCE.json'),
             'toolchain_source_sha256':sha(a.toolchain/'toolchain-source.json'),'compiler_sha256':sha(compiler),
             'private_capsule_included':False,'public_release_authorized':False,'installed':False,
             'excluded_notice_module_or_cache_files':notice_exclusions}
    write(out/'build-receipt.json',receipt)
    (out/'SHA256SUMS.txt').write_text(receipt['installer_sha256']+'  '+target.name+'\n',encoding='utf8')
    print(json.dumps(receipt))


if __name__=='__main__':main()
