#!/usr/bin/env python3
"""Camera cleanup under stalled capture/signals; mock device, no video or suspend."""
import argparse
import ctypes
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1] / 'programs/mbox/camera-host.py')
parser.add_argument('--case', choices=['repeat-term', 'kill', 'hup', 'stall', 'term'])
args = parser.parse_args()
# Reap our own orphan fixtures, even on the buggy implementation. This affects
# only this private test's descendants, never other user processes.
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
    raise OSError(ctypes.get_errno(), 'subreaper setup failed')

with tempfile.TemporaryDirectory(prefix='mbox-camera-lifecycle-') as tmp:
    root = Path(tmp)
    fake = root / 'capture'
    fake.write_text('#!' + sys.executable + '''
import os,signal,time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(os.environ['CAPTURE_PID']).write_text(str(os.getpid()))
# First frame proves the child has exec'd and installed its TERM handler.
os.write(1, bytes([1,2,3,255])*320*240)
while True: time.sleep(.1)
''')
    fake.chmod(0o700)
    harness = root / 'harness.py'
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
    cases = [args.case] if args.case else ['repeat-term', 'kill', 'hup', 'stall', 'term']
    for case in cases:
        path = root / f'frames-{case}'
        pidfile = root / f'pid-{case}'
        bridge = subprocess.Popen([sys.executable, str(harness), str(args.source.resolve()), str(fake),
                                   '/dev/video-test', str(path)], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE, env={**os.environ, 'CAPTURE_PID':str(pidfile)})
        client = socket.socket(socket.AF_UNIX)
        capture = pidfd = None
        try:
            for _ in range(150):
                if path.is_socket(): break
                assert bridge.poll() is None, bridge.stderr.read().decode()
                time.sleep(.02)
            client.settimeout(3)
            client.connect(str(path))
            data = bytearray()
            while len(data) < 320*240*4:
                chunk = client.recv(320*240*4-len(data))
                assert chunk, 'unexpected capture EOF'
                data.extend(chunk)
            capture = int(pidfile.read_text())
            pidfd = os.pidfd_open(capture)
            poll = select.poll()
            poll.register(pidfd, select.POLLIN)
            if case == 'repeat-term':
                # EOF enters finally and SIGTERM is ignored by fake ffmpeg.
                # A launcher TERM during that grace period must not cancel cleanup.
                client.close()
                time.sleep(.25)
                bridge.terminate()
                time.sleep(.1)
                if bridge.poll() is None:
                    bridge.terminate()
            elif case == 'kill':
                bridge.kill()  # no Python finally; child must die with parent
            elif case == 'hup':
                bridge.send_signal(signal.SIGHUP)
            elif case == 'term':
                bridge.terminate()
            # stall: no more bytes, so the existing 5-second watchdog must fire.
            bridge.wait(timeout=10)
            assert poll.poll(1500), f'{case}: capture child survived bridge exit (orphan reproduced)'
            print(f'PASS: {case}: capture child exited with bridge')
        finally:
            client.close()
            if bridge.poll() is None:
                bridge.kill()
            bridge.wait(timeout=4)
            if pidfd is not None:
                try: signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                except ProcessLookupError: pass
                os.close(pidfd)
            elif pidfile.exists():
                # Fixture is our descendant, not a numeric PID from user logs.
                capture = int(pidfile.read_text())
                try: os.kill(capture, signal.SIGKILL)
                except ProcessLookupError: pass
            if capture is not None:
                try: os.waitpid(capture, 0)
                except ChildProcessError: pass  # bridge already reaped it
            bridge.stderr.close()
