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
console_patch = (root/'patches/microsandbox-console-inactive-events.patch').read_text()
assert '../../patches/microsandbox-console-inactive-events.patch' in pkg
assert "changed.append('src/virtio/console/event_handler.rs')" in pkg
assert '-p msb_krun_devices --lib virtio::console::' in pkg
assert 'console_events_before_activation_do_not_index_queues' in console_patch
assert 'console_events_after_reset_do_not_index_queues' in console_patch
assert 'console.sigwinch_evt.read().is_err()' in console_patch
gpu_patch = (root/'patches/microsandbox-gpu.patch').read_text()
gpu_lock_patch = (root/'patches/microsandbox-gpu-lock.patch').read_text()
assert '../../patches/microsandbox-gpu.patch' in pkg
assert 'cargoPatches = [ ../../patches/microsandbox-gpu-lock.patch ]' in pkg
assert 'virglrenderer' in pkg and 'libepoxy' in pkg and 'libdrm' in pkg
assert 'MSB_GPU_VIRGL_FLAGS' in gpu_patch and 'gpu_virgl_flags' in gpu_patch
assert 'features embed-binaries,net,ssh,gpu' in pkg
assert 'name = "msb_krun_rutabaga_gfx"' in gpu_lock_patch and 'name = "msb_krun_display"' in gpu_lock_patch
assert 'name = "msb_krun_rutabaga_gfx"\n+version = "0.1.39"' in gpu_lock_patch and 'name = "msb_krun_display"\n+version = "0.1.39"' in gpu_lock_patch
assert 'name = "bindgen"' in gpu_lock_patch  # regression: duplicate-block cleanup stays canonical
print('PASS: pinned PR backport, source agent, offline Cargo, original firmware, no impure candidate; terminated-PID and inactive-console fixes and regressions wired; GPU (virgl/venus) feature chain wired')
