"""Guest local HTTP first-page probe; fresh private profile only."""
import http.server
import threading
import subprocess
import sys
from pathlib import Path
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/painted': Path('painted').touch()
        page=b'<html style="background:#00ff00"><h1>MBOX FIREFOX FIXTURE</h1><script>requestAnimationFrame(()=>requestAnimationFrame(()=>fetch("/painted")))</script></html>'
        self.send_response(200)
        self.send_header('Content-Length',str(len(page)))
        self.end_headers()
        self.wfile.write(page)
server=http.server.HTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
profile=Path.home()/'browser-profile'
profile.mkdir(exist_ok=True)
(profile/'user.js').write_text('user_pref("browser.aboutwelcome.enabled",false);\nuser_pref("browser.startup.homepage_override.mstone","ignore");\n')
Path('launched').touch()
subprocess.run(sys.argv[1:]+['--no-remote','--profile',str(profile),'http://127.0.0.1:'+str(server.server_port)])
