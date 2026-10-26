# Monkeybox

Explicit-grant Linux microVM environments built on **microsandbox + libkrun**.
Run development tools and software-rendered desktop applications with a private
home, a read-only Nix store, and explicit file/network/desktop grants.

Experimental, initially **x86_64 Linux with Nix and KVM**. NixOS is not required.
This is a standalone flake: no parent checkout, Spaces, host configuration,
credentials, application accounts or personal shell hooks are imported.

## Install / run

Install Nix with flakes enabled, and ensure your account can read/write `/dev/kvm`.
Use your distribution's normal KVM group/udev setup; do not run Monkeybox as root
or make KVM world-writable. Internet access is needed for the initial Nix build
unless you already have its dependencies. The built-in guest image needs no
registry download. Docker is **not** required to run or build this image.

From this checkout:

```sh
nix run . -- --help
nix build .
./result/bin/mbox -- bash -c 'id; ls'
nix profile install .
```

After publication, replace `.` with `github:vbuterin/monkeybox`.
`mbox` is the primary command; `monkeybox` is an alias in the installed package.
Existing `MBOX_*` environment variables and `~/.local/state/mbox` state paths are
retained deliberately. Do not run the old and new wrappers concurrently against
the same project/state; use `MBOX_STATE_ROOT` to test separately.

The default package contains a small CLI userland. The **desktop preset** adds
Firefox, Evince, feh and MPlayer, including a narrow Firefox font-directory mount:

```sh
nix run .#desktop -- -vic -- firefox
nix run .#desktop -- -vf ~/Downloads/*.pdf -- evince ~/Downloads/*.pdf
nix run .#desktop -- -af ~/Music -- mplayer -shuffle ~/Music/*
```

Visual mode requires an existing Wayland session. Audio requires a local Pulse
socket (including PipeWire-Pulse). Combined clipboard transfer requires Sway;
shortcut installation is manual and opt-in. Ordinary filtered visual mode lacks
a protocol GTK needs: use `-vc` for Firefox/GTK with the current Sway adapter.
Software rendering only; no host GPU, X socket, session bus or native PipeWire
socket is shared. Hardware camera support is experimental; see [camera](docs/mbox-camera.md).

## Grants

Default networking is blocked. The current project is shared read-write at its
canonical path; launching in your home instead uses the private guest home.
Choose a small project directory rather than a directory full of sensitive files.

| Option | Grant |
|---|---|
| `-f PATH...` | Selected existing files/directories, same-path read-write |
| `-i` | Public internet egress |
| `-p PORT` | Guest access to one host TCP service (sbox's `--allow-port`) |
| `--expose-port PORT` | Host access to one guest TCP service, on the same loopback port |
| `-v` | Software Wayland window/input bridge |
| `-a` | Audio output only (guest may monitor its own playback) |
| `-r` | Microphone input, independent of `-a` |
| `-c` | Guest-local desktop plus explicit Sway clipboard transfers; requires `-v` |
| `-l` | Selected host camera stream for invocation lifetime |
| `-C STORE_PATH` | Export realized closure to a temporary read-only offline Nix cache |

Short options cluster; only the last letter may take arguments. Values are
separate arguments. `-af ~/Music -im 15000` is valid; `-afm` is not. Memory numbers
without suffixes are MiB. `-f` consumes non-option arguments until the next option
or `--`; put `--` before the application. Shells expand globs, not Monkeybox.
Missing values error. Application arguments after `--` are never reparsed.

The two port options point opposite ways, as in sbox. `-p 8012` lets the guest reach
the host's 8012 (egress). `--expose-port 8080` opens host `127.0.0.1:8080` and forwards
it to the guest's 8080 (ingress). Publishing reopens only the ingress direction, so
a blocked sandbox stays offline in the outbound direction. A published port lives
exactly as long as the invocation that opened it, because each invocation boots its
own VM.

A published port reaches the guest's *network* address, not the guest's loopback.
The service inside has to listen on `0.0.0.0` (or the guest's own address); a dev
server started on `localhost`/`127.0.0.1` is reachable from inside the guest and
invisible from the host, which shows up as a reset connection (`Empty reply from
server`, `ERR_EMPTY_RESPONSE`) rather than a timeout. Check it in the guest with
`ss -tln | grep PORT`: `0.0.0.0:PORT` or `*:PORT` works, `127.0.0.1:PORT` does not.

## Configure without NixOS

Create your own flake using our builder:

```nix
{
  inputs.monkeybox.url = "github:vbuterin/monkeybox";
  inputs.nixpkgs.follows = "monkeybox/nixpkgs";
  outputs = { self, monkeybox, nixpkgs }: let
    pkgs = import nixpkgs { system = "x86_64-linux"; };
    box = monkeybox.lib.mkMonkeybox {
      inherit pkgs;
      settings = {
        packages = [ pkgs.python3 pkgs.git ];
        memory = "4096M";
        cpus = 2;
        # Optional additional explicitly shared/persistent resources:
        # allowedTCPPorts = [ 8012 ];      # guest may reach host:8012
        # publishedTCPPorts = [ 8080 ];    # host may reach guest:8080
        # bindReadOnly."/path/to/reference-data" = {};
        # shellHook = "export MY_VARIABLE=value";
      };
    };
  in { packages.x86_64-linux.default = box.package; };
}
```

`box.wrapper`, `box.clipboardPackage`, `box.config` and `box.options` are also
available. Options remain under `programs.mbox`. This uses `lib.evalModules`, not
`nixosSystem`. Host profile sharing, shell-file sharing, binds and additional
packages are off/empty by default. Private home state persists across launches;
no automatic venv setup, Pi credentials or host home migration is performed.

## Optional NixOS adapter

Import `monkeybox.nixosModules.default`, enable `programs.mbox`, and select packages
through the same options. Add only intended users to the `kvm` group explicitly:

```nix
programs.mbox.enable = true;
users.users.YOUR_USERNAME.extraGroups = [ "kvm" ];
```

Creating the group alone grants nobody access. After activation, log out and
back in (including restarting the graphical session); existing sessions retain
old groups. Check `id -nG` and `test -r /dev/kvm && test -w /dev/kvm`.
The adapter
installs the wrapper/broker and KVM group permissions; it does not install Sway
bindings, Noctalia patches or host-service grants.

## Boundaries and limitations

Read [security](SECURITY.md), [clipboard integration](docs/mbox-clipboard.md),
[desktop](docs/mbox-desktop.md), [Nix cache](docs/mbox-nix-cache.md), and
[testing](tests/README.md). This is not a universal desktop sandbox or a claim of
Qubes parity. Backend statfs accounting/performance and broader vsock lifecycle
questions remain unresolved; selected font mitigations do not repair them.

Noctalia UI modifications and personal host configuration intentionally remain
in the original NixOS repository. The broker offers payload-free `watch` activity
and explicit `preview` reads for optional UI integrations; it does not observe
all application-local Copy/Cut/Paste actions.

## Provenance / publication

Extracted from `vbuterin/nixos-example` commit `937f215` with portable packaging
and defaults added here. See [provenance](PROVENANCE.md). Initial validation was
on a NixOS Linux host using standalone package evaluation and private fixtures;
a non-NixOS distribution has **not yet been runtime-tested**.

Licensing of newly extracted first-party code must be selected by the owner
before advertising this as an open-source release; see `LICENSE-NOTICE.md`.
