#!/usr/bin/env python3
"""Private guest-supervisor fixture: no camera, bus, VM, or real application.

Execute the actual helper with mocked process/IPC boundaries. This verifies
failure policy and portal responses, not real PipeWire/Electron recovery.
"""
import asyncio
from pathlib import Path
import runpy
import socket
import sys
import types
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'programs/mbox/camera-guest.py'


class Variant:
    def __init__(self, signature, value): self.value = value


class Message:
    def __init__(self, **kwargs): self.__dict__.update(kwargs)
    @staticmethod
    def new_error(msg, name, text): return Message(error=name)
    @staticmethod
    def new_method_return(msg, signature='', body=None, **kwargs):
        return Message(body=body, **kwargs)


class Stream:
    def settimeout(self, value): pass
    def connect(self, address): pass
    def close(self): pass


def case(failure, notification_failure=False):
    processes = []
    state = {'ticks': 0, 'checked': False}

    class Process:
        def __init__(self, args, **kwargs):
            self.index = len(processes)
            self.returncode = None
            self.signals = []
            processes.append(self)
        def poll(self): return self.returncode
        def terminate(self):
            self.signals.append('TERM')
            self.returncode = -15
        def kill(self):
            self.signals.append('KILL')
            self.returncode = -9
        def wait(self, timeout=None): return self.returncode

    class Bus:
        connected = True
        def __init__(self, **kwargs): self.sent = []; state['bus'] = self
        async def connect(self): return self
        async def request_name(self, name): pass
        def add_message_handler(self, handler): self.handler = handler
        def disconnect(self): self.connected = False
        def send(self, msg):
            self.sent.append(msg)
            future = asyncio.get_running_loop().create_future()
            if notification_failure and getattr(msg, 'member', None) == 'PropertiesChanged':
                future.set_exception(ConnectionError('fixture bus disconnected'))
            else:
                future.set_result(None)
            return future

    def request(interface, member, body, signature='a{sv}'):
        return Message(message_type=1, path='/org/freedesktop/portal/desktop',
                       interface=interface, member=member, body=body,
                       signature=signature, sender=':1.42')

    real_sleep = asyncio.sleep
    async def sleep(delay):
        state['ticks'] += 1
        bus = state['bus']
        camera = 'org.freedesktop.portal.Camera'
        if state['ticks'] == 1:
            # All daemons and the app started. Grant before failure to ensure
            # previously granted callers also lose access.
            assert len(processes) == 5
            reply = bus.handler(request(camera, 'AccessCamera', [{}]))
            assert not hasattr(reply, 'error')
            processes[failure].returncode = 7
        elif not state['checked']:
            assert processes[4].poll() is None, 'application was terminated'
            assert not processes[4].signals
            for i in (1, 2, 3):
                assert processes[i].poll() is not None, 'capture not revoked'
            if failure != 0:
                assert processes[0].poll() is None, 'private bus was stopped'
            reply = bus.handler(request('org.freedesktop.DBus.Properties', 'Get',
                                        [camera, 'IsCameraPresent'], 'ss'))
            assert reply.body[0].value is False
            for member in ('AccessCamera', 'OpenPipeWireRemote'):
                reply = bus.handler(request(camera, member, [{}]))
                assert reply.error == 'org.freedesktop.portal.Error.NotAvailable'
            assert any(getattr(m, 'member', None) == 'PropertiesChanged' for m in bus.sent)
            state['checked'] = True
            processes[4].returncode = 23
        await real_sleep(0)

    dbus = types.ModuleType('dbus_next')
    dbus.Message, dbus.Variant = Message, Variant
    dbus.MessageType = types.SimpleNamespace(METHOD_CALL=1, SIGNAL=2)
    aio = types.ModuleType('dbus_next.aio'); aio.MessageBus = Bus
    real_socket = socket.socket
    def sock(family=socket.AF_INET, *args, **kwargs):
        if family == socket.AF_VSOCK: return Stream()
        return real_socket(family, *args, **kwargs)

    with patch.dict(sys.modules, {'dbus_next': dbus, 'dbus_next.aio': aio}), \
         patch('subprocess.Popen', Process), patch('socket.socket', sock), \
         patch.object(Path, 'is_socket', return_value=True), \
         patch('asyncio.sleep', sleep), \
         patch('signal.signal'), \
         patch.object(sys, 'argv', [str(SOURCE), '/fake/pw', '/fake/wp', '/fake/bin/dbus',
                                   '/fake/source', '/fake/config', '/fake/app']):
        try:
            runpy.run_path(str(SOURCE), run_name='__main__')
        except SystemExit as error:
            assert error.code == 23, error.code
        else:
            raise AssertionError('helper did not return application status')
    assert state['checked']
    assert len(processes) == 5, 'unexpected restart'
    assert not processes[4].signals, 'cleanup signalled already-exited app'
    print(f'PASS: service {failure} failure revokes camera, preserves app, returns app status')


for index in (3, 2, 1, 0):
    case(index)
case(3, notification_failure=True)
