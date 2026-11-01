#!/usr/bin/env python3
"""Actual guest shell/bridges in private bwrap /tmp, not a VM.
Usage: TEST SWAY SATELLITE XIM IM_CACHE FONTCONFIG
No live desktop, app profile, clipboard, network or bus is used.
"""
import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time

repo = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('session', repo / 'programs/mbox/desktop-session.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
sway, satellite, xim, cache, fonts = sys.argv[1:]
with tempfile.TemporaryDirectory(prefix='mbw-') as tmp:
    root = Path(tmp)
    for name in ('home', 'outer'):
        (root / name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(root / 'home'), XDG_RUNTIME_DIR=str(root / 'outer'),
               WLR_BACKENDS='headless', WLR_RENDERER='pixman', WLR_LIBINPUT_NO_DEVICES='1',
               DBUS_SESSION_BUS_ADDRESS=f'unix:path={root}/no-bus',
               DBUS_SYSTEM_BUS_ADDRESS=f'unix:path={root}/no-system-bus')
    for key in ('SWAYSOCK', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'DISPLAY', 'XAUTHORITY'):
        env.pop(key, None)
    text = (repo / 'programs/mbox/clipboard-desktop.sh.in').read_text()
    helper = root / 'supervisor'
    helper.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable, '-I',
                      str(repo / 'programs/mbox/desktop-session.py')]) + ' "$@"\n')
    helper.chmod(0o700)
    for key, value in dict(SWAY=sway, SATELLITE=satellite, XIM=xim, IM_CACHE=cache,
                           FONTCONFIG=fonts, FONTCONFIG_FALLBACK=fonts, SESSION=str(helper)).items():
        text = text.replace(f'@@{key}@@', shlex.quote(value))
    (root / 'wrapper').write_text(text)
    (root / 'config').write_text('output * mode 800x600\nxwayland disable\n')
    children = []
    with (root / 'log').open('w') as log:
        def launch(args, environment):
            p = subprocess.Popen(args, env=environment, stdout=log, stderr=log)
            children.append(p)
            return p

        def wait_for(predicate):
            for _ in range(200):
                if predicate():
                    return
                time.sleep(.05)
            raise AssertionError((root / 'log').read_text()[-6000:])

        try:
            outer = launch([sway, '-c', str(root / 'config')], env)
            wait_for(lambda: list((root / 'outer').glob('sway-ipc*')))
            ipc = str(next((root / 'outer').glob('sway-ipc*')))
            env['WAYLAND_DISPLAY'] = str(next(p for p in (root / 'outer').glob('wayland-*') if p.is_socket()))
            for action in ('exit', 'close', 'term'):
                marker = root / action
                code = ('import os,time; '
                        f'open({str(marker)!r}, "w").write(str(os.getpid())); ' +
                        ('raise SystemExit(42)' if action == 'exit' else 'time.sleep(600)'))
                # Only /tmp is replaced; fixture sockets are rebound explicitly.
                # Private /tmp gives Xwayland its own X20 and runtime directory.
                command = ['bwrap', '--die-with-parent', '--dev-bind', '/', '/',
                           '--tmpfs', '/tmp', '--bind', tmp, tmp, '--unshare-net',
                           'bash', str(root / 'wrapper'), 'side-by-side', sys.executable, '-c', code]
                child = launch(command, env)
                wait_for(marker.exists)
                pid = int(marker.read_text())
                # Record wrapper descendants before they disappear, without
                # reading command lines or touching unrelated processes.
                def descendants(parent):
                    selected = {parent}
                    entries = {}
                    for entry in Path('/proc').glob('[0-9]*/stat'):
                        try:
                            fields = entry.read_text().rsplit(')', 1)[1].split()
                            entries[int(entry.parent.name)] = (int(fields[1]), fields[19], fields[0])
                        except (OSError, ValueError):
                            continue
                    while True:
                        more = {p for p, (pp, _, _) in entries.items() if pp in selected}
                        if more <= selected:
                            break
                        selected |= more
                    return {p: entries[p][1] for p in selected if p in entries}
                targets = descendants(child.pid)
                if action == 'close':
                    m.request(ipc, 0, b'[all] kill')
                elif action == 'term':
                    # Signal the guest wrapper, not bwrap's namespace supervisor.
                    wrappers = list((Path('/proc') / str(pid) / 'status').read_text().splitlines())
                    parent = int(next(line.split()[1] for line in wrappers if line.startswith('PPid:')))
                    parent_status = (Path('/proc') / str(parent) / 'status').read_text().splitlines()
                    wrapper_pid = int(next(line.split()[1] for line in parent_status if line.startswith('PPid:')))
                    os.kill(wrapper_pid, 15)
                expected = {'exit': 42, 'close': 0, 'term': 143}[action]
                assert child.wait(timeout=20) == expected, (action, (root / 'log').read_text()[-6000:])
                def gone():
                    for p, start in targets.items():
                        try:
                            fields = (Path('/proc') / str(p) / 'stat').read_text().rsplit(')', 1)[1].split()
                        except FileNotFoundError:
                            continue
                        if fields[19] == start and fields[0] not in ('Z', 'X', 'x'):
                            return False
                    return True
                wait_for(gone)
                assert outer.poll() is None
            print('PASS: actual guest wrapper exit/outer-close/cancel, compositor + Xwayland + XIM + app teardown')
        finally:
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
