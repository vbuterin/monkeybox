{ lib, stdenv, fetchurl, flex, bison, bc, cpio, perl, elfutils, python3 }:

# Exact vendor/libkrunfw gitlink of microsandbox f9f40e1a341922c0f66da36f10914d2aad0ca8be.
# Source is vendored to avoid a mutable checkout or a runtime GitHub dependency.
stdenv.mkDerivation (finalAttrs: {
  pname = "mbox-libkrunfw-swap";
  version = "5.6.1-cf4c22b9";
  src = ./source.tar.gz;

  kernelSrc = fetchurl {
    url = "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.109.tar.gz";
    hash = "sha256-6Bryj1lB2xx/NWFR37qyyleGyL52R099QuowbN60Rc0=";
  };

  nativeBuildInputs = [ flex bison bc cpio perl python3 python3.pkgs.pyelftools ];
  buildInputs = [ elfutils ];

  postPatch = ''
    # Fail closed if the pinned upstream config or fetch recipe changes.
    substituteInPlace config-libkrunfw_x86_64 \
      --replace-fail '# CONFIG_SWAP is not set' 'CONFIG_SWAP=y'
    substituteInPlace Makefile \
      --replace-fail 'curl --fail --location --retry 5 --retry-delay 2 $(KERNEL_REMOTE) -o $(KERNEL_TARBALL)' \
                     'ln -s ${finalAttrs.kernelSrc} $(KERNEL_TARBALL)'
    patchShebangs scripts
  '';

  # Upstream interpolates MAKEFLAGS into a recursive shell command. A literal
  # $(out) gets propagated as $$(out), which is invalid shell syntax there.
  makeFlags = [ "PREFIX=${placeholder "out"}" "LIBDIR_Linux=lib" ];
  enableParallelBuilding = true;

  # Verify the effective config after olddefconfig, not just the input file.
  postBuild = ''
    grep -qx 'CONFIG_SWAP=y' linux-6.12.109/.config
    grep -qx 'CONFIG_VIRTIO_BLK=y' linux-6.12.109/.config
    grep -qx 'CONFIG_EXT4_FS=y' linux-6.12.109/.config
    test -s libkrunfw.so.5.6.1
  '';
  postInstall = ''
    install -Dm644 linux-6.12.109/.config $out/share/mbox-libkrunfw/kernel.config
    printf '%s\n' cf4c22b9f05c680928e6d96a9d198f5845573a87 \
      > $out/share/mbox-libkrunfw/source-revision
  '';

  meta = {
    description = "Microsandbox's pinned x86 guest firmware with disk swap support";
    license = with lib.licenses; [ gpl2Only lgpl21Only ];
    platforms = [ "x86_64-linux" ];
  };
})
