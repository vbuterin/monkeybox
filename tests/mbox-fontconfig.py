#!/usr/bin/env python3
"""Built fixture hook + real font lookup without /etc/fonts (no VM).

Usage: python3 tests/mbox-fontconfig.py FIXTURE_WRAPPER
Requires bubblewrap/user namespaces. Use the minimal desktop fixture, not a
production hook with arbitrary application setup. Only /nix/store is shared;
no host home, font configuration, credentials, network or desktop is exposed.
This tests the fallback contract, not a custom image boot or hook-bypassing exec.
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

wrapper = Path(sys.argv[1]).read_text()

def assignment(name):
    matches = re.findall(r'^' + name + r'="([^"\n]+)"$', wrapper, re.M)
    assert len(matches) == 1, f'missing/ambiguous built {name}'
    path = Path(matches[0])
    assert path.exists(), path
    return path

hook = assignment('HOOK_FILE')
env = assignment('ENV_BIN')
bash = assignment('GUEST_BASH')
exports = re.findall(r'^\s*export FONTCONFIG_FILE=(/nix/store/[^\s]+)$', hook.read_text(), re.M)
assert len(exports) == 2, 'built hook must provide local-image config and custom-image fallback'
config = Path(exports[1])
assert config.is_file(), config
assert ET.parse(config).getroot().tag == 'fontconfig', config
# Guard the normal command route too, not just a disconnected hook artifact.
assert '. /etc/mbox-shellhook.bash; exec "$@"' in wrapper
assert '--mount-file "$HOOK_FILE:/etc/mbox-shellhook.bash:ro"' in wrapper

bwrap = shutil.which('bwrap')
assert bwrap, 'bwrap required'
base = [bwrap, '--unshare-all', '--die-with-parent', '--new-session', '--clearenv',
        '--ro-bind', '/nix/store', '/nix/store', '--proc', '/proc', '--dev', '/dev',
        '--tmpfs', '/tmp', '--dir', '/etc', '--dir', '/home/fixture',
        '--ro-bind', str(hook), '/etc/mbox-shellhook.bash',
        '--setenv', 'HOME', '/home/fixture', '--setenv', 'PATH', str(env/'bin'),
        '--setenv', 'LC_ALL', 'C', '--chdir', '/home/fixture', str(bash), '-eu', '-c']
script = '''test ! -e /etc/fonts
. /etc/mbox-shellhook.bash
test "$FONTCONFIG_FILE" = "$1"
test -r "$FONTCONFIG_FILE"
# fc-match is a child process: success depends on an exported variable.
font=$(fc-match -f '%{file}' sans)
test -n "$font" && test -r "$font"
printf 'HOOK_FONT_MATCH_OK: %s\\n' "$font"
'''
result = subprocess.run(base + [script, 'font-test', str(config)],
                        capture_output=True, text=True, timeout=30)
assert result.returncode == 0, result.stdout + result.stderr
assert 'HOOK_FONT_MATCH_OK:' in result.stdout and 'DejaVu' in result.stdout, result.stdout
# Local-image fonts use real copied bytes, not a store symlink: exercise separately.
local = Path(exports[0])
assert '/usr/share/mbox-fonts' in local.read_text()
assert not re.search(r'<dir[^>]*>\s*/nix/store/', local.read_text())
assert 'Fontconfig error:' not in result.stderr, result.stderr
print(result.stdout, end='')
# Negative control in an independent empty-home namespace: omitting the hook
# must expose the missing-config error. This fontconfig build can still resolve
# a compiled-in minimal font, so a nonempty fc-match result alone is insufficient.
negative = subprocess.run(base + ['test ! -e /etc/fonts; fc-match -f "%{file}" sans'],
                          capture_output=True, text=True, timeout=30)
assert 'Fontconfig error: Cannot load default config file' in negative.stderr, negative.stderr
print('PASS: built hook export/config, normal command routing, real font lookup '
      'without /etc/fonts, missing-hook negative control (no custom-image VM boot)')
