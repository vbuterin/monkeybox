#!/usr/bin/env python3
"""Private Xwayland/Xlib integration test. Usage: SWAY SATELLITE XIM LIBX11 [ELECTRON].
No host desktop, Signal account, network, microphone or keyboard is used.
"""
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time

client = r'''
import ctypes as c, locale, os, sys
locale.setlocale(locale.LC_ALL, '')
x = c.CDLL(sys.argv[1])
def fn(name, restype, *args):
    f = getattr(x, name); f.restype = restype; f.argtypes = list(args); return f
P = c.c_void_p; U = c.c_ulong; I = c.c_int; S = c.c_char_p
open_display = fn('XOpenDisplay', P, S)
d = open_display(None); assert d
root = fn('XDefaultRootWindow', U, P)(d)
w = fn('XCreateSimpleWindow', U, P,U,I,I,c.c_uint,c.c_uint,c.c_uint,U,U)(d,root,0,0,200,100,0,0,0)
fn('XSelectInput', I,P,U,c.c_long)(d,w,1|2|1<<17|1<<21)
fn('XMapWindow', I,P,U)(d,w)
fn('XFlush', I,P)(d)
map_event = (c.c_long * 24)()
while True:
    fn('XNextEvent', I,P,P)(d,c.byref(map_event))
    if c.cast(c.byref(map_event),c.POINTER(I))[0] == 19: break
fn('XSetInputFocus', I,P,U,I,U)(d,w,1,0)
fn('XSync', I,P,I)(d,0)
assert fn('XSetLocaleModifiers', S,S)(b'@im=mbox')
im = fn('XOpenIM', P,P,P,S,S)(d,None,None,None); assert im
# XCreateIC is varargs: explicitly type every pointer/long argument.
x.XCreateIC.restype=P
class Point(c.Structure): _fields_ = [('x', c.c_short), ('y', c.c_short)]
spot = Point(70, 40)
x.XVaCreateNestedList.restype = P
attrs = x.XVaCreateNestedList(I(0), S(b'spotLocation'), c.byref(spot), P(None))
ic = x.XCreateIC(P(im), S(b'inputStyle'), U(0x0008|0x0400),
    S(b'clientWindow'), U(w), S(b'focusWindow'), U(w),
    S(b'preeditAttributes'), P(attrs), P(None)); assert ic
fn('XFree', I,P)(attrs)
fn('XSetICFocus', None,P)(ic)
fn('XFlush', I,P)(d)
print('READY', flush=True)
event = (c.c_long * 24)()
lookup = fn('Xutf8LookupString', I,P,P,P,I,P,P)
while True:
    fn('XNextEvent', I,P,P)(d,c.byref(event))
    if fn('XFilterEvent', I,P,U)(c.byref(event),0): continue
    if c.cast(c.byref(event),c.POINTER(I))[0] == 2:
        buf=c.create_string_buffer(4096); status=I(); sym=U()
        n=lookup(ic,c.byref(event),buf,len(buf),c.byref(sym),c.byref(status))
        if n: print('TEXT:'+buf.raw[:n].hex(),flush=True)
'''

sway, satellite, xim, libx11 = sys.argv[1:5]
electron = sys.argv[5] if len(sys.argv) > 5 else None
with tempfile.TemporaryDirectory(prefix='mbox-xim-test-') as directory:
    root = Path(directory)
    env = dict(os.environ, HOME=directory, XDG_RUNTIME_DIR=directory,
        WLR_BACKENDS='headless', WLR_RENDERER='pixman', WLR_LIBINPUT_NO_DEVICES='1',
        DBUS_SESSION_BUS_ADDRESS='unix:path='+directory+'/no-bus',
        DBUS_SYSTEM_BUS_ADDRESS='unix:path='+directory+'/no-system-bus',
        XMODIFIERS='@im=mbox', LC_ALL='C.UTF-8', MBOX_XIM_CARET=directory+'/xim-caret')
    for key in ('DISPLAY','WAYLAND_DISPLAY','SWAYSOCK','XAUTHORITY','GTK_IM_MODULE','QT_IM_MODULE'):
        env.pop(key,None)
    children=[]
    def start(args, name):
        log=open(root/name,'w+')
        child=subprocess.Popen(args,env=env,stdout=log,stderr=log)
        children.append((child,log))
        return child
    def wait(predicate):
        for _ in range(200):
            if predicate(): return
            if any(p.poll() is not None for p,_ in children): break
            time.sleep(.05)
        for p,log in children:
            log.seek(0); print(log.read(),file=sys.stderr)
        raise AssertionError('timeout/child exit')
    try:
        (root/'sway.conf').write_text('output HEADLESS-1 mode 640x480\nxwayland disable\n')
        start([sway,'-c',str(root/'sway.conf')],'sway.log')
        wait(lambda:any(p.is_socket() for p in root.glob('wayland-*')))
        env['WAYLAND_DISPLAY']=next(p.name for p in root.glob('wayland-*') if p.is_socket())
        display=next(n for n in range(170,220) if not Path(f'/tmp/.X11-unix/X{n}').exists()
                     and not Path(f'/tmp/.X{n}-lock').exists())
        start([satellite,f':{display}','-glamor','none','-nolisten','tcp'],'satellite.log')
        wait(lambda:Path(f'/tmp/.X11-unix/X{display}').is_socket())
        env['DISPLAY']=f':{display}'
        endpoint=str(root/'xim.sock')
        start([xim,endpoint],'xim.log')
        wait(lambda:Path(endpoint).is_socket())
        def commit(text):
            with socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET) as s:
                s.settimeout(2); s.connect(endpoint); s.sendall(text.encode()); return s.recv(1)
        assert commit('no focus') == b'N'
        xclient = start([sys.executable,'-c',client,libx11],'client.log')
        wait(lambda:'READY' in (root/'client.log').read_text())
        # READY follows XFlush, not a server roundtrip. Wait for focus handling.
        time.sleep(.1)
        def caret():
            p = root/'xim-caret'
            return struct.unpack('=q6i', p.read_bytes()) if p.exists() else (0,)*7
        wait(lambda:caret()[0] > 0)
        assert caret()[1:5] == (70,40,1,1), caret()
        assert caret()[5] > 0 and caret()[6] > 0, caret()
        print('PASS: real XIM spot-location attributes publish focused top-level coordinates')
        for text in ('привет', '你好', '🙂', 'abc', '中文 русский'):
            assert commit(text)==b'H'
            expected='TEXT:'+text.encode().hex()
            wait(lambda:expected in (root/'client.log').read_text())
        print('PASS: real Xlib XIM receives Russian, Chinese, emoji and ASCII commits; no-focus routes to Wayland')
        if electron:
            # A fresh, offline Electron app, never Signal's app or user profile.
            xclient.terminate(); xclient.wait(timeout=3)
            entry = next(entry for entry in children if entry[0] is xclient)
            entry[1].close()
            children.remove(entry)
            wait(lambda:caret()[0] == 0)
            (root/'index.html').write_text('<textarea autofocus></textarea>')
            (root/'main.js').write_text('''
const {app, BrowserWindow} = require('electron');
app.disableHardwareAcceleration();
app.whenReady().then(async () => {
  const w = new BrowserWindow({width:400,height:200});
  await w.loadFile(__dirname + '/index.html');
  w.show(); w.focus();
  console.log('XID:' + w.getNativeWindowHandle().readUInt32LE(0));
  await w.webContents.executeJavaScript('document.querySelector("textarea").focus()');
  setInterval(async () => {
    console.log('VALUE:' + JSON.stringify(await w.webContents.executeJavaScript('document.querySelector("textarea").value')));
  },100);
});
''')
            env.pop('WAYLAND_DISPLAY', None)
            env.pop('NIXOS_OZONE_WL', None)
            env['ELECTRON_OZONE_PLATFORM_HINT'] = 'x11'
            env['GTK_IM_MODULE'] = 'xim'
            start([electron,'--ozone-platform=x11','--user-data-dir='+str(root/'electron-profile'),
                   str(root/'main.js')],'electron.log')
            wait(lambda:'VALUE:' in (root/'electron.log').read_text())
            ipc = next(root.glob('sway-ipc*'))
            # Satellite uses an app_id derived from the X11 WM_CLASS.
            import json
            tree = json.loads(subprocess.check_output([str(Path(sway).with_name('swaymsg')),
                '-s', str(ipc), '-t', 'get_tree', '-r'], env=env))
            def walk(node):
                yield node
                for child in node.get('nodes', []) + node.get('floating_nodes', []): yield from walk(child)
            windows = [n for n in walk(tree) if n.get('app_id')]
            assert windows, tree
            subprocess.run([str(Path(sway).with_name('swaymsg')), '-s', str(ipc),
                            '[con_id=' + str(windows[-1]['id']) + '] focus'], env=env, check=True, capture_output=True)
            # Headless Sway has no physical keyboard/seat focus. Set only our
            # private X server's client focus explicitly, as the Xlib probe did.
            focus_script = r'''
import ctypes as c, sys
x=c.CDLL(sys.argv[1]); P=c.c_void_p; U=c.c_ulong
x.XOpenDisplay.restype=P; x.XOpenDisplay.argtypes=[c.c_char_p]
d=x.XOpenDisplay(None)
target = int(sys.argv[2])
assert target
x.XSetInputFocus.argtypes=[P,U,c.c_int,U]
x.XSetInputFocus(d,target,1,0)
x.XSync.argtypes=[P,c.c_int]; x.XSync(d,0)
'''
            xid = next(line.split(':')[1] for line in (root/'electron.log').read_text().splitlines() if line.startswith('XID:'))
            subprocess.run([sys.executable,'-c',focus_script,libx11,xid],env=env,check=True)
            time.sleep(.3)
            assert commit('你好привет') == b'H'
            wait(lambda:'VALUE:"你好привет"' in (root/'electron.log').read_text())
            print('PASS: fresh Electron X11 textarea receives Chinese/Russian through XIM')
    finally:
        for child,log in reversed(children):
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=3)
                except subprocess.TimeoutExpired: child.kill(); child.wait()
            log.close()
