#!/usr/bin/env python3
"""Guest IM defaults; optional private compositor/GTK runtime regression.
Usage: mbox-desktop-im.py [SWAY XWAYLAND_SATELLITE LIBGTK3 [IM_CACHE]]
No Signal profile, host display, host input or host bus is used.
"""
import os
import resource
from pathlib import Path
import subprocess
import sys
import tempfile
import time

root = Path(__file__).resolve().parents[1]
source = (root / 'programs/mbox/desktop-guest.sh.in').read_text()
setup = source.split('    export LIBGL_ALWAYS_SOFTWARE=1', 1)[1].split('    # X11 sockets', 1)[0]
probe = setup + '\nprintf "%s:%s:%s" "${GTK_IM_MODULE-unset}" "${QT_IM_MODULE-unset}" "${XMODIFIERS-unset}"'
result = subprocess.check_output(['bash', '-eu', '-c', probe],
    env=dict(os.environ, GTK_IM_MODULE='wayland', QT_IM_MODULE='fcitx', XMODIFIERS='@im=fcitx'))
assert result == b'unset:unset:unset', result
print('PASS: guest does not force backend-specific IM modules')
if len(sys.argv) == 1:
    sys.exit(0)
sway, satellite, gtk = sys.argv[1:4]
im_cache = sys.argv[4] if len(sys.argv) > 4 else None
# The negative control deliberately aborts; never write a core dump.
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
# Use ctypes so this regression needs neither PyGObject nor a compiler.
gtk_probe = r'''
import ctypes as c, sys
g = c.CDLL(sys.argv[1])
g.gtk_init_check.argtypes = [c.c_void_p, c.c_void_p]
assert g.gtk_init_check(None, None)
g.gtk_window_new.restype = c.c_void_p
g.gtk_widget_realize.argtypes = [c.c_void_p]
g.gtk_widget_get_window.argtypes = [c.c_void_p]
g.gtk_widget_get_window.restype = c.c_void_p
g.gtk_im_multicontext_new.restype = c.c_void_p
g.gtk_im_context_set_client_window.argtypes = [c.c_void_p, c.c_void_p]
g.gtk_im_context_focus_in.argtypes = [c.c_void_p]
w = g.gtk_window_new(0)
g.gtk_widget_realize(w)
im = g.gtk_im_multicontext_new()
g.gtk_im_context_set_client_window(im, g.gtk_widget_get_window(w))
g.gtk_im_context_focus_in(im)
print('GTK_IM_OK')
'''
with tempfile.TemporaryDirectory(prefix='mbox-im-test-') as directory:
    path = Path(directory)
    env = dict(os.environ, HOME=directory, XDG_RUNTIME_DIR=directory,
               WLR_BACKENDS='headless', WLR_RENDERER='pixman', WLR_LIBINPUT_NO_DEVICES='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + directory + '/no-bus',
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + directory + '/no-system-bus')
    for key in ('WAYLAND_DISPLAY', 'DISPLAY', 'SWAYSOCK', 'XAUTHORITY', 'GTK_IM_MODULE',
                'QT_IM_MODULE', 'XMODIFIERS', 'GDK_BACKEND'):
        env.pop(key, None)
    conf = path / 'sway.conf'
    conf.write_text('output HEADLESS-1 mode 640x480\nxwayland disable\n')
    children = []
    def start(args):
        log = tempfile.TemporaryFile()
        child = subprocess.Popen(args, env=env, stdout=log, stderr=log)
        children.append((child, log))
        return child
    def wait_for(predicate, child):
        for _ in range(200):
            if predicate():
                return
            if child.poll() is not None:
                break
            time.sleep(.05)
        for _, log in children:
            log.seek(0)
            print(log.read().decode(), file=sys.stderr)
        raise AssertionError('private display failed to become ready')
    try:
        compositor = start([sway, '-c', str(conf)])
        wait_for(lambda: any(p.is_socket() for p in path.glob('wayland-*')), compositor)
        env['WAYLAND_DISPLAY'] = next(p.name for p in path.glob('wayland-*') if p.is_socket())
        # Never reuse an existing X server. Pick an unused display for our child.
        display = next(n for n in range(170, 220) if not Path(f'/tmp/.X11-unix/X{n}').exists()
                       and not Path(f'/tmp/.X{n}-lock').exists())
        xserver = start([satellite, f':{display}', '-glamor', 'none', '-nolisten', 'tcp'])
        wait_for(lambda: Path(f'/tmp/.X11-unix/X{display}').is_socket(), xserver)
        env['DISPLAY'] = f':{display}'
        for backend in ('x11', 'wayland'):
            selected = dict(env, GDK_BACKEND=backend, G_DEBUG='fatal-criticals')
            result = subprocess.run(['bash', '-eu', '-c', setup + '\nexec "$@"', 'probe',
                                     sys.executable, '-c', gtk_probe, gtk], env=selected,
                                    capture_output=True, text=True, timeout=10)
            assert result.returncode == 0, (backend, result.stdout, result.stderr)
            assert 'GTK_IM_OK' in result.stdout
            # Retain the legacy XIM fallback regression on both backends.
            result = subprocess.run([sys.executable, '-c', gtk_probe, gtk],
                env=dict(selected, GTK_IM_MODULE='xim'), capture_output=True, text=True, timeout=10)
            assert result.returncode == 0, (backend, result.stdout, result.stderr)
            if im_cache:
                result = subprocess.run([sys.executable, '-c', gtk_probe, gtk],
                    env=dict(selected, GTK_IM_MODULE='mbox', GTK_IM_MODULE_FILE=im_cache),
                    capture_output=True, text=True, timeout=10)
                assert result.returncode == 0, (backend, result.stdout, result.stderr)
                assert 'Unable to load' not in result.stderr and 'Failed to load' not in result.stderr, result.stderr
        # Negative control reproduces the previous forced-Wayland/X11 assertion.
        result = subprocess.run([sys.executable, '-c', gtk_probe, gtk],
            env=dict(env, GDK_BACKEND='x11', GTK_IM_MODULE='wayland', G_DEBUG='fatal-criticals'),
            capture_output=True, text=True, timeout=10)
        assert result.returncode != 0 and 'GDK_IS_WAYLAND_DISPLAY' in result.stderr, result
        print('PASS: private GTK IM initialization on X11 and Wayland; old forced module fails on X11')
    finally:
        for child, log in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            log.close()
