{ config, lib, ... }:
{
  imports = [ ./mbox.nix ];
  config = lib.mkIf config.programs.mbox.enable {
    environment.systemPackages = with config.programs.mbox; [ wrapper clipboardPackage ];
    boot.kernelModules = [ "tun" "kvm" ];
    # Grant selected users KVM access through their normal group membership.
    # Never make KVM world writable as a side effect of installing this module.
    users.groups.kvm = {};
    services.udev.extraRules = ''KERNEL=="kvm", GROUP="kvm", MODE="0660"'';
  };
}
