# Security model

The host user is trusted. This is not protection against malware already running
as that same host user. Guest applications are untrusted; VM isolation, explicit
mounts, network policy and the filtered display bridge constrain their access.
This is an experimental implementation, not an independent security audit.

- KVM access is explicit: the NixOS adapter creates group `kvm` with device mode
  0660; consumers must add intended users to that group and start a new login
  session. Other Linux distributions need equivalent local permissions.
- The selected project and `-f` mounts are read-write: a guest can alter or delete
  their contents. Do not grant broad home directories or sensitive projects.
- `/nix/store` is exposed read-only, not the host Nix daemon. Never place secrets
  in Nix derivations. Store visibility is broader than the selected guest PATH.
- Private homes persist per project; persistence does not make guest files safe
  to execute on the host. Rollbacks do not undo guest state migrations/writes.
- The bare launcher blocks networking, but application profiles in `~/.mboxrc`
  can grant egress and mounts. The shipped browser/agent profiles enable internet.
  First normal launch creates this file without overwriting existing entries.
  Profiles match command basenames, not executable identity; never treat them as
  an authentication boundary. `--no-config` bypasses all profile grants.
  `-p` exposes a selected host service to all apps in the VM, not just the foreground
  application. `-i` allows public egress.
- `--read-only` still exposes contents. `-b` exposes shell startup code (which may
  contain secrets), but not histories. `-v` copies discovered host font bytes and
  may mount bundled-font directories read-only. It does not import host font
  configuration or cache directories. Existing private guest state is not purged
  when a profile grant is removed.
- Software Wayland grants display/input interaction through a host-enforced
  protocol allowlist; it does not expose host clipboard, capture protocols,
  virtual input, host X11, GPU devices or the host session bus.
- `-a` grants playback, not host microphone/output monitoring; `-r` separately
  grants microphone input. `-l` grants an invocation-long
  camera stream starting before browser website permission. Closing a tab is
  not revocation; exit the VM. All guest applications share the grant.
- Clipboard transfers are intentional, bounded and text-only. The transfer
  buffer persists for repeated explicit pastes for 300 seconds, or until cleared.
  Focus checks are not atomic; an imported/previewed payload cannot be recalled.
- Guest-local exit-status files report process behavior, not attestations from a
  trusted guest. Forced shutdown can lose unflushed writes.

Known limitations: synthetic shared-filesystem accounting, slow aggregate-store
statfs, incomplete broad lifecycle coverage, compositor-specific clipboard
support, unverified sustained camera/audio performance and broad browser support.
No arbitrary USB/Bluetooth passthrough or complete portal suite is offered.

Report issues through the repository after publication. Avoid including live
clipboard text, camera frames, credentials, private profiles or unredacted logs.
For sensitive vulnerabilities use GitHub private vulnerability reporting if the
owner has enabled it; do not assume a private channel has already been set up.
