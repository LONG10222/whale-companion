"""Build the source-only installer archive. Never uploads, commits or pushes."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from self_update import application_path, version


def build(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output == root or output.is_relative_to(root):
        raise ValueError('Write distribution archives outside the source checkout.')
    spec = importlib.util.spec_from_file_location('public_scan', root / 'scripts/scan_public.py')
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    if scanner.main():
        raise ValueError('Public-source privacy check failed.')
    release = json.loads((root / 'configs/release.json').read_text(encoding='utf-8'))
    version(release['version'])
    tag = 'v' + release['version']
    files = {}
    for source in sorted(scanner.files()):
        if not source.is_file() or source.name == 'release-manifest.json':
            continue
        name = application_path(source.relative_to(root).as_posix())
        if source.resolve() != source.absolute() or source.is_symlink():
            raise ValueError('Source links cannot be included in a release.')
        files[name] = source.read_bytes()
    manifest = {'schema_version': 1, 'version': release['version'], 'platform': 'windows-x64',
                'files': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
                          for name, data in files.items()}}
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'WhaleCompanion-{tag}-windows-x64.zip'
    if archive.exists():
        raise FileExistsError('An archive for this version already exists; use a new output directory.')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name, data in {**files, 'release-manifest.json': json.dumps(manifest, ensure_ascii=False, indent=2).encode()}.items():
            entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            bundle.writestr(entry, data)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / 'SHA256SUMS').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    return {'archive': str(archive), 'sha256': digest, 'files': len(files), 'bytes': archive.stat().st_size,
            'contains_credentials_or_runtime': False, 'uploaded': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(ROOT, args.output), ensure_ascii=False, indent=2))
