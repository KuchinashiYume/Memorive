"""Export an explicit source review set without git history or private test files.

The result remains a candidate pending license, artwork and final-content review.
"""
from pathlib import Path
import argparse, hashlib, json, re, shutil

DESKTOP=Path(__file__).resolve().parents[1]
REPO=DESKTOP.parent
EXCLUDE={'source/private_test_bootstrap.py','source/automated_acceptance.py',
         'product/desktop/private_test_bootstrap.py','product/desktop/automated_acceptance.py'}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(p,v):
    p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args();out=a.output.resolve()
    if out.exists() or out.is_relative_to(REPO):raise ValueError('NEW_OUTPUT_OUTSIDE_REPOSITORY_REQUIRED')
    out.mkdir(parents=True)
    originals={};excluded=[]
    def copy(source,relative,expected=None):
        relative=Path(relative)
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('UNSAFE_EXPORT_PATH')
        if source.is_symlink() or getattr(source.stat(),'st_file_attributes',0)&0x400:raise ValueError('REPARSE_INPUT')
        digest=sha(source)
        if expected and expected!=digest:raise ValueError('INPUT_DRIFT:'+str(relative))
        target=out/relative
        if target.exists():
            if sha(target)!=digest:raise ValueError('EXPORT_COLLISION')
            return
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        originals[relative.as_posix()]=digest
    manifest=json.loads((DESKTOP/'SOURCE_MANIFEST.public-candidate.json').read_text('utf8'))
    resources=json.loads((DESKTOP/'BUILD_RESOURCES.public-candidate.json').read_text('utf8'))
    for row in manifest['files']:
        if row['path'] in EXCLUDE:excluded.append(row['path']);continue
        copy(DESKTOP/row['path'],Path('memorive_desktop')/row['path'],row['sha256'])
    for row in resources['files']:
        copy(REPO/row['source'],row['source'],row['sha256'])
    copy(REPO/resources['icon']['path'],resources['icon']['path'],resources['icon']['sha256'])
    files=('INSTALL.md','SECURITY.md','RELEASE_CHECKS.md','LICENSE','LICENSING.md','BUILDING.md','PUBLISHING.md','RELEASE_PREPARATION.md','RELEASE_NOTES.md','LICENSE_PROPOSAL.md','ASSET_NOTICE.md',
           'ARTWORK_RIGHTS.md','THIRD_PARTY_NOTICES.md','GITHUB_FAN_PROJECTS.md','README_PUBLIC.md',
           'README_PUBLIC.en.md','README_PUBLIC.ja.md','requirements-build-baseline.lock','requirements-build-agpl-candidate.lock',
           'tools/build_memorive.py','tools/collect_build_inventory.py','tools/collect_release_sources.py','tools/export_source_candidate.py')
    for name in files:copy(DESKTOP/name,Path('memorive_desktop')/name)
    for folder in ('installer/public_candidate','release_materials'):
        for p in sorted((DESKTOP/folder).rglob('*')):
            if not p.is_file():continue
            if '__pycache__' in p.parts or p.suffix in ('.pyc','.pyo') or (folder=='release_materials' and p.suffix=='.py'):
                excluded.append(p.relative_to(DESKTOP).as_posix());continue
            if p.name in {'documentation-content-review.json', 'legacy-font-compatibility.json'}:continue
            copy(p,Path('memorive_desktop')/p.relative_to(DESKTOP))
    # Exported identity metadata carries no private capsule or local provenance pointer.
    adaptations=[]
    for relative in list(originals):
        if Path(relative).name!='release_identity_binding.json':continue
        p=out/relative;obj=json.loads(p.read_text('utf8'))
        obj={k:v for k,v in obj.items() if not k.startswith('private_test_capsule')}
        obj.update(package_id='v1.01',release_authorized=False,acceptance_verdict='NOT_ASSESSED',canonical_profile='brand_profile.json',
                   authorization_locator='local-source-candidate',source_exact_set_locator='SOURCE_REVIEW_MANIFEST.json')
        write(p,obj);adaptations.append({'path':relative,'before':originals[relative],'after':sha(p),'reason':'clear private delivery identity metadata'})
    manifest['files']=[dict(row,sha256=sha(out/'memorive_desktop'/row['path']),bytes=(out/'memorive_desktop'/row['path']).stat().st_size)
                       for row in manifest['files'] if row['path'] not in EXCLUDE]
    manifest['export_scope']='Public source review candidate; private test source omitted; original baseline retained in canonical repository'
    for row in resources['files']:row['sha256']=sha(out/row['source'])
    write(out/'memorive_desktop/SOURCE_MANIFEST.public-candidate.json',manifest)
    write(out/'memorive_desktop/BUILD_RESOURCES.public-candidate.json',resources)
    readme=(DESKTOP/'README_PUBLIC.md').read_text('utf8')
    readme=re.sub(r'\]\((?!https?://)([^)]+)\)',lambda m:'](memorive_desktop/'+m[1]+')',readme)
    (out/'README.md').write_text(readme,encoding='utf8')
    shutil.copyfile(DESKTOP/'LICENSE',out/'LICENSE')
    (out/'LICENSING.md').write_text('# Licensing scope\n\nSee [Memorive licensing scope](memorive_desktop/LICENSING.md). Character artwork is excluded from the code license.\n',encoding='utf8')
    (out/'.gitignore').write_text('__pycache__/\n*.db\n*.sqlite*\n*.jsonl\n*.log\nprivate-test/\nsandbox_profile/\n*.py[cod]\n.venv/\n.env\n.env.*\n/build/\n/dist/\n*.exe\n*.pfx\n*.p12\n',encoding='utf8')
    rows=[];findings=[]
    patterns={'token_like':re.compile(r'(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{28,}|gh[pousr]_[A-Za-z0-9]{30,}|AIza[A-Za-z0-9_-]{30,})'),
              'author_home':re.compile(r'(?i)(?:C:[\\/]+Users[\\/]+KuchinashiYume|G:[\\/]+desktop)')}
    for p in sorted(out.rglob('*')):
        if not p.is_file():continue
        rel=p.relative_to(out).as_posix();rows.append({'path':rel,'sha256':sha(p),'bytes':p.stat().st_size})
        if p.suffix.lower() in ('.py','.js','.html','.json','.md','.txt','.yaml','.yml','.toml','.cs','.xml','.lock','.svg','.css'):
            text=p.read_text('utf8',errors='replace')
            for label,pattern in patterns.items():
                if pattern.search(text):findings.append({'path':rel,'rule':label,'count':len(pattern.findall(text))})
    report={'schema':'MemoriveSourceExportReview-v1','public_release_authorized':False,'files':rows,
            'excluded':sorted(excluded),'adaptations':adaptations,'text_findings':findings,
            'scan_scope':'Selected textual files; binary screenshots/PDF contents and all possible secrets are not covered'}
    write(out/'SOURCE_REVIEW_MANIFEST.json',report)
    print(json.dumps({'files':len(rows),'excluded':len(excluded),'text_findings':findings,'output':str(out)},ensure_ascii=False))


if __name__=='__main__':main()
