"""Prepare private Pi state and repair legacy guest-root directory overrides.
Never change host ownership or application contents.
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
        try:
            os.removexattr(fd, KEY)
        except OSError as e:
            # Concurrent launches may both read the legacy override before one
            # removes it. Already absent is success; preserve every other error.
            if e.errno == errno.ENODATA:
                return
            raise
        print("mbox: repaired legacy guest-root metadata on a private state directory", file=sys.stderr)

def main(home):
    root = os.open(home, FLAGS)
    try:
        repair(root)
        for path in (".npm-global", ".npm-global/bin", ".pi", ".pi/agent", ".venv"):
            fd = os.dup(root)
            try:
                for component in path.split("/"):
                    # Read-only Pi resource mounts otherwise create .pi/agent
                    # as guest root on first boot, before Pi can write sessions.
                    # Create only these fixed private parents as the host caller.
                    if path in (".pi", ".pi/agent"):
                        try:
                            os.mkdir(component, mode=0o700, dir_fd=fd)
                        except FileExistsError:
                            pass
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
