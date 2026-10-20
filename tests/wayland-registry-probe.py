#!/usr/bin/env python3
"""Raw registry probe; no libwayland client-side validation of forged binds."""
import json
import os
import socket
import struct
import sys

s = socket.socket(socket.AF_UNIX)
s.settimeout(5)
display = os.environ['WAYLAND_DISPLAY']
s.connect(display if display.startswith('/') else os.path.join(os.environ['XDG_RUNTIME_DIR'], display))
def send(obj, opcode, payload):
    s.sendall(struct.pack('=II', obj, ((8 + len(payload)) << 16) | opcode) + payload)
def exact(n):
    data = b''
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data
def receive():
    obj, word = struct.unpack('=II', exact(8))
    return obj, word & 65535, exact((word >> 16) - 8)
def string(text):
    raw = text.encode() + b'\0'
    return struct.pack('=I', len(raw)) + raw + b'\0' * (-len(raw) % 4)
send(1, 1, struct.pack('=I', 2))  # get_registry
send(1, 0, struct.pack('=I', 3))  # sync
registry = {}
while True:
    obj, opcode, payload = receive()
    if obj == 3 and opcode == 0:
        break
    if obj == 2 and opcode == 0:
        name, size = struct.unpack_from('=II', payload)
        interface = payload[8:8+size-1].decode()
        version = struct.unpack_from('=I', payload, 8 + ((size+3)//4)*4)[0]
        registry[interface] = [name, version]
if len(sys.argv) == 1:
    print(json.dumps(registry))
else:
    # Use a real forbidden host global ID, either with its actual interface or
    # a forged allowed interface. Hiding advertisements alone cannot stop this.
    name, interface = int(sys.argv[1]), sys.argv[2]
    version = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    expect = sys.argv[4] if len(sys.argv) > 4 else 'rejected'
    send(2, 0, struct.pack('=I', name) + string(interface) + struct.pack('=II', version, 4))
    send(1, 0, struct.pack('=I', 5))
    try:
        while True:
            obj, opcode, payload = receive()
            if obj == 1 and opcode == 0:
                break  # display error
            if obj == 5 and opcode == 0:
                if expect == 'accepted':
                    print('BIND_ACCEPTED')
                    sys.exit(0)
                raise AssertionError('forbidden bind survived through sync')
    except (EOFError, ConnectionResetError, BrokenPipeError):
        pass
    assert expect == 'rejected', 'bind unexpectedly rejected'
    print('FORBIDDEN_BIND_REJECTED')
