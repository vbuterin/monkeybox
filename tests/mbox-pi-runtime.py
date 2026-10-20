#!/usr/bin/env python3
"""Real mbox/Pi with fake OpenAI SSE and synthetic shared resources only."""
import http.server
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading

wrapper = sys.argv[1]
requests = []
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        requests.append((self.path, body))
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for delta, finish in [({'role':'assistant', 'content':'MBOX_PI_REPLY_OK'}, None), ({}, 'stop')]:
            event = {'id':'fixture', 'object':'chat.completion.chunk', 'created':1,
                     'model':'fixture', 'choices':[{'index':0,'delta':delta,'finish_reason':finish}]}
            self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()

with tempfile.TemporaryDirectory(prefix='mbox-pi-') as tmp:
    root = Path(tmp)
    home, project, state = root/'home', root/'home/Music', root/'state'
    project.mkdir(parents=True)
    state.mkdir()
    agent = home/'.pi/agent'
    agent.mkdir(parents=True)
    for resource in ['skills','themes','extensions','prompts']:
        (agent/resource).mkdir()
        (agent/resource/'fixture.txt').write_text('SHARED_RESOURCE')
    (agent/'auth.json').write_text('HOST_AUTH_NOT_SHARED')
    (agent/'settings.json').write_text('HOST_SETTINGS_NOT_SHARED')
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_port
    (agent/'models.json').write_text(json.dumps({'providers':{'fixture':{
        'api':'openai-completions','apiKey':'dummy','baseUrl':f'http://127.0.0.1:{port}/v1',
        'models':[{'id':'fixture','contextWindow':65536,'maxTokens':1024}]}}}))
    backing = state/('projects'+str(project))/'home'
    for path in ['.npm-global','.pi/agent']:
        p=backing/path
        p.mkdir(parents=True, exist_ok=True)
        os.setxattr(p, 'user.msb.override_stat', struct.pack('=B3xIIII',1,0,0,0o40755,0))
    env=dict(os.environ, HOME=str(home), MBOX_STATE_ROOT=str(state), MSB_HOME=str(state/'engine'))
    def run(args, expected=0):
        try:
            r=subprocess.run([wrapper,str(project),*args],env=env,text=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
        except subprocess.TimeoutExpired as exc:
            print('TIMEOUT OUTPUT:', exc.stdout, exc.stderr, 'REQUESTS:', requests, flush=True)
            import hashlib
            name='mbox-'+hashlib.sha256(str(project).encode()).hexdigest()[:12]
            subprocess.run([wrapper,'logs','--source','system',name],env=env,timeout=15)
            subprocess.run([wrapper,'stop',name],env=env,timeout=15)
            raise
        print(r.stdout, flush=True)
        if r.returncode != expected:
            import hashlib
            name='mbox-'+hashlib.sha256(str(project).encode()).hexdigest()[:12]
            subprocess.run([wrapper,'logs','--source','system',name],env=env,timeout=15)
        assert r.returncode==expected, r.returncode
        return r.stdout
    try:
        for flag in ['-f','--file','-p','--expose-port','-P','--persist',
                     '-e','--env','-m','--memory','-c','--cpus','-n','--network','--image','-C','--nix-cache']:
            for suffix in ([], ['--'], ['']):
                text=run([flag,*suffix],2)
                assert 'requires a value' in text and 'unbound variable' not in text
        script='''set -e
            test ! -e "$HOME/.pi/agent/auth.json"
            test ! -e "$HOME/.pi/agent/settings.json"
            test -r "$HOME/.pi/agent/models.json"
            for resource in skills prompts extensions themes; do
                grep -q SHARED_RESOURCE "$HOME/.pi/agent/$resource/fixture.txt"
                if touch "$HOME/.pi/agent/$resource/forbidden" 2>/dev/null; then exit 1; fi
            done
            if echo BAD >> "$HOME/.pi/agent/models.json" 2>/dev/null; then exit 1; fi
            mkdir -p "$HOME/.npm-global/bin"
            echo OK > "$HOME/.npm-global/bin/fixture"
            echo '{}' > "$HOME/.pi/agent/settings.json"
            timeout 20 pi --provider fixture --model fixture --no-tools --no-extensions --no-skills --no-context-files -p 'fixture prompt' </dev/null
        '''
        out=run(['--host-loopback','-p',str(port),'--network','blocked','-e','PI_OFFLINE=1','--','bash','-c',script])
        assert 'Permission denied' not in out and 'MBOX_PI_REPLY_OK' in out
        assert requests and requests[0][0]=='/v1/chat/completions'
        assert (agent/'auth.json').read_text()=='HOST_AUTH_NOT_SHARED'
        assert (agent/'settings.json').read_text()=='HOST_SETTINGS_NOT_SHARED'
        assert list((backing/'.pi/agent/sessions').rglob('*.jsonl'))
        run(['--network','blocked','--','bash','-c', '''set -e
            test -s "$HOME/.pi/agent/settings.json"
            grep -q OK "$HOME/.npm-global/bin/fixture"
            test -n "$(find "$HOME/.pi/agent/sessions" -name '*.jsonl' -print -quit)"
            echo PI_NPM_PERSIST_OK
        '''])
        print('PASS: stale ownership repaired, private Pi sessions/settings, shared RO resources, real Pi via guest loopback, missing arguments')
    finally:
        server.shutdown()
        server.server_close()
