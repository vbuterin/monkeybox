#!/usr/bin/env python3
"""Private host Sway/Foot paste integration; no live clipboard, input or shell.
Usage: python3 tests/mbox-foot-clipboard.py BROKER FOOT
Foot's search selects fixture text; no real mouse/physical key testing.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from mbox_test_tools import tool

broker, fallback_foot = sys.argv[1:]
foot = tool('foot', 'bin/foot', fallback_foot)
sway = tool('sway-unwrapped', 'bin/sway', '/nix/store/jh2y4bkbdd6a0dkpw6ryz0j8cgy6hg7k-sway-unwrapped-1.12/bin/sway')
wtype = tool('wtype', 'bin/wtype', '/nix/store/z8z1h48kb1jpqs5rgw2m5xh6wlpzyfwv-wtype-0.4/bin/wtype')
with tempfile.TemporaryDirectory(prefix='mbox-foot-test-') as tmp:
    root = Path(tmp)
    for name in ('runtime', 'home'):
        (root/name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(root/'home'), XDG_RUNTIME_DIR=str(root/'runtime'),
               WLR_BACKENDS='headless', WLR_RENDERER='pixman', WLR_LIBINPUT_NO_DEVICES='1',
               DBUS_SESSION_BUS_ADDRESS=f'unix:path={root}/no-bus')
    for key in ('WAYLAND_DISPLAY', 'DISPLAY', 'SWAYSOCK'):
        env.pop(key, None)
    (root/'sway.conf').write_text('output HEADLESS-1 mode 1000x700\n'
                                'bindsym Ctrl+Shift+c nop\nbindsym Ctrl+Shift+v nop\n'
                                f'bindsym --release Ctrl+Shift+v exec {broker} paste\n'
                                f'bindsym --release Ctrl+Shift+c exec {broker} copy\n')
    (root/'foot.ini').write_text('[main]\nfont=monospace:size=12\n'
                                '[key-bindings]\nclipboard-copy=Control+Shift+c XF86Copy\n'
                                'clipboard-paste=Control+Shift+v XF86Paste\n')
    log = (root/'log').open('w')
    compositor = subprocess.Popen([sway, '-c', str(root/'sway.conf')], env=env, stdout=log, stderr=log)
    terminal = None
    keyboard = None
    hold_ms = int(os.environ.get('MBOX_TEST_MODIFIER_HOLD_MS', '0'))
    hold_args = ['-s', str(hold_ms)] if hold_ms > 0 else []
    try:
        for _ in range(100):
            sockets = [p for p in (root/'runtime').glob('wayland-*') if p.is_socket()]
            ipcs = list((root/'runtime').glob('sway-ipc*'))
            if sockets and ipcs:
                break
            time.sleep(.1)
        env.update(WAYLAND_DISPLAY=sockets[0].name, SWAYSOCK=str(ipcs[0]))
        # Keep the headless seat keyboard capability alive before Foot binds it.
        keyboard = subprocess.Popen([wtype, '-s', '60000', ''], env=env)
        time.sleep(.5)
        program = ('import pathlib,signal,time; p=pathlib.Path('+repr(str(root))+'); '
                   'signal.signal(signal.SIGINT,lambda *_:(p/"interrupted").touch()); '
                   'print("EXPLICIT_FOOT_COPY",flush=True); (p/"ready").touch(); '
                   'line=input(); (p/"received").write_text(line); time.sleep(30)')
        terminal = subprocess.Popen([foot, '-c', str(root/'foot.ini'), sys.executable, '-c', program],
                                    env=env, stdout=log, stderr=log)
        for _ in range(150):
            if (root/'ready').exists():
                break
            assert terminal.poll() is None, (root/'log').read_text()
            time.sleep(.1)
        assert (root/'ready').exists(), 'Foot readiness timeout'
        directory = root/'runtime/mbox-clipboard'
        directory.mkdir(mode=0o700)
        # Search and commit selects terminal output without host mouse access.
        subprocess.run([wtype, '-M', 'ctrl', '-M', 'shift', '-k', 'r', '-m', 'shift', '-m', 'ctrl',
                        '-s', '200', 'EXPLICIT_FOOT_COPY', '-s', '200', '-k', 'Return'],
                       env=env, check=True, timeout=10)
        time.sleep(.3)
        subprocess.run([wtype, '-M', 'ctrl', '-M', 'shift', '-k', 'c',
                        *hold_args, '-m', 'shift', '-m', 'ctrl'],
                       env=env, check=True, timeout=10)
        for _ in range(100):
            if (directory/'buffer.json').exists():
                break
            time.sleep(.1)
        assert json.loads((directory/'buffer.json').read_text())['text'] == 'EXPLICIT_FOOT_COPY'
        assert not (root/'interrupted').exists()
        print('FOOT_COMBINED_COPY_OK: search selection -> release binding -> XF86Copy -> export')
        subprocess.run([wtype, '-M', 'ctrl', '-M', 'shift', '-k', 'v',
                        *hold_args, '-m', 'shift', '-m', 'ctrl'],
                       env=env, check=True, timeout=10)
        for _ in range(100):
            if not (directory/'buffer.json').exists():
                break
            time.sleep(.1)
        assert not (directory/'buffer.json').exists(), 'shortcut did not consume buffer'
        time.sleep(3)
        subprocess.run([wtype, '-k', 'Return'], env=env, check=True, timeout=10)
        for _ in range(100):
            if (root/'received').exists():
                break
            time.sleep(.1)
        assert (root/'received').read_text() == 'EXPLICIT_FOOT_COPY', repr((root/'received').read_text())
        assert not (root/'interrupted').exists()
        print('FOOT_COMBINED_PASTE_OK: private release binding -> import -> XF86Paste -> terminal input')
        failure = subprocess.run([broker, 'paste'], env=env, capture_output=True, timeout=15)
        assert failure.returncode == 23, (failure.returncode, failure.stderr)
        assert b'EXPLICIT_FOOT_COPY' not in failure.stderr
        print('BROKER_EXIT_CATEGORY_OK: consumed buffer exits 23 without payload')
    finally:
        print((root/'log').read_text(), flush=True)
        if terminal:
            terminal.terminate()
            try:
                terminal.wait(timeout=5)
            except subprocess.TimeoutExpired:
                terminal.kill(); terminal.wait()
        if keyboard:
            keyboard.terminate(); keyboard.wait(timeout=5)
        compositor.terminate()
        try:
            compositor.wait(timeout=5)
        except subprocess.TimeoutExpired:
            compositor.kill(); compositor.wait()
