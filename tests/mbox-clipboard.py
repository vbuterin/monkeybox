#!/usr/bin/env python3
"""Broker policy tests without a real clipboard, compositor, VM or credentials."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('broker', root/'programs/mbox/clipboard.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

with tempfile.TemporaryDirectory(prefix='mbox-clipboard-policy-') as tmp:
    os.environ['XDG_RUNTIME_DIR'] = tmp
    os.environ['SWAYSOCK'] = '/fixture/sway.sock'
    d = b.directory()
    host = (1, os.getpid(), None)
    calls = []
    def endpoint(target, action, data=None):
        calls.append((target, action, data))
        return b'fixture text' if action == 'export' else b''
    def main(action):
        with patch.object(sys, 'argv', ['broker', action]):
            b.main()
    def category(code, fn):
        try:
            fn()
        except b.ClipboardFailure as exc:
            assert exc.code == code
            return
        raise AssertionError('expected categorized failure')
    def refused(fn):
        try:
            fn()
        except (RuntimeError, UnicodeError, FileNotFoundError):
            return
        raise AssertionError('expected refusal')
    # Bound untrusted stdout before decoding; also reject binary payloads.
    refused(lambda: b.bounded([sys.executable, '-c', 'import sys; sys.stdout.buffer.write(b"x"*1100000)']))
    refused(lambda: b.text(b'\xff'))
    refused(lambda: b.text(b'hello\0world'))
    category(22, lambda: b.text(b'x' * (b.LIMIT + 1)))
    category(22, lambda: b.text(b'\xff'))
    with patch.object(b, 'focused', return_value=host), patch.object(b, 'endpoint', side_effect=endpoint), \
            patch.object(subprocess, 'Popen'):
        main('export')
        assert (d/'buffer.json').stat().st_mode & 0o077 == 0
        main('import')
        assert calls[-1][2] == b'fixture text'
        # Persistence: a second guest imports the same buffer without a new Copy.
        main('import')
        assert calls[-1][2] == b'fixture text'
        main('export')
        record = json.loads((d/'buffer.json').read_text())
        record['expires'] = time.monotonic()-1
        (d/'buffer.json').write_text(json.dumps(record))
        count = len(calls)
        refused(lambda: main('import'))
        assert len(calls) == count and not (d/'buffer.json').exists()
        main('export'); main('clear')
        assert not (d/'buffer.json').exists()
    with patch.object(b, 'focused', side_effect=[host, (2, os.getpid(), None)]), \
            patch.object(b, 'endpoint', side_effect=endpoint):
        category(20, lambda: main('export'))
        assert not (d/'buffer.json').exists()
    # Unregistered Waypipe cannot be misclassified as a host application.
    tree = {'nodes': [{'id': 1, 'pid': os.getpid(), 'focused': True}]}
    with patch.object(b, 'bounded', return_value=json.dumps(tree).encode()), \
            patch.object(os, 'readlink', return_value='/nix/store/test-waypipe/bin/waypipe'):
        category(21, b.focused)
    with patch.object(b, 'bounded', return_value=json.dumps(tree).encode()), \
            patch.object(os, 'readlink', return_value='/nix/store/not-waypipe/bin/editor'):
        assert b.focused()[2] is None
    record = dict(start=b.start_time(os.getpid()), enabled=False, sway='/fixture/sway.sock')
    (d/f'session-{os.getpid()}.json').write_text(json.dumps(record))
    with patch.object(b, 'bounded', return_value=json.dumps(tree).encode()):
        refused(b.focused)
        record['enabled'] = True
        record['start'] = 'wrong-start-time'
        (d/f'session-{os.getpid()}.json').write_text(json.dumps(record))
        refused(b.focused)
    calls.clear()
    with patch.object(b, 'focused', return_value=host), patch.object(b, 'endpoint', side_effect=endpoint), \
            patch.object(b, 'application_key') as key, patch.object(subprocess, 'Popen'):
        main('copy')
        assert [c[1] for c in calls] == ['clear', 'export']
        key.assert_called_once_with(host, 'c')
        main('paste')
        assert calls[-1][1:] == ('import', b'fixture text')
        assert key.call_args.args == (host, 'v')
        # Paste never consumes the transfer buffer: one Copy can feed several
        # guests until it expires or an explicit clear removes it.
        assert (d/'buffer.json').exists()
        main('paste')
        assert calls[-1][1:] == ('import', b'fixture text')
        assert (d/'buffer.json').exists()
    # A failed Copy cannot export the old buffer.
    with patch.object(b, 'focused', return_value=host), patch.object(b, 'endpoint', side_effect=endpoint), \
            patch.object(b, 'application_key', side_effect=RuntimeError('focus changed')):
        refused(lambda: main('copy'))
        assert not (d/'buffer.json').exists()
    # Host Foot gets semantic clipboard keys, never Ctrl+C (SIGINT) or the
    # globally bound Ctrl+Shift+C/V. A guest title cannot select this path.
    with patch.object(b, 'focused', return_value=host), patch.object(b, 'bounded') as send, \
            patch.object(os, 'readlink', return_value='/nix/store/fixture-foot/bin/foot'):
        b.application_key(host, 'c')
        assert send.call_args.args[0] == ['@@WTYPE@@', '-m', 'shift', '-m', 'ctrl', '-k', 'XF86Copy']
        b.application_key(host, 'v')
        assert send.call_args.args[0][-1] == 'XF86Paste'
    # Other native host apps get the app's own chord with Ctrl held at the
    # keypress (an earlier chord released ctrl first, so they saw a bare
    # Shift+C and no copy ever arrived). Bindings match per keyboard device,
    # so this never recurses into the global Ctrl+Shift+C/V release binding.
    with patch.object(b, 'focused', return_value=host), patch.object(b, 'bounded') as send, \
            patch.object(os, 'readlink', return_value='/nix/store/fixture-evince/bin/evince'):
        b.application_key(host, 'c')
        assert send.call_args.args[0] == ['@@WTYPE@@', '-m', 'ctrl', '-k', 'c', '-M', 'ctrl']
        b.application_key(host, 'v')
        assert send.call_args.args[0] == ['@@WTYPE@@', '-m', 'ctrl', '-k', 'v', '-M', 'ctrl']
    guest_target = (1, os.getpid(), {'sandbox': 'fixture'})
    with patch.object(b, 'focused', return_value=guest_target), patch.object(b, 'bounded') as send, \
            patch.object(b, 'endpoint') as guest_endpoint:
        b.application_key(guest_target, 'c')
        guest_endpoint.assert_called_once_with(guest_target, 'key-copy')
        b.application_key(guest_target, 'v')
        assert guest_endpoint.call_args.args == (guest_target, 'key-paste')
        send.assert_not_called()  # Never inject guest editing keys on the host.
print('CLIPBOARD_POLICY_OK: host Foot semantic keys,  copy/paste sequencing, stale-copy invalidation,  bounds, encoding, expiry, paste persistence, focus change, stale PID, disabled/unregistered proxy')
