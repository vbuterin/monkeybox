# Swap-enabled firmware for `mbox -s`

This is the **exact firmware source revision** referenced by
Microsandbox `f9f40e1a341922c0f66da36f10914d2aad0ca8be`:

- Repository: https://github.com/superradcompany/libkrunfw
- Commit: `cf4c22b9f05c680928e6d96a9d198f5845573a87`
- Firmware: 5.6.1, ABI 5; Linux 6.12.109
- `source.tar.gz` SHA-256:
  `41eaf9f417252011c021dba27132f308c487703545697a21bc5f59c2e61f371f`

The archive was produced from the user's clean local checkout, without Git
metadata, untracked files, or modifications:

```sh
git -c core.attributesFile=/dev/null archive --format=tar --prefix=libkrunfw/ \
  cf4c22b9f05c680928e6d96a9d198f5845573a87 | gzip -n > source.tar.gz
```

It is vendored so the package is self-contained: it does not depend on a sibling
checkout, a mutable branch, a fetch hash placeholder, or GitHub access. The
upstream license texts remain in the archive. The ordinary non-SEV/non-TDX
x86_64 build uses the kernel bundle; it does not replace the msb embedded agent.

The derivation makes one guest configuration change:

```diff
-# CONFIG_SWAP is not set
+CONFIG_SWAP=y
```

It preserves every upstream kernel patch. PSI and zram remain disabled, so
`/proc/pressure/memory` may still be unavailable. The build checks the effective
swap, ext4 and virtio-block settings after `olddefconfig`, and installs that
configuration under `share/mbox-libkrunfw/kernel.config` for inspection.

The kernel tarball is separately hash-pinned using the Linux 6.12.109 checksum
recorded in the matching Microsandbox source at
`.github/actions/cache-libkrunfw-kernel/action.yml`:
`e81af28f5941db1c7f356151dfbab2ca5786c8be76474f7d42ea306cdeb445cd`.
The upstream Makefile's network download is replaced with a link to that Nix
fixed-output dependency. No network fetch occurs during compilation.

## Deployment and validation

The flake exposes `.#libkrunfw-swap` for a standalone host build, without
installing Monkeybox or the unfinished management/UI work. Build this first.
The default wrapper depends on both firmware outputs, so even users who never
pass `-s` will build/download this output unless they configure
`swapFirmwarePath = null`. Selection at **runtime** is strictly opt-in.

Host command (not executed by the agent):

```sh
cd /home/v/ethereum/nixos-example/monkeybox
nix build .#libkrunfw-swap --out-link result-swap-firmware
```

A successful compilation alone does not establish runtime compatibility. Before
publishing: use the private disposable VM probe in `tests/mbox-swap-vm.py`, verify
swap-in/out, then exercise the desktop stack with disposable profiles. Do not
change the host's swap configuration. The user successfully built this package after fixing recursive-make PREFIX
expansion. Its installed config confirms SWAP, VIRTIO_BLK and EXT4_FS enabled.
Real 512 MiB guest pressure tests passed with 512 MiB swap and 600 MiB
anonymous allocations, both as root and after bootstrap dropped to UID/GID 1234.
Used swap slots and major faults on anonymous page revisits prove swap-out/in;
upstream VM_EVENT_COUNTERS is disabled, so pswpin/pswpout are unavailable.
The user-built desktop test-fixture also passed both with and without swap:
guest identity, private Wayland/audio readiness, diagnostics and termination
cleanup. Full production deployment remains a separate step.
