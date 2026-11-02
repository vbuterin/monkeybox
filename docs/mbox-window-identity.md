# Host window identity and mount metadata

New launchers register the final host bind mounts with the host-only management
registry. `mbox-manage list --json` exposes them as `mounts`, with `source`,
`destination`, `display`, `kind` (`directory` or `file`) and `mode` (`ro` or `rw`).
`display` abbreviates the launcher's canonical host home prefix as `~`; it never
uses the guest destination as evidence of host access. In particular, private
home backing mounted at the guest home is not a grant of the host home.
Guest-only tmpfs and owned disks have no host source and are omitted. This is a
bind-mount inventory, not an inventory of devices, network permissions or every
path inside the guest. The existing mount/grant policy is unchanged.

Old launchers have `mounts: null` (or no field); callers should report unavailable
metadata rather than an empty access list. Metadata contains paths, not file
contents. The registry remains private to the host user and is not mounted into
guests. Records are bounded to 256 KiB.

`mbox-manage borders --swaymsg /absolute/path/to/swaymsg` is an optional host
service requiring the matching host Sway outline patch. It reconciles only the
visual indicator every 750 ms; it does not focus windows, inject keys, change
layouts, or alter ordinary border settings. The patched compositor exposes
`mbox_outline` in GET_TREE and accepts `border monkeybox on|off` on a view.

Association uses live launcher/waypipe PID and process-start identities and the
launcher's host SWAYSOCK. Waypipe display-client descendants are included; window
titles and application IDs are not trusted for association. Foreground terminal
launches without waypipe can identify their terminal ancestor. Background jobs
and windowless launches do not mark arbitrary windows. Shared terminal servers
owning multiple windows are deliberately skipped: ancestry alone cannot identify
which window contains the active PTY. Detached/multiplexed terminals may likewise
lack a provable association. Existing instances need relaunching to register
identity metadata.

The host implementation draws a five-logical-pixel #00B7FF inset outline without
resizing the client, including fullscreen. It covers the outermost five pixels;
input hit-testing skips the overlay. It is a visual aid, not an unforgeable
security indicator: software with host Sway IPC access can change the flag.
Disappearance, backgrounding and service termination clear indicators; service
restarts reconcile stale flags from compositor state.

Deploy the new Monkeybox package and matching host configuration together, then
restart the Sway session (reload alone does not replace compositor code). No
sandbox state deletion is required.
