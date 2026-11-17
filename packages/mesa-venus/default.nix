# Mesa with the virtio (formerly "venus") Vulkan driver: the guest-side ICD
# for the engine's virtio-gpu device (virglrenderer/venus on the host, the
# venus protocol in the guest). Mesa renamed the driver to "virtio" in 25.x;
# the meson option and the ICD json (virtio_icd.*) follow the new name.
# The guest shares the host /nix/store, so the ICD json and its
# libvulkan_virtio.so are reachable from inside the sandbox at their host
# store paths; the -g/--gpu launcher wires VK_DRIVER_FILES to the ICD.
# swrast (lavapipe) stays as a CPU fallback ICD for debugging.
{ lib, mesa }:

let
  m = mesa.override { vulkanDrivers = [ "swrast" "virtio" ]; };
in
  # Dropping microsoft-experimental (the only consumer) leaves the spirv2dxil
  # binary unbuilt, so the declared spirv2dxil output would stay empty and fail
  # the build; drop the output with it.
  m.overrideAttrs (old: {
    outputs = builtins.filter (o: o != "spirv2dxil") old.outputs;
  })
