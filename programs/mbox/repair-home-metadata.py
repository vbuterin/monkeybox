"""Repair legacy guest-root directory overrides, never host ownership or contents.
Only fixed application-state directories in the caller-owned private home.
Open every component with O_NOFOLLOW; do not traverse arbitrary guest symlinks.
"""
import errno
import os
import stat
import struct
import sys

FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
KEY = "user.msb.override_stat"

def repair(fd):
    st = os.fstat(fd)
    if st.st_uid != os.getuid():
        return
    try:
        value = os.getxattr(fd, KEY)
    except OSError as e:
        if e.errno in (errno.ENODATA, errno.ENOTSUP):
            return
        raise
    if len(value) != 20:
        return
    version, uid, gid, mode, rdev = struct.unpack("=B3xIIII", value)
    if version == 1 and uid == 0 and stat.S_ISDIR(mode) and os.getuid() != 0:
        # Removing only this legacy override restores mount uid/gid mapping and
        # actual caller-owned mode. Preserve all other xattrs and guest data.
        os.removexattr(fd, KEY)
        print("mbox: repaired legacy guest-root metadata on a private state directory", file=sys.stderr)

def main(home):
    root = os.open(home, FLAGS)
    try:
        repair(root)
        for path in (".npm-global", ".npm-global/bin", ".pi", ".pi/agent", ".venv"):
            fd = os.dup(root)
            try:
                for component in path.split("/"):
                    child = os.open(component, FLAGS, dir_fd=fd)
                    os.close(fd)
                    fd = child
                repair(fd)
            except OSError as e:
                if e.errno not in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
                    raise
            finally:
                os.close(fd)
    finally:
        os.close(root)

if __name__ == "__main__":
    main(sys.argv[1])
