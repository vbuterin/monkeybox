#!/usr/bin/env python3
"""Private registry/process fixtures only; never enumerate/kill real instances."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
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


GUEST_SNAPSHOT = {
    'MemTotal_KiB': 4194304,        # 4096 MiB
    'MemAvailable_KiB': 1793024,    # used: exactly 2345 MiB
    'SwapTotal_KiB': 8388608,       # 8192 MiB
    'SwapFree_KiB': 8388608,
    'pswpin': 0, 'pswpout': 0, 'oom_kill': None,
}


class Reporting(unittest.TestCase):
    """Label addressing, normalized sizes, guest samples with a short TTL, text table."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = Path(self.tmp.name)
        p = patch.object(m, 'directory', return_value=self.registry)
        p.start()
        self.addCleanup(p.stop)

    def labelled(self, label, pid=None, memory='4096'):
        """One record per real helper process: registry keys are '<pid>-<start>'."""
        if pid is None:
            helper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
            self.addCleanup(helper.wait)
            self.addCleanup(helper.terminate)
            pid = helper.pid
        return m.register(pid, 'mbox-fixture', '/nix/store/fixture/bin/msb',
                          '/private/engine', '/nix/store/firmware.so', memory, f'/app/{label}')

    def test_label_resolves_only_when_unique_and_live(self):
        firefox = self.labelled('firefox')
        self.assertEqual(m.resolve('firefox'), firefox)
        self.assertEqual(m.resolve(firefox), firefox)  # an ID is always itself
        self.labelled('shell')
        self.labelled('shell')
        with self.assertRaisesRegex(ValueError, 'matches several instances'):
            m.resolve('shell')
        with self.assertRaisesRegex(ValueError, 'no instance labelled'):
            m.resolve('nope')
        # A stale record never wins while a live one carries the same label.
        ghost = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        self.addCleanup(ghost.wait)
        self.addCleanup(ghost.terminate)
        ghost_key = self.labelled('firefox', pid=ghost.pid)
        ghost.terminate()
        ghost.wait(timeout=5)
        self.assertEqual(m.resolve('firefox'), firefox)
        # Only the stale match remains: resolve hands back its ID so load()
        # explains that the instance has exited.
        (self.registry / (firefox + '.json')).unlink()
        self.assertEqual(m.resolve('firefox'), ghost_key)
        with self.assertRaisesRegex(RuntimeError, 'exited'):
            m.load(m.resolve('firefox'))

    def test_unregister_accepts_label(self):
        key = self.labelled('firefox')
        out = io.StringIO()
        with patch.object(sys, 'argv', ['mbox-manage', 'unregister', 'firefox']), \
                contextlib.redirect_stdout(out):
            m.main()
        self.assertFalse((self.registry / (key + '.json')).exists())

    def test_memory_limit_normalizes_to_mib(self):
        for text, expected in (('10240', 10240), ('10240M', 10240), ('8G', 8192),
                               ('8192MiB', 8192), ('512m', 512), ('', None), ('lots', None)):
            self.assertEqual(m.memory_limit_mib(text), expected, text)

    def test_guest_fields_are_derived_or_absent(self):
        self.assertEqual(m.guest_fields(GUEST_SNAPSHOT),
                         dict(memory_used_mib=2345, swap_used_mib=0, swap_total_mib=8192))
        self.assertEqual(m.guest_fields({'MemTotal_KiB': 1024, 'SwapTotal_KiB': 0}),
                         dict(memory_used_mib=None, swap_used_mib=None, swap_total_mib=0))
        self.assertEqual(m.guest_fields(None),
                         dict(memory_used_mib=None, swap_used_mib=None, swap_total_mib=None))
        self.assertEqual(m.guest_fields({'MemTotal_KiB': 1024, 'MemAvailable_KiB': 2048})['memory_used_mib'], 0)

    def listing_once(self, calls):
        def query(record):
            calls.append(record['label'])
            return dict(GUEST_SNAPSHOT)
        with patch.object(m, 'query', side_effect=query):
            return m.listing()

    def test_list_samples_guests_once_within_ttl(self):
        self.labelled('firefox')
        calls = []
        first = self.listing_once(calls)
        second = self.listing_once(calls)
        self.assertEqual(calls, ['firefox'], 'guest must be sampled once, then cached')
        for items in (first, second):
            self.assertEqual(items[0]['memory_used_mib'], 2345)
            self.assertEqual(items[0]['memory_limit_mib'], 4096)
            self.assertEqual(items[0]['swap_used_mib'], 0)
            self.assertEqual(items[0]['swap_total_mib'], 8192)
        cache = json.loads((self.registry / m.CACHE_NAME).read_text())['guest-memory']
        captured = cache[first[0]['id']]['captured_unix_seconds']
        cache[first[0]['id']]['captured_unix_seconds'] = captured - m.GUEST_TTL_SECONDS - 1
        (self.registry / m.CACHE_NAME).write_text(json.dumps({'guest-memory': cache}))
        self.listing_once(calls)
        self.assertEqual(calls, ['firefox', 'firefox'], 'expired sample must be refreshed')

    def test_guest_failure_is_reported_not_hidden(self):
        self.labelled('firefox')
        with patch.object(m, 'query', side_effect=RuntimeError('timeout')):
            items = m.listing()
        self.assertIsNone(items[0]['memory_used_mib'])
        self.assertEqual(items[0]['memory_limit_mib'], 4096)
        # A failed sample is also cached briefly, so a booting VM is not hammered.
        with patch.object(m, 'query', side_effect=AssertionError('must stay cached')):
            self.assertEqual(m.listing()[0]['memory_used_mib'], None)

    def test_listing_ignores_cache_and_temp_files(self):
        key = self.labelled('firefox')
        (self.registry / (m.CACHE_NAME)).write_text('{"guest-memory": {}}')
        (self.registry / 'not-an-instance.json').write_text('{}')
        (self.registry / (key + '.json')).chmod(0o600)
        items = self.listing_once([])
        self.assertEqual([item['id'] for item in items], [key])

    def run_cli(self, *argv, query=None):
        """Run the CLI in-process; guest samples default to the healthy fixture."""
        out = io.StringIO()
        if query is None:
            query = dict(GUEST_SNAPSHOT)
        if isinstance(query, BaseException):
            patcher = patch.object(m, 'query', side_effect=query)
        else:
            patcher = patch.object(m, 'query', return_value=query)
        with patcher, patch.object(sys, 'argv', ['mbox-manage', *argv]), contextlib.redirect_stdout(out):
            m.cli()
        return out.getvalue()

    def test_cli_pretty_by_default_and_json_on_flag(self):
        self.labelled('firefox')
        table = self.run_cli('list')
        self.assertIn('ID', table.splitlines()[0])
        self.assertIn('firefox', table)
        self.assertIn('2345 / 4096 MiB', table)
        self.assertIn('0 / 8192 MiB', table)
        self.assertNotIn('[', table)  # not JSON
        payload = json.loads(self.run_cli('list', '--json'))
        self.assertEqual(payload[0]['label'], 'firefox')
        self.assertEqual(payload[0]['memory_used_mib'], 2345)

    def test_cli_diagnostics_pretty_by_default_and_json_on_flag(self):
        key = self.labelled('firefox')
        report = self.run_cli('diagnostics', key)
        self.assertIn('firefox', report.splitlines()[0])
        self.assertIn('Memory limit  4096 MiB', report)
        self.assertIn('Guest memory  2345 MiB used', report)
        self.assertIn('Guest swap    0 / 8192 MiB', report)
        self.assertIn('Saved to      /tmp/diagnostics-', report)
        self.assertNotIn('"instance"', report)  # not JSON
        payload = json.loads(self.run_cli('diagnostics', key, '--json'))
        Path(payload['path']).unlink()
        self.assertEqual(payload['path'], '/tmp/' + Path(payload['path']).name)
        report = json.loads(payload['text'])
        self.assertEqual(report['instance'], key)
        self.assertEqual(report['guest']['MemTotal_KiB'], 4194304)

    def test_cli_diagnostics_pretty_reports_guest_failure_and_swap_disabled(self):
        key = self.labelled('shell', memory='10G')
        report = self.run_cli('diagnostics', key, query=RuntimeError('timeout'))
        self.assertIn('Guest query   ', report)
        self.assertIn('Memory limit  10240 MiB', report)
        disabled = self.run_cli('diagnostics', key, query=dict(GUEST_SNAPSHOT, SwapTotal_KiB=0))
        self.assertIn('Guest swap    not enabled', disabled)

    def test_cli_kill_pretty_by_default_and_json_on_flag(self):
        # The dispatch is what differs per flag; the real termination path has
        # its own process-level test, so the operation is stubbed here.
        key = self.labelled('firefox')
        with patch.object(m, 'kill_instance', return_value=dict(message='Termination requested.')):
            self.assertEqual(self.run_cli('kill', key), 'Termination requested.\n')
            payload = json.loads(self.run_cli('kill', key, '--json'))
        self.assertEqual(payload['message'], 'Termination requested.')

    def test_cli_pretty_reports_absent_guest_data(self):
        self.labelled('firefox')
        table = self.run_cli('list', query=RuntimeError('timeout'))
        self.assertIn('- / 4096 MiB', table)
        self.assertIn('-     ', table)  # swap column unknown, not zero
        # The failed sample was cached for the TTL; drop it to exercise swap=0.
        (self.registry / m.CACHE_NAME).unlink()
        disabled = dict(GUEST_SNAPSHOT, SwapTotal_KiB=0)
        self.assertIn('not enabled', self.run_cli('list', query=disabled))

    def test_cli_errors_carry_their_reason(self):
        self.labelled('shell')
        self.labelled('shell')
        out = io.StringIO()
        with patch.object(sys, 'argv', ['mbox-manage', 'diagnostics', 'shell']), \
                contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as raised:
                m.cli()
        self.assertEqual(raised.exception.code, 1)
        self.assertIn('matches several instances', json.loads(out.getvalue())['error'])

    def test_cli_prints_real_reason_for_exited_instance(self):
        helper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
        self.addCleanup(helper.wait)
        self.addCleanup(helper.terminate)
        key = self.labelled('ghost', pid=helper.pid)
        helper.terminate()
        helper.wait(timeout=5)
        self.assertEqual(m.resolve('ghost'), key)
        out = io.StringIO()
        with patch.object(sys, 'argv', ['mbox-manage', 'diagnostics', 'ghost']), \
                contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit):
                m.cli()
        self.assertEqual(json.loads(out.getvalue())['error'],
                         'instance has exited (or PID was reused)')

    def test_format_columns_stay_aligned(self):
        self.labelled('firefox')
        self.labelled('a-very-long-application-label-here')
        table = self.run_cli('list')
        rows = table.splitlines()
        columns = [line.split('  ') for line in rows]
        for row in columns[1:]:
            self.assertEqual(len([cell for cell in row if cell != '']), 6, row)

    def test_diagnostics_report_carries_mib_limit(self):
        key = self.labelled('firefox', memory='10240M')
        with patch.object(m, 'query', return_value=dict(GUEST_SNAPSHOT)):
            result = m.diagnostics(key)
        Path(result['path']).unlink()
        self.assertEqual(json.loads(result['text'])['memory_limit_mib'], 10240)


if __name__ == '__main__':
    unittest.main()
