#!/usr/bin/env python3
"""Private Firefox/GTK focus-caret protocol regression; no host input/profile.
Usage: mbox-firefox-caret.py SWAY FIREFOX WTYPE TEST_IM IM_CACHE
TEST_IM is compiled from mbox-test-im.c; see tests/README.md.
Use '-' for IM_CACHE to exercise stock GTK (expected to fail on GTK 3.24.52).
Only protocol geometry is inspected; this does not validate a rendered popup.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
import time

assert len(sys.argv) == 6, __doc__
sway, firefox, wtype, test_im, cache = sys.argv[1:]
with tempfile.TemporaryDirectory(prefix='mbox-firefox-caret-') as directory:
    p = Path(directory)
    env = dict(os.environ, HOME=directory, XDG_RUNTIME_DIR=directory,
               WLR_BACKENDS='headless', WLR_RENDERER='pixman',
               WLR_LIBINPUT_NO_DEVICES='1', MOZ_ENABLE_WAYLAND='1',
               GDK_BACKEND='wayland', GTK_IM_MODULE='wayland',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + directory + '/no-bus',
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + directory + '/no-system-bus')
    for key in ('WAYLAND_DISPLAY', 'DISPLAY', 'SWAYSOCK', 'GTK_IM_MODULE_FILE',
                'XAUTHORITY', 'MOZ_HEADLESS'):
        env.pop(key, None)
    children = []
    logs = []

    def start(args, extra=None):
        log = tempfile.TemporaryFile()
        logs.append(log)
        child = subprocess.Popen(args, env=dict(env, **(extra or {})), stdout=log, stderr=log)
        children.append(child)
        return child

    try:
        (p/'config').write_text('output HEADLESS-1 mode 1000x800\nxwayland disable\nseat seat0 fallback true\n')
        compositor = start([sway, '-c', str(p/'config')])
        for _ in range(100):
            sockets = [s for s in p.glob('wayland-*') if s.is_socket()]
            if sockets or compositor.poll() is not None:
                break
            time.sleep(.05)
        assert sockets and compositor.poll() is None, 'private Sway did not start'
        env['WAYLAND_DISPLAY'] = sockets[0].name
        # This virtual keyboard exists ONLY on our disposable compositor.
        start([wtype, '-s', '18000'])
        time.sleep(.2)
        start([test_im], {'ACK': '1'})
        (p/'index.html').write_text('''<!doctype html>
<textarea id="a" style="position:absolute;left:100px;top:100px" autofocus></textarea>
<textarea id="b" style="position:absolute;left:400px;top:400px"></textarea>
<script>let i=0;setInterval(()=>document.getElementById(++i%2?'b':'a').focus(),1000);</script>
''')
        (p/'profile').mkdir()
        (p/'profile/user.js').write_text('''user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("datareporting.policy.dataSubmissionEnabled", false);
''')
        if cache != '-':
            env['GTK_IM_MODULE'] = 'mbox'
            env['GTK_IM_MODULE_FILE'] = cache
        browser = start([firefox, '--no-remote', '--profile', str(p/'profile'),
                         (p/'index.html').as_uri()], {'WAYLAND_DEBUG': 'client'})
        time.sleep(12)
        assert browser.poll() is None, 'Firefox exited prematurely'
        logs[-1].seek(0)
        text = logs[-1].read().decode(errors='replace')
        # Each focus-in initially sends the cached previous rectangle. GTK must
        # then send the newly supplied rectangle in the SAME focus epoch.
        epochs = re.split(r'zwp_text_input_v3#\d+\.enable\(\)', text)[1:]
        checked = 0
        for epoch in epochs:
            epoch = re.split(r'zwp_text_input_v3#\d+\.disable\(\)', epoch)[0]
            rectangles = re.findall(r'set_cursor_rectangle\((-?\d+), (-?\d+), (\d+), (\d+)\)', epoch)
            rectangles = [tuple(map(int, r)) for r in rectangles]
            if not rectangles or rectangles[0][3] == 0:
                continue  # initial focus has no valid cached rectangle yet
            assert len(rectangles) >= 2, f'Only old caret published in focus epoch: {rectangles}'
            old, new = rectangles[0], rectangles[-1]
            assert abs(new[0]-old[0]) == 300 and abs(new[1]-old[1]) == 300, rectangles
            checked += 1
        assert checked >= 4, f'Not enough focus transitions: {checked}'
        print(f'PASS: Firefox publishes new caret within each focus epoch ({checked} transitions)')
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        for log in logs:
            log.close()
