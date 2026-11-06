#!/usr/bin/env python3
"""Host-side declarative application profiles and bounded resource discovery.

Never source user configuration or execute a discovered application. Stdout is
NUL-delimited argv; diagnostics go to stderr. No shell evaluation/expansion.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import runpy

# Resolve explicitly: the installed helper runs with Python isolated mode (-I).
policy = runpy.run_path(str(Path(__file__).with_name('profiles.py')))


def emit(values):
    for value in values:
        if '\0' in value:
            raise ValueError('NUL in argument')
        sys.stdout.buffer.write(os.fsencode(value) + b'\0')


def path(value):
    if not isinstance(value, str) or not value:
        raise ValueError('expected a nonempty path string')
    result = os.path.expanduser(value)
    if not os.path.isabs(result) or any(c in result for c in ':\n\0'):
        raise ValueError(f'expected an absolute path without delimiters: {value!r}')
    return result


def initialize_config(destination, template):
    """Publish a complete template atomically, never replace an existing entry."""
    import tempfile
    destination = Path(destination)
    if os.path.lexists(destination):
        return
    fd, temporary = tempfile.mkstemp(prefix='.mboxrc-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as output, open(template, 'rb') as source:
            shutil.copyfileobj(source, output)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            pass  # Concurrent launcher or user owns the existing entry.
    finally:
        os.unlink(temporary)


profile = policy['profile']
validate_flags = policy['validate_flags']


def shell_rc():
    home = str(Path.home())
    config = path(os.environ.get('XDG_CONFIG_HOME', home + '/.config'))
    zdir = path(os.environ.get('ZDOTDIR', home))
    # Only files/directories conventionally containing startup code. No history,
    # credentials, fish_variables or whole config trees. Symlink source resolves
    # but its logical destination is retained.
    candidates = [(home + '/' + name, home + '/' + name) for name in
                  ('.profile', '.shrc', '.bashrc', '.bash_profile', '.bash_login', '.bash_logout')]
    candidates += [(zdir + '/' + name, home + '/' + name) for name in
                   ('.zshenv', '.zprofile', '.zshrc', '.zlogin', '.zlogout')]
    candidates += [(config + '/fish/' + name, home + '/.config/fish/' + name) for name in
                   ('config.fish', 'conf.d', 'functions', 'completions')]
    result = []
    for source, destination in candidates:
        if os.path.isfile(source) or os.path.isdir(source):
            result += mount(source, destination)
    return result


def mount(source, destination):
    source = path(os.path.realpath(source))
    destination = path(destination)
    return ['--mount-dir' if os.path.isdir(source) else '--mount-file',
            f'{source}:{destination}:ro,nodev,nosuid']


def bundled_fonts(program):
    executable = shutil.which(program) if program else None
    if not executable:
        return []
    resolved = Path(executable).resolve()
    # This launcher shares Nix packages. Never scan arbitrary project trees,
    # HOME, the whole store or transitive package dependencies.
    parts = resolved.parts
    if len(parts) < 5 or parts[1:3] != ('nix', 'store'):
        return []
    root = Path(*parts[:4])
    found = bundled_font_dirs(root)
    if found is None:
        # Bundled fonts are a convenience on top of the host fontconfig fonts
        # that fonts() already mounts. A package root too large to scan (for
        # example a node_modules monorepo) must not block launching the app.
        print(f'mbox: skipping bundled-font discovery for {root}: '
              f'exceeded 20000 entries', file=sys.stderr)
        return []
    return found


def bundled_font_dirs(root, budget=20000):
    """Directories under the package root holding bundled font files.

    Returns None when the tree exceeds the entry budget: the answer is then
    "unknown", and callers must degrade rather than report a partial scan,
    because os.walk order would make the mounted set vary between runs.
    """
    found = set()
    for directory, dirs, files in os.walk(root, followlinks=False):
        budget -= len(dirs) + len(files) + 1
        if budget < 0:
            return None
        dirs[:] = sorted(d for d in dirs if not Path(directory, d).is_symlink())
        if any(Path(f).suffix.lower() in ('.ttf', '.otf', '.ttc', '.otc', '.woff', '.woff2') for f in files):
            found.add(directory)
    return sorted(found)


def fonts(program, fc_list, target):
    # Host fontconfig discovers installed fonts across distributions. Query data
    # only; do not copy its arbitrary configuration, caches or parent directories.
    reply = subprocess.run([fc_list, '--format', '%{file}\\n'], capture_output=True,
                           timeout=15, check=True)
    if len(reply.stdout) > 8 * 1024 * 1024:
        raise ValueError('font discovery output too large')
    paths = set(os.fsdecode(reply.stdout).splitlines())
    if len(paths) > 8192:
        raise ValueError('font discovery exceeded 8192 fonts')
    # Runtime imports copy only font bytes to a private temporary directory.
    # This avoids thousands of virtio mounts and the aggregate-store statfs bug.
    target = Path(target)
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    total = 0
    for index, value in enumerate(sorted(paths)):
        source = Path(value)
        if not source.is_file() or source.suffix.lower() not in ('.ttf', '.otf', '.ttc', '.otc', '.pfa', '.pfb', '.pcf', '.gz'):
            continue
        size = source.stat().st_size
        total += size
        if size > 128 * 1024 * 1024 or total > 512 * 1024 * 1024:
            raise ValueError('discovered fonts exceed 512 MiB total / 128 MiB per file')
        shutil.copyfile(source, target / f'{index}{source.suffix}')
    result = mount(str(target), '/usr/share/mbox-host-fonts')
    for directory in bundled_fonts(program):
        result += mount(directory, directory)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['init', 'profile', 'prepare', 'app-key', 'lock-key', 'shell-rc', 'fonts'])
    parser.add_argument('arguments', nargs='*')
    args = parser.parse_args()
    try:
        if args.operation == 'init':
            initialize_config(*args.arguments)
            result = []
        elif args.operation == 'profile':
            result = policy['profile'](*args.arguments)
        elif args.operation == 'prepare':
            result = policy['prepare'](*args.arguments)
        elif args.operation == 'app-key':
            result = [policy['app_key'](*args.arguments)]
        elif args.operation == 'lock-key':
            result = [policy['lock_key'](*args.arguments)]
        elif args.operation == 'shell-rc':
            result = shell_rc()
        else:
            program, fc_list, target = args.arguments
            # Target is passed explicitly; no guest-provided paths are consulted.
            result = fonts(program, fc_list, target)
        emit(result)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        print(f'mbox: {error}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
