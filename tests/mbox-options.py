#!/usr/bin/env python3
"""Exercise the actual option parser without starting a VM."""
from pathlib import Path
import subprocess

source = (Path(__file__).resolve().parents[1]/'programs/mbox/mbox.sh.in').read_text()
parser = source.split('DRY_RUN=0\n', 1)[1].split('# Refuse to nest.', 1)[0]
parser = 'CPUS=2\nDRY_RUN=0\n' + parser.replace('@@EXPOSE_TCP@@', '')
parser += '\nprintf "%s\\0" "$CLIPBOARD" "$WAYLAND" "$CPUS" "${CMD[@]}"'
for option in ('-c', '--clipboard'):
    for args in ([ '-v', option, 'firefox'], [option, '-v', '--', 'firefox'],
                 ['-v', '--cpus', '4', option, '--', 'firefox']):
        result = subprocess.check_output(['bash', '-eu', '-c', parser, 'test', *args])
        assert result.split(b'\0')[:-1] == [b'1', b'1', b'4' if '4' in args else b'2', b'firefox']
for args in (['--cpus'], ['--cpus', '-c']):
    result = subprocess.run(['bash', '-eu', '-c', parser, 'test', *args], capture_output=True)
    assert result.returncode == 2 and b'requires a value' in result.stderr
result = subprocess.check_output(['bash', '-eu', '-c', parser, 'test', '--', 'bash', '-c', 'true'])
assert result.split(b'\0')[:-1] == [b'0', b'0', b'2', b'bash', b'-c', b'true']
# Swap is an explicit bounded per-launch override; do not accept shell numbers
# with octal ambiguity, suffixes, overflow, negatives or missing arguments.
swap_parser = 'SWAP_MIB=8192\n' + parser + '\nprintf "ENABLED=%s SWAP=%s" "$SWAP_ENABLED" "$SWAP_MIB"'
for size in ('64', '8192', '65536'):
    result = subprocess.check_output(['bash', '-eu', '-c', swap_parser, 'test', '--swap-mib', size])
    assert result.endswith(('ENABLED=0 SWAP=' + size).encode())
for args in (['--swap-mib'], *(['--swap-mib', value] for value in
             ('0', '-1', '63', '65537', '08', '8G', '999999999999999999999', '$(false)'))):
    result = subprocess.run(['bash', '-eu', '-c', swap_parser, 'test', *args], capture_output=True)
    assert result.returncode == 2
for args in (['-s'], ['--swap'], ['-avics'], ['-savc'], ['--swap-mib', '8192', '-s']):
    result = subprocess.check_output(['bash', '-eu', '-c', swap_parser, 'test', *args])
    assert result.endswith(b'ENABLED=1 SWAP=8192')
for args in ([], ['--', 'echo', '-s'], ['echo', '-s']):
    result = subprocess.check_output(['bash', '-eu', '-c', swap_parser, 'test', *args])
    assert result.endswith(b'ENABLED=0 SWAP=8192')
# Every permutation of the desktop cluster, plus repeated flags and ordering.
import itertools
cluster_parser = parser.replace('"$CLIPBOARD" "$WAYLAND" "$CPUS"',
                                '"$CLIPBOARD" "$WAYLAND" "$AUDIO" "$CPUS"')
for flags in itertools.permutations('avc'):
    result = subprocess.check_output(['bash', '-eu', '-c', cluster_parser, 'test',
                                      '-' + ''.join(flags), '--', 'firefox'])
    assert result.split(b'\0')[:-1] == [b'1', b'1', b'1', b'2', b'firefox']
for args, expected in (
    (['-vc', 'firefox'], [b'1', b'1', b'0', b'2', b'firefox']),
    (['-aavc', '--no-audio', 'firefox'], [b'1', b'1', b'0', b'2', b'firefox']),
    (['--', 'echo', '-awc'], [b'0', b'0', b'0', b'2', b'echo', b'-awc']),
    (['echo', '-awc'], [b'0', b'0', b'0', b'2', b'echo', b'-awc']),
):
    result = subprocess.check_output(['bash', '-eu', '-c', cluster_parser, 'test', *args])
    assert result.split(b'\0')[:-1] == expected, result
for flag in ('-c', '--clipboard', '-avc'):
    result = subprocess.run(['bash', '-eu', '-c', parser, 'test', flag, '4'], capture_output=True)
    assert result.returncode == 2 and b'--clipboard takes no value' in result.stderr
    result = subprocess.check_output(['bash', '-eu', '-c', parser, 'test', flag, '--', '4'])
    assert result.split(b'\0')[-2] == b'4'
# Unknown/value-taking clusters are not partially interpreted.
for flag in ('-avz', '-w', '--wayland', '--volume'):
    result = subprocess.run(['bash', '-eu', '-c', 'usage() { :; };\n' + cluster_parser,
                             'test', flag], capture_output=True)
    assert result.returncode == 2 and b'unknown option' in result.stderr
file_parser = parser + '\nprintf "FILE=%s\\0" "${EXTRA_MOUNTS[@]}"'
for flag in ('-af', '-avf', '-viclaf'):
    result = subprocess.check_output(['bash','-eu','-c',file_parser,'test',flag,'/tmp/music files','--','mplayer','-shuffle','song.mp3'])
    assert result.endswith(b'FILE=/tmp/music files\0')
    assert b'mplayer\0-shuffle\0song.mp3\0' in result
    missing = subprocess.run(['bash','-eu','-c',parser,'test',flag],capture_output=True)
    assert missing.returncode == 2 and b'requires a value' in missing.stderr
network_parser = 'NET_MODE=blocked\n' + parser + '\nprintf "%s" "$NET_MODE"'
for args, mode in [([], b'blocked'), (['-avic'], b'open'),
                   (['-i', '--no-internet'], b'blocked')]:
    result = subprocess.check_output(['bash', '-eu', '-c', network_parser, 'test', *args])
    assert result.endswith(mode)
camera_parser = 'CAMERA=0; CAMERA_DEVICE=/dev/video0\n' + parser + '\nprintf "CAMERA=%s:%s" "$CAMERA" "$CAMERA_DEVICE"'
for args, expected in [(['-avicl'], b'CAMERA=1:/dev/video0'),
                       (['--camera', '--camera-device', '/dev/video2'], b'CAMERA=1:/dev/video2'),
                       (['-l', '--no-camera'], b'CAMERA=0:/dev/video0')]:
    assert subprocess.check_output(['bash','-eu','-c',camera_parser,'test',*args]).endswith(expected)
# All value-taking short options may END a cluster, never occur inside one.
variables = {'p': 'EXPOSE[@]', 'n': 'NET_MODE', 'P': 'EXTRA_PERSIST[@]',
             'f': 'EXTRA_MOUNTS[@]', 'e': 'EXTRA_ENV[@]', 'C': 'NIX_CACHE_PATHS[@]', 'm': 'MEMORY'}
for letter, variable in variables.items():
    probe = parser + '\nprintf "VALUE=%s" "${' + variable + '}"'
    for prefix in ('a', 'iv', 'aail'):
        result = subprocess.check_output(['bash','-eu','-c',probe,'test','-'+prefix+letter,'value','--','command'])
        assert result.endswith(b'VALUE=value'), (letter, result)
    for cluster in ('-'+letter+'a', '-a'+letter+'i', '-'+letter+'m'):
        result = subprocess.run(['bash','-eu','-c',parser,'test',cluster,'value'],capture_output=True)
        assert result.returncode == 2 and b'must be last' in result.stderr
    missing = subprocess.run(['bash','-eu','-c',parser,'test','-ai'+letter],capture_output=True)
    assert missing.returncode == 2 and b'requires a value' in missing.stderr

multi = 'NET_MODE=blocked; MEMORY=unset\n' + file_parser + '\nprintf "MODE=%s MEMORY=%s" "$NET_MODE" "$MEMORY"'
for args in (['-aif','file1.png','file two.png','-m','15000'],
             ['-af','file1.png','file two.png','-im','15000'],
             ['-ai','--file','file1.png','-f','file two.png','-m','15000']):
    result = subprocess.check_output(['bash','-eu','-c',multi,'test',*args,'--','mplayer','-shuffle','-af'])
    assert b'FILE=file1.png\0FILE=file two.png\0' in result
    assert result.endswith(b'MODE=open MEMORY=15000')
    assert b'mplayer\0-shuffle\0-af\0' in result
for args in (['-f'], ['-af','--'], ['--file','-m','15000'], ['-f','one',''],
             ['-afm','15000'], ['-m15000'], ['-aif','one','-im']):
    result = subprocess.run(['bash','-eu','-c',parser,'test',*args],capture_output=True)
    assert result.returncode == 2
result = subprocess.check_output(['bash','-eu','-c',file_parser,'test','-f','one','two'])
assert result.endswith(b'FILE=one\0FILE=two\0')
result = subprocess.check_output(['bash','-eu','-c',file_parser,'test','-f','./-one','./-two','--','echo','-afm'])
assert result.endswith(b'FILE=./-one\0FILE=./-two\0') and b'echo\0-afm\0' in result
# Without -- a command-like token is deliberately another grant, not guessed.
result = subprocess.check_output(['bash','-eu','-c',file_parser,'test','-f','one','evince'])
assert result.endswith(b'FILE=one\0FILE=evince\0')
# The caller's shell, not mbox, expands globs. Only matched files are grants.
import tempfile
with tempfile.TemporaryDirectory() as tmp:
    for name in ('one.pdf','two spaced.pdf','not-pdf.png'):
        Path(tmp, name).touch()
    result = subprocess.check_output(['bash','-eu','-c',
        'probe=$1; shift; exec bash -eu -c "$probe" test -vf "$1"/*.pdf -- evince "$1"/*.pdf',
        'glob',file_parser,tmp])
    assert b'not-pdf.png' not in result
    assert (b'FILE='+str(Path(tmp,'one.pdf')).encode()+b'\0') in result
    assert (b'FILE='+str(Path(tmp,'two spaced.pdf')).encode()+b'\0') in result
audio_parser = parser + '\nprintf "AUDIO=%s MIC=%s" "$AUDIO" "$MICROPHONE"'
for args, expected in (
    ([], b'AUDIO=0 MIC=0'), (['-a'], b'AUDIO=1 MIC=0'),
    (['-r'], b'AUDIO=0 MIC=1'), (['--microphone'], b'AUDIO=0 MIC=1'),
    (['-varicl'], b'AUDIO=1 MIC=1'),
    (['-ar', '--no-audio'], b'AUDIO=0 MIC=1'),
    (['-ar', '--no-microphone'], b'AUDIO=1 MIC=0'),
    (['-r', '--no-microphone', '-a'], b'AUDIO=1 MIC=0'),
    (['--', 'echo', '-r'], b'AUDIO=0 MIC=0'),
):
    assert subprocess.check_output(['bash', '-eu', '-c', audio_parser, 'test', *args]).endswith(expected)
print('PASS: general short clusters, independent audio/microphone flags, variadic file grants/globs, option/command boundaries and invalid/missing arguments')
