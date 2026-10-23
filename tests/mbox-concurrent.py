#!/usr/bin/env python3
"""Same-project identities; optional real concurrent VMs with private engine/home.

Without flags, checks the actual launcher identity fragment and lifecycle argv.
With --msb/--firmware/--image/--python/--state-parent, boots two disposable VMs,
stops the first, and proves the second still executes. No live state or network.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

source = (Path(__file__).resolve().parents[1]/'programs/mbox/mbox.sh.in').read_text()
fragment = source[source.index('PROJECT="$(realpath'):source.index('# Runs msb,')]
assert '--replace "$IMAGE"' not in source
assert '"$MSB" remove "$SANDBOX" --force' not in source
parser = argparse.ArgumentParser(description=__doc__)
for option in ('msb', 'firmware', 'image', 'python', 'state-parent'):
    parser.add_argument('--' + option)
args = parser.parse_args()
real = args.msb is not None
if real:
    for option in ('msb', 'firmware', 'image', 'python'):
        value = getattr(args, option)
        assert value and value.startswith('/nix/store/') and Path(value).exists()
    assert args.state_parent
root = Path(tempfile.mkdtemp(prefix='mc-', dir=args.state_parent))
print(root, flush=True)
project = root/'project'
project.mkdir()
env = dict(os.environ, PROJECT_DIR=str(project), STATE_ROOT=str(root/'state'))
command = ['bash', '-eu', '-c', fragment + '\nprintf "%s\\n" "$SANDBOX" "$PROJECT_STATE" "$PERSIST_ROOT"']
a = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, text=True)
b = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, text=True)
first = a.communicate(timeout=10)[0].splitlines()
second = b.communicate(timeout=10)[0].splitlines()
assert a.returncode == b.returncode == 0
assert first[0] != second[0] and first[1:] == second[1:]
assert first[0].startswith('mbox-') and len(first[0]) < 64
print('PASS: parallel launcher identities differ; project persistence unchanged', flush=True)
processes = []
names = []
try:
    if real:
        env.update(HOME=str(root/'host-home'), MSB_HOME=str(root/'e'),
                   MSB_LIBKRUNFW_PATH=args.firmware, TMPDIR=str(root))
        (root/'host-home').mkdir()
        home = root/'guest-home'; home.mkdir()
        def run(command):
            r = subprocess.run([args.msb, *command], env=env, capture_output=True,
                               text=True, timeout=120)
            assert r.returncode == 0, r.stdout[-8192:] + r.stderr[-8192:]
            return r.stdout
        run(['image', 'load', '--input', args.image, '--tag', 'concurrent:latest', '--quiet'])
        for i, identity in enumerate((first, second)):
            name = identity[0]; names.append(name)
            log = (root/f'vm-{i}.log').open('w')
            code = f"from pathlib import Path; import time; Path('/home/fixture/ready-{i}').write_text('ready'); time.sleep(90)"
            p = subprocess.Popen([args.msb, 'run', '--name', name, '--cpus', '1',
                '--memory', '512M', '--user', f'{os.getuid()}:{os.getgid()}',
                '--net', 'none', '--no-tty', '--timeout', '100s',
                '--mount-dir', '/nix/store:/nix/store:ro',
                '--mount-dir', f'{home}:/home/fixture:rw,uid={os.getuid()},gid={os.getgid()}',
                'concurrent:latest', '--', args.python, '-c', code],
                env=env, stdout=log, stderr=log)
            processes.append((p, log))
            import time
            deadline = time.monotonic() + 40
            while not (home/f'ready-{i}').exists():
                assert time.monotonic() < deadline and p.poll() is None, (root/f'vm-{i}.log').read_text()
                time.sleep(.1)
        assert all(p.poll() is None for p, _ in processes), 'second launch displaced first'
        def alive(name):
            out = run(['exec', name, '--', args.python, '-c', "print('ALIVE')"])
            assert 'ALIVE' in out, out
        alive(first[0]); alive(second[0])
        run(['stop', first[0], '--force'])
        processes[0][0].wait(timeout=15)
        assert processes[1][0].poll() is None
        alive(second[0])
        print('PASS: both VMs alive together; stopping first leaves second usable', flush=True)
finally:
    if real:
        for name in names:
            run(['remove', name, '--force'])
        for p, log in processes:
            p.wait(timeout=15); log.close()
    shutil.rmtree(root)
