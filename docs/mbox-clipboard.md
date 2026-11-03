# Optional Sway clipboard adapter

Start `mbox -vc -- APPLICATION`. Each opted-in VM gets a guest-local compositor
and clipboard. The host broker chooses registered instances using host Sway peer
PID, process ancestry and process start time—not guest-controlled window titles.
One desktop invocation per project/VM; stop an existing session before relaunch.

## Window layout

The nested desktop defaults to side-by-side tiling. Select explicitly with:

```sh
mbox -vc --desktop-layout side-by-side -- APPLICATION
mbox -vc --desktop-layout tabbed -- APPLICATION
```

Tabbed mode gives tiled secondary windows the full available width, with a tab
bar to switch between them. Windows recognized as dialogs still float normally.
This affects only the guest desktop, not the host layout. The option requires
`-vc`; terminal-only and direct visual invocations are unchanged. It does not
change the application's minimum-size requirements.

## Window lifetime

Closing the outer nested-desktop window ends that invocation: remaining guest
views are asked to close, then background app processes are terminated with a
bounded grace period. Closing a guest app window (including `Ctrl+Alt+Q`) is
not the same operation. A running command may legitimately have no windows,
for example an audio player or an app that hides instead of quitting.
No-window startup and transitions from video to audio do not end the session.
Terminal-only invocations do not use this desktop lifecycle handler.

## Manual transfers

- Copy locally, then `mbox-clipboard export` while the source remains focused.
- Focus the destination, `mbox-clipboard import`, then paste locally.
- `mbox-clipboard clear` discards the transfer buffer, **not Cut**.
- `printf 'text' | mbox-clipboard write` explicitly writes stdin to both the host
  local clipboard and transfer buffer, without inspecting window focus.
- `mbox-clipboard read` prints the unexpired transfer buffer verbatim to stdout,
  without consuming it or appending a newline. Missing/expired text fails.
- `mbox-clipboard cut` is host-only: copy the focused editable selection, publish
  the transfer buffer, recheck focus and clipboard bytes, then request Ctrl+X.
  Failed copy/storage, empty text, changed focus/clipboard, guest windows and
  recognized terminals are refused before Cut. It does not guarantee deletion:
  the application owns selection/editability and handles Ctrl+X. Selection changes
  within the same application cannot be checked atomically; don't change selection
  while the shortcut runs. Unrecognized terminal emulators may not be detected.

The personal host config maps `xi` to `write`, `xom` to `read`, and binds
Ctrl+Alt+X on release to `cut` (press is consumed). `xo` remains local wl-paste.
These shell aliases are host-side; no new guest access to the broker is granted.

Text only: UTF-8, no NUL, maximum 1 MiB, 300-second expiry. Paste preserves the
transfer buffer for repeated explicit imports until expiry or clear. No
passive synchronization, primary selection, images, files, HTML, DnD or history.
Imports cannot be retracted from applications by clearing the transfer buffer.

## Combined shortcuts (opt-in)

Place these in your Sway config, using an installed broker on Sway's PATH or
an absolute package path. They override ordinary terminal copy/paste chords:

```sway
bindsym Ctrl+Shift+c nop
bindsym --release Ctrl+Shift+c exec mbox-clipboard copy
bindsym Ctrl+Shift+v nop
bindsym --release Ctrl+Shift+v exec mbox-clipboard paste
bindsym Ctrl+Shift+x exec mbox-clipboard clear
```

For native host Foot, add to its configuration (merge existing key-bindings):

```ini
[key-bindings]
clipboard-copy=Control+Shift+c XF86Copy
clipboard-paste=Control+Shift+v XF86Paste
```

The broker uses Foot semantic Copy/Paste keys so it does not send shell Ctrl+C.
Footclient/server, other terminals and guest terminals are not supported combined
editing targets; use manual local editing plus raw export/import instead.
Other supported apps receive fixed Copy/Paste actions. Host and guest both use
the same full-keymap helper; host actions run locally and guest actions run via
the registered VM. No host virtual-keyboard endpoint is exposed to guests.
Text MIME aliases are negotiated, then the bytes are checked for valid UTF-8.

Keep focus stable: checks are not an atomic compositor targeting operation.
Copy clears source clipboard before requesting Copy; failure may leave it empty.
Paste retains the transfer buffer even if an application ignores Paste. A success means
Paste requested, not application acknowledgment. Successful host imports remain
readable to host applications under the desktop's normal clipboard model.

## Optional panel UI

`mbox-clipboard watch` emits streaming JSON activity metadata. Copy/export and
paste/import are distinct; no clipboard payload is read by watching. `preview`
reads host text plus the unconsumed transfer buffer explicitly; panels should
call it only on a user click, render plain text and discard the snapshot promptly.
A visible snapshot cannot be retracted by later transfer clearing or expiry.

Noctalia styling/patches are not packaged here. No automatic shortcut install,
clipboard history, universal application-local edit observer, or Cut operation
is provided. Do not trigger preview periodically or log its payload.
