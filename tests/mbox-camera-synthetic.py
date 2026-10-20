#!/usr/bin/env python3
"""Guest-only synthetic camera experiment; not installed and never opens host devices.
Usage: python3 tests/mbox-camera-synthetic.py WRAPPER
"""
import os
import re
import socket
import threading
import time
from pathlib import Path
import subprocess
import sys
import tempfile
from mbox_test_tools import tool

pw = tool('pipewire.out', 'bin/pipewire', '')
wp = tool('wireplumber.out', 'bin/wireplumber', '')
bus = tool('dbus.out', 'bin/dbus-daemon', '')
firefox = tool('firefox', 'bin/firefox', '')
python = tool('python3.withPackages (p: [ p.dbus-next ])', 'bin/python3', '')
root_repo = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='mbox-camera-synthetic-') as tmp:
    root = Path(tmp)
    (root/'home').mkdir(); (root/'project').mkdir()
    (root/'project/probe.py').write_bytes((root_repo/'tests/mbox-camera-synthetic-guest.py').read_bytes())
    env = dict(os.environ, HOME=str(root/'home'), MBOX_STATE_ROOT=str(root/'state'), MSB_HOME=str(root/'state/engine'))
    for key in ('MSB_PATH','MBOX_SANDBOX','DBUS_SESSION_BUS_ADDRESS','PIPEWIRE_REMOTE','PIPEWIRE_RUNTIME_DIR'):
        env.pop(key,None)
    wrapper = sys.argv[1]
    bridge = os.environ.get('MBOX_CAMERA_BRIDGE') == '1'
    stop = threading.Event()
    listener = None
    thread = None
    if bridge:
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(root/'camera.sock')); listener.listen(1); listener.settimeout(.2)
        def feed():
            client = None
            try:
                while not stop.is_set():
                    try: client,_ = listener.accept(); break
                    except socket.timeout: pass
                if client is None: return
                client.settimeout(2)
                # Distinct horizontal bands, unlike the guest's XY gradient.
                frame = b''.join(bytes((0,0,255,255) if y<120 else (0,255,0,255))*320 for y in range(240))
                while not stop.is_set():
                    client.sendall(frame); stop.wait(.04)
            except (BrokenPipeError,ConnectionResetError,socket.timeout): pass
            finally:
                if client: client.close()
        thread = threading.Thread(target=feed); thread.start()
        text = Path(wrapper).read_text()
        text = text.replace('DESKTOP_ARGS=()', 'DESKTOP_ARGS=(--vsock "'+str(root/'camera.sock')+':5004")')
        patched = root/'wrapper'; patched.write_text(text); patched.chmod(0o755); wrapper = str(patched)
    guest_command = [python,'probe.py',pw,wp,bus,firefox]
    if os.environ.get('MBOX_CAMERA_PRODUCTION') == '1':
        assert bridge
        original = Path(sys.argv[1]).read_text()
        helper = re.search(r'"(/nix/store/[^" ]+-mbox-camera-guest)"', original)[1]
        (root/'project/browser.py').write_bytes((root_repo/'tests/mbox-camera-browser.py').read_bytes())
        guest_command = [helper,python,'browser.py',firefox]
    try:
        result = subprocess.run([wrapper,str(root/'project'),'--memory','4G','--network','blocked','-e','MBOX_CAMERA_BRIDGE='+str(int(bridge)),'-e','MBOX_CAMERA_TRACE='+os.environ.get('MBOX_CAMERA_TRACE','0'),'--',*guest_command],
                            env=env,stdin=subprocess.DEVNULL,capture_output=True,timeout=300)
    finally:
        stop.set()
        if thread: thread.join(timeout=4)
        if listener: listener.close()
    print(result.stdout.decode(errors='replace')); print(result.stderr.decode(errors='replace'))
    # Only synthetic test data, daemon logs and browser diagnostics.
    evidence = Path('/tmp/mbox-camera-synthetic-latest'); evidence.mkdir(exist_ok=True)
    for p in (root/'project').iterdir():
        if p.suffix in ('.log','.trace'):
            (evidence/p.name).write_bytes(p.read_bytes())
    if (root/'project/result.json').exists():
        (evidence/'result.json').write_bytes((root/'project/result.json').read_bytes())
    assert result.returncode == 0, result.returncode
