#!/usr/bin/env python3
"""Actual wrapper network assembly, without VM/network/host-service access."""
import json
import sys
from pathlib import Path
import subprocess

repo = Path(__file__).resolve().parents[1]
wrapper = (repo / "programs/mbox/mbox.sh.in").read_text()
fragment = wrapper.split("# ---- network ", 1)[1].split("# ---- environment ", 1)[0]
fragment = fragment[fragment.index("declare -a NET=()"):]
for mode in ("open", "host", "blocked"):
    for ports in ([], ["6000"], ["8012", "06000"]):
        result = subprocess.check_output([
            "bash", "-eu", "-c", 'NET_MODE=$1; shift; EXPOSE=("$@");\n' + fragment
            + '\nprintf "%s\\n" "${NET[@]}"', "test", mode, *ports,
        ], text=True).splitlines()
        expected = ["--no-net"] if mode == "blocked" else ["--net", "public"]
        for port in ports:
            expected += ["--net-rule", f"allow:egress@host:tcp:{int(port)}"]
        assert result == expected, result
for port in ("0", "65536", "-1", "6000,allow@host", "foo", "1:2", ""):
    result = subprocess.run(["bash", "-eu", "-c",
        'NET_MODE=open; EXPOSE=("$1");\n' + fragment, "test", port], capture_output=True)
    assert result.returncode == 2, (port, result)
if '--assembly-only' in sys.argv:
    print('PASS: actual network assembly and invalid ports (no Nix evaluation)')
    sys.exit(0)
# Confirm the option is substituted into the real generated wrapper, not only
# tested in a copied implementation. This evaluates but does not build torch.
path = json.loads(subprocess.check_output([
    "nix", "eval", "--impure", "--json", "--expr", '''let
    f = builtins.getFlake (toString ./.);
    c = (f.lib.mkMonkeybox {
      pkgs = f.legacyPackages.x86_64-linux;
      settings.exposedTCPPorts = [8012];
    }).config;
    in c.programs.mbox.wrapper.text'''], cwd=repo, text=True))
assert "EXPOSE=(8012)" in path
assert "@@EXPOSE_TCP@@" not in path
print("PASS: actual mbox port assembly, blocked-mode grants, invalid port rejection, evaluated default ports")
