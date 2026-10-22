#!/usr/bin/env python3
"""Opt-in real guest swap probe. Requires a built firmware; never alters host swap.

Creates/removes only a private engine. No desktop, audio, project or home grants.
Use --state-parent on disk-backed storage; needs ~2 GiB available host disk space.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--msb', required=True)
    parser.add_argument('--firmware', required=True)
    parser.add_argument('--image', required=True, help='mbox-base.tar store path')
    parser.add_argument('--python', required=True, help='guest Python binary in /nix/store')
    parser.add_argument('--swap-tools', required=True, help='util-linux bin directory in /nix/store')
    parser.add_argument('--state-parent', required=True, help='short, disk-backed host directory')
    parser.add_argument('--non-root', action='store_true', help='also exercise bootstrap identity drop to a disposable guest account')
    args = parser.parse_args()
    for name in ('msb', 'firmware', 'image', 'python', 'swap_tools'):
        value = Path(getattr(args, name))
        if not value.is_absolute() or not value.exists() or not str(value).startswith('/nix/store/'):
            parser.error(f'{name} must be an existing absolute /nix/store path')
    if shutil.disk_usage(args.state_parent).free < 2 * 1024**3:
        parser.error('at least 2 GiB free disk space is required')

    # The helper is embedded in a trusted command string, not mounted from a
    # mutable checkout. It sets up swap on the dedicated guest ext4 disk only.
    helper = (ROOT / 'programs/mbox/guest-memory.py').read_text()
    pressure = '''
setup(512, TOOLS)
assert os.getuid() == 0
import resource
before = snapshot()
def used_swap(s):
    rows = s['proc/swaps'].splitlines()[1:]
    return sum(int(row.split()[3]) for row in rows)
assert used_swap(before) == 0, before
# Incompressible anonymous pages exceed the 512 MiB guest RAM limit. Retain and
# revisit every allocation so the test requires both swap-out and swap-in.
blocks = [bytearray(os.urandom(1024 * 1024)) for _ in range(600)]
allocated = snapshot()
assert used_swap(allocated) > 0, allocated
faults_before = resource.getrusage(resource.RUSAGE_SELF).ru_majflt
checksum = 0
for block in blocks:
    for page in range(0, len(block), 4096):
        checksum += block[page]
after = snapshot()
# Upstream disables CONFIG_VM_EVENT_COUNTERS, so pswpin/pswpout are absent.
# Resident swap slots prove writes; major faults revisiting anonymous pages
# (no file reads in this loop) prove demand reads back from swap.
faults = resource.getrusage(resource.RUSAGE_SELF).ru_majflt - faults_before
assert faults > 100, (faults, after)
assert '/run/mbox-swap/swapfile' in after['proc/swaps'], after
print(json.dumps({'result': 'PASS', 'allocated_swap_KiB': used_swap(allocated),
                  'revisit_major_faults': faults, 'guest': after, 'checksum': checksum}))
'''
    code = '__name__ = "probe"\n' + helper + '\nTOOLS = ' + repr(args.swap_tools) + '\n' + pressure
    if args.non_root:
        # This edits only the disposable guest rootfs. No host identity database.
        workload = pressure.replace('setup(512, TOOLS)\nassert os.getuid() == 0',
                                   'assert os.getuid() == os.getgid() == 1234\nassert os.getgroups() == [1234]')
        workload = '__name__ = "probe"\n' + helper + '\n' + workload
        code = '__name__ = "probe"\n' + helper + '\n' + '''
with open('/etc/passwd', 'a') as f:
    f.write('swapfixture:x:1234:1234::/tmp:/bin/sh\\n')
with open('/etc/group', 'a') as f:
    f.write('swapfixture:x:1234:\\n')
''' + '\nsys.argv = ' + repr(['bootstrap', '512', args.swap_tools, '1234:1234',
                               args.python, '-c', workload]) + '\nmain()\n'
    tmp = tempfile.mkdtemp(prefix='ms-', dir=args.state_parent)
    try:
        env = dict(os.environ, HOME=tmp + '/h', MSB_HOME=tmp + '/e',
                   MSB_LIBKRUNFW_PATH=args.firmware, TMPDIR=tmp)
        Path(env['HOME']).mkdir(mode=0o700)
        name = 'swap-probe'
        def run(command, timeout):
            result = subprocess.run([args.msb, *command], env=env,
                                    timeout=timeout, capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(f'msb {command[0]} failed ({result.returncode}):\n'
                                   + result.stdout[-16384:] + result.stderr[-16384:])
            return result
        try:
            run(['image', 'load', '--input', args.image, '--tag', 'mbox-probe:latest', '--quiet'], 120)
            result = run(['run', '--name', name, '--cpus', '2', '--memory', '512M',
                '--user', '0:0', '--net', 'none', '--no-tty', '--timeout', '180s',
                '--mount-dir', '/nix/store:/nix/store:ro',
                '--mount-owned', '/run/mbox-swap:kind=disk,size=768M,nodev,nosuid,noexec',
                '--replace', 'mbox-probe:latest', '--', args.python, '-c', code], 210)
            print(result.stdout)
            assert '"result": "PASS"' in result.stdout, result.stderr
        finally:
            # Never delete backing storage while a possibly live VM still owns it.
            # Preserve it for manual cleanup if the fixed-name removal fails.
            try:
                run(['remove', name, '--force'], 30)
            except Exception as error:
                raise RuntimeError(f'VM removal failed: inspect private engine {tmp}') from error
            shutil.rmtree(tmp)
    except Exception:
        if Path(tmp).exists():
            print(f'Private probe state retained for inspection: {tmp}')
        raise


if __name__ == '__main__':
    main()
