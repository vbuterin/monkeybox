# Microsandbox 0.7.3 with the scoped libkrun PR #171 backport.
# Both CLI and embedded agent are built offline from source. Firmware remains
# selected by modules/nixos/mbox.nix from the original release bundle.
# Deliberate pairing: release firmware/initramfs + source-built injected agent;
# the PR-pinned build's bulk, desktop and statfs VM fixtures exercise this pair.
# Native x86_64-linux only: the agent target is fixed below. Unlike a fetch-only
# package this is not an aarch64 build; port agent/toolchain handling before enabling it.
{ lib, rustPlatform, fetchFromGitHub, fetchurl, pkg-config, cmake, ninja,
  clang, libclang, python3, perl, libcap_ng, openssl, zlib, glibc }:
rustPlatform.buildRustPackage {
  pname = "microsandbox";
  version = "0.7.3-vsock-pr171";
  src = fetchFromGitHub {
    owner = "superradcompany";
    repo = "microsandbox";
    rev = "f9f40e1a341922c0f66da36f10914d2aad0ca8be";
    hash = "sha256-8f7iScRqNMuHxcwmx6njvX8s7ql+0E9a87yYp8ScbA4=";
  };
  cargoHash = "sha256-h1CKRcB0otcGibRD3gQJsGiDLJnW7BH6QxjMCJtGVT0=";
  auditable = false;
  nativeBuildInputs = [ pkg-config cmake ninja clang python3 perl ];
  buildInputs = [ libcap_ng openssl zlib ];
  LIBCLANG_PATH = "${libclang.lib}/lib";
  # Pin the commit, not a mutable PR URL. Backport only its three-file diff to
  # 0.1.39; no implicit upgrade to the PR branch's newer backend/ABI dependencies.
  # TODO: drop this backport/checksum rewrite when the selected microsandbox
  # release's locked backend is verified to include BOTH PR #171 fixes. A version
  # floor of >= 0.1.40 is insufficient: the PR itself was based on 0.1.41.
  # Update tests/mbox-source-package.py's patch/pin assertions at the same time,
  # and retain bulk/pixel regressions to validate the unpatched replacement.
  vsockPatch = fetchurl {
    url = "https://github.com/vbuterin/libkrun/commit/d51d1c450ee53a494e046da7f7c30c3e79616404.patch";
    hash = "sha256-HmRy5nBedEJwfezgjqyz9oywa1tkd+1FN61XO5I9pUg=";
  };
  postPatch = ''
    device="$cargoDepsCopy/source-registry-0/msb_krun_devices-0.1.39"
    test -f "$device/src/virtio/vsock/packet.rs"
    patch --batch --fuzz=0 -d "$device" -p3 < "$vsockPatch"
    # Cargo validates vendored file checksums. Update only the patched files;
    # the fixed-output original vendor tree and fetched patch remain verified.
    python3 - "$device" <<'PY'
    import hashlib, json, pathlib, sys
    root = pathlib.Path(sys.argv[1])
    path = root / '.cargo-checksum.json'
    data = json.loads(path.read_text())
    for name in ('packet.rs', 'packet_tests.rs', 'custom_stream.rs'):
        rel = 'src/virtio/vsock/' + name
        data['files'][rel] = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    path.write_text(json.dumps(data))
    PY
    # Do not retain the workspace's development-relative runtime lookup paths.
    rm .cargo/config.toml
  '';
  buildPhase = ''
    runHook preBuild
    export CARGO_BUILD_JOBS="$NIX_BUILD_CORES"
    RUSTFLAGS="-C target-feature=+crt-static -L native=${glibc.static}/lib" \
      cargo build --frozen --profile ci -p microsandbox-agentd --target x86_64-unknown-linux-gnu
    mkdir -p "$TMPDIR/embed"
    cp target/x86_64-unknown-linux-gnu/ci/agentd "$TMPDIR/embed/agentd"
    export MSB_EMBED_ARTIFACTS_DIR="$TMPDIR/embed"
    cargo build --frozen --profile ci --no-default-features \
      --features embed-binaries,net,ssh -p microsandbox-cli
    runHook postBuild
  '';
  # Deliberately focused: the earlier broader vsock run on this 0.1.39 base
  # hung for several minutes in the existing device test
  # vsock_quiesce_joins_background_workers_and_is_reactivatable, then was stopped.
  # Filed: https://github.com/superradcompany/libkrun/issues/172 (log attached).
  # See docs/mbox-vsock-lifecycle-upstream-report.md (command/log,
  # descriptor-only candidate context and SIGTERM outcome; not a baseline proof).
  # Do not claim full --lib coverage. Revisit broader coverage/exact exclusions
  # when that lifecycle hang is diagnosed; compilation cost is not the reason.
  checkPhase = ''
    runHook preCheck
    cargo test --frozen --profile ci -p msb_krun_devices --lib virtio::vsock::packet::tests
    cargo test --frozen --profile ci -p msb_krun_devices --lib virtio::vsock::custom_stream::tests
    runHook postCheck
  '';
  installPhase = ''
    runHook preInstall
    install -Dm755 target/ci/msb "$out/bin/msb"
    ln -s msb "$out/bin/microsandbox"
    runHook postInstall
  '';
  meta = {
    description = "Microsandbox with libkrun PR 171 TX-chain and credit fixes";
    homepage = "https://github.com/superradcompany/libkrun/pull/171";
    platforms = [ "x86_64-linux" ];
    mainProgram = "msb";
  };
}
