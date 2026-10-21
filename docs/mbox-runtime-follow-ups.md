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

## Open: playback after changing output device

User report: launch Firefox, then switch the host audio output to Bluetooth
AirPods. Firefox playback does not follow the switch; relaunching Firefox
restores playback. Not yet reproduced in a private component test.

Investigate whether the private playback tunnel remains bound to the output
selected at startup. Test default-sink changes, device removal/reconnection,
and two concurrent app VMs. Preserve playback/capture separation: following a
playback output change must not grant microphone access or silently change a
microphone grant. No live audio routing changes made during diagnosis.

## Open: Firefox candidate geometry across text fields

Signal candidate placement is now consistently correct in user testing.
Firefox sometimes temporarily anchors candidates at the preceding text field,
not at (0,0). Determine whether the application publishes the new rectangle
late or the bridge retains previous state. Capture geometry transition logs
without message contents before choosing a fix.

## Compose validation

User confirmed Right Alt Compose works on the host and in Signal and Firefox
after `swaymsg 'input type:keyboard xkb_options compose:ralt'`. Ordinary Compose
has no pinyin-style choice bar. The host's persistent Home Manager setting
belongs in its configuration repo; it must be activated separately. No further
guest keymap override is needed.
