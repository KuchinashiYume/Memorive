"""Download exact PyPI source archives and recover notices without executing them.

The output is provenance evidence, not a blanket license-compatibility verdict.
Existing output directories are rejected. No package is installed or imported.
"""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import urllib.request
import zipfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Memorive-release-preparation/1.0'})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--all-sdists', action='store_true', help='Collect exact sdists for every dependency with a PyPI source release.')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'metadata').mkdir()
    (args.output / 'archives').mkdir()
    (args.output / 'notices').mkdir()
    components = json.loads(args.inventory.read_text('utf8'))['components']

    def collect(component):
        name = re.sub(r'[-_.]+', '-', component['name']).lower()
        version = component['version']
        url = f'https://pypi.org/pypi/{name}/{version}/json'
        row = {'name': name, 'version': version, 'metadata_url': url,
               'source_archives': [], 'notices': [], 'compatibility_review': 'NOT_ASSESSED'}
        try:
            raw = fetch(url)
            write(args.output / 'metadata' / f'{name}-{version}.json', json.loads(raw))
            info = json.loads(raw)
            row['project_urls'] = info['info'].get('project_urls') or {}
            selected = [item for item in info['urls'] if item['packagetype'] == 'sdist']
            row['source_candidates'] = [{k: item[k] for k in ('filename', 'url', 'size', 'digests')} for item in selected]
            required = args.all_sdists or not component['license_files'] or name.startswith('pymupdf')
            if required and not selected:
                row['source_status'] = 'NO_PYPI_SDIST'
            for item in selected if required else []:
                if item['size'] > 512 * 1024 * 1024:
                    raise ValueError('SOURCE_ARCHIVE_EXCEEDS_512_MIB')
                filename = item['filename']
                if PurePosixPath(filename).name != filename or '\\' in filename:
                    raise ValueError('UNSAFE_FILENAME')
                data = fetch(item['url'])
                if sha(data) != item['digests']['sha256'] or len(data) != item['size']:
                    raise ValueError('PYPI_ARCHIVE_HASH_OR_SIZE_MISMATCH')
                archive = args.output / 'archives' / filename
                archive.write_bytes(data)
                row['source_archives'].append({'file': 'archives/' + filename, 'url': item['url'],
                                               'sha256': sha(data), 'bytes': len(data)})
                if zipfile.is_zipfile(archive):
                    with zipfile.ZipFile(archive) as container:
                        members = [(m.filename, container.read(m)) for m in container.infolist()
                                   if not m.is_dir() and m.file_size < 2 * 1024 * 1024
                                   and re.search(r'(^|/)(licen[sc]e[^/]*|copying[^/]*|notice[^/]*)$', m.filename, re.I)]
                else:
                    with tarfile.open(archive) as container:
                        members = [(m.name, container.extractfile(m).read()) for m in container.getmembers()
                                   if m.isfile() and m.size < 2 * 1024 * 1024
                                   and re.search(r'(^|/)(licen[sc]e[^/]*|copying[^/]*|notice[^/]*)$', m.name, re.I)]
                for index, (member, content) in enumerate(members):
                    dest = args.output / 'notices' / name / f'{index:03d}-{PurePosixPath(member).name}'
                    dest.parent.mkdir(exist_ok=True)
                    dest.write_bytes(content)
                    row['notices'].append({'file': dest.relative_to(args.output).as_posix(),
                                           'archive_member': member, 'sha256': sha(content)})
                row['source_status'] = 'EXACT_SDIST_HASH_VERIFIED'
            if not required:
                row['source_status'] = 'SOURCE_URLS_RECORDED'
        except Exception as exc:
            row['error'] = type(exc).__name__ + ': ' + str(exc)
        return row

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        rows = sorted(executor.map(collect, components), key=lambda row: row['name'])
    report = {'schema': 'MemoriveReleaseSourceEvidence-v1',
              'inventory_sha256': sha(args.inventory.read_bytes()), 'components': rows,
              'archive_execution': False, 'public_release_authorized': False}
    write(args.output / 'source-evidence.json', report)
    print(json.dumps({'components': len(rows), 'archives': sum(len(r['source_archives']) for r in rows),
                      'notices': sum(len(r['notices']) for r in rows),
                      'errors': [{'name': r['name'], 'error': r['error']} for r in rows if 'error' in r],
                      'missing_notice_recovery': [r['name'] for r in rows if r['source_archives'] and not r['notices']]}))


if __name__ == '__main__':
    main()
