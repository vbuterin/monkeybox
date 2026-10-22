#!/usr/bin/env python3
"""Disk-swap setup tests: synthetic guest /proc and mocked swap syscalls only."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('memory', ROOT / 'programs/mbox/guest-memory.py')
memory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory)


class Memory(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('proc/self', 'run/mbox-swap'):
            (self.root / name).mkdir(parents=True)
        (self.root / 'proc/swaps').write_text('Filename Type Size Used Priority\n')
        (self.root / 'proc/self/mountinfo').write_text(
            '25 1 254:2 / /run/mbox-swap rw - ext4 /dev/vdc rw\n')

    def setup_swap(self):
        with patch.object(Path, 'lstat', return_value=type('Stat', (), {
                'st_mode': 0o40700, 'st_uid': 0})()), \
                patch.object(memory.os, 'posix_fallocate') as allocate, \
                patch.object(memory.subprocess, 'run') as run:
            memory.setup(64, '/trusted/bin', self.root)
        return allocate, run

    def test_disk_swap_and_permissions(self):
        allocate, run = self.setup_swap()
        allocate.assert_called_once()
        self.assertEqual(allocate.call_args.args[1:], (0, 64 * 1024**2))
        swap = self.root / 'run/mbox-swap/swapfile'
        self.assertEqual(swap.stat().st_mode & 0o777, 0o600)
        self.assertEqual([c.args[0] for c in run.call_args_list], [
            ['/trusted/bin/mkswap', str(swap)],
            ['/trusted/bin/swapon', '--priority', '10', str(swap)]])

    def test_missing_kernel_support_fails_before_writes(self):
        (self.root / 'proc/swaps').unlink()
        with self.assertRaisesRegex(RuntimeError, 'kernel lacks swap'):
            self.setup_swap()
        self.assertFalse((self.root / 'run/mbox-swap/swapfile').exists())

    def test_reject_shared_or_ram_filesystem(self):
        for fs in ('virtiofs', 'tmpfs', 'overlay'):
            (self.root / 'proc/self/mountinfo').write_text(
                f'25 1 254:2 / /run/mbox-swap rw - {fs} none rw\n')
            with self.assertRaisesRegex(RuntimeError, 'dedicated ext4'):
                self.setup_swap()

    def test_existing_swap_or_symlink_never_truncated(self):
        swap = self.root / 'run/mbox-swap/swapfile'
        swap.write_text('existing')
        with self.assertRaises(FileExistsError):
            self.setup_swap()
        self.assertEqual(swap.read_text(), 'existing')
        swap.unlink()
        target = self.root / 'sentinel'
        target.write_text('preserve')
        swap.symlink_to(target)
        with self.assertRaises(FileExistsError):
            self.setup_swap()
        self.assertEqual(target.read_text(), 'preserve')

    def test_size_bounds(self):
        for size in (0, -1, 63, 65537):
            with self.assertRaises(ValueError):
                memory.setup(size, '/trusted/bin', self.root)

    def test_application_identity_is_dropped_before_exec(self):
        account = type('User', (), dict(pw_uid=1000, pw_gid=100, pw_name='test'))()
        events = []
        with patch.object(sys, 'argv', ['memory', '64', '/tools', '1000:100', '/app', 'arg']), \
                patch.object(os, 'geteuid', return_value=0), \
                patch.object(memory.pwd, 'getpwuid', return_value=account), \
                patch.object(memory, 'setup', side_effect=lambda *a: events.append('swap')), \
                patch.object(os, 'initgroups', side_effect=lambda *a: events.append('groups')), \
                patch.object(os, 'setgid', side_effect=lambda *a: events.append('gid')), \
                patch.object(os, 'setuid', side_effect=lambda *a: events.append('uid')), \
                patch.object(os, 'execv', side_effect=lambda *a: events.append('exec')):
            memory.main()
        self.assertEqual(events, ['swap', 'groups', 'gid', 'uid', 'exec'])

    def test_swap_failure_never_launches_application(self):
        with patch.object(sys, 'argv', ['memory', '64', '/tools', '0:0', '/app']), \
                patch.object(os, 'geteuid', return_value=0), \
                patch.object(memory, 'setup', side_effect=RuntimeError('unsupported')), \
                patch.object(os, 'execv') as execute:
            with self.assertRaises(RuntimeError):
                memory.main()
            execute.assert_not_called()

    def test_firmware_selection_requires_explicit_flag(self):
        source = (ROOT / 'programs/mbox/mbox.sh.in').read_text()
        selection = source.split('# Firmware selection is strictly per invocation,', 1)[1]
        selection = '# Firmware selection is strictly per invocation,' + selection.split('PROJECT="$(realpath', 1)[0]
        firmware = self.root / 'swap-firmware.so'
        firmware.touch()
        for enabled in ('0', '1'):
            env = dict(os.environ, SWAP_ENABLED=enabled, SWAP_FIRMWARE=str(firmware),
                       MSB_LIBKRUNFW_PATH='baseline')
            result = subprocess.check_output(['bash', '-eu', '-c', selection +
                '\nprintf "%s" "$MSB_LIBKRUNFW_PATH"'], env=env)
            self.assertEqual(result.decode(), str(firmware) if enabled == '1' else 'baseline')
        result = subprocess.run(['bash', '-eu', '-c', selection], capture_output=True,
            env=dict(os.environ, SWAP_ENABLED='1', SWAP_FIRMWARE=''))
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'none is configured', result.stderr)
        self.assertIn('export MSB_LIBKRUNFW_PATH="@@LIBKRUNFW@@"', source)
        self.assertIn('if [ "$SWAP_ENABLED" = 1 ]; then\n    USER_ARGS=', source)
        self.assertIn('if [ "$SWAP_ENABLED" = 1 ]; then\n    INNER=', source)

    def test_missing_counters_report_unknown(self):
        counters = memory.snapshot(self.root)
        self.assertEqual(counters['proc/meminfo'], 'unavailable')
        self.assertIn('Filename', counters['proc/swaps'])


if __name__ == '__main__':
    unittest.main()
