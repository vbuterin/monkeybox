#!/usr/bin/env python3
"""Private HOME tests: profiles, first-use creation, RC and font discovery."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('resources', root / 'programs/mbox/resources.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
template = root / 'programs/mbox/default.mboxrc'
source = (root / 'programs/mbox/mbox.sh.in').read_text()
with tempfile.TemporaryDirectory(prefix='mbox-resources-') as tmp:
    home = Path(tmp)
    with patch.dict(os.environ, {'HOME': tmp, 'XDG_CONFIG_HOME': tmp + '/config', 'ZDOTDIR': tmp + '/zsh'}):
        config = home / '.mboxrc'
        # Concurrent first use never exposes a partial file or replaces edits.
        processes = [subprocess.Popen(['python3', str(root / 'programs/mbox/resources.py'),
                                      'init', str(config), str(template)]) for _ in range(8)]
        assert all(p.wait() == 0 for p in processes)
        assert config.read_bytes() == template.read_bytes()
        assert config.stat().st_mode & 0o777 == 0o600
        config.write_text('version = 1\n')
        r.initialize_config(config, template)
        assert config.read_text() == 'version = 1\n'
        config.unlink()
        config.symlink_to(home / 'absent')
        r.initialize_config(config, template)
        assert config.is_symlink() and not config.exists()
        config.unlink()
        (home / 'Downloads').mkdir()
        (home / '.agents/skills').mkdir(parents=True)
        (home / '.pi/agent').mkdir(parents=True)
        (home / '.pi/agent/models.json').write_text('{}')
        firefox = r.profile(template, '/bin/firefox')
        pi = r.profile(template, 'pi')
        assert str(home / 'Downloads') in firefox and not any('.pi' in x or '.agents' in x for x in firefox)
        assert str(home / '.pi/agent/models.json') in pi and not any('Downloads' in x for x in pi)
        assert r.profile(template, 'unknown') == []
        for invalid in ['flags = ["--", "evil"]', 'flags = ["-m"]',
                        'read_only = ["relative"]', 'typo = true', 'flags = ["--file", "/"]']:
            config.write_text('version = 1\n[programs.pi]\n' + invalid)
            try:
                r.profile(config, 'other')
                raise AssertionError(invalid)
            except ValueError:
                pass
        config.write_text('version = 1\n[programs.pi]\nread_only = ["~/$(touch nope)"]\n')
        assert r.profile(config, 'pi') == []
        assert not (home / 'nope').exists()

        backing = home / 'stored-bashrc'; backing.write_text('# synthetic RC')
        (home / '.bashrc').symlink_to(backing)
        (home / '.bash_history').write_text('secret')
        (home / 'config/fish').mkdir(parents=True)
        (home / 'config/fish/config.fish').write_text('# fish')
        (home / 'config/fish/fish_variables').write_text('secret')
        (home / 'zsh').mkdir(); (home / 'zsh/.zshrc').write_text('# zsh')
        rc = r.shell_rc()
        assert f'{backing}:{home}/.bashrc:ro,nodev,nosuid' in rc
        assert any(f'{home}/.config/fish/config.fish:' in x for x in rc)
        assert any(f'{home}/.zshrc:' in x for x in rc)
        assert not any('history' in x or 'fish_variables' in x for x in rc)

        font = home / 'font.ttf'; font.write_bytes(b'font fixture')
        private = home / 'not-a-font'; private.write_text('not copied')
        fake_fc = home / 'fc-list'
        fake_fc.write_text('#!/bin/sh\nprintf "%s\\n" "' + str(font) + '" "' + str(private) + '"\n')
        fake_fc.chmod(0o700)
        with patch.object(r, 'bundled_fonts', return_value=[]):
            result = r.fonts('unknown', str(fake_fc), str(home / 'fonts'))
        assert result == ['--mount-dir', f'{home}/fonts:/usr/share/mbox-host-fonts:ro,nodev,nosuid']
        assert [p.read_bytes() for p in (home / 'fonts').iterdir()] == [b'font fixture']
        assert r.bundled_fonts('/bin/sh') == []

        # Real RO mount fragment: preserve symlink destination, reject invalid
        # paths and collisions rather than quietly retaining a writable mount.
        ro = source[source.index('for m in "${EXTRA_RO_MOUNTS[@]}"'):source.index('if [ "$SHELL_RC" = 1 ]')]
        setup = 'MOUNTS=(); EXTRA_RO_MOUNTS=("$@");\n'
        def ro_run(*paths, prefix=''):
            return subprocess.run(['bash', '-eu', '-c', setup + prefix + ro + '\nprintf "%s\\0" "${MOUNTS[@]}"',
                                   'test', *map(str, paths)], capture_output=True)
        assert f'{backing}:{home}/.bashrc:ro,nodev,nosuid'.encode() in ro_run(home / '.bashrc').stdout
        for paths in [(home / 'missing',), ('/dev/null',), (backing, backing)]:
            assert ro_run(*paths).returncode == 2
        assert ro_run(backing, prefix=f'MOUNTS=(--mount-file "{backing}:{backing}:rw");\n').returncode == 2
        bad = home / 'bad:name'; bad.touch()
        assert ro_run(bad).returncode == 2
        # Font setup sits behind both switches; disabled paths must not invoke
        # the deliberately unresolved helper or source any font fragment.
        visual = source[source.index('if [ "$SHARE_STORE" = 1 ]; then\n    MOUNTS+='):source.index('# The project at its own path:')]
        for store, wayland in [('0', '1'), ('1', '0')]:
            subprocess.run(['bash', '-eu', '-c', f'MOUNTS=(); SHARE_STORE={store}; WAYLAND={wayland};\n' + visual], check=True)

        # Execute the real parser/profile re-exec, but stop before any VM setup.
        parser = source[source.index('ORIGINAL_ARGS='):source.index('# Refuse to nest.')]
        apply = source[source.index('# Resolve profile'):source.index('if [ "$DESKTOP_LAYOUT_SET" = 1 ]')]
        fixture = '#!/usr/bin/env bash\nset -eu\n' + parser + apply
        fixture += '\nprintf "%s\\0" "$WAYLAND" "$SHELL_RC" "$PROJECT_DIR" "${CMD[@]}" "${EXTRA_MOUNTS[@]}"\n'
        fixture = fixture.replace('@@ALLOW_TCP@@', '').replace('@@PUBLISH_TCP@@', '')
        fixture = fixture.replace('@@PYTHON@@', 'python3').replace('@@RESOURCES@@', str(root / 'programs/mbox/resources.py'))
        fixture = fixture.replace('@@DEFAULT_CONFIG@@', str(template))
        launcher = home / 'launcher'; launcher.write_text(fixture); launcher.chmod(0o700)
        config.unlink()
        result = subprocess.check_output([str(launcher), '--no-config', '--', 'firefox'])
        assert not config.exists() and result.split(b'\0')[0] == b'0'
        result = subprocess.check_output([str(launcher), '--no-visual', '-b', '--no-shell-rc', '--', 'firefox', '--app-option'])
        values = result.split(b'\0')
        assert values[:5] == [b'0', b'0', os.fsencode(home), b'firefox', b'--app-option'], values
        assert config.exists() and os.fsencode(home / 'Downloads') in values
        result = subprocess.check_output([str(launcher), str(home / 'Downloads'), '--', 'firefox'])
        assert result.split(b'\0')[2] == os.fsencode(home / 'Downloads')
        custom = home / 'custom'; custom.write_text('version = 1\n')
        config.unlink()
        result = subprocess.check_output([str(launcher), '--config', str(custom), '--', 'firefox'])
        assert not config.exists() and result.split(b'\0')[0] == b'0'
        # Full launcher dry-run, private sockets/HOME and synthetic resources;
        # not a sliced parser and never invokes msb or a live desktop.
        import re
        import socket
        tokens = {name: '' for name in re.findall(r'@@([A-Z_]+)@@', source)}
        empty = home / 'empty'; empty.write_text('')
        tokens.update(PYTHON='python3', RESOURCES=str(root / 'programs/mbox/resources.py'),
                      DEFAULT_CONFIG=str(template), FC_LIST=str(fake_fc), FONT_MOUNTS=str(empty),
                      MSB='/bin/false', IMAGE='fixture', BASE_IMAGE_TAG='different',
                      CPUS='2', MEMORY='1024M', NET_MODE='blocked', SHARE_STORE='1',
                      SHARE_PROFILE='0', RUN_AS_CALLER='1', ENV_BIN='/usr',
                      GUEST_BASH='/bin/bash', GUEST_SHELL_STORE='/bin/bash',
                      CACERT=str(empty), HOOK_FILE=str(empty), BASHRC_SYSTEM=str(empty),
                      SWAP_MIB='8192', AGENT_WAIT='100')
        full = source
        for token, value in tokens.items():
            full = full.replace('@@' + token + '@@', value)
        launcher.write_text(full)
        private_socket = socket.socket(socket.AF_UNIX)
        private_socket.bind(str(home / 'wayland'))
        try:
            environment = dict(os.environ, MBOX_SKIP_KVM_CHECK='1',
                               MBOX_STATE_ROOT=str(home / 'state'), MSB_HOME=str(home / 'state/engine'),
                               WAYLAND_DISPLAY=str(home / 'wayland'))
            for key in ('MBOX_SANDBOX', 'MBOX_PROJECT', 'BASH_ENV', 'ENV'):
                environment.pop(key, None)
            result = subprocess.run([str(launcher), '--dry-run', '--no-audio', '-b', '--', 'firefox'],
                                    env=environment, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
            assert '/usr/share/mbox-host-fonts:ro' in result.stdout, result.stdout
            assert f'{backing}:{home}/.bashrc:ro' in result.stdout
            assert '.pi/agent' not in result.stdout and '.agents/skills' not in result.stdout
            assert config.exists()
            result = subprocess.run([str(launcher), '--dry-run', '--no-visual', '--no-audio', '--', 'firefox'],
                                    env=environment, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
            assert '/usr/share/mbox-host-fonts' not in result.stdout
        finally:
            private_socket.close()
print('PASS: atomic config, scoped profiles/overrides, RC/RO mounts, visual fonts, full launcher dry-runs')
