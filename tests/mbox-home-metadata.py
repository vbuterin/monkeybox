#!/usr/bin/env python3
"""Narrow legacy override repair; no actual host chown or live state."""
import importlib.util
import os
from pathlib import Path
import struct
import tempfile

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
print('PASS: fixed directories only, symlinks not followed, data/other xattrs/nonroot/unknown overrides preserved')
