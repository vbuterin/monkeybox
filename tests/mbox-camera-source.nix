let pkgs = (builtins.getFlake (toString ../.)).legacyPackages.x86_64-linux;
in pkgs.stdenv.mkDerivation {
  name = "mbox-synthetic-camera";
  dontUnpack = true;
  nativeBuildInputs = [ pkgs.pkg-config ];
  buildInputs = [ pkgs.pipewire ];
  buildPhase = ''
    $CC -Wall -Wextra -Werror ${./mbox-camera-source.c} $(pkg-config --cflags --libs libpipewire-0.3) -o camera
  '';
  installPhase = "mkdir -p $out/bin; cp camera $out/bin/mbox-synthetic-camera";
}
