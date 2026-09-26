"""Package audited companion sources without reading host profiles or user data."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import zipfile

DESKTOP = Path(__file__).resolve().parents[1]


def package(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    root = DESKTOP / 'integrations'
    obsidian = root / 'obsidian/memorive-companion'
    zotero = root / 'zotero/memorive-companion'
    generated = ('const memoCompanionFeature=(()=>{const module={exports:{}};\n'
                 + (obsidian / 'companion.js').read_text('utf8')
                 + '\nreturn module.exports;})();\n'
                 + (obsidian / 'main.source.js').read_text('utf8').replace(
                     "require(path.join(base,this.manifest.dir,'companion.js')).install",
                     'memoCompanionFeature.install'))
    if generated != (obsidian / 'main.js').read_text('utf8'):
        raise ValueError('OBSIDIAN_BUNDLE_DIFFERS_FROM_SOURCE')
    for folder in ('obsidian', 'zotero', 'developer', 'skills'):
        shutil.copytree(root / folder, output / folder)
    for name in ('README.md', 'README.en.md', 'README.ja.md', 'NOTICE.md'):
        shutil.copyfile(root / name, output / name)
    for folder in (output / 'obsidian/memorive-companion', output / 'zotero/memorive-companion'):
        shutil.copyfile(DESKTOP / 'LICENSE', folder / 'LICENSE')
        shutil.copyfile(root / 'NOTICE.md', folder / 'NOTICE.md')
    archives = [
        ('memorive-obsidian-companion.zip', output / 'obsidian',
         ['memorive-companion/' + n for n in ('main.js', 'manifest.json', 'styles.css', 'LICENSE', 'NOTICE.md')]),
        ('zotero/memorive-companion.xpi', output / 'zotero/memorive-companion',
         ['bootstrap.js', 'main.js', 'manifest.json', 'preferences.js', 'preferences.xhtml', 'style.css', 'LICENSE', 'NOTICE.md']),
    ]
    for name, base, names in archives:
        with zipfile.ZipFile(output / name, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for member in names:
                info = zipfile.ZipInfo(member, (2026, 9, 26, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, (base / member).read_bytes())
    rows = [{'path': p.relative_to(output).as_posix(),
             'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size}
            for p in sorted(output.rglob('*')) if p.is_file()]
    (output / 'package-manifest.json').write_text(json.dumps({'version': '1.0.0', 'files': rows}, indent=2) + '\n', encoding='utf8')
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({'files': len(package(args.output)), 'output': str(args.output)}))
