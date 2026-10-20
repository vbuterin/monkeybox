#!/usr/bin/env python3
"""Isolated real microVM/Wayland/Pulse/network smoke test (no host session).
Usage: python3 tests/mbox-desktop-runtime.py WRAPPER SWAY PULSEAUDIO WESTON_PRESENTATION_SHM
Requires KVM, MBOX_TEST_UNFILTERED_WAYPIPE=/path/to/original/waypipe,
and MBOX_TEST_WTYPE=/path/to/wtype (input injection only into the private fixture).
Uses a headless compositor, null audio sink, and fake TCP services.
"""
import os
import json
import shlex
import shutil
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

if len(sys.argv) != 5:
    sys.exit('Usage: mbox-desktop-runtime.py FIXTURE_WRAPPER SWAY PULSEAUDIO WESTON_PRESENTATION_SHM\nBuild wrapper: nix build --impure --file tests/mbox-desktop-fixture.nix --no-link --print-out-paths')
wrapper, sway, pulse, shm_app = map(os.path.abspath, sys.argv[1:])
import re
try:
    script = Path(wrapper).read_text()
    hook = re.search(r'HOOK_FILE="([^"]+)"', script)
    if not hook or 'MBOX_DESKTOP_TEST_FIXTURE' not in Path(hook[1]).read_text():
        raise ValueError()
except (OSError, UnicodeError, ValueError):
    sys.exit('Expected the desktop FIXTURE wrapper, not production mbox or another binary.\nBuild: nix build --impure --file tests/mbox-desktop-fixture.nix --no-link --print-out-paths; append /bin/mbox')
for executable in (sway, pulse, shm_app):
    if not os.access(executable, os.X_OK): sys.exit('Test executable missing or not executable: '+executable)
for variable in ('MBOX_TEST_UNFILTERED_WAYPIPE', 'MBOX_TEST_WTYPE'):
    if not os.access(os.environ.get(variable,''), os.X_OK): sys.exit('Set '+variable+' to its test executable path')
unfiltered = os.environ['MBOX_TEST_UNFILTERED_WAYPIPE']
wtype = os.environ['MBOX_TEST_WTYPE']
with tempfile.TemporaryDirectory(prefix="mbox-runtime-") as tmp:
    root = Path(tmp)
    for p in ("home", "project", "state", "runtime", "runtime/pulse"):
        (root / p).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(root / "home"), XDG_RUNTIME_DIR=str(root / "runtime"),
               MBOX_STATE_ROOT=str(root / "state"), MSB_HOME=str(root / "state/engine"),
               WLR_BACKENDS="headless", WLR_RENDERER="pixman", WLR_LIBINPUT_NO_DEVICES="1",
               DBUS_SESSION_BUS_ADDRESS=f"unix:path={root}/no-session-bus",
               DBUS_SYSTEM_BUS_ADDRESS=f"unix:path={root}/no-system-bus")
    for key in ("WAYLAND_DISPLAY", "DISPLAY", "SWAYSOCK", "PULSE_SERVER", "PULSE_CLIENTCONFIG"):
        env.pop(key, None)
    (root / "sway.conf").write_text("output HEADLESS-1 mode 800x600\n")
    (root / "pulse.conf").write_text(
        f"load-module module-native-protocol-unix socket={root}/runtime/pulse/native auth-anonymous=1\n"
        "load-module module-null-sink sink_name=mbox_fixture\n")
    processes = []
    services = []
    try:
        for name, cmd in (("sway", [sway, "-c", str(root / "sway.conf")]),
                          ("pulse", [pulse, "-n", "-F", str(root / "pulse.conf"),
                                     "--daemonize=no", "--exit-idle-time=-1", "--use-pid-file=no"])):
            log = open(root / f"{name}.log", "w")
            proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=log)
            processes.append((name, proc, log))
        for _ in range(150):
            displays = [p for p in (root / "runtime").glob("wayland-*") if p.is_socket()]
            if displays and (root / "runtime/pulse/native").is_socket():
                break
            if any(p.poll() is not None for _, p, _ in processes):
                raise RuntimeError("fixture service exited")
            time.sleep(.1)
        assert displays, "no headless Wayland socket"
        env["WAYLAND_DISPLAY"] = displays[0].name

        def run(flags, command, verbose=True, *, step=None, timeout=30):
            label = step or command.splitlines()[0][:90]
            started = time.monotonic()
            print(f'START {label} (budget {timeout}s)', flush=True)
            try:
                result = subprocess.run([wrapper, str(root / "project"), *flags, "--",
                                         "bash", "-c", command], env=env, text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                print(f'TIMEOUT {label}: {exc.stdout!r}', flush=True)
                # Stop only this fixture VM, not any host/user session.
                import hashlib
                name = 'mbox-' + hashlib.sha256(str(root/'project').encode()).hexdigest()[:12]
                try:
                    subprocess.run([wrapper, 'stop', name], env=env, timeout=15)
                except subprocess.TimeoutExpired:
                    print(f'Fixture VM stop timed out: {name}', flush=True)
                raise
            finally:
                print(f'ELAPSED {label}: {time.monotonic() - started:.3f}s', flush=True)
            if verbose or result.returncode != 0:
                print(result.stdout, flush=True)
            assert result.returncode == 0, result.returncode
            return result.stdout

        # A new boot for each invocation; no previous desktop permissions survive.
        assert "HEADLESS_OK" in run([], 'set -e; test -z "${WAYLAND_DISPLAY:-}"; test -z "${PULSE_SERVER:-}"; echo HEADLESS_OK')
        assert "NPM_WRITE_OK" in run([], 'set -e; echo private > "$HOME/.npm-global/bin/fixture"; echo NPM_WRITE_OK')
        assert "NPM_PERSIST_OK" in run([], 'set -e; grep -q private "$HOME/.npm-global/bin/fixture"; echo NPM_PERSIST_OK')
        # Generate real nonzero PCM and verify that MPlayer's default ALSA
        # output reaches the Pulse null sink (not its silent null backend).
        import wave
        import struct
        import math
        with wave.open(str(root / "project/tone.wav"), "wb") as wav:
            wav.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
            wav.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(i * 2 * math.pi * 440 / 44100))) for i in range(44100)))
        player = run(["-a"], '''set -e
            parec --device=mbox_fixture.monitor --raw --format=s16le --rate=44100 --channels=1 > /tmp/captured.pcm &
            capture=$!
            trap 'kill "$capture" 2>/dev/null || true' EXIT
            sleep .3
            mplayer tone.wav
            sleep .3
            kill "$capture"
            wait "$capture" || true
            python3 -c 'assert any(open("/tmp/captured.pcm", "rb").read()), "no nonzero audio reached host sink"'
        ''')
        assert "AO: [alsa]" in player and "AO: [null]" not in player, player
        print("MPLAYER_ALSA_PULSE_OK")
        # Move the actual fixture socket: a silent fallback to the conventional
        # location now fails. Test both supported override forms, then rejection.
        alternate = root / 'runtime/custom-pulse'
        (root / 'runtime/pulse/native').rename(alternate)
        for endpoint in (str(alternate), 'unix:' + str(alternate)):
            env['PULSE_SERVER'] = endpoint
            assert 'mbox_fixture' in run(['-a'], 'pactl list short sinks')
        for endpoint in ('tcp:127.0.0.1:4713', 'unix:/nonexistent/mbox-pulse'):
            env['PULSE_SERVER'] = endpoint
            result = subprocess.run([wrapper, str(root/'project'), '-a', '--', 'true'],
                                    env=env, capture_output=True, text=True, timeout=15)
            assert result.returncode == 2 and (
                'Pulse' in result.stderr or 'PULSE_SERVER' in result.stderr), result.stderr
        env['PULSE_SERVER'] = 'unix:' + str(alternate)
        runtime_dir = env.pop('XDG_RUNTIME_DIR')
        try:
            assert 'mbox_fixture' in run(['-a'], 'pactl list short sinks')
        finally:
            env['XDG_RUNTIME_DIR'] = runtime_dir
        print('PULSE_SERVER_OVERRIDE_OK')
        out = run(["--visual", "--audio"],
                  'set -e; wayland-info; pactl info; pactl list short sinks; '
                  'head -c 19200 /dev/zero | pacat --playback --raw --rate=48000 --channels=2; '
                  'echo DESKTOP_OK')
        assert "wl_compositor" in out and "mbox_fixture" in out and "DESKTOP_OK" in out
        # Compare the actual exposed registry against a fixed policy, not merely
        # a few required globals. The host fixture must advertise forbidden ones.
        probe = Path(__file__).with_name('wayland-registry-probe.py')
        shutil.copyfile(probe, root/'project/registry.py')
        host_globals = json.loads(subprocess.check_output([sys.executable, str(probe)], env=env))
        guest_globals = json.loads(run(['-v'], 'python3 registry.py'))
        allowed_globals = {
            'wl_compositor', 'wl_subcompositor', 'wl_shm', 'wl_output', 'wl_seat',
            'xdg_wm_base', 'wp_viewporter', 'wp_presentation',
            'wp_fractional_scale_manager_v1', 'zxdg_output_manager_v1',
            'zxdg_decoration_manager_v1', 'zwp_pointer_gestures_v1',
            'zwp_relative_pointer_manager_v1', 'zwp_pointer_constraints_v1',
        }
        assert {'wl_compositor', 'wl_shm', 'xdg_wm_base'} <= guest_globals.keys()
        assert guest_globals.keys() == host_globals.keys() & allowed_globals
        forbidden = 'zwlr_screencopy_manager_v1'
        assert forbidden in host_globals and forbidden not in guest_globals
        global_id = host_globals[forbidden][0]
        baseline = subprocess.run([sys.executable, str(probe), str(global_id), forbidden],
                                  env=env, capture_output=True, text=True, timeout=10)
        assert baseline.returncode != 0 and 'forbidden bind survived through sync' in baseline.stderr
        for bind_id, interface in ((global_id, forbidden), (global_id, 'wl_compositor'),
                                   (host_globals['wl_compositor'][0], forbidden)):
            # The original unfiltered waypipe replaces the guest proxy, proving
            # rejection at the host boundary, not just by cooperative guest code.
            command = (f'{shlex.quote(unfiltered)} --no-gpu --vsock --socket 2:5001 server -- '
                       f'python3 registry.py {bind_id} {interface}')
            result = run(['-v'], command)
            assert 'FORBIDDEN_BIND_REJECTED' in result
        # libwayland's legacy zero-version behavior is compositor-dependent.
        # Assert the pinned baseline rejects zero; mbox also requires >= 1.
        compositor_id = host_globals['wl_compositor'][0]
        positive = subprocess.run(
            [sys.executable, str(probe), str(compositor_id), 'wl_compositor', '1', 'accepted'],
            env=env, capture_output=True, text=True, timeout=10)
        assert positive.returncode == 0 and 'BIND_ACCEPTED' in positive.stdout, positive.stderr
        baseline_zero = subprocess.run(
            [sys.executable, str(probe), str(compositor_id), 'wl_compositor', '0', 'rejected'],
            env=env, capture_output=True, text=True, timeout=10)
        assert baseline_zero.returncode == 0, baseline_zero.stderr
        assert 'FORBIDDEN_BIND_REJECTED' in baseline_zero.stdout
        print('UNFILTERED_VERSION_ZERO_REJECTED')
        zero_command = (f'{shlex.quote(unfiltered)} --no-gpu --vsock --socket 2:5001 server -- '
                        f'python3 registry.py {compositor_id} wl_compositor 0')
        assert 'FORBIDDEN_BIND_REJECTED' in run(['-v'], zero_command)
        print('FILTERED_VERSION_ZERO_REJECTED: explicit version >= 1 required')
        print('WAYLAND_GLOBAL_ALLOWLIST_OK: exact registry and hostile binds')
        frames = run(["--visual"],
                     f'WAYLAND_DEBUG=1 timeout 3 {shlex.quote(shm_app)}; test $? = 124',
                     verbose=False)
        assert "create_pool" in frames and "wl_callback" in frames and ".done(" in frames, frames
        print("SHM_RENDER_FRAMES_OK: real shared-memory buffer transfer and frame callbacks")
        # Exercise the shared production base config and actual font bytes, not
        # just the presence of a fontconfig executable. statfs on the guest store
        # can stall (see mbox-statfs-runtime.py), so budget this step separately.
        fonts = run([], '''set -e
            test -r /etc/fonts/fonts.conf
            font=$(fc-match -f '%{file}' sans)
            test -n "$font" && test -r "$font"
            printf 'FONT_MATCH_OK: %s\\n' "$font"
        ''', step='fontconfig match', timeout=150)
        assert 'FONT_MATCH_OK' in fonts and 'DejaVu' in fonts, fonts
        assert 'Fontconfig error:' not in fonts, fonts
        # Real feh, generated PNG, guest X server only. No host DISPLAY/socket.
        import zlib
        def png_chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
        (root/'project/fixture.png').write_bytes(
            b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', struct.pack('>IIBBBBB', 16, 16, 8, 2, 0, 0, 0))
            + png_chunk(b'IDAT', zlib.compress((b'\0' + b'\xff\x40\x20' * 16) * 16))
            + png_chunk(b'IEND', b''))
        x11 = run(['-v', '-e', 'WAYLAND_DEBUG=1'], '''set -e
            test -n "$DISPLAY"
            feh --title MBOX_FEH_FIXTURE fixture.png &
            viewer=$!
            trap 'kill "$viewer" 2>/dev/null || true; wait "$viewer" 2>/dev/null || true' EXIT
            for attempt in $(seq 1 100); do
                kill -0 "$viewer"
                if timeout 2 xwininfo -name MBOX_FEH_FIXTURE > /tmp/feh-window 2>/dev/null; then break; fi
                sleep .1
            done
            grep 'Map State: IsViewable' /tmp/feh-window
            python3 - <<'PY'
import os
port = 6000 + int(os.environ['DISPLAY'].split(':')[1].split('.')[0])
for table in ('/proc/net/tcp', '/proc/net/tcp6'):
    for row in open(table).read().splitlines()[1:]:
        fields = row.split()
        assert not (int(fields[1].split(':')[1], 16) == port and fields[3] == '0A'), 'X11 TCP listener'
print('GUEST_X11_NO_TCP_OK')
PY
            echo FEH_XWAYLAND_OK
        ''', verbose=False)
        assert 'FEH_XWAYLAND_OK' in x11 and 'GUEST_X11_NO_TCP_OK' in x11, x11
        assert 'wl_shm' in x11 and '.attach(' in x11 and '.commit(' in x11, x11
        print('FEH_XWAYLAND_OK: mapped PNG, Wayland SHM commits, guest-only X11 without TCP')
        # Headless Sway has no physical keyboard. Create one only in this private
        # compositor; the guest still cannot bind virtual-keyboard protocols.
        log = open(root/'keyboard.log', 'w')
        keyboard = subprocess.Popen([wtype, '-s', '60000', 'a'], env=env, stdout=log, stderr=log)
        processes.append(('keyboard', keyboard, log))
        time.sleep(.5)
        assert keyboard.poll() is None, 'fixture keyboard injector exited'
        eventdemo = str(Path(shm_app).with_name('weston-eventdemo'))
        # eventdemo can block in fontconfig fstatfs on the guest store, delaying
        # even fatal-signal completion; this is not proof it ignores SIGTERM.
        # Budget that known backend stall separately, without claiming it fixed.
        keys = run(['-v'], f'WAYLAND_DEBUG=1 timeout -k 2 4 {shlex.quote(eventdemo)}; code=$?; test "$code" = 124 -o "$code" = 137',
                   verbose=False, step='keyboard/keymap and teardown', timeout=150)
        assert 'wl_seat' in keys and 'get_keyboard(' in keys and '.keymap(' in keys, keys
        assert 'failed to compile keymap' not in keys.lower(), keys
        assert 'Fontconfig error:' not in keys, keys
        print('\n'.join(line for line in keys.splitlines()
                        if 'get_keyboard(' in line or '.keymap(' in line))
        print('KEYBOARD_KEYMAP_OK: real wl_seat keyboard and keymap FD through waypipe')
        assert "HEADLESS_AGAIN_OK" in run([], 'set -e; test -z "${WAYLAND_DISPLAY:-}"; test -z "${PULSE_SERVER:-}"; test -z "${DISPLAY:-}"; test ! -S /tmp/.X11-unix/X0; echo HEADLESS_AGAIN_OK')

        # Listen on random loopback ports; never bind known production services.
        for _ in range(2):
            service = socket.socket()
            service.bind(("127.0.0.1", 0))
            service.listen(8)
            services.append(service)
        allowed, denied = [s.getsockname()[1] for s in services]
        probe = '''python3 - <<'PY'
import socket, struct
routes = open('/proc/net/route').read().splitlines()[1:]
gateway = next(socket.inet_ntoa(struct.pack('<I', int(r.split()[2], 16)))
               for r in routes if r.split()[1] == '00000000')
socket.create_connection((gateway, ALLOWED), timeout=3).close()
try:
    socket.create_connection((gateway, DENIED), timeout=2)
except OSError:
    print('NARROW_HOST_PORT_OK')
else:
    raise AssertionError('ungranted host port reachable')
PY'''.replace("ALLOWED", str(allowed)).replace("DENIED", str(denied))
        for mode in ("blocked", "open"):
            assert "NARROW_HOST_PORT_OK" in run(["--network", mode, "-p", str(allowed)], probe)
        print("PASS: real mbox boot, Wayland SHM frames, null-sink audio playback, headless reset, narrow host ports")
    finally:
        for _, proc, _ in processes:
            proc.terminate()
        for name, proc, log in processes:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            log.close()
            print(f"--- {name} fixture log ---\n" + (root / f"{name}.log").read_text())
        for service in services:
            service.close()
