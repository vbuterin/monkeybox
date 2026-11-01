#!/usr/bin/env python3
"""Private host GTK regression, no VM, live clipboard, bus or user profiles.
Usage: mbox-host-clipboard.py SWAY WTYPE KEY WL_CLIPBOARD_BIN LIBGTK3
WTYPE supplies only a persistent fixture keyboard and the old negative control.
KEY must be built from programs/mbox/clipboard-key.c.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

repo = Path(__file__).resolve().parents[1]
sway, wtype, key, wl, gtk = map(lambda p: str(Path(p).resolve()), sys.argv[1:])
with tempfile.TemporaryDirectory(prefix='mbox-host-clipboard-') as tmp:
    root = Path(tmp)
    for name in ('runtime', 'home'):
        (root / name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(root / 'home'), XDG_RUNTIME_DIR=str(root / 'runtime'),
               WLR_BACKENDS='headless', WLR_RENDERER='pixman', WLR_LIBINPUT_NO_DEVICES='1',
               GDK_BACKEND='wayland', GTK_IM_MODULE='simple',
               DBUS_SESSION_BUS_ADDRESS=f'unix:path={root}/no-bus')
    for name in ('SWAYSOCK', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'DISPLAY',
                 'GTK_IM_MODULE_FILE', 'XAUTHORITY'):
        env.pop(name, None)
    (root / 'config').write_text('output HEADLESS-1 mode 800x600\n')
    children = []

    def run(args, **kwargs):
        return subprocess.run(args, env=env, capture_output=True, timeout=10, **kwargs)

    def wait_for(predicate):
        for _ in range(100):
            if predicate():
                return
            time.sleep(.05)
        raise AssertionError('fixture readiness timeout')

    with (root / 'log').open('w') as log:
        def launch(args):
            child = subprocess.Popen(args, env=env, stdout=log, stderr=log)
            children.append(child)
            return child

        try:
            launch([sway, '-c', str(root / 'config')])
            def sockets():
                return [p for p in (root / 'runtime').glob('wayland-*') if p.is_socket()]
            wait_for(lambda: sockets() and list((root / 'runtime').glob('sway-ipc*')))
            env.update(WAYLAND_DISPLAY=sockets()[0].name,
                       SWAYSOCK=str(next((root / 'runtime').glob('sway-ipc*'))))
            launch([wtype, '-s', '600000', ''])
            time.sleep(.2)
            launch([sys.executable, str(repo / 'tests/mbox-gtk-clipboard-app.py'), gtk, tmp])
            wait_for(lambda: (root / 'entry-ready').exists())
            time.sleep(.3)
            source = (repo / 'programs/mbox/clipboard.py').read_text()
            for name, value in {'SWAYMSG': str(Path(sway).with_name('swaymsg')),
                                'WL_PASTE': f'{wl}/wl-paste', 'WL_COPY': f'{wl}/wl-copy',
                                'KEY': key}.items():
                source = source.replace(f'@@{name}@@', value)
            broker_file = root / 'broker.py'
            broker_file.write_text(source)
            spec = importlib.util.spec_from_file_location('broker', broker_file)
            broker = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(broker)
            # Broker subprocesses inherit ONLY the private fixture environment.
            os.environ.clear()
            os.environ.update(env)
            target = broker.focused()
            assert target[2] is None
            assert broker.copy_selection(target) == b'GUEST_SELECTED_TEXT'
            # GTK really inserts the imported value, not just a clipboard echo.
            broker.endpoint(target, 'import', b'HOST_PASTE_OK')
            broker.application_key(target, 'v')
            wait_for(lambda: (root / 'entry-text').exists() and
                     (root / 'entry-text').read_text() == 'HOST_PASTE_OK')
            # MIME aliases are negotiated; no requirement for the charset suffix.
            broker.bounded([f'{wl}/wl-copy', '--type', 'text/plain'],
                           data=b'PLAIN_ALIAS', discard=True)
            assert broker.endpoint(target, 'export') == b'PLAIN_ALIAS'
            # The shared full keymap also resolves Foot's semantic keys, with
            # no Ctrl modifier (which would interrupt a terminal process).
            for action, keysym in [('copy-foot', 0x1008ff57), ('paste-foot', 0x1008ff6d)]:
                run([key, action], check=True)
                wait_for(lambda: any(json.loads(line)['state'] == 0 and
                         json.loads(line)['key'] == keysym
                         for line in (root / 'entry-keys').read_text().splitlines()))
            # Negative control: the shipped host command sends an unmodified c.
            run([wtype, '-m', 'ctrl', '-k', 'c', '-M', 'ctrl'], check=True)
            wait_for(lambda: any(json.loads(line)['state'] == 0 and json.loads(line)['key'] == 99
                     for line in (root / 'entry-keys').read_text().splitlines()))
            print('PASS: real host GTK copy/paste, text MIME alias, Foot keysyms, reversed-chord negative control')
        finally:
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
