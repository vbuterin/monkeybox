#!/usr/bin/env python3
"""Source wiring guards; runtime behavior is covered by mbox-xim.py."""
from pathlib import Path
root = Path(__file__).resolve().parents[1]
script = (root/'programs/mbox/clipboard-desktop.sh.in').read_text()
module = (root/'modules/nixos/mbox.nix').read_text()
relay = (root/'programs/mbox/nested-input.c').read_text()
assert script.index('export MBOX_XIM_SOCKET=') < script.index('@@SWAY@@')
assert '@@XIM@@ "$MBOX_XIM_SOCKET" &' in script
assert 'export XMODIFIERS=@im=mbox' in script
assert 'GTK_IM_MODULE=mbox' in script and 'GTK_IM_MODULE_FILE=@@IM_CACHE@@' in script
selector = (root/'programs/mbox/im-auto.c').read_text()
assert 'GDK_IS_WAYLAND_DISPLAY' in selector and '? "mbox-wayland" : "xim"' in selector
module = (root/'modules/nixos/mbox.nix').read_text()
assert '../../patches/mbox-gtk-caret-commit.patch' in module
assert 'wayland-scanner private-code' in module
assert 'im-mbox-wayland.so' in module
caret_patch = (root/'patches/mbox-gtk-caret-commit.patch').read_text()
assert 'global->current == context && context_wayland->enabled' in caret_patch
assert 'notify_cursor_location (context_wayland)' in caret_patch
assert 'commit_state (context_wayland)' in caret_patch
assert 'kill -KILL "$xim"' in script and 'wait "$xim"' in script
assert 'pkgs.xcb-imdkit' in module and '${guestXim}/bin/mbox-xim' in module
xim_derivation = module.split('  guestXim = ', 1)[1].split('  clipboardDesktop = ', 1)[0]
assert xim_derivation.startswith('pkgs.runCommandCC "mbox-xim"')
assert 'pkgs.libxcb-util' in xim_derivation
assert 'ximFlags=$(pkg-config --cflags --libs xcb-imdkit xcb)' in xim_derivation
assert 'mbox_xim_commit(input->commit)' in relay
assert "return reply != 'N'" in relay
caret = (root/'programs/mbox/sway-caret.c').read_text()
patch = (root/'patches/mbox-sway-caret.patch').read_text()
assert 'mbox-sway-caret.patch' in module and 'mbox-caret-test.c -lm' in module
assert 'wlr_scene_node_for_each_buffer' in caret and 'current.cursor_rectangle' in caret
assert 'pid == satellite_pid' in caret and 'mbox_caret_to_output' in caret
state = (root/'programs/mbox/sway-caret-state.h').read_text()
assert 'mbox_caret_has_rectangle(text->input, focus)' in caret
assert 'input->current_enabled' in state and 'input->current.features &' in state
assert 'wlr_text_input_v3_send_done(input)' in state
assert patch.count('mbox_caret_state_changed(text_input)') == 3
assert 'if (handled) mbox_update_caret(text->relay)' in caret
assert 'strcmp(relay->seat->wlr_seat->name, "seat0") == 0' in caret
assert 'MBOX_CARET_DEBUG' in caret and 'MBOX_CARET_DEBUG' in relay
assert 'mbox-caret-protocol-test' in module and 'sway/input/sway-caret-state.h' in module
assert 'input->entered' in relay and 'output->surface == input->entered' in relay
assert 'now - caret.time_ms <= 250' in relay
assert 'wl_event_source_remove(relay->mbox_caret_timer)' in patch
assert 'relay->input_method || getenv("MBOX_CARET_FILE")' in patch
for name in ('MBOX_CARET_FILE', 'MBOX_XIM_CARET', 'MBOX_SATELLITE_PID_FILE'):
    assert script.index('export '+name+'=') < script.index('@@SWAY@@')
print('PASS: guest-local XIM/GTK/caret startup, environment, dependency, cleanup and fail-closed routing wired')
