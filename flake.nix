{
  description = "Monkeybox: explicit-grant Linux microVM applications, powered by microsandbox";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { self, nixpkgs }: let
    system = "x86_64-linux";
    pkgs = import nixpkgs { inherit system; };
    mk = settings: self.lib.mkMonkeybox { inherit pkgs settings; };
    base = mk {};
    desktop = mk {
      packages = with pkgs; [ firefox evince feh mplayer ];
      guestFontMounts = [ "${pkgs.firefox}/lib/firefox/fonts" ];
    };
    fixture = mk {
      packages = with pkgs; [ python3 pulseaudio wayland-utils mplayer feh xorg.xwininfo ];
      persist = [ "$HOME/.npm-global" "$HOME/.pi" ];
      shellHook = ''
        # MBOX_DESKTOP_TEST_FIXTURE: validated by desktop runtime suite.
        export NPM_CONFIG_PREFIX="$HOME/.npm-global"
        mkdir -p "$NPM_CONFIG_PREFIX/bin"
      '';
      memory = "1024M";
      cpus = 2;
    };
  in {
    lib.mkMonkeybox = import ./lib/mkMonkeybox.nix;
    nixosModules.default = import ./modules/nixos;
    legacyPackages.${system} = pkgs;
    packages.${system} = {
      default = base.package;
      monkeybox = base.package;
      desktop = desktop.package;
      test-fixture = fixture.wrapper;
      clipboard = base.clipboardPackage;
      microsandbox = pkgs.callPackage ./packages/microsandbox {};
      libkrunfw-swap = pkgs.callPackage ./packages/libkrunfw-swap {};
    };
    apps.${system} = {
      default = { type = "app"; program = "${base.package}/bin/mbox"; };
      desktop = { type = "app"; program = "${desktop.package}/bin/mbox"; };
    };
    checks.${system}.policy = pkgs.runCommand "monkeybox-policy-tests" {
      nativeBuildInputs = [ pkgs.python3 pkgs.bash pkgs.coreutils pkgs.gnumake ];
    } ''
      cp -r ${pkgs.lib.fileset.toSource {
        root = ./.;
        fileset = pkgs.lib.fileset.difference
          (pkgs.lib.fileset.unions [ ./tests ./programs ./modules ./packages ./patches ])
          ./tests/evidence;
      }} source; chmod -R u+w source; cd source
      python3 tests/mbox-source-package.py
      python3 tests/mbox-test-tools.py
      python3 tests/mbox-network.py --assembly-only
      python3 tests/mbox-camera-host.py
      python3 tests/mbox-options.py
      python3 tests/mbox-memory.py
      python3 tests/mbox-manage.py
      python3 tests/mbox-swap-package.py
      python3 tests/mbox-audio.py
      python3 tests/mbox-desktop-im.py
      python3 tests/mbox-xim-policy.py
      python3 tests/mbox-file-mounts.py
      python3 tests/mbox-home-project.py
      # xattr metadata tests require a host filesystem supporting user xattrs;
      # Nix build sandboxes may not provide that. Run them explicitly on host.
      python3 tests/mbox-clipboard.py
      python3 tests/mbox-clipboard-preview.py
      touch $out
    '';
    devShells.${system}.default = pkgs.mkShell {
      packages = with pkgs; [ python3 bash shellcheck git nix ];
    };
  };
}
