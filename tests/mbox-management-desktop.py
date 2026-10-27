#!/usr/bin/env python3
"""Opt-in packaged fixture: private Wayland/Pulse, real VM, diagnostics and kill.
No production profile, input injection, camera device or audio output is used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--wrapper', required=True)
p.add_argument('--sway', required=True)
p.add_argument('--pulse', required=True)
p.add_argument('--swap', action='store_true')
a = p.parse_args()
script = Path(a.wrapper).read_text()
hook = re.search(r'HOOK_FILE="([^"]+)"', script)
assert hook and 'MBOX_DESKTOP_TEST_FIXTURE' in Path(hook[1]).read_text(), 'only packaged test-fixture accepted'
manager = re.search(r'(/nix/store/[^\s";]+/bin/mbox-manage) register', script)
assert manager, 'fixture predates management changes'
manager = manager[1]
root = Path(tempfile.mkdtemp(prefix='md-', dir='/tmp'))
print('Private fixture:', root, flush=True)
for name in ('home', 'runtime', 'project', 'state'):
    (root/name).mkdir(mode=0o700)
env = dict(os.environ, HOME=str(root/'home'), XDG_RUNTIME_DIR=str(root/'runtime'),
           MBOX_STATE_ROOT=str(root/'state'), MSB_HOME=str(root/'engine'), TMPDIR=str(root),
           WLR_BACKENDS='headless', WLR_RENDERER='pixman', WLR_LIBINPUT_NO_DEVICES='1',
           DBUS_SESSION_BUS_ADDRESS='unix:path='+str(root/'no-session-bus'),
           DBUS_SYSTEM_BUS_ADDRESS='unix:path='+str(root/'no-system-bus'))
for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'SWAYSOCK', 'PULSE_SERVER', 'PULSE_CLIENTCONFIG', 'MBOX_PROJECT'):
    env.pop(key, None)
(root/'sway.conf').write_text('output HEADLESS-1 mode 800x600\n')
(root/'pulse.conf').write_text(f'load-module module-native-protocol-unix socket={root}/runtime/pulse-socket auth-anonymous=1\nload-module module-null-sink sink_name=fixture\n')
children = []
logs = []
instance = None
passed = False
registry = Path(f'/run/user/{os.getuid()}/monkeybox')
try:
    for cmd, name in (([a.sway, '-c', str(root/'sway.conf')], 'sway'),
                      ([a.pulse, '-n', '-F', str(root/'pulse.conf'), '--daemonize=no', '--exit-idle-time=-1', '--use-pid-file=no'], 'pulse')):
        log = (root/(name+'.log')).open('w'); logs.append(log)
        children.append(subprocess.Popen(cmd, env=env, stdout=log, stderr=log))
    for _ in range(150):
        displays = [x for x in (root/'runtime').glob('wayland-*') if x.is_socket()]
        if displays and (root/'runtime/pulse-socket').is_socket(): break
        assert all(x.poll() is None for x in children), 'fixture service exited; inspect logs'
        time.sleep(.1)
    assert displays and (root/'runtime/pulse-socket').is_socket()
    env['WAYLAND_DISPLAY'] = displays[0].name
    env['PULSE_SERVER'] = 'unix:'+str(root/'runtime/pulse-socket')
    log = (root/'wrapper.log').open('w'); logs.append(log)
    cmd = [a.wrapper, str(root/'project'), '-va', '--network', 'blocked']
    if a.swap: cmd += ['-s', '--swap-mib', '512']
    cmd += ['--', 'bash', '-c', 'set -e; wayland-info >/dev/null; pactl info >/dev/null; id > fixture-id; touch fixture-ready; exec sleep 120']
    instance = subprocess.Popen(cmd, env=env, stdout=log, stderr=log)
    for _ in range(600):
        if (root/'project/fixture-ready').exists(): break
        assert instance.poll() is None, (root/'wrapper.log').read_text()
        time.sleep(.1)
    assert (root/'project/fixture-ready').exists(), 'guest startup deadline'
    records = list(registry.glob(f'{instance.pid}-*.json'))
    assert len(records) == 1, records
    key = records[0].stem
    def manage(op, *flags):
        # The CLI prints a text report by default; the registry-facing JSON form
        # is what this fixture parses.
        return json.loads(subprocess.check_output([manager, op, '--json', *flags, key], env=env, timeout=15))
    report = manage('diagnostics')
    data = json.loads(report['text'])
    assert 'guest' in data and data['guest']['MemTotal_KiB'] > 0, data
    guest_id = re.search(r'uid=([0-9]+).*gid=([0-9]+)', (root/'project/fixture-id').read_text())
    assert guest_id and int(guest_id[1]) == os.getuid() and int(guest_id[2]) == os.getgid(), 'guest identity mismatch'
    assert (data['guest']['SwapTotal_KiB'] > 0) == a.swap, data
    assert Path(report['path']).stat().st_mode & 0o777 == 0o600
    targets = data['host_processes']
    print(manage('kill'), flush=True)
    instance.wait(timeout=20)
    assert not records[0].exists(), 'launcher failed to unregister'
    for _ in range(100):
        survivors = []
        for target in targets:
            path = Path('/proc')/str(target['pid'])/'stat'
            try: fields = path.read_text().rsplit(')', 1)[1].split()
            except FileNotFoundError: continue
            if fields[19] == target['start'] and fields[0] not in ('Z', 'X', 'x'):
                survivors.append(target)
        if not survivors: break
        time.sleep(.1)
    assert not survivors, survivors
    print('PASS: packaged desktop/isolated audio, guest counters, private diagnostics, pidfd termination and launcher unregister', flush=True)
    Path(report['path']).unlink()
    passed = True
finally:
    if instance is not None and instance.poll() is None:
        # This fixture owns this launcher; ordinary cancellation is safe here.
        instance.terminate()
        try: instance.wait(timeout=20)
        except subprocess.TimeoutExpired:
            print('Launcher did not exit; preserve engine and inspect', root, flush=True)
    for child in reversed(children):
        if child.poll() is None: child.terminate()
        try: child.wait(timeout=5)
        except subprocess.TimeoutExpired: child.kill(); child.wait()
    for log in logs: log.close()
    # Remove sandbox backing only after verified termination. Keep logs.
    if passed:
        msb = re.search(r'MSB="\$\{MSB_PATH:-([^}]+)\}"', script)[1]
        name = 'mbox-' + hashlib.sha256(str(root/'project').encode()).hexdigest()[:12]
        subprocess.run([msb, 'remove', name, '--force'], env=env, check=True,
                       capture_output=True, timeout=30)
    print('Private logs retained (sandbox removed on success):', root, flush=True)
