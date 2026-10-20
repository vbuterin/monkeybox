"""Disposable guest-local PipeWire/portal/getUserMedia fixture. Synthetic pixels only."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from dbus_next import Message, MessageType, Variant
from dbus_next.aio import MessageBus

PW, WP, DBUS, FIREFOX = sys.argv[1:]
project = Path.cwd()
processes = []
logs = []

def spawn(name, argv, env):
    log = (project/(name+'.log')).open('wb'); logs.append(log)
    p = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    processes.append(p)
    return p

async def main():
    with tempfile.TemporaryDirectory(prefix='camera-guest-') as tmp:
        runtime = Path(tmp); runtime.chmod(0o700)
        env = dict(os.environ, XDG_RUNTIME_DIR=tmp, PIPEWIRE_RUNTIME_DIR=tmp,
                   DBUS_SESSION_BUS_ADDRESS='unix:path='+tmp+'/bus', MOZ_ENABLE_WAYLAND='0',
                   MOZ_LOG='VideoCapture:5')
        for k in ('DISPLAY','WAYLAND_DISPLAY','PIPEWIRE_REMOTE','DBUS_SYSTEM_BUS_ADDRESS'):
            env.pop(k,None)
        spawn('dbus', [DBUS,'--config-file='+str(Path(DBUS).parents[1]/'share/dbus-1/session.conf'),'--nofork','--address='+env['DBUS_SESSION_BUS_ADDRESS']], env)
        config = runtime/'pipewire.conf'
        config.write_text('''context.properties = { core.daemon = true core.name = pipewire-0 }
context.spa-libs = {
    support.* = support/libspa-support
    videotestsrc = videotestsrc/libspa-videotestsrc
    audio.convert.* = audioconvert/libspa-audioconvert
}
context.modules = [
    { name = libpipewire-module-protocol-native }
    { name = libpipewire-module-metadata }
    { name = libpipewire-module-spa-node-factory }
    { name = libpipewire-module-client-node }
    { name = libpipewire-module-client-device }
    { name = libpipewire-module-access }
    { name = libpipewire-module-adapter }
    { name = libpipewire-module-link-factory }
    { name = libpipewire-module-session-manager }
]
context.objects = [
    { factory = metadata args = { metadata.name = default } }
    { factory = spa-node-factory args = {
        factory.name = support.node.driver node.name = Dummy-Driver node.group = pipewire.dummy priority.driver = 20000
    } }
]
''')
        spawn('pipewire', [PW,'-c',str(config)], env)
        for _ in range(100):
            if (runtime/'bus').is_socket() and (runtime/'pipewire-0').is_socket(): break
            await asyncio.sleep(.05)
        assert (runtime/'pipewire-0').is_socket(), 'PipeWire did not start'
        spawn('wireplumber', [WP], env)
        source = '/nix/store/fhjkf33rv43wkynz36y8xjhbmj6naid9-mbox-synthetic-camera/bin/mbox-synthetic-camera'
        if os.environ.get('MBOX_CAMERA_BRIDGE') == '1':
            stream = socket.socket(socket.AF_VSOCK,socket.SOCK_STREAM); stream.connect((2,5004))
            log = (project/'synthetic-source.log').open('wb'); logs.append(log)
            processes.append(subprocess.Popen([source,'--stdin'],env=env,stdin=stream,stdout=log,stderr=log))
            stream.close()
        else:
            spawn('synthetic-source', [source], env)
        bus = await MessageBus(bus_address=env['DBUS_SESSION_BUS_ADDRESS'], negotiate_unix_fd=True).connect()
        await bus.request_name('org.freedesktop.portal.Desktop')
        await asyncio.sleep(1)
        with (project/'pw-dump.log').open('w') as log:
            try:
                subprocess.run([str(Path(PW).with_name('pw-dump'))], env=env, stdout=log, stderr=log, timeout=3)
            except subprocess.TimeoutExpired:
                pass
        remotes = []
        loop = asyncio.get_running_loop()
        def handler(msg):
            if msg.message_type != MessageType.METHOD_CALL: return
            if msg.interface == 'org.freedesktop.portal.Camera':
                print('PORTAL_CALL', msg.member, flush=True)
                if msg.member == 'AccessCamera':
                    options = msg.body[0]
                    token = options.get('handle_token',Variant('s','camera')).value
                    path = '/org/freedesktop/portal/desktop/request/'+msg.sender[1:].replace('.','_')+'/'+token
                    print('REQUEST_PATH',path,flush=True)
                    def respond():
                        print('RESPONSE_SENT',path,flush=True)
                        bus.send(Message(path=path,interface='org.freedesktop.portal.Request',member='Response',message_type=MessageType.SIGNAL,signature='ua{sv}',body=[0,{}],destination=msg.sender))
                    loop.call_later(.05,respond)
                    return Message.new_method_return(msg,'o',[path])
                if msg.member == 'OpenPipeWireRemote':
                    s = socket.socket(socket.AF_UNIX); s.connect(str(runtime/'pipewire-0')); remotes.append(s)
                    return Message.new_method_return(msg,'h',[0],unix_fds=[s.fileno()])
            if msg.interface == 'org.freedesktop.DBus.Properties':
                print('PROPERTY_CALL', msg.body, flush=True)
                if msg.body[0] != 'org.freedesktop.portal.Camera':
                    return Message.new_error(msg,'org.freedesktop.DBus.Error.UnknownInterface','Only synthetic Camera is supported')
                properties = {'version':Variant('u',1),'IsCameraPresent':Variant('b',True)}
                if msg.member == 'GetAll': return Message.new_method_return(msg,'a{sv}',[properties])
                if msg.member == 'Get' and msg.body[1] in properties:
                    return Message.new_method_return(msg,'v',[properties[msg.body[1]]])
            if msg.interface == 'org.freedesktop.portal.Request' and msg.member == 'Close':
                return Message.new_method_return(msg)
        bus.add_message_handler(handler)
        result = loop.create_future()
        page = b'''<!doctype html><video id="v" autoplay muted></video><canvas id="c"></canvas><script>
(async()=>{let r;try {
 const stream=await navigator.mediaDevices.getUserMedia({video:{width:320,height:240},audio:false});
 v.srcObject=stream;await v.play();await new Promise(ok=>setTimeout(ok,1500));
 c.width=v.videoWidth;c.height=v.videoHeight;c.getContext('2d').drawImage(v,0,0);
 const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
 const values=new Set();for(let i=0;i<d.length;i+=4)values.add(`${d[i]},${d[i+1]},${d[i+2]}`);
 r={ok:c.width>0&&values.size>1,width:c.width,height:c.height,colors:values.size,
 top:Array.from(d.slice(0,4)),bottom:Array.from(d.slice(d.length-4)),
 label:stream.getVideoTracks()[0].label,settings:stream.getVideoTracks()[0].getSettings()};
 stream.getTracks().forEach(t=>t.stop());
}catch(e){r={ok:false,error:e.name,message:e.message};}
 await fetch('/result',{method:'POST',body:JSON.stringify(r)});
})();</script>'''
        async def http(reader, writer):
            try:
                headers = await reader.readuntil(b'\r\n\r\n')
                print('HTTP',headers.split(b'\r\n')[0].decode(),flush=True)
                if headers.startswith(b'POST /result '):
                    length = next(int(l.split(b':',1)[1]) for l in headers.split(b'\r\n') if l.lower().startswith(b'content-length:'))
                    assert length < 65536
                    data = json.loads(await reader.readexactly(length))
                    if not result.done(): result.set_result(data)
                    body=b'ok'
                else: body=page
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
                await writer.drain()
            finally: writer.close()
        server = await asyncio.start_server(http,'127.0.0.1',0)
        port = server.sockets[0].getsockname()[1]
        profile = Path(tempfile.mkdtemp(prefix='camera-firefox-', dir='/dev/shm'))
        (profile/'user.js').write_text('''user_pref("media.webrtc.camera.allow-pipewire", true);
user_pref("media.navigator.permission.disabled", true);
// Fixture avoids Firefox's separate store-font statfs probe; no host profile change.
user_pref("gfx.bundled-fonts.activate", 0);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
''')
        browser_env = dict(env, HOME=str(profile), TMPDIR='/dev/shm', XDG_CACHE_HOME=str(profile/'cache'), XDG_CONFIG_HOME=str(profile/'config'))
        browser_command = [FIREFOX,'--headless','--no-remote','--profile',str(profile),f'http://127.0.0.1:{port}']
        if os.environ.get('MBOX_CAMERA_TRACE') == '1':
            browser_command = ['/nix/store/ndyzkj4lgi9v4dhg1l61vqz67291cz5y-strace-7.2/bin/strace','-f','-tt','-T','-o',str(project/'browser-start.trace')] + browser_command
        spawn('firefox',browser_command,browser_env)
        try:
            data = await asyncio.wait_for(result,200)
            (project/'result.json').write_text(json.dumps(data))
            print('CAMERA_RESULT',json.dumps(data),flush=True)
            assert data['ok'], data
            if os.environ.get('MBOX_CAMERA_BRIDGE') == '1':
                assert data['top'][0]>200 and data['top'][1]<30, data
                assert data['bottom'][1]>200 and data['bottom'][0]<30, data
                print('VSOCK_CAMERA_PIXELS_OK',flush=True)
        finally:
            server.close(); await server.wait_closed()
            bus.disconnect()
            for s in remotes: s.close()

try:
    asyncio.run(main())
finally:
    for p in reversed(processes):
        if p.poll() is None: p.terminate()
    for p in reversed(processes):
        try: p.wait(timeout=3)
        except subprocess.TimeoutExpired: p.kill(); p.wait()
    for log in logs: log.close()
