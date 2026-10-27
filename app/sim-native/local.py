"""Run the pinned native Sim preview on Windows without Docker."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CACHE = Path.home() / 'minus-sim'
sys.path.insert(0, str(ROOT))
from core.cli import main as _cli_bootstrap
from redaction import sanitize


def verify_cache():
    marker = CACHE / '.minusops-source.json'
    if CACHE.is_symlink() or not marker.is_file():
        raise ValueError('Native source cache is unavailable.')
    value = json.loads(marker.read_text())
    if value['project'] != str(ROOT) or value['revision'] != manifest()['upstream_revision']:
        raise ValueError('Native source cache belongs to another project or revision.')


def manifest():
    return json.loads((HERE / 'patch-manifest.json').read_text())


def prepare():
    source = ROOT / 'artifacts/references/canvas-upstreams/sim'
    revision = manifest()['upstream_revision']
    actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != revision or subprocess.run(['git', '-C', str(source), 'diff', '--quiet', 'HEAD']).returncode:
        raise ValueError('A clean pinned native reference checkout is required.')
    if CACHE.exists():
        verify_cache()
    else:
        CACHE.mkdir()
        (CACHE / '.minusops-source.json').write_text(json.dumps({'project': str(ROOT), 'revision': revision}))
    archive = CACHE / '.source.tar'
    subprocess.run(['git', '-C', str(source), 'archive', '--format=tar', '-o', str(archive), revision,
                    'apps/sim', 'apps/realtime', 'packages', 'scripts', 'patches',
                    'package.json', 'bun.lock', 'bunfig.toml', 'turbo.json'], check=True)
    with tarfile.open(archive) as bundle:
        bundle.extractall(CACHE, filter='data')
    archive.unlink()
    for item in manifest()['files']:
        original = source / item['path']
        overlay = HERE / 'overlay' / item['path']
        if hashlib.sha256(overlay.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Native overlay hash mismatch.')
        if item['upstream_sha256'] and hashlib.sha256(original.read_bytes()).hexdigest() != item['upstream_sha256']:
            raise ValueError('Native source hash mismatch.')
        target = CACHE / item['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(overlay, target)
    print('Pinned native Windows source prepared; reference checkout retained.', flush=True)


def environment(build=False):
    result = {key: value for key, value in os.environ.items() if key.upper() in {
        'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC',
        'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS', 'PROGRAMFILES', 'PROGRAMFILES(X86)',
    }}
    home = CACHE / '.build-home'
    home.mkdir(exist_ok=True)
    result.update(USERPROFILE=str(home), APPDATA=str(home / 'Roaming'),
                  LOCALAPPDATA=str(home / 'Local'), BUN_INSTALL_CACHE_DIR=str(Path.home() / '.bun/install/cache'),
                  HUSKY='0', NEXT_TELEMETRY_DISABLED='1', NEXT_PUBLIC_TELEMETRY_DISABLED='true',
                  NODE_ENV='production', NEXT_PUBLIC_APP_URL='http://localhost:3010',
                  NEXT_PUBLIC_SOCKET_URL='http://localhost:3012', NEXT_PUBLIC_CHAT_DISABLED='false',
                  MINUS_OPENROUTER_CHAT='true', NEXT_WEBPACK_PARALLELISM='2',
                  NODE_OPTIONS='--max-old-space-size=8192' if build else '--max-old-space-size=2048',
                  DATABASE_URL='postgresql://127.0.0.1:6543/native_build')
    if not build:
        private = ROOT / '.minus/native-sim'
        values = dict(line.split('=', 1) for line in (private / 'preview.env').read_text().splitlines() if '=' in line)
        database = dict(line.split('=', 1) for line in (private / 'local.env').read_text().splitlines() if '=' in line)
        address = urlsplit(database['DATABASE_URL'])
        if (address.scheme != 'postgresql' or address.hostname != '127.0.0.1' or address.port != 6543
                or address.path != '/minus_native_sim' or address.username != 'minus_native_sim'
                or not address.password or address.query or address.fragment):
            raise ValueError('The native local preview requires its dedicated loopback database.')
        if not all(values.get('SIM_NATIVE_' + name) for name in (
                'AUTH_SECRET', 'INTERNAL_SECRET', 'ENCRYPTION_KEY', 'API_ENCRYPTION_KEY', 'GATEWAY_TOKEN')):
            raise ValueError('Private local transport configuration is incomplete.')
        result.update(DATABASE_URL=database['DATABASE_URL'], DISABLE_AUTH='true',
                      BETTER_AUTH_URL='http://localhost:3010',
                      BETTER_AUTH_SECRET=values['SIM_NATIVE_AUTH_SECRET'],
                      INTERNAL_API_SECRET=values['SIM_NATIVE_INTERNAL_SECRET'],
                      ENCRYPTION_KEY=values['SIM_NATIVE_ENCRYPTION_KEY'],
                      API_ENCRYPTION_KEY=values['SIM_NATIVE_API_ENCRYPTION_KEY'],
                      COPILOT_API_KEY=values['SIM_NATIVE_GATEWAY_TOKEN'],
                      MINUS_ASSISTANT_GATEWAY_TOKEN=values['SIM_NATIVE_GATEWAY_TOKEN'],
                      MINUS_ASSISTANT_GATEWAY_URL='http://127.0.0.1:5180',
                      SIM_AGENT_API_URL='http://127.0.0.1:5180',
                      SOCKET_SERVER_URL='http://127.0.0.1:3012', PORT='3012',
                      ENTERPRISE_ENABLED='false', NEXT_PUBLIC_ENTERPRISE_ENABLED='false')
    return result


def execute(command, *, cwd=CACHE, env):
    secrets = [value for key, value in env.items() if value and (key.endswith(('SECRET', 'KEY', 'TOKEN')) or key == 'DATABASE_URL')]
    with subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace') as process:
        for line in process.stdout:
            for value in secrets:
                line = line.replace(value, '[REDACTED]')
            print(sanitize(line).encode('ascii', 'backslashreplace').decode(), end='', flush=True)
        status = process.wait()
    if status:
        print('Native command exited with status', status, flush=True)
        raise SystemExit(status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'install', 'build', 'migrate', 'start', 'dev', 'realtime'])
    action = parser.parse_args().action
    if action == 'prepare':
        prepare()
        return
    verify_cache()
    bun = str(ROOT / 'artifacts/tools/bun-1.4.2/bun-windows-x64/bun.exe')
    node = shutil.which('node')
    if not Path(bun).is_file() or node is None:
        raise ValueError('The pinned Bun tool and Node 24 are required.')
    env = environment(action in ('install', 'build'))
    if action == 'install':
        env['NODE_ENV'] = 'development'
        execute([bun, 'install', '--force', '--ignore-scripts', '--linker=hoisted'], env=env)
        execute([node, 'apps/sim/scripts/verify-oracledb-patch.cjs'], env=env)
    elif action in ('build', 'dev'):
        if action == 'dev':
            env.update(NODE_ENV='development', NODE_OPTIONS='--max-old-space-size=6144')
        config = CACHE / 'apps/sim/next.config.ts'
        original_config = config if config.exists() else config.with_name('next.config.source.ts')
        execute([bun, 'build', str(original_config), '--target=node', '--format=esm',
                 '--packages=bundle', '--outfile=apps/sim/next.config.native.mjs'], env=env)
        shutil.copyfile(HERE / 'dev-next.config.mjs', CACHE / 'apps/sim/next.config.mjs')
        adapter = HERE / 'browser-password.mjs'
        if hashlib.sha256(adapter.read_bytes()).hexdigest() != manifest()['build_adapters'][adapter.name]:
            raise ValueError('Native build adapter hash mismatch.')
        shutil.copyfile(adapter, CACHE / 'apps/sim' / adapter.name)
        if config.exists():
            config.replace(config.with_name('next.config.source.ts'))
        execute([bun, 'build', 'packages/sim-cli/src/runtime.ts', '--target=node', '--format=esm',
                 '--packages=bundle', '--outfile=packages/sim-cli/dist/runtime.js'], env=env)
        if action == 'dev':
            execute([node, str(CACHE / 'node_modules/next/dist/bin/next'), 'dev',
                     '--hostname', '127.0.0.1', '--port', '3010'], cwd=CACHE / 'apps/sim', env=env)
            return
        env['NEXT_BUILD_SKIP_TYPE_CHECK'] = '1'
        # Native source types are checked separately against generated declarations.
        execute([node, str(HERE / 'build.mjs')], cwd=CACHE, env=env)
        env['SIM_NATIVE_APP_ROOT'] = str(CACHE / 'apps/sim')
        env['SIM_NATIVE_OVERLAY_ROOT'] = str(HERE)
        execute([node, str(HERE / 'tests/typecheck.cjs')], env=env)
    elif action == 'migrate':
        execute([bun, '--no-env-file', 'scripts/migrate.ts'], cwd=CACHE / 'packages/db', env=env)
    elif action == 'realtime':
        env.update(SIM_DB_ROLE='realtime', DB_APP_NAME='sim-realtime')
        execute([bun, '--no-env-file', 'src/index.ts'], cwd=CACHE / 'apps/realtime', env=env)
    else:
        execute([node, str(CACHE / 'node_modules/next/dist/bin/next'), 'start',
                 '--hostname', '127.0.0.1', '--port', '3010'], cwd=CACHE / 'apps/sim', env=env)


if __name__ == '__main__':
    main()
