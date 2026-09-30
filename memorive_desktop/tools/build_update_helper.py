"""Compile the shared native installer/update engine using an existing toolchain."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import re


def typography_contract(source):
    """Use the delivered Memo declarations, not a second installer type scale."""
    path=source/'product/desktop/bundle_integrated.html'
    html=path.read_text('utf8')
    styles=re.findall(r'<style id="memo-typography">(.*?)</style>',html,re.S)
    sizes=re.findall(r'const sizes=\{(page:\d+,section:\d+,body:\d+,empty:\d+,control:\d+,value:\d+,small:\d+,code:\d+)\}',html)
    if len(styles)!=1 or len(sizes)!=1:
        raise ValueError('MEMO_TYPOGRAPHY_DECLARATION_AMBIGUOUS')
    css=styles[0]
    stacks={}
    for language,selector in [('zh-CN',':root'),('en-US',':root:lang(en)'),('ja-JP',':root:lang(ja)')]:
        matches=re.findall(re.escape(selector)+r'\s*\{([^}]+)\}',css)
        if len(matches)!=1:raise ValueError('MEMO_FONT_DECLARATION_MISSING:'+language)
        stacks[language]={}
        for name in ('heading','body'):
            raw=re.search('--memo-font-'+name+r':([^;]+)',matches[0]).group(1)
            stacks[language][name]=[x.strip().strip('"') for x in raw.split(',')]
    if '[data-memo-type="page"],[data-memo-type="section"] {font-weight:700!important;' not in css:
        raise ValueError('MEMO_HEADING_WEIGHT_CHANGED')
    if 'font-family:Consolas,"Courier New",monospace!important;' not in css:
        raise ValueError('MEMO_CODE_FONT_CHANGED')
    return dict(schema='MemoNativeTypography-v1',source='product/desktop/bundle_integrated.html',source_sha256=sha(path),
                sizes={k:int(v) for k,v in (x.split(':') for x in sizes[0].split(','))},stacks=stacks,
                weights={role:700 if role in ('page','section') else 400 for role in ('page','section','body','empty','control','value','small','code')},
                code=['Consolas','Courier New','monospace'],native_bridge='Use declared Latin family for Latin UI and first declared CJK family for zh/ja UI; OS font resolution is verified separately.')

SOURCES = ('installation_cross_setup.cs','lifecycle.cs','html_host.cs','prerequisite_wizard.cs',
           'update_protocol.cs','update_download.cs','update_transaction.cs','update_entry.cs','installer_locale.cs')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def compile_engine(source, toolchain, output, *, trust=None):
    source,toolchain,output=map(Path,(source,toolchain,output))
    if output.exists():
        raise FileExistsError('NEW_NATIVE_OUTPUT_REQUIRED')
    installer=source/'installer/public_candidate'
    identity=json.loads((source/'product/desktop/update_release.json').read_text('utf8'))
    version=identity['version_tuple']
    if len(version)!=4 or any(type(n)!=int or not 0<=n<=65535 for n in version) or identity['release_authorized'] is not False:
        raise ValueError('UPDATE_RELEASE_IDENTITY_INVALID')
    manifest=json.loads((toolchain/'toolchain-source.json').read_text('utf8'))
    for row in manifest['files']:
        if sha(toolchain/row['file']).lower()!=row['sha256'].lower():
            raise ValueError('NATIVE_TOOLCHAIN_DRIFT')
    trust=Path(trust) if trust else installer/'update_trust.json'
    trust_value=json.loads(trust.read_text('utf8'))
    if trust != installer/'update_trust.json' and (not trust_value.get('test_only') or not trust_value.get('public_key_xml')):
        raise ValueError('EXTERNAL_TRUST_REQUIRES_EXPLICIT_TEST_KEY')
    if trust_value.get('test_only') and (not Path(trust_value.get('test_root','')).is_absolute() or Path(trust_value['test_root']).name!='Memorive-Installer-Tests'):
        raise ValueError('EXPLICIT_TEST_ROOT_REQUIRED')
    output.mkdir(parents=True)
    from compile_installer_locales import compile_locales
    localized=output/'localized'
    generated=compile_locales(installer,localized,identity)
    metadata=output/'release_metadata.cs'
    four='.'.join(map(str,version));display=identity['release_version']
    metadata.write_text('using System.Reflection;\n[assembly: AssemblyTitle("Memorive")]\n[assembly: AssemblyProduct("Memorive")]\n[assembly: AssemblyVersion("'+four+'")]\n[assembly: AssemblyFileVersion("'+four+'")]\n[assembly: AssemblyInformationalVersion("'+display+'")]\n',encoding='utf8')
    compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    engine=output/'Memorive.Update.exe';icon=source/'product/desktop/assets/memorive.ico'
    command=[str(compiler),'/nologo','/utf8output','/target:winexe','/platform:x64','/optimize+',
             '/out:'+str(engine),'/win32manifest:'+str(installer/'app.manifest'),'/win32icon:'+str(icon)]
    command+=['/reference:'+name for name in ('System.Windows.Forms.dll','System.Drawing.dll','System.Web.Extensions.dll','System.IO.Compression.dll','System.IO.Compression.FileSystem.dll','Microsoft.CSharp.dll')]
    command += [str(localized/name if (localized/name).exists() else installer/name) for name in SOURCES]+[str(metadata)]
    for name in ('Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll'):
        command+=['/reference:'+str(toolchain/name),'/resource:'+str(toolchain/name)+','+name]
    resources=[(source/'product/desktop/assets/decorations/home-interaction.png','memorive-brand.png'),(icon,'memorive.ico'),(toolchain/'WebView2Loader.dll','WebView2Loader.dll'),(trust,'update_trust.json')]
    typography=output/'memo-typography.json'
    typography.write_text(json.dumps(typography_contract(source),ensure_ascii=False,indent=2),encoding='utf8')
    resources.append((typography,'memo-typography.json'))
    catalog=json.loads((installer/'installer-catalog.json').read_text('utf8'))
    for row in catalog.values():
        for language in ('zh-CN','en-US','ja-JP'):
            row[language]=row[language].replace('v1.01',identity['display_version'])
    catalog_path=output/'installer-catalog.json';catalog_path.write_text(json.dumps(catalog,ensure_ascii=False),encoding='utf8')
    resources += [(catalog_path,'installer-catalog.json')]+[(path,path.name) for path in generated if path.suffix=='.html']
    command+=['/resource:'+str(path)+','+name for path,name in resources]
    result=subprocess.run(command,capture_output=True,text=True,encoding='utf8',timeout=90,check=False)
    (output/'compiler.log').write_text(result.stdout+result.stderr,encoding='utf8')
    if result.returncode:
        raise RuntimeError('NATIVE_COMPILATION_FAILED')
    shutil.copyfile(engine,output/'Memorive.Launcher.exe')
    receipt=dict(schema='MemoriveUpdateHelperIdentity-v1',helper_sha256=sha(engine),launcher_sha256=sha(engine),
                 trust_sha256=sha(trust),test_only=trust_value['test_only'],production_trust_configured=bool(trust_value['public_key_xml']) and not trust_value['test_only'],
                 compiler_sha256=sha(compiler),toolchain_sha256=sha(toolchain/'toolchain-source.json'),
                 release_version=display,version_tuple=version,real_model_calls=0,network_calls=0)
    (output/'helper-identity.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf8')
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('source','toolchain','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--test-trust',type=Path)
    args=parser.parse_args()
    print(json.dumps(compile_engine(args.source,args.toolchain,args.output,trust=args.test_trust)))
