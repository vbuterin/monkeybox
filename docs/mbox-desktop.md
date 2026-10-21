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
Each audio daemon has its own minimal D-Bus session (no host service activation).
Tunnel devices appear asynchronously: the host helper waits for the requested
devices, sets defaults, then publishes a readiness marker. A socket without
ready devices does not permit VM launch.

## Input methods

The Waypipe filter permits `zwp_text_input_manager_v3` (application-side input),
not host input-method registration, keyboard grabs or virtual-input protocols.
Direct native visual applications can use the host compositor's IME. In direct
mode GTK/Qt IM overrides are cleared so each toolkit chooses its display's
frontend; forcing GTK's Wayland module on an X11 Electron window can abort it. The guest-only
wlroots used by nested clipboard Sway preserves parent keymaps and consumes
host IME commits. Native Wayland retains the guest-local Unicode key relay.
X11 commits instead go to a private `mbox` XIM server through a bounded local
socket. Changing keymaps per character is not reliable for Xwayland clients.
The XIM server sends committed strings only to an IC matching the current X
focus; unsupported/non-XIM applications drop commits rather than typing `u`.
A dead bridge fails closed. No host session bus, Fcitx socket, X socket or
configuration mount is added. Host Fcitx switching stays on the host.

Nested mode sets `XMODIFIERS=@im=mbox` and uses a guest-local GTK3 IM selector
(`GTK_IM_MODULE=mbox`). It selects Wayland text-input on Wayland displays and XIM
on X11, without loading a backend-specific module on the wrong display. This
lets native GTK report its caret instead of silently using the simple fallback.
The selector's private module cache retains GTK's standard modules. Direct
(non-nested) visual mode still clears backend-specific IM overrides. Chromium initializes
its GTK3 XIM context lazily; the bridge primes a missing context using a local
NoSymbol press/release, without replacing a keymap or pressing modifiers.
Initialization is bounded; a missing context or focus change drops the commit.
XIM commits over 16 KiB are refused to stay inside protocol encoding limits.

Candidate UI stays on the host. This also covers host IME popups used while
composing; there is no separate Compose candidate-position channel. Ordinary
XKB Compose sequences generally do not show a candidate window. Host Sway's
`xkb_options compose:ralt` is inherited through the parent keymap by native and
nested guests; do not replace it with a guest-only US keymap. Right Alt then
ceases to be AltGr. A keymap-import regression verifies `Multi_key` and the
Compose, apostrophe, e → é sequence without touching the live desktop.

Guest Sway forwards the focused application's
text-input-v3 cursor rectangle, including scene/window offsets and nested output
scale, into the parent output surface. In this guest-only mode, Sway acknowledges
text-input commits with `done(current_serial)` even without a local input method;
Electron otherwise waits indefinitely before sending its real caret. Rectangles
sent after the initial enable commit are accepted, not just enable-time fields.
No text/preedit events are synthesized by these acknowledgements. If a local
input method exists, its normal acknowledgement path remains authoritative.
Geometry is published immediately on text-input state changes and refreshed every
30 ms so
moving/resizing a window also moves the candidate anchor. Parent HiDPI scaling
is not applied twice. For X11, the bridge reads XIM spot-location attributes,
translates child-window coordinates into top-level pixels, and scales them to
the focused satellite surface. This fallback is restricted to satellite's client
PID. No host pointer position or host desktop queries are used.

Only the configured `seat0` publishes the shared geometry record; an unfocused
auxiliary seat cannot overwrite it. For intermittent fallback diagnosis, pass
`-e MBOX_CARET_DEBUG=1` to **mbox** (before `--`, not as an application argument).
It logs transition reasons (`no-keyboard-focus`, `surface-not-in-scene`,
`no-enabled-caret`, `invalid-or-off-output-caret`, `output-not-entered`, or invalid
record) without logging surrounding text or message contents. These diagnostics
are opt-in. Timing improvements do not establish that every intermittent Signal
fallback is fixed; capture a failing transition before attributing it to a cause.

Applications must actually publish a caret (text-input-v3 or XIM spot location).
Missing/disabled/stale geometry resets to the top-left fallback; rotated nested
outputs are not supported. XIM provides a spot, not the text line's full height.
Host IME/compositor policy still decides whether candidates appear above or below
the anchor. Inline preedit and surrounding-text reconversion remain unsupported;
committed text still follows the existing Unicode/XIM relay. Full rebuilt-guest
candidate placement must be checked live; component tests are not a VM result.

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
