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

`-a` bridges a selected local Pulse socket over vsock; ALSA applications receive
an invocation-local Pulse configuration. It grants recording/monitoring as well
as playback. No native PipeWire FD forwarding is attempted.

Explicit visual commands use no guest terminal, so host Ctrl+C requests bounded
VM shutdown. Forced shutdown warns about possible unflushed writes. Bare visual
interactive shells retain their terminal. Guest command exit codes are recorded
across Waypipe's child boundary; see [exit status](mbox-exit-status.md).

## Fonts / performance

The built-in guest image contains actual DejaVu font bytes locally. Expensive
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
