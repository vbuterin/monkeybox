# Runtime fixes and open follow-ups

## Concurrent invocations in one project

The launcher formerly derived one sandbox name from the project and passed
`--replace`. A second launch therefore replaced the first, producing a stale
lifecycle warning. Names now include a kernel-generated UUID per invocation,
and normal launches no longer pass `--replace` or remove an earlier sandbox.
`--fresh` remains accepted for compatibility but is redundant.

The persistent home and project files remain shared between invocations in the
same project. This is lifecycle independence, not isolation from concurrent file
edits: application profile locks and concurrent writes still need coordination.
Each invocation has its own root/owned disks. Stopped instances accumulate until
explicit `mbox rm NAME`; use `mbox list` to inspect them. No automatic disk GC is
implemented, and launching again no longer removes earlier swap backing disks.

`tests/mbox-concurrent.py` tests the actual launcher identity fragment and argv
policy. Its opt-in real-VM mode boots two private VMs sharing one disposable home,
checks both remain usable, stops the first, then checks the second still executes.
That test passed against the installed runtime; it is not a rebuilt-wrapper or
host activation test.

## Launching inside a granted directory

Launching in a directory that is also a resource grant — `~/world_knowledge` is
granted read-only to every sandbox by host policy, to be read from other
projects — assembled the same guest path twice: the launcher's read-write
project mount and the inherited read-only grant. msb rejects that before boot
("multiple volumes cannot mount the same guest path"). The launcher now enforces
one volume per guest path at the end of mount assembly, keeping the earlier
mount and noting the skipped duplicate on stderr. The read-write project mount
is assembled first, so working in a granted directory keeps project semantics
and only the redundant grant is skipped. Explicit `--file` grants of an already
mounted destination still fail loudly; that is a deliberate per-invocation act,
not inherited policy.

`tests/mbox-mount-dedupe.py` exercises the real mount-assembly fragment: the
project-grant collision (read-only and read-write variants), file grants inside
the project surviving, the no-collision case skipping nothing, and no duplicate
guest paths in any assembled set.

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

## Keyboard loss after an mbox window resize: transaction focus repair

User-visible fault: while typing in an mbox guest app, the keyboard
occasionally goes dead; switching the input method away and back (the
Super+2/Super+1 round-trip) restores it. A captured incident
(wayland wire trace, 2026-10-27) anchors the failure to the instant the mbox
window is re-tiled: the host sway resizes the waypipe surface, the guest sway
applies the new output size in a transaction, and in the same batch the guest
seat sends `wl_keyboard.leave` to the focused client without ever re-sending
`enter`. The client keeps rendering but receives no keys. The incident had no
input-method activity at all (zero fcitx5 ACTIVATE lines), so the IM grab is
not the trigger; the seat focus drop is.

Mechanism: sway's `seat_set_focus` early-returns when the focused node is
unchanged, so any path that clears the wlr keyboard focus without changing
sway's focus node leaves the seat in an inconsistent state (a mapped view is
focused, `keyboard_state.focused_surface` is NULL). Nothing re-asserts focus
because every later focus command targets the same node.

Fix (`patches/mbox-sway-focus-repair.patch`, applied to the guest sway in
`nestedSway`): after each applied transaction, every seat whose focused node
is a mapped view but whose wlr seat has no keyboard focus (and which has a
keyboard attached and no layer holding focus) gets
`wlr_seat_keyboard_notify_enter` re-sent, plus a text-input relay refocus.
The repair is a no-op in every consistent state, so it only heals the race.

Verification: the patch applies cleanly to pristine sway 1.12, builds, and
the harness (headless host sway + fcitx5 + waypipe + guest sway, scenarios
smoke/focus/churn/storm) stays green with it: typing reaches the listener in
every scenario, enter/leave pairs stay balanced (one server-side enter and
one leave per run, the leave being the scenario's final unfocus). The
harness's focus-churn storms did not reproduce the original race after the
startup-socket discovery was made race-free, so the fix rests on the captured
incident trace plus the mechanism analysis; production validation is the
user's rebuild and normal use. The harness lives outside the repo
(`/tmp/mbox-kb-hunt/`, rebuilt from this description if needed).

## `--host-loopback` dies in a traceback when the guest has no IPv4 network

`mbox --host-loopback` relays granted host ports onto the guest's own loopback
by forwarding to the guest's default gateway, the host-side end of the virtual
network. The gateway came from a Python one-liner over `/proc/net/route` that
assumed a default route always exists.

The engine, however, assigns the guest an IPv4 only when the *host* itself can
select an IPv4 route at sandbox start (a UDP `connect()` probe to 192.0.2.1 in
the network engine). Launching an mbox while the host network was down or still
coming up — observed with `mbox-online` — boots the guest with no IPv4 address
and no default route, and the relay died with a bare `StopIteration`, killing
the whole sandbox start with no clue about the cause.

The gateway probe in `host-loopback.sh` now reads the same table in three
steps: the default route when it has a real gateway; otherwise the /30 guest
subnet layout, whose gateway is the subnet address plus one (covering an
on-link default and a missing default alike, and never a wider subnet); and
when the guest has no IPv4 network at all, a plain one-line failure —
`mbox: guest has no IPv4 network (the host had no IPv4 route while the sandbox
was starting); host loopback relay cannot start — retry once the host network
is up` — so the sandbox start fails fast with a diagnosis instead of a
traceback. The probe is tested against fabricated route tables in
`tests/mbox-network.py`, including the live-shaped case of a default route and
a connected subnet on the same interface.
