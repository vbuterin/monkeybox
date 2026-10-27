"""Host-only instance registry and bounded diagnostics. Never mount registry in guests."""
import argparse
import concurrent.futures
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

# Instance IDs are '<launcher pid>-<process start>'. Labels come from the
# application name and are what a human sees in `list`; both address an instance.
ID_PATTERN = r'[1-9][0-9]*-[0-9]+'

# Guest memory and swap are sampled inside each VM, which costs a guest command
# per instance. The panel refreshes every couple of seconds, so samples are
# cached briefly; the cache is advisory state that `list` may always skip.
GUEST_TTL_SECONDS = 15
CACHE_NAME = 'guest-memory-cache.json'


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
    try:
        p = process(record['pid'])
    except OSError as error:
        raise RuntimeError('instance has exited (or PID was reused)') from error
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
    if not re.fullmatch(ID_PATTERN, key):
        raise ValueError('invalid instance ID')
    record = read_record(key)
    if key != f'{record["pid"]}-{record["start"]}':
        raise RuntimeError('instance identity mismatch')
    live(record)
    return record


def read_record(key):
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
    return record


def resolve(key):
    """An instance ID always works; a label must name exactly one live instance.

    `list` shows labels because that is what a launcher was started as, so
    diagnostics and termination accept them too. Stale records never win over
    live ones, and an ambiguous label is refused rather than guessed.
    """
    if re.fullmatch(ID_PATTERN, key):
        return key
    matches = []
    for path in sorted(directory().glob('*.json')):
        if not re.fullmatch(ID_PATTERN, path.stem):
            continue
        try:
            record = read_record(path.stem)
        except (OSError, RuntimeError, ValueError):
            continue
        if record['label'] == key:
            matches.append(path.stem)
    live_ids = []
    for candidate in matches:
        try:
            load(candidate)
        except (OSError, RuntimeError, ValueError):
            continue
        live_ids.append(candidate)
    chosen = live_ids or matches
    if len(chosen) == 1:
        return chosen[0]
    if not chosen:
        raise ValueError(f"no instance labelled '{key}' is registered; see mbox-manage list")
    raise ValueError(f"label '{key}' matches several instances: {', '.join(chosen)}; pass the instance ID instead")


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
    records = []
    for path in sorted(directory().glob('*.json')):
        if not re.fullmatch(ID_PATTERN, path.stem):
            continue  # only instance records; cache and temp files are not rows
        try:
            records.append((path.stem, load(path.stem)))
        except (OSError, RuntimeError, ValueError, KeyError, IndexError):
            # Ignore stale entries; never signal a PID just because a file names it.
            continue

    def sample(item):
        key, record = item
        try:
            guest = query(record)
            captured = int(time.time())
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            guest, captured = None, int(time.time())
        return key, guest, captured

    samples, refreshed = {}, {}
    if records:
        cache = load_cache()
        now = int(time.time())
        pending = []
        for key, record in records:
            entry = cache.get(key)
            if entry and now - entry['captured_unix_seconds'] < GUEST_TTL_SECONDS:
                samples[key] = (entry['guest'], entry['captured_unix_seconds'])
            else:
                pending.append((key, record))
        if pending:
            # Guests are queried concurrently: one slow VM must not stretch the
            # whole listing by the sum of every timeout.
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                for key, guest, captured in pool.map(sample, pending):
                    samples[key] = (guest, captured)
                    refreshed[key] = {'captured_unix_seconds': captured, 'guest': guest}
        store_cache({**{key: {'captured_unix_seconds': captured, 'guest': guest}
                         for key, (guest, captured) in samples.items() if key not in refreshed},
                     **refreshed})

    for key, record in records:
        guest, captured = samples.get(key, (None, None))
        children = tree(record)
        fields = dict(id=key, label=record['label'], name=record['name'],
                      memory_limit=record['memory_limit'],
                      memory_limit_mib=memory_limit_mib(record['memory_limit']),
                      host_rss_bytes=sum(p['rss_bytes'] for p in children),
                      processes=len(children),
                      guest_captured_unix_seconds=captured)
        fields.update(guest_fields(guest))
        items.append(fields)
    return items


def guest_fields(guest):
    """Fixed numeric guest counters only; absence stays None, never zero."""
    if not isinstance(guest, dict):
        return dict(memory_used_mib=None, swap_used_mib=None, swap_total_mib=None)
    return dict(memory_used_mib=used_mib(guest.get('MemTotal_KiB'), guest.get('MemAvailable_KiB')),
                swap_used_mib=used_mib(guest.get('SwapTotal_KiB'), guest.get('SwapFree_KiB')),
                swap_total_mib=(None if guest.get('SwapTotal_KiB') is None
                                else round(guest['SwapTotal_KiB'] / 1024)))


def used_mib(total_kib, free_kib):
    if total_kib is None or free_kib is None:
        return None
    return max(0, round((total_kib - free_kib) / 1024))


def memory_limit_mib(text):
    """Configured memory as MiB ('10240', '10240M', '8G' all work); else None."""
    match = re.fullmatch(r'\s*([0-9]{1,7})\s*([GgMm])?([Ii]?[Bb])?\s*', str(text))
    if not match:
        return None
    return int(match[1]) * (1024 if match[2] in ('G', 'g') else 1)


def load_cache():
    try:
        fd = os.open(directory() / CACHE_NAME, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return {}
    try:
        with os.fdopen(fd) as stream:
            entries = json.load(stream).get('guest-memory')
    except (OSError, ValueError):
        return {}
    if not isinstance(entries, dict):
        return {}
    clean = {}
    for key, entry in entries.items():
        if not re.fullmatch(ID_PATTERN, str(key)) or not isinstance(entry, dict):
            continue
        if type(entry.get('captured_unix_seconds')) is not int:
            continue
        guest = entry.get('guest')
        if guest is not None and not (isinstance(guest, dict)
                                      and all(value is None or type(value) is int for value in guest.values())):
            continue
        clean[key] = {'captured_unix_seconds': entry['captured_unix_seconds'], 'guest': guest}
    return clean


def store_cache(entries):
    try:
        fd, path = tempfile.mkstemp(prefix='.guest-memory-', suffix='.json', dir=directory())
    except OSError:
        return
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'guest-memory': entries}, stream)
        os.replace(path, directory() / CACHE_NAME)
    except OSError:
        try:
            os.unlink(path)
        except OSError:
            pass


def format_listing(items):
    """Aligned text for people; --json remains the machine-readable form."""
    headers = ('ID', 'LABEL', 'MEMORY USED', 'SWAP', 'PROCS', 'NAME')
    rows = []
    for item in items:
        limit = ('?' if item['memory_limit_mib'] is None else f"{item['memory_limit_mib']} MiB")
        if item['memory_used_mib'] is None:
            memory = f'- / {limit}'
        else:
            memory = f"{item['memory_used_mib']} / {limit}"
        if item['swap_total_mib'] == 0:
            swap = 'not enabled'
        elif item['swap_total_mib'] is None or item['swap_used_mib'] is None:
            swap = '-'
        else:
            swap = f"{item['swap_used_mib']} / {item['swap_total_mib']} MiB"
        rows.append((item['id'], item['label'], memory, swap, str(item['processes']), item['name']))
    widths = [max(len(row[column]) for row in (headers,) + tuple(rows)) for column in range(len(headers))]
    lines = ['  '.join(header.ljust(width) for header, width in zip(headers, widths)).rstrip()]
    lines += ['  '.join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows]
    return '\n'.join(lines)


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
                memory_limit_mib=memory_limit_mib(record['memory_limit']),
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
    parser = argparse.ArgumentParser(description='Host-only Monkeybox management')
    sub = parser.add_subparsers(dest='action', required=True)
    list_parser = sub.add_parser('list', help='running instances; text table unless --json')
    list_parser.add_argument('--json', action='store_true', help='machine-readable JSON array')
    for action, help_text in (('diagnostics', 'memory counters for one instance'),
                              ('kill', 'terminate one instance and its children'),
                              ('unregister', 'remove one registry entry')):
        p = sub.add_parser(action, help=help_text)
        p.add_argument('id', help='instance ID, or the label shown by list')
    p = sub.add_parser('register')
    p.add_argument('pid', type=int)
    for name in ('name', 'msb', 'engine', 'firmware', 'memory', 'label'):
        p.add_argument(name)
    args = parser.parse_args()
    if args.action == 'register':
        print(register(args.pid, args.name, args.msb, args.engine, args.firmware, args.memory, args.label))
    elif args.action == 'unregister':
        (directory() / (resolve(args.id) + '.json')).unlink(missing_ok=True)
    elif args.action == 'list':
        items = listing()
        print(json.dumps(items, ensure_ascii=True) if args.json else format_listing(items))
    else:
        key = resolve(args.id)
        result = diagnostics(key) if args.action == 'diagnostics' else kill_instance(key)
        print(json.dumps(result, ensure_ascii=True))


def cli():
    try:
        main()
    except (ValueError, RuntimeError) as error:
        # Wrong instance, ambiguity and exited instances deserve their real reason.
        print(json.dumps({'error': str(error)}))
        sys.exit(1)
    except Exception:
        print(json.dumps({'error': 'Monkeybox operation failed: instance exited, access refused, or runtime unavailable.'}))
        sys.exit(1)


if __name__ == '__main__':
    cli()
