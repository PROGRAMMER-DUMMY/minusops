"""Resource-bounded, credential-free build for this pinned local preview."""
from pathlib import Path
import argparse
import json
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
CONTEXT = ROOT / 'artifacts/runtime/native-sim/openrouter-build'
OUT = ROOT / 'artifacts/runtime/native-sim/build-logs'
NAME = 'minusops-sim-openrouter-build'
OWNER = 'minusops.native-sim-build'
MIN_FREE = 3 * 1024**3
GIB = 1024**3


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def compile_memory():
    memory = min(12, int(docker('info', '--format', '{{.MemTotal}}')) // GIB - 2) * GIB
    if memory < 9 * GIB:
        raise RuntimeError('Native compilation needs at least 11 GiB assigned to Docker, including host headroom; use a larger build host.')
    return memory


def checked(command, label):
    log = OUT / f'docker-{label}.log'
    if shutil.disk_usage(ROOT).free < MIN_FREE:
        raise RuntimeError('At least 3 GiB free host storage is required before starting.')
    deadline = time.monotonic() + 1200
    with log.open('wb') as handle:
        process = subprocess.Popen(command, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
        try:
            while process.poll() is None:
                if shutil.disk_usage(ROOT).free < MIN_FREE or time.monotonic() > deadline:
                    raise RuntimeError('Build stopped at its storage or time bound; see the recorded log.')
                time.sleep(2)
        finally:
            if process.poll() is None:
                if label == 'compile':
                    assert docker('inspect', '--format', '{{index .Config.Labels "' + OWNER + '"}}', NAME) == 'true'
                    subprocess.run(['docker', 'stop', '--time', '5', NAME], stdout=subprocess.DEVNULL, check=False)
                process.terminate()
                process.wait(timeout=15)
    if process.returncode:
        print(log.read_text(encoding='utf-8', errors='replace')[-64000:].encode('ascii', 'backslashreplace').decode())
        raise SystemExit(process.returncode)
    print(label, 'completed.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['deps', 'source', 'compile'])
    mode = parser.parse_args().action
    OUT.mkdir(parents=True, exist_ok=True)
    if mode == 'deps':
        checked(['docker', 'build', '--progress=plain', '-t', 'minusops-native-sim-openrouter-deps:df3db64',
                 '-f', str(CONTEXT / 'Dockerfile.deps'), str(CONTEXT)], mode)
    elif mode == 'source':
        checked(['docker', 'build', '--progress=plain', '--target', 'source',
                 '-t', 'minusops-native-sim-openrouter-source:df3db64',
                 '-f', str(CONTEXT / 'Dockerfile'), str(CONTEXT)], mode)
    elif mode == 'compile':
        memory = compile_memory()
        docker('create', '--name', NAME, '--label', OWNER + '=true', f'--memory={memory}',
               f'--memory-swap={memory + 2 * GIB}', '--cpus=4', '--pids-limit=1024', '--ulimit', 'core=0:0',
               'minusops-native-sim-openrouter-source:df3db64', 'sh', '-c',
               'node /overlay/build.mjs && chown -R nextjs:nodejs apps/sim/.next')
        config = json.loads(docker('inspect', '--format', '{{json .HostConfig}}', NAME))
        assert config['Memory'] == memory and config['MemorySwap'] == memory + 2 * GIB
        assert config['NanoCpus'] == 4_000_000_000 and config['PidsLimit'] == 1024
        (OUT / 'docker-build-limits.json').write_text(json.dumps({
            key: config[key] for key in ('Memory', 'MemorySwap', 'NanoCpus', 'PidsLimit')}, indent=2))
        checked(['docker', 'start', '--attach', NAME], mode)
        state = json.loads(docker('inspect', '--format', '{{json .State}}', NAME))
        assert not state['OOMKilled'] and state['ExitCode'] == 0
        docker('commit', '--change', 'USER nextjs', '--change', 'ENV NODE_OPTIONS=--max-old-space-size=4096',
               '--change', 'LABEL org.opencontainers.image.source=https://github.com/PROGRAMMER-DUMMY/minusops',
               '--change', 'CMD ["node","node_modules/next/dist/bin/next","start","apps/sim","--hostname","0.0.0.0","--port","3000"]',
               NAME, 'minusops-native-sim-openrouter:df3db64')
        docker('rm', NAME)
        print('Compiled preview image saved. Type-check acceptance remains separate.', flush=True)
    else:
        raise SystemExit('Expected deps, source or compile.')
