#!/usr/bin/env python3
"""Guest-local Cut guard. No guest-to-host input interface."""
import json
from pathlib import Path
import subprocess
import sys

TERMINALS = {'foot', 'footclient', 'alacritty', 'kitty', 'wezterm-gui',
             'gnome-terminal-server', 'konsole', 'xterm', 'rxvt', 'urxvt',
             'st', 'terminator', 'xfce4-terminal', 'tilix', 'ghostty'}


def focused(swaymsg):
    reply = subprocess.run([swaymsg, '-t', 'get_tree', '-r'], check=True,
                           capture_output=True, timeout=2)
    if len(reply.stdout) > 8 * 1024 * 1024:
        raise ValueError('tree too large')
    pending = [json.loads(reply.stdout)]
    while pending:
        node = pending.pop()
        pending.extend(node.get('nodes', []) + node.get('floating_nodes', []))
        if node.get('focused') and node.get('pid'):
            pid = int(node['pid'])
            exe = Path('/proc', str(pid), 'exe').resolve(strict=True).name
            if exe in TERMINALS or node.get('app_id') in TERMINALS:
                raise ValueError('terminal selection is not editable')
            return [node['id'], pid]
    raise ValueError('no focused application')


def main():
    action, swaymsg, key = sys.argv[1:]
    identity = focused(swaymsg)
    if action == 'check-cut':
        print(json.dumps(identity))
    elif action == 'key-cut':
        expected = json.loads(sys.stdin.buffer.read(1024))
        if identity != expected or focused(swaymsg) != identity:
            raise ValueError('focus changed')
        subprocess.run([key, 'cut'], check=True, timeout=2)
    else:
        raise ValueError('invalid action')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('mbox: guest Cut refused', file=sys.stderr)
        sys.exit(1)
