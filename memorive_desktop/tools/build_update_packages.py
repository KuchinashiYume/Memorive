"""Build one-hop update artifacts from immutable runtime directories. No network.

The index signs exact UTF-8 bytes using .NET RSA3072/SHA256 PKCS#1 v1.5.
The signer is a release-side tool, never included in an application payload.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

MAX_FILES = 20000
MAX_EXPANDED = 8 * 1024**3


def sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def relative(name: str) -> str:
    if not name or any(ord(c) < 32 for c in name) or any(c in name for c in '\\:*?"<>|'):
        raise ValueError('UPDATE_PATH_INVALID')
    parts = name.split('/')
    if any(p in ('', '.', '..') or p.rstrip(' .') != p or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', p, re.I) for p in parts):
        raise ValueError('UPDATE_PATH_INVALID')
    if any(p.lower() in ('__pycache__', '.git', '.env', 'sandbox_profile', 'private-test') for p in parts):
        raise ValueError('UPDATE_PRIVATE_PAYLOAD_FORBIDDEN')
    return name


def inventory(root: Path) -> list[dict]:
    root = root.resolve(strict=True)
    for p in (root, *root.parents):
        if getattr(p.stat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('UPDATE_REPARSE_FORBIDDEN')
    rows, names = [], set()
    for directory, dirs, files in os.walk(root, followlinks=False):
        for n in dirs + files:
            p = Path(directory) / n
            if p.is_symlink() or getattr(p.stat(), 'st_file_attributes', 0) & 0x400:
                raise ValueError('UPDATE_REPARSE_FORBIDDEN')
        for n in files:
            p = Path(directory) / n
            name = relative(p.relative_to(root).as_posix())
            if name.casefold() in names:
                raise ValueError('UPDATE_CASE_COLLISION')
            names.add(name.casefold())
            rows.append(dict(path=name, size=p.stat().st_size, sha256=sha(p)))
    if not rows or len(rows) > MAX_FILES or sum(r['size'] for r in rows) > MAX_EXPANDED:
        raise ValueError('UPDATE_INVENTORY_LIMIT')
    return sorted(rows, key=lambda r: r['path'].encode('utf-16-be'))


def fingerprint(rows: list[dict]) -> str:
    # Defined wire representation; tab/newline are forbidden in Windows paths.
    raw = ''.join(f"{r['path']}\t{r['size']}\t{r['sha256'].lower()}\n" for r in rows)
    return hashlib.sha256(raw.encode('utf8')).hexdigest()


def packed(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf8')


def write_zip(path: Path, root: Path, manifest: bytes, selected: list[dict]) -> None:
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        meta = zipfile.ZipInfo('target-manifest.json', date_time=(2026, 9, 29, 0, 0, 0))
        meta.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(meta, manifest)
        for row in selected:
            source = root / row['path']
            if sha(source) != row['sha256']:
                raise ValueError('UPDATE_INPUT_DRIFT')
            info = zipfile.ZipInfo('files/' + row['path'], date_time=(2026, 9, 29, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            with source.open('rb') as src, archive.open(info, 'w', force_zip64=True) as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            if sha(source) != row['sha256']:
                raise ValueError('UPDATE_INPUT_DRIFT')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--base', type=Path, action='append', default=[])
    parser.add_argument('--identity', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--signer', type=Path, required=True)
    parser.add_argument('--test-only', action='store_true')
    parser.add_argument('--asset-root')
    args = parser.parse_args()
    identity = json.loads(args.identity.read_text('utf8'))
    version = identity['release_version']
    if not re.fullmatch(r'\d+\.\d{2,}', version) or identity['version_tuple'] != [int(x) for x in version.split('.')] + [0, 0]:
        raise ValueError('UPDATE_RELEASE_VERSION_INVALID')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', identity['package_id']):
        raise ValueError('UPDATE_PACKAGE_ID_INVALID')
    if identity.get('release_authorized') is not False:
        raise ValueError('PUBLIC_RELEASE_REQUIRES_SEPARATE_PIPELINE')
    target = args.target.resolve(strict=True)
    out = args.output.resolve()
    bases = [p.resolve(strict=True) for p in args.base]
    if out.exists() or any(out.is_relative_to(p) or p.is_relative_to(out) for p in [target, *bases]):
        raise ValueError('UPDATE_NEW_OUTPUT_REQUIRED')
    rows = inventory(target)
    known = {r['path'] for r in rows}
    if not {'Memorive.exe', '_internal/release_identity_binding.json',
            '_internal/update/Memorive.Update.exe', '_internal/update/Memorive.Launcher.exe',
            '_internal/update/helper-identity.json'} <= known:
        raise ValueError('UPDATE_MAIN_APPLICATION_REQUIRED')
    manifest = dict(schema_version='MemoriveTargetManifest-v1', product='Memorive', platform='win-x64',
                    package_id=identity['package_id'], release_version=version, files=rows,
                    inventory_sha256=fingerprint(rows), total_bytes=sum(r['size'] for r in rows))
    raw = packed(manifest)
    if len(raw) > 8 * 1024**2:
        raise ValueError('UPDATE_MANIFEST_LIMIT')
    out.mkdir(parents=True)
    (out/'target-manifest.json').write_bytes(raw)
    url_root = 'https://github.com/KuchinashiYume/Memorive/releases/download/v' + version + '/'
    if args.asset_root:
        from urllib.parse import urlsplit
        local = urlsplit(args.asset_root)
        if not args.test_only or local.scheme != 'http' or local.hostname != '127.0.0.1' or local.username or local.query or local.fragment:
            raise ValueError('UPDATE_TEST_ASSET_ROOT_INVALID')
        url_root = args.asset_root.rstrip('/')+'/'
    def asset(path):
        return dict(name=path.name, url=url_root+path.name, size=path.stat().st_size, sha256=sha(path))
    full = out/f'Memorive-{version}-win-x64-full.zip'
    write_zip(full, target, raw, rows)
    deltas, receipts = [], []
    for number, base in enumerate(bases, 1):
        source = inventory(base)
        source_hash = fingerprint(source)
        old = {r['path']: r for r in source}
        changed = [r for r in rows if old.get(r['path']) != r]
        delta = out/f'Memorive-{version}-win-x64-from-{source_hash[:16]}.zip'
        write_zip(delta, target, raw, changed)
        deltas.append(dict(source_manifest_sha256=source_hash, **asset(delta)))
        receipts.append(dict(source=str(base), source_manifest_sha256=source_hash,
            source_files=len(source), reused=len(rows)-len(changed), changed=len(changed),
            removed=sorted(set(old)-known), delta_bytes=delta.stat().st_size,
            full_bytes=full.stat().st_size, byte_saving=full.stat().st_size-delta.stat().st_size,
            saving_fraction=1-delta.stat().st_size/full.stat().st_size,
            source_inventory=source))
    index = dict(schema_version='MemoriveUpdateIndex-v1', product='Memorive', platform='win-x64',
        channel='stable', test_only=args.test_only, release_version=version,
        version_tuple=identity['version_tuple'], package_id=identity['package_id'],
        source_commit=identity['source_commit'], target_manifest_sha256=hashlib.sha256(raw).hexdigest(),
        min_updater_version=1, min_os=19045, full=asset(full), deltas=deltas,
        data_compatibility=dict(migration_id='E11-existing-schemas-v1', readable=[1,2], writable=2),
        notes=identity['notes'])
    encoded = packed(index)
    if len(encoded) > 262144:
        raise ValueError('UPDATE_INDEX_LIMIT')
    index_path = out/'memorive-update.json'
    index_path.write_bytes(encoded)
    # MEMORIVE_RELEASE_KEY_FILE identifies a private DPAPI-protected local file.
    # Its path/value and plaintext key are never copied into a receipt.
    proc = subprocess.run([str(args.signer), '--sign', str(index_path), '--output', str(out/'memorive-update.sig')],
                          capture_output=True, text=True, timeout=30)
    if proc.returncode:
        raise RuntimeError('UPDATE_SIGNING_FAILED')
    (out/'build-receipt.json').write_bytes(packed(dict(status='BUILT_NOT_RELEASED', test_only=args.test_only,
        target_manifest_sha256=sha(out/'target-manifest.json'), index_sha256=sha(index_path),
        signature_sha256=sha(out/'memorive-update.sig'), full=asset(full), comparisons=receipts,
        builder_sha256=sha(Path(__file__)), signer_sha256=sha(args.signer), private_key_exported=False,
        release_authorized=False, model_calls=0, network_calls=0)))
    print(json.dumps(dict(status='BUILT_NOT_RELEASED', output=str(out), files=len(rows), deltas=len(deltas))))


if __name__ == '__main__':
    main()
