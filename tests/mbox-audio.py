#!/usr/bin/env python3
"""Pure policy tests; --runtime PULSEAUDIO PACTL also tests private real servers."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import shutil
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('audio', root / 'programs/mbox/audio-host.py')
audio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio)
for playback, microphone in ((False, True), (True, False), (True, True)):
    conf = audio.configuration('unix:/tmp/host socket', playback, microphone, Path('/tmp/private'), 'mic')
    assert ('module-tunnel-sink ' in conf) == playback
    assert ('module-tunnel-source ' in conf) == microphone
    assert 'socket="/tmp/private/pulse"' in conf
    assert 'source="mic"' in conf or not microphone
    assert 'set-default-' not in conf  # tunnels are asynchronous
try:
    audio.configuration('unix:/tmp/host', False, True, Path('/tmp/private'), 'sink.monitor')
except ValueError:
    pass
else:
    raise AssertionError('monitor allowed as microphone')
source = (root / 'programs/mbox/mbox.sh.in').read_text()
assert '--vsock "$pulse_socket:5002"' not in source
assert '--vsock "$AUDIO_RUNTIME/pulse:5002"' in source
assert '--disallow-module-loading=yes' in Path(audio.__file__).read_text()
# A socket that answers before its tunnel device exists must not signal ready.
with tempfile.TemporaryDirectory() as directory:
    runtime = Path(directory)
    class Child:
        def poll(self):
            return None
    calls = []
    def delayed_devices(args, **kwargs):
        calls.append(args)
        if 'sinks' in args:
            value = [] if len(calls) < 3 else [{'name': 'mbox_output'}]
        elif 'sources' in args:
            value = []
        else:
            assert args[-2:] == ['set-default-sink', 'mbox_output']
            assert not (runtime / 'ready').exists()
            value = ''
        return subprocess.CompletedProcess(args, 0, json.dumps(value))
    with patch.object(audio.subprocess, 'run', side_effect=delayed_devices):
        audio.wait_ready(Child(), 'pactl', {}, runtime, True, False)
    assert (runtime / 'ready').exists() and len(calls) == 5
    (runtime / 'ready').unlink()
    with patch.object(Child, 'poll', return_value=1):
        try:
            audio.wait_ready(Child(), 'pactl', {}, runtime, True, False)
        except RuntimeError:
            pass
        else:
            raise AssertionError('dead daemon accepted')
    assert not (runtime / 'ready').exists()
print('PASS: independent audio policy and asynchronous readiness/dead-daemon handling')

if len(sys.argv) == 1:
    sys.exit(0)
assert len(sys.argv) == 4 and sys.argv[1] == '--runtime'
pulse, pactl = sys.argv[2:]
dbus_run_session = shutil.which('dbus-run-session')
assert dbus_run_session, 'dbus-run-session required'
# Reproduce a desktop bus already owned by another Pulse server, without
# ever using the live user's bus. The host fixture below acquires that name.
if os.environ.get('MBOX_AUDIO_TEST_PRIVATE_BUS') != '1':
    env = dict(os.environ, MBOX_AUDIO_TEST_PRIVATE_BUS='1')
    with tempfile.TemporaryDirectory(prefix='mbox-audio-test-bus-') as directory:
        config = audio.bus_configuration(Path(directory))
        sys.exit(subprocess.call([dbus_run_session, '--config-file=' + str(config), '--',
                                  sys.executable, __file__, *sys.argv[1:]], env=env))
processes = []
with tempfile.TemporaryDirectory(prefix='mbox-audio-test-') as temp:
    temp = Path(temp)
    env = os.environ.copy()
    for key in list(env):
        if key.startswith('PULSE_') or key in ('DISPLAY', 'XAUTHORITY'):
            env.pop(key)
    env['HOME'] = str(temp)
    env['XDG_RUNTIME_DIR'] = str(temp)
    env['PULSE_RUNTIME_PATH'] = str(temp / 'host')
    env['PULSE_STATE_PATH'] = str(temp / 'host-state')
    (temp / 'host').mkdir(mode=0o700)
    server = 'unix:' + str(temp / 'host/native')
    conf = temp / 'host.pa'
    conf.write_text('.fail\nload-module module-null-sink sink_name=host_output\n'
                    'load-module module-null-source source_name=fixture_mic\n'
                    'set-default-source fixture_mic\n'
                    'load-module module-native-protocol-unix socket=' + str(temp / 'host/native') + ' auth-anonymous=yes\n')
    def start(args, child_env):
        log = tempfile.TemporaryFile()
        proc = subprocess.Popen(args, env=child_env, stdout=log, stderr=log)
        processes.append((proc, log))
        return proc, log
    def ready(path, proc, log):
        for _ in range(150):
            if path.exists() and proc.poll() is None:
                return
            if proc.poll() is not None:
                break
            time.sleep(.02)
        log.seek(0)
        raise AssertionError(log.read().decode())
    def ctl(endpoint, *args, check=True):
        return subprocess.run([pactl, '--server', endpoint, *args], env=env, text=True,
                              capture_output=True, timeout=5, check=check)
    try:
        host, log = start([pulse, '-n', '-F', str(conf), '--daemonize=no', '--use-pid-file=no',
                           '--exit-idle-time=-1', '--log-target=stderr'], env)
        ready(temp / 'host/native', host, log)
        for playback, mic in ((True, False), (False, True), (True, True)):
            runtime = temp / f'bridge-{int(playback)}-{int(mic)}'
            runtime.mkdir(mode=0o700)
            bridge, log = start([sys.executable, str(root / 'programs/mbox/audio-host.py'),
                                 pulse, pactl, dbus_run_session, server, str(int(playback)), str(int(mic)), str(runtime)], env)
            ready(runtime / 'ready', bridge, log)
            endpoint = 'unix:' + str(runtime / 'pulse')
            sinks = json.loads(ctl(endpoint, '--format=json', 'list', 'sinks').stdout)
            sources = json.loads(ctl(endpoint, '--format=json', 'list', 'sources').stdout)
            assert [s['name'] for s in sinks] == (['mbox_output'] if playback else []), sinks
            if playback:
                assert ctl(endpoint, 'get-default-sink').stdout.strip() == 'mbox_output'
            if mic:
                assert ctl(endpoint, 'get-default-source').stdout.strip() == 'mbox_microphone'
            names = {s['name'] for s in sources}
            assert names == ({'mbox_output.monitor'} if playback else set()) | ({'mbox_microphone'} if mic else set()), names
            for module in ('module-tunnel-source server=' + server, 'module-tunnel-sink server=' + server,
                           'module-cli-protocol-unix'):
                result = ctl(endpoint, 'load-module', *module.split(), check=False)
                assert result.returncode != 0, result
            result = ctl(endpoint, 'exit', check=False)
            assert result.returncode != 0 and bridge.poll() is None
            bridge.terminate()
            bridge.wait(timeout=5)
            assert not (runtime / 'ready').exists()
            log.seek(0)
            messages = log.read().decode()
            assert 'already taken' not in messages and 'does not exist' not in messages, messages
        # An unreachable upstream must fail before granting a ready bridge.
        runtime = temp / 'broken-bridge'
        runtime.mkdir(mode=0o700)
        bridge, log = start([sys.executable, str(root / 'programs/mbox/audio-host.py'),
                             pulse, pactl, dbus_run_session, 'unix:' + str(temp / 'missing'),
                             '1', '0', str(runtime)], env)
        assert bridge.wait(timeout=12) != 0
        assert not (runtime / 'ready').exists()
        print('PASS: real private Pulse directions/defaults/readiness on an occupied session bus; guest module loading/exit denied; unreachable upstream fails closed')
    finally:
        for proc, log in reversed(processes):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            log.close()
