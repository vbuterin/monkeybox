#!/usr/bin/env python3
"""Real VM noisy-image mapping and pixel test on a PRIVATE compositor only.
Usage: python3 tests/mbox-xwayland-presentation.py WRAPPER SWAY GRIM
Requires KVM, Python/Pillow on host and feh in fixture. MSB_PATH selects the VMM.
No live desktop sockets, input injection or capture grants to the guest.
"""
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time
from PIL import Image

wrapper, sway, grim = map(os.path.abspath, sys.argv[1:])
swaymsg = str(Path(sway).with_name('swaymsg'))

def walk(node):
    yield node
    for child in node.get('nodes', []) + node.get('floating_nodes', []):
        yield from walk(child)

with tempfile.TemporaryDirectory(prefix='mbox-pixels-') as tmp:
    root = Path(tmp)
    for name in ('home', 'project', 'state', 'runtime'):
        (root/name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(root/'home'), XDG_RUNTIME_DIR=str(root/'runtime'),
               MBOX_STATE_ROOT=str(root/'state'), MSB_HOME=str(root/'state/engine'),
               WLR_BACKENDS='headless', WLR_RENDERER='pixman', WLR_LIBINPUT_NO_DEVICES='1',
               DBUS_SESSION_BUS_ADDRESS=f'unix:path={root}/no-bus',
               DBUS_SYSTEM_BUS_ADDRESS=f'unix:path={root}/no-system-bus')
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'SWAYSOCK', 'PULSE_SERVER', 'PULSE_CLIENTCONFIG'):
        env.pop(key, None)
    vm = 'mbox-' + hashlib.sha256(str(root/'project').encode()).hexdigest()[:12]
    image = Image.frombytes('RGB', (697, 1008), random.Random(735).randbytes(697*1008*3))
    # Large flat markers amid >2 MiB of noisy pixels make delivery verifiable.
    image.paste((240, 16, 208), (260, 420, 340, 580))
    image.paste((16, 224, 240), (360, 420, 440, 580))
    image.save(root/'project/fixture.png')
    (root/'sway.conf').write_text('output HEADLESS-1 mode 1920x1200\n'
                                'for_window [app_id=feh] floating enable\n')
    with open(root/'sway.log', 'w') as compositor_log:
        compositor = subprocess.Popen([sway, '-c', str(root/'sway.conf')], env=env,
                                      stdout=compositor_log, stderr=compositor_log)
        guest = None
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                sockets = [p for p in (root/'runtime').glob('wayland-*') if p.is_socket()]
                ipcs = list((root/'runtime').glob('sway-ipc*'))
                if sockets and ipcs:
                    break
                assert compositor.poll() is None, 'private compositor exited'
                time.sleep(.1)
            assert sockets and ipcs, 'private compositor readiness timeout'
            env['WAYLAND_DISPLAY'] = sockets[0].name
            ipc = str(ipcs[0])
            for scale in ('1', '1.5'):
                subprocess.run([swaymsg, '-s', ipc, f'output HEADLESS-1 scale {scale}'],
                               env=env, check=True, capture_output=True, timeout=5)
                with open(root/'guest.log', 'w') as log:
                    guest = subprocess.Popen([wrapper, str(root/'project'), '-v', '--',
                                              'feh', '--auto-zoom', '--title', 'MBOX_PIXEL_FIXTURE',
                                              'fixture.png'], env=env, stdin=subprocess.DEVNULL,
                                             stdout=log, stderr=log)
                    passed = False
                    last_counts = None
                    deadline = time.monotonic() + 25
                    while time.monotonic() < deadline and guest.poll() is None:
                        tree = json.loads(subprocess.check_output(
                            [swaymsg, '-s', ipc, '-t', 'get_tree', '-r'], env=env, timeout=5))
                        windows = [n for n in walk(tree) if n.get('app_id') == 'feh'
                                   and n.get('visible')]
                        if windows:
                            rect = windows[0]['rect']
                            # Host-only capture on our newly created compositor.
                            subprocess.run([grim, '-s', '1', str(root/'shot.png')],
                                           env=env, check=True, capture_output=True, timeout=5)
                            with Image.open(root/'shot.png') as shot:
                                crop = shot.convert('RGB').crop((rect['x'], rect['y'],
                                    rect['x']+rect['width'], rect['y']+rect['height']))
                                pixels = list(crop.getdata())
                            counts = [sum(all(abs(a-b) <= 3 for a,b in zip(pixel, marker))
                                          for pixel in pixels)
                                      for marker in ((240,16,208), (16,224,240))]
                            last_counts = counts
                            if min(counts) > 500:
                                print(f'HOST_PIXELS_OK scale={scale} rect={rect} markers={counts}',
                                      flush=True)
                                passed = True
                                break
                        time.sleep(.15)
                    assert passed, f'no mapped/delivered noisy image at scale {scale}; markers={last_counts}'
                    subprocess.run([wrapper, 'stop', vm], env=env, check=True, timeout=15)
                    guest.wait(timeout=15)
                    guest = None
        finally:
            if guest is not None:
                try:
                    subprocess.run([wrapper, 'stop', vm], env=env, timeout=15)
                    guest.wait(timeout=15)
                except (subprocess.TimeoutExpired, OSError) as exc:
                    print(f'Fixture cleanup incomplete: {exc}', flush=True)
                finally:
                    print((root/'guest.log').read_text(errors='replace'), flush=True)
            compositor.terminate()
            try:
                compositor.wait(timeout=5)
            except subprocess.TimeoutExpired:
                compositor.kill()
                compositor.wait()
            for path in (root/'state/engine/sandboxes').glob('*/logs/runtime.log'):
                for line in path.read_text(errors='replace').splitlines():
                    if 'error reading TX packet' in line:
                        print(line, flush=True)
print('XWAYLAND_HOST_MAPPING_AND_PIXELS_OK')
