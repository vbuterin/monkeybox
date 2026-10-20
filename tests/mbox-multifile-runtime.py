#!/usr/bin/env python3
"""Real blocked VM, multi-file grants outside project/private HOME; synthetic only."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
with tempfile.TemporaryDirectory(prefix='mbox-multi-file-') as tmp:
    root=Path(tmp)
    for n in ('home','project','runtime','downloads'): (root/n).mkdir(mode=0o700)
    a=root/'downloads/first.pdf';a.write_text('FIRST_SYNTHETIC')
    b=root/'downloads/two spaced.pdf';b.write_text('SECOND_SYNTHETIC')
    hidden=root/'downloads/not-granted.txt';hidden.write_text('NOT_SHARED')
    env=dict(os.environ,HOME=str(root/'home'),XDG_RUNTIME_DIR=str(root/'runtime'),
             MBOX_STATE_ROOT=str(root/'state'),MSB_HOME=str(root/'state/engine'))
    for k in ('MSB_PATH','MBOX_SANDBOX','DISPLAY','WAYLAND_DISPLAY','SWAYSOCK'):env.pop(k,None)
    code='''from pathlib import Path
import sys
a,b,hidden=map(Path,sys.argv[1:])
assert a.read_text()=='FIRST_SYNTHETIC'
assert b.read_text()=='SECOND_SYNTHETIC'
assert not hidden.exists()
a.write_text('GUEST_WRITE_OK')
print('MULTIFILE_VM_OK')
'''
    p=subprocess.Popen([sys.argv[1],str(root/'project'),'-if',str(a),str(b),
                        '--no-internet','-m','1024M','--','python3','-c',code,str(a),str(b),str(hidden)],
                       env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
    try:
        out=p.communicate(timeout=90)[0]
        print(out.decode(errors='replace'))
        assert p.returncode==0 and b'MULTIFILE_VM_OK' in out
        assert a.read_text()=='GUEST_WRITE_OK' and hidden.read_text()=='NOT_SHARED'
    finally:
        if p.poll() is None:os.killpg(p.pid,signal.SIGKILL);p.wait()
