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
    # Shell I/O is explicit host clipboard access, independent of focus.
    import io
    from types import SimpleNamespace
    payload = b'line one\nline two\n'
    with patch.object(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(payload))), \
            patch.object(b, 'focused', side_effect=AssertionError('must not inspect focus')), \
            patch.object(b, 'endpoint') as local, patch.object(subprocess, 'Popen'):
        main('write')
        local.assert_called_once_with((None, None, None), 'import', payload)
        output = io.BytesIO()
        with patch.object(sys, 'stdout', SimpleNamespace(buffer=output)):
            main('read')
        assert output.getvalue() == payload and (d/'buffer.json').exists()
    # Copy/store must precede Cut. Reject terminals, guests, empty text and races.
    def cut_key(target, key):
        assert key == 'x'
        assert json.loads((d/'buffer.json').read_text())['text'] == 'fixture text'
    with patch.object(b, 'focused', return_value=host), \
            patch.object(os, 'readlink', return_value='/nix/store/fixture/bin/editor'), \
            patch.object(b, 'copy_selection', return_value=b'fixture text'), \
            patch.object(b, 'endpoint', side_effect=endpoint), \
            patch.object(b, 'application_key', side_effect=cut_key) as key, patch.object(subprocess, 'Popen'):
        main('cut')
        key.assert_called_once_with(host, 'x')
        key.reset_mock()
        with patch.object(b, 'copy_selection', side_effect=RuntimeError('copy failed')):
            refused(lambda: main('cut'))
        key.assert_not_called()
        with patch.object(b, 'copy_selection', return_value=b''):
            refused(lambda: main('cut'))
        key.assert_not_called()
        with patch.object(b, 'endpoint', return_value=b'changed'):
            refused(lambda: main('cut'))
        key.assert_not_called()
        with patch.object(b, 'focused', side_effect=[host, host, (2, host[1], None)]):
            category(20, lambda: main('cut'))
        key.assert_not_called()
        with patch.object(os, 'readlink', return_value='/nix/store/fixture/bin/foot'):
            refused(lambda: main('cut'))
        with patch.object(b, 'focused', return_value=(1, host[1], {'enabled': True})):
            category(21, lambda: main('cut'))
        key.assert_not_called()
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
        assert send.call_args.args[0] == ['@@KEY@@', 'copy-foot']
        b.application_key(host, 'v')
        assert send.call_args.args[0] == ['@@KEY@@', 'paste-foot']
    with patch.object(b, 'focused', return_value=host), patch.object(b, 'bounded') as send, \
            patch.object(os, 'readlink', return_value='/nix/store/fixture/bin/editor'):
        b.application_key(host, 'x')
        assert send.call_args.args[0] == ['@@KEY@@', 'cut']
    # Host GTK uses the same complete-keymap helper as guests. Do not regress
    # to wtype's tiny keymap (or its easily reversed modifier CLI flags).
    with patch.object(b, 'focused', return_value=host), patch.object(b, 'bounded') as send, \
            patch.object(os, 'readlink', return_value='/nix/store/fixture-evince/bin/evince'):
        b.application_key(host, 'c')
        assert send.call_args.args[0] == ['@@KEY@@', 'copy']
        b.application_key(host, 'v')
        assert send.call_args.args[0] == ['@@KEY@@', 'paste']
    # Text MIME negotiation accepts aliases, never arbitrary binary formats.
    with patch.object(b, 'bounded', return_value=b'plain text') as read:
        assert b.endpoint(host, 'export') == b'plain text'
        assert read.call_args.args[0] == ['@@WL_PASTE@@', '--no-newline', '--type', 'text']
    guest_target = (1, os.getpid(), {'sandbox': 'fixture'})
    with patch.object(b, 'focused', return_value=guest_target), patch.object(b, 'bounded') as send, \
            patch.object(b, 'endpoint') as guest_endpoint:
        b.application_key(guest_target, 'c')
        guest_endpoint.assert_called_once_with(guest_target, 'key-copy')
        b.application_key(guest_target, 'v')
        assert guest_endpoint.call_args.args == (guest_target, 'key-paste')
        send.assert_not_called()  # Never inject guest editing keys on the host.
print('CLIPBOARD_POLICY_OK: host Foot semantic keys,  copy/paste sequencing, stale-copy invalidation,  bounds, encoding, expiry, paste persistence, focus change, stale PID, disabled/unregistered proxy')
