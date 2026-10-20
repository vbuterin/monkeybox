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


def quote(value):
    # Pulse's startup/module argument parser uses double quotes and backslashes.
    if any(c in value for c in '\n\r\0'):
        raise ValueError('invalid Pulse argument')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def configuration(server, playback, microphone, runtime, source=None):
    lines = ['.fail']
    if playback:
        lines.append('load-module module-tunnel-sink server=' + quote(server) + ' sink_name=mbox_output')
        lines.append('set-default-sink mbox_output')
    if microphone:
        if not source or source.endswith('.monitor'):
            raise ValueError('microphone must be a non-monitor source')
        lines.append('load-module module-tunnel-source server=' + quote(server) +
                     ' source=' + quote(source) + ' source_name=mbox_microphone')
        lines.append('set-default-source mbox_microphone')
    lines.append('load-module module-native-protocol-unix socket=' + quote(str(runtime / 'pulse')) +
                 ' auth-anonymous=yes')
    return '\n'.join(lines) + '\n'


def main():
    pulse, pactl, server, playback, microphone, directory = sys.argv[1:]
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
        # Pulse uses UINT32_MAX (or null in some implementations) for non-monitors.
        if selected is None or selected.get('monitor_of_sink') not in (None, 4294967295):
            raise ValueError('default source is a monitor, not a microphone; select a microphone on the host')
    config = runtime / 'default.pa'
    config.write_text(configuration(server, playback == '1', microphone == '1', runtime, source))
    daemon_config = runtime / 'daemon.conf'
    daemon_config.write_text('enable-shm = no\nenable-memfd = no\n')
    env['PULSE_CONFIG'] = str(daemon_config)
    # Private state/runtime avoids touching the live host daemon. The tunnel alone
    # reads the host authentication cookie (if needed); no cookie goes to the VM.
    env['PULSE_RUNTIME_PATH'] = str(runtime)
    env['PULSE_STATE_PATH'] = str(runtime / 'state')
    child = subprocess.Popen([pulse, '--daemonize=no', '--use-pid-file=no', '--exit-idle-time=-1',
                              '--disallow-exit=yes', '--disallow-module-loading=yes',
                              '--log-target=stderr',
                              '-n', '-F', str(config)], env=env)
    def stop(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        return child.wait()
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    sys.exit(main())
