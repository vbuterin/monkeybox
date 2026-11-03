#!/usr/bin/env python3
"""Real parser/re-exec, app identity, private locks and ephemeral lifecycle."""
import os
from pathlib import Path
import runpy
import signal
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
p = runpy.run_path(str(root / 'programs/mbox/profiles.py'))
source = (root / 'programs/mbox/mbox.sh.in').read_text()
with tempfile.TemporaryDirectory(prefix='mbox-app-policy-') as tmp:
    home = Path(tmp)
    project = home / 'project'; project.mkdir()
    shared = home / 'shared'; shared.mkdir()
    config = home / '.mboxrc'
    config.write_text('''# Compact policy; old TOML is tested by mbox-resources.py.
browser: -dx -m 8192 -- --fixed
agent: -i -p 8545
--offline: --network blocked -p 8000
--online: --network open
''')
    preamble = '''#!/usr/bin/env bash
set -euo pipefail
STATE_ROOT="$HOME/state"; GUEST_HOME="$HOME"; CPUS=2; MEMORY=1024; NET_MODE=blocked
RUN_AS_CALLER=1; RUN_AS_SPEC=''; SWAP_FIRMWARE=''
usage() { :; }
'''
    parser = source[source.index('ORIGINAL_ARGS='):source.index('# Runs msb,')]
    fixture = preamble + parser + '''
printf '%s\\n' "$PROJECT" "$PROJECT_STATE" "$NET_MODE" "$MEMORY"
printf 'CMD=%s\\n' "${CMD[@]}"
printf 'PORT=%s\\n' "${ALLOW[@]}"
if [ "${HOLD:-0}" = 1 ]; then touch "$HOME/ready"; sleep 30; fi
'''
    for token, value in {'ALLOW_TCP':'', 'PUBLISH_TCP':'', 'PYTHON':'python3',
                         'RESOURCES':str(root / 'programs/mbox/resources.py'),
                         'DEFAULT_CONFIG':str(config)}.items():
        fixture = fixture.replace('@@'+token+'@@', value)
    launcher = home / 'launcher'; launcher.write_text(fixture); launcher.chmod(0o700)
    env = dict(os.environ, HOME=str(home), XDG_STATE_HOME=str(home / 'state-base'))
    env.pop('MBOX_SANDBOX', None)
    def run(*args, ok=True):
        result = subprocess.run([str(launcher), *args], cwd=project, env=env,
                                capture_output=True, text=True, timeout=10)
        assert (result.returncode == 0) == ok, (args, result.stdout, result.stderr)
        return result.stdout
    # Invalid and duplicate rules fail globally; no shell execution.
    for text in ('browser: -d\nbrowser: -x', '--help: -i', 'agent: --bogus',
                 '--online: -- echo', 'agent: -m', 'agent: -f'):
        bad = home / 'bad'; bad.write_text(text)
        try:
            p['load'](bad)
        except ValueError:
            pass
        else:
            raise AssertionError(text)
    quoted = home / 'quoted'
    quoted.write_text('agent: --env "VALUE=$(touch /tmp/mbox-should-not-execute)" -- "#literal"\n')
    data = p['load'](quoted)['programs']['agent']
    assert data['args'] == ['#literal'] and data['flags'][1].startswith('VALUE=$(')
    a = run('browser', '--online')
    assert str(home / 'state/apps' / p['app_key']('browser', '')) in a
    assert 'CMD=--online' in a  # app argument, not an abbreviation
    assert 'CMD=--fixed' in a
    assert run('--profile', 'work', 'browser') != a
    assert p['app_key']('/nix/store/old/bin/browser', '') == p['app_key']('/usr/bin/browser', '')
    assert p['app_key']('browser', 'a/b') != p['app_key']('browser', 'ab')
    run('--profile', 'work', 'agent', ok=False)
    run('-de', 'agent', ok=False)
    run('-e', 'browser', ok=False)  # profile supplies -d
    run('--unknown', 'agent', ok=False)
    run('--online', '--no-config', 'agent', ok=False)
    offline = run('--offline', 'agent')
    assert 'blocked' in offline and 'PORT=8000' in offline and 'PORT=8545' not in offline
    assert 'open' in run('--offline', '--network', 'open', 'agent')
    assert 'open' in run('--online')  # shell, no command
    assert 'CMD=--online' in run('--', 'browser', '--online')
    run('--no-config', '-e', '--env', 'TEST=yes', 'agent')
    run('-ed', 'agent', ok=False)
    run('--profile', ok=False)
    run('--exclusive-path', ok=False)
    run('--env', '--online', 'agent', ok=False)  # missing flag value, not a macro
    run('-dx', '--exclusive-path', str(shared), '--exclusive-path', str(shared), 'other')
    # Actual flock contention across independent processes and canonical aliases.
    holder = subprocess.Popen([str(launcher), '-dx', '--exclusive-path', str(shared), 'other'],
                              cwd=project, env=dict(env, HOLD='1'), stdout=subprocess.DEVNULL,
                              start_new_session=True)
    try:
        import time
        for _ in range(100):
            if (home / 'ready').exists(): break
            time.sleep(.02)
        assert (home / 'ready').exists()
        run('-dx', 'other', ok=False)
        link = home / 'link'; link.symlink_to(shared)
        run('-d', '--profile', 'different', '--exclusive-path', str(link), 'other', ok=False)
        run('-dx', '--profile', 'different', 'other')
    finally:
        # The child inherits the lock, intentionally: kill/reap both.
        os.killpg(holder.pid, signal.SIGTERM)
        holder.wait(timeout=5)
    run('-dx', 'other')
    # Migration preserves source and refuses existing destinations.
    old = home / 'old'; old.mkdir(); (old / 'sentinel').write_text('keep')
    run('-d', '--migrate-home', str(old), 'migrated')
    dest = home / 'state/apps' / p['app_key']('migrated', '') / 'home'
    assert (dest / 'sentinel').read_text() == 'keep' and (old / 'sentinel').exists()
    run('-d', '--migrate-home', str(old), 'migrated', ok=False)
    # Execute actual ephemeral assembly and cleanup, independent random homes.
    assembly = source[source.index('# Ephemeral uses'):source.index('# Persisted paths usually')]
    cleanup = source[source.index('cleanup_nix_cache()'):source.index('# Install once,')]
    script = '''set -eu
EPHEMERAL=1; DRY_RUN=0; GUEST_HOME="$HOME"; MBOX_UID=1000; MBOX_GID=1000
EPHEMERAL_HOME=''; RESOURCE_ARGS=''; FONT_RUNTIME=''; NIX_CACHE_DIR=''; MOUNTS=()
''' + cleanup + '\ntrap cleanup_nix_cache EXIT\n' + assembly + '''
printf '%s\\n' "$EPHEMERAL_HOME"
test "$(stat -c %a "$EPHEMERAL_HOME")" = 700
touch "$EPHEMERAL_HOME/sentinel"
'''
    first = subprocess.check_output(['bash', '-c', script], env=env, text=True).strip()
    second = subprocess.check_output(['bash', '-c', script], env=env, text=True).strip()
    assert first != second and not Path(first).exists() and not Path(second).exists()
print('PASS: app policy, profiles, abbreviations, contention/release, migration, ephemeral cleanup')
