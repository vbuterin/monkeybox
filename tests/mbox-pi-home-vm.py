#!/usr/bin/env python3
"""Opt-in fresh private-home permission regression in disposable real VMs.

No live Pi, host home, credentials, desktop, networking or application data.
Requires installed store paths and a short disk-backed --state-parent.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
for option in ('msb', 'firmware', 'image', 'python', 'state-parent'):
    parser.add_argument('--' + option, required=True)
args = parser.parse_args()
for option in ('msb', 'firmware', 'image', 'python'):
    path = Path(getattr(args, option))
    assert path.is_absolute() and str(path).startswith('/nix/store/') and path.exists()
assert os.getuid() != 0
root = Path(tempfile.mkdtemp(prefix='ph-', dir=args.state_parent))
print(root, flush=True)
env = dict(os.environ, MSB_HOME=str(root/'engine'), MSB_LIBKRUNFW_PATH=args.firmware,
           HOME=str(root/'host-home'), TMPDIR=str(root))
(root/'host-home').mkdir()
(root/'models.json').write_text('{}')
helper = Path(__file__).resolve().parents[1]/'programs/mbox/repair-home-metadata.py'

def run(command):
    result = subprocess.run([args.msb, *command], env=env, text=True,
                            capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stdout[-8192:] + result.stderr[-8192:])
    return result.stdout

code = '''import os
from pathlib import Path
p = Path('/home/fixture/.pi/agent')
s = p.stat()
print('AGENT', s.st_uid, s.st_gid, oct(s.st_mode))
try:
    (p/'sessions').mkdir(exist_ok=True)
    (p/'sessions/probe').write_text('private session')
    print('WRITE_OK')
except PermissionError:
    print('WRITE_DENIED')
try:
    (p/'models.json').write_text('changed')
except OSError:
    print('RESOURCE_READ_ONLY')
else:
    raise AssertionError('shared config writable')
assert not (p/'auth.json').exists()
'''
try:
    run(['image', 'load', '--input', args.image, '--tag', 'probe:latest', '--quiet'])
    for prepared in (False, True):
        home = root/('prepared-home' if prepared else 'unprepared-home')
        (home/'.pi').mkdir(parents=True)
        if prepared:
            subprocess.run([sys.executable, str(helper), str(home)], check=True)
        result = run(['run', '--name', 'pi-home-probe', '--replace', '--cpus', '1',
            '--memory', '512M', '--user', f'{os.getuid()}:{os.getgid()}', '--net', 'none',
            '--no-tty', '--timeout', '30s', '--mount-dir', '/nix/store:/nix/store:ro',
            '--mount-dir', f'{home}:/home/fixture:rw,uid={os.getuid()},gid={os.getgid()}',
            '--mount-file', f'{root}/models.json:/home/fixture/.pi/agent/models.json:ro',
            'probe:latest', '--', args.python, '-c', code])
        print(result, flush=True)
        assert ('WRITE_OK' if prepared else 'WRITE_DENIED') in result
        assert 'RESOURCE_READ_ONLY' in result
        assert (root/'models.json').read_text() == '{}'
finally:
    # Preserve state rather than delete backing storage if VM removal fails.
    run(['remove', 'pi-home-probe', '--force'])
    shutil.rmtree(root)
print('PASS: reproduced first-launch EACCES; prepared private home writable, shared config read-only')
