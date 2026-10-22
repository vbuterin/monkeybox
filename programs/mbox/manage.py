"""Host-only instance registry and bounded diagnostics. Never mount registry in guests."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import selectors
import stat
import socket
import struct
import subprocess
import sys
import tempfile
import time

GUEST_MEMORY = '@@GUEST_MEMORY@@'


def directory():
    # Use the actual per-UID runtime, independent of sandbox profile/HOME overrides.
    base = Path(f'/run/user/{os.getuid()}')
    for path in (base, base / 'monkeybox'):
        if path != base:
            path.mkdir(mode=0o700, exist_ok=True)
        st = path.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise RuntimeError('unsafe Monkeybox runtime directory')
    return base / 'monkeybox'


def process(pid):
    path = Path('/proc') / str(pid)
    if path.stat().st_uid != os.getuid():
        raise RuntimeError('process belongs to another user')
    fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
    return dict(pid=pid, start=fields[19], parent=int(fields[1]),
                rss_bytes=int(fields[21]) * os.sysconf('SC_PAGE_SIZE'), state=fields[0])


def live(record):
    p = process(record['pid'])
    if p['start'] != record['start'] or p['state'] in ('Z', 'X', 'x'):
        raise RuntimeError('instance has exited (or PID was reused)')
    return p


def register(pid, name, msb, engine, firmware, memory, label):
    p = process(pid)
    # Do not save command lines, environment, clipboard or project contents.
    record = dict(pid=pid, start=p['start'], name=name, msb=msb, engine=engine,
                  firmware=firmware, memory_limit=memory, label=Path(label).name[:80])
    key = f'{pid}-{p["start"]}'
    path = directory() / (key + '.json')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(record, stream)
    return key


def load(key):
    if not re.fullmatch(r'[1-9][0-9]*-[0-9]+', key):
        raise ValueError('invalid instance ID')
    path = directory() / (key + '.json')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_size > 8192:
            raise RuntimeError('unsafe instance record')
        raw = stream.read(8193)
        if len(raw) > 8192:
            raise ValueError('record grew beyond limit')
        record = json.loads(raw)
    if not isinstance(record, dict) or type(record.get('pid')) is not int:
        raise ValueError('invalid instance record')
    for field in ('start', 'name', 'msb', 'engine', 'firmware', 'memory_limit', 'label'):
        if not isinstance(record.get(field), str):
            raise ValueError('invalid instance field')
    if not re.fullmatch(r'mbox-[a-zA-Z0-9-]+', record['name']):
        raise ValueError('invalid sandbox name')
    if key != f'{record["pid"]}-{record["start"]}':
        raise RuntimeError('instance identity mismatch')
    live(record)
    return record


def tree(record):
    live(record)
    processes = {}
    for path in Path('/proc').iterdir():
        if path.name.isdecimal():
            try:
                p = process(int(path.name))
                processes[p['pid']] = p
            except (OSError, RuntimeError, ValueError, IndexError):
                pass
    selected = {record['pid']}
    for _ in range(64):
        more = {pid for pid, p in processes.items() if p['parent'] in selected}
        if more <= selected:
            break
        selected |= more
    live(record)
    return [processes[pid] for pid in sorted(selected) if pid in processes]


def listing():
    items = []
    for path in sorted(directory().glob('*.json')):
        try:
            record = load(path.stem)
            children = tree(record)
            items.append(dict(id=path.stem, label=record['label'], name=record['name'],
                              memory_limit=record['memory_limit'],
                              host_rss_bytes=sum(p['rss_bytes'] for p in children),
                              processes=len(children)))
        except (OSError, RuntimeError, ValueError, KeyError, IndexError):
            # Ignore stale entries; never signal a PID just because a file names it.
            continue
    return items


def query(record):
    # Only a trusted installed msb executable can run on the host. Registered
    # engine/firmware paths come from the host launcher, never the guest.
    msb = Path(record['msb'])
    if not str(msb).startswith('/nix/store/') or not msb.is_file():
        raise RuntimeError('diagnostics require an installed Nix-store msb')
    identity = runtime_identity(record)
    env = dict(PATH='/run/current-system/sw/bin', HOME='/nonexistent',
               MSB_HOME=record['engine'], MSB_LIBKRUNFW_PATH=record['firmware'])
    data = bounded_command([str(msb), 'exec', record['name'], '--no-tty', '--stream',
                            '--quiet', '--timeout', '4s', '--user', '0:0', '--',
                            GUEST_MEMORY, 'snapshot'], env)
    if runtime_identity(record) != identity:
        raise RuntimeError('runtime replaced during diagnostics; discard counters')
    return memory_counters(json.loads(data))


def runtime_identity(record):
    """Bind name-based read-only queries to a live descendant and socket inode.

    CLI exec has no expected-run flag. A replacement may receive the fixed
    snapshot request, but its counters must never be attributed to the old row.
    No destructive operation uses a sandbox name.
    """
    live(record)
    digest = hashlib.sha256(record['name'].encode()).hexdigest()[:24]
    path = Path(record['engine']) / 'run/sandboxes' / digest / 'agent.sock'
    before = path.lstat()
    if not stat.S_ISSOCK(before.st_mode) or before.st_uid != os.getuid():
        raise RuntimeError('unsafe runtime socket')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(.5)
        connection.connect(str(path))
        pid, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        after = path.lstat()
        if uid != os.getuid() or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise RuntimeError('runtime endpoint changed')
        descendants = {p['pid']: p for p in tree(record)}
        peer = descendants.get(pid)
        if peer is None or peer['state'] in ('Z', 'X', 'x'):
            raise RuntimeError('runtime does not belong to selected launcher')
        return (pid, peer['start'], before.st_dev, before.st_ino)


def bounded_command(argv, env, timeout=6, limit=65536):
    """Bound bytes while reading, not after spooling arbitrary guest output."""
    child = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    deadline = time.monotonic() + timeout
    data = bytearray()
    try:
        with selectors.DefaultSelector() as poll:
            poll.register(child.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not poll.select(remaining):
                    raise RuntimeError('guest query timed out')
                chunk = os.read(child.stdout.fileno(), min(8192, limit + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > limit:
                    raise RuntimeError('guest diagnostics exceeded output limit')
        child.wait(timeout=max(0.01, deadline - time.monotonic()))
        if child.returncode:
            raise RuntimeError('guest query failed')
        return bytes(data)
    finally:
        # On timeout/overflow the immediate child has not been reaped, so its
        # PID still pins the process-group number, even if it already exited.
        # Never signal that numeric group after wait() allowed PID reuse.
        if child.returncode is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        child.wait()
        child.stdout.close()


def memory_counters(data):
    """Only fixed numeric counters cross into the report; never arbitrary guest text."""
    if not isinstance(data, dict):
        raise ValueError('invalid guest snapshot')
    result = {}
    for source, names, unit in (
        ('proc/meminfo', ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'), ' kB'),
        ('proc/vmstat', ('pswpin', 'pswpout', 'oom_kill'), ''),
    ):
        text = data.get(source, '')
        if not isinstance(text, str):
            raise ValueError('invalid guest counters')
        for name in names:
            separator = r':\s+' if unit else r'\s+'
            match = re.search(r'^' + name + separator + r'([0-9]{1,20})' + unit + r'$', text, re.M)
            result[name + ('_KiB' if unit else '')] = int(match[1]) if match else None
    return result


def diagnostics(key):
    record = load(key)
    data = dict(instance=key, label=record['label'], memory_limit=record['memory_limit'],
                host_processes=tree(record), captured_unix_seconds=int(time.time()),
                note='Host RSS sum may double-count shared pages; guest counters are guest-reported. No command lines, environment, clipboard or file contents collected.')
    try:
        data['guest'] = query(record)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
        data['guest_error'] = 'Guest memory query unavailable or timed out; host metrics still collected.'
    text = json.dumps(data, indent=2, ensure_ascii=True) + '\n'
    # Explicit /tmp ignores inherited TMPDIR. mkstemp is exclusive, mode 0600.
    fd, path = tempfile.mkstemp(prefix='diagnostics-', suffix='.txt', dir='/tmp')
    with os.fdopen(fd, 'w') as stream:
        stream.write(text)
    return dict(path=path, text=text)


def kill_instance(key):
    record = load(key)
    targets = tree(record)
    handles = []
    try:
        # Pin and revalidate every identity before sending any signal. Killing
        # only the wrapper could leave the VM/capture services orphaned.
        for target in targets:
            try:
                fd = os.pidfd_open(target['pid'])
            except ProcessLookupError:
                continue
            handles.append((target, fd))
            current = process(target['pid'])
            if current['start'] != target['start']:
                raise RuntimeError('process changed during termination; retry')
        live(record)
        # Terminate descendants first, then invoke launcher cleanup only.
        # SIGTERM's ordinary cancel handler calls `msb stop NAME`, which can
        # stop a replacement! USR1 exits through cleanup without name-based RPC.
        ordered = [pair for pair in reversed(handles) if pair[0]['pid'] != record['pid']]
        ordered += [pair for pair in handles if pair[0]['pid'] == record['pid']]
        for target, fd in ordered:
            try:
                signal.pidfd_send_signal(fd, signal.SIGUSR1 if target['pid'] == record['pid'] else signal.SIGKILL)
            except ProcessLookupError:
                pass
    finally:
        for _, fd in handles:
            os.close(fd)
    return dict(message='Termination requested for the selected process tree; verify it disappears. Unsaved work may be lost.')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='Host-only Monkeybox management; JSON output')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('list')
    for action in ('diagnostics', 'kill', 'unregister'):
        p = sub.add_parser(action)
        p.add_argument('id')
    p = sub.add_parser('register')
    p.add_argument('pid', type=int)
    for name in ('name', 'msb', 'engine', 'firmware', 'memory', 'label'):
        p.add_argument(name)
    args = parser.parse_args()
    if args.action == 'register':
        print(register(args.pid, args.name, args.msb, args.engine, args.firmware, args.memory, args.label))
    elif args.action == 'unregister':
        if not re.fullmatch(r'[1-9][0-9]*-[0-9]+', args.id):
            raise ValueError('invalid instance ID')
        (directory() / (args.id + '.json')).unlink(missing_ok=True)
    else:
        value = listing() if args.action == 'list' else diagnostics(args.id) if args.action == 'diagnostics' else kill_instance(args.id)
        print(json.dumps(value, ensure_ascii=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'error': 'Monkeybox operation failed: instance exited, access refused, or runtime unavailable.'}))
        sys.exit(1)
