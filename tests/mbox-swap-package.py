#!/usr/bin/env python3
"""Offline provenance/configuration checks; does not compile or boot a kernel."""
import hashlib
from pathlib import Path
import os
import subprocess
import shutil
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
pkg = ROOT / 'packages/libkrunfw-swap'
archive = pkg / 'source.tar.gz'
assert hashlib.sha256(archive.read_bytes()).hexdigest() == (
    '41eaf9f417252011c021dba27132f308c487703545697a21bc5f59c2e61f371f')
with tarfile.open(archive, 'r:gz') as tar:
    assert all(m.name.startswith('libkrunfw') and '..' not in Path(m.name).parts
               and not m.issym() and not m.islnk() for m in tar)
    def text(name):
        return tar.extractfile('libkrunfw/' + name).read().decode()
    make = text('Makefile')
    assert 'KERNEL_VERSION = linux-6.12.109\n' in make
    assert 'ABI_VERSION = 5\n' in make
    assert 'FULL_VERSION = 5.6.1\n' in make
    config = text('config-libkrunfw_x86_64')
    assert config.count('# CONFIG_SWAP is not set') == 1
    assert 'CONFIG_EXT4_FS=y\n' in config and 'CONFIG_VIRTIO_BLK=y\n' in config
    assert '# CONFIG_PSI is not set\n' in config
    assert 'curl --fail --location --retry 5 --retry-delay 2 $(KERNEL_REMOTE) -o $(KERNEL_TARBALL)' in make
    assert '$(MAKE) olddefconfig' in make
    assert tar.extractfile('libkrunfw/LICENSE-GPL-2.0-only').read()
recipe = (pkg / 'default.nix').read_text()
assert "--replace-fail '# CONFIG_SWAP is not set' 'CONFIG_SWAP=y'" in recipe
assert "grep -qx 'CONFIG_SWAP=y' linux-6.12.109/.config" in recipe
assert 'sha256-6Bryj1lB2xx/NWFR37qyyleGyL52R099QuowbN60Rc0=' in recipe
assert 'src = ./source.tar.gz;' in recipe
assert '"PREFIX=${placeholder "out"}"' in recipe
# Exercise the actual upstream recursive recipe with a tiny fake kernel target.
# No firmware build, network, host installation or Nix invocation is involved.
recursive_line = next(line for line in make.splitlines()
                      if 'rm -f .version ; $(MAKE) $(MAKEFLAGS)' in line)
with tempfile.TemporaryDirectory(prefix='mbox-make-') as tmp:
    directory = Path(tmp)
    kernel = directory / 'kernel'
    kernel.mkdir()
    (directory / 'Makefile').write_text('KERNEL_SOURCES = kernel\nall:\n' + recursive_line + '\n')
    (kernel / 'Makefile').write_text('all:\n\t@printf "%s\\n" "$(PREFIX)" > prefix.txt\n')
    env = {k: v for k, v in os.environ.items()
           if k not in ('MAKEFLAGS', 'MFLAGS', 'MAKELEVEL', 'MAKEOVERRIDES')}
    def probe(prefix):
        return subprocess.run(['make', '-j2', 'SHELL=' + shutil.which('bash'), 'PREFIX=' + prefix,
                               'LIBDIR_Linux=lib'], cwd=directory, env=env,
                              capture_output=True, text=True)
    broken = probe('$(out)')
    assert broken.returncode != 0, broken.stdout
    output = '/nix/store/00000000000000000000000000000000-swap-fixture'
    fixed = probe(output)
    assert fixed.returncode == 0, fixed.stderr
    assert (kernel / 'prefix.txt').read_text().strip() == output
module = (ROOT / 'modules/nixos/mbox.nix').read_text()
assert 'default = "${pkgs.callPackage ../../packages/libkrunfw-swap { }}/lib/libkrunfw.so";' in module
assert '"${msbPackage}/lib/libkrunfw.so"' in module
assert 'default = 8192;' in module
print('PASS: firmware archive provenance, upstream swap-off config, pinned kernel and opt-in package wiring')
