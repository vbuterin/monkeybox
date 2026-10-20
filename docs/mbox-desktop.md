# Desktop architecture

The host runs a scoped patched Waypipe which filters advertised Wayland globals
and validates binds against advertised IDs, versions and revocation. The guest
runs software Waypipe and guest-local Xwayland. No host Xauthority, X socket,
GPU device, session bus, or host clipboard manager is granted.

`-v` enables visual mode. For GTK/Firefox use `-vc`: guest-local nested Sway
provides the data-device manager GTK requires, without exposing the host one.
The clipboard broker currently requires host Sway for trustworthy peer/focus
selection. Other compositors may support ordinary `-v` applications but are not
validated clipboard targets.

`-a` enables playback; `-r` independently enables microphone input. Both are
off by default; `--no-audio` and `--no-microphone` revoke only their respective
direction. A private host Pulse daemon loads only the selected tunnels, disables
client module loading and exit, and exports its private socket over
vsock. The host's native Pulse socket is never exported. The guest can monitor
its own playback, not other host applications or the host microphone without
`-r`. Microphone input selects the host's current default non-monitor source at
launch; selecting a monitor fails closed. ALSA applications receive a private
Pulse configuration. No native PipeWire FD forwarding is attempted.

## Input methods

The Waypipe filter permits `zwp_text_input_manager_v3` (application-side input),
not host input-method registration, keyboard grabs or virtual-input protocols.
Native visual applications can use the host compositor's IME. The guest-only
wlroots used by nested clipboard Sway preserves parent keymaps and consumes
host IME commits, translating Unicode into guest-local key events for both
Wayland and Xwayland applications. This needs no host session bus, Fcitx socket,
or Fcitx configuration mount. Host Fcitx switching stays on the host.

The nested relay handles committed text only: inline preedit, surrounding-text
reconversion, and accurate candidate-window placement are not implemented.
Candidate UI stays on the host. Live Russian/Pinyin typing and Xwayland behavior
must be checked on a rebuilt fixture; source compilation is not a runtime test.

Explicit visual commands use no guest terminal, so host Ctrl+C requests bounded
VM shutdown. Forced shutdown warns about possible unflushed writes. Bare visual
interactive shells retain their terminal. Guest command exit codes are recorded
across Waypipe's child boundary; see [exit status](mbox-exit-status.md).

## Fonts / performance

The built-in guest image contains actual font bytes locally: DejaVu plus Noto,
CJK Sans and color emoji by default. The NixOS adapter instead defaults the
additional selection to `fonts.packages`, matching the host's declared fonts,
and imports declarative fontconfig antialiasing, hinting, subpixel and family
preferences (not arbitrary host configuration files).
`fontPackages` customizes that list, and `fontConfigIncludes` accepts immutable
rendering/family rules. Fontconfig's standard rendering/substitution rules are
included (previously all includes were disabled). No host home or font cache is
shared. Expensive
statfs on the aggregate host Nix store can otherwise block font discovery.
The `desktop` package also configures a narrow same-path Firefox font-directory
mount for Firefox's bundled fonts; it applies headlessly too. Custom packages
can set `guestFontMounts`; nonexistent directories fail at build time.

These are measured, scoped mitigations, not a fix for backend filesystem
accounting. Shared filesystem free-space/inode reporting is synthetic and may
mislead package managers and garbage collectors. Arbitrary store-directory
probes may remain slow. No throughput or GPU performance parity is claimed.

## Upstream dependencies

Pinned microsandbox 0.7.3 uses a focused immutable libkrun PR #171 backport:
readable TX descriptor-chain gathering and actual-consumption credit updates.
Both fixes are required. Do not remove the backport merely because a dependency
version is newer. Source and vendor checksums are verified by the package build.

- https://github.com/superradcompany/libkrun/pull/171
- https://github.com/superradcompany/libkrun/issues/172

The broader lifecycle test issue #172 is separate and unresolved. Historical
bulk/pixel/statfs evidence remains in the original repository, not copied as
claims of fresh standalone coverage; see [provenance](../PROVENANCE.md).
