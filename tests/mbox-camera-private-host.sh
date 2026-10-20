#!/usr/bin/env bash
# Private host-kernel control for the synthetic camera only; no host devices/bus/home.
set -euo pipefail
fixture=$(mktemp -d /tmp/mbox-camera-host.XXXXXXXX)
cp tests/mbox-camera-synthetic-guest.py "$fixture/probe.py"
printf 'test:x:%s:%s::/home/test:/bin/sh\n' "$(id -u)" "$(id -g)" > "$fixture/passwd"
printf 'test:x:%s:\n' "$(id -g)" > "$fixture/group"
printf 'Synthetic evidence: %s\n' "$fixture"
bwrap --unshare-all --die-with-parent --new-session \
  --ro-bind /nix/store /nix/store --proc /proc --dev /dev --tmpfs /tmp \
  --ro-bind "$fixture/passwd" /etc/passwd --ro-bind "$fixture/group" /etc/group \
  --dir /home/test --bind "$fixture" /project --chdir /project --clearenv \
  --setenv HOME /home/test --setenv PATH /nix/store/xlc4igs3a88wgn3g3d7drgj2kvdy748a-mbox-env/bin \
  --setenv FONTCONFIG_FILE /nix/store/kh8kvcgvj4a7mhjwhpwbpw4jl3m4xfcs-fonts.conf \
  /nix/store/vmp84m72hgg4gjwbrgrcvw3gr9pqiznv-python3-3.14.7-env/bin/python3 probe.py \
  /nix/store/rvgcqlnpq9lv4vbq85zvv51bkk4izpbl-pipewire-1.6.8/bin/pipewire \
  /nix/store/iw25pcr0ry2m8v14d5phlfdczi4h88my-wireplumber-0.5.17/bin/wireplumber \
  /nix/store/zjslpcn77sh4qwm0jwv5xil8jwacckdi-dbus-1.16.2/bin/dbus-daemon \
  /nix/store/6zzf1yls8bhg0avllbh63vvygbhlw8f3-firefox-156.0.1/bin/firefox
