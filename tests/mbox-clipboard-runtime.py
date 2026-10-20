#!/usr/bin/env python3
"""Private KVM + nested compositor clipboard integration. Never uses live sockets.
Usage: python3 tests/mbox-clipboard-runtime.py WRAPPER
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from mbox_test_tools import tool

# Resolve before booting VMs, so tool builds don't eat desktop readiness budgets.
wtype = tool('wtype', 'bin/wtype', '/nix/store/z8z1h48kb1jpqs5rgw2m5xh6wlpzyfwv-wtype-0.4/bin/wtype')
weston = tool('weston', 'bin/weston-presentation-shm', '/nix/store/b78hmfrnpc8f285lxgw0kdc1gjj6bv13-weston-16.0.0/bin/weston-presentation-shm')
trust = tool('p11-kit.bin', 'bin/trust', '/nix/store/cdj6bymdyrbnc7bka8zvh30pl9y812xi-p11-kit-0.26.2-bin/bin/trust')
gtk = tool('gtk3', 'lib/libgtk-3.so.0', '/nix/store/8ycx83hg9ba53hmlgq9qcfnm5yikd70j-gtk+3-3.24.52/lib/libgtk-3.so.0')
if os.environ.get('MBOX_TEST_FIREFOX_TLS') == '1':
    firefox = tool('firefox', 'bin/firefox', '/nix/store/vicz4i0cfy1r3jkspggkk13m7z2gl2lk-firefox-154.0/bin/firefox')

wrapper = str(Path(sys.argv[1]).resolve())
script = Path(wrapper).read_text()
broker = re.search(r'(/nix/store/[^ ]+/bin/mbox-clipboard) register', script)[1]
source = Path(re.search(r'(/nix/store/[^ ]+-mbox-clipboard.py)', Path(broker).read_text())[1]).read_text()
swaymsg = re.search(r"bounded\(\['([^']+swaymsg)'", source)[1]
sway = str(Path(swaymsg).with_name('sway'))
wl_paste = re.search(r"\['([^']+wl-paste)'", source)[1]
wl_copy = str(Path(wl_paste).with_name('wl-copy'))
msb = re.search(r'MSB="\$\{MSB_PATH:-([^}]+)', script)[1]
fw = re.search(r'export MSB_LIBKRUNFW_PATH="([^"]+)', script)[1]
guest_helper = re.search(r"'(/nix/store/[^']+-mbox-clipboard-guest)'", source)[1]

def walk(n):
    yield n
    for c in n.get('nodes', []) + n.get('floating_nodes', []):
        yield from walk(c)

with tempfile.TemporaryDirectory(prefix='mbox-clipboard-test-') as tmp:
    root = Path(tmp)
    for name in ('home', 'project', 'runtime'):
        (root/name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(root/'home'), XDG_RUNTIME_DIR=str(root/'runtime'),
               MBOX_STATE_ROOT=str(root/'state'), MSB_HOME=str(root/'state/engine'),
               MSB_LIBKRUNFW_PATH=fw, WLR_BACKENDS='headless', WLR_RENDERER='pixman',
               WLR_LIBINPUT_NO_DEVICES='1', DBUS_SESSION_BUS_ADDRESS=f'unix:path={root}/no-bus')
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'SWAYSOCK', 'MSB_PATH'):
        env.pop(key, None)
    vm = 'mbox-' + hashlib.sha256(str(root/'project').encode()).hexdigest()[:12]
    def run(args, data=None, ok=True, timeout=15):
        p = subprocess.run(args, input=data, env=env, capture_output=True, timeout=timeout)
        if ok:
            assert p.returncode == 0, (args, p.returncode, p.stdout, p.stderr)
        return p
    def guest(action, data=None, ok=True):
        return run([msb, 'exec', vm, '--stream', '--no-tty', '--quiet', '--timeout', '5s',
                    '--user', f'{os.getuid()}:{os.getgid()}', '--', guest_helper, action], data, ok)
    (root/'sway.conf').write_text('output HEADLESS-1 mode 1280x800\n'
                                f'bindsym Ctrl+Shift+c exec {broker} export\n'
                                f'bindsym Ctrl+Shift+v exec {broker} import\n')
    compositor_log = (root/'sway.log').open('w')
    compositor = subprocess.Popen([sway, '-c', str(root/'sway.conf')], env=env,
                                 stdout=compositor_log, stderr=compositor_log)
    proc = None
    host_app = None
    second = None
    second_vm = None
    keyboard = None
    entry_app = None
    try:
        for _ in range(100):
            sockets = [p for p in (root/'runtime').glob('wayland-*') if p.is_socket()]
            ipcs = list((root/'runtime').glob('sway-ipc*'))
            if sockets and ipcs:
                break
            time.sleep(.1)
        env.update(WAYLAND_DISPLAY=sockets[0].name, SWAYSOCK=str(ipcs[0]))
        keyboard = subprocess.Popen([wtype, '-s', '600000', ''], env=env)
        time.sleep(.5)
        subprocess.run([wl_copy, '--type', 'text/plain;charset=utf-8'], input=b'HOST_SECRET_NOT_FOR_GUEST',
                       env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=10)
        log = (root/'guest.log').open('w')
        proc = subprocess.Popen([wrapper, str(root/'project'), '-v', '--clipboard',
                                 '--network', 'open' if os.environ.get('MBOX_TEST_FIREFOX_TLS') == '1' else 'blocked', '--',
                                 'bash', '-c', 'echo READY > ready; sleep 900'], env=env,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        deadline = time.monotonic()+180
        while time.monotonic()<deadline:
            if (root/'project/ready').exists():
                break
            assert proc.poll() is None, (root/'guest.log').read_text()
            time.sleep(.2)
        if not (root/'project/ready').exists():
            print(run([msb, 'exec', vm, '--no-tty', '--quiet', '--', 'sh', '-c',
                       'ps -ef; ls -la /tmp/mbox*; cat /tmp/mbox-clipboard-runtime/compositor.log'], ok=False).stdout.decode(), flush=True)
        assert (root/'project/ready').exists(), 'guest readiness timeout'
        assert guest('export', ok=False).stdout != b'HOST_SECRET_NOT_FOR_GUEST'
        guest('import', b'GUEST_LOCAL_TEXT')
        assert guest('export').stdout == b'GUEST_LOCAL_TEXT'
        assert run([wl_paste, '-n']).stdout == b'HOST_SECRET_NOT_FOR_GUEST'
        tree = json.loads(run([swaymsg, '-r', '-t', 'get_tree']).stdout)
        print('HOST_WINDOWS', [(n.get('id'),n.get('pid'),n.get('app_id')) for n in walk(tree) if n.get('pid')], flush=True)
        # Inject only into the PRIVATE host compositor to exercise host binds.
        run([wtype, '-M', 'ctrl', '-M', 'shift', '-k', 'c', '-m', 'shift', '-m', 'ctrl'])
        buffer = root/'runtime/mbox-clipboard/buffer.json'
        for _ in range(100):
            if buffer.exists():
                break
            time.sleep(.1)
        assert json.loads(buffer.read_text())['text'] == 'GUEST_LOCAL_TEXT'
        guest('import', b'REPLACED')
        run([broker, 'import'])
        assert guest('export').stdout == b'GUEST_LOCAL_TEXT'
        assert not buffer.exists()
        assert run([wl_paste, '-n']).stdout == b'HOST_SECRET_NOT_FOR_GUEST'
        # Host<->guest explicit transfers. A private host window supplies focus;
        # no actual user's compositor or clipboard participates.
        nested = next(n for n in walk(tree) if n.get('pid'))['id']
        host_app = subprocess.Popen([weston],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            tree = json.loads(run([swaymsg, '-r', '-t', 'get_tree']).stdout)
            host_nodes = [n for n in walk(tree) if n.get('pid') == host_app.pid]
            if host_nodes:
                break
            time.sleep(.1)
        assert host_nodes, 'host test window missing'
        host_id = host_nodes[0]['id']
        run([swaymsg, f'[con_id={host_id}] focus'])
        run([broker, 'export'])
        run([swaymsg, f'[con_id={nested}] focus'])
        assert guest('export').stdout == b'GUEST_LOCAL_TEXT'  # no passive sync
        run([broker, 'import'])
        assert guest('export').stdout == b'HOST_SECRET_NOT_FOR_GUEST'
        guest('import', b'GUEST_EXPLICIT_OUT')
        run([broker, 'export'])
        run([swaymsg, f'[con_id={host_id}] focus'])
        run([broker, 'import'])
        assert run([wl_paste, '-n']).stdout == b'GUEST_EXPLICIT_OUT'
        assert not buffer.exists()
        if os.environ.get('MBOX_TEST_COMBINED_KEYS') == '1':
            import shutil
            shutil.copyfile(Path(__file__).with_name('mbox-gtk-clipboard-app.py'), root/'project/entry.py')
            entry_log = (root/'entry.log').open('w')
            entry_app = subprocess.Popen([msb, 'exec', vm, '--no-tty', '--quiet', '--timeout', '300s',
                '-e', 'XDG_RUNTIME_DIR=/tmp/mbox-clipboard-runtime',
                '-e', 'WAYLAND_DISPLAY=mbox-clipboard', '-e', 'GDK_BACKEND=wayland', '--',
                'python3', str(root/'project/entry.py'), gtk, str(root/'project')],
                env=env, stdout=entry_log, stderr=entry_log)
            deadline = time.monotonic()+150
            while not (root/'project/entry-ready').exists() and time.monotonic()<deadline:
                assert entry_app.poll() is None, (root/'entry.log').read_text()
                time.sleep(.1)
            assert (root/'project/entry-ready').exists(), 'entry readiness timeout'
            run([swaymsg, f'[con_id={nested}] focus'])
            time.sleep(.5)
            # Install production-shaped combined bindings only in our private host.
            for key, action in [('c', 'copy'), ('v', 'paste')]:
                run([swaymsg, f'bindsym Ctrl+Shift+{key} nop'])
                run([swaymsg, f'bindsym --release Ctrl+Shift+{key} exec {broker} {action}'])
            assert not buffer.exists()
            run([wtype, '-M', 'ctrl', '-M', 'shift', '-k', 'c', '-s', '300', '-m', 'shift', '-m', 'ctrl'])
            deadline = time.monotonic()+15
            while time.monotonic()<deadline and not buffer.exists():
                time.sleep(.1)
            print('GUEST_APP_KEYS', (root/'project/entry-keys').read_text() if (root/'project/entry-keys').exists() else 'none', flush=True)
            assert buffer.exists(), 'combined guest Copy failed'
            assert json.loads(buffer.read_text())['text'] == 'GUEST_SELECTED_TEXT'
            # Deliver the guest selection to host clipboard and check exact bytes.
            run([swaymsg, f'[con_id={host_id}] focus'])
            run([broker, 'import'])
            assert run([wl_paste, '-n']).stdout == b'GUEST_SELECTED_TEXT'
            # Distinct imported value proves application insertion, not merely
            # unchanged selected text or a successful wl-copy command.
            run([swaymsg, f'[con_id={host_id}] focus'])
            subprocess.run([wl_copy, '--type', 'text/plain;charset=utf-8'], input=b'HOST_INTO_GUEST_ENTRY',
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=10)
            run([broker, 'export'])
            run([swaymsg, f'[con_id={nested}] focus'])
            run([wtype, '-M', 'ctrl', '-M', 'shift', '-k', 'v', '-s', '300', '-m', 'shift', '-m', 'ctrl'])
            deadline = time.monotonic()+15
            while time.monotonic()<deadline:
                if (root/'project/entry-text').exists() and (root/'project/entry-text').read_text() == 'HOST_INTO_GUEST_ENTRY':
                    break
                time.sleep(.1)
            assert (root/'project/entry-text').read_text() == 'HOST_INTO_GUEST_ENTRY'
            print('GUEST_GTK_COMBINED_COPY_PASTE_OK', flush=True)
            sys.exit(0)
        # A second independent VM: explicit VM->VM transfer, no broadcast.
        second_project = root/'second-project'; second_project.mkdir()
        second_vm = 'mbox-' + hashlib.sha256(str(second_project).encode()).hexdigest()[:12]
        second_log = (root/'second.log').open('w')
        second = subprocess.Popen([wrapper, str(second_project), '-v', '--clipboard', '--',
                                   'bash', '-c', 'echo READY > ready; sleep 900'], env=env,
                                  stdin=subprocess.DEVNULL, stdout=second_log, stderr=second_log)
        deadline = time.monotonic()+180
        while time.monotonic()<deadline and not (second_project/'ready').exists():
            assert second.poll() is None, (root/'second.log').read_text()
            time.sleep(.2)
        assert (second_project/'ready').exists(), 'second VM readiness timeout'
        tree = json.loads(run([swaymsg, '-r', '-t', 'get_tree']).stdout)
        other_id = next(n['id'] for n in walk(tree) if n.get('pid') and n['id'] not in (nested, host_id))
        first_vm = vm; vm = second_vm
        assert guest('export', ok=False).stdout != b'GUEST_EXPLICIT_OUT'
        vm = first_vm
        guest('import', b'VM_TO_VM')
        run([swaymsg, f'[con_id={nested}] focus']); run([broker, 'export'])
        run([swaymsg, f'[con_id={other_id}] focus']); run([broker, 'import'])
        vm = second_vm
        assert guest('export').stdout == b'VM_TO_VM'
        vm = first_vm
        assert run([wl_paste, '-n']).stdout == b'GUEST_EXPLICIT_OUT'
        print('HOST_GUEST_AND_VM_VM_EXPLICIT_TRANSFERS_OK', flush=True)
        # Trust store through the same guest boot, no HTTPS or private accounts.
        certs = run([msb, 'exec', vm, '--no-tty', '--quiet', '--', trust, 'list', '--filter=ca-anchors'], timeout=45)
        print('TRUST_RESULT', certs.returncode, len(certs.stdout), certs.stderr, flush=True)
        assert b'trust: anchor' in certs.stdout, certs.stdout
        # GTK3 must now create a seat using the guest-local data-device manager.
        probe = ('import ctypes; g=ctypes.CDLL('+repr(gtk)+'); '
                 'g.gtk_init_check.restype=ctypes.c_int; assert g.gtk_init_check(None,None); '
                 'g.gdk_display_get_default.restype=ctypes.c_void_p; '
                 'g.gdk_display_get_default_seat.argtypes=[ctypes.c_void_p]; '
                 'g.gdk_display_get_default_seat.restype=ctypes.c_void_p; '
                 'assert g.gdk_display_get_default_seat(g.gdk_display_get_default()); '
                 'print("NESTED_GTK_SEAT_OK")')
        result = run([msb, 'exec', vm, '--no-tty', '--quiet', '--timeout', '150s',
                      '-e', 'XDG_RUNTIME_DIR=/tmp/mbox-clipboard-runtime',
                      '-e', 'WAYLAND_DISPLAY=mbox-clipboard', '-e', 'GDK_BACKEND=wayland',
                      '--', 'python3', '-c', probe], timeout=165)
        assert b'NESTED_GTK_SEAT_OK' in result.stdout, result.stdout
        print(result.stdout.decode().strip(), flush=True)
        # Firefox/NSS must validate a real public HTTPS connection, not just list
        # roots. Opt-in: this accesses example.com with a disposable guest profile.
        if os.environ.get('MBOX_TEST_FIREFOX_TLS') == '1':
            run([msb, 'exec', vm, '--no-tty', '--quiet', '--', 'mkdir', '-p', '/tmp/mbox-test-firefox'])
            run([msb, 'exec', vm, '--no-tty', '--quiet', '--timeout', '300s', '--',
                 firefox, '--headless', '--no-remote', '--profile', '/tmp/mbox-test-firefox',
                 '--screenshot', str(root/'project/tls.png'), 'https://example.com'], timeout=320)
            assert (root/'project/tls.png').exists()
            import shutil
            shutil.copyfile(root/'project/tls.png', '/tmp/mbox-firefox-tls.png')
            print('FIREFOX_TLS_SCREENSHOT_CREATED: inspect screenshot content separately', flush=True)
        print('PRIVATE_CLIPBOARD_AND_GUEST_TRUST_OK', flush=True)
    finally:
        if second:
            run([wrapper, 'stop', second_vm], ok=False)
            try:
                second.wait(timeout=15)
            except subprocess.TimeoutExpired:
                second.kill(); second.wait()
        if proc:
            run([wrapper, 'stop', vm], ok=False)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()
            print((root/'guest.log').read_text(), flush=True)
        if entry_app:
            entry_app.terminate(); entry_app.wait(timeout=5)
        if keyboard:
            keyboard.terminate(); keyboard.wait(timeout=5)
        if host_app:
            host_app.terminate(); host_app.wait(timeout=5)
        compositor.terminate()
        try:
            compositor.wait(timeout=5)
        except subprocess.TimeoutExpired:
            compositor.kill(); compositor.wait()
        print((root/'sway.log').read_text(), flush=True)
