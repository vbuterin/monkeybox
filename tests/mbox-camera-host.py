#!/usr/bin/env python3
"""Host bridge plumbing with mocked device FD and synthetic capture executable."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

source=Path(__file__).resolve().parents[1]/'programs/mbox/camera-host.py'
with tempfile.TemporaryDirectory(prefix='mbox-camera-host-test-') as tmp:
    root=Path(tmp)
    fake=root/'capture'
    fake.write_text('#!'+sys.executable+'\nimport os,time\nwhile True:\n os.write(1,bytes([1,2,3,255])*320*240);time.sleep(.04)\n')
    fake.chmod(0o700)
    harness=root/'harness.py'
    harness.write_text('''import os,runpy,stat,sys,types
from unittest.mock import patch
original_open=os.open
original_fstat=os.fstat
fd=None
def open_device(path,flags,*args,**kwargs):
 global fd
 if path=='/dev/video-test':
  fd=original_open('/dev/null',os.O_RDWR);return fd
 return original_open(path,flags,*args,**kwargs)
def fstat_device(n):
 if n==fd:return types.SimpleNamespace(st_mode=stat.S_IFCHR,st_rdev=os.makedev(81,0))
 return original_fstat(n)
with patch.object(os,'open',open_device),patch.object(os,'fstat',fstat_device):
 runpy.run_path(sys.argv.pop(1),run_name='__main__')
''')
    for reverse in (False,True):
        path=root/'frames'
        p=subprocess.Popen([sys.executable,str(harness),str(source),str(fake),'/dev/video-test',str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                if path.is_socket():break
                assert p.poll() is None
                time.sleep(.02)
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(3);client.connect(str(path))
                data=bytearray()
                while len(data)<320*240*4:data.extend(client.recv(320*240*4-len(data)))
                assert data==bytes([1,2,3,255])*320*240
                if reverse:client.sendall(b'guest command refused')
            assert p.wait(timeout=4)==0
            assert not path.exists()
        finally:
            if p.poll() is None:p.kill();p.wait()
    bad=subprocess.run([sys.executable,str(source),str(fake),'/dev/null',str(root/'bad')],capture_output=True)
    assert bad.returncode!=0 and not (root/'bad').exists()
print('PASS: fixed synthetic frames, EOF cleanup, reverse-data revocation, non-V4L device refusal')
