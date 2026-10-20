#!/usr/bin/env python3
"""Exercise actual project/home mount assembly; no VM or host-home sharing."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'programs/mbox/mbox.sh.in').read_text()
fragment = source.split('# The project at its own path:', 1)[1]
fragment = '# The project at its own path:' + fragment.split('# Persisted paths usually', 1)[0]
with tempfile.TemporaryDirectory(prefix='mbox-home-project-') as tmp:
    base = Path(tmp)
    home = base / 'home with spaces [test]'
    home.mkdir()
    alias = base / 'home-link'
    alias.symlink_to(home, target_is_directory=True)
    for guest_home in (home, alias):
        for project in (home, home / 'project'):
            for ephemeral in ('0', '1'):
                script = '''
GUEST_HOME=$1; PROJECT=$2; PROJECT_STATE=$3; EPHEMERAL=$4
MBOX_UID=1000; MBOX_GID=1000; RUN_AS_CALLER=1; RUN_AS_SPEC=''; DRY_RUN=1
MOUNTS=()
''' + fragment + '\nprintf "%s\\0" "${MOUNTS[@]}"'
                args = subprocess.check_output([
                    'bash', '-eu', '-c', script, 'test', str(guest_home),
                    str(project), str(base / 'state'), ephemeral,
                ]).decode().rstrip('\0').split('\0')
                mounts = list(zip(args[::2], args[1::2]))
                expected_project = project != home
                assert len(mounts) == 1 + expected_project, mounts
                if expected_project:
                    assert mounts[0][1].startswith(f'{project}:{project}:'), mounts
                if ephemeral == '1':
                    assert mounts[-1] == ('--tmpfs', f'{guest_home}:512M'), mounts
                else:
                    assert mounts[-1] == ('--mount-dir',
                        f'{base}/state/home:{guest_home}:rw,uid=1000,gid=1000'), mounts
                assert not any(spec.startswith(f'{home}:{home}:') for _, spec in mounts)
print('PASS: home-only/project mounts, persistent/ephemeral home, spaces and symlinked home')
