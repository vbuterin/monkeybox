#!/usr/bin/env python3
"""Real guest cache import/build/persistence test; no host build delegation.
Usage: python3 tests/mbox-nix-cache.py WRAPPER BUSYBOX_STORE_PATH
"""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

wrapper, busybox = sys.argv[1:]
with tempfile.TemporaryDirectory(prefix="mbox-cache-test-") as tmp:
    root = Path(tmp)
    for name in ("home", "project", "state"):
        (root / name).mkdir(mode=0o700)
    # Exercise the actual host export/cleanup fragment with a fake nix client.
    # This tests argument restrictions and early failures without touching stores.
    source = (Path(__file__).resolve().parents[1] / "programs/mbox/mbox.sh.in").read_text()
    fragment = source.split('NIX_CACHE_DIR=""', 1)[1].split('# ---- mounts ', 1)[0]
    fragment = 'NIX_CACHE_DIR=""' + fragment
    assert source.count("trap 'cleanup_desktop || true; cleanup_nix_cache' EXIT") == 1
    assert "trap cleanup_desktop EXIT" not in source
    fake = root / "fake-nix"
    (fake / "bin").mkdir(parents=True)
    (fake / "bin/nix").write_text('#!' + shutil.which('bash') + '\nprintf "%s\\n" "$@" > "$ARGS_FILE"\nexit "${EXPORT_EXIT:-0}"\n')
    (fake / "bin/nix").chmod(0o755)
    for export_exit in (0, 17):
        args_file = root / "export-args"
        desktop = root / "desktop-runtime"
        desktop.mkdir()
        script = ('set -euo pipefail\nSHARE_STORE=1; DRY_RUN=0; NIX_CACHE_PATHS=($1)\n'
                  + fragment.replace("@@NIX@@", str(fake))
                  + '\nDESKTOP_RUNTIME=$2; exit 23\n')
        check = subprocess.run(["bash", "-c", script, "fixture", busybox, str(desktop)],
                               env=dict(os.environ, ARGS_FILE=str(args_file), EXPORT_EXIT=str(export_exit)))
        assert check.returncode == (export_exit or 23)
        args = args_file.read_text().splitlines()
        assert args[args.index("--substituters") + 1] == "", args
        cache = args[args.index("--to") + 1].removeprefix("file://").split("?", 1)[0]
        assert not Path(cache).exists(), "early exit leaked cache"
        if export_exit:
            desktop.rmdir()  # this resource was never assigned to the cleanup
        else:
            assert not desktop.exists(), "early exit leaked desktop runtime"
    print("HOST_EXPORT_OFFLINE_AND_EARLY_CLEANUP_OK", flush=True)
    env = dict(os.environ, HOME=str(root / "home"), MBOX_STATE_ROOT=str(root / "state"),
               MSB_HOME=str(root / "state/engine"))
    def run(args, ok=True):
        result = subprocess.run([wrapper, str(root / "project"), *args], env=env,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=120)
        print(result.stdout, flush=True)
        assert (result.returncode == 0) == ok, result.returncode
        return result.stdout

    for bad in ("nixpkgs#hello", "/etc", busybox + "/bin/sh"):
        run(["--dry-run", "-C", bad, "--", "true"], ok=False)
    run(["-C"], ok=False)
    dry = run(["--dry-run", "-C", busybox, "--", "true"])
    assert "/run/mbox-nix-cache:ro" in dry and "--pull never" in dry
    assert "daemon-socket" not in dry
    # Explicit storePath gives the builder a dependency context. A plain string
    # would omit it from the sandbox's input closure, correctly failing exec.
    expr = ('let b = builtins.storePath ' + json.dumps(busybox) + '; in derivation { '
            'name="mbox-independent-fixture"; system="x86_64-linux"; '
            'builder="${b}/bin/sh"; args=[ "-c" "echo GUEST_BUILD_OK > $out" ]; }')
    script = '''set -e
        test ! -e /nix/var/nix/daemon-socket/socket
        if touch /run/mbox-nix-cache/guest-write 2>/dev/null; then exit 1; fi
        test -f "$NIX_REMOTE/nix/var/nix/db/db.sqlite"
        out=$(nix build --impure --offline --no-link --print-out-paths --expr EXPR)
        grep -q GUEST_BUILD_OK "$NIX_REMOTE$out"
        printf '%s' "$out" > "$HOME/guest-output"
        echo GUEST_BUILD_OK
    '''.replace("EXPR", shlex.quote(expr))
    assert "GUEST_BUILD_OK" in run(["-C", busybox, "--", "bash", "-c", script])
    script = '''set -e
        out=$(cat "$HOME/guest-output")
        grep -q GUEST_BUILD_OK "$HOME/.local/share/mbox-nix/root$out"
        test ! -e "$out"
        test ! -e /run/mbox-nix-cache
        test -z "${NIX_REMOTE:-}"
        echo CACHE_PERSIST_NO_HOST_OUTPUT_OK
    '''
    assert "CACHE_PERSIST_NO_HOST_OUTPUT_OK" in run(["--", "bash", "-c", script])
    print("PASS: selected read-only cache, isolated guest Nix build, persistence, default has no cache")
