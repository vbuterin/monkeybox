#!/usr/bin/env python3
"""Private two-compositor lifecycle regression. Usage: TEST SWAY FOOT.
No live display, clipboard, input, profile, VM or bus is accessed.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

repo = Path(__file__).resolve().parents[1]
source = repo / 'programs/mbox/desktop-session.py'
spec = importlib.util.spec_from_file_location('session', source)
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)

def has_window(node):
    return bool(node.get('shell') or node.get('window')) or any(
        has_window(child) for key in ('nodes', 'floating_nodes')
        for child in node.get(key, []))


sway, foot = sys.argv[1:]
with tempfile.TemporaryDirectory(prefix='mbox-session-') as tmp:
    root = Path(tmp)
    for name in ('home', 'outer', 'inner'):
        (root / name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(root / 'home'), XDG_RUNTIME_DIR=str(root / 'outer'),
               WLR_BACKENDS='headless', WLR_RENDERER='pixman', WLR_LIBINPUT_NO_DEVICES='1',
               DBUS_SESSION_BUS_ADDRESS=f'unix:path={root}/no-bus',
               DBUS_SYSTEM_BUS_ADDRESS=f'unix:path={root}/no-system-bus')
    for key in ('SWAYSOCK', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'DISPLAY', 'XAUTHORITY'):
        env.pop(key, None)
    (root / 'config').write_text('output * mode 800x600\nxwayland disable\n')
    children = []

    def wait_for(predicate):
        for _ in range(200):
            if predicate():
                return
            time.sleep(.05)
        raise AssertionError('fixture timeout: ' + (root / 'log').read_text()[-6000:])

    def sockets(runtime, pattern):
        return [p for p in (root / runtime).glob(pattern) if p.is_socket()]

    with (root / 'log').open('w') as log:
        def launch(args, environment):
            child = subprocess.Popen(args, env=environment, stdout=log, stderr=log)
            children.append(child)
            return child

        try:
            outer = launch([sway, '-c', str(root / 'config')], env)
            wait_for(lambda: sockets('outer', 'wayland-*') and sockets('outer', 'sway-ipc*'))
            outer_ipc = str(sockets('outer', 'sway-ipc*')[0])
            other_env = dict(env, WAYLAND_DISPLAY=str(sockets('outer', 'wayland-*')[0]))
            unrelated = launch([foot, '--config=/dev/null', 'sleep', '600'], other_env)
            wait_for(lambda: has_window(session.request(outer_ipc, 4)))
            for mode in ('last-window', 'multiple-windows', 'outer-window', 'no-window', 'disabled', 'compositor-exit', 'app-exit'):
                guest = dict(env, XDG_RUNTIME_DIR=str(root / 'inner'), WLR_BACKENDS='wayland',
                             WAYLAND_DISPLAY=str(sockets('outer', 'wayland-*')[0]))
                inner = launch([sway, '-c', str(root / 'config')], guest)
                wait_for(lambda: sockets('inner', 'wayland-*') and sockets('inner', 'sway-ipc*'))
                ipc = str(sockets('inner', 'sway-ipc*')[0])
                guest['WAYLAND_DISPLAY'] = str(sockets('inner', 'wayland-*')[0])
                if mode == 'app-exit':
                    child = launch([sys.executable, str(source), ipc, 'sh', '-c', 'exit 7'], guest)
                    assert child.wait(timeout=10) == 7
                elif mode in ('no-window', 'disabled', 'compositor-exit'):
                    child = launch([sys.executable, str(source), ipc, 'sleep', '600'], guest)
                    time.sleep(1)
                    if mode == 'disabled':
                        session.request(ipc, 0, b'output * disable')
                        time.sleep(3)
                        assert child.poll() is None, 'disabled output is not closed'
                        session.request(ipc, 0, b'output * enable')
                    time.sleep(1)
                    assert child.poll() is None, 'audio/terminal-only command must survive'
                    if mode == 'compositor-exit':
                        inner.terminate()
                        inner.wait(timeout=5)
                        assert child.wait(timeout=12) == 1
                    else:
                        session.request(outer_ipc, 0, f'[pid={inner.pid}] kill'.encode())
                        assert child.wait(timeout=12) == 0
                else:
                    pidfile = root / 'app-pid'
                    app = ('import os, subprocess, sys, time; '
                           'open(sys.argv[2], "w").write(str(os.getpid())); '
                           'subprocess.run([sys.argv[1], "--config=/dev/null", "sleep", "600"]); '
                           'time.sleep(600)')
                    child = launch([sys.executable, str(source), ipc, sys.executable,
                                    '-c', app, foot, str(pidfile)], guest)
                    wait_for(lambda: has_window(session.request(ipc, 4)))
                    time.sleep(1)  # let lifecycle observer see the initial window
                    assert child.poll() is None
                    app_pid = int(pidfile.read_text())
                    if mode == 'multiple-windows':
                        second = launch([foot, '--config=/dev/null', 'sleep', '600'], guest)
                        time.sleep(1)
                        assert second.poll() is None
                        session.request(ipc, 0, f'[pid={second.pid}] kill'.encode())
                        second.wait(timeout=5)
                        assert has_window(session.request(ipc, 4))
                        assert child.poll() is None, 'closing secondary window ended session'
                    if mode == 'last-window':
                        session.request(ipc, 0, b'[all] kill')
                        wait_for(lambda: not has_window(session.request(ipc, 4)))
                        time.sleep(3)
                        assert child.poll() is None, 'windowless app must keep running'
                    session.request(outer_ipc, 0, f'[pid={inner.pid}] kill'.encode())
                    assert child.wait(timeout=12) == 0
                    assert not Path(f'/proc/{app_pid}').exists(), 'windowless app survived'
                # The production wrapper's EXIT trap owns compositor teardown.
                if inner.poll() is None:
                    inner.terminate()
                inner.wait(timeout=5)
                wait_for(lambda: not sockets('inner', 'sway-ipc*'))
                assert outer.poll() is None, 'host compositor must survive'
                assert unrelated.poll() is None, 'unrelated outer desktop app must survive'
            print('PASS: windowless apps survive; outer-window close cleans app; command exit status preserved')
        finally:
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
