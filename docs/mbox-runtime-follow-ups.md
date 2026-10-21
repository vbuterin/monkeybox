# Runtime fixes and open follow-ups

## Terminated-run PID reuse blocks `--replace`

Observed on the host: Signal's sandbox was `Stopped`, its last run was
`Terminated`, and PID 4021 had since been reused by an unrelated process after
reboot. Firefox had an independent `MSB_HOME`; identical project-derived names
were not a shared-VM collision. Clearing the verified historical PID restored
Signal, but editing a database is not the ongoing solution.

`patches/microsandbox-terminated-pid.patch` changes microsandbox's replacement
check: after acquiring the lifecycle lock, Unix checks PID liveness only for
a running run, not a terminated historical run. A live runtime endpoint still
blocks removal, including legacy endpoints. Windows retains its conservative
PID check because older Windows runtimes can publish termination early.

The regression uses a private SQLite database and points a terminated run at
the test process's own live PID. Replacement must succeed without signalling
that process. First it creates a live Unix control socket and verifies that
replacement refuses and leaves the sandbox record/storage intact. Existing
stopped, stale-running, active-running and non-replacement tests run alongside
it. These are SDK tests, not VM tests. They are wired into the package check.

Scope: this fixes the confirmed **terminated-run replacement** bug. It does
not provide process-birth identity/pidfd validation for every microsandbox
lifecycle operation. Stale records still marked `Running` and PID reuse in
other stop/remove paths need a separate upstream audit. Do not interpret this
as a general guarantee that all historical PID handling is safe.

Publishing/pinning/rebuilding Monkeybox is required to replace the installed
microsandbox executable. No runtime databases are repaired automatically, and
no live processes are stopped by the patch installation.

## Playback after changing output device: component fix

User report: launch Firefox, then switch the host audio output to Bluetooth
AirPods. Firefox playback does not follow the switch; relaunching Firefox
restores playback. Reproduced the routing restriction with two private Pulse
null sinks: the original tunnel explicitly sets `no_move=true` and rejects
`move-sink-input`. `mbox-pulse-playback-move.patch` changes only playback to
`no_move=false`; microphone capture stays pinned. The bridge uses a private
patched Pulse package, not a replacement host daemon.

The runtime regression fails with the stock tunnel and passes with the patched
module: playback moves to the second output and back; microphone moves to an
output monitor remain forbidden. Existing isolation/readiness tests also pass.
This permits the host session policy to move playback; changing a Pulse default
alone is not a universal promise that existing streams move. Actual
PipeWire/WirePlumber AirPods switching, removal/reconnection, audible continuity,
and two simultaneous VMs still require host validation. No live routing changed.

## Firefox candidate geometry across text fields: component fix

Signal candidate placement is now consistently correct in user testing.
Firefox sometimes temporarily anchors candidates at the preceding text field,
not at (0,0). Reproduced with Firefox 156.0.1 on private headless Sway and a
throwaway two-textarea page. GTK 3.24.52 sends the preceding rectangle during
focus-in; `set_cursor_location` then updates its cache without committing it.
The stale rectangle is therefore already present on the application protocol.

`mbox-gtk-caret-commit.patch` publishes a changed cursor rectangle immediately
when that context is current/enabled. The guest builds only the GTK Wayland IM
module from the pinned GTK source as `mbox-wayland`, with a distinct GType name;
`im-auto.c` selects it on Wayland and still selects XIM on X11. No host GTK or
Firefox package replacement is needed. The standard modules remain in the
private cache for compatibility.

`mbox-firefox-caret.py` fails on stock GTK (one old rectangle per focus epoch),
and passes with the patched selector/module (11 transitions tested, each
receiving the corrected rectangle in the same epoch). Private GTK X11/Wayland
initialization tests also pass. This is protocol evidence, not a rebuilt-VM or
actual Fcitx candidate-window validation.

## Compose validation

User confirmed Right Alt Compose works on the host and in Signal and Firefox
after `swaymsg 'input type:keyboard xkb_options compose:ralt'`. Ordinary Compose
has no pinyin-style choice bar. The host's persistent Home Manager setting
belongs in its configuration repo; it must be activated separately. No further
guest keymap override is needed.
