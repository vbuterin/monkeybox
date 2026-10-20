#!/usr/bin/env python3
"""Resolver success, explicit fallback, missing-tool refusal (no actual build)."""
from pathlib import Path
import os
import subprocess
import tempfile
from unittest.mock import patch
from mbox_test_tools import tool

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    (root/'bin').mkdir()
    (root/'bin/tool').touch()
    with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, tmp+'\n', '')) as run:
        assert tool('example', 'bin/tool', '/missing') == str(root/'bin/tool')
        assert '--no-link' in run.call_args.args[0]
        with patch.dict(os.environ, {'MBOX_TEST_CONFIGURATION': 'other-host'}):
            tool('example', 'bin/tool', '/missing')
            assert '.legacyPackages.x86_64-linux.example' in run.call_args.args[0][4]
            assert 'nixosConfigurations' not in run.call_args.args[0][4]
    with patch.object(subprocess, 'run', side_effect=FileNotFoundError()):
        assert tool('example', 'bin/tool', str(root/'bin/tool')) == str(root/'bin/tool')
        try:
            tool('example', 'bin/tool', '/missing')
        except RuntimeError:
            pass
        else:
            raise AssertionError('missing tool accepted')
print('TEST_TOOL_RESOLVER_OK')
