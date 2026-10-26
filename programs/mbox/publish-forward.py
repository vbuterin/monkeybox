#!/usr/bin/env python3
"""Forward host connections into the guest's loopback for published ports.

A published port is handed to the guest by the microsandbox network stack at the
guest's *network* address (172.16.0.x), so it reaches only listeners bound to that
address, to 0.0.0.0 or to ::. A dev server started on "localhost" binds the guest's
own loopback and stays invisible from the host even though the port is published,
which is not something every caller should have to know.

This watches the published ports from inside the guest. For each port whose only
listener is on the guest's loopback it binds the guest's network address and
forwards into the loop. A port whose service already listens on the network
address is left completely alone, so the common case neither needs nor gets a
relay and nothing steals a port that would have worked by itself. The relay is
withdrawn again when the loopback listener goes away, which frees the port for a
restart that binds the network address instead.

Nothing here is port-specific: it is driven by the published port list and by what
is actually listening, so any service on any published port is handled the same
way, whenever it starts.
"""

import asyncio
import fcntl
import socket
import struct
import sys

# How often the guest's listening sockets are re-read. The port list is tiny and
# the file read is a few microseconds, so a fast poll keeps the window between a
# service appearing and the relay appearing short enough to be invisible.
POLL_INTERVAL = 0.2

# A listener on one of these addresses is only reachable from inside the guest.
# Anything else (the guest's own address, 0.0.0.0, ::) accepts the host already.
LOOPBACK = ('127.0.0.1', '::1')

TCP_LISTEN = '0A'
SIOCGIFADDR = 0x8915


def log(message):
    print(f'mbox: {message}', file=sys.stderr, flush=True)


def parse_address(text):
    """/proc/net/tcp{,6} local_address -> (address, port)."""
    hex_address, _, hex_port = text.partition(':')
    raw = bytes.fromhex(hex_address)
    if len(raw) == 4:
        return socket.inet_ntop(socket.AF_INET, raw[::-1]), int(hex_port, 16)
    # tcp6 stores the address as four 32-bit words, each little-endian.
    words = [raw[index:index + 4][::-1] for index in range(0, 16, 4)]
    return socket.inet_ntop(socket.AF_INET6, b''.join(words)), int(hex_port, 16)


def listeners(ports):
    """Every listening address per port, from the guest's own socket tables."""
    found = {port: set() for port in ports}
    for path in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            with open(path) as table:
                rows = table.read().splitlines()[1:]
        except OSError:
            continue  # IPv6 may be absent
        for row in rows:
            fields = row.split()
            if len(fields) < 4 or fields[3] != TCP_LISTEN:
                continue
            try:
                address, port = parse_address(fields[1])
            except ValueError:
                continue
            if port in found:
                found[port].add(address)
    return found


def interface_address(name):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        request = struct.pack('256s', name.encode()[:15])
        answer = fcntl.ioctl(probe.fileno(), SIOCGIFADDR, request)
    return socket.inet_ntoa(answer[20:24])


def network_address():
    """The guest's address on the interface the published port arrives on."""
    try:
        with open('/proc/net/route') as routes:
            for row in routes.read().splitlines()[1:]:
                fields = row.split()
                if len(fields) > 2 and fields[1] == '00000000':
                    return interface_address(fields[0])
    except OSError:
        pass
    # No default route: ask the kernel which source address a route would use.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(('192.0.2.1', 9))  # TEST-NET-1, no packet is sent
        return probe.getsockname()[0]


async def pipe(reader, writer):
    """Copy one direction, then half-close the other so no reply is truncated."""
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (OSError, asyncio.IncompleteReadError):
        pass


async def forward(reader, writer, target):
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(*target), timeout=10)
    except (OSError, asyncio.TimeoutError):
        writer.close()
        return
    await asyncio.gather(pipe(reader, upstream_writer),
                         pipe(upstream_reader, writer))
    for stream in (writer, upstream_writer):
        try:
            stream.close()
        except OSError:
            pass


def describe(address):
    return f'[{address}]' if ':' in address else address


async def network_address_when_ready():
    """The guest's address, once the interface has one (it may still be booting)."""
    for _ in range(1500):  # five minutes at the poll interval, then give up
        try:
            address = network_address()
        except OSError:
            address = None
        if address and address not in LOOPBACK:
            return address
        await asyncio.sleep(POLL_INTERVAL)
    return None


async def main(argv):
    ports = sorted({int(argument) for argument in argv})
    if not ports:
        return
    address = await network_address_when_ready()
    if address is None:
        log('no guest network address; published ports are not forwarded into '
            'the guest loopback')
        return
    relays = {}

    while True:
        listening = listeners(ports)
        for port in ports:
            seen = listening[port]
            # A relay of our own must not count as evidence that the service is
            # reachable on the network address: that reading would withdraw the
            # relay and immediately rebuild it, on every poll.
            if port in relays:
                seen = seen - {address}
            loopback = {entry for entry in seen if entry in LOOPBACK}
            # Reachable on its own: the guest's address, 0.0.0.0 or a dual-stack ::.
            reachable = seen - loopback
            target = None
            if loopback and not reachable:
                target = ('127.0.0.1' if '127.0.0.1' in loopback else '::1', port)

            relay = relays.get(port)
            if target is not None and relay is None:
                try:
                    relays[port] = await asyncio.start_server(
                        lambda reader, writer, target=target: forward(reader, writer, target),
                        host=address, port=port)
                except OSError:
                    continue  # address briefly taken; the next pass retries
                log(f'port {port} listens on guest loopback only; forwarding '
                    f'{address}:{port} to {describe(target[0])}:{port} for the host')
            elif target is None and relay is not None:
                relay.close()
                await relay.wait_closed()
                del relays[port]
                log(f'port {port} no longer listens on guest loopback only; '
                    f'stopped forwarding')
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    try:
        asyncio.run(main(sys.argv[1:]))
    except KeyboardInterrupt:
        pass
    except OSError as error:
        log(f'stopped forwarding published ports: {error}')
