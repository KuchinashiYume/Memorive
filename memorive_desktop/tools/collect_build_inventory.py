"""Record exact local build files, wheel hashes, declared licenses and native origins.

This is an inventory, not a legal compatibility decision or a privacy clearance.
Run using the same isolated interpreter used for the build.
"""
from pathlib import Path
import argparse, ast, hashlib, importlib.metadata as md, json, re, shutil, sys, zipfile, email

def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
def normal(n):return re.sub(r'[-_.]+','-',n).lower()
def strings(node):
    if isinstance(node,str):yield node
    elif isinstance(node,(tuple,list)):
        for x in node:yield from strings(x)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--build',type=Path,required=True)
    ap.add_argument('--wheelhouse',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    app=a.build/'dist/Memorive';receipt=json.loads((a.build/'build-receipt.json').read_text('utf8'))
    assert receipt['status']=='BUILT'
    used={str(Path(s).resolve()).casefold() for s in strings(ast.literal_eval((a.build/'work/Memorive/Analysis-00.toc').read_text('utf8'))) if ':\\' in s or ':/' in s}
    native_sources={};components=[]
    for d in sorted(md.distributions(),key=lambda x:normal(x.metadata['Name'])):
        name=d.metadata['Name']
        if normal(name)=='pip':continue
        files=d.files or [];license_rows=[];runtime_files=[]
        for rel in files:
            p=Path(d.locate_file(rel));s=str(rel).replace('\\','/')
            if not p.is_file():continue
            if str(p.resolve()).casefold() in used and '.dist-info/' not in s:runtime_files.append(s)
            if p.suffix.lower() in ('.dll','.pyd','.exe'):
                native_sources.setdefault(sha(p),[]).append({'component':name,'source':s})
            if re.search(r'(^|/)(licenses?([^/]*)|copying([^/]*)|notice([^/]*))($|/)',s,re.I):
                dest=a.output/'licenses'/normal(name)/s.replace('../','parent/')
                dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
                license_rows.append({'path':dest.relative_to(a.output).as_posix(),'sha256':sha(dest)})
        components.append({'name':name,'version':d.version,'purl':f'pkg:pypi/{normal(name)}@{d.version}',
          'declared_license_expression':d.metadata.get('License-Expression'),
          'declared_license':d.metadata.get('License'),'license_classifiers':[v for v in d.metadata.get_all('Classifier',[]) if v.startswith('License ::')],
          'project_urls':d.metadata.get_all('Project-URL',[]),'home_page':d.metadata.get('Home-page'),
          'license_files':license_rows,'analysis_runtime_files':runtime_files,
          'scope':'analysis-runtime' if runtime_files else 'environment-or-metadata-only',
          'license_review':'NOT_ASSESSED'})
    members=[];native=[]
    for p in sorted(app.rglob('*')):
        if not p.is_file():continue
        row={'path':p.relative_to(app).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)}
        members.append(row)
        if p.suffix.lower() in ('.dll','.pyd','.exe'):
            native.append({**row,'matched_distribution_sources':native_sources.get(row['sha256'],[]),'review':'IDENTIFIED_DISTRIBUTION' if row['sha256'] in native_sources else 'REQUIRES_NATIVE_PROVENANCE'})
    wheels=[];hashlock=['# Windows x64 CPython 3.13.14; use with --no-index --find-links <verified-wheelhouse>.']
    for p in sorted(a.wheelhouse.glob('*.whl')):
        with zipfile.ZipFile(p) as z:
            m=email.message_from_bytes(z.read(next(n for n in z.namelist() if n.endswith('.dist-info/METADATA'))))
        h=sha(p);wheels.append({'file':p.name,'name':m['Name'],'version':m['Version'],'sha256':h,'bytes':p.stat().st_size})
        hashlock.append(f"{normal(m['Name'])}=={m['Version']} --hash=sha256:{h}")
    assert {normal(x['name']) for x in wheels}=={normal(x['name']) for x in components},'WHEELHOUSE_COMPONENT_SET_MISMATCH'
    write(a.output/'dependency-inventory.json',{'schema':'MemoriveDependencyInventory-v1','python':sys.version.split()[0],
      'scope':'Exact build environment; runtime files identified from PyInstaller Analysis; native dependencies separately inventoried',
      'components':components,'legal_compatibility_review':'NOT_COMPLETE','corresponding_source_collection':'PENDING'})
    write(a.output/'artifact-manifest.json',{'build_receipt_sha256':sha(a.build/'build-receipt.json'),'members':members})
    write(a.output/'native-inventory.json',{'components':native,'note':'Unmatched files require Python, Microsoft, PyInstaller or vendored native provenance review; no identity guessed.'})
    write(a.output/'wheelhouse-manifest.json',{'files':wheels})
    (a.output/'requirements-wheelhouse.lock').write_text('\n'.join(hashlock)+'\n',encoding='utf8')
    shutil.copyfile(a.build/'build-receipt.json',a.output/'build-receipt.json')
    print(json.dumps({'distributions':len(components),'artifacts':len(members),'native_files':len(native),'native_unmatched':sum(not x['matched_distribution_sources'] for x in native),'wheels':len(wheels),'license_files':sum(len(x['license_files']) for x in components),'missing_license_files':[x['name'] for x in components if not x['license_files']]}))
if __name__=='__main__':main()
