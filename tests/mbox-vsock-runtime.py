#!/usr/bin/env python3
"""Check guest->host vsock bulk integrity without Wayland or live services.
Usage: python3 tests/mbox-vsock-runtime.py FIXTURE_WRAPPER
Requires KVM and the fixture's Python. Exits nonzero on transport failure.
Only a temporary copy of the realized wrapper gains the private test route.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading

wrapper = Path(sys.argv[1]).resolve()
text = wrapper.read_text()
assert text.count('DESKTOP_ARGS=()') == 1, 'wrapper assembly changed'
failures = []
with tempfile.TemporaryDirectory(prefix='mbox-vsock-test-') as tmp:
    root = Path(tmp)
    for name in ('home', 'project', 'state'):
        (root / name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(root/'home'), MBOX_STATE_ROOT=str(root/'state'),
               MSB_HOME=str(root/'state/engine'))
    vm = 'mbox-' + hashlib.sha256(str(root/'project').encode()).hexdigest()[:12]
    # The acked control prevents coalescing into large guest packets. It is a
    # diagnostic control, not a proposed throughput-preserving production fix.
    for size, acked in ((4096, False), (65536, False), (1048576, False),
                        (16777216, False), (1048576, True)):
        path = root/'socket'
        path.unlink(missing_ok=True)
        payload = bytes(range(256)) * (size // 256)
        received = bytearray()
        host_errors = []
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(path))
        listener.listen(1)
        listener.settimeout(10)

        def receive():
            try:
                conn, _ = listener.accept()
                with conn:
                    conn.settimeout(6)
                    while len(received) < size:
                        # Ack only a complete 4 KiB block, never a short recv.
                        target = min(size, len(received) + (4096 if acked else size))
                        while len(received) < target:
                            data = conn.recv(target - len(received))
                            if not data:
                                raise EOFError('guest closed before all bytes arrived')
                            received.extend(data)
                        if acked:
                            conn.sendall(b'A')
                    conn.sendall(hashlib.sha256(received).digest())
                    # Keep the endpoint open until the guest consumed the digest;
                    # otherwise host-close/RX draining can race independently of TX.
                    if conn.recv(1) != b'D':
                        raise EOFError('guest did not acknowledge digest')
            except Exception as exc:
                host_errors.append(f'{type(exc).__name__}: {exc}')

        thread = threading.Thread(target=receive, daemon=True)
        thread.start()
        diagnostic = root/'wrapper'
        diagnostic.write_text(text.replace('DESKTOP_ARGS=()',
                              f'DESKTOP_ARGS=(--vsock "{path}:5003")'))
        diagnostic.chmod(0o700)
        script = f'''import hashlib, socket
s = socket.socket(socket.AF_VSOCK)
s.settimeout(5)
s.connect((2, 5003))
data = bytes(range(256)) * ({size} // 256)
if {acked!r}:
    for offset in range(0, len(data), 4096):
        s.sendall(data[offset:offset+4096])
        assert s.recv(1) == b'A'
else:
    s.sendall(data)
digest = b''
while len(digest) < 32:
    part = s.recv(32-len(digest))
    assert part, 'EOF before digest'
    digest += part
assert digest == hashlib.sha256(data).digest()
s.sendall(b'D')
print('BULK_DIGEST_OK')
'''
        (root/'project/send.py').write_text(script)
        result = None
        try:
            result = subprocess.run([str(diagnostic), str(root/'project'), '--',
                                     'python3', 'send.py'], env=env, capture_output=True,
                                    text=True, timeout=20)
        finally:
            # Scoped cleanup also handles an exception/outer interruption.
            subprocess.run([str(wrapper), 'stop', vm], env=env, timeout=15,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            thread.join(11)
            listener.close()
        assert not thread.is_alive(), 'host receiver did not stop'
        ok = (result.returncode == 0 and received == payload and not host_errors)
        record = dict(bytes=size, acked_4k=acked, received=len(received),
                      exact_bytes=received == payload, guest_exit=result.returncode,
                      host_errors=host_errors, passed=ok)
        print(json.dumps(record), flush=True)
        print(result.stdout + result.stderr, end='', flush=True)
        # Only diagnostic fixture logs; never inspect a user VM's state.
        for log in (root/'state/engine/sandboxes').glob('*/logs/runtime.log'):
            for line in log.read_text(errors='replace').splitlines():
                if 'error reading TX packet' in line:
                    print(line, flush=True)
        if not ok:
            failures.append(record)
assert not failures, f'{len(failures)} vsock integrity case(s) failed'
print('VSOCK_BULK_INTEGRITY_OK')
