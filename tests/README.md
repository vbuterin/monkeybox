# Tests

From the repository root:

```sh
nix flake check             # evaluate flake outputs and available checks
nix build .#checks.x86_64-linux.policy --no-link  # explicit policy build gate
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

The restored full desktop suite includes `wayland-registry-probe.py` and additionally takes Sway, PulseAudio and Weston's
presentation-shm sample binaries, plus MBOX_TEST_UNFILTERED_WAYPIPE and
MBOX_TEST_WTYPE. Read its usage first; the wrapper must be `test-fixture`, not
production `mbox` or a raw `msb` binary. It validates its immutable fixture hook
before booting. Noctalia tests are intentionally not part of this project.

## Check tiers and source snapshots

The `checks.x86_64-linux.policy` derivation runs nine fast policy/helper scripts: source-package pins,
resolver mocks, network assembly, camera host harness, options, file mounts,
home-project mounts, clipboard policy and preview. Network assembly is distinct
from its explicit Nix evaluation below. Use the explicit `nix build` gate above:
flake-check summaries and cached/build-vs-run counts are not a test count.
It deliberately
does not boot VMs or claim that all shipped integration tests run in CI.
Source is an explicit fileset of tests/programs/modules/packages/patches, copied
to an immutable store snapshot; a dirty checkout's tracked edits can change that
snapshot but cannot mutate it during a build. Untracked files must be staged
before flake builds. No checkout is a substitute for committing/reviewing a
release. The snapshot excludes documentation and local evidence.

Additional restored tests:

- `mbox-network.py`: full Nix evaluation plus shell assembly.
- `mbox-source-package.py`: immutable backport/package guardrails.
- `mbox-vsock-runtime.py FIXTURE`: bulk integrity up to 16 MiB.
- `mbox-xwayland-presentation.py FIXTURE SWAY GRIM`: private host pixel checks.
- `mbox-fontconfig.py FIXTURE`: bwrap custom-image fallback contract.
- `mbox-statfs-runtime.py FIXTURE`: diagnostic accounting/timings, not a fix.
- `MBOX_CAMERA_BRIDGE=1 MBOX_CAMERA_PRODUCTION=1 python3 tests/mbox-camera-synthetic.py FIXTURE`:
  host synthetic frames through production guest source/portal/Firefox; no physical capture.
- Firefox visual/headless/startup and Pi fixtures retained with their explicit
  arguments/environment prerequisites; never use production credentials.

The review follow-up reran the full desktop suite, bulk integrity, font fallback,
production synthetic camera bridge and expanded pure checks. The synthetic camera
still emitted expected no-host-bus/RTKit warnings. Non-NixOS runtime and broad
lifecycle behavior remain unverified.

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
