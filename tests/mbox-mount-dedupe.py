#!/usr/bin/env python3
"""Actual duplicate-mount dedupe; no VM, no Nix evaluation.

Launching mbox inside a directory that is also a resource grant (the
bindReadOnly workspace read from other projects) used to present the same
guest path twice and die in msb's config validation. This exercises the real
mount-assembly fragment including the one-volume-per-guest-path pass.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'programs/mbox/mbox.sh.in').read_text()
assert '# One volume per guest path.' in source, 'dedupe pass missing'
fragment = source.split('# ---- mounts ', 1)[1]
fragment = '# ---- mounts ' + fragment.split('# ---- network ', 1)[0]


def dest_of(flag, arg):
    """Mirror the launcher's per-flag guest-path extraction."""
    if flag in ('--mount-dir', '--mount-file'):
        return arg.split(':', 2)[1]
    if flag in ('--tmpfs', '--mount-owned'):
        return arg.split(':', 1)[0]
    return None


def assemble(bind_fn, bind_ro_fn):
    with tempfile.TemporaryDirectory(prefix='mbox-mount-dedupe-') as tmp:
        base = Path(tmp)
        home = base / 'home'
        home.mkdir()
        project = base / 'project'  # the directory mbox is launched from
        (project / 'sub').mkdir(parents=True)
        models = project / 'models.json'  # a file grant that lives in the project
        models.write_text('{}')
        other = base / 'other-grant'  # an unrelated directory grant
        other.mkdir()
        bind = bind_fn(project, models, other)
        bind_ro = bind_ro_fn(project, models, other)
        plumbing = []
        for name in ('hook', 'passwd', 'group', 'bashrc-system'):
            f = base / name
            f.write_text('')
            plumbing.append(str(f))
        frag = fragment.replace('@@BIND_RO@@', ' '.join('"%s:%s"' % (s, s) for s in bind_ro))
        frag = frag.replace('@@BIND@@', '"%s:%s"' % (bind, bind))
        frag = frag.replace('@@PERSIST@@', '')
        script = '''
HOME=$1; GUEST_HOME=$1; PROJECT=$2; PROJECT_STATE=$3; EPHEMERAL=0
MBOX_UID=1000; MBOX_GID=1000; RUN_AS_CALLER=1; RUN_AS_SPEC=''; DRY_RUN=1
SHARE_STORE=0; NIX_CACHE_DIR=''; SHARE_PROFILE=0; BASHRC=''
EXTRA_PERSIST=(); EXCLUDE=(); EXTRA_MOUNTS=()
HOOK_FILE=$4; PASSWD_FILE=$5; GROUP_FILE=$6; BASHRC_SYSTEM=$7
''' + frag + '\nprintf "%s\\0" "${MOUNTS[@]}"'
        run = subprocess.run(
            ['bash', '-eu', '-c', script, 'test', str(home), str(project),
             str(base / 'state'), *plumbing],
            capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        args = run.stdout.rstrip('\0').split('\0')
        mounts = list(zip(args[::2], args[1::2]))
        dests = [dest_of(f, a) for f, a in mounts]
        return base, project, models, other, mounts, dests, run.stderr


def assert_no_duplicates(dests):
    seen = set()
    for d in dests:
        assert d not in seen, ('duplicate guest mount', d, dests)
        seen.add(d)


# The world_knowledge case: the project itself is also a read-only grant.
base, project, models, other, mounts, dests, err = assemble(
    lambda p, m, o: o, lambda p, m, o: [p, m])
assert_no_duplicates(dests)
project_mounts = [m for m, d in zip(mounts, dests) if d == str(project)]
assert len(project_mounts) == 1, project_mounts
flag, arg = project_mounts[0]
assert flag == '--mount-dir', (flag, arg)
assert arg.startswith('%s:%s:rw,' % (project, project)), arg
assert 'duplicate guest mount for %s' % project in err, err
assert ('--mount-file', '%s:%s:ro' % (models, models)) in mounts, mounts
assert ('--mount-dir', '%s:%s:rw,uid=1000,gid=1000' % (other, other)) in mounts, mounts
assert ('--mount-dir', '%s:%s:rw,uid=1000,gid=1000'
        % (base / 'state/home', base / 'home')) in mounts, mounts
# A read-write grant of the project loses to the project mount the same way.
_, p2, _, _, mounts, dests, err = assemble(lambda p, m, o: p, lambda p, m, o: [o])
assert_no_duplicates(dests)
assert sum(1 for d in dests if d == str(p2)) == 1, dests
assert 'duplicate guest mount for %s' % p2 in err, err
p2_mount = next(m for m, d in zip(mounts, dests) if d == str(p2))
assert p2_mount[1].startswith('%s:%s:rw,' % (p2, p2)), p2_mount
# No collision: every grant survives, nothing is skipped.
_, _, m3, o3, mounts, dests, err = assemble(lambda p, m, o: o, lambda p, m, o: [m])
assert_no_duplicates(dests)
assert err == '', err
assert ('--mount-dir', '%s:%s:rw,uid=1000,gid=1000' % (o3, o3)) in mounts, mounts
assert ('--mount-file', '%s:%s:ro' % (m3, m3)) in mounts, mounts
print('PASS: one volume per guest path; project mount wins over duplicate grants')
