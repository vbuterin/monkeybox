#!/usr/bin/env python3
"""Actual wrapper network assembly, without VM/network/host-service access."""
import json
import shlex
import sys
from pathlib import Path
import subprocess

repo = Path(__file__).resolve().parents[1]
wrapper = (repo / "programs/mbox/mbox.sh.in").read_text()
fragment = wrapper.split("# ---- network ", 1)[1].split("# ---- environment ", 1)[0]
fragment = fragment[fragment.index("declare -a NET=()"):]
ASSEMBLE = '\nprintf "NET %s\\nLOOPBACK%s\\n" "${NET[*]}" "$LOOPBACK_PORTS"'


def preamble(allow=(), publish=()):
    """Set the two grant arrays the way the argument loop leaves them."""
    def array(name, values):
        return "%s=(%s)\n" % (name, " ".join(shlex.quote(v) for v in values))
    return array("ALLOW", allow) + array("PUBLISH", publish)


def assemble(mode, allow=(), publish=()):
    """Run the wrapper's own network section with the given grants."""
    out = subprocess.check_output([
        "bash", "-eu", "-c",
        'NET_MODE=$1; shift;\n' + preamble(allow, publish) + fragment + ASSEMBLE,
        "test", mode,
    ], text=True)
    net_line, loopback_line = out.splitlines()
    return net_line[len("NET "):].split(), loopback_line[len("LOOPBACK"):].strip()


# Direction 1, sbox's -p/--allow-port: a host port the guest may reach. It is guest
# egress, so it must never turn into a published (ingress) mapping.
for mode in ("open", "host", "blocked"):
    for ports in ([], ["6000"], ["8012", "06000"]):
        result, loopback = assemble(mode, allow=ports)
        expected = ["--no-net"] if mode == "blocked" else ["--net", "public"]
        for port in ports:
            expected += ["--net-rule", f"allow:egress@host:tcp:{int(port)}"]
        assert result == expected, (mode, ports, result)
        assert not any(flag == "--port" for flag in result), result
        assert loopback.split() == [str(int(port)) for port in ports], loopback

# Direction 2, sbox's --expose-port: a guest port the host may reach. It is published
# on loopback at the same number, deduplicated, and never becomes an egress rule.
for mode in ("open", "host", "blocked"):
    for ports in ([], ["8080"], ["8080", "08080", "9000"]):
        result, loopback = assemble(mode, publish=ports)
        expected = ["--net", "public"]
        if mode == "blocked":
            # Nothing published: the plain blocked default. Publishing is what forces
            # ingress open, and it must not do so when there is nothing to publish.
            expected = (["--net-default-egress", "deny", "--net-default-ingress", "allow"]
                        if ports else ["--no-net"])
        seen = []
        for port in ports:
            number = int(port)
            if number in seen:
                continue
            seen.append(number)
            expected += ["--port", f"127.0.0.1:{number}:{number}"]
        assert result == expected, (mode, ports, result)
        assert not any("allow:egress" in flag for flag in result), result
        # Published ports run host-to-guest, so --host-loopback has nothing to relay.
        assert loopback == "", loopback

# Both directions at once: the blocked default keeps egress denied while ingress stays
# open for the relay, so neither grant may erase the other.
result, loopback = assemble("blocked", allow=["8012"], publish=["8080"])
assert result == [
    "--net-default-egress", "deny", "--net-default-ingress", "allow",
    "--net-rule", "allow:egress@host:tcp:8012",
    "--port", "127.0.0.1:8080:8080",
], result
assert loopback == "8012", loopback

# --no-net would close ingress and silently disable every published port, so the
# blocked default must not be used once something is published.
for mode in ("open", "host", "blocked"):
    result, _ = assemble(mode, publish=["8080"])
    assert "--no-net" not in result, (mode, result)

for port in ("0", "65536", "-1", "6000,allow@host", "foo", "1:2", ""):
    for direction in ("allow", "publish"):
        result = subprocess.run(["bash", "-eu", "-c",
            'NET_MODE=open;\n' + preamble(**{direction: (port,)}) + fragment, "test"],
            capture_output=True)
        assert result.returncode == 2, (direction, port, result)
if '--assembly-only' in sys.argv:
    print('PASS: actual network assembly and invalid ports (no Nix evaluation)')
    sys.exit(0)
# Confirm both options are substituted into the real generated wrapper, not only
# tested in a copied implementation. This evaluates but does not build torch.
path = json.loads(subprocess.check_output([
    "nix", "eval", "--impure", "--json", "--expr", '''let
    f = builtins.getFlake (toString ./.);
    c = (f.lib.mkMonkeybox {
      pkgs = f.legacyPackages.x86_64-linux;
      settings = {
        allowedTCPPorts = [8012];
        publishedTCPPorts = [8080];
      };
    }).config;
    in c.programs.mbox.wrapper.text'''], cwd=repo, text=True))
assert "ALLOW=(8012)" in path, path[path.index("declare -a ALLOW"):][:200]
assert "PUBLISH=(8080)" in path, path[path.index("declare -a ALLOW"):][:200]
assert "@@ALLOW_TCP@@" not in path
assert "@@PUBLISH_TCP@@" not in path
# A published port has to reach a guest service that only listens on the guest's own
# loopback, so the wrapper starts the guest-side forward before the user's command --
# and only when something is published, since the forward binds nothing by itself.
assert "@@PUBLISH_GUEST@@" not in path
forward = [line for line in path.splitlines() if "-mbox-publish-forward\"" in line]
assert len(forward) == 1, forward
line = forward[0].strip()
assert line.startswith('INNER=("/nix/store/') and '"$PUBLISHED"' in line, line
assert 'if [ -n "$PUBLISHED" ]; then' in path
# The old name still evaluates: a host config pinning an older wrapper keeps working.
legacy = json.loads(subprocess.check_output([
    "nix", "eval", "--impure", "--json", "--expr", '''let
    f = builtins.getFlake (toString ./.);
    c = (f.lib.mkMonkeybox {
      pkgs = f.legacyPackages.x86_64-linux;
      settings.exposedTCPPorts = [8012];
    }).config;
    in c.programs.mbox.wrapper.text'''], cwd=repo, text=True))
assert "ALLOW=(8012)" in legacy

# The guest-side loopback relay resolves the host-side gateway from the guest's
# own routing table: the default route when present, otherwise the /30 subnet
# layout (network + 1), and a plain failure when the guest has no IPv4 network
# at all — the sandbox booted while the host had no IPv4 route, and a traceback
# from a route probe is not a diagnosis.
import os
import tempfile
loopback_script = (repo / "programs/mbox/host-loopback.sh.in").read_text()
probe = loopback_script.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]

def probe_gateway(route_table):
    """Run the wrapper's own gateway probe against a fabricated route table."""
    handle = tempfile.NamedTemporaryFile('w', suffix='-route', delete=False)
    handle.write(route_table)
    handle.close()
    try:
        done = subprocess.run([sys.executable, '-', handle.name], input=probe,
                              capture_output=True, text=True)
    finally:
        os.unlink(handle.name)
    return done.returncode, done.stdout.strip(), done.stderr.strip()

# The common case: a default route through the virtual host (little-endian hex).
code, gateway, err = probe_gateway(
    "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
    "eth0\t050010AC\t00000000\t0003\t0\t0\t0\tFFFFFFFC\n"
    "eth0\t00000000\t050010AC\t0003\t0\t0\t0\t00000000\n")
assert (code, gateway) == (0, '172.16.0.5'), (code, gateway, err)
assert err == '', err

# No default route: the /30 connected route still names the gateway (network + 1).
code, gateway, err = probe_gateway(
    "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
    "eth0\t040010AC\t00000000\t0003\t0\t0\t0\tFFFFFFFC\n")
assert (code, gateway) == (0, '172.16.0.5'), (code, gateway, err)

# An on-link (gateway-less) default must not be mistaken for a usable gateway.
code, gateway, err = probe_gateway(
    "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
    "eth0\t00000000\t00000000\t0003\t0\t0\t0\t00000000\n"
    "eth0\t040010AC\t00000000\t0003\t0\t0\t0\tFFFFFFFC\n")
assert (code, gateway) == (0, '172.16.0.5'), (code, gateway, err)

# A wider connected subnet is not the microsandbox /30 layout; no invention.
code, gateway, err = probe_gateway(
    "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
    "eth0\t0000A8C0\t00000000\t0003\t0\t0\t0\t00FFFFFF\n")
assert code != 0 and 'no IPv4 network' in err, (code, gateway, err)

# The host was offline (or still coming up): the guest got no IPv4 at all.
code, gateway, err = probe_gateway(
    "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n")
assert code != 0 and 'no IPv4 network' in err, (code, gateway, err)
assert 'traceback' not in err.lower(), err

print("PASS: actual mbox port assembly in both directions, blocked-mode grants, "
      "invalid port rejection, evaluated default ports, renamed option alias, "
      "loopback relay gateway probe and its plain no-network failure")
