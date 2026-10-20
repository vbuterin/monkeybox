# Optional Sway clipboard adapter

Start `mbox -vc -- APPLICATION`. Each opted-in VM gets a guest-local compositor
and clipboard. The host broker chooses registered instances using host Sway peer
PID, process ancestry and process start time—not guest-controlled window titles.
One desktop invocation per project/VM; stop an existing session before relaunch.

## Manual transfers

- Copy locally, then `mbox-clipboard export` while the source remains focused.
- Focus the destination, `mbox-clipboard import`, then paste locally.
- `mbox-clipboard clear` discards the transfer buffer, **not Cut**.

Text only: UTF-8, no NUL, maximum 1 MiB, 60-second expiry, consume once. No
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
Other supported apps receive fixed Copy/Paste actions, and guests use a local
full-keymap helper rather than an unrestricted host virtual keyboard endpoint.

Keep focus stable: checks are not an atomic compositor targeting operation.
Copy clears source clipboard before requesting Copy; failure may leave it empty.
Paste consumes the buffer even if an application ignores Paste. A success means
Paste requested, not application acknowledgment. Successful host imports remain
readable to host applications under the desktop's normal clipboard model.

## Optional panel UI

`mbox-clipboard watch` emits streaming JSON activity metadata. Copy/export and
paste/import are distinct; no clipboard payload is read by watching. `preview`
reads host text plus the unconsumed transfer buffer explicitly; panels should
call it only on a user click, render plain text and discard the snapshot promptly.
A visible snapshot cannot be retracted by later transfer consumption or expiry.

Noctalia styling/patches are not packaged here. No automatic shortcut install,
clipboard history, universal application-local edit observer, or Cut operation
is provided. Do not trigger preview periodically or log its payload.
