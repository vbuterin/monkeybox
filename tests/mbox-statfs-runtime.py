#!/usr/bin/env python3
"""Diagnostic: store-vs-home/project/root statvfs latency and space accounting.

Usage: python3 tests/mbox-statfs-runtime.py FIXTURE_WRAPPER
Reports the known slow /nix/store share separately from other guest paths, and
exposes synthesized free-space/inode counts. No desktop bridge or host/user
persistent state is used. Measurements are observations, not quota guarantees.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

# Run the same formatter on both sides. Preserve exact block/inode fields as
# well as readable GiB so rounded values cannot hide synthesized accounting.
command = '''import os,time
side = os.environ.get('STATVFS_SIDE', 'GUEST')
for label,path in (('store','/nix/store'), ('home',os.environ['HOME']),
                   ('project',os.getcwd()), ('root','/')):
    start=time.monotonic()
    print(f'{side}_STATVFS_START label={label} path={path}', flush=True)
    st=os.statvfs(path)
    elapsed=time.monotonic()-start
    unit=st.f_frsize or st.f_bsize
    print(f'{side}_STATVFS label={label} seconds={elapsed:.6f} '
          f'total_GiB={st.f_blocks*unit/2**30:.6f} '
          f'free_GiB={st.f_bfree*unit/2**30:.6f} '
          f'available_GiB={st.f_bavail*unit/2**30:.6f} '
          f'f_files={st.f_files} f_ffree={st.f_ffree} f_favail={st.f_favail} '
          f'f_bsize={st.f_bsize} f_frsize={st.f_frsize} '
          f'f_blocks={st.f_blocks} f_bfree={st.f_bfree} f_bavail={st.f_bavail} '
          f'f_flag={st.f_flag} f_namemax={st.f_namemax}', flush=True)
'''

wrapper = str(Path(sys.argv[1]).resolve())
with tempfile.TemporaryDirectory(prefix='mbox-statfs-') as tmp:
    root = Path(tmp)
    for name in ('home', 'project', 'state'):
        (root/name).mkdir()
    env = dict(os.environ, HOME=str(root/'home'), MBOX_STATE_ROOT=str(root/'state'),
               MSB_HOME=str(root/'state/engine'))
    project = root/'project'
    # Host HOME is the synthetic source home, not the VM's private backing home.
    subprocess.run([sys.executable, '-u', '-c', command], cwd=project,
                   env=dict(env, STATVFS_SIDE='HOST'), check=True, timeout=30)
    start = time.monotonic()
    try:
        subprocess.run([wrapper, str(project), '--', 'python3', '-u', '-c', command],
                       env=env, stdin=subprocess.DEVNULL, check=True, timeout=150)
    finally:
        print('INVOCATION_SECONDS', time.monotonic()-start, flush=True)
        name = 'mbox-' + hashlib.sha256(str(project).encode()).hexdigest()[:12]
        subprocess.run([wrapper, 'stop', name], env=env, timeout=15)
