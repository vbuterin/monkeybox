#!/usr/bin/env python3
"""Execute same-path mount assembly, without starting a VM."""
from pathlib import Path
import subprocess
import tempfile

source = (Path(__file__).resolve().parents[1]/'programs/mbox/mbox.sh.in').read_text()
fragment = source.split('# Explicit same-path grants.', 1)[1].split("\n# The caller's own bashrc", 1)[0]
# Include comment marker removed by split.
fragment = '# Explicit same-path grants.' + fragment
with tempfile.TemporaryDirectory(prefix='mbox-files-') as temp:
    root = Path(temp)
    file = root/'file with spaces'; file.write_text('fixture')
    directory = root/'directory'; directory.mkdir()
    link = root/'link'; link.symlink_to(file)
    colon = root/'bad:name'; colon.touch()
    setup = 'MOUNTS=(); HOME_MOUNT=(); MBOX_UID=1000; MBOX_GID=100; EXTRA_MOUNTS=("$@");\n'
    def run(*args, prefix=''):
        return subprocess.run(['bash', '-eu', '-c', setup+prefix+fragment+'\nprintf "%s\\n" "${MOUNTS[@]}"',
                               'test', *map(str,args)], capture_output=True, text=True)
    result = run(file, directory)
    assert result.returncode == 0, result.stderr
    assert f'{file}:{file}:rw' in result.stdout and '--mount-file' in result.stdout
    assert f'{directory}:{directory}:rw,nodev,nosuid,uid=1000,gid=100' in result.stdout
    assert str(file) in run(link).stdout
    for args in [(root/'missing',), (colon,), ('/dev/null',), (file,file)]:
        assert run(*args).returncode == 2, args
    assert run(directory, prefix=f'HOME_MOUNT=(--mount-dir "{directory}:{directory}:rw");\n').returncode == 2
    # Real parser -> real mount assembly: variadic grants are not just parsed
    # but become individual same-path file/directory mounts.
    parser = 'DRY_RUN=0\n' + source.split('DRY_RUN=0\n',1)[1].split('# Refuse to nest.',1)[0].replace('@@EXPOSE_TCP@@','')
    combined = parser + '\nMOUNTS=(); HOME_MOUNT=(); MBOX_UID=1000; MBOX_GID=100;\n' + fragment
    combined += '\nprintf "%s\\n" "${MOUNTS[@]}" "COMMAND=${CMD[*]}" "MEMORY=$MEMORY" "NETWORK=$NET_MODE"'
    result = subprocess.run(['bash','-eu','-c',combined,'test','-af',str(file),str(directory),'-im','15000','--','mplayer','-shuffle',str(file)],capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    assert f'{file}:{file}:rw' in result.stdout
    assert f'{directory}:{directory}:rw,nodev,nosuid,uid=1000,gid=100' in result.stdout
    assert 'MEMORY=15000' in result.stdout and 'NETWORK=open' in result.stdout
    assert f'COMMAND=mplayer -shuffle {file}' in result.stdout
print('PASS: file/dir same-path mounts, spaces, symlink canonicalization, invalid/duplicate paths')
