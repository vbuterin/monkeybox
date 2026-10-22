# Guest disk swap and memory diagnostics (work in progress)

The current release firmware has **no guest swap support**: a private VM probe
found no `/proc/swaps` or zram control device. Do not enable swap on that firmware.
Adding a userland tool cannot enable a missing kernel feature. Monkeybox now
packages a swap-enabled variant from the exact firmware source gitlink of its
pinned Microsandbox revision: `cf4c22b9f05c680928e6d96a9d198f5845573a87`,
Linux 6.12.109 / libkrunfw 5.6.1. Only `CONFIG_SWAP=y` is changed; the upstream
kernel patches remain intact. The user completed the host firmware build; disposable real-VM pressure tests
passed both as root and after dropping to a non-root guest identity. See [firmware provenance/build notes](../packages/libkrunfw-swap/README.md).

## Prepared interface

- `mbox -s ...` (or `--swap`) opts in to swap and swap-enabled firmware.
  Clusters such as `-savic` work. An application argument after `--` is untouched.
- Without `-s`: baseline release firmware, no swap disk, no guest-root swap wrapper,
  even if `swapMiB` or `--swap-mib` is set. Inherited firmware overrides are ignored.
- Size defaults to 8192 MiB. `programs.mbox.swapMiB` or `--swap-mib N` changes
  size but does not enable swap. Omit `-s` to run without swap.
- `swapFirmwarePath` defaults to the pinned source-built firmware. Both firmware
  outputs are build dependencies, but only `-s` selects the swap variant at runtime.
  Set `swapFirmwarePath = null` to omit that dependency and make `-s` fail explicitly.
  Unsupported/unavailable swap never silently falls back.
- 64–65536 MiB supported; 256 MiB additional virtual-disk headroom for ext4.
- Private `--mount-owned /run/mbox-swap:kind=disk,...` volume, not a host directory
  shared through virtiofs. The volume is stored by msb beneath `MSB_HOME`.
- Guest root creates a 0600, allocated (not sparse) swapfile on that ext4 volume,
  runs fixed `mkswap`/`swapon`, then drops groups/GID/UID before the application.
- Failure to initialize swap aborts startup explicitly; no silent fallback.
- No zram, host swap changes, or host root privileges. Guest swap does not expand
  GPU VRAM. Heavy sustained disk swapping can still make an application freeze.

## Storage and confidentiality

`MSB_HOME` must be on **disk-backed encrypted storage**, outside all guest
host-directory grants. This is also required for ordinary private engine state.
Overriding it to tmpfs defeats disk swap's purpose. Moving it to an unencrypted
partition exposes swapped pages at rest. The draft does not verify the live
host device-mapper topology automatically.

Owned disks are removed on sandbox removal/replacement, not necessarily when
an application exits. Crash leftovers may persist. Deleting files is not secure
erasure on SSDs; encryption at rest matters. Host sparse backing allocation can
still exhaust host disk space; ext4 allocation within the guest is not a host
capacity reservation. Budget disk space for all concurrently running VMs.

For the user's `v-amd` configuration, `modules/nixos/disko.nix` declares an ext4
root inside LUKS `cryptusb`; the ESP at `/boot` is unencrypted. A path on the normal
root/home filesystem is therefore intended to be encrypted. This is a config
inspection, not verification of the running host's mount/device topology.

## Validation status

`python3 tests/mbox-memory.py` covers synthetic guest mount validation, unsupported
kernel behavior, file/symlink refusal, allocation/syscall ordering, size limits,
and identity drop. Swap syscalls are mocked; no host swap is touched.

The management CLI and parent Noctalia UI have separate process, private display
and packaged VM integration tests. The registry covers live launcher descendants,
not pre-upgrade or reparented/orphan runtimes. RSS may double-count shared pages;
it is not a complete host VM memory inventory. Production deployment is separate.

`python3 tests/mbox-swap-package.py` checks the vendored archive hash, version,
configuration and package wiring offline. `tests/mbox-swap-vm.py` is an opt-in
real guest probe using a private engine, no application profiles, and no network
or desktop grants; it requires already-built store paths and disk-backed storage.
PSI and VM_EVENT_COUNTERS remain disabled in this narrowly changed firmware.
Pressure and pswpin/pswpout counters are unavailable; meminfo and swaps work.
The probe therefore requires used swap slots plus >100 major faults when
revisiting anonymous pages, rather than nonexistent global event counters.

Real VM results: 512 MiB RAM / 512 MiB swap, 600 MiB anonymous allocations:
- Root: 165324 KiB swap used; 19493 major faults on page revisits.
- Bootstrap drop to guest UID/GID 1234, groups [1234]: 170456 KiB swap used;
  19478 major faults. The probe executed the actual helper main/identity drop.
Both private engines were removed successfully; no host swap changed.

User-built packaged desktop wrapper and parent Noctalia adapter checks passed.
The packaged wrapper ran both with and without swap on a private Wayland display
and Pulse null sink: guest identity, desktop/audio readiness, diagnostics,
termination and registration cleanup passed. No production session activated.
`tests/mbox-management-desktop.py` accepts only the built test-fixture wrapper
and runs actual desktop/audio readiness, management diagnostics and termination
against a private compositor, null sink and disposable VM.
