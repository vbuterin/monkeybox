{ pkgs, modules ? [], settings ? {} }:
let
  evaluated = pkgs.lib.evalModules {
    specialArgs = { inherit pkgs; };
    modules = [ ../modules/nixos/mbox.nix { programs.mbox = settings; } ] ++ modules;
  };
  cfg = evaluated.config.programs.mbox;
in {
  inherit (evaluated) options config;
  package = pkgs.symlinkJoin {
    name = "monkeybox";
    paths = [ cfg.wrapper cfg.clipboardPackage cfg.managementPackage ];
    postBuild = "ln -s mbox $out/bin/monkeybox";
    meta.mainProgram = "mbox";
  };
  inherit (cfg) wrapper clipboardPackage managementPackage;
}
