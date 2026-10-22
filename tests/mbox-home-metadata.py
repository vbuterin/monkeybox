#!/usr/bin/env python3
"""Narrow legacy override repair; no actual host chown or live state."""
import errno
import importlib.util
import os
from pathlib import Path
import struct
import tempfile
from unittest import mock

spec = importlib.util.spec_from_file_location('repair', Path(__file__).resolve().parents[1]/'programs/mbox/repair-home-metadata.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
assert os.getuid() != 0, 'run as ordinary caller, as mbox does'
root_override = struct.pack('=B3xIIII', 1, 0, 0, 0o40755, 0)
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)/'home'
    home.mkdir()
    for name in ['', '.npm-global', '.npm-global/bin', '.pi', '.pi/agent']:
        p = home/name
        p.mkdir(exist_ok=True)
        os.setxattr(p, m.KEY, root_override)
        os.setxattr(p, 'user.fixture', b'keep')
    data = home/'.npm-global/bin/data'
    data.write_text('keep')
    nested = home/'.pi/agent/unrelated'
    nested.mkdir()
    os.setxattr(nested, m.KEY, root_override)
    outside = Path(tmp)/'outside'
    outside.mkdir()
    os.setxattr(outside, m.KEY, root_override)
    (home/'.venv').symlink_to(outside)
    m.main(home)
    for name in ['', '.npm-global', '.npm-global/bin', '.pi', '.pi/agent']:
        assert m.KEY not in os.listxattr(home/name)
        assert os.getxattr(home/name, 'user.fixture') == b'keep'
    assert data.read_text() == 'keep'
    assert os.getxattr(nested, m.KEY) == root_override
    assert os.getxattr(outside, m.KEY) == root_override
    for value in [b'unknown', struct.pack('=B3xIIII', 2, 0, 0, 0o40755, 0),
                  struct.pack('=B3xIIII', 1, 1234, 1234, 0o40755, 0)]:
        os.setxattr(home/'.pi', m.KEY, value)
        m.main(home)
        assert os.getxattr(home/'.pi', m.KEY) == value
    # Another launcher may finish the same repair between getxattr/removexattr.
    target = home/'.pi'
    os.setxattr(target, m.KEY, root_override)
    remove = os.removexattr
    def concurrent_remove(fd, key):
        remove(fd, key)  # the other launcher wins
        remove(fd, key)  # our launcher observes ENODATA
    with mock.patch.object(m.os, 'removexattr', side_effect=concurrent_remove):
        m.main(home)
    assert m.KEY not in os.listxattr(target)
    assert os.getxattr(target, 'user.fixture') == b'keep'
    # Do not hide permission or I/O failures under the concurrency exception.
    for code in (errno.EACCES, errno.EIO):
        os.setxattr(target, m.KEY, root_override)
        with mock.patch.object(m.os, 'removexattr', side_effect=OSError(code, 'fixture')):
            try:
                m.main(home)
            except OSError as error:
                assert error.errno == code
            else:
                raise AssertionError('unexpected removal failure was swallowed')
        assert os.getxattr(target, m.KEY) == root_override
print('PASS: narrow metadata repair, concurrent removal tolerated, other errors preserved')
