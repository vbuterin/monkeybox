"""Host-only explicit text transfer. No socket or clipboard API exposed to guests."""
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
import uuid

LIMIT = 1024 * 1024
TTL = 300


class ClipboardFailure(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__('clipboard operation refused')


# Stable, payload-free failure categories shared with the notification wrapper.
FOCUS, SESSION, TEXT, EXPIRED = 20, 21, 22, 23


def bounded(argv, env=None, data=None, discard=False):
    # Regular temporary stdin avoids bidirectional pipe deadlocks. Runtime dir
    # is private; contents are unlinked before the subprocess is started.
    import tempfile
    with tempfile.TemporaryFile(dir=directory()) as stdin:
        if data is not None:
            stdin.write(data)
        stdin.seek(0)
        p = subprocess.Popen(argv, env=env, stdin=stdin, stdout=subprocess.DEVNULL if discard else subprocess.PIPE,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        out = bytearray()
        try:
            sel = selectors.DefaultSelector()
            if discard:
                if p.wait(timeout=8):
                    raise RuntimeError('clipboard endpoint failed')
                return b''
            sel.register(p.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 8
            while True:
                if time.monotonic() >= deadline:
                    raise RuntimeError('clipboard operation timed out')
                if not sel.select(.1):
                    continue
                block = os.read(p.stdout.fileno(), 65536)
                if not block:
                    break
                out.extend(block)
                if len(out) > LIMIT:
                    raise ClipboardFailure(TEXT)
            if p.wait(timeout=2):
                raise RuntimeError('clipboard endpoint failed (text may be unavailable)')
            return bytes(out)
        finally:
            sel.close()
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
            if p.stdout:
                p.stdout.close()


def directory():
    runtime = Path(os.environ['XDG_RUNTIME_DIR'])
    st = runtime.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise RuntimeError('XDG_RUNTIME_DIR must be private and caller-owned')
    path = runtime / 'mbox-clipboard'
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise RuntimeError('unsafe clipboard runtime directory')
    return path


def start_time(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def focused():
    tree = json.loads(bounded(['@@SWAYMSG@@', '-r', '-t', 'get_tree']))
    def walk(n):
        yield n
        for child in n.get('nodes', []) + n.get('floating_nodes', []):
            yield from walk(child)
    nodes = [n for n in walk(tree) if n.get('focused') and n.get('pid')]
    if len(nodes) != 1:
        raise ClipboardFailure(FOCUS)
    node = nodes[0]
    pid = int(node['pid'])
    # Waypipe forks connection workers. Sway reports the worker's SO_PEERCRED
    # PID, not necessarily the registered launcher. Walk host /proc ancestry.
    owner = pid
    registration = directory() / f'session-{owner}.json'
    for _ in range(32):
        if registration.exists() or owner <= 1:
            break
        owner = int(Path(f'/proc/{owner}/stat').read_text().rsplit(')', 1)[1].split()[1])
        registration = directory() / f'session-{owner}.json'
    if registration.exists():
        session = json.loads(registration.read_text())
        if session['start'] != start_time(owner) or session['sway'] != os.environ.get('SWAYSOCK'):
            raise ClipboardFailure(SESSION)
        if not session['enabled']:
            raise ClipboardFailure(SESSION)
        return (node['id'], pid, session)
    # Never mistake an unregistered proxy window for a host clipboard endpoint.
    exe = os.readlink(f'/proc/{pid}/exe')
    if Path(exe.removesuffix(' (deleted)')).name == 'waypipe':
        raise ClipboardFailure(SESSION)
    return (node['id'], pid, None)


def endpoint(target, action, data=None):
    session = target[2]
    if session is None:
        # Let wl-paste negotiate a text MIME alias (GTK/X11 need not offer
        # exactly text/plain;charset=utf-8). text() still validates the bytes.
        argv = (['@@WL_PASTE@@', '--no-newline', '--type', 'text']
                if action == 'export' else ['@@WL_COPY@@', '--clear']
                if action == 'clear' else ['@@WL_COPY@@', '--type', 'text/plain;charset=utf-8'])
        return bounded(argv, data=data, discard=action != 'export')
    env = dict(os.environ, MSB_HOME=session['msb_home'],
               MSB_LIBKRUNFW_PATH=session['firmware'])
    argv = [session['msb'], 'exec', session['sandbox'], '--no-tty', '--stream',
            '--quiet', '--timeout', '5s', '--user', session['user'], '--',
            '@@GUEST@@', action]
    return bounded(argv, env=env, data=data)


def application_key(target, key):
    if focused() != target:
        raise ClipboardFailure(FOCUS)
    if target[2] is not None:
        if key == 'x':
            raise ClipboardFailure(SESSION)
        # Do not relay wtype's temporary host keymap through Waypipe/nested Sway:
        # real guest GTK received Ctrl+Escape for Ctrl+C on that path. The host
        # selects a fixed action in its registered VM; no guest->host input API.
        endpoint(target, 'key-copy' if key == 'c' else 'key-paste')
        if focused() != target:
            raise ClipboardFailure(FOCUS)
        return
    # Use the same complete-keymap, fixed-action helper on both desktops.
    # wtype's per-command keymap is unsuitable for GTK editing shortcuts.
    exe = Path(os.readlink(f'/proc/{target[1]}/exe'))
    action = {'c': 'copy', 'v': 'paste', 'x': 'cut'}[key]
    if key == 'x':
        require_cut_target(target)
    if exe.name == 'foot' and str(exe).startswith('/nix/store/'):
        # Semantic keys avoid shell Ctrl+C and global Ctrl+Shift+C/V bindings.
        action += '-foot'
    bounded(['@@KEY@@', action])
    if focused() != target:
        raise ClipboardFailure(FOCUS)


def require_cut_target(target):
    if target[2] is not None:
        raise ClipboardFailure(SESSION)
    exe = Path(os.readlink(f'/proc/{target[1]}/exe')).name
    # Terminal selections aren't editable application selections. In particular
    # never send Readline's Ctrl+X prefix into a shell after copying scrollback.
    if exe in {'foot', 'footclient', 'alacritty', 'kitty', 'wezterm-gui',
               'gnome-terminal-server', 'konsole', 'xterm', 'rxvt', 'urxvt',
               'st', 'terminator', 'xfce4-terminal', 'tilix', 'ghostty'}:
        raise RuntimeError('cut is not supported in terminals')


def copy_selection(target):
    # Do not export a previous clipboard when Copy has no selection or fails.
    # This deliberately clears the source local clipboard before requesting Copy.
    endpoint(target, 'clear')
    application_key(target, 'c')
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if focused() != target:
            raise ClipboardFailure(FOCUS)
        try:
            return text(endpoint(target, 'export'))
        except ClipboardFailure:
            raise
        except RuntimeError:
            time.sleep(.05)
    raise RuntimeError('application did not provide a text selection')


def text(data):
    if len(data) > LIMIT or b'\0' in data:
        raise ClipboardFailure(TEXT)
    try:
        data.decode('utf-8', errors='strict')
    except UnicodeError:
        raise ClipboardFailure(TEXT) from None
    return data


def main():
    with ExitStack() as resources:
        return run(resources)


def run(resources):
    os.umask(0o077)
    path = directory()
    action = sys.argv[1]
    if action == 'pulse':
        # Payload-free UI metadata, not a clipboard history or synchronization.
        event = path / 'activity.json'
        temp = path / ('activity-' + uuid.uuid4().hex)
        operation = sys.argv[2] if len(sys.argv) > 2 else 'copy'
        if operation not in ('copy', 'export', 'paste', 'import', 'clear', 'cut', 'write'):
            raise RuntimeError('invalid activity')
        temp.write_text(json.dumps(dict(at=time.monotonic(), operation=operation)))
        temp.replace(event)
        return
    if action == 'watch':
        previous = None
        while True:
            try:
                event = json.loads((path / 'activity.json').read_text())
                at, operation = event['at'], event['operation']
                current = (at, operation) if 0 <= time.monotonic() - at < 2 else (0, '')
            except (OSError, ValueError, KeyError):
                current = (0, '')
            if current != previous:
                print(json.dumps(dict(text='', icon='clipboard',
                                      activity=current[1], serial=current[0])), flush=True)
                previous = current
            time.sleep(.15)
    if action == 'preview':
        # Explicit user click only. Never invoked by the background indicator.
        # Read the host clipboard and transfer buffer separately: bar focus is
        # not authority to introspect an arbitrary guest's local clipboard.
        result = dict(host='', transfer='', hostStatus='No supported host text',
                      transferStatus='Empty or expired')
        try:
            result['host'] = text(bounded(['@@WL_PASTE@@', '--no-newline', '--type', 'text'])).decode('utf-8')
            result['hostStatus'] = 'Host clipboard (snapshot)'
        except (RuntimeError, OSError):
            pass
        with (path / 'lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            buffer = path / 'buffer.json'
            if buffer.exists():
                record = json.loads(buffer.read_text())
                if time.monotonic() < record['expires']:
                    result['transfer'] = text(record['text'].encode('utf-8')).decode('utf-8')
                    result['transferStatus'] = 'Mbox transfer buffer (snapshot; not consumed)'
                else:
                    buffer.unlink()
        print(json.dumps(result))
        return
    if action == 'register':
        pid, enabled, msb, sandbox, user = sys.argv[2:]
        record = dict(start=start_time(int(pid)), enabled=enabled == '1', msb=msb,
                      sandbox=sandbox, user=user, msb_home=os.environ['MSB_HOME'],
                      firmware=os.environ['MSB_LIBKRUNFW_PATH'], sway=os.environ.get('SWAYSOCK'))
        (path / f'session-{int(pid)}.json').write_text(json.dumps(record))
        return
    if action == 'unregister':
        (path / f'session-{int(sys.argv[2])}.json').unlink(missing_ok=True)
        return
    if action == 'expire':
        time.sleep(TTL)
    if action in ('copy', 'export', 'paste', 'import', 'clear', 'cut', 'write'):
        # Nested helpers (binding echoes, double triggers) must not interleave:
        # a second clear would destroy the first helper's copied text mid-poll.
        operation = resources.enter_context((path / 'operation.lock').open('a'))
        try:
            fcntl.flock(operation, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('another clipboard operation is already running')
    with (path / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        buffer = path / 'buffer.json'
        if action == 'expire':
            if buffer.exists() and json.loads(buffer.read_text())['id'] == sys.argv[2]:
                buffer.unlink()
            return
        if action == 'clear':
            buffer.unlink(missing_ok=True)
            return
        # Explicit shell I/O does not infer authority from the focused window.
        # write always targets the host local clipboard, read only the buffer.
        target = None if action in ('write', 'read') else focused()
        if action == 'cut':
            require_cut_target(target)
        if action in ('export', 'copy', 'cut', 'write'):
            # Invalidate any earlier transfer even when the new export fails.
            buffer.unlink(missing_ok=True)
            if action == 'write':
                data = text(sys.stdin.buffer.read(LIMIT + 1))
                endpoint((None, None, None), 'import', data)
            else:
                data = copy_selection(target) if action in ('copy', 'cut') else text(endpoint(target, 'export'))
                if focused() != target:
                    raise ClipboardFailure(FOCUS)
                if action == 'cut' and not data:
                    raise RuntimeError('refusing to cut an empty selection')
            ident = uuid.uuid4().hex
            buffer.write_text(json.dumps(dict(id=ident, expires=time.monotonic()+TTL,
                                             text=data.decode('utf-8'))))
            subprocess.Popen(['@@PYTHON@@', __file__, 'expire', ident],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            if action == 'cut':
                # Buffer is safely published first. Never delete following a
                # failed copy/store, changed focus, or changed clipboard payload.
                if focused() != target:
                    raise ClipboardFailure(FOCUS)
                if text(endpoint(target, 'export')) != data:
                    raise RuntimeError('selection clipboard changed before Cut')
                application_key(target, 'x')
            print(f'mbox clipboard: text exported; expires in {TTL} seconds', file=sys.stderr)
        elif action in ('import', 'paste', 'read'):
            if not buffer.exists():
                raise ClipboardFailure(EXPIRED)
            record = json.loads(buffer.read_text())
            if time.monotonic() >= record['expires']:
                # A stale buffer cannot be delivered later; remove it now.
                buffer.unlink()
                raise ClipboardFailure(EXPIRED)
            # The buffer survives Paste: one Copy can feed several guests until
            # it expires or an explicit clear removes it. Failed deliveries also
            # keep it, so the Paste can simply be retried on the right window.
            data = text(record['text'].encode('utf-8'))
            if action == 'read':
                sys.stdout.buffer.write(data)
                return
            if focused() != target:
                raise ClipboardFailure(FOCUS)
            endpoint(target, 'import', data)
            if action == 'paste':
                application_key(target, 'v')
                print('mbox clipboard: text imported and Paste requested')
            else:
                print('mbox clipboard: text imported; use ordinary Ctrl+V')
        else:
            raise RuntimeError('expected copy, cut, paste, export, import, write, read or clear')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Never print guest output, payloads or arbitrary exception strings.
        print('mbox clipboard: operation refused or failed; check focus, --clipboard, text size and expiry', file=sys.stderr)
        sys.exit(exc.code if isinstance(exc, ClipboardFailure) else 1)
