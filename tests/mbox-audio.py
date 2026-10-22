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
module = (root / 'modules/nixos/mbox.nix').read_text()
assert '${bridgePulse}/bin/pulseaudio' in module
assert '../../patches/mbox-pulse-playback-move.patch' in module
move_patch = (root / 'patches/mbox-pulse-playback-move.patch').read_text()
assert '#ifdef TUNNEL_SINK' in move_patch
assert '+        pa_tagstruct_put_boolean(reply, false); /* no_move */' in move_patch
assert '         pa_tagstruct_put_boolean(reply, true); /* no_move */' in move_patch
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
# Missing monitor metadata is unknown, not permission to capture host output.
# Exercise main's selection path without opening a real audio device/server.
with tempfile.TemporaryDirectory() as directory:
    argv = ['audio-host.py', 'pulse', 'pactl', 'dbus-run-session',
            'unix:/unused', '0', '1', directory]
    with patch.object(audio.sys, 'argv', argv), \
            patch.object(audio.subprocess, 'check_output', side_effect=[
                'unknown-source\n', json.dumps([{'name': 'unknown-source'}])]), \
            patch.object(audio.subprocess, 'Popen', side_effect=AssertionError(
                'source with missing monitor metadata reached daemon startup')) as spawn:
        try:
            audio.main()
        except ValueError:
            pass
        else:
            raise AssertionError('source with missing monitor metadata accepted')
        spawn.assert_not_called()
    # Explicit non-monitor markers retain compatibility. Stop at mocked Popen,
    # before any real process, signal handler, or readiness loop is involved.
    class StartupReached(Exception):
        pass
    for metadata in ({'monitor_of_sink': None}, {'monitor_of_sink': 4294967295},
                     {'monitor_source': ''},
                     {'monitor_source': '', 'monitor_of_sink': 4294967295}):
        with patch.object(audio.sys, 'argv', argv), \
                patch.object(audio.subprocess, 'check_output', side_effect=[
                    'mic\n', json.dumps([{'name': 'mic', **metadata}])]), \
                patch.object(audio.subprocess, 'Popen', side_effect=StartupReached) as spawn:
            try:
                audio.main()
            except StartupReached:
                pass
            else:
                raise AssertionError('explicit non-monitor marker did not reach startup')
            spawn.assert_called_once()
    for marker in (0, 7, 'unknown'):
        with patch.object(audio.sys, 'argv', argv), \
                patch.object(audio.subprocess, 'check_output', side_effect=[
                    'source\n', json.dumps([{'name': 'source', 'monitor_of_sink': marker}])]), \
                patch.object(audio.subprocess, 'Popen', side_effect=AssertionError('monitor accepted')) as spawn:
            try:
                audio.main()
            except ValueError:
                pass
            else:
                raise AssertionError('invalid monitor marker accepted')
            spawn.assert_not_called()
# Real pactl JSON field semantics, malformed and contradictory provider fields.
for metadata in ({'monitor_source': 'sink'}, {'monitor_source': None},
                 {'monitor_source': False}, {'monitor_source': 0},
                 {'monitor_source': '', 'monitor_of_sink': 0},
                 {'monitor_source': 'sink', 'monitor_of_sink': None},
                 {'monitor_source': '', 'properties': {'device.class': 'monitor'}},
                 {'monitor_source': '', 'name': 'sink.monitor'},
                 {'monitor_of_sink': False}, {}):
    assert not audio.is_non_monitor({'name': 'source', **metadata}), metadata
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
                    'load-module module-null-sink sink_name=headphones\n'
                    'set-default-sink host_output\n'
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
            if playback:
                streams = json.loads(ctl(server, '--format=json', 'list', 'sink-inputs').stdout)
                assert len(streams) == 1, streams
                stream = streams[0]['index']
                # Host policy must be able to move this invocation's playback
                # stream on an output-device switch, without restarting the VM.
                ctl(server, 'set-default-sink', 'headphones')
                ctl(server, 'move-sink-input', str(stream), 'headphones')
                devices = json.loads(ctl(server, '--format=json', 'list', 'sinks').stdout)
                target = next(s['index'] for s in devices if s['name'] == 'headphones')
                streams = json.loads(ctl(server, '--format=json', 'list', 'sink-inputs').stdout)
                assert streams[0]['sink'] == target, streams
                ctl(server, 'move-sink-input', str(stream), 'host_output')
                ctl(server, 'set-default-sink', 'host_output')
            if mic:
                # A playback-route fix must not make capture movable. Even an
                # explicit host request must fail to redirect it to a monitor.
                streams = json.loads(ctl(server, '--format=json', 'list', 'source-outputs').stdout)
                assert len(streams) == 1, streams
                moved = ctl(server, 'move-source-output', str(streams[0]['index']),
                            'headphones.monitor', check=False)
                assert moved.returncode != 0, moved
            bridge.terminate()
            bridge.wait(timeout=5)
            assert not (runtime / 'ready').exists()
            log.seek(0)
            messages = log.read().decode()
            assert 'already taken' not in messages and 'does not exist' not in messages, messages
        # Actual pactl JSON must distinguish a null microphone from a monitor,
        # including an explicitly selected host monitor (no bridge may start).
        host_sources = json.loads(ctl(server, '--format=json', 'list', 'sources').stdout)
        assert audio.is_non_monitor(next(s for s in host_sources if s['name'] == 'fixture_mic'))
        assert not audio.is_non_monitor(next(s for s in host_sources if s['name'] == 'host_output.monitor'))
        ctl(server, 'set-default-source', 'host_output.monitor')
        runtime = temp / 'monitor-refused'
        runtime.mkdir(mode=0o700)
        bridge, log = start([sys.executable, str(root / 'programs/mbox/audio-host.py'),
                             pulse, pactl, dbus_run_session, server, '1', '1', str(runtime)], env)
        assert bridge.wait(timeout=8) != 0
        assert not (runtime / 'default.pa').exists() and not (runtime / 'ready').exists()
        ctl(server, 'set-default-source', 'fixture_mic')
        # An unreachable upstream must fail before granting a ready bridge.
        runtime = temp / 'broken-bridge'
        runtime.mkdir(mode=0o700)
        bridge, log = start([sys.executable, str(root / 'programs/mbox/audio-host.py'),
                             pulse, pactl, dbus_run_session, 'unix:' + str(temp / 'missing'),
                             '1', '0', str(runtime)], env)
        assert bridge.wait(timeout=12) != 0
        assert not (runtime / 'ready').exists()
        print('PASS: real private Pulse playback moves both ways; capture cannot move to monitor; directions/defaults/readiness; guest module loading/exit denied; unreachable upstream fails closed')
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
