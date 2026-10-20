#!/usr/bin/env python3
"""Native Firefox + SIGINT diagnostic in private Sway/VM, no live desktop."""
import os
from pathlib import Path
import signal
import pty
import fcntl
import termios
import subprocess
import sys
import tempfile
import time
from mbox_test_tools import tool
sway=tool('sway-unwrapped','bin/sway','')
wtype=tool('wtype','bin/wtype','')
firefox=tool('firefox','bin/firefox','')
strace=tool('strace.out','bin/strace','')
grim=tool('grim','bin/grim','')
wrapper=sys.argv[1]
root=Path(tempfile.mkdtemp(prefix='mbox-firefox-visual-'))
print('EVIDENCE',root,flush=True)
for n in ('home','runtime','project'): (root/n).mkdir(mode=0o700)
env=dict(os.environ,HOME=str(root/'home'),XDG_RUNTIME_DIR=str(root/'runtime'),
    MSB_HOME=str(root/'state/engine'),MBOX_STATE_ROOT=str(root/'state'),
    WLR_BACKENDS='headless',WLR_RENDERER='pixman',WLR_LIBINPUT_NO_DEVICES='1')
for k in ('SWAYSOCK','WAYLAND_DISPLAY','DISPLAY','MSB_PATH','MBOX_SANDBOX'): env.pop(k,None)
(root/'config').write_text('output HEADLESS-1 mode 1280x800\n')
(root/'project/probe.py').write_bytes(Path(__file__).with_name('mbox-firefox-page.py').read_bytes())
command=[firefox]
if os.environ.get('MBOX_TEST_TRACE')=='1': command=[strace,'-f','-tt','-T','-o',str(root/'project/browser.trace')]+command
if os.environ.get('MBOX_TEST_FONT_MOUNT')=='1':
    # Dedicated RO mount bypasses the store-wide statfs probe; bytes unchanged.
    fonts=str(Path(firefox).parents[1]/'lib/firefox/fonts')
    text=Path(wrapper).read_text().replace('DESKTOP_ARGS=()', 'MOUNTS+=(--mount-dir "'+fonts+':'+fonts+':ro,nodev,nosuid")\nDESKTOP_ARGS=()')
    custom=root/'wrapper';custom.write_text(text);custom.chmod(0o755);wrapper=str(custom)
processes=[]
with (root/'log').open('w') as log:
    try:
        compositor=subprocess.Popen([sway,'-c',str(root/'config')],env=env,stdout=log,stderr=log);processes.append(compositor)
        for _ in range(100):
            displays=[p for p in (root/'runtime').glob('wayland-*') if p.is_socket()]
            ipcs=list((root/'runtime').glob('sway-ipc*'))
            if displays and ipcs:break
            time.sleep(.1)
        env.update(WAYLAND_DISPLAY=displays[0].name,SWAYSOCK=str(ipcs[0]))
        keyboard=subprocess.Popen([wtype,'-s','600000',''],env=env);processes.append(keyboard)
        start=time.monotonic()
        master,slave=pty.openpty()
        def terminal():
            os.setsid()
            fcntl.ioctl(slave,termios.TIOCSCTTY,0)
            signal.signal(signal.SIGINT,signal.SIG_DFL)
        proc=subprocess.Popen([wrapper,str(root/'project'),'-vc','--memory','4G','--network','blocked','--','python3','probe.py',*command],
            env=env,stdin=slave,stdout=slave,stderr=log,preexec_fn=terminal)
        os.close(slave)
        try:
            while time.monotonic()-start<int(os.environ.get('MBOX_TEST_PAGE_BUDGET','120')) and proc.poll() is None and not (root/'project/painted').exists(): time.sleep(.1)
            print('FIREFOX_PAGE', (root/'project/painted').exists(),'seconds',round(time.monotonic()-start,3),flush=True)
            if (root/'project/painted').exists():
                time.sleep(.5)
                pixels=subprocess.check_output([grim,'-t','ppm','-'],env=env,timeout=10)
                (root/'pixels.ppm').write_bytes(pixels)
                # Fixture uses a solid green body; count RGB triples, not PNG bytes.
                body=pixels.split(b'\n',3)[3]
                count=sum(body[i]<30 and body[i+1]>220 and body[i+2]<30 for i in range(0,len(body)-2,3))
                print('HOST_GREEN_PIXELS',count,flush=True)
                assert count>10000
            start=time.monotonic()
            os.write(master,b'\x03')
            try:
                proc.wait(timeout=12)
                print('SIGINT_EXIT',proc.returncode,'seconds',round(time.monotonic()-start,3),flush=True)
                assert proc.returncode==130
            except subprocess.TimeoutExpired:
                print('SIGINT_TIMEOUT',flush=True)
                raise
            assert (root/'project/painted').exists(), 'Firefox never painted fixture page'
        finally:
            # Only this fixture's process group and VM state, never live sessions.
            os.killpg(proc.pid,signal.SIGKILL) if proc.poll() is None else None
            proc.wait()
            os.close(master)
    finally:
        for p in reversed(processes):
            p.terminate()
            try:p.wait(timeout=3)
            except subprocess.TimeoutExpired:p.kill();p.wait()
