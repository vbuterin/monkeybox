"""Guest-only memory counters and disk swap setup before dropping identity."""
import grp
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys

SWAP_MOUNT = '/run/mbox-swap'


def snapshot(root=Path('/')):
    result = {}
    for name in ('proc/meminfo', 'proc/swaps', 'proc/pressure/memory', 'proc/vmstat'):
        try:
            with (root / name).open() as stream:
                result[name] = stream.read(32768)
        except OSError:
            result[name] = 'unavailable'
    return result


def setup(size_mib, tools, root=Path('/')):
    if not 64 <= size_mib <= 65536:
        raise ValueError('swap size must be 64..65536 MiB')
    if not (root / 'proc/swaps').exists():
        raise RuntimeError('guest kernel lacks swap support; select swap-capable firmware before enabling disk swap')
    mount = root / SWAP_MOUNT.lstrip('/')
    # Refuse a directory on the root filesystem, virtiofs or tmpfs. The host
    # launcher supplies a dedicated sandbox-owned ext4 virtio disk at this path.
    rows = (root / 'proc/self/mountinfo').read_text().splitlines()
    matches = [row.split(' - ', 1)[1].split() for row in rows
               if len(row.split()) > 4 and row.split()[4] == SWAP_MOUNT]
    if len(matches) != 1 or matches[0][0] != 'ext4':
        raise RuntimeError('swap requires the dedicated ext4 disk mount')
    info = mount.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
        raise RuntimeError('unsafe guest swap directory')
    mount.chmod(0o700)
    swap = mount / 'swapfile'
    # Never follow/reuse an existing guest file or truncate active swap. Owned
    # volumes are recreated with the sandbox. Fail rather than guess on restart.
    fd = os.open(swap, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        # Allocate actual extents, not a sparse file (swapon rejects holes).
        os.posix_fallocate(fd, 0, size_mib * 1024 * 1024)
        os.fsync(fd)
    finally:
        os.close(fd)
    subprocess.run([tools + '/mkswap', str(swap)], check=True, timeout=30,
                   stdout=subprocess.DEVNULL)
    subprocess.run([tools + '/swapon', '--priority', '10', str(swap)], check=True, timeout=30)


def main():
    if sys.argv[1:] == ['snapshot']:
        print(json.dumps(snapshot()))
        return
    size, tools, identity, *command = sys.argv[1:]
    if not command or os.geteuid() != 0:
        raise RuntimeError('swap launcher requires guest root and an application command')
    user, _, group = identity.partition(':')
    account = pwd.getpwuid(int(user)) if user.isdecimal() else pwd.getpwnam(user)
    gid = (int(group) if group.isdecimal() else grp.getgrnam(group).gr_gid) if group else account.pw_gid
    setup(int(size), tools)
    os.initgroups(account.pw_name, gid)
    os.setgid(gid)
    os.setuid(account.pw_uid)
    os.execv(command[0], command)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('mbox: guest memory setup failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
