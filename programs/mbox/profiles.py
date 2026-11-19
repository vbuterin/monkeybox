#!/usr/bin/env python3
"""Declarative launch policy. No shell evaluation or command-name heuristics."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import tomllib


# The one declarative flag list: long name -> (cluster letter or None, kind).
# The launcher (mbox.sh.in) implements the same grammar imperatively in its
# option case; tests/mbox-options.py cross-checks the two so they cannot
# drift. Rules may use any of these; interactive-only options (--config,
# --project, --nix-cache, ...) are deliberately absent.
FLAGS = {
    '--visual': ('v', 'switch'), '--clipboard': ('c', 'switch'),
    '--audio': ('a', 'switch'), '--microphone': ('r', 'switch'),
    '--camera': ('l', 'switch'), '--internet': ('i', 'switch'),
    '--host-loopback': (None, 'switch'), '--shell-rc': ('b', 'switch'),
    '--ephemeral': ('e', 'switch'), '--swap': ('s', 'switch'),
    '--app-home': ('d', 'switch'), '--exclusive': ('x', 'switch'),
    '--gpu': ('g', 'switch'),
    '--no-visual': (None, 'switch'), '--no-clipboard': (None, 'switch'),
    '--no-audio': (None, 'switch'), '--no-microphone': (None, 'switch'),
    '--no-camera': (None, 'switch'), '--no-internet': (None, 'switch'),
    '--no-shell-rc': (None, 'switch'),
    '--memory': ('m', 'value'), '--cpus': (None, 'value'),
    '--network': ('n', 'value'), '--allow-port': ('p', 'value'),
    '--expose-port': (None, 'value'), '--desktop-layout': (None, 'value'),
    '--swap-mib': (None, 'value'), '--env': (None, 'value'),
    '--profile': (None, 'value'), '--exclusive-path': (None, 'value'),
    '--persist': ('P', 'value'),
}


def _names(kind):
    names = set()
    for name, (short, flag_kind) in FLAGS.items():
        if flag_kind == kind:
            names.add(name)
            if short:
                names.add('-' + short)
    return names


SWITCHES = _names('switch')
VALUES = _names('value')
# Cluster letter -> long name. Single-dash tokens are walked letter by
# letter; only the launcher's case gives the letters meaning besides this.
SHORT = {letter: name for name, (letter, _) in FLAGS.items() if letter}
# Abbreviations may not shadow any launcher option, even one disallowed in flags.
RESERVED = SWITCHES | VALUES | {'--help', '--config', '--no-config', '--project',
    '--read-only', '--read-write', '--file', '--persist', '--nix-cache', '--image',
    '--camera-device', '--fresh', '--dry-run', '--diagnostics', '--migrate-home', '--optional-file', '--warn', '--previous-home'}


def expand(value):
    # A small explicit variable vocabulary, not general environment interpolation.
    home = str(Path.home())
    variables = {'HOME': home, 'XDG_CONFIG_HOME': os.environ.get('XDG_CONFIG_HOME', home + '/.config'),
                 'XDG_CACHE_HOME': os.environ.get('XDG_CACHE_HOME', home + '/.cache'),
                 'XDG_STATE_HOME': os.environ.get('XDG_STATE_HOME', home + '/.local/state')}
    for key, replacement in variables.items():
        value = value.replace('${' + key + '}', replacement)
    if value == '~' or value.startswith('~/'):
        value = home + value[1:]
    return value


def path(value):
    if not isinstance(value, str) or not value:
        raise ValueError('expected a nonempty path string')
    value = expand(value)
    if not os.path.isabs(value) or any(c in value for c in ':\n\0'):
        raise ValueError(f'expected an absolute path without delimiters: {value!r}')
    return value


def strings(values):
    if not isinstance(values, list) or not all(isinstance(x, str) and '\0' not in x for x in values):
        raise ValueError('expected a list of strings without NUL')
    return values


def validate_flags(flags):
    flags = strings(flags)
    index = 0
    while index < len(flags):
        flag = flags[index]
        if flag in VALUES:
            index += 1
            if index == len(flags) or not flags[index] or flags[index].startswith('-'):
                raise ValueError(f'missing profile flag value: {flag}')
        elif flag not in SWITCHES:
            if flag.startswith('--') or not flag.startswith('-') or len(flag) < 2:
                raise ValueError(f'unsupported profile flag: {flag}')
            for position, letter in enumerate(flag[1:]):
                if letter not in SHORT:
                    raise ValueError(f'unsupported profile flag: -{letter}')
                if FLAGS[SHORT[letter]][1] == 'value' and position != len(flag) - 2:
                    raise ValueError(
                        f'profile flag -{letter} takes a value and must end the cluster: {flag}')
            if FLAGS[SHORT[flag[-1]]][1] == 'value':
                # Legacy TOML entries can still carry raw clusters; a trailing
                # value letter consumes the next token, as the launcher would.
                index += 1
                if index == len(flags) or not flags[index] or flags[index].startswith('-'):
                    raise ValueError(f'missing profile flag value: {flag}')
        index += 1


def parse_lines(text):
    """One program/abbreviation per line; quotes/comments, never shell code."""
    data = {'version': 1, 'programs': {}, 'abbreviations': {}}
    # Resource directives become typed entries; no unrestricted option injection.
    resources = {'-f': 'required_read_write', '--file': 'required_read_write',
                 '--read-write': 'required_read_write', '--optional-file': 'read_write',
                 '--read-only': 'read_only', '--exclusive-path': 'exclusive_paths'}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        name, separator, body = line.partition(':')
        name = name.strip()
        if not separator or not re.fullmatch(r'(?:--)?[A-Za-z0-9_+.@-]+', name):
            raise ValueError(f'mboxrc line {number}: expected name: flags')
        section = 'abbreviations' if name.startswith('--') else 'programs'
        name = name[2:] if section == 'abbreviations' else name
        if name in data[section]:
            raise ValueError(f'mboxrc line {number}: duplicate entry {name}')
        tokens = shlex.split(body, comments=True, posix=True)
        entry = {'flags': []}
        index = 0
        while index < len(tokens):
            token = tokens[index]
            index += 1
            if token == '--':
                if section != 'programs':
                    raise ValueError('abbreviations cannot add application arguments')
                entry['args'] = tokens[index:]
                break
            if token in resources or token in ('--warn', '--previous-home'):
                start = index
                while index < len(tokens) and not tokens[index].startswith('-'):
                    value = tokens[index]
                    if token in resources:
                        entry.setdefault(resources[token], []).append(value)
                    else:
                        entry['warning' if token == '--warn' else 'previous_home'] = value
                    index += 1
                    if token not in ('-f', '--file', '--optional-file', '--read-only'):
                        break
                if index == start:
                    raise ValueError(f'mboxrc line {number}: {token} needs a value')
            else:
                # Single-dash clusters are walked letter by letter here, the
                # same grammar the launcher applies interactively; after this
                # only single letters and --long names remain in 'flags'.
                expanded = (['-' + letter for letter in token[1:]]
                            if token.startswith('-') and not token.startswith('--')
                            and len(token) > 2 else [token])
                entry['flags'].extend(expanded)
                if expanded[-1] in VALUES:
                    if index == len(tokens):
                        raise ValueError(f'mboxrc line {number}: {expanded[-1]} needs a value')
                    entry['flags'].append(tokens[index]); index += 1
        data[section][name] = entry
    return data


def load(config):
    with open(config, 'rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('mboxrc exceeds 64 KiB')
    text = raw.decode()
    significant = next((line.strip() for line in text.splitlines()
                        if line.strip() and not line.lstrip().startswith('#')), '')
    legacy = (significant.startswith('version') and '=' in significant) or significant.startswith('[')
    data = tomllib.loads(text) if legacy else parse_lines(text)
    if set(data) - {'version', 'programs', 'abbreviations'} or data.get('version') != 1:
        raise ValueError('mboxrc requires version = 1, programs and/or abbreviations tables')
    for section in ('programs', 'abbreviations'):
        entries = data.get(section, {})
        if not isinstance(entries, dict):
            raise ValueError(f'{section} must be a table')
        for name, entry in entries.items():
            if section == 'abbreviations' and (not re.fullmatch('[a-z][a-z0-9-]*', name) or '--' + name in RESERVED):
                raise ValueError(f'invalid or reserved abbreviation: {name}')
            allowed = {'flags', 'project', 'read_only', 'read_write', 'required_read_write',
                       'exclusive_paths', 'warning'}
            if section == 'programs':
                allowed |= {'args', 'previous_home'}
            if not isinstance(entry, dict) or set(entry) - allowed:
                raise ValueError(f'invalid {section} entry: {name}')
            if legacy:
                # In version-1 TOML, standalone -e was the environment option.
                entry['flags'] = ['--env' if flag == '-e' else flag
                                  for flag in strings(entry.get('flags', []))]
            validate_flags(entry.get('flags', []))
            strings(entry.get('args', []))
            if 'previous_home' in entry:
                path(entry['previous_home'])
            if entry.get('project', 'cwd') not in ('home', 'cwd'):
                raise ValueError('profile project must be home or cwd')
            if not isinstance(entry.get('warning', ''), str) or '\0' in entry.get('warning', ''):
                raise ValueError('warning must be text without NUL')
            for key in ('read_only', 'read_write', 'required_read_write', 'exclusive_paths'):
                for value in strings(entry.get(key, [])):
                    path(value)
    return data


def entry_flags(entry, project=''):
    result = []
    flags = entry.get('flags', [])
    index = 0
    while index < len(flags):
        flag = flags[index]
        if flag in VALUES:
            result += [flag, expand(flags[index + 1])]
            index += 2
        else:
            result.extend(['-' + c for c in flag[1:]] if flag.startswith('-') and not flag.startswith('--') else [flag])
            index += 1
    if entry.get('project') == 'home':
        result += ['--project', str(Path.home())]  # v1 compatibility, NOT app-home
    effective_project = project or (str(Path.home()) if entry.get('project') == 'home' else os.getcwd())
    app_home = '--app-home' in result or '-d' in result
    for key, option in [('read_only', '--read-only'), ('read_write', '--read-write'),
                        ('required_read_write', '--read-write')]:
        for value in entry.get(key, []):
            expanded = path(value)
            exists = os.path.isfile(expanded) or os.path.isdir(expanded)
            if not exists and key == 'required_read_write':
                raise ValueError(f'required shared path is missing: {expanded}')
            if not app_home and os.path.realpath(expanded) == os.path.realpath(effective_project):
                continue
            if exists:
                result += [option, expanded]
    for value in entry.get('exclusive_paths', []):
        result += ['--exclusive-path', path(value)]
    return result


def profile(config, program, project=''):
    return entry_flags(load(config).get('programs', {}).get(os.path.basename(program), {}), project)


def prepare(config, project, command_count, command_taken, *original):
    """Parser supplied the boundary; never interpret application arguments."""
    count = int(command_count)
    command = list(original[-count:]) if count else []
    prefix = list(original[:-count]) if count else list(original)
    if prefix and prefix[-1] == '--':
        prefix.pop()
    data = load(config)
    entry = data.get('programs', {}).get(os.path.basename(command[0]) if command else '', {})
    defaults = entry_flags(entry, project)
    warnings = [entry.get('warning', '')]
    clean, macros = [], []
    skip_value = False
    variadic = False
    for arg in prefix:
        if skip_value or (variadic and not arg.startswith('-')):
            clean.append(arg)
            skip_value = False
            continue
        variadic = False
        if arg in VALUES | {'--config', '--project', '--read-only', '--read-write', '--image', '--camera-device', '--migrate-home', '-P', '--persist', '-C', '--nix-cache'} or (arg.startswith('-') and not arg.startswith('--') and arg[-1:] in 'pnPCm'):
            skip_value = True
        if arg in ('-f', '--file') or (arg.startswith('-') and not arg.startswith('--') and arg.endswith('f')):
            variadic = True
        name = arg[2:] if arg.startswith('--') else ''
        if name in data.get('abbreviations', {}):
            abbreviation = data['abbreviations'][name]
            macros += entry_flags(abbreviation, project)
            warnings.append(abbreviation.get('warning', ''))
        else:
            clean.append(arg)
    # An abbreviation with host-port grants replaces the program's host-port
    # defaults (e.g. offline must not retain online-only RPC). Explicit CLI
    # grants remain additive. Other resources are additive and deduplicated.
    if any(x in ('-p', '--allow-port') for x in macros):
        filtered, index = [], 0
        while index < len(defaults):
            if defaults[index] in ('-p', '--allow-port'):
                index += 2
            else:
                filtered.append(defaults[index]); index += 1
        defaults = filtered
    # Profiles < abbreviations (in invocation order) < explicit scalar CLI flags.
    result = defaults + macros + clean
    # Skip duplicate declarative grants, but retain explicit CLI conflicts so
    # the launcher can diagnose them rather than silently changing permissions.
    seen, filtered, index = set(), [], 0
    combined = defaults + macros
    while index < len(combined):
        flag = combined[index]
        if flag in ('--read-only', '--read-write', '--exclusive-path', '-p', '--allow-port'):
            pair = (flag, combined[index + 1])
            if pair not in seen:
                filtered.extend(pair); seen.add(pair)
            index += 2
        else:
            filtered.append(flag); index += 1
    result = filtered + clean
    if command and 'previous_home' in entry and '--migrate-home' not in clean:
        profile_id = ''
        for index, flag in enumerate(result[:-1]):
            if flag == '--profile':
                profile_id = result[index + 1]
        state = os.environ.get('MBOX_STATE_ROOT', expand('${XDG_STATE_HOME}/mbox'))
        destination = Path(state) / 'apps' / app_key(command[0], profile_id) / 'home'
        previous = path(entry['previous_home'])
        if not profile_id and os.path.isdir(previous) and not destination.exists():
            raise ValueError('old application home exists at ' + previous +
                             '; stop old instances, then rerun with --migrate-home ' + repr(previous))
    if command:
        result += ['--', command[0]] + [expand(x) for x in entry.get('args', [])] + command[1:]
    elif command_taken == '1':
        result += ['--']
    import sys
    for warning in warnings:
        if warning:
            print('mbox: ' + expand(warning), file=sys.stderr)
    return result


def app_key(program, profile_id):
    name = os.path.basename(program)
    if not name or name in ('.', '..'):
        raise ValueError('--app-home requires a command')
    return hashlib.sha256(json.dumps(['mbox-app-home-v1', name, profile_id],
                                     ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def lock_key(value):
    return hashlib.sha256(os.fsencode(os.path.realpath(value))).hexdigest()
