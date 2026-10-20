#!/usr/bin/env python3
"""Private VM exit codes: direct msb, headless wrapper, visual wrapper."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from mbox_test_tools import tool
wrapper = str(Path(sys.argv[1]).resolve())
script = Path(wrapper).read_text()
for flag in ('-f', '--file', '-af', '--cpus', '--nix-cache'):
    for suffix in ([], ['--'], ['--no-internet']):
        result=subprocess.run([wrapper,flag,*suffix],capture_output=True,timeout=5)
        assert result.returncode==2,(flag,suffix,result.returncode)
print('BUILT_MISSING_VALUES_OK',flush=True)
msb = re.search(r'MSB="\$\{MSB_PATH:-([^}]+)', script)[1]
sway = tool('sway-unwrapped', 'bin/sway', '')
with tempfile.TemporaryDirectory(prefix='mbox-exit-') as tmp:
    root=Path(tmp)
    for n in ('home','project','runtime'): (root/n).mkdir(mode=0o700)
    env=dict(os.environ,HOME=str(root/'home'),XDG_RUNTIME_DIR=str(root/'runtime'),
        MBOX_STATE_ROOT=str(root/'state'),MSB_HOME=str(root/'state/engine'),
        WLR_BACKENDS='headless',WLR_RENDERER='pixman',WLR_LIBINPUT_NO_DEVICES='1')
    for k in ('SWAYSOCK','WAYLAND_DISPLAY','DISPLAY','MSB_PATH','MBOX_SANDBOX'):env.pop(k,None)
    (root/'config').write_text('output HEADLESS-1 mode 800x600\n')
    with (root/'sway.log').open('w') as log:
        compositor=subprocess.Popen([sway,'-c',str(root/'config')],env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                displays=[p for p in (root/'runtime').glob('wayland-*') if p.is_socket()]
                if displays:break
                time.sleep(.1)
            env['WAYLAND_DISPLAY']=displays[0].name
            env['SWAYSOCK']=str(next((root/'runtime').glob('sway-ipc*')))
            # A recorder captures the exact built headless invocation after
            # normal image initialization, then direct msb bypasses the wrapper.
            recorder=root/'record'
            recorder.write_text('#!/usr/bin/env python3\nimport os,sys,json\n'
                'if sys.argv[1]=="run":\n open('+repr(str(root/'argv'))+',"w").write(json.dumps(sys.argv[1:]))\n'
                'os.execv('+repr(msb)+', ['+repr(msb)+']+sys.argv[1:])\n')
            recorder.chmod(0o700)
            failures=[]
            for flags in ([], ['-v'], ['-vc']):
                for code in (0, 42, 127, 143):
                    p=subprocess.run([wrapper,str(root/'project'),*flags,'--','bash','-c',f'exit {code}'],
                        env=env,stdin=subprocess.DEVNULL,capture_output=True,timeout=45)
                    print('WRAPPER',flags,'expected',code,'actual',p.returncode,p.stderr.decode()[-600:],flush=True)
                    if p.returncode!=code:failures.append((flags,code,p.returncode))
                    if not flags:
                        import json
                        capture=subprocess.run([wrapper,str(root/'project'),'--','bash','-c',f'exit {code}'],env=dict(env,MSB_PATH=str(recorder)),stdin=subprocess.DEVNULL,capture_output=True,timeout=45)
                        argv=json.loads((root/'argv').read_text())
                        fw=re.search(r'export MSB_LIBKRUNFW_PATH="([^"]+)',script)[1]
                        direct=subprocess.run([msb,*argv],env=dict(env,MSB_LIBKRUNFW_PATH=fw),stdin=subprocess.DEVNULL,capture_output=True,timeout=45)
                        print('DIRECT_MSB_NO_TTY expected',code,'actual',direct.returncode,flush=True)
                        if direct.returncode!=code:failures.append(('direct',code,direct.returncode))
            assert not failures,failures
        finally:
            compositor.terminate()
            try:compositor.wait(timeout=3)
            except subprocess.TimeoutExpired:compositor.kill();compositor.wait()
