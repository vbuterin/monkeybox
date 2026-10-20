# Tests

From the repository root:

```sh
nix flake check             # pure policy tests; no KVM, desktop or live accounts
nix build .#test-fixture    # minimal private-runtime test package
python3 tests/mbox-exit-status.py ./result/bin/mbox
python3 tests/mbox-multifile-runtime.py ./result/bin/mbox
python3 tests/mbox-clipboard-runtime.py ./result/bin/mbox
MBOX_TEST_COMBINED_KEYS=1 python3 tests/mbox-clipboard-runtime.py ./result/bin/mbox
python3 tests/mbox-home-metadata.py  # host temporary FS needs user xattrs
```

Runtime tests require KVM access. Their compositors, HOME, runtime sockets,
clipboard and project data are private fixtures, not your live desktop. Test
tools resolve from this flake's pinned legacyPackages.x86_64-linux.

The full desktop suite additionally takes Sway, PulseAudio and Weston's
presentation-shm sample binaries, plus MBOX_TEST_UNFILTERED_WAYPIPE and
MBOX_TEST_WTYPE. Read its usage first; the wrapper must be `test-fixture`, not
production `mbox` or a raw `msb` binary. It validates its immutable fixture hook
before booting. Noctalia tests are intentionally not part of this project.

## Extraction validation

- Standalone default, desktop preset and test-fixture builds.
- Pure parser, mount assembly, home-project and clipboard policy tests.
- Private VM direct-msb vs headless/visual/nested-clipboard exit codes
  0, 42, 127, 143, plus built-wrapper missing-argument guards.
- Private VM multiple file grants, guest write-through and adjacent-file denial.
- Private Sway/VM combined GTK copy/paste regression passed. The pre-existing
  teardown message `exec session ended without exit event` remains; this is not
  a claim of universal graceful lifecycle behavior.
- Optional NixOS adapter evaluated independently with a minimal configuration.

Logs are retained in `evidence/extraction/`. No live desktop was activated.

The initial pure check attempted xattr tests in the Nix build sandbox, which
returned EOPNOTSUPP. That environment-specific test remains a separate explicit
host test; it is not silently skipped or counted as passing in the pure check.

Non-NixOS runtime deployment, physical cameras, sustained audio/video,
Chromium/Electron and broad lifecycle coverage remain unverified in this
extraction. Historical evidence is documented in ../PROVENANCE.md, not treated
as a rerun of every test after packaging changes.
