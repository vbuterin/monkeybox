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
assert 'export GTK_IM_MODULE=xim' in script
assert 'kill -KILL "$xim"' in script and 'wait "$xim"' in script
assert 'pkgs.xcb-imdkit' in module and '${guestXim}/bin/mbox-xim' in module
assert 'mbox_xim_commit(input->commit)' in relay
assert "return reply != 'N'" in relay
print('PASS: guest-local XIM startup, environment, dependency, cleanup and fail-closed routing wired')
