"""Invocation-scoped V4L2 -> fixed BGRA stream. No guest commands are accepted."""
import ctypes
import os
from pathlib import Path
import selectors
import signal
import socket
import stat
import subprocess
import sys
import time

FFMPEG, DEVICE, SOCKET = sys.argv[1:]
# Open exactly one host-selected capture device. Hold the FD so a path rename
# cannot silently change devices between validation and ffmpeg opening it.
fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
st = os.fstat(fd)
if not stat.S_ISCHR(st.st_mode) or os.major(st.st_rdev) != 81:
    os.close(fd)
    raise SystemExit('mbox: camera must be a Video4Linux character device')
listener = socket.socket(socket.AF_UNIX)
listener.bind(SOCKET); os.chmod(SOCKET, 0o600); listener.listen(1)
child = None
client = None

def stop(signum, frame):
    raise SystemExit(128 + signum)

STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
for sig in STOP_SIGNALS:
    signal.signal(sig, stop)

# Linux-only, single-threaded helper. Arm before exec; SIGKILL is deliberate:
# FFmpeg may ignore graceful termination after a camera/driver stall. It must not
# keep the camera when its supervising bridge is gone (including SIGKILL/OOM).
libc = ctypes.CDLL(None, use_errno=True)
parent_pid = os.getpid()

def capture_setup(old_mask):
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        os._exit(125)
    # Parent could have died between fork and prctl; don't miss that window.
    if os.getppid() != parent_pid:
        os._exit(125)
    signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
try:
    client, _ = listener.accept()
    listener.close()
    # Lower-resolution initial implementation: 7.68 MB/s at 25 fps, no compressed
    # guest parser and no dimension/format negotiation from the guest.
    # Don't let a termination handler interrupt Popen before its return value is
    # assigned: cleanup must always know the child. Restore the mask in the
    # child before exec as well as in the supervisor after assignment.
    old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, STOP_SIGNALS)
    try:
        child = subprocess.Popen([FFMPEG,'-nostdin','-hide_banner','-loglevel','error',
            '-f','v4l2','-i',f'/proc/self/fd/{fd}', '-an','-vf','scale=320:240,fps=25',
            '-pix_fmt','bgra','-f','rawvideo','pipe:1'], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, pass_fds=(fd,),
            preexec_fn=lambda: capture_setup(old_mask))
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
    os.close(fd); fd = -1
    client.setblocking(False)
    os.set_blocking(child.stdout.fileno(),False)
    # At most two frames buffered. A stalled guest ends capture after 5 seconds;
    # it cannot request additional buffering, device controls or other streams.
    pending = bytearray()
    last_progress = time.monotonic()
    with selectors.DefaultSelector() as selector:
        selector.register(client, selectors.EVENT_READ)
        selector.register(child.stdout, selectors.EVENT_READ)
        while True:
            selector.modify(client,selectors.EVENT_READ | (selectors.EVENT_WRITE if pending else 0))
            for key, events in selector.select(.2):
                if key.fileobj is client:
                    if events & selectors.EVENT_READ:
                        # Any reverse data is a protocol violation; EOF revokes access.
                        client.recv(1)
                        raise SystemExit(0)
                    if events & selectors.EVENT_WRITE:
                        try: n = client.send(pending)
                        except BlockingIOError: continue
                        del pending[:n]; last_progress = time.monotonic()
                else:
                    data = child.stdout.read(65536)
                    if not data: raise SystemExit('mbox: camera capture ended')
                    pending.extend(data)
                    if len(pending)>2*320*240*4:
                        raise SystemExit('mbox: camera receiver is too slow')
            if time.monotonic()-last_progress>5:
                raise SystemExit('mbox: camera stream stalled')
finally:
    # A second TERM/INT/HUP must not raise SystemExit out of the grace-period
    # wait and skip escalation/reaping. Outer launchers may signal us again.
    for sig in STOP_SIGNALS:
        signal.signal(sig, signal.SIG_IGN)
    listener.close()
    if client: client.close()
    if fd >= 0: os.close(fd)
    if child and child.poll() is None:
        child.terminate()
        try: child.wait(timeout=2)
        except subprocess.TimeoutExpired: child.kill(); child.wait()
    Path(SOCKET).unlink(missing_ok=True)
