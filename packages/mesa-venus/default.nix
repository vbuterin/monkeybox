# Mesa with the virtio (formerly "venus") Vulkan driver: the guest-side ICD
# for the engine's virtio-gpu device (virglrenderer/venus on the host, the
# venus protocol in the guest). Mesa renamed the driver to "virtio" in 25.x;
# the meson option and the ICD json (virtio_icd.*) follow the new name.
# The guest shares the host /nix/store, so the ICD json and its
# libvulkan_virtio.so are reachable from inside the sandbox at their host
# store paths; the -g/--gpu launcher wires VK_DRIVER_FILES to the ICD.
# swrast (lavapipe) stays as a CPU fallback ICD for debugging.
#
# Dropping microsoft-experimental (the only consumer) leaves the spirv2dxil
# binary unbuilt. We keep the declared spirv2dxil output anyway (it ends up
# an empty dir): mesa's postInstall does `mkdir -p $spirv2dxil`
# unconditionally since 26.3.0, so filtering the output out would leave the
# variable empty and kill the build with "mkdir: missing operand".
{ lib, mesa }:

let
  m = mesa.override { vulkanDrivers = [ "swrast" "virtio" ]; };
in
  m.overrideAttrs (old: {
    # nixpkgs mesas without the unconditional mkdir would leave this output
    # unproduced; ensure it exists either way.
    postInstall = (old.postInstall or "") + ''
      mkdir -p "$spirv2dxil"
    '';
  })
