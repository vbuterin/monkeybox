#!/usr/bin/env python3
"""End a nested desktop session when its outer window is closed.

Only queries the invocation-private guest Sway socket. Never queries or controls
host windows. Windowless apps (audio players, terminals) are not exit signals.
"""
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import time

MAGIC = b'i3-ipc'
HEADER = struct.Struct('<6sII')


def request(path, kind, payload=b''):
    deadline = time.monotonic() + 2

    def receive(sock, size):
        data = bytearray()
        while len(data) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('desktop IPC timed out')
            sock.settimeout(remaining)
            chunk = sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError('desktop IPC closed')
            data.extend(chunk)
        return data

    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(2)
        sock.connect(path)
        sock.sendall(HEADER.pack(MAGIC, len(payload), kind) + payload)
        magic, size, reply_kind = HEADER.unpack(receive(sock, HEADER.size))
        if magic != MAGIC or reply_kind != kind or size > 8 * 1024 * 1024:
            raise ValueError('invalid desktop IPC reply')
        return json.loads(receive(sock, size))


def stop_group(child):
    # The app owns a new session; never signal the wrapper/compositor group.
    # Terminate background children even if the initial command already exited.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(child.pid, sig)
        except ProcessLookupError:
            break
        if sig == signal.SIGTERM:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                child.poll()
                try:
                    os.killpg(child.pid, 0)
                except ProcessLookupError:
                    return
                time.sleep(.1)
    child.wait()


def main():
    path, *command = sys.argv[1:]
    child = None
    stopping = 0

    def stop(sig, _frame):
        nonlocal stopping
        stopping = 128 + sig

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        # Do not launch an app into an already-closed desktop. Installing signal
        # handlers first also covers cancellation during startup.
        if not request(path, 3):
            return 0
        if stopping:
            return stopping
        child = subprocess.Popen(command, start_new_session=True)
        while not stopping:
            status = child.poll()
            if status is not None:
                return status if status >= 0 else 128 - status
            # A disabled output is still present. Only destruction of the
            # outer window removes it; DPMS/temporary disabling is not close.
            if not request(path, 3):
                print('mbox: desktop closed; ending guest session', file=sys.stderr)
                # Ask any remaining views to close before terminating background
                # processes. The shell's EXIT trap tears down the compositor.
                request(path, 0, b'[all] kill')
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
                return 0
            time.sleep(.5)
        return stopping
    except (OSError, ValueError) as error:
        print(f'mbox: guest desktop disconnected: {error}', file=sys.stderr)
        return 1
    finally:
        if child is not None:
            stop_group(child)


if __name__ == '__main__':
    sys.exit(main())
