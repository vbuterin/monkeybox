#!/usr/bin/env python3
"""The guest-side forwarder that makes published ports reach a loopback service.

Runs programs/mbox/publish-forward.py directly. It needs a real listening socket
table and a real non-loopback address to bind, but no VM: the forwarder decides
everything from /proc/net/tcp{,6} and from what is listening, so starting, moving
and stopping ordinary listeners here exercises the same paths a dev server would.

No host input, messaging or desktop state is touched; the servers below are
throwaway sockets on high ports.
"""

import contextlib
import http.server
import os
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FORWARDER = os.path.join(HERE, '..', 'programs', 'mbox', 'publish-forward.py')


def free_port():
    with contextlib.closing(socket.socket()) as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def network_address():
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as probe:
        probe.connect(('192.0.2.1', 9))  # no packet is sent
        return probe.getsockname()[0]


def fetch(host, port, hostname='localhost'):
    """GET / from one address, the way a browser would arrive at it."""
    with contextlib.closing(socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET)) as sock:
        sock.settimeout(2)
        sock.connect((host, port))
        sock.sendall(f'GET / HTTP/1.0\r\nHost: {hostname}\r\n\r\n'.encode())
        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    return b''.join(chunks).decode(errors='replace')


class Server(http.server.ThreadingHTTPServer):
    """A one-page server on a chosen address, like a dev server binding localhost."""

    allow_reuse_address = True

    def __init__(self, address, family=socket.AF_INET):
        self.address_family = family
        handler = type('Handler', (http.server.BaseHTTPRequestHandler,), {
            'do_GET': lambda self: self._page(),
            '_page': lambda self: (
                self.send_response(200),
                self.send_header('Content-Length', str(len(self.page))),
                self.end_headers(),
                self.wfile.write(self.page)),
            'log_message': lambda *arguments: None,
        })
        handler.page = self.page
        super().__init__(address, handler)


def start(address, page, family=socket.AF_INET):
    Server.page = page.encode()
    server = Server(address, family)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def wait(predicate, description, timeout=6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError(f'timed out waiting for {description}')


def reachable(host, port):
    try:
        return fetch(host, port)
    except OSError:
        return None


def main():
    address = network_address()
    loopback, wildcard, loopback6 = free_port(), free_port(), free_port()

    log = []
    forwarder = subprocess.Popen(
        [sys.executable, FORWARDER, str(loopback), str(wildcard), str(loopback6)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    threading.Thread(target=lambda: [log.append(line) for line in forwarder.stderr],
                     daemon=True).start()

    def text():
        return ''.join(log)

    try:
        # A service on the guest's loopback is invisible to the host until the
        # forwarder binds the guest's own address for it.
        server = start(('127.0.0.1', loopback), 'loopback page')
        wait(lambda: reachable(address, loopback), 'the loopback port to be forwarded')
        assert 'loopback page' in reachable(address, loopback)
        wait(lambda: f'forwarding {address}:{loopback} to 127.0.0.1:{loopback}' in text(),
             'the forwarding line for the loopback port')

        # Repeated polls must not re-announce or rebind it.
        time.sleep(0.5)
        assert text().count(f'forwarding {address}:{loopback} ') == 1, text()

        # A service that binds the guest's network address needs no help, and the
        # forwarder must not take its port: start it after the poll has already run.
        wait(lambda: text(), 'the first poll', timeout=2)
        wildcard_server = start(('0.0.0.0', wildcard), 'wildcard page')
        wait(lambda: 'wildcard page' in (reachable(address, wildcard) or ''),
             'the wildcard service to answer on the network address')
        assert 'wildcard page' in reachable('127.0.0.1', wildcard)
        time.sleep(0.5)
        assert f'{wildcard}' not in text(), f'forwarded a port that needed nothing: {text()}'

        # IPv6-only loopback is the same problem on the other family.
        server6 = start(('::1', loopback6), 'loopback6 page', socket.AF_INET6)
        wait(lambda: reachable(address, loopback6), 'the IPv6 loopback port to be forwarded')
        wait(lambda: f'to [::1]:{loopback6}' in text(), 'the forwarding line for ::1')

        # When the service stops listening on the loopback, the forwarder must let
        # go of the port, so a restart that binds the network address can have it.
        server.shutdown()
        server.server_close()
        wait(lambda: reachable(address, loopback) is None, 'forwarding to be withdrawn')
        wait(lambda: f'stopped forwarding' in text(), 'the withdrawal line')
        replacement = start(('0.0.0.0', loopback), 'rebound page')
        wait(lambda: 'rebound page' in (reachable(address, loopback) or ''),
             'the freed port to accept a network-address listener')

        # Everything above is about the address bound; nothing depends on a port
        # number, so this is the whole generic behaviour.
        assert wildcard_server is not None and server6 is not None and replacement is not None
        print('PASS: loopback services are forwarded on published ports, ports a '
              'service already serves on the network address are left alone, and a '
              'stopped listener releases the port')
    finally:
        forwarder.terminate()
        try:
            forwarder.wait(timeout=5)
        except subprocess.TimeoutExpired:
            forwarder.kill()


if __name__ == '__main__':
    main()
