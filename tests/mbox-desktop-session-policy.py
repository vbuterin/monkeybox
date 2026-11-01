#!/usr/bin/env python3
"""Real supervisor/processes against private fake Sway IPC; no desktop or VM."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

root = Path(__file__).resolve().parents[1]
source = root / 'programs/mbox/desktop-session.py'
spec = importlib.util.spec_from_file_location('session', source)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

with tempfile.TemporaryDirectory(prefix='mbox-session-policy-') as tmp:
    path = str(Path(tmp) / 'ipc')
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(path)
    listener.listen()
    outputs = [{'active': True}]
    mode = 'normal'
    calls = []

    def serve():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            with conn:
                try:
                    header = conn.recv(m.HEADER.size, socket.MSG_WAITALL)
                    if len(header) != m.HEADER.size:
                        continue
                    _, size, kind = m.HEADER.unpack(header)
                    payload = conn.recv(size, socket.MSG_WAITALL) if size else b''
                    calls.append((kind, payload))
                    if mode == 'disconnect':
                        continue
                    if mode == 'stall':
                        time.sleep(2.2)
                        continue
                    body = json.dumps(outputs if kind == 3 else [{'success': True}]).encode()
                    magic = b'badipc' if mode == 'bad-magic' else m.MAGIC
                    size = 9 * 1024 * 1024 if mode == 'oversize' else len(body)
                    conn.sendall(m.HEADER.pack(magic, size, kind) + body)
                except (OSError, ValueError):
                    pass
    threading.Thread(target=serve, daemon=True).start()

    def launch(code):
        return subprocess.Popen([sys.executable, str(source), path, sys.executable, '-c', code],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def done(child, expected):
        out, err = child.communicate(timeout=12)
        assert child.returncode == expected, (child.returncode, expected, out, err)

    for code in (0, 7, 42, 127, 143):
        done(launch(f'raise SystemExit({code})'), code)
    done(launch('import os,signal; os.kill(os.getpid(), signal.SIGTERM)'), 143)
    # Preserve stdin even though the wrapper supervises asynchronously.
    stdin_child = subprocess.Popen([sys.executable, str(source), path, sys.executable,
                                   '-c', 'import sys; assert sys.stdin.read() == "fixture"'],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = stdin_child.communicate(b'fixture', timeout=10)
    assert stdin_child.returncode == 0, (out, err)
    # Reap same-process-group background children even after the command exits.
    descendant = Path(tmp) / 'descendant'
    child = launch('import subprocess,sys; '
                   'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(600)"]); '
                   f'open({str(descendant)!r}, "w").write(str(p.pid)); raise SystemExit(9)')
    done(child, 9)
    pid = int(descendant.read_text())
    stat = Path(f'/proc/{pid}/stat')
    assert not stat.exists() or stat.read_text().rsplit(')', 1)[1].split()[0] == 'Z'

    # An already closed desktop must not even start the command.
    marker = Path(tmp) / 'unexpected'
    outputs = []
    done(launch(f'open({str(marker)!r}, "w").close()'), 0)
    assert not marker.exists()
    outputs = [{'active': False}]  # disabled != destroyed
    child = launch('import time; time.sleep(600)')
    time.sleep(.8)
    assert child.poll() is None
    outputs = [{'active': True}]
    time.sleep(.6)
    assert child.poll() is None
    outputs = []
    done(child, 0)
    assert (0, b'[all] kill') in calls

    for action in ('close', 'term', 'int', 'disconnect', 'stall', 'bad-magic', 'oversize'):
        outputs = [{'active': True}]
        mode = 'normal'
        marker = Path(tmp) / action
        child = launch('import os,signal,time; '
                       'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                       f'open({str(marker)!r}, "w").write(str(os.getpid())); '
                       'time.sleep(600)')
        for _ in range(100):
            if marker.exists():
                break
            time.sleep(.02)
        assert marker.exists()
        pid = int(marker.read_text())
        if action == 'close':
            outputs = []
            expected = 0
        elif action in ('term', 'int'):
            sig = signal.SIGTERM if action == 'term' else signal.SIGINT
            child.send_signal(sig)
            expected = 128 + sig
        else:
            mode = action
            expected = 1
        done(child, expected)
        assert not Path(f'/proc/{pid}').exists(), (action, 'stubborn app survived')
    listener.close()

wrapper = (root / 'programs/mbox/clipboard-desktop.sh.in').read_text()
assert 'wait "$session" || status=$?' in wrapper
assert 'exit "$status"' in wrapper
assert '"$ipc" "$@" <&0 &' in wrapper
module = (root / 'modules/nixos/mbox.nix').read_text()
assert '${desktopSession}' in module and '${../../programs/mbox/desktop-session.py}' in module
print('PASS: exit codes/signals, pre-launch close, disabled output, bounded malformed/stalled IPC, stubborn app cleanup, wrapper wiring')
