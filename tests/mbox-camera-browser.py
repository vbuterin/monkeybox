"""Browser-side check for the real guest camera service; synthetic host frames only."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

async def main():
    result=asyncio.get_running_loop().create_future()
    page=b'''<video id="v" autoplay muted></video><canvas id="c"></canvas><script>
(async()=>{let r;try{const s=await navigator.mediaDevices.getUserMedia({video:true,audio:false});
v.srcObject=s;await v.play();await new Promise(ok=>setTimeout(ok,1500));
c.width=v.videoWidth;c.height=v.videoHeight;c.getContext('2d').drawImage(v,0,0);
const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
r={ok:true,width:c.width,height:c.height,top:Array.from(d.slice(0,4)),bottom:Array.from(d.slice(-4)),label:s.getVideoTracks()[0].label};
s.getTracks().forEach(t=>t.stop());}catch(e){r={ok:false,error:e.name};}
fetch('/result',{method:'POST',body:JSON.stringify(r)});})();</script>'''
    async def http(reader,writer):
        try:
            headers=await reader.readuntil(b'\r\n\r\n')
            if headers.startswith(b'POST /result '):
                n=next(int(l.split(b':',1)[1]) for l in headers.split(b'\r\n') if l.lower().startswith(b'content-length:'))
                assert n<65536
                data=json.loads(await reader.readexactly(n))
                if not result.done():result.set_result(data)
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\nContent-Length: '+str(len(page)).encode()+b'\r\n\r\n'+page)
            await writer.drain()
        finally:writer.close()
    server=await asyncio.start_server(http,'127.0.0.1',0)
    with tempfile.TemporaryDirectory(dir='/dev/shm',prefix='camera-browser-') as tmp:
        profile=Path(tmp)
        (profile/'user.js').write_text('''user_pref("media.webrtc.camera.allow-pipewire",true);
user_pref("media.navigator.permission.disabled",true);
user_pref("gfx.bundled-fonts.activate",0);
user_pref("browser.aboutwelcome.enabled",false);
user_pref("browser.startup.homepage_override.mstone","ignore");
''')
        env=dict(os.environ,HOME=tmp,TMPDIR='/dev/shm',XDG_CACHE_HOME=tmp+'/cache')
        with open('production-browser.log','w') as log:
            p=subprocess.Popen([sys.argv[1],'--headless','--no-remote','--profile',tmp,
                'http://127.0.0.1:'+str(server.sockets[0].getsockname()[1])],env=env,stdout=log,stderr=log)
            try:
                data=await asyncio.wait_for(result,100)
                print('PRODUCTION_CAMERA_RESULT',json.dumps(data),flush=True)
                Path('result.json').write_text(json.dumps(data))
                assert data['ok'] and data['width']==320 and data['height']==240,data
                assert data['top'][0]>200 and data['top'][1]<30,data
                assert data['bottom'][1]>200 and data['bottom'][0]<30,data
            finally:
                p.terminate()
                try:p.wait(timeout=3)
                except subprocess.TimeoutExpired:p.kill();p.wait()
                server.close();await server.wait_closed()
asyncio.run(main())
