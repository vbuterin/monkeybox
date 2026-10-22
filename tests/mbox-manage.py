#!/usr/bin/env python3
"""Private registry/process fixtures only; never enumerate/kill real instances."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('manage', Path(__file__).resolve().parents[1] / 'programs/mbox/manage.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Management(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = Path(self.tmp.name)
        p = patch.object(m, 'directory', return_value=self.registry)
        p.start()
        self.addCleanup(p.stop)

    def register(self, pid=None):
        return m.register(pid or os.getpid(), 'mbox-fixture', '/nix/store/fixture/bin/msb',
                          '/private/engine', '/nix/store/firmware.so', '1024M', '/app/firefox')

    def test_registry_permissions_stale_and_symlink(self):
        key = self.register()
        file = self.registry / (key + '.json')
        self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(m.load(key)['label'], 'firefox')
        data = json.loads(file.read_text())
        data['start'] = str(int(data['start']) + 1)
        file.write_text(json.dumps(data))
        with self.assertRaises(RuntimeError):
            m.load(key)
        file.unlink()
        file.symlink_to('/dev/null')
        with self.assertRaises(OSError):
            m.load(key)
        for key in ('../escape', '0-0', '1;echo x', '1-2/3'):
            with self.assertRaises(ValueError):
                m.load(key)

    def test_reused_pid_is_never_signalled(self):
        key = self.register()
        record = m.load(key)
        root = m.process(os.getpid())
        with patch.object(m, 'tree', return_value=[root]), \
                patch.object(m.os, 'pidfd_open', return_value=123456), \
                patch.object(m, 'process', return_value={**root, 'start': 'reused'}), \
                patch.object(m, 'load', return_value=record), \
                patch.object(m.os, 'close') as close, \
                patch.object(m.signal, 'pidfd_send_signal') as send:
            with self.assertRaises(RuntimeError):
                m.kill_instance(key)
            send.assert_not_called()
            close.assert_called_once_with(123456)

    def test_runtime_replacement_discards_query(self):
        record = {'msb': '/nix/store/fixture/bin/msb', 'engine': '/engine',
                  'firmware': '/firmware', 'name': 'mbox-fixture'}
        with patch.object(Path, 'is_file', return_value=True), \
                patch.object(m, 'runtime_identity', side_effect=[(1, 'old', 2, 3), (4, 'new', 2, 5)]), \
                patch.object(m, 'bounded_command', return_value=b'{}'):
            with self.assertRaisesRegex(RuntimeError, 'replaced'):
                m.query(record)

    def test_launcher_cleanup_never_stops_replacement_by_name(self):
        source = (Path(__file__).resolve().parents[1] / 'programs/mbox/mbox.sh.in').read_text()
        cleanup = source.split('cleanup_desktop() {', 1)[1].split('if [ ${#NIX_CACHE_PATHS[@]}', 1)[0]
        cleanup = 'cleanup_desktop() {' + cleanup
        log = self.registry / 'cleanup.log'
        fake = self.registry / 'fake-clipboard'
        fake.write_text('#!/bin/sh\necho clipboard-unregister >> "$LOG"\n')
        fake.chmod(0o700)
        cleanup = cleanup.replace('@@CLIPBOARD@@', str(fake)).replace('@@MANAGE@@', str(fake))
        helpers = []
        for name in ('audio', 'camera', 'waypipe'):
            helpers.append(f'sleep 60 & {name.upper()}_PID=$!; echo ${name.upper()}_PID >> "$LOG.pids"')
        script = '\n'.join(helpers) + '''
AUDIO_RUNTIME=""; CAMERA_RUNTIME=""; DESKTOP_RUNTIME=""
NIX_CACHE_DIR=""; MANAGEMENT_ID="fixture"; RUN_PID=""
cancel_run() { echo UNSAFE-NAME-STOP >> "$LOG"; exit 1; }
''' + cleanup + '\nkill -USR1 $$\n'
        result = subprocess.run(['bash', '-c', script], env=dict(os.environ, LOG=str(log)), timeout=15,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 138, result.stderr)
        self.assertNotIn('UNSAFE', log.read_text())
        self.assertIn('clipboard-unregister', log.read_text())
        for pid in Path(str(log) + '.pids').read_text().split():
            try:
                info = m.process(int(pid))
            except FileNotFoundError:
                continue
            self.assertIn(info['state'], ('Z', 'X', 'x'), info)

    def test_counter_allowlist_excludes_hostile_payload(self):
        data = m.memory_counters({'proc/meminfo': 'MemTotal: 1024 kB\nSwapFree: 0 kB\nsecret: PASSWORD',
                                  'proc/vmstat': 'pswpin 7\noom_kill 2\n', 'clipboard': 'PASSWORD'})
        self.assertEqual(data['MemTotal_KiB'], 1024)
        self.assertEqual(data['pswpin'], 7)
        self.assertIsNone(data['MemAvailable_KiB'])
        self.assertNotIn('PASSWORD', json.dumps(data))
        with self.assertRaises(ValueError):
            m.memory_counters([])

    def command(self, code, **kwargs):
        return m.bounded_command([sys.executable, '-c', code], dict(os.environ), **kwargs)

    def test_live_output_limit_timeout_and_exit(self):
        self.assertEqual(self.command('print("ok")'), b'ok\n')
        with self.assertRaisesRegex(RuntimeError, 'output limit'):
            self.command('import os; os.write(1,b"x"*1000000)', limit=100)
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            self.command('import time; time.sleep(5)', timeout=.1)
        with self.assertRaisesRegex(RuntimeError, 'failed'):
            self.command('raise SystemExit(2)')
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            self.command('import os,time; pid=os.fork(); time.sleep(5) if pid == 0 else None', timeout=.1)

    def test_diagnostics_saved_private_even_when_guest_hangs(self):
        key = self.register()
        with patch.object(m, 'query', side_effect=RuntimeError('timeout')):
            result = m.diagnostics(key)
        file = Path(result['path'])
        try:
            self.assertEqual(file.parent, Path('/tmp'))
            self.assertTrue(file.name.startswith('diagnostics-'))
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(file.read_text(), result['text'])
            self.assertIn('guest_error', json.loads(result['text']))
            self.assertNotIn('/private/engine', result['text'])
        finally:
            file.unlink()

    def test_disposable_tree_kill_preserves_unrelated_process(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
        launcher = subprocess.Popen([sys.executable, '-u', '-c',
            'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]); '
            'print(p.pid,flush=True);time.sleep(60)'], stdout=subprocess.PIPE, text=True)
        child = int(launcher.stdout.readline())
        try:
            key = self.register(launcher.pid)
            self.assertTrue({launcher.pid, child} <= {p['pid'] for p in m.tree(m.load(key))})
            m.kill_instance(key)
            launcher.wait(timeout=5)
            self.assertIsNone(unrelated.poll())
            self.assertNotIn(key, [item['id'] for item in m.listing()])
        finally:
            for p in (launcher, unrelated):
                if p.poll() is None:
                    p.kill()
                p.wait()
            launcher.stdout.close()
            # Child termination was requested through a pinned pidfd by the test.


if __name__ == '__main__':
    unittest.main()
