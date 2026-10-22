"""Camera-only guest PipeWire + private session bus. No host bus/socket grants."""
import asyncio
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
from dbus_next import Message, MessageType, Variant
from dbus_next.aio import MessageBus

PW, WP, DBUS, SOURCE, CONFIG, *command = sys.argv[1:]
children = []

async def main():
    with tempfile.TemporaryDirectory(prefix='mbox-camera-') as tmp:
        runtime = Path(tmp)
        env = dict(os.environ, PIPEWIRE_RUNTIME_DIR=tmp, PIPEWIRE_REMOTE='pipewire-0',
                   DBUS_SESSION_BUS_ADDRESS='unix:path='+tmp+'/bus')
        # Preserve the private visual runtime for applications, but use a separate
        # local runtime for camera daemons. Never inherit a host PipeWire remote.
        daemon_env = dict(env, XDG_RUNTIME_DIR=tmp)
        def spawn(args, **kwargs):
            p = subprocess.Popen(args, env=daemon_env, stdin=subprocess.DEVNULL, **kwargs)
            children.append(p); return p
        dbus = spawn([DBUS,'--config-file='+str(Path(DBUS).parents[1]/'share/dbus-1/session.conf'),
                     '--nofork','--address='+env['DBUS_SESSION_BUS_ADDRESS']])
        pipewire = spawn([PW,'-c',CONFIG])
        for _ in range(100):
            if any(p.poll() is not None for p in children): raise RuntimeError('camera daemon failed')
            if (runtime/'bus').is_socket() and (runtime/'pipewire-0').is_socket(): break
            await asyncio.sleep(.05)
        if not (runtime/'bus').is_socket() or not (runtime/'pipewire-0').is_socket():
            raise RuntimeError('camera startup timeout')
        # Policy/linking only: do not enumerate ALSA, Bluetooth or V4L2 devices.
        wireplumber = spawn([WP,'--profile','policy'])
        stream = socket.socket(socket.AF_VSOCK,socket.SOCK_STREAM)
        stream.settimeout(5); stream.connect((2,5004)); stream.settimeout(None)
        source = subprocess.Popen([SOURCE,'--stdin'],env=daemon_env,stdin=stream)
        children.append(source); stream.close()
        bus = await MessageBus(bus_address=env['DBUS_SESSION_BUS_ADDRESS'],negotiate_unix_fd=True).connect()
        await bus.request_name('org.freedesktop.portal.Desktop')
        granted = set()
        camera_available = True
        services = [('session bus', dbus), ('PipeWire', pipewire),
                    ('WirePlumber', wireplumber), ('frame source', source)]
        loop = asyncio.get_running_loop()
        def handler(msg):
            if msg.message_type != MessageType.METHOD_CALL: return
            if msg.path == '/org/freedesktop/portal/desktop' and msg.interface == 'org.freedesktop.portal.Camera':
                if not camera_available:
                    return Message.new_error(msg,'org.freedesktop.portal.Error.NotAvailable','Camera stream ended; relaunch to grant capture again')
                if msg.member == 'AccessCamera' and msg.signature == 'a{sv}':
                    token = msg.body[0].get('handle_token',Variant('s','camera')).value
                    if not isinstance(token,str) or not token or len(token)>128 or not all(c.isascii() and (c.isalnum() or c=='_') for c in token):
                        return Message.new_error(msg,'org.freedesktop.portal.Error.InvalidArgument','Invalid token')
                    path='/org/freedesktop/portal/desktop/request/'+msg.sender[1:].replace('.','_')+'/'+token
                    # Bound bookkeeping even if a guest creates many bus clients.
                    if len(granted) >= 256 and msg.sender not in granted:
                        return Message.new_error(msg,'org.freedesktop.portal.Error.Failed','Too many clients')
                    granted.add(msg.sender)
                    def respond():
                        bus.send(Message(path=path,interface='org.freedesktop.portal.Request',member='Response',
                            message_type=MessageType.SIGNAL,signature='ua{sv}',body=[0,{}],destination=msg.sender))
                    loop.call_soon(respond)
                    return Message.new_method_return(msg,'o',[path])
                if msg.member == 'OpenPipeWireRemote' and msg.signature == 'a{sv}' and msg.sender in granted:
                    # Send only a guest-local remote FD. Await send completion before
                    # closing our copy; repeated calls cannot leak descriptors.
                    async def send_remote():
                        if not camera_available:
                            await bus.send(Message.new_error(msg,'org.freedesktop.portal.Error.NotAvailable','Camera stream ended'))
                            return
                        with socket.socket(socket.AF_UNIX) as remote:
                            remote.connect(str(runtime/'pipewire-0'))
                            await bus.send(Message.new_method_return(msg,'h',[0],unix_fds=[remote.fileno()]))
                    asyncio.create_task(send_remote())
                    return True
            if msg.interface == 'org.freedesktop.DBus.Properties' and msg.body and msg.body[0]=='org.freedesktop.portal.Camera':
                props={'version':Variant('u',1),'IsCameraPresent':Variant('b',camera_available)}
                if msg.member=='GetAll': return Message.new_method_return(msg,'a{sv}',[props])
                if msg.member=='Get' and len(msg.body)==2 and msg.body[1] in props:
                    return Message.new_method_return(msg,'v',[props[msg.body[1]]])
            if msg.interface == 'org.freedesktop.portal.Request' and msg.member=='Close':
                return Message.new_method_return(msg)
            return Message.new_error(msg,'org.freedesktop.DBus.Error.UnknownMethod','Camera-only guest portal')
        bus.add_message_handler(handler)
        # All apps in this opted-in VM can access the camera. This portal is
        # compatibility plumbing, NOT per-application security isolation.
        app = subprocess.Popen(command,env=env)
        children.append(app)
        try:
            while app.poll() is None:
                failed = [(name, p.returncode) for name, p in services if p.poll() is not None]
                if camera_available and failed:
                    camera_available = False
                    granted.clear()
                    print('mbox: camera unavailable (' + ', '.join(f'{name} exited {code}' for name, code in failed)
                          + '); keeping application open; relaunch to restore camera', file=sys.stderr, flush=True)
                    # Revoke even already-issued PipeWire FDs by stopping the private
                    # camera server. Keep the app and its session bus, not a stale
                    # source or a background reconnect loop. Closing source stdin
                    # propagates EOF through vsock to release host capture.
                    capture_services = [source, wireplumber, pipewire]
                    for p in capture_services:
                        if p.poll() is None:
                            try: p.terminate()
                            except ProcessLookupError: pass
                    deadline = loop.time() + 2
                    while any(p.poll() is None for p in capture_services) and loop.time() < deadline:
                        await asyncio.sleep(.05)
                    for p in capture_services:
                        if p.poll() is None:
                            try: p.kill()
                            except ProcessLookupError: pass
                    # poll() reaps exited children; final cleanup also waits for
                    # any delayed exits. Do not stall the app on blocked I/O.
                    for p in capture_services: p.poll()
                    if bus.connected:
                        try:
                            await bus.send(Message(path='/org/freedesktop/portal/desktop',
                                interface='org.freedesktop.DBus.Properties', member='PropertiesChanged',
                                message_type=MessageType.SIGNAL, signature='sa{sv}as',
                                body=['org.freedesktop.portal.Camera', {'IsCameraPresent':Variant('b',False)}, []]))
                        except Exception:
                            # A concurrently lost bus must not turn camera failure
                            # back into application termination.
                            print('mbox: camera-unavailable notification failed', file=sys.stderr)
                await asyncio.sleep(.1)
            return app.returncode
        finally: bus.disconnect()

def stop(signum, frame): raise SystemExit(128+signum)
signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
try:
    sys.exit(asyncio.run(main()))
finally:
    for p in reversed(children):
        if p.poll() is None: p.terminate()
    for p in reversed(children):
        try: p.wait(timeout=2)
        except subprocess.TimeoutExpired: p.kill(); p.wait()
