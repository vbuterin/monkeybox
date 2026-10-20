"""Resolve runtime-test tools from this checkout's locked Nix configuration."""
import json
import os
from pathlib import Path
import subprocess
import sys


def tool(attribute, relative, fallback):
    repo = Path(__file__).resolve().parents[1]
    expression = (f'(builtins.getFlake {json.dumps(str(repo))}).legacyPackages.'
                  f'x86_64-linux.{attribute}')
    try:
        result = subprocess.run(['nix', 'build', '--impure', '--expr', expression,
                                 '--no-link', '--print-out-paths'],
                                check=True, capture_output=True, text=True, timeout=600)
        outputs = result.stdout.splitlines()
        if len(outputs) != 1:
            raise RuntimeError('expected one package output')
        resolved = Path(outputs[0]) / relative
        if not resolved.is_file():
            raise RuntimeError('package tool missing')
        print(f'TEST_TOOL {attribute}: {resolved}', flush=True)
        return str(resolved)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        if not Path(fallback).is_file():
            raise RuntimeError(f'Cannot resolve {attribute}; fallback also unavailable') from exc
        print(f'WARNING: Nix resolution failed for {attribute}; using historical fallback {fallback}',
              file=sys.stderr, flush=True)
        return fallback
