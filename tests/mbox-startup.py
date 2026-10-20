#!/usr/bin/env python3
"""Private Sway/VM startup timing: app command reached, not first painted frame.
Usage: python3 tests/mbox-startup.py WRAPPER
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from mbox_test_tools import tool
sway = tool('sway-unwrapped', 'bin/sway', '/nix/store/jh2y4bkbdd6a0dkpw6ryz0j8cgy6hg7k-sway-unwrapped-1.12/bin/sway')
wtype = tool('wtype', 'bin/wtype', '/nix/store/cwzg3yii4xv6hk1yn3jjgw14cxzy98qa-wtype-0.4/bin/wtype')
wrapper = sys.argv[1]
with tempfile.TemporaryDirectory(prefix='mbox-startup-') as tmp:
    root = Path(tmp)
    for name in ('home','project','runtime'):
        (root/name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(root/'home'), XDG_RUNTIME_DIR=str(root/'runtime'),
               MBOX_STATE_ROOT=str(root/'state'), MSB_HOME=str(root/'state/engine'),
               WLR_BACKENDS='headless', WLR_RENDERER='pixman', WLR_LIBINPUT_NO_DEVICES='1')
    for k in ('SWAYSOCK','WAYLAND_DISPLAY','DISPLAY','MSB_PATH','MBOX_SANDBOX'):
        env.pop(k,None)
    (root/'config').write_text('output HEADLESS-1 mode 1280x800\n')
    with (root/'sway.log').open('w') as log:
        compositor = subprocess.Popen([sway,'-c',str(root/'config')], env=env, stdout=log, stderr=log)
        keyboard = None
        try:
            for _ in range(100):
                sockets=[p for p in (root/'runtime').glob('wayland-*') if p.is_socket()]
                ipcs=list((root/'runtime').glob('sway-ipc*'))
                if sockets and ipcs:break
                time.sleep(.1)
            assert sockets and ipcs, 'private compositor readiness timeout'
            env.update(WAYLAND_DISPLAY=sockets[0].name,SWAYSOCK=str(ipcs[0]))
            keyboard = subprocess.Popen([wtype, '-s', '600000', ''], env=env)
            time.sleep(.2)
            for flags in ([],['-v'],['-vc'],['-vc']):
                start=time.monotonic()
                # Also inspect firmware camera prerequisites without host device access.
                command='echo APP_READY; date +%s.%N; test ! -f /proc/config.gz || zcat /proc/config.gz | grep -E "CONFIG_(VIDEO_V4L2|VIDEO_DEV|MEDIA_SUPPORT|MODULES|USB_SUPPORT)="; true'
                p=subprocess.run([wrapper,str(root/'project'),*flags,'--','bash','-c',command],
                                 env=env,stdin=subprocess.DEVNULL,capture_output=True,timeout=180)
                print('STARTUP',flags,'seconds',round(time.monotonic()-start,3),'exit',p.returncode,
                      p.stdout.decode(errors='replace'),p.stderr.decode(errors='replace')[-500:],flush=True)
                assert p.returncode==0
        finally:
            if keyboard:
                keyboard.terminate(); keyboard.wait(timeout=5)
            compositor.terminate()
            try:compositor.wait(timeout=5)
            except subprocess.TimeoutExpired:compositor.kill();compositor.wait()
