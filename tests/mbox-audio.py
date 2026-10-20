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
print('PASS: independent host-side playback/capture tunnel policy')

if len(sys.argv) == 1:
    sys.exit(0)
assert len(sys.argv) == 4 and sys.argv[1] == '--runtime'
pulse, pactl = sys.argv[2:]
processes = []
with tempfile.TemporaryDirectory(prefix='mbox-audio-test-') as temp:
    temp = Path(temp)
    env = os.environ.copy()
    for key in list(env):
        if key.startswith('PULSE_') or key in ('DISPLAY', 'XAUTHORITY', 'DBUS_SESSION_BUS_ADDRESS'):
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
            if path.is_socket():
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
                                 pulse, pactl, server, str(int(playback)), str(int(mic)), str(runtime)], env)
            ready(runtime / 'pulse', bridge, log)
            endpoint = 'unix:' + str(runtime / 'pulse')
            sinks = json.loads(ctl(endpoint, '--format=json', 'list', 'sinks').stdout)
            sources = json.loads(ctl(endpoint, '--format=json', 'list', 'sources').stdout)
            assert [s['name'] for s in sinks] == (['mbox_output'] if playback else []), sinks
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
        print('PASS: real private Pulse servers expose only selected directions; guest module loading/exit denied')
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
