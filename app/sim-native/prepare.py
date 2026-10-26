"""Prepare the pinned native source build without changing the reference checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'core/reporting'))
from operations_models import local_path


def prepare(source, *, build_only=False):
    source = Path(source).resolve(strict=True)
    manifest = json.loads((HERE / 'patch-manifest.json').read_text())
    revision = manifest['upstream_revision']
    actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != revision or subprocess.run(['git', '-C', str(source), 'diff', '--quiet', 'HEAD']).returncode:
        raise ValueError('Native source must match the clean pinned revision.')
    runtime = local_path(ROOT, ROOT / 'artifacts/runtime/native-sim')
    build = local_path(ROOT, runtime / 'openrouter-build')
    build.mkdir(parents=True, exist_ok=True)
    for row in manifest['files']:
        path = local_path(HERE, HERE / 'overlay' / row['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Native overlay hash mismatch.')
        original = source / row['path']
        if row['upstream_sha256'] is not None and hashlib.sha256(original.read_bytes()).hexdigest() != row['upstream_sha256']:
            raise ValueError('Native source hash mismatch.')
        target = local_path(ROOT, build / 'patches' / row['path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    for name in ('Dockerfile', 'Dockerfile.deps', '.dockerignore', 'dev-next.config.mjs', 'build.mjs'):
        shutil.copyfile(HERE / name, local_path(ROOT, build / name))
    shutil.copyfile(HERE / 'patch-manifest.json', local_path(ROOT, build / 'patch-manifest.json'))
    shutil.copyfile(HERE / 'tests/typecheck.cjs', local_path(ROOT, build / 'typecheck.cjs'))
    archive = local_path(ROOT, build / 'source.tar')
    subprocess.run(['git', '-C', str(source), 'archive', '--format=tar', '-o', str(archive),
                    revision, 'apps/sim', 'apps/realtime', 'packages', 'scripts', 'patches',
                    'package.json', 'bun.lock', 'bunfig.toml', 'turbo.json', 'LICENSE', 'NOTICE'], check=True)
    if build_only:
        print('Pinned public source and overlays prepared; no private configuration accessed.')
        return
    for name in ('compose.json', 'openrouter-compose.json'):
        shutil.copyfile(HERE / name, local_path(ROOT, runtime / name))
    private = local_path(ROOT, ROOT / '.minus/native-sim')
    private.mkdir(parents=True, exist_ok=True)
    env = local_path(ROOT, private / 'preview.env')
    if env.exists() and (not env.is_file() or env.stat().st_nlink != 1):
        raise ValueError('Private configuration requires an unlinked regular file.')
    names = ('DB_PASSWORD', 'AUTH_SECRET', 'ENCRYPTION_KEY', 'API_ENCRYPTION_KEY', 'INTERNAL_SECRET')
    if not env.exists():
        with env.open('x', encoding='utf-8') as handle:
            for name in names:
                handle.write(f'SIM_NATIVE_{name}={secrets.token_hex(32)}\n')
        os.chmod(env, 0o600)
    entries = [line.split('=', 1)[0] for line in env.read_text().splitlines() if '=' in line]
    if len(entries) != len(set(entries)) or not all(f'SIM_NATIVE_{name}' in entries for name in names):
        raise ValueError('Existing private configuration is incomplete; it was preserved.')
    if 'SIM_NATIVE_GATEWAY_TOKEN' not in entries:
        with env.open('a', encoding='utf-8') as handle:
            handle.write(f'SIM_NATIVE_GATEWAY_TOKEN={secrets.token_hex(32)}\n')
    print('Native source, overlays and private configuration prepared; existing credentials retained.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default=str(ROOT / 'artifacts/references/canvas-upstreams/sim'))
    parser.add_argument('--build-only', action='store_true', help='Prepare public build inputs without reading or creating private configuration.')
    args = parser.parse_args()
    prepare(args.source, build_only=args.build_only)
