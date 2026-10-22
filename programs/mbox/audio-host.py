#!/usr/bin/env python3
"""Private, non-reconfigurable Pulse server; never export the host native socket.

Only trusted startup config can load tunnels. The guest can monitor its own
playback, but cannot load modules, discover host devices or connect new tunnels.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def quote(value):
    # Pulse's startup/module argument parser uses double quotes and backslashes.
    if any(c in value for c in '\n\r\0'):
        raise ValueError('invalid Pulse argument')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def is_non_monitor(source):
    """Accept explicit non-monitor markers from pactl, never absence/guesswork.

    PulseAudio pactl JSON calls this field monitor_source (a sink name or "").
    Some providers expose the underlying libpulse monitor_of_sink index instead.
    If both are present, both must explicitly describe a non-monitor.
    """
    if not isinstance(source, dict):
        return False
    name = source.get('name')
    if not isinstance(name, str) or not name or name.endswith('.monitor'):
        return False
    known = False
    if 'monitor_source' in source:
        known = True
        if source['monitor_source'] != '':
            return False
    if 'monitor_of_sink' in source:
        known = True
        marker = source['monitor_of_sink']
        if marker is not None and not (type(marker) is int and marker == 4294967295):
            return False
    properties = source.get('properties', {})
    if not isinstance(properties, dict) or properties.get('device.class') == 'monitor':
        return False
    return known


def configuration(server, playback, microphone, runtime, source=None):
    lines = ['.fail']
    if playback:
        lines.append('load-module module-tunnel-sink server=' + quote(server) + ' sink_name=mbox_output')
    if microphone:
        if not source or source.endswith('.monitor'):
            raise ValueError('microphone must be a non-monitor source')
        lines.append('load-module module-tunnel-source server=' + quote(server) +
                     ' source=' + quote(source) + ' source_name=mbox_microphone')
    lines.append('load-module module-native-protocol-unix socket=' + quote(str(runtime / 'pulse')) +
                 ' auth-anonymous=yes')
    return '\n'.join(lines) + '\n'


def wait_ready(child, pactl, env, runtime, playback, microphone, timeout=8):
    # Tunnel devices are created asynchronously after connecting to the remote
    # server. A listening native socket alone is not a usable audio bridge.
    endpoint = 'unix:' + str(runtime / 'pulse')
    deadline = time.monotonic() + timeout
    def ctl(*args):
        return subprocess.run([pactl, '--server', endpoint, *args], env=env,
                              capture_output=True, text=True, timeout=1, check=True).stdout
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError('private Pulse server exited before audio devices were ready')
        try:
            sinks = json.loads(ctl('--format=json', 'list', 'sinks'))
            sources = json.loads(ctl('--format=json', 'list', 'sources'))
            if playback and not any(s['name'] == 'mbox_output' for s in sinks):
                time.sleep(.05)
                continue
            if microphone and not any(s['name'] == 'mbox_microphone' for s in sources):
                time.sleep(.05)
                continue
            if playback:
                ctl('set-default-sink', 'mbox_output')
            if microphone:
                ctl('set-default-source', 'mbox_microphone')
            if child.poll() is not None:
                raise RuntimeError('private Pulse server exited during audio setup')
            (runtime / 'ready').touch()
            return
        except (subprocess.SubprocessError, json.JSONDecodeError):
            time.sleep(.05)
    raise RuntimeError('timed out waiting for private Pulse audio devices')


def bus_configuration(runtime):
    # No /etc dependency, activation directories, host services or discovery.
    config = runtime / 'bus.conf'
    config.write_text('''<busconfig>
  <type>session</type><listen>unix:tmpdir=/tmp</listen>
  <policy context="default">
    <allow user="*"/><allow own="*"/>
    <allow send_destination="*"/><allow receive_sender="*"/>
  </policy>
</busconfig>
''')
    return config


def main():
    pulse, pactl, dbus_run_session, server, playback, microphone, directory = sys.argv[1:]
    runtime = Path(directory)
    env = os.environ.copy()
    # Never inherit a guest or application-specific client configuration.
    env.pop('PULSE_CLIENTCONFIG', None)
    env['PULSE_SERVER'] = server
    source = None
    if microphone == '1':
        source = subprocess.check_output([pactl, 'get-default-source'], env=env, text=True, timeout=5).strip()
        sources = json.loads(subprocess.check_output([pactl, '--format=json', 'list', 'sources'],
                                                     env=env, text=True, timeout=5))
        selected = next((s for s in sources if s['name'] == source), None)
        if not is_non_monitor(selected):
            raise ValueError('default source is a monitor or lacks monitor metadata; select a microphone on the host')
    config = runtime / 'default.pa'
    config.write_text(configuration(server, playback == '1', microphone == '1', runtime, source))
    daemon_config = runtime / 'daemon.conf'
    daemon_config.write_text('enable-shm = no\nenable-memfd = no\n')
    env['PULSE_CONFIG'] = str(daemon_config)
    # Private state/runtime avoids touching the live host daemon. The tunnel alone
    # reads the host authentication cookie (if needed); no cookie goes to the VM.
    env['PULSE_RUNTIME_PATH'] = str(runtime)
    env['PULSE_STATE_PATH'] = str(runtime / 'state')
    # Never compete for org.pulseaudio.Server on the user's session bus, or
    # let a daemon without DBUS_SESSION_BUS_ADDRESS auto-discover that bus.
    child = subprocess.Popen([dbus_run_session, '--config-file=' + str(bus_configuration(runtime)), '--', pulse, '--daemonize=no', '--use-pid-file=no', '--exit-idle-time=-1',
                              '--disallow-exit=yes', '--disallow-module-loading=yes',
                              '--log-target=stderr',
                              '-n', '-F', str(config)], env=env, start_new_session=True)
    def stop(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        wait_ready(child, pactl, env, runtime, playback == '1', microphone == '1')
        return child.wait()
    finally:
        (runtime / 'ready').unlink(missing_ok=True)
        # dbus-run-session and Pulse belong to this invocation's process group.
        # Stop both even when the session wrapper exited first.
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


if __name__ == '__main__':
    sys.exit(main())
