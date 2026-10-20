#!/usr/bin/env python3
"""No -v: normal bundled fonts in disposable Firefox headless screenshot."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from mbox_test_tools import tool
firefox=tool('firefox','bin/firefox','')
with tempfile.TemporaryDirectory(prefix='mbox-headless-fonts-') as tmp:
    root=Path(tmp)
    for n in ('home','project','runtime'): (root/n).mkdir(mode=0o700)
    env=dict(os.environ,HOME=str(root/'home'),XDG_RUNTIME_DIR=str(root/'runtime'),
             MBOX_STATE_ROOT=str(root/'state'),MSB_HOME=str(root/'state/engine'))
    for k in ('DISPLAY','WAYLAND_DISPLAY','SWAYSOCK','MSB_PATH','MBOX_SANDBOX'):env.pop(k,None)
    start=time.monotonic()
    p=subprocess.Popen([sys.argv[1],str(root/'project'),'--memory','4G','--',
        'bash','-c','mkdir -p "$HOME/profile"; exec "$1" --headless --no-remote --profile "$HOME/profile" --screenshot "$2/screenshot.png" about:blank','probe',firefox,str(root/'project')],
        env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
    try:
        out=p.communicate(timeout=90)[0]
        print(out.decode(errors='replace')[-1000:])
        assert p.returncode==0 and (root/'project/screenshot.png').stat().st_size>1000
        print('HEADLESS_FIREFOX_FONTS_OK',round(time.monotonic()-start,3),'seconds')
    finally:
        if p.poll() is None:os.killpg(p.pid,signal.SIGKILL);p.wait()
