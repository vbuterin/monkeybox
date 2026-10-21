#!/usr/bin/env python3
"""Source guardrails for the pure, pinned microsandbox package (not a build test)."""
from pathlib import Path
root = Path(__file__).resolve().parents[1]
pkg = (root/'packages/microsandbox/default.nix').read_text()
module = (root/'modules/nixos/mbox.nix').read_text()
# Maintenance: when the selected release's locked backend is verified to include
# BOTH PR #171 fixes, drop the package backport/checksum rewrite and update these
# patch/version assertions together. >= 0.1.40 is not proof (PR base was 0.1.41).
# Keep runtime bulk/pixel regressions for the replacement package.
assert 'd51d1c450ee53a494e046da7f7c30c3e79616404.patch' in pkg
assert 'f9f40e1a341922c0f66da36f10914d2aad0ca8be' in pkg
assert 'msb_krun_devices-0.1.39' in pkg
for forbidden in ('lib.fakeHash', 'builtins.getEnv', 'builtins.storePath', '/tmp/mbox', 'fetchGit'):
    assert forbidden not in pkg, forbidden
assert 'cargoHash = "sha256-' in pkg
assert 'cargo build --frozen' in pkg and 'cargo test --frozen' in pkg
assert 'MSB_EMBED_ARTIFACTS_DIR' in pkg and 'microsandbox-agentd' in pkg
assert 'patch --batch --fuzz=0' in pkg
assert 'pkgs.callPackage ../../packages/microsandbox { }' in module
assert '"${msbPackage}/lib/libkrunfw.so"' in module
assert not (root/'modules/nixos/mbox-vsock-candidate.nix').exists()
pid_patch = (root/'patches/microsandbox-terminated-pid.patch').read_text()
assert '../../patches/microsandbox-terminated-pid.patch' in pkg
assert '-p microsandbox --lib test_prepare_create_target' in pkg
assert 'cfg!(windows) || run.status == run_entity::RunStatus::Running' in pid_patch
assert 'test_prepare_create_target_terminated_reused_pid' in pid_patch
assert 'UnixListener::bind(&paths.control)' in pid_patch
assert 'runtime endpoint is still live' in pid_patch
print('PASS: pinned PR backport, source agent, offline Cargo, original firmware, no impure candidate; terminated-PID fix and regression wired')
