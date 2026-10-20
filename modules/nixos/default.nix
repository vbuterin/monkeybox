{ config, lib, pkgs, ... }:
let
  fc = config.fonts.fontconfig;
  bool = value: if value then "true" else "false";
  familyRule = generic: families: ''
    <alias binding="same"><family>${generic}</family><prefer>
      ${lib.concatMapStrings (name: "<family>${lib.escapeXML name}</family>") families}
    </prefer></alias>
  '';
  rendering = pkgs.writeText "mbox-host-font-rendering.conf" ''
    <?xml version="1.0"?>
    <!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
    <fontconfig>
      <match target="pattern">
        <edit name="antialias" mode="assign"><bool>${bool fc.antialias}</bool></edit>
        <edit name="hinting" mode="assign"><bool>${bool fc.hinting.enable}</bool></edit>
        <edit name="autohint" mode="assign"><bool>${bool fc.hinting.autohint}</bool></edit>
        <edit name="hintstyle" mode="assign"><const>hint${fc.hinting.style}</const></edit>
        <edit name="rgba" mode="assign"><const>${fc.subpixel.rgba}</const></edit>
        <edit name="lcdfilter" mode="assign"><const>lcd${fc.subpixel.lcdfilter}</const></edit>
      </match>
      ${familyRule "sans-serif" fc.defaultFonts.sansSerif}
      ${familyRule "serif" fc.defaultFonts.serif}
      ${familyRule "monospace" fc.defaultFonts.monospace}
      ${familyRule "emoji" fc.defaultFonts.emoji}
    </fontconfig>
  '';
in
{
  imports = [ ./mbox.nix ];
  config = lib.mkIf config.programs.mbox.enable {
    environment.systemPackages = with config.programs.mbox; [ wrapper clipboardPackage ];
    # Reuse the host's declared font choices, but copy bytes into the private image.
    programs.mbox.fontPackages = lib.mkDefault config.fonts.packages;
    # Copy declarative preferences, not host cache paths or impure conf.d includes.
    programs.mbox.fontConfigIncludes = lib.mkDefault [ rendering ];
    boot.kernelModules = [ "tun" "kvm" ];
    # Grant selected users KVM access through their normal group membership.
    # Never make KVM world writable as a side effect of installing this module.
    users.groups.kvm = {};
    services.udev.extraRules = ''
      KERNEL=="kvm", GROUP="kvm", MODE="0660"
    '';
  };
}
