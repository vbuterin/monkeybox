#!/usr/bin/env python3
"""--diagnostics: one 0600 log per run captures every layer.

Full launcher dry-run against a stub msb, private HOME/state, no VM and no
desktop. Covers log creation and permissions, the header, teed launcher
stdout/stderr (mount dump, session decision, dry-run msb line), the
MBOX_DIAGNOSTICS=1 guest environment argument, and the exit-trap footer with
the msb system log section. Also verifies no logs directory appears without
the flag.
"""
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'programs/mbox/mbox.sh.in').read_text()

with tempfile.TemporaryDirectory(prefix='mbox-diagnostics-') as tmp:
    home = Path(tmp)
    project = home / 'project'; project.mkdir()
    (project / 'data').mkdir()
    config = home / '.mboxrc'
    config.write_text('version = 1\n')
    stub = home / 'msb-stub'
    stub.write_text('''#!/usr/bin/env bash
if [ "$1" = logs ]; then
    echo "stub-system-log-for-$3"
    exit 0
fi
echo "stub-msb-unexpected:$*"
''')
    stub.chmod(0o700)

    tokens = {name: '' for name in re.findall(r'@@([A-Z_]+)@@', source)}
    empty = home / 'empty'; empty.write_text('')
    tokens.update(PYTHON='python3', RESOURCES=str(root / 'programs/mbox/resources.py'),
                  DEFAULT_CONFIG=str(config), MSB=str(stub),
                  FC_LIST=str(empty), FONT_MOUNTS=str(empty),
                  IMAGE='fixture', CPUS='2', MEMORY='1024M', NET_MODE='blocked',
                  SHARE_STORE='1', SHARE_PROFILE='0', RUN_AS_CALLER='1',
                  ENV_BIN='/usr', GUEST_BASH='/bin/bash',
                  GUEST_SHELL_STORE='/bin/bash', CACERT=str(empty),
                  HOOK_FILE=str(empty), BASHRC_SYSTEM=str(empty),
                  SWAP_MIB='8192', AGENT_WAIT='100')
    launcher = home / 'launcher'
    launcher.write_text(source)
    for token, value in tokens.items():
        launcher.write_text(launcher.read_text().replace('@@' + token + '@@', value))
    launcher.chmod(0o700)

    private_socket = socket.socket(socket.AF_UNIX)
    private_socket.bind(str(home / 'wayland'))
    try:
        environment = dict(os.environ, MBOX_SKIP_KVM_CHECK='1',
                           MSB_PATH=str(stub),
                           MBOX_STATE_ROOT=str(home / 'state'),
                           MSB_HOME=str(home / 'state/engine'))
        for key in ('MBOX_SANDBOX', 'MBOX_PROJECT', 'MBOX_DIAGNOSTICS',
                    'BASH_ENV', 'ENV', 'WAYLAND_DISPLAY'):
            environment.pop(key, None)

        # Without the flag: no logs directory is created at all.
        plain = subprocess.run([str(launcher), '--dry-run', '--no-audio', '--', 'true'],
                               cwd=project, env=environment, capture_output=True,
                               text=True, timeout=30)
        assert plain.returncode == 0, plain.stderr
        assert not (home / 'state/logs').exists(), 'diagnostics directory without the flag'
        assert 'diagnostics' not in plain.stderr

        # With the flag: the whole trail lands in one 0600 file.
        result = subprocess.run([str(launcher), '--dry-run', '--no-audio',
                                 '--diagnostics', '--file', str(project / 'data'),
                                 '--', 'true'],
                                cwd=project, env=environment, capture_output=True,
                                text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert 'diagnostics recording to' in result.stderr, result.stderr
        logdir = home / 'state/logs'
        logs = sorted(logdir.glob('diag-*.log'))
        assert len(logs) == 1, f'expected exactly one diagnostics log, got {logs}'
        log = logs[0]
        mode = stat.S_IMODE(log.stat().st_mode)
        assert mode == 0o600, f'diagnostics log must be 0600, got {oct(mode)}'
        text = log.read_text()
        for expected in ('[header] run begins', f'project: {project}',
                         '[mounts]', str(project / 'data'),
                         '[session] net=blocked tty=', 'MBOX_DIAGNOSTICS=1',
                         'msb run ', 'image=fixture',
                         '[msb system log]', f'stub-system-log-for-mbox-',
                         '[end]'):
            assert expected in text, f'missing {expected!r} in diagnostics log:\n{text}'
        # Sections appear in a readable order: header first, msb system log and
        # the closing marker last (the footer is appended after the tees).
        assert text.index('[header] run begins') < text.index('[mounts]') \
            < text.index('[session]') < text.index('[msb system log]') \
            < text.index('[end]'), 'section order'
        print('PASS: diagnostics mode single-file capture')
    finally:
        private_socket.close()
